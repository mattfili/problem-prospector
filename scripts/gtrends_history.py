#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["trendspyg>=1.1,<2"]
# ///
"""Google Trends interest-over-time, shaped for the retro-trend stage.

WHY THIS EXISTS
---------------
The historian panel of an OpportunityCard (`retro_trend` in docs/CONTRACTS.md)
has to answer one question: is this pain *emerging*, *persistent*, or *fading*?
`hn_history.py` and `gh_history.py` answer it from discussion volume — how many
people complained on HN, how many repos got created. Google Trends answers it
from a different and complementary axis: **search demand**. Complaining about a
problem and going looking for a solution are not the same act, and a pain whose
discussion is flat while its search demand climbs is a very different
opportunity from one where both are flat. This is the third leg, not a duplicate.

It emits the same `series / slope_pct_per_year / shape / coverage` vocabulary as
the other history scripts — and binds to `hn_history.classify_shape` at runtime
rather than reimplementing it, so the three sources cannot drift into meaning
different things by the same word. The binding actually used is reported in the
output under `shape_classifier`.

THE INTERPRETATION TRAP
-----------------------
Google Trends does **not** return search volume. It returns a relative index,
normalized 0-100 against the peak *inside the request you made*. A 40 is not
forty of anything, and two series fetched in separate requests are not on the
same scale. The output says so in a top-level `units` field, again per-series,
and again in `notes` — three times, because this is the single most common way
Trends data gets misread downstream.

TRANSPORT
---------
`trendspyg` (verified live, no API key, no account, no login) drives a local
headless Chrome against the public Explore page and reads the same
`widgetdata/multiline` payload the page itself reads. Consequences:
  * Chrome must be installed locally. ChromeDriver is auto-managed by Selenium.
  * ~15-40s per browser load. This path is for analysis, never for polling.
  * The User-Agent on the wire is Chrome's own, set by trendspyg. We do not
    control it and will not forge one into somebody else's browser session;
    tool identity travels in the output's `tool` field instead.

Google throttles Explore aggressively. A persistent throttle is recorded as
`unavailable` and circuit-breaks the run. It is NEVER reported as "no search
interest" — keeping those two apart is the whole job of `source_health`.

PIPELINE POSITION
-----------------
    inputs.json (matrix[].queries)
      -> gtrends_history.py --query ... --out runs/<slug>/gtrends_history.json
      -> historian agent -> cards/<cluster_id>.json : retro_trend.series[]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, NamedTuple, Sequence

TOOL = "problem-prospector/0.1 (+https://github.com/mattfili/problem-prospector)"
SOURCE = "google-trends"

# Google Trends date-range strings. Deliberately only three windows: the
# retro-trend stage is asking about multi-year persistence, and the sub-quarterly
# ranges Trends offers ("now 7-d", "now 1-H") are news-cycle noise at this
# altitude.
WINDOWS: dict[str, str] = {
    "5y": "today 5-y",  # weekly points, ~262 of them
    "12m": "today 12-m",  # weekly points, ~52
    "all": "all",  # monthly points back to 2004
}

# Bucket granularity per window, chosen so every window yields ~4-12 buckets —
# enough for hn_history's spiky test (needs >= 4) without smearing the trend.
# 5y -> half-years reproduces the "2022H1" period labels in CONTRACTS.md §4, so
# a Trends series can be interleaved with the HN one bucket for bucket.
BUCKETING: dict[str, str] = {"5y": "half-year", "12m": "quarter", "all": "year"}
BUCKETS_PER_YEAR: dict[str, int] = {"half-year": 2, "quarter": 4, "year": 1}

# Google's comparison URL puts 2-5 terms on one shared 0-100 scale in a single
# browser load. Using it is both cheaper (1 load instead of N) and more correct
# for cross-term reads, at the cost of the domination effect handled below.
MAX_COMPARISON_TERMS = 5

# Gap between browser loads. Measured 2026-07-31: a successful single-keyword
# load followed ~60s later by a 3-keyword comparison load was throttled, so
# Google's Explore cooldown for one IP runs in tens of seconds, not single
# digits. 20s is cheap next to a 20-40s load and materially improves the odds
# that a second group lands at all. Grouping 2-5 terms per load is the primary
# defense; this gap is the secondary one.
POLITE_GAP_S = 20.0

# The shared shape vocabulary, owned by hn_history.py. `None` is also a legal
# value and often the right one: "no shape claimed" beats a guessed shape.
SHAPES = ("emerging", "accelerating", "persistent-flat", "declining", "spiky-episodic")

UNITS_NOTE = (
    "RELATIVE interest index, 0-100, normalized against the peak within each "
    "request. NOT search volume: a 40 is not 40 searches, 40 users, or 40 of "
    "anything. Values are comparable only within one series, or across series "
    "sharing a comparison_group. Never sum, average, or rank across groups, "
    "geos, or windows."
)

# --- Trends-specific coverage thresholds ----------------------------------- #
# Coverage here is deliberately NOT hn_history.classify_coverage. That function
# reads coverage off total volume, which is the right basis for mention counts
# and meaningless for a normalized index: every series peaks at 100 regardless
# of whether the term gets ten searches a year or ten million. For Trends,
# coverage is a function of how many points Google returned and how much of the
# series is pinned at its reporting floor.
MIN_POINTS_FOR_GOOD = 8  # below this, a slope is arithmetic rather than evidence
THIN_NONZERO_FRACTION = 0.25  # mostly-zero series sit at/below the reporting floor
DOMINATED_PEAK = 5  # this low a peak inside a comparison group means the scale was stolen
# Sub-bucket spike test, run at full weekly resolution because the shared
# classifier's bucket-share test cannot see a spike narrower than one bucket.
# 4x off the median with a collapsed linear fit is an event, not a level.
SPIKE_RATIO = 4.0
SPIKE_R2 = 0.30

# Fallback thresholds, used ONLY when hn_history.py cannot be bound. Values are
# copied from hn_history.SHAPE_THRESHOLDS; the output records which binding ran
# so a drifted copy can never masquerade as the canonical one.
FALLBACK_SHAPE_THRESHOLDS: dict[str, float] = {
    "min_total_for_shape": 5,
    "spiky_max_bucket_share": 0.50,
    "spiky_min_buckets": 4,
    "emerging_first_half_share_max": 0.15,
    "flat_band_pct_per_year": 15.0,
    "accelerating_slope_pct_per_year": 60.0,
    "declining_slope_pct_per_year": -20.0,
}


def log(msg: str) -> None:
    """Diagnostics go to stderr; stdout is reserved for parseable JSON."""
    print(f"[gtrends_history] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# Shape classification — bound to hn_history.py, the vocabulary's canonical home
# --------------------------------------------------------------------------- #


class Classifier(NamedTuple):
    slope_pct_per_year: Callable[[list[int], int], float | None]
    classify_shape: Callable[[list[int], float | None], tuple[str | None, dict[str, Any]]]
    binding: str


def _ols_slope_per_bucket(counts: Sequence[float]) -> float | None:
    """Least-squares slope per bucket index. None when undefined."""
    n = len(counts)
    if n < 2:
        return None
    mean_x = (n - 1) / 2.0
    mean_y = sum(counts) / n
    sxx = sum((i - mean_x) ** 2 for i in range(n))
    if sxx == 0:
        return None
    sxy = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(counts))
    return sxy / sxx


def _fallback_slope_pct_per_year(counts: list[int], buckets_per_year: int) -> float | None:
    slope = _ols_slope_per_bucket(counts)
    if slope is None:
        return None
    mean_y = sum(counts) / len(counts)
    if mean_y <= 0:
        return None
    return round(slope * buckets_per_year / mean_y * 100.0, 1)


def _fallback_classify_shape(
    counts: list[int], slope_pct: float | None
) -> tuple[str | None, dict[str, Any]]:
    """Compact mirror of hn_history.classify_shape. Order matters (see there)."""
    t = FALLBACK_SHAPE_THRESHOLDS
    total = sum(counts)
    evidence: dict[str, Any] = {
        "total_in_slope_window": total,
        "buckets_used": len(counts),
    }
    if total < t["min_total_for_shape"]:
        evidence["reason"] = f"total {total} below min_total_for_shape; no shape claimed"
        return None, evidence
    max_share = max(counts) / total
    evidence["max_bucket_share"] = round(max_share, 3)
    if len(counts) >= t["spiky_min_buckets"] and max_share > t["spiky_max_bucket_share"]:
        evidence["reason"] = f"one bucket holds {max_share:.0%}; event, not a level"
        return "spiky-episodic", evidence
    mid = len(counts) // 2
    first_half_share = (sum(counts[:mid]) / total) if mid else None
    evidence["first_half_share"] = (
        round(first_half_share, 3) if first_half_share is not None else None
    )
    if slope_pct is None:
        evidence["reason"] = "slope undefined; treated as flat"
        return "persistent-flat", evidence
    if (
        first_half_share is not None
        and first_half_share <= t["emerging_first_half_share_max"]
        and slope_pct > t["flat_band_pct_per_year"]
    ):
        evidence["reason"] = (
            f"first half holds only {first_half_share:.0%} with slope "
            f"{slope_pct:+.1f}%/yr; growth from a near-zero base"
        )
        return "emerging", evidence
    if slope_pct >= t["accelerating_slope_pct_per_year"]:
        evidence["reason"] = f"slope {slope_pct:+.1f}%/yr from an existing base"
        return "accelerating", evidence
    if slope_pct <= t["declining_slope_pct_per_year"]:
        evidence["reason"] = f"slope {slope_pct:+.1f}%/yr sustained decay"
        return "declining", evidence
    evidence["reason"] = f"slope {slope_pct:+.1f}%/yr; durable level with drift"
    return "persistent-flat", evidence


def load_classifier() -> Classifier:
    """Bind the shape vocabulary to hn_history.py when that sibling is importable.

    "emerging" has to mean the same thing whether it came from HN mentions or
    from Google searches, so hn_history owns the definition and this script
    borrows it instead of holding a second opinion. Both borrowed functions are
    probed with a known input and the result validated against SHAPES, so a
    sibling whose signature has changed fails closed to the local mirror rather
    than returning something plausible-looking and wrong. Whichever binding wins
    is reported in the output.
    """
    local = Classifier(
        _fallback_slope_pct_per_year, _fallback_classify_shape, "local-mirror"
    )
    sibling = Path(__file__).resolve().parent / "hn_history.py"
    if not sibling.exists():
        return local._replace(binding="local-mirror (hn_history.py not present)")
    try:
        spec = importlib.util.spec_from_file_location("hn_history", sibling)
        if spec is None or spec.loader is None:
            return local._replace(binding="local-mirror (hn_history.py not loadable)")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        slope_fn = getattr(module, "slope_pct_per_year", None)
        shape_fn = getattr(module, "classify_shape", None)
        if not callable(slope_fn) or not callable(shape_fn):
            return local._replace(binding="local-mirror (hn_history API absent)")
        probe_slope = slope_fn([1, 2, 3, 6], 2)
        probe_shape, probe_evidence = shape_fn([1, 2, 3, 6], probe_slope)
        if not isinstance(probe_slope, float) or probe_shape not in SHAPES:
            return local._replace(
                binding=f"local-mirror (hn_history probe returned {probe_shape!r})"
            )
        if not isinstance(probe_evidence, dict):
            return local._replace(binding="local-mirror (hn_history evidence not a dict)")
        return Classifier(slope_fn, shape_fn, "hn_history.classify_shape")
    except Exception as exc:  # noqa: BLE001 - any failure means: use the mirror
        return local._replace(binding=f"local-mirror ({type(exc).__name__} on hn_history)")


# --------------------------------------------------------------------------- #
# Raw-point diagnostics
# --------------------------------------------------------------------------- #


def _parse_date(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fit_points(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Diagnostics from the full-resolution series, before bucketing.

    The headline slope/shape come from the bucketed series (so they are
    comparable with the other history scripts); these are the finer-grained
    checks that say how much to trust them — dispersion, spikiness, how much of
    the series is pinned at zero, and a raw-resolution slope for cross-check.

    Partial points are excluded throughout. Trends marks the trailing bucket
    `is_partial` while the period is still in progress; that point always reads
    low, and on a 5y weekly series one of them is enough to tip a flat term into
    "declining". A real trap, not a rounding concern.
    """
    usable = [p for p in points if not p["is_partial"]]
    n = len(usable)
    out: dict[str, Any] = {
        "n_points_total": len(points),
        "n_points_fitted": n,
        "n_points_partial": len(points) - n,
        "mean_index": None,
        "max_index": None,
        "nonzero_fraction": None,
        "spike_ratio": None,
        "r_squared": None,
        "slope_pct_per_year_raw_points": None,
        "first_date": None,
        "last_date": None,
        "first_nonzero_date": None,
    }
    if n == 0:
        return out

    values = [float(p["index"]) for p in usable]
    out["first_date"] = usable[0]["date"]
    out["last_date"] = usable[-1]["date"]
    # Where the non-zero history starts is the difference between "zero for four
    # years then took off" and "scattered noise across the whole window". Both
    # look identical in nonzero_fraction alone, so record it.
    out["first_nonzero_date"] = next(
        (p["date"] for p in usable if p["index"] > 0), None
    )
    mean_index = sum(values) / n
    out["mean_index"] = round(mean_index, 2)
    out["max_index"] = int(max(values))
    out["nonzero_fraction"] = round(sum(1 for v in values if v > 0) / n, 3)

    median = statistics.median(values)
    if median > 0:
        # max/median: how far the peak stands off the typical level. Complements
        # the bucket-share spike test, which can miss a spike that straddles a
        # bucket boundary.
        out["spike_ratio"] = round(max(values) / median, 2)

    if n >= 2 and mean_index > 0:
        t0 = _parse_date(usable[0]["date"])
        years = [
            (_parse_date(p["date"]) - t0).total_seconds() / 31_556_952.0 for p in usable
        ]
        mean_x = sum(years) / n
        sxx = sum((x - mean_x) ** 2 for x in years)
        if sxx > 0:
            sxy = sum((x - mean_x) * (y - mean_index) for x, y in zip(years, values))
            slope = sxy / sxx
            intercept = mean_index - slope * mean_x
            ss_tot = sum((y - mean_index) ** 2 for y in values)
            ss_res = sum(
                (y - (slope * x + intercept)) ** 2 for x, y in zip(years, values)
            )
            out["r_squared"] = round(1.0 - ss_res / ss_tot, 3) if ss_tot > 0 else 0.0
            out["slope_pct_per_year_raw_points"] = round(100.0 * slope / mean_index, 1)
    return out


