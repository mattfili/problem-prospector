#!/usr/bin/env python3
"""Stages 0b-3 of /prospect — the pain-point search, as callable stages.

WHY THIS EXISTS
---------------
Pain-point search is the front half of `/prospect`: frame, capture, merge,
cluster, score frequency and intensity, gate for physical inventory, report. It
ends exactly where the expensive half begins (§3.3b's analysis-pool cap, then
the economist/skeptic/historian fan-out), which makes it the natural place to
stop, read, and decide whether to spend the rest.

Every stage here was previously carried as prose in `commands/prospect.md` and
`agents/distiller.md` and executed by a model reading that prose. The rules that
matter most are the ones a model can skip without any error appearing: never pass
`--min-score`, always pass `--comments`, never capture an out-of-enum source,
never set a marker without a verbatim quote, never write a blended score. Here
those rules are structural — `capture_reddit` has no parameter that can express
a score floor, `capture_trends` has no source value outside the queryable enum,
and `score_intensity` computes the score from evidence on disk rather than
accepting a claim.

WHAT STAYS WITH THE MODEL
-------------------------
The frame (personas x verticals x framings), which quote evidences which marker,
the canonical pain sentence, and the inventory-gate verdict. Judgment in,
arithmetic and enforcement here.

CONTRACTS
---------
Reads and writes only the shapes in `docs/CONTRACTS.md` §1-§4 plus cross-cutting
rule 5. Adds one artifact of its own, `runs/<slug>/pain-clusters.md`, rendered by
`pain_report.py`. A run left by this module is a legal Stage-3-complete run:
`/prospect` on the same slug resumes at Stage 3.5 and never re-captures.

Stdlib only, so it can be imported by a PEP 723 script without widening its
dependency set.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


#: Where this bundle's code lives. Used only to invoke the sibling capture scripts.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def workspace_root() -> Path:
    """Where `runs/` lives: the user's project, never this bundle's install directory.

    These two are the same directory in a git checkout and very different once the
    bundle is installed as a plugin, where `PLUGIN_ROOT` is a versioned cache path
    like `~/.claude/plugins/cache/<mp>/<plugin>/<version>/`. Writing run state there
    would put a user's research inside a directory the next `plugin update` replaces,
    and would hide it from the project they are working in. The prose commands have
    always written a relative `runs/<slug>`, i.e. into the caller's project, and this
    keeps that behaviour when the same stages run inside an MCP server instead.

    Resolution order: `PROSPECTOR_RUNS_ROOT` (explicit override, for tests and for
    pinning a run somewhere specific), then `CLAUDE_PROJECT_DIR` (set by the host),
    then the process working directory.
    """
    for variable in ("PROSPECTOR_RUNS_ROOT", "CLAUDE_PROJECT_DIR"):
        value = os.environ.get(variable)
        if value and value.strip():
            return Path(value).expanduser().resolve()
    return Path.cwd().resolve()

#: CONTRACTS §2 `source` enum. Closed — every downstream consumer keys on it.
EVIDENCE_SOURCES = (
    "reddit", "hackernews", "stackoverflow", "producthunt", "github",
    "pypi", "npm", "wikipedia", "google-trends", "dialog",
)

#: In-enum AND keyword-searchable via trend-pulse. The rest of the enum is
#: trending-only: those feeds are unrelated to a cell's framing and would still
#: cluster, so they are recorded `degraded` and never captured per-cell.
QUERYABLE_SOURCES = ("hackernews", "stackoverflow", "producthunt")

TRENDING_ONLY_SOURCES = ("github", "pypi", "npm", "wikipedia", "google-trends")

#: The two cross-cutting-rule-5 statuses that record a *decision not to capture*.
#: `ok` / `unavailable` / `searched-no-results` are the scripts' to report, not
#: a caller's to assert.
DECISION_STATUSES = ("skipped", "degraded")

THIN_CAPTURE_MIN_ITEMS = 40
THIN_CAPTURE_MIN_SOURCES = 3

_CELL_SUFFIX = re.compile(r"-(?P<cell>[a-z]\d+)$")


# --------------------------------------------------------------------------
# Run paths and small file helpers
# --------------------------------------------------------------------------

def run_dir(slug: str) -> Path:
    """`<workspace>/runs/<slug>/`, absolute. Never trusts a caller-supplied path.

    Rooted at `workspace_root()`, not `PLUGIN_ROOT` — see that function for why the
    distinction matters once this bundle is an installed plugin.
    """
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", slug):
        raise ValueError(f"invalid slug {slug!r}: expected kebab-case, <=81 chars")
    return workspace_root() / "runs" / slug


def slugify(inspiration: str, run_date: str | None = None) -> str:
    """The Stage 0 slug recipe, exactly — `/rescan` has to find the run again."""
    stem = re.sub(r"[^a-z0-9]+", "-", inspiration.lower()).strip("-")
    if len(stem) > 40:
        head = stem[:40]
        stem = head.rsplit("-", 1)[0] if "-" in head else head
    date = run_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return stem.strip("-") + "-" + date


def read_json(path: Path) -> Any:
    """Parse a JSON file, or `None` when it is absent."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> tuple[list[dict], int]:
    """Parse a JSONL file tolerantly, returning `(objects, dropped_line_count)`.

    The jq equivalent is `fromjson?`: a truncated line is skipped rather than
    failing the whole read. The dropped count is returned, never swallowed — a
    silently dropped line is a lost record with no error message.
    """
    if not path.exists():
        return [], 0
    objects: list[dict] = []
    dropped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            dropped += 1
            continue
        if isinstance(parsed, dict):
            objects.append(parsed)
        else:
            dropped += 1
    return objects, dropped


