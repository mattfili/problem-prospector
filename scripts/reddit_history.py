#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["requests"]
# ///
"""Backward-facing Reddit mention counts for the retro-trend method.

WHY THIS EXISTS
---------------
Hacker News tells you what developers argued about. Reddit tells you what
operators complained about while doing the job. For the retro-trend question --
"how long has this pain been true, and is the complaint volume durable or was it
one news cycle?" -- Reddit is usually the longer, less fashion-driven record, so
a card whose history rests on HN alone is a card with one witness.

This script produces the `reddit` entry of CONTRACTS section 4
`card.retro_trend.series`, plus the `shape` and `slope_pct_per_year` the
historian agent copies into the card. Like `hn_history.py` it is deliberately
*not* an evidence scout: it returns counts, never posts, and never writes
`evidence/*.jsonl`. Use `reddit_search.py` when you need the actual items.

HOW IT FITS THE PIPELINE
------------------------
    clusters.json --(canonical pain keyphrases)--+--> hn_history.py     --+
    inputs.json   --(matrix[].subreddits)--------+--> reddit_history.py --+
                                                                         |
                          retro_trend block --> cards/<cluster_id>.json --+

THE CENSORING PROBLEM (read this before trusting a number)
----------------------------------------------------------
Arctic Shift has no count-only endpoint. Algolia hands `hn_history.py` an
`nbHits` total for free; Arctic Shift hands us documents, so the only way to
count is to *count what came back*. The page limit is 100. Therefore:

    a bucket that returns 100 items is CENSORED -- 100 is a floor, not a count.

Every such bucket is marked `"censored": true`, its coverage is downgraded, and
the note says the counts are floors. A censored bucket is never presented as an
exact count, and the shape is withheld entirely once censored buckets dominate
the window (`REDDIT_THRESHOLDS["max_censored_share_for_shape"]`), because you
cannot fit a trend line to a series of ceilings. The honest fix when that
happens is a narrower bucket (`--bucket half-year`) or a narrower keyphrase, and
the note says so. Paginating (Arctic Shift returns `meta.last_result`, so you
can walk backwards) would de-censor a bucket at N times the request cost against
a host that already times out under load; this tool takes the floor instead.

VERIFIED API BEHAVIOUR ENCODED BELOW
------------------------------------
  * `query` (full text over title + selftext, terms AND-combined) is rejected
    with HTTP 400 unless the request is scoped: "'query' query parameter
    requires one of: author, subreddit". So `--subreddits` is not optional in
    practice, and the script says exactly that instead of returning zeros.
  * HTTP 422 with body `{"error": "Timeout. Maybe slow down a bit"}` is common
    (roughly one call in three for a year-wide full-text query) and is
    *transient*, not a limit problem. It is retried with backoff, then once at
    `limit=50`, then recorded as a failed bucket.
  * `fields=id,created_utc` trims a ~95 KB payload to a few KB. `permalink` is
    not a valid `fields` value ("'permalink' is not a valid field"), which is
    fine here -- this script never emits URLs.
  * A subreddit that does not exist returns HTTP 200 with `{"data": []}`, which
    is indistinguishable from silence. Hence the recency probe: a subreddit with
    no indexed posts at all is reported as a likely wrong name, never as "no
    discussion in r/x".

HONESTY NOTES
-------------
  * A bucket whose fetch failed gets `count: null` and a recorded failure. It is
    never written as `0`. "We could not measure it" and "nobody said it" are
    different facts and the whole tool depends on not conflating them.
  * Buckets are summed across the supplied subreddits, and a bucket only gets a
    `count` if every one of them answered. Lose one subreddit to a timeout and
    the bucket is reported as unmeasured (`count: null`) with the partial sum in
    `partial_count`, because a bucket summed over a subset is a smaller number
    for a reason that has nothing to do with the pain.
  * The current calendar period is incomplete, so counting it as a full data
    point would fake a decline. It is fetched (real signal for a reader) but
    flagged `partial` and excluded from slope and shape.
  * Windows are clipped to what the archive actually indexes. A bucket beyond
    the newest indexed post is `null`, not `0`.
  * The shape vocabulary and thresholds are IMPORTED from `hn_history.py`, not
    re-implemented, so the two sources of a card's history can never disagree
    about what "declining" means.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, NamedTuple

import requests

ARCTIC_SHIFT_POSTS_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"
ARCTIC_SHIFT_HOST = "arctic-shift.photon-reddit.com"

USER_AGENT = "problem-prospector/0.1 (+https://github.com/mattfili/problem-prospector)"

SOURCE_NAME = "reddit"

# Arctic Shift's documented courtesy floor is 1.2s between requests to this
# host. 1.3s leaves headroom for clock jitter without slowing a run that is
# dominated by ~5s server-side query time anyway. Do not lower it.
HOST_MIN_INTERVAL_S = 1.3
# Each 422 ("slow down a bit") widens the interval for the rest of the run. The
# host is telling us it is unhappy; the polite response is to permanently ease
# off, not to keep hammering at the floor rate.
HOST_BACKPRESSURE_STEP_S = 1.0
HOST_MAX_INTERVAL_S = 8.0
# A year-wide full-text query against a busy subreddit measured ~5-6s
# server-side, so the socket timeout has to be generous or we would abandon
# requests the host is still honestly working on.
REQUEST_TIMEOUT_S = 45.0
# Observed median latency of one full-text bucket call, used only to print a
# realistic ETA before a long run.
OBSERVED_CALL_LATENCY_S = 5.5

# Arctic Shift caps a page at 100. This is also the censoring threshold: a
# bucket returning exactly this many items has been truncated.
PAGE_LIMIT = 100
# Documented fallback from the platform reference: on a stubborn 422, ask for
# less. It lowers the censoring threshold too, so `limit_used` is recorded per
# bucket and censoring is judged against the limit actually sent.
PAGE_LIMIT_FALLBACK = 50
# Backoff before retrying a transient 422/5xx. Escalating, and long: the host
# times out under load, so retrying quickly just times out again.
TRANSIENT_BACKOFF_S = (4.0, 10.0)
# After this many consecutive fully-failed requests the run stops asking. Each
# failure already cost three attempts and ~15s of backoff, so five in a row is
# not a blip - it is the host telling us it has nothing for us right now.
# Grinding through the remaining buckets would add load and return nothing.
MAX_CONSECUTIVE_FAILURES = 5

REDDIT_THRESHOLDS: dict[str, float] = {
    # Above this share of censored buckets, no shape is claimed. A censored
    # bucket is a ceiling, and a trend line through mostly-ceilings measures the
    # ceiling, not the pain. Half is the point where the fitted line is carried
    # by the limit rather than by the data.
    "max_censored_share_for_shape": 0.50,
    # How far a subreddit's archive may fall short of a bucket's end before that
    # bucket counts as unmeasured for that subreddit. Without this, a subreddit
    # whose indexing stopped last spring would report honest-looking zeros for
    # every bucket since, and "the archive stopped" would read as "the
    # complaining stopped". A month of slack keeps the in-progress bucket (whose
    # end is "now", minutes ahead of the newest post) from tripping it.
    "stale_archive_days": 30.0,
    # Fewer usable buckets than this and the fitted line is arithmetic, not
    # history: two points always fit perfectly. The shape is still reported
    # (the classifier's own min_total_for_shape governs that) but the note has
    # to say how little it rests on.
    "min_buckets_for_reliable_slope": 3,
}

SIBLING_CLASSIFIER = "hn_history.py"


def log(msg: str) -> None:
    """Diagnostics go to stderr; stdout stays parseable JSON."""
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Shared classifier, imported rather than duplicated
# --------------------------------------------------------------------------
class Shared(NamedTuple):
    """The pieces of `hn_history.py` this script reuses verbatim.

    Loaded by path rather than by module name so the script works when invoked
    as `uv run /abs/path/scripts/reddit_history.py` from any cwd.
    """

    module_path: str
    bucket_windows: Callable[..., list[dict[str, Any]]]
    slope_pct_per_year: Callable[[list[int], int], float | None]
    half_over_half_pct: Callable[[list[int]], float | None]
    classify_coverage: Callable[[int, float], str]
    classify_shape: Callable[[list[int], float | None], tuple[str | None, dict[str, Any]]]
    shape_thresholds: dict[str, float]
    coverage_thresholds: dict[str, float]
    shapes: tuple[str, ...]


_REQUIRED_SHARED_ATTRS = (
    "bucket_windows",
    "slope_pct_per_year",
    "half_over_half_pct",
    "classify_coverage",
    "classify_shape",
    "SHAPE_THRESHOLDS",
    "COVERAGE_THRESHOLDS",
    "SHAPES",
)


def load_shared() -> Shared:
    """Import bucketing, slope and shape classification from `hn_history.py`.

    Deliberately not a local copy. If HN said "persistent-flat" at 12%/yr and
    Reddit said "declining" at the same slope, the card would be incoherent and
    nobody would know which script drifted. One definition, one file.
    """
    path = (Path(__file__).resolve().parent / SIBLING_CLASSIFIER).resolve()
    if not path.is_file():
        raise SystemExit(
            f"[error] cannot find {SIBLING_CLASSIFIER} next to this script (looked at {path}).\n"
            "        reddit_history.py imports its shape vocabulary, thresholds and time\n"
            "        bucketing from that file so the two retro-trend sources can never label\n"
            "        the same slope differently. Run this script from the plugin's scripts/\n"
            "        directory, or copy both files together."
        )

    spec = importlib.util.spec_from_file_location("_pp_hn_history", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"[error] could not load {path} as a module")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim, not swallowed
        raise SystemExit(
            f"[error] failed to import {path}: {type(exc).__name__}: {exc}\n"
            "        Fix hn_history.py first; this script depends on its classifier."
        ) from exc

    missing = [name for name in _REQUIRED_SHARED_ATTRS if not hasattr(module, name)]
    if missing:
        raise SystemExit(
            f"[error] {path} is missing {', '.join(missing)}.\n"
            "        reddit_history.py expects hn_history.py to expose bucket_windows(),\n"
            "        slope_pct_per_year(), half_over_half_pct(), classify_coverage(),\n"
            "        classify_shape(), SHAPE_THRESHOLDS, COVERAGE_THRESHOLDS and SHAPES.\n"
            "        If hn_history.py renamed one of them, update this list rather than\n"
            "        forking the classifier."
        )

    return Shared(
        module_path=str(path),
        bucket_windows=module.bucket_windows,
        slope_pct_per_year=module.slope_pct_per_year,
        half_over_half_pct=module.half_over_half_pct,
        classify_coverage=module.classify_coverage,
        classify_shape=module.classify_shape,
        shape_thresholds=dict(module.SHAPE_THRESHOLDS),
        coverage_thresholds=dict(module.COVERAGE_THRESHOLDS),
        shapes=tuple(module.SHAPES),
    )


# --------------------------------------------------------------------------
# Host politeness and circuit breaking
# --------------------------------------------------------------------------
class HostGate:
    """Politeness spacing, adaptive backpressure, and a one-way circuit breaker.

    Once tripped (403/429) the breaker never reopens inside a run: the correct
    response to being refused is to stop asking and degrade the output, not to
    back off and probe again, rotate identity, or retry from another angle.
    """

    def __init__(
        self,
        host: str,
        min_interval_s: float = HOST_MIN_INTERVAL_S,
        step_s: float = HOST_BACKPRESSURE_STEP_S,
        max_interval_s: float = HOST_MAX_INTERVAL_S,
    ) -> None:
        self.host = host
        self.interval_s = min_interval_s
        self.step_s = step_s
        self.max_interval_s = max_interval_s
        self.broken_reason: str | None = None
        # "refusal" (403/429) or "backpressure" (repeated timeouts). Both stop
        # the run; only the first is the host declining to serve us.
        self.stop_class: str | None = None
        self.calls = 0
        self.backpressure_events = 0
        self.consecutive_failures = 0
        self._last_call = 0.0

    @property
    def is_broken(self) -> bool:
        return self.broken_reason is not None

    def trip(self, reason: str, stop_class: str = "refusal") -> None:
        if self.broken_reason is None:
            self.broken_reason = reason
            self.stop_class = stop_class
            log(f"[circuit-break] {self.host}: {reason} - no further requests this run")

    def note_outcome(self, ok: bool) -> None:
        """Track consecutive dead requests so a hopeless run stops early."""
        if ok:
            self.consecutive_failures = 0
            return
        self.consecutive_failures += 1
        if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self.trip(
                f"{self.consecutive_failures} consecutive failed requests",
                stop_class="backpressure",
            )

    def penalize(self, why: str) -> None:
        self.backpressure_events += 1
        if self.interval_s < self.max_interval_s:
            self.interval_s = min(self.max_interval_s, self.interval_s + self.step_s)
            log(f"[backpressure] {why}; spacing raised to {self.interval_s:.1f}s for this run")

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if self._last_call and elapsed < self.interval_s:
            time.sleep(self.interval_s - elapsed)
        self._last_call = time.monotonic()
        self.calls += 1


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------
def _iso(ts: int | float) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat().replace("+00:00", "Z")


def arctic_get(
    session: requests.Session, gate: HostGate, params: dict[str, Any]
) -> tuple[list[dict[str, Any]] | None, str | None, int]:
    """One Arctic Shift request, with the consecutive-failure stop applied.

    Returns (items, error, limit_used). `limit_used` is the page size actually
    sent on the attempt that answered -- not a guess made from how many items
    came back -- because censoring is judged against it.
    """
    if gate.is_broken:
        broken = f"host circuit-broken: {gate.broken_reason}"
        return None, broken, int(params.get("limit", PAGE_LIMIT))
    items, error, limit_used = _arctic_request(session, gate, params)
    gate.note_outcome(error is None)
    return items, error, limit_used


def _arctic_request(
    session: requests.Session, gate: HostGate, params: dict[str, Any]
) -> tuple[list[dict[str, Any]] | None, str | None, int]:
    """Attempts and retries for one request. Returns (items, error, limit_used).

    Retry policy, all of it deliberate:
      * 403/429  -> trip the breaker, never retried.
      * 422      -> transient host timeout ("Maybe slow down a bit"): widen
                    spacing, back off, retry, and on the last attempt drop to
                    limit=50 as the platform reference suggests.
      * 5xx/net  -> same escalating backoff.
      * 400      -> a request we built wrong; reported verbatim, never retried,
                    except the one recoverable case of an unsupported `fields`.
    """
    attempts = len(TRANSIENT_BACKOFF_S) + 1
    sent = dict(params)

    def limit_now() -> int:
        return int(sent.get("limit", PAGE_LIMIT))

    for attempt in range(1, attempts + 1):
        last_attempt = attempt == attempts
        if last_attempt and sent.get("limit") == PAGE_LIMIT:
            # Documented fallback: a stubborn 422 sometimes clears at a smaller
            # page. Censoring is judged against limit_used, so this stays honest.
            sent["limit"] = PAGE_LIMIT_FALLBACK

        gate.wait()
        try:
            resp = session.get(ARCTIC_SHIFT_POSTS_URL, params=sent, timeout=REQUEST_TIMEOUT_S)
        except requests.RequestException as exc:
            if last_attempt:
                return None, f"network: {type(exc).__name__}", limit_now()
            log(f"[warn] network error ({type(exc).__name__}); retrying")
            time.sleep(TRANSIENT_BACKOFF_S[attempt - 1])
            continue

        if resp.status_code in (403, 429):
            gate.trip(f"HTTP {resp.status_code}")
            return None, f"HTTP {resp.status_code}", limit_now()

        body_error: str | None = None
        payload: Any = None
        try:
            payload = resp.json()
            if isinstance(payload, dict):
                body_error = payload.get("error")
        except ValueError:
            payload = None

        if resp.status_code == 422 or resp.status_code >= 500:
            reason = f"HTTP {resp.status_code}" + (f" ({body_error})" if body_error else "")
            if resp.status_code == 422:
                gate.penalize(reason)
            if last_attempt:
                return None, reason, limit_now()
            log(f"[warn] {reason}; backing off {TRANSIENT_BACKOFF_S[attempt - 1]:.0f}s and retrying")
            time.sleep(TRANSIENT_BACKOFF_S[attempt - 1])
            continue

        if resp.status_code == 400 and body_error and "not a valid field" in body_error:
            if "fields" in sent:
                # Trimming the payload is an optimisation, not a requirement.
                log(f"[warn] server rejected fields= ({body_error}); retrying with full payload")
                sent.pop("fields", None)
                continue
            return None, f"HTTP 400 ({body_error})", limit_now()

        if resp.status_code != 200:
            return (
                None,
                f"HTTP {resp.status_code}" + (f" ({body_error})" if body_error else ""),
                limit_now(),
            )

        if not isinstance(payload, dict):
            return None, "non-JSON response body", limit_now()
        items = payload.get("data")
        if items is None:
            return None, f"response carried no data ({body_error or 'no error given'})", limit_now()
        if not isinstance(items, list):
            return None, "response data was not a list", limit_now()
        return items, None, limit_now()

    return None, "retries exhausted", limit_now()


def probe_subreddit(
    session: requests.Session, gate: HostGate, subreddit: str
) -> dict[str, Any]:
    """Cheap pre-flight: newest indexed post for one subreddit.

    Two jobs, both about not lying later:
      1. A guessed subreddit name that does not exist returns HTTP 200 and an
         empty list, exactly like a real subreddit nobody posted in. Without
         this probe a typo becomes "no discussion found", which is the one
         failure mode this whole tool exists to prevent.
      2. It bounds the archive. Buckets past the newest indexed post are
         unmeasured, not empty.
    """
    items, error, _limit = arctic_get(
        session,
        gate,
        {"subreddit": subreddit, "limit": 1, "sort": "desc", "fields": "id,created_utc"},
    )
    if error is not None:
        log(f"[fail] recency probe r/{subreddit}: {error}")
        return {"subreddit": subreddit, "indexed": None, "latest_post_utc": None, "error": error}
    if not items:
        log(f"[warn] r/{subreddit}: no indexed posts at all - likely a wrong subreddit name")
        return {"subreddit": subreddit, "indexed": False, "latest_post_utc": None, "error": None}

    latest = items[0].get("created_utc")
    latest_int = int(latest) if isinstance(latest, (int, float)) else None
    log(
        f"[ok]   r/{subreddit}: archive current to "
        f"{_iso(latest_int) if latest_int else 'unknown'}"
    )
    return {
        "subreddit": subreddit,
        "indexed": True,
        "latest_post_utc": latest_int,
        "latest_post_iso": _iso(latest_int) if latest_int else None,
        "error": None,
    }


def count_bucket_for_subreddit(
    session: requests.Session,
    gate: HostGate,
    subreddit: str,
    query: str,
    start_utc: int,
    end_utc: int,
) -> dict[str, Any]:
    """Count matching posts in one subreddit for one bucket.

    `after`/`before` accept unix seconds (verified). The window is half-open
    [start, end): a post landing exactly on a boundary second may be missed
    rather than double-counted, which is the safer error for a count.
    """
    params = {
        "subreddit": subreddit,
        "query": query,
        "limit": PAGE_LIMIT,
        "sort": "desc",
        "after": start_utc,
        "before": end_utc,
        # Payload trimming only; this script emits counts, never items.
        "fields": "id,created_utc",
    }
    items, error, limit_used = arctic_get(session, gate, params)
    if error is not None:
        return {"subreddit": subreddit, "count": None, "error": error}

    assert items is not None
    # Defensive: verify the host honoured our bounds instead of trusting that it
    # did. A silent bound misinterpretation would corrupt every bucket equally
    # and be invisible in the output.
    in_window = [
        it
        for it in items
        if isinstance(it.get("created_utc"), (int, float))
        and start_utc <= int(it["created_utc"]) < end_utc
    ]
    dropped = len(items) - len(in_window)
    if dropped:
        log(
            f"[warn] r/{subreddit}: {dropped} returned item(s) fell outside the requested "
            "window and were not counted"
        )

    # `limit_used` is the page size the answering attempt actually sent, threaded
    # back from the request layer. It must not be inferred from len(items): a
    # bucket holding exactly PAGE_LIMIT_FALLBACK real posts would then look like
    # a page truncated at 50, and an exact count would be published as a floor.
    censored = len(items) >= limit_used
    oldest = min((int(it["created_utc"]) for it in in_window), default=None)
    return {
        "subreddit": subreddit,
        "count": len(in_window),
        "returned": len(items),
        "limit_used": limit_used,
        "censored": censored,
        "out_of_window_dropped": dropped,
        "oldest_returned_utc": oldest,
        "error": None,
    }


# --------------------------------------------------------------------------
# Per-query orchestration
# --------------------------------------------------------------------------
def _missing_subreddits(bucket: dict[str, Any]) -> list[str]:
    """Subreddits absent from a bucket, whether they failed or were out of archive."""
    return list(bucket["subreddits_failed"] or []) + [
        s["subreddit"] for s in (bucket["subreddits_skipped"] or [])
    ]


def run_query(
    session: requests.Session,
    gate: HostGate,
    shared: Shared,
    query: str,
    subreddits: list[str],
    recency: dict[str, dict[str, Any]],
    windows: list[dict[str, Any]],
    buckets_per_year: int,
    bucket_label: str,
) -> dict[str, Any]:
    buckets: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    # Sub-level failures inside an otherwise-measured bucket. Tracked separately
    # from `failures` because they degrade the run without emptying a bucket,
    # and a run that quietly dropped a subreddit must not report status "ok".
    subreddit_failures: list[dict[str, Any]] = []
    censored_detail: list[dict[str, Any]] = []
    # Every post successfully counted per subreddit, including ones inside buckets
    # later marked unmeasured, so this can legitimately exceed total_count.
    per_subreddit_seen: dict[str, int | None] = {}

    for window in windows:
        sub_counts: dict[str, int] = {}
        sub_failed: list[dict[str, Any]] = []
        sub_censored: list[str] = []
        skipped: list[dict[str, Any]] = []

        for subreddit in subreddits:
            info = recency.get(subreddit, {})
            latest = info.get("latest_post_utc")
            if isinstance(latest, int):
                # Beyond (or barely inside) the archive is unmeasured, not empty.
                gap_days = (window["end_utc"] - latest) / 86400.0
                if gap_days > REDDIT_THRESHOLDS["stale_archive_days"]:
                    skipped.append(
                        {
                            "subreddit": subreddit,
                            "reason": (
                                f"archive ends {_iso(latest)}, {gap_days:.0f} days before this "
                                "bucket closes; the bucket is unmeasured, not empty"
                            ),
                        }
                    )
                    continue

            result = count_bucket_for_subreddit(
                session, gate, subreddit, query, window["start_utc"], window["end_utc"]
            )
            if result["count"] is None:
                sub_failed.append({"subreddit": subreddit, "error": result["error"]})
                subreddit_failures.append(
                    {
                        "period": window["period"],
                        "subreddit": subreddit,
                        "error": result["error"],
                    }
                )
                log(f"[fail] {query!r} {window['period']} r/{subreddit}: {result['error']}")
                continue

            sub_counts[subreddit] = result["count"]
            prior = per_subreddit_seen.get(subreddit)
            per_subreddit_seen[subreddit] = (prior or 0) + result["count"]
            if result["censored"]:
                sub_censored.append(subreddit)
                bucket_days = (window["end_utc"] - window["start_utc"]) / 86400.0
                observed_days = (
                    (window["end_utc"] - result["oldest_returned_utc"]) / 86400.0
                    if result["oldest_returned_utc"]
                    else None
                )
                censored_detail.append(
                    {
                        "period": window["period"],
                        "subreddit": subreddit,
                        "returned": result["returned"],
                        "limit_used": result["limit_used"],
                        # Factual, not extrapolated: the page we got covers only
                        # the newest slice of the bucket. Reported so a reader
                        # can see how badly truncated the bucket is.
                        "oldest_returned_utc": result["oldest_returned_utc"],
                        "oldest_returned_iso": (
                            _iso(result["oldest_returned_utc"])
                            if result["oldest_returned_utc"]
                            else None
                        ),
                        "observed_days_of_bucket": (
                            round(observed_days, 1) if observed_days is not None else None
                        ),
                        "bucket_days": round(bucket_days, 1),
                    }
                )
            log(
                f"[{'cens' if result['censored'] else 'ok'}] {query!r} {window['period']} "
                f"r/{subreddit}: {result['count']}"
                + (f" (floor, limit {result['limit_used']})" if result["censored"] else "")
            )

        # A bucket only has a count if EVERY in-scope subreddit answered for it.
        # A bucket summed over a subset is a smaller number for a reason that has
        # nothing to do with the pain, and publishing it as `count` would be the
        # exact conflation this tool exists to avoid: a failed fetch showing up
        # as "fewer people complained". The partial sum is kept, clearly labelled,
        # so a reader can still see what did come back.
        incomplete_scope = bool(sub_failed or skipped)
        measured = bool(sub_counts) and not incomplete_scope
        bucket: dict[str, Any] = {
            "period": window["period"],
            # CONTRACTS section 4 requires period+count. The rest are additive
            # audit fields; a consumer reading only `count` still needs
            # `censored` to know the number is a floor.
            "count": sum(sub_counts.values()) if measured else None,
            "censored": bool(sub_censored) if measured else None,
            "partial_period": bool(window["partial"]),
            "incomplete_scope": incomplete_scope,
            # Only set when `count` is null: what the subreddits that did answer
            # added up to. A floor on the true bucket total, never a count.
            "partial_count": sum(sub_counts.values()) if (sub_counts and not measured) else None,
            "subreddit_counts": sub_counts or None,
            "subreddits_failed": [f["subreddit"] for f in sub_failed] or None,
            "subreddits_skipped": skipped or None,
        }
        buckets.append(bucket)
        if not measured:
            reason = "; ".join(
                [f"r/{f['subreddit']}: {f['error']}" for f in sub_failed]
                + [f"r/{s['subreddit']}: {s['reason']}" for s in skipped]
            ) or "no subreddits to query"
            failures.append(
                {
                    "period": window["period"],
                    "error": reason,
                    "partial_count": bucket["partial_count"],
                }
            )

    # ---- series math -----------------------------------------------------
    fetched = [b for b in buckets if b["count"] is not None]
    total_count = sum(b["count"] for b in fetched) if fetched else None
    failed_share = len(failures) / len(buckets) if buckets else 1.0
    # `total_count` sums every measured bucket, the in-progress one included, so
    # whether it is a floor has to be judged over the same set. Judging it over
    # the slope-eligible buckets alone would report `total_count_is_floor: false`
    # for a total whose largest term is a ceiling.
    censored_fetched = [b for b in fetched if b["censored"]]

    # `count is not None` already implies full subreddit scope, so the only
    # further exclusion is the in-progress period.
    usable = [b for b in buckets if b["count"] is not None and not b["partial_period"]]
    usable_counts = [b["count"] for b in usable]
    censored_usable = [b for b in usable if b["censored"]]
    censored_share = len(censored_usable) / len(usable) if usable else 0.0

    notes: list[str] = []
    coverage: str | None
    slope: float | None
    shape: str | None
    shape_evidence: dict[str, Any]
    # Set when a slope was computed but is not fit to publish in the card.
    slope_withheld: float | None = None

    if not fetched:
        coverage, slope, shape = None, None, None
        shape_evidence = {"reason": "every bucket failed or was out of archive; nothing measured"}
        notes.append(
            f"All {len(buckets)} Reddit buckets failed to measure - this is a source or scope "
            "failure, not an absence of Reddit discussion. Do not read as zero."
        )
        # The note is what reaches the card, so it has to carry the reason too,
        # not just the warning. Without this a stale archive and a refused host
        # produce identical notes and point at different remedies.
        distinct = sorted({f["error"] for f in failures})
        if distinct:
            shown = distinct[:3]
            rest = len(distinct) - len(shown)
            notes.append(
                "Reason(s): "
                + "; ".join(shown)
                + (f"; and {rest} more (see diagnostics.failures)." if rest else ".")
            )
        salvaged = [b for b in buckets if b["partial_count"] is not None]
        if salvaged:
            notes.append(
                f"{len(salvaged)} bucket(s) did get a partial sum from the subreddits that did "
                "answer (see partial_count); a subset sum is not a bucket count and is excluded "
                "from every number above."
            )
    else:
        coverage = shared.classify_coverage(total_count or 0, failed_share)
        slope = shared.slope_pct_per_year(usable_counts, buckets_per_year) if usable_counts else None
        if usable_counts:
            shape, shape_evidence = shared.classify_shape(usable_counts, slope)
        else:
            shape, shape_evidence = None, {
                "reason": "no complete, fully-measured buckets available for slope"
            }

        if shape is not None and len(usable_counts) < 2:
            # The shared classifier treats an undefined slope as flat, which is
            # right for a full window of zeros and wrong for a lone survivor: one
            # bucket is a level, not a trajectory. Withheld here rather than
            # fixed there, because the vocabulary must stay identical across
            # sources and this is a Reddit-side data condition (timeouts eating
            # the rest of the window), not a disagreement about what flat means.
            shape = None
            shape_evidence = {
                "reason": (
                    f"only {len(usable_counts)} usable bucket; a single point has no shape, and "
                    "the undefined slope must not be read as flatness"
                ),
                "measured_periods": [b["period"] for b in usable],
            }
            notes.append(
                "Shape withheld: exactly one complete bucket survived, which is a level rather "
                "than a history."
            )

        if censored_usable:
            # Coverage here describes measurement quality, not volume: a censored
            # series has plenty of discussion and an untrustworthy slope, so it
            # must not be labelled "good".
            if coverage == "good":
                coverage = "thin"
            periods = ", ".join(sorted({b["period"] for b in censored_usable}))
            remedy = (
                "Re-run with --bucket half-year or a narrower keyphrase for exact counts."
                if bucket_label == "year"
                else "Narrow the keyphrase or the subreddit list for exact counts; the bucket is "
                "already at its narrowest here."
            )
            notes.append(
                f"{len(censored_usable)} of {len(usable)} measured buckets hit the page limit "
                f"({periods}); those counts are FLOORS, not totals, so the slope is a lower "
                f"bound. {remedy}"
            )
            # A 422 retry can drop a single bucket to limit=50, which gives it a
            # *lower* ceiling than its neighbours. Comparing a 50-floor bucket
            # with a 100-floor bucket manufactures a decline, so say so.
            limits_used = {
                c["limit_used"]
                for c in censored_detail
                if c["period"] in {b["period"] for b in censored_usable}
            }
            if len(limits_used) > 1:
                notes.append(
                    "Censored buckets were measured at different page limits ("
                    + ", ".join(str(limit) for limit in sorted(limits_used))
                    + ") because a retry after host backpressure asked for less; their floors are "
                    "not comparable with each other, and any apparent decline between them is an "
                    "artefact of the limit."
                )
            if censored_share > REDDIT_THRESHOLDS["max_censored_share_for_shape"]:
                shape = None
                # The slope goes with it. A card that printed
                # "slope_pct_per_year: 0.0" off two ceilings would be asserting
                # flatness the data cannot support; the fitted value is kept in
                # diagnostics for audit instead.
                slope_withheld, slope = slope, None
                shape_evidence = {
                    "reason": (
                        f"{censored_share:.0%} of usable buckets are censored at the page limit "
                        f"(> {REDDIT_THRESHOLDS['max_censored_share_for_shape']:.0%}); a series of "
                        "ceilings has no shape"
                    ),
                    "censored_periods": sorted({b["period"] for b in censored_usable}),
                }
                notes.append(
                    "Shape and slope withheld: most buckets are censored, so any trend line "
                    "would be measuring the page limit rather than the pain"
                    + (
                        f" (the value fitted to the floor counts was {slope_withheld:+.1f}%/yr; "
                        "see diagnostics.slope_on_floor_counts)."
                        if slope_withheld is not None
                        else "."
                    )
                )

        # A censored bucket that is excluded from the slope window (the
        # in-progress period) still lands in `total_count`, and the block above
        # would never mention it. Left unsaid, the card's note would present a
        # total whose largest term is a ceiling as an exact number.
        _in_slope = {b["period"] for b in censored_usable}
        censored_outside_slope = [b for b in censored_fetched if b["period"] not in _in_slope]
        if censored_outside_slope:
            outside_periods = {b["period"] for b in censored_outside_slope}
            # The limit actually sent, which a 422 retry may have lowered to 50.
            outside_limits = sorted(
                {c["limit_used"] for c in censored_detail if c["period"] in outside_periods}
            )
            one = len(censored_outside_slope) == 1
            notes.append(
                ", ".join(b["period"] for b in censored_outside_slope)
                + " hit the page limit"
                + (
                    " (" + ", ".join(str(limit) for limit in outside_limits) + ")"
                    if outside_limits
                    else ""
                )
                + (" as well" if censored_usable else "")
                + ", so "
                + ("that count is a FLOOR" if one else "those counts are FLOORS")
                + " and total_count is a lower bound; "
                + ("it is" if one else "they are")
                + " outside the slope window, so the shape is unaffected."
            )

        if failures:
            notes.append(
                f"{len(failures)} of {len(buckets)} buckets could not be measured "
                f"({', '.join(f['period'] for f in failures)})."
            )
        partly = [b for b in buckets if b["count"] is None and b["partial_count"] is not None]
        if partly:
            detail = ", ".join(
                f"{b['period']} (measured {', '.join('r/' + s for s in b['subreddit_counts'])} = "
                f"{b['partial_count']}; missing "
                f"{', '.join('r/' + s for s in _missing_subreddits(b))})"
                for b in partly
            )
            notes.append(
                f"{len(partly)} bucket(s) are missing a subreddit - a failed request or an "
                "archive gap - and are reported as unmeasured rather than as a smaller count: "
                f"{detail}. The partial sums are floors, kept in partial_count, and excluded "
                "from slope and shape."
            )
        stale_subs = sorted(
            {s["subreddit"] for b in buckets for s in (b["subreddits_skipped"] or [])}
        )
        for subreddit in stale_subs:
            latest = recency.get(subreddit, {}).get("latest_post_utc")
            periods = [
                b["period"]
                for b in buckets
                if subreddit in {s["subreddit"] for s in (b["subreddits_skipped"] or [])}
            ]
            notes.append(
                f"r/{subreddit}'s archive ends "
                f"{_iso(latest) if latest else 'before this window'}, so "
                f"{', '.join(periods)} are unmeasured for it rather than empty."
            )
        partial = [b for b in buckets if b["partial_period"]]
        if partial:
            notes.append(
                f"{'/'.join(b['period'] for b in partial)} is an in-progress period; reported in "
                "the series but excluded from slope and shape."
            )
        if (
            0 < len(usable_counts) < REDDIT_THRESHOLDS["min_buckets_for_reliable_slope"]
            # Only a caveat if something was actually published. Saying "slope
            # and shape rest on 1 bucket" when both are null reads as though a
            # slope exists, which is the opposite of what happened.
            and (slope is not None or shape is not None)
        ):
            notes.append(
                f"Slope and shape rest on only {len(usable_counts)} complete bucket(s) of "
                f"{len(buckets)}; directional at best, not a rate."
            )
        if coverage == "thin" and not censored_usable:
            notes.append(
                f"Thin history: {total_count} total matching posts across {len(buckets)} buckets "
                f"in {', '.join('r/' + s for s in subreddits)}. Treat shape as suggestive only."
            )
        if coverage == "none":
            notes.append(
                "Zero matching posts in the whole window. Measurement succeeded, so this is a "
                "real absence in these subreddits - it says nothing about other subreddits or "
                "other sources."
            )

    return {
        "query": query,
        "search_term_sent": query,
        "subreddits": subreddits,
        "total_count": total_count,
        "total_count_is_floor": bool(censored_fetched) if fetched else None,
        "buckets_measured": len(fetched),
        "buckets_failed": len(failures),
        "buckets_censored": len([b for b in fetched if b["censored"]]),
        "buckets_partial_scope": len(
            [b for b in buckets if b["count"] is None and b["partial_count"] is not None]
        ),
        "subreddit_call_failures": len(subreddit_failures),
        # Shaped for direct merge into CONTRACTS section 4 card.retro_trend.
        "retro_trend": {
            "shape": shape,
            "slope_pct_per_year": slope,
            "series": [{"source": SOURCE_NAME, "buckets": buckets, "coverage": coverage}],
            "note": " ".join(notes) if notes else None,
        },
        "diagnostics": {
            "count_basis": (
                "items returned by Arctic Shift, summed across subreddits; there is no count-only "
                f"endpoint, so a bucket returning the page limit ({PAGE_LIMIT}) is a floor"
            ),
            "slope_basis": (
                "ordinary least squares on complete, fully-measured bucket counts, normalised by "
                "the window mean and scaled to percent per year (imported from hn_history.py)"
            ),
            "buckets_used_for_slope": len(usable_counts),
            "censored_share_of_usable": round(censored_share, 3),
            # True when the published slope was fitted to at least one floor
            # count: the real slope is at least this steep, direction included.
            "slope_is_lower_bound": bool(censored_usable) and slope is not None,
            "slope_on_floor_counts": slope_withheld,
            "half_over_half_pct": shared.half_over_half_pct(usable_counts),
            "shape_evidence": shape_evidence,
            "per_subreddit_posts_seen": per_subreddit_seen or None,
            "censored_buckets": censored_detail,
            "failures": failures,
            "subreddit_failures": subreddit_failures,
        },
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def normalize_subreddits(values: list[str]) -> list[str]:
    """Accept repeats, comma lists, and r/-prefixed names; de-duplicate."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        for piece in raw.split(","):
            name = piece.strip().strip("/")
            for prefix in ("r/", "/r/"):
                if name.lower().startswith(prefix):
                    name = name[len(prefix) :]
            name = name.strip("/")
            if name and name.lower() not in seen:
                seen.add(name.lower())
                out.append(name)
    return out


