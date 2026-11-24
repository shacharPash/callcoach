#!/bin/bash
# Fails unless every author and committer email in the history is the owner's noreply address and no
# word from ~/.callcoach/forbidden-words.txt (one literal word per line, # starts a comment) appears in
# the files that would be committed (tracked and untracked, so plugin/ and .claude-plugin/ are covered), in a file name or path in the history, in any historical revision,
# or in a commit message.
set -euo pipefail
cd "$(dirname "$0")/.."
ALLOWED="93581407+shacharPash@users.noreply.github.com"
WORDS="${CALLCOACH_FORBIDDEN:-$HOME/.callcoach/forbidden-words.txt}"

fail() { echo "$1" >&2; exit 1; }

bad="$(git log --all --format='%ae%n%ce' | sort -u | grep -v -x -F "$ALLOWED" || true)"
[ -z "$bad" ] || fail "Foreign identity in history: $bad"

[ -f "$WORDS" ] || fail "Missing $WORDS (one forbidden word per line); create it, then run this again"

revs="$(git rev-list --all)"
while IFS= read -r w || [ -n "$w" ]; do
  w="${w%$'\r'}"
  w="$(printf '%s' "$w" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  case "$w" in ''|'#'*) continue ;; esac
  hits="$(git ls-files -co --exclude-standard -z | xargs -0 grep -i -I -F -l -- "$w" 2>/dev/null || true)"
  [ -z "$hits" ] || fail "Forbidden word in the working tree: $w ($hits)"
  names="$(git ls-files -co --exclude-standard)"
  if printf '%s\n' "$names" | grep -i -F -q -- "$w"; then fail "Forbidden word in a file name: $w"; fi
  [ -n "$revs" ] || continue
  rc=0
  git grep -i -I -q -F -- "$w" $revs || rc=$?
  case "$rc" in
    0) fail "Forbidden word in history: $w" ;;
    1) ;;
    *) fail "History scan failed (git grep exit $rc)" ;;
  esac
  paths="$(git log --all --name-only --format=)"
  if printf '%s\n' "$paths" | grep -i -F -q -- "$w"; then fail "Forbidden word in a path in history: $w"; fi
  msgs="$(git log --all -i -F --grep="$w" --format=%h)"
  [ -z "$msgs" ] || fail "Forbidden word in a commit message: $w"
done < "$WORDS"
echo "Private check passed"
