import AVFoundation
import SwiftUI

// Screens 4 to 8: the Mac, the access code, why we ask (with the switches and the voice), looking around,
// and what the agent found.

// MARK: 4. Your computer

struct MacScreen: View {
    @Environment(OnboardingModel.self) private var model
    @State private var copied = false

    var body: some View {
        OnboardingPage {
            GeometryReader { geo in
                VStack(alignment: .leading, spacing: 0) {
                    OnboardingHeadline("Do You Have a Mac Nearby?")
                    Text("That's where I'll do most of my work.")
                        .font(Theme.text(20)).foregroundStyle(Theme.inkSoft)
                        .padding(.top, 8)
                    Spacer(minLength: 0)
                    AgentStage(look: model.draft.look, size: max(120, min(geo.size.height * 0.5, 280)))
                        .frame(maxWidth: .infinity)
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 24)
            }
        } footer: {
            ShareLink(item: OnboardingLinks.macDownload, subject: Text("Zero for Mac"),
                      message: Text("Open this link on your Mac to download Zero.")) {
                Text("Send the Download Link")
                    .font(Theme.text(17, .semibold))
                    .foregroundStyle(Theme.mineText)
                    .frame(maxWidth: .infinity, minHeight: 54)
                    .background(Theme.mine, in: Capsule())
            }
            .buttonStyle(PressStyle())
            OnboardingSecondaryButton(copied ? "Copied" : "Copy a Setup Prompt", symbol: copied ? "checkmark" : "doc.on.doc") {
                UIPasteboard.general.string = OnboardingLinks.setupPrompt
                withAnimation { copied = true }
            }
            .padding(.top, 4)
            Text(copied ? "Paste it into Claude or ChatGPT on your Mac, and it will help you set me up."
                        : "Or ask Claude or ChatGPT on your Mac to set me up for you.")
                .font(Theme.caption).foregroundStyle(Theme.inkSoft)
                .multilineTextAlignment(.center)
                .padding(.top, 2)
            Button("I Have My Code") {
                model.draft.noComputer = false
                model.go(.code)
            }
            .font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink)
            .frame(minHeight: 40)
            Button("I Don't Have a Computer") {
                model.draft.noComputer = true
                model.go(.noMac)
            }
            .font(Theme.text(14, .medium)).foregroundStyle(Theme.inkSoft)
            .frame(minHeight: 36)
        }
        .sensoryFeedback(.success, trigger: copied)
    }
}

// MARK: 4b. No computer

/// For people without a Mac or other computer: what the agent can still do, and why the rest needs a computer.
struct NoComputerScreen: View {
    @Environment(OnboardingModel.self) private var model
    @State private var explaining = false

    var body: some View {
        OnboardingPage {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    AgentStage(look: model.draft.look, size: 160)
                        .frame(maxWidth: .infinity)
                    OnboardingHeadline("That's Okay, I Can Still Help")
                    Text("Without a computer, I work best as a home for your projects. Add your notes, files, photos and links to a Zip, and I'll help you make sense of them, plan, write and think things through. I won't be able to go do things for you, like opening websites, using your accounts or working while your phone is in your pocket.")
                        .font(Theme.text(17)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 12)
            }
            .scrollIndicators(.hidden)
        } footer: {
            OnboardingButton("Continue Without a Computer") { model.go(.consent) }
            OnboardingSecondaryButton("Why?") { explaining = true }
                .padding(.top, 4)
        }
        .sheet(isPresented: $explaining) { WhyComputerSheet() }
        #if DEBUG
        .task { if CommandLine.arguments.contains("-explain") { explaining = true } }   // for screenshots
        #endif
    }
}

private struct WhyComputerSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Why Do I Need a Computer?")
                .font(Theme.text(24, .bold)).foregroundStyle(Theme.ink)
                .accessibilityAddTraits(.isHeader)
            Text("Your Zips do their real work on a computer. That's where they can open websites, sign in to your accounts with your permission and keep going while you're busy. A phone can't run that in the background. If you get a Mac later, you can connect it any time and everything you've added stays.")
                .font(Theme.text(17)).foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            OnboardingButton("Got It") { dismiss() }
        }
        .padding(24)
        .padding(.top, 8)
        .background(Color.white.ignoresSafeArea())
        .environment(\.colorScheme, .light)
        .presentationDetents([.medium])
    }
}

/// A project someone without a computer could start a Zip for.
// PLACEHOLDER: fixed ideas until project Zips exist; nothing is created from them yet.
struct ProjectIdea: Identifiable {
    let id: String
    let name: String
    let pitch: String
    let symbol: String

    static let all: [ProjectIdea] = [
        .init(id: "home", name: "Home projects", pitch: "Plans, photos and receipts for work around the house.",
              symbol: "house"),
        .init(id: "trip", name: "A trip", pitch: "Ideas, bookings and a day by day plan in one place.", symbol: "airplane"),
        .init(id: "meals", name: "Meals and recipes", pitch: "Recipes you like, weekly plans and shopping lists.",
              symbol: "fork.knife"),
        .init(id: "writing", name: "Writing", pitch: "Drafts and notes for anything you're working on.",
              symbol: "pencil.line"),
    ]
}

