import AppKit
import SwiftUI

enum SummaryText {
    enum Kind: Equatable { case title, heading, bullet, plain }

    struct Line: Equatable, Identifiable {
        let id: Int
        let kind: Kind
        let text: String
    }

    static func lines(_ text: String) -> [Line] {
        var out: [Line] = []
        for raw in text.split(separator: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty else { continue }
            let hashes = line.prefix { $0 == "#" }.count
            let kind: Kind
            let body: Substring
            if hashes > 0 && line.dropFirst(hashes).hasPrefix(" ") {
                kind = hashes == 1 ? .title : .heading
                body = line.dropFirst(hashes + 1)
            } else if line.count > 4 && line.hasPrefix("**") && line.hasSuffix("**") {
                kind = .heading
                body = line.dropFirst(2).dropLast(2)
            } else if line.hasPrefix("- ") || line.hasPrefix("* ") {
                kind = .bullet
                body = line.dropFirst(2)
            } else {
                kind = .plain
                body = Substring(line)
            }
            out.append(Line(id: out.count, kind: kind, text: body.replacingOccurrences(of: "**", with: "")))
        }
        return out
    }
}

struct SummaryLineView: View {
    let line: SummaryText.Line

    var body: some View {
        switch line.kind {
        case .title: DirectedText(line.text, size: 18, weight: .bold)
        case .heading: DirectedText(line.text, size: 14, weight: .semibold).padding(.top, 6)
        case .bullet:
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text("•")
                DirectedText(line.text)
            }
            .environment(\.layoutDirection, TextDirection.isRTL(line.text) ? .rightToLeft : .leftToRight)
        case .plain: DirectedText(line.text)
        }
    }
}

struct SummaryView: View {
    let summary: SummaryReady
    let finalized: Finalized?
    let notes: [String]
    let found: Bool
    let lines: [SummaryText.Line]
    /// A window of its own has padding and a minimum size; inside the main window it has neither.
    let padded: Bool
    /// A call summary has a follow-up email; lecture notes have none.
    let followUp: FollowUpSource?

    init(summary: SummaryReady, finalized: Finalized?, notes: [String], padded: Bool = true, followUp: FollowUpSource? = nil) {
        self.summary = summary
        self.finalized = finalized
        self.notes = notes
        self.padded = padded
        self.followUp = followUp
        let text = try? String(contentsOfFile: summary.path, encoding: .utf8)
        found = text != nil
        lines = SummaryText.lines(text ?? "")
    }

    static let followUpId = "followup"

    var problems: [String] {
        var problems = notes
        if !summary.message.isEmpty { problems.append(summary.message) }
        if let finalized, !finalized.ok {
            problems.append(L("ההקלטה לא נשמרה במלואה: \(finalized.reason)",
                              "The recording was not saved in full: \(finalized.reason)"))
        }
        if !found {
            problems.append(L("קובץ הסיכום לא נמצא: \(summary.path)", "The summary file was not found: \(summary.path)"))
        }
        return problems
    }

    var body: some View {
        ScrollViewReader { proxy in
            VStack(alignment: .leading, spacing: 10) {
                ForEach(problems, id: \.self) { DirectedText($0, size: 12).foregroundStyle(.orange) }
                ScrollView {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(lines) { SummaryLineView(line: $0) }
                        if let followUp { FollowUpBox(call: followUp.call, state: followUp.state).id(Self.followUpId) }
                    }
                    .textSelection(.enabled)
                    .padding(.vertical, 4)
                }
                HStack {
                    Button(L("פתח את הסיכום", "Open summary")) { NSWorkspace.shared.open(URL(fileURLWithPath: summary.path)) }
                        .disabled(lines.isEmpty)
                    Spacer()
                    if let followUp {
                        FollowUpButton(call: followUp.call, state: followUp.state) {
                            withAnimation(.easeOut(duration: 0.3)) { proxy.scrollTo(Self.followUpId, anchor: .bottom) }
                        }
                    }
                }
            }
        }
        .padding(padded ? 16 : 0)
        .frame(minWidth: padded ? 480 : nil, minHeight: padded ? 420 : nil)
    }
}

extension SelfCheck {
    static func summaryChecks(_ root: URL) -> [String] {
        var p: [String] = []
        let lines = SummaryText.lines("# סיכום השיחה\n\n## מה למדתי\n- הם מאשרים גישה ידנית\n* \"we export from Excel\"\nשורה רגילה\n### פרטים\n#תגית")
        expect(lines.map(\.kind) == [.title, .heading, .bullet, .bullet, .plain, .heading, .plain], "kinds \(lines.map(\.kind))", &p)
        expect(lines.map(\.text) == ["סיכום השיחה", "מה למדתי", "הם מאשרים גישה ידנית", "\"we export from Excel\"",
                                     "שורה רגילה", "פרטים", "#תגית"], "texts \(lines.map(\.text))", &p)
        expect(SummaryText.lines("\n\n").isEmpty, "an empty summary has no lines", &p)
        let bold = SummaryText.lines("**עובדות ודוגמאות**\n- **SOC 2**: תקן ביקורת")
        expect(bold.map(\.kind) == [.heading, .bullet] && bold.map(\.text) == ["עובדות ודוגמאות", "SOC 2: תקן ביקורת"],
               "bold markers \(bold.map(\.text))", &p)
        return p
    }
}
