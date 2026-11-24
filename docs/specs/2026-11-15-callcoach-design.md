# CallCoach design

Date: 2026-11-15. Status: draft for review.

## 1. What it is

CallCoach is a private live call copilot for macOS. While you are on a call (Zoom, Meet, Slack, WhatsApp, any app) it transcribes both sides locally, in Hebrew, English or both, and an AI agent reads the transcript and shows you, in a small floating panel that only you see, short suggestions: the next good question, a tip, a nudge back to your goal, a one-line explanation of a term. After the call it writes a summary.

Top requirement: speed. A suggestion that arrives after the moment has passed is noise.

Second requirement: it is easy. Preparing a call takes one text box and one button, and you can always see what the agent knows.

It is a separate app from CallRec. It reuses CallRec's capture approach, tools and models, but has its own repo, its own app and its own data folder.

## 2. Principles

- Nothing runs unless you start it. No audio capture, no models in memory, no tokens.
- Local first. Audio and transcription stay on the Mac. Only transcript text and your prep text are sent to Claude.
- Short output only. Every card is readable in a glance.
- Silence is the default. The agent shows a card only when it adds real value.
- The other side never sees it: no bot joins, no sounds, no notifications, no focus stealing.
- Personal project. Only the owner's personal identity appears in the repo and its history. Personal data lives outside the repo.
- Built to grow: buttons, call types, library cards and AI providers are added as data or as one new component, without touching the rest.

## 3. Scope

### Stage 1 (core, this plan)

- Live transcription of both sides, automatic language per segment (Hebrew, English), stereo backup recording.
- Agent cards: question, tip, steer (back to goal, time left), term.
- Goals checklist, talk ratio, three action buttons with hotkeys, three activity levels (the "active" level behaves like "normal" until stage 2).
- Watch mode: lectures, webinars, videos, meetings where you only listen.
- Prep window with the "what the agent knows" card, summary after the call, token usage.
- Accounts: connect a Claude subscription, two named accounts with a switch.
- One-command setup from a clone, synthetic demo call, latency and model comparison report.

### Stage 2

- Entity cards for companies and people, with web search, prefetched during prep for the people you are meeting, cached across calls.

### Stage 3

- Join an ongoing call: catch up on what was said before from CallRec's in-progress recording.

### Later ideas (not planned)

- GPT provider (OpenAI API, or ChatGPT subscription through Codex).
- Google Drive search during prep.
- Cumulative insights log per product across calls.
- Summary that updates during the call.
- Automatic full-quality re-transcription after the call (today: `callrec import call.m4a`).
- Remaining subscription quota in the UI, if the SDK exposes it.

### Non-goals

- Publishing, signing or notarizing the app. Distribution is a private GitHub repo shared with a few friends.
- Intel Macs.
- Speaker diarization inside one channel (all remote people are "them").

## 4. User experience

### States

| State | Capture and transcription | Agent | Panel |
|---|---|---|---|
| Off (default) | no | no | none |
| Active | yes | yes | visible |
| Hidden | yes | paused, no requests | hidden |

When the panel comes back from hidden, the agent gets one catch-up request with the lines since its last request.

Auto stop: if both channels are silent for 5 minutes, the call ends as if you pressed Stop.

### Starting

The menu bar icon or ⌃⌥C opens the prep window. The same flow is used before a call and in the middle of one.

Prep window:

- Account line: "Connected: Personal" with a green, amber or red dot, a switch between the two named accounts, and Connect / Reconnect.
- Call type buttons: Validation, Sales, General, Watch. Each type is a file that selects library cards and a goal template.
- Starting language: Hebrew or English, default = last used.
- Activity level: Quiet, Normal (default), Active.
- One free text box: who, goal, what I want to learn, optional call length.
- Briefs: files that appeared in `briefs/` since the last call, each with a checkbox.
- Prepare button: one request that returns 3 to 5 goals and 3 opening questions, and fills the "what the agent knows" card (one line per layer: me, library cards, this call, briefs, goals). Editable.
- Start button. Pressing Enter with an empty box is allowed (no goals, library and "me" still apply).
- Permission check for Microphone and System Audio Recording before Start, with a clear message if missing.

