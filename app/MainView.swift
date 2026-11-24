import AppKit
import AVFoundation
import SwiftUI

/// One call folder in the data folder, as the main window lists it.
struct CallEntry: Identifiable, Equatable {
    let folder: URL
    let started: Date
    let kind: String
    let hasSummary: Bool
    let terms: Int
    /// The name he gave the call, the `title` in its meta.json.
    var name: String? = nil
    /// A call still in progress has no call.m4a yet.
    var hasAudio = false
    /// The summary is lecture notes, not a call summary, so it gets no follow-up email.
    var lecture = false
    /// The brief file the call used, the `brief` in its meta.json.
    var brief: String? = nil
    /// The call only recorded, so it has no summary by design: `record_only` in its meta.json.
    var recordOnly = false
    /// The second of the call at which the coach was first on, the `coach_from` in its meta.json; nil when it never was.
    var coachFrom: Double? = nil
    /// The model failed and the summary was written locally, so a full one can still be asked for: `summary_local`.
    var localSummary = false
    /// Why the last copy to the export folder failed, the `export_error` in its meta.json; nil when it worked or
    /// there was none.
    var exportError: String? = nil

    var id: String { folder.lastPathComponent }
    var summary: URL { folder.appendingPathComponent("summary.md") }
    var audio: URL { folder.appendingPathComponent("call.m4a") }
    var followUp: URL { folder.appendingPathComponent("followup.md") }

    /// The label of the call type, the title while he has given the call no name. A watch is a call folder from before
    /// there was one way to listen.
    var defaultTitle: String {
        ["general": L("שיחה", "Call"), "watch": L("צפייה", "Watching"), "autostart": L("הדגמה", "Demo")][kind]
            ?? callTypeLabels[kind] ?? kind.replacingOccurrences(of: "_", with: " ")
    }

    var title: String { name ?? defaultTitle }

    /// A recording with no summary that the coach never joined, so its rows say "הקלטה בלבד".
    var onlyRecording: Bool {
        recordOnly && !hasSummary && CallPage.coachJoined(recordOnly: true, coachFrom: coachFrom) == nil
    }

    var subtitle: String {
        let time = CallLibrary.time.string(from: started)
        if onlyRecording { return time + " · " + L("הקלטה בלבד", "Recording only") }
        if !hasSummary { return time + " · " + L("אין סיכום", "No summary") }
        return terms > 0 ? time + " · " + CallLibrary.termsLabel(terms) : time
    }
}

enum CallLibrary {
    static func termsLabel(_ count: Int) -> String {
        count == 1 ? L("מושג אחד", "1 term") : L("\(count) מושגים", "\(count) terms")
    }

