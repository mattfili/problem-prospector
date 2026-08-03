#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["requests"]
# ///
"""Backward-facing Hacker News mention counts for the retro-trend method.

WHY THIS EXISTS
---------------
A pain that looks hot today is only worth building against if it has *history*.
The retro-trend method asks the opposite question from a trend tool: not "is
this rising?" but "how long has this been true, and is the complaint volume
durable or was it one news cycle?" A five-year backward series separates:

  * a durable, boring, persistently-complained-about problem (buildable), from
  * a spike that a single HN front-page story manufactured (not demand).

This script produces the `hackernews` entry of CONTRACTS §4
`card.retro_trend.series`, plus the `shape` and `slope_pct_per_year` that the
historian agent copies into the card.

HOW IT FITS THE PIPELINE
------------------------
    clusters.json --(canonical pain keyphrases)--> hn_history.py
        --> retro_trend block --> cards/<cluster_id>.json  (historian panel)

It is deliberately *not* an evidence scout: it returns counts, not posts, so it
never writes `evidence/*.jsonl`. Use `hn_search.py` when you need the actual
items.

THE EFFICIENCY TRICK
--------------------
Algolia returns `nbHits` — the total number of matching documents for the
query — in the *first* response page. So one request with `hitsPerPage=1` per
time bucket yields the whole count for that bucket. We never paginate: 11
requests cover five years of half-year buckets for a keyphrase, instead of
potentially thousands of item fetches.

Two verified gotchas encoded below:
  1. `numericFilters` contains `>` and `<`. These MUST be URL-encoded, which
     means passing them through requests' `params=` dict. Naive string
     concatenation returns non-JSON.
  2. Algolia `tags` is AND-combined: `tags=story,comment` matches documents
     that are both a story and a comment, i.e. nothing (verified nbHits=0).
     The OR form is the parenthesised `tags=(story,comment)`.

HONESTY NOTES
-------------
  * Algolia reports `exhaustiveNbHits: false` for most non-trivial counts, so
    `nbHits` is an *estimate* of the total. Each bucket therefore carries
    `count_exhaustive`, and the query note says so when any bucket is
    approximate. Counts are used comparatively (bucket vs bucket), where an
    estimator applied uniformly is still informative.
  * A bucket whose fetch failed gets `count: null` and a recorded failure. It
    is never written as `0`. "We could not measure it" and "nobody said it" are
    different facts and the whole tool depends on not conflating them. The same
    rule applies at the series level: coverage `"none"` is a positive claim that
    HN is silent, so it is only made when most of the window answered. If the
    measured buckets are empty but too many failed, coverage is `null`.
  * The current calendar half/year is incomplete, so including it as a full
    data point would fake a decline. It is fetched (real signal, useful to a
    reader) but flagged `partial` and excluded from slope and shape.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
ALGOLIA_HOST = "hn.algolia.com"

USER_AGENT = "problem-prospector/0.1 (+https://github.com/mattfili/problem-prospector)"

# hn.algolia.com publishes no hard rate limit. 0.5s between calls is polite and
# keeps a 2-query/5-year run under ~15s. Do not lower it.
HOST_MIN_INTERVAL_S = 0.5
REQUEST_TIMEOUT_S = 20.0
# One retry, for transient 5xx/socket errors only. 403/429 are never retried
# (see fetch_bucket_count) because retry-probing a refusal is evasion.
TRANSIENT_RETRY_DELAY_S = 1.5

SOURCE_NAME = "hackernews"


# --------------------------------------------------------------------------
# Classification thresholds. Kept here, in one auditable place, so a reader can
# see exactly why a keyphrase was called "declining" and can tune it without
# reading the classifier. Emitted verbatim in the JSON output under
# "thresholds" so any stored card can be re-derived later.
# --------------------------------------------------------------------------
SHAPE_THRESHOLDS: dict[str, float] = {
    # Below this total, bucket-to-bucket differences are noise (a single
    # unrelated post moves a 3-hit series by 33%). No shape is claimed.
    "min_total_for_shape": 5,
    # One bucket holding more than this share of all mentions means the series
    # is an event, not a level. Checked FIRST, because a spike also produces a
    # large |slope| and would otherwise masquerade as growth or decline.
    "spiky_max_bucket_share": 0.50,
    # The spike test needs enough buckets to be meaningful: with 2 buckets, 50%
    # is simply "even". Requires at least this many usable buckets.
    "spiky_min_buckets": 4,
    # "emerging" = the problem barely existed early on. If the first half of the
    # window holds <= this share of total mentions and the slope is positive,
    # the series is emerging rather than accelerating from an existing base.
    "emerging_first_half_share_max": 0.15,
    # |slope| inside this band is flat. 15%/yr on HN counts is well within
    # year-to-year variance in overall site volume.
    "flat_band_pct_per_year": 15.0,
    # Sustained growth from an existing base.
    "accelerating_slope_pct_per_year": 60.0,
    # Sustained decay. Asymmetric with the flat band on purpose: a real decline
    # has to clear more than the flat band to be called, since HN's own volume
    # drift is more likely to look like mild decline than mild growth.
    "declining_slope_pct_per_year": -20.0,
}

COVERAGE_THRESHOLDS: dict[str, float] = {
    # ~2 mentions per bucket on average over a 10-bucket window: the floor at
    # which a slope is worth quoting at all.
    "good_min_total": 20,
    # 1..good_min_total-1 is "thin": real history, too little to lean on.
    "thin_min_total": 1,
    # Coverage cannot be "good" if this share of buckets failed to fetch, no
    # matter how many hits the surviving buckets returned. The same bar also
    # blocks "none": above it, a zero total means "we did not measure it", so
    # coverage is null rather than a claim of real absence.
    "max_failed_bucket_share_for_good": 0.25,
    # Coverage itself stays a function of total hits (the contract's basis), but
    # a series with more than this share of empty buckets is intermittent: its
    # slope turns on whether one post landed in one half-year. Above this share
    # the query note says so.
    "intermittent_zero_bucket_share": 0.30,
    # Measured drift in Algolia's non-exhaustive nbHits, repeating the identical
    # request minutes apart: high-volume buckets moved <1% (kubernetes 2023H1
    # 411/411/411, 2024H2 363/364) while low-volume buckets moved by whole posts
    # (a 2021H2 bucket returned 0, 0, then 3; a 2022H2 bucket 3, 3, 5, then 2).
    # Absolute drift is small either way, so it only matters when buckets are
    # this small — there it can double the reported slope between runs, and the
    # note must say so rather than implying a few-percent error bar.
    "unstable_max_bucket_count": 10,
}

SHAPES = ("emerging", "accelerating", "persistent-flat", "declining", "spiky-episodic")


def log(msg: str) -> None:
    """Diagnostics go to stderr; stdout stays parseable JSON."""
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Time bucketing
# --------------------------------------------------------------------------
def _shift_months(dt: datetime, months: int) -> datetime:
    total = (dt.year * 12 + (dt.month - 1)) + months
    return datetime(total // 12, (total % 12) + 1, 1, tzinfo=timezone.utc)


def _period_label(start: datetime, step_months: int) -> str:
    if step_months == 6:
        return f"{start.year}H{1 if start.month == 1 else 2}"
    return str(start.year)


def bucket_windows(
    now: datetime, years: int, bucket: str, include_partial: bool
) -> list[dict[str, Any]]:
    """Calendar-aligned buckets covering `years` back from `now`.

    Aligned to calendar halves/years rather than rolling N-day windows so that
    labels ("2022H1") are stable and comparable across runs and across the
    other retro-trend sources. The trailing in-progress period is appended
    separately and marked partial.
    """
    step_months = 6 if bucket == "half-year" else 12
    if step_months == 6:
        current_start = datetime(now.year, 1 if now.month <= 6 else 7, 1, tzinfo=timezone.utc)
    else:
        current_start = datetime(now.year, 1, 1, tzinfo=timezone.utc)

    count = years * (12 // step_months)
    starts: list[datetime] = []
    cursor = current_start
    for _ in range(count):
        cursor = _shift_months(cursor, -step_months)
        starts.append(cursor)
    starts.reverse()

    windows: list[dict[str, Any]] = []
    for start in starts:
        end = _shift_months(start, step_months)
        windows.append(
            {
                "period": _period_label(start, step_months),
                "start_utc": int(start.timestamp()),
                "end_utc": int(end.timestamp()),
                "start_iso": start.isoformat().replace("+00:00", "Z"),
                "end_iso": end.isoformat().replace("+00:00", "Z"),
                "partial": False,
            }
        )

    if include_partial and now > current_start:
        windows.append(
            {
                "period": _period_label(current_start, step_months),
                "start_utc": int(current_start.timestamp()),
                "end_utc": int(now.timestamp()),
                "start_iso": current_start.isoformat().replace("+00:00", "Z"),
                "end_iso": now.isoformat().replace("+00:00", "Z"),
                "partial": True,
            }
        )
    return windows


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------
class HostGate:
    """Politeness spacing plus a one-way circuit breaker for a single host.

    Once tripped (403/429) the breaker never reopens inside a run: the correct
    response to being refused is to stop asking and degrade the output, not to
    back off and probe again.
    """

    def __init__(self, min_interval_s: float) -> None:
        self.min_interval_s = min_interval_s
        self._last_call = 0.0
        self.broken_reason: str | None = None

    @property
    def is_broken(self) -> bool:
        return self.broken_reason is not None

    def trip(self, reason: str) -> None:
        if self.broken_reason is None:
            self.broken_reason = reason
            log(f"[circuit-break] {ALGOLIA_HOST}: {reason} — no further requests this run")

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)
        self._last_call = time.monotonic()


def algolia_tags_param(tags: str) -> str:
    """Translate the CLI tags value into Algolia filter syntax.

    Algolia AND-combines a bare comma list, so `story,comment` matches nothing
    (verified: nbHits=0). The OR form is parenthesised.
    """
    parts = [t.strip() for t in tags.split(",") if t.strip()]
    if len(parts) == 1:
        return parts[0]
    return "(" + ",".join(parts) + ")"


def fetch_bucket_count(
    session: requests.Session,
    gate: HostGate,
    query: str,
    tags_param: str,
    start_utc: int,
    end_utc: int,
) -> dict[str, Any]:
    """One Algolia call; returns {count, exhaustive} or {count: None, error}.

    `count` is `nbHits`, the total number of matching documents in the window.
    That total is the entire point: we ask for a single hit and read the total
    off the envelope, so a bucket costs one request regardless of its volume.
    """
    if gate.is_broken:
        return {"count": None, "exhaustive": None, "error": f"host circuit-broken: {gate.broken_reason}"}

    params = {
        "query": query,
        "tags": tags_param,
        # Algolia's numericFilters comparisons are strict, so shift the lower
        # bound by one second to make the window inclusive of `start_utc`.
        "numericFilters": f"created_at_i>{start_utc - 1},created_at_i<{end_utc}",
        "hitsPerPage": 1,
    }

    attempt = 0
    while True:
        attempt += 1
        gate.wait()
        try:
            resp = session.get(ALGOLIA_SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT_S)
        except requests.RequestException as exc:
            if attempt == 1:
                log(f"[warn] network error ({type(exc).__name__}); one retry")
                time.sleep(TRANSIENT_RETRY_DELAY_S)
                continue
            return {"count": None, "exhaustive": None, "error": f"network: {type(exc).__name__}"}

        if resp.status_code in (403, 429):
            gate.trip(f"HTTP {resp.status_code}")
            return {"count": None, "exhaustive": None, "error": f"HTTP {resp.status_code}"}

        if resp.status_code >= 500:
            if attempt == 1:
                log(f"[warn] HTTP {resp.status_code} from {ALGOLIA_HOST}; one retry")
                time.sleep(TRANSIENT_RETRY_DELAY_S)
                continue
            return {"count": None, "exhaustive": None, "error": f"HTTP {resp.status_code}"}

        if resp.status_code != 200:
            return {"count": None, "exhaustive": None, "error": f"HTTP {resp.status_code}"}

        try:
            payload = resp.json()
        except ValueError:
            # Historically this is the signature of an unencoded numericFilters
            # value; params= should make it impossible, so surface it loudly.
            return {"count": None, "exhaustive": None, "error": "non-JSON response body"}

        count = payload.get("nbHits")
        if not isinstance(count, int):
            return {"count": None, "exhaustive": None, "error": "response missing nbHits"}

        return {"count": count, "exhaustive": bool(payload.get("exhaustiveNbHits", False))}


# --------------------------------------------------------------------------
# Series math
# --------------------------------------------------------------------------
def ols_slope_per_bucket(counts: Iterable[float]) -> float | None:
    """Least-squares slope of count against bucket index."""
    ys = list(counts)
    n = len(ys)
    if n < 2:
        return None
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    return num / denom


def slope_pct_per_year(counts: list[int], buckets_per_year: int) -> float | None:
    """Slope expressed as percent of the window's mean volume, per year.

    Normalising by the mean makes the number comparable across keyphrases of
    wildly different absolute volume, which is what the card needs: "grew ~30%
    a year" is legible where "grew 1.4 mentions per half-year" is not.
    """
    slope = ols_slope_per_bucket(counts)
    if slope is None:
        return None
    mean_y = sum(counts) / len(counts)
    if mean_y <= 0:
        return None
    return round(slope * buckets_per_year / mean_y * 100.0, 1)


def half_over_half_pct(counts: list[int]) -> float | None:
    """Second-half total vs first-half total, as a sanity check on the OLS fit.

    With an odd number of buckets the middle bucket is dropped so the two halves
    span equal time. Comparing 2 buckets against 3 inflates the result by ~50%
    and would report a flat series as strong growth.
    """
    if len(counts) < 4:
        return None
    mid = len(counts) // 2
    tail = counts[mid + 1 :] if len(counts) % 2 else counts[mid:]
    first, second = sum(counts[:mid]), sum(tail)
    if first == 0:
        return None
    return round((second - first) / first * 100.0, 1)


def classify_coverage(total: int, failed_share: float) -> str | None:
    """Coverage as a function of total hits, guarded by how much was measured.

    Returns None when the window is too unmeasured to characterise at all.
    "none" is not a neutral label: it asserts that HN really is silent on the
    keyphrase, and the historian panel reads it that way. That assertion is only
    available if most of the window actually answered — zero hits in the three
    buckets that responded says nothing about the eight that errored.
    """
    too_many_failed = failed_share > COVERAGE_THRESHOLDS["max_failed_bucket_share_for_good"]
    if total <= 0:
        return None if too_many_failed else "none"
    if total < COVERAGE_THRESHOLDS["good_min_total"]:
        return "thin"
    if too_many_failed:
        return "thin"
    return "good"


def classify_shape(counts: list[int], slope_pct: float | None) -> tuple[str | None, dict[str, Any]]:
    """Return (shape, evidence) using SHAPE_THRESHOLDS. Order matters.

    1. too little total volume -> no shape (never guess one)
    2. one dominant bucket     -> spiky-episodic (checked before slope, since a
                                  spike fakes a large slope in either direction)
    3. near-empty first half   -> emerging
    4. strong positive slope   -> accelerating
    5. clear negative slope    -> declining
    6. otherwise               -> persistent-flat
    """
    total = sum(counts)
    evidence: dict[str, Any] = {"total_in_slope_window": total, "buckets_used": len(counts)}

    if total < SHAPE_THRESHOLDS["min_total_for_shape"]:
        evidence["reason"] = (
            f"total {total} below min_total_for_shape "
            f"{int(SHAPE_THRESHOLDS['min_total_for_shape'])}; no shape claimed"
        )
        return None, evidence

    max_count = max(counts)
    max_share = max_count / total if total else 0.0
    max_period_index = counts.index(max_count)
    evidence["max_bucket_index"] = max_period_index
    evidence["max_bucket_share"] = round(max_share, 3)

    if (
        len(counts) >= SHAPE_THRESHOLDS["spiky_min_buckets"]
        and max_share > SHAPE_THRESHOLDS["spiky_max_bucket_share"]
    ):
        evidence["reason"] = (
            f"one bucket holds {max_share:.0%} of volume (> "
            f"{SHAPE_THRESHOLDS['spiky_max_bucket_share']:.0%}); news-driven, not a level"
        )
        return "spiky-episodic", evidence

    # "First half" is the earliest floor(n/2) buckets as a share of the whole
    # window's volume. Recorded with its bucket count so an odd-length series
    # cannot be misread as an even split.
    mid = len(counts) // 2
    first_half_share = (sum(counts[:mid]) / total) if (total and mid) else None
    evidence["first_half_buckets"] = mid
    evidence["first_half_share"] = (
        round(first_half_share, 3) if first_half_share is not None else None
    )

    if slope_pct is None:
        evidence["reason"] = "slope undefined (flat zero or single bucket); treated as flat"
        return "persistent-flat", evidence

    if (
        first_half_share is not None
        and first_half_share <= SHAPE_THRESHOLDS["emerging_first_half_share_max"]
        and slope_pct > SHAPE_THRESHOLDS["flat_band_pct_per_year"]
    ):
        evidence["reason"] = (
            f"first half holds only {first_half_share:.0%} of volume with slope "
            f"{slope_pct:+.1f}%/yr; growth from a near-zero base"
        )
        return "emerging", evidence

    if slope_pct >= SHAPE_THRESHOLDS["accelerating_slope_pct_per_year"]:
        evidence["reason"] = (
            f"slope {slope_pct:+.1f}%/yr >= "
            f"{SHAPE_THRESHOLDS['accelerating_slope_pct_per_year']}%/yr from an existing base"
        )
        return "accelerating", evidence

    if slope_pct <= SHAPE_THRESHOLDS["declining_slope_pct_per_year"]:
        evidence["reason"] = (
            f"slope {slope_pct:+.1f}%/yr <= "
            f"{SHAPE_THRESHOLDS['declining_slope_pct_per_year']}%/yr"
        )
        return "declining", evidence

    # Fall-through covers two distinct situations and must name the right one:
    # a slope genuinely inside the flat band, versus a drifting slope that is
    # outside the band but has not cleared the accelerating/declining bar.
    if abs(slope_pct) <= SHAPE_THRESHOLDS["flat_band_pct_per_year"]:
        evidence["reason"] = (
            f"slope {slope_pct:+.1f}%/yr inside the flat band "
            f"+/-{SHAPE_THRESHOLDS['flat_band_pct_per_year']}%/yr"
        )
    else:
        bar = (
            SHAPE_THRESHOLDS["accelerating_slope_pct_per_year"]
            if slope_pct > 0
            else SHAPE_THRESHOLDS["declining_slope_pct_per_year"]
        )
        early = f"{first_half_share:.0%}" if first_half_share is not None else "an unknown share"
        evidence["reason"] = (
            f"slope {slope_pct:+.1f}%/yr drifts outside the flat band "
            f"+/-{SHAPE_THRESHOLDS['flat_band_pct_per_year']}%/yr but does not reach "
            f"{bar:+.0f}%/yr, and the first half holds {early} of volume so it is not "
            "emerging from zero; durable level with drift"
        )
    return "persistent-flat", evidence


# --------------------------------------------------------------------------
# Per-query orchestration
# --------------------------------------------------------------------------
def run_query(
    session: requests.Session,
    gate: HostGate,
    query: str,
    windows: list[dict[str, Any]],
    tags: str,
    phrase: bool,
    buckets_per_year: int,
) -> dict[str, Any]:
    algolia_tags = algolia_tags_param(tags)
    search_term = f'"{query}"' if phrase else query

    buckets: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for window in windows:
        result = fetch_bucket_count(
            session, gate, search_term, algolia_tags, window["start_utc"], window["end_utc"]
        )
        buckets.append(
            {
                "period": window["period"],
                "count": result["count"],
                # Additive audit field beyond CONTRACTS §4: Algolia's nbHits is
                # an estimate unless this is true.
                "count_exhaustive": result["exhaustive"],
            }
        )
        if result["count"] is None:
            failures.append({"period": window["period"], "error": result["error"]})
            log(f"[fail] {query!r} {window['period']}: {result['error']}")
        else:
            log(f"[ok]   {query!r} {window['period']}: nbHits={result['count']}")

    partial_periods = {w["period"] for w in windows if w["partial"]}
    fetched = [b for b in buckets if b["count"] is not None]
    total_count = sum(b["count"] for b in fetched) if fetched else None
    failed_share = len(failures) / len(buckets) if buckets else 1.0

    # Slope/shape are computed only on complete, successfully-fetched buckets.
    # A partial trailing bucket would fake a decline; a null bucket must not be
    # silently read as zero.
    slope_counts = [
        b["count"]
        for b in buckets
        if b["count"] is not None and b["period"] not in partial_periods
    ]

    notes: list[str] = []
    if not fetched:
        coverage: str | None = None
        slope: float | None = None
        shape: str | None = None
        shape_evidence: dict[str, Any] = {
            "reason": "every bucket fetch failed; no measurement was made"
        }
        notes.append(
            f"All {len(buckets)} HN buckets failed to fetch — this is a source failure, "
            "not an absence of HN discussion. Do not read as zero."
        )
    else:
        coverage = classify_coverage(total_count or 0, failed_share)
        slope = slope_pct_per_year(slope_counts, buckets_per_year) if slope_counts else None
        shape, shape_evidence = classify_shape(slope_counts, slope) if slope_counts else (
            None,
            {"reason": "no complete buckets available for slope"},
        )
        if failures:
            notes.append(
                f"{len(failures)} of {len(buckets)} buckets failed to fetch "
                f"({', '.join(f['period'] for f in failures)}); slope computed on "
                f"{len(slope_counts)} complete buckets."
            )
        if partial_periods:
            notes.append(
                f"{'/'.join(sorted(partial_periods))} is an in-progress period; reported in the "
                "series but excluded from slope and shape."
            )
        if coverage == "thin":
            # "thin" has two distinct causes and the note must name the real one:
            # too little volume, or too little of the window measured. Calling a
            # 700-mention series "thin history" when the real problem is four
            # failed buckets misdirects the reader.
            if total_count is not None and total_count < COVERAGE_THRESHOLDS["good_min_total"]:
                notes.append(
                    f"Thin history: {total_count} total HN mentions across {len(buckets)} buckets. "
                    "Treat shape as suggestive only."
                )
            else:
                notes.append(
                    f"Coverage held to thin by fetch failures, not by volume: {len(failures)} of "
                    f"{len(buckets)} buckets are unmeasured, so the {total_count} mentions counted "
                    "are a floor rather than the window total."
                )
        zero_buckets = sum(1 for c in slope_counts if c == 0)
        zero_share = (zero_buckets / len(slope_counts)) if slope_counts else 0.0
        # An all-zero series is an absence, not an intermittent one: "the slope
        # turns on individual posts" is false when there are no posts. The
        # zero/absence notes below cover that case.
        if total_count and zero_buckets and zero_share >= COVERAGE_THRESHOLDS["intermittent_zero_bucket_share"]:
            notes.append(
                f"Intermittent history: {zero_buckets} of {len(slope_counts)} complete buckets "
                "are zero, so the slope turns on individual posts rather than a steady level."
            )
        if coverage == "none":
            if failures:
                notes.append(
                    f"Zero HN mentions in the {len(fetched)} of {len(buckets)} buckets that were "
                    f"measured; {len(failures)} bucket(s) failed to fetch "
                    f"({', '.join(f['period'] for f in failures)}), so this is an absence in the "
                    "measured periods only."
                )
            else:
                notes.append(
                    "Zero HN mentions in the whole window. Fetching succeeded, so this is a real "
                    "absence on HN — it says nothing about other sources."
                )
        elif coverage is None:
            notes.append(
                f"Coverage unknown, NOT zero: the {len(fetched)} of {len(buckets)} buckets that "
                f"answered returned no mentions, but {len(failures)} failed to fetch. Zero measured "
                "volume across a mostly-unmeasured window is not evidence of absence — do not read "
                "this as 'no HN discussion'."
            )
        # Algolia reports exhaustiveNbHits=false even for zero-hit multi-word
        # queries, where "this total is approximate" is noise. Only flag it when
        # an approximate bucket actually carries volume.
        if any(b["count_exhaustive"] is False and b["count"] > 0 for b in fetched):
            notes.append(
                "Some bucket counts are Algolia estimates (exhaustiveNbHits=false); they are "
                "comparable to each other but not exact totals."
            )
            # The drift is a few mentions per bucket in absolute terms. On a
            # high-volume series that is rounding error; on a series whose
            # buckets are single digits it is the difference between "flat" and
            # "growing", so do not let the sentence above imply a small error bar.
            biggest = max(slope_counts) if slope_counts else 0
            if 0 < biggest <= COVERAGE_THRESHOLDS["unstable_max_bucket_count"]:
                notes.append(
                    f"Buckets this small (largest complete bucket is {biggest}) are not "
                    "reproducible: repeating the identical request minutes apart has moved a "
                    "single bucket by 2-3 mentions, which can roughly double or halve the "
                    "reported slope. Treat the shape, not the slope number, as the finding."
                )

    return {
        "query": query,
        "search_term_sent": search_term,
        "total_count": total_count,
        "buckets_fetched": len(fetched),
        "buckets_failed": len(failures),
        # Shaped for direct merge into CONTRACTS §4 card.retro_trend.
        "retro_trend": {
            "shape": shape,
            "slope_pct_per_year": slope,
            "series": [
                {
                    "source": SOURCE_NAME,
                    "buckets": buckets,
                    "coverage": coverage,
                }
            ],
            "note": " ".join(notes) if notes else None,
        },
        "diagnostics": {
            "slope_basis": (
                "ordinary least squares on complete-bucket counts, normalised by the window "
                "mean and scaled to percent per year"
            ),
            "buckets_used_for_slope": len(slope_counts),
            "zero_buckets_in_slope_window": sum(1 for c in slope_counts if c == 0),
            "half_over_half_pct": half_over_half_pct(slope_counts),
            "shape_evidence": shape_evidence,
            "failures": failures,
        },
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def read_queries_file(path: Path) -> list[str]:
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hn_history.py",
        description=(
            "Backward-facing Hacker News mention counts per time bucket, for the retro-trend "
            "method (CONTRACTS section 4, card.retro_trend.series). Answers 'how long has this "
            "pain been complained about, and is the volume durable or one news cycle?' rather "
            "than 'is it trending now?'. One key-free Algolia call per bucket reads nbHits as "
            "the bucket total, so five years costs ~11 requests per keyphrase."
        ),
        epilog=(
            "examples:\n"
            "  # five years of half-year buckets for one keyphrase\n"
            "  uv run --quiet scripts/hn_history.py --query 'permit software'\n\n"
            "  # two keyphrases, stories and comments, yearly buckets, persisted\n"
            "  uv run --quiet scripts/hn_history.py \\\n"
            "      --query 'permit software' --query 'records request' \\\n"
            "      --bucket year --tags story,comment --out runs/my-run/hn_history.json\n\n"
            "  # keyphrases from a file, exact-phrase matching\n"
            "  uv run --quiet scripts/hn_history.py --queries-file pains.txt --phrase\n\n"
            "exit codes:\n"
            "  0  fetching worked (zero mentions is a valid result)\n"
            "  1  nothing usable was gathered; see source_health\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--query",
        action="append",
        default=[],
        metavar="KEYPHRASE",
        help="keyphrase to count; repeatable",
    )
    parser.add_argument(
        "--queries-file",
        type=Path,
        metavar="PATH",
        help="file of keyphrases, one per line ('#' comments and blanks ignored)",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=5,
        help="how many complete years back to cover (default: 5)",
    )
    parser.add_argument(
        "--bucket",
        choices=("half-year", "year"),
        default="half-year",
        help="bucket width, calendar-aligned (default: half-year)",
    )
    parser.add_argument(
        "--tags",
        default="story",
        choices=("story", "comment", "story,comment"),
        # Explicit metavar: argparse renders a comma-containing choice as if it
        # were two separate options, which reads as nonsense in --help.
        metavar="story | comment | story,comment",
        help=(
            "which HN document types to count. 'story,comment' is sent to Algolia as the OR "
            "form '(story,comment)' (default: story)"
        ),
    )
    parser.add_argument(
        "--phrase",
        action="store_true",
        help="wrap each keyphrase in quotes for exact-phrase matching (Algolia advancedSyntax)",
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    queries: list[str] = list(args.query)
    if args.queries_file:
        if not args.queries_file.is_file():
            log(f"[error] --queries-file not found: {args.queries_file}")
            return 1
        queries.extend(read_queries_file(args.queries_file))

    # De-duplicate while preserving order; duplicate keyphrases would double the
    # request count for identical answers.
    seen: set[str] = set()
    queries = [q for q in queries if not (q in seen or seen.add(q))]

    if not queries:
        log("[error] no queries given; pass --query or --queries-file (see --help)")
        return 1
    if args.years < 1:
        log("[error] --years must be >= 1")
        return 1

    now = datetime.now(timezone.utc)
    windows = bucket_windows(now, args.years, args.bucket, include_partial=not args.drop_partial)
    buckets_per_year = 2 if args.bucket == "half-year" else 1

    total_calls = len(queries) * len(windows)
    log(
        f"[plan] {len(queries)} quer{'y' if len(queries) == 1 else 'ies'} x {len(windows)} "
        f"buckets = {total_calls} calls, ~{math.ceil(total_calls * HOST_MIN_INTERVAL_S)}s minimum"
    )

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    gate = HostGate(HOST_MIN_INTERVAL_S)

    results = [
        run_query(session, gate, q, windows, args.tags, args.phrase, buckets_per_year)
        for q in queries
    ]

    fetched_total = sum(r["buckets_fetched"] for r in results)
    failed_total = sum(r["buckets_failed"] for r in results)
    if fetched_total == 0:
        status, detail = "unavailable", (
            f"all {failed_total} bucket requests failed"
            + (f" ({gate.broken_reason}, circuit-broken)" if gate.is_broken else "")
        )
    elif failed_total:
        status, detail = "degraded", (
            f"{fetched_total} of {fetched_total + failed_total} bucket requests succeeded"
            + (f"; circuit-broken on {gate.broken_reason}" if gate.is_broken else "")
        )
    else:
        status, detail = "ok", f"{fetched_total} bucket counts read from nbHits"

    payload = {
        "tool": "hn_history",
        "generated_utc": int(now.timestamp()),
        "generated_iso": now.isoformat().replace("+00:00", "Z"),
        "params": {
            "queries": queries,
            "years": args.years,
            "bucket": args.bucket,
            "buckets_per_year": buckets_per_year,
            "tags": args.tags,
            "tags_sent_to_algolia": algolia_tags_param(args.tags),
            "phrase": args.phrase,
            "include_current_partial": not args.drop_partial,
        },
        "windows": windows,
        "thresholds": {"shape": SHAPE_THRESHOLDS, "coverage": COVERAGE_THRESHOLDS},
        "shapes_possible": list(SHAPES),
        "results": results,
        "source_health": [{"source": SOURCE_NAME, "status": status, "detail": detail}],
    }

    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        log(f"[out] wrote {args.out}")

    print(text)
    return 0 if fetched_total else 1


if __name__ == "__main__":
    sys.exit(main())
