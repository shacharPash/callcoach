# Warn when a side of the call is not being recorded

Date: 2026-11-26. CallRec warns when one side of a call is not being recorded; CallCoach did not. It matters most for
an automatic call ("הקלט שיחות אוטומטית"), which records with the panel hidden and no one looking: a missing permission
would leave an hour of call with one side only, found out after the call.

## Decided with the user

- It catches a recording failure, as CallRec's `Health` does, not people who are quiet. The user asked to keep it
  small and light: if he tries to record and it does not record, he should know; the exact moment matters little.
- Every call, started by hand or on its own.
- Shown by where the panel is: a line in the panel while the problem holds and a triangle in the menu bar icon; a
  macOS notification only when the panel is not on screen, so a call with the panel open gets no double signal.
- It goes away by itself when the side is heard again, the notification included (taken back from the Notification
  Center), as in CallRec since its commit 7ce3063.
- The thresholds were left to this design.

## What counts as missing

- **My side:** 15 seconds in a row of pure digital zero on the microphone (CallRec's rule). A real microphone always
  hears some noise, so this is a missing Microphone permission or a dead device.
- **The other side:** nothing at all from it after 90 seconds of the call (CallRec's rule), and only once CallCoach
  heard me talk for 15 seconds. System audio is digital zero whenever nothing plays, and an automatic call starts the
  moment Zoom takes the mic, sometimes in a waiting room before anyone joined; CallRec's plain 90 seconds would warn in
  every such room. Waiting for my talk costs one condition. The price: in a call where I only listen, a missing
  permission is found once I speak.
- Once the other side was heard it is never missing again in that call (CallRec's rule). A tap that stops delivering
  mid-call is already restarted and reported by `SystemSource` (`capture`).
- Silence the user asked for is never a warning: while the recording is paused or CallCoach does not hear me, my side
  is not looked at and its 15 seconds start again; paused audio of the other side does not count as heard.

## How it is built

- `engine/callcoach/silence.py`, `SilentSides`: fed every chunk in `CallEngine._handle_chunk`, before the pause and
  the hearing switch replace it with silence, with whether it is heard. A chunk is zero when all its bytes are zero
  (`bytes.count`, no numpy). Times are the chunks' own, so a fast replay gets the same answer as the call.
  `sides(my_speech_s)` gives the missing sides, `me` first; the engine passes `voiced[ME]`, which already counts only
  heard speech.
- `status` gets `silent`, a list, every second. No new event: the app reads the state, so clearing is the same as
  setting, and a later banner of another kind cannot hide it for good. The idle status sends `[]`. Protocol stays 6
  (an older app ignores the field, the app reads a status without it as none). Each change is a note `silent` in
  `events.log`.
- The microphone is looked at after CallCoach's echo canceller, which is what gets recorded. WebRTC's AEC3 adds
  comfort noise where it suppresses, so it does not turn room noise into 15 seconds of exact zero; this was not
  measured on a live call.
- App: `Status.silent` (missing means `[]`); `app/SilentSide.swift` holds the texts, the panel's line
  (`SilentSide.notice`, orange, above the red banner, folded panel included) and the rule for notifications
  (`SilentSide.alerts`: post a side newly missing when the panel is not on screen, take back a side heard again, and
  nothing when the call ends, so what was still missing stays in the Notification Center). `CallIcon.warning`
  ("exclamationmark.triangle.fill") wins over the recording and coach icons while a side is missing.
  `AppDelegate.sync` keeps the last list and sends or removes `UNNotificationRequest`s with the identifiers
  `silent me` and `silent them`; a click on one shows the panel.

## Not done

- No warning for a side that goes quiet after it was heard, and no warning in the call page or the main window's call
  card: the panel, the icon and the notification are enough for "it is not recording".
- No setting to turn it off.

## Checks

- `tests/test_silence.py`: the 15 seconds of the mic, sound resetting them, hearing off, the 90 seconds with and
  without my talk, the other side heard once, a paused other side, a dead mic.
- `tests/test_engine.py`: a call whose other side is all zero ends with `silent: ["them"]` in its last status, and a
  call with both sides heard never has a side in it.
- Self-check group "silent side": decoding with and without the field, the banner and the triangle on and off, an
  ended call showing nothing, and each case of the notification rule.
