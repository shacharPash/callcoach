import Foundation

/// A side of the call that is not being recorded, from the status's `silent`: a banner in the panel and a triangle in
/// the menu bar while it holds, and a notification when the panel is not on screen (an automatic call starts with it
/// hidden). All of it goes when the side is heard again, the notification included.
enum SilentSide {
    static func title(_ side: String) -> String {
        side == "me" ? L("המיקרופון שלך לא מוקלט", "Your microphone is not being recorded")
            : L("הצד השני לא נשמע", "The other side is not heard")
    }

    static func detail(_ side: String) -> String {
        side == "me" ? L("כדאי לבדוק שהמיקרופון מחובר ושל-CallCoach יש גישה למיקרופון בהגדרות המערכת.",
                         "Check that the microphone is connected and that CallCoach has Microphone access in System Settings.")
            : L("אם מדברים איתך, כדאי לבדוק של-CallCoach יש גישה להקלטת שמע המערכת בהגדרות המערכת.",
                "If people are talking, check that CallCoach has System Audio Recording access in System Settings.")
    }

    /// The panel's banner, the first side when both are missing; nil when nothing is missing or there is no call.
    static func notice(_ model: CallModel) -> Notice? {
        guard model.phase == .active, let side = model.status?.silent.first else { return nil }
        return Notice(title: title(side), detail: detail(side), seconds: nil, until: nil)
    }

    /// The sides missing now in a call; none outside one, so an ended call leaves its last word as it was.
    static func sides(_ model: CallModel) -> [String] {
        model.phase == .active ? model.status?.silent ?? [] : []
    }

    /// Which notifications to post and which to take back when the missing sides change. A side newly missing is
    /// posted only when the panel is not on screen; a side heard again takes its notification back. Leaving the call
    /// takes nothing back: what was still missing at the end stays in the Notification Center.
    static func alerts(was: [String], now: [String], panelShown: Bool, inCall: Bool) -> (post: [String], remove: [String]) {
        guard inCall else { return ([], []) }
        return (panelShown ? [] : now.filter { !was.contains($0) }, was.filter { !now.contains($0) })
    }

    static func identifier(_ side: String) -> String { "silent " + side }

    static func checks(_ root: URL) -> [String] {
        var p: [String] = []
        let decoded = { (json: String) -> Status? in
            guard let (event, _) = try? Event.decode(json), case .status(let s) = event else { return nil }
            return s
        }
        let base = #"{"type": "status", "state": "active", "elapsed": 1, "talk_share": 0, "lang_me": "he", "lang_them": "he", "conversation_lang": "he", "agent": "off", "level": "normal", "visible": true"#
        expect(decoded(base + "}")?.silent == [], "an older engine's status has no side missing", &p)
        expect(decoded(base + #", "silent": ["them"]}"#)?.silent == ["them"], "the status names the missing side", &p)

        var model = CallModel()
        model.phase = .active
        model.status = decoded(base + #", "silent": ["me", "them"]}"#)
        expect(notice(model)?.title == title("me") && sides(model) == ["me", "them"], "the banner shows my side first", &p)
        expect(AppDelegate.callIcon(model) == .warning, "a missing side turns the menu bar icon into a triangle", &p)
        model.status = decoded(base + #", "silent": []}"#)
        expect(notice(model) == nil && AppDelegate.callIcon(model) == .coach, "a side heard again takes the banner and the triangle off", &p)
        model.status = decoded(base + #", "silent": ["them"]}"#)
        model.phase = .ended
        expect(notice(model) == nil && sides(model) == [] && AppDelegate.callIcon(model) == .none, "an ended call shows no warning", &p)

        let hidden = alerts(was: [], now: ["them"], panelShown: false, inCall: true)
        expect(hidden.post == ["them"] && hidden.remove == [], "a hidden panel gets a notification", &p)
        let shown = alerts(was: [], now: ["them"], panelShown: true, inCall: true)
        expect(shown.post == [] && shown.remove == [], "a panel on screen gets only the banner", &p)
        let back = alerts(was: ["me", "them"], now: ["me"], panelShown: false, inCall: true)
        expect(back.post == [] && back.remove == ["them"], "a side heard again takes its notification back", &p)
        let ended = alerts(was: ["them"], now: [], panelShown: false, inCall: false)
        expect(ended.post == [] && ended.remove == [], "the end of the call takes nothing back", &p)
        expect(["me", "them"].allSatisfy { title($0).first.map { !$0.isASCII } ?? false }, "the titles start in Hebrew", &p)
        return p
    }
}
