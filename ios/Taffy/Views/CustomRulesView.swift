import SwiftUI

/// A short confirmation shown at the top of the screen, then gone: "Allowed from now on: ..." after Always Allow on a
/// card, "Saved to Custom Rules" after editing a rule.
struct Confirmation: Identifiable, Equatable {
    let id = UUID()
    let title: String
    var text: String = ""
}

struct ConfirmationBanner: View {
    let confirmation: Confirmation
    var onDone: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 20, weight: .medium)).foregroundStyle(Theme.ink)
            VStack(alignment: .leading, spacing: 2) {
                Text(confirmation.title).font(Theme.text(15, .semibold)).foregroundStyle(Theme.ink)
                if !confirmation.text.isEmpty {
                    Text(confirmation.text).font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        .frame(maxWidth: 520)
        .background(Theme.raised, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(Theme.rule, lineWidth: 1))
        .shadow(color: .black.opacity(0.12), radius: 16, y: 6)
        .padding(.horizontal, 16)
        .contentShape(Rectangle())
        .onTapGesture(perform: onDone)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
        .accessibilityHint("Closes this message.")
        .task(id: confirmation.id) {
            AccessibilityNotification.Announcement("\(confirmation.title). \(confirmation.text)").post()
            try? await Task.sleep(for: .seconds(5))
            guard !Task.isCancelled else { return }
            onDone()
        }
        .transition(.move(edge: .top).combined(with: .opacity))
    }
}

extension View {
    /// Shows `confirmation` over the top of this view until it times out or is tapped.
    func confirmationBanner(_ confirmation: Binding<Confirmation?>) -> some View {
        overlay(alignment: .top) {
            if let c = confirmation.wrappedValue {
                ConfirmationBanner(confirmation: c) { confirmation.wrappedValue = nil }
                    .padding(.top, 8)
            }
        }
        .animation(.spring(duration: 0.35), value: confirmation.wrappedValue)
    }
}

/// Custom Rules (from Grok Bot 0.68.1): every standing rule in a plain sentence, which Zip it covers, when it was made
/// and how often it was used. Allow rules come from Always Allow on a card. Ask First rules are Colin's own: a Zip asks
/// before something it would otherwise just do. Opened from Settings (every Zip) and from a Zip's profile (that Zip).
struct CustomRulesView: View {
    @Environment(AppState.self) private var state
    /// One Zip's rules (and the rules for every Zip), or nil for all of them.
    var botID: String? = nil

    @State private var rules: [PermissionRule] = []
    @State private var choices: RuleChoices?
    @State private var loaded = false
    @State private var error: String?
    @State private var editing: RuleEdit?
    @State private var saved: Confirmation?

    private var askRules: [PermissionRule] { rules.filter(\.isAskFirst) }
    private var allowRules: [PermissionRule] { rules.filter { !$0.isAskFirst } }

    var body: some View {
        List {
            if let error {
                Section { Label(error, systemImage: "exclamationmark.circle.fill").foregroundStyle(Theme.danger) }
            }
            Section {
                if loaded && askRules.isEmpty {
                    Text(botID == nil ? "No Zip has an Ask First rule yet."
                                      : "\(botName) has no Ask First rule yet.")
                        .font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                }
                ForEach(askRules) { row($0) }
                Button { editing = RuleEdit(rule: nil) } label: {
                    Label("Add an Ask First Rule", systemImage: "plus.circle")
                        .font(Theme.secondary.weight(.medium)).foregroundStyle(Theme.ink)
                }
                .disabled(choices == nil || state.offline)
            } header: { Text("Ask First") } footer: {
                Text("A Zip with an Ask First rule shows you a card and waits for your okay, even for things it would normally just do. These rules can only add a question. They never let a Zip do more.")
            }
            Section {
                if loaded && allowRules.isEmpty {
                    Text("Nothing is always allowed yet. When a card offers Always Allow and you pick it, the rule shows up here.")
                        .font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                }
                ForEach(allowRules) { row($0) }
            } header: { Text("Allow") } footer: {
                Text("Paying, passwords and deleting are never covered by a rule.")
            }
        }
        .scrollContentBackground(.hidden)
        .background(Theme.canvas)
        .navigationTitle("Custom Rules")
        .confirmationBanner($saved)
        .refreshable { await load() }
        .task { await load() }
        .sheet(item: $editing) { edit in
            if let choices {
                RuleEditorSheet(rule: edit.rule, choices: choices, startBot: botID ?? state.bots.first?.id ?? "default") { message in
                    saved = Confirmation(title: message)
                    Task { await load() }
                }
                .environment(state)
            }
        }
    }

    private var botName: String { botID.flatMap { state.bot($0)?.name } ?? "This Zip" }

