# Record every call on its own, as CallRec does

Date: 2026-11-25. Asked for so that CallCoach can take over CallRec's job for the user: a call that starts is
recorded with nothing to press, and stops when it ends. The coach stays something he turns on by hand during the call.

## Decided with the user

- Off by default. A switch in the menu bar menu turns it on, exactly as in CallRec ("Start automatically when a call
  starts"): "הקלט שיחות אוטומטית" / "Record calls automatically", an item with a check mark.
- Live transcription in an automatic call, as in any "הקלטה בלבד" today. The coach can join at any moment with the
  whole transcript behind it, the captions are there when the panel is opened, and the final pass after the call works
  as it is (it finds its turns and their language from the live lines, so without them it would have nothing to send).
- No link to CallRec. While CallRec's own automatic start is on, both apps record each call; the user turns CallRec's
  off in its menu once CallCoach does the job. The guide says so in one line.
- A second switch in the same menu, "פתח בכניסה למחשב" / "Open at login", off by default, as in CallRec: automatic
  recording works only while CallCoach is open.
- A call that started on its own stops on its own when the call app lets go of the mic and the sound, even when the
  coach was turned on during it, and with no summary (a summary is written only when asked for: "עצור וסכם" before the
  end, or "צור סיכום" on the call page after it).
- The detection lives in the app (Swift), with CallRec's rules and the same Core Audio facts.

## Why it is built differently from CallRec

CallRec's menu bar app runs one Python process all day (`callrec`, the auto loop). That process is both the watcher
and the recorder: once a second it asks Core Audio (through catap) which apps use the mic and which play sound, starts
recording in the same process, and transcribes only after the call, in another process. While it waits it is light.

CallCoach's engine is different: it starts the Whisper servers the moment it starts and serves one call, so an engine
kept running all day to wait for a call would hold the GPU all day. So CallCoach keeps CallRec's rules and facts and
moves only the watching into the app; the engine starts when a call starts, as it does today.

Measured on 2026-11-25 (48 audio processes on the Mac): reading CallRec's way, every process's flags each second, costs
coreaudiod about 37 ms of work per second (about 4% of one core, all day). CallCoach reads a process's bundle id once
(it is kept by process object) and the two flags only of the processes of call apps (11 then), about 9 ms per second,
under 1% of one core. With the switch off nothing is read at all. The recording itself costs what a "הקלטה בלבד" costs
today (live Whisper during the call, the final pass after it).

## What he sees

- The menu bar menu has the two switches at all times, in and out of a call, above "הגדרות…".
- With the switch on, no call in progress and none starting: when a call app uses the mic for 3 seconds in a row, a
  "הקלטה בלבד" call starts. The call apps are CallRec's list (`MEETING_APPS`: Zoom, Teams, Slack, Chrome, Safari, Arc,
  Firefox, Edge, Brave, FaceTime, WhatsApp, Discord, Webex). Only an app that uses the mic counts, so a browser playing
  a video does not. WhatsApp also uses the mic alone to record a voice message, so it counts only while it plays sound
  too, as in a call.
- The panel does not show for such a call: the call is seen in the menu bar icon (the recording symbol) and in the
  main window's call card when the window is open. No notification at the start, as in CallRec. Showing the panel is
  the same as in any call (the menu, the hotkey, the main window).
- The call's title is what the home's field "על מה השיחה?" says, as in any "הקלטה בלבד", and the call app's name
  (for example "Zoom") when the field is empty.
- The coach is turned on by hand, as in any recording: same folder, same audio, same transcript.

## When it ends

- A call that started on its own stops when the app it started for has neither used the mic nor played sound for 5
  seconds (CallRec's `END_GRACE`), with no summary. The end notification is today's ("ההקלטה נשמרה").
- The engine's own stop after 5 minutes with no speech stays, for every call (CallRec's is 15 minutes of no sound;
  CallCoach keeps its own).
- A call he started by hand never stops because a call app ended, as in CallRec.
- After a call stopped by hand, or by the 5 minutes with no speech, while a call app is still on: no new automatic
  call until no call app uses the mic or plays sound (CallRec's "paused"). This keeps a stop from being undone 3 seconds
  later, and a silent waiting room from making a new folder every 5 minutes.
- After the engine fell in the middle of a call: today's recovery runs (the recording is finished in its folder and a
  notification says so). If the call app is still on, a new automatic call starts for the rest of the call, in a new
  folder.

## Open at login

- The switch registers the app as a login item with `SMAppService.mainApp`, the standard login item of macOS (it shows
  in System Settings, under Login Items). No background process is added.
- Opened as a login item, the app shows only its menu bar icon: the main window does not open. The app knows by the
  open event's "launched as a login item" flag; when the flag cannot be read, the window opens as it does today.

## Units

- `app/CallWatch.swift`:
  - `CallApps`: CallRec's list of bundle id prefixes and names, the voice message apps, and `name(bundle)`.
  - `AudioUse`: one call app's use of the audio now (name, mic, sound). `AudioUses.now()` reads Core Audio's process
    list, keeps each process object's bundle id, and reads the two flags of call apps only. It runs off the main
    thread.
  - `CallWatch`: the rules as a value with no clock or Core Audio of its own. Each second it gets the uses and what
    the app is doing (no call, a call it started, a call started by hand, starting) and answers start(app), stop or
    nothing. `ended(byWatch:crashed:)` tells it a call ended, for the pause rule. Self-checks drive it second by second.
- `AppState`: `autoRecord` and `openAtLogin` settings, the one second timer while the switch is on, `startAutoRecording
  (app:)` (a "הקלטה בלבד" with the panel hidden and the app's name as the title when the field gave none), and the stop
  with no summary when the watch says so. The model keeps `autoApp` for the call the watch started.
- `CallCoachApp.swift`: the two menu items with their check marks, and no main window when opened as a login item.
- Nothing changes in the engine or the protocol.

## Not in this change

- A warning when one side stays silent (CallRec warns after 90 s). With the panel hidden in an automatic call such a
  warning would have to be a notification. It concerns calls started by hand too, so it is a change of its own.
- Keeping the mic as it came beside the echo-cancelled track (CallRec cancels the echo after the call from raw
  tracks). It concerns every call, so it is a change of its own.
- Any link with CallRec (turning its automatic start off, or skipping a call it records).
