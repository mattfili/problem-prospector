#!/usr/bin/env bash
# Smoke tests: every script runs standalone, key-free, end to end.
#
# The point of this harness is to make the plugin's two hard guarantees
# *testable* rather than aspirational:
#
#   1. KEY-FREE — scripts are run under a scrubbed environment with decoy
#      credentials present. A script that reads a credential would either
#      change behavior or fail; either way we catch it here. This is why the
#      decoys are set rather than merely unset.
#   2. PARSEABLE — stdout must be valid JSON and nothing else, because agents
#      parse it directly with no wrapper. A stray print() breaks the pipeline
#      in a way that is very hard to debug from inside an agent.
#
# Network tests hit real public endpoints, so a failure here can mean "upstream
# is down" rather than "the script is broken". Failures name which it is.
#
#   tests/smoke.sh              # offline checks + network checks
#   tests/smoke.sh --offline    # skip anything that needs the network
#   tests/smoke.sh --quick      # offline + one representative network check

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MODE="full"
for arg in "$@"; do
  case "$arg" in
    --offline) MODE="offline" ;;
    --quick) MODE="quick" ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

PASS=0; FAIL=0; SKIP=0
FAILED_NAMES=()

green() { printf '\033[32m%s\033[0m' "$1"; }
red()   { printf '\033[31m%s\033[0m' "$1"; }
dim()   { printf '\033[2m%s\033[0m' "$1"; }

ok()   { green "  PASS"; echo " $1"; PASS=$((PASS+1)); }
bad()  { red   "  FAIL"; echo " $1"; if [ -n "${2:-}" ]; then echo "        $2"; fi; FAIL=$((FAIL+1)); FAILED_NAMES+=("$1"); }
skip() { dim   "  SKIP"; echo " $1"; SKIP=$((SKIP+1)); }

section() { echo; echo "== $1"; }

# Decoy credentials. If any script reads one of these, it is not key-free.
# We deliberately set them to obviously-invalid values: a script that picks one
# up and sends it will get a 401 and fail loudly rather than silently working.
export OPENAI_API_KEY="decoy-must-not-be-used"
export GITHUB_TOKEN="decoy-must-not-be-used"
export ANTHROPIC_API_KEY="decoy-must-not-be-used"
export REDDIT_CLIENT_ID="decoy-must-not-be-used"
export REDDIT_CLIENT_SECRET="decoy-must-not-be-used"
export CHROMA_PROXY_API_KEY="decoy-must-not-be-used"

SCRIPTS=(
  cluster.py
  reddit_search.py
  hn_history.py
  reddit_history.py
  gtrends_history.py
  gh_history.py
  crawl.py
  trends_cli.py
  reality_cli.py
)

# ---------------------------------------------------------------------------
section "presence"
# ---------------------------------------------------------------------------
for s in "${SCRIPTS[@]}"; do
  if [ -f "scripts/$s" ]; then ok "scripts/$s exists"; else bad "scripts/$s exists" "file not found"; fi
done
for f in plugin.json .mcp.json .claude-plugin/marketplace.json docs/CONTRACTS.md README.md LICENSE ATTRIBUTION.md; do
  if [ -f "$f" ]; then ok "$f exists"; else bad "$f exists" "file not found"; fi
done

# ---------------------------------------------------------------------------
section "manifests are valid JSON"
# ---------------------------------------------------------------------------
for f in plugin.json .mcp.json .claude-plugin/marketplace.json; do
  if [ ! -f "$f" ]; then skip "$f parses"; continue; fi
  if python3 -m json.tool "$f" > /dev/null 2>&1; then ok "$f parses"; else bad "$f parses" "invalid JSON"; fi
done

