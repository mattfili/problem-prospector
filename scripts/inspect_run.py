#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = []
# ///
"""Build the mechanical digest of one run — the inspector's data layer.

Walks `runs/<slug>/` read-only and emits a single JSON object holding
everything the inspection artifact renders: the frame, per-cell capture
counts, source totals, the thin-capture gate verdict (evaluated without
recording a stop), the clustering shape, every card's panels with its
exemplars, the 2x2 quadrant counts, the staged saturation reads, and the
source-health ledger grouped by kind.

Two obligations shape the output:

- **Stage-progressive.** Every section is present-or-null by stage, so the
  same digest renders a run that has only captured, one that has clustered,
  and one that is fully scored. Nothing is fabricated for a stage that has
  not run; a missing file yields null, never an invented empty success.
- **Interpretation is a separate layer.** The digest carries an
  `interpretation` object of nulls. The agent presenting the run fills those
  fields with plain-language readings (per skills/plain-reading); this script
  never writes prose, so the mechanical and judged layers stay separable.

    uv run scripts/inspect_run.py --slug <slug> [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pain_cards import card_paths  # noqa: E402
from pain_stages import capture_gate, evidence_records, read_json, read_jsonl, run_dir  # noqa: E402


def _cells(inputs: dict, per_cell: Counter) -> list[dict]:
    """The frame, joined with how many evidence items each cell yielded."""
    return [
        {
            "cell_id": c.get("cell_id"),
            "persona": c.get("persona"),
            "vertical": c.get("vertical"),
            "framing": c.get("framing"),
            "queries": c.get("queries") or [],
            "subreddits": c.get("subreddits") or [],
            "items": per_cell.get(c.get("cell_id"), 0),
        }
        for c in inputs.get("matrix", [])
    ]


def _card_row(card: dict) -> dict:
    """One card, flattened to what the artifact renders. Panels stay null-honest."""
    freq = card.get("frequency") or {}
    intensity = card.get("intensity") or {}
    gate = card.get("inventory_gate") or {}
    return {
        "cluster_id": card.get("cluster_id"),
        "canonical_pain": card.get("canonical_pain"),
        "cluster_size": freq.get("cluster_size"),
        "distinct_authors": freq.get("distinct_authors"),
        "distinct_communities": freq.get("distinct_communities"),
        "engagement_weighted": freq.get("engagement_weighted"),
        "frequency": freq.get("read"),
        "frequency_note": freq.get("note"),
        "intensity": intensity.get("score"),
        "intensity_note": intensity.get("note"),
        "markers": intensity.get("markers"),
        "exemplars": intensity.get("exemplars") or [],
        "quadrant": card.get("quadrant"),
        "gate_verdict": gate.get("verdict"),
        "gate_flags": gate.get("flags") or [],
    }


def _saturation(slug: str, inputs: dict) -> list[dict]:
    """The staged first competitor reads, one per cell that has one."""
    rows = []
    for cell in inputs.get("matrix", []):
        cid = cell.get("cell_id")
        payload = read_json(run_dir(slug) / "evidence" / ".staging" / f"saturation-{cid}.json")
        sat = (payload or {}).get("saturation") or {}
        if sat:
            rows.append({"cell_id": cid, **{k: sat.get(k) for k in
                        ("competitor_count", "trend_direction", "read", "source")}})
    return rows


def _health(slug: str) -> dict:
    """The source-health ledger: full entries plus counts by kind."""
    entries, _ = read_jsonl(run_dir(slug) / "source_health.json")
    kinds = Counter()
    for e in entries:
        status = str(e.get("status") or "")
        kind = ("zero_result" if status.startswith("searched-no-")
                else status if status in ("ok", "degraded", "unavailable", "skipped", "stopped")
                else "other")
        kinds[kind] += 1
    return {"counts": dict(kinds), "entries": entries}


def build_digest(slug: str) -> dict:
    """Assemble the digest. Read-only; every stage not yet run stays null."""
    root = run_dir(slug)
    if not root.exists():
        raise SystemExit(f"no run at {root} — nothing to inspect")
    inputs = read_json(root / "inputs.json") or {}
    records, per_source = evidence_records(slug)
    per_cell = Counter(str(r.get("cell_id")) for r in records)
    clusters = read_json(root / "clusters.json")
    cards = [c for c in (read_json(p) for p in card_paths(slug)) if c]
    rows = sorted(
        (_card_row(c) for c in cards),
        key=lambda r: (-(r["intensity"] or 0), -(r["cluster_size"] or 0)),
    )
    quadrants = Counter(r["quadrant"] for r in rows if r["quadrant"])
    largest = None
    if clusters and clusters.get("clusters"):
        big = clusters["clusters"][0]
        largest = {
            "cluster_id": big.get("cluster_id"),
            "size": big.get("member_count"),
            "share": round(big.get("member_count", 0) / max(len(records), 1), 3),
        }
    return {
        "slug": slug,
        "inspiration": inputs.get("inspiration"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cells": _cells(inputs, per_cell),
        "evidence_total": len(records),
        "items_per_source": dict(per_source),
        "gate": capture_gate(slug, record=False) if records else None,
        "clustering": {
            "clusters": len(clusters.get("clusters") or []),
            "cut_basis": clusters.get("cut_basis"),
            "unclustered": len(clusters.get("unclustered_ids") or []),
            "largest": largest,
        } if clusters else None,
        "cards": rows or None,
        "quadrants": dict(quadrants) or None,
        "saturation": _saturation(slug, inputs) or None,
        "health": _health(slug),
        "report_path": str(root / "pain-clusters.md")
        if (root / "pain-clusters.md").exists() else None,
        # Filled by the presenting agent per skills/plain-reading; never here.
        "interpretation": {
            "verdict_html": None,
            "frame_note": None,
            "capture_note": None,
            "gate_note": None,
            "clustering_note": None,
            "seams": {},
            "pipeline_note": None,
        },
    }


def main() -> int:
    """CLI: print the digest; --out also persists it under the run."""
    parser = argparse.ArgumentParser(description=(
        "Build the read-only JSON digest of one run for inspection — the frame, "
        "capture counts, gate verdict, clusters, every card, and source health."
    ))
    parser.add_argument("--slug", required=True, help="run directory name under runs/")
    parser.add_argument("--out", help="also write the digest JSON to this path")
    args = parser.parse_args()
    digest = build_digest(args.slug)
    text = json.dumps(digest, indent=1)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
