import AppKit
import SwiftUI

/// A text field whose text sits on its own side: Hebrew on the right, English on the left, and an empty field on the
/// page's side. A text field keeps its text on the left in a right-to-left layout, so it is laid out left to right and
/// aligned to the side the text needs.
struct DirectedField: View {
    let placeholder: String
    @Binding var text: String
    var size: CGFloat = 13
    @Environment(\.layoutDirection) private var direction

    var body: some View {
        TextField(placeholder, text: $text)
            .textFieldStyle(.roundedBorder)
            .font(.system(size: size))
            .multilineTextAlignment(Self.rightSide(text, page: direction) ? .trailing : .leading)
            .environment(\.layoutDirection, .leftToRight)
    }

    static func rightSide(_ text: String, page: LayoutDirection) -> Bool {
        text.trimmingCharacters(in: .whitespaces).isEmpty ? page == .rightToLeft : TextDirection.isRTL(text)
    }
}

/// The briefs tab's list: "תדריך חדש" and "הכן תדריך…" on top, then the three groups.
struct BriefsList: View {
    let briefs: [Brief]
    let calls: [String: Int]
    @Binding var selected: String?
    var newBrief: () -> Void = {}
    var prepare: () -> Void = {}

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Button(action: newBrief) { Label(L("תדריך חדש", "New brief"), systemImage: "square.and.pencil") }
                Button(action: prepare) { Label(L("הכן תדריך…", "Prepare a brief…"), systemImage: "sparkles") }
            }
            .controlSize(.small)
            .padding(.horizontal, 10)
            .padding(.bottom, 6)
            if briefs.isEmpty {
                VStack(spacing: 6) {
                    Text(L("אין עדיין תדריכים", "No briefs yet")).foregroundStyle(.secondary)
                    Text(L("תדריך חדש כותבים לבד, ו\"הכן תדריך…\" מכין אחד עם AI מטקסט, קבצים וקישורים.",
                           "A new brief, or one the AI prepares from text, files and links."))
                        .font(.caption).foregroundStyle(.tertiary).multilineTextAlignment(.center)
                }
                .padding(20)
                .frame(maxHeight: .infinity, alignment: .top)
            } else {
                TimelineView(.everyMinute) { context in
                    let groups = BriefGroup.groups(briefs, calls: calls, now: context.date)
                    List(selection: $selected) {
                        ForEach(groups.indices, id: \.self) { i in
                            Section(groups[i].group.title) {
                                ForEach(groups[i].briefs) { brief in
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(TextDirection.marked(brief.title)).font(.system(size: 13, weight: .medium)).lineLimit(2)
                                        Text(BriefGroup.subtitle(brief, calls: calls[brief.file] ?? 0, now: context.date))
                                            .font(.system(size: 11)).foregroundStyle(.secondary).monospacedDigit()
                                    }
                                    .padding(.vertical, 2)
                                    .tag(brief.file)
                                }
                            }
                        }
                    }
                    .listStyle(.sidebar)
                }
            }
        }
    }
}

/// What the brief page does outside itself.
struct BriefActions {
    var start: (Brief) -> Void = { _ in }
    var followUp: (CallEntry) -> Void = { _ in }
    /// Opens a call in the calls tab.
    var openCall: (String) -> Void = { _ in }
    var saved: (String) -> Void = { _ in }
    var cancelled: () -> Void = {}
    /// Reads the calls again, as the calls tab does after a call page changed one.
    var reload: () -> Void = {}
}

