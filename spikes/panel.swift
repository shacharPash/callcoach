import AppKit

setvbuf(stdout, nil, _IONBF, 0)

final class Panel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

final class Controller: NSObject {
    let label = NSTextField(labelWithString: "כרטיס לדוגמה: שאל מי מחליט על הרכישה?")
    var clicks = 0

    func frontmost() -> String { NSWorkspace.shared.frontmostApplication?.localizedName ?? "?" }

    @objc func tapped() {
        clicks += 1
        label.stringValue = "לחיצה \(clicks). האפליקציה בפוקוס: \(frontmost())"
        print("click \(clicks): frontmost app = \(frontmost())")
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let controller = Controller()
let panel = Panel(contentRect: NSRect(x: 0, y: 0, width: 460, height: 110),
                  styleMask: [.nonactivatingPanel, .borderless], backing: .buffered, defer: false)
panel.level = .statusBar
panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
panel.isFloatingPanel = true
panel.hidesOnDeactivate = false
panel.becomesKeyOnlyIfNeeded = true
panel.isMovableByWindowBackground = true
panel.sharingType = .none
panel.backgroundColor = NSColor.windowBackgroundColor.withAlphaComponent(0.95)
panel.hasShadow = true

let stack = NSStackView()
stack.orientation = .vertical
stack.alignment = .trailing
stack.edgeInsets = NSEdgeInsets(top: 12, left: 16, bottom: 12, right: 16)
stack.userInterfaceLayoutDirection = .rightToLeft
controller.label.font = .systemFont(ofSize: 17, weight: .semibold)
controller.label.alignment = .right
let button = NSButton(title: "שאלה עכשיו", target: controller, action: #selector(Controller.tapped))
stack.addArrangedSubview(controller.label)
stack.addArrangedSubview(button)
panel.contentView = stack

if let screen = NSScreen.main {
    let f = screen.visibleFrame
    panel.setFrameOrigin(NSPoint(x: f.midX - 230, y: f.maxY - 130))
}
panel.orderFrontRegardless()
print("panel shown; frontmost app = \(controller.frontmost())")
app.run()