    private func row(_ r: PermissionRule) -> some View {
        Button { editing = RuleEdit(rule: r) } label: {
            HStack(alignment: .top, spacing: 12) {
                if r.bot_id == "*" {
                    Image(systemName: "person.3.fill")
                        .font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.ink)
                        .frame(width: 24, height: 24)
                        .background(Theme.field, in: Circle())
                        .accessibilityHidden(true)
                } else {
                    BotAvatar(bot: state.bot(r.bot_id), size: 24)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(Self.sentence(r, bot: state.bot(r.bot_id)?.name ?? r.bot_id))
                        .font(Theme.secondary.weight(.medium)).foregroundStyle(Theme.ink)
                        .multilineTextAlignment(.leading)
                    Text(Self.detail(r)).font(Theme.caption)
                        .foregroundStyle(r.stale == true ? Theme.taffyDeep : Theme.inkSoft)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Theme.inkSoft.opacity(0.6))
                    .accessibilityHidden(true)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityHint("Opens Edit Rule.")
        .swipeActions { Button("Remove", role: .destructive) { remove(r) } }
        .contextMenu {
            Button { editing = RuleEdit(rule: r) } label: { Label("Edit Rule", systemImage: "pencil") }
            Button(role: .destructive) { remove(r) } label: { Label("Remove Rule", systemImage: "trash") }
        }
    }

    /// The hub's sentence, or one made here for an older hub that sends none.
    static func sentence(_ r: PermissionRule, bot: String) -> String {
        if let s = r.sentence, !s.isEmpty { return s }
        switch r.kind {
        case "send": return "\(bot) can send to \(r.scope) without asking."
        case "write": return "\(bot) can make changes in \(r.scope) without asking."
        default: return "\(bot) can do this without asking: \(r.scope)."
        }
    }

    static func detail(_ r: PermissionRule) -> String {
        let added = "Made \(Date(timeIntervalSince1970: r.created_at).listStamp)"
        if r.stale == true { return "\(added). Not used in 60 days. Is it still needed?" }
        guard let last = r.last_used else { return r.isAskFirst ? "\(added). It has not paused anything yet." : "\(added). Not used yet." }
        let times = "\(r.uses) \(r.uses == 1 ? "time" : "times")"
        return r.isAskFirst
            ? "\(added). It paused something \(times), last \(Date(timeIntervalSince1970: last).listStamp)."
            : "\(added). Used \(times), last \(Date(timeIntervalSince1970: last).listStamp)."
    }

    private func remove(_ r: PermissionRule) {
        if state.fixtureMode { rules.removeAll { $0.id == r.id }; return }
        Task {
            do {
                try await state.client.deleteRule(r.id)
                rules.removeAll { $0.id == r.id }
                error = nil
            } catch { self.error = HubClient.describe(error) }
        }
    }

    private func load() async {
        #if DEBUG
        if state.fixtureMode {
            let page = Self.fixture
            rules = page.rules.filter { botID == nil || $0.bot_id == botID || $0.bot_id == "*" }
            choices = page.choices
            loaded = true
            return
        }
        #endif
        do {
            let page = try await state.client.customRules(bot: botID)
            rules = page.rules
            choices = page.choices
            error = nil
        } catch { self.error = HubClient.describe(error) }
        loaded = true
    }

    #if DEBUG
    static var fixture: HubClient.RulesPage {
        let t = Date().timeIntervalSince1970
        return HubClient.RulesPage(
            rules: [PermissionRule(id: 3, bot_id: "builder", kind: "ask:push", scope: "*", created_at: t - 3600,
                                   last_used: t - 600, uses: 1, stale: false, effect: "ask",
                                   sentence: "Builder asks before pushing code to any repo.", what: "push", where: "*"),
                    PermissionRule(id: 4, bot_id: "school", kind: "ask:change", scope: "google-docs", created_at: t - 7200,
                                   last_used: nil, uses: 0, stale: false, effect: "ask",
                                   sentence: "School asks before changing anything in Google Docs and Drive.",
                                   what: "change", where: "google-docs"),
                    PermissionRule(id: 1, bot_id: "clients", kind: "send", scope: "ken@brightpath.test",
                                   created_at: t - 86400 * 3, last_used: t - 7200, uses: 2, stale: false, effect: "allow",
                                   sentence: "Clients can email ken@brightpath.test without asking."),
                    PermissionRule(id: 2, bot_id: "money", kind: "write", scope: "its scheduled job Daily spending update",
                                   created_at: t - 86400 * 70, last_used: nil, uses: 0, stale: true, effect: "allow",
                                   sentence: "Money can make changes in its scheduled job Daily spending update without asking.")],
            choices: RuleChoices(what: [.init(id: "push", title: "Pushing code to any repo"),
                                        .init(id: "change", title: "Changing anything"),
                                        .init(id: "send", title: "Sending any message or email")],
                                 places: [.init(id: "*", title: "Anywhere"),
                                          .init(id: "google-docs", title: "Google Docs and Drive"),
                                          .init(id: "gmail", title: "Gmail"),
                                          .init(id: "calendar", title: "Google Calendar"),
                                          .init(id: "trello", title: "Trello"),
                                          .init(id: "github", title: "GitHub")]))
    }
    #endif
}

/// The sheet's subject: a rule to edit, or nil for a new Ask First rule.
struct RuleEdit: Identifiable {
    let id = UUID()
    let rule: PermissionRule?
}

/// Edit Rule, or a new Ask First rule. Any rule can move to another Zip. An Ask First rule can also change what it
/// covers and where. An Allow rule already covers one person or one job, so its scope stays as it is.
struct RuleEditorSheet: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    let rule: PermissionRule?
    let choices: RuleChoices
    var startBot: String
    var onSaved: (String) -> Void