/// A brief: tabs for the brief and for each call that used it. The brief tab shows what the brief holds, and "ערוך"
/// turns it into a form. The call tabs show the call page as the calls tab does.
struct BriefPage: View {
    let brief: Brief
    /// The calls that used it, newest first.
    let calls: [CallEntry]
    /// Every call, to name the one this brief follows.
    let allCalls: [CallEntry]
    let player: CallPlayer
    let state: AppState
    let inCall: Bool
    @Binding var tab: String?
    var refresh = 0
    var actions = BriefActions()
    @ViewState private var doc: BriefDocument?
    @ViewState private var changed: Date?
    @ViewState private var files: [URL] = []
    @ViewState private var editing = false
    @ViewState private var missing = false
    @Environment(\.layoutDirection) private var direction

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !calls.isEmpty && !editing {
                Picker(L("תצוגה", "View"), selection: $tab) {
                    ForEach(inReadingOrder(tabs), id: \.self) { item in
                        Text(item.map { id in Self.callLabel(calls.first { $0.id == id }) } ?? L("תדריך", "Brief")).tag(item)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
                .padding([.horizontal, .top], 20)
            }
            if let id = tab, let call = calls.first(where: { $0.id == id }), !editing {
                CallPage(call: call, player: player, state: state, refresh: refresh, renamed: actions.reload,
                         followUp: { actions.followUp(call) })
                    .id("\(call.id) \(call.hasSummary)")
            } else if editing, let doc {
                BriefEditor(doc: Self.titled(doc, brief.title), file: brief.file, changed: changed, files: files, data: state.data,
                            saved: { file in
                                editing = false
                                load()
                                actions.saved(file)
                            },
                            cancelled: {
                                editing = false
                                load()
                            })
            } else if missing {
                Text(L("לא הצלחתי לקרוא את קובץ התדריך.", "Could not read the brief file.")).foregroundStyle(.orange).padding(20)
                Spacer()
            } else {
                ScrollView { reading.padding(20).frame(maxWidth: .infinity, alignment: .leading) }
            }
        }
        .task(id: "\(brief.file) \(refresh) \(brief.changed.timeIntervalSince1970)") {
            if !editing { load() }
        }
    }

    private var tabs: [String?] { [nil] + calls.map { Optional($0.id) } }

    /// "שיחה 18.11 14:00".
    static func callLabel(_ call: CallEntry?) -> String {
        guard let call else { return L("שיחה", "Call") }
        let c = Calendar.current.dateComponents([.day, .month], from: call.started)
        return L("שיחה ", "Call ") + "\(c.day ?? 0).\(c.month ?? 0) " + CallLibrary.time.string(from: call.started)
    }

    private func load() {
        guard let found = BriefStore.load(state.data, brief.file) else {
            missing = true
            return
        }
        missing = false
        doc = found.doc
        changed = found.changed
        files = BriefStore.files(state.data, brief.file)
    }

    /// When, type, language and length, the parts it has.
    static func details(_ doc: BriefDocument, now: Date = Date()) -> String {
        [doc.when.map { Briefs.whenLabel($0, now: now) } ?? L("בלי מועד", "No time"),
         callTypeLabels[doc.callType] ?? doc.callType, Briefs.langLabel(doc.lang),
         doc.lengthMin.map { L("\(String(format: "%g", $0)) דקות", "\(String(format: "%g", $0)) min") }]
            .compactMap { $0 }.joined(separator: " · ")
    }

