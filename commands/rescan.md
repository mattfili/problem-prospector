---
description: Re-run capture and retro-trends for a saved run against its stored matrix, then diff — cluster weight deltas, new and vanished clusters, slope and shape changes.
argument-hint: "<run-slug> [--top N] [--cells m01,m04] [--no-trends] [--force-trends] [--card-new N]"
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, Task
---

# /rescan — drift detection against a stored run

You are the orchestrator. You parse args, enforce gates, delegate to subagents, persist
artifacts, and write one report. **You do not do the analysis a subagent owns**, and you
do not re-diligence the run: this is a diff, not a second `/prospect`.

Read first, in this order: `docs/CONTRACTS.md` (§1, §3, §4 `retro_trend`, §9,
cross-cutting rule 5), `skills/prospect-methodology/SKILL.md` (stage-gate discipline,
§3.1, §3.2), `skills/retro-trends/SKILL.md` (*Reproducibility for /rescan*). Field names
come from CONTRACTS and nowhere else.

**What a rescan is for.** `/prospect` looks backward: it reconstructs 3–5 years of
history from a single snapshot. Storing that snapshot in `runs/<slug>/` is what makes the
method forward-looking — re-run the *same* capture later and the delta between the two
snapshots is the only genuinely forward signal this plugin can produce without keys. It
is nearly free because `inputs.json`, `clusters.json`, `cards/`, `trends/`, and
`source_health.json` are already on disk.

**What a rescan is not.** No re-ranking, no new `opportunity-cards.md`, no economist or
skeptic fan-out over clusters that already have cards, and **no composite "drift score"**
— the same ban as everywhere else (§3.8). Report deltas per axis, cite evidence, stop.

**Cost shape.** Recapture (R2) is the same wall clock as a `/prospect` capture and is
unavoidable — it is the measurement. Everything after it is cheap: clustering is one
local pass, matching is one embedding call, and the analysis fan-out is skipped entirely.
Retro-trends (R6) is the only optional expense and it is capped at `--top N` with one
shared GitHub series. A weights-only rescan (`--no-trends`) is capture plus about a
minute.

**The original run is read-only for the entire rescan.** Everything you write goes under
`runs/<slug>/rescan-<YYYY-MM-DD>/`, except the report, which is written to the CONTRACTS §9
path `runs/<slug>/rescan.md` **and** copied to `runs/<slug>/rescan-<YYYY-MM-DD>.md` so
repeated rescans accumulate instead of overwriting each other. `rescan.md` is the contract
path and always holds the latest diff; the dated copies are the history.

If any subagent patches `runs/<slug>/cards/*.json` you have destroyed the baseline you are
diffing against and the run is unrecoverable — `runs/` is gitignored.

Shell state does not persist between Bash calls in every host. Substitute the literal
slug and date into each command; do not rely on exported variables.

---

## R0. Resolve the run, or list and stop

`$1` is the slug. Everything after it is flags. Normalize first: strip a leading `runs/`
and a trailing `/`, so `runs/foo-2026-07-31/` and `foo-2026-07-31` both work.

**If `$ARGUMENTS` is empty, or the slug does not resolve, list what exists and stop.**

```bash
# find, not a glob: an unmatched runs/*/ aborts the whole command under zsh
find runs -mindepth 1 -maxdepth 1 -type d | sort | while read -r d; do
  created=$(jq -r '(.created_utc|todate)? // "-"' "$d/inputs.json" 2>/dev/null || echo "no inputs.json")
  nclusters=$(jq -r '(.clusters|length)? // "-"' "$d/clusters.json" 2>/dev/null || echo "no clusters.json")
  ncards=$(find "$d/cards" -maxdepth 1 -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
  last=$(find "$d" -maxdepth 1 -name 'rescan-*.md' 2>/dev/null | sort | tail -1)
  printf '%-46s created %s  clusters %s  cards %s  last rescan %s\n' \
    "${d#runs/}" "$created" "$nclusters" "$ncards" "${last##*/}"
done
```

Empty output means there are no saved runs at all — say that, and point at `/prospect`.

If exactly one slug has the argument as a prefix, use it and say which you picked. If
several do, print them and stop — do not guess.

**Fail clearly, do not degrade** (each of these gets its own message, not a generic
error):

| Condition | Message |
|---|---|
| `runs/<slug>/` absent | slug not found; print the list above |
| `inputs.json` missing or unparseable | "captured before `inputs.json` existed (or written by hand) — the matrix cannot be reused, so a rescan would search somewhere else and the diff would be meaningless. Run `/prospect` fresh." |
| `inputs.json.matrix` empty / <1 cell | same reasoning: nothing to re-run |
| `clusters.json` missing or unparseable | "no stored clustering to diff against. Run `/prospect` fresh." |
| `clusters.json.clusters` empty | "the stored run found zero clusters; there is no baseline. Check its `source_health.json` before assuming the pain isn't there." |

A run that cannot be rescanned should be *said* to be unrescannable. Silently producing a
diff against a partial baseline is worse than refusing.

