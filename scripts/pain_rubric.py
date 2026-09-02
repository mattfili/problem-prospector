#!/usr/bin/env python3
"""Prospect-methodology §3.3, as code: the frequency and intensity rubrics.

WHY THIS EXISTS
---------------
§3.3 is the one stage every later stage trusts, and its inputs are almost
entirely arithmetic: count distinct authors, compare against a threshold, apply
a correction, read a level off a ladder. Carried as prose in a subagent brief,
that arithmetic gets re-derived by a model on every run and drifts — a marker set
`true` with no quote behind it, a level 4 claimed off one articulate author, a
`read` that disagrees with its own `score`. Carried here it is reproducible: two
runs over the same evidence land on the same number, and the number can be
explained by pointing at a line.

WHAT DELIBERATELY STAYS WITH THE MODEL
--------------------------------------
Which quote evidences which marker. That is reading comprehension and it does
not belong in code. Everything downstream of that judgment — distinct-author
counts, which ladder legs are met, which caps bind, the `read`, the `quadrant` —
is here.

RUBRIC GAP, RESOLVED IN THE OPEN (do not "fix" this silently)
-------------------------------------------------------------
§3.3's ladder is not total as written. Level 3 reads "*exactly one* of {money_loss,
time_quantified, workaround_built, abandonment} is citable, from >=2 distinct
authors"; level 4 needs ">=2 of them ... **and** `complainer_is_buyer`". So a
cluster with three cost markers at >=2 authors each and no buyer marker meets
neither 3 nor 4 — and level 2 explicitly excludes it ("No cost, no workaround, no
abandonment anywhere in the cluster"). Nothing in the rubric scores it.

This module reads level 3's leg as "*at least one*", which is the only reading
that makes the ladder monotone and therefore the only one under which "the
highest level whose criteria are fully met" is well defined. When that reading is
what carried a score, `derive_intensity` says so in its note and
`pain_report.py` prints it in the report header. An encoded judgment nobody can
see is precisely what this pipeline exists to prevent.

CONSUMERS
---------
`pain_stages.py` (writes the panels), `pain_report.py` (prints the thresholds
actually used). Pure: no I/O, no network, no run state.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Axis A — frequency (mechanical, from clusters.json)
# --------------------------------------------------------------------------

#: §3.3's thresholds are calibrated for a run of this many evidence items.
CALIBRATION_RANGE = (300, 1500)

BASE_THRESHOLDS: dict[str, dict[str, int]] = {
    "high": {"cluster_size": 20, "distinct_authors": 12, "distinct_communities": 3},
    "medium": {"cluster_size": 8, "distinct_authors": 6, "distinct_communities": 2},
}

LEVELS = ("low", "medium", "high")


def scaled_thresholds(total_items: int) -> dict[str, object]:
    """Scale §3.3's frequency thresholds to a corpus outside the calibration range.

    §3.3 requires scaling "proportionally" and printing the thresholds actually
    used, without saying which fields scale. Two decisions, made here and stated
    so a reader can disagree with them:

    * Only `cluster_size` and `distinct_authors` scale. They are volume
      thresholds and a 60-item run cannot produce a 20-member cluster.
    * `distinct_communities` never scales. It is a structural guard against a
      single-subreddit echo, not a volume measure; scaling it to 1 would delete
      the guard exactly when a thin corpus makes echo chambers most likely.

    Returns the thresholds plus `factor` and `scaled`, both of which land in the
    report header — an unstated threshold makes every read non-reproducible.
    """
    low, high = CALIBRATION_RANGE
    if total_items < low:
        factor = total_items / low
    elif total_items > high:
        factor = total_items / high
    else:
        factor = 1.0

    thresholds = {
        level: {
            "cluster_size": max(2, round(fields["cluster_size"] * factor)),
            "distinct_authors": max(2, round(fields["distinct_authors"] * factor)),
            "distinct_communities": fields["distinct_communities"],
        }
        for level, fields in BASE_THRESHOLDS.items()
    }
    # A hard floor can collapse high onto medium; keep the ladder strictly ordered.
    for field in ("cluster_size", "distinct_authors"):
        if thresholds["high"][field] <= thresholds["medium"][field]:
            thresholds["high"][field] = thresholds["medium"][field] + 1

    return {
        "thresholds": thresholds,
        "factor": round(factor, 3),
        "scaled": factor != 1.0,
        "total_items": total_items,
    }


def _base_read(cluster: dict, thresholds: dict[str, dict[str, int]]) -> str:
    """Highest level whose *volume* legs are met — members and distinct authors.

    `distinct_communities` is deliberately not read here; it applies as a cap in
    `frequency_read`. See that function's SECOND DISCLOSED READING note for why
    the two cannot both be gates.
    """
    for level in ("high", "medium"):
        wanted = thresholds[level]
        if (
            cluster.get("member_count", 0) >= wanted["cluster_size"]
            and cluster.get("distinct_authors", 0) >= wanted["distinct_authors"]
        ):
            return level
    return "low"


def _demote(level: str) -> str:
    """One level down; `low` is the floor."""
    return LEVELS[max(0, LEVELS.index(level) - 1)]


def frequency_read(
    cluster: dict,
    thresholds: dict[str, dict[str, int]],
    engagement_decile_floor: float | None,
) -> tuple[str, str | None]:
    """Apply §3.3 Axis A: volume read, community cap, then the two corrections.

    Returns `(read, note)`. The note names every rule that fired and lands on the
    sanctioned additive key `frequency.note` — without it a read is not
    reproducible from the numbers printed beside it.

    SECOND DISCLOSED READING (§3.3 contradicts itself here; do not "fix" silently)
    -----------------------------------------------------------------------------
    §3.3 states `distinct_communities` twice, incompatibly. As a *threshold* it
    gates the levels (high needs >=3, medium needs >=2), which forces a
    single-community cluster to `low`. As *correction 2* it says
    "`distinct_communities == 1` -> cap at **medium** (echo chamber)". Both cannot
    hold: under the thresholds a one-community cluster can never reach medium, so
    correction 2 is unreachable dead text; under correction 2 the medium
    threshold's two-community leg is overridden.

    This module gives the later, explicit correction precedence, so the community
    count *caps* rather than *gates*: fewer than three communities caps at medium,
    and one community caps at medium rather than collapsing to low. The alternative
    reading calls a 47-member, 39-author, single-subreddit pain `low` frequency and
    triages it as low-freq in the 2x2 alongside a 3-member cluster, which is the
    less defensible of the two. When the cap binds on a single-community cluster
    the note says so, and `pain_report.py` prints the disclosure in the header.
    """
    size = cluster.get("member_count", 0) or 0
    authors = cluster.get("distinct_authors", 0) or 0
    communities = cluster.get("distinct_communities", 0) or 0
    engagement = cluster.get("engagement_sum") or 0

    read = _base_read(cluster, thresholds)
    fired: list[str] = []

    # Community cap, ahead of the corrections: breadth of community bounds the
    # level, it does not set it.
    if communities < thresholds["high"]["distinct_communities"] and read == "high":
        read = "medium"
        if communities <= 1:
            fired.append(
                "frequency lowered from high to medium: every one of these posts "
                "came from a single community. One community's shared vocabulary "
                "clusters beautifully and tells you nothing about the wider world. "
                "The pain may still be real — the cap is about your coverage, not "
                "the problem. Add communities and re-capture to lift it"
            )
        else:
            fired.append(
                f"frequency lowered from high to medium: these posts come from only "
                f"{communities} communities and high needs "
                f"{thresholds['high']['distinct_communities']} — breadth of "
                "community bounds the level. Add communities and re-capture to "
                "lift it"
            )

    # 1. Repetition-heavy: few authors relative to members.
    if size and authors / size < 0.4:
        before, read = read, _demote(read)
        if before != read:
            fired.append(
                f"frequency lowered from {before} to {read}: only {authors} of "
                f"{size} posts have distinct authors — a few people repeating "
                "themselves reads as volume but is not. Add sources or communities "
                "and re-capture"
            )

    # 2. Engagement may promote medium->high only, and never on its own.
    if (
        read == "medium"
        and engagement_decile_floor is not None
        and engagement >= engagement_decile_floor
        and communities >= 3
    ):
        read = "high"
        fired.append(
            f"frequency raised from medium to high: engagement on these posts "
            f"({engagement}) is in this run's top tenth (floor "
            f"{round(engagement_decile_floor)}) across {communities} communities — "
            "informational, no action needed"
        )

    if read in ("medium", "low"):
        fired.append(f"2x2 boundary: {read} frequency sits on the low-freq side by design")

    return read, "; ".join(fired) if fired else None


def engagement_top_decile(clusters: list[dict]) -> float | None:
    """The 90th-percentile `engagement_sum` across this run's clusters.

    `None` when there are too few clusters for a decile to mean anything (<10),
    which disables the engagement promotion rather than inventing a floor from
    three data points.
    """
    values = sorted(float(c.get("engagement_sum") or 0) for c in clusters)
    if len(values) < 10:
        return None
    index = 0.9 * (len(values) - 1)
    low = int(index)
    if low == index:
        return values[low]
    return values[low] + (values[low + 1] - values[low]) * (index - low)


# --------------------------------------------------------------------------
# Axis B — intensity (rubric 1-5, from validated marker evidence)
# --------------------------------------------------------------------------

#: The six markers of §3.3 Axis B. Exactly these — no seventh, no reweighting.
MARKERS = (
    "money_loss",
    "time_quantified",
    "workaround_built",
    "abandonment",
    "profanity_urgency",
    "complainer_is_buyer",
)

#: The four that carry cost. The ladder's legs are counted over these.
COST_MARKERS = ("money_loss", "time_quantified", "workaround_built", "abandonment")

MAX_EXEMPLAR_WORDS = 15


def derive_intensity(
    marker_present: set[str],
    marker_authors: dict[str, set[str]],
    recurring_authors: set[str],
) -> dict[str, object]:
    """Score §3.3 Axis B from validated marker evidence. Never from a claim.

    Presence and author count are separate inputs on purpose. A marker is
    *present* when at least one verbatim quote evidences it (§3.3: "no quote, no
    marker"), which is what `markers` on the card records. Its *author count* is
    the set of distinct identifying authors behind those quotes, resolved off the
    evidence on disk by the caller so it cannot be asserted — and it can be
    smaller than the quote count, or zero when every quote came from a deleted
    account. Levels 3, 4 and 5 turn on the author count; presence alone only ever
    reaches level 2.

    `recurring_authors` is the subset of cost-marker authors whose quote carried a
    recurring quantified cost (hours/week, dollars/month, dedicated headcount) —
    level 5's extra leg.

    Returns `score`, `read`, the six-key `markers` block, a human-readable `note`
    for `intensity.note`, and a `derivation` block that explains the number.
    `derivation` is tool output only: CONTRACTS §4 declares no such key and
    writing it to a card would be silent contract drift.
    """
    present = {m for m in MARKERS if m in marker_present}
    all_authors: set[str] = set()
    for authors in marker_authors.values():
        all_authors |= authors

    cost_two_plus = [m for m in COST_MARKERS if len(marker_authors.get(m, set())) >= 2]
    cost_thin = [
        m for m in COST_MARKERS
        if marker_authors.get(m) and len(marker_authors[m]) < 2
    ]
    buyers = marker_authors.get("complainer_is_buyer", set())

    notes: list[str] = []
    level = 2 if present else 1
    if cost_two_plus:
        level = 3
    if len(cost_two_plus) >= 2 and len(buyers) >= 1:
        level = 4
    if level == 4 and len(recurring_authors) >= 2 and len(buyers) >= 2:
        level = 5

    if level == 3 and len(cost_two_plus) >= 2:
        notes.append(
            f"scored 3: {len(cost_two_plus)} cost markers are each backed by two or "
            "more different people — enough markers for level 4, but there is no "
            "evidence the complainer is someone who could buy, so level 4 is not met"
        )
    if cost_thin and level <= 2:
        notes.append(
            "cost marker(s) " + ", ".join(sorted(cost_thin))
            + " citable but from a single author each; level 3 needs >=2 distinct authors"
        )

    capped_from = level
    if len(all_authors) <= 1 and level > 2:
        level = 2
        notes.append(
            f"capped {capped_from}->2: every citable marker traces to a single author "
            "(one articulate sufferer is a lead, not a market)"
        )
    if present and present <= {"profanity_urgency"} and level > 2:
        notes.append(
            f"intensity capped from {level} to 2: the only evidence of severity is "
            "angry language. Swearing shows feeling, not cost — a higher score "
            "needs a cost marker (money lost, time quantified, a workaround built)"
        )
        level = 2

    missing = sorted(set(MARKERS) - present)
    if missing:
        notes.append("left false for want of a citable quote: " + ", ".join(missing))

    return {
        "score": level,
        "read": intensity_read(level),
        "markers": {m: m in present for m in MARKERS},
        "note": "; ".join(notes) if notes else None,
        "derivation": {
            "authors_per_marker": {m: sorted(a) for m, a in marker_authors.items() if a},
            "cost_markers_at_2plus_authors": cost_two_plus,
            "cost_markers_single_author": cost_thin,
            "buyer_authors": sorted(buyers),
            "recurring_cost_authors": sorted(recurring_authors),
            "distinct_authors_overall": len(all_authors),
        },
    }


def intensity_read(score: int) -> str:
    """§3.3: 4-5 high, 3 medium, 1-2 low."""
    if score >= 4:
        return "high"
    return "medium" if score == 3 else "low"


def quadrant(frequency_read_value: str, intensity_score: int) -> str:
    """The 2x2 (CONTRACTS §4 enum). High-freq iff read is high; high-intensity iff >=4."""
    freq = "high-freq" if frequency_read_value == "high" else "low-freq"
    pain = "high-intensity" if intensity_score >= 4 else "low-intensity"
    return f"{freq}/{pain}"


QUADRANT_READS = {
    "high-freq/high-intensity": "real and crowded — expect incumbents; the work is the wedge, not proving the problem",
    "low-freq/high-intensity": "possible niche gold — few voices, all bleeding; demand a real buyer before advancing",
    "high-freq/low-intensity": "a content play, not a product — audience/SEO/newsletter, not software someone buys",
    "low-freq/low-intensity": "discard — card written for auditability, do not advance",
}


# --------------------------------------------------------------------------
# Verbatim-quote helpers (the "no quote, no marker" rule, mechanically)
# --------------------------------------------------------------------------

_TYPOGRAPHIC = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ", "…": "...",
}


def normalize_quote(text: str) -> str:
    """Fold typographic variants and collapse whitespace; case is preserved.

    Verbatim means verbatim, but a source's line wrapping and curly apostrophes
    are artefacts of the source, not of the quote. Case is *not* folded: a
    lowercased quote is a rewrite, and `find_span` reports it as one.
    """
    for fancy, plain in _TYPOGRAPHIC.items():
        text = text.replace(fancy, plain)
    return " ".join(text.split())


def word_count(quote: str) -> int:
    """Words in a quote, whitespace-delimited — what `exemplars[].words` records."""
    return len(quote.split())