def read_lines_file(path: Path) -> list[str]:
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reddit_history.py",
        description=(
            "Backward-facing Reddit mention counts per time bucket, for the retro-trend method "
            "(CONTRACTS section 4, card.retro_trend.series). Answers 'how long has this pain been "
            "complained about, and is the volume durable or one news cycle?' rather than 'is it "
            "trending now?'. Key-free via the Arctic Shift archive. Arctic Shift has no count-only "
            "endpoint, so counts come from counting returned posts: a bucket that hits the page "
            f"limit ({PAGE_LIMIT}) is marked censored and its count is a floor, never an exact "
            "total. Shape and slope vocabulary is imported from hn_history.py so the two sources "
            "can never label the same series differently."
        ),
        epilog=(
            "examples:\n"
            "  # five years of yearly buckets for one keyphrase in two subreddits\n"
            "  uv run --quiet scripts/reddit_history.py \\\n"
            "      --query 'permit software' --subreddits sysadmin,msp\n\n"
            "  # tighter buckets when a yearly bucket comes back censored\n"
            "  uv run --quiet scripts/reddit_history.py --query 'permit software' \\\n"
            "      --subreddits sysadmin --years 3 --bucket half-year\n\n"
            "  # keyphrases from a file, persisted for the run\n"
            "  uv run --quiet scripts/reddit_history.py --queries-file pains.txt \\\n"
            "      --subreddits r/sysadmin --out runs/my-run/reddit_history.json\n\n"
            "notes:\n"
            "  --subreddits is effectively required: Arctic Shift rejects a full-text query that\n"
            "  is not scoped to a subreddit or author (HTTP 400), and there is no global Reddit\n"
            "  search. Take subreddits from inputs.json matrix[].subreddits.\n"
            "  Expect roughly 6s per bucket per subreddit; the host times out under load and is\n"
            "  retried with backoff, so a 5-year 2-subreddit run takes a couple of minutes.\n\n"
            "exit codes:\n"
            "  0  measurement worked (zero matching posts is a valid result)\n"
            "  1  nothing usable was gathered; see source_health\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--query",
        action="append",
        default=[],
        metavar="KEYPHRASE",
        help="keyphrase to count (terms are AND-combined over title and body); repeatable",
    )
    parser.add_argument(
        "--queries-file",
        type=Path,
        metavar="PATH",
        help="file of keyphrases, one per line ('#' comments and blanks ignored)",
    )
    parser.add_argument(
        "--subreddits",
        action="append",
        default=[],
        metavar="SUBS",
        help=(
            "subreddits to scope the search to, comma-separated and/or repeated "
            "('sysadmin,msp' or 'r/sysadmin'). Required in practice - see notes below"
        ),
    )
    parser.add_argument(
        "--years",
        type=int,
        default=5,
        help="how many complete years back to cover (default: 5)",
    )
    parser.add_argument(
        "--bucket",
        choices=("year", "half-year"),
        default="year",
        # Yearly by default because every bucket costs one ~6s request per
        # subreddit; half-year doubles the run but halves the volume per bucket,
        # which is the right move once buckets come back censored.
        help="bucket width, calendar-aligned (default: year)",
    )
    parser.add_argument(
        "--drop-partial",
        action="store_true",
        help="omit the in-progress period entirely instead of reporting it flagged as partial",
    )
    parser.add_argument(
        "--out",
        type=Path,
        metavar="PATH",
        help="also write the JSON to PATH (stdout always gets it)",
    )
    return parser


