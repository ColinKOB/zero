import SwiftUI

// MARK: 9. Connect Google (after What I Found, only when a picked Zip uses Gmail, Calendar or Drive)

/// The onboarding's Connect Google screen: which picked Zips need Google, what Zero can do with Gmail, Calendar and
/// Drive, a button that opens the Connectors page of the person's Claude account (or the Apps page of their ChatGPT
/// account when that is their only plan), and three rows that turn green live. Skipping is fine; a Zip asks later
/// with a card when it needs Google. The full steps and disconnect guidance are one tap away (Show Me How).
struct GoogleScreen: View {
    @Environment(AppState.self) private var state
    @Environment(OnboardingModel.self) private var model
    @Environment(\.openURL) private var openURL
    @State private var guide = false
    @State private var finishing = false
    private var accounts: AccountsModel { AccountsModel.shared }

    private var status: AccountsStatus? { accounts.status }
    private var done: Bool { status?.allConnected(.google) ?? false }
    private var plan: String { status?.planName(for: .google) ?? "Claude" }
    /// "Inbox and Calendar", the picked Zips that use Google.
    private var zips: String { ConnectAccountsView.list(model.pickedGoogle.map(\.name)) }
    private var intro: String {
        let who = zips.isEmpty ? "Some of your Zips" : zips
        let one = model.pickedGoogle.count == 1
        return "\(who) \(one ? "works" : "work") with your email, calendar and files. You connect Google once in \(plan), so I never see your Google password."
    }

    var body: some View {
        OnboardingPage {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    AgentStage(look: model.draft.look, mood: done ? .happy : .idle, size: 140)
                        .frame(maxWidth: .infinity)
                    OnboardingHeadline(done ? "Google Is Connected" : "Connect Google")
                    Text(done ? "\(zips.isEmpty ? "Your Zips" : zips) can use your email, calendar and files now." : intro)
                        .font(Theme.text(17)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    VStack(spacing: 0) {
                        ForEach(Array(AccountGroup.google.uses.enumerated()), id: \.element.id) { i, use in
                            VStack(alignment: .leading, spacing: 0) {
                                AccountRow(use: use, service: status?.service(use.id), loading: status == nil, compact: true)
                                Text(short[use.id] ?? "")
                                    .font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                                    .fixedSize(horizontal: false, vertical: true)
                                    .padding(.leading, 58).padding(.trailing, 12).padding(.bottom, 12).padding(.top, -6)
                            }
                            if i < 2 { Divider().padding(.leading, 58) }
                        }
                    }
                    .onboardingCard()
                    if let s = status, s.claudeProblem {
                        Text(s.plan == "none" ? "Sign in to Claude or ChatGPT on \(s.claudeComputer) first. Either plan works for Google."
                                              : "Sign in to Claude on \(s.claudeComputer) first, then come back to this step.")
                            .font(Theme.text(15, .medium)).foregroundStyle(ConnectAccountsView.orange)
                    } else if !done {
                        Text("Sending email is always up to you. I write drafts, and you send them.")
                            .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 12)
            }
            .scrollIndicators(.hidden)
        } footer: {
            if done {
                OnboardingButton("Get Started", busy: finishing) { finish() }
            } else {
                OnboardingButton("Open \(plan) Settings") { open() }
                OnboardingSecondaryButton("Show Me How", symbol: "list.number") { guide = true }
                    .padding(.top, 4)
                Button("Skip for Now") { finish() }
                    .font(Theme.text(14, .medium)).foregroundStyle(Theme.inkSoft)
                    .frame(minHeight: 36)
            }
        }
        .task { await accounts.refresh(state) }
        .onAppear { accounts.startPolling(state) }
        .onDisappear { accounts.stopPolling() }
        .sheet(isPresented: $guide) {
            NavigationStack { ConnectAccountsView(group: .google, modal: true) }
                .environment(state)
        }
        .sensoryFeedback(.success, trigger: done)
        #if DEBUG
        .task {
            if state.fixtureMode && model.templates.isEmpty {
                model.templates = FoundScreen.sampleTemplates
                model.picked = ["inbox", "calendar"]
            }
            if CommandLine.arguments.contains("-guide") { guide = true }
        }
        #endif
    }

    private func finish() {
        guard !finishing else { return }
        finishing = true
        Task { await finishOnboarding(model, state) }
    }

    private let short = [
        "gmail": "Search, read and draft. I never send for you.",
        "calendar": "See events, find times, add what you ask for.",
        "drive": "Find and read files, Docs and Sheets.",
    ]

    private func open() {
        #if DEBUG
        if state.fixtureMode { accounts.simulateConnecting(.google); return }
        #endif
        if let u = URL(string: status?.connectURL(for: .google) ?? "https://claude.ai/customize/connectors") { openURL(u) }
    }
}
