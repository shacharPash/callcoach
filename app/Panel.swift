import AppKit
import Combine
import SwiftUI

final class CoachPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

final class ClickHostingView<Content: View>: NSHostingView<Content> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

/// How the panel view is drawn at the panel's size. Text, symbols and spacing grow with the width.
struct PanelLayout: Equatable {
    static let maxScale: CGFloat = 2
    var width = PanelController.width
    var scale: CGFloat = 1
    /// The height the terms list gets above its own.
    var extra: CGFloat = 0
    /// How much taller than its own height the terms list may grow to show an open row whole.
    var room = CGFloat.infinity
    var filled = false

    /// The text stays its size at every width, so a wider panel gives more room and a narrow one keeps the text
    /// readable. Only Fill screen grows it with the width, for reading from across the room.
    static func scale(_ width: CGFloat, filled: Bool = false) -> CGFloat {
        filled ? min(maxScale, max(1, width / PanelController.width)) : 1
    }

    /// Below this width the call's controls go to a second row under the time; the one row needs about 360 points.
    static let statusRowWidth: CGFloat = 400

    static func statusInTwoRows(_ width: CGFloat) -> Bool { width < statusRowWidth }

    /// Narrower than the default width, the usage badge flows after the chips instead of beside them.
    static func usageInChips(_ width: CGFloat) -> Bool { width < PanelController.width }

    /// A scale whose least height is more than the screen's is lowered until it fits; 1 stays 1.
    static func lowered(_ scale: CGFloat, least: CGFloat, room: CGFloat) -> CGFloat {
        least > room && scale > 1 ? max(1, scale * room / least) : scale
    }

    /// Never below the set height, and taller when the content needs more; the terms list gets what is left. A set
    /// height under the content's takes up to `shrink` off the terms list (a negative extra), so a short panel keeps
    /// everything else whole. All in whole points: the content's measured height is rounded up to whole points, so a
    /// fraction in the extra room made every fit measure a point more or less than the last, and the panel moved a
    /// point up and down.
    static func height(set: CGFloat, least: CGFloat, shrink: CGFloat = 0) -> (height: CGFloat, extra: CGFloat) {
        let least = least.rounded(.up)
        let height = max(set.rounded(), least - max(0, shrink).rounded(.down))
        return (height, height - least)
    }

    /// A size from the grip: at least the panel's least width, and no further than the screen from the top left.
    static func size(_ wanted: NSSize, topLeft: NSPoint, visible: NSRect) -> NSSize {
        let least = PanelController.leastWidth
        return NSSize(width: min(max(least, wanted.width), max(least, visible.maxX - topLeft.x)).rounded(.down),
               height: min(max(1, wanted.height), max(1, topLeft.y - visible.minY)).rounded(.down))
    }

    /// The frame at the chosen top left, moved only as far as it takes to stay on the screen.
    static func frame(topLeft: NSPoint, size: NSSize, visible: NSRect) -> NSRect {
        let x = max(visible.minX, min(topLeft.x, visible.maxX - size.width))
        let top = min(visible.maxY, max(topLeft.y, visible.minY + size.height))
        return NSRect(x: x, y: top - size.height, width: size.width, height: size.height)
    }

    /// A top left is usable when a point just inside it is on one of the screens. A panel that keeps its right edge
    /// (a folded strip in a right-to-left language, saved by the left of its full width) is probed just inside that
    /// edge, since a strip at the left edge of a screen has its full width's left side off it.
    static func reachable(_ topLeft: NSPoint, in screens: [NSRect], rightEdge width: CGFloat? = nil) -> Bool {
        let x = width.map { topLeft.x + $0 - 20 } ?? topLeft.x + 20
        return screens.contains { $0.contains(NSPoint(x: x, y: topLeft.y - 20)) }
    }

    /// The place with no usable one: the saved place when it is on a screen, else the top middle of the main screen.
    static func home(saved: NSPoint?, screens: [NSRect], main: NSRect?, width: CGFloat, rightEdge: Bool = false) -> NSPoint? {
        if let saved, reachable(saved, in: screens, rightEdge: rightEdge ? width : nil) { return saved }
        guard let main else { return nil }
        return NSPoint(x: main.midX - min(width, main.width) / 2, y: main.maxY - 8)
    }

    static let hereMargin: CGFloat = 16
    static let besideGap: CGFloat = 12

    /// A window opened from the panel, such as "הקשר לשיחה": beside the panel on its screen, top to top, on the side
    /// with room (the left first), else under it; always on the screen.
    static func beside(_ panel: NSRect, size: NSSize, visible: NSRect) -> NSRect {
        let left = panel.minX - besideGap - size.width
        let right = panel.maxX + besideGap
        if left >= visible.minX { return frame(topLeft: NSPoint(x: left, y: panel.maxY), size: size, visible: visible) }
        if right + size.width <= visible.maxX {
            return frame(topLeft: NSPoint(x: right, y: panel.maxY), size: size, visible: visible)
        }
        return frame(topLeft: NSPoint(x: panel.minX, y: panel.minY - besideGap), size: size, visible: visible)
    }

