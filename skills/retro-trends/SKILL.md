---
name: retro-trends
description: "Reconstructs the backward-facing 3-5 year history of a pain cluster from four key-free scripts (HN Algolia, Reddit via Arctic Shift, Google Trends, GitHub repo creation) and fills only `retro_trend` in `cards/<cluster_id>.json`. Triggers when a run must answer 'has this been broken for years or did it just show up?', decide whether a hot cluster is only a news spike, check whether solutions are accumulating against a pain (repo-creation curves), or recompute slopes for `/rescan`; also on phrases like 'is this trending or persistent', 'how long has this been a problem', 'five-year history', 'is anyone building here', 'was this always broken'. Does NOT cover forward-looking forecasts, current-momentum lookups (that is the `trend-pulse` MCP or `scripts/trends_cli.py`), competitor counts (that is `saturation` from the `idea-reality` MCP or `scripts/reality_cli.py`), or ranking scores — it emits per-source series and one named shape, never a blended number."
---

# Retro trends: reconstruct 3-5 years of history per cluster, key-free

## Why this skill exists

Two failures, both fatal, both invisible without history.

**The news spike.** A cluster with 60 members and 4,000 engagement points looks like the
best thing in the run. Then you look back and every post lands in one two-month window
around a regulatory deadline or a viral thread. The pain was manufactured by a headline.
You would have spent a month building for demand that already evaporated.

**The quiet five-year hole.** A cluster with 14 members, low engagement, boring phrasing.
Then you look back and it has been complained about at exactly the same rate every
half-year since 2021, and nobody has shipped a repo against it. That is the money read:

> **Persistent-flat pain with no accumulating solutions is the classic underserved
> signal.** Flat complaints mean durable demand. Flat repos mean nobody has taken it.
> Both curves are required — either one alone is noise.

Today's snapshot cannot distinguish these cases. Frequency and intensity are measured on
a pile of posts with no time axis. This gap adds the time axis, and it is the only gap
in the pipeline where **every source is a plain script** — no MCP, no key, no OAuth — so
it is also the most reliable thing in the run. There is no excuse for skipping it.

No forward persistence is needed on day one. `/rescan` turns this forward-facing nearly
for free by diffing the stored `retro_trend` against a fresh capture, which is why the
keyphrases you choose must be recorded (see *Reproducibility for /rescan*).

## Decisions already taken — do not re-litigate

1. **Four sources, all key-free scripts.** HN Algolia, Reddit via Arctic Shift, Google
   Trends via trendspyg, GitHub unauthenticated search. Do not reach for an API that
   needs a token; do not propose one; do not read `GITHUB_TOKEN` or `GH_TOKEN`
   (`gh_history.py` sees both and ignores them on purpose, and says so on stderr, so every
   user gets the same series).
2. **Window is 3-5 years.** Default 5. Shorter only when the space did not exist 5 years
   ago, and then say so in `note`.
3. **Per-source series stay separate.** There is no cross-source average, no composite
   "trend score". The card carries one shape and one slope, and `note` names which
   source the slope came from.
4. **`retro_trend` is not a ranking input.** The default sort (CONTRACTS §4 Sort
   contract) is `intensity.score` desc → `wtp.read` desc → `saturation.competitor_count`
   asc. Trend never enters it. A reader may explicitly re-sort by
   `retro_trend.slope_pct_per_year`, and the active sort key gets printed above the list.
5. **Shape vocabulary is fixed and closed** (five values, below). Do not invent a sixth
   because a series looks interesting.
6. **Run this only on clusters that already passed `skills/no-inventory-gate`** and that
   are in the top-N being carded (`inputs.json` → `flags.top`). GitHub pacing costs real
   minutes per cluster; spending it on a cluster that gets gated out later is pure waste.

## Order of operations

1. Read `clusters.json` (CONTRACTS §3). For each target cluster take `canonical`,
   `member_count`, and pull 3-5 `exemplar_urls` / member texts for phrasing.
2. Derive keyphrases — **pain-side and solution-side sets are different** (next section).
3. Run the four scripts. GitHub last or in the background; it is the slow one.
4. Classify each series independently. Then assemble one cluster-level shape + slope.
5. Write `cards/<cluster_id>.json` → `retro_trend` only. Append source health.
6. Render the ASCII block for `opportunity-cards.md`.

**Before the first call in a run, probe the flags:**

