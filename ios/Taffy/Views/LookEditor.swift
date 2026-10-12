import SwiftUI

/// Change Look: the same picker as the onboarding's first screen. A body from the carousel and, for any plush body,
/// an outfit from the boxes on the left (dimmed on flat bodies). Saved on the hub with the Zip.
struct LookEditor: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    let bot: Bot

    @State private var look: AgentLook
    @State private var saving = false
    @State private var error: String?
    @AppStorage(SimpleLook.key) private var simpleLook = false

    init(bot: Bot) {
        self.bot = bot
        _look = State(initialValue: bot.agentLook)
    }

    var body: some View {
        NavigationStack {
            GeometryReader { geo in
                let stage = max(140, min(geo.size.height * 0.55, geo.size.width - 112, 360))
                VStack(spacing: 0) {
                    Spacer(minLength: 8)
                    HStack(alignment: .center, spacing: 0) {
                        OutfitColumn(look: $look)
                            .frame(width: 84, height: min(stage + 120, geo.size.height - 150))
                        AgentStage(look: look, size: stage, exact: true)
                            .frame(maxWidth: .infinity)
                            .padding(.trailing, 12)
                    }
                    Spacer(minLength: 8)
                    if simpleLook {
                        Text("Simple Look is on, so your Zips show as flat shapes. You can turn it off in Settings.")
                            .font(Theme.caption).foregroundStyle(Theme.inkSoft)
                            .multilineTextAlignment(.center).padding(.horizontal, 24).padding(.bottom, 8)
                    }
                    BodyCarousel(selected: $look.body)
                        .padding(.bottom, 16)
                }
            }
            .background(Color.white.ignoresSafeArea())
            .safeAreaInset(edge: .top, spacing: 0) {
                SheetBar(title: "Change Look", onCancel: { dismiss() }, confirm: saving ? "Saving" : "Save",
                         confirmEnabled: !saving, onConfirm: { save() })
                    .background(Color.white)
            }
            .toolbar(.hidden, for: .navigationBar)
            .environment(\.colorScheme, .light)   // the picker's boxes and art are made for white
            .alert("That Did Not Work", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
                Button("OK", role: .cancel) {}
            } message: { Text(error ?? "") }
        }
    }

    private func save() {
        saving = true
        Task {
            error = await state.saveLook(bot.id, look.zipLook)
            saving = false
            if error == nil { dismiss() }
        }
    }
}
