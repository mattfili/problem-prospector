#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["fastembed", "scikit-learn", "numpy", "scipy"]
# ///
"""
cluster.py — the dedup + clustering engine. CONTRACTS §2 in, CONTRACTS §3 out.

WHY THIS EXISTS
    Scouts capture raw posts and never interpret them, so the evidence pile is
    400 phrasings of maybe 9 real pains. Every downstream stage (distiller,
    economist, skeptic, historian, wedgesmith) must reason over the PAIN, not
    over whichever phrasing happened to score highest on Reddit. This script is
    the hinge: after it runs, the cluster is the unit of analysis, never the raw
    post (§3.2). One pain restated 400 times is one cluster of weight 400 — and
    `distinct_authors` / `distinct_communities` are what keep that weight
    honest when the 400 are one person in one subreddit.

    It also does the boring-but-load-bearing job of deduplication: two scouts
    capturing the same permalink must not double-count engagement.

THE ONE HARD-WON LESSON (inherited from the Armsreach divergence engine)
    A fixed cosine-distance threshold does NOT transfer across embedding
    models. Short strings under bge-small sit in a narrow band — distinct items
    measured 0.21-0.53 apart, median ~0.37 — so a hardcoded 0.45 collapses the
    entire pool into one cluster, and a hardcoded 0.25 shatters it into
    singletons. So the cut is derived from the pool's OWN pairwise-distance
    distribution at a percentile (default 35, i.e. "the closest third of all
    pairs are restatements of each other"). The basis is always reported as
    `cut_basis` so a reader can see which cut produced the shape they are
    looking at. Never replace this with a constant.

    Evidence pools are TIGHTER than the divergence engine's candidate pools,
    because every item was captured against one inspiration:
    tests/fixtures/evidence-sample.jsonl spans only 0.118-0.494 under bge
    (p35 = 0.362, p75 = 0.405). In a band that narrow p35 can fuse
    adjacent-but-distinct pains, so `fusion_advisory()` warns (on stderr and
    in the payload) when a cluster is wider inside than the pool's typical
    unrelated pair, or when one cluster holds over 15% of a pool of 1,000+
    items (35% below that) — the
    reader then re-runs at a lower `--percentile` (10-25 for a few hundred
    items; 5-10 at several thousand, where average linkage chains harder).
    Reporting the tension is the design; hiding it behind a hardcoded
    constant is what this whole approach rejects.

KEY-FREE BY CONSTRUCTION
    The Armsreach original shipped `openai` and `composio` embedding backends.
    They are deliberately deleted here — this plugin reads no API key, ever, so
    a backend that needs one is the wrong backend. Two remain: `fastembed`
    (local ONNX bge-small, semantic) and `offline` (dependency-free lexical
    proxy) for boxes where fastembed cannot install. The only network touch in
    this whole script is fastembed's one-time model download into its own
    cache; there is no HTTP client here and nothing to rate-limit.

SCALE
    Real frames now produce 6-9k evidence items, not the few hundred this was
    first written for. Whenever numpy imports, clustering runs through
    `cluster_scale.py` — the same arithmetic on arrays, held to the same labels
    and cut by tests/test_cluster_scale.py — and the pure-Python code here is
    the fallback for a box without numpy. Embeddings can be cached per run
    (`--embedding-cache-dir`), because re-clustering at a new `--percentile`
    is the documented workflow and re-embedding 6,800 items took ~45 minutes.
    Pools past `PROSPECTOR_CLUSTER_MAX_ITEMS` (default 15,000) are refused up
    front with the memory estimate, rather than dying silently mid-run.

REUSED DOWNSTREAM — import, do not copy
    `embed()`, `cosine_distance()`, `centroid()` and `centroid_distance()` are
    the shared embedding space for the rest of the pipeline: `wedge-voltage`
    computes pain_distance / incumbent_distance against cluster centroids
    (§5) and `/diligence` reuses it for novelty. Distances are only comparable
    within one backend, so a consumer must embed with the same `backend` that
    produced the clusters.json it is reading (recorded there as `backend` /
    `embedding_model`).

Usage:
    uv run --quiet scripts/cluster.py runs/<slug>/evidence/ \\
        --out runs/<slug>/clusters.json

    uv run --quiet scripts/cluster.py a.jsonl b.jsonl --backend offline
    uv run --quiet scripts/cluster.py evidence/ --percentile 25 --min-clusters 8
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import math
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Sequence

import cluster_scale as scale

# bge-small truncates its input at 512 tokens (~2000 characters), so text past
# that boundary cannot influence the vector no matter what we pass. Truncating
# at the same place is therefore free for the fastembed path, and for the
# lexical path it is a deliberate choice: the complaint is stated in the title
# and opening paragraphs, while long tails are logs, stack traces and thread
# quoting that add lexical noise and pull unrelated posts together.
EMBED_CHAR_LIMIT = 2000

OFFLINE_DIMS = 512
# Version the lexical embedder's name: its vectors are not comparable with
# bge's, and a consumer needs to be able to tell which space it is in.
OFFLINE_MODEL_NAME = "offline-lexical-hashed-512d-v1"
FASTEMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"


def log(msg: str) -> None:
    """Diagnostics go to stderr; stdout stays parseable JSON."""
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Embedding backends
# --------------------------------------------------------------------------

def _stable_hash(token: str) -> int:
    """Process-independent hash — Python's built-in hash() is salted per run,
    which would make the offline vectors non-reproducible across processes."""
    return int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)


def _embed_offline_one(text: str) -> list[float]:
    """Dependency-free lexical embedding: hashed word tokens + character
    trigrams, L2-normalized so the dot product is the cosine similarity.

    This is a lexical PROXY for semantic distance, not a substitute: it catches
    restatements that share vocabulary ("permit status is invisible" /
    "no visibility into permit status") and misses synonym-only paraphrases.
    It exists so the script still produces clusters where fastembed cannot
    install, and the output records which backend ran so the reader can
    discount accordingly.
    """
    vec = [0.0] * OFFLINE_DIMS
    lowered = text.lower()
    for word in re.findall(r"[a-z0-9]+", lowered):
        # Words carry more signal than trigrams; weight them higher.
        vec[_stable_hash("w:" + word) % OFFLINE_DIMS] += 1.6
    trigrams = ["".join(g) for g in zip(lowered, lowered[1:], lowered[2:])]
    for tri in (trigrams or [lowered]):
        vec[_stable_hash("t:" + tri) % OFFLINE_DIMS] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _fastembed_batch_embedder() -> Callable[[Sequence[str]], list]:
    """Build the fastembed model once; return a function embedding one batch.

    bge-small-en-v1.5 is 384-dim, ~130MB, cached after the first download, and
    fastembed already L2-normalizes its output, so the vectors are ready for
    cosine work as returned. Each returned vector is a float32 numpy array.
    """
    # Lazy import so `--backend offline` works with the package absent.
    from fastembed import TextEmbedding

    # fastembed's own default (256) batches enough text through ONNX at once
    # to OOM on a ~1000-record corpus on a fresh machine. 32 held at 9,133
    # items on an 8 GB box (observed 2026-10-06); batch memory does not grow
    # with corpus size. Override for a bigger or smaller box.
    batch_size = int(os.environ.get("PROSPECTOR_EMBED_BATCH_SIZE", "32"))
    # ONNX otherwise picks its own thread count; observed using 2 of the
    # available cores, which made a 6,800-item corpus take ~45 minutes.
    model = TextEmbedding(model_name=FASTEMBED_MODEL_NAME, threads=os.cpu_count())
    return lambda texts: list(model.embed(list(texts), batch_size=batch_size))


def _embed_fastembed(texts: Sequence[str]) -> list[list[float]]:
    """Local ONNX embeddings via fastembed. No key, no PyTorch, no server."""
    return [vec.tolist() for vec in _fastembed_batch_embedder()(texts)]


def embed(texts: Sequence[str], backend: str = "fastembed") -> list[list[float]]:
    """Embed `texts` with `backend` ("fastembed" | "offline").

    The public entry point for the whole pipeline — wedge-voltage and
    /diligence call this so their distances live in the same space as
    clusters.json. Raises on an unknown backend; propagates fastembed's own
    errors (missing package, failed model download) so the caller can decide
    whether to degrade. `cluster()` below degrades; a caller that needs the
    semantic space may prefer to fail.
    """
    if backend == "offline":
        return [_embed_offline_one(t) for t in texts]
    if backend == "fastembed":
        return _embed_fastembed(texts)
    raise ValueError(f"unknown backend '{backend}'. choose from: fastembed, offline")


def model_name_for(backend: str) -> str:
    """The recorded model name for a backend — what clusters.json reports."""
    return FASTEMBED_MODEL_NAME if backend == "fastembed" else OFFLINE_MODEL_NAME


#: Vectors embedded between cache checkpoints. A run killed mid-embedding (a
#: timeout, a closed laptop) keeps everything up to the last checkpoint.
EMBED_CHECKPOINT = 1024


def cache_file_for(cache_dir: str | Path, backend: str) -> Path:
    """One cache file per model, so a fastembed failure that falls back to the
    offline backend can never overwrite the fastembed vectors already paid for."""
    slug = re.sub(r"[^a-z0-9]+", "-", model_name_for(backend).lower()).strip("-")
    return Path(cache_dir).expanduser() / f"embeddings-{slug}.npz"


def _batch_embedder(backend: str) -> Callable[[Sequence[str]], list]:
    """A function embedding one batch of texts with `backend`."""
    if backend == "fastembed":
        return _fastembed_batch_embedder()
    if backend == "offline":
        return lambda texts: [_embed_offline_one(t) for t in texts]
    raise ValueError(f"unknown backend '{backend}'. choose from: fastembed, offline")


def embed_for_run(
    texts: Sequence[str], backend: str, cache_dir: str | Path | None
) -> tuple[Any, str]:
    """Embed `texts` for clustering, reusing a per-run cache when given one.

    Returns (vectors, cache_status). Vectors are a float64 numpy array when
    numpy imports (so the scale path can use them directly) and lists of
    floats otherwise. cache_status is `hit` (nothing embedded), `miss`
    (nothing reused), `partial: R of N reused`, or `off` with the reason.
    New vectors are checkpointed every EMBED_CHECKPOINT items.
    """
    np = scale.numpy_or_none()
    if np is None:
        return embed(texts, backend), "off: numpy unavailable"
    if cache_dir is None:
        return scale.as_matrix(embed(texts, backend)), "off: no cache directory given"

    cache = scale.EmbeddingCache(cache_file_for(cache_dir, backend), model_name_for(backend))
    if cache.ignored_reason:
        log(f"[cluster] embedding cache ignored: {cache.ignored_reason}")
    keys = [scale.text_key(t) for t in texts]
    # Identical texts share one vector; embed each distinct missing text once.
    first_missing: dict[bytes, int] = {}
    for i, key in enumerate(keys):
        if cache.get(key) is None:
            first_missing.setdefault(key, i)
    todo = list(first_missing.items())
    reused = len(keys) - sum(1 for k in keys if k in first_missing)

    if todo:
        embed_batch = _batch_embedder(backend)
        for start in range(0, len(todo), EMBED_CHECKPOINT):
            chunk = todo[start:start + EMBED_CHECKPOINT]
            vectors = embed_batch([texts[i] for _, i in chunk])
            cache.add([k for k, _ in chunk], [np.asarray(v) for v in vectors])
            cache.save()
            log(f"[cluster] embedded {min(start + EMBED_CHECKPOINT, len(todo))}/{len(todo)} "
                f"new texts; checkpointed to {cache.path}")

    matrix = np.stack([cache.get(k) for k in keys]).astype(np.float64)
    if not todo:
        status = "hit"
    elif reused == 0:
        status = "miss"
    else:
        status = f"partial: {reused} of {len(texts)} reused"
    return matrix, status


# --------------------------------------------------------------------------
# Geometry. All backends return L2-normalized vectors, so cosine similarity is
# the plain dot product and cosine distance is 1 - dot. Kept in pure Python so
# a box without numpy still clusters; cluster_scale.py is the array version.
# --------------------------------------------------------------------------

def cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine distance (0 = identical) between two L2-normalized vectors."""
    return 1.0 - sum(x * y for x, y in zip(a, b))