def append_health(slug: str, entries: list[dict]) -> int:
    """Append cross-cutting-rule-5 lines to `source_health.json` (JSONL at run root)."""
    path = run_dir(slug) / "source_health.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return len(entries)


def evidence_records(slug: str) -> tuple[list[dict], dict[str, int]]:
    """Every merged evidence record, plus per-source counts."""
    records: list[dict] = []
    counts: dict[str, int] = {}
    for path in sorted((run_dir(slug) / "evidence").glob("*.jsonl")):
        parsed, _ = read_jsonl(path)
        records.extend(parsed)
        counts[path.stem] = len(parsed)
    return records, counts


# --------------------------------------------------------------------------
# Script invocation (the guaranteed key-free path for every capture)
# --------------------------------------------------------------------------

def invoke(script: str, args: list[str], timeout: float = 1800.0) -> dict:
    """Run one PEP 723 capture script and parse its stdout JSON.

    The timeout is language-level on purpose: stock macOS has no `timeout(1)`, so
    a shell-wrapped probe silently becomes a no-op and a dead source looks like a
    slow one. Never raises for a failed source — a failure is data (`ok: false`
    plus the stderr tail), because the caller has to record it as health rather
    than as an absence of discussion.
    """
    command = ["uv", "run", "--quiet", f"scripts/{script}", *args]
    try:
        proc = subprocess.run(
            command, cwd=PLUGIN_ROOT, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "script": script, "exit_code": None, "payload": None,
            "error": f"{script} exceeded {timeout}s and was killed",
            "stderr_tail": None,
        }
    payload: Any = None
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        payload = None
    tail = "\n".join(proc.stderr.strip().splitlines()[-8:]) or None
    return {
        "ok": payload is not None,
        "script": script,
        "exit_code": proc.returncode,
        "payload": payload,
        "error": None if payload is not None else f"{script} produced no parseable JSON on stdout",
        "stderr_tail": tail,
    }


# --------------------------------------------------------------------------
# Stage 1 — the frame
# --------------------------------------------------------------------------

