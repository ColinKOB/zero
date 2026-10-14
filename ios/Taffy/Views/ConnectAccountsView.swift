import SwiftUI

/// Connect Google (or Trello, or another connector such as Notion): what the Zips can do with each account, a button
/// that opens the Connectors page of the person's Claude account (or the Apps page of their ChatGPT account, when
/// that is the only plan they have), plain steps, and rows that turn green as soon as the hub sees the connection.
/// The person connects in Claude or ChatGPT, never in Zero, so Zero never sees a Google password
/// (docs/specs/2026-10-08-google-connect-design.md, docs/qa/2026-10-08-google-connect-v2.md).
///
/// DEBUG: `-open connect` (or `-open connect trello`, `-open connect notion`) shows it from Home; `-accounts <variant>`
/// sets the fixture status (none, partial, all, expired, signedout, chatgpt).
struct ConnectAccountsView: View {
    @Environment(AppState.self) private var state
    @Environment(\.openURL) private var openURL
    @Environment(\.dismiss) private var dismiss
    let group: AccountGroup
    /// Shown as a sheet (from a Zip's card or onboarding): adds Done.
    var modal = false
    private var model: AccountsModel { AccountsModel.shared }

    private var status: AccountsStatus? { model.status }
    private var done: Bool { status?.allConnected(group) ?? false }
    private var account: String { status?.claude.account ?? "" }
    private var names: String { ConnectAccountsView.list(group.uses.map(\.name)) }
    /// Where the person connects this group: "Claude", or "ChatGPT" when that is their only plan.
    private var plan: String { status?.planName(for: group) ?? "Claude" }
    private var onChatGPT: Bool { plan == "ChatGPT" }
    /// Only a ChatGPT plan, and this account works through Claude only for now (Trello, or a Claude connector).
    private var needsClaude: Bool { (status?.chatGPTOnly ?? false) && !group.chatGPTWorks }

