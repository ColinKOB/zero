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

    var onRefused: (() -> Void)?
    private var person = ""

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
                        self.running = false
                        self.onRefused?()
                        return
                    }
                    let wait = self.retry
                    self.retry = min(self.retry * 2, 30)
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
        if msg["event"] as? String == "person", let name = msg["name"] as? String {
            setPerson(name)
            return
        }
        if msg["event"] as? String == "approvers" {
            call(op: "approvers", args: ["keys": msg["keys"] as? [[String: Any]] ?? []]) { _ in }
            return
        }
        if msg["event"] as? String == "replaced" {
            running = false
            socket?.cancel(with: .normalClosure, reason: nil)
            socket = nil
            call(op: "idle", args: [:]) { _ in }
            Task { @MainActor [weak self] in
                while true {
                    try? await Task.sleep(for: .seconds(300))
                    guard let self, !self.running, let client = self.client else { return }
                    if await client.macLinkOnline() == false {
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

    func wisprFlow(start: Bool) async -> String? {
        guard ProcessInfo.processInfo.isMacCatalystApp, loadBridge() else { return "Dictation isn't available right now." }
        return await withCheckedContinuation { c in
            call(op: "flow", args: ["action": start ? "start" : "stop"]) { r in
                c.resume(returning: (r["ok"] as? Bool) == true ? nil : (r["error"] as? String ?? "Wispr Flow didn't start."))
            }
        }
    }

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

    func offerMoveToApplications() {
        guard ProcessInfo.processInfo.isMacCatalystApp, loadBridge(), let bridge else { return }
        _ = bridge.perform(NSSelectorFromString("offerMoveToApplications"))
    }

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
