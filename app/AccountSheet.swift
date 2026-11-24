import AppKit
import SwiftUI

/// How long a new subscription's sign-in is checked by itself after the Terminal window opens.
enum AccountWatch {
    static let every: Double = 5
    static let most: Double = 300

    static func keepsChecking(elapsed: Double, signedIn: Bool) -> Bool {
        !signedIn && elapsed <= most
    }
}

/// What a subscription means for privacy on a work computer, in the add sheet and under the accounts: one short line,
/// calm, with the warning color only on its sign.
struct SubscriptionNote: View {
    static var text: String {
        L("במחשב עבודה עם Claude Code מנוהל, ה-hooks המנוהלים רואים גם שיחות דרך מנוי. לשיחות פרטיות עדיף מפתח API.",
          "On a work computer with managed Claude Code, the managed hooks also see calls through a subscription. For private calls, use an API key.")
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 11)).foregroundStyle(.orange)
            DirectedText(Self.text, size: 11).foregroundStyle(.secondary)
        }
    }
}

/// "הוסף חשבון": the kind as two choices, a name in any language, then sign in (a subscription) or save (a key).
/// A missing name shows a red line under the field; the button always works.
struct AccountSheet: View {
    @ObservedObject var state: AppState
    var done: (AppAccount) -> Void
    var cancel: () -> Void
    @ViewState private var kind = AccountKind.subscription
    @ViewState private var label = ""
    @ViewState private var key = ""
    @ViewState private var loginMode = "subscription"
    @ViewState private var problem: String?
    @Environment(\.layoutDirection) private var direction

    static func primaryTitle(_ kind: AccountKind) -> String {
        kind == .apiKey ? L("שמור", "Save") : L("התחבר", "Sign in")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            DirectedText(L("חשבון חדש", "New account"), size: 15, weight: .semibold)
            Picker("", selection: $kind) {
                Text(L("מנוי Claude", "Claude subscription")).tag(AccountKind.subscription)
                Text(L("מפתח API", "API key")).tag(AccountKind.apiKey)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .onChange(of: kind) { problem = nil }
            VStack(alignment: .leading, spacing: 4) {
                DirectedText(L("שם", "Name"), size: 12).foregroundStyle(.secondary)
                TextField("", text: $label, prompt: Text(L("למשל: פרטי", "For example: Private")))
                    .textFieldStyle(.roundedBorder)
                    .multilineTextAlignment(direction == .rightToLeft ? .trailing : .leading)
                    .environment(\.layoutDirection, .leftToRight)
                    .onChange(of: label) { problem = nil }
                if let problem { DirectedText(problem, size: 11).foregroundStyle(.red) }
            }
            if kind == .apiKey {
                SecureField(L("מפתח API", "API key"), text: $key).textFieldStyle(.roundedBorder)
                    .onChange(of: key) { problem = nil }
            } else {
                DisclosureGroup(L("דרך אחרת להתחבר", "Another way to sign in")) {
                    Picker("", selection: $loginMode) {
                        Text(L("מנוי claude.ai", "claude.ai subscription")).tag("subscription")
                        Text("Console").tag("console")
                        Text("SSO").tag("sso")
                    }
                    .pickerStyle(.radioGroup)
                    .labelsHidden()
                }
                .font(.system(size: 12))
                SubscriptionNote()
            }
            HStack {
                Spacer()
                Button(L("ביטול", "Cancel"), action: cancel).keyboardShortcut(.cancelAction)
                Button(Self.primaryTitle(kind), action: save).keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent)
            }
        }
        .padding(20)
        .frame(width: 380)
    }

    private func save() {
        if kind == .apiKey && key.trimmingCharacters(in: .whitespaces).isEmpty {
            problem = L("צריך להדביק מפתח", "Paste a key")
            return
        }
        do {
            let account = try state.accounts.add(label: label, kind: kind, loginMode: kind == .subscription ? loginMode : nil)
            if kind == .apiKey {
                do { try AccountStore.setKey(key, for: account.name) } catch {
                    try? state.accounts.remove(account.name)
                    throw error
                }
            }
            key = ""
            done(account)
        } catch {
            problem = error.localizedDescription
        }
    }
}
