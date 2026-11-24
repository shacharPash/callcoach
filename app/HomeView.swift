import AppKit
import AVFoundation
import SwiftUI

/// What the home's buttons ask of the main window.
struct HomeActions {
    var bringPanel: () -> Void = {}
    var openAccounts: () -> Void = {}
    /// A brief's page, in the briefs tab.
    var openBrief: (String) -> Void = { _ in }
    var showBriefs: () -> Void = {}
    /// A call's page, in the calls tab.
    var openCall: (String) -> Void = { _ in }
    /// A summary was written: the call list is read again.
    var reload: () -> Void = {}
}

/// The main window's first tab: the live card, the start, the next briefs and the last calls. The brief that is due
/// follows the clock, so the briefs are drawn again every minute.
struct HomeView: View {
    @ObservedObject var state: AppState
    let calls: [CallEntry]
    let actions: HomeActions
    @ViewState private var mic = Permissions.microphone
    @ViewState private var hasMic = true
    @Environment(\.layoutDirection) private var direction

    static let mostBriefs = 3
    static let mostCalls = 5

    static var recordTitle: String { L("הקלטה בלבד", "Record only") }
    static var openPanelTitle: String { L("פתח את הפאנל", "Open the panel") }
    static var stopTitle: String { L("עצור", "Stop") }
    static var startTitle: String { L("התחל", "Start") }
    static var startCallTitle: String { L("התחל שיחה", "Start a call") }
    static var nowTitle: String { L("עכשיו", "Now") }
    static var nextTitle: String { L("השיחות הבאות", "Next calls") }
    static var recentTitle: String { L("שיחות אחרונות", "Recent calls") }
    static var noBriefsText: String { L("אין שיחות מתוכננות", "No calls planned") }
    static var prepareTitle: String { L("הכן תדריך", "Prepare a brief") }
    static var allBriefsTitle: String { L("כל התדריכים", "All briefs") }
    static var noCallsText: String { L("עדיין אין שיחות", "No calls yet") }
    static var idleLine: String { L("אין שיחה פעילה", "No call right now") }

    /// The live card's one line: the call's state and clock, or that there is none.
    static func liveLine(_ m: CallModel) -> String {
        switch m.phase {
        case .stopping:
            return L("מסיים את השיחה…", "Ending the call…")
        case .active:
            let mode = m.chips.coach ? L("עם מאמן", "With the coach") : L("הקלטה בלבד", "Recording only")
            return L("שיחה פעילה", "In a call") + " · " + clockText(m.status?.elapsed ?? 0) + " · " + mode
        default:
            return idleLine
        }
    }

    /// The briefs the home lists: the one shown first, then the others, at most three.
    static func nextBriefs(_ choice: BriefChoice) -> [Brief] {
        Array(((choice.shown.map { [$0] } ?? []) + choice.others).prefix(mostBriefs))
    }

    /// Only the brief the choice marks as due.
    static func isDue(_ brief: Brief, _ choice: BriefChoice) -> Bool {
        choice.due && choice.shown?.file == brief.file
    }

    /// The one start button that is the main one: red, prominent and with ⌃⌥S, since those keys start the same call.
    enum MainStart: Equatable {
        /// "התחל שיחה", a call without a brief.
        case call
        /// The "התחל" of the brief with this file.
        case brief(String)
    }

    /// The due brief's "התחל" when a brief is due, else "התחל שיחה"; none in a call.
    static func mainStart(_ choice: BriefChoice, inCall: Bool) -> MainStart? {
        guard !inCall else { return nil }
        return choice.dueBrief.map { .brief($0.file) } ?? .call
    }

    /// The last calls of the list, which is newest first.
    static func recent(_ calls: [CallEntry]) -> [CallEntry] {
        Array(calls.prefix(mostCalls))
    }

    /// A finished call with no summary; a call still recording has no audio file yet.
    static func needsSummary(_ call: CallEntry) -> Bool {
        !call.hasSummary && call.hasAudio
    }

