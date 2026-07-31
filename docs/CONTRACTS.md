# Data contracts

Every script, agent, and command in this plugin reads and writes these shapes.
They are the integration spine: if you change one, change every producer and
consumer named in its **Produced by** / **Consumed by** lines.

All files live under `runs/<slug>/`, where `<slug>` is
`<kebab-inspiration-truncated-40>-<YYYY-MM-DD>`.

---

## 1. `inputs.json` — the frame

Written **before any capture** so a run is auditable and re-runnable (§3.0).

**Produced by** `/prospect` (frame stage) · **Consumed by** every scout, `/rescan`

```json
{
  "slug": "back-office-pain-small-gov-2026-07-31",
  "inspiration": "back-office pain in small government agencies",
  "created_utc": 1753920000,
  "flags": {
    "wtp": "high",
    "pain": "high",
    "niche": "311, permitting, records requests",
    "cards_only": false,
    "top": 5
  },
  "matrix": [
    {
      "cell_id": "m01",
      "persona": "311 dispatcher",
      "vertical": "municipal call center",
      "framing": "call volume triage without a CRM",
      "queries": ["311 dispatch software complaints", "..."],
      "subreddits": ["sysadmin", "publicwork"]
    }
  ]
}
```

`matrix` holds 6–12 cells: {personas} × {verticals} × {problem framings}.
`--niche` free text **constrains or extends** the vertical axis; it never
replaces generation.

---

## 2. `evidence/<source>.jsonl` — raw capture

One JSON object per line. Scouts **capture only, never interpret** (§3.1).
Append-only. Never edited by later stages.

**Produced by** scout agents, `reddit_search.py`, `trends_cli.py`,
`reality_cli.py` · **Consumed by** `cluster.py`

Note: the `*_history.py` scripts do **not** produce evidence — they return bucket
counts, not items, and feed §4 `retro_trend` instead. HN/Stack Overflow/Product
Hunt evidence arrives via `trends_cli.py` (or the `trend-pulse` MCP).

```json
{
  "id": "sha1-of-source-plus-url",
  "cell_id": "m01",
  "source": "reddit|hackernews|stackoverflow|producthunt|github|pypi|npm|wikipedia|google-trends|dialog",
  "url": "https://www.reddit.com/r/sysadmin/comments/abc123/...",
  "title": "Our permit system is held together with Access and prayer",
  "text": "verbatim body or comment text, never paraphrased",
  "author": "u/someone",
  "community": "r/sysadmin",
  "engagement": {"score": 412, "comments": 88},
  "created_utc": 1731000000,
  "captured_utc": 1753920000,
  "query": "the exact query string that surfaced this item"
}
```

Rules:
- `text` is **verbatim**. Truncation is allowed; rewording is not.
- `url` must be a real, resolvable permalink. No constructed or guessed URLs.
- `id` is stable across runs so `/rescan` can diff.
- Missing fields are `null`, never invented. A `null` engagement means
  "the source did not report it", not zero.

---

## 3. `clusters.json` — the unit of analysis

After this file exists, **the cluster is the unit of analysis, never the raw
post** (§3.2). 400 phrasings of one pain = one cluster of weight 400.

**Produced by** `cluster.py` · **Consumed by** distiller, economist, skeptic,
historian, wedgesmith

```json
{
  "run_slug": "back-office-pain-small-gov-2026-07-31",
  "embedding_model": "BAAI/bge-small-en-v1.5",
  "backend": "fastembed",
  "cut_basis": "adaptive:p35",
  "clustered_utc": 1753920600,
  "clusters": [
    {
      "cluster_id": "c01",
      "canonical": "permit status is invisible to staff and applicants alike",
      "member_count": 47,
      "distinct_authors": 39,
      "distinct_communities": 6,
      "engagement_sum": 3021,
      "cell_ids": ["m01", "m04"],
      "exemplar_urls": ["https://...", "https://..."],
      "member_ids": ["sha1...", "sha1..."]
    }
  ],
  "unclustered_ids": ["sha1..."]
}
```

`distinct_authors` guards against one person ranting 40 times.
`distinct_communities` guards against a single-subreddit echo.

---

## 4. `cards/<cluster_id>.json` — the OpportunityCard

The central object. Every panel is independently sourced; **no panel may be
blended into a composite score** (§3.8).

