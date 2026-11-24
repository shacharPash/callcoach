import AppKit
import Combine
import SwiftUI

/// "הכן תדריך" writing a brief, or the request that failed, kept for "נסה שוב".
enum BriefJob: Equatable {
    case making(BriefRequest)
    case failed(BriefRequest, String)
}

/// The prep sheet's start for a follow-up: its text, the files to attach and the call it follows.
struct PrepDraft: Equatable {
    var text = ""
    var files: [URL] = []
    var after: String?

    /// "שיחת המשך ל<call>.", with a hyphen before a name that does not start with a Hebrew letter.
    static func text(_ title: String) -> String {
        let hebrew = title.unicodeScalars.first.map { (0x0590...0x05FF).contains($0.value) } ?? false
        return L("שיחת המשך ל\(hebrew ? "" : "-")\(title).", "A follow-up to \(title).")
    }
}

/// "טיוטת מייל" of one call being written, or why it was not; a draft that was written is the call's followup.md.
enum FollowUpJob: Equatable {
    case writing
    case failed(String)
}

final class AppState: ObservableObject {
    @Published private(set) var model = CallModel()
    /// Start was pressed and the call has not begun; reset by a new engine and by the end of the call.
    @Published var starting = false
    /// What the engine was last told about translating.
    private var translating = false
    /// What the engine was last told about the panel being seen; every call starts seen.
    private var engineVisible = true
    /// "פאנל השיחה" was pressed with no call: the panel shows "אין שיחה פעילה כרגע" until it is closed or a call starts.
    @Published var idlePanel = false
    /// The language picked in the panel: auto, he or en.
    @Published var language = "auto"
    @Published var accountsChanged = 0
    /// Why the active account is not used for the engine, for example a Keychain read that failed.
    @Published var accountProblem: String?
    /// Context added during the call, shown again in the context window.
    @Published var contextNotes: [String] = []
    /// The main window's one-line field: the call's context and, with no brief, its title. A call that begins clears it.
    @Published var topic = ""
    /// The briefs in the data folder and the ones a call used, read again when the main window or the menu needs them.
    @Published private(set) var briefs: [Brief] = []
    @Published private(set) var usedBriefs: Set<String> = []
    /// The brief he picked instead of the next one, or the one just prepared.
    @Published var chosenBrief: String?
    @Published private(set) var briefJob: BriefJob?
    @Published var prepSheetOpen = false
    /// What "הכן תדריך לשיחת המשך…" puts in the prep sheet; the sheet takes it when it opens.
    @Published var prepDraft: PrepDraft?
    /// The brief the main window shows in its briefs tab: the one just prepared, or the one a call page opened.
    @Published var selectBrief: String?
    /// The follow-up drafts being written or that failed, by call folder path. A call page that is left and opened
    /// again finds its draft still being written.
    @Published private(set) var followUps: [String: FollowUpJob] = [:]
    /// Changes each time a draft is written, so the page reads followup.md again.
    @Published private(set) var followUpsWritten = 0
    /// The call the main window shows when it opens, for a notification he clicked.
    @Published var selectCall: String?
    let accounts: AccountStore
    let stream: URL?
    let speed: Double
    var data = Paths.data
    /// Where a new AppState keeps the transcript chip and the chips of the last call with the coach. The self-check
    /// points it at a throwaway store before any check runs, so no check writes the app's own settings.
    static var store = UserDefaults.standard
    var defaults = AppState.store
    /// The app used last before CallCoach, kept by the app delegate, so a shot finds the call's window.
    var lastApp: String?
    /// Takes the call's window into a folder; the self-check replaces it.
    var takeScreen: (String?, URL, @escaping (Result<URL, ShotFailure>) -> Void) -> Void = {
        ScreenShot.take(last: $0, folder: $1, done: $2)
    }
    var onSummary: (SummaryReady) -> Void = { _ in }
    var onFatal: (String) -> Void = { _ in }
    /// Makes the engine channel; the self-check replaces it with a fake one.
    var makeChannel: (() throws -> EngineChannel)?
    /// Runs the brief command and returns a way to cancel it; the self-check replaces it.
    var runBrief: ((BriefRequest, @escaping (BriefOutcome) -> Void) -> () -> Void)?
    /// Runs the follow-up command for a call folder; the self-check replaces it.
    var runFollowUp: ((URL, @escaping (BriefOutcome) -> Void) -> Void)?
    /// Asks for the microphone before a call when macOS has not asked yet, so the prompt never holds up the
    /// first seconds of the call; the self-check replaces it.
    var askMicrophone: (@escaping () -> Void) -> Void = { go in
        guard Permissions.microphone == .notDetermined else { return go() }
        Permissions.requestMicrophone { _ in go() }
    }
    private var engine: EngineChannel?
    private var retired: [ObjectIdentifier: EngineChannel] = [:]
    private var timer: Timer?
    private var stopBrief: (() -> Void)?
    private var briefRuns = 0

