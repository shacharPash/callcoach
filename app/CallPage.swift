import AppKit
import AVFoundation
import Combine
import SwiftUI

/// One line of the call's transcript, timed from the start of the recording.
struct CallLine: Identifiable, Equatable {
    let id: Int
    let channel: String
    let text: String
    let time: Double
}

enum CallTranscript {
    private struct Entry: Decodable {
        let kind: String?
        let channel: String?
        let text: String?
        let tStart: Double?
        let t: Double?

        enum CodingKeys: String, CodingKey {
            case kind, channel, text, t
            case tStart = "t_start"
        }
    }

    /// The lines of lines.jsonl by their start, each at `t_start` minus `started`; a line that does not read is
    /// skipped. Records are split on "\n" only and, without `started`, the earliest record is the start, as in the engine.
    static func parse(_ text: String, started: Double?) -> [CallLine] {
        let decoder = JSONDecoder()
        let records = text.utf8.split(separator: UInt8(ascii: "\n")).compactMap { try? decoder.decode(Entry.self, from: Data($0)) }
        let entries = records.compactMap { entry -> (channel: String, text: String, start: Double)? in
            guard entry.kind == "line", let channel = entry.channel, let start = entry.tStart, start.isFinite else { return nil }
            let text = (entry.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            return text.isEmpty ? nil : (channel, text, start)
        }
        let zero = started ?? records.compactMap { $0.kind == "line" ? $0.tStart : $0.t }.filter(\.isFinite).min() ?? 0
        return entries.enumerated()
            .sorted { ($0.element.start, $0.offset) < ($1.element.start, $1.offset) }
            .enumerated()
            .map { CallLine(id: $0.offset, channel: $0.element.element.channel, text: $0.element.element.text,
                            time: max(0, $0.element.element.start - zero)) }
    }

    static func load(_ folder: URL) -> [CallLine] {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("lines.jsonl")) else { return [] }
        return parse(String(decoding: data, as: UTF8.self), started: CallLibrary.meta(folder)["started"] as? Double)
    }

    /// m:ss, and h:mm:ss from an hour.
    static func clock(_ seconds: Double) -> String {
        let s = seconds.isFinite ? Int(max(0, seconds)) : 0
        return s >= 3600 ? String(format: "%d:%02d:%02d", s / 3600, s % 3600 / 60, s % 60)
            : String(format: "%d:%02d", s / 60, s % 60)
    }

    static func speaker(_ channel: String) -> String {
        channel == "me" ? L("אני", "Me") : L("הצד השני", "Them")
    }

    /// The transcript as text to paste: a line for each line, the speaker first and the time after it, so a Hebrew
    /// line starts with a Hebrew word.
    static func text(_ lines: [CallLine]) -> String {
        lines.map { "\(speaker($0.channel)) (\(clock($0.time))): \($0.text)" }.joined(separator: "\n")
    }

    /// Puts the transcript on the clipboard; false when it has no lines.
    @discardableResult
    static func copy(_ lines: [CallLine], to board: NSPasteboard = .general) -> Bool {
        guard !lines.isEmpty else { return false }
        board.clearContents()
        return board.setString(text(lines), forType: .string)
    }

    /// The line being played: the last one that started by the position, once playback has begun.
    static func current(_ lines: [CallLine], at position: Double, begun: Bool) -> Int? {
        guard begun else { return nil }
        return lines.last { $0.time <= position + 0.3 }?.id
    }
}

/// The chosen call's recording, played as it is: me on the left, the other side on the right, as in CallRec.
final class CallPlayer: ObservableObject {
    static let rates: [Float] = [1, 1.25, 1.5, 2]
    @Published private(set) var playing = false
    @Published private(set) var position = 0.0
    @Published private(set) var duration = 0.0
    @Published var rate: Float = 1 {
        didSet { player?.rate = rate }
    }
    /// The recording that opened, and the one that did not.
    @Published private(set) var file: URL?
    @Published private(set) var failed: URL?
    /// Nothing plays while a call starts or runs.
    @Published var locked = false {
        didSet { if locked { pause() } }
    }
    private var player: AVAudioPlayer?
    private var timer: Timer?

    var ready: Bool { player != nil && !locked }

    var note: String? {
        if failed != nil { return L("לא הצלחתי לפתוח את ההקלטה", "Could not open the recording") }
        return locked ? L("הנגן כבוי בזמן שיחה", "Playback is off during a call") : nil
    }

    deinit {
        timer?.invalidate()
    }

    /// Another file, or none, stops the one playing.
    func open(_ file: URL?) {
        guard file != (self.file ?? failed) else { return }
        player?.stop()
        player = nil
        self.file = nil
        failed = nil
        if let file {
            if let next = try? AVAudioPlayer(contentsOf: file) {
                next.enableRate = true
                next.rate = rate
                next.prepareToPlay()
                player = next
                self.file = file
            } else {
                failed = file
            }
        }
        duration = player?.duration ?? 0
        sync()
    }

