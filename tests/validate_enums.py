#!/usr/bin/env python3
"""Scan agents/, commands/, and skills/ for CONTRACTS §4 enum drift.

This check exists because enum drift actually happened during the build: three
agents agreed on `inventory_gate.verdict == "excluded"` while CONTRACTS defined
`"exclude"`. Internally consistent, contract-noncompliant, and completely
invisible until a conformant consumer sees a value it does not recognize.

Only flags values written in a JSON-ish assignment position (`"field": "value"`
or `field == "value"`), so prose that mentions a wrong spelling in order to
warn against it does not trip the check. That distinction matters here: several
files deliberately say 'write "exclude", never "excluded"', and punishing that
would discourage exactly the diligence we want.

    python3 tests/validate_enums.py
"""

import pathlib
import re
import sys

# Straight from docs/CONTRACTS.md §4 "Enums".
ENUMS: dict[str, set[str]] = {
    "verdict": {"pass", "exclude"},
    "buyer_class": {"b2b-operator", "prosumer", "hobbyist"},
    "quadrant": {
        "high-freq/high-intensity",
        "low-freq/high-intensity",
        "high-freq/low-intensity",
        "low-freq/low-intensity",
    },
    "shape": {
        "emerging",
        "accelerating",
        "persistent-flat",
        "declining",
        "spiky-episodic",
    },
    "coverage": {"good", "thin", "none"},
    # source_health statuses, plus crawl.py's separate per-URL manifest enum.
    # Both are checked under the same field name because both are written as
    # `"status": "..."`; the union is acceptable since the two vocabularies do
    # not collide on any value with a different meaning.
    "status": {
        "ok",
        "degraded",
        "unavailable",
        "skipped",
        "searched-no-results",
        "stopped",
        # crawl.py manifest
        "blocked",
        "robots-denied",
        "failed",
    },
}

# `searched-no-results` may carry a specificity suffix where the distinction
# has analytic weight (e.g. `searched-no-counterevidence` drives the skeptic's
# under_researched flag). Same for the deliberate-halt statuses.
SUFFIXABLE = ("searched-no-", "stopped", "skipped")

# `read` is deliberately excluded: frequency/intensity/wtp use high|medium|low
# but saturation.read is free text passed through from the upstream tool, so a
# field-name-only check would produce false positives on the honest case.

SEARCH_DIRS = ["agents", "commands", "skills"]

# "field": "value"   |   field == "value"   |   .field == "value"
ASSIGN = re.compile(
    r'["\']?\.?(?P<field>[a-z_]+)["\']?\s*(?::|==)\s*["\'](?P<value>[^"\']{1,40})["\']'
)


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    violations: list[str] = []

    for d in SEARCH_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            # The vendored marketing tree is upstream code with its own
            # vocabulary; our enums do not apply to it.
            if "skills/marketing/" in str(path.relative_to(root)):
                continue
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                for m in ASSIGN.finditer(line):
                    field, value = m.group("field"), m.group("value")
                    allowed = ENUMS.get(field)
                    if allowed is None or value in allowed:
                        continue
                    # Placeholders and template markers are not claims.
                    if value.startswith(("<", "$", "{", "[")) or value in {"...", "null"}:
                        continue
                    if field == "status" and value.startswith(SUFFIXABLE):
                        continue
                    violations.append(
                        f"{path.relative_to(root)}:{lineno}: "
                        f'{field} = "{value}" not in {sorted(allowed)}'
                    )

    if violations:
        print(f"enum drift ({len(violations)}):", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
