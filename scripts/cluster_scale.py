"""cluster_scale.py — the numpy path that lets cluster.py handle thousands of items.

WHY THIS EXISTS
    cluster.py was written for evidence pools of a few hundred items, in pure
    Python so the offline backend needed no scientific stack. Real runs now
    produce 6-9k items (an 11-cell x 4-query frame), and at that size the pure
    path stops working: a 6,776-item run was killed at the 1,800 s subprocess
    timeout, and a 9,133-item run died silently of memory on an 8 GB box —
    the full distance matrix was held as ~83M Python floats, then copied to
    lists twice more, then copied again into numpy.

    This module is the same arithmetic on arrays. Every function here mirrors a
    pure-Python function in cluster.py and is held to the same answer by
    tests/test_cluster_scale.py (same labels, same cut to 1e-12). cluster.py
    picks this path whenever numpy imports, and falls back to its own code
    when it does not.

MEMORY SHAPE
    Only the upper triangle of the distance matrix is ever materialised (the
    "condensed" form scipy uses: n(n-1)/2 float64), built a block of rows at a
    time so the full n x n matrix never exists. At 9,133 items that is 333 MB
    instead of the 667 MB square plus three list copies that killed the run.

PERCENTILES WITHOUT THE DOUBLED LIST
    cluster.py's adaptive cut is a percentile over the OFF-DIAGONAL entries of
    the square matrix, which lists every pair twice. Rather than build that
    2x list, the doubled sorted sequence is indexed through the sorted upper
    triangle: element k of the doubled list is element k // 2 of the single
    one. Same interpolation formula, same answer, half the memory.

Stdlib-importable: numpy is imported lazily, so cluster.py can import this
module on a box where numpy is absent and simply not take this path.
"""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from typing import Any, Callable, Sequence

#: Rows of the distance matrix computed per block. 512 x 10k float64 is ~40 MB,
#: small next to the condensed array itself.
BLOCK_ROWS = 512

#: Largest cluster whose medoid is found from an explicit k x k distance block
#: (exact tie-breaking, 128 MB at 4,000). Bigger clusters use the O(k * d)
#: identity, where an exact tie between two real posts is not a practical case.
MEDOID_EXACT_MAX = 4_000

#: Default ceiling on pool size for the dense path. Past this the condensed
#: matrix plus scipy's working copy approaches the RAM of an ordinary laptop,
#: and dying silently mid-run is the outcome this guard exists to prevent.
#: Override with PROSPECTOR_CLUSTER_MAX_ITEMS on a bigger box.
DEFAULT_MAX_ITEMS = 15_000

#: The pure-Python fallback (no numpy) holds n^2 Python floats; past a few
#: thousand items it is both slow and memory-bound.
DEFAULT_MAX_ITEMS_PURE = 4_000

#: Copies of the condensed matrix alive at peak. Two at any moment: the array
#: itself plus either the sorted copy used for percentiles (freed before
#: linkage) or scipy's working copy during linkage.
_PEAK_CONDENSED_COPIES = 2


def numpy_or_none() -> Any:
    """The numpy module, or None when it cannot be imported."""
    try:
        import numpy

        return numpy
    except ImportError:
        return None


# --------------------------------------------------------------------------
# Percentiles — one formula, shared with cluster.percentile()
# --------------------------------------------------------------------------

def interpolate_sorted(at: Callable[[int], float], count: int, pct: float) -> float:
    """Linear-interpolation percentile over a sorted sequence read through `at`.

    The exact formula cluster.percentile() uses, factored out so the pure and
    numpy paths cannot drift. `pct` is validated by the caller.
    """
    if count == 0:
        return 0.0
    rank = (pct / 100.0) * (count - 1)
    low, high = math.floor(rank), math.ceil(rank)
    if low == high:
        return float(at(int(low)))
    lo, hi = float(at(int(low))), float(at(int(high)))
    return lo + (hi - lo) * (rank - low)


def upper_percentile(sorted_upper: Any, pct: float) -> float:
    """Percentile over each pair once (cluster.py's median / p75 basis)."""
    return interpolate_sorted(sorted_upper.__getitem__, len(sorted_upper), pct)