Then read the flags. All are optional and additive; a bare `/rescan <slug>` is the
intended invocation.

| Flag | Effect | Default |
|---|---|---|
| `--top N` | cap matched clusters getting a retro-trend re-run | `inputs.json.flags.top`, else 5 |
| `--cells m01,m04` | recapture only these matrix cells (cheap partial) | all cells |
| `--no-trends` | weights-only diff; skip R6 entirely | off |
| `--force-trends` | run R6 even when elapsed time makes it uninformative | off |
| `--card-new N` | build cards for the N largest **new** clusters | off (N defaults to 3 when bare) |

**Unrecognized flags are surfaced, never swallowed.** Print them in one line
(`unrecognized, ignored: --tpo 3`), carry them into the report header, and proceed with
defaults. A typo silently resolving to a default is a quiet wrong answer.

Compute and print, before doing anything expensive:

- `DATE` = `date -u +%F`. Rescan root `RS = runs/<slug>/rescan-<DATE>/`.
- `elapsed_days` = now − `inputs.json.created_utc`; if a previous `rescan-*.md` exists,
  also print the gap since that one.
- **If `elapsed_days < 30` and neither `--force-trends` nor `--no-trends` is set, skip
  R6 automatically** and say why: retro-trend buckets are half-years (HN, Trends `5y`)
  and years (Reddit, GitHub). Inside one bucket there is nothing a trend could have done;
  any movement you would report is arithmetic on a partial bucket.

If `RS` already exists (same-day re-invocation, or a crashed rescan), **resume**: walk the
stage gates named in each heading below, in order, and restart at the first unsatisfied
one. Evidence is append-only and deduped by `id`, so resuming is safe. Never delete a
staged file — it already cost rate limit. Gates, in order:

| Stage | May not start until this exists |
|---|---|
| R2 recapture | `runs/<slug>/inputs.json` parses with ≥1 matrix cell, and `clusters.json` has ≥1 cluster |
| R3 cluster | `rescan-<DATE>/evidence/*.jsonl` totalling ≥40 lines **and** `rescan-<DATE>/source_health.json` with one entry per attempted source |
| R4 match | `rescan-<DATE>/clusters.json` with a non-empty `clusters` array, same backend as the original |
| R5 verdicts | `rescan-<DATE>/cluster_match.json` |
| R6 trends | `rescan-<DATE>/cards/<cid>.json` copies exist for every eligible cluster |
| R7 card-new | `--card-new` passed **and** R6 finished (GitHub pacing is shared) |
| R8 report | `cluster_match.json`, both `source_health.json` files |

```bash
mkdir -p runs/<slug>/rescan-<DATE>/{evidence/.staging,trends,cards}
```

---

## R1. Probe the MCPs once, then forget them (dual-path rule)

Each probe is one check for the tool's presence, not a retry loop, not a credential
prompt. Failure is silent to the user and recorded.

| Capability | Primary (opportunistic) | Guaranteed fallback |
|---|---|---|
| Reddit capture | `mcp__dialog__*` | `uv run scripts/reddit_search.py` |
| Multi-source capture | `mcp__trend-pulse__*` | `uv run scripts/trends_cli.py` |
| Saturation | `mcp__idea-reality__idea_check` | `uv run scripts/reality_cli.py` |

`dialog` requires OAuth and returns `401 invalid_token` unauthenticated (CONTRACTS
appendix); expect it to be absent. `trend-pulse` and `idea-reality` are stdio servers
that may not load at all in Cowork. Scouts probe for themselves — you only need the probe
result to seed the health file and to pass a hint down.

**Rescan health goes to `runs/<slug>/rescan-<DATE>/source_health.json`, not the original.**
This is a deliberate, stated deviation from cross-cutting rule 5's single-file
convention: R5 and the report *compare* the two runs' health files, and appending the new
capture to the old file would destroy the comparison. The rescan directory mirrors the
run layout (`evidence/`, `clusters.json`, `source_health.json`, `trends/`, `cards/`), so
every contract shape still applies inside it.

```bash
RS=runs/<slug>/rescan-<DATE>
[ -f "$RS"/source_health.json ] || echo '[]' > "$RS"/source_health.json

# one probe verdict per server, appended INTO the array (this file is an array,
# not JSONL — the rescan copy differs from the original on purpose)
jq --argjson e '{"source":"dialog","status":"unavailable","fallback":"reddit_search.py","detail":"401 invalid_token"}' \
   '. + [$e]' "$RS"/source_health.json > "$RS"/.sh.tmp && mv "$RS"/.sh.tmp "$RS"/source_health.json
```

Write one such entry for each of `dialog`, `trend-pulse`, `idea-reality`, whatever the
probe said. R5 gate 1 reads these back; a probe you did not record is a degradation the
diff cannot see.

**Retro-trend history has no MCP path at all** — all four scripts are key-free by design
(`skills/retro-trends`). Do not reach for `trend-pulse` to substitute for a history
series; different question, different window, and MCP output may never set
`retro_trend.shape`.

