import AppKit

/// Runs Zero's engine installer (Zero.app/Contents/Resources/Engine/zero-engine, staged from mac/engine by the Mac
/// build) for the app's own setup screens: install, status, the AI sign-in check, pairing codes and uninstall.
/// Mac Catalyst cannot start processes, so the app asks this bundle. Its own selector, like the voice guide, so
/// nothing from the hub can reach it, and only the commands listed here run.
extension TaffyMacBridge {
    private static let engineActions: Set<String> = [
        "install", "start", "stop", "status", "ai-status", "configure-ai", "pair", "soul", "uninstall", "sign-in",
        "sign-in-stop",
    ]

    /// `command`: `{action, args?: [String]}`; or `{action: "terminal"}` to open Terminal for an official
    /// installer the person runs themselves, or `{action: "reveal", path}` to show a folder in Finder.
    /// `events` gets `{type: "line", text}` for each line the engine prints, then `{type: "exit", code, output}`.
    @objc(engine:events:)
    func engine(_ command: NSDictionary, events: @escaping @convention(block) (NSDictionary) -> Void) {
        let action = command["action"] as? String ?? ""
        if action == "terminal" {
            let url = URL(fileURLWithPath: "/System/Applications/Utilities/Terminal.app")
            NSWorkspace.shared.openApplication(at: url, configuration: NSWorkspace.OpenConfiguration()) { _, error in
                DispatchQueue.main.async { events(["type": "exit", "code": error == nil ? 0 : 1, "output": ""]) }
            }
            return
        }
        if action == "reveal", let path = command["path"] as? String {
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
            events(["type": "exit", "code": 0, "output": ""])
            return
        }
        guard Self.engineActions.contains(action),
              let script = Bundle.main.resourceURL?.appendingPathComponent("Engine/zero-engine"),
              FileManager.default.isExecutableFile(atPath: script.path) else {
            events(["type": "exit", "code": 127, "output": "This copy of Zero is missing its setup files."])
            return
        }
        // Only short plain arguments: a pairing code's minutes, or the uninstall choice.
        let extra = (command["args"] as? [String] ?? []).filter {
            $0.range(of: #"^[A-Za-z0-9.-]{1,20}$"#, options: .regularExpression) != nil
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [script.path, action] + extra
        p.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
        let out = Pipe()
        p.standardOutput = out
        p.standardError = out
        p.standardInput = FileHandle.nullDevice
        var collected = Data()
        var pending = Data()
        let lock = NSLock()
        out.fileHandleForReading.readabilityHandler = { h in
            let chunk = h.availableData
            guard !chunk.isEmpty else { return }
            lock.lock()
            collected.append(chunk)
            if collected.count > 256_000 { collected = collected.suffix(128_000) }
            pending.append(chunk)
            var lines: [String] = []
            while let nl = pending.firstIndex(of: 0x0A) {
                lines.append(String(decoding: pending[pending.startIndex..<nl], as: UTF8.self))
                pending = Data(pending[(nl + 1)...])
            }
            lock.unlock()
            for line in lines { DispatchQueue.main.async { events(["type": "line", "text": line]) } }
        }
        p.terminationHandler = { proc in
            out.fileHandleForReading.readabilityHandler = nil
            let rest = out.fileHandleForReading.readDataToEndOfFile()
            lock.lock()
            collected.append(rest)
            let text = String(decoding: collected, as: UTF8.self)
            lock.unlock()
            DispatchQueue.main.async {
                events(["type": "exit", "code": Int(proc.terminationStatus), "output": text])
            }
        }
        do { try p.run() } catch {
            events(["type": "exit", "code": 126, "output": "Zero could not start its setup."])
        }
    }
}
