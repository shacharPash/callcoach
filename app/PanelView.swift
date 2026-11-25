import SwiftUI

var kindLabels: [String: String] {
    ["question": L("שאלה", "Question"), "tip": L("טיפ", "Tip"), "steer": L("חזרה למטרה", "Back to the goal"), "term": L("מושג", "Term")]
}
let kindSymbols = ["question": "questionmark.bubble", "tip": "lightbulb", "steer": "arrow.uturn.backward"]

/// Dims while pressed, so a click shows at once; the panel never becomes the key window, so the system styles do not.
struct PressDim: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.opacity(configuration.isPressed ? 0.5 : 1)
    }
}

func termSymbol(_ term: Term) -> String {
    ["person": "person", "company": "building.2", "product": "shippingbox", "fund": "banknote",
     "org": "building.columns", "event": "calendar"][term.kind ?? ""] ?? "text.book.closed"
}

func clockText(_ seconds: Double) -> String {
    let s = Int(max(0, seconds))
    return String(format: "%02d:%02d", s / 60, s % 60)
}

func agentColor(_ agent: String) -> Color {
    ["ok": .green, "unavailable": .orange, "auth": .red][agent] ?? .gray
}

private struct PanelScaleKey: EnvironmentKey {
    static let defaultValue: CGFloat = 1
}

extension EnvironmentValues {
    /// How much bigger the panel's text, symbols and spacing are than at its least width; 1 outside the panel.
    var panelScale: CGFloat {
        get { self[PanelScaleKey.self] }
        set { self[PanelScaleKey.self] = newValue }
    }
}

struct PanelView: View {
    static let spacing: CGFloat = 10
    @ObservedObject var state: AppState
    var layout = PanelLayout()
    var drag: (Bool) -> Void
    var resize: (Bool) -> Void = { _ in }
    var fill: () -> Void = {}
    var addContext: () -> Void = {}
    @Environment(\.layoutDirection) private var direction

    var body: some View {
        let m = state.model
        let s = layout.scale
        let strip = Self.isStrip(m, idle: state.idlePanel && !state.inCall)
        let narrow = !strip && PanelLayout.statusInTwoRows(layout.width)
        VStack(alignment: .leading, spacing: Self.spacing * s) {
            if !state.idlePanel || state.inCall {
                StatusLine(state: state, addContext: addContext, twoRows: narrow)
                if m.stopConfirmUntil != nil && m.phase == .active {
                    StopChoice(state: state)
                }
                if m.menuOpen && !m.folded {
                    PanelMenu(state: state, filled: layout.filled, fill: fill)
                }
                if !m.folded {
                    let usage = UsageBadge(status: m.status, open: m.usageOpen, toggle: { state.toggleUsage() })
                    if PanelLayout.usageInChips(layout.width) {
                        // Narrower than the default, the badge flows after the chips, so they keep two or three
                        // rows instead of one chip a row beside it.
                        ChipsRow(chips: m.chips, enabled: m.phase == .active, toggle: { state.toggleChip($0) },
                                 trailing: AnyView(usage))
                    } else {
                        HStack(alignment: .top, spacing: 8 * s) {
                            ChipsRow(chips: m.chips, enabled: m.phase == .active, toggle: { state.toggleChip($0) })
                            Spacer(minLength: 0)
                            usage
                        }
                    }
                    if m.usageOpen { UsageWindow(status: m.status, chips: m.chips, close: { state.toggleUsage() }) }
                }
                if let limit = LimitBanner.text(m.status?.limit, coach: m.chips.coach) {
                    LimitBanner(text: limit, coach: m.chips.coach && m.phase == .active, turnOff: { state.toggleCoach() })
                }
                if let banner = m.banner { NoticeView(notice: banner, tint: .red).frame(maxWidth: strip ? 280 * s : nil) }
                if let note = m.note { NoticeView(notice: note, tint: .secondary).frame(maxWidth: strip ? 280 * s : nil) }
                if !m.folded { sections(m, s) }
            } else {
                IdlePanel(state: state)
            }
        }
        .padding(.bottom, roomBelow(m))
        .padding(14 * s)
        .frame(width: strip ? nil : layout.width, alignment: .leading)
        .fixedSize(horizontal: strip, vertical: true)
        .background { Self.backdrop(RoundedRectangle(cornerRadius: 12 * s)) }
        .overlay(alignment: direction == .rightToLeft ? .bottomLeading : .bottomTrailing) {
            if !m.folded { ResizeGrip(resize: resize).padding(1 * s) }
        }
        .environment(\.panelScale, s)
        .gesture(DragGesture(minimumDistance: 3).onChanged { _ in drag(true) }.onEnded { _ in drag(false) })
    }

    /// The panel's ground: a thick material with the window color over it, so a white page behind the panel does not
    /// wash out the gray text, while it still looks like a light glass. The window color follows light and dark.
    static let groundOpacity = 0.62

    static func backdrop<S: Shape>(_ shape: S) -> some View {
        ZStack {
            shape.fill(.thickMaterial)
            shape.fill(Color(nsColor: .windowBackgroundColor).opacity(groundOpacity))
        }
    }

    static var finishingText: String { L("מסיים: שומר את ההקלטה", "Finishing: saving the recording") }
    static var finishingSummaryText: String {
        L("מסיים: שומר את ההקלטה וכותב סיכום", "Finishing: saving the recording and writing a summary")
    }

    /// The finishing line says whether the summary was asked for.
    static func finishing(_ m: CallModel) -> String {
        m.stopSummary == true ? finishingSummaryText : finishingText
    }

    /// A folded panel is as wide as its content; with no call, the idle panel is not a strip.
    static func isStrip(_ m: CallModel, idle: Bool) -> Bool { m.folded && !idle }

    /// The sections of the chips that are on, in the chips' order; a folded panel shows none of them.
    @ViewBuilder
    private func sections(_ m: CallModel, _ s: CGFloat) -> some View {
        if m.phase == .stopping {
            DirectedText(Self.finishing(m), size: 15, weight: .semibold)
        }
        if let rows = Self.transcript(m) { rows.id(m.chips.coach) }
        if m.chips[.recap] {
            RecapSection(recap: m.recap, parts: m.recapParts,
                         update: m.phase == .active ? m.recapUpdate : nil, canUpdate: m.phase == .active,
                         nowOn: m.nowLineOn, nowLine: m.nowLine,
                         retry: { state.retryRecap() }, refresh: { state.refreshRecap() },
                         toggleNow: { state.toggleNowLine() })
        }
        if m.chips[.ask] {
            SectionBlock(chip: .ask, accessory: m.phase == .active
                            ? AnyView(AskNowButton(asking: m.askingUntil != nil, press: { state.perform(Actions.ask) })) : nil) {
                AskSection(state: state)
            }
        }
        if m.chips[.terms] {
            SectionBlock(chip: .terms, accessory: AnyView(HStack(spacing: 10 * s) {
                SmallSwitch(title: ListOnly.title, on: m.listOnly, hint: ListOnly.hint, toggle: { state.toggleListOnly() })
                if m.phase == .active { TermsNowButton(spot: m.spot, press: { state.perform(Actions.termsNow) }) }
            })) {
                if m.listOnly {
                    DirectedText(ListOnly.line, size: 11).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let picker = m.picker {
                    TermPickerView(picker: picker, toggle: { state.togglePick($0) }, check: { state.checkPicked() },
                                   close: { state.closePicker() })
                }
                TermsList(terms: m.shownTerms, freshId: m.freshTermId, openId: m.openTermId, details: m.termDetails,
                          mores: m.termMore, canAskMore: { m.canAskMore($0) }, adds: m.knowledgeAdds,
                          least: TermsList.leastHeight(transcript: m.chips[.transcript]),
                          extra: layout.extra, room: layout.room, toggle: { state.toggleTerm($0) },
                          add: { state.addToKnowledge($0) }, ignore: { state.ignoreTerm($0) },
                          askMore: { state.askMore($0) })
            }
        }
    }

    /// The room the set height gives above the content's own goes to the terms list; without it, under the last
    /// section.
    private func roomBelow(_ m: CallModel) -> CGFloat {
        (state.idlePanel && !state.inCall) || m.folded || m.chips[.terms] ? 0 : max(0, layout.extra)
    }

    /// The transcript rows while their chip is on, with the Hebrew under the other side's English lines while
    /// translation is on too. With no AI chip on, the call only records and its lines show whole. The rows start over
    /// when the mode changes, so they do not keep the height of the other mode.
    static func transcript(_ m: CallModel) -> TranscriptRows? {
        m.chips[.transcript] ? TranscriptRows(lines: m.recentLines, partial: m.partial,
                                              translations: m.chips[.translate] ? m.translations : [:],
                                              wrap: !m.chips.coach) : nil
    }
}

/// The call's features: a symbol and a word each, a clear on and off, and a thin line marked AI between "כתוביות", which needs no
/// coach, and the coach's chips. They wrap to a second row when the panel is narrow.
struct ChipsRow: View {
    let chips: Chips
    let enabled: Bool
    let toggle: (Chip) -> Void
    /// A view after the last chip, in the same flow: the usage badge on a narrow panel.
    var trailing: AnyView? = nil
    @Environment(\.panelScale) private var scale

    /// The line goes after the last chip that needs no coach, before the first AI chip.
    static func split(after chip: Chip) -> Bool {
        let all = Chip.allCases
        guard let index = all.firstIndex(of: chip), index + 1 < all.count else { return false }
        return !Chip.ai.contains(chip) && Chip.ai.contains(all[index + 1])
    }

    var body: some View {
        FlowRow(spacing: 6 * scale) {
            ForEach(Chip.allCases, id: \.self) { chip in
                if Self.split(after: chip) {
                    HStack(spacing: 6 * scale) {
                        button(chip)
                        Rectangle().fill(Color.secondary.opacity(0.35)).frame(width: 1, height: 14 * scale)
                            .accessibilityHidden(true)
                        Text("AI").font(.system(size: 10 * scale, weight: .medium)).foregroundStyle(.secondary)
                            .help(L("הכפתורים מכאן משתמשים בסוכן, וצורכים טוקנים", "The chips from here use the agent, and use tokens"))
                    }
                } else {
                    button(chip)
                }
            }
            if let trailing { trailing }
        }
    }

