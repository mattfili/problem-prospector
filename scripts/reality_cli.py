#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["idea-reality-mcp>=0.5.0,<0.6.0"]
# ///
"""Script fallback for the `idea-reality` stdio MCP server -- the saturation read.

WHY THIS EXISTS
---------------
In Cowork (and any host that refuses to spawn stdio MCP servers) the
`idea-reality` entry in `.mcp.json` never loads. The plugin makes a hard
guarantee: every MCP-provided capability degrades to a script so the same
`/prospect` run completes either way. This script is one half of that
guarantee; `trends_cli.py` (trend-pulse) is the other.

HOW IT FITS THE PIPELINE
------------------------
Two consumers, one shape:
  * `/prospect` -- the saturation read. `saturation` in this output is
    drop-in for `cards/<cluster_id>.json` -> `saturation` (CONTRACTS §4):
    `{source, competitor_count, trend_direction, read}`.
  * `/diligence` -- the novelty check. `competitors` and `raw` carry the named
    repos/packages with resolvable URLs that the Competition and Novelty
    sections cite.

    /prospect --> reality_cli.py --> cards/<id>.json:saturation
    /diligence -> reality_cli.py --> diligence.md: Competition, Novelty

`--evidence-out` additionally writes CONTRACTS §2 evidence records for each
named competitor, which is why §2 lists this script as a producer.

HOW IT CALLS IDEA-REALITY (and why not through MCP)
--------------------------------------------------
Verified against idea-reality-mcp 0.5.0 installed from PyPI. In fastmcp 3.4.5
`@mcp.tool()` registers the function and returns it *unchanged*, so
`idea_reality_mcp.tools.idea_check` is a plain awaitable and no stdio JSON-RPC
loop is needed. We nonetheless call one level below it -- the four key-free
source functions plus `scoring.engine.compute_signal` -- for two reasons:

  1. `idea_check(depth="deep")` calls `search_producthunt()`, which reads the
     `PRODUCTHUNT_TOKEN` environment variable. Requirement 1 is zero
     credentials, so that path must be structurally unreachable, not merely
     unused. Calling the sources ourselves excludes it.
  2. `idea_check` swallows every per-request failure inside the source
     functions and reports the result as a count of zero. This wrapper needs
     to know the difference between "nobody built it" and "we could not look",
     so it observes the HTTP layer directly (see `_HostGovernor`).

Importing `sources.*` / `scoring.engine` directly also skips the fastmcp server
import entirely, which is why startup is fast.

WHAT IDEA-REALITY ACTUALLY IS (read before trusting this output)
---------------------------------------------------------------
Five deltas from what the plugin spec assumed, all of which change how a caller
should read the numbers:

1. **There is no StackOverflow source.** 0.5.0 ships github, hn, npm, pypi and
   producthunt. The spec's "GitHub/HN/npm/PyPI/StackOverflow" is wrong on the
   last one, and Product Hunt -- the source that actually exists in its place
   -- requires `PRODUCTHUNT_TOKEN` and is therefore excluded here. Four sources
   are reachable key-free.

2. **PyPI is dead upstream.** `sources/pypi.py` regex-scrapes
   `https://pypi.org/search/`, and pypi.org now answers non-browser clients
   with an HTTP **200** anti-bot "Client Challenge" page. The scraper parses
   zero projects from it and reports `total_count = 0` with no error. Verified
   live. A silent zero is exactly the failure mode this plugin forbids, so this
   wrapper inspects the response body itself and reports pypi `unavailable`
   with `pypi_packages: null` -- never 0.

3. **The composite is upstream-computed and it is opaque.** `reality_signal`
   is a weighted blend of log-curve scores plus a +/-10 "temporal boost".
   §3.8 forbids presenting a blended number as our analysis, so it lives under
   `upstream_composite` with its weights, its subscores, and the exact number
   of points it is deflated by when a source failed. Nothing in `saturation`
   is derived from it.

4. **Keyword extraction is dictionary-based and tuned for developer tools.**
   `extract_keywords()` picks an "intent anchor" from a fixed synonym table.
   Given a vertical/domain idea it degrades to generic tokens -- verified:
   "a CLI that tracks municipal permit status and notifies applicants" becomes
   `["cli tracks", ...]`, which matches ~19,800 GitHub repos and ~352,000 npm
   packages and returns `react-use` and an AI job-search tool as competitors.
   Worse, upstream reports the **max** count across variants, so the broadest
   variant sets the number. Every candidate is therefore relevance-checked here
   against the idea's own words (`competitors[].matched_terms`), and when none
   match, `saturation.read` says the extraction went off-domain instead of
   reporting a saturation level.

5. **Registry totals are keyword-breadth artifacts, not competitor counts.**
   npm answers ~90,000 for "monitoring self"; GitHub answers ~5,300. Those are
   breadth measurements. `competitor_count` is therefore the count of distinct
   *named*, on-domain candidates with resolvable URLs, not `total_repo_count`.
   The totals are still reported under `counts` so the read is re-derivable.

DISCIPLINE
----------
- Zero credentials. `sources.github._headers` (the only credential read on a
  path we use) is replaced before any call; `search_producthunt` and
  `scoring.llm` are never invoked. See `credential_paths_excluded` in the
  output for the audited list.
- A failed fetch is a failure, never "no results found". Every count whose
  source did not answer is `null`, and every source lands in `source_health`.
  When a competitor source answered nothing, or answered only some of its
  queries, `saturation.read` names it and labels the count a floor -- a card may
  copy only those four keys, so the caveat cannot live anywhere else.
- `saturation.source` is always `"reality_cli.py"`. §4's `source` is a
  provenance enum ("idea-reality" = the MCP answered, "reality_cli.py" = this
  script did), not a data-origin label; the upstream package is reported under
  `upstream`. Saying "idea-reality" here would file a degraded run as a healthy
  one.
- On 403/429 the host is circuit-broken: recorded once, then every further
  request to it fails locally without touching the network. No retry-probing,
  no header rotation.
- GitHub unauthenticated search allows 10 requests/minute and this pipeline
  spends 2 per keyword variant, so keywords are capped (default 3 -> 6
  requests) and spaced 6.5s apart.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

TOOL_UA = "problem-prospector/0.1 (+https://github.com/mattfili/problem-prospector)"

# The version this script's credential audit, composite reconstruction and
# PyPI-challenge finding were verified against. A different version still runs;
# it just gets a `source_health` warning that the audit may be stale.
AUDITED_VERSION = "0.5.0"

# Source name -> host. Names follow the CONTRACTS §2 source enum, which is why
# HN is "hackernews" here and not idea-reality's internal "hn" module name.
SOURCE_HOSTS: dict[str, str] = {
    "github": "api.github.com",
    "hackernews": "hn.algolia.com",
    "npm": "registry.npmjs.org",
    "pypi": "pypi.org",
}
DEFAULT_SOURCES = ("github", "hackernews", "npm", "pypi")
SOURCE_ALIASES = {"hn": "hackernews", "hackernews": "hackernews", "gh": "github"}

# Minimum seconds between requests to each host.
#   api.github.com   -- unauthenticated search is 10 req/min. 6.5s => <=9.2/min,
#                       leaving headroom for a second run started in the same
#                       minute before the window resets.
#   hn.algolia.com   -- no documented hard limit; 0.5s is the polite floor used
#                       across this plugin.
#   registry.npmjs.org / pypi.org -- no documented limit. pypi.org is a full
#                       HTML page render, so it gets the slower interval.
HOST_DELAYS: dict[str, float] = {
    "api.github.com": 6.5,
    "hn.algolia.com": 0.5,
    "registry.npmjs.org": 1.0,
    "pypi.org": 1.5,
}
DEFAULT_HOST_DELAY = 1.5

# Credential and third-party-egress paths in idea-reality-mcp 0.5.0 that this
# wrapper refuses to take. Audited by reading the installed source.
CREDENTIAL_PATHS_EXCLUDED = [
    {
        "path": "idea_reality_mcp.sources.producthunt.search_producthunt",
        "reads": "PRODUCTHUNT_TOKEN",
        "excluded_how": "never called; this wrapper orchestrates the four key-free sources itself "
        "instead of calling idea_check(depth='deep'), which would call it",
        "cost": "Product Hunt launch counts are unavailable; its 0.15 composite weight is "
        "redistributed by upstream across the remaining sources",
    },
    {
        "path": "idea_reality_mcp.sources.github._headers",
        "reads": "GITHUB_TOKEN",
        "excluded_how": "replaced at import time with a header dict containing only Accept and "
        "User-Agent, so the environment is never consulted",
        "cost": "GitHub search runs at the unauthenticated 10 req/min ceiling, which is why "
        "keywords are capped and calls are spaced 6.5s apart",
    },
    {
        "path": "idea_reality_mcp.scoring.llm.extract_keywords_llm",
        "reads": "IDEA_REALITY_API_URL (not a credential, but POSTs the idea text to a "
        "third-party Render deployment that calls an LLM)",
        "excluded_how": "never imported; keyword extraction uses the local dictionary pipeline, "
        "which is also what idea_check itself uses for stdio clients",
        "cost": "none for this use; the dictionary path is the upstream default",
    },
]

# `read` thresholds. The candidate list is hard-capped by upstream at 5 GitHub
# repos + 3 npm + 3 PyPI packages = 11 with Product Hunt excluded, so this is a
# 0-11 scale, not an open-ended count. 7+ means at least two ecosystems came
# back crowded; 1-2 means one ecosystem produced a couple of near-misses.
READ_SPARSE_MAX = 2
READ_MODERATE_MAX = 6

# CONTRACTS §4 `saturation.source` is a two-value provenance enum: "idea-reality"
# when the MCP server answered, "reality_cli.py" when this script did. It records
# which path ran, not which upstream package the data came from (that is
# `upstream.package`). This script is always the fallback path, so it must always
# say so -- claiming "idea-reality" would report a degraded run as a healthy one,
# which is the exact misattribution the enum exists to prevent
# (agents/scout.md "Source 3", commands/prospect.md "Stage 5").
SATURATION_SOURCE = "reality_cli.py"

# Relevance rule for deciding whether a surfaced candidate is on-domain, mirrored
# from upstream's own `_filter_relevant_similars`: two or more matched idea terms
# is a strong match, exactly one is a match only if that term is specific enough
# to carry it alone. Mirrored rather than reinvented so the count agrees with the
# order upstream already put the candidates in.
MATCH_MIN_TERM_LENGTH = 4
WEAK_MATCH_MIN_LENGTH = 5


def log(msg: str) -> None:
    """Diagnostics go to stderr so stdout stays parseable JSON."""
    print(msg, file=sys.stderr, flush=True)


class UpstreamImportError(RuntimeError):
    """idea-reality-mcp itself could not be imported; nothing can be gathered."""


# --------------------------------------------------------------------------
# HTTP governor
# --------------------------------------------------------------------------
# idea-reality's source functions each build their own httpx.AsyncClient, send
# no User-Agent, apply no rate limiting, and catch httpx.HTTPError per request
# with `continue` -- so a 403 becomes a count of zero indistinguishable from an
# empty market. Everything this wrapper knows about what actually happened on
# the wire is observed here.


class _HostGovernor:
    """Per-host rate limiting, circuit breaking and request observation."""

    def __init__(self, delays: Mapping[str, float]) -> None:
        self._delays = dict(delays)
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}
        self._tripped: dict[str, str] = {}
        self.observations: list[dict[str, Any]] = []
        # GitHub reports its remaining unauthenticated budget; carrying the
        # low-water mark into source_health makes a near-miss visible.
        self.rate_remaining: dict[str, int] = {}

    def tripped(self, host: str) -> str | None:
        return self._tripped.get(host)

    def trip(self, host: str, reason: str) -> None:
        if host not in self._tripped:
            self._tripped[host] = reason
            log(f"  circuit-break {host}: {reason} (no further requests to this host)")

    async def throttle(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        # The lock is held across the sleep on purpose: that is what serialises
        # concurrent coroutines sharing a host into a spaced queue.
        async with lock:
            delay = self._delays.get(host, DEFAULT_HOST_DELAY)
            previous = self._last.get(host)
            if previous is not None:
                wait = delay - (time.monotonic() - previous)
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last[host] = time.monotonic()

    def record(
        self,
        host: str,
        url: str,
        status: int | None,
        error: str | None = None,
        note: str | None = None,
    ) -> None:
        self.observations.append(
            {"host": host, "url": url, "status": status, "error": error, "note": note}
        )

    def for_host(self, host: str) -> list[dict[str, Any]]:
        return [o for o in self.observations if o["host"] == host]


def _install_governor(gov: _HostGovernor) -> None:
    """Wrap httpx.AsyncClient.get with the governor.

    Patching the client rather than each source keeps this working if upstream
    adds a source, and it is the only interception point that sees every
    request: the source functions do not expose their clients.
    """
    import httpx

    original_get = httpx.AsyncClient.get

    async def governed_get(self: Any, url: Any, **kwargs: Any) -> Any:
        host = urlsplit(str(url)).netloc

        reason = gov.tripped(host)
        if reason is not None:
            # Fail locally. httpx.HTTPError is what the source functions catch,
            # so upstream records its own per-query error and moves on.
            gov.record(host, str(url), None, error=f"circuit-broken: {reason}")
            raise httpx.HTTPError(f"circuit-broken: {reason}")

        headers = httpx.Headers(kwargs.get("headers") or {})
        if "user-agent" not in headers:
            headers["user-agent"] = TOOL_UA
        kwargs["headers"] = headers

        await gov.throttle(host)
        try:
            response = await original_get(self, url, **kwargs)
        except Exception as exc:  # transport errors, timeouts, DNS
            gov.record(host, str(url), None, error=f"{type(exc).__name__}: {exc}")
            raise

        remaining = response.headers.get("x-ratelimit-remaining")
        if remaining is not None:
            try:
                value = int(remaining)
            except ValueError:
                pass
            else:
                prior = gov.rate_remaining.get(host)
                gov.rate_remaining[host] = value if prior is None else min(prior, value)

        note = _inspect_body(host, response)
        gov.record(host, str(response.url), response.status_code, note=note)

        if response.status_code in (403, 429):
            retry_after = response.headers.get("retry-after")
            detail = f"HTTP {response.status_code}"
            if retry_after:
                detail += f", Retry-After: {retry_after}"
            gov.trip(host, detail)

        return response

    httpx.AsyncClient.get = governed_get  # type: ignore[method-assign]


def _inspect_body(host: str, response: Any) -> str | None:
    """Second-guess upstream's PyPI parser.

    pypi.org answers scripted clients with an HTTP 200 anti-bot challenge page.
    idea-reality's regex scraper finds no `package-snippet` markup in it and
    reports zero projects with no error, which would read as "no PyPI
    competitors". Only the response body distinguishes the two, and upstream
    throws it away, so it is checked here.
    """
    if host != "pypi.org" or response.status_code != 200:
        return None
    try:
        body = response.text
    except Exception:  # pragma: no cover - body already buffered in practice
        return None
    if "Client Challenge" in body or "_fs-ch-" in body:
        return "anti-bot-challenge"
    if "package-snippet" not in body:
        return "no-package-snippet-markup"
    return None


def _disable_credential_paths(github_module: Any) -> None:
    """Remove the only credential read on a path this wrapper uses.

    Upstream's `_headers()` adds `Authorization: Bearer $GITHUB_TOKEN` when the
    variable is set. Replacing it means the environment is never consulted, so
    the key-free guarantee holds identically on a maintainer's machine and on a
    brand-new one -- the same reason `gh_history.py` ignores GITHUB_TOKEN.
    """
    github_module._headers = lambda: {
        "Accept": "application/vnd.github+json",
        "User-Agent": TOOL_UA,
    }


# --------------------------------------------------------------------------
# Upstream binding
# --------------------------------------------------------------------------


def load_upstream() -> dict[str, Any]:
    """Import idea-reality's plain functions, with credential paths disabled.

    Binds to `sources.*` + `scoring.engine` rather than to `tools.idea_check`
    (see module docstring): `idea_check` is directly callable in fastmcp 3.4.5,
    but its deep path reads PRODUCTHUNT_TOKEN and it discards per-request
    failure detail.
    """
    try:
        import idea_reality_mcp
        import idea_reality_mcp.sources.github as github_module
        from idea_reality_mcp.scoring import engine
        from idea_reality_mcp.sources.github import search_github_repos
        from idea_reality_mcp.sources.hn import search_hn
        from idea_reality_mcp.sources.npm import search_npm
        from idea_reality_mcp.sources.pypi import search_pypi
    except ImportError as exc:  # pragma: no cover - environment problem
        log(f"FATAL: cannot import idea_reality_mcp ({exc}).")
        log("Run this script via `uv run` so PEP 723 metadata installs idea-reality-mcp>=0.5.0.")
        raise UpstreamImportError(
            f"cannot import idea_reality_mcp ({exc}); run via `uv run` so PEP 723 metadata "
            "installs idea-reality-mcp>=0.5.0,<0.6.0"
        ) from exc

    _disable_credential_paths(github_module)
    return {
        "version": getattr(idea_reality_mcp, "__version__", "unknown"),
        "engine": engine,
        "search": {
            "github": search_github_repos,
            "hackernews": search_hn,
            "npm": search_npm,
            "pypi": search_pypi,
        },
    }


def empty_results(engine: Any, source: str) -> Any:
    """A zero-valued result object for a source we did not query.

    `compute_signal` takes GitHub and HN positionally and cannot be told they
    are absent, so excluded sources are represented rather than faked away.
    Every count derived from one of these is forced to `null` downstream.
    """
    from idea_reality_mcp.sources.github import GitHubResults
    from idea_reality_mcp.sources.hn import HNResults
    from idea_reality_mcp.sources.npm import NpmResults
    from idea_reality_mcp.sources.pypi import PyPIResults

    if source == "github":
        return GitHubResults(total_repo_count=0, max_stars=0, top_repos=[])
    if source == "hackernews":
        return HNResults(total_mentions=0, evidence=[], recent_mention_ratio=None)
    if source == "npm":
        return NpmResults()
    return PyPIResults()


async def gather_sources(
    upstream: Mapping[str, Any],
    selected: Sequence[str],
    keywords: Sequence[str],
    gov: _HostGovernor,
) -> dict[str, Any]:
    """Query the selected sources concurrently. Hosts are distinct, so the
    per-host governor is what actually paces each one."""
    order = [s for s in DEFAULT_SOURCES if s in selected]

    async def one(source: str) -> Any:
        started = time.monotonic()
        result = await upstream["search"][source](list(keywords))
        log(f"  {source}: done in {time.monotonic() - started:.1f}s")
        return result

    log(f"reality_cli: querying {', '.join(order)} with {len(keywords)} keyword variant(s)")
    results = await asyncio.gather(*(one(s) for s in order), return_exceptions=True)

    out: dict[str, Any] = {}
    for source, result in zip(order, results):
        if isinstance(result, BaseException):
            # The source functions catch per-request errors themselves, so
            # reaching here means something structural broke.
            log(f"  {source}: FAILED ({type(result).__name__}: {result})")
            gov.record(SOURCE_HOSTS[source], f"({source} source function)", None, error=str(result))
            out[source] = empty_results(upstream["engine"], source)
        else:
            out[source] = result
    return out


# --------------------------------------------------------------------------
# Source health
# --------------------------------------------------------------------------


def source_status(gov: _HostGovernor, source: str, queried: bool) -> dict[str, str]:
    """Classify one source from what the governor actually observed."""
    if not queried:
        return {
            "source": source,
            "status": "unavailable",
            "detail": "excluded by --sources; not queried",
        }

    host = SOURCE_HOSTS[source]
    observations = gov.for_host(host)
    if not observations:
        return {
            "source": source,
            "status": "unavailable",
            "detail": f"no request to {host} was observed",
        }

    ok = [o for o in observations if o["status"] is not None and 200 <= o["status"] < 300]
    bad = [o for o in observations if o not in ok]
    challenged = [o for o in ok if o["note"] in ("anti-bot-challenge", "no-package-snippet-markup")]

    parts = [f"{len(ok)}/{len(observations)} requests ok"]
    remaining = gov.rate_remaining.get(host)
    if remaining is not None:
        parts.append(f"x-ratelimit-remaining low-water {remaining}")
    tripped = gov.tripped(host)
    if tripped:
        parts.append(f"circuit-broken ({tripped})")
    for observation in bad[:3]:
        parts.append(observation["error"] or f"HTTP {observation['status']}")

    # A PyPI 200 that carries the anti-bot challenge is a failed fetch wearing
    # a success code. Upstream reports it as `total_count = 0`; calling that
    # "ok" would publish a fabricated zero.
    if challenged and len(challenged) == len(ok):
        return {
            "source": source,
            "status": "unavailable",
            "detail": (
                f"{host} answered HTTP 200 with an anti-bot challenge page for every request; "
                "idea-reality 0.5.0 regex-scrapes this HTML and reports 0 projects with no "
                "error. Count suppressed to null -- absence of evidence, not evidence of absence"
            ),
        }
    if not ok:
        return {"source": source, "status": "unavailable", "detail": "; ".join(parts)}
    if bad or challenged:
        return {"source": source, "status": "degraded", "detail": "; ".join(parts)}
    return {"source": source, "status": "ok", "detail": "; ".join(parts)}


def status_of(health: Iterable[Mapping[str, str]], source: str) -> str | None:
    """The classified status recorded for `source`, or None if it has no entry."""
    for entry in health:
        if entry["source"] == source:
            return entry["status"]
    return None


def usable(health: Iterable[Mapping[str, str]], source: str) -> bool:
    """True when `source` returned at least one real response."""
    return status_of(health, source) in ("ok", "degraded")


# --------------------------------------------------------------------------
# Mapping to the CONTRACTS §4 saturation block
# --------------------------------------------------------------------------

# Upstream trend vocabulary -> the `trend_direction` vocabulary used by
# `retro_trend` elsewhere in this plugin ("flat" rather than "stable").
TREND_MAP = {"accelerating": "accelerating", "stable": "flat", "declining": "declining"}


def distinctive_terms(engine: Any, idea: str) -> list[str]:
    """Words from the idea that can anchor a relevance check.

    Built from the idea text only -- deliberately NOT from the derived keywords.
    The derived keywords are the thing that may have drifted off-domain (delta 4),
    so including them would launder the drift into the relevance test. The
    stoplists are upstream's own module constants, so this does not reinvent a
    second vocabulary.
    """
    stop = getattr(engine, "STOP_WORDS", frozenset()) | getattr(engine, "GENERIC_WORDS", frozenset())
    terms: list[str] = []
    for word in "".join(c if c.isalnum() else " " for c in idea.lower()).split():
        if len(word) >= MATCH_MIN_TERM_LENGTH and word not in stop and word not in terms:
            terms.append(word)
    return terms


def match_terms(text: str, terms: Sequence[str]) -> tuple[list[str], bool]:
    """Which idea terms appear in a candidate's name+description, and is it on-domain."""
    lowered = text.lower()
    hits = sorted(term for term in terms if term in lowered)
    on_domain = len(hits) >= 2 or (len(hits) == 1 and len(hits[0]) >= WEAK_MATCH_MIN_LENGTH)
    return hits, on_domain