    var body: some View {
        ScrollViewReader { proxy in
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                header
                statusCard.padding(.top, 22)
                if let problem = claudeProblem { problemNote(problem).padding(.top, 14) }
                if needsClaude {
                    problemNote(("\(group.provider) Needs a Claude Plan",
                                 "Your ChatGPT plan works for Google but not for \(group.provider) yet. If you get a Claude plan later, connect \(group.provider) in Claude and it shows up here."))
                        .padding(.top, 14)
                } else {
                    actions
                }
                uses
                if !needsClaude {
                    steps.id("steps")
                    disconnect.id("disconnect")
                }
                privacy
            }
            .frame(maxWidth: 520, alignment: .leading)
            .padding(.horizontal, 24)
            .padding(.vertical, Theme.onMac ? 28 : 20)
            .frame(maxWidth: .infinity)
        }
        #if DEBUG
        .task {
            let a = CommandLine.arguments
            if let i = a.firstIndex(of: "-scrollto"), i + 1 < a.count {
                try? await Task.sleep(for: .milliseconds(900))
                proxy.scrollTo(a[i + 1], anchor: .top)
            }
        }
        #endif
        }
        .background(Theme.canvas)
        .navigationTitle("")
        #if !targetEnvironment(macCatalyst)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .toolbar {
            if modal { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .task { await model.refresh(state) }
        .onAppear { model.startPolling(state) }
        .onDisappear { model.stopPolling() }
        .sensoryFeedback(.success, trigger: done)
    }

    // MARK: Pieces

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            Image(systemName: done ? "checkmark" : "link")
                .font(.system(size: 22, weight: .semibold))
                .foregroundStyle(Self.charcoal)
                .frame(width: 52, height: 52)
                .background(done ? Self.green.opacity(0.9) : Self.yellow, in: Circle())
                .padding(.bottom, 16)
                .accessibilityHidden(true)
            Text(done ? "\(group.provider) Is Connected" : group.title)
                .font(Theme.text(28, .bold)).foregroundStyle(Theme.ink)
                .accessibilityAddTraits(.isHeader)
            Text(done ? "Your Zips can use \(names) now."
                      : group.isMore ? "Your Zips use \(names) through your Claude account, where you connected it."
                      : needsClaude ? "Your Zips use \(names) through a Claude plan."
                      : "Your Zips use \(names) through your \(plan) account. You connect \(group.provider) once in \(plan), and every Zip can use it.")
                .font(Theme.text(16)).foregroundStyle(Theme.ink.opacity(0.85))
                .lineSpacing(2)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 10)
        }
    }

    private var statusCard: some View {
        VStack(spacing: 0) {
            ForEach(Array(group.uses.enumerated()), id: \.element.id) { i, use in
                AccountRow(use: use, service: status?.service(use.id), loading: status == nil)
                if i < group.uses.count - 1 { Divider().padding(.leading, 58) }
            }
            Divider()
            HStack(spacing: 8) {
                if model.checking || status == nil {
                    ProgressView().controlSize(.small)
                    Text("Checking").font(Theme.text(13)).foregroundStyle(Theme.inkSoft)
                } else if let e = model.error {
                    Text(e).font(Theme.text(13)).foregroundStyle(Theme.danger)
                } else {
                    Image(systemName: "arrow.triangle.2.circlepath").font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Theme.inkSoft)
                    Text("\(state.bot("default")?.name ?? "Zero") checks again every few seconds while this is open.")
                        .font(Theme.text(13)).foregroundStyle(Theme.inkSoft)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 16).padding(.vertical, 11)
        }
        .background(Theme.raised, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(Theme.rule, lineWidth: 1))
    }

    private var claudeProblem: (String, String)? {
        guard let s = status, s.claudeProblem else { return nil }
        let place = s.claudeComputer
        let elsewhere = place != "your Mac"
        switch s.claude.state {
        case "signed_out":
            return ("Sign In to Claude First", elsewhere
                    ? "Your Zips reach your accounts through Claude on \(place), and Claude is signed out there. Signing in has to happen on that computer, so do it there or ask the person who set it up. Then come back here."
                    : "Your Zips reach your accounts through Claude on \(place). Sign in to Claude there, then come back here.")
        case "api_key":
            return ("Claude Needs Your Plan",
                    "Accounts you connect in Claude only work when Claude on \(place) is signed in with your Claude plan. Sign in with your plan there to use them."
                    + (elsewhere ? " If someone else set up that computer, ask them to do it." : ""))
        default:
            return ("Claude Isn't Set Up Yet", elsewhere
                    ? "Finish setting up Claude on \(place), or ask the person who set it up. Then come back here."
                    : "Finish setting up Claude on \(place), then come back here.")
        }
    }

    private func problemNote(_ p: (String, String)) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "exclamationmark.circle.fill").font(.system(size: 18)).foregroundStyle(Self.orange)
            VStack(alignment: .leading, spacing: 4) {
                Text(p.0).font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink)
                Text(p.1).font(Theme.text(15)).foregroundStyle(Theme.ink.opacity(0.85))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Self.orange.opacity(0.1), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private var actions: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 10) { actionButtons }
            VStack(alignment: .leading, spacing: 10) { actionButtons }
        }
        .padding(.top, 22)
    }

    @ViewBuilder private var actionButtons: some View {
        if done && modal {
            primary("Done") { dismiss() }
        } else {
            primary("Open \(plan) Settings", action: openClaude)
        }
        secondary("Check Again") { Task { await model.refresh(state, fresh: true) } }
    }

    private var uses: some View {
        section("What Can Your Zips Do?") {
            ForEach(group.uses, id: \.id) { use in
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: use.symbol).font(.system(size: 16, weight: .medium)).foregroundStyle(Theme.ink)
                        .frame(width: 24).padding(.top, 2)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(use.name).font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink)
                        Text(use.can).font(Theme.text(15)).foregroundStyle(Theme.ink.opacity(0.85))
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            if !group.isMore {
                Text("Anything else, like sending an email, a Zip asks you to do yourself.")
                    .font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var steps: some View {
        let first = group.uses.first?.name ?? group.provider
        let rest = ConnectAccountsView.list(group.uses.dropFirst().map(\.name))
        let place = status?.claudeComputer ?? "your Mac"
        var lines: [String]
        if onChatGPT {
            lines = [
                "Tap Open ChatGPT Settings. The Apps page of your ChatGPT account opens.",
                "Make sure you're signed in to the ChatGPT account your Zips use on \(place).",
                "Find \(first), then tap Connect.",
                "Pick your \(group.provider) account and allow access.",
            ]
        } else if group.isMore {
            lines = [
                "Tap Open Claude Settings. The Connectors page of your Claude account opens.",
                "Choose \(first). If it asks you to sign in again, tap Connect and allow access.",
            ]
        } else {
            lines = [
                "Tap Open Claude Settings. The Connectors page of your Claude account opens.",
                account.isEmpty ? "Make sure you're signed in to the Claude account your Zips use on \(place)."
                                : "Make sure you're signed in to the Claude account your Zips use: \(account).",
                "Tap the plus button next to Connectors, choose \(first), then tap Connect.",
                "Pick your \(group.provider) account and allow access.",
            ]
        }
        if !rest.isEmpty { lines.append("Do the same for \(rest).") }
        lines.append("Come back to Zero. Each account turns green here when it's ready.")
        return section("How Do I Connect?") {
            ForEach(Array(lines.enumerated()), id: \.offset) { i, line in
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text("\(i + 1)").font(Theme.text(13, .bold)).foregroundStyle(Self.charcoal)
                        .frame(width: 22, height: 22).background(Self.yellow, in: Circle())
                    Text(line).font(Theme.text(15)).foregroundStyle(Theme.ink.opacity(0.85))
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Text(onChatGPT ? "If you don't see \(first) there, your ChatGPT plan may not include apps. On a ChatGPT Business or Enterprise plan, an admin may have to turn on \(group.provider) apps first."
                           : "On a Claude Team or Enterprise plan, an owner has to turn on \(group.provider) for your organization first.")
                .font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var disconnect: some View {
        section("How Do I Disconnect?") {
            bullet("Open \(plan) Settings, choose the account under \(onChatGPT ? "Apps" : "Connectors") and tap Disconnect.")
            bullet("Your Zips lose access right away. When a Zip needs that account again, it asks you with a card in its chat.")
            bullet("\(plan) stops using that account in your \(plan) chats too, because it is the same connection.")
            if group.id == "google" {
                bullet("To remove \(plan)'s access on Google's side as well, remove \(plan) from your Google account's connections.")
                quiet("Open Google Account Connections") {
                    if let u = URL(string: status?.google_connections_url ?? "https://myaccount.google.com/connections") { openURL(u) }
                }
            }
        }
    }

    private var privacy: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "lock.fill").font(.system(size: 15, weight: .semibold)).foregroundStyle(Theme.ink)
                .padding(.top, 2)
            Text("Zero never sees your \(group.provider) password. You sign in on \(group.provider)'s own page, and what your Zips read goes to \(plan) like the rest of their work.")
                .font(Theme.text(15)).foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .padding(.top, 26)
    }

    private func openClaude() {
        #if DEBUG
        if state.fixtureMode { model.simulateConnecting(group); return }
        #endif
        if let u = URL(string: status?.connectURL(for: group) ?? "https://claude.ai/customize/connectors") { openURL(u) }
    }

    // MARK: Building blocks (the voice setup's look)

    static let yellow = Color(red: 249 / 255, green: 196 / 255, blue: 16 / 255)
    static let charcoal = Color(red: 24 / 255, green: 24 / 255, blue: 27 / 255)
    static let green = Color(hex: "#10A35A")
    static let orange = Color(hex: "#E07B00")

    static func list(_ items: [String]) -> String {
        switch items.count {
        case 0: return ""
        case 1: return items[0]
        default: return items.dropLast().joined(separator: ", ") + " and " + items.last!
        }
    }

    private func section<C: View>(_ title: String, @ViewBuilder _ content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title).font(Theme.text(18, .semibold)).foregroundStyle(Theme.ink).accessibilityAddTraits(.isHeader)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 28)
    }

    private func bullet(_ line: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Circle().fill(Theme.inkSoft).frame(width: 5, height: 5).alignmentGuide(.firstTextBaseline) { $0[.bottom] + 5 }
            Text(line).font(Theme.text(15)).foregroundStyle(Theme.ink.opacity(0.85))
                .fixedSize(horizontal: false, vertical: true)
        }
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
            .font(Theme.text(15, .semibold))
            .foregroundStyle(Theme.ink)
            .underline()
            .frame(minHeight: 36)
    }
}

