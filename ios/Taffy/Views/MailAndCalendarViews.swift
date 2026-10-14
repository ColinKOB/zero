import SwiftUI

// MARK: Settings page

struct MailAndCalendarView: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    var modal = false
    private var home: HomeModel { HomeModel.shared }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Image(systemName: "envelope")
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(ConnectAccountsView.charcoal)
                    .frame(width: 52, height: 52)
                    .background(ConnectAccountsView.yellow, in: Circle())
                    .padding(.bottom, 16)
                    .accessibilityHidden(true)
                Text("Email and Calendars")
                    .font(Theme.text(28, .bold)).foregroundStyle(Theme.ink)
                    .accessibilityAddTraits(.isHeader)
                Text("Your Zips read the mail and calendars you add here. They write drafts and ask you before anything is sent or anyone is invited.")
                    .font(Theme.text(16)).foregroundStyle(Theme.ink.opacity(0.85))
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 10)
                MailAccountsPanel(loginHint: nil)
                    .padding(.top, 22)
            }
            .frame(maxWidth: 520, alignment: .leading)
            .padding(.horizontal, 24)
            .padding(.vertical, Theme.onMac ? 28 : 20)
            .frame(maxWidth: .infinity)
        }
        .background(Theme.canvas)
        .navigationTitle("")
        #if !targetEnvironment(macCatalyst)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .toolbar {
            if modal { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .task { await home.load(state) }
    }
}

struct MailAccountsPanel: View {
    @Environment(AppState.self) private var state
    let loginHint: String?
    var plain = false
    @State private var googleBusy = false
    @State private var problem: String?
    @State private var addingMailbox = false
    @State private var addingCalendar = false
    private var home: HomeModel { HomeModel.shared }
    private var settings: HomeSettings? { home.settings }
    private var googleReady: Bool { home.googleClientID != nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let s = settings, s.hasMail || s.hasCalendar {
                AccountsList(plain: plain)
            }
            GoogleSignInButton(busy: googleBusy, enabled: googleReady) { signIn() }
            Text(googleReady
                 ? "For Gmail and Google Calendar. You sign in on Google's own page, so Zero never sees your password."
                 : "Sign In with Google isn't set up for your home yet. You can add your email with another provider below, or ask the person who set up your home.")
                .font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
            if let problem {
                Text(problem).font(Theme.text(15, .medium)).foregroundStyle(Theme.danger)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 18) {
                quiet("Use Another Email Provider") { addingMailbox = true }
                quiet("Add a Calendar") { addingCalendar = true }
            }
            .padding(.top, 2)
        }
        .sheet(isPresented: $addingMailbox) {
            AddMailboxView(loginHint: loginHint).environment(state)
        }
        .sheet(isPresented: $addingCalendar) {
            AddCalendarView(address: settings?.mailboxes.first?.address ?? loginHint ?? "").environment(state)
        }
        .sensoryFeedback(.success, trigger: settings?.googleAccounts.count ?? 0)
        #if DEBUG
        .task {
            let a = CommandLine.arguments
            if let i = a.firstIndex(of: "-sheet"), i + 1 < a.count {
                try? await Task.sleep(for: .milliseconds(500))
                if a[i + 1] == "mailbox" { addingMailbox = true }
                if a[i + 1] == "calendar" { addingCalendar = true }
            }
            if a.contains("-googleBusy") { googleBusy = true }
        }
        #endif
    }

    private func signIn() {
        guard !googleBusy else { return }
        googleBusy = true
        problem = nil
        Task {
            let hint = loginHint.flatMap { $0.lowercased().hasSuffix("@gmail.com") || $0.lowercased().hasSuffix("@googlemail.com") ? $0 : nil }
            problem = await home.connectGoogle(state, loginHint: hint)
            googleBusy = false
        }
    }

    private func quiet(_ title: String, action: @escaping () -> Void) -> some View {
        Button(title, action: action)
            .buttonStyle(.plain)
            .font(Theme.text(15, .semibold))
            .foregroundStyle(Theme.ink)
            .underline()
            .frame(minHeight: 40)
    }
}

struct AccountsList: View {
    @Environment(AppState.self) private var state
    var plain = false
    @State private var removing: Row?
    @State private var problem: String?
    @State private var reconnecting = false
    private var home: HomeModel { HomeModel.shared }

