#!/bin/bash
# The update behind "עדכן את CallCoach…" in the menu bar menu. The app copies this file out of the clone, starts it on
# its own and quits. It waits until the app is gone, moves the clone to GitHub's main (only straight forward, and never
# over changes of your own), runs ./setup.sh again and opens the app, which then says how it went. When setup.sh fails,
# the clone goes back to where it was and setup.sh runs once more for that version, so the app, the engine and its
# Python packages stay one version. It writes RESULT.started first, so the app knows it is running before it quits.
# Usage: update.sh ROOT APP PID RESULT
set -u
trap '' HUP
root="$1" app="$2" pid="$3" result="$4"
mkdir -p "$(dirname "$result")" && : > "$result.started"
setup="${CALLCOACH_UPDATE_SETUP:-./setup.sh}"
opener="${CALLCOACH_UPDATE_OPEN:-open}"
export GIT_TERMINAL_PROMPT=0
export PATH="${CALLCOACH_TOOLS:-$HOME/.callcoach}/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

finish() {  # finish OK STEP FROM TO
  mkdir -p "$(dirname "$result")"
  printf '{"ok": %s, "step": "%s", "from": "%s", "to": "%s"}\n' "$1" "$2" "$3" "$4" > "$result.tmp"
  mv -f "$result.tmp" "$result"
  printf '%s update %s: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$2" "$3..$4"
  $opener "$app" >/dev/null 2>&1 || true
  exit 0
}

for _ in $(seq 1 240); do
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.5
done
kill -0 "$pid" 2>/dev/null && finish false running "" ""
cd "$root" 2>/dev/null || finish false folder "" ""
from="$(git rev-parse HEAD 2>/dev/null)" || finish false folder "" ""
[ "$(git symbolic-ref --quiet --short HEAD)" = "main" ] || finish false branch "$from" "$from"
[ -z "$(git status --porcelain --untracked-files=no)" ] || finish false changes "$from" "$from"
git fetch --quiet origin main || finish false fetch "$from" "$from"
[ "$(git rev-parse FETCH_HEAD)" = "$from" ] && finish true done "$from" "$from"
git merge-base --is-ancestor HEAD FETCH_HEAD || finish false diverged "$from" "$from"
git merge --ff-only --quiet FETCH_HEAD || finish false pull "$from" "$from"
to="$(git rev-parse HEAD)"
if ! $setup; then
  git reset --quiet --keep "$from" && $setup && finish false setup "$from" "$to"
  finish false restore "$from" "$to"
fi
finish true done "$from" "$to"
