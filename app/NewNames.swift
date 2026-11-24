import Foundation
import SwiftUI

/// One name from the call folder's terms.json, which the engine writes when the call ends.
struct CallTerm: Decodable, Equatable {
    let term, note: String
    var kind: String? = nil
    var known: Bool? = nil
    /// The few sentences the engine gave when its row was opened in the panel.
    var detail: String? = nil

    var key: String { NewNames.key(term) }
}

/// The names a call brought up that no knowledge file had, and the knowledge file they are added to.
enum NewNames {
    static let fileName = "שמות-משיחות.md"

    static let header = """
        שמות שעלו בשיחות ונוספו לידע: מהחלון הראשי בכפתור "הוסף לקובץ הידע", או מהפאנל בזמן שיחה בכפתור "הוסף לידע".
        אפשר לערוך את הקובץ ביד: לתקן תקציר, להוסיף כתיבים מופרדים בפסיקים, להוסיף שורות תחת "שלנו" או למחוק רשומה.
        כל רשומה מתחילה בשורה של שני סימני סולמית ואחריהם השם, כמו בשאר קובצי הידע. השורות שלפני הרשומה הראשונה לא נקראות.

        """

    static let day: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    static func file(_ data: URL) -> URL {
        data.appendingPathComponent("knowledge").appendingPathComponent(fileName)
    }

    /// The names in the call folder's terms.json that were not known, each once, in the order they came.
    static func load(_ folder: URL) -> [CallTerm] {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("terms.json")),
              let terms = try? JSONDecoder().decode([CallTerm].self, from: data) else { return [] }
        var seen = Set<String>()
        return terms.filter { $0.known != true && !$0.key.isEmpty && seen.insert($0.key).inserted }
    }

    static func clean(_ text: String) -> String {
        text.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    }

    /// The words a name is matched by, folded as the engine folds them: case, niqqud, geresh and quotes removed,
    /// dashes, dots and slashes as spaces, then the runs of letters and digits.
    static func words(_ text: String) -> [String] {
        var out: [String] = []
        var word = String.UnicodeScalarView()
        func close() {
            if !word.isEmpty { out.append(String(word)) }
            word = String.UnicodeScalarView()
        }
        for scalar in text.folding(options: .caseInsensitive, locale: nil).unicodeScalars {
            let v = scalar.value
            if v == 0x2D || v == 0x5BE || (0x2010...0x2015).contains(v) || v == 0x2E || v == 0x2F {
                close()
            } else if (0x591...0x5C7).contains(v) || [0x27, 0x22, 0x60, 0x5F3, 0x5F4, 0x2018, 0x2019, 0x201C, 0x201D].contains(v) {
                continue
            } else if isWordScalar(scalar) {
                word.append(scalar)
            } else {
                close()
            }
        }
        close()
        return out
    }

    private static func isWordScalar(_ scalar: Unicode.Scalar) -> Bool {
        switch scalar.properties.generalCategory {
        case .uppercaseLetter, .lowercaseLetter, .titlecaseLetter, .modifierLetter, .otherLetter, .decimalNumber,
             .letterNumber, .otherNumber: return true
        default: return false
        }
    }

    /// A name's words joined, so "Team 8" and "Team8" are the same name, as in the engine's joined index.
    static func key(_ name: String) -> String {
        words(name).joined()
    }

    static func kindLabel(_ kind: String?) -> String {
        ["company": "חברה", "person": "אדם", "product": "מוצר", "term": "מושג", "fund": "קרן", "org": "ארגון",
         "event": "אירוע"][kind ?? ""] ?? "מושג"
    }

    /// A knowledge file entry. The spellings are comma separated, so a comma in the name is left out of them. The
    /// summary is the explanation, else the note, else the kind, since an entry without a summary is not read.
    static func entry(_ term: CallTerm, call: String, date: String) -> String {
        let name = clean(term.term)
        let summary = [clean(term.detail ?? ""), clean(term.note)].first { !$0.isEmpty } ?? kindLabel(term.kind)
        return """
            ## \(name)
            סוג: \(kindLabel(term.kind))
            כתיבים: \(clean(name.replacingOccurrences(of: ",", with: "")))
            תקציר: \(summary)
            שלנו:
            - עלה בשיחה "\(clean(call))", \(date)

            """
    }

    /// The keys of a knowledge file's names: each entry's "## " heading and its spellings. A spelling's trailing * is not
    /// a letter, so it is left out.
    static func names(_ text: String) -> Set<String> {
        var out = Set<String>()
        var inEntry = false
        let text = text.hasPrefix("\u{FEFF}") ? String(text.dropFirst()) : text
        for raw in text.components(separatedBy: .newlines) {
            if raw.hasPrefix("## ") {
                inEntry = true
                out.insert(key(String(raw.dropFirst(3))))
            } else if raw.hasPrefix("# ") {
                inEntry = false
            } else if inEntry, let colon = raw.firstIndex(of: ":"),
                      raw[..<colon].trimmingCharacters(in: .whitespaces) == "כתיבים" {
                raw[raw.index(after: colon)...].split(separator: ",").forEach { out.insert(key(String($0))) }
            }
        }
        out.remove("")
        return out
    }

    /// The keys of the names in every knowledge file the engine reads; a backup copy is not read.
    static func known(_ data: URL) -> Set<String> {
        let folder = data.appendingPathComponent("knowledge")
        let files = (try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? []
        return files.filter { $0.hasSuffix(".md") && !$0.hasPrefix(".") && !$0.contains(".backup") }
            .reduce(into: Set<String>()) { keys, name in
                if let text = try? String(contentsOf: folder.appendingPathComponent(name), encoding: .utf8) {
                    keys.formUnion(names(text))
                }
            }
    }

    /// Appends the name's entry to the calls' file, which starts with the header when it is new, and replaces the file
    /// whole. False when a knowledge file already has the name.
    static func add(_ term: CallTerm, from call: CallEntry, data: URL) throws -> Bool {
        if known(data).contains(term.key) { return false }
        let target = file(data)
        var text = header
        if FileManager.default.fileExists(atPath: target.path) {
            text = try String(contentsOf: target, encoding: .utf8)
        } else {
            try FileManager.default.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
        }
        if !text.isEmpty && !text.hasSuffix("\n") { text += "\n" }
        if !text.isEmpty && !text.hasSuffix("\n\n") { text += "\n" }
        text += entry(term, call: call.title, date: day.string(from: call.started))
        try text.write(to: target, atomically: true, encoding: .utf8)
        return true
    }
}

