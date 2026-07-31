---
name: wedge-voltage
description: "Generates entry strategies — voltage-banded wedge permutations — for an OpportunityCard that already exists at `runs/<slug>/cards/<cluster_id>.json`, and writes `runs/<slug>/wedges/<cluster_id>.json` per CONTRACTS §5. Applies at the wedgesmith step of `/prospect`, at the wedge refresh inside `/diligence`, and on requests phrased as 'what's the wedge', 'where would you start', 'entry point', 'how do we get in', 'voltage permutations', 'give me angles on this pain'. Applies especially when a run is about to free-associate three or four product ideas off a pain cluster, or when wedge output across cards has started to look interchangeable. Do NOT use it to find or cluster pain (that is `skills/prospect-methodology` + `scripts/cluster.py`), to grade build or distribution complexity (`skills/mvp-shapes`), to write the five-section report (`skills/deep-diligence`), or to screen physical-goods businesses (`skills/no-inventory-gate`)."
---

# Wedge Voltage — mechanical permutation of entry strategies

## Divergence from the commissioning spec (read this first)

The spec that commissioned this plugin assumed **voltage** meant the *differential
between pain intensity and current-solution quality* at a slice — a scoring idea.
The Armsreach source
(`vendor/armsreach/skills/divergence-engine/SKILL.md`) defines voltage as
**distance from the obvious (V1–V4), a generation setting**. This implementation
follows the source repo's method — the repo wins on method — while honoring the
spec's I/O contract in `docs/CONTRACTS.md` §5.

That is the right call for two reasons. The differential reading tells you how to
*rank* wedges you already thought of; it does nothing about the reason your wedge
list is bad, which is that you only thought of the obvious ones. Armsreach's
reading is a *generation* mechanism aimed squarely at model collapse — the actual
failure. And nothing is lost: the differential intuition survives intact as the
**distance pair** below (`pain_distance` = how grounded in real complaint,
`incumbent_distance` = how far from what is already sold). Low pain distance with
high incumbent distance *is* "severe pain, weak existing solution" — measured
against cited text instead of asserted on a 1–5 scale.

## Why this skill exists

Frontier LLMs are trained to predict likely continuations. Asked for wedges off a
pain cluster, they emit the single most probable answer set — and asking for
"creative" or "brainstorm" makes it worse. **This is a structural property of the
model, not a prompting error. You cannot prompt your way out of it; you have to
engineer divergence structurally.**

The concrete failure this prevents: a run produces eight beautiful
OpportunityCards, and every one of them gets wedged into "a SaaS dashboard for
the team that has the problem." Eight cards, one idea. The reader cannot tell,
because each wedge reads fine in isolation. Worse, the collapse is invisible
without measurement — a list of 40 candidates *looks* diverse. It is often one
idea wearing forty hats.

So: **enumerate permutations, do not free-associate.** You are filling a grid,
not having ideas. Then you measure the spread and let a number — the cluster
count — tell you whether you actually diverged. Then a human converges.

One line for the whole engine: **spark wide with no quality bar, measure the
spread to prevent collapse, converge with human judgement.**

## Decisions already taken — do not re-litigate

- Voltage is distance from the obvious (V1–V4), a generation setting. Not a
  differential. Not a quality score.
- Three stages, never collapsed. No quality judgement in Stage 1. No generation
  in Stage 3.
- Stage 2 is the **gate**, not optional polish. `min_clusters` default **6**.
- Target **40–60** candidates. Final output **3–7** wedges — this skill's rule;
  CONTRACTS §5 fixes the *shape* of `wedges[]`, not its length.
- The cluster cut is **adaptive, percentile-based, default p35**. Never a fixed
  cosine threshold.
- Embedding space is **BAAI/bge-small-en-v1.5 via fastembed**, the same space
  `clusters.json` was built in. Never substitute a model — distances to the pain
  centroid become meaningless the moment the space changes.
