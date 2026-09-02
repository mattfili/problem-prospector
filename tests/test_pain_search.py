#!/usr/bin/env python3
"""Tests for the pain-search stage modules.

Two halves. The pure half exercises `pain_rubric` — the §3.3 ladder, its caps,
and the frequency corrections — because those are the numbers the whole pipeline
sorts on and they must not move silently. The integration half walks a synthetic
run end to end (frame -> merge -> gate -> cluster -> inventory gate -> intensity
-> report) against real files on disk, so a contract-shape drift fails here
rather than twenty minutes into a live run.

Run:  uv run --quiet tests/test_pain_search.py
      PROSPECTOR_EMBED_BACKEND=offline uv run --quiet tests/test_pain_search.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import pain_capture  # noqa: E402
import pain_cards  # noqa: E402
import pain_intensity  # noqa: E402
import pain_report  # noqa: E402
import pain_rubric as rubric  # noqa: E402
import pain_stages  # noqa: E402

TEST_SLUG = "pain-search-selftest-1970-01-01"


def evidence_id(source: str, url: str) -> str:
    """The CONTRACTS §2 id recipe: sha1 of source plus url."""
    return hashlib.sha1(f"{source}{url}".encode()).hexdigest()


class WorkspaceRoot(unittest.TestCase):
    """Run state belongs to the user's project, not to this bundle's install dir.

    Once installed as a plugin, `PLUGIN_ROOT` is a versioned cache path that the
    next `plugin update` replaces. A run written there is research the user cannot
    find and an update silently deletes.
    """

    def tearDown(self) -> None:
        for var in ("PROSPECTOR_RUNS_ROOT", "CLAUDE_PROJECT_DIR"):
            os.environ.pop(var, None)

    def test_runs_are_not_written_under_the_bundle(self) -> None:
        os.environ["PROSPECTOR_RUNS_ROOT"] = "/tmp/some-project"
        self.assertEqual(
            pain_stages.run_dir("x-2026-08-18"),
            Path("/tmp/some-project/runs/x-2026-08-18").resolve())
        self.assertNotIn(str(pain_stages.PLUGIN_ROOT),
                         str(pain_stages.run_dir("x-2026-08-18")))

    def test_explicit_override_beats_the_host_variable(self) -> None:
        os.environ["CLAUDE_PROJECT_DIR"] = "/tmp/host-project"
        os.environ["PROSPECTOR_RUNS_ROOT"] = "/tmp/explicit"
        self.assertTrue(str(pain_stages.workspace_root()).endswith("explicit"))

    def test_host_variable_is_used_when_no_override(self) -> None:
        os.environ["CLAUDE_PROJECT_DIR"] = "/tmp/host-project"
        self.assertTrue(str(pain_stages.workspace_root()).endswith("host-project"))

    def test_falls_back_to_the_working_directory(self) -> None:
        self.assertEqual(pain_stages.workspace_root(), Path.cwd().resolve())

    def test_blank_variable_is_ignored(self) -> None:
        os.environ["PROSPECTOR_RUNS_ROOT"] = "   "
        self.assertEqual(pain_stages.workspace_root(), Path.cwd().resolve())


class FrequencyRubric(unittest.TestCase):
    def setUp(self) -> None:
        self.thresholds = rubric.BASE_THRESHOLDS

    def test_high_needs_all_three_thresholds(self) -> None:
        cluster = {"member_count": 47, "distinct_authors": 39,
                   "distinct_communities": 6, "engagement_sum": 3021}
        read, note = rubric.frequency_read(cluster, self.thresholds, None)
        self.assertEqual(read, "high")
        self.assertIsNone(note)

    def test_repetition_demotes_one_level(self) -> None:
        # 40 members but 12 authors: 0.3 < 0.4, so high demotes to medium.
        cluster = {"member_count": 40, "distinct_authors": 12,
                   "distinct_communities": 4, "engagement_sum": 10}
        read, note = rubric.frequency_read(cluster, self.thresholds, None)
        self.assertEqual(read, "medium")
        self.assertIn("repeating themselves", note)

    def test_single_community_caps_at_medium_not_low(self) -> None:
        # The disclosed reading: the explicit cap-at-medium correction overrides the
        # medium threshold's two-community leg, so a broad single-subreddit pain
        # caps rather than collapses.
        cluster = {"member_count": 47, "distinct_authors": 39,
                   "distinct_communities": 1, "engagement_sum": 10}
        read, note = rubric.frequency_read(cluster, self.thresholds, None)
        self.assertEqual(read, "medium")
        self.assertIn("came from a single community", note)

    def test_two_communities_cap_at_medium_without_the_echo_wording(self) -> None:
        cluster = {"member_count": 47, "distinct_authors": 39,
                   "distinct_communities": 2, "engagement_sum": 10}
        read, note = rubric.frequency_read(cluster, self.thresholds, None)
        self.assertEqual(read, "medium")
        self.assertIn("come from only", note)
        self.assertNotIn("single community", note)

    def test_engagement_promotes_medium_only_with_three_communities(self) -> None:
        cluster = {"member_count": 10, "distinct_authors": 8,
                   "distinct_communities": 3, "engagement_sum": 9000}
        read, note = rubric.frequency_read(cluster, self.thresholds, 5000)
        self.assertEqual(read, "high")
        self.assertIn("frequency raised from medium to high", note)

    def test_engagement_never_promotes_out_of_low(self) -> None:
        cluster = {"member_count": 3, "distinct_authors": 3,
                   "distinct_communities": 3, "engagement_sum": 999_999}
        read, _ = rubric.frequency_read(cluster, self.thresholds, 5000)
        self.assertEqual(read, "low")

    def test_decile_disabled_under_ten_clusters(self) -> None:
        self.assertIsNone(rubric.engagement_top_decile(
            [{"engagement_sum": n} for n in range(9)]))
        self.assertIsNotNone(rubric.engagement_top_decile(
            [{"engagement_sum": n} for n in range(20)]))

    def test_scaling_keeps_communities_and_ladder_order(self) -> None:
        scaled = rubric.scaled_thresholds(60)
        self.assertEqual(scaled["thresholds"]["high"]["distinct_communities"], 3)
        self.assertGreater(scaled["thresholds"]["high"]["cluster_size"],
                           scaled["thresholds"]["medium"]["cluster_size"])


class IntensityRubric(unittest.TestCase):
    @staticmethod
    def derive(markers: dict[str, list[str]], recurring: set[str] | None = None) -> dict:
        authors = {m: set(a) for m, a in markers.items()}
        return rubric.derive_intensity(set(markers), authors, recurring or set())

    def test_no_markers_is_preference(self) -> None:
        self.assertEqual(self.derive({})["score"], 1)

    def test_feeling_only_caps_at_two(self) -> None:
        result = self.derive({"profanity_urgency": ["a", "b", "c"]})
        self.assertEqual(result["score"], 2)

    def test_one_cost_marker_two_authors_is_three(self) -> None:
        result = self.derive({"workaround_built": ["a", "b"]})
        self.assertEqual(result["score"], 3)
        self.assertEqual(result["read"], "medium")

    def test_cost_marker_single_author_stays_two_and_says_why(self) -> None:
        result = self.derive({"workaround_built": ["a"], "complainer_is_buyer": ["b"]})
        self.assertEqual(result["score"], 2)
        self.assertIn("single author each", result["note"])

    def test_paid_pain_needs_buyer(self) -> None:
        without = self.derive({"money_loss": ["a", "b"], "time_quantified": ["c", "d"]})
        self.assertEqual(without["score"], 3)
        self.assertIn("enough markers for level 4", without["note"])
        with_buyer = self.derive({"money_loss": ["a", "b"], "time_quantified": ["c", "d"],
                                  "complainer_is_buyer": ["a"]})
        self.assertEqual(with_buyer["score"], 4)
        self.assertEqual(with_buyer["read"], "high")

    def test_bleeding_needs_recurring_and_two_buyers(self) -> None:
        markers = {"money_loss": ["a", "b"], "time_quantified": ["c", "d"],
                   "complainer_is_buyer": ["a", "c"]}
        self.assertEqual(self.derive(markers, {"a"})["score"], 4)
        self.assertEqual(self.derive(markers, {"a", "c"})["score"], 5)

    def test_single_author_cluster_caps_at_two(self) -> None:
        result = self.derive({"money_loss": ["solo"], "time_quantified": ["solo"],
                              "workaround_built": ["solo"], "complainer_is_buyer": ["solo"]})
        self.assertEqual(result["score"], 2)
        self.assertIn("single author", result["note"])

    def test_markers_block_holds_all_six_keys(self) -> None:
        result = self.derive({"money_loss": ["a", "b"]})
        self.assertEqual(set(result["markers"]), set(rubric.MARKERS))
        self.assertTrue(result["markers"]["money_loss"])
        self.assertFalse(result["markers"]["abandonment"])

    def test_quadrant_boundaries(self) -> None:
        self.assertEqual(rubric.quadrant("high", 4), "high-freq/high-intensity")
        self.assertEqual(rubric.quadrant("medium", 5), "low-freq/high-intensity")
        self.assertEqual(rubric.quadrant("high", 3), "high-freq/low-intensity")


class QuoteNormalisation(unittest.TestCase):
    def test_typographic_folding_and_whitespace(self) -> None:
        self.assertEqual(
            rubric.normalize_quote("I  rebuilt\nthe queue’s guts"),
            "I rebuilt the queue's guts",
        )

    def test_word_count(self) -> None:
        self.assertEqual(rubric.word_count("I rebuilt the whole queue in Excel"), 7)


class FailureIsNotAbsence(unittest.TestCase):
    """Zero items from a failed source must never be recorded as a zero-result.

    Found by a live capture: Arctic Shift 422'd, pullpush 429'd, and the first
    implementation still wrote `searched-no-results` on top of the two
    `unavailable` lines — a rate limit turned into "nobody is complaining", which
    inverts the run's conclusion.
    """

    SLUG = "capture-result-selftest-1970-01-01"

    def setUp(self) -> None:
        self.directory = pain_stages.run_dir(self.SLUG)
        shutil.rmtree(self.directory, ignore_errors=True)
        (self.directory / "evidence" / ".staging").mkdir(parents=True, exist_ok=True)
        self.out = self.directory / "evidence" / ".staging" / "reddit-m01.jsonl"

    def tearDown(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)

    def statuses(self) -> list[str]:
        health, _ = pain_stages.read_jsonl(self.directory / "source_health.json")
        return [str(e.get("status")) for e in health]

    def result(self, health: list[dict]) -> dict:
        return {"ok": True, "script": "reddit_search.py", "exit_code": 0,
                "payload": {"source_health": health, "totals": {"items_written": 0}},
                "error": None, "stderr_tail": None}

    def test_every_host_failed_is_not_a_zero_result(self) -> None:
        self.out.write_text("")
        outcome = pain_capture._capture_result(
            self.SLUG,
            self.result([
                {"source": "reddit:arctic-shift", "status": "unavailable",
                 "detail": "HTTP 422", "fallback": "pullpush"},
                {"source": "reddit:pullpush", "status": "unavailable", "detail": "HTTP 429"},
            ]),
            self.out, "reddit", "m01", "permit software")
        self.assertFalse(outcome["zero_result"])
        self.assertFalse(outcome["ok"])
        self.assertEqual(len(outcome["sources_failed"]), 2)
        self.assertIn("NOT a finding about the world", outcome["note"])
        self.assertNotIn("searched-no-results", self.statuses())

    def test_healthy_source_with_no_items_is_a_zero_result(self) -> None:
        self.out.write_text("")
        outcome = pain_capture._capture_result(
            self.SLUG,
            self.result([{"source": "reddit:arctic-shift", "status": "ok",
                          "detail": "4/4 subreddits served"}]),
            self.out, "reddit", "m01", "foia backlog spreadsheet")
        self.assertTrue(outcome["zero_result"])
        self.assertTrue(outcome["ok"])
        self.assertIsNone(outcome["note"])
        self.assertIn("searched-no-results", self.statuses())

    def test_items_captured_adds_no_extra_line(self) -> None:
        self.out.write_text(json.dumps({"id": "abc", "source": "reddit"}) + "\n")
        outcome = pain_capture._capture_result(
            self.SLUG,
            self.result([{"source": "reddit:arctic-shift", "status": "ok", "detail": "served"}]),
            self.out, "reddit", "m01", "permit")
        self.assertFalse(outcome["zero_result"])
        self.assertEqual(outcome["items_in_staging_file"], 1)
        self.assertEqual(self.statuses(), ["ok"])


class DialogIngest(unittest.TestCase):
    """Records handed over by a model are the one capture path with no script.

    So the validation is the contract here: computed ids, real Reddit permalinks,
    engagement-or-null, and an all-or-nothing batch.
    """

    SLUG = "dialog-ingest-selftest-1970-01-01"

    def setUp(self) -> None:
        self.directory = pain_stages.run_dir(self.SLUG)
        shutil.rmtree(self.directory, ignore_errors=True)
        matrix = [{"cell_id": f"m0{n}", "persona": f"p{n}", "vertical": f"v{n}",
                   "framing": f"f{n}", "queries": ["a", "b", "c"], "subreddits": ["sysadmin"]}
                  for n in range(1, 7)]
        pain_stages.create_run("dialog ingest selftest", matrix, run_date="1970-01-01")

    def tearDown(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)

    @staticmethod
    def record(**overrides) -> dict:
        base = {
            "url": "https://www.reddit.com/r/sysadmin/comments/d1/permit/",
            "title": "permit status invisible", "text": "I rebuilt the queue in Excel",
            "author": "u/one", "community": "r/sysadmin",
            "engagement": {"score": 12, "comments": 3}, "created_utc": 1731000000,
        }
        base.update(overrides)
        return base

    def test_id_is_computed_not_accepted(self) -> None:
        result = pain_capture.ingest_records(
            self.SLUG, "m01", "permit", [self.record(id="attacker-supplied")])
        self.assertTrue(result["ok"], result)
        staged, _ = pain_stages.read_jsonl(
            self.directory / "evidence" / ".staging" / "dialog-m01.jsonl")
        expected = evidence_id("dialog", self.record()["url"])
        self.assertEqual(staged[0]["id"], expected)
        self.assertEqual(staged[0]["source"], "dialog")
        self.assertEqual(staged[0]["query"], "permit")

    def test_off_reddit_url_is_rejected(self) -> None:
        result = pain_capture.ingest_records(
            self.SLUG, "m01", "q", [self.record(url="https://example.com/made-up")])
        self.assertFalse(result["ok"])
        self.assertIn("not Reddit", result["rejected"][0]["rejected_because"])

    def test_bare_zero_engagement_is_rejected(self) -> None:
        result = pain_capture.ingest_records(self.SLUG, "m01", "q", [self.record(engagement=0)])
        self.assertFalse(result["ok"])
        self.assertIn("0 is a claim", result["rejected"][0]["rejected_because"])

    def test_null_engagement_is_allowed(self) -> None:
        result = pain_capture.ingest_records(self.SLUG, "m01", "q", [self.record(engagement=None)])
        self.assertTrue(result["ok"], result)

    def test_one_bad_record_stages_nothing(self) -> None:
        result = pain_capture.ingest_records(self.SLUG, "m01", "q", [
            self.record(), self.record(url="https://www.reddit.com/r/x/comments/d2/a/",
                                       title="", text="")])
        self.assertFalse(result["ok"])
        self.assertEqual(result["staged"], 0)
        self.assertFalse((self.directory / "evidence" / ".staging" / "dialog-m01.jsonl").exists())

    def test_unknown_cell_is_refused(self) -> None:
        result = pain_capture.ingest_records(self.SLUG, "m99", "q", [self.record()])
        self.assertFalse(result["ok"])
        self.assertIn("not in this run's matrix", result["error"])

    def test_empty_batch_is_a_zero_result_not_a_failure(self) -> None:
        result = pain_capture.ingest_records(self.SLUG, "m01", "q", [])
        self.assertTrue(result["ok"])
        self.assertTrue(result["zero_result"])
        health, _ = pain_stages.read_jsonl(self.directory / "source_health.json")
        self.assertIn("searched-no-results", [e.get("status") for e in health])

    def test_merge_collapses_a_post_both_sources_captured(self) -> None:
        shared = "https://www.reddit.com/r/sysadmin/comments/shared/permit/"
        staging = self.directory / "evidence" / ".staging"
        staging.mkdir(parents=True, exist_ok=True)
        # Arctic Shift's copy, with the fuller body.
        (staging / "reddit-m01.jsonl").write_text(json.dumps({
            "id": evidence_id("reddit", shared), "cell_id": "m01", "source": "reddit",
            "url": shared, "title": "t", "text": "a much longer verbatim body " * 5,
            "author": "u/one", "community": "r/sysadmin", "engagement": {"score": 9},
            "created_utc": 1, "captured_utc": 2, "query": "permit"}) + "\n")
        # dialog's copy of the same post, plus one it alone found.
        pain_capture.ingest_records(self.SLUG, "m01", "permit", [
            self.record(url=shared, text="short"),
            self.record(url="https://www.reddit.com/r/sysadmin/comments/only/x/"),
        ])
        merged = pain_stages.merge_staging(self.SLUG)
        self.assertEqual(merged["cross_source_duplicates_collapsed"]["collapsed"], 1)
        self.assertEqual(
            merged["cross_source_duplicates_collapsed"]["dropped_from"], {"dialog": 1})
        # The fuller Arctic Shift record survived; dialog keeps only its unique find.
        self.assertEqual(merged["evidence_files"], {"reddit": 1, "dialog": 1})
        records, _ = pain_stages.evidence_records(self.SLUG)
        self.assertEqual(len([r for r in records if r["url"].rstrip("/") == shared.rstrip("/")]), 1)


class ThinCaptureSourceFloor(unittest.TestCase):
    """The source floor distinguishes a recorded relevance skip from silence.

    Observed live (2026-09-02): a 928-item run with every non-technical source
    deliberately skipped was stopped by "2 responding sources < 3" — a floor
    that frame could never satisfy. A skip is a decision on the record; only a
    relevant source that stayed SILENT may fire the source leg.
    """

    def gate(self, n_items: int, per_source: dict, health: list[dict]) -> dict:
        records = [{"id": str(i)} for i in range(n_items)]
        real_records = pain_stages.evidence_records
        real_jsonl = pain_stages.read_jsonl
        pain_stages.evidence_records = lambda slug: (records, per_source)
        pain_stages.read_jsonl = lambda path: (health, 0)
        try:
            return pain_stages.capture_gate("synthetic-floor-test", record=False)
        finally:
            pain_stages.evidence_records = real_records
            pain_stages.read_jsonl = real_jsonl

    def test_all_silent_sources_skipped_waives_the_floor(self) -> None:
        gate = self.gate(60, {"reddit": 47, "hackernews": 13}, [
            {"source": "stackoverflow", "status": "skipped", "detail": "no code surface"},
            {"source": "producthunt", "status": "skipped", "detail": "vendor copy"},
        ])
        self.assertEqual(gate["decision"], "proceed")
        self.assertTrue(gate["source_floor_waived"])
        self.assertIn("relevance skip", gate["guidance"])

    def test_a_silent_attempted_source_still_stops(self) -> None:
        gate = self.gate(60, {"reddit": 47, "hackernews": 13}, [
            {"source": "stackoverflow", "status": "skipped", "detail": "no code surface"},
        ])
        self.assertEqual(gate["decision"], "stop")
        self.assertFalse(gate["source_floor_waived"])
        self.assertIn("producthunt", gate["guidance"])
        # The guidance names the failing leg only — no item-count story here.
        self.assertNotIn("group of 2 looks identical", gate["guidance"])

    def test_item_floor_guidance_names_only_the_item_leg(self) -> None:
        gate = self.gate(10, {"reddit": 5, "hackernews": 3, "stackoverflow": 2}, [])
        self.assertEqual(gate["decision"], "stop")
        self.assertEqual(len(gate["reasons"]), 1)
        self.assertIn("10 posts", gate["guidance"])
        self.assertNotIn("stayed silent", gate["guidance"])


class EndToEnd(unittest.TestCase):
    """A full synthetic pain-search run against real files on disk."""

    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = pain_stages.run_dir(TEST_SLUG)
        shutil.rmtree(cls.directory, ignore_errors=True)
        cls.matrix = [
            {"cell_id": f"m0{n}", "persona": f"persona {n}", "vertical": f"vertical {n}",
             "framing": f"framing {n}", "queries": [f"q{n}a", f"q{n}b", f"q{n}c"],
             "subreddits": ["sysadmin"]}
            for n in range(1, 7)
        ]

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.directory, ignore_errors=True)

    def stage_evidence(self) -> None:
        """Write synthetic staged captures: two pains, several authors, two sources."""
        staging = self.directory / "evidence" / ".staging"
        staging.mkdir(parents=True, exist_ok=True)
        rows: dict[str, list[dict]] = {
            "reddit-m01": [], "hackernews-m02": [], "stackoverflow-m03": []}
        for n in range(24):
            author = f"u/author{n % 8}"
            url = f"https://www.reddit.com/r/sysadmin/comments/p{n}/permit/"
            rows["reddit-m01"].append({
                "id": evidence_id("reddit", url), "cell_id": "m01", "source": "reddit",
                "url": url, "title": "permit status is invisible to everyone",
                "text": f"nobody can see where a permit is. I rebuilt the whole queue in Excel."
                        f" three hours every Monday. run {n}",
                "author": author, "community": f"r/sub{n % 4}",
                "engagement": {"score": 10 + n, "comments": n},
                "created_utc": 1731000000 + n, "captured_utc": 1753920000,
                "query": "permit status tracker",
            })
        for n in range(20):
            url = f"https://news.ycombinator.com/item?id={9000 + n}"
            rows["hackernews-m02"].append({
                "id": evidence_id("hackernews", url), "cell_id": "m02",
                "source": "hackernews", "url": url,
                "title": "records request backlog is unmanageable in a spreadsheet",
                "text": f"our foia backlog lives in a shared sheet and we paid 4k in late fees."
                        f" I approve the invoices. note {n}",
                "author": f"hn{n % 7}", "community": "hackernews",
                "engagement": {"score": 5 + n, "comments": None},
                "created_utc": 1731000000 + n, "captured_utc": 1753920000,
                "query": "records request backlog",
            })
        for n in range(6):
            url = f"https://stackoverflow.com/questions/{5000 + n}"
            rows["stackoverflow-m03"].append({
                "id": evidence_id("stackoverflow", url), "cell_id": "m03",
                "source": "stackoverflow", "url": url,
                "title": "parsing a permit export that changes shape every month",
                "text": f"the export schema drifts and my importer breaks. case {n}",
                "author": f"so{n}", "community": "stackoverflow",
                "engagement": {"score": n, "comments": None},
                "created_utc": 1731000000 + n, "captured_utc": 1753920000,
                "query": "permit export schema",
            })
        for name, records in rows.items():
            (staging / f"{name}.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
            )
        (staging / "health-m01.jsonl").write_text(
            json.dumps({"source": "reddit:arctic-shift", "status": "ok",
                        "fallback": None, "detail": "synthetic"}) + "\n"
            + "{truncated half line\n",
            encoding="utf-8",
        )

    def test_01_frame_gate_rejects_a_short_matrix(self) -> None:
        result = pain_stages.create_run("selftest", self.matrix[:3], run_date="1970-01-01")
        self.assertFalse(result["ok"])
        self.assertTrue(any("6-12" in p for p in result["problems"]))

    def test_02_frame_writes_inputs(self) -> None:
        result = pain_stages.create_run(
            "pain search selftest", self.matrix, run_date="1970-01-01"
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["slug"], TEST_SLUG)
        inputs = pain_stages.read_json(self.directory / "inputs.json")
        self.assertEqual(len(inputs["matrix"]), 6)
        self.assertTrue(inputs["flags"]["cards_only"])

    def test_03_merge_reports_the_dropped_health_line(self) -> None:
        self.stage_evidence()
        result = pain_stages.merge_staging(TEST_SLUG)
        self.assertEqual(
            result["evidence_files"], {"reddit": 24, "hackernews": 20, "stackoverflow": 6})
        self.assertEqual(result["malformed_health_lines_dropped"], 1)
        health, _ = pain_stages.read_jsonl(self.directory / "source_health.json")
        self.assertTrue(any("dropped as malformed" in str(e.get("detail")) for e in health))

    def test_04_merge_is_idempotent(self) -> None:
        again = pain_stages.merge_staging(TEST_SLUG)
        self.assertEqual(
            again["evidence_files"], {"reddit": 24, "hackernews": 20, "stackoverflow": 6})

    def test_05_capture_gate_proceeds_on_three_responding_sources(self) -> None:
        gate = pain_stages.capture_gate(TEST_SLUG, record=False)
        self.assertEqual(gate["total_items"], 50)
        self.assertEqual(gate["decision"], "proceed")
        self.assertEqual(gate["responding_sources"], ["hackernews", "reddit", "stackoverflow"])

    def test_06_cluster_seeds_cards(self) -> None:
        os.environ.setdefault("PROSPECTOR_EMBED_BACKEND", "offline")
        result = pain_cards.cluster_and_seed_cards(TEST_SLUG, percentile=25)
        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(result["clusters"], 1)
        self.assertEqual(result["clusters"], result["cards_written"])
        card = pain_stages.read_json(
            self.directory / "cards" / f"{result['cards'][0]['cluster_id']}.json")
        self.assertIsNone(card["intensity"])
        self.assertIn("saturation", card)
        self.assertIsNone(card["inventory_gate"]["verdict"])

    def test_07_intensity_refuses_before_the_gate(self) -> None:
        cluster_id = self.first_cluster()
        result = pain_intensity.score_intensity(TEST_SLUG, cluster_id, {})
        self.assertFalse(result["ok"])
        self.assertIn("inventory gate", result["error"])

    def test_08_exclusion_normalises_the_flag_prefix(self) -> None:
        cluster_id = self.first_cluster()
        result = pain_cards.set_inventory_gate(
            TEST_SLUG, cluster_id, "exclude", ["needs warehoused stock"])
        self.assertTrue(result["flag_prefix_normalized"])
        self.assertTrue(result["flags"][0].startswith("excluded:"))
        blocked = pain_intensity.score_intensity(TEST_SLUG, cluster_id, {})
        self.assertFalse(blocked["ok"])
        pain_cards.set_inventory_gate(TEST_SLUG, cluster_id, "pass", [])

    def test_09_intensity_rejects_unciteable_quotes(self) -> None:
        cluster_id = self.first_cluster()
        url = self.first_member_url(cluster_id)
        cases = {
            "money_loss": [{"quote": "we lost a great deal of money on this", "url": url}],
            "time_quantified": [{"quote": "three hours every monday", "url": url}],
            "workaround_built": [{"quote": "I rebuilt the whole queue in Excel",
                                  "url": "https://example.com/not-a-member"}],
            "abandonment": [{"quote": " ".join(["word"] * 16), "url": url}],
        }
        result = pain_intensity.score_intensity(TEST_SLUG, cluster_id, cases)
        self.assertFalse(result["ok"])
        reasons = {r["marker"]: r["rejected_because"] for r in result["rejected"]}
        self.assertIn("verbatim", reasons["money_loss"])
        self.assertIn("only in case", reasons["time_quantified"])
        self.assertIn("not a member", reasons["workaround_built"])
        self.assertIn("the cap is", reasons["abandonment"])
        self.assertIsNone(
            pain_stages.read_json(self.directory / "cards" / f"{cluster_id}.json")["intensity"])

    def test_10_intensity_rejects_an_unknown_marker(self) -> None:
        result = pain_intensity.score_intensity(
            TEST_SLUG, self.first_cluster(), {"vibes": []})
        self.assertFalse(result["ok"])
        self.assertIn("unknown marker", result["error"])

    def test_11_intensity_writes_a_derived_score(self) -> None:
        cluster_id = self.first_cluster()
        members = self.members(cluster_id)
        quotes = [
            {"quote": "I rebuilt the whole queue in Excel", "url": m["url"]}
            for m in members[:3]
        ]
        timed = [
            {"quote": "three hours every Monday", "url": m["url"], "recurring": True}
            for m in members[:3]
        ]
        result = pain_intensity.score_intensity(
            TEST_SLUG, cluster_id,
            {"workaround_built": quotes, "time_quantified": timed},
            canonical_pain="Permit status is invisible to staff and applicants alike",
        )
        self.assertTrue(result["ok"], result)
        # Two cost markers at >=2 distinct authors but no buyer marker: level 3,
        # carried by the disclosed at-least-one reading of the ladder.
        self.assertEqual(result["score"], 3)
        self.assertIn("enough markers for level 4", result["note"])
        card = pain_stages.read_json(self.directory / "cards" / f"{cluster_id}.json")
        self.assertEqual(card["intensity"]["read"], "medium")
        self.assertEqual(card["quadrant"],
                         rubric.quadrant(card["frequency"]["read"], 3))
        self.assertTrue(all(e["words"] <= 15 for e in card["intensity"]["exemplars"]))
        self.assertEqual(card["canonical_pain"],
                         "Permit status is invisible to staff and applicants alike")

    def test_12_report_renders_with_no_composite(self) -> None:
        for path in pain_cards.card_paths(TEST_SLUG):
            card = pain_stages.read_json(path)
            if card["inventory_gate"]["verdict"] is None:
                pain_cards.set_inventory_gate(TEST_SLUG, card["cluster_id"], "pass", [])
        result = pain_report.render_report(TEST_SLUG)
        self.assertTrue(result["ok"], result)
        text = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn("**Sort key:**", text)
        self.assertIn("Frequency thresholds used", text)
        self.assertIn("Source health", text)
        # Rubric-interpretation disclosures are maintainer-facing: absent from the
        # default report, present with verbose=True.
        self.assertNotIn("Encoded rubric interpretation", text)
        verbose = pain_report.render_report(TEST_SLUG, verbose=True)
        self.assertIn("Encoded rubric interpretation",
                      Path(verbose["path"]).read_text(encoding="utf-8"))
        pain_report.render_report(TEST_SLUG)  # restore the default rendering
        for banned in ("opportunity score", "signal strength", "weighted sum"):
            self.assertNotIn(banned, text.lower())

    def test_13_status_names_the_clusters_still_unscored(self) -> None:
        status = pain_report.run_status(TEST_SLUG)
        self.assertEqual(status["stage"], "3 — gated, intensity incomplete")
        self.assertEqual(status["ungated_clusters"], [])
        self.assertTrue(status["unscored_clusters"])

    def test_14_empty_marker_evidence_scores_one_and_completes_the_run(self) -> None:
        # A cluster with nothing citable is a preference, not a problem: score 1.
        # That is a legal panel, and scoring it is how a run reaches completion.
        for cluster_id in pain_report.run_status(TEST_SLUG)["unscored_clusters"]:
            result = pain_intensity.score_intensity(TEST_SLUG, cluster_id, {})
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["score"], 1)
            self.assertEqual(result["read"], "low")
        rendered = pain_report.render_report(TEST_SLUG)
        self.assertTrue(rendered["ok"])
        self.assertEqual(rendered["unscored"], 0)
        status = pain_report.run_status(TEST_SLUG)
        self.assertIn("3 complete", status["stage"])
        self.assertIn("resumes this run where pain-search stops", status["next"])

    def test_15_recluster_refuses_without_reseed(self) -> None:
        result = pain_cards.cluster_and_seed_cards(TEST_SLUG, percentile=15)
        self.assertFalse(result["ok"])
        self.assertIn("reseed=true", result["error"])

    # -- helpers ----------------------------------------------------------
    def first_cluster(self) -> str:
        """The cluster whose members carry the workaround/time quotes we cite below.

        Never assume `c01`: cluster ids are assigned by size, so which pain lands
        first depends on the embedding backend and the cut.
        """
        clusters = pain_stages.read_json(self.directory / "clusters.json")
        records, _ = pain_stages.evidence_records(TEST_SLUG)
        by_id = {r["id"]: r for r in records}
        for cluster in clusters["clusters"]:
            texts = " ".join(
                str(by_id[m].get("text") or "") for m in cluster["member_ids"] if m in by_id)
            if "I rebuilt the whole queue in Excel" in texts:
                return cluster["cluster_id"]
        raise AssertionError("no cluster carries the reddit workaround text")

    def members(self, cluster_id: str) -> list[dict]:
        clusters = pain_stages.read_json(self.directory / "clusters.json")
        cluster = next(c for c in clusters["clusters"] if c["cluster_id"] == cluster_id)
        wanted = set(cluster["member_ids"])
        records, _ = pain_stages.evidence_records(TEST_SLUG)
        seen: dict[str, dict] = {}
        for record in records:
            if record["id"] in wanted:
                seen.setdefault(record["author"], record)
        return list(seen.values())

    def first_member_url(self, cluster_id: str) -> str:
        return self.members(cluster_id)[0]["url"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
