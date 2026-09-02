# Audit: every runtime string a user reads

Scope: non-docstring string literals in `scripts/pain_report.py`, `pain_rubric.py`,
`pain_stages.py`, `pain_intensity.py`, `pain_capture.py`, `pain_cards.py`,
`cluster.py`, collected by AST walk with docstrings excluded and f-strings
counted once (reassembled with `{…}` placeholders). Measured 2026-09-01.

**Reconciliation against the 2026-09-02 baseline** (16 section references,
13 undefined terms): section references match exactly — **16** unique strings
match `§\s*\d` or `CONTRACTS §`. The vocabulary count depends on the unit:
**18 strings** of ≥6 words contain contract vocabulary, spanning **11 distinct
terms**; the baseline's 13 sits between and most plausibly excluded the three
stat-row templates (`pain_report.py:93/142/257`) that render field names as
labelled keys rather than inside sentences. All 18 are listed below so nothing
is silently descoped; the three stat rows are marked as key-rendering rather
than prose.

Failure tests: **[S]** section reference · **[V]** undefined contract
vocabulary in prose · **[A]** no next action and not explicitly informational.

## Failing strings, worst first

| file:line | verbatim (truncated) | fails | why | suggested rewrite |
|---|---|---|---|---|
| scripts/pain_rubric.py:171 | `echo-chamber cap high->medium (distinct_communities == 1) per §3.3 correction 2, which this implementation reads as overriding the medium threshold's >=2-community leg — see pain_rubric.frequency_read` | S,V,A | Cites a correction number and its own source file at the user; "see pain_rubric.frequency_read" is a maintainer action | `Frequency lowered from high to medium: every one of these posts came from a single community. One community's shared vocabulary clusters beautifully and tells you nothing about the wider world. The pain may still be real — the cap is about your coverage, not the problem. Add communities and re-capture to lift it.` |
| scripts/pain_rubric.py:295 | `level 3 carried by the monotone reading of §3.3 (>=1 cost marker at >=2 distinct authors, not exactly one): {…} cost markers qualify but 'complainer_is_buyer' is absent, so level 4 is not met` | S,V,A | "monotone reading" defined nowhere user-reachable; states the rule by citation | `Scored 3: at least one cost marker is backed by two or more different people. Level 4 also needs a second cost marker and evidence the complainer is someone who could buy — neither is present.` |
| scripts/pain_report.py:192 | `**Encoded rubric interpretation — the intensity ladder.** At least one score was carried by the monotone reading of §3.3's level 3…` | S,V,A | A maintainer's methodology contradiction surfaced in a user's report; no user action exists | Move behind `--verbose`; file the underlying contradiction as a TODO in the methodology so the disclosure stops being needed |
| scripts/pain_report.py:199 | `**Encoded rubric interpretation — the community cap.** At least one single-community cluster was capped at 'medium' rather than collapsed to 'low'. §3.3 states 'distinct_communities' both as…` | S,V,A | Same disease as :192 | Same: `--verbose` + methodology TODO |
| scripts/pain_report.py:157 | `· scaled by {…} for a {…}-item corpus (§3.3 calibrates for {…}-{…}; distinct_communities never scales — it guards against an echo chamber, not a volume shortfall)` | S,V | The why is half-present; the citation carries the other half | `· thresholds scaled by {…} for a {…}-item corpus (the rubric is calibrated for {…}-{…} items; the community count never scales — it guards against an echo chamber, not a volume shortfall)` |
| scripts/pain_report.py:36 | `intensity.score desc -> frequency.cluster_size desc (pain-search sort: wtp and saturation are not researched at this stage, so the CONTRACTS §4 default sort cannot be run yet)` | S,V | Sort-order caption leans on the contract doc | `sorted by intensity, then by cluster size (willingness-to-pay and saturation are not researched at this stage, so the full ranking cannot run yet — informational)` |
| scripts/pain_report.py:270 | `This is a pain-search run: §3.0-§3.3 complete, §3.3b onward not started. '/prospect "{…}"' resumes this same run at Stage 3.5 and will not re-capture…` | S | Stage numbers are citations; the action is present and good | `This is a pain-search run: capture, clustering and scoring are complete; the paid analysis half (willingness-to-pay, skeptic, trends) has not started. '/prospect "{…}"' resumes this same run without re-capturing.` |
| scripts/pain_rubric.py:199 | `engagement-driven promotion medium->high (engagement_weighted {…} >= run top decile {…}, distinct_communities {…})` | V,A | Field names as nouns; no action and not marked informational | `Frequency raised from medium to high: engagement on these posts is in this run's top tenth and they come from {…} different communities. Informational — no action needed.` |
| scripts/pain_rubric.py:177 | `community cap high->medium (distinct_communities {…} < {…})` | V,A | Same cap as :171 in terser form, same fixes | Same rewrite family as :171 |
| scripts/pain_rubric.py:186 | `repetition demotion {…}->{…} (distinct_authors/cluster_size {…}/{…} < 0.4)` | V,A | A ratio of two field names explains nothing | `Frequency lowered: fewer than 40% of these posts have distinct authors — a few people repeating themselves reads as volume but is not. Add sources or communities and re-capture.` |
| scripts/pain_rubric.py:313 | `capped {…}->2: markers rest entirely on profanity_urgency` | V,A | Field name as noun; why unstated | `Intensity capped at 2: the only evidence of severity is angry language. Swearing shows feeling, not cost — a score above 2 needs a cost marker (money lost, time quantified, a workaround built).` |
| scripts/pain_stages.py:232 | `matrix holds {…} cells; §3.0 requires 6-12` | S,A | Citation stands in for the reason; no fix named | `Your frame has {…} search angles. This stage takes 6-12 — fewer than 6 and you only find what you already suspected; more than 12 and capture takes longer than reading the result. Drop some, or split into two runs.` |
| scripts/pain_stages.py:241 | `cell {…}: {…} queries; §3.0 wants 3-6` | S,A | Same as :232 | Same treatment, per-cell |
| scripts/pain_intensity.py:69 | `{…} words; §3.3 caps an exemplar at {…}` | S | Cap cited, not explained | `Quote is {…} words; the cap is {…}. A long quote stops being evidence for one specific marker and starts being a paragraph that mentions it.` |
| scripts/pain_intensity.py:185 | `unknown marker(s) {…}; §3.3 has exactly these six: {…}` | S | Citation adds nothing to the list that follows | `unknown marker(s) {…}; the rubric has exactly these six: {…}` |
| scripts/pain_intensity.py:191 | `set the inventory gate on this cluster first — §3.7 is applied at every promotion point` | S | Action is present; citation carries the why | `set the inventory gate on this cluster first — every promotion point re-checks it so excluded businesses never absorb paid analysis` |
| scripts/pain_intensity.py:86 | `quote differs from the source only in case — §3.3 requires verbatim` | S | The verbatim rule deserves its own words (its sibling at :79 already has them) | `quote differs from the source only in case — quotes must match the captured text exactly, or they are not citable` |
| scripts/pain_capture.py:310 | `{…} of {…} record(s) failed the CONTRACTS §2 check; nothing was staged. Fix or drop each one and call again.` | S | Action present; "CONTRACTS §2" names a doc the user does not have | `{…} of {…} record(s) failed the evidence-shape check; nothing was staged. Fix or drop each one and call again.` (per-record errors already name the field) |
| scripts/pain_capture.py:354 | `{…} is outside the CONTRACTS §2 enum` | S,A | Citation, and the legal values are not listed | `{…} is not a recognised source; valid sources are: {list}` |
| scripts/cluster.py:1212 | `Dedup and cluster captured evidence (CONTRACTS §2 JSONL) into clusters.json (CONTRACTS §3). After this runs, the cluster is the unit of analysis…` | S | argparse description; contract cites replaceable with plain nouns | `Dedup and cluster captured evidence (the JSONL the capture stage wrote) into clusters.json. After this runs, the cluster is the unit of analysis, never the raw post.` |
| scripts/pain_report.py:354 | `nothing here. '/prospect "{…}"' resumes this run at Stage 3.5 (analysis-pool cap, then wtp/skeptic/retro_trend).` | V | Stage number + field names in prose; action present | `nothing here. '/prospect "{…}"' resumes this run where pain-search stops: capping the analysis pool, then willingness-to-pay, skeptic and trend research.` |
| scripts/pain_cards.py:205 | `excluded cards keep null intensity/quadrant by design; they still appear in the report's own section, never silently dropped` | V | Borderline: explicitly informational and states the why; "quadrant" is the one unglossed noun | Link or gloss "quadrant"; otherwise keep |
| scripts/pain_stages.py:249 | `cell_id {…} must match <letter><2 digits>, e.g. m01` | V | Field name as noun, but the action and an example are present | `cell id {…} must match <letter><2 digits>, e.g. m01` |
| scripts/cluster.py:1294 | `smallest group that counts as a cluster (default 2: one post is a rumour, two phrasings are a pain)…` | V | `--help` text; mentions `min_cluster_size` as an implementation aside — harmless but glossable | Keep; drop the HDBSCAN aside or move it to the docstring |
| scripts/pain_report.py:93 | `**Frequency — '{…}'** · cluster_size {…} · distinct_authors {…} · distinct_communities {…} · engagement_weighted {…}` | V* | Stat row rendering field names as labelled keys, not prose — mechanically flagged, judgment: keys are correct here | Link each label to its glossary anchor when the report renders markdown |
| scripts/pain_report.py:142 | `'{…}' · {…} evidence items across {…} responding sources · cut_basis '{…}'` | V* | Same stat-row shape; `cut_basis` value is a machine token a reader cannot decode | Render the glossary sentence for the cut alongside the token |
| scripts/pain_report.py:257 | `- '{…}' — cluster_size {…} · frequency '{…}' · gate '{…}' — {…}` | V* | Same stat-row shape | Glossary links, as :93 |
| scripts/pain_report.py:172 | `Every cluster is on disk in 'runs/{…}/cards/'… Re-sortable by 'frequency.read', 'frequency.cluster_size', or 'quadrant'.` | V* | Field paths named as sort keys — correct as keys; borderline | Keep; glossary-link `quadrant` |