/// One account and its live status: green when the Zips can use it.
struct AccountRow: View {
    let use: AccountGroup.Use
    let service: AccountsStatus.Service?
    var loading = false
    var compact = false

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: use.symbol)
                .font(.system(size: 17, weight: .medium)).foregroundStyle(Theme.ink)
                .frame(width: 34, height: 34)
                .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
            VStack(alignment: .leading, spacing: 2) {
                Text(use.name).font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink)
                if !compact, let service, !service.connected, service.state != "not_connected", service.state != "needs_claude" {
                    Text(service.detail).font(Theme.text(13)).foregroundStyle(Theme.inkSoft)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 8)
            AccountPill(state: loading ? nil : service?.state)
        }
        .padding(.horizontal, 12).padding(.vertical, 11)
        .accessibilityElement(children: .combine)
    }
}

struct AccountPill: View {
    /// nil while the first check runs.
    let state: String?

    var body: some View {
        let (text, color): (String, Color) = {
            switch state {
            case nil: return ("Checking", Theme.inkSoft)
            case "connected": return ("Connected", ConnectAccountsView.green)
            case "needs_sign_in": return ("Sign In Again", ConnectAccountsView.orange)
            case "turned_off": return ("Turned Off", ConnectAccountsView.orange)
            case "claude_signed_out", "claude_api_key", "claude_sign_in_again", "claude_missing":
                return ("Waiting on Claude", ConnectAccountsView.orange)
            case "trouble", "unknown": return ("Can't Check", Theme.inkSoft)
            case "needs_claude": return ("Needs Claude", Theme.inkSoft)
            case "needs_own_plan": return ("Needs Your Own Plan", Theme.inkSoft)
            default: return ("Not Connected", Theme.inkSoft)
            }
        }()
        HStack(spacing: 5) {
            if state == "connected" {
                Image(systemName: "checkmark").font(.system(size: 11, weight: .bold))
            } else {
                Circle().frame(width: 7, height: 7)
            }
            Text(text).font(Theme.text(13, .semibold))
        }
        .foregroundStyle(color)
        .fixedSize()
        .padding(.horizontal, 10).frame(height: 28)
        .background(color.opacity(state == "connected" ? 0.14 : 0.1), in: Capsule())
        .animation(.smooth, value: state)
    }
}

// MARK: Settings

/// Settings' Connected Accounts section: Google and Trello for everyone, then every other connector on the person's
/// Claude account (Notion, Slack and so on), each opening its Connect flow.
struct ConnectedAccountsSection: View {
    @Environment(AppState.self) private var state

    var body: some View {
        let status = AccountsModel.shared.status
        Section {
            NavigationLink { ConnectAccountsView(group: .google) } label: { label(.google, symbol: "envelope") }
            NavigationLink { ConnectAccountsView(group: .trello) } label: { label(.trello, symbol: "rectangle.split.3x1") }
            ForEach(status?.more ?? []) { s in
                let group = AccountGroup.more(id: s.id, name: s.name)
                NavigationLink { ConnectAccountsView(group: group) } label: { label(group, symbol: "square.stack.3d.up") }
            }
        } header: { Text("Connected Accounts") } footer: {
            Text(status?.chatGPTOnly == true
                 ? "Your Zips use Google through your ChatGPT account. You connect and disconnect it in ChatGPT."
                 : "Your Zips use these through your Claude account. You connect and disconnect them in Claude.")
        }
        .task { await AccountsModel.shared.refresh(state) }
    }

    private func label(_ group: AccountGroup, symbol: String) -> some View {
        let s = AccountsModel.shared.status
        let rows = s?.services(in: group) ?? []
        let on = rows.filter(\.connected).count
        let needsYou = rows.first { $0.state == "needs_sign_in" || $0.state.hasPrefix("claude_") }
        let line: String = {
            guard s != nil else { return "Checking" }
            if let needsYou { return needsYou.state == "needs_sign_in" ? "\(needsYou.name) needs you to sign in again" : "Waiting on Claude" }
            if rows.count == 1 { return on == 1 ? "Connected" : rows[0].state == "needs_claude" ? "Needs a Claude plan" : "Not Connected" }
            return on == 0 ? "Not Connected" : on == rows.count ? "Gmail, Calendar and Drive connected" : "\(on) of \(rows.count) connected"
        }()
        return HStack(spacing: 12) {
            Label {
                VStack(alignment: .leading, spacing: 2) {
                    Text(group.provider).font(Theme.body)
                    Text(line).font(Theme.caption).foregroundStyle(needsYou != nil ? ConnectAccountsView.orange : Theme.inkSoft)
                }
            } icon: { Image(systemName: symbol) }
            Spacer()
            if s != nil, !rows.isEmpty, on == rows.count {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(ConnectAccountsView.green)
                    .accessibilityLabel("Connected")
            }
        }
    }
}

// MARK: The card a Zip raises in its chat

