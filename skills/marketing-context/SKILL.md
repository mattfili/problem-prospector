---
name: marketing-context
description: "Generates the product-marketing context document that activates the vendored marketing tree, writing both `runs/<slug>/product-marketing.md` and the canonical `.agents/product-marketing.md`. Applies after `wedges/<cluster_id>.json` exists for a card in `/prospect`, on spec ingest in `/diligence` before section 1, before any skill under `skills/marketing/` (copywriting, launch, marketing-plan, pricing, cold-email, …) runs on a candidate from this plugin, when the user asks to 'wire this into the marketing skills', 'generate the marketing context', or why the marketing advice is generic, and when a marketing skill starts asking foundational questions the run already answered. Do NOT use for authoring marketing copy, plans, or channel strategy (those are the vendored skills themselves); do NOT use to draft context from a repo/README with no run behind it (that is `skills/marketing/product-marketing`); do NOT use before a wedge exists, and never for a card whose `inventory_gate.verdict` is not `pass`."
---

# Marketing context generator

## Why this skill exists

Every one of the 49 vendored skills in `skills/marketing/` opens with the same instruction: read `.agents/product-marketing.md` first, and only ask the user for what it does not cover. 101 files in that tree name a `product-marketing.md` path, 56 of them the canonical `.agents/` one. It is not decoration — it is the injection point for the entire marketing section of this plugin.

Two failure modes follow, and both look like success:

1. **The file is absent.** Every marketing skill then either interrogates the user from zero or, worse, quietly assumes a generic B2B SaaS product and generates fluent, well-structured, completely subjectless advice. The output *reads fine*. Nobody notices that "your ICP" was never defined and the headline was written about nothing.
2. **The file was written to the run directory only.** `runs/<slug>/product-marketing.md` is an audit artifact. The tree does not read it. Writing it and stopping produces the exact same inert tree as (1), while leaving a convincing paper trail that the step was done. This is the single most likely thing to be silently skipped in this whole plugin.

So the contract (CONTRACTS §7) is **two writes, both required**:

| Path | Role | If missing |
|---|---|---|
| `runs/<slug>/product-marketing.md` | audit copy, tied to the run, diffable across runs | you lose the provenance of what the tree was told |
| `.agents/product-marketing.md` | **canonical copy the skills actually read** | the entire marketing tree is inert and generates generic output |

This skill exists because the pipeline has something no ordinary product-marketing interview has: real customer sentences with URLs and engagement counts. Its job is to carry those across without laundering, inventing, or polishing them.

## Decisions already taken — do not re-litigate

- The canonical path is `.agents/product-marketing.md`. Not `.claude/`, not `product-marketing-context.md`. The upstream skill still *reads* those legacy locations, which is why a stale one is a hazard (see gotchas), but you always **write** the canonical path.
- Both writes happen. Same content, byte-for-byte. Verify with `cmp`.
- The document format is not yours to design. It is `skills/marketing/product-marketing/SKILL.md` **Step 3**, exactly: 12 sections, plus the `Document version` / `Last updated` header and the `Changelog` at the bottom.
- Missing information is marked with the exact string `[unknown — no evidence in run]` (em dash). Never blank, never plausible-sounding filler.
- Every claim carried from a card cites its evidence URL inline.
- No composite score anywhere. Frequency, intensity, WTP, saturation and skeptic reads travel as separate labeled facts. There is no "opportunity strength" line in this document, ever.

## Preconditions

Generate only when a **specific candidate** exists — one cluster, one wedge, one MVP shape:

- **In `/prospect`:** after `wedges/<cluster_id>.json` is written for the card. Not before. A context built off a raw `canonical_pain` with no wedge describes a vague pain, and the tree turns a vague pain into vague advice. The wedge is what makes Product Overview and Target Audience specific.
- **In `/diligence`:** immediately on spec ingest, **before** section 1 (Competition). Sections 1–5 of `diligence.md` and the competitor crawl are better with the context loaded, and Competitive Landscape gets backfilled afterward (see the mapping table).
- **Never** for a card with `inventory_gate.verdict != "pass"`. The no-inventory filter is a gate, not a penalty. A gated card does not get marketing context, a wedge, or a shape.

If there is no card or no wedge, stop and say so. Do not write the file. Readiness level 1 below.

## Procedure

### 1. Read the template (every time)

```
Read skills/marketing/product-marketing/SKILL.md
```

