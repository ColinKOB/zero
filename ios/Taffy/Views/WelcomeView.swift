import AVFoundation
import SwiftUI

/// The welcome flow. It starts from what Taffy already knows and what is already connected, so it
/// never asks for anything twice, then lets Colin pick what else to set up.
struct WelcomeView: View {
    @Environment(AppState.self) private var state
    @State private var step = 0
    @State private var about: AboutMe?
    @State private var loadError: String?
    @State private var wants: Set<Extra> = []
    @State private var next: AppState.WelcomeNext = .none
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    enum Extra: String, CaseIterable, Identifiable {
        case icon, voice, login, bot
        var id: String { rawValue }
        var title: String {
            switch self {
            case .icon: return "Choose an App Icon"
            case .voice: return "Pick a Voice for Calls"
            case .login: return "Save Another Login"
            case .bot: return "Make a New Zip"
            }
        }
        var detail: String {
            switch self {
            case .icon: return "Choose Zero, Clay Face or The Twist."
            case .voice: return "Choose how your Zips sound when you call them."
            case .login: return "Add a site a Zip should be able to sign in to."
            case .bot: return "Tell Zero what you need and it builds the Zip."
            }
        }
        var symbol: String {
            switch self {
            case .icon: return "app.badge"
            case .voice: return "waveform"
            case .login: return "key"
            case .bot: return "plus.circle"
            }
        }
    }

    private let steps = 5

    var body: some View {
        VStack(spacing: 0) {
            header
            ScrollView {
                Group {
                    switch step {
                    case 0: hello
                    case 1: notes
                    case 2: connected
                    case 3: team
                    default: extras
                    }
                }
                .padding(.horizontal, 24)
                .padding(.top, 12)
                .padding(.bottom, 24)
                .frame(maxWidth: .infinity, alignment: .leading)
                .id(step)
                .transition(reduceMotion ? .opacity : .asymmetric(insertion: .move(edge: .trailing).combined(with: .opacity),
                                                                  removal: .move(edge: .leading).combined(with: .opacity)))
            }
            .scrollIndicators(.hidden)
            footer
        }
        .background(Theme.canvas.ignoresSafeArea())
        .task {
            do { about = try await state.client.aboutMe() } catch { loadError = HubClient.describe(error) }
        }
    }

    // MARK: Chrome

    private var header: some View {
        HStack {
            if step > 0 {
                CircleButton(action: { go(step - 1) }) { Image(systemName: "chevron.left") }
                    .accessibilityLabel("Back")
            } else {
                Color.clear.frame(width: 44, height: 44)
            }
            Spacer()
            HStack(spacing: 6) {
                ForEach(0..<steps, id: \.self) { i in
                    Capsule().fill(i == step ? Theme.ink : Theme.rule)
                        .frame(width: i == step ? 18 : 6, height: 6)
                }
            }
            .animation(.spring(duration: 0.3), value: step)
            .accessibilityLabel("Step \(step + 1) of \(steps)")
            Spacer()
            Button("Skip") { state.finishWelcome(then: .none) }
                .font(Theme.secondary.weight(.medium)).foregroundStyle(Theme.inkSoft)
                .frame(width: 44, height: 44)
        }
        .padding(.horizontal, 20).padding(.top, 8)
    }

    private var footer: some View {
        Button {
            if step < steps - 1 { go(step + 1) } else { finish() }
        } label: {
            Text(primaryTitle)
                .font(Theme.text(17, .semibold)).foregroundStyle(Theme.mineText)
                .frame(maxWidth: .infinity, minHeight: 54)
                .background(Theme.mine, in: Capsule())
        }
        .buttonStyle(PressStyle())
        .padding(.horizontal, 24).padding(.bottom, 12).padding(.top, 8)
        .barFade(.bottom)
    }

    private var primaryTitle: String {
        switch step {
        case 0: return "Let's Go"
        case steps - 1: return wants.isEmpty ? "Start Using Zero" : "Finish"
        default: return "Continue"
        }
    }

    private func go(_ s: Int) {
        withAnimation(reduceMotion ? .none : .spring(duration: 0.4, bounce: 0.15)) { step = s }
    }

