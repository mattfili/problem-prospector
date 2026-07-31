---
name: no-inventory-gate
description: "Type-checks a candidate business, wedge, or MVP shape against the plugin's no-inventory constraint before it is promoted, ranked, or written down — at cluster-to-card promotion, wedge generation, MVP shaping, and `/diligence` ingest — and supplies the exact `inventory_gate` verdict and flags to write into `cards/<cluster_id>.json` per CONTRACTS §4. Also applies when the open question is 'does this count as inventory?', 'is dropshipping ok?', or 'what about hardware?'. Do NOT use it to score, rank, or compare opportunities; do NOT use it to gate raw evidence capture (scouts capture everything); and do NOT use it to judge whether a customer's inventory-heavy business is a valid market to sell software into — that is never a reason to exclude."
---

# No-inventory gate

## Why this exists

An inventory business that gets *down-ranked* still lands in the top 5 when the pain signal is strong — and pain signal in physical-goods niches is often screaming. Then the economist, skeptic, historian, wedgesmith, and a full `/diligence` crawl all burn on a business the owner will never build. Ranking is the wrong instrument: it trades off, and there is nothing to trade off here.

**The gate is a type check, not a preference.** It answers "is this the kind of thing that can be built here", not "how good is it". A type error does not get a score, a subscore, or a place in the sort. It gets a verdict and a recorded reason.

Two corollaries you must not forget:
- A **pass is not an endorsement.** It only means the candidate is in-type. Quality lives in the independent panels of CONTRACTS §4.
- **Flags never subtract.** The sort contract in §4 is `intensity.score` desc → `wtp.read` desc → `saturation.competitor_count` asc. `inventory_gate.flags` is not in it and must never be added to it. The owner asked to see heavy services, licensure, and govtech procurement *eyes open*, not filtered out.

---

## The 20-second version