def named_competitors(signal: Mapping[str, Any], terms: Sequence[str]) -> list[dict[str, Any]]:
    """Decode upstream's `top_similars` into per-source competitor records.

    Upstream prefixes non-GitHub entries (`npm:foo`, `pypi:bar`) and zero-fills
    `stars`/`updated` for them. Those zeros mean "not applicable", so they
    become `null` here rather than travelling as measurements.

    Off-domain candidates are annotated, never dropped: upstream orders weak
    matches last rather than removing them, and the skeptic wants to see what
    was rejected as much as what was kept.
    """
    competitors: list[dict[str, Any]] = []
    for item in signal.get("top_similars") or []:
        name = item.get("name") or ""
        if name.startswith("npm:"):
            source, name = "npm", name[4:]
            stars: int | None = None
        elif name.startswith("pypi:"):
            source, name = "pypi", name[5:]
            stars = None
        else:
            source = "github"
            raw_stars = item.get("stars")
            stars = raw_stars if isinstance(raw_stars, int) else None
        description = (item.get("description") or "").strip()
        hits, on_domain = match_terms(f"{name} {description}", terms)
        competitors.append(
            {
                "name": name,
                "source": source,
                "url": item.get("url") or None,
                "stars": stars,
                "description": description or None,
                "last_updated": (item.get("updated") or "").strip() or None,
                "matched_terms": hits,
                "on_domain": on_domain,
            }
        )
    return competitors


