import SwiftUI

/// "מה המושג שנאמר עכשיו?" opens the last lines said as words to pick, beside the search of what was just said.
/// Picked words next to each other in a row are one phrase, so "Okta Identity Governance" is one term; "בדוק" sends
/// the phrases, and the search's own answer is then left out. With nothing picked it closes on its own after 15 s,
/// so the search's answer stands alone.
struct TermPicker: Equatable {
    struct Spot: Hashable {
        let row, word: Int
    }

    static let seconds = 15.0
    /// The end of a long line is what was just said.
    static let wordsPerRow = 20
    /// The engine takes at most five phrases of at most 60 characters.
    static let most = 5
    static let mostChars = 60
    /// Words of the other direction stay together in a row up to this many, so an English name inside Hebrew reads
    /// left to right.
    static let groupMost = 4
    static let edges = CharacterSet.punctuationCharacters.union(.symbols).union(.whitespaces)

    let rows: [[String]]
    private(set) var picked: Set<Spot> = []
    private(set) var until: Double

    /// Nil when the lines have no words.
    init?(lines: [String], now: Double) {
        let rows = lines.map(Self.words).filter { !$0.isEmpty }
        guard !rows.isEmpty else { return nil }
        self.rows = rows
        until = now + Self.seconds
    }

    /// A line's words, the last 20 of a long one; a mark with no letter or digit is not a word.
    static func words(_ line: String) -> [String] {
        Array(line.split(whereSeparator: \.isWhitespace).map(String.init)
            .filter { $0.unicodeScalars.contains(where: CharacterSet.alphanumerics.contains) }
            .suffix(wordsPerRow))
    }

    /// What "בדוק" sends: each run of picked words once, the first five.
    var phrases: [String] {
        var seen = Set<String>()
        return Array(Self.phrases(rows, picked).filter { seen.insert($0).inserted }.prefix(Self.most))
    }

    /// Picks a word or lets it go. A pick that would make a sixth phrase, or one longer than 60 characters, is not
    /// taken. Each change keeps it open 15 s more.
    mutating func toggle(_ spot: Spot, now: Double) {
        guard rows.indices.contains(spot.row), rows[spot.row].indices.contains(spot.word) else { return }
        var next = picked
        if next.remove(spot) == nil {
            next.insert(spot)
            let all = Self.phrases(rows, next)
            guard all.count <= Self.most, all.allSatisfy({ $0.unicodeScalars.count <= Self.mostChars }) else { return }
        }
        picked = next
        until = now + Self.seconds
    }

    func closes(now: Double) -> Bool {
        picked.isEmpty && now >= until
    }

    static func phrases(_ rows: [[String]], _ picked: Set<Spot>) -> [String] {
        var out: [String] = []
        for (r, words) in rows.enumerated() {
            var run: [String] = []
            for (w, word) in words.enumerated() {
                if picked.contains(Spot(row: r, word: w)) {
                    run.append(word)
                } else if !run.isEmpty {
                    out.append(clean(run))
                    run = []
                }
            }
            if !run.isEmpty { out.append(clean(run)) }
        }
        return out.filter { !$0.isEmpty }
    }

    private static func clean(_ run: [String]) -> String {
        run.joined(separator: " ").trimmingCharacters(in: edges)
    }

    /// True for a word with Hebrew or Arabic letters first, false for Latin, nil for one with neither, like a number.
    static func direction(_ word: String) -> Bool? {
        let strong = word.unicodeScalars.contains {
            switch $0.value {
            case 0x41...0x5A, 0x61...0x7A, 0x0590...0x05FF, 0x0600...0x06FF, 0xFB1D...0xFB4F: return true
            default: return false
            }
        }
        return strong ? TextDirection.isRTL(word) : nil
    }

