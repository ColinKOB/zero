import SwiftUI

// MARK: 6. Your email and calendar

/// Which email the Zips read: Sign In with Google first, then any other provider with an app password. Shown after the
/// consent screen in a home without Claude or ChatGPT connectors, and again after the Zips are picked when one of them
/// reads mail or a calendar and nothing is connected yet.
struct MailScreen: View {
    @Environment(AppState.self) private var state
    @Environment(OnboardingModel.self) private var model
    @State private var finishing = false
    private var home: HomeModel { HomeModel.shared }

    private var after: Bool { model.draft.mailAfterPicks == true }
    private var connected: Bool { home.settings?.hasMail == true || home.settings?.hasCalendar == true }
    /// "Inbox and Calendar", the picked Zips that read mail or a calendar.
    private var readers: String {
        ConnectAccountsView.list(model.templates.filter { model.picked.contains($0.id) && OnboardingModel.readsMail($0) }.map(\.name))
    }

    var body: some View {
        OnboardingPage {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    AgentStage(look: model.draft.look, mood: connected ? .happy : .idle, size: 120)
                        .frame(maxWidth: .infinity)
                    OnboardingHeadline(after ? "Your Zips Need Your Email" : "Which Email Should Your Zips Read?")
                    Text(after
                         ? "\(readers.isEmpty ? "Some of your Zips" : readers) work with your email and calendar. Sign in once, and every Zip can use them."
                         : "Sign in with Google for Gmail and Google Calendar. Your Zips read and write drafts, and they ask you before anything is sent.")
                        .font(Theme.text(17)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    if model.rehearsal {
                        Text("This is a rehearsal, so nothing you add here is kept.")
                            .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                    }
                    MailAccountsPanel(loginHint: model.emailHint, plain: true)
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 12)
            }
            .scrollIndicators(.hidden)
        } footer: {
            if connected {
                OnboardingButton(after ? "Get Started" : "Continue", busy: finishing) { next() }
            } else {
                Button("Skip for Now") { next() }
                    .font(Theme.text(16, .medium)).foregroundStyle(Theme.inkSoft)
                    .frame(minHeight: 44)
            }
        }
        .task { await home.load(state) }
    }

    private func next() {
        guard !finishing else { return }
        if after {
            finishing = true
            Task { await finishOnboarding(model, state) }
        } else {
            model.go(.ask)
        }
    }
}

// MARK: 7. How your Zips should ask

struct AskScreen: View {
    @Environment(AppState.self) private var state
    @Environment(OnboardingModel.self) private var model
    @State private var quiet = HomeSettings.QuietHours(start: 23, end: 7)
    @State private var locale = HomeWording.phoneLocale
    @State private var questionsOn = true
    @State private var saving = false
    @State private var ready = false
    private var home: HomeModel { HomeModel.shared }

    /// Zero's questions read mail and send it to the AI company to think, so they wait for a yes on the consent screen.
    private var consented: Bool { model.rehearsal ? model.draft.consent == "agreed" : state.aiConsent == .agreed }

