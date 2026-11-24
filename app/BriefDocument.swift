import AppKit
import Foundation

/// A brief file as the brief page edits it: the header keys the page edits, the header lines it does not (`glossary`,
/// or a key his own AI added), kept as they were, and the body. It reads and writes the format the engine reads
/// (engine/callcoach/brief.py), with two keys the engine ignores: `links` and `after`.
struct BriefDocument: Equatable {
    static let edited: Set<String> = ["title", "when", "type", "lang", "length", "goals", "questions", "terms", "links", "after"]

    var title = ""
    var when: Date? = nil
    var callType = "general"
    var lang = "auto"
    var lengthMin: Double? = nil
    var goals: [String] = []
    /// Each one "text" or "text | translation", as the file has it.
    var questions: [String] = []
    /// Each one "Term: Hebrew note".
    var terms: [String] = []
    var links: [String] = []
    /// The folder name of the call this brief follows.
    var after: String? = nil
    var kept: [String] = []
    var body = ""

    static func parse(_ text: String, timeZone: TimeZone = .current) -> BriefDocument {
        var lines = text.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
        if let first = lines.first, first.hasPrefix("\u{FEFF}") { lines[0] = String(first.dropFirst()) }
        var doc = BriefDocument()
        guard let start = lines.firstIndex(where: { !$0.trimmingCharacters(in: .whitespaces).isEmpty }),
              lines[start].trimmingCharacters(in: .whitespaces) == "---",
              let end = lines[(start + 1)...].firstIndex(where: { $0.trimmingCharacters(in: .whitespaces) == "---" }) else {
            doc.body = text.trimmingCharacters(in: .whitespacesAndNewlines)
            return doc
        }
        var values: [String: String] = [:]
        var items: [String: [String]] = [:]
        var key: String?
        for line in lines[(start + 1)..<end] {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("- ") || trimmed == "-" {
                if let key, edited.contains(key) {
                    items[key, default: []].append(unquoted(String(trimmed.dropFirst())))
                } else if key != nil {
                    doc.kept.append(line)
                }
                continue
            }
            guard let colon = trimmed.firstIndex(of: ":") else {
                if !trimmed.isEmpty { doc.kept.append(line) }
                continue
            }
            let name = trimmed[..<colon].trimmingCharacters(in: .whitespaces).lowercased()
            key = name
            if edited.contains(name) {
                values[name] = unquoted(String(trimmed[trimmed.index(after: colon)...]))
            } else {
                doc.kept.append(line)
            }
        }
        func list(_ key: String) -> [String] {
            if let found = items[key]?.filter({ !$0.isEmpty }), !found.isEmpty { return found }
            let value = values[key] ?? ""
            let bracketed = value.hasPrefix("[") && value.hasSuffix("]")
            let inner = bracketed ? String(value.dropFirst().dropLast()) : value
            return (bracketed ? inner.components(separatedBy: ",") : [inner]).map(unquoted).filter { !$0.isEmpty }
        }
        doc.title = NewNames.clean(values["title"] ?? "")
        doc.when = values["when"].flatMap { Briefs.whenFormatter(timeZone).date(from: $0) }
        let type = (values["type"] ?? "").lowercased()
        doc.callType = type.isEmpty ? "general" : type
        let lang = (values["lang"] ?? "").lowercased()
        doc.lang = ["he", "en"].contains(lang) ? lang : "auto"
        doc.lengthMin = Briefs.minutes(values["length"] ?? "")
        doc.goals = list("goals")
        doc.questions = list("questions")
        doc.terms = list("terms")
        doc.links = list("links")
        let after = values["after"] ?? ""
        doc.after = after.isEmpty ? nil : after
        doc.body = lines[(end + 1)...].joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        return doc
    }