    struct Row: Identifiable, Equatable {
        enum Kind { case google, mailbox, calendar }
        let kind: Kind
        let id: String
        let title: String
        let subtitle: String
        let symbol: String
        let state: String
        let detail: String?
        var key: String { "\(kind)-\(id)" }
    }

    private var rows: [Row] {
        guard let s = home.settings else { return [] }
        let google = s.googleAccounts.map {
            Row(kind: .google, id: $0.id, title: $0.address, subtitle: "Gmail and Google Calendar", symbol: "envelope",
                state: $0.needsSignIn ? "needs_sign_in" : ($0.state ?? "connected"), detail: $0.detail)
        }
        let mail = s.mailboxes.map {
            Row(kind: .mailbox, id: $0.id, title: $0.address, subtitle: Self.providerName($0.provider, mail: true),
                symbol: "tray", state: $0.state ?? "connected", detail: $0.detail)
        }
        let cals = s.otherCalendars.map {
            Row(kind: .calendar, id: $0.id, title: $0.label ?? $0.username ?? "Calendar",
                subtitle: $0.kind == "ical" ? "Calendar link, read only" : Self.providerName($0.provider, mail: false),
                symbol: "calendar", state: $0.state ?? "connected", detail: $0.detail)
        }
        return google + mail + cals
    }

    static func providerName(_ p: String?, mail: Bool) -> String {
        switch p {
        case "icloud": return mail ? "iCloud Mail" : "iCloud Calendar"
        case "gmail", "google": return mail ? "Gmail" : "Google Calendar"
        default: return mail ? "Email" : "Calendar"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(rows.enumerated()), id: \.element.key) { i, row in
                rowView(row)
                if i < rows.count - 1 { Divider().padding(.leading, 58) }
            }
        }
        .background(plain ? Color.white : Theme.raised, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous)
            .stroke(plain ? Color.black.opacity(0.1) : Theme.rule, lineWidth: 1))
        .confirmationDialog(removing.map { "Disconnect \($0.title)?" } ?? "",
                            isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }),
                            titleVisibility: .visible, presenting: removing) { row in
            Button("Disconnect", role: .destructive) { Task { await remove(row) } }
            Button("Cancel", role: .cancel) {}
        } message: { row in
            Text(row.kind == .google
                 ? "Your Zips stop reading this Gmail and Google Calendar. Nothing in your Google account is deleted."
                 : "Your Zips stop reading it. Nothing in the account itself is deleted.")
        }
        if let problem {
            Text(problem).font(Theme.text(14, .medium)).foregroundStyle(Theme.danger).padding(.top, 8)
        }
    }

    private func rowView(_ row: Row) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: row.symbol)
                    .font(.system(size: 17, weight: .medium)).foregroundStyle(Theme.ink)
                    .frame(width: 34, height: 34)
                    .background(plain ? Color(hex: "#F2F2F2") : Theme.bubble, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
                VStack(alignment: .leading, spacing: 4) {
                    Text(row.title).font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink)
                        .lineLimit(1).minimumScaleFactor(0.7)
                    Text(row.subtitle).font(Theme.text(13)).foregroundStyle(Theme.inkSoft)
                    AccountPill(state: row.state).padding(.top, 2)
                }
                Spacer(minLength: 4)
                Menu {
                    Button("Disconnect", role: .destructive) { removing = row }
                } label: {
                    Image(systemName: "ellipsis").font(.system(size: 15, weight: .semibold)).foregroundStyle(Theme.inkSoft)
                        .frame(width: 32, height: 32).contentShape(Rectangle())
                }
                .accessibilityLabel("More for \(row.title)")
            }
            if row.state == "needs_sign_in" || row.state == "trouble" {
                // Google's sign-ins for an app in Testing run out about once a week, so this stays calm and one tap.
                Text(row.kind == .google
                     ? "Google asked you to sign in again. It takes one tap, and your Zips pick up where they left off."
                     : row.detail ?? "This password stopped working. Make a new app-specific password and add the account again.")
                    .font(Theme.text(14)).foregroundStyle(Theme.ink.opacity(0.85))
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, 46)
                if row.kind == .google {
                    GoogleSignInButton(busy: reconnecting) { reconnect(row) }
                        .padding(.leading, 46)
                }
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 11)
    }

    private func reconnect(_ row: Row) {
        reconnecting = true
        Task {
            problem = await home.connectGoogle(state, loginHint: row.title)
            reconnecting = false
        }
    }

    private func remove(_ row: Row) async {
        switch row.kind {
        case .google: problem = await home.disconnectGoogle(row.id, state)
        case .mailbox: problem = await home.removeMailbox(row.id, state)
        case .calendar: problem = await home.removeCalendar(row.id, state)
        }
    }
}

