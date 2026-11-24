import SwiftUI

/// A short note about the call, added while it runs: who I am talking to and what I want to learn, or an instruction
/// to the coach (focus on X, no questions about Y). A picture can go with it, or alone: one I took myself and paste with ⌘V, or a shot of the call's window. One that is not added
/// is deleted.
struct ContextView: View {
    static var title: String { L("הקשר לשיחה", "Call context") }

    @ObservedObject var state: AppState
    var done: () -> Void
    @ViewState private var text = ""
    @ViewState private var shot: URL?
    @ViewState private var picture: NSImage?
    @ViewState private var shooting = false
    @ViewState private var problem: String?
    @ViewState private var sent = false
    @ViewState private var monitor: Any?
    @FocusState private var writing: Bool

    static var hint: String {
        L("מה כדאי שהמאמן ידע או יעשה? למשל עם מי מדברים ומה אתה רוצה ללמוד, או הוראה: \"תתמקד בגיוס\", \"בלי שאלות על סייבר\". הוראה תקפה עד סוף השיחה, וכשהוראות סותרות, האחרונה קובעת.",
          "What should the coach know or do? For example who you are talking to and what you want to learn, or an instruction: \"focus on fundraising\", \"no questions about cyber\". An instruction holds until the call ends, and when two disagree, the last one counts.")
    }

    static var placeholder: String { L("כתוב כאן…", "Write here…") }

    private var trimmed: String { text.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(Self.title).font(.headline)
            Text(Self.hint).font(.caption).foregroundStyle(.secondary)
            TextEditor(text: $text).font(.system(size: 13)).frame(minHeight: 110)
                .scrollContentBackground(.hidden)
                .padding(.vertical, 4)
                .padding(.horizontal, 2)
                .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Color(nsColor: .separatorColor)))
                .overlay(alignment: .topLeading) {
                    if text.isEmpty {
                        Text(Self.placeholder).font(.system(size: 13)).foregroundStyle(.tertiary)
                            .padding(.vertical, 4)
                            .padding(.horizontal, 7)
                            .allowsHitTesting(false)
                    }
                }
                .focused($writing)
                .onAppear { writing = true }
            ShotRow(picture: picture, shooting: shooting, problem: problem, enabled: state.model.phase == .active,
                    take: take, remove: remove)
            if !state.contextNotes.isEmpty {
                Text(L("כבר נוסף:", "Already added:")).font(.caption.weight(.semibold))
                ForEach(Array(state.contextNotes.enumerated()), id: \.offset) { _, note in
                    DirectedText(note, size: 12).foregroundStyle(.secondary)
                }
            }
            HStack {
                Spacer()
                Button(L("ביטול", "Cancel")) { done() }.keyboardShortcut(.cancelAction)
                Button(L("הוסף", "Add")) { add() }
                    .keyboardShortcut(.return, modifiers: .command)
                    .disabled((trimmed.isEmpty && shot == nil) || shooting || state.model.phase != .active)
            }
        }
        .padding(16)
        .frame(width: 420)
        .onAppear { listen() }
        .onDisappear {
            if let monitor { NSEvent.removeMonitor(monitor) }
            if !sent { remove() }
        }
    }

    /// ⌘V in this window with a picture on the clipboard attaches the picture; with text it pastes the text.
    private func listen() {
        guard monitor == nil else { return }
        monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            guard event.window?.title == Self.title,
                  event.modifierFlags.intersection(.deviceIndependentFlagsMask) == .command,
                  event.charactersIgnoringModifiers == "v", let image = ScreenShot.pasted() else { return event }
            guard let url = state.keepPicture(image) else {
                problem = ShotFailure.failed.message
                return nil
            }
            remove()
            shot = url
            picture = NSImage(contentsOf: url)
            problem = nil
            return nil
        }
    }

    private func add() {
        let note = String(trimmed.prefix(4000))
        if let shot {
            state.sendScreen(shot, text: note)
        } else {
            state.addContext(note)
        }
        sent = true
        done()
    }

    private func take() {
        shooting = true
        problem = nil
        state.takeShot { result in
            shooting = false
            switch result {
            case .success(let url):
                shot = url
                picture = NSImage(contentsOf: url)
            case .failure(let failure):
                problem = failure.message
            }
        }
    }

    private func remove() {
        if let shot { try? FileManager.default.removeItem(at: shot) }
        shot = nil
        picture = nil
    }
}

/// "צרף צילום של חלון השיחה", then the shot small with "הסר".
struct ShotRow: View {
    let picture: NSImage?
    var shooting = false
    var problem: String?
    var enabled = true
    var take: () -> Void = {}
    var remove: () -> Void = {}

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                if let picture {
                    let fit = min(160 / max(picture.size.width, 1), 90 / max(picture.size.height, 1))
                    Image(nsImage: picture).resizable()
                        .frame(width: picture.size.width * fit, height: picture.size.height * fit)
                        .clipShape(RoundedRectangle(cornerRadius: 4))
                        .overlay(RoundedRectangle(cornerRadius: 4).stroke(Color.primary.opacity(0.15)))
                    Button(L("הסר", "Remove"), action: remove)
                } else {
                    Button(action: take) {
                        Label(shooting ? L("מצלם…", "Taking…") : L("צרף צילום של חלון השיחה", "Attach a shot of the call window"),
                              systemImage: "camera.viewfinder")
                    }
                    .disabled(shooting || !enabled)
                }
                Spacer(minLength: 0)
            }
            if let problem {
                DirectedText(problem, size: 11).foregroundStyle(.orange)
            } else if picture == nil {
                DirectedText(L("אפשר גם להדביק כאן צילום שעשית בעצמך (‎⌘⌃⇧4 ואז ‎⌘V), בלי הרשאה. הצד השני לא רואה את זה.",
                               "You can also paste a picture you took (⌘⌃⇧4, then ⌘V), with no permission. The other side does not see it."), size: 11)
                    .foregroundStyle(.secondary)
            }
        }
    }
}
