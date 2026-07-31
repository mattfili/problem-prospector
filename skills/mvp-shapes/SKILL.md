---
name: mvp-shapes
description: "Turns a wedge from `runs/<slug>/wedges/<cluster_id>.json` into 1–3 concrete MVP proposals drawn from a closed eight-shape taxonomy, grades technical and distribution complexity as two independent 1–5 scores that are never blended, stamps founder-fit separately, and writes `runs/<slug>/shapes/<cluster_id>.json` per CONTRACTS §6. Applies at the post-wedge stage of `/prospect`, and on requests phrased as \"what would I actually build\", \"shape this into an MVP\", \"how hard is this\", \"first version\", \"scope the build\", as well as grading technical or distribution complexity for an existing shape or checking founder-fit against the owner's stack. Do NOT use it to invent opportunities from thin air (a wedge grounded in evidence is a hard precondition), to do competitor or pricing research (`skills/deep-diligence`), to generate the wedges themselves (`skills/wedge-voltage`), to decide whether an opportunity is admissible at all (`skills/no-inventory-gate`), or to produce any single overall difficulty number — the two grades always travel side by side."
---

# MVP shapes: fixed taxonomy, two separate grades, honest founder fit

## Why this skill exists

Left alone, a model asked "what's the MVP?" writes a fresh product vision every
time. Same evidence, three runs, three unrelated proposals — one a SaaS, one a
marketplace, one a "platform" — and no way to tell whether the difference came
from the evidence or from the model's mood that afternoon. You cannot compare
proposals across clusters, you cannot compare across runs, and you cannot audit
why a shape was chosen. The evidence pipeline upstream is rigorous and then the
last mile turns into freestyle brainstorming.

Three specific failures this skill prevents:

1. **Shape drift.** Without a fixed taxonomy, the shape reflects the writer, not
   the signal. The taxonomy below is closed on purpose. Constraining the vocabulary
   is what makes "why this shape?" answerable.
2. **The laundered composite.** "Overall difficulty: 3.5/5" is worse than no
   number. It hides that one business is a weekend build nobody can find and the
   other is a year of engineering with a warm list of buyers waiting. Those need
   opposite decisions. Averaging destroys exactly the information that drives the
   decision.
3. **The flattering discount.** The owner has deep Scala/Spark/Databricks, Azure,
   healthcare-data and MCP/agent experience. It is very easy to quietly grade a
   hard thing a 3 "because I could do it", and then hand that 3 to a reader who
   reads it as universally tractable. The grade describes the build; the fit
   describes the builder; they are separate fields.

## Decisions already taken — do not re-litigate

- The taxonomy has **exactly eight shapes**. You do not add a ninth, and you do
  not rename one. If nothing fits, emit zero shapes with a note.
- **1–3 shapes per wedge.** Not four. Not three-by-default.
- **Technical complexity and distribution complexity are two independent 1–5
  grades, reported side by side, never averaged, summed, weighted, or collapsed
  into any single ranking number.**
- **Founder fit never edits the grade.** It writes `founder_fit.note` and
  `founder_fit.effective_complexity_delta`, and nothing else.
- **Distribution complexity belongs to the distributor agent**, informed by
  `skills/marketing/`. This skill defines its rubric and sanity-checks it; it does
  not invent channels.
- **Inventory is a gate, not a penalty.** A shape needing physical stock,
  warehousing, fulfillment, or per-unit COGS on goods is deleted from the list,
  never graded harder. See `skills/no-inventory-gate`.
- Output goes to `runs/<slug>/shapes/<cluster_id>.json` in the exact shape of
  CONTRACTS §6 — top level is exactly `wedge_id` and `shapes`. No extra
  top-level keys, no `cluster_id` key (the filename carries it).
- **Nothing in this file is estimated.** Every number, name, channel, and link
  must trace to a card field, a wedge field, or the distributor's block. No
  invented prices, competitor counts, timelines, vendor names, or URLs — and no
  filling a gap with a plausible-sounding value. CONTRACTS cross-cutting rule 1:
  an unknown is `null` in the JSON and `[unknown]` in prose. Pricing research
  lives in `skills/deep-diligence`, never here.

---

## Preconditions

Read, in this order:

1. `runs/<slug>/cards/<cluster_id>.json` — the OpportunityCard (CONTRACTS §4).
   This is the evidence you route on.
2. `runs/<slug>/wedges/<cluster_id>.json` — the wedge permutations (CONTRACTS §5).

