#!/usr/bin/env python3
"""Stage 2 of the pain-point search: capture, with the forbidden levers removed.

WHY THIS IS A SEPARATE MODULE
-----------------------------
Capture is the one stage whose mistakes are invisible downstream. A score floor,
a truncated limit, a title-only pull, an out-of-enum source: the run still
completes, the cards still render with the same confident formatting, and the
numbers are wrong with nothing on disk to reveal it. So the rules live in the
call signatures here rather than in prose a model reads once — `capture_reddit`
has no parameter that can express `--min-score`, and `capture_trends` has no
source value outside the queryable enum.

Capture is volume and fidelity. Taste is a later stage's job: nothing here drops
an item for being boring, unpopular, off-tone or already said. The boring posts
are the denominator — frequency is only meaningful over an unfiltered corpus, and
400 phrasings of one pain is the finding, not the waste.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from pain_stages import (
    DECISION_STATUSES,
    EVIDENCE_SOURCES,
    QUERYABLE_SOURCES,
    TRENDING_ONLY_SOURCES,
    append_health,
    invoke,
    read_jsonl,
    run_dir,
)

#: Minimum politeness multiplier on the Reddit host interval, regardless of how
#: few captures are in flight. Observed live: a single capture at politeness 1.0
#: (a 1.2s interval) drew `HTTP 422: Timeout. Maybe slow down a bit` from Arctic
#: Shift, and pullpush then 429'd, so the cell captured nothing. Two is the floor
#: because the cheapest fix for a throttle is to not be at the edge of it.
POLITENESS_FLOOR = 2


# --------------------------------------------------------------------------
# Stage 2 — capture
# --------------------------------------------------------------------------

def _staging(slug: str, name: str) -> Path:
    path = run_dir(slug) / "evidence" / ".staging" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def capture_reddit(
    slug: str,
    cell_id: str,
    query: str | None,
    subreddits: list[str],
    concurrent_captures: int = 4,
    retry_at_limit_50: bool = False,
) -> dict:
    """Reddit capture for one cell and one query, via Arctic Shift. Always.

    Reddit is the only source with no relevance test (§3.1). Comments are always
    captured — `time_quantified`, `workaround_built` and `money_loss` almost
    always live in comments, and a title-only capture produces intensity 2 and
    looks like a finding.

    Three of `reddit_search.py`'s flags are deliberately unreachable from here:
    `--min-score` (the forbidden capture filter; Arctic Shift snapshots score at
    ingest, so a floor deletes the newest evidence first), `--after`/`--before`
    (historical windowing belongs to the retro-trend stage), and a free `--limit`
    (truncating the pull truncates the frequency denominator). `retry_at_limit_50`
    is the one sanctioned deviation: a single retry for a subreddit whose comment
    pulls 422'd.
    """
    out = _staging(slug, f"reddit-{cell_id}.jsonl")
    args = [
        "--subreddits", ",".join(s.lstrip("r/").strip("/") for s in subreddits),
        "--limit", "50" if retry_at_limit_50 else "100",
        "--comments", "--comments-per-post", "10", "--comments-max-posts", "25",
        "--politeness", str(float(max(POLITENESS_FLOOR, concurrent_captures))),
        "--cell-id", cell_id, "--out", str(out),
    ]
    if query:
        args += ["--query", query]
    result = invoke("reddit_search.py", args)
    return _capture_result(slug, result, out, "reddit", cell_id, query)


def capture_trends(
    slug: str, cell_id: str, source: str, query: str, limit: int = 30
) -> dict:
    """Keyword capture from one queryable trend-pulse source, for one cell.

    Only `hackernews`, `stackoverflow` and `producthunt` are accepted: they are the
    intersection of the CONTRACTS §2 enum with trend-pulse's search-capable
    sources. The rest of the enum is trending-only, and a global trending feed
    dumped into evidence clusters as if it were pain — record those with
    `record_source_decision(status="degraded")` instead.

    Apply §3.1's relevance table before calling: an irrelevant source does not
    return zero, it returns lexically similar noise that inflates `member_count`
    and corrupts the one number the rest of the pipeline trusts most. Skipping a
    source costs nothing.

    `producthunt` is accepted but was 403 at the origin when last probed, and it
    is vendor copy rather than pain language — expect `unavailable`, not silence.
    """
    if source not in QUERYABLE_SOURCES:
        trending = ", ".join(TRENDING_ONLY_SOURCES)
        return {
            "ok": False, "source": source,
            "error": (
                f"{source!r} is not keyword-searchable. Queryable: "
                f"{', '.join(QUERYABLE_SOURCES)}. Trending-only (record as degraded, "
                f"do not capture): {trending}. Reddit is captured by capture_reddit only."
            ),
        }
    out = _staging(slug, f"{source}-{cell_id}.jsonl")
    result = invoke("trends_cli.py", [
        "--source", source, "--query", query, "--limit", str(limit),
        "--cell-id", cell_id, "--out", str(out),
    ])
    return _capture_result(slug, result, out, source, cell_id, query)


def capture_saturation(slug: str, cell_id: str, idea: str) -> dict:
    """The first saturation read for one cell, to a sidecar — never to evidence.

    There is no `idea-reality` value in the §2 source enum, and a blob of
    competitor marketing copy would cluster as if it were pain. `/prospect`
    Stage 5 joins this sidecar onto cards; a pain-search run leaves it staged.
    Both paths failing leaves saturation `null` — never a competitor count of 0,
    which is a claim that nobody is building here.
    """
    out = _staging(slug, f"saturation-{cell_id}.json")
    result = invoke("reality_cli.py", [
        "--idea", idea, "--cell-id", cell_id, "--out", str(out),
    ])
    health = list((result.get("payload") or {}).get("source_health") or [])
    if not result["ok"]:
        health.append({
            "source": "idea-reality", "status": "unavailable",
            "fallback": "reality_cli.py",
            "detail": result.get("error") or "no parseable payload",
        })
    append_health(slug, health)
    saturation = (result.get("payload") or {}).get("saturation")
    return {
        "ok": result["ok"], "cell_id": cell_id, "staged": str(out),
        "saturation": saturation, "health_entries": len(health),
        "stderr_tail": result.get("stderr_tail"),
    }


def _capture_result(
    slug: str, result: dict, out: Path, source: str, cell_id: str, query: str | None
) -> dict:
    """Shape one capture's outcome: counts, staged path, health, zero-result flag.

    A source that failed and a query that ran and found nothing are different
    findings, and this is where they are kept apart — the single most consequential
    branch in the module, because collapsing them turns a rate limit into the
    conclusion "nobody is complaining", which inverts the entire run.

    Zero items is therefore only a `searched-no-results` finding when *nothing
    failed*. If any attempted host reported `unavailable` — a 422, a 429, a
    circuit-break — those entries already say what happened and no zero-result
    line is added on top of them, because "we looked and found nothing" would be a
    false claim about the world. `zero_result` in the return follows the same rule,
    so a caller cannot read an outage as an empty search either.
    """
    payload = result.get("payload") or {}
    health = list(payload.get("source_health") or [])
    staged, _ = read_jsonl(out)
    failed = [
        f"{entry.get('source')}: {entry.get('detail')}"
        for entry in health if entry.get("status") == "unavailable"
    ]
    genuine_zero = result["ok"] and not staged and not failed

    if not result["ok"]:
        health.append({
            "source": source, "status": "unavailable", "fallback": None,
            "detail": (result.get("error") or "capture failed")
                      + (f" | stderr: {result['stderr_tail']}" if result.get("stderr_tail") else ""),
        })
    elif genuine_zero:
        health.append({
            "source": source, "status": "searched-no-results", "fallback": None,
            "detail": f"cell {cell_id} query {query!r} ran and returned nothing",
        })
    append_health(slug, health)

    return {
        "ok": result["ok"] and not failed,
        "source": source, "cell_id": cell_id, "query": query,
        "staged": str(out), "items_in_staging_file": len(staged),
        "zero_result": genuine_zero,
        "sources_failed": failed,
        "note": (
            "every attempted host failed, so zero items is NOT a finding about the "
            "world — do not report this as 'no discussion found'. Retry later or "
            "widen the subreddit list."
            if failed and not staged else None
        ),
        "totals": payload.get("totals") or payload.get("summary"),
        "health_entries": len(health),
        "stderr_tail": result.get("stderr_tail"),
    }


#: Hosts a dialog record may legitimately point at. dialog is a Reddit research
#: server, so an off-Reddit URL means a wrong or fabricated record.
DIALOG_HOSTS = ("reddit.com", "www.reddit.com", "old.reddit.com", "np.reddit.com")


def ingest_records(
    slug: str, cell_id: str, query: str | None, records: list[dict]
) -> dict:
    """Stage evidence the *client* captured from the `dialog` MCP, contract-checked here.

    WHY THIS TOOL EXISTS AT ALL
    ---------------------------
    An MCP server cannot call another MCP server's tools; the client holds both
    connections. So the `dialog` path physically cannot live inside
    `capture_reddit` the way the Arctic Shift path does — the agent calls dialog
    itself and hands the results here. That split is the whole reason this function
    is strict: records arriving from a model are the one capture path with no script
    guaranteeing their shape.

    So nothing is taken on trust. The `id` is computed with the CONTRACTS recipe
    rather than accepted, because two different id recipes for the same post is how
    one pain gets counted twice. URLs must be real Reddit permalinks. `engagement`
    must be an object or `null` — never a bare `0`, which is a claim about the world
    rather than about the source. Any record that fails is rejected by index with a
    reason, and a batch with rejections stages nothing.

    `source` is always `dialog`: every other source in the enum has a script that
    writes its own contract-shaped records, and accepting hand-written `reddit`
    evidence would open exactly the hole this validation closes.
    """
    from urllib.parse import urlparse

    try:
        cell_ids = [
            c.get("cell_id")
            for c in (json.loads((run_dir(slug) / "inputs.json").read_text())).get("matrix", [])
        ]
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"cannot read runs/{slug}/inputs.json: {exc}"}
    if cell_id not in cell_ids:
        return {"ok": False, "error": f"cell {cell_id} is not in this run's matrix {cell_ids}"}

    staged_now = int(datetime.now(timezone.utc).timestamp())
    prepared: list[dict] = []
    rejected: list[dict] = []
    seen_urls: set[str] = set()

    for index, record in enumerate(records or []):
        url = str(record.get("url") or "").strip()
        host = urlparse(url).netloc.lower()
        title = str(record.get("title") or "").strip()
        text = str(record.get("text") or "").strip()
        engagement = record.get("engagement")

        problem = None
        if not url.startswith("http"):
            problem = "url must be a real resolvable permalink, never constructed"
        elif host not in DIALOG_HOSTS:
            problem = (f"url host {host!r} is not Reddit; dialog is a Reddit source, so an "
                       "off-Reddit URL is a wrong or invented record")
        elif url.rstrip("/") in seen_urls:
            problem = "duplicate url within this batch"
        elif not title and not text:
            problem = "record carries neither title nor text; there is nothing to cluster"
        elif engagement is not None and not isinstance(engagement, dict):
            problem = ("engagement must be an object or null — a bare number cannot say "
                       "which counts it holds, and 0 is a claim the source did not make")

        if problem:
            rejected.append({"index": index, "url": url[:120], "rejected_because": problem})
            continue

        seen_urls.add(url.rstrip("/"))
        prepared.append({
            # Computed, never accepted: CONTRACTS §2's id recipe is sha1(source + url).
            "id": hashlib.sha1(f"dialog{url}".encode()).hexdigest(),
            "cell_id": cell_id,
            "source": "dialog",
            "url": url,
            "title": title or None,
            "text": text or None,
            "author": (str(record.get("author")).strip() or None)
                      if record.get("author") is not None else None,
            "community": (str(record.get("community")).strip() or None)
                         if record.get("community") is not None else None,
            "engagement": engagement,
            "created_utc": record.get("created_utc"),
            "captured_utc": staged_now,
            "query": query,
        })

    if rejected:
        return {
            "ok": False, "staged": 0, "rejected": rejected,
            "error": f"{len(rejected)} of {len(records or [])} record(s) failed the "
                     "evidence-shape check; nothing was staged. Each rejection names "
                     "its field and problem — fix or drop each one and call again.",
        }
    if not prepared:
        append_health(slug, [{
            "source": "dialog", "status": "searched-no-results", "fallback": None,
            "detail": f"cell {cell_id} query {query!r} returned no records via dialog",
        }])
        return {"ok": True, "staged": 0, "zero_result": True,
                "note": "recorded as searched-no-results; if dialog actually errored, "
                        "call pain_record_source_decision instead so it is not read as silence"}

    out = _staging(slug, f"dialog-{cell_id}.jsonl")
    with out.open("a", encoding="utf-8") as handle:
        for record in prepared:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    append_health(slug, [{
        "source": "dialog", "status": "ok", "fallback": None,
        "detail": f"cell {cell_id}: {len(prepared)} record(s) ingested from the dialog MCP",
    }])
    return {
        "ok": True, "staged": len(prepared), "zero_result": False,
        "path": str(out),
        "ids_computed": True,
        "note": "ids were computed from source+url, so pain_merge_staging will collapse "
                "any post that Arctic Shift also captured rather than double-counting it",
    }


def record_source_decision(
    slug: str, cell_id: str, source: str, status: str, reason: str
) -> dict:
    """Record a deliberate decision *not* to capture a source, on the record.

    `skipped` — the §3.1 relevance table ruled it out for this cell. `degraded` —
    relevant but structurally uncapturable per-cell (a trending-only source with
    no keyword search). Both are decisions, not gaps, and neither is a failure.
    `ok`, `unavailable` and `searched-no-results` are not accepted here: those
    describe what a source did, and only the capture scripts can report that.
    """
    if status not in DECISION_STATUSES:
        return {"ok": False, "error": f"status must be one of {DECISION_STATUSES}"}
    if source not in EVIDENCE_SOURCES:
        return {"ok": False, "error": f"{source!r} is not a recognised source; "
                                      f"valid sources are: {sorted(EVIDENCE_SOURCES)}"}
    entry = {
        "source": source, "status": status, "fallback": None,
        "detail": f"cell {cell_id}: {reason}",
    }
    append_health(slug, [entry])
    return {"ok": True, "recorded": entry}
