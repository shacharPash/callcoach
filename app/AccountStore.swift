import Foundation
import Security

enum AccountKind: String, Codable, CaseIterable, Identifiable {
    case subscription, apiKey = "api", fake

    var id: String { rawValue }
    var provider: String { self == .subscription ? "claude" : rawValue }
    var label: String {
        switch self {
        case .subscription: return L("מנוי Claude", "Claude subscription")
        case .apiKey: return L("מפתח API", "API key")
        case .fake: return L("סוכן לדוגמה (פיתוח)", "Sample agent (development)")
        }
    }
}

struct AppAccount: Codable, Equatable, Identifiable {
    var name: String
    var kind: AccountKind
    /// The name I gave it, in any language; accounts from before have none and show their folder name.
    var label: String? = nil
    /// How a subscription signs in: "subscription", "console" or "sso"; accounts from before have none.
    var loginMode: String? = nil
    var id: String { name }
    var shown: String {
        guard let label, !label.trimmingCharacters(in: .whitespaces).isEmpty else { return name }
        return label
    }
}

struct AccountFile: Codable {
    var accounts: [AppAccount] = []
    var active: String?
    /// Folder names of removed accounts, never given to a new one (the sign-in is keyed on the folder path).
    var retired: [String]? = nil
}

struct AccountError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

final class AccountStore {
    static let service = "app.callcoach.api-key"

    let tools: URL
    private(set) var state: AccountFile
    /// Runs for a removed subscription account; the self-check gives its own, so nothing is logged out.
    private let logout: (String) -> Void

    init(tools: URL = Paths.tools, logout: ((String) -> Void)? = nil) {
        self.tools = tools
        let data = try? Data(contentsOf: tools.appendingPathComponent("accounts.json"))
        state = data.flatMap { try? JSONDecoder().decode(AccountFile.self, from: $0) } ?? AccountFile()
        self.logout = logout ?? { Self.logoutAndTrash($0, tools: tools) }
    }

    var accounts: [AppAccount] { state.accounts }
    var active: AppAccount? { state.accounts.first { $0.name == state.active } }

    static func validName(_ name: String) -> Bool {
        name.range(of: "^[a-z0-9][a-z0-9_-]{0,31}$", options: .regularExpression) != nil
    }

    /// The folder name of a new account: the English letters and digits of its name with dashes between words, else
    /// "account", with a number after it when the name is taken.
    static func folderName(for label: String, taken: [String]) -> String {
        var base = ""
        for scalar in label.lowercased().folding(options: .diacriticInsensitive, locale: Locale(identifier: "en")).unicodeScalars {
            if ("a"..."z").contains(scalar) || ("0"..."9").contains(scalar) {
                base.unicodeScalars.append(scalar)
            } else if !base.isEmpty && base.last != "-" {
                base.append("-")
            }
        }
        base = String(base.trimmingCharacters(in: CharacterSet(charactersIn: "-")).prefix(24))
            .trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        if base.isEmpty { base = "account" }
        guard taken.contains(base) else { return base }
        var number = 2
        while taken.contains("\(base)-\(number)") { number += 1 }
        return "\(base)-\(number)"
    }

    /// What is wrong with a new account's name, or nil.
    static func labelProblem(_ label: String, taken: [String]) -> String? {
        let name = label.trimmingCharacters(in: .whitespaces)
        if name.isEmpty { return L("צריך לתת לחשבון שם", "Give the account a name") }
        if name.count > 40 { return L("שם קצר יותר, עד 40 תווים", "A shorter name, up to 40 characters") }
        if taken.contains(where: { $0.compare(name, options: [.caseInsensitive, .diacriticInsensitive]) == .orderedSame }) {
            return L("כבר יש חשבון בשם הזה", "There is already an account with this name")
        }
        return nil
    }

    /// A new account from the name I typed: its folder name is made here, the name is kept as its label.
    func add(label: String, kind: AccountKind, loginMode: String? = nil) throws -> AppAccount {
        if let problem = Self.labelProblem(label, taken: accounts.map(\.shown)) { throw AccountError(message: problem) }
        let account = AppAccount(name: Self.folderName(for: label, taken: accounts.map(\.name) + (state.retired ?? [])), kind: kind,
                                 label: label.trimmingCharacters(in: .whitespaces), loginMode: loginMode)
        try add(account)
        return account
    }

    static func folder(for name: String, tools: URL = Paths.tools) -> URL {
        tools.appendingPathComponent("accounts/\(name)")
    }

    func add(_ account: AppAccount) throws {
        guard Self.validName(account.name) else {
            throw AccountError(message: L("שם חשבון: אותיות קטנות באנגלית, ספרות, - ו-_ בלבד",
                                          "Account name: lowercase English letters, digits, - and _ only"))
        }
        state.accounts.removeAll { $0.name == account.name }
        state.accounts.append(account)
        if active == nil { state.active = account.name }
        try save()
    }

