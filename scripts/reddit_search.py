#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["requests"]
# ///
"""Guaranteed key-free Reddit capture for problem-prospector.

WHY THIS EXISTS
---------------
Reddit is where operators complain in their own words, so it is the highest-value
pain source in the pipeline. But every authenticated path to it is a trap for a
plugin that promises zero credentials:

  * the hosted `dialog` MCP (reddit-research-mcp) answers 401 `invalid_token` and
    requires OAuth; self-hosting it needs Reddit API keys plus a ChromaDB proxy
    key. It is therefore an *opportunistic* primary only.
  * anonymous `www.reddit.com/*.json` returns 403 from the CDN edge.

This script is the fallback that must always work with no credentials, which
makes it load-bearing for the whole tool. It reads the Arctic Shift public
archive, and if that host circuit-breaks it drops to pullpush as a last resort.

WHERE IT FITS
-------------
Scouts capture, they never interpret (methodology 3.1). Output is CONTRACTS
section 2 evidence JSONL, appended to `runs/<slug>/evidence/reddit.jsonl`, and
consumed downstream by `cluster.py`. Judgement about what a post *means* happens
after clustering, not here.

OUTPUT CONVENTION
-----------------
  --out PATH given  ->  JSONL is appended to PATH; a JSON run summary goes to stdout.
  --out omitted     ->  JSONL goes to stdout (one object per line) and the JSON
                        run summary goes to STDERR instead, so that stdout stays
                        machine-consumable as a single stream.

KNOWN SOURCE QUIRKS (recorded, never papered over)
--------------------------------------------------
  * Arctic Shift has no global subreddit-search API and its `query` parameter
    requires `subreddit` (or `author`). So `--subreddits` is mandatory; the
    caller (or `plan-communities`) must supply or guess community names.
  * `limit` above 100 is rejected (HTTP 400 "'limit' must be between 1 and 100").
    We cap client-side at 100 per request and paginate for larger `--limit`.
    A 422 is treated the same way and retried once at limit=50.
  * `/api/comments/search?link_id=t3_<id>` returns 422 for some posts. Those
    posts are marked as having no retrievable comments; content is never
    invented to fill the gap.
  * The archive snapshots score/num_comments at ingest time, so posts younger
    than roughly two days usually read `score: 1, num_comments: 0`. That is the
    archive's value, not a fabrication, and `--min-score` will over-filter
    fresh windows. The summary emits a warning when it sees such items.
  * pullpush lags the live site by weeks and rate-limits hard (429). It is
    strictly last-resort, at a >=4s interval, and stops for the whole run on its
    first 429. No proxy rotation, no identity spoofing, no retry-probing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

import requests

USER_AGENT = "problem-prospector/0.1 (+https://github.com/mattfili/problem-prospector)"

ARCTIC_HOST = "arctic-shift.photon-reddit.com"
ARCTIC_POSTS = f"https://{ARCTIC_HOST}/api/posts/search"
ARCTIC_COMMENTS = f"https://{ARCTIC_HOST}/api/comments/search"

PULLPUSH_HOST = "api.pullpush.io"
PULLPUSH_POSTS = f"https://{PULLPUSH_HOST}/reddit/search/submission/"

# Minimum seconds between requests to a host. Arctic Shift asks for >=1.2s and
# is the workhorse; pullpush 429s aggressively so it gets a much wider spacing.
HOST_INTERVALS = {ARCTIC_HOST: 1.2, PULLPUSH_HOST: 4.0}
DEFAULT_INTERVAL = 2.0

REQUEST_TIMEOUT = 30
MAX_PER_REQUEST = 100
DEGRADED_LIMIT = 50  # what a 400/422 "limit too high" is retried at
TIMEOUT_BACKOFF = 3.0  # first pause after Arctic Shift says "slow down a bit"
# Successive limits tried after a "slow down a bit" 422, each with double the
# previous backoff. One retry was not enough in practice: the archive answers a
# heavy full-text query with a timeout regardless of our cadence, and asking for
# less is the only thing that helps. Obeying "slow down" harder is not evasion --
# 403 and 429 are still circuit-broken on sight, never retried.
TIMEOUT_LIMIT_LADDER = (50, 25, 10)
MAX_HOST_INTERVAL = 8.0  # ceiling for the adaptive slow-down

# Bodies that mean "the body is gone", not "the body says this".
EMPTY_BODY_MARKERS = {"[removed]", "[deleted]", "[removed by reddit]"}

# Posts with no body still count as evidence when the title itself carries a
# complaint signal. The list is deliberately broad: this script captures and
# does not interpret (methodology 3.1), so the filter exists only to drop
# body-less noise (link dumps, memes, "hi im new"), never to pre-judge pain.
# Interpretation is the distiller's job, downstream of clustering.
COMPLAINT_SIGNALS = (
    "abandon", "advice", "alternativ", "annoy", "anyone else", "avoid", "awful",
    "backlog", "bloat", "bottleneck", "broke", "bug", "burn", "can't", "cannot",
    "cant ", "chaos", "charge", "cheaper", "complain", "confus", "cost", "crash",
    "deprecat", "disaster", "downtime", "drown", "dumb", "duplicate", "error",
    "expensive", "fail", "fed up", "fix", "frustrat", "garbage", "hack togeth",
    "hard time", "hate", "headache", "help", "hidden fee", "horrible", "how do",
    "how to", "impossible", "inefficien", "issue", "junk", "lawsuit", "leav",
    "lock-in", "lockin", "lost", "manual", "mess", "migrat", "miss", "mistake",
    "nightmare", "no way to", "not working", "outage", "overwhelm", "pain",
    "paper", "pointless", "problem", "quit", "rant", "recommend", "refus",
    "regret", "replac", "ridiculous", "risk", "sick of", "slow", "spreadsheet",
    "struggl", "stuck", "suck", "switch", "tedious", "terrible", "tired of",
    "trash", "trouble", "unusable", "useless", "vent", "warn", "waste", "why does",
    "why is", "won't", "wont ", "workaround", "worse", "worst", "wrong",
)
COMPLAINT_FLAIRS = {
    "rant", "help", "question", "questions", "support", "discussion", "vent",
    "advice", "troubleshooting", "general discussion", "problem", "issue",
}

# AutoModerator's identical "please read the rules" reply appears on thousands of
# threads. Left in, it would cluster into a single fake pain of enormous weight,
# so it is dropped at capture. This is not interpretation: it is a bot, not a
# person reporting a problem. No other author is filtered.
BOT_AUTHORS = {"automoderator"}

# The archive's score is captured at ingest, so anything this fresh reads score=1.
FRESH_POST_SECONDS = 48 * 3600


def log(msg: str) -> None:
    """Diagnostics go to stderr so stdout stays parseable."""
    print(f"[reddit_search] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# networking
# --------------------------------------------------------------------------- #


@dataclass
class FetchResult:
    ok: bool
    status: int | None = None
    payload: Any = None
    error: str | None = None


class HostThrottle:
    """Per-host minimum spacing. Politeness is a hard requirement, not a knob.

    The interval only ever grows: when a host tells us to slow down we believe it
    for the rest of the run rather than retry-probing at the old cadence.
    """

    def __init__(self, intervals: dict[str, float], scale: float = 1.0) -> None:
        self._intervals = dict(intervals)
        self._scale = scale
        self._last: dict[str, float] = {}

    def interval(self, host: str) -> float:
        return self._intervals.get(host, DEFAULT_INTERVAL) * self._scale

    def slow_down(self, host: str, factor: float = 2.0, cap: float = MAX_HOST_INTERVAL) -> float:
        current = self._intervals.get(host, DEFAULT_INTERVAL)
        widened = min(current * factor, cap)
        self._intervals[host] = widened
        return widened * self._scale

    def wait(self, host: str) -> None:
        previous = self._last.get(host)
        if previous is not None:
            remaining = self.interval(host) - (time.monotonic() - previous)
            if remaining > 0:
                time.sleep(remaining)
        self._last[host] = time.monotonic()


class Fetcher:
    """HTTP with throttling, single retry on transient failure, and circuit breaking.

    A 403 or 429 permanently disables the host for this process. We do not
    retry-probe, rotate, or evade access controls; we record and degrade.
    """

    def __init__(self, throttle: HostThrottle) -> None:
        self.throttle = throttle
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.broken: dict[str, str] = {}
        self.calls = 0

    def is_broken(self, host: str) -> bool:
        return host in self.broken

    def get(self, url: str, params: dict[str, Any]) -> FetchResult:
        host = urlparse(url).netloc
        if self.is_broken(host):
            return FetchResult(False, None, None, f"circuit-broken: {self.broken[host]}")

        attempts = 0
        while True:
            attempts += 1
            self.throttle.wait(host)
            self.calls += 1
            try:
                resp = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                if attempts == 1:
                    log(f"network error on {host}: {exc.__class__.__name__}; retrying once")
                    time.sleep(2.0)
                    continue
                return FetchResult(False, None, None, f"network error: {exc.__class__.__name__}")

            if resp.status_code in (403, 429):
                retry_after = resp.headers.get("Retry-After")
                detail = f"HTTP {resp.status_code}"
                if retry_after:
                    detail += f" (Retry-After: {retry_after})"
                self.broken[host] = detail
                log(f"{host} returned {detail} -- circuit-breaking this host for the run")
                return FetchResult(False, resp.status_code, None, detail)

            if resp.status_code >= 500:
                if attempts == 1:
                    log(f"{host} returned HTTP {resp.status_code}; retrying once")
                    time.sleep(2.0)
                    continue
                return FetchResult(False, resp.status_code, None, f"HTTP {resp.status_code}")

            if resp.status_code >= 400:
                return FetchResult(False, resp.status_code, None, _error_detail(resp))

            try:
                return FetchResult(True, resp.status_code, resp.json(), None)
            except ValueError:
                return FetchResult(False, resp.status_code, None, "response was not JSON")


def _error_detail(resp: requests.Response) -> str:
    """Surface the API's own error text; it is how we tell 'limit too high' apart."""
    try:
        body = resp.json()
        if isinstance(body, dict) and body.get("error"):
            return f"HTTP {resp.status_code}: {body['error']}"
    except ValueError:
        pass
    return f"HTTP {resp.status_code}"