```bash
uv run scripts/hn_history.py --help
uv run scripts/reddit_history.py --help
uv run scripts/gtrends_history.py --help
uv run scripts/gh_history.py --help
```

Every script is standalone and key-free, run as `uv run scripts/<name>.py`, JSON on
stdout, diagnostics on stderr (CONTRACTS cross-cutting rules 3 and 4). **The keyphrase
flag is not uniform:** `hn_history.py`, `gtrends_history.py` and `reddit_history.py` take
`--query` (repeatable); **only `gh_history.py` takes `--terms`**; and `gtrends_history.py`
takes `--window 5y|12m|all` instead of `--years`. `reddit_history.py` additionally
requires `--subreddits` in practice — Arctic Shift rejects an unscoped full-text query
with HTTP 400, and there is no global Reddit search. **If a flag name differs, `--help` is
authoritative, not this document.** Do not guess a flag twice; read the help.

Why this paragraph is load-bearing rather than pedantic: a wrong flag name exits non-zero
with no series, and the four failure paths in this gap all degrade *toward*
`persistent-flat` — the read you were hoping for. A mistyped flag therefore hands you the
underserved signal for free and wrong. Treat any empty series as a measurement failure
until you have re-read the help.

---

## Choosing keyphrases: the highest-leverage step, and where this gap usually dies

Everything downstream is a function of the strings you type. A bad keyphrase does not
produce a bad number — it produces a **confidently shaped curve about the wrong thing**,
which is worse, because it reads as evidence.

Derive **2-4 phrases per cluster** from the cluster's `canonical` plus its exemplars.
Take the nouns people actually used. Do not take your own summary language.

### The two failure modes

**Over-specific.** `"permit status is invisible to staff and applicants"` returns 0 in
every bucket. You then read the flat-zero line as "declining" or, worse, as
"persistent-flat" — the shape you were hoping for. It is neither. It is unmeasurable.

**Over-generic.** `"government"` returns thousands per bucket and measures the news
cycle of an entire sector. The curve is real and tells you nothing about your pain.

### The rule

> A phrase returning near-zero across **all** buckets is a **measurement failure, not a
> trend.** Re-derive the phrase. Never report a shape from an all-zero series.
> "Near-zero" = every complete bucket ≤ 2, or the whole-window sum < 10.

Re-derivation ladder, in order: drop qualifiers → keep the domain noun + the pain noun →
swap your word for the community's word (read the exemplars again) → drop to the domain
noun alone and accept that you are now measuring the space, and say so in `note`.

### Good and bad, concretely

Cluster canonical: *"permit status is invisible to staff and applicants alike."*

| Source | Good | Bad | Why the bad one fails |
|---|---|---|---|
| HN / Reddit (pain side) | `"permit status"`, `"permitting software"`, `"records request backlog"` | `"permit status is invisible"` | Sentence-shaped; zero hits every bucket |
| HN / Reddit | `"Accela"` (named incumbent) | `"software"` | Measures the industry, not the pain |
| Google Trends (public interest) | `"permit status"`, `"building permit"` | `"permit workflow opacity"` | Below Google's reporting threshold → flat zero |
| GitHub (solution side) | `"permit software"`, `"permitting"`, `"code enforcement"` | `"permit status is invisible"` | 0 repos → you conclude nobody is building, which is the exact wrong conclusion |

**The GitHub set must be broader than the pain set.** Repos accumulate at the level of
the *space noun*, not the pain phrasing. Searching GitHub for the pain sentence returns
zero and manufactures a false "nobody is building here" — the single most dangerous
error in this gap, because it produces the underserved read you want. Use the solution-
space noun a developer would put in a repo description.

**Named incumbents are excellent keyphrases** on HN and Reddit. Mentions of `Accela`,
`Granicus`, `Tyler Technologies` over time track the pain more faithfully than generic
phrasing, and they cross-check `wtp.existing_spend`.

Log the exact keyphrases used per source. They are part of the finding.

---

## The four sources

### 1. HN — `scripts/hn_history.py` (pain side, developer/operator voice)

HN Algolia with `created_at_i` numeric-filter windows, one query per half-year, reading
**`nbHits`** rather than paginating. Cheap and fast; no pagination cap to distort counts.

```bash
uv run scripts/hn_history.py \
  --query "permit status" --query "permitting software" \
  --years 5 --phrase \
  --out runs/<slug>/trends/c01-hackernews.json
```

