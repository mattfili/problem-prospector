#!/usr/bin/env python3
"""The pain-search report and the run-state walk.

`pain-clusters.md` is this stage's terminal artifact — the front half of an
OpportunityCard set, honest about being the front half. It carries the two axes
(§3.3) and nothing else: no willingness-to-pay, no counter-evidence, no trend
reconstruction, no saturation, because none of those has been researched yet and
a panel omitted reads as "not applicable" when it means "we didn't look".

The header rules are §3.8's and they are not decoration. The active sort key is
printed verbatim so a reader can ask for another. The frequency thresholds
actually used are printed because a scaled threshold left unstated makes every
`read` non-reproducible. Source health is printed because a source that failed
must never be readable as "nobody is complaining". And there is no blended
number anywhere — no opportunity score, no weighted sum, no tiers. A single
composite launders judgment into something nobody can audit.

`run_status` is the other half of this module: a read-only walk of the stage
gates that names the next tool to call. It never writes.
"""

from __future__ import annotations

import pain_rubric as rubric
from pain_cards import card_paths
from pain_stages import (
    capture_gate,
    evidence_records,
    read_json,
    read_jsonl,
    run_dir,
)

#: Printed verbatim above the list, every time, including on a re-sort.
SORT_KEY = (
    "intensity.score desc -> frequency.cluster_size desc "
    "(pain-search sort: wtp and saturation are not researched at this stage, so the "
    "CONTRACTS §4 default sort cannot be run yet)"
)


def _cards(slug: str) -> list[dict]:
    """Every card on disk, gate-excluded ones included."""
    return [card for card in (read_json(path) for path in card_paths(slug)) if card]


def _sorted_ranked(cards: list[dict]) -> list[dict]:
    """The printed sort, run rather than eyeballed."""
    ranked = [
        c for c in cards
        if (c.get("inventory_gate") or {}).get("verdict") == "pass" and c.get("intensity")
    ]
    return sorted(
        ranked,
        key=lambda c: (
            -(c["intensity"].get("score") or 0),
            -((c.get("frequency") or {}).get("cluster_size") or 0),
        ),
    )


def _health_line(slug: str) -> str:
    """One-line source health, grouped by status — never collapsed into 'no results'."""
    health, _ = read_jsonl(run_dir(slug) / "source_health.json")
    grouped: dict[str, set[str]] = {}
    for entry in health:
        grouped.setdefault(str(entry.get("status")), set()).add(str(entry.get("source")))
    order = ("ok", "degraded", "unavailable", "skipped", "searched-no-results", "stopped")
    parts = [
        f"{status}: {', '.join(sorted(grouped[status]))}"
        for status in order if grouped.get(status)
    ]
    extra = sorted(set(grouped) - set(order))
    parts += [f"{status}: {', '.join(sorted(grouped[status]))}" for status in extra]
    return " · ".join(parts) or "no health entries recorded"


def _threshold_line(fields: dict | None) -> str:
    """`cluster_size >= 20, distinct_authors >= 12, distinct_communities >= 3`."""
    if not fields:
        return "[not recorded]"
    return ", ".join(f"{name} >= {value}" for name, value in fields.items())