// MARK: 5. The code

struct CodeScreen: View {
    @Environment(AppState.self) private var state
    @Environment(OnboardingModel.self) private var model
    @State private var code = ""
    /// The zero://pair link that filled this screen; its fingerprint goes with the pairing for that address only.
    @State private var link: PairLink?
    @State private var busy = false
    @State private var connected = false
    @State private var server = HubClient.savedServer
    @State private var showServer = HubClient.savedServer.isEmpty   // open when this device has no home yet
    @FocusState private var typing: Bool

    private var cleaned: String { code.uppercased().filter { $0.isLetter || $0.isNumber } }
    /// A rehearsal runs on a device that is already set up, so it never pairs again.
    private var already: Bool { model.rehearsal }

    var body: some View {
        OnboardingPage {
            GeometryReader { geo in
                VStack(spacing: 0) {
                    OnboardingHeadline("Type Your Pairing Code")
                    Text(Theme.onMac ? "Make one in Zero on a device that is already signed in, under Settings and Add a Device. If someone set up Zero for you, open the link they sent."
                                     : "Make one in Zero on a device that is already signed in, under Settings and Add a Device. If someone set up Zero for you, scan the code they gave you.")
                        .font(Theme.text(18)).foregroundStyle(Theme.inkSoft)
                        .fixedSize(horizontal: false, vertical: true)   // never cut short; the art below gives way
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.top, 8)
                    Spacer(minLength: 0)
                    // Whatever room is left after the text and the field, so nothing slides under the keyboard.
                    let stage = max(0, min(geo.size.height - 280, 240))
                    AgentStage(look: model.draft.look, mood: connected ? .happy : (typing ? .watching : .idle),
                               size: stage)
                        .opacity(stage < 72 ? 0 : 1)
                    if already {
                        Label("This \(Theme.device) is already connected.", systemImage: "checkmark.circle.fill")
                            .font(Theme.text(17, .semibold))
                            .foregroundStyle(Color(hex: "#10A35A"))
                            .padding(.top, 16)
                    } else {
                    TextField("", text: $code, prompt: Text("ABCD 2345").foregroundStyle(Color.black.opacity(0.2)))
                        .font(.system(size: 30, weight: .semibold, design: .monospaced))
                        .tracking(3)
                        .foregroundStyle(Theme.ink)
                        .multilineTextAlignment(.center)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .textContentType(.oneTimeCode)
                        .submitLabel(.go)
                        .focused($typing)
                        .onSubmit(connect)
                        .disabled(connected)
                        .frame(width: 290, height: 64)
                        .onboardingCard(selected: typing, radius: 16)
                        .padding(.top, 16)
                        .accessibilityLabel("Access code")
                    Group {
                        if connected {
                            Text("We're connected.").foregroundStyle(Color(hex: "#10A35A"))
                        } else if let e = state.lastError {
                            VStack(spacing: 6) {
                                Text(e).foregroundStyle(Theme.danger)
                                if state.pairUnreachable {
                                    Text("Check that your phone is on the same Wi-Fi as the computer running Zero, or that your private network app is on, and try again.")
                                        .foregroundStyle(Theme.inkSoft)
                                }
                            }
                            .fixedSize(horizontal: false, vertical: true)
                        } else {
                            Text(" ")
                        }
                    }
                    .font(Theme.text(15, .medium))
                    .multilineTextAlignment(.center)
                    .padding(.top, 12)
                    }
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 24)
            }
        } footer: {
            if already {
                OnboardingButton("Continue") { model.go(.consent) }
            } else {
            OnboardingButton(busy ? "Connecting" : "Connect", busy: busy,
                             enabled: cleaned.count >= 6 && !connected && !server.trimmingCharacters(in: .whitespaces).isEmpty) {
                connect()
            }
            if !connected {
                PairLinkButton { link in
                    fill(link)
                    connect()
                }
                .padding(.top, 4)
            }
            Button(showServer ? "Hide Connection Address" : "Connection Address") { showServer.toggle() }
                .font(Theme.caption).foregroundStyle(Theme.inkSoft)
                .frame(minHeight: 36)
            if showServer {
                TextField("Connection address", text: $server)
                    .font(Theme.text(13)).multilineTextAlignment(.center)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .keyboardType(.URL)
                if HubClient.savedServer.isEmpty {
                    Text("The link with your code fills this in for you.")
                        .font(Theme.caption).foregroundStyle(Theme.inkSoft)
                        .multilineTextAlignment(.center)
                }
            }
            }
        }
        .onAppear {
            state.lastError = nil
            state.pairUnreachable = false
            if let link = state.pendingPair { fill(link) }
            #if DEBUG
            // -server <address> -code <code>: the code screen filled in, for the simulator and the UI tests.
            let args = CommandLine.arguments
            if let i = args.firstIndex(of: "-server"), i + 1 < args.count { server = args[i + 1]; showServer = true }
            if let i = args.firstIndex(of: "-code"), i + 1 < args.count { code = args[i + 1] }
            #endif
        }
        .onChange(of: state.pendingPair) { _, link in if let link { fill(link) } }
        .sensoryFeedback(.success, trigger: connected)
    }

    /// The address and code from a zero://pair link or QR code.
    private func fill(_ link: PairLink) {
        self.link = link
        if !link.server.isEmpty { server = link.server; showServer = true }
        if !link.code.isEmpty { code = link.code }
        state.pendingPair = nil
        state.lastError = nil
        state.pairUnreachable = false
    }

    private func connect() {
        guard cleaned.count >= 6, !busy, !connected else { return }
        typing = false
        Task {
            busy = true
            if state.fixtureMode {
                try? await Task.sleep(for: .milliseconds(600))   // fixture: any code connects
                connected = true
            } else {
                // The code shown on the Mac is the hub's pairing code.
                await state.pair(code: cleaned, server: server,
                                 fingerprint: link?.server == server ? link?.fingerprint : nil)
                connected = state.paired
            }
            busy = false
            guard connected else { return }
            try? await Task.sleep(for: .seconds(1.6))
            model.go(.plan)
        }
    }
}