    /// The row's words as the units it lays out: a word of the row's direction alone, and words of the other
    /// direction together, up to four, with a number that follows them.
    static func groups(_ words: [String], rtl: Bool) -> [[Int]] {
        var out: [[Int]] = []
        var run: [Int] = []
        for (i, word) in words.enumerated() {
            let other = direction(word).map { $0 != rtl } ?? !run.isEmpty
            if other {
                if run.count == groupMost {
                    out.append(run)
                    run = []
                }
                run.append(i)
            } else {
                if !run.isEmpty { out.append(run) }
                run = []
                out.append([i])
            }
        }
        if !run.isEmpty { out.append(run) }
        return out
    }
}

/// The picker under the terms title: the last lines said, each a row of words. A click picks a word or lets it go;
/// "בדוק" sends the picked words, the ✕ closes it and the search of what was just said goes on alone.
struct TermPickerView: View {
    let picker: TermPicker
    var toggle: (TermPicker.Spot) -> Void = { _ in }
    var check: () -> Void = {}
    var close: () -> Void = {}
    @Environment(\.panelScale) private var scale

    static var hint: String {
        L("בחר את המילים של המושג, או חכה לחיפוש", "Pick the words of the term, or wait for the search")
    }

    static func checkText(_ phrases: [String]) -> String {
        phrases.isEmpty ? L("בדוק", "Check") : L("בדוק: ", "Check: ") + phrases.joined(separator: ", ")
    }

    var body: some View {
        let phrases = picker.phrases
        VStack(alignment: .leading, spacing: 6 * scale) {
            HStack(alignment: .firstTextBaseline, spacing: 6 * scale) {
                DirectedText(Self.hint, size: 11).foregroundStyle(.secondary)
                Button(action: close) {
                    Image(systemName: "xmark").font(.system(size: 10 * scale, weight: .semibold))
                        .foregroundStyle(.secondary)
                        .padding(3 * scale)
                        .contentShape(Rectangle())
                }
                .buttonStyle(PressDim())
                .accessibilityLabel(L("סגור", "Close"))
            }
            ForEach(Array(picker.rows.enumerated()), id: \.offset) { r, words in
                WordRow(words: words, row: r, picked: picker.picked, toggle: toggle)
            }
            Button(action: check) {
                Text(TextDirection.marked(Self.checkText(phrases)))
                    .font(.system(size: 12 * scale, weight: .semibold))
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .foregroundStyle(phrases.isEmpty ? AnyShapeStyle(.tertiary) : AnyShapeStyle(Color.white))
                    .padding(.horizontal, 10 * scale)
                    .padding(.vertical, 4 * scale)
                    .background(phrases.isEmpty ? Color.primary.opacity(0.06) : Color.accentColor, in: Capsule())
                    .contentShape(Capsule())
            }
            .buttonStyle(PressDim())
            .disabled(phrases.isEmpty)
        }
        .padding(8 * scale)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8 * scale))
    }
}

/// One line said, as words that wrap in the line's own direction.
struct WordRow: View {
    let words: [String]
    let row: Int
    let picked: Set<TermPicker.Spot>
    var toggle: (TermPicker.Spot) -> Void
    @Environment(\.panelScale) private var scale
    @Environment(\.layoutDirection) private var page

    var body: some View {
        let rtl = TextDirection.isRTL(words.joined(separator: " "))
        WordFlow(rtl: rtl, page: page, spacing: 4 * scale, lineSpacing: 4 * scale) {
            ForEach(TermPicker.groups(words, rtl: rtl), id: \.self) { group in
                HStack(spacing: 4 * scale) {
                    ForEach(group, id: \.self) { chip($0) }
                }
                .environment(\.layoutDirection, rtl ? .leftToRight : .rightToLeft)
            }
        }
        .environment(\.layoutDirection, .leftToRight)
    }