    func toggle() {
        if playing { pause() } else { play() }
    }

    func play() {
        guard !locked else { return }
        player?.play()
        sync()
    }

    func pause() {
        player?.pause()
        sync()
    }

    func seek(_ time: Double, play: Bool) {
        guard let player, !locked else { return }
        player.currentTime = max(0, min(time, player.duration))
        if play { player.play() }
        sync()
    }

    func nextRate() {
        let i = Self.rates.firstIndex(of: rate) ?? 0
        rate = Self.rates[(i + 1) % Self.rates.count]
    }

    static func rateText(_ rate: Float) -> String {
        String(format: "%g", rate) + "×"
    }

    /// The position follows the recording four times a second while it plays.
    private func sync() {
        playing = player?.isPlaying ?? false
        position = player?.currentTime ?? 0
        if playing && timer == nil {
            let timer = Timer(timeInterval: 0.25, repeats: true) { [weak self] _ in self?.sync() }
            RunLoop.main.add(timer, forMode: .common)
            self.timer = timer
        } else if !playing {
            timer?.invalidate()
            timer = nil
        }
    }
}

/// A call in the main window: its name, its recording, and its summary or transcript.
struct CallPage: View {
    enum Tab: String {
        case summary, transcript, names

        func label(names: Int) -> String {
            switch self {
            case .summary: return L("סיכום", "Summary")
            case .transcript: return L("תמלול", "Transcript")
            case .names: return L("שמות חדשים (\(names))", "New names (\(names))")
            }
        }

        /// "שמות חדשים" only when the call has new names.
        static func shown(names: Int) -> [Tab] {
            names > 0 ? [.summary, .transcript, .names] : [.summary, .transcript]
        }
    }

    let call: CallEntry
    /// Not observed here: the bar and the transcript follow it, so the summary is not read again as it plays.
    let player: CallPlayer
    /// Not observed here either; the follow-up email follows it.
    let state: AppState
    /// Changes when the main window reloads its calls, so the transcript of a call in progress is read again.
    var refresh = 0
    var renamed: () -> Void = {}
    /// The title of the brief the call used, when it is still there; nil also on the brief's own page.
    var brief: String?
    var openBrief: () -> Void = {}
    var followUp: (() -> Void)?
    @ViewState private var tab: Tab
    @ViewState private var lines: [CallLine] = []
    @ViewState private var names: [CallTerm] = []
    @ViewState private var editing = false
    @ViewState private var name = ""
    @ViewState private var failure: String?
    @FocusState private var focused: Bool
    @Environment(\.layoutDirection) private var direction

    init(call: CallEntry, player: CallPlayer, state: AppState, refresh: Int = 0, renamed: @escaping () -> Void = {},
         brief: String? = nil, openBrief: @escaping () -> Void = {}, followUp: (() -> Void)? = nil) {
        self.call = call
        self.player = player
        self.state = state
        self.refresh = refresh
        self.renamed = renamed
        self.brief = brief
        self.openBrief = openBrief
        self.followUp = followUp
        _tab = ViewState(initialValue: call.hasSummary ? .summary : .transcript)
    }

    /// The button that opens the prep sheet for the next call, with this call's summary and brief attached.
    static var followUpTitle: String { L("הכן תדריך לשיחת המשך…", "Prepare a follow-up brief…") }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            header
            details
            if let joined = Self.coachJoined(recordOnly: call.recordOnly, coachFrom: call.coachFrom) {
                Text(joined).font(.system(size: 12)).foregroundStyle(.secondary).monospacedDigit()
            }
            if let failure { DirectedText(failure, size: 12).foregroundStyle(.orange) }
            if call.hasAudio { PlayerBar(player: player).padding(.top, 10) }
            Picker(L("תצוגה", "View"), selection: $tab) {
                ForEach(inReadingOrder(Tab.shown(names: names.count)), id: \.self) { Text($0.label(names: names.count)).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .fixedSize()
            .padding(.top, 12)
            .padding(.bottom, 4)
            if tab == .transcript && !lines.isEmpty { CopyTranscriptButton(lines: lines) }
            switch tab {
            case .summary: summary
            case .transcript:
                TranscriptList(lines: lines, player: player, audio: call.hasAudio ? call.audio : nil)
            case .names:
                NewNamesView(call: call, names: names, data: Paths.data, refresh: refresh)
            }
        }
        .padding(20)
        .task(id: "\(call.id) \(refresh)") {
            lines = CallTranscript.load(call.folder)
            names = NewNames.load(call.folder)
            if tab == .names && names.isEmpty { tab = .summary }
        }
    }