    /// "הבא את הפאנל לכאן": the top left that puts the panel at the top right of the visible frame of the screen the
    /// mouse is on, 16 points in. The mouse can be on the menu bar or the Dock, so the whole frame finds the screen;
    /// with the mouse on none, the first screen, the one with the menu bar.
    static func here(mouse: NSPoint, screens: [(frame: NSRect, visible: NSRect)], width: CGFloat) -> NSPoint? {
        let on = screens.first {
            mouse.x >= $0.frame.minX && mouse.x <= $0.frame.maxX && mouse.y >= $0.frame.minY && mouse.y <= $0.frame.maxY
        }
        guard let visible = (on ?? screens.first)?.visible else { return nil }
        let shown = min(width, max(PanelController.leastWidth, visible.width))
        return NSPoint(x: max(visible.minX, visible.maxX - hereMargin - shown), y: visible.maxY - hereMargin)
    }
}

final class PanelController: NSObject {
    /// The width a panel starts with before the grip set one.
    static let width: CGFloat = 440
    /// The grip goes down to this width: the chips wrap to three rows and the call's controls to a second row.
    static let leastWidth: CGFloat = 300
    static let sizeKey = "panel.size"
    static let placeKey = "panel.topLeft"
    let panel: CoachPanel
    private let state: AppState
    private let addContext: () -> Void
    private var host: ClickHostingView<Localized<PanelView>>!
    private var changes: AnyCancellable?
    private var screensChange: AnyCancellable?
    private var fitQueued = false
    private var layout = PanelLayout()
    /// The size set with the grip. The height is the least the panel has.
    private var size: NSSize
    /// The top left he chose; the panel leaves it only as far as it takes to stay on the screen.
    private var home = NSPoint.zero
    /// The place and set size from before Fill screen, while the panel fills its screen.
    private var beforeFill: (topLeft: NSPoint, size: NSSize)?
    private var dragStart: (mouse: NSPoint, origin: NSPoint)?
    private var resizeStart: (mouse: NSPoint, size: NSSize)?
    /// The width, screen height, least height and fill the scale and the open row's room were set for.
    private var fitted: (width: CGFloat, screen: CGFloat, least: CGFloat, filled: Bool)?

