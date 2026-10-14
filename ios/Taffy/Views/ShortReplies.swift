import SwiftUI

// MARK: Short replies (Oct 10)

/// The home's Keep Replies Short switch and the replies opened in full.
@MainActor @Observable
final class ShortReplies {
    static let shared = ShortReplies()

    /// On unless the person turns it off. The hub keeps it, so every device in the home agrees.
    var keepShort = true
    /// Messages showing their full reply, so the chat redrawing or scrolling does not close them.
    var opened: Set<String> = []

    func toggle(_ id: String) {
        if opened.contains(id) { opened.remove(id) } else { opened.insert(id) }
    }

    func load(_ state: AppState) async {
        guard !state.fixtureMode, !state.demoMode,
              let s = try? await state.client.shortReplySettings() else { return }
        keepShort = s.keep_replies_short ?? true
    }

    /// Shows the new state at once and goes back if the hub does not take it.
    func set(_ on: Bool, state: AppState) {
        let before = keepShort
        keepShort = on
        if state.demoMode || state.fixtureMode { return }
        Task {
            do { keepShort = try await state.client.setKeepRepliesShort(on) } catch {
                keepShort = before
                state.lastError = "That change didn't save. Try again."
            }
        }
    }
}

extension HubClient {
    struct ShortReplySettings: Decodable {
        let keep_replies_short: Bool?
    }

    func shortReplySettings() async throws -> ShortReplySettings {
        try await send(try request("/api/settings"), as: ShortReplySettings.self)
    }

    func setKeepRepliesShort(_ on: Bool) async throws -> Bool {
        struct Body: Encodable { let keep_replies_short: Bool }
        let r = try request("/api/settings", method: "PUT", body: Body(keep_replies_short: on))
        return try await send(r, as: ShortReplySettings.self).keep_replies_short ?? on
    }
}

/// A Zip's message text. A reply the hub shortened shows the short version, with a quiet Show Full Reply under it
/// that opens the original in place (and Show Short Reply to close it again).
struct ReplyText: View {
    let message: ChatMessage
    let size: CGFloat

    var body: some View {
        let replies = ShortReplies.shared
        let full = message.full.flatMap { $0.isEmpty || $0 == message.text ? nil : $0 }
        let open = full != nil && replies.opened.contains(message.id)
        VStack(alignment: .leading, spacing: 6) {
            Text(ChatText.render(open ? (full ?? message.text) : message.text))
                .font(Theme.text(size))
                .lineSpacing(Theme.onMac ? 3 : 0)
                .foregroundStyle(Theme.ink)
                .tint(Theme.ink)
                .bubbleSelection()
                .contentTransition(.opacity)
            if full != nil {
                Button {
                    withAnimation(.snappy(duration: 0.25)) { replies.toggle(message.id) }
                } label: {
                    Text(open ? "Show Short Reply" : "Show Full Reply")
                        .font(Theme.text(Theme.onMac ? 12 : 13, .medium))
                        .foregroundStyle(Theme.inkSoft)
                        .frame(minHeight: Theme.onMac ? 20 : 28, alignment: .leading)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityHint(open ? "Shows the short version again." : "Shows the whole reply as it was written.")
            }
            if message.backup == true { BackupNote() }
        }
    }
}

/// A quiet line under a reply the backup model gave (the hub's `backup`, hermes.mark_backup). No alert, no badge.
struct BackupNote: View {
    var hPad: CGFloat = 0

    var body: some View {
        Text("Answered with the backup model.")
            .font(Theme.text(Theme.onMac ? 11 : 12))
            .foregroundStyle(Theme.inkSoft)
            .padding(.horizontal, hPad)
    }
}

/// A Zip's reply as a few small bubbles, one per paragraph, the way a teammate sends a few short texts instead of one
/// long one (Colin, Oct 10, from Grok Bot: "small messages like a teammate would"). One paragraph stays one bubble.
struct ReplyBubbles: View {
    let message: ChatMessage
    let size: CGFloat
    let hPad: CGFloat
    let vPad: CGFloat
    let radius: CGFloat

    /// The paragraphs to show as separate bubbles, or nil when the reply is one bubble.
    static func parts(_ text: String) -> [String]? {
        guard !text.contains("```") else { return nil }
        var parts: [String] = []
        for raw in text.components(separatedBy: "\n\n") {
            let p = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !p.isEmpty else { continue }
            if let last = parts.last, last.hasSuffix(":") { parts[parts.count - 1] = last + "\n\n" + p } else { parts.append(p) }
        }
        return parts.count > 1 && parts.count <= 8 ? parts : nil
    }

    var body: some View {
        let replies = ShortReplies.shared
        let full = message.full.flatMap { $0.isEmpty || $0 == message.text ? nil : $0 }
        let open = full != nil && replies.opened.contains(message.id)
        let text = open ? (full ?? message.text) : message.text
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array((Self.parts(text) ?? [text]).enumerated()), id: \.offset) { _, part in
                Text(ChatText.render(part))
                    .font(Theme.text(size))
                    .lineSpacing(Theme.onMac ? 3 : 0)
                    .foregroundStyle(Theme.ink)
                    .tint(Theme.ink)
                    .bubbleSelection()
                    .padding(.horizontal, hPad).padding(.vertical, vPad)
                    .background(Theme.bubble, in: RoundedRectangle(cornerRadius: radius, style: .continuous))
            }
            if full != nil {
                Button {
                    withAnimation(.snappy(duration: 0.25)) { replies.toggle(message.id) }
                } label: {
                    Text(open ? "Show Short Reply" : "Show Full Reply")
                        .font(Theme.text(Theme.onMac ? 12 : 13, .medium))
                        .foregroundStyle(Theme.inkSoft)
                        .frame(minHeight: Theme.onMac ? 20 : 28, alignment: .leading)
                        .padding(.horizontal, hPad)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityHint(open ? "Shows the short version again." : "Shows the whole reply as it was written.")
            }
            if message.backup == true { BackupNote(hPad: hPad) }
        }
    }
}

/// Settings: Keep Replies Short.
struct KeepRepliesShortSection: View {
    @Environment(AppState.self) private var state

    var body: some View {
        Section {
            Toggle("Keep Replies Short", isOn: Binding(get: { ShortReplies.shared.keepShort },
                                                       set: { ShortReplies.shared.set($0, state: state) }))
                .tint(Theme.taffyDeep)
        } footer: {
            Text("Long replies show as two or three sentences with the main point first. Tap Show Full Reply to read "
                 + "the whole thing. Replies you ask to be detailed, and drafts, always show in full.")
        }
        .task { await ShortReplies.shared.load(state) }
    }
}