def emit(payload: dict[str, Any], out: Path | None) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        log(f"[out] wrote {out}")
    print(text)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    queries: list[str] = list(args.query)
    if args.queries_file:
        if not args.queries_file.is_file():
            log(f"[error] --queries-file not found: {args.queries_file}")
            return 1
        queries.extend(read_lines_file(args.queries_file))
    seen: set[str] = set()
    queries = [q for q in queries if not (q in seen or seen.add(q))]

    subreddits = normalize_subreddits(args.subreddits)

    if not queries:
        log("[error] no queries given; pass --query or --queries-file (see --help)")
        return 1
    if args.years < 1:
        log("[error] --years must be >= 1")
        return 1

    now = datetime.now(timezone.utc)
    base_payload: dict[str, Any] = {
        "tool": "reddit_history",
        "generated_utc": int(now.timestamp()),
        "generated_iso": now.isoformat().replace("+00:00", "Z"),
        "params": {
            "queries": queries,
            "subreddits": subreddits,
            "years": args.years,
            "bucket": args.bucket,
            "buckets_per_year": 2 if args.bucket == "half-year" else 1,
            "page_limit": PAGE_LIMIT,
            "include_current_partial": not args.drop_partial,
        },
    }

    if not subreddits:
        # Not a usage nit: Arctic Shift returns HTTP 400 for an unscoped
        # full-text query, and there is no global Reddit search. Say so in
        # machine-readable form rather than emitting a series of zeros.
        detail = (
            "no subreddits supplied; Arctic Shift rejects an unscoped full-text query "
            "(HTTP 400: \"'query' query parameter requires one of: author, subreddit\") and "
            "offers no global Reddit search. Pass --subreddits (e.g. from inputs.json "
            "matrix[].subreddits)."
        )
        log(f"[error] {detail}")
        base_payload["results"] = []
        base_payload["source_health"] = [
            {"source": SOURCE_NAME, "status": "unavailable", "detail": detail}
        ]
        emit(base_payload, args.out)
        return 1

    shared = load_shared()
    windows = shared.bucket_windows(
        now, args.years, args.bucket, include_partial=not args.drop_partial
    )
    buckets_per_year = base_payload["params"]["buckets_per_year"]

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    gate = HostGate(ARCTIC_SHIFT_HOST)

    full_text_calls = len(queries) * len(windows) * len(subreddits)
    log(
        f"[plan] {len(queries)} quer{'y' if len(queries) == 1 else 'ies'} x {len(windows)} buckets "
        f"x {len(subreddits)} subreddit(s) = {full_text_calls} full-text calls "
        f"(+{len(subreddits)} recency probe(s)); expect roughly "
        f"{math.ceil(full_text_calls * OBSERVED_CALL_LATENCY_S / 60)}m"
    )
    log(f"[classifier] shape vocabulary imported from {shared.module_path}")

    recency = {sub: probe_subreddit(session, gate, sub) for sub in subreddits}
    live_subs = [s for s, info in recency.items() if info.get("indexed") is not False]
    if not live_subs:
        detail = (
            "none of the requested subreddits have indexed posts: "
            + ", ".join("r/" + s for s in subreddits)
            + ". Likely wrong or guessed names - this is not evidence of silence."
        )
        log(f"[error] {detail}")
        base_payload["archive_recency"] = recency
        base_payload["results"] = []
        base_payload["source_health"] = [
            {"source": SOURCE_NAME, "status": "unavailable", "detail": detail}
        ]
        emit(base_payload, args.out)
        return 1

    # Only subreddits the archive actually has are measured. Keeping a
    # never-indexed name in scope would mark every bucket incomplete and sink an
    # otherwise valid two-subreddit series over one typo.
    results = [
        run_query(
            session, gate, shared, q, live_subs, recency, windows, buckets_per_year, args.bucket
        )
        for q in queries
    ]

    measured_total = sum(r["buckets_measured"] for r in results)
    failed_total = sum(r["buckets_failed"] for r in results)
    censored_total = sum(r["buckets_censored"] for r in results)
    partial_scope_total = sum(r["buckets_partial_scope"] for r in results)
    subreddit_fail_total = sum(r["subreddit_call_failures"] for r in results)
    unindexed = [s for s, info in recency.items() if info.get("indexed") is False]
    probe_failed = [s for s, info in recency.items() if info.get("error")]

    stopped = (
        f"stopped early ({gate.stop_class}): {gate.broken_reason}" if gate.is_broken else None
    )

    # Built once, outside the branch. A run where nothing could be measured needs
    # this context more than a degraded one does: "no bucket could be measured"
    # on its own reads as "the source is dead", when the cause may be one flaky
    # subreddit out of two, a typo'd name, or an archive that stopped years ago -
    # and each of those has a different remedy.
    context_bits: list[str] = []
    if censored_total:
        context_bits.append(
            f"{censored_total} censored at the page limit (counts are floors, not totals)"
        )
    if partial_scope_total:
        # Only worth saying when a bucket lost *some* of its subreddits; a
        # bucket where every subreddit failed is already in failed_total.
        context_bits.append(
            f"{partial_scope_total} bucket(s) unmeasured but partially counted after "
            f"{subreddit_fail_total} failed subreddit request(s)"
        )
    elif subreddit_fail_total:
        context_bits.append(f"{subreddit_fail_total} failed subreddit request(s)")
    if unindexed:
        context_bits.append("no indexed posts for " + ", ".join("r/" + s for s in unindexed))
    if probe_failed:
        context_bits.append(
            "recency probe failed for " + ", ".join("r/" + s for s in probe_failed)
        )
    if stopped:
        context_bits.append(stopped)
    # Deliberately not part of the degrade trigger: a run that hit backpressure
    # and still measured every bucket is "ok". The spacing is an operational
    # note, not a defect in the counts.
    pressure_bit = (
        f"{gate.backpressure_events} host backpressure event(s); spacing raised to "
        f"{gate.interval_s:.1f}s"
        if gate.backpressure_events
        else None
    )
    trailing = [pressure_bit] if pressure_bit else []

    if measured_total == 0:
        status = "unavailable"
        detail = "; ".join(
            [f"no bucket could be measured ({failed_total} failed)"] + context_bits + trailing
        )
    elif failed_total or context_bits:
        status = "degraded"
        detail = "; ".join(
            [f"{measured_total} of {measured_total + failed_total} buckets measured"]
            + context_bits
            + trailing
        )
    else:
        status = "ok"
        detail = (
            f"{measured_total} buckets counted from returned posts across "
            + ", ".join("r/" + s for s in subreddits)
        )

    payload = {
        **base_payload,
        "windows": windows,
        "thresholds": {
            "shape": shared.shape_thresholds,
            "coverage": shared.coverage_thresholds,
            "reddit": REDDIT_THRESHOLDS,
        },
        "classifier": {
            "imported_from": shared.module_path,
            "shapes_possible": list(shared.shapes),
        },
        "archive_recency": recency,
        "subreddits_measured": live_subs,
        "subreddits_excluded": unindexed,
        "requests_made": gate.calls,
        "results": results,
        "source_health": [{"source": SOURCE_NAME, "status": status, "detail": detail}],
    }

    emit(payload, args.out)
    return 0 if measured_total else 1


if __name__ == "__main__":
    sys.exit(main())