    @ViewBuilder private var header: some View {
        if editing {
            HStack(spacing: 8) {
                field
                Button(L("שמור", "Save"), action: save)
                Button(L("ביטול", "Cancel")) { editing = false }.keyboardShortcut(.cancelAction)
            }
        } else {
            HStack(spacing: 12) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(TextDirection.marked(call.title)).font(.system(size: 20, weight: .semibold)).lineLimit(1)
                    Button(action: rename) { Image(systemName: "pencil") }
                        .buttonStyle(.borderless)
                        .accessibilityLabel(L("שנה שם", "Rename"))
                }
                Spacer(minLength: 0)
                HStack(spacing: 8) {
                    Button { NSWorkspace.shared.open(call.folder) } label: { Label(L("פתח תיקייה", "Open folder"), systemImage: "folder") }
                    if let followUp {
                        Button(action: followUp) { Label(Self.followUpTitle, systemImage: "sparkles") }
                    }
                }
                .controlSize(.small)
                .fixedSize()
            }
        }
    }

    /// The one line under the name, in the interface's direction even when it is only a date, with the brief the call
    /// used as a link at its end.
    private var details: some View {
        HStack(spacing: 4) {
            Text((direction == .rightToLeft ? "\u{200F}" : "") + Self.details(call))
                .foregroundStyle(.secondary).monospacedDigit().fixedSize()
            if let brief {
                Text("·").foregroundStyle(.secondary)
                Button(action: openBrief) { Text(L("תדריך: ", "Brief: ") + TextDirection.marked(brief)).lineLimit(1) }
                    .buttonStyle(.link)
            }
        }
    }

    /// When the call was, its type when it has a name of its own, and "הקלטה בלבד" for a recording with no summary
    /// that the coach never joined.
    static func details(_ call: CallEntry, now: Date = Date()) -> String {
        let type = call.name != nil && call.kind != "general" ? call.defaultTitle : nil
        return [CallLibrary.dayTitle(call.started, now: now) + " " + CallLibrary.time.string(from: call.started), type,
                call.onlyRecording ? L("הקלטה בלבד", "Recording only") : nil].compactMap { $0 }.joined(separator: " · ")
    }

    /// The name, on the right in Hebrew: a text field keeps its text on the left in a right-to-left layout, so it is
    /// laid out left to right and aligned to its trailing side.
    private var field: some View {
        TextField(call.defaultTitle, text: $name)
            .textFieldStyle(.roundedBorder)
            .font(.system(size: 15))
            .multilineTextAlignment(direction == .rightToLeft ? .trailing : .leading)
            .environment(\.layoutDirection, .leftToRight)
            .frame(maxWidth: 340)
            .focused($focused)
            .onSubmit(save)
            .onAppear { focused = true }
    }

    /// "המאמן הצטרף בדקה 12" for a call that started as a recording and had the coach later; the minute counts from 1.
    static func coachJoined(recordOnly: Bool, coachFrom: Double?) -> String? {
        guard recordOnly, let from = coachFrom, from.isFinite, from > 0 else { return nil }
        let minute = Int(from / 60) + 1
        return L("המאמן הצטרף בדקה \(minute)", "The coach joined in minute \(minute)")
    }

    @ViewBuilder private var summary: some View {
        if let error = call.exportError {
            DirectedText(L("ההעתקה לתיקייה שנבחרה בהגדרות נכשלה: \(error)",
                           "The copy to the folder chosen in Settings failed: \(error)"), size: 12)
                .foregroundStyle(.orange).padding(.top, 8)
        }
        if call.hasSummary {
            SummaryView(summary: SummaryReady(ok: true, path: call.summary.path, message: ""), finalized: nil, notes: [],
                        padded: false, followUp: call.lecture ? nil : FollowUpSource(call: call, state: state))
                .padding(.top, 4)
            if call.localSummary {
                DirectedText(L("המודל לא ענה בסוף השיחה, אז הסיכום הזה נכתב מקומית.",
                               "The model did not answer at the end of the call, so this summary was written locally."),
                             size: 12).foregroundStyle(.secondary).padding(.top, 8)
                SummaryMaker(call: call, state: state, title: L("צור סיכום מלא", "Write a full summary"), done: renamed)
            }
        } else {
            Text(L("לשיחה הזאת אין סיכום.", "This call has no summary.")).foregroundStyle(.secondary).padding(.top, 8)
            SummaryMaker(call: call, state: state, done: renamed)
            Spacer()
        }
    }

    private func rename() {
        name = call.title
        failure = nil
        editing = true
    }

    /// The label of the call type, typed back or left empty, is no name.
    private func save() {
        let value = NewNames.clean(name)
        do {
            try CallLibrary.rename(call.folder, to: value == call.defaultTitle ? "" : value)
            failure = nil
            editing = false
            renamed()
        } catch {
            failure = L("השם לא נשמר: \(error.localizedDescription)", "The name was not saved: \(error.localizedDescription)")
        }
    }
}

