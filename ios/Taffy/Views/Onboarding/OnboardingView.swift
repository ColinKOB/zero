import SwiftUI

struct OnboardingView: View {
    @Environment(AppState.self) private var state
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var model: OnboardingModel

    init(rehearsal: Bool = false) {
        var start: OnboardingModel.Step?
        #if DEBUG
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: rehearsal ? "-rehearsal" : "-onboarding") {
            start = i + 1 < args.count ? OnboardingModel.Step(rawValue: args[i + 1]) ?? .meet : .meet
        }
        #endif
        _model = State(initialValue: OnboardingModel(start: start, rehearsal: rehearsal))
    }

    var body: some View {
        ZStack {
            Color.white.ignoresSafeArea()
            screen
                .id(model.draft.step)
                .transition(reduceMotion ? .opacity : .asymmetric(
                    insertion: .move(edge: .trailing).combined(with: .opacity),
                    removal: .move(edge: .leading).combined(with: .opacity)))
        }
        .environment(\.colorScheme, .light)
        .environment(model)
        .onChange(of: state.pendingPair) { _, link in
            guard link != nil, !state.paired, !model.rehearsal else { return }
            if [.meet, .name, .talk, .account, .mac, .guide, .noMac].contains(model.draft.step) {
                model.draft.noComputer = false
                model.go(.code)
            }
        }
        .task {
            #if DEBUG
            if state.fixtureMode || CommandLine.arguments.contains("-fixture") { HomeModel.shared.settings = .fixture(HomeModel.fixtureVariant) }
            #endif
            if state.pendingPair != nil, !state.paired, [.meet, .name, .talk, .account, .mac, .guide, .noMac].contains(model.draft.step) {
                model.go(.code)
            }
        }
    }

    @ViewBuilder private var screen: some View {
        switch model.draft.step {
        case .meet: MeetScreen()
        case .name: NameScreen()
        case .talk: TalkScreen()
        case .mac: MacScreen()
        case .guide: GuideScreen()
        case .noMac: NoComputerScreen()
        case .account: AccountScreen()
        case .code: CodeScreen()
        case .plan: PlanScreen()
        case .consent: ConsentScreen()
        case .why: WhyScreen()
        case .mail: MailScreen()
        case .ask: AskScreen()
        case .google: GoogleScreen()
        case .voice: VoiceScreen()
        case .look: LookingScreen()
        case .found: FoundScreen()
        }
    }
}

extension OnboardingModel {
    func go(_ step: Step) {
        withAnimation(.smooth(duration: 0.4)) { draft.step = step }
    }

    var previous: Step? {
        switch draft.step {
        case .name: return .meet
        case .talk: return .name
        case .account: return draft.signUpJob == nil ? .talk : nil
        case .mac: return .talk
        case .guide: return .mac
        case .noMac: return .mac
        case .code: return rehearsal ? .talk : (FrontDoor.signUpOpen ? .account : draft.computer == nil ? .mac : .guide)
        case .google: return .found
        case .consent: return draft.noComputer ? .noMac : nil
        case .mail: return draft.mailAfterPicks == true ? .found : nil
        case .ask:
            if HomeModel.shared.nativeAccounts { return .mail }
            return HomeModel.shared.serverHome || draft.consent != "agreed" ? nil : .why
        case .voice: return draft.noComputer ? .consent : .ask
        default: return nil
        }
    }

    func afterConsent(agreed: Bool) -> Step {
        if draft.noComputer { return .voice }
        if HomeModel.shared.nativeAccounts { return .mail }
        if HomeModel.shared.serverHome || !agreed { return .ask }
        return .why
    }
}

enum OnboardingLinks {
    static let macDownload = URL(string: "https://heyzero.app/download")!

