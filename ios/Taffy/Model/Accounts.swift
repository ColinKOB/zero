import AuthenticationServices
import Foundation
import Observation
import SwiftUI

struct AccountsStatus: Decodable, Equatable {
    struct Claude: Decodable, Equatable {
        let state: String
        let account: String
    }

    struct Service: Decodable, Equatable, Identifiable {
        let id: String
        let name: String
        let group: String
        let state: String
        let connected: Bool
        let detail: String
        var via: String? = nil
    }

    struct ChatGPT: Decodable, Equatable {
        let state: String
    }

    struct Count: Decodable, Equatable {
        let connected: Int
        let total: Int
    }

    let checked_at: Double
    let claude: Claude
    let connect_url: String?
    let google_connections_url: String
    let services: [Service]
    let google: Count
    var computer: String? = nil
    var plan: String? = nil
    var chatgpt: ChatGPT? = nil
    var claude_connect_url: String? = nil
    var chatgpt_connect_url: String? = nil

    var chatGPTOnly: Bool { plan == "chatgpt" }
    func planName(for group: AccountGroup) -> String { chatGPTOnly && group.chatGPTWorks ? "ChatGPT" : "Claude" }
    func connectURL(for group: AccountGroup) -> String {
        chatGPTOnly && group.chatGPTWorks ? (chatgpt_connect_url ?? connect_url ?? "https://chatgpt.com/apps")
            : (claude_connect_url ?? connect_url ?? "https://claude.ai/customize/connectors")
    }
    var more: [Service] { services.filter { $0.group == "more" } }

    var claudeComputer: String { computer ?? "your Mac" }

    func services(in group: AccountGroup) -> [Service] { services.filter { group.serviceIDs.contains($0.id) } }
    func service(_ id: String) -> Service? { services.first { $0.id == id } }
    func allConnected(_ group: AccountGroup) -> Bool {
        let s = services(in: group)
        return !s.isEmpty && s.allSatisfy(\.connected)
    }
    var claudeProblem: Bool { !chatGPTOnly && claude.state != "signed_in" && claude.state != "unknown" }
}

struct AccountGroup: Identifiable, Hashable {
    struct Use: Hashable {
        let id: String
        let name: String
        let symbol: String
        let can: String
    }

    let id: String
    let provider: String
    let uses: [Use]
    var serviceIDs: [String] { uses.map(\.id) }
    var title: String { "Connect \(provider)" }
    var chatGPTWorks: Bool { id == "google" }
    var isMore: Bool { id.hasPrefix("more:") }

    static let google = AccountGroup(id: "google", provider: "Google", uses: [
        Use(id: "gmail", name: "Gmail", symbol: "envelope",
            can: "Search and read your email, write drafts for you to look over, and archive or label mail when you ask. Zips don't send email or delete it."),
        Use(id: "calendar", name: "Google Calendar", symbol: "calendar",
            can: "See your events, find open times, and add or move events when you ask. Zips don't delete events or answer invitations."),
        Use(id: "drive", name: "Google Drive", symbol: "folder",
            can: "Find and read your files, Docs and Sheets, and save new files. Zips don't share, delete or change files you already have."),
    ])

    static let trello = AccountGroup(id: "trello", provider: "Trello", uses: [
        Use(id: "trello", name: "Trello", symbol: "rectangle.split.3x1",
            can: "See your boards and move, add and update cards when you ask. Zips don't delete boards or change workspace settings."),
    ])

    static func more(id: String, name: String) -> AccountGroup {
        AccountGroup(id: "more:\(id)", provider: name, uses: [
            Use(id: id, name: name, symbol: "square.stack.3d.up",
                can: "Read and find what you ask for, and make changes you can undo. Before a Zip sends, posts, shares, invites or deletes anything there, it asks you first."),
        ])
    }

    static func of(service: String, name: String? = nil) -> AccountGroup {
        if trello.serviceIDs.contains(service) { return trello }
        if google.serviceIDs.contains(service) { return google }
        return more(id: service, name: name ?? service.capitalized)
    }
}

@MainActor
@Observable
final class AccountsModel {
    static let shared = AccountsModel()

    var status: AccountsStatus?
    var checking = false
    var error: String?
    @ObservationIgnored private var pollers = 0
    @ObservationIgnored private var pollTask: Task<Void, Never>?

