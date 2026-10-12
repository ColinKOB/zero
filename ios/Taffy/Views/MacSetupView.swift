import CoreImage.CIFilterBuiltins
import SwiftUI
import UIKit

/// The Mac's first run, before the onboarding: welcome, set up Zero on this Mac (with progress), sign in to your AI,
/// then the existing onboarding ("Create Your First Zip") and, at the end, Pair Your Phone. Someone who already has
/// a home elsewhere (Colin's, on his server) chooses Connect to Another Home and signs in or pairs as before.
/// Nothing here sends anyone to a home that is not on their own Mac.
struct MacSetupView: View {
    @Environment(AppState.self) private var state
    @State private var engine = MacEngine.shared
    @State private var screen: Screen = .welcome
    @State private var connecting = false
    @State private var problem: String?

    enum Screen { case welcome, installing, ai }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    AgentStage(look: .zero, mood: mood, size: screen == .welcome ? 200 : 130)
                        .frame(maxWidth: .infinity)
                        .padding(.top, 24)
                    switch screen {
                    case .welcome: welcome
                    case .installing: installing
                    case .ai: MacAIStep(onReady: { Task { await connect() } }, connecting: connecting)
                    }
                    if let problem {
                        Text(problem).font(Theme.text(15, .medium)).foregroundStyle(Theme.danger)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(.horizontal, 28)
                .frame(maxWidth: 560)
                .frame(maxWidth: .infinity)
            }
            .scrollIndicators(.hidden)
            footer
                .padding(.horizontal, 28).padding(.bottom, 20).padding(.top, 8)
                .frame(maxWidth: 560)
        }
        .background(Color.white.ignoresSafeArea())
        .environment(\.colorScheme, .light)
        .task {
            // Set up already (the window was closed before the sign-in): go on to the sign-in.
            if screen == .welcome, let s = await engine.status(), s.installed, s.running {
                engine.phase = .done
                screen = .ai
            }
        }
    }

    private var mood: AgentMood {
        switch engine.phase {
        case .done: return .happy
        case .working: return .watching
        default: return .idle
        }
    }

    private var welcome: some View {
        VStack(alignment: .leading, spacing: 12) {
            OnboardingHeadline("Welcome to Zero")
            Text("Zero is a team of assistants that lives on this Mac. Your chats, your Zips and what they learn stay here, except what they send to Claude or ChatGPT to think.")
                .font(Theme.text(17)).foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
            Text("Setup takes a few minutes and needs an internet connection. Zero works while your Mac is awake.")
                .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var installing: some View {
        VStack(alignment: .leading, spacing: 14) {
            OnboardingHeadline(engine.phase == .done ? "Zero Is Set Up" : "Setting Up Zero")
            switch engine.phase {
            case .failed(let why):
                Text(why).font(Theme.text(17)).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
            case .done:
                Text("Zero now runs in the background whenever you are logged in to this Mac.")
                    .font(Theme.text(17)).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
            default:
                ProgressView(value: Double(max(engine.step, 0)), total: Double(max(engine.total, 1)))
                    .tint(Theme.taffyDeep)
                Text(engine.stepText.isEmpty ? "Getting ready." : engine.stepText)
                    .font(Theme.text(17)).foregroundStyle(Theme.ink)
                    .accessibilityIdentifier("macSetupStep")
                Text("You can leave this window open and do something else.")
                    .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
            }
        }
    }

    @ViewBuilder private var footer: some View {
        switch screen {
        case .welcome:
            VStack(spacing: 8) {
                OnboardingButton("Set Up Zero on This Mac") { start() }
                    .accessibilityIdentifier("macSetUpHere")
                Button("Connect to Another Home") {
                    UserDefaults.standard.set(true, forKey: MacEngine.otherHomeKey)
                    state.onboarding = false   // the sign-in screen: an account or a pairing code
                    engine.setupShowing = false
                }
                .font(Theme.text(15, .medium)).foregroundStyle(Theme.inkSoft)
                .frame(minHeight: 40)
                .accessibilityIdentifier("macOtherHome")
            }
        case .installing:
            switch engine.phase {
            case .failed:
                OnboardingButton("Try Again") { start() }
            case .done:
                OnboardingButton("Continue") { withAnimation { screen = .ai } }
            default:
                EmptyView()
            }
        case .ai:
            EmptyView()
        }
    }

    private func start() {
        problem = nil
        withAnimation { screen = .installing }
        Task {
            if await engine.install() {
                try? await Task.sleep(for: .seconds(1))
                withAnimation { screen = .ai }
            }
        }
    }

    /// Signed in to Claude or ChatGPT: point the Zips at it, connect this app to the engine, and start the
    /// onboarding from its first screen.
    private func connect() async {
        guard !connecting else { return }
        connecting = true
        problem = nil
        defer { connecting = false }
        guard await engine.configureAI() else {
            problem = "Zero could not use your sign-in yet. Try again in a moment."
            return
        }
        if let why = await engine.connectApp(state) {
            problem = why
            return
        }
        OnboardingModel.clearSaved()
        state.onboarding = true
        engine.setupShowing = false
    }
}

/// Sign In to Your AI: Zero thinks with the person's own Claude or ChatGPT plan, through the official Claude Code or
/// Codex signed in on this Mac. Zero never sees the password or keeps the sign-in.
struct MacAIStep: View {
    @State private var engine = MacEngine.shared
    @State private var checking = false
    @State private var copied: String?
    var onReady: () -> Void
    var connecting: Bool

    static let claudeCommand = "curl -fsSL https://claude.ai/install.sh | bash"
    static let claudePage = URL(string: "https://docs.claude.com/en/docs/claude-code/setup")!
    static let codexCommand = "npm install -g @openai/codex"
    static let codexPage = URL(string: "https://developers.openai.com/codex/cli")!

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            OnboardingHeadline(ready ? "You Are Signed In" : "Sign In to Your AI")
            if let ai = engine.ai {
                if ready {
                    Text(ai.claude.signed_in
                         ? "Your Zips will think with your Claude plan."
                         : "Your Zips will think with your ChatGPT plan.")
                        .font(Theme.text(17)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    Text("Zero thinks with your own Claude or ChatGPT plan. Sign in to one of them on this Mac with its official app. Zero never sees your password.")
                        .font(Theme.text(17)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    toolCard(title: "Claude (Pro or Max)", tool: ai.claude, command: Self.claudeCommand,
                             signIn: "claude", page: Self.claudePage, name: "Claude Code")
                    toolCard(title: "ChatGPT (Plus or Pro)", tool: ai.codex, command: Self.codexCommand,
                             signIn: "codex login", page: Self.codexPage, name: "Codex")
                }
            } else {
                HStack(spacing: 10) {
                    ProgressView()
                    Text("Checking for Claude and ChatGPT on this Mac.").font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                }
            }
            if ready {
                OnboardingButton(connecting ? "Getting Zero Ready" : "Continue", busy: connecting) { onReady() }
                    .accessibilityIdentifier("macAIContinue")
            } else if engine.ai != nil {
                OnboardingButton(checking ? "Checking" : "Check Again", busy: checking) { Task { await check() } }
                    .accessibilityIdentifier("macAICheck")
            }
        }
        .task { await check() }
    }

    private var ready: Bool { engine.ai?.ready == true }

    private func check() async {
        checking = true
        _ = await engine.checkAI()
        checking = false
    }

    @ViewBuilder
    private func toolCard(title: String, tool: MacEngine.Tool, command: String, signIn: String, page: URL,
                          name: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(Theme.text(17, .semibold)).foregroundStyle(Theme.ink)
            if tool.installed {
                Text("\(name) is on this Mac but is not signed in. Open Terminal, type the line below and press Return, then sign in on the page that opens.")
                    .font(Theme.text(15)).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                commandRow(signIn)
            } else {
                Text("Install \(name), then sign in. Open Terminal, type the line below and press Return. When it finishes, type the second line, press Return and sign in on the page that opens.")
                    .font(Theme.text(15)).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                commandRow(command)
                commandRow(signIn)
                Link("Read the Official Steps", destination: page)
                    .font(Theme.text(15, .medium)).foregroundStyle(Theme.taffyDeep)
            }
            Button("Open Terminal") { engine.openTerminal() }
                .font(Theme.text(15, .medium)).foregroundStyle(Theme.taffyDeep)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .onboardingCard(selected: false, radius: 16)
    }

    private func commandRow(_ command: String) -> some View {
        HStack(spacing: 10) {
            Text(command).font(.system(size: 14, design: .monospaced)).foregroundStyle(Theme.ink)
                .textSelection(.enabled)
                .lineLimit(2)
            Spacer(minLength: 4)
            Button(copied == command ? "Copied" : "Copy") {
                UIPasteboard.general.string = command
                copied = command
            }
            .font(Theme.text(14, .medium)).foregroundStyle(Theme.taffyDeep)
        }
        .padding(10)
        .background(Theme.field, in: RoundedRectangle(cornerRadius: 10))
    }
}

/// Pair Your Phone: a QR code the iPhone's Camera reads, holding this Mac's address on the Wi-Fi and a one-time code.
/// Shown once after the onboarding on a Mac home, and from Settings.
struct PairPhoneView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var engine = MacEngine.shared
    @State private var pairing: MacEngine.Pairing?
    @State private var expires = Date()
    @State private var failed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            OnboardingHeadline("Pair Your Phone")
            Text("Open the Camera on your iPhone and point it at this code, then tap the link it shows. The Zero app on your iPhone opens and connects to this Mac.")
                .font(Theme.text(17)).foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
            if let pairing, let image = Self.qr(pairing.link) {
                HStack {
                    Spacer()
                    Image(uiImage: image).interpolation(.none).resizable().frame(width: 220, height: 220)
                        .accessibilityLabel("Pairing code for your iPhone")
                    Spacer()
                }
                TimelineView(.periodic(from: .now, by: 1)) { ctx in
                    let left = max(0, Int(expires.timeIntervalSince(ctx.date)))
                    Text(left > 0
                         ? "The code works once and expires in \(left / 60):\(String(format: "%02d", left % 60)). It also lets your iPhone check that it is talking to this Mac."
                         : "This code expired. Make a new one.")
                        .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let url = URL(string: pairing.link) {
                    ShareLink(item: url, subject: Text("Connect to Zero"),
                              message: Text("Open this on your iPhone to connect it to Zero on my Mac.")) {
                        Label("Send the Link to Your iPhone", systemImage: "square.and.arrow.up")
                    }
                    .font(Theme.text(15, .medium)).foregroundStyle(Theme.taffyDeep)
                }
            } else if failed {
                Text("Zero could not make a code. Check that Zero is running, then try again.")
                    .font(Theme.text(15, .medium)).foregroundStyle(Theme.danger)
            } else {
                ProgressView().frame(maxWidth: .infinity)
            }
            Text("Your phone works with Zero when it is on the same Wi-Fi as this Mac. Away from home, it shows what it last saw.")
                .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
            if pairing?.firewall == true {
                Text("This Mac's firewall is on. If your Mac asks whether to accept incoming connections, choose Allow so your iPhone can reach Zero. If your iPhone still can't connect, open System Settings, then Network, then Firewall, and allow incoming connections.")
                    .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 12) {
                OnboardingSecondaryButton("Make a New Code") { Task { await make() } }
                OnboardingButton("Done") {
                    engine.phonePending = false
                    dismiss()
                }
            }
        }
        .padding(28)
        .frame(maxWidth: 560)
        .background(Color.white.ignoresSafeArea())
        .environment(\.colorScheme, .light)
        .task { await make() }
    }

    private func make() async {
        failed = false
        pairing = nil
        if let p = await engine.pairing(minutes: 15) {
            pairing = p
            expires = Date().addingTimeInterval(Double(p.expires_in))
        } else {
            failed = true
        }
    }

    static func qr(_ text: String) -> UIImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(text.utf8)
        filter.correctionLevel = "M"
        guard let out = filter.outputImage?.transformed(by: CGAffineTransform(scaleX: 8, y: 8)),
              let cg = CIContext().createCGImage(out, from: out.extent) else { return nil }
        return UIImage(cgImage: cg)
    }
}

