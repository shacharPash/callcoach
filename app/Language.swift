import AppKit
import Combine
import SwiftUI

enum UILang: String, CaseIterable, Identifiable {
    case he, en

    var id: String { rawValue }
    var name: String { self == .he ? "עברית" : "English" }
    var direction: LayoutDirection { self == .he ? .rightToLeft : .leftToRight }
}

/// The interface language. Hebrew unless the user picked English.
final class LanguageStore: ObservableObject {
    static let shared = LanguageStore()
    static let key = "uiLanguage"

    @Published var lang: UILang

    private init() {
        lang = UILang(rawValue: UserDefaults.standard.string(forKey: Self.key) ?? "") ?? .he
    }

    func choose(_ value: UILang) {
        UserDefaults.standard.set(value.rawValue, forKey: Self.key)
        lang = value
    }
}

/// The interface text in the current language: Hebrew first, English second.
func L(_ he: String, _ en: String) -> String {
    LanguageStore.shared.lang == .he ? he : en
}

/// Segmented controls keep their left-to-right order inside a right-to-left layout, so Hebrew lists the options
/// reversed, which puts the first one on the right. A right-to-left system mirrors them by itself.
func inReadingOrder<T>(_ items: [T]) -> [T] {
    LanguageStore.shared.lang == .he && NSApplication.shared.userInterfaceLayoutDirection == .leftToRight
        ? items.reversed() : items
}

/// Draws its content again in the new language and direction when the language changes.
struct Localized<Content: View>: View {
    @ObservedObject var store = LanguageStore.shared
    let content: Content

    var body: some View {
        content.id(store.lang).environment(\.layoutDirection, store.lang.direction)
    }
}

extension SelfCheck {
    static func languageChecks(_ root: URL) -> [String] {
        var p: [String] = []
        let store = LanguageStore.shared
        let saved = store.lang
        defer { store.lang = saved }
        store.lang = .en
        expect(L("שלום", "Hello") == "Hello", "English text", &p)
        expect(Actions.all.allSatisfy { $0.label.unicodeScalars.allSatisfy { !(0x0590...0x05FF).contains($0.value) } },
               "action labels in English", &p)
        expect(inReadingOrder([1, 2, 3]) == [1, 2, 3], "English keeps the order", &p)
        store.lang = .he
        expect(L("שלום", "Hello") == "שלום", "Hebrew text", &p)
        let mirrored = NSApplication.shared.userInterfaceLayoutDirection == .rightToLeft
        expect(inReadingOrder([1, 2, 3]) == (mirrored ? [1, 2, 3] : [3, 2, 1]), "Hebrew starts on the right", &p)
        return p
    }
}
