import Foundation

/// The call types there are: the known ones first, then the ones he added in `<data>/call-types`.
enum CallTypes {
    static let known = ["validation", "sales", "general"]

    static func all(data: URL = Paths.data, root: URL = Paths.root) -> [String] {
        var names = Set<String>()
        for folder in [data.appendingPathComponent("call-types"), root.appendingPathComponent("defaults/call-types")] {
            for name in (try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? [] where name.hasSuffix(".md") {
                names.insert(String(name.dropLast(3)))
            }
        }
        if names.isEmpty { return known }
        return known.filter(names.contains) + names.subtracting(known).sorted()
    }
}

var callTypeLabels: [String: String] {
    ["validation": L("ולידציה", "Validation"), "sales": L("מכירה", "Sales"), "general": L("כללית", "General")]
}

/// A brief file in `<data>/briefs/`: what its header says, and when the file last changed. The body is the live
/// model's context, which the engine reads.
struct Brief: Identifiable, Equatable {
    let file: String
    var title: String
    var when: Date? = nil
    var callType = "general"
    var lang = "auto"
    var lengthMin: Double? = nil
    var goals: [String] = []
    /// As written, which may carry a translation after " | "; the engine splits it.
    var questions: [String] = []
    var glossary: [String] = []
    /// `terms:` items, "Term: Hebrew note"; the engine shows them when they are said.
    var terms: [String] = []
    /// `links:` and `after:` (the call folder it follows); the engine ignores both.
    var links: [String] = []
    var after: String? = nil
    var changed = Date.distantPast

    var id: String { file }
}

/// The brief shown in the main window, the other briefs it can be swapped for, and whether a call now uses it.
struct BriefChoice: Equatable {
    var shown: Brief?
    var others: [Brief] = []
    var due = false

    var dueBrief: Brief? { due ? shown : nil }
}

enum Briefs {
    static let pastWindow: TimeInterval = 60 * 60
    static let aheadWindow: TimeInterval = 7 * 86_400
    static let undatedLife: TimeInterval = 12 * 3600
    static let dueBefore: TimeInterval = 30 * 60
    static let dueAfter: TimeInterval = 60 * 60
    static let mostGoals = 5
    static let mostQuestions = 3
    static let mostTerms = 20

    static func folder(_ data: URL) -> URL {
        data.appendingPathComponent("briefs")
    }

    /// `when` in a brief's header and in the brief command: local time, `YYYY-MM-DD HH:MM`.
    static func whenFormatter(_ timeZone: TimeZone = .current) -> DateFormatter {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = timeZone
        f.isLenient = false
        f.dateFormat = "yyyy-MM-dd HH:mm"
        return f
    }

    /// The header between two `---` lines at the top; every key is optional and unknown keys are ignored. A file with
    /// no header is plain context, named by its first `# ` heading or its file name.
    static func parse(_ text: String, file: String, changed: Date = .distantPast, types: Set<String> = Set(CallTypes.known),
                      timeZone: TimeZone = .current) -> Brief {
        var lines = text.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
        if let first = lines.first, first.hasPrefix("\u{FEFF}") { lines[0] = String(first.dropFirst()) }
        var values: [String: String] = [:]
        var items: [String: [String]] = [:]
        var body = lines[...]
        if let start = lines.firstIndex(where: { !$0.trimmingCharacters(in: .whitespaces).isEmpty }),
           lines[start].trimmingCharacters(in: .whitespaces) == "---",
           let end = lines[(start + 1)...].firstIndex(where: { $0.trimmingCharacters(in: .whitespaces) == "---" }) {
            var key: String?
            for line in lines[(start + 1)..<end] {
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                if trimmed.hasPrefix("- ") || trimmed == "-" {
                    if let key { items[key, default: []].append(unquoted(String(trimmed.dropFirst()))) }
                    continue
                }
                guard let colon = trimmed.firstIndex(of: ":") else { continue }
                let name = trimmed[..<colon].trimmingCharacters(in: .whitespaces).lowercased()
                key = name
                values[name] = unquoted(String(trimmed[trimmed.index(after: colon)...]))
            }
            body = lines[(end + 1)...]
        }
        func list(_ key: String, commas: Bool) -> [String] {
            if let found = items[key]?.filter({ !$0.isEmpty }), !found.isEmpty { return found }
            let value = values[key] ?? ""
            let bracketed = value.hasPrefix("[") && value.hasSuffix("]")
            let inner = bracketed ? String(value.dropFirst().dropLast()) : value
            return (bracketed || commas ? inner.components(separatedBy: ",") : [inner]).map(unquoted).filter { !$0.isEmpty }
        }
        let heading = body.first { $0.hasPrefix("# ") }.map { $0.dropFirst(2).trimmingCharacters(in: .whitespaces) }
        let titles = [values["title"], heading, (file as NSString).deletingPathExtension]
        let type = values["type"]?.lowercased() ?? ""
        let lang = values["lang"]?.lowercased() ?? ""
        return Brief(file: file, title: NewNames.clean(titles.compactMap { $0 }.first { !$0.isEmpty } ?? file),
                     when: values["when"].flatMap { whenFormatter(timeZone).date(from: $0) },
                     callType: types.contains(type) ? type : "general", lang: ["he", "en"].contains(lang) ? lang : "auto",
                     lengthMin: minutes(values["length"] ?? ""),
                     goals: Array(list("goals", commas: false).prefix(mostGoals)),
                     questions: Array(list("questions", commas: false).prefix(mostQuestions)),
                     glossary: list("glossary", commas: true), terms: Array(list("terms", commas: false).prefix(mostTerms)),
                     links: list("links", commas: false), after: values["after"].flatMap { $0.isEmpty ? nil : $0 },
                     changed: changed)
    }

