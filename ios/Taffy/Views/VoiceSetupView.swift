import SwiftUI

/// Where the Gemini key lives: this device's keychain, readable only after first unlock, never synced, and never
/// a file (unlike the pairing token, there is no file fallback). Voice setup runs on the Mac, so the long-lived key
/// stays there (see docs/research/2026-10-08-voice-mode-design.md).
enum GeminiKeyStore {
    private static let account = "geminiKey"
    /// False until calls run on Gemini with this key. While false, the setup says calls still use the ChatGPT plan.
    static let callsUseKey = false
    static var saved: String? { Keychain.readKeychainOnly(account) }
    static var isSet: Bool { saved != nil }
    /// False when the keychain refused the key; nothing is written anywhere else.
    static func save(_ key: String) -> Bool { Keychain.writeKeychainOnly(account, key) }
    static func remove() { Keychain.delete(account) }
}

/// Guided setup for Zero's free Google voice (docs/specs/2026-10-08-guided-voice-setup-design.md).
///
/// On the Mac: explains what happens, then opens Google AI Studio in the person's own browser while the Mac
/// bridge highlights each click there (ios/MacBridge/VoiceGuide.swift). When the key is copied, Zero checks
/// it with a Gemini Live handshake and saves it. On the iPhone: points the person to their Mac.
struct VoiceSetupView: View {
    enum Phase: Equatable {
        case intro, guiding, paste, checking
        case done(String)
        case failed(GeminiKeyCheck.Result)
        /// The key works but the keychain wouldn't take it.
        case notSaved
        /// Opened when a key is already saved: which one, and ways to change it.
        case saved(String)
        case onMac
    }

    @Environment(\.dismiss) private var dismiss
    @State private var phase: Phase
    @State private var stepTitle: String?
    @State private var cardMode: Bool
    @State private var pasteProblem: String?
    /// The key being checked, kept only so Try Again works after a network or limit failure. Never shown or logged.
    @State private var pendingKey: String?
    @State private var pasted = ""
    /// A short note on the first screen, such as why the guide stopped.
    @State private var notice: String?
    @State private var confirmRemove = false
    /// Shown while guiding when the browser window was closed.
    @State private var windowClosed = false
    /// Debug only: point the guide at the local practice page instead of Google.
    private let practice: (studio: String, start: String)?
    /// Debug only: open the page in this browser app instead of the default browser.
    var testBrowser: String?

