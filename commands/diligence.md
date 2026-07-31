---
description: Stress-test ONE chosen candidate into the five-section diligence report, built from crawled competitor pages rather than recollection.
argument-hint: "[<spec path> | <pasted spec text> | \"the thing we just discussed\"] [--slug <run-slug>] [--cluster <cluster_id>] [--competitors N] [--skeptic]"
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, Task
---

# /diligence — orchestrator

`$ARGUMENTS` is the raw argument string. `$1` is the first positional token.

**Read before you do anything else, in this order:**

1. `skills/deep-diligence/SKILL.md` — **it owns the method.** The five sections, the momentum
   rubric, the three novelty calls, the coverage levels, the pricing band, the CAC/margin/payback
   model, the TAM sketch, the assumptions register, the confidence grades. This file does not
   re-specify any of them; where it names a section it means "do what the skill says."
2. `docs/CONTRACTS.md` — §8 (`diligence.md`), §1 (`inputs.json`), §4/§5/§6 (card / wedge / shape
   field names you will read), §7 (marketing context), cross-cutting rules.
3. `skills/prospect-methodology/SKILL.md` — the constitution. It wins any conflict with this file.

You are the **orchestrator**: you parse args, own the ingest, fix the slug, enforce gates, fan out
to subagents, merge their staged output, assemble `runs/<slug>/diligence.md`, and stop. You do not
crawl pages yourself, you do not profile competitors yourself, you do not generate wedges yourself.
All paths are relative to the plugin root; every artifact except `.agents/product-marketing.md`
lives under `runs/<slug>/`.

**Context rule that shapes the whole design:** raw crawled markdown never enters your context. If
you are reading a file under `runs/<slug>/competitors/raw/`, a fan-out brief was under-specified —
fix the brief, not your reading habits. Subagents return ≤12 lines each; the disk holds the rest.

---

## Stage A — parse arguments (deterministic, flags never required)

A bare invocation with just an inspiration or spec must work and freewheel. Flags are additive.

| Flag | Effect | Default |
|---|---|---|
| `--slug <run-slug>` | Target this existing run directory instead of inferring one. | inferred (Stage B) |
| `--cluster <cluster_id>` | Which card/wedge in that run the spec refers to. | top card under the printed CONTRACTS §4 sort, named out loud |
| `--competitors N` | Crawl budget, clamped to 5–10. | 8 |
| `--skeptic` | Force the skeptic even if the card's panel is already populated and cited. | conditional (Stage E) |

Everything left after removing recognised flags and their values is the **spec argument**.

**Unknown flags are surfaced, never swallowed.** Print one line before Stage B —
`unknown flag: --foo (ignored; no default applied)` — and name the likely intended flag if it is a
near-miss (`--competitor` → `--competitors`). Then continue. Never write an unknown flag into
`inputs.json`: CONTRACTS cross-cutting rule 1 forbids inventing a flag value to fill a shape.
`--competitors 3` is accepted, clamped to 5, and you say why: section 3's `thin` coverage level
requires ≥5 competitors returning `ok` feature *and* pricing pages, so a budget under 5 guarantees
`[unknown]` coverage everywhere and makes the gap analysis unassessable.

---

## Stage B — ingest (this is the part the command owns)

Mode detection, in order — first match wins:

| Condition on the spec argument | Mode |
|---|---|
| Empty | **Offer** — find the most recent candidate (below) and confirm |
| Resolves to a readable file | **File** — `Read` it |
| Contains a newline, a markdown heading, or ≳40 words | **Pasted spec** — use as-is |
| Short deictic reference — "the thing we just discussed", "that idea", "the permit one", "^ that" | **Synthesize** from the conversation |
| Short, non-deictic, not a path (a bare name or one-liner) | **Synthesize** — a one-liner is a name, not a spec |

**Empty-`$ARGUMENTS` candidate discovery:**

