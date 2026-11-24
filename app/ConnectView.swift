import AppKit
import SwiftUI

struct ConnectView: View {
    @ObservedObject var state: AppState
    @ViewState private var adding = false
    @ViewState private var notes: [String: String] = [:]
    @ViewState private var deleting: AppAccount?
    @ViewState private var watches: [String: Timer] = [:]

    var body: some View {
        let accounts = state.accounts.accounts
        Form {
            Section(SettingsPage.account.title) {
                if accounts.isEmpty { Text(L("אין חשבונות עדיין", "No accounts yet")).foregroundStyle(.secondary) }
                ForEach(accounts) { row($0) }
                Button { adding = true } label: { Label(L("הוסף חשבון", "Add an account"), systemImage: "plus") }
                    .buttonStyle(.borderedProminent)
                if let check = state.model.account {
                    let shown = accounts.first { $0.name == check.name }?.shown ?? check.name
                    DirectedText(check.ok ? L("בדיקה אחרונה: \(shown) תקין", "Last check: \(shown) is OK")
                                 : L("בדיקה אחרונה: \(shown) נכשל, \(check.message)",
                                     "Last check: \(shown) failed, \(check.message)"), size: 11)
                }
            }
            if accounts.contains(where: { $0.kind == .subscription }) {
                Section { SubscriptionNote() }
            }
        }
        .formStyle(.grouped)
        .sheet(isPresented: $adding) { AccountSheet(state: state, done: added, cancel: { adding = false }) }
        .onDisappear { watches.values.forEach { $0.invalidate() }; watches = [:] }
        .alert(L("למחוק את החשבון \(deleting?.shown ?? "")?", "Delete the account \(deleting?.shown ?? "")?"),
               isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }), presenting: deleting) { account in
            Button(L("מחק", "Delete"), role: .destructive) { remove(account) }
            Button(L("ביטול", "Cancel"), role: .cancel) {}
        } message: { account in
            Text(account.kind == .apiKey ? L("המפתח שלו יימחק מה-Keychain.", "Its key is deleted from the Keychain.")
                 : account.kind == .subscription ? L("הוא ינותק מ-Claude Code, ותיקיית ההתחברות שלו תעבור לפח.",
                                                      "It is signed out of Claude Code, and its sign-in folder goes to the Trash.")
                 : L("החשבון יימחק מהרשימה.", "The account is removed from the list."))
        }
    }

    /// One account: its name over its kind, the sign-in and check as plain buttons, "הפעל" as the row's button (or
    /// "פעיל"), and "מחק" in red at the end, apart from the rest.
    private func row(_ account: AppAccount) -> some View {
        let active = state.accounts.active?.name == account.name
        return HStack(spacing: 12) {
            Image(systemName: active ? "checkmark.circle.fill" : "circle").foregroundStyle(active ? Color.green : .secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(account.shown).bold()
                Text(account.kind.label).font(.system(size: 11)).foregroundStyle(.secondary)
                if let note = notes[account.name] { DirectedText(note, size: 11) }
            }
            Spacer()
            Group {
                if account.kind == .subscription {
                    Button(L("התחבר", "Sign in")) {
                        if login(account.name, mode: account.loginMode ?? "subscription") { watch(account.name) }
                    }
                }
                Button(L("בדוק", "Check")) { check(account) }
            }
            .buttonStyle(.borderless)
            Group {
                if active {
                    Text(L("פעיל", "Active")).foregroundStyle(.green)
                } else {
                    Button(L("הפעל", "Activate")) { state.switchAccount(account.name) }.disabled(state.inCall)
                }
            }
            .frame(minWidth: 64)
            Divider().frame(height: 18)
            Button(L("מחק", "Delete"), role: .destructive) { deleting = account }
                .buttonStyle(.borderless)
                .foregroundStyle(Color.red.opacity(state.inCall && active ? 0.4 : 1))
                .disabled(state.inCall && active)
        }
    }

    private func added(_ account: AppAccount) {
        adding = false
        if account.kind == .subscription {
            if login(account.name, mode: account.loginMode ?? "subscription") { watch(account.name) }
        }
        if state.accounts.active?.name == account.name { state.switchAccount(account.name) } else { state.accountsChanged += 1 }
    }

    @discardableResult
    private func login(_ name: String, mode: String) -> Bool {
        do {
            NSWorkspace.shared.open(try state.accounts.loginScript(for: name, mode: mode))
            notes[name] = L("בדפדפן, בדוק שמופיע החשבון הנכון לפני האישור",
                            "In the browser, check that the right account shows before you approve")
            return true
        } catch {
            notes[name] = error.localizedDescription
            return false
        }
    }

    /// Checks the sign-in of a subscription every few seconds, until it is signed in or the time is up.
    /// A tick is skipped while the last check still runs, and the time limit is judged in the tick itself.
    private func watch(_ name: String) {
        watches[name]?.invalidate()
        let store = state.accounts
        let started = Date()
        var running = false
        watches[name] = Timer.scheduledTimer(withTimeInterval: AccountWatch.every, repeats: true) { timer in
            func stop() {
                timer.invalidate()
                if watches[name] === timer { watches[name] = nil }
            }
            guard AccountWatch.keepsChecking(elapsed: Date().timeIntervalSince(started), signedIn: false) else { stop(); return }
            guard !running else { return }
            running = true
            DispatchQueue.global().async {
                let status = store.status(of: name)
                DispatchQueue.main.async {
                    running = false
                    guard timer.isValid else { return }
                    if status.loggedIn {
                        notes[name] = L("מחובר", "Signed in")
                        stop()
                    } else if !AccountWatch.keepsChecking(elapsed: Date().timeIntervalSince(started), signedIn: false) {
                        notes[name] = L("לא מחובר: \(status.message)", "Not signed in: \(status.message)")
                        stop()
                    }
                }
            }
        }
    }

    private func check(_ account: AppAccount) {
        if state.accounts.active?.name == account.name {
            state.checkAccount()
        } else if account.kind == .subscription {
            let store = state.accounts
            DispatchQueue.global().async {
                let status = store.status(of: account.name)
                DispatchQueue.main.async {
                    notes[account.name] = status.loggedIn ? L("מחובר", "Signed in")
                        : L("לא מחובר: \(status.message)", "Not signed in: \(status.message)")
                }
            }
        } else {
            notes[account.name] = L("צריך להפעיל את החשבון כדי לבדוק אותו", "Activate the account to check it")
        }
    }

    /// The account in use is never removed while a call runs, even when the confirmation was asked before it began.
    static func mayRemove(active: Bool, inCall: Bool) -> Bool {
        !(active && inCall)
    }

    private func remove(_ account: AppAccount) {
        let wasActive = state.accounts.active?.name == account.name
        guard Self.mayRemove(active: wasActive, inCall: state.inCall) else {
            notes[account.name] = L("את החשבון שבשימוש אפשר למחוק רק בין שיחות", "The account in use can be deleted only between calls")
            return
        }
        try? state.accounts.remove(account.name)
        watches[account.name]?.invalidate()
        watches[account.name] = nil
        notes[account.name] = nil
        if wasActive, let next = state.accounts.active { state.switchAccount(next.name) } else { state.accountsChanged += 1 }
    }
}
