import SwiftUI

/// Names and terms I already know, so the panel does not show them: one per line after "- ", in the data folder's
/// לא-להקפיץ.md. The engine reads the file when a call starts; the panel hides a row added during the call at once.
enum IgnoreList {
    static let fileName = "לא-להקפיץ.md"

    static var header: String {
        "# לא להקפיץ\n\nשמות ומושגים שאני כבר מכיר. CallCoach לא מקפיץ אותם בפאנל. שורה שנמחקת מחזירה את השם מהשיחה הבאה.\n\n"
    }

    static func file(_ data: URL) -> URL {
        data.appendingPathComponent(fileName)
    }

    /// The names in the file, in its order; none when it is missing or cannot be read.
    static func load(_ data: URL) -> [String] {
        guard let text = try? String(contentsOf: file(data), encoding: .utf8) else { return [] }
        return names(text)
    }

    static func names(_ text: String) -> [String] {
        text.split(separator: "\n", omittingEmptySubsequences: false).compactMap { line in
            guard line.hasPrefix("- ") else { return nil }
            let name = line.dropFirst(2).trimmingCharacters(in: .whitespaces)
            return name.isEmpty ? nil : name
        }
    }

    /// Case, spaces and punctuation do not count, so "Team 8" and "team8" are one name.
    static func same(_ a: String, _ b: String) -> Bool {
        NewNames.key(a) == NewNames.key(b)
    }

    /// Adds the name at the end; false when it is already there. The file starts with the header when it is new.
    @discardableResult
    static func add(_ name: String, data: URL) throws -> Bool {
        let name = name.replacingOccurrences(of: "\n", with: " ").trimmingCharacters(in: .whitespaces)
        guard !NewNames.key(name).isEmpty else { return false }
        let target = file(data)
        var text = header
        if FileManager.default.fileExists(atPath: target.path) {
            text = try String(contentsOf: target, encoding: .utf8)
        } else {
            try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
        }
        if names(text).contains(where: { same($0, name) }) { return false }
        if !text.isEmpty && !text.hasSuffix("\n") { text += "\n" }
        text += "- \(name)\n"
        try text.write(to: target, atomically: true, encoding: .utf8)
        return true
    }

    /// Takes the name's lines out, and keeps every other line as it was.
    static func remove(_ name: String, data: URL) throws {
        let target = file(data)
        guard let text = try? String(contentsOf: target, encoding: .utf8) else { return }
        let kept = text.split(separator: "\n", omittingEmptySubsequences: false).filter { line in
            !(line.hasPrefix("- ") && same(line.dropFirst(2).trimmingCharacters(in: .whitespaces), name))
        }
        try kept.joined(separator: "\n").write(to: target, atomically: true, encoding: .utf8)
    }
}

/// Settings' "לא להקפיץ": the names on the list, each with a way to bring it back, and a field to add one.
struct IgnoreSettings: View {
    var data = Paths.data
    @ViewState private var names: [String] = []
    @ViewState private var typed = ""
    @ViewState private var failure: String?

    var body: some View {
        Form {
            Section {
                HStack {
                    TextField(L("שם או מושג", "A name or a term"), text: $typed,
                              prompt: Text(L("שם או מושג להוסיף", "A name or a term to add")))
                        .textFieldStyle(.roundedBorder)
                        .labelsHidden()
                        .onSubmit(add)
                    Button(L("הוסף", "Add"), action: add)
                        .disabled(NewNames.key(typed).isEmpty)
                }
                if let failure { DirectedText(failure, size: 12).foregroundStyle(.orange) }
            } footer: {
                Text(L("שמות ומושגים שאתה כבר מכיר לא יוקפצו בפאנל, החל מהשיחה הבאה. בשיחה עצמה, \"אני מכיר, לא להקפיץ\" בשורה פתוחה מסתיר אותה מיד.",
                       "Names and terms you already know are not shown in the panel, from the next call. During a call, \"I know it, don't show\" on an open row hides it at once."))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section(L("ברשימה", "On the list")) {
                if names.isEmpty {
                    Text(L("אין עדיין", "None yet")).foregroundStyle(.secondary)
                } else {
                    ForEach(names, id: \.self) { name in
                        HStack {
                            Text(TextDirection.marked(name))
                                .frame(maxWidth: .infinity, alignment: .leading)
                            Button(L("החזר", "Bring back")) { remove(name) }.controlSize(.small)
                        }
                    }
                }
            }
        }
        .formStyle(.grouped)
        .onAppear { names = IgnoreList.load(data) }
    }

    private func add() {
        let name = typed
        guard !NewNames.key(name).isEmpty else { return }
        save { try IgnoreList.add(name, data: data) }
        if failure == nil { typed = "" }
    }

    private func remove(_ name: String) {
        save { try IgnoreList.remove(name, data: data) }
    }

    private func save(_ write: () throws -> Void) {
        do {
            try write()
            failure = nil
        } catch {
            failure = L("הרשימה לא נשמרה: \(error.localizedDescription)", "The list was not saved: \(error.localizedDescription)")
        }
        names = IgnoreList.load(data)
    }
}

extension SelfCheck {
    static func ignoreChecks(_ root: URL) -> [String] {
        var p: [String] = []
        let data = FileManager.default.temporaryDirectory.appendingPathComponent("callcoach-ignore-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: data) }
        expect(IgnoreList.load(data).isEmpty, "no file, no names", &p)
        let first = try? IgnoreList.add("Okta", data: data)
        let again = try? IgnoreList.add("  okta ", data: data)
        let team = try? IgnoreList.add("Team 8", data: data)
        let spaced = try? IgnoreList.add("team8", data: data)
        let blank = try? IgnoreList.add(" , ", data: data)
        expect(first == true && again == false && team == true && spaced == false && blank == false,
               "added once by any spelling \([first, again, team, spaced, blank])", &p)
        let text = (try? String(contentsOf: IgnoreList.file(data), encoding: .utf8)) ?? ""
        expect(text.hasPrefix("# לא להקפיץ\n") && text.hasSuffix("- Okta\n- Team 8\n") && IgnoreList.load(data) == ["Okta", "Team 8"],
               "the file \(text.suffix(40))", &p)
        try? (text + "הערה שלי\n- סקים\n").write(to: IgnoreList.file(data), atomically: true, encoding: .utf8)
        try? IgnoreList.remove("OKTA", data: data)
        let left = (try? String(contentsOf: IgnoreList.file(data), encoding: .utf8)) ?? ""
        expect(IgnoreList.load(data) == ["Team 8", "סקים"] && left.contains("הערה שלי\n") && left.hasPrefix("# לא להקפיץ\n"),
               "a name taken out keeps every other line \(left.suffix(40))", &p)
        expect(IgnoreList.fileName == "לא-להקפיץ.md", "the engine reads לא-להקפיץ.md", &p)
        let engine = (try? String(contentsOf: root.appendingPathComponent("engine/callcoach/ignore.py"), encoding: .utf8)) ?? ""
        expect(engine.contains("FILE = \"\(IgnoreList.fileName)\""), "the engine's file name is the app's", &p)
        return p
    }
}