def centroid(vectors: Sequence[Sequence[float]]) -> list[float]:
    """L2-normalized mean vector — a cluster's position in embedding space."""
    if not vectors:
        raise ValueError("centroid of an empty set")
    dims = len(vectors[0])
    acc = [0.0] * dims
    for vec in vectors:
        for i, x in enumerate(vec):
            acc[i] += x
    norm = math.sqrt(sum(x * x for x in acc)) or 1.0
    return [x / norm for x in acc]


def centroid_distance(
    vector: Sequence[float],
    reference: Sequence[Sequence[float]] | Sequence[float],
) -> float:
    """Cosine distance from `vector` to the centroid of `reference`.

    `reference` may be a list of vectors (centroid computed here) or an already
    computed centroid. This is the primitive behind CONTRACTS §5:
    `pain_distance` = centroid_distance(wedge_vec, cluster_evidence_vecs)
    (lower = better grounded) and `incumbent_distance` =
    centroid_distance(wedge_vec, incumbent_positioning_vecs)
    (higher = more novel).
    """
    if reference and isinstance(reference[0], (list, tuple)):
        ref = centroid(reference)  # type: ignore[arg-type]
    else:
        ref = list(reference)  # type: ignore[arg-type]
    return cosine_distance(vector, ref)


def pairwise_matrix(vectors: Sequence[Sequence[float]]) -> list[list[float]]:
    """Full symmetric cosine-distance matrix as Python lists.

    O(n^2) boxed floats: fine for a few hundred items, fatal at 9,000 (~83M
    floats, observed killing an 8 GB box). cluster_vectors() only uses this
    when numpy is missing; with numpy, cluster_scale.condensed_distances()
    builds the upper triangle as an array, a block of rows at a time.
    """
    n = len(vectors)
    dist = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = cosine_distance(vectors[i], vectors[j])
            # Floating-point noise can push identical vectors slightly negative,
            # which some clusterers reject outright.
            d = max(0.0, d)
            dist[i][j] = d
            dist[j][i] = d
    return dist


def percentile(values: Sequence[float], pct: float) -> float:
    """Linear-interpolation percentile; pure Python so offline needs no numpy.

    `pct` must be in [0, 100]. Out of range is rejected rather than clamped: a
    caller asking for p150 has a bug, and quietly handing back the maximum would
    be exactly the kind of silent adjustment this module refuses elsewhere. It
    matters because `pct` reaches here from the CLI and from importing
    consumers — before this guard, p150 raised a bare IndexError from the
    indexing below, and a NEGATIVE pct silently indexed the sorted list from the
    END, so p-5 returned a near-MAXIMUM distance and produced a one-cluster
    collapse labelled `adaptive:p-5` as if it were a real calibration.
    """
    _check_pct(pct)
    ordered = sorted(values)
    return scale.interpolate_sorted(ordered.__getitem__, len(ordered), pct)


def _check_pct(pct: float) -> None:
    """Reject a percentile outside [0, 100] — see percentile() for why."""
    if not math.isfinite(pct) or not 0.0 <= pct <= 100.0:
        raise ValueError(f"percentile must be within [0, 100], got {pct!r}")


def adaptive_cut(dist: Sequence[Sequence[float]], pct: float) -> float:
    """The cut, derived from the pool's own off-diagonal distance distribution.

    See the module docstring: this is the whole reason clustering survives a
    change of embedding model. A degenerate pool (every item identical) yields
    0.0, which would make the clusterer treat nothing as mergeable, so floor it
    at a hair above zero.
    """
    off_diag = [dist[i][j] for i in range(len(dist)) for j in range(len(dist)) if i != j]
    return max(percentile(off_diag, pct), 1e-6)


