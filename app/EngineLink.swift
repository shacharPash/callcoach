import Foundation

protocol EngineChannel: AnyObject {
    var onEvent: (Event) -> Void { get set }
    var onExit: (Bool) -> Void { get set }
    func start() throws
    func send(_ command: Command)
    func closeInput()
    func terminate()
    func now() -> Double
}

final class EngineLink: EngineChannel {
    static let passThrough = ["CALLCOACH_TOOLS", "CALLCOACH_DATA"]

    var onEvent: (Event) -> Void = { _ in }
    var onExit: (Bool) -> Void = { _ in }
    var onBadLine: (String) -> Void = { AppLog.write("engine line not understood: \($0.prefix(200))") }
    private let process = Process()
    private let input = Pipe()
    private let output = Pipe()
    private let queue: DispatchQueue
    private let finished = DispatchGroup()
    private var buffer = Data()
    private var sawStopped = false

    static func baseEnvironment(from inherited: [String: String] = ProcessInfo.processInfo.environment,
                                home: String = NSHomeDirectory()) -> [String: String] {
        var env = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": home, "LANG": "en_US.UTF-8",
                   "USER": NSUserName(), "LOGNAME": NSUserName()]
        for key in passThrough { if let value = inherited[key] { env[key] = value } }
        return env
    }

    static func environment(account: AppAccount?, apiKey: String?, tools: URL = Paths.tools,
                            inherited: [String: String] = ProcessInfo.processInfo.environment,
                            export: URL? = Settings.exportFolder()) -> [String: String] {
        var env = baseEnvironment(from: inherited)
        // The folder every finished call is copied to; the engine copies nothing when it is not set.
        if let export { env["CALLCOACH_EXPORT"] = export.path }
        guard let account else {
            env["CALLCOACH_PROVIDER"] = "none"
            return env
        }
        env["CALLCOACH_PROVIDER"] = account.kind.provider
        env["CALLCOACH_ACCOUNT_NAME"] = account.name
        if account.kind == .subscription {
            env["CALLCOACH_ACCOUNT_DIR"] = AccountStore.folder(for: account.name, tools: tools).path
        }
        if account.kind == .apiKey, let apiKey { env["CALLCOACH_API_KEY"] = apiKey }
        return env
    }

    static func serve(environment: [String: String]) -> EngineLink {
        EngineLink(executable: Paths.engine, arguments: ["serve", "--data", Paths.data.path], environment: environment,
                   log: Paths.tools.appendingPathComponent("logs/engine-stderr.log"))
    }

    init(executable: URL, arguments: [String], environment: [String: String], log: URL? = nil,
         queue: DispatchQueue = .main) {
        self.queue = queue
        process.executableURL = executable
        process.arguments = arguments
        process.environment = environment
        process.standardInput = input
        process.standardOutput = output
        process.standardError = Self.appendHandle(log) ?? FileHandle.nullDevice
    }

    /// The log file, opened for appending.
    static func appendHandle(_ url: URL?) -> FileHandle? {
        guard let url else { return nil }
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        let handle = try? FileHandle(forWritingTo: url)
        _ = try? handle?.seekToEnd()
        return handle
    }

    func start() throws {
        finished.enter()
        finished.enter()
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                self?.outputEnded()
                return
            }
            self?.received(data)
        }
        process.terminationHandler = { [weak self] _ in self?.finished.leave() }
        do {
            try process.run()
        } catch {
            output.fileHandleForReading.readabilityHandler = nil
            throw error
        }
        finished.notify(queue: queue) { [weak self] in
            guard let self else { return }
            self.onExit(self.sawStopped)
        }
    }

    private func received(_ data: Data) {
        buffer.append(data)
        while let newline = buffer.firstIndex(of: 0x0A) {
            let line = String(decoding: buffer[buffer.startIndex..<newline], as: UTF8.self)
            buffer.removeSubrange(buffer.startIndex...newline)
            decode(line)
        }
    }

    private func outputEnded() {
        if !buffer.isEmpty {
            decode(String(decoding: buffer, as: UTF8.self))
            buffer.removeAll()
        }
        finished.leave()
    }

    private func decode(_ line: String) {
        guard !line.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        guard let (event, _) = try? Event.decode(line) else {
            let report = onBadLine
            queue.async { report(line) }
            return
        }
        if event == .stopped { sawStopped = true }
        queue.async { self.onEvent(event) }
    }

    func send(_ command: Command) {
        try? input.fileHandleForWriting.write(contentsOf: Data(command.line.utf8))
    }

    func closeInput() {
        try? input.fileHandleForWriting.close()
    }

    func terminate() {
        if process.isRunning { process.terminate() }
    }

    func now() -> Double {
        Date().timeIntervalSince1970
    }
}

final class StreamPlayer: EngineChannel {
    var onEvent: (Event) -> Void = { _ in }
    var onExit: (Bool) -> Void = { _ in }
    private let items: [(Event, Double)]
    private let speed: Double
    private let t0: Double
    private var startedAt = Date()
    private var cancelled = false

    init(file: URL, speed: Double) throws {
        let text = try String(contentsOf: file, encoding: .utf8)
        items = text.split(separator: "\n").compactMap { line in
            (try? Event.decode(String(line))).map { ($0.0, $0.1 ?? 0) }
        }
        self.speed = max(speed, 0.1)
        t0 = items.first?.1 ?? 0
    }

    func now() -> Double {
        t0 + Date().timeIntervalSince(startedAt) * speed
    }

    func start() throws {
        startedAt = Date()
        for (event, t) in items {
            DispatchQueue.main.asyncAfter(deadline: .now() + (t - t0) / speed) { [weak self] in
                guard let self, !self.cancelled else { return }
                self.onEvent(event)
                if event == .stopped { self.onExit(true) }
            }
        }
    }

