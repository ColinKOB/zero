import SwiftUI

/// A Zip asking to use the Mac, in its chat. On the iPhone it has Allow (Face ID or the passcode) and Not Now; on the
/// Mac itself the request is answered on the card the Mac shows. Once answered it becomes one quiet line, with Stop
/// while the Zip is still working on the Mac.
struct MacAskCard: View {
    @Environment(AppState.self) private var state
    let message: ChatMessage
    let botID: String
    var gap: CGFloat = 48

    @State private var live: MacState?
    @State private var working = false
    @State private var note: String?

    private var ask: MacRequest? { message.macAsk }
    private var bot: Bot? { state.bot(message.botID ?? botID) }
    private var open: Bool {
        guard let ask, ask.state == "open" else { return false }
        if Date().timeIntervalSince1970 > Double(ask.expires) { return false }
        if let live { return live.approvals.contains { $0.id == ask.id } }
        return true
    }

    var body: some View {
        if let ask {
            if open { card(ask) } else { line(ask) }
        }
    }

    private func card(_ ask: MacRequest) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 6) {
                    BotAvatar(bot: bot, size: 18)
                    Text("Allow \(ask.bot) to use your Mac?").font(Theme.text(15, .medium)).foregroundStyle(Theme.ink)
                }
                VStack(alignment: .leading, spacing: 6) {
                    Text(ask.task).font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    if !ask.said.isEmpty {
                        Text("You said: \u{201C}\(ask.said)\u{201D}").font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                            .lineLimit(3)
                    }
                    Text("\(ask.bot) can open things, run commands, see the screen, click and type for this one task. "
                         + "Your Mac shows Stop the whole time and ends it after 30 minutes on its own clock.")
                        .font(Theme.text(13)).foregroundStyle(Theme.inkSoft)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.canvas, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                if let note {
                    Text(note).font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                }
                if Theme.onMac {
                    Text("Allow or decline on the card at the top of this Mac's screen.")
                        .font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                } else if live?.mac_online == false {
                    Text("Your Mac isn't reachable right now. Open Zero on your Mac, then ask again.")
                        .font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                } else {
                    MacAnswerButtons(request: ask, working: $working, note: $note) {
                        Task { await refresh(); await state.loadChat(botID) }
                    }
                }
            }
            .padding(14)
            .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
            .frame(maxWidth: Theme.onMac ? Theme.bubbleMax : .infinity)
            Spacer(minLength: gap)
        }
        .task(id: ask.id) {
            while !Task.isCancelled, open {
                await refresh()
                try? await Task.sleep(for: .seconds(3))
            }
        }
    }

    private func line(_ ask: MacRequest) -> some View {
        let words: String = switch ask.state {
        case "allowed": ask.via == "phone" ? "You allowed \(ask.bot) to use your Mac from your phone."
                                           : "You allowed \(ask.bot) to use your Mac."
        case "denied": "You said no to \(ask.bot) using your Mac."
        case "unreachable": "\(ask.bot) asked to use your Mac, but your Mac wasn't reachable."
        default: "\(ask.bot)'s request to use your Mac timed out."
        }
        let using = live?.grant?.bot_id == ask.bot_id && ask.state == "allowed"
        return HStack(spacing: 8) {
            Image(systemName: "desktopcomputer").font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.inkSoft)
            Text(words).font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
            if using {
                Button("Stop") {
                    Task {
                        note = try? await state.client.stopMac()
                        await refresh()
                    }
                }
                .font(Theme.text(14, .semibold)).foregroundStyle(Theme.danger)
                .buttonStyle(.plain)
                .accessibilityLabel("Stop \(ask.bot) using your Mac")
            }
            Spacer(minLength: gap)
        }
        .task(id: ask.id) { if ask.state == "allowed" { await refresh() } }
    }

    private func refresh() async {
        guard !state.fixtureMode, let s = try? await state.client.macState() else { return }
        live = s
    }
}

/// Not Now and Allow on a Mac request. Allow asks for Face ID or the passcode first, because it hands over the Mac.
struct MacAnswerButtons: View {
    @Environment(AppState.self) private var state
    let request: MacRequest
    @Binding var working: Bool
    @Binding var note: String?
    var light = false
    var done: () -> Void = {}

    var body: some View {
        HStack(spacing: 10) {
            Button { answer(false) } label: {
                Text("Not Now").font(Theme.text(17, .medium))
                    .foregroundStyle(light ? .white : Theme.ink)
                    .frame(maxWidth: .infinity, minHeight: 48)
                    .background(light ? Color.white.opacity(0.16) : Theme.field,
                                in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
            .buttonStyle(PressStyle())
            Button { answer(true) } label: {
                Text("Allow").font(Theme.text(17, .medium)).foregroundStyle(Theme.mineText)
                    .frame(maxWidth: .infinity, minHeight: 48)
                    .background(Theme.mine, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
            .buttonStyle(PressStyle())
        }
        .disabled(working)
        .opacity(working ? 0.6 : 1)
    }

    private func answer(_ allow: Bool) {
        working = true
        Task {
            defer { working = false }
            do {
                note = allow ? try await MacApprover.shared.allow(request, client: state.client)
                             : try await state.client.answerMac(request.id, allow: false)
                done()
            } catch let e as HubClient.HubError {
                note = e.errorDescription
                done()
            } catch {
                note = allow ? "Not allowed. Face ID or the passcode is needed to allow it." : "That didn't go through. Try again."
            }
        }
    }
}

/// The same request on the call screen, while talking to the Zip that asked.
struct MacCallPrompt: View {
    @Environment(AppState.self) private var state
    let botID: String
    @State private var live: MacState?
    @State private var working = false
    @State private var note: String?

    var body: some View {
        Group {
            if let ask = live?.approvals.first(where: { $0.bot_id == botID }) {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Allow \(ask.bot) to use your Mac?").font(Theme.text(17, .semibold)).foregroundStyle(.white)
                    Text(ask.task).font(Theme.text(15)).foregroundStyle(.white.opacity(0.8)).lineLimit(3)
                    if let note { Text(note).font(Theme.text(14)).foregroundStyle(.white.opacity(0.8)) }
                    if live?.mac_online == false {
                        Text("Your Mac isn't reachable right now.").font(Theme.text(15)).foregroundStyle(.white)
                    } else if !Theme.onMac {
                        MacAnswerButtons(request: ask, working: $working, note: $note, light: true) {
                            Task { await refresh() }
                        }
                    } else {
                        Text("Allow or decline on the card at the top of this Mac's screen.")
                            .font(Theme.text(14)).foregroundStyle(.white.opacity(0.8))
                    }
                }
                .padding(16)
                .background(Color.white.opacity(0.12), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                .padding(.horizontal, 24)
                .transition(.opacity)
            }
        }
        .task {
            while !Task.isCancelled {
                await refresh()
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    private func refresh() async {
        guard !state.fixtureMode, !state.demoMode, let s = try? await state.client.macState() else { return }
        withAnimation { live = s }
    }
}