    static func setupPrompt(_ computer: OnboardingModel.Computer) -> String {
        switch computer {
        case .alwaysOnMac:
            return """
                I want to set up Zero, a personal assistant app for Mac and iPhone \
                (https://heyzero.app). Zero is already on my iPhone, and now it needs a computer to live \
                on. I am not technical, so please walk me through it one step at a time. Keep each \
                message short, tell me exactly what to click, and wait for me to say I am done before \
                the next step. If something looks different from what you describe, ask me what I see.

                Zero will live on a Mac at home that stays on, like a Mac mini, and I will use it from \
                my iPhone and my other Mac.

                First, help me check that I have what Zero needs: a Mac to leave on at home with Apple \
                silicon (M1 or newer) on macOS 14 or later, and a Claude Pro or Max plan or a ChatGPT \
                Plus or Pro plan.

                These are the steps:
                1. On the Mac that stays on, go to https://heyzero.app/download to download Zero for \
                Mac.
                2. Open the Zero file in my Downloads folder. In the window that appears, drag Zero onto \
                the Applications folder.
                3. Open Zero from the Applications folder. If it offers to move itself to Applications, \
                I click Move to Applications.
                4. Click Set Up Zero on This Mac and wait a few minutes.
                5. Click Sign In With Claude or Sign In With ChatGPT, whichever plan I have, and sign in \
                on the page that opens in my browser. Zero continues by itself after that.
                6. Answer Zero's short welcome questions on the Mac.
                7. When the Mac shows a code, I point my iPhone's Camera at it and tap the link. Zero \
                opens on my iPhone with the code filled in, and I tap Connect.
                8. Keep this Mac ready: in System Settings, open Energy (called Energy Saver on older \
                Macs) and turn on the option that keeps the Mac from sleeping when the display is off, \
                and the option to start up after a power failure if it is there. On a MacBook, these are \
                under Battery, then Options. Then open Users & Groups and set Automatically log in as to \
                my account, so Zero starts again after a restart. If my Mac does not offer that, I log \
                in after each restart.
                9. For my other Mac: on the Mac that stays on, open Zero's Settings with the round \
                button showing my initials at the bottom left of the window (or press Command and \
                comma), click Pair a Phone or Mac, then click Send the Link and send it to my other Mac \
                with AirDrop or Messages. On the other Mac, download Zero, drag it to Applications and \
                open it, but do not click Set Up Zero on This Mac. Then open the link I sent and click \
                Connect. If the link is not clickable, I copy it, click Connect to Another Home if Zero \
                shows it, then Sign In With a Pairing Code, then Paste Link.

                If a Mac asks whether to accept incoming network connections, I click Allow, even if it \
                names python instead of Zero. My iPhone and my other Mac reach Zero when they are on the \
                same Wi-Fi as the Mac that stays on. If the code expires, I click Make a New Code, or \
                open Settings again and click Pair a Phone or Mac.
                """
        case .everydayMac:
            return """
                I want to set up Zero, a personal assistant app for Mac and iPhone \
                (https://heyzero.app). Zero is already on my iPhone, and now it needs a computer to live \
                on. I am not technical, so please walk me through it one step at a time. Keep each \
                message short, tell me exactly what to click, and wait for me to say I am done before \
                the next step. If something looks different from what you describe, ask me what I see.

                Zero will live on the Mac I use every day.

                First, help me check that I have what Zero needs: a Mac with Apple silicon (M1 or newer) \
                on macOS 14 or later, and a Claude Pro or Max plan or a ChatGPT Plus or Pro plan.

                These are the steps:
                1. On my Mac, go to https://heyzero.app/download to download Zero for Mac.
                2. Open the Zero file in my Downloads folder. In the window that appears, drag Zero onto \
                the Applications folder.
                3. Open Zero from the Applications folder. If it offers to move itself to Applications, \
                I click Move to Applications.
                4. Click Set Up Zero on This Mac and wait a few minutes.
                5. Click Sign In With Claude or Sign In With ChatGPT, whichever plan I have, and sign in \
                on the page that opens in my browser. Zero continues by itself after that.
                6. Answer Zero's short welcome questions on the Mac.
                7. When the Mac shows a code, I point my iPhone's Camera at it and tap the link. Zero \
                opens on my iPhone with the code filled in, and I tap Connect.

                If my Mac asks whether to accept incoming network connections, I click Allow, even if it \
                names python instead of Zero. Zero works while my Mac is awake, and my iPhone reaches it \
                when it is on the same Wi-Fi as the Mac. If the code expires, I click Make a New Code.
                """
        case .linux:
            return """
                I want to set up Zero, a personal assistant app for Mac and iPhone \
                (https://heyzero.app). Zero is already on my iPhone, and now it needs a computer to live \
                on. I am not technical, so please walk me through it one step at a time. Keep each \
                message short, tell me exactly what to click, and wait for me to say I am done before \
                the next step. If something looks different from what you describe, ask me what I see.

                Zero will live on a Linux computer at home that stays on. I will run one command in its \
                terminal, and you will help me answer what it asks. The full guide is at \
                https://github.com/ColinKOB/zero/blob/main/docs/install-server.md.

                First, help me check that I have what Zero needs: a computer running Ubuntu 24.04 or \
                another Debian-like Linux, with at least 8 GB of memory and 20 GB of free disk space; a \
                user account on it that can use sudo; and a Claude Pro or Max plan or a ChatGPT Plus or \
                Pro plan.

                These are the steps:
                1. On the Linux computer, log in as myself and open a terminal.
                2. Run this command: curl -fsSL \
                https://raw.githubusercontent.com/ColinKOB/zero/main/install.sh | bash
                3. Type my password when it asks, then wait a few minutes until it asks my name, my time \
                zone and whether to use Tailscale. Tailscale lets me use Zero away from home with my own \
                free Tailscale account. If I say yes, I open the sign-in link it shows, turn on HTTPS \
                for my tailnet if it asks, and install the Tailscale app on my iPhone and sign in with \
                the same account.
                4. Wait while it works, which takes 10 to 20 minutes, and stay nearby: near the end it \
                asks which AI plan to sign in to. I open the link it shows on any device and sign in. \
                For Claude, I paste the code from the page back into the terminal. For ChatGPT, I type \
                the code from the terminal into the page; if the page says device code sign-in is off, I \
                turn on device code login in ChatGPT's Settings, under Security, and try again. Then it \
                shows a QR code.
                5. Point my iPhone's Camera at the QR code and tap the link. Zero opens on my iPhone \
                with the code filled in, and I tap Connect.

                If the code expires, I run bash ~/.local/share/zero/install.sh pair in the terminal for \
                a new one.
                """
        }
    }

