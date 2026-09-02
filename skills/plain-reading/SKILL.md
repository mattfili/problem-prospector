---
name: plain-reading
description: "Translates the plugin's output into language a first-time reader can act on — every time run results are presented to a human: a /pain-search or /prospect summary, a report walkthrough, an inspection artifact, an answer to 'what did the run find?'. Load it BEFORE writing the presentation, not after. Also applies when the user says the output is illegible, asks 'what does <term> mean', or asks for the plain-English version of a card, cluster, gate verdict, or quadrant. Do NOT use it to change what any stage computed — scores, reads, verdicts, and enum spellings are never reworded on disk; this skill governs how they are EXPLAINED, and the contract files keep their exact vocabulary."
---

# Plain reading — interpretation and explainability

## Why this exists

This plugin's stages speak in contract vocabulary because the files they write
are contracts: `distinct_communities`, `cut_basis`, `high-freq/low-intensity`,
`inventory_gate.verdict`. That precision is load-bearing on disk and illegible
in a sentence. A run whose findings the owner cannot decipher has produced
nothing — the evidence was captured, clustered, and scored, and then lost in
the last meter. This skill governs that last meter.

The one rule: **meaning first, mechanism available.** Every number, verdict,
and term reaches the reader as what it means for them, with the mechanical
name kept alongside (not hidden — the reader must be able to find the field
in the files), and the raw evidence one click or one path away.

## The translation contract

1. **Lead with the finding, in the reader's own stakes.** Not "c01 scored 3,
   quadrant high-freq/low-intensity" but "the most widespread pain is real but
   nobody cites what it cost them — which usually means an audience to write
   for, not software to sell. (c01, intensity 3 of 5.)" The mechanical handle
   follows in parentheses or a data row; it never leads.
2. **Every term of art is glossed at first use** — one clause, in place, drawn
   from `docs/GLOSSARY.md` (do not restate the glossary; adapt its sentence to
   the context). A term used once gets its gloss inline; a term the page uses
   throughout gets one visible definition where it first appears.
3. **Numbers carry their scale and their consequence.** "Intensity 3" is
   unreadable alone; "3 of 5 — real cited costs, but not yet from people who
   could buy" is a reading. Same for percentiles ("p12 — only the closest 12%
   of pairs counted as the same complaint"), thresholds, and counts.
4. **Verdicts explain themselves against the reader's case.** A gate verdict,
   cap, or demotion is presented as: what happened, why the rule exists, what
   the reader can do next — the same three-part shape the runtime messages
   use. Never present a bare verdict with a section citation.
5. **Absence is explained, not implied.** "No high-intensity clusters" must
   say what would have counted ("no cluster had two kinds of cited cost from
   multiple people plus a buyer") so the reader can judge the miss.
6. **Failure, skip, and zero-result are three different sentences.** "The
   archive was down", "we chose not to search there, because…", and "we
   searched and found nothing" must never collapse into each other — the
   pipeline keeps them apart on disk; keep them apart in prose.
7. **The mechanism stays reachable.** Every translated claim names its source:
   the card path, the cluster id, the quote URL. Plain language that cannot be
   audited back to the file is spin, not explanation.
8. **An identifier never stands alone.** A cell id, cluster id, or run slug
   always travels with its human referent at every rendering — "m01 — HVAC
   company owner", "c06 — AI-hallucinated citations drawing sanctions" — in
   every table row, chart label, and sentence, not just at first mention. The
   bare handle is for grepping the files; the pairing is for reading. A table
   whose first column is bare ids fails this even when the ids are defined
   somewhere else on the page.
9. **Headers and passthrough strings are glossed where they stand.** Every
   table carries a visible column key (one line, above or below it) defining
   what each column means and how to read its scale; every section heading is
   followed by a sentence saying what the section shows. A string kept
   verbatim from a tool — a saturation read, a health detail, a recorded
   reason — stays verbatim, but its recurring jargon gets a standing gloss
   beside the table ("FLOOR ONLY means a lookup source failed, so the true
   count can only be higher"). This applies to your own interpretation prose
   too: a term of art you introduce ("cut", "tightness", "ungrouped") carries
   its parenthetical definition in the same sentence.

## What this skill never does

- Never rewrites a value on disk. `exclude` stays `exclude`, `cut_basis` stays
  `cut_basis` in every JSON file and contract surface.
- Never rounds a judgment into a friendlier one. "Discard" is presented as
  discard, with the why.
- Never summarizes past a disagreement. If the mechanical read and the plain
  reading would leave different impressions, the gap itself is the finding —
  surface it.

## Litmus

Hand the finished presentation to someone who has never opened this repo. If
any sentence sends them looking for a glossary, a section number, or you, the
translation is not done. `tests/validate_user_strings.py` enforces this
mechanically for runtime strings; this skill extends the same standard to
everything composed for a human at presentation time.