// MARK: 6. Why we ask, and the switches

struct WhyScreen: View {
    @Environment(OnboardingModel.self) private var model

    var body: some View {
        @Bindable var model = model
        OnboardingPage {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    AgentStage(look: model.draft.look, size: 150)
                        .frame(maxWidth: .infinity)
                    OnboardingHeadline("Why We Ask")
                    Text("I'm a lot more useful when I know a bit about your life. If you're okay with it, I'll look around your Mac once to see what you work on, who's important to you and what your weeks look like.")
                        .font(Theme.text(17)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(alignment: .top, spacing: 12) {
                        Image(systemName: "lock.fill")
                            .font(.system(size: 17, weight: .semibold))
                            .foregroundStyle(Theme.ink)
                            .padding(.top, 2)
                        Text("Nothing I learn about you is sent to anyone but the AI company of your own plan, and only when you have agreed.")
                            .font(Theme.text(17, .semibold)).foregroundStyle(Theme.ink)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color(hex: "#F5F5F5"), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                    Text("Turn off anything you would rather I skip.")
                        .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                        .padding(.top, 4)
                    VStack(spacing: 0) {
                        ForEach(LookSource.all) { source in
                            Toggle(isOn: Binding(get: { model.draft.sources[source.id] ?? false },
                                                 set: { model.draft.sources[source.id] = $0 })) {
                                HStack(alignment: .top, spacing: 12) {
                                    Image(systemName: source.symbol)
                                        .font(.system(size: 17, weight: .medium))
                                        .foregroundStyle(Theme.ink)
                                        .frame(width: 26)
                                        .padding(.top, 1)
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(source.title).font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink)
                                        Text(source.detail).font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                                            .fixedSize(horizontal: false, vertical: true)
                                    }
                                }
                            }
                            .tint(Color(hex: "#10A35A"))
                            .padding(.horizontal, 16)
                            .padding(.vertical, 13)
                            if source.id != LookSource.all.last?.id {
                                Divider().padding(.leading, 54)
                            }
                        }
                    }
                    .onboardingCard()
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 12)
            }
            .scrollIndicators(.hidden)
        } footer: {
            OnboardingButton("Continue") { model.go(.ask) }
        }
    }
}

// MARK: 6b. Pick my voice

struct VoiceScreen: View {
    @Environment(OnboardingModel.self) private var model
    @Environment(AppState.self) private var state
    @State private var sampler = VoiceSampler()
    @State private var settingUpVoice = false