    func refresh(_ state: AppState, fresh: Bool = false) async {
        #if DEBUG
        if state.fixtureMode {
            if status == nil { status = .fixture(Self.fixtureVariant) }
            return
        }
        #endif
        guard state.paired else { return }
        checking = true
        defer { checking = false }
        do {
            status = fresh ? try await state.client.checkAccounts() : try await state.client.accounts()
            error = nil
        } catch {
            self.error = HubClient.describe(error)
        }
    }

    func startPolling(_ state: AppState) {
        pollers += 1
        guard pollTask == nil else { return }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh(state, fresh: true)
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    func stopPolling() {
        pollers = max(0, pollers - 1)
        guard pollers == 0 else { return }
        pollTask?.cancel()
        pollTask = nil
    }

    #if DEBUG
    static var fixtureVariant: String {
        let a = CommandLine.arguments
        if let i = a.firstIndex(of: "-accounts"), i + 1 < a.count { return a[i + 1] }
        return "none"
    }

    func simulateConnecting(_ group: AccountGroup) {
        Task {
            for id in group.serviceIDs {
                try? await Task.sleep(for: .seconds(1.5))
                guard let s = status else { return }
                withAnimation(.smooth) { status = s.with(id, state: "connected") }
            }
        }
    }
    #endif
}

#if DEBUG
extension AccountsStatus {
    func with(_ id: String, state: String) -> AccountsStatus {
        let rows = services.map { s -> Service in
            guard s.id == id else { return s }
            let on = state == "connected"
            return Service(id: s.id, name: s.name, group: s.group, state: state, connected: on,
                           detail: on ? "Connected. Your Zips can use it." : s.detail, via: s.via)
        }
        let g = rows.filter { $0.group == "google" }
        return AccountsStatus(checked_at: Date().timeIntervalSince1970, claude: claude, connect_url: connect_url,
                              google_connections_url: google_connections_url, services: rows,
                              google: Count(connected: g.filter(\.connected).count, total: g.count), computer: computer,
                              plan: plan, chatgpt: chatgpt, claude_connect_url: claude_connect_url,
                              chatgpt_connect_url: chatgpt_connect_url)
    }

    static func fixture(_ fullVariant: String) -> AccountsStatus {
        let server = fullVariant.hasSuffix("-server")
        let variant = server ? String(fullVariant.dropLast("-server".count)) : fullVariant
        let gpt = variant == "chatgpt"
        let place = server ? "the computer your Zips use" : "your Mac"
        let names = [("gmail", "Gmail", "google"), ("calendar", "Google Calendar", "google"),
                     ("drive", "Google Drive", "google"), ("trello", "Trello", "other")]
        func state(_ id: String) -> String {
            switch variant {
            case "all": return "connected"
            case "partial": return ["gmail": "connected", "calendar": "needs_sign_in", "trello": "connected"][id] ?? "not_connected"
            case "expired": return id == "gmail" ? "needs_sign_in" : "connected"
            case "signedout": return "claude_signed_out"
            case "chatgpt": return id == "gmail" ? "connected" : id == "trello" ? "needs_claude" : "not_connected"
            default: return id == "trello" ? "connected" : "not_connected"
            }
        }
        func detail(_ st: String, _ provider: String) -> String {
            switch st {
            case "connected": return "Connected. Your Zips can use it."
            case "needs_sign_in": return "\(provider) asked you to sign in again. Reconnect it in Claude."
            case "claude_signed_out": return "Claude needs to be signed in on \(place) first."
            case "needs_claude": return "Your Zips can use \(provider) through a Claude plan only, for now."
            default: return "Not connected yet."
            }
        }
        let rows = names.map { id, name, group in
            let st = state(id)
            return Service(id: id, name: name, group: group, state: st, connected: st == "connected",
                           detail: detail(st, group == "google" ? "Google" : "Trello"),
                           via: gpt && group == "google" ? "chatgpt" : "claude")
        }
        let more: [Service] = gpt || variant == "signedout" ? [] : [
            Service(id: "linear", name: "Linear", group: "more", state: "connected", connected: true,
                    detail: "Connected. Your Zips can use it.", via: "claude"),
            Service(id: "notion", name: "Notion", group: "more", state: "connected", connected: true,
                    detail: "Connected. Your Zips can use it.", via: "claude"),
            Service(id: "slack", name: "Slack", group: "more", state: "needs_sign_in", connected: false,
                    detail: "Slack asked you to sign in again. Reconnect it in Claude.", via: "claude"),
        ]
        let g = rows.filter { $0.group == "google" }
        return AccountsStatus(checked_at: Date().timeIntervalSince1970,
                              claude: Claude(state: variant == "signedout" || gpt ? "signed_out" : "signed_in",
                                             account: variant == "signedout" || gpt ? "" : "pat@example.com"),
                              connect_url: gpt ? "https://chatgpt.com/apps" : "https://claude.ai/customize/connectors",
                              google_connections_url: "https://myaccount.google.com/connections",
                              services: rows + more, google: Count(connected: g.filter(\.connected).count, total: g.count),
                              computer: server ? place : nil, plan: gpt ? "chatgpt" : variant == "signedout" ? "none" : "claude",
                              chatgpt: ChatGPT(state: gpt ? "signed_in" : "missing"),
                              claude_connect_url: "https://claude.ai/customize/connectors",
                              chatgpt_connect_url: "https://chatgpt.com/apps")
    }
}
#endif

// MARK: Connect Apps

struct AppsCatalog: Decodable, Equatable {
    struct App: Decodable, Equatable, Identifiable {
        let id: String
        let name: String
        let category: String
        let symbol: String
        let native: Bool
        let can: String
        let state: String
        let detail: String
        let action: String
        var account: String? = nil
        var via: String? = nil
        var blurb: String? = nil
        var popular: Bool? = nil