/// Play and pause, the position and the speed. The controls keep their left-to-right order in Hebrew too, as media
/// controls do. A line under them says why they are off.
struct PlayerBar: View {
    @ObservedObject var player: CallPlayer

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            controls
            if let note = player.note {
                DirectedText(note, size: 12).foregroundStyle(player.failed == nil ? Color.secondary : .orange)
            }
        }
    }

    private var controls: some View {
        HStack(spacing: 10) {
            Button { player.toggle() } label: {
                Image(systemName: player.playing ? "pause.fill" : "play.fill").frame(width: 16)
            }
            .accessibilityLabel(player.playing ? L("השהה", "Pause") : L("נגן", "Play"))
            .disabled(!player.ready)
            Text(CallTranscript.clock(player.position)).monospacedDigit().foregroundStyle(.secondary)
            Slider(value: Binding(get: { player.position }, set: { player.seek($0, play: false) }),
                   in: 0...max(player.duration, 1))
                .disabled(!player.ready)
            Text(CallTranscript.clock(player.duration)).monospacedDigit().foregroundStyle(.secondary)
            Button { player.nextRate() } label: {
                Text(CallPlayer.rateText(player.rate)).monospacedDigit()
            }
            .buttonStyle(.borderless)
            .fixedSize()
            .accessibilityLabel(L("מהירות", "Speed"))
            .accessibilityValue(CallPlayer.rateText(player.rate))
            .accessibilityHint(L("לחיצה עוברת למהירות הבאה", "A click goes to the next speed"))
        }
        .environment(\.layoutDirection, .leftToRight)
    }
}

/// "העתק את התמלול" over the transcript; for a moment after a click it says it was copied.
struct CopyTranscriptButton: View {
    let lines: [CallLine]
    @ViewState private var copied = false

    var body: some View {
        Button {
            copied = CallTranscript.copy(lines)
            Task {
                try? await Task.sleep(for: .seconds(2))
                copied = false
            }
        } label: {
            Label(copied ? L("הועתק", "Copied") : L("העתק את התמלול", "Copy the transcript"),
                  systemImage: copied ? "checkmark" : "doc.on.doc")
        }
        .controlSize(.small)
    }
}

/// Every line of the call by its time. The time and the speaker keep the interface's side on every row, and the text
/// keeps its own direction next to the speaker. A click plays the recording from the line, and the line being played
/// is tinted.
struct TranscriptList: View {
    let lines: [CallLine]
    @ObservedObject var player: CallPlayer
    /// The call's recording, if it has one.
    let audio: URL?
    @Environment(\.layoutDirection) private var direction

    private var mine: Bool { audio != nil && player.file == audio }
    private var playable: Bool { mine && player.ready }
    private var current: Int? {
        CallTranscript.current(lines, at: player.position, begun: mine && (player.playing || player.position > 0))
    }

    var body: some View {
        if lines.isEmpty {
            Text(L("לשיחה הזאת אין תמלול.", "This call has no transcript.")).foregroundStyle(.secondary).padding(.top, 8)
            Spacer()
        } else {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 2) {
                    let current = current, playable = playable
                    ForEach(lines) { row($0, current: current, playable: playable) }
                }
                .padding(.vertical, 4)
            }
        }
    }

    private func row(_ line: CallLine, current: Int?, playable: Bool) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(CallTranscript.clock(line.time))
                .font(.system(size: 11)).monospacedDigit().foregroundStyle(.secondary)
                .frame(width: 46, alignment: .leading)
            Text(CallTranscript.speaker(line.channel))
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(line.channel == "me" ? Color.blue : .orange)
                .frame(width: 58, alignment: .leading)
            text(line.text)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .background(line.id == current ? Color.accentColor.opacity(0.16) : .clear, in: RoundedRectangle(cornerRadius: 6))
        .contentShape(Rectangle())
        .onTapGesture { if playable { player.seek(line.time, play: true) } }
        .accessibilityHint(playable ? L("נגן מכאן", "Play from here") : "")
    }

    /// Laid out in its own direction, so its punctuation and word order are right, and aligned to the speaker's side.
    private func text(_ text: String) -> some View {
        let own: LayoutDirection = TextDirection.isRTL(text) ? .rightToLeft : .leftToRight
        return Text(TextDirection.marked(text))
            .font(.system(size: 13))
            .multilineTextAlignment(own == direction ? .leading : .trailing)
            .fixedSize(horizontal: false, vertical: true)
            .environment(\.layoutDirection, own)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// The call a follow-up email is drafted for, and the state that runs the command.
struct FollowUpSource {
    let call: CallEntry
    let state: AppState
}

/// "צור סיכום" for a call that has none: runs `callcoach summary` in the background and tells how it went.
enum SummaryJob {
    static func arguments(folder: URL, data: URL) -> [String] {
        ["summary", "--data", data.path, "--call", folder.path]
    }

    /// The command's one line of answer; a failure keeps the engine's message.
    static func read(_ output: Data, status: Int32) -> BriefOutcome {
        BriefOutcome.read(output, status: status, failure: failure)
    }

    static func failure(_ status: Int32) -> String {
        L("כתיבת הסיכום נכשלה (קוד \(status)). הפרטים בקובץ:\n\(BriefOutcome.log)",
          "Writing the summary failed (code \(status)). The details are in:\n\(BriefOutcome.log)")
    }

    /// The engine with the account a call would use, as the follow-up is run; nil when the account has a problem.
    static func run(state: AppState, folder: URL) -> (run: BriefRun?, problem: String?) {
        let chosen = AppState.engineAccount(state.accounts.active, readKey: AccountStore.readKey)
        if let problem = chosen.problem { return (nil, problem) }
        let run = BriefRun(executable: Paths.engine, arguments: arguments(folder: folder, data: state.data),
                           environment: EngineLink.environment(account: chosen.account, apiKey: chosen.key),
                           log: Paths.tools.appendingPathComponent("logs/engine-stderr.log"), failure: failure)
        return (run, nil)
    }
}

/// The summary tab of a call with no summary: "צור סיכום", then "כותב סיכום…", then the page reloads with the summary
/// or shows why it was not written. Off during a call.
struct SummaryMaker: View {
    let call: CallEntry
    @ObservedObject var state: AppState
    var title = L("צור סיכום", "Write a summary")
    var done: () -> Void = {}
    @ViewState private var writing = false
    @ViewState private var failure: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if writing {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(L("כותב סיכום…", "Writing a summary…")).foregroundStyle(.secondary)
                }
            } else {
                Button(title, action: make).disabled(state.inCall)
            }
            if let failure { DirectedText(failure, size: 12).foregroundStyle(.orange) }
        }
        .padding(.vertical, 4)
    }

    private func make() {
        failure = nil
        let made = SummaryJob.run(state: state, folder: call.folder)
        guard let run = made.run else {
            failure = made.problem
            return
        }
        writing = true
        run.start("") { outcome in
            writing = false
            if case .failed(let message) = outcome { failure = message }
            done()
        }
    }
}

