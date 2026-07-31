---
name: deep-diligence
description: "Stress-tests ONE already-chosen candidate before the owner commits to building it, and writes the five-section `runs/<slug>/diligence.md` (CONTRACTS §8): Competition, Novelty, Proposed wedge/gap, Pricing potential, Unit economics — built from crawled competitor pages, not recollection. Applies once the owner has landed on a single candidate and wants it tested; triggers include `/diligence`, \"deep dive on this idea\", \"who else is doing this\", \"what could we charge\", \"is this a real market\", \"run diligence on the thing we just discussed\", or handing over a spec file / pasted spec / MVP proposal for evaluation. Do NOT use it for generating or ranking candidates (that is `/prospect` + `skills/prospect-methodology`), for inventing wedge permutations from pain evidence alone (`skills/wedge-voltage`), for choosing an MVP shape (`skills/mvp-shapes`), for the physical-goods exclusion (`skills/no-inventory-gate`), or when no single candidate has been chosen yet — diligence on three ideas at once produces three shallow reports and one wrong decision."
---

# Deep diligence: the five-section report

## Why this skill exists

The moment an owner picks one idea, the failure mode changes. During `/prospect` the risk is
missing signal. During diligence the risk is **confident fabrication**: a competitor table with
plausible prices nobody crawled, a "$4B TAM growing 14% CAGR" line traceable to nothing, a
margin number that forgot the human in the loop. Those numbers then flow into the pricing band
and the payback period, and the owner commits six months of their life to arithmetic performed
on invented inputs. That is the single worst thing this command can produce — worse than saying
"unknown" fifty times, worse than producing no report at all.

Three disciplines follow from that and they are not negotiable:

1. **A price that was not crawled is `[unknown]`.** Never estimated, never "roughly", never
   "typically around". An invented price silently poisons sections 4 and 5, and by the time it
   reaches the payback table nobody remembers it was a guess.
2. **Unknown is information; fabrication is damage.** "Blocked by robots.txt", "no pricing page
   — sales-led", "page rendered empty" are all *findings*. A blank cell filled with a plausible
   number is a lie with a table around it.
3. **No composite score, ever.** There is no "diligence score", no "opportunity rating", no
   weighted go/no-go number. Five sections, each with its own evidence and its own read. If you
   average them you have laundered five auditable judgments into one number nobody can check —
   the exact failure mode this whole plugin exists to avoid. Subscores stay separate all the way
   to the closing confidence statement.

And the inversion that catches the lazy report: **if you find no competitors and no chatter,
that is not a green field — it is an UNDER-RESEARCHED flag.** Real problems worth money almost
always have somebody bad at solving them already. Absence of counter-evidence is suspicious,
not validating. Say so in the section rather than writing "wide open market".

---

## Decisions already taken (do not re-litigate)

- **Every path below is run-relative.** `cards/`, `wedges/`, `shapes/`, `evidence/`, and
  `competitors/` all mean `runs/<slug>/…` — all contract files live under the run directory
  (CONTRACTS preamble). `.agents/product-marketing.md` is the one path that lives outside it,
  and that is exactly why it needs the backup dance below.
- **The five sections are fixed and ordered** (CONTRACTS §8): Competition, Novelty, Proposed
  wedge/gap, Pricing potential, Unit economics. Do not add a "Risks" section, do not reorder,
  do not merge Novelty into Competition. Later sections depend on earlier ones: pricing needs
  crawled comparables, economics needs the pricing band.
- **Key-free only.** No API keys, no OAuth, no paid data anywhere in this path. The vendored
  `skills/marketing/competitor-profiling` skill assumes Firecrawl + DataForSEO — **both are out
  of scope.** Use its *frames and profile template*; substitute `uv run scripts/crawl.py` for
  Firecrawl; the entire DataForSEO phase (domain rank, organic traffic, referring domains,
  ranked keywords) is `[unknown - requires paid data]`. Do not estimate traffic. Ever.
- **`skills/marketing/positioning` does not exist.** The vendored tree's positioning frame lives
  in `skills/marketing/product-marketing`. Use that plus `skills/marketing/launch` for narrative.
  Verify any marketing skill name with `ls skills/marketing` before citing it — citing a
  directory that does not exist makes the whole report look invented.
- **The no-inventory gate already ran** (`skills/no-inventory-gate`). Do not re-argue physical
  shapes. But "no inventory" does **not** mean "no COGS" — see section 5.
- **Bottom-up TAM only.** Top-down market-size claims are banned outright (section 5).
- **`dialog` is opportunistic.** Probe it; on any failure fall back to
  `uv run scripts/reddit_search.py` silently to the user but recorded in `source_health.json`.
- **Quotes are verbatim, ≤15 words, each with a resolvable URL** (CONTRACTS cross-cutting §2).