        var isConnected: Bool { state == "connected" || state == "elsewhere" }
        var blurbText: String { (blurb?.isEmpty == false ? blurb : AppsCatalog.blurbs[id]) ?? can }
        var isPopular: Bool { popular ?? AppsCatalog.popularIDs.contains(id) }
        var needsYou: Bool { state == "needs_sign_in" }
    }

    struct TrelloSetup: Decodable, Equatable {
        let available: Bool
        let redirect: Bool
    }

    let apps: [App]
    let categories: [String]
    let connected: Int
    var trello: TrelloSetup? = nil

    func app(_ id: String) -> App? { apps.first { $0.id == id } }

    static let blurbs: [String: String] = [
        "gmail": "Read and sort your mail", "google_calendar": "See and add your events",
        "outlook": "Read your mail and calendar", "google_drive": "Search Docs, Sheets and Slides",
        "dropbox": "Search and read your files", "trello": "Read and update your boards",
        "notion": "Search and read your pages", "todoist": "Read and add your tasks",
        "atlassian": "Read Jira issues and Confluence pages", "linear": "Find and read your issues",
        "slack": "Read the channels you can see", "github": "Read repos, issues and pull requests",
        "figma": "Read design files and comments", "onepassword": "Use logins from a shared vault",
    ]
    static let popularIDs: Set<String> = ["gmail", "google_calendar", "google_drive", "outlook", "github", "notion"]
    var connectedApps: [App] { apps.filter(\.isConnected) }
}

struct AppStart: Decodable, Equatable {
    let mode: String
    var url: String? = nil
    var callback_scheme: String? = nil
    var use: [String]? = nil
    var scopes: [String]? = nil
    var state: String? = nil
    var user_code: String? = nil
    var interval: Int? = nil
}

struct AppAnswer: Decodable {
    let ok: Bool
    var detail: String? = nil
    var pending: Bool? = nil
    var interval: Int? = nil
}

extension HubClient {
    func apps() async throws -> AppsCatalog {
        try await send(try request("/api/apps"), as: AppsCatalog.self)
    }

    func startApp(_ id: String) async throws -> AppStart {
        try await send(try request("/api/apps/\(id)/start?relay=1", method: "POST"), as: AppStart.self)
    }

    func sendAppCode(_ id: String, code: String, state: String? = nil) async throws -> AppAnswer {
        struct Body: Encodable { let code: String; let state: String? }
        var r = try request("/api/apps/\(id)/code", method: "POST", body: Body(code: code, state: state))
        r.timeoutInterval = 45
        return try await send(r, as: AppAnswer.self)
    }

    func pollApp(_ id: String, state: String) async throws -> AppAnswer {
        struct Body: Encodable { let state: String }
        var r = try request("/api/apps/\(id)/poll", method: "POST", body: Body(state: state))
        r.timeoutInterval = 45
        return try await send(r, as: AppAnswer.self)
    }

