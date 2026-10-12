import AppKit
import ApplicationServices
import ScreenCaptureKit

/// Native macOS half of "bots using Colin's Mac". Mac Catalyst cannot run processes, capture the screen
/// or post input events, so the Catalyst app loads this bundle and calls `handle:reply:` with a request
/// dictionary `{op, args}`. Every reply is a dictionary with `ok` and either `result` or `error`.
/// The hub keeps the grant and the command guard, but this side never acts on its word alone: nothing
/// that touches the Mac runs unless Colin pressed Allow on this Mac in the last 30 minutes and has not
/// pressed Stop since. A changed or compromised hub still cannot use the Mac without him.
@objc(TaffyMacBridge)
final class TaffyMacBridge: NSObject {
    private var bar: NSPanel?
    private var approval: NSPanel?
    private var approvalReply: ((Bool, Bool) -> Void)?
    /// The request on the card now, as the hub sent it: what a phone's signature has to cover.
    var pendingApproval: (id: String, bot: String, task: String, said: String, expires: Int)?
    /// Where the last request was answered: "mac" (the card's buttons) or "phone".
    var answeredVia = "mac"
    /// The one-time question about trusting a phone's approvals (TaffyMacBridge+Phone.swift).
    var trustPanel: NSPanel?
    var trustCandidate: (id: String, key: String)?
    /// Whether the permission guide was already shown during this grant (offerGuide).
    var guideOfferedThisGrant = false
    private var stopHandler: (() -> Void)?
    /// Elements from the last UI list, so a bot can press one by number without touching the mouse.
    var elements: [AXUIElement] = []
    /// The same controls with their names, for pressing by name (TaffyMacBridge+UI.swift).
    var items: [Item] = []
    /// The app the bot is working in; background input goes to it instead of the front app.
    var targetPid: pid_t = 0
    private var cursor: NSPanel?
    private var botName = "Taffy"
    /// The paired person's first name for the words the bot reads ("Sam has to allow it once"), set by the app from
    /// the home's settings and by the hub's "person" event. Never used to decide anything.
    private var person = ""
    var who: String { person.isEmpty ? "The person" : person }
    /// Colin's Allow, held here: set only by the Allow button, cleared by Stop, idle or time.
    private var grantUntil: Date?
    /// Whether Colin said yes to the bot using his real mouse during this grant.
    private var realMouseAllowed = false
    private var mousePanel: NSPanel?
    private var mouseReply: ((Bool) -> Void)?
    private var approvalTimer: Timer?
    private static let grantSeconds: TimeInterval = 30 * 60
    private static let approvalSeconds: TimeInterval = 115
    /// Apps where typed keys become commands, including other AI agents that can run them; bots run commands
    /// through mac_run, which has the guard and the sandbox.
    static let shells: Set<String> = [
        "com.apple.Terminal", "com.googlecode.iterm2", "com.apple.ScriptEditor2", "com.apple.Automator",
        "com.apple.shortcuts", "dev.warp.Warp-Stable", "com.mitchellh.ghostty", "net.kovidgoyal.kitty",
        "io.alacritty", "co.zeit.hyper", "com.microsoft.VSCode", "com.apple.dt.Xcode",
        "com.anysphere.sand", "com.anthropic.claudefordesktop", "com.openai.codex", "com.openai.chat",
        "com.todesktop.230313mzl4w4u92", "com.t3tools.t3code",
    ]
    /// Apps that never open for a bot: they run whatever file they are given.
    private static let launchers: Set<String> = [
        "com.apple.Terminal", "com.googlecode.iterm2", "com.apple.ScriptEditor2", "com.apple.Automator",
        "com.apple.shortcuts", "com.apple.installer", "dev.warp.Warp-Stable", "com.mitchellh.ghostty",
        "net.kovidgoyal.kitty", "io.alacritty", "co.zeit.hyper",
    ]
    /// Files that run something when opened.
    private static let runnable: Set<String> = [
        "command", "tool", "sh", "zsh", "bash", "scpt", "scptd", "applescript", "workflow", "shortcut", "terminal",
        "pkg", "mpkg", "app", "webloc", "inetloc", "fileloc", "py", "rb", "pl", "jar", "dmg", "action", "prefpane",
    ]
    /// Commands running now, so Stop and the end of a grant end them too.
    private var running: [Process] = []
    private let runningLock = NSLock()

    private var granted: Bool {
        guard let until = grantUntil else { return false }
        if until > Date() { return true }
        endGrant()
        return false
    }

