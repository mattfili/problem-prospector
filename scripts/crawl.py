#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["crawl4ai"]
# ///
"""Fetch public web pages as clean markdown for the /diligence stage.

WHY THIS EXISTS
    /diligence has to make claims about real competitors: what they charge, what
    they ship, what they say they do. Those claims are only allowed to come from
    something we actually read (docs/CONTRACTS.md cross-cutting rule 1: no
    invented prices). This script is the only sanctioned way to turn a
    competitor URL into text an agent may quote, and its JSON manifest is the
    record of what was genuinely retrieved versus what was not.

    The manifest matters as much as the markdown. A pricing page that failed to
    load looks exactly like a pricing page with no prices once it is a 0-byte
    file, and that false negative would silently corrupt the "Pricing potential"
    section of diligence.md. So every URL comes back with an explicit status:

        ok            markdown retrieved, plausibly complete
        degraded      200-ish response but near-empty text (JS-only render,
                      consent wall, bot interstitial) - DO NOT read this as
                      "no pricing found"
        robots-denied robots.txt forbids us; we did not fetch
        blocked       auth wall / paywall / 401 / 403 / 429; we did not and
                      will not try to get past it
        failed        the fetch itself broke, or the host served an error page
                      (4xx/5xx) instead of the page we asked for - that body is
                      never kept, because a 500 page full of nav boilerplate
                      would otherwise read as usable competitor evidence

POSITION IN THE PIPELINE
    /diligence -> crawl.py (this script) -> diligence.md sections
    (Competition, Novelty, Pricing potential). Consumed alongside
    cards/<cluster_id>.json and wedges/<cluster_id>.json.

DISCIPLINE, MADE STRUCTURAL RATHER THAN ADVISORY
    - robots.txt is consulted per host before the first fetch to that host and
      its Crawl-delay is honoured. There is deliberately no --ignore-robots
      flag; the polite path is the only path.
    - Per-host requests are serialised with a floor of 1s between them.
      --concurrency only buys parallelism across *different* hosts.
    - The crawler cannot authenticate. It has no credential input, submits no
      forms, and carries no cookies from a logged-in profile. A page behind a
      login is recorded as blocked and skipped. A 403/429 circuit-breaks that
      host for the rest of the run instead of being retried or worked around.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse

TOOL_UA = "problem-prospector/0.1 (+https://github.com/mattfili/problem-prospector)"

# Floor between two requests to the same host. A site's own Crawl-delay can
# raise this but never lower it.
MIN_HOST_DELAY_S = 1.0

# Content shorter than this (after stripping markdown scaffolding) from an
# otherwise successful response means we almost certainly did not get the real
# page. Calibrated against real SaaS pricing pages, which run 8k-20k chars;
# even a terse one clears 400 chars of prose.
DEFAULT_MIN_CONTENT_CHARS = 400

# One page must not be able to eat a downstream context window.
DEFAULT_MAX_CHARS = 120_000

# Paths that mean "you are being asked to log in". Matched against the final
# URL after redirects, so a pricing page that 302s to /login is caught.
AUTH_PATH_RE = re.compile(
    r"(^|/)(login|log-in|signin|sign-in|sign_in|sso|oauth|auth[0-9]?|"
    r"authorize|session[s]?/new|users/sign_in|account[s]?/login|"
    r"checkout/login|paywall|subscribe)(/|$|\?)",
    re.I,
)
AUTH_HOST_RE = re.compile(
    r"(accounts\.google\.com|login\.microsoftonline\.com|github\.com/login|"
    r"auth0\.com|okta\.com|onelogin\.com)",
    re.I,
)

# Phrases that identify a wall or an interstitial. Only trusted on SHORT pages:
# a real pricing page routinely contains the words "Sign in" in its nav, so
# these are evidence only when there is nothing else on the page.
WALL_MARKERS = (
    "sign in to continue",
    "log in to continue",
    "sign in to your account",
    "log in to your account",
    "please log in",
    "please sign in",
    "forgot your password",
    "you must be logged in",
    "login required",
    "create an account to continue",
    "subscribe to read",
    "subscribe to continue",
    "this content is for members",
    "members only",
    "enable javascript",
    "javascript is required",
    "javascript is disabled",
    "verify you are human",
    "verifying you are human",
    "checking your browser",
    "just a moment",
    "attention required",
    "access denied",
    "captcha",
    "unusual traffic",
)

STATUS_ORDER = ("ok", "degraded", "robots-denied", "blocked", "failed")


def log(msg: str) -> None:
    """Diagnostics go to stderr; stdout is reserved for the JSON manifest."""
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# URL handling
# --------------------------------------------------------------------------


def normalise_url(raw: str) -> str:
    """Add a scheme if the caller pasted a bare host. No other rewriting."""
    raw = raw.strip()
    if not raw:
        return raw
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        raw = "https://" + raw
    return raw


def slugify_url(url: str) -> str:
    """Filename from host+path, per the /diligence output convention.

    A short hash of the full URL is appended only when the host+path alone is
    ambiguous (query or fragment present), so `site.com/#pricing` and
    `site.com/` do not fight over one file while stable URLs keep readable
    names across reruns.
    """
    parsed = urlparse(url)
    base = f"{parsed.netloc}{parsed.path}"
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    slug = slug or "page"
    if len(slug) > 110:
        slug = slug[:110].rstrip("-")
    if parsed.query or parsed.fragment:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:6]
        slug = f"{slug}-{digest}"
    return slug


def read_urls_file(path: Path) -> list[str]:
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


# --------------------------------------------------------------------------
# robots.txt
# --------------------------------------------------------------------------


def _is_network_level(exc: BaseException) -> bool:
    """True when the host itself never answered (DNS, refused, TLS, timeout).

    This is the line between two very different manifest rows. A host that does
    not resolve is a *failed* fetch. A host that answers but cannot serve
    robots.txt leaves its rules unknown, which we treat as disallow. Collapsing
    the two would let "your DNS is wrong" masquerade as "that site said no".
    """
    candidates = [exc, getattr(exc, "reason", None)]
    return any(
        isinstance(c, (socket.gaierror, socket.timeout, ConnectionError, ssl.SSLError, TimeoutError, OSError))
        and not isinstance(c, urllib.error.HTTPError)
        for c in candidates
        if c is not None
    )


class RobotsCache:
    """One robots.txt fetch per host, cached for the run.

    Fetched with stdlib urllib rather than the browser: it is a tiny text file,
    and the decision to crawl has to be made *before* a browser touches the
    host. Follows RFC 9309 for the awkward cases - 4xx means allow all, 401/403
    means the whole host is off limits, and a robots.txt that a live host will
    not serve is treated as disallow rather than as permission.
    """

    # Decisions returned by check(): allow, deny (rules say no / rules unknown),
    # or unreachable (the host never answered at all).
    ALLOW = "allow"
    DENY = "deny"
    UNREACHABLE = "unreachable"

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout
        self._cache: dict[str, tuple[urllib.robotparser.RobotFileParser | None, str, bool]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, origin: str) -> asyncio.Lock:
        return self._locks.setdefault(origin, asyncio.Lock())

    def _fetch_sync(self, origin: str) -> tuple[urllib.robotparser.RobotFileParser | None, str, bool]:
        """Returns (parser_or_None, detail, host_unreachable)."""
        robots_url = f"{origin}/robots.txt"
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        req = urllib.request.Request(
            robots_url,
            headers={"User-Agent": TOOL_UA, "Accept": "text/plain,*/*"},
        )
        # One retry only, for a transient blip. RFC 9309 says retry then assume
        # disallow; it does not say keep probing.
        last_detail = ""
        unreachable = False
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    body = resp.read(512_000).decode("utf-8", errors="replace")
                parser.parse(body.splitlines())
                return parser, f"robots.txt {getattr(resp, 'status', 200)}", False
            except urllib.error.HTTPError as exc:
                unreachable = False
                if exc.code in (401, 403):
                    parser.disallow_all = True
                    return parser, f"robots.txt {exc.code} (host treated as disallow)", False
                if 400 <= exc.code < 500:
                    parser.allow_all = True
                    return parser, f"robots.txt {exc.code} (no robots.txt; allow all)", False
                last_detail = f"robots.txt HTTP {exc.code}"
            except Exception as exc:
                unreachable = _is_network_level(exc)
                reason = getattr(exc, "reason", None)
                last_detail = f"{origin} unreachable: {type(reason or exc).__name__}"
            if attempt == 1:
                time.sleep(1.0)
        return None, last_detail or "robots.txt unreachable", unreachable

    async def check(self, url: str) -> tuple[str, float | None, str]:
        """Return (decision, crawl_delay_seconds_or_None, detail)."""
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        async with self._lock(origin):
            if origin not in self._cache:
                log(f"[robots] fetching {origin}/robots.txt")
                self._cache[origin] = await asyncio.to_thread(self._fetch_sync, origin)
        parser, detail, unreachable = self._cache[origin]
        if parser is None:
            if unreachable:
                return self.UNREACHABLE, None, detail
            # A live host whose rules we cannot read has not granted permission.
            return self.DENY, None, f"{detail}; treating as disallow (RFC 9309)"
        path = urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, ""))
        allowed = parser.can_fetch(TOOL_UA, url)
        delay: float | None = None
        try:
            raw_delay = parser.crawl_delay(TOOL_UA)
            if raw_delay is not None:
                delay = float(raw_delay)
            else:
                rate = parser.request_rate(TOOL_UA)
                if rate is not None and rate.requests > 0:
                    delay = float(rate.seconds) / float(rate.requests)
        except Exception:
            delay = None
        verb = "allows" if allowed else "disallows"
        decision = self.ALLOW if allowed else self.DENY
        return decision, delay, f"{detail}; {verb} {path or '/'}"


# --------------------------------------------------------------------------
# Per-host pacing
# --------------------------------------------------------------------------


class HostPacer:
    """Serialises requests per host and enforces the inter-request delay.

    Serialising per host is the point: --concurrency parallelises across hosts,
    never within one, so a single site never sees two of our requests at once.
    """

    def __init__(self, min_delay: float = MIN_HOST_DELAY_S) -> None:
        self._min_delay = min_delay
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}
        self._delays: dict[str, float] = {}

    def note_crawl_delay(self, host: str, delay: float | None) -> None:
        if delay is None:
            return
        self._delays[host] = max(self._delays.get(host, 0.0), delay)

    def delay_for(self, host: str) -> float:
        return max(self._min_delay, self._delays.get(host, 0.0))

    @contextlib.asynccontextmanager
    async def slot(self, host: str):
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self.delay_for(host) - (time.monotonic() - self._last.get(host, 0.0))
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                yield
            finally:
                self._last[host] = time.monotonic()


# --------------------------------------------------------------------------
# Content judgement
# --------------------------------------------------------------------------


def content_chars(markdown: str) -> int:
    """Rough count of prose, ignoring markdown scaffolding.

    Link-only pages (nav bars, cookie shells) collapse to near zero here while
    a real pricing table stays large, which is what separates degraded from ok.
    """
    text = re.sub(r"!?\[[^\]]*\]\([^)]*\)", " ", markdown)  # links and images
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[#>*_`|\-=+~]", " ", text)
    return len(re.sub(r"\s+", " ", text).strip())


def looks_like_wall(final_url: str, title: str | None, markdown: str) -> str | None:
    """Return a reason string if this response is an auth/paywall/interstitial."""
    parsed = urlparse(final_url or "")
    if AUTH_HOST_RE.search(parsed.netloc or ""):
        return f"redirected to identity provider host {parsed.netloc}"
    if AUTH_PATH_RE.search(parsed.path or ""):
        return f"redirected to auth path {parsed.path}"
    haystack = f"{title or ''}\n{markdown[:4000]}".lower()
    # Only short pages are judged by phrase: every pricing page has a "Sign in"
    # link in its header and must not be discarded for it.
    if content_chars(markdown) < 3000:
        for marker in WALL_MARKERS:
            if marker in haystack:
                return f"wall/interstitial marker: {marker!r}"
    return None


PLAYWRIGHT_HINT_MARKERS = (
    "executable doesn't exist",
    "playwright install",
    "please run the following command to download new browsers",
    "browsertype.launch",
    "looks like playwright was just installed",
)


def is_missing_browser(exc: BaseException) -> bool:
    blob = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in blob for marker in PLAYWRIGHT_HINT_MARKERS)


BROWSER_FIX_HINT = (
    "crawl4ai has no browser yet - fix with: "
    "uv run --with crawl4ai python -m playwright install chromium   (or: crawl4ai-setup)"
)


# --------------------------------------------------------------------------
# Crawl
# --------------------------------------------------------------------------


def write_markdown(
    out_dir: Path, slug: str, url: str, title: str | None, markdown: str, fetched_utc: int, robots_detail: str
) -> Path:
    """Write one page with a provenance header so a quote can be traced back."""
    header = [
        "---",
        f"source_url: {url}",
        f"title: {json.dumps(title) if title else 'null'}",
        f"fetched_utc: {fetched_utc}",
        f"fetched_iso: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(fetched_utc))}",
        f"robots: {robots_detail}",
        f"crawler: {TOOL_UA}",
        "---",
        "",
    ]
    path = out_dir / f"{slug}.md"
    path.write_text("\n".join(header) + markdown, encoding="utf-8")
    return path


async def crawl_one(
    crawler: Any,
    run_config_factory: Any,
    url: str,
    slug: str,
    out_dir: Path | None,
    robots: RobotsCache,
    pacer: HostPacer,
    broken_hosts: dict[str, str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "url": url,
        "status": "failed",
        "out_path": None,
        "markdown_chars": 0,
        "title": None,
        "fetched_utc": None,
        "http_status": None,
        "robots_allowed": None,
        "truncated": False,
        "detail": None,
    }
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        record["detail"] = f"unsupported scheme {parsed.scheme!r}; only http/https"
        return record
    host = parsed.netloc

    if host in broken_hosts:
        record["status"] = "blocked"
        record["detail"] = f"host circuit-broken earlier this run: {broken_hosts[host]}"
        log(f"[skip] {url} - {record['detail']}")
        return record

    decision, crawl_delay, robots_detail = await robots.check(url)
    pacer.note_crawl_delay(host, crawl_delay)
    if decision == RobotsCache.UNREACHABLE:
        # Host never answered. That is a broken fetch, not a site saying no.
        record["status"] = "failed"
        record["detail"] = f"{robots_detail} (could not reach host to read robots.txt)"
        log(f"[failed] {url} - {record['detail']}")
        return record
    record["robots_allowed"] = decision == RobotsCache.ALLOW
    if decision != RobotsCache.ALLOW:
        record["status"] = "robots-denied"
        record["detail"] = robots_detail
        log(f"[robots-denied] {url} - {robots_detail}")
        return record
    if crawl_delay is not None and crawl_delay > args.max_crawl_delay:
        # Honouring a very long Crawl-delay would stall the run; crawling faster
        # than asked is not an option, so we decline the page instead.
        record["status"] = "robots-denied"
        record["detail"] = (
            f"robots.txt Crawl-delay {crawl_delay:g}s exceeds --max-crawl-delay "
            f"{args.max_crawl_delay:g}s; declined rather than crawled faster than asked"
        )
        log(f"[robots-denied] {url} - {record['detail']}")
        return record

    async with pacer.slot(host):
        # Re-check the breaker from inside the per-host slot. The check at the
        # top of this function ran before any sibling had fetched, so with
        # several URLs on one host it always saw an empty breaker; only in here,
        # after waiting our turn behind them, is a 403/429 they just earned
        # visible. Without this, "circuit-break the host" degraded into
        # "annotate every request we already fired at it".
        #
        # This is sound because the caller trips the breaker (below) with no
        # await between releasing this lock and the assignment, so the next
        # waiter cannot wake up in between. Keep it that way.
        if host in broken_hosts:
            record["status"] = "blocked"
            record["detail"] = f"host circuit-broken earlier this run: {broken_hosts[host]}"
            log(f"[skip] {url} - {record['detail']}")
            return record
        fetched = int(time.time())
        record["fetched_utc"] = fetched
        log(f"[fetch] {url} (host delay {pacer.delay_for(host):g}s)")
        try:
            result = await asyncio.wait_for(
                crawler.arun(url=url, config=run_config_factory()),
                timeout=args.timeout + 20,
            )
        except asyncio.TimeoutError:
            record["detail"] = f"timed out after {args.timeout + 20:g}s"
            log(f"[failed] {url} - {record['detail']}")
            return record
        except Exception as exc:
            if is_missing_browser(exc):
                record["detail"] = BROWSER_FIX_HINT
                log(f"[failed] {url} - {BROWSER_FIX_HINT}")
                return record
            record["detail"] = f"{type(exc).__name__}: {exc}".strip()[:300]
            log(f"[failed] {url} - {record['detail']}")
            return record

    status_code = getattr(result, "status_code", None)
    record["http_status"] = status_code
    # redirected_url first: crawl4ai leaves .url as the URL we asked for, so
    # checking it first would miss a pricing page that 30x'd to /login.
    final_url = getattr(result, "redirected_url", None) or getattr(result, "url", None) or url
    metadata = getattr(result, "metadata", None) or {}
    title = metadata.get("title") or None
    record["title"] = title

    if not getattr(result, "success", False):
        err = (getattr(result, "error_message", None) or "").strip()
        if err and any(m in err.lower() for m in PLAYWRIGHT_HINT_MARKERS):
            record["detail"] = BROWSER_FIX_HINT
        elif status_code in (401, 402, 403, 407):
            record["status"] = "blocked"
            record["detail"] = f"HTTP {status_code}; access controlled, not bypassed"
            broken_hosts[host] = f"HTTP {status_code}"
        elif status_code == 429:
            record["status"] = "blocked"
            record["detail"] = "HTTP 429; host rate-limited us, circuit-breaking it"
            broken_hosts[host] = "HTTP 429"
        else:
            record["detail"] = f"crawl failed (HTTP {status_code}): {err[:220] or 'no error message'}"
        log(f"[{record['status']}] {url} - {record['detail']}")
        return record

    if status_code in (401, 402, 403, 407, 429):
        record["status"] = "blocked"
        record["detail"] = f"HTTP {status_code}; access controlled, not bypassed"
        broken_hosts[host] = f"HTTP {status_code}"
        log(f"[blocked] {url} - {record['detail']}")
        return record

    if status_code is not None and status_code >= 400:
        # A 404/410/500 body is the server's error page, not the page we asked
        # for - and error pages on marketing sites carry the whole nav, so they
        # clear the prose threshold and would otherwise be recorded `ok`. That is
        # the worst failure this script has: diligence.md quoting a 500 page as
        # what a competitor charges. Nothing is written for these.
        record["status"] = "failed"
        record["detail"] = f"HTTP {status_code}; served an error page, not the requested page"
        log(f"[failed] {url} - {record['detail']}")
        return record

    md_obj = getattr(result, "markdown", None)
    markdown = getattr(md_obj, "raw_markdown", None) or (str(md_obj) if md_obj else "") or ""
    prose = content_chars(markdown)

    wall = looks_like_wall(final_url, title, markdown)
    if wall:
        record["status"] = "blocked"
        record["detail"] = f"{wall}; auth/paywall not attempted (prose chars {prose})"
        log(f"[blocked] {url} - {record['detail']}")
        return record

    if len(markdown) > args.max_chars:
        markdown = (
            markdown[: args.max_chars].rstrip()
            + f"\n\n<!-- truncated by crawl.py at --max-chars={args.max_chars} -->\n"
        )
        record["truncated"] = True

    record["markdown_chars"] = len(markdown)

    if out_dir is not None:
        try:
            path = write_markdown(out_dir, slug, url, title, markdown, fetched, robots_detail)
            record["out_path"] = str(path)
        except OSError as exc:
            record["detail"] = f"markdown retrieved but not written: {exc}"
            record["status"] = "degraded"
            log(f"[degraded] {url} - {record['detail']}")
            return record

    # prose is measured on the whole retrieved page, before any --max-chars cut,
    # so truncation can never make a page look degraded.
    details: list[str] = [f"HTTP {status_code}", f"page prose chars {prose}"]
    if record["truncated"]:
        details.append(f"truncated to {args.max_chars} chars")
    if prose < args.min_content_chars:
        record["status"] = "degraded"
        details.append(
            "near-empty for a 2xx/3xx response: likely client-side rendered, "
            "consent-gated, or an interstitial. NOT evidence of an empty page"
        )
        log(f"[degraded] {url} - {details[-1]}")
    else:
        record["status"] = "ok"
        log(f"[ok] {url} - {record['markdown_chars']} md chars, {prose} prose chars")
    record["detail"] = "; ".join(details)
    return record


async def crawl_all(urls: list[str], out_dir: Path | None, args: argparse.Namespace) -> tuple[list[dict[str, Any]], str | None]:
    """Returns (records, engine_error). engine_error set if the browser never started."""
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    except Exception as exc:  # pragma: no cover - dependency resolution problem
        return [], f"crawl4ai import failed: {type(exc).__name__}: {exc}"

    browser_config = BrowserConfig(
        headless=True,
        verbose=False,
        user_agent=TOOL_UA,
        # No storage_state, no cookies, no proxy: the crawler is structurally
        # incapable of presenting someone's session or hiding its identity.
    )

    def run_config_factory() -> Any:
        return CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,  # pricing changes; never serve a stale page to diligence
            page_timeout=int(args.timeout * 1000),
            verbose=False,
            # crawl4ai's default block filter drops short text nodes, which is
            # exactly where prices live ("$9", "per seat"). Keep everything.
            word_count_threshold=1,
            # Let client-side pricing tables finish painting before extraction;
            # this is the single biggest lever on false "no pricing found".
            delay_before_return_html=args.settle,
            excluded_tags=["script", "style", "noscript"],
        )

    robots = RobotsCache(timeout=min(15.0, args.timeout))
    pacer = HostPacer()
    broken_hosts: dict[str, str] = {}
    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    slugs: dict[str, str] = {}
    used: set[str] = set()
    for url in urls:
        slug = slugify_url(url)
        candidate, n = slug, 2
        while candidate in used:
            candidate = f"{slug}-{n}"
            n += 1
        used.add(candidate)
        slugs[url] = candidate

    results: dict[str, dict[str, Any]] = {}

    async def worker(url: str) -> None:
        async with semaphore:
            results[url] = await crawl_one(
                crawler, run_config_factory, url, slugs[url], out_dir, robots, pacer, broken_hosts, args
            )

    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            await asyncio.gather(*(worker(u) for u in urls))
    except Exception as exc:
        if is_missing_browser(exc):
            return [], BROWSER_FIX_HINT
        return [], f"browser session failed: {type(exc).__name__}: {exc}".strip()[:300]

    return [results[u] for u in urls], None


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def build_source_health(records: list[dict[str, Any]], engine_error: str | None) -> list[dict[str, str]]:
    health: list[dict[str, str]] = []
    if engine_error:
        health.append({"source": "crawl4ai", "status": "unavailable", "detail": engine_error})
        return health

    statuses = [r["status"] for r in records]
    usable = sum(1 for s in statuses if s in ("ok", "degraded"))
    if not records:
        health.append({"source": "crawl4ai", "status": "unavailable", "detail": "no URLs supplied"})
        return health
    if usable == len(records) and all(s == "ok" for s in statuses):
        engine_status, engine_detail = "ok", f"{len(records)}/{len(records)} pages retrieved"
    elif usable:
        engine_status = "degraded"
        engine_detail = f"{usable}/{len(records)} pages retrieved as usable markdown"
    else:
        engine_status = "unavailable"
        engine_detail = f"0/{len(records)} pages retrieved as usable markdown"
    health.append({"source": "crawl4ai", "status": engine_status, "detail": engine_detail})

    by_host: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_host.setdefault(urlparse(record["url"]).netloc or "(unparsed)", []).append(record)
    for host, group in by_host.items():
        counts: dict[str, int] = {}
        for record in group:
            counts[record["status"]] = counts.get(record["status"], 0) + 1
        if set(counts) == {"ok"}:
            status = "ok"
        elif counts.get("ok", 0) or counts.get("degraded", 0):
            status = "ok" if set(counts) <= {"ok"} else "degraded"
        else:
            status = "unavailable"
        detail = ", ".join(f"{counts[s]} {s}" for s in STATUS_ORDER if s in counts)
        # Quote the most serious outcome, not the first one, so a host with both
        # a failure and a degrade explains the failure.
        worst = next(
            (r for s in reversed(STATUS_ORDER) for r in group if r["status"] == s and s != "ok" and r["detail"]),
            None,
        )
        if worst and status != "ok":
            detail = f"{detail} ({worst['detail']})"
        health.append({"source": f"web:{host}", "status": status, "detail": detail})
    return health


def build_manifest(
    records: list[dict[str, Any]], out_dir: Path | None, engine_error: str | None, started: int
) -> dict[str, Any]:
    counts = {status: 0 for status in STATUS_ORDER}
    for record in records:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    return {
        "tool": "crawl.py",
        "crawled_utc": started,
        "out_dir": str(out_dir) if out_dir else None,
        "requested": len(records),
        "counts": counts,
        "pages": records,
        "source_health": build_source_health(records, engine_error),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


EPILOG = """\
examples:
  # two competitor pricing pages into a run directory
  uv run --quiet scripts/crawl.py \\
      --url https://plausible.io/#pricing \\
      --url https://www.metabase.com/pricing/ \\
      --out runs/my-run-2026-07-31/crawl

  # a list of docs/changelog URLs, one per line ('#' comments allowed)
  uv run --quiet scripts/crawl.py --urls-file competitors.txt --out crawl/ --concurrency 2

  # manifest only, no files on disk
  uv run --quiet scripts/crawl.py --url https://www.metabase.com/pricing/ | jq '.pages[0].status'