    func disconnectApp(_ id: String) async throws -> AppAnswer {
        try await send(try request("/api/apps/\(id)", method: "DELETE"), as: AppAnswer.self)
    }
}

@MainActor
final class WebSheet: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var session: ASWebAuthenticationSession?

    func open(_ url: URL, scheme: String) async -> URL? {
        let back: URL? = await withCheckedContinuation { cont in
            let s = ASWebAuthenticationSession(url: url, callbackURLScheme: scheme) { url, _ in cont.resume(returning: url) }
            s.presentationContextProvider = self
            s.prefersEphemeralWebBrowserSession = false
            session = s
            if !s.start() { cont.resume(returning: nil) }
        }
        session = nil
        return back
    }

    nonisolated func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        MainActor.assumeIsolated {
            let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
            return scenes.flatMap(\.windows).first(where: \.isKeyWindow) ?? scenes.first?.windows.first ?? ASPresentationAnchor()
        }
    }
}

@MainActor
@Observable
final class AppsModel {
    static let shared = AppsModel()

    var catalog: AppsCatalog?
    var error: String?
    var working: String?

    enum Outcome: Equatable {
        case connected
        case closed
        case paste(URL)
        case device(AppStart)
        case failed(String)
    }

    enum Poll: Equatable {
        case waiting(seconds: Int)
        case connected
        case failed(String)
    }

    func refresh(_ state: AppState) async {
        #if DEBUG
        if state.fixtureMode {
            if catalog == nil { catalog = .fixture(Self.fixtureVariant) }
            return
        }
        #endif
        guard state.paired else { return }
        do {
            catalog = try await state.client.apps()
            error = nil
        } catch {
            self.error = HubClient.describe(error)
        }
    }

    func connect(_ app: AppsCatalog.App, _ state: AppState) async -> Outcome {
        working = app.id
        defer { working = nil }
        #if DEBUG
        if state.fixtureMode {
            try? await Task.sleep(for: .seconds(1))
            if app.id == "trello", let url = URL(string: "https://trello.com/1/authorize") { return .paste(url) }
            if app.id == "github" {
                return .device(AppStart(mode: "device", url: "https://github.com/login/device", state: "fixture",
                                        user_code: "WDJB-MJHT", interval: 2))
            }
            setFixture(app.id, "connected", account: "sam.rivera@gmail.com")
            return .connected
        }
        #endif
        let start: AppStart
        do {
            start = try await state.client.startApp(app.id)
        } catch {
            return .failed(HubClient.describe(error))
        }
        switch start.mode {
        case "google":
            let message = await HomeModel.shared.connectGoogle(state, loginHint: nil,
                                                               scopes: start.scopes ?? GoogleSignIn.scopes,
                                                               use: start.use)
            await refresh(state)
            if let message { return .failed(message) }
            return catalog?.app(app.id)?.state == "connected" ? .connected : .closed
        case "redirect":
            guard let url = start.url.flatMap(URL.init(string:)) else { return .failed("Trello couldn't be opened.") }
            let back = await WebSheet().open(url, scheme: start.callback_scheme ?? "zero")
            await refresh(state)
            guard let back else { return .closed }
            let ok = URLComponents(url: back, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "ok" }?.value
            return ok == "1" ? .connected : .failed("Trello didn't connect. Try again.")
        case "relay":
            // Trello sends the token back in the link's fragment (zero://oauth/trello#token=...), through the relay.
            guard let url = start.url.flatMap(URL.init(string:)) else { return .failed("Trello couldn't be opened.") }
            guard let back = await WebSheet().open(url, scheme: start.callback_scheme ?? "zero") else { return .closed }
            let fragment = URLComponents(url: back, resolvingAgainstBaseURL: false)?.fragment ?? ""
            let token = URLComponents(string: "?" + fragment)?.queryItems?.first { $0.name == "token" }?.value ?? ""
            guard !token.isEmpty else { return .failed("Trello didn't connect. Try again.") }
            do {
                let r = try await state.client.sendAppCode(app.id, code: token)
                await refresh(state)
                return r.ok ? .connected : .failed(r.detail ?? "Trello didn't connect. Try again.")
            } catch {
                return .failed(HubClient.describe(error))
            }
        case "paste":
            guard let url = start.url.flatMap(URL.init(string:)) else { return .failed("Trello couldn't be opened.") }
            return .paste(url)
        case "oauth":
            guard let url = start.url.flatMap(URL.init(string:)) else { return .failed("\(app.name) couldn't be opened.") }
            guard let back = await WebSheet().open(url, scheme: start.callback_scheme ?? "zero") else { return .closed }
            let items = URLComponents(url: back, resolvingAgainstBaseURL: false)?.queryItems ?? []
            guard let code = items.first(where: { $0.name == "code" })?.value,
                  let returned = items.first(where: { $0.name == "state" })?.value else {
                return .failed("\(app.name) didn't connect. Try again.")
            }
            do {
                let r = try await state.client.sendAppCode(app.id, code: code, state: returned)
                await refresh(state)
                return r.ok ? .connected : .failed(r.detail ?? "\(app.name) didn't connect. Try again.")
            } catch {
                return .failed(HubClient.describe(error))
            }
        case "device":
            guard start.user_code != nil, start.state != nil else { return .failed("\(app.name) couldn't be opened.") }
            return .device(start)
        default:
            return .failed("This app can't be connected here yet.")
        }
    }