    private func button(_ chip: Chip) -> some View {
        let on = chips[chip]
        return Button { toggle(chip) } label: {
            HStack(spacing: 4 * scale) {
                Label(chip.title, systemImage: chip.symbol)
                if chip == .terms {
                    Image(systemName: "cpu").font(.system(size: 9 * scale)).accessibilityLabel(L("מעמיס על המעבד", "Uses the CPU"))
                }
            }
                .font(.system(size: 11 * scale, weight: on ? .semibold : .regular))
                .padding(.vertical, 3 * scale)
                .padding(.horizontal, 7 * scale)
                .background(on ? Color.accentColor.opacity(0.16) : Color.clear, in: Capsule())
                .overlay(Capsule().strokeBorder(on ? Color.accentColor : Color.secondary.opacity(0.35)))
                .foregroundStyle(on ? Color.accentColor : .secondary)
                .contentShape(Capsule())
        }
        .buttonStyle(PressDim())
        .disabled(!enabled)
        .help(chip.hint)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

extension Chip {
    /// One line on what the chip does and what it costs, shown on hover.
    var hint: String {
        switch self {
        case .transcript: return L("מציג את מה שנאמר. התמלול רץ ונשמר תמיד, גם כשזה כבוי.",
                                   "Shows what is said. The transcript always runs and is saved, even when this is off.")
        case .translate: return L("מתרגם שורות באנגלית לעברית: בקשה קטנה על כל כמה שורות.",
                                  "Translates English lines into Hebrew: a small request every few lines.")
        case .recap: return L("סיכום של השיחה עד עכשיו, רק כשמבקשים.", "A recap of the call so far, only when asked.")
        case .ask: return L("שאלות מהסוכן החי. הוא קורא את השיחה כל הזמן, יחד עם המושגים.",
                            "Questions from the live agent. It reads the call all the time, together with the terms.")
        case .terms: return L("שמות ומושגים. כדי ששם יקפוץ באמצע משפט, המשפט מתומלל שוב, וזה מעמיס על המעבד.",
                              "Names and terms. So a name shows mid-sentence, the sentence is transcribed again, which uses the CPU.")
        }
    }
}

/// The CPU and tokens of the call, and the "?" that opens what they are made of.
struct UsageBadge: View {
    let status: Status?
    let open: Bool
    let toggle: () -> Void
    @Environment(\.panelScale) private var scale

    var body: some View {
        Button(action: toggle) {
            HStack(spacing: 6 * scale) {
                if let cpu = status?.cpu {
                    Label(Usage.percent(cpu), systemImage: "cpu").foregroundStyle(Usage.hot ? Color.orange : .secondary)
                }
                if let tokens = status?.tokens { Label(Usage.tokens(tokens), systemImage: "bolt") }
                Image(systemName: open ? "questionmark.circle.fill" : "questionmark.circle")
            }
            .labelStyle(.titleAndIcon)
            .font(.system(size: 11 * scale).monospacedDigit())
            .foregroundStyle(.secondary)
            .fixedSize()
        }
        .buttonStyle(PressDim())
        .help(L("מה צורך עכשיו", "What the call uses now"))
    }
}

enum Usage {
    /// The Mac is warm enough that the CPU is worth saving.
    static var hot: Bool { ProcessInfo.processInfo.thermalState.rawValue >= ProcessInfo.ThermalState.serious.rawValue }

    static func percent(_ cpu: Double) -> String { "\(Int(cpu.rounded()))%" }

    static func tokens(_ n: Int) -> String {
        n < 1000 ? "\(n)" : n < 100_000 ? String(format: "%.1fK", Double(n) / 1000) : "\(n / 1000)K"
    }

    static var heatText: String {
        switch ProcessInfo.processInfo.thermalState {
        case .nominal: return L("חום המק: תקין", "Mac temperature: normal")
        case .fair: return L("חום המק: מעט חם", "Mac temperature: a little warm")
        case .serious: return L("חום המק: חם. כבה את \"מושגים\" כדי להוריד עומס.", "Mac temperature: hot. Turn off Terms to lower the load.")
        case .critical: return L("חום המק: חם מאוד. כבה את \"מושגים\".", "Mac temperature: very hot. Turn off Terms.")
        @unknown default: return ""
        }
    }

    /// The features in the window's order, each with its name; "live" serves both questions and terms.
    static let order = ["live", "explain", "translate", "terms_now", "recap", "screen", "other"]

    static func name(_ feature: String) -> String {
        [
            "live": L("הסוכן החי (שאלות ומושגים)", "The live agent (questions and terms)"),
            "explain": L("הסברים על מושגים", "Term explanations"),
            "translate": L("תרגום", "Translation"),
            "terms_now": L("מה המושג שנאמר עכשיו", "The term just said"),
            "recap": L("סיכום ביניים", "Recap"),
            "screen": L("צילומי מסך", "Screenshots"),
            "other": L("אחר (הכנה ובדיקת חשבון)", "Other (prep and account check)"),
        ][feature] ?? feature
    }

    /// The rows of the window: every feature that used tokens, in order, with its share of the largest.
    static func rows(_ features: [String: Int]) -> [(name: String, tokens: Int, share: Double)] {
        let top = Double(features.values.max() ?? 0)
        let known = order.filter { (features[$0] ?? 0) > 0 }
        let rest = features.keys.filter { !order.contains($0) && (features[$0] ?? 0) > 0 }.sorted()
        return (known + rest).map { f in (name(f), features[f]!, top > 0 ? Double(features[f]!) / top : 0) }
    }
}

/// What the call uses now, opened from the "?": tokens by feature, CallCoach's CPU, the Mac's heat, and what to turn
/// off to save which.
struct UsageWindow: View {
    let status: Status?
    let chips: Chips
    let close: () -> Void
    @Environment(\.panelScale) private var scale

    var body: some View {
        VStack(alignment: .leading, spacing: 6 * scale) {
            HStack {
                DirectedText(L("מה צורך עכשיו", "What the call uses now"), size: 12, weight: .semibold)
                Spacer(minLength: 0)
                Button(action: close) { Image(systemName: "xmark").font(.system(size: 10 * scale, weight: .semibold)) }
                    .buttonStyle(PressDim()).foregroundStyle(.secondary)
            }
            DirectedText(L("טוקנים בשיחה הזאת", "Tokens in this call"), size: 11, weight: .medium).foregroundStyle(.secondary)
            let rows = Usage.rows(status?.features ?? [:])
            if rows.isEmpty {
                DirectedText(L("עדיין לא נשלח כלום לסוכן.", "Nothing was sent to the agent yet."), size: 11).foregroundStyle(.secondary)
            }
            ForEach(rows, id: \.name) { row in
                HStack(spacing: 6 * scale) {
                    DirectedText(row.name, size: 11).frame(width: 150 * scale, alignment: .leading)
                    ShareBar(share: row.share)
                    Text(Usage.tokens(row.tokens)).font(.system(size: 11 * scale).monospacedDigit()).foregroundStyle(.secondary)
                        .frame(width: 40 * scale, alignment: .trailing)
                }
            }
            DirectedText(L("בלי קריאות מהמטמון, שהן רוב כל בקשה וזולות בהרבה.", "Cache reads are left out: they are most of every request and cost far less."),
                         size: 10).foregroundStyle(.tertiary)
            DirectedText(L("מעבד", "CPU"), size: 11, weight: .medium).foregroundStyle(.secondary).padding(.top, 2 * scale)
            DirectedText(status?.cpu.map { L("‏CallCoach משתמש ב-\(Usage.percent($0)) מהמעבד, עם התמלול.", "CallCoach uses \(Usage.percent($0)) of the CPU, transcription included.") }
                         ?? L("לא הצלחתי למדוד את המעבד.", "The CPU could not be measured."), size: 11)
            DirectedText(Usage.heatText, size: 11).foregroundStyle(Usage.hot ? Color.orange : .primary)
            Divider()
            DirectedText(L("לחסוך במכסה: לכבות את \"שאלות\" וגם את \"מושגים\", כי שניהם מאותו סוכן, או להשאיר את \"מושגים\" עם \"רק מהרשימה\": שמות מקבצי הידע ומהתדריך בלי טוקנים. להוריד חום: לכבות את \"מושגים\". ההקלטה והתמלול ממשיכים תמיד.",
                           "To save the quota: turn off both Questions and Terms, which share one agent, or keep Terms with List only: names from the knowledge files and the brief, with no tokens. To cool down: turn off Terms. Recording and transcription always go on."),
                         size: 11).foregroundStyle(.secondary)
        }
        .padding(10 * scale)
        .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8 * scale))
    }
}

struct ShareBar: View {
    let share: Double
    @Environment(\.panelScale) private var scale

    var body: some View {
        GeometryReader { g in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.primary.opacity(0.08))
                Capsule().fill(Color.secondary).frame(width: max(2, g.size.width * share))
            }
        }
        .frame(height: 5 * scale)
    }
}

/// The subscription limit as Claude tells it: near, with a way to turn the agent off and keep the quota for the
/// summary; reached, saying the call goes on.
struct LimitBanner: View {
    let text: String
    let coach: Bool
    let turnOff: () -> Void
    @Environment(\.panelScale) private var scale

    static func text(_ limit: String?, coach: Bool) -> String? {
        switch limit {
        case "near" where coach:
            return L("מתקרבים למגבלת המנוי. ההקלטה תמשיך גם אם היא תיגמר.", "The subscription limit is near. The recording goes on even if it runs out.")
        case "reached":
            return L("המכסה של המנוי נגמרה. ההקלטה והתמלול ממשיכים, וסיכום בסוף השיחה ייכתב מקומית.",
                     "The subscription quota ran out. Recording and transcription go on, and a summary at the end is written locally.")
        default: return nil
        }
    }

    var body: some View {
        HStack(spacing: 8 * scale) {
            DirectedText(text, size: 11).foregroundStyle(.orange)
            if coach {
                Button(L("כבה את הסוכן", "Turn off the agent"), action: turnOff).buttonStyle(PressDim())
                    .font(.system(size: 11 * scale, weight: .semibold)).foregroundStyle(Color.accentColor).fixedSize()
            }
        }
        .padding(8 * scale)
        .background(Color.orange.opacity(0.1), in: RoundedRectangle(cornerRadius: 8 * scale))
    }
}

/// Places its views in rows, starting a new row when the next one does not fit. In a right-to-left layout SwiftUI
/// mirrors the places, so the first view is on the right.
struct FlowRow: Layout {
    var spacing: CGFloat = 6

    /// A view that is over the width by less than this still fits, so a sum of widths that floating point puts a
    /// hair over a width it equals does not start a row.
    static let tolerance: CGFloat = 0.5

    /// Where each view goes from the top leading corner, and the size of them all.
    static func places(_ sizes: [CGSize], width: CGFloat, spacing: CGFloat) -> (points: [CGPoint], size: CGSize) {
        var points: [CGPoint] = []
        var x: CGFloat = 0, y: CGFloat = 0, row: CGFloat = 0, widest: CGFloat = 0
        for size in sizes {
            if x > 0 && x + size.width > width + tolerance { y += row + spacing; x = 0; row = 0 }
            points.append(CGPoint(x: x, y: y))
            x += size.width + spacing
            row = max(row, size.height)
            widest = max(widest, x - spacing)
        }
        return (points, CGSize(width: min(widest, width), height: y + row))
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        Self.places(subviews.map { $0.sizeThatFits(.unspecified) }, width: proposal.width ?? .infinity, spacing: spacing).size
    }