Ask of **the business we would build** (never of the customer's business):

1. Do we ever take **title** to a physical good? → EXCLUDE
2. Do we ever take **possession** of one — store, pick, pack, ship, inspect? → EXCLUDE
3. Does one more unit of revenue oblige us to **buy, make, or move** a physical item? → EXCLUDE
4. Is a **device** part of what the customer buys, receives, installs, or RMAs from us? → EXCLUDE
5. Must we **deploy or maintain equipment in the field** for the thing to work at all? → EXCLUDE
6. None fired → PASS. Then add flags for heavy services / licensure / procurement / hardware dependency and move on.

If you spend more than a minute, you are pattern-matching on the industry instead of running the test. Run the test.

---

## Decisions already taken — do not re-litigate

- Physical goods, held stock, warehousing, fulfillment/shipping, per-unit COGS **on goods**, and hardware-as-product are **excluded at the gate**. Not down-ranked, not "flagged for later", not "interesting if the pain is high enough".
- **Allowed:** software; data products; marketplaces that never hold inventory (pure matching/booking/listing, payments pass-through, no possession and no title transfer of goods); services productized as software; content/community with a software attach.
- **Flagged, never excluded:** heavy services components, licensure requirements, long procurement cycles, regulated-data surface, hardware the customer already owns. These are cost-of-doing-business facts the reader wants surfaced.
- There are exactly **two verdicts**, and they are the CONTRACTS §4 enum spellings: **`"pass"` and `"exclude"`**. Write `"exclude"`, not `"excluded"` — the economist's and the skeptic's preflight both stop on the literal string `"exclude"`, so a card written `"excluded"` sails past them and burns exactly the panel work this gate exists to save. (The prose below says "excluded" as an English word; the field value is `"exclude"`.) "Flag" is not a verdict — a flagged candidate is `pass` with a non-empty `flags` array. Do not invent a third state; a third state is how a soft exclusion sneaks back into the ranking.

---

## Scope: whose inventory?

The single most common misapplication. The gate is about **our** balance sheet, not the customer's.

- Warehouse management SaaS, city fleet asset tracking, restaurant food-cost software, spoilage analytics, 3PL billing reconciliation → **PASS**. The customer owns the pallets. We own a database.
- The gate fires only when *we* would own, hold, move, or owe a physical item.

Corollary: never suppress evidence. A cluster whose pain is entirely about inventory chaos is excellent evidence and often the best software market in the run.

---

## The bright line (marketplaces especially)

> If the goods vanished mid-transaction — lost, damaged, wrong item, buyer wants a refund — **is it our problem?**

If we would eat the loss, handle the return, file the claim, or appear as merchant of record: we are in the inventory business, and the software is a wrapper on it. **EXCLUDE.**

The passthrough rescue clause is conjunctive. A marketplace passes only if **all four** hold:
1. The counterparty holds **title** throughout.
2. The counterparty holds **possession** throughout.
3. The counterparty bears the **per-unit COGS** and the shipping cost.
4. The counterparty owns **returns, damage, and warranty liability**.

We take a fee, commission, or subscription on a transaction we never physically touch. Any one of the four failing → **EXCLUDE**. "Mostly passthrough" is not passthrough.

---

## Decision procedure

Run in order on the candidate as currently written. First trigger wins; stop.

| # | Question | If yes |
|---|---|---|
| G1 | Does our entity take legal **title** to a physical good it later transfers? | EXCLUDE |
| G2 | Do we ever **possess** a physical good — receive, store, pick, pack, ship, inspect, authenticate, refurbish — even without title? | EXCLUDE |
| G3 | Does each incremental unit of revenue oblige us to **buy, make, or move a physical item**? | EXCLUDE |
| G4 | Is a **device** part of what the customer must buy/receive/install/replace/RMA from us or from a partner whose RMA we own? | EXCLUDE |
| G5 | Does the offering require us to **deploy or maintain equipment in the field** to function? | EXCLUDE |
| G6 | Did G1–G5 fire only through a third party? Apply the four-part passthrough clause above. | ALL four hold → continue; else EXCLUDE |
| G7 | None fired. Verdict `pass`. Now enumerate flags: heavy services, licensure, procurement cycle, regulated data, customer-procured hardware dependency, per-unit passthrough COGS compressing margin. | PASS + flags |

Three disciplines that make this reliable:

- **Rule on the wedge as written, not the category.** "Marketplace" is not a verdict; "marketplace that authenticates watches in-house" is. If the candidate text is too vague to run G1–G5, it is too vague to promote — sharpen it first, then rule.
- **Read the evidence, not just the label.** The gate runs on the cluster's `canonical` plus 2–3 items from `exemplar_urls` in `clusters.json` (produced by `uv run scripts/cluster.py`). Canonical strings are compressed and routinely drop the physical part. When the ruling turns on how a named incumbent actually operates — does it take possession? — do not guess: `uv run scripts/crawl.py <url>` and read their own words.
- **Assume the MCPs are absent.** Reddit exemplars normally reopen through the `dialog` MCP; when it 401s or demands OAuth — routine, and the standing condition in Cowork, where MCP servers may not be wired at all — fall back to `uv run scripts/reddit_search.py` for Reddit and `uv run scripts/crawl.py <url>` for everything else. Record the degradation per CONTRACTS cross-cutting rule 5: `{"source": "dialog", "status": "unavailable", "fallback": "reddit_search.py", "detail": "401"}`. The gate itself never needs `trend-pulse` or `idea-reality` (script equivalents: `uv run scripts/trends_cli.py`, `uv run scripts/reality_cli.py`) — a ruling turns on title, possession, and liability, never on saturation or trend data, so never block a verdict waiting on them. An exemplar you could not open is an unread exemplar: rule on what you actually have, or send the candidate back to be sharpened.

---

## Edge cases, rulings, reasoning

| Candidate | Verdict | Reason |
|---|---|---|
| Print-on-demand / dropshipping storefront | **EXCLUDE** | We are merchant of record: title passes through us at sale and we own COGS, chargebacks, and returns even with zero possession (G1, G3). Selling *software to* POD merchants is a different candidate — rewrite it and re-run the gate. |
| SaaS that ships a hardware sensor | **EXCLUDE** | The device is part of what the customer buys, receives, installs, and RMAs (G4). Rescue: same software on hardware the customer already owns or procures itself → PASS, flag `hardware dependency (customer-procured)`. |
| Marketplace that takes possession for QA / authentication | **EXCLUDE** | Possession plus loss-and-damage liability is inventory operations with extra steps; title is irrelevant (G2, bright line). Rescue only if authentication is remote/documentary and the item never moves through us. |
| Kitting or assembly | **EXCLUDE** | Assembly is manufacturing: we hold components as work-in-progress and bear per-unit materials and labor (G1, G2, G3). |
| Digital goods with per-unit licensing cost | **PASS** | Per-unit cost is real but it is not a *good*: no stock, no warehouse, no shrinkage, no reverse logistics. Flag `per-unit license cost caps gross margin` so `diligence.md` unit economics does not assume 90% — see `skills/marketing/pricing/SKILL.md`. |
| Food / perishables with a software layer | **EXCLUDE** if we buy, hold, or deliver the food; **PASS** if we sell software to the operator who does | Perishables are the worst case of the thing being excluded — spoilage is per-unit COGS with a clock. Bright line: who owns the walk-in cooler. |
| Services business reselling physical materials | **EXCLUDE** while materials sit on our invoice at markup | We take title, warranty, and per-job goods COGS (G1, G3). Rescue: client procures materials directly, never on our invoice, we never touch them → PASS, flag `heavy services`. |
| White-label manufacturing with a software front end | **EXCLUDE** | The front end changes distribution, not the substance: MOQs, tooling, and per-unit COGS on a manufactured good remain ours (G1, G3). "White-label" relocates the factory, not the inventory. |
| Equipment rental or leasing | **EXCLUDE** | A depreciating fleet we buy, store, maintain, and repossess is inventory with a subscription wrapper (G1, G2, G5). Rescue: pure software sold to people who own the fleet → PASS. |
| Data product whose input is a sensor network we deploy and maintain | **EXCLUDE** | Field capex, installs, truck rolls, spares (G5) — the data is a byproduct of an infrastructure business. Rescue: license, scrape, or partner for an existing feed where someone else owns the metal → PASS, flag `single-source data dependency`. |
| Warehouse / fleet / inventory management software | **PASS** | The customer's inventory is not our inventory. Agents get this wrong constantly; the gate reads our balance sheet only. Flag procurement or licensure if the buyer is public sector. |

### Two genuine close calls — reason, do not pattern-match

**1. Shipping-label / postage-reselling API.** Every surface word is logistics, and there *is* per-unit COGS. Run it anyway: title — no, the parcel is the shipper's. Possession — no, we never touch it. G3 asks whether we must buy, make, or **move a physical item**; we buy a carrier *service* and the carrier moves the customer's own parcel. Nothing is stocked, nothing spoils, nothing is returned to us. **PASS**, flags `per-unit passthrough COGS compresses margin`, `carrier concentration risk`. The tell that you got it wrong: if the design starts needing a warehouse, a bin, or an RMA desk, G2 has actually fired and the earlier answer was wrong.

**2. Marketplace for 3D-print files, with an obvious "just print it for me" adjacent wedge.** Files only: digital, no title, no possession, no unit movement → **PASS**. The print-and-ship variant trips G1, G2, and G3 simultaneously → **EXCLUDE**, and that exclusion is scoped to *the wedge*, not the cluster. Excluding one wedge is never grounds for killing the pain or the card. This is why the gate re-runs at wedge and shape granularity, not once per cluster.

---

## Where the gate fires

| Checkpoint | What to do |
|---|---|
| `inputs.json` `matrix` generation | Do not spend cells on verticals where the only buildable business is goods-based. This is a budget decision, not a verdict — do not write `inventory_gate` here. |
| Evidence capture (scouts) | **Never gate.** Scouts capture only, never interpret. Filtering here starves clustering and hides the market. |
| Cluster → card promotion (distiller) | Primary gate. Write `inventory_gate` into `runs/<slug>/cards/<cluster_id>.json`. `excluded` stops panel work immediately — no economist, skeptic, historian, or crawl spend. |
| Wedge generation (wedgesmith) | Re-run per wedge. A passing pain can be wedged into an inventory business; that is a normal failure, caught here. |
| MVP shaping | Re-run per shape. Shapes drift physical — "we'd send them a starter kit" is an exclusion. |
| `/diligence` ingest | Re-run once on the ingested shape before crawling. Cheap check, expensive crawl. |

---

## Output obligation

Every agent that touches a candidate writes CONTRACTS §4 `inventory_gate` — both fields, always, no omission and no empty object:

```json
"inventory_gate": {"verdict": "pass", "flags": ["long procurement cycle", "licensure-adjacent"]}
```

- `verdict` — `"pass"` or `"exclude"` (CONTRACTS §4 enum). Nothing else, and never `"excluded"`.
- `flags` — array of short strings; `[]` when clean. Flags are informational and never affect ordering.
- On `"exclude"`, the **first** element of `flags` is the reason and begins with the literal prefix `excluded:` — the trigger that fired plus one line of why. (Yes, the verdict is `exclude` and the flag prefix is `excluded:`; both spellings are load-bearing and neither is a typo.) Example:

```json
"inventory_gate": {"verdict": "exclude", "flags": ["excluded: G4 — offering ships a LoRa sensor the customer installs and RMAs"]}
```

Recording rules:
- An excluded cluster **still gets its `cards/<cluster_id>.json`**, carrying `cluster_id`, `canonical_pain`, `provenance`, and `inventory_gate`. Remaining panels stay unfilled. The card is not ranked and not wedged; it exists so the run shows what it rejected.
- `runs/<slug>/opportunity-cards.md` lists excluded candidates in a separate section, below the ranked cards, each with its `excluded:` reason verbatim.
- A wedge that fails the gate is simply **not written** to `wedges/<cluster_id>.json`; append a flag to the card instead: `"wedge-dropped: <short thesis> — G2 possession"`. Same rule for a dropped shape in `shapes/<cluster_id>.json`.
- The **verdict** never touches `source_health.json` — the gate is a judgment, not a source failure. A fetch that failed while re-reading an exemplar *is* a source failure and is recorded there per CONTRACTS cross-cutting rule 5.

---

## Failure modes

- **Silent exclusion.** A candidate that vanishes with no card and no reason is a bug. The reader must be able to see the gate fired and disagree with the ruling. Unreviewable filtering is the same disease as an opaque composite score, pointed the other way.
- **Down-ranking instead of excluding.** "It's inventory-ish, so I'll drop its intensity to 2." Never. Panel scores describe the pain, not our appetite; corrupting them destroys the audit trail and the business still surfaces.
- **Flags used as a soft exclusion.** Two flags is not a verdict. Govtech procurement is flagged precisely so it can be chosen deliberately.
- **Excluding the customer's inventory.** Killing warehouse or fleet software because pallets appear in the text. Re-read the scope section.
- **Gating the category instead of the candidate.** "Marketplaces are fine" / "hardware is out" applied to a label that hides the operative fact. Rule on the sentence you would actually build from.
- **Rescue-clause optimism.** "The supplier probably handles returns." Unverified means not established: absent evidence that all four passthrough conditions hold, the answer is EXCLUDE. Say so plainly rather than assuming the convenient arrangement.
- **Filling an unknown to finish the ruling.** Inventing a supplier's return policy, a fulfillment fee, a per-unit price, or a plausible-looking URL so the verdict has something to stand on. CONTRACTS cross-cutting rule 1 holds here too: what a source did not return is `[unknown]` or `null`. A gate ruling never needs an estimated number — it needs title, possession, and liability, and each of those is either evidenced or openly unresolved.
- **Ruling from the canonical string alone.** Compression drops the physical half of a pain. Open two exemplars.
- **Late gating.** Running the gate after the economist and skeptic have worked the card. The whole point is to spend nothing on out-of-type candidates; gate before panels, not after.