Copy the fenced markdown skeleton from **Step 3** and its versioning rules from **Step 4**. Do not reconstruct it from memory or from this file — heading text is what downstream skills pattern-match, and drift breaks them silently. Verify you have all 12 sections in this order:

Product Overview · Target Audience · Personas · Problems & Pain Points · Competitive Landscape · Differentiation · Objections · Switching Dynamics · Customer Language · Brand Voice · Proof Points · Goals

Plus `**Document version:**` / `**Last updated:**` at the top and `## Changelog` at the bottom (newest first, one line per revision, `v1` on creation).

Do not add sections, rename them, or reorder them. If you have pipeline material with no home (e.g. `retro_trend.shape`), it goes inside an existing section as an annotated field value, not a new heading.

### 2. Fix the subject — exactly one candidate

Pick one `cluster_id`, one `wedge_id` from `wedges/<cluster_id>.json`, one shape from `shapes/<cluster_id>.json`. Note that the shapes file is *named* by cluster but its top-level field is `wedge_id` — take the shapes block whose `wedge_id` matches the wedge you chose.

If several cards are live, choose the top card under the currently printed sort key (CONTRACTS §4 sort contract) and name the choice out loud. **Never merge two candidates into one context document.** Two wedges averaged into one "Product Overview" produces a product that does not exist, and every downstream skill will write copy for that non-product.

### 3. Load the artifacts

```
runs/<slug>/inputs.json                  # inspiration, flags, matrix cell text
runs/<slug>/clusters.json                # member/author/community counts
runs/<slug>/cards/<cluster_id>.json      # all panels
runs/<slug>/wedges/<cluster_id>.json     # thesis, axes, incumbent_distance
runs/<slug>/shapes/<cluster_id>.json     # sketch, channel, time_to_first_25_users
runs/<slug>/evidence/*.jsonl             # verbatim text + url for Customer Language
runs/<slug>/source_health.json           # which sources were degraded
runs/<slug>/diligence.md                 # /diligence only: competitor table
```

Quotes come from evidence already captured — **this skill does not run capture.** Two exceptions, both `/diligence`-side, both key-free:

- Customer Language is thin (see readiness 2) and you need more real phrasing: probe the `dialog` MCP; on **any** failure fall back to `uv run scripts/reddit_search.py` (Arctic Shift, the guaranteed key-free path). The fallback is silent to the user but recorded: append `{"source": "dialog", "status": "unavailable", "fallback": "reddit_search.py", "detail": "401"}` to `runs/<slug>/source_health.json`. Never report a source failure as "no discussion found."
- Competitive Landscape needs names, not a count: use the `idea-reality` MCP, falling back to `uv run scripts/reality_cli.py` when the stdio server does not load (it often will not in Cowork); crawl competitor pages with `uv run scripts/crawl.py`. Record the fallback the same way. If **both** paths fail, named competitors stay `[unknown — no evidence in run]` — a degraded lookup is never a licence to name a competitor from memory.

If `source_health.json` shows a degraded source, say so inside the document — a one-line note in Customer Language such as `[dialog unavailable; quotes via Arctic Shift capture]` stops a later reader from over-reading thin coverage as "people don't talk about this."

### 4. Fill the sections from the mapping

This table is the substance of the skill. Left column is the template section; middle is the exact source field; right is the discipline.