    /// The header in the engine's order, the kept lines where the glossary goes, then the page's own keys and the body.
    func render(timeZone: TimeZone = .current) -> String {
        func one(_ text: String) -> String { NewNames.clean(text) }
        func items(_ key: String, _ list: [String]) -> [String] {
            let clean = list.map(one).filter { !$0.isEmpty }
            return clean.isEmpty ? [] : ["\(key):"] + clean.map { "  - \($0)" }
        }
        var lines = ["---"]
        if !one(title).isEmpty { lines.append("title: \(one(title))") }
        if let when { lines.append("when: \(Briefs.whenFormatter(timeZone).string(from: when))") }
        lines.append("type: \(one(callType).isEmpty ? "general" : one(callType))")
        lines.append("lang: \(lang)")
        if let lengthMin { lines.append("length: \(String(format: "%g", lengthMin))") }
        lines += items("goals", goals) + items("questions", questions) + kept + items("terms", terms) + items("links", links)
        if let after, !one(after).isEmpty { lines.append("after: \(one(after))") }
        lines.append("---")
        let text = body.trimmingCharacters(in: .whitespacesAndNewlines)
        return (lines + (text.isEmpty ? [] : [text])).joined(separator: "\n") + "\n"
    }

    private static func unquoted(_ text: String) -> String {
        let t = text.trimmingCharacters(in: .whitespaces)
        for quote in ["\"", "'"] where t.count >= 2 && t.hasPrefix(quote) && t.hasSuffix(quote) {
            return String(t.dropFirst().dropLast()).trimmingCharacters(in: .whitespaces)
        }
        return t
    }
}

/// A brief that changed on disk after the page read it.
struct BriefChanged: Error {}

/// Reading and writing brief files, the files added to a brief, and the calls that used one.
enum BriefStore {
    static func url(_ data: URL, _ file: String) -> URL {
        Briefs.folder(data).appendingPathComponent(file)
    }

    static func changed(_ url: URL) -> Date? {
        try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate
    }

    /// The document and when the file last changed; nil when it cannot be read.
    static func load(_ data: URL, _ file: String) -> (doc: BriefDocument, changed: Date?)? {
        let url = url(data, file)
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        return (BriefDocument.parse(text), changed(url))
    }

    /// Writes the file whole. A file that changed on disk since `read` is left as it is.
    static func save(_ doc: BriefDocument, data: URL, file: String, read: Date?) throws {
        let url = url(data, file)
        if let now = changed(url), let read, abs(now.timeIntervalSince(read)) > 0.001 { throw BriefChanged() }
        try FileManager.default.createDirectory(at: Briefs.folder(data), withIntermediateDirectories: true)
        try doc.render().write(to: url, atomically: true, encoding: .utf8)
    }

    /// `<YYYY-MM-DD>-<slug>.md` as the engine names a brief (the date of its time, else today), with -2, -3 when taken.
    static func newFile(title: String, when: Date?, data: URL, today: Date = Date()) -> String {
        let day = Briefs.whenFormatter().string(from: when ?? today).prefix(10)
        let slug = String(title.replacingOccurrences(of: "[^\\p{L}\\p{N}_-]+", with: "-", options: .regularExpression)
            .trimmingCharacters(in: CharacterSet(charactersIn: "-")).prefix(40))
        let base = "\(day)-\(slug.isEmpty ? "brief" : slug)"
        var name = base + ".md"
        var n = 2
        while FileManager.default.fileExists(atPath: url(data, name).path) {
            name = "\(base)-\(n).md"
            n += 1
        }
        return name
    }

    /// Creates an empty brief with its title and time, and returns its file name.
    static func create(title: String, when: Date?, data: URL) throws -> String {
        let file = newFile(title: title, when: when, data: data)
        var doc = BriefDocument()
        doc.title = title
        doc.when = when
        try save(doc, data: data, file: file, read: nil)
        return file
    }

    /// The folder of the files added to a brief: `<name>.files` beside it.
    static func filesFolder(_ data: URL, _ file: String) -> URL {
        Briefs.folder(data).appendingPathComponent((file as NSString).deletingPathExtension + ".files")
    }

    static func files(_ data: URL, _ file: String) -> [URL] {
        let folder = filesFolder(data, file)
        let names = (try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? []
        return names.filter { !$0.hasPrefix(".") }.sorted().map { folder.appendingPathComponent($0) }
    }

    /// Copies the files in; a name already there gets " 2", " 3" before its extension.
    static func add(_ urls: [URL], data: URL, file: String) throws {
        let folder = filesFolder(data, file)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        for source in urls where source.isFileURL {
            let ext = source.pathExtension
            let stem = source.deletingPathExtension().lastPathComponent
            var target = folder.appendingPathComponent(source.lastPathComponent)
            var n = 2
            while FileManager.default.fileExists(atPath: target.path) {
                target = folder.appendingPathComponent(ext.isEmpty ? "\(stem) \(n)" : "\(stem) \(n).\(ext)")
                n += 1
            }
            try FileManager.default.copyItem(at: source, to: target)
        }
    }

    /// A file taken out of a brief goes to the Trash, so it can come back.
    static func remove(_ url: URL) throws {
        try FileManager.default.trashItem(at: url, resultingItemURL: nil)
    }
}

/// The briefs list's three groups.
enum BriefGroup: String, CaseIterable {
    case upcoming, undated, past

