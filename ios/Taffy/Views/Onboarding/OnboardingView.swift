import SwiftUI

/// The first-run onboarding: meet the agent, name it, tell it about yourself, set up the Mac, connect with the
/// code, choose what it may look at and its voice, then see the first Zips it suggests. Connect Google comes last,
/// and only when a picked Zip uses Gmail, Calendar or Drive (Colin, Oct 8 evening). White screens, one idea
/// each, the agent always on stage (docs/specs/2026-10-08-zero-release-onboarding-design.md, screens 1 to 8).
///
/// A home on a server skips the look-around (there is nothing to look at), and a home without Claude or ChatGPT
/// connectors adds its email and calendar right after the consent screen (Sign In with Google, or an app password).
/// Every paired path then asks How Your Zips Should Ask before the voice.
///
/// DEBUG: `-onboarding` shows it on any launch; `-onboarding <step>` starts at a screen (meet, name, talk, mac,
/// noMac, code, consent, why, mail, ask, google, voice, look, found); `-noComputer` takes the path for people without a
/// computer. Add `-fixture` to run without a hub: any code connects and nothing is sent; `-plan shared` (or own) picks
/// the fixture home. `-server <address> -code <code>` fill the code screen.
struct OnboardingView: View {
    @Environment(AppState.self) private var state
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var model: OnboardingModel

    init(rehearsal: Bool = false) {
        var start: OnboardingModel.Step?
        #if DEBUG
        // -onboarding <step>, or -rehearsal <step> for the rehearsal from Settings.
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
        // A zero://pair link or QR code before the home is connected: straight to the code screen, filled in.
        .onChange(of: state.pendingPair) { _, link in
            guard link != nil, !state.paired, !model.rehearsal else { return }
            if [.meet, .name, .talk, .account, .mac, .noMac].contains(model.draft.step) {
                model.draft.noComputer = false
                model.go(.code)
            }
        }
        .task {
            #if DEBUG
            if state.fixtureMode || CommandLine.arguments.contains("-fixture") { HomeModel.shared.settings = .fixture(HomeModel.fixtureVariant) }
            #endif
            if state.pendingPair != nil, !state.paired, [.meet, .name, .talk, .account, .mac, .noMac].contains(model.draft.step) {
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

    /// The screen Back returns to, where going back makes sense. Nothing goes back past the code once connected.
    var previous: Step? {
        switch draft.step {
        case .name: return .meet
        case .talk: return .name
        case .account: return draft.signUpJob == nil ? .talk : nil
        case .mac: return .talk
        case .noMac: return .mac
        case .code: return rehearsal ? .talk : (FrontDoor.signUpOpen ? .account : .mac)
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

    /// Where the consent screen leads. Without a computer, straight to the voice. A home without connectors adds its
    /// email first. A server home has nothing to look around in, and the look-around waits for a yes.
    func afterConsent(agreed: Bool) -> Step {
        if draft.noComputer { return .voice }
        if HomeModel.shared.nativeAccounts { return .mail }
        if HomeModel.shared.serverHome || !agreed { return .ask }
        return .why
    }
}

/// Links the onboarding shares.
enum OnboardingLinks {
    // The newest Zero for Mac, from the public repository's releases.
    static let macDownload = URL(string: "https://github.com/ColinKOB/zero/releases/latest/download/Zero.dmg")!

    static var setupPrompt: String {
        "Please help me set up Zero on this Mac. Download Zero for Mac from \(macDownload.absoluteString), open it, "
            + "and walk me through each step, including signing in. When Zero shows an access code, tell me so I can "
            + "type it into my iPhone. If I have a ChatGPT plan, also help me sign in to Codex with it on this Mac, "
            + "because Zero's calls use ChatGPT's voice through Codex."
    }
}

// MARK: Shared pieces

/// One onboarding screen: an optional Back button, the content, and the buttons pinned at the bottom. Content is
/// held to a readable column on the Mac.
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
                    // Nothing is saved in a rehearsal; tapping the label ends it.
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

/// The smaller choice under the main button: white with a faint border.
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

/// A white card with a faint gray border, the look of the add-on boxes and choice rows.
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