    static let time: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "HH:mm"
        return f
    }()

    private static let folderName: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd_HHmm"
        return f
    }()

    /// Call folders, newest first. A folder name is "2026-11-15_1647_watch"; other folders are skipped.
    static func load(_ dir: URL) -> [CallEntry] {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
        return names.sorted(by: >).compactMap { entry(dir.appendingPathComponent($0)) }
    }

    static func entry(_ folder: URL) -> CallEntry? {
        let parts = folder.lastPathComponent.split(separator: "_", maxSplits: 2).map(String.init)
        guard parts.count >= 2, let started = folderName.date(from: parts[0] + "_" + parts[1]) else { return nil }
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: folder.path, isDirectory: &isDir), isDir.boolValue else { return nil }
        let text = try? String(contentsOf: folder.appendingPathComponent("summary.md"), encoding: .utf8)
        let kind = parts.count > 2 ? parts[2] : "general"
        let info = meta(folder)
        return CallEntry(folder: folder, started: started, kind: kind,
                         hasSummary: text != nil, terms: termCount(text ?? ""), name: name(info),
                         hasAudio: FileManager.default.fileExists(atPath: folder.appendingPathComponent("call.m4a").path),
                         lecture: lecture(info, kind: kind),
                         brief: (info["brief"] as? String).flatMap { $0.isEmpty ? nil : $0 },
                         recordOnly: info["record_only"] as? Bool == true,
                         coachFrom: (info["coach_from"] as? NSNumber)?.doubleValue,
                         localSummary: info["summary_local"] as? Bool == true,
                         exportError: (info["export_error"] as? String).flatMap { $0.isEmpty ? nil : $0 })
    }

    /// `summary_kind` in meta.json; a call from before it, that was a watch, has lecture notes.
    static func lecture(_ meta: [String: Any], kind: String) -> Bool {
        if let summaryKind = meta["summary_kind"] as? String { return summaryKind == "lecture" }
        return meta["watch"] as? Bool == true || kind == "watch"
    }

    /// The call's meta.json, where the engine writes `started` when the call starts.
    static func meta(_ folder: URL) -> [String: Any] {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("meta.json")),
              let meta = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return meta
    }

    static func name(_ folder: URL) -> String? {
        name(meta(folder))
    }

    static func name(_ meta: [String: Any]) -> String? {
        let name = NewNames.clean(meta["title"] as? String ?? "")
        return name.isEmpty ? nil : name
    }

    /// Writes the name as `title` in meta.json, keeping every other key, and replaces the file whole. An empty name
    /// removes it, so the label of the call type comes back. The folder keeps its name.
    static func rename(_ folder: URL, to name: String) throws {
        let url = folder.appendingPathComponent("meta.json")
        var meta: [String: Any] = [:]
        if FileManager.default.fileExists(atPath: url.path) {
            guard let saved = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any] else {
                throw CocoaError(.fileReadCorruptFile, userInfo: [NSFilePathErrorKey: url.path])
            }
            meta = saved
        }
        let name = NewNames.clean(name)
        meta["title"] = name.isEmpty ? nil : name
        try JSONSerialization.data(withJSONObject: meta, options: [.sortedKeys, .withoutEscapingSlashes])
            .write(to: url, options: .atomic)
    }

    /// The bullets under "## מושגים שעלו", the section the engine adds to the summary ("## מונחים שעלו" in summaries
    /// written before it had that name).
    static func termCount(_ summary: String) -> Int {
        var inTerms = false
        var count = 0
        for line in summary.split(separator: "\n") {
            if line.hasPrefix("## ") { inTerms = line == "## מושגים שעלו" || line == "## מונחים שעלו" } else if inTerms && line.hasPrefix("- ") { count += 1 }
        }
        return count
    }

    static func dayTitle(_ date: Date, now: Date = Date(), calendar: Calendar = .current) -> String {
        if calendar.isDate(date, inSameDayAs: now) { return L("היום", "Today") }
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: now), calendar.isDate(date, inSameDayAs: yesterday) {
            return L("אתמול", "Yesterday")
        }
        let c = calendar.dateComponents([.day, .month], from: date)
        return String(format: "%d.%d", c.day ?? 0, c.month ?? 0)
    }

    /// The call's line with its day first, for a list that is not grouped by day: "היום 14:30 · 4 מושגים".
    static func datedLine(_ call: CallEntry, now: Date = Date(), calendar: Calendar = .current) -> String {
        dayTitle(call.started, now: now, calendar: calendar) + " " + call.subtitle
    }

    /// Calls grouped by day, keeping their order.
    static func days(_ calls: [CallEntry], now: Date = Date()) -> [(title: String, calls: [CallEntry])] {
        var out: [(title: String, calls: [CallEntry])] = []
        for call in calls {
            let title = dayTitle(call.started, now: now)
            if out.last?.title == title { out[out.count - 1].calls.append(call) } else { out.append((title, [call])) }
        }
        return out
    }
}

struct MainActions {
    var openAccounts: () -> Void = {}
    var bringPanel: () -> Void = {}
    var openSettings: () -> Void = {}
}

/// Where the main window is: its tab and what is chosen in it. A change of language draws the window again from
/// scratch, and it opens back here instead of on the home.
final class MainPlace {
    static let shared = MainPlace()
    var mode = MainView.Mode.home
    var selected: String?
    var selectedBrief: String?
    var briefTab: String?
}

/// The main window: the tabs on top, then the home, or the chosen tab's list on the side and its page next to it.
struct MainView: View {
    @ObservedObject var state: AppState
    let actions: MainActions
    /// The chosen call's recording; the window stops it when it closes. Only opened and locked here, so not observed.
    let player: CallPlayer
    /// The window's tabs.
    enum Mode: String, CaseIterable {
        case home, calls, briefs, settings

        var title: String {
            switch self {
            case .home: return L("בית", "Home")
            case .calls: return L("שיחות", "Calls")
            case .briefs: return L("תדריכים", "Briefs")
            case .settings: return L("הגדרות", "Settings")
            }
        }
    }