    @ViewBuilder private var reading: some View {
        if let doc {
            VStack(alignment: .leading, spacing: 14) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(TextDirection.marked(doc.title.isEmpty ? brief.title : doc.title))
                        .font(.system(size: 20, weight: .semibold))
                    Text(Self.details(doc)).foregroundStyle(.secondary).monospacedDigit()
                    if let after = doc.after {
                        Button {
                            actions.openCall(after)
                        } label: {
                            Text(L("המשך של: ", "Follows: ") + TextDirection.marked(allCalls.first { $0.id == after }?.title ?? after))
                        }
                        .buttonStyle(.link)
                        .disabled(!allCalls.contains { $0.id == after })
                    }
                }
                HStack(spacing: 8) {
                    Button { actions.start(brief) } label: { Label(L("התחל שיחה עם התדריך", "Start a call with the brief"), systemImage: "mic.fill") }
                        .buttonStyle(.borderedProminent).tint(.red)
                        .disabled(inCall)
                    Button(L("ערוך", "Edit")) { editing = true }
                    if let last = calls.first {
                        Button { actions.followUp(last) } label: { Label(CallPage.followUpTitle, systemImage: "sparkles") }
                    }
                    Button(L("פתח בעורך", "Open in editor")) {
                        NSWorkspace.shared.open(BriefStore.url(state.data, brief.file))
                    }
                }
                if Self.isEmpty(doc, files: files) {
                    Text(L("התדריך עדיין ריק. \"ערוך\" כדי להוסיף מטרות, שאלות, מושגים, קישורים, קבצים והקשר.",
                           "The brief is still empty. \"Edit\" adds goals, questions, terms, links, files and context."))
                        .foregroundStyle(.secondary)
                }
                section(Self.sectionOrder[0], doc.goals.isEmpty) {
                    ForEach(Array(doc.goals.enumerated()), id: \.offset) { _, goal in bullet(goal) }
                }
                section(Self.sectionOrder[1], doc.body.isEmpty) {
                    ForEach(SummaryText.lines(Self.context(doc.body, title: brief.title))) { SummaryLineView(line: $0) }
                }
                section(L("שאלות פתיחה", "Opening questions"), doc.questions.isEmpty) {
                    ForEach(Array(doc.questions.enumerated()), id: \.offset) { _, item in
                        let parts = BriefEditor.split(item, " | ")
                        VStack(alignment: .leading, spacing: 1) {
                            bullet(parts.0)
                            if !parts.1.isEmpty {
                                DirectedText(parts.1, size: 12, side: direction).foregroundStyle(.secondary).padding(.leading, 14)
                            }
                        }
                    }
                }
                section(L("מושגים", "Terms"), doc.terms.isEmpty) {
                    ForEach(Array(doc.terms.enumerated()), id: \.offset) { _, item in
                        let parts = BriefEditor.split(item, ":")
                        HStack(alignment: .firstTextBaseline, spacing: 4) {
                            Text(TextDirection.marked(parts.0)).fontWeight(.semibold)
                            if !parts.1.isEmpty { DirectedText(parts.1).foregroundStyle(.secondary) }
                        }
                    }
                }
                section(L("קישורים", "Links"), doc.links.isEmpty) {
                    ForEach(doc.links, id: \.self) { link in
                        if let url = URL(string: link), ["http", "https"].contains(url.scheme?.lowercased() ?? "") {
                            Link(link, destination: url).lineLimit(1).truncationMode(.middle)
                        } else {
                            Text(link).lineLimit(1)
                        }
                    }
                }
                section(L("קבצים", "Files"), files.isEmpty) {
                    ForEach(files, id: \.self) { url in
                        Button { NSWorkspace.shared.open(url) } label: {
                            Label(TextDirection.marked(url.lastPathComponent), systemImage: "doc")
                        }
                        .buttonStyle(.link)
                    }
                }
            }
            .textSelection(.enabled)
        }
    }

    /// The body without a first `# ` heading that only repeats the title, as a file with no header has it.
    static func context(_ body: String, title: String) -> String {
        let lines = body.components(separatedBy: "\n")
        guard let first = lines.first, first.hasPrefix("# "),
              NewNames.clean(String(first.dropFirst(2))) == NewNames.clean(title) else { return body }
        return lines.dropFirst().joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// A file with no `title:` is named by its heading or its file name; the form starts from that name.
    static func titled(_ doc: BriefDocument, _ title: String) -> BriefDocument {
        var doc = doc
        if NewNames.clean(doc.title).isEmpty { doc.title = title }
        return doc
    }

    static func isEmpty(_ doc: BriefDocument, files: [URL]) -> Bool {
        doc.goals.isEmpty && doc.questions.isEmpty && doc.terms.isEmpty && doc.links.isEmpty && files.isEmpty && doc.body.isEmpty
    }

    /// A bullet and its text in the text's own order, on the page's side, so an English goal reads "• To hear…" under
    /// a Hebrew heading.
    private func bullet(_ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text("•")
            Text(TextDirection.marked(text)).multilineTextAlignment(.leading).fixedSize(horizontal: false, vertical: true)
        }
        .environment(\.layoutDirection, TextDirection.isRTL(text) ? .rightToLeft : .leftToRight)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// A part with something in it; "מטרות" and "הקשר" stay when empty, with their one-line hint.
    @ViewBuilder private func section<Content: View>(_ title: String, _ empty: Bool, @ViewBuilder content: () -> Content) -> some View {
        if !empty || Self.emptyHint(title) != nil {
            VStack(alignment: .leading, spacing: 5) {
                Text(title).font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
                if empty, let hint = Self.emptyHint(title) {
                    DirectedText(hint, size: 13).foregroundStyle(.secondary)
                } else {
                    content()
                }
            }
        }
    }

    /// The parts of a brief in the order the page and the editor show them: what he wants from the call and what the
    /// coach needs to know come first.
    static var sectionOrder: [String] {
        [L("מטרות", "Goals"), L("הקשר", "Context"), L("שאלות פתיחה", "Opening questions"),
         L("מושגים", "Terms"), L("קישורים", "Links"), L("קבצים", "Files")]
    }

    /// The line under an empty "מטרות" or "הקשר"; the other parts have none.
    static func emptyHint(_ title: String) -> String? {
        switch title {
        case L("מטרות", "Goals"): return L("מה אתה רוצה לצאת איתו מהשיחה", "What you want to come out of the call with")
        case L("הקשר", "Context"): return L("מה המאמן צריך לדעת", "What the coach needs to know")
        default: return nil
        }
    }
}

