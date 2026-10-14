import Foundation
import SwiftUI

/// Whether the person has agreed to send their words to the AI company of their own plan (App Review guideline
/// 5.1.2(i)). Asked once in the onboarding, again wherever something needs to be sent while the answer is not Agree,
/// and reviewable any time in Settings under AI Data Sharing.
enum AIConsentChoice: String {
    case unasked, agreed, declined
}

struct AIProvider: Identifiable, Equatable {
    let id: String
    let company: String
    let sent: String
}

enum AIDataSharing {
    static func providers(plan: String?, googleVoice: Bool, owner: String? = nil) -> [AIProvider] {
        if plan == "shared" {
            let who = owner.map { "a Claude plan that \($0) shares with you" } ?? "a Claude plan shared with you"
            return [
                AIProvider(id: "anthropic", company: "Anthropic",
                           sent: "Your Zips think with \(who), so Anthropic receives your messages to a Zip and anything a Zip reads to answer you, such as an email or a calendar entry."),
                AIProvider(id: "openai", company: "OpenAI",
                           sent: "The shared plan includes ChatGPT, so OpenAI receives the same kinds of things when ChatGPT does the thinking, and the sound of your voice on calls."),
            ]
        }
        let known = plan == "claude" || plan == "chatgpt" || plan == "both"
        var out: [AIProvider] = []
        if !known || plan == "claude" || plan == "both" {
            out.append(AIProvider(
                id: "anthropic", company: "Anthropic",
                sent: (known ? "You use a Claude plan, so Anthropic receives" : "If you use a Claude plan, Anthropic receives")
                    + " your messages to a Zip and anything a Zip reads to answer you, such as a file, a calendar entry or an email."))
        }
        if !known || plan == "chatgpt" || plan == "both" {
            out.append(AIProvider(
                id: "openai", company: "OpenAI",
                sent: (known ? "You use a ChatGPT plan, so OpenAI receives" : "If you use a ChatGPT plan, OpenAI receives")
                    + " the same kinds of things, and the sound of your voice on calls."))
        } else {
            out.append(AIProvider(
                id: "openai", company: "OpenAI",
                sent: "Calls use ChatGPT's voice for now, so OpenAI receives the sound of your voice on calls."))
        }
        if googleVoice {
            out.append(AIProvider(
                id: "google", company: "Google",
                sent: "You set up the free Google voice, so Google receives your voice on calls and a short summary of what your Zips know about you."))
        }
        return out
    }

    static let closing = "Zero does not receive any of this. Each company handles it under its own terms and the settings of your own account with it."
    static let sharedClosing = "Zero does not receive any of this. Each company handles it under its own terms and the settings of the account the plan belongs to."

    static func intro(owner: String?, shared: Bool, speaker: String, settings: Bool = false, server: Bool = false) -> String {
        let uses = speaker == "I" ? "I use" : "\(speaker) uses"
        let plan = shared ? (owner.map { "a Claude and ChatGPT plan that \($0) shares with you" } ?? "a Claude and ChatGPT plan shared with you")
                          : "the AI plan you already pay for"
        let place = shared ? "your phone and the computer your Zips use" : server ? "Zero" : "your phone and your computer"
        return "To think and talk, \(uses) \(plan). \(settings ? "When sharing is on" : "If you agree"), this is what leaves \(place)."
    }
}

extension AppState {
    func setAIConsent(_ choice: AIConsentChoice) {
        aiConsent = choice
        if choice == .declined, proactivity?.enabled == true {
            Task { await setProactivity(enabled: false) }
        }
    }

    func ensureAIConsent() async -> Bool {
        if demoMode || aiConsent == .agreed { return true }
        aiConsentSheet = true
        return await withCheckedContinuation { consentWaiters.append($0) }
    }

    func resolveAIConsent(_ agreed: Bool) {
        setAIConsent(agreed ? .agreed : .declined)
        aiConsentSheet = false
        let waiting = consentWaiters
        consentWaiters = []
        for w in waiting { w.resume(returning: agreed) }
    }

