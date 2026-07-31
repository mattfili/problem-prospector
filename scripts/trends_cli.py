#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["trend-pulse>=2.0.0,<3.0.0"]
# ///
"""Script fallback for the `trend-pulse` stdio MCP server.

WHY THIS EXISTS
---------------
In Cowork (and any host that refuses to spawn stdio MCP servers) the
`trend-pulse` entry in `.mcp.json` never loads. The plugin spec makes a hard
guarantee: every MCP-provided capability has a script fallback so the same
`/prospect` run completes either way. This script is one half of that
guarantee; `reality_cli.py` (idea-reality) is the other.

HOW IT FITS THE PIPELINE
------------------------
It is a *scout*: it captures and never interprets (§3.1). It calls trend-pulse's
key-free built-in sources in-process and maps each returned item into a
CONTRACTS §2 evidence record, so its output is line-for-line interchangeable
with `hn_search.py` / `reddit_search.py` captures and is consumed by
`cluster.py` with no special-casing.

    /prospect --> trends_cli.py --> runs/<slug>/evidence/<source>.jsonl --> cluster.py

WHAT TREND-PULSE ACTUALLY IS (read before trusting this output)
--------------------------------------------------------------
Verified against trend-pulse 2.0.0 installed from PyPI. Three deltas from what
the plugin spec assumed, all of which change how a caller should use this:

1. It is a *trending-keyword* aggregator, not a discussion archive. A
   `TrendItem` carries a headline (`keyword`), a normalized 0-100 popularity
   score, a URL, and a free-text `traffic` string. Most sources return no body
   text at all, so evidence `text` is `null` for them and `title` is the only
   verbatim prose. Clustering must therefore key on `title` for these records.
   Sources that do supply a body: arxiv (abstract), github/dockerhub/mastodon
   (description), producthunt (tagline).

2. Only 8 of the 20 built-in sources implement `search()`: hackernews, arxiv,
   lemmy, reddit, stackoverflow, bluesky, devto, producthunt. The other 12 are
   trending-only. `--source google-trends --query ...` cannot work; there is no
   Google Trends keyword search in this package. With `--query`, trending-only
   sources are skipped and recorded as `degraded` rather than silently
   returning trending items tagged with a query they never matched.

3. `TrendItem.score` is a per-source *normalized* 0-100 value (HN divides
   points by 5, Reddit divides upvotes by 500, and Stack Overflow can even go
   negative). It is not an engagement count and is never emitted as one.
   Real engagement is recovered only from the verbatim `traffic` string and
   from `metadata`; anything else is `null`. See `_engagement()`.

OBSERVED LIVE (2026-07-31, every built-in source exercised)
-----------------------------------------------------------
Working: hackernews, arxiv, lemmy, stackoverflow, wikipedia, github, pypi, npm,
google-trends, google-news, lobsters, mastodon, coingecko, dockerhub, ptt.
Blocked at the origin, and this script will not fight it: reddit (403 Blocked on
`reddit.com/search.json` -- use `reddit_search.py` against Arctic Shift instead,
which is why that scout exists), producthunt (403), dcard (403), devto (404,
because its `search()` misuses the `tag=` parameter and any multi-word query
404s upstream). All four are reported per-source; none is ever reported as
"no discussion found".

DISCIPLINE
----------
- Zero credentials. Only the 20 built-in `requires_auth = False` sources are
  ever constructed, and plugin auto-loading is disabled, which structurally
  excludes trend-pulse's `x_trending` plugin (it reads `X_BEARER_TOKEN`).
  trend-pulse's one *built-in* credential read -- github's Cloudflare Browser
  Rendering fallback, which reads `CF_API_TOKEN` and sends it as a bearer token
  -- is removed before any fetch runs. See `_disable_ai_fallbacks()`.
- Nothing here may emit a value a source did not return. That same GitHub
  fallback asks an LLM to extract repos and turns the reply into evidence with a
  constructed `github.com/<name>` URL and an invented star count; it is disabled
  for that reason as much as for the credential read.
- A failed fetch is a failure, never "no results found". Every source lands in
  `source_health` as ok | degraded | unavailable with the real reason. Three
  sources (pypi, bluesky, mastodon) swallow non-200 responses upstream and
  return `[]`, so response statuses are recorded independently and a zero-item
  fetch that was actually refused is reported `unavailable`, not empty. See
  `_install_response_recorder()`.
- On 403/429 the host is circuit-broken: recorded once, never retried, never
  probed with rotated headers.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import json
import re
import sys
import time
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

TOOL_UA = "problem-prospector/0.1 (+https://github.com/mattfili/problem-prospector)"


def log(msg: str) -> None:
    """Diagnostics go to stderr so stdout stays parseable JSON."""
    print(msg, file=sys.stderr, flush=True)


def emit_fatal(detail: str) -> int:
    """Print a contract-shaped failure payload and return exit code 1.

    Even a total failure must leave stdout parseable and carry `source_health`,
    because the caller is an agent reading stdout directly with no wrapper.
    """
    print(
        json.dumps(
            {
                "run": {"tool": "trends_cli.py", "fallback_for": "trend-pulse MCP server"},
                "summary": {"evidence_count": 0},
                "source_health": [
                    {"source": "trend-pulse", "status": "unavailable", "detail": detail}
                ],
                "evidence": [],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 1


# --------------------------------------------------------------------------
# User-Agent shim
# --------------------------------------------------------------------------
# trend-pulse builds its own httpx.AsyncClient per source, and what it sends for
# User-Agent is wrong for this plugin in three different ways:
#   * Most sources send no UA at all. Wikimedia hard-403s UA-less clients.
#   * wikipedia.py sends a per-request "trend-mcp/0.1", which has no contact
#     URL and which Wikimedia's UA policy also rejects with 403. Verified live:
#     that same pageviews URL returns 403 with upstream's UA and 200 with ours,
#     so this shim is the difference between wikipedia being unavailable and ok.
#   * producthunt.py sends a spoofed Chrome UA. This plugin does not
#     impersonate browsers, so that string must not leave this process.
# We are the operator making these calls, so every outbound request is forced to
# identify problem-prospector with a contact URL -- unconditionally, overriding
# upstream's per-request headers rather than merely filling in a default.
#
# `build_request` is the single choke point: AsyncClient.get/post/request/stream
# all construct their Request through it, so per-request `headers=` kwargs
# (which otherwise take precedence over client defaults) are covered too.


def _install_user_agent_shim() -> None:
    import httpx

    original_build_request = httpx.AsyncClient.build_request

    def patched_build_request(self: Any, *args: Any, **kwargs: Any) -> Any:
        request = original_build_request(self, *args, **kwargs)
        request.headers["user-agent"] = TOOL_UA
        return request

    httpx.AsyncClient.build_request = patched_build_request  # type: ignore[method-assign]


# --------------------------------------------------------------------------
# Response-status recorder
# --------------------------------------------------------------------------
# Most trend-pulse sources call `raise_for_status()`, so a 403/429 arrives here
# as an exception and is reported `unavailable`. But three of them --
# pypi (a documented CONTRACTS §2 source), bluesky, and mastodon -- test
# `resp.status_code != 200` and simply `continue`/`return []`. Verified by
# injecting a synthetic 429 at the transport: those sources returned an empty
# list with NO exception, which this wrapper then reported as `degraded` with the
# detail "HTTP fetch succeeded and matched nothing". That detail was false, and
# the throttling signal was lost entirely -- exactly the failure this tool must
# never make. A rate-limited fetch is a failure, not an empty result.
#
# So every response status is recorded per-source and consulted whenever a source
# yields zero items. `build_request` cannot do this (it sees only requests);
# `AsyncClient.send` is the choke point every response passes through.
_STATUS_LOG: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar(
    "trends_cli_status_log", default=None
)

# Statuses that mean "the origin refused us", so zero items is not an answer.
BLOCKING_STATUSES = frozenset({401, 402, 403, 407, 408, 409, 429, 451})
CIRCUIT_BREAK_STATUSES = frozenset({403, 429})


def _install_response_recorder() -> None:
    import httpx

    original_send = httpx.AsyncClient.send

    async def patched_send(self: Any, *args: Any, **kwargs: Any) -> Any:
        response = await original_send(self, *args, **kwargs)
        seen = _STATUS_LOG.get()
        if seen is not None:
            seen.append(response.status_code)
        return response

    httpx.AsyncClient.send = patched_send  # type: ignore[method-assign]


def refused_statuses(statuses: Iterable[int]) -> list[int]:
    """Statuses proving the origin refused or failed the request, deduped."""
    bad = {s for s in statuses if s in BLOCKING_STATUSES or s >= 500}
    return sorted(bad)


# --------------------------------------------------------------------------
# AI / browser-rendering fallback removal
# --------------------------------------------------------------------------
# trend-pulse's github source has a fallback nobody should reach through this
# plugin. When its HTML parse yields 0 items it calls
# `browser_renderer.extract_json()`, which is Cloudflare Browser Rendering's
# *AI extraction* endpoint, and turns the model's reply into TrendItems --
# `url=f"https://github.com/{name}"` from an LLM-supplied name, plus LLM-supplied
# `stars_today` which lands in `traffic` and which `_vote_count()` then parses
# into `engagement.score`. Verified end to end with a synthetic 200-that-parses-
# to-nothing: the run emitted, and reported `ok`,
#   url  https://github.com/zorblax/quibnitz-permits   (repo never existed)
#   engagement {"score": 8421}                         (count never existed)
# That is a constructed URL and an invented count, which CONTRACTS §2 and
# cross-cutting rule 1 both forbid outright.
#
# The same call also reads CF_ACCOUNT_ID / CF_API_TOKEN and sends the token as
# `Authorization: Bearer ...`. Verified with a canary token: it left the process.
# That breaks cross-cutting rule 4, "no script reads an API key".
#
# `_fallback_browser` is the only path from any source to `browser_renderer`, and
# the import of that module (where the credential read lives at module scope) is
# lazy and happens inside it. Replacing the method therefore removes the
# fabrication and the credential read together -- the module is never imported.
# With the fallback gone, a 0-item GitHub fetch surfaces as `degraded`, which is
# the truthful verdict.
AI_FALLBACK_ATTRS = ("_fallback_browser",)


def _disable_ai_fallbacks(classes: Sequence[Any]) -> list[str]:
    """Strip any LLM/browser-rendering fallback off the source classes."""
    disabled: list[str] = []

    async def refuse(self: Any, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    for cls in classes:
        for attr in AI_FALLBACK_ATTRS:
            if getattr(cls, attr, None) is not None:
                setattr(cls, attr, refuse)
                disabled.append(f"{cls.name}.{attr}")
    return disabled


# --------------------------------------------------------------------------
# Source naming
# --------------------------------------------------------------------------
# trend-pulse uses snake_case internally; CONTRACTS §2 documents a kebab-case
# enum. Sources present in that enum get its exact spelling; sources trend-pulse
# offers that the enum does not document get kebab-case of their trend-pulse
# name and are flagged in source_health so a reader knows the value is an
# extension rather than a contract value.
CONTRACT_ENUM_SOURCES = {
    "reddit": "reddit",
    "hackernews": "hackernews",
    "stackoverflow": "stackoverflow",
    "producthunt": "producthunt",
    "github": "github",
    "pypi": "pypi",
    "npm": "npm",
    "wikipedia": "wikipedia",
    "google_trends": "google-trends",
}

# Caller-friendly spellings for the same underlying source.
SOURCE_ALIASES = {
    "google_trends": "google_trends",
    "googletrends": "google_trends",
    "trends": "google_trends",
    "google_news": "google_news",
    "googlenews": "google_news",
    "news": "google_news",
    "github_trending": "github",
    "githubtrending": "github",
    "hn": "hackernews",
    "hacker_news": "hackernews",
    "so": "stackoverflow",
    "stack_overflow": "stackoverflow",
    "ph": "producthunt",
    "product_hunt": "producthunt",
    "dev_to": "devto",
    "stackexchange": "stackoverflow",
}

ALL_FREE = "all-free"


def contract_source(tp_name: str) -> str:
    """Map a trend-pulse source name to its CONTRACTS §2 `source` value."""
    return CONTRACT_ENUM_SOURCES.get(tp_name, tp_name.replace("_", "-"))


def normalize_requested(name: str) -> str:
    """Accept kebab-case, snake_case, and common short names."""
    key = name.strip().lower().replace("-", "_")
    return SOURCE_ALIASES.get(key, key)


# --------------------------------------------------------------------------
# TrendItem -> evidence field extraction
# --------------------------------------------------------------------------
# `traffic` is the only place a real, un-normalized engagement count survives.
# Observed live formats, one per source:
#   "194 points"  "521 upvotes"  "50 points"  "100 pushes"
#   "612 stars today (0 total)"          -> vote-like, first int is the count
#   "46 views"  "256 uses, 233 accounts"  "44K pulls"  "Rank #77"
#   "16,366,271/day (97,663,750/week)"   "26,772,295 downloads/day"
# Only vote-like units become `engagement.score`. Views, uses, pulls, and
# downloads are a different quantity by two or three orders of magnitude;
# summing them into `clusters.json:engagement_sum` alongside upvote counts would
# let one npm package outweigh every human complaint in the run. Those sources
# therefore report `score: null` -- "the source did not report a score" -- which
# is exactly what CONTRACTS §2 says null means.
VOTE_UNITS = frozenset(
    {"point", "points", "upvote", "upvotes", "vote", "votes", "star", "stars", "push", "pushes"}
)
_TRAFFIC_RE = re.compile(r"^\s*([0-9][0-9,]*)\s*([A-Za-z]+)")

# "44K pulls" must not be read as 44. Any abbreviated magnitude is unusable.
_MAGNITUDE_UNITS = frozenset({"k", "m", "b"})

COMMENT_KEYS = ("comments", "num_comments", "answer_count", "descendants", "comment_count", "replies")
AUTHOR_KEYS = ("by", "author", "owner", "submitter", "creator", "username", "handle", "authors")
COMMUNITY_KEYS = ("subreddit", "community", "board", "instance", "forum")
# Body-ish fields, most specific first. `keyword` is deliberately NOT a
# fallback: a headline is a title, and copying it into `text` would double its
# weight in clustering and misrepresent a headline as a body.
TEXT_KEYS = ("summary", "abstract", "selftext", "body", "text", "content", "description", "tagline", "excerpt")

TEXT_MAX_CHARS = 4000


def _vote_count(traffic: str) -> int | None:
    """Recover a real vote-like engagement count from the `traffic` string."""
    if not traffic:
        return None
    match = _TRAFFIC_RE.match(traffic)
    if not match:
        return None
    unit = match.group(2).lower()
    if unit in _MAGNITUDE_UNITS or unit not in VOTE_UNITS:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _first_str(meta: dict[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (list, tuple)) and value:
            parts = [str(v).strip() for v in value[:5] if str(v).strip()]
            if parts:
                return ", ".join(parts)
    return None


def _first_int(meta: dict[str, Any], keys: Iterable[str]) -> int | None:
    for key in keys:
        value = meta.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _engagement(traffic: str, meta: dict[str, Any]) -> dict[str, int | None] | None:
    """Recover real engagement, or null. Caveat the caller must know:

    several trend-pulse sources build metadata with `.get("comments", 0)`, so a
    `comments` of 0 can be upstream's default rather than a source-reported zero
    -- the exact conflation CONTRACTS §2 warns about. This wrapper cannot tell
    the two apart from the outside, and it keeps the 0 rather than nulling it,
    because nulling would also destroy the genuine zeros (a real 0-comment post
    is common and meaningful). Treat `comments: 0` as "0 or unreported".
    """
    score = _vote_count(traffic)
    comments = _first_int(meta, COMMENT_KEYS)
    if score is None and comments is None:
        # CONTRACTS §2: null engagement means "the source did not report it".
        return None
    return {"score": score, "comments": comments}


def _to_epoch(published: str) -> int | None:
    """Parse trend-pulse's `published` string. Returns None rather than guessing.

    Live formats seen across sources: "2020-09-27T10:40:33Z",
    "2026-07-31T06:15:54.655-05:00", "2026-07-31T13:41:30.557537Z",
    "Fri, 31 Jul 2026 13:30:00 -0700", "Fri, 31 Jul 2026 16:31:09 GMT", "".
    """
    if not published or not published.strip():
        return None
    raw = published.strip()
    for parse in (
        lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
        parsedate_to_datetime,
    ):
        try:
            parsed = parse(raw)
        except (ValueError, TypeError):
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    return None


def _community(tp_name: str, meta: dict[str, Any]) -> str | None:
    value = _first_str(meta, COMMUNITY_KEYS)
    if value is None:
        return None
    if tp_name == "reddit" and not value.startswith("r/"):
        return f"r/{value}"
    return value


def _author(tp_name: str, meta: dict[str, Any]) -> str | None:
    value = _first_str(meta, AUTHOR_KEYS)
    if value is None:
        return None
    if tp_name == "reddit" and not value.startswith("u/"):
        return f"u/{value}"
    return value


def _url(tp_name: str, url: str, meta: dict[str, Any]) -> str | None:
    """Return a resolvable permalink, or None. Never invents one.

    One deliberate derivation: HN's Algolia search returns `url: ""` for
    Ask HN / Show HN posts that have no outbound link, but every hit carries the
    story's real `objectID`. `news.ycombinator.com/item?id=<objectID>` is that
    story's canonical permalink -- it is the identical template trend-pulse
    itself uses in `hackernews.fetch_trending()`, applied to an id the source
    returned, and it was verified to resolve 200 during development. Without it
    every Ask HN item -- the highest-signal pain text HN has -- would arrive
    uncitable. No other source gets a derived URL.
    """
    if url and url.strip():
        return url.strip()
    if tp_name == "hackernews":
        story_id = str(meta.get("id", "")).strip()
        if story_id.isdigit():
            return f"https://news.ycombinator.com/item?id={story_id}"
    return None


def to_evidence(
    item: dict[str, Any],
    tp_name: str,
    cell_id: str | None,
    query: str | None,
    captured_utc: int,
) -> dict[str, Any]:
    """Map one `TrendItem.to_dict()` onto a CONTRACTS §2 evidence record."""
    meta = item.get("metadata") or {}
    source = contract_source(tp_name)
    title = (item.get("keyword") or "").strip() or None
    url = _url(tp_name, item.get("url") or "", meta)

    text = _first_str(meta, TEXT_KEYS)
    if text is not None:
        # Truncation is contract-legal; rewording is not. Nothing is appended,
        # so what remains is still byte-for-byte what the source said.
        text = text[:TEXT_MAX_CHARS]

    # CONTRACTS §2 calls this "sha1-of-source-plus-url". The title is folded in
    # because several trend-pulse sources (google_trends, google_news) hand back
    # one shared landing URL for every item, which would otherwise collapse a
    # whole page of distinct trends onto a single id. Title is verbatim and
    # fixed, so the id stays stable across runs for /rescan to diff.
    ident = hashlib.sha1(f"{source}|{url or ''}|{title or ''}".encode()).hexdigest()

    return {
        "id": ident,
        "cell_id": cell_id,
        "source": source,
        "url": url,
        "title": title,
        "text": text,
        "author": _author(tp_name, meta),
        "community": _community(tp_name, meta),
        "engagement": _engagement(item.get("traffic") or "", meta),
        "created_utc": _to_epoch(item.get("published") or ""),
        "captured_utc": captured_utc,
        "query": query,
    }


# --------------------------------------------------------------------------
# Source loading
# --------------------------------------------------------------------------


class SourceImportError(RuntimeError):
    """trend-pulse itself could not be imported; nothing can be gathered."""


def load_sources() -> tuple[list[Any], Any]:
    """Import trend-pulse's built-in source classes.

    Binds to `trend_pulse.sources.ALL_SOURCES` (an exported name) rather than
    to `TrendAggregator`, for three reasons that matter to this wrapper:
      * The aggregator drops any source that returns zero items from BOTH its
        `sources_ok` and `sources_error` lists, so a silently-empty source is
        indistinguishable from one that was never asked. Per-source health is
        the entire point here, so that loss is unacceptable.
      * `TrendAggregator.search()` silently omits sources lacking `search()`
        instead of reporting them.
      * The aggregator touches `~/.trend-pulse/history.db` when it exists.
        A read-only scout should have no such side effect.
    Plugin sources are never loaded: `ALL_SOURCES` is the 20 built-ins only,
    which structurally excludes the `x_trending` plugin's `X_BEARER_TOKEN` read.
    """
    try:
        from trend_pulse.sources import ALL_SOURCES
        from trend_pulse.sources.base import TrendSource
    except ImportError as exc:  # pragma: no cover - environment problem
        log(f"FATAL: cannot import trend_pulse ({exc}).")
        log("Run this script via `uv run` so PEP 723 metadata installs trend-pulse>=2.0.0.")
        raise SourceImportError(
            f"cannot import trend_pulse ({exc}); run via `uv run` so PEP 723 metadata "
            "installs trend-pulse>=2.0.0"
        ) from exc
    return list(ALL_SOURCES), TrendSource


def supports_search(cls: Any, base: Any) -> bool:
    """True when the source overrides `search()` rather than inheriting the stub."""
    return cls.search is not base.search


def describe_sources(classes: Sequence[Any], base: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cls in classes:
        rows.append(
            {
                "source": cls.name,
                "contract_source": contract_source(cls.name),
                "in_contract_enum": cls.name in CONTRACT_ENUM_SOURCES,
                "requires_auth": bool(cls.requires_auth),
                "rate_limit": cls.rate_limit,
                "supports_query": supports_search(cls, base),
                "description": cls.description,
            }
        )
    return sorted(rows, key=lambda r: r["source"])


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------


def _classify_error(exc: BaseException) -> tuple[str, bool]:
    """Return (detail, circuit_broken). 403/429 are terminal for that host."""
    name = type(exc).__name__
    text = " ".join(str(exc).split())
    status = None
    match = re.search(r"\b(4\d\d|5\d\d)\b", text)
    if match:
        status = match.group(1)
    detail = f"{name}: {text[:300]}"
    if status in {"403", "429"}:
        return (
            f"{detail} | host circuit-broken after {status}; not retried, no header rotation",
            True,
        )
    return detail, False


async def _fetch_one(
    cls: Any,
    base: Any,
    query: str | None,
    limit: int,
    geo: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """Run one source and return a health+items record. Never raises."""
    name = cls.name
    result: dict[str, Any] = {
        "source": name,
        "items": [],
        "error": None,
        "mode": None,
        "statuses": [],
    }

    if query is not None and not supports_search(cls, base):
        result["mode"] = "unsupported"
        return result

    # Set inside the coroutine, so each gathered task gets its own list in its
    # own copied context and no source can see another's statuses.
    statuses: list[int] = []
    _STATUS_LOG.set(statuses)
    result["statuses"] = statuses

    async with semaphore:
        started = time.monotonic()
        try:
            instance = cls()
            if query is not None:
                result["mode"] = "search"
                items = await instance.search(query=query, geo=geo)
            else:
                result["mode"] = "trending"
                items = await instance.fetch_trending(geo=geo, count=limit)
        except Exception as exc:  # noqa: BLE001 - one bad source must not sink the run
            result["error"] = exc
            log(f"  {name}: FAILED after {time.monotonic() - started:.1f}s -- {type(exc).__name__}")
            return result

    # `search()` takes no count argument upstream (it fetches a fixed page,
    # typically 20), so --limit can only truncate search results client-side.
    # For trending, `count` was passed through to the source.
    result["items"] = [it.to_dict() for it in items][:limit]
    refused = refused_statuses(statuses)
    suffix = f" (refused statuses seen: {refused})" if refused else ""
    log(f"  {name}: {len(result['items'])} items in {time.monotonic() - started:.1f}s{suffix}")
    return result


async def gather_sources(
    classes: Sequence[Any],
    base: Any,
    query: str | None,
    limit: int,
    geo: str,
    concurrency: int,
) -> list[dict[str, Any]]:
    # Each source targets a distinct host, so bounded concurrency across sources
    # does not stack load on any single host. The bound exists to keep the
    # aggregate burst modest -- notably HN, whose fetch_trending fans out one
    # Firebase request per story.
    semaphore = asyncio.Semaphore(max(1, concurrency))
    return list(
        await asyncio.gather(
            *(_fetch_one(cls, base, query, limit, geo, semaphore) for cls in classes)
        )
    )


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def silently_refused(res: dict[str, Any]) -> list[int]:
    """Refused statuses behind a zero-item fetch that raised no exception.

    Non-empty means the source did not fail loudly but was in fact turned away
    by the origin, so its emptiness must be reported as `unavailable` and never
    as an empty-but-successful fetch.
    """
    if res["mode"] in (None, "unsupported") or res["error"] is not None or res["items"]:
        return []
    return refused_statuses(res.get("statuses") or [])


def build_health(
    results: Sequence[dict[str, Any]],
    query: str | None,
) -> list[dict[str, str]]:
    health: list[dict[str, str]] = []
    for res in sorted(results, key=lambda r: r["source"]):
        name = res["source"]
        label = contract_source(name)
        enum_note = "" if name in CONTRACT_ENUM_SOURCES else " | outside documented CONTRACTS §2 source enum"

        if res["mode"] == "unsupported":
            health.append(
                {
                    "source": label,
                    "status": "degraded",
                    "detail": (
                        "trend-pulse exposes no search() for this source; skipped because "
                        "--query was given. Omit --query to capture its trending feed."
                        + enum_note
                    ),
                }
            )
            continue

        if res["error"] is not None:
            detail, _ = _classify_error(res["error"])
            health.append(
                {"source": label, "status": "unavailable", "detail": detail + enum_note}
            )
            continue

        count = len(res["items"])
        if count == 0:
            refused = silently_refused(res)
            if refused:
                # The source swallowed a refusal and returned []. Report the
                # refusal, not the emptiness: a throttled or blocked fetch is a
                # failure, and calling it an empty result would be a lie.
                note = (
                    " | host circuit-broken; not retried, no header rotation"
                    if any(s in CIRCUIT_BREAK_STATUSES for s in refused)
                    else ""
                )
                health.append(
                    {
                        "source": label,
                        "status": "unavailable",
                        "detail": (
                            f"{res['mode']} returned 0 items, but the origin refused the request: "
                            f"HTTP {', '.join(map(str, refused))}. This source swallows non-200 "
                            "responses upstream instead of raising, so the empty list is a refusal, "
                            "NOT an absence of results." + note + enum_note
                        ),
                    }
                )
                continue
            # Not "no results found". Every response this source received was a
            # 2xx/3xx, so the HTTP fetch itself did succeed -- but for the
            # HTML-scraping sources a broken selector also yields an empty list
            # off a 200, and this wrapper cannot tell the two apart. Reported as
            # degraded so the run records the ambiguity instead of asserting
            # absence of discussion.
            health.append(
                {
                    "source": label,
                    "status": "degraded",
                    "detail": (
                        f"{res['mode']} returned 0 items with no error and no refused status: "
                        "the HTTP fetch succeeded and matched nothing, or an upstream HTML parser "
                        "broke on a 200 response. Not evidence that nothing exists." + enum_note
                    ),
                }
            )
            continue

        # Items came back, but a partial refusal must still be visible: mastodon
        # and pypi fan out over several requests and keep whatever survived, so
        # `ok` alone would hide that the capture is short.
        partial = refused_statuses(res.get("statuses") or [])
        partial_note = (
            f" | partial: origin refused some requests (HTTP {', '.join(map(str, partial))}), "
            "so this capture is incomplete"
            if partial
            else ""
        )
        health.append(
            {
                "source": label,
                "status": "degraded" if partial else "ok",
                "detail": f"{count} items via {res['mode']}" + partial_note + enum_note,
            }
        )
    if query is not None:
        health.append(
            {
                "source": "trend-pulse",
                "status": "degraded",
                "detail": (
                    "--query mode: trend-pulse implements search() on only 8 of 20 built-in "
                    "sources, and its search() accepts no count, so --limit truncates "
                    "client-side rather than narrowing the upstream fetch."
                ),
            }
        )
    return health


def write_out(out: Path, evidence: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Persist evidence as CONTRACTS §2 JSONL. Append-only, per §2.

    A path ending in `.jsonl` gets every record in one file. Anything else is
    treated as a directory and gets one `<source>.jsonl` per source, which is
    the layout CONTRACTS §2 names (`evidence/<source>.jsonl`).
    """
    written: dict[str, int] = {}
    if out.suffix == ".jsonl":
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as handle:
            for record in evidence:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        written[str(out)] = len(evidence)
        return {"mode": "single-file", "files": written}

    out.mkdir(parents=True, exist_ok=True)
    by_source: dict[str, list[dict[str, Any]]] = {}
    for record in evidence:
        by_source.setdefault(record["source"], []).append(record)
    for source, records in sorted(by_source.items()):
        path = out / f"{source}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        written[str(path)] = len(records)
    return {"mode": "per-source", "files": written}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