# --------------------------------------------------------------------------
# Clustering. HDBSCAN is preferred (it finds variable-density pain groups and
# is honest about singletons via label -1); agglomerative average linkage at
# the adaptive cut is the fallback. Average linkage — not single — because
# single-linkage chains through bge's narrow distance band and produces one
# giant snake of a cluster.
# --------------------------------------------------------------------------

def _labels_hdbscan(
    dist: Sequence[Sequence[float]],
    cut: float,
    min_cluster_size: int,
    allow_sklearn: bool,
) -> tuple[list[int], str] | None:
    """HDBSCAN over the precomputed cosine-distance matrix.

    The adaptive cut is passed as `cluster_selection_epsilon` so the
    pool-derived scale still governs merging — otherwise HDBSCAN's own density
    heuristics ignore the calibration this script exists to compute, and on a
    tight evidence pool they call most of it noise.

    Two implementations, and they are NOT interchangeable: the standalone
    `hdbscan` package honours `cluster_selection_epsilon` with a precomputed
    metric, while sklearn's vendored HDBSCAN raises on that combination
    (observed on sklearn 1.9: "only 0-dimensional arrays can be converted to
    Python scalars", from epsilon_search). Without epsilon the calibration is
    gone, so sklearn's version is only used when the caller explicitly asks for
    HDBSCAN — `auto` prefers the deterministic adaptive cut over an
    uncalibrated density run. Returns None when no usable implementation ran.
    """
    np = scale.numpy_or_none()
    if np is None:
        return None
    # asarray, never a list copy: at 9,000 items each copy of the matrix is
    # ~83M boxed floats, and three of them killed an 8 GB box.
    arr = np.asarray(dist, dtype="float64")
    size = max(2, min_cluster_size)

    try:
        import hdbscan  # type: ignore

        model = hdbscan.HDBSCAN(
            metric="precomputed",
            min_cluster_size=size,
            min_samples=1,
            cluster_selection_epsilon=float(cut),
        )
        return model.fit_predict(arr).tolist(), "hdbscan"
    except ImportError:
        pass
    except Exception as exc:
        log(f"[cluster] standalone hdbscan failed ({type(exc).__name__}: {exc})")

    if not allow_sklearn:
        return None

    try:
        from sklearn.cluster import HDBSCAN  # sklearn >= 1.3
    except ImportError:
        return None
    try:
        model = HDBSCAN(
            metric="precomputed",
            min_cluster_size=size,
            min_samples=1,
            cluster_selection_epsilon=float(cut),
            copy=True,
        )
        return model.fit_predict(arr).tolist(), "hdbscan-sklearn"
    except Exception as exc:
        log(
            f"[cluster] sklearn HDBSCAN rejected cluster_selection_epsilon "
            f"({type(exc).__name__}: {exc}); retrying density-only, which "
            f"DISCARDS the adaptive cut {cut:.4f}"
        )
    try:
        model = HDBSCAN(metric="precomputed", min_cluster_size=size, min_samples=1, copy=True)
        return model.fit_predict(arr).tolist(), "hdbscan-sklearn-no-epsilon"
    except Exception as exc:
        log(f"[cluster] sklearn HDBSCAN failed ({type(exc).__name__}: {exc})")
        return None


def _labels_agglomerative(
    dist: Sequence[Sequence[float]], cut: float
) -> tuple[list[int], str]:
    """Average-linkage agglomerative clustering cut at `cut`.

    Uses sklearn when available; otherwise a pure-Python Lance-Williams merge
    so the offline backend needs no scientific stack at all. Same answer, worse
    asymptotics (O(n^3)), which is acceptable for evidence-sized pools. Only
    reached on the pure path — the scale path builds the same tree with scipy
    in cluster_scale.average_linkage().
    """
    matrix = dist
    try:
        import numpy as np
        from sklearn.cluster import AgglomerativeClustering

        model = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=float(cut),
            metric="precomputed",
            linkage="average",
        )
        labels = model.fit_predict(np.asarray(matrix, dtype="float64"))
        return labels.tolist(), "agglomerative-average"
    except ImportError:
        pass
    except Exception as exc:
        log(
            f"[cluster] sklearn agglomerative failed ({type(exc).__name__}: {exc}); "
            "using the pure-Python merge"
        )

    n = len(matrix)
    members: dict[int, list[int]] = {i: [i] for i in range(n)}
    between: dict[tuple[int, int], float] = {
        (i, j): matrix[i][j] for i in range(n) for j in range(i + 1, n)
    }
    while len(members) > 1:
        (a, b), best = min(between.items(), key=lambda kv: kv[1])
        if best > cut:
            break
        for other in list(members):
            if other in (a, b):
                continue
            key_a = (min(a, other), max(a, other))
            key_b = (min(b, other), max(b, other))
            # Lance-Williams update for average linkage: the merged cluster's
            # distance is the size-weighted mean of its parts' distances.
            merged = (
                len(members[a]) * between[key_a] + len(members[b]) * between[key_b]
            ) / (len(members[a]) + len(members[b]))
            between[key_a] = merged
            del between[key_b]
        members[a] = members[a] + members[b]
        del members[b]
        del between[(min(a, b), max(a, b))]
    labels = [0] * n
    for new_label, root in enumerate(sorted(members)):
        for idx in members[root]:
            labels[idx] = new_label
    return labels, "agglomerative-average-purepython"


#: Percentiles the scale path re-cuts the linkage tree at, so a fusion advisory
#: can say which lower percentile would actually split a dominant cluster.
PROBE_PERCENTILES = (25.0, 15.0, 10.0, 5.0)


def _hdbscan_installed() -> bool:
    """Whether the standalone hdbscan package is importable (without importing it)."""
    import importlib.util

    return importlib.util.find_spec("hdbscan") is not None


def _resolve_cut(
    pct: float, distance_threshold: float | None, adaptive: Callable[[float], float]
) -> tuple[float, str]:
    """(cut, cut_basis): the fixed threshold if given, else the adaptive one."""
    if distance_threshold is not None:
        return float(distance_threshold), f"fixed:{float(distance_threshold)}"
    return max(adaptive(pct), 1e-6), f"adaptive:p{pct:g}"