/// The call page's "שמות חדשים" tab: the names no knowledge file had, each with a way to add it to the calls' file.
struct NewNamesView: View {
    let call: CallEntry
    let names: [CallTerm]
    let data: URL
    /// Changes when the main window reloads its calls, so a knowledge file edited by hand is read again.
    var refresh = 0
    @ViewState private var known: Set<String> = []
    @ViewState private var added: Set<String> = []
    @ViewState private var failure: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let failure { DirectedText(failure, size: 12).foregroundStyle(.orange) }
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(names, id: \.key) { row($0) }
                }
                .padding(.vertical, 4)
            }
        }
        .padding(.top, 4)
        .task(id: "\(call.id) \(refresh)") { known = NewNames.known(data) }
    }

    private func row(_ name: CallTerm) -> some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                line(NewNames.clean(name.term), size: 13, weight: .semibold)
                line(NewNames.clean(name.note), size: 12).foregroundStyle(.secondary)
                if let detail = name.detail.map(NewNames.clean), !detail.isEmpty {
                    line(detail, size: 12).fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if known.contains(name.key) {
                Text(added.contains(name.key) ? L("נוסף", "Added") : L("כבר בקובץ הידע", "Already in a knowledge file"))
                    .font(.system(size: 12)).foregroundStyle(.secondary)
            } else {
                Button(L("הוסף לקובץ הידע", "Add to the knowledge file")) { add(name) }.controlSize(.small)
            }
        }
    }

    /// Behind its direction's mark, so a Hebrew line that starts with a Latin word keeps its punctuation in place.
    static func lineText(_ text: String) -> String {
        TextDirection.marked(text)
    }

    /// In its own direction, so a Latin name keeps its punctuation, and on the row's leading side.
    private func line(_ text: String, size: CGFloat, weight: Font.Weight = .regular) -> some View {
        Text(Self.lineText(text)).font(.system(size: size, weight: weight))
            .environment(\.layoutDirection, TextDirection.isRTL(text) ? .rightToLeft : .leftToRight)
    }

    private func add(_ name: CallTerm) {
        do {
            if try NewNames.add(name, from: call, data: data) { added.insert(name.key) }
            failure = nil
        } catch {
            failure = L("השם לא נשמר בקובץ הידע: \(error.localizedDescription)",
                        "The name was not saved to the knowledge file: \(error.localizedDescription)")
        }
        known = NewNames.known(data)
    }
}

