import SwiftUI

enum Theme {
    static let onMac = ProcessInfo.processInfo.isMacCatalystApp
    static let device = onMac ? "Mac" : "iPhone"
    static let canvas = shared("Canvas", mac: "#FCFCFC", dark: "#070707")
    static let raised = shared("Raised", mac: "#FCFCFC", dark: "#181818")
    static let composer = shared("Raised", mac: "#FCFCFC", dark: "#2F2F2F")
    static let composerEdge = shared("Rule", mac: "#D9D9D9", dark: "#6D6D6D")
    static let action = shared("Mine", mac: "#070707", dark: "#FAFAFA")
    static let actionGlyph = shared("MineText", mac: "#FCFCFC", dark: "#070707")
    static let ink = Color("Ink")
    static let inkSoft = Color("InkSoft")
    static let bubble = shared("Bubble", mac: "#EEEEEE", dark: "#262626")
    static let field = Color("Field")
    static let rule = shared("Rule", mac: "#E0E0E0", dark: "#292929")
    static let mine = shared("Mine", mac: "#070707", dark: "#5A5A5A")
    static let mineText = shared("MineText", mac: "#FCFCFC", dark: "#DCDCDC")

    static let sidebar = macColor("#F7F7F7", dark: "#111111")
    static let sidebarSelected = macColor("#E1E1E1", dark: "#313131")
    static let profileCircle = macColor("#EBEBEB", dark: "#232323")
    static let profileText = macColor("#818181", dark: "#8E8E93")

    static let chatColumn: CGFloat = 960
    static let bubbleMax: CGFloat = 560

    private static func shared(_ asset: String, mac macHex: String, dark darkHex: String) -> Color {
        if onMac { return macColor(macHex, dark: darkHex) }
        let light = UIColor(named: asset) ?? .systemBackground, dark = UIColor(Color(hex: darkHex))
        return Color(UIColor { $0.userInterfaceStyle == .dark ? dark : light.resolvedColor(with: $0) })
    }

    private static func macColor(_ hex: String, dark darkHex: String) -> Color {
        let light = UIColor(Color(hex: hex)), dark = UIColor(Color(hex: darkHex))
        return Color(UIColor { $0.userInterfaceStyle == .dark ? dark : light })
    }
    static let taffy = Color(hex: "#F2711C")
    static let taffyDeep = Color(hex: "#C2500A")
    static let danger = Color(hex: "#D93036")
    static let amber = macColor("#8F5309", dark: "#F0B45C")
    static let amberFill = macColor("#F2E2C6", dark: "#3A2D18")

    static let largeTitle = Font.largeTitle.weight(.bold)
    static let title = Font.title2.weight(.semibold)
    static let headline = Font.headline
    static let body = Font.body
    static let secondary = Font.subheadline
    static let caption = Font.footnote
    static let micro = Font.caption

    static func text(_ size: CGFloat = 17, _ weight: Font.Weight = .regular) -> Font {
        .system(size: min(UIFontMetrics.default.scaledValue(for: size), size * 1.4), weight: weight)
    }
}

struct PressStyle: ButtonStyle {
    var scale: CGFloat = 0.96
    func makeBody(configuration: Configuration) -> some View {
        Pressed(configuration: configuration, scale: scale)
    }

    private struct Pressed: View {
        let configuration: Configuration
        let scale: CGFloat
        @Environment(\.isEnabled) private var enabled

        var body: some View {
            configuration.label
                .scaleEffect(configuration.isPressed ? scale : 1)
                .opacity(!enabled ? 0.4 : configuration.isPressed ? 0.88 : 1)
                .saturation(enabled ? 1 : 0)
                .animation(.spring(duration: 0.25, bounce: 0.3), value: configuration.isPressed)
        }
    }
}

struct Floating<S: Shape>: ViewModifier {
    let shape: S
    var dark = false
    var fill: Color = Theme.raised
    @Environment(\.colorScheme) private var scheme

    func body(content: Content) -> some View {
        if dark {
            content
                .background(Color.white.opacity(0.10), in: shape)
                .overlay(shape.stroke(Color.white.opacity(0.14), lineWidth: 0.5))
        } else if scheme == .dark {
            content
                .background(fill, in: shape)
                .overlay(shape.stroke(Color.white.opacity(0.10), lineWidth: 0.5))
        } else {
            content
                .background(fill, in: shape)
                .shadow(color: .black.opacity(0.06), radius: 2, y: 1)
                .shadow(color: .black.opacity(0.08), radius: 14, y: 6)
        }
    }
}