| Template section | Source (exact field) | Rule |
|---|---|---|
| **Product Overview** | `wedges[].thesis` + chosen `shapes[].sketch`, `shapes[].shape` | One-liner is the wedge thesis, not the pain. Product category = the shelf the wedge sits on. Business model/pricing: `[unknown — no evidence in run]` pre-launch, or carry `/diligence` "Pricing potential" **keeping its `[assumption]` label** (CONTRACTS §8). Never promote an assumption to a fact by moving it into this doc. |
| **Target Audience** | `wedges[].axes.who_first`, `card.provenance.cell_ids` → `inputs.json` `matrix[].persona` / `.vertical` / `.framing`, `card.wtp.buyer_class` | `who_first` is the audience — not everyone touched by the pain. Company type/industry come from the matrix cell that produced the cluster, cited by `cell_id`. Jobs-to-be-done are phrased from evidence, not invented verbs. |
| **Personas** | `card.provenance.personas` + `card.wtp.buyer_class`, `card.intensity.markers.complainer_is_buyer` | Fill only if `buyer_class` is a B2B class (upstream: Personas are B2B-only). If `complainer_is_buyer` is `false`, that split **must** appear — user and financial buyer are different people, and copy aimed at the wrong one is the standard way this fails. |
| **Problems & Pain Points** | `card.canonical_pain`, `card.intensity.exemplars[]`, `card.intensity.markers`, `card.wtp.workaround_cost[]` | Core problem = `canonical_pain`. "What it costs them" comes from `wtp.workaround_cost[].claim` with its `url`, or from an `intensity.exemplars[]` quote backing `intensity.markers.time_quantified` — never a made-up hours/dollars figure. Emotional tension is supported by an exemplar quote, not asserted. |
| **Competitive Landscape** | `card.saturation.competitor_count` / `.trend_direction` / `.read`; in `/diligence` the crawled competitor table | Pre-diligence you usually have a count and no names: write the count with its `saturation.source` and mark named direct/secondary/indirect competitors `[unknown — no evidence in run]`. In `/diligence`, backfill named competitors with the URL each was crawled from. Do not name a competitor you have not seen a page for. |
| **Differentiation** | `wedges[].grounding.incumbent_distance` + `wedges[].axes.substrate` + `wedges[].rationale` | Differentiation is the *reasoning* behind incumbent distance, expressed in customer terms. Report the number if you use it, but the differentiator is the substrate/slice choice ("attaches to the existing portal, replaces nothing"), never "0.68 cosine distance." |
| **Objections** | `card.skeptic.failed_attempts[]`, `.churn_testimony[]`, `.structural_blockers[]`, `.steelman` | **The skeptic panel is objection research.** A `structural_blocker` ("18-month procurement cycle") is an objection you will hear in every sales call; a `failed_attempt` is the prospect saying "we tried that." Put the blocker in the Objection column and a response grounded in the wedge in the Response column. Anti-persona comes from `steelman` plus who the wedge deliberately excludes. |
| **Switching Dynamics** | Push ← `card.intensity.exemplars[]` + `card.intensity.markers.abandonment`; Pull ← `wedges[].thesis`; Habit ← `card.wtp.existing_spend[]` + `card.wtp.workaround_cost[]`; Anxiety ← `card.skeptic.churn_testimony[]` + `card.skeptic.structural_blockers[]` | Same reframe: counter-evidence is switching friction. An existing paid vendor in `wtp.existing_spend[].tool` is Habit *and* a budget line (`wtp.budget_line.attaches_to`) — say both. |
| **Customer Language** | `evidence/*.jsonl` `text` + `url`, via `card.intensity.exemplars[]` and `clusters.json` `exemplar_urls` | Highest-value section in the document. See the dedicated rules below. |
| **Brand Voice** | `wedges[].axes` tone implications only | Usually `[unknown — no evidence in run]` and that is the honest answer pre-launch. An invented voice is uniquely damaging: it is consistent, so every generated asset inherits the same wrong register and nothing looks off. If evidence shows the audience's own register (blunt, profane, jargon-heavy), you may record *that* with a citation and label it "audience register, observed" — not "our brand voice, decided." |
| **Proof Points** | `card.frequency.cluster_size` / `.distinct_authors` / `.distinct_communities` / `.engagement_weighted` / `.read`, `card.intensity.score` + `card.intensity.read`, `card.retro_trend.shape` / `.slope_pct_per_year` / `.note`, `card.wtp.existing_spend[]` | A pre-launch candidate has no product proof. Everything here is **evidence the problem is real**, and must be labeled that way: `Evidence the problem is real (not product traction): 47 posts / 39 distinct authors / 6 communities [c01]; retro-trend persistent-flat, +2.1%/yr [HN+Reddit, GitHub coverage thin]`. `Testimonials`, `Customers`, and product `Metrics` are `[unknown — no evidence in run]`. Do not average frequency, intensity and trend into one "validation" statement — they stay separate lines with separate sources. |
| **Goals** | `shapes[].distribution_complexity.time_to_first_25_users` / `.primary_channel` / `.secondary_channel`, `shapes[].sketch` | Business goal = first 25 users in the stated window via the stated primary channel. Conversion action = the one action the sketch implies. Current metrics = `[unknown — no evidence in run]` (zero users). `distribution_complexity.skills_consulted` names the vendored skill directories to hand off to — carry that list into your handoff message. |

### 5. Customer Language: carry verbatim, with links

The upstream skill states the reason plainly: "Capture exact words. Customer language beats polished descriptions." This pipeline is the only input that can actually satisfy that, because it holds real sentences with permalinks.