    @State private var bot = ""
    @State private var what = "push"
    @State private var place = "*"
    @State private var typed = ""
    @State private var busy = false
    @State private var error: String?

    /// The picker's tag for a place Colin types himself.
    private static let typedPlace = "typed"
    private var isAllow: Bool { rule.map { !$0.isAskFirst } ?? false }

    /// For a push the choice is any repo or one branch; otherwise anywhere, a named place, or one he types.
    private var places: [RuleChoices.Option] {
        if what == "push" { return [.init(id: "*", title: "Any Repo"), .init(id: Self.typedPlace, title: "One Branch")] }
        return choices.places + [.init(id: Self.typedPlace, title: what == "send" ? "Someone or Somewhere Else" : "Another Website")]
    }

    private var typedPrompt: String {
        switch what {
        case "push": return "Branch, like main or staging"
        case "send": return "Email address, @company.com or a website"
        default: return "Website, like docs.google.com"
        }
    }

    private var whereValue: String {
        place == Self.typedPlace ? typed.trimmingCharacters(in: .whitespacesAndNewlines) : place
    }

    private var canSave: Bool { !busy && !bot.isEmpty && (place != Self.typedPlace || !whereValue.isEmpty) }

    var body: some View {
        NavigationStack {
            Form {
                if let error {
                    Section { Label(error, systemImage: "exclamationmark.circle.fill").foregroundStyle(Theme.danger) }
                }
                Section {
                    Picker("Zip", selection: $bot) {
                        if !isAllow { Text("Every Zip").tag("*") }
                        ForEach(state.bots) { Text($0.name).tag($0.id) }
                    }
                } header: { Text("Which Zip") }
                if isAllow, let rule {
                    Section {
                        Text(CustomRulesView.sentence(rule, bot: state.bot(rule.bot_id)?.name ?? rule.bot_id))
                            .font(Theme.secondary).foregroundStyle(Theme.ink)
                    } header: { Text("What It Covers") } footer: {
                        Text("This rule already covers only one person or one job, so it cannot be narrowed further. To allow something else, remove this rule and pick Always Allow on a new card.")
                    }
                } else {
                    Section {
                        Picker("Ask Before", selection: $what) {
                            ForEach(choices.what) { Text($0.title).tag($0.id) }
                        }
                        .onChange(of: what) { _, _ in
                            if !places.contains(where: { $0.id == place }) { place = "*" }
                        }
                    } header: { Text("Ask Before") }
                    Section {
                        Picker("Where", selection: $place) {
                            ForEach(places) { Text($0.title).tag($0.id) }
                        }
                        if place == Self.typedPlace {
                            TextField(typedPrompt, text: $typed)
                                .textInputAutocapitalization(.never).autocorrectionDisabled()
                                .keyboardType(what == "push" ? .default : .URL)
                        }
                    } header: { Text("Where") } footer: {
                        Text("The Zip shows you a card and waits for your okay before it does this. It never lets a Zip do more than it already could.")
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background(Theme.canvas)
            .safeAreaInset(edge: .top, spacing: 0) {
                SheetBar(title: rule == nil ? "New Ask First Rule" : "Edit Rule", onCancel: { dismiss() },
                         confirm: busy ? "Saving" : "Save", confirmEnabled: canSave,
                         onConfirm: { Task { await save() } })
            }
            .toolbar(.hidden, for: .navigationBar)
            .onAppear(perform: start)
        }
    }

    private func start() {
        guard bot.isEmpty else { return }
        bot = rule?.bot_id ?? startBot
        guard let rule, rule.isAskFirst else { return }
        what = rule.what ?? "push"
        let w = rule.where ?? "*"
        if places.contains(where: { $0.id == w && $0.id != Self.typedPlace }) {
            place = w
        } else {
            place = Self.typedPlace
            typed = w
        }
    }

    private func save() async {
        error = nil
        if state.fixtureMode { onSaved("Saved to Custom Rules"); dismiss(); return }
        busy = true
        defer { busy = false }
        do {
            let r: HubClient.RuleSaved
            if let rule {
                r = try await state.client.editRule(rule.id, bot: bot, what: isAllow ? nil : what,
                                                    where: isAllow ? nil : whereValue)
            } else {
                r = try await state.client.addAskFirstRule(bot: bot, what: what, where: whereValue)
            }
            onSaved(r.message)
            dismiss()
        } catch {
            self.error = HubClient.describe(error)
        }
    }
}
