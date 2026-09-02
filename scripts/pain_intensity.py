#!/usr/bin/env python3
"""Stage 3's intensity panel: scored from evidence on disk, never from a claim.

WHY THIS IS THE STRICTEST MODULE IN THE BUNDLE
----------------------------------------------
`intensity.score` is the primary sort key of the whole pipeline and the gate on
`--pain high`. §3.3 makes it citable on purpose — "no quote, no marker" — but as
prose that rule is enforced only by the diligence of whichever model is reading
it, and the failure is silent: a marker set `true` on a paraphrase, a level 4
claimed off one articulate author, a score of 4 beside a `read` of "medium".
Nothing downstream can tell.

So this module accepts marker *evidence*, not a score. For every quote it:

* checks the quote is <=15 words;
* checks the URL belongs to a record in *this* cluster, not the run at large;
* checks the quote appears verbatim in that record's captured `title`/`text`,
  which is what makes paraphrase and ellipsis-stitching mechanically impossible;
* resolves the author **off disk**, so the distinct-author counts that carry
  levels 3, 4 and 5 cannot be asserted by a caller.

Then `pain_rubric.derive_intensity` reads the level off the ladder. If any single
entry fails validation nothing is written: a partially validated panel scored
from whatever survived is the silent-skip failure this repo exists to prevent.

WHAT THE CALLER STILL OWNS
--------------------------
Which quote evidences which marker, and whether a cost is recurring. That is
reading comprehension. Everything after it is arithmetic.
"""

from __future__ import annotations

import pain_rubric as rubric
from pain_cards import load_card, write_card
from pain_stages import evidence_records, read_json, run_dir

#: Author strings that do not identify a person. Counting them as distinct
#: authors would let three deleted accounts satisfy a ">=2 distinct authors" leg.
NON_IDENTIFYING_AUTHORS = {
    "", "none", "[deleted]", "[removed]", "u/[deleted]", "u/[removed]",
    "deleted", "removed", "automoderator", "u/automoderator",
}

MAX_EXEMPLARS = 6


def _member_index(slug: str, member_ids: list[str]) -> dict[str, dict]:
    """Map every URL in this cluster's membership to its evidence record."""
    members = set(member_ids)
    index: dict[str, dict] = {}
    records, _ = evidence_records(slug)
    for record in records:
        if record.get("id") in members and record.get("url"):
            index[str(record["url"]).rstrip("/")] = record
    return index


def _validate_entry(entry: dict, marker: str, index: dict[str, dict]) -> dict:
    """Validate one quote against the cluster's evidence. Returns a verdict dict."""
    quote = str(entry.get("quote") or "").strip()
    url = str(entry.get("url") or "").strip()
    problem = None
    record = None

    if not quote:
        problem = "empty quote"
    elif rubric.word_count(quote) > rubric.MAX_EXEMPLAR_WORDS:
        problem = (f"quote is {rubric.word_count(quote)} words; the cap is "
                   f"{rubric.MAX_EXEMPLAR_WORDS}. A long quote stops being evidence "
                   "for one specific marker and starts being a paragraph that "
                   "mentions it — trim to the phrase that proves the marker")
    elif not url.startswith("http"):
        problem = "url must be a real resolvable permalink from the captured evidence"
    else:
        record = index.get(url.rstrip("/"))
        if record is None:
            problem = ("url is not a member of this cluster — an exemplar must come "
                       "from the cluster it scores")
        else:
            haystack = rubric.normalize_quote(
                " ".join(str(record.get(f) or "") for f in ("title", "text"))
            )
            needle = rubric.normalize_quote(quote)
            if needle not in haystack:
                lowered = needle.lower() in haystack.lower()
                problem = (
                    "quote differs from the source only in case — quotes must match "
                    "the captured text exactly, or they are not citable"
                    if lowered else
                    "quote does not appear verbatim in the captured title/text of that "
                    "record; paraphrase and ellipsis-stitched fragments are not citable"
                )

    author = str((record or {}).get("author") or "").strip()
    identifying = author.lower() not in NON_IDENTIFYING_AUTHORS
    return {
        "marker": marker, "quote": quote, "url": url, "problem": problem,
        "author": author if identifying else None,
        "author_not_identifying": bool(record) and not identifying,
        "recurring": bool(entry.get("recurring")),
        "words": rubric.word_count(quote),
    }


def _collect(verdicts: list[dict]) -> tuple[set[str], dict[str, set[str]], set[str], list[str]]:
    """Fold validated verdicts into presence, per-marker author sets, and recurring.

    Presence and author count are tracked separately: a quote from a deleted
    account still evidences its marker, but it never becomes a distinct author,
    because three deleted accounts must not satisfy a ">=2 distinct authors" leg.
    """
    present: set[str] = set()
    marker_authors: dict[str, set[str]] = {m: set() for m in rubric.MARKERS}
    recurring: set[str] = set()
    notes: list[str] = []
    anonymous = 0

    for verdict in verdicts:
        marker = verdict["marker"]
        present.add(marker)
        if verdict["author"]:
            marker_authors[marker].add(verdict["author"])
        else:
            anonymous += 1
        if not verdict["recurring"]:
            continue
        if marker in rubric.COST_MARKERS:
            if verdict["author"]:
                recurring.add(verdict["author"])
        else:
            notes.append(
                f"recurring flag ignored on {marker}: level 5's recurring leg counts "
                "cost markers only"
            )

    if anonymous:
        notes.append(
            f"{anonymous} quote(s) came from a deleted or non-identifying author: they "
            "evidence their marker but contribute no distinct author, so they cannot "
            "carry a level-3-or-higher leg"
        )
    return present, marker_authors, recurring, notes