/// A Zip needs an account that isn't connected: instead of failing quietly it asks with this card, which opens
/// Connect Google (or Connect Trello). The card closes by itself once the account is connected, and the Zip
/// picks its work back up.
struct AccountAskCard: View {
    @Environment(AppState.self) private var state
    let message: ChatMessage
    let botID: String
    var gap: CGFloat = 48
    @State private var connecting = false

    private var bot: Bot? { state.bot(message.botID ?? botID) }
    private var serviceID: String { message.details.first ?? "gmail" }
    private var group: AccountGroup { AccountGroup.of(service: serviceID, name: message.label) }
    private var plan: String { AccountsModel.shared.status?.planName(for: group) ?? "Claude" }
    private var serviceName: String { message.label ?? group.provider }
    private var symbol: String { group.uses.first { $0.id == serviceID }?.symbol ?? "link" }
    /// The Zip's request as the end of a sentence: "Needed to find this month's receipts."
    private var neededFor: String {
        let s = message.text.trimmingCharacters(in: .whitespacesAndNewlines)
        let lowered = s.prefix(1).lowercased() + s.dropFirst()
        return lowered.hasSuffix(".") ? lowered : lowered + "."
    }

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 6) {
                    BotAvatar(bot: bot, size: 18)
                    Text("\(bot?.name ?? "A Zip") needs \(serviceName)").font(Theme.text(15, .medium)).foregroundStyle(Theme.ink)
                    Spacer()
                    Button {
                        guard let id = message.feedID else { return }
                        Task {
                            if !state.fixtureMode { try? await state.client.closeAccountCard(bot: message.botID ?? botID, feedID: id) }
                            await state.loadChat(botID)
                        }
                    } label: {
                        Image(systemName: "xmark").font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.inkSoft)
                            .frame(width: 30, height: 30).contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Close")
                }
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: symbol)
                        .font(.system(size: 18, weight: .medium)).foregroundStyle(Theme.ink)
                        .frame(width: 40, height: 40)
                        .background(Theme.field, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    VStack(alignment: .leading, spacing: 6) {
                        Text("\(serviceName) isn't connected").font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink)
                        if !message.text.isEmpty {
                            Text("Needed to \(neededFor)").font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                                .lineLimit(3)
                        }
                        Text(HomeModel.shared.nativeAccounts && group.id == "google"
                             ? "Sign in with Google once. \(bot?.name ?? "The Zip") picks this back up as soon as it's connected."
                             : "You connect \(group.provider) once in \(plan). \(bot?.name ?? "The Zip") picks this back up as soon as it's connected.")
                            .font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.canvas, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                Button { connecting = true } label: {
                    Text(HomeModel.shared.nativeAccounts && group.id == "google" ? "Add Your Email" : group.title)
                        .font(Theme.text(17, .medium)).foregroundStyle(Theme.mineText)
                        .frame(maxWidth: .infinity, minHeight: 48)
                        .background(Theme.mine, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                }
                .buttonStyle(PressStyle())
            }
            .padding(14)
            .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
            .frame(maxWidth: Theme.onMac ? Theme.bubbleMax : .infinity)
            Spacer(minLength: gap)
        }
        .sheet(isPresented: $connecting, onDismiss: { Task { await state.loadChat(botID) } }) {
            NavigationStack {
                if HomeModel.shared.nativeAccounts && group.id == "google" {
                    MailAndCalendarView(modal: true)
                } else {
                    ConnectAccountsView(group: group, modal: true)
                }
            }
            .environment(state)
        }
    }
}

// MARK: Connect Apps

/// Connect Apps, laid out like ChatGPT's Plugins page (Colin, Oct 12): the title and one plain line, a rounded search
/// field, the connected apps as a row of logo tiles, then Popular and each category as a list of rows (two columns on
/// iPad and Mac, one on iPhone). Each row has the app's real logo on a white tile, its name, a few gray words, and a
/// plus to connect it, or a "..." for details and Disconnect once it is connected. Logos are bundled in the app
/// (Assets.xcassets/AppLogos: Simple Icons in each brand's color, and Slack's and Outlook's official app icons).
/// Trello, Google Drive, Outlook, Dropbox, Notion, Linear and GitHub connect in Zero itself; Gmail and Google Calendar
/// use Sign In with Google; the rest work through the person's Claude or ChatGPT account until Zero connects them
/// directly, and look the same.
///
/// DEBUG: `-fixture -open apps` with `-apps none | some | expired`; `-trello paste | done` opens the Trello walkthrough;
/// `-appDetail <id>` opens an app's details.
struct ConnectAppsView: View {
    @Environment(AppState.self) private var state
    @Environment(\.openURL) private var openURL
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var query = ""
    @State private var notice: String?
    @State private var trello: TrelloConnectView.Phase?
    @State private var device: DeviceConnectView.Sheet?
    @State private var managingMail = false
    @State private var confirmDisconnect: AppsCatalog.App?
    @State private var detail: AppsCatalog.App?
    private var model: AppsModel { AppsModel.shared }
    /// Two columns where there is room (iPad and Mac), one on iPhone.
    private var wide: Bool { Theme.onMac || sizeClass == .regular }

