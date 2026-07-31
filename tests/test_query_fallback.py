#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["requests"]
# ///
"""Regression test for the dropped-query fallback in reddit_search.py.

The bug this guards against was found during acceptance testing and is the
plugin's central failure mode in miniature. When Arctic Shift fails,
reddit_search.py falls back to pullpush, which has no equivalent full-text
match, so the query is dropped and a plain subreddit listing comes back. Two
things went wrong with that:

  1. Every returned item was still stamped `query: "permit"`, though "permit"
     had surfaced none of them. CONTRACTS §2 defines `query` as the exact
     string that surfaced the item, so this was fabricated provenance — and
     unlike the mode change in the summary, it travelled with the data into
     clustering and onto the cards.
  2. Forty unrelated posts were written into a permit-framed matrix cell.
     They are real posts, but they are not evidence of the pain being
     researched, and clustering cannot tell the difference. That manufactures
     signal, which is the mirror image of reporting a failure as "no results".

Both paths are checked here against a stubbed HTTP layer, because reproducing
a live Arctic Shift outage on demand is not possible.

    uv run tests/test_query_fallback.py
"""

import importlib.util
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "reddit_search", REPO / "scripts" / "reddit_search.py"
)
rs = importlib.util.module_from_spec(spec)
sys.modules["reddit_search"] = rs
spec.loader.exec_module(rs)

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))
        FAILURES.append(name)


# A pullpush-shaped post. Deliberately says nothing about permits: it stands in
# for the off-topic listing the fallback returns.
FAKE_POST = {
    "id": "abc123",
    "title": "How did you figure out sourcing when starting your business",
    "selftext": "Looking for advice on suppliers.",
    "author": "someone",
    "subreddit": "smallbusiness",
    "score": 12,
    "num_comments": 4,
    "created_utc": 1731000000,
    "permalink": "/r/smallbusiness/comments/abc123/how_did_you_figure_out_sourcing/",
}


def run(argv: list[str]) -> dict:
    """Run main() with Arctic Shift forced to fail and pullpush stubbed."""

    def dead_arctic(fetcher, subreddit, *, query=None, limit=100, after=None,
                    before=None, report=None):
        # Mirror a circuit-broken host: no items, status unavailable. This is
        # the precondition the fallback branch keys on.
        if report is not None:
            report.status = "unavailable"
            report.detail = "stubbed arctic-shift outage"
        return []

    def live_pullpush(fetcher, subreddit, *, limit=100, after=None, before=None,
                      report=None):
        if report is not None:
            report.status = "degraded"
            report.detail = "stubbed pullpush listing"
        return [dict(FAKE_POST)]

    orig_arctic, orig_pp = rs.fetch_arctic_posts, rs.fetch_pullpush_posts
    orig_broken = rs.Fetcher.is_broken
    rs.fetch_arctic_posts = dead_arctic
    rs.fetch_pullpush_posts = live_pullpush
    # Keep the pullpush host "healthy" so the fallback is actually attempted.
    rs.Fetcher.is_broken = lambda self, host: False

    import contextlib
    import io
    import json

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            sys.argv = ["reddit_search.py", *argv]
            try:
                rs.main()
            except SystemExit:
                pass
    finally:
        rs.fetch_arctic_posts, rs.fetch_pullpush_posts = orig_arctic, orig_pp
        rs.Fetcher.is_broken = orig_broken

    return json.loads(buf.getvalue())


def main() -> int:
    import json
    import tempfile

    print("dropped-query fallback:")

    # --- default: refuse to substitute an unfiltered listing ----------------
    with tempfile.NamedTemporaryFile("r", suffix=".jsonl", delete=False) as fh:
        out = fh.name
    summary = run(["--subreddits", "smallbusiness", "--query", "permit",
                   "--limit", "5", "--cell-id", "m03", "--out", out])

    written = summary["totals"]["items_written"]
    check("default writes no items", written == 0, f"wrote {written}")

    lines = [ln for ln in pathlib.Path(out).read_text().splitlines() if ln.strip()]
    check("default writes no evidence lines", not lines, f"{len(lines)} lines")

    statuses = {h["status"] for h in summary["source_health"]}
    check(
        "default reports unavailable, not empty-success",
        "unavailable" in statuses,
        f"statuses={statuses}",
    )

    mode = summary["per_subreddit"][0]["mode"]
    check("default mode names the refusal", "dropped" in mode, f"mode={mode!r}")

    # --- opt-in: keep the listing, but never claim the query found it -------
    with tempfile.NamedTemporaryFile("r", suffix=".jsonl", delete=False) as fh:
        out2 = fh.name
    summary2 = run(["--subreddits", "smallbusiness", "--query", "permit",
                    "--limit", "5", "--cell-id", "m03", "--out", out2,
                    "--allow-unfiltered-fallback"])

    written2 = summary2["totals"]["items_written"]
    check("opt-in keeps the items", written2 > 0, f"wrote {written2}")

    records = [
        json.loads(ln)
        for ln in pathlib.Path(out2).read_text().splitlines()
        if ln.strip()
    ]
    queries = {r.get("query") for r in records}
    check(
        "opt-in stamps query=null, never a fabricated match",
        queries == {None},
        f"query values={queries}",
    )

    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s): {FAILURES}")
        return 1
    print("\nall dropped-query fallback checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
