#!/usr/bin/env bash
# Refresh the vendored copy of coreyhaines31/marketingskills (MIT).
#
# Shows a diff first and requires confirmation, so upstream changes never land
# silently in a run. Records the synced commit in skills/marketing/.upstream-ref
# so the vendored tree's provenance is always known.
#
#   scripts/sync-marketingskills.sh            # diff, then prompt to apply
#   scripts/sync-marketingskills.sh --check    # diff only, exit 1 if drifted
#   scripts/sync-marketingskills.sh --yes      # apply without prompting

set -euo pipefail

UPSTREAM="https://github.com/coreyhaines31/marketingskills.git"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$REPO_ROOT/skills/marketing"
REF_FILE="$DEST/.upstream-ref"

MODE="prompt"
for arg in "$@"; do
  case "$arg" in
    --check) MODE="check" ;;
    --yes|-y) MODE="yes" ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

WORK="$(mktemp -d)"
# Clean up the clone on any exit path, including failure.
trap 'rm -rf "$WORK"' EXIT

echo "==> fetching $UPSTREAM"
git clone --quiet --depth=1 "$UPSTREAM" "$WORK/upstream"
NEW_REF="$(cd "$WORK/upstream" && git rev-parse HEAD)"
OLD_REF="$(cat "$REF_FILE" 2>/dev/null || echo "none")"

echo "==> vendored: $OLD_REF"
echo "==> upstream: $NEW_REF"

if [ "$OLD_REF" = "$NEW_REF" ]; then
  echo "==> already in sync; nothing to do."
  exit 0
fi

# Compare only the skills tree — that is all this repo vendors.
echo "==> diff (vendored -> upstream)"
DIFF_OUT="$WORK/diff.txt"
# diff exits 1 when files differ, which is the expected case here.
diff -ruN --exclude=".upstream-ref" --exclude="LICENSE.upstream" \
  "$DEST" "$WORK/upstream/skills" > "$DIFF_OUT" 2>&1 || true

if [ ! -s "$DIFF_OUT" ]; then
  echo "==> content identical despite differing refs; recording new ref only."
  echo "$NEW_REF" > "$REF_FILE"
  exit 0
fi

# Summarize rather than dumping thousands of lines.
echo "--- changed files ---"
grep -E '^(diff|Only in)' "$DIFF_OUT" | sed 's/^/  /' || true
echo "--- $(wc -l < "$DIFF_OUT" | tr -d ' ') diff lines total; full diff at $DIFF_OUT ---"

if [ "$MODE" = "check" ]; then
  echo "==> drift detected (--check): not applying."
  # Copy the diff somewhere durable since $WORK is about to be removed.
  cp "$DIFF_OUT" "$REPO_ROOT/marketingskills-drift.diff"
  echo "==> saved to marketingskills-drift.diff"
  exit 1
fi

if [ "$MODE" = "prompt" ]; then
  printf "==> apply upstream over skills/marketing/? [y/N] "
  read -r reply
  case "$reply" in
    y|Y|yes|YES) ;;
    *) echo "==> aborted."; exit 0 ;;
  esac
fi

echo "==> applying"
# Preserve our provenance files, replace the skill tree wholesale so upstream
# deletions propagate instead of leaving orphaned skills behind.
cp "$REF_FILE" "$WORK/keep-ref" 2>/dev/null || true
rm -rf "$DEST"
mkdir -p "$DEST"
cp -R "$WORK/upstream/skills/." "$DEST/"
cp "$WORK/upstream/LICENSE" "$DEST/LICENSE.upstream"
echo "$NEW_REF" > "$REF_FILE"

echo "==> synced to $NEW_REF"
echo "==> review with: git diff --stat skills/marketing"