def temporal_inputs(
    results: Mapping[str, Any], health: Sequence[Mapping[str, str]]
) -> dict[str, float]:
    """The ratios upstream's momentum (and therefore `trend`) is built from.

    Mirrors `compute_signal`'s own selection exactly: GitHub contributes only
    when it found repos, HN only when it could compute a recency ratio. When
    this is empty upstream falls back to a neutral 0.5 and reports "stable" --
    a manufactured "flat" that must not reach a card, so `trend_direction`
    becomes null instead.
    """
    inputs: dict[str, float] = {}
    github = results.get("github")
    if github is not None and usable(health, "github") and github.total_repo_count > 0:
        inputs["github_created_last_6mo_ratio"] = round(github.recent_ratio, 4)
    hackernews = results.get("hackernews")
    if (
        hackernews is not None
        and usable(health, "hackernews")
        and hackernews.recent_mention_ratio is not None
    ):
        inputs["hn_mentions_last_3mo_ratio"] = round(hackernews.recent_mention_ratio, 4)
    return inputs


def saturation_read(
    on_domain: int,
    surfaced: int,
    capture_ok: bool,
    gaps: Sequence[str] = (),
) -> str:
    """Plain-language saturation label -- a transparent function of the on-domain
    candidate count only.

    Deliberately not derived from `reality_signal`: §3.8 forbids a blended
    number driving a panel, and the composite is deflated whenever a source
    fails (PyPI always, as of now). Incumbent size is reported separately in
    `counts.github_max_stars` rather than folded in here.

    The two zero cases are different findings and get different labels. Nothing
    on-domain out of several candidates means the keyword extractor drifted, so
    saturation was not measured at all -- reporting "unsaturated" there would be
    the same fabrication as reporting a failed fetch as zero.

    `gaps` names every competitor source whose capture was not clean -- both the
    ones that answered nothing and the ones that answered only some queries. A
    *partial* capture failure is as misleading as a total one: with GitHub 403'd,
    "3 competitors / moderately saturated" from npm alone reads on a card as a
    measured field, and upstream reports the MAX across keyword variants, so a
    dropped variant silently lowers every count. Every caveat therefore lives
    inside `read`, because a card may copy only these four keys (cross-cutting
    rule 5: a source that failed is never reported as absence).
    """
    missing = ", ".join(gaps)
    if not capture_ok:
        detail = f" ({missing})" if missing else ""
        return f"unknown -- every competitor source failed to fetch{detail}"

    if on_domain == 0:
        if surfaced:
            base = (
                f"unknown -- idea-reality's keyword extraction went off-domain: {surfaced} "
                "candidate(s) surfaced, none matching the idea's own terms"
            )
        else:
            base = "no named competitors surfaced"
    else:
        if on_domain <= READ_SPARSE_MAX:
            base = "sparsely populated"
        elif on_domain <= READ_MODERATE_MAX:
            base = "moderately saturated"
        else:
            base = "heavily saturated"
        if surfaced > on_domain:
            base += f" ({surfaced - on_domain} of {surfaced} candidates discarded as off-domain)"

    if missing:
        base += f" -- FLOOR ONLY: incomplete capture ({missing})"
    return base


