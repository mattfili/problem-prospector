#!/usr/bin/env python3
"""Validate cluster.py stdout against docs/CONTRACTS.md §3.

Reads the JSON on stdin. Exits 0 if it satisfies the contract, 1 otherwise with
the specific violation on stderr. Lives in a file rather than inline in
smoke.sh because nesting f-string quotes inside a shell-quoted `python -c` is
a reliable way to write a test that fails on its own syntax rather than on the
thing it is testing.

    uv run scripts/cluster.py fixture.jsonl | python3 tests/validate_clusters.py
"""

import json
import sys

# Exactly the per-cluster keys CONTRACTS §3 requires. Drift here silently
# breaks every downstream agent, so it is checked field by field.
REQUIRED_CLUSTER_KEYS = {
    "cluster_id",
    "canonical",
    "member_count",
    "distinct_authors",
    "distinct_communities",
    "engagement_sum",
    "cell_ids",
    "exemplar_urls",
    "member_ids",
}

REQUIRED_TOP_KEYS = {"clusters", "unclustered_ids"}


def fail(msg: str) -> None:
    print(f"contract violation: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        fail("empty stdout")
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"stdout is not valid JSON ({exc})")

    missing_top = REQUIRED_TOP_KEYS - set(doc)
    if missing_top:
        fail(f"missing top-level keys {sorted(missing_top)}")

    clusters = doc["clusters"]
    if not clusters:
        fail("no clusters produced from the fixture")

    for cluster in clusters:
        missing = REQUIRED_CLUSTER_KEYS - set(cluster)
        if missing:
            fail(f"cluster {cluster.get('cluster_id')} missing {sorted(missing)}")
        # member_ids must actually account for member_count, otherwise the
        # frequency panel downstream would report a weight it cannot evidence.
        if len(cluster["member_ids"]) != cluster["member_count"]:
            fail(
                f"cluster {cluster['cluster_id']}: member_count "
                f"{cluster['member_count']} != len(member_ids) "
                f"{len(cluster['member_ids'])}"
            )
        if cluster["distinct_authors"] > cluster["member_count"]:
            fail(f"cluster {cluster['cluster_id']}: more authors than members")

    # Reported by cluster.py so a reader can tell which backend and which
    # distance cut produced the grouping. The adaptive cut is the whole point
    # of the approach, so its absence means something regressed.
    if not doc.get("cut_basis"):
        fail("cut_basis not reported")

    sizes = sorted((c["member_count"] for c in clusters), reverse=True)
    model = doc.get("embedding_model") or doc.get("backend") or "unknown"
    print(
        f"        {len(clusters)} clusters, sizes {sizes}, "
        f"model={model}, cut={doc['cut_basis']}, "
        f"unclustered={len(doc['unclustered_ids'])}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