    func send(_ command: Command) {
        FileHandle.standardError.write(Data(("command " + command.line).utf8))
    }

    func closeInput() {}

    func terminate() {
        cancelled = true
    }
}

extension SelfCheck {
    static func run(_ link: EngineLink, send: [Command] = [], timeout: Double = 10) -> (events: [Event], clean: Bool?) {
        let done = DispatchSemaphore(value: 0)
        var events: [Event] = []
        var clean: Bool?
        link.onEvent = { events.append($0) }
        link.onExit = { clean = $0; done.signal() }
        do { try link.start() } catch { return ([], nil) }
        send.forEach(link.send)
        link.closeInput()
        _ = done.wait(timeout: .now() + timeout)
        return (events, clean)
    }

    static func linkChecks(_ root: URL) -> [String] {
        var p: [String] = []
        let inherited = ["ANTHROPIC_BASE_URL": "x", "CLAUDE_CODE_OAUTH_SCOPES": "y", "CALLCOACH_API_KEY": "old",
                         "CALLCOACH_TOOLS": "/t", "PATH": "/opt/x:/usr/bin"]
        let sub = EngineLink.environment(account: AppAccount(name: "work", kind: .subscription), apiKey: nil,
                                         tools: URL(fileURLWithPath: "/t"), inherited: inherited)
        expect(Set(sub.keys) == ["PATH", "HOME", "LANG", "USER", "LOGNAME", "CALLCOACH_TOOLS", "CALLCOACH_PROVIDER", "CALLCOACH_ACCOUNT_NAME",
                                 "CALLCOACH_ACCOUNT_DIR"], "subscription environment \(sub.keys.sorted())", &p)
        expect(sub["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin" && sub["CALLCOACH_PROVIDER"] == "claude"
               && sub["CALLCOACH_ACCOUNT_DIR"] == "/t/accounts/work", "subscription values", &p)
        let api = EngineLink.environment(account: AppAccount(name: "home", kind: .apiKey), apiKey: "k",
                                         inherited: inherited)
        expect(api["CALLCOACH_API_KEY"] == "k" && api["CALLCOACH_PROVIDER"] == "api" && api["CALLCOACH_ACCOUNT_DIR"] == nil,
               "api environment", &p)
        expect(EngineLink.environment(account: nil, apiKey: nil, inherited: [:])["CALLCOACH_PROVIDER"] == "none",
               "no account means no agent", &p)

        let stream = root.appendingPathComponent("tests/protocol/stream/demo-call.jsonl").path
        let count = ((try? String(contentsOfFile: stream, encoding: .utf8)) ?? "").split(separator: "\n").count
        let cat = run(EngineLink(executable: URL(fileURLWithPath: "/bin/cat"), arguments: [stream], environment: [:],
                                 queue: DispatchQueue(label: "events")))
        expect(count > 0 && cat.events.count == count && cat.clean == true,
               "cat stream: \(cat.events.count) of \(count) events, clean \(String(describing: cat.clean))", &p)
        let crash = run(EngineLink(executable: URL(fileURLWithPath: "/bin/sh"),
                                   arguments: ["-c", "echo '{\"type\": \"hello\", \"protocol\": 2, \"version\": \"x\"}'; exit 3"],
                                   environment: [:], queue: DispatchQueue(label: "events")))
        expect(crash.events.count == 1 && crash.clean == false, "a crash is not clean", &p)
        let file = FileManager.default.temporaryDirectory.appendingPathComponent("callcoach-stdin-\(getpid()).txt")
        defer { try? FileManager.default.removeItem(at: file) }
        _ = run(EngineLink(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", "cat > '\(file.path)'"],
                           environment: [:], queue: DispatchQueue(label: "events")),
                send: [.accountCheck, .setLevel("quiet")])
        let written = (try? String(contentsOf: file, encoding: .utf8)) ?? ""
        expect(written == "{\"type\":\"account_check\"}\n{\"level\":\"quiet\",\"type\":\"set_level\"}\n",
               "commands on stdin: \(written)", &p)

        let tail = run(EngineLink(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", "printf '{\"type\": \"stopped\"}'"],
                                  environment: [:], queue: DispatchQueue(label: "events")))
        expect(tail.events == [.stopped] && tail.clean == true, "a last line without a newline is still read", &p)
        var bad: [String] = []
        let noisy = EngineLink(executable: URL(fileURLWithPath: "/bin/sh"),
                               arguments: ["-c", "printf 'not json\\n{\"type\": \"stopped\"}\\n'"],
                               environment: [:], queue: DispatchQueue(label: "events"))
        noisy.onBadLine = { bad.append($0) }
        let noisyRun = run(noisy)
        expect(bad == ["not json"] && noisyRun.events == [.stopped], "undecodable lines are reported: \(bad)", &p)

        func cpuSeconds() -> Double {
            var u = rusage()
            getrusage(RUSAGE_SELF, &u)
            return Double(u.ru_utime.tv_sec + u.ru_stime.tv_sec) + Double(u.ru_utime.tv_usec + u.ru_stime.tv_usec) / 1e6
        }
        do {
            let released = EngineLink(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", "echo '{\"type\": \"stopped\"}'"],
                                      environment: [:], queue: DispatchQueue(label: "events"))
            try? released.start()
        }
        Thread.sleep(forTimeInterval: 0.3)
        let before = cpuSeconds()
        Thread.sleep(forTimeInterval: 0.5)
        let spent = cpuSeconds() - before
        expect(spent < 0.2, "a released link stops reading at the end of its output (\(spent) s of CPU in 0.5 s)", &p)
        return p
    }
}