---

## R2. Recapture — the same matrix, in parallel (gate: `inputs.json` validated)

**Reuse `inputs.json.matrix` verbatim. Do not regenerate it, do not improve a query, do
not add a cell.** This is the single decision that makes the whole command mean anything:
a regenerated matrix changes *what was searched*, so every observed delta confounds "the
world changed" with "we looked somewhere else." A rescan whose matrix drifted is not a
measurement, it is two unrelated snapshots stapled together. If the matrix is genuinely
wrong, that is a `/prospect` run, not a rescan — say so and stop.

Launch **one `scout` subagent per matrix cell, batched 4–6 concurrent** (§3.1). Parallel
because cells are independent and each is IO-bound on distinct hosts; batched because
`--politeness` scales per process and N concurrent scouts multiply the request rate at
Arctic Shift by N.

Task prompt per scout — the standard scout brief plus exactly one override:

> slug `<slug>`, cell `<cell_id>` from `runs/<slug>/inputs.json`. Concurrency hint: `<N>`.
> **Path override, the only deviation from your instructions:** everywhere they say
> `runs/<slug>/evidence/.staging/`, write to
> `runs/<slug>/rescan-<DATE>/evidence/.staging/` instead. Flags, limits, gates, capture
> discipline, and manifest format are unchanged.

Non-negotiables, because each one silently fabricates a delta:

- **Identical flags to the original capture.** `--limit 100 --comments
  --comments-per-post 10 --comments-max-posts 25`, no `--min-score`. A different `--limit`
  makes the diff a measurement of the limit, not of the world.
- **No `--after` / `--before`.** An incremental window makes every delta positive by
  construction, every cluster look like it grew, and every genuinely dead cluster
  invisible. Arctic Shift also back-fills, so a full re-pull can legitimately *gain* old
  posts — that is signal about the archive, and you want to see it.
- **Never point a scout at `runs/<slug>/evidence/.staging/`.** Both capture scripts dedupe
  against their own `--out` file by `id`, so appending into the original run's staging
  files would fold the fresh pull into the old capture and manufacture a flat diff.
- Scouts return manifests. Discard any analysis; keep the files (§3.1).

With `--cells`, launch only those scouts and **record the restriction** — R5 downgrades
every cluster whose `cell_ids` are not fully inside the captured set.

**Merge** (orchestrator only, never a scout):

```bash
RS=runs/<slug>/rescan-<DATE>
# <source>-<cell_id>.jsonl -> evidence/<source>.jsonl, deduped on id.
# The <source> prefix is the CONTRACTS §2 enum value and derives the destination.
ls -1 "$RS"/evidence/.staging/*.jsonl | sed 's#.*/##; s#-[a-z][0-9][0-9]*\.jsonl$##' | sort -u \
| grep -v '^health$\|^saturation$' | while read -r src; do
    jq -c -s 'unique_by(.id)[]' "$RS"/evidence/.staging/"$src"-*.jsonl \
      > "$RS"/evidence/"$src".jsonl
  done
# every health-*.jsonl line becomes one element of the rescan health array,
# MERGED ON TOP of whatever R1's probe already recorded — never overwriting it
jq -s 'add' \
   <(jq -s 'if length==1 and (.[0]|type=="array") then .[0] else . end' "$RS"/source_health.json) \
   <(jq -s '.' "$RS"/evidence/.staging/health-*.jsonl) \
   > "$RS"/.sh.tmp && mv "$RS"/.sh.tmp "$RS"/source_health.json
```

**Do not truncate `source_health.json` here.** R1 already wrote the probe verdicts into it
(`dialog: unavailable -> reddit_search.py`, `trend-pulse: unavailable -> trends_cli.py`,
`idea-reality: unavailable -> reality_cli.py`). A `>` redirect over this file deletes
exactly the evidence that R5 gate 1 needs to decide whether negative deltas are readable
at all — and a rescan whose degradation record was silently erased prints `shrank` and
`vanished` over a run where the source simply 401'd. That is the failure-as-absence bug
with the audit trail removed, which is worse than the bug.

Apply the **thin-capture stop** (§3.1): if the fresh corpus is <40 items or fewer than
three attempted sources returned anything, stop before clustering, record it, and report
the run as *unrescannable today* — **not** as "the pain went away." A thin recapture next
to a healthy baseline is the exact input that would otherwise print "every cluster
shrank."

---

## R3. Re-cluster with pinned geometry (gate: ≥1 `evidence/*.jsonl`, ≥40 lines, health file written)

```bash
uv run --quiet scripts/cluster.py runs/<slug>/rescan-<DATE>/evidence/*.jsonl \
  --run-slug <slug> \
  --backend <old .backend> --algorithm <old .algorithm> \
  --distance-threshold <old absolute cut>  \
  --min-cluster-size <same as the original run> \
  --out runs/<slug>/rescan-<DATE>/clusters.json
```

