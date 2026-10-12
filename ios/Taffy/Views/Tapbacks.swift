import SwiftUI
import UIKit

/// Tapbacks drawn like Apple Messages: the six glyphs, the round badge on the
/// message's corner (Colin's in blue, the bot's in gray), and the floating bar that picks one. Long-press
/// opens the bar on the iPhone; double-click or right-click, Tapback, opens it on the Mac.

/// One tapback symbol.
struct TapbackGlyph: View {
    let kind: String
    var size: CGFloat = 15

    var body: some View {
        if kind == "laugh" {
            VStack(spacing: -size * 0.28) {
                Text("HA")
                Text("HA").padding(.leading, size * 0.35)
            }
            .font(.system(size: size * 0.62, weight: .heavy, design: .rounded))
            .rotationEffect(.degrees(-12))
        } else {
            Image(systemName: Reaction.symbol(kind))
                .font(.system(size: size, weight: kind == "emphasize" || kind == "question" ? .heavy : .semibold))
        }
    }
}

/// The badge on a message's corner: one round glyph, Colin's in blue and the bot's in gray.
struct TapbackBadge: View {
    let kind: String
    let mine: Bool
    /// Kept for the call sites; the badge no longer has a tail.
    let tailLeading: Bool

    private var fill: Color { mine ? Color(.systemBlue) : Color(.systemGray5) }
    private var ink: Color { mine ? .white : Color(.systemGray) }

    var body: some View {
        TapbackGlyph(kind: kind, size: 14)
            .foregroundStyle(ink)
            .frame(width: 32, height: 32)
            .background(fill, in: Circle())
            .overlay(Circle().stroke(Theme.canvas, lineWidth: 2.5))
    }
}

/// The floating bar of six tapbacks. The one Colin already picked sits in a blue circle.
struct TapbackBar: View {
    let selected: String?
    let onPick: (String) -> Void

    var body: some View {
        HStack(spacing: 4) {
            ForEach(Reaction.kinds, id: \.self) { kind in
                Button { onPick(kind) } label: {
                    TapbackGlyph(kind: kind, size: 19)
                        .foregroundStyle(selected == kind ? .white : Color(.systemGray))
                        .frame(width: 40, height: 40)
                        .background(selected == kind ? Color(.systemBlue) : .clear, in: Circle())
                        .contentShape(Circle())
                }
                .buttonStyle(PressStyle(scale: 0.85))
                .accessibilityLabel(selected == kind ? "Remove \(Reaction.name(kind))" : Reaction.name(kind))
            }
        }
        .padding(.horizontal, 6).padding(.vertical, 5)
        .background(.regularMaterial, in: Capsule())
        .overlay(Capsule().stroke(Color.black.opacity(0.06)))
        .shadow(color: .black.opacity(0.14), radius: 14, y: 6)
    }
}

/// The message whose tapback bar is open, and where its bubble sits on screen. The menu under the bar has Reply and Copy.
struct TapbackTarget {
    let message: ChatMessage
    let botID: String
    let isMine: Bool
    let frame: CGRect
    let canReact: Bool
    /// One more line for the menu, like Show Question on a card's answer.
    var extra: BubbleAction? = nil
}

/// One more action on a bubble's menus, beside Reply and Copy: Show Question on the answer a card turned into.
struct BubbleAction {
    let title: String
    let symbol: String
    let run: () -> Void
}

/// Makes a chat bubble take tapbacks: shows its badges and opens the bar (drawn by TapbackLayer, above the
/// chat, so opening it never moves the message under Colin's finger).
struct Tapbackable: ViewModifier {
    @Environment(AppState.self) private var state
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let message: ChatMessage
    let botID: String
    /// Colin's own bubble (right side): badges on the top-leading corner. Bot bubbles: top-trailing.
    let isMine: Bool
    var extra: BubbleAction? = nil
    @State private var frame: CGRect = .zero

    private var canReact: Bool {
        !message.id.hasPrefix("local-") && !message.id.hasPrefix("feed-") && !message.id.hasPrefix("pending-")
    }

    private func openBar() {
        withAnimation(reduceMotion ? nil : .spring(duration: 0.3, bounce: 0.25)) {
            state.tapback = TapbackTarget(message: message, botID: botID, isMine: isMine, frame: frame, canReact: canReact,
                                          extra: extra)
        }
    }