- `series[].source` → **`"hackernews"`** (CONTRACTS §2 enum).
- Buckets are half-years by default (`--bucket half-year|year`), `period` format `2022H1`.
- `nbHits` counts **mentions, not people.** Never call an HN curve "demand" or "users".
- **Annualization trap:** half-year buckets mean the per-bucket slope is *half* the
  annual rate. `hn_history.py` already annualizes (it scales by
  `params.buckets_per_year`), so pass its `slope_pct_per_year` through unchanged. If you
  ever compute a slope by hand off the buckets, read `params.bucket` /
  `params.buckets_per_year` first, multiply, and say in `note` which you did.
- This script owns the shape vocabulary and its thresholds; it emits them verbatim under
  `thresholds` so a stored card can be re-derived later. Its `retro_trend` block is
  already contract-shaped — merge it, do not re-classify it.

### 2. Reddit — `scripts/reddit_history.py` (pain side, practitioner voice)

Arctic Shift public archive, post counts per year for the cluster keyphrases. Key-free
and the guaranteed Reddit path in this plugin.

```bash
uv run scripts/reddit_history.py \
  --query "permit status" --query "records request" \
  --subreddits sysadmin,msp \
  --years 5 \
  --out runs/<slug>/trends/c01-reddit.json
```

- **`--query`, not `--terms`** (that flag belongs to `gh_history.py` alone), and
  **`--subreddits` is effectively required**: Arctic Shift answers an unscoped full-text
  query with HTTP 400, so omitting it yields zero buckets — which reads as "nobody on
  Reddit ever mentioned this" and is a claim about the run, not the world. Take the
  subreddit list from `inputs.json` `matrix[].subreddits` for this cluster's `cell_ids`.
  Comma-separated and/or repeated both work; `r/` prefixes are tolerated.
- **Availability:** there is no MCP path to Reddit history here, and you must not reach
  for one. The hosted `dialog` MCP answers 401 `invalid_token` and needs OAuth, and
  anonymous `www.reddit.com/*.json` is 403 at the CDN edge. If a run needs live Reddit
  capture alongside this history (fresh posts, not counts), that is
  `uv run scripts/reddit_search.py` — Arctic Shift with a pullpush last resort — and the
  degradation is recorded, never mentioned to the user:
  `{"source": "dialog", "status": "unavailable", "fallback": "reddit_search.py", "detail": "401"}`.

- `series[].source` → **`"reddit"`** (the §2 enum value; if the script emits
  `reddit-history` or similar, normalize to `reddit` before writing the card).
- Buckets are calendar years, `period` format `2022`.
- **Counts can be censored by the request limit.** A bucket whose count equals the
  script's limit is a **FLOOR, not a count.** Never present a censored bucket as exact.
  Suffix it `+` in the rendering and say "at request limit" in `note`.
- **Censoring flattens.** It truncates the tall buckets and leaves the short ones alone,
  which manufactures `persistent-flat` and `declining`. Therefore: if any complete bucket
  is censored, **only rising/accelerating conclusions survive** from that series. You may
  never conclude flat or declining off a censored series. Raise the limit and re-run, or
  set `coverage: "thin"` and lean on HN.

### 3. Google Trends — `scripts/gtrends_history.py` (public search interest)

Interest-over-time, 5y window, via trendspyg.

```bash
uv run scripts/gtrends_history.py \
  --query "permit status" --query "building permit" \
  --window 5y \
  --out runs/<slug>/trends/c01-google-trends.json
```

- `series[].source` → **`"google-trends"`** (§2 enum).
- Buckets follow the window: `5y` → half-years (`2022H1`, interleavable with HN bucket
  for bucket), `12m` → quarters (`2022Q3`), `all` → years (`2022`). `count` carries the
  **mean relative index** for the period, not a count of anything; the series-level
  `units` key (`relative-index-0-100`) says so and should be carried through.
- Up to 5 terms share one browser load **and one 0-100 scale**, so a weak term next to a
  strong peer is compressed against the floor. The script marks that `thin` and names the
  peer. That is a **scale artifact, not low interest** — re-run with `--no-compare` for an
  independently normalized read before concluding anything about the weak term.
- **Values are relative — 0-100 normalized within the window, not absolute volume. A 40
  is not forty of anything.** Say this every single time the series is reported, in
  `note` and in the rendered card. A reader who thinks it is volume will compare it to
  HN counts and draw nonsense.
- Slope and shape are still valid (the normalization is monotonic within a window);
  levels are not comparable to anything else.