- `pain_distance` and `incumbent_distance` are reported as a **pair, never
  blended**. There is no composite wedge score. A reader must be able to see
  which distance a given wedge is winning on.
- Stage 3 **presents; the human decides.** You rank and cut; you do not pick.
- Write only the fields CONTRACTS §5 defines. No extra keys, ever.

## Preflight — refuse to run without these

| Need | Path | Used for |
| --- | --- | --- |
| The card | `runs/<slug>/cards/<cluster_id>.json` | canonical_pain, wtp.existing_spend, saturation, skeptic.under_researched |
| The cluster | `runs/<slug>/clusters.json` | `member_ids`, `exemplar_urls`, `canonical` for the matching `cluster_id` |
| Raw evidence | `runs/<slug>/evidence/*.jsonl` | the verbatim `text` + `url` behind each `member_id` (CONTRACTS §2) |

If `clusters.json` is missing you have no pain centroid and therefore no
grounding check — stop and say so. Do not proceed on the card's prose alone; the
card is already a summary, and building the pain centroid from a summary is how
V4 nonsense gets waved through.

If `cards/<cluster_id>.json` has `skeptic.under_researched: true`, carry that
label into the Stage-3 presentation verbatim. A tidy wedge table over
under-researched pain reads as validation. It is not.

---

## Stage 1 — Diverge

### 1a. Decompose first (the bridges)

Do not ask for wedges. Ask for the **dimensions along which a wedge could vary**,
then diverge along each dimension separately. Decomposition is itself a
divergence technique: it forces traversal of multiple axes instead of collapse to
one attractor.

The bridges here are fixed by CONTRACTS §5 — three axes, no more:

- **`who_first`** — which persona in the cluster gets served first.
- **`slice`** — which sub-pain inside the cluster is the entering wedge.
- **`substrate`** — what existing workflow or tool the wedge attaches to versus
  replaces.

For **each axis** generate **4–6 values at each voltage level** (12 axis-voltage
cells, ~48–60 axis values). Every cell must be filled. **An empty cell is the
collapse showing up as a hole** — it means you could not get off the attractor on
that axis, not that the axis has nothing there.

### The voltage rubric — observable, not vibes

Anchored so a reader can check the label. If you cannot run the test, the tag is
wrong.

- **V1 — obvious.** The answer everyone gives. **Test:** you can point to a post
  in the cluster that proposes it, *or* to a feature the named incumbent already
  ships. If neither, it is not V1.
- **V2 — one bridge out.** Exactly **one** axis value moves off obvious; the
  other two stay obvious. **Test:** you can name the single substitution in one
  clause ("same pain, same substrate, different buyer").
- **V3 — two bridges out.** **Two** axis values differ from V1 simultaneously.
  No cited post proposes it, yet you can trace the causal chain back to a cited
  complaint in **one** step. **Test:** write the chain as one sentence. If you
  need two "and then"s, it is V4.
- **V4 — deliberately distant.** No surface connection: `who_first` is not a
  complainant anywhere in the evidence, *or* `substrate` is not software at all —
  yet a **named** party has a documented reason to pay. **Test:** you can cite
  that party's incentive from captured evidence, not from your own market
  knowledge. If you cannot cite it, it is not V4, it is noise — and it will
  surface as high `pain_distance` and be dropped in Stage 2.

**The costume check.** Restate a candidate in the incumbent's own vocabulary. If
it becomes indistinguishable from a V1 candidate, it *is* V1 wearing a V3
costume. Retag it. Inflated voltage tags are the most common way this stage lies
to itself: the pool looks hot, the cluster count says otherwise, and you waste a
re-run finding out.

### Worked example prompts (use these shapes; the abstract instruction reliably yields V1-only output)

Card `c01`, canonical pain: *"Permit status is invisible to staff and applicants
alike."* Complainants: 311 dispatchers, permit clerks. Named incumbent from
`wtp.existing_spend`: Accela.

