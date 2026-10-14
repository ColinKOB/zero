import SwiftUI
import UIKit

// MARK: Reply to a specific message (Oct 10)

struct ReplyTarget: Equatable {
    let messageID: String
    let quote: ReplyQuote
    let title: String
}

extension AppState {
    static func quotedWords(_ m: ChatMessage, full: Bool) -> String {
        let shown = full ? (m.full ?? m.text) : m.text
        return m.kind == .call ? "Voice call: \(plainText(shown))" : plainText(shown)
    }

    func startReply(to m: ChatMessage, bot: String) {
        let opened = m.full != nil && ShortReplies.shared.opened.contains(m.id)
        let name = self.bot(bot)?.name ?? "Zero"
        let who: String, title: String
        switch m.kind {
        case .colin:
            who = "person"; title = "Replying to yourself"
        case .bot, .boardUpdate:
            who = "bot"; title = "Replying to \(self.bot(m.botID ?? bot)?.name ?? name)"
        case .team:
            who = "other"; title = "Replying to \(self.bot(m.team?.from ?? "")?.name ?? "the team")"
        case .call:
            who = "other"; title = "Replying to your call"
        default:
            who = "other"; title = "Replying to \(self.bot(m.botID ?? bot)?.name ?? name)"
        }
        let quote = ReplyQuote(text: ReplyLogic.excerpt(Self.quotedWords(m, full: opened)), who: who, messageID: m.id)
        guard !quote.text.isEmpty else { return }
        answering[bot] = nil
        replying[bot] = ReplyTarget(messageID: m.id, quote: quote, title: title)
    }

    func restoreReply(_ target: ReplyTarget?, to bot: String) {
        guard let target, replying[bot] == nil, answering[bot] == nil else { return }
        replying[bot] = target
    }
}

enum ReplyMotion {
    static func header(_ reduceMotion: Bool) -> Animation {
        reduceMotion ? .easeInOut(duration: 0.18) : .spring(response: 0.36, dampingFraction: 0.86)
    }
}

// MARK: Environment: the row under the pointer, the original a quote points at

private struct RowHoveredKey: EnvironmentKey { static let defaultValue = false }
private struct FlashedMessageKey: EnvironmentKey { static let defaultValue: String? = nil }
private struct ShowOriginalKey: EnvironmentKey { static let defaultValue: ShowOriginal? = nil }

struct ShowOriginal {
    let run: (_ quote: ReplyQuote, _ replyID: String) -> Void
}

extension EnvironmentValues {
    var rowHovered: Bool {
        get { self[RowHoveredKey.self] }
        set { self[RowHoveredKey.self] = newValue }
    }
    var flashedMessage: String? {
        get { self[FlashedMessageKey.self] }
        set { self[FlashedMessageKey.self] = newValue }
    }
    var showOriginal: ShowOriginal? {
        get { self[ShowOriginalKey.self] }
        set { self[ShowOriginalKey.self] = newValue }
    }
}

struct RowChrome: ViewModifier {
    let id: String
    @Environment(\.flashedMessage) private var flashed
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var hovering = false

    func body(content: Content) -> some View {
        let lit = flashed == id
        content
            .environment(\.rowHovered, hovering)
            .background {
                RoundedRectangle(cornerRadius: Theme.onMac ? 14 : 18, style: .continuous)
                    .fill(Theme.ink.opacity(0.07))
                    .padding(.horizontal, -8).padding(.vertical, -4)
                    .opacity(lit ? 1 : 0)
                    .allowsHitTesting(false)
            }
            .scaleEffect(lit && !reduceMotion ? 1.02 : 1)
            .animation(reduceMotion ? .easeInOut(duration: 0.2) : .spring(response: 0.35, dampingFraction: 0.7), value: lit)
            .modifier(HoverTracking(on: Theme.onMac, hovering: $hovering))
    }
}

private struct HoverTracking: ViewModifier {
    let on: Bool
    @Binding var hovering: Bool
    func body(content: Content) -> some View {
        if on {
            content.contentShape(Rectangle()).onHover { hovering = $0 }
        } else {
            content
        }
    }
}

// MARK: Swipe, hover and menus on a bubble