---

## Stage 0 — Ingest and fix the run

### Three accepted inputs

| Input mode | What you do |
|---|---|
| **File path** (`/diligence specs/permit-status.md`) | Read it. If it lacks buyer or wedge, ask for those two only — do not interview. |
| **Pasted spec text** | Use as-is. Extract the seven fields below; ask only for missing ones. |
| **"the thing we just discussed"** | Synthesize the spec from the conversation, then **confirm it back and wait for an explicit yes before running anything.** |

The confirm-back is mandatory in the third mode and it is not a formality. Twenty crawls, an
embedding run, and a pricing band aimed at the wrong buyer is a half-hour of compute and a
report that reads authoritative while being about a different product. Confirm in exactly this
shape, seven lines, no prose:

```
Name:        <short product name>
Buyer:       <who signs — role + org type, not "SMBs">
Job:         <the one job it does on day one>
Wedge:       <the narrow first slice; from wedges/<cluster_id>.json thesis if it exists>
Substrate:   <replaces X | attaches to X | greenfield>
Price guess: <owner's instinct, or "none">
Excluded:    <what it explicitly is not doing in v1>
```

Then: "Diligence will crawl competitors, price comparables, and model unit economics against
this. Correct?" Do not proceed on silence or on "sounds good, also what about…". Get the yes.

### Fix the slug and write inputs.json

**If the spec came from an existing `/prospect` run** (a card, a wedge, an MVP shape), **reuse
that run's slug and write `diligence.md` into that run directory.** Forking a new slug orphans
the report from `cards/<cluster_id>.json`, `wedges/<cluster_id>.json`, and the frequency panel
that section 5's TAM sketch is derived from. Reuse is the default.

If the spec is freestanding, mint a slug per CONTRACTS §1:
`<kebab-inspiration-truncated-40>-<YYYY-MM-DD>`, where the inspiration is the spec name.

Write `runs/<slug>/inputs.json` **before any capture** (CONTRACTS §1) so the run is auditable
and re-runnable. For diligence the `matrix` is minimal — 1–3 cells — and exists so that any
evidence captured in section 1 has a legal `cell_id` (CONTRACTS §2 requires one):

```json
{
  "slug": "permit-status-lookup-2026-07-31",
  "inspiration": "public permit-status lookup for contractors",
  "created_utc": 1753920000,
  "flags": {"niche": "municipal permitting", "cards_only": false, "top": 1},
  "matrix": [
    {
      "cell_id": "d01",
      "persona": "contractor waiting on a permit",
      "vertical": "municipal permitting",
      "framing": "competitor and pricing reconnaissance for a chosen spec",
      "queries": ["Accela alternative", "switched from Accela", "OpenGov permitting pricing"],
      "subreddits": ["Construction", "smallbusiness"]
    }
  ]
}
```

Set only the flags that apply; omit or `null` the rest. Never invent a flag value to fill the
shape (cross-cutting rule 1). If you are reusing an existing run's `inputs.json`, **append**
diligence cells with fresh `cell_id`s (`d01`, `d02`) — do not rewrite the prospect matrix.

### Generate the per-idea marketing context

The vendored marketing tree reads `.agents/product-marketing.md`. That path is a **global
singleton**, and this is the quiet killer: if a previous candidate's context is sitting there,
`skills/marketing/competitors` and `skills/marketing/pricing` will happily profile and price
*the wrong product*, and nothing in their output will say so.

1. If `.agents/product-marketing.md` exists and belongs to a different candidate, copy it to
   that candidate's `runs/<prior-slug>/product-marketing.md` if not already there, then overwrite.
2. Invoke `skills/marketing-context` to generate the context for **this** candidate.
   If that skill is unavailable, follow `skills/marketing/product-marketing/SKILL.md` Step 3
   directly: 12 sections plus `Document version` and `Changelog`.
3. Write **both** paths (CONTRACTS §7): `runs/<slug>/product-marketing.md` for the audit trail
   **and** `.agents/product-marketing.md`, which is what the tree actually reads. The audit copy
   alone does not activate anything.
4. Fields with no evidence are `[unknown — no evidence in run]`. Any claim carried from a card
   cites its evidence URL.

Confirm the tree is live before section 1: `.agents/product-marketing.md` names this candidate's
buyer. If it names a different buyer, stop and fix it.

---

## Tool probing (do this once, up front)

Never assume a flag. Both wrappers are written by this repo and their interfaces are the source
of truth:

```bash
uv run scripts/crawl.py --help
uv run scripts/reality_cli.py --help
```

Availability matrix — probe, then record. For every degraded path append to
`runs/<slug>/source_health.json` (CONTRACTS cross-cutting §5):