    /// Removes an account and its API key. A subscription account is also logged out of the Claude CLI, which keeps
    /// its login in the Keychain, and then its folder goes to the Trash.
    func remove(_ name: String) throws {
        let subscription = state.accounts.first { $0.name == name }?.kind == .subscription
        Self.deleteKey(for: name)
        state.accounts.removeAll { $0.name == name }
        state.retired = (state.retired ?? []) + [name]
        if state.active == name { state.active = state.accounts.first?.name }
        if subscription { logout(name) }
        try save()
    }

    func activate(_ name: String) throws {
        guard state.accounts.contains(where: { $0.name == name }) else { return }
        state.active = name
        try save()
    }

    private func save() throws {
        try FileManager.default.createDirectory(at: tools, withIntermediateDirectories: true)
        let url = tools.appendingPathComponent("accounts.json")
        try JSONEncoder().encode(state).write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    static func setKey(_ key: String, for name: String) throws {
        deleteKey(for: name)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
            kSecAttrAccount as String: name, kSecValueData as String: Data(key.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
        ]
        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw AccountError(message: L("שמירה ב-Keychain נכשלה (\(status))",
                                          "Saving to the Keychain failed (\(status))"))
        }
    }

    /// The API key of an account and the Keychain status of the read.
    static func readKey(for name: String) -> (key: String?, status: OSStatus) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
            kSecAttrAccount as String: name, kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess else { return (nil, status) }
        guard let data = item as? Data, let key = String(data: data, encoding: .utf8), !key.isEmpty else {
            return (nil, errSecDecode)
        }
        return (key, status)
    }

    static func deleteKey(for name: String) {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service, kSecAttrAccount as String: name]
        SecItemDelete(query as CFDictionary)
    }

    private static func quoted(_ s: String) -> String { "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'" }

    func loginScript(for name: String, mode: String, root: URL = Paths.root) throws -> URL {
        guard Self.validName(name) else { throw AccountError(message: L("שם חשבון לא תקין", "Invalid account name")) }
        let folder = tools.appendingPathComponent("run")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let url = folder.appendingPathComponent("login-\(name).command")
        let flag = ["console": " --console", "sso": " --sso"][mode] ?? ""
        let script = """
        #!/bin/zsh
        cd \(Self.quoted(root.path)) || exit 1
        env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME="$HOME" USER="$USER" LOGNAME="$LOGNAME" LANG=en_US.UTF-8 \(tools.path == Paths.tools.path ? "" : "CALLCOACH_TOOLS=\(Self.quoted(tools.path)) ")bin/callcoach-engine account login --name \(name)\(flag)
        echo
        echo "Done. You can close this window and press Check in CallCoach."

        """
        try script.write(to: url, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: url.path)
        return url
    }

    /// Runs `account logout` for a removed account in the background, then moves its folder to the Trash. Best effort:
    /// the account is already removed, and a logout that fails leaves only the CLI's own Keychain item.
    static func logoutAndTrash(_ name: String, tools: URL, root: URL = Paths.root) {
        DispatchQueue.global().async {
            let process = Process()
            process.executableURL = root.appendingPathComponent("bin/callcoach-engine")
            process.arguments = ["account", "logout", "--name", name]
            var environment = EngineLink.baseEnvironment()
            environment["CALLCOACH_TOOLS"] = tools.path
            process.environment = environment
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            if (try? process.run()) != nil { process.waitUntilExit() }
            try? FileManager.default.trashItem(at: folder(for: name, tools: tools), resultingItemURL: nil)
        }
    }

    func status(of name: String, root: URL = Paths.root) -> (loggedIn: Bool, message: String) {
        let process = Process()
        process.executableURL = root.appendingPathComponent("bin/callcoach-engine")
        process.arguments = ["account", "status", "--name", name]
        process.environment = EngineLink.baseEnvironment()
        let out = Pipe()
        process.standardOutput = out
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return (false, error.localizedDescription) }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        struct Reply: Decodable { let logged_in: Bool; let message: String }
        guard let reply = try? JSONDecoder().decode(Reply.self, from: data) else {
            return (false, L("בדיקת הסטטוס נכשלה", "The status check failed"))
        }
        return (reply.logged_in, reply.message)
    }
}