    init(phase: Phase? = nil, stepTitle: String? = nil, cardMode: Bool = false, notice: String? = nil,
         practice: (studio: String, start: String)? = nil) {
        _notice = State(initialValue: notice)
        _phase = State(initialValue: phase ?? (!Theme.onMac ? .onMac
            : GeminiKeyStore.saved.map { .saved(GeminiKeyPattern.ending($0)) } ?? .intro))
        _stepTitle = State(initialValue: stepTitle)
        _cardMode = State(initialValue: cardMode)
        self.practice = practice
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                content
            }
            .frame(maxWidth: 520, alignment: .leading)
            .padding(.horizontal, 28)
            .padding(.vertical, Theme.onMac ? 28 : 24)
            .frame(maxWidth: .infinity)
        }
        .background(Theme.canvas)
        .onDisappear { MacLink.shared.voiceGuide(["op": "stop"]) { _ in } }
        .confirmationDialog("Remove your Google key from this Mac?", isPresented: $confirmRemove, titleVisibility: .visible) {
            Button("Remove Key", role: .destructive) {
                GeminiKeyStore.remove()
                notice = "Zero removed your key from this Mac."
                phase = .intro
            }
        } message: {
            Text("You can set up voice again at any time.")
        }
    }

    // MARK: Screens

    @ViewBuilder private var content: some View {
        switch phase {
        case .intro: intro
        case .guiding: guiding
        case .paste: paste
        case .checking: checking
        case .done(let ending): done(ending)
        case .failed(let result): failed(result)
        case .notSaved: notSaved
        case .saved(let ending): saved(ending)
        case .onMac: onMac
        }
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 0) {
            header(symbol: "waveform", title: "Give Zero a Voice",
                   lead: GeminiKeyStore.callsUseKey
                       ? "Zero can talk with you on calls using a free voice from Google. Google asks you to get a free key first, which takes about two minutes."
                       : "Zero is moving its calls to a free voice from Google. Google asks you to get a free key first, which takes about two minutes.")
            if let notice {
                Text(notice).font(Theme.text(15)).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 14).padding(.vertical, 12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .padding(.top, 18)
            }
            section("What Happens Next?", [
                "Zero opens Google's key page in your browser.",
                "Zero dims the page and highlights one thing to click at a time, with a short note on why.",
                "If Google asks you to sign in, use your own Google account. Zero can't see what you type.",
                "When your key appears, Zero picks it up, checks it, and keeps it on this Mac.",
            ])
            section("What Should You Know?", [
                "On the free plan, Google may use your calls to improve its products, and people at Google may listen to some of them.",
                "If you add billing to your key in Google AI Studio, Google stops using your calls. Calls then cost about a cent a minute.",
                "Google requires you to be 18 or older.",
            ] + (GeminiKeyStore.callsUseKey ? [] : [
                VoiceWording.chatGPTPlan + " Zero will switch to Google's voice in an update and use the key you save today.",
            ]))
            actions {
                primary("Get My Free Key", action: startGuide)
                secondary("I Already Have a Key") { pasteProblem = nil; notice = nil; phase = .paste }
                quiet("Not Now") { dismiss() }
            }
        }
    }

    private var guiding: some View {
        VStack(alignment: .leading, spacing: 0) {
            if cardMode {
                header(symbol: "list.number", title: "Follow the Card in Your Browser",
                       lead: "Google's key page is open in your browser, with Zero's steps in a card beside it. Zero can highlight each click instead if you allow Zero under Accessibility in System Settings.")
            } else {
                header(symbol: "hand.point.up.left", title: "Follow the Highlights",
                       lead: "Google's key page is open in your browser. Zero is pointing at each click there. When you copy your key, Zero brings you back here.")
            }
            if windowClosed {
                Text(cardMode ? "Your browser window closed. Choose Start Again to open Google's key page again."
                              : "Your browser window closed. Choose Show the Guide to open Google's key page again.")
                    .font(Theme.text(15)).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 14).padding(.vertical, 12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .padding(.top, 22)
            } else if let stepTitle, !cardMode {
                HStack(spacing: 10) {
                    Circle().fill(Self.yellow).frame(width: 10, height: 10)
                    Text("Now: \(stepTitle)").font(Theme.text(15, .semibold)).foregroundStyle(Theme.ink)
                }
                .padding(.horizontal, 14).padding(.vertical, 12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .padding(.top, 22)
            }
            actions {
                if cardMode {
                    primary("Open System Settings") {
                        UIApplication.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!)
                    }
                    secondary("Start Again", action: startGuide)
                } else {
                    primary("Show the Guide") { windowClosed = false; MacLink.shared.voiceGuide(["op": "resume"]) { _ in } }
                }
                secondary("Paste My Key Instead") { pasteProblem = nil; phase = .paste }
                quiet("Stop") { MacLink.shared.voiceGuide(["op": "stop"]) { _ in }; phase = .intro }
            }
        }
    }

    private var paste: some View {
        VStack(alignment: .leading, spacing: 0) {
            header(symbol: "doc.on.clipboard", title: "Paste Your Key",
                   lead: "Copy your key in Google AI Studio and paste it here. Zero checks it with Google before saving it.")
            if let pasteProblem {
                Text(pasteProblem)
                    .font(Theme.text(14)).foregroundStyle(Theme.danger)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 16)
            }
            // A secure field: the key isn't shown on screen, and Command-V is a paste the person started, so macOS
            // doesn't ask about it. (SwiftUI's PasteButton draws nothing on Mac Catalyst.)
            SecureField("Paste your key here", text: $pasted)
                .textFieldStyle(.plain)
                .font(Theme.text(16))
                .padding(.horizontal, 14).frame(height: 46)
                .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .padding(.top, 22)
                .onSubmit(checkPasted)
            actions {
                primary("Check Key", action: checkPasted)
                secondary("Show Me Where to Get One", action: startGuide)
                quiet("Back") { phase = .intro }
            }
        }
    }

    private var checking: some View {
        VStack(alignment: .leading, spacing: 0) {
            header(symbol: "checkmark.shield", title: "Checking Your Key",
                   lead: "Zero is asking Google whether this key works. This takes a few seconds.")
            ProgressView().controlSize(.large).padding(.top, 26)
        }
    }

    private func done(_ ending: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            header(symbol: "checkmark.circle", title: GeminiKeyStore.callsUseKey ? "Your Voice Is Ready" : "Your Key Is Saved",
                   lead: GeminiKeyStore.callsUseKey
                       ? "Zero checked your key with Google and saved it on this Mac. Calls use Google's voice from now on."
                       : "Zero checked your key with Google and saved it on this Mac. For now, calls use ChatGPT's voice. Zero will switch to Google's voice in an update.")
            Text("Key \(ending)").font(Theme.text(14)).foregroundStyle(Theme.inkSoft).padding(.top, 14)
            actions { primary("Done") { dismiss() } }
        }
    }

    private func saved(_ ending: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            header(symbol: "key", title: "Your Google Key Is Saved",
                   lead: "Zero already has a working key on this Mac. You can keep it, or swap in a different one.")
            Text("Key \(ending)").font(Theme.text(14)).foregroundStyle(Theme.inkSoft).padding(.top, 14)
            actions {
                primary("Done") { dismiss() }
                secondary("Use a Different Key") { notice = nil; phase = .intro }
                quiet("Remove Key") { confirmRemove = true }
            }
        }
    }

    private var notSaved: some View {
        VStack(alignment: .leading, spacing: 0) {
            header(symbol: "exclamationmark.triangle", title: "Zero Couldn't Save Your Key",
                   lead: "Google accepted your key, but this Mac's keychain didn't let Zero keep it. Try again in a moment. If it keeps happening, restart your Mac and set up voice again.")
            actions { primary("Try Again", action: retry); quiet("Not Now") { dismiss() } }
        }
    }

    @ViewBuilder private func failed(_ result: GeminiKeyCheck.Result) -> some View {
        switch result {
        case .rejected:
            VStack(alignment: .leading, spacing: 0) {
                header(symbol: "xmark.octagon", title: "Google Didn't Accept This Key",
                       lead: "The key may be incomplete, or it may have been turned off. Copy it again in Google AI Studio, or make a new one there.")
                actions {
                    primary("Show Me Again", action: startGuide)
                    secondary("Paste a Different Key") { pasteProblem = nil; phase = .paste }
                }
            }
        case .limited:
            VStack(alignment: .leading, spacing: 0) {
                header(symbol: "hourglass", title: "Google Says Try Again Soon",
                       lead: "This key has reached Google's free limit for the moment. Wait a minute, then try again.")
                actions { primary("Try Again", action: retry); quiet("Not Now") { dismiss() } }
            }
        case .offline:
            VStack(alignment: .leading, spacing: 0) {
                header(symbol: "wifi.slash", title: "Zero Couldn't Reach Google",
                       lead: "Check your internet connection, then try again.")
                actions { primary("Try Again", action: retry); quiet("Not Now") { dismiss() } }
            }
        case .refused(let why) where why.lowercased().contains("location is not supported"):
            VStack(alignment: .leading, spacing: 0) {
                header(symbol: "mappin.and.ellipse", title: "Google's Voice Isn't Offered Here",
                       lead: "Google doesn't offer this voice where you are yet, so it turned the key down.")
                actions { primary("OK") { dismiss() } }
            }
        case .refused:
            VStack(alignment: .leading, spacing: 0) {
                header(symbol: "exclamationmark.triangle", title: "Google Turned This Key Down",
                       lead: "This happens with some work and school accounts. A personal Google account usually works.")
                actions {
                    primary("Try Another Account", action: startGuide)
                    quiet("Not Now") { dismiss() }
                }
            }
        case .works:
            EmptyView()
        }
    }

    private var onMac: some View {
        VStack(alignment: .leading, spacing: 0) {
            header(symbol: "laptopcomputer", title: "Set Up Voice on Your Mac",
                   lead: "Zero keeps your Google key on your Mac, so voice setup happens there. Open Zero on your Mac and choose Settings, then Set Up Voice.")
            actions { primary("OK") { dismiss() } }
        }
    }

    // MARK: Actions

    private func startGuide() {
        var command: [String: Any] = ["op": "start"]
        if let practice { command["studio"] = practice.studio; command["startURL"] = practice.start }
        if let testBrowser { command["browserApp"] = testBrowser }
        stepTitle = nil
        cardMode = false
        notice = nil
        windowClosed = false
        let started = MacLink.shared.voiceGuide(command) { event in handle(event) }
        if started {
            phase = .guiding
        } else {
            pasteProblem = "Zero couldn't open its guide on this Mac. You can still copy your key in Google AI Studio and paste it here."
            phase = .paste
        }
    }

    private func handle(_ event: [String: Any]) {
        switch event["type"] as? String {
        case "needsAccess": cardMode = true
        case "step": stepTitle = event["title"] as? String; windowClosed = false
        case "noWindow": windowClosed = true
        case "key": if let key = event["key"] as? String { check(key) }
        case "paste": pasteProblem = nil; phase = .paste
        case "stopped": if phase == .guiding { phase = .intro }
        case "lost":
            if phase == .guiding {
                notice = "Your browser closed, so Zero stopped the guide. Choose Get My Free Key when you're ready to try again."
                phase = .intro
            }
        default: break
        }
    }

    private func check(_ key: String) {
        pendingKey = key
        phase = .checking
        Task { @MainActor in
            let result = await GeminiKeyCheck.run(key)
            if result == .works {
                guard GeminiKeyStore.save(key) else { phase = .notSaved; return }
                pendingKey = nil
                phase = .done(GeminiKeyPattern.ending(key))
            } else {
                if result == .rejected { pendingKey = nil }
                phase = .failed(result)
            }
        }
    }

    private func checkPasted() {
        guard let key = GeminiKeyPattern.find(in: pasted) else {
            pasteProblem = pasted.isEmpty
                ? "Paste your key in the box first."
                : "That doesn't look like a Google key. Google's keys start with AQ. or AIza. Copy the key itself, then paste it here again."
            return
        }
        pasted = ""
        check(key)
    }

    private func retry() {
        if let pendingKey { check(pendingKey) } else { phase = .paste }
    }

    #if DEBUG
    /// `-voiceSetupKeychainTest`: saves the practice key through GeminiKeyStore, reads it back and removes it, and
    /// logs only whether each step worked. Skipped if a key is already saved, so a real one is never touched.
    static let keychainSelfTest: Void = {
        guard CommandLine.arguments.contains("-voiceSetupKeychainTest") else { return }
        guard !GeminiKeyStore.isSet else { return NSLog("Voice setup keychain test: skipped, a key is already saved") }
        let fake = "AQ.Zero-Practice-Key-Not-Real-00000000000000000000000"
        let saved = GeminiKeyStore.save(fake)
        let readBack = GeminiKeyStore.saved == fake
        GeminiKeyStore.remove()
        NSLog("Voice setup keychain test: saved %@, read back %@, removed %@", "\(saved)", "\(readBack)", "\(!GeminiKeyStore.isSet)")
    }()
    #endif

    // MARK: Pieces

    /// Zero's sunflower yellow and deep charcoal, from the app icon (`ios/scripts/make-icon.py`).
    private static let yellow = Color(red: 249 / 255, green: 196 / 255, blue: 16 / 255)
    private static let charcoal = Color(red: 24 / 255, green: 24 / 255, blue: 27 / 255)

    private func header(symbol: String, title: String, lead: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Image(systemName: symbol)
                .font(.system(size: 24, weight: .semibold))
                .foregroundStyle(Self.charcoal)
                .frame(width: 52, height: 52)
                .background(Self.yellow, in: Circle())
                .padding(.bottom, 16)
                .accessibilityHidden(true)
            Text(title).font(Theme.text(28, .bold)).foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            Text(lead).font(Theme.text(16)).foregroundStyle(Theme.ink.opacity(0.85))
                .lineSpacing(2)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 10)
        }
    }

    private func section(_ title: String, _ lines: [String]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(Theme.text(17, .semibold)).foregroundStyle(Theme.ink)
                .accessibilityAddTraits(.isHeader)
            ForEach(lines, id: \.self) { line in
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Circle().fill(Theme.inkSoft).frame(width: 5, height: 5).alignmentGuide(.firstTextBaseline) { $0[.bottom] + 5 }
                    Text(line).font(Theme.text(15)).foregroundStyle(Theme.ink.opacity(0.85))
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(.top, 24)
    }

    /// One row of buttons when they fit (the Mac), a stack when they don't (the iPhone).
    private func actions<C: View>(@ViewBuilder _ buttons: () -> C) -> some View {
        let b = buttons()
        return ViewThatFits(in: .horizontal) {
            HStack(spacing: 10) { b }
            VStack(alignment: .leading, spacing: 10) { b }
        }
        .padding(.top, 28)
    }

    private func primary(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(Theme.text(16, .semibold)).foregroundStyle(Theme.actionGlyph)
                .padding(.horizontal, 22).frame(height: 46)
                .background(Theme.action, in: Capsule())
        }
        .buttonStyle(PressStyle())
    }

    private func secondary(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink)
                .padding(.horizontal, 22).frame(height: 46)
                .background(Theme.bubble, in: Capsule())
        }
        .buttonStyle(PressStyle())
    }

    private func quiet(_ title: String, action: @escaping () -> Void) -> some View {
        Button(title, action: action)
            .buttonStyle(.plain)
            .font(Theme.text(15, .medium))
            .foregroundStyle(Theme.inkSoft)
            .padding(.horizontal, 4).frame(height: 36)
    }
}