def cluster_vectors(
    vectors: Sequence[Sequence[float]],
    algorithm: str = "auto",
    pct: float = 35.0,
    distance_threshold: float | None = None,
    min_cluster_size: int = 2,
    dense: bool | None = None,
) -> dict[str, Any]:
    """Label `vectors`. Returns labels (-1 = unclustered) plus how it got there.

    `distance_threshold` overrides the adaptive cut with a fixed cosine
    distance — available for reproducing an old run, not recommended for new
    ones (see the module docstring on why fixed cuts do not transfer).

    `dense` picks the path: None (default) uses the numpy scale path whenever
    numpy imports; False forces the pure-Python path. Both give the same
    labels — the switch exists so tests can prove that.
    """
    n = len(vectors)
    if n == 0:
        return {"labels": [], "algorithm": "none", "cut": None, "cut_basis": "n/a"}
    if n == 1:
        return {
            "labels": [0],
            "algorithm": "trivial",
            "cut": None,
            "cut_basis": "n/a:single-item-pool",
        }
    if dense is None:
        dense = scale.numpy_or_none() is not None
    if dense:
        return _cluster_vectors_dense(
            vectors, algorithm, pct, distance_threshold, min_cluster_size
        )

    dist = pairwise_matrix(vectors)
    cut, cut_basis = _resolve_cut(pct, distance_threshold, lambda p: adaptive_cut(dist, p))

    # Density estimation needs a handful of points to mean anything; below that
    # HDBSCAN would call almost everything noise. Route tiny pools to the
    # deterministic cut instead.
    if algorithm == "auto" and n < 8:
        algorithm = "agglomerative"

    labels: list[int] | None = None
    used = ""
    if algorithm in ("auto", "hdbscan"):
        attempt = _labels_hdbscan(
            dist, cut, min_cluster_size, allow_sklearn=(algorithm == "hdbscan")
        )
        if attempt is None:
            if algorithm == "hdbscan":
                log("[cluster] hdbscan unavailable; falling back to agglomerative")
            else:
                log(
                    "[cluster] standalone hdbscan not installed; using "
                    "agglomerative average linkage at the adaptive cut"
                )
        else:
            labels, used = attempt
            # A density run that claims everything is noise has told us nothing.
            # Better to report the deterministic cut than an empty result.
            if all(lab == -1 for lab in labels):
                log("[cluster] hdbscan labelled every item noise; using agglomerative")
                labels = None

    if labels is None:
        labels, used = _labels_agglomerative(dist, cut)

    # Say so when the cut was computed but the clusterer could not use it —
    # a reader comparing runs must not think this shape came from p35.
    if used.endswith("-no-epsilon"):
        cut_basis += ";not-applied"

    upper = [dist[i][j] for i in range(n) for j in range(i + 1, n)]
    return {
        "labels": [int(lab) for lab in labels],
        "algorithm": used,
        "cut": round(cut, 6),
        "cut_basis": cut_basis,
        "median_pairwise_distance": round(percentile(upper, 50.0), 4),
        # Reused by fusion_advisory() so it does not have to recompute the
        # matrix: the distance a typical unrelated pair sits at in THIS pool.
        "p75_pairwise_distance": round(percentile(upper, 75.0), 4),
        # The pure path has no reusable linkage tree, so no cheap probe.
        "percentile_probe": None,
    }


def _cluster_vectors_dense(
    vectors: Sequence[Sequence[float]],
    algorithm: str,
    pct: float,
    distance_threshold: float | None,
    min_cluster_size: int,
) -> dict[str, Any]:
    """cluster_vectors() on arrays: the same cut, labels and stats, at scale.

    Only the condensed upper triangle exists (never the n x n matrix, unless
    HDBSCAN actually runs and needs it). Percentiles come from one sort of it.
    The average-linkage tree is built once and cut at the chosen threshold,
    then re-cut at PROBE_PERCENTILES so the result can say what a lower
    percentile would do — nearly free, since the tree is the expensive part.
    """
    np = scale.numpy_or_none()
    matrix = scale.as_matrix(vectors)
    n = matrix.shape[0]
    condensed = scale.condensed_distances(matrix)
    ordered = np.sort(condensed)
    cut, cut_basis = _resolve_cut(
        pct, distance_threshold, functools.partial(scale.offdiag_percentile, ordered)
    )
    median = scale.upper_percentile(ordered, 50.0)
    p75 = scale.upper_percentile(ordered, 75.0)
    probe_cuts = {
        p: max(scale.offdiag_percentile(ordered, p), 1e-6) for p in PROBE_PERCENTILES
    }
    del ordered  # free one condensed-sized copy before linkage needs its own

    if algorithm == "auto" and n < 8:
        algorithm = "agglomerative"

    labels: list[int] | None = None
    used = ""
    if algorithm == "hdbscan" or (algorithm == "auto" and _hdbscan_installed()):
        attempt = _labels_hdbscan(
            scale.square_from_condensed(condensed), cut, min_cluster_size,
            allow_sklearn=(algorithm == "hdbscan"),
        )
        if attempt is None:
            log("[cluster] hdbscan unavailable; falling back to agglomerative")
        else:
            labels, used = attempt
            if all(lab == -1 for lab in labels):
                log("[cluster] hdbscan labelled every item noise; using agglomerative")
                labels = None
    elif algorithm == "auto":
        log(
            "[cluster] standalone hdbscan not installed; using "
            "agglomerative average linkage at the adaptive cut"
        )

    tree = None
    if labels is None:
        tree = scale.average_linkage(condensed)
        labels, used = scale.labels_at_cut(tree, n, cut), "agglomerative-average"
    del condensed

    if used.endswith("-no-epsilon"):
        cut_basis += ";not-applied"
    return {
        "labels": [int(lab) for lab in labels],
        "algorithm": used,
        "cut": round(cut, 6),
        "cut_basis": cut_basis,
        "median_pairwise_distance": round(median, 4),
        "p75_pairwise_distance": round(p75, 4),
        "percentile_probe": (
            _percentile_probe(tree, n, probe_cuts, min_cluster_size)
            if tree is not None else None
        ),
    }


def _percentile_probe(
    tree: Any, n: int, cuts: dict[float, float], min_cluster_size: int
) -> list[dict]:
    """What each probed percentile would produce: cluster count and largest share.

    Counted the way build_clusters() counts: a group smaller than
    min_cluster_size is unclustered, not a cluster.
    """
    floor = max(1, min_cluster_size)
    probe = []
    for p, cut in cuts.items():
        sizes = [s for s in scale.group_sizes(scale.labels_at_cut(tree, n, cut)) if s >= floor]
        probe.append({
            "percentile": p,
            "cut": round(cut, 6),
            "clusters": len(sizes),
            "largest_share": round(sizes[0] / n, 3) if sizes else 0.0,
        })
    return probe


# --------------------------------------------------------------------------
# Evidence loading (CONTRACTS §2). Append-only JSONL, one object per line.
# --------------------------------------------------------------------------

def expand_inputs(paths: Sequence[str]) -> tuple[list[Path], list[str]]:
    """Resolve CLI paths (files or directories) to a sorted list of JSONL files."""
    files: list[Path] = []
    problems: list[str] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            found = sorted(p.rglob("*.jsonl"))
            if not found:
                problems.append(f"no .jsonl files under {p}")
            files.extend(found)
        elif p.is_file():
            files.append(p)
        else:
            problems.append(f"missing path: {p}")
    # Preserve order but drop repeats (a file named twice, or named and also
    # picked up by its parent directory).
    seen: set[Path] = set()
    unique: list[Path] = []
    for f in files:
        r = f.resolve()
        if r not in seen:
            seen.add(r)
            unique.append(f)
    return unique, problems


