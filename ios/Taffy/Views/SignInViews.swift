import SwiftUI

/// Errors on the sign-in and computer screens: one plain sentence for Colin, and the same sentence with what failed
/// underneath in the hub's log, so the next one can be traced (Oct 9: "it errored" on the Lens Shop card left
/// no trace anywhere).
enum ScreenTrouble {
    static func sentence(_ error: Error, name: String) -> String {
        if let e = error as? HubClient.HubError {
            if case .http(_, let msg) = e, !msg.isEmpty { return msg }   // the hub's own sentence
            if case .notPaired = e { return e.localizedDescription }
            return "\(name.possessive) computer had a problem. Try again in a moment."
        }
        if let u = error as? URLError {
            switch u.code {
            case .timedOut:
                return "\(name.possessive) computer is taking too long to answer. Try again in a moment."
            case .notConnectedToInternet:
                return "Your \(Theme.device) is offline. Try again once it is back online."
            case .networkConnectionLost, .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed:
                return "\(name) cannot be reached right now. Try again in a moment."
            default: break
            }
        }
        return "Something went wrong with \(name.possessive) computer. Try again in a moment."
    }

    /// What failed underneath, without addresses: an HTTP status or a network error code.
    static func detail(_ error: Error) -> String {
        if let e = error as? HubClient.HubError { return e.code == 401 ? "not paired" : "HTTP \(e.code)" }
        if let u = error as? URLError { return "URLError \(u.code.rawValue)" }
        return String(describing: type(of: error))
    }

    /// Shows nothing itself: returns the sentence to show and logs it on the hub.
    @MainActor static func report(_ error: Error, screen: String, bot: String, state: AppState) -> String {
        let name = state.bot(bot)?.name ?? "The Zip"
        let text = sentence(error, name: name)
        if !state.fixtureMode { state.client.reportError(screen: screen, bot: bot, message: text, detail: detail(error)) }
        return text
    }

    /// A problem with no error behind it (the viewer dropped), logged the same way.
    @MainActor static func report(_ text: String, detail: String, screen: String, bot: String, state: AppState) -> String {
        if !state.fixtureMode { state.client.reportError(screen: screen, bot: bot, message: text, detail: detail) }
        return text
    }
}

