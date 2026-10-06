#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["requests"]
# ///
"""Key-free Reddit capture for BIG subreddits: sweep by time, then match locally.

WHY THIS EXISTS
---------------
reddit_search.py asks the Arctic Shift archive to run a full-text `query` inside
a subreddit. On a large subreddit that server-side search is slow and answers
`422 Timeout. Maybe slow down a bit` most of the time; on the whiskey run 4 of
44 captures finished in 25 minutes, two of them empty, and the pullpush
fallback 429'd at once. Even when it works it returns the newest 100 posts, so a
three-year frame on a busy subreddit only ever sees its last few weeks.

What did work, from the same IP at the same time: listing a subreddit's posts by
time window with NO query. It swept 112k posts across 9 subreddits in a few
hours. So this script does that, once per subreddit per run, caches the posts,
and does the query matching itself:

  1. SWEEP   page /api/posts/search by subreddit + after/before, oldest first,
             into runs/<slug>/evidence/.sweep/<sub>.jsonl. Resumable: a later
             call continues from the newest post on disk. Very busy subreddits
             are swept on a declared sample (days 1-10 of every month), chosen
             by a one-call volume probe and recorded — never silent.
  2. MATCH   case-insensitive whole-word match of the query against title +
             body, per subreddit, with the same body-less rule and bot filter
             reddit_search.py applies.
  3. CAP     at most 100 posts per (query, subreddit), taken EVENLY ACROSS TIME
             rather than newest-first, so the sample represents the window.
  4. COMMENTS for the 10 most-commented matched posts, the 10 top-scored
             comments from each thread, via /api/comments/tree.
  5. WRITE   CONTRACTS evidence records built by reddit_search.py's own
             post_to_evidence / comment_to_evidence, so ids and URLs are
             identical to the query path's. Permalinks come from the source.

The sweep cache is not evidence; only step 5's records are.

RATE LIMITS
-----------
Pacing is >=1.4 s between requests, scaled by how many captures run at once.
A "slow down" (422, or an `error` body) is retried with doubling backoff. A 429
or 403 is answered by PAUSING 60-90 s (longer if the archive's Retry-After says
so) and retrying, up to THROTTLE_RETRIES times per request — a deliberate,
owner-approved difference from reddit_search.py, which never retries either.
The sweep is thousands of requests long and stopping it on one 429 would make
big subreddits uncapturable. Every page is on disk before the next request, so
giving up after the last pause loses nothing: the next call resumes.

Each call has a time budget for sweeping. A sweep that is not finished when the
budget runs out reports `sweep-in-progress` and stages NOTHING — matching
against half a window would quietly skew the sample toward its start.

    uv run --quiet scripts/reddit_sweep.py --subreddits landscaping,NoLawns \\
        --after 2023-10-01 --before 2026-09-29 --query "french drain" \\
        --cell-id m03 --sweep-dir runs/<slug>/evidence/.sweep \\
        --out runs/<slug>/evidence/.staging/reddit-m03.jsonl
"""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reddit_search as rs  # noqa: E402

ARCTIC_POSTS = rs.ARCTIC_POSTS
ARCTIC_TREE = f"https://{rs.ARCTIC_HOST}/api/comments/tree"

#: Seconds between request starts for one sweep stream. Observed reliable at
#: 1.3-1.6 s per IP (2026-09-30); three or more parallel streams drew 429s.
SWEEP_INTERVAL = 1.4
REQUEST_TIMEOUT = 60
PAGE = 100

#: "Slow down" answers (422 / an `error` body / 5xx / network): doubling backoff.
SLOW_RETRIES = 6
SLOW_BACKOFF = 3.0
SLOW_BACKOFF_CAP = 60.0

#: 429 / 403: long pauses, then give up for this call (progress is on disk).
THROTTLE_RETRIES = 5
THROTTLE_PAUSES = (60.0, 75.0, 90.0, 90.0, 90.0)
RETRY_AFTER_CAP = 300.0