// MARK: Another email provider (app passwords)

struct AddMailboxView: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    let loginHint: String?

    enum Provider: String, CaseIterable, Identifiable {
        case icloud, other
        var id: String { rawValue }
        var title: String { self == .icloud ? "iCloud Mail" : "Other Email" }
    }

    @State private var provider: Provider = .icloud
    @State private var address = ""
    @State private var password = ""
    @State private var imapHost = ""
    @State private var imapPort = "993"
    @State private var smtpHost = ""
    @State private var smtpPort = "587"
    @State private var busy = false
    @State private var result: AccountCheck?
    @State private var problem: String?
    @State private var calendarBusy = false
    @State private var calendarAdded = false
    @FocusState private var focus: Field?

    enum Field { case address, password, imap, smtp }

    private var ready: Bool {
        address.contains("@") && password.count >= 4 && (provider == .icloud || (!imapHost.isEmpty && !smtpHost.isEmpty))
    }
    private var added: Bool { result?.ok == true }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Add a Mailbox").font(Theme.text(28, .bold)).foregroundStyle(Theme.ink)
                        .accessibilityAddTraits(.isHeader)
                    Text("Your Zips sign in to your mailbox with an app password. It's a separate password just for Zero, and you can turn it off any time from your account.")
                        .font(Theme.text(16)).foregroundStyle(Theme.ink.opacity(0.85))
                        .fixedSize(horizontal: false, vertical: true)
                    Picker("Provider", selection: $provider) {
                        ForEach(Provider.allCases) { Text($0.title).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .disabled(added)
                    steps
                    field("Email address", text: $address, field: .address) {
                        $0.textContentType(.username).keyboardType(.emailAddress)
                    }
                    secure
                    if provider == .other {
                        HStack(spacing: 10) {
                            field("Incoming server (IMAP)", text: $imapHost, field: .imap) { $0.keyboardType(.URL) }
                            field("Port", text: $imapPort, field: nil) { $0.keyboardType(.numberPad) }.frame(width: 84)
                        }
                        HStack(spacing: 10) {
                            field("Outgoing server (SMTP)", text: $smtpHost, field: .smtp) { $0.keyboardType(.URL) }
                            field("Port", text: $smtpPort, field: nil) { $0.keyboardType(.numberPad) }.frame(width: 84)
                        }
                        Text("Your email provider's help pages list these. They often look like imap.example.com and smtp.example.com.")
                            .font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    outcome
                }
                .frame(maxWidth: 520, alignment: .leading)
                .padding(.horizontal, 24).padding(.vertical, 20)
                .frame(maxWidth: .infinity)
            }
            .safeAreaInset(edge: .bottom) {
                VStack(spacing: 6) {
                    if added {
                        OnboardingButton("Done") { dismiss() }
                    } else {
                        OnboardingButton(busy ? "Checking" : "Check and Add", busy: busy, enabled: ready) { check() }
                    }
                }
                .padding(.horizontal, 24).padding(.vertical, 10)
                .background(Theme.canvas)
            }
            .background(Theme.canvas)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button(added ? "Close" : "Cancel") { dismiss() } }
            }
        }
        .onAppear {
            if address.isEmpty, let hint = loginHint, hint.contains("@") {
                address = hint
                let domain = hint.lowercased().split(separator: "@").last.map(String.init) ?? ""
                provider = ["icloud.com", "me.com", "mac.com"].contains(domain) ? .icloud : .other
            }
        }
        #if DEBUG
        .task { if CommandLine.arguments.contains("-mailboxFilled") { address = "sam@icloud.com"; password = "abcd-efgh-ijkl-mnop" } }
        #endif
    }

    @ViewBuilder private var steps: some View {
        if provider == .icloud {
            VStack(alignment: .leading, spacing: 10) {
                step(1, "Open your Apple Account page and sign in.")
                step(2, "Choose Sign-In and Security, then App-Specific Passwords, and make one named Zero.")
                step(3, "Paste it below. Your Apple Account password stays yours.")
                Button { openURL(URL(string: "https://account.apple.com/account/manage")!) } label: {
                    Label("Open Apple Account", systemImage: "arrow.up.right.square")
                        .font(Theme.text(15, .semibold)).foregroundStyle(Theme.ink)
                }
                .buttonStyle(.plain).frame(minHeight: 36)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        } else {
            Text("Make an app password in your email account's security settings, then paste it below. If your provider has no app passwords, use the password you sign in with.")
                .font(Theme.text(15)).foregroundStyle(Theme.ink.opacity(0.85))
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var secure: some View {
        SecureField("", text: $password, prompt: Text(provider == .icloud ? "App-specific password" : "App password"))
            .textContentType(.password)
            .focused($focus, equals: .password)
            .font(Theme.text(17))
            .padding(.horizontal, 16).frame(minHeight: 50)
            .background(Theme.field, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .disabled(added)
            .submitLabel(.go)
            .onSubmit { if ready { check() } }
    }

    @ViewBuilder private var outcome: some View {
        if let problem {
            Text(problem).font(Theme.text(15, .medium)).foregroundStyle(Theme.danger)
                .fixedSize(horizontal: false, vertical: true)
        } else if let result {
            Label {
                Text(result.detail ?? (result.ok ? "Connected. Your Zips can read this mailbox now." : "That didn't work. Check the address and password."))
                    .fixedSize(horizontal: false, vertical: true)
            } icon: {
                Image(systemName: result.ok ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
            }
            .font(Theme.text(15, .medium))
            .foregroundStyle(result.ok ? ConnectAccountsView.green : Theme.danger)
            if result.ok && provider == .icloud {
                if calendarAdded {
                    Label("Your iCloud Calendar is added too.", systemImage: "checkmark.circle.fill")
                        .font(Theme.text(15, .medium)).foregroundStyle(ConnectAccountsView.green)
                } else {
                    OnboardingSecondaryButton(calendarBusy ? "Adding" : "Add My iCloud Calendar Too", symbol: "calendar") {
                        addCalendar()
                    }
                    Text("It uses the same app-specific password.")
                        .font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                }
            }
        }
    }

    private func step(_ n: Int, _ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text("\(n)").font(Theme.text(13, .bold)).foregroundStyle(ConnectAccountsView.charcoal)
                .frame(width: 22, height: 22).background(ConnectAccountsView.yellow, in: Circle())
            Text(text).font(Theme.text(15)).foregroundStyle(Theme.ink.opacity(0.85))
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func field<V: View>(_ prompt: String, text: Binding<String>, field: Field?,
                                _ style: (TextField<Text>) -> V) -> some View {
        style(TextField("", text: text, prompt: Text(prompt)))
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .font(Theme.text(17))
            .padding(.horizontal, 16).frame(minHeight: 50)
            .background(Theme.field, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .disabled(added)
    }

    private func check() {
        guard ready, !busy else { return }
        busy = true
        problem = nil
        result = nil
        let a = address.trimmingCharacters(in: .whitespaces)
        let body = HubClient.MailboxBody(
            provider: provider.rawValue, address: a, password: password,
            label: provider == .icloud ? "iCloud" : nil,
            imap: provider == .other ? .init(host: imapHost.trimmingCharacters(in: .whitespaces), port: Int(imapPort) ?? 993) : nil,
            smtp: provider == .other ? .init(host: smtpHost.trimmingCharacters(in: .whitespaces), port: Int(smtpPort) ?? 587) : nil)
        Task {
            let (r, e) = await HomeModel.shared.addMailbox(body, state)
            result = r
            problem = e
            busy = false
        }
    }

    private func addCalendar() {
        guard !calendarBusy else { return }
        calendarBusy = true
        Task {
            let (r, e) = await HomeModel.shared.addCalendar(
                .init(kind: "caldav", provider: "icloud", url: "https://caldav.icloud.com", username: address,
                      password: password, label: "iCloud Calendar"), state)
            calendarAdded = r?.ok == true
            if !calendarAdded { problem = e ?? r?.detail ?? "The calendar didn't connect. Try again in a moment." }
            calendarBusy = false
        }
    }
}

// MARK: A calendar without Google sign-in

struct AddCalendarView: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    let address: String

    enum Kind: String, CaseIterable, Identifiable {
        case icloud, caldav, link
        var id: String { rawValue }
        var title: String {
            switch self {
            case .icloud: return "iCloud"
            case .caldav: return "Other"
            case .link: return "Calendar Link"
            }
        }
    }

    @State private var kind: Kind = .icloud
    @State private var url = ""
    @State private var username = ""
    @State private var password = ""
    @State private var busy = false
    @State private var result: AccountCheck?
    @State private var problem: String?

    private var ready: Bool {
        switch kind {
        case .icloud: return username.contains("@") && password.count >= 4
        case .caldav: return url.count > 8 && !username.isEmpty && password.count >= 4
        case .link: return url.count > 8
        }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Add a Calendar").font(Theme.text(28, .bold)).foregroundStyle(Theme.ink)
                        .accessibilityAddTraits(.isHeader)
                    Text(explain).font(Theme.text(16)).foregroundStyle(Theme.ink.opacity(0.85))
                        .fixedSize(horizontal: false, vertical: true)
                    Picker("Kind", selection: $kind) {
                        ForEach(Kind.allCases) { Text($0.title).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .disabled(result?.ok == true)
                    if kind != .icloud {
                        input(kind == .link ? "Calendar address (starts with https or webcal)" : "Server address", $url)
                            .keyboardType(.URL)
                    }
                    if kind != .link {
                        input(kind == .icloud ? "Apple Account email" : "Username", $username)
                            .textContentType(.username).keyboardType(.emailAddress)
                        SecureField("", text: $password, prompt: Text(kind == .icloud ? "App-specific password" : "Password"))
                            .textContentType(.password)
                            .font(Theme.text(17))
                            .padding(.horizontal, 16).frame(minHeight: 50)
                            .background(Theme.field, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    }
                    if let problem {
                        Text(problem).font(Theme.text(15, .medium)).foregroundStyle(Theme.danger)
                    } else if let result {
                        Label(result.detail ?? (result.ok ? "Connected. Your Zips can see this calendar now." : "That didn't work. Check the details and try again."),
                              systemImage: result.ok ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                            .font(Theme.text(15, .medium))
                            .foregroundStyle(result.ok ? ConnectAccountsView.green : Theme.danger)
                    }
                }
                .frame(maxWidth: 520, alignment: .leading)
                .padding(.horizontal, 24).padding(.vertical, 20)
                .frame(maxWidth: .infinity)
            }
            .safeAreaInset(edge: .bottom) {
                Group {
                    if result?.ok == true {
                        OnboardingButton("Done") { dismiss() }
                    } else {
                        OnboardingButton(busy ? "Checking" : "Check and Add", busy: busy, enabled: ready) { check() }
                    }
                }
                .padding(.horizontal, 24).padding(.vertical, 10)
                .background(Theme.canvas)
            }
            .background(Theme.canvas)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button(result?.ok == true ? "Close" : "Cancel") { dismiss() } }
            }
        }
        .onAppear { if username.isEmpty { username = address } }
    }

    private var explain: String {
        switch kind {
        case .icloud: return "Your iCloud Calendar uses the same kind of app-specific password as iCloud Mail. Make one on your Apple Account page under Sign-In and Security."
        case .caldav: return "Most calendar services give a server address for other apps. Your Zips can see events and add the ones you ask for."
        case .link: return "A calendar link lets your Zips see your events but not change them. In Google Calendar on the web, open the calendar's settings and copy its secret address in iCal format."
        }
    }

    private func input(_ prompt: String, _ text: Binding<String>) -> some View {
        TextField("", text: text, prompt: Text(prompt))
            .textInputAutocapitalization(.never).autocorrectionDisabled()
            .font(Theme.text(17))
            .padding(.horizontal, 16).frame(minHeight: 50)
            .background(Theme.field, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private func check() {
        guard ready, !busy else { return }
        busy = true
        problem = nil
        let body: HubClient.CalendarBody = switch kind {
        case .icloud: .init(kind: "caldav", provider: "icloud", url: "https://caldav.icloud.com",
                            username: username.trimmingCharacters(in: .whitespaces), password: password, label: "iCloud Calendar")
        case .caldav: .init(kind: "caldav", provider: "other", url: url.trimmingCharacters(in: .whitespaces),
                            username: username, password: password, label: "Calendar")
        case .link: .init(kind: "ical", provider: "link", url: url.trimmingCharacters(in: .whitespaces), label: "Calendar Link")
        }
        Task {
            let (r, e) = await HomeModel.shared.addCalendar(body, state)
            result = r
            problem = e
            busy = false
        }
    }
}
