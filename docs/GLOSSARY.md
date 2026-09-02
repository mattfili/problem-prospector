# Glossary

Every term the tool's output uses, defined for someone who has never opened the
source. Cross-references point only at other entries here, never at the code or
the methodology.

## Frame and capture

### matrix
The 6–12 search angles a run fans out across, each combining a persona, a
vertical, and a framing. Fewer than 6 and you only find what you already
suspected; more than 12 and capture takes longer than reading the result.

### cell
One search angle of the [matrix](#matrix): one persona in one vertical with one
framing, plus the queries that will hunt its complaints. Every captured post is
tagged with the cell that found it, so evidence stays traceable to the angle
that produced it.

### cell_id
A cell's short handle, one letter plus two digits (`m01`). It appears on every
evidence record so you can trace a quote back to the search angle that found it.

### persona
The specific person doing the complaining — a job, not a demographic. "311
dispatcher" is a persona; "government workers" is not, because you cannot write
a search query in a vague group's voice.

### vertical
The industry or setting the persona works in ("municipal call center"). It
exists as its own axis so the same persona's pain can be compared across
settings, and the same setting across personas.

### framing
The specific problem angle a cell searches for ("call volume triage without a
CRM"). One persona in one vertical has many problems; the framing picks which
one this cell is listening for.

### complainer vocabulary
The words someone actually uses when venting — "permit system nightmare", not
"municipal workflow inefficiency". Queries written in analyst language find
analyst documents; queries written the way someone vents at 11pm find pain.

### inspiration
The vague hunch the run started from, verbatim ("back-office pain in small
government agencies"). It seeds the matrix and names the [run slug](#run-slug),
and is kept so a rescan months later knows exactly what it is rescanning.

### run slug
The run's directory name: the inspiration, kebab-cased and truncated, plus the
date. Everything a run produces lives under it, so two runs never overwrite
each other and a slug alone tells you what and when.

### thin capture
The stop that fires when capture found under 40 posts or fewer than 3 sources
answered. Below that floor a group of 2 looks the same as a group of 40 in the
report, which is how one loud thread becomes a "market" — the fix is more
search angles or better [complainer vocabulary](#complainer-vocabulary), then
capture again.

### source health
The run's log of what every data source actually did — answered, answered
partially, failed, or was deliberately skipped, and why. It exists so a small
capture reads as "two sources were down", not as "nobody has this problem".

### degradation
A source answering worse than asked — a fallback used, results truncated, a
partial page — recorded in [source health](#source-health) rather than papered
over. A number whose degraded origin is hidden looks more trustworthy than it
is.

## Clustering

### cluster
A group of captured posts that phrase the same complaint differently, treated
from here on as one pain. After clustering, the cluster is the unit of
analysis — never the individual post, whose popularity says more about the
poster than the problem.

### cluster_size
How many captured posts landed in the cluster. It is the raw bulk behind a
[frequency read](#frequency-read) — but bulk alone can be one person reposting,
which is why [distinct_authors](#distinct_authors) and
[distinct_communities](#distinct_communities) sit beside it.

### distinct_authors
How many different people wrote a cluster's posts. It exists because ten posts
from one frustrated person is one data point wearing ten hats; frequency is
demoted when authors are few relative to posts.

### distinct_communities
How many different subreddits (or other sources) a cluster's posts came from.
One community's shared way of talking clusters beautifully and tells you
nothing about the wider world — so a single-community cluster is capped at
medium frequency, however many posts it holds.

### cut_basis
A record of how tightly posts had to resemble each other to land in the same
cluster. `adaptive:p12` means "the closest 12% of all post pairs in this run
counted as the same complaint"; a lower number splits harder. It is printed
because the same evidence produces different-looking clusters at different
settings, and you should see which setting you are reading.

### adaptive cut
Choosing the clustering tightness from this run's own data instead of a fixed
number, because a threshold that works on one corpus mangles another. The
chosen tightness is recorded as the [cut_basis](#cut_basis).

### percentile cut
The dial behind the [adaptive cut](#adaptive-cut): which percentage of closest
post-pairs count as "the same complaint". `--percentile 12` is tighter than
`--percentile 35`; lower the number when clusters look fused, raise it when
everything shatters into singletons.

### fusion advisory
A warning that one cluster's posts are about as unrelated to each other as two
random posts from the whole run — usually a sign two different problems got
merged. Re-run with a lower [percentile cut](#percentile-cut) to split harder;
if it still looks wrong, the frame is too broad.

### unclustered tail
Posts that matched nothing else closely enough to group. One post is a rumour;
two independent phrasings are a pain — the tail is kept on disk and out of the
rankings, so nothing is silently dropped and nothing lonely gets promoted.

### embedding backend
The local model that turns each post into numbers so similarity can be
measured. It runs on your machine specifically so clustering needs no API key
and no network beyond a one-time model download.

## Scoring

### frequency read
How widespread a pain looks: `high`, `medium`, or `low`, derived from
[cluster_size](#cluster_size), [distinct_authors](#distinct_authors),
[distinct_communities](#distinct_communities) and
[engagement_weighted](#engagement_weighted) against thresholds printed with
every run. It is deliberately separate from [intensity](#intensity-score):
many people mildly annoyed is not one person bleeding.

### intensity score
How badly the pain hurts the people who have it: 1–5, earned only by cited
quotes that hit specific [markers](#the-six-markers). Higher levels need cost
markers from multiple different people — volume of complaints alone never
raises it.

### the six markers
The only evidence that counts toward [intensity](#intensity-score):
[money_loss](#money_loss), [time_quantified](#time_quantified),
[workaround_built](#workaround_built), [abandonment](#abandonment),
[profanity_urgency](#profanity_urgency), and
[complainer_is_buyer](#complainer_is_buyer). Each must be backed by a verbatim
quote, or it is false.

### money_loss
Someone states an actual cost in money — "booth fee was $75, I made $120". A
[cost marker](#cost-marker).

### time_quantified
Someone puts a number on time lost — "two staff, ten hours a week". A
[cost marker](#cost-marker).

### workaround_built
Someone built or bought something to route around the problem — a
spreadsheet, a script, a paid tool used off-label. A
[cost marker](#cost-marker), and often the strongest one: effort spent is
willingness to pay in its larval form.

### abandonment
Someone quit — the tool, the workflow, the business line — because of this
pain. A [cost marker](#cost-marker).

### profanity_urgency
The complaint is written in anger. It shows feeling, not cost, so it can
support a low score but never carry a high one — swearing is not an invoice.

### complainer_is_buyer
The person complaining is someone who could actually purchase a fix — they own
the budget or the decision. Without it, intensity stops at 3: pain felt by
people who cannot buy is a symptom, not a market.

### cost marker
Any of [money_loss](#money_loss), [time_quantified](#time_quantified),
[workaround_built](#workaround_built), or [abandonment](#abandonment) — the
markers that show the pain costs something, as opposed to merely being voiced.

### exemplar
The verbatim quote (15 words or fewer) cited as evidence for a marker, with
its URL. The cap exists because a long quote stops being evidence for one
specific marker and starts being a paragraph that mentions it.

### verbatim rule
An [exemplar](#exemplar) must appear word-for-word in the captured post.
Paraphrases and ellipsis-stitched fragments are rejected, because a quote you
cannot find in the source is a claim, not evidence.

### engagement_weighted
A cluster's post count weighted by upvotes and comments, so fifty posts nobody
read do not outrank five posts a community piled onto. Sources that report no
engagement contribute their posts unweighted rather than as zero.

### quadrant
Where a pain lands on the 2×2 of [frequency](#frequency-read) ×
[intensity](#intensity-score). The four cells get four fixed readings — see
[quadrant reads](#quadrant-reads) — because the same evidence means different
things in different corners.

### quadrant reads
The four fixed interpretations: **high-freq/high-intensity** — real and
crowded; expect incumbents, the work is the wedge, not proving the problem.
**low-freq/high-intensity** — possible niche gold; few voices, all bleeding;
demand a real buyer before advancing. **high-freq/low-intensity** — a content
play, not a product; audience/SEO/newsletter, not software someone buys.
**low-freq/low-intensity** — discard; the card is written for auditability, do
not advance.

## Gates and analysis

### inventory gate
The rule that excludes any business where a lost, damaged, or refunded
physical item would be *our* problem — holding stock, but also dropshipping
and print-on-demand, where title passes through us at the moment of sale.
Excluded candidates keep their card and their stated reason; they are never
silently dropped or quietly down-ranked.

### pass / exclude
The [inventory gate](#inventory-gate)'s only two verdicts. `pass` means the
candidate advances to paid analysis; `exclude` means it stops before any
analysis money is spent, with the firing reason recorded first in its
[flags](#flags), prefixed `excluded:`.

### flags
Short warnings attached to a card that inform without re-ranking — "long
procurement cycle", "licensure-adjacent". They exist so a judgment call is
visible and deliberate rather than laundered into a score; two flags never add
up to a verdict.

### wedge
A specific way into a pain — the first product move, not the whole product
vision. One pain generates many candidate wedges; each is kept close to the
complaint evidence and far from what incumbents already say.

### voltage
A wedge's ranking energy: how directly it touches the cited pain and how far
it stands from incumbent positioning, shown as separate distances rather than
one blended score, so you can see *why* a wedge ranked where it did.

### MVP shape
The smallest buildable version of a wedge, graded separately for technical
difficulty and [distribution](#distribution-complexity) difficulty. The grades
are never averaged: a trivial build with impossible distribution is nothing
like the reverse.

### distribution complexity
How hard it is to get the MVP in front of its first ~25 real users, graded 1–5
with the channel named. Kept apart from technical difficulty because "easy to
build, impossible to reach buyers" is the most common way a plausible idea
dies.

### willingness to pay
Evidence that people already spend money or serious effort on this pain —
named paid tools in use, quantified workaround costs, a budget line the fix
could attach to. Inferred from public text only; nobody is surveyed and no
price is being set.

### saturation
How crowded the solution space already looks: a competitor count and trend
direction from a pre-build reality check, passed through in the upstream
tool's own words. It is reported, never folded into a ranking score.

### retro_trend
The pain's reconstructed 3–5 year history: is it long-broken, newly emerging,
or a news spike — and are solutions accumulating against it? Built from
public archives so a hot cluster can be told apart from a hot week.