/// Settings on a Mac home: Pair Your Phone, and Remove Zero From This Mac.
struct MacHomeSection: View {
    @Environment(AppState.self) private var state
    @State private var engine = MacEngine.shared
    @State private var pairing = false
    @State private var confirmRemove = false
    @State private var removing = false
    @State private var problem: String?

    var body: some View {
        Section {
            Button("Pair Your Phone") { pairing = true }
                .foregroundStyle(Theme.ink)
            Button(role: .destructive) { confirmRemove = true } label: {
                Text(removing ? "Removing Zero" : "Remove Zero From This Mac")
            }
            .disabled(removing)
            if let problem {
                Text(problem).font(Theme.caption).foregroundStyle(Theme.danger)
            }
        } header: { Text("This Mac") } footer: {
            Text("Zero runs in the background on this Mac. Your phone works with it on the same Wi-Fi. Removing Zero stops it and takes it off this Mac. Claude Code and Codex are yours and stay as they are.")
        }
        .sheet(isPresented: $pairing) { PairPhoneView() }
        .confirmationDialog("Remove Zero From This Mac?", isPresented: $confirmRemove, titleVisibility: .visible) {
            Button("Remove Zero and Keep My Data") { remove(keep: true) }
            Button("Remove Zero and Delete My Data", role: .destructive) { remove(keep: false) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Zero stops and is removed. If you keep your data, your chats, Zips and the files in Zero Work stay on this Mac for next time.")
        }
    }

    private func remove(keep: Bool) {
        removing = true
        problem = nil
        Task {
            if !(await engine.remove(keepData: keep, state: state)) {
                problem = "Zero could not be removed. Quit Zero, open it again and try once more."
            }
            removing = false
        }
    }
}