    var body: some View {
        ScrollViewReader { scroller in
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                header
                search.padding(.top, 20)
                if let notice { noteCard(notice).padding(.top, 16) }
                if let c = model.catalog {
                    if query.isEmpty && !c.connectedApps.isEmpty { connectedRow(c).padding(.top, 28) }
                    let groups = sections(c)
                    if groups.isEmpty {
                        Text("No apps match \u{201C}\(query)\u{201D}.")
                            .font(Theme.text(15)).foregroundStyle(Theme.inkSoft).padding(.top, 28)
                    }
                    ForEach(groups, id: \.0) { name, apps in
                        section(name, apps).padding(.top, 30).id(name)
                    }
                } else if let e = model.error {
                    noteCard(e).padding(.top, 24)
                } else {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, 40)
                }
                Text("Your Zips read what you connect here. They ask you on a card before they change anything, and you can disconnect an app at any time.")
                    .font(Theme.text(13)).foregroundStyle(Theme.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 32)
            }
            .frame(maxWidth: wide ? 960 : 600, alignment: .leading)
            .padding(.horizontal, 20)
            .padding(.vertical, Theme.onMac ? 28 : 16)
            .frame(maxWidth: .infinity)
        }
        #if DEBUG
        .task {
            let a = CommandLine.arguments
            if let i = a.firstIndex(of: "-appsScroll"), i + 1 < a.count {
                try? await Task.sleep(for: .milliseconds(1200))
                scroller.scrollTo(a[i + 1], anchor: .top)
            }
        }
        #endif
        }
        .background(Theme.canvas)
        .navigationTitle("")
        #if !targetEnvironment(macCatalyst)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .task {
            await model.refresh(state)
            #if DEBUG
            let a = CommandLine.arguments
            if let i = a.firstIndex(of: "-trello"), i + 1 < a.count {
                trello = a[i + 1] == "done" ? .done("Sam Rivera") : a[i + 1] == "paste" ? .paste : .intro
            }
            if let i = a.firstIndex(of: "-appDetail"), i + 1 < a.count { detail = model.catalog?.app(a[i + 1]) }
            #endif
        }
        .sheet(item: $trello) { phase in
            NavigationStack { TrelloConnectView(phase: phase) }
        }
        .sheet(item: $device) { sheet in
            NavigationStack { DeviceConnectView(sheet: sheet) }
        }
        .sheet(isPresented: $managingMail) {
            NavigationStack { MailAndCalendarView(modal: true) }
        }
        .sheet(item: $detail) { app in
            AppDetailSheet(app: model.catalog?.app(app.id) ?? app, working: model.working == app.id) { action in
                detail = nil
                switch action {
                case .connect: connect(app)
                case .claude: openClaude()
                case .disconnect: confirmDisconnect = app
                case .manageMail: managingMail = true
                }
            }
            .presentationDetents([.medium, .large])
            .presentationBackground(Theme.canvas)
        }
        .confirmationDialog(confirmDisconnect.map { "Disconnect \($0.name)?" } ?? "", isPresented: Binding(
            get: { confirmDisconnect != nil }, set: { if !$0 { confirmDisconnect = nil } }), titleVisibility: .visible) {
            Button("Disconnect", role: .destructive) {
                guard let app = confirmDisconnect else { return }
                Task { notice = await model.disconnect(app.id, state) }
            }
        } message: {
            Text("Your Zips stop using it. You can connect it again at any time.")
        }
    }

    // MARK: Pieces

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Connect Apps").font(Theme.text(30, .bold)).foregroundStyle(Theme.ink)
                .accessibilityAddTraits(.isHeader)
            Text("Connect the apps you use, and your Zips can work across them.")
                .font(Theme.text(16)).foregroundStyle(Theme.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var search: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass").foregroundStyle(Theme.inkSoft).accessibilityHidden(true)
            TextField("Search Apps", text: $query)
                .font(Theme.text(16))
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            if !query.isEmpty {
                Button { query = "" } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(Theme.inkSoft) }
                    .buttonStyle(.plain).accessibilityLabel("Clear Search")
            }
        }
        .padding(.horizontal, 16).frame(height: 46)
        .background(Theme.raised, in: Capsule())
        .overlay(Capsule().stroke(Theme.rule, lineWidth: 1))
    }

    /// The connected apps' logos only, each opening that app's details.
    private func connectedRow(_ c: AppsCatalog) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionTitle("Connected")
            Divider()
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(c.connectedApps) { app in
                        Button { detail = app } label: { AppLogoTile(app: app, size: 48) }
                            .buttonStyle(PressStyle())
                            .accessibilityLabel("\(app.name), connected")
                            .accessibilityHint("Shows its details")
                    }
                }
                .padding(.vertical, 2)
            }
        }
    }

    private func sectionTitle(_ name: String) -> some View {
        Text(name).font(Theme.text(20, .semibold)).foregroundStyle(Theme.ink).accessibilityAddTraits(.isHeader)
    }

    /// Popular first, then every other app under its category. A search shows the matches by name in one list.
    private func sections(_ c: AppsCatalog) -> [(String, [AppsCatalog.App])] {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        if !q.isEmpty {
            let found = c.apps.filter { $0.name.lowercased().contains(q) }
            return found.isEmpty ? [] : [("Results", found)]
        }
        let popular = c.apps.filter(\.isPopular)
        var out: [(String, [AppsCatalog.App])] = popular.isEmpty ? [] : [("Popular", popular)]
        for name in c.categories {
            let apps = c.apps.filter { $0.category == name && !$0.isPopular }
            if !apps.isEmpty { out.append((name, apps)) }
        }
        return out
    }

    private func section(_ name: String, _ apps: [AppsCatalog.App]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionTitle(name)
            LazyVGrid(columns: wide ? [GridItem(.flexible(), spacing: 32), GridItem(.flexible())] : [GridItem(.flexible())],
                      alignment: .leading, spacing: 4) {
                ForEach(apps) { app in row(app) }
            }
        }
    }

    private func row(_ app: AppsCatalog.App) -> some View {
        HStack(spacing: 14) {
            HStack(spacing: 14) {
                AppLogoTile(app: app, size: 44)
                VStack(alignment: .leading, spacing: 2) {
                    Text(app.name).font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink)
                    Text(subtitle(app))
                        .font(Theme.text(14))
                        .foregroundStyle(app.needsYou ? ConnectAccountsView.orange : Theme.inkSoft)
                        .lineLimit(2)
                }
                Spacer(minLength: 8)
            }
            .contentShape(Rectangle())
            .onTapGesture { detail = app }
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isButton)
            .accessibilityHint("Shows its details")
            trailing(app)
        }
        .padding(.vertical, 10)
    }

    /// The gray words under the name: the account for an app connected here, a problem to fix, or what it is for.
    private func subtitle(_ app: AppsCatalog.App) -> String {
        if app.needsYou { return "Connect it again" }
        if app.state == "connected", let account = app.account, !account.isEmpty { return account }
        if app.state == "elsewhere" { return app.via == "chatgpt" ? "Connected in ChatGPT" : "Connected in Claude" }
        return app.blurbText
    }

    @ViewBuilder private func trailing(_ app: AppsCatalog.App) -> some View {
        if model.working == app.id {
            ProgressView().controlSize(.small).frame(width: 40, height: 40)
        } else if app.isConnected {
            Menu {
                Button("Details") { detail = app }
                if app.state == "connected" {
                    if app.id == "gmail" || app.id == "google_calendar" {
                        Button("Manage Email and Calendars") { managingMail = true }
                    } else {
                        Button("Disconnect", role: .destructive) { confirmDisconnect = app }
                    }
                } else {
                    Button(app.via == "chatgpt" ? "Manage in ChatGPT" : "Manage in Claude") { openClaude() }
                }
            } label: {
                Image(systemName: "ellipsis").font(.system(size: 17, weight: .semibold)).foregroundStyle(Theme.ink)
                    .frame(width: 40, height: 40).contentShape(Rectangle())
            }
            .accessibilityLabel("More for \(app.name)")
        } else if app.state == "unavailable" || app.state == "not_set_up" || app.action == "none" {
            Text("Soon").font(Theme.text(13, .medium)).foregroundStyle(Theme.inkSoft).frame(minWidth: 40)
        } else {
            Button { app.action == "claude" ? openClaude() : connect(app) } label: {
                Image(systemName: app.needsYou ? "arrow.clockwise" : "plus")
                    .font(.system(size: 18, weight: .regular))
                    .foregroundStyle(app.needsYou ? ConnectAccountsView.orange : Theme.ink)
                    .frame(width: 40, height: 40).contentShape(Rectangle())
            }
            .buttonStyle(PressStyle())
            .accessibilityLabel(app.action == "claude" ? "Add \(app.name) in your Claude account"
                                : "\(app.needsYou ? "Reconnect" : "Connect") \(app.name)")
        }
    }

    private func noteCard(_ text: String) -> some View {
        Text(text).font(Theme.text(15)).foregroundStyle(Theme.ink)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 14).padding(.vertical, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    private func openClaude() {
        let status = AccountsModel.shared.status
        let link = status?.claude_connect_url ?? status?.connect_url ?? "https://claude.ai/customize/connectors"
        if let url = URL(string: link) { openURL(url) }
    }

    private func connect(_ app: AppsCatalog.App) {
        notice = nil
        if app.id == "trello" { trello = .intro; return }
        Task {
            switch await model.connect(app, state) {
            case .connected: notice = "\(app.name) is connected. Your Zips can use it now."
            case .failed(let message): notice = message
            case .device(let start): device = .init(app: app, start: start)
            case .closed, .paste: break
            }
        }
    }
}