def _card_block(card: dict, rank: int) -> list[str]:
    """One card: the two axes, their evidence, and the 2x2 read. No composite."""
    frequency = card.get("frequency") or {}
    intensity = card.get("intensity") or {}
    true_markers = [m for m, on in (intensity.get("markers") or {}).items() if on]
    lines = [
        f"### {rank}. `{card['cluster_id']}` — {card.get('canonical_pain') or '[no canonical pain]'}",
        "",
        f"**Frequency — `{frequency.get('read')}`** · cluster_size "
        f"{frequency.get('cluster_size')} · distinct_authors "
        f"{frequency.get('distinct_authors')} · distinct_communities "
        f"{frequency.get('distinct_communities')} · engagement_weighted "
        f"{frequency.get('engagement_weighted')}",
    ]
    if frequency.get("note"):
        lines.append(f"  - corrections: {frequency['note']}")
    lines += [
        "",
        f"**Intensity — `{intensity.get('score')}/5` (`{intensity.get('read')}`)** · "
        f"markers true: {', '.join(true_markers) or 'none'}",
    ]
    for exemplar in intensity.get("exemplars") or []:
        lines.append(
            f"  - \"{exemplar['quote']}\" ({exemplar['words']}w) — {exemplar['url']}"
        )
    if intensity.get("note"):
        lines.append(f"  - scoring note: {intensity['note']}")
    quadrant = card.get("quadrant")
    lines += [
        "",
        f"**2x2 — `{quadrant}`** — {rubric.QUADRANT_READS.get(quadrant, '')}",
        "",
        f"provenance: cells {', '.join((card.get('provenance') or {}).get('cell_ids') or [])}"
        f" · personas {', '.join((card.get('provenance') or {}).get('personas') or []) or '[none recorded]'}",
    ]
    flags = (card.get("inventory_gate") or {}).get("flags") or []
    if flags:
        lines.append(f"inventory gate: pass, with flags — {'; '.join(flags)}")
    lines.append("")
    return lines


def _header(slug: str, cards: list[dict], ranked: list[dict]) -> list[str]:
    """§3.8's header: sort key, counts, thresholds used, health, and the stop point."""
    inputs = read_json(run_dir(slug) / "inputs.json") or {}
    calibration = read_json(run_dir(slug) / "cards" / ".calibration.json") or {}
    clusters = read_json(run_dir(slug) / "clusters.json") or {}
    records, per_source = evidence_records(slug)
    excluded = [c for c in cards if (c.get("inventory_gate") or {}).get("verdict") == "exclude"]
    unscored = [
        c for c in cards
        if (c.get("inventory_gate") or {}).get("verdict") != "exclude" and not c.get("intensity")
    ]
    thresholds = calibration.get("thresholds") or {}
    lines = [
        f"# Pain clusters — {inputs.get('inspiration', slug)}",
        "",
        f"`{slug}` · {len(records)} evidence items across "
        f"{len([s for s, n in per_source.items() if n])} responding sources · "
        f"cut_basis `{clusters.get('cut_basis')}`",
        "",
        f"**Sort key:** {SORT_KEY}",
        "",
        "**Counts** — clusters found "
        f"{len(clusters.get('clusters') or [])} · cards written {len(cards)} · "
        f"ranked {len(ranked)} · excluded at the inventory gate {len(excluded)} · "
        f"not yet scored {len(unscored)} · unclustered evidence items "
        f"{len(clusters.get('unclustered_ids') or [])}",
        "",
        "**Frequency thresholds used** — "
        f"high: {_threshold_line(thresholds.get('high'))} · "
        f"medium: {_threshold_line(thresholds.get('medium'))}"
        + (f" · scaled by {calibration.get('factor')} for a "
           f"{calibration.get('total_items')}-item corpus (§3.3 calibrates for "
           f"{rubric.CALIBRATION_RANGE[0]}-{rubric.CALIBRATION_RANGE[1]}; "
           "distinct_communities never scales — it guards against an echo chamber, "
           "not a volume shortfall)"
           if calibration.get("scaled") else " · unscaled"),
        "",
        f"**Source health** — {_health_line(slug)}",
        "",
        "**What this report is not.** Willingness-to-pay, counter-evidence, trend "
        "reconstruction and saturation are unresearched at this stage and are `null` on "
        "every card, not omitted. Nothing here is a business yet: high frequency with "
        "low intensity is a content play, and high intensity with no proven buyer is a "
        "sad hobby. Both reads are settled by the stages this run stopped before.",
        "",
        f"Every cluster is on disk in `runs/{slug}/cards/`, including the excluded and "
        "the unscored. Re-sortable by `frequency.read`, `frequency.cluster_size`, or "
        "`quadrant`.",
        "",
        "---",
        "",
    ]
    disclosures = _disclosures(cards)
    if disclosures:
        # Insert ahead of the trailing "---" / "" that close the header.
        lines[-2:-2] = disclosures
    return lines


