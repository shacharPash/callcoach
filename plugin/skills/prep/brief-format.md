# Brief file format

A brief is one Markdown file in `<data>/briefs/`. CallCoach reads its header and uses the body as context for the
live model.

## File name

`<YYYY-MM-DD>-<slug>.md`. The date is the date of `when`, else today. The slug comes from the title: lowercase
ASCII words joined by `-` (for a Hebrew title, transliterate or use the company or topic in ASCII, at most 40
characters). If the name is taken, add `-2`, then `-3`. Never overwrite an existing brief with a new one; to change
a brief, edit it (see "Editing a brief").

## Template

```
---
title: Intro call with Acme
when: 2026-11-17 14:00
type: sales
lang: en
length: 30
goals:
  - Understand how they manage permissions today
  - Book a follow-up meeting
questions:
  - What made you look for a solution now? | מה גרם לכם לחפש פתרון עכשיו?
glossary: Acme, Okta, SOC 2
terms:
  - SOC 2: תקן ביקורת אבטחה לספקי שירותים
  - Okta: שירות התחברות וניהול זהויות לארגונים
links:
  - https://acme.example/product
---
## מי בשיחה
- Dana Example, VP Engineering at Acme: owns the rollout, previously led the platform team.

## רקע
Two or three short paragraphs: the company, the product, what led to this call.

## לא לשכוח
- Get to the budget owner.
- Avoid pricing before the pain is clear.
```

## Header rules

- The header is between two lines that contain only `---`, and it is the first thing in the file.
- Every key is optional. Leave out a key you have no value for; do not write empty keys.
- `title`: short, in the user's language.
- `when`: local time, `YYYY-MM-DD HH:MM` (24 hours). Omit it if unknown. Never guess a time.
- `type`: the name of an existing call type, taken from the file names in `<data>/call-types/` without `.md`
  (for example `sales`, `validation`, `general`). If none fits or the folder is missing, use `general`. Never
  invent a type.
- `lang`: `he` or `en`, the language the call will be spoken in. If unknown, omit it (CallCoach then uses `auto`).
- `length`: minutes, a number.
- `goals`: a list, at most 5. Short, concrete, what the user wants out of the call.
- `questions`: a list, at most 3. Good opening questions to ask, in the language of the call. When `lang` is not
  `he`, follow each question with ` | ` and its Hebrew translation, for example
  `What made you look now? | מה גרם לכם לחפש עכשיו?`. A Hebrew question has no translation.
- `glossary`: one line, comma separated names and terms likely to be said (company, product, people, jargon).
- `terms`: a list, at most 20, of the names and terms likely to be said that the user may not know, each as
  `Term: note`. The term is spelled as it is written; the note says what it is, in Hebrew, at most 8 words. An item
  without a note is ignored. CallCoach shows a term the moment it is said in the call, with this note. Leave out
  private people unless a source says who they are.
- `links`: a list of links worth having at hand (the company site, a deck, a doc). The live model does not read
  them; the user opens them from the brief's page in the app.
- `after`: only in a follow-up brief, the folder name of the call it follows (`2026-11-18_1131_sales`, a folder in
  `<data>/calls/`). The brief's page in the app links to that call.
- Unknown keys are ignored, so do not add any.
- Quote a value with double quotes if it contains `: ` or starts with a special character (`#`, `-`, `[`).

## Body rules

- Exactly three sections, in this order, with these headings (Hebrew, keep them as written): `## מי בשיחה`,
  `## רקע`, `## לא לשכוח`.
- Write the content in the user's language. Headings stay as above.
- `מי בשיחה`: one bullet per person: name, role, what is known of them. Say "unknown" rather than guess.
- `רקע`: the company, the product, what led to the call. Facts from sources, not marketing copy.
- `לא לשכוח`: what the user wants to get to or avoid, from the user's own answers and notes.
- Keep it scannable: the whole body should be readable in about a minute.
- Mark anything uncertain as such. Never invent facts, quotes or numbers.
- No credentials, tokens or private links in the body.

## Files

Files that belong to a brief (a deck, notes, a contract draft) go in the folder next to it named like the file
without `.md`, plus `.files`: `<data>/briefs/2026-11-17-acme.files/`. Copy them there; the folder is the list. The
live model does not read them; they are for the user and for preparing a follow-up.

## Editing a brief

When the user asks to change or add to a brief, edit that file in place:

- Keep the file name, and every header key you were not asked to change, as it is.
- Add to the body's sections rather than rewrite them; keep what the user wrote there.
- Keep the limits above (5 goals, 3 questions, 20 terms).
- The app reads the file again when its window comes to the front. If the brief is open for editing in the app at
  that moment, the app does not overwrite the file; it says the file changed and offers to load it again.

## Follow-up brief

A brief for the next call with the same people: read `<data>/calls/<folder>/summary.md` and the brief that call
used (the `brief` in `<data>/calls/<folder>/meta.json`, a file name in `<data>/briefs/`), then write a new brief
with `after: <folder>`. Carry over who is in the call and what is still open; the goals and opening questions are for
the next call, not the last one.