**Pass the glob, not the directory.** Verified: `cluster.py` resolves a directory with
`rglob("*.jsonl")`, which descends into `evidence/.staging/` and re-reads every staged
file the merge already folded in. The `id` dedupe keeps the counts honest, but the
payload's `evidence-input` health line then reports dozens of phantom "duplicate captures
collapsed" — and R5 gate 1 diffs the two runs' health files, so phantom entries on the
rescan side look like a capture-quality regression that never happened.

The cut is adaptive: `--percentile 35` derives an *absolute* cosine cut from the pool's
own pairwise distribution, so the same percentile on a different pool gives a different
cut, and cluster boundaries move for reasons that have nothing to do with the world.
Recover the original's absolute cut and pin it:

```bash
jq -r '.source_health[] | select(.source=="clustering") | .detail' runs/<slug>/clusters.json
# -> "agglomerative, cut adaptive:p35=0.34, median pairwise distance 0.41"
```

If the absolute cut is unrecoverable, fall back to the same `--percentile` as
`cut_basis` records, and print `cut_drift: <old> -> <new>` in the report header so the
reader knows some boundary movement is geometric, not real.

If the original's `cut_basis` contains `;not-applied`, the clusterer ran density-only and
that cut was never applied to the original grouping — pinning it reproduces nothing.
Re-run with the same `--algorithm` and say in the header that boundary movement is partly
geometric, so `new` and `vanished` counts are soft.

**Backend guard.** If `cluster.py` degrades to the offline lexical backend (fastembed
model not cached, no network) while the original used fastembed, the two clusterings live
in different spaces and no weight delta is interpretable. **Stop the diff**, record the
health entry, and report the rescan as blocked on the embedding model. Do not print
deltas with a caveat — they would be read anyway.

Append `clusters.json`'s own `source_health[]` array into the rescan health file.

---

## R4. Match clusters by embedding proximity, never by id (gate: `rescan-<DATE>/clusters.json` has ≥1 cluster)

**`cluster_id` is positional.** `c01` is "the first cluster this run produced," and it is
stable across nothing — a single new post reorders the labels. Diffing `c01` to `c01`
produces a table of numbers that is entirely garbage and entirely plausible-looking: it
will show clusters growing, shrinking, and swapping meaning, and nothing in the output
says so. Match on *meaning*, using the same embedding space `clusters.json` was built in.

`canonical` is the medoid phrasing, so it can change wording for the identical pain when
the corpus changes. Text equality is not matching either. Embed both sides and measure.

One process, one `embed()` call: fastembed loads a ~130MB model per process, and the
distances must be computed in a single space.

