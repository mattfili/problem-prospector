# problem-prospector

Evidence-first discovery of business-shaped problems.

You give it a vague hunch — *"government intake is broken"*, *"something about how
small clinics handle referrals"* — and it runs a pipeline that captures real
complaints from public sources, collapses them into clusters, separates how
**often** a pain shows up from how **badly** it hurts, hunts for evidence anyone
pays to fix it, then attacks each survivor with a skeptic whose only job is to
find reasons it is not a business. What lives through that gets wedged into MVP
shapes with separate technical and distribution grades.

Two constraints hold everywhere:

**No API keys.** Nothing in the research path reads a credential. Embeddings run
locally. Every data source is public and unauthenticated. You can run this on a
fresh machine with nothing configured.

**No inventory.** Anything requiring physical stock, warehousing, fulfillment, or
per-unit COGS on goods is excluded *at the gate* — not down-ranked into a top-5
list on the strength of a loud pain signal.

## The one design rule

**No opaque composite scores.** Every ranked output shows its subscores and cites
raw evidence — URLs, engagement counts, dates. There is no blended "opportunity
score" anywhere, because a single number launders judgment into something nobody
can audit or argue with. A 2/5 technical + 5/5 distribution business is nothing
like a 5/5 technical + 2/5 distribution business, and averaging them to 3.5
destroys exactly the information you needed.

The corollary is stranger and more useful: **if the skeptic can't find
counter-evidence, that's flagged as suspicious**, not as validation. A cluster
with no findable failures is marked `UNDER-RESEARCHED` and does not get promoted.

## Install

```
/plugin marketplace add mattfili/problem-prospector
/plugin install problem-prospector
```

Restart afterwards: newly installed MCP servers do not appear until the host
restarts, and `/reload-plugins` is not enough for them.

**Working on this repo?** Install the checkout itself — it carries its own
single-plugin marketplace manifest:

```
claude plugin marketplace add /path/to/problem-prospector
claude plugin install problem-prospector@problem-prospector
```

The install **copies** the tree into a version-pinned cache
(`~/.claude/plugins/cache/.../<version>/`), so editing the checkout changes
nothing on its own. To pick up an edit, bump `version` in
`.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`, then:

```
claude plugin marketplace update problem-prospector
claude plugin update problem-prospector@problem-prospector
```

Without the version bump `plugin update` reports "already at the latest version"
and silently keeps the stale copy. Run state is unaffected either way — `runs/`
resolves against your project, not the install directory (see Layout).

### First run

Two things download once, then cache:

- **`bge-small-en-v1.5`** (~130MB) — the local embedding model, on first
  `cluster.py` call.
- **A Chromium build** for `crawl.py`, if you use `/diligence`. If crawling
  fails with a browser error, run `uvx --from crawl4ai crawl4ai-setup`.

