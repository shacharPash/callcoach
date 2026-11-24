# One way to listen, sections you can close, and a clear "hears you" switch

## Goal

There is no call mode and watch mode any more: one start, one panel. What the panel computes is up to him while it
runs: each of its three sections closes with a click on its title, and a closed section costs nothing. A switch
says plainly whether CallCoach hears him, which is not the same as being muted in the meeting app. Questions to ask
come in the call's language with a Hebrew translation. Terms from a brief show the moment they are said. After a
call, the call page can draft a follow-up email.

## One mode

- The main window has one start button (and "הכן תדריך…"). "צפייה", its menu bar item and ⌃⌥W go away.
- Every start is the same: all three sections open, CallCoach hears him. Nothing is remembered from the last call.
  Watching a video or a lecture is a call where he closes "לשאול" and turns hearing off.
- The engine has no watch any more: the `cadence` key of a call type is ignored, the microphone is always captured,
  and the step asks on the other side's lines as a call does today (a line of 3 words or more; either side at the
  `active` level).
- `defaults/call-types/watch.md` is removed. Old call folders whose `meta.json` says `"watch": true` still open.

## The summary picks its own form

- When my lines hold less than 10% of the transcript's words (or there are none), the summary is lecture notes (the
  watch summary of today). Otherwise it is the call summary.
- `meta.json` gets `"summary_kind": "call"` or `"lecture"`.
- When CallCoach did not hear me for part of the call, the summary's input says so with the times, and the rules say
  I may have spoken then: never write that I was silent.

## The panel

From the top:

- **Status line:** the red dot and the time (the red dot means only "the call is being recorded"), the hearing chip,
  "הקשר", "עצור", "…". The agent dot shows only when something is wrong, as today.
- **The hearing chip** takes the place of "אני N%":
  - On: an ear symbol and "אני N%". The ear is a still outline in the secondary color: it does not fill or change
    color with his voice (that flicker was distracting in a real call).
  - Off: a capsule with its own color (not red, not orange) and the text "מקליט רק אותם", always visible while off.
    The words name the recording, so they never read like the meeting's mute. No tooltip: it would show in a
    screen share.
  - A click toggles it. The words "מושתק" and "השתק" and a crossed microphone never appear: the meeting app owns those.
  - Every start begins with hearing on.
