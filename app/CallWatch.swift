import CoreAudio
import Foundation
import ServiceManagement

/// CallRec's call apps: an app whose use of the mic means a call is on, by the prefix of its bundle id (so a browser's
/// helper processes count as the browser), and the name it goes by.
enum CallApps {
    static let byPrefix: [(prefix: String, name: String)] = [
        ("us.zoom.xos", "Zoom"), ("com.microsoft.teams", "Teams"), ("com.tinyspeck.slackmacgap", "Slack"),
        ("com.google.Chrome", "Chrome"), ("com.apple.WebKit", "Safari"), ("com.apple.Safari", "Safari"),
        ("company.thebrowser", "Arc"), ("org.mozilla.firefox", "Firefox"), ("com.microsoft.edgemac", "Edge"),
        ("com.brave.Browser", "Brave"), ("com.apple.FaceTime", "FaceTime"), ("com.apple.avconferenced", "FaceTime"),
        ("net.whatsapp.WhatsApp", "WhatsApp"), ("com.hnc.Discord", "Discord"), ("com.cisco.webex", "Webex"),
        ("Cisco-Systems.Spark", "Webex"),
    ]
    /// Apps that also take the mic alone to record a voice message: they count as a call only while they play sound too.
    static let voiceMessages: Set<String> = ["WhatsApp"]

    static func name(_ bundle: String) -> String? {
        byPrefix.first { bundle.hasPrefix($0.prefix) }?.name
    }
}

/// One call app's use of the audio now, all its processes together: whether it takes the mic and whether it plays sound.
struct AudioUse: Equatable {
    let app: String
    var mic: Bool
    var sound: Bool
}

/// Reads from Core Audio's process objects which call apps take the mic and which play sound, as CallRec does through
/// catap. A process's bundle id is read once and kept by its object, and the two flags are read only for call apps, so
/// a read costs coreaudiod about a quarter of what reading every process does. Used from one queue only.
final class AudioUses {
    private var bundles: [AudioObjectID: String] = [:]

    func now() -> [AudioUse] {
        let ids = Self.processes()
        let alive = Set(ids)
        bundles = bundles.filter { alive.contains($0.key) }
        var uses: [String: AudioUse] = [:]
        for id in ids {
            let bundle = bundles[id] ?? Self.bundle(id)
            if !bundle.isEmpty { bundles[id] = bundle }
            guard let app = CallApps.name(bundle) else { continue }
            var use = uses[app] ?? AudioUse(app: app, mic: false, sound: false)
            use.mic = use.mic || Self.flag(id, kAudioProcessPropertyIsRunningInput)
            use.sound = use.sound || Self.flag(id, kAudioProcessPropertyIsRunningOutput)
            uses[app] = use
        }
        return uses.values.sorted { $0.app < $1.app }
    }

    private static func address(_ selector: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                                   mElement: kAudioObjectPropertyElementMain)
    }

    private static func processes() -> [AudioObjectID] {
        var where_ = address(kAudioHardwarePropertyProcessObjectList)
        let system = AudioObjectID(kAudioObjectSystemObject)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(system, &where_, 0, nil, &size) == noErr, size > 0 else { return [] }
        var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard AudioObjectGetPropertyData(system, &where_, 0, nil, &size, &ids) == noErr else { return [] }
        return Array(ids.prefix(Int(size) / MemoryLayout<AudioObjectID>.size))
    }

    private static func bundle(_ id: AudioObjectID) -> String {
        var where_ = address(kAudioProcessPropertyBundleID)
        var value: Unmanaged<CFString>?
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        guard AudioObjectGetPropertyData(id, &where_, 0, nil, &size, &value) == noErr,
              let text = value?.takeRetainedValue() else { return "" }
        return text as String
    }

    private static func flag(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector) -> Bool {
        var where_ = address(selector)
        var value: UInt32 = 0
        var size = UInt32(MemoryLayout<UInt32>.size)
        return AudioObjectGetPropertyData(id, &where_, 0, nil, &size, &value) == noErr && value != 0
    }
}