Then check `cards/<cluster_id>.json` → `inventory_gate.verdict`. If it is anything
other than `"pass"`, **stop**. That cluster was excluded upstream and must not be
shaped. Say so plainly rather than producing shapes for an inadmissible cluster.

Also check `skeptic.under_researched`. If `true`, you may still shape, but every
shape's `technical_complexity.reasoning` must state that the requirement surface
is unverified — an under-researched pain routinely hides an integration or
compliance dimension that moves the grade two levels.

### Missing panels are unread sources, not empty markets

Two of the card panels this skill routes on are produced by stdio MCP servers
that frequently do not load at all — the standing condition in Cowork.
`saturation` comes from `idea-reality` (CONTRACTS §4 `saturation.source`);
`retro_trend` comes from `trend-pulse` and the history scripts.

So before routing on them, check whether they are actually populated:

- `saturation.competitor_count` / `saturation.trend_direction` `null` or the
  panel absent → you have **no saturation read**. Do not route to
  `free-tool-wedge` on an assumed-high competitor count, and do not clear
  `api-integration` of the "both vendors are shipping this connector" risk on an
  unread `trend_direction`. Say which panel was unread and route on what you have.
- `retro_trend.shape` absent → the `persistent-flat` row of the routing table
  simply does not fire. It is not a `null`-means-flat.

If you need one of those reads and are going to fetch it: use the `idea-reality`
MCP, falling back to `uv run scripts/reality_cli.py` when the stdio server does
not load; `trend-pulse`, falling back to `uv run scripts/trends_cli.py`. If you
need to reopen a Reddit exemplar to check whether a workaround is really uniform,
the `dialog` MCP routinely 401s or demands OAuth — fall back to
`uv run scripts/reddit_search.py` (Arctic Shift, key-free), and
`uv run scripts/crawl.py <url>` for anything else. Every fallback is silent to
the user but **recorded** per CONTRACTS cross-cutting rule 5 — append to
`runs/<slug>/source_health.json`:

```json
{"source": "idea-reality", "status": "unavailable", "fallback": "reality_cli.py", "detail": "stdio server did not load"}
```

A source that failed is **never** reported as "no competitors" or "no discussion
found", and never as a reason a shape looks uncontested.

### One wedge per file — a real integration trap

CONTRACTS §6 gives the file a **single scalar `wedge_id`** at top level and the
filename is keyed by `cluster_id`. So one cluster yields shapes for **exactly one
wedge**: the top wedge as already ranked by the wedgesmith. Do not re-rank the
wedges here, do not invent a `wedges` array, do not key the file by `wedge_id`, do
not write two objects into one file. If a run genuinely needs shapes for a second
wedge of the same cluster, that is a contract change — update CONTRACTS §6 and
every producer/consumer named there. Do not improvise a schema; the downstream
`opportunity-cards.md` renderer and `/diligence` both index on this shape.

Pick the top wedge by the wedgesmith's own ordering. Sanity-check only that
`grounding.pain_distance` is not the worst of the set — a wedge that is ungrounded
invention should already have been dropped, and if the top wedge has the highest
`pain_distance` in the file, say so before shaping it.

---

## The fixed taxonomy

Legal values for `shape` — these exact strings, nothing else:

`concierge-manual` · `single-workflow-saas` · `data-product-report` ·
`api-integration` · `marketplace-no-inventory` · `browser-extension` ·
`agent-automation-service` · `free-tool-wedge`

### `concierge-manual`
- **IS:** You do the work by hand for a handful of named customers; software, if
  any, is internal. The customer buys the outcome and never sees the seams.
- **RIGHT when:** `intensity.score` ≥ 4 and the workflow is bespoke per customer —
  `skeptic.steelman` or `skeptic.structural_blockers` says the process differs by
  organization, or `wtp.workaround_cost` shows people paying real hours in
  idiosyncratic ways. Also right when `quadrant` is `low-freq/high-intensity`:
  too few sufferers to justify product build, each one bleeding enough to pay.
- **WRONG when:** frequency is high and the workflow is uniform — you are choosing
  to hand-crank something that wanted to be code. Also wrong when the unit of work
  is large and unbounded (you have sold consulting and called it a startup).
- **Failure mode:** it never converts to product. You learn the customer's process
  so well that automating it always looks like next quarter's job, and the margin
  stays at agency levels forever. Mitigation: from day one, log every manual step
  and name the single step you will automate first.

### `single-workflow-saas`
- **IS:** One screen or one loop that replaces one named recurring workaround for
  one persona. Multi-tenant, self-serve or light-touch sales.