def build_counts(
    results: Mapping[str, Any], health: Sequence[Mapping[str, str]]
) -> dict[str, int | None]:
    """Measured totals, with `null` wherever the source did not answer.

    Every one of these is a keyword-breadth artifact -- "repos matching the
    broadest keyword variant", not "competitors" -- which is why the saturation
    block does not use them directly.
    """
    github = results.get("github")
    hackernews = results.get("hackernews")
    npm = results.get("npm")
    pypi = results.get("pypi")
    return {
        "github_repos_matching": github.total_repo_count if usable(health, "github") else None,
        "github_max_stars": github.max_stars if usable(health, "github") else None,
        "github_created_last_6mo": (
            github.recent_created_count if usable(health, "github") else None
        ),
        "hn_mentions_12mo": hackernews.total_mentions if usable(health, "hackernews") else None,
        "npm_packages": npm.total_count if usable(health, "npm") else None,
        "pypi_packages": pypi.total_count if usable(health, "pypi") else None,
    }


def build_saturation(
    signal: Mapping[str, Any],
    competitors: Sequence[Mapping[str, Any]],
    counts: Mapping[str, int | None],
    results: Mapping[str, Any],
    health: Sequence[Mapping[str, str]],
    selected: Sequence[str],
    terms: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the §4 `saturation` block plus a sibling basis block.

    `saturation` is held to exactly the four contract keys so it can be spliced
    verbatim into `cards/<cluster_id>.json`. Everything needed to re-derive or
    reject it lives in `saturation_basis`, which a card author may inline.
    """
    competitor_sources = [s for s in ("github", "npm", "pypi") if s in selected]
    live = [s for s in competitor_sources if usable(health, s)]
    # Queried but did not answer. Sources the caller deselected are not in
    # `competitor_sources` at all, so this never blames the caller's own scope.
    unavailable = [s for s in competitor_sources if s not in live]
    # Every source whose capture was incomplete, at the granularity a reader
    # needs: nothing at all, or some queries lost. Both make the count a floor.
    gaps = [
        f"{s} {'unavailable' if s in unavailable else 'partial'}"
        for s in competitor_sources
        if s in unavailable or status_of(health, s) == "degraded"
    ]
    capture_ok = bool(live)

    surfaced = len(competitors)
    on_domain = sum(1 for c in competitors if c["on_domain"])

    ratios = temporal_inputs(results, health)
    upstream_trend = signal.get("trend")
    trend_direction = TREND_MAP.get(upstream_trend) if ratios else None

    saturation = {
        "source": SATURATION_SOURCE,
        "competitor_count": on_domain if capture_ok else None,
        "trend_direction": trend_direction,
        "read": saturation_read(on_domain, surfaced, capture_ok, gaps),
    }

    basis = {
        "source_basis": (
            f"`saturation.source` is CONTRACTS §4's provenance enum for which path answered: "
            f"'idea-reality' (MCP server) or '{SATURATION_SOURCE}' (this script). It is always "
            f"'{SATURATION_SOURCE}' here. The upstream package that produced the underlying "
            "numbers is reported separately as `upstream.package` / `upstream.version`."
        ),
        "competitor_count_basis": (
            "distinct named candidates with resolvable URLs that are on-domain for this idea, "
            "from idea-reality's deduped GitHub/npm/PyPI results. Upstream hard-caps the "
            "candidate list at 5 GitHub + 3 npm + 3 PyPI = 11 with Product Hunt excluded, so "
            "this is a 0-11 scale, not an open-ended count. Registry totals live in `counts` "
            "and are keyword-breadth artifacts, not competitor counts."
        ),
        "candidates_surfaced": surfaced,
        "candidates_on_domain": on_domain,
        "on_domain_rule": (
            f"a candidate is on-domain when its name+description contains >=2 idea terms, or 1 "
            f"term of >={WEAK_MATCH_MIN_LENGTH} characters. Mirrors upstream's own "
            "_filter_relevant_similars rule, but the terms come from the idea text only, not "
            "from the derived keywords -- the keywords are what may have drifted. Per-candidate "
            "hits are in `competitors[].matched_terms`; nothing is dropped, only annotated."
        ),
        "idea_terms": list(terms),
        "competitor_sources_queried": competitor_sources,
        "competitor_sources_usable": live,
        "competitor_sources_unavailable": unavailable,
        "competitor_capture_gaps": gaps,
        "capture_complete": not gaps,
        "read_basis": (
            f"transparent thresholds on competitor_count: 0 / 1-{READ_SPARSE_MAX} sparsely "
            f"populated / {READ_SPARSE_MAX + 1}-{READ_MODERATE_MAX} moderately saturated / "
            f"{READ_MODERATE_MAX + 1}+ heavily saturated. Not derived from reality_signal "
            "(§3.8: no blended number drives a panel). Incumbent size is not folded in; see "
            "counts.github_max_stars. When a queried competitor source answered nothing "
            "('unavailable') or only some queries ('partial'), `read` carries a 'FLOOR ONLY' "
            "caveat naming it -- the count is then a lower bound. The caveat lives in `read` "
            "because a card may copy only the four saturation keys."
        ),
        "trend_direction_basis": (
            "upstream `trend`, remapped stable->flat. Upstream derives it from mean market "
            "momentum: >0.6 accelerating, <0.3 declining, else stable. Momentum is the mean of "
            "the ratios in `trend_direction_inputs`."
        ),
        "trend_direction_inputs": ratios or None,
        "trend_direction_upstream": upstream_trend,
    }
    if surfaced and not on_domain:
        basis["off_domain_detail"] = (
            "idea-reality's dictionary keyword extractor produced queries that share no "
            "vocabulary with this idea, so the candidates it returned are not competitors. "
            "Saturation was not measured. Re-run with a more software-shaped --idea string, or "
            "treat this block as unusable rather than as evidence of a clear field."
        )
    if not ratios:
        basis["trend_direction_null_reason"] = (
            "no temporal input survived: GitHub found no repos and/or HN could not supply a "
            "recency ratio. Upstream would report 'stable' from its neutral 0.5 default; that "
            "manufactured flat is suppressed here."
        )
    return saturation, basis


# --------------------------------------------------------------------------
# The upstream composite, labelled as upstream
# --------------------------------------------------------------------------


def applied_weights(engine: Any, depth: str) -> dict[str, float] | None:
    """Reconstruct the weights `compute_signal` actually applied.

    Read from upstream's own presets so they track a version bump; only the
    Product Hunt redistribution rule is mirrored here (upstream pops the 0.15
    PH weight and spreads it proportionally when PH is unavailable, which it
    always is for us). Returns None if the presets have been renamed.
    """
    preset = "_QUICK_WEIGHTS" if depth == "quick" else "_DEEP_WEIGHTS"
    base = getattr(engine, preset, None)
    if not isinstance(base, dict):
        return None
    weights = dict(base)
    if depth != "quick":
        ph_weight = weights.pop("ph", 0.0)
        total = sum(weights.values())
        if total > 0:
            for key in list(weights):
                weights[key] += ph_weight * (weights[key] / total)
    return {k: round(v, 4) for k, v in weights.items()}


def build_composite(
    signal: Mapping[str, Any],
    engine: Any,
    depth: str,
    health: Sequence[Mapping[str, str]],
    selected: Sequence[str],
    ratios: Mapping[str, float],
) -> dict[str, Any]:
    """Pass `reality_signal` through, labelled as upstream-computed, with its
    subscores and the exact damage done by every missing source.

    §3.8 forbids presenting a blended number as our own analysis. The plugin
    still wants the number visible -- a reader may find it useful -- so it is
    reported with its formula, its weights, its inputs, and its deflation.
    """
    raw_sub = dict(signal.get("sub_scores") or {})
    weights = applied_weights(engine, depth)

    # Which weighted inputs are missing, and what that costs the composite.
    # Upstream scores an unavailable source as 0 and still applies its weight
    # (only Product Hunt gets redistributed), so a failed fetch pushes the
    # composite down -- toward "greenfield". That must be stated, not implied.
    weight_keys = {
        "github": ("github_repo", "github_star"),
        "hackernews": ("hn",),
        "npm": ("npm",),
        "pypi": ("pypi",),
    }
    missing: list[dict[str, Any]] = []
    deflation = 0.0
    for source, keys in weight_keys.items():
        if source in selected and usable(health, source):
            continue
        if depth == "quick" and source in ("npm", "pypi"):
            continue  # quick mode does not weight the registries at all
        lost = sum((weights or {}).get(k, 0.0) for k in keys)
        deflation += lost * 100
        missing.append(
            {
                "source": source,
                "reason": "excluded by --sources" if source not in selected else "fetch failed",
                "weight_still_applied_to_zero": round(lost, 4),
            }
        )

    # Zeros that mean "not measured" must not travel as measurements.
    subscores: dict[str, Any] = {
        "competition_density": raw_sub.get("competition_density") if usable(health, "github") else None,
        "market_maturity": raw_sub.get("market_maturity") if usable(health, "github") else None,
        "community_buzz": raw_sub.get("community_buzz") if usable(health, "hackernews") else None,
        "ecosystem_depth_npm": raw_sub.get("ecosystem_depth_npm") if usable(health, "npm") else None,
        "ecosystem_depth_pypi": raw_sub.get("ecosystem_depth_pypi") if usable(health, "pypi") else None,
        "product_launches": None,
        "market_momentum": raw_sub.get("market_momentum") if ratios else None,
    }

    caveats = [
        "Upstream-computed. This is NOT this plugin's analysis and no card panel is derived "
        "from it (CONTRACTS §3.8: no panel may be blended into a composite score).",
        "product_launches is null because Product Hunt requires PRODUCTHUNT_TOKEN and is "
        "excluded; upstream would report 0, which reads as 'no launches'.",
    ]
    if deflation > 0:
        caveats.append(
            f"Deflated by up to {deflation:.1f} of 100 points: "
            + ", ".join(
                f"{m['source']} scores 0 at weight {m['weight_still_applied_to_zero']} "
                f"({m['reason']})"
                for m in missing
            )
            + ". A lower reality_signal here means less evidence, not less competition."
        )
    if not ratios:
        caveats.append(
            "The +/-10 temporal boost used a neutral 0.5 momentum default because no recency "
            "ratio was available, so the boost contributed 0 rather than a measurement."
        )

    return {
        "reality_signal": signal.get("reality_signal"),
        "scale": "0-100; higher = more already-built",
        "duplicate_likelihood": signal.get("duplicate_likelihood"),
        "computed_by": f"idea-reality-mcp {signal.get('meta', {}).get('version')} "
        "scoring.engine.compute_signal",
        "our_analysis": False,
        "formula": (
            "sum(weight * min(100, k*ln(1+count))) per source, then a +/-10 boost from mean "
            "recency ratios, clamped to 0-100"
        ),
        "weights_applied": weights,
        "weights_note": (
            "read from upstream's own presets; the Product Hunt redistribution (its 0.15 spread "
            "proportionally across the rest) is mirrored by this wrapper, not returned by upstream"
        ),
        "subscores": subscores,
        "subscores_raw": raw_sub,
        "missing_weighted_inputs": missing,
        "deflation_max_points": round(deflation, 1),
        "trustworthy": not missing,
        "caveats": caveats,
    }


# --------------------------------------------------------------------------
# CONTRACTS §2 evidence records
# --------------------------------------------------------------------------


def evidence_records(
    competitors: Sequence[Mapping[str, Any]], cell_id: str | None, captured_utc: int
) -> list[dict[str, Any]]:
    """One §2 record per on-domain named competitor that has a resolvable URL.

    Off-domain candidates are excluded here even though they are kept in
    `competitors`: this file feeds `cluster.py`, and a repo that shares no
    vocabulary with the idea would become cluster mass for a pain nobody
    described.

    `query` is null: upstream aggregates across keyword variants and never
    records which variant surfaced a given hit, and inventing the attribution
    would break the "exact query string" contract. `created_utc` is null for
    the same reason -- no source here reports a creation date.
    """
    records: list[dict[str, Any]] = []
    for item in competitors:
        url = item.get("url")
        if not url or not item.get("on_domain"):
            continue  # no constructed or guessed URLs, no off-domain noise
        source = item["source"]
        stars = item.get("stars")
        author = None
        if source == "github" and "/" in (item.get("name") or ""):
            author = item["name"].split("/", 1)[0]
        records.append(
            {
                "id": hashlib.sha1(f"{source}|{url}".encode()).hexdigest(),
                "cell_id": cell_id,
                "source": source,
                "url": url,
                "title": item.get("name"),
                "text": item.get("description"),
                "author": author,
                "community": None,
                "engagement": {"score": stars, "comments": None} if stars is not None else None,
                "created_utc": None,
                "captured_utc": captured_utc,
                "query": None,
            }
        )
    return records


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

EPILOG = """\
examples:
  # saturation read for a card (CONTRACTS §4 saturation block)
  uv run --quiet scripts/reality_cli.py --idea "permit status tracking for city permitting offices"

  # persist alongside the run, and emit §2 evidence for the named competitors
  uv run --quiet scripts/reality_cli.py --idea "AI meeting notes for court reporters" \\
      --out runs/my-slug/saturation.json \\
      --evidence-out runs/my-slug/evidence/github.jsonl --cell-id m01

  # skip the registries (GitHub + HN only, ~2x faster, upstream 'quick' weights)
  uv run --quiet scripts/reality_cli.py --idea "self-hosted status page" --sources github,hackernews

  # fewer GitHub calls when you are near the 10 req/min unauthenticated ceiling
  uv run --quiet scripts/reality_cli.py --idea "invoice reconciliation for freight brokers" \\
      --max-keywords 2

notes:
  * stdout is always JSON: {saturation, saturation_basis, upstream_composite,
    competitors, counts, raw, source_health, ...}. `saturation` holds exactly the
    four §4 keys so it can be spliced into a card verbatim, and its `source` is
    always "reality_cli.py" -- §4's provenance enum for "the script fallback
    answered, not the MCP server".
  * if a competitor source answered nothing, or answered only some queries,
    `read` carries a "FLOOR ONLY: incomplete capture (...)" caveat naming it.
    The count is still real; it is just a lower bound. PyPI is always in that
    list -- see above.
  * exit 0 whenever at least one source completed a fetch, even with 0 competitors.
    exit 1 only when every selected source failed.
  * PyPI is expected to report `unavailable`: pypi.org now answers scripted
    clients with an HTTP 200 anti-bot page and idea-reality 0.5.0 silently
    scrapes 0 projects from it. The count is nulled, never reported as zero.
  * producthunt is not selectable. It requires PRODUCTHUNT_TOKEN and this plugin
    reads no credentials; see `credential_paths_excluded` in the output.
  * every candidate is relevance-checked against the idea's own words. If none
    match, `read` reports that the keyword extractor went off-domain rather than
    reporting a clear field -- idea-reality's extractor is tuned for developer
    tools and drifts on vertical ideas.
  * GitHub spends 2 requests per keyword variant against a 10 req/min
    unauthenticated ceiling, so runtime is roughly 6.5s * 2 * --max-keywords.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reality_cli.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Script fallback for the idea-reality stdio MCP server. Calls idea-reality-mcp's "
            "key-free GitHub/HN/npm/PyPI source functions in-process and maps the result into "
            "the CONTRACTS §4 saturation block, so /prospect's saturation read and /diligence's "
            "novelty check complete identically whether or not the MCP server loaded. Reads no "
            "API key, ever."
        ),
        epilog=EPILOG,
    )
    parser.add_argument(
        "--idea",
        required=True,
        metavar="TEXT",
        help="One-line idea or space description. Fed to idea-reality's dictionary keyword "
        "extractor, which is tuned for developer tools -- see `keywords` in the output.",
    )
    parser.add_argument(
        "--sources",
        default=",".join(DEFAULT_SOURCES),
        metavar="LIST",
        help="Comma-separated subset of: " + ", ".join(DEFAULT_SOURCES) + " ('hn' aliases "
        "'hackernews'). Default: all four. producthunt is not available (needs a token).",
    )
    parser.add_argument(
        "--max-keywords",
        type=int,
        default=3,
        metavar="N",
        help="Keyword variants to query (default: 3, upstream generates 3-8). GitHub spends 2 "
        "requests per variant against a 10 req/min ceiling, so this bounds runtime.",
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Persist this JSON payload to PATH (parents created). Still printed to stdout.",
    )
    parser.add_argument(
        "--evidence-out",
        default=None,
        metavar="PATH",
        help="Append CONTRACTS §2 evidence JSONL for each on-domain named competitor with a "
        "resolvable URL. Off-domain candidates are excluded so they cannot become cluster mass.",
    )
    parser.add_argument(
        "--cell-id",
        default=None,
        help="Matrix cell id from inputs.json (e.g. m01), stamped onto every evidence record.",
    )
    return parser


