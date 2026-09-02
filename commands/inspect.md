---
description: Publish a run as a live inspection artifact — plain-language readings over the mechanical digest, republished to the same URL as the run advances.
argument-hint: "[run slug — omit for the most recent run]"
allowed-tools: Read, Write, Bash, Grep, Glob, Task, ToolSearch, Skill, Artifact, mcp__pain-search
---

# /inspect — the run's living window

Load `skills/run-inspector` and follow it end to end. Everything below is the
command-level contract; the skill owns the procedure.

**Resolve the target.** `$ARGUMENTS` names a run slug under `runs/`. With no
argument, take the most recently modified run directory and say which one you
picked. No runs at all → say so and stop; do not invent one.

**Produce the surface.** Digest via `scripts/inspect_run.py` (or the
`pain_run_digest` tool), interpretation per `skills/plain-reading`, page from
`templates/inspect-artifact.html`, published with the Artifact tool. The
deliverable is the URL plus a three-or-four-sentence plain-language summary of
what the page shows — not a re-narration of the whole run.

**Same run, same URL.** If this session already published an inspection
artifact for this slug, republish the same file path. If the user asks to
inspect a run whose artifact came from an earlier conversation, find its URL
(`Artifact` action `list`, or ask for the link) and publish with `url` so the
existing page updates instead of forking.

**Never** edit run files from here (the digest is the only write), never
narrate MCP degradations to the user (they are in the health ledger on the
page), and never blend the two axes or invent a composite score on the surface
— the page shows subscores and evidence, same as every other render.
