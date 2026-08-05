---
name: prospect-methodology
description: "Use this skill when running, resuming, auditing, or debugging the problem-prospector discovery pipeline — whenever the user invokes /prospect or /rescan, hands over a broad inspiration ('government intake is broken', 'back-office pain in small agencies'), asks to frame a permutation matrix, launch capture scouts, cluster evidence, score pain intensity, test willingness-to-pay, run counter-evidence, or produce OpportunityCards. It is the constitution: every other skill and agent defers to it for stage order, stage gates, rubric definitions, and contract field names. Load it before touching runs/<slug>/. Do NOT use it as the how-to for the sub-stages it delegates — retro-trend reconstruction belongs to skills/retro-trends, the physical-goods exclusion to skills/no-inventory-gate, wedge generation to skills/wedge-voltage, MVP shaping to skills/mvp-shapes, and post-selection deep research to skills/deep-diligence — and do NOT use it for marketing execution on an already-chosen product."
---

# Prospect methodology — the constitution

## Why this skill exists

Idea research fails in a specific, predictable way, and every rule below is a scar
from it.

The failure: an agent reads 300 Reddit posts, feels a vibe, writes "strong demand
signal," attaches an 8.4/10 opportunity score, and hands over a ranked list. Nothing
in that list can be checked. The 8.4 is a laundering operation — it takes an
unaccountable judgment, wraps it in a number, and makes disagreement feel like
innumeracy. Nobody can ask "which part of the 8.4 is wrong?" because there are no
parts. Meanwhile 280 of those 300 posts were the same complaint reworded, so the
"frequency" was one person's pain multiplied by a subreddit's phrasing diversity,
and the one post that said "we tried that, it failed for these three reasons" never
made the summary because it was inconvenient.

This pipeline exists to make that failure structurally impossible:

- **No composite scores, ever.** Subscores stay separate, all the way to the
  rendered output. Ranking happens by a mechanical, printed, re-runnable sort over
  named fields (CONTRACTS §4 Sort contract), never by a blended magic number.
- **The cluster is the unit of analysis, not the post.** Frequency means distinct
  people in distinct places, not distinct sentences.
- **Frequency and intensity are orthogonal.** A thousand mild grumbles is a content
  business. Five people who built spreadsheets to survive is a product.
- **Counter-evidence is mandatory and rides on the card.** If the skeptic can't
  find any, that is a red flag about the research, not a green flag about the idea.
- **Absence of evidence is never presented as evidence.** A source that 401'd is a
  source that failed, recorded as such — never rendered as "no discussion found."

Read `docs/CONTRACTS.md` before writing anything to disk. Every path, field name,
and enum below comes from it; if this skill and CONTRACTS.md ever disagree,
CONTRACTS.md wins and this file is the bug.

---

## Decisions already taken — do not re-litigate

1. **Key-free only.** No API keys, no OAuth, no paid data in the capture or analysis
   path. Local embeddings only (fastembed, `BAAI/bge-small-en-v1.5`). Every
   capability must be reachable through a bash script, because MCP servers do not
   always load. Do not propose "just add a Reddit API key" — that breaks the
   plugin's whole premise.
2. **MCP is opportunistic, scripts are guaranteed.** `dialog` (hosted Reddit
   research) returns 401 and needs OAuth; self-hosting it needs Reddit API keys plus
   a ChromaDB proxy key. It is a *bonus*, never a dependency. `scripts/reddit_search.py`
   (Arctic Shift) is the guaranteed Reddit path. `trend-pulse` and `idea-reality` are
   stdio servers that may not load at all in some hosts; `scripts/trends_cli.py` and
   `scripts/reality_cli.py` are their script equivalents.
3. **No-inventory is a gate, not a penalty.** Physical stock, warehousing,
   fulfillment, per-unit COGS on goods → excluded at the gate. Never "down-ranked."
4. **Scouts capture, they do not interpret.** Non-negotiable; see §3.1.
5. **Intensity is a rubric, not an impression.** The rubric in §3.3 is the rubric.
   Do not invent a seventh marker or reweight the six mid-run.
6. **Filters (`--pain`, `--wtp`) are display filters at §3.8, not capture filters.**
   Every surviving cluster gets a card on disk regardless of flags. Filtering during
   capture would destroy the frequency baseline the whole pipeline depends on.
7. **Paid competitors are positive evidence.** See §3.4. This is counterintuitive
   and agents get it backwards constantly.
8. **Deep analysis (§3.4–§3.6) is capped, capture is not.** Every gate-passing
   cluster still gets a card; only the top `3 × flags.top` by intensity/frequency
   get the expensive WTP/skeptic/trend work (§3.3b). This is a cost gate on
   analysis *depth*, not a capture filter — decision 6 above is untouched.

---

## Stage-gate discipline

Runs crash. Contexts run out. Users ctrl-C. A half-analyzed run that *looks*
finished is worse than no run, so each stage has a gate: an artifact that must
exist on disk before the next stage may begin. On resume, walk the gates in order
and restart at the first unsatisfied one. Never re-run a satisfied stage
destructively — evidence JSONL is append-only and deduped by `id`.

| # | Stage | Gate that must hold before it starts | Verify with |
|---|-------|--------------------------------------|-------------|
| 3.0 | Frame | `runs/<slug>/` exists | `mkdir -p runs/<slug>/{evidence/.staging,cards}` |
| 3.1 | Capture | `inputs.json` parses, `matrix` has 6–12 cells, every cell has `cell_id`/`persona`/`vertical`/`framing`/`queries` | `jq -e '.matrix\|length>=6 and length<=12' runs/<slug>/inputs.json` |
| 3.2 | Cluster | ≥1 `evidence/*.jsonl` with ≥40 total lines, **and** `source_health.json` has one entry per attempted source | `wc -l runs/<slug>/evidence/*.jsonl` |
| 3.3 | Freq/intensity | `clusters.json` exists with a non-empty `clusters` array | `jq -e '.clusters\|length>0' runs/<slug>/clusters.json` |
| 3.4 | WTP | every **passing** card has `frequency` and `intensity` | `P` with `select((.frequency and .intensity)\|not)` |
| 3.5 | Skeptic | every card **in the analysis pool** (§3.3b) has `wtp` | `P_analyzed` with `select(.wtp\|not)` |
| 3.6 | Retro-trend | every card in the analysis pool has `skeptic` (incl. `steelman`, `under_researched`) | `P_analyzed` with `select(.skeptic.steelman\|not)` |
| 3.7 | Inventory gate | every card in the analysis pool has `retro_trend` | `P_analyzed` with `select(.retro_trend\|not)` |
| 3.8 | Render | every card has `inventory_gate.verdict` | `jq -s -c '[.[]\|select(.inventory_gate.verdict==null)\|.cluster_id]' runs/<slug>/cards/*.json` |
| — | Wedge | `opportunity-cards.md` written and top-N selected | see §Continuation |

