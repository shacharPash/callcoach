import Foundation

struct AppAction: Identifiable, Equatable {
    let id: String
    let label: String
    let keyCode: UInt32
    /// Starts with a right-to-left mark and then a left-to-right one: the keys keep their order inside Hebrew text,
    /// and a title that ends in English ("פתח את CallCoach") does not pull them in between its words.
    let hotkey: String
}

enum Actions {
    static var prepare: AppAction {
        AppAction(id: "prepare", label: L("הכן תדריך", "Prepare a brief"), keyCode: 8, hotkey: "\u{200F}\u{200E}⌃⌥C")
    }
    static var startNow: AppAction {
        AppAction(id: "start_now", label: L("התחל שיחה", "Start a call"), keyCode: 1, hotkey: "\u{200F}\u{200E}⌃⌥S")
    }
    static var open: AppAction {
        AppAction(id: "open", label: L("פתח את CallCoach", "Open CallCoach"), keyCode: 31, hotkey: "\u{200F}\u{200E}⌃⌥O")
    }
    static var ask: AppAction {
        AppAction(id: "ask_now", label: L("שאלה עכשיו", "Ask now"), keyCode: 12, hotkey: "\u{200F}\u{200E}⌃⌥Q")
    }
    static var termsNow: AppAction {
        AppAction(id: "terms_now", label: L("מה המושג שנאמר עכשיו?", "What term was just said?"), keyCode: 17,
                  hotkey: "\u{200F}\u{200E}⌃⌥T")
    }
    /// The context window: a note on the call, or an instruction to the coach.
    static var context: AppAction {
        AppAction(id: "context", label: L("הקשר או הוראה למאמן", "Context or a note to the coach"), keyCode: 45,
                  hotkey: "\u{200F}\u{200E}⌃⌥N")
    }
    static var screen: AppAction {
        AppAction(id: "screen", label: L("צלם את חלון השיחה", "Capture the call window"), keyCode: 35,
                  hotkey: "\u{200F}\u{200E}⌃⌥P")
    }
    static var mark: AppAction {
        AppAction(id: "mark", label: L("סמן רגע", "Mark moment"), keyCode: 11, hotkey: "\u{200F}\u{200E}⌃⌥B")
    }
    static var hide: AppAction {
        AppAction(id: "hide", label: L("הסתר", "Hide"), keyCode: 4, hotkey: "\u{200F}\u{200E}⌃⌥H")
    }
    static var fill: AppAction {
        AppAction(id: "fill", label: L("מסך מלא", "Fill screen"), keyCode: 3, hotkey: "\u{200F}\u{200E}⌃⌥F")
    }
    static var panel: [AppAction] { [ask, termsNow, context, screen, mark, hide] }
    static var all: [AppAction] { [prepare, startNow, open] + panel + [fill] }

    /// The action's label and its keys, as a menu item reads.
    static func titled(_ action: AppAction, _ title: String? = nil) -> String {
        "\(title ?? action.label) \(action.hotkey)"
    }
}

enum UsageMemory {
    static func key(_ date: Date, _ parts: Set<Calendar.Component>) -> String {
        let c = Calendar.current.dateComponents(parts, from: date)
        return "\(c.year ?? 0)-\(c.month ?? 0)-\(c.day ?? 0)"
    }

    static func save(_ usage: UsageEvent, date: Date = Date(), defaults: UserDefaults = .standard) {
        defaults.set(try? JSONEncoder().encode(usage), forKey: "usage.last")
        defaults.set(key(date, [.year, .month, .day]), forKey: "usage.day")
        defaults.set(key(date, [.year, .month]), forKey: "usage.month")
    }

    static func load(date: Date = Date(), defaults: UserDefaults = .standard) -> (today: Totals, month: Totals) {
        guard let data = defaults.data(forKey: "usage.last"),
              let usage = try? JSONDecoder().decode(UsageEvent.self, from: data) else { return (.zero, .zero) }
        let sameMonth = defaults.string(forKey: "usage.month") == key(date, [.year, .month])
        let sameDay = defaults.string(forKey: "usage.day") == key(date, [.year, .month, .day])
        return (sameDay ? usage.today : .zero, sameMonth ? usage.month : .zero)
    }

    static func requests(_ n: Int) -> String {
        n == 1 ? L("בקשה אחת", "1 request") : L("\(n) בקשות", "\(n) requests")
    }

    /// A subscription has a fixed price, so its dollar figure is only what the API would charge and is left out.
    static func label(_ t: Totals, subscription: Bool) -> String {
        subscription ? requests(t.requests) + L(", כלול במנוי", ", included in the plan")
            : String(format: "$%.4f, ", t.usd) + requests(t.requests)
    }
}

extension SelfCheck {
    static func actionChecks(_ root: URL) -> [String] {
        var p: [String] = []
        let ids = Actions.all.map(\.id)
        expect(ids == ["prepare", "start_now", "open", "ask_now", "terms_now", "context", "screen", "mark", "hide", "fill"],
               "actions \(ids)", &p)
        expect(!Actions.all.map(\.keyCode).contains(13), "no watch key any more", &p)
        let codes = Actions.all.map(\.keyCode)
        expect(Set(codes).count == codes.count && !codes.contains(15) && !codes.contains(46),
               "hotkeys are unique and leave CallRec's R and M free", &p)
        expect(Actions.all.allSatisfy { $0.hotkey.hasPrefix("\u{200F}\u{200E}") },
               "hotkey labels keep their order inside Hebrew text", &p)
        expect(Actions.titled(Actions.fill) == "מסך מלא \u{200F}\u{200E}⌃⌥F"
               && Actions.titled(Actions.prepare, "הכן תדריך…") == "הכן תדריך… \u{200F}\u{200E}⌃⌥C",
               "menu titles with keys", &p)
        let day = DateComponents(calendar: .current, year: 2026, month: 11, day: 15, hour: 12).date!
        let usage = UsageEvent(call: .zero, today: Totals(input: 1, output: 1, cacheRead: 0, cacheWrite: 0, requests: 9, usd: 0.0014),
                               month: Totals(input: 1, output: 1, cacheRead: 0, cacheWrite: 0, requests: 44, usd: 0.0071))
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("callcoach-usage-\(getpid())")
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let suite = folder.appendingPathComponent("usage").path
        let defaults = UserDefaults(suiteName: suite)!
        defer {
            defaults.removePersistentDomain(forName: suite)
            try? FileManager.default.removeItem(at: folder)
        }
        UsageMemory.save(usage, date: day, defaults: defaults)
        let same = UsageMemory.load(date: day, defaults: defaults)
        expect(same.today.requests == 9 && same.month.requests == 44, "usage is remembered", &p)
        let next = UsageMemory.load(date: day.addingTimeInterval(86_400), defaults: defaults)
        expect(next.today.requests == 0 && next.month.requests == 44, "a new day starts at zero", &p)
        let nextMonth = UsageMemory.load(date: day.addingTimeInterval(20 * 86_400), defaults: defaults)
        expect(nextMonth.month.requests == 0, "a new month starts at zero", &p)
        expect(UsageMemory.label(usage.today, subscription: false) == "$0.0014, 9 בקשות",
               "label \(UsageMemory.label(usage.today, subscription: false))", &p)
        expect(UsageMemory.label(usage.today, subscription: true) == "9 בקשות, כלול במנוי",
               "subscription label \(UsageMemory.label(usage.today, subscription: true))", &p)
        expect(UsageMemory.requests(1) == "בקשה אחת", "one request", &p)
        return p
    }
}
