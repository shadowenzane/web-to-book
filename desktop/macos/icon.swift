import AppKit

let size = 1024
let img = NSImage(size: NSSize(width: size, height: size))
img.lockFocus()
let ctx = NSGraphicsContext.current!.cgContext

// rounded background
let inset: CGFloat = 60
let rect = NSRect(x: inset, y: inset, width: CGFloat(size) - inset*2, height: CGFloat(size) - inset*2)
let path = NSBezierPath(roundedRect: rect, xRadius: 200, yRadius: 200)
let grad = NSGradient(colors: [NSColor(srgbRed: 0.24, green: 0.45, blue: 0.95, alpha: 1),
                               NSColor(srgbRed: 0.55, green: 0.30, blue: 0.90, alpha: 1)])!
grad.draw(in: path, angle: -60)

// page
let pw: CGFloat = 460, ph: CGFloat = 560
let pageRect = NSRect(x: (CGFloat(size)-pw)/2, y: (CGFloat(size)-ph)/2 + 40, width: pw, height: ph)
NSColor.white.setFill()
NSBezierPath(roundedRect: pageRect, xRadius: 36, yRadius: 36).fill()

// folded corner
let fold = NSBezierPath()
let x = pageRect.maxX, y = pageRect.maxY
fold.move(to: NSPoint(x: x-120, y: y))
fold.line(to: NSPoint(x: x, y: y-120))
fold.line(to: NSPoint(x: x, y: y))
fold.close()
NSColor(srgbRed: 0.85, green: 0.88, blue: 0.96, alpha: 1).setFill()
fold.fill()

// text HTML
let para = NSMutableParagraphStyle()
para.alignment = .center
let attrs: [NSAttributedString.Key: Any] = [
    .font: NSFont.systemFont(ofSize: 130, weight: .heavy),
    .foregroundColor: NSColor(srgbRed: 0.20, green: 0.30, blue: 0.70, alpha: 1),
    .paragraphStyle: para
]
("HTML" as NSString).draw(in: NSRect(x: pageRect.minX, y: pageRect.midY + 20, width: pw, height: 160), withAttributes: attrs)

// arrow
let arrow = NSBezierPath()
let cx = CGFloat(size)/2
arrow.move(to: NSPoint(x: cx-70, y: pageRect.minY + 150))
arrow.line(to: NSPoint(x: cx+70, y: pageRect.minY + 150))
arrow.line(to: NSPoint(x: cx+70, y: pageRect.minY + 110))
arrow.line(to: NSPoint(x: cx+140, y: pageRect.minY + 110))
arrow.line(to: NSPoint(x: cx, y: pageRect.minY + 20))
arrow.line(to: NSPoint(x: cx-140, y: pageRect.minY + 110))
arrow.line(to: NSPoint(x: cx-70, y: pageRect.minY + 110))
arrow.close()
NSColor(srgbRed: 0.20, green: 0.30, blue: 0.70, alpha: 1).setFill()
arrow.fill()

img.unlockFocus()

let rep = NSBitmapImageRep(data: img.tiffRepresentation!)!
let data = rep.representation(using: .png, properties: [:])!
try! data.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
print("icon written")
