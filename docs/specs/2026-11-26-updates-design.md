# Updates from inside the app

Shachar shares CallCoach with friends and does not want to tell each of them when there is something new. So the app
says when GitHub's `main` has moved on and installs it with one click, the same way CallRec does with its releases.

## Check

- A minute after the app opens, then every 24 hours: `git fetch origin main` in the clone the app runs from
  (`root.txt`), then `HEAD` against `origin/main`. Nothing else is sent.
- Only a clone whose `origin` is github.com/shacharPash/callcoach (any spelling of that URL) is checked. A folder with
  no origin, or another one, is left alone, so a working copy is never offered someone else's history.
- Newer and straight ahead: an update. Same commit: nothing. Commits of the clone's own: no update, a line in the log.
- "בדוק עדכונים" in the menu bar menu turns it off (on by default).
- `CALLCOACH_UPDATE_SOURCE` stands a local repository in for GitHub, for tests; `CallCoach --check-update` prints
  what the check finds.

## Notice

- "עדכן את CallCoach…" at the top of the menu bar menu while an update is there; off during a call.
- One notification per new `main` (the commit is remembered); during a call it waits for the call to end. A click on
  it does what the menu item does.

## Click

- Changes of your own in tracked files stop it with a message before anything closes.
- A dialog says the app will close, update and open again, how many changes come in, that it takes a minute or two,
  and that macOS may ask for its two permissions again.
- The app copies `tools/update.sh` to `~/.callcoach/run` (the pull may replace the file in the clone), starts it on its
  own with `nohup`, and quits.
- The script waits for the app to be gone, refuses another branch, changes of your own and commits of your own, does
  `git fetch` and a fast-forward merge, runs `./setup.sh`, and opens the app whatever happened. A failed `setup.sh`
  puts the clone back (`git reset --keep`), so the app and the engine stay one version.
- It writes `~/.callcoach/run/update-result.json`; the app reads it once when it opens and posts one notification:
  updated with how many changes, or not updated and why. The log is `~/.callcoach/logs/update.log`.

## Tests

- `tests/test_update_script.py`: the script against a local bare repository: forward, changes of your own, an
  untracked file, commits of your own, a failed setup, another branch, already up to date.
- The app's self-check group "updates": URL spellings, the decision, one notification per commit, the off switch, the
  result file, the texts.
