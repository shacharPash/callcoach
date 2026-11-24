# One way to start, one brief file, and a quieter panel

## Goal

Starting a call is one button. Preparing is optional and comes in three forms that all end in the same brief file:
nothing at all, "הכן תדריך" in the app (text, files and links that CallCoach's own model reads, plus the web), or a
chat agent from the plugin in this repo (for sources like Drive or Notion). The panel shows less chrome and gives
the terms more room, and a click on a term explains it in a few sentences. Watching keeps working as it is.

## The brief file

A brief is a Markdown file in `<data>/briefs/`, named `<YYYY-MM-DD>-<slug>.md` (the date of `when`, else today; the
slug from the title as call folders slug a name; `-2`, `-3` when the name is taken). It starts with a header:

```
---
title: שיחת היכרות עם Acme
when: 2026-11-17 14:00
type: sales
lang: en
length: 30
goals:
  - להבין איך הם מנהלים היום הרשאות
  - לקבוע פגישת המשך
questions:
  - מה גרם לכם לחפש פתרון עכשיו?
glossary: Acme, Okta, SOC 2
---
## מי בשיחה
## רקע
## לא לשכוח
```

- `when` is local time, `YYYY-MM-DD HH:MM`. `type` is an existing call type, else `general`. `lang` is `he` or `en`,
  else `auto`. `length` is minutes. `goals` keeps the first 5, `questions` the first 3. `glossary` is comma separated.
  Every key is optional and unknown keys are ignored.
- The body has three Hebrew sections: "מי בשיחה" (people, roles, what we know of each), "רקע" (the company, the
  product, what led to the call) and "לא לשכוח" (what he wants to get to or avoid). The body is context for the
  live model, as briefs are today.
- A file with no header still works: it is plain context, its title is its first `#` heading or its file name, and
  it has no `when`.
- A call that uses a brief writes `"brief": "<file name>"` into its `meta.json`. That brief is used.

### Which brief is next

- A brief is a candidate when no call used it and its `when` is between 60 minutes ago and 7 days ahead. A brief with
  no `when` is a candidate for 12 hours after its last change.
- The next brief is the candidate with the nearest `when` (one with no `when` counts as now).
- A brief is due when now is between 30 minutes before its `when` and 60 minutes after it. A brief with no `when` is
  due for its whole 12 hours.

## Three ways to prepare

1. **Nothing.** Start, and the call works from what it hears, as today. Started in the middle of a call, it works
   from that moment on; what was said before is not heard. "הקשר" in the panel takes one line at any time.
2. **"הכן תדריך…" in the app.** A sheet with: "על מה השיחה?" (text, several lines), "מתי" (optional date and time),
   a drop area "גרור לכאן קבצים" (and a "בחר קבצים…" button), and "קישורים" (one per line). "הכן תדריך" closes the
   sheet and the brief is written in the background (see "The brief command"). The "השיחה הבאה" card shows
   "מכין תדריך…" while it runs, then the new brief, or the error with "נסה שוב". The app reads the files itself:
   `md` and `txt` as text, PDF with PDFKit, `doc`, `docx`, `rtf`, `rtfd`, `odt` and `html` with
   `/usr/bin/textutil -convert txt -stdout`. Any other file shows "לא נתמך" in the sheet and is left out. Each file
   is cut at 100,000 characters.
3. **The prep agent** (the plugin in this repo, see "The plugin"): in a Claude Code chat, "תכין אותי לשיחה עם…". It
   asks a few questions, reads his sources and the web, and writes the same brief file.

## The main window

The top of the main window is the start area, in this order:

- **"השיחה הבאה"** card, only when there is a next brief: its title, when (today's time, "מחר 14:00", or the date),
  type and language, "פתח" (opens the file in the default editor) and, when there are other candidates, a menu to
  choose another one. A brief that is not due shows "התחל עם התדריך".
- **"על מה השיחה? (לא חובה)"**, one line. Its text is the call's context and, with no brief, the call's title.
- **One start button.** With a due brief it reads "התחל: <title>" and uses it, with a smaller "שיחה בלי תדריך"
  beside it. Otherwise it reads "התחל שיחה". ⌃⌥S does what the button does.
- **"צפייה"** (⌃⌥W) starts watching, with the field's text as its context.
- **"הכן תדריך…"** (⌃⌥C) opens the sheet.
- While the models load after a start, the button reads "טוען…" with a small progress indicator, and the call
  begins when they are ready.
- Account and microphone lines show only when something is wrong (no account, no access, no microphone).

There is no call type, language, level or length to choose before a call: they come from the brief, else `general`,
`auto`, the level in Settings, and no length. The prep window goes away.

## The panel

- **Status line:** the red dot and the time, "אני N%" (not in watch), "הקשר", "עצור", and "…". The agent dot shows
  only when something is wrong. "עצור" asks for a second click: the first turns it into "לעצור?" for 3 seconds.
- **"…" menu:** "שפה" (אוטומטית, עברית, אנגלית; with a mark on the current one), the nudge level, "מסך מלא ⌃⌥F",
  "הצג תמלול", "שאלה עכשיו ⌃⌥Q", "סמן רגע ⌃⌥B", "הסתר ⌃⌥H". The bottom row of buttons goes away.
- **Goals** fold into one line, "מטרות 1/3", which opens the list on a click.
- **Opening questions** from the brief show in the questions area at the start, until live questions replace them.
- **Terms are bigger:** the term line is 15 points (was 13) at scale 1, and a closed row shows up to two lines.
- **A click explains a term.** A click on a term the model explained opens it in place, like a known name: under it
  "…" and then two or three Hebrew sentences on what it is and why it came up in this call. A second click closes it,
  one row is open at a time, and opening it again shows the same text at once. If it fails the row reads
  "לא הצלחתי להרחיב" and a click tries again. A known name opens as it does today (its whole summary and its שלנו
  lines), with no model request.

## After the call

- The call page has three tabs: "סיכום", "תמלול" and "שמות חדשים (n)" (the last only when there are new names).
  A name with an explanation shows it under its note, and "הוסף לקובץ הידע" writes the explanation as its `תקציר`
  (the note when there is none).
- The call's title is the brief's title, else the field's text (its first 60 characters), else today's default.
- When the summary is ready and the main window is not open, a notification "הסיכום מוכן" with the call's title opens
  that call's page.

## Menu bar and Dock

- Settings has "תמיד ב-Dock" (off by default). On, CallCoach has a Dock icon, and a click on it opens the main window.
- ⌃⌥O opens the main window, so it is reachable when the menu bar has no room for the icon.
- The menu bar menu follows the start area: start (with the due brief), "צפייה", "הכן תדריך…", open the window,
  Settings, quit.
- Deleting an account asks for confirmation first.

## The engine and the protocol

The protocol becomes version 3 (engine and app together).

- **`start`** gains `title` (string, may be empty) and `questions` (list), and `brief` (one file name in
  `<data>/briefs/`, may be empty) takes the place of the `briefs` list. The app reads the brief's header and fills `call_type`, `lang`, `length_min`, `goals`, `questions`
  and `glossary` from it; the engine reads the body as it reads briefs today. `meta.json` gets `title` (when not
  empty) and `brief` (when not empty).
- At start, `questions` are sent at once as a `questions` event (no trigger lines).
- When `goals` is empty, the call is not a watch, and there is text or a brief to work from, the engine asks the
  model for goals in the background and sends a `goals` event; the call does not wait for it. Otherwise the call
  type's goals are used, as today.
- **`prepare`** and `prep_result` leave the protocol.
- **`set_language`** takes `auto` as well, which unlocks the language and lets detection decide again.
- **`explain_term`** (`id`): the engine asks the live model for two or three Hebrew sentences on that term (its
  name, kind and note, and the last 3 minutes of the transcript), at most 60 words, and sends **`term_detail`**
  (`id`, `ok`, `text`). It runs beside the live step, never blocks it, keeps one request per term, answers a repeat
  from what it has, and counts the cost like any request. `terms.json` gets `detail` for each term that has one.
- **The brief command:** `callcoach brief` reads one JSON request on stdin,
  `{"title"?, "when"?, "text", "files": [{"name", "text"}], "links": [url]}`, and writes one JSON line on stdout,
  `{"ok": true, "path": ...}` or `{"ok": false, "message": ...}`. It runs no audio and uses the strong model with web
  search and web fetch only (through the account in use, like the summary). The model writes the header and the
  body in the format above, the engine checks the header (unknown type becomes `general`, a bad `when` is dropped,
  lists are cut) and writes the file atomically. It reads `me.md` and the knowledge files for context.

## The plugin

The repo is also a Claude Code plugin marketplace with one plugin, `callcoach`, so a chat agent can prepare briefs.

- `.claude-plugin/marketplace.json` at the repo root, `plugin/.claude-plugin/plugin.json`, and two skills:
  `plugin/skills/prep/` (`SKILL.md` and `brief-format.md`) and `plugin/skills/update-knowledge/SKILL.md`.
- **prep:** finds the data folder (`defaults read app.callcoach dataDir`, else `~/CallCoachData`), reads
  `<data>/sources.md` (where this person's information lives: a Drive folder, a Notion space, a CRM, anything; on the
  first run it asks and writes it), asks at most four questions one at a time (each can be skipped), researches the
  web, those sources (read only) and the knowledge files and `me.md`, and writes the brief. People and companies it
  learned that no knowledge file has go into `<data>/knowledge/מהכנות.md` in the knowledge file format (a backup
  first, no duplicates).
- **update-knowledge:** refreshes the knowledge files from the sources in `sources.md` (a backup first, his own lines
  kept).
- The README says how to add the marketplace and install the plugin.
- The plugin has no personal information: no names, emails, folder names, company names or "do not touch" lists.
  Everything personal lives in the data folder. `tools/check-private.sh` covers `plugin/` and `.claude-plugin/`.

## Not in this change

- Catching up on what was said before a mid-call start, from CallRec's recording in progress.
- Recording without the coach, and full-quality transcription after the call.
- Explaining a term after the call that was not opened during it.
- Publishing the repo.
