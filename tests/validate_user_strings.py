#!/usr/bin/env python3
"""Scan the pain modules for runtime messages a user cannot act on.

Three invariants, each of which was violated on the tree this check was born
on (measured 2026-09-02: 16 section references, 13 undefined terms in prose):

1. **No section numbers in runtime messages.** `§3.3` is a citation, not an
   explanation. The reader does not have the methodology skill open, and
   telling someone a rule exists somewhere is not telling them the rule.
2. **No undefined contract vocabulary in prose.** A field name is correct as a
   JSON key. The moment it appears inside a sentence a user is meant to
   understand, it needs an entry in `docs/GLOSSARY.md`. The glossary is read at
   run time — the check must not rot as the glossary grows. If the glossary is
   missing, that IS the failure: every term is treated as undefined rather
   than passing vacuously.
3. **The `excluded:` flag prefix.** Everywhere a literal shows an
   `inventory_gate` with `"verdict": "exclude"`, the first flag must start
   with `excluded:` — the spelling pair (verdict `exclude`, prefix `excluded:`)
   is load-bearing and was previously defended only by a parenthetical in a
   skill file. Runtime writes are normalized by pain_cards.py; this guards the
   documented examples that teach agents the shape.

Docstrings are exempt — they are for maintainers, who do have the repo open.

    python3 tests/validate_user_strings.py
"""

import ast
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

MODULES = [
    "pain_report.py",
    "pain_rubric.py",
    "pain_stages.py",
    "pain_intensity.py",
    "pain_capture.py",
    "pain_cards.py",
    "cluster.py",
]

# Contract vocabulary that reads as jargon inside a sentence. Whether each term
# is *defined* is decided by docs/GLOSSARY.md at run time, never hardcoded.
VOCAB = [
    "cut_basis", "distinct_communities", "distinct_authors",
    "complainer_is_buyer", "profanity_urgency", "money_loss",
    "time_quantified", "workaround_built", "engagement_weighted",
    "inventory_gate", "cell_id", "quadrant", "cluster_size", "retro_trend",
    "source_health", "monotone reading", "thin-capture",
]

SECTION = re.compile(r"§\s*\d")
GLOSSARY = REPO / "docs" / "GLOSSARY.md"

# JSON-ish object literal carrying an exclude verdict, in .py or .md files.
EXCLUDE_OBJ = re.compile(r"\{[^{}]*\"verdict\"\s*:\s*\"exclude\"[^{}]*\}")
FIRST_FLAG = re.compile(r"\"flags\"\s*:\s*\[\s*\"([^\"]*)")
MD_ROOTS = ["skills", "docs", "agents", "commands"]


def glossary_terms() -> set[str] | None:
    """The set of defined terms: every `###` heading, lowercased. None = no file."""
    if not GLOSSARY.exists():
        return None
    return {
        line[4:].strip().lower()
        for line in GLOSSARY.read_text().splitlines()
        if line.startswith("### ")
    }


def is_defined(term: str, defined: set[str]) -> bool:
    """A term is defined if a heading matches it, or matches it with the
    underscore/hyphen spelled as a space (`source_health` -> "source health")."""
    t = term.lower()
    return any(v in defined for v in (t, t.replace("_", " "), t.replace("-", " ")))


def runtime_strings(path: pathlib.Path):
    """Yield (lineno, text) for every non-docstring string literal.

    f-strings are reassembled once with `{…}` placeholders; their inner
    constant parts are skipped so nothing is counted twice.
    """
    tree = ast.parse(path.read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    in_fstring = {
        id(v) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)
        for v in ast.walk(node) if isinstance(v, ast.Constant)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings and id(node) not in in_fstring:
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr):
            parts = [
                str(v.value) if isinstance(v, ast.Constant) else "{…}"
                for v in node.values
            ]
            yield node.lineno, "".join(parts)


def main() -> int:
    defined = glossary_terms()
    findings: list[str] = []
    section_refs = 0
    undefined_terms = 0
    bad_prefixes = 0

    if defined is None:
        print(
            "docs/GLOSSARY.md is missing — every contract term is treated as "
            "undefined. The missing glossary is the failure, not an excuse to "
            "skip the check.",
            file=sys.stderr,
        )
        defined = set()

    for name in MODULES:
        path = REPO / "scripts" / name
        if not path.exists():
            continue
        rel = f"scripts/{name}"
        for lineno, text in runtime_strings(path):
            if SECTION.search(text):
                section_refs += 1
                findings.append(f"SECTION-REF {rel}:{lineno}  {text[:96]!r}")
            if len(text.split()) >= 6:
                for term in VOCAB:
                    if term in text and not is_defined(term, defined):
                        undefined_terms += 1
                        findings.append(
                            f"UNDEFINED {rel}:{lineno} [{term}]  {text[:96]!r}"
                        )

    # Check 3: every literal exclude-verdict object carries the excluded: prefix.
    md_files = [REPO / "README.md"] + [
        p for root in MD_ROOTS for p in (REPO / root).rglob("*.md")
    ]
    py_files = [REPO / "scripts" / name for name in MODULES]
    for path in md_files + py_files:
        if not path.exists():
            continue
        rel = path.relative_to(REPO)
        text = path.read_text()
        for match in EXCLUDE_OBJ.finditer(text):
            flag = FIRST_FLAG.search(match.group(0))
            if flag is None or not flag.group(1).startswith("excluded:"):
                bad_prefixes += 1
                lineno = text.count("\n", 0, match.start()) + 1
                findings.append(
                    f"BAD-PREFIX {rel}:{lineno}  exclude verdict whose first "
                    f"flag does not start with 'excluded:'"
                )

    for line in findings:
        print(line, file=sys.stderr)
    print(
        f"{section_refs} section references · {undefined_terms} undefined "
        f"terms in prose · {bad_prefixes} exclude literals missing the "
        f"'excluded:' prefix",
        file=sys.stderr,
    )
    if findings:
        print(
            "FAIL — a reader without this repo open cannot act on these messages.",
            file=sys.stderr,
        )
        return 1
    print("runtime messages are self-contained", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