    /// The rows break at the width the size was measured for, not at the narrower size it reported, so a row never
    /// breaks where no room was kept for it.
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let sizes = subviews.map { $0.sizeThatFits(.unspecified) }
        let points = Self.places(sizes, width: proposal.width ?? bounds.width, spacing: spacing).points
        for (view, (point, size)) in zip(subviews, zip(points, sizes)) {
            view.place(at: CGPoint(x: bounds.minX + point.x, y: bounds.minY + point.y), proposal: ProposedViewSize(size))
        }
    }
}

/// The bottom right corner of the panel; dragging it sets the panel's width and height, each on its own: long and
/// narrow, or wide and short. Its lines are dark enough to find on the panel's ground, darker under the mouse, and the
/// spot to grab is larger than the lines.
struct ResizeGrip: View {
    var resize: (Bool) -> Void
    @Environment(\.panelScale) private var scale
    @ViewState private var hover = false

    static let side: CGFloat = 18

    var body: some View {
        Canvas { context, size in
            for inset in [0.25, 0.5, 0.75] {
                var line = Path()
                line.move(to: CGPoint(x: size.width * (1 - inset), y: size.height - 1))
                line.addLine(to: CGPoint(x: size.width - 1, y: size.height * (1 - inset)))
                context.stroke(line, with: .color(Color.primary.opacity(hover ? 0.7 : 0.4)),
                               style: StrokeStyle(lineWidth: 1.5 * scale, lineCap: .round))
            }
        }
        .frame(width: Self.side * scale, height: Self.side * scale)
        .contentShape(Rectangle())
        .environment(\.layoutDirection, .leftToRight)
        .onHover { hover = $0 }
        .modifier(ResizeCursor())
        .gesture(DragGesture(minimumDistance: 1).onChanged { _ in resize(true) }.onEnded { _ in resize(false) })
        .help(L("גוררים כדי לשנות רוחב וגובה", "Drag to change the width and the height"))
        .accessibilityLabel(L("שינוי גודל הפאנל", "Resize the panel"))
    }
}

/// The diagonal resize pointer over the grip, where macOS has one for a view (15 and later).
private struct ResizeCursor: ViewModifier {
    func body(content: Content) -> some View {
        if #available(macOS 15.0, *) {
            content.pointerStyle(.frameResize(position: .bottomTrailing))
        } else {
            content
        }
    }
}

/// The red dot and the time, the hearing switch, the agent's dot when something is wrong, and the call's controls.
/// While the recording is paused the dot is a pause sign and the hearing switch says so instead.
struct StatusLine: View {
    @ObservedObject var state: AppState
    var addContext: () -> Void = {}
    /// A narrow panel puts the call's controls on a second row, under the time and the panel's own buttons.
    var twoRows = false
    @Environment(\.panelScale) private var scale

    /// The agent's dot shows only while the coach is on, and only when the agent is not answering or the account was
    /// rejected; in a recording the problem shows when the coach is switched on.
    static func agentTrouble(_ agent: String, coach: Bool) -> Bool {
        coach && (agent == "unavailable" || agent == "auth")
    }

    /// "טוען תמלול…" shows from the start of the recording until the speech models are loaded.
    static func loading(phase: Phase, transcribing: Bool) -> Bool {
        phase == .active && !transcribing
    }

    /// The panel never becomes the key window, so a button left to its style is drawn as in a window in the back.
    static func tint(_ enabled: Bool) -> Color {
        enabled ? .primary : .secondary
    }

    var body: some View {
        Group {
            if twoRows {
                VStack(alignment: .leading, spacing: 6 * scale) {
                    HStack(spacing: 8 * scale) {
                        lead
                        Spacer(minLength: 0)
                        window
                    }
                    HStack(spacing: 8 * scale) {
                        controls
                        Spacer(minLength: 0)
                    }
                }
            } else {
                HStack(spacing: 8 * scale) {
                    lead
                    Spacer(minLength: 0)
                    controls
                    window
                }
            }
        }
        .font(.system(size: 12 * scale))
        .buttonStyle(PressDim())
    }

    /// The dot and the time, the hearing switch (or the pause or loading), and the agent's dot.
    @ViewBuilder private var lead: some View {
        let m = state.model
        let s = m.status
        let active = m.phase == .active
        if m.paused {
            Image(systemName: "pause.fill").font(.system(size: 8 * scale, weight: .bold))
                .foregroundStyle(PausedChip.color).frame(width: 8 * scale, height: 8 * scale)
        } else {
            Circle().fill(active ? Color.red : .gray).frame(width: 8 * scale, height: 8 * scale)
        }
        Text(clockText(s?.elapsed ?? 0)).monospacedDigit().fixedSize()
        if Self.loading(phase: m.phase, transcribing: s?.transcribing ?? true) {
            ViewThatFits(in: .horizontal) {
                Text(L("טוען תמלול…", "Loading the transcript…")).fixedSize()
                Text(L("טוען…", "Loading…")).fixedSize()
            }
            .foregroundStyle(.secondary)
        } else if m.paused {
            PausedChip(toggle: { state.togglePause() })
        } else {
            HearingChip(hearing: m.hearing, share: s?.talkShare ?? 0, toggle: { state.toggleHearing() })
        }
        if Self.agentTrouble(m.agent, coach: m.chips.coach) {
            Circle().fill(agentColor(m.agent)).frame(width: 8 * scale, height: 8 * scale)
                .accessibilityLabel(errorTitles[m.agent == "auth" ? "agent_auth" : "agent_unavailable"] ?? "")
        }
    }

    /// The call's controls: the coach switch, context, pause and stop.
    @ViewBuilder private var controls: some View {
        let m = state.model
        let active = m.phase == .active
        let asking = m.stopConfirmUntil != nil
        Button { state.toggleCoach() } label: { CoachSwitch(on: m.chips.coach) }
            .disabled(!active)
            .accessibilityAddTraits(m.chips.coach ? .isSelected : [])
        if m.chips.coach {
            Button { addContext() } label: { Self.hit(Text(L("הקשר", "Context")).fixedSize()) }
                .foregroundStyle(Self.tint(active))
                .disabled(!active)
        }
        Button { state.togglePause() } label: { Self.hit(Text(m.paused ? L("המשך", "Resume") : L("השהה", "Pause")).fixedSize()) }
            .foregroundStyle(m.paused ? PausedChip.color : Self.tint(active))
            .fontWeight(m.paused ? .semibold : .regular)
            .disabled(!active)
            .accessibilityHint(m.paused ? L("ממשיך להקליט את השיחה", "Goes on recording the call")
                                        : L("משהה את ההקלטה בלי לסיים את השיחה", "Pauses the recording without ending the call"))
        Button { state.pressStop() } label: { Self.hit(Text(L("עצור", "Stop")).fixedSize()) }
            .foregroundStyle(asking ? Color.red : Self.tint(active))
            .fontWeight(asking ? .semibold : .regular)
            .disabled(!active)
    }

    /// The panel's own buttons: "…", fold and close.
    @ViewBuilder private var window: some View {
        let m = state.model
        if !m.folded {
            Button { state.toggleMenu() } label: {
                Self.hit(Image(systemName: "ellipsis").font(.system(size: 13 * scale, weight: .semibold))
                    .foregroundStyle(m.menuOpen ? Color.accentColor : .primary))
            }
            .accessibilityLabel(L("עוד", "More"))
        }
        Button { state.toggleFold() } label: {
            Self.hit(Image(systemName: m.folded ? "chevron.down" : "chevron.up")
                .font(.system(size: 11 * scale, weight: .semibold)))
        }
        .accessibilityLabel(m.folded ? L("פתח את הפאנל", "Unfold the panel") : L("קפל את הפאנל", "Fold the panel"))
        Button { state.closePanel() } label: {
            Self.hit(Image(systemName: "xmark").font(.system(size: 11 * scale, weight: .semibold)))
        }
        .accessibilityLabel(L("סגור את הפאנל", "Close the panel"))
    }

    /// A header button's label with a few points of room around it, all of it clickable.
    static func hit(_ label: some View) -> some View {
        label.padding(.vertical, 3).padding(.horizontal, 2).contentShape(Rectangle())
    }
}

/// "מאמן" in the header, one switch for every AI chip, drawn as the chips are: a tinted capsule while the coach is on,
/// an empty one while it is off. Both are the same size, so a click moves nothing.
struct CoachSwitch: View {
    let on: Bool
    @Environment(\.panelScale) private var scale

    var body: some View {
        HStack(spacing: 3 * scale) {
            Image(systemName: "sparkles").font(.system(size: 11 * scale, weight: .medium))
            Text(L("מאמן", "Coach")).fontWeight(.medium)
        }
        .fixedSize()
        .foregroundStyle(on ? Color.accentColor : .secondary)
        .padding(.horizontal, 7 * scale)
        .background(Capsule().fill(on ? Color.accentColor.opacity(0.16) : Color.clear).padding(.vertical, -2 * scale))
        .overlay(Capsule().strokeBorder(on ? Color.accentColor : Color.secondary.opacity(0.35))
            .padding(.vertical, -2 * scale))
        .padding(.vertical, 3)
        .contentShape(Capsule())
    }
}

/// The second step of stop, on a row of its own under the header: two small buttons, the default first and the only
/// one in red, and "ביטול" right after them.
struct StopChoice: View {
    @ObservedObject var state: AppState
    @Environment(\.panelScale) private var scale

    /// The two stop buttons, the default first: a summary only when asked for, since one can be written later from
    /// the call's page.
    static var choices: [(title: String, summary: Bool)] {
        [(title: L("עצור בלי סיכום", "Stop without a summary"), summary: false),
         (title: L("עצור וסכם", "Stop and summarize"), summary: true)]
    }

    var body: some View {
        // A flow, so on a narrow panel "ביטול" goes to a second row instead of past the edge.
        FlowRow(spacing: 8 * scale) {
            ForEach(Array(Self.choices.enumerated()), id: \.offset) { index, choice in
                Button { state.confirmStop(summary: choice.summary) } label: {
                    button(choice.title, prominent: index == 0)
                }
            }
            Button { state.cancelStop() } label: { StatusLine.hit(Text(L("ביטול", "Cancel")).fixedSize()) }
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .font(.system(size: 12 * scale))
        .buttonStyle(PressDim())
    }

    /// A small button with a border, or filled red for the default. The panel never becomes the key window, so the
    /// system's bordered styles would draw both gray.
    private func button(_ title: String, prominent: Bool) -> some View {
        let shape = RoundedRectangle(cornerRadius: 5 * scale)
        return Text(title)
            .fontWeight(prominent ? .semibold : .regular)
            .fixedSize()
            .foregroundStyle(prominent ? Color.white : .primary)
            .padding(.vertical, 3 * scale)
            .padding(.horizontal, 9 * scale)
            .background(prominent ? Color.red : Color.clear, in: shape)
            .overlay(shape.strokeBorder(prominent ? Color.clear : Color.secondary.opacity(0.5)))
            .contentShape(shape)
    }
}

/// What "…" opens, one item a line: the call's language, the panel's size, copying the transcript, and the call's
/// keys, each with its keys at the line's end. Hiding the panel is the X right above it, so it is not in the list. It
/// is drawn inside the panel, because a menu pops up in a window of its own, which a screen share shows. Any choice
/// closes it. The nudge level is in Settings, as the level a call starts with.
struct PanelMenu: View {
    @ObservedObject var state: AppState
    var filled = false
    var fill: () -> Void = {}
    @Environment(\.panelScale) private var scale