    /// A brief's opening question as it is shown: the part before its translation.
    static func questionText(_ question: String) -> String {
        let text = question.components(separatedBy: " | ").first ?? question
        return text.trimmingCharacters(in: .whitespaces)
    }

    private static func unquoted(_ text: String) -> String {
        let t = text.trimmingCharacters(in: .whitespaces)
        for quote in ["\"", "'"] where t.count >= 2 && t.hasPrefix(quote) && t.hasSuffix(quote) {
            return String(t.dropFirst().dropLast()).trimmingCharacters(in: .whitespaces)
        }
        return t
    }

    /// The number a length starts with, in minutes, from 1 to 600.
    static func minutes(_ text: String) -> Double? {
        let number = text.trimmingCharacters(in: .whitespaces).prefix { $0.isASCII && ($0.isNumber || $0 == ".") }
        guard let value = Double(number), value > 0, value <= 600 else { return nil }
        return value
    }

    /// The Markdown and text files of the briefs folder, by name; hidden files and backup copies are left out.
    static func load(_ data: URL, types: Set<String> = Set(CallTypes.known), timeZone: TimeZone = .current) -> [Brief] {
        let urls = (try? FileManager.default.contentsOfDirectory(at: folder(data),
                                                                  includingPropertiesForKeys: [.contentModificationDateKey])) ?? []
        return urls.filter { url in
            let name = url.lastPathComponent
            return !name.hasPrefix(".") && !name.contains(".backup") && ["md", "txt"].contains(url.pathExtension.lowercased())
        }
        .compactMap { url in
            guard let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
            let changed = (try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            return parse(text, file: url.lastPathComponent, changed: changed, types: types, timeZone: timeZone)
        }
        .sorted { $0.file < $1.file }
    }

    /// The briefs some call used: the `brief` in each call folder's meta.json.
    static func used(_ data: URL) -> Set<String> {
        let calls = data.appendingPathComponent("calls")
        let names = (try? FileManager.default.contentsOfDirectory(atPath: calls.path)) ?? []
        return Set(names.compactMap { CallLibrary.meta(calls.appendingPathComponent($0))["brief"] as? String }.filter { !$0.isEmpty })
    }

    /// No call used it, and its time is from an hour ago to a week ahead; a brief with no time is one for 12 hours
    /// after its last change.
    static func isCandidate(_ brief: Brief, used: Set<String>, now: Date) -> Bool {
        guard !used.contains(brief.file) else { return false }
        guard let when = brief.when else { return now < brief.changed.addingTimeInterval(undatedLife) }
        return when >= now.addingTimeInterval(-pastWindow) && when <= now.addingTimeInterval(aheadWindow)
    }

    /// From half an hour before its time to an hour after it; a brief with no time is due for its whole 12 hours.
    static func isDue(_ brief: Brief, now: Date) -> Bool {
        guard let when = brief.when else { return now < brief.changed.addingTimeInterval(undatedLife) }
        return now >= when.addingTimeInterval(-dueBefore) && now <= when.addingTimeInterval(dueAfter)
    }

    /// The candidates, nearest time first; a brief with no time counts as now, and of those the newest comes first.
    static func candidates(_ briefs: [Brief], used: Set<String>, now: Date) -> [Brief] {
        briefs.filter { isCandidate($0, used: used, now: now) }.sorted { a, b in
            let da = abs((a.when ?? now).timeIntervalSince(now)), db = abs((b.when ?? now).timeIntervalSince(now))
            if da != db { return da < db }
            if a.changed != b.changed { return a.changed > b.changed }
            return a.file < b.file
        }
    }

    /// The next brief, or the one he chose instead while no call has used it.
    static func choose(_ briefs: [Brief], used: Set<String>, chosen: String?, now: Date) -> BriefChoice {
        let list = candidates(briefs, used: used, now: now)
        let picked = chosen.flatMap { name in used.contains(name) ? nil : briefs.first { $0.file == name } }
        guard let shown = picked ?? list.first else { return BriefChoice() }
        return BriefChoice(shown: shown, others: list.filter { $0.file != shown.file }, due: isDue(shown, now: now))
    }

    /// Today's time, "מחר 14:00", or the date and the time.
    static func whenLabel(_ date: Date, now: Date, calendar: Calendar = .current) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = calendar.timeZone
        f.dateFormat = "HH:mm"
        let time = f.string(from: date)
        if calendar.isDate(date, inSameDayAs: now) { return time }
        if let next = calendar.date(byAdding: .day, value: 1, to: now), calendar.isDate(date, inSameDayAs: next) {
            return L("מחר \(time)", "Tomorrow \(time)")
        }
        if let last = calendar.date(byAdding: .day, value: -1, to: now), calendar.isDate(date, inSameDayAs: last) {
            return L("אתמול \(time)", "Yesterday \(time)")
        }
        let c = calendar.dateComponents([.day, .month], from: date)
        return "\(c.day ?? 0).\(c.month ?? 0) \(time)"
    }

