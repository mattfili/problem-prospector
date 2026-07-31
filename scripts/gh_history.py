#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["requests"]
# ///
"""GitHub repo-creation histogram: is the *solution* side of a pain accumulating?

WHY THIS EXISTS
---------------
The prospector's core read is a two-sided one: persistent pain with NO
accumulating solutions is the classic underserved signal. `hn_history.py`
measures the pain side (are people still complaining?). This script measures
the solution side (are people still *building*?). A flat-or-rising complaint
curve next to a flat-or-empty repo-creation curve is the shape worth chasing;
a flat complaint curve next to a steeply rising repo curve usually means the
space is being commoditized while you read it.

Because that inference turns entirely on the direction of two curves, the
counts here must be honest. A year we failed to fetch is `count: null` and a
recorded failure — never a zero, and never "nothing found". Conflating those
two would invert the conclusion.

HOW IT FITS THE PIPELINE
------------------------
Emits one `series` entry per term plus a `combined` entry, each shaped so it
can be dropped verbatim into `cards/<cluster_id>.json` →
`retro_trend.series[]` (CONTRACTS §4): `{"source", "buckets": [{"period",
"count"}], "coverage"}`. The `shape` / `slope_pct_per_year` fields use the same
vocabulary as `hn_history.py` and feed `retro_trend.shape` /
`retro_trend.slope_pct_per_year`. Extra keys (`term`, `partial`, `detail`, …)
are additive audit metadata; consumers may ignore them.

NO CREDENTIALS, BY DESIGN
-------------------------
This script does NOT read `GITHUB_TOKEN` (or `GH_TOKEN`) even when one is
present in the environment. That is deliberate, not an oversight. The whole
plugin must behave identically for a user with no credentials, and silently
borrowing a token found in the environment would make the key-free guarantee
untestable — the maintainer's machine would quietly run at 30 req/min while
every new user hit the 10 req/min unauthenticated ceiling and got a different,
worse series. If a token is present we log that we are ignoring it (we check
for the key's *presence*, never its value) and pace ourselves anyway. The same
rule is enforced against `~/.netrc`, which `requests` would otherwise turn into
a silent Basic-auth header — see `_no_credentials`.

RATE DISCIPLINE
---------------
Unauthenticated GitHub code/repo search is 10 requests/minute. We pace at
~6.5s between calls, read `X-RateLimit-Remaining` / `X-RateLimit-Reset`, and
sleep out a window that reports itself exhausted. On 403 or 429 we
circuit-break the host immediately: no retry-probing, no rotation, no evasion.
The break is recorded in `source_health` and the run degrades.

EXAMPLES
--------
    uv run --quiet scripts/gh_history.py --terms "permit software" --years 5
    uv run --quiet scripts/gh_history.py \
        --terms "permitting" --terms "records request" --years 3 \
        --language python --out runs/my-slug/gh_history.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import requests

TOOL = "problem-prospector/0.1 (+https://github.com/mattfili/problem-prospector)"
SEARCH_URL = "https://api.github.com/search/repositories"
SOURCE = "github"

# Unauthenticated search ceiling is 10 requests/minute. 6.5s spacing gives
# ~9.2 req/min steady-state, which leaves headroom for clock skew between our
# sleep and GitHub's window accounting. This is a floor, not a default: --pace
# can only slow us down.
MIN_PACE_SECONDS = 6.5

# Waiting out a search-window reset should never exceed ~60s. Anything longer
# means the header is stale or we are being throttled for another reason, so we
# degrade rather than hang the agent that is blocking on our stdout.
MAX_RESET_WAIT_SECONDS = 75.0

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 25

# A shape read off two points is a line, not a trend.
MIN_BUCKETS_FOR_SHAPE = 3

# GitHub-wide repo creation grows roughly 10%/year, so a +/-15%/year band keeps
# ordinary platform growth inside "persistent-flat". Only movement that beats
# the platform's own drift counts as rising or declining.
FLAT_BAND_PCT = 15.0

# Under ~10 repos across the whole window, `total_count` swings on individual
# hobby repos and naming coincidences; the slope is not meaningful even when
# every fetch succeeded.
MIN_TOTAL_FOR_GOOD_COVERAGE = 10

# GitHub reports `total_count` exactly for small result sets but approximates it
# for very large ones; flag buckets past the documented 1000-result page cap.
APPROXIMATE_TOTAL_THRESHOLD = 1000

# GitHub launched in 2008, so a `created:` window before that is empty by
# construction. Asking for more years than that used to build a `date(-974,...)`
# and die with a traceback (no JSON on stdout at all), so the request is clamped
# to the platform's own lifetime and the clamp is reported in `note`.
GITHUB_EPOCH_YEAR = 2008


def log(msg: str) -> None:
    """Diagnostics go to stderr so stdout stays parseable JSON."""
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Trend classifier
#
# Contract: classify_series(counts) -> {"shape": str, "slope_pct_per_year":
# float | None}, where `counts` is chronological and may contain None for
# buckets that failed to fetch. Nones are dropped, never treated as zero.
#
# hn_history.py owns this vocabulary. We import it when present so the two
# sources cannot drift apart, and keep the mirror below so this script still
# runs standalone.
# --------------------------------------------------------------------------

SHAPES = (
    "insufficient-data",
    "no-signal",
    "emerging",
    "spike-and-fade",
    "rising",
    "declining",
    "persistent-flat",
)


def _ols_slope(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Least-squares slope of ys over xs (units: counts per bucket)."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


def _classify_series_local(counts: Sequence[int | None]) -> dict[str, Any]:
    usable = [(i, c) for i, c in enumerate(counts) if c is not None]
    if len(usable) < MIN_BUCKETS_FOR_SHAPE:
        return {"shape": "insufficient-data", "slope_pct_per_year": None}

    xs = [float(i) for i, _ in usable]
    ys = [float(c) for _, c in usable]
    total = sum(ys)
    if total == 0:
        # Every bucket fetched and every bucket was empty. That is a real
        # finding (nobody is building here), distinct from a failed fetch.
        return {"shape": "no-signal", "slope_pct_per_year": 0.0}

    mean = total / len(ys)
    slope_pct = round(100.0 * _ols_slope(xs, ys) / mean, 1)

    half = len(ys) // 2
    first_half = sum(ys[:half])
    second_half = sum(ys[half:])
    peak_idx = max(range(len(ys)), key=lambda k: ys[k])
    peak = ys[peak_idx]

    if first_half == 0 and second_half > 0:
        shape = "emerging"
    elif (
        0 < peak_idx < len(ys) - 1
        and ys[-1] <= 0.5 * peak
        and ys[0] <= 0.8 * peak
    ):
        shape = "spike-and-fade"
    elif slope_pct >= FLAT_BAND_PCT:
        shape = "rising"
    elif slope_pct <= -FLAT_BAND_PCT:
        shape = "declining"
    else:
        shape = "persistent-flat"

    return {"shape": shape, "slope_pct_per_year": slope_pct}


def _load_classifier() -> tuple[Any, str]:
    """Prefer hn_history's classifier; fall back to the local mirror.

    Validated rather than trusted: a same-named function with a different
    return shape would silently corrupt the card, so we probe it once.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from hn_history import classify_series as imported  # type: ignore
    except Exception as exc:
        # Logged, never silent: which classifier scored the curve changes how the
        # shape should be read, so the fallback has to be visible on stderr as
        # well as in `note`.
        log(
            f"[info] hn_history.classify_series not importable ({type(exc).__name__}: {exc}); "
            "using local mirror"
        )
        return _classify_series_local, "gh_history (local mirror)"
    try:
        probe = imported([1, 2, 3])
        if not isinstance(probe, dict) or "shape" not in probe:
            raise TypeError(f"unexpected return shape: {probe!r}")
    except Exception as exc:
        log(f"[warn] hn_history.classify_series unusable ({exc}); using local mirror")
        return _classify_series_local, "gh_history (local mirror)"
    return imported, "hn_history.classify_series"


