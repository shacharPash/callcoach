import AppKit
import ScreenCaptureKit

/// A window on screen, as the choice of the call's window sees it.
struct ShotWindow: Equatable {
    let id: CGWindowID
    let bundle: String
    let title: String
    let width, height: CGFloat
    var layer = 0
    var onScreen = true

    var area: CGFloat { width * height }
}

enum ShotFailure: Error, Equatable {
    case permission, noWindow, failed

    var message: String {
        switch self {
        case .permission:
            return L("אין הרשאת הקלטת מסך. אפשר אותה בהגדרות של CallCoach, לא באמצע שיחה",
                     "No screen recording permission. Allow it in CallCoach's Settings, not during a call")
        case .noWindow: return L("לא מצאתי את חלון השיחה", "Could not find the call's window")
        case .failed: return L("הצילום נכשל", "The screenshot failed")
        }
    }
}

/// A picture of the call app's window only, never the whole screen. It is taken with ScreenCaptureKit, with no
/// sound, flash or thumbnail, and the call app is not told; CallCoach's own windows are never in it.
enum ScreenShot {
    static let callApps: Set<String> = ["us.zoom.xos", "com.microsoft.teams2", "com.microsoft.teams",
                                        "com.cisco.webexmeetingsapp", "Cisco-Systems.Spark", "com.apple.FaceTime"]
    static let browsers: Set<String> = ["com.google.Chrome", "com.apple.Safari", "company.thebrowser.Browser",
                                        "com.microsoft.edgemac", "org.mozilla.firefox", "com.brave.Browser"]
    static let callWords = ["Meet", "Teams", "Zoom", "Webex"]
    /// Smaller windows are toolbars and floating controls.
    static let leastSide: CGFloat = 200
    /// The model reads up to about this many pixels on the long side; more only costs.
    static let longEdge: CGFloat = 1568
    static let quality = 0.8

    /// The call's window: of the app used last, when it is a call app or a browser showing a call; else the largest
    /// window of a call app; else a browser window whose title names a call service; else the largest window of the
    /// app used last. A window on screen comes before one that is not.
    static func pick(_ windows: [ShotWindow], last: String?, own: String) -> ShotWindow? {
        let usable = windows.filter { $0.layer == 0 && min($0.width, $0.height) >= leastSide && $0.bundle != own }
        func largest(_ match: (ShotWindow) -> Bool) -> ShotWindow? {
            let found = usable.filter(match)
            return found.filter(\.onScreen).max { $0.area < $1.area } ?? found.max { $0.area < $1.area }
        }
        func inCall(_ w: ShotWindow) -> Bool {
            callApps.contains(w.bundle)
                || browsers.contains(w.bundle) && callWords.contains { w.title.localizedCaseInsensitiveContains($0) }
        }
        if let last, let w = largest({ $0.bundle == last && inCall($0) }) { return w }
        if let w = largest({ callApps.contains($0.bundle) }) { return w }
        if let w = largest(inCall) { return w }
        guard let last else { return nil }
        return largest { $0.bundle == last && $0.onScreen }
    }

    /// The size to take, at most `longEdge` on the long side.
    static func size(points: CGSize, scale: CGFloat) -> CGSize {
        let pixels = CGSize(width: points.width * scale, height: points.height * scale)
        let fit = min(1, longEdge / max(pixels.width, pixels.height, 1))
        return CGSize(width: (pixels.width * fit).rounded(), height: (pixels.height * fit).rounded())
    }

    /// `screens/HHmmss.jpg`, with a number after it when one was taken in the same second.
    static func file(in folder: URL, at date: Date, exists: (URL) -> Bool) -> URL {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.dateFormat = "HHmmss"
        let name = format.string(from: date)
        var url = folder.appendingPathComponent(name + ".jpg")
        var n = 2
        while exists(url) {
            url = folder.appendingPathComponent("\(name)-\(n).jpg")
            n += 1
        }
        return url
    }