# ---------------------------------------------------------------------------
section "skills have valid frontmatter"
# ---------------------------------------------------------------------------
for d in skills/*/; do
  n="$(basename "$d")"
  [ "$n" = "marketing" ] && continue
  f="$d/SKILL.md"
  if [ ! -f "$f" ]; then bad "skills/$n has SKILL.md" "missing"; continue; fi
  # Frontmatter must open on line 1, declare a name matching the directory,
  # and carry a description — the description is what drives skill selection,
  # so an empty one means the skill silently never loads.
  if [ "$(head -1 "$f")" != "---" ]; then
    bad "skills/$n frontmatter" "does not start with ---"; continue
  fi
  fm_name="$(awk '/^---$/{c++; next} c==1 && /^name:/{print $2; exit}' "$f")"
  if [ "$fm_name" != "$n" ]; then
    bad "skills/$n frontmatter name" "name: '$fm_name' != dir '$n'"; continue
  fi
  if ! awk '/^---$/{c++; next} c==1 && /^description:/{found=1} END{exit !found}' "$f"; then
    bad "skills/$n frontmatter description" "missing description:"; continue
  fi
  ok "skills/$n frontmatter"
done

# ---------------------------------------------------------------------------
section "agents have valid frontmatter"
# ---------------------------------------------------------------------------
# `name` must match the filename or delegation by name fails. `tools` must be
# present so the agent is not silently granted everything.
for f in agents/*.md; do
  [ -f "$f" ] || { skip "agents/* frontmatter"; break; }
  n="$(basename "$f" .md)"
  if [ "$(head -1 "$f")" != "---" ]; then bad "agents/$n frontmatter" "no leading ---"; continue; fi
  fm_name="$(awk '/^---$/{c++; next} c==1 && /^name:/{print $2; exit}' "$f")"
  if [ "$fm_name" != "$n" ]; then bad "agents/$n frontmatter name" "'$fm_name' != '$n'"; continue; fi
  if ! awk '/^---$/{c++; next} c==1 && /^description:/{f=1} END{exit !f}' "$f"; then
    bad "agents/$n frontmatter" "missing description:"; continue; fi
  if ! awk '/^---$/{c++; next} c==1 && /^tools:/{f=1} END{exit !f}' "$f"; then
    bad "agents/$n frontmatter" "missing tools:"; continue; fi
  ok "agents/$n frontmatter"
done

# ---------------------------------------------------------------------------
section "commands have valid frontmatter"
# ---------------------------------------------------------------------------
# Commands orchestrate subagents, so allowed-tools MUST include Task —
# without it every delegation in the command silently fails.
for f in commands/*.md; do
  [ -f "$f" ] || { skip "commands/* frontmatter"; break; }
  n="$(basename "$f" .md)"
  if [ "$(head -1 "$f")" != "---" ]; then bad "commands/$n frontmatter" "no leading ---"; continue; fi
  if ! awk '/^---$/{c++; next} c==1 && /^description:/{f=1} END{exit !f}' "$f"; then
    bad "commands/$n frontmatter" "missing description:"; continue; fi
  if ! awk '/^---$/{c++; next} c==1 && /^allowed-tools:.*Task/{f=1} END{exit !f}' "$f"; then
    bad "commands/$n frontmatter" "allowed-tools missing Task (delegation would fail)"; continue; fi
  ok "commands/$n frontmatter"
done

# ---------------------------------------------------------------------------
section "no invented marketing-skill references"
# ---------------------------------------------------------------------------
# Agents and commands cite vendored skills by name; a name that does not exist
# means that step silently consults nothing.
if [ -d skills/marketing ]; then
  # Skip lines that explicitly call out a name as absent — several skills
  # deliberately document "skills/marketing/X does not exist, use Y instead",
  # and flagging those would punish exactly the diligence we want.
  missing_refs=""
  for ref in $(grep -rhoE 'skills/marketing/[a-z0-9-]+' agents commands skills --include='*.md' 2>/dev/null \
               | sed 's|skills/marketing/||' | sort -u); do
    [ -d "skills/marketing/$ref" ] && continue
    # A bare mention is a defect only if some citation is NOT a negation.
    real_cite="$(grep -rhE "skills/marketing/$ref([^a-z0-9-]|\$)" agents commands skills --include='*.md' 2>/dev/null \
                 | grep -viE 'does not exist|no such|not a real|is absent|does not ship' || true)"
    [ -z "$real_cite" ] || missing_refs="$missing_refs $ref"
  done
  if [ -z "$missing_refs" ]; then ok "marketing-skill references all resolve"
  else bad "marketing-skill references all resolve" "missing:$missing_refs"; fi
else
  skip "marketing-skill references"
fi

# ---------------------------------------------------------------------------
section "contract enum consistency"
# ---------------------------------------------------------------------------
# Enum drift actually happened during this build (three agents agreed on
# "excluded" while CONTRACTS said "exclude"), and it is invisible until a
# conformant consumer chokes. This check is the permanent guard.
if python3 tests/validate_enums.py; then ok "no CONTRACTS enum drift"
else bad "no CONTRACTS enum drift" "see output above"; fi

# ---------------------------------------------------------------------------
section "credential audit (static)"
# ---------------------------------------------------------------------------
# A read of any credential-shaped env var is a hard failure. Comments are
# stripped first so that a script *explaining* that it does not read tokens
# does not trip its own audit.
for s in "${SCRIPTS[@]}"; do
  f="scripts/$s"
  if [ ! -f "$f" ]; then skip "$s credential-free"; continue; fi
  hits="$(sed 's/#.*//' "$f" \
    | grep -nEi '(os\.environ|getenv)[^)]*(KEY|TOKEN|SECRET|PASSWORD|CLIENT_ID|CREDENTIAL)' \
    || true)"
  if [ -z "$hits" ]; then ok "$s credential-free"; else bad "$s credential-free" "$hits"; fi
done

# ---------------------------------------------------------------------------
section "--help works"
# ---------------------------------------------------------------------------
for s in "${SCRIPTS[@]}"; do
  f="scripts/$s"
  if [ ! -f "$f" ]; then skip "$s --help"; continue; fi
  if out="$(uv run --quiet "$f" --help 2>&1)"; then
    if echo "$out" | grep -qi "usage"; then ok "$s --help"; else bad "$s --help" "no usage line"; fi
  else
    bad "$s --help" "$(echo "$out" | tail -3 | tr '\n' ' ')"
  fi