**`who_first`**
- **V1:** "Name the buyer the evidence already points at — the office that owns
  the permitting system and its budget line."
  → *the permitting department manager*
- **V2:** "Name a different role inside the same building who feels the identical
  pain but is not the software buyer."
  → *the front-desk clerk absorbing the "where is my permit" calls*
- **V3:** "Name someone on the other side of the transaction, who is not a
  government employee at all, and who loses money when status is opaque."
  → *the general contractor's office admin tracking 40 permits across 6 cities*
- **V4:** "Name a party with no surface connection to permitting whose own
  economics slip when permits slip, and cite where the evidence shows they care."
  → *the construction lender whose draw schedule depends on permit milestones*

**`slice`**
- **V1:** "State the pain as the cluster states it, with nothing removed."
  → *automate the permit workflow end to end*
- **V2:** "Carve out exactly one step of that pain and forbid yourself the rest."
  → *the status notification only — not the workflow, not the forms*
- **V3:** "Do not solve the pain. Solve the most expensive *consequence* of the
  pain that the evidence quantifies."
  → *the inbound call volume the opacity generates, measured in staff hours*
- **V4:** "Treat the pain's byproduct data as the product. What does this broken
  process emit that nobody currently publishes?"
  → *comparative permit cycle-time data across jurisdictions*

**`substrate`**
- **V1:** "Attach to nothing — a new web app that replaces the incumbent."
  → *a modern permitting portal*
- **V2:** "Attach to the incumbent without touching it. No migration, no login,
  no data model."
  → *a scrape-and-notify layer sitting on top of Accela's public pages*
- **V3:** "Attach to a tool nobody thinks of as part of this workflow but which
  the persona already has open all day."
  → *rules and a saved search in the shared Outlook inbox*
- **V4:** "Solve it with no software substrate at all. Name the delivery
  mechanism a software person would refuse to consider."
  → *an SMS shortcode and a weekly PDF the clerk already prints*

### Constraint prompting — constraints beat adjectives

"Name a wedge a competitor would never think to look at" beats "be creative."
Use all three forms; rotate the specifics per card.

- **Exclusion:** "No wedge involving a new database, a new login, or a data
  migration." / "No wedge whose first customer is the city."
- **Competitor-framed:** "Name a wedge Accela's roadmap would never fund because
  it cannibalizes seat revenue." / "Name the wedge an incumbent's enterprise
  sales motion structurally cannot deliver."
- **Format-mismatch:** "Solve permit status opacity as a weekly email, not an
  app." / "Solve it as a public webpage with no auth." / "Solve it as a phone
  tree."

### 1b. Compose the permutations (this is the mechanical part)

A wedge is a **triple** — one value per axis. Do not hand-pick triples by taste;
that is free association re-entering through the back door.

1. Enumerate **voltage-banded** triples: for each level `v` in 1..4, form 12–15
   triples drawing axis values at level `v` and `v±1` only.
2. **A wedge's `voltage` = the max of its three drawn values' voltages** — never
   an average, never a sum. A wedge is as far from obvious as its most distant
   move, and averaging three tags would launder a V4 move into a V2 label.
3. **Coverage rule:** every axis value generated in 1a must appear in at least
   one composed triple. If you cannot place a value, that is a Stage-1 gap to
   note — not a licence to discard it.
4. **Quotas:** ≥8 candidates in each voltage band, 40–60 total. The quota is what
   makes an all-V1 pool structurally impossible.

Write each candidate as **one self-contained sentence, 12–25 words**, that
encodes all three axis values without naming them:

> "Give the waiting contractor a nightly SMS on permit status, scraped from the
> city's public portal."

Then write the pool to `runs/<slug>/wedges/candidates-<cluster_id>.jsonl`, one
record per line. This is a Stage-1 scratch artifact kept for audit; it is **not**
a contract file and no downstream consumer reads it.

