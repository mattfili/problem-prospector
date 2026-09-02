#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["mcp>=1.9,<2"]
# ///
"""Assert the pain-search tool surface enforces what its docs claim.

The argument for exposing these stages as tools rather than prose is that a
forbidden call becomes *unrepresentable* — there is no `min_score` parameter to
pass, no source outside the queryable enum to choose. That is a claim about the
published JSON Schema, and an unchecked claim of that kind decays the first time
somebody adds a convenience parameter. So it is checked here.

Introspects the registered tools in-process rather than over stdio: the schema is
the same object either way, and this runs in under a second with no subprocess.

    uv run --quiet tests/validate_tool_schemas.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pain_mcp  # noqa: E402

#: Parameters no tool may expose, and why each one is forbidden.
FORBIDDEN = {
    "min_score": "the forbidden capture filter; the archive snapshots score at "
                 "ingest, so a floor deletes the newest evidence first",
    "after": "historical windowing belongs to the retro-trend stage, not capture",
    "before": "historical windowing belongs to the retro-trend stage, not capture",
    "score": "intensity is derived from validated quotes, never asserted",
    "signal_strength": "a blended score is banned everywhere in this pipeline",
    "opportunity_score": "a blended score is banned everywhere in this pipeline",
}

#: Enums that must reach the schema, so an off-contract value cannot be sent.
REQUIRED_ENUMS = {
    ("pain_capture_trends", "source"): {"hackernews", "stackoverflow", "producthunt"},
    ("pain_record_source_decision", "status"): {"skipped", "degraded"},
    ("pain_inventory_gate", "verdict"): {"pass", "exclude"},
}

EXPECTED_TOOLS = {
    "pain_run_create", "pain_run_status", "pain_capture_reddit", "pain_capture_trends",
    "pain_capture_saturation", "pain_record_source_decision", "pain_merge_staging",
    "pain_ingest_records",
    "pain_capture_gate", "pain_cluster", "pain_inventory_gate", "pain_score_intensity",
    "pain_report", "pain_run_digest",
}


def enum_values(spec: dict) -> set[str]:
    """Enum members from a property schema, through any anyOf/$ref indirection."""
    if "enum" in spec:
        return set(spec["enum"])
    values: set[str] = set()
    for branch in spec.get("anyOf", []) + spec.get("allOf", []):
        values |= enum_values(branch)
    return values


def main() -> int:
    tools = asyncio.run(pain_mcp.mcp.list_tools())
    failures: list[str] = []

    names = {tool.name for tool in tools}
    if missing := EXPECTED_TOOLS - names:
        failures.append(f"FAIL missing tool(s): {sorted(missing)}")
    if extra := names - EXPECTED_TOOLS:
        failures.append(f"FAIL undeclared tool(s) {sorted(extra)} — add them here on purpose")

    for tool in tools:
        properties = tool.inputSchema.get("properties", {})
        for parameter, why in FORBIDDEN.items():
            if parameter in properties:
                failures.append(f"FAIL {tool.name} exposes `{parameter}`: {why}")
        if not (tool.description or "").strip():
            failures.append(f"FAIL {tool.name} has no description — the schema IS the doc")

    schemas = {tool.name: tool.inputSchema for tool in tools}
    for (tool_name, parameter), wanted in REQUIRED_ENUMS.items():
        spec = schemas.get(tool_name, {}).get("properties", {}).get(parameter)
        if spec is None:
            failures.append(f"FAIL {tool_name}.{parameter} is absent")
            continue
        found = enum_values(spec)
        if found != wanted:
            failures.append(
                f"FAIL {tool_name}.{parameter} enum is {sorted(found)}, expected {sorted(wanted)}"
            )

    # `limit` is legal on the trends tool (a per-source cap) and nowhere else --
    # on a Reddit pull it would truncate the frequency denominator.
    for tool in tools:
        if "limit" in tool.inputSchema.get("properties", {}) and tool.name != "pain_capture_trends":
            failures.append(f"FAIL {tool.name} exposes `limit`; only pain_capture_trends may")

    if failures:
        print("\n".join(failures))
        return 1
    print(json.dumps({
        "tools": len(tools),
        "forbidden_parameters_absent": sorted(FORBIDDEN),
        "enums_enforced": [f"{t}.{p}" for t, p in sorted(REQUIRED_ENUMS)],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
