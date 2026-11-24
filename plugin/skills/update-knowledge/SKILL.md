---
name: update-knowledge
description: Refresh the CallCoach knowledge files from the user's own sources. Use when the user asks to update, refresh or sync their CallCoach knowledge ("עדכן את קובץ הידע", "refresh my knowledge files").
---

# Update the knowledge files

Refresh `<data>/knowledge/*.md` from the sources in `sources.md`. Sources are read only: never write to them, never
send anything, never put credentials anywhere.

## 1. Find the data and the sources

Run `defaults read app.callcoach dataDir`; if it fails or prints nothing, use `~/CallCoachData`. If the folder does
not exist, say CallCoach is not set up and stop. Read `<data>/sources.md` (sections Use, Avoid, Notes). If it is
missing, say so, point to the `prep` skill, which creates it, and stop. Respect "Avoid".

## 2. Read what exists

Read every file in `<data>/knowledge/` that ends in `.md` and does not contain `.backup` in its name. Format:
entries start with `## <name>` under `# ` group headings; fields `סוג`, `כתיבים`, `תקציר`, `שלנו`, and others
(`פרטים`, `מקורות`, `דוגמאות`) that are for people. `סוג` is one of: חברה, קרן, אדם, ארגון, אירוע, מוצר, מונח.
`כתיבים` are comma separated transcript spellings (a trailing `*` marks an everyday word). `שלנו` is `אין עדיין` or
bullets with a date `YYYY-MM-DD` and `, מקור: ...` at the end. An entry without `תקציר` is ignored by CallCoach.
`פרטים` bullets are what the panel shows, one per line, when the name is clicked during a call. A file whose header
(before its first `## ` entry) has a line exactly `למודל: לא` is local only: CallCoach never sends it to a model.
Read only its `## ` heading lines, to avoid duplicating a name, and never change it.

## 3. Read the sources and propose

Using whatever tools this chat has, read the sources for people, companies, funds, events and terms the user deals
with, and for new meetings, introductions or conferences with names already in the files. Build a list of:

- New entries (names no file has, with case and spaces folded).
- Updates to existing entries: new `שלנו` bullets (dated, with source), a better `תקציר`, extra `כתיבים`.

Show this list to the user in short form and ask for a go-ahead before writing. Only include what a source supports.

## 4. Write

For each file you change, first copy it to `<file>.backup-<YYYYMMDD-HHMM>` in the same folder. Then edit:

- Keep every line the user wrote by hand. Add lines; change a line only to extend it (for example a new `שלנו`
  bullet, newest first). Never delete or reword existing text.
- Never duplicate a `## ` heading, in the same file or any other (case and spaces folded). An update goes into the
  existing entry.
- New entries go in `מהכנות.md` (create it with a `# ` heading line if missing), in the entry format:
  `## <name>`, `סוג:`, `כתיבים:`, `תקציר:`, `שלנו:`.
- A spelling must not equal another entry's name or spelling.

## 5. Report

Show a short summary: files changed, entries added, entries updated (what changed), backups created, sources that
were unavailable.