```bash
uv run --with fastembed python - <<'PY'
import json, sys
sys.path.insert(0, "scripts")
from cluster import embed, cosine_distance

SLUG, DATE = "<slug>", "<DATE>"
old = json.load(open(f"runs/{SLUG}/clusters.json"))
new = json.load(open(f"runs/{SLUG}/rescan-{DATE}/clusters.json"))

if old.get("backend") != new.get("backend"):
    sys.exit(f"STOP: backends differ ({old.get('backend')} vs {new.get('backend')}); "
             "clusterings are not comparable")

def recorded_cut(payload):
    for h in payload.get("source_health", []):
        if h.get("source") == "clustering" and "cut " in h.get("detail", ""):
            try:
                return float(h["detail"].split("cut ")[1].split("=")[1].split(",")[0])
            except (IndexError, ValueError):
                pass
    return None

CUT = recorded_cut(old) or recorded_cut(new)
if CUT is None:
    sys.exit("STOP: no recorded cosine cut in either clusters.json; do not hardcode one")

O, N = old["clusters"], new["clusters"]
eo, en = old.get("evidence_count"), new.get("evidence_count")
vecs = embed([c["canonical"] for c in O] + [c["canonical"] for c in N])  # ONE call
ov, nv = vecs[:len(O)], vecs[len(O):]
D = [[cosine_distance(a, b) for b in nv] for a in ov]

best_o = [min(range(len(N)), key=lambda j: D[i][j]) for i in range(len(O))] if N else []
best_n = [min(range(len(O)), key=lambda i: D[i][j]) for j in range(len(N))] if O else []

def share(c, total):
    return round(c["member_count"] / total, 4) if total else None

matches, seen_o, seen_n = [], set(), set()
for i, j in enumerate(best_o):
    if best_n[j] == i and D[i][j] <= CUT:          # mutual nearest neighbour, inside the cut
        a, b = O[i], N[j]
        dm = b["member_count"] - a["member_count"]
        da = b["distinct_authors"] - a["distinct_authors"]
        dc = b["distinct_communities"] - a["distinct_communities"]
        matches.append({
            "old_id": a["cluster_id"], "new_id": b["cluster_id"],
            "match_distance": round(D[i][j], 3),
            "loose": D[i][j] > 0.75 * CUT,
            "old_canonical": a["canonical"], "new_canonical": b["canonical"],
            "canonical_changed": a["canonical"] != b["canonical"],
            "members": [a["member_count"], b["member_count"], dm],
            "authors": [a["distinct_authors"], b["distinct_authors"], da],
            "communities": [a["distinct_communities"], b["distinct_communities"], dc],
            "engagement": [a["engagement_sum"], b["engagement_sum"],
                           b["engagement_sum"] - a["engagement_sum"]],
            "share": [share(a, eo), share(b, en)],
            "author_share_of_growth": round(da / dm, 2) if dm > 0 else None,
            "old_cell_ids": a["cell_ids"], "new_cell_ids": b["cell_ids"],
            "new_exemplar_urls": b["exemplar_urls"][:3],
        })
        seen_o.add(i); seen_n.add(j)

within_o = {i: [j for j in range(len(N)) if D[i][j] <= CUT] for i in range(len(O))}
within_n = {j: [i for i in range(len(O)) if D[i][j] <= CUT] for j in range(len(N))}

payload = {
  "cut": CUT, "backend": old.get("backend"),
  "evidence_count": {"old": eo, "new": en},
  "matches": matches,
  "vanished": [{"old_id": O[i]["cluster_id"], "canonical": O[i]["canonical"],
                "member_count": O[i]["member_count"],
                "distinct_authors": O[i]["distinct_authors"],
                "distinct_communities": O[i]["distinct_communities"],
                "cell_ids": O[i]["cell_ids"], "exemplar_urls": O[i]["exemplar_urls"][:3],
                "nearest_new": (O[i] and N and {"id": N[best_o[i]]["cluster_id"],
                                "canonical": N[best_o[i]]["canonical"],
                                "distance": round(D[i][best_o[i]], 3)}) or None}
               for i in range(len(O)) if i not in seen_o],
  "new": [{"new_id": N[j]["cluster_id"], "canonical": N[j]["canonical"],
           "member_count": N[j]["member_count"],
           "distinct_authors": N[j]["distinct_authors"],
           "distinct_communities": N[j]["distinct_communities"],
           "engagement_sum": N[j]["engagement_sum"], "cell_ids": N[j]["cell_ids"],
           "exemplar_urls": N[j]["exemplar_urls"][:3],
           "nearest_old": (N[j] and O and {"id": O[best_n[j]]["cluster_id"],
                           "canonical": O[best_n[j]]["canonical"],
                           "distance": round(D[best_n[j]][j], 3)}) or None}
          for j in range(len(N)) if j not in seen_n],
  "splits": [{"old_id": O[i]["cluster_id"],
              "into": [N[j]["cluster_id"] for j in within_o[i]]}
             for i in within_o if len(within_o[i]) > 1],
  "merges": [{"new_id": N[j]["cluster_id"],
              "from": [O[i]["cluster_id"] for i in within_n[j]]}
             for j in within_n if len(within_n[j]) > 1],
}
out = f"runs/{SLUG}/rescan-{DATE}/cluster_match.json"
json.dump(payload, open(out, "w"), indent=2)
print(json.dumps({"cut": CUT, "matched": len(matches),
                  "new": len(payload["new"]), "vanished": len(payload["vanished"]),
                  "splits": len(payload["splits"]), "merges": len(payload["merges"]),
                  "out": out}, indent=2))
PY
```

Signatures come from `scripts/cluster.py`; if they differ, read it and adapt. **Never
substitute a lexical similarity for `embed()`** — lexical matching pairs clusters for
sharing nouns, which is precisely the false match this step exists to prevent. If
`embed()` raises, that is a stop, recorded in health, not a licence to eyeball the
matching.

Threshold discipline: the match threshold is the **run's own recorded cosine cut** — the
distance at which `cluster.py` already judged two phrasings to be the same pain. Do not
invent one. **Mutual nearest neighbour** is required, or two old clusters both claim the
same new cluster and one of them reads as "vanished." A match above `0.75 × cut` is
flagged `loose`: the pain may have re-shaped, so its weight delta is comparing two things
that are only nearly the same. Say `loose` in the report; do not silently average it in.

**Splits and merges are not new/vanished clusters.** A cut that split one pain into two
manufactures a vanished cluster (weight halved) *and* a new cluster in the same stroke.
Report them in their own section with the constituent ids and no weight verdict.

---

## R5. Assign verdicts — where a diff invites over-reading

Do this yourself; it is arithmetic plus two gates, and it is the interpretive core of the
command. **Print every threshold you use in the report header** (§3.3 convention: an
unstated threshold makes a read non-reproducible).

**Gate 1 — source health, checked before any verdict.** Compare the two health files.

**The two files are not the same shape and the read must not assume one.** `/prospect`
appends `runs/<slug>/source_health.json` as **JSONL** (one object per line — CONTRACTS
cross-cutting rule 5, and every appending agent uses `>>`), while the rescan copy is a
**JSON array** (R1 seeds it with `[]`). A bare `jq -r '.[] | …'` against the JSONL
original iterates each object's *values* and dies with
`Cannot index string with string "detail"` (exit 5). Read both shape-agnostically, or
gate 1 silently never runs and every negative delta ships unguarded:

```bash
sh_read() {  # accepts either JSONL or a JSON array
  jq -rs 'if length==1 and (.[0]|type=="array") then .[0] else . end
          | .[] | "\(.source)\t\(.status)\t\(.detail // "")"' "$1" | sort
}
sh_read runs/<slug>/source_health.json
sh_read runs/<slug>/rescan-<DATE>/source_health.json
```

If either read returns nothing at all, **that is a stop, not a clean bill of health** —
an unreadable or missing health file means you cannot tell a degraded source from a
solved pain, so every negative verdict is `unresolved (health file unreadable)`.

Any source that was `ok` in the original and is now `degraded`, `unavailable`, or
`skipped` **poisons every negative delta**. A vanished cluster is far more often a
degraded source than a solved pain: a rate limit, an archive gap, a subreddit gone
private, a 401. When that has happened, no cluster may be reported as *vanished* or
*shrank* — every negative verdict becomes `unresolved (source degraded: <source>,
<detail>)`, and the report says so at the top, not in a footnote.

Also compare corpus size (`cluster_match.json → evidence_count`). If the fresh corpus is
>25% smaller, banner it: **the diff is measuring capture volume, not the world.** Report
`share` alongside raw counts for every matched cluster — share is robust to corpus-size
change in a way member counts are not. Share is a normalization of one count, not a blend
of axes; it never becomes a ranking.

**Gate 2 — `--cells`.** With a partial recapture, only clusters whose `old_cell_ids` are
fully inside the captured set get a verdict. Everything else is
`unresolved (cells not recaptured)`. A cluster fed by an unsearched cell will always look
like it shrank.

**Verdicts (defaults; print them):** noise floor `|Δmembers| ≤ max(2, 10% of old
member_count)`.

| Verdict | Condition | What it means |
|---|---|---|
| `grew-broadly` | Δmembers above the floor, Δauthors > 0, Δcommunities ≥ 0, and `author_share_of_growth ≥ 0.4` | more people, in at least as many places. The only growth worth acting on. |
| `grew-narrowly` | Δmembers above the floor but `author_share_of_growth < 0.4`, or Δcommunities < 0 | one thread or one person. **Name the cause**: cite the highest-engagement new member URL from `new_exemplar_urls`. A viral post is attention, not demand. |
| `flat` | within the noise floor | the durable read; next to a flat retro-trend it is the persistent-flat signal holding up |
| `shrank` | Δmembers below −floor, **and** gate 1 clean | subject to everything below |
| `unresolved` | gate 1 or gate 2 tripped, or match `loose` | no verdict, with the reason |

**Name the viral post when you call something `grew-narrowly`.** Evidence `id` is a sha1
of source+url and is stable across runs (CONTRACTS §2), so the members added since the
baseline are a set difference on `member_ids` — no re-reading required:

```bash
RS=runs/<slug>/rescan-<DATE>
jq -r '.clusters[]|select(.cluster_id=="<old cid>")|.member_ids[]' runs/<slug>/clusters.json \
  > "$RS"/.baseline-<old cid>.ids
jq -r '.clusters[]|select(.cluster_id=="<new cid>")|.member_ids[]' "$RS"/clusters.json \
  | grep -vxF -f "$RS"/.baseline-<old cid>.ids > "$RS"/.added-<old cid>.ids
# the three loudest additions, with real URLs
jq -c '{id, url, author, community, score: (.engagement.score // null)}' "$RS"/evidence/*.jsonl \
  | grep -F -f "$RS"/.added-<old cid>.ids \
  | jq -s 'sort_by(-(.score // 0)) | .[0:3]'
```

If the added members are one thread by one author, quote the URL and say so. If they are
spread across authors and communities, that is `grew-broadly` and the count is real.

**Weight authors and communities over raw member count.** `distinct_authors` and
`distinct_communities` are the two guard fields the constitution built for exactly this
(§3.2): member count is the number of sentences, authors is the number of people,
communities is the number of places. A cluster that doubled its members while gaining one
author did not grow — a thread got popular. A cluster that gained four authors across two
new communities grew, even if the member delta is modest. The 0.4 ratio is the same one
§3.3 uses to demote repetition-heavy clusters; it is not a new number.

**On vanished clusters, be actively suspicious.** In order of likelihood: (1) the source
degraded, (2) the cut moved (`cut_drift`, or a split — check the `splits` array), (3) the
community moved platforms, (4) vocabulary changed and the stored matrix queries no longer
match how people phrase it, (5) the pain was actually solved. Only (5) is interesting and
it is the least likely. Print each vanished cluster with its old counts, its
`nearest_new` distance (a near-miss just above the cut is a re-shaped pain, not a
disappearance), and its `exemplar_urls` so the reader can click through and see whether
the posts still exist.

**Never write "the pain is gone," "nobody is complaining anymore," or "solved."** Those
are claims about the world. "cluster c04 has no match above the cut in this capture" is a
claim about the run. Confusing the two is the single most damaging bug this plugin can
have, in miniature.

---