- **RIGHT when:** `frequency.read` is `high`, `intensity.markers.workaround_built`
  is `true`, and the *same* workaround keeps appearing across
  `frequency.distinct_communities` ≥ 3 — that repetition is the evidence that the
  workflow is uniform enough to productize. Strengthened when
  `wtp.budget_line.new_category` is `false`.
- **WRONG when:** `quadrant` is `high-freq/low-intensity` — everybody has the
  problem and nobody will pay for a subscription to fix it. Also wrong when the
  evidence shows each org's process differs (that is `concierge-manual` first).
- **Failure mode:** scope creep into "the platform". The complaint was one screen;
  six months later there is auth, billing, an admin panel, a settings page, and no
  users. Mitigation: the `sketch` must name the one screen and what it explicitly
  does not do.

### `data-product-report`
- **IS:** You assemble data nobody has assembled and sell the answer — a
  recurring report, dataset, benchmark, or dashboard. The output is the product.
- **RIGHT when:** the evidence is people asking for *numbers they cannot get*:
  "how do other agencies handle X", "what's the benchmark", "we have no visibility
  across systems". Strengthened when `wtp.existing_spend` names a paid vendor whose
  data the buyer cannot see across, and when `frequency.distinct_communities` shows
  the same question asked in separate rooms (nobody has published the answer).
- **WRONG when:** the underlying data requires negotiated or purchased access
  (`data_acquisition` 4–5) — that is not an MVP, that is a business-development
  project. Also wrong when the buyer wants the workflow changed, not measured.
- **Failure mode:** you build the pipeline before confirming anyone pays for the
  answer. Mitigation: hand-produce the first edition in a spreadsheet and sell it
  before any pipeline exists.

### `api-integration`
- **IS:** A connector, sync, or endpoint that makes two systems the customer
  already pays for talk to each other. You own no system of record.
- **RIGHT when:** evidence text **names both systems** and the complaint is the
  gap between them ("we export from A and retype into B"). Strongest with
  `intensity.markers.time_quantified` `true` — the hours are the pitch. Works at
  `low-freq/high-intensity` because the buyer is already identified by the pair of
  tools they run.
- **WRONG when:** either endpoint has no accessible API or a certification program
  gates integration (`integration_surface` 5). Also wrong when the two systems'
  vendors are actively shipping the same connector — check
  `saturation.trend_direction`.
- **Failure mode:** platform risk plus versioning tax. The first version ships in
  three weeks and then you spend forever chasing both vendors' breaking changes,
  with no pricing power because it looks like a feature. Mitigation: price on the
  hours saved, not on the connector.

### `marketplace-no-inventory`
- **IS:** You match two parties and never take possession of anything. Services,
  capacity, attention, appointments, information — never goods.
- **RIGHT when:** the pain is *finding the counterparty*, and evidence shows both
  sides already transacting inefficiently elsewhere (DMs, spreadsheets, phone
  trees, a Facebook group). Both sides must appear in the evidence — one-sided
  demand with imagined supply is not a marketplace.
- **WRONG when:** either side is thin, when the transaction is one-off per user
  (no repeat = no liquidity), or when **anything is stocked, warehoused, shipped,
  or bought-to-resell**. That last case is not a lower grade, it is an exclusion:
  delete the shape.
- **Failure mode:** cold start on both sides simultaneously; you subsidize
  liquidity you cannot afford and disintermediation happens the moment both sides
  swap contact details. Mitigation: name in the `sketch` which side you hand-build
  first and how the transaction stays on-platform.

### `browser-extension`
- **IS:** Software injected into a web UI the user cannot change, improving the
  screen in place.
- **RIGHT when:** the pain lives inside a specific vendor portal, admin console,
  or web client that the buyer does not control, and the evidence quotes describe
  per-screen friction ("seven clicks to see status", "I keep a second tab open").
  Excellent at `high-freq/low-intensity` because install is the whole onboarding.
- **WRONG when:** the workflow spans systems or needs server-side state and
  scheduling. Also wrong when a single vendor's UI change or store policy can end
  you and the evidence names only one such vendor.
- **Failure mode:** existential platform dependence plus store review, and a user
  base that assumes free. Mitigation: the extension is the wedge; name the paid
  server-side artifact it feeds.

### `agent-automation-service`
- **IS:** An agent or automation that performs a repeated, rule-shaped task
  end-to-end — watch, extract, decide, act, report — and is sold as a service, not
  as tooling the customer must configure.