- **Verbatim means verbatim.** Do not fix grammar, expand abbreviations, soften profanity, or merge two people's phrasings into one cleaner line. "held together with Access and prayer" is the asset. "relies on legacy tooling" is the same sentence with the value removed. Polishing destroys the exact property that makes downstream copy resonate.
- **≤15 words per quote, each with a resolvable link** (CONTRACTS cross-cutting rule 2). Truncation is allowed; rewording is not.
- Format each as `- "[verbatim]" — [community], [ISO date], https://…` using the evidence item's `community`, `created_utc`, and `url`.
- **"How they describe us"** is `[unknown — no evidence in run]` unless the evidence contains people describing this specific wedge. It almost never does. Leave it unknown rather than paraphrasing the thesis in fake customer voice.
- **Words to use:** terms recurring across **≥3 distinct authors** (check against `clusters.json` `distinct_authors`, not raw post count — one person ranting 40 times is one voice). Cite one URL per term.
- **Words to avoid:** vendor/incumbent vocabulary the evidence shows people not using or actively mocking, with the URL that shows it. Do not editorialize a list of words you personally dislike.

### 6. Honesty pass before writing

Danger to hold in mind: this document feeds copy generators. A fabricated proof point or an invented testimonial does not stay here — it propagates into a landing page, a launch post, or a cold email that a human may actually publish under their own name. That is the worst failure this plugin can produce, and it starts in this file.

Run this pass:

1. Search your draft for empty template placeholders (`**Metrics:**` with nothing after it, empty table rows, `[quote]`, `[Competitor]`). Every one becomes `[unknown — no evidence in run]` or gets real cited content.
2. Every factual claim traceable to a card carries its evidence URL. If you cannot produce the URL, the claim comes out.
3. Zero invented: testimonials, customer names, logos, revenue or usage numbers, prices, competitor names, dates. If a source did not return it, it is unknown (CONTRACTS cross-cutting rule 1).
4. `/diligence` assumptions keep their `[assumption]` marking when carried across.
5. If `card.skeptic.under_researched` is `true`, Objections and Switching Dynamics say so explicitly: `[skeptic panel UNDER-RESEARCHED — no counter-evidence found; treat as unvalidated, not as absence of objections]`. Absence of counter-evidence is a suspicious signal, never a clean bill of health, and this document must not launder it into one.
6. No line in the document is a blended score.

### 7. Overwrite discipline and versioning

`.agents/product-marketing.md` is a **single global file.** A second run overwrites the first candidate's context, and every marketing skill silently starts generating for the new subject. Silently swapping it is how a user ends up with a launch post about candidate B under the working notes of candidate A.

Check first — `.agents/product-marketing.md`, plus `.claude/product-marketing.md` and legacy `product-marketing-context.md` in either directory (the upstream skill reads those too). Then:

| Existing state | Action |
|---|---|
| No file anywhere | `Document version: v1`; Changelog: `- v1 (YYYY-MM-DD) — Initial context for <cluster_id>/<wedge_id> from run <slug>.` |
| File for the **same** candidate (same `cluster_id` + `wedge_id`) | Increment version, update `Last updated`, prepend one Changelog line naming the sections touched and why. Never rewrite or reorder past entries. |
| File for a **different** candidate | **Tell the user out loud before writing**: name the outgoing candidate and the incoming one. Ensure the outgoing content is preserved at its own `runs/<prior-slug>/product-marketing.md` (copy it there if it is not already). Then increment the version and prepend an entry naming the switch: `- v4 (2026-07-31) — Switched context from c03/c03-w2 (run permit-status-2026-07-14) to c01/c01-w1 (run back-office-pain-small-gov-2026-07-31); all sections replaced.` |
| A legacy/`.claude` copy exists | Note it to the user and offer to move or remove it. Two contexts that disagree is worse than none — you cannot tell which one a given skill read. |

Dates in ISO form (`YYYY-MM-DD`). Typo-only fixes do not bump the version (upstream Step 4).

### 8. Write both files, then prove it

```bash
mkdir -p runs/<slug> .agents
# write runs/<slug>/product-marketing.md
# write .agents/product-marketing.md   <-- the one that actually activates the tree
cmp runs/<slug>/product-marketing.md .agents/product-marketing.md && echo "both copies in sync"
```

If `cmp` reports a difference or a missing file, the tree is not wired. Fix it before reporting done.

## Context readiness (auditable, and not a quality score)

Report one readiness level with the counts that produced it. This is a **gate label on the document**, not an assessment of the opportunity. It is never combined with `intensity.score`, `frequency.read`, `wtp.read`, or `saturation.read` — those stay separate on the card, where a reader can audit them. Counts below are of fields carrying the exact marker `[unknown — no evidence in run]`.