classify_series, CLASSIFIER_SOURCE = _load_classifier()


def coverage_for(counts: Sequence[int | None]) -> str:
    """`good | thin | none` — how much weight the series can carry."""
    usable = [c for c in counts if c is not None]
    if not usable:
        return "none"
    if len(usable) < len(counts):
        return "thin"  # a hole in the window; the reader cannot see the whole curve
    if len(usable) < MIN_BUCKETS_FOR_SHAPE:
        return "thin"
    if sum(usable) == 0:
        # Every bucket fetched and every bucket was empty. Coverage is complete
        # and the finding is unambiguous — nobody is building here. This is the
        # highest-value read the script produces, so it must NOT be discounted
        # as "thin" by the small-volume rule below.
        return "good"
    if sum(usable) < MIN_TOTAL_FOR_GOOD_COVERAGE:
        return "thin"
    return "good"


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------


class Window:
    """One calendar-year bucket of the histogram."""

    def __init__(self, year: int, start: dt.date, end: dt.date, partial: bool) -> None:
        self.year = year
        self.start = start
        self.end = end
        self.partial = partial

    @property
    def label(self) -> str:
        return str(self.year)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def qualifier(self) -> str:
        return f"created:{self.start.isoformat()}..{self.end.isoformat()}"


def year_windows(today: dt.date, years: int) -> list[Window]:
    """The `years` most recent complete calendar years, plus year-to-date.

    The partial current-year bucket is reported (agents want to see it) but
    excluded from slope/shape, because a 7-month bucket next to 12-month
    buckets manufactures a decline that is an artifact of the calendar.
    """
    windows: list[Window] = []
    for year in range(today.year - years, today.year):
        windows.append(Window(year, dt.date(year, 1, 1), dt.date(year, 12, 31), False))
    if today.month > 1 or today.day > 1:
        windows.append(Window(today.year, dt.date(today.year, 1, 1), today, True))
    return windows


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------