/// An app's real logo on a rounded white tile. Simple Icons glyphs sit inside the tile in the brand's color; full app
/// icons (Slack, Outlook) fill it. An app with no bundled logo shows its SF Symbol.
struct AppLogoTile: View {
    let app: AppsCatalog.App
    var size: CGFloat = 44
    /// Logos that are whole app icons, drawn edge to edge.
    static let fullIcons: Set<String> = ["slack", "outlook"]

    var body: some View {
        let radius = size * 0.24
        let shape = RoundedRectangle(cornerRadius: radius, style: .continuous)
        Group {
            if UIImage(named: "AppLogo-\(app.id)") != nil {
                if Self.fullIcons.contains(app.id) {
                    Image("AppLogo-\(app.id)").resizable().renderingMode(.original).scaledToFill()
                } else {
                    Image("AppLogo-\(app.id)").resizable().renderingMode(.original).scaledToFit()
                        .padding(size * 0.22)
                }
            } else {
                Image(systemName: app.symbol).font(.system(size: size * 0.42, weight: .medium)).foregroundStyle(Theme.ink)
            }
        }
        .frame(width: size, height: size)
        .background(Color.white)
        .clipShape(shape)
        .overlay(shape.stroke(Color.black.opacity(0.08), lineWidth: 1))
        .accessibilityHidden(true)
    }
}

/// One app's details: its logo and name, what the Zips do with it, how it is connected, and the one thing to do.
struct AppDetailSheet: View {
    enum Action { case connect, claude, disconnect, manageMail }
    let app: AppsCatalog.App
    let working: Bool
    let act: (Action) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(spacing: 14) {
                        AppLogoTile(app: app, size: 64)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(app.name).font(Theme.text(24, .bold)).foregroundStyle(Theme.ink)
                                .accessibilityAddTraits(.isHeader)
                            Text(app.blurbText).font(Theme.text(15)).foregroundStyle(Theme.inkSoft)
                        }
                    }
                    Text(app.can).font(Theme.text(16)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    if !status.isEmpty {
                        Label(status, systemImage: app.isConnected ? "checkmark.circle.fill" : app.needsYou ? "exclamationmark.circle" : "info.circle")
                            .font(Theme.text(15))
                            .foregroundStyle(app.isConnected ? ConnectAccountsView.green : app.needsYou ? ConnectAccountsView.orange : Theme.inkSoft)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let primary {
                        Button { act(primary.0) } label: {
                            Text(primary.1).font(Theme.text(16, .semibold))
                                .foregroundStyle(primary.0 == .disconnect ? Theme.danger : Theme.actionGlyph)
                                .frame(maxWidth: .infinity, minHeight: 48)
                                .background(primary.0 == .disconnect ? Theme.danger.opacity(0.1) : Theme.action, in: Capsule())
                        }
                        .buttonStyle(PressStyle())
                        .disabled(working)
                        .padding(.top, 4)
                    }
                }
                .padding(24)
            }
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } } }
        }
    }

    /// How it is connected now, in one sentence.
    private var status: String {
        if app.state == "connected" {
            return (app.account ?? "").isEmpty ? "Connected." : "Connected as \(app.account ?? "")."
        }
        return app.detail
    }

    private var primary: (Action, String)? {
        if app.state == "connected" {
            return app.id == "gmail" || app.id == "google_calendar" ? (.manageMail, "Manage Email and Calendars")
                                                                      : (.disconnect, "Disconnect")
        }
        if app.state == "elsewhere" { return (.claude, app.via == "chatgpt" ? "Manage in ChatGPT" : "Manage in Claude") }
        if app.state == "unavailable" || app.state == "not_set_up" || app.action == "none" { return nil }
        if app.action == "claude" { return (.claude, "Add in Claude") }
        return (.connect, app.needsYou ? "Connect Again" : "Connect")
    }
}

