import Foundation

/// The Mac app's line to the hub for "bots using Colin's Mac". While Taffy is open on the Mac it holds a
/// WebSocket to the hub; the hub sends `{id, op, args}` and gets back `{id, ok, result | error}`. The
/// work itself is done by TaffyMacBridge.bundle (native AppKit code the Catalyst app cannot contain).
/// The hub decides what is allowed; this only carries requests to the bridge and answers back.
@MainActor
final class MacLink {
    static let shared = MacLink()

    private var bridge: NSObject?
    private var socket: URLSessionWebSocketTask?
    private var client: HubClient?
    private var retry = 1.0
    private var running = false
    private var connectedAt: Date?

    /// Told when the hub refuses this Mac because another paired device is set as the Mac bots use.
    var onRefused: (() -> Void)?
    /// The paired person's first name, for the bridge's words to the bot ("Sam has to allow it once").
    private var person = ""

    /// Sets the name the bridge uses in its messages: from the home's settings, or the name kept at onboarding.
    func setPerson(_ name: String) {
        person = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard bridge != nil, !person.isEmpty else { return }
        call(op: "person", args: ["name": person]) { _ in }
    }

    func start(client: HubClient) {
        guard ProcessInfo.processInfo.isMacCatalystApp, !running else { return }
        self.client = client
        guard loadBridge() else { return }
        running = true
        connect()
    }

    /// Log Out: the link closes and stays closed until a home is opened again.
    func stop() {
        guard running else { return }
        running = false
        let task = socket
        socket = nil
        task?.cancel(with: .goingAway, reason: nil)
        retry = 1
        call(op: "idle", args: [:]) { _ in }
    }

    private func loadBridge() -> Bool {
        if bridge != nil { return true }
        guard let url = Bundle.main.builtInPlugInsURL?.appendingPathComponent("TaffyMacBridge.bundle"),
              let bundle = Bundle(url: url), loaded(bundle),
              let cls = bundle.principalClass as? NSObject.Type else { return false }
        let b = cls.init()
        bridge = b
        // Stop on the in-use bar ends the bot's turn on the Mac at once.
        let stop: @convention(block) () -> Void = { [weak self] in
            Task { @MainActor in self?.send(["event": "stop"]) }
        }
        _ = b.perform(NSSelectorFromString("setStopHandler:"), with: unsafeBitCast(stop, to: AnyObject.self))
        if !person.isEmpty { call(op: "person", args: ["name": person]) { _ in } }
        return true
    }

    private func loaded(_ bundle: Bundle) -> Bool {
        do { try bundle.loadAndReturnError(); return true } catch {
            NSLog("Mac bridge failed to load: %@", String(describing: error))
            return false
        }
    }

    private func connect() {
        guard running, let client, let token = client.token,
              var comps = URLComponents(string: client.server + "/api/mac/link") else { return }
        comps.scheme = comps.scheme == "https" ? "wss" : "ws"
        guard let url = comps.url else { return }
        var r = URLRequest(url: url)
        r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let task = HubSession.shared.webSocketTask(with: r)
        socket = task
        connectedAt = nil
        task.resume()
        receive(task)
        ping(task)
    }

    private func ping(_ task: URLSessionWebSocketTask) {
        Task { [weak self] in
            while let self, self.socket === task {
                try? await Task.sleep(for: .seconds(20))
                task.sendPing { _ in }
            }
        }
    }