def _exemplars(verdicts: list[dict]) -> list[dict]:
    """Pick exemplars that demonstrate the markers set true, cost markers first.

    Deduped by quote *text*, not by (quote, url): the same sentence cited from
    three authors is one exemplar plus a distinct-author count, and the count is
    already carried by the derivation. Printing it three times reads as three
    findings.
    """
    def rank(verdict: dict) -> int:
        if verdict["marker"] in rubric.COST_MARKERS:
            return 0
        return 1 if verdict["marker"] == "complainer_is_buyer" else 2

    chosen: list[dict] = []
    seen: set[str] = set()
    for verdict in sorted(verdicts, key=rank):
        key = rubric.normalize_quote(verdict["quote"]).lower()
        if key in seen:
            continue
        seen.add(key)
        chosen.append({"quote": verdict["quote"], "url": verdict["url"],
                       "words": verdict["words"]})
        if len(chosen) >= MAX_EXEMPLARS:
            break
    return chosen


def score_intensity(
    slug: str,
    cluster_id: str,
    marker_evidence: dict[str, list[dict]],
    canonical_pain: str | None = None,
) -> dict:
    """Validate marker evidence, derive §3.3 Axis B, write `intensity` + `quadrant`.

    Refuses when the inventory gate has not run (§3.7 is applied at every
    promotion point) and when any quote fails validation — a panel scored from
    the entries that happened to survive is worse than no panel, because it looks
    identical to a clean one.
    """
    unknown = sorted(set(marker_evidence) - set(rubric.MARKERS))
    if unknown:
        return {"ok": False, "error": f"unknown marker(s) {unknown}; the rubric has "
                                     f"exactly these six: {list(rubric.MARKERS)}"}

    path, card = load_card(slug, cluster_id)
    verdict = (card.get("inventory_gate") or {}).get("verdict")
    if verdict is None:
        return {"ok": False, "error": "set the inventory gate on this cluster first — "
                                      "every promotion point re-checks it so excluded "
                                      "businesses never absorb paid analysis"}
    if verdict == "exclude":
        return {"ok": False, "error": f"{cluster_id} was excluded at the inventory gate; "
                                      "excluded cards keep a null intensity panel by design"}

    clusters = read_json(run_dir(slug) / "clusters.json") or {}
    cluster = next(
        (c for c in clusters.get("clusters", []) if c.get("cluster_id") == cluster_id), None
    )
    if cluster is None:
        return {"ok": False, "error": f"{cluster_id} is not in clusters.json"}

    index = _member_index(slug, cluster.get("member_ids") or [])
    verdicts = [
        _validate_entry(entry, marker, index)
        for marker, entries in marker_evidence.items()
        for entry in (entries or [])
    ]
    rejected = [
        {"marker": v["marker"], "quote": v["quote"][:80], "url": v["url"],
         "rejected_because": v["problem"]}
        for v in verdicts if v["problem"]
    ]
    if rejected:
        return {
            "ok": False, "cluster_id": cluster_id, "rejected": rejected,
            "error": f"{len(rejected)} of {len(verdicts)} quotes failed validation; "
                     "nothing was written. Fix or drop each one and call again — a "
                     "panel scored from the survivors would look identical to a clean one.",
            "cluster_member_urls": len(index),
        }

    present, marker_authors, recurring, collect_notes = _collect(verdicts)
    derived = rubric.derive_intensity(present, marker_authors, recurring)
    note_parts = [p for p in ([derived["note"]] + collect_notes) if p]

    card["intensity"] = {
        "score": derived["score"],
        "markers": derived["markers"],
        "exemplars": _exemplars(verdicts),
        "read": derived["read"],
        "note": "; ".join(note_parts) or None,
    }
    card["quadrant"] = rubric.quadrant(
        (card.get("frequency") or {}).get("read") or "low", derived["score"]
    )
    if canonical_pain and canonical_pain.strip():
        card["canonical_pain"] = canonical_pain.strip()
    write_card(path, card)

    return {
        "ok": True, "cluster_id": cluster_id,
        "score": derived["score"], "read": derived["read"],
        "markers": derived["markers"], "quadrant": card["quadrant"],
        "quadrant_read": rubric.QUADRANT_READS[card["quadrant"]],
        "note": card["intensity"]["note"],
        "derivation": derived["derivation"],
        "exemplars_written": len(card["intensity"]["exemplars"]),
        "quotes_validated": len(verdicts),
    }
