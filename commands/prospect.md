---
description: Evidence-first discovery — capture public complaints, cluster them, score intensity separately from frequency, attack survivors with a mandatory skeptic, emit OpportunityCards, then wedge the top N into MVP shapes.
argument-hint: <broad inspiration> [--wtp high] [--pain high] [--niche "<text>"] [--cards-only] [--top N]
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, Task
---

# /prospect — orchestrator

You are the **orchestrator**, not an analyst. You parse arguments, build the frame,
launch subagents, enforce stage gates, merge and reconcile artifacts, render, and
present. Every judgment this pipeline makes belongs to a subagent or a skill. If you
find yourself reading evidence and forming a view about which pain is strongest, you
have taken a stage away from the agent that owns it.

**Read first, before anything else:**

- `skills/prospect-methodology/SKILL.md` — the constitution. It owns stage order, gate
  definitions, the intensity rubric, the WTP legs, the sort, and the render header.
  **Reference its sections; do not restate them and do not re-derive them here.**
- `docs/CONTRACTS.md` — every path, field name and enum. Field-name drift is a silent
  pipeline break.

**Context hygiene, enforced throughout.** Subagents write artifacts to disk and return
short manifests. You read artifacts with `jq` when you need a value. Never ask a
subagent to return card JSON, evidence bodies, quotes, or a wedge table — the artifact
is on disk and your context is the scarce resource in a 12-cell run. If a subagent
returns analysis you did not ask for, discard the prose and keep the files (§3.1).

Throughout: `S` = the run slug, `R` = `runs/$S`.

**Shell state does not persist between Bash calls in every host.** `S`, `R`, `E` and
`PASS` below are *notation*, not exported variables. Substitute the literal slug into
every command, or re-assign the variable inside the same Bash invocation that uses it. A
command that expands `$R` to the empty string quietly reads `/inputs.json` and reports
"missing" — which looks exactly like a stage that never ran.

**Gate commands must check every card, not the last one.** `jq -e '<expr>' cards/*.json`
sets its exit status from the **last** output only, so a glob of nine cards where eight
are missing a panel still exits 0. Every gate below therefore slurps (`jq -s`) and prints
the offending `cluster_id`s; `[]` means the gate holds. Never replace one of these with a
bare `jq -e` over a glob.

---

## Stage map

| # | Stage | Runs | Parallelism | Gate that must hold first |
|---|---|---|---|---|
| 0 | Parse args | you | — | — |
| 0b | Probe MCPs | 1 probe Task | — | — |
| 1 | Frame | you | — | args parsed |
| 2 | Capture | `scout` × cells | **parallel, 4–6 concurrent** | `inputs.json` valid, 6–12 cells |
| 2b | Merge staging | you | — | all scouts returned |
| 3 | Distill | `distiller` × 1 | serial | `evidence/*.jsonl` ≥40 lines |
| 4 | Analyze | `economist` + `skeptic` + `historian` × passing clusters | **parallel, widest fan-out** | `clusters.json` non-empty, cards have `frequency`+`intensity` |
| 4b | Reconcile panels | you | — | analyze wave returned |
| 5 | Saturation | you | — | all three panels present |
| 6 | Render | you | — | every **passing** card has all eight panels |
| 7 | Wedge → shape → context → distribute | 4 Tasks per card | **sequential within a card, sequential across cards** | `opportunity-cards.md` written |

Stage 2 is parallel because a dozen cells run serially is unusable. Stage 4 is the
widest fan-out (3 × passing clusters) and the one place where the run actually spends
its wall clock. Stage 7 is sequential in both dimensions, for reasons given there.

---

## Stage 0 — parse `$ARGUMENTS`

`$ARGUMENTS` is the whole argument string. **Flags are additive and never required.**
A bare `/prospect "something about how small clinics handle referrals"` must work and
freewheel: no niche, no filters, `top=5`, full pipeline through MVP shapes. That is the
intended default invocation, not a degraded one — do not prompt for a niche, a vertical,
or a persona list to "make the run better."

| Token | Parse | Default |
|---|---|---|
| everything not a flag or a flag value | `inspiration` (join with spaces, strip quotes) | required |
| `--pain high` | `flags.pain = "high"` | `null` |
| `--wtp high` | `flags.wtp = "high"` | `null` |
| `--niche "<free text>"` | `flags.niche = "<free text>"` | `null` |
| `--cards-only` | `flags.cards_only = true` | `false` |
| `--top N` | `flags.top = N` (integer ≥1) | `5` |