    var title: String {
        switch self {
        case .upcoming: return L("הבאים", "Coming up")
        case .undated: return L("בלי מועד", "No time")
        case .past: return L("היו", "Past")
        }
    }

    /// Coming up: a time from an hour ago on and no call yet, nearest first. No time: no time and no call, the last
    /// changed first. Past: a call used it or its time passed, newest first.
    static func groups(_ briefs: [Brief], calls: [String: Int], now: Date) -> [(group: BriefGroup, briefs: [Brief])] {
        var found: [BriefGroup: [Brief]] = [:]
        for brief in briefs {
            let used = (calls[brief.file] ?? 0) > 0
            let group: BriefGroup
            if used { group = .past } else if let when = brief.when {
                group = when >= now.addingTimeInterval(-Briefs.pastWindow) ? .upcoming : .past
            } else { group = .undated }
            found[group, default: []].append(brief)
        }
        let upcoming = (found[.upcoming] ?? []).sorted { ($0.when ?? now, $0.file) < ($1.when ?? now, $1.file) }
        let undated = (found[.undated] ?? []).sorted { ($0.changed, $1.file) > ($1.changed, $0.file) }
        let past = (found[.past] ?? []).sorted { ($0.when ?? $0.changed, $1.file) > ($1.when ?? $1.changed, $0.file) }
        return [(.upcoming, upcoming), (.undated, undated), (.past, past)].filter { !$0.1.isEmpty }
    }