/// CallRec's rules for "הקלט שיחות אוטומטית", once a second, with no clock or Core Audio of its own. With no call:
/// a call app on the mic for `startAfter` seconds in a row starts one. In a call the watch started: once that app has
/// neither taken the mic nor played sound for more than `endGrace` seconds, the call stops. A call started by hand, or
/// one that is starting or stopping, is left alone. A call that ended by hand or by itself while a call app was on
/// pauses the watch until no call app takes the mic or plays sound, so the stop is not undone a moment later.
struct CallWatch {
    /// What the app is doing: no call, a call it is busy with (by hand, starting or stopping), or a call the watch
    /// started for an app.
    enum Doing: Equatable { case idle, busy, auto(String) }
    enum Step: Equatable { case none, start(String), stop }

    static let startAfter = 3.0
    static let endGrace = 5.0

    private(set) var paused = false
    private var since: Double?
    private var lastActive: Double?

    mutating func step(_ uses: [AudioUse], doing: Doing, now: Double) -> Step {
        switch doing {
        case .busy:
            since = nil
            lastActive = nil
            return .none
        case .auto(let app):
            since = nil
            if lastActive == nil || uses.contains(where: { $0.app == app && ($0.mic || $0.sound) }) { lastActive = now }
            return now - (lastActive ?? now) > Self.endGrace ? .stop : .none
        case .idle:
            lastActive = nil
            if paused && !uses.contains(where: { $0.mic || $0.sound }) { paused = false }
            guard !paused, let app = Self.callApp(uses) else {
                since = nil
                return .none
            }
            if let since, now - since >= Self.startAfter {
                self.since = nil
                return .start(app)
            }
            since = since ?? now
            return .none
        }
    }

    /// A call ended: `pause` when it ended by hand or by itself, not when the watch stopped it or the engine fell.
    mutating func ended(pause: Bool) {
        paused = pause
        since = nil
        lastActive = nil
    }

    /// The call app on the mic now, the first by name when there are two.
    static func callApp(_ uses: [AudioUse]) -> String? {
        uses.filter { $0.mic && (!CallApps.voiceMessages.contains($0.app) || $0.sound) }.map(\.app).sorted().first
    }
}

/// "פתח בכניסה למחשב": the app as the standard login item of macOS, shown in System Settings under Login Items.
enum LoginItem {
    static var on: Bool { SMAppService.mainApp.status == .enabled }

    /// Turns it on or off; the error's text when macOS refused.
    static func set(_ on: Bool) -> String? {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            return nil
        } catch {
            return error.localizedDescription
        }
    }
}

