import Foundation
import Observation

/// Zero's home on this Mac: the engine that runs in the background, set up by the installer inside the app
/// (mac/engine/zero-engine, run through the Mac bridge because Catalyst cannot start processes). The setup screens
/// (Views/MacSetupView.swift), Pair a Phone or Mac and Remove Zero From This Mac use it. Mac only.
@MainActor
@Observable
final class MacEngine {
    static let shared = MacEngine()

    /// The Mac setup screens are on screen instead of the sign-in or onboarding. They show on a Mac that has no
    /// home yet, until the person sets Zero up here or chooses to connect to another home.
    var setupShowing: Bool = Theme.onMac && !UserDefaults.standard.bool(forKey: MacEngine.otherHomeKey)

    /// This app is connected to the engine on this Mac (not to another home): Settings shows Pair a Phone or Mac and
    /// Remove Zero From This Mac.
    var isLocalHome: Bool { UserDefaults.standard.bool(forKey: Self.localKey) }
    /// The person still has to pair their phone (shown once after the onboarding).
    var phonePending: Bool {
        get { UserDefaults.standard.bool(forKey: Self.phoneKey) }
        set { UserDefaults.standard.set(newValue, forKey: Self.phoneKey) }
    }

    static let localKey = "macEngineHome"
    static let phoneKey = "macPairPhonePending"
    static let otherHomeKey = "macOtherHome"

    // MARK: Install

    enum Phase: Equatable { case idle, working, failed(String), done }
    var phase: Phase = .idle
    var stepText = ""
    var step = 0
    var total = 8

    /// Runs the installer and follows its progress lines. Safe to run again after a failure: every step checks
    /// what is already there.
    func install() async -> Bool {
        phase = .working
        stepText = "Getting ready."
        step = 0
        let result = await run("install") { [weak self] line in self?.progress(line) }
        if result.code == 0 {
            phase = .done
            return true
        }
        if case .failed = phase { return false }
        phase = .failed(result.code == 127 || result.code == 126
                        ? "This copy of Zero is missing some of its files. Download Zero again and retry."
                        : "Setup stopped before it finished. Try again.")
        return false
    }

    private func progress(_ line: String) {
        let parts = line.split(separator: " ", maxSplits: 3, omittingEmptySubsequences: false).map(String.init)
        if parts.first == "ZERO-STEP", parts.count == 4 {
            step = Int(parts[1]) ?? step
            total = Int(parts[2]) ?? total
            stepText = parts[3]
        } else if line.hasPrefix("ZERO-ERROR ") {
            phase = .failed(String(line.dropFirst("ZERO-ERROR ".count)))
        }
    }

    // MARK: Status and the AI sign-in

    struct Tool: Decodable, Equatable {
        let installed: Bool
        let signed_in: Bool
        var plan: String?
    }
    struct AIStatus: Decodable, Equatable {
        let claude: Tool
        let codex: Tool
        let ready: Bool
    }

    var ai: AIStatus?
    /// DEBUG screenshots (MacSetupView.debugScreen): the sample answers stay and nothing is asked of the engine.
    var debugSample = false

    struct Status: Decodable { let installed: Bool; let running: Bool }

    /// Whether the engine is set up and answering, so a setup that was interrupted after the install goes straight
    /// on to the sign-in.
    func status() async -> Status? {
        let r = await run("status")
        return Self.lastJSON(r.output).flatMap { try? JSONDecoder().decode(Status.self, from: $0) }
    }

    func checkAI() async -> AIStatus? {
        let r = await run("ai-status")
        let status = Self.lastJSON(r.output).flatMap { try? JSONDecoder().decode(AIStatus.self, from: $0) }
        ai = status
        return status
    }

    /// Points the Zips at whichever plan is signed in now and restarts them.
    func configureAI() async -> Bool { await run("configure-ai").code == 0 }

    struct Pairing: Decodable {
        let code: String
        let expires_in: Int
        let local: String
        let server: String
        let server_ip: String?
        let link: String
        var firewall: Bool?
    }

    /// A one-time pairing code from the engine, with the local address (for this app) and the Wi-Fi address (for
    /// the phone).
    func pairing(minutes: Int = 15) async -> Pairing? {
        let r = await run("pair", args: [String(minutes)])
        return Self.lastJSON(r.output).flatMap { try? JSONDecoder().decode(Pairing.self, from: $0) }
    }