    var body: some View {
        OnboardingPage {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    AgentStage(look: model.draft.look, size: 120)
                        .frame(maxWidth: .infinity)
                    OnboardingHeadline("How Should Your Zips Ask?")
                    Text("These are the usual choices. You can change any of them later in Settings.")
                        .font(Theme.text(17)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    ZipsAskForm(quiet: $quiet, locale: $locale, questionsOn: $questionsOn, questionsAllowed: true,
                                lead: model.agentName, plain: true, sharingOff: !consented)
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 12)
            }
            .scrollIndicators(.hidden)
        } footer: {
            OnboardingButton("Continue", busy: saving) { save() }
        }
        .onAppear(perform: fill)
        .onChange(of: quiet) { _, v in if ready { model.draft.quiet = v } }
        .onChange(of: locale) { _, v in if ready { model.draft.locale = v } }
        .onChange(of: questionsOn) { _, v in if ready { model.draft.questionsOn = v } }
    }

    /// What the person already said: their sleep answer, else the home's quiet hours, else 11 PM to 7 AM. Units and
    /// spelling come from the phone.
    private func fill() {
        if let q = model.draft.quiet ?? SleepAnswer.quietHours(from: model.draft.about["sleep"] ?? "") ?? home.quietHours {
            quiet = q
        }
        locale = model.draft.locale ?? HomeWording.phoneLocale
        questionsOn = model.draft.questionsOn ?? true
        ready = true
    }

    private func save() {
        guard !saving else { return }
        model.draft.quiet = quiet
        model.draft.locale = locale
        model.draft.questionsOn = questionsOn
        if model.rehearsal || model.draft.noComputer {
            model.go(.voice)
            return
        }
        saving = true
        Task {
            var agreed = consented
            if questionsOn && !agreed && !state.fixtureMode { agreed = await state.ensureAIConsent() }
            let on = questionsOn && agreed
            _ = await home.save(HomeSettingsUpdate(
                person: .init(timezone: TimeZone.current.identifier, quiet_hours: quiet),
                locale: locale, proactivity: .init(enabled: on)), state)
            if home.unsupported { await state.setProactivity(enabled: on) }
            saving = false
            model.go(.voice)
        }
    }
}

/// The three rows of How Your Zips Should Ask, and the units line. Shared by the onboarding and Settings.
struct ZipsAskForm: View {
    @Binding var quiet: HomeSettings.QuietHours
    @Binding var locale: HomeSettings.Locale
    @Binding var questionsOn: Bool
    let questionsAllowed: Bool
    let lead: String
    var plain = false
    /// The onboarding, when AI data sharing is off: says that Continue asks for it.
    var sharingOff = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(spacing: 0) {
                row("key", "Signing In to Websites",
                    "A Zip asks you the first time it signs in to each site. Rules for single sites are in Settings under Custom Rules.") {
                    Text("First Time").font(Theme.text(14, .semibold)).foregroundStyle(Theme.inkSoft)
                }
                Divider().padding(.leading, 54)
                VStack(alignment: .leading, spacing: 10) {
                    row("moon", "Quiet Hours", "Alerts wait until morning unless something needs you right away.") { EmptyView() }
                    HStack(spacing: 10) {
                        hourMenu("From", $quiet.start)
                        hourMenu("To", $quiet.end)
                        Spacer(minLength: 0)
                    }
                    .padding(.leading, 54).padding(.trailing, 16).padding(.bottom, 13).padding(.top, -4)
                }
                Divider().padding(.leading, 54)
                VStack(alignment: .leading, spacing: 8) {
                    row("questionmark.bubble", "Questions From \(lead)",
                        "\(lead) notices things that need you and asks a short question about each one, such as:") {
                        Toggle("", isOn: $questionsOn).labelsHidden().tint(Color(hex: "#10A35A"))
                            .disabled(!questionsAllowed)
                            .accessibilityLabel("Questions From \(lead)")
                    }
                    VStack(alignment: .leading, spacing: 6) {
                        bullet("An email that is waiting on your reply")
                        bullet("A renewal or a free trial that ends soon")
                        bullet("Something you said you would do")
                        if sharingOff && questionsOn {
                            Text("These questions need AI data sharing, which is off. When you continue, \(lead) asks you to turn it on.")
                                .font(Theme.text(14, .medium)).foregroundStyle(Theme.inkSoft)
                                .fixedSize(horizontal: false, vertical: true)
                                .padding(.top, 4)
                        } else if !questionsAllowed {
                            Text("This needs AI data sharing, which is off. You can turn both on later in Settings.")
                                .font(Theme.text(14, .medium)).foregroundStyle(ConnectAccountsView.orange)
                                .fixedSize(horizontal: false, vertical: true)
                                .padding(.top, 4)
                        }
                    }
                    .padding(.leading, 54).padding(.trailing, 16).padding(.bottom, 13)
                }
            }
            .background(plain ? Color.white : Theme.raised, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(plain ? Color.black.opacity(0.1) : Theme.rule, lineWidth: 1))
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("\(HomeWording.units(locale.units)) and \(HomeWording.spelling(locale.spelling)).")
                    .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 4)
                Menu {
                    Picker("Units", selection: Binding(get: { locale.units ?? "us" }, set: { locale.units = $0 })) {
                        Text("US Units").tag("us")
                        Text("Metric Units").tag("metric")
                        Text("UK Units").tag("uk")
                    }
                    Picker("Spelling", selection: Binding(get: { locale.spelling ?? "american" }, set: { locale.spelling = $0 })) {
                        Text("American Spelling").tag("american")
                        Text("British Spelling").tag("british")
                    }
                } label: {
                    Text("Change").font(Theme.text(15, .semibold)).foregroundStyle(Theme.ink).underline()
                }
                .accessibilityLabel("Change units and spelling")
            }
        }
    }

    private func row<Trailing: View>(_ symbol: String, _ title: String, _ detail: String,
                                     @ViewBuilder trailing: () -> Trailing) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 17, weight: .medium)).foregroundStyle(Theme.ink)
                .frame(width: 26).padding(.top, 1)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink)
                Text(detail).font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            trailing()
        }
        .padding(.horizontal, 16).padding(.vertical, 13)
    }

    private func bullet(_ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Circle().fill(Theme.inkSoft).frame(width: 4, height: 4).alignmentGuide(.firstTextBaseline) { $0[.bottom] + 5 }
            Text(text).font(Theme.text(14)).foregroundStyle(Theme.ink.opacity(0.85))
        }
    }

    private func hourMenu(_ label: String, _ hour: Binding<Int>) -> some View {
        Menu {
            Picker(label, selection: hour) {
                ForEach(0..<24, id: \.self) { Text(HomeWording.hour($0)).tag($0) }
            }
        } label: {
            HStack(spacing: 4) {
                Text("\(label) \(HomeWording.hour(hour.wrappedValue))")
                Image(systemName: "chevron.up.chevron.down").font(.system(size: 11, weight: .semibold))
            }
            .font(Theme.text(15, .semibold)).foregroundStyle(Theme.ink)
            .padding(.horizontal, 12).frame(minHeight: 36)
            .background(plain ? Color(hex: "#F2F2F2") : Theme.bubble, in: Capsule())
        }
        .accessibilityLabel("Quiet hours \(label.lowercased()) \(HomeWording.hour(hour.wrappedValue))")
    }
}

