import AppKit
import Combine
import SwiftUI

enum Settings {
    /// Every call starts without nudges: no tips, no back-to-goal cards, no question cards unless asked for. Names,
    /// terms, what was asked of me and the live questions come as before.
    static let defaultLevel = "quiet"

    static let transcriptKey = "panelTranscript"

    /// The transcript chip as the last call left it, recordings included: the last transcript lines and the other
    /// side's open sentence in the panel. Off by default, so their room goes to the names and terms.
    static func transcriptInPanel(_ defaults: UserDefaults = .standard) -> Bool {
        defaults.bool(forKey: transcriptKey)
    }

    static func setTranscriptInPanel(_ on: Bool, _ defaults: UserDefaults = .standard) {
        defaults.set(on, forKey: transcriptKey)
    }

    static let coachChipsKey = "coachChips"

    /// The AI chips on at the end of the last call that was in מאמן; nil before the first such call.
    static func coachChips(_ defaults: UserDefaults = .standard) -> Set<Chip>? {
        (defaults.array(forKey: coachChipsKey) as? [String]).map { Set($0.compactMap(Chip.init(rawValue:))) }
    }

    static func setCoachChips(_ chips: Set<Chip>, _ defaults: UserDefaults = .standard) {
        defaults.set(chips.map(\.rawValue).sorted(), forKey: coachChipsKey)
    }

    static let nowLineKey = "recapNowLine"

    /// The line on what is discussed now at the top of "סיכום ביניים", as the last call left its switch. On by default.
    static func nowLine(_ defaults: UserDefaults = .standard) -> Bool {
        defaults.object(forKey: nowLineKey) as? Bool ?? true
    }

    static func setNowLine(_ on: Bool, _ defaults: UserDefaults = .standard) {
        defaults.set(on, forKey: nowLineKey)
    }

    static let listOnlyKey = "termsListOnly"

    /// "רק מהרשימה" in "מושגים", as the last call left it. Off by default.
    static func termsListOnly(_ defaults: UserDefaults = .standard) -> Bool {
        defaults.bool(forKey: listOnlyKey)
    }

    static func setTermsListOnly(_ on: Bool, _ defaults: UserDefaults = .standard) {
        defaults.set(on, forKey: listOnlyKey)
    }

    static let dockKey = "alwaysInDock"

    /// A Dock icon all the time, not only while a window is open. Off by default.
    static func alwaysInDock(_ defaults: UserDefaults = .standard) -> Bool {
        defaults.bool(forKey: dockKey)
    }

    static func setAlwaysInDock(_ on: Bool, _ defaults: UserDefaults = .standard) {
        defaults.set(on, forKey: dockKey)
    }

    static let exportKey = "exportDir"

    /// The folder a copy of every finished call goes to (its summary and transcript, never the audio), such as one
    /// Google Drive for desktop syncs; nil when none is chosen, which is the default.
    static func exportFolder(_ defaults: UserDefaults = .standard) -> URL? {
        guard let path = defaults.string(forKey: exportKey), !path.isEmpty else { return nil }
        return URL(fileURLWithPath: path, isDirectory: true)
    }

    static func setExportFolder(_ folder: URL?, _ defaults: UserDefaults = .standard) {
        if let folder { defaults.set(folder.path, forKey: exportKey) } else { defaults.removeObject(forKey: exportKey) }
    }
}

/// The settings pages, in the main window's "הגדרות" tab.
enum SettingsPage: String, CaseIterable, Identifiable {
    case general, account, ignore

    var id: String { rawValue }

    var title: String {
        switch self {
        case .general: return L("כללי", "General")
        case .account: return L("חשבונות", "Accounts")
        case .ignore: return L("לא להקפיץ", "Don't show")
        }
    }

    var icon: String {
        switch self {
        case .general: return "gearshape"
        case .account: return "person.crop.circle"
        case .ignore: return "eye.slash"
        }
    }
}

/// The settings page to show; "הגדרות", Cmd-comma and the account line send the main window to it.
final class SettingsRoute: ObservableObject {
    static let shared = SettingsRoute()
    @Published var page = SettingsPage.general
    /// The main window was asked to show the settings and has not yet; it takes the request when it opens.
    @Published var pending = false

    func show(_ page: SettingsPage? = nil) {
        if let page { self.page = page }
        pending = true
    }

    static func checks(_ root: URL) -> [String] {
        var p: [String] = []
        let route = SettingsRoute()
        route.show(.account)
        expect(route.pending && route.page == .account, "the account line asks for the account page", &p)
        route.pending = false
        route.show()
        expect(route.pending && route.page == .account, "Settings again keeps the page last shown", &p)
        expect(SettingsPage.account.title == L("חשבונות", "Accounts") && AccountLine.accountsTitle.hasPrefix(SettingsPage.account.title),
               "the accounts page has one name, and the home's account line uses it", &p)
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("callcoach-settings-\(getpid())")
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let suite = folder.appendingPathComponent("settings").path
        let defaults = UserDefaults(suiteName: suite)!
        defer {
            defaults.removePersistentDomain(forName: suite)
            try? FileManager.default.removeItem(at: folder)
        }
        expect(Settings.coachChips(defaults) == nil, "no chips are remembered before the first call with the coach", &p)
        Settings.setCoachChips([.translate, .recap, .terms], defaults)
        expect(Settings.coachChips(defaults) == [.translate, .recap, .terms], "the chips of the last call with the coach", &p)
        Settings.setCoachChips([.ask], defaults)
        expect(Settings.coachChips(defaults) == [.ask], "a later call with the coach replaces them", &p)
        expect(!Settings.transcriptInPanel(defaults), "the transcript chip starts off", &p)
        expect(Settings.exportFolder(defaults) == nil, "no export folder until one is chosen", &p)
        Settings.setExportFolder(URL(fileURLWithPath: "/x/Drive/Calls"), defaults)
        expect(Settings.exportFolder(defaults)?.path == "/x/Drive/Calls", "the export folder chosen", &p)
        Settings.setExportFolder(nil, defaults)
        expect(Settings.exportFolder(defaults) == nil, "the export folder can be removed", &p)
        return p
    }
}