| Capability | Opportunistic primary | Guaranteed fallback |
|---|---|---|
| Competitor scan / trend direction | `idea-reality` MCP full scan | `uv run scripts/reality_cli.py` |
| Trend direction (secondary) | `trend-pulse` MCP | `uv run scripts/trends_cli.py` |
| Reddit chatter | `dialog` MCP | `uv run scripts/reddit_search.py` (Arctic Shift) |
| Page content | — | `uv run scripts/crawl.py` (there is no MCP path; this is it) |

`dialog` returns 401 without OAuth and self-hosting it needs Reddit API keys — treat failure as
the expected case, not an incident. The stdio MCPs (`trend-pulse`, `idea-reality`) may not load
at all in Cowork; the script fallbacks are the contract. Record each:

```json
{"source": "dialog", "status": "unavailable", "fallback": "reddit_search.py", "detail": "401"}
{"source": "idea-reality", "status": "unavailable", "fallback": "reality_cli.py", "detail": "stdio server did not load"}
{"source": "web:accela.com", "status": "robots-denied", "detail": "robots.txt disallows /pricing"}
```

The third line is not yours to invent: `crawl.py` emits its own `source_health` array keyed
`web:<host>` (plus `crawl4ai` for the engine itself). Copy those entries through verbatim rather
than re-describing them, and keep its status vocabulary exactly —
`ok` · `degraded` · `robots-denied` · `blocked` · `failed`. Those five strings are the only legal
values in the `Crawl status` column of every table below.

**A source that failed is never reported as "no discussion found" or "no competitors found."**
Those sentences are reserved for a source that actually answered and came back empty — and even
then they trigger the UNDER-RESEARCHED flag, not a green light.

---

## Crawl discipline

Non-negotiable, and cheap to obey:

