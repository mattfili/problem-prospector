#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["numpy", "scikit-learn", "scipy"]
# ///
"""Tests for clustering at 3k-10k items: the numpy path must change speed, not answers.

cluster.py has two paths. The pure-Python one is what every past run used; the
numpy one (cluster_scale.py) exists because the pure one was killed at 1,800 s
on 6,776 items and died of memory on 9,133. A faster path that clusters
differently would silently change every cut_basis a reader compares across
runs, so the core assertion here is equivalence: same partition, same cut to
1e-12, same summary distances, same canonical phrase per cluster.

Also covered: the doubled-percentile shortcut against the square-matrix
original, the one-tree-many-cuts labelling against sklearn itself, the
per-run embedding cache (hit, partial, model mismatch, checkpoint survives a
crash), the memory guard refusing before embedding, and the fusion advisory's
suggestion text.

The 8k-item timing run is opt-in (it takes a while and allocates ~1 GB):

    uv run --quiet tests/test_cluster_scale.py
    PROSPECTOR_SLOW_TESTS=1 uv run --quiet tests/test_cluster_scale.py
"""

from __future__ import annotations

import json
import os
import resource
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import cluster  # noqa: E402
import cluster_scale as scale  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "evidence-sample.jsonl"


def clustered_unit_vectors(n: int, dims: int, centers: int, spread: float, seed: int) -> np.ndarray:
    """n L2-normalised vectors scattered around `centers` random directions."""
    rng = np.random.default_rng(seed)
    hubs = rng.normal(size=(centers, dims))
    picks = rng.integers(0, centers, size=n)
    vecs = hubs[picks] + spread * rng.normal(size=(n, dims))
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def partition(labels: list[int]) -> set[frozenset[int]]:
    """Labels as a set of member-index groups, so label numbering is irrelevant."""
    groups: dict[int, set[int]] = {}
    for idx, lab in enumerate(labels):
        groups.setdefault(lab, set()).add(idx)
    return {frozenset(g) for g in groups.values()}


class PathEquivalence(unittest.TestCase):
    """The numpy path must reproduce the pure path's clustering exactly."""

    def assert_equivalent(self, vectors, pct: float) -> None:
        as_lists = [list(map(float, v)) for v in vectors]
        slow = cluster.cluster_vectors(as_lists, pct=pct, dense=False)
        fast = cluster.cluster_vectors(np.asarray(vectors), pct=pct, dense=True)
        self.assertEqual(partition(slow["labels"]), partition(fast["labels"]))
        self.assertAlmostEqual(slow["cut"], fast["cut"], delta=1e-12)
        self.assertEqual(slow["cut_basis"], fast["cut_basis"])
        self.assertEqual(slow["algorithm"], fast["algorithm"])
        self.assertEqual(slow["median_pairwise_distance"], fast["median_pairwise_distance"])
        self.assertEqual(slow["p75_pairwise_distance"], fast["p75_pairwise_distance"])

    def test_200_synthetic_items_at_several_percentiles(self) -> None:
        vectors = clustered_unit_vectors(200, 64, centers=12, spread=0.6, seed=7)
        for pct in (5.0, 10.0, 25.0, 35.0):
            with self.subTest(pct=pct):
                self.assert_equivalent(vectors, pct)

    def test_fixture_evidence_under_the_offline_embedder(self) -> None:
        loaded = cluster.load_evidence([FIXTURE])
        texts = [r["_embed_text"] for r in loaded["records"]]
        self.assert_equivalent(cluster.embed(texts, "offline"), 35.0)

    def test_fixed_threshold_is_equivalent_too(self) -> None:
        vectors = clustered_unit_vectors(120, 32, centers=6, spread=0.5, seed=3)
        slow = cluster.cluster_vectors(vectors.tolist(), distance_threshold=0.4, dense=False)
        fast = cluster.cluster_vectors(vectors, distance_threshold=0.4, dense=True)
        self.assertEqual(partition(slow["labels"]), partition(fast["labels"]))
        self.assertEqual(slow["cut_basis"], fast["cut_basis"])

    def test_built_clusters_match_including_the_canonical_medoid(self) -> None:
        loaded = cluster.load_evidence([FIXTURE])
        records = loaded["records"]
        vectors = cluster.embed([r["_embed_text"] for r in records], "offline")
        labels = cluster.cluster_vectors(vectors, dense=False)["labels"]
        slow, slow_un = cluster.build_clusters(records, vectors, labels)
        fast, fast_un = cluster.build_clusters(records, scale.as_matrix(vectors), labels)
        self.assertEqual(slow, fast)
        self.assertEqual(slow_un, fast_un)