# --------------------------------------------------------------------------- #
# Bucketing
# --------------------------------------------------------------------------- #


def _period_label(dt: datetime, granularity: str) -> str:
    if granularity == "year":
        return f"{dt.year}"
    if granularity == "quarter":
        return f"{dt.year}Q{(dt.month - 1) // 3 + 1}"
    return f"{dt.year}H{1 if dt.month <= 6 else 2}"


def bucketize(points: list[dict[str, Any]], granularity: str) -> list[dict[str, Any]]:
    """Aggregate raw points into contract-shaped `{period, count}` buckets.

    `count` carries the *mean relative index* over the period, not a count of
    anything. The field name is fixed by CONTRACTS.md §4 so this series can sit
    next to the HN and GitHub ones in `retro_trend.series[]`; `units` on the
    series says what the number actually is.

    Rounding the mean to an int is not laziness: it is what makes a term hovering
    at Google's reporting floor round to 0 and therefore fall below the shared
    classifier's `min_total_for_shape` gate, i.e. get no shape claimed at all.
    That is the correct answer for such a term.

    Partial points are excluded from the mean and counted separately. A bucket
    with no usable points gets `count: null`, never a fabricated 0.
    """
    order: list[str] = []
    groups: dict[str, dict[str, Any]] = {}
    for point in points:
        label = _period_label(_parse_date(point["date"]), granularity)
        if label not in groups:
            order.append(label)
            groups[label] = {"values": [], "partial": 0}
        if point["is_partial"]:
            groups[label]["partial"] += 1
        else:
            groups[label]["values"].append(float(point["index"]))

    buckets = []
    for label in order:
        values = groups[label]["values"]
        buckets.append(
            {
                "period": label,
                "count": round(sum(values) / len(values)) if values else None,
                "n_points": len(values),
                "n_partial_points": groups[label]["partial"],
            }
        )
    return buckets


