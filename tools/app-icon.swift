import AppKit

let size = 1024.0
let img = NSImage(size: NSSize(width: size, height: size))
img.lockFocus()
let bg = NSBezierPath(roundedRect: NSRect(x: 100, y: 100, width: 824, height: 824), xRadius: 185, yRadius: 185)
NSGradient(starting: NSColor(calibratedRed: 0.27, green: 0.33, blue: 0.86, alpha: 1),
           ending: NSColor(calibratedRed: 0.13, green: 0.12, blue: 0.42, alpha: 1))!.draw(in: bg, angle: -90)
let bubble = NSImage.SymbolConfiguration(pointSize: 420, weight: .semibold).applying(.init(paletteColors: [.white]))
if let b = NSImage(systemSymbolName: "bubble.left.fill", accessibilityDescription: nil)?.withSymbolConfiguration(bubble) {
    b.draw(in: NSRect(x: (size - b.size.width) / 2 - 30, y: (size - b.size.height) / 2 - 40,
                      width: b.size.width, height: b.size.height))
}
let wave = NSImage.SymbolConfiguration(pointSize: 190, weight: .bold)
    .applying(.init(paletteColors: [NSColor(calibratedRed: 0.20, green: 0.24, blue: 0.70, alpha: 1)]))
if let w = NSImage(systemSymbolName: "waveform", accessibilityDescription: nil)?.withSymbolConfiguration(wave) {
    w.draw(in: NSRect(x: (size - w.size.width) / 2 - 30, y: (size - w.size.height) / 2 - 10,
                      width: w.size.width, height: w.size.height))
}
let spark = NSImage.SymbolConfiguration(pointSize: 210, weight: .bold)
    .applying(.init(paletteColors: [NSColor(calibratedRed: 1.0, green: 0.80, blue: 0.25, alpha: 1)]))
if let s = NSImage(systemSymbolName: "sparkle", accessibilityDescription: nil)?.withSymbolConfiguration(spark) {
    s.draw(in: NSRect(x: 650, y: 620, width: s.size.width, height: s.size.height))
}
img.unlockFocus()
let rep = NSBitmapImageRep(data: img.tiffRepresentation!)!
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
