#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["requests"]
# ///
"""Tests for reddit_sweep.py, the big-subreddit capture. No live calls.

The sweep replaces the archive's full-text search (which times out on busy
subreddits) with a cached time-window listing plus local matching. What must
hold, and is checked here against an in-memory fake of the archive:

  * matching: whole words, either apostrophe, multi-word phrases, case-blind;
  * the same body-less rule and bot filter as reddit_search.py;
  * removed or deleted posts are excluded AND counted;
  * the 100-per-subreddit cap is spread across the window, not newest-first;
  * very busy subreddits are swept on a declared days-1-10 sample;
  * a "slow down" is retried with doubling backoff; a 429 is answered with a
    LONG pause (>= 60 s, or the archive's Retry-After) and never a tight retry;
  * every page is on disk before the next request, so an interrupted sweep
    resumes without losing or duplicating posts, even past a truncated line;
  * an unfinished sweep stages nothing;
  * records round-trip through pain_merge_staging with the contract id recipe.

`sleep` is stubbed, so this runs in seconds and asserts the waits it WOULD take.

    uv run --quiet tests/test_reddit_sweep.py
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import pain_stages  # noqa: E402
import reddit_sweep as sweep  # noqa: E402

DAY = 86400
T0 = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
T1 = T0 + 365 * DAY


def evidence_id(source: str, url: str) -> str:
    """The CONTRACTS id recipe: sha1 of source plus url, no separator."""
    return hashlib.sha1(f"{source}{url}".encode()).hexdigest()


def post(pid: str, sub: str, created: int, title: str, body: str = "a long body", **extra) -> dict:
    """An archive post object with the fields the sweep reads."""
    row = {"id": pid, "subreddit": sub, "created_utc": created, "title": title,
           "selftext": body, "author": f"user_{pid}", "score": 5, "num_comments": 3,
           "permalink": f"/r/{sub}/comments/{pid}/slug/", "url": "https://example.com"}
    row.update(extra)
    return row


class FakeResponse:
    """Just enough of requests.Response."""

    def __init__(self, status: int, body: dict | None = None, headers: dict | None = None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}

    def json(self) -> dict:
        return self._body


class FakeArchive:
    """In-memory Arctic Shift: posts/search (after/before exclusive) and comments/tree.

    `script` is a queue of canned responses served before the real ones, to
    inject 422s and 429s. `fail_after` makes every request past that count
    answer 429, to interrupt a sweep mid-way.
    """

    def __init__(self, posts: list[dict], script: list[FakeResponse] | None = None,
                 fail_after: int | None = None) -> None:
        self.posts = posts
        self.script = list(script or [])
        self.fail_after = fail_after
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, params: dict, timeout: float = 0) -> FakeResponse:
        self.calls.append((url, dict(params)))
        if self.script:
            return self.script.pop(0)
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            return FakeResponse(429)
        if url.endswith("/comments/tree"):
            return FakeResponse(200, {"data": self.tree(params["link_id"])})
        sub = params["subreddit"].lower()
        rows = [p for p in self.posts if p["subreddit"].lower() == sub
                and params["after"] < p["created_utc"] < params["before"]]
        rows.sort(key=lambda p: p["created_utc"], reverse=params.get("sort") == "desc")
        rows = rows[: params["limit"]]
        if params.get("fields"):
            keep = params["fields"].split(",")
            rows = [{k: p[k] for k in keep} for p in rows]
        else:
            rows = [{**p, "_meta": {"removal_type": p.get("_removal")}} for p in rows]
        return FakeResponse(200, {"data": rows})

    @staticmethod
    def tree(link_id: str) -> list[dict]:
        pid = link_id.removeprefix("t3_")

        def comment(cid: str, score: int, replies=""):
            return {"kind": "t1", "data": {
                "id": cid, "body": f"comment {cid} on {pid}", "score": score,
                "author": "AutoModerator" if cid.endswith("bot") else f"c_{cid}",
                "subreddit": "landscaping", "created_utc": T0 + DAY,
                "permalink": f"/r/landscaping/comments/{pid}/slug/{cid}/", "replies": replies}}

        nested = {"kind": "Listing", "data": {"children": [comment(f"{pid}r", 50)]}}
        return [comment(f"{pid}a", 10, nested), comment(f"{pid}bot", 99),
                {"kind": "more", "data": {"children": ["x"]}}]


class SweepTestCase(unittest.TestCase):
    """Shared plumbing: a temp run dir, a stubbed sleep, and a fake archive."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sweep_dir = self.root / "sweep"
        self.out = self.root / "out.jsonl"
        self.sleeps: list[float] = []
        self._sleep, self._session = sweep.sleep, sweep.make_session
        sweep.sleep = self.sleeps.append

    def tearDown(self) -> None:
        sweep.sleep, sweep.make_session = self._sleep, self._session
        self.tmp.cleanup()

    def use(self, archive: FakeArchive) -> FakeArchive:
        sweep.make_session = lambda: archive
        return archive

    def run_main(self, query: str, subs: str = "landscaping", budget: float = 900,
                 out: Path | None = None) -> dict:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            sweep.main(["--subreddits", subs, "--query", query, "--after", str(T0),
                        "--before", str(T1), "--cell-id", "m01",
                        "--sweep-dir", str(self.sweep_dir), "--out", str(out or self.out),
                        "--budget", str(budget)])
        return json.loads(buffer.getvalue())