extension SelfCheck {
    static func watchChecks(_ root: URL) -> [String] {
        var p: [String] = []
        let zoom = [AudioUse(app: "Zoom", mic: true, sound: true)]
        let zoomSoundOnly = [AudioUse(app: "Zoom", mic: false, sound: true)]
        let nothing: [AudioUse] = []
        func run(_ watch: inout CallWatch, _ seconds: ClosedRange<Int>, _ uses: [AudioUse],
                 _ doing: CallWatch.Doing) -> [CallWatch.Step] {
            seconds.map { watch.step(uses, doing: doing, now: Double($0)) }
        }

        var w = CallWatch()
        expect(run(&w, 0...2, zoom, .idle) == [.none, .none, .none] && w.step(zoom, doing: .idle, now: 3) == .start("Zoom"),
               "three seconds of Zoom on the mic start a call", &p)
        var video = CallWatch()
        expect(!run(&video, 0...10, [AudioUse(app: "Chrome", mic: false, sound: true)], .idle).contains(.start("Chrome")),
               "a browser that only plays sound is no call", &p)
        var voice = CallWatch()
        expect(!run(&voice, 0...10, [AudioUse(app: "WhatsApp", mic: true, sound: false)], .idle).contains(.start("WhatsApp")),
               "WhatsApp on the mic alone is a voice message", &p)
        expect(run(&voice, 11...14, [AudioUse(app: "WhatsApp", mic: true, sound: true)], .idle).last == .start("WhatsApp"),
               "WhatsApp on the mic and playing sound is a call", &p)
        var gap = CallWatch()
        _ = run(&gap, 0...1, zoom, .idle)
        _ = gap.step(nothing, doing: .idle, now: 2)
        expect(run(&gap, 3...5, zoom, .idle) == [.none, .none, .none] && gap.step(zoom, doing: .idle, now: 6) == .start("Zoom"),
               "a second off the mic counts the three seconds again", &p)
        expect(CallWatch.callApp([AudioUse(app: "Zoom", mic: true, sound: false), AudioUse(app: "Chrome", mic: true, sound: true)])
               == "Chrome", "two call apps on the mic: the first by name", &p)

        var call = CallWatch()
        expect(run(&call, 0...20, zoom, .auto("Zoom")).allSatisfy { $0 == .none }, "a call goes on while Zoom has the mic", &p)
        expect(run(&call, 21...30, zoomSoundOnly, .auto("Zoom")).allSatisfy { $0 == .none },
               "and while Zoom only plays sound, as when it is muted", &p)
        expect(run(&call, 31...35, [AudioUse(app: "Chrome", mic: true, sound: true)], .auto("Zoom")).allSatisfy { $0 == .none }
               && call.step(nothing, doing: .auto("Zoom"), now: 36) == .stop,
               "once Zoom has neither for more than five seconds the call stops, another app on the mic or not", &p)
        var late = CallWatch()
        expect(late.step(nothing, doing: .auto("Zoom"), now: 100) == .none && late.step(nothing, doing: .auto("Zoom"), now: 105) == .none
               && late.step(nothing, doing: .auto("Zoom"), now: 106) == .stop, "the five seconds count from the call's first second", &p)

        var hand = CallWatch()
        expect(run(&hand, 0...20, zoom, .busy).allSatisfy { $0 == .none } && run(&hand, 21...40, nothing, .busy).allSatisfy { $0 == .none },
               "a call by hand, or one starting, is left alone", &p)
        expect(hand.step(zoom, doing: .idle, now: 41) == .none, "and the three seconds start after it", &p)

        var stopped = CallWatch()
        stopped.ended(pause: true)
        expect(run(&stopped, 0...10, zoom, .idle).allSatisfy { $0 == .none } && stopped.paused,
               "after a stop by hand Zoom still on starts nothing", &p)
        _ = run(&stopped, 11...12, [AudioUse(app: "Chrome", mic: false, sound: true)], .idle)
        expect(stopped.paused, "a browser still playing sound keeps the pause", &p)
        _ = stopped.step(nothing, doing: .idle, now: 13)
        expect(!stopped.paused && run(&stopped, 14...17, zoom, .idle).last == .start("Zoom"),
               "a second with no call app on the mic or the sound ends the pause, and the next call starts", &p)
        var fell = CallWatch()
        fell.ended(pause: false)
        expect(run(&fell, 0...3, zoom, .idle).last == .start("Zoom"), "after the engine fell a call still on starts again", &p)

        expect(CallApps.name("com.google.Chrome.helper") == "Chrome" && CallApps.name("us.zoom.xos") == "Zoom"
               && CallApps.name("com.apple.Music") == nil && CallApps.name("") == nil, "the call apps by bundle id", &p)
        return p
    }

