# One live panel with features you switch, and a clear home

Date: 2026-11-19. Asked for after the round of five: the main window mixes briefs, calls, recording and six buttons
in one row, record only opens a big panel that is not needed, and moving between "only record" and "coach" should
work both ways at any moment. The goal is an app clear enough to need no guide.

Built in two parts: part 1 is the live panel and what a call does (this round), part 2 is the home in the main window.

## The model

- Two places only: the home (the main window: prepare, start, past calls) and the live panel (the call happening now).
- A call always records. On top of the recording are features, each switched on or off at any moment of the call,
  and nothing restarts when they change: the same folder, the same audio files, the same transcript.
- Two modes, and the line between them is AI:
  - הקלטה: no AI at all. Nothing is sent to a model and no token is spent: recording and local transcription only.
  - מאמן: the AI features, any of them, even one alone (for example only translation).
- The features, each a chip with an SF Symbol and a word (no icon alone: the panel has no tooltips, since a tooltip
  shows in a screen share):
  - תמלול (local, in both modes): the transcript lines in the panel.
  - תרגום (מאמן): a small Hebrew line under each English line of the other side. It needs תמלול: switching תרגום on
    turns תמלול on, and switching תמלול off hides the translation.
  - סיכום ביניים (מאמן): what was said so far (today's 'עד עכשיו'), only on demand (see below).
  - שאלות (מאמן): the live questions, 'שאלו אותך', 'שאלה עכשיו' and the goals (see "Goals").
  - מושגים (מאמן): names and terms, from the knowledge files and from the model.
- The mode follows the chips: the call is in מאמן while any AI chip is on, and in הקלטה when none is. Switching an AI
  chip on during a recording turns the coach on with that chip alone. "מאמן" in the header is one switch for all of
  them: on brings back the AI chips that were on last time, off turns every AI chip off.
- Two ways to start, and they differ only in what starts on:
  - "התחל שיחה" starts in מאמן. The AI chips start as they were the last time the coach was on in the last call
    that had it (so a call that switched the coach off before its end leaves the chips from just before); before any
    such call, שאלות and מושגים start on.
  - "הקלטה בלבד" starts in הקלטה, with the panel folded to a strip.
- תרגום starts off by default and is on only after it was chosen (it is remembered from the last מאמן call, never
  turned on by a recording). תמלול is remembered across all calls. The chips write these choices, and the two
  switches leave the Settings page ("הצג תמלול בפאנל", "תרגום לעברית").

## The live panel

- The header, always: the red dot and clock, the hearing chip, "מאמן" (marked when on), "הקשר" (only with the
  coach), "השהה", "עצור", the fold chevron and a close button (X). "…" stays for the language, the size and the call's
  keys; its "הצג תמלול" moves to the chips.
- Folded, the panel is the header alone, as narrow as its content: the strip. Unfolded, the chips row is under the
  header, and under it the sections of the chips that are on, in the chips' order.
- X hides the panel, as ⌃⌥H does today. The recording goes on and the menu bar icon shows it. It comes back from the
  menu bar, from the home ("פתח את הפאנל") and with ⌃⌥H.
- Nothing that cannot be seen is computed: while the panel is hidden or folded, no live step, recap or translation is
  asked of the model. Recording, transcription and the local knowledge match go on.
- The record-only line "הקלטה בלבד: בלי סוכן ובלי סיכום" goes; the strip and the "מאמן" switch say it.
- The live "now" line goes: the model no longer writes it, and the live step runs only while שאלות or מושגים is on.

### סיכום ביניים

- It updates only when it becomes visible (its chip turns on, the panel unfolds or is shown again) and when its
  refresh button is pressed: a round arrow (`arrow.clockwise`) beside its title, in place of today's "עדכן" text.
- The first update covers the whole call; each later one adds only what was said since the last, as "עדכן" does
  today ("מאז 14:05", "אין חדש"). Before anything was said it shows "עוד אין מה לסכם" and asks nothing.

### Translation is a coach feature

- It runs only while תרגום is on (so the call is in מאמן), the panel is shown and unfolded, and תמלול is on.
- A call can be in מאמן with תרגום alone: then translation is the only request sent to the model.
- What it translates is unchanged: the other side's English lines, the newest 3 per request.

### Switching the coach on and off

- On: the engine starts the live step from that moment. The provider is set up at the start of every call, record
  only included, but a recording never sends it anything; switching on asks no login and loses no audio. Without a
  working account the switch shows the account problem and the call goes on recording.
- What the coach knows: questions and terms start from the moment it was switched on; סיכום ביניים and the summary
  read the whole transcript, the part before included.
- Off: the coach features stop, the chips keep their place, and the panel stays as it is.
- meta.json records the first minute the coach was on (`coach_from`, seconds), and the call page shows "המאמן הצטרף
  בדקה 12" for a call that started as a recording.

### Goals

- In the brief page, "מטרות" and "הקשר" are the first two sections, each with a one-line hint when empty: "מה אתה רוצה
  לצאת איתו מהשיחה" and "מה המאמן צריך לדעת".
- In the call the goals stay where they are today: a folded line "מטרות 1/3" inside שאלות, closed by default. Opened,
  it is a checklist with a check on each goal reached. The coach already aims its questions at open goals and closes
  a goal once it was talked through; that stays.
- A goal can be checked or unchecked by hand (a click on its check), and the summary uses the final state.
- Each open goal has "שאל על זה": the coach asks its next questions toward that goal at once, and keeps it as the
  focus until the goal is reached, another goal is chosen, or "שאל על זה" is pressed again. The focused goal is
  marked "בפוקוס".

### Recording starts at the click

- Today a start that arrives while the models are still loading waits for them, so the first seconds are not
  recorded. From now on the engine opens the microphone and the system audio and starts the recording at once; lines
  are transcribed when the transcriber is ready, from the start of the recording, so nothing is lost.
- The panel appears at the click with the clock running; while the models load, the header shows "טוען תמלול…".

### The summary is a choice

- "עצור" still takes two clicks. The second step offers two buttons, "עצור וסכם" and "עצור בלי סיכום". The default
  (the one in front, and the one the menu bar's stop and the Dock menu use) is "עצור וסכם" when the coach was on at
  any point, and "עצור בלי סיכום" when the call only recorded.
- A call without a summary has "צור סיכום" on its call page, at any later time. It runs a new engine command,
  `callcoach summary --folder <call>`, like `callcoach followup`, with the call's title and brief from meta.json.

## The menu bar

- The icon: `text.bubble` with no call, `text.bubble.fill` with the coach, a recording symbol while the call only
  records.
- In a call the menu starts with the call's state ("שיחה פעילה · מקליט" or "שיחה פעילה · עם מאמן"), then "הפעל את
  המאמן" or "כבה את המאמן", show or hide the panel, pause and stop. The coach items (question now, term now, shot of
  the call window, context, mark a moment) show only while the coach is on.

## Part 2: the home

- The sidebar has four tabs: "בית", "שיחות", "תדריכים", "הגדרות". The window opens on "בית".
- "בית", top to bottom:
  - The live card. With no call: "אין שיחה פעילה" and "פתח את הפאנל" (the idle panel, as today). In a call:
    "שיחה פעילה · 12:34 · הקלטה בלבד" or "· עם מאמן", with "פתח את הפאנל" and "עצור".
  - With no call: "על מה השיחה? (לא חובה)", then "התחל שיחה" and "הקלטה בלבד".
  - "השיחות הבאות": up to three briefs, the due one first and marked, each with "התחל", then "הכן תדריך" (⌃⌥C) and
    "כל התדריכים". "התחל שיחה" always starts a call without a brief; with a brief due, the brief's "התחל" is the
    main button instead. ⌃⌥S starts the due brief, else a call without one, as today.
  - "שיחות אחרונות": the last five, a click opens it in "שיחות"; a call without a summary shows "צור סיכום".
- Leaving the top area: "פאנל השיחה" (the live card has it), "הכן תדריך…" (under "השיחות הבאות"), the settings gear
  (the sidebar has "הגדרות"), "שיחה בלי תדריך" (that is "התחל שיחה" now).
- The account line shows only when something is wrong; the account lives in "הגדרות". (It became always visible on
  2026-11-18 because the account was hard to find; with "הגדרות" always in the sidebar it is one click away.)
- "שיחות", "תדריכים" and "הגדרות" stay as they are, apart from the two switches that move to the panel.

## Protocol and engine

- `start`: `record_only` keeps its meaning (start with the coach off). The provider is made for every call.
- `set_sections`: `ask` and `terms`; `now` goes. The call is in מאמן while either is on, תרגום is on, or the app asks
  for a recap; the engine tracks "it was in מאמן at some point" for `coach_from` and the summary default.
- `stop` gains `summary: true|false`. The engine writes the summary only when it is true.
- `set_goal` (`id`, `done`): a goal checked or unchecked by hand. `focus_goal` (`id` or null): "שאל על זה".
- `recap` and `set_translate` stay. In הקלטה the engine sends nothing to the provider, whatever it is asked.
- The engine opens the audio sources at `start` even while the transcriber warms up, and queues segments until it is
  ready.
- Protocol version goes up by one; fixtures, the demo stream and `docs/protocol.md` follow.

## Accounts (first, before part 1)

He looked for a way to add a second account and found none that worked. The render of the account page shows why:
the "חשבון חדש" section's name field is a bare placeholder row in a grouped form, so it reads as a label and not as a
field, and "הוסף" under it is greyed out until a name is typed, so a click on it does nothing. The name also takes
only lowercase English letters. The fix makes two accounts (for example a work subscription and a private one) easy
to add and to switch between:

- Under the accounts list, a "הוסף חשבון" button that always works. It opens a short sheet: the kind as two clear
  choices ("מנוי Claude" or "מפתח API"), then a bordered name field with an example ("למשל: פרטי"), then "התחבר" (or
  "שמור" for a key). A missing name shows a red line under the field instead of a greyed button.
- The name can be in any language. The folder name is made from it internally; existing accounts keep theirs.
- A subscription opens the Terminal sign-in at once, with one line: "בדפדפן, בדוק שמופיע החשבון הנכון לפני האישור".
  The browser signs in with whatever claude.ai account it is logged in to, which is how a second account can end up
  signed in as the first.
- After the sign-in the app checks by itself every few seconds for five minutes and shows "מחובר" or what went
  wrong; "בדוק" stays for later.
- Console and SSO move under "דרך אחרת להתחבר", so the first choice is the common one.
- Every account row shows "פעיל" or "הפעל"; switching works between calls, as today.

## Later, not in this round

- Translation of the whole transcript on the call page, and of his own lines.
- Memory per person or company, people in the room with him on his microphone.
- Who said what among several people on the other side. Today every other voice is "הם". Ways to tell them apart:
  - Local voice separation of the other side's audio: works in every call app, gives "דובר 1", "דובר 2" without
    names; a name can be attached by a click, from the brief's people. First after the call (transcript and summary),
    then maybe live.
  - Google Meet in Chrome shows the speaker's name in its captions; a small Chrome extension could pass the name and
    time to CallCoach. Meet in Chrome only, and it breaks when Meet changes its page.
  - The Zoom and Meet APIs give a transcript with speakers only after the meeting, and need account permissions.
  - Recommended order when it comes: local separation after the call, and the Meet extension only if names matter.
- Using what Meet or Zoom already do (captions, translated captions): research when it comes.
- The general polish pass over all screens: after part 2, from renders of every state, as a short list he picks from.

## Testing

- Engine: in הקלטה nothing reaches the provider, whatever is asked (recap, translate, sections); after the coach turns
  on, the live step runs; translation alone runs only translation; the summary is written only with `summary: true`;
  recap since; no now line; a start while warming records from the start and transcribes after; `set_goal` and
  `focus_goal`; `callcoach summary` on a finished recording.
- App self-checks: the chips' start state (remembered, the fallback to שאלות and מושגים, תרגום off by default and
  never on in a recording), an AI chip in a recording turning מאמן on with that chip alone, תרגום turning תמלול on, no
  model work while hidden or folded, the stop defaults, the menu bar icon per state, account names in Hebrew.
- Renders before install: the strip, the unfolded record-only panel with translation, the coach panel with every
  chip, goals, the two-button stop, the menu in each state, and, in part 2, the home with no call, in a call and with
  a due brief, light and dark.