- The script re-checks the **weekly** points and escalates any shape to `spiky-episodic`
  when the peak stands ≥4× the median with r² < 0.30 — an event the half-year means
  smoothed away. It records the override in the series notes; keep the escalation and the
  reason, and go find the dated cause.
- A flat-zero Google Trends line almost always means the query is **below Google's
  reporting threshold for that geo and window** — unmeasurable, not "no interest". The
  script marks a zero-heavy series `thin` and reserves `coverage: "none"` for an empty
  return; keep whichever it emitted. Neither is evidence of absence: the fix is a broader
  term or `--no-compare`, never a conclusion.

### 4. GitHub — `scripts/gh_history.py` (solution side: are builders accumulating?)

Repos matching the space, bucketed by `created:` year, unauthenticated search.

```bash
uv run scripts/gh_history.py \
  --terms "permit software" --terms "permitting" \
  --years 5 \
  --out runs/<slug>/trends/c01-github.json
```

- `series[].source` → **`"github"`** (already emitted correctly by the script).
- **Unauthenticated search is 10 req/min. The script paces itself at ~6.5s.** Requests =
  `terms × (years + 1)`. Two terms over five years ≈ 12 requests ≈ 80 seconds; three
  terms ≈ 2 minutes. Five clusters at three terms ≈ 10 minutes. **Budget for it:** cap
  GitHub at 2 terms per cluster, or run one space-level GitHub query shared across
  clusters in the same vertical (repos accumulate per space, not per cluster) and reuse
  the series with a `note` saying it is space-level. The script also caps total requests
  at 40 (`--max-requests`) and **drops the oldest years first** when a plan exceeds the
  cap, so read `params` for the window you actually got rather than the one you asked for.
- The current year bucket is year-to-date and is **excluded from slope/shape** by the
  script. Do not re-include it — a 7-month bucket next to 12-month buckets fabricates a
  decline.
