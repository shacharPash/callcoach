import AppKit
import CoreText
import PDFKit
import SwiftUI

/// The text of a file added to "הכן תדריך". The app reads the files itself, so the brief command gets only text.
enum BriefFiles {
    static let limit = 100_000
    static let plain: Set<String> = ["md", "txt"]
    static let converted: Set<String> = ["doc", "docx", "rtf", "rtfd", "odt", "html"]

    enum Content: Equatable {
        case text(String), unsupported, unreadable
    }

    static func supported(_ url: URL) -> Bool {
        let ext = url.pathExtension.lowercased()
        return plain.contains(ext) || ext == "pdf" || converted.contains(ext)
    }

    /// Markdown and text as UTF-8, PDF with PDFKit, and the rest with textutil; each cut at 100,000 characters.
    /// It waits for textutil, so it runs off the main thread.
    static func read(_ url: URL) -> Content {
        let ext = url.pathExtension.lowercased()
        let text: String?
        if plain.contains(ext) {
            text = try? String(contentsOf: url, encoding: .utf8)
        } else if ext == "pdf" {
            text = PDFDocument(url: url)?.string
        } else if converted.contains(ext) {
            text = textutil(url)
        } else {
            return .unsupported
        }
        guard let text, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return .unreadable }
        return .text(String(text.prefix(limit)))
    }

    private static func textutil(_ url: URL) -> String? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/textutil")
        process.arguments = ["-convert", "txt", "-stdout", url.path]
        let out = Pipe()
        process.standardOutput = out
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return nil }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else { return nil }
        return String(data: data, encoding: .utf8)
    }
}

/// A text editor drawn like a text field: a field background, a thin border, and a hint while it is empty.
struct EditorField: View {
    @Binding var text: String
    let placeholder: String
    var size: CGFloat = 13
    let height: CGFloat
    @Environment(\.layoutDirection) private var direction

    var body: some View {
        DirectedTextView(text: $text, size: size, page: direction)
            .padding(.vertical, 4)
            .padding(.horizontal, 2)
            .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Color(nsColor: .separatorColor)))
            .overlay(alignment: .topLeading) {
                if text.isEmpty {
                    Text(placeholder).font(.system(size: size)).foregroundStyle(.tertiary)
                        .padding(.vertical, 4)
                        .padding(.horizontal, 7)
                        .allowsHitTesting(false)
                }
            }
            .frame(height: height)
    }
}

/// A multi-line text view in which every paragraph is laid out in its own direction and flush to its own side, so a
/// Hebrew line that starts with an English name stays right to left. An empty paragraph takes the page's direction.
struct DirectedTextView: NSViewRepresentable {
    @Binding var text: String
    let size: CGFloat
    let page: LayoutDirection

    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: DirectedTextView

        init(_ parent: DirectedTextView) {
            self.parent = parent
        }

        func textDidChange(_ notification: Notification) {
            guard let view = notification.object as? NSTextView else { return }
            if !view.hasMarkedText() { DirectedTextView.direct(view, size: parent.size, page: parent.page) }
            parent.text = view.string
        }

        func textViewDidChangeSelection(_ notification: Notification) {
            guard let view = notification.object as? NSTextView, !view.hasMarkedText() else { return }
            DirectedTextView.type(view, size: parent.size, page: parent.page)
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSTextView.scrollableTextView()
        scroll.drawsBackground = false
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        guard let view = scroll.documentView as? NSTextView else { return scroll }
        view.drawsBackground = false
        view.isRichText = false
        view.allowsUndo = true
        view.isAutomaticQuoteSubstitutionEnabled = false
        view.isAutomaticDashSubstitutionEnabled = false
        view.textContainerInset = NSSize(width: 0, height: 0)
        view.string = text
        Self.direct(view, size: size, page: page)
        view.delegate = context.coordinator
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        context.coordinator.parent = self
        guard let view = scroll.documentView as? NSTextView, view.string != text else { return }
        view.string = text
        Self.direct(view, size: size, page: page)
    }

    static func style(_ paragraph: String, page: LayoutDirection) -> NSParagraphStyle {
        let style = NSMutableParagraphStyle()
        let rtl = paragraph.trimmingCharacters(in: .whitespaces).isEmpty ? page == .rightToLeft : TextDirection.isRTL(paragraph)
        style.baseWritingDirection = rtl ? .rightToLeft : .leftToRight
        style.alignment = .natural
        return style
    }