#: Seconds of sweeping per call before reporting progress and stopping.
CALL_BUDGET = 900.0

#: A window estimated to hold more posts than this is swept on a sample.
SAMPLE_ABOVE_POSTS = 60_000
#: The declared sample: these days of every month.
SAMPLE_DAYS = (1, 10)

PER_SUBREDDIT_CAP = 100
COMMENT_POSTS = 10
COMMENTS_PER_POST = 10
COMMENT_MIN = 2
MAX_TEXT_CHARS = 8000

#: Post fields kept in the sweep cache. `fields=` cannot select `_meta` (where
#: the removal type lives), so full objects are fetched and slimmed here.
KEEP = (
    "id", "created_utc", "score", "num_comments", "title", "selftext", "author",
    "link_flair_text", "url", "permalink", "subreddit", "removed_by_category",
    "over_18", "stickied",
)

sleep = time.sleep  # patched by tests


def log(msg: str) -> None:
    """Diagnostics go to stderr so stdout stays parseable."""
    print(f"[reddit_sweep] {msg}", file=sys.stderr, flush=True)


def day(ts: int | None) -> str | None:
    """Unix seconds as YYYY-MM-DD (UTC), or None."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #


def query_pattern(query: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive matcher for a cell query.

    Spaces match any run of whitespace, an apostrophe matches a straight or a
    curly one, and the edges are "not a word character" rather than `\\b` so a
    query that starts or ends with punctuation ("C++") still anchors.
    """
    parts = [re.escape(word) for word in query.split()]
    body = r"\s+".join(re.sub("['’]", "['’]", part) for part in parts)
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


def removal_of(post: dict) -> str | None:
    """Why the post is gone, if it is: the archive's removal type, else Reddit's."""
    return post.get("removal_type") or post.get("removed_by_category") or None


def slim(post: dict) -> dict:
    """The cache row: KEEP fields plus the removal type from `_meta`."""
    row = {k: post.get(k) for k in KEEP}
    meta = post.get("_meta") or {}
    row["removal_type"] = meta.get("removal_type")
    return row


def stratified_cap(posts: list[dict], cap: int = PER_SUBREDDIT_CAP) -> list[dict]:
    """At most `cap` posts, evenly spaced across time (oldest to newest).

    The query path's cap is the newest N, which on a busy subreddit covers a
    few weeks of a three-year frame. Stride sampling over the time-sorted
    matches keeps the same count but represents the whole window.
    """
    ordered = sorted(posts, key=lambda p: p.get("created_utc") or 0)
    if len(ordered) <= cap:
        return ordered
    step = len(ordered) / cap
    return [ordered[int(i * step)] for i in range(cap)]


def window_segments(after: int, before: int, sample: str | None) -> list[tuple[int, int]]:
    """The time ranges to sweep: the whole window, or each month's sample days."""
    if sample is None:
        return [(after, before)]
    first, last = SAMPLE_DAYS
    start = datetime.fromtimestamp(after, tz=timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0)
    segments = []
    while int(start.timestamp()) < before:
        seg_start = int(start.replace(day=first).timestamp())
        seg_end = int(start.replace(day=last).timestamp()) + 86400
        lo, hi = max(seg_start, after), min(seg_end, before)
        if lo < hi:
            segments.append((lo, hi))
        start = start.replace(year=start.year + 1, month=1) if start.month == 12 \
            else start.replace(month=start.month + 1)
    return segments


def sample_label(sample: str | None) -> str:
    """Human wording for a sample declaration."""
    if sample is None:
        return "full window"
    return f"sampled: days {SAMPLE_DAYS[0]}-{SAMPLE_DAYS[1]} of every month"