extension SelfCheck {
    static func accountChecks(_ root: URL) -> [String] {
        var p: [String] = []
        for (name, ok) in [("work", true), ("home-2", true), ("Work", false), ("../x", false), ("", false),
                           (String(repeating: "a", count: 33), false)] {
            expect(AccountStore.validName(name) == ok, "name \(name)", &p)
        }
        expect(AccountStore.folderName(for: "פרטי", taken: []) == "account", "a Hebrew name gets a plain folder name", &p)
        expect(AccountStore.folderName(for: "פרטי", taken: ["account"]) == "account-2", "folder names never repeat", &p)
        expect(AccountStore.folderName(for: "Work Max", taken: []) == "work-max", "English letters are kept", &p)
        expect(AccountStore.folderName(for: "work", taken: ["work", "work-2"]) == "work-3", "the next free number", &p)
        let old = try? JSONDecoder().decode(AccountFile.self, from: Data(#"{"accounts":[{"name":"work","kind":"subscription"}],"active":"work"}"#.utf8))
        expect(old?.accounts.first?.shown == "work" && old?.accounts.first?.label == nil, "an account from before keeps its name", &p)
        let labeled = AppAccount(name: "account", kind: .subscription, label: "פרטי")
        expect(labeled.shown == "פרטי" && labeled.id == "account", "the label shows, the folder name stays the id", &p)
        expect(AccountStore.labelProblem("  ", taken: []) == L("צריך לתת לחשבון שם", "Give the account a name"), "an empty name", &p)
        expect(AccountStore.labelProblem("פרטי", taken: ["פרטי"]) == L("כבר יש חשבון בשם הזה", "There is already an account with this name"),
               "a name that is taken", &p)
        expect(AccountStore.labelProblem("פרטי", taken: ["work"]) == nil, "a free name", &p)
        expect(AccountStore.folderName(for: String(repeating: "a", count: 23) + " bcd", taken: []) == String(repeating: "a", count: 23),
               "a folder name never ends in a dash", &p)
        var loggedOut: [String] = []
        let reuse = AccountStore(tools: FileManager.default.temporaryDirectory.appendingPathComponent("callcoach-check-reuse-\(getpid())"),
                                 logout: { loggedOut.append($0) })
        defer { try? FileManager.default.removeItem(at: reuse.tools) }
        let first = try? reuse.add(label: "Check reuse", kind: .subscription)
        try? reuse.remove(first?.name ?? "")
        let second = try? reuse.add(label: "Check reuse", kind: .subscription)
        expect(first != nil && second != nil && first?.name != second?.name, "a removed account's folder name is not reused", &p)
        expect(first != nil && loggedOut == [first?.name ?? ""], "a removed subscription account is logged out of the CLI", &p)
        expect(AccountWatch.keepsChecking(elapsed: 0, signedIn: false) && AccountWatch.keepsChecking(elapsed: 295, signedIn: false),
               "the sign-in is checked for five minutes", &p)
        expect(!AccountWatch.keepsChecking(elapsed: 301, signedIn: false) && !AccountWatch.keepsChecking(elapsed: 10, signedIn: true),
               "and stops when it is signed in or the time is up", &p)
        expect(AccountWatch.every == 5, "every five seconds", &p)
        expect(AccountSheet.primaryTitle(.subscription) == L("התחבר", "Sign in") && AccountSheet.primaryTitle(.apiKey) == L("שמור", "Save"),
               "the sheet's button says what it does", &p)
        expect(!ConnectView.mayRemove(active: true, inCall: true) && ConnectView.mayRemove(active: true, inCall: false)
               && ConnectView.mayRemove(active: false, inCall: true), "the account in use stays during a call", &p)
        let tools = FileManager.default.temporaryDirectory.appendingPathComponent("callcoach-check-\(getpid())")
        defer { try? FileManager.default.removeItem(at: tools) }
        let store = AccountStore(tools: tools, logout: { loggedOut.append($0) })
        try? store.add(AppAccount(name: "work", kind: .subscription))
        try? store.add(AppAccount(name: "check-home", kind: .apiKey))
        expect(store.active?.name == "work", "the first account is active", &p)
        try? store.activate("check-home")
        expect(AccountStore(tools: tools).active?.name == "check-home", "the active account is saved", &p)
        try? store.remove("check-home")
        expect(AccountStore(tools: tools).accounts.map(\.name) == ["work"] && store.active?.name == "work",
               "removing the active account falls back", &p)
        expect(loggedOut.count == 1, "a removed API key account is not logged out", &p)
        expect((try? store.add(AppAccount(name: "Bad Name", kind: .apiKey))) == nil, "bad names are refused", &p)
        let script = (try? store.loginScript(for: "work", mode: "subscription", root: root))
            .flatMap { try? String(contentsOf: $0, encoding: .utf8) } ?? ""
        expect(script.contains("bin/callcoach-engine account login --name work") && script.contains("env -i"),
               "login script \(script)", &p)
        let text = (try? String(contentsOf: tools.appendingPathComponent("accounts.json"), encoding: .utf8)) ?? ""
        expect(!text.contains("sk-"), "no secret in accounts.json", &p)
        return p
    }
}