class Matching(unittest.TestCase):
    def test_whole_words_case_and_phrases(self) -> None:
        rx = sweep.query_pattern("french drain")
        self.assertTrue(rx.search("My FRENCH\n  drain backs up"))
        self.assertFalse(rx.search("frenchdrain"))
        hoa = sweep.query_pattern("HOA")
        self.assertTrue(hoa.search("the hoa's letter"))
        self.assertFalse(hoa.search("whoa there"))

    def test_either_apostrophe(self) -> None:
        rx = sweep.query_pattern("can't find")
        self.assertTrue(rx.search("I can’t find natives"))
        self.assertTrue(rx.search("I can't find natives"))

    def test_query_with_punctuation_at_the_edge(self) -> None:
        self.assertTrue(sweep.query_pattern("C++").search("learning C++ now"))

    def test_bodyless_rule_bots_and_removals(self) -> None:
        rx = sweep.query_pattern("pavers")
        posts = [
            post("a", "s", T0 + 1, "Pavers", body=""),               # no body, no signal: dropped
            post("b", "s", T0 + 2, "Pavers help?", body=""),         # no body but a question: kept
            post("c", "s", T0 + 3, "pavers", author="AutoModerator"),  # bot: dropped
            post("d", "s", T0 + 4, "pavers", removal_type="moderator"),  # removed: counted
            post("e", "s", T0 + 5, "pavers", body="[deleted]", removal_type="deleted"),
            post("f", "s", T0 + 6, "lawn"),                          # no match
        ]
        result = sweep.match_subreddit(posts, "s", rx)
        self.assertEqual([p["id"] for p in result.kept], ["b"])
        self.assertEqual(result.matched, 5)
        self.assertEqual(result.bodyless, 1)
        self.assertEqual(result.bot, 1)
        self.assertEqual(result.removed, {"moderator": 1, "deleted": 1})


class TimeSpread(unittest.TestCase):
    def test_cap_is_exactly_100_and_spans_the_window(self) -> None:
        posts = [post(str(i), "s", T0 + i * 3600, "x") for i in range(1000)]
        kept = sweep.stratified_cap(list(reversed(posts)))
        self.assertEqual(len(kept), 100)
        dates = [p["created_utc"] for p in kept]
        window = posts[-1]["created_utc"] - posts[0]["created_utc"]
        self.assertGreaterEqual(max(dates) - min(dates), 0.8 * window)
        self.assertEqual(dates, sorted(dates))

    def test_sample_segments_are_days_1_to_10_of_each_month(self) -> None:
        after = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp())
        before = int(datetime(2024, 4, 5, tzinfo=timezone.utc).timestamp())
        segments = sweep.window_segments(after, before, "days:1-10")
        as_days = [(datetime.fromtimestamp(a, timezone.utc).strftime("%m-%d"),
                    datetime.fromtimestamp(b, timezone.utc).strftime("%m-%d")) for a, b in segments]
        self.assertEqual(as_days, [("02-01", "02-11"), ("03-01", "03-11"), ("04-01", "04-05")])
        self.assertEqual(sweep.window_segments(after, before, None), [(after, before)])

    def test_tree_flattening_skips_more_stubs(self) -> None:
        flat = sweep.flatten_tree(FakeArchive.tree("t3_p1"))
        self.assertEqual(sorted(c["id"] for c in flat), ["p1a", "p1bot", "p1r"])


