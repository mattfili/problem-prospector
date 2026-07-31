#!/usr/bin/env python3
"""Validate that a script's stdout is clean, parseable JSON carrying source_health.

Two invariants every script in this repo owes its callers:

  1. stdout is valid JSON and NOTHING else. Agents parse it directly with no
     wrapper, so one stray print() breaks the pipeline in a way that is very
     hard to diagnose from inside an agent.
  2. a `source_health` block is present. Without it a degraded source becomes
     indistinguishable from an empty result, which is the single most damaging
     failure this tool could have.

    uv run scripts/foo.py --args | python3 tests/validate_stdout.py
"""

import json
import sys


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        print("empty stdout", file=sys.stderr)
        return 1
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Show the head of the offending output — usually a log line that
        # escaped to stdout instead of stderr.
        print(f"stdout is not clean JSON ({exc})", file=sys.stderr)
        print(f"first 200 chars: {raw[:200]!r}", file=sys.stderr)
        return 1

    if isinstance(doc, dict) and "source_health" not in doc:
        print(f"missing source_health (keys: {sorted(doc)[:12]})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