def validate_matrix(matrix: list[dict]) -> list[str]:
    """Stage-1 gate: the shape checks that must hold before any capture runs.

    Composition is checked but never repaired — §3.0's two requirements (a buyer
    persona and a sufferer-who-cannot-buy; one inverted framing) are the frame
    stage's judgment, and a tool that silently rewrote the matrix would make the
    run unreproducible against `inputs.json`.
    """
    problems: list[str] = []
    if not 6 <= len(matrix) <= 12:
        problems.append(f"matrix holds {len(matrix)} cells; §3.0 requires 6-12")
    seen: set[str] = set()
    for index, cell in enumerate(matrix):
        label = cell.get("cell_id") or f"[{index}]"
        for field in ("cell_id", "persona", "vertical", "framing"):
            if not str(cell.get(field) or "").strip():
                problems.append(f"cell {label}: {field} is empty")
        queries = cell.get("queries") or []
        if not 3 <= len(queries) <= 6:
            problems.append(f"cell {label}: {len(queries)} queries; §3.0 wants 3-6")
        if any(not str(q or "").strip() for q in queries):
            problems.append(f"cell {label}: an empty query string")
        cid = cell.get("cell_id")
        if cid in seen:
            problems.append(f"duplicate cell_id {cid}")
        seen.add(cid)
        if not re.fullmatch(r"[a-z]\d{2}", str(cid or "")):
            problems.append(f"cell_id {cid!r} must match <letter><2 digits>, e.g. m01")
    return problems


def create_run(
    inspiration: str,
    matrix: list[dict],
    niche: str | None = None,
    top: int = 5,
    run_date: str | None = None,
) -> dict:
    """Write `inputs.json` — before any capture, so the run is auditable.

    Refuses on a failing Stage-1 gate rather than capturing against a half-written
    frame. Idempotent for an existing slug only when the frame is unchanged;
    otherwise it refuses, because silently replacing a frame mid-run detaches
    every already-captured record's `cell_id` from its meaning.
    """
    problems = validate_matrix(matrix)
    if problems:
        return {"ok": False, "stage": "frame", "problems": problems}

    slug = slugify(inspiration, run_date)
    directory = run_dir(slug)
    existing = read_json(directory / "inputs.json")
    if existing is not None and existing.get("matrix") != matrix:
        return {
            "ok": False, "stage": "frame", "slug": slug,
            "problems": [
                f"runs/{slug}/inputs.json already exists with a different matrix. "
                "This is a resume, not a fresh run: call pain_run_status(slug) and "
                "continue, or re-run with a different inspiration."
            ],
        }

    (directory / "evidence" / ".staging").mkdir(parents=True, exist_ok=True)
    (directory / "cards").mkdir(parents=True, exist_ok=True)
    payload = {
        "slug": slug,
        "inspiration": inspiration,
        "created_utc": int(datetime.now(timezone.utc).timestamp()),
        "flags": {
            "wtp": None, "pain": None, "niche": niche,
            "cards_only": True, "top": int(top),
        },
        "matrix": matrix,
    }
    (directory / "inputs.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "ok": True, "slug": slug, "run_dir": str(directory),
        "cells": [c["cell_id"] for c in matrix],
        "note": "flags.cards_only is true: a pain-search run stops before wedging.",
    }


def cell(slug: str, cell_id: str) -> dict:
    """One matrix cell, verbatim from `inputs.json`."""
    inputs = read_json(run_dir(slug) / "inputs.json") or {}
    for candidate in inputs.get("matrix", []):
        if candidate.get("cell_id") == cell_id:
            return candidate
    raise ValueError(f"cell {cell_id} is not in runs/{slug}/inputs.json")


# --------------------------------------------------------------------------
# Stage 2b — merge staging into the contract paths
# --------------------------------------------------------------------------

