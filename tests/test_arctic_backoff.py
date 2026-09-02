#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["requests"]
# ///
"""Regression test for the Arctic Shift timeout retries in reddit_search.py.

Measured live (2026-09-01, 20 query-cells at a fixed limit=30): the
`HTTP 422: Timeout. Maybe slow down a bit` answer is stochastic, not
size-correlated — ~36% of requests succeed per attempt, ~80% within four
attempts at a constant limit, and limit=5 can fail where limit=100 succeeds.
The earlier shrink-ladder (50/25/10) was built on the opposite premise and had
a dead zone: a caller limit at or below its smallest rung got ZERO retries,
and a shrunk limit fed back into pagination pinned every later page at the
floor. This file previously only exercised limit=100, which is why it never
caught that.

What must stay true, and is checked here:

  * a "slow down" 422 is retried up to TIMEOUT_MAX_RETRIES times at the SAME
    limit — including small caller limits (the dead-zone regression);
  * the effective limit returned on the timeout path is the caller's limit,
    so pagination never gets pinned smaller by a transient timeout;
  * the backoff doubles per retry and the host interval is widened each time,
    so we obey "slow down" harder rather than hammering at the same cadence;
  * a `limit` rejection still gets exactly one retry — the host told us the number
    was wrong, and repeating that is pointless;
  * **403 and 429 are never retried.** That is the repo's no-evasion discipline and
    the reason this test exists next to the retry loop rather than trusting the
    comment.

`time.sleep` is stubbed, so this runs in milliseconds and asserts on the backoff
schedule it *would* have used.

    uv run tests/test_arctic_backoff.py
"""

import importlib.util
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "reddit_search", REPO / "scripts" / "reddit_search.py"
)
rs = importlib.util.module_from_spec(spec)
sys.modules["reddit_search"] = rs
spec.loader.exec_module(rs)

URL = f"https://{rs.ARCTIC_HOST}/api/posts/search"
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))
        FAILURES.append(name)


class FakeThrottle:
    """Records every slow_down call; never actually waits."""

    def __init__(self) -> None:
        self.widened: list[float] = []
        self._interval = 1.2

    def wait(self, host: str) -> None:
        pass

    def slow_down(self, host: str, factor: float = 2.0, cap: float = 8.0) -> float:
        self._interval = min(self._interval * factor, cap)
        self.widened.append(self._interval)
        return self._interval


class ScriptedFetcher:
    """Returns a scripted sequence of FetchResults and records requested limits."""

    def __init__(self, results: list[rs.FetchResult]) -> None:
        self.results = list(results)
        self.limits: list[int] = []
        self.throttle = FakeThrottle()

    def get(self, url: str, params: dict) -> rs.FetchResult:
        self.limits.append(params.get("limit"))
        return self.results.pop(0) if self.results else rs.FetchResult(
            False, 500, None, "ran out of scripted results")


def timeout(status: int = 422) -> rs.FetchResult:
    return rs.FetchResult(False, status, None, "HTTP 422: Timeout. Maybe slow down a bit")


def ok() -> rs.FetchResult:
    return rs.FetchResult(True, 200, {"data": []}, None)


def run(results: list[rs.FetchResult], limit: int = 100):
    sleeps: list[float] = []
    original = rs.time.sleep
    rs.time.sleep = sleeps.append
    fetcher = ScriptedFetcher(results)
    try:
        result, effective = rs.arctic_get_with_recovery(
            fetcher, URL, {"limit": limit, "query": "permit software"}, label="r/sysadmin")
    finally:
        rs.time.sleep = original
    return result, effective, fetcher, sleeps


def main() -> int:
    print("arctic shift timeout retries:")

    # --- recovers on a later attempt, at the same limit ----------------------
    result, effective, fetcher, sleeps = run([timeout(), timeout(), ok()])
    check("retries past the first attempt", len(fetcher.limits) == 3,
          f"made {len(fetcher.limits)} requests")
    check("retries at a constant limit", fetcher.limits == [100, 100, 100],
          f"limits {fetcher.limits}")
    check("recovers and keeps the caller's limit", result.ok and effective == 100,
          f"ok={result.ok} effective={effective}")
    check("backoff doubles", sleeps == [3.0, 6.0], f"sleeps {sleeps}")
    check("widens the host interval each retry", len(fetcher.throttle.widened) == 2,
          f"widened {fetcher.throttle.widened}")

    # --- small caller limits get the same retries (the dead-zone regression) -
    result, effective, fetcher, sleeps = run([timeout(), ok()], limit=10)
    check("limit=10 is retried", fetcher.limits == [10, 10] and result.ok,
          f"limits {fetcher.limits} ok={result.ok}")
    result, effective, fetcher, sleeps = run([timeout(), timeout(), ok()], limit=5)
    check("limit=5 is retried with backoff", fetcher.limits == [5, 5, 5]
          and sleeps == [3.0, 6.0], f"limits {fetcher.limits} sleeps {sleeps}")

    # --- exhausts the retries and reports the failure honestly ---------------
    result, effective, fetcher, sleeps = run([timeout()] * 5)
    check("exhausts the retries", fetcher.limits == [100, 100, 100, 100],
          f"limits {fetcher.limits}")
    check("still fails cleanly after the retries", not result.ok and effective == 100,
          f"ok={result.ok} effective={effective}")
    check("backoff schedule is 3/6/12", sleeps == [3.0, 6.0, 12.0], f"sleeps {sleeps}")

    # --- a 429 arriving mid-walk ends it, and is never retried --------------
    throttled = rs.FetchResult(False, 429, None, "HTTP 429")
    result, _, fetcher, _ = run([timeout(), throttled, ok()])
    check("429 mid-walk stops the retries", fetcher.limits == [100, 100],
          f"limits {fetcher.limits}")
    check("429 is surfaced, not recovered", result.status == 429, f"status {result.status}")

    # --- a 403 is not recoverable at all -----------------------------------
    forbidden = rs.FetchResult(False, 403, None, "HTTP 403")
    result, _, fetcher, sleeps = run([forbidden, ok()])
    check("403 is never retried", fetcher.limits == [100], f"limits {fetcher.limits}")
    check("403 never sleeps", sleeps == [], f"sleeps {sleeps}")

    # --- a limit rejection still gets exactly one retry ---------------------
    rejected = rs.FetchResult(False, 400, None, "HTTP 400: 'limit' must be between 1 and 100")
    result, effective, fetcher, sleeps = run([rejected, ok()], limit=200)
    check("limit rejection retries once", fetcher.limits == [200, 50], f"limits {fetcher.limits}")
    check("limit rejection does not back off", sleeps == [], f"sleeps {sleeps}")

    # --- an already-small limit is not retried pointlessly ------------------
    result, effective, fetcher, sleeps = run([rejected, ok()], limit=10)
    check("small limit rejection is not retried", fetcher.limits == [10],
          f"limits {fetcher.limits}")

    # --- an unrelated failure is passed straight through -------------------
    boom = rs.FetchResult(False, 404, None, "HTTP 404")
    result, _, fetcher, _ = run([boom, ok()])
    check("unrecoverable status is not retried", fetcher.limits == [100],
          f"limits {fetcher.limits}")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all arctic backoff checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