    private func receive(_ task: URLSessionWebSocketTask) {
        task.receive { [weak self] result in
            Task { @MainActor in
                guard let self, self.socket === task else { return }
                switch result {
                case .success(let message):
                    self.retry = 1
                    if self.connectedAt == nil { self.connectedAt = Date() }
                    if case .string(let text) = message,
                       let obj = try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any] {
                        self.handle(obj)
                    }
                    self.receive(task)
                case .failure:
                    self.socket = nil
                    if task.closeCode.rawValue == 4403 {
                        // Another paired device holds the Mac link; retrying cannot help until Colin frees it.
                        self.running = false
                        self.onRefused?()
                        return
                    }
                    let wait = self.retry
                    self.retry = min(self.retry * 2, 30)
                    // A short drop keeps Colin's go-ahead, the same 15 seconds the hub waits. If the link is still
                    // down after that, the in-use bar must not linger.
                    let dropped = Date()
                    Task { @MainActor [weak self] in
                        try? await Task.sleep(for: .seconds(15))
                        guard let self, self.socket == nil || self.connectedAt ?? .distantPast < dropped else { return }
                        self.call(op: "idle", args: [:]) { _ in }
                    }
                    if !self.running { return }
                    try? await Task.sleep(for: .seconds(wait))
                    self.connect()
                }
            }
        }
    }

    private func handle(_ msg: [String: Any]) {
        // The hub may send the paired person's name; it only changes wording.
        if msg["event"] as? String == "person", let name = msg["name"] as? String {
            setPerson(name)
            return
        }
        // Phones that can allow a Zip's task from afar; the bridge asks Colin once before trusting a new one.
        if msg["event"] as? String == "approvers" {
            call(op: "approvers", args: ["keys": msg["keys"] as? [[String: Any]] ?? []]) { _ in }
            return
        }
        if msg["event"] as? String == "replaced" {
            // Another copy of Taffy on this Mac took over the link; two copies would keep bumping each other. This
            // one checks back every few minutes in case the other copy has quit.
            running = false
            socket?.cancel(with: .normalClosure, reason: nil)
            socket = nil
            call(op: "idle", args: [:]) { _ in }
            Task { @MainActor [weak self] in
                while true {
                    try? await Task.sleep(for: .seconds(300))
                    guard let self, !self.running, let client = self.client else { return }
                    if await client.macLinkOnline() == false {   // only when no other copy holds the link
                        self.running = true
                        self.connect()
                        return
                    }
                }
            }
            return
        }
        guard let id = msg["id"], let op = msg["op"] as? String else { return }
        let args = msg["args"] as? [String: Any] ?? [:]
        call(op: op, args: args) { [weak self] reply in
            var out = reply
            out["id"] = id
            self?.send(out)
        }
    }

    /// Starts or stops Wispr Flow's hands-free dictation for the composer's mic button. Nil when it worked,
    /// otherwise what to tell Colin.
    func wisprFlow(start: Bool) async -> String? {
        guard ProcessInfo.processInfo.isMacCatalystApp, loadBridge() else { return "Dictation isn't available right now." }
        return await withCheckedContinuation { c in
            call(op: "flow", args: ["action": start ? "start" : "stop"]) { r in
                c.resume(returning: (r["ok"] as? Bool) == true ? nil : (r["error"] as? String ?? "Wispr Flow didn't start."))
            }
        }
    }

    /// Starts, resumes or stops the guided voice setup in the Mac bridge (VoiceGuide.swift). This uses its own
    /// selector, not `handle:reply:`, so nothing from the hub can reach it, and the key it returns stays in this
    /// process. False when the bridge can't load (on the iPhone, or a damaged install).
    @discardableResult
    func voiceGuide(_ command: [String: Any], events: @escaping ([String: Any]) -> Void) -> Bool {
        guard ProcessInfo.processInfo.isMacCatalystApp, loadBridge(), let bridge else { return false }
        let block: @convention(block) (NSDictionary) -> Void = { d in
            Task { @MainActor in events(d as? [String: Any] ?? [:]) }
        }
        _ = bridge.perform(NSSelectorFromString("guide:events:"), with: command as NSDictionary,
                           with: unsafeBitCast(block, to: AnyObject.self))
        return true
    }

    /// Opens, resumes or reads the permission guide in the Mac bridge (PermissionGuide.swift): Accessibility, Screen
    /// Recording and Full Disk Access, one step at a time. `events` gets `{type: "status", status: {step: Bool}}`
    /// now and whenever a step changes. Its own selector, like the voice guide, so the hub cannot reach it.
    @discardableResult
    func permissionGuide(_ command: [String: Any], events: @escaping ([String: Any]) -> Void = { _ in }) -> Bool {
        guard ProcessInfo.processInfo.isMacCatalystApp, loadBridge(), let bridge else { return false }
        let block: @convention(block) (NSDictionary) -> Void = { d in
            Task { @MainActor in events(d as? [String: Any] ?? [:]) }
        }
        _ = bridge.perform(NSSelectorFromString("permissions:events:"), with: command as NSDictionary,
                           with: unsafeBitCast(block, to: AnyObject.self))
        return true
    }

    /// Once at launch on the Mac: offers to move Zero into Applications when it was opened from the disk image,
    /// Downloads or the Desktop, before anything is set up from the wrong copy (TaffyMacBridge+Move.swift).
    func offerMoveToApplications() {
        guard ProcessInfo.processInfo.isMacCatalystApp, loadBridge(), let bridge else { return }
        _ = bridge.perform(NSSelectorFromString("offerMoveToApplications"))
    }

    /// Runs a command of Zero's engine installer in the Mac bridge (TaffyMacBridge+Engine.swift): install, status,
    /// ai-status, configure-ai, pair, uninstall, or "terminal" to open Terminal. Its own selector, so the hub cannot
    /// reach it. `events` gets each printed line and then the exit. False when the bridge can't load.
    @discardableResult
    func engine(_ command: [String: Any], events: @escaping ([String: Any]) -> Void) -> Bool {
        guard ProcessInfo.processInfo.isMacCatalystApp, loadBridge(), let bridge else { return false }
        let block: @convention(block) (NSDictionary) -> Void = { d in
            Task { @MainActor in events(d as? [String: Any] ?? [:]) }
        }
        _ = bridge.perform(NSSelectorFromString("engine:events:"), with: command as NSDictionary,
                           with: unsafeBitCast(block, to: AnyObject.self))
        return true
    }

    private func call(op: String, args: [String: Any], reply: @escaping ([String: Any]) -> Void) {
        guard let bridge else { return reply(["ok": false, "error": "The Mac helper is not loaded."]) }
        let done: @convention(block) (NSDictionary) -> Void = { d in
            Task { @MainActor in reply(d as? [String: Any] ?? ["ok": false, "error": "No answer."]) }
        }
        _ = bridge.perform(NSSelectorFromString("handle:reply:"), with: ["op": op, "args": args] as NSDictionary,
                           with: unsafeBitCast(done, to: AnyObject.self))
    }

    private func send(_ obj: [String: Any]) {
        guard let socket, let data = try? JSONSerialization.data(withJSONObject: obj),
              let text = String(data: data, encoding: .utf8) else { return }
        socket.send(.string(text)) { _ in }
    }
}