## R6. Retro-trend re-run, per matched cluster (gate: `cluster_match.json` exists; skipped if `--no-trends` or elapsed <30d)

Eligible clusters: matched, not `loose`, with a stored card at
`runs/<slug>/cards/<cid>.json` whose `retro_trend` has a non-null `shape` **or**
`slope_pct_per_year` (nothing to diff otherwise), capped at `--top N` by the original
run's contract sort order. Skipping the rest is the point — this is drift detection, not
re-diligence.

**Preserve the baseline before delegating.** The historian read-modify-writes
`cards/<cid>.json`; pointing it at the original would overwrite the very `retro_trend` you
are diffing.

```bash
cp runs/<slug>/cards/<cid>.json runs/<slug>/rescan-<DATE>/cards/<cid>.json
# baseline integrity check, run again after R6:
shasum runs/<slug>/cards/*.json > runs/<slug>/rescan-<DATE>/cards-baseline.sha
```

**One shared GitHub series for the whole rescan, launched first, in the background.**
`gh_history.py` is unauthenticated at 10 req/min and paces at ~6.5s; the limit is
per-IP, so parallel historians each running GitHub will collide and circuit-break.
Repos accumulate per *space*, not per cluster, and `skills/retro-trends` explicitly
permits a space-level series carried with a note.

Terms: take the GitHub-side keyphrases already recorded in the eligible cards'
`retro_trend.note` and use the two that recur most. Do not coin new ones — a new
solution-side term is a new series, and the two-curve read would silently change question.

```bash
jq -r '.retro_trend.note' runs/<slug>/cards/*.json | grep -o 'github: .*'   # recorded terms
uv run scripts/gh_history.py --terms "<space noun 1>" --terms "<space noun 2>" \
  --years 5 --out runs/<slug>/rescan-<DATE>/trends/space-github.json
```

Then launch **`historian` subagents in parallel, capped at 3 concurrent** (one per
eligible cluster; parallel because HN Algolia, Arctic Shift, and Google Trends are
distinct hosts, capped because Trends drives a local headless Chrome at 15–40s per load).
Task prompt per cluster:

> slug `<slug>`, cluster `<old cid>`. **Reuse these keyphrases exactly, per source**, as
> recorded in the stored card's `retro_trend.note`: `<verbatim quoted phrases>`. Do not
> re-derive any of them. Subreddits: `<union of matrix[].subreddits for the cluster's
> old cell_ids>`.
> Write payloads to `runs/<slug>/rescan-<DATE>/trends/<cid>-<source>.json` and patch
> `runs/<slug>/rescan-<DATE>/cards/<cid>.json`. **Never touch `runs/<slug>/cards/`.**
> Append health to `runs/<slug>/rescan-<DATE>/source_health.json` — **that file is a JSON
> array**, not the JSONL the run root uses, so merge with `jq '. + [$e]'` into a temp file
> and `mv`; a bare `>>` line would leave it unparseable and R8's comparison would lose it.
> **Skip GitHub** — a shared space-level series is at
> `runs/<slug>/rescan-<DATE>/trends/space-github.json`; merge it as the `github` series
> with a note that it is space-level and not comparable to the original per-cluster
> GitHub series. Use it for the two-curve read only.
> `runs/<slug>/rescan-<DATE>/clusters.json` uses fresh positional ids that do **not**
> correspond to card ids — you do not need it.

**If the stored `retro_trend.note` does not carry the keyphrases** (a run predating that
discipline), report `slope: [unknown] — no recorded keyphrases; a re-derived phrase is a
new series, not a delta` and skip that cluster. Re-deriving would produce a confidently
shaped curve about a different question and print it as movement.

**Diff `shape` and `slope_pct_per_year`** from the stored card against the rescan card.
The interpretation rules are the ones the elapsed clock allows, and no more:

- **The 5-year window is anchored to the run date and slides.** Every rescan drops the
  oldest bucket off the back and adds a new one at the front, which moves the slope on its
  own. Compare `params` in `runs/<slug>/trends/<cid>-<source>.json` against the fresh
  payload, and name the dropped bucket in the report when the window moved.
- **A shape may be reported as changed only when `elapsed_days` ≥ one full bucket of that
  series' granularity** — 183 days for HN and Trends (`5y`), 365 for Reddit. Below that,
  both runs share every complete bucket but one and the flip is arithmetic. Print it as
  `shape flip (within-bucket, not a trend change): persistent-flat -> declining`.
- **`|Δslope| ≤ 15` percentage-points/yr is noise**, the same platform-drift band
  `retro-trends` uses to refuse to call a slope a trend. Report the number; do not call it
  movement.
- Carry each series' `coverage` through unchanged. **A slope that moved because coverage
  fell from `good` to `thin` is a measurement change, not a trend change** — and because
  every defect in that stage degrades *toward* `persistent-flat`, a rescan that quietly
  lost coverage will hand you the underserved read for free and wrong.

---

## R7. Optional — card the new clusters (only with `--card-new`)