    var body: some View {
        OnboardingPage {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    AgentStage(look: model.draft.look, mood: sampler.playing != nil ? .talking : .idle, size: 150)
                        .frame(maxWidth: .infinity)
                    OnboardingHeadline("Pick My Voice")
                    Text("Tap Play to hear each one, then pick the one you like.")
                        .font(Theme.text(17)).foregroundStyle(Theme.inkSoft)
                        .fixedSize(horizontal: false, vertical: true)
                    // Above the voices, so it shows without scrolling on iPhone and Mac (voice setup QA, L1).
                    // People without a computer have no Mac to keep a Google key on, and a home on a server has none
                    // either, so the free voice waits there until Gemini calls exist.
                    if !model.draft.noComputer && !HomeModel.shared.serverHome { googleVoice }
                    else if HomeModel.shared.isShared || HomeModel.shared.serverHome {
                        Text(VoiceWording.chatGPTPlan)
                            .font(Theme.text(14)).foregroundStyle(Theme.ink.opacity(0.8))
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(14)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color(hex: "#F5F5F5"), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                    }
                    ForEach(AgentVoice.all) { v in
                        let on = model.draft.voice == v.id
                        HStack(spacing: 14) {
                            Image(systemName: on ? "checkmark.circle.fill" : "circle")
                                .font(.system(size: 22))
                                .foregroundStyle(on ? Theme.ink : Color.black.opacity(0.2))
                            VStack(alignment: .leading, spacing: 2) {
                                Text(v.name).font(Theme.text(17, .semibold)).foregroundStyle(Theme.ink)
                                Text(v.detail).font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                            }
                            Spacer()
                            Button {
                                model.draft.voice = v.id
                                sampler.toggle(v, line: "Hey, I'm \(model.agentName). This is what I'll sound like when we talk.")
                            } label: {
                                Label(sampler.playing == v.id ? "Stop" : "Play",
                                      systemImage: sampler.playing == v.id ? "stop.fill" : "play.fill")
                                    .font(Theme.text(15, .semibold))
                                    .foregroundStyle(Theme.ink)
                                    .padding(.horizontal, 14)
                                    .frame(minHeight: 38)
                                    .background(Color(hex: "#F2F2F2"), in: Capsule())
                            }
                            .buttonStyle(PressStyle(scale: 0.94))
                            .accessibilityLabel("\(sampler.playing == v.id ? "Stop" : "Play") \(v.name)")
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 12)
                        .onboardingCard(selected: on)
                        .contentShape(Rectangle())
                        .onTapGesture { model.draft.voice = v.id }
                        .accessibilityElement(children: .contain)
                        .accessibilityAddTraits(on ? .isSelected : [])
                    }
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 12)
            }
            .scrollIndicators(.hidden)
        } footer: {
            OnboardingButton("Use \(AgentVoice.all.first { $0.id == model.draft.voice }?.name ?? "This Voice")") {
                sampler.stop()
                if !model.rehearsal {
                    UserDefaults.standard.set(model.draft.voice, forKey: "leadVoice")
                    if let call = AgentVoice.all.first(where: { $0.id == model.draft.voice })?.callVoice,
                       CallEngine.chatGPTVoices.contains(call) {
                        state.callVoice = call
                    }
                }
                // Looking around sends what I find to your AI company, so it waits for a yes.
                let agreed = model.rehearsal ? model.draft.consent == "agreed" : state.aiConsent == .agreed
                model.go(model.draft.noComputer || !agreed || HomeModel.shared.serverHome ? .found : .look)
            }
        }
        .onDisappear { sampler.stop() }
        .sheet(isPresented: $settingUpVoice) { VoiceSetupView() }
    }

    /// The free Google voice (VoiceSetupView). Its key lives on the Mac, so the guided setup opens only there; the
    /// iPhone says where to find it. A rehearsal saves nothing, so it points to Settings instead of opening it. The
    /// first sentence is the same on Pick My Voice, Set Up Voice and Settings (voice setup QA, L2).
    @ViewBuilder private var googleVoice: some View {
        let here = Theme.onMac && !model.rehearsal
        HStack(alignment: .center, spacing: 12) {
            Text(VoiceWording.chatGPTPlan + (here
                 ? " If you don't have one, you can set up a free voice from Google now."
                 : Theme.onMac
                 ? " If you don't have one, you can set up a free voice from Google later, in Settings under Set Up Voice."
                 : " If you don't have one, you can set up a free voice from Google on your Mac later, in Settings under Set Up Voice."))
                .font(Theme.text(14)).foregroundStyle(Theme.ink.opacity(0.8))
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            if here {
                Button {
                    sampler.stop()
                    settingUpVoice = true
                } label: {
                    Label(GeminiKeyStore.isSet ? "Key Saved" : "Set Up", systemImage: GeminiKeyStore.isSet ? "checkmark" : "waveform")
                        .font(Theme.text(15, .semibold)).foregroundStyle(Theme.ink)
                        .padding(.horizontal, 14).frame(minHeight: 38)
                        .background(Color.white, in: Capsule())
                        .overlay(Capsule().stroke(Color.black.opacity(0.12), lineWidth: 1))
                }
                .buttonStyle(PressStyle(scale: 0.94))
                .fixedSize()
                .accessibilityLabel(GeminiKeyStore.isSet ? "Your Google key is saved" : "Set up a free Google voice")
            }
        }
        .padding(14)
        .background(Color(hex: "#F5F5F5"), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .padding(.bottom, 4)
    }
}

/// The ChatGPT plan line, worded once for Pick My Voice, Set Up Voice and Settings (voice setup QA, L2). It says
/// where the voice comes from, so a person running their own computer knows what calls need (Oct 9).
enum VoiceWording {
    /// On a plan someone shares with the person, calls go through that plan, so nothing needs signing in.
    @MainActor static var chatGPTPlan: String {
        HomeModel.shared.isShared ? "Calls use ChatGPT's voice through the plan shared with you."
            // A home that is the person's account has no computer of theirs to install anything on.
            : HomeModel.shared.serverHome ? "For now, calls use ChatGPT's voice, so they need a ChatGPT plan."
            : "For now, calls use ChatGPT's voice. They need Codex on your computer, signed in with a ChatGPT plan."
    }
}