/// The settings page chosen in the main window's sidebar.
struct SettingsDetail: View {
    @ObservedObject var state: AppState
    @ObservedObject var route = SettingsRoute.shared

    var body: some View {
        switch route.page {
        case .general: GeneralSettings()
        case .account: AccountSettings(state: state)
        case .ignore: IgnoreSettings()
        }
    }
}

struct GeneralSettings: View {
    @ObservedObject var language = LanguageStore.shared
    @ViewState private var dock = Settings.alwaysInDock()
    @ViewState private var export = Settings.exportFolder()
    @ViewState private var screen = Permissions.screen

    var body: some View {
        Form {
            Picker(L("שפת הממשק", "Interface language"), selection: Binding(get: { language.lang },
                                                                         set: { language.choose($0) })) {
                ForEach(inReadingOrder(UILang.allCases)) { Text($0.name).tag($0) }
            }
            .pickerStyle(.segmented)
            Toggle(L("תמיד ב-Dock", "Always in the Dock"), isOn: $dock)
                .onChange(of: dock) { _, on in Settings.setAlwaysInDock(on) }
            Text(L("כשזה כבוי, האייקון ב-Dock מופיע רק כשחלון פתוח. לחיצה עליו פותחת את החלון הראשי, וגם \(Actions.open.hotkey) פותח אותו.",
                   "When this is off, the Dock icon shows only while a window is open. A click on it opens the main window, and so does \(Actions.open.hotkey)."))
                .font(.caption).foregroundStyle(.secondary)
            LabeledContent(L("צילום חלון השיחה", "Call window shots")) {
                if screen {
                    Text(L("מותר", "Allowed")).foregroundStyle(.secondary)
                } else {
                    Button(L("אפשר", "Allow")) {
                        screen = Permissions.requestScreen()
                        if !screen { Permissions.openSettings("Privacy_ScreenCapture") }
                    }
                }
            }
            Text(L("הקיצור \(Actions.screen.hotkey) והכפתור בחלון ההקשר מצלמים רק את חלון השיחה, בלי צליל. בפעם הראשונה צריך לאשר הקלטת מסך בהגדרות המערכת, ומדי פעם המערכת שואלת שוב, לכן כדאי לאשר כאן לפני שיחה.",
                   "\(Actions.screen.hotkey) and the button in the context window take only the call's window, with no sound. The first time, allow screen recording in System Settings; macOS asks again now and then, so allow it here before a call."))
                .font(.caption).foregroundStyle(.secondary)
                .onAppear { screen = Permissions.screen }
            LabeledContent(L("תיקיית הנתונים", "Data folder")) {
                HStack {
                    Text(Paths.data.path).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                    Button(L("פתח", "Open")) {
                        try? FileManager.default.createDirectory(at: Paths.data, withIntermediateDirectories: true)
                        NSWorkspace.shared.open(Paths.data)
                    }
                }
            }
            LabeledContent(L("העתק כל שיחה לתיקייה", "Copy every call to a folder")) {
                HStack {
                    if let export {
                        Text(export.path).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                        Button(L("הסר", "Remove")) { choose(nil) }
                    }
                    Button(export == nil ? L("בחר…", "Choose…") : L("שנה…", "Change…"), action: pick)
                }
            }
            Text(L("בסוף כל שיחה, וגם אחרי \"צור סיכום\", נשמרים שם הסיכום והתמלול בתיקייה בשם השיחה. ההקלטה לא מועתקת. אפשר לבחור תיקייה ש-Google Drive מסנכרן, והקבצים יעלו לדרייב.",
                   "At the end of every call, and after \"Write a summary\", the summary and the transcript are saved there in a folder named after the call. The recording is not copied. Choose a folder Google Drive syncs and the files go up to Drive."))
                .font(.caption).foregroundStyle(.secondary)
        }
        .formStyle(.grouped)
    }

    private func pick() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = L("בחר", "Choose")
        panel.directoryURL = export ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/CloudStorage", isDirectory: true)
        if panel.runModal() == .OK, let url = panel.url { choose(url) }
    }

    private func choose(_ folder: URL?) {
        Settings.setExportFolder(folder)
        export = folder
    }
}

struct AccountSettings: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            let usage = UsageMemory.load()
            let subscription = state.accounts.active?.kind == .subscription
            HStack(spacing: 16) {
                Text(L("היום: ", "Today: ") + UsageMemory.label(usage.today, subscription: subscription))
                Text(L("החודש: ", "This month: ") + UsageMemory.label(usage.month, subscription: subscription))
            }
            .font(.system(size: 12)).foregroundStyle(.secondary)
            .padding(.horizontal, 20).padding(.top, 8)
            ConnectView(state: state)
        }
    }
}