`P` is the card-set predicate template — it prints the failing `cluster_id`s, and `[]`
means the gate holds:

```bash
jq -s -c '[.[] | select(.inventory_gate.verdict=="pass") | <select(...)> | .cluster_id]' \
  runs/<slug>/cards/*.json
```

`P_analyzed` is `P` with one more filter, dropping capped cards the same way `P`
already drops gate-excluded ones:

```bash
jq -s -c '[.[] | select(.inventory_gate.verdict=="pass") | select(.analysis_capped|not) | <select(...)> | .cluster_id]' \
  runs/<slug>/cards/*.json
```

Three things these are doing that a one-liner does not, all of which have teeth:

- **They slurp.** `jq -e '<expr>' cards/*.json` takes its exit status from the **last**
  file's output, so eight cards missing a panel followed by one complete card exits 0 and
  the gate waves through exactly the half-analyzed run this table exists to stop. Never
  gate a card *set* with a bare `jq -e` over a glob.
- **They filter to `pass` first.** A gate-excluded card carries `null` in every analysis
  panel *by design* (§3.7), so an unfiltered check fires a false HALT the moment the
  inventory gate does its job.
- **`P_analyzed` also filters out capped cards.** A capped card carries `null` in
  `wtp`/`skeptic`/`retro_trend` *by design* too (§3.3b) — using plain `P` on stages 3.5–3.7
  would fire a false HALT on every capped card, or worse, prompt an agent to "repair" a
  card that was never supposed to be analyzed this run.

**Renderable-card predicate.** A card may not appear **in the ranked list** in
`opportunity-cards.md` until all of `frequency`, `intensity`, `quadrant`, `wtp`,
`skeptic`, `retro_trend`, `saturation`, `inventory_gate` are present. A panel with no
evidence is written as `null` with a note — it is *not* omitted, because an omitted
panel reads as "not applicable" when it actually means "we didn't look." Capped cards
never satisfy this predicate by design (§3.3b) and are not a bug when they don't —
they appear instead in their own visible section, same treatment as gate-excluded
cards (§3.8).

**Thin-capture stop.** If after §3.1 total evidence is <40 items, or fewer than
three attempted sources returned anything, **stop and report** — do not proceed to
clustering. Clustering 11 posts produces clusters of size 2 that then get rendered
with the same confident formatting as clusters of size 47. Widen the matrix, revise
queries into complainer vocabulary (§3.0), or tell the user the inspiration is too
narrow for public-signal research. Record the stop in `source_health.json`.

---

## Source-health discipline (CONTRACTS cross-cutting rule 5)

Degradation is **silent to the user, loud in the run**. Every stage that touches an
external source appends one line to `runs/<slug>/source_health.json`:

```json
{"source": "dialog", "status": "unavailable", "fallback": "reddit_search.py", "detail": "401"}
```

Rules that are not optional:

- Probe `dialog` once per run. On any failure (401, timeout, tool-not-found), fall
  back to `scripts/reddit_search.py` **without narrating the failure to the user**,
  and write the entry. Do not retry in a loop; do not ask the user for credentials.
- Same for `trend-pulse` → `scripts/trends_cli.py` and `idea-reality` →
  `scripts/reality_cli.py`.
- **A failed source is never reported as an absence of signal.** "No discussion
  found on Hacker News" is a claim about the world. "hackernews: unavailable" is a
  claim about the run. Confusing the two is how a research tool becomes a
  hallucination engine.
- The rendered `opportunity-cards.md` header carries a one-line source-health
  summary (`sources ok: reddit(script), hn, pypi · degraded: dialog(401) · failed:
  google-trends(timeout)`) so the reader can discount the frequency numbers
  appropriately.
- The skeptic also writes here (§3.5), using `"source": "skeptic:<cluster_id>"`, so
  "found no counter-evidence" is distinguishable from "did not search."

---

## 3.0 Frame — inspiration to permutation matrix

**Purpose:** turn a vague sentence into 6–12 concrete, searchable framings *before
anything is captured*, and commit them to disk so the run is auditable and
re-runnable.

**Slug.** `<kebab-inspiration-truncated-40>-<YYYY-MM-DD>` (UTC). Lowercase,
non-alphanumeric → `-`, collapse repeats, trim to 40 chars at a hyphen boundary,
append the date. Deterministic, because `/rescan` has to find the run again.

**Build the matrix along three axes:**

- **Personas** — the humans who touch the broken process. Be specific to the point
  of job title. "Government workers" is not a persona; "311 dispatcher," "permit
  office clerk," "small-city IT director," "citizen filing a FOIA request" are.
- **Verticals** — the segment/setting. Municipal call center, county records
  office, school district, state licensing board.
- **Problem framings** — what specifically breaks, in one clause: "call volume
  triage without a CRM," "status opacity between office and applicant,"
  "records-request SLA tracking in email."

Take a **spanning set, not a full cross product.** 6–12 cells that cover distinct
personas across ≥2 verticals. Two composition requirements, both there to serve
later stages:

1. **At least one cell where the persona is the buyer** (director, owner, office
   manager) and **at least one where the persona suffers but cannot buy** (frontline
   clerk, citizen). Without that contrast, §3.3's `complainer_is_buyer` marker has
   no discriminating power — everything looks the same.
2. **At least one inverted/adversarial framing** — the person on the other side of
   the counter. "Government intake is broken" from the clerk's side is workflow
   pain; from the citizen's side it is status opacity. Those are different products
   and only one of them may be buyable.

