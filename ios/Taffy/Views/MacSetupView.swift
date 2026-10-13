import CoreImage.CIFilterBuiltins
import SwiftUI
import UIKit

/// The Mac's first run, before the onboarding: welcome, set up Zero on this Mac (with progress), sign in to your AI,
/// then the existing onboarding ("Create Your First Zip") and, at the end, Pair a Phone or Mac. Someone who already has
/// a home elsewhere (Colin's, on his server) chooses Connect to Another Home and signs in or pairs as before.
/// Nothing here sends anyone to a home that is not on their own Mac.
struct MacSetupView: View {
    @Environment(AppState.self) private var state
    @State private var engine = MacEngine.shared
    @State private var screen: Screen = .welcome
    @State private var connecting = false
    @State private var problem: String?

    enum Screen { case welcome, installing, ai }

    /// DEBUG: `-macsetup welcome|ai|preparing|waiting|failed` shows that screen with sample engine answers and runs
    /// nothing, on the Mac or in the simulator, for screenshots. Nil in a release build.
    static var debugScreen: String? {
        #if DEBUG
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "-macsetup"), i + 1 < args.count { return args[i + 1] }
        #endif
        return nil
    }

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
            #if DEBUG
            if let shot = Self.debugScreen {
                if shot != "welcome" {
                    let none = MacEngine.Tool(installed: false, signed_in: false)
                    engine.ai = MacEngine.AIStatus(claude: none, codex: none, ready: false)
                    engine.debugSample = true
                    engine.signingIn = "claude"
                    engine.signIn = shot == "preparing" ? .preparing("Getting Claude ready on this Mac.")
                        : shot == "waiting" ? .waiting
                        : shot == "failed" ? .failed("The sign-in did not finish. Try again.") : .idle
                    if shot == "waiting" { engine.signInPage = URL(string: "https://claude.com") }
                    screen = .ai
                }
                return
            }
            #endif
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

    /// The engine runs on Apple silicon only (zero-engine checks too); an Intel Mac hears it before anything downloads.
    private var intel: Bool {
        #if arch(x86_64)
        return true
        #else
        return false
        #endif
    }

    private var welcome: some View {
        VStack(alignment: .leading, spacing: 12) {
            OnboardingHeadline("Welcome to Zero")
            if intel {
                Text("Zero needs a Mac with Apple silicon, such as an M1 or newer. This Mac has an Intel processor, so Zero can't run on it.")
                    .font(Theme.text(17)).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("Zero is a team of assistants that lives on this Mac. Your chats and everything Zero learns stay here, except what it sends to Claude or ChatGPT to think.")
                    .font(Theme.text(17)).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text("Setup takes a few minutes and needs an internet connection. Zero works while your Mac is awake.")
                    .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
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
                if !intel {
                    OnboardingButton("Set Up Zero on This Mac") { start() }
                        .accessibilityIdentifier("macSetUpHere")
                }
                Button("Connect to Another Home") {
                    UserDefaults.standard.set(true, forKey: MacEngine.otherHomeKey)
                    state.onboarding = false   // the sign-in screen: an account or a pairing code
                    engine.setupShowing = false
                }
                .font(Theme.text(15, .medium)).foregroundStyle(Theme.inkSoft)
                .frame(minHeight: 40)
                .buttonStyle(.plain)   // a text link, not the Mac's gray bordered button
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
/// Codex on this Mac. One button does it all: the engine gets the tool with its official installer if it is missing
/// and runs its own sign-in, which opens the page in the person's browser. This screen notices when they are signed
/// in and goes on by itself, so nobody opens Terminal. Zero never sees the password or keeps the sign-in.
struct MacAIStep: View {
    @Environment(\.openURL) private var openURL
    @State private var engine = MacEngine.shared
    var onReady: () -> Void
    var connecting: Bool

    private var ready: Bool { engine.ai?.ready == true }
    private var label: String { engine.signingIn == "codex" ? "ChatGPT" : "Claude" }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if ready {
                OnboardingHeadline("You Are Signed In")
                Text(engine.ai?.claude.signed_in == true
                     ? "Your Zips will think with your Claude plan."
                     : "Your Zips will think with your ChatGPT plan.")
                    .font(Theme.text(17)).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                OnboardingButton(connecting ? "Getting Zero Ready" : "Continue", busy: connecting) { onReady() }
                    .accessibilityIdentifier("macAIContinue")
            } else if engine.ai == nil {
                OnboardingHeadline("Sign In to Your AI")
                HStack(spacing: 10) {
                    ProgressView()
                    Text("Checking this Mac.").font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                }
            } else {
                switch engine.signIn {
                case .preparing:
                    OnboardingHeadline("Getting \(label) Ready")
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        ProgressView()
                        Text("This takes about a minute. Your browser opens when it is time to sign in.")
                            .font(Theme.text(17)).foregroundStyle(Theme.ink)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .accessibilityElement(children: .combine)
                    otherChoice
                case .waiting:
                    OnboardingHeadline("Sign In in Your Browser")
                    Text("Sign in to \(label) on the page that just opened. Zero continues by itself when you are done.")
                        .font(Theme.text(17)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: 10) {
                        ProgressView()
                        Text("Waiting for you to sign in.").font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                    }
                    .accessibilityElement(children: .combine)
                    if let page = engine.signInPage {
                        OnboardingSecondaryButton("Open the Sign-In Page Again", symbol: "arrow.up.right") { openURL(page) }
                    }
                    otherChoice
                case .idle, .failed:
                    OnboardingHeadline("Sign In to Your AI")
                    Text("Zero works with the Claude or ChatGPT plan you already have. Pick one and sign in on the page that opens.")
                        .font(Theme.text(17)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    if case .failed(let why) = engine.signIn {
                        Text(why).font(Theme.text(15, .medium)).foregroundStyle(Theme.danger)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    VStack(spacing: 0) {
                        planButton("claude", title: "Sign In With Claude", plans: "Claude Pro or Max")
                            .accessibilityIdentifier("macSignInClaude")
                        Divider().padding(.leading, 58)
                        planButton("codex", title: "Sign In With ChatGPT", plans: "ChatGPT Plus or Pro")
                            .accessibilityIdentifier("macSignInChatGPT")
                    }
                    .onboardingCard()
                    Text("Zero never sees your password.")
                        .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                }
            }
        }
        .task { if !engine.debugSample { _ = await engine.checkAI() } }
        // Signed in: go on without another click.
        .onChange(of: ready) { _, now in if now, !connecting { onReady() } }
    }

    private func planButton(_ tool: String, title: String, plans: String) -> some View {
        Button { Task { await engine.signIn(with: tool) } } label: {
            HStack(spacing: 14) {
                Image(tool == "claude" ? "AppLogo-claude" : "AppLogo-openai")
                    .resizable().scaledToFit().frame(width: 26, height: 26)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(Theme.text(17, .semibold)).foregroundStyle(Theme.ink)
                    Text(plans).font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Theme.inkSoft)
            }
            .padding(.horizontal, 18)
            .frame(maxWidth: .infinity, minHeight: 68)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle())
    }

    /// Switch plans while one is still getting ready or waiting.
    private var otherChoice: some View {
        let other = engine.signingIn == "codex" ? "claude" : "codex"
        return Button(other == "codex" ? "Use ChatGPT Instead" : "Use Claude Instead") {
            Task { await engine.signIn(with: other) }
        }
        .font(Theme.text(15, .medium)).foregroundStyle(Theme.inkSoft)
        .frame(minHeight: 40)
        .buttonStyle(.plain)
    }
}

/// Pair a Phone or Mac: a QR code the iPhone's Camera reads, holding this Mac's address on the Wi-Fi and a one-time code.
/// Shown once after the onboarding on a Mac home, and from Settings.
struct PairPhoneView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var engine = MacEngine.shared
    @State private var pairing: MacEngine.Pairing?
    @State private var expires = Date()
    @State private var failed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            OnboardingHeadline("Pair a Phone or Mac")
            Text("Open the Camera on your iPhone and point it at this code, then tap the link it shows. The Zero app on your iPhone opens with the code filled in. Tap Connect.")
                .font(Theme.text(17)).foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
            if let pairing, let image = Self.qr(pairing.link) {
                HStack {
                    Spacer()
                    Image(uiImage: image).interpolation(.none).resizable().frame(width: 220, height: 220)
                        .accessibilityLabel("Pairing code")
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
                              message: Text("Open this on your iPhone or Mac to connect it to Zero on my Mac.")) {
                        Label("Send the Link", systemImage: "square.and.arrow.up")
                    }
                    .font(Theme.text(15, .medium)).foregroundStyle(Theme.taffyDeep)
                }
            } else if failed {
                Text("Zero could not make a code. Check that Zero is running, then try again.")
                    .font(Theme.text(15, .medium)).foregroundStyle(Theme.danger)
            } else {
                ProgressView().frame(maxWidth: .infinity)
            }
            Text("To connect another Mac, send it the link and open it there. Your phone and other Macs work with Zero when they are on the same Wi-Fi as this Mac. Away from home, they show what they last saw.")
                .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
            if pairing?.firewall == true {
                Text("This Mac's firewall is on. If your Mac asks whether to accept incoming connections, choose Allow so your iPhone can reach Zero, even if it names python instead of Zero. If your iPhone still can't connect, open System Settings, then Network, then Firewall, and allow incoming connections.")
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

/// Settings on a Mac home: Pair a Phone or Mac, and Remove Zero From This Mac.
struct MacHomeSection: View {
    @Environment(AppState.self) private var state
    @State private var engine = MacEngine.shared
    @State private var pairing = false
    @State private var confirmRemove = false
    @State private var removing = false
    @State private var problem: String?

    var body: some View {
        Section {
            Button("Pair a Phone or Mac") { pairing = true }
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