- `--pain` / `--wtp` accept `high` only, and bare `--pain` means `--pain high`. Any other
  value: say `--pain low is not a supported value; ignoring it` and continue unfiltered.
  These are **display filters applied at stage 6** (§3.8) — never capture filters, never
  words in a query string.
- `--niche` is quoted free text, comma-separated or prose. See Stage 1 for what it does
  and does not do.
- **Unknown flags are surfaced, never swallowed.** Print one line —
  `unrecognized flag: --depth (ignored)` — and remove the token from the inspiration
  string so it does not contaminate the slug or the queries. Do not halt.
- Empty `$ARGUMENTS`: ask for an inspiration in one sentence. **This is the only question
  this command ever asks the user.**

Derive the slug (§3.0 — deterministic, because `/rescan` has to find the run again):

```bash
S=$(python3 -c '
import re, sys, datetime
s = re.sub(r"[^a-z0-9]+", "-", sys.argv[1].lower()).strip("-")
if len(s) > 40:
    head = s[:40]
    s = head.rsplit("-", 1)[0] if "-" in head else head
print(s.strip("-") + "-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"))
' "<inspiration>")
R=runs/$S
mkdir -p $R/evidence/.staging $R/cards
```

If `$R/inputs.json` already exists, **this is a resume, not a fresh run.** Walk the gate
table in "Resuming" below and restart at the first unsatisfied gate. Never re-run a
satisfied stage destructively; evidence JSONL is append-only.

---

## Stage 0b — probe the MCPs once, up front

Three MCP-dependent capabilities, each with a guaranteed script fallback (CONTRACTS
appendix). **Never write a step that only works if an MCP is present.**

| Capability | Opportunistic primary | Guaranteed fallback |
|---|---|---|
| Reddit discovery + comment pulls | `dialog` (hosted HTTP; **401s unauthenticated**) | `uv run scripts/reddit_search.py` |
| Multi-source trends (HN, SO, PH, GTrends, Wikipedia, PyPI/npm) | `trend-pulse` | `uv run scripts/trends_cli.py` |
| Saturation / competitor count | `idea-reality` | `uv run scripts/reality_cli.py` |

Probe **once per run, before any scout launches.** Twelve scouts each rediscovering that
`dialog` 401s is twelve wasted round trips and twelve chances to narrate a failure.

Delegate one short probe:

> `Task(subagent_type: "general-purpose")` — "Probe MCP availability for a
> problem-prospector run. Check whether tools matching `mcp__dialog__*`,
> `mcp__trend-pulse__*`, `mcp__idea-reality__*` exist (one `ToolSearch` query total). For
> each server that is present, make exactly one cheapest call to confirm it answers
> (`list_sources` for trend-pulse; a one-line `idea_check` for idea-reality; any dialog
> tool). Do not retry, do not ask for credentials, do not narrate. Return exactly three
> lines of the form `<server>: available|unavailable — <detail>` and nothing else."

Write one CONTRACTS cross-cutting-rule-5 line per server, appending (never
read-modify-write — parallel agents append here too):

```bash
printf '%s\n' '{"source":"dialog","status":"unavailable","fallback":"reddit_search.py","detail":"401 invalid_token"}' >> $R/source_health.json
```

Carry the resolved path for each capability into **every** scout prompt as a three-line
table. The probe is an optimization, not a contract: a scout that finds its resolved path
failing anyway falls back and records its own health line.

**Degradation is silent to the user and loud in the run.** Do not tell Matt that `dialog`
needs OAuth. Do not offer to authenticate it. The health file is where that lives.

---

## Stage 1 — Frame

Build the permutation matrix per **§3.0** — personas × verticals × problem framings,
6–12 cells, spanning set not cross product, queries in the complainer's vocabulary. The
two composition requirements (≥1 buyer persona **and** ≥1 sufferer-who-cannot-buy; ≥1
inverted/adversarial framing) are load-bearing for §3.3's `complainer_is_buyer` marker —
without the contrast the marker has no discriminating power.