def _clean(value: Any) -> str | None:
    """Trim a string field; empty / non-string becomes None (never invented)."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def engagement_value(record: dict) -> int | None:
    """score + comments, or None if the source reported neither.

    None is not zero — CONTRACTS §2 is explicit about that — so a record whose
    source does not report engagement contributes nothing to the sum rather
    than dragging it toward zero.
    """
    eng = record.get("engagement")
    if not isinstance(eng, dict):
        return None
    nums = [
        v
        for v in (eng.get("score"), eng.get("comments"))
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    return int(sum(nums)) if nums else None


def embed_text(record: dict) -> str:
    """The text that carries the complaint: title first, then body."""
    parts = [record.get("_title") or "", record.get("_text") or ""]
    joined = "\n".join(p for p in parts if p).strip()
    return joined[:EMBED_CHAR_LIMIT]


def derive_id(record: dict) -> str:
    """Stable fallback id when a scout omitted one, so /rescan can still diff.

    §2 specifies "sha1-of-source-plus-url". Title and created_utc are folded in
    as well, deliberately: several distinct comments are routinely captured
    against ONE thread permalink, and a source+url-only digest would collide
    them into a single id, which the by-id dedup below would then silently
    merge — losing evidence. The extra fields keep distinct captures distinct.
    This derives a key from data already present; it invents nothing.
    """
    basis = "\n".join(
        str(record.get(k) or "") for k in ("source", "url", "title", "created_utc")
    )
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def load_evidence(files: Sequence[Path]) -> dict[str, Any]:
    """Read the JSONL pile, normalize, and deduplicate.

    Two dedup passes, both mattering for honest weights:
      1. by `id` — the same capture appended twice (a re-run, a resumed scout)
      2. by (url, normalized embed text) — two scouts capturing one permalink
         under different cell_ids, which would otherwise double-count its score
    The survivor keeps the union of cell_ids so provenance is not lost, and the
    dropped ids are reported in `duplicate_ids` (kept_id -> dropped capture ids)
    so /rescan can still resolve an id it saw last time. A line appended twice
    verbatim therefore shows up as its own duplicate, which is the truth: one
    extra copy of that id was collapsed.
    """
    records: list[dict] = []
    by_id: dict[str, dict] = {}
    by_content: dict[str, str] = {}
    duplicate_ids: dict[str, list[str]] = {}
    dropped_no_text = 0
    malformed = 0
    derived_ids = 0
    read_errors: list[str] = []

    for path in files:
        try:
            raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            read_errors.append(f"{path}: {exc}")
            log(f"[cluster] cannot read {path}: {exc}")
            continue
        kept_here = 0
        for lineno, line in enumerate(raw_lines, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                malformed += 1
                log(f"[cluster] skipping malformed line {path}:{lineno}: {exc}")
                continue
            if not isinstance(obj, dict):
                malformed += 1
                log(f"[cluster] skipping non-object line {path}:{lineno}")
                continue

            obj["_title"] = _clean(obj.get("title"))
            obj["_text"] = _clean(obj.get("text"))
            text = embed_text(obj)
            if not text:
                # No title and no body: nothing to cluster on. Recorded, not
                # silently vanished.
                dropped_no_text += 1
                continue

            ident = _clean(obj.get("id"))
            if ident is None:
                ident = derive_id(obj)
                derived_ids += 1
            obj["_id"] = ident
            obj["_embed_text"] = text
            obj["_engagement"] = engagement_value(obj)
            obj["_source_file"] = str(path)

            if ident in by_id:
                duplicate_ids.setdefault(ident, []).append(ident)
                _merge_cells(by_id[ident], obj)
                continue

            url = _clean(obj.get("url"))
            content_key = hashlib.sha1(
                f"{url or ''}\n{' '.join(text.lower().split())}".encode("utf-8")
            ).hexdigest()
            if url is not None and content_key in by_content:
                keeper_id = by_content[content_key]
                duplicate_ids.setdefault(keeper_id, []).append(ident)
                _merge_cells(by_id[keeper_id], obj)
                continue

            by_id[ident] = obj
            by_content[content_key] = ident
            records.append(obj)
            kept_here += 1
        log(f"[cluster] {path}: {kept_here} new records")

    return {
        "records": records,
        "duplicate_ids": duplicate_ids,
        "dropped_no_text": dropped_no_text,
        "malformed": malformed,
        "derived_ids": derived_ids,
        "read_errors": read_errors,
    }


def _merge_cells(keeper: dict, dupe: dict) -> None:
    """Fold a duplicate's cell_id into the survivor so provenance survives."""
    cells = keeper.setdefault("_extra_cell_ids", [])
    cell = _clean(dupe.get("cell_id"))
    if cell and cell not in cells:
        cells.append(cell)


# --------------------------------------------------------------------------
# Cluster assembly (CONTRACTS §3)
# --------------------------------------------------------------------------

_TAG_PREFIX = re.compile(r"^\s*[\[\(][^\]\)]{1,24}[\]\)]\s*[:\-]?\s*")
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s")
CANONICAL_MAX_CHARS = 120


def canonical_phrase(record: dict) -> str:
    """Trim the medoid's own words down to one readable pain phrase.

    Mechanical on purpose — no summarizing, no rewriting. The canonical label
    is a QUOTE of the most central member, shortened: strip markdown noise and
    a leading "[Question]"-style tag, take the first sentence, cap the length
    at a word boundary. If the distiller wants prose it can write its own in
    the card; clusters.json stays traceable to text a human can go read.
    """
    text = record.get("_title") or record.get("_text") or ""
    text = " ".join(text.split())
    text = _TAG_PREFIX.sub("", text)
    text = text.strip(" \t\"'`*_#>-")
    first = _SENTENCE_END.split(text, maxsplit=1)[0] if text else ""
    # A 6-character "sentence" ("Help!") is not a pain statement; in that case
    # keep the fuller text and let the length cap do the trimming.
    phrase = first if len(first) >= 25 else text
    phrase = phrase.rstrip(".")
    if len(phrase) > CANONICAL_MAX_CHARS:
        head = phrase[:CANONICAL_MAX_CHARS]
        if " " in head:
            head = head[: head.rfind(" ")]
        # Mark the trim so nobody mistakes a cut phrase for a complete one.
        phrase = head.rstrip(" ,;:") + "..."
    return phrase


def medoid_index(indices: Sequence[int], vectors: Sequence[Sequence[float]]) -> int:
    """The member minimizing summed cosine distance to its clustermates — the
    most central phrasing, chosen mechanically rather than by engagement (the
    loudest post is often the least representative)."""
    if len(indices) == 1:
        return indices[0]
    if hasattr(vectors, "shape"):
        # Array input: the O(k * d) form in cluster_scale. A 3,000-member
        # cluster through the loop below is ~9M Python dot products.
        return scale.medoid(vectors, indices)
    best_idx, best_total = indices[0], None
    for i in indices:
        total = sum(cosine_distance(vectors[i], vectors[j]) for j in indices if j != i)
        if best_total is None or total < best_total:
            best_idx, best_total = i, total
    return best_idx


def build_clusters(
    records: Sequence[dict],
    vectors: Sequence[Sequence[float]],
    labels: Sequence[int],
    min_cluster_size: int = 2,
) -> tuple[list[dict], list[str]]:
    """Turn labels into the §3 cluster objects, largest pain first.

    `min_cluster_size` is applied to BOTH algorithms so their outputs are
    comparable: HDBSCAN expresses "no group claimed this" as label -1, while
    agglomerative expresses it as a cluster of one, and both belong in
    `unclustered_ids`. One post is a rumour; two independent phrasings are the
    beginning of a pain. Nothing is discarded — the ids stay in the payload.
    """
    grouped: dict[int, list[int]] = {}
    unclustered: list[str] = []
    for idx, label in enumerate(labels):
        if label < 0:  # HDBSCAN noise: an item no pain group claimed
            unclustered.append(records[idx]["_id"])
            continue
        grouped.setdefault(label, []).append(idx)

    for label in [k for k, v in grouped.items() if len(v) < max(1, min_cluster_size)]:
        unclustered.extend(records[i]["_id"] for i in grouped.pop(label))

    built: list[dict] = []
    for _, indices in grouped.items():
        members = [records[i] for i in indices]
        # Highest engagement first: exemplar_urls and member_ids then read as
        # "the loudest instances of this pain" without a second sort downstream.
        ranked = sorted(
            indices, key=lambda i: (records[i]["_engagement"] or -1), reverse=True
        )
        engagements = [r["_engagement"] for r in members if r["_engagement"] is not None]
        authors = {a for a in (_clean(r.get("author")) for r in members) if a}
        communities = {c for c in (_clean(r.get("community")) for r in members) if c}
        cells: list[str] = []
        for r in members:
            for cell in [_clean(r.get("cell_id")), *r.get("_extra_cell_ids", [])]:
                if cell and cell not in cells:
                    cells.append(cell)

        exemplars: list[str] = []
        for i in ranked:
            url = _clean(records[i].get("url"))
            if url and url not in exemplars:
                exemplars.append(url)
            if len(exemplars) == 3:
                break

        built.append(
            {
                "canonical": canonical_phrase(records[medoid_index(indices, vectors)]),
                "member_count": len(indices),
                "distinct_authors": len(authors),
                "distinct_communities": len(communities),
                # None, not 0, when no member's source reported engagement:
                # "the source did not report it" is not "nobody engaged".
                "engagement_sum": sum(engagements) if engagements else None,
                "cell_ids": sorted(cells),
                "exemplar_urls": exemplars,
                "member_ids": [records[i]["_id"] for i in ranked],
            }
        )

    # Weight order: biggest cluster is the loudest pain, ties broken by
    # engagement. cluster_ids are assigned after sorting so c01 is always the
    # heaviest cluster in the run.
    built.sort(key=lambda c: (c["member_count"], c["engagement_sum"] or -1), reverse=True)
    for n, cluster in enumerate(built, start=1):
        cluster["cluster_id"] = f"c{n:02d}"
    # Reorder keys so cluster_id leads, matching the contract's presentation.
    ordered = [
        {
            "cluster_id": c["cluster_id"],
            "canonical": c["canonical"],
            "member_count": c["member_count"],
            "distinct_authors": c["distinct_authors"],
            "distinct_communities": c["distinct_communities"],
            "engagement_sum": c["engagement_sum"],
            "cell_ids": c["cell_ids"],
            "exemplar_urls": c["exemplar_urls"],
            "member_ids": c["member_ids"],
        }
        for c in built
    ]
    return ordered, unclustered