    static let languages = ["auto", "he", "en"]

    static var languageTitle: String { L("שפת השיחה", "Call language") }

    static var languageLabels: [String: String] {
        ["auto": L("אוטומטית", "Automatic"), "he": L("עברית", "Hebrew"), "en": L("אנגלית", "English")]
    }

    /// The call's keys in the list, each with the symbol the menu bar shows for it.
    static var keys: [(action: AppAction, symbol: String)] {
        [(Actions.ask, MenuSymbol.ask), (Actions.termsNow, MenuSymbol.termsNow), (Actions.screen, MenuSymbol.screen),
         (Actions.mark, MenuSymbol.mark)]
    }

    static func fillTitle(_ filled: Bool) -> String {
        filled ? L("חזרה לגודל הקודם", "Back to the size before") : Actions.fill.label
    }

    var body: some View {
        let m = state.model
        let active = m.phase == .active
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12 * scale) {
                Text(Self.languageTitle).font(.system(size: 11 * scale, weight: .semibold)).foregroundStyle(.secondary)
                ForEach(Self.languages, id: \.self) { lang in
                    language(lang, enabled: active)
                }
            }
            .padding(.vertical, 3 * scale)
            Divider().padding(.vertical, 4 * scale)
            item(Self.fillTitle(filled),
                 symbol: filled ? "arrow.down.right.and.arrow.up.left" : "arrow.up.left.and.arrow.down.right",
                 keys: Actions.fill.hotkey, action: fill)
            item(L("העתק את התמלול", "Copy the transcript"), symbol: MenuSymbol.copy, enabled: m.folder != nil) {
                state.copyTranscript()
            }
            Divider().padding(.vertical, 4 * scale)
            ForEach(Self.keys, id: \.action.id) { key in
                item(key.action.label, symbol: key.symbol, keys: key.action.hotkey, enabled: active) {
                    state.perform(key.action)
                }
            }
        }
        .font(.system(size: 12 * scale))
        .buttonStyle(PressDim())
        .padding(.vertical, 7 * scale)
        .padding(.horizontal, 10 * scale)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 8 * scale))
    }

    /// A language to choose, with a check beside the one the call holds.
    private func language(_ lang: String, enabled: Bool) -> some View {
        let marked = state.language == lang
        return Button { state.fromMenu { state.setLanguage(lang) } } label: {
            HStack(spacing: 3 * scale) {
                if marked { Image(systemName: "checkmark").font(.system(size: 10 * scale, weight: .bold)) }
                Text(Self.languageLabels[lang] ?? lang).fontWeight(marked ? .semibold : .regular)
            }
            .contentShape(Rectangle())
        }
        .foregroundStyle(StatusLine.tint(enabled))
        .disabled(!enabled)
    }

    /// One line of the list: the symbol, the words, and the keys at the line's end.
    private func item(_ title: String, symbol: String, keys: String? = nil, enabled: Bool = true,
                      action: @escaping () -> Void) -> some View {
        Button { state.fromMenu(action) } label: {
            HStack(spacing: 6 * scale) {
                Image(systemName: symbol).font(.system(size: 11 * scale)).frame(width: 16 * scale)
                Text(title)
                Spacer(minLength: 12 * scale)
                if let keys { Text(keys).foregroundStyle(.secondary) }
            }
            .padding(.vertical, 3 * scale)
            .contentShape(Rectangle())
        }
        .foregroundStyle(StatusLine.tint(enabled))
        .disabled(!enabled)
    }
}

/// One nudge of a few words, in the largest type on the panel, with its kind ("טיפ", "שאלה") above it, so the symbol
/// is not the only hint; a question card has its Hebrew translation under it. The kind, the symbol and the text stay on
/// the panel's reading side, the text in its own direction, as the questions and the transcript do. The slot keeps the
/// height of a one-line nudge, so the panel does not jump when one comes or goes.
struct NudgeSlot: View {
    static let height: CGFloat = 42
    let card: Card?
    let phase: Phase
    var asked: Asked? = nil
    /// "לא בכיוון" at the end of a question card, while the call runs.
    var notThisWay: ((Card) -> Void)? = nil
    /// "בכיוון" beside it.
    var onTrack: ((Card) -> Void)? = nil
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.layoutDirection) private var direction
    @Environment(\.panelScale) private var scale

    var body: some View {
        ZStack(alignment: .leading) {
            if phase == .stopping {
                Color.clear
            } else if let asked {
                AskedView(asked: asked)
                    .id("asked " + asked.text)
                    .transition(.opacity)
            } else if let card {
                VStack(alignment: .leading, spacing: 2 * scale) {
                    if let label = kindLabels[card.kind] { SectionLabel(label) }
                    HStack(alignment: .firstTextBaseline, spacing: 8 * scale) {
                        Image(systemName: kindSymbols[card.kind] ?? "text.bubble")
                            .font(.system(size: 16 * scale, weight: .semibold))
                            .foregroundStyle(card.forced ? Color.accentColor : .secondary)
                        VStack(alignment: .leading, spacing: 1 * scale) {
                            DirectedText(card.text, size: 22, weight: .semibold, side: direction).lineLimit(2)
                            if let he = card.he, card.kind == "question", !he.isEmpty {
                                DirectedText(he, size: 13, side: direction).foregroundStyle(.secondary).lineLimit(2)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        if card.kind == "question", let notThisWay {
                            DirectionButtons(onTrack: { onTrack?(card) }, notThisWay: { notThisWay(card) })
                        }
                    }
                }
                .id(card.id)
                .transition(.opacity)
            } else {
                DirectedText(L("כאן תופיע שאלה שתבקש (\(Actions.ask.hotkey))", "A question you ask for shows here (\(Actions.ask.hotkey))"),
                             size: 13)
                    .foregroundStyle(.tertiary)
                    .transition(.opacity)
            }
        }
        .frame(minHeight: Self.height * scale, alignment: .leading)
        .animation(reduceMotion ? nil : .spring(response: 0.3, dampingFraction: 1), value: card?.id)
        .animation(reduceMotion ? nil : .easeOut(duration: 0.3), value: asked?.text)
    }
}

struct SectionLabel: View {
    let text: String
    var tint: Color? = nil
    @Environment(\.panelScale) private var scale

    init(_ text: String, tint: Color? = nil) {
        self.text = text
        self.tint = tint
    }

    var body: some View {
        Text(text).font(.system(size: 11 * scale, weight: .semibold)).foregroundStyle(tint.map(AnyShapeStyle.init) ?? AnyShapeStyle(.secondary))
    }
}

/// "דיברת N%" (my share of the talk) with an ear while CallCoach records us both; "מקליט רק אותם" in a capsule of its
/// own color while it does not record me. A click switches it. It names the recording, not the meeting app, so it
/// never says muted and never shows a crossed microphone.
struct HearingChip: View {
    let hearing: Bool
    let share: Double
    var toggle: () -> Void = {}
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.panelScale) private var scale

    /// Not red or orange, which tell of trouble: not hearing me is a choice.
    static let offColor = Color.indigo

    static func shareText(_ share: Double) -> String {
        L("דיברת \(percent(share))%", "You spoke \(percent(share))%")
    }

    static func percent(_ share: Double) -> Int { Int((max(0, min(1, share)) * 100).rounded()) }

    static var onText: String { L("מקליט את שניכם", "Recording you both") }
    static var offText: String { L("מקליט רק אותם", "Recording only them") }

    var body: some View {
        Button(action: toggle) {
            ViewThatFits(in: .horizontal) {
                chip(full: true)
                chip(full: false)
            }
        }
        .buttonStyle(PressDim())
        .accessibilityLabel(L("הקלטת הקול שלך", "Recording your voice"))
        .accessibilityValue(hearing ? Self.onText + ", " + Self.shareText(share) : Self.offText)
        .accessibilityHint(L("לחיצה מפסיקה או מחזירה את ההקלטה של הקול שלך", "A click stops or brings back recording your voice"))
    }

    /// The whole words, or, when the header is short of room, the number alone or the capsule with the ear alone: the
    /// chip gives way before any header text breaks into a second line.
    @ViewBuilder
    private func chip(full: Bool) -> some View {
        if hearing {
            HStack(spacing: 3 * scale) {
                Image(systemName: "ear")
                    .font(.system(size: 11 * scale, weight: .medium))
                    .foregroundStyle(.secondary)
                    .frame(width: 12 * scale)
                Text(full ? Self.shareText(share) : "\(Self.percent(share))%").monospacedDigit()
            }
            .fixedSize()
            .contentShape(Rectangle())
        } else {
            HStack(spacing: 3 * scale) {
                Image(systemName: "ear").font(.system(size: 11 * scale, weight: .medium))
                if full { Text(Self.offText).fontWeight(.medium) }
            }
            .fixedSize()
            .foregroundStyle(Self.offColor)
            .padding(.horizontal, 7 * scale)
            .background(Capsule().fill(Self.offColor.opacity(0.16)).padding(.vertical, -2 * scale))
            .contentShape(Capsule())
        }
    }
}

/// "מושהה" in a capsule while the recording is paused, in place of the hearing switch: neither side is heard. A
/// click goes on.
struct PausedChip: View {
    var toggle: () -> Void = {}
    @Environment(\.panelScale) private var scale

    /// Not red or orange, which tell of trouble: a pause is a choice.
    static let color = Color.indigo
    static var text: String { L("ההקלטה מושהית", "Recording paused") }

    var body: some View {
        Button(action: toggle) {
            ViewThatFits(in: .horizontal) {
                label(full: true)
                label(full: false)
            }
        }
        .buttonStyle(PressDim())
        .accessibilityLabel(Self.text)
        .accessibilityHint(L("לחיצה ממשיכה להקליט", "A click goes on recording"))
    }

    /// The words, or the pause sign alone when the header is short of room.
    private func label(full: Bool) -> some View {
        HStack(spacing: 3 * scale) {
            Image(systemName: "pause.circle").font(.system(size: 11 * scale, weight: .medium))
            if full { Text(Self.text).fontWeight(.medium) }
        }
        .fixedSize()
        .foregroundStyle(Self.color)
        .padding(.horizontal, 7 * scale)
        .background(Capsule().fill(Self.color.opacity(0.16)).padding(.vertical, -2 * scale))
        .contentShape(Capsule())
    }
}