**Queries: write in the complainer's vocabulary, not the analyst's.** Searching
"workflow inefficiency in municipal permitting" returns consultants and vendor
whitepapers. Searching "permit system Access database nightmare" returns operators.
3–6 queries per cell, phrased the way a person venting at 11pm phrases it. Include
tool names, hacks, and profanity-adjacent idiom. `skills/marketing/customer-research`
has usable material on watering-hole selection and review/forum mining vocabulary —
use its research-mining sections only, and ignore the parts that assume you already
have a product.

**`--niche` handling — read this twice.** `--niche "311, permitting, records
requests"` **seeds and constrains the vertical axis; it does not replace matrix
generation.** Concretely: every named niche must appear in ≥1 cell, and generation
*continues* to fill remaining cells with adjacent verticals and personas the user
did not name. It is not a whitelist and it is not a capture filter. The common
misreading — produce exactly three cells, one per named niche — converts an
exploration tool into a confirmation tool and guarantees you only find what the user
already suspected. If the niche list is long enough to fill 12 cells on its own,
still add one un-named adjacent vertical and say so.

**Write `runs/<slug>/inputs.json` now** (CONTRACTS §1): `slug`, `inspiration`,
`created_utc`, `flags` (`wtp`, `pain`, `niche`, `cards_only`, `top` — `top` defaults
to 5), and `matrix[]` with `cell_id` (`m01`, `m02`, …), `persona`, `vertical`,
`framing`, `queries[]`, `subreddits[]`. Nothing gets captured before this file
exists — that is gate 3.1.

**Gotcha:** do not put a `--pain`/`--wtp` threshold into the queries. Those flags
filter cards at §3.8. Encoding them into search strings biases capture and silently
deletes the low-intensity baseline you need to know what high intensity even means.

---

## 3.1 Capture — parallel scouts, zero interpretation

**Purpose:** get verbatim raw material onto disk, tagged with the matrix cell that
produced it.

**Parallelism.** Launch one scout subagent per matrix cell, batched to ~4–6
concurrent. Each scout owns its cells completely and writes to
`runs/<slug>/evidence/.staging/<source>-<cell_id>.jsonl`. The orchestrator then
merges staging files into the contract path `runs/<slug>/evidence/<source>.jsonl`,
deduping on `id`. **Do not let parallel subagents append to the same file** — you
get interleaved half-lines and a JSONL file that `cluster.py` rejects, usually
discovered 20 minutes later.

**Per cell, in this order:**

1. **Reddit (always).** Probe `dialog` MCP. If it authenticates, use it for
   discovery + post/comment pulls. On any failure, fall back to
   `uv run scripts/reddit_search.py` (Arctic Shift, key-free) — silently to the
   user, recorded in `source_health.json`. Pull comments, not just post titles: the
   quantified-cost and workaround-built markers of §3.3 almost always live in
   comments, not in the OP.
2. **trend-pulse (selective).** `trend-pulse` MCP, else
   `uv run scripts/trends_cli.py`. Covers HN, Stack Overflow, Product Hunt, Google
   Trends, Wikipedia pageviews, PyPI/npm. **Only query the sources that fit the
   framing** — see the relevance table below.
3. **idea-reality (once per cell or per obvious concept).** `idea-reality` MCP, else
   `uv run scripts/reality_cli.py`. First saturation read: competitor count and
   trend direction. Store it; it lands on the card's `saturation` panel at §3.8.
   Record which path produced it in `saturation.source` (`idea-reality` when the MCP
   answered, `reality_cli.py` when the script did) — a count whose provenance is
   unrecorded cannot be re-checked.

**Source relevance — why indiscriminate spraying is worse than skipping:**

| Source | Query it when | Skip it when |
|---|---|---|
| Reddit | always | never |
| Hacker News | persona is technical, dev-adjacent, or founder-adjacent | pain lives with clerks, nurses, contractors |
| Stack Overflow | the pain has a code/API/data-format surface | the pain is procedural or organizational |
| Product Hunt | you want existing-spend / saturation evidence | you want pain language (PH is vendor copy) |
| Google Trends | the pain has a stable named search term | the pain has no shared vocabulary yet |
| Wikipedia pageviews | a named entity, statute, or standard anchors the topic | topic is generic |
| PyPI / npm | the workaround is a library; dev-tool pain | non-technical buyer |

An irrelevant source does not return zero. It returns *lexically similar noise* —
which then clusters, inflates `member_count`, and corrupts the single number the
rest of the pipeline trusts most. Skipping a source costs you nothing; spraying it
costs you the frequency signal. Record deliberate skips in `source_health.json`
with `"status": "skipped"` and a one-clause reason, so the skip is a decision on the
record rather than a gap.

**Evidence shape** (CONTRACTS §2) — one JSON object per line, append-only, never
edited by later stages: `id` (sha1 of source+url, stable across runs so `/rescan`
can diff), `cell_id`, `source` (enum: `reddit|hackernews|stackoverflow|producthunt|github|pypi|npm|wikipedia|google-trends|dialog`), `url` (real resolvable permalink
— **never constructed or guessed**), `title`, `text` (**verbatim**; truncation
allowed, rewording forbidden), `author`, `community`, `engagement`, `created_utc`,
`captured_utc`, `query` (the exact string that surfaced it). Missing fields are
`null`, never invented. **`null` engagement means "the source did not report it,"
not zero** — treating it as zero silently down-weights every source that lacks a
score field.

**Scouts capture; they do not interpret.** A scout's final message is a manifest —
counts per source, queries run, source-health entries, staging file paths — and
nothing else. If a scout returns analysis ("the strongest pain here is…"), discard
the analysis and keep the files.

Why this separation is load-bearing: a scout that pre-filters for "interesting" pain
destroys the frequency signal that §3.2 exists to measure. Frequency is only
meaningful over an unfiltered corpus. The moment a scout drops the 200 boring
restatements because they were boring, cluster weights become a record of the
scout's taste, and §3.3's 2×2 becomes undefined — you cannot identify
high-frequency/low-intensity (the content play) if low-intensity items were never
captured. The boring posts are the denominator.

---

## 3.2 Gap 1 — Dedup and clustering

```
uv run scripts/cluster.py runs/<slug>/evidence/*.jsonl > runs/<slug>/clusters.json
```