    private func reply() { state.startReply(to: message, bot: botID) }

    func body(content: Content) -> some View {
        content
            .overlay(alignment: isMine ? .topLeading : .topTrailing) {
                if !message.reactions.isEmpty {
                    HStack(spacing: -10) {
                        ForEach(message.reactions, id: \.self) { r in
                            TapbackBadge(kind: r.kind, mine: r.who == "colin", tailLeading: isMine)
                                .accessibilityLabel("\(r.who == "colin" ? "You" : (state.bot(botID)?.name ?? "The Zip")) reacted \(Reaction.name(r.kind))")
                        }
                    }
                    .offset(x: isMine ? -20 : 20, y: -22)
                    .allowsHitTesting(false)
                }
            }
            // Swipe to reply on the iPhone; Tapback, Reply and More beside it on the Mac (Replies.swift).
            .modifier(Replyable(isMine: isMine, copyText: plainText(message.text), onReply: reply,
                                onTapback: canReact ? openBar : nil, voiceOverAction: false, extra: extra))
            .onGeometryChange(for: CGRect.self) { $0.frame(in: .global) } action: { frame = $0 }
            // VoiceOver: who said it, and Copy and React as actions instead of a long press.
            .accessibilityElement(children: .combine)
            .accessibilityLabel("\(isMine ? "You" : (state.bot(botID)?.name ?? "The Zip")): \(plainText(message.text))")
            .accessibilityAction(named: "Copy") { UIPasteboard.general.string = plainText(message.text) }
            .accessibilityAction(named: "React") { if canReact { openBar() } }
            .accessibilityAction(named: "Reply", reply)
            .modifier(ExtraAction(extra: extra))
            // Long press always opens Reply and Copy; the tapback bar only on messages that can take one yet.
            .modifier(OpenGesture(enabled: !message.id.hasPrefix("local-"), canReact: canReact, text: plainText(message.text),
                                  reply: reply, extra: extra) {
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                openBar()
            })
            .padding(.top, message.reactions.isEmpty ? 0 : 20)
            #if DEBUG
            // Simulator check of where the bar opens: -tapback <message id>.
            .task {
                let args = ProcessInfo.processInfo.arguments
                if let i = args.firstIndex(of: "-tapback"), i + 1 < args.count, args[i + 1] == message.id {
                    try? await Task.sleep(for: .seconds(1.5))
                    openBar()
                }
            }
            #endif
    }
}

/// The open tapback bar and Copy, over the whole chat. A clear layer behind them closes them on a new tap; the
/// finger that opened the bar touched down before this layer existed, so lifting it closes nothing. The bar sits
/// above the message, below it near the top of the screen, or, for a message taller than the screen, at the top
/// of what is visible instead of at the message's own top.
struct TapbackLayer: View {
    @Environment(AppState.self) private var state
    let target: TapbackTarget
    /// This layer already sits between the chat's top bar and the message box; a small margin inside it.
    var topInset: CGFloat = 8
    var bottomInset: CGFloat = 8

    private func close() { withAnimation(.spring(duration: 0.25)) { state.tapback = nil } }

