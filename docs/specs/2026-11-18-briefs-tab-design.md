# Briefs tab, the panel from the main window, follow-up briefs

Date: 2026-11-18. Asked for after the first calls with briefs: the main window and the panel feel like two apps, and a
brief the AI wrote is hard to see and fix before the call.

## Goals

- See what the AI prepared, and fix it, before the call.
- Keep a brief over days: its time, goals, questions, terms, links, files and context, all in one place.
- Start a call with any brief, not only the one that is due.
- After a call, see the brief with its recording and summary, and make the brief of the next call from it.
- Open the panel from the main window at any time.
- His own AI (his private Claude) can still create and edit briefs as files; the app reads what it wrote.

## Main window

- The sidebar has two tabs on top: "שיחות" (as today) and "תדריכים".
- The start area gets "פאנל השיחה". With no call it says "אין שיחה פעילה כרגע" beside the button for 4 s. During a
  call the call bar already shows the panel and brings it here.
- When a brief is made with "הכן תדריך", the window opens the briefs tab on it, so the new brief is in front of him.

## Briefs list

- Three groups: "הבאים" (a time from an hour ago on, no call yet, nearest first), "בלי מועד" (no time, no call, last
  changed first), "היו" (a call used it, or its time passed, newest first).
- A row: the title, then the time ("היום 14:00", "מחר 10:30", "בלי מועד") and the number of calls when there are some.
- On top of the list: "תדריך חדש" (an empty brief opened for editing) and "הכנה עם AI…" (the prep sheet).

## Brief page

- Tabs: "תדריך", then one tab per call that used the brief ("שיחה 18.11 14:00") with the call page as it is in the
  calls tab: the recording, the summary, the transcript.
- The brief tab, to read: the title; when, type, language and length; "המשך של: <call>" when the brief follows a call
  (a click opens that call); the buttons "התחל שיחה עם התדריך", "ערוך", "שיחת המשך" (once a call used it) and "פתח
  בעורך"; then goals, opening questions with their translation, terms with their notes, links, files, and the context
  (the body) drawn as the summary is drawn.
- "ערוך" turns the same page into a form: title, a time (on or off, date and hour), length in minutes, type,
  language, goals (up to 5), opening questions with an optional translation (up to 3), terms with a note (up to 20),
  links, files (add, drop, remove) and the context as text. "שמור" writes the file; "ביטול" drops the changes.
- A file that changed on disk since the page read it is not overwritten: the page says so and offers to load it again.
- The page reads the file again when the window comes to the front, so what his own AI wrote shows up.

## Call page

- "תדריך: <title>" under the call's title, when the call used a brief that is still there; a click opens it in the
  briefs tab.
- "שיחת המשך" next to it (also on calls without a brief).

## Follow-up brief

- "שיחת המשך" opens the prep sheet with "שיחת המשך ל<call>." in the text, and the call's summary and its brief (when it
  has one) already attached. He adds what is new and the time; the AI writes the brief as any prep.
- The new brief gets `after: <call folder>` in its header, so its page links to the call it follows.

## The brief file

The file stays the one the engine and his own AI already use, `<data>/briefs/<name>.md`, with two new header keys:

- `links:` a list of links (`  - https://…`).
- `after:` the folder name of the call this brief follows (`2026-11-18_1131_general`).

Files added to a brief go in `<data>/briefs/<name>.files/`; the folder is the list. The engine ignores both keys and
the folder: the live model still reads the body, and the brief's terms still show when they are said. Links and files
are for him and for the AI when it prepares a follow-up.

Saving keeps every header key the app does not edit (`glossary`, or one his AI added) as it was, writes the known keys
in the engine's order, and replaces the file whole. The format for any AI, with editing a brief and a follow-up brief,
is in the prep skill's `plugin/skills/prep/brief-format.md`; the skill also changes or adds to a brief when asked.

A term with no note is kept in the file (the engine ignores it), and the form says so. The form needs a title to
save; a file with no `title:` starts the form from its heading or file name.

## Not in this round

- An AI pass that rewrites a brief with new notes ("הוסף לתדריך עם AI").
- Calendar invites as briefs.