class RetryPolicy(SweepTestCase):
    def test_slow_down_backs_off_and_succeeds(self) -> None:
        self.use(FakeArchive([], script=[FakeResponse(422), FakeResponse(200, {"error": "Timeout"})]))
        client = sweep.ArchiveClient()
        data, why = client.get(sweep.ARCTIC_POSTS, {"subreddit": "x", "after": 0, "before": 1,
                                                    "limit": 100})
        self.assertEqual((data, why), ([], None))
        backoffs = [s for s in self.sleeps if s >= sweep.SLOW_BACKOFF]
        self.assertEqual(backoffs, [3.0, 6.0])

    def test_429_pauses_long_and_never_tight(self) -> None:
        self.use(FakeArchive([], script=[FakeResponse(429), FakeResponse(429, headers={"Retry-After": "120"})]))
        client = sweep.ArchiveClient()
        data, _ = client.get(sweep.ARCTIC_POSTS, {"subreddit": "x", "after": 0, "before": 1, "limit": 100})
        self.assertEqual(data, [])
        pauses = [s for s in self.sleeps if s >= 60]
        self.assertEqual(pauses, [60.0, 120.0])
        self.assertEqual(client.throttle_pauses, 2)

    def test_gives_up_after_the_last_pause(self) -> None:
        self.use(FakeArchive([], script=[FakeResponse(403)] * 10))
        data, why = sweep.ArchiveClient().get(sweep.ARCTIC_POSTS, {})
        self.assertIsNone(data)
        self.assertIn("progress so far is saved", why)
        self.assertEqual(len([s for s in self.sleeps if s >= 60]), sweep.THROTTLE_RETRIES)

    def test_a_plain_400_is_not_retried(self) -> None:
        self.use(FakeArchive([], script=[FakeResponse(400)]))
        data, why = sweep.ArchiveClient().get(sweep.ARCTIC_POSTS, {})
        self.assertEqual((data, why), (None, "HTTP 400"))