def merge_staging(slug: str) -> dict:
    """Merge `.staging/<source>-<cell>.jsonl` into `evidence/<source>.jsonl`.

    Parallel captures appending to one file produce interleaved half-lines that
    `cluster.py` rejects, usually discovered twenty minutes later — so captures
    stage and this merges, deduping on `id`. Idempotent and safe to re-run;
    staging files are never deleted, because they already cost rate limit.

    Every dropped malformed line is counted and reported. A dropped health line
    is a lost degradation record, which is the one thing that file exists for.

    Also collapses the one duplicate that id-level dedup structurally cannot see: a
    post captured by both Arctic Shift and the dialog MCP carries two different ids,
    because the id recipe hashes the source name alongside the URL. See
    `_collapse_reddit_family`.
    """
    staging = run_dir(slug) / "evidence" / ".staging"
    evidence = run_dir(slug) / "evidence"
    merged: dict[str, dict] = {}
    off_enum: list[str] = []
    dropped_evidence = 0

    for path in sorted(staging.glob("*.jsonl")):
        stem = _CELL_SUFFIX.sub("", path.stem)
        if stem == "health":
            continue
        if stem not in EVIDENCE_SOURCES:
            off_enum.append(path.name)
            continue
        records, dropped = read_jsonl(path)
        dropped_evidence += dropped
        merged.setdefault(stem, {"records": {}, "staged": 0})
        merged[stem]["staged"] += len(records)
        for record in records:
            if record.get("id"):
                merged[stem]["records"].setdefault(record["id"], record)

    written: dict[str, int] = {}
    for source, bundle in merged.items():
        target = evidence / f"{source}.jsonl"
        existing, dropped = read_jsonl(target)
        dropped_evidence += dropped
        combined: dict[str, dict] = {}
        for record in existing:
            if record.get("id"):
                combined.setdefault(record["id"], record)
        for record_id, record in bundle["records"].items():
            combined.setdefault(record_id, record)
        target.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in combined.values()),
            encoding="utf-8",
        )
        written[source] = len(combined)

    collapsed = _collapse_reddit_family(evidence)
    if collapsed["collapsed"]:
        written = {s: n for s, n in written.items()}
        for source, count in collapsed["dropped_from"].items():
            written[source] = max(0, written.get(source, 0) - count)
        append_health(slug, [{
            "source": "reddit-family-dedup", "status": "ok", "fallback": None,
            "detail": f"{collapsed['collapsed']} post(s) captured by both Arctic Shift and "
                      f"dialog collapsed to one record each ({collapsed['dropped_from']}); "
                      "without this they would double the cluster weight of the same pain",
        }])

    health_merged, health_dropped = _merge_health(slug, staging)
    if health_dropped:
        append_health(slug, [{
            "source": "source_health-merge", "status": "degraded", "fallback": None,
            "detail": f"{health_dropped} staged health line(s) dropped as malformed "
                      "during merge; that many degradation records are lost",
        }])
    if off_enum:
        append_health(slug, [{
            "source": "staging-merge", "status": "degraded", "fallback": None,
            "detail": "off-enum staging file(s) not merged, nothing downstream reads "
                      f"them: {', '.join(off_enum)}",
        }])
    return {
        "ok": True, "evidence_files": written, "health_lines_merged": health_merged,
        "malformed_evidence_lines_dropped": dropped_evidence,
        "malformed_health_lines_dropped": health_dropped,
        "off_enum_staging_files": off_enum,
        "cross_source_duplicates_collapsed": collapsed,
    }


#: Sources that can hold the *same* Reddit post under different ids. The §2 id
#: recipe is sha1(source + url), so one post captured by both Arctic Shift and the
#: dialog MCP yields two distinct ids, survives id-level dedup, and silently
#: doubles the cluster weight of whatever pain it expresses — inflating the one
#: number every later stage trusts most.
REDDIT_FAMILY = ("reddit", "dialog")