**`--niche` constrains and extends the vertical axis; it does not replace generation.**
The misreading to guard against, because it is the natural one: three named niches →
three cells, one each, done. That converts an exploration tool into a confirmation tool
and guarantees you only find what Matt already suspected. Concretely: every named niche
appears in ≥1 cell, **and** generation continues, filling the remaining cells with
adjacent verticals and personas he did not name. If the niche list alone would fill 12
cells, still add one un-named adjacent vertical and say so in your frame summary.

Do not encode `--pain` / `--wtp` into any query string (§3.0 gotcha).

Write `$R/inputs.json` (CONTRACTS §1) **before any capture**: `slug`, `inspiration`,
`created_utc`, `flags` (`wtp`, `pain`, `niche`, `cards_only`, `top`), `matrix[]` with
`cell_id` (`m01`, `m02`, …), `persona`, `vertical`, `framing`, `queries[]` (3–6),
`subreddits[]`.

**Gate out of Stage 1 — capture may not start until this passes:**

```bash
jq -e '.matrix|length>=6 and length<=12' $R/inputs.json >/dev/null \
  && jq -e '(.matrix|map(select(.cell_id and .persona and .vertical and .framing and (.queries|length>0)))|length) == (.matrix|length)' $R/inputs.json >/dev/null \
  || echo "HALT: inputs.json invalid — fix the matrix before capture"
```

A failing gate **halts with that message.** Do not launch scouts against a half-written
frame.

Tell Matt the frame in ~6 lines: cell count, the persona/vertical spread, which cells
came from `--niche` and which are the un-named adjacents. Then keep going — this is not
a checkpoint for approval.

---

## Stage 2 — Capture (parallel scouts, one Task per matrix cell)

**Launch one `scout` Task per cell, batched 4–6 concurrent.** To actually get parallelism,
issue the batch's Task calls **in a single message**; sequential messages run serially and
a 12-cell run becomes unusable. Two to three waves at 5 concurrent covers 6–12 cells.

The batch cap is a rate-limit decision, not a politeness gesture: Arctic Shift wants
≥1.2s/req and GitHub allows 10 req/min **per IP, shared across every concurrent process**.
Pass the wave's concurrency into each scout so it can set `--politeness`.

Each scout Task gets exactly:

1. `slug`
2. **one `matrix` cell, verbatim** from `inputs.json` (`cell_id`, `persona`, `vertical`,
   `framing`, `queries[]`, `subreddits[]`)
3. the resolved-path table from Stage 0b
4. the concurrency hint (how many scouts are in this wave)

The scout owns the source-relevance decisions in §3.1 (Reddit always; HN/SO/PH/GTrends/
Wikipedia/PyPI/npm only where the framing fits, deliberate skips recorded). It writes to
`$R/evidence/.staging/` and returns a manifest. **Discard any analysis it returns.**

### Stage 2b — merge staging (you, after every wave has returned)

Scouts never write the contract paths; parallel appends to one file produce interleaved
half-lines that `cluster.py` rejects, discovered twenty minutes later. You merge, deduping
on `id`. This is idempotent and safe to re-run:

```bash
E=$R/evidence
for src in $(ls $E/.staging/*.jsonl 2>/dev/null | xargs -n1 basename \
             | sed -E 's/-[a-z][0-9]+\.jsonl$//' | grep -v '^health$' | sort -u); do
  cat $E/.staging/$src-*.jsonl $E/$src.jsonl 2>/dev/null \
    | jq -Rc 'fromjson? | select(type=="object" and .id)' \
    | jq -sc 'unique_by(.id)[]' > $E/$src.jsonl.tmp && mv $E/$src.jsonl.tmp $E/$src.jsonl
done
raw=$(cat $E/.staging/health-*.jsonl 2>/dev/null | wc -l | tr -d ' ')
cat $E/.staging/health-*.jsonl 2>/dev/null | jq -Rc 'fromjson?' >> $R/source_health.json
parsed=$(cat $E/.staging/health-*.jsonl 2>/dev/null | jq -Rc 'fromjson?' | wc -l | tr -d ' ')
if [ "$raw" != "$parsed" ]; then
  printf '%s\n' "{\"source\":\"source_health-merge\",\"status\":\"degraded\",\"fallback\":null,\"detail\":\"$((raw - parsed)) of $raw staged health line(s) dropped as malformed/truncated during merge\"}" >> $R/source_health.json
fi
```

