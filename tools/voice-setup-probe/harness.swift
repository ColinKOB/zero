// Runs Zero's real guide engine (ios/MacBridge/VoiceGuide.swift) over a browser window and takes a screenshot of
// each step once it settles. With --autopilot it presses each highlighted element like a person would.
// Build: see run-harness.sh. The key, when one arrives, is never printed; only its last four characters.
import AppKit
import ApplicationServices

var opts: [String: String] = [:]
var flags = Set<String>()
var i = 1
let argv = CommandLine.arguments
while i < argv.count {
    let a = argv[i]
    if a.hasPrefix("--"), i + 1 < argv.count, !argv[i + 1].hasPrefix("--") { opts[String(a.dropFirst(2))] = argv[i + 1]; i += 2 }
    else { flags.insert(String(a.dropFirst(2))); i += 1 }
}
guard let pidText = opts["pid"], let pid = pid_t(pidText) else { print("need --pid"); exit(2) }
let shots = URL(fileURLWithPath: opts["shots"] ?? "/tmp/zero-guide-shots")
try? FileManager.default.createDirectory(at: shots, withIntermediateDirectories: true)
let studio = opts["studio"] ?? "aistudio.google.com"
let stopAt = opts["stop-at"]
let seconds = Double(opts["seconds"] ?? "90") ?? 90
let autopilot = flags.contains("autopilot")
let practiceKey = "AQ.Zero-Practice-Key-Not-Real-00000000000000000000000"

let pb = NSPasteboard.general
let savedClipboard: [[(NSPasteboard.PasteboardType, Data)]] = (pb.pasteboardItems ?? []).map { item in
    item.types.compactMap { t in item.data(forType: t).map { (t, $0) } }
}
func restoreClipboard() {
    pb.clearContents()
    pb.writeObjects(savedClipboard.map { pairs in
        let it = NSPasteboardItem(); for (t, d) in pairs { it.setData(d, forType: t) }; return it
    })
}
func finish(_ code: Int32) -> Never { restoreClipboard(); exit(code) }

var shotCount = 0
var lastState = ""
var stateSince = Date()
var done = Set<String>()

@MainActor func shoot(_ name: String) {
    let g = VoiceGuide.shared
    guard let r = g.debugWindowFrame ?? cardWindow() else { return }
    shotCount += 1
    let file = shots.appendingPathComponent(String(format: "%02d-%@.png", shotCount, name)).path
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
    p.arguments = ["-x", "-R\(Int(r.minX)),\(Int(r.minY)),\(Int(r.width)),\(Int(r.height))", file]
    try? p.run(); p.waitUntilExit()
    try? g.debugPageText.write(toFile: file.replacingOccurrences(of: ".png", with: ".txt"), atomically: true, encoding: .utf8)
    print("shot", file)
}

/// In card mode the guide has no accessibility snapshot; find the window like the guide does.
func cardWindow() -> CGRect? {
    let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
    guard let b = list.first(where: { ($0[kCGWindowOwnerPID as String] as? pid_t) == pid && ($0[kCGWindowLayer as String] as? Int) == 0 })?[kCGWindowBounds as String] as? [String: CGFloat] else { return nil }
    return CGRect(x: b["X"] ?? 0, y: b["Y"] ?? 0, width: b["Width"] ?? 0, height: b["Height"] ?? 0)
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
MainActor.assumeIsolated {
    let g = VoiceGuide.shared
    g.forceCardMode = flags.contains("card")
    g.debugHook = { step, status, element in
        let state = "\(step.id)-\(status)"
        if state != lastState { lastState = state; stateSince = Date(); print("state", state); return }
        guard !done.contains(state), Date().timeIntervalSince(stateSince) > 1.3 else { return }
        done.insert(state)
        shoot(state)
        if flags.contains("stuck") && !done.contains("stuck") {
            done.insert("stuck")
            g.debugSetStuck(true)
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { MainActor.assumeIsolated { shoot(step.id + "-stuck") ; finish(0) } }
            return
        }
        if step.id == stopAt && (status == .pointing || status == .notFound) { print("reached", step.id); finish(0) }
        // --nokey: on the copy step, press I Copied It with no key on the clipboard, then picture the answer.
        if flags.contains("nokey"), step.id == "copy", status == .pointing, !done.contains("nokey") {
            done.insert("nokey")
            pb.clearContents(); pb.setString("not a key", forType: .string)
            g.debugPressNext()
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { MainActor.assumeIsolated { shoot("copy-nokey"); finish(0) } }
            return
        }
        // --closewindow: on the keys step, close the browser window, wait for Zero to notice, then Show the Guide.
        if flags.contains("closewindow"), step.id == "keys-page", status == .pointing, !done.contains("closed") {
            done.insert("closed")
            let app = AXUIElementCreateApplication(pid)
            var w: AnyObject?
            AXUIElementCopyAttributeValue(app, kAXMainWindowAttribute as CFString, &w)
            var close: AnyObject?
            if let w { AXUIElementCopyAttributeValue(w as! AXUIElement, kAXCloseButtonAttribute as CFString, &close) }
            if let close { AXUIElementPerformAction(close as! AXUIElement, kAXPressAction as CFString) }
            print("closed the browser window")
            return
        }
        guard autopilot, status == .pointing, let element else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
            NSRunningApplication(processIdentifier: pid)?.activate()
            let r = AXUIElementPerformAction(element, kAXPressAction as CFString)
            print("pressed", step.id, r == .success ? "ok" : "error \(r.rawValue)")
        }
    }
    let script = GuideScript.geminiKey(studio: studio, startURL: opts["start"].flatMap(URL.init(string:)))
    g.start(script: script, browserPid: pid, openPage: false) { event in
        let type = event["type"] as? String ?? "?"
        if type == "key", let key = event["key"] as? String {
            print("key received,", GeminiKeyPattern.ending(key) + ",", "is the practice key:", key == practiceKey)
            finish(key == practiceKey ? 0 : 3)
        }
        print("event", type, event["id"] as? String ?? "")
        if type == "noWindow" {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { MainActor.assumeIsolated {
                print("resume after the window closed"); g.resume()
            } }
        }
        if type == "stopped" || type == "lost" { finish(1) }
    }
    NSRunningApplication(processIdentifier: pid)?.activate()
}
DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { print("timed out"); finish(4) }
app.run()