def _collapse_reddit_family(evidence: Path) -> dict:
    """Drop cross-source duplicate Reddit posts, keeping the more complete record.

    Runs after the per-source merge. For a URL present in both `reddit.jsonl` and
    `dialog.jsonl`, the survivor is whichever carries more body text — dialog pulls
    comment trees and Arctic Shift pulls comments too, so "longer" is the better
    proxy for "more evidence" than either source's name. Ties go to `reddit`,
    because that file was written by a script whose shape is contract-guaranteed.
    """
    loaded: dict[str, dict[str, dict]] = {}
    for source in REDDIT_FAMILY:
        records, _ = read_jsonl(evidence / f"{source}.jsonl")
        loaded[source] = {str(r.get("url") or "").rstrip("/"): r for r in records if r.get("url")}

    shared = set(loaded["reddit"]) & set(loaded["dialog"])
    if not shared:
        return {"collapsed": 0, "dropped_from": {}}

    dropped: dict[str, int] = {}
    for url in shared:
        reddit_len = len(str(loaded["reddit"][url].get("text") or ""))
        dialog_len = len(str(loaded["dialog"][url].get("text") or ""))
        loser = "dialog" if dialog_len <= reddit_len else "reddit"
        del loaded[loser][url]
        dropped[loser] = dropped.get(loser, 0) + 1

    for source in REDDIT_FAMILY:
        path = evidence / f"{source}.jsonl"
        if not path.exists():
            continue
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in loaded[source].values()),
            encoding="utf-8",
        )
    return {"collapsed": len(shared), "dropped_from": dropped}


def _merge_health(slug: str, staging: Path) -> tuple[int, int]:
    """Append staged `health-<cell>.jsonl` lines into `source_health.json`."""
    entries: list[dict] = []
    dropped = 0
    for path in sorted(staging.glob("health-*.jsonl")):
        parsed, bad = read_jsonl(path)
        entries.extend(parsed)
        dropped += bad
    if entries:
        append_health(slug, entries)
    return len(entries), dropped


# --------------------------------------------------------------------------
# The thin-capture stop
# --------------------------------------------------------------------------

def capture_gate(slug: str, record: bool = True) -> dict:
    """§3.1's thin-capture stop: is there enough evidence to cluster at all?

    Clustering 11 posts yields clusters of size 2 rendered with the same
    confident formatting as clusters of size 47. Under 40 items, or fewer than
    three sources returning anything, this stops the run and records the stop —
    and names failed sources separately from zero-result ones, because a rate
    limit read as "nobody is complaining" inverts the entire run.

    Returns `decision`, not `verdict`: `proceed`/`stop` is a different enum from
    `inventory_gate.verdict`'s `pass`/`exclude`, and two enums sharing one field
    name is the drift class `tests/validate_enums.py` exists to catch.

    `record=False` evaluates the gate without appending the `stopped` health line,
    so `run_status` can report the verdict without writing a stop that never
    happened.
    """
    records, per_source = evidence_records(slug)
    health, _ = read_jsonl(run_dir(slug) / "source_health.json")
    responding = sorted(s for s, n in per_source.items() if n > 0)

    by_status: dict[str, list[str]] = {}
    for entry in health:
        by_status.setdefault(str(entry.get("status")), []).append(
            f"{entry.get('source')} ({entry.get('detail')})"
        )

    decision = "proceed"
    reasons: list[str] = []
    if len(records) < THIN_CAPTURE_MIN_ITEMS:
        decision = "stop"
        reasons.append(f"{len(records)} items < {THIN_CAPTURE_MIN_ITEMS}")
    if len(responding) < THIN_CAPTURE_MIN_SOURCES:
        decision = "stop"
        reasons.append(f"{len(responding)} responding sources < {THIN_CAPTURE_MIN_SOURCES}")

    if decision == "stop" and record:
        append_health(slug, [{
            "source": "capture", "status": "stopped", "fallback": None,
            "detail": "thin-capture gate: " + "; ".join(reasons)
                      + "; needs a wider matrix or complainer-vocabulary queries",
        }])
    return {
        "decision": decision, "reasons": reasons, "total_items": len(records),
        "items_per_source": per_source, "responding_sources": responding,
        "failed": by_status.get("unavailable", []),
        "degraded": by_status.get("degraded", []),
        "skipped": by_status.get("skipped", []),
        "zero_result": [v for k, v in by_status.items() if k.startswith("searched-no-")
                        for v in ([v] if isinstance(v, str) else v)],
        "guidance": (
            "Do not cluster. Widen the matrix or revise queries into complainer "
            "vocabulary, then capture again. A source that failed is never reported "
            "as 'no discussion found'." if decision == "stop" else
            "Gate holds — cluster next."
        ),
    }