#: Where §3.3 contradicts or under-specifies itself, `pain_rubric` resolves it and
#: the resolution is printed. An encoded judgment nobody can see is the thing this
#: pipeline exists to prevent, so these are disclosures, not footnotes.
DISCLOSURES = (
    (
        "monotone reading",
        "**Encoded rubric interpretation — the intensity ladder.** At least one score "
        "was carried by the monotone reading of §3.3's level 3 (\">=1 cost marker at "
        ">=2 distinct authors\", not \"exactly one\"). §3.3's ladder is not total as "
        "written; `scripts/pain_rubric.py` documents the gap and the resolution.",
    ),
    (
        "correction 2",
        "**Encoded rubric interpretation — the community cap.** At least one "
        "single-community cluster was capped at `medium` rather than collapsed to "
        "`low`. §3.3 states `distinct_communities` both as a level threshold and as a "
        "cap-at-medium correction, which cannot both hold; `pain_rubric.frequency_read` "
        "gives the explicit correction precedence and says why.",
    ),
)


def _disclosures(cards: list[dict]) -> list[str]:
    """Header lines for every rubric interpretation that actually bound this run."""
    notes = " ".join(
        str((card.get(panel) or {}).get("note") or "")
        for card in cards for panel in ("intensity", "frequency")
    )
    lines: list[str] = []
    for marker, text in DISCLOSURES:
        if marker in notes:
            lines += [text, ""]
    return lines


def render_report(slug: str) -> dict:
    """Write `runs/<slug>/pain-clusters.md` and return what it contains.

    Renders whatever is on disk. A gate-passing cluster with no intensity panel is
    listed in its own section rather than quietly left out — an unscored cluster
    and a low-scoring one are different findings.
    """
    cards = _cards(slug)
    if not cards:
        return {"ok": False, "error": f"no cards in runs/{slug}/cards — cluster first"}
    ranked = _sorted_ranked(cards)
    lines = _header(slug, cards, ranked)

    for rank, card in enumerate(ranked, start=1):
        lines += _card_block(card, rank)

    excluded = [c for c in cards if (c.get("inventory_gate") or {}).get("verdict") == "exclude"]
    if excluded:
        lines += ["## Excluded at the inventory gate", "",
                  "Visible and unranked, never silently deleted.", ""]
        lines += [
            f"- `{c['cluster_id']}` — {c.get('canonical_pain')} — "
            f"{'; '.join((c.get('inventory_gate') or {}).get('flags') or [])}"
            for c in excluded
        ]
        lines.append("")

    unscored = [
        c for c in cards
        if (c.get("inventory_gate") or {}).get("verdict") != "exclude" and not c.get("intensity")
    ]
    if unscored:
        lines += ["## Not scored", "",
                  "These passed or have not reached the gate and carry no intensity panel. "
                  "Unscored is not low-intensity.", ""]
        lines += [
            f"- `{c['cluster_id']}` — cluster_size "
            f"{(c.get('frequency') or {}).get('cluster_size')} · frequency "
            f"`{(c.get('frequency') or {}).get('read')}` · gate "
            f"`{(c.get('inventory_gate') or {}).get('verdict')}` — {c.get('canonical_pain')}"
            for c in unscored
        ]
        lines.append("")

    lines += [
        "---",
        "",
        "## Where this stops",
        "",
        f"This is a pain-search run: §3.0-§3.3 complete, §3.3b onward not started. "
        f"`/prospect \"{(read_json(run_dir(slug) / 'inputs.json') or {}).get('inspiration', '')}\"` "
        "resumes this same run at Stage 3.5 and will not re-capture — evidence is "
        "append-only and every gate below Stage 3.5 already holds.",
        "",
    ]
    path = run_dir(slug) / "pain-clusters.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "ok": True, "path": str(path), "ranked": len(ranked),
        "excluded": len(excluded), "unscored": len(unscored),
        "quadrants": _quadrant_counts(ranked),
        "top": [
            {"cluster_id": c["cluster_id"], "score": c["intensity"]["score"],
             "frequency_read": (c.get("frequency") or {}).get("read"),
             "quadrant": c.get("quadrant"), "canonical_pain": c.get("canonical_pain")}
            for c in ranked[:10]
        ],
    }