/// Asks the lead Zip to make the picked Zips from their templates, once each.
@MainActor
func sendPickedZips(_ model: OnboardingModel, _ state: AppState) async {
    await saveOnboardingAnswers(model, state)
    guard !model.rehearsal else { return }
    let names = model.templates.filter { model.picked.contains($0.id) && !model.sentPicks.contains($0.id) }
    guard !names.isEmpty else { return }
    model.sentPicks.formUnion(names.map(\.id))
    if state.fixtureMode { return }
    let list = names.map { "\($0.name) (the \($0.id) template)" }.joined(separator: ", ")
    // What the person already answered for each Zip, so the lead doesn't ask again in its chat.
    let answered: [String] = names.compactMap { t in
        let pairs = t.needs.compactMap { q -> String? in
            let a = (model.needsAnswers[t.id]?[q] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            return a.isEmpty ? nil : "\(q): \(a)"
        }
        return pairs.isEmpty ? nil : "For \(t.name), " + pairs.joined(separator: "; ") + "."
    }
    let more = answered.isEmpty ? "" : " I already answered some of their questions. " + answered.joined(separator: " ")
    let ask = "Please make me these Zips from their templates: \(list).\(more)"
    // A new account's lead cannot think until a plan is signed in, and the ask would come back as a bare failure.
    // It waits and goes out right after Sign In With Claude.
    if state.planMissing {
        UserDefaults.standard.set(ask, forKey: AppState.pendingPicksKey)
        return
    }
    _ = await state.send(ask, to: "default")
}

/// The end of the onboarding, from What I Found or Connect Google.
@MainActor
func finishOnboarding(_ model: OnboardingModel, _ state: AppState) async {
    await sendPickedZips(model, state)
    if model.rehearsal {   // nothing is sent; home is as it was
        state.rehearsingOnboarding = false
        return
    }
    state.savePersonName(model.personName)
    if Theme.onMac && MacEngine.shared.isLocalHome {
        // The answers reached the hub at Looking Around; make sure, then name the person in the lead's SOUL.
        await saveOnboardingAnswers(model, state)
        await MacEngine.shared.refreshSoul()
    }
    // Zero meets the person in its chat with first tasks made from these answers (Model/Fluid.swift, #6).
    FirstTasks.save(FirstTasks.make(
        agent: model.agentName, person: model.personName,
        picked: model.draft.noComputer ? [] : model.templates.filter { model.picked.contains($0.id) },
        projects: model.draft.noComputer ? ProjectIdea.all.filter { model.picked.contains($0.id) } : [],
        about: model.draft.about, home: HomeModel.shared.settings))
    FirstTasks.openChatOnce = true
    state.finishOnboarding()
}

/// Sends the answers to the hub once the phone is connected (from Looking Around); the hub renames the lead Zip.
/// Tried again when the onboarding finishes if it failed.
@MainActor
func saveOnboardingAnswers(_ model: OnboardingModel, _ state: AppState) async {
    guard !model.draft.saved, !model.rehearsal else { return }
    if state.fixtureMode { model.draft.saved = true; return }
    guard state.paired else { return }
    do {
        try await state.client.saveOnboarding(model.answers)
        model.draft.saved = true
        await state.refreshBots()
    } catch {
        // Not shown: nothing on screen depends on it, and it is sent again at the end.
    }
}

/// Plays a voice's sample: the recording bundled for it (Resources/VoiceSamples/<id>.m4a, made in the real call
/// voice), or Apple's speech only if that file is missing. Says which voice is playing.
@MainActor
@Observable
final class VoiceSampler: NSObject, AVSpeechSynthesizerDelegate, AVAudioPlayerDelegate {
    var playing: String?
    @ObservationIgnored private let synth = AVSpeechSynthesizer()
    @ObservationIgnored private var player: AVAudioPlayer?

    override init() {
        super.init()
        synth.delegate = self
    }

    func toggle(_ voice: AgentVoice, line: String) {
        let was = playing
        stop()
        guard was != voice.id else { return }
        playing = voice.id
        // Plays even with the ring switch on silent, like any sound the person asked for.
        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
        try? AVAudioSession.sharedInstance().setActive(true)
        if let url = Bundle.main.url(forResource: voice.id, withExtension: "m4a"),
           let p = try? AVAudioPlayer(contentsOf: url) {
            p.delegate = self
            player = p
            p.play()
        } else {
            synth.speak(voice.utterance(line))
        }
    }

    func stop() {
        player?.stop()
        player = nil
        if synth.isSpeaking { synth.stopSpeaking(at: .immediate) }
        playing = nil
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in self.playing = nil }
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in self.playing = nil; self.player = nil }
    }
}

// MARK: 7. Looking around

struct LookingScreen: View {
    @Environment(AppState.self) private var state
    @Environment(OnboardingModel.self) private var model
    @State private var phase = Phase.asking
    @State private var notifying = false

    enum Phase { case asking, looking, done }

