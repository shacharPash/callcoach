# A panel of any size, the other side's sentence as it is said, and new names after the call

## Goal

The panel is easier to read during a call (any size, up to the whole screen), shows what the other side is saying
before their sentence ends, opens a known name's full שלנו lines on a click, and after the call each new name the
model explained can be added to a knowledge file in one click.

## Panel size

- A grip in the panel's bottom right corner resizes it; the top edge stays where it is. The smallest size is
  today's width (440) and the height that fits the content with an empty terms list; the largest is the visible
  frame of the screen the panel is on.
- Text, symbols and spacing grow with the width: the scale is `min(2.0, width / 440)`, so a wide panel is a
  bigger panel, not a panel of long lines.
- Extra height goes to the terms list (more rows). The transcript, when it is shown, keeps two lines plus the live
  line. When the content needs more height than the panel has (a nudge, questions and goals at once), the panel
  grows to fit it; it never shrinks below the size that was set.
- "Fill screen" (a button in the status line, and ⌃⌥F like the other panel keys) makes the panel fill the visible
  frame of its screen; pressing it again brings back the size and place it had before. The size that was set (not
  the fill state) is kept between calls, like the place is today.
- The panel stays out of screen capture at every size: `sharingType` belongs to the window, not to its size.

## The other side's sentence, live

- After each partial transcription that has text (see the knowledge file spec, "Before the sentence ends") the
  engine sends the app a `partial` event: `channel` (`them`), `text`, `t_start` (the start of the open sentence).
- The panel shows it only when "הצג תמלול בפאנל" / "Show the transcript in the panel" is on in Settings (off by
  default, see the call page spec), under the last transcript lines, in a lighter style with a leading "…",
  truncated at its head like the other lines. It goes away when the line of the same sentence comes (a `them` line
  whose `t_start` is within 0.1 s of it), or 8 s after its last update. With the setting off the partials still
  run, so known names show before the sentence ends.
- Partials still run only when a knowledge file has entries, so without one there is no live line.

## A known name in full

- A click on a known name's row opens it in place: the whole summary and every שלנו line (month first, newest
  first). A second click closes it. One row is open at a time, and the list does not scroll to new names while a
  row is open.

## New names after the call

- When the call ends, the engine writes `terms.json` in the call folder: every name and term shown in the call,
  each with `term`, `note`, `kind`, `lang` and `known`.
- In the main window, a call whose `terms.json` has names that are not known lists them under "שמות חדשים", each
  with "הוסף לקובץ הידע". The button adds an entry to `<data>/knowledge/שמות-משיחות.md` (created with a short
  header when missing):

  ```
  ## <term>
  סוג: <חברה, אדם, מוצר, מונח, קרן, ארגון or אירוע, from kind>
  כתיבים: <term>
  תקציר: <note>
  שלנו:
  - עלה בשיחה "<call name>", <YYYY-MM-DD>
  ```

  and then reads "נוסף". A name that file already has (the same `## ` heading, case and spaces folded) reads
  "כבר בקובץ" without a button.
- The file's Hebrew name sorts after the Latin names, so it is read last and an entry he writes himself in another
  file wins over it.

## Not in this change

A faster model for the model's terms: the live step already runs on Haiku 5.5, so the 2 to 3 s is its answer
time; a separate small request for terms only would need its own measurement first.