class PercentileShortcut(unittest.TestCase):
    """offdiag_percentile over the sorted upper triangle == adaptive_cut over the square."""

    def test_matches_the_square_matrix_original(self) -> None:
        vectors = clustered_unit_vectors(41, 16, centers=4, spread=0.8, seed=11).tolist()
        square = cluster.pairwise_matrix(vectors)
        ordered = np.sort(scale.condensed_distances(scale.as_matrix(vectors)))
        for pct in (0.0, 5.0, 12.5, 35.0, 50.0, 99.0, 100.0):
            with self.subTest(pct=pct):
                self.assertAlmostEqual(
                    cluster.adaptive_cut(square, pct),
                    max(scale.offdiag_percentile(ordered, pct), 1e-6),
                    delta=1e-12,
                )

    def test_out_of_range_still_raises(self) -> None:
        with self.assertRaises(ValueError):
            cluster.percentile([0.1, 0.2], 150.0)
        with self.assertRaises(ValueError):
            cluster.percentile([0.1, 0.2], -5.0)


class TreeCut(unittest.TestCase):
    """labels_at_cut on one scipy tree == sklearn AgglomerativeClustering per cut."""

    def test_matches_sklearn_at_many_thresholds(self) -> None:
        from sklearn.cluster import AgglomerativeClustering

        vectors = clustered_unit_vectors(150, 24, centers=8, spread=0.7, seed=5)
        condensed = scale.condensed_distances(vectors)
        square = scale.square_from_condensed(condensed)
        tree = scale.average_linkage(condensed)
        # Include exact pair distances as cuts: equality is where the two
        # merge rules (< versus <=) would disagree if they did.
        cuts = list(np.quantile(condensed, [0.02, 0.1, 0.3, 0.6])) + list(condensed[:3])
        for cut in cuts:
            with self.subTest(cut=float(cut)):
                ref = AgglomerativeClustering(
                    n_clusters=None, distance_threshold=float(cut),
                    metric="precomputed", linkage="average",
                ).fit_predict(square)
                self.assertEqual(
                    partition(ref.tolist()), partition(scale.labels_at_cut(tree, 150, float(cut)))
                )

    def test_probe_shares_never_grow_as_the_percentile_drops(self) -> None:
        vectors = clustered_unit_vectors(300, 32, centers=10, spread=0.9, seed=2)
        result = cluster.cluster_vectors(vectors, pct=35.0, dense=True)
        shares = [row["largest_share"] for row in result["percentile_probe"]]
        self.assertEqual([row["percentile"] for row in result["percentile_probe"]],
                         list(cluster.PROBE_PERCENTILES))
        self.assertEqual(shares, sorted(shares, reverse=True))


