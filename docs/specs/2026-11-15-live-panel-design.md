# Live panel: questions, names and terms, transcript

Date: 2026-11-15. Status: option A chosen by the user from three mockups, built in one pass.

## Why

In a short test call the user did not really see the terms, and asked for live questions that follow the
call plus a stable page of names and terms that grows when a person or a company comes up. The panel
showed one rare nudge, the four latest terms (newest first, so every row moved) and one faint transcript
line.

## What changes

One panel with fixed regions, top to bottom:

1. The nudge slot, unchanged (rare, a few words, 20 s).
2. Ask next: up to two questions of at most 8 words, from the step reply's new `questions` array. They stay
   until the agent replaces them; an empty array keeps them. The block keeps the height of two questions.
   Late replies (older than the stale limit) do not replace them. A call type without the question card
   (watch) has no live questions and no block.
3. Goals, unchanged, when the call has goals.
4. Names and terms: every term in the order it came, with a kind (person, company, product, term) and a
   symbol for it. Four rows show; the list scrolls to a new term and tints it for 10 s.
5. Transcript: the last two lines with the speaker ("אני" / "הם"); a long line shows its end.

Live questions and terms come at every level, through the card gap, and cost no extra request.

## Protocol

- `term` gains `kind`. The app treats a missing kind as a term.
- New event `questions`: `items`, `lang`, `trigger_line_ids`. The orchestrator sends one with no items when a
  call with live questions starts, so the app reserves the block from the first second.
- The state message gains `live_questions: on|off` and `shown_questions`.

## Not in this change

- A full live view in the main window (option C).
- Fixing the microphone restarts seen in the same test call.