### Floating panel

An `NSPanel` with `.nonactivatingPanel`: floats above all windows, clicks never take focus from the call app. Default position top center near the camera, draggable, position remembered. No Dock icon, no sounds, no notifications. Right to left layout; English text is shown left to right automatically.

Top to bottom:

1. Status line: listening dot, elapsed time, your talk share in percent, current language (click locks it), activity level (click cycles), Stop.
2. Main card: the newest card, large font, type label (question, tip, steer, term, insight).
3. Previous cards: the last two, dimmed, one line each.
4. Goals: checklist with progress ("1 of 3").
5. Last transcript line: small gray text, one line.
6. Action buttons: Question now (⌃⌥Q), Mark moment (⌃⌥B), Hide (⌃⌥H).

Hotkeys do not clash with CallRec (⌃⌥R, ⌃⌥M), so both apps can run in the same call.

Card rules: a new card replaces the main card only after the current one was visible for at least 8 seconds. Questions are at most about 12 words, in the language the other side is speaking. Tips and steer cards are in Hebrew. Terms and insights at most 2 short lines.

### Actions

An action is a declared entry: id, label, hotkey, and what it asks the engine to do. Adding a button is adding an entry. Stage 1 actions:

- `ask_now`: forced agent request that must return a question.
- `mark`: records a bookmark at the current time. No tokens. Appears in the summary with the quote around it.
- `hide`: toggles Hidden.

### Watch mode

Call type Watch turns the mic channel off and changes the agent cadence: one request about every 30 seconds with everything said since the last one, instead of after every segment. Cards are insights and terms (entities in stage 2), and a good audience question near the end if there is Q&A. The summary becomes lecture notes.

### After the call

On Stop (or auto stop): capture ends, the recording is finalized (section 9), the transcript is written, and one summary request runs. A small window shows the summary with "Open folder". The summary is in Hebrew with quotes in the original language:

- What I learned: facts and real examples, separated from compliments and hypotheticals.
- Goals answered and not answered.
- Marked moments with their quotes.
- Commitments by each side, next steps.
- Questions for the next conversation.
- Usage: tokens (and how many came from cache) and the API price equivalent.

## 5. Architecture

```
CallCoach.app (Swift, menu bar)
  prep window, floating panel, hotkeys, accounts (Keychain), actions
        |  JSON lines over stdin/stdout (versioned protocol)
        v
engine (Python, uv script)
  capture: mic (ffmpeg) + system audio (catap)  or  file replay
  recorder -> call.wav (stereo, L=me R=them) -> call.m4a
  segmenter (per channel) -> language id -> transcriber
        |                                  |
        |                     whisper-server x2-3 (localhost, Metal)
        v
  lines -> transcript store (lines.jsonl, transcript.md)
        -> agent orchestrator -> provider (Claude via Agent SDK)
        -> app (line, card, goals, status, usage)
```

The app owns the UI and nothing else. The engine owns all logic and is fully testable without the app. The provider is the only component that talks to an AI service.

## 6. Components

Each component has one job and a small interface. Engine components are plain Python modules under `engine/` used by one entry script.

### App (Swift)

- `MenuBar`: icon, menu (Prepare call, today and month usage, data folder, quit).
- `PrepWindow`, `Panel`, `SummaryWindow`.
- `Hotkeys`: global hotkeys through Carbon `RegisterEventHotKey` (no Accessibility permission).
- `EngineClient`: starts the engine process, sends commands, decodes events, restarts the engine on crash.
- `AccountStore`: named accounts in the Keychain, passes the active credential to the engine through the environment.
- `Actions`: the action registry.
- Built with `swiftc` by `app/build.sh`, Command Line Tools only, like CallRec.

### Engine (Python)

