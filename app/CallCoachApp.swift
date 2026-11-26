import AppKit
import Combine
import SwiftUI
import UserNotifications

enum Paths {
    static let root: URL = {
        if let text = try? String(contentsOf: Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/root.txt"),
                                  encoding: .utf8) {
            return URL(fileURLWithPath: text.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        return URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
    }()
    static var engine: URL { root.appendingPathComponent("bin/callcoach-engine") }
    static var tools: URL {
        URL(fileURLWithPath: ProcessInfo.processInfo.environment["CALLCOACH_TOOLS"] ?? NSHomeDirectory() + "/.callcoach")
    }
    static var data: URL {
        let saved = UserDefaults.standard.string(forKey: "dataDir")
        return URL(fileURLWithPath: saved ?? ProcessInfo.processInfo.environment["CALLCOACH_DATA"]
                   ?? NSHomeDirectory() + "/CallCoachData")
    }
}

@main
enum Main {
    static func main() {
        let args = CommandLine.arguments
        signal(SIGPIPE, SIG_IGN)
        if args.contains("--self-check") {
            exit(SelfCheck.run(root: Paths.root) ? 0 : 1)
        }
        let app = NSApplication.shared
        app.mainMenu = mainMenu()
        let delegate = AppDelegate(stream: value(after: "--stream", in: args).map { URL(fileURLWithPath: $0) },
                                   speed: Double(value(after: "--speed", in: args) ?? "") ?? 1)
        app.delegate = delegate
        app.run()
    }

    static func mainMenu() -> NSMenu {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: L("הגדרות…", "Settings…"), action: #selector(AppDelegate.openSettings), keyEquivalent: ",")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: Actions.open.label, action: #selector(AppDelegate.openMain), keyEquivalent: "0")
        appMenu.addItem(withTitle: L("הכן תדריך…", "Prepare a brief…"), action: #selector(AppDelegate.openPrep), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: L("יציאה מ-CallCoach", "Quit CallCoach"), action: #selector(NSApplication.terminate(_:)),
                        keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)
        let editItem = NSMenuItem()
        let edit = NSMenu(title: L("עריכה", "Edit"))
        edit.addItem(withTitle: L("בטל", "Undo"), action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: L("בצע שוב", "Redo"), action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: L("גזור", "Cut"), action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: L("העתק", "Copy"), action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: L("הדבק", "Paste"), action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: L("בחר הכל", "Select All"), action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        main.addItem(editItem)
        return main
    }

    static func value(after flag: String, in args: [String]) -> String? {
        guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
        return args[i + 1]
    }
}

/// SwiftUI's `State` property wrapper under another name: the Command Line Tools have no plugin for the `@State` macro.
typealias ViewState<Value> = SwiftUI.State<Value>

/// Keeps the window it is drawn in out of screen capture, like the app's own windows; a sheet has a window of its own.
struct CaptureExcluded: NSViewRepresentable {
    final class Probe: NSView {
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            window?.sharingType = WindowHost.sharing
        }
    }

    func makeNSView(context: Context) -> NSView { Probe() }
    func updateNSView(_ nsView: NSView, context: Context) {}
}

final class WindowHost: NSObject, NSWindowDelegate {
    /// CallCoach windows never show in a screen share or a screenshot; the capture switch is for development.
    static var sharing: NSWindow.SharingType {
        ProcessInfo.processInfo.environment["CALLCOACH_PANEL_CAPTURE"] == "1" ? .readOnly : .none
    }

    let window: NSWindow
    var onClose: () -> Void = {}

    init<Content: View>(title: String, size: NSSize, content: Content) {
        window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                          styleMask: [.titled, .closable, .resizable, .miniaturizable], backing: .buffered, defer: false)
        super.init()
        window.title = title
        window.isReleasedWhenClosed = false
        window.sharingType = Self.sharing
        window.contentView = NSHostingView(rootView: Localized(content: content))
        window.delegate = self
        window.center()
    }

    /// A window gives the app a Dock icon until the last window closes; see `AppDelegate.updateDock`.
    func show(activate: Bool = true) {
        NSApp.setActivationPolicy(.regular)
        if activate {
            NSApp.activate()
            window.makeKeyAndOrderFront(nil)
        } else {
            window.orderFrontRegardless()
        }
    }

    func close() {
        window.close()
    }

    func windowWillClose(_ notification: Notification) {
        onClose()
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate, UNUserNotificationCenterDelegate {
    let stream: URL?
    let speed: Double
    var state: AppState!
    var statusItem: NSStatusItem!
    var mainWindow: WindowHost?
    var contextWindow: WindowHost?
    var panel: PanelController?
    var previousApp: NSRunningApplication?
    var lastPhase = Phase.starting
    var lastIcon = CallIcon.none
    var lastSilent: [String] = []
    var subscriptions = Set<AnyCancellable>()

    init(stream: URL?, speed: Double) {
        self.stream = stream
        self.speed = speed
    }

    func applicationDidFinishLaunching(_ note: Notification) {
        let atLogin = Self.launchedAsLoginItem(NSAppleEventManager.shared().currentAppleEvent)
        state = AppState(stream: stream, speed: speed)
        state.onFatal = { [weak self] in self?.alert($0) }
        state.onSummary = { [weak self] in self?.summaryReady($0) }
        state.$model.receive(on: RunLoop.main).sink { [weak self] in self?.sync($0) }.store(in: &subscriptions)
        state.$idlePanel.receive(on: RunLoop.main).sink { [weak self] _ in
            guard let self else { return }
            self.sync(self.state.model)
        }.store(in: &subscriptions)
        LanguageStore.shared.$lang.dropFirst().receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.languageChanged() }.store(in: &subscriptions)
        NotificationCenter.default.publisher(for: UserDefaults.didChangeNotification).map { _ in Settings.alwaysInDock() }
            .prepend(Settings.alwaysInDock()).removeDuplicates().dropFirst().receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.updateDock() }.store(in: &subscriptions)
        UNUserNotificationCenter.current().delegate = self
        buildStatusItem()
        let own = NSRunningApplication.current
        if let front = NSWorkspace.shared.frontmostApplication, front != own { state.lastApp = front.bundleIdentifier }
        NSWorkspace.shared.notificationCenter.publisher(for: NSWorkspace.didActivateApplicationNotification)
            .compactMap { $0.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication }
            .filter { $0 != own }
            .sink { [weak self] in self?.state.lastApp = $0.bundleIdentifier }
            .store(in: &subscriptions)
        for (i, action) in Actions.all.enumerated() {
            let id = action.id
            Hotkeys.shared.register(keyCode: action.keyCode, id: UInt32(i + 1)) { [weak self] in self?.perform(id) }
        }
        state.recover()
        if let file = ProcessInfo.processInfo.environment["CALLCOACH_AUTOSTART"] {
            autostart(URL(fileURLWithPath: file))
        } else if atLogin {
            AppLog.write("opened as a login item: only the menu bar icon")
        } else {
            openMain()
        }
        updateDock()
    }

    /// Opened by macOS as a login item ("פתח בכניסה למחשב"): the open event says so in its property data. Any other
    /// open, or an event that cannot be read, shows the main window as before.
    static func launchedAsLoginItem(_ event: NSAppleEventDescriptor?) -> Bool {
        guard let event, event.eventID == AEEventID(kAEOpenApplication) else { return false }
        return event.paramDescriptor(forKeyword: AEKeyword(keyAEPropData))?.enumCodeValue == OSType(keyAELaunchedAsLogInItem)
    }

    /// Opening the app again, or a click on its Dock icon, shows the main window, and the panel too during a call.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        openMain()
        if state.inCall { state.setVisible(true) }
        return false
    }

