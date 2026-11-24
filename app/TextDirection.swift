import SwiftUI

enum TextDirection {
    /// Right to left when the first strong letter is Hebrew or Arabic, or when most letters are.
    static func isRTL(_ text: String) -> Bool {
        var rtl = 0, ltr = 0, first: Bool?
        for scalar in text.unicodeScalars {
            switch scalar.value {
            case 0x0590...0x05FF, 0x0600...0x06FF, 0xFB1D...0xFB4F:
                rtl += 1
                if first == nil { first = true }
            case 0x41...0x5A, 0x61...0x7A:
                ltr += 1
                if first == nil { first = false }
            default: break
            }
        }
        return first == true || (rtl > 0 && rtl >= ltr)
    }

    /// The text behind a right-to-left or left-to-right mark, so its first character does not decide the base direction.
    /// A text that already starts with a mark keeps it alone.
    static func marked(_ text: String) -> String {
        if text.hasPrefix("\u{200F}") || text.hasPrefix("\u{200E}") { return text }
        return (isRTL(text) ? "\u{200F}" : "\u{200E}") + text
    }

    /// A path or other left-to-right token kept whole inside Hebrew text, so its slashes and dots stay in place.
    static func isolated(_ token: String) -> String {
        "\u{2066}" + token + "\u{2069}"
    }

    /// Every line behind the mark of the text's direction, so each paragraph of a text in one language keeps it,
    /// even one that starts with a name in the other.
    static func markedLines(_ text: String) -> String {
        let mark = isRTL(text) ? "\u{200F}" : "\u{200E}"
        return text.components(separatedBy: "\n").map { mark + $0 }.joined(separator: "\n")
    }
}

/// In the panel, the size grows with the panel's scale. The text is laid out in its own direction, so its punctuation
/// and word order are right, and flush to its own leading side; with `side`, flush to that side's leading edge instead,
/// so a Hebrew translation sits under the English question it translates.
struct DirectedText: View {
    let text: String
    var size: CGFloat = 13
    var weight: Font.Weight = .regular
    var side: LayoutDirection? = nil
    @Environment(\.panelScale) private var scale

    init(_ text: String, size: CGFloat = 13, weight: Font.Weight = .regular, side: LayoutDirection? = nil) {
        self.text = text
        self.size = size
        self.weight = weight
        self.side = side
    }

    var body: some View {
        let own: LayoutDirection = TextDirection.isRTL(text) ? .rightToLeft : .leftToRight
        let side = self.side ?? own
        Text(TextDirection.marked(text))
            .font(.system(size: size * scale, weight: weight))
            .multilineTextAlignment(own == side ? .leading : .trailing)
            .environment(\.layoutDirection, own)
            .frame(maxWidth: .infinity, alignment: .leading)
            .environment(\.layoutDirection, side)
    }
}