def _no_credentials(request: Any) -> Any:
    """An auth hook that adds nothing, so `requests` never reaches for ~/.netrc.

    `requests` attaches HTTP Basic auth from `~/.netrc` (or `$NETRC`) whenever a
    session has no auth of its own and the file has a `machine` line matching the
    request host. On a machine with `machine api.github.com` in .netrc that puts a
    credential on the wire this script never asked for and cannot see — breaking
    the key-free guarantee exactly where it is least visible: the maintainer's own
    laptop would run authenticated, at a different rate limit and with different
    counts, while a new user runs anonymously. Setting the session's auth to this
    no-op is what suppresses that lookup; `trust_env` stays on so proxy and
    CA-bundle settings still work for users who need them.
    """
    return request


class GitHubCounter:
    """Paced, credential-free GitHub search-count client with a circuit breaker."""

    def __init__(self, pace: float, max_requests: int) -> None:
        self.pace = pace
        self.max_requests = max_requests
        self.requests_made = 0
        self.broken = False
        self.break_reason: str | None = None
        self._last_call: float | None = None
        self.session = requests.Session()
        # No Authorization header is ever set on this session, and no credential
        # is allowed to arrive from anywhere else either. See module docstring:
        # the key-free guarantee has to be observable.
        self.session.auth = _no_credentials
        self.session.headers.update(
            {
                "User-Agent": TOOL,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def _wait_turn(self) -> None:
        if self._last_call is None:
            return
        elapsed = time.monotonic() - self._last_call
        remaining = self.pace - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _honor_headers(self, resp: requests.Response) -> None:
        """Sleep out an exhausted search window before the next call."""
        raw_remaining = resp.headers.get("X-RateLimit-Remaining")
        if raw_remaining is None:
            return
        try:
            remaining = int(raw_remaining)
        except ValueError:
            return
        if remaining > 0:
            return
        try:
            reset_at = float(resp.headers.get("X-RateLimit-Reset", ""))
        except ValueError:
            return
        wait = reset_at - time.time() + 1.0
        if wait <= 0:
            return
        if wait > MAX_RESET_WAIT_SECONDS:
            self.broken = True
            self.break_reason = (
                f"rate-limit window would not reset for {wait:.0f}s; stopped rather than wait"
            )
            log(f"[warn] {self.break_reason}")
            return
        log(f"[pace] rate-limit window exhausted; sleeping {wait:.1f}s until reset")
        time.sleep(wait)

    def count(self, query: str) -> tuple[int | None, str | None, bool]:
        """Return (total_count, failure_detail, search_incomplete).

        Exactly one of total_count / failure_detail is None. `search_incomplete`
        mirrors GitHub's `incomplete_results`: when true the search hit its
        server-side time limit and `total_count` is a FLOOR, not a count. It is
        reported as such rather than passed off as a clean total.
        """
        if self.broken:
            return None, f"circuit-break: {self.break_reason}", False
        if self.requests_made >= self.max_requests:
            return None, f"skipped: --max-requests cap ({self.max_requests}) reached", False

        attempts = 0
        while True:
            attempts += 1
            self._wait_turn()
            self.requests_made += 1
            self._last_call = time.monotonic()
            try:
                resp = self.session.get(
                    SEARCH_URL,
                    params={"q": query, "per_page": 1},
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                )
            except requests.RequestException as exc:
                detail = f"network error: {type(exc).__name__}"
                # One retry for transient transport failures only.
                if attempts == 1 and self.requests_made < self.max_requests:
                    log(f"[warn] {detail}; retrying once")
                    continue
                return None, detail, False

            if resp.status_code in (403, 429):
                retry_after = resp.headers.get("Retry-After")
                self.broken = True
                self.break_reason = f"HTTP {resp.status_code}" + (
                    f" (Retry-After: {retry_after})" if retry_after else ""
                )
                log(
                    f"[warn] {self.break_reason} from api.github.com — circuit-breaking "
                    "this host for the rest of the run (no retry-probing)"
                )
                return None, f"circuit-break: {self.break_reason}", False

            if resp.status_code == 422:
                # Malformed/unsupported query. Bucket-local, not host-wide.
                return None, "HTTP 422 (query rejected by GitHub)", False

            if resp.status_code >= 500:
                detail = f"HTTP {resp.status_code}"
                if attempts == 1 and self.requests_made < self.max_requests:
                    log(f"[warn] {detail}; retrying once")
                    continue
                return None, detail, False

            if resp.status_code != 200:
                return None, f"HTTP {resp.status_code}", False

            self._honor_headers(resp)

            try:
                body = resp.json()
            except ValueError:
                return None, "unparseable JSON body", False
            if not isinstance(body, dict):
                return None, "response body was not a JSON object", False
            total = body.get("total_count")
            if not isinstance(total, int) or isinstance(total, bool):
                return None, "response had no integer total_count", False
            return total, None, body.get("incomplete_results") is True


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def build_query(term: str, window: Window, language: str | None) -> str:
    parts = [term.strip(), window.qualifier()]
    if language:
        parts.append(f"language:{language}")
    return " ".join(p for p in parts if p)


def summarize(
    buckets: list[dict[str, Any]], extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Wrap buckets into a CONTRACTS §4 `retro_trend.series[]`-shaped entry."""
    # Partial buckets are reported but never shape the trend line.
    complete = [b for b in buckets if not b.get("partial")]
    counts = [b["count"] for b in complete]
    verdict = classify_series(counts)
    usable = [c for c in counts if c is not None]
    entry: dict[str, Any] = {
        "source": SOURCE,
        "buckets": buckets,
        "coverage": coverage_for(counts),
        "shape": verdict.get("shape"),
        "slope_pct_per_year": verdict.get("slope_pct_per_year"),
        "totals": {
            "sum_complete_years": sum(usable) if usable else None,
            "buckets_total": len(buckets),
            "buckets_ok": sum(1 for b in buckets if b["count"] is not None),
            "buckets_failed": sum(1 for b in buckets if b["count"] is None),
            "buckets_scored": len(usable),
        },
    }
    if extra:
        entry.update(extra)
    return entry


def health(
    counter: GitHubCounter,
    series: Iterable[dict[str, Any]],
    degrade_reasons: Sequence[str] = (),
) -> dict[str, Any]:
    """CONTRACTS cross-cutting rule 5: what worked, what degraded, and why.

    `degrade_reasons` carries degradations that are not fetch failures: a year
    window we never asked for because of --max-requests, or a bucket whose count
    GitHub told us is incomplete. Nothing failed, but each one narrows the series
    the reader is about to judge, so none of them may be reported as a clean "ok".
    """
    series = list(series)
    any_ok = any(s["totals"]["buckets_ok"] > 0 for s in series)
    any_failed = any(s["totals"]["buckets_failed"] > 0 for s in series)
    reasons = list(degrade_reasons)
    if not any_ok:
        status = "unavailable"
        # Surface the recorded per-bucket reason rather than a generic "nothing
        # usable": a consumer must be able to tell a throttle from an outage
        # from a rejected query without re-reading every bucket.
        first_reason = next(
            (b["detail"] for s in series for b in s["buckets"] if b.get("detail")), None
        )
        primary = counter.break_reason or first_reason or "no bucket returned a usable total_count"
    elif any_failed or counter.broken or reasons:
        status = "degraded"
        if counter.break_reason:
            primary = counter.break_reason
        elif any_failed:
            primary = "one or more year buckets failed to fetch"
        else:
            # Nothing failed to fetch, so the recorded reasons are the whole
            # story. Let them speak for themselves instead of prefixing a
            # boilerplate clause that may not describe what happened.
            primary = reasons.pop(0)
    else:
        status = "ok"
        primary = f"{counter.requests_made} unauthenticated search requests, no throttling"
    return {"source": SOURCE, "status": status, "detail": "; ".join([primary, *reasons])}


def _strip_non_finite(node: Any) -> Any:
    """Replace NaN/inf with None so the payload can always be strict JSON.

    A number we cannot represent is reported as `null` — "the source did not give
    us a usable value" — never as a made-up one.
    """
    if isinstance(node, float) and not math.isfinite(node):
        return None
    if isinstance(node, dict):
        return {k: _strip_non_finite(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_strip_non_finite(v) for v in node]
    return node


def _dump_strict_json(payload: dict[str, Any]) -> str:
    """Serialize with allow_nan=False; sanitize and shout if that ever trips."""
    try:
        return json.dumps(payload, indent=2, allow_nan=False)
    except ValueError as exc:  # pragma: no cover - defensive tripwire
        log(f"[warn] non-finite number in payload ({exc}); emitting null in its place")
        return json.dumps(_strip_non_finite(payload), indent=2, allow_nan=False)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gh_history.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Count GitHub repositories created per year for one or more search terms, "
            "to see whether solutions are accumulating in a space.\n\n"
            "Persistent pain with NO accumulating solutions is the classic underserved "
            "signal; this is the solution-accumulation half of that read. Reads no "
            "credentials and ignores GITHUB_TOKEN by design, so every user gets the "
            "same series."
        ),
        epilog=(
            "examples:\n"
            "  # 5 complete years plus year-to-date for one term\n"
            "  uv run --quiet scripts/gh_history.py --terms \"permit software\" --years 5\n\n"
            "  # several terms, narrowed by language, persisted for a run\n"
            "  uv run --quiet scripts/gh_history.py \\\n"
            "      --terms \"permitting\" --terms \"code enforcement\" --years 3 \\\n"
            "      --language python --out runs/my-slug/gh_history.json\n\n"
            "  # exact-phrase search: quote inside the argument\n"
            "  uv run --quiet scripts/gh_history.py --terms '\"records request\"' --years 4\n\n"
            "notes:\n"
            "  Terms are passed to GitHub verbatim. Unquoted multi-word terms are ANDed\n"
            "  by GitHub, not matched as a phrase.\n"
            "  Unauthenticated search allows 10 requests/minute, so a 3-term 5-year run\n"
            "  (18 requests) takes roughly two minutes. Progress goes to stderr.\n"
            "  Exit 0 = fetching worked (even with zero repos found). Exit 1 = nothing\n"
            "  usable was gathered; see source_health.\n"
        ),
    )
    parser.add_argument(
        "--terms",
        action="append",
        required=True,
        metavar="TERM",
        help="Search term; repeat for multiple terms (each gets its own series).",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=5,
        help="Number of complete calendar years to cover (default: 5). "
        "The current year-to-date is also reported but excluded from slope. "
        f"Clamped to GitHub's own lifetime (since {GITHUB_EPOCH_YEAR}).",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Optional GitHub language qualifier (e.g. python) applied to every query.",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=40,
        help="Safety cap on total HTTP requests (default: 40). Oldest years are "
        "dropped first if the plan exceeds it.",
    )
    parser.add_argument(
        "--pace",
        type=float,
        default=MIN_PACE_SECONDS,
        help=f"Seconds between requests (default and minimum: {MIN_PACE_SECONDS}). "
        "Must be a finite number; values below the minimum are raised, never "
        "lowered, so this flag can only slow the run down.",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        default=None,
        help="Also write the JSON payload to PATH (stdout is unaffected).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    terms = [t.strip() for t in args.terms if t and t.strip()]
    if not terms:
        log("[error] no usable --terms after stripping whitespace")
        return 1
    if args.years < 1:
        log("[error] --years must be >= 1")
        return 1

    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if var in os.environ:
            log(
                f"[info] {var} is set in this environment and is being IGNORED by design "
                "(the plugin must behave identically with no credentials)"
            )

    # NaN and inf must be rejected before the comparison below, not after:
    # `max(nan, 6.5)` is `nan`, `nan - elapsed > 0` is False, and the pacer would
    # then never sleep at all — turning --pace into a rate-limit evasion switch,
    # the one thing it must never be. (`inf` hangs instead, and both serialize as
    # bare NaN/Infinity, which is not valid JSON for a non-Python consumer.)
    if not math.isfinite(args.pace):
        log("[error] --pace must be a finite number of seconds")
        return 1
    if args.pace < MIN_PACE_SECONDS:
        log(f"[info] --pace raised to the {MIN_PACE_SECONDS}s floor")
    pace = max(args.pace, MIN_PACE_SECONDS)

    today = dt.datetime.now(dt.timezone.utc).date()

    notes: list[str] = []
    degrade_reasons: list[str] = []

    # GitHub has no repos before it existed, and `dt.date(negative_year, ...)`
    # raises, which would kill the run with a traceback and no JSON on stdout.
    max_years = today.year - GITHUB_EPOCH_YEAR
    years = args.years
    if years > max_years:
        reason = (
            f"--years {years} clamped to {max_years}: GitHub launched in "
            f"{GITHUB_EPOCH_YEAR}, so earlier windows are empty by construction"
        )
        notes.append(reason)
        log(f"[info] {reason}")
        years = max_years

    windows = year_windows(today, years)
    planned = len(terms) * len(windows)
    if planned > args.max_requests:
        # Trim oldest years first: the recent end of the curve is what the
        # accumulation read depends on.
        keep = max(1, args.max_requests // len(terms))
        dropped = [w.label for w in windows[:-keep]]
        windows = windows[-keep:]
        reason = (
            f"--max-requests {args.max_requests} forced dropping year(s) {', '.join(dropped)}"
        )
        notes.append(reason)
        degrade_reasons.append(reason)
        log(f"[warn] {reason}")
        planned = len(terms) * len(windows)

    log(
        f"[plan] {len(terms)} term(s) x {len(windows)} year bucket(s) = {planned} request(s); "
        f"~{max(0, planned - 1) * pace:.0f}s of pacing"
    )

    counter = GitHubCounter(pace=pace, max_requests=args.max_requests)
    series: list[dict[str, Any]] = []
    approximate_buckets = 0
    incomplete_buckets = 0
    bucket_no = 0

    for term in terms:
        buckets: list[dict[str, Any]] = []
        for window in windows:
            query = build_query(term, window, args.language)
            total, detail, search_incomplete = counter.count(query)
            bucket_no += 1
            if total is None:
                log(f"[fail] {term!r} {window.label}: {detail}")
            else:
                if total > APPROXIMATE_TOTAL_THRESHOLD:
                    approximate_buckets += 1
                if search_incomplete:
                    # A count from a timed-out search is a floor. Say so on the
                    # bucket instead of letting it read as a clean total.
                    incomplete_buckets += 1
                    detail = (
                        "incomplete_results: GitHub's search timed out; "
                        "count is a floor, not a total"
                    )
                log(
                    f"[ok]   {term!r} {window.label}"
                    f"{' (YTD)' if window.partial else ''}: {total}"
                    f"  [{bucket_no}/{planned}, {counter.requests_made} req]"
                )
                if search_incomplete:
                    log(f"[warn] {term!r} {window.label}: {detail}")
            bucket: dict[str, Any] = {
                "period": window.label,
                "count": total,
                "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
                "partial": window.partial,
                "detail": detail,
                "search_incomplete": search_incomplete,
            }
            if window.partial:
                bucket["days_covered"] = window.days
            buckets.append(bucket)
        series.append(
            summarize(
                buckets,
                {
                    "term": term,
                    "query_template": build_query(term, windows[0], args.language),
                },
            )
        )

    # Combined view: sum across terms per year. A year is null only when EVERY
    # term failed for it; partially-fetched years carry terms_ok so the reader
    # can see the sum is a floor, not a total.
    combined_buckets: list[dict[str, Any]] = []
    for idx, window in enumerate(windows):
        per_term = [s["buckets"][idx]["count"] for s in series]
        ok = [c for c in per_term if c is not None]
        combined_buckets.append(
            {
                "period": window.label,
                "count": sum(ok) if ok else None,
                "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
                "partial": window.partial,
                "detail": None if len(ok) == len(per_term) else "partial: some terms failed",
                "search_incomplete": any(
                    s["buckets"][idx].get("search_incomplete") for s in series
                ),
                "terms_ok": len(ok),
                "terms_total": len(per_term),
            }
        )
    combined = summarize(
        combined_buckets,
        {"term": None, "terms": terms, "note": "sum across terms; repos matching two terms are double-counted"},
    )

    partials = [w.label for w in windows if w.partial]
    if partials:
        w = next(w for w in windows if w.partial)
        notes.append(
            f"{w.label} is year-to-date ({w.days} days) and is excluded from slope/shape"
        )
    if approximate_buckets:
        notes.append(
            f"{approximate_buckets} bucket(s) exceed {APPROXIMATE_TOTAL_THRESHOLD}; "
            "GitHub reports total_count approximately at that scale"
        )
    if incomplete_buckets:
        # A known-partial count must not be summarized as a clean "ok".
        reason = (
            f"{incomplete_buckets} bucket(s) returned incomplete_results (GitHub search "
            "timed out); those counts are floors, not totals"
        )
        notes.append(reason)
        degrade_reasons.append(reason)
    notes.append(f"classifier: {CLASSIFIER_SOURCE}")

    payload = {
        "tool": "gh_history",
        "generated_utc": int(time.time()),
        "params": {
            "terms": terms,
            "years": years,
            "years_requested": args.years,
            "language": args.language,
            "bucket": "calendar-year",
            "pace_seconds": pace,
            "max_requests": args.max_requests,
            "authenticated": False,
        },
        "series": series,
        "combined": combined,
        "requests_made": counter.requests_made,
        "note": "; ".join(notes),
        "source_health": [health(counter, series, degrade_reasons)],
    }

    # CONTRACTS cross-cutting rule 3: stdout is JSON an agent can parse. Serialize
    # to a string first (so a failure can never leave half a document on stdout)
    # with allow_nan=False, because bare NaN/Infinity is accepted by Python's own
    # json module but rejected by every strict parser downstream.
    text = _dump_strict_json(payload)
    sys.stdout.write(text + "\n")

    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        log(f"[info] wrote {out}")

    status = payload["source_health"][0]["status"]
    log(f"[done] {counter.requests_made} request(s); source_health={status}")
    return 1 if status == "unavailable" else 0


if __name__ == "__main__":
    sys.exit(main())