    /// "היום 14:00", "מחר 10:30" or "בלי מועד", and the number of calls when there are some.
    static func subtitle(_ brief: Brief, calls: Int, now: Date) -> String {
        let when = brief.when.map { date in
            Calendar.current.isDate(date, inSameDayAs: now) ? L("היום ", "Today ") + Briefs.whenLabel(date, now: now)
                : Briefs.whenLabel(date, now: now)
        } ?? L("בלי מועד", "No time")
        guard calls > 0 else { return when }
        return when + " · " + (calls == 1 ? L("שיחה אחת", "1 call") : L("\(calls) שיחות", "\(calls) calls"))
    }
}

extension SelfCheck {
    static func briefDocumentChecks(_ root: URL) -> [String] {
        var p: [String] = []
        let zone = TimeZone(identifier: "Asia/Jerusalem")!
        let text = """
        ---
        title: "שיחת היכרות עם דנה"
        when: 2026-11-19 14:00
        type: sales
        lang: he
        length: 30
        goals:
          - להבין מי מאשר רכישה
        questions:
          - What made you look now? | מה גרם לכם לחפש עכשיו?
        glossary: Okta, SCIM
        source: drive
          - פריט של מפתח זר
        terms:
          - SCIM: תקן לסנכרון משתמשים
        links:
          - https://example.com/acme
        after: 2026-11-18_1131_general
        ---
        ## מי בשיחה
        דנה, מנהלת IT
        """
        var doc = BriefDocument.parse(text, timeZone: zone)
        expect(doc.title == "שיחת היכרות עם דנה" && doc.callType == "sales" && doc.lang == "he" && doc.lengthMin == 30
               && doc.goals == ["להבין מי מאשר רכישה"] && doc.questions == ["What made you look now? | מה גרם לכם לחפש עכשיו?"]
               && doc.terms == ["SCIM: תקן לסנכרון משתמשים"] && doc.links == ["https://example.com/acme"]
               && doc.after == "2026-11-18_1131_general" && doc.body == "## מי בשיחה\nדנה, מנהלת IT",
               "a brief file is read \(doc)", &p)
        expect(doc.kept == ["glossary: Okta, SCIM", "source: drive", "  - פריט של מפתח זר"], "unknown keys are kept \(doc.kept)", &p)
        expect(BriefDocument.parse(doc.render(timeZone: zone), timeZone: zone) == doc, "written and read again, it is the same", &p)
        let brief = Briefs.parse(doc.render(timeZone: zone), file: "x.md", timeZone: zone)
        expect(brief.title == doc.title && brief.glossary == ["Okta", "SCIM"] && brief.terms == doc.terms
               && brief.when == doc.when && brief.links == doc.links && brief.after == doc.after,
               "the app's reader reads what the page writes", &p)
        doc.when = nil
        doc.goals = ["  ", "שורה\nשבורה"]
        doc.lengthMin = nil
        let plain = doc.render(timeZone: zone)
        expect(!plain.contains("when:") && !plain.contains("length:") && plain.contains("goals:\n  - שורה שבורה\nquestions:"),
               "empty and broken lines are cleaned \(plain.prefix(200))", &p)
        let loose = BriefDocument.parse("# רק הערות\nבלי כותרת", timeZone: zone)
        expect(loose.title.isEmpty && loose.body == "# רק הערות\nבלי כותרת" && loose.render(timeZone: zone).hasPrefix("---\ntype: general\nlang: auto\n---\n# רק הערות"),
               "a file with no header gets one when saved", &p)

        let fm = FileManager.default
        let data = fm.temporaryDirectory.appendingPathComponent("callcoach-briefdoc-\(UUID().uuidString)")
        defer { try? fm.removeItem(at: data) }
        let when = Briefs.whenFormatter().date(from: "2026-11-19 14:00")
        let made = try? BriefStore.create(title: "היכרות עם Acme / דנה", when: when, data: data)
        let again = try? BriefStore.create(title: "היכרות עם Acme / דנה", when: when, data: data)
        expect(made == "2026-11-19-היכרות-עם-Acme-דנה.md" && again == "2026-11-19-היכרות-עם-Acme-דנה-2.md",
               "a new brief is named as the engine names one \(String(describing: made)) \(String(describing: again))", &p)
        if let made, let loaded = BriefStore.load(data, made) {
            var edit = loaded.doc
            edit.goals = ["מטרה"]
            expect((try? BriefStore.save(edit, data: data, file: made, read: loaded.changed)) != nil, "a saved brief", &p)
            let stale = loaded.changed?.addingTimeInterval(-60)
            expect((try? BriefStore.save(edit, data: data, file: made, read: stale)) == nil, "a brief changed on disk is not overwritten", &p)
            expect(BriefStore.load(data, made)?.doc.goals == ["מטרה"], "the saved goals", &p)
            let source = data.appendingPathComponent("notes.txt")
            try? "x".write(to: source, atomically: true, encoding: .utf8)
            try? BriefStore.add([source, source], data: data, file: made)
            let names = BriefStore.files(data, made).map(\.lastPathComponent)
            expect(names == ["notes 2.txt", "notes.txt"] && Briefs.load(data).count == 2,
                   "added files sit beside the brief and are not briefs \(names)", &p)
        } else {
            expect(false, "the new brief can be read", &p)
        }

        let now = Briefs.whenFormatter().date(from: "2026-11-18 12:00")!
        func dated(_ file: String, _ hours: Double?, changed: Double = 0) -> Brief {
            Brief(file: file, title: file, when: hours.map { now.addingTimeInterval($0 * 3600) },
                  changed: now.addingTimeInterval(changed * 3600))
        }
        let briefs = [dated("later.md", 30), dated("soon.md", 2), dated("missed.md", -3), dated("done.md", 1),
                      dated("old-note.md", nil, changed: -40), dated("new-note.md", nil, changed: -1)]
        let groups = BriefGroup.groups(briefs, calls: ["done.md": 1], now: now)
        expect(groups.map(\.group) == [.upcoming, .undated, .past]
               && groups[0].briefs.map(\.file) == ["soon.md", "later.md"]
               && groups[1].briefs.map(\.file) == ["new-note.md", "old-note.md"]
               && groups[2].briefs.map(\.file) == ["done.md", "missed.md"],
               "the three groups \(groups.map { $0.briefs.map(\.file) })", &p)
        expect(BriefGroup.subtitle(dated("x", 2), calls: 0, now: now) == "היום 14:00"
               && BriefGroup.subtitle(dated("x", nil), calls: 2, now: now) == "בלי מועד · 2 שיחות",
               "a row's time and calls", &p)
        return p
    }
}