    var body: some View {
        ScrollView {
            TimelineView(.everyMinute) { context in
                let choice = state.briefChoice(now: context.date)
                let main = Self.mainStart(choice, inCall: state.inCall)
                VStack(alignment: .leading, spacing: 18) {
                    liveCard
                    if !state.inCall { start(main: main == .call) }
                    next(choice, main: main, now: context.date)
                    recent(now: context.date)
                }
                .frame(maxWidth: 640)
                .padding(20)
                .frame(maxWidth: .infinity)
            }
        }
        .onAppear(perform: checkMicrophone)
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.didBecomeKeyNotification)) { _ in checkMicrophone() }
    }

    private func checkMicrophone() {
        mic = Permissions.microphone
        hasMic = Permissions.hasMicrophone
    }

    /// "פתח את הפאנל" works only in a call; with none the card says so and the button is off.
    private var liveCard: some View {
        let m = state.model
        return HStack(spacing: 10) {
            if state.inCall { Circle().fill(Color.red).frame(width: 8, height: 8) }
            Text(Self.liveLine(m))
                .font(.system(size: 14, weight: state.inCall ? .semibold : .regular))
                .foregroundStyle(state.inCall ? Color.primary : Color.secondary)
                .monospacedDigit()
            Spacer()
            Button(Self.openPanelTitle) { actions.bringPanel() }.disabled(!state.inCall)
            if state.inCall {
                Button(Self.stopTitle) { state.stop() }.disabled(m.phase != .active)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background((state.inCall ? Color.red : Color.primary).opacity(state.inCall ? 0.08 : 0.05), in: RoundedRectangle(cornerRadius: 10))
    }

    /// With a brief due, its "התחל" below is the main button, so these two step down to the regular size.
    private func start(main: Bool) -> some View {
        let loading = state.starting
        let size: ControlSize = main ? .large : .regular
        return VStack(alignment: .leading, spacing: 10) {
            TextField(L("על מה השיחה? (לא חובה)", "What is the call about? (optional)"), text: $state.topic)
                .textFieldStyle(.roundedBorder)
                .font(.system(size: 14))
                .multilineTextAlignment(direction == .rightToLeft ? .trailing : .leading)
                .environment(\.layoutDirection, .leftToRight)
            HStack(spacing: 10) {
                let button = Button { state.startCall(brief: nil) } label: {
                    HStack(spacing: 6) {
                        if loading {
                            ProgressView().controlSize(.small)
                            Text(L("טוען…", "Loading…"))
                        } else {
                            startLabel(Self.startCallTitle, keys: main)
                        }
                    }
                    .frame(minWidth: main ? 150 : nil)
                }
                if main {
                    button.buttonStyle(.borderedProminent).tint(.red).controlSize(size).disabled(state.starting)
                } else {
                    button.controlSize(size).disabled(state.starting)
                }
                Button { state.startRecording() } label: { Label(Self.recordTitle, systemImage: "record.circle") }
                    .controlSize(size)
                    .disabled(state.starting)
                    .fixedSize()
            }
            AccountLine(state: state, openAccounts: actions.openAccounts)
            if let problem = MicLine.problem(status: mic, hasDevice: hasMic) {
                MicLine(text: problem, denied: mic == .denied || mic == .restricted)
            }
        }
    }

    private func next(_ choice: BriefChoice, main: MainStart?, now: Date) -> some View {
        let briefs = Self.nextBriefs(choice)
        return VStack(alignment: .leading, spacing: 8) {
            sectionTitle(Self.nextTitle)
            if briefs.isEmpty {
                Text(Self.noBriefsText).foregroundStyle(.secondary).font(.system(size: 13))
            }
            ForEach(briefs) { brief in
                briefRow(brief, due: Self.isDue(brief, choice), main: main == .brief(brief.file), now: now)
            }
            briefJob
            HStack(spacing: 10) {
                Button(Actions.titled(Actions.prepare, Self.prepareTitle)) { state.prepSheetOpen = true }
                Button(Self.allBriefsTitle) { actions.showBriefs() }
            }
            .controlSize(.small)
        }
    }

    private func briefRow(_ brief: Brief, due: Bool, main: Bool, now: Date) -> some View {
        HStack(spacing: 10) {
            Button { actions.openBrief(brief.file) } label: {
                VStack(alignment: .leading, spacing: 2) {
                    DirectedText(brief.title, size: 14, weight: .semibold).lineLimit(1)
                    DirectedText(Briefs.details(brief, now: now), size: 12).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            .buttonStyle(.plain)
            if due {
                Text(Self.nowTitle)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Color.accentColor)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 2)
                    .background(Color.accentColor.opacity(0.15), in: Capsule())
            }
            if main {
                Button { state.startCall(brief: brief) } label: { startLabel(Self.startTitle, keys: true) }
                    .buttonStyle(.borderedProminent).tint(.red).controlSize(.large).disabled(state.starting)
            } else {
                Button(Self.startTitle) { state.startCall(brief: brief) }.disabled(state.starting || state.inCall)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
    }

    @ViewBuilder private var briefJob: some View {
        switch state.briefJob {
        case .making?:
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                DirectedText(L("מכין תדריך…", "Preparing a brief…"), size: 12)
                Button(L("ביטול", "Cancel")) { state.cancelBrief() }
            }
            .controlSize(.small)
        case .failed(_, let message)?:
            HStack(spacing: 8) {
                DirectedText(message, size: 12).foregroundStyle(.orange)
                Button(L("נסה שוב", "Try again")) { state.retryBrief() }
                Button(L("סגור", "Dismiss")) { state.cancelBrief() }
            }
            .controlSize(.small)
        case nil:
            EmptyView()
        }
    }

    /// One card, as the live card's, with a thin line between the rows.
    private func recent(now: Date) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionTitle(Self.recentTitle)
            let list = Self.recent(calls)
            if list.isEmpty {
                Text(Self.noCallsText).foregroundStyle(.secondary).font(.system(size: 13))
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(list.enumerated()), id: \.element.id) { index, call in
                        if index > 0 { Divider().padding(.horizontal, 12) }
                        callRow(call, now: now)
                    }
                }
                .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
            }
        }
    }

    /// A click anywhere on the row opens the call; "צור סיכום" sits right after its lines. The date line is marked
    /// with the window's direction, since a line of only digits ("15.11 16:00") has none of its own.
    private func callRow(_ call: CallEntry, now: Date) -> some View {
        let mark = direction == .rightToLeft ? "\u{200F}" : "\u{200E}"
        return HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(TextDirection.marked(call.title)).font(.system(size: 13, weight: .medium)).lineLimit(1)
                Text(mark + CallLibrary.datedLine(call, now: now)).font(.system(size: 11)).foregroundStyle(.secondary)
                    .monospacedDigit()
            }
            if Self.needsSummary(call) {
                SummaryMaker(call: call, state: state, done: actions.reload).controlSize(.small)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .contentShape(Rectangle())
        .onTapGesture { actions.openCall(call.id) }
    }

    /// A start button's label: the microphone, the title and, on the main button, the keys that press it.
    private func startLabel(_ title: String, keys: Bool) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "mic.fill")
            Text(title).lineLimit(1)
            if keys { Text(Actions.startNow.hotkey).opacity(0.75) }
        }
    }

    private func sectionTitle(_ text: String) -> some View {
        Text(text).font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
    }
}

