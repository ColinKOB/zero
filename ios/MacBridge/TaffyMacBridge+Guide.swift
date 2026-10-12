import AppKit

/// The guided voice setup's door into the bridge. This is a separate selector from `handle:reply:` on
/// purpose: only Zero's own window calls it, never the hub, so the hub can't start a guide or receive a key.
extension TaffyMacBridge {
    /// `command`: `{op: "start", studio?: host, startURL?: url, browserApp?: path}`, `{op: "resume"}` or `{op: "stop"}`.
    /// `studio`, `startURL` and `browserApp` are for testing against the practice page in a test browser.
    /// `events` gets `{type: needsAccess | step | key | paste | stopped | lost, ...}` on the main thread.
    @objc func guide(_ command: NSDictionary, events: @escaping @convention(block) (NSDictionary) -> Void) {
        let op = command["op"] as? String ?? ""
        let studio = command["studio"] as? String
        let start = (command["startURL"] as? String).flatMap(URL.init(string:))
        let browserApp = (command["browserApp"] as? String).map { URL(fileURLWithPath: $0) }
        DispatchQueue.main.async {
            MainActor.assumeIsolated {
                let guide = VoiceGuide.shared
                switch op {
                case "start":
                    let script = studio.map { GuideScript.geminiKey(studio: $0, startURL: start) } ?? GuideScript.geminiKey()
                    guide.start(script: script, browserApp: browserApp) { events($0 as NSDictionary) }
                case "resume": guide.resume()
                case "stop": guide.stop()
                default: events(["type": "error", "message": "Unknown guide command."])
                }
            }
        }
    }
}

/// The permission guide's door (PermissionGuide.swift). Like `guide:events:`, only Zero's own window calls this.
extension TaffyMacBridge {
    /// `command`: `{op: "start", focus?: "accessibility" | "screen" | "files"}`, `{op: "status"}`, `{op: "resume"}`
    /// (at launch, after a restart) or `{op: "stop"}`. `events` gets `{type: "status", status: {step: Bool}}`.
    @objc func permissions(_ command: NSDictionary, events: @escaping @convention(block) (NSDictionary) -> Void) {
        let op = command["op"] as? String ?? ""
        let focus = (command["focus"] as? String).flatMap(PermissionGuide.Step.init(rawValue:))
        DispatchQueue.main.async {
            MainActor.assumeIsolated {
                let guide = PermissionGuide.shared
                guide.onChange = { events(["type": "status", "status": $0] as NSDictionary) }
                switch op {
                case "start": guide.start(focus: focus)
                case "resume": guide.resumeIfNeeded()
                case "stop": guide.stop()
                default: break
                }
                events(["type": "status", "status": PermissionGuide.status()] as NSDictionary)
            }
        }
    }

    /// A Zip ran into a permission that is off: the guide opens at that step on the Mac (at most every ten minutes).
    func offerGuide(_ step: PermissionGuide.Step) {
        let show = {
            MainActor.assumeIsolated {
                guard !self.guideOfferedThisGrant else { return }   // once per grant
                self.guideOfferedThisGrant = true
                PermissionGuide.shared.offer(step)
            }
        }
        if Thread.isMainThread { show() } else { DispatchQueue.main.async(execute: show) }
    }
}