- **Honor robots.txt.** Disallowed path → row reads `robots-denied` (crawl.py's status for "we
  did not fetch"), with the disallowed path named. `blocked` is reserved for a wall we *hit* —
  401/403/429, auth, paywall — so the reader can tell "not allowed to ask" from "asked and refused".
- **Rate limit**: ≤1 request/sec per domain, ≤10 pages per competitor. You need pricing,
  features, changelog, docs — not the whole site.
- **No auth walls, no paywall circumvention, no logins, no trial signups.** If pricing is behind
  a signup, that is a *finding* (self-serve but gated), recorded as `[unknown - behind signup]`.
- **Review sites** (G2, Capterra, TrustRadius) usually block key-free crawling. When they do,
  ratings and review counts are `[unknown]`. Do not substitute remembered ratings — a
  half-remembered "4.2 on G2" is indistinguishable from a fabricated one.
- **The JS-empty-page false negative.** `crawl.py` flags near-empty 200s as `degraded`. A
  degraded pricing page means *the crawler saw nothing*, not that the product is free and not
  that pricing does not exist. Retry the exact URL once; if still degraded, write
  `[unknown - page rendered empty]` and grade that competitor's pricing evidence 1/5. Reading a
  degraded page as "free product" has torpedoed a pricing floor before; it is the most common
  crawl mistake in this pipeline.

**Where crawled pages go.** Point `crawl.py --out` at
`runs/<slug>/competitors/raw/<competitor-slug>/<YYYY-MM-DD>/scrapes/`, mirroring
competitor-profiling's `raw/<competitor-slug>/<YYYY-MM-DD>/scrapes/` layout but rooted inside the
run so the run stays self-contained. `crawl.py` names each file from the slugified host+path and
stamps a `source_url` / `fetched_iso` / `robots` header into it — that header is what makes a
later quote traceable, so leave it intact. Never create a date folder that overwrites a prior
date's pull.

**Do not write crawled pages into `evidence/<source>.jsonl`.** The §2 `source` enum is
`reddit|hackernews|stackoverflow|producthunt|github|pypi|npm|wikipedia|google-trends|dialog`.
A crawled vendor page has no legal value there, and inventing one (`"source": "crawl"`) breaks
every consumer of that file. Reddit chatter captured in section 1 *does* belong in
`evidence/reddit.jsonl` (or `evidence/dialog.jsonl`), append-only, with a `cell_id` from the
matrix you just wrote.

---

## Section 1 — Competition

**Goal:** a table of who is already taking money for something adjacent, with **actual list
prices that were crawled, quoted, and linked.**

Read `skills/marketing/competitors/SKILL.md` and
`skills/marketing/competitor-profiling/SKILL.md` and follow their frames — specifically
competitor-profiling's "Facts Over Opinions" (every claim traceable, inferences labeled),
"Current Data" (snapshot date on everything, flag staleness), "Honest Assessment" (do not
inflate their weaknesses), its profile template, and competitors' "Pricing Comparison" and
"Centralized Competitor Data" (one source of truth per competitor, reused by later sections).

### 1a. Build the candidate list

Three sources, unioned:

- `idea-reality` full scan (or `uv run scripts/reality_cli.py`) — this is also where
  `saturation.competitor_count` and `saturation.trend_direction` in
  `cards/<cluster_id>.json` came from; reconcile with it and note any divergence.
- Reddit chatter: `dialog` if it answers, else `uv run scripts/reddit_search.py`. Queries that
  actually surface incumbents: `"<name> alternative"`, `"<name> pricing"`,
  `"switched from <name>"`, `"<name> vs"`, `"we use <name>"`, `"replacing <name>"`.
- Existing evidence in the run: `cards/<cluster_id>.json` → `wtp.existing_spend[].tool` is a
  list of vendors people already named as paid. Those are the highest-signal competitors in the
  whole report, because someone in the evidence is already paying them.

**Resolving a name to a domain: never guess.** Take the URL from the evidence item that named
the vendor, or from the `idea-reality` scan output. If neither resolves, the row's domain is
`[unknown]` and its crawl status is `not attempted` — no URL was ever fetched, so none of
crawl.py's five statuses applies, and writing `blocked` there would claim a request you never
made. A competitor you could not locate is still a competitor, and pretending you crawled
`<name>.com` is fabrication.

Deduplicate carefully: one company under two brand names is one competitor; a suite vendor's
adjacent module is one *row* but note it is a module, not a company, because its pricing is
bundled and its momentum signals belong to the parent.

### 1b. Crawl the top 5–10

Per competitor, in priority order: **pricing page → features/product page → changelog or
"what's new" → docs → careers/hiring**. Homepage last (it is the least informative page on any
SaaS site). Stop at 10 pages.

Extract, per `competitor-profiling`'s extraction table: tiers, prices, per-tier inclusions,
billing options, free tier/trial, enterprise signals; feature categories and how they *name*
things (this feeds section 2's corpus); changelog entry dates; open roles.

### 1c. The table

| Competitor | Domain | Segment | Packaging model | List price (crawled) | Price source URL + date | Momentum | Crawl status |
|---|---|---|---|---|---|---|---|

Hard rules for this table:

- **`List price` is either a crawled number or `[unknown]`.** No ranges you inferred, no
  "starts around". If the page says "from $99/mo per seat", write exactly that.
- **Every price carries its source URL and crawl date.** A price without a link is a rumor.
- **"No pricing page" is a finding, not a blank.** Record packaging model as
  `sales-led / quote-only` with the URL of the "Contact sales" or "Request a demo" page as
  evidence. Consequence to carry into sections 4 and 5: quote-only implies higher ACV, a sales
  motion you may not have, and a longer payback — say that in section 5, do not bury it here.
- **Every non-`ok` row stays in the table with its status visible** — `degraded`,
  `robots-denied`, `blocked`, `failed`, `not attempted`. A reader must be able to see how much of
  the landscape you actually saw.

### 1d. Momentum rubric (1–5, observable, never averaged into anything)

Graded from crawled artifacts only. If you could not crawl a changelog, momentum is `[unknown]`
— not 3.

| Grade | Observable criteria |
|---|---|
| 5 | ≥8 dated changelog/release entries in the trailing 90 days, **or** ≥5 open engineering roles on a crawled careers page. |
| 4 | 4–7 dated entries in trailing 90 days, or 2–4 open engineering roles. |
| 3 | 1–3 dated entries in trailing 90 days. Shipping, slowly. |
| 2 | Newest dated entry is 91–365 days old. Maintenance mode. |
| 1 | Newest dated entry >365 days old, or a changelog that exists but is undated (undated is a 1 — you cannot verify cadence). |
| `[unknown]` | No changelog, releases page, or careers page was crawlable. |

Write per-competitor profiles to `runs/<slug>/competitors/<competitor-slug>.md` using
competitor-profiling's profile structure, in its order (At a Glance / Positioning & Messaging /
Product & Features / Pricing / Customers & Social Proof / SEO & Content Strategy / Strengths &
Weaknesses / Competitive Implications for [Your Product] / Raw Data Sources). Keep the
`SEO & Content Strategy` heading and fill every row under it with
`[unknown - requires paid data]` — that is where the banned DataForSEO metrics would have gone,
and a visible row of `[unknown]`s is the honest record that the section was skipped for lack of
paid data, not overlooked. Then `runs/<slug>/competitors/_summary.md` with the landscape
paragraph, comparison table, positioning map, and gaps — the gaps list is the raw material for
section 3.

**If this section finds zero locatable competitors:** write `UNDER-RESEARCHED` at the top of the
section and state what you tried (queries run, sources probed, `source_health.json` entries).
Either the buyer names their tools in language you did not search, or the problem is not painful
enough for anyone to have monetized it. Both are worth knowing. "Wide open" is not one of them.

---

## Section 2 — Novelty

**Goal:** state plainly which of three things this is. The distance number supports the call; it
is not the call.

### 2a. Build the positioning corpus

From the crawled pages, one document per competitor: homepage headline + subheadline + pricing
tier names + feature-page H1/H2s. Their words, not your summary of their words — you are
measuring their positioning language, and paraphrase collapses exactly the differences you are
trying to detect. Write `runs/<slug>/competitors/positioning_corpus.jsonl`.

### 2b. Compute the distance

Reuse `scripts/cluster.py`'s embedding space — `embed()` and `centroid_distance()` — so the
number is comparable to `grounding.incumbent_distance` in `wedges/<cluster_id>.json`. Write a
small helper with PEP 723 metadata (fastembed) and run it:

```bash
uv run runs/<slug>/tmp/novelty.py   # imports embed/centroid_distance from scripts/cluster.py
```

That is the one invocation in this skill that is not `uv run scripts/<name>.py`, because it is a
throwaway run-local helper, not a plugin script — do not add it to `scripts/`.

**Assert the model matches.** `clusters.json.embedding_model` must be
`BAAI/bge-small-en-v1.5` and your helper must use the same. Mixing embedding models produces a
number with no meaning that looks exactly like a number with meaning. First run downloads the
model (~130MB, no key); it is offline after that.

Spec text to embed = the wedge `thesis` plus the three `axes` (`who_first`, `slice`,
`substrate`) from `wedges/<cluster_id>.json`, or the confirmed seven-line spec if no wedge file
exists. Also pull trend direction from `idea-reality` (or `uv run scripts/reality_cli.py`), and cross-check
`retro_trend.shape` and `slope_pct_per_year` on the card — a "new category" claim against a
`persistent-flat` twelve-year trend line is a re-segmentation wearing a costume.

### 2c. Make the call — one of exactly three

| Call | Criteria (all reader-checkable) | Strategic consequence |
|---|---|---|
| **New category** | Distance to competitor centroid ≥0.60; **and** no crawled page names both the same buyer and the same job; **and** the buyer has no existing budget line (`wtp.budget_line.new_category` is `true`). | Expensive education. You are not competing for a budget, you are creating one — which means the sale is to someone who must invent a line item, and that is the slowest sale there is. Price for a champion, not a procurement process. Assume 2–3x the sales cycle. |
| **Re-segmentation** | Distance 0.35–0.60; **and** ≥1 crawled competitor does substantially this job but for a different buyer, size, or vertical (quote their own segment language from the page). | **The fastest wedge, and the default answer.** The category is proven, the budget exists, and the incumbent's own positioning page tells your prospect they are not the customer. Win by being obviously *for them*. |
| **Better mousetrap** | Distance <0.35; **and** ≥1 crawled competitor names the same buyer and the same job. | Only viable if you win on a dimension the incumbent **structurally cannot copy** — their pricing model, their data moat's absence, their enterprise contracts, their on-prem legacy, their channel conflict. Name that structural constraint explicitly with a page citation. "Better UX" is not structural; they can hire a designer. If you cannot name the structural constraint, say so — that is the honest read, and it usually means don't build. |

Report the distance **and** the plain-language call, in that order, in one sentence. Then the
override rule, which matters more than the number:

**Crawled pages beat embedding distance.** If distance says 0.71 (new category) but a crawled
feature page shows a direct match on buyer and job, the page wins and the call is
better-mousetrap. Vocabulary distance is not market distance — a competitor describing the same
product in different words is still the same product. Note the disagreement in the report;
a number that disagreed with the evidence and lost is useful context for the reader.

---

## Section 3 — Proposed wedge / gap

**Goal:** where voltage is highest **and** incumbent coverage is thinnest — grounded in real
crawled positioning instead of a prospect-time proxy.

Re-run `skills/wedge-voltage` with one change: the **incumbent centroid is built from
`positioning_corpus.jsonl`** (real crawled pages), not from pain evidence or a guessed
competitor description. This is the whole reason diligence produces better wedges than
`/prospect` — at prospect time the incumbent centroid is an approximation; here it is the
incumbents' actual words.

Two things that will bite you:

1. **Do not recompute `pain_distance` against the competitor corpus.** Per CONTRACTS §5,
   `pain_distance` is distance to the cluster's *pain evidence* centroid (lower = better
   grounded). Re-anchoring it to competitor text silently redefines the field and makes
   ungrounded inventions look well-grounded — the one check that stops fabricated wedges stops
   working. Only `incumbent_distance` changes here.