/// The guided walkthrough for Trello, in the style of the voice setup (VoiceSetupView): what happens, Trello's own
/// page in a sheet, and, when Trello shows a code instead of coming back by itself, the steps to paste it.
struct TrelloConnectView: View, ConnectWalkthrough {
    enum Phase: Identifiable, Equatable {
        case intro, paste, checking
        case done(String)
        case failed(String)
        var id: String { "\(self)" }
    }

    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    @State var phase: Phase
    @State private var code = ""
    @State private var problem: String?
    @State private var pageURL: URL?
    private var model: AppsModel { AppsModel.shared }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                switch phase {
                case .intro: intro
                case .paste: paste
                case .checking: checking
                case .done(let name): done(name)
                case .failed(let message): failed(message)
                }
            }
            .frame(maxWidth: 520, alignment: .leading)
            .padding(.horizontal, 28)
            .padding(.vertical, Theme.onMac ? 28 : 24)
            .frame(maxWidth: .infinity)
        }
        .background(Theme.canvas)
        .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
        .sensoryFeedback(.success, trigger: isDone)
    }

    private var isDone: Bool { if case .done = phase { return true } else { return false } }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 0) {
            header("rectangle.split.3x1", "Connect Trello",
                   "Your Zips read your boards and cards, and they ask you before they add, move or change a card.")
            steps("What Happens Next?", [
                "Zero opens Trello's own page. If Trello asks you to sign in, use your Trello account. Zero can't see what you type.",
                "Trello asks whether Zero may use your account. Tap Allow.",
                "If Trello then shows a long code, copy it, close the page, and paste the code here.",
            ])
            buttons {
                primary("Open Trello") { open() }
                quiet("Not Now") { dismiss() }
            }
        }
    }

    private var paste: some View {
        VStack(alignment: .leading, spacing: 0) {
            header("doc.on.clipboard", "Paste Trello's Code",
                   "After you tapped Allow, Trello showed a long code. Paste it here to finish.")
            steps("How Do You Copy It?", [
                "On Trello's page, touch and hold the code, then tap Copy.",
                "Close Trello's page and come back here.",
                "Tap Paste below.",
            ])
            HStack(spacing: 10) {
                TextField("Trello's code", text: $code)
                    .font(.system(size: 14, design: .monospaced))
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .padding(.horizontal, 14).frame(height: 46)
                    .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                PasteButton(payloadType: String.self) { strings in
                    Task { @MainActor in code = strings.first ?? "" }
                }
                .labelStyle(.titleOnly)
                .buttonBorderShape(.capsule)
            }
            .padding(.top, 24)
            if let problem {
                Text(problem).font(Theme.text(14)).foregroundStyle(Theme.danger)
                    .fixedSize(horizontal: false, vertical: true).padding(.top, 10)
            }
            buttons {
                primary("Connect Trello") { send() }
                secondary("Open Trello Again") { open() }
                quiet("Not Now") { dismiss() }
            }
        }
    }

    private var checking: some View {
        VStack(alignment: .leading, spacing: 16) {
            header("rectangle.split.3x1", "Connecting Trello", "Zero is checking with Trello. This takes a moment.")
            ProgressView()
        }
    }

    private func done(_ name: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            header("checkmark", "Trello Is Connected",
                   (name.isEmpty ? "" : "Zero is connected as \(name). ") +
                   "Your Zips can read your boards now. They ask you before they add, move or change a card.")
            buttons { primary("Done") { dismiss() } }
        }
    }

    private func failed(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            header("exclamationmark.triangle", "Trello Didn't Connect", message)
            buttons {
                primary("Try Again") { open() }
                quiet("Not Now") { dismiss() }
            }
        }
    }

    // MARK: Actions

    private func open() {
        problem = nil
        Task { @MainActor in
            if let pageURL {
                _ = await WebSheet().open(pageURL, scheme: "zero")
                phase = .paste
                return
            }
            guard let app = model.catalog?.app("trello") else { phase = .failed("Trello isn't ready for your home yet."); return }
            switch await model.connect(app, state) {
            case .connected:
                phase = .done(model.catalog?.app("trello")?.account ?? "")
            case .closed, .device:
                break
            case .failed(let message):
                phase = .failed(message)
            case .paste(let url):
                pageURL = url
                #if DEBUG
                if state.fixtureMode { phase = .paste; return }
                #endif
                _ = await WebSheet().open(url, scheme: "zero")
                phase = .paste
            }
        }
    }

    private func send() {
        let text = code.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { problem = "Paste Trello's code in the box first."; return }
        phase = .checking
        Task { @MainActor in
            if let message = await model.sendCode("trello", text, state) {
                problem = message
                phase = .paste
            } else {
                code = ""
                phase = .done(model.catalog?.app("trello")?.account ?? "")
            }
        }
    }
}