- Buckets over 1,000 are approximate (GitHub's own `total_count` behavior); the script
  flags them. A window summing under 10 repos swings on hobby projects — that is
  `coverage: "thin"`, **except** the fully-fetched all-zero curve, which is `good` (see
  *Coverage honesty*).
- **This is the one script whose per-series `shape` uses a wider vocabulary** (`rising`,
  `spike-and-fade`, `no-signal`, `insufficient-data`, plus `emerging`, `declining`,
  `persistent-flat`). That is **not** the card vocabulary. Map it — see *Vocabulary
  mapping* below. Copying `"rising"` into `retro_trend.shape` breaks the enum.

### MCP servers are not part of this gap

All four sources are scripts, by design. Both stdio MCPs may simply be absent (Cowork and
other hosts do not run them), so the CLI path is the assumption, not the exception:

- `trend-pulse` for *current* momentum → probe it, and on any failure or absence fall
  back to `uv run scripts/trends_cli.py`.
- `idea-reality`, if you read `saturation` for the two-curve matrix below → fall back to
  `uv run scripts/reality_cli.py`. This skill never *writes* `saturation`; it only reads
  what the economist/skeptic stage recorded.

Either fallback is silent to the user but recorded in `runs/<slug>/source_health.json`
with its `fallback` naming the script (CONTRACTS cross-cutting rule 5). **MCP output never
substitutes for the retro series** and never sets `retro_trend.shape` — different
question, different window.

---

## Classify each series independently

Use complete buckets only (drop the partial current bucket). `null` buckets are dropped,
**never treated as zero** — a failed fetch and an empty window are different findings and
conflating them inverts the conclusion.

**Slope.** `slope_pct_per_year` = OLS slope over bucket index, divided by the mean bucket
value, ×100, × buckets-per-year. Percent of the typical level per year. Round to 1dp.

**Flat band is ±15%/year.** This matches `flat_band_pct_per_year` in `hn_history.py`
`SHAPE_THRESHOLDS` and `FLAT_BAND_PCT` in `gh_history.py`, and exists because HN, Reddit,
and GitHub all grow ~10%/year as platforms. Movement inside ±15% is platform drift, not a
trend about your pain. Clearing the band is **necessary, not sufficient**: the
accelerating and declining bars sit further out (+60%/yr, −20%/yr) and a slope between the
band and the bar is "durable level with drift", i.e. still `persistent-flat`. **Absolute
mention counts are not normalized by platform volume** — Google Trends already is, the
other three are not. If a series only just clears the band, say so in `note` rather than
promoting it to a trend.

**Minimum three usable buckets.** A shape read off two points is a line.

### Decision procedure — evaluate in this order, first match wins

These are the thresholds `hn_history.py` classifies with (`SHAPE_THRESHOLDS`, emitted
verbatim under `thresholds` in every payload, and borrowed by `gtrends_history.py` so
`emerging` means the same thing on both). Do not invent your own numbers; if a script's
emitted thresholds differ from these, the payload wins and you say so in `note`.

Let `n` = complete buckets, `f` = first-half sum (earliest `floor(n/2)` buckets),
`total` = whole-window sum, `peak` = max bucket.

| # | Shape | Observable signature | Business read |
|---|---|---|---|
| 1 | *(no shape)* | `n` < 3, or `total` < 10, or every complete bucket ≤ 2 | **Measurement failure.** `shape: null`. Re-derive keyphrases or record `coverage: "none"`. Never a shape. (The scripts claim no shape below `total` 5, `min_total_for_shape`; this skill is stricter and writes `null` below 10, because 5-9 mentions across five years is one person's posting habit.) |
| 2 | `spiky-episodic` | `n` ≥ 4 **and** `peak` > 50% of `total` — tested **before** slope, since a spike fakes a large slope in either direction | Usually **news-driven, not durable demand** — a deadline, an outage, a viral thread, a regulation. Find the dated event before believing anything. Do not build for a spike that already faded. |
| 3 | `emerging` | `f` ≤ 15% of `total` **and** slope > +15%/yr (growth from a near-zero base) | New pain. Real *or* an artifact of renamed vocabulary — check whether an older word for the same thing exists before calling it new. Early is good; too early is indistinguishable from wrong. |
| 4 | `accelerating` | slope ≥ +60%/yr, with row 3 already ruling out the near-zero base | Rising demand on established pain. Best case when repos are flat — you are early on a curve that is already moving. |
| 5 | `declining` | slope ≤ −20%/yr. Asymmetric with the flat band on purpose: platform volume drift is likelier to look like mild decline than mild growth | Either it got solved (check the GitHub curve and `saturation`) or the community moved platforms. Distinguish before writing it off. |
| 6 | `persistent-flat` | everything left: \|slope\| ≤ 15%/yr, or a slope outside the band that reaches neither +60 nor −20 (say which in `note`) | **The money read**, but only next to a flat solution curve. Durable, unglamorous, unsolved. |

One guard on row 6: if ≥30% of complete buckets are `0`
(`intermittent_zero_bucket_share`), the series is **intermittent** — the slope turns on
whether one post landed in one bucket. The scripts note this; carry the note and do not
sell an intermittent series as durable.

Nothing outside `emerging | accelerating | persistent-flat | declining | spiky-episodic`
may be written to `retro_trend.shape`. When nothing fits, `null` plus a `note` — see the
warning below.

### Vocabulary mapping from script output

Only `gh_history.py` needs mapping. `hn_history.py` owns the card vocabulary and
`gtrends_history.py` borrows its classifier, so both already emit contract values — pass
those through unchanged rather than re-deriving them.

| `gh_history.py` emits | Card `retro_trend.shape` |
|---|---|
| `rising` | `accelerating` |
| `spike-and-fade` | `spiky-episodic` |
| `emerging` | `emerging` |
| `declining` | `declining` |
| `persistent-flat` | `persistent-flat` |
| `no-signal`, `insufficient-data` | **`null`** — measurement failure, plus `note`. Not a shape. |

`gh_history.py`'s local mirror uses lower bars (±15%/yr for `rising`/`declining`, an
interior-peak-and-fade test for `spike-and-fade`). So a GitHub `rising` between +15 and
+60%/yr is drift by the canonical thresholds: map it, then say in `note` that it did not
clear the accelerating bar. This never matters for the card-level shape — GitHub is the
solution side and never sets it — but it matters for the two-curve read below.

> **The shape you must never assign by default is the one you are hoping for.**
> `persistent-flat` is the desirable read, so every defect in this pipeline —
> over-specific keyphrases, censored Reddit buckets, thin GitHub coverage, a dropped
> null bucket — degrades *toward* flat. If you find yourself writing `persistent-flat`
> off a series with any of those defects, you are not measuring durability, you are
> measuring your own failure to measure. Write `null` and say why.

---

## Assemble the cluster-level read

**Do not average sources into one slope.** Report every series in `retro_trend.series[]`
with its own coverage.

**Card-level `slope_pct_per_year`** comes from the **single highest-coverage pain-side
source** (`hackernews` or `reddit`), and `note` names it: `"slope from hackernews"`.
GitHub is the solution side and must never enter the pain slope. Google Trends is
relative and must never be the headline slope when a pain-side source has good coverage.

**Card-level `shape`** is that same source's shape, unless another pain-side source with
equal coverage contradicts it — then `null` the shape and state the contradiction. A
contradiction reported plainly is worth more than a shape asserted falsely.

### When sources disagree, name the disagreement

Divergence is itself a finding. Write it in `note`, in plain words, with both numbers.

- **HN declining, Reddit flat** — plausible community migration, not dying pain. Say so.
- **Google Trends rising, GitHub flat** — public interest without builder supply. Demand
  outrunning tooling. This is the shape you want to find.
- **GitHub rising, Google Trends flat** — builders chasing each other, or a dev-tool
  space where the buyer *is* the builder. Different market, not a contradiction.
- **Reddit rising, HN silent** — practitioner pain that developers have not noticed.
  Often genuinely underserved; also often a market that does not buy software.

### The two-curve matrix — the actual point of this gap

Pain side (HN/Reddit) against solution side (GitHub). The words in this matrix are
**directions, not `shape` values**: "rising" covers `accelerating` and `emerging`, "flat"
is `persistent-flat`. Read the matrix with them; write only the enum to the card.

| Pain | Repos | Read | What to do |
|---|---|---|---|
| flat | rising | **Getting solved while you read this.** | Hurry or skip. Check `saturation.competitor_count` before committing. |
| flat | flat | **Underserved — the good one.** Durable pain, nobody building. | Proceed. Ask the skeptic *why* nobody built it; a structural blocker is the usual answer and it is usually the real story. |
| declining | rising | **Late.** Solutions arrived, pain is being absorbed. | Skip unless the wedge is a wholly different buyer. |
| rising | flat | **Early — best case.** Demand moving, supply hasn't. | Highest-priority card. |
| rising | rising | Hot and contested. | Only with a real wedge (`skills/wedge-voltage`); differentiation must be structural, not faster. |

"flat pain + flat repos" is not automatically good. It is good **only** when both curves
have `coverage: "good"`. Two thin curves agreeing on nothing is not agreement.

---

## Coverage honesty

Label `coverage` per source with observable criteria. This is not a vibe.

Every script emits its own `coverage` per series. **Carry the script's value through; do
not recompute a softer one.** The criteria, so you can read them:

| `coverage` | Criteria |
|---|---|
| `good` | Every planned complete bucket fetched, ≥3 usable buckets, no censored buckets, and the whole-window sum clears that source's floor — 20 mentions for HN (`good_min_total`), 10 repos for GitHub (`MIN_TOTAL_FOR_GOOD_COVERAGE`), ≥8 non-partial points and >25% of them nonzero for Trends (`MIN_POINTS_FOR_GOOD`, `THIN_NONZERO_FRACTION`). HN also allows up to 25% of buckets to fail and still call `good` (`max_failed_bucket_share_for_good`) |
| `thin` | Fetching worked but the series cannot carry weight: any bucket `null` past that tolerance, or <3 usable buckets, or the sum below the source's floor, or any bucket censored at the request limit, or a Trends peak index < 5 inside a comparison group (`DOMINATED_PEAK` — a stronger peer took the scale) |
| `none` | No bucket returned a usable number — host unreachable, circuit-broken, query rejected, empty series, or below the source's reporting threshold |

**One deliberate exception, and it is the whole point of the GitHub curve:** a GitHub
series where **every planned bucket fetched and every count is `0`** is `coverage: "good"`,
not `thin`. Coverage is complete and the finding is unambiguous — nobody is building here.
`gh_history.py` does this on purpose; do not discount it back to `thin` under the
small-volume rule, or you delete the highest-value read in this gap.

Rules that follow from it:

1. **A `thin` source must not be laundered into a confident slope.** Report its series;
   do not let it set the card-level shape; say so in `note`
   (`"GitHub history thin; treat slope as HN/Reddit-driven"` — the CONTRACTS §4 example
   is exactly this case).
2. **Distinguish loudly between "we measured little activity" and "we could not
   measure."** `count: 0` with a successful fetch is a finding: nobody is talking, nobody
   is building. `count: null` is an absence of information. They render differently
   (`▁` vs `?`), they set coverage differently, and confusing them is the failure this
   section exists to prevent. **A source that failed is never reported as "no discussion
   found" / "no interest" / "nobody is building."**
3. **All four sources `thin`/`none` is itself a finding.** Write it in `note`:
   `"no source achieved good coverage; history unmeasured, not absent"`. Do not write
   `skeptic.under_researched` — that field belongs to the skeptic and means something
   else (absence of counter-evidence). Hand the coverage failure over; do not overwrite.

Append every script's `source_health` entry to `runs/<slug>/source_health.json` per
CONTRACTS cross-cutting rule 5, including that rule's `fallback` key (`null` when the
source has no fallback, as none of these four do):