/// The brief as a form: every part of it, and "שמור" writes the file. Files to add and to take out wait for "שמור" too.
/// A brief with no file yet (`file` nil) gets one on its first save.
struct BriefEditor: View {
    struct Pair: Identifiable, Equatable {
        let id = UUID()
        var first = ""
        var second = ""
    }

    struct Line: Identifiable, Equatable {
        let id = UUID()
        var text = ""
    }

    let doc: BriefDocument
    let file: String?
    let changed: Date?
    let files: [URL]
    var data = Paths.data
    var saved: (String) -> Void = { _ in }
    var cancelled: () -> Void = {}
    @ViewState private var title = ""
    @ViewState private var timed = false
    @ViewState private var when = PrepSheet.nextHour()
    @ViewState private var length = ""
    @ViewState private var callType = "general"
    @ViewState private var lang = "auto"
    @ViewState private var goals: [Line] = []
    @ViewState private var questions: [Pair] = []
    @ViewState private var terms: [Pair] = []
    @ViewState private var links: [Line] = []
    @ViewState private var adding: [URL] = []
    @ViewState private var removing: Set<URL> = []
    @ViewState private var bodyText = ""
    @ViewState private var failure: String?
    @ViewState private var conflict = false
    @ViewState private var filled = false
    @ViewState private var targeted = false

    /// "text | he" or "Term: note" in two parts, the second "" when there is none.
    static func split(_ item: String, _ separator: String) -> (String, String) {
        guard let range = item.range(of: separator) else { return (item.trimmingCharacters(in: .whitespaces), "") }
        return (String(item[..<range.lowerBound]).trimmingCharacters(in: .whitespaces),
                String(item[range.upperBound...]).trimmingCharacters(in: .whitespaces))
    }

    /// The two parts back in one item; the second is left out when it is empty, and so is a pair with no first part.
    static func joined(_ first: String, _ second: String, _ separator: String) -> String? {
        let a = NewNames.clean(first), b = NewNames.clean(second)
        guard !a.isEmpty else { return nil }
        return b.isEmpty ? a : a + separator + b
    }

    /// The form's values as a document; header lines it does not edit come from the document it started from.
    static func document(from start: BriefDocument, title: String, when: Date?, length: String, callType: String, lang: String,
                         goals: [String], questions: [(String, String)], terms: [(String, String)], links: [String],
                         body: String) -> BriefDocument {
        var doc = start
        doc.title = NewNames.clean(title)
        doc.when = when
        doc.lengthMin = Briefs.minutes(length)
        doc.callType = callType
        doc.lang = lang
        doc.goals = goals.map(NewNames.clean).filter { !$0.isEmpty }.prefix(Briefs.mostGoals).map { $0 }
        doc.questions = questions.compactMap { joined($0.0, $0.1, " | ") }.prefix(Briefs.mostQuestions).map { $0 }
        doc.terms = terms.compactMap { joined($0.0, $0.1, ": ") }.prefix(Briefs.mostTerms).map { $0 }
        doc.links = links.map(NewNames.clean).filter { !$0.isEmpty }
        doc.body = body.trimmingCharacters(in: .whitespacesAndNewlines)
        return doc
    }