    var body: some View {
        GeometryReader { geo in
            let box = geo.frame(in: .global)
            let f = target.frame.offsetBy(dx: -box.minX, dy: -box.minY)
            let barH: CGFloat = 50, copyH: CGFloat = target.extra == nil ? 88 : 132, gap: CGFloat = 8
            let top = topInset, bottom = box.height - bottomInset
            let above = f.minY - gap - barH
            // Above the message; for a message whose top is scrolled away, at the top of what is visible; for a
            // short message just under the top bar, below it.
            let barY: CGFloat = above >= top ? above
                : (f.minY < top ? top : (f.maxY + gap + barH + gap + copyH <= bottom ? f.maxY + gap : top))
            let copyY: CGFloat = (barY == above && f.maxY + gap + copyH <= bottom) ? f.maxY + gap : barY + barH + gap
            let lead = max(12, f.minX), trail = max(12, box.width - f.maxX)
            ZStack(alignment: .topLeading) {
                Color.black.opacity(0.001)
                    .contentShape(Rectangle())
                    .ignoresSafeArea()
                    .onTapGesture(perform: close)
                    .accessibilityHidden(true)
                if target.canReact {
                    TapbackBar(selected: target.message.reactions.first { $0.who == "colin" }?.kind) { kind in
                        state.react(kind, to: target.message, bot: target.botID)
                        close()
                    }
                    .fixedSize()
                    .padding(.leading, target.isMine ? 0 : lead)
                    .padding(.trailing, target.isMine ? trail : 0)
                    .frame(width: box.width, alignment: target.isMine ? .trailing : .leading)
                    .offset(y: barY)
                    .transition(.scale(scale: 0.6, anchor: target.isMine ? .topTrailing : .topLeading).combined(with: .opacity))
                }
                VStack(spacing: 0) {
                    menuRow("Reply", symbol: "arrowshape.turn.up.left") {
                        state.startReply(to: target.message, bot: target.botID)
                    }
                    Divider()
                    menuRow("Copy", symbol: "doc.on.doc") { UIPasteboard.general.string = plainText(target.message.text) }
                    if let extra = target.extra {
                        Divider()
                        menuRow(extra.title, symbol: extra.symbol, action: extra.run)
                    }
                }
                .frame(width: target.extra == nil ? 168 : 204)
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .shadow(color: .black.opacity(0.12), radius: 12, y: 5)
                .fixedSize()
                .padding(.leading, target.isMine ? 0 : lead)
                .padding(.trailing, target.isMine ? trail : 0)
                .frame(width: box.width, alignment: target.isMine ? .trailing : .leading)
                .offset(y: target.canReact ? copyY : max(top, min(f.maxY + gap, bottom - copyH)))
                .transition(.opacity)
            }
        }
    }

    /// One line of the menu under the bar, like Messages': the word on the left, the symbol on the right.
    private func menuRow(_ title: String, symbol: String, action: @escaping () -> Void) -> some View {
        Button {
            action()
            close()
        } label: {
            HStack {
                Text(title)
                Spacer(minLength: 12)
                Image(systemName: symbol).font(.system(size: 15, weight: .regular))
            }
            .font(Theme.text(16)).foregroundStyle(Theme.ink)
            .padding(.horizontal, 16).frame(height: 43.5)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// Long-press on the iPhone; double-click or right-click on the Mac, like Messages on each.
private struct OpenGesture: ViewModifier {
    let enabled: Bool
    var canReact = true
    let text: String
    var reply: () -> Void = {}
    var extra: BubbleAction? = nil
    let open: () -> Void

    func body(content: Content) -> some View {
        if !enabled {
            content
        } else if Theme.onMac {
            content
                .onTapGesture(count: 2, perform: open)
                .contextMenu {
                    Button(action: reply) { Label("Reply", systemImage: "arrowshape.turn.up.left") }
                    if canReact { Button(action: open) { Label("Tapback", systemImage: "heart") } }
                    Button { UIPasteboard.general.string = text } label: { Label("Copy", systemImage: "doc.on.doc") }
                    if let extra { Button(action: extra.run) { Label(extra.title, systemImage: extra.symbol) } }
                }
        } else {
            content.onLongPressGesture(minimumDuration: 0.35, perform: open)
        }
    }
}

extension View {
    /// Bubble text is selectable on the Mac. On the iPhone a long press opens the tapback bar instead (selection
    /// would take the long press), and the bar has Copy.
    @ViewBuilder func bubbleSelection() -> some View {
        if Theme.onMac { textSelection(.enabled) } else { textSelection(.disabled) }
    }

    func tapbackable(_ message: ChatMessage, botID: String, isMine: Bool, extra: BubbleAction? = nil) -> some View {
        modifier(Tapbackable(message: message, botID: botID, isMine: isMine, extra: extra))
    }
}

/// A bubble's extra action, for VoiceOver.
private struct ExtraAction: ViewModifier {
    let extra: BubbleAction?
    func body(content: Content) -> some View {
        if let extra { content.accessibilityAction(named: extra.title, extra.run) } else { content }
    }
}


/// What a message reads as, without markdown symbols, for Copy.
func plainText(_ markdown: String) -> String {
    let opts = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
    guard let a = try? AttributedString(markdown: markdown, options: opts) else { return markdown }
    return String(a.characters)
}