    @ObservedObject private var route = SettingsRoute.shared

    @ViewState private var calls: [CallEntry] = []
    @ViewState private var selected = MainPlace.shared.selected
    @ViewState private var reloads = 0
    @ViewState private var mode = MainPlace.shared.mode
    @ViewState private var selectedBrief = MainPlace.shared.selectedBrief
    /// The brief page's tab: nil for the brief, or a call that used it.
    @ViewState private var briefTab = MainPlace.shared.briefTab
    /// "תדריך חדש": a form for a brief that has no file until it is saved.
    @ViewState private var newBrief = false

    var body: some View {
        VStack(spacing: 0) {
            tabs
            if state.inCall && mode != .home {
                InCallBar(state: state, bringPanel: actions.bringPanel)
            }
            Divider()
            if mode == .home {
                HomeView(state: state, calls: calls,
                         actions: HomeActions(bringPanel: actions.bringPanel, openAccounts: actions.openAccounts,
                                              openBrief: { showBrief($0) },
                                              showBriefs: { mode = .briefs },
                                              openCall: { call in
                                                  selected = call
                                                  mode = .calls
                                              },
                                              reload: { reload() }))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                HStack(spacing: 0) {
                    sidebar.frame(width: 250)
                    Divider()
                    detail.frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
        }
        .frame(minWidth: 760, minHeight: 560)
        .onAppear {
            reload()
            show(state.selectCall)
            showBrief(state.selectBrief)
        }
        .onChange(of: state.selectCall) { _, call in show(call) }
        .onChange(of: state.selectBrief) { _, brief in showBrief(brief) }
        .onChange(of: route.pending, initial: true) { _, pending in
            guard pending else { return }
            route.pending = false
            mode = .settings
        }
        .onChange(of: selectedBrief) { _, brief in
            briefTab = nil
            if brief != nil { newBrief = false }
            MainPlace.shared.selectedBrief = brief
        }
        .onChange(of: mode) { _, mode in
            if mode == .briefs && selectedBrief == nil && !newBrief { selectedBrief = Self.firstBrief(state.briefs, calls: briefCalls) }
            MainPlace.shared.mode = mode
        }
        .onChange(of: selected) { _, call in MainPlace.shared.selected = call }
        .onChange(of: briefTab) { _, tab in MainPlace.shared.briefTab = tab }
        .onChange(of: audio, initial: true) { _, file in player.open(file) }
        .onChange(of: Self.playbackOff(state), initial: true) { _, off in player.locked = off }
        .onChange(of: state.model.phase) { _, _ in reload() }
        .onChange(of: state.model.summary) { _, summary in
            reload()
            if let summary { selected = URL(fileURLWithPath: summary.path).deletingLastPathComponent().lastPathComponent }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.didBecomeKeyNotification)) { _ in reload() }
        .sheet(isPresented: $state.prepSheetOpen) {
            PrepSheet(state: state).environment(\.layoutDirection, LanguageStore.shared.lang.direction)
        }
    }

    /// Nothing plays from the moment a call is started until it ends.
    static func playbackOff(_ state: AppState) -> Bool {
        state.inCall || state.starting
    }

    /// The chosen call's recording, once it has one; another call stops the one playing. In the briefs tab, the call
    /// open on the brief page; the settings tab keeps the call that was playing.
    private var audio: URL? {
        let id = mode == .briefs ? briefTab : selected
        return calls.first { $0.id == id && $0.hasAudio }?.audio
    }

    /// How many calls used each brief, by file name.
    private var briefCalls: [String: Int] {
        Dictionary(grouping: calls.compactMap(\.brief), by: { $0 }).mapValues(\.count)
    }

    /// The brief the tab opens on: the first of the list.
    static func firstBrief(_ briefs: [Brief], calls: [String: Int], now: Date = Date()) -> String? {
        BriefGroup.groups(briefs, calls: calls, now: now).first?.briefs.first?.file
    }

    private func reload() {
        calls = CallLibrary.load(Paths.data.appendingPathComponent("calls"))
        reloads += 1
        if selected == nil || !calls.contains(where: { $0.id == selected }) { selected = calls.first?.id }
        state.reloadBriefs()
    }

    /// The call a notification asked for.
    private func show(_ call: String?) {
        guard let call else { return }
        state.selectCall = nil
        reload()
        if calls.contains(where: { $0.id == call }) {
            selected = call
            mode = .calls
        }
    }

    /// The brief just prepared, or the one a call page opened.
    private func showBrief(_ brief: String?) {
        guard let brief else { return }
        state.selectBrief = nil
        state.reloadBriefs()
        guard state.briefs.contains(where: { $0.file == brief }) else { return }
        newBrief = false
        selectedBrief = brief
        mode = .briefs
    }

    private var tabs: some View {
        Picker(L("תצוגה", "View"), selection: $mode) {
            ForEach(inReadingOrder(Mode.allCases), id: \.self) { Text($0.title).tag($0) }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .frame(maxWidth: 420)
        .padding(10)
        .frame(maxWidth: .infinity)
    }

    private var sidebar: some View {
        VStack(spacing: 0) {
            switch mode {
            case .home: EmptyView()
            case .calls: callList
            case .briefs:
                BriefsList(briefs: state.briefs, calls: briefCalls, selected: $selectedBrief,
                           newBrief: {
                               selectedBrief = nil
                               newBrief = true
                           },
                           prepare: { state.prepSheetOpen = true })
            case .settings: settingsList
            }
        }
    }

    private var settingsList: some View {
        List(selection: Binding(get: { route.page }, set: { if let page = $0 { route.page = page } })) {
            ForEach(SettingsPage.allCases) { page in
                Label(page.title, systemImage: page.icon).tag(page)
            }
        }
        .listStyle(.sidebar)
    }

    private var callList: some View {
        List(selection: $selected) {
            ForEach(CallLibrary.days(calls), id: \.title) { day in
                Section(day.title) {
                    ForEach(day.calls) { call in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(TextDirection.marked(call.title)).font(.system(size: 13, weight: .medium))
                            Text(call.subtitle).font(.system(size: 11)).foregroundStyle(.secondary).monospacedDigit()
                        }
                        .padding(.vertical, 2)
                        .tag(call.id)
                    }
                }
            }
        }
        .listStyle(.sidebar)
    }

    @ViewBuilder private var detail: some View {
        switch mode {
        case .home: EmptyView()
        case .calls: callDetail
        case .briefs: briefDetail
        case .settings: SettingsDetail(state: state)
        }
    }

    @ViewBuilder private var briefDetail: some View {
        if newBrief {
            BriefEditor(doc: BriefDocument(), file: nil, changed: nil, files: [], data: state.data,
                        saved: { file in
                            state.reloadBriefs()
                            newBrief = false
                            selectedBrief = file
                        },
                        cancelled: { newBrief = false })
                .id("new")
        } else if let brief = state.briefs.first(where: { $0.file == selectedBrief }) {
            BriefPage(brief: brief, calls: calls.filter { $0.brief == brief.file }, allCalls: calls, player: player,
                      state: state, inCall: state.inCall || state.starting, tab: $briefTab, refresh: reloads,
                      actions: BriefActions(start: { state.startCall(brief: $0) },
                                            followUp: { state.followUp($0) },
                                            openCall: { call in
                                                selected = call
                                                mode = .calls
                                            },
                                            saved: { _ in state.reloadBriefs() },
                                            reload: { reload() }))
                .id(brief.file)
        } else {
            VStack(spacing: 10) {
                Image(systemName: "doc.text").font(.system(size: 40)).foregroundStyle(.secondary)
                Text(state.briefs.isEmpty ? L("עדיין אין תדריכים", "No briefs yet") : L("בחר תדריך מהרשימה", "Choose a brief"))
                    .font(.system(size: 17, weight: .semibold))
                Text(L("תדריך הוא מה שהמאמן יודע לפני השיחה: מטרות, שאלות, מושגים והקשר.",
                       "A brief is what the coach knows before the call: goals, questions, terms and context."))
                    .foregroundStyle(.secondary)
            }
            .padding(30)
        }
    }

    @ViewBuilder private var callDetail: some View {
        if let call = calls.first(where: { $0.id == selected }) {
            CallPage(call: call, player: player, state: state, refresh: reloads, renamed: { reload() },
                     brief: call.brief.flatMap { file in state.briefs.first { $0.file == file }?.title },
                     openBrief: {
                         if let file = call.brief { showBrief(file) }
                     },
                     followUp: call.lecture ? nil : { state.followUp(call) })
                .id("\(call.id) \(call.hasSummary)")
        } else {
            VStack(spacing: 10) {
                Image(systemName: "text.bubble").font(.system(size: 40)).foregroundStyle(.secondary)
                Text(L("התחל את השיחה הראשונה", "Start your first call")).font(.system(size: 17, weight: .semibold))
                Text(L("כאן יופיעו השיחות שלך עם הסיכום והמושגים של כל אחת.",
                       "Your calls show up here, each with its summary and terms."))
                    .foregroundStyle(.secondary)
            }
            .padding(30)
        }
    }
}

/// A line about the account, only when something is wrong with it, with the way to the account tab.
struct AccountLine: View {
    @ObservedObject var state: AppState
    var openAccounts: () -> Void