    private var types: [String] {
        let all = CallTypes.all(data: data)
        return all.contains(callType) ? all : all + [callType]
    }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView { form.padding(20).frame(maxWidth: .infinity, alignment: .leading) }
            Divider()
            HStack(spacing: 8) {
                if let failure { DirectedText(failure, size: 12).foregroundStyle(.orange).lineLimit(3) }
                if conflict {
                    Button(L("טען מחדש", "Load again"), action: cancelled)
                    Button(L("שמור בכל זאת", "Save anyway")) { save(force: true) }
                }
                Spacer()
                Button(L("ביטול", "Cancel"), action: cancelled).keyboardShortcut(.cancelAction)
                Button(L("שמור", "Save")) { save(force: false) }.keyboardShortcut("s", modifiers: .command)
                    .buttonStyle(.borderedProminent)
                    .disabled(NewNames.clean(title).isEmpty)
            }
            .padding(12)
        }
        .onAppear(perform: fill)
    }

    private var form: some View {
        VStack(alignment: .leading, spacing: 14) {
            labeled(L("כותרת", "Title")) { DirectedField(placeholder: L("למשל: היכרות עם דנה מ-Acme", "For example: meeting Dana from Acme"), text: $title, size: 15) }
            HStack(spacing: 12) {
                Toggle(L("מועד", "Time"), isOn: $timed)
                if timed {
                    DatePicker("", selection: $when, displayedComponents: [.date, .hourAndMinute])
                        .datePickerStyle(.stepperField).labelsHidden().fixedSize()
                }
                Text(L("אורך בדקות", "Length in minutes"))
                TextField("30", text: $length).textFieldStyle(.roundedBorder).frame(width: 60)
                Spacer()
            }
            HStack(spacing: 12) {
                Picker(L("סוג", "Type"), selection: $callType) {
                    ForEach(types, id: \.self) { Text(callTypeLabels[$0] ?? $0).tag($0) }
                }
                .fixedSize()
                Picker(L("שפה", "Language"), selection: $lang) {
                    Text(L("אוטומטית", "Automatic")).tag("auto")
                    Text(L("עברית", "Hebrew")).tag("he")
                    Text(L("אנגלית", "English")).tag("en")
                }
                .fixedSize()
                Spacer()
            }
            list(L("מטרות", "Goals"), add: goals.count < Briefs.mostGoals ? { goals.append(Line()) } : nil,
                 addLabel: L("הוסף מטרה", "Add a goal")) {
                if goals.isEmpty, let hint = BriefPage.emptyHint(L("מטרות", "Goals")) {
                    Text(hint).font(.caption).foregroundStyle(.secondary)
                }
                ForEach($goals) { $goal in
                    row(remove: { goals.removeAll { $0.id == goal.id } }) {
                        DirectedField(placeholder: L("מטרה, עד 12 מילים", "A goal, up to 12 words"), text: $goal.text)
                    }
                }
            }
            labeled(L("הקשר", "Context")) {
                EditorField(text: $bodyText,
                            placeholder: (BriefPage.emptyHint(L("הקשר", "Context")) ?? "")
                                + L(". מי בשיחה, רקע, מה לא לשכוח. המאמן קורא את זה בזמן השיחה.",
                                    ". Who is on the call, background, what not to forget. The coach reads it during the call."),
                            height: 220)
            }
            list(L("שאלות פתיחה", "Opening questions"),
                 add: questions.count < Briefs.mostQuestions ? { questions.append(Pair()) } : nil,
                 addLabel: L("הוסף שאלה", "Add a question")) {
                ForEach($questions) { $q in
                    row(remove: { questions.removeAll { $0.id == q.id } }) {
                        VStack(spacing: 4) {
                            DirectedField(placeholder: L("השאלה בשפת השיחה", "The question in the call's language"), text: $q.first)
                            DirectedField(placeholder: L("תרגום לעברית, כשהשאלה לא בעברית", "Hebrew translation, when it is not Hebrew"),
                                          text: $q.second, size: 12)
                        }
                    }
                }
            }
            list(L("מושגים", "Terms"), add: terms.count < Briefs.mostTerms ? { terms.append(Pair()) } : nil,
                 addLabel: L("הוסף מושג", "Add a term")) {
                Text(L("מושג עם הסבר קצר בעברית מופיע בפאנל כשאומרים אותו; מושג בלי הסבר נשמר, אבל לא מופיע.",
                       "A term with a short Hebrew note shows in the panel when it is said; one with no note is kept, but not shown."))
                    .font(.caption).foregroundStyle(.secondary)
                ForEach($terms) { $t in
                    row(remove: { terms.removeAll { $0.id == t.id } }) {
                        HStack(spacing: 6) {
                            DirectedField(placeholder: L("מושג", "Term"), text: $t.first).frame(width: 160)
                            DirectedField(placeholder: L("הסבר קצר", "A short note"), text: $t.second)
                        }
                    }
                }
            }
            list(L("קישורים", "Links"), add: { links.append(Line()) }, addLabel: L("הוסף קישור", "Add a link")) {
                ForEach($links) { $link in
                    row(remove: { links.removeAll { $0.id == link.id } }) {
                        TextField("https://", text: $link.text).textFieldStyle(.roundedBorder).environment(\.layoutDirection, .leftToRight)
                    }
                }
            }
            filesPart
        }
    }

    private var filesPart: some View {
        let shown = files.filter { !removing.contains($0) } + adding
        return VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(L("קבצים", "Files")).font(.system(size: 14, weight: .semibold))
                Spacer()
                Button(L("הוסף קבצים…", "Add files…"), action: choose).controlSize(.small)
            }
            ForEach(shown, id: \.self) { url in
                row(remove: {
                    if adding.contains(url) { adding.removeAll { $0 == url } } else { removing.insert(url) }
                }) {
                    Label(TextDirection.marked(url.lastPathComponent), systemImage: "doc").lineLimit(1).truncationMode(.middle)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            Text(L("גרור לכאן קבצים. הם נשמרים ליד התדריך, למעקב ולהכנת שיחת המשך; המאמן לא קורא אותם בזמן השיחה.",
                   "Drag files here. They are kept beside the brief, to follow it and to prepare a follow-up; the coach does not read them during the call."))
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 8)
            .strokeBorder(targeted ? Color.accentColor : Color.secondary.opacity(0.3), style: StrokeStyle(lineWidth: 1, dash: [4])))
        .dropDestination(for: URL.self) { urls, _ in
            adding += urls.filter { $0.isFileURL && !adding.contains($0) }
            return !urls.isEmpty
        } isTargeted: { targeted = $0 }
    }

    private func labeled<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.system(size: 14, weight: .semibold))
            content()
        }
    }

    private func list<Content: View>(_ title: String, add: (() -> Void)?, addLabel: String,
                                     @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(title).font(.system(size: 14, weight: .semibold))
                Spacer()
                if let add { Button(addLabel, action: add).controlSize(.small) }
            }
            content()
        }
    }

    private func row<Content: View>(remove: @escaping () -> Void, @ViewBuilder content: () -> Content) -> some View {
        HStack(alignment: .top, spacing: 6) {
            content()
            Button(action: remove) { Image(systemName: "minus.circle") }
                .buttonStyle(.borderless)
                .foregroundStyle(.secondary)
                .accessibilityLabel(L("הסר", "Remove"))
                .padding(.top, 3)
        }
    }

    private func fill() {
        guard !filled else { return }
        filled = true
        title = doc.title
        timed = doc.when != nil
        when = doc.when ?? PrepSheet.nextHour()
        length = doc.lengthMin.map { String(format: "%g", $0) } ?? ""
        callType = doc.callType
        lang = doc.lang
        goals = doc.goals.map { Line(text: $0) }
        questions = doc.questions.map { let (a, b) = Self.split($0, " | "); return Pair(first: a, second: b) }
        terms = doc.terms.map { let (a, b) = Self.split($0, ":"); return Pair(first: a, second: b) }
        links = doc.links.map { Line(text: $0) }
        bodyText = doc.body
    }

    private func choose() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        if panel.runModal() == .OK { adding += panel.urls.filter { !adding.contains($0) } }
    }

    private func save(force: Bool) {
        let next = Self.document(from: doc, title: title, when: timed ? when : nil, length: length, callType: callType,
                                 lang: lang, goals: goals.map(\.text), questions: questions.map { ($0.first, $0.second) },
                                 terms: terms.map { ($0.first, $0.second) }, links: links.map(\.text), body: bodyText)
        let name = file ?? BriefStore.newFile(title: next.title, when: next.when, data: data)
        do {
            try BriefStore.save(next, data: data, file: name, read: force || file == nil ? nil : changed)
            for url in removing { try BriefStore.remove(url) }
            if !adding.isEmpty { try BriefStore.add(adding, data: data, file: name) }
            failure = nil
            conflict = false
            saved(name)
        } catch is BriefChanged {
            conflict = true
            failure = L("התדריך השתנה מחוץ לאפליקציה מאז שנפתח כאן. \"טען מחדש\" מציג אותו כמו שהוא בקובץ, בלי השינויים שעשית כאן.",
                        "The brief changed outside the app since it opened here. \"Load again\" shows it as the file has it, without your changes here.")
        } catch {
            failure = L("התדריך לא נשמר: \(error.localizedDescription)", "The brief was not saved: \(error.localizedDescription)")
        }
    }
}