## Counts

- **1,401** non-docstring string literals scanned; **121** message-shaped
  (≥6 words or mechanically flagged); the remainder are JSON keys, enum values,
  format fragments and paths.
- **Failures by test:** section reference **16** · vocabulary in prose **18
  strings / 11 distinct terms** (of which 4 are stat-row key renderings, marked
  V\*) · no-next-action **8** (all listed above; every no-action string also
  fails S or V).
- **93** message-shaped strings pass all three tests — argparse help,
  per-record validation errors that name the field and the fix, and log lines
  with explicit actions dominate. The scan itself is reproducible; prompt 05's
  `tests/validate_user_strings.py` re-derives the mechanical half on every run.

## The five worst offenders

1. `scripts/pain_rubric.py:171` — cites a correction number and its own
   function name at a user, for the cap they will hit most often.
2. `scripts/pain_rubric.py:295` — "monotone reading of §3.3" appears in every
   level-3 intensity note; the phrase exists in no user-reachable document.
3. `scripts/pain_report.py:192` — a methodology self-contradiction disclosed in
   every report, unactionably, to the person least able to fix it.
4. `scripts/pain_report.py:199` — same, for the community cap.
5. `scripts/pain_stages.py:232` — the first gate a new user can hit rejects
   their frame with a citation instead of the reason and the fix.

## The house style to match (do not rewrite)

- `scripts/pain_intensity.py` — "quote does not appear verbatim in the captured
  title/text of that record; paraphrase and ellipsis-stitched fragments are not
  citable"
- `scripts/pain_rubric.py` `QUADRANT_READS` — "possible niche gold — few
  voices, all bleeding; demand a real buyer before advancing"