    static func problem(active: AppAccount?, agent: String, check: AccountState?, problem: String? = nil)
        -> (color: Color, text: String)? {
        if let problem { return (.red, problem) }
        guard let active else {
            return (.orange, L("אין חשבון: השיחה תתומלל בלי סוכן", "No account: the call will be transcribed without an agent"))
        }
        if let check, check.name == active.name {
            return check.ok ? nil : (.red, L("החשבון \(active.shown) לא מחובר: \(check.message)",
                                             "The account \(active.shown) is not signed in: \(check.message)"))
        }
        if agent == "auth" {
            return (.red, L("החשבון \(active.shown) נדחה, צריך להתחבר מחדש", "The account \(active.shown) was rejected, sign in again"))
        }
        return nil
    }

    static var accountsTitle: String { L("חשבונות והוספת חשבון…", "Accounts and new account…") }

    var body: some View {
        if let line = Self.problem(active: state.accounts.active, agent: state.model.agent, check: state.model.account,
                                   problem: state.accountProblem) {
            HStack {
                Circle().fill(line.color).frame(width: 9, height: 9)
                DirectedText(line.text, size: 12)
                Button(L("בדוק", "Check")) { state.checkAccount() }.disabled(state.accounts.active == nil)
                Button(Self.accountsTitle) { openAccounts() }
            }
            .controlSize(.small)
        }
    }
}

/// A line about the microphone, only when there is no access or no microphone.
struct MicLine: View {
    let text: String
    let denied: Bool