def classify_recoverable(detail: str | None, status: int | None) -> str | None:
    """Tell apart the two 4xx failures that are worth one retry at a smaller limit.

    Observed live: an over-large `limit` answers HTTP 400 "'limit' must be between
    1 and 100", while a slow full-text query answers HTTP 422 "Timeout. Maybe slow
    down a bit". Both are recoverable by asking for less, but they need different
    handling -- the timeout also needs a backoff -- and conflating them produces a
    misleading log line. Anything else is a real failure and is recorded as one.
    """
    if status not in (400, 422):
        return None
    text = (detail or "").lower()
    if "timeout" in text or "slow down" in text:
        return "timeout"
    if "limit" in text:
        return "limit"
    return None


def arctic_get_with_recovery(
    fetcher: Fetcher, url: str, params: dict[str, Any], *, label: str
) -> tuple[FetchResult, int]:
    """Recover from the two 4xx failures worth retrying. Returns (result, effective limit).

    A `limit` rejection gets exactly one retry at a smaller limit: the host told us
    the number was wrong, and retrying more times changes nothing.

    A "slow down a bit" timeout walks `TIMEOUT_LIMIT_LADDER`, widening the host
    interval and doubling the backoff at each rung. The archive answers a heavy
    full-text query with a timeout whatever our cadence is, so asking for
    progressively less is the only move that helps. The walk stops early on success,
    on a different kind of failure, or once the host is circuit-broken — a 403 or 429
    arriving mid-walk ends it immediately and is never retried.
    """
    limit = params.get("limit", MAX_PER_REQUEST)
    result = fetcher.get(url, params)
    if result.ok:
        return result, limit

    kind = classify_recoverable(result.error, result.status)
    if kind is None:
        return result, limit

    if kind == "limit":
        degraded = DEGRADED_LIMIT if limit > DEGRADED_LIMIT else limit
        if degraded == limit:
            return result, limit  # already small; retrying changes nothing
        log(f"{label}: limit={limit} rejected ({result.error}); retrying once at {degraded}")
        retry_params = dict(params)
        retry_params["limit"] = degraded
        return fetcher.get(url, retry_params), degraded

    host = urlparse(url).netloc
    effective = limit
    for rung, candidate in enumerate(TIMEOUT_LIMIT_LADDER):
        if candidate >= effective:
            continue  # never retry asking for the same or more
        effective = candidate
        # The host literally asked us to slow down, so widen its interval for the
        # rest of the run instead of hammering it again at the same cadence.
        widened = fetcher.throttle.slow_down(host)
        backoff = TIMEOUT_BACKOFF * (2 ** rung)
        log(
            f"{label}: {result.error}; host interval now {widened:.1f}s, "
            f"backing off {backoff:.0f}s and retrying at limit={effective}"
        )
        time.sleep(backoff)
        retry_params = dict(params)
        retry_params["limit"] = effective
        result = fetcher.get(url, retry_params)
        if result.ok:
            return result, effective
        if classify_recoverable(result.error, result.status) != "timeout":
            return result, effective  # a different failure, or circuit-broken
    return result, effective


