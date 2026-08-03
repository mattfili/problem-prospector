#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["requests"]
# ///
"""npm package-creation histogram: a second read on the *solution* side.

WHY THIS EXISTS
---------------
The prospector's core read is a two-sided one: persistent pain with NO
accumulating solutions is the classic underserved signal. `gh_history.py` is
the historian's only coded supply-side source, and unauthenticated GitHub
search 403s from most cloud/CI IPs — so the read the whole pipeline is built
around can go dark on exactly the runs most likely to be automated. This
script is a second, independent supply-side signal — are npm packages
accumulating in this space? — so the two-curve read survives a GitHub outage
instead of failing closed.

SAMPLING CAVEAT — READ THIS BEFORE TRUSTING A COUNT
----------------------------------------------------
Unlike `gh_history.py`, this is NOT an exact census. GitHub's search API
returns an exact `total_count` for a `created:<range>` query server-side; npm's
search API has no date-range filter at all. So per term we fetch the top
`--candidates-per-term` search-ranked packages (npm's own relevance ranking,
not chronological), then look up each candidate's own creation date and bucket
it by year. `total_matches_reported` (npm's own `total` field) is recorded
alongside `candidates_examined` on every series so a reader can see how much
of the real population the bucket counts actually cover — when
`candidates_examined < total_matches_reported`, older or less-popular packages
are systematically under-represented, because npm's default sort is
popularity/relevance, not recency. Never read a bucket count here as
`gh_history.py`-grade exhaustive; `note` says so on every run and `coverage`
degrades to `thin` once the candidate pool visibly can't cover the window.

HOW IT FITS THE PIPELINE
------------------------
Emits one `series` entry per term plus a `combined` entry, each shaped so it
can be dropped verbatim into `cards/<cluster_id>.json` →
`retro_trend.series[]` (CONTRACTS §4): `{"source", "buckets": [{"period",
"count"}], "coverage"}`. `shape` / `slope_pct_per_year` are computed with
`gh_history.py`'s own classifier (imported directly, not re-implemented) so
both supply-side scripts share one vocabulary and one flat-band threshold —
see that module's comment on why the vocabulary is wider than the pain-side
scripts' and must stay that way.

NO CREDENTIALS, BY DESIGN
--------------------------
The public npm registry needs no token for reads, so there is no credential
to accidentally borrow here — but the stance is stated anyway for the same
reason `gh_history.py` states it: every user gets the same series, and this
script never reads an environment variable that could change that.

RATE DISCIPLINE
----------------
npm publishes no documented rate limit for registry reads (unlike GitHub's
10 req/min unauthenticated search ceiling). The pace below is a courtesy, not
a compliance floor: registry.npmjs.org is a CDN-backed service every `npm
install` in the world already hits constantly. On 403/429/repeated 5xx we
circuit-break the host immediately anyway — no retry-probing, no rotation,
no evasion, same as every other script in this plugin.

EXAMPLES
--------
    uv run --quiet scripts/npm_history.py --terms "permit tracking" --years 5
    uv run --quiet scripts/npm_history.py \
        --terms "permitting" --terms "records request" --years 3 \
        --out runs/my-slug/npm_history.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import requests

# gh_history.py owns the classifier: same vocabulary, same flat-band
# threshold, for both supply-side scripts. See its module comment for why
# that vocabulary is deliberately wider than the pain-side scripts' and must
# not be re-derived here.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gh_history import classify_series, coverage_for  # noqa: E402

TOOL = "problem-prospector/0.1 (+https://github.com/mattfili/problem-prospector)"
SEARCH_URL = "https://registry.npmjs.org/-/v1/search"
PACKAGE_URL = "https://registry.npmjs.org/{name}"
SOURCE = "npm"

# No documented ceiling; this is a courtesy pace against a CDN-backed public
# service, not a measured compliance floor the way GitHub's is.
MIN_PACE_SECONDS = 0.2

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 25

# npm launched in 2010; a window before that is empty by construction, same
# reasoning as gh_history.py's GITHUB_EPOCH_YEAR clamp.
NPM_EPOCH_YEAR = 2010

DEFAULT_CANDIDATES_PER_TERM = 15
# npm's search endpoint caps a single page at 250.
MAX_SEARCH_PAGE_SIZE = 250


def log(msg: str) -> None:
    """Diagnostics go to stderr so stdout stays parseable JSON."""
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Windows — a local, minimal mirror of gh_history.py's Window. Not imported:
# that one carries a GitHub `created:` search qualifier that has no npm
# equivalent, since npm's search has no date-range filter to build one for.
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


def year_windows(today: dt.date, years: int) -> list[Window]:
    """The `years` most recent complete calendar years, plus year-to-date.

    The partial current-year bucket is reported but excluded from
    slope/shape — a 7-month bucket next to 12-month buckets manufactures a
    decline that is an artifact of the calendar, not the data.
    """
    windows: list[Window] = []
    for year in range(today.year - years, today.year):
        windows.append(Window(year, dt.date(year, 1, 1), dt.date(year, 12, 31), False))
    if today.month > 1 or today.day > 1:
        windows.append(Window(today.year, dt.date(today.year, 1, 1), today, True))
    return windows


def bucket_for(created: dt.date, windows: Sequence[Window]) -> Window | None:
    for w in windows:
        if w.start <= created <= w.end:
            return w
    return None


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------


class NpmClient:
    """Paced npm registry client with a circuit breaker (search + per-package reads)."""

    def __init__(self, pace: float, max_requests: int) -> None:
        self.pace = pace
        self.max_requests = max_requests
        self.requests_made = 0
        self.broken = False
        self.break_reason: str | None = None
        self._last_call: float | None = None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": TOOL})

    def _wait_turn(self) -> None:
        if self._last_call is None:
            return
        elapsed = time.monotonic() - self._last_call
        remaining = self.pace - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _get(self, url: str, params: dict[str, Any] | None = None) -> tuple[Any, str | None]:
        """Return (json_body, failure_detail). Exactly one is None (404 -> ({}, None) is not used;
        callers distinguish 404 explicitly since it is a valid "package not found", not a fetch failure).
        """
        if self.broken:
            return None, f"circuit-break: {self.break_reason}"
        if self.requests_made >= self.max_requests:
            return None, f"skipped: --max-requests cap ({self.max_requests}) reached"

        attempts = 0
        while True:
            attempts += 1
            self._wait_turn()
            self.requests_made += 1
            self._last_call = time.monotonic()
            try:
                resp = self.session.get(
                    url, params=params, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)
                )
            except requests.RequestException as exc:
                detail = f"network error: {type(exc).__name__}"
                if attempts == 1 and self.requests_made < self.max_requests:
                    log(f"[warn] {detail}; retrying once")
                    continue
                return None, detail

            if resp.status_code in (403, 429):
                retry_after = resp.headers.get("Retry-After")
                self.broken = True
                self.break_reason = f"HTTP {resp.status_code}" + (
                    f" (Retry-After: {retry_after})" if retry_after else ""
                )
                log(
                    f"[warn] {self.break_reason} from registry.npmjs.org — circuit-breaking "
                    "this host for the rest of the run (no retry-probing)"
                )
                return None, f"circuit-break: {self.break_reason}"

            if resp.status_code == 404:
                return {"_not_found": True}, None

            if resp.status_code >= 500:
                detail = f"HTTP {resp.status_code}"
                if attempts == 1 and self.requests_made < self.max_requests:
                    log(f"[warn] {detail}; retrying once")
                    continue
                return None, detail

            if resp.status_code != 200:
                return None, f"HTTP {resp.status_code}"

            try:
                return resp.json(), None
            except ValueError:
                return None, "unparseable JSON body"

    def search(self, term: str, size: int) -> tuple[list[str], int | None, str | None]:
        """Return (candidate package names, total_matches_reported, failure_detail)."""
        body, detail = self._get(
            SEARCH_URL, params={"text": term, "size": min(size, MAX_SEARCH_PAGE_SIZE)}
        )
        if body is None:
            return [], None, detail
        objects = body.get("objects")
        total = body.get("total")
        if not isinstance(objects, list):
            return [], None, "response had no usable 'objects' array"
        names = [
            o["package"]["name"]
            for o in objects
            if isinstance(o, dict) and isinstance(o.get("package"), dict) and o["package"].get("name")
        ]
        return names, total if isinstance(total, int) else None, None

    def created_date(self, package: str) -> tuple[dt.date | None, str | None]:
        """Return (creation date, failure_detail). A package that 404s is not a fetch
        failure — the search index and the registry can disagree briefly — so it is
        reported as a bucket-local detail, not folded into the health verdict.
        """
        body, detail = self._get(PACKAGE_URL.format(name=package))
        if body is None:
            return None, detail
        if body.get("_not_found"):
            return None, "404: package indexed by search but not found in registry"
        created_raw = (body.get("time") or {}).get("created")
        if not isinstance(created_raw, str):
            return None, "response had no time.created"
        try:
            return dt.datetime.fromisoformat(created_raw.replace("Z", "+00:00")).date(), None
        except ValueError:
            return None, f"unparseable time.created: {created_raw!r}"


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def summarize(
    buckets: list[dict[str, Any]], extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Wrap buckets into a CONTRACTS §4 `retro_trend.series[]`-shaped entry."""
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