    /// Each paragraph gets the direction of its own text, then the typing follows the paragraph at the cursor.
    static func direct(_ view: NSTextView, size: CGFloat, page: LayoutDirection) {
        guard let storage = view.textStorage else { return }
        let string = storage.string as NSString
        let font = NSFont.systemFont(ofSize: size)
        storage.beginEditing()
        storage.addAttributes([.font: font, .foregroundColor: NSColor.labelColor], range: NSRange(location: 0, length: string.length))
        string.enumerateSubstrings(in: NSRange(location: 0, length: string.length), options: .byParagraphs) { paragraph, _, enclosing, _ in
            storage.addAttribute(.paragraphStyle, value: style(paragraph ?? "", page: page), range: enclosing)
        }
        storage.endEditing()
        type(view, size: size, page: page)
    }

    /// What is typed next: the direction of the paragraph at the cursor, or the page's on an empty line.
    static func type(_ view: NSTextView, size: CGFloat, page: LayoutDirection) {
        let string = view.string as NSString
        let at = min(view.selectedRange().location, string.length)
        let range = string.paragraphRange(for: NSRange(location: at, length: 0))
        let paragraph = string.substring(with: range).trimmingCharacters(in: .newlines)
        view.typingAttributes = [.font: NSFont.systemFont(ofSize: size), .foregroundColor: NSColor.labelColor,
                                 .paragraphStyle: style(paragraph, page: page)]
    }
}

/// "הכן תדריך": what the call is about, when it is, files and links. "הכן תדריך" closes the sheet, and the brief is
/// written in the background while the "השיחות הבאות" card on "בית" says so.
struct PrepSheet: View {
    /// A file in the sheet; its content is nil while it is read.
    struct PrepFile: Identifiable, Equatable {
        let url: URL
        var content: BriefFiles.Content?

        var id: String { url.path }
        var name: String { url.lastPathComponent }
    }

    @ObservedObject var state: AppState
    @Environment(\.dismiss) private var dismiss
    @ViewState private var text = ""
    @ViewState private var timed = false
    @ViewState private var when = PrepSheet.nextHour()
    @ViewState private var files: [PrepFile] = []
    @ViewState private var links = ""
    @ViewState private var targeted = false
    /// The call a follow-up follows, from "הכן תדריך לשיחת המשך…".
    @ViewState private var after: String?

    static func nextHour(_ now: Date = Date(), calendar: Calendar = .current) -> Date {
        (calendar.dateInterval(of: .hour, for: now)?.start ?? now).addingTimeInterval(3600)
    }

    /// The links one per line, and only the files that were read.
    static func request(text: String, when: Date?, files: [PrepFile], links: String) -> BriefRequest {
        BriefRequest(when: when, text: text.trimmingCharacters(in: .whitespacesAndNewlines),
                     files: files.compactMap { file in
                         guard case .text(let content)? = file.content else { return nil }
                         return BriefRequest.File(name: file.name, text: content)
                     },
                     links: links.components(separatedBy: .newlines).map { $0.trimmingCharacters(in: .whitespaces) }
                         .filter { !$0.isEmpty })
    }

    /// Something to work from: text, a file or a link.
    static func ready(_ request: BriefRequest) -> Bool {
        !request.text.isEmpty || !request.files.isEmpty || !request.links.isEmpty
    }

    private var request: BriefRequest {
        var request = Self.request(text: text, when: timed ? when : nil, files: files, links: links)
        request.after = after
        return request
    }

    private var busy: Bool {
        if case .making? = state.briefJob { return true }
        return false
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(after == nil ? L("הכן תדריך", "Prepare a brief") : L("הכן תדריך לשיחת המשך", "Prepare a follow-up brief")).font(.headline)
            Text(L("על מה השיחה?", "What is the call about?")).font(.system(size: 13, weight: .semibold))
            EditorField(text: $text, placeholder: L("למשל: היכרות עם דנה מ-Acme", "For example: meeting Dana from Acme"),
                        height: 96)
            Text(L("עם מי, מה המטרה ומה כדאי לדעת לפני. התדריך נכתב ברקע, ואפשר לסגור את החלון.",
                   "Who, what the goal is and what to know before. The brief is written in the background."))
                .font(.caption).foregroundStyle(.secondary)
            HStack(spacing: 10) {
                Toggle(L("מתי", "When"), isOn: $timed)
                if timed {
                    DatePicker("", selection: $when, displayedComponents: [.date, .hourAndMinute])
                        .datePickerStyle(.stepperField).labelsHidden().fixedSize()
                }
                Spacer()
            }
            dropArea
            ForEach(files) { row($0) }
            Text(L("קישורים", "Links")).font(.system(size: 13, weight: .semibold))
            EditorField(text: $links, placeholder: "https://", size: 12, height: 56)
            Text(L("קישור בכל שורה. גם הרשת נקראת בזמן ההכנה.", "One link per line. The web is searched as well."))
                .font(.caption).foregroundStyle(.secondary)
            if busy {
                DirectedText(L("כבר מכין תדריך. את הבא אפשר להכין כשהוא יסתיים.",
                               "A brief is being prepared. The next one can start when it is done."), size: 12)
                    .foregroundStyle(.orange)
            }
            HStack {
                Spacer()
                Button(L("ביטול", "Cancel")) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(L("הכן תדריך", "Prepare the brief")) {
                    state.makeBrief(request)
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(!Self.ready(request) || files.contains { $0.content == nil } || busy)
            }
        }
        .padding(20)
        .frame(width: 520)
        .background(CaptureExcluded())
        .onAppear(perform: takeDraft)
    }