/// The walkthroughs' pieces, in the voice setup's look (TrelloConnectView, DeviceConnectView).
@MainActor
protocol ConnectWalkthrough {}

extension ConnectWalkthrough {
    func header(_ symbol: String, _ title: String, _ lead: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Image(systemName: symbol)
                .font(.system(size: 24, weight: .semibold))
                .foregroundStyle(ConnectAccountsView.charcoal)
                .frame(width: 52, height: 52)
                .background(symbol == "checkmark" ? ConnectAccountsView.green.opacity(0.9) : ConnectAccountsView.yellow,
                            in: Circle())
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

    func steps(_ title: String, _ lines: [String]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title).font(Theme.text(17, .semibold)).foregroundStyle(Theme.ink).accessibilityAddTraits(.isHeader)
            ForEach(Array(lines.enumerated()), id: \.offset) { i, line in
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text("\(i + 1)").font(Theme.text(13, .bold)).foregroundStyle(ConnectAccountsView.charcoal)
                        .frame(width: 22, height: 22).background(ConnectAccountsView.yellow, in: Circle())
                        .accessibilityHidden(true)
                    Text(line).font(Theme.text(15)).foregroundStyle(Theme.ink.opacity(0.85))
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(.top, 24)
    }

    func buttons<C: View>(@ViewBuilder _ content: () -> C) -> some View {
        let b = content()
        return ViewThatFits(in: .horizontal) {
            HStack(spacing: 10) { b }
            VStack(alignment: .leading, spacing: 10) { b }
        }
        .padding(.top, 28)
    }

    func primary(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(Theme.text(16, .semibold)).foregroundStyle(Theme.actionGlyph)
                .padding(.horizontal, 22).frame(height: 46)
                .background(Theme.action, in: Capsule())
        }
        .buttonStyle(PressStyle())
    }

    func secondary(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink)
                .padding(.horizontal, 22).frame(height: 46)
                .background(Theme.bubble, in: Capsule())
        }
        .buttonStyle(PressStyle())
    }

    func quiet(_ title: String, action: @escaping () -> Void) -> some View {
        Button(title, action: action)
            .buttonStyle(.plain)
            .font(Theme.text(15, .medium))
            .foregroundStyle(Theme.inkSoft)
            .padding(.horizontal, 4).frame(height: 36)
    }
}

/// GitHub's sign-in: GitHub has no page that comes back to Zero on a phone, so Zero shows a short code, the person
/// enters it on GitHub's page, and the hub asks GitHub every few seconds until they allow Zero (hub oauth.py).
struct DeviceConnectView: View, ConnectWalkthrough {
    struct Sheet: Identifiable {
        let app: AppsCatalog.App
        let start: AppStart
        var id: String { app.id }
    }

    let sheet: Sheet
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @State private var connected = false
    @State private var problem: String?
    private var model: AppsModel { AppsModel.shared }
    private var name: String { sheet.app.name }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                if connected {
                    header("checkmark", "\(name) Is Connected", sheet.app.can)
                    buttons { primary("Done") { dismiss() } }
                } else if let problem {
                    header("exclamationmark.triangle", "\(name) Didn't Connect", problem)
                    buttons { quiet("Close") { dismiss() } }
                } else {
                    waiting
                }
            }
            .frame(maxWidth: 520, alignment: .leading)
            .padding(.horizontal, 28)
            .padding(.vertical, Theme.onMac ? 28 : 24)
            .frame(maxWidth: .infinity)
        }
        .background(Theme.canvas)
        .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
        .sensoryFeedback(.success, trigger: connected)
        .task { await poll() }
    }

    private var waiting: some View {
        VStack(alignment: .leading, spacing: 0) {
            header(sheet.app.symbol, "Connect \(name)", sheet.app.can)
            steps("What Happens Next?", [
                "Zero copies the code below and opens \(name)'s page.",
                "Sign in if \(name) asks, paste the code, and tap Continue.",
                "Tap Authorize. Come back here, and Zero finishes by itself.",
            ])
            Text(sheet.start.user_code ?? "")
                .font(.system(size: 30, weight: .semibold, design: .monospaced))
                .foregroundStyle(Theme.ink)
                .textSelection(.enabled)
                .padding(.horizontal, 18).frame(height: 64)
                .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .padding(.top, 24)
                .accessibilityLabel("Code \(sheet.start.user_code ?? "")")
            buttons {
                primary("Copy Code and Open \(name)") { open() }
                quiet("Not Now") { dismiss() }
            }
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text("Waiting for \(name).").font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
            }
            .padding(.top, 20)
        }
    }

    private func open() {
        UIPasteboard.general.string = sheet.start.user_code
        if let url = sheet.start.url.flatMap(URL.init(string:)) { openURL(url) }
    }

    /// Asks the hub until GitHub answers, for as long as the sheet is open.
    private func poll() async {
        var wait = max(2, sheet.start.interval ?? 5)
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(wait))
            if Task.isCancelled { return }
            switch await model.poll(sheet.app.id, sheet.start, state) {
            case .waiting(let seconds): wait = seconds
            case .connected: connected = true; return
            case .failed(let message): problem = message; return
            }
        }
    }
}

/// Settings: Connect Apps with how many are connected.
struct ConnectAppsSection: View {
    @Environment(AppState.self) private var state

    var body: some View {
        let count = AppsModel.shared.catalog?.connected
        Section {
            NavigationLink { ConnectAppsView() } label: {
                LabeledContent {
                    Text(count.map { "\($0) Connected" } ?? "").foregroundStyle(Theme.inkSoft)
                } label: {
                    Label("Connect Apps", systemImage: "square.grid.2x2")
                }
            }
        } footer: {
            Text("Connect Trello, Google Drive and the other apps your Zips use.")
        }
        .task { await AppsModel.shared.refresh(state) }
    }
}