def flatten_tree(nodes: Any) -> list[dict]:
    """Every comment in an Arctic Shift /comments/tree answer, depth-first.

    Nodes arrive as {"kind": "t1", "data": {...}}; `replies` is "" or a
    Listing ({"data": {"children": [...]}}) or, defensively, a plain list.
    "more" stubs carry no comment and are skipped.
    """
    out: list[dict] = []
    stack = list(nodes or []) if isinstance(nodes, list) else []
    while stack:
        node = stack.pop(0)
        if not isinstance(node, dict):
            continue
        if node.get("kind") not in (None, "t1"):
            continue
        data = node.get("data") if "data" in node and "kind" in node else node
        if not isinstance(data, dict):
            continue
        out.append(data)
        replies = data.get("replies")
        if isinstance(replies, dict):
            stack.extend((replies.get("data") or {}).get("children") or [])
        elif isinstance(replies, list):
            stack.extend(replies)
    return out


def read_cache(path: Path) -> tuple[list[dict], int]:
    """Cached posts (deduped by id) and how many malformed lines were skipped.

    A sweep killed mid-write leaves a truncated last line; it is skipped and
    counted, and the next page simply re-fetches that post.
    """
    posts: dict[str, dict] = {}
    bad = 0
    if not path.exists():
        return [], 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if isinstance(row, dict) and row.get("id"):
                posts.setdefault(row["id"], row)
    return list(posts.values()), bad


# --------------------------------------------------------------------------- #
# the archive client: pacing and the retry policy
# --------------------------------------------------------------------------- #


def make_session() -> Any:
    """The HTTP session (replaced by a fake in tests)."""
    session = requests.Session()
    session.headers.update({"User-Agent": rs.USER_AGENT, "Accept": "application/json"})
    return session


def throttle_pause(retry_after: str | None, attempt: int) -> float:
    """Seconds to pause after the attempt-th 429/403: the archive's own
    Retry-After when it gives one (capped), else the 60-90 s ladder."""
    ladder = THROTTLE_PAUSES[min(attempt, len(THROTTLE_PAUSES) - 1)]
    try:
        asked = float(retry_after) if retry_after else 0.0
    except ValueError:
        asked = 0.0
    return min(max(ladder, asked), RETRY_AFTER_CAP)


class ArchiveClient:
    """GETs against Arctic Shift with pacing, backoff, and long throttle pauses."""

    def __init__(self, politeness: float = 1.0) -> None:
        self.session = make_session()
        self.interval = SWEEP_INTERVAL * max(1.0, politeness)
        self.last = 0.0
        self.requests = 0
        self.throttle_pauses = 0
        self.slow_retries = 0

    def _pace(self) -> None:
        wait = self.interval - (time.monotonic() - self.last)
        if wait > 0:
            sleep(wait)
        self.last = time.monotonic()

    def get(self, url: str, params: dict) -> tuple[Any, str | None]:
        """Return (data, None) on success or (None, why) after retries run out."""
        throttled = slowed = 0
        backoff = SLOW_BACKOFF
        while True:
            self._pace()
            self.requests += 1
            try:
                resp = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                why = f"network error: {type(exc).__name__}"
            else:
                if resp.status_code in (403, 429):
                    if throttled >= THROTTLE_RETRIES:
                        return None, (f"HTTP {resp.status_code} after {throttled} long pauses; "
                                      "progress so far is saved")
                    pause = throttle_pause(resp.headers.get("Retry-After"), throttled)
                    throttled += 1
                    self.throttle_pauses += 1
                    log(f"HTTP {resp.status_code} from {urlparse(url).netloc}; "
                        f"pausing {pause:.0f}s before retrying ({throttled}/{THROTTLE_RETRIES})")
                    sleep(pause)
                    continue
                why = self._slow_reason(resp)
                if why is None:
                    try:
                        return resp.json().get("data"), None
                    except (ValueError, AttributeError):
                        why = "response was not JSON"
                elif why.startswith("final:"):
                    return None, why.removeprefix("final:")
            if slowed >= SLOW_RETRIES:
                return None, f"{why} (after {slowed} retries)"
            slowed += 1
            self.slow_retries += 1
            log(f"{why}; backing off {backoff:.0f}s")
            sleep(backoff)
            backoff = min(backoff * 2, SLOW_BACKOFF_CAP)

    @staticmethod
    def _slow_reason(resp: Any) -> str | None:
        """None if the answer is usable; a reason to back off; or `final:` + why."""
        if resp.status_code >= 500 or resp.status_code == 422:
            return f"HTTP {resp.status_code}"
        if resp.status_code >= 400:
            return f"final:HTTP {resp.status_code}"
        try:
            body = resp.json()
        except ValueError:
            return "response was not JSON"
        if isinstance(body, dict) and body.get("error"):
            return f"archive error: {body['error']}"
        return None