    /// "הכן תדריך לשיחת המשך…" left its text, files and call here.
    private func takeDraft() {
        guard let draft = state.prepDraft else { return }
        state.prepDraft = nil
        text = draft.text
        after = draft.after
        add(draft.files)
    }

    private var dropArea: some View {
        VStack(spacing: 6) {
            Image(systemName: "doc.badge.plus").font(.system(size: 20)).foregroundStyle(.secondary)
            Text(L("גרור לכאן קבצים", "Drag files here"))
            Button(L("בחר קבצים…", "Choose files…"), action: choose).controlSize(.small)
            Text(L("קובצי טקסט, Markdown, PDF, Word, RTF, ODT ו-HTML", "Text, Markdown, PDF, Word, RTF, ODT and HTML files"))
                .font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, minHeight: 96)
        .background(RoundedRectangle(cornerRadius: 8)
            .strokeBorder(targeted ? Color.accentColor : Color.secondary.opacity(0.5), style: StrokeStyle(lineWidth: 1.5, dash: [5])))
        .dropDestination(for: URL.self) { urls, _ in
            add(urls)
            return !urls.isEmpty
        } isTargeted: { targeted = $0 }
    }

    private func row(_ file: PrepFile) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "doc.text").foregroundStyle(.secondary)
            Text(TextDirection.marked(file.name)).lineLimit(1).truncationMode(.middle)
            Spacer()
            switch file.content {
            case nil: ProgressView().controlSize(.small)
            case .unsupported?: Text(L("לא נתמך", "Not supported")).foregroundStyle(.orange)
            case .unreadable?: Text(L("לא הצלחתי לקרוא", "Could not read it")).foregroundStyle(.orange)
            case .text?: Image(systemName: "checkmark").foregroundStyle(.green)
            }
            Button { files.removeAll { $0.id == file.id } } label: { Image(systemName: "xmark.circle.fill") }
                .buttonStyle(.borderless)
                .foregroundStyle(.secondary)
                .accessibilityLabel(L("הסר", "Remove"))
        }
        .font(.system(size: 12))
    }

    private func choose() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        if panel.runModal() == .OK { add(panel.urls) }
    }

    /// A file that is not supported stays in the list as "לא נתמך" and is left out of the request.
    private func add(_ urls: [URL]) {
        for url in urls where url.isFileURL && !files.contains(where: { $0.url == url }) {
            let supported = BriefFiles.supported(url)
            files.append(PrepFile(url: url, content: supported ? nil : .unsupported))
            guard supported else { continue }
            DispatchQueue.global(qos: .userInitiated).async {
                let content = BriefFiles.read(url)
                DispatchQueue.main.async {
                    if let i = files.firstIndex(where: { $0.url == url }) { files[i].content = content }
                }
            }
        }
    }
}