/// A section's title row, the chip's word with its accessory at the row's end, and the section under it. It is drawn
/// only while its chip is on.
struct SectionBlock<Content: View>: View {
    let chip: Chip
    /// Beside the title, at the row's end.
    var accessory: AnyView? = nil
    @ViewBuilder var content: () -> Content
    @Environment(\.panelScale) private var scale

    var body: some View {
        VStack(alignment: .leading, spacing: 4 * scale) {
            // On a narrow panel the title's buttons go under it, at its end, rather than past the edge.
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 6 * scale) {
                    title
                    Spacer(minLength: 0)
                    if let accessory { accessory }
                }
                VStack(alignment: .trailing, spacing: 2 * scale) {
                    HStack { title; Spacer(minLength: 0) }
                    if let accessory { accessory }
                }
            }
            content()
        }
    }

    private var title: some View {
        SectionLabel(chip.title).accessibilityAddTraits(.isHeader)
    }
}

/// The panel with no call: it says so, and a call can start from it.
struct IdlePanel: View {
    @ObservedObject var state: AppState
    @Environment(\.panelScale) private var scale

    static var title: String { L("אין שיחה פעילה כרגע", "No call right now") }

    var body: some View {
        let choice = state.briefChoice()
        VStack(alignment: .leading, spacing: 10 * scale) {
            DirectedText(Self.title, size: 15, weight: .semibold)
            HStack(spacing: 14 * scale) {
                if state.starting {
                    Text(L("טוען…", "Loading…")).font(.system(size: 12 * scale)).foregroundStyle(.secondary)
                } else {
                    link(Briefs.startTitle(choice), icon: "mic.fill", color: .red) { state.startCall(brief: choice.dueBrief) }
                }
                link(L("סגור", "Close"), icon: "xmark", color: .secondary) { state.idlePanel = false }
            }
        }
    }

    private func link(_ title: String, icon: String, color: Color, press: @escaping () -> Void) -> some View {
        Button(action: press) {
            HStack(spacing: 4 * scale) {
                Image(systemName: icon).font(.system(size: 11 * scale, weight: .semibold))
                Text(TextDirection.marked(title)).font(.system(size: 12 * scale, weight: .medium)).lineLimit(1)
            }
            .foregroundStyle(color)
            .padding(.vertical, 2 * scale)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressDim())
    }
}

/// "שאלה עכשיו" at the end of the ask title row: a press asks at once for a question to ask. Until it comes the
/// row says it is looking.
struct AskNowButton: View {
    let asking: Bool
    var press: () -> Void = {}
    @Environment(\.panelScale) private var scale

    static var lookingText: String { L("מחפש שאלה…", "Finding a question…") }

    var body: some View {
        if asking {
            Text(Self.lookingText).font(.system(size: 11 * scale)).foregroundStyle(.secondary)
                .padding(.vertical, 2 * scale)
        } else {
            Button(action: press) {
                HStack(spacing: 3 * scale) {
                    Image(systemName: "questionmark.bubble").font(.system(size: 10 * scale, weight: .semibold))
                    Text(Actions.ask.label).font(.system(size: 11 * scale, weight: .medium))
                }
                .foregroundStyle(Color.accentColor)
                .padding(.vertical, 2 * scale)
                .contentShape(Rectangle())
            }
            .buttonStyle(PressDim())
        }
    }
}

/// "בכיוון" over "לא בכיוון" at the end of a question, one above the other so a narrow panel keeps room for the text.
struct DirectionButtons: View {
    var onTrack: () -> Void = {}
    var notThisWay: () -> Void = {}
    @Environment(\.panelScale) private var scale

    var body: some View {
        VStack(alignment: .trailing, spacing: 0) {
            OnTrackButton(press: onTrack)
            NotThisWayButton(press: notThisWay)
        }
    }
}

/// "בכיוון" beside a live question: it stays, and the coach is told to bring more like it.
struct OnTrackButton: View {
    var press: () -> Void = {}
    @Environment(\.panelScale) private var scale

    static var label: String { L("בכיוון", "On track") }

    var body: some View {
        Button(action: press) {
            HStack(spacing: 3 * scale) {
                Image(systemName: "hand.thumbsup").font(.system(size: 9 * scale, weight: .semibold))
                Text(Self.label).font(.system(size: 10 * scale)).fixedSize()
            }
            .foregroundStyle(.tertiary)
            .padding(.vertical, 2 * scale)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressDim())
        .help(L("עוד שאלות בכיוון הזה", "More questions in this direction"))
    }
}

/// "לא בכיוון" beside a live question: the question goes, and the coach is told to look in another direction.
struct NotThisWayButton: View {
    var press: () -> Void = {}
    @Environment(\.panelScale) private var scale

    static var label: String { L("לא בכיוון", "Not this way") }

    var body: some View {
        Button(action: press) {
            HStack(spacing: 3 * scale) {
                Image(systemName: "hand.thumbsdown").font(.system(size: 9 * scale, weight: .semibold))
                Text(Self.label).font(.system(size: 10 * scale)).fixedSize()
            }
            .foregroundStyle(.tertiary)
            .padding(.vertical, 2 * scale)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressDim())
    }
}

/// "מה המושג שנאמר עכשיו?" at the end of the terms title row: a press looks at once at what was just said. While it
/// looks, and for a moment after it found nothing or failed, the row says so instead.
struct TermsNowButton: View {
    let spot: SpotState?
    var press: () -> Void = {}
    @Environment(\.panelScale) private var scale

    static func text(_ spot: SpotState?) -> String {
        switch spot {
        case .searching: return L("מחפש…", "Looking…")
        case .nothing: return L("לא נמצא מושג", "No term found")
        case .failed: return L("החיפוש לא הצליח", "The search failed")
        case nil: return Actions.termsNow.label
        }
    }

    var body: some View {
        if spot == nil {
            Button(action: press) {
                HStack(spacing: 3 * scale) {
                    Image(systemName: "text.magnifyingglass").font(.system(size: 10 * scale, weight: .semibold))
                    Text(Self.text(nil)).font(.system(size: 11 * scale, weight: .medium))
                }
                .foregroundStyle(Color.accentColor)
                .padding(.vertical, 2 * scale)
                .contentShape(Rectangle())
            }
            .buttonStyle(PressDim())
        } else {
            Text(Self.text(spot)).font(.system(size: 11 * scale))
                .foregroundStyle(spot == .failed ? AnyShapeStyle(Color.orange) : AnyShapeStyle(.secondary))
                .padding(.vertical, 2 * scale)
        }
    }
}

/// A small on and off switch on a section's title row: a check in a thin capsule, tinted while on.
struct SmallSwitch: View {
    let title: String
    let on: Bool
    var hint = ""
    var toggle: () -> Void = {}
    @Environment(\.panelScale) private var scale

