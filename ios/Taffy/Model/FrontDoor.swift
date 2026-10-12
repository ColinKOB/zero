import Foundation
import UIKit

/// Zero accounts: a username and a password that lead to the person's home (alien/frontdoor on the server). Sign-up
/// makes a new home and takes a few minutes; sign-in returns the same address and token a pairing code gives, so
/// everything after it is the same as pairing.
///
/// DEBUG: `-frontDoor <address>` points the app at another front door (the QA one).
enum FrontDoor {
    static let defaultAddress = "https://alien.tail0000.ts.net:6440"

    /// New accounts on the front door make a home on Colin's server, which strangers must never get (Oct 12: the
    /// public release puts each person's home on their own Mac). Off in every build; a DEBUG run with `-frontDoor`
    /// or `-frontDoorSignUp` turns it on for the account UI tests. Signing in to an account that exists still works.
    static var signUpOpen: Bool {
        #if DEBUG
        let args = CommandLine.arguments
        return args.contains("-frontDoor") || args.contains("-frontDoorSignUp")
        #else
        return false
        #endif
    }

    static var address: String {
        #if DEBUG
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "-frontDoor"), i + 1 < args.count { return args[i + 1] }
        #endif
        return UserDefaults.standard.string(forKey: "frontDoor") ?? defaultAddress
    }

    /// Password AutoFill on the account fields. DEBUG `-noAutoFill` turns it off for the UI tests, where the strong
    /// password cover hides what the test types.
    static var autoFill: Bool {
        #if DEBUG
        return !CommandLine.arguments.contains("-noAutoFill")
        #else
        return true
        #endif
    }

    struct SignIn: Decodable { let server: String; let token: String; let username: String; let plan: String? }
    struct Job: Decodable {
        let state: String          // waiting, creating, ready or failed
        let message: String
        var job: String? = nil
        var step: String? = nil
        var seconds: Int? = nil
    }

    enum Problem: LocalizedError {
        case said(Int, String)
        var errorDescription: String? { if case .said(_, let m) = self { return m } else { return nil } }
        var code: Int { if case .said(let c, _) = self { return c } else { return 0 } }
    }

    static var deviceName: String {
        ProcessInfo.processInfo.isMacCatalystApp ? "Mac" : UIDevice.current.name
    }

    static func signUp(username: String, password: String, name: String) async throws -> Job {
        struct Body: Encodable { let username, password, name, timezone, device_name: String }
        return try await send("/api/sign-up", Body(username: username, password: password, name: name,
                                                    timezone: TimeZone.current.identifier, device_name: deviceName))
    }

    static func status(_ job: String) async throws -> Job {
        try await send("/api/sign-up/\(job)", Optional<String>.none)
    }

    static func signIn(username: String, password: String) async throws -> SignIn {
        struct Body: Encodable { let username, password, device_name: String }
        return try await send("/api/sign-in", Body(username: username, password: password, device_name: deviceName))
    }

    private static func send<T: Decodable, B: Encodable>(_ path: String, _ body: B?) async throws -> T {
        guard let url = URL(string: address + path) else { throw Problem.said(0, "Zero can't be reached right now.") }
        var r = URLRequest(url: url)
        r.timeoutInterval = 40
        if let body {
            r.httpMethod = "POST"
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            r.httpBody = try JSONEncoder().encode(body)
        }
        let (data, resp) = try await URLSession.shared.data(for: r)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            throw Problem.said(code, detail ?? "Zero had a problem. Try again in a moment.")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    /// One plain sentence for any failure on the sign-in and account screens.
    static func describe(_ error: Error) -> String {
        if let p = error as? Problem, let m = p.errorDescription { return m }
        if let u = error as? URLError {
            switch u.code {
            case .notConnectedToInternet: return "Your \(Theme.device) is offline. Try again once it is back online."
            case .timedOut: return "Zero is taking too long to answer. Try again in a moment."
            default: return "Zero can't be reached right now. Check your internet connection and try again."
            }
        }
        return "Something went wrong. Try again in a moment."
    }
}

extension AppState {
    /// The username this device signed in with, for Settings. Empty after pairing with a code.
    static var accountUsername: String { UserDefaults.standard.string(forKey: "accountUsername") ?? "" }

    /// Uses a home's address and device token, from sign-in or a pairing code, the same way pairing does.
    func connect(server: String, token: String) async {
        client.server = HubClient.normalizedAddress(server)
        client.token = token
        Keychain.write("hubToken", token)
        UserDefaults.standard.set(client.server, forKey: "server")
        lastError = nil
        pairUnreachable = false
        paired = true
        await refreshAll()
        startPolling()
    }

    /// Signs in to an existing account and opens its home. Returns nil when it worked, else one plain sentence.
    /// An account that already exists skips the onboarding and the older welcome.
    func signIn(username: String, password: String, existing: Bool = true) async -> String? {
        do {
            let r = try await FrontDoor.signIn(username: username.trimmingCharacters(in: .whitespaces),
                                               password: password)
            UserDefaults.standard.set(r.username, forKey: "accountUsername")
            if existing { markSetUp() }
            await connect(server: r.server, token: r.token)
            return nil
        } catch {
            return FrontDoor.describe(error)
        }
    }

    /// An account or home that was set up before: no onboarding, no welcome tour.
    func markSetUp() {
        UserDefaults.standard.set(true, forKey: "onboarded_v1")
        UserDefaults.standard.set(true, forKey: "welcomed_v1")
        OnboardingModel.clearSaved()
        showWelcome = false
        onboarding = false
    }

    static let pendingPicksKey = "pendingPicksMessage"

    /// The home is on the person's own plan and nothing is signed in, so its Zips cannot answer yet.
    var planMissing: Bool {
        guard let s = AccountsModel.shared.status, !HomeModel.shared.isShared else { return false }
        return s.plan == "none" && s.claude.state != "shared" && s.claude.state != "unknown"
    }

    /// The Zips picked during the onboarding, asked for once a plan is signed in.
    func sendPendingPicks() async {
        guard let ask = UserDefaults.standard.string(forKey: Self.pendingPicksKey) else { return }
        UserDefaults.standard.removeObject(forKey: Self.pendingPicksKey)
        _ = await send(ask, to: "default")
    }

    /// Log Out: this device forgets its token and everything it kept for the home, and goes back to the sign-in
    /// screen. The home, its Zips and its chats stay as they are; only this device's token stops working.
    func logOut() async {
        MacLink.shared.stop()
        if paired && !demoMode && !fixtureMode { await client.unpair() }
        wipeThisDevice()
        AccountsModel.shared.status = nil   // the next account's plan is its own
        onboarding = false   // the sign-in screen, not a stranger's onboarding
    }
}