    static func langLabel(_ lang: String) -> String? {
        ["he": L("עברית", "Hebrew"), "en": L("אנגלית", "English")][lang]
    }

    /// When, the call type and the language, the parts it has.
    static func details(_ brief: Brief, now: Date, calendar: Calendar = .current) -> String {
        [brief.when.map { whenLabel($0, now: now, calendar: calendar) }, callTypeLabels[brief.callType] ?? brief.callType,
         langLabel(brief.lang)].compactMap { $0 }.joined(separator: " · ")
    }

    /// The start button's title: the due brief's title, or a plain start.
    static func startTitle(_ choice: BriefChoice) -> String {
        guard let brief = choice.dueBrief else { return L("התחל שיחה", "Start a call") }
        return L("התחל: \(brief.title)", "Start: \(brief.title)")
    }
}

extension StartFields {
    /// A call from the brief's header and the field's text, or from the text alone. The title is the brief's, else the
    /// text's first 60 characters. The questions go as written; the engine splits off their translations.
    init(brief: Brief?, text: String, level: String) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        self.init(level: level, text: text)
        if let brief {
            callType = brief.callType
            lang = brief.lang
            lengthMin = brief.lengthMin
            goals = brief.goals.enumerated().map { Goal(id: "g\($0.offset + 1)", text: $0.element, done: false) }
            glossary = brief.glossary
            questions = brief.questions
            self.brief = brief.file
            title = brief.title
        } else {
            callType = "general"
            title = String(NewNames.clean(text).prefix(60))
        }
        name = callType
    }
}

/// What "הכן תדריך" sends to the brief command: his text, the time of the call, the files' text and the links.
struct BriefRequest: Equatable {
    struct File: Equatable {
        let name, text: String
    }

    var title = ""
    var when: Date? = nil
    var text = ""
    var files: [File] = []
    var links: [String] = []
    /// The call a follow-up brief follows; the app writes it into the new brief, the brief command does not see it.
    var after: String? = nil