extension SelfCheck {
    static func homeChecks(_ root: URL) -> [String] {
        var p: [String] = []
        let store = LanguageStore.shared
        store.lang = .he
        expect(MainView.Mode.allCases == [.home, .calls, .briefs, .settings] && MainView.Mode.home.title == L("בית", "Home"),
               "four tabs, the home first", &p)
        let none = CallModel()
        expect(HomeView.liveLine(none) == L("אין שיחה פעילה", "No call right now"), "the live card with no call", &p)
        var rec = CallModel()
        rec.phase = .active
        rec.chips = Chips(on: [.transcript])
        rec.status = Status(state: "active", elapsed: 754, talkShare: 0.3, langMe: "he", langThem: "he", conversationLang: "he",
                            agent: "ok", level: "normal", visible: true)
        expect(HomeView.liveLine(rec) == "שיחה פעילה · 12:34 · הקלטה בלבד", "a recording \(HomeView.liveLine(rec))", &p)
        var coach = rec
        coach.chips = Chips(on: [.ask])
        expect(HomeView.liveLine(coach) == "שיחה פעילה · 12:34 · עם מאמן", "with the coach \(HomeView.liveLine(coach))", &p)
        var stopping = coach
        stopping.phase = .stopping
        expect(HomeView.liveLine(stopping) == "מסיים את השיחה…", "while it ends", &p)
        store.lang = .en
        expect(HomeView.liveLine(coach) == "In a call · 12:34 · With the coach" && HomeView.liveLine(none) == "No call right now",
               "English live line", &p)
        store.lang = .he

        func brief(_ title: String, _ file: String) -> Brief { Briefs.parse("---\ntitle: \(title)\n---\n", file: file) }
        let a = brief("א", "a.md"), b = brief("ב", "b.md"), c = brief("ג", "c.md"), d = brief("ד", "d.md")
        let many = BriefChoice(shown: a, others: [b, c, d], due: true)
        expect(HomeView.nextBriefs(many).map(\.file) == ["a.md", "b.md", "c.md"], "at most three, the shown first", &p)
        expect(HomeView.nextBriefs(BriefChoice(shown: a, others: [b])).map(\.file) == ["a.md", "b.md"], "two briefs", &p)
        expect(HomeView.nextBriefs(BriefChoice()).isEmpty, "no briefs", &p)
        expect(HomeView.isDue(a, many) && !HomeView.isDue(b, many) && !HomeView.isDue(a, BriefChoice(shown: a, others: [b])),
               "only the due one is marked", &p)
        let calm = BriefChoice(shown: a, others: [b])
        expect(HomeView.mainStart(calm, inCall: false) == .call && HomeView.mainStart(BriefChoice(), inCall: false) == .call,
               "with no brief due, התחל שיחה is the main button", &p)
        expect(HomeView.mainStart(many, inCall: false) == .brief("a.md") && many.dueBrief?.file == "a.md" && calm.dueBrief == nil
               && HomeView.nextBriefs(many).filter { HomeView.mainStart(many, inCall: false) == .brief($0.file) }.map(\.file) == ["a.md"],
               "with a brief due, its row alone has the main button, the brief ⌃⌥S starts", &p)
        expect(HomeView.mainStart(many, inCall: true) == nil && HomeView.mainStart(calm, inCall: true) == nil, "none in a call", &p)

        let folder = URL(fileURLWithPath: "/x/calls")
        let entries = (0..<7).map { i in
            CallEntry(folder: folder.appendingPathComponent("2026-11-1\(i)_0900_general"), started: Date(), kind: "general",
                      hasSummary: i == 0, terms: 0, hasAudio: i != 2)
        }
        expect(HomeView.recent(entries).map(\.id) == entries.prefix(5).map(\.id) && HomeView.recent([]).isEmpty, "the last five", &p)
        expect(entries.map(HomeView.needsSummary) == [false, true, false, true, true, true, true],
               "a finished call without a summary needs one \(entries.map(HomeView.needsSummary))", &p)
        return p
    }
}