    init(accounts: AccountStore = AccountStore(), stream: URL?, speed: Double) {
        self.accounts = accounts
        self.stream = stream
        self.speed = speed
        let timer = Timer(timeInterval: 0.5, repeats: true) { [weak self] _ in self?.tick() }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    var now: Double { engine?.now() ?? Date().timeIntervalSince1970 }
    var inCall: Bool { model.phase == .active || model.phase == .stopping }

    func ensureEngine() {
        if engine != nil && model.phase == .ended { retire() }
        guard engine == nil else { return }
        model = CallModel()
        contextNotes = []
        starting = false
        language = "auto"
        accountProblem = nil
        do {
            let channel = try makeChannel?() ?? defaultChannel()
            channel.onEvent = { [weak self] in self?.handle($0) }
            channel.onExit = { [weak self] in self?.exited(clean: $0) }
            engine = channel
            try channel.start()
        } catch {
            engine = nil
            onFatal(L("המנוע לא עלה (\(error.localizedDescription)). צריך להריץ את setup.sh.",
                      "The engine did not start (\(error.localizedDescription)). Run setup.sh."))
        }
    }

    private func defaultChannel() throws -> EngineChannel {
        if let stream { return try StreamPlayer(file: stream, speed: speed) }
        let chosen = Self.engineAccount(accounts.active, readKey: AccountStore.readKey)
        accountProblem = chosen.problem
        if let problem = chosen.problem { AppLog.write(problem) }
        return EngineLink.serve(environment: EngineLink.environment(account: chosen.account, apiKey: chosen.key))
    }

    /// The account the engine runs with. An API account whose key cannot be read runs with no agent.
    static func engineAccount(_ active: AppAccount?, readKey: (String) -> (key: String?, status: OSStatus))
        -> (account: AppAccount?, key: String?, problem: String?) {
        guard let active, active.kind == .apiKey else { return (active, nil, nil) }
        let read = readKey(active.name)
        if let key = read.key { return (active, key, nil) }
        if read.status == errSecItemNotFound {
            return (nil, nil, L("המפתח של \(active.shown) לא נמצא ב-Keychain. השיחה תתומלל בלי סוכן עד שתוסיף אותו מחדש בהגדרות, בעמוד החשבונות.",
                                "The key for \(active.shown) is not in the Keychain. The call will be transcribed without an agent until you add it again in Settings, on the Accounts page."))
        }
        return (nil, nil, L("לא הצלחתי לקרוא את המפתח של \(active.shown) מה-Keychain (קוד \(read.status)). השיחה תתומלל בלי סוכן.",
                            "Could not read the key for \(active.shown) from the Keychain (code \(read.status)). The call will be transcribed without an agent."))
    }

    /// Detaches the current engine and lets it end on its own; it is kept until it exits.
    private func retire() {
        guard let old = engine else { return }
        engine = nil
        let id = ObjectIdentifier(old)
        retired[id] = old
        old.onEvent = { _ in }
        old.onExit = { [weak self] _ in self?.retired[id] = nil }
        old.closeInput()
        DispatchQueue.main.asyncAfter(deadline: .now() + 15) { [weak old] in old?.terminate() }
    }

    func send(_ command: Command) {
        if case .stop(let summary) = command { model.stopSummary = summary }
        engine?.send(command)
    }

    private func handle(_ event: Event) {
        update { $0.apply(event, now: now) }.forEach(send)
        switch event {
        case .hello where model.fatal != nil:
            engine?.terminate()
            onFatal(model.fatal ?? "")
        case .ready:
            topic = ""
            chosenBrief = nil
            reloadBriefs()
            refreshRecap()
        case .stopped:
            rememberChips()
            retire()
            starting = false
            reloadBriefs()
        case .summaryReady(let s):
            onSummary(s)
        case .usage(let u):
            UsageMemory.save(u)
        case .error(let e) where e.code == "no_agent_account":
            send(.setSections(model.sections))
        case .screen(let s) where s.ok:
            contextNotes.append(L("מהמסך: ", "From the screen: ") + s.text)
        default:
            break
        }
        syncVisibility()
        syncTranslation()
    }

    /// The engine translates the other side's English lines only while translation and the transcript are on and
    /// the panel is shown and unfolded.
    static func wantsTranslation(_ m: CallModel) -> Bool {
        m.phase == .active && m.chips[.translate] && m.chips[.transcript] && m.visible && !m.folded
    }

    /// Tells the engine when that changes.
    func syncTranslation() {
        guard model.phase == .active else { translating = false; return }
        let want = Self.wantsTranslation(model)
        guard want != translating else { return }
        translating = want
        send(.setTranslate(want))
    }

    /// What the engine hears as visible: shown and unfolded. Nothing is asked of the model for a panel that cannot
    /// be read.
    static func engineVisible(_ m: CallModel) -> Bool { m.visible && !m.folded }

    /// Tells the engine when that changes. A recording starts folded, so its call hears "not visible" with `ready`.
    func syncVisibility() {
        guard model.phase == .active else { return }
        let want = Self.engineVisible(model)
        guard want != engineVisible else { return }
        engineVisible = want
        send(.setVisibility(want))
    }

    /// The AI chips the next call with the coach starts with: the ones on at the end of a call that had the coach,
    /// or, when it was switched off before the end, the ones of the last time it was on. A call that only recorded
    /// leaves them as they were. The transcript chip is kept at the end of every call.
    static func chipsToRemember(_ m: CallModel) -> Set<Chip>? {
        guard m.coachEver else { return nil }
        let ai = m.chips.on.intersection(Chip.ai)
        return ai.isEmpty ? m.lastCoachChips : ai
    }

    private func rememberChips() {
        if let chips = Self.chipsToRemember(model) { Settings.setCoachChips(chips, defaults) }
        Settings.setTranscriptInPanel(model.chips[.transcript], defaults)
    }

    private func exited(clean: Bool) {
        engine = nil
        let wasCall = inCall
        let folder = model.folder
        model.phase = .ended
        if wasCall { rememberChips() }
        starting = false
        guard !clean, stream == nil else { return }
        if wasCall, let folder {
            recover { [weak self] in
                self?.onSummary(SummaryReady(ok: false, path: folder + "/summary.md",
                                             message: L("השיחה נקטעה כי המנוע נפל. ההקלטה שוחזרה בתיקייה.",
                                                        "The call was cut off because the engine crashed. The recording was recovered in the folder.")))
            }
        } else {
            let log = TextDirection.isolated("~/.callcoach/logs/engine-stderr.log")
            onFatal(L("המנוע נסגר לפני השיחה. הלוג נמצא בקובץ:\n\(log)", "The engine closed before the call. The log is in:\n\(log)"))
        }
    }

    func recover(_ done: @escaping () -> Void = {}) {
        guard stream == nil else { return done() }
        DispatchQueue.global().async {
            let process = Process()
            process.executableURL = Paths.engine
            process.arguments = ["recover", "--data", Paths.data.path]
            process.environment = EngineLink.baseEnvironment()
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            try? process.run()
            process.waitUntilExit()
            DispatchQueue.main.async(execute: done)
        }
    }

    private func tick() {
        update { $0.tick(now: now) }.forEach(send)
    }

    func tickForCheck() {
        tick()
    }

    /// The model is published only when it changed, so an event or a tick that changes nothing redraws no window.
    /// File private so the self-check can clear a notice.
    fileprivate func update(_ change: (inout CallModel) -> [Command]) -> [Command] {
        var next = model
        let commands = change(&next)
        if next != model { model = next }
        return commands
    }

    /// Starts a call now: with the brief, else from the field's text alone.
    func startCall(brief: Brief?) {
        guard !inCall, !starting else { return }
        ensureEngine()
        guard engine != nil else { return }
        start(StartFields(brief: brief, text: topic, level: Settings.defaultLevel))
    }

    /// "הקלטה בלבד": the recording and the live transcript, with no agent, no summary and no brief.
    func startRecording() {
        guard !inCall, !starting else { return }
        ensureEngine()
        guard engine != nil else { return }
        var fields = StartFields(brief: nil, text: topic, level: Settings.defaultLevel)
        fields.recordOnly = true
        start(fields)
    }

    /// Every call starts recording and hearing me. A call with the coach starts with the AI chips of the last call
    /// that had it, a recording with none and folded to its strip; the transcript chip is as the last call left it.
    /// The start carries the sections of those chips, since a call with the coach can start without questions or terms.
    func start(_ fields: StartFields) {
        let replay = ProcessInfo.processInfo.environment["CALLCOACH_REPLAY"]
        starting = true
        model.recordOnly = fields.recordOnly
        model.recentLines = []
        model.translations = [:]
        translating = false
        engineVisible = true
        model.hearing = true
        model.hearingClick = nil
        model.paused = false
        model.pausedClick = nil
        model.chips = CallModel.startChips(recording: fields.recordOnly, transcript: Settings.transcriptInPanel(defaults),
                                           remembered: Settings.coachChips(defaults))
        model.nowLineOn = Settings.nowLine(defaults)
        model.nowLine = nil
        model.listOnly = Settings.termsListOnly(defaults)
        model.coachEver = !fields.recordOnly
        model.lastCoachChips = model.chips.on.intersection(Chip.ai)
        model.folded = fields.recordOnly
        var sent = fields
        sent.sections = model.sections
        let command = Command.start(sent, replay: replay)
        let begin = { [weak self] in guard let self else { return }; self.send(command) }
        guard replay == nil else { return begin() }
        let asking = engine.map(ObjectIdentifier.init)
        askMicrophone { [weak self] in
            guard let self, self.starting, self.engine.map(ObjectIdentifier.init) == asking else { return }
            begin()
        }
    }

    func briefChoice(now: Date = Date()) -> BriefChoice {
        Briefs.choose(briefs, used: usedBriefs, chosen: chosenBrief, now: now)
    }

    func reloadBriefs() {
        let loaded = Briefs.load(data, types: Set(CallTypes.all(data: data)))
        let used = Briefs.used(data)
        if loaded != briefs { briefs = loaded }
        if used != usedBriefs { usedBriefs = used }
    }

    /// Writes a brief in the background. The new brief becomes the one shown; a failure keeps the request for a retry.
    func makeBrief(_ request: BriefRequest) {
        stopBrief?()
        briefRuns += 1
        let id = briefRuns
        briefJob = .making(request)
        let run = runBrief ?? engineBrief
        stopBrief = run(request) { [weak self] outcome in
            guard let self, self.briefRuns == id else { return }
            self.stopBrief = nil
            switch outcome {
            case .made(let path):
                let file = URL(fileURLWithPath: path).lastPathComponent
                if let after = request.after { Self.mark(file, after: after, data: self.data) }
                self.briefJob = nil
                self.chosenBrief = file
                self.selectBrief = file
                self.reloadBriefs()
            case .failed(let message):
                self.briefJob = .failed(request, message)
            }
        }
    }

    /// Writes `after:` into a brief the AI just made; a brief that cannot be read or written is left as it is.
    static func mark(_ file: String, after: String, data: URL) {
        guard var found = BriefStore.load(data, file) else { return }
        found.doc.after = after
        do {
            try BriefStore.save(found.doc, data: data, file: file, read: nil)
        } catch {
            AppLog.write("could not mark \(file) as a follow-up of \(after): \(error)")
        }
    }

    /// "הכן תדריך לשיחת המשך…": the prep sheet opens with the call named in its text and the call's summary and brief attached.
    func followUp(_ call: CallEntry) {
        var files: [URL] = []
        if call.hasSummary { files.append(call.summary) }
        if let brief = call.brief {
            let url = BriefStore.url(data, brief)
            if FileManager.default.fileExists(atPath: url.path) { files.append(url) }
        }
        prepDraft = PrepDraft(text: PrepDraft.text(call.title), files: files, after: call.id)
        prepSheetOpen = true
    }

    func cancelBrief() {
        briefRuns += 1
        stopBrief?()
        stopBrief = nil
        briefJob = nil
    }

    func retryBrief() {
        guard case .failed(let request, _) = briefJob else { return }
        makeBrief(request)
    }

    private func engineBrief(_ request: BriefRequest, _ done: @escaping (BriefOutcome) -> Void) -> () -> Void {
        let chosen = Self.engineAccount(accounts.active, readKey: AccountStore.readKey)
        if let problem = chosen.problem {
            DispatchQueue.main.async { done(.failed(problem)) }
            return {}
        }
        let run = BriefRun.engine(account: chosen.account, apiKey: chosen.key)
        run.start(request.line(), done: done)
        return run.cancel
    }

    /// Writes the follow-up email of a call in the background; a failure keeps its message for "נסה שוב".
    func writeFollowUp(_ folder: URL) {
        let key = folder.path
        guard followUps[key] != .writing else { return }
        followUps[key] = .writing
        let run = runFollowUp ?? engineFollowUp
        run(folder) { [weak self] outcome in
            guard let self else { return }
            switch outcome {
            case .made:
                self.followUps[key] = nil
                self.followUpsWritten += 1
            case .failed(let message):
                self.followUps[key] = .failed(message)
            }
        }
    }

    private func engineFollowUp(_ folder: URL, _ done: @escaping (BriefOutcome) -> Void) {
        let chosen = Self.engineAccount(accounts.active, readKey: AccountStore.readKey)
        if let problem = chosen.problem {
            DispatchQueue.main.async { done(.failed(problem)) }
            return
        }
        BriefRun.followUp(account: chosen.account, apiKey: chosen.key, folder: folder).start("", done: done)
    }

    func addContext(_ text: String) {
        guard model.phase == .active else { return }
        contextNotes.append(text)
        send(.addContext(text))
        model.noted(now: now)
    }

    /// The note says "לא בכיוון" in Hebrew whatever the interface language: the live rules read it that way.
    static func notThisWay(_ question: Question) -> String {
        "לא בכיוון: " + question.text
    }

    /// "לא בכיוון" on a live question: it leaves the panel, and a note asks the coach for questions in another
    /// direction.
    func notThisWay(_ question: Question) {
        guard model.dropQuestion(question) else { return }
        addContext(Self.notThisWay(question))
    }

    /// "לא בכיוון" on a question I asked for, in the slot: the same, for the card.
    func notThisWay(card: Card) {
        guard model.dropCard(card) else { return }
        addContext("לא בכיוון: " + card.text)
    }

    /// "בכיוון", in Hebrew for the live rules as "לא בכיוון" is: more questions like this one.
    static func onTrack(_ text: String) -> String {
        "בכיוון: " + text
    }

    /// "בכיוון" on a live question or on a question card: the question stays, and a note asks the coach for more like
    /// it. A second press on the same question tells the coach nothing new, so it only shows that it was sent.
    func onTrack(_ text: String) {
        guard model.phase == .active else { return }
        let note = Self.onTrack(text)
        if contextNotes.last == note { model.noted(now: now); return }
        addContext(note)
    }

    func toggleFold() {
        model.toggleFold()
        syncVisibility()
        syncTranslation()
        if !model.folded { refreshRecap() }
    }

    /// Lets an engine that holds no call end, as when the window that started it for an account check closes.
    func releaseEngine() {
        if !inCall && !starting { retire() }
    }

    func stop() {
        guard model.phase == .active, model.stopSummary == nil else { return }
        model.stopConfirmUntil = nil
        send(.stop(summary: model.summaryByDefault))
    }

    /// The panel's Stop: the first click opens the second step, where one of two buttons stops the call.
    func pressStop() {
        model.pressStop(now: now).forEach(send)
    }

    /// A button of the second step: stops the call with or without the summary.
    func confirmStop(summary: Bool) {
        update { $0.confirmStop(summary: summary) }.forEach(send)
    }

    /// "ביטול" in the second step.
    func cancelStop() {
        model.stopConfirmUntil = nil
    }

    /// X in the panel's header: hides the panel in a call as the hide key does; with no call, closes the idle panel.
    func closePanel() {
        if inCall { setVisible(false) } else { idlePanel = false }
    }

    func perform(_ action: AppAction) {
        switch action.id {
        case "ask_now":
            pressNow { $0.askNow(now: now) }
        case "terms_now":
            pressNow { $0.termsNow(now: now) }
        case "screen":
            shootScreen()
        case "mark":
            if model.phase == .active { send(.action(action.id)) }
        case "hide":
            if inCall { setVisible(!model.visible) }
        default:
            break
        }
    }

    /// "שאלה עכשיו" and the terms search. One that turned its chip on unfolded the panel, and the engine hears that
    /// it is seen before the request, so the answer is not held back for a hidden panel.
    private func pressNow(_ press: (inout CallModel) -> [Command]) {
        let wasFolded = model.folded
        let commands = update(press)
        syncVisibility()
        commands.forEach(send)
        syncTranslation()
        if wasFolded && !model.folded { refreshRecap() }
    }

    func togglePick(_ spot: TermPicker.Spot) {
        model.togglePick(spot, now: now)
    }

    func checkPicked() {
        model.checkPicked().forEach(send)
    }

    func closePicker() {
        model.closePicker()
    }

    /// The shot key: the call's window goes to the engine at once with no note. The panel says so, then what the
    /// screen shows comes as a note.
    func shootScreen() {
        takeShot { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let url): self.sendScreen(url, text: "")
            case .failure(let failure): self.model.screenFailed(failure.message, now: self.now)
            }
        }
    }

    /// Takes the call's window into the call folder's `screens`; nothing outside a call.
    func takeShot(_ done: @escaping (Result<URL, ShotFailure>) -> Void) {
        guard model.phase == .active, let folder = model.folder else { return }
        takeScreen(lastApp, URL(fileURLWithPath: folder).appendingPathComponent("screens"), done)
    }

    /// A picture I pasted in the context window, kept with the call's shots.
    func keepPicture(_ image: CGImage) -> URL? {
        guard model.phase == .active, let folder = model.folder else { return nil }
        return try? ScreenShot.save(image, folder: URL(fileURLWithPath: folder).appendingPathComponent("screens"))
    }

    /// A shot of the call's window, and a note with it that may be empty, goes to the engine.
    func sendScreen(_ url: URL, text: String) {
        guard model.phase == .active else { return }
        if !text.isEmpty { contextNotes.append(text) }
        send(.addScreen(path: url.path, text: text))
        model.screenSent(now: now)
    }

    /// "העתק את התמלול" during a call: every line so far, as the call page copies it.
    func copyTranscript(to board: NSPasteboard = .general) {
        guard inCall, let folder = model.folder else { return }
        let lines = CallTranscript.load(URL(fileURLWithPath: folder))
        CallTranscript.copy(lines, to: board)
        model.transcriptCopied(lines.count, now: now)
    }

    func toggleTerm(_ id: Int) {
        model.toggleTerm(id).forEach(send)
    }

    func askMore(_ id: Int) {
        model.askMore(id).forEach(send)
    }

    /// "הוסף לידע" on an open row: the term goes to the calls' knowledge file now, as the call page adds it after the
    /// call, with its sentences when they came. The engine reads knowledge files when a call starts, so it is known
    /// from the next call.
    func addToKnowledge(_ id: Int) {
        guard let term = model.terms.first(where: { $0.id == id }), term.known != true else { return }
        var detail: String?
        if case .ready(let text)? = model.termDetails[id] { detail = text }
        model.knowledgeAdds[id] = Self.add(term, detail: detail, folder: model.folder, data: data)
    }

    /// "אני מכיר, לא להקפיץ" on an open row: the name goes to the list the engine reads when a call starts, and the
    /// row goes from the panel now.
    func ignoreTerm(_ id: Int) {
        guard let term = model.terms.first(where: { $0.id == id }) else { return }
        let saved = (try? IgnoreList.add(term.term, data: data)) != nil
        model.hideTerm(id, saved: saved, now: now)
    }

    static func add(_ term: Term, detail: String?, folder: String?, data: URL) -> KnowledgeAdd {
        guard let folder, let call = CallLibrary.entry(URL(fileURLWithPath: folder)) else { return .failed }
        let name = CallTerm(term: term.term, note: term.note, kind: term.kind, known: false, detail: detail)
        guard let added = try? NewNames.add(name, from: call, data: data) else { return .failed }
        return added ? .added : .known
    }

    func toggleGoals() {
        model.goalsOpen.toggle()
    }

    func toggleGoal(_ id: String) {
        model.toggleGoal(id).forEach(send)
    }

    /// "שאל על זה": a goal put in focus may turn questions on and unfold the panel, so it goes the way a question now does.
    func focus(_ id: String) {
        pressNow { $0.focus(id) }
    }

    func toggleHearing() {
        model.toggleHearing(now: now).forEach(send)
    }

    func togglePause() {
        model.togglePause(now: now).forEach(send)
    }

    func toggleChip(_ chip: Chip) {
        changeChips { $0.toggleChip(chip) }
    }

    func toggleCoach() {
        let remembered = Settings.coachChips(defaults)
        changeChips { $0.toggleCoach(remembered: remembered) }
    }

    /// Tells the engine what the chips changed and keeps the transcript chip for the next call; translation follows.
    /// "סיכום ביניים" turned on asks for its update last, after the engine heard the coach on.
    private func changeChips(_ change: (inout CallModel) -> [Command]) {
        let transcript = model.chips[.transcript]
        let recap = model.chips[.recap]
        update(change).forEach(send)
        if model.chips[.transcript] != transcript { Settings.setTranscriptInPanel(model.chips[.transcript], defaults) }
        syncVisibility()
        syncTranslation()
        if model.chips[.recap] && !recap { refreshRecap() }
    }

    /// The round arrow of "סיכום ביניים", and what asks for its update when it comes into view.
    func refreshRecap() {
        update { $0.refreshRecap() }.forEach(send)
    }

    func retryRecap() {
        model.askRecap().forEach(send)
    }

    /// The now line's switch on "סיכום ביניים"; the next call starts with it as it is left.
    func toggleNowLine() {
        update { $0.toggleNowLine() }.forEach(send)
        Settings.setNowLine(model.nowLineOn, defaults)
    }

    /// "רק מהרשימה" on "מושגים"; the next call starts with it as it is left.
    func toggleListOnly() {
        update { $0.toggleListOnly() }.forEach(send)
        Settings.setTermsListOnly(model.listOnly, defaults)
    }

    func toggleMenu() {
        model.menuOpen.toggle()
    }

    func toggleUsage() {
        model.usageOpen.toggle()
    }

    /// A choice in the panel's "…" list closes it, then does what it says.
    func fromMenu(_ act: () -> Void) {
        model.menuOpen = false
        act()
    }

    func setVisible(_ visible: Bool) {
        let wasVisible = model.visible
        let shown = model.setVisible(visible, now: now)
        syncVisibility()
        shown.forEach(send)
        syncTranslation()
        if visible && !wasVisible { refreshRecap() }
    }

    /// Automatic lets detection decide again; Hebrew or English holds the language for the rest of the call.
    func setLanguage(_ lang: String) {
        guard model.phase == .active else { return }
        send(.setLanguage(lang))
        language = lang
    }

    func checkAccount() {
        ensureEngine()
        send(.accountCheck)
    }

    func switchAccount(_ name: String) {
        guard !inCall else { return }
        try? accounts.activate(name)
        accountsChanged += 1
        guard engine != nil else { return }
        retire()
        ensureEngine()
    }

    func shutdown() {
        if inCall { rememberChips() }
        retire()
    }
}

