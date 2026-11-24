import AVFoundation
import AppKit

enum Permissions {
    static var microphone: AVAuthorizationStatus { AVCaptureDevice.authorizationStatus(for: .audio) }

    /// Some microphone is connected; finding one does not ask for access.
    static var hasMicrophone: Bool { AVCaptureDevice.default(for: .audio) != nil }

    static func requestMicrophone(_ done: @escaping (Bool) -> Void) {
        AVCaptureDevice.requestAccess(for: .audio) { ok in DispatchQueue.main.async { done(ok) } }
    }

    /// Screen recording, which a shot of the call's window needs. Asking shows a system dialog, so it is asked only
    /// from Settings, never during a call.
    static var screen: Bool { CGPreflightScreenCaptureAccess() }

    @discardableResult
    static func requestScreen() -> Bool { CGRequestScreenCaptureAccess() }

    static func openSettings(_ anchor: String) {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(anchor)") {
            NSWorkspace.shared.open(url)
        }
    }
}
