import Foundation

/// Updates from GitHub, for a clone of the public repo. A minute after the app opens and once a day, `git fetch` in the
/// clone asks whether GitHub's main has moved on; nothing else is sent. A newer main puts "עדכן את CallCoach…" at the
/// top of the menu bar menu and posts one notification per new main, never during a call (it waits for the call to
/// end). A click asks first, then hands over to tools/update.sh and quits: the script pulls (only straight forward,
/// never over changes of your own), runs ./setup.sh and opens the app again, which says how it went. A folder that is
/// not a clone of the public repo, such as one with no origin, is never checked. "בדוק עדכונים" turns it all off.
enum Updater {
    static let source = "https://github.com/shacharPash/callcoach.git"
    static let checkKey = "checkUpdates"
    static let notifiedKey = "updateNotified"

    struct Found: Equatable {
        let remote: String
        let count: Int
    }

    enum Outcome: Equatable {
        case off, notAClone, upToDate, ahead(Found), diverged, failed(String)
    }

    /// What the script wrote for the app to read when it opens again.
    struct Result: Decodable, Equatable {
        let ok: Bool
        let step: String
        let from: String
        let to: String
    }

    enum Notice: Equatable { case post, later, none }

    static var resultFile: URL { Paths.tools.appendingPathComponent("run/update-result.json") }
    static var logFile: URL { Paths.tools.appendingPathComponent("logs/update.log") }

    /// On by default.
    static func checksOn(_ defaults: UserDefaults = .standard) -> Bool {
        defaults.object(forKey: checkKey) == nil || defaults.bool(forKey: checkKey)
    }

    static func setChecks(_ on: Bool, _ defaults: UserDefaults = .standard) {
        defaults.set(on, forKey: checkKey)
    }

    /// The repository a remote names, to compare two of them: no scheme, no user, no ".git", lower case.
    static func repoKey(_ url: String) -> String {
        var s = url.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        for scheme in ["https://", "http://", "ssh://", "git://"] where s.hasPrefix(scheme) { s.removeFirst(scheme.count) }
        if let at = s.firstIndex(of: "@"), !s[..<at].contains("/") { s = String(s[s.index(after: at)...]) }
        s = s.replacingOccurrences(of: "github.com:", with: "github.com/")
        while s.hasSuffix("/") { s.removeLast() }
        if s.hasSuffix(".git") { s.removeLast(4) }
        return s
    }

    static func decide(head: String, remote: String, ancestor: Bool, count: Int) -> Outcome {
        if head == remote { return .upToDate }
        return ancestor && count > 0 ? .ahead(Found(remote: remote, count: count)) : .diverged
    }

    /// A new main is posted once; during a call it waits for the call to end.
    static func notice(_ found: Found, notified: String?, inCall: Bool) -> Notice {
        if found.remote == notified { return .none }
        return inCall ? .later : .post
    }

    /// The source a test clone may stand in for GitHub with; the public repo otherwise.
    static var expected: String { ProcessInfo.processInfo.environment["CALLCOACH_UPDATE_SOURCE"] ?? source }