```json
{"id": "c01-cand-07", "text": "Give the waiting contractor a nightly SMS on permit status, scraped from the city's public portal.", "voltage": 3, "axes": {"who_first": "contractor's office admin", "slice": "status opacity only", "substrate": "scrape the public portal, replace nothing"}}
```

### Stage-1 prohibitions

**No quality bar. No filtering. No ranking. No self-critique.** Quality
judgement and divergence are different operations; performing them together
collapses variety. The tell that you broke this rule: your pool has 12 entries
and all 12 are defensible. Cheap volume is the point — Stage 2 does the killing.

---

## Stage 2 — Map & Measure (the gate)

Stage 1 over-generates on purpose. Stage 2 makes the collapse measurable.

### 2a. Build the pain centroid and its footprint

The pain centroid is the mean of the cluster's **member evidence text** — the
verbatim `title` + `text` (CONTRACTS §2) of every `member_id` listed for this
`cluster_id` in `clusters.json`. Truncate each to a comparable length (~400
chars); do not paraphrase.

Also compute, for **every member**, its own distance to that centroid, and take
the **p90** of those distances. That p90 is the pain's observed footprint and it
becomes the drop gate in 2d. It is derived from the pool, not hardcoded, for the
same reason the cluster cut is.

```bash
# Run from the plugin root. Scripts are always invoked as `uv run scripts/<name>.py`;
# this snippet is not an invocation — it imports cluster.py's public geometry API
# (embed / centroid_distance / percentile) so distances land in the same space
# clusters.json was built in. embed() raises rather than degrading, which is what
# you want here: a lexical fallback must not silently replace the semantic space.
uv run --with fastembed python - <<'PY'
import glob, json, sys
sys.path.insert(0, "scripts")
from cluster import embed, centroid_distance, percentile

SLUG, CID = "back-office-pain-small-gov-2026-07-31", "c01"
run = f"runs/{SLUG}"

clusters = json.load(open(f"{run}/clusters.json"))
cl = next(c for c in clusters["clusters"] if c["cluster_id"] == CID)

ev = {}
for path in glob.glob(f"{run}/evidence/*.jsonl"):
    for line in open(path):
        rec = json.loads(line)
        ev[rec["id"]] = rec

members = [ev[i] for i in cl["member_ids"] if i in ev]
member_texts = [f"{m.get('title') or ''} {m.get('text') or ''}".strip()[:400] for m in members]

pain_vecs = embed(member_texts)
# footprint: how far the real complaints sit from their own centroid
member_d = [centroid_distance(v, pain_vecs) for v in pain_vecs]
p90 = round(percentile(member_d, 90), 3)

cands = [json.loads(l) for l in open(f"{run}/wedges/candidates-{CID}.jsonl")]
cand_vecs = embed([c["text"] for c in cands])   # wedge SENTENCE only

for c, v in zip(cands, cand_vecs):
    c["pain_distance"] = round(centroid_distance(v, pain_vecs), 3)
print(json.dumps({"pain_p90_gate": p90, "candidates": cands}, indent=2))
PY
```

If `embed()` / `centroid_distance()` / `percentile()` signatures differ from the
above, read `scripts/cluster.py` and adapt — but never swap the model and never
fall back to a lexical similarity measure. A lexical fallback would score a wedge
as "grounded" for reusing the cluster's nouns, which is exactly the wrong signal.
And if `embed()` raises because fastembed cannot reach its model (first use
downloads ~130MB into `~/.cache/fastembed`), that is a **stop**: record it in
`runs/<slug>/source_health.json` and say the distances could not be computed. It
is not a licence to estimate `pain_distance` by eye — an eyeballed distance is a
fabricated number in a field a downstream consumer will treat as measured.

### 2b. Build the incumbent centroid

Competitor **positioning text** — how incumbents describe themselves — not your
description of them.

