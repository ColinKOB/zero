import AppKit
import QuartzCore

// The guide's look, in AppKit and Core Animation. The bridge runs inside the Mac Catalyst app, where SwiftUI
// resolves to its iOS build (no NSHostingView), so a bridge that links SwiftUI fails to load at all.

enum GuideColors {
    /// Zero's sunflower yellow and deep charcoal, from the app icon (`ios/scripts/make-icon.py`).
    static let yellow = NSColor(srgbRed: 249 / 255, green: 196 / 255, blue: 16 / 255, alpha: 1)
    static let charcoal = NSColor(srgbRed: 24 / 255, green: 24 / 255, blue: 27 / 255, alpha: 1)
    static let dim = NSColor.black.withAlphaComponent(0.58)
    static let card = NSColor(srgbRed: 0.992, green: 0.992, blue: 0.992, alpha: 1)
    static let ink = NSColor(srgbRed: 0.07, green: 0.07, blue: 0.08, alpha: 1)
    static let inkSoft = NSColor(srgbRed: 0.38, green: 0.38, blue: 0.40, alpha: 1)
    static let rule = NSColor(srgbRed: 0.88, green: 0.88, blue: 0.89, alpha: 1)
    static let stop = NSColor(srgbRed: 0.78, green: 0.15, blue: 0.17, alpha: 1)
}

/// The dimmed layer over the browser window, with a rounded hole, a yellow ring and a slow pulse around it.
final class DimView: NSView {
    private let dim = CAShapeLayer(), outline = CAShapeLayer(), ring = CAShapeLayer(), pulse = CAShapeLayer()
    private let radius: CGFloat = 10
    private var shownHole: CGRect?

    override init(frame: NSRect) {
        super.init(frame: frame)
        let root = CALayer()
        layer = root
        wantsLayer = true
        dim.fillRule = .evenOdd
        dim.fillColor = GuideColors.dim.cgColor
        for (l, color, width) in [(outline, GuideColors.charcoal.withAlphaComponent(0.85), 5.0), (ring, GuideColors.yellow, 3.0),
                                  (pulse, GuideColors.yellow.withAlphaComponent(0.7), 2.0)] {
            l.fillColor = nil
            l.strokeColor = color.cgColor
            l.lineWidth = width
        }
        [dim, outline, ring, pulse].forEach(root.addSublayer)
    }

    required init?(coder: NSCoder) { fatalError() }

    /// `hole` is in this view's coordinates with a top-left origin, like the accessibility frames. Nil dims everything.
    func show(hole topLeftHole: CGRect?) {
        let hole = topLeftHole.map { CGRect(x: $0.minX, y: bounds.height - $0.maxY, width: $0.width, height: $0.height) }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        let full = CGMutablePath()
        full.addRect(bounds)
        dim.frame = bounds
        if let hole {
            full.addRoundedRect(in: hole, cornerWidth: radius, cornerHeight: radius)
            let ringRect = hole.insetBy(dx: -2, dy: -2)
            let ringPath = CGPath(roundedRect: ringRect, cornerWidth: radius + 2, cornerHeight: radius + 2, transform: nil)
            outline.path = ringPath
            ring.path = ringPath
            outline.isHidden = false; ring.isHidden = false
        } else {
            outline.isHidden = true; ring.isHidden = true
        }
        dim.path = full
        CATransaction.commit()
        updatePulse(hole)
    }

    private func updatePulse(_ hole: CGRect?) {
        guard hole != shownHole else { return }
        shownHole = hole
        pulse.removeAllAnimations()
        guard let hole, !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion else { pulse.path = nil; return }
        let from = CGPath(roundedRect: hole.insetBy(dx: -4, dy: -4), cornerWidth: radius + 2, cornerHeight: radius + 2, transform: nil)
        let to = CGPath(roundedRect: hole.insetBy(dx: -18, dy: -18), cornerWidth: radius + 9, cornerHeight: radius + 9, transform: nil)
        pulse.path = from
        let grow = CABasicAnimation(keyPath: "path")
        grow.fromValue = from; grow.toValue = to
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = 1; fade.toValue = 0
        let group = CAAnimationGroup()
        group.animations = [grow, fade]
        group.duration = 1.6
        group.repeatCount = .infinity
        group.timingFunction = CAMediaTimingFunction(name: .easeOut)
        pulse.add(group, forKey: "pulse")
    }
}

/// A capsule button with a closure, drawn to match the app's buttons.
final class PillButton: NSButton {
    enum Kind { case primary, secondary, quiet, row, stopRow }
    private let handler: () -> Void
    private let kind: Kind