# --------------------------------------------------------------------------- #
# Coverage
# --------------------------------------------------------------------------- #


def assess_coverage(
    fit: dict[str, Any], peers: list[str], n_usable_buckets: int
) -> tuple[str, list[str]]:
    """Return (coverage, notes). Coverage describes the *series*, not the term.

    A term with genuinely low search demand is a finding. A series too sparse to
    read a shape off is a coverage problem. These get conflated constantly, so
    they are separated here and both are said out loud.
    """
    notes: list[str] = []
    if fit["n_points_total"] == 0:
        return "none", ["Google Trends returned an empty series for this term."]

    coverage = "good"
    n = fit["n_points_fitted"]
    if n < MIN_POINTS_FOR_GOOD or n_usable_buckets < 2:
        coverage = "thin"
        notes.append(
            f"Only {n} non-partial point(s) across {n_usable_buckets} usable "
            "bucket(s); too few to read a trend from."
        )
    nonzero = fit["nonzero_fraction"]
    if nonzero is not None and nonzero < THIN_NONZERO_FRACTION:
        coverage = "thin"
        notes.append(
            f"{round((1 - nonzero) * 100, 1)}% of points are 0 — the term sits at or "
            "below Google's reporting floor for this geo/window. Low search demand "
            "is itself a finding; a flat-zero line is not a trend."
        )
    if peers and (fit["max_index"] or 0) < DOMINATED_PEAK:
        coverage = "thin"
        notes.append(
            f"Peak index {fit['max_index']} on a 0-100 scale set by a stronger "
            f"comparison peer ({', '.join(peers)}), so this series is compressed "
            "against the floor. Re-run with --no-compare for an independently "
            "normalized read."
        )
    if fit["n_points_partial"]:
        notes.append(
            f"{fit['n_points_partial']} partial (in-progress) point(s) excluded from "
            "the fit; they read low by construction."
        )
    return coverage, notes


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #


# Throttle phrases seen in the wild. Verified live 2026-07-31: Google can
# rate-limit the widget replay *after* the chart has already rendered, which
# trendspyg surfaces as DownloadError("...the widget request was rate-limited on
# replay..."), not as RateLimitError. Text matching is how a throttle wearing the
# wrong exception class still gets circuit-broken instead of being retried as a
# transient parse failure.
#
# Phrases must be un-negated. An earlier version matched the bare noun
# "rate-limit", which also matches trendspyg's DOM-change message "...and no
# rate-limit message was shown", turning an explicit "this was NOT a throttle"
# into a throttle report. Only past-participle / status forms belong here.
THROTTLE_MARKERS = ("rate-limited", "rate limited", "429", "too many requests")


def classify_error(trendspyg: Any, exc: BaseException) -> str:
    """Bucket a fetch exception into rate_limited | browser_error | <ClassName>.

    Exception class wins over text. trendspyg already separates the two cases it
    can tell apart — RateLimitError when Google showed a throttle message,
    BrowserError when the chart simply never rendered — so re-reading its prose
    could only overrule a judgement it made with more information than we have.
    Text matching is reserved for the classes that carry no verdict of their own.
    """
    if isinstance(exc, trendspyg.RateLimitError):
        return "rate_limited"
    if isinstance(exc, trendspyg.BrowserError):
        return "browser_error"
    if any(m in str(exc).lower() for m in THROTTLE_MARKERS):
        return "rate_limited"
    return type(exc).__name__