    init(state: AppState, addContext: @escaping () -> Void = {}) {
        self.state = state
        self.addContext = addContext
        let saved = UserDefaults.standard.string(forKey: Self.sizeKey).map(NSSizeFromString) ?? .zero
        size = NSSize(width: Self.startWidth(saved: saved.width), height: max(0, saved.height).rounded(.down))
        panel = CoachPanel(contentRect: NSRect(x: 0, y: 0, width: size.width, height: 200),
                           styleMask: [.nonactivatingPanel, .borderless], backing: .buffered, defer: false)
        super.init()
        panel.level = .statusBar
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.isMovableByWindowBackground = false
        panel.sharingType = WindowHost.sharing
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = true
        host = ClickHostingView(rootView: view())
        panel.contentView = host
        changes = state.objectWillChange.merge(with: LanguageStore.shared.objectWillChange)
            .merge(with: NotificationCenter.default.publisher(for: UserDefaults.didChangeNotification).map { _ in })
            .sink { [weak self] _ in self?.queueFit() }
        screensChange = NotificationCenter.default.publisher(for: NSApplication.didChangeScreenParametersNotification)
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.screensChanged() }
        place()
    }

    /// The saved width, never under the least; the default before the grip set one.
    static func startWidth(saved: CGFloat) -> CGFloat {
        saved > 0 ? max(leastWidth, saved).rounded(.down) : width
    }

    /// A place on a screen that is gone gives way to the default one.
    func show() {
        guard !panel.isVisible else { return }
        if !reachable(home) { place() }
        fit()
        panel.orderFrontRegardless()
    }

    /// Moves the panel to the top right of the screen the mouse is on and keeps that place like a dragged one. It ends
    /// Fill screen. The caller shows the panel when it is hidden.
    func bringHere() {
        let screens = NSScreen.screens.map { (frame: $0.frame, visible: $0.visibleFrame) }
        if let before = beforeFill {
            beforeFill = nil
            size = before.size
        }
        guard let place = PanelLayout.here(mouse: NSEvent.mouseLocation, screens: screens, width: size.width) else { return }
        home = place
        saveHome()
        if panel.isVisible { fit() } else { panel.setFrameTopLeftPoint(home) }
    }

    /// Screens were added or removed: a place on none of them gives way to the saved or the default one.
    private func screensChanged() {
        if !reachable(home) {
            if let before = beforeFill {
                beforeFill = nil
                size = before.size
            }
            place()
        }
        if panel.isVisible { fit() }
    }

    /// The fill state ends with the call; hiding the panel during the call keeps it.
    func hide() {
        if !state.inCall { unfill() }
        panel.orderOut(nil)
    }

    /// Fills the visible frame of the panel's screen, or brings back the place and size it had before.
    func toggleFill() {
        guard panel.isVisible else { return }
        if beforeFill != nil {
            unfill()
        } else {
            guard let visible = screen?.visibleFrame else { return }
            beforeFill = (home, size)
            size = visible.size
            home = NSPoint(x: visible.minX, y: visible.maxY)
        }
        fit()
    }

    private func unfill() {
        guard let before = beforeFill else { return }
        beforeFill = nil
        size = before.size
        if reachable(before.topLeft) {
            home = before.topLeft
            panel.setFrameTopLeftPoint(home)
        } else {
            place()
        }
    }

    /// Whether a place is on a screen. In a right-to-left language the place is probed at the right edge it keeps.
    private func reachable(_ topLeft: NSPoint) -> Bool {
        PanelLayout.reachable(topLeft, in: NSScreen.screens.map(\.visibleFrame), rightEdge: Self.stripFromRight ? size.width : nil)
    }

    private var screen: NSScreen? {
        NSScreen.screens.first { PanelLayout.reachable(home, in: [$0.visibleFrame], rightEdge: Self.stripFromRight ? size.width : nil) }
            ?? panel.screen ?? NSScreen.main
    }

    /// With no screen for a moment, as when displays change, nothing limits the size.
    private var visible: NSRect { screen?.visibleFrame ?? .infinite }

    private var topLeft: NSPoint { NSPoint(x: panel.frame.minX, y: panel.frame.maxY) }

    /// A folded strip reads from the right in a right-to-left language, and its right edge is the one that stays
    /// where it is when the panel folds and unfolds.
    private static var stripFromRight: Bool { LanguageStore.shared.lang.direction == .rightToLeft }

    /// The top left of the panel as it is unfolded: the strip's right edge keeps the full width's right edge.
    private var homeTopLeft: NSPoint {
        let strip = PanelView.isStrip(state.model, idle: state.idlePanel && !state.inCall)
        guard strip && Self.stripFromRight else { return topLeft }
        return NSPoint(x: panel.frame.maxX - min(size.width, max(Self.leastWidth, visible.width)), y: panel.frame.maxY)
    }

    private func view() -> Localized<PanelView> {
        Localized(content: PanelView(state: state, layout: layout,
                                     drag: { [weak self] moving in self?.drag(moving) },
                                     resize: { [weak self] moving in self?.resize(moving) },
                                     fill: { [weak self] in self?.toggleFill() }, addContext: addContext))
    }

    private func apply(_ next: PanelLayout) {
        guard next != layout else { return }
        layout = next
        host.rootView = view()
    }

    /// The content's height with the terms list at its own height plus the open row it shows.
    private func measure() -> CGFloat {
        host.fittingSize.height - layout.extra
    }

    /// Several changes in one turn of the run loop fit the panel once, and a hidden panel waits for show().
    private func queueFit() {
        guard !fitQueued else { return }
        fitQueued = true
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.fitQueued = false
            if self.panel.isVisible { self.fit() }
        }
    }

    /// The scale is lowered when the content without an open row does not fit the screen, and an open row gets
    /// only the room the screen has left, so a click never changes the text size. Both are set again only when
    /// the width, the screen or the content's height changes. The panel is the set height when that is more.
    private func fit() {
        let visible = self.visible
        let width = min(size.width, max(Self.leastWidth, visible.width))
        var next = layout
        next.width = width
        next.filled = beforeFill != nil
        if state.model.folded || (state.idlePanel && !state.inCall) { next.extra = 0 }
        apply(next)
        var least = measure()
        let same = fitted.map {
            $0.width == width && $0.screen == visible.height && abs($0.least - least) < 0.5 && $0.filled == next.filled
        } ?? false
        if !same {
            next.scale = PanelLayout.scale(width, filled: next.filled)
            next.room = 0
            apply(next)
            var closed = measure()
            let lowered = PanelLayout.lowered(next.scale, least: closed, room: visible.height)
            if lowered != next.scale {
                next.scale = lowered
                apply(next)
                closed = measure()
            }
            next.room = max(0, visible.height - closed)
            apply(next)
            least = measure()
            fitted = (width, visible.height, least, next.filled)
        }
        // Only a height set with the grip shrinks the terms list; with none (0) the panel is as tall as its content.
        let shrink = size.height > 0 && state.model.chips[.terms] && !(state.idlePanel && !state.inCall)
            ? TermsList.shrink(transcript: state.model.chips[.transcript]) * next.scale : 0
        next.extra = state.model.folded ? 0
            : PanelLayout.height(set: min(size.height, visible.height), least: least, shrink: shrink).extra
        apply(next)
        var content = NSSize(width: width, height: least + next.extra)
        let strip = PanelView.isStrip(state.model, idle: state.idlePanel && !state.inCall)
        if strip { content.width = min(width, host.fittingSize.width.rounded(.up)) }
        let edge = strip && Self.stripFromRight
        let frame: NSRect
        if dragStart == nil {
            let x = edge ? home.x + width - content.width : home.x
            frame = PanelLayout.frame(topLeft: NSPoint(x: x, y: home.y), size: content, visible: visible)
        } else {
            let x = edge ? panel.frame.maxX - content.width : panel.frame.minX
            frame = NSRect(origin: NSPoint(x: x, y: panel.frame.maxY - content.height), size: content)
        }
        if frame != panel.frame { panel.setFrame(frame, display: true) }
    }

    private func place() {
        let saved = UserDefaults.standard.string(forKey: Self.placeKey).map(NSPointFromString)
        if let next = PanelLayout.home(saved: saved, screens: NSScreen.screens.map(\.visibleFrame),
                                       main: NSScreen.main?.visibleFrame, width: size.width, rightEdge: Self.stripFromRight) {
            home = next
        }
        panel.setFrameTopLeftPoint(home)
    }

    /// The panel follows the mouse; where it is let go, it is kept on the screen and that place is kept.
    private func drag(_ moving: Bool) {
        let mouse = NSEvent.mouseLocation
        if moving {
            let start = dragStart ?? (mouse, panel.frame.origin)
            dragStart = start
            panel.setFrameOrigin(NSPoint(x: start.origin.x + mouse.x - start.mouse.x,
                                         y: start.origin.y + mouse.y - start.mouse.y))
        } else {
            dragStart = nil
            home = homeTopLeft
            fit()
            home = homeTopLeft
            if beforeFill == nil { saveHome() }
        }
    }

    /// The grip moves the bottom right corner; the top left stays. A new size ends Fill screen and is kept.
    private func resize(_ moving: Bool) {
        let mouse = NSEvent.mouseLocation
        guard moving else {
            guard resizeStart != nil else { return }
            resizeStart = nil
            home = topLeft
            UserDefaults.standard.set(NSStringFromSize(size), forKey: Self.sizeKey)
            saveHome()
            return
        }
        let start = resizeStart ?? (mouse, panel.frame.size)
        resizeStart = start
        beforeFill = nil
        size = PanelLayout.size(NSSize(width: start.size.width + mouse.x - start.mouse.x,
                                       height: start.size.height + start.mouse.y - mouse.y),
                                topLeft: topLeft, visible: visible)
        queueFit()
    }

    private func saveHome() {
        UserDefaults.standard.set(NSStringFromPoint(home), forKey: Self.placeKey)
    }
}