def _quadrant_counts(cards: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for card in cards:
        counts[card.get("quadrant") or "unset"] = counts.get(card.get("quadrant") or "unset", 0) + 1
    return counts


def run_status(slug: str) -> dict:
    """Read-only walk of the pain-search gates; names the next tool to call.

    Restart at the first gate that does not hold. Nothing here writes, so it is
    safe to call at any point, including mid-capture and after a crash.
    """
    directory = run_dir(slug)
    inputs = read_json(directory / "inputs.json")
    if inputs is None:
        return {"stage": "0 — no run", "next": "pain_run_create",
                "detail": f"runs/{slug}/inputs.json does not exist"}

    cells = [c["cell_id"] for c in inputs.get("matrix", [])]
    staged = sorted(p.name for p in (directory / "evidence" / ".staging").glob("*.jsonl"))
    records, per_source = evidence_records(slug)
    gate = capture_gate(slug, record=False)
    clusters = read_json(directory / "clusters.json") or {}
    cards = _cards(slug)
    ungated = [c["cluster_id"] for c in cards if (c.get("inventory_gate") or {}).get("verdict") is None]
    unscored = [
        c["cluster_id"] for c in cards
        if (c.get("inventory_gate") or {}).get("verdict") == "pass" and not c.get("intensity")
    ]
    report = directory / "pain-clusters.md"

    state = {
        "slug": slug, "inspiration": inputs.get("inspiration"),
        "cells": cells, "staging_files": len(staged),
        "evidence_items": len(records), "items_per_source": per_source,
        "capture_gate": gate["decision"], "capture_gate_reasons": gate["reasons"],
        "clusters": len(clusters.get("clusters") or []),
        "cards": len(cards), "ungated_clusters": ungated, "unscored_clusters": unscored,
        "report_written": report.exists(),
    }
    if not staged and not records:
        return {**state, "stage": "2 — capture not started",
                "next": "pain_capture_reddit (every cell), then the relevant "
                        "pain_capture_trends sources, then pain_capture_saturation"}
    if staged and not records:
        return {**state, "stage": "2b — captured, not merged", "next": "pain_merge_staging"}
    if gate["decision"] == "stop":
        return {**state, "stage": "2 — thin capture",
                "next": "widen the matrix or revise queries, then capture again",
                "detail": gate["guidance"]}
    if not clusters.get("clusters"):
        return {**state, "stage": "3 — merged, not clustered", "next": "pain_cluster"}
    if ungated:
        return {**state, "stage": "3 — clustered, gate incomplete",
                "next": f"pain_inventory_gate on {len(ungated)} cluster(s)"}
    if unscored:
        return {**state, "stage": "3 — gated, intensity incomplete",
                "next": f"pain_score_intensity on {len(unscored)} passing cluster(s)"}
    if not report.exists():
        return {**state, "stage": "3 — scored, not rendered", "next": "pain_report"}
    return {
        **state, "stage": "3 complete — pain search done",
        "next": f"nothing here. `/prospect \"{inputs.get('inspiration')}\"` resumes this "
                "run at Stage 3.5 (analysis-pool cap, then wtp/skeptic/retro_trend).",
    }
