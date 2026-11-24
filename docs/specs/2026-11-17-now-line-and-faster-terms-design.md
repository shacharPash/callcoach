# A line on what is being discussed now, faster and more terms, and a panel that comes to you

## Goal

During a call or a watch the panel answers three things at a glance: what the conversation is about right now,
what to ask next (in a call), and what each new name or term means. Terms come sooner and more of them. Watching
shows no questions and no cards. A lost panel comes back with one click.

## What the panel shows

- **"עכשיו"**: under the status line, one or two lines on what is being discussed right now, for example
  "דנה מסבירה איך בקשות גישה עוברות היום במייל". Always Hebrew, at most 14 words. It changes when the topic
  moves, not on every line. It shows in calls and in watches once the model wrote one; before that there is no
  line.
- **"לשאול עכשיו"** (calls only): one or two questions, as today. A call that starts without a brief but with
  text in "על מה השיחה?" gets opening questions from the background request that makes the goals, so there is
  something to ask before the other side has spoken. ⌃⌥Q still forces a fresh question when the call is stuck.
- **Cards** show their kind as a small label above the text ("טיפ", "חזרה למטרה", "שאלה"), so the symbol is
  never the only hint.
- **Watch**: no questions area and no cards. Only "עכשיו", the terms and the goals (when there are any).

## Terms

- More terms: at most 5 per answer (was 3), and the rule leans to including: every name, acronym, product,
  company, standard or concept specific to the field, unless it is an everyday word or already shown.
- Sooner:
  - A sentence is closed after at most 8 seconds of continuous speech (was 12), so a term said in the middle of
    a long stretch waits less for its line.
  - A watch asks the model on every new line, like a call does, instead of at most every 15 seconds.

## The engine and the protocol

The protocol becomes version 4.

- The live step's answer gets `now`: a string, "" to keep the shown line. A new event **`topic`** (`text`,
  `trigger_line_ids`) carries a new line; the same line again sends nothing.
- The background request at start returns questions as well as goals; when the call has no opening questions
  and no live questions were shown yet, they are sent as a `questions` event.
- The watch call type has `cards: term` (no cards, no live questions); its guidance asks for the now line and
  terms only. The watch step runs on every new line (a gap of at least 4 seconds between requests).
- The segmenter's longest sentence is 8 seconds.
- Terms: at most 5 per answer, with the broader rule above.

## Bring the panel here

- The menu bar menu has "הבא את הפאנל לכאן" while a call or watch runs, and the main window shows the same
  button during a call. It moves the panel to the top right of the visible frame of the screen the mouse is on
  (a 16 point margin) and shows it if it was hidden. The new place is kept like a dragged one.
- When screens are added or removed and the panel's place is on no screen, it moves to the main screen.