Off by default. A new cluster arrives with no `frequency`, `intensity`, `wtp`, or
`skeptic`, and building those is the full `/prospect` fan-out. Default behavior: report
the new cluster with its raw counts and `exemplar_urls`, and tell the user how to get a
card.

With `--card-new N`, for the N largest new clusters only: `distiller` first (it writes
`canonical_pain`, `provenance`, `frequency`, `intensity`, `quadrant`, `inventory_gate`),
then `economist`, `skeptic`, and `historian` **in parallel** — sequential-then-parallel
because the later three each patch one independent key of a card that must already exist,
and running them concurrently is safe only because none of them writes another's panel.
Fan out only over cards whose `inventory_gate.verdict` is `"pass"`; an `"exclude"` card
keeps its `null` panels, exactly as in `/prospect`.

`saturation` has no agent owner (§3.8 rider) — it is yours. Join the scouts' staged
`rescan-<DATE>/evidence/.staging/saturation-<cell_id>.json` to each new card through
`provenance.cell_ids`, carrying `source`, `competitor_count`, `trend_direction` and `read`
in the tool's own vocabulary. No read for any of the cluster's cells → `"saturation": null`
plus a health entry. **Never `competitor_count: 0` for a lookup that failed.**

Hand each subagent the **rescan** paths, not the original: clusters at
`runs/<slug>/rescan-<DATE>/clusters.json`, evidence at `runs/<slug>/rescan-<DATE>/evidence/`,
card at `runs/<slug>/rescan-<DATE>/cards/<new cid>.json`, health at
`runs/<slug>/rescan-<DATE>/source_health.json` (a JSON **array** here, not the run root's
JSONL — tell them, or their entries land unparseable). **Never** into `runs/<slug>/cards/`: these
were built from a different corpus, and mixing them destroys the audit trail of the
original run. Everything in `skills/prospect-methodology` §3.3–3.7 still applies,
including the mandatory skeptic, `under_researched`, and the no-inventory gate. Run this
after R6, not alongside it — both stages contend for the same unauthenticated GitHub
budget.

---

## R8. Write the report — `runs/<slug>/rescan.md` (CONTRACTS §9)

`rescan.md` is the contract path and holds the latest diff. Immediately copy it to
`runs/<slug>/rescan-<DATE>.md` so repeated rescans accumulate instead of overwriting each
other, and so R0's listing can show when the run was last rescanned:

```bash
cp runs/<slug>/rescan.md runs/<slug>/rescan-<DATE>.md
```

Sections in this order; the health comparison comes first because it gates the reading of
everything below it.

1. **Header.** slug · original run date · rescan date · `elapsed_days` (and gap since the
   previous rescan) · corpus size old → new · matching method (`embedding proximity of
   canonical phrasings, mutual nearest neighbour, cut = <CUT> from the original run`) ·
   `cut_drift` if any · every threshold used (noise floor, 0.4 author-share, 15pp slope
   band, bucket-granularity rule) · active flags · unrecognized flags · what was skipped
   and why (`--no-trends`, elapsed <30d, `--cells`).
2. **Source health, both runs.** Per-source `ok/degraded/unavailable/skipped` side by
   side, with details. Then one line: whether negative deltas are trustworthy at all.
3. **Cluster weight deltas** — matched clusters only. Columns: old→new `member_count`,
   `distinct_authors`, `distinct_communities`, `engagement_sum`, `share`, verdict,
   `match_distance`, `loose`. Show both canonicals when `canonical_changed` is true (a
   re-worded medoid is not a new pain). Sort by |Δauthors| descending, and **say that is
   the sort key** — authors over members is the whole interpretive stance of this report.
4. **New clusters.** Counts, `cell_ids`, `exemplar_urls`, and `nearest_old` distance. A
   "new" cluster whose nearest old neighbour sits just above the cut is a re-shaped old
   pain; say so rather than announcing a discovery.
5. **Vanished clusters.** Old counts, `nearest_new` distance, `exemplar_urls`, and the
   ranked list of likelier explanations from R5. Never phrase as solved.
6. **Splits and merges.** Constituent ids, no weight verdict.
7. **Slope and shape changes.** Per cluster: stored → fresh `shape`, stored → fresh
   `slope_pct_per_year`, the source the slope came from, both coverages, the keyphrases
   (verbatim, proving they did not change), and whether the elapsed clock permits calling
   it a change. Any window slide named explicitly.
8. **What did not move.** Flat is a finding — persistent-flat pain that is still
   persistent-flat one quarter later is the read strengthening, not nothing happening.

Rules for the whole report: **no drift score, no blended number, no re-ranking of the
original cards.** Every claim carries counts and at least one resolvable URL. Unknowns are
`[unknown]` or `null`. A failed source is a failed source, never an absence of discussion.

Close your reply to the user with: both report paths (`rescan.md` and the dated copy), the counts
(matched / new / vanished / split / merged / trends re-run), the one-line health verdict,
and the three largest author-deltas. Say plainly that this is drift detection — if a
cluster moved enough to matter, the next step is `/prospect` or `/diligence`, not this
command.