def source_health_for_records(records: Sequence[dict], loaded: dict) -> list[dict]:
    """One entry per evidence source plus input/parse health.

    A source whose records are missing urls or engagement is `degraded`, never
    silently fine — the reader needs to know that a thin cluster is thin
    because the capture was thin, not because the pain is rare.
    """
    health: list[dict] = []
    per_source: dict[str, dict[str, int]] = {}
    for rec in records:
        src = _clean(rec.get("source")) or "unknown"
        stats = per_source.setdefault(src, {"n": 0, "no_url": 0, "no_engagement": 0})
        stats["n"] += 1
        if _clean(rec.get("url")) is None:
            stats["no_url"] += 1
        if rec["_engagement"] is None:
            stats["no_engagement"] += 1

    for src, stats in sorted(per_source.items()):
        degraded = stats["no_url"] or stats["no_engagement"] or src == "unknown"
        detail = (
            f"{stats['n']} records; {stats['no_url']} without url; "
            f"{stats['no_engagement']} without engagement"
        )
        health.append(
            {
                "source": src,
                "status": "degraded" if degraded else "ok",
                "detail": detail,
            }
        )

    input_problems: list[str] = []
    if loaded["malformed"]:
        input_problems.append(f"{loaded['malformed']} malformed line(s) skipped")
    if loaded["dropped_no_text"]:
        input_problems.append(
            f"{loaded['dropped_no_text']} record(s) had neither title nor text"
        )
    if loaded["derived_ids"]:
        input_problems.append(
            f"{loaded['derived_ids']} record(s) had no id "
            f"(derived sha1 of source+url+title+created_utc)"
        )
    if loaded["read_errors"]:
        input_problems.append("read errors: " + "; ".join(loaded["read_errors"]))
    dupes = sum(len(v) for v in loaded["duplicate_ids"].values())
    detail = "; ".join(input_problems) if input_problems else "all lines parsed"
    if dupes:
        detail += f"; {dupes} duplicate captures collapsed"
    if not records:
        # Files existed but yielded nothing usable. That is a failed read, not
        # "no pain found" — never let the two look alike.
        status = "unavailable"
    elif input_problems:
        status = "degraded"
    else:
        status = "ok"
    health.insert(
        0,
        {
            "source": "evidence-input",
            "status": status,
            "detail": f"{len(records)} unique records; {detail}",
        },
    )
    return health


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def infer_run_slug(files: Sequence[Path]) -> str | None:
    """Pull the slug out of a runs/<slug>/evidence/... path. None if absent —
    never guessed from a filename."""
    for path in files:
        parts = path.resolve().parts
        if "runs" in parts:
            i = parts.index("runs")
            if i + 1 < len(parts) - 1:
                return parts[i + 1]
    return None


GATE_NOTE = "advisory for evidence; only the wedge divergence gate is pass/fail"

# Members sampled per cluster when measuring internal spread (see below).
SPREAD_SAMPLE = 40


#: Above this share of all items in one cluster, the advisory fires. Measured on
#: two real runs (6,776 and 9,133 items): at p25 and p10 average linkage chained
#: 22-46% of the pool into one cluster, and the first usable cut held ~10-12%.
FUSION_SHARE = 0.15
#: Below this pool size the older, looser share applies. In a pool of a few
#: hundred, a cluster of 15% is 30-50 posts and is often just the loudest real
#: pain; the chaining that FUSION_SHARE catches is a large-pool behaviour.
FUSION_SHARE_MIN_POOL = 1000
FUSION_SHARE_SMALL_POOL = 0.35


def fusion_share_threshold(n_items: int) -> float:
    """The largest-cluster share above which the size advisory fires."""
    return FUSION_SHARE if n_items >= FUSION_SHARE_MIN_POOL else FUSION_SHARE_SMALL_POOL


def fusion_advisory(
    clusters: Sequence[dict],
    records: Sequence[dict],
    vectors: Sequence[Sequence[float]],
    reference_distance: float | None,
    probe: Sequence[dict] | None = None,
    pct: float | None = None,
) -> str | None:
    """Warn when the biggest cluster looks like fused pains. Returns the text.

    Two tests. First, size: one cluster holding more than
    fusion_share_threshold() of all items (15% from 1,000 items up). At 6-9k items average linkage at p10-p25 chains adjacent pains into
    one dominant cluster, and a card written about it describes nothing in
    particular. When the scale path supplied a `probe` (the same linkage tree
    re-cut at lower percentiles), the advisory names the highest percentile
    that would bring the largest cluster under the threshold — a suggestion
    for the human, never applied here.

    Second, spread: average linkage merges on the MEAN distance, so two members
    of one cluster can sit further apart than the cut — which is how
    adjacent-but-distinct pains ("nobody can see permit status", "nobody can
    see 311 request status") end up as one card. A cluster whose widest
    internal pair exceeds the pool's p75 pairwise distance is holding members
    as far apart as typical UNRELATED items. An absolute multiple of the cut
    would not work — in bge's narrow band 1.25x the cut is past the pool
    maximum.
    """
    if not clusters or not records:
        return None
    big = clusters[0]
    share = big["member_count"] / len(records)
    threshold = fusion_share_threshold(len(records))
    if share > threshold:
        return (
            f"{big['cluster_id']} holds {big['member_count']} of {len(records)} "
            f"items ({share:.0%}). One cluster that large usually means several "
            f"pains merged into one. {_fusion_suggestion(probe, pct, threshold)}"
        )
    if reference_distance is None:
        return None
    index_of = {r["_id"]: i for i, r in enumerate(records)}
    worst: tuple[float, dict] | None = None
    for cluster in clusters:
        # Members are engagement-ranked, so for a very large cluster the first
        # SPREAD_SAMPLE are a reasonable sample; this keeps the check O(1) in
        # cluster size instead of quadratic on a 1000-member pile.
        idxs = [index_of[m] for m in cluster["member_ids"][:SPREAD_SAMPLE] if m in index_of]
        if len(idxs) < 3:
            continue
        if hasattr(vectors, "shape"):
            spread = scale.max_pair_distance(vectors, idxs)
        else:
            spread = max(
                cosine_distance(vectors[a], vectors[b]) for a in idxs for b in idxs if a < b
            )
        if worst is None or spread > worst[0]:
            worst = (spread, cluster)
    if worst is None or worst[0] <= reference_distance:
        return None
    spread, cluster = worst
    return (
        f"{cluster['cluster_id']} ({cluster['member_count']} members) spans "
        f"{spread:.3f} internally, wider than the pool's p75 pairwise distance "
        f"({reference_distance:.3f}) — its members are as far apart as typical "
        f"unrelated evidence. Re-run with a lower --percentile (10-25) if it is "
        f"really two pains."
    )