    static func problem(status: AVAuthorizationStatus, hasDevice: Bool) -> String? {
        if status == .denied || status == .restricted {
            return L("אין גישה למיקרופון. בלעדיה רק הצד השני יתומלל", "No microphone access. Without it, only the other side is transcribed")
        }
        return hasDevice ? nil : L("לא נמצא מיקרופון. בלעדיו רק הצד השני יתומלל", "No microphone found. Without one, only the other side is transcribed")
    }

    var body: some View {
        HStack {
            DirectedText(text, size: 12).foregroundStyle(.red)
            if denied { Button(L("פתח הגדרות", "Open Settings")) { Permissions.openSettings("Privacy_Microphone") }.controlSize(.small) }
        }
    }
}

struct InCallBar: View {
    @ObservedObject var state: AppState
    var bringPanel: () -> Void = {}

    var body: some View {
        let m = state.model
        HStack(spacing: 12) {
            Circle().fill(Color.red).frame(width: 8, height: 8)
            Text(m.phase == .stopping ? L("מסיים את השיחה…", "Ending the call…")
                 : m.chips.coach ? L("בשיחה", "In a call") : L("מקליט בלבד", "Recording only"))
                .font(.system(size: 13, weight: .semibold))
            Text(clockText(m.status?.elapsed ?? 0)).monospacedDigit().foregroundStyle(.secondary)
            if !m.terms.isEmpty {
                Text(CallLibrary.termsLabel(m.terms.count)).foregroundStyle(.secondary)
            }
            Spacer()
            Button(m.visible ? L("הסתר את הפאנל", "Hide the panel") : L("הצג את הפאנל", "Show the panel")) {
                state.setVisible(!m.visible)
            }
            Button(AppDelegate.bringHereTitle) { bringPanel() }
            Button(L("עצור את השיחה", "Stop the call")) { state.stop() }.disabled(m.phase != .active)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(Color.red.opacity(0.08))
    }
}

extension SelfCheck {
    static func mainChecks(_ root: URL) -> [String] {
        var p: [String] = []
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("callcoach-main-\(getpid())")
        defer { try? FileManager.default.removeItem(at: dir) }
        for name in ["2026-11-14_1000_validation", "2026-11-15_0900_general", "2026-11-15_1200_watch", ".hidden", "notes"] {
            try? FileManager.default.createDirectory(at: dir.appendingPathComponent(name), withIntermediateDirectories: true)
        }
        FileManager.default.createFile(atPath: dir.appendingPathComponent("usage.json").path, contents: Data())
        let summary = "## מה למדתי\n- x\n\n## מושגים שעלו\n- SOC 2: תקן\n- Okta: התחברות\n\n## שימוש\n- בקשות: 3\n"
        try? summary.write(to: dir.appendingPathComponent("2026-11-15_0900_general/summary.md"), atomically: true, encoding: .utf8)
        try? #"{"summary_local": true, "export_error": "FileNotFoundError: gone"}"#.write(
            to: dir.appendingPathComponent("2026-11-14_1000_validation/meta.json"), atomically: true, encoding: .utf8)
        try? #"{"export_error": null}"#.write(to: dir.appendingPathComponent("2026-11-15_0900_general/meta.json"),
                                              atomically: true, encoding: .utf8)
        let calls = CallLibrary.load(dir)
        expect(calls.map(\.localSummary) == [false, false, true], "a local summary \(calls.map(\.localSummary))", &p)
        expect(calls.map(\.exportError) == [nil, nil, "FileNotFoundError: gone"], "a failed copy \(calls.map(\.exportError))", &p)
        expect(calls.map(\.id) == ["2026-11-15_1200_watch", "2026-11-15_0900_general", "2026-11-14_1000_validation"],
               "calls \(calls.map(\.id))", &p)
        expect(calls.map(\.kind) == ["watch", "general", "validation"], "kinds", &p)
        expect(calls.map(\.lecture) == [true, false, false], "an old watch has lecture notes \(calls.map(\.lecture))", &p)
        expect(CallLibrary.lecture(["watch": true], kind: "general") && CallLibrary.lecture(["summary_kind": "lecture"], kind: "general")
               && !CallLibrary.lecture(["summary_kind": "call"], kind: "watch") && !CallLibrary.lecture([:], kind: "sales"),
               "the summary's kind", &p)
        expect(calls[1].hasSummary && calls[1].terms == 2 && !calls[0].hasSummary, "summary and terms", &p)
        expect(CallLibrary.termCount("## מושגים שעלו\n## שימוש\n- x") == 0, "no terms", &p)
        expect(CallLibrary.termCount("## מונחים שעלו\n- SOC 2: תקן\n") == 1, "a summary from before the new name", &p)
        expect(CallLibrary.termsLabel(1) == L("מושג אחד", "1 term"), "one term", &p)
        let store = LanguageStore.shared
        store.lang = .he
        expect(calls[1].title == "שיחה" && calls[1].subtitle == "09:00 · 2 מושגים", "row \(calls[1].subtitle)", &p)
        expect(calls[0].subtitle == "12:00 · אין סיכום", "row without a summary", &p)
        let recorded = CallEntry(folder: calls[0].folder, started: calls[0].started, kind: "general", hasSummary: false,
                                 terms: 0, recordOnly: true)
        var joined = recorded
        joined.coachFrom = 700
        expect(recorded.subtitle == "12:00 · הקלטה בלבד" && joined.subtitle == "12:00 · אין סיכום",
               "a recording the coach joined is not only a recording \(joined.subtitle)", &p)
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "Asia/Jerusalem")!
        let now = c.date(from: DateComponents(year: 2026, month: 11, day: 15, hour: 18))!
        let today = c.date(from: DateComponents(year: 2026, month: 11, day: 15, hour: 9))!
        let yesterday = c.date(from: DateComponents(year: 2026, month: 11, day: 14, hour: 23))!
        let older = c.date(from: DateComponents(year: 2026, month: 11, day: 3, hour: 9))!
        expect([today, yesterday, older].map { CallLibrary.dayTitle($0, now: now, calendar: c) } == ["היום", "אתמול", "3.11"],
               "day titles", &p)
        store.lang = .en
        expect(calls[2].title == "Validation" && CallLibrary.dayTitle(yesterday, now: now, calendar: c) == "Yesterday",
               "English rows", &p)
        store.lang = .he
        let days = CallLibrary.days(calls, now: c.date(from: DateComponents(year: 2026, month: 11, day: 15, hour: 20))!)
        expect(days.map(\.calls.count).reduce(0, +) == 3 && days.count <= 3, "days \(days.map(\.title))", &p)
        let local = Calendar.current
        let sameEvening = local.date(from: DateComponents(year: 2026, month: 11, day: 15, hour: 20))!
        let weekLater = local.date(from: DateComponents(year: 2026, month: 11, day: 22, hour: 9))!
        let dated = calls.map { CallLibrary.datedLine($0, now: sameEvening) }
        expect(dated == ["היום 12:00 · אין סיכום", "היום 09:00 · 2 מושגים", "אתמול 10:00 · אין סיכום"], "dated lines \(dated)", &p)
        expect(CallLibrary.datedLine(calls[1], now: weekLater) == "15.11 09:00 · 2 מושגים", "an older call's date", &p)
        store.lang = .en
        expect(CallLibrary.datedLine(calls[2], now: sameEvening) == "Yesterday 10:00 · No summary", "English dated line", &p)
        store.lang = .he
        expect(CallLibrary.entry(dir.appendingPathComponent("2026-11-15_0900_general"))?.terms == 2
               && CallLibrary.entry(dir.appendingPathComponent("notes")) == nil, "one call folder", &p)

