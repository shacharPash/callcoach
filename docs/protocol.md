# Engine protocol, version 6

JSON lines over stdin (app to engine) and stdout (engine to app). Every message has `type`; events also carry `t` (wall time, seconds). Times named `t_start`, `speech_end`, `closed_at`, `line_ready`, `requested_at` and `received_at` are wall times in seconds. One example of every message is in `tests/protocol/events` and `tests/protocol/commands` (without `t`); a whole recorded call is in `tests/protocol/stream/demo-call.jsonl` (with `t`). The Python tests and the Swift self-check decode the same files. Required fields are listed in `engine/callcoach/protocol.py` (`EVENTS`, `COMMANDS`, `ERRORS`). Unknown extra fields are allowed and ignored.

The app refuses an engine whose `hello.protocol` is not 6, with a clear message, and does not start a call.

Changes in 6:
- The panel's "now" line was removed at first (the event `topic` and the live step's `now`), and came back later in 6 at the top of "סיכום ביניים": the event `topic`, the live step's `now`, and an optional `now` in `set_sections` and in `start.sections`. A sections object without `now` keeps the line off, so an app that does not know it never pays for it, and an app ignores an event it does not know. Nothing else changed, so the version stays 6.
- Later in 6: an optional `list_only` in `set_sections` and in `start.sections` ("רק מהרשימה", see "Sections"). Missing means false, so an older app keeps the model's terms; the version stays 6.
- The panel's sections are `ask` and `terms`: `set_sections` takes those two.
- New commands: `set_coach` (the coach on or off), `set_goal` (a goal checked by hand) and `focus_goal` (the goal the live questions aim at).
- `stop` takes `summary` (whether the engine writes the summary).
- `status` has `transcribing`.
- New error `no_agent_account`.
- A call has a coach that is on or off (see "The coach"). `start.record_only` starts it off; the agent is made for every call, a recording included, and gets nothing while the coach is off.
- The engine records from `start` even while the whisper servers warm up: `status` says `transcribing` false until they are ready, and the lines come from the start of the recording once they are. `idle` comes only when no call has started.
- `meta.json` gets `call_type` at the start, `coach_from` (the call's second at which the coach was first on: `0` for a call that starts with it) and, at the end, `goals` (their final state) and `notes` (the text written for the call plus the notes added during it).
- `callcoach summary` writes the summary of a call that has none (see "Summary later").
- `term` has `detail` (a known name with `פרטים` in its file), and `explain_term` takes an optional `more` ("עוד מהמודל"). An older app sends no `more` and ignores `detail`.

Version 5 adds the commands `set_hearing`, `set_sections` and `recap`, the events `asked` and `recap`, `hearing` and `me_voice` in `status`, and `he` (a Hebrew translation) in `card` and in every `questions` item, which is now `{text, he}`. The card kind `insight` and the watch are gone: every call listens the same way, the `cadence` key of a call type is ignored and the microphone is always captured. A brief header may have `terms:` and opening questions with a translation after ` | `. The summary picks its own form (`summary_kind` in `meta.json`). `callcoach followup` drafts a follow-up email. The action `terms_now`, the commands `check_terms`, `add_screen` and `set_paused`, the events `terms_now` and `screen`, and `paused` in `status` came later in version 5.

Version 4 adds `topic` (the line on what is being discussed now) and the live step's `now`. A segment is at most 8 seconds (was 12). The background request at start also sends opening questions. A live answer has at most 5 terms (was 3).

Version 3 removes `prepare` and `prep_result` (and the error `prepare_failed`): a brief file holds what preparing gives (see "Briefs"). `start` takes one `brief` in place of the `briefs` list, and `title` and `questions`. `set_language` takes `auto`. `explain_term` and `term_detail` are new.

## Lifecycle

1. The app starts `bin/callcoach-engine serve --data DIR` ahead of a call. The active account is in the environment (see Accounts).
2. The engine sends `hello`, then `status` with `state: "warming"` and a `usage` with today's and this month's totals, and starts the whisper servers.
3. When the servers are ready it sends `status` with `state: "idle"`, unless a call has already started. If they cannot start it sends `error` `transcription_start_failed` and still goes to `idle` (or, during a call, only sends the error): a call then records without transcription. A `start` may come while the engine is warming: the call records from it at once, `status` says `transcribing` false, and its lines come once the servers are ready, from the start of the recording.
4. Before the call the app may send `account_check` (answered by `account`) any number of times.
5. `start` begins the call: `ready`, the first `questions` (see below), then `status` every second, `segment`, `line`, `card`, `term`, `questions`, `asked` and `goals` as they happen, `term_detail` for each `explain_term`, `recap` for each `recap` `terms_now` for each `terms_now` action and each `check_terms`, `screen` for each `add_screen`, and `topic` as the line on what is discussed now changes. Every call starts with CallCoach hearing me. A call starts with the `sections` of `start` when it has them, applied before anything at start (the first `questions` and the background goals request). Without `sections`, a call with the coach starts with every section open (the now line too) and a recording (`record_only`) starts with the coach off and both sections closed; the app opens what it wants later with `set_coach` and `set_sections`. The opening questions and the background goals request come at the first moment of the call the coach is on with "שאלות" (`ask`) on: at the start, or later when the coach joins or `ask` opens (see "The coach"); once in a call.
   - When `start` has `questions` (the brief's opening questions), they come at that first moment as a `questions` event with no trigger lines (`lang` `he` when every question is Hebrew, else `en`), and stay until live questions replace them.
   - When `start` has no `goals`, there is an agent, and there is `text` or a brief body to work from, the engine asks the strong model for goals in the background at that first moment and sends them in a `goals` event when they come; the call does not wait for them and starts with the call type's goals (ids `g1`, `g2`, ...). The goals that come have ids of their own (`b1`, `b2`, ...), so a live reply still about the first goals marks none of them. When I already work with the first goals (a goal set by hand with `set_goal`, or a `focus_goal`), the ones that come are dropped (the log gets `goals_kept`), so a focus I chose is never undone. The same reply has up to three opening questions, each `{text, he}`: they come as a `questions` event (`lang` from their text as above, no trigger lines) only when the call has no opening questions from `start`, no live questions came yet and "שאלות" is open. If the request fails, the call keeps the call type's goals (the failure is written to the log only). Otherwise the call uses the `goals` it was given, or the call type's goals.
   - The terms of the brief (its header's `terms:`) are read at start: their names are added to the glossary, and a line or a partial transcript that says one shows it at once as a `term` (see the table).
6. The call ends on `stop`, on the end of stdin, on auto stop (`error` `auto_stop`) or on an error with `ends_call: true`. The engine sends `status` with `state: "stopping"`, `finalized`, any lines still being transcribed, `goals`, `usage`, `summary_ready`, and last `stopped`. Then the process exits. The summary is written when `stop` has `summary` true, or, when the call ends without a `stop` (the end of stdin, auto stop, an error), when the coach was on at any point; otherwise `summary_ready` comes with `ok` false and the message "בלי סיכום", and `usage.json` is still written. The summary is lecture notes when my lines hold less than 10% of the transcript's words (or there are none), else the call summary; `meta.json` gets `summary_kind` (`call` or `lecture`).
7. The end of stdin before `start` sends `stopped` and exits. One call per process.
8. `stopped` is sent exactly once and is always the last message. Nothing is written to stdout after it.

`bin/callcoach-engine run` (command line, replays, the demo) sends the same events; it starts the call by itself from its flags.

## Engine to app

| type | when | fields |
|---|---|---|
| `hello` | first message | `protocol` (6), `version` |
| `status` | on change before the call, every second during it | `state` (`warming`, `idle`, `active`, `stopping`), `elapsed`, `talk_share` (share of voiced time that is me, counted only while CallCoach hears me), `lang_me`, `lang_them`, `conversation_lang`, `agent` (`off`, `ok`, `unavailable`, `auth`), `level` (`quiet`, `normal`, `active`), `visible`, `hearing` (CallCoach hears me, see `set_hearing`), `me_voice` (CallCoach heard my voice in the last second; false while `hearing` is false or the recording is paused), `paused` (see `set_paused`), `transcribing` (true once the transcriber is ready; a call can be recording while it is false); during a call also `tokens` (tokens the call added, cache reads left out), `features` (those tokens by the feature that asked: `live`, `explain`, `translate`, `terms_now`, `recap`, `screen`, `other`), `cpu` (the engine and its children as a share of the whole Mac, sampled every 5 s, or null) and `limit` (`ok`, `near`, `reached`, as the subscription last reported) |
| `account` | answer to `account_check` | `ok`, `provider` (`claude`, `api`, `fake`, `none`), `name`, `via_claude_code` (the account runs through Claude Code: true for subscription accounts; on a Mac with managed Claude Code settings, for example a work laptop, the managed hooks see those sessions), `message` |
| `ready` | capture started | `folder`, `replay_t0` (wall time of the replay start, `null` when live) |
| `segment` | a segment closed at a pause, or after 8 s of speech without one (timing only) | `id`, `channel`, `t_start`, `speech_end`, `closed_at`, `duration` |
| `line` | a transcribed line (lines can arrive out of time order; place them by `t_start`) | `id`, `segment_id`, `channel` (`me` or `them`), `lang`, `text`, `t_start`, `speech_end`, `line_ready`, `conversation_lang` |
| `partial` | the other side's sentence so far, while they are still saying it; it comes only while a knowledge file has entries or the brief has terms, and "מושגים" is open, at most once per 2 s of their speech; the app drops it when the line with the same `t_start` comes | `channel` (`them`), `text`, `t_start` (the start of the open sentence, the same as its line's) |
| `card` | the agent has a nudge (a few words); none while "שאלות" is closed | `id`, `kind` (`question`, `tip`, `steer`), `text`, `lang`, `he` (the Hebrew translation of a question card that is not in Hebrew; `""` otherwise: a tip and a steer are always Hebrew), `trigger_line_ids`, `requested_at`, `received_at`, `forced` (`true` for an answer to `ask_now`) |
| `term` | a term, name or company was said, once per call (a knowledge entry or brief term once per entry; one named in Hebrew that was said in English, or that the agent named in English, has `term` as it was said, else the entry's name), while "מושגים" is open; a name from a knowledge file or a term from the brief comes while the other side is still saying its sentence (it is transcribed again every 2 s of speech), or else as soon as its line does, with no model request; `trigger_line_ids` is `[]` when it came before its line, and stays so when the line itself does not name it | `id`, `term`, `note` (what it is, a few words, in Hebrew), `kind` (`person`, `company`, `product` or `term`; a knowledge file entry also `fund`, `org` or `event`; a brief term is `term`), `lang`, `trigger_line_ids`, `known` (`true` for an entry from a knowledge file; `false` for a brief term, which has its note from the brief), `ours` (what we already know about a known name, newest first, without the sources; `[]` otherwise), `ours_links` (the web link of each `ours` line's source, in the same order, `""` for a line whose source has none; a click on the line opens it), `file` (the knowledge file a known name came from, without `.md`; `""` otherwise), `detail` (`true` for a known name whose entry has `פרטים`: `explain_term` answers with them at once, and `more` asks the model for more; `false` otherwise) |
| `term_detail` | answer to `explain_term` | `id` (the term's id), `ok`, `text` (two or three Hebrew sentences on what the term is and why it came up in this call; for a known name with `פרטים`, those bullets, one per line, and after `more` the model's sentences under them after an empty line; when `ok` is false, a short Hebrew reason) |
| `questions` | the questions to ask next changed; shown live questions stay at least 45 s (newer ones in that time are dropped), while opening questions give way to the first live ones at once; the first one comes when the call starts, or in a call that starts without the coach or with "שאלות" closed at the first moment both are on: the brief's opening questions (at most three), or with no items when a call with live questions and an agent starts without them (a call type without `question` cards has no live questions); later also the opening questions of the background request at start (see Lifecycle); none while "שאלות" is closed | `items` (at most two live questions, each `{text, he}`: the question in the call's language and its Hebrew translation, `""` when the question is in Hebrew), `lang`, `trigger_line_ids` |
| `asked` | the question the other side asked me and I did not answer yet appeared, changed or is gone; the same question again (case and punctuation folded) sends nothing; none while "שאלות" is closed or CallCoach does not hear me, and turning hearing off clears it | `text` (as they said it, in the call's language; `""` clears it), `he` (its Hebrew translation, `""` when it is in Hebrew), `trigger_line_ids` |
| `topic` | the line on what is being discussed now changed (the live step's `now`, always Hebrew, at most 14 words); a shown line stays at least 45 s (a newer one in that time is dropped); the same line again (case and punctuation folded) sends nothing, and an answer older than 8 s is dropped, as for live questions; none comes before the model writes the first one, and none while the `now` section is off | `text`, `trigger_line_ids` |
| `recap` | answer to `recap` | `ok`, `text` (up to five short Hebrew lines joined by `\n`, each starting with a Hebrew word and without a bullet mark; when `ok` is false, a short Hebrew reason); an answer to `recap` with `since` also has `since` (the call time the update reads from, like `14:05`) and up to three lines, and an empty `text` with `ok` true means nothing new was said |
| `terms_now` | answer to the action `terms_now` (`picked` false) or to `check_terms` (`picked` true) | `ok`, `ids` (for the action, the ids of the terms said in the last 30 seconds, newest first; for `check_terms`, the ids of the picked terms in the order picked; new ones are each sent first as a `term` with `trigger_line_ids` `[]`, and shown ones are named too; `[]` when none was said; when `ok` is false the search failed), `picked` |
| `screen` | answer to `add_screen` | `ok`, `text` (one Hebrew line, at most 30 words, on what the screenshot shows; when `ok` is false, a short Hebrew reason) |
| `translation` | during a call, while `set_translate` is on | `id` (the line's id), `he` (its Hebrew, at most 60 words): only for the other side's lines transcribed in English. One request at a time to the live model: lines that come while one runs wait, and the next request takes the newest three of them; older ones get no translation. A failed request is noted and not retried. Each request counts in the usage |
| `goals` | a goal changed, or the goals asked for in the background came | `goals` (the whole list of `{id, text, done}`) |
| `finalized` | recording compressed and verified (lines can still follow until `stopped`) | `ok`, `seconds`, `reason` |
| `usage` | after `hello` and after the call | `call`, `today`, `month`, each `{input, output, cache_read, cache_write, requests, usd}` (`usd` is the API price equivalent) |
| `summary_ready` | summary written | `ok`, `path` (`summary.md`), `message` |
| `error` | anything that went wrong | `code`, `message`, `ends_call` |
| `stopped` | last message | none |

## App to engine

| type | fields | effect |
|---|---|---|
| `start` | `call_type`, `lang` (`he`, `en` or `auto`: the language the call starts in; `auto` starts in Hebrew, and detection decides in every case), `level`, `text` (the call's context), `brief` (one file name in `briefs/`, or `""`), `length_min` (`null` or minutes), `goals` (list of `{id, text, done}`, may be empty), `glossary`, `name` (the call folder's name comes from it), `title` (the call's title, may be `""`), `questions` (the opening questions, may be empty; each as the brief writes it, `text | he` or the text alone); optional `replay` (a 16 kHz WAV played instead of live capture, for development), optional `record_only` (true or false), optional `sections` (`{ask, terms}`, both bools, and optional bools `now` and `list_only`: the same shape as `set_sections`) | starts the call; the engine reads the brief's body (without its header) as context and its header's `terms`, and writes `title` and `brief` into the call's `meta.json` when they are not empty, and `call_type`. With `sections` the call starts with exactly those sections open in place of the default; a `sections` that is not an object with both bools (and `now` and `list_only` bools when they are there) is a `bad_command`, like `set_sections`. The background goals request (and the opening questions it may send, and the brief's opening questions) happen at the start only when the call starts with the coach and `ask` on, else at the first moment both are. With `record_only` true the call starts as a recording: the coach is off (see "The coach"), no opening questions until it joins, no cards, both sections closed, and `meta.json` gets `record_only: true`; with no `stop` choosing otherwise and the coach never on, there is no summary |
| `stop` | `summary` (bool) | ends the call; `summary` says whether the engine writes the summary |
| `action` | `id`: `ask_now`, `mark` or `terms_now` | `ask_now`: a forced request that must return a question (ignored while "שאלות" is closed or the coach is off: the app turns them on first); `mark`: a bookmark at the current time, no tokens; `terms_now`: "מה המושג שנאמר עכשיו?", the live model looks at once for the terms said in the last 30 seconds (the lines whose speech ended in that time, the other side's sentence still being said when its partial is under 8 s old, and the shown terms, so a shown one keeps its spelling) and answers with `terms_now`. It runs beside the live step, which it neither waits for nor changes. One search at a time: a repeat while one runs is ignored. With nothing said in that time it answers at once with `ok` true and no ids, and without an agent, with "מושגים" closed (the app opens it first) or after the call, at once with `ok` false. The request counts in the usage like any other |
| `set_visibility` | `visible` | hidden: no agent requests; shown again: one catch-up request |
| `set_level` | `level` | changes how many nudges come: `quiet` none (only `ask_now`), `normal` rare, `active` more; terms come at every level. The app starts every call at `quiet` and never sends it |
| `set_language` | `lang` (`he`, `en` or `auto`) | `he` or `en` locks the language for the rest of the call; `auto` unlocks it, and detection decides again |
| `shown` | `card_id`, `t` | the app displayed this card at wall time `t` (for the latency report and the agent's state) |
| `account_check` | none | one tiny request with the current account |
| `add_context` | `text` (1 to 4000 characters) | during a call: context I add (who I am talking to, what I want) or an instruction to the coach (focus on X, no questions about Y, `לא בכיוון: <question>`, or `בכיוון: <question>` for more like it); the agent sees it in every request and the summary in my notes. While live questions are on, it asks the live step at once (mode `note`, no card) and the questions it brings replace the shown ones without waiting for their hold |
| `set_hearing` | `on` (bool) | during a call: whether CallCoach hears me (see "Hearing me") |
| `set_paused` | `on` (bool) | during a call: pause the recording, or go on (see "Pausing") |
| `set_translate` | `on` (bool) | during a call: translate the other side's English lines into Hebrew from now on (`translation` events), or stop; off forgets the lines still waiting. The app sends it while its תרגום and תמלול chips are on and the panel is shown and unfolded. A call without an agent, or with the coach off, never translates |
| `set_sections` | `ask`, `terms` (bools); optional `now` and `list_only` (bools, `false` when missing) | during a call: which sections are computed (see "Sections") |
| `set_coach` | `on` (bool) | the coach on or off; while it is off the engine sends nothing to the model. Example: `{"type": "set_coach", "on": true}` |
| `set_goal` | `id` (a goal's id), `done` (bool) | a goal checked or unchecked by hand; the coach leaves it so. Example: `{"type": "set_goal", "id": "g1", "done": true}` |
| `focus_goal` | `id` (a goal's id, or `null`) | the goal the live questions aim at now, or `null` to end the focus. Example: `{"type": "focus_goal", "id": "g2"}` |
| `recap` | `since` (optional, true or false) | during a call: asks the strong model for "עד עכשיו" (the call's transcript of the last 30 minutes, my notes and the call's title) and answers with `recap`: three to five short Hebrew lines, at most 70 words. It runs beside the live step and never holds it up. One request at a time: a repeat while one is running is ignored, and while no line came since the last answer, that answer comes back at once. A call without an agent, with the coach off ("המאמן כבוי"), or a call that has ended, answers at once with `ok` false. With `since` true after a first answer, it asks only for what is new: the model gets what it already answered and the transcript from 5 seconds before the last line those answers read, and writes one to three lines, at most 45 words, or "אין חדש", which comes as an empty `text`. While no line came since, that empty answer comes at once. A failed update is not kept, so the next one reads from the same point. With `since` before any answer, it is a first answer. The request counts in the usage like any other |
| `explain_term` | `id` (a `term` event's `id`); optional `more` (`true` or `false`, default `false`) | during a call: asks the live model for two or three Hebrew sentences, at most 60 words, on the term (its name, kind and note, and the transcript of the last 3 minutes), and answers with `term_detail`. It runs beside the live step and never holds it up. One request per term at a time: a repeat while one is running is ignored, and a repeat once the term has its detail answers at once from it. An unknown id, a call without an agent, or a call that has ended answers at once with `ok` false (a term that already has its detail still answers with it). The request counts in the usage like any other. `terms.json` gets `detail` for every term that has one. A known name whose entry has `פרטים` (its `term` event has `detail` true) answers at once from them, with no request. `more` true ("עוד מהמודל") asks the model about such a name all the same, with what its file says (unless the file is marked local only, see "קובץ ידע" in guide.he.md: then only the name and the transcript), and answers with the file's detail and the model's sentences under it; it asks once per name: after the answer was added, or on a term with no detail from a file, `more` is a plain `explain_term`. With the coach off, `more` answers at once with `ok` false and "המאמן כבוי"; a failure answers `ok` false and keeps the file's detail |
| `check_terms` | `terms` (1 to 5 phrases of up to 60 characters, picked from the transcript) | during a call: the words I picked from what was just said, each a term I want to know. A phrase that names a knowledge entry or a brief term (as its line would) shows it at once with no request. The rest go to the live model in one request, with the transcript of the last 3 minutes, the open sentence when its partial is under 8 s old and the shown terms, so it can tell what the transcription misheard (an English term in Hebrew letters is written in English, a Hebrew prefix letter is dropped). It runs beside the live step, which it neither waits for nor changes, and answers with `terms_now`, `picked` true. With "מושגים" closed (the app opens it first) or after the call it answers at once with `ok` false; without an agent, `ok` is true only when every phrase was known |
| `add_screen` | `path` (a `.png` or `.jpg` the app wrote, in the call folder's `screens/`), `text` (a note with it, may be empty) | during a call: a screenshot of the call app's window. A non-empty `text` is added at once as `add_context` adds it. The live model gets the image with the call's title, the note, the transcript of the last 2 minutes and the shown terms, and answers in one request beside the live step: one Hebrew line on what the screen shows, added to the notes as `מהמסך: <line>` (so the live step, the recap and the summary read it), and up to 5 terms written on the screen, which show like the live step's terms while "מושגים" is open. The answer is `screen`. Without an agent or after the call it answers at once with `ok` false. The request counts in the usage like any other |

A malformed command produces `error` `bad_command` and is otherwise ignored. A command in the wrong state (for example `start` twice) is also `bad_command`.

## The live step

Each request to the live model answers with one JSON object: `card` (null or `{kind, text, lang, he}`), `goals` (`[{id, done}]`), `terms` (at most 5 `{term, note, kind}`; the rules lean to including every name, acronym, product, company, standard or concept of the field, except everyday words and terms already shown), `questions` (live questions, `[{text, he}]`), `now` (a Hebrew line, at most 14 words, on what is being discussed and where it is going; `""` keeps the shown line) and `asked` (null when there is no open question the other side asked me, else `{text, he}`). Live questions, question cards and `asked` are in the call's language (`card_language`) with a Hebrew translation `he` (`""` when the text is Hebrew); the now line, term notes, tips and steers are always Hebrew. The request's state carries the shown questions as `shown_questions`, the shown asked question as `shown_asked`, the shown now line as `shown_now` (only while `now` is on), the open sections (`sections: ask on, terms on, now on`) and `hearing_me` (`on` or `off`). A call asks when the other side says a line of 3 words or more (at the `active` level, either side). While a question asked of me shows, a line of mine of 3 words or more also asks, so it clears soon after I answer it.

### Sections

`set_sections` says which of the coach features the engine computes: the panel's "שאלות" and "מושגים" chips, and the now line, which the app turns on while the "סיכום ביניים" chip and its "עכשיו" switch are both on (the recap itself and "תרגום" have their own commands, `recap` and `set_translate`). They count only while the coach is on (`set_coach`):

- `ask` off: no live questions, no cards (the talk-too-much tip included), no `asked`, and `ask_now` is ignored. The goals still update when a request runs.
- `terms` off: the request asks for no terms, no `term` event is sent (from the model, the knowledge files or the brief), and partial transcripts stop.
- `now` off (the default when `set_sections` has no `now`): the state says `now off` and has no `shown_now`, the rules say `now` is `""`, and a `now` that still comes is dropped. With `ask` and `terms` off and `now` on, the live step still runs, for the line alone.
- `list_only` on (with `terms` on; the default is off): "רק מהרשימה". Terms come only from the knowledge files and the brief's terms, matched locally as always, partials included. The live model is told terms are off for it (the state says `terms off`) and a term it still returns is dropped. With `ask` and `now` off too, no live request is sent at all. `explain_term`, a known name's file details, `terms_now` and `check_terms` still work as with `terms` on. With `terms` off, `list_only` changes nothing.
- All off: no live request at all, and the lines said meanwhile are not sent later. `explain_term` and `recap` still work.
- Closing `ask` or `now` forgets what it showed (the questions, the card and the asked question, or the now line), so opened again the section is empty until the next request fills it. Closing `terms` keeps the terms already shown; terms said while it is closed are not found.

### The coach

`set_coach` with `on` false: the engine sends the agent nothing at all, whatever the app asks. No live step, no translation, no goals request; `recap` answers at once with `ok` false and "המאמן כבוי", as do `explain_term` (a term that already has its detail still answers with it), `terms_now` and `add_screen`; `ask_now` is ignored. Lines are still transcribed and sent, names from the knowledge files and the brief's terms still show while "מושגים" is open, and the recap and the summary read the whole call.

- On: the live step starts from that moment. The lines said while the coach was off never reach it, but the recap and the summary read them. The sections stay as they were: the app sends `set_sections` right after `set_coach`.
- On with no working account: the coach stays off, the engine sends the error `no_agent_account` (the call goes on recording), and the app turns its coach chips off again.
- The first time the coach turns on in a call that started as a recording, `meta.json` gets `coach_from`.
- The first moment the coach is on with `ask` on (the coach joining with `ask` on, or `ask` opening while it is on) brings what a call that starts so gets at its start: the brief's opening questions (or a `questions` with no items, as at the start) and the background goals request, with the same conditions. Once in a call: whatever is toggled after, neither comes again, and a call that started with the coach and `ask` on had them at the start.

### Goals by hand

- `set_goal` checks or unchecks a goal by hand. The live step sees it as `done (by me)` or open and no longer changes it; the next `goals` event has it, and the summary uses the final state.
- `focus_goal` with a goal's id ("שאל על זה"): the next live request goes at once (mode `focus`, no card) and its questions aim at that goal; every later request carries it as `focus_goal` until the goal is done, another goal is chosen, or `focus_goal` comes with `null`. A goal that is done or unknown is ignored (a note in the log). A focus chosen while "שאלות" is closed or the coach is off asks nothing at once.

### Hearing me

`set_hearing` with `on` false: the microphone stays open, but every microphone chunk is replaced by silence of the same length before anything uses it. `me.wav` gets silence (it stays in step with `them.wav`), and nothing of my voice is transcribed, sent to the model or measured. A sentence of mine still open when hearing goes off is dropped; the language detection of my side stays where it was. While off, `talk_share` and the talk-too-much tip count neither side, `me_voice` is false, and the question asked of me is cleared (`asked` with `text` `""`) and not asked for. Each change is a row `{"kind": "hearing", "on", "t"}` in `lines.jsonl` and a note in `transcript.md` ("מכאן CallCoach לא שומע אותי", "מכאן CallCoach שומע אותי שוב") at its time; the summary's input lists the times CallCoach did not hear me, and its rules say I may have spoken then. Switching any number of times, in the middle of a sentence too, is safe. Auto stop still counts only the speech that was heard: while hearing is off, the other side's speech keeps the call going.

## Pausing

`set_paused` with `on` true: both sides are replaced by silence of the same length before anything uses them, so `me.wav` and `them.wav` stay in step and nothing is transcribed, sent to the model or measured. The sentences still open are dropped. While paused, `me_voice` is false and the call does not end for its silence; the 5 minutes with no speech count again from the moment it goes on. Each change is a row `{"kind": "paused", "on", "t"}` in `lines.jsonl` and a note in `transcript.md` ("מכאן ההקלטה מושהית", "מכאן ההקלטה ממשיכה"); the summary, the follow-up and the recap list the paused times, and their rules say nothing was recorded then.

## Terms I know

The data folder's `לא-להקפיץ.md` lists names and terms I already know, one per line after `- `. A call reads it when it starts: a term whose name, or the knowledge entry it names, is on the list is never sent as `term`, and the live model gets the list with the shown terms so it does not return them.

## Cards in the panel

The app shows a new card only after the current one has been visible for 8 seconds; a newer card waiting replaces an older waiting one. A forced card (`forced: true`) is shown at once. The app sends `shown` when a card becomes visible. A card leaves the panel after 20 seconds, an answer to `ask_now` after 40.

Under the status line the panel has a row of five chips, each on or off: "תמלול" (the transcript, the last lines with the speaker), "תרגום" (`set_translate`, a Hebrew line under every line in another language; it needs the transcript, so turning it on turns "תמלול" on), "סיכום ביניים" (`recap` and `recap_since`, with the now line from `topic` on top of the recap and a small "עכשיו" switch on its title row that turns the line and its `now` section off; the switch is remembered across calls and is on by default), "שאלות" (the question asked of me, from `asked`; the card; the live questions, up to two, replaced by each `questions` event that has items, each with its Hebrew translation under it when it is not in Hebrew; the goals checklist) and "מושגים" (every term in the order it came, so a row never moves; up to four rows show, fewer when rows have a second line; the list scrolls down to a new one and highlights it for 10 seconds, except while a row is open: then it stays and a round down arrow at its bottom edge counts the new terms, and a click on it scrolls down; a known name has a tinted symbol and, when it has `ours`, a second line with the total count and the newest one). With none of the last four on the call is a recording (הקלטה) and the app sends `set_coach false`; any of them on is the coach (מאמן): the app sends `set_coach true` and then `set_sections` with "שאלות", "מושגים" and the now line. The "מאמן" switch in the header turns every AI chip off, or back to the ones last on. The summary lists every term of the call, with every `ours` line and its source under the known names.

## Errors

| code | ends call | the app shows | meaning |
|---|---|---|---|
| `bad_command` | no | nothing (log) | a command was malformed or came in the wrong state |
| `command_failed` | no | nothing (log) | a command in the call raised an error; the call and the next commands go on |
| `capture` | no | status note, 5 s | the microphone or system audio restarted |
| `source` | no | banner | an audio source could not start or stop |
| `no_sources` | yes | banner | no audio source started |
| `engine` | yes | banner | the engine loop failed |
| `auto_stop` | yes | note in the summary window | no speech for 5 minutes |
| `recording_stopped` | no | banner, stays | disk full or write error; transcription continues |
| `transcript_stopped` | no | banner | transcript file write failed; lines are still shown |
| `transcription_start_failed` | no | banner | whisper servers could not start; the call records only |
| `transcription_failed` | no | nothing (log) | one segment failed |
| `transcription_stopped` | no | banner, stays | a whisper server stopped twice; recording continues |
| `langid_failed` | no | nothing (log) | language detection failed for one segment |
| `langid_stopped` | no | status note | language detection stopped; the language stays as it is |
| `system_audio_dropout` | no | nothing (log) | digital silence inside the other side's speech; call apps send it between phrases too, so it is not shown |
| `agent_unavailable` | no | amber agent dot | Claude is not answering or the quota is reached; one catch-up request on recovery |
| `agent_auth` | no | red agent dot, Reconnect in the main window | the account was rejected; no more requests in this call |
| `summary_failed` | no | message in the summary window | the summary request failed; a local summary was written |
| `export_failed` | no | message in the summary window | the copy of the call to the export folder (`CALLCOACH_EXPORT`) failed; the call is saved as usual and `export_error` in its meta.json keeps the reason |
| `no_agent_account` | no | message | the coach was asked to turn on with no working account; the call goes on recording |

The app also shows its own error when the protocol version does not match.

## Accounts

The app passes the active account to the engine in the environment:

| variable | value |
|---|---|
| `CALLCOACH_PROVIDER` | `claude` (subscription, Agent SDK), `api` (API key, Anthropic SDK), `fake` (scripted replies, development), `none` (no agent) |
| `CALLCOACH_ACCOUNT_NAME` | the account's short name, for example `work` |
| `CALLCOACH_ACCOUNT_DIR` | `claude` only: the account's Claude config folder, `~/.callcoach/accounts/<name>`, logged in once with `bin/callcoach-engine account login --name <name>` |
| `CALLCOACH_API_KEY` | `api` only: the key from the Keychain |

`bin/callcoach-engine account status --name <name>` prints one JSON line, `{"name", "logged_in", "message"}`, and nothing else the CLI said. `bin/callcoach-engine account logout --name <name>` runs the bundled CLI's `auth logout` in the account folder, which removes the login the CLI keeps in the Keychain, and prints one JSON line, `{"name", "logged_out", "message"}` (exit code 0, or 1 when the logout failed; an account folder that is not there is already logged out). Removing a subscription account in the app runs it in the background and then moves the account folder to the Trash; a logout that fails never stops the removal.

A subscription token never passes through CallCoach: it stays in the account folder, where the bundled Claude CLI reads it. The engine runs SDK sessions with `CLAUDE_CONFIG_DIR` set to the account folder and without any inherited Claude or Anthropic variables. The engine removes `CALLCOACH_API_KEY` from its environment as soon as it reads it, so no child process inherits it, and never logs it. `account.via_claude_code` is true for subscription accounts. On a Mac with managed Claude Code settings (for example a work laptop) the managed hooks see those sessions, so the account screen shows a warning for every subscription account and suggests an API key account for private calls. The engine does not look for managed settings itself. Switching accounts restarts the engine; it is allowed only between calls.

## Briefs

A brief is a Markdown file in `<data>/briefs/`, named `<YYYY-MM-DD>-<slug>.md` (the date of `when`, else today; the slug from the title as call folders slug a name; `-2`, `-3` when the name is taken). It starts with a header between a first line `---` (blank lines may come before it) and the next `---`:

```
---
title: שיחת היכרות עם Acme
when: 2026-11-17 14:00
type: sales
lang: en
length: 30
goals:
  - להבין איך הם מנהלים היום הרשאות
  - לקבוע פגישת המשך
questions:
  - What made you look now? | מה גרם לכם לחפש עכשיו?
glossary: Acme, Okta, SOC 2
terms:
  - SOC 2: תקן ביקורת אבטחה לספקי שירותים
---
## מי בשיחה
## רקע
## לא לשכוח
```

Every key is optional and unknown keys are ignored. `when` is local time, `YYYY-MM-DD HH:MM`; `type` is an existing call type, else `general`; `lang` is `he` or `en`, else `auto`; `type` and `lang` are read in any case; `length` is the number of minutes it starts with, up to 600; a value may be in single or double quotes; `goals`, `questions` and `glossary` are lists of `- item` lines or `[a, b]` (a glossary may also be comma separated, and a single goal or question may be the value itself); `goals` keeps the first 5 and `questions` the first 3. An opening question may carry its Hebrew translation after ` | ` (the brief writer writes it so when `lang` is not `he`). `terms` is a list of up to 20 `Term: note` items (the note in Hebrew, at most 8 words), for the names and terms likely to be said that I may not know; an item without a note is dropped, and a name is kept once. A BOM and CRLF line ends are fine. The engine reads a header exactly as the app does (`app/Brief.swift`), so the body it gives the live model never holds the header. A file with no header is plain context; its title is its first `# ` heading, else its file name. The app reads the header and fills `start` from it; the engine reads the body (`engine/callcoach/brief.py` parses and writes the format).

Two more keys are for the app and the brief writer, and the engine ignores them: `links` (a list of links, shown on the brief's page) and `after` (the folder name of the call a follow-up brief follows, linked from its page). Files added to a brief live in `<data>/briefs/<name>.files/` (the file name without `.md`, plus `.files`); the folder is the list, and the engine ignores it. The brief's page writes the file whole (`app/BriefDocument.swift`): the keys it edits in the order above, `links` and `after` last, and every other header line (`glossary`, or a key another writer added) kept as it was. It does not write a file that changed on disk since the page read it.

### The brief command

`bin/callcoach-engine brief [--data DIR]` writes one brief and exits. It runs no audio. The active account is in the environment, as for `serve`.

- stdin: one JSON request, `{"title"?, "when"?, "text", "files": [{"name", "text"}], "links": [url]}`. `text` is required (it may be empty when there are files, links or a title); `files` and `links` may be left out; `title` and `when` are strings.
- stdout: one JSON line, `{"ok": true, "path": "<the new file>"}` (exit code 0) or `{"ok": false, "message": "<Hebrew>"}` (exit code 1): for a request that is not JSON or not in this shape, no account, a rejected account, a failed writing request, an answer the model did not finish, or an empty reply.
- It makes two requests to the strong model (the same one as the summary) through the account in use:
  1. Research: with web search and web fetch only (a subscription account: the built-in `WebSearch` and `WebFetch` tools and no other, no settings and no MCP servers; an API key: the server tools `web_search` and `web_fetch`). It is given the request alone (title, when, text, files and links), never `me.md` or the knowledge files, so a page that tries to steer the model has nothing private to send out. It answers with short research notes: people, company, background and the sources it used.
  2. Writing: with no tools, in the same options as the summary. It is given the brief's rules and format, the request, the research notes, `me.md`, the call types (each with one line) and the knowledge files (cut to 60,000 characters in all, a file that is not UTF-8 left out), and asks for the whole file.
- If the research fails (any error but a rejected account), the brief is still written, from the request alone: the writing request is told so, and a line `{"note": "research_failed", "error", "message"}` goes to stderr (the app's engine log). A rejected account ends the brief.
- Only an answer that ended on `end_turn` counts: an answer cut at `max_tokens`, a refusal, or any other stop reason fails the research (written as above) or the brief (`ok` false, with a Hebrew message that the model did not finish). An API key's research that the server pauses (`pause_turn`) is sent back as it is, up to 5 times.
- The engine checks the header (an unknown type becomes `general`, a bad `when` or `length` is dropped, the lists are cut), puts the request's `title` and `when` in place of the model's when they are given, and writes the file atomically, never over another one.

## The follow-up email

`bin/callcoach-engine followup --data DIR --call FOLDER` drafts the follow-up email of one call and exits. It runs no audio. `FOLDER` is a folder name in `<data>/calls/` or a path. The active account is in the environment, as for `serve`.

- It reads the call's transcript (`lines.jsonl`), `summary.md` (without its usage section), `meta.json` (title, start, brief), `me.md` and the brief the call used (its body), and the times CallCoach did not hear me. The call's language is the language most of the other side's words are in.
- It makes one request to the strong model with no tools, in the same options as the summary. Only an answer that ended on `end_turn` counts.
- It writes `<call>/followup.md` atomically, replacing one that is there: a first line `Subject: ...`, a blank line, then the body, in the call's language. A reply without a subject line in its first three lines, or with an empty subject or body, is not a draft.
- stdout: one JSON line, `{"ok": true, "path": "<call>/followup.md"}` (exit code 0) or `{"ok": false, "message": "<Hebrew>"}` (exit code 1): for a call folder that is not there, a call with no lines, no account, a rejected account, an answer the model did not finish, or a reply that is not a draft. Nothing is ever sent.

## Summary later

`bin/callcoach-engine summary --data DIR --call FOLDER` writes the summary of a finished call that has none (a call that only recorded, or one stopped without a summary) and exits. It runs no audio. `FOLDER` is a folder name in `<data>/calls/` or a path. The active account is in the environment, as for `serve`.

- It reads the call's transcript, `me.md`, from `meta.json` the title, brief name, call type, goals and `notes` (the text written for the call plus the notes added during it, which the engine keeps at the end of every call that had any), and `terms.json`. It does not read the brief file: the brief's text reaches the request only through `notes`, as at the end of a call. A call recorded before `notes` was kept has none.
- It writes `<call>/summary.md` as the end of a call does, replacing one that is there, and sets `summary_kind` in `meta.json`. It adds no usage section and leaves `usage.json` as it is.
- stdout: one JSON line, `{"ok": true, "path": "<call>/summary.md", "message": ""}` (exit code 0) or `{"ok": false, "message": "<Hebrew>"}` (exit code 1): for a call folder that is not there, a call still recording, no account, or a rejected account. When the model fails, `ok` is false with its message and `path` names a local summary that was written instead.
