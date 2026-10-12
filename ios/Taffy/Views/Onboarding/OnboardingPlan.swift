import SwiftUI

// Screen 5: the person's own AI plan. A new account's home has no plan signed in, so its Zips cannot think until the
// person signs in to Claude here (hub plansignin.py). A home on a shared plan, or one already signed in, goes straight on.

/// Sign In With Claude: open Claude's sign-in page, then paste the code it shows. Used by the onboarding and Settings.
struct ClaudeSignInPanel: View {
    @Environment(AppState.self) private var state
    @Environment(\.openURL) private var openURL
    /// Called once Claude is connected.
    var onDone: () -> Void
    @State private var page: URL?
    @State private var code = ""
    @State private var busy = false
    @State private var problem: String?
    @FocusState private var typing: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if page == nil {
                Button { Task { await start() } } label: {
                    HStack(spacing: 10) {
                        if busy { ProgressView().tint(Theme.mineText) }
                        Text(busy ? "Opening Claude" : "Sign In With Claude")
                    }
                    .font(Theme.text(17, .semibold)).foregroundStyle(Theme.mineText)
                    .frame(maxWidth: .infinity, minHeight: 54)
                    .background(Theme.mine, in: Capsule())
                }
                .buttonStyle(PressStyle())
                .disabled(busy)
                .accessibilityIdentifier("claudeSignIn")
            } else {
                Text("Sign in on the Claude page. When it shows a code, copy it, come back here and paste it below.")
                    .font(Theme.text(16)).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 10) {
                    TextField("Code from Claude", text: $code)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .focused($typing)
                        .font(Theme.text(16))
                        .padding(.horizontal, 14).frame(height: 50)
                        .onboardingCard(selected: typing, radius: 14)
                        .accessibilityIdentifier("claudeCode")
                    PasteButton(payloadType: String.self) { strings in
                        if let s = strings.first { code = s.trimmingCharacters(in: .whitespacesAndNewlines) }
                    }
                    .labelStyle(.iconOnly)
                    .buttonBorderShape(.capsule)
                    .tint(Theme.ink)
                }
                Button { Task { await finish() } } label: {
                    HStack(spacing: 10) {
                        if busy { ProgressView().tint(Theme.mineText) }
                        Text(busy ? "Checking the Code" : "Finish Signing In")
                    }
                    .font(Theme.text(17, .semibold)).foregroundStyle(Theme.mineText)
                    .frame(maxWidth: .infinity, minHeight: 54)
                    .background(Theme.mine, in: Capsule())
                    .opacity(code.count < 8 ? 0.35 : 1)
                }
                .buttonStyle(PressStyle())
                .disabled(busy || code.count < 8)
                Button("Open the Claude Page Again") { if let page { openURL(page) } }
                    .font(Theme.text(15, .medium)).foregroundStyle(Theme.inkSoft)
                    .frame(minHeight: 36)
            }
            if let problem {
                Text(problem).font(Theme.text(15, .medium)).foregroundStyle(Theme.danger)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func start() async {
        busy = true
        problem = nil
        defer { busy = false }
        do {
            let url = try await state.client.startClaudeSignIn()
            page = url
            openURL(url)
        } catch {
            problem = HubClient.describe(error)
        }
    }

    private func finish() async {
        busy = true
        problem = nil
        typing = false
        defer { busy = false }
        do {
            let r = try await state.client.finishClaudeSignIn(code: code)
            if r.ok {
                await AccountsModel.shared.refresh(state, fresh: true)
                await state.sendPendingPicks()
                onDone()
            } else {
                problem = r.detail ?? "That code did not work. Try again."
                page = nil
                code = ""
            }
        } catch {
            problem = HubClient.describe(error)
            page = nil
            code = ""
        }
    }
}

struct PlanScreen: View {
    @Environment(AppState.self) private var state
    @Environment(OnboardingModel.self) private var model
    @State private var connected = false
    @State private var loading = true

    private var accounts: AccountsModel { AccountsModel.shared }
    /// Signed in already, or a plan someone shares: nothing to do here.
    private var nothingToDo: Bool {
        HomeModel.shared.isShared || accounts.status?.claude.state == "signed_in" || accounts.status?.plan == "shared"
            || accounts.status?.chatgpt?.state == "signed_in"
    }

    var body: some View {
        OnboardingPage {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    AgentStage(look: model.draft.look, mood: connected ? .happy : .idle, size: 140)
                        .frame(maxWidth: .infinity)
                    OnboardingHeadline(connected ? "Claude Is Connected" : "Bring Your Own Claude")
                    Text(connected
                         ? "\(model.agentName) and your Zips think with your Claude plan from now on."
                         : "\(model.agentName) thinks with your own Claude plan, such as Pro or Max. Sign in once, and every Zip can use it. Zero never sees your Claude password.")
                        .font(Theme.text(17)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    if loading {
                        HStack(spacing: 10) {
                            ProgressView()
                            Text("Checking your plan").font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                        }
                    } else if !connected {
                        ClaudeSignInPanel { withAnimation { connected = true } }
                    }
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 12)
            }
            .scrollIndicators(.hidden)
        } footer: {
            if connected {
                OnboardingButton("Continue") { model.go(.consent) }
            } else if !loading {
                Button("Skip for Now") {
                    if !state.fixtureMode { Task { await state.client.cancelClaudeSignIn() } }
                    model.go(.consent)
                }
                    .font(Theme.text(16, .medium)).foregroundStyle(Theme.inkSoft)
                    .frame(minHeight: 44)
                Text("Your Zips can't answer until a plan is connected. You can sign in later in Settings.")
                    .font(Theme.caption).foregroundStyle(Theme.inkSoft)
                    .multilineTextAlignment(.center)
            }
        }
        .sensoryFeedback(.success, trigger: connected)
        .task {
            if model.rehearsal || state.fixtureMode {
                loading = false
                return
            }
            await HomeModel.shared.load(state)
            await accounts.refresh(state, fresh: true)
            loading = false
            if nothingToDo { model.go(.consent) }
        }
    }
}