```json
{"source": "github", "status": "degraded", "fallback": null,
 "detail": "circuit-break: HTTP 403; years 2021, 2022 not fetched"}
```

Statuses come from the scripts (`ok` | `degraded` | `unavailable`). Never drop a failing
source from the card silently — emit the series with `coverage: "none"` so the gap is
visible in the artifact, not just in the log.

---

## Writing the contract fields

Write **only** the `retro_trend` key of `cards/<cluster_id>.json` (CONTRACTS §4). The
card is co-written by the distiller, economist, skeptic, and this skill (the historian
role). **Read the file, modify `retro_trend`, write it back.** Never emit a fresh card
object — you will clobber `intensity`, `wtp`, or `skeptic` and destroy the audit trail.

```json
"retro_trend": {
  "shape": "persistent-flat",
  "slope_pct_per_year": 2.1,
  "series": [
    {"source": "hackernews", "buckets": [{"period": "2022H1", "count": 12}], "coverage": "good"}
  ],
  "note": "keyphrases: \"permit status\" | \"permitting software\" (pain), \"permit software\" | \"permitting\" (github). Slope from hackernews (half-year buckets, annualized by the script, buckets_per_year=2). google-trends values are relative 0-100, not volume. GitHub history thin; treat slope as HN/Reddit-driven."
}
```

