import Foundation

/// The app's own log, next to the engine log in the tools folder.
enum AppLog {
    private static let queue = DispatchQueue(label: "app.callcoach.log")
    static var file: URL { Paths.tools.appendingPathComponent("logs/app.log") }
    static var sink: (String) -> Void = append

    static func write(_ message: String) {
        sink(message)
    }

    private static func append(_ message: String) {
        let line = ISO8601DateFormatter().string(from: Date()) + " " + message + "\n"
        let url = file
        queue.async {
            let fm = FileManager.default
            try? fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            if !fm.fileExists(atPath: url.path) { fm.createFile(atPath: url.path, contents: nil) }
            guard let handle = try? FileHandle(forWritingTo: url) else { return }
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: Data(line.utf8))
        }
    }
}