2. **Preserve the prospect-time file.** Copy `wedges/<cluster_id>.json` to
   `wedges/<cluster_id>.prospect.json` before updating in place, and say in the report that
   `incumbent_distance` was recomputed against crawled positioning, with both values shown.
   A number that moved from 0.68 to 0.31 once the real pages arrived is one of the most valuable
   findings this command produces.

Then, for each claimed gap:

> **Gap:** <one sentence> · **Voltage:** V<1-4>, copied from the wedge's `voltage` field
> · **Incumbent coverage:** thin/partial/covered
> · **Evidence:** <competitor page URL> — "<≤15-word quote from the page>" (crawled <date>)

`Incumbent coverage` is read off the crawled corpus, never off impression — three levels and an
honest fourth:

| Level | Observable criterion (crawled pages only) |
|---|---|
| `covered` | ≥1 crawled page names the capability as shipped: a feature-page H1/H2, a pricing-tier inclusion, or a docs page for it. Cite the page. |
| `partial` | It appears only as a changelog/roadmap item, a beta, a paid add-on, or is named for a different buyer, size, or vertical than ours. |
| `thin` | No crawled page from any competitor names it, **and** ≥5 competitors' feature *and* pricing pages came back `ok`. |
| `[unknown]` | Fewer than 5 competitors returned `ok` feature/pricing pages. Coverage is unassessed, not thin — this is the same false negative as reading `degraded` as "free". |