    private func chip(_ w: Int) -> some View {
        let spot = TermPicker.Spot(row: row, word: w)
        let on = picked.contains(spot)
        return Button { toggle(spot) } label: {
            Text(TextDirection.marked(words[w]))
                .font(.system(size: 13 * scale))
                .foregroundStyle(on ? Color.white : Color.primary)
                .padding(.horizontal, 5 * scale)
                .padding(.vertical, 2 * scale)
                .background(on ? Color.accentColor : Color.primary.opacity(0.07),
                            in: RoundedRectangle(cornerRadius: 5 * scale))
                .contentShape(Rectangle())
        }
        .buttonStyle(PressDim())
    }
}

/// Lays its views out in lines, from the right edge in a right-to-left row, wrapping at the width it is given. Every
/// line is flush to the page's reading side, so an English line in the Hebrew panel starts under the Hebrew ones.
struct WordFlow: Layout {
    struct Line: Equatable {
        var items: [Int] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    var rtl: Bool
    var page: LayoutDirection
    var spacing: CGFloat
    var lineSpacing: CGFloat

    /// Where a line of this width starts, its first view's left edge counted from the start of its direction.
    static func start(_ width: CGFloat, in bounds: CGRect, rtl: Bool, page: LayoutDirection) -> CGFloat {
        let pageRTL = page == .rightToLeft
        if rtl == pageRTL { return 0 }
        return bounds.width - width
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let lines = Self.lines(subviews.map { $0.sizeThatFits(.unspecified) }, width: proposal.width ?? .infinity,
                               spacing: spacing)
        let height = lines.map(\.height).reduce(0, +) + lineSpacing * CGFloat(max(0, lines.count - 1))
        return CGSize(width: proposal.width ?? lines.map(\.width).max() ?? 0, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let sizes = subviews.map { $0.sizeThatFits(.unspecified) }
        var y = bounds.minY
        for line in Self.lines(sizes, width: bounds.width, spacing: spacing) {
            var x = Self.start(line.width, in: bounds, rtl: rtl, page: page)
            for i in line.items {
                let size = CGSize(width: min(sizes[i].width, bounds.width), height: sizes[i].height)
                let left = rtl ? bounds.maxX - x - size.width : bounds.minX + x
                subviews[i].place(at: CGPoint(x: left, y: y + (line.height - size.height) / 2), anchor: .topLeading,
                                  proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += line.height + lineSpacing
        }
    }

    /// Which views go on each line: as many as fit the width, and a view wider than the width on a line of its own.
    static func lines(_ sizes: [CGSize], width: CGFloat, spacing: CGFloat) -> [Line] {
        var lines: [Line] = []
        var line = Line()
        for (i, size) in sizes.enumerated() {
            let w = min(size.width, width)
            if !line.items.isEmpty && line.width + spacing + w > width {
                lines.append(line)
                line = Line()
            }
            line.width = line.items.isEmpty ? w : line.width + spacing + w
            line.height = max(line.height, size.height)
            line.items.append(i)
        }
        if !line.items.isEmpty { lines.append(line) }
        return lines
    }
}

extension SelfCheck {
    static func pickerChecks(_ root: URL) -> [String] {
        var p: [String] = []
        expect(TermPicker(lines: ["", " - "], now: 0) == nil, "lines with no words open no picker", &p)
        let box = CGRect(x: 0, y: 0, width: 300, height: 40)
        expect(WordFlow.start(100, in: box, rtl: false, page: .rightToLeft) == 200
               && WordFlow.start(100, in: box, rtl: true, page: .rightToLeft) == 0
               && WordFlow.start(100, in: box, rtl: true, page: .leftToRight) == 200,
               "a line in the other direction starts on the page's reading side", &p)
        let lines = ["אנחנו עובדים עם Okta Identity Governance כבר שנה.", "and the SOC 2 audit, right?"]
        guard var picker = TermPicker(lines: lines, now: 0) else {
            p.append("the picker did not open")
            return p
        }
        expect(picker.rows.map(\.count) == [8, 6], "each line is a row of words \(picker.rows)", &p)
        expect(picker.phrases.isEmpty && !picker.closes(now: 14.9) && picker.closes(now: 15),
               "with nothing picked it closes after 15 s", &p)
        for w in [3, 4, 5] { picker.toggle(.init(row: 0, word: w), now: 2) }
        expect(picker.phrases == ["Okta Identity Governance"], "words next to each other are one phrase \(picker.phrases)", &p)
        expect(!picker.closes(now: 100), "a picker with a pick stays open", &p)
        picker.toggle(.init(row: 1, word: 2), now: 3)
        picker.toggle(.init(row: 1, word: 3), now: 3)
        expect(picker.phrases == ["Okta Identity Governance", "SOC 2"], "a phrase from each row \(picker.phrases)", &p)
        picker.toggle(.init(row: 0, word: 4), now: 4)
        expect(picker.phrases == ["Okta", "Governance", "SOC 2"], "letting a word go splits its phrase \(picker.phrases)", &p)
        picker.toggle(.init(row: 0, word: 7), now: 4)
        expect(picker.phrases == ["Okta", "Governance", "שנה", "SOC 2"], "the end mark is not part of a phrase \(picker.phrases)", &p)
        picker.toggle(.init(row: 0, word: 1), now: 5)
        let five = picker.picked
        expect(picker.phrases.count == 5, "five phrases \(picker.phrases)", &p)
        picker.toggle(.init(row: 1, word: 0), now: 6)
        expect(picker.picked == five, "a sixth phrase is not taken", &p)
        picker.toggle(.init(row: 9, word: 0), now: 6)
        expect(picker.picked == five, "a word that is not there is not taken", &p)
        picker.toggle(.init(row: 1, word: 4), now: 6)
        expect(picker.phrases.last == "SOC 2 audit", "a word next to a phrase extends it \(picker.phrases)", &p)
        let long = TermPicker(lines: [Array(repeating: "Governance", count: 7).joined(separator: " ")], now: 0)
        if var long {
            for w in 0..<7 { long.toggle(.init(row: 0, word: w), now: 0) }
            expect(long.phrases.first.map { $0.count <= TermPicker.mostChars } == true, "a phrase stays within 60 characters", &p)
        }
        let many = TermPicker(lines: [(1...30).map { "w\($0)" }.joined(separator: " ")], now: 0)
        expect(many?.rows.first?.first == "w11" && many?.rows.first?.count == 20, "a long line keeps its last 20 words", &p)

        let words = ["אנחנו", "עם", "Okta", "Identity", "Governance", "2026", "כבר", "שנה"]
        expect(TermPicker.groups(words, rtl: true) == [[0], [1], [2, 3, 4, 5], [6], [7]],
               "an English name inside Hebrew stays together \(TermPicker.groups(words, rtl: true))", &p)
        expect(TermPicker.groups(["we", "use", "סלאק", "today"], rtl: false) == [[0], [1], [2], [3]],
               "one Hebrew word in English is a unit of its own", &p)
        expect(TermPicker.groups(["a", "b", "c", "d", "e"].map { "x" + $0 }, rtl: true) == [[0, 1, 2, 3], [4]],
               "at most four together", &p)
        expect(TermPicker.direction("2026") == nil && TermPicker.direction("ה-CISO") == true
               && TermPicker.direction("Okta") == false, "word directions", &p)

        let sizes = [CGSize(width: 40, height: 18), CGSize(width: 50, height: 20), CGSize(width: 30, height: 18),
                     CGSize(width: 200, height: 18)]
        let lined = WordFlow.lines(sizes, width: 100, spacing: 4)
        expect(lined.map(\.items) == [[0, 1], [2], [3]] && lined.first?.width == 94 && lined.first?.height == 20
               && lined.last?.width == 100, "words wrap at the width \(lined)", &p)
        expect(TermPickerView.checkText([]) == "בדוק" && TermPickerView.checkText(["Okta", "SOC 2"]) == "בדוק: Okta, SOC 2",
               "the check button says what it sends", &p)
        return p
    }
}