`fromjson?` drops any truncated line instead of failing the whole merge — the
`raw`/`parsed` check above exists so that drop is never silent: a line lost
here is a lost degradation record, the one thing this file exists to
preserve. The `sed` strips a
`-<cell_id>` suffix of any prefix letter (`-m03`, and `/diligence`'s `-d01`), so `<source>`
is what remains: it must be an exact CONTRACTS §2 enum value (`google-trends`, never
`google_trends`) — if a prefix is off-enum, fix the filename, do not invent a destination.
`evidence/reddit-m03.jsonl` is not a contract path and nothing downstream reads it. **Never delete `.staging/`**: those files cost rate limit, and both scripts
dedupe against their own `--out`, so a retried scout is safe. Leave
`.staging/saturation-<cell_id>.json` alone — Stage 5 reads it.

### Thin-capture stop (§3.1) — check before you go further

```bash
wc -l $R/evidence/*.jsonl                                          # total items
cat $R/evidence/*.jsonl | jq -Rr 'fromjson? | .source' | sort -u   # sources that returned anything
jq -Rr 'fromjson? | select(.status!="ok") | "\(.source)\t\(.status)\t\(.detail)"' $R/source_health.json
```

If total evidence is **<40 items**, or **fewer than three attempted sources returned
anything**, stop and say so. Do not cluster. Clustering 11 posts yields clusters of size 2
rendered with the same confident formatting as clusters of size 47. Record the stop:

```bash
printf '%s\n' '{"source":"capture","status":"stopped","fallback":null,"detail":"thin-capture gate: 31 items across 2 responding sources; needs a wider matrix or complainer-vocabulary queries"}' >> $R/source_health.json
```

Then report the counts, name which sources failed vs. returned zero (they are different
findings), and suggest widening the matrix or revising queries into complainer vocabulary.
**A source that failed is never reported as "no discussion found."**

---

## Stage 3 — Distill

**Gate in:** `evidence/*.jsonl` exists with ≥40 total lines, and `source_health.json` has
one entry per attempted source. Missing → halt with the message above.

One `distiller` Task. Hand it `slug` and nothing else; it reads the rest off disk. It runs
`scripts/cluster.py`, derives the mechanical frequency panel, scores intensity 1–5 against
the §3.3 rubric with cited ≤15-word exemplars, applies `skills/no-inventory-gate`, and
writes `clusters.json` plus one initial `cards/<cluster_id>.json` per cluster —
`frequency`, `intensity`, `quadrant`, `inventory_gate` filled; `wtp`, `skeptic`,
`retro_trend`, `saturation` left `null`.

**Gate out:**

```bash
jq -e '.clusters|length>0' $R/clusters.json >/dev/null || echo "HALT: no clusters"
# one card per cluster, gate-excluded ones included — a mismatch means the distiller stopped early
echo "clusters=$(jq '.clusters|length' $R/clusters.json) cards=$(ls $R/cards/*.json 2>/dev/null | wc -l)"
# every card carries a verdict; every PASSING card carries the three distiller panels.
# Excluded cards legitimately hold null intensity/quadrant (§3.7) and are exempt.
# Both print [] when the gate holds — a non-empty list names the offenders.
jq -s -c '[.[] | select(.inventory_gate.verdict == null) | .cluster_id]' $R/cards/*.json
jq -s -c '[.[] | select(.inventory_gate.verdict == "pass")
  | select((.frequency and .intensity and .quadrant) | not) | .cluster_id]' $R/cards/*.json
```

Either list non-empty → **HALT** naming those `cluster_id`s. Do **not** substitute
`jq -e '<expr>' $R/cards/*.json`: over a glob, `-e` takes its exit status from the last
file only, so eight broken cards followed by one good one exits 0.

If the distiller reports a large `unclustered_ids` tail, carry that number forward to the
render header — it is diagnostic (heterogeneous matrix or thin capture), not garbage.

If the cut looks wrong, the correct move is re-clustering at a different `--percentile`
via the distiller, which records the new `cut_basis`. **Never hand-merge or hand-split
clusters** — that leaves `cut_basis` lying about what produced the shape.

---

## Stage 4 — Analyze (economist + skeptic + historian, in parallel, per cluster)

**This is the widest fan-out in the run: 3 × passing clusters.** Nine clusters is 27
Tasks. Fan out over cards where `inventory_gate.verdict == "pass"` only — the gate is a
gate, and an excluded card keeps its `null` panels on disk (§3.7):

```bash
PASS=$(jq -r 'select(.inventory_gate.verdict=="pass")|.cluster_id' $R/cards/*.json)
```

Each Task receives `slug` + one `cluster_id`. Nothing else; they read disk.

- **`economist`** → fills `wtp` (§3.4: `existing_spend`, `workaround_cost`, `buyer_class`,
  `budget_line`, `read`).
- **`skeptic`** → fills `skeptic` (§3.5: `failed_attempts`, `churn_testimony`,
  `structural_blockers`, `steelman`, `under_researched`). **Mandatory. Never skipped for
  time budget or for "obviously real" pain.**
- **`historian`** → fills `retro_trend` (§3.6, delegating to `skills/retro-trends`).

**The historian is the slow one, and it is slow on purpose.** `gh_history.py` paces itself
at ~6.5s/request against GitHub's unauthenticated 10 req/min ceiling; two terms over five
years is ~80s, three terms ~2 minutes. Expect the minutes and do not kill it. That ceiling
is **per IP, not per process** — N parallel historians hitting GitHub multiply the rate and
403 each other into a `coverage: none` series that then reads as "no repos accumulating,"
which is the underserved signal inverted into a fabrication. So: **cap concurrent
historians at 2**, and let the economists and skeptics for the remaining clusters fill the
wave. Never lower `--pace`, never add a token.

Add one line to each of the three Task prompts:

> "Write your panel fragment to disk **before** merging it into the card
> (`cards/.staging/<cid>.wtp.json` / `cards/.panels/<cid>.skeptic.json` /
> `trends/<cid>-retro_trend.json`). Patch the single key with jq; never `Write` the whole
> card. If your merge races another agent, the fragment on disk is authoritative."

### Stage 4b — reconcile (you, after the wave)

Three agents read-modify-write one card file. jq-patch races are rare but real, and a lost
update looks exactly like an agent that silently skipped its stage. Check, then repair
from the fragments — do not re-run an agent whose fragment is already on disk:

```bash
# name the missing panel, per passing card, in one pass. No output = the wave landed clean.
jq -s -r '.[] | select(.inventory_gate.verdict == "pass")
  | . as $c
  | [ (if $c.wtp then empty else "wtp" end),
      (if $c.skeptic.steelman then empty else "skeptic" end),
      (if $c.retro_trend then empty else "retro_trend" end) ] as $missing
  | select($missing | length > 0)
  | "MISSING PANEL: \($c.cluster_id) -> \($missing | join(", "))"' $R/cards/*.json
```

For each missing panel, re-merge from its fragment path (`cards/.staging/<cid>.wtp.json`,
`cards/.panels/<cid>.skeptic.json`, `trends/<cid>-retro_trend.json`); if the fragment is
absent too, re-run that one agent for that one cluster. Re-running all three is wasteful
and will re-spend the GitHub rate limit.

**Gate out:** the command above prints nothing. That single slurped pass *is* the gate —
a per-file `jq -e` loop is only correct if you genuinely iterate, and a `jq -e` over the
glob is not (it reports on the last card only).

---

## Stage 5 — Saturation (you)

`saturation` is the one panel no analysis agent owns: it comes from the capture-time
`idea-reality` read, staged by the scouts at `$R/evidence/.staging/saturation-<cell_id>.json`
(§3.1 step 3, §3.8 rider). Join it to each card through `provenance.cell_ids`.

- Carry `competitor_count`, `trend_direction`, and `read` **in the vocabulary the tool
  returned**. Never coin a saturation adjective yourself. `reality_cli.py`'s payload already
  contains a drop-in `.saturation` block with exactly the four §4 keys (`source`,
  `competitor_count`, `trend_direction`, `read`) — splice it whole rather than rebuilding it
  field by field, which is where a coined adjective gets in.
- `source` records which path answered: `idea-reality` (MCP) or `reality_cli.py` (script).
  A count whose provenance is unrecorded cannot be re-checked.
- If a cluster's cells carry more than one read, take the read from the cell contributing
  the most members and note the disagreement in one line of the render header. **Never
  average competitor counts** — that is a blend, and blends are banned.
- If no cell has a saturation read, write `"saturation": null` and append a health line.
  **`competitor_count: 0` is a claim that nobody is building here.** Writing it because the
  lookup failed is the failure-as-absence bug in its purest form.

Saturation never gets netted against WTP (§3.4). It is a separate panel with its own
number and its own place in the sort.

---

## Stage 6 — Render OpportunityCards

**Gate in — the renderable-card predicate.** A card may not appear **in the ranked list**
until all of `frequency`, `intensity`, `quadrant`, `wtp`, `skeptic`, `retro_trend`,
`saturation`, `inventory_gate` are **present** (a panel with no evidence is `null` *with a
note*, not omitted — an omitted panel reads as "not applicable" when it means "we didn't
look"). Gate-excluded cards are exempt: they legitimately carry `null` analysis panels and
appear only in the "excluded at the gate" section below.

```bash
# prints the cluster_ids that are NOT renderable; [] means the gate holds
jq -s -c '[.[] | select(.inventory_gate.verdict=="pass")
  | select((.frequency and .intensity and .quadrant and .wtp and .skeptic
            and .retro_trend and has("saturation")) | not) | .cluster_id]' $R/cards/*.json
```

Non-empty → **halt** and go back to Stage 4b for exactly those clusters. Do not render a
partial card; a card rendered with an empty `skeptic` after a crash is the half-analyzed
run the gate table exists to prevent.

Render `$R/opportunity-cards.md` from the card JSONs. Six panels per card in §3.8 order —
canonical pain, frequency, intensity, WTP, **skeptic, in the body between WTP and
retro-trend**, retro-trend — plus the `saturation`, `provenance`, `quadrant` and
`inventory_gate` riders. If you catch yourself writing "see Appendix: Risks," you have
reintroduced the exact failure this plugin exists to kill.

Retro-trend visual: the historian already built it. Paste `retro_trend.render_block`
verbatim — it carries the per-source ramps, the raw counts, the coverage column, the Google
Trends relativity caveat and the two-curve read, all scaled per source. Only when
`render_block` is absent do you draw it yourself: ASCII sparkline from `series[].buckets`
scaled to the max bucket (`2022H1 ▃▄▄▅▄▅▆ 2025H2`), **but a small table instead if any
series' `coverage` is not `"good"`** — a smooth sparkline over sparse buckets is a lie told
with typography.

### The sort — printed, mechanical, reproducible

**Print the active sort key verbatim above the list, every time, including on re-sorts.**
Default (CONTRACTS §4): `intensity.score` desc → `wtp.read` desc (high > medium > low) →
`saturation.competitor_count` asc. Run it; do not eyeball it:

```bash
jq -s -r '
  def wrank: {"high":3,"medium":2,"low":1}[.wtp.read // ""] // 0;
  map(select(.inventory_gate.verdict == "pass"))
  | sort_by([ (0 - (.intensity.score // 0)), (0 - wrank), (.saturation.competitor_count // 999999) ])
  | .[] | [.cluster_id, .intensity.score, .wtp.read,
           (.saturation.competitor_count // "unknown"), .skeptic.under_researched] | @tsv
' $R/cards/*.json
```

A null `competitor_count` sorts last on purpose: an unknown count must not beat a measured
one in a tiebreak. **Never nudge the order by judgment while claiming the contract sort** —
that is worse than an honest composite, because it looks auditable and isn't. And no
blended figure anywhere: no opportunity score, no weighted sum, no averaged `read`, no
A/B/C tiers.

### Header, before any card (§3.8)

1. The active sort key, verbatim.
2. Counts: clusters found / cards written / excluded at the inventory gate / flagged
   UNDER-RESEARCHED / unclustered evidence items.
3. Frequency thresholds actually used, if §3.3's defaults were scaled for corpus size.
4. One-line source health, e.g.
   `sources ok: reddit(script), hn, pypi · degraded: dialog(401) · failed: google-trends(timeout) · skipped: npm(non-technical buyer)`.
5. Active flags from `inputs.json`, and the note that **filters affect the display only —
   every card is on disk in `runs/<slug>/cards/`.**

Apply `--pain high` (`intensity.score >= 4`) and `--wtp high` (`buyer_class ==
"b2b-operator"` or ≥1 cited `existing_spend`) **here and only here.**

Then a short **"excluded at the gate"** section: each excluded cluster, one line, with its
`inventory_gate.flags` reason. Visible, unranked, not silently deleted.

**If `--cards-only`, stop here** and go to "How to end."

---

## Stage 7 — Wedge → shape → context → distribute (top N)

Select the top N (`flags.top`, default 5) from the printed sort, **skipping cards with
`skeptic.under_researched: true` (§3.5) and any card that failed the inventory gate.**
Say which cards you skipped and why — a silently shortened list is indistinguishable from
a thin run. If fewer than N survive, say that too.

Per card, **four Tasks in strict sequence** — each link consumes the previous one's file:

1. **`wedgesmith`** (`slug`, `cluster_id`) → `$R/wedges/<cluster_id>.json` (CONTRACTS §5).
   Executes `skills/wedge-voltage` end to end. On a failed divergence gate it returns the
   failure, not a wedge list — carry that forward and move to the next card.
2. **`Task(subagent_type: "general-purpose")` applying `skills/mvp-shapes`** (`slug`,
   `cluster_id`, `wedge_id`) → `$R/shapes/<cluster_id>.json` (CONTRACTS §6). The
   `wedge_id` is the wedgesmith's rank-1 wedge, from its return line — do not re-rank the
   wedge file yourself. Technical complexity graded; `distribution_complexity` left `null`
   for step 4. Prompt it to read `skills/mvp-shapes/SKILL.md` in full first.
3. **`Task(subagent_type: "general-purpose")` applying `skills/marketing-context`**
   (`slug`, `cluster_id`, `wedge_id`) → **both** `$R/product-marketing.md` **and**
   `.agents/product-marketing.md` (CONTRACTS §7). Prompt it to read
   `skills/marketing-context/SKILL.md` and verify with `cmp`.
4. **`distributor`** (`slug`, `cluster_id`) → patches each shape's
   `distribution_complexity` block.

**Why this order and not "context first."** The marketing context cannot precede the wedge
— `skills/marketing-context` hard-requires `wedges/<cluster_id>.json`, because a context
built off a raw `canonical_pain` describes a vague pain and the tree turns a vague pain
into vague advice. And the distributor hard-stops without `.agents/product-marketing.md`,
because all 49 vendored marketing skills read that path and, when it is missing, silently
assume a generic B2B SaaS and produce fluent channel advice about nothing. So the chain is
forced: wedge → shape → context → distribute.

**Why cards are sequential, not parallel.** `.agents/product-marketing.md` is a **single
global file**. Two cards in flight means card 5's context overwrites card 3's while card
3's distributor is still grading — and the output is a graded, cited-looking channel
analysis of the wrong product. Nothing in the artifact would reveal it.

Handle that same global file across the loop:

```bash
# after each card's distributor finishes, snapshot the context for that candidate
cp $R/product-marketing.md $R/product-marketing.$CID.md   # orchestrator-local; no consumer
```

After the last card, restore the **top-ranked** card's context into both canonical paths so
the marketing tree ends the run wired to the #1 candidate, and say which candidate that is:

```bash
cp $R/product-marketing.$TOP_CID.md $R/product-marketing.md
cp $R/product-marketing.$TOP_CID.md .agents/product-marketing.md
cmp $R/product-marketing.md .agents/product-marketing.md && echo "marketing tree wired to $TOP_CID"
```

If the distributor's `primary_channel` / `time_to_first_25_users` landed after that card's
context was written, re-invoke `skills/marketing-context` for the **same** candidate: it is
a version bump touching two fields with a prepended Changelog line, not a rewrite.

Finally, append the wedge thesis and the shapes (with **both** complexity grades side by
side, never blended, plus `founder_fit.effective_complexity_delta` when non-zero) into
`opportunity-cards.md` under each card.

---

## How to end

Present the cards conversationally: the sort key you used, then each card in order — the
pain in the operator's own frame, the frequency numbers with `distinct_authors` beside
`cluster_size`, the intensity score with a marker or two, the WTP read with its evidence,
the strongest skeptic finding, the trend shape. Then the wedge and MVP shape for the top N.
Name the UNDER-RESEARCHED and gate-excluded cards. Give the paths on disk.

**Then stop. Ask nothing.**

No "would you like me to…". No numbered menu of next steps. No "shall I run /diligence on
c01?". No "let me know which one interests you." The instinct to offer follow-ups is strong
here and it is wrong: Matt will react to what he reads, and a menu pre-frames his reaction.

One reconciliation, since §3.8 says to offer re-sorts: state the re-sort keys as a **fact**,
not a question — "re-sortable by `frequency.read`, `wtp.read`,
`saturation.competitor_count`, or `retro_trend.shape`" — one line, no question mark, and
move on.

---

## Resuming a crashed or interrupted run

`$R/inputs.json` exists → resume. Walk these in order and restart at the first that fails.
A missing precondition **halts with a clear message**; it never produces a half-analyzed
run that looks finished.

Every check below is a **slurped** `jq -s` over the whole card set and prints the
`cluster_id`s that fail; `[]` means the stage is satisfied. `PASS` is shorthand for
`select(.inventory_gate.verdict == "pass")` — excluded cards are exempt from every
panel check (§3.7).

| Restart at | Check (prints offenders; `[]` = satisfied) |
|---|---|
| Stage 1 | `jq -e '.matrix\|length>=6 and length<=12' $R/inputs.json` (single file, `-e` is safe here) |
| Stage 2 | `wc -l $R/evidence/*.jsonl` ≥40 total, `source_health.json` has one entry per attempted source |
| Stage 3 | `jq -e '.clusters\|length>0' $R/clusters.json` (single file) |
| Stage 4 | `jq -s -c '[.[]\|PASS\|select((.frequency and .intensity)\|not)\|.cluster_id]' $R/cards/*.json` |
| Stage 4 (per agent) | the Stage 4b `MISSING PANEL:` command above — it names which of `wtp` / `skeptic` / `retro_trend` is absent, per card. Re-run only the missing one |
| Stage 5 | `jq -s -c '[.[]\|PASS\|select(has("saturation")\|not)\|.cluster_id]' $R/cards/*.json` |
| Stage 6 | `jq -s -c '[.[]\|select(.inventory_gate.verdict==null)\|.cluster_id]' $R/cards/*.json`, then `$R/opportunity-cards.md` exists |
| Stage 7 | `$R/wedges/<cid>.json` → `$R/shapes/<cid>.json` → `.agents/product-marketing.md` → `distribution_complexity != null` |

Re-running a scout is idempotent (both scripts dedupe on `id`); re-running the merge is
idempotent; re-running the historian re-spends GitHub rate limit, so check the fragment at
`$R/trends/<cid>-retro_trend.json` first.

---

## Orchestrator failure modes

| Failure | What it looks like | Discipline |
|---|---|---|
| Serial scouts | 12 cells, 40 minutes, user leaves | One message, 4–6 Task calls, per matrix cell |
| Per-scout MCP rediscovery | 12 agents each probe `dialog`, each get 401 | Probe once at Stage 0b, pass the resolved path down |
| Narrated degradation | "dialog needs OAuth, want me to set it up?" | Silent to the user, one line in `source_health.json` |
| Failure as absence | "No HN discussion found" when HN timed out | Health entry; a failed source is never a finding about the world |
| Parallel append corruption | `cluster.py` chokes on a half-written line | Scouts stage; you merge with `fromjson?` + `unique_by(.id)` |
| Lost panel update | Card silently missing `wtp` after the wave | Stage 4b reconcile from fragments before rendering |
| Gate that checks one card | `jq -e '.wtp' cards/*.json` exits 0 because the *last* card happened to be complete | Every multi-card gate is `jq -s` and prints the offending `cluster_id`s |
| Gate that halts on an excluded card | "HALT: missing intensity" because a gate-excluded card holds `null` panels by design | Gates filter on `.inventory_gate.verdict == "pass"` first |
| Saturation zero | `competitor_count: 0` because the lookup failed | `null` + health entry; zero is a claim |
| `--niche` as whitelist | 3 cells, one per named niche, zero discovery | Niche seeds/constrains the vertical axis; generation continues |
| Filter-at-capture | `--pain high` in a query string | Flags are display filters at Stage 6 only |
| Parallel cards in Stage 7 | Card 3 graded against card 5's marketing context | Sequential across cards; single global `.agents/` file |
| Analysis inline | Orchestrator reads evidence and picks a winner | Every judgment belongs to a subagent or a skill |
| Context bloat | Scout manifests pasted with 400 evidence items | Manifests only; read artifacts with `jq` on demand |
| Ending with a menu | "Would you like me to diligence c01?" | Present, then stop. Ask nothing. |