/// An engine channel the self-check drives by hand.
final class FakeChannel: EngineChannel {
    var onEvent: (Event) -> Void = { _ in }
    var onExit: (Bool) -> Void = { _ in }
    private(set) var sent: [Command] = []
    private(set) var inputClosed = false
    private(set) var terminated = false

    func start() throws {}
    func send(_ command: Command) { sent.append(command) }
    func closeInput() { inputClosed = true }
    func terminate() { terminated = true }
    func now() -> Double { 1000 }

    func emit(_ json: String) {
        if let (event, _) = try? Event.decode(json) { onEvent(event) }
    }
}

extension SelfCheck {
    static func stateChecks(_ root: URL) -> [String] {
        var p: [String] = []
        var call = CallModel()
        call.phase = .active
        call.chips = Chips(on: [.transcript, .translate])
        expect(AppState.wantsTranslation(call), "an open panel with transcript and translation translates", &p)
        var noTranscript = call
        noTranscript.chips = Chips(on: [.translate])
        var folded = call
        folded.toggleFold()
        var hidden = call
        hidden.visible = false
        var ended = call
        ended.phase = .ended
        expect([noTranscript, folded, hidden, ended].allSatisfy { !AppState.wantsTranslation($0) },
               "no translation without the transcript, folded or hidden, or after the call", &p)
        expect(AppState.engineVisible(folded) == false && AppState.engineVisible(call) && !AppState.engineVisible(hidden),
               "folded counts as hidden for the engine", &p)
        var kept = CallModel()
        kept.chips = Chips(on: [.transcript])
        expect(AppState.chipsToRemember(kept) == nil, "a call that only recorded leaves the remembered chips", &p)
        kept.coachEver = true
        kept.lastCoachChips = [.translate]
        expect(AppState.chipsToRemember(kept) == [.translate],
               "a coach switched off before the end leaves the chips of the last time it was on", &p)
        kept.chips = Chips(on: [.transcript, .recap])
        expect(AppState.chipsToRemember(kept) == [.recap], "a call with the coach leaves its AI chips at the end", &p)
        let fm = FileManager.default
        let tools = fm.temporaryDirectory.appendingPathComponent("callcoach-state-\(getpid())")
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
        var channels: [FakeChannel] = []
        var fatals: [String] = []
        state.onFatal = { fatals.append($0) }
        var asked: [() -> Void] = []
        state.askMicrophone = { asked.append($0) }
        state.makeChannel = {
            let channel = FakeChannel()
            channels.append(channel)
            return channel
        }
        let hello = #"{"type": "hello", "protocol": \#(protocolVersion), "version": "x"}"#
        func status(_ s: String) -> String {
            #"{"type": "status", "state": "\#(s)", "elapsed": 0, "talk_share": 0, "lang_me": "he", "lang_them": "he", "conversation_lang": "he", "agent": "off", "level": "normal", "visible": true, "hearing": true, "me_voice": false}"#
        }
        func starts(_ channel: FakeChannel) -> [StartFields] {
            channel.sent.compactMap { if case .start(let f, _) = $0 { return f } else { return nil } }
        }

        state.ensureEngine()
        state.ensureEngine()
        expect(channels.count == 1, "one engine while it is open", &p)
        channels[0].emit(hello)
        channels[0].emit(status("idle"))
        state.releaseEngine()
        expect(channels[0].inputClosed, "an idle engine is let go", &p)
        channels[0].emit(status("warming"))
        channels[0].onExit(false)
        expect(state.model.phase == .idle && fatals.isEmpty, "a detached engine is not heard from", &p)

        state.topic = "  שיחה עם דנה מ-Acme  "
        state.startCall(brief: nil)
        expect(channels.count == 2 && state.starting && starts(channels[1]).isEmpty && asked.count == 1,
               "a call starts a new engine and waits for the microphone question", &p)
        state.releaseEngine()
        expect(!channels[1].inputClosed, "an engine with a call starting is kept", &p)
        state.startCall(brief: nil)
        expect(asked.count == 1, "a second start waits for the first", &p)
        asked.removeFirst()()
        let plain = starts(channels[1])
        expect(plain == [StartFields(callType: "general", lang: "auto", level: Settings.defaultLevel, text: "שיחה עם דנה מ-Acme",
                                     name: "general", title: "שיחה עם דנה מ-Acme", sections: Sections())], "a call from the field \(plain)", &p)
        expect(channels[1].sent.count == 1 && starts(channels[1]).first?.sections == Sections() && state.model.chips == Chips(on: [.ask, .terms])
               && state.model.coachEver && !state.model.folded, "a first call with the coach: questions and terms, carried by the start", &p)
        channels[1].emit(#"{"type": "ready", "folder": "/x/calls/2026-11-16_1000_general", "replay_t0": null}"#)
        expect(state.topic.isEmpty && state.model.phase == .active, "a call that begins clears the field", &p)
        let questionsEvent = #"{"type": "questions", "items": [{"text": "Why now?", "he": "למה עכשיו?"}], "lang": "en", "trigger_line_ids": []}"#
        channels[1].emit(questionsEvent)
        var redraws = 0
        let watch = state.objectWillChange.sink { redraws += 1 }
        channels[1].emit(questionsEvent)
        state.tickForCheck()
        watch.cancel()
        expect(redraws == 0, "an event and a tick that change nothing redraw nothing (\(redraws))", &p)
        channels[1].emit(questionsEvent)
        state.notThisWay(Question(text: "Why now?", he: "למה עכשיו?"))
        expect(channels[1].sent.last == .addContext("לא בכיוון: Why now?") && state.model.questions.isEmpty
               && state.contextNotes.last == "לא בכיוון: Why now?" && state.model.note?.title == "המאמן עודכן",
               "לא בכיוון drops the question and tells the coach", &p)
        channels[1].emit(questionsEvent)
        let notes = channels[1].sent.count
        _ = state.update { $0.note = nil; return [] }
        state.onTrack("Why now?")
        expect(channels[1].sent.last == .addContext("בכיוון: Why now?") && state.model.questions.count == 1
               && state.model.note?.title == "המאמן עודכן", "בכיוון keeps the question and tells the coach", &p)
        _ = state.update { $0.note = nil; return [] }
        state.onTrack("Why now?")
        expect(channels[1].sent.count == notes + 1 && state.model.note?.title == "המאמן עודכן",
               "בכיוון twice sends one note, and both presses show it was sent", &p)
        state.toggleHearing()
        state.toggleChip(.ask)
        expect(channels[1].sent.suffix(2) == [.setHearing(false), .setSections(Sections(ask: false, terms: true))]
               && !state.model.hearing && !state.model.chips[.ask], "the hearing switch and a chip from the panel", &p)
        state.togglePause()
        expect(channels[1].sent.last == .setPaused(true) && state.model.paused, "pause from the panel", &p)
        state.perform(Actions.ask)
        expect(channels[1].sent.suffix(2) == [.setSections(Sections()), .action("ask_now")] && state.model.chips[.ask],
               "asking now turns שאלות on first", &p)
        state.perform(Actions.ask)
        expect(channels[1].sent.suffix(2) == [.action("ask_now"), .action("ask_now")], "with שאלות on it only asks", &p)
        state.toggleCoach()
        channels[1].emit(#"{"type": "line", "id": 4, "segment_id": 4, "channel": "them", "lang": "he", "text": "דנה מספרת על סלאק", "t_start": 20, "speech_end": 22, "line_ready": 22, "conversation_lang": "he"}"#)
        state.toggleChip(.recap)
        expect(channels[1].sent.suffix(3) == [.setCoach(true), .setSections(Sections(ask: false, terms: false, now: true)), .recap]
               && state.model.recap == .asking, "the recap chip in a call with a line turns the coach on, then asks", &p)
        channels[1].emit(#"{"type": "recap", "ok": true, "text": "דנה מספרת על סלאק\nמפתח חיכה יומיים"}"#)
        expect(state.model.recap == .ready(["דנה מספרת על סלאק", "מפתח חיכה יומיים"]), "the recap arrives", &p)
        state.toggleChip(.ask)
        state.toggleChip(.terms)
        state.toggleFold()
        expect(channels[1].sent.last == .setVisibility(false) && state.model.folded, "a folded panel asks nothing", &p)
        state.toggleFold()
        expect(channels[1].sent.suffix(2) == [.setVisibility(true), .recapSince] && state.model.recapUpdate == .asking,
               "unfolded again it asks only for what is new, after the engine hears it is seen", &p)
        channels[1].emit(#"{"type": "recap", "ok": true, "text": "מארק ביקש הצעת מחיר", "since": "14:05"}"#)
        state.setVisible(false)
        expect(channels[1].sent.last == .setVisibility(false) && !state.model.visible, "a hidden panel tells the engine", &p)
        state.setVisible(true)
        expect(channels[1].sent.suffix(2) == [.setVisibility(true), .recapSince], "shown again it asks for what is new", &p)
        channels[1].emit(#"{"type": "recap", "ok": true, "text": "", "since": "14:10"}"#)
        state.refreshRecap()
        expect(channels[1].sent.last == .recapSince && state.model.recapUpdate == .asking, "the round arrow asks again", &p)
        state.perform(Actions.termsNow)
        expect(channels[1].sent.last == .action("terms_now") && state.model.spot == .searching, "the search from its key", &p)
        let liveData = tools.appendingPathComponent("data")
        let liveFolder = liveData.appendingPathComponent("calls/2026-11-16_1000_general")
        try? fm.createDirectory(at: liveFolder, withIntermediateDirectories: true)
        channels[1].emit(#"{"type": "term", "id": 9, "term": "SCIM", "note": "תקן לסנכרון משתמשים", "kind": "term", "lang": "he", "trigger_line_ids": [], "known": false, "ours": [], "ours_links": [], "file": ""}"#)
        channels[1].emit(#"{"type": "terms_now", "ok": true, "ids": [9]}"#)
        expect(state.model.spot == nil && state.model.freshTermId == 9, "the term found is pointed at", &p)
        channels[1].emit(#"{"type": "line", "id": 5, "segment_id": 5, "channel": "them", "lang": "he", "text": "ועכשיו SailPoint", "t_start": 30, "speech_end": 32, "line_ready": 32, "conversation_lang": "he"}"#)
        state.perform(Actions.termsNow)
        let pickWord = state.model.picker?.rows.last?.firstIndex(of: "SailPoint")
        if let pickWord, let row = state.model.picker.map({ $0.rows.count - 1 }) {
            state.togglePick(.init(row: row, word: pickWord))
            state.checkPicked()
        }
        expect(channels[1].sent.last == .checkTerms(["SailPoint"]) && state.model.picker == nil, "picked words from the panel", &p)
        channels[1].emit(#"{"type": "terms_now", "ok": true, "ids": [9], "picked": true}"#)
        state.perform(Actions.termsNow)
        state.closePicker()
        expect(state.model.picker == nil && state.model.spot == .searching, "closing the picker leaves the search", &p)
        var shots: [(String?, URL)] = []
        var shotResult: Result<URL, ShotFailure> = .failure(.noWindow)
        state.lastApp = "us.zoom.xos"
        state.takeScreen = { app, folder, done in
            shots.append((app, folder))
            done(shotResult)
        }
        state.perform(Actions.screen)
        expect(shots.last?.0 == "us.zoom.xos" && shots.last?.1.path == "/x/calls/2026-11-16_1000_general/screens"
               && state.model.note?.detail == "לא מצאתי את חלון השיחה", "a shot with no call window says so", &p)
        shotResult = .success(URL(fileURLWithPath: "/x/calls/2026-11-16_1000_general/screens/101500.jpg"))
        state.perform(Actions.screen)
        expect(channels[1].sent.last == .addScreen(path: "/x/calls/2026-11-16_1000_general/screens/101500.jpg", text: "")
               && state.model.note?.title == "צילמתי את חלון השיחה", "the shot key sends the call's window", &p)
        state.sendScreen(URL(fileURLWithPath: "/x/s.jpg"), text: "שקף התמחור")
        expect(channels[1].sent.last == .addScreen(path: "/x/s.jpg", text: "שקף התמחור") && state.contextNotes.last == "שקף התמחור",
               "a shot with a note from the context window", &p)
        channels[1].emit(#"{"type": "screen", "ok": true, "text": "שקף על תמחור לפי משתמש"}"#)
        expect(state.contextNotes.last == "מהמסך: שקף על תמחור לפי משתמש", "what the screen shows is kept as context", &p)
        state.addToKnowledge(9)
        expect(state.model.knowledgeAdds[9] == .failed, "a call folder that is not there cannot add", &p)
        let scim = state.model.terms.first { $0.id == 9 }
        let first = scim.map { AppState.add($0, detail: nil, folder: liveFolder.path, data: liveData) }
        let knowledgeText = (try? String(contentsOf: NewNames.file(liveData), encoding: .utf8)) ?? ""
        expect(first == .added && knowledgeText.contains("## SCIM\n") && knowledgeText.contains("תקציר: תקן לסנכרון משתמשים")
               && knowledgeText.contains("עלה בשיחה"), "a term added to the knowledge from the panel \(knowledgeText.suffix(160))", &p)
        let again = scim.map { AppState.add($0, detail: nil, folder: liveFolder.path, data: liveData) }
        expect(again == .known, "added again, it is already known", &p)
        state.ignoreTerm(9)
        expect(IgnoreList.load(state.data) == ["SCIM"] && !state.model.shownTerms.contains { $0.id == 9 }
               && state.model.note?.title == "לא אקפיץ יותר את SCIM", "a term marked as known from the panel", &p)
        let board = NSPasteboard(name: NSPasteboard.Name("callcoach-state-\(UUID().uuidString)"))
        state.copyTranscript(to: board)
        expect(state.model.note?.title == "אין עדיין תמלול להעתיק" && board.string(forType: .string) == nil,
               "no lines yet, nothing is copied", &p)
        board.releaseGlobally()
        state.pressStop()
        state.confirmStop(summary: true)
        expect(channels[1].sent.filter { $0 == .stop(summary: true) }.count == 1 && state.model.stopSummary == true,
               "a button of the second step stops", &p)
        let afterConfirm = channels[1].sent.count
        state.stop()
        state.pressStop()
        state.confirmStop(summary: false)
        expect(channels[1].sent.count == afterConfirm && state.model.stopSummary == true && state.model.stopConfirmUntil == nil,
               "a second stop after a confirmed one sends nothing and keeps the choice", &p)
        state.setLanguage("auto")
        expect(channels[1].sent.last == .setLanguage("auto") && state.language == "auto", "language from the panel", &p)
        channels[1].emit(#"{"type": "stopped"}"#)
        expect(state.model.phase == .ended && channels[1].inputClosed && !state.starting, "stopped detaches the engine", &p)

        let brief = Brief(file: "2026-11-16-acme.md", title: "Acme", callType: "sales", lang: "en", lengthMin: 30,
                          goals: ["להבין"], questions: ["What changed? | מה השתנה?"], glossary: ["Okta"])
        expect(Settings.coachChips(defaults) == [.ask, .terms, .recap], "a call with the coach leaves its AI chips for the next", &p)
        state.startCall(brief: brief)
        expect(state.model.hearing && !state.model.paused && state.model.chips == Chips(on: [.ask, .terms, .recap]),
               "the next call starts recording, hearing me, with the AI chips of the last call with the coach", &p)
        asked.removeFirst()()
        let briefed = starts(channels[2]).first
        expect(briefed?.brief == "2026-11-16-acme.md" && briefed?.title == "Acme" && briefed?.callType == "sales"
               && briefed?.questions == ["What changed? | מה השתנה?"] && briefed?.goals.map(\.text) == ["להבין"] && briefed?.text == "",
               "a call with a brief \(String(describing: briefed))", &p)
        channels[2].emit(#"{"type": "stopped"}"#)
        channels[2].onExit(true)
        expect(fatals.isEmpty, "an engine that ended is not an error", &p)
        state.startCall(brief: nil)
        state.releaseEngine()
        asked.removeFirst()()
        expect(starts(channels[3]).count == 1, "a start that waits for the microphone is not let go", &p)
        channels[3].emit(#"{"type": "ready", "folder": "/x/calls/2026-11-16_1100_general", "replay_t0": null}"#)
        expect(state.model.recap == .empty, "a call that starts with the recap chip has it say there is nothing yet", &p)
        let sectionsAtStart = starts(channels[3]).first?.sections
        expect(sectionsAtStart == Sections(ask: true, terms: true, now: true) && !channels[3].sent.contains { if case .setSections = $0 { return true } else { return false } },
               "the start carries the sections and nothing repeats them", &p)
        channels[3].emit(#"{"type": "line", "id": 1, "segment_id": 1, "channel": "them", "lang": "he", "text": "דנה מספרת על סלאק", "t_start": 1, "speech_end": 2, "line_ready": 2, "conversation_lang": "he"}"#)
        let beforeRecap = channels[3].sent.count
        state.setVisible(true)
        expect(channels[3].sent.count == beforeRecap, "showing a panel that is already shown asks for nothing", &p)
        state.stop()
        expect(channels[3].sent.last == .stop(summary: true), "a stop from the menu with the coach on summarizes", &p)
        channels[3].emit(#"{"type": "stopped"}"#)

        state.startRecording()
        asked.removeFirst()()
        expect(starts(channels[4]).first?.recordOnly == true && starts(channels[4]).first?.sections == Sections(ask: false, terms: false)
               && state.model.chips.on.isEmpty && state.model.folded && !state.model.coachEver,
               "a recording starts folded with no AI chip, the transcript as the last call left it", &p)
        channels[4].emit(#"{"type": "ready", "folder": "/x/calls/2026-11-16_1200_general", "replay_t0": null}"#)
        expect(channels[4].sent.last == .setVisibility(false), "a folded panel is not visible for the engine", &p)
        state.toggleFold()
        expect(channels[4].sent.last == .setVisibility(true), "unfolded it is", &p)
        state.stop()
        expect(channels[4].sent.last == .stop(summary: false), "a stop from the menu in a recording has no summary", &p)
        state.toggleChip(.translate)
        expect(channels[4].sent.suffix(3) == [.setCoach(true), .setSections(Sections(ask: false, terms: false)), .setTranslate(true)]
               && state.model.chips.on == [.translate, .transcript] && state.model.coachEver && Settings.transcriptInPanel(defaults),
               "translation in a recording turns the coach on with it alone, and the transcript is kept for the next call", &p)
        state.toggleFold()
        expect(channels[4].sent.suffix(2) == [.setVisibility(false), .setTranslate(false)], "folding stops the translation", &p)
        state.toggleCoach()
        expect(channels[4].sent.last == .setCoach(false) && state.model.chips.on == [.transcript], "מאמן off turns the AI chips off", &p)
        state.perform(Actions.ask)
        expect(channels[4].sent.suffix(4) == [.setVisibility(true), .setCoach(true), .setSections(Sections(ask: true, terms: false)),
                                              .action("ask_now")] && !state.model.folded && state.model.chips.on == [.transcript, .ask],
               "a question now in a folded recording unfolds the panel before it asks, so the answer can be read", &p)
        state.toggleCoach()
        expect(channels[4].sent.suffix(2) == [.setSections(Sections(ask: false, terms: false)), .setCoach(false)], "and מאמן off again", &p)
        channels[4].emit(#"{"type": "goals", "goals": [{"id": "g1", "text": "מי מחליט", "done": false}]}"#)
        state.toggleFold()
        state.focus("g1")
        expect(channels[4].sent.suffix(4) == [.setVisibility(true), .setCoach(true), .setSections(Sections(ask: true, terms: false)),
                                              .focusGoal("g1")] && !state.model.folded && state.model.focusGoal == "g1",
               "שאל על זה in a folded recording unfolds the panel, turns questions on, then focuses", &p)
        state.toggleGoal("g1")
        expect(channels[4].sent.last == .setGoal("g1", true) && state.model.goals[0].done && state.model.focusGoal == nil,
               "a goal checked from the panel", &p)
        state.toggleCoach()
        expect(channels[4].sent.last == .setCoach(false), "מאמן off after the focus", &p)
        state.toggleCoach()
        channels[4].emit(#"{"type": "error", "code": "no_agent_account", "message": "no agent account is chosen", "ends_call": false}"#)
        expect(channels[4].sent.last == .setSections(state.model.sections) && !state.model.chips.coach
               && state.model.banner?.title == errorTitles["no_agent_account"],
               "no account: the sections are sent again without the AI chips", &p)
        state.closePanel()
        expect(channels[4].sent.last == .setVisibility(false) && !state.model.visible, "X in a call hides the panel", &p)
        state.setVisible(true)
        channels[4].emit(#"{"type": "stopped"}"#)
        expect(Settings.coachChips(defaults) == [.ask], "the chips of the last time the coach was on are kept", &p)
        Settings.setTranscriptInPanel(false, defaults)
        Settings.setCoachChips([.translate], defaults)
        state.startCall(brief: nil)
        expect(state.model.chips.on == [.translate, .transcript] && state.model.coachEver && !state.model.folded,
               "the next call with the coach starts with the remembered translation, which brings the transcript", &p)
        asked.removeFirst()()
        channels[5].emit(#"{"type": "stopped"}"#)
        expect(Settings.transcriptInPanel(defaults), "a transcript that translation turned on is kept at the end of the call", &p)
        state.startRecording()
        expect(state.model.chips.on == [.transcript], "so the next recording shows it", &p)
        asked.removeFirst()()
        channels[6].emit(#"{"type": "stopped"}"#)
        state.startCall(brief: nil)
        asked.removeFirst()()
        channels[7].emit(#"{"type": "ready", "folder": "/x/calls/2026-11-16_1300_general", "replay_t0": null}"#)
        state.toggleChip(.recap)
        state.shutdown()
        expect(Settings.coachChips(defaults) == [.translate, .recap] && channels[7].inputClosed,
               "quitting in a call keeps its chips for the next one", &p)

        var runs: [(BriefRequest, (BriefOutcome) -> Void)] = []
        var cancels = 0
        state.runBrief = { request, done in
            runs.append((request, done))
            return { cancels += 1 }
        }
        let request = BriefRequest(text: "שיחה עם דנה", links: ["https://acme.example"])
        state.makeBrief(request)
        expect(state.briefJob == .making(request) && runs.count == 1, "a brief is being made", &p)
        runs[0].1(.failed("אין חשבון"))
        expect(state.briefJob == .failed(request, "אין חשבון"), "a failure keeps the request", &p)
        state.retryBrief()
        expect(runs.count == 2 && runs[1].0 == request && state.briefJob == .making(request), "try again runs the same request", &p)
        state.cancelBrief()
        runs[1].1(.made(path: "/x/briefs/late.md"))
        expect(cancels == 1 && state.briefJob == nil && state.chosenBrief == nil, "a cancelled brief is dropped", &p)
        state.makeBrief(request)
        runs[2].1(.made(path: state.data.appendingPathComponent("briefs/2026-11-17-acme.md").path))
        expect(state.briefJob == nil && state.chosenBrief == "2026-11-17-acme.md" && state.selectBrief == "2026-11-17-acme.md",
               "the new brief is the one shown", &p)

        let briefsDir = state.data.appendingPathComponent("briefs")
        try? FileManager.default.createDirectory(at: briefsDir, withIntermediateDirectories: true)
        try? "---\ntitle: המשך עם דנה\nglossary: Okta\n---\nרקע\n".write(to: briefsDir.appendingPathComponent("2026-11-19-dana.md"),
                                                                       atomically: true, encoding: .utf8)
        var following = request
        following.after = "2026-11-18_1131_sales"
        state.makeBrief(following)
        expect(!following.line().contains("after"), "the brief command does not get the call it follows", &p)
        runs[3].1(.made(path: briefsDir.appendingPathComponent("2026-11-19-dana.md").path))
        let marked = BriefStore.load(state.data, "2026-11-19-dana.md")?.doc
        expect(marked?.after == "2026-11-18_1131_sales" && marked?.kept == ["glossary: Okta"] && marked?.body == "רקע"
               && state.selectBrief == "2026-11-19-dana.md", "a follow-up brief names the call it follows \(String(describing: marked))", &p)

        let callDir = state.data.appendingPathComponent("calls/2026-11-18_1131_sales")
        try? FileManager.default.createDirectory(at: callDir, withIntermediateDirectories: true)
        try? "# סיכום".write(to: callDir.appendingPathComponent("summary.md"), atomically: true, encoding: .utf8)
        state.prepSheetOpen = false
        state.followUp(CallEntry(folder: callDir, started: Date(), kind: "sales", hasSummary: true, terms: 0, name: "Acme",
                                 brief: "2026-11-19-dana.md"))
        expect(state.prepSheetOpen && state.prepDraft == PrepDraft(text: "שיחת המשך ל-Acme.",
                                                                   files: [callDir.appendingPathComponent("summary.md"),
                                                                           briefsDir.appendingPathComponent("2026-11-19-dana.md")],
                                                                   after: "2026-11-18_1131_sales"),
               "a follow-up opens the prep sheet with the call's summary and brief \(String(describing: state.prepDraft))", &p)
        state.followUp(CallEntry(folder: callDir, started: Date(), kind: "sales", hasSummary: false, terms: 0, name: "דנה",
                                 brief: "gone.md"))
        expect(state.prepDraft == PrepDraft(text: "שיחת המשך לדנה.", files: [], after: "2026-11-18_1131_sales"),
               "a brief that is gone is not attached", &p)
        state.prepSheetOpen = false
        state.prepDraft = nil
        state.selectBrief = nil

        var followRuns: [(URL, (BriefOutcome) -> Void)] = []
        state.runFollowUp = { folder, done in followRuns.append((folder, done)) }
        let callFolder = URL(fileURLWithPath: "/x/calls/2026-11-16_1000_sales")
        state.writeFollowUp(callFolder)
        state.writeFollowUp(callFolder)
        expect(followRuns.count == 1 && state.followUps[callFolder.path] == .writing, "one follow-up draft at a time per call", &p)
        followRuns[0].1(.failed("אין חשבון"))
        expect(state.followUps[callFolder.path] == .failed("אין חשבון"), "a failed draft keeps its message", &p)
        state.writeFollowUp(callFolder)
        let written = state.followUpsWritten
        followRuns[1].1(.made(path: callFolder.appendingPathComponent("followup.md").path))
        expect(followRuns.count == 2 && state.followUps[callFolder.path] == nil && state.followUpsWritten == written + 1,
               "try again writes the draft", &p)

        let home = AppAccount(name: "home", kind: .apiKey)
        let work = AppAccount(name: "work", kind: .subscription)
        let found = AppState.engineAccount(home) { _ in ("k", errSecSuccess) }
        expect(found.account == home && found.key == "k" && found.problem == nil, "an API key that is read", &p)
        let missing = AppState.engineAccount(home) { _ in (nil, errSecItemNotFound) }
        expect(missing.account == nil && missing.key == nil && missing.problem?.contains("לא נמצא") == true,
               "a missing key runs with no agent and says so", &p)
        let locked = AppState.engineAccount(home) { _ in (nil, errSecInteractionNotAllowed) }
        expect(locked.account == nil && locked.problem?.contains("\(errSecInteractionNotAllowed)") == true,
               "a Keychain failure runs with no agent and gives the code", &p)
        expect(EngineLink.environment(account: locked.account, apiKey: nil, inherited: [:])["CALLCOACH_PROVIDER"] == "none",
               "no API provider without a key", &p)
        expect(EngineLink.environment(account: nil, apiKey: nil, inherited: [:], export: nil)["CALLCOACH_EXPORT"] == nil
               && EngineLink.environment(account: nil, apiKey: nil, inherited: [:],
                                         export: URL(fileURLWithPath: "/x/Drive/Calls"))["CALLCOACH_EXPORT"] == "/x/Drive/Calls",
               "the export folder reaches the engine only when one is chosen", &p)
        let sub = AppState.engineAccount(work) { _ in (nil, errSecItemNotFound) }
        expect(sub.account == work && sub.problem == nil, "a subscription needs no key", &p)
        expect(AccountLine.problem(active: home, agent: "ok", check: nil, problem: missing.problem)?.color == .red,
               "the main window shows the key problem in red", &p)
        return p
    }
}