**A gap asserted without a competitor-page citation is a guess and must be labeled
`[guess - no page evidence]` inline.** Do not delete uncited gaps — an owner's intuition about a
gap is worth recording — but never let one sit in the same visual register as a cited one. That
mixing is how a hunch becomes a roadmap.

Also record the anti-gap: gaps you expected and found **covered**, with the page that covers
them. That list is what stops the owner rediscovering the same dead end in month three.

For narrative framing use `skills/marketing/product-marketing` (the vendored tree's positioning
frame — there is no `positioning` skill) and `skills/marketing/launch`. Keep the frames; drop
any step that needs a keyed tool.

---

## Section 4 — Pricing potential

Read `skills/marketing/pricing/SKILL.md` and use its three axes explicitly: **packaging** (what
is in each tier), **pricing metric** (what you charge for), **price point** (the dollars). Use
its value-metric test on your proposed metric: "as a customer uses more of <metric>, do they get
more value?" If no, the metric is wrong and no price point rescues it.

The band comes from pricing's value-based frame, with each end sourced:

- **Floor** = the next best alternative's crawled effective price (cite URL + date). If the next
  best alternative is a spreadsheet, the floor is the crawled/quoted cost of the workaround from
  `cards/<cluster_id>.json` → `wtp.workaround_cost[].claim` with its URL.
- **Ceiling** = quantified value from the card's evidence — e.g. "two staff, ~10 hrs/week each"
  × a loaded hourly rate that is labeled `[assumption]`. The hours are evidence; the rate is an
  assumption. Never blur them.
- **Proposed point** sits between, with one sentence on why.

### Comparables table

| Competitor | Their price (crawled) | Source URL + date | Their packaging | Their segment | We sit above/below | Why |
|---|---|---|---|---|---|---|

Every number in this section traces to a crawled page or carries `[assumption]`. There is no
third category. If more than half the comparables are `[unknown]` or `[assumption]`, say at the
top of the section: **"Pricing band rests on N of M crawled comparables"** — and let the reader
discount it accordingly rather than discovering the softness in section 5.

Sales-led competitors with no public price: their row's price is `[unknown - quote only]`, and
that is a positioning signal, not a hole. It tells you the top of the market is negotiated,
which usually means a self-serve public price is itself the wedge.

---

## Section 5 — Unit economics

No inventory (the gate already ran), so the model has exactly four moving parts — CAC, monthly
price (from §4), gross margin, and the payback they imply — plus a TAM sketch.
**"No inventory" does not mean "no COGS."**

### CAC by channel

Take the channel from `shapes/<cluster_id>.json` →
`distribution_complexity.primary_channel` / `secondary_channel` and its
`distribution_complexity.grade`. **Name which marketing skill supplied each benchmark**, so the
reader can go read it and disagree:

| Channel | Benchmark used | Source skill (exact path) |
|---|---|---|
| Cold outbound | Realistic funnel: 500 emails → 25 replies (5%) → 4 meetings → 1 customer, ~0.2% end-to-end for average performers; reply rates 4–5.8% and declining ~15% YoY | `skills/marketing/cold-email/references/benchmarks.md` |
| Comparison/alternative pages (SEO) | Comparison pages convert 5–15% vs 0.5–2% for generic content | `skills/marketing/directory-submissions` |
| Funnel + CAC definition | CAC = total sales + marketing spend / new customers; LTV:CAC 3:1–5:1 healthy; lead→MQL 5–15%; win rate 20–30% | `skills/marketing/revops` |
| Blended-CAC discipline | Blended CAC must include salaries, content, tools, retainers — not just ad spend; add 10–20% experimental budget | `skills/marketing/marketing-plan` |
| Paid (only if genuinely proposed) | Max allowable CAC derived from LTV; judge blended CAC, not platform CPA | `skills/marketing/ads` |
| Free-tool / programmatic / community wedges | Effort and mechanics, not dollar CAC | `skills/marketing/free-tools`, `skills/marketing/programmatic-seo`, `skills/marketing/community-marketing` |