    /// Connects this app to the engine on this Mac, the same way a phone pairs.
    func connectApp(_ state: AppState) async -> String? {
        guard let p = await pairing(minutes: 10) else { return "Zero is set up but did not answer. Try again." }
        await state.pair(code: p.code, server: p.local)
        guard state.paired else { return state.lastError ?? "Zero is set up but did not answer. Try again." }
        UserDefaults.standard.set(true, forKey: Self.localKey)
        UserDefaults.standard.set(false, forKey: Self.otherHomeKey)
        phonePending = true
        return nil
    }

    /// After the onboarding: the lead's instructions name the person it met (no restart).
    func refreshSoul() async { _ = await run("soul") }

    // MARK: Signing in without Terminal

    /// Where a sign-in is: getting Claude Code or Codex onto this Mac, then waiting for the person to sign in on the
    /// page the engine opened in their browser.
    enum SignIn: Equatable { case idle, preparing(String), waiting, failed(String) }
    var signIn: SignIn = .idle
    /// "claude" or "codex", while a sign-in runs.
    var signingIn: String?
    /// The sign-in page, so the person can open it again if they closed the tab.
    var signInPage: URL?

    /// Gets the tool with its official installer if it is missing, then runs its own sign-in in the browser
    /// (zero-engine sign-in). True once signed in. Ends any other sign-in first.
    func signIn(with tool: String) async -> Bool {
        if signingIn != nil { await stopSignIn() }
        signingIn = tool
        signInPage = nil
        signIn = .preparing("Getting ready.")
        let result = await run("sign-in", args: [tool]) { [weak self] line in
            guard let self, self.signingIn == tool else { return }
            let parts = line.split(separator: " ", maxSplits: 3, omittingEmptySubsequences: false).map(String.init)
            if parts.first == "ZERO-STEP", parts.count == 4 {
                self.signIn = parts[1] == "1" ? .preparing(parts[3]) : .waiting
            } else if line.hasPrefix("ZERO-URL "), let url = URL(string: String(line.dropFirst("ZERO-URL ".count))) {
                self.signInPage = url
            } else if line.hasPrefix("ZERO-ERROR ") {
                self.signIn = .failed(String(line.dropFirst("ZERO-ERROR ".count)))
            }
        }
        // Another sign-in took over, or the person stopped this one (the engine exits 3).
        guard signingIn == tool, result.code != 3 else { return false }
        signingIn = nil
        if result.code == 0 {
            signIn = .idle
            _ = await checkAI()
            return ai?.ready == true
        }
        if case .failed = signIn {} else { signIn = .failed("The sign-in did not finish. Try again.") }
        return false
    }

    /// Ends a sign-in that is still waiting.
    func stopSignIn() async {
        signingIn = nil
        signInPage = nil
        signIn = .idle
        _ = await run("sign-in-stop")
    }

    // MARK: Remove

    /// Stops the engine, removes it, and removes the person's data too when `keepData` is false.
    func remove(keepData: Bool, state: AppState) async -> Bool {
        let r = await run("uninstall", args: [keepData ? "--keep-data" : "--delete-data"])
        guard r.code == 0 else { return false }
        await state.unpair()
        UserDefaults.standard.set(false, forKey: Self.localKey)
        phonePending = false
        UserDefaults.standard.removeObject(forKey: "onboarded_v1")
        phase = .idle
        setupShowing = true
        return true
    }

    // MARK: Running the installer

    struct Result { let code: Int; let output: String }

    private func run(_ action: String, args: [String] = [], line: ((String) -> Void)? = nil) async -> Result {
        await withCheckedContinuation { (c: CheckedContinuation<Result, Never>) in
            var finished = false
            let started = MacLink.shared.engine(["action": action, "args": args]) { event in
                if event["type"] as? String == "line", let text = event["text"] as? String {
                    line?(text)
                } else if event["type"] as? String == "exit", !finished {
                    finished = true
                    c.resume(returning: Result(code: event["code"] as? Int ?? 1, output: event["output"] as? String ?? ""))
                }
            }
            if !started {
                finished = true
                c.resume(returning: Result(code: 127, output: ""))
            }
        }
    }

    /// The last line of the output that is a JSON object (the engine prints one for status, ai-status and pair).
    static func lastJSON(_ output: String) -> Data? {
        output.split(separator: "\n").reversed().first { $0.hasPrefix("{") }.map { Data($0.utf8) }
    }
}