- `protocol`: command and event types, JSON encode and decode, protocol version.
- `capture`: `MicSource` (ffmpeg avfoundation, raw PCM to a pipe, follows the default mic and restarts when it changes), `SystemSource` (catap process tap with `on_buffer`, no file), `FileSource` (plays a stereo WAV at real-time speed for tests and demos). All produce 16 kHz mono s16 frames per channel with capture timestamps.
- `recorder`: appends both channels to `call.wav` as they arrive; finalize, verify and delete (section 9).
- `segmenter`: per channel endpointing with WebRTC VAD. A segment closes after about 600 ms of silence or at 12 s. Very short noise is dropped. Both numbers are tuned with the demo.
- `langid`: a small multilingual Whisper model in its own whisper-server. Rules in section 8.
- `transcriber`: one whisper-server per language model (ivrit.ai for Hebrew, large-v3-turbo for English), loaded once per call. Uses Whisper's Silero VAD option and CallRec's hallucination phrase list as guards.
- `echo`: drops a mic line whose text is at least 80% similar to a "them" line within 2 seconds (speaker leakage without headphones).
- `store`: appends lines to `lines.jsonl`, renders `transcript.md`, records bookmarks.
- `meter`: talk share from voiced seconds per channel; emits the talk-too-much tip locally, no tokens.
- `orchestrator`: trigger policy, single request in flight, stale drop, hidden pause, catch-up, auto stop.
- `provider`: interface `prepare(prep) -> PrepResult`, `step(new_lines, state) -> AgentReply`, `summarize(transcript, state) -> Summary`, plus `usage`. Implementations: `ClaudeProvider` (Claude Agent SDK) and `FakeProvider` (scripted replies for tests).
- `prep`: builds the fixed context (rules, me, library cards, call type, this call, briefs) and the knowledge card.
- `usage`: per call totals into `usage.json`, daily and monthly totals for the menu.
- `profile`: first run speed test, picks Strong or Light (section 10).
- `callrec_catchup` (stage 3): reads CallRec's in-progress files. Isolated with its own test so a CallRec change cannot break anything else silently.

### whisper-server

Built from whisper.cpp 1.9.5, the same version and Metal flags as CallRec's whisper-cli, one more cmake target (`whisper-server`). Listens on 127.0.0.1 on a free port chosen by the engine. Started when the prep window opens (so Start has no delay) and stopped when the call ends or the prep window closes without starting.

## 7. Protocol

JSON lines. Every message has `type`. The first engine message is `hello` with `protocol: 1`; the app refuses a different major version with a clear error. The schema is documented in `docs/protocol.md` with one example file per message under `tests/protocol/`; both the Python tests and a Swift self-check decode the same files.

App to engine: `prepare`, `start`, `stop`, `action` (id), `set_visibility`, `set_level`, `set_language` (lock), `shown` (card id, wall time), `account_check`.

Engine to app: `hello`, `ready`, `prep_result`, `status` (state, elapsed, talk share, language, agent health), `segment` (timing only, for the latency report), `line`, `card`, `goals`, `summary_ready`, `usage`, `finalized` (recording verified or kept), `error` (code, human message, recoverable), `stopped` (last message).

Each `line` carries: id, channel (`me` or `them`), language, text, `speech_end` (wall time of the last voiced frame), `line_ready` (wall time). Each `card` carries: id, type, text, language, trigger line ids, `requested_at`, `received_at`.

## 8. Language

- The starting language from prep is only the initial assumption.
- Every segment of at least 1.5 s goes through `langid`. If Hebrew or English holds at least 70% of the Hebrew plus English probability, and the two together hold at least 30% (so another language or noise cannot flip it), that language is used; otherwise the current language is kept. Shorter segments ("yes", "OK") always use the current language, so detection does not flap.
- The segment is transcribed by the model of its language.
- The question language switches after two consecutive "them" segments clearly detected in the other language (short or unsure segments do not count). Tips and steer cards stay in Hebrew.
- Clicking the language in the status line locks it for the rest of the call.
- Hebrew sentences with English terms inside are handled by the ivrit.ai model as today.
- Demo check: the call starts in Hebrew and a new participant switches it to English; the report shows after how many segments transcription and questions switched.