extension View {
    func floating<S: Shape>(_ shape: S, dark: Bool = false, fill: Color = Theme.raised) -> some View {
        modifier(Floating(shape: shape, dark: dark, fill: fill))
    }

    func barFade(_ edge: VerticalEdge) -> some View {
        background(alignment: edge == .top ? .top : .bottom) {
            Theme.canvas.ignoresSafeArea(edges: edge == .top ? .top : .bottom)
        }
        .overlay(alignment: edge == .top ? .bottom : .top) {
            LinearGradient(colors: [Theme.canvas, Theme.canvas.opacity(0)],
                           startPoint: edge == .top ? .top : .bottom, endPoint: edge == .top ? .bottom : .top)
                .frame(height: 16)
                .offset(y: edge == .top ? 16 : -16)
                .allowsHitTesting(false)
        }
    }
}

struct CircleButton<Label: View>: View {
    var size: CGFloat = 44
    var dark = false
    var active = false
    let action: () -> Void
    @ViewBuilder let label: () -> Label

    var body: some View {
        Button(action: action) {
            label()
                .font(.system(size: 18, weight: .medium))
                .foregroundStyle(active ? (dark ? Color.black : Theme.mineText) : (dark ? .white : Theme.ink))
                .frame(width: size, height: size)
                .background(active ? (dark ? Color.white : Theme.mine) : (dark ? .clear : Theme.bubble), in: Circle())
                .modifier(DarkFloat(on: dark && !active))
                .contentShape(Circle())
        }
        .buttonStyle(PressStyle(scale: 0.92))
    }
}

private struct DarkFloat: ViewModifier {
    let on: Bool
    func body(content: Content) -> some View {
        if on { content.floating(Circle(), dark: true) } else { content }
    }
}

struct NamePill: View {
    let bot: Bot?
    var dark = false

    var body: some View {
        HStack(spacing: Theme.onMac ? 9 : 8) {
            BotAvatar(bot: bot, size: Theme.onMac ? 22 : 30)
            Text(bot?.name ?? "Zero")
                .font(Theme.onMac ? Theme.text(15) : Theme.headline)
                .foregroundStyle(dark ? .white : Theme.ink)
                .lineLimit(1)
        }
        .padding(.leading, Theme.onMac ? 8 : 7).padding(.trailing, Theme.onMac ? 14 : 16)
        .frame(height: Theme.onMac ? 38 : 44)
        .floating(Capsule(), dark: dark)
    }
}

extension Color {
    static func deepForWhite(_ hex: String) -> Color {
        var s = hex.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("#") { s.removeFirst() }
        var v: UInt64 = 0
        Scanner(string: s).scanHexInt64(&v)
        var rgb = [Double((v >> 16) & 0xFF) / 255, Double((v >> 8) & 0xFF) / 255, Double(v & 0xFF) / 255]
        func lum(_ c: [Double]) -> Double {
            let l = c.map { $0 <= 0.03928 ? $0 / 12.92 : pow(($0 + 0.055) / 1.055, 2.4) }
            return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2]
        }
        var steps = 0
        while 1.05 / (lum(rgb) + 0.05) < 4.5 && steps < 60 {
            rgb = rgb.map { $0 * 0.96 }
            steps += 1
        }
        return Color(red: rgb[0], green: rgb[1], blue: rgb[2])
    }

    init(hex: String) {
        var s = hex.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("#") { s.removeFirst() }
        var v: UInt64 = 0
        Scanner(string: s).scanHexInt64(&v)
        self.init(red: Double((v >> 16) & 0xFF) / 255, green: Double((v >> 8) & 0xFF) / 255,
                  blue: Double(v & 0xFF) / 255)
    }
}

extension Date {
    var listStamp: String {
        let cal = Calendar.current
        let f = DateFormatter()
        if cal.isDateInToday(self) { f.dateFormat = "h:mm a"; return f.string(from: self) }
        if cal.isDateInYesterday(self) { return "Yesterday" }
        if let days = cal.dateComponents([.day], from: self, to: Date()).day, days < 7 {
            f.dateFormat = "EEEE"; return f.string(from: self)
        }
        f.dateFormat = "MMM d"; return f.string(from: self)
    }