    func putBack(_ text: String, to bot: String) {
        let current = (drafts[bot] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if !current.contains(text) { drafts[bot] = current.isEmpty ? text : current + "\n\n" + text }
        chats[bot, default: []].append(ChatMessage(
            id: UUID().uuidString, kind: .error,
            text: "That wasn't sent, because sharing with your AI company is off. It's back in the message box.",
            date: Date()))
    }

    func startCallIfAllowed(_ start: @escaping () -> Void) {
        Task {
            if await ensureAIConsent() { start() }
        }
    }
}

struct AIConsentBody: View {
    let providers: [AIProvider]
    var plain = true

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(providers) { p in
                VStack(alignment: .leading, spacing: 4) {
                    Text(p.company).font(Theme.text(17, .semibold)).foregroundStyle(Theme.ink)
                    Text(p.sent).font(Theme.text(15)).foregroundStyle(Theme.ink.opacity(0.8))
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(plain ? Color(hex: "#F5F5F5") : Theme.bubble,
                            in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                .accessibilityElement(children: .combine)
            }
            Text(HomeModel.shared.isShared ? AIDataSharing.sharedClosing : AIDataSharing.closing)
                .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct AIConsentProviders {
    @MainActor static func current() -> [AIProvider] {
        let home = HomeModel.shared
        return AIDataSharing.providers(plan: home.isShared ? "shared" : AccountsModel.shared.status?.plan,
                                       googleVoice: GeminiKeyStore.isSet, owner: home.planOwner)
    }
}

struct AIConsentSheet: View {
    @Environment(AppState.self) private var state

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(spacing: 10) {
                        TablerIcon(name: "TablerShieldLock", size: 26).foregroundStyle(Theme.ink)
                    }
                    Text("Who Gets What You Say?")
                        .font(Theme.text(28, .bold)).foregroundStyle(Theme.ink)
                        .accessibilityAddTraits(.isHeader)
                    Text(AIDataSharing.intro(owner: HomeModel.shared.planOwner, shared: HomeModel.shared.isShared, speaker: "Zero", server: HomeModel.shared.serverHome))
                        .font(Theme.text(17)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    AIConsentBody(providers: AIConsentProviders.current())
                }
                .padding(.horizontal, 24)
                .padding(.top, 28)
                .padding(.bottom, 12)
            }
            .scrollIndicators(.hidden)
            VStack(spacing: 6) {
                OnboardingButton("Agree") { state.resolveAIConsent(true) }
                OnboardingSecondaryButton("Not Now") { state.resolveAIConsent(false) }
            }
            .padding(.horizontal, 24)
            .padding(.bottom, 12)
            .padding(.top, 8)
        }
        .frame(maxWidth: 560)
        .frame(maxWidth: .infinity)
        .background(Color.white.ignoresSafeArea())
        .environment(\.colorScheme, .light)
        .presentationDetents([.large])
        .interactiveDismissDisabled()
        .task { await AccountsModel.shared.refresh(state) }
    }
}

struct AIDataSharingView: View {
    @Environment(AppState.self) private var state

    private var agreedAt: Date? {
        let t = UserDefaults.standard.double(forKey: "aiConsentAt")
        return t > 0 ? Date(timeIntervalSince1970: t) : nil
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text(status)
                    .font(Theme.text(17, .semibold)).foregroundStyle(Theme.ink)
                Text(AIDataSharing.intro(owner: HomeModel.shared.planOwner, shared: HomeModel.shared.isShared, speaker: "Zero", settings: true, server: HomeModel.shared.serverHome))
                    .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
                AIConsentBody(providers: AIConsentProviders.current(), plain: false)
                if state.aiConsent == .agreed {
                    Button(role: .destructive) { state.setAIConsent(.declined) } label: {
                        Text("Stop Sharing").font(Theme.text(17, .semibold)).frame(maxWidth: .infinity, minHeight: 50)
                    }
                    .buttonStyle(.bordered)
                    Text("With sharing off, Zero still opens and shows what you have. It asks again before it sends anything. Work your Zips already have scheduled on your computer is not stopped from here.")
                        .font(Theme.caption).foregroundStyle(Theme.inkSoft)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    OnboardingButton("Agree") { state.setAIConsent(.agreed) }
                }
            }
            .padding(20)
        }
        .background(Theme.canvas)
        .navigationTitle("AI Data Sharing")
        .navigationBarTitleDisplayMode(.inline)
        .task { await AccountsModel.shared.refresh(state) }
    }

    private var status: String {
        switch state.aiConsent {
        case .agreed:
            return agreedAt.map { "You agreed on \($0.formatted(date: .abbreviated, time: .omitted))." } ?? "You agreed."
        case .declined:
            return "Sharing is off. You chose Not Now."
        case .unasked:
            return "You haven't been asked yet."
        }
    }
}
