import SwiftUI

struct ConsentScreen: View {
    @Environment(AppState.self) private var state
    @Environment(OnboardingModel.self) private var model

    var body: some View {
        OnboardingPage {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    AgentStage(look: model.draft.look, size: 88)
                        .frame(maxWidth: .infinity)
                    OnboardingHeadline("Who Gets What You Say?")
                    Text(AIDataSharing.intro(owner: HomeModel.shared.planOwner, shared: HomeModel.shared.isShared, speaker: "I", server: HomeModel.shared.serverHome))
                        .font(Theme.text(17)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    AIConsentBody(providers: AIConsentProviders.current())
                    Text("You can change your mind any time in Settings under AI Data Sharing. If you choose Not Now, I'll still be here, and I'll ask again before I send anything.")
                        .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 12)
            }
            .scrollIndicators(.visible)
        } footer: {
            OnboardingButton("Agree") { choose(.agreed) }
            OnboardingSecondaryButton("Not Now") { choose(.declined) }
                .padding(.top, 4)
        }
        .task {
            await HomeModel.shared.load(state)
            await AccountsModel.shared.refresh(state)
        }
    }

    private func choose(_ choice: AIConsentChoice) {
        if !model.rehearsal { state.setAIConsent(choice) }
        model.draft.consent = choice.rawValue
        model.go(model.afterConsent(agreed: choice == .agreed))
    }
}

/// The small link on the first screen that opens the sample home (Model/DemoHome.swift).
struct SampleHomeLink: View {
    @Environment(AppState.self) private var state

    var body: some View {
        Button("Explore a Sample Home") { state.enterDemo() }
            .font(Theme.text(14, .medium)).foregroundStyle(Theme.inkSoft)
            .frame(minHeight: 36)
            .accessibilityHint("Opens a sample home with made-up chats. It needs no account and connects to nothing.")
    }
}