statuses in the manifest:
  ok             usable markdown
  degraded       response arrived but near-empty text (JS render / consent wall).
                 Never read this as "the page has no pricing".
  robots-denied  robots.txt forbids the path, or its Crawl-delay exceeds
                 --max-crawl-delay. Nothing was fetched.
  blocked        auth wall, paywall, 401/402/403/407 or 429. Not bypassed, by design.
  failed         the fetch broke, or the host answered 4xx/5xx with an error
                 page instead of the page requested. Nothing is written.

exit codes:
  0  at least one page yielded markdown (ok or degraded); see source_health for
     anything that degraded
  1  nothing usable was gathered, or the browser could not start

first run: if you see the missing-browser hint, run
  uv run --with crawl4ai python -m playwright install chromium
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="crawl.py",
        description=(
            "Fetch public web pages as clean markdown for /diligence, with a JSON manifest "
            "saying exactly which URLs were retrieved, which were near-empty, which were "
            "forbidden by robots.txt, and which sit behind a wall. Key-free: it authenticates "
            "to nothing and has no --ignore-robots flag."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--url", action="append", default=[], metavar="URL", help="URL to crawl; repeatable")
    parser.add_argument("--urls-file", type=Path, metavar="PATH", help="file of URLs, one per line")
    parser.add_argument("--out", type=Path, metavar="DIR", help="directory for one .md per URL (slugified host+path)")
    parser.add_argument("--concurrency", type=int, default=3, metavar="N", help="parallel hosts (default 3; per-host requests are always serialised)")
    parser.add_argument("--timeout", type=float, default=45.0, metavar="S", help="per-page page-load timeout in seconds (default 45)")
    parser.add_argument("--settle", type=float, default=1.5, metavar="S", help="wait after load for client-side rendering (default 1.5)")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, metavar="N", help=f"truncate each page's markdown (default {DEFAULT_MAX_CHARS})")
    parser.add_argument(
        "--min-content-chars",
        type=int,
        default=DEFAULT_MIN_CONTENT_CHARS,
        metavar="N",
        help=f"below this much prose a 2xx page is flagged degraded, not empty (default {DEFAULT_MIN_CONTENT_CHARS})",
    )
    parser.add_argument(
        "--max-crawl-delay",
        type=float,
        default=15.0,
        metavar="S",
        help="decline a page whose robots.txt Crawl-delay exceeds this (default 15); we never crawl faster than asked",
    )
    parser.add_argument("--manifest-out", type=Path, metavar="PATH", help="also write the JSON manifest here")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = int(time.time())

    raw = list(args.url)
    if args.urls_file:
        if not args.urls_file.exists():
            manifest = build_manifest([], None, f"--urls-file not found: {args.urls_file}", started)
            print(json.dumps(manifest, indent=2))
            log(f"[fatal] --urls-file not found: {args.urls_file}")
            return 1
        raw.extend(read_urls_file(args.urls_file))
    urls = dedupe(normalise_url(u) for u in raw)

    if not urls:
        manifest = build_manifest([], None, "no URLs supplied (use --url and/or --urls-file)", started)
        print(json.dumps(manifest, indent=2))
        log("[fatal] no URLs supplied. See --help.")
        return 1

    out_dir: Path | None = None
    if args.out:
        out_dir = args.out
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            manifest = build_manifest([], None, f"cannot create --out {out_dir}: {exc}", started)
            print(json.dumps(manifest, indent=2))
            log(f"[fatal] cannot create --out {out_dir}: {exc}")
            return 1

    log(f"[start] {len(urls)} URL(s), concurrency {args.concurrency}, out={out_dir or '(manifest only)'}")

    # crawl4ai's progress logger writes to stdout. stdout belongs to the
    # manifest, so the whole crawl runs with stdout aliased to stderr and the
    # JSON is emitted afterwards to the real stdout.
    real_stdout = sys.stdout
    try:
        with contextlib.redirect_stdout(sys.stderr):
            records, engine_error = asyncio.run(crawl_all(urls, out_dir, args))
    except KeyboardInterrupt:
        log("[fatal] interrupted")
        return 1

    if engine_error:
        # Every URL still gets a row: an unstarted browser is a failure to
        # fetch, never "these pages had nothing".
        records = [
            {
                "url": u,
                "status": "failed",
                "out_path": None,
                "markdown_chars": 0,
                "title": None,
                "fetched_utc": None,
                "http_status": None,
                "robots_allowed": None,
                "truncated": False,
                "detail": engine_error,
            }
            for u in urls
        ]
        log(f"[fatal] {engine_error}")

    manifest = build_manifest(records, out_dir, engine_error, started)
    payload = json.dumps(manifest, indent=2)

    if args.manifest_out:
        try:
            args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
            args.manifest_out.write_text(payload + "\n", encoding="utf-8")
        except OSError as exc:
            log(f"[warn] could not write --manifest-out: {exc}")

    print(payload, file=real_stdout)

    usable = sum(1 for r in records if r["status"] in ("ok", "degraded"))
    counts = manifest["counts"]
    log(
        "[done] "
        + ", ".join(f"{counts[s]} {s}" for s in STATUS_ORDER if counts.get(s))
        + f" | usable {usable}/{len(records)}"
    )
    return 0 if usable else 1


if __name__ == "__main__":
    sys.exit(main())