Field rules:

- `shape` — one of the five, or `null`. No other string.
- `slope_pct_per_year` — number or `null`. One decimal. Never a percentage of a
  percentage; never averaged across sources.
- `series[]` — one entry per source **attempted**, including failures. `source` must be
  a §2 enum value: `hackernews`, `reddit`, `google-trends`, `github`.
- `buckets[]` — `period` and `count` are required. `count` is an integer or `null`,
  **never 0 for a failed fetch.** Extra script keys (`window`, `partial`, `detail`,
  `terms_ok` from GitHub; `count_exhaustive` from HN; `units` and `n_partial_points` from
  Trends) may be carried through; they are additive audit metadata.
- `period` format is consistent within a series and comes from the script: `2022H1`
  half-years (HN default, Trends `5y`), `2022` years (Reddit, GitHub, Trends `all`),
  `2022Q3` quarters (Trends `12m`). Never mix granularities inside one series and never
  compare bucket levels across granularities.
- Unknowns are `null` (CONTRACTS cross-cutting rule 1). Do not estimate a count you did
  not fetch, do not construct a URL, do not fill a period the script did not return. A
  missing bucket is `null`; a missing slope is `null`; a shape you cannot justify is
  `null`. There is no acceptable guess in this file.
- `note` — free text, and the only place the reader learns what you actually did. It
  must always carry: the keyphrases per source, which source the slope came from, the
  Google Trends relativity caveat if that series is present, any censored buckets, and
  any coverage shortfall.

### Reproducibility for /rescan

`/rescan` diffs stored `retro_trend` slopes against a fresh capture (CONTRACTS §9). A
diff is meaningless if the keyphrases changed, so **the keyphrases live in `note`**, per
source, verbatim, quoted. Also persist raw script payloads with `--out` to
`runs/<slug>/trends/<cluster_id>-<source>.json`. That path is an additive audit artifact,
not a contract path — no consumer depends on it — but without it a rescan re-fetches
blind. On rescan: reuse the recorded keyphrases exactly; if you must change one, that is
a new series, not a delta, and say so.

---

## Rendering for the card

`opportunity-cards.md` is read as prose, so the series has to be legible in one glance.
Eight-level block ramp, one line per source, counts trailing.

Ramp: `▁▂▃▄▅▆▇█` — **scale each source independently to its own max.** Never share a
y-axis across sources; the units are different and Google Trends is not a count at all.