- **In `/prospect`:** take competitor names and one-line positioning from the
  `idea-reality` MCP. If that stdio server did not load (it often does not),
  fall back to `uv run scripts/reality_cli.py` — silently to the user, but
  **recorded**: append to `runs/<slug>/source_health.json`
  `{"source": "idea-reality", "status": "unavailable", "fallback": "reality_cli.py", "detail": "<what happened>"}`.
  Seed the list with the tool names already in `wtp.existing_spend[].tool`.
- **In `/diligence`:** crawl the real competitor pages —
  `uv run scripts/crawl.py <url>...` — and build the centroid from hero,
  features, and pricing copy. See `skills/marketing/competitor-profiling` for
  what is worth extracting from a competitor page (positioning statement, stated
  ICP, headline claims). Same method, better-grounded incumbent distance.
- **Degenerate case:** fewer than 2 real positioning texts → `incumbent_distance`
  is `null` and you say so out loud. **Do not synthesize competitor copy from
  memory to fill the centroid.** CONTRACTS cross-cutting rule 1: if a source did
  not return it, the field is `null`. With a null incumbent distance, selection
  runs on `pain_distance` plus explicit qualitative whitespace reasoning, and the
  presentation must flag that the novelty half is unmeasured.
- A source that failed is **never** reported as "no competitors found."

### 2c. Cluster the candidate pool — the gate

Same script, same embedding space as `clusters.json`:

```bash
uv run scripts/cluster.py \
  runs/<slug>/wedges/candidates-<cluster_id>.jsonl \
  --min-clusters 6 \
  --percentile 35 \
  --out runs/<slug>/wedges/candidate-clusters-<cluster_id>.json
```

Inputs are **positional** (files or directories); `--out` writes the payload and
stdout stays parseable JSON either way. `candidate-clusters-<cluster_id>.json` is
scratch like the candidate pool — the only contract file this skill produces is
`wedges/<cluster_id>.json`. Run `uv run scripts/cluster.py --help` once per
session if you have not; flag names may drift. Two things must not be adapted
away: the model stays bge, and the cut stays adaptive.

**Verify the space before you trust the gate.** `cluster.py` degrades to its
lexical backend on its own when fastembed's model is not cached, and announces it
only in the payload. If `backend` is not `"fastembed"` or `embedding_model` is
not `"BAAI/bge-small-en-v1.5"`, this pool was embedded somewhere other than
`clusters.json`'s space: the cluster count is then measuring word overlap, not
idea variety. Record it in `runs/<slug>/source_health.json` and re-run once the
model is cached. Do not write a `divergence_gate` off lexical vectors.

**Embed the wedge sentence only.** Do not concatenate the axis labels into the
embedded text — if you do, all twelve `who_first` candidates share the literal
string "who_first" and cluster on that. The gate then passes on shared
boilerplate. Same reason candidate sentences must be roughly the same length and
register: mixed lengths inflate cluster count, and the gate ends up certifying
formatting variety instead of idea variety.

**One representative per cluster.** If 30 of 48 candidates land in one cluster,
that cluster gets **one** slot, not 30. The representative is the member with the
**lowest `pain_distance`** — best grounded wins the slot.

**The cluster count is the diversity metric.** `cluster.py` reports the numbers
under a `gate` key — `candidate_count`, `cluster_count`, `min_clusters_required`,
`passed`, `largest_cluster_share`, `cut_basis` — but there `passed` is
**advisory** and the exit code stays 0, because for *evidence* three clusters is
a finding, not a failure. For a *candidate pool* it is a failure. So recompute
`passed = cluster_count >= min_clusters_required` yourself from the JSON and
never infer the outcome from the exit code. Then write the result to
`runs/<slug>/wedges/<cluster_id>.json` exactly per CONTRACTS §5 — the key there
is `divergence_gate`, not `gate`, and the `note` field `cluster.py` adds is not
copied across:

```json
{
  "cluster_id": "c01",
  "divergence_gate": {
    "candidate_count": 48, "cluster_count": 9, "min_clusters_required": 6,
    "passed": true, "largest_cluster_share": 0.19, "cut_basis": "adaptive:p35"
  },
  "wedges": []
}
```

`cut_basis` is whatever `cluster.py` reports (`"adaptive:p35"` at the default
percentile). `largest_cluster_share` comes from `gate.largest_cluster_share` —
largest cluster's member count ÷ `candidate_count`, 3 decimals — recomputed the
same way if the payload omits it.

**On FAIL (`cluster_count < min_clusters_required`): stop.** Write the file with
`"passed": false` and an empty `wedges` array, and do not produce wedges, shapes,
or a presentation. See the gotcha below — a failed divergence run *is* the
convergence trap.

**Concentration warning.** `passed` is defined by the cluster-count rule only —
do not redefine it. But `largest_cluster_share > 0.40` with a passing count means
one dominant idea plus scattered noise. Say that in the presentation and re-run
Stage 1 hotter unless the dominant cluster is genuinely the pain's core.

### 2d. The two distances

This is where the commissioning spec's differential intuition lives.

- **`pain_distance`** — cosine distance from the wedge sentence to the cluster's
  **pain-evidence centroid**. **LOWER is better:** the wedge is grounded in cited
  evidence. **Hard drop:** `pain_distance > pain_p90_gate` from 2a — the wedge
  sits further from the pain than 90% of the actual complaints do, i.e. outside
  the pain's own footprint. **Drop it however clever it sounds.** This is the
  guard against V4 producing beautiful nonsense, and it is the only thing
  standing between "deliberately distant" and "made up."
- **`incumbent_distance`** — cosine distance to the **incumbent-positioning
  centroid**. **HIGHER is better:** novelty, whitespace.

**The wedge you want is minimally distant from the pain and maximally distant
from the incumbents.** Both numbers are reported; **neither is blended into a
single score, and no ratio, product, or difference of the two is ever computed.**
A reader must be able to see that wedge A wins on grounding and wedge B wins on
novelty, and choose. Collapsing that into one number is the exact laundering this
plugin exists to prevent.

**Distances are only comparable within one pool, one model, one run.** Never
compare `c01`'s `pain_distance` to `c02`'s as though they were the same scale —
different centroids, different footprints. Never carry a distance forward across
a re-embed.

---

## Stage 3 — Converge (present, do not decide)

Human plus AI. Craft, risk screening, and judgement re-enter here — and only
here.

**Mechanical selection rule, in order:**

1. **Drop** every candidate with `pain_distance > pain_p90_gate`. Non-negotiable.
2. **Drop** every candidate that reintroduces physical inventory, warehousing,
   fulfillment, or per-unit COGS on goods — route survivors through
   `skills/no-inventory-gate`. V4 loves hardware; this is excluded **at the
   gate**, not down-ranked. Note the drop in your presentation prose; do **not**
   add a field to CONTRACTS §5 for it.
3. **Drop** every candidate with no real `evidence_ids`. A wedge with no
   pointers is a hunch.
4. Keep **one representative per Stage-2 cluster**, then rank by
   `incumbent_distance` **descending**.
5. Fill **3–7** slots subject to three spread requirements:
   - **≥1 V1 anchor.** The obvious wedge stays on the table so the reader can see
     what was rejected and against what.
   - **≥1 surviving V3 or V4.** If none survives the pain gate, the run is
     substantively convergent even though the cluster gate passed — say so
     explicitly and re-run Stage 1 hotter.
   - **≤2 wedges sharing the same `who_first` value.** Buyer is the axis that
     drives distribution; three wedges for the same buyer is one wedge.
6. Where two wedges are near-tied on the pair, **present both**. You rank; the
   human picks.

Then write each wedge as an element of the `wedges` array in
`runs/<slug>/wedges/<cluster_id>.json` — the same file 2c created, top-level keys
`cluster_id`, `divergence_gate`, `wedges` — exactly the CONTRACTS §5 shape and
nothing more:

```json
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
```

- `wedge_id` is `<cluster_id>-w<N>`, numbered in presentation rank order.
- `thesis` is one line, and it must be falsifiable by the evidence it cites.
- `evidence_ids` are real `member_ids` from `clusters.json`; `evidence_urls` are
  the matching `url` values from the evidence JSONL. **Never construct a URL.
  Never cite a post you did not read.** Two to four pointers per wedge, chosen
  because they *power the claim* — not because they are the highest-engagement
  posts in the cluster.
- `rationale` names which distance the wedge is winning on and at what cost. One
  sentence. It is not a place to reintroduce a composite judgement.

**Presentation to the human** — print the gate line first
(`candidate_count / cluster_count / min_clusters_required / cut_basis`), then a
table of wedges with axis values, voltage, and the distance **pair shown as two
columns**. Carry `skeptic.under_researched` and any null `incumbent_distance`
as visible caveats. Do not recommend one. Ask which to take into `mvp-shapes`.

---

## Failure modes and gotchas

- **A failed divergence run IS the convergence trap.** FAIL means re-run Stage 1
  **hotter** — never proceed downstream. Concretely: drop the V1 quota, double
  the V3/V4 cells, and add two new exclusion constraints derived from whatever
  the collapsed cluster's members all assumed (they collapsed because they shared
  an assumption — read the cluster and forbid it). **Never fix a FAIL by lowering
  `--min-clusters`, and never by lowering `--percentile`** — a lower percentile is
  a tighter cut, so it shatters the same convergent pool into more, smaller
  clusters and the count climbs while nothing about the ideas changed. Either move
  is gaming the gate, and it converts the one honest signal in this skill into
  decoration. Both moves also leave a trace — `min_clusters_required` and
  `cut_basis` sit in the file for exactly this reason — so a reader will see a
  tuned number where a measured one was promised.
- **Fixed distance thresholds do not transfer across embedding models.** Short
  strings under bge sit in a narrow band — distinct candidates 0.21–0.53 apart,
  median ~0.37 — so a hardcoded 0.45 collapses everything into a single cluster
  and the gate cheerfully reports FAIL forever. The cut is derived from the
  pool's own pairwise-distance distribution (p35 default) and `cluster.py`
  reports `cut_basis` so the choice is auditable. The same reasoning is why the
  pain drop gate is the members' p90 and not a magic number.
- **Stage-1 self-critique is forbidden** because it collapses variety. If your
  pool is short and every entry is defensible, you critiqued while generating.
  Throw it out and regenerate to quota.
- **Do not embed axis labels or JSON keys** — shared boilerplate manufactures
  fake spread. Embed the wedge sentence only, at a consistent length and
  register.
- **Voltage inflation.** Tagging a V1 as V3 to satisfy the quota produces a pool
  that looks hot and clusters cold. Run the costume check.
- **Ungrounded cleverness.** The single most seductive failure: a V4 wedge that
  reads brilliantly and cites nothing. It will have a high `pain_distance`. Drop
  it. The pain gate exists precisely because you will not want to.
- **Cross-run distance comparison.** Distances are pool-relative. A 0.21 here and
  a 0.21 on another card are not the same claim.
- **Source failures are never "nothing found."** If `idea-reality` did not load
  or a crawl returned nothing, `incumbent_distance` is `null`, the fallback is
  recorded in `source_health.json`, and the presentation says the novelty half is
  unmeasured. Silence to the user about the *mechanism*, never about the *gap*.
- **Contract drift.** `wedges/<cluster_id>.json` is consumed by the distributor,
  `mvp-shapes`, and `/diligence`. Adding a helpful extra field breaks them
  silently. If you genuinely need one, change `docs/CONTRACTS.md` §5 and every
  producer/consumer named there — do not improvise.