class EmbeddingCache(unittest.TestCase):
    """Re-clustering must not re-embed; a crash must not lose finished vectors."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.calls: list[int] = []
        self.real = cluster._fastembed_batch_embedder

        def fake_embedder():
            def run(texts):
                self.calls.append(len(texts))
                return [np.asarray(cluster._embed_offline_one(t), dtype=np.float32) for t in texts]
            return run

        cluster._fastembed_batch_embedder = fake_embedder

    def tearDown(self) -> None:
        cluster._fastembed_batch_embedder = self.real
        self.tmp.cleanup()

    def test_second_call_is_a_hit_and_embeds_nothing(self) -> None:
        texts = [f"permit status is invisible {i}" for i in range(30)]
        first, status1 = cluster.embed_for_run(texts, "fastembed", self.tmp.name)
        self.assertEqual(status1, "miss")
        self.assertEqual(sum(self.calls), 30)
        second, status2 = cluster.embed_for_run(texts, "fastembed", self.tmp.name)
        self.assertEqual(status2, "hit")
        self.assertEqual(sum(self.calls), 30, "a cache hit must not call the embedder")
        np.testing.assert_array_equal(first, second)

    def test_new_texts_are_the_only_ones_embedded(self) -> None:
        texts = [f"invoice chasing takes hours {i}" for i in range(20)]
        cluster.embed_for_run(texts, "fastembed", self.tmp.name)
        self.calls.clear()
        _, status = cluster.embed_for_run(texts + ["a brand new complaint"], "fastembed", self.tmp.name)
        self.assertEqual(self.calls, [1])
        self.assertEqual(status, "partial: 20 of 21 reused")

    def test_duplicate_texts_are_embedded_once(self) -> None:
        cluster.embed_for_run(["same", "same", "other"], "fastembed", self.tmp.name)
        self.assertEqual(sum(self.calls), 2)

    def test_a_cache_from_another_model_is_ignored_not_mixed(self) -> None:
        path = cluster.cache_file_for(self.tmp.name, "fastembed")
        stale = scale.EmbeddingCache(path, "some-other-model")
        stale.add([scale.text_key("x")], [np.zeros(512, dtype=np.float32)])
        stale.save()
        _, status = cluster.embed_for_run(["x"], "fastembed", self.tmp.name)
        self.assertEqual(status, "miss")

    def test_offline_and_fastembed_caches_never_share_a_file(self) -> None:
        self.assertNotEqual(cluster.cache_file_for("d", "offline"),
                            cluster.cache_file_for("d", "fastembed"))

    def test_a_crash_keeps_every_checkpointed_vector(self) -> None:
        original = cluster.EMBED_CHECKPOINT
        cluster.EMBED_CHECKPOINT = 10

        def dies_on_second_batch():
            seen = []

            def run(texts):
                seen.append(len(texts))
                if len(seen) == 2:
                    raise RuntimeError("killed mid-embedding")
                return [np.asarray(cluster._embed_offline_one(t), dtype=np.float32) for t in texts]
            return run

        cluster._fastembed_batch_embedder = dies_on_second_batch
        texts = [f"complaint number {i}" for i in range(25)]
        try:
            with self.assertRaises(RuntimeError):
                cluster.embed_for_run(texts, "fastembed", self.tmp.name)
        finally:
            cluster.EMBED_CHECKPOINT = original
        survived = scale.EmbeddingCache(cluster.cache_file_for(self.tmp.name, "fastembed"),
                                        cluster.FASTEMBED_MODEL_NAME)
        self.assertEqual(survived.loaded, 10)


class MemoryGuard(unittest.TestCase):
    """Too many items is refused up front, loudly — never a silent death."""

    def tearDown(self) -> None:
        os.environ.pop("PROSPECTOR_CLUSTER_MAX_ITEMS", None)

    def test_refuses_before_embedding_with_guidance(self) -> None:
        os.environ["PROSPECTOR_CLUSTER_MAX_ITEMS"] = "10"
        real = cluster.embed_for_run

        def must_not_embed(*_args, **_kwargs):
            raise AssertionError("embedding started on a pool the guard should refuse")

        cluster.embed_for_run = must_not_embed
        try:
            payload, code = cluster.run([str(FIXTURE)], backend="offline")
        finally:
            cluster.embed_for_run = real
        self.assertEqual(code, 1)
        self.assertEqual(payload["clusters"], [])
        detail = next(h["detail"] for h in payload["source_health"] if h["source"] == "clustering")
        self.assertIn("GB", detail)
        self.assertIn("PROSPECTOR_CLUSTER_MAX_ITEMS", detail)
        self.assertEqual(len(payload["unclustered_ids"]), payload["evidence_count"])

    def test_default_ceiling_admits_a_real_run(self) -> None:
        self.assertIsNone(cluster.pool_too_large(9133))


class FusionAdvisory(unittest.TestCase):
    """The size advisory fires at 15% on big pools and names a percentile."""

    @staticmethod
    def pool(n: int, biggest: int) -> tuple[list[dict], list[dict]]:
        records = [{"_id": f"r{i}"} for i in range(n)]
        clusters = [{"cluster_id": "c01", "member_count": biggest,
                     "member_ids": [f"r{i}" for i in range(biggest)]}]
        return clusters, records

    def test_big_pool_over_fifteen_percent_suggests_the_first_fitting_percentile(self) -> None:
        clusters, records = self.pool(6776, 3132)
        probe = [
            {"percentile": 25.0, "cut": 0.3, "clusters": 400, "largest_share": 0.46},
            {"percentile": 15.0, "cut": 0.28, "clusters": 600, "largest_share": 0.3},
            {"percentile": 10.0, "cut": 0.26, "clusters": 800, "largest_share": 0.23},
            {"percentile": 5.0, "cut": 0.22, "clusters": 1050, "largest_share": 0.12},
        ]
        text = cluster.fusion_advisory(clusters, records, [], None, probe=probe, pct=25.0)
        self.assertIn("3132 of 6776 items (46%)", text)
        self.assertIn("Re-cluster at percentile 5", text)
        self.assertIn("12%", text)

    def test_says_so_when_no_probed_percentile_fits(self) -> None:
        clusters, records = self.pool(2000, 900)
        probe = [{"percentile": 5.0, "cut": 0.2, "clusters": 50, "largest_share": 0.4}]
        text = cluster.fusion_advisory(clusters, records, [], None, probe=probe, pct=10.0)
        self.assertIn("genuinely dominant", text)

    def test_small_pool_keeps_the_looser_threshold(self) -> None:
        clusters, records = self.pool(300, 60)  # 20%: fine below 1,000 items
        self.assertIsNone(cluster.fusion_advisory(clusters, records, [], None))


@unittest.skipUnless(os.environ.get("PROSPECTOR_SLOW_TESTS"), "set PROSPECTOR_SLOW_TESTS=1")
class EightThousandItems(unittest.TestCase):
    """An 8k x 384 pool clusters in bounded time and memory on the scale path."""

    def test_completes_under_budget(self) -> None:
        vectors = clustered_unit_vectors(8000, 384, centers=300, spread=0.8, seed=1)
        started = time.monotonic()
        result = cluster.cluster_vectors(vectors, pct=5.0)
        elapsed = time.monotonic() - started
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_gb = peak / (1e9 if sys.platform == "darwin" else 1e6)
        print(json.dumps({"items": 8000, "seconds": round(elapsed, 1),
                          "peak_rss_gb": round(peak_gb, 2),
                          "groups": len(set(result["labels"]))}), file=sys.stderr)
        self.assertLess(elapsed, 300)
        self.assertLess(peak_gb, 3.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