def _fusion_suggestion(
    probe: Sequence[dict] | None, pct: float | None, threshold: float
) -> str:
    """The re-cluster suggestion for a size advisory, from the probe if there is one."""
    if not probe:
        return "Re-cluster at a lower percentile (try 10, then 5) and compare."
    lower = sorted(
        (row for row in probe if pct is None or row["percentile"] < pct),
        key=lambda row: row["percentile"], reverse=True,
    )
    fits = [row for row in lower if row["largest_share"] <= threshold]
    if fits:
        best = fits[0]
        return (
            f"Re-cluster at percentile {best['percentile']:g}: on this same "
            f"evidence that gives {best['clusters']} clusters, the largest "
            f"holding {best['largest_share']:.0%}."
        )
    if not lower:
        return "No lower percentile was probed; re-cluster at 5 and compare."
    floor = lower[-1]
    return (
        f"Even percentile {floor['percentile']:g} leaves one cluster with "
        f"{floor['largest_share']:.0%} of items, so this may be one genuinely "
        f"dominant pain — read a sample of its members before re-clustering."
    )


def pool_too_large(n: int) -> str | None:
    """The refusal message when n items cannot be clustered on this box, else None.

    The scale path holds the condensed distance matrix (n(n-1)/2 float64) plus
    one working copy; the pure path holds n^2 boxed floats. Past the ceiling
    the process would die without writing anything — observed at 9,133 items
    on an 8 GB box before the scale path existed — so it is refused up front,
    loudly, with what to do instead.
    """
    dense = scale.numpy_or_none() is not None
    ceiling = scale.max_items(dense)
    if n <= ceiling:
        return None
    return (
        f"{n} evidence items is more than this box is set to cluster at once "
        f"({ceiling}); the distance matrix alone needs about "
        f"{scale.estimated_gb(n, dense)} GB. Split the run (fewer search angles "
        f"per run, or one run per community), or on a machine with more memory "
        f"raise PROSPECTOR_CLUSTER_MAX_ITEMS and re-run."
    )


def failed_payload(
    health: list[dict],
    min_clusters: int,
    run_slug: str | None,
    unclustered_ids: Sequence[str] = (),
    duplicate_ids: dict[str, list[str]] | None = None,
) -> dict:
    """The §3 shape for a run that could not cluster anything.

    Still valid JSON with the same keys, so a consumer parses it the same way —
    but with empty `clusters` and a `source_health` block that says the stage
    FAILED. A failed run must never be indistinguishable from a run that found
    no pain.
    """
    return {
        "run_slug": run_slug,
        "embedding_model": None,
        "backend": None,
        "cut_basis": "n/a",
        "algorithm": None,
        "clustered_utc": int(time.time()),
        "evidence_count": len(unclustered_ids),
        "clusters": [],
        "unclustered_ids": list(unclustered_ids),
        "duplicate_ids": duplicate_ids or {},
        "largest_cluster_sizes": [],
        "fusion_advisory": None,
        "percentile_probe": None,
        "gate": {
            "candidate_count": len(unclustered_ids),
            "cluster_count": 0,
            "min_clusters_required": min_clusters,
            "passed": False,
            "largest_cluster_share": None,
            "cut_basis": "n/a",
            "note": GATE_NOTE,
        },
        "source_health": health,
    }


def run(
    inputs: Sequence[str],
    backend: str = "fastembed",
    algorithm: str = "auto",
    pct: float = 35.0,
    distance_threshold: float | None = None,
    min_cluster_size: int = 2,
    min_clusters: int = 6,
    run_slug: str | None = None,
    embedding_cache_dir: str | None = None,
) -> tuple[dict, int]:
    """Full pipeline. Returns (payload, exit_code)."""
    files, path_problems = expand_inputs(inputs)
    for problem in path_problems:
        log(f"[cluster] {problem}")

    health: list[dict] = []
    if not files:
        health.append(
            {
                "source": "evidence-input",
                "status": "unavailable",
                "detail": "; ".join(path_problems) or "no input files resolved",
            }
        )
        return failed_payload(health, min_clusters, run_slug), 1

    loaded = load_evidence(files)
    records = loaded["records"]
    health.extend(source_health_for_records(records, loaded))

    if not records:
        # Nothing usable was read. This is a FAILURE, not "no pain found" —
        # the distinction is the whole discipline of this tool.
        health.append(
            {
                "source": "clustering",
                "status": "unavailable",
                "detail": "no usable evidence records; nothing embedded",
            }
        )
        return (
            failed_payload(
                health,
                min_clusters,
                run_slug or infer_run_slug(files),
                duplicate_ids=loaded["duplicate_ids"],
            ),
            1,
        )

    too_big = pool_too_large(len(records))
    if too_big:
        # Refuse BEFORE embedding: finding out after 45 minutes of embedding
        # that the matrix cannot fit is the failure this guard replaces.
        log(f"[cluster] {too_big}")
        health.append({"source": "clustering", "status": "unavailable", "detail": too_big})
        return (
            failed_payload(
                health, min_clusters, run_slug or infer_run_slug(files),
                unclustered_ids=[r["_id"] for r in records],
                duplicate_ids=loaded["duplicate_ids"],
            ),
            1,
        )

    texts = [r["_embed_text"] for r in records]
    used_backend = backend
    log(f"[cluster] embedding {len(texts)} records with {backend}")
    try:
        vectors, cache_status = embed_for_run(texts, backend, embedding_cache_dir)
        health.append(
            {
                "source": f"embedding:{backend}",
                "status": "ok",
                "detail": f"{model_name_for(backend)}, {len(vectors[0])} dims; "
                          f"embedding cache {cache_status}",
                "embedding_cache": cache_status,
            }
        )
    except Exception as exc:
        if backend == "offline":
            # The dependency-free path failing means something is badly wrong.
            log(f"[cluster] offline embedding failed: {exc}")
            health.append(
                {
                    "source": "embedding:offline",
                    "status": "unavailable",
                    "detail": str(exc),
                }
            )
            return (
                failed_payload(
                    health,
                    min_clusters,
                    run_slug or infer_run_slug(files),
                    unclustered_ids=[r["_id"] for r in records],
                    duplicate_ids=loaded["duplicate_ids"],
                ),
                1,
            )
        log(f"[cluster] fastembed unavailable: {exc}")
        log(
            "[cluster] hint: fastembed needs its model cached in "
            "~/.cache/fastembed (first use downloads ~130MB). Re-run with "
            "--backend offline for the dependency-free lexical fallback."
        )
        health.append(
            {
                "source": "embedding:fastembed",
                "status": "unavailable",
                "detail": f"{type(exc).__name__}: {exc}; fell back to offline lexical backend",
            }
        )
        used_backend = "offline"
        vectors, cache_status = embed_for_run(texts, "offline", embedding_cache_dir)
        health.append(
            {
                "source": "embedding:offline",
                "status": "degraded",
                "detail": (
                    f"{OFFLINE_MODEL_NAME} used as fallback; distances are lexical, "
                    "not semantic — clusters are conservative"
                ),
                "embedding_cache": cache_status,
            }
        )

    result = cluster_vectors(
        vectors,
        algorithm=algorithm,
        pct=pct,
        distance_threshold=distance_threshold,
        min_cluster_size=min_cluster_size,
    )
    clusters, unclustered = build_clusters(
        records, vectors, result["labels"], min_cluster_size=min_cluster_size
    )
    health.append(
        {
            "source": "clustering",
            # Degraded when the clusterer could not apply the pool-derived cut:
            # the shape is then a density guess, not the calibrated grouping.
            "status": "degraded" if ";not-applied" in result["cut_basis"] else "ok",
            "detail": (
                f"{result['algorithm']}, cut {result['cut_basis']}="
                f"{result['cut']}, median pairwise distance "
                f"{result.get('median_pairwise_distance')}"
            ),
        }
    )
    log(
        f"[cluster] {len(clusters)} clusters, {len(unclustered)} unclustered "
        f"via {result['algorithm']} at {result['cut_basis']}={result['cut']}"
    )

    largest = max((c["member_count"] for c in clusters), default=0)
    advisory = fusion_advisory(
        clusters, records, vectors, result.get("p75_pairwise_distance"),
        probe=result.get("percentile_probe"),
        pct=pct if distance_threshold is None else None,
    )
    if advisory:
        log(f"[cluster] advisory: {advisory}")
    payload = {
        "run_slug": run_slug or infer_run_slug(files),
        "embedding_model": model_name_for(used_backend),
        "backend": used_backend,
        "cut_basis": result["cut_basis"],
        "algorithm": result["algorithm"],
        "clustered_utc": int(time.time()),
        "evidence_count": len(records),
        "clusters": clusters,
        "unclustered_ids": unclustered,
        # Kept so /rescan can resolve an id that was folded into another
        # capture of the same permalink.
        "duplicate_ids": loaded["duplicate_ids"],
        # The shape of the result, so a reader can see fusion without opening
        # every card: the five biggest clusters, the size advisory (or null),
        # and — on the scale path — what lower percentiles would produce.
        "largest_cluster_sizes": [c["member_count"] for c in clusters[:5]],
        "fusion_advisory": advisory,
        "percentile_probe": result.get("percentile_probe"),
        # `passed` is ADVISORY here. Evidence is what it is: if the world only
        # complains about three things, three clusters is the finding, not a
        # failure, so this never changes the exit code. Only the wedge
        # divergence gate (CONTRACTS §5) treats a low cluster count as FAIL —
        # there, a collapsed pool means the generator converged and must be
        # re-run hotter.
        "gate": {
            "candidate_count": len(records),
            "cluster_count": len(clusters),
            "min_clusters_required": min_clusters,
            "passed": len(clusters) >= min_clusters,
            "largest_cluster_share": (
                round(largest / len(records), 3) if records else None
            ),
            "cut_basis": result["cut_basis"],
            "note": GATE_NOTE,
        },
        "source_health": health,
    }
    return payload, 0