# --------------------------------------------------------------------------- #
# sweep
# --------------------------------------------------------------------------- #


@dataclass
class SweepState:
    """One subreddit's sweep: where it stands after this call."""

    sub: str
    sample: str | None = None
    rate_per_day: float | None = None
    complete: bool = False
    progress: float = 0.0
    error: str | None = None
    fetched_now: int = 0
    skipped_lines: int = 0
    note: list[str] = field(default_factory=list)


def probe_rate(client: ArchiveClient, sub: str, before: int) -> tuple[float | None, str | None]:
    """Posts per day near the end of the window, from one request.

    Asks for the newest 100 posts in the 7 days before `before`. Fewer than
    100 is an exact count; a full page measures how long 100 posts took.
    """
    data, why = client.get(ARCTIC_POSTS, {
        "subreddit": sub, "after": before - 7 * 86400, "before": before,
        "limit": PAGE, "sort": "desc", "fields": "id,created_utc",
    })
    if data is None:
        return None, why
    if len(data) < PAGE:
        return len(data) / 7.0, None
    oldest = min(int(p["created_utc"]) for p in data)
    return PAGE / max((before - oldest) / 86400, 1 / 24), None


def sweep_subreddit(
    client: ArchiveClient, sub: str, after: int, before: int, cache_dir: Path, deadline: float
) -> SweepState:
    """Bring one subreddit's cache up to the whole window, or as far as time allows."""
    key = sub.lower()
    data_path = cache_dir / f"{key}.jsonl"
    meta_path = cache_dir / f"{key}.meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else None
    state = SweepState(sub=sub)

    if meta and (meta.get("after"), meta.get("before")) != (after, before):
        state.error = (f"r/{sub} was swept for a different window "
                       f"({day(meta.get('after'))}..{day(meta.get('before'))}); delete "
                       f"{data_path.name} and {meta_path.name} to re-sweep")
        return state
    if meta is None:
        rate, why = probe_rate(client, sub, before)
        if rate is None:
            state.error = f"volume probe failed: {why}"
            return state
        estimate = rate * (before - after) / 86400
        meta = {"sub": sub, "after": after, "before": before,
                "rate_per_day": round(rate, 1),
                "sample": "days:1-10" if estimate > SAMPLE_ABOVE_POSTS else None,
                "complete": False}
        meta_path.write_text(json.dumps(meta) + "\n")
    state.sample, state.rate_per_day = meta["sample"], meta["rate_per_day"]
    if meta.get("complete"):
        state.complete, state.progress = True, 1.0
        return state

    cached, state.skipped_lines = read_cache(data_path)
    seen = {p["id"] for p in cached}
    cursor = max((int(p["created_utc"]) for p in cached if p.get("created_utc")), default=after)
    segments = window_segments(after, before, meta["sample"])
    span = sum(hi - lo for lo, hi in segments) or 1

    with data_path.open("a", encoding="utf-8") as sink:
        if not _ends_with_newline(data_path):
            # A crash mid-write left a truncated last line. Without this newline
            # the first new row would be glued onto it and lost with it.
            sink.write("\n")
        for lo, hi in segments:
            if hi <= cursor:
                continue
            cur = max(lo, cursor)
            while True:
                if time.monotonic() > deadline:
                    state.progress = _progress(segments, cur, span)
                    return state
                page, why = client.get(ARCTIC_POSTS, {
                    "subreddit": sub, "after": cur, "before": hi, "limit": PAGE, "sort": "asc",
                })
                if page is None:
                    state.error = why
                    state.progress = _progress(segments, cur, span)
                    return state
                fresh = [slim(p) for p in page if p.get("id") and p["id"] not in seen]
                for row in fresh:
                    seen.add(row["id"])
                    sink.write(json.dumps(row, ensure_ascii=False) + "\n")
                sink.flush()  # on disk before the next request: nothing is lost on a stop
                state.fetched_now += len(fresh)
                if len(page) < PAGE:
                    break
                newest = max(int(p["created_utc"]) for p in page)
                cur = newest if newest > cur else cur + 1
            cursor = max(cursor, hi)

    meta["complete"] = True
    meta_path.write_text(json.dumps(meta) + "\n")
    state.complete, state.progress = True, 1.0
    return state