/// The follow-up email the engine wrote in the call folder: a first line "Subject: ...", a blank line, then the body.
enum FollowUp {
    static func read(_ file: URL) -> String? {
        guard let text = try? String(contentsOf: file, encoding: .utf8) else { return nil }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    /// The draft in its own direction, every line marked, with the subject line in bold.
    static func text(_ draft: String, size: CGFloat = 13) -> AttributedString {
        let marked = TextDirection.markedLines(draft)
        let lines = marked.components(separatedBy: "\n")
        var out = AttributedString()
        for (i, line) in lines.enumerated() {
            var part = AttributedString(line + (i < lines.count - 1 ? "\n" : ""))
            let subject = i == 0 && line.dropFirst().lowercased().hasPrefix("subject:")
            part.font = .system(size: size, weight: subject ? .semibold : .regular)
            out += part
        }
        return out
    }
}

/// "טיוטת מייל" in the row under a call summary: it writes the draft when there is none, then shows it.
struct FollowUpButton: View {
    let call: CallEntry
    @ObservedObject var state: AppState
    var show: () -> Void = {}

    var body: some View {
        Button {
            if state.followUps[call.folder.path] == nil && FollowUp.read(call.followUp) == nil { state.writeFollowUp(call.folder) }
            DispatchQueue.main.async(execute: show)
        } label: {
            Label(FollowUpBox.title, systemImage: "envelope")
        }
    }
}

/// The draft under the summary, once it is asked for or written: "כותב טיוטה…", then the text in its own direction with
/// "העתק" and "כתוב מחדש", or the error with "נסה שוב". Nothing is ever sent.
struct FollowUpBox: View {
    let call: CallEntry
    @ObservedObject var state: AppState
    @ViewState private var draft: String?
    @ViewState private var copied = false

    static var title: String { L("טיוטת מייל", "Follow-up email") }
    static var writingText: String { L("כותב טיוטה…", "Writing a draft…") }

    var body: some View {
        let job = state.followUps[call.folder.path]
        let writing = job == .writing
        VStack(alignment: .leading, spacing: 8) {
            if job != nil || draft != nil {
                Divider().padding(.vertical, 8)
                HStack(spacing: 8) {
                    Text(Self.title).font(.system(size: 14, weight: .semibold))
                    Spacer()
                    if let draft, !writing {
                        Button(copied ? L("הועתק", "Copied") : L("העתק", "Copy")) { copy(draft) }
                        Button(L("כתוב מחדש", "Write again")) { state.writeFollowUp(call.folder) }
                    }
                }
                .controlSize(.small)
                if writing {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text(Self.writingText).foregroundStyle(.secondary)
                    }
                } else if case .failed(let message)? = job {
                    HStack(spacing: 8) {
                        DirectedText(message, size: 12).foregroundStyle(.orange)
                        Button(L("נסה שוב", "Try again")) { state.writeFollowUp(call.folder) }.controlSize(.small)
                    }
                }
                if let draft, !writing {
                    let own: LayoutDirection = TextDirection.isRTL(draft) ? .rightToLeft : .leftToRight
                    Text(FollowUp.text(draft))
                        .lineSpacing(2)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .environment(\.layoutDirection, own)
                        .frame(maxWidth: .infinity, alignment: own == .rightToLeft ? .trailing : .leading)
                        .environment(\.layoutDirection, .leftToRight)
                        .padding(12)
                        .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
                }
            }
        }
        .task(id: "\(call.id) \(state.followUpsWritten)") { draft = FollowUp.read(call.followUp) }
    }