    var separatorStamp: String {
        let f = DateFormatter()
        f.dateFormat = "h:mm a"
        let cal = Calendar.current
        if cal.isDateInToday(self) { return "Today " + f.string(from: self) }
        if cal.isDateInYesterday(self) { return "Yesterday " + f.string(from: self) }
        f.dateFormat = "EEE, MMM d h:mm a"
        return f.string(from: self)
    }
}

extension String {
    var possessive: String { hasSuffix("s") ? self + "'" : self + "'s" }
}

/// Hidden navigation bars switch off iOS's swipe-from-the-left-edge to go back. This turns it back on
/// for every pushed screen, so a chat slides away like in Messages.
extension UINavigationController: @retroactive UIGestureRecognizerDelegate {
    override open func viewDidLoad() {
        super.viewDidLoad()
        interactivePopGestureRecognizer?.delegate = self
    }

    public func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
        viewControllers.count > 1
    }

    public func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                                  shouldBeRequiredToFailBy other: UIGestureRecognizer) -> Bool {
        gestureRecognizer === interactivePopGestureRecognizer && viewControllers.count > 1
    }
}


struct TypingDots: View {
    var dot: CGFloat = 7
    var color: Color = Theme.inkSoft
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30, paused: reduceMotion)) { ctx in
            let t = ctx.date.timeIntervalSinceReferenceDate
            HStack(spacing: dot * 0.55) {
                ForEach(0..<3, id: \.self) { i in
                    let phase = (t * 1.4 - Double(i) * 0.18).truncatingRemainder(dividingBy: 1)
                    let lift = reduceMotion ? 0 : max(0, sin(phase * 2 * .pi))
                    Circle().fill(color)
                        .frame(width: dot, height: dot)
                        .opacity(0.35 + 0.65 * lift)
                        .scaleEffect(0.85 + 0.2 * lift)
                }
            }
        }
        .accessibilityHidden(true)
    }
}


struct CapWidth: Layout {
    let max: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let w = Swift.min(proposal.width ?? max, max)
        return subviews.first?.sizeThatFits(ProposedViewSize(width: w, height: proposal.height)) ?? .zero
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        subviews.first?.place(at: bounds.origin, proposal: ProposedViewSize(width: bounds.width, height: bounds.height))
    }
}

/// The top of a sheet: the title and plain text buttons with their full labels ("Cancel", "Save"). Used instead of
/// toolbar items, which the Mac draws as small round buttons that cut the labels off and hide when they're off.
struct SheetBar: View {
    let title: String
    var cancel: String? = "Cancel"
    var onCancel: (() -> Void)? = nil
    var confirm: String? = nil
    var confirmEnabled = true
    var onConfirm: (() -> Void)? = nil

    var body: some View {
        ZStack {
            Text(title)
                .font(Theme.text(17, .semibold)).foregroundStyle(Theme.ink)
                .lineLimit(1)
                .padding(.horizontal, 90)
                .accessibilityAddTraits(.isHeader)
            HStack {
                if let cancel, let onCancel {
                    Button(cancel, action: onCancel)
                        .font(Theme.text(17)).foregroundStyle(Theme.ink)
                        .frame(minWidth: 44, minHeight: 44, alignment: .leading)
                        .buttonStyle(.plain)
                        .keyboardShortcut(.cancelAction)
                }
                Spacer()
                if let confirm, let onConfirm {
                    Button(confirm, action: onConfirm)
                        .font(Theme.text(17, .semibold))
                        .foregroundStyle(confirmEnabled ? Theme.ink : Theme.inkSoft.opacity(0.45))
                        .frame(minWidth: 44, minHeight: 44, alignment: .trailing)
                        .buttonStyle(.plain)
                        .disabled(!confirmEnabled)
                        .keyboardShortcut(.defaultAction)
                }
            }
        }
        .padding(.horizontal, 20)
        .padding(.top, 10)
        .padding(.bottom, 4)
        .frame(maxWidth: .infinity)
        .background(Theme.canvas)
    }
}