    /// Takes the call's window into `folder` and calls back on the main thread. With no screen recording
    /// permission it fails at once and asks nothing, so no system dialog opens in the middle of a call.
    static func take(last: String?, folder: URL, done: @escaping (Result<URL, ShotFailure>) -> Void) {
        guard CGPreflightScreenCaptureAccess() else { return done(.failure(.permission)) }
        Task {
            let result: Result<URL, ShotFailure>
            do {
                result = .success(try await capture(last: last, folder: folder))
            } catch let failure as ShotFailure {
                result = .failure(failure)
            } catch {
                AppLog.write("screenshot: \(error.localizedDescription)")
                result = .failure(.failed)
            }
            await MainActor.run { done(result) }
        }
    }

    private static func capture(last: String?, folder: URL) async throws -> URL {
        let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
        let windows = content.windows.map {
            ShotWindow(id: $0.windowID, bundle: $0.owningApplication?.bundleIdentifier ?? "", title: $0.title ?? "",
                       width: $0.frame.width, height: $0.frame.height, layer: $0.windowLayer, onScreen: $0.isOnScreen)
        }
        guard let chosen = pick(windows, last: last, own: Bundle.main.bundleIdentifier ?? "app.callcoach"),
              let window = content.windows.first(where: { $0.windowID == chosen.id }) else { throw ShotFailure.noWindow }
        let filter = SCContentFilter(desktopIndependentWindow: window)
        let config = SCStreamConfiguration()
        let size = size(points: filter.contentRect.size, scale: CGFloat(filter.pointPixelScale))
        config.width = Int(size.width)
        config.height = Int(size.height)
        config.showsCursor = false
        config.ignoreShadowsSingleWindow = true
        let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        return try save(image, folder: folder)
    }

