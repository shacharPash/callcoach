# Terms and nudges: two lanes in the live panel

Date: 2026-11-15. Status: approved direction ("כיוון 1"), built in one pass.

## Why

In a real call the cards did not help. Three reasons:

- A sentence cannot be read while talking. The reader has one glance of two to four words.
- The tightened rules (silence by default, 60 s between cards, a term card only if "I likely do not know it")
  left the agent almost silent: one unprompted card in five and a half minutes of a live call.
- Terms and coaching competed for one slot, so a term card could push out a nudge and the other way round.

The user wants two things most: short unprompted reminders when they miss something, and help with terms and
names that come up.

## What changes

The panel gets two lanes.

### Terms lane

- A list under the nudge slot. Every term, acronym, product, company or public person the other side says,
  once per call, with a note of at most 8 words in Hebrew (the term itself stays as it was said; changed 2026-11-16, was the conversation language). Newest on top, the four
  latest visible, the newest highlighted for 10 s.
- The bar is low: anything a smart non-expert might not know. Everyday words are skipped. Private people are
  explained only from my notes, my profile or a brief, never guessed.
- Terms come in the same step reply as the card, so they cost no extra request and arrive in about the same
  1 to 2 s. The reply gets a `terms` array (at most 3 per reply). The engine drops repeats (case and
  punctuation folded) and sends each new term as a `term` event. The state message lists `known_terms` so
  the agent does not repeat them.
- Terms are not dropped for being late (they stay useful), not limited by the card gap, and arrive at every
  level, including the one without nudges. A hidden panel sends no requests, as today.
- The summary file gets a `## מושגים שעלו` section listing them (written by the engine, not the model).

### Nudge lane

- One slot, one line in a large font, at most 5 words (a question the agent offers on its own: at most 6).
  A small symbol shows the kind instead of a word label. An unprompted nudge disappears after 20 s, an
  answer to ⌃⌥Q after 40 s. The two "previous card" lines are removed.
- Still rare: at most one per 40 s (20 s at the high level; it was 60 s, but a nudge now leaves after 20 s), only at a moment that matters (a promising
  thread to follow, a compliment or hypothetical to turn into a real example, a forgotten goal, the call is
  stuck). A longer card is dropped and logged as `reply_problem`.
- `term` is no longer a card kind. Call type files that still list it keep working: it is ignored for cards.
- An answer to ⌃⌥Q stays a question of at most 12 words.

### Levels

The level now controls nudges only; terms are always on while the panel is visible.

| level | Hebrew | English | nudges |
|---|---|---|---|
| `quiet` | בלי תזכורות | No nudges | none, only ⌃⌥Q |
| `normal` | תזכורות | Nudges | rare |
| `active` | הרבה תזכורות | More nudges | gap halved, my own lines also trigger |

The default for a new call becomes `normal`, since unprompted reminders are the main value. The menu toggle
is renamed from notes to nudges.

Quiet with no goals now sends requests (for terms), where before it sent none.

### Watch mode

Requests every 15 s instead of 30 s, so terms in a talk arrive sooner. Insight cards follow the same card gap
as nudges and the 12 word limit.

## Protocol

- New event `term`: `id`, `term`, `note`, `lang`, `trigger_line_ids`. Protocol stays version 2: an older app
  ignores unknown events.
- `card.kind` is one of `question`, `tip`, `steer`, `insight`.

## Not in this change

- A separate fast model for terms (the step model is already the small one).
- Pulling Drive or other connectors into terms.
- Changing the prep or the summary prompt.

## Testing

- Engine: orchestrator tests for terms (emitted, deduplicated, known_terms in the state, quiet level, no
  staleness drop, card gap does not block them), the shorter card limits, the summary section, protocol
  fixture for `term`.
- App: self-check for decoding `term`, the terms list (dedup by id, newest first, four visible), nudge expiry
  at 20 s and 40 s, new level labels.
- Live: a demo replay with the fake agent shows terms and nudges in the panel.
