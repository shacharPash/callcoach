import Foundation

func expect(_ ok: Bool, _ what: @autoclosure () -> String, _ problems: inout [String]) {
    if !ok { problems.append(what()) }
}

enum SelfCheck {
    static let groups: [(String, (URL) -> [String])] = [
        ("root", rootChecks),
        ("protocol", protocolChecks),
        ("model", modelChecks),
        ("picker", pickerChecks),
        ("screen", screenChecks),
        ("panel", panelChecks),
        ("accounts", accountChecks),
        ("engine link", linkChecks),
        ("actions and usage", actionChecks),
        ("briefs", briefChecks),
        ("prep sheet", prepChecks),
        ("summary", summaryChecks),
        ("app state", stateChecks),
        ("language", languageChecks),
        ("menu", menuChecks),
        ("main window", mainChecks),
        ("home", homeChecks),
        ("new names", newNamesChecks),
        ("don't show", ignoreChecks),
        ("brief file", briefDocumentChecks),
        ("brief page", briefPageChecks), ("settings", SettingsRoute.checks),
        ("call page", callPageChecks),
        ("call watch", watchChecks),
    ]

    static func run(root: URL) -> Bool {
        AppLog.sink = { _ in }
        let suite = FileManager.default.temporaryDirectory.appendingPathComponent("callcoach-checks-\(getpid())").path
        let store = UserDefaults(suiteName: suite)
        if let store { AppState.store = store }
        defer { store?.removePersistentDomain(forName: suite) }
        LanguageStore.shared.lang = .he
        var failures: [String] = []
        for (name, group) in groups {
            failures += group(root).map { "\(name): \($0)" }
        }
        failures.forEach { print("FAIL \($0)") }
        print(failures.isEmpty ? "self-check passed (\(groups.count) groups)" : "self-check failed: \(failures.count)")
        return failures.isEmpty
    }

    static func rootChecks(_ root: URL) -> [String] {
        var p: [String] = []
        expect(FileManager.default.fileExists(atPath: root.appendingPathComponent("docs/protocol.md").path),
               "repo root not found at \(root.path)", &p)
        expect(AppState.store !== UserDefaults.standard, "no check writes the app's own settings", &p)
        return p
    }
}