def _ends_with_newline(path: Path) -> bool:
    """True for an empty file or one whose last byte is a newline."""
    if not path.exists() or path.stat().st_size == 0:
        return True
    with path.open("rb") as handle:
        handle.seek(-1, 2)
        return handle.read(1) == b"\n"


def _progress(segments: Sequence[tuple[int, int]], cur: int, span: int) -> float:
    """Share of the (sampled) window swept so far, by time."""
    done = sum(min(hi, max(lo, cur)) - lo for lo, hi in segments)
    return round(done / span, 3)


class SubredditLock:
    """An exclusive, non-blocking lock on one subreddit's cache file."""

    def __init__(self, cache_dir: Path, sub: str) -> None:
        self.path = cache_dir / f"{sub.lower()}.lock"
        self.handle = None

    def __enter__(self) -> bool:
        self.handle = self.path.open("w")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def __exit__(self, *_exc: Any) -> None:
        if self.handle:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()


# --------------------------------------------------------------------------- #
# match + records
# --------------------------------------------------------------------------- #


@dataclass
class SubredditMatch:
    """What matching one query against one subreddit's cache produced."""

    sub: str
    cached: int = 0
    matched: int = 0
    removed: dict[str, int] = field(default_factory=dict)
    bot: int = 0
    bodyless: int = 0
    kept: list[dict] = field(default_factory=list)


def match_subreddit(posts: Iterable[dict], sub: str, rx: re.Pattern[str]) -> SubredditMatch:
    """Apply the query, the removal/bot/body-less filters, and the time-spread cap."""
    result = SubredditMatch(sub=sub)
    survivors = []
    for post in posts:
        result.cached += 1
        if not rx.search(f"{post.get('title') or ''}\n{post.get('selftext') or ''}"):
            continue
        result.matched += 1
        removal = removal_of(post)
        if removal:
            # Removed by moderators, by Reddit, or deleted by the author: the
            # community or the person withdrew it. Counted, never silent.
            result.removed[removal] = result.removed.get(removal, 0) + 1
            continue
        if str(post.get("author") or "").lower() in rs.BOT_AUTHORS:
            result.bot += 1
            continue
        # reddit_search.py's own rule: a body-less post stays only when its
        # title or flair carries a complaint signal.
        if not rs.normalise_text(post.get("selftext"), MAX_TEXT_CHARS) \
                and not rs.has_complaint_signal(post):
            result.bodyless += 1
            continue
        survivors.append(post)
    result.kept = stratified_cap(survivors)
    return result


