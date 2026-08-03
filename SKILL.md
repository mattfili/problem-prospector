---
name: problem-prospector
description: "Entry point and component map for the problem-prospector bundle — evidence-first discovery of business-shaped problems from real public complaints, wedged into MVP proposals with transparent, never-blended subscores. Activates on the slash commands /prospect, /diligence, /rescan, or on requests phrased as 'find me a problem worth building for', 'validate this idea', 'is there a market for X', 'what should I build', 'stress-test this business idea before I build it', or 'has anyone solved this yet'. Read this file first in any environment where the commands/, agents/, and skills/ directories below are not already auto-discovered as native slash-commands and subagents (e.g. a raw skill/zip upload rather than a marketplace plugin install) — it tells you which file to read and follow by hand for each piece. No API keys anywhere in the research path; every source is public and unauthenticated, with a key-free script fallback for every MCP."
---

# problem-prospector — component map

This bundle is normally installed as a Claude Code **plugin** (`.claude-plugin/plugin.json`
in this same directory), which auto-registers `commands/*.md` as native slash commands and
`agents/*.md` as delegatable subagents. **If that native registration is not active in your
current environment** — you were handed this bundle as a flat skill upload rather than a
plugin install — this file is your map: read the referenced `.md` file directly and follow
its instructions by hand, including delegating to an agent file as if it were a subagent
(read it, do what it says, return only what it says to return).

## The one-paragraph version

You give it a vague hunch — *"government intake is broken"*, *"something about how small
clinics handle referrals"* — and it runs a pipeline that captures real complaints from
public sources, collapses them into clusters, separates how **often** a pain shows up from
how **badly** it hurts, hunts for evidence anyone pays to fix it, then attacks each survivor
with a skeptic whose only job is to find reasons it is not a business. What lives through
that gets wedged into MVP shapes with separate technical and distribution grades. **No
opaque composite scores, anywhere** — every ranked output shows its subscores and cites raw
evidence (URLs, engagement counts, dates), because a single blended number launders
judgment into something nobody can audit. Full detail, design rationale, and data sources:
`README.md`.

## Commands — read on user intent, run as the orchestrator

| File | Invoke when | What it does |
|---|---|---|
| `commands/prospect.md` | `/prospect "<hunch>"`, or any "find/validate a problem" request | Full pipeline: frame → capture → cluster → intensity/WTP/skeptic/retro-trends per cluster → OpportunityCards → wedge → MVP shapes. This is the main entry point; read it in full before starting, it is the orchestrator's own script. |
| `commands/diligence.md` | `/diligence`, or "stress-test this idea before I build it" | Deep dive on one already-chosen wedge: crawls real competitor pages, writes the five-section `runs/<slug>/diligence.md` report (competition, novelty, wedge/gap, pricing, unit economics). |
| `commands/rescan.md` | `/rescan <slug>`, or "has this changed since we last looked" | Re-runs capture and trend reconstruction for a saved run, diffs it against stored state (cluster weight deltas, new/vanished clusters, slope changes). |

## Agents — subagents delegated to by the commands above

Each file's own frontmatter `description` is the authoritative trigger; this is the map,
not the source of truth. Read the target file before delegating to it.

| File | Role |
|---|---|
| `agents/scout.md` | Captures raw evidence for one matrix cell, zero interpretation — capture only, never scores or ranks. |
| `agents/distiller.md` | Clusters evidence (`scripts/cluster.py`), scores frequency + intensity, applies the no-inventory gate, writes the first draft of each OpportunityCard. |
| `agents/economist.md` | Willingness-to-pay panel: existing spend, workaround cost, buyer class, budget-line test — one card's `wtp` block. |
| `agents/skeptic.md` | Mandatory counter-evidence hunt against one cluster; silence is flagged `under_researched`, never treated as validation. |
| `agents/historian.md` | Backward-facing 3–5 year trend reconstruction (`retro_trend`): the two-curve pain-vs-solutions read, key-free. |
| `agents/wedgesmith.md` | Generates voltage-banded entry-strategy permutations off a finished OpportunityCard (`skills/wedge-voltage`). |
| `agents/distributor.md` | Grades distribution complexity 1–5 for each MVP shape from the vendored marketing skills. |

