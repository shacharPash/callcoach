---
name: prep
description: Prepare a call brief for CallCoach, or change or add to one. Use whenever the user asks, in any language and any wording, to prepare for a call or meeting with someone ("prepare me for a call with ...", "prep for my meeting with ...", "תכין אותי לשיחה עם ...", "הכנה לפגישה עם ..."), including a first call with someone nothing is known about yet, to add something to a brief ("תוסיף לתדריך ..."), or to prepare a follow-up call. Researches past calls, the user's own sources, knowledge files and the web, then writes a brief file that CallCoach offers as the next call.
---

# Prepare a call brief

Write one brief file for CallCoach. This is read-only research: never send messages, emails or invites, never write
to the user's sources, never put credentials anywhere. The brief format is in [brief-format.md](brief-format.md);
read it before writing.

## 1. Find the data folder

Run `defaults read app.callcoach dataDir`. If that fails or prints nothing, use `~/CallCoachData`. If the folder
does not exist, say "CallCoach is not set up on this Mac" and stop. Call it `<data>` below. Only read what this
skill names: `sources.md`, `me.md`, `knowledge/*.md`, the names in `call-types/`, `briefs/` (file names, and the
brief being changed), and `calls/*/meta.json` and `calls/*/summary.md`.

To change or add to an existing brief, skip to "Change a brief" below.

## 2. Find the sources

Read `<data>/sources.md`. It says where this person's information lives. If it does not exist, ask once, in one
message: where does your information about people and companies live (a Drive folder, a Notion space, a CRM, a
wiki, local folders), which connectors available in this chat to use, and is there anything to avoid. Then write
`<data>/sources.md` in this format and tell the user it is local and personal:

```
# Sources

## Use
- <where to look, one line each: a connector or tool, plus the folder, space, project or path>

## Avoid
- <anything not to read or mention, one line each; leave the section out if there is none>

## Notes
- <how to search, naming habits, what is where; optional>
```

Never hardcode any source in this skill. Use only what `sources.md` lists, through whatever tools this chat has. If
a listed source needs a connector that is not available, say so and continue with the rest. Respect "Avoid".

## 3. Ask (at most four questions)

Ask one question at a time, short, each skippable ("דלג" or "skip"). Skip any question the request already
answered. In this order:

1. Who is the call with (person, company)?
2. When is it (date and time)?
3. What is the goal of the call?
4. Anything special I should know or watch for?

Answer a skip with no pushback. If today's date matters for "tomorrow" or "Thursday", resolve it to a real date.

If the name matches more than one person or company and the request does not say which, or the goal of the call is
unclear, ask before researching rather than guessing.

## 4. Research (read only)

- Past calls in `<data>/calls/`: each folder holds `meta.json` (the call's name) and `summary.md`. Find earlier
  calls with the same person or company by the name in `meta.json` and the names in `summary.md`. From each, take
  what is still open in the sections "התחייבויות וצעדים הבאים" (commitments and next steps) and "שאלות לשיחה
  הבאה" (questions for the next call). Skip calls that match anything under "Avoid" in `sources.md`.
- The web: the person and company, recent news, the product, anything that led to the call.
- The sources from `sources.md`: notes, past meetings, documents, CRM records about them.
- `<data>/knowledge/*.md`: what is already known (entries `## <name>`, with `סוג`, `כתיבים`, `תקציר`, `פרטים`,
  `שלנו` lines). Reuse it; do not contradict it without saying so. A file whose header (before its first `## `
  entry) has a line exactly `למודל: לא` is local only: read only its `## ` heading lines, to avoid duplicating a
  name in step 6, and never put anything from it into the brief.
- `<data>/me.md`: who the user is and what they do, to aim the brief.
- Names of files in `<data>/call-types/` (without `.md`), to choose `type`.

Stay on this one call. Stop when you can fill the three sections with real content.

A first call is normal: when past calls, sources and knowledge files have nothing on them, rely on the web and on
the user's answers, say in `## רקע` that this is a first call and which facts come from the web, and keep the brief
short rather than padding it.

## 5. Write the brief

Write `<data>/briefs/<YYYY-MM-DD>-<slug>.md` exactly as [brief-format.md](brief-format.md) says: header between
`---` lines, then `## מי בשיחה`, `## רקע`, `## לא לשכוח`. Write in the user's language (the headings stay as
written). Create `briefs/` if missing. Check the file name is free (add `-2`, `-3`). Never overwrite a brief.

Put what was left open in earlier calls with these people (from past calls and from the sources) first under
`## לא לשכוח`.

In the header, give the opening questions in the language of the call, each with ` | ` and its Hebrew translation
when the call is not in Hebrew, and fill `terms` with the names and terms likely to be said that the user may not
know, each with a short Hebrew note: CallCoach shows them the moment they are said.

## Change a brief

When the user asks to change or add to a brief: find it in `<data>/briefs/` by its title or date (ask if more than
one fits), read it, and edit it in place as "Editing a brief" in [brief-format.md](brief-format.md) says. For a
file to add, copy it into the brief's `.files` folder. Research only what the change needs. Then say in one line
what changed. Steps 2 to 5 do not apply.

For a follow-up call, follow "Follow-up brief" in [brief-format.md](brief-format.md), with the questions of step 3
that the summary does not answer.

## 6. Update knowledge

People and companies you learned that no knowledge file already has go into `<data>/knowledge/מהכנות.md`:

- Match existing headings across all `knowledge/*.md` files with case and spaces folded; an existing `## ` heading
  (in any file) means skip, never duplicate it.
- Before touching `מהכנות.md`, if it exists, copy it to `מהכנות.md.backup-<YYYYMMDD-HHMM>` in the same folder.
  Add new entries at the end and keep every existing line as is. Never edit any other knowledge file.
- If the file does not exist, create it starting with a `# ` heading line (for example `# מהכנות`).
- Entry format, one per name:

```
## Acme
סוג: חברה
כתיבים: Acme, אקמי
תקציר: One line: what it is and why it matters to the user.
שלנו: אין עדיין
```

  `סוג` is one of: חברה, קרן, אדם, ארגון, אירוע, מוצר, מונח. `כתיבים` are spellings the transcript may use, comma
  separated; end a spelling with `*` if it is also an everyday word. `תקציר` is required (an entry without it is
  skipped by CallCoach). `שלנו` is `אין עדיין`, or bullets each with a date `YYYY-MM-DD` and `, מקור: ...` at the
  end. Only add what a source or the web actually supports.

## 7. Finish

Tell the user where the brief is (full path), a two-line summary, and that CallCoach will offer it as "השיחה
הבאה". List any knowledge entries added and any sources that were unavailable.