    func applicationDockMenu(_ sender: NSApplication) -> NSMenu? {
        let menu = NSMenu()
        if state.inCall {
            menu.addItem(item(L("הצג את הפאנל", "Show the panel"), #selector(showPanelFromMenu), MenuSymbol.show))
            menu.addItem(item(CallMenuItem.stop.title(state.model), #selector(stopFromMenu), MenuSymbol.stop))
            menu.addItem(item(CallMenuItem.stopSummary.title(state.model), #selector(stopSummaryFromMenu), MenuSymbol.stopSummary))
        } else {
            state.reloadBriefs()
            menu.addItem(item(Briefs.startTitle(state.briefChoice()), #selector(startCallNow), MenuSymbol.start))
            menu.addItem(item(L("הכן תדריך…", "Prepare a brief…"), #selector(openPrep), MenuSymbol.prepare))
        }
        return menu
    }

    @objc func openMain() {
        if mainWindow == nil {
            let actions = MainActions(openAccounts: { [weak self] in self?.openAccounts() },
                                      bringPanel: { [weak self] in self?.bringPanelHere() },
                                      openSettings: { [weak self] in self?.openSettings() })
            let player = CallPlayer()
            let host = WindowHost(title: "CallCoach", size: NSSize(width: 960, height: 680),
                                  content: MainView(state: state, actions: actions, player: player))
            host.window.setFrameAutosaveName("CallCoachMain")
            host.onClose = { [weak self] in
                player.open(nil)
                self?.mainWindow = nil
                self?.state.releaseEngine()
                self?.updateDock()
            }
            mainWindow = host
        }
        mainWindow?.show()
    }

    /// The main window on one call, for a notification he clicked.
    func openCall(_ call: String?) {
        state.selectCall = call
        openMain()
    }

    /// The settings are a tab of the main window.
    @objc func openSettings() {
        openMain()
        SettingsRoute.shared.show()
    }

    @objc func showPanelFromMenu() { state.setVisible(true) }

    @objc func startRecordingFromMenu() { state.startRecording() }

    @objc func togglePanelFromMenu() { state.setVisible(!state.model.visible) }

    /// The panel comes to the top right of the screen the mouse is on, and shows if it was hidden.
    @objc func bringPanelHere() {
        if panel == nil { panel = PanelController(state: state, addContext: { [weak self] in self?.openContext() }) }
        panel?.bringHere()
        if state.inCall { state.setVisible(true) } else { state.idlePanel = true }
    }

    /// In a call the panel follows its own show and hide; out of one it shows only after "פאנל השיחה".
    static func panelShown(phase: Phase, visible: Bool, idle: Bool) -> Bool {
        phase == .active || phase == .stopping ? visible : idle
    }

    @objc func askFromMenu() { state.perform(Actions.ask) }

    @objc func markFromMenu() { state.perform(Actions.mark) }

    @objc func termsNowFromMenu() { state.perform(Actions.termsNow) }

    @objc func screenFromMenu() { state.perform(Actions.screen) }

    @objc func stopFromMenu() { state.stop() }

    @objc func stopSummaryFromMenu() { state.stop(summary: true) }

    @objc func toggleCoachFromMenu() { state.toggleCoach() }

    @objc func pauseFromMenu() { state.togglePause() }

    @objc func copyTranscriptFromMenu() { state.copyTranscript() }

    @objc func toggleAutoRecordFromMenu() { state.toggleAutoRecord() }

    @objc func toggleOpenAtLoginFromMenu() {
        guard let error = LoginItem.set(!LoginItem.on) else { return }
        AppLog.write("open at login: \(error)")
        alert(L("לא הצלחתי לשנות את הפתיחה בכניסה למחשב: \(error)", "Could not change opening at login: \(error)"))
    }

    @objc func startCallNow() { startNow() }

    /// What the start button does: the due brief when there is one.
    func startNow() {
        state.reloadBriefs()
        state.startCall(brief: state.briefChoice().dueBrief)
    }

    @objc func openContext() {
        guard state.model.phase == .active else { return }
        if contextWindow == nil {
            if let front = NSWorkspace.shared.frontmostApplication, front != NSRunningApplication.current {
                previousApp = front
            }
            let host = WindowHost(title: ContextView.title, size: NSSize(width: 420, height: 300),
                                  content: ContextView(state: state, done: { [weak self] in self?.closeContext() }))
            host.onClose = { [weak self] in
                self?.contextWindow = nil
                self?.updateDock()
            }
            contextWindow = host
        }
        guard let host = contextWindow else { return }
        host.window.collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        if let shown = panel?.panel, shown.isVisible, let screen = shown.screen {
            host.window.setFrame(PanelLayout.beside(shown.frame, size: host.window.frame.size, visible: screen.visibleFrame),
                                 display: false)
        }
        host.show()
    }

    func closeContext() {
        contextWindow?.close()
        previousApp?.activate()
    }

    /// Development: starts a call at once with the file's text as its context.
    func autostart(_ file: URL) {
        let text = (try? String(contentsOf: file, encoding: .utf8)) ?? ""
        openMain()
        state.ensureEngine()
        state.start(StartFields(callType: "validation", lang: "he", text: text, lengthMin: 20, name: "autostart"))
    }

    func applicationWillTerminate(_ notification: Notification) {
        state.shutdown()
    }

    func perform(_ id: String) {
        guard let action = Actions.all.first(where: { $0.id == id }) else { return }
        switch id {
        case "prepare": openPrep()
        case "start_now": startNow()
        case "open": openMain()
        case "fill": panel?.toggleFill()
        case "context": openContext()
        default: state.perform(action)
        }
    }

    func sync(_ model: CallModel) {
        let kind = Self.callIcon(model)
        if kind != lastIcon {
            lastIcon = kind
            statusItem?.button?.image = Self.icon(kind)
        }
        let silent = SilentSide.sides(model)
        if silent != lastSilent {
            let shown = Self.panelShown(phase: model.phase, visible: model.visible, idle: state.idlePanel)
            let alerts = SilentSide.alerts(was: lastSilent, now: silent, panelShown: shown, inCall: model.phase == .active)
            lastSilent = silent
            alerts.post.forEach(silentWarning)
            let gone = alerts.remove.map(SilentSide.identifier)
            if !gone.isEmpty { UNUserNotificationCenter.current().removeDeliveredNotifications(withIdentifiers: gone) }
        }
        if model.phase != lastPhase {
            lastPhase = model.phase
            if model.phase != .active { contextWindow?.close() }
        }
        if model.phase == .active && state.idlePanel { state.idlePanel = false }
        if Self.panelShown(phase: model.phase, visible: model.visible, idle: state.idlePanel) {
            if panel == nil { panel = PanelController(state: state, addContext: { [weak self] in self?.openContext() }) }
            panel?.show()
        } else {
            panel?.hide()
        }
    }

    /// With "תמיד ב-Dock" off, the Dock icon stays only while a window is open, as in CallRec.
    static func dockPolicy(windowOpen: Bool, always: Bool) -> NSApplication.ActivationPolicy {
        windowOpen || always ? .regular : .accessory
    }

    func updateDock() {
        let open = mainWindow != nil || contextWindow != nil
        NSApp.setActivationPolicy(Self.dockPolicy(windowOpen: open, always: Settings.alwaysInDock()))
    }

    func languageChanged() {
        NSApp.mainMenu = Main.mainMenu()
    }

    /// The sheet in the main window.
    @objc func openPrep() {
        openMain()
        state.prepSheetOpen = true
    }

    @objc func openAccounts() {
        openMain()
        SettingsRoute.shared.show(.account)
    }

    func alert(_ message: String) {
        NSApp.activate()
        let alert = NSAlert()
        alert.messageText = message
        alert.window.sharingType = WindowHost.sharing
        alert.runModal()
    }

    /// The notification's title and body: the summary is ready with the call's title, or the call ended with why
    /// there is no summary.
    static func summaryNote(_ summary: SummaryReady, callTitle: String?, recordOnly: Bool = false) -> (title: String, body: String) {
        if (recordOnly && !summary.ok) || summary.stoppedWithoutSummary { return (L("ההקלטה נשמרה", "The recording is saved"), callTitle ?? "") }
        return summary.ok ? (L("הסיכום מוכן", "The summary is ready"), callTitle ?? "")
            : (L("השיחה הסתיימה", "The call ended"), summary.message)
    }

    /// With the main window closed, a notification says the summary is ready; a click opens the call's page.
    func summaryReady(_ summary: SummaryReady) {
        guard mainWindow == nil else { return }
        let folder = URL(fileURLWithPath: summary.path).deletingLastPathComponent()
        let entry = CallLibrary.entry(folder)
        let note = Self.summaryNote(summary, callTitle: entry?.title, recordOnly: entry?.recordOnly == true)
        let content = UNMutableNotificationContent()
        content.title = note.title
        content.body = note.body
        content.userInfo = ["call": folder.lastPathComponent]
        let request = UNNotificationRequest(identifier: "summary " + folder.lastPathComponent, content: content, trigger: nil)
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert]) { granted, _ in
            if granted { center.add(request) }
        }
    }

    /// A side of the call is not being recorded and the panel is not on screen; a click shows the panel.
    func silentWarning(_ side: String) {
        let content = UNMutableNotificationContent()
        content.title = SilentSide.title(side)
        content.body = SilentSide.detail(side)
        content.userInfo = ["panel": true]
        let request = UNNotificationRequest(identifier: SilentSide.identifier(side), content: content, trigger: nil)
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert]) { granted, _ in
            if granted { center.add(request) }
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler done: @escaping () -> Void) {
        let info = response.notification.request.content.userInfo
        let call = info["call"] as? String
        DispatchQueue.main.async {
            if info["panel"] as? Bool == true { self.state.setVisible(true) } else { self.openCall(call) }
            done()
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
        done([.banner, .list])
    }

    /// Points from the right edge of the screen. A new menu bar icon goes to the far left, which a notch hides
    /// when the bar is full, so the first launch places it next to the system icons. A ⌘-drag replaces this.
    static let menuBarPosition = 400

    /// The icon follows the mode: none with no call, a recording symbol while the call only records, the filled
    /// bubble while the coach is on, and a triangle while a side of the call is not being recorded.
    static func callIcon(_ model: CallModel) -> CallIcon {
        guard model.phase == .active || model.phase == .stopping else { return .none }
        if !SilentSide.sides(model).isEmpty { return .warning }
        return model.chips.coach ? .coach : .recording
    }

    static func icon(_ kind: CallIcon) -> NSImage? {
        let name: String
        switch kind {
        case .none: name = "text.bubble"
        case .recording: name = MenuSymbol.record
        case .coach: name = "text.bubble.fill"
        case .warning: name = "exclamationmark.triangle.fill"
        }
        let image = NSImage(systemSymbolName: name, accessibilityDescription: "CallCoach")
        image?.isTemplate = true
        return image
    }

    func buildStatusItem() {
        let key = "NSStatusItem Preferred Position Item-0"
        if UserDefaults.standard.object(forKey: key) == nil {
            UserDefaults.standard.set(Self.menuBarPosition, forKey: key)
        }
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = Self.icon(.none)
        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu
    }

    /// The menu is built again each time it opens, so it follows the call, the briefs and the language. Out of a call
    /// it follows the start area of the main window.
    func menuNeedsUpdate(_ menu: NSMenu) {
        guard menu === statusItem.menu else { return }
        fill(menu)
    }

    func fill(_ menu: NSMenu) {
        menu.removeAllItems()
        menu.userInterfaceLayoutDirection = LanguageStore.shared.lang == .he ? .rightToLeft : .leftToRight
        let model = state.model
        menu.addItem(info(Self.statusLine(model, starting: state.starting)))
        menu.addItem(.separator())
        if state.inCall {
            let active = model.phase == .active
            for entry in Self.callMenu(model) {
                switch entry {
                case .separator: menu.addItem(.separator())
                case .coachSwitch:
                    menu.addItem(item(entry.title(model), active ? #selector(toggleCoachFromMenu) : nil, MenuSymbol.coach))
                case .ask:
                    menu.addItem(item(entry.title(model), active ? #selector(askFromMenu) : nil, MenuSymbol.ask))
                case .terms:
                    menu.addItem(item(entry.title(model), active ? #selector(termsNowFromMenu) : nil, MenuSymbol.termsNow))
                case .screen:
                    menu.addItem(item(entry.title(model), active ? #selector(screenFromMenu) : nil, MenuSymbol.screen))
                case .context:
                    menu.addItem(item(entry.title(model), active ? #selector(openContext) : nil, MenuSymbol.context))
                case .mark:
                    menu.addItem(item(entry.title(model), active ? #selector(markFromMenu) : nil, MenuSymbol.mark))
                case .panel:
                    menu.addItem(item(entry.title(model), #selector(togglePanelFromMenu), model.visible ? MenuSymbol.hide : MenuSymbol.show))
                case .bringHere:
                    menu.addItem(item(entry.title(model), #selector(bringPanelHere), MenuSymbol.bringHere))
                case .pause:
                    menu.addItem(item(entry.title(model), active ? #selector(pauseFromMenu) : nil,
                                      model.paused ? MenuSymbol.resume : MenuSymbol.pause))
                case .copy:
                    menu.addItem(item(entry.title(model), model.folder != nil ? #selector(copyTranscriptFromMenu) : nil, MenuSymbol.copy))
                case .stop:
                    menu.addItem(item(entry.title(model), active ? #selector(stopFromMenu) : nil, MenuSymbol.stop))
                case .stopSummary:
                    menu.addItem(item(entry.title(model), active ? #selector(stopSummaryFromMenu) : nil, MenuSymbol.stopSummary))
                }
            }
        } else {
            let ready = !state.starting
            state.reloadBriefs()
            menu.addItem(item(Self.withHotkey(Briefs.startTitle(state.briefChoice()), Actions.startNow),
                              ready ? #selector(startCallNow) : nil, MenuSymbol.start))
            menu.addItem(item(HomeView.recordTitle, #selector(startRecordingFromMenu), MenuSymbol.record))
            menu.addItem(item(Self.withHotkey(L("הכן תדריך…", "Prepare a brief…"), Actions.prepare), #selector(openPrep),
                              MenuSymbol.prepare))
        }
        menu.addItem(item(Self.withHotkey(Actions.open.label, Actions.open), #selector(openMain), MenuSymbol.open))
        menu.addItem(.separator())
        menu.addItem(check(Self.autoRecordTitle, on: Settings.autoRecord(state.defaults), #selector(toggleAutoRecordFromMenu),
                           MenuSymbol.autoRecord))
        menu.addItem(check(Self.openAtLoginTitle, on: LoginItem.on, #selector(toggleOpenAtLoginFromMenu), MenuSymbol.openAtLogin))
        menu.addItem(item(L("הגדרות…", "Settings…"), #selector(openSettings), MenuSymbol.settings))
        menu.addItem(.separator())
        let quit = menu.addItem(withTitle: L("יציאה מ-CallCoach", "Quit CallCoach"), action: #selector(NSApplication.terminate(_:)),
                                keyEquivalent: "q")
        quit.image = NSImage(systemSymbolName: MenuSymbol.quit, accessibilityDescription: nil)
    }

    /// The items of the menu in a call, in order. The coach items are there only while the coach is on. The stop has
    /// no summary; the item after it asks for one.
    static func callMenu(_ model: CallModel) -> [CallMenuItem] {
        var items: [CallMenuItem] = [.coachSwitch, .panel, .bringHere, .pause, .copy]
        if model.chips.coach { items += [.separator, .ask, .terms, .screen, .context, .mark] }
        items += [.separator, .stop, .stopSummary]
        return items
    }

    static func statusLine(_ model: CallModel, starting: Bool = false) -> String {
        if starting && model.phase != .active && model.phase != .stopping {
            return L("מתחיל את השיחה…", "Starting the call…")
        }
        switch model.phase {
        case .active:
            let coach = model.chips.coach
            return L("שיחה פעילה · ", "In a call · ") + (coach ? L("עם מאמן", "With the coach") : L("מקליט", "Recording"))
                + (model.paused ? L(", ההקלטה מושהית", ", paused") : "")
                + (coach && !model.goals.isEmpty ? ", " + L("מטרות: ", "goals: ") + model.goalProgress : "")
        case .stopping: return L("מסיים את השיחה…", "Ending the call…")
        default: return L("מוכן לשיחה", "Ready for a call")
        }
    }

    static func pauseTitle(_ model: CallModel) -> String {
        model.paused ? L("המשך להקליט", "Resume recording") : L("השהה את ההקלטה", "Pause the recording")
    }

    static func withHotkey(_ title: String, _ action: AppAction) -> String { "\(title)    \(action.hotkey)" }

    static var bringHereTitle: String { L("הבא את הפאנל לכאן", "Bring the panel here") }

    /// The two switches of the menu, in and out of a call, as in CallRec's menu.
    static var autoRecordTitle: String { L("הקלט שיחות אוטומטית", "Record calls automatically") }
    static var openAtLoginTitle: String { L("פתח בכניסה למחשב", "Open at login") }

    private func info(_ title: String) -> NSMenuItem {
        NSMenuItem(title: title, action: nil, keyEquivalent: "")
    }

    /// An item with a check mark when its switch is on.
    private func check(_ title: String, on: Bool, _ action: Selector, _ symbol: String) -> NSMenuItem {
        let switchItem = item(title, action, symbol)
        switchItem.state = on ? .on : .off
        return switchItem
    }

    private func item(_ title: String, _ action: Selector?, _ symbol: String? = nil) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
        item.target = self
        if let symbol { item.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil) }
        return item
    }
}

enum CallIcon: CaseIterable { case none, recording, coach, warning }

/// An item of the menu in a call.
enum CallMenuItem {
    case separator, coachSwitch, ask, terms, screen, context, mark, panel, bringHere, pause, copy, stop, stopSummary

    func title(_ model: CallModel) -> String {
        switch self {
        case .separator: return ""
        case .coachSwitch: return model.chips.coach ? L("כבה את המאמן", "Turn the coach off") : L("הפעל את המאמן", "Turn the coach on")
        case .ask: return AppDelegate.withHotkey(Actions.ask.label, Actions.ask)
        case .terms: return AppDelegate.withHotkey(Actions.termsNow.label, Actions.termsNow)
        case .screen: return AppDelegate.withHotkey(Actions.screen.label, Actions.screen)
        case .context: return AppDelegate.withHotkey(L("הקשר או הוראה למאמן…", "Context or a note to the coach…"), Actions.context)
        case .mark: return AppDelegate.withHotkey(Actions.mark.label, Actions.mark)
        case .panel:
            return AppDelegate.withHotkey(model.visible ? L("הסתר את הפאנל", "Hide the panel") : L("הצג את הפאנל", "Show the panel"), Actions.hide)
        case .bringHere: return AppDelegate.bringHereTitle
        case .pause: return AppDelegate.pauseTitle(model)
        case .copy: return L("העתק את התמלול", "Copy the transcript")
        case .stop: return L("עצור את השיחה", "Stop the call")
        case .stopSummary: return L("עצור וסכם", "Stop and summarize")
        }
    }
}

/// The symbol beside each item of the menu bar and Dock menus, the same one the main window or the panel shows for
/// it, so an item is found by its picture.
enum MenuSymbol {
    static let start = "mic.fill"
    static let record = "record.circle"
    static let prepare = "doc.text"
    static let open = "macwindow"
    static let settings = "gearshape"
    static let quit = "power"
    static let ask = "questionmark.bubble"
    static let termsNow = "text.magnifyingglass"
    static let screen = "camera.viewfinder"
    static let context = "square.and.pencil"
    static let pause = "pause.circle"
    static let resume = "play.circle"
    static let copy = "doc.on.doc"
    static let mark = "bookmark"
    static let hide = "eye.slash"
    static let show = "eye"
    static let bringHere = "rectangle.on.rectangle"
    static let stop = "stop.circle"
    static let stopSummary = "text.badge.checkmark"
    static let coach = "sparkles"
    static let autoRecord = "phone.arrow.down.left"
    static let openAtLogin = "person.crop.circle.badge.checkmark"

    static let all = [start, record, prepare, open, settings, quit, ask, termsNow, screen, context, pause, resume, copy,
                      mark, hide, show, bringHere, stop, stopSummary, coach, autoRecord, openAtLogin]
}

extension SelfCheck {
    static func menuChecks(_ root: URL) -> [String] {
        var p: [String] = []
        var none = CallModel()
        var rec = CallModel()
        rec.phase = .active
        rec.chips = Chips(on: [.transcript])
        var withCoach = rec
        withCoach.chips = Chips(on: [.ask])
        none.phase = .idle
        expect(AppDelegate.callIcon(none) == .none && AppDelegate.callIcon(rec) == .recording && AppDelegate.callIcon(withCoach) == .coach,
               "the icon follows the mode", &p)
        expect(CallIcon.allCases.allSatisfy { AppDelegate.icon($0) != nil }, "every icon exists", &p)
        expect(AppDelegate.statusLine(rec) == L("שיחה פעילה · מקליט", "In a call · Recording")
               && AppDelegate.statusLine(withCoach) == L("שיחה פעילה · עם מאמן", "In a call · With the coach"), "the state line", &p)
        let recTitles = AppDelegate.callMenu(rec).map { $0.title(rec) }
        let coachTitles = AppDelegate.callMenu(withCoach).map { $0.title(withCoach) }
        expect(recTitles.contains(L("הפעל את המאמן", "Turn the coach on")) && !AppDelegate.callMenu(rec).contains(.ask)
               && !AppDelegate.callMenu(rec).contains(.mark) && AppDelegate.callMenu(rec).contains(.stop),
               "a recording has no coach items and offers the coach \(recTitles)", &p)
        expect(AppDelegate.callMenu(rec).suffix(2) == [.stop, .stopSummary] && AppDelegate.callMenu(withCoach).suffix(2) == [.stop, .stopSummary]
               && CallMenuItem.stopSummary.title(rec) == L("עצור וסכם", "Stop and summarize"),
               "the menu ends with the stop and, after it, stop and summarize", &p)
        expect(coachTitles.contains(L("כבה את המאמן", "Turn the coach off")) && AppDelegate.callMenu(withCoach).contains(.ask)
               && AppDelegate.callMenu(withCoach).contains(.terms) && AppDelegate.callMenu(withCoach).contains(.screen)
               && AppDelegate.callMenu(withCoach).contains(.context) && AppDelegate.callMenu(withCoach).contains(.mark),
               "a coach call has the coach items and turns the coach off \(coachTitles)", &p)
        let missing = MenuSymbol.all.filter { NSImage(systemSymbolName: $0, accessibilityDescription: nil) == nil }
        expect(missing.isEmpty && Set(MenuSymbol.all).count == MenuSymbol.all.count, "every menu symbol exists, once \(missing)", &p)
        expect(!AppDelegate.panelShown(phase: .idle, visible: true, idle: false)
               && AppDelegate.panelShown(phase: .idle, visible: false, idle: true)
               && AppDelegate.panelShown(phase: .ended, visible: false, idle: true), "with no call the panel shows only when asked for", &p)
        expect(AppDelegate.panelShown(phase: .active, visible: true, idle: false)
               && !AppDelegate.panelShown(phase: .active, visible: false, idle: true)
               && AppDelegate.panelShown(phase: .stopping, visible: true, idle: false), "in a call the panel follows its own show and hide", &p)
        let store = LanguageStore.shared
        store.lang = .en
        var model = CallModel()
        model.phase = .stopping
        expect(AppDelegate.statusLine(model) == "Ending the call…", "English status line", &p)
        expect(AppDelegate.statusLine(CallModel(), starting: true) == "Starting the call…", "starting status line", &p)
        store.lang = .he
        expect(AppDelegate.statusLine(CallModel()) == "מוכן לשיחה", "Hebrew status line", &p)
        expect(AppDelegate.bringHereTitle == "הבא את הפאנל לכאן", "bring the panel here", &p)
        expect(AppDelegate.autoRecordTitle == "הקלט שיחות אוטומטית" && AppDelegate.openAtLoginTitle == "פתח בכניסה למחשב",
               "the two switches in Hebrew", &p)
        store.lang = .en
        expect(AppDelegate.autoRecordTitle == "Record calls automatically" && AppDelegate.openAtLoginTitle == "Open at login",
               "the two switches in English", &p)
        store.lang = .he
        func openEvent(atLogin: Bool) -> NSAppleEventDescriptor {
            let event = NSAppleEventDescriptor(eventClass: AEEventClass(kCoreEventClass), eventID: AEEventID(kAEOpenApplication),
                                               targetDescriptor: nil, returnID: AEReturnID(kAutoGenerateReturnID),
                                               transactionID: AETransactionID(kAnyTransactionID))
            if atLogin {
                event.setParam(NSAppleEventDescriptor(enumCode: OSType(keyAELaunchedAsLogInItem)), forKeyword: AEKeyword(keyAEPropData))
            }
            return event
        }
        expect(AppDelegate.launchedAsLoginItem(openEvent(atLogin: true)) && !AppDelegate.launchedAsLoginItem(openEvent(atLogin: false))
               && !AppDelegate.launchedAsLoginItem(nil), "only an open as a login item keeps the main window closed", &p)
        var paused = CallModel()
        paused.phase = .active
        paused.chips = Chips(on: [.transcript])
        expect(AppDelegate.statusLine(paused) == "שיחה פעילה · מקליט" && AppDelegate.pauseTitle(paused) == "השהה את ההקלטה",
               "the menu while recording", &p)
        paused.paused = true
        expect(AppDelegate.statusLine(paused) == "שיחה פעילה · מקליט, ההקלטה מושהית" && AppDelegate.pauseTitle(paused) == "המשך להקליט",
               "the menu while paused", &p)
        expect(AppDelegate.dockPolicy(windowOpen: false, always: false) == .accessory
               && AppDelegate.dockPolicy(windowOpen: true, always: false) == .regular
               && AppDelegate.dockPolicy(windowOpen: false, always: true) == .regular, "the Dock icon", &p)
        let made = AppDelegate.summaryNote(SummaryReady(ok: true, path: "/c/summary.md", message: ""), callTitle: "שיחה עם Acme")
        expect(made == ("הסיכום מוכן", "שיחה עם Acme"), "the summary notification \(made)", &p)
        let kept = AppDelegate.summaryNote(SummaryReady(ok: false, path: "/c/summary.md", message: "הקלטה בלבד"), callTitle: "שיחה", recordOnly: true)
        expect(kept.0 == L("ההקלטה נשמרה", "The recording is saved") && kept.1 == "שיחה", "a record-only call says the recording is saved \(kept)", &p)
        let joined = AppDelegate.summaryNote(SummaryReady(ok: true, path: "/c/summary.md", message: ""), callTitle: "שיחה", recordOnly: true)
        expect(joined == ("הסיכום מוכן", "שיחה"), "a recording the coach joined says its summary is ready \(joined)", &p)
        let without = AppDelegate.summaryNote(SummaryReady(ok: false, path: "/c/summary.md", message: SummaryReady.noSummary), callTitle: "שיחה עם Acme")
        expect(without == (L("ההקלטה נשמרה", "The recording is saved"), "שיחה עם Acme"),
               "a call stopped without a summary says the recording is saved \(without)", &p)
        let failed = AppDelegate.summaryNote(SummaryReady(ok: false, path: "/c/summary.md", message: "אין חשבון"),
                                             callTitle: "שיחה עם Acme")
        expect(failed == ("השיחה הסתיימה", "אין חשבון"), "no summary, so the call ended \(failed)", &p)
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("callcoach-menu-\(getpid())")
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let suite = folder.appendingPathComponent("settings").path
        let defaults = UserDefaults(suiteName: suite)!
        defer {
            defaults.removePersistentDomain(forName: suite)
            try? FileManager.default.removeItem(at: folder)
        }
        expect(!Settings.alwaysInDock(defaults), "the Dock icon is off by default", &p)
        Settings.setAlwaysInDock(true, defaults)
        expect(Settings.alwaysInDock(defaults), "always in the Dock", &p)
        return p
    }
}