**Produced by** distiller (+ economist, skeptic, historian filling panels) ·
**Consumed by** wedgesmith, distributor, `/diligence`, `opportunity-cards.md`

```json
{
  "cluster_id": "c01",
  "canonical_pain": "Permit status is invisible to staff and applicants alike",
  "provenance": {"cell_ids": ["m01"], "personas": ["311 dispatcher"]},

  "frequency": {
    "cluster_size": 47,
    "distinct_authors": 39,
    "distinct_communities": 6,
    "engagement_weighted": 3021,
    "read": "high"
  },

  "intensity": {
    "score": 4,
    "markers": {
      "money_loss": true, "time_quantified": true, "workaround_built": true,
      "abandonment": false, "profanity_urgency": true, "complainer_is_buyer": true
    },
    "exemplars": [
      {"quote": "I rebuilt the whole queue in Excel", "url": "https://...", "words": 7}
    ],
    "read": "high"
  },

  "quadrant": "high-freq/high-intensity",

  "wtp": {
    "existing_spend": [{"tool": "Accela", "evidence_url": "https://...", "note": "named as current paid vendor"}],
    "workaround_cost": [{"claim": "two staff, ~10 hrs/week each", "url": "https://..."}],
    "buyer_class": "b2b-operator",
    "budget_line": {"attaches_to": "existing permitting software line", "new_category": false},
    "read": "high"
  },

  "skeptic": {
    "failed_attempts": [{"what": "...", "why_failed": "...", "url": "https://..."}],
    "churn_testimony": [{"quote": "...", "url": "https://..."}],
    "structural_blockers": [{"blocker": "18-month procurement cycle", "url": "https://..."}],
    "steelman": "This persists because each city's workflow is bespoke, so...",
    "under_researched": false
  },

  "retro_trend": {
    "shape": "persistent-flat",
    "slope_pct_per_year": 2.1,
    "series": [
      {"source": "hackernews", "buckets": [{"period": "2022H1", "count": 12}], "coverage": "good"}
    ],
    "note": "GitHub history thin; treat slope as HN/Reddit-driven"
  },

  "saturation": {
    "source": "idea-reality",
    "competitor_count": 14,
    "trend_direction": "flat",
    "read": "moderately saturated"
  },

  "inventory_gate": {"verdict": "pass", "flags": ["long procurement cycle", "licensure-adjacent"]}
}
```

### Sort contract

Cards are ranked by a **transparent, re-sortable sort** — never a blended
number. Default: `intensity.score` desc → `wtp.read` desc
(high > medium > low) → `saturation.competitor_count` asc. The active sort key
is always printed above the card list so the reader can ask for another.

### Enums

- `frequency.read`, `intensity.read`, `wtp.read` — `"high" | "medium" | "low"`.
- `saturation.read` — **not** that enum. It is free text in whatever vocabulary
  the upstream tool returned (e.g. `"moderately saturated"`), or `null` if the
  tool returned none. Never coin a saturation adjective, and never fold it into
  another panel's `read`. It is passed through, not judged here.
- `saturation.source` — `"idea-reality"` when the MCP answered,
  `"reality_cli.py"` when the script fallback did. Recording which path ran is
  what keeps a degraded source from being silently misattributed.
- `quadrant` — `high-freq/high-intensity` | `low-freq/high-intensity` |
  `high-freq/low-intensity` | `low-freq/low-intensity`.
- `wtp.buyer_class` — `"b2b-operator" | "prosumer" | "hobbyist"`. Observable
  criteria for each are in `skills/prospect-methodology/SKILL.md` §3.4.
- `intensity.score` — integer 1–5. `retro_trend.shape` — `emerging` |
  `accelerating` | `persistent-flat` | `declining` | `spiky-episodic`.
- `inventory_gate.verdict` — `"pass" | "exclude"`. An `exclude` verdict is
  recorded with its reason in `flags`, never silently dropped. The verdict is
  `exclude`; the first `flags` element on an exclusion begins with the prefix
  `excluded:`. Both spellings are load-bearing — the economist and the skeptic
  preflight on `verdict == "exclude"` and skip the card, so `"excluded"` in the
  verdict field silently sends both agents to work on a card the gate killed.

Note that `frequency.cluster_size` carries clusters.json's `member_count` — the
field is renamed as it moves from §3 to §4, so do not mix the two names within
one panel.

### Additive panel keys

A panel may carry these **optional** keys beyond the shape above. They are
named here so the policy is uniform — previously some agents added notes while
others were told not to, on the grounds that §4 did not define them.