- **Three sections**, each with a small title row: a chevron and the title. A click on the title row closes or opens
  the section. A closed section shows only its title row, dimmed, with "כבוי" after the title. A closed section is
  not computed by the engine (see below); opening it again starts from the next request, nothing is filled in.
  1. **"עכשיו":** the now line, as today. Before the first one, a dim "מחכה לשיחה". A click on the line (or on the
     chevron at its end, the same chevron as a term row's) opens **"עד עכשיו"** under it: "בטעינה" while it is
     asked, then up to five short Hebrew lines on the call so far, each as a bullet. A second click closes it. The
     answer is kept: opened again it shows at once, and during the call a newer one is asked for and replaces it
     when it comes; a newer one that fails keeps the last. A term's explanation also says "בטעינה" while it is asked. If it fails: "לא הצלחתי לסכם", and a click tries again.
  2. **"לשאול":** in this order:
     - **"שאלו אותך"**: a question the other side asked me that I did not answer yet, with its label "שאלו אותך",
       the question as said, and the Hebrew translation under it (smaller, secondary) when the question is not in
       Hebrew. It takes the card slot, before any card. Hidden while CallCoach does not hear me.
     - The card slot, as today (kinds `question`, `tip`, `steer` with their labels). A question card shows its
       Hebrew translation under it like a question.
     - The live questions (up to two). Each is the question in the call's language and, when that is not Hebrew,
       the Hebrew translation under it, smaller and secondary.
     - The goals line ("מטרות 1/3"), as today.
  3. **"מושגים":** the terms list, as today, plus a small chevron at the end of every row that opens it (pointing
     forward when closed, down when open), so it is plain that a row opens.
- **Nothing moves under new text.** The now line, the card slot and the live questions each keep the tallest height
  they had in this call (at this panel size), and the old and new text overlap while they fade instead of standing
  one above the other. A longer text makes room once; the room stays, so the panel does not move up and down as the
  line, the questions and "שאלו אותך" change.
- The transcript rows (when the setting is on), the banners and the notes stay as they are.
- ⌃⌥Q ("שאלה עכשיו") opens "לשאול" if it is closed, then asks.
- **The "…" menu** loses the nudge level (it stays in Settings as the level a call starts with). It keeps: שפה, מסך
  מלא, הצג תמלול, שאלה עכשיו, סמן רגע, הסתר.

## Hearing me

When the switch is off:

- The microphone stays open (the meeting app has it open anyway), but every microphone chunk is replaced by silence
  of the same length before anything uses it: `me.wav` gets silence, so it stays in step with `them.wav`, and nothing
  of my voice is kept, transcribed, sent to the model or measured.
- A sentence of mine still open when the switch goes off is dropped. The language detection of my side stays where
  it was.
- My share of the talk and the talk-too-much tip count only the time CallCoach heard me: while off, the other side's
  speech is not counted either. `me_voice` is false.
- "שאלו אותך" is cleared and not asked for.
- The transcript gets a note at each change: "מכאן CallCoach לא שומע אותי" and "מכאן CallCoach שומע אותי שוב", with the
  time. `transcript.md` shows them; the summary's input carries the off intervals.
- Switching on and off any number of times, at any moment (in the middle of a sentence too), breaks nothing.

## Sections the engine does not compute

- `now` off: the request's state says `now: off`, the rules say now is "", and a `now` that still comes is dropped.
- `ask` off: no live questions, no cards (the talk-too-much tip included), no "שאלו אותך". The goals still update
  when a request runs.
- `terms` off: the request asks for no terms, no `term` event is sent (from the model, the knowledge files or the
  brief), and partial transcripts stop (they exist only to find names early).
- All three off: no live request at all. The on-demand requests (`explain_term`, `recap`) still work.
- Closing "עכשיו" or "לשאול" forgets what it showed, in the engine and in the panel: the now line, or the questions,
  the card and "שאלו אותך". Opened again, the section is empty until the next request fills it. Closing "מושגים"
  keeps the terms already shown (they are the call's list); terms said while it is closed are not found.

## Languages

- The now line, term notes, tips and steers: always Hebrew.
- Live questions, question cards, opening questions and "שאלו אותך": in the call's language (`card_language`), each
  with a Hebrew translation `he`, which is "" when the text is Hebrew.
- A term keeps its own language, with a Hebrew note (as today).

## Terms from the brief

- The brief header gets `terms:`, a list of up to 20 items `Term: Hebrew note` (the note at most 8 words), for the
  names and terms likely to be said that I may not know. The brief writer fills it; the plugin's brief format
  documents it.
- At start the engine reads them from the brief file. A line or a partial transcript that says one shows it at once
  as a `term` (kind `term`, `known: false`, its note), with no model request, like a name from a knowledge file. Its
  name is added to the glossary for the transcription. A click explains it with the model, like any model term.
- Partial transcripts run when there are knowledge entries or brief terms (and "מושגים" is open).
- Opening questions in a brief header may carry a translation after " | ":
  `What made you look now? | מה גרם לכם לחפש עכשיו?`. The brief writer writes them so when the call is not in
  Hebrew.

## The follow-up email

- The call page's summary tab has "טיוטת מייל" for a call summary (not for lecture notes). A click runs
  `callcoach followup --data DIR --call FOLDER`, which shows "כותב טיוטה…" and then the draft: selectable text in the
  call's language with its own direction, a "העתק" button and "כתוב מחדש". Nothing is ever sent.
- The command reads the call's transcript, summary, `meta.json`, `me.md` and the brief used (if any), asks the strong
  model with no tools (as the summary does), and writes `followup.md` in the call folder: a first line
  `Subject: ...`, a blank line, then the body. It answers one JSON line on stdout, `{"ok": true, "path": ...}` or
  `{"ok": false, "message": ...}`, and exits 0 or 1.
- The email: in the call's language; a short thank you, what we discussed in two or three points, what each side
  committed to, the next step with its date when one was said; signed with my name from `me.md`. Only what the
  transcript says: no invented commitments, numbers or dates. Plain text.
- When `followup.md` exists, the tab shows it without asking again; "כתוב מחדש" asks again and replaces it.

## The protocol

Version 5.

Commands:

| command | fields | what it does |
|---|---|---|
| `set_hearing` | `on` (bool) | whether CallCoach hears me (see "Hearing me") |
| `set_sections` | `now`, `ask`, `terms` (bools) | which sections are computed (see above) |
| `recap` | | asks the live model for "עד עכשיו" and answers with `recap` |

`set_level` stays. `start` is unchanged; every call starts with hearing on and all sections on.

Events:

| event | fields | when |
|---|---|---|
| `status` | adds `hearing` (bool) and `me_voice` (bool: CallCoach heard my voice in the last second; false while hearing is off) | every second |
| `questions` | `items` become objects `{text, he}` | as today |
| `card` | adds `he` (the Hebrew translation of a question card; "" otherwise) | as today |
| `asked` | `text` (in the call's language; "" clears), `he`, `trigger_line_ids` | a question asked of me appears, changes or is answered |
| `recap` | `ok`, `text` (up to five lines joined by "\n", each a short Hebrew sentence with no bullet mark) | the answer to `recap` |

The card kind `insight` leaves the protocol.

The live step's reply gets `asked`: null when there is no open question asked of me, else `{text, he}`; the request's
state carries `hearing_me`, the open sections and `shown_asked`. `questions` items and the card get `he`. When a
"שאלו אותך" question is shown, a line of mine of 3 words or more also asks the model, so the question clears soon after
I answer it.

`recap`: the strong model (since 2026-11-17, see below; before, the live model) gets the call's transcript so far (its last 30 minutes), my notes and the call's title, and
writes three to five short Hebrew lines on what was said and agreed so far, at most 70 words, every line starting with a
Hebrew word. One request at a time; when no line came since the last answer, the last answer comes back at once. After
the call ends it answers `ok` false.

## Not in this change

- Memory per person and company across calls (the next round).
- Telling whether he is muted in the meeting app.
- A hotkey for the hearing switch.

## Added 2026-11-17: no nudges, and "עד עכשיו" on the strong model

- Every call starts at the `quiet` level and the app has no way to change it: the level picker in Settings and the
  nudges item in the menu bar menu are gone. No tips, no back-to-goal cards, no question cards and no talk-too-long tip
  come on their own. "שאלה עכשיו" (the "…" list and ⌃⌥Q) still brings a question card. "שאלו אותך", the live questions,
  the goals line, the now line and the terms are as before. The empty card slot reads "שאלה עכשיו: ⌃⌥Q".
- `recap` runs on the strong model, with its own 40 second timeout, since it is asked rarely and read with care. The
  engine keeps the levels and `set_level` for a client that sends it.
- Closing "עכשיו" no longer forgets "עד עכשיו": it opens again with the last answer at once and refreshes it in
  place, like opening it again while the section stayed open. The loading text reads "בטעינה…".

## Added 2026-11-17: "מה המושג שנאמר עכשיו?", "הוסף לידע" in the panel, and the context window

- The terms title row ends with "מה המושג שנאמר עכשיו?" (also ⌃⌥T, the "…" list and the menu bar menu). A press
  sends the action `terms_now`: the live model reads at once the lines whose speech ended in the last 30 seconds, the
  other side's open sentence when its partial is under 8 s old, and the shown terms. It runs on a thread of its own
  beside the live step, which it neither waits for nor changes (no pending line is taken or sent). Its terms show
  like the live step's, and the answer `terms_now` names the terms said, newest first, shown ones too, so the panel
  marks the one said last as fresh and scrolls to it. The row reads "מחפש…" meanwhile, and for 4 s "לא נמצא מושג"
  or "החיפוש לא הצליח". With nothing said in that time there is no request.
- An open row of a term that is not from a knowledge file has "הוסף לידע": the term goes to `שמות-משיחות.md` now, as
  the call page's "הוסף לקובץ הידע" adds it after the call (its sentences as the summary when they came). The engine
  reads knowledge files at the start of a call, so the name is known from the next call.
- "הקשר" opened its window centered on the main screen, and on another Space when the call app was full screen, so a
  click seemed to do nothing. The window now opens beside the panel on the panel's screen (`PanelLayout.beside`),
  moves to the active Space and may join a full-screen one, and the text field has the focus. The header buttons dim
  while pressed.
- Not done: keeping a timestamp so "עד עכשיו" only reads what came since its last answer. A 30 minute call is about
  12K input tokens (about 2.4 cents on the strong model); a recap from the last answer plus the new lines would save
  about 1 to 2 cents and half a second, and a recap of a recap drifts. Opening it again is already instant.

## Added 2026-11-17: picking the words of a term, and a shot of the call's window

- "מה המושג שנאמר עכשיו?" also opens the last two lines and the other side's open sentence as words to pick, under the
  terms title. The search of what was just said starts at once, as before, so a press that picks nothing costs nothing
  more; picking adds one request for the picked words. Picked words next to each other in a row are one phrase. "בדוק"
  sends `check_terms` with up to five phrases of up to 60 characters: a phrase a knowledge file or the brief names
  shows at once, the rest go to the live model in one request with the last 3 minutes of transcript, which fixes the
  transcription's spelling and writes each note. The answer is `terms_now` with `picked: true`; once picked words were
  sent, the search's own answer (`picked: false`) is left out. With nothing picked the words close after 15 s, and
  closing "מושגים" closes them.
- ⌃⌥P ("צלם את חלון השיחה", also in the "…" list and the menu bar menu) takes the call app's window only, with
  ScreenCaptureKit: no sound, flash or thumbnail, the call app is not told, and CallCoach's windows are never in it.
  The window is the largest of the app used last when it is a call app or a browser whose title names a call, else the
  largest of a call app, else a browser call window, else the largest of the app used last. It is saved as
  `screens/HHmmss.jpg` (at most 1568 pixels on the long side) and sent with `add_screen`; the live model answers
  `screen` with one Hebrew line of what the screen shows and its terms. The line is a note of the call, so the live
  step, "עד עכשיו" and the summary read it, and the panel shows it under "מהמסך". The context window can attach a shot
  to its note; a shot that is not added is deleted. With no screen recording permission a shot fails at once and asks
  nothing, so no system dialog opens during a call; Settings has a row to allow it before calls.
- Not done: taking the window again by itself every few seconds. Many calls have no slides, and an automatic shot
  costs a request each time.

## Added 2026-11-18: pause, a question button, names I know, and copying the transcript

- "השהה" beside Stop sends `set_paused`. Both channels are replaced by silence of the same length, as the hearing
  switch does for mine, so the recording keeps its time; nothing is transcribed, sent or measured, open sentences are
  dropped, and auto stop waits. The dot becomes a pause sign and the hearing switch reads "ההקלטה מושהית", a click on
  either goes on, and the menu bar has it too. `status` has `paused`; a click shows at once and an older status does
  not undo it for 3 s, as with hearing. Each change is a row in `lines.jsonl` and a note in `transcript.md`, and the
  summary, the follow-up and the recap list the paused times.
- "שאלה עכשיו" is a button at the end of the ask title row, as "מה המושג שנאמר עכשיו?" is on the terms row. It reads
  "מחפש שאלה…" until the forced card comes, or for 12 s.
- An open row has "אני מכיר, לא להקפיץ": the name goes to `לא-להקפיץ.md` in the data folder, one per line after `- `,
  and the row goes from the panel at once. The engine reads the list when a call starts: a term whose name, or the
  knowledge entry it names, is on it is never sent, and the live model gets the list with the shown terms. The
  knowledge files are never changed, since one may be a copy from Drive. Settings has a tab with the list, "החזר" for
  each name and a field to add one.
- "העתק את התמלול" on the call page's transcript tab, and during a call in the panel's "…" list and the menu bar,
  puts every line on the clipboard as `speaker (m:ss): text`, the speaker first so a Hebrew line starts with a Hebrew
  word.

## Added 2026-11-18, after the 11:31 call

- Steering: the context window ("הקשר", ⌃⌥N) takes facts or instructions to the coach. An instruction holds for the
  rest of the call, comes before the brief and the goals, and a later note wins. A note asks the live step at once
  (mode `note`, no card), and its questions replace the shown ones without waiting for their hold. "לא בכיוון" beside
  each live question drops it and adds the note `לא בכיוון: <question>`. The summary, lecture notes and recap keep
  everything that was said. The panel says "המאמן עודכן" for a few seconds.
- Hebrew questions: in a Hebrew call a live question or a card in another language shows its Hebrew translation.
- "שאלה עכשיו" digs into what is discussed now and takes an open goal only when the talk is stuck, the topic ran
  out, or time is short; a goal the call talked through counts as done.
- Fold: a chevron at the end of the status line folds the panel to that line and unfolds it as it was.
- Settings: a button in the main window's start area.
- CPU: the app publishes its model only when it changed, so idle ticks and no-op events redraw nothing.