/// Save a login into one or more bots' vaults. iPhone password AutoFill fills the fields, the
/// password goes straight into the vault on alien, and the bots only ever see the username.
struct SaveLoginSheet: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss

    var siteURL: String = ""
    var botID: String = "default"
    var taskID: String? = nil
    /// A chat's sign-in card this answers (the bot carries on once it's saved).
    var requestID: Int? = nil

    @State private var url = ""
    @State private var username = ""
    @State private var password = ""
    @State private var otp = ""
    @State private var bots: Set<String> = []
    @State private var busy = false
    @State private var error: String?
    @State private var userField = LoginFieldReader()
    @State private var passField = LoginFieldReader()
    @FocusState private var field: Field?
    private enum Field { case website }

    /// What still stops Save, as a short line by the fields (nil when it can save).
    private var missing: String? {
        if clean(url).isEmpty { return "Add the website." }
        if clean(username).isEmpty && clean(userField.text).isEmpty { return "Add your email or username." }
        if password.isEmpty && passField.text.isEmpty { return "Add the password." }
        if bots.isEmpty { return "Pick at least one Zip." }
        return nil
    }

    private var siteName: String {
        let u = url.contains("://") ? url : "https://" + url
        return URL(string: u)?.host()?.replacingOccurrences(of: "www.", with: "") ?? "this site"
    }

    var body: some View {
        NavigationStack {
            Form {
                if let error {
                    Section {
                        Label(error, systemImage: "exclamationmark.circle.fill")
                            .foregroundStyle(Theme.danger)
                    }
                }
                Section {
                    TextField("Website", text: $url)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .textContentType(.URL)
                        .focused($field, equals: .website)
                    LoginField(placeholder: "Email or Username", text: $username, contentType: .username,
                               reader: userField, focusOnAppear: !siteURL.isEmpty,
                               onReturn: { passField.field?.becomeFirstResponder() })
                    LoginField(placeholder: "Password", text: $password, secure: true, contentType: .password,
                               reader: passField)
                } footer: {
                    VStack(alignment: .leading, spacing: 6) {
                        if let missing {
                            Text(missing).fontWeight(.medium).foregroundStyle(Theme.taffyDeep)
                        }
                        Text(Theme.onMac
                         ? "To use a password saved on this Mac, click the password field and pick your \(siteName) login from the list that appears. The password goes into the Zip's locked vault. The Zip can sign in with it but never sees it."
                         : "To use a password saved on this iPhone, tap the key above the keyboard, then pick your \(siteName) login. The password goes into the Zip's locked vault. The Zip can sign in with it but never sees it.")
                    }
                }
                Section {
                    SecureField("2FA Setup Key (Optional)", text: $otp)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                } footer: {
                    Text("If the site uses an authenticator app, paste its setup key and the Zip can enter codes by itself.")
                }
                Section("Who Can Use It") {
                    ForEach(state.bots) { b in
                        Button {
                            if bots.contains(b.id) { bots.remove(b.id) } else { bots.insert(b.id) }
                        } label: {
                            HStack(spacing: 12) {
                                BotAvatar(bot: b, size: 28)
                                Text(b.name).foregroundStyle(Theme.ink)
                                Spacer()
                                Image(systemName: bots.contains(b.id) ? "checkmark.circle.fill" : "circle")
                                    .font(.system(size: 20))
                                    .foregroundStyle(bots.contains(b.id) ? Theme.ink : Theme.inkSoft.opacity(0.6))
                            }
                        }
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background(Theme.canvas)
            .safeAreaInset(edge: .top, spacing: 0) {
                SheetBar(title: "Save Login", onCancel: { dismiss() }, confirm: busy ? "Saving" : "Save",
                         confirmEnabled: !busy && missing == nil, onConfirm: { Task { await save() } })
            }
            .toolbar(.hidden, for: .navigationBar)
            .onAppear {
                url = siteURL
                bots = [botID]
            }
            .task {
                // Start in the username field so the keyboard and its Passwords key are already up.
                try? await Task.sleep(for: .milliseconds(500))
                if siteURL.isEmpty { field = .website }   // otherwise the username field focuses itself
                #if DEBUG
                // -fillLogin: writes into the fields the way a silent AutoFill does (no edit events), to check Save
                // still turns on.
                if CommandLine.arguments.contains("-fillLogin") {
                    url = "elearning.lakeview.test"
                    try? await Task.sleep(for: .seconds(1))
                    userField.field?.text = "sam@example.com"
                    passField.field?.text = "not-a-real-password"
                }
                #endif
            }
        }
    }

    private func clean(_ s: String) -> String { s.trimmingCharacters(in: .whitespacesAndNewlines) }

    private func save() async {
        error = nil
        // What is really in the fields, in case AutoFill filled them without telling SwiftUI.
        if !userField.text.isEmpty { username = userField.text }
        if !passField.text.isEmpty { password = passField.text }
        // Every failure is shown here and logged on the hub, including the ones the phone catches itself (Oct 9: a
        // failed Save Login left no trace anywhere).
        func refuse(_ text: String, _ why: String) {
            error = ScreenTrouble.report(text, detail: "checked on the \(Theme.device): \(why)", screen: "save_login",
                                         bot: botID, state: state)
        }
        if let missing { refuse(missing, "a field is empty"); return }
        let site = clean(url)
        let normalized = site.contains("://") ? site : "https://" + site
        guard let parsed = URL(string: normalized), let host = parsed.host(), host.contains(".") else {
            refuse("That website does not look right. Try something like canvas.school.edu.", "website not understood")
            return
        }
        guard parsed.scheme == "https" else {
            refuse("Passwords are only saved for secure sites, so the address has to start with https://.", "not https")
            return
        }
        let username = clean(username)
        busy = true
        defer { busy = false }
        let type = username.contains("@") ? "email" : (username.allSatisfy { $0.isNumber || "+-() ".contains($0) } ? "phone" : "username")
        do {
            let warning = try await state.client.saveLogin(bots: Array(bots), label: host, url: normalized,
                                                           identifier: username, identifierType: type, password: password,
                                                           otp: clean(otp).isEmpty ? nil : clean(otp), taskID: taskID,
                                                           requestID: requestID)
            password = ""
            otp = ""
            if let warning, !warning.isEmpty { state.confirmation = Confirmation(title: "Login Saved", text: warning) }
            await state.refreshBoard()
            dismiss()
        } catch {
            self.error = ScreenTrouble.report(error, screen: "save_login", bot: botID, state: state)
        }
    }
}

/// The bot's computer opened straight to a site's sign-in page, with Colin already in control.
struct SignInScreen: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    let botID: String
    let site: URL
    var taskID: String? = nil
    var requestID: Int? = nil

    @State private var viewer: URL?
    @State private var error: String?
    @State private var busy = false

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if let viewer {
                ScreenWeb(url: viewer, onState: { if $0 == "disconnected" { Task { await reconnect() } } })
                    .ignoresSafeArea(edges: .bottom)
            } else if let error {
                VStack(spacing: 18) {
                    Text(error).font(Theme.headline).foregroundStyle(.white).multilineTextAlignment(.center)
                    Button { Task { self.error = nil; await open() } } label: {
                        Text("Try Again").font(Theme.text(17, .medium)).foregroundStyle(.black)
                            .padding(.horizontal, 24).frame(minHeight: 48).background(.white, in: Capsule())
                    }
                    .buttonStyle(PressStyle())
                }
                .padding(30)
            } else {
                VStack(spacing: 14) {
                    BotAvatar(bot: state.bot(botID), size: 56, working: true)
                    Text("Opening \(site.host() ?? "the site") on \((state.bot(botID)?.name ?? "the Zip").possessive) computer")
                        .font(Theme.secondary).foregroundStyle(.white.opacity(0.7)).multilineTextAlignment(.center)
                }
                .padding(30)
            }
        }
        .safeAreaInset(edge: .top) {
            HStack(spacing: 10) {
                CircleButton(dark: true, action: { Task { await finish(signedIn: false) } }) {
                    Image(systemName: "xmark")
                }
                .accessibilityLabel("Cancel")
                NamePill(bot: state.bot(botID), dark: true)
                Spacer()
            }
            .padding(.horizontal, 20).padding(.top, 4).padding(.bottom, 8)
        }
        .safeAreaInset(edge: .bottom) {
            Button { Task { await finish(signedIn: true) } } label: {
                Text(busy ? "Handing Back" : "I'm Signed In")
                    .font(Theme.text(17, .semibold)).foregroundStyle(.black)
                    .frame(maxWidth: .infinity, minHeight: 52)
                    .background(.white, in: Capsule())
            }
            .buttonStyle(PressStyle())
            .disabled(busy || viewer == nil)
            .opacity(viewer == nil ? 0.5 : 1)
            .padding(.horizontal, 20).padding(.bottom, 10)
        }
        .task { await open() }
        .onDisappear {
            // Swiped away or closed some other way: still hand the computer back.
            if !finished {
                let client = state.client, id = botID, host = site.host(), task = taskID, req = requestID
                Task { try? await client.finishSignIn(bot: id, taskID: task, site: host, declined: true, requestID: req) }
            }
        }
    }

    @State private var finished = false
    @State private var reconnects = 0

    private func open() async {
        do {
            let path = try await state.client.startSignIn(bot: botID, url: site.absoluteString)
            viewer = URL(string: state.client.server + path)
        } catch { self.error = ScreenTrouble.report(error, screen: "sign_in_screen", bot: botID, state: state) }
    }

    /// The viewer dropped (phone locked, network blip): fetch a fresh viewer a few times.
    private func reconnect() async {
        reconnects += 1
        guard reconnects <= 3 else {
            viewer = nil
            error = ScreenTrouble.report("The connection to \((state.bot(botID)?.name ?? "the Zip").possessive) computer dropped.",
                                         detail: "viewer disconnected 3 times", screen: "sign_in_screen", bot: botID,
                                         state: state)
            return
        }
        try? await Task.sleep(for: .seconds(Double(reconnects)))
        viewer = nil
        await open()
    }

    /// Always hands control back, so the bot is never left locked out.
    private func finish(signedIn: Bool) async {
        busy = true
        defer { busy = false }
        do {
            try await state.client.finishSignIn(bot: botID, taskID: taskID, site: site.host(), declined: !signedIn,
                                                requestID: requestID)
        } catch {
            if signedIn {
                let said = ScreenTrouble.report(error, screen: "sign_in_screen", bot: botID, state: state)
                self.error = "\(state.bot(botID)?.name ?? "The Zip") has not heard that you signed in yet. \(said)"
                return
            }
        }
        finished = true
        await state.refreshBoard()
        dismiss()
    }
}