    var body: some View {
        OnboardingPage {
            GeometryReader { geo in
                VStack(spacing: 0) {
                    Spacer(minLength: 0)
                    AgentStage(look: model.draft.look, mood: phase == .done ? .happy : .idle,
                               size: max(120, min(geo.size.height * 0.48, 300)))
                    OnboardingHeadline("Let Me Take a Look at Your Mac", alignment: .center)
                        .padding(.top, 20)
                    Text(phase == .asking ? "This takes a few minutes. You can wait here, or turn on notifications and I'll let you know when I'm done."
                         : notifying ? "Sounds good. I'll let you know when I'm done."
                         : "This takes a few minutes. I'll be quick.")
                        .font(Theme.text(18)).foregroundStyle(Theme.inkSoft)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 10)
                    if phase == .looking {
                        HStack(spacing: 10) {
                            TypingDots(dot: 7)
                            Text("Looking around").font(Theme.text(15, .medium)).foregroundStyle(Theme.inkSoft)
                        }
                        .padding(.top, 18)
                    }
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 24)
            }
        } footer: {
            switch phase {
            case .asking:
                OnboardingButton("Turn On Notifications") {
                    Task {
                        notifying = await Push.shared.requestPermission()
                        startLooking()
                    }
                }
                OnboardingSecondaryButton("I'll Stay") { startLooking() }
                    .padding(.top, 4)
            case .looking:
                Color.clear.frame(height: 54)
            case .done:
                OnboardingButton("See What I Found") { model.go(.found) }
            }
        }
        .task { await saveOnboardingAnswers(model, state) }
    }

    // PLACEHOLDER: the look-around does not exist yet (build step 4 in the design). This waits a moment and
    // moves on, so the rest of the flow can be seen and tested.
    private func startLooking() {
        withAnimation { phase = .looking }
        Task {
            try? await Task.sleep(for: .seconds(4))
            withAnimation { phase = .done }
        }
    }
}

// MARK: 8. What I found

struct FoundScreen: View {
    @Environment(AppState.self) private var state
    @Environment(OnboardingModel.self) private var model
    @State private var loading = true
    @State private var finishing = false