extension SelfCheck {
    static func prepChecks(_ root: URL) -> [String] {
        var p: [String] = []
        let editor = NSTextView()
        editor.string = "Acme היא חברת תוכנה של כ-300 עובדים\nHello world.\n\n## רקע"
        DirectedTextView.direct(editor, size: 13, page: .rightToLeft)
        func direction(_ at: Int) -> NSWritingDirection? {
            (editor.textStorage?.attribute(.paragraphStyle, at: at, effectiveRange: nil) as? NSParagraphStyle)?.baseWritingDirection
        }
        let hello = (editor.string as NSString).range(of: "Hello").location
        let heading = (editor.string as NSString).range(of: "## רקע").location
        expect(direction(0) == .rightToLeft && direction(hello) == .leftToRight && direction(heading) == .rightToLeft,
               "each paragraph of the editor in its own direction", &p)
        editor.setSelectedRange(NSRange(location: hello + 13, length: 0))
        DirectedTextView.type(editor, size: 13, page: .rightToLeft)
        expect((editor.typingAttributes[.paragraphStyle] as? NSParagraphStyle)?.baseWritingDirection == .rightToLeft,
               "an empty line is typed in the page's direction", &p)
        let fm = FileManager.default
        let dir = fm.temporaryDirectory.appendingPathComponent("callcoach-prep-\(getpid())")
        defer { try? fm.removeItem(at: dir) }
        try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
        func file(_ name: String, _ text: String) -> URL {
            let url = dir.appendingPathComponent(name)
            try? text.write(to: url, atomically: true, encoding: .utf8)
            return url
        }
        let md = file("acme.md", "# Acme\nדנה, מנהלת IT")
        let long = file("long.TXT", String(repeating: "א", count: BriefFiles.limit + 5))
        let html = file("acme.html", "<html><body><p>Hello <b>Acme</b></p></body></html>")
        let other = file("acme.key", "x")
        let empty = file("empty.txt", " \n")
        let bad = dir.appendingPathComponent("bad.txt")
        try? Data([0xFF, 0xFE, 0x41, 0xC3]).write(to: bad)
        let pdf = dir.appendingPathComponent("acme.pdf")
        var box = CGRect(x: 0, y: 0, width: 300, height: 200)
        if let context = CGContext(pdf as CFURL, mediaBox: &box, nil) {
            context.beginPDFPage(nil)
            let line = CTLineCreateWithAttributedString(NSAttributedString(string: "Acme brief",
                                                                            attributes: [.font: NSFont.systemFont(ofSize: 18)]))
            context.textPosition = CGPoint(x: 20, y: 100)
            CTLineDraw(line, context)
            context.endPDFPage()
            context.closePDF()
        }
        expect(BriefFiles.read(md) == .text("# Acme\nדנה, מנהלת IT"), "a Markdown file", &p)
        if case .text(let text) = BriefFiles.read(long) {
            expect(text.count == BriefFiles.limit, "a file is cut at 100,000 characters (\(text.count))", &p)
        } else {
            p.append("a text file with an upper case extension does not read")
        }
        if case .text(let text) = BriefFiles.read(html) {
            expect(text.contains("Hello Acme") && !text.contains("<"), "html through textutil \(text)", &p)
        } else {
            p.append("html through textutil does not read")
        }
        if case .text(let text) = BriefFiles.read(pdf) {
            expect(text.contains("Acme brief"), "a PDF through PDFKit \(text)", &p)
        } else {
            p.append("a PDF does not read")
        }
        expect(BriefFiles.read(other) == .unsupported && !BriefFiles.supported(other) && BriefFiles.supported(pdf),
               "another file is not supported", &p)
        expect(BriefFiles.read(empty) == .unreadable && BriefFiles.read(bad) == .unreadable
               && BriefFiles.read(dir.appendingPathComponent("gone.docx")) == .unreadable, "files that do not read", &p)
        expect(["doc", "docx", "rtf", "rtfd", "odt", "html", "md", "txt", "pdf"].allSatisfy {
            BriefFiles.supported(URL(fileURLWithPath: "/x/a.\($0)"))
        }, "every supported kind", &p)

        let files = [PrepSheet.PrepFile(url: md, content: .text("Acme")), PrepSheet.PrepFile(url: other, content: .unsupported),
                     PrepSheet.PrepFile(url: empty, content: .unreadable)]
        let request = PrepSheet.request(text: "  שיחה עם דנה \n", when: nil, files: files,
                                        links: " https://acme.example/about \n\n  https://acme.example/team\n")
        expect(request == BriefRequest(text: "שיחה עם דנה", files: [BriefRequest.File(name: "acme.md", text: "Acme")],
                                       links: ["https://acme.example/about", "https://acme.example/team"]),
               "the request from the sheet \(request)", &p)
        expect(PrepSheet.ready(request) && !PrepSheet.ready(PrepSheet.request(text: " ", when: nil, files: [files[1]], links: "\n")),
               "the sheet needs something to work from", &p)
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "Asia/Jerusalem")!
        let now = c.date(from: DateComponents(year: 2026, month: 11, day: 16, hour: 13, minute: 20))!
        expect(PrepSheet.nextHour(now, calendar: c) == c.date(from: DateComponents(year: 2026, month: 11, day: 16, hour: 14)),
               "the time starts at the next hour", &p)
        return p
    }
}