    var body: some View {
        Button(action: toggle) {
            Label(title, systemImage: on ? "checkmark.circle.fill" : "circle")
                .labelStyle(.titleAndIcon)
                .font(.system(size: 10 * scale, weight: on ? .semibold : .regular))
                .foregroundStyle(on ? Color.accentColor : Color.secondary)
                .padding(.vertical, 2 * scale)
                .padding(.horizontal, 6 * scale)
                .overlay(Capsule().strokeBorder(on ? Color.accentColor.opacity(0.6) : Color.secondary.opacity(0.35)))
                .contentShape(Capsule())
                .fixedSize()
        }
        .buttonStyle(PressDim())
        .help(hint)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

/// "רק מהרשימה": the terms from the knowledge files and the brief only, with no tokens for them.
enum ListOnly {
    static var title: String { L("רק מהרשימה", "List only") }
    static var hint: String {
        L("מושגים רק מקבצי הידע ומהתדריך, בלי לשאול את הסוכן. חוסך טוקנים, אבל שמות שאין בקבצים לא יופיעו. \"מה המושג שנאמר עכשיו?\" עדיין שואל אותו.",
          "Terms only from the knowledge files and the brief, without asking the agent. Saves tokens, but names not in the files will not show. \"What term was just said?\" still asks it.")
    }
    static var line: String {
        L("רק מהרשימה: חוסך טוקנים, ושמות שאין בקבצים לא יופיעו.", "List only: saves tokens, and names not in the files will not show.")
    }
}

/// "סיכום ביניים": the title with its round arrow, which asks for what was said since, and under it the recap.
struct RecapSection: View {
    var recap: RecapState? = nil
    var parts: [RecapPart] = []
    var update: RecapUpdate? = nil
    var canUpdate = false
    /// The line on what is discussed now, on top of the recap, and its switch on the title row.
    var nowOn = true
    var nowLine: String? = nil
    var retry: () -> Void = {}
    var refresh: () -> Void = {}
    var toggleNow: () -> Void = {}
    @Environment(\.panelScale) private var scale
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    static var nowTitle: String { L("עכשיו", "Now") }
    static var nowWaitingText: String { L("מחכה לשיחה", "Waiting for the call") }
    static var nowHint: String {
        L("שורה על מה שמדברים עליו עכשיו. הסוכן החי כותב אותה, וכשהיא כבויה לא מבקשים אותה.",
          "A line on what is being discussed now. The live agent writes it; while it is off it is not asked for.")
    }

    private var busy: Bool { recap == .asking || update == .asking }

    var body: some View {
        VStack(alignment: .leading, spacing: 4 * scale) {
            HStack(spacing: 6 * scale) {
                Text(Chip.recap.title).font(.system(size: 11 * scale, weight: .semibold)).foregroundStyle(.secondary)
                if canUpdate {
                    Button(action: refresh) {
                        Image(systemName: "arrow.clockwise").font(.system(size: 10 * scale, weight: .semibold))
                            .foregroundStyle(busy ? AnyShapeStyle(.tertiary) : AnyShapeStyle(Color.accentColor))
                            .frame(width: 18 * scale, height: 18 * scale)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(PressDim())
                    .disabled(busy)
                    .accessibilityLabel(L("עדכן", "Update"))
                }
                Spacer(minLength: 0)
                if update == .nothingNew {
                    Text(RecapView.nothingNewText).font(.system(size: 11 * scale)).foregroundStyle(.secondary)
                } else if update == .failed {
                    Text(RecapView.updateFailedText).font(.system(size: 11 * scale)).foregroundStyle(Color.orange)
                }
                nowSwitch
            }
            .modifier(GrowOnly())
            if nowOn {
                nowRow.modifier(GrowOnly())
            }
            if recap == .empty {
                Text(RecapView.emptyText).font(.system(size: 13 * scale)).foregroundStyle(.secondary)
                    .modifier(GrowOnly())
            } else {
                RecapView(recap: recap, parts: parts, update: update, retry: retry)
                    .modifier(GrowOnly())
            }
        }
    }

    /// A small switch, so the line can be turned off without closing the recap; it is remembered for the next call.
    private var nowSwitch: some View {
        SmallSwitch(title: Self.nowTitle, on: nowOn, hint: Self.nowHint, toggle: toggleNow)
    }

    /// The line itself, two lines at most; the old and the new one cross-fade.
    private var nowRow: some View {
        ZStack(alignment: .topLeading) {
            if let nowLine {
                DirectedText(nowLine, size: 14, weight: .medium)
                    .foregroundStyle(Color.primary.opacity(0.85))
                    .lineLimit(2)
                    .truncationMode(.tail)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .id(nowLine)
                    .transition(.opacity)
            } else {
                DirectedText(Self.nowWaitingText, size: 13).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .animation(reduceMotion ? nil : .easeOut(duration: 0.3), value: nowLine)
        .accessibilityLabel(nowLine.map { Self.nowTitle + ": " + $0 } ?? Self.nowWaitingText)
    }
}

/// The recap: "טוען…" while it is asked, then a bullet for each line, or the failure a click tries again. Each
/// refresh adds what was said since, under "מאז" and the time of the call.
struct RecapView: View {
    let recap: RecapState?
    var parts: [RecapPart] = []
    var update: RecapUpdate? = nil
    var retry: () -> Void = {}
    @Environment(\.panelScale) private var scale

    static var failedText: String { L("לא הצלחתי לסכם", "Could not sum it up") }
    static var emptyText: String { L("עוד אין מה לסכם", "Nothing to recap yet") }
    static var nothingNewText: String { L("אין חדש", "Nothing new") }
    static var updateFailedText: String { L("לא הצלחתי לעדכן", "Could not update") }

    static func sinceText(_ since: String) -> String { L("מאז \(since)", "Since \(since)") }

    var body: some View {
        VStack(alignment: .leading, spacing: 3 * scale) {
            switch recap {
            case .ready(let lines)?:
                bullets(lines)
                ForEach(Array(parts.suffix(RecapPart.shown).enumerated()), id: \.offset) { _, part in
                    Text(Self.sinceText(part.since)).font(.system(size: 11 * scale, weight: .medium)).foregroundStyle(.secondary)
                        .monospacedDigit()
                        .padding(.top, 3 * scale)
                    bullets(part.lines)
                }
            case .failed?:
                Button(action: retry) {
                    DirectedText(Self.failedText, size: 13).foregroundStyle(Color.orange).contentShape(Rectangle())
                }
                .buttonStyle(PressDim())
                .accessibilityHint(L("לחיצה מנסה שוב", "A click tries again"))
            default:
                Text(loadingText).font(.system(size: 13 * scale)).foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 8 * scale)
        .padding(.vertical, 6 * scale)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 8 * scale))
    }

    private func bullets(_ lines: [String]) -> some View {
        ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
            HStack(alignment: .firstTextBaseline, spacing: 5 * scale) {
                Text("•").font(.system(size: 13 * scale)).foregroundStyle(.secondary)
                DirectedText(line, size: 13).fixedSize(horizontal: false, vertical: true)
            }
            .environment(\.layoutDirection, TextDirection.isRTL(line) ? .rightToLeft : .leftToRight)
        }
    }
}

/// "לשאול": the slot (the question asked of me, else the card), the live questions and the goals line. The slot and
/// the questions keep the tallest height they had, so new text never moves what is under them.
struct AskSection: View {
    @ObservedObject var state: AppState
    @Environment(\.panelScale) private var scale

    var body: some View {
        let m = state.model
        VStack(alignment: .leading, spacing: 8 * scale) {
            NudgeSlot(card: m.cards.current, phase: m.phase, asked: m.shownAsked,
                      notThisWay: m.phase == .active ? { state.notThisWay(card: $0) } : nil,
                      onTrack: { state.onTrack($0.text) })
                .modifier(GrowOnly())
            if m.liveQuestions {
                QuestionsView(questions: m.questions,
                              notThisWay: m.phase == .active ? { state.notThisWay($0) } : nil,
                              onTrack: { state.onTrack($0.text) })
                    .modifier(GrowOnly())
            }
            if !m.goals.isEmpty {
                GoalsView(goals: m.goals, title: m.goalCount, open: m.goalsOpen, focus: m.focusGoal,
                          active: m.phase == .active, toggle: { state.toggleGoals() },
                          check: { state.toggleGoal($0) }, press: { state.focus($0) })
            }
        }
    }
}

/// "הם שאלו אותך": the question as it was said and, when it is not Hebrew, its translation under it, both in their
/// own direction on the panel's reading side, under the label.
struct AskedView: View {
    let asked: Asked
    @Environment(\.layoutDirection) private var direction
    @Environment(\.panelScale) private var scale

    var body: some View {
        VStack(alignment: .leading, spacing: 2 * scale) {
            SectionLabel(L("הם שאלו אותך", "They asked you"), tint: .accentColor)
            DirectedText(asked.text, size: 16, weight: .medium, side: direction).lineLimit(3)
            if !asked.he.isEmpty {
                DirectedText(asked.he, size: 12, side: direction).foregroundStyle(.secondary).lineLimit(2)
            }
        }
        .padding(.horizontal, 8 * scale)
        .padding(.vertical, 6 * scale)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.accentColor.opacity(0.10), in: RoundedRectangle(cornerRadius: 8 * scale))
    }
}

/// Up to two questions to ask next, each in the call's language with its Hebrew translation under it when it is not
/// Hebrew, both in their own direction on the panel's reading side. The old and the new questions overlap while they
/// fade, so the block never holds both at once.
struct QuestionsView: View {
    let questions: [Question]
    /// "לא בכיוון" at the end of each question, while the call runs.
    var notThisWay: ((Question) -> Void)? = nil
    /// "בכיוון" beside it.
    var onTrack: ((Question) -> Void)? = nil
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.layoutDirection) private var direction
    @Environment(\.panelScale) private var scale

    var body: some View {
        ZStack(alignment: .topLeading) { list.id(questions).transition(.opacity) }
            .frame(minHeight: 48 * scale, alignment: .topLeading)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.3), value: questions)
    }

    private var list: some View {
        VStack(alignment: .leading, spacing: 6 * scale) {
            if questions.isEmpty {
                DirectedText(L("שאלות יופיעו כשהצד השני ידבר", "Questions show up once the other side talks"),
                             size: 13).foregroundStyle(.tertiary)
            } else {
                ForEach(Array(questions.enumerated()), id: \.offset) { index, question in
                    HStack(alignment: .firstTextBaseline, spacing: 6 * scale) {
                        VStack(alignment: .leading, spacing: 1 * scale) {
                            DirectedText(question.text, size: 16, weight: .medium, side: direction)
                                .foregroundStyle(index == 0 ? .primary : .secondary)
                                .lineLimit(2)
                            if !question.he.isEmpty {
                                DirectedText(question.he, size: 12, side: direction).foregroundStyle(.secondary).lineLimit(2)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        if let notThisWay {
                            DirectionButtons(onTrack: { onTrack?(question) }, notThisWay: { notThisWay(question) })
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
    }
}

/// Every name and term of the call in the order they came, so a row never moves. Four rows show, more without the
/// transcript rows, and more in a taller panel. The list scrolls to each new one, which is tinted for a few seconds,
/// unless a row is open. A click opens a row; the chevron at its end says so. With rows above the ones shown, its top edge fades out, so a cut row reads as more above.
struct TermsList: View {
    let terms: [Term]
    let freshId: Int?
    var openId: Int? = nil
    var details: [Int: TermDetailState] = [:]
    var mores: [Int: TermMoreState] = [:]
    var canAskMore: (Int) -> Bool = { _ in false }
    var adds: [Int: KnowledgeAdd] = [:]
    var least = TermsList.height
    var extra: CGFloat = 0
    var room = CGFloat.infinity
    var toggle: (Int) -> Void = { _ in }
    var add: ((Int) -> Void)? = nil
    var ignore: ((Int) -> Void)? = nil
    var askMore: ((Int) -> Void)? = nil
    @Environment(\.panelScale) private var scale
    @ViewState private var above = false
    /// The terms that came while a row was open and the list stayed where it was, shown on the round arrow.
    @ViewState private var unseen = 0
    /// The list's own height, under the section's title row.
    static let height: CGFloat = 106

    static let openMost: CGFloat = 320
    static let fade: CGFloat = 18

    /// Without the transcript rows the list gets their room too.
    static func leastHeight(transcript: Bool) -> CGFloat {
        transcript ? height : height + TranscriptRows.height + PanelView.spacing
    }

    /// However short the panel is set, the list keeps room for about two rows.
    static let leastShown: CGFloat = 52

    /// How much a short panel may take off the list's own height.
    static func shrink(transcript: Bool) -> CGFloat {
        leastHeight(transcript: transcript) - leastShown
    }

    var body: some View {
        ReservedHeight(least: least * scale, room: room, extra: extra) {
            list
            if let open = terms.first(where: { $0.id == openId }) {
                TermRow(term: open, fresh: false, open: true, detail: details[open.id], more: mores[open.id],
                        added: adds[open.id], add: add.map { add in { add(open.id) } },
                        ignore: ignore.map { ignore in { ignore(open.id) } }, askMore: moreAction(open.id))
                    .frame(maxHeight: Self.openMost * scale, alignment: .top)
                    .hidden()
            }
        }
        .animation(.easeOut(duration: 0.4), value: freshId)
    }

    /// "עוד מהמודל" for a row, only while it may be asked.
    private func moreAction(_ id: Int) -> (() -> Void)? {
        guard let askMore, canAskMore(id) else { return nil }
        return { askMore(id) }
    }

    /// A new term always scrolls the list to its end, also when I scrolled up to read, so it shows; only an open row
    /// holds the list where it is, so what I read does not move away.
    static func follows(openId: Int?) -> Bool { openId == nil }

    /// The count on the round arrow: new terms add to it while a row holds the list, and it is gone once the list
    /// follows again.
    static func unseen(_ count: Int, adding: Int, openId: Int?) -> Int {
        follows(openId: openId) ? 0 : count + max(0, adding)
    }

    /// The arrow's number stays two digits wide.
    static func badgeText(_ count: Int) -> String { count > 99 ? "99+" : "\(count)" }

    private var list: some View {
        VStack(alignment: .leading, spacing: 4 * scale) {
            if terms.isEmpty {
                DirectedText(L("שמות, חברות ומושגים מהשיחה יופיעו כאן", "Names, companies and terms from the call show up here"),
                             size: 13).foregroundStyle(.secondary)
                Spacer(minLength: 0)
            } else {
                ScrollViewReader { proxy in
                    ScrollView(.vertical, showsIndicators: false) {
                        VStack(alignment: .leading, spacing: 2 * scale) {
                            ForEach(terms) { term in
                                TermRow(term: term, fresh: term.id == freshId, open: term.id == openId,
                                        detail: details[term.id], more: mores[term.id], added: adds[term.id],
                                        add: add.map { add in { add(term.id) } },
                                        ignore: ignore.map { ignore in { ignore(term.id) } },
                                        askMore: moreAction(term.id))
                                    .id(term.id)
                                    .onTapGesture { toggle(term.id) }
                            }
                        }
                    }
                    .modifier(ScrolledBelowTop(above: $above, reachedEnd: { unseen = 0 }))
                    .mask {
                        VStack(spacing: 0) {
                            LinearGradient(colors: [.clear, .black], startPoint: .top, endPoint: .bottom)
                                .frame(height: above ? Self.fade * scale : 0)
                            Color.black
                        }
                    }
                    .overlay(alignment: .bottom) {
                        if unseen > 0 {
                            NewTermsBadge(count: unseen) {
                                unseen = 0
                                withAnimation(.easeOut(duration: 0.3)) { proxy.scrollTo(terms.last?.id, anchor: .bottom) }
                            }
                            .padding(4 * scale)
                            .transition(.scale.combined(with: .opacity))
                        }
                    }
                    .animation(.easeOut(duration: 0.2), value: unseen > 0)
                    .onAppear { proxy.scrollTo(openId ?? terms.last?.id, anchor: openId == nil ? .bottom : .top) }
                    .onChange(of: terms.last?.id) { old, id in
                        let added = terms.filter { $0.id > (old ?? Int.min) }.count
                        unseen = Self.unseen(unseen, adding: added, openId: openId)
                        guard Self.follows(openId: openId) else { return }
                        withAnimation(.easeOut(duration: 0.3)) { proxy.scrollTo(id, anchor: .bottom) }
                    }
                    .onChange(of: freshId) { _, id in
                        guard Self.follows(openId: openId), let id, id != terms.last?.id else { return }
                        withAnimation(.easeOut(duration: 0.3)) { proxy.scrollTo(id, anchor: .center) }
                    }
                    .onChange(of: openId) { _, id in
                        if id == nil { unseen = 0 }
                        withAnimation(.easeOut(duration: 0.3)) {
                            proxy.scrollTo(id ?? terms.last?.id, anchor: id == nil ? .bottom : .top)
                        }
                    }

                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

/// The round arrow at the bottom edge of the terms list while a row is open and new terms came under it, like a chat
/// that got messages while I read above: the count of them, and a click scrolls down to the newest.
struct NewTermsBadge: View {
    let count: Int
    let press: () -> Void
    @Environment(\.panelScale) private var scale

    static func label(_ count: Int) -> String {
        L("\(count) מושגים חדשים למטה", "\(count) new terms below")
    }

    var body: some View {
        Button(action: press) {
            Image(systemName: "chevron.down")
                .font(.system(size: 12 * scale, weight: .bold))
                .foregroundStyle(Color.accentColor)
                .frame(width: 28 * scale, height: 28 * scale)
                .background(.thickMaterial, in: Circle())
                .overlay(Circle().strokeBorder(Color.primary.opacity(0.12)))
                .shadow(color: .black.opacity(0.18), radius: 3 * scale, y: 1 * scale)
                .overlay(alignment: .top) {
                    Text(TermsList.badgeText(count))
                        .font(.system(size: 10 * scale, weight: .bold).monospacedDigit())
                        .foregroundStyle(.white)
                        .padding(.horizontal, 4 * scale)
                        .frame(minWidth: 16 * scale, minHeight: 16 * scale)
                        .background(Color.accentColor, in: Capsule())
                        .offset(y: -9 * scale)
                        .fixedSize()
                }
                .contentShape(Circle())
        }
        .buttonStyle(PressDim())
        .help(Self.label(count))
        .accessibilityLabel(Self.label(count))
    }
}

/// Whether a scroll view has rows scrolled away above, and whether it is at its end. macOS 14 does not tell a scroll
/// view's offset, so there both stay as they are: no fade, and only the arrow or closing the row clears the count.
private struct ScrolledBelowTop: ViewModifier {
    @Binding var above: Bool
    var reachedEnd: () -> Void = {}
    /// Within this of the end still counts as at the end, so a row a hair taller still clears the count.
    static let endSlack: CGFloat = 12

    func body(content: Content) -> some View {
        if #available(macOS 15.0, *) {
            content.onScrollGeometryChange(for: Bool.self) { $0.contentOffset.y + $0.contentInsets.top > 0.5 } action: { _, value in
                above = value
            }
            // Each time a scroll comes to rest at the end, also when it was there before, the new terms' count goes.
            .onScrollPhaseChange { _, phase, context in
                guard phase == .idle else { return }
                let g = context.geometry
                if g.contentOffset.y + g.containerSize.height >= g.contentSize.height - Self.endSlack { reachedEnd() }
            }
        } else {
            content
        }
    }
}

/// Its first view at its own height, taller by up to `room` when a second, hidden view needs more, and then `extra`
/// taller; so an open row shows whole as far as the screen has room, and the panel grows for it.
struct ReservedHeight: Layout {
    let least: CGFloat
    var room = CGFloat.infinity
    var extra: CGFloat = 0

    static func height(least: CGFloat, need: CGFloat, room: CGFloat, extra: CGFloat) -> CGFloat {
        max(least, min(need, least + room)) + extra
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? subviews.first?.sizeThatFits(.unspecified).width ?? 0
        let need = subviews.dropFirst().map { $0.sizeThatFits(ProposedViewSize(width: width, height: nil)).height }.max()
        return CGSize(width: width, height: Self.height(least: least, need: need ?? 0, room: room, extra: extra))
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        for view in subviews { view.place(at: bounds.origin, proposal: ProposedViewSize(bounds.size)) }
    }
}

/// A row opens on a click: a known name shows the whole summary, the details from its file and every line we have,
/// newest first; a model term shows the engine's few sentences on it.
struct TermRow: View {
    let term: Term
    let fresh: Bool
    var open = false
    var detail: TermDetailState? = nil
    /// "עוד מהמודל" on a known name.
    var more: TermMoreState? = nil
    var added: KnowledgeAdd? = nil
    var add: (() -> Void)? = nil
    /// "אני מכיר, לא להקפיץ", on an open row of either kind.
    var ignore: (() -> Void)? = nil
    /// "עוד מהמודל", while the model may be asked about a known name.
    var askMore: (() -> Void)? = nil
    @Environment(\.panelScale) private var scale
    @Environment(\.openURL) private var openURL
    static let size: CGFloat = 15
    /// The note after a term and the text under an open row: softer than the term, but darker than the system's
    /// secondary gray, which is hard to read over a light window.
    static let noteColor = Color.primary.opacity(0.74)

    static func addedText(_ added: KnowledgeAdd) -> String {
        switch added {
        case .added: return L("נוסף לידע", "Added to the knowledge")
        case .known: return L("כבר בקובץ הידע", "Already in a knowledge file")
        case .failed: return L("לא הצלחתי להוסיף", "Could not add it")
        }
    }

    /// "הוסף לידע", or what came of it.
    @ViewBuilder private var addRow: some View {
        if let added {
            Text(Self.addedText(added)).font(.system(size: 12 * scale, weight: .medium))
                .foregroundStyle(added == .failed ? AnyShapeStyle(Color.orange) : AnyShapeStyle(Self.noteColor))
        } else if let add {
            Button(action: add) {
                HStack(spacing: 4 * scale) {
                    Image(systemName: "plus.circle").font(.system(size: 11 * scale, weight: .semibold))
                    Text(L("הוסף לידע", "Add to the knowledge")).font(.system(size: 12 * scale, weight: .medium))
                }
                .foregroundStyle(Color.accentColor)
                .padding(.vertical, 2 * scale)
                .contentShape(Rectangle())
            }
            .buttonStyle(PressDim())
        }
    }

    static var ignoreText: String { L("אני מכיר, לא להקפיץ", "I know it, don't show") }
    static var moreText: String { L("עוד מהמודל", "More from the model") }

    /// Under a known name's details: "טוען…" while the model is asked, the failure, or nothing.
    static func moreNote(_ more: TermMoreState?) -> String? {
        switch more {
        case .asking?: return loadingText
        case .failed?: return L("לא הצלחתי להרחיב", "Could not explain it")
        default: return nil
        }
    }

    /// A known name's details from its file, and under them what "עוד מהמודל" brought, or its button.
    @ViewBuilder private var knownDetail: some View {
        if case .ready(let text)? = detail {
            DirectedText(text, size: 13).fixedSize(horizontal: false, vertical: true)
        } else if detail == .asking {
            Text(loadingText).font(.system(size: 13 * scale)).frame(maxWidth: .infinity, alignment: .leading)
        }
        if let note = Self.moreNote(more) {
            Text(note).font(.system(size: 12 * scale))
                .foregroundStyle(more == .failed ? AnyShapeStyle(Color.orange) : AnyShapeStyle(Self.noteColor))
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        if let askMore, more != .asking {
            Button(action: askMore) {
                HStack(spacing: 4 * scale) {
                    Image(systemName: "sparkles").font(.system(size: 10 * scale, weight: .semibold))
                    Text(Self.moreText).font(.system(size: 12 * scale, weight: .medium))
                }
                .foregroundStyle(Color.accentColor)
                .padding(.vertical, 2 * scale)
                .contentShape(Rectangle())
            }
            .buttonStyle(PressDim())
            .accessibilityHint(L("שואל את המודל על השם, והתשובה תתווסף מתחת לפרטים", "Asks the model about the name; the answer is added under the details"))
        }
    }

    @ViewBuilder private var ignoreRow: some View {
        if let ignore {
            Button(action: ignore) {
                HStack(spacing: 4 * scale) {
                    Image(systemName: "eye.slash").font(.system(size: 10 * scale, weight: .semibold))
                    Text(Self.ignoreText).font(.system(size: 12 * scale, weight: .medium))
                }
                .foregroundStyle(Self.noteColor)
                .padding(.vertical, 2 * scale)
                .contentShape(Rectangle())
            }
            .buttonStyle(PressDim())
            .accessibilityHint(L("השם לא יוקפץ יותר. אפשר להחזיר בהגדרות", "The name won't show again. Bring it back in Settings"))
        }
    }

    static func line(_ term: Term, scale: CGFloat = 1) -> AttributedString {
        let mark = TextDirection.isRTL(term.note) ? "\u{200F}" : "\u{200E}"
        var head = AttributedString(mark + term.term)
        head.font = .system(size: size * scale, weight: .semibold)
        var tail = AttributedString(": " + term.note)
        tail.font = .system(size: size * scale)
        tail.foregroundColor = noteColor
        return head + tail
    }

    /// Forward while closed, down while open; forward points to the row's end in either direction.
    static func chevron(open: Bool) -> String {
        open ? "chevron.down" : "chevron.forward"
    }

    /// "טוען…" while it is asked for, then the sentences, or the failure a click tries again.
    static func detailText(_ detail: TermDetailState?) -> String {
        switch detail {
        case .ready(let text)?: return text
        case .failed?: return L("לא הצלחתי להרחיב", "Could not explain it")
        default: return loadingText
        }
    }

    /// The newest thing we already know about a known name; the count comes first, so a cut line keeps it.
    static func ours(_ term: Term) -> String? {
        guard let ours = term.ours, let newest = ours.first else { return nil }
        let label = ours.count > 1 ? L("שלנו (\(ours.count)) · ", "Ours (\(ours.count)) · ") : L("שלנו · ", "Ours · ")
        return label + newest
    }

    /// The source of a שלנו line, when it is a web link; anything else never opens.
    static func link(_ term: Term, _ index: Int) -> URL? {
        guard let links = term.oursLinks, links.indices.contains(index), let url = URL(string: links[index]),
              let scheme = url.scheme?.lowercased(), scheme == "https" || scheme == "http", url.host != nil else { return nil }
        return url
    }

    /// The knowledge file a known name came from.
    static func fromText(_ term: Term) -> String? {
        guard term.known == true, let file = term.file, !file.isEmpty else { return nil }
        return L("מתוך \(file)", "From \(file)")
    }

    static func oursLabel(_ term: Term) -> String? {
        guard let ours = term.ours, !ours.isEmpty else { return nil }
        return ours.count > 1 ? L("שלנו (\(ours.count))", "Ours (\(ours.count))") : L("שלנו", "Ours")
    }

    /// The sentences in their own direction; "טוען…" and the failure on the row's side, under the term.
    @ViewBuilder private var detailView: some View {
        if case .ready(let text)? = detail {
            DirectedText(text, size: 13)
        } else {
            Text(Self.detailText(detail)).font(.system(size: 13 * scale)).frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// A שלנו line; one whose source is a web link opens it in the browser on a click, with a symbol saying so.
    @ViewBuilder private func oursRow(_ line: String, link: URL?) -> some View {
        let text = Text(TextDirection.marked(line))
            .font(.system(size: 12 * scale, weight: .medium))
            .fixedSize(horizontal: false, vertical: true)
        if let link {
            Button { openURL(link) } label: {
                HStack(alignment: .firstTextBaseline, spacing: 4 * scale) {
                    text
                    Image(systemName: "arrow.up.forward.square").font(.system(size: 10 * scale, weight: .semibold))
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(PressDim())
            .accessibilityHint(L("פותח את המקור בדפדפן", "Opens the source in the browser"))
        } else {
            text.frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: (open ? 3 : 1) * scale) {
            HStack(alignment: .firstTextBaseline, spacing: 6 * scale) {
                Image(systemName: termSymbol(term))
                    .font(.system(size: 13 * scale))
                    .foregroundStyle(term.known == true ? Color.accentColor : Self.noteColor)
                    .frame(width: 16 * scale)
                Text(Self.line(term, scale: scale))
                    .lineLimit(open ? nil : 2)
                    .fixedSize(horizontal: false, vertical: open)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: Self.chevron(open: open))
                    .font(.system(size: 9 * scale, weight: .semibold))
                    .foregroundStyle(.secondary)
                    .frame(width: 10 * scale)
            }
            if open && term.known == true && term.detail == true {
                knownDetail.padding(.leading, 22 * scale)
            }
            if open && term.known != true {
                detailView
                    .foregroundStyle(detail == .failed ? Color.orange : .primary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, 22 * scale)
                HStack(spacing: 14 * scale) {
                    addRow
                    ignoreRow
                }
                .padding(.leading, 22 * scale)
            } else if open, Self.oursLabel(term) != nil || Self.fromText(term) != nil {
                VStack(alignment: .leading, spacing: 2 * scale) {
                    if let label = Self.oursLabel(term) {
                        Text(label).font(.system(size: 11 * scale, weight: .semibold))
                        ForEach(Array((term.ours ?? []).enumerated()), id: \.offset) { index, line in
                            oursRow(line, link: Self.link(term, index))
                        }
                    }
                    if let from = Self.fromText(term) {
                        Text(from).font(.system(size: 11 * scale)).foregroundStyle(Self.noteColor)
                    }
                }
                .foregroundStyle(Color.accentColor)
                .padding(.leading, 22 * scale)
            } else if let ours = Self.ours(term) {
                Text(TextDirection.marked(ours))
                    .font(.system(size: 12 * scale, weight: .medium))
                    .foregroundStyle(Color.accentColor)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.leading, 22 * scale)
            }
            if open && term.known == true { ignoreRow.padding(.leading, 22 * scale) }
        }
        .environment(\.layoutDirection, TextDirection.isRTL(term.note) ? .rightToLeft : .leftToRight)
        .padding(.horizontal, 6 * scale)
        .padding(.vertical, (open ? 4 : 2) * scale)
        .background(fresh ? Color.accentColor.opacity(0.16) : open ? Color.primary.opacity(0.06) : .clear,
                    in: RoundedRectangle(cornerRadius: 6 * scale))
        .contentShape(RoundedRectangle(cornerRadius: 6 * scale))
    }
}

/// The last lines of the transcript (two while an AI chip is on, more while none is), so it is plain that both sides
/// are heard, and under them what the other side is saying now. A line cut to one row shows its end. They show while
/// the transcript chip is on. The speaker stays on the panel's reading side and each line, and its translation, sits
/// next to it in its own direction, so English and Hebrew rows read straight down.
struct TranscriptRows: View {
    static let height: CGFloat = 52
    let lines: [TranscriptLine]
    var partial: Partial? = nil
    /// The Hebrew under the other side's English lines, by line id.
    var translations: [Int: String] = [:]
    /// Whole lines while no AI chip is on; otherwise each line is cut to one row.
    var wrap = false
    @Environment(\.layoutDirection) private var direction
    @Environment(\.panelScale) private var scale

    static func speaker(_ channel: String) -> String {
        channel == "me" ? L("אני:", "Me:") : L("הם:", "Them:")
    }

    static func live(_ text: String) -> String {
        TextDirection.marked("…" + text)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2 * scale) {
            ForEach(lines, id: \.id) { l in
                row(Self.speaker(l.channel), l.text, he: translations[l.id])
            }
            if let partial {
                row(Self.speaker(partial.channel), Self.live(partial.text)).foregroundStyle(.secondary)
            }
        }
        .foregroundStyle(.secondary)
        .frame(minHeight: Self.height * scale, alignment: .topLeading)
        .modifier(GrowOnly())
    }

    private func row(_ speaker: String, _ text: String, he: String? = nil) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 4 * scale) {
            Text(speaker).font(.system(size: 12 * scale, weight: .semibold)).fixedSize()
            VStack(alignment: .leading, spacing: 1 * scale) {
                rowText(text, size: wrap ? 13 : 12)
                if let he { rowText(he, size: wrap ? 12 : 11).foregroundStyle(.secondary) }
            }
        }
    }

    private func rowText(_ text: String, size: CGFloat) -> some View {
        DirectedText(text, size: size, side: direction)
            .lineLimit(wrap ? nil : 1)
            .truncationMode(.head)
            .fixedSize(horizontal: false, vertical: wrap)
    }
}

/// One line, "מטרות 1/3", that opens the list on a click. Each open goal has "שאל על זה" right after its text, and the
/// goal in focus "בפוקוס" there instead.
struct GoalsView: View {
    let goals: [Goal]
    let title: String
    var open = false
    var focus: String?
    var active = true
    var toggle: () -> Void = {}
    var check: (String) -> Void = { _ in }
    var press: (String) -> Void = { _ in }
    @Environment(\.panelScale) private var scale

    /// "שאל על זה" puts a goal in focus; on the goal in focus, "בפוקוס" ends it.
    static func focusText(focused: Bool) -> String {
        focused ? L("בפוקוס", "In focus") : L("שאל על זה", "Ask about this")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2 * scale) {
            Button(action: toggle) {
                HStack(spacing: 4 * scale) {
                    Text(title).font(.system(size: 11 * scale, weight: .semibold))
                    Image(systemName: open ? "chevron.down" : "chevron.forward").font(.system(size: 9 * scale, weight: .semibold))
                }
                .foregroundStyle(.secondary)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open {
                ForEach(goals) { goal in
                    HStack(spacing: 6 * scale) {
                        Button { check(goal.id) } label: {
                            Image(systemName: goal.done ? "checkmark.circle.fill" : "circle")
                                .font(.system(size: 12 * scale))
                                .foregroundStyle(goal.done ? Color.green : .secondary)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(PressDim())
                        .disabled(!active)
                        Text(TextDirection.marked(goal.text)).font(.system(size: 12 * scale)).lineLimit(1)
                        if !goal.done {
                            let focused = focus == goal.id
                            Button { press(goal.id) } label: {
                                HStack(spacing: 3 * scale) {
                                    Image(systemName: focused ? "questionmark.bubble.fill" : "questionmark.bubble")
                                        .font(.system(size: 9 * scale, weight: .semibold))
                                    Text(Self.focusText(focused: focused)).font(.system(size: 10 * scale, weight: .semibold))
                                }
                                .fixedSize()
                                .foregroundStyle(focused ? Color.accentColor : .secondary)
                                .padding(.leading, 2 * scale)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(PressDim())
                            .disabled(!active)
                        }
                        Spacer(minLength: 0)
                    }
                }
            }
        }
    }
}

struct NoticeView: View {
    let notice: Notice
    let tint: Color
    @Environment(\.panelScale) private var scale

    var body: some View {
        VStack(alignment: .leading, spacing: 1 * scale) {
            HStack(spacing: 8 * scale) {
                DirectedText(notice.title, size: 12, weight: .semibold).foregroundStyle(tint)
                if notice.accounts {
                    Button(L("הגדרות", "Settings")) { NSApp.sendAction(#selector(AppDelegate.openAccounts), to: nil, from: nil) }
                        .buttonStyle(PressDim())
                        .font(.system(size: 12 * scale))
                        .foregroundStyle(Color.accentColor)
                        .fixedSize()
                }
            }
            if !notice.detail.isEmpty { DirectedText(notice.detail, size: 10).foregroundStyle(.secondary).lineLimit(2) }
        }
    }
}

/// Keeps the tallest height its content had at this scale, so text that changes in place never moves what is under
/// it: a longer text makes room once, and the room stays for the rest of the call.
struct GrowOnly: ViewModifier {
    @Environment(\.panelScale) private var scale
    @ViewState private var tallest = Tallest()

    struct Tallest: Equatable {
        var scale: CGFloat = 0
        var height: CGFloat = 0
    }

    private struct HeightKey: PreferenceKey {
        static let defaultValue: CGFloat = 0
        static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
    }

    func body(content: Content) -> some View {
        content
            .background(GeometryReader { g in Color.clear.preference(key: HeightKey.self, value: g.size.height) })
            .onPreferenceChange(HeightKey.self) { height in
                if tallest.scale != scale {
                    tallest = Tallest(scale: scale, height: height)
                } else if height > tallest.height + 0.5 {
                    tallest.height = height
                }
            }
            .frame(minHeight: tallest.scale == scale ? tallest.height : nil, alignment: .topLeading)
    }
}

/// What shows while an answer is asked for, in words rather than "…", so it reads as loading.
var loadingText: String { L("טוען…", "Loading…") }