def health(client: NpmClient, series: list[dict[str, Any]], degrade_reasons: Sequence[str]) -> dict[str, Any]:
    """CONTRACTS cross-cutting rule 5: what worked, what degraded, and why."""
    any_ok = any(s["totals"]["buckets_ok"] > 0 for s in series)
    any_failed = any(s["totals"]["buckets_failed"] > 0 for s in series)
    reasons = list(degrade_reasons)
    if not any_ok:
        status = "unavailable"
        first_reason = next(
            (b["detail"] for s in series for b in s["buckets"] if b.get("detail")), None
        )
        primary = client.break_reason or first_reason or "no term returned a usable candidate"
    elif any_failed or client.broken or reasons:
        status = "degraded"
        if client.break_reason:
            primary = client.break_reason
        elif any_failed:
            primary = "one or more year buckets had no usable candidates"
        else:
            primary = reasons.pop(0)
    else:
        status = "ok"
        primary = f"{client.requests_made} request(s), no throttling"
    return {"source": SOURCE, "status": status, "detail": "; ".join([primary, *reasons])}


def _strip_non_finite(node: Any) -> Any:
    if isinstance(node, float) and not math.isfinite(node):
        return None
    if isinstance(node, dict):
        return {k: _strip_non_finite(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_strip_non_finite(v) for v in node]
    return node


def _dump_strict_json(payload: dict[str, Any]) -> str:
    try:
        return json.dumps(payload, indent=2, allow_nan=False)
    except ValueError as exc:  # pragma: no cover - defensive tripwire
        log(f"[warn] non-finite number in payload ({exc}); emitting null in its place")
        return json.dumps(_strip_non_finite(payload), indent=2, allow_nan=False)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="npm_history.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Bucket npm packages by creation year for one or more search terms, as a "
            "second supply-side signal alongside gh_history.py.\n\n"
            "NOT an exact census (see module docstring's SAMPLING CAVEAT): counts are "
            "bucketed from the top --candidates-per-term search-ranked packages, not "
            "every package matching the term. Reads no credentials."
        ),
        epilog=(
            "examples:\n"
            "  uv run --quiet scripts/npm_history.py --terms \"permit tracking\" --years 5\n\n"
            "  uv run --quiet scripts/npm_history.py \\\n"
            "      --terms \"permitting\" --terms \"records request\" --years 3 \\\n"
            "      --out runs/my-slug/npm_history.json\n\n"
            "notes:\n"
            "  Exit 0 = fetching worked (even with zero packages found). Exit 1 = nothing\n"
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
        f"Clamped to npm's own lifetime (since {NPM_EPOCH_YEAR}).",
    )
    parser.add_argument(
        "--candidates-per-term",
        type=int,
        default=DEFAULT_CANDIDATES_PER_TERM,
        help=f"Top-N search-ranked packages examined per term (default: "
        f"{DEFAULT_CANDIDATES_PER_TERM}). Higher covers more of the real population "
        "at the cost of one request per candidate.",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=80,
        help="Safety cap on total HTTP requests, search plus per-candidate lookups "
        "combined (default: 80).",
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
    if args.candidates_per_term < 1:
        log("[error] --candidates-per-term must be >= 1")
        return 1

    if not math.isfinite(args.pace):
        log("[error] --pace must be a finite number of seconds")
        return 1
    if args.pace < MIN_PACE_SECONDS:
        log(f"[info] --pace raised to the {MIN_PACE_SECONDS}s floor")
    pace = max(args.pace, MIN_PACE_SECONDS)

    today = dt.datetime.now(dt.timezone.utc).date()

    notes: list[str] = []
    degrade_reasons: list[str] = []

    max_years = today.year - NPM_EPOCH_YEAR
    years = args.years
    if years > max_years:
        reason = f"--years {years} clamped to {max_years}: npm launched in {NPM_EPOCH_YEAR}"
        notes.append(reason)
        log(f"[info] {reason}")
        years = max_years

    windows = year_windows(today, years)
    client = NpmClient(pace=pace, max_requests=args.max_requests)
    series: list[dict[str, Any]] = []
    undersampled_terms = 0

    for term in terms:
        candidates, total_matches, search_detail = client.search(term, args.candidates_per_term)
        if search_detail:
            log(f"[fail] search {term!r}: {search_detail}")

        buckets_by_year: dict[str, list[str]] = {w.label: [] for w in windows}
        failed_lookups = 0
        for name in candidates:
            created, detail = client.created_date(name)
            if created is None:
                failed_lookups += 1
                if detail:
                    log(f"[warn] {term!r} {name}: {detail}")
                continue
            w = bucket_for(created, windows)
            if w is not None:
                buckets_by_year[w.label].append(name)

        # The search itself failing (zero candidates because we couldn't even ask)
        # is a fetch failure: every bucket is `null`, never a zero. A search that
        # succeeded but simply found no candidates for a given year is a real,
        # scoreable zero — the two must not collapse into each other.
        search_failed = bool(search_detail) and not candidates

        buckets: list[dict[str, Any]] = []
        for w in windows:
            names = buckets_by_year[w.label]
            bucket: dict[str, Any] = {
                "period": w.label,
                "count": None if search_failed else len(names),
                "window": {"start": w.start.isoformat(), "end": w.end.isoformat()},
                "partial": w.partial,
                "detail": search_detail if search_failed else None,
            }
            if w.partial:
                bucket["days_covered"] = w.days
            buckets.append(bucket)

        undersampled = bool(candidates and total_matches and len(candidates) < total_matches)
        if undersampled:
            undersampled_terms += 1

        # Two reasons a "good" verdict from coverage_for() (which only sees bucket
        # counts) would overstate this series: (1) a candidate we couldn't resolve
        # to a creation date doesn't map to any single bucket, so no specific
        # bucket can be nulled for it, and (2) the search matched more packages
        # than we examined, so the bucket counts are a floor from the top-ranked
        # candidates, not the real population (SAMPLING CAVEAT above). Either one,
        # left unflagged, is the failure-as-absence bug in a diffuse form. Cap
        # coverage at "thin" for both.
        coverage_override = None
        if candidates and failed_lookups / len(candidates) > 0.3:
            coverage_override = "thin"
            degrade_reasons.append(
                f"{term!r}: {failed_lookups} of {len(candidates)} candidate lookups "
                "failed to resolve a creation date; series coverage capped at 'thin'"
            )
        if undersampled:
            coverage_override = "thin"
            degrade_reasons.append(
                f"{term!r}: only {len(candidates)} of {total_matches} matches examined; "
                "series coverage capped at 'thin' (see SAMPLING CAVEAT)"
            )

        series_entry = summarize(
            buckets,
            {
                "term": term,
                "candidates_examined": len(candidates),
                "total_matches_reported": total_matches,
                "failed_lookups": failed_lookups,
            },
        )
        if coverage_override and series_entry["coverage"] == "good":
            series_entry["coverage"] = coverage_override
        series.append(series_entry)
        log(
            f"[ok]   {term!r}: {len(candidates)} candidate(s) examined "
            f"(of {total_matches if total_matches is not None else '?'} matched), "
            f"{failed_lookups} lookup failure(s)  [{client.requests_made} req]"
        )

    combined_buckets: list[dict[str, Any]] = []
    for idx, w in enumerate(windows):
        per_term = [s["buckets"][idx]["count"] for s in series]
        ok = [c for c in per_term if c is not None]
        combined_buckets.append(
            {
                "period": w.label,
                "count": sum(ok) if ok else None,
                "window": {"start": w.start.isoformat(), "end": w.end.isoformat()},
                "partial": w.partial,
                "detail": None if len(ok) == len(per_term) else "partial: some terms failed",
            }
        )
    combined = summarize(
        combined_buckets,
        {
            "term": None,
            "terms": terms,
            "note": "sum across terms; a package matching two terms is double-counted",
        },
    )

    partials = [w.label for w in windows if w.partial]
    if partials:
        w = next(w for w in windows if w.partial)
        notes.append(
            f"{w.label} is year-to-date ({w.days} days) and is excluded from slope/shape"
        )
    if undersampled_terms:
        reason = (
            f"{undersampled_terms} term(s) had more matches than --candidates-per-term "
            f"({args.candidates_per_term}) examined — counts are a floor from the "
            "top-ranked candidates, not an exhaustive census; see SAMPLING CAVEAT"
        )
        notes.append(reason)
        degrade_reasons.append(reason)
    notes.append("classifier: gh_history (local), shared with the GitHub supply-side series")

    payload = {
        "tool": "npm_history",
        "generated_utc": int(time.time()),
        "params": {
            "terms": terms,
            "years": years,
            "years_requested": args.years,
            "candidates_per_term": args.candidates_per_term,
            "bucket": "calendar-year",
            "pace_seconds": pace,
            "max_requests": args.max_requests,
            "authenticated": False,
        },
        "series": series,
        "combined": combined,
        "requests_made": client.requests_made,
        "note": "; ".join(notes),
        "source_health": [health(client, series, degrade_reasons)],
    }

    text = _dump_strict_json(payload)
    sys.stdout.write(text + "\n")

    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        log(f"[info] wrote {out}")

    status = payload["source_health"][0]["status"]
    log(f"[done] {client.requests_made} request(s); source_health={status}")
    return 1 if status == "unavailable" else 0


if __name__ == "__main__":
    sys.exit(main())
