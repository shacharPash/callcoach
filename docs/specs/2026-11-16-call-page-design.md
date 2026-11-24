# A call page with its name, recording and transcript, and a panel without the transcript

## Goal

CallCoach stands on its own for a call it recorded: after the call he can name it, listen to it and read its
transcript in the main window, without CallRec. During the call the panel shows no transcript (call apps already
show captions, and the lines were hard to read while talking), so the names and terms get the room.

## The call page (main window)

- **Name.** Next to the call's title, a pencil button ("שנה שם" / "Rename") opens a field with the current name.
  Saving writes `title` into the call's `meta.json` (every other key kept, written atomically); an empty name
  removes `title` and the default comes back (today's label from the call type). The folder is not renamed. The
  name shows in the list on the side, on the page, and in the "עלה בשיחה" line that "שמות חדשים" writes.
- **Player.** When the folder has `call.m4a`: play and pause, a position slider, elapsed and total time, speed
  (1, 1.25, 1.5, 2) and "הצג בתיקייה" / "Show in Finder" (reveals `call.m4a`). The recording is stereo (me on the
  left, the other side on the right) and plays as it is, as in CallRec. Playback stops when another call is
  chosen or the window closes. A call still in progress (no `call.m4a` yet) has no player.
- **Summary and transcript.** Under the header, a two-way choice: "סיכום" / "Summary" (today's summary and "שמות
  חדשים") and "תמלול" / "Transcript". The transcript is every line of `lines.jsonl` in order: its time from the
  start of the recording (`t_start` minus `started` in `meta.json`, as m:ss or h:mm:ss), the speaker ("אני" / "Me"
  for `me`, "הצד השני" / "Them" for `them`) and the text, each line in its own direction. A click on a line plays
  the recording from there. The line being played is highlighted. A call without a summary opens on the
  transcript.

## The panel

- The last two transcript lines and the live line of the other side's open sentence are no longer shown; their
  room goes to the names and terms list.
- Settings has "הצג תמלול בפאנל" / "Show the transcript in the panel", off by default. On brings back the lines
  and the live line as they were.
- Partial transcription keeps running, so known names still show before the sentence ends.