# --------------------------------------------------------------------------- #
# argument helpers
# --------------------------------------------------------------------------- #


def parse_when(value: str | None, label: str) -> int | None:
    """Accept unix seconds or an ISO date/datetime; normalise to unix seconds.

    Arctic Shift takes either form, pullpush only takes epoch, so everything is
    normalised here to keep the two backends behaviourally identical.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.lstrip("-").isdigit():
        return int(text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"--{label} must be unix seconds or an ISO date like 2026-01-15 "
            f"(or 2026-01-15T12:00:00); got {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def split_subreddits(raw: Sequence[str]) -> list[str]:
    """`--subreddits a,b --subreddits c` and `r/a` all normalise to ['a','b','c']."""
    out: list[str] = []
    for chunk in raw:
        for part in chunk.split(","):
            name = part.strip().strip("/")
            if name.lower().startswith("r/"):
                name = name[2:]
            if name and name not in out:
                out.append(name)
    return out


# --------------------------------------------------------------------------- #
# contract section 2 mapping
# --------------------------------------------------------------------------- #


def evidence_id(source: str, url: str) -> str:
    """Stable across runs so /rescan can diff. sha1 of source + url, no separator."""
    return hashlib.sha1(f"{source}{url}".encode("utf-8")).hexdigest()


def normalise_text(raw: Any, max_chars: int) -> str | None:
    """Verbatim, or None. Truncation is allowed by the contract; rewording is not."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or text.lower() in EMPTY_BODY_MARKERS:
        return None
    return text[:max_chars] if len(text) > max_chars else text


