import AppKit

struct IconSpec {
    let name: String
    let symbol: String
    let top: NSColor
    let bottom: NSColor
}

func color(_ red: CGFloat, _ green: CGFloat, _ blue: CGFloat) -> NSColor {
    NSColor(calibratedRed: red / 255, green: green / 255, blue: blue / 255, alpha: 1)
}

let specs = [
    IconSpec(name: "search", symbol: "magnifyingglass",
             top: color(90, 151, 255), bottom: color(24, 91, 214)),
    IconSpec(name: "otp", symbol: "timer",
             top: color(77, 211, 115), bottom: color(24, 145, 67)),
    IconSpec(name: "alias", symbol: "tag.fill",
             top: color(198, 104, 237), bottom: color(113, 45, 170)),
    IconSpec(name: "setup", symbol: "arrow.down.circle.fill",
             top: color(255, 183, 77), bottom: color(226, 112, 0)),
    IconSpec(name: "stats", symbol: "chart.bar.fill",
             top: color(72, 200, 214), bottom: color(24, 128, 150)),
    IconSpec(name: "lock", symbol: "lock.fill",
             top: color(255, 104, 94), bottom: color(210, 42, 37)),
    IconSpec(name: "help", symbol: "questionmark.circle.fill",
             top: color(164, 164, 170), bottom: color(99, 99, 102)),
]

let size = NSSize(width: 256, height: 256)
let output = URL(fileURLWithPath: "src/icons", isDirectory: true)
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)

for spec in specs {
    guard let symbol = NSImage(systemSymbolName: spec.symbol, accessibilityDescription: nil) else {
        fatalError("Missing SF Symbol: \(spec.symbol)")
    }
    let point = NSImage.SymbolConfiguration(pointSize: 119, weight: .semibold)
    let white = NSImage.SymbolConfiguration(hierarchicalColor: .white)
    guard let glyph = symbol.withSymbolConfiguration(point.applying(white)) else {
        fatalError("Cannot configure SF Symbol: \(spec.symbol)")
    }

    let image = NSImage(size: size)
    image.lockFocus()
    guard let context = NSGraphicsContext.current?.cgContext else {
        fatalError("Cannot create icon graphics context")
    }
    context.clear(CGRect(origin: .zero, size: size))
    let rect = NSRect(origin: .zero, size: size)
    let shape = NSBezierPath(roundedRect: rect.insetBy(dx: 1, dy: 1),
                             xRadius: 58, yRadius: 58)
    NSGraphicsContext.saveGraphicsState()
    shape.addClip()
    let gradient = NSGradient(starting: spec.top, ending: spec.bottom)!
    gradient.draw(in: rect, angle: -90)
    NSGraphicsContext.restoreGraphicsState()

    let glyphSize = glyph.size
    let scale = min(140 / glyphSize.width, 140 / glyphSize.height)
    let width = glyphSize.width * scale
    let height = glyphSize.height * scale
    glyph.draw(in: NSRect(x: (size.width - width) / 2,
                          y: (size.height - height) / 2,
                          width: width, height: height))
    image.unlockFocus()

    guard let tiff = image.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: tiff),
          let png = bitmap.representation(using: .png, properties: [:]) else {
        fatalError("Cannot encode icon: \(spec.name)")
    }
    try png.write(to: output.appendingPathComponent(spec.name + ".png"))
}

print("Wrote \(specs.count) Alfred result icons")
