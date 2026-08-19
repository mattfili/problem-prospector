#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["requests"]
# ///
"""Regression test for the Arctic Shift throttle ladder in reddit_search.py.

Found live: a full-text query against r/sysadmin answered
`HTTP 422: Timeout. Maybe slow down a bit`, the single retry answered the same,
pullpush then answered `429`, and the cell captured nothing. The archive times out
on a heavy query whatever our cadence is, so asking for progressively less is the
only recovery that works.

What must stay true, and is checked here:

  * a "slow down" 422 is retried more than once, at successively smaller limits;
  * the backoff doubles at each rung and the host interval is widened each time,
    so we obey "slow down" harder rather than hammering at the same cadence;
  * a `limit` rejection still gets exactly one retry — the host told us the number
    was wrong, and repeating that is pointless;
  * **403 and 429 are never retried.** That is the repo's no-evasion discipline and
    the reason this test exists next to the ladder rather than trusting the comment.

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
    print("arctic shift throttle ladder:")

    # --- recovers on a later rung -------------------------------------------
    result, effective, fetcher, sleeps = run([timeout(), timeout(), ok()])
    check("retries past the first attempt", len(fetcher.limits) == 3,
          f"made {len(fetcher.limits)} requests")
    check("walks the limit ladder down", fetcher.limits == [100, 50, 25],
          f"limits {fetcher.limits}")
    check("recovers and reports the effective limit", result.ok and effective == 25,
          f"ok={result.ok} effective={effective}")
    check("backoff doubles", sleeps == [3.0, 6.0], f"sleeps {sleeps}")
    check("widens the host interval each rung", len(fetcher.throttle.widened) == 2,
          f"widened {fetcher.throttle.widened}")

    # --- exhausts the ladder and reports the failure honestly ---------------
    result, effective, fetcher, sleeps = run([timeout()] * 5)
    check("exhausts the ladder", fetcher.limits == [100, 50, 25, 10],
          f"limits {fetcher.limits}")
    check("still fails cleanly after the ladder", not result.ok and effective == 10,
          f"ok={result.ok} effective={effective}")
    check("backoff schedule is 3/6/12", sleeps == [3.0, 6.0, 12.0], f"sleeps {sleeps}")

    # --- a 429 arriving mid-walk ends it, and is never retried --------------
    throttled = rs.FetchResult(False, 429, None, "HTTP 429")
    result, _, fetcher, _ = run([timeout(), throttled, ok()])
    check("429 mid-walk stops the ladder", fetcher.limits == [100, 50],
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