// MARK: Settings

/// Settings, How Your Zips Ask: the same rows as the onboarding, saved as they change.
struct AskSettingsView: View {
    @Environment(AppState.self) private var state
    @State private var quiet = HomeSettings.QuietHours(start: 23, end: 7)
    @State private var locale = HomeWording.phoneLocale
    @State private var questionsOn = false
    @State private var loaded = false
    @State private var problem: String?
    private var home: HomeModel { HomeModel.shared }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                ZipsAskForm(quiet: $quiet, locale: $locale, questionsOn: $questionsOn,
                            questionsAllowed: true, lead: state.bot("default")?.name ?? "Zero")
                if let problem {
                    Text(problem).font(Theme.text(14, .medium)).foregroundStyle(Theme.danger)
                }
            }
            .frame(maxWidth: 560, alignment: .leading)
            .padding(20)
            .frame(maxWidth: .infinity)
        }
        .background(Theme.canvas)
        .navigationTitle("How Your Zips Ask")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await home.load(state)
            await state.loadProactivity()
            let s = home.settings
            if let q = s?.person?.quiet_hours { quiet = q }
            if let l = s?.locale, l.units != nil { locale = l }
            questionsOn = state.proactivity?.enabled ?? s?.proactivity?.enabled ?? false
            try? await Task.sleep(for: .milliseconds(50))
            loaded = true
        }
        .onChange(of: quiet) { _, q in
            guard loaded else { return }
            Task { problem = await home.save(.init(person: .init(quiet_hours: q)), state) }
        }
        .onChange(of: locale) { _, l in
            guard loaded else { return }
            Task { problem = await home.save(.init(locale: l), state) }
        }
        .onChange(of: questionsOn) { _, on in
            guard loaded else { return }
            Task {
                await state.setProactivity(enabled: on)
                questionsOn = state.proactivity?.enabled ?? on
            }
        }
    }
}