def normalise_author(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    if not name or name.lower() in EMPTY_BODY_MARKERS:
        return None
    return f"u/{name}"


def permalink_url(item: dict[str, Any]) -> str | None:
    """Only ever a source-provided permalink. No URL is constructed or guessed."""
    permalink = item.get("permalink")
    if isinstance(permalink, str) and permalink.startswith("/"):
        return "https://www.reddit.com" + permalink
    # Self-posts also carry an absolute reddit URL; that is still the source's
    # own field, not something we assembled.
    fallback = item.get("url")
    if isinstance(fallback, str) and fallback.startswith("https://www.reddit.com/"):
        return fallback
    return None


def engagement_of(item: dict[str, Any]) -> dict[str, int | None] | None:
    """null engagement means the source did not report it -- it never means zero."""
    score = item.get("score")
    comments = item.get("num_comments")
    score = score if isinstance(score, int) else None
    comments = comments if isinstance(comments, int) else None
    if score is None and comments is None:
        return None
    return {"score": score, "comments": comments}


def has_complaint_signal(item: dict[str, Any]) -> bool:
    title = (item.get("title") or "").lower()
    if "?" in title:
        return True
    if any(token in title for token in COMPLAINT_SIGNALS):
        return True
    flair = (item.get("link_flair_text") or "").strip().lower()
    return flair in COMPLAINT_FLAIRS


def post_to_evidence(
    item: dict[str, Any],
    *,
    cell_id: str | None,
    query: str | None,
    captured_utc: int,
    max_text_chars: int,
) -> dict[str, Any] | None:
    url = permalink_url(item)
    if url is None:
        return None
    subreddit = item.get("subreddit")
    created = item.get("created_utc")
    return {
        "id": evidence_id("reddit", url),
        "cell_id": cell_id,
        "source": "reddit",
        "url": url,
        "title": item.get("title") or None,
        "text": normalise_text(item.get("selftext"), max_text_chars),
        "author": normalise_author(item.get("author")),
        "community": f"r/{subreddit}" if subreddit else None,
        "engagement": engagement_of(item),
        "created_utc": int(created) if isinstance(created, (int, float)) else None,
        "captured_utc": captured_utc,
        "query": query,
    }


def comment_to_evidence(
    item: dict[str, Any],
    *,
    cell_id: str | None,
    query: str | None,
    captured_utc: int,
    max_text_chars: int,
) -> dict[str, Any] | None:
    url = permalink_url(item)
    if url is None:
        return None
    text = normalise_text(item.get("body"), max_text_chars)
    if text is None:
        return None  # a comment with no body carries no evidence
    subreddit = item.get("subreddit")
    created = item.get("created_utc")
    score = item.get("score")
    return {
        "id": evidence_id("reddit", url),
        "cell_id": cell_id,
        "source": "reddit",
        "url": url,
        # Comments have no title of their own. The parent post is recoverable
        # from the permalink path (/r/<sub>/comments/<post_id>/<slug>/<id>/),
        # so nothing is invented to fill this in.
        "title": None,
        "text": text,
        "author": normalise_author(item.get("author")),
        "community": f"r/{subreddit}" if subreddit else None,
        "engagement": {"score": score if isinstance(score, int) else None, "comments": None},
        "created_utc": int(created) if isinstance(created, (int, float)) else None,
        "captured_utc": captured_utc,
        # The retrieval key was link_id, so the honest lineage is the query that
        # surfaced the parent post.
        "query": query,
    }


# --------------------------------------------------------------------------- #
# capture
# --------------------------------------------------------------------------- #


@dataclass
class SubredditReport:
    subreddit: str
    backend: str | None = None
    mode: str = "listing"
    query: str | None = None
    fetched: int = 0
    written: int = 0
    comments_written: int = 0
    status: str = "unavailable"
    detail: str | None = None
    # Arctic Shift's own outcome, kept even when pullpush later served this
    # subreddit, so source_health can report each backend truthfully.
    arctic_status: str | None = None
    arctic_detail: str | None = None
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def fetch_arctic_posts(
    fetcher: Fetcher,
    subreddit: str,
    *,
    query: str | None,
    limit: int,
    after: int | None,
    before: int | None,
    report: SubredditReport,
) -> list[dict[str, Any]]:
    """Paginate Arctic Shift `sort=desc` by walking `before` backwards in time."""
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor = before
    page_limit = min(limit, MAX_PER_REQUEST)
    # Generous page ceiling: enough to satisfy --limit even with heavy overlap,
    # but bounded so a misbehaving cursor cannot loop forever.
    max_pages = max(1, -(-limit // MAX_PER_REQUEST) + 2)

    for _ in range(max_pages):
        params: dict[str, Any] = {
            "subreddit": subreddit,
            "limit": page_limit,
            "sort": "desc",
        }
        if query:
            params["query"] = query
        if after is not None:
            params["after"] = after
        if cursor is not None:
            params["before"] = cursor

        result, page_limit = arctic_get_with_recovery(
            fetcher, ARCTIC_POSTS, params, label=f"r/{subreddit}"
        )

        if not result.ok:
            if collected:
                report.status = "degraded"
                report.detail = f"partial: {result.error}"
                log(f"r/{subreddit}: stopping after {len(collected)} items -- {result.error}")
            else:
                report.status = "unavailable"
                report.detail = result.error
            return collected

        items = (result.payload or {}).get("data") or []
        if not isinstance(items, list) or not items:
            break

        fresh = 0
        oldest: int | None = None
        for item in items:
            created = item.get("created_utc")
            if isinstance(created, (int, float)):
                oldest = int(created) if oldest is None else min(oldest, int(created))
            key = item.get("id") or item.get("permalink") or ""
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            collected.append(item)
            fresh += 1
            if len(collected) >= limit:
                break

        if len(collected) >= limit or fresh == 0 or oldest is None:
            break
        cursor = oldest  # overlap at the boundary is removed by `seen`

    report.status = report.status if report.status == "degraded" else "ok"
    if report.status == "ok":
        report.detail = None
    return collected[:limit]


def fetch_pullpush_posts(
    fetcher: Fetcher,
    subreddit: str,
    *,
    limit: int,
    after: int | None,
    before: int | None,
    report: SubredditReport,
) -> list[dict[str, Any]]:
    """Last-resort archive. One request, no pagination, stop for good on 429."""
    params: dict[str, Any] = {
        "subreddit": subreddit,
        "size": min(limit, DEGRADED_LIMIT),
        "sort": "desc",
        "sort_type": "created_utc",
    }
    if after is not None:
        params["after"] = after
    if before is not None:
        params["before"] = before

    result = fetcher.get(PULLPUSH_POSTS, params)
    if not result.ok:
        report.status = "unavailable"
        report.detail = result.error
        return []

    items = (result.payload or {}).get("data") or []
    if not isinstance(items, list):
        report.status = "unavailable"
        report.detail = "unexpected payload shape"
        return []
    report.status = "degraded"  # pullpush lags the live site; never call it 'ok'
    report.detail = "served by pullpush fallback; archive lags the live site"
    return items[:limit]


def spread_across_subreddits(
    targets: list[tuple[str, str | None, str]]
) -> list[tuple[str, str | None, str]]:
    """Round-robin the comment targets across subreddits before the cap applies.

    Targets are collected subreddit by subreddit, so a flat head-slice at
    `--comments-max-posts` spends the whole budget on the first community and
    leaves every later one with no comment evidence at all. That skew is
    invisible in the output yet it undermines `clusters.json`'s
    `distinct_communities`, which exists precisely to catch a single-subreddit
    echo. Order within a subreddit is preserved, so each community still
    contributes its own most-recent posts first.
    """
    buckets: dict[str, list[tuple[str, str | None, str]]] = {}
    for target in targets:
        buckets.setdefault(target[2], []).append(target)
    queues = list(buckets.values())
    out: list[tuple[str, str | None, str]] = []
    while queues:
        for queue in list(queues):
            out.append(queue.pop(0))
            if not queue:
                queues.remove(queue)
    return out


def fetch_comments(
    fetcher: Fetcher,
    post_id: str,
    *,
    top_n: int,
) -> tuple[list[dict[str, Any]], str | None]:
    """Top comments for one post. Returns (comments, failure_detail)."""
    params: dict[str, Any] = {"link_id": f"t3_{post_id}", "limit": MAX_PER_REQUEST}
    result, _ = arctic_get_with_recovery(fetcher, ARCTIC_COMMENTS, params, label=f"comments t3_{post_id}")
    if not result.ok:
        # This endpoint 422s for some posts. Empty is the honest answer; we do
        # not synthesise a discussion that we could not read.
        return [], result.error

    items = (result.payload or {}).get("data") or []
    if not isinstance(items, list):
        return [], "unexpected payload shape"
    items.sort(key=lambda c: c.get("score") if isinstance(c.get("score"), int) else 0, reverse=True)
    return items[:top_n], None


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #


class JsonlSink:
    """Append-only evidence writer. Existing ids are skipped, not rewritten."""

    def __init__(self, path: str | None) -> None:
        self.path = path
        self.known_ids: set[str] = set()
        self.duplicates = 0
        self._handle = None
        if path:
            directory = os.path.dirname(os.path.abspath(path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            if os.path.exists(path):
                self.known_ids = self._read_existing_ids(path)
                if self.known_ids:
                    log(f"{path}: {len(self.known_ids)} existing evidence ids will not be re-appended")
            self._handle = open(path, "a", encoding="utf-8")

    @staticmethod
    def _read_existing_ids(path: str) -> set[str]:
        ids: set[str] = set()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and isinstance(row.get("id"), str):
                        ids.add(row["id"])
        except OSError as exc:
            log(f"could not read existing {path} for dedupe: {exc}")
        return ids

    def write(self, record: dict[str, Any]) -> bool:
        if record["id"] in self.known_ids:
            self.duplicates += 1
            return False
        self.known_ids.add(record["id"])
        line = json.dumps(record, ensure_ascii=False)
        if self._handle:
            self._handle.write(line + "\n")
        else:
            print(line, flush=True)
        return True

    def close(self) -> None:
        if self._handle:
            self._handle.close()


def build_source_health(fetcher: Fetcher, reports: Iterable[SubredditReport], used_pullpush: bool) -> list[dict[str, Any]]:
    """One entry per backend actually touched. A failure is never reported as
    'no discussion found' -- that distinction is the point of this block."""
    reports = list(reports)
    health: list[dict[str, Any]] = []

    attempted = [r for r in reports if r.arctic_status is not None]
    served = [r for r in attempted if r.arctic_status in ("ok", "degraded")]
    failures = [f"r/{r.subreddit}: {r.arctic_detail}" for r in attempted if r.arctic_status == "unavailable"]

    if not attempted:
        arctic = {"source": "reddit:arctic-shift", "status": "unavailable", "detail": "no request attempted"}
    elif not served:
        detail = "; ".join(failures) or "no subreddit served"
        if fetcher.is_broken(ARCTIC_HOST):
            detail = f"{fetcher.broken[ARCTIC_HOST]} -- host circuit-broken for this run, not retried; {detail}"
        arctic = {"source": "reddit:arctic-shift", "status": "unavailable", "detail": detail}
    elif failures or any(r.arctic_status == "degraded" for r in served):
        partial = [f"r/{r.subreddit}: {r.arctic_detail}" for r in served if r.arctic_status == "degraded"]
        arctic = {
            "source": "reddit:arctic-shift",
            "status": "degraded",
            "detail": f"{len(served)}/{len(attempted)} subreddits served; " + "; ".join(failures + partial),
        }
    else:
        arctic = {
            "source": "reddit:arctic-shift",
            "status": "ok",
            "detail": f"{len(served)}/{len(attempted)} subreddits served",
        }
    if used_pullpush:
        arctic["fallback"] = "pullpush"
    health.append(arctic)

    if used_pullpush:
        pp = [r for r in reports if r.backend == "pullpush"]
        # "answered" is not "returned items". A pullpush 200 with an empty data
        # array is a real answer and must not be labelled unavailable -- rule 5
        # cuts both ways, and mislabelling it would contradict the per_subreddit
        # status for the same subreddit. The item count is reported separately
        # so an empty answer is still visible for what it is.
        pp_answered = [r for r in pp if r.status in ("ok", "degraded")]
        pp_items = sum(r.fetched for r in pp_answered)
        if fetcher.is_broken(PULLPUSH_HOST):
            health.append({
                "source": "reddit:pullpush",
                "status": "unavailable",
                "detail": f"{fetcher.broken[PULLPUSH_HOST]} -- stopped for this run, not retried",
            })
        else:
            health.append({
                "source": "reddit:pullpush",
                # Never "ok": pullpush lags the live site, so anything it serves
                # is degraded evidence by construction.
                "status": "degraded" if pp_answered else "unavailable",
                "detail": (
                    f"last-resort fallback answered for {len(pp_answered)}/{len(pp)} subreddits "
                    f"({pp_items} posts); archive lags the live site by weeks"
                ),
            })
    return health


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reddit_search.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Capture Reddit pain evidence with zero credentials, as CONTRACTS section 2 JSONL.\n"
            "Primary source: the Arctic Shift public archive. Last-resort fallback: pullpush.\n"
            "Never reads an API key, token, or cookie."
        ),
        epilog="""examples:
  # latest 100 posts from two subreddits, written to a run's evidence file
  uv run --quiet scripts/reddit_search.py \\
      --subreddits sysadmin,smallbusiness --cell-id m01 \\
      --out runs/my-run-2026-07-31/evidence/reddit.jsonl

  # full-text query inside specific communities, with a date window
  uv run --quiet scripts/reddit_search.py --subreddits smallbusiness \\
      --query "invoicing" --after 2025-01-01 --before 2025-07-01 --limit 200

  # posts plus their top comments (Arctic Shift 422s on some threads; those are
  # recorded as having no retrievable comments, never filled in)
  uv run --quiet scripts/reddit_search.py --subreddits sysadmin \\
      --limit 25 --comments --comments-per-post 10 --out evidence/reddit.jsonl

  # no --out: JSONL on stdout, run summary on stderr
  uv run --quiet scripts/reddit_search.py --subreddits sysadmin --limit 5 > reddit.jsonl

notes:
  --subreddits is required: Arctic Shift has no global subreddit-search API and
  its query parameter only works alongside subreddit (or author).

  The archive snapshots score at ingest, so posts younger than ~2 days usually
  read score=1. Use --min-score only on windows older than that.

exit codes:
  0  fetching worked (even if zero items survived filtering)
  1  nothing usable was gathered -- see source_health in the summary
""",
    )
    parser.add_argument(
        "--subreddits", action="append", default=[], metavar="a,b,c",
        help="comma-separated subreddit names (r/ prefix optional); repeatable",
    )
    parser.add_argument("--query", default=None, help="full-text query applied within each subreddit")
    parser.add_argument(
        "--limit", type=int, default=100, metavar="N",
        help="posts per subreddit (default 100; requests are capped at 100 each and paginated)",
    )
    parser.add_argument("--after", default=None, metavar="WHEN", help="ISO date or unix seconds: only posts newer than this")
    parser.add_argument("--before", default=None, metavar="WHEN", help="ISO date or unix seconds: only posts older than this")
    parser.add_argument("--min-score", type=int, default=None, metavar="N", help="drop posts scoring below N (caller's lever; off by default)")
    parser.add_argument(
        "--allow-unfiltered-fallback",
        action="store_true",
        help="when --query cannot be applied (pullpush fallback has no "
             "equivalent full-text match), keep the unfiltered listing instead "
             "of returning nothing. Off by default: an unfiltered listing "
             "answers a different question than the one asked, and downstream "
             "clustering cannot tell the two apart.",
    )
    parser.add_argument("--comments", action="store_true", help="also capture top comments for each captured post")
    parser.add_argument("--comments-per-post", type=int, default=10, metavar="N", help="top comments kept per post (default 10)")
    parser.add_argument(
        "--comments-max-posts", type=int, default=25, metavar="N",
        help="cap posts we request comments for, since each costs >=1.2s (default 25)",
    )
    parser.add_argument("--cell-id", default=None, metavar="ID", help="matrix cell id stamped on every record, e.g. m01")
    parser.add_argument("--out", default=None, metavar="PATH", help="append JSONL here; without it JSONL goes to stdout and the summary to stderr")
    parser.add_argument("--max-text-chars", type=int, default=8000, metavar="N", help="truncate verbatim text at N chars (default 8000)")
    parser.add_argument("--politeness", type=float, default=1.0, metavar="X", help="multiply every per-host interval by X (>=1 to be gentler)")
    return parser


def emit(summary: dict[str, Any], to_stdout: bool) -> None:
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text, file=sys.stdout if to_stdout else sys.stderr, flush=True)


def fatal(detail: str) -> int:
    """Pre-flight failure. Always printed to stdout, even in --out-less mode.

    Without --out the run summary normally goes to stderr so stdout can carry
    pure JSONL. But a pre-flight failure produces no JSONL at all, so honouring
    that convention would leave stdout empty and give a calling agent nothing to
    parse. stdout always carries parseable JSON.
    """
    log(detail)
    emit(
        {
            "script": "reddit_search.py",
            "source": "reddit",
            "error": detail,
            "totals": {"items_written": 0, "posts_written": 0, "comments_written": 0},
            "per_subreddit": [],
            "source_health": [
                {
                    "source": "reddit:arctic-shift",
                    "status": "unavailable",
                    "detail": f"not attempted: {detail}",
                }
            ],
            "warnings": [],
        },
        True,
    )
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary_to_stdout = args.out is not None

    subreddits = split_subreddits(args.subreddits)
    try:
        after = parse_when(args.after, "after")
        before = parse_when(args.before, "before")
    except ValueError as exc:
        return fatal(str(exc))

    if not subreddits:
        return fatal(
            "--subreddits is required: Arctic Shift has no global subreddit-search API "
            "and its 'query' parameter requires 'subreddit' or 'author'. Supply or guess "
            "community names before capturing."
        )

    limit = max(1, args.limit)
    fetcher = Fetcher(HostThrottle(HOST_INTERVALS, scale=max(1.0, args.politeness)))
    sink = JsonlSink(args.out)
    captured_utc = int(time.time())
    reports: list[SubredditReport] = []
    used_pullpush = False
    posts_written = 0
    comments_written = 0
    fresh_score_seen = 0
    bot_comments_skipped = 0
    comment_failures: list[dict[str, str]] = []
    # (post_id, parent query, subreddit) -- the subreddit is carried so the
    # --comments-max-posts budget can be spread across communities.
    comment_targets: list[tuple[str, str | None, str]] = []

    log(
        f"capturing r/{', r/'.join(subreddits)} "
        f"(limit {limit}/subreddit, query={args.query!r}, comments={args.comments})"
    )

    for subreddit in subreddits:
        report = SubredditReport(subreddit=subreddit, mode="query" if args.query else "listing", query=args.query)
        reports.append(report)

        if fetcher.is_broken(ARCTIC_HOST):
            report.arctic_status = "unavailable"
            report.arctic_detail = f"skipped: host circuit-broken ({fetcher.broken[ARCTIC_HOST]})"
            report.backend = "pullpush"
            used_pullpush = True
            if fetcher.is_broken(PULLPUSH_HOST):
                report.status = "unavailable"
                report.detail = f"both archives unavailable ({fetcher.broken[PULLPUSH_HOST]})"
                log(f"r/{subreddit}: skipped, both archives unavailable")
                continue
            if args.query:
                # pullpush does have a 'q' parameter, but its behaviour differs
                # from Arctic Shift's full-text match; rather than silently
                # changing query semantics mid-run we take the listing and let
                # the caller see the mode change in the summary.
                report.mode = "listing (query dropped: pullpush fallback)"
            items = fetch_pullpush_posts(fetcher, subreddit, limit=limit, after=after, before=before, report=report)
        else:
            report.backend = "arctic-shift"
            items = fetch_arctic_posts(
                fetcher, subreddit, query=args.query, limit=limit, after=after, before=before, report=report
            )
            report.arctic_status = report.status
            report.arctic_detail = report.detail
            # Fall back when Arctic Shift yielded nothing *because it failed*. A
            # successful empty result set is a real answer and must not be
            # replaced with fallback data. Note that status "unavailable" here
            # covers both the 403/429 circuit-break and an unrecovered 422.
            if not items and report.status == "unavailable" and not fetcher.is_broken(PULLPUSH_HOST):
                log(f"r/{subreddit}: arctic-shift gave nothing ({report.detail}); trying pullpush")
                arctic_detail = report.detail
                report.backend = "pullpush"
                used_pullpush = True
                if args.query:
                    report.mode = "listing (query dropped: pullpush fallback)"
                items = fetch_pullpush_posts(fetcher, subreddit, limit=limit, after=after, before=before, report=report)
                report.detail = f"arctic-shift: {arctic_detail}; then {report.detail}"

        report.fetched = len(items)
        log(f"r/{subreddit}: fetched {len(items)} posts via {report.backend} [{report.status}]")

        # CONTRACTS §2 defines `query` as "the exact query string that surfaced
        # this item". When the pullpush fallback drops the query and returns a
        # plain listing, the query surfaced nothing — stamping it anyway would
        # be a fabricated provenance claim, and unlike the mode change in the
        # summary it would travel *with the data* into clustering and onto the
        # cards. Null is the honest value; the dropped query stays visible in
        # report.mode for anyone reading the summary.
        effective_query = None if "query dropped" in report.mode else args.query

        # A dropped query is not a degraded answer to the question asked — it is
        # an answer to a different question. Returning r/smallbusiness's latest
        # 40 posts to a caller who asked about permits manufactures signal: the
        # items are real, but they are not evidence of the pain the matrix cell
        # is about, and clustering cannot tell the difference. So by default we
        # return nothing for this subreddit and say why. `source_health` marks
        # it unavailable, which downstream reads as "we could not look" rather
        # than "nobody is complaining" — the distinction the whole tool rests on.
        if args.query and effective_query is None and not args.allow_unfiltered_fallback:
            log(
                f"r/{subreddit}: dropping {len(items)} unfiltered items "
                f"(query {args.query!r} could not be applied); "
                f"pass --allow-unfiltered-fallback to keep them"
            )
            report.mode = f"dropped (query {args.query!r} unsupported by fallback)"
            report.status = "unavailable"
            report.detail = (
                f"{report.detail}; refused to substitute an unfiltered listing "
                f"for the query {args.query!r}"
            )
            items = []

        for item in items:
            score = item.get("score")
            created = item.get("created_utc")
            if isinstance(created, (int, float)) and captured_utc - int(created) < FRESH_POST_SECONDS:
                fresh_score_seen += 1
            author = item.get("author")
            if isinstance(author, str) and author.lower() in BOT_AUTHORS:
                # A weekly automod megathread body is boilerplate that would
                # cluster into a fake pain, but the thread hanging off it is
                # prime evidence. So drop the post and still mine its comments.
                # Checked before --min-score because megathreads often sit at
                # score 1 while carrying hundreds of comments.
                report.skip("bot_author")
                bot_post_id = item.get("id")
                if args.comments and isinstance(bot_post_id, str):
                    comment_targets.append((bot_post_id, args.query, subreddit))
                continue

            if args.min_score is not None:
                if not isinstance(score, int):
                    # "the source reported no score" is not "the score is low".
                    # The post is still dropped -- the caller asked for a floor
                    # we cannot check -- but it is not counted as below it.
                    report.skip("no_score_reported")
                    continue
                if score < args.min_score:
                    report.skip("below_min_score")
                    continue

            record = post_to_evidence(
                item,
                cell_id=args.cell_id,
                query=effective_query,
                captured_utc=captured_utc,
                max_text_chars=args.max_text_chars,
            )
            if record is None:
                # No source-provided permalink, and we never build one.
                report.skip("no_permalink")
                continue
            if record["text"] is None and not has_complaint_signal(item):
                report.skip("no_body_and_no_title_signal")
                continue
            if sink.write(record):
                report.written += 1
                posts_written += 1
                post_id = item.get("id")
                if args.comments and isinstance(post_id, str):
                    comment_targets.append((post_id, record["query"], subreddit))
            else:
                report.skip("already_in_out_file")

    if args.comments and comment_targets:
        by_subreddit = {r.subreddit: r for r in reports}
        targets = spread_across_subreddits(comment_targets)[: args.comments_max_posts]
        truncated = len(comment_targets) - len(targets)
        log(f"fetching comments for {len(targets)} posts" + (f" ({truncated} beyond --comments-max-posts skipped)" if truncated else ""))
        for post_id, parent_query, parent_subreddit in targets:
            if fetcher.is_broken(ARCTIC_HOST):
                comment_failures.append({"post_id": post_id, "detail": "arctic-shift circuit-broken"})
                continue
            comments, failure = fetch_comments(fetcher, post_id, top_n=args.comments_per_post)
            if failure:
                # Recorded as a failure, never as "no discussion found".
                comment_failures.append({"post_id": post_id, "detail": failure})
                continue
            for comment in comments:
                author = comment.get("author")
                if isinstance(author, str) and author.lower() in BOT_AUTHORS:
                    bot_comments_skipped += 1
                    continue
                record = comment_to_evidence(
                    comment,
                    cell_id=args.cell_id,
                    query=parent_query,
                    captured_utc=captured_utc,
                    max_text_chars=args.max_text_chars,
                )
                if record is None:
                    continue
                if sink.write(record):
                    comments_written += 1
                    parent_report = by_subreddit.get(parent_subreddit)
                    if parent_report is not None:
                        parent_report.comments_written += 1

    sink.close()

    fetch_worked = any(r.status in ("ok", "degraded") for r in reports)
    health = build_source_health(fetcher, reports, used_pullpush)

    warnings: list[str] = []
    if fresh_score_seen:
        warnings.append(
            f"{fresh_score_seen} fetched posts are younger than 48h; the archive snapshots "
            "score at ingest so those read score=1 / comments=0. Do not read that as low engagement."
        )
    if comment_failures:
        warnings.append(
            f"{len(comment_failures)} posts returned no retrievable comments (Arctic Shift 422s on "
            "some threads); they are recorded as failures, not as threads without discussion."
        )
    if used_pullpush:
        warnings.append("pullpush served part of this capture; its archive lags the live site by weeks.")

    summary = {
        "script": "reddit_search.py",
        "source": "reddit",
        "cell_id": args.cell_id,
        "captured_utc": captured_utc,
        "params": {
            "subreddits": subreddits,
            "query": args.query,
            "limit": limit,
            "after": after,
            "before": before,
            "min_score": args.min_score,
            "comments": args.comments,
            "comments_per_post": args.comments_per_post if args.comments else None,
            "max_text_chars": args.max_text_chars,
        },
        "out": os.path.abspath(args.out) if args.out else None,
        "out_mode": "append" if args.out else "stdout",
        "totals": {
            "items_written": posts_written + comments_written,
            "posts_written": posts_written,
            "comments_written": comments_written,
            "bot_comments_skipped": bot_comments_skipped,
            "posts_fetched": sum(r.fetched for r in reports),
            "deduped_against_out": sink.duplicates,
            "http_requests": fetcher.calls,
        },
        "per_subreddit": [
            {
                "subreddit": f"r/{r.subreddit}",
                "backend": r.backend,
                "mode": r.mode,
                "query": r.query,
                "fetched": r.fetched,
                "written": r.written,
                "comments_written": r.comments_written,
                "skipped": r.skipped,
                "status": r.status,
                "detail": r.detail,
            }
            for r in reports
        ],
        "comment_failures": comment_failures,
        "source_health": health,
        "warnings": warnings,
    }

    emit(summary, summary_to_stdout)
    if not fetch_worked:
        log("no source returned usable data; exiting 1")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("interrupted")
        sys.exit(1)
