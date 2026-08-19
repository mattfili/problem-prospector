#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["mcp>=1.9,<2"]
# ///
"""problem-prospector's pain-point search, exposed as MCP tools.

WHY A TOOL SURFACE AND NOT ANOTHER PROSE COMMAND
------------------------------------------------
The pain-search stages were carried as ~40KB of prose across
`commands/prospect.md` and `agents/{scout,distiller}.md`, and the rules that
matter most were the ones a model can skip with no error appearing: never pass
`--min-score`, always pass `--comments`, never capture an out-of-enum source,
never set a marker without a verbatim quote, never blend two axes into one
number. Prose asks a model to remember. A tool schema makes the wrong call
unrepresentable — there is no `min_score` parameter to pass, no source value
outside the queryable enum to choose, and no `score` field on the intensity tool
to assert, because the score is derived from evidence on disk.

WHAT THIS COVERS
----------------
Stages 0b-3 of `/prospect`: frame, capture, merge, the thin-capture stop,
clustering, the frequency panel, the inventory gate, the intensity panel, and the
report. It stops where the expensive half starts. A run left here is a legal
Stage-3-complete run — `/prospect "<same inspiration>"` resumes it at Stage 3.5
and never re-captures.

Judgment stays with the caller: the frame, which quote evidences which marker,
the canonical pain sentence, and the inventory-gate verdict. Arithmetic and
enforcement are here.

RUN IT
------
    uv run --quiet scripts/pain_mcp.py          # stdio MCP server

Key-free throughout: every tool routes to the guaranteed script fallback, so it
behaves identically in a host that refuses to spawn the opportunistic MCPs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402

# Aliased away from the `pain_*` namespace on purpose: a tool named `pain_report`
# would otherwise rebind the module of the same name, and every call that reached
# through it would raise AttributeError at runtime with a clean import.
import pain_capture as capture_stage  # noqa: E402
import pain_cards as cards_stage  # noqa: E402
import pain_intensity as intensity_stage  # noqa: E402
import pain_report as report_stage  # noqa: E402
import pain_stages as stages  # noqa: E402

mcp = FastMCP("problem-prospector-pain")

#: Schema-level enums. These are the point of the exercise: an out-of-contract
#: source, a third gate verdict, or a health status only a capture script may
#: report are not merely rejected at runtime — they cannot be expressed.
QueryableSource = Literal["hackernews", "stackoverflow", "producthunt"]
ContractSource = Literal[
    "reddit", "hackernews", "stackoverflow", "producthunt", "github",
    "pypi", "npm", "wikipedia", "google-trends", "dialog",
]
DecisionStatus = Literal["skipped", "degraded"]
GateVerdict = Literal["pass", "exclude"]


# --------------------------------------------------------------------------
# Run lifecycle
# --------------------------------------------------------------------------

@mcp.tool()
def pain_run_create(
    inspiration: str,
    matrix: list[dict],
    niche: str | None = None,
    top: int = 5,
) -> dict:
    """Open a pain-search run: validate the permutation matrix and write inputs.json.

    Call this first. `inspiration` is the user's hunch, verbatim — it derives the
    run slug, so it must not be reworded. `matrix` is 6-12 cells spanning
    {personas} x {verticals} x {problem framings}; a spanning set, never a cross
    product. Each cell: `cell_id` (`m01`, `m02`, ...), `persona`, `vertical`,
    `framing`, `queries` (3-6, in the *complainer's* vocabulary, not a vendor's),
    `subreddits` (names, `r/` optional).

    Two composition requirements the matrix must satisfy, checked by you and not
    by this tool, because they are judgment: at least one buyer persona AND one
    sufferer-who-cannot-buy (without the contrast the `complainer_is_buyer` marker
    has no discriminating power), and at least one inverted or adversarial framing.

    `niche` constrains and extends the vertical axis; it never replaces
    generation. Every named niche appears in at least one cell AND generation
    continues into adjacent verticals the user did not name — three niches
    becoming three cells turns an exploration tool into a confirmation tool.

    Refuses on a failing shape gate rather than letting capture run against a
    half-written frame, and refuses to replace an existing frame for the same slug
    because that would detach every captured record's `cell_id` from its meaning.
    """
    return stages.create_run(inspiration, matrix, niche=niche, top=top)


@mcp.tool()
def pain_run_status(slug: str) -> dict:
    """Where this run stands and which tool to call next. Read-only, always safe.

    Walks the pain-search gates in order and reports the first that does not hold,
    with counts: staged vs merged evidence, items per source, the thin-capture
    verdict, cluster count, which clusters still need an inventory gate, which
    still need an intensity panel, whether the report is written. Call it after a
    crash, mid-capture, or whenever you are unsure what has already run — nothing
    here re-runs a satisfied stage and nothing here writes.
    """
    return report_stage.run_status(slug)


# --------------------------------------------------------------------------
# Stage 2 — capture
# --------------------------------------------------------------------------

@mcp.tool()
def pain_capture_reddit(
    slug: str,
    cell_id: str,
    query: str | None = None,
    subreddits: list[str] | None = None,
    concurrent_captures: int = 4,
    retry_at_limit_50: bool = False,
) -> dict:
    """Capture Reddit posts AND their comments for one cell and one query.

    Reddit is captured for every cell with no relevance test — it is the only
    source that is always right. Run one call per query in the cell, using the
    query strings from `inputs.json` verbatim: a query that returns nothing is a
    zero-result finding, not an invitation to invent better vocabulary, and
    substituting one makes the run unreproducible.

    Comments are always captured and cannot be turned off. The `time_quantified`,
    `workaround_built` and `money_loss` markers almost always live in comments
    rather than the post, so a title-only capture systematically produces
    intensity 2 and looks like a real finding.

    There is deliberately no score floor, date window, or limit parameter. A score
    floor is the forbidden capture filter and would delete the newest evidence
    first (the archive snapshots score at ingest, so posts under ~2 days old read
    score=1); date windowing belongs to the retro-trend stage; and a lowered limit
    truncates the frequency denominator, which is the number every later stage
    trusts most. `retry_at_limit_50` is the one sanctioned deviation: a single
    retry for a subreddit whose comment pulls 422'd.

    Pass `concurrent_captures` = how many captures you are running at once. The
    archive wants >=1.2s between requests per IP, shared across every process, so
    the pacing multiplier has to know about its siblings. It is floored at 2 even for
    a lone capture, because a single capture at the bare interval was observed
    drawing a throttle. On a "slow down" timeout the script walks a limit ladder
    (100 -> 50 -> 25 -> 10) with doubling backoff before giving up; a 403 or 429 is
    circuit-broken on sight and never retried.

    Omit `query` only for a subreddit whose entire topic *is* this cell's vertical;
    for a broad sub the latest 100 posts are mostly unrelated noise that clusters.
    """
    return capture_stage.capture_reddit(
        slug, cell_id, query, subreddits or [], concurrent_captures, retry_at_limit_50
    )


@mcp.tool()
def pain_capture_trends(
    slug: str, cell_id: str, source: QueryableSource, query: str, limit: int = 30
) -> dict:
    """Keyword-capture one cell from `hackernews`, `stackoverflow`, or `producthunt`.

    These three are the only sources that are both in the evidence contract's
    closed enum and keyword-searchable. Apply the relevance test before calling —
    Hacker News for a technical, dev-adjacent or founder-adjacent persona and
    wrong for clerks, nurses and contractors; Stack Overflow when the pain has a
    code/API/data-format surface and wrong when it is procedural or
    organizational; Product Hunt for existing-spend evidence, never for pain
    language (it is vendor copy), and it was 403 at the origin when last probed.

    Why the test matters: an irrelevant source does not return zero, it returns
    lexically similar noise, which then clusters, inflates the cluster's member
    count, and corrupts the frequency signal. Skipping a source costs nothing —
    record the skip with `pain_record_source_decision` and move on.

    `github`, `pypi`, `npm`, `wikipedia` and `google-trends` are in the enum but
    have no keyword search: they return a global trending feed unrelated to your
    framing that would still cluster. This tool refuses them. Record them as
    `degraded` instead and leave ecosystem history to the retro-trend stage.
    Reddit is refused too — `pain_capture_reddit` already captures it deeper, and a
    second shallow copy under a different id recipe inflates cluster weight with
    the same posts.
    """
    return capture_stage.capture_trends(slug, cell_id, source, query, limit)


@mcp.tool()
def pain_ingest_records(
    slug: str,
    cell_id: str,
    records: list[dict],
    query: str | None = None,
) -> dict:
    """Stage Reddit evidence YOU captured from the `dialog` MCP. Contract-checked here.

    Use this only for `dialog`. This server cannot call another MCP server's tools,
    so when `dialog` is available you call it yourself — semantic subreddit discovery
    plus full comment trees with citations, which beats the Arctic Shift archive — and
    hand the results here to be shaped, validated, and staged. When `dialog` is absent
    or 401s, use `pain_capture_reddit` instead and do not narrate the failure.

    Each record: `url` (a real Reddit permalink, never constructed), `title`, `text`
    (**verbatim** — truncation is allowed, rewording is not), `author`, `community`,
    `engagement` (an object like `{"score": 412, "comments": 88}`, or `null` if
    dialog did not report it — never `0`, which claims something the source didn't),
    and `created_utc`.

    Do not send an `id`: it is computed from `source + url` with the contract recipe.
    That matters more than it looks — a second id recipe for the same post is how one
    pain gets counted twice, and `pain_merge_staging` relies on this one to collapse
    posts that Arctic Shift also captured.

    Any record that fails validation is returned by index with a reason and **nothing
    is staged** until the batch is clean. If dialog errored rather than returning
    nothing, call `pain_record_source_decision` — an empty batch here is recorded as
    "searched and found nothing", which would be a false claim about the world.
    """
    return capture_stage.ingest_records(slug, cell_id, query, records)


@mcp.tool()
def pain_capture_saturation(slug: str, cell_id: str, idea: str) -> dict:
    """Take the first saturation read for one cell — competitor count and direction.

    `idea` is this cell's framing as one sentence. The result is staged to a
    sidecar, never to evidence: there is no saturation source in the evidence
    enum, and a blob of competitor marketing copy would cluster as if it were
    pain. A pain-search run leaves it staged; `/prospect` Stage 5 joins it onto
    cards when the run continues.

    Carry the tool's own wording for the read — never coin a saturation adjective.
    If both paths fail the count stays null: `competitor_count: 0` is a claim that
    nobody is building here, and writing it because a lookup failed is the
    failure-as-absence bug in its purest form.
    """
    return capture_stage.capture_saturation(slug, cell_id, idea)


@mcp.tool()
def pain_record_source_decision(
    slug: str,
    cell_id: str,
    source: ContractSource,
    status: DecisionStatus,
    reason: str,
) -> dict:
    """Record a deliberate decision NOT to capture a source, so the skip is on record.

    `status` is `skipped` — the relevance test ruled it out for this cell (e.g.
    "non-technical buyer; no library workaround surface") — or `degraded` —
    relevant but structurally uncapturable per-cell, which is the correct status
    for a trending-only source like `npm` on a dev-tool framing.

    Neither is a failure and both are decisions rather than gaps. `ok`,
    `unavailable` and `searched-no-results` are not accepted here: those describe
    what a source *did*, and only the capture tools can report that. Keeping "we
    could not look", "we looked and found nothing" and "we chose not to look"
    apart is what stops a rate limit from becoming the conclusion that nobody is
    complaining.
    """
    return capture_stage.record_source_decision(slug, cell_id, source, status, reason)


@mcp.tool()
def pain_merge_staging(slug: str) -> dict:
    """Merge staged captures into the contract evidence paths, deduping on id.

    Call once after each wave of captures has returned. Captures stage per-cell
    because parallel appends to one file produce interleaved half-lines that the
    clusterer rejects — usually discovered twenty minutes later. Idempotent and
    safe to re-run; staging files are never deleted, because they already cost
    rate limit and every capture script dedupes against its own output.

    Reports every malformed line dropped rather than swallowing it. A dropped
    health line is a lost degradation record, which is the one thing that file
    exists to preserve.
    """
    return stages.merge_staging(slug)


@mcp.tool()
def pain_capture_gate(slug: str) -> dict:
    """The thin-capture stop: is there enough evidence to cluster at all?

    Returns `decision`: `proceed` or `stop`. (Not `verdict` — that word belongs to
    the inventory gate's `pass`/`exclude`, and one field name for two enums is how
    drift starts.) Stops under 40 items or fewer than three sources
    returning anything — clustering 11 posts yields clusters of size 2 rendered
    with exactly the same confident formatting as clusters of size 47.

    On a stop, widen the matrix or revise the queries into complainer vocabulary
    and capture again; do not cluster anyway. The result names sources that
    *failed* separately from queries that ran and *found nothing*, because those
    are different findings and collapsing them inverts the run's conclusion.
    """
    return stages.capture_gate(slug)


# --------------------------------------------------------------------------
# Stage 3 — cluster, gate, score, report
# --------------------------------------------------------------------------

@mcp.tool()
def pain_cluster(
    slug: str,
    percentile: float | None = None,
    min_cluster_size: int | None = None,
    reseed: bool = False,
) -> dict:
    """Cluster the evidence and write one card per cluster with its frequency panel.

    After this the cluster is the unit of analysis and never the raw post: 400
    phrasings of one pain is one cluster of weight 400, not 400 signals. Local
    embeddings, no keys, no network model calls.

    The frequency panel is computed, not judged — thresholds, the repetition
    demotion when distinct authors run below 40% of members, the echo-chamber cap
    on a single-community cluster, and the engagement promotion (medium to high
    only, and never on its own) all run here, and the thresholds actually used are
    recorded for the report header. Intensity is left null for
    `pain_score_intensity`.

    `percentile` controls the cut: lower means more, tighter clusters (default 35;
    evidence captured against one inspiration sits in a narrow band, so 10-25
    often separates adjacent pains that 35 fuses). If the cut looks wrong,
    re-cluster at a different percentile — never hand-merge or hand-split
    clusters, which leaves the recorded cut basis lying about what produced the
    shape. Re-clustering changes every cluster id and discards any intensity
    already scored, so it needs `reseed=true`.
    """
    return cards_stage.cluster_and_seed_cards(slug, percentile, min_cluster_size, reseed)


@mcp.tool()
def pain_inventory_gate(
    slug: str, cluster_id: str, verdict: GateVerdict, flags: list[str]
) -> dict:
    """Record the no-inventory-gate verdict for one cluster: `pass` or `exclude`.

    Exclude a cluster whose solution requires holding, shipping, or manufacturing
    physical goods — the exclusion is a scope rule, not a judgment that the pain
    is unreal. `flags` carries the reason on an exclusion (required) and any
    noted-but-not-disqualifying friction on a pass (long procurement cycle,
    licensure-adjacent, and so on).

    Must be set on every cluster before intensity is scored, and the two verdict
    spellings are load-bearing: downstream stages preflight on `exclude`, so
    `excluded` in that field would silently send them to work on a card the gate
    killed. An excluded card keeps a null intensity panel by design and still
    appears in the report's own section — visible and unranked, never deleted.
    """
    return cards_stage.set_inventory_gate(slug, cluster_id, verdict, flags)


@mcp.tool()
def pain_score_intensity(
    slug: str,
    cluster_id: str,
    marker_evidence: dict[str, list[dict]],
    canonical_pain: str | None = None,
) -> dict:
    """Score one cluster's pain intensity from quoted evidence. You supply quotes,
    not a score — the score is derived and returned with its full derivation.

    `marker_evidence` maps marker names to the quotes that evidence them:

        {"money_loss": [{"quote": "we paid 4k in late fees", "url": "https://..."}],
         "time_quantified": [{"quote": "three hours every Monday", "url": "https://...",
                              "recurring": true}],
         "complainer_is_buyer": [{"quote": "I approve the invoices", "url": "https://..."}]}

    The six markers, and what counts as present: `money_loss` (a number, a vendor,
    or a named loss — "it's expensive" does not count), `time_quantified` (a
    quantity with a period — "it takes forever" does not count), `workaround_built`
    (they *constructed* something — wishing for a tool is not a workaround),
    `abandonment` (they stopped; of a paid tool is strongest), `profanity_urgency`
    (weakest, corroborating only), `complainer_is_buyer` (holds or directly
    influences budget — the highest-signal marker, because it is the only one that
    connects pain to a purchase order).

    Set `recurring: true` on a cost-marker quote carrying a *recurring* quantified
    cost — hours per week, dollars per month, dedicated headcount. That is the
    extra leg the top of the scale requires.

    Every quote is validated against the evidence on disk: at most 15 words, a URL
    belonging to a record in *this* cluster, and the text appearing verbatim in
    that record's captured title or body. Authors are resolved from disk, so the
    distinct-author counts that carry the upper levels cannot be asserted — and a
    quote from a deleted account evidences its marker but contributes no author.

    If any single quote fails validation, nothing is written and every rejection is
    returned with its reason. A panel scored from whichever quotes happened to
    survive would be indistinguishable from a clean one.

    `canonical_pain` optionally rewrites the cluster's machine-picked label into
    the operator's own frame. Requires the inventory gate to have run first.
    """
    return intensity_stage.score_intensity(slug, cluster_id, marker_evidence, canonical_pain)


@mcp.tool()
def pain_report(slug: str) -> dict:
    """Render `runs/<slug>/pain-clusters.md` — the pain-search report.

    Prints the active sort key verbatim, the counts, the frequency thresholds
    actually used, and one line of source health, then the ranked clusters with
    their two axes side by side and the quotes behind every marker. Excluded and
    unscored clusters get their own visible sections: unscored is not
    low-intensity, and excluded is not deleted.

    No composite anywhere — no opportunity score, no weighted sum, no tiers. The
    two axes stay separate because their combination is the finding: high
    frequency with low intensity is a content play rather than a product, and high
    intensity with no proven buyer is a sad hobby. This is the last tool of a
    pain-search run.
    """
    return report_stage.render_report(slug)


if __name__ == "__main__":
    mcp.run()