## Skills — auto-activate on context match, or read directly for the method

| Path | Covers |
|---|---|
| `skills/prospect-methodology/SKILL.md` | The pipeline spec — the constitution every stage defers to. |
| `skills/wedge-voltage/SKILL.md` | Divergence-engine method for generating and gating entry-strategy wedges. |
| `skills/mvp-shapes/SKILL.md` | Closed eight-shape MVP taxonomy plus independent technical/distribution complexity rubrics. |
| `skills/retro-trends/SKILL.md` | Backward-facing trend reconstruction method (pairs with `agents/historian.md`). |
| `skills/no-inventory-gate/SKILL.md` | The physical-goods exclusion rule, applied by every agent at every promotion point. |
| `skills/deep-diligence/SKILL.md` | Method behind `/diligence`'s five-section report. |
| `skills/marketing-context/SKILL.md` | Wires the vendored marketing tree onto a chosen candidate. |
| `skills/marketing/*/SKILL.md` | 49 vendored marketing skills (MIT, coreyhaines31) — ads, SEO, pricing, launch, etc. Consulted by `agents/distributor.md` and `skills/marketing-context`, never by the core research pipeline. |

## Scripts — standalone, key-free, run as `uv run scripts/<name>.py`

Every script is self-bootstrapping via PEP 723 inline metadata (`uv` is the only
prerequisite) and is the **guaranteed fallback** when the corresponding MCP is absent —
routine in environments like Cowork where stdio MCP servers frequently do not load. No
script reads an API key; see `docs/CONTRACTS.md` cross-cutting rule 4.

| Script | Guaranteed path for |
|---|---|
| `scripts/cluster.py` | Local embedding + clustering (`clusters.json`) — no MCP equivalent, this is the only path. |
| `scripts/reddit_search.py` | Reddit capture, fallback for the `dialog` MCP. |
| `scripts/trends_cli.py` | Multi-source trend/evidence capture (HN, Stack Overflow, Product Hunt, etc.), fallback for `trend-pulse`. |
| `scripts/reality_cli.py` | Saturation/competitor-count read, fallback for `idea-reality`. |
| `scripts/crawl.py` | Competitor page crawling for `/diligence` — no MCP equivalent. |
| `scripts/hn_history.py`, `scripts/reddit_history.py`, `scripts/gtrends_history.py` | Backward-facing history, pain side — no MCP equivalent, always run directly. |
| `scripts/gh_history.py`, `scripts/npm_history.py` | Backward-facing history, solution side (two independent sources — GitHub repo creation and npm package creation — so a GitHub 403 does not take down the two-curve read). No MCP equivalent, always run directly. |
| `scripts/sync-marketingskills.sh` | Maintenance only: refreshes the vendored `skills/marketing/` tree from upstream. Not part of any run. |

## The data contracts

`docs/CONTRACTS.md` is the integration spine — every script, agent, and command reads and
writes the JSON shapes defined there (`inputs.json`, `evidence/*.jsonl`, `clusters.json`,
`cards/<cluster_id>.json`, `wedges/<cluster_id>.json`, `shapes/<cluster_id>.json`). Read it
before writing to or parsing any `runs/<slug>/` file — a shape that drifts from CONTRACTS
breaks a downstream consumer silently.

## Environment notes

- **No API keys, anywhere in the research path.** `.mcp.json` declares three opportunistic
  MCP servers (`dialog`, `trend-pulse`, `idea-reality`); every one of them has a key-free
  script fallback above, and the fallback path is what the key-free guarantee actually
  rests on — see `README.md`'s "On `dialog` and the key-free promise."
- **Assume MCP servers may simply not load** — the standing condition in Cowork and other
  hosts that don't run stdio MCP. Every command and agent above already probes once and
  falls back silently to the matching script; do not treat an absent MCP as an error.
- **`runs/` is gitignored, per-run state.** If it is not present in this bundle, the first
  `/prospect` call creates it.