    func line(timeZone: TimeZone = .current) -> String {
        var o: [String: Any] = ["text": text, "files": files.map { ["name": $0.name, "text": $0.text] }, "links": links]
        if !title.isEmpty { o["title"] = title }
        if let when { o["when"] = Briefs.whenFormatter(timeZone).string(from: when) }
        let data = (try? JSONSerialization.data(withJSONObject: o, options: [.sortedKeys, .withoutEscapingSlashes])) ?? Data("{}".utf8)
        return String(decoding: data, as: UTF8.self) + "\n"
    }
}

enum BriefOutcome: Equatable {
    case made(path: String)
    case failed(String)

    /// The command's one line of answer; no answer is a failure that points to the log.
    static func read(_ output: Data, status: Int32, failure: (Int32) -> String = briefFailure) -> BriefOutcome {
        let first = output.split(separator: UInt8(ascii: "\n")).first { !$0.allSatisfy { $0 == 0x20 || $0 == 0x0D } }
        let reply = first.flatMap { try? JSONSerialization.jsonObject(with: Data($0)) as? [String: Any] }
        if reply?["ok"] as? Bool == true, let path = reply?["path"] as? String, !path.isEmpty { return .made(path: path) }
        if let message = reply?["message"] as? String, !message.isEmpty { return .failed(message) }
        return .failed(failure(status))
    }

    static var log: String { TextDirection.isolated("~/.callcoach/logs/engine-stderr.log") }

    static func briefFailure(_ status: Int32) -> String {
        L("הכנת התדריך נכשלה (קוד \(status)). הפרטים בקובץ:\n\(log)",
          "Preparing the brief failed (code \(status)). The details are in:\n\(log)")
    }

    static func followUpFailure(_ status: Int32) -> String {
        L("כתיבת הטיוטה נכשלה (קוד \(status)). הפרטים בקובץ:\n\(log)",
          "Writing the draft failed (code \(status)). The details are in:\n\(log)")
    }
}

/// One run of `callcoach-engine brief` or `followup`: the request on its input and one line back. It writes, reads and
/// waits off the main thread; a cancel ends the process and drops its answer. Cancel it on the queue its answer comes on.
final class BriefRun {
    private let process = Process()
    private let queue: DispatchQueue
    private let failure: (Int32) -> String
    private var cancelled = false

    init(executable: URL, arguments: [String], environment: [String: String], log: URL? = nil, queue: DispatchQueue = .main,
         failure: @escaping (Int32) -> String = BriefOutcome.briefFailure) {
        self.queue = queue
        self.failure = failure
        process.executableURL = executable
        process.arguments = arguments
        process.environment = environment
        process.standardError = EngineLink.appendHandle(log) ?? FileHandle.nullDevice
    }

    /// The engine with the account the call would use, as the engine of a call is started.
    static func engine(account: AppAccount?, apiKey: String?) -> BriefRun {
        BriefRun(executable: Paths.engine, arguments: ["brief", "--data", Paths.data.path],
                 environment: EngineLink.environment(account: account, apiKey: apiKey),
                 log: Paths.tools.appendingPathComponent("logs/engine-stderr.log"))
    }

    /// The follow-up email of a call, with the account a call would use; it reads no input.
    static func followUp(account: AppAccount?, apiKey: String?, folder: URL) -> BriefRun {
        BriefRun(executable: Paths.engine, arguments: followUpArguments(data: Paths.data, folder: folder),
                 environment: EngineLink.environment(account: account, apiKey: apiKey),
                 log: Paths.tools.appendingPathComponent("logs/engine-stderr.log"), failure: BriefOutcome.followUpFailure)
    }

    static func followUpArguments(data: URL, folder: URL) -> [String] {
        ["followup", "--data", data.path, "--call", folder.path]
    }

    func start(_ request: String, done: @escaping (BriefOutcome) -> Void) {
        let input = Pipe(), output = Pipe()
        process.standardInput = input
        process.standardOutput = output
        do {
            try process.run()
        } catch {
            let reason = error.localizedDescription
            queue.async { [self] in
                guard !cancelled else { return }
                done(.failed(L("המנוע לא עלה (\(reason)). צריך להריץ את setup.sh.", "The engine did not start (\(reason)). Run setup.sh.")))
            }
            return
        }
        let process = self.process
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            try? input.fileHandleForWriting.write(contentsOf: Data(request.utf8))
            try? input.fileHandleForWriting.close()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            let status = process.terminationStatus
            queue.async { [self] in
                guard !cancelled else { return }
                done(BriefOutcome.read(data, status: status, failure: failure))
            }
        }
    }

    func cancel() {
        cancelled = true
        if process.isRunning { process.terminate() }
    }

    var running: Bool { process.isRunning }
}