- **RIGHT when:** `intensity.markers.time_quantified` is `true` and the evidence
  describes a *repeated deterministic-ish chore*: chasing status, reconciling two
  lists, re-keying, triaging an inbox, monitoring for a change. Strengthened when
  `wtp.workaround_cost` quantifies staff hours (an agent priced under those hours
  sells itself).
- **WRONG when:** an error is expensive and unrecoverable and no human review step
  is affordable (that is `model_needs` 3–4, grade honestly). Also wrong when the
  task needs judgment the evidence shows practitioners themselves argue about.
- **Failure mode:** silent wrongness. It works in the demo, drifts in month two,
  and nobody notices until trust is gone. Mitigation: the `sketch` must state the
  human-in-the-loop checkpoint and what the agent refuses to do.

### `free-tool-wedge`
- **IS:** A genuinely useful free utility that captures the search or community
  demand around the pain and hands you the audience for a paid thing. Per
  `skills/marketing/free-tools` (engineering-as-marketing).
- **RIGHT when:** there is narrow, specific query demand around the pain — the
  same question asked in the same words across communities — and
  `saturation.competitor_count` is high enough that direct entry is a knife fight
  but nobody owns the top-of-funnel utility. **Cite the actual
  `saturation.competitor_count` value and `saturation.trend_direction`** rather
  than asserting "high"; if the panel is `null`, this row does not fire (see
  *Missing panels are unread sources*). Pairs with
  `skills/marketing/programmatic-seo` when the tool templates over a list
  (cities, agencies, vendors, codes).
- **WRONG when:** there is no named paid thing downstream (then it is a hobby),
  or when `quadrant` is `low-freq/high-intensity` — high-value pain with no search
  volume cannot be reached by a free tool. Also wrong when the tool's whole value
  is the thing you would otherwise charge for.
- **Failure mode:** traffic that never converts. You get 20k visitors who wanted
  the free answer and nothing else. Mitigation: the `sketch` must state the paid
  next step and the moment the handoff happens.

---

## Signal → shape routing (traceability, not taste)

Shape choice must be defensible by pointing at card fields. Use this table; when
two rows fire, propose both shapes rather than splitting the difference.

| Card signal (exact field) | Points to |
| --- | --- |
| `intensity.score` ≥ 4 **and** `skeptic.steelman` cites per-org bespoke process | `concierge-manual` |
| `quadrant` = `low-freq/high-intensity` | `concierge-manual`, `api-integration`, `data-product-report` |
| `quadrant` = `high-freq/low-intensity` | `browser-extension`, `free-tool-wedge` — **never** `single-workflow-saas` |
| `quadrant` = `high-freq/high-intensity` | `single-workflow-saas`, `agent-automation-service` |
| `intensity.markers.workaround_built` = `true`, same workaround across ≥ 3 `frequency.distinct_communities` | `single-workflow-saas` |
| `intensity.markers.time_quantified` = `true` on a repeated rule-shaped chore | `agent-automation-service` |
| Evidence names two specific systems and the gap between them | `api-integration` |
| Evidence asks for numbers/benchmarks nobody publishes | `data-product-report` |
| Pain is counterparty discovery, both sides present in evidence | `marketplace-no-inventory` (then run the inventory check) |
| Pain is inside a vendor web UI the buyer does not control | `browser-extension` |
| High `saturation.competitor_count` + narrow repeated query wording | `free-tool-wedge` |
| `wtp.budget_line.new_category` = `true` | prefer `free-tool-wedge` or `concierge-manual` — creating a budget line is the hardest sale there is |
| `intensity.markers.complainer_is_buyer` = `false` | re-read the wedge's `axes.who_first`; the shape must serve the *buyer* named in `wtp.buyer_class`, not the loudest sufferer |
| `retro_trend.shape` = `persistent-flat` | favors `concierge-manual` / `api-integration` — persistent means durable, not urgent |

Every shape's `sketch` must be traceable to the chosen wedge's `axes` — the
`who_first` is the first user, the `slice` bounds what the MVP does, the
`substrate` determines whether you attach to or replace an existing system. A
sketch that contradicts the wedge's axes means either the wrong shape or the wrong
wedge.

`sketch` is one or two sentences, names the first customer and the first surface,
and is falsifiable. "Public permit-status lookup, one city, scraped nightly." is a
sketch. "An AI-powered platform for modernizing government workflows" is not.

**Do not pad.** One well-routed shape beats three where two were filler. If only
one row of the table fires, write one shape.

---

## Technical complexity — grade honestly, not protectively