    func sendCode(_ appID: String, _ code: String, _ state: AppState) async -> String? {
        working = appID
        defer { working = nil }
        #if DEBUG
        if state.fixtureMode {
            try? await Task.sleep(for: .seconds(1))
            if code.count < 20 { return "That doesn't look like the code Trello shows. Copy the whole code and paste it again." }
            setFixture(appID, "connected", account: "Sam Rivera")
            return nil
        }
        #endif
        do {
            let r = try await state.client.sendAppCode(appID, code: code)
            await refresh(state)
            return r.ok ? nil : (r.detail ?? "Trello didn't connect. Try again.")
        } catch {
            return HubClient.describe(error)
        }
    }

    func poll(_ appID: String, _ start: AppStart, _ state: AppState) async -> Poll {
        #if DEBUG
        if state.fixtureMode {
            fixturePolls += 1
            guard fixturePolls >= 3 else { return .waiting(seconds: 2) }
            fixturePolls = 0
            setFixture(appID, "connected", account: "samrivera")
            return .connected
        }
        #endif
        do {
            let r = try await state.client.pollApp(appID, state: start.state ?? "")
            if r.ok {
                await refresh(state)
                return .connected
            }
            if r.pending == true { return .waiting(seconds: max(5, r.interval ?? 5)) }
            return .failed(r.detail ?? "GitHub didn't connect. Tap Connect and try again.")
        } catch {
            return .waiting(seconds: 10)
        }
    }

    func disconnect(_ appID: String, _ state: AppState) async -> String? {
        working = appID
        defer { working = nil }
        #if DEBUG
        if state.fixtureMode { setFixture(appID, "not_connected", account: nil); return nil }
        #endif
        do {
            let r = try await state.client.disconnectApp(appID)
            await refresh(state)
            return r.ok ? nil : r.detail
        } catch {
            return HubClient.describe(error)
        }
    }

    #if DEBUG
    @ObservationIgnored private var fixturePolls = 0

    static var fixtureVariant: String {
        let a = CommandLine.arguments
        if let i = a.firstIndex(of: "-apps"), i + 1 < a.count { return a[i + 1] }
        return "some"
    }

    private func setFixture(_ id: String, _ st: String, account: String?) {
        guard let c = catalog else { return }
        let apps = c.apps.map { a -> AppsCatalog.App in
            guard a.id == id else { return a }
            let on = st == "connected"
            return AppsCatalog.App(id: a.id, name: a.name, category: a.category, symbol: a.symbol, native: a.native,
                                   can: a.can, state: st, detail: on ? "Connected\(account.map { " as \($0)" } ?? "")." : "",
                                   action: on ? "disconnect" : (a.id.hasPrefix("google") || a.id == "gmail" ? "google" : "connect"),
                                   account: on ? account : nil, via: on ? "zero" : nil)
        }
        withAnimation(.smooth) {
            catalog = AppsCatalog(apps: apps, categories: c.categories, connected: apps.filter(\.isConnected).count,
                                  trello: c.trello)
        }
    }
    #endif
}