    var body: some View {
        OnboardingPage {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    AgentStage(look: model.draft.look, mood: .idle, size: 140)
                        .frame(maxWidth: .infinity)
                    if HomeModel.shared.serverHome && !model.draft.noComputer {
                        // A home on a server had nothing to look around in, so this starts from what the person said.
                        OnboardingHeadline("Where Should We Start?")
                        Text("From what you told me, here are the Zips I'd start with. Pick any you like.")
                            .font(Theme.text(20, .semibold)).foregroundStyle(Theme.ink)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.top, 6)
                        if loading {
                            ProgressView().frame(maxWidth: .infinity).padding(.vertical, 20)
                        } else if let e = model.templatesError {
                            Text(e).font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                        }
                        ForEach(model.templates) { t in templateCard(t) }
                    } else if model.draft.noComputer {
                        // Without a computer nothing was looked at, so this suggests projects instead.
                        OnboardingHeadline("Where Should We Start?")
                        Text("Here are a few projects you could start with.")
                            .font(Theme.text(20, .semibold)).foregroundStyle(Theme.ink)
                            .padding(.top, 6)
                        ForEach(ProjectIdea.all) { p in projectCard(p) }
                    } else {
                        OnboardingHeadline("Here's What I Found")
                        summary
                        Text("Based on what I found, here are the Zips I'd start with.")
                            .font(Theme.text(20, .semibold)).foregroundStyle(Theme.ink)
                            .padding(.top, 6)
                        if loading {
                            ProgressView().frame(maxWidth: .infinity).padding(.vertical, 20)
                        } else if let e = model.templatesError {
                            Text(e).font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                        }
                        ForEach(model.templates) { t in templateCard(t) }
                    }
                    VStack(alignment: .leading, spacing: 6) {
                        Text("What Is a Zip?").font(Theme.text(17, .semibold)).foregroundStyle(Theme.ink)
                        Text("Each Zip does one job. Talk to it directly, or tell me and I'll hand it out.")
                            .font(Theme.text(16)).foregroundStyle(Theme.inkSoft)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color(hex: "#F5F5F5"), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 12)
            }
            .scrollIndicators(.hidden)
        } footer: {
            // A picked Zip that uses Gmail, Calendar or Drive leads to Connect Google (OnboardingGoogle.swift). In a
            // home without connectors, a picked Zip that reads mail or a calendar leads back to the email step when
            // nothing is connected yet.
            let google = !model.draft.noComputer && !native && !model.pickedGoogle.isEmpty
            let mail = !model.draft.noComputer && native && needsMail
            OnboardingButton(model.picked.isEmpty || model.draft.noComputer ? "Get Started"
                             : "Add \(model.picked.count == 1 ? "This Zip" : "These Zips")" + (google || mail ? "" : " and Get Started"),
                             busy: finishing) { google ? next() : mail ? nextMail() : finish() }
        }
        .task { if !model.draft.noComputer { await loadTemplates() } }
    }

    private var native: Bool { HomeModel.shared.nativeAccounts }

    /// A picked Zip reads mail or a calendar, and the home has neither yet.
    private var needsMail: Bool {
        let s = HomeModel.shared.settings
        let picked = model.templates.filter { model.picked.contains($0.id) }
        let wantsMail = picked.contains { ($0.services ?? []).contains("gmail") || ($0.services ?? []).contains("email") }
        let wantsCalendar = picked.contains { ($0.services ?? []).contains("calendar") }
        return (wantsMail && s?.hasMail != true) || (wantsCalendar && s?.hasCalendar != true)
    }

    private func nextMail() {
        finishing = true
        Task {
            await sendPickedZips(model, state)
            finishing = false
            model.draft.mailAfterPicks = true
            model.go(.mail)
        }
    }

    private func projectCard(_ p: ProjectIdea) -> some View {
        let on = model.picked.contains(p.id)
        return Button {
            if on { model.picked.remove(p.id) } else { model.picked.insert(p.id) }
        } label: {
            HStack(alignment: .center, spacing: 14) {
                Image(systemName: p.symbol)
                    .font(.system(size: 22, weight: .medium))
                    .foregroundStyle(Theme.ink)
                    .frame(width: 56, height: 56)
                    .background(Color(hex: "#F2F2F2"), in: Circle())
                VStack(alignment: .leading, spacing: 3) {
                    Text(p.name).font(Theme.text(17, .semibold)).foregroundStyle(Theme.ink)
                    Text(p.pitch).font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
                Image(systemName: on ? "checkmark.circle.fill" : "plus.circle")
                    .font(.system(size: 24))
                    .foregroundStyle(on ? Theme.ink : Color.black.opacity(0.25))
            }
            .padding(14)
            .onboardingCard(selected: on)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle(scale: 0.98))
        .accessibilityLabel("\(p.name). \(p.pitch)")
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    // PLACEHOLDER: the real summary comes from the look-around, which does not exist yet.
    private var summary: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Preview")
                .font(Theme.text(13, .semibold))
                .foregroundStyle(Theme.taffyDeep)
                .padding(.horizontal, 10).frame(height: 24)
                .background(Theme.taffy.opacity(0.12), in: Capsule())
            Text("When I finish looking around, I'll tell you who you seem to be here. It will read something like this: \"You look like you work in construction in Ohio and have two kids.\"")
                .font(Theme.text(17)).foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .onboardingCard()
    }

    private func templateCard(_ t: ZipTemplate) -> some View {
        let on = model.picked.contains(t.id)
        return VStack(alignment: .leading, spacing: 0) {
            templateHeader(t, on: on)
            if on && !t.needs.isEmpty && !model.rehearsal {
                needsFields(t)
            }
        }
        .onboardingCard(selected: on)
    }

    /// The picked Zip's own questions, asked here so its first chat can start working.
    private func needsFields(_ t: ZipTemplate) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Divider()
            ForEach(t.needs, id: \.self) { q in
                VStack(alignment: .leading, spacing: 5) {
                    Text(q).font(Theme.text(14, .semibold)).foregroundStyle(Theme.ink)
                    TextField("", text: Binding(get: { model.needsAnswers[t.id]?[q] ?? "" },
                                                set: { model.needsAnswers[t.id, default: [:]][q] = $0 }),
                              prompt: Text(placeholder(for: q, template: t)).foregroundStyle(Color.black.opacity(0.3)),
                              axis: .vertical)
                        .font(Theme.text(15)).foregroundStyle(Theme.ink)
                        .lineLimit(1...3)
                        .padding(.horizontal, 14).padding(.vertical, 10)
                        .background(Color(hex: "#F2F2F2"), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .accessibilityLabel(q)
                }
            }
            Text("Leave any of these empty, and \(t.name) asks you in its chat.")
                .font(Theme.text(13)).foregroundStyle(Theme.inkSoft)
        }
        .padding(.horizontal, 14).padding(.bottom, 14)
    }

    private func placeholder(for question: String, template t: ZipTemplate) -> String {
        let q = question.lowercased()
        if q.contains("email"), let e = model.emailHint { return "For example, \(e)" }
        if q.contains("email") || q.contains("account") { return "For example, my personal email" }
        if q.contains("sender") || q.contains("people") { return "For example, my boss and my kids' school" }
        if q.contains("store") || q.contains("sign-in") { return "For example, Target and Costco" }
        return "Your answer"
    }

    private func templateHeader(_ t: ZipTemplate, on: Bool) -> some View {
        Button {
            if on { model.picked.remove(t.id) } else { model.picked.insert(t.id) }
        } label: {
            HStack(alignment: .center, spacing: 14) {
                AgentStage(look: .forZip(id: t.id, look: t.look, color: t.look.color), size: 56)
                    .frame(width: 56, height: 56)
                VStack(alignment: .leading, spacing: 3) {
                    Text(t.name).font(Theme.text(17, .semibold)).foregroundStyle(Theme.ink)
                    Text(t.pitch).font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
                Image(systemName: on ? "checkmark.circle.fill" : "plus.circle")
                    .font(.system(size: 24))
                    .foregroundStyle(on ? Theme.ink : Color.black.opacity(0.25))
            }
            .padding(14)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle(scale: 0.98))
        .accessibilityLabel("\(t.name). \(t.pitch)")
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    /// What this home can run, best first. In a home without connectors, a Zip that only waits on a mailbox or a
    /// calendar is still offered, because the email step comes right after when it is picked.
    static func suggestions(_ all: [ZipTemplate], model: OnboardingModel, home: HomeSettings?) -> [ZipTemplate] {
        let native = home?.nativeAccounts == true
        let fixable: Set<String> = ["gmail", "calendar", "email"]
        let usable = all.filter { t in
            guard !t.added else { return false }
            if t.available != false { return true }
            let s = t.services ?? []
            return native && !s.isEmpty && Set(s).isSubset(of: fixable)
        }
        func score(_ t: ZipTemplate) -> Int {
            let a = model.draft.about
            func said(_ k: String) -> Bool { !(a[k] ?? "").trimmingCharacters(in: .whitespaces).isEmpty }
            var n = 0
            if t.id == "inbox" && (said("email") || home?.hasMail == true) { n += 2 }
            if t.id == "family" && said("family") { n += 2 }
            if t.id == "calendar" && (said("work") || home?.hasCalendar == true) { n += 1 }
            if t.available == false { n -= 1 }
            return n
        }
        let ranked = usable.enumerated().sorted { l, r in
            let (a, b) = (score(l.element), score(r.element))
            return a != b ? a > b : l.offset < r.offset
        }.map(\.element)
        return Array(ranked.prefix(home?.serverHome == true ? 6 : 4))
    }

    private func loadTemplates() async {
        defer { loading = false }
        guard model.templates.isEmpty else { return }
        #if DEBUG
        if state.fixtureMode {
            model.templates = Self.suggestions(Self.sampleTemplates, model: model, home: HomeModel.shared.settings)
            // -pick inbox,errands: those cards picked, for screenshots of their questions.
            let a = CommandLine.arguments
            if let i = a.firstIndex(of: "-pick"), i + 1 < a.count { model.picked = Set(a[i + 1].split(separator: ",").map(String.init)) }
            return
        }
        #endif
        do {
            model.templates = Self.suggestions(try await state.client.templates(), model: model, home: HomeModel.shared.settings)
        } catch {
            model.templatesError = "I couldn't load the suggestions. You can add Zips later from the plus button."
        }
    }

    private func finish() {
        finishing = true
        Task { await finishOnboarding(model, state) }
    }

    /// The picked Zips are asked for now, so the lead Zip builds them while the person connects Google.
    private func next() {
        finishing = true
        Task {
            await sendPickedZips(model, state)
            finishing = false
            model.go(.google)
        }
    }

    #if DEBUG
    /// Fixture suggestions; -templates nogoogle leaves out the ones that use Google, for the path without Connect Google.
    static var sampleTemplates: [ZipTemplate] {
        let home = HomeModel.shared.settings
        if home?.nativeAccounts == true {
            // A home without connectors: what it can run depends on the mail and calendars added.
            func can(_ services: [String]) -> Bool {
                services.allSatisfy { s in
                    s == "gmail" ? home?.hasMail == true : s == "calendar" ? home?.hasCalendar == true : false
                }
            }
            return [
                ZipTemplate(id: "inbox", name: "Inbox", pitch: "Gets your email to zero: clears the noise, batches the quick reads and drafts replies in your voice.",
                            look: ZipLook(color: "#06B6D4"), needs: ["Which email accounts to watch", "Senders that are always important"],
                            services: ["gmail"], routine: "Weekdays at 7:30 a.m.", added: false, available: can(["gmail"])),
                ZipTemplate(id: "errands", name: "Errands", pitch: "Does the clicking: shopping research, carts, returns and appointments. You place the order.",
                            look: ZipLook(color: "#EF4444"), needs: ["Sign-ins for the stores you use"], services: [],
                            routine: nil, added: false, available: true),
                ZipTemplate(id: "family", name: "Family", pitch: "Keeps school emails, practices and birthdays straight, and reminds you before each one.",
                            look: ZipLook(color: "#F472B6"), needs: ["Who is in your family"], services: ["gmail", "calendar"],
                            routine: "Sundays at 5 p.m.", added: false, available: can(["gmail", "calendar"])),
                ZipTemplate(id: "publisher", name: "Publisher", pitch: "Turns your notes into posts and pages, and asks before anything goes live.",
                            look: ZipLook(color: "#8B5CF6"), needs: [], services: [], routine: nil, added: false, available: true),
                ZipTemplate(id: "brief", name: "Daily Brief", pitch: "One short morning note: what needs you, today's plan, and anything a Zip can handle for you.",
                            look: ZipLook(color: "#EAB308"), needs: [], services: ["gmail", "calendar", "trello"],
                            routine: "Every day at 7:30 a.m.", added: false, available: false),
            ]
        }
        let all = ZipTemplate.samples.filter { !$0.added } + [
            ZipTemplate(id: "brief", name: "Daily Brief", pitch: "One short morning note: what needs you, today's plan, and anything a Zip can handle for you.",
                        look: ZipLook(color: "#EAB308"), needs: [], services: ["gmail", "calendar", "trello"],
                        routine: "Every day at 7:30 a.m.", added: false),
        ]
        return CommandLine.arguments.contains("nogoogle") ? all.filter { !$0.usesGoogle } : all
    }
    #endif
}