def comment_records(
    client: ArchiveClient, posts: list[dict], cell_id: str, query: str, captured: int
) -> tuple[list[dict], list[str]]:
    """Top comments from the most-commented matched posts, as evidence records."""
    targets = sorted(posts, key=lambda p: -(p.get("num_comments") or 0))[:COMMENT_POSTS]
    records, failures = [], []
    for post in targets:
        if (post.get("num_comments") or 0) < COMMENT_MIN:
            continue
        tree, why = client.get(ARCTIC_TREE, {"link_id": f"t3_{post['id']}", "limit": 500})
        if tree is None:
            failures.append(f"t3_{post['id']}: {why}")
            continue
        comments = [c for c in flatten_tree(tree)
                    if str(c.get("author") or "").lower() not in rs.BOT_AUTHORS]
        comments.sort(key=lambda c: -(c.get("score") if isinstance(c.get("score"), int) else 0))
        for comment in comments[:COMMENTS_PER_POST]:
            record = rs.comment_to_evidence(comment, cell_id=cell_id, query=query,
                                            captured_utc=captured, max_text_chars=MAX_TEXT_CHARS)
            if record:
                records.append(record)
    return records, failures


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """CLI definition."""
    parser = argparse.ArgumentParser(
        prog="reddit_sweep.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Capture Reddit evidence from busy subreddits with zero credentials: sweep\n"
            "each subreddit's posts by time window (cached, resumable), match the query\n"
            "locally, keep <=100 posts per subreddit spread evenly across the window,\n"
            "and add the top comments. Writes CONTRACTS section 2 JSONL."
        ),
    )
    parser.add_argument("--subreddits", required=True, metavar="a,b,c")
    parser.add_argument("--query", required=True)
    parser.add_argument("--after", required=True, metavar="WHEN", help="window start (ISO date or unix)")
    parser.add_argument("--before", required=True, metavar="WHEN", help="window end (ISO date or unix)")
    parser.add_argument("--cell-id", required=True)
    parser.add_argument("--sweep-dir", required=True, help="the per-run sweep cache (not evidence)")
    parser.add_argument("--out", required=True, help="evidence JSONL to append to")
    parser.add_argument("--politeness", type=float, default=1.0,
                        help="multiply the request interval by X (captures running at once)")
    parser.add_argument("--budget", type=float, default=CALL_BUDGET,
                        help=f"seconds of sweeping before reporting progress (default {CALL_BUDGET:g})")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Sweep, match, write; print one JSON summary on stdout — always, even on a crash."""
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except Exception as exc:  # stdout must stay parseable JSON for the caller
        log(f"unexpected failure: {type(exc).__name__}: {exc}")
        return _emit({
            "script": "reddit_sweep.py", "source": "reddit", "outcome": "unavailable",
            "error": f"{type(exc).__name__}: {exc}",
            "source_health": [{
                "source": "reddit:arctic-shift", "status": "unavailable", "fallback": None,
                "detail": f"{args.cell_id} {args.query!r}: sweep capture crashed "
                          f"({type(exc).__name__}: {exc}); anything already swept is saved",
            }],
        })


def _run(args: argparse.Namespace) -> int:
    """The body of main(), separated so a crash still prints JSON."""
    after = rs.parse_when(args.after, "after")
    before = rs.parse_when(args.before, "before")
    subs = rs.split_subreddits([args.subreddits])
    cache_dir = Path(args.sweep_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    client = ArchiveClient(args.politeness)
    deadline = time.monotonic() + args.budget
    window = f"{day(after)}..{day(before)}"

    states: list[SweepState] = []
    for sub in subs:
        with SubredditLock(cache_dir, sub) as locked:
            if not locked:
                states.append(SweepState(sub=sub, error=None, note=["being swept by another call"]))
                continue
            states.append(sweep_subreddit(client, sub, after, before, cache_dir, deadline))

    summary: dict[str, Any] = {
        "script": "reddit_sweep.py", "source": "reddit", "cell_id": args.cell_id,
        "query": args.query, "window": {"after": after, "before": before},
        "per_subreddit": [_state_row(s) for s in states],
        "http": {"requests": client.requests, "throttle_pauses": client.throttle_pauses,
                 "slow_down_retries": client.slow_retries},
    }
    failed = [s for s in states if s.error]
    pending = [s for s in states if not s.complete and not s.error]
    if failed:
        summary.update(outcome="unavailable", source_health=[{
            "source": "reddit:arctic-shift", "status": "unavailable", "fallback": None,
            "detail": f"{args.cell_id} {args.query!r}: sweep stopped — "
                      + "; ".join(f"r/{s.sub}: {s.error} ({s.progress:.0%} of window swept, saved)"
                                  for s in failed)
                      + ". Calling again resumes from what is saved.",
        }])
        return _emit(summary)
    if pending:
        summary.update(outcome="sweep-in-progress", source_health=[])
        return _emit(summary)

    summary.update(_match_and_write(client, subs, states, args, window))
    return _emit(summary)


def _state_row(state: SweepState) -> dict:
    """One subreddit's sweep status for the summary."""
    return {"subreddit": f"r/{state.sub}", "complete": state.complete,
            "progress": state.progress, "sample": state.sample,
            "rate_per_day": state.rate_per_day, "fetched_this_call": state.fetched_now,
            "malformed_cache_lines": state.skipped_lines, "error": state.error,
            "note": "; ".join(state.note) or None}