extension SelfCheck {
    /// Whether the first of two views a `FlowRow` places at the leading edge, as the panel places the chips, is drawn
    /// on the right, in a layout of that direction. The self-check runs on the main thread.
    static func firstOnRight(_ direction: LayoutDirection) -> Bool? {
        let view = FlowRow(spacing: 0) {
            Color.red.frame(width: 20, height: 10)
            Color.blue.frame(width: 20, height: 10)
        }
        .frame(width: 100, height: 10, alignment: .leading)
        .environment(\.layoutDirection, direction)
        let image: CGImage? = MainActor.assumeIsolated {
            let renderer = ImageRenderer(content: view)
            renderer.scale = 1
            return renderer.cgImage
        }
        guard let image else { return nil }
        let bitmap = NSBitmapImageRep(cgImage: image)
        func red(_ x: Int) -> Bool { (bitmap.colorAt(x: x, y: 5)?.usingColorSpace(.deviceRGB)?.redComponent ?? 0) > 0.5 }
        return red(95) && !red(5) ? true : red(5) && !red(95) ? false : nil
    }

    static func panelChecks(_ root: URL) -> [String] {
        var p: [String] = []
        expect(PanelLayout.scale(440) == 1 && PanelLayout.scale(880) == 1 && PanelLayout.scale(300) == 1,
               "a wider panel gives room, not bigger text", &p)
        expect(PanelLayout.scale(660, filled: true) == 1.5 && PanelLayout.scale(1800, filled: true) == 2
               && PanelLayout.scale(300, filled: true) == 1, "Fill screen grows the text, between 1 and 2", &p)
        expect(PanelLayout.statusInTwoRows(300) && PanelLayout.statusInTwoRows(399) && !PanelLayout.statusInTwoRows(440),
               "a narrow panel puts the call's controls on a second row", &p)
        expect(PanelLayout.usageInChips(300) && !PanelLayout.usageInChips(PanelController.width),
               "narrower than the default, the usage badge flows after the chips", &p)
        expect(PanelController.startWidth(saved: 0) == 440 && PanelController.startWidth(saved: 320.6) == 320
               && PanelController.startWidth(saved: 120) == 300 && PanelController.startWidth(saved: 900) == 900,
               "the saved width is kept, never under the least", &p)
        let short = PanelLayout.height(set: 300, least: 420, shrink: 50)
        expect(short.height == 370 && short.extra == -50, "a short panel takes the shrink off the terms list \(short)", &p)
        let notShort = PanelLayout.height(set: 400, least: 420, shrink: 50)
        expect(notShort.height == 400 && notShort.extra == -20, "and only as much as it needs \(notShort)", &p)
        expect(TermsList.shrink(transcript: true) == TermsList.height - TermsList.leastShown
               && TermsList.shrink(transcript: false) > TermsList.shrink(transcript: true),
               "the terms list keeps two rows however short the panel is", &p)
        let grown = PanelLayout.height(set: 300, least: 420)
        expect(grown.height == 420 && grown.extra == 0, "content that needs more height grows the panel", &p)
        let set = PanelLayout.height(set: 600, least: 420)
        expect(set.height == 600 && set.extra == 180, "the set height gives the terms list the rest", &p)
        let dragged = PanelLayout.height(set: 624.195, least: 430.4)
        expect(dragged.height == 624 && dragged.extra == 193, "a dragged height is kept in whole points \(dragged)", &p)
        var measured: CGFloat = 430.4
        var heights: Set<CGFloat> = []
        for _ in 0..<4 {
            let fit = PanelLayout.height(set: 624.195, least: measured)
            heights.insert(fit.height)
            measured = (430.4 + fit.extra).rounded(.up) - fit.extra
        }
        expect(heights == [624], "fitting again and again keeps one height \(heights)", &p)
        let fraction = PanelLayout.size(NSSize(width: 520.67, height: 624.2), topLeft: NSPoint(x: 100, y: 800),
                                        visible: NSRect(x: 0, y: 0, width: 1440, height: 875))
        expect(fraction == NSSize(width: 520, height: 624), "the grip gives whole points \(fraction)", &p)
        let visible = NSRect(x: 0, y: 0, width: 1440, height: 875)
        let top = NSPoint(x: 100, y: 800)
        let small = PanelLayout.size(NSSize(width: 200, height: -40), topLeft: top, visible: visible)
        expect(small == NSSize(width: 300, height: 1), "the grip stops at the least size, and a height it set is never 0 \(small)", &p)
        let narrow = PanelLayout.size(NSSize(width: 320, height: 900), topLeft: top, visible: visible)
        expect(narrow == NSSize(width: 320, height: 800), "a tall narrow panel \(narrow)", &p)
        let big = PanelLayout.size(NSSize(width: 3000, height: 3000), topLeft: top, visible: visible)
        expect(big == NSSize(width: 1340, height: 800), "the grip stops at the screen \(big)", &p)
        let inside = PanelLayout.frame(topLeft: top, size: NSSize(width: 440, height: 500), visible: visible)
        expect(inside == NSRect(x: 100, y: 300, width: 440, height: 500), "a panel on the screen stays put \(inside)", &p)
        let low = PanelLayout.frame(topLeft: NSPoint(x: 100, y: 575), size: NSSize(width: 440, height: 875), visible: visible)
        expect(low == NSRect(x: 100, y: 0, width: 440, height: 875), "a panel dragged down comes back up \(low)", &p)
        let wide = PanelLayout.frame(topLeft: NSPoint(x: 1200, y: 800), size: NSSize(width: 1000, height: 400), visible: visible)
        expect(wide == NSRect(x: 440, y: 400, width: 1000, height: 400), "a wide panel moves left onto the screen \(wide)", &p)
        let tall = PanelLayout.frame(topLeft: NSPoint(x: -50, y: 900), size: NSSize(width: 440, height: 1000), visible: visible)
        expect(tall == NSRect(x: 0, y: -125, width: 440, height: 1000), "a panel taller than the screen starts at its top \(tall)", &p)
        let screens = [visible, NSRect(x: 1440, y: 0, width: 2560, height: 1415)]
        expect(PanelLayout.reachable(NSPoint(x: 2000, y: 1400), in: screens), "a place on the second screen", &p)
        expect(!PanelLayout.reachable(NSPoint(x: 2000, y: 1400), in: [visible]), "that place once the screen is gone", &p)
        let second = NSRect(x: 1440, y: 0, width: 2560, height: 1415)
        let pair = [(frame: NSRect(x: 0, y: 0, width: 1440, height: 900), visible: visible),
                    (frame: NSRect(x: 1440, y: 0, width: 2560, height: 1440), visible: second)]
        let there = PanelLayout.here(mouse: NSPoint(x: 3000, y: 700), screens: pair, width: 440)
        expect(there == NSPoint(x: 4000 - 16 - 440, y: 1415 - 16), "brought to the top right of the mouse's screen \(String(describing: there))", &p)
        let bar = PanelLayout.here(mouse: NSPoint(x: 700, y: 890), screens: pair, width: 600)
        expect(bar == NSPoint(x: 1440 - 16 - 600, y: 875 - 16), "the mouse on the menu bar finds its screen \(String(describing: bar))", &p)
        let edge = PanelLayout.here(mouse: NSPoint(x: 1440, y: 900), screens: pair, width: 440)
        expect(edge == NSPoint(x: 1440 - 16 - 440, y: 875 - 16), "the mouse on the screen's far corner \(String(describing: edge))", &p)
        let lost = PanelLayout.here(mouse: NSPoint(x: -500, y: 3000), screens: pair, width: 440)
        expect(lost == NSPoint(x: 1440 - 16 - 440, y: 875 - 16), "a mouse on no screen brings it to the first \(String(describing: lost))", &p)
        let huge = PanelLayout.here(mouse: NSPoint(x: 100, y: 100), screens: pair, width: 3000)
        expect(huge == NSPoint(x: 0, y: 875 - 16), "a panel wider than the screen starts at its left edge \(String(describing: huge))", &p)
        expect(PanelLayout.here(mouse: .zero, screens: [], width: 440) == nil, "no screen, no place", &p)
        let note = NSSize(width: 420, height: 332)
        let leftOf = PanelLayout.beside(NSRect(x: 900, y: 459, width: 440, height: 400), size: note, visible: visible)
        expect(leftOf == NSRect(x: 468, y: 527, width: 420, height: 332), "the context window opens left of the panel \(leftOf)", &p)
        let rightOf = PanelLayout.beside(NSRect(x: 16, y: 459, width: 440, height: 400), size: note, visible: visible)
        expect(rightOf == NSRect(x: 468, y: 527, width: 420, height: 332), "or right of it at the screen's left edge \(rightOf)", &p)
        let under = PanelLayout.beside(NSRect(x: 0, y: 459, width: 1440, height: 400), size: note, visible: visible)
        expect(under == NSRect(x: 0, y: 115, width: 420, height: 332), "or under a panel as wide as the screen \(under)", &p)
        let onSecond = PanelLayout.beside(NSRect(x: 3500, y: 1000, width: 440, height: 400), size: note, visible: second)
        expect(onSecond.minX == 3068 && second.contains(onSecond), "on the panel's own screen \(onSecond)", &p)
        expect(PanelLayout.reachable(there ?? .zero, in: [visible, second]), "the place it was brought to is on a screen", &p)
        let gone = NSPoint(x: 2000, y: 1400)
        expect(!PanelLayout.reachable(gone, in: [visible])
               && PanelLayout.home(saved: gone, screens: [visible], main: visible, width: 440) == NSPoint(x: 500, y: 867),
               "a place whose screen is gone moves to the top middle of the main screen", &p)
        expect(PanelLayout.home(saved: NSPoint(x: 100, y: 800), screens: [visible], main: visible, width: 440)
               == NSPoint(x: 100, y: 800), "a saved place on a screen is kept", &p)
        expect(PanelLayout.home(saved: gone, screens: [], main: nil, width: 440) == nil, "with no screen the place stays", &p)
        let stripAtLeft = NSPoint(x: -340, y: 800)
        expect(!PanelLayout.reachable(stripAtLeft, in: [visible])
               && PanelLayout.reachable(stripAtLeft, in: [visible], rightEdge: 440)
               && PanelLayout.home(saved: stripAtLeft, screens: [visible], main: visible, width: 440, rightEdge: true) == stripAtLeft,
               "a Hebrew strip at the left edge is saved by a place that is off the screen and still on it", &p)
        expect(PanelLayout.lowered(2, least: 950, room: 875) == 2 * 875 / 950, "a scale that does not fit is lowered", &p)
        expect(PanelLayout.lowered(2, least: 800, room: 875) == 2 && PanelLayout.lowered(1, least: 950, room: 875) == 1,
               "a scale that fits, or 1, stays", &p)
        expect(ReservedHeight.height(least: 112, need: 60, room: 400, extra: 30) == 142, "a short open row takes no more room", &p)
        expect(ReservedHeight.height(least: 112, need: 260, room: 400, extra: 30) == 290, "an open row grows the list", &p)
        expect(ReservedHeight.height(least: 112, need: 260, room: 50, extra: 0) == 162, "only as far as the screen has room", &p)

        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("callcoach-panel-\(getpid())")
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let suite = folder.appendingPathComponent("settings").path
        let defaults = UserDefaults(suiteName: suite)!
        defer {
            defaults.removePersistentDomain(forName: suite)
            try? FileManager.default.removeItem(at: folder)
        }
        var model = CallModel()
        model.recentLines = [1, 2].map {
            TranscriptLine(id: $0, segmentId: $0, channel: $0 == 1 ? "me" : "them", lang: "en", text: "line \($0)",
                           tStart: Double($0), speechEnd: Double($0), lineReady: Double($0), conversationLang: "en")
        }
        model.partial = Partial(channel: "them", text: "and last time", tStart: 3)
        model.translations = [2: "שורה 2"]
        expect(PanelView.transcript(model) == nil, "no transcript while its chip is off", &p)
        model.chips = Chips(on: [.transcript])
        let plain = PanelView.transcript(model)
        expect(plain?.lines.map(\.id) == [1, 2] && plain?.partial == model.partial && plain?.translations.isEmpty == true
               && plain?.wrap == true, "the transcript chip brings the lines and the live line, whole while no AI chip is on", &p)
        model.chips = Chips(on: [.transcript, .translate])
        let translated = PanelView.transcript(model)
        expect(translated?.translations == [2: "שורה 2"] && translated?.wrap == false,
               "translation puts the Hebrew under them, one row a line with the coach on", &p)
        expect(PanelView.groundOpacity >= 0.5 && PanelView.groundOpacity < 1,
               "the panel's ground hides most of a white page behind it, and is still glass", &p)
        expect(TermsList.follows(openId: nil) && !TermsList.follows(openId: 3),
               "a new term scrolls the list down unless a row is open", &p)
        expect(TermsList.unseen(0, adding: 2, openId: 3) == 2 && TermsList.unseen(2, adding: 1, openId: 3) == 3,
               "new terms under an open row add to the arrow's count", &p)
        expect(TermsList.unseen(3, adding: 1, openId: nil) == 0 && TermsList.unseen(2, adding: -1, openId: 3) == 2,
               "the count is gone once the list follows, and a hidden row takes nothing off", &p)
        expect(TermsList.badgeText(7) == "7" && TermsList.badgeText(120) == "99+", "the arrow's number stays short", &p)
        expect(TermsList.leastHeight(transcript: false) == TermsList.height + TranscriptRows.height + PanelView.spacing
               && TermsList.leastHeight(transcript: true) == TermsList.height, "the transcript's room goes to the terms list", &p)
        let flow = FlowRow.places([CGSize(width: 100, height: 20), CGSize(width: 100, height: 24), CGSize(width: 100, height: 20)],
                                  width: 220, spacing: 6)
        expect(flow.points == [CGPoint(x: 0, y: 0), CGPoint(x: 106, y: 0), CGPoint(x: 0, y: 30)]
               && flow.size == CGSize(width: 206, height: 50), "chips that do not fit go to a second row \(flow)", &p)
        let exact = FlowRow.places([CGSize(width: 100, height: 20), CGSize(width: 100, height: 20)], width: 206, spacing: 6)
        let hair = FlowRow.places([CGSize(width: 0.1, height: 20), CGSize(width: 0.2, height: 20)], width: 0.3, spacing: 0)
        expect(exact.points.map(\.y) == [0, 0] && exact.size == CGSize(width: 206, height: 20) && hair.points.map(\.y) == [0, 0]
               && hair.size.height == 20, "a row exactly as wide as the panel stays one row \(exact) \(hair)", &p)
        let oneRow = FlowRow.places([CGSize(width: 100, height: 20), CGSize(width: 80, height: 20)], width: .infinity, spacing: 6)
        expect(oneRow.points.map(\.y) == [0, 0] && oneRow.size == CGSize(width: 186, height: 20), "with room they share a row \(oneRow)", &p)
        expect(Chip.allCases.allSatisfy { NSImage(systemSymbolName: $0.symbol, accessibilityDescription: nil) != nil }
               && Set(Chip.allCases.map(\.symbol)).count == Chip.allCases.count, "chip symbols", &p)
        expect(firstOnRight(.rightToLeft) == true && firstOnRight(.leftToRight) == false,
               "in Hebrew the first chip is on the right", &p)

        func slot(_ card: Card?) -> CGFloat {
            NSHostingView(rootView: NudgeSlot(card: card, phase: .active).frame(width: 412)).fittingSize.height
        }
        let empty = slot(nil)
        let one = Card(id: 1, kind: "tip", text: "תבקש דוגמה אמיתית", lang: "he", triggerLineIds: [], requestedAt: 0,
                       receivedAt: 0, forced: false)
        expect(abs(slot(one) - empty) < 0.5, "a one-line nudge with its label keeps the slot's height \(empty) \(slot(one))", &p)
        let english = Card(id: 2, kind: "question", text: "Who signs off?", lang: "en", triggerLineIds: [], requestedAt: 0,
                           receivedAt: 0, forced: false, he: "מי מאשר?")
        let tip = Card(id: 3, kind: "tip", text: "Ask for an example", lang: "en", triggerLineIds: [], requestedAt: 0, receivedAt: 0,
                   forced: false, he: "תבקש דוגמה")
        expect(slot(english) > slot(one) + 8 && abs(slot(tip) - empty) < 0.5,
               "a question card shows its translation under it, a tip does not \(slot(english)) \(slot(tip))", &p)

        let state = AppState(accounts: AccountStore(tools: folder.appendingPathComponent("tools")), stream: nil, speed: 1)
        state.defaults = defaults
        func height() -> CGFloat {
            NSHostingView(rootView: Localized(content: PanelView(state: state, drag: { _ in }))).fittingSize.height
        }
        state.idlePanel = true
        let idle = height()
        state.idlePanel = false
        let closed = height()
        expect(idle > 40 && idle < closed, "with no call the panel says so and is small \(idle) \(closed)", &p)
        state.toggleMenu()
        let opened = height()
        expect(state.model.menuOpen && opened > closed + 40, "… opens its list inside the panel, which grows \(closed) \(opened)", &p)
        var chose = false
        state.fromMenu { chose = true }
        expect(chose && !state.model.menuOpen, "a choice closes the list", &p)
        state.toggleMenu()
        state.toggleMenu()
        expect(!state.model.menuOpen && abs(height() - closed) < 0.5, "a second click on … closes it", &p)
        expect(PanelMenu.languages.allSatisfy { PanelMenu.languageLabels[$0] != nil }, "every choice in the list has a label", &p)
        state.toggleFold()
        let folded = height()
        let stripWidth = NSHostingView(rootView: Localized(content: PanelView(state: state, drag: { _ in }))).fittingSize.width
        expect(stripWidth > 100 && stripWidth < PanelController.width - 20, "a folded panel is as wide as its content \(stripWidth)", &p)
        state.toggleFold()
        expect(folded < closed - 100 && folded < 60 && abs(height() - closed) < 0.5,
               "a folded panel is its status line, and unfolds as it was \(folded) \(closed)", &p)

        let live = AppState(accounts: AccountStore(tools: folder.appendingPathComponent("tools-live")), stream: nil, speed: 1)
        live.defaults = defaults
        let channel = FakeChannel()
        live.makeChannel = { channel }
        live.ensureEngine()
        channel.emit(#"{"type": "hello", "protocol": \#(protocolVersion), "version": "x"}"#)
        channel.emit(#"{"type": "ready", "folder": "/x", "replay_t0": null}"#)
        func liveHeight() -> CGFloat {
            NSHostingView(rootView: Localized(content: PanelView(state: live, drag: { _ in }))).fittingSize.height
        }
        func liveWidth(_ view: some View) -> CGFloat {
            NSHostingView(rootView: Localized(content: view)).fittingSize.width
        }
        let all = liveHeight()
        live.toggleChip(.terms)
        let noTerms = liveHeight()
        live.toggleChip(.ask)
        let none = liveHeight()
        expect(noTerms < all - 60 && none < noTerms - 40, "a chip turned off takes its section away \(all) \(noTerms) \(none)", &p)
        expect(channel.sent.suffix(2) == [.setSections(Sections(ask: false, terms: false)), .setCoach(false)],
               "the last AI chip off turns the coach off", &p)
        live.toggleChip(.transcript)
        expect(liveHeight() > none + 30 && Settings.transcriptInPanel(defaults), "the transcript chip shows the lines \(none)", &p)
        let hearingOn = liveWidth(HearingChip(hearing: true, share: 0.38))
        let hearingOff = liveWidth(HearingChip(hearing: false, share: 0.38))
        expect(hearingOn > 20 && hearingOff > hearingOn, "the hearing switch, on and off \(hearingOn) \(hearingOff)", &p)
        func chipHeight(_ on: Bool) -> CGFloat {
            NSHostingView(rootView: Localized(content: HearingChip(hearing: on, share: 0.38))).fittingSize.height
        }
        expect(chipHeight(true) == chipHeight(false),
               "turning hearing off does not change the status line's height \(chipHeight(true)) \(chipHeight(false))", &p)
        expect(TermRow.chevron(open: false) == "chevron.forward" && TermRow.chevron(open: true) == "chevron.down"
               && ["ear", "ear.fill", "chevron.forward", "chevron.down", "envelope"].allSatisfy {
                   NSImage(systemSymbolName: $0, accessibilityDescription: nil) != nil
               }, "the panel's symbols exist", &p)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 10, height: 10), styleMask: [.titled],
                              backing: .buffered, defer: true)
        window.sharingType = .readOnly
        window.contentView = CaptureExcluded.Probe()
        expect(window.sharingType == WindowHost.sharing, "a sheet stays out of screen capture like the app's windows", &p)
        return p
    }
}