    /// Where Open ChatGPT and Open Claude go, after the prompt is copied. Claude opens an empty chat in its app (its
    /// web fallback is `web`): a prompt sent in a link shows a caution banner there, so the person pastes it instead.
    /// ChatGPT has no such banner, so its link carries the prompt; its app may ignore it, and the copy covers that.
    static func chat(_ tool: String, prompt: String) -> URL {
        if tool == "claude" { return URL(string: "claude://claude.ai/new")! }
        let q = prompt.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? ""
        return URL(string: "https://chatgpt.com/?q=\(q)")!
    }

    static func web(_ tool: String) -> URL { URL(string: tool == "claude" ? "https://claude.ai/new" : "https://chatgpt.com/")! }
}

// MARK: Shared pieces

struct OnboardingPage<Content: View, Footer: View>: View {
    @Environment(AppState.self) private var state
    @Environment(OnboardingModel.self) private var model
    @ViewBuilder let content: () -> Content
    @ViewBuilder let footer: () -> Footer

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                if let back = model.previous {
                    CircleButton(action: { model.go(back) }) { Image(systemName: "chevron.left") }
                        .accessibilityLabel("Back")
                }
                Spacer()
                if model.rehearsal {
                    Button { state.rehearsingOnboarding = false } label: {
                        HStack(spacing: 5) {
                            Text("Rehearsal")
                            Image(systemName: "xmark").font(.system(size: 10, weight: .bold))
                        }
                        .font(Theme.text(13, .semibold))
                        .foregroundStyle(Theme.inkSoft)
                        .padding(.horizontal, 12)
                        .frame(height: 30)
                        .background(Color(hex: "#F2F2F2"), in: Capsule())
                    }
                    .buttonStyle(PressStyle())
                    .accessibilityLabel("Rehearsal. Nothing is saved. End rehearsal")
                }
            }
            .frame(height: 44)
            .padding(.horizontal, 20)
            .padding(.top, 4)
            content()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            VStack(spacing: 6) { footer() }
                .padding(.horizontal, 24)
                .padding(.bottom, 12)
                .padding(.top, 8)
        }
        .frame(maxWidth: 560)
        .frame(maxWidth: .infinity)
    }
}

struct OnboardingHeadline: View {
    let text: String
    var alignment: TextAlignment = .leading

    init(_ text: String, alignment: TextAlignment = .leading) {
        self.text = text
        self.alignment = alignment
    }

    var body: some View {
        Text(text)
            .font(Theme.text(36, .bold))
            .foregroundStyle(Theme.ink)
            .multilineTextAlignment(alignment)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: alignment == .center ? .center : .leading)
            .accessibilityAddTraits(.isHeader)
    }
}

struct OnboardingButton: View {
    let title: String
    var busy = false
    var enabled = true
    let action: () -> Void

    init(_ title: String, busy: Bool = false, enabled: Bool = true, action: @escaping () -> Void) {
        self.title = title
        self.busy = busy
        self.enabled = enabled
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                if busy { ProgressView().tint(Theme.mineText) }
                Text(title)
            }
            .font(Theme.text(17, .semibold))
            .foregroundStyle(Theme.mineText)
            .frame(maxWidth: .infinity, minHeight: 54)
            .background(Theme.mine, in: Capsule())
            .opacity(enabled ? 1 : 0.35)
        }
        .buttonStyle(PressStyle())
        .disabled(!enabled || busy)
    }
}

struct OnboardingSecondaryButton: View {
    let title: String
    var symbol: String?
    let action: () -> Void

    init(_ title: String, symbol: String? = nil, action: @escaping () -> Void) {
        self.title = title
        self.symbol = symbol
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                if let symbol { Image(systemName: symbol) }
                Text(title)
            }
            .font(Theme.text(16, .medium))
            .foregroundStyle(Theme.ink)
            .frame(maxWidth: .infinity, minHeight: 48)
            .background(Color.white, in: Capsule())
            .overlay(Capsule().stroke(Color.black.opacity(0.12), lineWidth: 1))
        }
        .buttonStyle(PressStyle())
    }
}

struct OnboardingCard: ViewModifier {
    var selected = false
    var radius: CGFloat = 18

    func body(content: Content) -> some View {
        content
            .background(Color.white, in: RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: radius, style: .continuous)
                .stroke(selected ? Theme.ink : Color.black.opacity(0.1), lineWidth: selected ? 2 : 1))
    }
}

extension View {
    func onboardingCard(selected: Bool = false, radius: CGFloat = 18) -> some View {
        modifier(OnboardingCard(selected: selected, radius: radius))
    }
}
