#!/usr/bin/env python3
"""Stage 3's card file: clustering into cards, and the gate stamped on each.

`cards/<cluster_id>.json` (CONTRACTS §4) is the pipeline's central object, and
this module owns writing it during pain search — seeding one card per cluster with
a *computed* frequency panel, and recording the no-inventory-gate verdict. The
intensity panel is `pain_intensity.py`'s, because validating quoted evidence is a
different job from laying out a card.

Split out of `pain_stages.py` when that file crossed the 600-line ceiling: the seam
is real, not cosmetic. Run state, capture merging and the thin-capture stop are
about a *run*; everything here is about a *card*.
"""

from __future__ import annotations

import json
from pathlib import Path

import pain_rubric as rubric
from pain_stages import evidence_records, invoke, read_json, run_dir


# --------------------------------------------------------------------------
# Stage 3 — cluster, then seed one card per cluster
# --------------------------------------------------------------------------

def cluster_and_seed_cards(
    slug: str,
    percentile: float | None = None,
    min_cluster_size: int | None = None,
    reseed: bool = False,
) -> dict:
    """Run `cluster.py`, then write one card per cluster with the frequency panel.

    After this, the cluster is the unit of analysis and never the raw post: 400
    phrasings of one pain is one cluster of weight 400. The frequency panel is
    derived here rather than by a model — thresholds, the repetition demotion, the
    echo-chamber cap and the engagement promotion are arithmetic (`pain_rubric`),
    and the thresholds actually used are recorded for the report header.

    Re-clustering at a different `--percentile` invalidates every cluster id, so
    any intensity already scored is discarded. That needs `reseed=True`, stated
    out loud. Hand-merging or hand-splitting clusters is never the move: it leaves
    `cut_basis` lying about what produced the shape.
    """
    directory = run_dir(slug)
    scored = [
        path.stem for path in card_paths(slug)
        if (read_json(path) or {}).get("intensity")
    ]
    if scored and not reseed:
        return {
            "ok": False,
            "error": f"{len(scored)} card(s) already carry a scored intensity panel "
                     f"({', '.join(scored[:5])}...). Re-clustering changes every "
                     "cluster_id and discards that work. Pass reseed=true to proceed.",
        }

    args = [str(directory / "evidence"), "--out", str(directory / "clusters.json"),
            "--run-slug", slug]
    if percentile is not None:
        args += ["--percentile", str(percentile)]
    if min_cluster_size is not None:
        args += ["--min-cluster-size", str(min_cluster_size)]
    result = invoke("cluster.py", args)
    if not result["ok"]:
        return {"ok": False, "error": result["error"], "stderr_tail": result["stderr_tail"]}

    payload = read_json(directory / "clusters.json") or {}
    clusters = payload.get("clusters") or []
    if not clusters:
        return {"ok": False, "error": "cluster.py produced no clusters", "payload": payload}

    records, _ = evidence_records(slug)
    calibration = rubric.scaled_thresholds(len(records))
    decile = rubric.engagement_top_decile(clusters)
    inputs = read_json(directory / "inputs.json") or {}
    personas = {c["cell_id"]: c.get("persona") for c in inputs.get("matrix", [])}

    for path in card_paths(slug):
        path.unlink()
    written = [
        _seed_card(directory, cluster, calibration["thresholds"], decile, personas)
        for cluster in clusters
    ]
    (directory / "cards" / ".calibration.json").write_text(
        json.dumps({**calibration, "engagement_top_decile": decile}, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "ok": True, "clusters": len(clusters),
        "cards_written": len(written),
        "cut_basis": payload.get("cut_basis"),
        "unclustered_items": len(payload.get("unclustered_ids") or []),
        "frequency_thresholds_used": calibration,
        "engagement_top_decile": decile,
        "cards": written,
        "next": "Set the inventory gate on every cluster, then score intensity on "
                "those that pass. Both are per-cluster.",
    }


def _seed_card(
    directory: Path,
    cluster: dict,
    thresholds: dict,
    decile: float | None,
    personas: dict[str, str | None],
) -> dict:
    """Write one CONTRACTS §4 card with frequency filled and the rest null."""
    read, note = rubric.frequency_read(cluster, thresholds, decile)
    cell_ids = cluster.get("cell_ids") or []
    card = {
        "cluster_id": cluster["cluster_id"],
        "canonical_pain": cluster.get("canonical"),
        "provenance": {
            "cell_ids": cell_ids,
            "personas": sorted({p for p in (personas.get(c) for c in cell_ids) if p}),
        },
        "frequency": {
            "cluster_size": cluster.get("member_count"),
            "distinct_authors": cluster.get("distinct_authors"),
            "distinct_communities": cluster.get("distinct_communities"),
            "engagement_weighted": cluster.get("engagement_sum"),
            "read": read,
            "note": note,
        },
        "intensity": None,
        "quadrant": None,
        "wtp": None,
        "skeptic": None,
        "retro_trend": None,
        "saturation": None,
        "inventory_gate": {"verdict": None, "flags": []},
    }
    (directory / "cards" / f"{cluster['cluster_id']}.json").write_text(
        json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "cluster_id": card["cluster_id"],
        "cluster_size": card["frequency"]["cluster_size"],
        "distinct_authors": card["frequency"]["distinct_authors"],
        "frequency_read": read,
        "canonical": card["canonical_pain"],
    }


def card_paths(slug: str) -> list[Path]:
    """Every card file, dot-prefixed scratch excluded.

    `pathlib.Path.glob("*.json")` matches dotfiles; shell globbing and `jq
    cards/*.json` do not. The dot prefix is this repo's scratch convention
    (`cards/.calibration.json`, `/prospect`'s `cards/.analysis-rank.jsonl`), so
    reading it through pathlib without this filter hands a consumer a scratch file
    shaped nothing like a card — a KeyError at best, a miscount at worst.
    """
    return [
        path for path in sorted((run_dir(slug) / "cards").glob("*.json"))
        if not path.name.startswith(".")
    ]


def load_card(slug: str, cluster_id: str) -> tuple[Path, dict]:
    path = run_dir(slug) / "cards" / f"{cluster_id}.json"
    card = read_json(path)
    if card is None:
        raise ValueError(f"no card at runs/{slug}/cards/{cluster_id}.json — cluster first")
    return path, card


def write_card(path: Path, card: dict) -> None:
    path.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def set_inventory_gate(slug: str, cluster_id: str, verdict: str, flags: list[str]) -> dict:
    """Record the no-inventory-gate verdict (§3.7) for one cluster.

    Two legal verdicts, `pass` and `exclude`, and the spellings are load-bearing:
    the economist and the skeptic preflight on `verdict == "exclude"`, so
    `"excluded"` in that field silently sends both to work on a card the gate
    killed. An exclusion's first flag carries the `excluded:` prefix; this
    normalizes it rather than leaving the class of bug open, and says that it did.
    """
    if verdict not in ("pass", "exclude"):
        return {"ok": False, "error": 'verdict must be "pass" or "exclude"'}
    flags = [str(f) for f in (flags or []) if str(f).strip()]
    normalized = False
    if verdict == "exclude":
        if not flags:
            return {"ok": False, "error": "an exclusion must carry its reason in flags"}
        if not flags[0].startswith("excluded:"):
            flags[0] = f"excluded: {flags[0]}"
            normalized = True
    path, card = load_card(slug, cluster_id)
    card["inventory_gate"] = {"verdict": verdict, "flags": flags}
    if verdict == "exclude":
        card["intensity"] = None
        card["quadrant"] = None
    write_card(path, card)
    return {
        "ok": True, "cluster_id": cluster_id, "verdict": verdict, "flags": flags,
        "flag_prefix_normalized": normalized,
        "next": "score_intensity" if verdict == "pass" else
                "excluded cards keep null intensity/quadrant by design; they still "
                "appear in the report's own section, never silently dropped",
    }