    @objc func handle(_ request: NSDictionary, reply: @escaping @convention(block) (NSDictionary) -> Void) {
        let op = request["op"] as? String ?? ""
        let args = request["args"] as? [String: Any] ?? [:]
        let done: ([String: Any]) -> Void = { r in DispatchQueue.main.async { reply(r as NSDictionary) } }
        let ok: (Any) -> Void = { done(["ok": true, "result": $0]) }
        let fail: (String) -> Void = { done(["ok": false, "error": $0]) }
        // Distinct code so the hub ends its own copy of the grant instead of telling the bot to carry on.
        let notGranted: () -> Void = {
            done(["ok": false, "code": "not_granted",
                  "error": "\(self.who)'s go-ahead on this Mac has ended (Stop, time, or a dropped connection). Ask again if there is more to do."])
        }

        let gated: Set<String> = ["open", "run", "screenshot", "ui", "click", "press", "type", "key", "scroll", "steps"]
        if gated.contains(op) {
            var allowed = false
            if Thread.isMainThread { allowed = granted } else { DispatchQueue.main.sync { allowed = self.granted } }
            guard allowed else { return notGranted() }
        }

        switch op {
        case "open": DispatchQueue.main.async { self.open(args, ok, fail) }
        case "run": DispatchQueue.global().async { self.run(args, ok, fail) }
        case "screenshot": Task { await self.screenshot(ok, fail) }
        case "ui": DispatchQueue.main.async { self.uiList(args, ok, fail) }
        case "steps": DispatchQueue.main.async { self.steps(args, ok, fail) }
        case "click": DispatchQueue.main.async { self.click(args, ok, fail) }
        case "press": DispatchQueue.main.async { self.press(args, ok, fail) }
        case "type": DispatchQueue.main.async { self.type(args, ok, fail) }
        case "key": DispatchQueue.main.async { self.key(args, ok, fail) }
        case "scroll": DispatchQueue.main.async { self.scroll(args, ok, fail) }
        case "approve": DispatchQueue.main.async {
            self.showApproval(args) { allowed, timedOut in
                ok(["allowed": allowed, "timed_out": timedOut, "via": self.answeredVia])
            }
        }
        // Colin's answer from his phone (TaffyMacBridge+Phone.swift): Allow only with a signature from a phone he
        // trusted on this Mac, over this very request; Not Now closes the card.
        case "grant": DispatchQueue.main.async { self.grantFromPhone(args, ok, fail) }
        case "dismiss": DispatchQueue.main.async {
            if (args["approval_id"] as? String) == self.pendingApproval?.id {
                self.answeredVia = "phone"
                self.dismissApproval(false)
            }
            ok(true)
        }
        case "approvers": DispatchQueue.main.async { self.phonesSeen(args["keys"] as? [[String: Any]] ?? []); ok(true) }
        case "flow": DispatchQueue.main.async { self.flow(args, ok, fail) }   // Colin's own mic button, not a bot
        case "in_use": DispatchQueue.main.async { ok(self.granted) }   // the bar shows when Colin presses Allow
        case "person": DispatchQueue.main.async {   // the paired person's name, for wording only
            self.person = String((args["name"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines).prefix(40))
            ok(true)
        }
        case "idle": DispatchQueue.main.async {
            self.dismissApproval(false); self.endGrant(); ok(true)
        }
        default: fail("Unknown action \(op).")
        }
    }

    /// Called when Colin presses Stop on the in-use bar.
    @objc func setStopHandler(_ handler: @escaping @convention(block) () -> Void) {
        stopHandler = handler
    }

    // MARK: Open and run

    /// Opens without taking over Colin's screen, and remembers the app so later input goes to it.
    private func open(_ args: [String: Any], _ ok: @escaping (Any) -> Void, _ fail: @escaping (String) -> Void) {
        let target = (args["target"] as? String ?? "").trimmingCharacters(in: .whitespaces)
        guard !target.isEmpty else { return fail("Nothing to open.") }
        if let reason = openRefusal(target) { return fail(reason) }
        let ws = NSWorkspace.shared
        let cfg = NSWorkspace.OpenConfiguration()
        // front brings the app onto this screen: its windows may be full screen or in another Space, where nothing
        // can see or reach them (Oct 10: Pages and System Settings were both out of reach behind a full-screen app).
        let front = args["front"] as? Bool ?? false
        cfg.activates = front
        let done: (NSRunningApplication?, Error?) -> Void = { app, err in
            DispatchQueue.main.async {
                if let app {
                    self.targetPid = app.processIdentifier
                    if front { app.activate(options: [.activateAllWindows]) }
                }
                err == nil ? ok(front ? "Opened \(target) in front." : "Opened \(target) in the background.")
                           : fail("macOS could not open \(target).")
            }
        }
        if let url = URL(string: target), let scheme = url.scheme, scheme.count > 1 {
            return ws.open(url, configuration: cfg, completionHandler: done)
        }
        let path = (target as NSString).expandingTildeInPath
        if FileManager.default.fileExists(atPath: path) {
            return ws.open(URL(fileURLWithPath: path), configuration: cfg, completionHandler: done)
        }
        // An app by name, such as "Safari" or "Notes", or the start of its name: Apple renamed Pages to "Pages
        // Creator Studio" (Oct 10: "open Pages" found nothing though Pages was installed).
        let folders = ["/Applications", "/System/Applications", "/Applications/Utilities",
                       (("~/Applications") as NSString).expandingTildeInPath]
        let exact = folders.map { URL(fileURLWithPath: "\($0)/\(target).app") }
            .first { FileManager.default.fileExists(atPath: $0.path) }
        let named: URL? = exact ?? folders.lazy.compactMap { dir -> URL? in
            let apps = (try? FileManager.default.contentsOfDirectory(atPath: dir))?.filter { $0.hasSuffix(".app") } ?? []
            let want = target.lowercased()
            let hit = apps.sorted { $0.count < $1.count }.first { $0.lowercased().hasPrefix(want + " ") }
                ?? apps.sorted { $0.count < $1.count }.first { $0.lowercased().contains(want) }
            return hit.map { URL(fileURLWithPath: "\(dir)/\($0)") }
        }.first
        let appURL = ws.urlForApplication(withBundleIdentifier: target) ?? named
        guard let appURL else { return fail("No app, file or link called \(target).") }
        ws.openApplication(at: appURL, configuration: cfg, completionHandler: done)
    }

    /// Wispr Flow's hands-free mode, started from the composer's mic. Only these two links, and Flow stays in the
    /// background so it types into Taffy's message box.
    private func flow(_ args: [String: Any], _ ok: @escaping (Any) -> Void, _ fail: @escaping (String) -> Void) {
        let start = (args["action"] as? String) != "stop"
        guard let url = URL(string: start ? "wispr-flow://start-hands-free" : "wispr-flow://stop-hands-free") else { return }
        guard NSWorkspace.shared.urlForApplication(toOpen: url) != nil else {
            return fail("Wispr Flow isn't installed on this Mac.")
        }
        let cfg = NSWorkspace.OpenConfiguration()
        cfg.activates = false
        NSWorkspace.shared.open(url, configuration: cfg) { _, err in
            DispatchQueue.main.async { err == nil ? ok(true) : fail("Wispr Flow didn't start. Try again.") }
        }
    }

    /// Why a file, app or link must not be opened for a bot: Taffy itself, apps that run files, and files that
    /// run something when opened (these would run outside the command sandbox).
    private func openRefusal(_ target: String) -> String? {
        let lower = target.lowercased()
        if let url = URL(string: target), let scheme = url.scheme?.lowercased(), scheme.count > 1 {
            if scheme == "file" { return openRefusal(url.path) }
            // System Settings links only show a pane; changing anything there still takes a press the bot has to make.
            if !["http", "https", "maps", "x-apple.systempreferences"].contains(scheme) {   // no mailto, tel or facetime: those reach people
                return "Links that open other apps (\(scheme):) are not opened for bots."
            }
            return nil
        }
        let path = (target as NSString).expandingTildeInPath
        let ext = (path as NSString).pathExtension.lowercased()
        if Self.runnable.contains(ext) { return "Files that run something when opened (.\(ext)) are never opened for bots." }
        var isDir: ObjCBool = false
        if FileManager.default.fileExists(atPath: path, isDirectory: &isDir) {
            if !isDir.boolValue && FileManager.default.isExecutableFile(atPath: path) {
                return "That file is a program. Bots run commands with mac_run instead."
            }
            return nil
        }
        // An app by name or bundle id.
        let ws = NSWorkspace.shared
        let appURL = ws.urlForApplication(withBundleIdentifier: target)
            ?? ["/Applications", "/System/Applications", "/Applications/Utilities", "/System/Applications/Utilities"]
                .map { URL(fileURLWithPath: "\($0)/\(target).app") }
                .first { FileManager.default.fileExists(atPath: $0.path) }
        if let appURL, let id = Bundle(url: appURL)?.bundleIdentifier {
            if id == Bundle.main.bundleIdentifier { return "Zips never work in Zero itself." }
            if Self.launchers.contains(id) || Self.shells.contains(id) {
                return "\(appURL.deletingPathExtension().lastPathComponent) can run commands, so it is never opened for bots. Use mac_run."
            }
        }
        if lower.contains("taffy") { return "Zips never work in Zero itself." }
        return nil
    }

    private func run(_ args: [String: Any], _ ok: (Any) -> Void, _ fail: (String) -> Void) {
        let command = args["command"] as? String ?? ""
        guard !command.isEmpty else { return fail("No command.") }
        let timeout = min(max(args["timeout"] as? Double ?? 120, 1), 600)
        let p = Process()
        // Commands run inside a sandbox: they can read and work in Colin's folders, but they cannot touch keys,
        // passwords, browser data, messages or Taffy's own sign-in, and they can only change files in his work
        // folders, temporary folders and caches. The hub's command guard is a first check; this is the wall.
        p.executableURL = URL(fileURLWithPath: "/usr/bin/sandbox-exec")
        p.arguments = ["-p", Self.sandboxProfile, "/bin/zsh", "-lc", command]
        p.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
        let out = Pipe(), err = Pipe()
        p.standardOutput = out
        p.standardError = err
        p.standardInput = FileHandle.nullDevice
        var outData = Data(), errData = Data()
        let lock = NSLock()
        out.fileHandleForReading.readabilityHandler = { h in lock.lock(); outData.append(h.availableData); lock.unlock() }
        err.fileHandleForReading.readabilityHandler = { h in lock.lock(); errData.append(h.availableData); lock.unlock() }
        do { try p.run() } catch { return fail("Could not start the command: \(error.localizedDescription)") }
        runningLock.lock(); running.append(p); runningLock.unlock()
        defer { runningLock.lock(); running.removeAll { $0 === p }; runningLock.unlock() }
        let deadline = Date().addingTimeInterval(timeout)
        var stopped = false
        while p.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
            var live = true
            DispatchQueue.main.sync { live = self.grantUntil != nil && self.grantUntil! > Date() }
            if !live { stopped = true; break }   // Stop or the end of the grant ends the command too
        }
        let timedOut = p.isRunning && !stopped
        if p.isRunning { p.terminate(); Thread.sleep(forTimeInterval: 0.3); if p.isRunning { kill(p.processIdentifier, SIGKILL) } }
        p.waitUntilExit()
        out.fileHandleForReading.readabilityHandler = nil
        err.fileHandleForReading.readabilityHandler = nil
        lock.lock(); defer { lock.unlock() }
        func cap(_ d: Data) -> String {
            let s = String(decoding: d, as: UTF8.self)
            return s.count > 20_000 ? String(s.prefix(20_000)) + "\n[cut at 20,000 characters]" : s
        }
        if stopped { return fail("\(who)'s go-ahead ended while the command ran, so it was stopped.") }
        var result: [String: Any] = ["exit_code": Int(p.terminationStatus), "stdout": cap(outData),
                                     "stderr": cap(errData), "timed_out": timedOut]
        let blocked = Self.foldersMacOSBlocks()
        if !blocked.isEmpty {
            // macOS hides these folders from Taffy until Colin allows it, and a search there comes back empty, which
            // reads as "not on the Mac" (Oct 10: Dana's site in Documents was never found).
            result["folders_macos_blocks"] = blocked
            offerGuide(.files)
            result["note"] = "macOS is not letting Taffy into " + blocked.joined(separator: ", ")
                + ", so anything there was not seen. \(who) can allow it in System Settings, Privacy & Security, "
                + "Files and Folders (or Full Disk Access) for Zero. A setup guide for it is open on the Mac."
        }
        ok(result)
    }