    init(_ title: String, _ kind: Kind, _ handler: @escaping () -> Void) {
        self.handler = handler
        self.kind = kind
        super.init(frame: .zero)
        isBordered = false
        wantsLayer = true
        target = self
        action = #selector(fire)
        let color: NSColor = switch kind {
        case .primary: .white
        case .quiet: GuideColors.inkSoft
        case .stopRow: GuideColors.stop
        default: GuideColors.ink
        }
        let weight: NSFont.Weight = (kind == .quiet || kind == .row || kind == .stopRow) ? .medium : .semibold
        let p = NSMutableParagraphStyle()
        p.alignment = (kind == .row || kind == .stopRow) ? .left : .center
        attributedTitle = NSAttributedString(string: title, attributes: [
            .font: NSFont.systemFont(ofSize: 14, weight: weight), .foregroundColor: color, .paragraphStyle: p])
        switch kind {
        case .primary: layer?.backgroundColor = GuideColors.charcoal.cgColor
        case .secondary: layer?.backgroundColor = GuideColors.rule.withAlphaComponent(0.7).cgColor
        case .row, .stopRow: layer?.backgroundColor = GuideColors.rule.withAlphaComponent(0.45).cgColor
        case .quiet: break
        }
    }

    required init?(coder: NSCoder) { fatalError() }

    @objc private func fire() { handler() }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    /// Width for a pill of this title.
    var pillWidth: CGFloat { attributedTitle.size().width + (kind == .quiet ? 6 : 28) }

    override func layout() {
        super.layout()
        layer?.cornerRadius = (kind == .row || kind == .stopRow) ? 8 : bounds.height / 2
    }

    override func draw(_ dirtyRect: NSRect) {
        if kind == .row || kind == .stopRow {
            attributedTitle.draw(in: bounds.insetBy(dx: 10, dy: (bounds.height - attributedTitle.size().height) / 2))
        } else {
            super.draw(dirtyRect)
        }
    }
}

/// The callout card: "Zero's Guide", the step's title and note, the I'm Stuck list, Back and Next, and a pointer.
final class CalloutView: NSView {
    static let shadowMargin: CGFloat = 26
    static let cardWidth: CGFloat = 340
    private static let notch: CGFloat = 11

    var onBack: () -> Void = {}
    var onNext: () -> Void = {}
    var onStuck: (StuckChoice) -> Void = { _ in }
    /// Told when the card changes size, so the panel can move and resize around it.
    var onResize: () -> Void = {}

    private(set) var stuckOpen = false
    private var content = CalloutContent(title: "", body: "", lookFor: "")
    private var arrow: CalloutArrow = .none
    private let card = FlippedView()
    private let pointer = NotchView()
    private let spinner = NSProgressIndicator()