extension SelfCheck {
    static func briefChecks(_ root: URL) -> [String] {
        var p: [String] = []
        let zone = TimeZone(identifier: "Asia/Jerusalem")!
        var c = Calendar(identifier: .gregorian)
        c.timeZone = zone
        func at(_ day: Int, _ hour: Int, _ minute: Int = 0) -> Date {
            c.date(from: DateComponents(year: 2026, month: 11, day: day, hour: hour, minute: minute))!
        }
        let types = Set(CallTypes.known)
        let full = """
            ---
            title: שיחת היכרות עם Acme
            when: 2026-11-17 14:00
            type: sales
            lang: en
            length: 30
            goals:
              - להבין איך הם מנהלים היום הרשאות
              - לקבוע פגישת המשך
            questions:
              - מה גרם לכם לחפש פתרון עכשיו?
            glossary: Acme, Okta, SOC 2
            terms:
              - Okta: שירות התחברות לארגונים
              - SOC 2: תקן ביקורת אבטחה
            ---
            ## מי בשיחה
            ## רקע
            ## לא לשכוח
            """
        let brief = Briefs.parse(full, file: "2026-11-17-acme.md", types: types, timeZone: zone)
        expect(brief.title == "שיחת היכרות עם Acme" && brief.when == at(17, 14) && brief.callType == "sales"
               && brief.lang == "en" && brief.lengthMin == 30, "the header \(brief)", &p)
        expect(brief.goals == ["להבין איך הם מנהלים היום הרשאות", "לקבוע פגישת המשך"]
               && brief.questions == ["מה גרם לכם לחפש פתרון עכשיו?"] && brief.glossary == ["Acme", "Okta", "SOC 2"]
               && brief.terms == ["Okta: שירות התחברות לארגונים", "SOC 2: תקן ביקורת אבטחה"],
               "the header lists \(brief.goals) \(brief.questions) \(brief.glossary) \(brief.terms)", &p)
        let translated = Briefs.parse("---\nquestions:\n  - What made you look now? | מה גרם לכם לחפש עכשיו?\n  - Who decides?\n---\n",
                                      file: "t.md")
        expect(translated.questions == ["What made you look now? | מה גרם לכם לחפש עכשיו?", "Who decides?"]
               && translated.questions.map(Briefs.questionText) == ["What made you look now?", "Who decides?"],
               "a question shows the part before its translation \(translated.questions)", &p)
        expect(Briefs.parse("---\nterms: Okta: התחברות\ntitle: x\n---\n", file: "u.md").title == "x", "a terms line does not upset the header", &p)
        let odd = Briefs.parse("\u{FEFF}---\r\ntitle: \"שיחה: Acme\"\r\nwhen: מחר\r\ntype: Demo\r\nlang: fr\r\nlength: חצי שעה\r\n"
                               + "owner: Dana\r\ngoals:\r\n- 1\r\n- 2\r\n- 3\r\n- 4\r\n- 5\r\n- 6\r\nquestions:\r\n- א\r\n- ב\r\n- ג\r\n- ד\r\n"
                               + "glossary: [Acme, 'Okta']\r\n---\r\n# כותרת\r\n", file: "x.md", types: types, timeZone: zone)
        expect(odd.title == "שיחה: Acme" && odd.when == nil && odd.callType == "general" && odd.lang == "auto"
               && odd.lengthMin == nil, "defaults for values that do not read \(odd)", &p)
        expect(odd.goals == ["1", "2", "3", "4", "5"] && odd.questions == ["א", "ב", "ג"] && odd.glossary == ["Acme", "Okta"],
               "lists are cut \(odd.goals) \(odd.questions) \(odd.glossary)", &p)
        let plain = Briefs.parse("רקע\n# תדריך Acme\nדנה, מנהלת IT", file: "acme.md", types: types, timeZone: zone)
        expect(plain.title == "תדריך Acme" && plain.when == nil && plain.callType == "general" && plain.lang == "auto"
               && plain.goals.isEmpty, "a file with no header \(plain)", &p)
        expect(Briefs.parse("דנה, מנהלת IT", file: "dana-acme.md").title == "dana-acme", "no heading: the file name", &p)
        let oldWatch = Briefs.parse("---\ntype: watch\n---\n# שם\n", file: "w.md")
        expect(oldWatch.title == "שם" && oldWatch.callType == "general", "a header with no title, and watch is no type any more", &p)
        expect(Briefs.parse("---\ntitle: x\nבלי סוף", file: "open.md").title == "open", "a header that does not close is text", &p)
        expect(Briefs.parse("---\nlength: 45 דקות\nglossary:\n  - Wiz\n---\n", file: "l.md").lengthMin == 45
               && Briefs.parse("---\nglossary:\n  - Wiz\n---\n", file: "l.md").glossary == ["Wiz"],
               "a length with words and a glossary list", &p)
        expect(Briefs.minutes("0") == nil && Briefs.minutes("601") == nil && Briefs.minutes("12.5") == 12.5, "minutes", &p)

        let now = at(16, 10)
        func dated(_ file: String, _ when: Date?, changed: Date = .distantPast) -> Brief {
            Brief(file: file, title: file, when: when, changed: changed)
        }
        let used: Set<String> = ["used.md"]
        let cases: [(Brief, Bool)] = [
            (dated("early.md", now.addingTimeInterval(-61 * 60)), false), (dated("late.md", now.addingTimeInterval(-59 * 60)), true),
            (dated("week.md", now.addingTimeInterval(7 * 86_400)), true), (dated("far.md", now.addingTimeInterval(7 * 86_400 + 60)), false),
            (dated("fresh.md", nil, changed: now.addingTimeInterval(-(12 * 3600 - 60))), true),
            (dated("stale.md", nil, changed: now.addingTimeInterval(-12 * 3600)), false),
            (dated("used.md", now.addingTimeInterval(3600)), false),
        ]
        for (b, candidate) in cases {
            expect(Briefs.isCandidate(b, used: used, now: now) == candidate, "candidate \(b.file)", &p)
        }
        let due: [(TimeInterval, Bool)] = [(29 * 60, true), (31 * 60, false), (-60 * 60, true), (-61 * 60, false), (0, true)]
        for (offset, isDue) in due {
            expect(Briefs.isDue(dated("d.md", now.addingTimeInterval(offset)), now: now) == isDue, "due at \(offset / 60) minutes", &p)
        }
        expect(Briefs.isDue(dated("u.md", nil, changed: now.addingTimeInterval(-11 * 3600)), now: now)
               && !Briefs.isDue(dated("u.md", nil, changed: now.addingTimeInterval(-13 * 3600)), now: now), "an undated brief is due 12 hours", &p)
        let soon = dated("soon.md", now.addingTimeInterval(20 * 60))
        let before = dated("before.md", now.addingTimeInterval(-50 * 60))
        let tomorrow = dated("tomorrow.md", at(17, 14))
        let order = Briefs.candidates([tomorrow, before, soon] + cases.map(\.0), used: used, now: now).map(\.file)
        expect(order == ["fresh.md", "soon.md", "before.md", "late.md", "tomorrow.md", "week.md"], "nearest first \(order)", &p)
        let choice = Briefs.choose([tomorrow, before, soon], used: used, chosen: nil, now: now)
        expect(choice.shown?.file == "soon.md" && choice.due && choice.others.map(\.file) == ["before.md", "tomorrow.md"],
               "the next brief \(String(describing: choice.shown?.file))", &p)
        let other = Briefs.choose([tomorrow, before, soon], used: used, chosen: "tomorrow.md", now: now)
        expect(other.shown?.file == "tomorrow.md" && !other.due && other.dueBrief == nil
               && other.others.map(\.file) == ["soon.md", "before.md"], "another brief he chose", &p)
        let gone = Briefs.choose([tomorrow, soon], used: ["tomorrow.md"], chosen: "tomorrow.md", now: now)
        expect(gone.shown?.file == "soon.md", "a chosen brief a call used gives way", &p)
        expect(Briefs.choose([dated("far.md", at(30, 9))], used: [], chosen: nil, now: now) == BriefChoice(), "no next brief", &p)

        LanguageStore.shared.lang = .he
        let labels = [at(16, 14), at(17, 14), at(15, 23, 30), at(20, 9, 5)].map { Briefs.whenLabel($0, now: now, calendar: c) }
        expect(labels == ["14:00", "מחר 14:00", "אתמול 23:30", "20.11 09:05"], "when labels \(labels)", &p)
        expect(Briefs.details(brief, now: now, calendar: c) == "מחר 14:00 · מכירה · אנגלית" && Briefs.details(plain, now: now) == "כללית",
               "card details \(Briefs.details(brief, now: now, calendar: c))", &p)
        expect(Briefs.startTitle(choice) == "התחל: soon.md" && Briefs.startTitle(other) == "התחל שיחה", "start titles", &p)
        LanguageStore.shared.lang = .en
        expect(Briefs.whenLabel(at(17, 14), now: now, calendar: c) == "Tomorrow 14:00" && Briefs.startTitle(other) == "Start a call",
               "English labels", &p)
        LanguageStore.shared.lang = .he

        let fromBrief = StartFields(brief: brief, text: "  דנה מצטרפת באיחור \n", level: "quiet")
        expect(fromBrief == StartFields(callType: "sales", lang: "en", level: "quiet", text: "דנה מצטרפת באיחור",
                                        brief: "2026-11-17-acme.md", lengthMin: 30,
                                        goals: [Goal(id: "g1", text: "להבין איך הם מנהלים היום הרשאות", done: false),
                                                Goal(id: "g2", text: "לקבוע פגישת המשך", done: false)],
                                        glossary: ["Acme", "Okta", "SOC 2"], name: "sales", title: "שיחת היכרות עם Acme",
                                        questions: ["מה גרם לכם לחפש פתרון עכשיו?"]), "start fields from a brief \(fromBrief)", &p)
        let long = String(repeating: "שיחה עם Acme ", count: 8)
        let fromText = StartFields(brief: nil, text: long, level: "normal")
        expect(fromText.callType == "general" && fromText.lang == "auto" && fromText.brief.isEmpty && fromText.goals.isEmpty
               && fromText.title.count == 60 && fromText.text == long.trimmingCharacters(in: .whitespaces) && fromText.name == "general",
               "start fields from the text \(fromText)", &p)
        let lecture = StartFields(brief: nil, text: "וובינר\nשל Acme", level: "normal")
        expect(lecture.callType == "general" && lecture.brief.isEmpty && lecture.title == "וובינר של Acme",
               "a lecture is a call like any other \(lecture)", &p)
        expect(StartFields(brief: nil, text: "", level: "normal").title.isEmpty, "no text, no title", &p)

        let fm = FileManager.default
        let data = fm.temporaryDirectory.appendingPathComponent("callcoach-briefs-\(getpid())")
        defer { try? fm.removeItem(at: data) }
        for folder in ["briefs", "calls/2026-11-15_1000_sales", "calls/2026-11-15_1200_general", "call-types"] {
            try? fm.createDirectory(at: data.appendingPathComponent(folder), withIntermediateDirectories: true)
        }
        for name in ["b.md", "a.md", ".hidden.md", "a.backup.md", "notes.pdf", "c.txt"] {
            try? "# \(name)".write(to: data.appendingPathComponent("briefs/\(name)"), atomically: true, encoding: .utf8)
        }
        try? #"{"started": 1, "brief": "a.md"}"#.write(to: data.appendingPathComponent("calls/2026-11-15_1000_sales/meta.json"),
                                                     atomically: true, encoding: .utf8)
        try? "not json".write(to: data.appendingPathComponent("calls/2026-11-15_1200_general/meta.json"), atomically: true, encoding: .utf8)
        let loaded = Briefs.load(data)
        expect(loaded.map(\.file) == ["a.md", "b.md", "c.txt"] && loaded.first?.title == "a.md" && loaded.first?.changed != .distantPast,
               "briefs in the folder \(loaded.map(\.file))", &p)
        expect(Briefs.used(data) == ["a.md"] && Briefs.used(data.appendingPathComponent("nothing")).isEmpty, "used briefs", &p)
        for name in ["custom", "general", "sales", "validation"] {
            fm.createFile(atPath: data.appendingPathComponent("call-types/\(name).md").path, contents: Data("x".utf8))
        }
        expect(CallTypes.all(data: data, root: data) == ["validation", "sales", "general", "custom"], "call types", &p)
        expect(CallTypes.all(data: data.appendingPathComponent("x"), root: data.appendingPathComponent("x")) == CallTypes.known,
               "known types are the fallback", &p)