| Key | Owner | Purpose |
|---|---|---|
| `frequency.note` | distiller | caveats on the counts (e.g. one community dominates the spread) |
| `intensity.note` | distiller | why the score landed where it did when the markers alone do not explain it |
| `wtp.note` | economist | caveats on the spend evidence |
| `retro_trend.render_block` | historian | the prepared ASCII sparkline/coverage table, so the renderer pastes it verbatim rather than redrawing and losing per-source scaling, raw counts, and the Trends relativity caveat |
| `skeptic.note` | skeptic | scope of the search performed, especially before setting `under_researched` |

Rules: these are **additive only** — a consumer must work when they are absent,
and no consumer may depend on one. They carry explanation, never data another
field already holds, and never a score. Any other key not in this table or the
shape above does not belong on a card.

### Additive keys (optional, declared here so they are not drift)

Three keys beyond the shape above are legal on a card. Producers may omit them;
consumers must tolerate their absence. Nothing else may be added — an undeclared
key is contract drift with no error message.

| Key | Written by | Read by | Why it exists |
|---|---|---|---|
| `frequency.note` | distiller | render header, `opportunity-cards.md` | Which §3.3 corrections fired (repetition demotion, echo-chamber cap, engagement-driven promotion, 2×2 boundary position). Without it a `read` is not reproducible. `null` when nothing fired. |
| `intensity.note` | distiller | `opportunity-cards.md` | Which cap was applied and which markers were left `false` for want of a quote. A score of 2 beside four `true` markers is unreadable otherwise. |
| `retro_trend.render_block` | historian | `/prospect` stage 6 | The finished ASCII trend block, newline-joined, already scaled per source with counts, coverage and the Trends relativity caveat. The renderer pastes it verbatim rather than re-deriving a sparkline that could disagree with the series it came from. |

There is deliberately **no** `wtp.note`, no `skeptic.confidence`, no
`saturation.note`. A panel with something to say says it in a declared field or
in the agent's return summary, not in a key invented at write time.

---

## 5. `wedges/<cluster_id>.json` — voltage permutations

**Produced by** wedgesmith (`skills/wedge-voltage`) · **Consumed by**
distributor, `mvp-shapes`, `/diligence`

Voltage is **distance from the obvious** (V1–V4), per the Armsreach method —
see `skills/wedge-voltage/SKILL.md` for why this differs from a
pain-versus-solution differential.

```json
{
  "cluster_id": "c01",
  "divergence_gate": {
    "candidate_count": 48, "cluster_count": 9, "min_clusters_required": 6,
    "passed": true, "largest_cluster_share": 0.19, "cut_basis": "adaptive:p35"
  },
  "wedges": [
    {
      "wedge_id": "c01-w1",
      "voltage": 3,
      "thesis": "Sell the status page to the applicant, not the permit office.",
      "axes": {
        "who_first": "the contractor waiting on the permit",
        "slice": "status opacity, not workflow automation",
        "substrate": "attaches to the existing portal via scrape+notify; replaces nothing"
      },
      "grounding": {
        "evidence_ids": ["sha1..."],
        "evidence_urls": ["https://..."],
        "pain_distance": 0.21,
        "incumbent_distance": 0.68
      },
      "rationale": "Highest incumbent distance while staying closest to cited pain."
    }
  ]
}
```

`pain_distance` = cosine distance from the wedge text to the cluster's pain
evidence centroid (**lower = better grounded**). `incumbent_distance` = cosine
distance to the incumbent-positioning centroid (**higher = more novel**). A
wedge with high pain_distance is ungrounded invention and must be dropped.

---

## 6. `shapes/<cluster_id>.json` — MVP shapes

**Produced by** `mvp-shapes` skill + distributor agent · **Consumed by**
`opportunity-cards.md`, `/diligence`

```json
{
  "wedge_id": "c01-w1",
  "shapes": [
    {
      "shape": "free-tool-wedge",
      "sketch": "Public permit-status lookup, one city, scraped nightly.",
      "technical_complexity": {
        "grade": 2,
        "reasoning": "One scraper, static host. No compliance surface.",
        "dimensions": {"data_acquisition": 3, "integration_surface": 1, "model_needs": 1, "infra": 1, "compliance": 1}
      },
      "distribution_complexity": {
        "grade": 2,
        "reasoning": "Ranks for '<city> permit status' with near-zero competition.",
        "primary_channel": "programmatic-seo",
        "secondary_channel": "community-marketing",
        "time_to_first_25_users": "2-3 weeks",
        "skills_consulted": ["programmatic-seo", "free-tools", "seo-audit"]
      },
      "founder_fit": {
        "note": "Spark/Databricks experience is irrelevant here; this is a small scraper. No fit discount applied.",
        "effective_complexity_delta": 0
      }
    }
  ]
}
```