def landscaping(n: int = 250) -> list[dict]:
    """n posts spread over the year; even ids mention a french drain."""
    rows = []
    for i in range(n):
        title = "French drain help?" if i % 2 == 0 else "Show off my yard"
        rows.append(post(f"p{i}", "landscaping", T0 + 10 + i * (360 * DAY // n), title))
    rows[0]["_removal"] = "moderator"
    return rows


class EndToEnd(SweepTestCase):
    def test_capture_writes_contract_records_spread_across_the_year(self) -> None:
        archive = self.use(FakeArchive(landscaping()))
        summary = self.run_main("french drain")
        self.assertEqual(summary["outcome"], "staged", summary)
        records = [json.loads(line) for line in self.out.read_text().splitlines()]
        posts = [r for r in records if r["title"]]
        comments = [r for r in records if not r["title"]]
        self.assertEqual(len(posts), 100)  # 124 eligible matches, capped
        for record in records:
            self.assertEqual(record["id"], evidence_id("reddit", record["url"]))
            self.assertEqual(record["query"], "french drain")
            self.assertEqual(record["cell_id"], "m01")
        self.assertEqual(len(comments), 20)  # 10 threads x 2 human comments; the bot is dropped
        span = summary["date_span"]
        self.assertGreater(span["latest_utc"] - span["earliest_utc"], 0.8 * 360 * DAY)
        row = summary["per_query"][0]
        self.assertEqual(row["removed_excluded"], {"moderator": 1})
        detail = summary["source_health"][0]["detail"]
        self.assertIn("at most 100 per subreddit, spread evenly", detail)
        self.assertIn("removed or deleted", detail)
        # A second query re-uses the cache: no more listing pages are fetched.
        listing_calls = sum(1 for url, p in archive.calls if url == sweep.ARCTIC_POSTS)
        self.run_main("yard")
        self.assertEqual(listing_calls, sum(1 for url, p in archive.calls if url == sweep.ARCTIC_POSTS))

    def test_interrupted_sweep_resumes_without_loss_or_duplicates(self) -> None:
        posts = landscaping(450)
        self.use(FakeArchive(posts, fail_after=3))  # probe + 2 pages, then 429 forever
        first = self.run_main("french drain")
        self.assertEqual(first["outcome"], "unavailable")
        self.assertFalse(self.out.exists() and self.out.read_text().strip(),
                         "an interrupted sweep must stage nothing")
        cache = self.sweep_dir / "landscaping.jsonl"
        self.assertEqual(len(cache.read_text().splitlines()), 200)
        # A crash mid-write leaves a truncated last line; resume must tolerate it.
        with cache.open("a") as handle:
            handle.write('{"id": "p999", "created_ut')
        self.use(FakeArchive(posts))
        second = self.run_main("french drain")
        self.assertEqual(second["outcome"], "staged")
        cached, bad = sweep.read_cache(cache)
        self.assertEqual(bad, 1)
        self.assertEqual(len(cached), 450)
        self.assertEqual(len({p["id"] for p in cached}), 450)

    def test_unfinished_sweep_stages_nothing(self) -> None:
        self.use(FakeArchive(landscaping()))
        summary = self.run_main("french drain", budget=0)
        self.assertEqual(summary["outcome"], "sweep-in-progress")
        self.assertEqual(summary["source_health"], [])
        self.assertFalse(self.out.exists() and self.out.read_text().strip())

    def test_busy_subreddit_is_sampled_and_says_so(self) -> None:
        # 100 posts in the last 6 hours => ~400/day => ~146k a year: sampled.
        busy = [post(f"b{i}", "landscaping", T1 - 1 - i * 216, "french drain help?") for i in range(100)]
        self.use(FakeArchive(busy))
        summary = self.run_main("french drain")
        self.assertEqual(summary["per_subreddit"][0]["sample"], "days:1-10")
        self.assertIn("days 1-10 of every month", summary["source_health"][0]["detail"])

    def test_another_call_holding_the_lock_means_in_progress(self) -> None:
        self.use(FakeArchive(landscaping()))
        self.sweep_dir.mkdir(parents=True)
        with sweep.SubredditLock(self.sweep_dir, "landscaping") as locked:
            self.assertTrue(locked)
            summary = self.run_main("french drain")
        self.assertEqual(summary["outcome"], "sweep-in-progress")
        self.assertIn("another call", summary["per_subreddit"][0]["note"])

    def test_records_round_trip_through_merge_staging(self) -> None:
        os.environ["PROSPECTOR_RUNS_ROOT"] = str(self.root)
        try:
            slug = "sweep-selftest-1970-01-01"
            staging = pain_stages.run_dir(slug) / "evidence" / ".staging"
            staging.mkdir(parents=True)
            self.use(FakeArchive(landscaping()))
            self.run_main("french drain", out=staging / "reddit-m01.jsonl")
            staged = [json.loads(x) for x in (staging / "reddit-m01.jsonl").read_text().splitlines()]
            result = pain_stages.merge_staging(slug)
            self.assertEqual(result["evidence_files"], {"reddit": len(staged)})
            merged, _ = pain_stages.read_jsonl(pain_stages.run_dir(slug) / "evidence" / "reddit.jsonl")
            self.assertEqual({r["id"] for r in merged}, {r["id"] for r in staged})
        finally:
            os.environ.pop("PROSPECTOR_RUNS_ROOT", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