    /// A JPEG of the picture on white, at most `longEdge` on its long side.
    static func jpeg(_ image: CGImage) -> Data? {
        let fit = size(points: CGSize(width: image.width, height: image.height), scale: 1)
        guard let context = CGContext(data: nil, width: Int(fit.width), height: Int(fit.height), bitsPerComponent: 8,
                                      bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                      bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
        let rect = CGRect(origin: .zero, size: fit)
        context.setFillColor(CGColor(gray: 1, alpha: 1))
        context.fill(rect)
        context.interpolationQuality = .high
        context.draw(image, in: rect)
        guard let flat = context.makeImage() else { return nil }
        return NSBitmapImageRep(cgImage: flat).representation(using: .jpeg, properties: [.compressionFactor: quality])
    }

    /// Saves a picture as the next `screens/HHmmss.jpg`.
    static func save(_ image: CGImage, folder: URL, at date: Date = Date()) throws -> URL {
        guard let data = jpeg(image) else { throw ShotFailure.failed }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let url = file(in: folder, at: date) { FileManager.default.fileExists(atPath: $0.path) }
        try data.write(to: url)
        return url
    }

    /// A picture on the clipboard, as ⌘⌃⇧4 leaves it, or an image file copied in Finder; nil when the clipboard
    /// has text, so ⌘V pastes the text as usual.
    static func pasted(_ board: NSPasteboard = .general) -> CGImage? {
        guard board.availableType(from: [.string]) == nil else { return nil }
        let image = (board.readObjects(forClasses: [NSImage.self]) as? [NSImage])?.first
            ?? (board.readObjects(forClasses: [NSURL.self], options: [.urlReadingContentsConformToTypes: ["public.image"]])
                as? [URL])?.first.flatMap(NSImage.init(contentsOf:))
        return image?.cgImage(forProposedRect: nil, context: nil, hints: nil)
    }
}

extension SelfCheck {
    static func screenChecks(_ root: URL) -> [String] {
        var p: [String] = []
        let zoom = ShotWindow(id: 1, bundle: "us.zoom.xos", title: "Zoom Meeting", width: 1400, height: 900)
        let zoomBar = ShotWindow(id: 2, bundle: "us.zoom.xos", title: "", width: 400, height: 60)
        let meet = ShotWindow(id: 3, bundle: "com.google.Chrome", title: "Meet - abc-defg-hij", width: 1600, height: 1000)
        let docs = ShotWindow(id: 4, bundle: "com.google.Chrome", title: "Docs", width: 1800, height: 1100)
        let own = ShotWindow(id: 5, bundle: "app.callcoach", title: "CallCoach", width: 2000, height: 1200)
        let notes = ShotWindow(id: 6, bundle: "com.apple.Notes", title: "Notes", width: 900, height: 700)
        let hidden = ShotWindow(id: 7, bundle: "us.zoom.xos", title: "Zoom Meeting", width: 1800, height: 1100, onScreen: false)
        let menu = ShotWindow(id: 8, bundle: "us.zoom.xos", title: "", width: 1400, height: 900, layer: 25)
        let all = [zoomBar, docs, own, notes, meet, zoom, hidden, menu]
        expect(ScreenShot.pick(all, last: "com.google.Chrome", own: "app.callcoach")?.id == 3,
               "the app used last first, when it shows a call", &p)
        expect(ScreenShot.pick(all, last: "com.apple.Notes", own: "app.callcoach")?.id == 1,
               "else the largest window of a call app on screen, not its toolbar or a hidden one", &p)
        expect(ScreenShot.pick([docs, meet, notes], last: nil, own: "app.callcoach")?.id == 3,
               "else a browser window that names a call", &p)
        expect(ScreenShot.pick([docs, notes, own], last: "com.apple.Notes", own: "app.callcoach")?.id == 6,
               "else the largest window of the app used last", &p)
        expect(ScreenShot.pick([docs, own], last: nil, own: "app.callcoach") == nil, "no call window found", &p)
        expect(ScreenShot.pick([hidden], last: nil, own: "app.callcoach")?.id == 7, "a call window not on screen still counts", &p)
        expect(ScreenShot.pick([own], last: "app.callcoach", own: "app.callcoach") == nil, "never CallCoach's own window", &p)
        expect(ScreenShot.size(points: CGSize(width: 1440, height: 900), scale: 2) == CGSize(width: 1568, height: 980),
               "a Retina window is taken at 1568 on its long side", &p)
        expect(ScreenShot.size(points: CGSize(width: 800, height: 600), scale: 1) == CGSize(width: 800, height: 600),
               "a small window is taken as it is", &p)
        let folder = URL(fileURLWithPath: "/calls/x/screens")
        let at = DateComponents(calendar: .current, year: 2026, month: 11, day: 17, hour: 9, minute: 5, second: 7).date!
        expect(ScreenShot.file(in: folder, at: at) { _ in false }.lastPathComponent == "090507.jpg", "a shot's file name", &p)
        var taken: Set<String> = ["090507.jpg"]
        let second = ScreenShot.file(in: folder, at: at) { taken.contains($0.lastPathComponent) }
        taken.insert(second.lastPathComponent)
        expect(second.lastPathComponent == "090507-2.jpg"
               && ScreenShot.file(in: folder, at: at) { taken.contains($0.lastPathComponent) }.lastPathComponent == "090507-3.jpg",
               "two shots in one second", &p)
        let wide = CGContext(data: nil, width: 3136, height: 1000, bitsPerComponent: 8, bytesPerRow: 0,
                             space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)?.makeImage()
        let shots = FileManager.default.temporaryDirectory.appendingPathComponent("callcoach-shots-\(getpid())")
        defer { try? FileManager.default.removeItem(at: shots) }
        if let wide, let saved = try? ScreenShot.save(wide, folder: shots, at: at),
           let rep = NSBitmapImageRep(data: (try? Data(contentsOf: saved)) ?? Data()) {
            expect(saved.lastPathComponent == "090507.jpg" && rep.pixelsWide == 1568 && rep.pixelsHigh == 500,
                   "a pasted picture is saved at 1568 on its long side \(rep.pixelsWide)x\(rep.pixelsHigh)", &p)
        } else {
            p.append("a pasted picture was not saved")
        }
        let board = NSPasteboard(name: NSPasteboard.Name("callcoach-check-\(getpid())"))
        defer { board.releaseGlobally() }
        board.clearContents()
        board.setString("שלום", forType: .string)
        expect(ScreenShot.pasted(board) == nil, "text on the clipboard pastes as text", &p)
        return p
    }
}
