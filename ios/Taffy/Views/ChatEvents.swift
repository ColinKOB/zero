import SwiftUI

/// A small centered line in a Zip's chat, like Grok Bot's "Created routine" and "Updated memory for": a quiet gray
/// caption (the same look as NoteLine) with the routine's clock or the Zip's own color beside the name.
struct EventLine: View {
    @Environment(AppState.self) private var state
    let message: ChatMessage

    private var bot: Bot? { message.botID.flatMap { state.bot($0) } }

    var body: some View {
        Group {
            if message.label == "stopped" {
                Label(message.text, systemImage: "stop.circle").foregroundStyle(Theme.inkSoft)
            } else if message.label == "routine" {
                Text("Created routine \(Image(systemName: "clock")) ").foregroundStyle(Theme.inkSoft)
                    + Text(message.text).foregroundStyle(Theme.ink)
            } else {
                HStack(spacing: 6) {
                    Text("Updated memory for").foregroundStyle(Theme.inkSoft)
                    ZipChip(bot: bot)
                }
            }
        }
        .font(Theme.text(13))
        .multilineTextAlignment(.center)
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 24)
        .accessibilityElement(children: .combine)
    }
}

/// A Zip's name beside a dot in its own color, on a pale wash of that color.
struct ZipChip: View {
    let bot: Bot?
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let tint = bot?.tint ?? Theme.taffy
        HStack(spacing: 5) {
            Circle().fill(tint).frame(width: 8, height: 8)
            Text(bot?.name ?? "A Zip").lineLimit(1)
        }
        .font(Theme.text(13, .medium))
        .foregroundStyle(scheme == .dark ? tint : (bot.map { Color.deepForWhite($0.color) } ?? Theme.taffyDeep))
        .padding(.horizontal, 8).padding(.vertical, 3)
        .background(tint.opacity(scheme == .dark ? 0.2 : 0.14), in: Capsule())
    }
}

/// Zips waiting on a teammate's answer: their avatars overlap, then "Pinging Money..." with dots that pulse, until
/// the answer arrives in the chat.
struct PingChip: View {
    @Environment(AppState.self) private var state
    let ping: PingInfo

    private var names: String {
        let all = ping.to.map { state.bot($0)?.name ?? "a teammate" }
        switch all.count {
        case 0, 1: return all.first ?? "a teammate"
        case 2: return "\(all[0]) and \(all[1])"
        default: return all.dropLast().joined(separator: ", ") + " and " + all[all.count - 1]
        }
    }

    var body: some View {
        HStack(spacing: 8) {
            HStack(spacing: -9) {
                ForEach(Array(([ping.from] + ping.to).prefix(3).enumerated()), id: \.offset) { i, id in
                    BotAvatar(bot: state.bot(id), size: 28)
                        .padding(2)
                        .background(Theme.canvas, in: Circle())
                        .zIndex(Double(-i))
                }
            }
            HStack(spacing: 7) {
                TypingDots(dot: 5, color: Theme.amber)
                Text("Pinging \(names)...").font(Theme.text(14, .medium)).foregroundStyle(Theme.amber).lineLimit(1)
            }
            .padding(.horizontal, 12).frame(height: 32)
            .background(Theme.amberFill, in: Capsule())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(state.bot(ping.from)?.name ?? "A Zip") is pinging \(names)")
    }
}