done

# ---------------------------------------------------------------------------
section "offline: clustering on the fixture"
# ---------------------------------------------------------------------------
# The lexical backend needs no model download and no network, so this proves
# the clustering path works on a cold machine. The fastembed path is exercised
# in the network section.
FIXTURE="tests/fixtures/evidence-sample.jsonl"
if [ ! -f "scripts/cluster.py" ] || [ ! -f "$FIXTURE" ]; then
  skip "cluster.py offline backend"
else
  if out="$(uv run --quiet scripts/cluster.py "$FIXTURE" --backend offline 2>/dev/null)"; then
    if echo "$out" | python3 tests/validate_clusters.py; then
      ok "cluster.py offline backend"
    else
      bad "cluster.py offline backend" "output failed contract check"
    fi
  else
    bad "cluster.py offline backend" "exited non-zero"
  fi
fi

if [ "$MODE" = "offline" ]; then
  section "summary"
  echo "  passed $PASS · failed $FAIL · skipped $SKIP  (offline mode)"
  [ "$FAIL" -eq 0 ] || { echo; echo "failed:"; printf '  - %s\n' "${FAILED_NAMES[@]}"; }
  exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)
fi

# ---------------------------------------------------------------------------
section "network: stdout is valid JSON"
# ---------------------------------------------------------------------------
# Each entry is "script|args". These are small, real calls against public
# endpoints. Keep them cheap: gh_history paces at ~6.5s/request by design.
NET_TESTS=(
  "hn_history.py|--query 'permit software' --years 2 --bucket year"
  # --out is required here: reddit_search.py documents that WITHOUT --out stdout
  # carries the evidence JSONL (many objects) and the summary goes to stderr, so
  # the single-object check below would fail on a working script. --out /dev/null
  # puts the summary on stdout and leaves no artifact behind.
  "reddit_search.py|--subreddits sysadmin --limit 5 --out /dev/null"
  "reddit_history.py|--query permit --subreddits sysadmin --years 2"
  "gtrends_history.py|--query 'permit software'"
  "gh_history.py|--terms 'permit software' --years 2"
  "crawl.py|--url https://plausible.io/#pricing"
  "trends_cli.py|--list-sources"
  "reality_cli.py|--idea 'permit status tracking for small cities'"
)

if [ "$MODE" = "quick" ]; then
  NET_TESTS=("hn_history.py|--query 'permit software' --years 2 --bucket year")
fi

for entry in "${NET_TESTS[@]}"; do
  s="${entry%%|*}"; a="${entry#*|}"
  f="scripts/$s"
  if [ ! -f "$f" ]; then skip "$s network"; continue; fi
  # eval so the quoted multi-word args in NET_TESTS are split correctly.
  out="$(eval uv run --quiet "$f" $a 2>/dev/null)"; rc=$?
  # A non-zero exit is NOT automatically a defect. These scripts exit 1 when a
  # live source gathered nothing usable — a throttled Google Trends, an API
  # that rejected the query. What matters is that the script still emitted
  # clean JSON and said so in source_health rather than fabricating zeros.
  # Conflating "upstream degraded" with "script broken" is exactly the
  # distinction this harness exists to make.
  if echo "$out" | python3 tests/validate_stdout.py; then
    if [ "$rc" -eq 0 ]; then
      ok "$s network -> valid JSON + source_health"
    else
      ok "$s network -> degraded but honest (exit $rc, source_health reported)"
    fi
  else
    if [ -z "$out" ]; then
      bad "$s network" "no stdout at all (exit $rc) — script crashed"
    else
      bad "$s network" "stdout not clean JSON or source_health absent (exit $rc)"
    fi
  fi
done

# ---------------------------------------------------------------------------
section "network: fastembed clustering"
# ---------------------------------------------------------------------------
# Downloads bge-small-en-v1.5 (~130MB) on first run, then cached.
if [ ! -f "scripts/cluster.py" ] || [ ! -f "$FIXTURE" ]; then
  skip "cluster.py fastembed backend"
else
  if out="$(uv run --quiet scripts/cluster.py "$FIXTURE" --backend fastembed 2>/dev/null)"; then
    if echo "$out" | python3 tests/validate_clusters.py; then
      ok "cluster.py fastembed backend"
    else
      bad "cluster.py fastembed backend" "output failed contract check"
    fi
  else
    bad "cluster.py fastembed backend" "exited non-zero"
  fi
fi

# ---------------------------------------------------------------------------
section "summary"
# ---------------------------------------------------------------------------
echo "  passed $PASS · failed $FAIL · skipped $SKIP"
if [ "$FAIL" -ne 0 ]; then
  echo; echo "failed:"; printf '  - %s\n' "${FAILED_NAMES[@]}"
fi
exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)
