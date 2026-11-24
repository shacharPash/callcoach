# Knowledge files in the live panel

## Goal

Names I am expected to know (companies, funds, people, organizations, events and terms) show in the panel the
moment they are said, with a curated one-line summary and what we already know about them ("שלנו": meetings,
conferences, introductions). The model no longer explains them from its own memory; what it still explains is
what the files do not have.

## Files

- Every `*.md` file in `<data>/knowledge/`, except names that contain `.backup`.
- An entry starts with `## <name>` and is grouped under `# ` headings. Fields: `סוג`, `כתיבים`, `תקציר`,
  `שלנו`; others (`פרטים`, `מקורות`, `דוגמאות`) are kept for people and ignored by the engine.
- `סוג` maps to a kind: חברה company, קרן fund, אדם person, ארגון org, אירוע event, מוצר product, מונח term.
- `כתיבים` are spellings the transcript may use, comma separated. A spelling that is also an everyday word
  ends with `*` (for example `גם*`, `ליד*`): it is never matched in the transcript on its own.
- `שלנו` is `אין עדיין` or a list of bullets, each with a date (YYYY-MM-DD) and `, מקור: ...` at its end.
- An entry without `תקציר` is skipped. A name that appears twice keeps the first entry. An entry's name is its
  own: another entry's spelling that equals it is dropped from that entry. A spelling that belongs to two entries
  is dropped from both, and the events log names it.
- The files are read as UTF-8 (a byte order mark is fine) when a call starts, so an edit between calls applies to
  the next call. A file that cannot be read is named in the events log and in the preparation window.

## Matching

- Text is folded: case, niqqud, geresh and quotes removed, dashes and dots become spaces, then split into words.
- Each new line, from either side, is matched against the entry names and the spellings without `*`, the longest
  match first, with up to two Hebrew prefix letters (ו, ה, ב, ל, מ, ש, כ) allowed on the first word. A person's
  one word name is matched only as a whole word, so "רון" is not found in "שרון".
- A term the model returns is looked up by its whole text against all spellings, including those with `*`, and
  then with its spaces removed, so "Team 8" finds "Team8". When it is an entry, the entry is shown instead of the
  model's note, so a garbled or everyday-word spelling still reaches the file through the model.
- An entry shows once per call, like any term; the shown names stay in `known_terms`, so the model skips them.

## Before the sentence ends

A line comes only after its sentence ends, so a name early in a long sentence used to show about 3 s after it was
said. While the other side's sentence is still open, the engine transcribes its last 6 s again every 2 s of the
sentence so far (counted from just before its speech starts to its last speech, so the pause before the segment
closes adds none, and a sentence under about 1.8 s of speech gets none) and matches the text against the files at
once. One such transcription runs at a time, and the next one waits at least as long as the last one took, so on a
slower Mac they take at most half of the transcription server's time. None starts once the call is stopping. The
agent never sees them, and the line still comes as before. They run only when a knowledge file has entries, and
only for the other side. A name only a partial found stays in the list with no trigger line.

Measured on synthetic Hebrew with the real file (7 names): 6 of 7 found while the sentence was still being said,
0.9 to 2.1 s after the name (median about 1.4 s), each transcription about 0.35 s. Re-transcribing every 0.5 s
found names at a median 0.5 s but added about 44 W to the whole Mac (the GPU busy almost all the time). Every 2 s,
measured through the real engine on an M4 Max: idle 8.5 W, a call today 9.6 W, a call with partials 21.0 W while
the other side spoke almost all the time (120 partials in 5.6 min, 0.36 s each), so about 5 to 6 W more in a call
where they speak half the time. Apple's on-device Hebrew dictation cost no measurable power but found 3 of 7.

## Protocol

The `term` event gets two fields: `known` (true for an entry from a knowledge file) and `ours` (the `שלנו`
bullets without their source, newest first; empty for the model's terms). Kinds add `fund`, `org` and `event`. A name found before its line has
`trigger_line_ids` `[]`.

Added 2026-11-17: `ours_links` (the web link of each bullet's source, `""` when it has none) and `file` (the
knowledge file of the entry). An open known name ends with "מתוך <file>", and a bullet with a link opens it in the
browser on a click; only http and https links open.

## Panel, preparation and summary

- A known row has a tinted symbol. When it has `ours`, a second line shows the newest one, with the month first
  and the total count, so a cut line keeps both. The list keeps its height, so fewer rows show when rows have a
  second line.
- The preparation window lists a knowledge layer: the files and the number of entries.
- The summary lists every `שלנו` bullet, with its source, under the known term it belongs to.

## Not in this change

Adding the call's new names to a file after the call, logos and pictures, and sending the files to the model.