Markers: `?` = failed fetch (`count: null`). `+` after a count = at request limit, FLOOR.
`~` = approximate (GitHub buckets >1000). `·` in the ramp = a bucket that is zero on a
successful fetch (`▁` is reserved for "smallest nonzero").

```
retro_trend   shape: persistent-flat   slope: +2.1 %/yr   [from hackernews]
  hackernews     2021H1-2025H2  ▄▃▅▄▅▄▃▄▅▄   12 9 13 11 14 11 8 12 14 11   coverage good
  reddit         2021-2025      ▅▅█▅▅        88 91 104 90 87              coverage good
  google-trends  2021H1-2025H2  ▄▅▄▄▅        relative 0-100, NOT volume    coverage good
  github         2021-2025      ▁·▂▁?        2 0 3 1 ?                    coverage thin (sum<10, 1 failed)
  read: flat pain + flat builders -> underserved. GitHub thin; do not lean on it.
  keyphrases: "permit status", "permitting software" | github: "permit software", "permitting"
```

Rules for the block:
- Always print the shape, the slope, **and the source the slope came from** on line 1.
- Always print `coverage` at the end of every source line. No exceptions.
- Print the raw counts next to the sparkline. The sparkline is an aid; the numbers are
  the evidence. A sparkline alone is an opaque score with a nicer font.
- Google Trends prints the relativity caveat inline, every time, not just in `note`.
- Close with the two-curve read in one sentence and the keyphrases used.
- Where a source is `none`, print the line anyway: `github  2021-2025  (unavailable: HTTP 403)  coverage none`.

---

## Failure modes and gotchas

1. **Zero-line misread as declining.** All-zero or near-zero buckets are unmeasurable,
   not shrinking. Re-derive the phrase. Most common error in this gap.
2. **GitHub searched with the pain sentence.** Returns 0, you conclude "nobody is
   building", you get the underserved read for free and wrong. Solution-space nouns only.
3. **Censored Reddit buckets read as flat.** The limit truncates tall buckets. Flat and
   declining conclusions are forbidden off a censored series.
4. **Google Trends numbers compared to HN counts.** 0-100 relative versus absolute
   mentions. Never on the same axis, never in the same sentence without the caveat.
5. **Partial current bucket included in the slope.** Manufactures a decline every time.
   The scripts exclude it; do not helpfully add it back.
6. **`null` treated as `0`.** Inverts every conclusion in the two-curve matrix.
7. **Half-year buckets slope-computed by hand.** Halves the real rate; a rising pain reads
   flat. `hn_history.py` and `gtrends_history.py` already annualize — pass their
   `slope_pct_per_year` through, and if you ever compute one yourself read
   `params.bucket` / `params.buckets_per_year` first.
8. **`gh_history.py` vocabulary copied into the card.** `rising`, `spike-and-fade`,
   `no-signal`, and `insufficient-data` are not contract values. Map them.
9. **Platform growth read as pain growth.** All three count-based sources grow ~10%/yr on
   their own. That is why the flat band is ±15%.
10. **Sources averaged into one number.** Forbidden. Divergence is the signal; averaging
    deletes it. This is the failure mode the whole plugin exists to avoid, in miniature.
11. **GitHub budget blown.** 10 req/min means a careless five-cluster run burns 15
    minutes of wall clock. Two terms per cluster, or one shared space-level series.
12. **Whole card overwritten.** Read-modify-write `retro_trend`; leave every other panel
    exactly as found.
13. **A spike accepted without finding the event.** If it is `spiky-episodic`, name the
    dated cause or admit you could not find it. An unexplained spike is not a trend.
14. **A number in the series that was never fetched.** No interpolated bucket, no
    remembered count, no reconstructed URL for the dated event behind a spike, no plausible
    figure standing in for a request that failed. `null` and `[unknown]` are always
    available and always correct (CONTRACTS cross-cutting rule 1).
15. **A GitHub all-zero curve discounted to `thin`.** Fully fetched and empty is `good`
    coverage and a real finding; downgrading it deletes the underserved read.

## Boundaries

Write `retro_trend` and nothing else. Do not touch `canonical_pain`, `provenance`,
`frequency`, `intensity`, `quadrant`, `wtp`, `skeptic`, `saturation`, or
`inventory_gate`. Do not produce wedges — that is
`skills/wedge-voltage`. Do not write launch copy from a "broken for five years" finding;
hand the series downstream and let `skills/marketing/launch` use it. Do not compute or
suggest a composite opportunity score from the slope, the shape, or anything else in
this file.