    private func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        copied = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { copied = false }
    }
}

extension SelfCheck {
    static func callPageChecks(_ root: URL) -> [String] {
        var p: [String] = []
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("callcoach-page-\(getpid())")
        defer { try? FileManager.default.removeItem(at: dir) }
        let folder = dir.appendingPathComponent("2026-11-16_1030_sales")
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let meta = folder.appendingPathComponent("meta.json")
        let started = 1_763_281_800.123456
        try? #"{"started": 1763281800.123456, "app": "Zoom", "extra": {"n": [1, 2]}}"#.write(to: meta, atomically: true,
                                                                                          encoding: .utf8)
        func saved() -> [String: Any] { CallLibrary.meta(folder) }
        LanguageStore.shared.lang = .he
        expect(CallLibrary.load(dir).first?.title == "מכירה" && CallLibrary.load(dir).first?.name == nil, "the default title", &p)
        do {
            try CallLibrary.rename(folder, to: "  שיחה עם\nAcme ")
        } catch {
            p.append("rename failed: \(error)")
        }
        let named = saved()
        expect(named["title"] as? String == "שיחה עם Acme" && named["started"] as? Double == started
               && named["app"] as? String == "Zoom" && (named["extra"] as? [String: Any])?["n"] as? [Int] == [1, 2],
               "the title round trip keeps every key \(named)", &p)
        let call = CallLibrary.load(dir).first
        expect(call?.title == "שיחה עם Acme" && call?.defaultTitle == "מכירה", "the name is the title \(String(describing: call?.title))", &p)
        if let call {
            expect(NewNames.entry(CallTerm(term: "Okta", note: "x"), call: call.title, date: "2026-11-16")
                .contains("- עלה בשיחה \"שיחה עם Acme\", 2026-11-16"), "new names use the name", &p)
        }
        try? CallLibrary.rename(folder, to: " \n ")
        let cleared = saved()
        expect(cleared["title"] == nil && cleared["started"] as? Double == started && cleared["app"] as? String == "Zoom",
               "an empty name removes the title \(cleared)", &p)
        expect(CallLibrary.load(dir).first?.title == "מכירה", "the default comes back", &p)
        try? "not json".write(to: meta, atomically: true, encoding: .utf8)
        expect((try? CallLibrary.rename(folder, to: "x")) == nil
               && (try? String(contentsOf: meta, encoding: .utf8)) == "not json", "a meta.json that does not read is kept", &p)
        try? FileManager.default.removeItem(at: meta)
        try? CallLibrary.rename(folder, to: "Acme")
        expect(CallLibrary.name(folder) == "Acme", "a call without meta.json gets one", &p)
        expect(CallLibrary.load(dir).first?.hasAudio == false, "a call in progress has no recording", &p)
        FileManager.default.createFile(atPath: folder.appendingPathComponent("call.m4a").path, contents: Data())
        expect(CallLibrary.load(dir).first?.hasAudio == true, "a call with call.m4a has a recording", &p)

