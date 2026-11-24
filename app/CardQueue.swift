import Foundation

/// The nudge slot: one card at a time. A card stays at least 8 s before a newer one replaces it, and leaves the
/// panel after 20 s (an answer to ask_now after 40 s), since an old nudge is noise.
struct CardQueue: Equatable {
    static let minVisible = 8.0
    static let life = 20.0
    static let forcedLife = 40.0
    private(set) var current: Card?
    private(set) var waiting: Card?
    private var since = 0.0

    mutating func add(_ card: Card, now: Double, visible: Bool) -> Card? {
        if visible && (current == nil || card.forced || now - since >= Self.minVisible) {
            if !card.forced { waiting = nil }
            return show(card, now)
        }
        waiting = card
        return nil
    }

    mutating func tick(now: Double, visible: Bool) -> Card? {
        if let card = current, now - since >= (card.forced ? Self.forcedLife : Self.life) { current = nil }
        guard visible, let next = waiting, current == nil || next.forced || now - since >= Self.minVisible else {
            return nil
        }
        waiting = nil
        return show(next, now)
    }

    /// "לא בכיוון" on the card shown: it leaves the slot, and a waiting card can take it at the next tick.
    mutating func drop(_ card: Card) -> Bool {
        guard current?.id == card.id else { return false }
        current = nil
        since = 0
        return true
    }

    /// "לשאול" was closed: the shown card and the waiting one go.
    mutating func clear() {
        current = nil
        waiting = nil
    }

    private mutating func show(_ card: Card, _ now: Double) -> Card {
        current = card
        since = now
        return card
    }
}