## 9. Recording safety

- During the call: `me.wav` and `them.wav`, 16 kHz 16 bit mono each, written incrementally and aligned to the wall clock (a channel that falls behind by more than 0.25 s is padded with silence). Together about 230 MB per hour. Header sizes are refreshed every 5 seconds. Two files keep the channels independent (watch mode has no mic, the mic restarts on its own).
- Finalize: merge into `call.m4a` (stereo, left = me, right = them, AAC 64 kbps, about 30 MB per hour). Delete the two WAVs only if ffmpeg exited 0 and the m4a duration (ffprobe) is within 1 second of the longer WAV. Otherwise keep them and show "Compression failed, original kept".
- Crash recovery: on launch (and with `callcoach recover`), any call folder that still has `me.wav` or `them.wav` is repaired (header sizes from the file length, as CallRec's `repair_wav`), padded to equal length, merged and verified the same way, and only then are the WAVs deleted. `transcript.md` is rebuilt from `lines.jsonl`.
- Disk full: stop recording, keep transcribing, show the error.
- The format matches CallRec, so `callrec import call.m4a` re-transcribes any call at full quality.

## 10. Hardware profiles

Minimum: Apple Silicon (M1 or newer), macOS 14.2 or newer (process taps).

On first run the engine transcribes a bundled 5 second clip and picks a profile. The threshold is set in the plan from measurements.

- Strong (for example M4 Max): Hebrew and English models plus the language id model stay loaded for the whole call, about 3 GB. Language switches are instant.
- Light: only the starting language model (a quantized variant) plus the language id model, under 1 GB. On the first switch the other model loads (a few seconds) and the segments from that window are re-transcribed, so nothing is lost.

Between calls the app holds no models. During a call only the current segment is in memory; audio goes to disk.

## 11. Agent

### Context layers

All are plain files in the data folder:

- `me.md`: who you are, your product, how you like to work. Template in the repo, filled by you or drafted by your personal agent.
- `library/*.md`: principle cards of about one page (The Mom Test, sales discovery). The repo ships two, written in our own words. Adding a card: paste text or a file, one request distills it into a card.
- `call-types/*.md`: which library cards to load, goal template, cadence, card types.
- This call: the prep text box, selected briefs, goals and opening questions from Prepare.

There is no model training. "Already knows" means the fixed layers are sent once per call at the start and served from the prompt cache.

### Request shape

- Fixed prefix per call: rules, card schema, me, library cards, call type, this call. Sent once, cached.
- Each step: only the new lines since the last request, plus a compact state (cards already shown, goal states, current language, level, elapsed and planned length).
- One Claude Agent SDK client session per call, streaming input.
- Reply: strict JSON, either `{"card": null}` or one card, plus optional goal updates. Stage 2 adds detected entities to the same reply (no extra request).
- No tools in stage 1. Stage 2 enables only web search, only at the Active level, only for entity lookups.
- Short output, thinking off or lowest effort, low max tokens.
- Model: measured in the demo. Candidates `claude-haiku-5-5` and `claude-sonnet-5-5` for steps; Prepare and the summary may use the stronger one because they are not latency critical.
- Known limit: one session per call means the context grows with the call. The demo measures tokens per minute; a rolling summary of older lines is added only if a one hour call proves too slow or too costly.

### Trigger policy

- Call mode: a request after each closed "them" segment of at least 3 words. Only one request in flight; lines that arrive meanwhile go into the next request.
- Watch mode: one request about every 30 seconds if there are new lines.
- `ask_now`: immediate forced request.
- A reply that arrives more than 8 seconds after its trigger is dropped as stale.
- Hidden: no requests; one catch-up request when shown again.

### Card types and levels

| Level | Cards |
|---|---|
| Quiet | only on `ask_now`; goals checklist still updates |
| Normal | question, tip, steer, term |
| Active | Normal plus entity cards with web search (stage 2) |

Steer: if the conversation drifted from the goals for a few minutes, a bridge sentence back ("We drifted to pricing. Back to: who decides on purchase?"). If a call length was given, a reminder near the end about unanswered goals.

### Entity cards (stage 2)

- Detected entities come in the normal step reply.
- Terms are explained from model knowledge, no search.
- People and companies you named in prep are looked up during Prepare and are ready during the call.
- New names during the call are looked up in a background request that never blocks the step loop. Card when ready, usually 5 to 10 seconds, with the source.
- Cache in `cache/entities.json`, reused across calls. A per call search cap.
- At most 3 lines. If it is unclear which company is meant, the card says so instead of guessing.
- In the panel: a row of entity names under the main card; the newest is open, others open on click.

## 12. Accounts

- CallCoach has its own credential slot, separate from your Claude Code login. The engine runs the Agent SDK with an isolated Claude configuration, so it loads none of `~/.claude` (settings, skills, agents, MCP servers, CLAUDE.md) and cannot use another account by accident.
- Connect: a browser opens on the normal Claude login, you approve, you give the account a short name. Two accounts can be stored; the prep window switches between them.
- Credentials are stored in the macOS Keychain, never in the repo or the data folder.
- After connecting, one tiny test request; the prep window shows "Connected: Personal" with a green dot. Every real request updates the health; an auth failure turns it red with Reconnect. Prepare runs before the call, so a broken login surfaces before the call starts.
- The exact mechanism (long lived token from `claude setup-token`, or a separate configuration directory with its own login) is chosen by a spike at the start of the plan.
- Sharing note: Anthropic does not allow products to offer claude.ai login. A personal tool that a few friends run, each with their own subscription, is personal use. If it ever becomes a product, it moves to API keys, which is one new provider.

## 13. Usage

The SDK reports usage per reply (input, output, cache read, cache write). The engine sums them per call (`usage.json`) and per day and month. The summary window shows the call total and the API price equivalent (from a pinned price table in the repo); the menu shows today and this month. The floating panel never shows usage.

## 14. Invisibility

- No bot, no sounds, no notifications, no Dock icon, panel never takes focus.
- The panel sets `sharingType = .none`. On macOS 15.4 and newer this does not reliably hide a window from ScreenCaptureKit based sharing, so the guidance is: share a window, not the whole screen, and use ⌃⌥H before sharing.
- macOS shows its own recording indicator in the menu bar; only you see it.

## 15. Data and files

Data folder (default `~/CallCoach`, configurable, can live in iCloud Drive or another synced folder):

```
~/CallCoach/
  me.md
  library/*.md
  call-types/*.md
  briefs/*.md
  cache/entities.json        (stage 2)
  calls/<YYYY-MM-DD_HHMM>_<slug>/
    call.m4a
    me.wav, them.wav         (only during the call or after a crash)
    transcript.md
    lines.jsonl
    meta.json                (start time)
    prep.md                  (what the agent knew)
    summary.md
    terms.json               (every name and term shown, with known; written before the summary request)
    usage.json
    events.log               (events and timings, no audio)
    logs/                    (whisper-server logs)
  settings.json
```

Tools folder `~/.callcoach/`: `bin/whisper-server`, the language id model, and links to CallRec's ffmpeg, ffprobe and models in `~/.callrec` when present (no second 2.5 GB download), otherwise the same pinned tools and models are downloaded with sha256 checks. Until checkpoint 3 (clean install), setup requires CallRec.

Repo:

```
callcoach/
  app/            Swift sources, build.sh
  bin/            callcoach-engine (runs the engine with the project environment)
  engine/         Python package callcoach (pyproject.toml and uv.lock at the repo root, pinned)
  defaults/       me.md template, library cards, call types
  tools/          build-whisper-server.sh, bench_whisper.py, check-private.sh
  demo/           script.json, make_demo.py, report.py
  tests/          pytest suites, protocol fixtures
  docs/           specs, plans, protocol.md
  setup.sh
```

## 16. Errors

The recording and transcript never stop because of the agent.

| Failure | Behavior |
|---|---|
| Claude unavailable or quota reached | status "Agent unavailable" (amber); transcription and recording continue; one catch-up request on recovery |
| Reply later than 8 s | dropped |
| Reply not valid JSON | ignored, logged |
| whisper-server crash | restarted once; recording continues; missed segments transcribed after restart; second crash: "Transcription stopped", recording continues |
| Mic disappears (AirPods) | restart on the new default mic, as CallRec |
| Missing permissions | message in the prep window before Start |
| Engine crash | app restarts it once, recovery runs (section 9) |
| Protocol version mismatch | clear error, no start |
| Disk full | recording stops, transcription continues, error shown |

Every call folder has `events.log` with events and timings.

## 17. Testing

- Unit tests (pytest) for every engine component with `FakeProvider`: segmenter, langid rules, echo filter, recorder finalize and recovery, trigger policy, stale drop, hidden and catch-up, reply parsing, actions, usage sums, protocol fixtures. Deterministic, no tokens.
- Swift: a self-check that decodes every protocol fixture; manual UI checklist.
- Synthetic demo call: a scripted validation call generated with macOS `say` (Hebrew and English voices), mixed into stereo (left me, right them). Planted moments: a Hebrew start and a switch to English when a new participant joins, an empty compliment ("it would be nice if"), a term, a company name, a drift off topic. The demo uses only generated audio, never real recordings.
- Replay: `FileSource` plays the demo at real-time speed through the whole engine and the real provider.
- Live loopback: the "them" side plays through the Mac's output and is captured by catap exactly as in a call.
- Latency report per run: for each line `speech_end -> line_ready`, for each card `speech_end -> shown` (the app reports `shown`), median and worst. Targets: line within 1.5 s, card within 3 s, both median.
- Model comparison: the same demo with Haiku and with Sonnet; side by side cards, latency, planted moments caught, tokens.
- Watch mode check on a real public lecture or video, run by you.
- Clean install check: setup from an empty tools folder and an empty data folder.
- Sandbox note for development: `say`, Metal, Keychain and network fail inside the Claude Code sandbox; run those steps outside it before concluding something is broken.

## 18. Packaging and sharing

- Private GitHub repo on your personal account, committed with your personal noreply identity. Before every push, a scan of the tree and history for any email other than the noreply identity and for a list of forbidden words kept outside the repo. Nothing is pushed without your explicit OK.
- New Mac: clone, run `./setup.sh`, open the app, connect an account, optionally copy or sync the data folder.
- `setup.sh`: pinned versions with sha256 (like CallRec), builds `whisper-server` (cmake through uvx, as CallRec's build script) and the app locally. Local builds avoid the Gatekeeper prompt for an unsigned app.
- Friends: added as collaborators on the private repo; each connects their own subscription and fills their own `me.md`.
- Before moving to a new Mac: clean install check, then push with your OK.

## 19. Checkpoints

The plan stops for your review at:

1. Transcription only: live capture, segments, language switching, recording safety, latency stages 1 to 2 on the demo.
2. Agent and panel: cards, goals, actions, levels, watch mode, latency stage 3, Haiku vs Sonnet report.
3. Prep, summary, accounts, usage, setup.sh, clean install. End of stage 1: you try it on real calls and lectures.
4. Stage 2: entity cards.
5. Stage 3: joining an ongoing call with CallRec catch-up.

## 20. To verify at the start of the plan (spikes)

- Claude subscription auth for the Agent SDK in an isolated configuration, and how the app learns the connected account.
- Agent SDK options: system prompt without the Claude Code default, no tools, no setting sources, model per request, thinking or effort control, streaming session, usage fields, any rate limit or quota information.
- Time to first token for Haiku and Sonnet through the SDK with a cached prefix.
- whisper-server: language detection response, concurrent servers on Metal, request latency for 2 to 12 second segments on M4 Max.
- Availability of a quantized ivrit.ai model for the Light profile, or quantizing it at setup.
- catap `on_buffer` sample format and the resampling path to 16 kHz.
- NSPanel non-activating behavior with Zoom, Meet in a browser, and Slack huddles.