Write all five sub-dimensions **and** the headline grade with stated reasoning
into `technical_complexity`. Per CONTRACTS §6 the block is exactly
`technical_complexity.grade` (the headline number),
`technical_complexity.reasoning`, and the five sub-dimensions nested under
`technical_complexity.dimensions` — `data_acquisition`, `integration_surface`,
`model_needs`, `infra`, `compliance`. Never flatten the sub-dimensions up into
`technical_complexity` itself; the renderer and `/diligence` both read
`.dimensions`. The sub-dimensions are the audit trail; a reader who disagrees
with the headline can see which dimension you weighted.

The owner can absorb more technical difficulty than most founders. That is a
reason to grade **accurately**, not gently: an inflated grade on a tractable
problem quietly kills a real opportunity, and nobody ever notices the cost.
Deflation is the mirror sin — do not soften a grade because the shape excites you.

### `data_acquisition`
1. Public bulk download or documented open API, stable, clearly licensable.
2. Scrapeable public HTML, no auth, tolerant of nightly cadence.
3. Behind a login, rate-limited, or anti-bot; or requires a per-customer manual export.
4. Requires a signed data agreement, a purchased dataset, or access to the customer's database.
5. The data does not exist in accessible form, or access is legally foreclosed.

### `integration_surface`
1. None — standalone page, file, or single-tenant app.
2. One direction: a webhook out, or CSV in/out.
3. One bidirectional OAuth integration you must keep working across vendor changes.
4. Two to four integrations across vendors with differing auth, versioning, and per-customer configuration.
5. Must run inside a system with no sandbox access, or integration requires a vendor certification program.

### `model_needs`
1. None — deterministic logic.
2. One LLM call behind a prompt; errors are visible and human-correctable.
3. LLM or heuristic where a wrong answer is costly: needs an eval set and a review loop.
4. Needs a trained or tuned model plus labeled ground truth you must first collect.
5. Requires accuracy the field has not demonstrated.

### `infra`
1. Static host or a single function.
2. One small server plus a managed DB; no queue.
3. Background workers, scheduled jobs, retries, per-tenant isolation.
4. Heavy batch (Spark-class), stateful multi-region, or contractual uptime with on-call.
5. Hard realtime or a scale where the infrastructure *is* the product.

### `compliance`
1. No regulated data; public information only.
2. Basic PII (name, email); privacy policy and a DPA on request.
3. Buyer security questionnaires; SOC 2 Type I expected inside the sales cycle.
4. PHI, PCI, FERPA, or CJIS in scope — BAA, encryption and audit-log controls before the first paying customer.
5. FedRAMP/StateRAMP authorization, or state licensure — a multi-quarter, capital-intensive gate.

### Headline grade

Deterministic rule, so the same sub-dimensions always yield the same headline:

- Headline = **max** of the sub-dimensions, **unless** exactly one dimension sits
  at that max and every other dimension is ≤ 2 — then headline = max − 1
  (an isolated spike is real but does not make the whole build hard).
- **Override:** `compliance` ≥ 4 forces headline ≥ 4. You cannot engineer around a
  BAA or an authorization boundary; time is time.
- Never below the second-highest sub-dimension.

Worked check against the CONTRACTS §6 example — `{data_acquisition: 3,
integration_surface: 1, model_needs: 1, infra: 1, compliance: 1}`: max is 3, held
by one dimension, all others ≤ 2, so headline = 2. Matches the contract.

What the headline means in wall-clock terms, for the reader:

1. A weekend. One stable public source, no auth, no persistent state worth naming.
2. One to two weeks. One brittle acquisition path or one documented API; single-tenant; no regulated data.
3. Four to eight weeks. Multi-tenant state, one real integration, background jobs, or a heuristic needing evaluation.
4. One to three months before anything usable. Multiple integrations you do not control, a model needing ground truth, PHI at rest, or negotiated data access.
5. A capability, not a build. No legal path to the data yet, an authorization regime, or unsolved accuracy.

`reasoning` must name the dimension that set the headline and the rule applied.
One or two sentences. "One scraper, static host. No compliance surface." is
enough — do not write an essay, but never leave the headline unexplained.

---

## Distribution complexity — supplied by the distributor

`distribution_complexity` is produced by the **distributor agent** drawing on
`skills/marketing/`. This skill does not invent it. Required fields per
CONTRACTS §6: `grade`, `reasoning`, `primary_channel`, `secondary_channel`,
`time_to_first_25_users`, `skills_consulted`.