    static func autoChecks(_ root: URL) -> [String] {
        var p: [String] = []
        let fm = FileManager.default
        let tools = fm.temporaryDirectory.appendingPathComponent("callcoach-auto-\(getpid())")
        try? fm.createDirectory(at: tools, withIntermediateDirectories: true)
        let suite = tools.appendingPathComponent("settings").path
        let defaults = UserDefaults(suiteName: suite)!
        defer {
            defaults.removePersistentDomain(forName: suite)
            try? fm.removeItem(at: tools)
        }
        let state = AppState(accounts: AccountStore(tools: tools), stream: nil, speed: 1)
        state.data = tools.appendingPathComponent("data")
        state.defaults = defaults
        state.askMicrophone = { $0() }
        state.readUses = { _ in }
        var t = 0.0
        state.clock = { t }
        var channels: [FakeChannel] = []
        var fatals: [String] = []
        state.onFatal = { fatals.append($0) }
        var engineFails = false
        state.makeChannel = {
            if engineFails { throw CocoaError(.fileNoSuchFile) }
            let channel = FakeChannel()
            channels.append(channel)
            return channel
        }
        let zoom = [AudioUse(app: "Zoom", mic: true, sound: true)]
        func seconds(_ n: Int, _ uses: [AudioUse]) {
            for _ in 0..<n {
                state.watchTick(uses)
                t += 1
            }
        }
        func starts(_ channel: FakeChannel?) -> [StartFields] {
            (channel?.sent ?? []).compactMap { if case .start(let f, _) = $0 { return f } else { return nil } }
        }
        func stops(_ channel: FakeChannel) -> [Command] { channel.sent.filter { if case .stop = $0 { return true } else { return false } } }
        func ready(_ channel: FakeChannel) {
            channel.emit(#"{"type": "ready", "folder": "/x/calls/2026-11-25_1000_general", "replay_t0": null}"#)
        }
        let stopped = #"{"type": "stopped"}"#

        expect(!Settings.autoRecord(defaults) && !state.watchWanted, "off by default, so nothing is read", &p)
        seconds(5, zoom)
        expect(channels.isEmpty, "with the switch off a call app starts nothing", &p)
        state.toggleAutoRecord()
        expect(Settings.autoRecord(defaults) && state.watchWanted, "the menu turns it on", &p)
        seconds(3, zoom)
        expect(channels.isEmpty, "nothing before three seconds", &p)
        seconds(1, zoom)
        let first = starts(channels.first).first
        expect(channels.count == 1 && first?.recordOnly == true && first?.title == "Zoom" && first?.brief == ""
               && state.model.autoApp == "Zoom" && !state.model.visible && state.model.folded,
               "three seconds of Zoom start a recording named Zoom, with the panel hidden \(String(describing: first))", &p)
        ready(channels[0])
        expect(state.model.phase == .active && channels[0].sent.last == .setVisibility(false)
               && !AppDelegate.panelShown(phase: state.model.phase, visible: state.model.visible, idle: state.idlePanel),
               "the panel does not show and the engine hears it is not seen", &p)
        state.toggleCoach()
        expect(state.model.chips.coach, "the coach is turned on by hand in the same call", &p)
        seconds(2, zoom)
        seconds(5, [])
        expect(stops(channels[0]).isEmpty, "five seconds without Zoom do not stop it yet", &p)
        seconds(1, [])
        expect(stops(channels[0]) == [.stop(summary: false)], "more than five stop it with no summary, coach or not", &p)
        seconds(3, [])
        expect(stops(channels[0]).count == 1, "the stop is sent once", &p)
        channels[0].emit(stopped)
        state.idlePanel = true
        seconds(4, zoom)
        expect(channels.count == 2 && starts(channels[1]).first?.title == "Zoom" && state.model.visible,
               "the next call starts as the first did, and a panel open already stays", &p)
        state.idlePanel = false

        ready(channels[1])
        state.stop()
        channels[1].emit(stopped)
        seconds(6, zoom)
        expect(channels.count == 2, "after a stop by hand Zoom still on starts nothing", &p)
        seconds(1, [])
        seconds(4, zoom)
        expect(channels.count == 3, "once Zoom let go, its next call is recorded", &p)
        ready(channels[2])
        state.stop()
        channels[2].emit(stopped)
        seconds(1, [])

        state.startRecording()
        expect(channels.count == 4 && state.model.autoApp == nil && state.model.visible && starts(channels[3]).first?.title == "",
               "a recording by hand has the panel and no call app", &p)
        ready(channels[3])
        seconds(10, [])
        seconds(5, zoom)
        expect(stops(channels[3]).isEmpty && channels.count == 4, "the watch leaves a call by hand alone", &p)
        channels[3].emit(stopped)
        seconds(1, [])

        state.topic = "דנה מ-Acme"
        seconds(4, zoom)
        expect(starts(channels[4]).first?.title == "דנה מ-Acme", "the field's text is the title when there is one", &p)
        ready(channels[4])
        state.toggleAutoRecord()
        expect(!Settings.autoRecord(defaults) && state.watchWanted, "turned off in a call it started, the watch still runs", &p)
        seconds(2, zoom)
        seconds(6, [])
        expect(stops(channels[4]) == [.stop(summary: false)], "so that call still ends with Zoom", &p)
        channels[4].emit(stopped)
        expect(!state.watchWanted, "after it nothing is read", &p)

        state.toggleAutoRecord()
        engineFails = true
        seconds(4, zoom)
        seconds(10, zoom)
        expect(fatals.count == 1 && channels.count == 5, "an engine that does not start is told once, not every three seconds", &p)
        return p
    }
}