extension SelfCheck {
    static func briefPageChecks(_ root: URL) -> [String] {
        var p: [String] = []
        expect(BriefEditor.split("What made you look now? | מה גרם לכם?", " | ") == ("What made you look now?", "מה גרם לכם?")
               && BriefEditor.split("SCIM: תקן: לסנכרון", ":") == ("SCIM", "תקן: לסנכרון")
               && BriefEditor.split("רק טקסט", " | ") == ("רק טקסט", ""), "an item in two parts", &p)
        var start = BriefDocument()
        start.kept = ["glossary: Okta"]
        let doc = BriefEditor.document(from: start, title: "  שיחה   עם דנה ", when: nil, length: "45 דק", callType: "sales",
                                       lang: "he", goals: ["", "להבין מי מחליט", "ב", "ג", "ד", "ה", "ו"],
                                       questions: [("Why now?", "למה עכשיו?"), ("", "בלי שאלה"), ("מי מחליט?", "")],
                                       terms: [("SCIM", "תקן לסנכרון"), ("בלי הסבר", ""), ("", "")],
                                       links: [" https://example.com ", ""], body: "\n## רקע\nדנה\n")
        expect(doc.title == "שיחה עם דנה" && doc.lengthMin == 45 && doc.goals.count == 5 && doc.goals.first == "להבין מי מחליט"
               && doc.questions == ["Why now? | למה עכשיו?", "מי מחליט?"] && doc.terms == ["SCIM: תקן לסנכרון", "בלי הסבר"]
               && doc.links == ["https://example.com"] && doc.body == "## רקע\nדנה" && doc.kept == ["glossary: Okta"],
               "the form becomes a brief \(doc)", &p)
        let now = Briefs.whenFormatter().date(from: "2026-11-18 12:00")!
        var shown = BriefDocument()
        shown.callType = "sales"
        shown.lang = "en"
        shown.lengthMin = 30
        expect(BriefPage.details(shown, now: now) == "בלי מועד · מכירה · אנגלית · 30 דקות", "the brief's details \(BriefPage.details(shown, now: now))", &p)
        expect(BriefPage.isEmpty(BriefDocument(), files: []) && !BriefPage.isEmpty(shown, files: [URL(fileURLWithPath: "/x")]),
               "an empty brief says so", &p)
        expect(BriefPage.titled(BriefDocument(), "acme").title == "acme" && BriefPage.titled(doc, "acme").title == "שיחה עם דנה",
               "a brief with no title starts the form from its name", &p)
        expect(PrepDraft.text("Acme") == "שיחת המשך ל-Acme." && PrepDraft.text("דנה") == "שיחת המשך לדנה.",
               "the follow-up's text", &p)
        expect(BriefPage.context("# רעיונות לקרן\n- לשאול על סבב A", title: "רעיונות לקרן") == "- לשאול על סבב A"
               && BriefPage.context("# רקע אחר\nטקסט", title: "רעיונות") == "# רקע אחר\nטקסט",
               "a heading that repeats the title is not shown twice", &p)
        expect(BriefPage.sectionOrder == [L("מטרות", "Goals"), L("הקשר", "Context"), L("שאלות פתיחה", "Opening questions"),
                                          L("מושגים", "Terms"), L("קישורים", "Links"), L("קבצים", "Files")], "goals and context first", &p)
        expect(BriefPage.emptyHint(L("מטרות", "Goals")) == L("מה אתה רוצה לצאת איתו מהשיחה", "What you want to come out of the call with")
               && BriefPage.emptyHint(L("הקשר", "Context")) == L("מה המאמן צריך לדעת", "What the coach needs to know")
               && BriefPage.emptyHint(L("מושגים", "Terms")) == nil, "the hints", &p)
        expect(DirectedField.rightSide("Who decides?", page: .rightToLeft) == false && DirectedField.rightSide("מי מחליט?", page: .leftToRight)
               && DirectedField.rightSide(" ", page: .rightToLeft), "a field's text sits on its own side", &p)
        return p
    }
}
