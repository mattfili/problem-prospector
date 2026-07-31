# Attribution

`problem-prospector` is assembled from several upstream projects. This file
records what came from where, how it was integrated, and under what license.

---

## Vendored (code ships in this repo)

### coreyhaines31/marketingskills — MIT

- **Upstream:** https://github.com/coreyhaines31/marketingskills
- **Copyright:** (c) 2025 Corey Haines
- **License:** MIT — permits redistribution and modification with attribution.
  A copy is preserved at `skills/marketing/LICENSE.upstream`.
- **Integration:** all 49 skills vendored verbatim into `skills/marketing/`,
  upstream directory structure preserved. Not modified.
- **Pinned commit:** recorded in `skills/marketing/.upstream-ref`.
- **Refresh:** `scripts/sync-marketingskills.sh` clones upstream, diffs against
  the vendored tree, and applies on confirmation. `--check` reports drift without
  applying.

Used by this plugin in three places: the `product-marketing` skill's context
document is generated per candidate so the whole tree activates (see
`skills/marketing-context/`); the distributor agent scores distribution
complexity against the channel skills; and `/diligence` structures its
competition and pricing sections with `competitors`, `competitor-profiling`, and
`pricing`.

---

## Adapted (method reimplemented, code not copied)

### mattfili/Armsreach-plugin — private, same owner

- **Integration:** the divergence/voltage engine from Armsreach's
  `divergence-engine` skill is adapted into `skills/wedge-voltage/`, and the
  adaptive percentile-cut clustering approach from its `measure_diversity.py` is
  reimplemented in `scripts/cluster.py`.
- **What carried over:** the three-stage Diverge → Map & Measure → Converge
  structure; voltage as an explicit distance-from-obvious generation setting
  (V1–V4); decomposition-before-divergence; the cluster-count-as-diversity-metric
  quality gate; and the finding that a fixed cosine-distance threshold does not
  transfer across embedding models, so the cut must be derived from the pool's own
  pairwise-distance distribution.
- **What changed:** the OpenAI and Composio embedding backends were deliberately
  removed — this plugin is key-free by construction, so only local `fastembed`
  and a dependency-free lexical fallback remain. Input/output shapes follow
  `docs/CONTRACTS.md` rather than Armsreach's schemas.
- **Documented divergence:** the spec that commissioned this plugin assumed
  "voltage" meant a differential between pain intensity and current-solution
  quality. In Armsreach it means distance from the obvious — a *generation*
  setting, not a measured differential. This implementation follows the source
  repo's method. The differential intuition survives as the
  `pain_distance` / `incumbent_distance` pair in `docs/CONTRACTS.md` §5. See the
  header of `skills/wedge-voltage/SKILL.md`.

### AdvancingTitans/pain-miner — reference only

- **Upstream:** https://github.com/AdvancingTitans/pain-miner
- **Integration:** read for method; no code copied and no runtime dependency.
- **What carried over:** the three-tier pain structure; the discipline that
  counter-evidence is mandatory rather than optional; cross-community
  consensus/divergence reporting; and — most concretely — its key-free source
  routing table, which supplied the verified Arctic Shift, HN Algolia, and
  pullpush endpoints plus their rate limits and failure modes. The rule that a
  fetch failure must never be presented as "no discussion found" comes from here.

---

## Referenced (external services, not vendored)

### king-of-the-grackles/reddit-research-mcp

- **Integration:** configured in `.mcp.json` as the `dialog` HTTP server
  (`https://reddit-research-mcp.fastmcp.app/mcp`). No code vendored.
- **Note:** the hosted endpoint requires OAuth (returns `401 invalid_token`
  unauthenticated), and self-hosting requires Reddit API credentials plus a
  ChromaDB proxy key. It is therefore an *opportunistic* primary only;
  `scripts/reddit_search.py` is the guaranteed key-free path. See the README.

### claude-world/trend-pulse

- **Integration:** `.mcp.json` stdio server via `uvx --from "trend-pulse[mcp]"
  trend-pulse-server`, plus `scripts/trends_cli.py` as the script fallback.
- **Constraint:** only its zero-auth built-in sources are used. Plugin sources
  requiring credentials are not enabled.

### mnemox-ai/idea-reality-mcp

- **Integration:** `.mcp.json` stdio server via `uvx idea-reality-mcp`, plus
  `scripts/reality_cli.py` as the script fallback.
- **Note:** where it emits a composite score, this plugin passes through the
  subscores and labels the composite as upstream-computed — presenting an opaque
  composite as our own analysis would violate the plugin's central design rule.

### unclecode/crawl4ai

- **Integration:** a Python dependency wrapped by `scripts/crawl.py`, declared
  via PEP 723 inline metadata. Docker is not required.
- **Constraint:** crawling honors `robots.txt`, rate-limits per host, and never
  attempts an auth wall or paywall. There is deliberately no `--ignore-robots`
  flag.

---

## This repo

MIT, © 2026 Matt Fili. See `LICENSE`.