If the distributor has not run for this wedge, write
`"distribution_complexity": null` (CONTRACTS cross-cutting rule 1: missing fields
are `null`, never invented), say out loud that the shapes are incomplete pending
the distributor, and do not guess a channel. Fabricating "product-hunt launch,
2 weeks" is worse than a null, because a null is visibly missing and a guess reads
as research.

### Grade rubric (so you can sanity-check what comes back)

1. Twenty-five users reachable **by hand this week** from a list, community, or
   network the owner is already inside. Channel is owned.
2. Two to three weeks. One mechanical channel with no gatekeeper — existing query
   demand to template against, a directory circuit, a community whose rules permit it.
3. Four to eight weeks. Requires trust-building content, or cold outreach at a
   real reply rate, or standing you must earn in a community first.
4. One to two quarters. The buyer is unreachable without introductions, partners,
   or conferences; or procurement stands between you and the user.
5. No channel exists at your current scale — needs a sales team, RFP responses,
   channel partners, or a paid-acquisition budget you do not have.

Sanity checks before accepting the distributor's block:

- `grade` must be consistent with `time_to_first_25_users`. "3 days" with a grade
  of 4 is one of the two fields being wrong.
- The channel must reach the wedge's `axes.who_first` and the card's
  `wtp.buyer_class`, not the loudest complainer. A channel that reaches sufferers
  who cannot buy is a grade 4 dressed as a 2.
- `skills_consulted` entries must be **real directory names** under
  `skills/marketing/`, and so must `primary_channel` / `secondary_channel`
  whenever they are named after one (CONTRACTS §6 uses `programmatic-seo` and
  `community-marketing`). Verify before writing: `ls skills/marketing`.
  Naming a skill that does not exist makes the whole block unfalsifiable.
  Names that exist in the vendored tree and are commonly relevant here:
  `free-tools`,
  `programmatic-seo`, `seo-audit`, `community-marketing`, `cold-email`,
  `prospecting`, `directory-submissions`, `lead-magnets`, `launch`,
  `marketing-loops`, `referrals`, `content-strategy`, `pricing`, `offers`,
  `competitor-profiling`, `customer-research`, `sales-enablement`.

---

## The two grades are never blended

**Report side by side. Never average, sum, weight, or rank on a single number.**

A `2/5` technical with `5/5` distribution is a weekend build that nobody will ever
find — a distribution business. A `5/5` technical with `2/5` distribution is a year
of engineering with buyers already waiting — a capital-and-endurance business. Both
average to 3.5. The 3.5 tells the founder nothing and actively hides the only
question that matters: which kind of hard is this?

Forbidden outputs, in any medium: an `overall_complexity` field, a "difficulty
score", sorting shapes by `grade_t + grade_d`, a single star rating, a
traffic-light that fuses both. If a reader asks for one number, give them the pair
and ask which axis they are optimizing.

When shapes must be ordered for presentation, order them by the pair — for example
"lowest technical first, distribution shown" — and **print the ordering key** you
used, the same discipline as the card sort contract in CONTRACTS §4. The
quadrant framing (low/low, low-tech/high-dist, high-tech/low-dist, high/high) is a
legitimate way to present both grades at once; a mean is not.

---

## Founder fit — the discipline is refusing the flattering discount

The owner's actual stack: **Scala/Spark/Databricks**, **Azure**, **healthcare data**
(claims, EHR/HL7-adjacent, provider/NPI domain knowledge), **MCP/agent tooling**.

Rules:

1. **The raw grade and sub-dimensions never move.** They describe the build for a
   competent generalist. Fit is recorded separately.
2. Fit is claimable **only** when the shape's dominant technical risk lands
   squarely in that stack. Name the sub-dimension it touches in the note.
3. `effective_complexity_delta` ∈ `{0, -1, -2}`. `-2` requires the fit to cover
   **two** sub-dimensions, one of which set the headline. Never positive — this
   field records advantage, not penalty; a lack of fit is already the raw grade.
4. Fit **never** discounts `compliance` unless the owner has personally shipped
   under that exact regime. Healthcare-data experience is real evidence for
   HIPAA/BAA fluency. It is no evidence at all for FedRAMP, CJIS, or PCI.
5. Fit **never** touches `distribution_complexity`. Engineering skill does not
   make a buyer reachable.
6. Test: if you removed the owner and handed the shape to a strong generalist,
   would the raw grade still be right? If yes, the grade is right and the delta
   carries the person. If you find yourself wanting to lower the grade instead,
   that is the failure mode this field exists to catch.