        let request = BriefRequest(when: at(17, 14), text: "שיחה עם דנה מ-Acme",
                                   files: [BriefRequest.File(name: "acme.pdf", text: "Acme")], links: ["https://acme.example/about"])
        expect(request.line(timeZone: zone) == #"{"files":[{"name":"acme.pdf","text":"Acme"}],"links":["https://acme.example/about"],"text":"שיחה עם דנה מ-Acme","when":"2026-11-17 14:00"}"# + "\n",
               "the brief request \(request.line(timeZone: zone))", &p)
        expect(BriefRequest(title: "Acme", text: "x").line().contains(#""title":"Acme""#) && !BriefRequest(text: "x").line().contains("when"),
               "title and when only when given", &p)
        expect(BriefOutcome.read(Data(#"{"ok": true, "path": "/d/briefs/a.md"}"#.utf8 + [10]), status: 0) == .made(path: "/d/briefs/a.md")
               && BriefOutcome.read(Data(#"{"ok": false, "message": "אין חשבון"}"#.utf8), status: 1) == .failed("אין חשבון"),
               "the brief answers", &p)
        if case .failed(let message) = BriefOutcome.read(Data("Traceback\n".utf8), status: 3, failure: BriefOutcome.followUpFailure) {
            expect(message.hasPrefix("כתיבת הטיוטה נכשלה (קוד 3)"), "a follow-up with no answer says so \(message)", &p)
        } else {
            p.append("a follow-up with no answer is not a failure")
        }
        expect(BriefRun.followUpArguments(data: URL(fileURLWithPath: "/d"), folder: URL(fileURLWithPath: "/d/calls/2026-11-16_1030_sales"))
               == ["followup", "--data", "/d", "--call", "/d/calls/2026-11-16_1030_sales"], "the follow-up command", &p)
        if case .failed(let message) = BriefOutcome.read(Data("Traceback\n".utf8), status: 2) {
            expect(message.contains("2") && message.hasSuffix("\n" + TextDirection.isolated("~/.callcoach/logs/engine-stderr.log")),
                   "no answer points to the log, on a line of its own and kept whole \(message)", &p)
        } else {
            p.append("a command with no answer is not a failure")
        }

        let queue = DispatchQueue(label: "brief")
        let input = data.appendingPathComponent("stdin.txt")
        let answered = DispatchSemaphore(value: 0)
        var outcome: BriefOutcome?
        let run = BriefRun(executable: URL(fileURLWithPath: "/bin/sh"),
                           arguments: ["-c", "cat > '\(input.path)'; printf '{\"ok\": true, \"path\": \"/d/briefs/2026-11-17-acme.md\"}\\n'"],
                           environment: [:], queue: queue)
        run.start(request.line(timeZone: zone)) { outcome = $0; answered.signal() }
        _ = answered.wait(timeout: .now() + 10)
        expect(outcome == .made(path: "/d/briefs/2026-11-17-acme.md")
               && (try? String(contentsOf: input, encoding: .utf8)) == request.line(timeZone: zone), "the brief command \(String(describing: outcome))", &p)
        var late: BriefOutcome?
        let slow = BriefRun(executable: URL(fileURLWithPath: "/bin/sleep"), arguments: ["20"], environment: [:], queue: queue)
        slow.start("{}\n") { late = $0 }
        queue.sync { slow.cancel() }
        Thread.sleep(forTimeInterval: 0.5)
        queue.sync {}
        expect(late == nil && !slow.running, "a cancelled brief ends and says nothing", &p)
        var missing: BriefOutcome?
        let none = BriefRun(executable: data.appendingPathComponent("no-engine"), arguments: [], environment: [:], queue: queue)
        none.start("{}\n") { missing = $0; answered.signal() }
        _ = answered.wait(timeout: .now() + 5)
        if case .failed(let message)? = missing {
            expect(message.contains("setup.sh"), "an engine that does not start \(message)", &p)
        } else {
            p.append("an engine that does not start gives no failure")
        }
        return p
    }
}