/// Every saved login, which bots can use it, and a way to remove it.
struct LoginsView: View {
    @Environment(AppState.self) private var state
    @State private var removing: HubClient.SavedLogin?
    @State private var loadError: String?
    @State private var logins: [HubClient.SavedLogin] = []
    @State private var adding = false

    var body: some View {
        List {
            if logins.isEmpty {
                Text("You have no saved logins yet. When a Zip needs one, its card offers Save Login.")
                    .foregroundStyle(Theme.inkSoft)
            }
            ForEach(logins) { l in
                HStack(spacing: 12) {
                    BotAvatar(bot: state.bot(l.bot), size: 28)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(l.label).font(Theme.text(16, .medium))
                        Text([l.identifier, l.has_otp == true ? "2FA automatic" : nil].compactMap { $0 }.joined(separator: "  ·  "))
                            .font(Theme.text(13)).foregroundStyle(Theme.inkSoft)
                    }
                }
                .swipeActions {
                    Button("Remove", role: .destructive) { removing = l }
                }
            }
        }
        .navigationTitle("Passwords & Logins")
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button { adding = true } label: { Image(systemName: "plus") } } }
        .sheet(isPresented: $adding, onDismiss: { Task { await load() } }) { SaveLoginSheet().environment(state) }
        .confirmationDialog(removing.map { "Remove the \($0.label) login from \(state.bot($0.bot)?.name ?? "this Zip")?" } ?? "",
                            isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }),
                            titleVisibility: .visible) {
            Button("Remove Login", role: .destructive) {
                guard let l = removing else { return }
                Task {
                    do { try await state.client.deleteLogin(bot: l.bot, id: l.id) }
                    catch { loadError = "That login couldn't be removed. Try again." }
                    await load()
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The Zip will need you to sign in again on that site.")
        }
        .overlay {
            if let loadError, logins.isEmpty {
                ContentUnavailableView {
                    Label("Couldn't Load Logins", systemImage: "wifi.slash")
                } description: { Text(loadError) } actions: {
                    Button("Try Again") { Task { await load() } }
                }
            }
        }
        .task { await load() }
    }

    /// An empty list is shown only after a successful load, never because the hub couldn't be reached.
    private func load() async {
        do { logins = try await state.client.logins(); loadError = nil }
        catch { loadError = "Zero couldn't be reached. Your saved logins are safe." }
    }
}

