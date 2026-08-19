---
description: Pain-point search only — capture public complaints, cluster them, score frequency and intensity from cited quotes, then stop before the expensive analysis half.
argument-hint: <broad inspiration> [--niche "<text>"] [--percentile N]
allowed-tools: Read, Write, Bash, Grep, Glob, Task, ToolSearch, mcp__pain-search
---

# /pain-search — the front half, run on its own

You are driving the `pain-search` MCP server, which owns Stages 0b-3 of
`/prospect`: frame, capture, merge, the thin-capture stop, clustering, the
frequency panel, the inventory gate, the intensity panel, and the report. Then
you stop.

**This is not a degraded `/prospect`.** It is the stage boundary that already
existed, made runnable: everything here is cheap and mechanical, and everything
after it (willingness-to-pay, the mandatory skeptic, trend reconstruction, wedges,
MVP shapes) is where a run actually spends. Stopping here to read the two axes
before spending the rest is the intended workflow, not a shortcut.

## Read this first, then work through the tools

- `skills/prospect-methodology/SKILL.md` **§3.0-§3.3** — the constitution for these
  stages. Reference its sections; do not restate them.
- The tool descriptions on the `pain-search` server. They carry the rules that used
  to live in `agents/scout.md` and `agents/distiller.md`, and they are the
  authority on *how* to call each stage. Read them rather than guessing arguments.
- `docs/CONTRACTS.md` §1-§4 only if you intend to read a run's files directly.

`ToolSearch` for `pain_` if the tools are not already loaded, then call
`pain_run_status` at any point to see which stage is next.

## What you own, and what the tools own

You own exactly four judgments. Everything else is enforced or computed.

| Yours | Theirs |
|---|---|
| The frame — personas x verticals x framings, queries in the complainer's vocabulary | The Stage-1 shape gate; refusing to capture against a half-written frame |
| Which source is relevant to which cell | The forbidden capture levers, which have no parameters |
| Which quote evidences which marker | Verbatim validation, distinct-author counts, the 1-5 ladder, the caps, the `read`, the `quadrant` |
| The inventory-gate verdict and the canonical pain sentence | The `pass`/`exclude` spelling and the `excluded:` flag prefix |

If you catch yourself deciding that a cluster "feels like the strongest pain
here", stop: that is what `intensity.score` is for, and it is computed from quotes
you cite, not from a view you form.

## The order

1. **Frame.** Build 6-12 cells per §3.0 and call `pain_run_create`. Two composition
   requirements the tool cannot check for you: at least one buyer persona **and**
   one sufferer-who-cannot-buy (without the contrast `complainer_is_buyer` has no
   discriminating power), and at least one inverted or adversarial framing. Tell
   the user the frame in ~6 lines, then keep going — it is not a checkpoint.

2. **Probe `dialog` once, before any capture.** One `ToolSearch` covering **both**
   spellings — `mcp__plugin_problem-prospector_dialog__*` (installed as a plugin, the
   normal case) and `mcp__dialog__*` (configured at user or project scope). Expect it
   absent or 401 until someone authenticates it, in which case Arctic Shift carries the
   run. Probe **once per run**, not once per cell, and carry the answer down.

   **If `dialog` answers, prefer it for Reddit.** It gives semantic subreddit discovery
   and full comment trees with citations, which beats the archive — and the archive is
   the source whose query endpoint throttles. This server cannot call dialog's tools for
   you, so: call them yourself, then hand each cell's results to
   **`pain_ingest_records`**, which shapes and validates them and computes the contract
   `id`.

   Its three tools wrap eleven operations; use `discover_operations` rather than
   assuming. **`search_subreddit` returns `selftext: null`** — search alone is a
   title-only capture, which systematically scores intensity 2. Run
   `search_subreddit` then `fetch_comments` per post, which returns the submission body
   and the comment tree, and ingest the posts. Dialog's comment objects have no URL and
   CONTRACTS forbids constructing one, so treat comments as reading for the intensity
   stage, not as separate evidence records.

   If dialog *errors* on a cell, record it with `pain_record_source_decision` and use
   `pain_capture_reddit` for that cell. Do not send an empty batch to
   `pain_ingest_records` to represent a failure — an empty batch is recorded as
   "searched and found nothing", which is a false claim about the world.

3. **Capture, in waves of 4-6.** Per cell: Reddit (dialog via `pain_ingest_records`
   if available, else `pain_capture_reddit` once per query), then
   `pain_capture_trends` only for sources the §3.1 relevance table admits, then
   `pain_capture_saturation` once. Record every deliberate skip with
   `pain_record_source_decision`. Pass `concurrent_captures` = the wave size, since
   the archive's rate limit is per IP and shared across processes.

   Mixing dialog and Arctic Shift across cells is safe: `pain_merge_staging`
   collapses any post both captured, so a pain is not weighted twice.

   Degradation is **silent to the user and loud in the run**. Do not report that
   `dialog` needs OAuth, do not offer to authenticate anything: the health file is
   where that lives.

4. **Merge and gate.** `pain_merge_staging` after each wave, then
   `pain_capture_gate`. On a `stop` decision, report the counts, name which sources
   *failed* separately from which returned *nothing*, and stop — do not cluster
   anyway.

5. **Cluster.** `pain_cluster`. If the cut looks wrong, re-cluster at a different
   `percentile`; never hand-merge or hand-split.

6. **Gate, then score, per cluster.** `pain_inventory_gate` on every cluster, then
   `pain_score_intensity` on each one that passed. Pull candidate quotes from the
   cluster's own evidence — the tool rejects a quote whose URL belongs to another
   cluster, and rejects the whole call if any single quote fails, so read before
   you cite. A cluster with nothing citable scores 1, which is a finding.

7. **Report.** `pain_report`, then present.

## How to end

Give the sort key, then each cluster in order: the pain in the operator's own
frame, the frequency numbers with `distinct_authors` beside `cluster_size`, the
intensity score with a marker or two and the quote behind it, and the 2x2 read.
Name the excluded clusters. Give the path on disk. Say that
`/prospect "<same inspiration>"` resumes this run at Stage 3.5 without
re-capturing.

Say plainly what has **not** been checked: nobody has looked for evidence anyone
pays to fix this, nobody has hunted counter-evidence, and no trend has been
reconstructed. A high score here means the pain is real and cited, not that there
is a business.

**Then stop. Ask nothing.** No "would you like me to run the full pipeline?", no
numbered menu. State the re-sort keys as a fact — "re-sortable by
`frequency.read`, `frequency.cluster_size`, or `quadrant`" — and move on.