**Complexity grades are never silently adjusted for founder fit.** If fit
lowers effective complexity, `founder_fit.note` must say so explicitly and
`effective_complexity_delta` records the amount.

---

## 7. `product-marketing.md` — marketing-tree activation

**Produced by** `/prospect` (post-wedge) and `/diligence` (on ingest) ·
**Consumed by** every skill in `skills/marketing/`

Written to **`runs/<slug>/product-marketing.md`** for the audit trail **and
copied to `.agents/product-marketing.md`**, which is the canonical path the
vendored tree actually reads. Both writes are required — the audit copy alone
does not activate the tree.

Format must match `skills/marketing/product-marketing/SKILL.md` Step 3
(12 sections + `Document version` + `Changelog`). Fields with no evidence are
marked `[unknown — no evidence in run]`, never fabricated. Any claim carried
from a card cites its evidence URL.

---

## 8. `diligence.md` — the five-section report

**Produced by** `/diligence` · sections fixed: Competition, Novelty, Proposed
wedge/gap, Pricing potential, Unit economics. Every table cell that is an
assumption rather than crawled evidence is labeled `[assumption]` and is
overridable by the reader.

---

## 9. `rescan.md` — drift diff

**Produced by** `/rescan`. Diffs the stored `clusters.json` and
`retro_trend` against a fresh capture: cluster weight deltas, new/vanished
clusters, and slope changes. Requires §1 and §3 from the prior run.

---

## Cross-cutting rules

1. **No invented URLs, quotes, counts, or prices.** If a source did not
   return it, the field is `null` or `[unknown]`.
2. **Verbatim quotes ≤15 words**, each with a resolvable link.
3. **Every script emits JSON to stdout** and diagnostics to stderr, so it can
   be piped and so an agent can parse it without a wrapper.
4. **Every script is standalone and key-free** — PEP 723 inline metadata, run
   via `uv run scripts/<name>.py`. No script reads an API key.