7. `founder_fit.note` is **required even when the delta is 0** — an explicit "no
   discount, and here is why" is the record that the question was asked.

### Worked example — a fit discount that is real

Shape: `data-product-report` over multi-payer claims extracts for a provider-network
buyer. Sub-dimensions `{data_acquisition: 4, integration_surface: 2, model_needs: 1,
infra: 4, compliance: 4}` → headline **4** (max, held by three dimensions; compliance
override also forces ≥ 4).

```json
"founder_fit": {
  "note": "Fit lowers effective complexity by 1: infra (4) is Spark-class batch, which is the owner's daily work on Databricks, and data_acquisition (4) involves 837/835 claims layouts he has already parsed in production, including prior work under a BAA. Raw grade stays 4 — a strong generalist would spend a quarter learning the file formats and the PHI controls before shipping anything.",
  "effective_complexity_delta": -1
}
```

Note what it does: names the sub-dimensions, names the concrete prior experience,
states the grade is unchanged, and says what a generalist would face.

### Worked examples — correctly declining the discount

The CONTRACTS §6 example gets this right and is the pattern to copy:

```json
"founder_fit": {
  "note": "Spark/Databricks experience is irrelevant here; this is a small scraper. No fit discount applied.",
  "effective_complexity_delta": 0
}
```

Big-data expertise does not make a one-page scraper easier. The scraper's risk is
HTML brittleness and cadence, which nobody's Spark background touches.

Second decline, harder to resist:

```json
"founder_fit": {
  "note": "MCP/agent tooling fit is real for the build, but the headline grade is set by compliance (4: CJIS in scope for records data). No prior CJIS work, so no discount. Agent experience does not shorten an authorization boundary.",
  "effective_complexity_delta": 0
}
```

The pattern to police in yourself: fit in a dimension that **did not set the
headline** buys nothing. If the headline came from compliance or data access and
your fit is in infra, the delta is 0.

---

## No-inventory verification at the shape level

`skills/no-inventory-gate` clears the *cluster*. You must clear the *shape*,
because a shape can smuggle inventory back into an admissible cluster.

For **every** shape, and especially `marketplace-no-inventory`, confirm:

- No physical stock held, ever — not "just to seed supply", not "only samples".
- No warehousing, no shipping, no returns, no per-unit COGS on goods.
- Nothing bought to resell. Taking title for even a moment is inventory.
- No fulfillment obligation that lands on you rather than the counterparty.

If a shape fails any line, **delete the shape and record why in your reply** — it
is not down-graded, not flagged, not "grade 5 on infra". If deleting leaves zero
shapes, write `"shapes": []` with an explanation. Zero honest shapes is a valid,
useful result; a fulfillment business dressed as software is not.

For `marketplace-no-inventory` specifically, the `sketch` must state what flows
between the parties (a service, a slot, an appointment, information) so a reader
can verify no goods are involved without asking you.

---

## Procedure

1. Read `runs/<slug>/cards/<cluster_id>.json` and
   `runs/<slug>/wedges/<cluster_id>.json`. Confirm `inventory_gate.verdict` is
   `"pass"`. Note which panels are populated — `saturation` and `retro_trend` are
   routinely `null` when their MCPs did not load.
2. Select the single top wedge; record its `wedge_id` as the file's top-level
   `wedge_id`.
3. Route to 1–3 shapes using the signal table. A row whose card field is `null`
   does not fire; either fetch the read via the script fallback or say the row was
   unread. For each shape, note *which card fields* fired — this reasoning belongs
   in your reply to the user and, compressed, in the shape's
   `technical_complexity.reasoning` where it bears on the build.
4. Write each `sketch` against the wedge's `axes`.
5. Grade the five technical sub-dimensions, then apply the headline rule and the
   compliance override. Write `reasoning` naming the driving dimension.
6. Take `distribution_complexity` from the distributor, sanity-check it against
   the rubric, or write `null` and say it is pending.