        let sample = """
            {"kind": "line", "id": 1, "channel": "me", "lang": "he", "text": "שלום, מה שלומך?", "t_start": 1000.5, "speech_end": 1002, "line_ready": 1003}
            {"kind": "bookmark", "t": 1001}
            {"kind": "line", "id": 3, "channel": "them", "lang": "en", "text": "We use Okta today", "t_start": 1065.2, "speech_end": 1068, "line_ready": 1069}
            {"kind": "line", "id": 2, "channel": "them", "lang": "he", "text": "טוב, תודה", "t_start": 1004
            {"kind": "line", "id": 4, "channel": "them", "lang": "he", "text": "  ", "t_start": 1070}
            {"kind": "line", "id": 5, "channel": "me", "lang": "he", "text": "בלי זמן"}
            {"kind": "line", "id": 6, "channel": "them", "lang": "he", "text": "ואז עברנו", "t_start": 1003.0, "speech_end": 1004, "line_ready": 1005}
            {"kind": "line", "id": 7, "channel": "me", "lang": "he", "text": "שעה אחרי", "t_start": 4723.9, "speech_end": 4725, "line_ready": 4726}
            """
        let lines = CallTranscript.parse(sample, started: 1000)
        expect(lines.map(\.text) == ["שלום, מה שלומך?", "ואז עברנו", "We use Okta today", "שעה אחרי"],
               "lines in order, a bad line skipped \(lines.map(\.text))", &p)
        expect(lines.map(\.time) == [0.5, 3, 1065.2 - 1000, 4723.9 - 1000] && lines.map(\.id) == [0, 1, 2, 3],
               "times from the start \(lines.map(\.time))", &p)
        expect(lines.map { CallTranscript.speaker($0.channel) } == ["אני", "הצד השני", "הצד השני", "אני"], "speakers", &p)
        expect(CallTranscript.parse(sample, started: nil).first?.time == 0, "without started the first line is the start", &p)
        let early = CallTranscript.parse(#"{"kind": "bookmark", "t": 990}"# + "\n" + sample, started: nil)
        expect(early.first?.time == 10.5, "without started an earlier bookmark is the start \(early.map(\.time))", &p)
        let separators = CallTranscript.parse(#"{"kind": "line", "channel": "them", "text": "אחת\#u{2028}שתיים\#u{85}שלוש", "t_start": 1010}"#
                                              + "\n" + #"{"kind": "line", "channel": "me", "text": "ארבע", "t_start": 1011}"#, started: 1000)
        expect(separators.map(\.text) == ["אחת\u{2028}שתיים\u{85}שלוש", "ארבע"], "records split on a newline only \(separators.map(\.text))", &p)
        try? #"{"started": 1000}"#.write(to: meta, atomically: true, encoding: .utf8)
        try? (sample + "\n").write(to: folder.appendingPathComponent("lines.jsonl"), atomically: true, encoding: .utf8)
        expect(CallTranscript.load(folder) == lines && CallTranscript.load(dir).isEmpty, "the transcript of a call folder", &p)
        expect(CallTranscript.text(Array(lines.prefix(3))) == "אני (0:00): שלום, מה שלומך?\nהצד השני (0:03): ואז עברנו\nהצד השני (1:05): We use Okta today",
               "the transcript as text \(CallTranscript.text(Array(lines.prefix(3))))", &p)
        let board = NSPasteboard(name: NSPasteboard.Name("callcoach-check-\(UUID().uuidString)"))
        expect(CallTranscript.copy(lines, to: board) && board.string(forType: .string) == CallTranscript.text(lines)
               && !CallTranscript.copy([], to: board), "copying the transcript", &p)
        board.releaseGlobally()
        expect(CallTranscript.current(lines, at: 70, begun: false) == nil && CallTranscript.current(lines, at: 70, begun: true) == 2
               && CallTranscript.current(lines, at: 2.8, begun: true) == 1, "the line being played", &p)
        let clocks = [0, 5.9, 65, 599, 3599.99, 3600, 3723.9, 36_000, -4].map(CallTranscript.clock)
        expect(clocks == ["0:00", "0:05", "1:05", "9:59", "59:59", "1:00:00", "1:02:03", "10:00:00", "0:00"], "clock \(clocks)", &p)
        expect(CallPlayer.rates.map(CallPlayer.rateText) == ["1×", "1.25×", "1.5×", "2×"], "speeds", &p)

        let player = CallPlayer()
        let broken = folder.appendingPathComponent("call.m4a")
        player.open(broken)
        expect(player.file == nil && player.failed == broken && !player.ready && player.note == "לא הצלחתי לפתוח את ההקלטה",
               "a recording that does not open says so", &p)
        player.locked = true
        expect(player.note == "לא הצלחתי לפתוח את ההקלטה", "the failure is told before the call", &p)
        player.open(nil)
        expect(player.failed == nil && !player.ready && player.note == "הנגן כבוי בזמן שיחה", "a call turns the player off", &p)
        player.locked = false
        expect(player.note == nil, "the player is on again after the call", &p)

        let state = AppState(accounts: AccountStore(tools: dir.appendingPathComponent("tools")), stream: nil, speed: 1)
        let suite = dir.appendingPathComponent("settings").path
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        state.defaults = defaults
        var channels: [FakeChannel] = []
        var asked: [() -> Void] = []
        state.onFatal = { p.append("fatal: \($0)") }
        state.askMicrophone = { asked.append($0) }
        state.makeChannel = {
            let channel = FakeChannel()
            channels.append(channel)
            return channel
        }
        state.ensureEngine()
        channels.first?.emit(#"{"type": "hello", "protocol": \#(protocolVersion), "version": "x"}"#)
        var off = [MainView.playbackOff(state)]
        state.start(StartFields())
        off.append(MainView.playbackOff(state))
        asked.forEach { $0() }
        channels.first?.emit(#"{"type": "status", "state": "active", "elapsed": 0, "talk_share": 0, "lang_me": "he", "lang_them": "he", "conversation_lang": "he", "agent": "off", "level": "normal", "visible": true}"#)
        off.append(MainView.playbackOff(state) && state.inCall)
        channels.first?.emit(#"{"type": "stopped"}"#)
        off.append(MainView.playbackOff(state))
        expect(off == [false, true, true, false], "playback is off from the start of a call until it stops \(off)", &p)
        let draft = folder.appendingPathComponent("followup.md")
        expect(FollowUp.read(draft) == nil, "no draft before one is written", &p)
        try? "Subject: Next steps\n\nHi Dana,\nThanks for the time today.\n".write(to: draft, atomically: true, encoding: .utf8)
        let english = FollowUp.read(draft) ?? ""
        expect(english.hasPrefix("Subject: Next steps") && !TextDirection.isRTL(english)
               && String(FollowUp.text(english).characters).hasPrefix("\u{200E}Subject"), "an English draft is left to right", &p)
        let hebrew = "Subject: המשך לשיחה\n\nהיי דנה,\nOkta עלתה בשיחה."
        let hebrewLines = String(FollowUp.text(hebrew).characters).components(separatedBy: "\n")
        expect(TextDirection.isRTL(hebrew) && hebrewLines.count == 4 && hebrewLines.allSatisfy { $0.hasPrefix("\u{200F}") },
               "a Hebrew draft marks every line right to left, a line that starts with a name too \(hebrewLines)", &p)
        expect(CallPage.Tab.shown(names: 0) == [.summary, .transcript] && CallPage.Tab.shown(names: 2) == [.summary, .transcript, .names]
               && CallPage.Tab.names.label(names: 2) == "שמות חדשים (2)", "the new names tab only with new names", &p)
        expect(CallPage.coachJoined(recordOnly: true, coachFrom: 700) == L("המאמן הצטרף בדקה 12", "The coach joined in minute 12"),
               "a recording the coach joined", &p)
        expect(CallPage.coachJoined(recordOnly: true, coachFrom: nil) == nil && CallPage.coachJoined(recordOnly: false, coachFrom: 0) == nil
               && CallPage.coachJoined(recordOnly: true, coachFrom: 0) == nil,
               "no line for a call that started with the coach or never had it", &p)
        let day = Calendar.current.date(from: DateComponents(year: 2026, month: 11, day: 16, hour: 14, minute: 30))!
        let later = Calendar.current.date(byAdding: .day, value: 3, to: day)!
        let details = [CallEntry(folder: dir, started: day, kind: "sales", hasSummary: true, terms: 4),
                       CallEntry(folder: dir, started: day, kind: "sales", hasSummary: true, terms: 4, name: "Acme"),
                       CallEntry(folder: dir, started: day, kind: "general", hasSummary: true, terms: 0, name: "Acme"),
                       CallEntry(folder: dir, started: day, kind: "general", hasSummary: false, terms: 0, recordOnly: true),
                       CallEntry(folder: dir, started: day, kind: "general", hasSummary: false, terms: 0, recordOnly: true,
                                 coachFrom: 700),
                       CallEntry(folder: dir, started: day, kind: "general", hasSummary: true, terms: 0, recordOnly: true)]
            .map { CallPage.details($0, now: later) }
        expect(details == ["16.11 14:30", "16.11 14:30 · מכירה", "16.11 14:30", "16.11 14:30 · הקלטה בלבד", "16.11 14:30",
                           "16.11 14:30"], "the line under the name \(details)", &p)
        expect(CallPage.details(CallEntry(folder: dir, started: day, kind: "sales", hasSummary: true, terms: 0), now: day)
               == "היום 14:30", "a call from today", &p)
        expect(SummaryJob.arguments(folder: URL(fileURLWithPath: "/x/calls/a"), data: URL(fileURLWithPath: "/x"))
               == ["summary", "--data", "/x", "--call", "/x/calls/a"], "the summary command", &p)
        let summaryOk = SummaryJob.read(Data(#"{"ok": true, "path": "/x/calls/a/summary.md", "message": ""}"#.utf8), status: 0)
        let summaryNo = SummaryJob.read(Data(#"{"ok": false, "message": "אין חשבון פעיל: צריך לבחור חשבון בהגדרות"}"#.utf8), status: 1)
        expect(summaryOk == .made(path: "/x/calls/a/summary.md") && summaryNo == .failed("אין חשבון פעיל: צריך לבחור חשבון בהגדרות"),
               "the summary reply \(summaryOk) \(summaryNo)", &p)
        expect({ if case .failed(let m) = SummaryJob.read(Data(), status: 3) { return m.contains("3") }; return false }(),
               "no reply is a failure with the code", &p)
        LanguageStore.shared.lang = .en
        expect(lines.map { CallTranscript.speaker($0.channel) } == ["Me", "Them", "Them", "Me"], "English speakers", &p)
        LanguageStore.shared.lang = .he
        return p
    }
}