def _match_and_write(client: ArchiveClient, subs: list[str], states: list[SweepState],
                     args: argparse.Namespace, window: str) -> dict:
    """Steps 2-5: match, cap, comments, write. Returns summary fields."""
    rx = query_pattern(args.query)
    captured = int(time.time())
    matches = [match_subreddit(read_cache(Path(args.sweep_dir) / f"{s.lower()}.jsonl")[0], s, rx)
               for s in subs]
    posts = [p for m in matches for p in m.kept]
    records = [r for p in posts if (r := rs.post_to_evidence(
        p, cell_id=args.cell_id, query=args.query, captured_utc=captured,
        max_text_chars=MAX_TEXT_CHARS))]
    comments, comment_failures = comment_records(client, posts, args.cell_id, args.query, captured)

    sink = rs.JsonlSink(args.out)
    written = sum(sink.write(r) for r in records + comments)
    sink.close()

    dates = [int(p["created_utc"]) for p in posts if p.get("created_utc")]
    removed: dict[str, int] = {}
    for m in matches:
        for kind, n in m.removed.items():
            removed[kind] = removed.get(kind, 0) + n
    samples = [f"r/{s.sub} {sample_label(s.sample)}" for s in states if s.sample]
    detail = (
        f"{args.cell_id} {args.query!r}: matched locally over a {window} sweep"
        + (f" ({'; '.join(samples)})" if samples else "")
        + f"; {len(posts)} post{'' if len(posts) == 1 else 's'} (at most {PER_SUBREDDIT_CAP} per subreddit, spread evenly "
          f"across the window), {len(records) + len(comments)} records including comments"
        + (f"; posts dated {day(min(dates))}..{day(max(dates))}" if dates else "")
        + (f"; excluded {sum(removed.values())} removed or deleted posts {removed}" if removed else "")
        + (f"; {len(comment_failures)} comment threads unreadable" if comment_failures else "")
    )
    return {
        "outcome": "staged",
        "captured_utc": captured,
        "per_query": [{"subreddit": f"r/{m.sub}", "cached_posts": m.cached, "matched": m.matched,
                       "removed_excluded": m.removed, "bot_posts": m.bot,
                       "bodyless_dropped": m.bodyless, "kept": len(m.kept)} for m in matches],
        "totals": {"post_records": len(records), "comment_records": len(comments),
                   "items_written": written, "deduped_against_out": sink.duplicates},
        "date_span": {"earliest_utc": min(dates) if dates else None,
                      "latest_utc": max(dates) if dates else None},
        "comment_failures": comment_failures,
        "source_health": [{
            "source": "reddit:arctic-shift",
            "status": "ok" if posts else "searched-no-results",
            "fallback": None, "detail": detail, "mode": "sweep",
            "date_span": {"earliest_utc": min(dates) if dates else None,
                          "latest_utc": max(dates) if dates else None},
        }],
    }


def _emit(summary: dict) -> int:
    """Print the summary; exit 1 only when the archive could not be read."""
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 1 if summary.get("outcome") == "unavailable" else 0


if __name__ == "__main__":
    sys.exit(main())