struct Replyable: ViewModifier {
    let isMine: Bool
    let copyText: String
    let onReply: () -> Void
    var onTapback: (() -> Void)? = nil
    var ownMenu = false
    var voiceOverAction = true
    var extra: BubbleAction? = nil

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.rowHovered) private var rowHovered
    @GestureState private var swipe = Swipe()
    @State private var horizontal = false
    @State private var barHover = false

    static let threshold: CGFloat = 58

    struct Swipe: Equatable {
        var horizontal: Bool? = nil
        var dx: CGFloat = 0
    }

    private var drag: CGFloat {
        guard swipe.horizontal == true else { return 0 }
        let dx = max(0, swipe.dx)
        return dx <= Self.threshold ? dx : min(Self.threshold + (dx - Self.threshold) * 0.3, Self.threshold + 36)
    }

    private var armed: Bool { swipe.horizontal == true && swipe.dx >= Self.threshold }

    func body(content: Content) -> some View {
        if Theme.onMac { mac(content) } else { phone(content) }
    }

    // MARK: iPhone

    private func phone(_ content: Content) -> some View {
        let progress = min(1, drag / Self.threshold)
        return content
            .offset(x: drag)
            .background(alignment: .leading) {
                Image(systemName: "arrowshape.turn.up.left.fill")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(armed ? Theme.ink : Theme.inkSoft)
                    .frame(width: 30, height: 30)
                    .background(Theme.bubble, in: Circle())
                    .scaleEffect(reduceMotion ? 1 : 0.55 + 0.45 * progress)
                    .opacity(progress * progress)
                    .offset(x: drag / 2 - 15)
                    .accessibilityHidden(true)
                    .allowsHitTesting(false)
            }
            .transaction { t in if reduceMotion, t.animation != nil { t.animation = .easeOut(duration: 0.16) } }
            .simultaneousGesture(swipeGesture)
            .sensoryFeedback(.impact(weight: .light), trigger: armed) { _, now in now }
            .onChange(of: swipe.horizontal) { _, h in if let h { horizontal = h } }
            .modifier(ReplyAction(on: voiceOverAction, onReply: onReply))
    }

    static let backEdge: CGFloat = 44

    private var swipeGesture: some Gesture {
        DragGesture(minimumDistance: 14, coordinateSpace: .global)
            .updating($swipe) { v, s, _ in
                if s.horizontal == nil {
                    let dx = v.translation.width, dy = v.translation.height
                    s.horizontal = dx > 0 && abs(dx) > abs(dy) * 1.3 && v.startLocation.x > Self.backEdge
                }
                s.dx = v.translation.width
            }
            .onEnded { v in
                if horizontal && v.translation.width >= Self.threshold { onReply() }
                horizontal = false
            }
    }

    // MARK: Mac

    private func mac(_ content: Content) -> some View {
        let shown = rowHovered || barHover
        return content
            .overlay(alignment: isMine ? .topLeading : .topTrailing) {
                hoverBar
                    .alignmentGuide(isMine ? .leading : .trailing) { d in isMine ? d.width : 0 }
                    .opacity(shown ? 1 : 0)
                    .allowsHitTesting(shown)
                    .animation(.easeOut(duration: 0.12), value: shown)
            }
            .modifier(MacMenu(on: ownMenu, copyText: copyText, onReply: onReply))
            .modifier(ReplyAction(on: voiceOverAction, onReply: onReply))
    }

    private var hoverBar: some View {
        HStack(spacing: 0) {
            if let onTapback {
                barButton("face.smiling", label: "Tapback", action: onTapback)
            }
            barButton("arrowshape.turn.up.left", label: "Reply", action: onReply)
            Menu {
                Button(action: onReply) { Label("Reply", systemImage: "arrowshape.turn.up.left") }
                if let onTapback { Button(action: onTapback) { Label("Tapback", systemImage: "heart") } }
                Button { UIPasteboard.general.string = copyText } label: { Label("Copy", systemImage: "doc.on.doc") }
                if let extra { Button(action: extra.run) { Label(extra.title, systemImage: extra.symbol) } }
            } label: {
                barGlyph("ellipsis")
            }
            .menuStyle(.button)
            .buttonStyle(.plain)
            .menuIndicator(.hidden)
            .accessibilityLabel("More")
        }
        .padding(isMine ? .trailing : .leading, 6)
        .contentShape(Rectangle())
        .onHover { barHover = $0 }
        .accessibilityHidden(true)
    }

    private func barButton(_ symbol: String, label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) { barGlyph(symbol) }
            .buttonStyle(PressStyle(scale: 0.9))
            .accessibilityLabel(label)
            .help(label)
    }

    private func barGlyph(_ symbol: String) -> some View {
        Image(systemName: symbol)
            .font(.system(size: 13, weight: .medium))
            .foregroundStyle(Theme.inkSoft)
            .frame(width: 26, height: 26)
            .contentShape(Rectangle())
    }
}