def resolve_sources(raw: str) -> tuple[list[str], list[str]]:
    selected: list[str] = []
    unknown: list[str] = []
    for token in raw.split(","):
        name = token.strip().lower()
        if not name:
            continue
        name = SOURCE_ALIASES.get(name, name)
        if name in SOURCE_HOSTS:
            if name not in selected:
                selected.append(name)
        else:
            unknown.append(token.strip())
    return [s for s in DEFAULT_SOURCES if s in selected], unknown


def emit_fatal(idea: str | None, detail: str) -> int:
    """Print a contract-shaped failure payload and return exit code 1.

    Even a total failure must leave stdout parseable and carry `source_health`
    and a `saturation` block, because the caller is an agent reading stdout
    directly with no wrapper. `competitor_count` is null, never 0.
    """
    print(
        json.dumps(
            {
                "tool": "reality_cli.py",
                "fallback_for": "idea-reality MCP server",
                "idea": idea,
                "saturation": {
                    "source": SATURATION_SOURCE,
                    "competitor_count": None,
                    "trend_direction": None,
                    "read": "unknown -- the saturation check did not run",
                },
                "competitors": [],
                "counts": {},
                "raw": None,
                "source_health": [
                    {"source": "idea-reality", "status": "unavailable", "detail": detail}
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 1


def persist(path_text: str, payload: Any, *, jsonl: bool = False) -> dict[str, Any]:
    path = Path(path_text).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if jsonl:
            with path.open("a", encoding="utf-8") as handle:
                for record in payload:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            written = len(payload)
        else:
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", "utf-8")
            written = 1
    except OSError as exc:
        log(f"WARNING: could not write {path} ({exc})")
        return {"path": str(path), "written": 0, "error": str(exc)}
    log(f"  wrote {written} record(s) to {path}")
    return {"path": str(path), "written": written, "error": None}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    idea = args.idea.strip()
    if not idea:
        parser.error("--idea must not be empty")
    if args.max_keywords < 1:
        parser.error("--max-keywords must be >= 1")

    selected, unknown = resolve_sources(args.sources)
    for name in unknown:
        log(f"WARNING: unknown source {name!r} -- ignored. Known: {', '.join(DEFAULT_SOURCES)}.")
    if not selected:
        return emit_fatal(
            idea,
            f"no known sources selected (unrecognised: {', '.join(map(repr, unknown)) or 'none'}); "
            f"valid names are {', '.join(DEFAULT_SOURCES)}",
        )

    try:
        upstream = load_upstream()
    except UpstreamImportError as exc:
        return emit_fatal(idea, str(exc))

    engine = upstream["engine"]
    version = upstream["version"]
    health: list[dict[str, str]] = []
    if version != AUDITED_VERSION:
        health.append(
            {
                "source": "idea-reality",
                "status": "degraded",
                "detail": (
                    f"installed idea-reality-mcp {version}; this wrapper's credential audit, "
                    f"composite weight reconstruction and PyPI finding were verified against "
                    f"{AUDITED_VERSION}. Re-verify before trusting `upstream_composite`."
                ),
            }
        )
    else:
        health.append(
            {
                "source": "idea-reality",
                "status": "ok",
                "detail": (
                    f"idea-reality-mcp {version} called in-process via sources.* + "
                    "scoring.engine.compute_signal (no stdio JSON-RPC, no MCP server import); "
                    f"{len(CREDENTIAL_PATHS_EXCLUDED)} credential/egress paths excluded"
                ),
            }
        )

    keywords_all = engine.extract_keywords(idea)
    keywords_used = keywords_all[: args.max_keywords]
    log(f"reality_cli: idea={idea!r}")
    log(f"  keywords (all {len(keywords_all)}): {json.dumps(keywords_all, ensure_ascii=False)}")
    log(f"  keywords (using {len(keywords_used)}): {json.dumps(keywords_used, ensure_ascii=False)}")

    governor = _HostGovernor(HOST_DELAYS)
    _install_governor(governor)

    started = time.monotonic()
    results = asyncio.run(gather_sources(upstream, selected, keywords_used, governor))
    elapsed = time.monotonic() - started
    log(f"  fetch complete in {elapsed:.1f}s, {len(governor.observations)} request(s)")

    for source in DEFAULT_SOURCES:
        health.append(source_status(governor, source, source in selected))
    health.append(
        {
            "source": "producthunt",
            "status": "unavailable",
            "detail": (
                "excluded by design: idea-reality's Product Hunt source reads PRODUCTHUNT_TOKEN "
                "and this plugin reads no credentials. Upstream redistributes its 0.15 composite "
                "weight across the remaining sources"
            ),
        }
    )

    for source in DEFAULT_SOURCES:
        if source not in results:
            results[source] = empty_results(engine, source)

    # Quick mode uses a different, GitHub-dominated weight preset upstream, so
    # the depth label must reflect what we actually asked for.
    depth = "quick" if not ({"npm", "pypi"} & set(selected)) else "deep"
    signal = engine.compute_signal(
        idea_text=idea,
        keywords=keywords_all,
        github_results=results["github"],
        hn_results=results["hackernews"],
        depth=depth,
        npm_results=results["npm"] if "npm" in selected else None,
        pypi_results=results["pypi"] if "pypi" in selected else None,
        ph_results=None,
    )

    terms = distinctive_terms(engine, idea)
    competitors = named_competitors(signal, terms)
    counts = build_counts(results, health)
    saturation, basis = build_saturation(
        signal, competitors, counts, results, health, selected, terms
    )
    ratios = temporal_inputs(results, health)
    composite = build_composite(signal, engine, depth, health, selected, ratios)

    captured_utc = int(time.time())
    payload: dict[str, Any] = {
        "tool": "reality_cli.py",
        "fallback_for": "idea-reality MCP server",
        "upstream": {
            "package": "idea-reality-mcp",
            "version": version,
            "audited_version": AUDITED_VERSION,
            "invocation": "in-process plain functions (sources.* + scoring.engine)",
            "depth": depth,
            "sources_queried": selected,
            "sources_unavailable_upstream": ["producthunt"],
            "no_stackoverflow": (
                "0.5.0 ships no StackOverflow source; the plugin spec's "
                "'GitHub/HN/npm/PyPI/StackOverflow' is wrong on the last one"
            ),
        },
        "idea": idea,
        "queried_utc": captured_utc,
        "elapsed_seconds": round(elapsed, 1),
        "keywords": {
            "all": keywords_all,
            "used": keywords_used,
            "extractor": "idea-reality dictionary pipeline (scoring.engine.extract_keywords)",
            "note": (
                "no LLM and no network: a fixed synonym/anchor table. Tuned for developer tools, "
                "so vertical or domain ideas often degrade to generic tokens. Upstream also "
                "reports the MAX count across variants, so the broadest variant sets the "
                "numbers in `counts` -- compare against saturation_basis.candidates_on_domain "
                "before trusting them."
            ),
        },
        "saturation": saturation,
        "saturation_basis": basis,
        "upstream_composite": composite,
        "competitors": competitors,
        "counts": counts,
        "counts_basis": (
            "totals reported by each source for its broadest keyword variant. Keyword-breadth "
            "artifacts, not competitor counts. null means the source did not answer -- never 0."
        ),
        "credential_paths_excluded": CREDENTIAL_PATHS_EXCLUDED,
        "raw": {
            "github": asdict(results["github"]) if "github" in selected else None,
            "hackernews": asdict(results["hackernews"]) if "hackernews" in selected else None,
            "npm": asdict(results["npm"]) if "npm" in selected else None,
            "pypi": asdict(results["pypi"]) if "pypi" in selected else None,
            "compute_signal": signal,
            "http_observations": governor.observations,
        },
        "source_health": health,
    }

    if args.evidence_out:
        records = evidence_records(competitors, args.cell_id, captured_utc)
        payload["evidence_persisted"] = persist(args.evidence_out, records, jsonl=True)
        payload["evidence_count"] = len(records)
    if args.out:
        # The receipt is added before the write so the file and stdout carry
        # identical content -- an agent can parse either one interchangeably.
        payload["persisted"] = {"path": str(Path(args.out).expanduser())}
        persist(args.out, payload)

    print(json.dumps(payload, indent=2, ensure_ascii=False))

    # exit 0 = fetching worked, even with zero competitors (requirement 7).
    if not any(usable(health, s) for s in selected):
        log("FATAL: every selected source failed; gathered nothing usable.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