    /// Runs git in the clone; nil when git cannot run or takes longer than `timeout` seconds.
    static func git(_ root: URL, _ args: [String], timeout: Double = 60) -> (code: Int32, out: String)? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        process.arguments = ["-C", root.path] + args
        var env = ProcessInfo.processInfo.environment
        env["GIT_TERMINAL_PROMPT"] = "0"
        process.environment = env
        let out = Pipe()
        process.standardOutput = out
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return nil }
        let timer = DispatchWorkItem { if process.isRunning { process.terminate() } }
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: timer)
        let data = out.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        timer.cancel()
        return (process.terminationStatus, String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines))
    }

    /// Blocks while git runs: call it off the main thread.
    static func check(root: URL, defaults: UserDefaults = .standard) -> Outcome {
        guard checksOn(defaults) else { return .off }
        guard let origin = git(root, ["remote", "get-url", "origin"]), origin.code == 0,
              repoKey(origin.out) == repoKey(expected) else { return .notAClone }
        guard git(root, ["fetch", "--quiet", "origin", "+refs/heads/main:refs/remotes/origin/main"])?.code == 0 else {
            return .failed("fetch")
        }
        guard let head = git(root, ["rev-parse", "HEAD"]), head.code == 0,
              let remote = git(root, ["rev-parse", "refs/remotes/origin/main"]), remote.code == 0 else { return .failed("rev-parse") }
        let ancestor = git(root, ["merge-base", "--is-ancestor", "HEAD", "refs/remotes/origin/main"])?.code == 0
        let count = Int(git(root, ["rev-list", "--count", "HEAD..refs/remotes/origin/main"])?.out ?? "") ?? 0
        return decide(head: head.out, remote: remote.out, ancestor: ancestor, count: count)
    }

    static func describe(_ outcome: Outcome) -> String {
        switch outcome {
        case .off: return "off"
        case .notAClone: return "not a clone of \(expected)"
        case .upToDate: return "up to date"
        case .ahead(let found): return "update: \(found.count) new commit(s), main at \(found.remote.prefix(7))"
        case .diverged: return "the clone has commits of its own; no update offered"
        case .failed(let step): return "check failed at \(step)"
        }
    }

    /// Changes of your own in files git tracks; the script would refuse them, so the app says so before it quits.
    static func localChanges(_ root: URL) -> Bool {
        guard let status = git(root, ["status", "--porcelain", "--untracked-files=no"]), status.code == 0 else { return true }
        return !status.out.isEmpty
    }

    /// The script updates only the main branch.
    static func onMain(_ root: URL) -> Bool {
        git(root, ["symbolic-ref", "--quiet", "--short", "HEAD"])?.out == "main"
    }

    /// Where setup.sh installs the app. The update opens that copy again, so a copy that runs from anywhere else would
    /// stay old beside a new engine: it gets no update button press, only the way to do it by hand.
    static var installedApp: URL { URL(fileURLWithPath: NSHomeDirectory() + "/Applications/CallCoach.app") }

    static func isInstalled(_ bundle: URL, installed: URL = installedApp) -> Bool {
        bundle.resolvingSymlinksInPath().standardizedFileURL.path == installed.resolvingSymlinksInPath().standardizedFileURL.path
    }

    /// Copies the script out of the clone (the pull may replace it while it runs) and starts it on its own, so it
    /// outlives the app. nil once the script says it is running (it writes RESULT.started first), else what went wrong.
    static func start(root: URL, app: URL = Bundle.main.bundleURL) -> String? {
        let fm = FileManager.default
        let copy = Paths.tools.appendingPathComponent("run/update.sh")
        do {
            try fm.createDirectory(at: copy.deletingLastPathComponent(), withIntermediateDirectories: true)
            try fm.createDirectory(at: logFile.deletingLastPathComponent(), withIntermediateDirectories: true)
            if fm.fileExists(atPath: copy.path) { try fm.removeItem(at: copy) }
            try fm.copyItem(at: root.appendingPathComponent("tools/update.sh"), to: copy)
            try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: copy.path)
            try? fm.removeItem(at: resultFile)
            try? fm.removeItem(atPath: resultFile.path + ".started")
        } catch {
            return error.localizedDescription
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = ["-c", #""$0" "$@" >> "$UPDATE_LOG" 2>&1 < /dev/null &"#,
                             copy.path, root.path, app.path, String(getpid()), resultFile.path]
        var env = ProcessInfo.processInfo.environment
        env["UPDATE_LOG"] = logFile.path
        process.environment = env
        do { try process.run() } catch { return error.localizedDescription }
        process.waitUntilExit()
        for _ in 0..<30 {
            if fm.fileExists(atPath: resultFile.path + ".started") { return nil }
            Thread.sleep(forTimeInterval: 0.1)
        }
        return L("סקריפט העדכון לא התחיל", "the update script did not start")
    }

    /// The script's result, read once: the file is removed.
    static func take(_ file: URL) -> Result? {
        guard let data = try? Data(contentsOf: file) else { return nil }
        try? FileManager.default.removeItem(at: file)
        return try? JSONDecoder().decode(Result.self, from: data)
    }

    static var menuTitle: String { L("עדכן את CallCoach…", "Update CallCoach…") }
    static var checkTitle: String { L("בדוק עדכונים", "Check for updates") }

    static func changes(_ count: Int) -> String {
        count == 1 ? L("שינוי אחד", "1 change") : L("\(count) שינויים", "\(count) changes")
    }

    static var foundNote: (title: String, body: String) {
        (L("יש עדכון ל-CallCoach", "A CallCoach update is out"),
         L("לחיצה כאן, או \"עדכן את CallCoach…\" בתפריט בסרגל העליון, מתקינה אותו.",
           "Click here, or \"Update CallCoach…\" in the menu bar menu, to install it."))
    }

    static func confirmText(_ found: Found) -> (title: String, body: String, update: String, cancel: String) {
        (L("לעדכן את CallCoach עכשיו?", "Update CallCoach now?"),
         L("האפליקציה תיסגר, תוריד מ-GitHub את הגרסה החדשה (\(changes(found.count))), תתקין אותה ותיפתח שוב לבד. זה לוקח בערך דקה או שתיים. אם macOS יבקש שוב גישה למיקרופון או להקלטת שמע המערכת, צריך לאשר.",
           "The app closes, gets the new version from GitHub (\(changes(found.count))), installs it and opens again on its own. It takes a minute or two. If macOS asks again for Microphone or System Audio Recording access, allow it."),
         L("עדכן עכשיו", "Update now"), L("לא עכשיו", "Not now"))
    }

    static func branchText(_ root: URL) -> String {
        L("התיקייה של CallCoach לא על הענף main, אז לא עדכנתי. התיקייה:\n\(root.path)",
          "The CallCoach folder is not on the main branch, so nothing was updated. The folder:\n\(root.path)")
    }

    static func notInstalledText(_ bundle: URL, root: URL) -> String {
        L("העדכון מתקין את CallCoach בתיקייה ~/Applications, והאפליקציה הזאת פתוחה מתיקייה אחרת:\n\(bundle.path)\nכדי לעדכן, מריצים בטרמינל את הפקודה cd \(root.path) && git pull && ./setup.sh ופותחים את CallCoach מהתיקייה ~/Applications.",
          "The update installs CallCoach in ~/Applications, and this app runs from somewhere else:\n\(bundle.path)\nTo update, run cd \(root.path) && git pull && ./setup.sh in Terminal and open CallCoach from ~/Applications.")
    }

    static var inCallText: String {
        L("אפשר לעדכן אחרי שהשיחה תיגמר.", "You can update once the call ends.")
    }

    static func changesText(_ root: URL) -> String {
        L("בתיקייה של CallCoach יש שינויים שלך בקבצים, אז לא עדכנתי. התיקייה:\n\(root.path)",
          "The CallCoach folder has changes of your own, so nothing was updated. The folder:\n\(root.path)")
    }

    static func startFailedText(_ error: String) -> String {
        L("לא הצלחתי להתחיל את העדכון: \(error)", "Could not start the update: \(error)")
    }

    /// The notification after the app opens again; `count` is how many commits came in.
    static func resultNote(_ result: Result, count: Int?) -> (title: String, body: String) {
        if result.ok {
            if result.from == result.to { return (L("אין עדכון חדש", "No new update"), L("הגרסה כבר הייתה הכי חדשה.", "It was already the newest version.")) }
            return (L("העדכון של CallCoach הותקן", "CallCoach is updated"),
                    count.map { L("נכנסו \(changes($0)).", "\(changes($0)) came in.") } ?? L("הגרסה החדשה פתוחה.", "The new version is open."))
        }
        let log = logFile.path.replacingOccurrences(of: NSHomeDirectory(), with: "~")
        let why: String
        switch result.step {
        case "changes": why = L("בתיקייה יש שינויים שלך בקבצים, אז שום דבר לא השתנה.", "The folder has changes of your own, so nothing changed.")
        case "diverged": why = L("בתיקייה יש קומיטים שלך שאין ב-GitHub, אז שום דבר לא השתנה.", "The folder has commits of your own that GitHub does not have, so nothing changed.")
        case "branch": why = L("התיקייה לא על הענף main, אז שום דבר לא השתנה.", "The folder is not on the main branch, so nothing changed.")
        case "fetch": why = L("לא הצלחתי להוריד את העדכון מ-GitHub. כדאי לבדוק את החיבור ולנסות שוב.", "Could not get the update from GitHub. Check the connection and try again.")
        case "setup": why = L("ההתקנה נכשלה, אז התיקייה חזרה לגרסה הקודמת. הלוג: \(log)", "The install failed, so the folder went back to the previous version. The log: \(log)")
        case "restore": why = L("ההתקנה נכשלה, וגם ההתקנה מחדש של הגרסה הקודמת. כדאי להריץ בטרמינל את ./setup.sh בתיקייה של CallCoach. הלוג: \(log)", "The install failed, and so did setting the previous version up again. Run ./setup.sh in the CallCoach folder in Terminal. The log: \(log)")
        case "running": why = L("האפליקציה לא נסגרה בזמן, אז העדכון לא רץ.", "The app did not close in time, so the update did not run.")
        default: why = L("משהו השתבש. הלוג: \(log)", "Something went wrong. The log: \(log)")
        }
        return (L("העדכון לא הותקן", "The update was not installed"), why)
    }

    static func checks(_ root: URL) -> [String] {
        var p: [String] = []
        let keys = [source, "https://github.com/shacharPash/callcoach", "git@github.com:shacharPash/callcoach.git",
                    "https://github.com/ShacharPash/callcoach/", "ssh://git@github.com/shacharPash/callcoach.git"].map(repoKey)
        expect(Set(keys) == ["github.com/shacharpash/callcoach"], "every way to write the public repo is the same repo \(keys)", &p)
        expect(repoKey("https://github.com/someone/callcoach.git") != repoKey(source), "a fork is another repo", &p)
        expect(decide(head: "a", remote: "a", ancestor: true, count: 0) == .upToDate, "the same commit is up to date", &p)
        expect(decide(head: "a", remote: "b", ancestor: true, count: 3) == .ahead(Found(remote: "b", count: 3)), "a newer main is an update", &p)
        expect(decide(head: "a", remote: "b", ancestor: false, count: 2) == .diverged, "commits of my own get no update", &p)
        let found = Found(remote: "b", count: 1)
        expect(notice(found, notified: "a", inCall: false) == .post && notice(found, notified: nil, inCall: true) == .later
               && notice(found, notified: "b", inCall: false) == .none, "one notification per new main, after a call", &p)
        let suite = "callcoach-update-checks-\(getpid())"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        expect(checksOn(defaults), "checks are on by default", &p)
        setChecks(false, defaults)
        expect(!checksOn(defaults) && check(root: root, defaults: defaults) == .off, "off means no check at all", &p)
        let file = FileManager.default.temporaryDirectory.appendingPathComponent("callcoach-update-\(getpid()).json")
        try? #"{"ok": false, "step": "setup", "from": "a", "to": "b"}"#.write(to: file, atomically: true, encoding: .utf8)
        let read = take(file)
        expect(read == Result(ok: false, step: "setup", from: "a", to: "b") && !FileManager.default.fileExists(atPath: file.path),
               "the result is read once \(String(describing: read))", &p)
        let steps = ["changes", "diverged", "branch", "fetch", "setup", "restore", "running", "other"]
        let notes = steps.map { resultNote(Result(ok: false, step: $0, from: "a", to: "a"), count: nil) }
            + [resultNote(Result(ok: true, step: "done", from: "a", to: "b"), count: 2), resultNote(Result(ok: true, step: "done", from: "a", to: "a"), count: 0)]
        let texts = notes.flatMap { [$0.title, $0.body] } + [menuTitle, checkTitle, foundNote.title, foundNote.body, inCallText,
                                                               changesText(root), startFailedText("x"), branchText(root),
                                                               notInstalledText(installedApp, root: root)]
            + { let c = confirmText(found); return [c.title, c.body, c.update, c.cancel] }()
        let latin = texts.filter { $0.first.map { $0.isASCII } ?? true }
        expect(latin.isEmpty, "every text starts in Hebrew \(latin)", &p)
        expect(Set(notes.prefix(steps.count).map(\.body)).count == steps.count, "each failure says its own reason", &p)
        expect(resultNote(Result(ok: true, step: "done", from: "a", to: "b"), count: 2).body == "נכנסו 2 שינויים.", "the count after an update", &p)
        let apps = URL(fileURLWithPath: "/Users/a/Applications/CallCoach.app")
        expect(isInstalled(URL(fileURLWithPath: "/Users/a/Applications/./CallCoach.app/"), installed: apps)
               && !isInstalled(URL(fileURLWithPath: "/Applications/CallCoach.app"), installed: apps), "only the copy setup.sh installs updates itself", &p)
        let script = root.appendingPathComponent("tools/update.sh").path
        expect(FileManager.default.isExecutableFile(atPath: script), "tools/update.sh is there and runs", &p)
        return p
    }
}