Scripts self-bootstrap their dependencies through
[PEP 723](https://peps.python.org/pep-0723/) inline metadata, so `uv` is the only
prerequisite. The system Python is not used directly — each script pins
`>=3.11,<3.13` because the scientific stack lags new releases.

## Commands

### `/prospect <inspiration> [flags]`

```
/prospect "back-office pain in small government agencies" --niche "311, permitting, records requests"
/prospect "something about how small clinics handle referrals"
/prospect "developer tooling for data teams" --pain high --wtp high --top 3
```

Runs the full pipeline, then wedges the top cards into MVP shapes.

| Flag | Effect |
|---|---|
| `--pain high` | show only intensity ≥4 cards |
| `--wtp high` | show only B2B-operator or documented-spend cards |
| `--niche "<text>"` | constrains **and extends** the vertical axis — it does not replace generation |
| `--cards-only` | stop after OpportunityCards; skip wedge + MVP shaping |
| `--top N` | how many cards get wedged (default 5) |

Flags are additive and never required. A bare `/prospect "<hunch>"` is meant to
freewheel.

`--pain` and `--wtp` are **display filters, not capture filters.** Every surviving
cluster still gets a card written to `runs/<slug>/cards/`; the flags only narrow
the rendered list. That is deliberate: filtering during capture would delete the
low-intensity baseline that makes "high intensity" mean anything.

### `/pain-search <inspiration> [flags]`

```
/pain-search "back-office pain in small government agencies"
/pain-search "something about how small clinics handle referrals" --niche "referrals, prior auth"
```

The front half of `/prospect`, run on its own: frame → capture → cluster →
frequency and intensity → `runs/<slug>/pain-clusters.md`, then stop. It ends where
the expensive half begins, so you can read the two axes before deciding whether to
spend the rest.

Nothing here is a business yet, and the report says so: willingness-to-pay,
counter-evidence, trend reconstruction and saturation are `null` on every card
rather than omitted. High frequency with low intensity is a content play; high
intensity with no proven buyer is a sad hobby. Both reads are settled by the stages
this stops before.

**It leaves a legal Stage-3-complete run.** `/prospect "<same inspiration>"` resumes
it at Stage 3.5 and does not re-capture — evidence is append-only and every gate
below that line already holds.

Unlike the other three commands, this one drives **MCP tools rather than prose**
(the `pain-search` server in `.mcp.json`, 13 tools). The reason is narrow: the rules
that matter most in capture and scoring are the ones a model can skip with no error
appearing — a score floor on a Reddit pull, an out-of-enum source, a marker set
`true` on a paraphrase, a level 4 claimed off one author. Prose asks a model to
remember them; a schema makes them unrepresentable. There is no `min_score`
parameter, no source value outside the queryable enum, and no `score` field on the
intensity tool — you pass quotes and the score is *derived*, with every quote
checked at ≤15 words, owned by that cluster, and appearing verbatim in the captured
text. Authors are resolved off disk, so the distinct-author counts that carry the
upper levels cannot be asserted.

Two places where the methodology contradicts itself are resolved in
`scripts/pain_rubric.py` and **disclosed in the report header** whenever the
resolution changed a number. See that file for both.

### `/diligence [<spec path | pasted spec | "the thing we just discussed">] [flags]`

Deep dive on a spec you've landed on. Crawls real competitor pricing pages and
produces a five-section report: competition, novelty, proposed wedge/gap, pricing
potential, unit economics. Every number either traces to a crawled page or is
labeled `[assumption]` and is yours to override.

| Flag | Effect |
|---|---|
| `--slug <run-slug>` | target this existing run instead of inferring one (default: reuse the run the spec came from) |
| `--cluster <cluster_id>` | which card/wedge the spec refers to (default: top card under the printed sort, named out loud) |
| `--competitors N` | crawl budget, clamped to 5–10 (default 8) |
| `--skeptic` | force the skeptic even when the card's panel is already populated and cited |

With no argument it offers the most recent run that has wedges and waits for an
explicit yes before spending a crawl budget.

### `/rescan <run-slug> [flags]`

Re-runs capture and trend reconstruction for a saved run and diffs it against the
stored state — cluster weight deltas, new and vanished clusters, slope changes.
This is what turns the backward-looking trend analysis forward-looking over time.
The original run is read-only; everything lands in `runs/<slug>/rescan-<date>/`
plus one report at `runs/<slug>/rescan-<date>.md`.

| Flag | Effect |
|---|---|
| `--top N` | cap how many matched clusters get a retro-trend re-run (default: the run's own `flags.top`, else 5) |
| `--cells m01,m04` | recapture only these matrix cells; clusters fed by un-recaptured cells go `unresolved` |
| `--no-trends` | weights-only diff — capture plus about a minute |
| `--force-trends` | run the trend re-check even when too little time has passed for it to mean anything |
| `--card-new N` | build full cards for the N largest *new* clusters (default off; 3 when passed bare) |

A bare `/rescan <slug>` is the intended invocation. Under 30 elapsed days the
trend re-run is skipped automatically — retro-trend buckets are half-years and
years, so inside one bucket any movement you could report is arithmetic on a
partial bucket. `/rescan` with no slug lists the saved runs and stops.

## How it works

```
inspiration
   ↓  frame           permutation matrix: personas × verticals × framings
   ↓  capture         scouts, parallel, one per matrix cell — capture only, no interpretation
   ↓  cluster         local embeddings; from here the CLUSTER is the unit of analysis
   ↓  intensity       scored separately from frequency, never merged → 2×2 read
   ↓  WTP             existing spend · workaround cost · buyer class · budget-line test
   ↓  skeptic         mandatory counter-evidence; silence ⇒ UNDER-RESEARCHED
   ↓  retro-trends    3–5y history → emerging / accelerating / persistent-flat / declining / spiky
   ↓  gate            no-inventory exclusion
   ↓  OpportunityCards
   ↓  wedge           Armsreach divergence: 40–60 candidates → cluster gate → 3–7 wedges
   ↓  MVP shapes      fixed taxonomy + independent technical & distribution grades
```

**400 phrasings of one pain is one cluster of weight 400, not 400 signals.** That
single rule is most of what separates this from research theater.

The read you're hunting for is **persistent-flat pain with no accumulating
solutions** — a problem that has hurt for five years while nobody shipped
anything. Rising pain with flat builder activity is the early-mover case. Spiky
pain is usually just news.

## What runs where

Everything degrades to a bash script, because MCP servers don't load everywhere
(notably in Cowork). Each command probes for the MCP and falls back silently —
silently to *you*, but recorded in `runs/<slug>/source_health.json`.

| Capability | Primary | Guaranteed fallback |
|---|---|---|
| Reddit | `dialog` MCP | `scripts/reddit_search.py` (Arctic Shift) |
| Trends / multi-source | `trend-pulse` MCP | `scripts/trends_cli.py` |
| Saturation & novelty | `idea-reality` MCP | `scripts/reality_cli.py` |
| History | — | `hn_history.py`, `reddit_history.py`, `gtrends_history.py`, `gh_history.py` |
| Crawling | — | `scripts/crawl.py` |
| Clustering | — | `scripts/cluster.py` |
| Pain search, stages 0b-3 | `pain-search` MCP (this repo's own; 12 tools) | `/prospect`'s prose stages |

**A source that failed is never reported as "no discussion found."** That
distinction matters more than it sounds: a rate-limited API silently becoming
"nobody is complaining about this" would invert the tool's conclusion.

### On `dialog` and the key-free promise

The commissioning spec assumed the hosted `reddit-research-mcp` endpoint was
zero-credential. It isn't — it returns `401 invalid_token` and requires OAuth,
and self-hosting it needs Reddit API keys plus a ChromaDB proxy key.

So `dialog` is configured as an *opportunistic* primary: Claude Code can complete
its OAuth registration without you pasting a key, and when it works you get
semantic search across 20k+ subreddits with citations. When it doesn't, the
Arctic Shift path carries the run with no credentials at all. The key-free
guarantee rests on the fallback, not on `dialog`.

Verified 2026-08-18: the endpoint advertises RFC 7591 dynamic client registration
through Descope, so the flow completes in-client with nothing pasted — run `/mcp`
and authenticate once. `mcp.dialog.tools/mcp` and `reddit-research-mcp.fastmcp.app/mcp`
are aliases of the same deployment (identical Descope app id), so the configured URL
is not the reason it 401s; it 401s because nobody has authenticated it.

**Authenticating it is worth doing when Arctic Shift throttles.** The archive answers
a heavy full-text query with `422 Timeout. Maybe slow down a bit` regardless of
pacing; `reddit_search.py` now walks a limit ladder (100 → 50 → 25 → 10) with
doubling backoff before giving up, and `403`/`429` are still circuit-broken on sight
and never retried. dialog sidesteps that path entirely.

Because an MCP server cannot call another MCP server's tools, the dialog path runs
client-side and hands its results to `pain_ingest_records`, which enforces the
evidence contract on them. `pain_merge_staging` then collapses any post both dialog
and Arctic Shift captured — necessary because the `id` recipe hashes the source name
alongside the URL, so the same post under two sources yields two ids and would
otherwise double that pain's cluster weight.

### What about `reddit-mcp-buddy`?

Evaluated and rejected as a key-free path. Tested with all Reddit credentials
scrubbed: Reddit's `.json` API answers `403` for every subreddit, so anonymous mode
always falls back to RSS — `score`, `num_comments` and `upvote_ratio` all `null`, the
post body hard-truncated at 500 characters, `search_reddit` and `get_post_details`
both `403`, at 10 req/min. No search means no query-driven capture; no comments means
the quantified-cost and workaround markers are unreachable and every cluster reads
intensity 1–2. With Reddit app credentials it is genuinely capable (60–100 req/min,
real search, comment trees), but that is a credentialed tier, not the key-free
guarantee.

## Data sources

All public, all unauthenticated, all verified live:

| Source | Endpoint | Notes |
|---|---|---|
| Reddit archive | `arctic-shift.photon-reddit.com` | ≥1.2s/req; no global sub search |
| Reddit (last resort) | `api.pullpush.io` | ≥4s/req; stops on first 429 |
| Hacker News | `hn.algolia.com/api/v1` | `nbHits` gives counts without paginating |
| GitHub | `api.github.com/search` | 10 req/min unauthenticated — paced |
| Google Trends | via `trendspyg` | **relative** 0–100 values, not volume |
| PyPI / npm / Product Hunt / Stack Overflow / Wikipedia | via `trend-pulse` | zero-auth built-ins only |

**Which of those actually carry pain language.** Only three sources are both in the
evidence contract's closed enum *and* keyword-searchable, so only three can be aimed
at a cell's framing: **Reddit** (Arctic Shift, posts and comments — the deepest by
far and the only one queried unconditionally), **Hacker News** (Algolia), and
**Stack Overflow**. Product Hunt is searchable but was 403 at the origin when last
probed, and it is vendor copy rather than complaint text. `github`, `pypi`, `npm`,
`wikipedia` and `google-trends` are in the enum but trending-only: they return a
global feed unrelated to your framing, which would still cluster and inflate a
pain's weight, so they are recorded `degraded` and never captured per-cell. Their
real use is the backward-facing trend stage, where `gh_history.py` and
`npm_history.py` read the *solution* side and `hn_history.py`,
`reddit_history.py` and `gtrends_history.py` read the *pain* side.

Rate limits are respected structurally, not advisorily. On `403`/`429` the host is
circuit-broken immediately — no retry-probing, no rotation, no evasion. `crawl.py`
honors `robots.txt` and there is deliberately no `--ignore-robots` flag.

## Layout

```
commands/         /prospect, /pain-search, /diligence, /rescan
agents/           scout, distiller, skeptic, economist, historian, wedgesmith, distributor
skills/
  prospect-methodology/   the pipeline spec — the constitution
  wedge-voltage/          Armsreach divergence method, adapted
  mvp-shapes/             MVP taxonomy + complexity rubrics
  deep-diligence/         /diligence method
  retro-trends/           backward-facing trend reconstruction
  no-inventory-gate/      exclusion rules, applied by every agent
  marketing-context/      wires the marketing tree onto the candidate
  marketing/              49 vendored skills (MIT, coreyhaines31)
scripts/          ten standalone key-free CLI scripts, plus the pain-search
                  stage modules behind pain_mcp.py (rubric, stages, capture,
                  cards, intensity, report)
docs/CONTRACTS.md the data contracts — the integration spine
runs/             per-run state (gitignored): evidence, clusters, cards, reports.
                  Resolved against your project — PROSPECTOR_RUNS_ROOT, else
                  CLAUDE_PROJECT_DIR, else the working directory — never the
                  plugin's install directory, which a plugin update replaces
tests/            smoke.sh plus the regression and contract-guard suites
```

`docs/CONTRACTS.md` is the spine. Every script, agent, and command reads and
writes the shapes defined there; change one and you change every producer and
consumer it names.

## Attribution

See [ATTRIBUTION.md](ATTRIBUTION.md).

- **[coreyhaines31/marketingskills](https://github.com/coreyhaines31/marketingskills)**
  (MIT) — 49 skills vendored into `skills/marketing/`. Refresh with
  `scripts/sync-marketingskills.sh`.
- **[AdvancingTitans/pain-miner](https://github.com/AdvancingTitans/pain-miner)** —
  method reference for the three-tier pain structure, counter-evidence
  discipline, and the key-free source routing.
- **mattfili/Armsreach-plugin** — the divergence/voltage engine adapted in
  `skills/wedge-voltage/`.
- **[king-of-the-grackles/reddit-research-mcp](https://github.com/king-of-the-grackles/reddit-research-mcp)**,
  **[claude-world/trend-pulse](https://github.com/claude-world/trend-pulse)**,
  **[mnemox-ai/idea-reality-mcp](https://github.com/mnemox-ai/idea-reality-mcp)** —
  MCP servers, referenced not vendored.
- **[unclecode/crawl4ai](https://github.com/unclecode/crawl4ai)** — wrapped by
  `scripts/crawl.py`.

## License

MIT. Vendored marketing skills retain their upstream MIT license
(`skills/marketing/LICENSE.upstream`).