#if DEBUG
extension AppsCatalog {
    static func fixture(_ variant: String) -> AppsCatalog {
        typealias A = AppsCatalog.App
        func row(_ id: String, _ name: String, _ cat: String, _ symbol: String, _ native: Bool, _ can: String,
                 _ st: String, _ action: String, _ detail: String = "", _ account: String? = nil) -> A {
            A(id: id, name: name, category: cat, symbol: symbol, native: native, can: can, state: st, detail: detail,
              action: action, account: account, via: st == "connected" ? "zero" : st == "elsewhere" ? "claude" : nil)
        }
        let some = variant != "none"
        let expired = variant == "expired"
        let apps = [
            row("gmail", "Gmail", "Email and Calendar", "envelope", true,
                "Your Zips read and sort your mail, write drafts, and ask you before anything is sent.",
                expired ? "needs_sign_in" : some ? "connected" : "not_connected", some ? "disconnect" : "google",
                expired ? "Google asked you to sign in again." : some ? "Connected with sam.rivera@gmail.com." : "",
                some ? "sam.rivera@gmail.com" : nil),
            row("google_calendar", "Google Calendar", "Email and Calendar", "calendar", true,
                "Your Zips read your calendar and add your own events. They ask you before anyone is invited.",
                some ? "connected" : "not_connected", some ? "disconnect" : "google",
                some ? "Connected with sam.rivera@gmail.com." : "", some ? "sam.rivera@gmail.com" : nil),
            row("outlook", "Outlook", "Email and Calendar", "envelope.badge", true,
                "Your Zips read your Outlook mail and calendar. They don't send mail or change events.",
                "not_connected", "connect"),
            row("google_drive", "Google Drive", "Files", "folder", true,
                "Your Zips search your Drive and read Docs, Sheets and Slides. They don't change or share files.",
                "not_connected", "google"),
            row("dropbox", "Dropbox", "Files", "shippingbox", true,
                "Your Zips search your Dropbox and read text files. They don't change or share files.",
                "not_connected", "connect"),
            row("trello", "Trello", "Productivity", "rectangle.split.3x1", true,
                "Your Zips read your boards and cards. They ask you before they add, move or change a card.",
                "not_connected", "connect"),
            row("notion", "Notion", "Productivity", "doc.text", true,
                "Your Zips search and read the Notion pages you share with Zero. They don't change pages.",
                "not_connected", "connect", some ? "Also connected through your Claude account." : ""),
            row("todoist", "Todoist", "Productivity", "checklist", false, "Read and add your tasks.", "not_connected", "claude",
                "For now, connect it in your Claude or ChatGPT account, and your Zips can use it from there."),
            row("atlassian", "Jira and Confluence", "Productivity", "square.stack.3d.up", false,
                "Read issues and pages from your Atlassian sites.", "not_connected", "claude",
                "For now, connect it in your Claude or ChatGPT account, and your Zips can use it from there."),
            row("linear", "Linear", "Productivity", "line.3.horizontal.decrease", true,
                "Your Zips find and read your Linear issues. They don't change them.", "not_connected", "connect"),
            row("slack", "Slack", "Communication", "number", false, "Read channels and messages you can see.",
                some ? "elsewhere" : "not_connected", some ? "none" : "claude",
                some ? "Connected through your Claude account." : "For now, connect it in your Claude or ChatGPT account, and your Zips can use it from there."),
            row("github", "GitHub", "Code", "chevron.left.forwardslash.chevron.right", true,
                "Your Zips read your repositories, issues and pull requests. They don't change anything.",
                some ? "connected" : "not_connected", some ? "disconnect" : "connect",
                some ? "Connected as samrivera." : "", some ? "samrivera" : nil),
            row("figma", "Figma", "Design", "paintpalette", false, "Read your design files and comments.", "not_connected",
                "claude", "For now, connect it in your Claude or ChatGPT account, and your Zips can use it from there."),
            row("onepassword", "1Password", "Password Managers", "key", false,
                "Use the logins you keep in a vault you share with Zero.", "not_connected", "claude",
                "For now, connect it in your Claude or ChatGPT account, and your Zips can use it from there."),
        ]
        return AppsCatalog(apps: apps, categories: ["Email and Calendar", "Files", "Productivity", "Communication", "Code",
                                                    "Design", "Password Managers"],
                           connected: apps.filter(\.isConnected).count, trello: .init(available: true, redirect: false))
    }
}
#endif