| Level | Observable criteria (all must hold) |
|---|---|
| **5** | All 12 sections addressed. ≥6 verbatim quotes from ≥5 distinct authors, each with a resolvable URL. ≥3 named competitors, each with a crawled URL. ≤2 `[unknown]` fields, and every one is structurally unknowable pre-launch (Testimonials, Current metrics, Brand Voice). |
| **4** | All 12 addressed. ≥4 quotes from ≥3 distinct authors with URLs. Competitive Landscape has `saturation.competitor_count` plus ≥1 named competitor with a URL. 3–5 `[unknown]` fields. |
| **3** | All 12 addressed. ≥2 quotes with URLs. Competitive Landscape is a count only, no names. 6–9 `[unknown]`. **Usable** — say plainly that Competitive Landscape, Objections and Differentiation output will be thin until `/diligence` runs. |
| **2** | Fewer than 2 verbatim quotes, **or** Customer Language empty, **or** `skeptic.under_researched == true`. Write the file, but warn before any copy-generating skill runs: the copy will be generic in exactly the places the evidence is missing. Prefer capturing more evidence first (step 3 fallbacks). |
| **1** | No card, or no wedge, or `inventory_gate.verdict != "pass"`. **Do not write the file.** You would be describing an inspiration, not a candidate. Return upstream and generate the wedge (or respect the gate). |

Assignment rule, so two readers get the same number: walk 5 → 1 and report the **first level whose criteria all hold**. If none holds, report the next level below the highest one you failed and name the criterion that failed, in the same breath as the number ("level 3 — 4 quotes and 2 named competitors would be level 4, but 7 `[unknown]` fields exceed its 3–5 band"). More than 9 `[unknown]` fields caps the document at **2** regardless of quote or competitor counts.

## Failure modes and gotchas

- **Wrote the run copy, forgot `.agents/`.** The number-one failure. The tree stays inert, downstream output looks polished and is about nothing. `cmp` both files, every time.
- **Paraphrased the template from memory.** Headings are the match surface for 101 downstream files. Re-read `skills/marketing/product-marketing/SKILL.md` Step 3 on every generation.
- **Polished the quotes.** The moment a quote becomes grammatical it stops being evidence and becomes your writing. Copy characters, not gist.
- **Presented card metrics as product traction.** "3,021 engagement-weighted mentions" proves the *problem*, not the *product*. Unlabeled in Proof Points, it will be reworded by a copy skill into "trusted by thousands." Label it, every time.
- **Merged two cards.** One document, one candidate.
- **Read `under_researched: false` as validation.** It means the skeptic looked and found counter-evidence to weigh; an *empty* skeptic panel with `under_researched: true` means nobody checked, and that is a red flag to surface, not a blank to fill with plausible objections.
- **Built context for a gated card.** No-inventory exclusions are gates. Do not resurrect one here.
- **Ran before the wedge existed** in `/prospect`. Vague pain in, generic advice out — and the tree gives no sign anything was wrong.
- **A stale `.claude/product-marketing.md` shadowing the canonical file.** Different skills may read different copies. Resolve to one.
- **Reported a source failure as silence.** If `dialog` 401s or a stdio MCP does not load, that is recorded in `source_health.json` and noted in the document. It is never "no one discusses this."
- **Slug collisions.** Two runs of the same inspiration on the same day produce the same `<slug>`. Check before overwriting the audit copy.

## Handoff message

After both writes succeed, tell the user, compactly:

1. Which candidate is loaded — `<cluster_id>/<wedge_id>`, the wedge thesis in one line, the run slug.
2. Both paths, and that `.agents/product-marketing.md` is what the marketing skills read.
3. Readiness level with its counts, and the list of `[unknown — no evidence in run]` fields they can fill by hand — Brand Voice and pricing are the two most worth their five minutes.
4. If this replaced a different candidate's context, the switch and the Changelog entry recording it.
5. Where to go next, using the exact vendored directory names, seeded from `shapes[].distribution_complexity.skills_consulted`: `product-marketing` to revise this document, then `copywriting`, `marketing-plan`, `launch`, `competitor-profiling`, `customer-research`, `offers`, `pricing`, `cold-email`, `lead-magnets`, `free-tools`, `programmatic-seo`, `community-marketing`, `marketing-psychology`, `marketing-council`. Verify any name you cite with `ls skills/marketing` before naming it.