def _normalize_points(raw: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """trendspyg point -> our point. `index`, not `count`: it is not countable."""
    return [
        {
            "date": str(p["date"]),
            "index": int(p["value"]),
            "is_partial": bool(p.get("is_partial", False)),
        }
        for p in raw
    ]


def plan_groups(queries: list[str], compare: bool) -> list[list[str]]:
    """Split terms into comparison groups of 2-5, or singletons.

    Balanced rather than greedy (6 terms -> 3+3, not 5+1) for two reasons: a
    leftover group of one wastes a whole 30-second browser load, and every extra
    term in a group is another chance for a dominant peer to flatten the others
    against the floor. Terms containing a comma go to the single-term path
    because Google's comparison URL uses the comma as its separator.
    """
    if not compare:
        return [[q] for q in queries]

    comparable = [q for q in queries if "," not in q]
    groups: list[list[str]] = [[q] for q in queries if "," in q]

    if len(comparable) == 1:
        groups.append([comparable[0]])
    elif comparable:
        n_groups = -(-len(comparable) // MAX_COMPARISON_TERMS)  # ceil
        chunks: list[list[str]] = [[] for _ in range(n_groups)]
        for i, term in enumerate(comparable):
            chunks[i % n_groups].append(term)
        groups.extend(c for c in chunks if c)
    return groups


def fetch_group(
    trendspyg: Any,
    group: list[str],
    geo: str,
    timeframe: str,
    headless: bool,
    max_retries: int,
    retry_wait: float,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch one browser load. Returns {query: points}. Raises trendspyg errors."""
    if len(group) == 1:
        raw = trendspyg.download_google_trends_interest_over_time(
            keyword=group[0],
            geo=geo,
            timeframe=timeframe,
            max_retries=max_retries,
            retry_wait=retry_wait,
            headless=headless,
        )
        return {group[0]: _normalize_points(raw)}

    envelope = trendspyg.download_google_trends_comparison(
        keywords=group,
        geo=geo,
        timeframe=timeframe,
        max_retries=max_retries,
        retry_wait=retry_wait,
        headless=headless,
        include_geo=False,  # only the time series is needed; skips an extra fetch
    )
    keywords: list[str] = list(envelope.get("keywords") or group)
    series: dict[str, list[dict[str, Any]]] = {k: [] for k in keywords}
    for point in envelope.get("interest_over_time") or []:
        values = point.get("values") or {}
        for keyword in keywords:
            if keyword not in values:
                continue  # a missing value is skipped, never zero-filled by us
            series[keyword].append(
                {
                    "date": str(point["date"]),
                    "index": int(values[keyword]),
                    "is_partial": bool(point.get("is_partial", False)),
                }
            )
    if len(keywords) == len(group):
        # trendspyg only strips whitespace and preserves order, so zipping back
        # onto the caller's exact strings is safe and lets the output echo the
        # input verbatim.
        series = {ours: series[theirs] for ours, theirs in zip(group, keywords)}
    return series


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gtrends_history.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Google Trends interest-over-time for the retro-trend stage of\n"
            "problem-prospector.\n\n"
            "Answers 'is search demand for this pain emerging, persistent, or\n"
            "fading?' in the same series/slope_pct_per_year/shape/coverage\n"
            "vocabulary as hn_history.py and gh_history.py, ready to drop into\n"
            "cards/<cluster_id>.json -> retro_trend.series[].\n\n"
            "No API key, no account. Drives a local headless Chrome via trendspyg\n"
            "against the public Explore page, so Chrome must be installed and each\n"
            "browser load takes 15-40s.\n\n"
            "IMPORTANT: Trends returns a RELATIVE 0-100 index, not search volume.\n"
            "A 40 is not forty of anything. See the `units` field in the output."
        ),
        epilog=(
            "Examples:\n"
            "  # one term, default 5-year US window\n"
            "  uv run --quiet scripts/gtrends_history.py --query 'permit software'\n\n"
            "  # 2-5 terms share ONE browser load and ONE 0-100 scale\n"
            "  uv run --quiet scripts/gtrends_history.py \\\n"
            "      --query 'permit software' --query 'code enforcement software' \\\n"
            "      --query '311 software' --out runs/my-slug/gtrends_history.json\n\n"
            "  # worldwide, full history back to 2004\n"
            "  uv run --quiet scripts/gtrends_history.py --query 'rag pipeline' \\\n"
            "      --geo '' --window all\n\n"
            "  # a weak term a strong peer flattened: re-run it on its own scale\n"
            "  uv run --quiet scripts/gtrends_history.py --query 'niche term' --no-compare\n\n"
            "Google throttles Explore hard. If a run reports `unavailable`, wait\n"
            "2-5 minutes rather than re-running immediately.\n\n"
            "Exit codes: 0 = at least one series came back (an empty series is a\n"
            "valid finding); 1 = nothing usable was fetched. Either way, read\n"
            "source_health before believing the numbers."
        ),
    )
    parser.add_argument(
        "--query",
        action="append",
        metavar="TERM",
        required=True,
        help="Search term. Repeatable. Terms are batched 2-5 per browser load "
        "onto one shared 0-100 scale (see --no-compare).",
    )
    parser.add_argument(
        "--window",
        choices=sorted(WINDOWS),
        default="5y",
        help="History depth: 5y (default, weekly points), 12m, all (since 2004).",
    )
    parser.add_argument(
        "--geo",
        default="US",
        help="Google geo code: US (default), GB, DE, US-CA. Pass '' for worldwide.",
    )
    parser.add_argument(
        "--no-compare",
        action="store_true",
        help="One browser load per term, each normalized to its own 0-100 scale. "
        "Slower and not cross-comparable, but recovers the shape of a low-volume "
        "term that a strong peer flattened to near-zero.",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="Also persist the JSON here (parent dirs created). Conventionally "
        "runs/<slug>/gtrends_history.json. JSON still goes to stdout.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=4,
        help="Chart-load attempts past Google's soft-throttle before giving up "
        "(default 4). Bounded on purpose: once a throttle is persistent, more "
        "attempts are probing, not patience.",
    )
    parser.add_argument(
        "--retry-wait",
        type=float,
        default=8.0,
        help="Seconds to watch the chart per attempt (default 8.0). Worst case "
        "per group is roughly max-retries * (retry-wait + 2s).",
    )
    parser.add_argument(
        "--visible",
        action="store_true",
        help="Run Chrome visibly instead of headless. Debugging aid for when "
        "Google changes the Explore DOM.",
    )
    return parser


def build_series_entry(
    term: str,
    points: list[dict[str, Any]],
    peers: list[str],
    group_index: int | None,
    granularity: str,
    window: str,
    timeframe: str,
    geo: str,
    classifier: Classifier,
) -> dict[str, Any]:
    """Turn one term's raw points into a contract-shaped series entry."""
    fit = fit_points(points)
    buckets = bucketize(points, granularity)

    # Only buckets with usable (non-partial) points can carry a count; feeding a
    # null through as 0 would invent a period of zero interest.
    counts = [b["count"] for b in buckets if b["count"] is not None]
    coverage, notes = assess_coverage(fit, peers, len(counts))
    if counts:
        slope_pct = classifier.slope_pct_per_year(counts, BUCKETS_PER_YEAR[granularity])
        shape, evidence = classifier.classify_shape(counts, slope_pct)
    else:
        slope_pct, shape, evidence = None, None, {"reason": "no usable buckets"}
    evidence = dict(evidence)

    # ---- Trends-specific adjustments to the shared verdict -----------------
    # The shared classifier is calibrated for mention counts in half-year
    # buckets. Two things differ here and both are corrected explicitly rather
    # than silently: its volume gate cannot see a normalized index (every series
    # peaks at 100 regardless of real demand), and its spike test runs on bucket
    # totals, which is coarser than the weekly resolution Trends actually gives.

    # (a) No support. hn_history's gate is `total >= 5`, which an index series
    #     clears trivially — a single bucket averaging 20 passes it and comes
    #     back "persistent-flat" off one data point. Point and bucket counts are
    #     the real support test for this source.
    if shape is not None and (fit["n_points_fitted"] < MIN_POINTS_FOR_GOOD or len(counts) < 2):
        evidence["overridden"] = (
            f"shape {shape!r} withdrawn: {fit['n_points_fitted']} non-partial "
            f"point(s) in {len(counts)} usable bucket(s) is not enough support to "
            "claim a shape"
        )
        shape, slope_pct = None, None

    # (b) Reporting floor. A series pinned at 0 can clear the volume gate on a
    #     couple of stray non-zero buckets and come back "emerging" off a slope
    #     of several hundred percent. That is exactly the fabrication this tool
    #     exists not to commit. Withdrawing the shape is not the same as having
    #     nothing to say, though: the date the non-zero history starts is itself
    #     the finding, so it goes in the message rather than being left for the
    #     reader to dig out of raw_points.
    nonzero = fit["nonzero_fraction"]
    if shape is not None and nonzero is not None and nonzero < THIN_NONZERO_FRACTION:
        onset = fit["first_nonzero_date"]
        where = (
            f"non-zero only from {onset[:10]} onward — read `buckets` directly"
            if onset
            else "no non-zero point anywhere in the window"
        )
        evidence["overridden"] = (
            f"shape {shape!r} (slope {slope_pct}%/yr) withdrawn: only "
            f"{round(nonzero * 100, 1)}% of points are non-zero, so the fit is against "
            f"Google's reporting floor rather than against a level; {where}"
        )
        shape, slope_pct = None, None

    # (c) Sub-bucket spike. Averaging weeks into half-years smooths a two-week
    #     news spike down to a mild slope: a series of 3s with one 90 in it fits
    #     inside the flat band once bucketed. The raw-resolution max/median with
    #     a collapsed linear fit sees it, so escalate to the shared vocabulary's
    #     spiky-episodic and keep the original verdict in evidence.
    if (
        shape is not None
        and fit["spike_ratio"] is not None
        and fit["spike_ratio"] >= SPIKE_RATIO
        and (fit["r_squared"] or 0.0) < SPIKE_R2
    ):
        evidence["overridden"] = (
            f"shape {shape!r} escalated to 'spiky-episodic': at full weekly "
            f"resolution the peak stands {fit['spike_ratio']}x off the median with "
            f"r^2 {fit['r_squared']} — an event the bucket means smoothed away"
        )
        shape = "spiky-episodic"

    if "overridden" in evidence:
        notes.append(evidence["overridden"] + ".")
    if shape is None:
        notes.append(f"No shape claimed: {evidence.get('reason', 'unknown reason')}.")

    return {
        "query": term,
        "source": SOURCE,
        "geo": geo or None,
        "geo_scope": geo or "worldwide",
        "window": window,
        "timeframe": timeframe,
        "units": "relative-index-0-100",
        "comparison_group": group_index if peers else None,
        "comparison_peers": peers,
        "coverage": coverage,
        "shape": shape,
        "slope_pct_per_year": slope_pct,
        "shape_evidence": evidence,
        "bucket_granularity": granularity,
        "buckets": buckets,
        "fit": fit,
        "raw_points": points,
        "note": " ".join(notes) if notes else None,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Dedupe case-insensitively, preserving order and original casing: Google
    # treats comparison terms case-insensitively and rejects the whole group on a
    # duplicate, so ["Permit", "permit"] would fail the entire batch.
    queries: list[str] = []
    seen: set[str] = set()
    for raw in args.query:
        term = raw.strip()
        if term and term.casefold() not in seen:
            seen.add(term.casefold())
            queries.append(term)
    if not queries:
        log("no non-empty --query terms given")
        return 1

    try:
        import trendspyg  # noqa: PLC0415 - after argparse, so --help never needs it
    except ImportError as exc:
        log(f"trendspyg unavailable: {exc}")
        log("Run via `uv run` so the PEP 723 metadata installs dependencies.")
        return 1

    classifier = load_classifier()
    if classifier.binding.startswith("local-mirror"):
        log(f"WARNING: shape classifier is the {classifier.binding}, not hn_history")

    timeframe = WINDOWS[args.window]
    geo = args.geo.strip()
    granularity = BUCKETING[args.window]
    groups = plan_groups(queries, compare=not args.no_compare)
    log(
        f"{len(queries)} term(s) in {len(groups)} browser load(s) | "
        f"geo={geo or 'worldwide'} timeframe={timeframe!r} | 15-40s per load"
    )

    results: dict[str, list[dict[str, Any]]] = {}
    group_of: dict[str, int] = {}
    failures: list[dict[str, Any]] = []
    health: list[dict[str, str]] = []
    circuit_broken = False

    for index, group in enumerate(groups, start=1):
        if circuit_broken:
            for term in group:
                failures.append(
                    {
                        "query": term,
                        "error_class": "not_attempted",
                        "detail": "skipped after circuit-break on an earlier group",
                    }
                )
            continue

        if index > 1:
            log(f"pausing {POLITE_GAP_S:.0f}s before the next browser load")
            time.sleep(POLITE_GAP_S)

        label = " + ".join(repr(t) for t in group)
        log(f"group {index}/{len(groups)}: {label}")
        started = time.monotonic()
        try:
            points_by_query = fetch_group(
                trendspyg,
                group,
                geo=geo,
                timeframe=timeframe,
                headless=not args.visible,
                max_retries=args.max_retries,
                retry_wait=args.retry_wait,
            )
        except Exception as exc:  # noqa: BLE001 - dispatched by classify_error below
            kind = classify_error(trendspyg, exc)
            detail = str(exc).splitlines()[0]

            if kind == "rate_limited":
                # Circuit-break; do not retry-probe. trendspyg has already sat
                # through max_retries chart loads on this URL, so a throttle here
                # has already persisted. Google throttles by IP, so the remaining
                # groups would fail identically: continuing would be probing.
                circuit_broken = True
                log(f"RATE LIMITED — circuit-breaking Google Trends: {detail}")
                log("HINT: wait 2-5 minutes, then re-run with fewer --query terms.")
                health.append(
                    {
                        "source": SOURCE,
                        "status": "unavailable",
                        "detail": (
                            f"Google Trends throttled on group {index} ({label}) after "
                            f"{args.max_retries} chart-load attempts: {detail} "
                            "Remaining groups were not attempted. This is a throttle, "
                            "NOT an absence of search interest."
                        ),
                    }
                )
            elif kind == "browser_error":
                circuit_broken = True
                log(f"BROWSER ERROR — circuit-breaking Google Trends: {detail}")
                if "start Chrome" in str(exc) or "WebDriver" in str(exc):
                    log("HINT: this path needs Google Chrome installed locally.")
                    log("  macOS:  brew install --cask google-chrome")
                    log("  Linux:  install google-chrome-stable from your distro/Google.")
                    log("  ChromeDriver itself is auto-managed by Selenium; no setup.")
                else:
                    # The chart never rendered and Google showed no throttle
                    # message. Two causes produce this and they are not
                    # distinguishable from here, so name both rather than
                    # asserting one. Observed live 2026-07-31 minutes after six
                    # terms fetched cleanly, which makes "the DOM changed"
                    # implausible in that instance — under load Google serves
                    # automation a stripped page with no explanation at all.
                    log("HINT: the chart never rendered and no throttle message appeared.")
                    log("  Most often: Google is serving automation a stripped page")
                    log("  under load. Wait 5+ minutes and re-run before anything else.")
                    log("  If it persists: the Explore DOM may have changed — bump the")
                    log("  trendspyg pin in the PEP 723 block, then use --visible to look.")
                health.append(
                    {
                        "source": SOURCE,
                        "status": "unavailable",
                        "detail": (
                            f"browser layer failed on group {index}: {detail} "
                            "Remaining groups skipped. Cause is ambiguous between a "
                            "load-shed stripped page and an Explore DOM change; this "
                            "is NOT evidence about search interest either way."
                        ),
                    }
                )
            elif kind == "InvalidParameterError":
                # A rejected --geo or --window is our caller's mistake, not a
                # Google outage, and every remaining group carries the same bad
                # parameter — so say which it is and stop rather than repeating it.
                circuit_broken = True
                log(f"INVALID ARGUMENT (not a source failure): {detail}")
                log("HINT: check --geo and --window; nothing was fetched.")
                health.append(
                    {
                        "source": SOURCE,
                        "status": "unavailable",
                        "detail": (
                            f"request rejected before any fetch: {detail} This is an "
                            "argument error in the caller, not a Google Trends outage."
                        ),
                    }
                )
            else:
                # Genuinely transient (parse hiccup, DOM race). Degrade this group
                # and let the remaining groups run.
                log(f"group {index} failed ({kind}): {detail}")
                health.append(
                    {
                        "source": SOURCE,
                        "status": "degraded",
                        "detail": f"group {index} ({label}) failed with {kind}: {detail}",
                    }
                )

            for term in group:
                failures.append({"query": term, "error_class": kind, "detail": detail})
            continue

        elapsed = time.monotonic() - started
        for term in group:
            results[term] = points_by_query.get(term, [])
            group_of[term] = index
        counts = ", ".join(f"{t}={len(results[t])}pts" for t in group)
        log(f"group {index} ok in {elapsed:.0f}s: {counts}")

    series: list[dict[str, Any]] = []
    for term in queries:
        if term not in results:
            continue
        peers = [
            other
            for other in queries
            if other != term and group_of.get(other) == group_of.get(term)
        ]
        series.append(
            build_series_entry(
                term=term,
                points=results[term],
                peers=peers,
                group_index=group_of.get(term),
                granularity=granularity,
                window=args.window,
                timeframe=timeframe,
                geo=geo,
                classifier=classifier,
            )
        )

    if series and not failures:
        health.insert(
            0,
            {
                "source": SOURCE,
                "status": "ok",
                "detail": f"{len(series)}/{len(queries)} term(s) returned a series.",
            },
        )
    elif series:
        health.insert(
            0,
            {
                "source": SOURCE,
                "status": "degraded",
                "detail": (
                    f"{len(series)}/{len(queries)} term(s) returned a series; "
                    f"{len(failures)} failed to fetch (see `failures`). Failed terms "
                    "have NO entry in `series` — their absence is absence of data, "
                    "not absence of search interest."
                ),
            },
        )
    else:
        health.insert(
            0,
            {
                "source": SOURCE,
                "status": "unavailable",
                "detail": (
                    f"no term returned a series; all {len(queries)} fetch attempt(s) "
                    "failed (see `failures`). Do not read this as zero interest."
                ),
            },
        )

    payload: dict[str, Any] = {
        "tool": TOOL,
        "script": "gtrends_history.py",
        "source": SOURCE,
        "generated_utc": int(datetime.now(tz=timezone.utc).timestamp()),
        "units": UNITS_NOTE,
        "request": {
            "queries": queries,
            "window": args.window,
            "timeframe": timeframe,
            "geo": geo or None,
            "geo_scope": geo or "worldwide",
            "compare_mode": "independent" if args.no_compare else "grouped-comparison",
            "groups": groups,
            "bucket_granularity": granularity,
            "buckets_per_year": BUCKETS_PER_YEAR[granularity],
            "max_retries": args.max_retries,
            "retry_wait": args.retry_wait,
        },
        "shapes_possible": list(SHAPES) + [None],
        "shape_classifier": classifier.binding,
        "coverage_summary": {
            "requested": len(queries),
            "returned": len(series),
            "failed": len(failures),
        },
        "series": series,
        "failures": failures,
        "source_health": health,
        "notes": [
            "Values are a relative 0-100 index, never volume. See `units`.",
            "buckets[].count is the MEAN INDEX over the period, not a count of "
            "anything; the field name follows CONTRACTS.md so this series can sit "
            "beside the HN and GitHub ones in retro_trend.series[].",
            "Series sharing a comparison_group are on one scale and directly "
            "comparable. Series in different groups are NOT.",
            "slope_pct_per_year is percent of the series' own mean level per year, "
            "fitted on the bucketed series by the shared classifier named in "
            "shape_classifier. fit.slope_pct_per_year_raw_points is the same "
            "measure at full resolution; a large disagreement means a fragile trend.",
            "shape is null when no shape could be claimed; shape_evidence.reason "
            "says why. Null is a real answer, not a missing one.",
        ],
    }

    if args.out:
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        log(f"wrote {out_path}")

    print(json.dumps(payload, indent=2))
    return 0 if series else 1


if __name__ == "__main__":
    sys.exit(main())
