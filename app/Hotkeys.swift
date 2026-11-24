import AppKit
import Carbon

final class Hotkeys {
    static let shared = Hotkeys()
    private var handlers: [UInt32: () -> Void] = [:]
    private var refs: [EventHotKeyRef] = []
    private var installed = false

    @discardableResult
    func register(keyCode: UInt32, id: UInt32, action: @escaping () -> Void) -> Bool {
        install()
        var ref: EventHotKeyRef?
        let hotKeyID = EventHotKeyID(signature: OSType(0x4343_4f43), id: id)
        let status = RegisterEventHotKey(keyCode, UInt32(controlKey | optionKey), hotKeyID, GetApplicationEventTarget(), 0,
                                         &ref)
        guard status == noErr, let ref else {
            NSLog("CallCoach hotkey \(id) not registered (\(status))")
            return false
        }
        refs.append(ref)
        handlers[id] = action
        return true
    }

    private func install() {
        guard !installed else { return }
        installed = true
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, event, _ in
            var id = EventHotKeyID()
            GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID), nil,
                              MemoryLayout<EventHotKeyID>.size, nil, &id)
            DispatchQueue.main.async { Hotkeys.shared.handlers[id.id]?() }
            return noErr
        }, 1, &spec, nil, nil)
    }
}