def _percentile_arg(raw: str) -> float:
    """argparse type for --percentile: a real percentile, 0-100 inclusive."""
    try:
        val = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {raw!r}") from None
    if not math.isfinite(val) or not 0.0 <= val <= 100.0:
        raise argparse.ArgumentTypeError(
            f"must be within [0, 100] (it indexes the pool's own pairwise "
            f"distances), got {raw!r}"
        )
    return val


def _distance_arg(raw: str) -> float:
    """argparse type for --distance-threshold: a cosine distance, 0-2 inclusive."""
    try:
        val = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {raw!r}") from None
    if not math.isfinite(val) or not 0.0 <= val <= 2.0:
        raise argparse.ArgumentTypeError(
            f"must be a cosine distance within [0, 2], got {raw!r}"
        )
    return val


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="cluster.py",
        description=(
            "Dedup and cluster captured evidence (the JSONL the capture stage "
            "wrote) into clusters.json. After this runs, the cluster is the "
            "unit of analysis, never the raw post. The cosine cut is derived "
            "from the pool's own pairwise-distance distribution, because a "
            "fixed threshold does not transfer across embedding models."
        ),
        epilog=(
            "examples:\n"
            "  # whole run directory -> clusters.json\n"
            "  uv run --quiet scripts/cluster.py runs/my-slug/evidence/ \\\n"
            "      --out runs/my-slug/clusters.json\n\n"
            "  # two files, no scientific stack available\n"
            "  uv run --quiet scripts/cluster.py reddit.jsonl hackernews.jsonl "
            "--backend offline\n\n"
            "  # split harder (lower percentile = more, tighter clusters)\n"
            "  uv run --quiet scripts/cluster.py evidence/ --percentile 25\n\n"
            "  # reproduce an older run's fixed cut\n"
            "  uv run --quiet scripts/cluster.py evidence/ --distance-threshold 0.31\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="evidence .jsonl files and/or directories to search recursively",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="also write the JSON payload here (typically runs/<slug>/clusters.json)",
    )
    parser.add_argument(
        "--run-slug",
        default=None,
        help="run slug for the output; inferred from a runs/<slug>/ path if omitted",
    )
    parser.add_argument(
        "--backend",
        default=os.environ.get("PROSPECTOR_EMBED_BACKEND", "fastembed"),
        choices=["fastembed", "offline"],
        help=(
            "fastembed = local ONNX bge-small (semantic, default); "
            "offline = dependency-free lexical fallback. Both key-free."
        ),
    )
    parser.add_argument(
        "--algorithm",
        default="auto",
        choices=["auto", "hdbscan", "agglomerative"],
        help=(
            "auto uses the standalone hdbscan package when it is installed "
            "(it honours the adaptive cut as cluster_selection_epsilon) and "
            "otherwise agglomerative average linkage at that cut. Pass hdbscan "
            "to also allow sklearn's vendored HDBSCAN, which currently cannot "
            "apply the cut and so runs density-only."
        ),
    )
    parser.add_argument(
        "--percentile",
        type=_percentile_arg,
        default=35.0,
        help=(
            "adaptive cut: percentile of the pool's pairwise cosine distances "
            "treated as same-pain (default 35; lower = more, tighter clusters). "
            "Evidence captured against one inspiration sits in a narrow band, "
            "so 10-25 often separates adjacent pains that 35 fuses; at several "
            "thousand items, 5-10."
        ),
    )
    parser.add_argument(
        "--distance-threshold",
        type=_distance_arg,
        default=None,
        help=(
            "fixed cosine-distance cut, overriding the adaptive one. Only for "
            "reproducing a previous run; fixed cuts do not transfer across models."
        ),
    )
    parser.add_argument(
        "--min-cluster-size",
        type=int,
        default=2,
        help=(
            "smallest group that counts as a cluster (default 2: one post is a "
            "rumour, two phrasings are a pain). Smaller groups are reported in "
            "unclustered_ids. Also HDBSCAN's min_cluster_size. Pass 1 to keep "
            "single-post pains as clusters."
        ),
    )
    parser.add_argument(
        "--embedding-cache-dir",
        default=None,
        help=(
            "reuse and extend a per-run embedding cache in this directory "
            "(typically runs/<slug>/.cache). Re-clustering at a new "
            "--percentile then skips embedding entirely; a run killed "
            "mid-embedding resumes from its last checkpoint."
        ),
    )
    parser.add_argument(
        "--min-clusters",
        type=int,
        default=6,
        help=(
            "cluster count below this sets gate.passed=false (advisory only — "
            "evidence is what it is, so this never changes the exit code)"
        ),
    )
    args = parser.parse_args()

    try:
        payload, code = run(
            args.inputs,
            backend=args.backend,
            algorithm=args.algorithm,
            pct=args.percentile,
            distance_threshold=args.distance_threshold,
            min_cluster_size=args.min_cluster_size,
            min_clusters=args.min_clusters,
            run_slug=args.run_slug,
            embedding_cache_dir=args.embedding_cache_dir,
        )
    except Exception as exc:
        # CONTRACTS cross-cutting rule 3: stdout is ALWAYS parseable JSON, so a
        # calling agent never has to distinguish "crashed" from "returned
        # nothing" by sniffing an empty pipe. The traceback still goes to
        # stderr in full — this net makes the failure legible, it does not hide
        # it — and the payload says `unavailable`, never "no pain found".
        traceback.print_exc()
        log(f"[cluster] unexpected failure: {type(exc).__name__}: {exc}")
        payload = failed_payload(
            [
                {
                    "source": "clustering",
                    "status": "unavailable",
                    "detail": f"unexpected failure: {type(exc).__name__}: {exc}",
                }
            ],
            args.min_clusters,
            args.run_slug,
        )
        code = 1

    if args.out:
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        log(f"[cluster] wrote {out_path}")

    print(json.dumps(payload, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
