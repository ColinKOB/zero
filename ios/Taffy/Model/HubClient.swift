import Foundation
import UIKit

/// Talks to the person's own computer (the hub), at the address saved when they connected. There is no built-in
/// address: the app finds a home only through onboarding or the pairing screen, and an install from before keeps
/// the address it stored then (UserDefaults "server").
final class HubClient {
    /// The address saved at pairing, or "" when this device is not connected to a home yet.
    static var savedServer: String { UserDefaults.standard.string(forKey: "server") ?? "" }

    /// What a person typed, as an address: spaces and a closing slash dropped, and https added when no scheme was typed.
    static func normalizedAddress(_ typed: String) -> String {
        var s = typed.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if !s.isEmpty && !s.lowercased().hasPrefix("http://") && !s.lowercased().hasPrefix("https://") {
            // Loopback is the Mac app talking to the engine on its own Mac; everything else is https.
            let host = s.split(separator: ":").first.map(String.init)?.lowercased() ?? ""
            s = (host == "127.0.0.1" || host == "localhost" ? "http://" : "https://") + s
        }
        return s
    }

    var server: String
    /// Sample home (demo mode): every request fails at once, so nothing reaches a real service.
    var demo = false
    var token: String?

    init(server: String, token: String?) {
        self.server = server
        self.token = token
    }

    enum HubError: LocalizedError {
        case notPaired, http(Int, String)
        var errorDescription: String? {
            switch self {
            case .notPaired: return "This \(Theme.device) is not paired with Zero yet."
            case .http(_, let msg): return msg.isEmpty ? "Zero had a problem. Try again in a moment." : msg
            }
        }
        var code: Int { if case .http(let c, _) = self { return c } else { return 401 } }
    }

    /// Plain words for network failures instead of the system's raw text.
    static func describe(_ error: Error) -> String {
        if let e = error as? HubError { return e.localizedDescription }
        if let u = error as? URLError {
            switch u.code {
            case .notConnectedToInternet, .networkConnectionLost, .timedOut, .cannotFindHost, .cannotConnectToHost:
                return "Can't reach Zero right now. Check that your computer is on and connected to the internet."
            default: break
            }
        }
        return "Something went wrong. Try again in a moment."
    }

    /// The home couldn't be reached at all, as opposed to answering with a problem.
    static func unreachable(_ error: Error) -> Bool {
        guard let u = error as? URLError else { return false }
        return [.notConnectedToInternet, .networkConnectionLost, .timedOut, .cannotFindHost, .cannotConnectToHost,
                .dnsLookupFailed, .secureConnectionFailed].contains(u.code)
    }