Local fastembed (`BAAI/bge-small-en-v1.5`), no keys, no network model calls.
`cluster.py` is also importable for `embed()` / `centroid_distance()` — `wedge-voltage`
uses those later; do not reimplement embedding anywhere else.

Output (CONTRACTS §3): `run_slug`, `embedding_model`, `backend`, `cut_basis`,
`clustered_utc`, `clusters[]` with `cluster_id`, `canonical`, `member_count`,
`distinct_authors`, `distinct_communities`, `engagement_sum`, `cell_ids[]`,
`exemplar_urls[]`, `member_ids[]`, plus `unclustered_ids[]`.

**THE INVIOLABLE RULE: from this file forward, the unit of analysis is the cluster,
never the raw post.** 400 phrasings of one pain are one cluster with weight 400, not
400 signals. Every count you quote downstream — in a card, in a summary, in a
sentence to the user — is a cluster-level count. Quoting "we found 400 complaints
about X" is the exact move that manufactures false consensus; it is research
theater, and it is how someone ends up building on top of one loud thread.

**The two guard fields, and what they catch:**

- `distinct_authors` catches **one person ranting 40 times.** A cluster with
  `member_count: 40, distinct_authors: 2` is two people with a grudge, not a market.
  Always report authors alongside size; if `distinct_authors / member_count < 0.4`,
  the cluster is repetition-heavy and its frequency read is demoted (§3.3).
- `distinct_communities` catches the **single-subreddit echo chamber.** One
  community's shared idiom clusters beautifully and means nothing about the wider
  world. `distinct_communities: 1` caps the frequency read at medium, permanently,
  no exceptions.

**Do not hand-merge or hand-split clusters to make a nicer story.** If the cut is
genuinely wrong, re-run with a different cut and record the new `cut_basis` — the
field exists so the reader knows which knob produced the shape they are looking at.
Manual edits leave `cut_basis` lying.

Check `unclustered_ids`. A large unclustered tail usually means the matrix framings
were too heterogeneous (each cell found its own unrelated world) or capture was
thin. It is diagnostic information, not garbage — mention it in the render header.

---

## 3.3 Gap 2 — Intensity, held strictly separate from frequency

Two independent axes per cluster. **They are never merged into one number, not here,
not on the card, not in the sort, not in prose.** "Signal strength 7/10" is banned
vocabulary.

### Axis A — Frequency (mechanical, from `clusters.json`)

Write to `frequency` (CONTRACTS §4): `cluster_size`, `distinct_authors`,
`distinct_communities`, `engagement_weighted` (= `engagement_sum`), `read`.

`read` thresholds (defaults, calibrated for a run of ~300–1500 evidence items):

- **high** — `cluster_size ≥ 20` **and** `distinct_authors ≥ 12` **and**
  `distinct_communities ≥ 3`
- **medium** — `cluster_size ≥ 8` **and** `distinct_authors ≥ 6` **and**
  `distinct_communities ≥ 2`
- **low** — anything else

Then apply corrections, in order:

1. `distinct_authors / cluster_size < 0.4` → demote one level (repetition-heavy).
   (`cluster_size` is `clusters.json`'s `member_count`, carried into `frequency`.)
2. `distinct_communities == 1` → cap at **medium** (echo chamber).
3. Engagement may promote **medium → high** only if `engagement_weighted` is in the
   top decile of this run's clusters *and* `distinct_communities ≥ 3`; when it does,
   the card note must say the promotion was engagement-driven. Engagement is a
   popularity proxy and is gameable, so it can never promote out of **low** and can
   never set the read on its own.

If total evidence is well outside the calibration range, scale thresholds
proportionally and **print the thresholds actually used** in the
`opportunity-cards.md` header. Unstated thresholds make every read
non-reproducible.

### Axis B — Intensity (rubric, 1–5)

Six markers, exactly these, from CONTRACTS §4 `intensity.markers`:

| Marker | What counts as present |
|---|---|
| `money_loss` | Named money lost, wasted, or paid to cope — fines, refunds, overtime, lost bids, consultant invoices. "It's expensive" does not count; a number, a vendor, or a named loss does. |
| `time_quantified` | A time quantity with a period: "3 hours every Monday," "two days a month," "~10 hrs/week each." Unquantified "it takes forever" does not count. |
| `workaround_built` | The complainer *constructed* something: spreadsheet, Access DB, script, Zap, shadow process, a person whose job is now the workaround. Wishing for a tool is not a workaround. |
| `abandonment` | They stopped: "we gave up and went back to paper," "we cancelled it," "we just don't track it anymore." Abandonment of a *paid* tool is the strongest form. |
| `profanity_urgency` | Profanity, all-caps, exclamation, "I'm losing my mind," "this is killing us." Weakest marker; corroborating only. |
| `complainer_is_buyer` | The speaker holds or directly influences the budget: owner, director, office manager, solo practitioner, "I approve the invoices," "I had to justify it to council." |

**Marker evidence discipline:** a marker may be set `true` only if you can cite a
verbatim exemplar ≤15 words with a resolvable URL. **No quote, no marker.** Count
markers across *distinct authors* within the cluster, never across restatements by
the same author.

**Why `complainer_is_buyer` is the highest-signal marker:** it is the only one that
connects pain to a purchase order. A frontline clerk can be in genuine agony over a
system their director is perfectly happy with — that pain is real, sympathetic, and
unsellable, because the person who feels it cannot sign anything and the person who
can sign feels nothing. Conversely, a business owner mildly annoyed by something
that costs them billable hours will pay this month. Pain that is loud but not
budget-adjacent is the single most common way this pipeline gets fooled, and this
marker is the tripwire. It also feeds §3.4's `buyer_class` — they must agree; if
they disagree, one of them is wrong, resolve it before writing the card.

**The 1–5 scale.** Score = the highest level whose criteria are fully met. Criteria
are observable and citable, so two independent runs over the same cluster land on
the same number.

- **1 — Preference.** Zero of the six markers citable. Language is "would be nice,"
  "wish it were better," "kind of annoying." Nobody has spent anything, built
  anything, or quit anything. *Not a problem, a taste.*
- **2 — Named friction.** A specific broken step is described, but the only citable
  markers are `profanity_urgency` and/or `complainer_is_buyer`. No cost, no
  workaround, no abandonment anywhere in the cluster. *People are irritated; nothing
  is being paid.*
- **3 — Cost implied or coped with.** Exactly one of {`money_loss`,
  `time_quantified`, `workaround_built`, `abandonment`} is citable, from ≥2 distinct
  authors. *Real, but the cost is tolerable enough that nobody has organized around
  it.*
- **4 — Paid pain.** ≥2 of {`money_loss`, `time_quantified`, `workaround_built`,
  `abandonment`} citable from ≥2 distinct authors each, **and** `complainer_is_buyer`
  citable at least once. *Someone with budget is already spending money, time, or
  headcount on this.*
- **5 — Bleeding.** Level 4 met, **plus** a *recurring quantified* cost (hours/week,
  dollars/month, or dedicated headcount) from ≥2 distinct authors, **plus**
  `complainer_is_buyer` from ≥2 distinct authors. Abandonment of a *paid* tool, or a
  workaround someone maintains as part of their job, both satisfy the recurring-cost
  leg. *The money is already leaving; the only question is where it goes next.*

**Caps.** If every citable marker in the cluster traces to a single author, cap the
score at **2** regardless of how vivid the writing is — one articulate sufferer is a
lead, not a market. If the cluster's markers rest entirely on `profanity_urgency`,
cap at **2**; volume of feeling is not evidence of cost.

`intensity.read`: score 4–5 → `high`, 3 → `medium`, 1–2 → `low`.

`intensity.exemplars[]`: verbatim quote **≤15 words**, `url`, and `words` = the
actual word count. Never stitch two fragments with an ellipsis to sharpen a point —
that is fabrication with punctuation. Never paraphrase into the quote field. Pick
exemplars that *demonstrate the markers you set true*, not the most quotable line.

### The 2×2 read

`quadrant` (CONTRACTS §4 enum). High-freq iff `frequency.read == "high"`;
high-intensity iff `intensity.score ≥ 4`.

- **`high-freq/high-intensity`** — real and crowded. Expect incumbents; the work is
  finding the wedge (`skills/wedge-voltage`), not proving the problem.
- **`low-freq/high-intensity`** — possible niche gold. Few voices, all bleeding.
  This is where underserved markets live. Do *not* discard for small numbers; do
  demand that §3.4 shows a real buyer, because tiny + high-intensity + no budget is
  just a sad hobby.
- **`high-freq/low-intensity`** — a content play, not a product. Lots of people
  mildly bothered means audience, SEO, newsletter — not software someone buys. Say
  this out loud on the card rather than letting the big frequency number imply a
  business.
- **`low-freq/low-intensity`** — discard. Write the card (auditability) but do not
  advance it.

A `medium` frequency read lands on the low-freq side of the 2×2 by design. The 2×2
is a triage device, not a truth claim, and the question it asks of low-freq clusters
— "is this niche gold or nothing?" — is exactly the right question for a
medium-frequency cluster. Note the boundary position on the card.

`--pain high` filters the rendered list to `intensity.score >= 4`. It does not stop
lower-intensity cards from being written to `runs/<slug>/cards/`.

---

## 3.3b Cap the analysis pool

§3.4–§3.6 are expensive: three subagent Tasks per cluster — WTP proxies, a mandatory
counter-evidence hunt, and a 3–5 year trend reconstruction against five external
scripts. Running that on every gate-passing cluster does not scale: a broad,
un-niched matrix routinely produces a dozen or more surviving clusters, and only
`flags.top` (default 5) of them will ever reach a wedge. The rest were full-priced
for zero chance of appearing in the output.

**Rank gate-passing clusters by `intensity.score` desc, then `frequency.cluster_size`
desc** — both already on disk from §3.3, free to sort by, no extra research. Take the
top `max(3 × flags.top, 9)` as the **analysis pool**. Every gate-passing cluster still
gets a card (§3.3's promise holds); only the pool's cards proceed to §3.4–§3.6.

Every gate-passing card **outside** the pool gets one additive key,
`analysis_capped: {"rank": <n>, "cap": <k>}` (CONTRACTS §4), and stops there —
`wtp`, `skeptic`, `retro_trend` stay `null`, legitimately and permanently for this
run. This is a different finding from "we searched and found nothing" (that is what
`null` inside a *completed* panel means) and a different finding from the inventory
gate's `exclude` (that is a verdict on the idea). It means *the run's cost budget went
to higher-ranked candidates first.* A capped card is not a rejected card: it is a
normal candidate again on a re-run with a wider `--top` or a smaller matrix.

`saturation` (§3.8's rider) still gets joined onto capped cards — it is a mechanical
lookup against data the scout already staged, not a research call, so there is no
cost reason to withhold it.

**Cards in the pool are the only ones §3.4–§3.6 below run on.** Read "every passing
cluster" in those sections as "every cluster in the analysis pool" — the two sets are
identical only when the cap is not reached.

---

## 3.4 Gap 3 — Willingness-to-pay proxies (all key-free)

You cannot survey anyone and there is no billing data. Everything here is a proxy
inferred from public text, and each proxy is reported separately — `wtp` has four
independent legs plus a `read`. Do not average them.

**Leg 1 — `existing_spend[]`.** Named paid tools, vendors, consultants, agencies,
contractors, or staff currently absorbing this pain: `{tool, evidence_url, note}`.

> **Paid competitors existing is POSITIVE willingness-to-pay evidence.** People get
> this exactly backwards and conclude "the space is taken, skip it." A budget line
> already exists, procurement already happened, and the category is already
> understood — that is most of the sales work done for you. What kills a wedge is a
> problem *nobody has ever paid to solve*, because you then have to prove the
> category before you sell the product. Saturation is a *separate* panel with its
> own number (`saturation.competitor_count`) and it enters the sort as its own term;
> it never gets netted against WTP.

**Leg 2 — `workaround_cost[]`.** The coping cost, quantified and cited:
`{claim, url}` — "two staff, ~10 hrs/week each," "we pay a contractor $2k/mo to
reconcile it," "one FTE whose whole job is this spreadsheet." This is the most
persuasive key-free WTP evidence that exists, because it is money already being
spent on the problem without a vendor to give credit to. Reuse the `time_quantified`
/ `workaround_built` exemplars from §3.3 — same evidence, different question (§3.3
asks *how much it hurts*, §3.4 asks *who pays*).

**Leg 3 — `buyer_class`.** One of `b2b-operator`, `prosumer`, `hobbyist`. Infer
from where the complaints live and how they are phrased, and say which signal drove
it:

- **`b2b-operator`** — speaks in staff, clients, invoices, deadlines, compliance,
  "my team." Complaints appear in trade/professional communities. Pain has a P&L
  attached.
- **`prosumer`** — individual professional spending own money at small scale;
  freelancer, solo practitioner, indie operator. Real budget, thin and
  price-sensitive.
- **`hobbyist`** — spending discretionary time; "for my personal setup," "when I get
  around to it." Free-tier gravity. Treat as WTP `low` no matter how loud.

Cross-check against §3.3's `complainer_is_buyer`. `b2b-operator` with
`complainer_is_buyer: false` everywhere is a contradiction worth naming on the card
— usually it means the operators are being *talked about*, not talking.

**Leg 4 — `budget_line` (the budget-line test).** `{attaches_to, new_category}`.
Ask literally: which existing budget category does an invoice for this land in?
"Existing permitting software line," "already-approved RPA budget," "the agency
retainer they're replacing" → `new_category: false`. If the honest answer is "there
isn't one; they'd have to create it" → `new_category: true`, **and flag it plainly
on the card.** New-category sales are materially harder: no comparable, no prior
approval, no internal owner, and the first question is "what line does this come out
of?" A brilliant new-category product loses to a mediocre one that attaches to an
existing line. Flag it; do not silently absorb it into the read.

**`wtp.read` — observable criteria:**

- **high** — `buyer_class == "b2b-operator"` **and** ≥1 cited `existing_spend` entry
  **or** ≥1 cited recurring `workaround_cost`, **and** `new_category == false`.
- **medium** — exactly one of those legs holds; or `b2b-operator` with
  `new_category: true`; or `prosumer` with cited paid spend.
- **low** — `hobbyist`; or no cited spend and no quantified workaround; or
  `new_category: true` with no identifiable budget owner.

`--wtp high` filters the rendered list to `buyer_class == "b2b-operator"` **or**
clusters with ≥1 cited `existing_spend` entry (documented spend). As with `--pain`,
it filters the render, not the disk.

`skills/marketing/pricing` is useful for framing value metrics and packaging
language if the user asks what this could charge — but it must not manufacture a
price. No number reaches a card without an evidence URL.
`skills/marketing/competitor-profiling` is the right tool when `existing_spend` names
vendors and you want structured profiles of them; that work belongs to `/diligence`,
not to this stage.

---

## 3.5 Gap 4 — Counter-evidence (MANDATORY)

For **every cluster in the analysis pool** (§3.3b), the skeptic produces four things,
with citations. This stage is not optional, not conditional on time budget, and not
skippable for "obviously real" pain. Obviously-real pain with no counter-evidence
search is exactly the profile of every idea that died in month nine.

Write to `skeptic` (CONTRACTS §4):

- **(a) `failed_attempts[]`** — `{what, why_failed, url}`. Prior products, internal
  projects, open-source attempts, municipal RFPs that went nowhere. Search for
  post-mortems, dead landing pages, abandoned repos, "we shut down X" threads. Use
  `uv run scripts/gh_history.py` for archived/abandoned repos in the space. Reddit
  searches here follow the same availability rule as §3.1: `dialog` if it
  authenticates, otherwise `uv run scripts/reddit_search.py`, recorded in
  `source_health.json` either way.
- **(b) `churn_testimony[]`** — `{quote, url}`. "We bought that and cancelled after
  six months." ≤15 words, linked. This is the single most valuable artifact in the
  whole run: someone paid, used it, and left. It tells you what the real job was.
- **(c) `structural_blockers[]`** — `{blocker, url}`. Procurement cycles, data
  access, platform dependence (a scrape one ToS change away from death), regulation,
  licensure, union rules, integration monopolies, "IT will never approve it."
- **(d) `steelman`** — the strongest honest case that **this pain persists because
  solving it is not worth paying for.** Not a strawman, not a hedge. Write the
  argument a smart, informed skeptic would actually make: the pain is real, and it
  stays unsolved because the cost of solving it exceeds what anyone will pay, or
  because each instance is bespoke, or because the sufferer is not the buyer, or
  because tolerating it is cheaper than changing. If the steelman is easy to write
  and hard to rebut, that is the finding.

**`under_researched`.** Set `true` when the skeptic ran all four searches and
produced **zero citations** across (a), (b), (c). A cluster with no findable
counter-evidence is flagged **UNDER-RESEARCHED**, not promoted. Reason: real
problems have wreckage. Somebody tried, somebody complained, somebody hit a wall. An
empty counter-evidence panel almost always means the search was too narrow, the
vocabulary was wrong, or the pain is so niche that public signal is exhausted — all
of which are reasons for *less* confidence. **Absence of counter-evidence is never
validation.** A steelman alone does not clear the flag: the steelman is reasoning,
(a)/(b)/(c) are evidence, and only evidence clears it.

Record the skeptic's search effort in `source_health.json` so the flag is
interpretable:
`{"source": "skeptic:c01", "status": "searched-no-counterevidence", "fallback": null, "detail": "queries: 'accela cancelled', 'permit software failed rfp', 'gave up on <vendor>'; sources: reddit(script), hn, github"}`

An `under_researched: true` card stays in the ranked list with the flag printed in
its header, and is **not counted toward the top-N handed to `skills/wedge-voltage`**
unless the user explicitly asks for it. Building on unexamined pain is the thing this
plugin exists to prevent.

**Skeptic findings ride on the OpportunityCard — never in a separate appendix.**
This is a placement rule with teeth. An appendix is where inconvenient findings go
to die: the reader reads six confident panels, forms a view, and then does not scroll
to section 9. Counter-evidence has to sit *between* the WTP panel and the trend
panel, in the same visual weight as everything else, so the reader cannot form a
view without it. If you find yourself writing "see Appendix: Risks," you have
reintroduced the failure mode this plugin was built to kill.

---

## 3.6 Gap 5 — Backward-facing trends

**Delegate to `skills/retro-trends/SKILL.md`.** Do not reimplement trend
reconstruction here. It owns `scripts/hn_history.py`, `scripts/reddit_history.py`,
`scripts/gtrends_history.py`, `scripts/gh_history.py`, the bucketing scheme, the
`shape` vocabulary, and the coverage caveats.

Summarize its output into `retro_trend` (CONTRACTS §4): `shape`,
`slope_pct_per_year`, `series[]` (`{source, buckets[{period, count}], coverage}`),
`note`.

**How to read the shape** (vocabulary owned by `retro-trends`; CONTRACTS shows
`persistent-flat`):

- **`persistent-flat` with no accumulating solutions** — the classic underserved
  signal, and the reason this stage looks *backward* instead of forward. A pain
  discussed at a steady rate for five years, with no growing set of tools aimed at
  it, means the problem is durable and the market has not organized around it.
  Forward-looking "is this trending?" research systematically misses these because
  flat looks boring.
- **`persistent-flat` with accumulating solutions** — steady pain, thickening
  competition. Still viable, but the wedge has to be sharp and `saturation` matters.
- **rising** — real momentum, and also everyone else's dashboard is lighting up.
  Timing risk cuts both ways.
- **spiky-episodic** — news-driven, not durable. A regulation passed, an outage
  happened, a viral thread landed. The spike is attention, not demand. Do not build
  on a spike; check whether the baseline between spikes is non-trivial, and if it
  isn't, say so.
- **declining** — the pain is being solved, or the substrate itself is dying. Either
  way you are late.

**Nuance worth one line on the card:** flat *discussion volume* is not a flat
*market*. Steady discussion over a growing installed base is a strengthening signal;
steady discussion over a shrinking one is worse than declining. Note which you
believe and why.

Honesty on coverage: if a `series[]` entry has weak coverage, `note` must say so
(CONTRACTS example: "GitHub history thin; treat slope as HN/Reddit-driven"). Do not
report `slope_pct_per_year` to two decimals off three sparse buckets and let the
precision imply confidence.

---

## 3.7 No-inventory gate

**Delegate to `skills/no-inventory-gate/SKILL.md`.** It owns the exclusion criteria
and the verdict vocabulary. Write its result to `inventory_gate`
(`{verdict, flags[]}`).

**It applies at the gate, not as a ranking penalty.** A cluster requiring physical
stock, warehousing, fulfillment, or per-unit COGS on goods is **excluded**, full
stop. It does not get a lower rank, a caveat, or a "could work if you dropship."
Rationale: down-ranking leaves it in the list, and a sufficiently exciting excluded
idea will get promoted by enthusiasm three messages later. A gate cannot be argued
with; a penalty always can.

Failed cards remain on disk with `inventory_gate.verdict` recording the failure, and
are listed in a short **"excluded at the gate"** section of `opportunity-cards.md`
with the reason — visible, unranked, not silently deleted. `flags[]` (e.g. "long
procurement cycle," "licensure-adjacent") are *not* gate failures; they are context
that travels with a passing card.

---

## 3.8 Output — OpportunityCards

One card per surviving cluster: `runs/<slug>/cards/<cluster_id>.json` (CONTRACTS
§4), rendered into `runs/<slug>/opportunity-cards.md`.

**Six panels, in this order, plus two riders:**

1. **Canonical pain** — `canonical_pain`, one sentence in the operator's own frame,
   not a product pitch. "Permit status is invisible to staff and applicants alike,"
   not "opportunity for a permit-status SaaS."
2. **Frequency** — `cluster_size`, `distinct_authors`, `distinct_communities`,
   `engagement_weighted`, `read`. Show all four numbers, always. `read` alone hides
   the echo-chamber correction.
3. **Intensity** — `score` (1–5), the six `markers` as booleans, and `exemplars[]`:
   verbatim quotes **≤15 words each, each linked**, `words` counted. Every `true`
   marker needs a quote that shows it.
4. **WTP evidence** — the four legs separately: `existing_spend`,
   `workaround_cost`, `buyer_class`, `budget_line` (with `new_category` flagged when
   true), then `read`.
5. **Skeptic findings** — `failed_attempts`, `churn_testimony`,
   `structural_blockers`, `steelman`, and the **UNDER-RESEARCHED** flag if set. Here,
   in the body, at full weight.
6. **Retro-trend** — `shape`, `slope_pct_per_year`, and a visual. ASCII sparkline
   from `series[].buckets` using `▁▂▃▄▅▆▇` scaled to the max bucket, e.g.
   `2022H1 ▃▄▄▅▄▅▆ 2025H2`. **If any series' `coverage` is not `"good"`, print a
   small table instead of a sparkline** — a smooth sparkline over sparse buckets is a
   lie told with typography. Carry the `note`.

Riders on every card: **`saturation`** (`competitor_count`, `trend_direction`,
`read`, `source` — carry `read` in the vocabulary the tool returned, or `null` if it
returned none; never coin a saturation adjective yourself, and never fold it into any
other panel's read) and **`provenance`** (`cell_ids`, `personas`) so the reader can
trace a card back to the matrix cell that found it — including "this only ever
showed up in one cell," which is itself a finding. Plus `quadrant` and
`inventory_gate`.

**Capped cards get a section too, same treatment as excluded-at-the-gate ones.**
A card carrying `analysis_capped` (§3.3b) is not renderable in the ranked list — it
never got `wtp`/`skeptic`/`retro_trend` — but it is not hidden either: list it in a
short **"not deep-analyzed (cost cap)"** section, one line each, with
`canonical_pain`, `intensity.score`, `frequency.cluster_size`, and `analysis_capped`'s
`rank`/`cap`. Visible, unranked, not silently deleted — the same rule §3.7 states for
gate-excluded cards, applied to a different reason.

### Ranking — the transparent sort

Rank by the CONTRACTS §4 Sort contract: **`intensity.score` desc → `wtp.read` desc
(high > medium > low) → `saturation.competitor_count` asc.**

- **Print the active sort key above the list, every time.** No exceptions, including
  when re-sorting on request.
- The sort must be **mechanically reproducible from the card JSONs**. A reader who
  runs it by hand gets your exact order. Quietly nudging order by judgment while
  claiming the contract sort is worse than an honest composite score, because it
  looks auditable and isn't.
- Offer re-sorts explicitly: by frequency, by `wtp.read`, by
  `saturation.competitor_count`, by `retro_trend.shape`. Re-sorting is the intended
  interaction — the user's weighting is theirs to choose, and that is precisely why
  you must not choose it for them.
- **Banned, permanently:** any blended figure — "opportunity score," weighted sums,
  averaged `read` fields, star ratings, percentage confidence, tiers named
  A/B/C that encode a hidden blend. If you feel the urge to summarize a card in one
  number, that urge is the failure mode; write one sentence instead, and let the
  panels carry the evidence.

**Render header** for `opportunity-cards.md`, before any card:

- Active sort key, verbatim.
- Counts: clusters found / cards written / excluded at the inventory gate /
  capped before analysis (§3.3b) / flagged UNDER-RESEARCHED / unclustered evidence
  items.
- Frequency thresholds actually used (see §3.3) if they were scaled.
- One-line source-health summary (ok / degraded / failed / skipped).
- Active flags from `inputs.json` (`--pain`, `--wtp`, `--niche`, `--top`) and a note
  that filters affect the display only; all cards are on disk.

---

## Continuation — wedge and MVP shaping

Unless `--cards-only`, `/prospect` continues for the top N cards (`flags.top`,
default 5):

1. **`skills/wedge-voltage/SKILL.md`** → `runs/<slug>/wedges/<cluster_id>.json`
   (CONTRACTS §5). Voltage is distance from the obvious (V1–V4); the divergence gate
   and the `pain_distance` / `incumbent_distance` grounding checks are that skill's
   business, using `cluster.py`'s `embed()` / `centroid_distance()`.
2. **`skills/mvp-shapes/SKILL.md`** → `runs/<slug>/shapes/<cluster_id>.json`
   (CONTRACTS §6). Note the rule that outlives this stage: **complexity grades are
   never silently adjusted for founder fit** — if fit lowers effective complexity,
   `founder_fit.note` says so and `effective_complexity_delta` records the amount.
   Same principle as the sort: adjustments must be visible.
3. **Marketing tree activation** (CONTRACTS §7): write
   `runs/<slug>/product-marketing.md` **and** copy it to
   `.agents/product-marketing.md`. **Both writes are required** — the audit copy
   alone does not activate the vendored tree, which reads only the `.agents/` path.
   Format follows `skills/marketing/product-marketing/SKILL.md` Step 3 (12 sections
   + `Document version` + `Changelog`). Fields with no evidence are
   `[unknown — no evidence in run]`, never fabricated; every carried claim cites its
   evidence URL.

Top-N selection: take the first N of the contract sort, **skipping
`under_researched: true` cards** (§3.5) and cards that failed the inventory gate
(§3.7). Say which cards you skipped and why — a silently shortened list looks
identical to a thin run. Capped cards (§3.3b) are not in the sort to begin with —
this is exactly why the pool is sized at `3 × flags.top`, not `flags.top`: it leaves
headroom for skipped cards without needing to fall back to an unanalyzed one. If the
pool genuinely runs dry before N is reached, say so explicitly rather than silently
shipping fewer than N.

---

## Failure modes and the discipline that prevents each

| Failure mode | What it looks like in practice | Discipline |
|---|---|---|
| Composite laundering | "Opportunity score: 8.4/10" | Subscores stay separate to the render; ranking is the printed CONTRACTS §4 sort |
| Post-counting | "400 people complained about this" | The cluster is the unit of analysis; quote cluster weight with `distinct_authors` beside it |
| One loud author | `member_count: 40, distinct_authors: 2` | `distinct_authors` ratio demotion; single-author markers cap intensity at 2 |
| Echo chamber | Whole cluster from one subreddit | `distinct_communities == 1` caps frequency at medium, permanently |
| Interpreting scouts | Scout returns "the real pain here is…" and 60 items instead of 400 | Scouts emit a manifest; discard analysis, keep files |
| Parallel append corruption | `cluster.py` chokes on a half-written JSONL line | Scouts write `evidence/.staging/<source>-<cell_id>.jsonl`; orchestrator merges and dedupes on `id` |
| Source-spraying | Google Trends queried for a pain with no shared vocabulary; noise clusters into a fake pain | Source relevance table in §3.1; record deliberate skips |
| Failure as absence | "No discussion found on HN" when HN timed out | `source_health.json` entry; never convert a failure into a finding |
| `--niche` as whitelist | Three cells, one per named niche, zero discovery | `--niche` seeds/constrains the vertical axis; generation continues |
| Filter-at-capture | `--pain high` baked into search strings | Flags filter the render at §3.8; capture stays unfiltered |
| Competitors read as negative | "Accela already exists, skip it" | Existing paid spend is positive WTP evidence; saturation is a separate panel with its own number |
| New category absorbed | `new_category: true` quietly folded into a `medium` read | Budget-line test flagged explicitly on the card |
| Appendix burial | "See Appendix: Risks" | Skeptic panel sits in the card body between WTP and retro-trend |
| Silence as validation | Empty skeptic panel treated as "no known objections" | `under_researched: true`, flagged in the card header, excluded from top-N |
| Stitched quotes | `"we lost … thousands … every month"` | Verbatim ≤15 words, single continuous span, `words` counted, linked |
| Invented precision | `slope_pct_per_year: 4.37` from three sparse buckets | Coverage stated in `note`; table instead of sparkline when coverage is weak |
| Half-analyzed run | Cards rendered with empty `skeptic` after a crash | Stage-gate table; renderable-card predicate |
| Un-capped fan-out | 14 gate-passing clusters × 3 roles = 42 Tasks to reach a 5-card output | §3.3b caps the analysis pool at `3 × flags.top`; everything else stays a card with `analysis_capped` |
| Capped card "repaired" | An agent re-runs WTP on a card that was deliberately never analyzed | `P_analyzed` excludes `analysis_capped` cards from every 3.5–3.7 gate check |
| Thin run dressed up | Confident cards over 11 evidence items | Thin-capture stop at <40 items or <3 responding sources |
| Down-ranking instead of gating | "Physical inventory, but interesting — ranked 4th" | `skills/no-inventory-gate` excludes at the gate; failures listed unranked |