    override var isFlipped: Bool { true }

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        card.wantsLayer = true
        card.layer?.backgroundColor = GuideColors.card.cgColor
        card.layer?.cornerRadius = 14
        card.layer?.borderWidth = 0.5
        card.layer?.borderColor = NSColor.black.withAlphaComponent(0.08).cgColor
        let s = NSShadow()
        s.shadowColor = NSColor.black.withAlphaComponent(0.22)
        s.shadowBlurRadius = 18
        s.shadowOffset = NSSize(width: 0, height: 8)
        card.shadow = s
        addSubview(card)
        addSubview(pointer)
        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.isDisplayedWhenStopped = false
    }

    required init?(coder: NSCoder) { fatalError() }

    func update(content: CalloutContent, arrow: CalloutArrow) {
        guard content != self.content || arrow != self.arrow else { return }
        let resized = content != self.content
        self.content = content
        self.arrow = arrow
        rebuild()
        if resized { onResize() }
    }

    func setStuck(_ open: Bool) {
        stuckOpen = open
        rebuild()
        onResize()
    }

    /// The whole view's size: card, pointer and room for the shadow.
    private(set) var fitted = NSSize.zero

    private func rebuild() {
        card.subviews.forEach { $0.removeFromSuperview() }
        let pad: CGFloat = 16, inner = Self.cardWidth - 2 * pad
        var y = pad

        let dot = NSView(frame: NSRect(x: pad, y: y + 3, width: 9, height: 9))
        dot.wantsLayer = true
        dot.layer?.backgroundColor = GuideColors.yellow.cgColor
        dot.layer?.cornerRadius = 4.5
        card.addSubview(dot)
        let tag = label("Zero's Guide", NSFont.systemFont(ofSize: 12, weight: .semibold), GuideColors.inkSoft, width: 200)
        tag.frame.origin = NSPoint(x: pad + 16, y: y)
        card.addSubview(tag)
        if content.status == .searching {
            spinner.frame = NSRect(x: Self.cardWidth - pad - 16, y: y, width: 16, height: 16)
            card.addSubview(spinner)
            spinner.startAnimation(nil)
        } else {
            spinner.stopAnimation(nil)
        }
        y += 16 + 8

        let title = label(content.heading, NSFont.systemFont(ofSize: 18, weight: .bold), GuideColors.ink, width: inner)
        title.frame.origin = NSPoint(x: pad, y: y)
        card.addSubview(title)
        y += title.frame.height + 6

        let body = label(content.message, NSFont.systemFont(ofSize: 14), GuideColors.ink.withAlphaComponent(0.86), width: inner, lineSpacing: 2)
        body.frame.origin = NSPoint(x: pad, y: y)
        card.addSubview(body)
        y += body.frame.height

        if stuckOpen {
            y += 12
            for choice in StuckChoice.allCases {
                let b = PillButton(choice.title, choice == .stop ? .stopRow : .row) { [weak self] in
                    self?.setStuck(false); self?.onStuck(choice)
                }
                b.frame = NSRect(x: pad, y: y, width: inner, height: 32)
                card.addSubview(b)
                y += 34
            }
            let help = label("Work and school accounts are sometimes blocked from making keys. A personal Google account usually works.",
                             NSFont.systemFont(ofSize: 12), GuideColors.inkSoft, width: inner)
            help.frame.origin = NSPoint(x: pad, y: y + 4)
            card.addSubview(help)
            y += help.frame.height + 4
        }

        y += 14
        let stuck = PillButton(stuckOpen ? "Close" : "I'm Stuck", .quiet) { [weak self] in
            guard let self else { return }
            self.setStuck(!self.stuckOpen)
        }
        stuck.frame = NSRect(x: pad - 2, y: y, width: stuck.pillWidth, height: 32)
        card.addSubview(stuck)
        var x = Self.cardWidth - pad
        let next = PillButton(content.nextTitle, .primary) { [weak self] in self?.onNext() }
        x -= next.pillWidth
        next.frame = NSRect(x: x, y: y, width: next.pillWidth, height: 32)
        card.addSubview(next)
        if content.canGoBack {
            let back = PillButton("Back", .secondary) { [weak self] in self?.onBack() }
            x -= back.pillWidth + 8
            back.frame = NSRect(x: x, y: y, width: back.pillWidth, height: 32)
            card.addSubview(back)
        }
        y += 32 + pad

        let m = Self.shadowMargin, n = Self.notch
        var cardOrigin = NSPoint(x: m, y: m)
        var size = NSSize(width: Self.cardWidth + 2 * m, height: y + 2 * m)
        pointer.isHidden = false
        switch arrow {
        case .up(let ax):
            cardOrigin.y += n; size.height += n
            pointer.edge = .top
            pointer.frame = NSRect(x: m + ax - 11, y: m + 0.5, width: 22, height: n)
        case .down(let ax):
            size.height += n
            pointer.edge = .bottom
            pointer.frame = NSRect(x: m + ax - 11, y: m + y - 0.5, width: 22, height: n)
        case .left(let ay):
            cardOrigin.x += n; size.width += n
            pointer.edge = .left
            pointer.frame = NSRect(x: m + 0.5, y: m + ay - 11, width: n, height: 22)
        case .none:
            pointer.isHidden = true
        }
        card.frame = NSRect(origin: cardOrigin, size: NSSize(width: Self.cardWidth, height: y))
        fitted = size
        setFrameSize(size)
    }

    private func label(_ text: String, _ font: NSFont, _ color: NSColor, width: CGFloat, lineSpacing: CGFloat = 0) -> NSTextField {
        let p = NSMutableParagraphStyle()
        p.lineSpacing = lineSpacing
        let f = NSTextField(wrappingLabelWithString: "")
        f.attributedStringValue = NSAttributedString(string: text, attributes: [.font: font, .foregroundColor: color, .paragraphStyle: p])
        f.isSelectable = false
        f.preferredMaxLayoutWidth = width
        let h = f.cell?.cellSize(forBounds: NSRect(x: 0, y: 0, width: width, height: 10_000)).height ?? 20
        f.frame = NSRect(x: 0, y: 0, width: width, height: ceil(h))
        return f
    }
}

/// A plain view laid out from the top left.
final class FlippedView: NSView {
    override var isFlipped: Bool { true }
}

/// The small triangle from the card to the highlighted element.
final class NotchView: NSView {
    enum Edge { case top, bottom, left }
    var edge: Edge = .top { didSet { needsDisplay = true } }
    override var isFlipped: Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        let r = bounds, p = NSBezierPath()
        switch edge {
        case .top: p.move(to: NSPoint(x: r.minX, y: r.maxY)); p.line(to: NSPoint(x: r.midX, y: r.minY)); p.line(to: NSPoint(x: r.maxX, y: r.maxY))
        case .bottom: p.move(to: NSPoint(x: r.minX, y: r.minY)); p.line(to: NSPoint(x: r.midX, y: r.maxY)); p.line(to: NSPoint(x: r.maxX, y: r.minY))
        case .left: p.move(to: NSPoint(x: r.maxX, y: r.minY)); p.line(to: NSPoint(x: r.minX, y: r.midY)); p.line(to: NSPoint(x: r.maxX, y: r.maxY))
        }
        p.close()
        GuideColors.card.setFill()
        p.fill()
    }
}
