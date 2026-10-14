import Foundation
import UIKit

enum FrontDoor {
    static let defaultAddress = "https://alien.tail0000.ts.net:6440"

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
        let state: String
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
    static var accountUsername: String { UserDefaults.standard.string(forKey: "accountUsername") ?? "" }

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

    func markSetUp() {
        UserDefaults.standard.set(true, forKey: "onboarded_v1")
        UserDefaults.standard.set(true, forKey: "welcomed_v1")
        OnboardingModel.clearSaved()
        showWelcome = false
        onboarding = false
    }

    static let pendingPicksKey = "pendingPicksMessage"

    var planMissing: Bool {
        guard let s = AccountsModel.shared.status, !HomeModel.shared.isShared else { return false }
        return s.plan == "none" && s.claude.state != "shared" && s.claude.state != "unknown"
    }

    func sendPendingPicks() async {
        guard let ask = UserDefaults.standard.string(forKey: Self.pendingPicksKey) else { return }
        UserDefaults.standard.removeObject(forKey: Self.pendingPicksKey)
        _ = await send(ask, to: "default")
    }

    func logOut() async {
        MacLink.shared.stop()
        if paired && !demoMode && !fixtureMode { await client.unpair() }
        wipeThisDevice()
        AccountsModel.shared.status = nil
        onboarding = false
    }
}