```bash
ls -dt runs/*/ 2>/dev/null | while read -r d; do
  ls "$d"wedges/*.json >/dev/null 2>&1 && echo "$d" && break
done
```

Take the newest run that has `wedges/*.json`, read its `wedges/<cluster_id>.json` `thesis` and the
matching `cards/<cluster_id>.json` `canonical_pain`, and offer that one candidate by name. If no run
has wedges, say so and ask for a spec or a path — do not diligence a raw `canonical_pain` with no
wedge, and do not invent a candidate to have something to run.

### The confirm-back — mandatory in Offer and Synthesize modes

This is not politeness, it is cost control. Twenty crawls, an embedding run, a wedge re-run and a
pricing band aimed at the wrong buyer costs half an hour of compute and produces a report that reads
authoritative while being about a different product. That failure is invisible in the output: every
table is well-formed, every citation resolves, and the whole thing is answering a question nobody
asked. It is also the one failure a single question prevents.

Emit exactly the skill's seven-line block — `Name / Buyer / Job / Wedge / Substrate / Price guess /
Excluded`, no prose around it — then its exact question:
`"Diligence will crawl competitors, price comparables, and model unit economics against this. Correct?"`

Then **wait for an explicit yes.** Do not proceed on silence, on "sounds good, also what about…",
on a counter-question, or on a partial correction you have not re-confirmed. A correction means you
re-emit the seven lines and ask again.

In **File** and **Pasted** modes, do not interview. If the spec is missing `Buyer` or `Wedge` —
the two fields the entire report is aimed at — ask for those two only, then proceed. Everything else
missing becomes `[unknown]` in the report, which is a finding.

If the run you are targeting has `cards/<cluster_id>.json` with `inventory_gate.verdict != "pass"`:
stop. That candidate was excluded at the gate (`skills/no-inventory-gate`), and a gate is not a
ranking penalty to be argued around three messages later.

---

## Stage C — fix the slug, write `inputs.json` (gate G1)

Per `skills/deep-diligence` Stage 0: **if the spec came from an existing `/prospect` run, reuse that
run's slug.** Forking a new slug orphans the report from `cards/`, `wedges/`, and the `frequency`
panel that section 5's TAM floor is derived from — you would then write `[unknown]` where a real 39
distinct authors is sitting on disk one directory away. Reuse is the default; `--slug` overrides;
minting a fresh slug (CONTRACTS §1: `<kebab-inspiration-truncated-40>-<YYYY-MM-DD>`) is only for a
genuinely freestanding spec.

```bash
mkdir -p runs/<slug>/{evidence,competitors/raw,competitors/.staging,tmp}
```

Write or append `inputs.json` (CONTRACTS §1). **Reusing a run: append diligence cells with fresh
`d`-prefixed `cell_id`s (`d01`, `d02`); never rewrite the prospect matrix** — those `cell_id`s are
foreign keys in every `evidence/*.jsonl` line already on disk. 1–3 cells is right for diligence; the
matrix exists so section 1's Reddit chatter has a legal `cell_id` (CONTRACTS §2 requires one).

**Gate G1** — nothing is captured or crawled before this passes:

```bash
jq -e '.slug and .inspiration and .created_utc and (.matrix|length>=1) and
       (.matrix|all(.cell_id and .persona and .vertical and .framing and (.queries|length>0)))' \
  runs/<slug>/inputs.json
```

---

## Stage D — marketing context, then the report stub (gate G2). **Before section 1.**

The vendored marketing skills used in sections 1, 4 and 5 (`competitors`, `competitor-profiling`,
`pricing`, plus everything the distributor consults) all open by reading
`.agents/product-marketing.md`. That path is a **global singleton**. Absent, they do not fail — they
silently assume a generic B2B SaaS and emit fluent, well-cited-looking analysis of nothing. Stale,
they profile and price the *previous* candidate with total confidence. Neither failure looks like a
failure in the output, which is why this is a gate and not a step.

1. Invoke `skills/marketing-context` for this candidate. It owns the overwrite dance, the versioning,
   and the 12-section format from `skills/marketing/product-marketing/SKILL.md` Step 3.
2. **If it returns readiness level 1** (no card or no wedge — the freestanding-spec case), it will
   correctly refuse to write. Do not stall and do not skip: write the context yourself from the
   confirmed seven-line spec, following `skills/marketing/product-marketing/SKILL.md` Step 3
   directly, with every evidence-derived field as the exact string `[unknown — no evidence in run]`.
   Then say plainly in your final message that the marketing tree is running on a spec-only context,
   so sections 1 and 4 will be thinner than a card-backed run.
3. Both writes are required (CONTRACTS §7). Prove it:

```bash
cmp runs/<slug>/product-marketing.md .agents/product-marketing.md && echo "context in sync"
grep -i -m1 'buyer' .agents/product-marketing.md   # must name THIS candidate's buyer
```

If `cmp` differs, a file is missing, or the buyer line names a different candidate — **stop and fix
it before section 1.** Writing only the run copy leaves the tree inert and a convincing paper trail
that the step was done.

4. Create the report stub `runs/<slug>/diligence.md`: a short header carrying the run slug, the ISO
   date, the confirmed seven-line spec in a fenced block, the active flags, and a
   `status: sections 0 of 5 complete` line. The five section headings are fixed and exact — the
   gate greps below match them literally, so do not restyle them:

   ```
   ## 1. Competition
   ## 2. Novelty
   ## 3. Proposed wedge / gap
   ## 4. Pricing potential
   ## 5. Unit economics
   ```

   **Sections are appended as they land.** A partial
   report must be unmistakably partial — a crashed run that stopped after section 2 should say so in
   its own header rather than looking like a finished report with a short back half. Update the
   `status` line every time you append a section.

---

## Stage E — probe the tools once, record every path (gate G3, dual-path rule)

Never assume a flag; the wrappers in this repo are the source of truth:

```bash
uv run scripts/crawl.py --help
uv run scripts/reality_cli.py --help
uv run scripts/reddit_search.py --help
uv run scripts/trends_cli.py --help
uv run scripts/trends_cli.py --list-sources   # supports_query per source; only 8 of 20 accept --query
```

Then probe, once each, and record. **An MCP whose tools are absent from your tool list is the
`unavailable` case, not an error** — stdio servers frequently do not load in Cowork, and `dialog`
returns `401 invalid_token` without OAuth, which is the *expected* case rather than an incident.
Never retry in a loop, never ask the user for credentials, never narrate the failure.

| Capability | Opportunistic primary | Guaranteed fallback |
|---|---|---|
| Competitor scan / trend direction | `idea-reality` MCP (`idea_check`) | `uv run scripts/reality_cli.py --idea "<spec one-liner>"` |
| Keyword trend chatter (HN/SO/PH) | `trend-pulse` MCP (`search_trends`) | `uv run scripts/trends_cli.py --source hackernews --source stackoverflow --query "<term>" --cell-id d01` |
| Google-Trends direction | — no MCP path you may rely on | `uv run scripts/gtrends_history.py --query "<term>" --window 5y` |
| Reddit chatter | `dialog` MCP | `uv run scripts/reddit_search.py --subreddits <a,b> --query "<q>" --comments --cell-id d01 --out runs/<slug>/evidence/reddit.jsonl` |
| Page content | — none exists | `uv run scripts/crawl.py --url <url>` — this *is* the path, and `--url` is required (no positionals) |

Two verified `trends_cli.py` traps, because both fail in ways that look like a quiet
answer rather than an error:

- **`--source` is repeatable, never comma-separated.** `--source google-trends,hackernews`
  is rejected as one unknown source name and the script **exits 1 with zero evidence**.
  Repeat the flag.
- **Only 8 of the 20 built-ins implement search** (`arxiv`, `bluesky`, `devto`,
  `hackernews`, `lemmy`, `producthunt`, `reddit`, `stackoverflow`). Passing `--query` to a
  trending-only source such as `google_trends`, `github`, `npm`, `pypi` or `wikipedia`
  **skips it, records `degraded`, and exits 0 with an empty result** — which is exactly the
  shape of "nobody is discussing this". Google-Trends direction comes from
  `gtrends_history.py`, not from `trends_cli.py --query`. Confirm with `--list-sources`
  (`supports_query`) before you believe an empty return.

`source_health.json` is one JSON object per line, appended (CONTRACTS cross-cutting rule 5):

```bash
printf '%s\n' '{"source":"dialog","status":"unavailable","fallback":"reddit_search.py","detail":"401"}' \
  >> runs/<slug>/source_health.json
```

**Gate G3:** one entry per probed source (`dialog`, `idea-reality`, `trend-pulse`, `crawl4ai`)
before section 1 starts. This gate exists because of the single most damaging bug this tool could
have: a rate-limited or unloaded source silently becoming "nobody is competing here," which inverts
the conclusion. Before you write **any** negative finding in **any** section, read
`source_health.json`. "No competitors found" and "no discussion found" are reserved for a source
that answered and came back empty — and even then they trigger UNDER-RESEARCHED, not a green light.

---

## Crawl discipline — you enforce this, the fan-out obeys it

Put these in every profiler brief and check them in the returns.

- **robots.txt is honored.** `crawl.py` has no `--ignore-robots` flag by design. **Do not route
  around it.** `WebFetch` and browser tools are deliberately absent from this command's
  `allowed-tools`: substituting them for a `robots-denied` or `blocked` page would make the report's
  provenance a lie and is the one crawl failure that cannot be fixed after the fact. A disallowed
  path is a row reading `robots-denied` with the path named.
- **`robots-denied` vs `blocked` is a real distinction.** `robots-denied` = we never fetched (a
  Disallow, or a `Crawl-delay` above `--max-crawl-delay`; say which). `blocked` = we asked and hit a
  wall (401/402/403/407/429, auth, paywall). The reader must be able to tell "not allowed to ask"
  from "asked and refused."
- **Per-host rate limits.** `crawl.py` serialises per host *inside one process*. So: one invocation
  per host, ≤3 invocations concurrent, **and if two competitor rows share a host — brand aliases, or
  a suite vendor's module and its parent — they are crawled in ONE invocation.** Two processes on one
  host defeats the rate limit exactly where you were being careful.
- **≤10 pages per competitor**, in priority order: pricing → features → changelog/"what's new" →
  docs → careers. Homepage last.
- **No auth walls, no paywall circumvention, no logins, no trial signups.** Pricing behind a signup
  is `[unknown - behind signup]`, which is itself a finding about their motion.
- **A degraded pricing page is never "free product."** `crawl.py` flags near-empty 200s as
  `degraded`, which means *the crawler saw nothing* — JS render, consent wall. Retry the exact URL
  **once** (`--settle 4` is the only knob for it); still degraded → `[unknown - page rendered empty]`
  and that competitor's pricing evidence grades 1/5. Reading `degraded` as "no paid tier" collapses
  the pricing floor in section 4 and has already happened in this pipeline.
- **A blocked competitor is recorded as `blocked`, never omitted.** Every non-`ok` row stays in the
  table with its status visible, so the reader can see how much of the landscape you actually saw.
  The only legal statuses are crawl.py's five — `ok` · `degraded` · `robots-denied` · `blocked` ·
  `failed` — plus `not attempted` for a competitor whose domain never resolved. Never write
  `blocked` for a fetch you did not make; that claims a request.
- **Never write crawled pages into `evidence/*.jsonl`.** The §2 `source` enum has no legal value for
  a vendor page, and inventing one breaks `cluster.py` and every downstream consumer. Reddit chatter
  from section 1 *does* belong there, append-only, with a `d`-prefixed `cell_id`.
- **Never overwrite a prior date's pull.** `raw/<competitor-slug>/<YYYY-MM-DD>/scrapes/` — a
  re-run on a new day is a new folder.

### The cardinal rule

**A price that was not crawled is `[unknown]`, never estimated.** No "roughly", no "typically
around", no remembered tier. An invented price silently poisons section 4's band and then section
5's payback arithmetic, and by the time it reaches the register nobody remembers it was a guess —
that is the worst output this command can produce, worse than fifty `[unknown]`s, worse than no
report. Unknown is information; fabrication is damage.

Mechanical audit before you append section 4 — every price you print must appear verbatim in a
crawled file:

```bash
grep -rF '<the exact price string>' runs/<slug>/competitors/raw/ | head -3
```

No hit means the number came from somewhere other than a page. Replace it with `[unknown]`.

---

## Stage F — the section pipeline: what runs in parallel, and why

### Wave 1 (parallel) — seed the competitor list

Three independent reads, none depends on another, all cheap. Run them together:

- **`idea-reality`** full scan (or `reality_cli.py --idea "<spec one-liner>" --out runs/<slug>/saturation.json`).
  Reconcile with `cards/<cluster_id>.json` → `saturation.competitor_count` / `.trend_direction` and
  **note any divergence** rather than quietly preferring the newer number.
- **Reddit seed chatter** (`dialog`, else `reddit_search.py`) on the spec's own vocabulary.
- **`cards/<cluster_id>.json` → `wtp.existing_spend[].tool`** — the highest-signal competitors in
  the whole report, because someone in the captured evidence is already paying them.

Union and dedupe to `--competitors N` rows. **Resolve every name to a domain from the evidence item
or the scan output — never from the name.** Unresolvable → domain `[unknown]`, status
`not attempted`. A competitor you could not locate is still a competitor.

### Wave 2 (parallel) — the long pole plus everything that can ride beside it

The crawl fan-out is the slowest thing in the run, so everything that does not need its output runs
alongside it:

- **N competitor profilers**, one per host, ≤3 concurrent (see brief below).
- **Name-keyed Reddit chatter** — now that you have vendor names, the queries that actually surface
  incumbents work: `"<name> alternative"`, `"<name> pricing"`, `"switched from <name>"`,
  `"<name> vs"`, `"we use <name>"`, `"replacing <name>"`. Appends to `evidence/reddit.jsonl` (or
  `evidence/dialog.jsonl`), never to a competitor file.
- **The skeptic**, *conditionally* — run it when `cards/<cluster_id>.json` has no `skeptic` panel,
  an empty or uncited one, `skeptic.under_researched: true`, when `--skeptic` is passed, or when the
  spec is freestanding and has therefore never been stress-tested. It is placed here because it is
  Reddit/GitHub-shaped and needs nothing from the crawl, and because its `structural_blockers[]`
  drive the distributor's grade floors in Wave 3 — it must land before the distributor runs.

**Staging, because parallel appends corrupt shared files.** Every Wave-2 subagent writes its own
file under `runs/<slug>/competitors/.staging/`; **you** merge. Two subagents appending to
`positioning_corpus.jsonl` or `source_health.json` produce interleaved half-lines, and you find out
twenty minutes later when the novelty helper rejects the corpus.

```bash
cat runs/<slug>/competitors/.staging/*.positioning.json  > runs/<slug>/competitors/positioning_corpus.jsonl
cat runs/<slug>/competitors/.staging/*.source_health.json >> runs/<slug>/source_health.json
```

Then write section 1 from the returned rows: the CONTRACTS-ordered table, per-competitor profiles
already on disk, and `competitors/_summary.md` whose **gaps list is the raw material for section 3**.
Zero locatable competitors → `UNDER-RESEARCHED` at the top of the section with the queries you ran
and the `source_health.json` entries, never "wide open market."

**Gate G4** before section 2 may start:

```bash
test -s runs/<slug>/competitors/_summary.md
test -s runs/<slug>/competitors/positioning_corpus.jsonl
ls runs/<slug>/competitors/*.md | grep -v _summary | head -1
grep -q '^## 1\. Competition' runs/<slug>/diligence.md
```

### Wave 3 (sequential) — novelty, then wedge, then distribution

**Both novelty and the wedge re-run need the corpus, and they are sequential, not parallel.** Two
reasons, both concrete: they would each build an incumbent centroid over the same
`positioning_corpus.jsonl`, and two helpers embedding the same corpus with different settings yield
two numbers that look comparable and are not; and the wedge re-run rewrites
`wedges/<cluster_id>.json`, which a concurrent novelty step is reading. Serialise:

1. **Section 2 — Novelty.** Write and run the throwaway helper
   `uv run runs/<slug>/tmp/novelty.py` (PEP 723, fastembed, importing `embed()` /
   `centroid_distance()` from `scripts/cluster.py` — do not add it to `scripts/`). **Assert the
   model**: `clusters.json.embedding_model` must be `BAAI/bge-small-en-v1.5` and the helper must use
   the same, or the distance is noise that looks exactly like signal. Then make one of the skill's
   three calls. Remember the override: **a crawled page beats the embedding distance**, and a
   number that disagreed with the evidence and lost is useful context — print the disagreement.
2. **Section 3 — Proposed wedge / gap.** Delegate to **wedgesmith in `/diligence` mode** with the
   corpus path. Only `incumbent_distance` is recomputed; `pain_distance` stays anchored to the pain
   evidence centroid; `wedges/<cluster_id>.prospect.json` is preserved first and both values are
   shown. Every claimed gap carries a competitor-page citation with a ≤15-word quote and a crawl
   date, or is labeled `[guess - no page evidence]` inline — uncited gaps are kept but never sit in
   the same visual register as cited ones. Record the anti-gaps too.
3. **Distribution grade.** Sequential after the wedge, because the distributor consumes the wedge
   and the shape. If `shapes/<cluster_id>.json` is absent but a wedge now exists, run
   `skills/mvp-shapes` first, then delegate to **distributor**. Its
   `distribution_complexity.primary_channel` and `.grade` are the input to section 5's CAC.

**Gate G5/G6:**

```bash
grep -q '^## 2\. Novelty' runs/<slug>/diligence.md
test -f runs/<slug>/wedges/<cluster_id>.prospect.json   # when a prospect-time wedge existed
jq -e '.wedges|all(.grounding.pain_distance != null)' runs/<slug>/wedges/<cluster_id>.json
grep -q '^## 3\.' runs/<slug>/diligence.md
```

### Wave 4 (sequential, last) — pricing, then unit economics

These are last because their inputs are everything above's outputs. **Section 4 needs the crawled
comparables** — a price is not knowable until the crawl has been graded, and running pricing early
means running it on estimates. **Section 5 needs section 4's band and the distributor's channel
grade** — CAC is not knowable until the channel is graded.

- **Section 4 — Pricing potential.** Per the skill: packaging / metric / price point, the value-metric
  test, floor and ceiling each sourced, the comparables table with a URL and date in every price
  row. If more than half the comparables are `[unknown]` or `[assumption]`, the section opens with
  **"Pricing band rests on N of M crawled comparables"** — the reader discounts it there rather than
  discovering the softness in section 5.
- **Section 5 — Unit economics.** CAC by channel with the **exact vendored skill path** naming each
  benchmark (and no attributed number where the skill contains none — fake attribution survives
  review, a naked `[assumption]` does not); gross margin including per-call model spend and
  **human-in-the-loop minutes** when `shapes[].shape` is `concierge-manual` or
  `agent-automation-service`; payback arithmetic shown inline; bottom-up TAM only, floor =
  `frequency.distinct_authors`, with the **visibility multiplier as its own prominent labeled row**
  because it is the largest lever in the section. Top-down market size is banned outright.
- **The assumptions register** closes section 5. Every `[assumption]` anywhere in sections 4–5
  appears there with the thing it moves, so the reader can override one number and see what shifts.
  Cross-check before you append:

```bash
# every labeled assumption in §4–§5 needs a register row; a label with no row means the register is wrong
grep -n '\[assumption\]' runs/<slug>/diligence.md
```

**Freestanding-spec degradations** (no card, no wedge, no shape). State these at the top of the
affected section, do not paper over them:

| Missing | Consequence |
|---|---|
| `clusters.json` / `evidence/*.jsonl` | wedgesmith **refuses** — no pain centroid, no grounding check. Section 3 is written from the confirmed spec against the crawled corpus, every gap labeled with its evidence or `[guess - no page evidence]`, and `pain_distance` is `[unknown — no pain corpus in this run]`. Do not eyeball a distance. |
| `cards/<cluster_id>.json` | Section 4's floor cannot fall back to `wtp.workaround_cost[]`; section 5's TAM floor and reach check are `[unknown]`. The skeptic has no card to merge into: take its findings into sections 3 and 5 and the confidence statement, record its effort under `"source":"skeptic:<slug>"`, and **do not fabricate a card shell to give it somewhere to write** — a card with null `frequency`/`intensity` would be picked up by every card consumer as a real card. |
| `shapes/<cluster_id>.json` (and no wedge to shape) | Distributor cannot run. Channel grade is `[unknown]`, every CAC cell is `[assumption]` with reasoning shown, and section 5 opens by saying so. |

---

## Delegation briefs

Each brief states what the subagent receives, writes, and returns. Keep returns compact — the disk
holds the artifact, your context does not.

**Competitor profiler** (general subagent, one per host, ≤3 concurrent). Receives: slug, competitor
name, resolved root domain, its ≤10 URLs in priority order, the date, and the out paths. Runs:

```bash
uv run --quiet scripts/crawl.py \
  --urls-file runs/<slug>/competitors/.staging/<cslug>.urls \
  --out runs/<slug>/competitors/raw/<cslug>/<YYYY-MM-DD>/scrapes/ \
  --manifest-out runs/<slug>/competitors/raw/<cslug>/<YYYY-MM-DD>/manifest.json
```

Writes: `competitors/<cslug>.md` in competitor-profiling's nine-section order, with every
`SEO & Content Strategy` row `[unknown - requires paid data]` (a visible row of unknowns is the
honest record that the section was skipped for lack of paid data, not overlooked);
`.staging/<cslug>.positioning.json` — one JSON object of **verbatim** headline, subheadline, pricing
tier names, and feature-page H1/H2s, their words not a summary, because paraphrase collapses exactly
the differences section 2 measures; `.staging/<cslug>.source_health.json` — crawl.py's own
`web:<host>` entries **copied verbatim**, statuses unchanged.
**Both staging files are written one JSON object per line, no pretty-printing** — you `cat` them
into `positioning_corpus.jsonl` and into JSONL `source_health.json`, and a multi-line object
turns both destinations into files that `jq` and the novelty helper reject.
Returns ≤12 lines: the section-1 table row; `names_our_buyer: y/n + url`; `names_our_job: y/n + url`
(these two decide section 2's call, which is why you never need to read the pages); momentum 1–5 from
crawled artifacts only or `[unknown]`; and any gap it saw explicitly covered, with the page URL.
Must not: bypass robots, retry a degraded page more than once, estimate a price, guess a domain, or
append to any shared file.

**wedgesmith** — `/diligence` mode. Receives slug, one `cluster_id`, and the
`positioning_corpus.jsonl` path. Returns the gate line, the wedge count, the top wedge's thesis with
**both distances as two numbers**, and caveats (`under_researched`, `incumbent_distance: null`, a
degraded embedding backend). One card per invocation.

**distributor** — after the wedge and shape exist. Receives slug and `cluster_id`. Returns per shape:
primary channel, 1–5 grade, `skills_consulted`. Never a blended difficulty number.

**skeptic** — conditional, Wave 2. Receives slug and `cluster_id`. Returns per-category counts and
the single strongest reason not to build. Its `under_researched` flag, if set, is carried visibly
into section 3 and the confidence statement: **absence of counter-evidence is suspicious, not
validating.**

---

## Stage gates — so a crashed or resumed run is never half-analyzed

On invocation, walk the gates in order and restart at the first unsatisfied one. Never re-run a
satisfied stage destructively: `evidence/*.jsonl` is append-only and deduped on `id`, and a
competitor whose `raw/<cslug>/<today>/manifest.json` already exists is not re-crawled.

| Gate | Holds when | Unlocks |
|---|---|---|
| G0 | Explicit user "yes" to the seven-line spec (Offer/Synthesize modes) | any `mkdir`, any crawl |
| G1 | `inputs.json` passes the Stage C `jq` | Stage D |
| G2 | `cmp` clean on both context copies, buyer line matches, `diligence.md` stub exists | section 1 |
| G3 | `source_health.json` has an entry per probed source | Wave 1 |
| G4 | `_summary.md`, ≥1 profile, non-empty `positioning_corpus.jsonl`, `## 1. Competition` appended | section 2 |
| G5 | `## 2. Novelty` appended, embedding model asserted | section 3 |
| G6 | `.prospect.json` preserved, `pain_distance` unchanged, `## 3.` appended | section 4 |
| G7 | `## 4.` appended with its N-of-M comparables line | section 5 |
| G8 | `## 5.` appended with the assumptions register | confidence statement |
| G9 | `grep -c '^## [1-5]\.' runs/<slug>/diligence.md` = 5 | done |

---

## Closing the report — the confidence statement

Per the skill: grade each of the five sections 1–5 on its observable criteria, **show the fraction
you counted** next to each grade (`4 — 11/13 claims cited`), and **do not average them.** Five
grades, side by side, exactly as with everything else in this plugin. Then, in prose, three things
and nothing else:

1. **The weakest link, named specifically.** Not "some data was unavailable" — name the sections that
   rest on crawled evidence versus inference, then name the single link: which competitors blocked,
   which price is missing, which assumption the conclusion turns on. A global hedge is a way of not
   saying which part is wrong.
2. **The one action that would most change the report** — usually one crawl, one quote from a real
   buyer, or one number the owner already knows.
3. **What this report does not claim.** No verdict line, no score, no build/don't-build. The owner
   reads five sections and decides.

**No composite score, anywhere, in any medium** — no "diligence score", no weighted go/no-go, no
A/B/C tier that encodes a hidden blend. If the owner asks "so is it good?", answer with the five
reads and the weakest link. If you feel the urge to summarise the report in one number, that urge is
the failure mode; write one sentence instead.

---

## Final message to the user — compact

- Path: `runs/<slug>/diligence.md`, and the `status` line confirming 5 of 5.
- The five section grades with their fractions, on one line each. Not a total.
- The named weakest link and the one action.
- Crawl ledger: `N ok / N degraded / N robots-denied / N blocked / N failed / N not attempted` —
  degraded and blocked named, because they are what bounds the report.
- Any unknown flags you surfaced, and any degradation from `source_health.json` (recorded, not
  narrated during the run).
- The override invitation: name the assumptions register and tell them that changing one row (the
  loaded hourly rate, the human-in-loop minutes, the visibility multiplier, the CAC) is how they
  re-run the arithmetic without re-running the command.