extension SelfCheck {
    static func newNamesChecks(_ root: URL) -> [String] {
        var p: [String] = []
        let okta = CallTerm(term: "Okta", note: "שירות התחברות וניהול זהויות", kind: "product", known: false)
        expect(NewNamesView.lineText("Okta הוא שירות התחברות לארגונים.") == "\u{200F}Okta הוא שירות התחברות לארגונים."
               && NewNamesView.lineText("Okta") == "\u{200E}Okta", "an explanation that starts in English keeps a Hebrew line", &p)
        expect(TextDirection.isolated("~/a.log") == "\u{2066}~/a.log\u{2069}", "a path inside Hebrew is isolated", &p)
        let entry = NewNames.entry(okta, call: "שיחה", date: "2026-11-16")
        expect(entry == "## Okta\nסוג: מוצר\nכתיבים: Okta\nתקציר: שירות התחברות וניהול זהויות\nשלנו:\n- עלה בשיחה \"שיחה\", 2026-11-16\n",
               "entry \(entry)", &p)
        let messy = CallTerm(term: " Acme, Inc.\nLabs ", note: "שורה ראשונה\r\nושנייה\n", kind: "company")
        let lines = NewNames.entry(messy, call: "מכירה\n", date: "2026-11-02").components(separatedBy: "\n")
        expect(lines == ["## Acme, Inc. Labs", "סוג: חברה", "כתיבים: Acme Inc. Labs", "תקציר: שורה ראשונה ושנייה", "שלנו:",
                         "- עלה בשיחה \"מכירה\", 2026-11-02", ""], "commas and newlines \(lines)", &p)
        let bare = NewNames.entry(CallTerm(term: "Wiz", note: " \n", kind: "company"), call: "שיחה", date: "2026-11-16")
        expect(bare.contains("\nתקציר: חברה\n"), "an empty note \(bare)", &p)
        let explained = CallTerm(term: "Okta", note: "התחברות", kind: "product", detail: "שירות התחברות לארגונים.\nעלה כשדנה תיארה את ההרשאות.")
        expect(NewNames.entry(explained, call: "שיחה", date: "2026-11-16").contains("\nתקציר: שירות התחברות לארגונים. עלה כשדנה תיארה את ההרשאות.\n")
               && NewNames.entry(CallTerm(term: "Okta", note: "התחברות", detail: " "), call: "שיחה", date: "2026-11-16").contains("\nתקציר: התחברות\n"),
               "the explanation is the summary, the note when there is none", &p)
        let kinds = ["company", "person", "product", "term", "fund", "org", "event", "place", nil].map(NewNames.kindLabel)
        expect(kinds == ["חברה", "אדם", "מוצר", "מושג", "קרן", "ארגון", "אירוע", "מושג", "מושג"], "kinds \(kinds)", &p)

        let folds: [(String, [String])] = [
            ("Check-Point", ["check", "point"]), ("צ'ק פוינט", ["צק", "פוינט"]), ("שָׁלוֹם", ["שלום"]), ("ג\u{05F3}ם", ["גם"]),
            ("\u{201C}Okta\u{201D}", ["okta"]), ("U.S./EU", ["u", "s", "eu"]), ("foo_bar", ["foo", "bar"]),
            ("בית\u{05BE}ספר", ["בית", "ספר"]), ("A\u{2013}B\u{2015}C", ["a", "b", "c"]), ("Straße x`y", ["strasse", "xy"]),
        ]
        for (text, words) in folds {
            expect(NewNames.words(text) == words, "words of \(text): \(NewNames.words(text))", &p)
        }
        expect(NewNames.key("Team 8") == "team8" && NewNames.key("TEAM8") == "team8", "joined words", &p)
        let sample = "\u{FEFF}## Check-Point\nסוג: חברה\nכתיבים: צ'ק פוינט, CHKP\n\n# קרנות\n## Team 8 \n  כתיבים : Gem*, ג\u{05F3}ם*\n"
            + "### Okta\n##Glow\n- כתיבים: Wiz\n# מונחים\nכתיבים: Orphan\n"
        let names = NewNames.names(sample)
        expect(names == ["checkpoint", "צקפוינט", "chkp", "team8", "gem", "גם"], "names \(names.sorted())", &p)
        expect(["Check Point", "צק פוינט", "Team8", "GEM", "ג'ם"].allSatisfy { names.contains(CallTerm(term: $0, note: "").key) }
               && !names.contains(okta.key), "already in a knowledge file", &p)

        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("callcoach-names-\(getpid())")
        defer { try? FileManager.default.removeItem(at: dir) }
        let folder = dir.appendingPathComponent("calls/2026-11-16_1030_sales")
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let terms = """
            [{"term": "Okta", "note": "x", "kind": "product", "lang": "he", "known": false, "detail": "שירות התחברות."},
             {"term": "Team8", "note": "y", "kind": "fund", "lang": "he", "known": true},
             {"term": "okta ", "note": "z", "kind": "product", "lang": "he", "known": false},
             {"term": "SOC 2", "note": "w", "kind": "term", "lang": "en", "known": false}]
            """
        try? terms.write(to: folder.appendingPathComponent("terms.json"), atomically: true, encoding: .utf8)
        expect(NewNames.load(folder).map(\.term) == ["Okta", "SOC 2"], "new names \(NewNames.load(folder).map(\.term))", &p)
        expect(NewNames.load(folder).map(\.detail) == ["שירות התחברות.", nil], "a name's explanation", &p)
        expect(NewNames.load(dir).isEmpty, "a call without terms.json", &p)
        guard let call = CallLibrary.load(dir.appendingPathComponent("calls")).first else {
            p.append("the sample call was not listed")
            return p
        }
        let data = dir.appendingPathComponent("data")
        let file = NewNames.file(data)
        let first = try? NewNames.add(okta, from: call, data: data)
        let again = try? NewNames.add(CallTerm(term: "OKTA ", note: "אחר"), from: call, data: data)
        let soc = CallTerm(term: "SOC 2", note: "תקן ביקורת אבטחה", kind: "term")
        let second = try? NewNames.add(soc, from: call, data: data)
        let text = (try? String(contentsOf: file, encoding: .utf8)) ?? ""
        expect(first == true && again == false && second == true, "adds \(String(describing: first)) \(String(describing: again))", &p)
        expect(text == NewNames.header + "\n" + NewNames.entry(okta, call: "מכירה", date: "2026-11-16") + "\n"
               + NewNames.entry(soc, call: "מכירה", date: "2026-11-16"), "file \(text)", &p)
        expect(NewNames.header.components(separatedBy: "\n").allSatisfy { !$0.hasPrefix("#") }, "the header is not read", &p)
        expect(NewNames.known(data) == ["okta", "soc2"], "names in the file \(NewNames.known(data))", &p)
        try? "## Wiz\nסוג: חברה".write(to: file, atomically: true, encoding: .utf8)
        _ = try? NewNames.add(soc, from: call, data: data)
        let appended = (try? String(contentsOf: file, encoding: .utf8)) ?? ""
        expect(appended.hasPrefix("## Wiz\nסוג: חברה\n\n## SOC 2\n"), "appended to a file by hand \(appended)", &p)

        let mine = dir.appendingPathComponent("mine")
        let folder2 = mine.appendingPathComponent("knowledge")
        try? FileManager.default.createDirectory(at: folder2, withIntermediateDirectories: true)
        try? "## Okta Inc\nסוג: חברה\nכתיבים: Okta\nתקציר: התחברות\n".write(to: folder2.appendingPathComponent("cyber.md"),
                                                                         atomically: true, encoding: .utf8)
        try? "## Wiz\nתקציר: ענן\n".write(to: folder2.appendingPathComponent("cyber.backup.md"), atomically: true, encoding: .utf8)
        try? "## Vanta\nתקציר: תאימות\n".write(to: folder2.appendingPathComponent("notes.txt"), atomically: true, encoding: .utf8)
        expect(NewNames.known(mine) == ["oktainc", "okta"], "names in the knowledge files \(NewNames.known(mine))", &p)
        let spelled = try? NewNames.add(okta, from: call, data: mine)
        expect(spelled == false && !FileManager.default.fileExists(atPath: NewNames.file(mine).path),
               "a spelling in another file is already known", &p)
        expect((try? NewNames.add(CallTerm(term: "Wiz", note: "ענן"), from: call, data: mine)) == true,
               "a backup copy is not read", &p)
        let blocked = dir.appendingPathComponent("blocked")
        FileManager.default.createFile(atPath: blocked.path, contents: Data())
        expect((try? NewNames.add(okta, from: call, data: blocked)) == nil, "a write that fails throws", &p)
        return p
    }
}