        let work = AppAccount(name: "work", kind: .subscription)
        let checked = AccountState(ok: true, provider: "claude", name: "work", viaClaudeCode: true, message: "")
        let failed = AccountState(ok: false, provider: "claude", name: "work", viaClaudeCode: true, message: "expired")
        expect(AccountLine.problem(active: nil, agent: "off", check: nil)?.color == .orange, "no account is told", &p)
        expect(AccountLine.problem(active: work, agent: "ok", check: nil) == nil
               && AccountLine.problem(active: work, agent: "unavailable", check: nil) == nil
               && AccountLine.problem(active: work, agent: "ok", check: checked) == nil, "an account that works is not told", &p)
        expect(AccountLine.problem(active: work, agent: "ok", check: failed)?.text == "החשבון work לא מחובר: expired"
               && AccountLine.problem(active: work, agent: "auth", check: nil)?.color == .red, "a rejected account is red", &p)
        expect(MicLine.problem(status: .authorized, hasDevice: true) == nil && MicLine.problem(status: .notDetermined, hasDevice: true) == nil
               && MicLine.problem(status: .denied, hasDevice: true)?.contains("אין גישה") == true
               && MicLine.problem(status: .authorized, hasDevice: false)?.contains("לא נמצא") == true, "microphone lines", &p)
        store.lang = .en
        expect(AccountLine.problem(active: work, agent: "auth", check: nil)?.text == "The account work was rejected, sign in again",
               "English account line", &p)
        expect(!AccountLine.accountsTitle.isEmpty, "the account line's button", &p)
        let labels = [callTypeLabels, kindLabels, PanelMenu.languageLabels].flatMap(\.values)
            + AccountKind.allCases.map(\.label) + Chip.allCases.map(\.title)
            + [HearingChip.onText, HearingChip.offText, HearingChip.shareText(0.4), RecapView.failedText, RecapView.emptyText,
               RecapView.nothingNewText, RecapView.updateFailedText, RecapView.sinceText("14:05"), PanelMenu.languageTitle, FollowUpBox.title, FollowUpBox.writingText, IdlePanel.title, HomeView.recordTitle, PanelView.finishingText, PanelView.finishingSummaryText]
            + SettingsPage.allCases.map(\.title) + MainView.Mode.allCases.map(\.title)
            + [HomeView.openPanelTitle, HomeView.stopTitle, HomeView.startTitle, HomeView.startCallTitle, HomeView.nowTitle,
               HomeView.nextTitle, HomeView.recentTitle, HomeView.noBriefsText, HomeView.prepareTitle, HomeView.allBriefsTitle,
               HomeView.noCallsText, HomeView.idleLine]
        expect(labels.allSatisfy { $0.unicodeScalars.allSatisfy { !(0x0590...0x05FF).contains($0.value) } },
               "English labels \(labels)", &p)
        store.lang = .he
        return p
    }
}