def offdiag_percentile(sorted_upper: Any, pct: float) -> float:
    """Percentile over the full off-diagonal, where every pair appears twice.

    Element k of the doubled sorted list is element k // 2 of the single one,
    so this matches cluster.adaptive_cut() on the square matrix exactly
    without building a list twice the size.
    """
    return interpolate_sorted(
        lambda k: sorted_upper[k // 2], 2 * len(sorted_upper), pct
    )


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def as_matrix(vectors: Sequence[Sequence[float]]) -> Any:
    """float64 array of the vectors. float32 embeddings upcast exactly, so the
    values are the same numbers the pure path reads as Python floats."""
    np = numpy_or_none()
    return np.asarray(vectors, dtype=np.float64)


def condensed_distances(matrix: Any) -> Any:
    """Upper-triangle cosine distances, row-major (scipy's condensed form).

    Built a block of rows at a time so the n x n matrix never exists. Clamped
    at zero, as cluster.pairwise_matrix() does, because float noise can push
    identical vectors slightly negative and clusterers reject that.
    """
    np = numpy_or_none()
    n = matrix.shape[0]
    out = np.empty(n * (n - 1) // 2, dtype=np.float64)
    offset = 0
    for start in range(0, n, BLOCK_ROWS):
        stop = min(start + BLOCK_ROWS, n)
        block = matrix[start:stop] @ matrix.T
        np.subtract(1.0, block, out=block)
        np.maximum(block, 0.0, out=block)
        for row in range(start, stop):
            width = n - row - 1
            out[offset:offset + width] = block[row - start, row + 1:]
            offset += width
    return out


def square_from_condensed(condensed: Any) -> Any:
    """The full symmetric matrix, for the HDBSCAN path that needs it."""
    from scipy.spatial.distance import squareform

    return squareform(condensed, checks=False)


def medoid(matrix: Any, indices: Sequence[int]) -> int:
    """Member minimising summed cosine distance to its clustermates.

    Mirrors cluster.medoid_index(): sum over j != i of (1 - v_i . v_j), which
    is (k - 1) - (v_i . sum_j v_j - v_i . v_i). O(k * d) instead of O(k^2 * d),
    which is what makes a 3,000-member cluster take milliseconds. Up to
    MEDOID_EXACT_MAX members it instead sums an explicit symmetric distance
    block, so exact ties break the same way the pure version breaks them.
    """
    np = numpy_or_none()
    if len(indices) == 1:
        return indices[0]
    sub = matrix[list(indices)]
    if len(indices) > MEDOID_EXACT_MAX:
        totals = (len(indices) - 1) - (sub @ sub.sum(axis=0) - np.einsum("ij,ij->i", sub, sub))
        return indices[int(np.argmin(totals))]
    # Mirror the pure version's ties exactly. Its distances are symmetric bit
    # for bit (a . b == b . a), so in a 2-member cluster both totals are equal
    # and the first member wins. A matrix product is not guaranteed symmetric
    # to the last bit, so take the upper triangle and reflect it.
    sims = sub @ sub.T
    sims = np.triu(sims) + np.triu(sims, 1).T
    dist = 1.0 - sims
    np.fill_diagonal(dist, 0.0)
    return indices[int(np.argmin(dist.sum(axis=1)))]


def max_pair_distance(matrix: Any, indices: Sequence[int]) -> float:
    """Widest internal pair among `indices` (unclamped, as fusion_advisory reads it)."""
    np = numpy_or_none()
    sub = matrix[list(indices)]
    dist = 1.0 - sub @ sub.T
    upper = dist[np.triu_indices(len(indices), 1)]
    return float(upper.max())


# --------------------------------------------------------------------------
# Average linkage, built once and cut at any threshold
# --------------------------------------------------------------------------

def average_linkage(condensed: Any) -> Any:
    """scipy's average-linkage tree — the same call sklearn makes internally
    for a precomputed metric, minus sklearn's extra index arrays."""
    from scipy.cluster.hierarchy import linkage

    return linkage(condensed, method="average")


def labels_at_cut(tree: Any, n: int, cut: float) -> list[int]:
    """Flat labels from the linkage tree at `cut`, matching sklearn exactly.

    sklearn's AgglomerativeClustering(distance_threshold=cut) sets
    n_clusters = count(merge distances >= cut) + 1 and then undoes the latest
    merges until that many remain — i.e. it keeps the EARLIEST n - n_clusters
    merges. This does the same with a top-down label pass, O(n). Building the
    tree once and cutting it here is what makes probing several percentiles
    nearly free.
    """
    np = numpy_or_none()
    kept = n - (int(np.count_nonzero(tree[:, 2] >= cut)) + 1)
    label = [-1] * (n + kept)
    next_label = 0
    for step in range(kept - 1, -1, -1):
        node = n + step
        if label[node] == -1:
            label[node] = next_label
            next_label += 1
        left, right = int(tree[step, 0]), int(tree[step, 1])
        label[left] = label[node]
        label[right] = label[node]
    for leaf in range(n):
        if label[leaf] == -1:
            label[leaf] = next_label
            next_label += 1
    return label[:n]


def group_sizes(labels: Sequence[int]) -> list[int]:
    """Group sizes, largest first, ignoring HDBSCAN noise (-1)."""
    counts: dict[int, int] = {}
    for lab in labels:
        if lab >= 0:
            counts[lab] = counts.get(lab, 0) + 1
    return sorted(counts.values(), reverse=True)


# --------------------------------------------------------------------------
# Memory guard
# --------------------------------------------------------------------------

def max_items(dense: bool) -> int:
    """The pool-size ceiling for this path, honouring the env override."""
    raw = os.environ.get("PROSPECTOR_CLUSTER_MAX_ITEMS")
    if raw and raw.strip().isdigit():
        return int(raw)
    return DEFAULT_MAX_ITEMS if dense else DEFAULT_MAX_ITEMS_PURE


def estimated_gb(n: int, dense: bool) -> float:
    """Rough peak memory for clustering n items on this path, in GB."""
    pairs = n * (n - 1) / 2
    if dense:
        return round(pairs * 8 * _PEAK_CONDENSED_COPIES / 1e9, 1)
    # Pure path: n^2 boxed floats (~24 B) plus list pointers (8 B).
    return round(n * n * 32 / 1e9, 1)


# --------------------------------------------------------------------------
# Embedding cache — one vector per text, keyed by content
# --------------------------------------------------------------------------

def text_key(text: str) -> bytes:
    """sha1 of one embed text — the cache's per-item key."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest().encode("ascii")


class EmbeddingCache:
    """Per-text embedding store for one run, in a single .npz file.

    Keyed per text rather than per corpus so that capturing 500 more items
    re-embeds 500, not 9,000, and so that a run killed mid-embedding keeps the
    vectors it finished (they are checkpointed as they arrive). A file written
    under a different model is ignored, never mixed: distances are only
    comparable within one embedding space.
    """

    def __init__(self, path: Path, model: str) -> None:
        self.path = path
        self.model = model
        self.rows: dict[bytes, Any] = {}
        self.loaded = 0
        self.ignored_reason: str | None = None
        self._load()

    def _load(self) -> None:
        np = numpy_or_none()
        if not self.path.exists():
            return
        try:
            with np.load(self.path, allow_pickle=False) as data:
                if str(data["model"]) != self.model:
                    self.ignored_reason = f"cache built with {data['model']}, not {self.model}"
                    return
                for key, vec in zip(data["keys"], data["vectors"]):
                    self.rows[bytes(key)] = vec
        except (OSError, ValueError, KeyError) as exc:
            self.ignored_reason = f"unreadable cache ({type(exc).__name__}: {exc})"
            self.rows = {}
        self.loaded = len(self.rows)

    def get(self, key: bytes) -> Any:
        return self.rows.get(key)

    def add(self, keys: Sequence[bytes], vectors: Any) -> None:
        for key, vec in zip(keys, vectors):
            self.rows[key] = vec

    def save(self) -> None:
        """Atomic write: a reader never sees a half-written cache."""
        np = numpy_or_none()
        if not self.rows:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        keys = list(self.rows)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with tmp.open("wb") as handle:
            np.savez(
                handle,
                model=np.array(self.model),
                keys=np.array(keys, dtype="S40"),
                vectors=np.stack([self.rows[k] for k in keys]),
            )
        os.replace(tmp, self.path)