/// When each bot may sign in to each site, and every recent sign-in.
struct SignInPermissionsView: View {
    @Environment(AppState.self) private var state
    @State private var permissions: [SignInPermission] = []
    @State private var history: [SignInRecord] = []
    @State private var actions: [ActionRecord] = []
    @State private var loaded = false
    @State private var error: String?

    var body: some View {
        List {
            Section {
                if loaded && permissions.isEmpty {
                    Text("Sites show up here once a Zip has a saved login for them or you signed it in yourself.")
                        .font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                }
                ForEach($permissions) { $p in
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(spacing: 12) {
                            BotAvatar(bot: state.bot(p.bot_id), size: 28)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(p.site).font(Theme.body).foregroundStyle(Theme.ink).lineLimit(1)
                                Text(state.bot(p.bot_id)?.name ?? p.bot_id).font(Theme.caption).foregroundStyle(Theme.inkSoft)
                            }
                        }
                        Picker("When \(state.bot(p.bot_id)?.name ?? "the Zip") May Sign In", selection: $p.mode) {
                            ForEach(SignInPermission.modes, id: \.id) { Text($0.title).tag($0.id) }
                        }
                        .pickerStyle(.segmented)
                        .onChange(of: p.mode) { _, mode in save(p, mode) }
                    }
                    .padding(.vertical, 4)
                }
            } header: { Text("Sites") } footer: {
                Text("When I Ask lets a Zip sign in only when something you asked for needs that site. Ask Me First "
                     + "shows a card every time. Anytime lets it sign in during scheduled work too. Sites that involve "
                     + "money, health or account settings start on Ask Me First.")
            }
            if let error {
                Text(error).foregroundStyle(Theme.danger)
            }
            Section {
                // The Always Allow rules live on Custom Rules with the Ask First rules, so each is listed once.
                NavigationLink { CustomRulesView() } label: {
                    Label("Custom Rules", systemImage: "checklist")
                }
            } footer: {
                Text("Custom Rules lists what you allowed from cards and what you asked your Zips to ask you about first.")
            }
            Section("What Your Zips Did") {
                if loaded && actions.isEmpty {
                    Text("Changes your Zips make for you show up here.").font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                }
                ForEach(actions) { a in
                    HStack(alignment: .top, spacing: 12) {
                        BotAvatar(bot: state.bot(a.bot_id), size: 24)
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(a.summary.prefix(1).uppercased() + a.summary.dropFirst())
                                    .font(Theme.secondary).foregroundStyle(Theme.ink).lineLimit(3)
                                Spacer()
                                Text(a.date.listStamp).font(Theme.caption).foregroundStyle(Theme.inkSoft)
                            }
                            Text(Self.how(a)).font(Theme.caption).foregroundStyle(Theme.inkSoft)
                        }
                    }
                }
            }
            Section("Recent Sign-Ins") {
                if loaded && history.isEmpty {
                    Text("There are no sign-ins yet.").font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                }
                ForEach(history) { h in
                    HStack(alignment: .top, spacing: 12) {
                        BotAvatar(bot: state.bot(h.bot_id), size: 24)
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(h.site).font(Theme.secondary.weight(.medium)).foregroundStyle(Theme.ink)
                                Spacer()
                                Text(h.date.listStamp).font(Theme.caption).foregroundStyle(Theme.inkSoft)
                            }
                            Text(Self.because(h)).font(Theme.caption).foregroundStyle(Theme.inkSoft).lineLimit(2)
                        }
                    }
                }
            }
        }
        .scrollContentBackground(.hidden)
        .background(Theme.canvas)
        .navigationTitle("Permissions")
        .refreshable { await load() }
        .task { await load() }
    }

    #if DEBUG
    static var fixture: HubClient.PermissionsPage {
        let t = Date().timeIntervalSince1970
        return HubClient.PermissionsPage(
            permissions: [SignInPermission(bot_id: "school", site: "elearning.lakeview.test", mode: "when_asked", sensitive: false),
                          SignInPermission(bot_id: "money", site: "chase.com", mode: "ask", sensitive: true)],
            signins: [SignInRecord(id: 1, bot_id: "school", site: "elearning.lakeview.test", reason: "What is due this week?",
                                   how: "asked", at: t - 3600)],
            actions: [ActionRecord(id: 1, bot_id: "school", kind: "write", summary: "Update your Trello board (Move the Engel summary card to Done)",
                                   how: "tell", at: t - 1800),
                      ActionRecord(id: 2, bot_id: "clients", kind: "send", summary: "Email ken@brightpath.test about \"Repo access\"",
                                   how: "button", at: t - 7200)])
    }
    #endif

    static func how(_ a: ActionRecord) -> String {
        switch a.how {
        case "button": return "You allowed it."
        case "rule": return "A rule you set covers it."
        case "review": return "You approved it."
        default: return "Done and mentioned."
        }
    }

    static func because(_ h: SignInRecord) -> String {
        switch h.how {
        case "asked": return "You asked: \(h.reason)"
        case "button": return "You allowed it."
        case "anytime": return "You allowed it anytime."
        default: return h.reason
        }
    }

    private func load() async {
        #if DEBUG
        if state.fixtureMode {   // sample rules and activity, so the screen can be checked without a hub (L5)
            let page = Self.fixture
            permissions = page.permissions
            history = page.signins
            actions = page.actions ?? []
            loaded = true
            return
        }
        #endif
        do {
            let r = try await state.client.permissions()
            permissions = r.permissions.sorted { ($0.site, $0.bot_id) < ($1.site, $1.bot_id) }
            history = r.signins
            actions = r.actions ?? []
            error = nil
        } catch { self.error = HubClient.describe(error) }
        loaded = true
    }

    private func save(_ p: SignInPermission, _ mode: String) {
        if state.fixtureMode { return }
        Task {
            do {
                try await state.client.setPermission(bot: p.bot_id, site: p.site, mode: mode)
                error = nil
            } catch {
                self.error = HubClient.describe(error)
                await load()
            }
        }
    }
}