    func request(_ path: String, method: String = "GET", body: Encodable? = nil) throws -> URLRequest {
        if demo { throw URLError(.notConnectedToInternet) }
        guard !server.isEmpty, let url = URL(string: server + path) else { throw HubError.notPaired }
        var r = URLRequest(url: url)
        r.httpMethod = method
        r.timeoutInterval = 30
        if let token { r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let body {
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            r.httpBody = try JSONEncoder().encode(AnyEncodable(body))
        }
        return r
    }

    func send<T: Decodable>(_ r: URLRequest, as: T.Type) async throws -> T {
        let (data, resp) = try await HubSession.shared.data(for: r)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            if code == 401 { throw HubError.notPaired }
            throw HubError.http(code, detail ?? "")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    func pair(code: String, deviceName: String) async throws -> String {
        struct Body: Encodable { let code: String; let device_name: String }
        struct Resp: Decodable { let token: String }
        let r = try request("/api/pair", method: "POST", body: Body(code: code, device_name: deviceName))
        return try await send(r, as: Resp.self).token
    }

    func templates() async throws -> [ZipTemplate] {
        struct Resp: Decodable { let templates: [ZipTemplate] }
        return try await send(try request("/api/templates"), as: Resp.self).templates
    }

    /// What the person told the onboarding. The hub saves it and renames the lead Zip to `agent_name`.
    struct OnboardingAnswers: Encodable {
        struct Look: Encodable { let body: String; let outfit: String }
        let agent_name: String
        let person_name: String
        let about: [String: String]
        let sources: [String: Bool]
        let voice: String
        let no_computer: Bool
        let look: Look
    }

    func saveOnboarding(_ answers: OnboardingAnswers) async throws {
        struct Resp: Decodable { let ok: Bool }
        _ = try await send(try request("/api/onboarding", method: "POST", body: answers), as: Resp.self)
    }

    func bots() async throws -> [Bot] {
        struct Resp: Decodable { let bots: [Bot] }
        return try await send(try request("/api/bots"), as: Resp.self).bots
    }

    func messages(bot: String) async throws -> (messages: [HermesMessage], busy: Bool, reactions: [String: [Reaction]]) {
        struct Resp: Decodable { let messages: [HermesMessage]; let busy: Bool; let reactions: [String: [Reaction]]? }
        let r = try await send(try request("/api/bots/\(bot)/messages"), as: Resp.self)
        return (r.messages, r.busy, r.reactions ?? [:])
    }

    /// Colin's tapback on a message; nil removes it.
    func react(bot: String, message: String, kind: String?, excerpt: String) async throws {
        struct Body: Encodable { let kind: String?; let excerpt: String }
        struct Ok: Decodable { let ok: Bool }
        let id = message.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? message
        _ = try await send(try request("/api/bots/\(bot)/messages/\(id)/reaction", method: "POST",
                                       body: Body(kind: kind, excerpt: String(excerpt.prefix(200)))), as: Ok.self)
    }

    struct HubSettings: Decodable {
        let muted_bots: [String]
        let save_call_transcripts: Bool?
    }

    func settings() async throws -> HubSettings {
        try await send(try request("/api/settings"), as: HubSettings.self)
    }

    /// Whether calls are saved on Colin's own computer for 30 days (Settings, Save Call Transcripts).
    func setSaveCallTranscripts(_ on: Bool) async throws -> Bool {
        struct Body: Encodable { let save_call_transcripts: Bool }
        let r = try request("/api/settings", method: "PUT", body: Body(save_call_transcripts: on))
        return try await send(r, as: HubSettings.self).save_call_transcripts ?? on
    }

    func setMutedBots(_ ids: [String]) async throws {
        struct Body: Encodable { let muted_bots: [String] }
        struct Resp: Decodable { let muted_bots: [String] }
        _ = try await send(try request("/api/settings", method: "PUT", body: Body(muted_bots: ids)), as: Resp.self)
    }

    func activity(bot: String) async throws -> [FeedItem] {
        struct Resp: Decodable { let items: [FeedItem] }
        return try await send(try request("/api/bots/\(bot)/activity"), as: Resp.self).items
    }

    struct SavedLogin: Decodable, Identifiable, Hashable {
        let bot: String
        let id: String
        let label: String
        let origin: String?
        let identifier: String?
        let has_otp: Bool?
    }

    /// Allow Once ("once"), Always Allow ("always") or Not Now ("deny") on a sign-in request.
    /// Returns the hub's confirmation when Always Allow made a standing rule ("Allowed from now on: ...").
    @discardableResult
    func decideSignIn(_ id: Int, _ decision: String) async throws -> (title: String, text: String)? {
        struct Body: Encodable { let decision: String }
        struct Resp: Decodable { let ok: Bool; var confirmation: String? = nil; var confirmation_title: String? = nil }
        let r = try await send(try request("/api/signin-requests/\(id)", method: "POST", body: Body(decision: decision)), as: Resp.self)
        guard let text = r.confirmation, !text.isEmpty else { return nil }
        return (r.confirmation_title ?? "Added to your Allow rules", text)
    }

    struct RulesPage: Decodable {
        let rules: [PermissionRule]
        let choices: RuleChoices
    }

    struct RuleSaved: Decodable {
        let rule: PermissionRule?
        let message: String
    }

    /// Every Custom Rule, or one Zip's (with the rules for every Zip).
    func customRules(bot: String? = nil) async throws -> RulesPage {
        let q = bot.map { "?bot_id=\($0.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? $0)" } ?? ""
        return try await send(try request("/api/rules" + q), as: RulesPage.self)
    }

    func addAskFirstRule(bot: String, what: String, where place: String) async throws -> RuleSaved {
        struct Body: Encodable { let bot_id: String; let what: String; let `where`: String }
        return try await send(try request("/api/rules", method: "POST", body: Body(bot_id: bot, what: what, where: place)),
                              as: RuleSaved.self)
    }

    func editRule(_ id: Int, bot: String, what: String?, where place: String?) async throws -> RuleSaved {
        struct Body: Encodable { let bot_id: String; let what: String?; let `where`: String? }
        return try await send(try request("/api/rules/\(id)", method: "PUT", body: Body(bot_id: bot, what: what, where: place)),
                              as: RuleSaved.self)
    }

    struct PermissionsPage: Decodable {
        let permissions: [SignInPermission]
        let signins: [SignInRecord]
        var rules: [PermissionRule]? = nil
        var actions: [ActionRecord]? = nil
    }

    func permissions() async throws -> PermissionsPage {
        try await send(try request("/api/permissions"), as: PermissionsPage.self)
    }

    func deleteRule(_ id: Int) async throws {
        struct Resp: Decodable { let ok: Bool }
        _ = try await send(try request("/api/rules/\(id)", method: "DELETE"), as: Resp.self)
    }

    func setPermission(bot: String, site: String, mode: String) async throws {
        struct Body: Encodable { let bot_id: String; let site: String; let mode: String }
        struct Resp: Decodable { let ok: Bool }
        _ = try await send(try request("/api/permissions", method: "PUT", body: Body(bot_id: bot, site: site, mode: mode)), as: Resp.self)
    }

    func snooze(_ bot: String, until: Date?) async throws {
        struct Body: Encodable { let until: Double? }
        struct Resp: Decodable { let ok: Bool }
        _ = try await send(try request("/api/bots/\(bot)/snooze", method: "POST",
                                       body: Body(until: until?.timeIntervalSince1970)), as: Resp.self)
    }

    func setNotify(_ bot: String, _ on: Bool) async throws {
        struct Body: Encodable { let on: Bool }
        struct Resp: Decodable { let ok: Bool }
        _ = try await send(try request("/api/bots/\(bot)/notify", method: "PUT", body: Body(on: on)), as: Resp.self)
    }

    func renameBot(_ id: String, to name: String) async throws {
        struct Body: Encodable { let name: String }
        struct Resp: Decodable { let ok: Bool }
        _ = try await send(try request("/api/bots/\(id)/name", method: "POST", body: Body(name: name)), as: Resp.self)
    }

    func removeBot(_ id: String) async throws {
        struct Resp: Decodable { let ok: Bool }
        _ = try await send(try request("/api/bots/\(id)", method: "DELETE"), as: Resp.self)
    }

    struct Device: Decodable, Identifiable, Hashable {
        let id: Int
        let name: String
        let created_at: Double
        let current: Bool
        /// The Mac bots may use; only one device holds this, and removing it frees it.
        let mac_link: Bool?
        let last_used_at: Double?
    }

    func devices() async throws -> [Device] {
        struct Resp: Decodable { let devices: [Device] }
        return try await send(try request("/api/devices"), as: Resp.self).devices
    }

    func removeDevice(_ id: Int) async throws {
        struct Resp: Decodable { let ok: Bool }
        _ = try await send(try request("/api/devices/\(id)", method: "DELETE"), as: Resp.self)
    }

    /// A picture a bot shared, loaded with this device's key. Kept in memory for the session.
    func picture(_ id: String) async throws -> UIImage {
        if let hit = Self.pictureCache.object(forKey: id as NSString) { return hit }
        let (data, resp) = try await HubSession.shared.data(for: try request("/api/media/\(id)"))
        guard (resp as? HTTPURLResponse)?.statusCode == 200, let img = UIImage(data: data) else {
            throw HubError.http((resp as? HTTPURLResponse)?.statusCode ?? 0, "That picture could not be loaded.")
        }
        Self.pictureCache.setObject(img, forKey: id as NSString)
        return img
    }
    private static let pictureCache = NSCache<NSString, UIImage>()

    func aboutMe() async throws -> AboutMe {
        try await send(try request("/api/me"), as: AboutMe.self)
    }

    func logins() async throws -> [SavedLogin] {
        struct Resp: Decodable { let logins: [SavedLogin] }
        return try await send(try request("/api/logins"), as: Resp.self).logins
    }

    /// Returns the hub's warning when the login was saved but the card or request it answered had moved on.
    @discardableResult
    func saveLogin(bots: [String], label: String, url: String, identifier: String, identifierType: String,
                   password: String, otp: String?, taskID: String?, requestID: Int? = nil) async throws -> String? {
        struct Body: Encodable {
            let bots: [String]; let label: String; let url: String; let identifier: String
            let identifier_type: String; let password: String; let otp_secret: String?; let task_id: String?
            let request_id: Int?
        }
        struct Resp: Decodable { var warning: String? = nil }
        return try await send(try request("/api/logins", method: "POST", body: Body(
            bots: bots, label: label, url: url, identifier: identifier, identifier_type: identifierType,
            password: password, otp_secret: otp, task_id: taskID, request_id: requestID)), as: Resp.self).warning
    }

    func deleteLogin(bot: String, id: String) async throws {
        struct Resp: Decodable { let removed: Bool }
        _ = try await send(try request("/api/logins/\(bot)/\(id)", method: "DELETE"), as: Resp.self)
    }

    /// Wakes the bot's computer with the site open and Colin in control; returns the viewer path. Waking a computer
    /// that was asleep can take a while, so this waits as long as the screen's own requests do.
    func startSignIn(bot: String, url: String) async throws -> String {
        struct Body: Encodable { let url: String }
        struct Resp: Decodable { let url: String }
        var r = try request("/api/bots/\(bot)/signin", method: "POST", body: Body(url: url))
        r.timeoutInterval = 120
        return try await send(r, as: Resp.self).url
    }

    /// An error a sign-in or computer screen showed, written to the hub's log so it can be traced. Never sends a
    /// password, an address with a ticket, or anything else from the screen itself. A report that cannot reach the
    /// hub (the very failure it describes may be the connection) waits on the device and goes with the next one that
    /// gets through (Oct 9: a failed Save Login left no trace anywhere).
    func reportError(screen: String, bot: String?, message: String, detail: String) {
        guard !demo, !server.isEmpty else { return }
        let report = ErrorReport(screen: screen, bot_id: bot, message: message, detail: detail,
                                 at: Date().timeIntervalSince1970)
        Task.detached { [self] in
            if await !self.deliver(report) { Self.keep(report) }
        }
    }

    /// Sends reports kept while the hub could not be reached. Called after a poll gets through.
    func flushErrorReports() {
        guard !demo, !server.isEmpty else { return }
        let kept = Self.takeKept()
        guard !kept.isEmpty else { return }
        Task.detached { [self] in
            for r in kept where await !self.deliver(r) { Self.keep(r) }
        }
    }

    struct ErrorReport: Codable {
        let screen: String
        let bot_id: String?
        let message: String
        let detail: String
        var at: Double
    }

    private func deliver(_ r: ErrorReport) async -> Bool {
        let late = Date().timeIntervalSince1970 - r.at
        let detail = late > 30 ? "\(r.detail) (reported \(Int(late / 60)) min late)" : r.detail
        struct Body: Encodable { let screen: String; let bot_id: String?; let message: String; let detail: String }
        guard var req = try? request("/api/app-errors", method: "POST",
                                     body: Body(screen: r.screen, bot_id: r.bot_id, message: r.message, detail: detail))
        else { return false }
        req.timeoutInterval = 15
        guard let (_, resp) = try? await HubSession.shared.data(for: req) else { return false }
        return ((resp as? HTTPURLResponse)?.statusCode ?? 0) < 500
    }

    private static let keptKey = "pendingAppErrors"
    private static let keptLock = NSLock()

    private static func keep(_ r: ErrorReport) {
        keptLock.lock(); defer { keptLock.unlock() }
        var list = (UserDefaults.standard.data(forKey: keptKey)).flatMap { try? JSONDecoder().decode([ErrorReport].self, from: $0) } ?? []
        list.append(r)
        if let d = try? JSONEncoder().encode(Array(list.suffix(20))) { UserDefaults.standard.set(d, forKey: keptKey) }
    }

    private static func takeKept() -> [ErrorReport] {
        keptLock.lock(); defer { keptLock.unlock() }
        let list = (UserDefaults.standard.data(forKey: keptKey)).flatMap { try? JSONDecoder().decode([ErrorReport].self, from: $0) } ?? []
        UserDefaults.standard.removeObject(forKey: keptKey)
        return list
    }

    func finishSignIn(bot: String, taskID: String?, site: String?, declined: Bool = false,
                      requestID: Int? = nil) async throws {
        struct Body: Encodable { let task_id: String?; let site: String?; let declined: Bool; let request_id: Int? }
        struct Resp: Decodable {}
        _ = try await send(try request("/api/bots/\(bot)/signed-in", method: "POST",
                                       body: Body(task_id: taskID, site: site, declined: declined, request_id: requestID)),
                           as: Resp.self)
    }

    /// Whether a Zip's computer is awake and who holds it, for the Computer card in its chat.
    func screenStatus(bot: String) async throws -> ScreenView.ScreenStatus {
        try await send(try request("/api/bots/\(bot)/screen"), as: ScreenView.ScreenStatus.self)
    }

    /// A small still of a Zip's screen. Nil when its computer is asleep or someone else is using it.
    func screenSnapshot(bot: String) async throws -> Data? {
        var r = try request("/api/bots/\(bot)/screen/snapshot")
        r.timeoutInterval = 8
        let (data, resp) = try await HubSession.shared.data(for: r)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code == 200 { return data }
        if code == 404 || code == 423 { return nil }
        if code == 401 { throw HubError.notPaired }
        throw HubError.http(code, "")
    }

    /// Hands a Zip's computer back to it, so it carries on from where it was.
    func releaseScreen(bot: String) async throws {
        _ = try await send(try request("/api/bots/\(bot)/screen/release", method: "POST"), as: ScreenView.ScreenStatus.self)
    }

    /// Colin closed a chat's sign-in card without signing in.
    func saveLook(_ bot: String, _ look: ZipLook) async throws {
        struct Resp: Decodable {}
        _ = try await send(try request("/api/bots/\(bot)/look", method: "PUT", body: look), as: Resp.self)
    }

    func savePushToken(_ token: String, environment: String, platform: String) async throws {
        struct Body: Encodable { let token: String; let environment: String; let platform: String }
        struct Resp: Decodable {}
        _ = try await send(try request("/api/push-token", method: "POST",
                                       body: Body(token: token, environment: environment, platform: platform)),
                           as: Resp.self)
    }

    func closeSignInCard(bot: String, feedID: Int) async throws {
        struct Body: Encodable { let outcome = "declined" }
        struct Resp: Decodable {}
        _ = try await send(try request("/api/bots/\(bot)/login-requests/\(feedID)", method: "POST", body: Body()),
                           as: Resp.self)
    }

    func checkins() async throws -> [FeedItem] {
        struct Resp: Decodable { let items: [FeedItem] }
        return try await send(try request("/api/checkins"), as: Resp.self).items
    }

    func board() async throws -> Board {
        try await send(try request("/api/board"), as: Board.self)
    }

    struct FeedPage: Decodable {
        let items: [FeedItem]; let busy: [String]; let has_more: Bool?; let max_id: Int?
        let changed: [String]?; let time: Double?
        /// Chats that show a Computer card now: bot id to "working" (the Zip is using its screen) or "control" (Colin).
        let computers: [String: String]?
        let pinging: [PingInfo]?
        /// What each working Zip is doing in its chat, in plain words (Model/Fluid.swift). Older hubs leave it out.
        var activity: [String: LiveActivity]? = nil
        /// How many of the person's messages wait behind each working Zip.
        var queued: [String: Int]? = nil
    }

    func feed(after: Int, changedAfter: Double = 0) async throws -> FeedPage {
        var r = try request("/api/feed?after=\(after)&changed_after=\(changedAfter)")
        r.timeoutInterval = 8   // a poll that can't get through should say so quickly
        return try await send(r, as: FeedPage.self)
    }

    struct PairingCode: Decodable { let code: String; let expires_in: Int }

    /// A one-time code for adding another device, made from this paired one.
    func newPairingCode() async throws -> PairingCode {
        try await send(try request("/api/pair/new-code", method: "POST"), as: PairingCode.self)
    }

    /// Settings, Delete Everything: the person's computer removes what Zero holds about them (hub erase.py).
    func deleteEverything() async throws {
        struct Body: Encodable { let confirm: String }
        struct Resp: Decodable { let ok: Bool }
        _ = try await send(try request("/api/delete-everything", method: "POST", body: Body(confirm: "delete everything")),
                           as: Resp.self)
    }

    /// Sign In With Claude: the home's own Claude Code starts its sign-in and returns the page to open (plansignin.py).
    func startClaudeSignIn() async throws -> URL {
        struct Resp: Decodable { let url: String }
        var r = try request("/api/plan/claude/start", method: "POST")
        r.timeoutInterval = 40
        let url = try await send(r, as: Resp.self).url
        guard let u = URL(string: url) else { throw HubError.http(500, "Claude did not answer. Try again in a minute.") }
        return u
    }

    struct PlanSignInResult: Decodable { let ok: Bool; let account: String?; let detail: String? }

    /// The code Claude's sign-in page showed.
    func finishClaudeSignIn(code: String) async throws -> PlanSignInResult {
        struct Body: Encodable { let code: String }
        var r = try request("/api/plan/claude/code", method: "POST", body: Body(code: code))
        r.timeoutInterval = 70
        return try await send(r, as: PlanSignInResult.self)
    }

    func cancelClaudeSignIn() async {
        struct Resp: Decodable { let ok: Bool }
        _ = try? await send(try request("/api/plan/claude/cancel", method: "POST"), as: Resp.self)
    }

    func unpair() async {
        struct Resp: Decodable { let ok: Bool }
        _ = try? await send(try request("/api/unpair", method: "POST"), as: Resp.self)
    }

    func act(task: String, action: String, note: String?) async throws {
        struct Body: Encodable { let note: String? }
        struct Resp: Decodable { let ok: Bool }
        _ = try await send(try request("/api/tasks/\(task)/\(action)", method: "POST", body: Body(note: note)), as: Resp.self)
    }

    func attachmentURLRequest(task: String, attachment: Int) throws -> URLRequest {
        try request("/api/tasks/\(task)/attachments/\(attachment)")
    }

    /// Sends a message and yields the turn's events as they arrive.
    /// `replyTo`: the message the person replied to; the hub hands the Zip its quote with these words.
    func stream(bot: String, text: String, replyTo: ReplyQuote? = nil) -> AsyncThrowingStream<TurnEvent, Error> {
        struct Quote: Encodable { let text: String; let who: String }
        struct Body: Encodable { let text: String; let reply_to: Quote? }
        let body = Body(text: text, reply_to: replyTo.map { Quote(text: $0.text, who: $0.who) })
        return events { try self.request("/api/bots/\(bot)/messages", method: "POST", body: body) }
    }

    // MARK: ChatGPT-voice calls

    struct VoiceEvent: Decodable {
        let type: String
        let role: String?
        let text: String?
        /// A `news` event's number, for an On-Device Voice call to ask for it (OnDeviceCall.news).
        let id: Int?
    }

    func startVoice(bot: String, offer: String, voice: String) async throws -> (callID: String, answer: String) {
        struct Body: Encodable { let sdp: String; let voice: String }
        struct Resp: Decodable { let call_id: String; let sdp: String }
        var r = try request("/api/bots/\(bot)/voice", method: "POST", body: Body(sdp: offer, voice: voice))
        r.timeoutInterval = 45
        let resp = try await send(r, as: Resp.self)
        return (resp.call_id, resp.sdp)
    }

    /// Captions, status and replies for a call, until it closes.
    func voiceEvents(call: String) -> AsyncThrowingStream<VoiceEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var r = try self.request("/api/voice/\(call)/events")
                    r.timeoutInterval = 120
                    let (bytes, resp) = try await HubSession.shared.bytes(for: r)
                    guard (resp as? HTTPURLResponse)?.statusCode == 200 else { throw HubError.http(0, "") }
                    for try await line in bytes.lines {
                        guard line.hasPrefix("data:") else { continue }
                        let json = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                        if let ev = try? JSONDecoder().decode(VoiceEvent.self, from: Data(json.utf8)) {
                            continuation.yield(ev)
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    func voiceReady(call: String) async {
        struct Ok: Decodable { let ok: Bool }
        guard let r = try? request("/api/voice/\(call)/ready", method: "POST") else { return }
        _ = try? await send(r, as: Ok.self)
    }

    /// What the hub made of an answer. For a question Zero raised on its own, `send` is what goes to Zero as
    /// Colin's own words (nil when the hub did it itself), and `follow` is the line shown under the card.
    struct AnsweredCard: Decodable {
        let state: String?
        let picked: String?
        let follow: String?
        let send: String?
    }

    @discardableResult
    func answerChoice(bot: String, feedID: Int, picked: String?, custom: Bool = false) async throws -> AnsweredCard? {
        struct Body: Encodable { let picked: String?; let custom: Bool }
        struct Resp: Decodable { let ok: Bool; let card: AnsweredCard? }
        return try await send(try request("/api/bots/\(bot)/choices/\(feedID)", method: "POST", body: Body(picked: picked, custom: custom)),
                              as: Resp.self).card
    }

    /// An answer from the notification itself (Yes, Not Now or a typed reply), with the app in the background. The hub
    /// sends Colin's words to Zero; a Yes on a send never answers, and the hub sends a second notification instead.
    func lockAnswer(bot: String, feedID: Int, action: String, text: String?, unlocked: Bool = false) async throws {
        struct Body: Encodable { let action: String; let text: String?; let unlocked: Bool }
        struct Resp: Decodable { let ok: Bool }
        _ = try await send(try request("/api/bots/\(bot)/choices/\(feedID)/lock", method: "POST",
                                       body: Body(action: action, text: text, unlocked: unlocked)), as: Resp.self)
    }

    /// Words typed on a Zip's notification (Push.swift): they go to that Zip as a reply to what the notification said
    /// (`line`), or to the whole report it came from (`itemID`). The Zip's answer comes back as a notification.
    func notificationReply(bot: String, text: String, line: String, itemID: Int?) async throws {
        struct Body: Encodable { let text: String; let line: String; let item_id: Int? }
        struct Resp: Decodable { let ok: Bool }
        _ = try await send(try request("/api/bots/\(bot)/notification-reply", method: "POST",
                                       body: Body(text: text, line: line, item_id: itemID)), as: Resp.self)
    }

    /// The menu on one of Zero's questions: "subject" stops this question, "kind" stops this kind of question.
    func stopQuestion(bot: String, feedID: Int, what: String) async throws {
        struct Body: Encodable { let what: String }
        struct Resp: Decodable { let ok: Bool }
        _ = try await send(try request("/api/bots/\(bot)/choices/\(feedID)/stop", method: "POST", body: Body(what: what)),
                           as: Resp.self)
    }

    func proactivity() async throws -> ProactivityStatus {
        try await send(try request("/api/proactivity"), as: ProactivityStatus.self)
    }

    func setProactivity(enabled: Bool? = nil, noticers: [String: String]? = nil) async throws -> ProactivityStatus {
        struct Body: Encodable { let enabled: Bool?; let noticers: [String: String]? }
        return try await send(try request("/api/proactivity", method: "PUT", body: Body(enabled: enabled, noticers: noticers)),
                              as: ProactivityStatus.self)
    }

    func undoStoppedQuestion(_ id: Int) async throws -> ProactivityStatus {
        try await send(try request("/api/proactivity/stopped/\(id)/undo", method: "POST"), as: ProactivityStatus.self)
    }

    /// Whether some copy of Taffy on a Mac holds the link for bots to use it. Nil when the hub can't be reached.
    func macLinkOnline() async -> Bool? {
        struct Resp: Decodable { let online: Bool }
        guard let r = try? request("/api/mac/online") else { return nil }
        return (try? await send(r, as: Resp.self))?.online
    }

    /// Colin started or stopped talking, from the microphone's level, so news waits for him to finish.
    @discardableResult
    func voiceTalking(call: String, talking: Bool) async -> Bool {
        struct Body: Encodable { let talking: Bool }
        struct Ok: Decodable { let ok: Bool }
        guard var r = try? request("/api/voice/\(call)/talking", method: "POST", body: Body(talking: talking)) else { return false }
        r.timeoutInterval = 5
        return (try? await send(r, as: Ok.self)) != nil
    }

    /// The call is on hold (a phone call Colin answered, Siri) or back; the voice keeps news until he's back.
    func voiceAway(call: String, away: Bool) async {
        struct Body: Encodable { let away: Bool }
        struct Ok: Decodable { let ok: Bool }
        guard var r = try? request("/api/voice/\(call)/away", method: "POST", body: Body(away: away)) else { return }
        r.timeoutInterval = 5
        _ = try? await send(r, as: Ok.self)
    }

    /// The phone's audio is connected: the bot says hello. Tried twice, since a call without a hello sounds dead.
    func voiceReadyWithRetry(call: String) async {
        struct Ok: Decodable { let ok: Bool }
        for attempt in 0..<2 {
            guard let r = try? request("/api/voice/\(call)/ready", method: "POST") else { return }
            if (try? await send(r, as: Ok.self)) != nil { return }
            if attempt == 0 { try? await Task.sleep(for: .seconds(1)) }
        }
    }

    /// The app's own events for a call (already JSON: {"events": [...]}), saved with the call on the hub.
    @discardableResult
    func voiceDiagnostics(call: String, body: Data) async -> Bool {
        struct Ok: Decodable { let ok: Bool }
        guard var r = try? request("/api/voice/\(call)/diagnostics", method: "POST") else { return false }
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = body
        r.timeoutInterval = 15
        return (try? await send(r, as: Ok.self)) != nil
    }

    func stopVoice(call: String) async {
        struct Ok: Decodable { let ok: Bool }
        guard let r = try? request("/api/voice/\(call)/stop", method: "POST") else { return }
        _ = try? await send(r, as: Ok.self)
    }

    /// Re-attaches to a turn that is still running on the hub (after a dropped connection).
    func followTurn(bot: String, id: String? = nil) -> AsyncThrowingStream<TurnEvent, Error> {
        events { try self.request("/api/bots/\(bot)/turn" + (id.map { "?id=\($0)" } ?? "")) }
    }

    private func events(_ make: @escaping () throws -> URLRequest) -> AsyncThrowingStream<TurnEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var r = try make()
                    r.timeoutInterval = 120   // the hub sends a keepalive every 15 s
                    let (bytes, resp) = try await HubSession.shared.bytes(for: r)
                    let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
                    guard (200..<300).contains(code) else {
                        var body = Data()
                        for try await b in bytes { body.append(b); if body.count > 4000 { break } }
                        let detail = (try? JSONSerialization.jsonObject(with: body) as? [String: Any])?["detail"] as? String
                        throw code == 401 ? HubError.notPaired : HubError.http(code, detail ?? "")
                    }
                    for try await line in bytes.lines {
                        guard line.hasPrefix("data:") else { continue }
                        let json = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                        if let ev = try? JSONDecoder().decode(TurnEvent.self, from: Data(json.utf8)) {
                            continuation.yield(ev)
                            if ev.type == "done" { break }
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

private struct AnyEncodable: Encodable {
    let value: Encodable
    init(_ v: Encodable) { value = v }
    func encode(to encoder: Encoder) throws { try value.encode(to: encoder) }
}

// MARK: Connected accounts (hub accounts.py)

extension HubClient {
    /// What the Zips can use, from a check under ten minutes old.
    func accounts() async throws -> AccountsStatus {
        try await send(try request("/api/accounts"), as: AccountsStatus.self)
    }

    /// A fresh check, for the Connect screen while it is open.
    func checkAccounts() async throws -> AccountsStatus {
        try await send(try request("/api/accounts/check", method: "POST"), as: AccountsStatus.self)
    }

    /// The person closed a Connect card in a chat.
    func closeAccountCard(bot: String, feedID: Int) async throws {
        struct Body: Encodable { let outcome = "declined" }
        struct Resp: Decodable {}
        _ = try await send(try request("/api/bots/\(bot)/account-requests/\(feedID)", method: "POST", body: Body()),
                           as: Resp.self)
    }
}