EPILOG = """\
examples:
  # what can I actually ask for, and which sources accept a --query?
  uv run --quiet scripts/trends_cli.py --list-sources

  # keyword search across the search-capable sources
  uv run --quiet scripts/trends_cli.py --source hackernews --source lemmy \\
      --query "permit software" --limit 20 --cell-id m01

  # trending capture (no --query) from sources that have no search endpoint
  uv run --quiet scripts/trends_cli.py --source github --source pypi --source wikipedia \\
      --limit 15 --cell-id m01 --out runs/my-slug/evidence

  # everything key-free; with --query this resolves to the 8 searchable sources
  uv run --quiet scripts/trends_cli.py --source all-free --query "permit backlog"

notes:
  * stdout is always JSON: {run, summary, source_health, evidence}.
  * exit 0 whenever at least one source completed a fetch, even with 0 items.
    exit 1 only when every requested source failed.
  * --out DIR writes CONTRACTS §2 <source>.jsonl files; --out FILE.jsonl writes
    one combined file. Both append, per the append-only evidence contract.
"""


def build_parser(source_names: Sequence[str]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trends_cli.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Script fallback for the trend-pulse stdio MCP server. Captures trend signal "
            "from trend-pulse's 20 key-free built-in sources and emits CONTRACTS §2 "
            "evidence JSONL, so a /prospect run completes identically whether or not the "
            "MCP server loaded. Reads no API key, ever."
        ),
        epilog=EPILOG,
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "Source to query; repeatable. Use 'all-free' for every key-free built-in. "
            "Accepts kebab or snake case. Known: " + ", ".join(sorted(source_names))
        ),
    )
    parser.add_argument(
        "--query",
        default=None,
        help=(
            "Keyword search. Only the 8 sources with supports_query=true honour this; "
            "the rest are skipped and recorded as degraded. Omit for trending capture."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max items per source (default: 20). Passed through for trending; truncates for search.",
    )
    parser.add_argument(
        "--cell-id",
        default=None,
        help="Matrix cell id from inputs.json (e.g. m01), stamped onto every evidence record.",
    )
    parser.add_argument(
        "--geo",
        default="",
        help="Country code for the geo-aware sources (google-trends, google-news, wikipedia), e.g. US.",
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Persist evidence. A directory gets <source>.jsonl files; a *.jsonl path gets one file.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Max sources fetched at once (default: 4). Each source is a distinct host.",
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="Print the key-free sources with their rate limits and query support, then exit.",
    )
    return parser


def resolve_selection(
    requested: Sequence[str],
    classes: Sequence[Any],
) -> tuple[list[Any], list[str]]:
    by_name = {cls.name: cls for cls in classes}
    if not requested or any(r.strip().lower() == ALL_FREE for r in requested):
        return list(classes), []

    selected: list[Any] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for raw in requested:
        name = normalize_requested(raw)
        cls = by_name.get(name)
        if cls is None:
            unknown.append(raw)
            continue
        if name not in seen:
            seen.add(name)
            selected.append(cls)
    return selected, unknown


def main(argv: Sequence[str] | None = None) -> int:
    try:
        classes, base = load_sources()
    except SourceImportError as exc:
        return emit_fatal(str(exc))
    parser = build_parser([cls.name for cls in classes])
    args = parser.parse_args(argv)

    if args.list_sources:
        rows = describe_sources(classes, base)
        payload = {
            "tool": "trends_cli.py",
            "trend_pulse_sources": len(rows),
            "query_capable": sorted(r["source"] for r in rows if r["supports_query"]),
            "trending_only": sorted(r["source"] for r in rows if not r["supports_query"]),
            "excluded_plugin_sources": {
                "reason": (
                    "trend-pulse plugin auto-loading is disabled; only the built-in "
                    "requires_auth=false sources are exposed."
                ),
                "notable": [
                    {
                        "source": "x_trending",
                        "why": "reads the X_BEARER_TOKEN environment variable (credential read)",
                    }
                ],
            },
            "sources": rows,
            "source_health": [
                {
                    "source": "trend-pulse",
                    "status": "ok",
                    "detail": f"enumerated {len(rows)} key-free built-in sources locally, no network calls",
                }
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if args.limit < 1:
        parser.error("--limit must be >= 1")

    selected, unknown = resolve_selection(args.source, classes)
    for name in unknown:
        log(f"WARNING: unknown source {name!r} -- ignored. Try --list-sources.")
    if not selected:
        log("FATAL: no known sources selected. Run --list-sources to see valid names.")
        return emit_fatal(
            f"no known sources selected (unrecognised: {', '.join(map(repr, unknown)) or 'none given'}); "
            "run --list-sources for valid names"
        )

    _install_user_agent_shim()
    _install_response_recorder()
    disabled = _disable_ai_fallbacks(selected)
    if disabled:
        log(f"disabled LLM/browser-rendering fallback(s): {', '.join(disabled)}")

    query = args.query
    mode = "search" if query is not None else "trending"
    log(f"trends_cli: {mode} across {len(selected)} source(s), limit={args.limit}, geo={args.geo or '-'}")

    results = asyncio.run(
        gather_sources(selected, base, query, args.limit, args.geo, args.concurrency)
    )

    captured_utc = int(time.time())
    evidence: list[dict[str, Any]] = []
    for res in results:
        for item in res["items"]:
            evidence.append(to_evidence(item, res["source"], args.cell_id, query, captured_utc))

    health = build_health(results, query)
    attempted = [r for r in results if r["mode"] != "unsupported"]
    # A source that returned [] because the origin refused it did not complete a
    # fetch, however quietly it failed. It is counted with the failures so the
    # summary, source_health, and the exit code all tell the same story.
    failed = [r for r in attempted if r["error"] is not None or silently_refused(r)]
    completed = [r for r in attempted if r not in failed]

    counts_by_source: dict[str, int] = {}
    for record in evidence:
        counts_by_source[record["source"]] = counts_by_source.get(record["source"], 0) + 1

    persisted: dict[str, Any] | None = None
    if args.out and evidence:
        persisted = write_out(Path(args.out).expanduser(), evidence)
        log(f"wrote {len(evidence)} evidence records to {args.out}")
    elif args.out:
        log(f"nothing to write to {args.out}: no source returned an item")

    payload = {
        "run": {
            "tool": "trends_cli.py",
            "fallback_for": "trend-pulse MCP server",
            "mode": mode,
            "query": query,
            "cell_id": args.cell_id,
            "geo": args.geo or None,
            "limit": args.limit,
            "ai_fallbacks_disabled": disabled,
            "captured_utc": captured_utc,
            "captured_iso": datetime.fromtimestamp(captured_utc, tz=timezone.utc).isoformat(),
        },
        "summary": {
            "sources_requested": len(selected),
            "sources_attempted": len(attempted),
            # "ok" means items AND a clean run. A source whose origin refused
            # part of the fan-out is listed as partial, matching its `degraded`
            # verdict in source_health, so the two can never disagree.
            "sources_ok": sorted(
                contract_source(r["source"])
                for r in completed
                if r["items"] and not refused_statuses(r.get("statuses") or [])
            ),
            "sources_partial": sorted(
                contract_source(r["source"])
                for r in completed
                if r["items"] and refused_statuses(r.get("statuses") or [])
            ),
            "sources_empty": sorted(contract_source(r["source"]) for r in completed if not r["items"]),
            "sources_unavailable": sorted(contract_source(r["source"]) for r in failed),
            "sources_no_search": sorted(
                contract_source(r["source"]) for r in results if r["mode"] == "unsupported"
            ),
            "unknown_source_args": unknown,
            "evidence_count": len(evidence),
            "evidence_by_source": dict(sorted(counts_by_source.items())),
            "with_url": sum(1 for e in evidence if e["url"]),
            "with_text": sum(1 for e in evidence if e["text"]),
            "with_engagement": sum(1 for e in evidence if e["engagement"]),
            "with_created_utc": sum(1 for e in evidence if e["created_utc"] is not None),
            "persisted": persisted,
        },
        "source_health": health,
        "evidence": evidence,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    # exit 0 = fetching worked, even with zero results (requirement 7).
    if not completed:
        log("FATAL: every requested source failed; gathered nothing usable.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