/// Every phone or test device paired with Taffy. Removing one signs it out right away.
struct DevicesView: View {
    @Environment(AppState.self) private var state
    @State private var devices: [HubClient.Device] = []
    @State private var error: String?

    var body: some View {
        List {
            Section {
                ForEach(devices) { d in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(d.name).font(Theme.body)
                            Text(["Paired \(Date(timeIntervalSince1970: d.created_at).listStamp)",
                                  d.last_used_at.map { "last used \(Date(timeIntervalSince1970: $0).listStamp)" },
                                  d.mac_link == true ? "the Mac Zips can use" : nil]
                                    .compactMap { $0 }.joined(separator: " · "))
                                .font(Theme.caption).foregroundStyle(Theme.inkSoft)
                        }
                        Spacer()
                        if d.current { Text("This \(Theme.device)").font(Theme.caption).foregroundStyle(Theme.inkSoft) }
                    }
                    .swipeActions {
                        if !d.current {
                            Button("Remove", role: .destructive) { Task { await remove(d) } }
                        }
                    }
                }
            } footer: {
                Text("Any device listed here can talk to your Zips. Swipe left to remove one you do not recognize.")
            }
            if let error { Text(error).foregroundStyle(Theme.danger) }
        }
        .scrollContentBackground(.hidden)
        .background(Theme.canvas)
        .navigationTitle("Paired Devices")
        .refreshable { await load() }
        .task { await load() }
    }

    private func load() async {
        do { devices = try await state.client.devices(); error = nil } catch { self.error = HubClient.describe(error) }
    }

    private func remove(_ d: HubClient.Device) async {
        do { try await state.client.removeDevice(d.id); await load() } catch { self.error = HubClient.describe(error) }
    }
}

/// A bot in a chat asking Colin to sign in, the moment it found it needed to: the Computer card, with a picture of
/// the Zip's screen, Take Over, I'm Done and Save Login, and an X to close it. Once he's done, the bot carries on in
/// this chat.
struct SignInAskCard: View {
    @Environment(AppState.self) private var state
    let message: ChatMessage
    let botID: String
    var gap: CGFloat = 48

    var body: some View {
        if let site = message.details.first.flatMap(URL.init(string:)), let id = message.feedID {
            ComputerCard(botID: message.botID ?? botID,
                         signIn: SignInPrompt(site: site, requestID: id, why: message.text,
                                              waiting: state.cardIsLive(message, bot: message.botID ?? botID)),
                         gap: gap)
        }
    }
}