    private func finish() {
        var then = next
        if then == .none {
            if wants.contains(.bot) { then = .newBot } else if wants.contains(.login) { then = .saveLogin }
        }
        state.finishWelcome(then: then)
    }

    private func pageTitle(_ title: String, _ subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(Theme.largeTitle).foregroundStyle(Theme.ink)
            Text(subtitle).font(Theme.body).foregroundStyle(Theme.inkSoft)
        }
        .padding(.bottom, 20)
    }

    // MARK: Steps

    private var hello: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .bottom, spacing: -14) {
                BotAvatar(bot: state.bot("builder"), size: 74).offset(y: 6)
                BotAvatar(bot: state.bot("default"), size: 150).zIndex(1)
                BotAvatar(bot: state.bot("school"), size: 74).offset(y: 6)
            }
            .frame(maxWidth: .infinity)
            .padding(.top, 28).padding(.bottom, 36)
            Text(greeting).font(.system(size: 40, weight: .bold)).foregroundStyle(Theme.ink)
                .padding(.bottom, 12)
            Text("Your team is already on your home computer and ready to work. Zero kept everything you have told it, so this takes a minute and asks for nothing twice.")
                .font(Theme.body).foregroundStyle(Theme.inkSoft)
        }
    }

    /// "Hi, Sam." with the name the home knows, or "Hi." while it loads or when it has none.
    private var greeting: String {
        let name = (about?.name ?? "").trimmingCharacters(in: .whitespaces)
        return name.isEmpty ? "Hi." : "Hi, \(name)."
    }

    private var notes: some View {
        VStack(alignment: .leading, spacing: 0) {
            pageTitle("What Zero Knows", "Every Zip reads these notes before it helps you.")
            if let about {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(about.notes, id: \.self) { n in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(n.title).font(Theme.headline).foregroundStyle(Theme.ink)
                            Text(n.text).font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .padding(16)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                    }
                }
                Button {
                    next = .correctNotes
                    finish()
                } label: {
                    Text("Something Is Off").font(Theme.secondary.weight(.medium)).foregroundStyle(Theme.ink)
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
                .padding(.top, 10)
            } else {
                loading
            }
        }
    }

    private var connected: some View {
        VStack(alignment: .leading, spacing: 0) {
            pageTitle("Already Set Up", "These carry over. You do not need to enter them again.")
            if let about {
                VStack(alignment: .leading, spacing: 18) {
                    doneRow("server.rack", "Connected to Your Team", "Your Zips are ready to work.")
                    doneRow("person.2", "Your Accounts", about.connectors.joined(separator: ", "))
                    if !about.alerts.isEmpty {
                        doneRow("bell", "Alerts", "Updates and anything that needs you go to \(about.alerts).")
                    }
                    if !about.logins.isEmpty {
                        doneRow("key", "Saved Logins", about.logins.map { "\(state.bot($0.bot_id)?.name ?? $0.bot_id) on \($0.site)" }
                            .joined(separator: "\n"))
                    }
                    if !about.checkins.isEmpty {
                        VStack(alignment: .leading, spacing: 10) {
                            Label("Check-Ins", systemImage: "calendar").font(Theme.headline).foregroundStyle(Theme.ink)
                                .labelStyle(DoneLabel())
                            ForEach(about.checkins, id: \.self) { c in
                                HStack(spacing: 10) {
                                    BotAvatar(bot: state.bot(c.bot_id), size: 24)
                                    VStack(alignment: .leading, spacing: 1) {
                                        Text(c.name).font(Theme.secondary).foregroundStyle(Theme.ink)
                                        Text(c.when).font(Theme.caption).foregroundStyle(Theme.inkSoft)
                                    }
                                }
                                .padding(.leading, 34)
                            }
                        }
                    }
                }
            } else {
                loading
            }
        }
    }

    private func doneRow(_ symbol: String, _ title: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Label(title, systemImage: symbol).font(Theme.headline).foregroundStyle(Theme.ink).labelStyle(DoneLabel())
            Text(detail).font(Theme.secondary).foregroundStyle(Theme.inkSoft).padding(.leading, 34)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var team: some View {
        VStack(alignment: .leading, spacing: 0) {
            pageTitle("Your Team", "Tap up to three to pin them to the top of your home screen. Anyone who needs you fills the open spots.")
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible()), GridItem(.flexible())], spacing: 22) {
                ForEach(state.bots) { b in
                    Button { state.togglePin(b.id) } label: {
                        VStack(spacing: 8) {
                            BotAvatar(bot: b, size: 64)
                                .overlay(alignment: .topLeading) {
                                    if state.isPinned(b.id) {
                                        Image(systemName: "pin.fill").font(.system(size: 11, weight: .bold))
                                            .foregroundStyle(Theme.mineText)
                                            .frame(width: 24, height: 24).background(Theme.mine, in: Circle())
                                            .overlay(Circle().stroke(Theme.canvas, lineWidth: 2))
                                    }
                                }
                            Text(b.name).font(Theme.secondary.weight(.medium)).foregroundStyle(Theme.ink)
                            Text(b.role).font(Theme.caption).foregroundStyle(Theme.inkSoft)
                                .multilineTextAlignment(.center).lineLimit(4)
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(PressStyle())
                    .accessibilityLabel("\(b.name). \(state.isPinned(b.id) ? "Pinned" : "Not pinned")")
                }
            }
        }
    }

    private var extras: some View {
        VStack(alignment: .leading, spacing: 0) {
            pageTitle("Anything Else?", "Pick what you want to set up now. You can do all of these later in Settings.")
            VStack(spacing: 10) {
                ForEach(Extra.allCases) { e in
                    VStack(alignment: .leading, spacing: 0) {
                        Button {
                            withAnimation(.spring(duration: 0.3)) {
                                if wants.contains(e) { wants.remove(e) } else { wants.insert(e) }
                            }
                        } label: {
                            HStack(spacing: 14) {
                                Image(systemName: e.symbol).font(.system(size: 18, weight: .medium))
                                    .foregroundStyle(Theme.ink).frame(width: 28)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(e.title).font(Theme.headline).foregroundStyle(Theme.ink)
                                    Text(e.detail).font(Theme.caption).foregroundStyle(Theme.inkSoft)
                                }
                                Spacer()
                                Image(systemName: wants.contains(e) ? "checkmark.circle.fill" : "circle")
                                    .font(.system(size: 22)).foregroundStyle(wants.contains(e) ? Theme.ink : Theme.inkSoft.opacity(0.5))
                            }
                            .padding(16)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        if wants.contains(e) && e == .icon {
                            VStack(spacing: 12) { AppIconPicker() }.padding(.horizontal, 16).padding(.bottom, 14)
                        }
                        if wants.contains(e) && e == .voice {
                            VoiceChoice().padding(.horizontal, 16).padding(.bottom, 14)
                        }
                    }
                    .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                }
            }
            if wants.contains(.login) || wants.contains(.bot) {
                Text(wants.contains(.bot) ? "After this, Zero opens so you can describe the Zip you want."
                                          : "After this, the Save Login sheet opens.")
                    .font(Theme.caption).foregroundStyle(Theme.inkSoft).padding(.top, 12)
            }
        }
    }

    private var loading: some View {
        Group {
            if let loadError {
                Text(loadError).font(Theme.secondary).foregroundStyle(Theme.danger)
            } else {
                HStack(spacing: 10) { TypingDots(dot: 7); Text("Getting your details").font(Theme.secondary).foregroundStyle(Theme.inkSoft) }
            }
        }
    }
}

/// A check in front of each thing that is already done.
private struct DoneLabel: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "checkmark.circle.fill").font(.system(size: 20)).foregroundStyle(Color(hex: "#10A35A"))
                .frame(width: 24)
            configuration.title
        }
    }
}

/// ChatGPT voice for calls.
private struct VoiceChoice: View {
    @Environment(AppState.self) private var state

    var body: some View {
        @Bindable var s = state
        Picker("Voice", selection: $s.callVoice) {
            ForEach(CallEngine.chatGPTVoices, id: \.self) { v in
                Text(v.capitalized).tag(v)
            }
        }
        .pickerStyle(.menu)
        .tint(Theme.ink)
    }
}