Where the vendored tree has **no dollar figure** for a channel — which is most of them, because
they are craft skills not benchmark databases — the CAC cell is `[assumption]` with your
reasoning shown, and you do **not** attribute a number to a skill that does not contain one.
Fake attribution is worse than a naked assumption because it survives review.

For a founder-time channel, denominate CAC in **hours**, then convert with a labeled
`[assumption]` hourly rate. Hiding founder time at $0 makes every channel look free and is how
"we'll just post in communities" becomes the plan.

### Gross margin

Line items, all per-customer-per-month:

- Infra (hosting, storage, egress) — `[assumption]` unless you have a real bill.
- **Per-call model/API spend** if the product is LLM-shaped. This is real variable COGS and it
  scales with usage, not with headcount.
- **Human-in-the-loop minutes × loaded hourly rate.** If `shapes/<cluster_id>.json` → `shape` is
  `concierge-manual` or `agent-automation-service` — the two shapes in `skills/mvp-shapes`' closed
  eight-shape taxonomy whose `sketch` must name a human-in-the-loop checkpoint — this is the
  dominant COGS line and **it is the omission this section exists to prevent.** Match on those
  exact strings; there is no `service-first` shape. A concierge MVP with 45 minutes of
  human review per customer per month is not a 92%-margin SaaS; it is a services business with
  a login. Write the minutes down explicitly, even at `[assumption]`, so the owner can see the
  number that decides whether this scales.

### Payback

`payback_months = CAC / (monthly price × gross margin %)`. Show the arithmetic inline so a
reader can change one input and redo it in their head. Flag payback >12 months explicitly:
with no outside capital assumed, a 12-month payback means you fund one customer at a time.
Cross-reference `revops`' 3:1 LTV:CAC as the sanity floor.

### TAM sketch — bottom-up from the frequency panel

**Top-down TAM is banned here and the ban is the point.** "$4.2B market growing 14% CAGR" is
unfalsifiable, traces to a paywalled report nobody in this run has read, and changes no decision
you will make this quarter. It is theater that makes a report feel finished. The bottom-up count
is falsifiable, it is built from URLs the reader can click, and it answers the only question
that matters in month one: **how many identifiable humans have this problem and where are they?**

Derive from `cards/<cluster_id>.json` → `frequency`:

- **Floor** = `distinct_authors` — humans who demonstrably have this problem, each traceable to
  a permalink. Not an estimate. A floor of 39 is a real 39.
- **Reach check** = `distinct_communities` — how many places you can find them. One community
  means one distribution channel and one point of failure.