    /// The protected folders macOS will not let Taffy (and so its commands) read right now. Asking also brings up
    /// macOS's own "allow access" prompt the first time, so Colin can allow it then and there.
    private static func foldersMacOSBlocks() -> [String] {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let folders = [("Documents", "Documents"), ("Desktop", "Desktop"), ("Downloads", "Downloads"),
                       ("iCloud Drive", "Library/Mobile Documents/com~apple~CloudDocs")]
        return folders.compactMap { name, rel in
            let path = "\(home)/\(rel)"
            guard FileManager.default.fileExists(atPath: path) else { return nil }
            return (try? FileManager.default.contentsOfDirectory(atPath: path)) == nil ? name : nil
        }
    }

    private static let sandboxProfile: String = {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        func paths(_ list: [String]) -> String { list.map { "(subpath \"\(home)/\($0)\")" }.joined(separator: " ") }
        func esc(_ s: String) -> String { NSRegularExpression.escapedPattern(for: s) }
        // Bots change files only in Downloads, the Desktop and their own work folder, plus caches and temp.
        let writable = ["Downloads", "Desktop", "Taffy Work", "Library/Caches", ".cache", ".npm", ".local/state"]
        // Dotfiles that shells and common tools need to start; every other dotfile and dot-folder is unreadable.
        let dotAllowed = [".zshrc", ".zprofile", ".zshenv", ".zlogin", ".bashrc", ".bash_profile", ".profile",
                          ".gitconfig", ".gitignore_global", ".cache", ".npm", ".local/state", ".local/share/mise",
                          ".cargo/bin", ".rustup", ".nvm", ".pyenv", ".bun/bin", ".deno/bin", ".CFUserTextEncoding",
                          ".Trash", ".config/git"]
        let privateLibrary = ["Library/Application Support", "Library/Group Containers", "Library/Containers",
                              "Library/Keychains", "Library/Cookies", "Library/Messages", "Library/Mail",
                              "Library/Safari", "Library/Accounts", "Library/Calendars",
                              "Library/HTTPStorages", "Library/WebKit", "Library/IdentityServices", "Library/Suggestions"]
        // In these profiles the last matching rule wins.
        return """
        (version 1)
        (allow default)
        (deny file-write* (subpath "\(home)") (subpath "/Applications") (subpath "/opt/homebrew") (subpath "/usr/local")
            (subpath "/Library") (subpath "/opt"))
        (allow file-write* \(paths(writable)))
        (deny file-write* (regex #"/\\.git/hooks(/|$)") (regex #"/\\.(claude|vscode|husky|cursor|codex)(/|$)"))
        (deny file-read* (regex #"^\(esc(home))/\\.[^/]+"))
        (allow file-read* \(dotAllowed.map { "(subpath \"\(home)/\($0)\")" }.joined(separator: " ")))
        (deny file-read* file-write* \(paths(privateLibrary)) (subpath "/Library/Keychains"))
        (deny file-write* (subpath "\(home)/Library/Mobile Documents"))
        (deny appleevent-send)
        (deny process-exec (literal "/usr/bin/open") (literal "/usr/bin/osascript") (literal "/usr/bin/shortcuts")
            (literal "/bin/launchctl") (literal "/usr/bin/automator") (literal "/usr/bin/security")
            (literal "/usr/sbin/installer") (literal "/usr/bin/sudo") (literal "/usr/bin/su"))
        (deny mach-lookup (global-name "com.apple.coreservices.launchservicesd"))
        """
    }()