7. Write `founder_fit.note` (always) and `effective_complexity_delta`.
8. Run the shape-level no-inventory verification; delete failures.
9. Write `runs/<slug>/shapes/<cluster_id>.json` and validate it.
10. Present shapes with both grades side by side and the ordering key stated. If a
    source was unavailable in producing any part of this (for example the
    distributor's channel research needed a source that failed), append to
    `runs/<slug>/source_health.json` per CONTRACTS cross-cutting rule 5 — silent to
    the user, recorded in the run. Never report a failed source as "no signal".

### Optional drift check

Shape sketches can quietly wander off the wedge. Comparing a sketch's distance
to the cluster's pain-evidence centroid against the wedge's own
`grounding.pain_distance` is a cheap smell test (lower = better grounded, per
CONTRACTS §5). `scripts/cluster.py` exposes `embed()` and `centroid_distance()`
for reuse — import them, do not reimplement, and run inside `uv` so the
PEP 723 dependencies resolve (CONTRACTS cross-cutting rule 4; the script's own
CLI form is `uv run scripts/cluster.py`):

```bash
# From the plugin root.
uv run --with fastembed --with numpy python - <<'PY'
import sys; sys.path.insert(0, "scripts")
from cluster import embed, centroid_distance
PY
```

Distances are only comparable **within one backend**, so embed with the same
`backend` / `embedding_model` recorded in `runs/<slug>/clusters.json` — a
different backend makes the comparison to `pain_distance` meaningless. If the
sketch is materially farther from the pain than the wedge it came from, you
drifted — rewrite the sketch, do not invent a threshold and do not report the
number as a score.

### Validation

```bash
jq -e '
  has("wedge_id") and (.wedge_id|type=="string")
  and (.shapes|type=="array") and (.shapes|length<=3)
  and (.shapes|all(
    (.shape as $s | ["concierge-manual","single-workflow-saas","data-product-report",
      "api-integration","marketplace-no-inventory","browser-extension",
      "agent-automation-service","free-tool-wedge"] | index($s) != null)
    and (.sketch|type=="string")
    and (.technical_complexity.grade|type=="number")
    and (.technical_complexity.reasoning|type=="string")
    and (.technical_complexity.dimensions
         | has("data_acquisition") and has("integration_surface")
           and has("model_needs") and has("infra") and has("compliance"))
    and (.founder_fit.note|type=="string")
    and (.founder_fit.effective_complexity_delta|type=="number")
    and (.founder_fit.effective_complexity_delta <= 0)
  ))
' runs/<slug>/shapes/<cluster_id>.json
```

Then eyeball four things `jq` cannot check: no composite score anywhere, every
`skills_consulted` name exists under `skills/marketing/`, no shape implies
fulfillment, and every value traces to a card field, a wedge field, or the
distributor — nothing estimated to fill a gap.

---

## Failure modes and gotchas

- **Padding to three shapes.** The taxonomy has eight entries; that is not a quota.
  Two of three shapes being filler makes the good one harder to see.
- **Everything becomes `single-workflow-saas`.** The default gravity of any model
  asked for an MVP. If three consecutive clusters all land there, you stopped
  reading the routing table. Check `quadrant` — `high-freq/low-intensity`
  categorically excludes it.
- **Grade inflation as prudence.** A 4 that should be a 2 reads as caution and
  costs a real opportunity, silently. Grade the build, not your anxiety.
- **Grade deflation on the exciting shape.** Same sin, opposite sign, and it is
  the one that wastes a quarter.
- **Adjusting the grade for founder fit.** The whole reason `effective_complexity_delta`
  exists. If you catch yourself typing a lower number in `grade`, stop and move it
  to the delta with a note.
- **Claiming fit on a dimension that did not set the headline.** Zero delta. Say so.
- **Fabricating the distributor's block.** `null` beats a plausible guess. Same
  for any price, timeline, competitor count, or link: `null` / `[unknown]`, never
  an estimate dressed as a finding.
- **Reading a `null` panel as good news.** An absent `saturation` block means
  `idea-reality` did not run, not that the field is clear. Fetch it
  (`uv run scripts/reality_cli.py`) or state that the row did not fire.
- **Naming a marketing skill that does not exist.** `ls skills/marketing` first.
  A wrong name is unauditable and reads as authority.
- **Writing a `wedges` array or keying the file by `wedge_id`.** CONTRACTS §6 is a
  single scalar `wedge_id` per cluster file. Downstream consumers break silently.
- **A `sketch` that is a vision statement.** If it does not name a first customer
  and a first surface, it cannot be built or falsified.
- **`free-tool-wedge` with no downstream paid thing.** That is a hobby with a
  hosting bill. Name the paid step and the handoff.
- **`marketplace-no-inventory` that seeds supply by buying it.** Inventory. Delete.
- **Shaping an `under_researched` cluster without saying so.** The unknown
  requirement is usually an integration or a compliance level, and it usually
  moves the grade two levels, not one.
- **Any composite.** If a number would let a reader skip reading the subscores,
  it does not belong in this file.