private struct ReplyAction: ViewModifier {
    let on: Bool
    let onReply: () -> Void
    func body(content: Content) -> some View {
        if on { content.accessibilityAction(named: "Reply", onReply) } else { content }
    }
}

private struct MacMenu: ViewModifier {
    let on: Bool
    let copyText: String
    let onReply: () -> Void
    func body(content: Content) -> some View {
        if on {
            content.contextMenu {
                Button(action: onReply) { Label("Reply", systemImage: "arrowshape.turn.up.left") }
                Button { UIPasteboard.general.string = copyText } label: { Label("Copy", systemImage: "doc.on.doc") }
            }
        } else {
            content
        }
    }
}

extension View {
    func replyable(_ message: ChatMessage, botID: String, isMine: Bool, state: AppState, ownMenu: Bool = true) -> some View {
        modifier(Replyable(isMine: isMine, copyText: AppState.quotedWords(message, full: false),
                           onReply: { state.startReply(to: message, bot: botID) }, ownMenu: ownMenu))
    }
}

// MARK: The quote above a reply

struct ReplyQuoteView: View {
    let quote: ReplyQuote
    let replyID: String
    let botName: String
    @Environment(\.showOriginal) private var showOriginal

    private var whose: String {
        switch quote.who {
        case "person": return "your message"
        case "bot": return botName
        default: return "an earlier update"
        }
    }

    var body: some View {
        Button { showOriginal?.run(quote, replyID) } label: {
            HStack(alignment: .top, spacing: 6) {
                Image(systemName: "arrowshape.turn.up.left")
                    .font(.system(size: Theme.onMac ? 10 : 11, weight: .semibold))
                    .padding(.top, Theme.onMac ? 2 : 3)
                Text(quote.text)
                    .font(Theme.text(Theme.onMac ? 13 : 14))
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
            }
            .foregroundStyle(Theme.inkSoft)
            .padding(.horizontal, Theme.onMac ? 10 : 12).padding(.vertical, Theme.onMac ? 5 : 7)
            .background(quote.who == "person" ? Theme.mine.opacity(0.1) : Theme.bubble.opacity(0.7),
                        in: RoundedRectangle(cornerRadius: Theme.onMac ? 14 : 16, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Theme.onMac ? 14 : 16, style: .continuous)
                .stroke(Theme.rule, lineWidth: 1))
        }
        .buttonStyle(PressStyle(scale: 0.97))
        .accessibilityLabel("In reply to \(whose): \(quote.text)")
        .accessibilityHint(showOriginal == nil ? "" : "Shows the original message.")
    }
}

// MARK: The message box's header

struct ReplyingHeader: View {
    let target: ReplyTarget
    let onCancel: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "arrowshape.turn.up.left").font(.system(size: 12, weight: .semibold))
            VStack(alignment: .leading, spacing: 1) {
                Text(target.title)
                    .font(Theme.text(Theme.onMac ? 12 : 13, .semibold)).foregroundStyle(Theme.ink)
                Text(target.quote.text).lineLimit(1)
            }
            Spacer(minLength: 4)
            Button(action: onCancel) {
                Image(systemName: "xmark").font(.system(size: 11, weight: .semibold))
                    .frame(width: 26, height: 26).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Cancel Reply")
        }
        .font(Theme.text(Theme.onMac ? 13 : 14, .medium)).foregroundStyle(Theme.inkSoft)
        .padding(.leading, 12).padding(.trailing, 4).padding(.vertical, 5)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.field, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .padding(.horizontal, 6).padding(.top, 6)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(target.title): \(target.quote.text)")
    }
}