5. **Graceful degradation is silent to the user but recorded in the run.**
   Each stage appends to `runs/<slug>/source_health.json`:
   `{"source": "dialog", "status": "unavailable", "fallback": "reddit_search.py", "detail": "401"}`.
   A source that failed is **never** reported as "no discussion found".

   `source_health.json` is **JSONL at the run root** (one object per line,
   appended with `printf '%s\n' … >>`) and a **JSON array inside
   `rescan-<DATE>/`**. Readers should tolerate both; writers must match the
   file they are appending to. This is the likeliest place for a silent entry
   loss, so check the shape before appending.

   ### `status` values

   The point of this vocabulary is to keep three genuinely different things
   apart: *we could not look*, *we looked and found nothing*, and *we chose not
   to look*. Collapsing any two of them is the failure-as-absence bug.

   | Status | Meaning |
   |---|---|
   | `ok` | the source answered and returned results |
   | `degraded` | the source answered partially — truncated, censored buckets, a near-empty page from a 200 |
   | `unavailable` | **we could not look.** Auth failure, throttle, circuit-break, MCP absent. Never render as zero |
   | `skipped` | **we chose not to look.** The source was not relevant to this cell. Not a failure |
   | `searched-no-results` | **we looked and genuinely found nothing.** The one case where an empty result is a finding |
   | `stopped` | a gate halted this stage deliberately (thin capture, budget cap) — `detail` says which gate |

   Agents may suffix `searched-no-results` for specificity where the distinction
   carries analytic weight — `searched-no-counterevidence` (which drives the
   skeptic's `under_researched` flag) and `searched-no-spend-evidence` are both
   sanctioned. Anything else must be one of the six above.

   ### `crawl.py` manifest statuses (a separate enum)

   Per-URL, in `crawl.py`'s manifest — not `source_health`:
   `ok` | `blocked` (auth wall) | `robots-denied` | `failed` | `degraded`
   (near-empty markdown from a 200, usually a JS-rendered page). **A `degraded`
   pricing page must never be read as "free product."**

---

## Appendix: verified MCP / source status

Probed live on 2026-07-31. Re-probe with `tests/smoke.sh` and the notes below
before assuming a source works.

| Server / source | Status | Detail |
|---|---|---|
| `idea-reality` (uvx) | **works, key-free** | server `idea-reality-mcp 3.4.5`, 1 tool: `idea_check` |
| `trend-pulse` (uvx) | **works, key-free — requires a pin** | its `[mcp]` extra does not constrain `mcp`, and `mcp 2.0.0` removed `mcp.server.fastmcp`, so the server crashes on import. `.mcp.json` pins `--with "mcp<2"`, which yields `trend-pulse 1.29.0` and 29 tools. If it ever breaks again, this is the first thing to check. |
| `dialog` (hosted HTTP) | **requires OAuth** | returns `401 invalid_token` unauthenticated. Opportunistic primary only; `scripts/reddit_search.py` is the guaranteed path. Its tool names were never observed (the 401 precedes the tool list), so **no agent's `tools:` frontmatter grants a `mcp__dialog__*` tool** — granting a guessed name is worse than not granting one. Consequence to expect, and to not mistake for a bug: the `dialog` probe resolves `unavailable` in every agent, every run, and the run proceeds on Arctic Shift. If the tool names are ever confirmed, add them to `scout` / `skeptic` / `economist` frontmatter; until then the guaranteed path is the only path. |
| Arctic Shift | works, key-free | `>=1.2s`/req; 422 ⇒ retry at `limit=50`; no global subreddit search. See the two query caveats below. |
| pullpush (last resort) | works, key-free | `>=4s`/req, stop on first 429. **Has no equivalent of Arctic Shift's `query`** — see below. |
| HN Algolia | works, key-free | `nbHits` gives bucket counts without paginating. `numericFilters` MUST be URL-encoded. `tags` is AND-combined — the OR form is the parenthesised `tags=(story,comment)`. |
| GitHub search | works, key-free | 10 req/min unauthenticated; pace ~6.5s/req |
| Google Trends (`trendspyg`) | works, key-free | values are **relative 0–100**, not absolute volume |
| crawl4ai | works, key-free | needs a Chromium build on first use (`crawl4ai-setup`) |

**macOS note for anyone writing tests here:** `timeout(1)` does not exist on
stock macOS. Use a language-level timeout instead — a shell probe wrapped in
`timeout` silently becomes a no-op and looks like a dead server.

### Arctic Shift query semantics — two caveats that bite

Both found by live testing during the build, both affect keyphrase choice.

**1. `query` requires a scope.** An unscoped full-text query is rejected:
`HTTP 400: "'query' query parameter requires one of: author, subreddit"`.
There is no global Reddit search. Always pass `--subreddits`, sourced from
`inputs.json` `matrix[].subreddits`.

**2. `query` is stem-matching, not phrase-matching.** Searching `permit` in
r/sysadmin returns posts about firewall ACLs — `permit`/`permitted` in a
networking sense — not building permits. Verified: 10/10 results matched the
stem, 0/10 were about the intended topic. The same word means different things
in different communities, so a keyphrase that works in one subreddit can be
pure noise in another.

The practical rule for whoever picks keyphrases (scouts, and the historian in
`skills/retro-trends`): **prefer multi-word phrases that are unambiguous in the
target community**, and sanity-check a sample of returned titles before
treating a cell's capture as on-topic. A high hit count against an ambiguous
stem is the easiest way to manufacture a confident, wrong cluster.

### The dropped-query rule

When Arctic Shift is unavailable and `reddit_search.py` falls back to pullpush,
the `query` cannot be applied. By default the script **returns nothing for that
subreddit** and records `status: "unavailable"` — it does not substitute an
unfiltered listing.

This is deliberate, and it is the failure-as-absence rule pointed the other
way. An unfiltered listing is not a degraded answer to the question asked; it
is a complete answer to a different question. Forty recent r/smallbusiness
posts returned to a caller researching permits are real posts that are not
evidence of anything the run is about, and clustering cannot tell the
difference. Returning them manufactures signal.

`--allow-unfiltered-fallback` opts in to keeping them. Even then, each item is
stamped `query: null` rather than the requested string, because §2 defines
`query` as *the exact string that surfaced this item*. Regression-tested in
`tests/test_query_fallback.py`.