- **Ceiling** = a countable universe × a labeled `[assumption]` visibility multiplier
  (posters per sufferer). **If there is no countable universe** (e.g. "the number of US
  municipalities" from a citable source), the ceiling is `[unknown]`, not a guess.

State the visibility multiplier as its own labeled row, prominently. It is the single largest
lever in this entire section — 1:100 versus 1:1000 moves the ceiling by 10x — so it must sit
where the reader can grab it, never buried inside a computed total.

### Assumptions register (required, at the end of section 5)

| # | Assumption | Value used | Moves what | Owner's override |
|---|---|---|---|---|
| A1 | Loaded hourly rate for buyer time | `[assumption]` $X/hr | Pricing ceiling (§4) | |
| A2 | Human-in-loop minutes/customer/month | `[assumption]` N min | Gross margin, payback | |
| A3 | Visibility multiplier (posters:sufferers) | `[assumption]` 1:N | TAM ceiling | |
| A4 | CAC for primary channel | `[assumption]` $X | Payback | |

Every `[assumption]` used anywhere in sections 4–5 appears here with the thing it moves. The
register is the deliverable's most useful artifact: it lets the owner change one number and see
what breaks without re-reading the report. If an assumption appears in a table but not in the
register, the register is wrong.

---

## Closing confidence statement

Not a hedge. A map of where the report is soft, with one named weakest link.

Grade each section's evidence 1–5 on observable criteria — and **do not average these into an
overall confidence number.** Five grades, shown separately, exactly as with everything else in
this plugin.

| Grade | Criteria |
|---|---|
| 5 | 100% of the section's claims cite a crawled page or a run artifact, with dates. No `[assumption]` in a load-bearing position. |
| 4 | 80–99% cited; assumptions present but all registered and non-structural. |
| 3 | 50–79% cited; ≥1 load-bearing `[assumption]` (a price, a CAC, a multiplier). |
| 2 | <50% cited, **or** ≥2 of the top-5 competitors came back `robots-denied` / `blocked` / `degraded`; the section's conclusion would flip if one assumption changed. |
| 1 | No crawled page or run artifact behind the section's conclusion. Or `UNDER-RESEARCHED`: sources answered and returned nothing, or failed and fell back with no result. |

Show the fraction you counted next to each grade (e.g. `4 — 11/13 claims cited`), counting a
claim as one sentence asserting a fact about the world. A grade with its denominator visible is
re-checkable by the reader; a bare number is another adjective.

Then, in prose, three things and nothing else:

1. **The weakest link, named specifically.** Not "some data was unavailable" — "The pricing band
   rests on two crawled comparables out of seven; Accela and Tyler both blocked crawling, and
   they are the two most likely to anchor the market."
2. **The one action that would most change the report.** Usually a single crawl, a single quote
   from a real buyer, or one number the owner already knows.
3. **What this report does not claim.** Diligence bounds a decision; it does not make it. There
   is no verdict line, no score, no recommendation to build or not build. The owner reads five
   sections and decides.

---

## Failure modes (each of these has actually happened)

1. **Invented price.** A plausible "$49/mo" nobody crawled flows into the band and the payback
   table. Discipline: every price cell carries a URL and a date, or reads `[unknown]`.
2. **Degraded page read as "free".** `crawl.py` returns a near-empty 200; the report concludes
   the competitor has no paid tier, and the pricing floor collapses. Discipline: `degraded`
   means the crawler saw nothing. Retry once, then `[unknown - page rendered empty]`.
3. **Stale marketing context.** `.agents/product-marketing.md` still describes the previous
   candidate, so `competitors` and `pricing` profile the wrong product with total confidence.
   Discipline: back up, overwrite, and verify the buyer line before section 1.
4. **Recomputing `pain_distance` against competitor text.** Silently redefines CONTRACTS §5 and
   makes ungrounded wedges pass the grounding check. Only `incumbent_distance` is recomputed.
5. **Mixing embedding models.** A distance computed in a different space than
   `clusters.json.embedding_model` is noise that looks like signal. Assert the model.
6. **"No competitors found" after `idea-reality` failed to load.** That is a tooling outcome
   reported as a market finding, and it is the most dangerous sentence in the report. Check
   `source_health.json` before writing any negative finding.
7. **Zero competitors treated as a green field.** Absence of counter-evidence is suspicious.
   Flag `UNDER-RESEARCHED` and list what you searched.
8. **Top-down TAM sneaking in** via a "for context, the market is roughly…" aside. Delete it.
9. **Forgotten human-in-loop cost** in a `concierge-manual` or `agent-automation-service` shape
   → fake 90% margin, fake payback.
10. **Rolling the five sections into a verdict** because the owner asked "so is it good?".
    Answer with the five reads and the weakest link. Never a number.
11. **Running before confirming a synthesized spec.** Twenty crawls aimed at the wrong buyer.
12. **Writing crawled pages into `evidence/*.jsonl`** with a `source` value outside the §2 enum,
    breaking `cluster.py` and every downstream consumer.
13. **Guessing a competitor's domain** from their name. Resolve from evidence or scan output, or
    mark the row `[unknown]` / `not attempted` — never `blocked`, which claims a fetch.
14. **Double-counting competitors** across brand names, or counting a suite vendor's bundled
    module as a standalone priced product.
15. **Crawling past a login or paywall** because the pricing was "right there behind the signup".
    It is `[unknown - behind signup]`, which is itself a finding about their motion.

## Definition of done

- `runs/<slug>/inputs.json` written before any capture (§1).
- `runs/<slug>/product-marketing.md` **and** `.agents/product-marketing.md` both written (§7),
  naming this candidate's buyer.
- `runs/<slug>/source_health.json` has an entry for every probed source, including crawl.py's
  per-host `web:<host>` entries with their real statuses.
- `runs/<slug>/competitors/` holds raw crawls, per-competitor profiles, `_summary.md`, and
  `positioning_corpus.jsonl`.
- `runs/<slug>/wedges/<cluster_id>.prospect.json` preserved;
  `runs/<slug>/wedges/<cluster_id>.json` updated with the recomputed `incumbent_distance` and
  unchanged `pain_distance` (§5).
- `runs/<slug>/diligence.md` has all five sections in contract order (§8), every assumption cell
  labeled `[assumption]`, an assumptions register, per-section evidence grades, a named weakest
  link — and no composite score anywhere.
