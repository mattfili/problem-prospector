#!/usr/bin/env python3
"""Guard the MCP tool-namespace rider: both spellings granted, everywhere.

WHY THIS EXISTS
---------------
Found 2026-08-19, the first time this bundle was installed as a plugin rather than
run from a checkout. Installed, its servers are namespaced
`mcp__plugin_problem-prospector_<server>__<tool>`; every agent's `tools:` frontmatter
granted only the bare `mcp__<server>__<tool>`. So **no agent could reach any of the
four MCP servers** — and nothing broke, nothing was logged, and every run completed,
because each capability degraded to its guaranteed script. The key-free guarantee is
precisely what hid the defect. The opportunistic primary had never once fired.

A defect that produces no error and no log entry can only be caught mechanically, so
it is caught here: any agent granting a bare `mcp__<server>` name for one of this
bundle's servers must also grant the plugin-namespaced variant, and vice versa.
Granting a name that does not exist is harmless, which is what makes "grant both" the
correct answer rather than a compromise.

    python3 tests/validate_mcp_grants.py
"""

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
PREFIX = "mcp__plugin_problem-prospector_"

#: Servers this bundle declares in .mcp.json. Read from the file so a new server
#: cannot be added without this check noticing it.
def declared_servers() -> set[str]:
    import json
    config = json.loads((REPO / ".mcp.json").read_text())
    return set(config.get("mcpServers", {}))


def grants(text: str) -> list[str]:
    match = re.search(r"^tools: (.+)$", text, re.M)
    return [t.strip() for t in match.group(1).split(",")] if match else []


def main() -> int:
    servers = declared_servers()
    failures: list[str] = []

    for path in sorted((REPO / "agents").glob("*.md")):
        granted = set(grants(path.read_text()))
        for token in sorted(granted):
            if not token.startswith("mcp__"):
                continue
            body = token[len("mcp__"):]
            if body.startswith("plugin_problem-prospector_"):
                bare = "mcp__" + body[len("plugin_problem-prospector_"):]
                if bare not in granted:
                    failures.append(
                        f"FAIL {path.name}: grants {token} but not the user/project-scope "
                        f"spelling {bare}"
                    )
                continue
            server = body.split("__")[0]
            if server not in servers:
                continue
            plugin = PREFIX + body
            if plugin not in granted:
                failures.append(
                    f"FAIL {path.name}: grants {token} but not the installed-plugin "
                    f"spelling {plugin} — as a plugin this agent cannot reach {server}"
                )

    if failures:
        print("\n".join(failures))
        print(f"\n{len(failures)} grant(s) missing a spelling. See the MCP tool-namespace "
              "rider in docs/CONTRACTS.md.")
        return 1
    print(f"mcp grants OK: both spellings present for {sorted(servers)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