    // MARK: Seeing the screen

    private func screenshot(_ ok: @escaping (Any) -> Void, _ fail: @escaping (String) -> Void) async {
        guard CGPreflightScreenCaptureAccess() else {
            offerGuide(.screen)
            return fail("Zero needs Screen Recording permission on the Mac. A setup guide is open on the Mac; \(who) can "
                        + "finish it in about a minute. Use mac_ui, which needs no screen picture, meanwhile.")
        }
        do {
            let content = try await SCShareableContent.current
            guard let display = content.displays.first(where: { $0.displayID == CGMainDisplayID() }) ?? content.displays.first else {
                return fail("No display found.")
            }
            let config = SCStreamConfiguration()
            let scale = min(1, 1440 / CGFloat(display.width))
            config.width = Int(CGFloat(display.width) * scale)
            config.height = Int(CGFloat(display.height) * scale)
            config.showsCursor = true
            // The bot's cursor and the in-use bar are Taffy's own windows; leave them out of what it sees.
            let mine = content.windows.filter { $0.owningApplication?.processID == getpid() }
            let filter = SCContentFilter(display: display, excludingWindows: mine)
            let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
            let rep = NSBitmapImageRep(cgImage: image)
            guard let jpeg = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.7]) else {
                return fail("Could not encode the screenshot.")
            }
            ok(["jpeg_base64": jpeg.base64EncodedString(), "image_width": config.width, "image_height": config.height,
                "screen_width": display.width, "screen_height": display.height,
                "note": "Click coordinates are in screen points: multiply image pixels by screen_width / image_width."])
        } catch {
            fail("Screenshot failed: \(error.localizedDescription)")
        }
    }

    func needAccessibility(_ fail: (String) -> Void) -> Bool {
        if AXIsProcessTrusted() { return false }
        offerGuide(.accessibility)   // Zero's own step-by-step guide, instead of macOS's bare prompt
        fail("Zero needs Accessibility permission on the Mac. A setup guide is open on the Mac; \(who) can finish it "
             + "in about a minute, then ask again.")
        return true
    }

    /// Why the bot must not work in this app: Taffy itself (its own approvals and Colin's message box), or an
    /// app where typing runs commands.
    func appRefusal(_ pid: pid_t) -> String? {
        if pid == getpid() { return "Zips never work in Zero itself. Open the app to work in with mac_open first." }
        return nil
    }

    // MARK: Acting

    /// Clicks without moving Colin's mouse: the click goes straight to the app under that point, and the
    /// bot's own cursor shows where. With real set, it uses the real mouse (only after Colin agrees).
    private func click(_ args: [String: Any], _ ok: @escaping (Any) -> Void, _ fail: @escaping (String) -> Void) {
        if needAccessibility(fail) { return }
        guard let x = args["x"] as? Double, let y = args["y"] as? Double else { return fail("Need x and y.") }
        if args["real"] as? Bool ?? false, !realMouseAllowed {
            // The click is not replayed after Colin answers: by then the screen may have changed.
            return askForMouse { yes in
                yes ? fail("\(self.who) allowed the real mouse for this task. Take a fresh look and click again.")
                    : fail("\(self.who) said no to using the real mouse. Press elements by number instead.")
            }
        }
        let p = CGPoint(x: x, y: y)
        let right = (args["button"] as? String) == "right"
        let count = max(1, min(args["count"] as? Int ?? 1, 3))
        let real = args["real"] as? Bool ?? false
        let down: CGEventType = right ? .rightMouseDown : .leftMouseDown
        let up: CGEventType = right ? .rightMouseUp : .leftMouseUp
        let button: CGMouseButton = right ? .right : .left
        moveCursor(to: p, click: true)
        var pid = targetPid
        let system = AXUIElementCreateSystemWide()
        var hit: AXUIElement?
        if AXUIElementCopyElementAtPosition(system, Float(x), Float(y), &hit) == .success, let hit {
            var hp: pid_t = 0
            if AXUIElementGetPid(hit, &hp) == .success {
                if let reason = appRefusal(hp) { return fail(reason) }
                if let app = NSRunningApplication(processIdentifier: hp), let id = app.bundleIdentifier,
                   Self.shells.contains(id) {
                    return fail("\(app.localizedName ?? "That app") can run commands, so bots never click in it.")
                }
                pid = hp; targetPid = hp
            }
        }
        if !real && pid == 0 {
            return fail("There is no app at that point to click in the background. Open the app with mac_open first.")
        }
        // A plain click on a control is pressed through accessibility, which works in apps that ignore clicks sent to
        // them in the background (Oct 10: System Settings ignored every background click). Rows are selected.
        if let hit, let reason = Self.switchRefusal(hit) { return fail(reason) }
        if !real, !right, count == 1, args["to_x"] == nil, let hit, let pressed = Self.pressAt(hit) {
            return ok("Pressed the \(pressed) at (\(Int(x)), \(Int(y))) without the mouse.")
        }
        let window = real ? nil : Self.windowNumber(pid: pid, at: p)
        func post(_ e: CGEvent?) {
            guard let e else { return }
            if real { return e.post(tap: .cghidEventTap) }
            // Name the window under the point, as the window server does for a real click, so a background app
            // routes the click to it (the event fields cua-driver stamps; public CoreGraphics fields).
            if let window {
                e.setIntegerValueField(.mouseEventWindowUnderMousePointer, value: Int64(window))
                e.setIntegerValueField(.mouseEventWindowUnderMousePointerThatCanHandleThisEvent, value: Int64(window))
            }
            e.postToPid(pid)
        }
        if !real {   // a move first, so the app's view knows where the pointer is before the press arrives
            post(CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: p, mouseButton: button))
            Thread.sleep(forTimeInterval: 0.012)
        }
        if let tx = args["to_x"] as? Double, let ty = args["to_y"] as? Double {
            let q = CGPoint(x: tx, y: ty)
            post(CGEvent(mouseEventSource: nil, mouseType: down, mouseCursorPosition: p, mouseButton: button))
            moveCursor(to: q, click: false)
            post(CGEvent(mouseEventSource: nil, mouseType: .leftMouseDragged, mouseCursorPosition: q, mouseButton: button))
            post(CGEvent(mouseEventSource: nil, mouseType: up, mouseCursorPosition: q, mouseButton: button))
            return ok("Dragged from (\(Int(x)), \(Int(y))) to (\(Int(tx)), \(Int(ty))).")
        }
        if real { CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: p, mouseButton: button)?.post(tap: .cghidEventTap) }
        for i in 1...count {
            let d = CGEvent(mouseEventSource: nil, mouseType: down, mouseCursorPosition: p, mouseButton: button)
            let u = CGEvent(mouseEventSource: nil, mouseType: up, mouseCursorPosition: p, mouseButton: button)
            d?.setIntegerValueField(.mouseEventClickState, value: Int64(i))
            u?.setIntegerValueField(.mouseEventClickState, value: Int64(i))
            post(d)
            Thread.sleep(forTimeInterval: 0.028)   // an AppKit button waits for the release; too quick and it misses it
            post(u)
        }
        ok(real ? "Clicked at (\(Int(x)), \(Int(y))) with the real mouse."
                : "Clicked at (\(Int(x)), \(Int(y))) in the background. If nothing changed, this app ignores background "
                  + "clicks: press the element by number instead, or ask \(who) before using the real mouse (real: true).")
    }

    func centerOf(_ e: AXUIElement) -> CGPoint? {
        var pv: AnyObject?, sv: AnyObject?
        guard AXUIElementCopyAttributeValue(e, kAXPositionAttribute as CFString, &pv) == .success,
              AXUIElementCopyAttributeValue(e, kAXSizeAttribute as CFString, &sv) == .success else { return nil }
        var p = CGPoint.zero, s = CGSize.zero
        AXValueGetValue(pv as! AXValue, .cgPoint, &p)
        AXValueGetValue(sv as! AXValue, .cgSize, &s)
        return CGPoint(x: p.x + s.width / 2, y: p.y + s.height / 2)
    }

    /// The focused element of the app the bot is typing into is a password field: bots never type there.
    func focusIsSecure() -> Bool {
        let app = AXUIElementCreateApplication(targetPid)
        var focused: AnyObject?
        guard AXUIElementCopyAttributeValue(app, kAXFocusedUIElementAttribute as CFString, &focused) == .success,
              let el = focused else { return false }
        var role: AnyObject?
        AXUIElementCopyAttributeValue(el as! AXUIElement, kAXRoleAttribute as CFString, &role)
        var sub: AnyObject?
        AXUIElementCopyAttributeValue(el as! AXUIElement, kAXSubroleAttribute as CFString, &sub)
        return (role as? String) == "AXSecureTextField" || (sub as? String) == "AXSecureTextField"
    }

    /// Why typing into the bot's current app is not allowed, if it is not.
    func typingRefusal() -> String? {
        guard targetPid != 0, let app = NSRunningApplication(processIdentifier: targetPid) else {
            return "No app is chosen to type into. Open it with mac_open or click into it first."
        }
        if let reason = appRefusal(targetPid) { return reason }
        if let id = app.bundleIdentifier, Self.shells.contains(id) {
            return "Typing into \(app.localizedName ?? "that app") would run commands. Use mac_run instead."
        }
        return nil
    }

    func type(_ args: [String: Any], _ ok: (Any) -> Void, _ fail: (String) -> Void) {
        if needAccessibility(fail) { return }
        let text = args["text"] as? String ?? ""
        guard !text.isEmpty else { return fail("No text.") }
        if let reason = typingRefusal() { return fail(reason) }
        if focusIsSecure() { return fail("That is a password field. Bots never type passwords; use the saved-logins flow.") }
        let pid = targetPid
        for chunk in stride(from: 0, to: text.utf16.count, by: 20).map({ start -> [UniChar] in
            Array(text.utf16)[start..<min(start + 20, text.utf16.count)].map { $0 }
        }) {
            var chars = chunk
            let d = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true)
            let u = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false)
            d?.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: &chars)
            u?.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: &chars)
            d?.postToPid(pid); u?.postToPid(pid)
            Thread.sleep(forTimeInterval: 0.01)
        }
        ok("Typed \(text.count) characters.")
    }

    static let keyCodes: [String: CGKeyCode] = [
        "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51, "escape": 53, "esc": 53,
        "left": 123, "right": 124, "down": 125, "up": 126, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
        "forwarddelete": 117, "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96,
        "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5, "h": 4, "i": 34, "j": 38, "k": 40, "l": 37, "m": 46,
        "n": 45, "o": 31, "p": 35, "q": 12, "r": 15, "s": 1, "t": 17, "u": 32, "v": 9, "w": 13, "x": 7, "y": 16, "z": 6,
        "0": 29, "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22, "7": 26, "8": 28, "9": 25,
        ",": 43, ".": 47, "/": 44, ";": 41, "'": 39, "[": 33, "]": 30, "-": 27, "=": 24, "`": 50,
    ]

    func key(_ args: [String: Any], _ ok: (Any) -> Void, _ fail: (String) -> Void) {
        if needAccessibility(fail) { return }
        let combo = (args["keys"] as? String ?? "").lowercased().replacingOccurrences(of: " ", with: "")
        var flags: CGEventFlags = []
        var keyName = ""
        for part in combo.split(separator: "+").map(String.init) {
            switch part {
            case "cmd", "command": flags.insert(.maskCommand)
            case "shift": flags.insert(.maskShift)
            case "opt", "option", "alt": flags.insert(.maskAlternate)
            case "ctrl", "control": flags.insert(.maskControl)
            default: keyName = part
            }
        }
        guard let code = Self.keyCodes[keyName] else { return fail("Unknown key \"\(keyName)\".") }
        if let reason = typingRefusal() { return fail(reason) }
        let d = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true)
        let u = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false)
        d?.flags = flags
        u?.flags = flags
        d?.postToPid(targetPid); u?.postToPid(targetPid)
        ok("Pressed \(combo).")
    }

    // MARK: Approval and the in-use bar

    private func panel(width: CGFloat, height: CGFloat) -> NSPanel {
        let p = NSPanel(contentRect: NSRect(x: 0, y: 0, width: width, height: height),
                        styleMask: [.nonactivatingPanel, .titled, .fullSizeContentView], backing: .buffered, defer: false)
        p.level = .statusBar
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        p.titleVisibility = .hidden
        p.titlebarAppearsTransparent = true
        p.isMovableByWindowBackground = true
        p.hidesOnDeactivate = false
        p.standardWindowButton(.closeButton)?.isHidden = true
        p.standardWindowButton(.miniaturizeButton)?.isHidden = true
        p.standardWindowButton(.zoomButton)?.isHidden = true
        return p
    }

    private func label(_ s: String, size: CGFloat, weight: NSFont.Weight = .regular, color: NSColor = .labelColor) -> NSTextField {
        let l = NSTextField(wrappingLabelWithString: s)
        l.preferredMaxLayoutWidth = 380   // long quotes wrap instead of stretching the card across the screen
        l.font = .systemFont(ofSize: size, weight: weight)
        l.textColor = color
        return l
    }

    private func showApproval(_ args: [String: Any], reply: @escaping (Bool, Bool) -> Void) {
        dismissApproval(false)
        let bot = args["bot"] as? String ?? "A bot"
        let task = args["task"] as? String ?? ""
        // "person_said" from hubs that know the person's name; "colin_said" from hubs before homes.
        let said = args["person_said"] as? String ?? args["colin_said"] as? String ?? ""
        answeredVia = "mac"
        if let id = args["approval_id"] as? String {
            pendingApproval = (id, bot, task, said, (args["expires"] as? Int) ?? Int(Date().timeIntervalSince1970 + Self.approvalSeconds))
        }
        let p = panel(width: 420, height: 230)
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 10
        stack.edgeInsets = NSEdgeInsets(top: 22, left: 22, bottom: 18, right: 22)
        stack.addArrangedSubview(label("\(bot) wants to use your Mac", size: 15, weight: .semibold))
        stack.addArrangedSubview(label(task, size: 13))
        if !said.isEmpty {
            var when = "You asked"
            if let at = args["said_at"] as? Double {
                let f = DateFormatter()
                f.timeStyle = .short
                f.dateStyle = Calendar.current.isDateInToday(Date(timeIntervalSince1970: at)) ? .none : .short
                when = "You said at \(f.string(from: Date(timeIntervalSince1970: at)))"
            }
            stack.addArrangedSubview(label("\(when): \u{201C}\(said)\u{201D}", size: 12, color: .secondaryLabelColor))
        }
        stack.addArrangedSubview(label("Until the task ends, 30 minutes pass, or you press Stop, \(bot) can open things, run commands, see the screen, click and type.",
                                       size: 11, color: .secondaryLabelColor))
        let deny = NSButton(title: "Deny", target: self, action: #selector(denyPressed))
        deny.keyEquivalent = "\u{1b}"   // Escape denies; nothing on the keyboard allows, so a stray Return cannot
        let allow = NSButton(title: "Allow", target: self, action: #selector(allowPressed))
        let buttons = NSStackView(views: [deny, allow])
        buttons.spacing = 10
        stack.addArrangedSubview(buttons)
        p.contentView = stack
        p.setContentSize(stack.fittingSize)
        if let screen = NSScreen.main {
            let f = screen.visibleFrame
            p.setFrameOrigin(NSPoint(x: f.midX - p.frame.width / 2, y: f.maxY - p.frame.height - 40))
        }
        approval = p
        approvalReply = reply
        p.orderFrontRegardless()
        NSApp.requestUserAttention(.criticalRequest)
        // An unanswered card closes itself before the hub stops waiting, so a late Allow never lands nowhere.
        approvalTimer = Timer.scheduledTimer(withTimeInterval: Self.approvalSeconds, repeats: false) { [weak self] _ in
            self?.dismissApproval(false, timedOut: true)
        }
        pendingBot = bot
    }

    private var pendingBot = "Taffy"

    @objc func allowPressed() {
        guideOfferedThisGrant = false
        // The grant lives here first, and the bar with its Stop button shows at the same moment.
        grantUntil = Date().addingTimeInterval(Self.grantSeconds)
        realMouseAllowed = false
        showBar(["bot": pendingBot])
        dismissApproval(true)
    }
    @objc private func denyPressed() { dismissApproval(false) }

    func dismissApproval(_ allowed: Bool, timedOut: Bool = false) {
        pendingApproval = nil
        approvalTimer?.invalidate()
        approvalTimer = nil
        approval?.orderOut(nil)
        approval = nil
        let r = approvalReply
        approvalReply = nil
        r?(allowed, timedOut)
    }

    private func endGrant() {
        grantUntil = nil
        runningLock.lock()
        for p in running where p.isRunning { p.terminate() }
        runningLock.unlock()
        realMouseAllowed = false
        hideBar()
        hideCursor()
        dismissMousePanel(false)
        targetPid = 0
    }

    private func showBar(_ args: [String: Any]) {
        hideBar()
        let bot = args["bot"] as? String ?? "Taffy"
        botName = bot
        let p = panel(width: 300, height: 44)
        let text = label("\(bot) is using your Mac", size: 13, weight: .medium)
        let stop = NSButton(title: "Stop", target: self, action: #selector(stopPressed))
        stop.bezelColor = .systemRed
        let row = NSStackView(views: [text, stop])
        row.spacing = 14
        row.edgeInsets = NSEdgeInsets(top: 10, left: 16, bottom: 10, right: 12)
        p.contentView = row
        p.setContentSize(row.fittingSize)
        if let screen = NSScreen.main {
            let f = screen.visibleFrame
            p.setFrameOrigin(NSPoint(x: f.maxX - p.frame.width - 16, y: f.maxY - p.frame.height - 12))
        }
        bar = p
        p.orderFrontRegardless()
    }

    private func hideBar() {
        bar?.orderOut(nil)
        bar = nil
    }

    @objc private func stopPressed() {
        endGrant()
        stopHandler?()
    }

    /// Asks on the Mac itself before the bot's first use of Colin's real mouse in a grant.
    private func askForMouse(_ reply: @escaping (Bool) -> Void) {
        if realMouseAllowed { return reply(true) }
        dismissMousePanel(false)
        let p = panel(width: 360, height: 140)
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 10
        stack.edgeInsets = NSEdgeInsets(top: 20, left: 20, bottom: 16, right: 20)
        stack.addArrangedSubview(label("\(botName) wants to use your mouse", size: 14, weight: .semibold))
        stack.addArrangedSubview(label("This app ignores \(botName)'s own cursor. Your pointer will move while it clicks.",
                                       size: 12, color: .secondaryLabelColor))
        let deny = NSButton(title: "Deny", target: self, action: #selector(mouseDenied))
        deny.keyEquivalent = "\u{1b}"
        let allow = NSButton(title: "Allow", target: self, action: #selector(mouseAllowed))
        let buttons = NSStackView(views: [deny, allow])
        buttons.spacing = 10
        stack.addArrangedSubview(buttons)
        p.contentView = stack
        p.setContentSize(stack.fittingSize)
        if let screen = NSScreen.main {
            let f = screen.visibleFrame
            p.setFrameOrigin(NSPoint(x: f.midX - p.frame.width / 2, y: f.maxY - p.frame.height - 40))
        }
        mousePanel = p
        mouseReply = reply
        p.orderFrontRegardless()
        DispatchQueue.main.asyncAfter(deadline: .now() + 25) { [weak self, weak p] in   // under the hub's 30 s wait
            if let p, self?.mousePanel === p { self?.dismissMousePanel(false) }
        }
    }

    @objc private func mouseAllowed() { realMouseAllowed = true; dismissMousePanel(true) }
    @objc private func mouseDenied() { dismissMousePanel(false) }

    private func dismissMousePanel(_ allowed: Bool) {
        mousePanel?.orderOut(nil)
        mousePanel = nil
        let r = mouseReply
        mouseReply = nil
        r?(allowed)
    }

    // MARK: The bot's own cursor

    private final class CursorView: NSView {
        var name = "Taffy"
        var pulse: CGFloat = 0

        override func draw(_ dirtyRect: NSRect) {
            let orange = NSColor(red: 0xF2 / 255, green: 0x71 / 255, blue: 0x1C / 255, alpha: 1)
            if pulse > 0 {
                orange.withAlphaComponent(0.25 * pulse).setFill()
                NSBezierPath(ovalIn: NSRect(x: 2 - 10 * pulse, y: bounds.height - 22 - 10 * pulse,
                                            width: 20 + 20 * pulse, height: 20 + 20 * pulse)).fill()
            }
            // An arrow pointer with its tip at the top-left corner of the view.
            let h = bounds.height
            let arrow = NSBezierPath()
            arrow.move(to: NSPoint(x: 4, y: h - 2))
            arrow.line(to: NSPoint(x: 4, y: h - 24))
            arrow.line(to: NSPoint(x: 10, y: h - 18.5))
            arrow.line(to: NSPoint(x: 14.5, y: h - 27))
            arrow.line(to: NSPoint(x: 18, y: h - 25.5))
            arrow.line(to: NSPoint(x: 13.5, y: h - 17))
            arrow.line(to: NSPoint(x: 21, y: h - 17))
            arrow.close()
            orange.setFill()
            arrow.fill()
            NSColor.white.setStroke()
            arrow.lineWidth = 1.5
            arrow.stroke()
            let attrs: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 11, weight: .semibold),
                                                        .foregroundColor: NSColor.white]
            let size = (name as NSString).size(withAttributes: attrs)
            let tag = NSRect(x: 20, y: h - 44, width: size.width + 14, height: 18)
            orange.setFill()
            NSBezierPath(roundedRect: tag, xRadius: 9, yRadius: 9).fill()
            (name as NSString).draw(at: NSPoint(x: tag.minX + 7, y: tag.minY + 2), withAttributes: attrs)
        }
    }

    /// Glides the bot's cursor to a point in screen coordinates (top-left origin, like the clicks).
    func moveCursor(to p: CGPoint, click: Bool) {
        let size = NSSize(width: 160, height: 48)
        if cursor == nil {
            let c = NSPanel(contentRect: NSRect(origin: .zero, size: size), styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
            c.isOpaque = false
            c.backgroundColor = .clear
            c.hasShadow = false
            c.ignoresMouseEvents = true
            c.level = NSWindow.Level(rawValue: NSWindow.Level.statusBar.rawValue + 1)
            c.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
            c.contentView = CursorView(frame: NSRect(origin: .zero, size: size))
            cursor = c
        }
        guard let c = cursor, let view = c.contentView as? CursorView else { return }
        view.name = botName
        let screenHeight = NSScreen.screens.first?.frame.height ?? 0
        let origin = NSPoint(x: p.x - 4, y: screenHeight - p.y - size.height + 2)
        if !c.isVisible {
            c.setFrameOrigin(origin)
            c.orderFrontRegardless()
        } else {
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = 0.35
                ctx.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
                c.animator().setFrameOrigin(origin)
            }
        }
        view.needsDisplay = true
        guard click else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
            var t = 0.0
            Timer.scheduledTimer(withTimeInterval: 1 / 30, repeats: true) { timer in
                t += 1 / 30
                view.pulse = CGFloat(max(0, 1 - t / 0.4))
                view.needsDisplay = true
                if t >= 0.4 { timer.invalidate() }
            }
        }
    }

    private func hideCursor() {
        cursor?.orderOut(nil)
        cursor = nil
    }
}
