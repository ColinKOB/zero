import AppKit
import ApplicationServices

/// Zero's guided setup, drawn over the person's own browser.
///
/// Google blocks signing in to Google inside apps, so the guide does not bring its own browser. It opens the
/// page in the person's browser, where they are usually signed in already, and lays two panels over that
/// window: a click-through layer that dims everything except the one thing to click, and a callout card that
/// says where to click and why. Targets are found in the browser's accessibility tree by their visible words
/// (the same tree VoiceOver reads), so nothing is injected into the page and nothing is clicked for the person.
///
/// Without Accessibility permission the guide still runs as a card beside the browser, with the steps in words.
///
/// The key reaches the app only through the `emit` block given to `start`. It is never logged, never put in
/// an event the hub can see (`handle:reply:` cannot reach this), and only looked for on AI Studio's pages.
@MainActor
final class VoiceGuide {
    static let shared = VoiceGuide()

    typealias Emit = ([String: Any]) -> Void

    /// For the test harness: called after each callout change with the step, its status and the target.
    var debugHook: ((GuideStep, CalloutContent.Status, AXUIElement?) -> Void)?
    /// For the test harness: the pasteboard is only read after a Copy click in a real run; tests can allow it.
    var running: Bool { script != nil }

    private var script: GuideScript?
    private var emit: Emit?
    private var pid: pid_t = 0
    private var browserURL: URL?
    private var index = 0
    private var cardMode = false
    /// For the test harness: behave as if Accessibility were off, to see the card the person gets then.
    var forceCardMode = false
    /// Set by Back: that step neither skips itself nor finishes until its condition changes or Next is pressed.
    private var held: (index: Int, done: Bool)?
    private var stepShownAt = Date()
    private var scrolledFor = -1
    private var keyDelivered = false
    private var paused = false
    private var pasteboardCount = NSPasteboard.general.changeCount
    /// When I Copied It last found no key on the clipboard, to say so for a few seconds.
    private var noKeyAt: Date?
    /// When the browser was first seen with no window, to tell Zero's window once it has stayed that way.
    private var windowMissingSince: Date?
    private var toldNoWindow = false

    private var snapshot: Snapshot?
    private var target: Node?
    private var snapshotBusy = false
    private var frameBusy = false
    private let axQueue = DispatchQueue(label: "zero.guide.ax", qos: .userInitiated)
    private let frameQueue = DispatchQueue(label: "zero.guide.frame", qos: .userInteractive)
    private var timers: [Timer] = []

    private var content = CalloutContent(title: "", body: "", lookFor: "")
    private var dimPanel: NSPanel?
    private var calloutPanel: NSPanel?
    private let dimView = DimView(frame: .zero)
    private let calloutView = CalloutView(frame: .zero)

    /// For the test harness: the browser window and the callout, in accessibility coordinates (top left).
    var debugWindowFrame: CGRect? { snapshot?.window }
    var debugCalloutFrame: CGRect? {
        guard let p = calloutPanel, p.isVisible else { return nil }
        return CGRect(x: p.frame.minX, y: primaryHeight - p.frame.maxY, width: p.frame.width, height: p.frame.height)
    }
    /// For calibrating the script against the live site: every element's role and words, keys blanked out.
    var debugPageText: String {
        guard let snap = snapshot else { return "" }
        let lines = snap.nodes.filter { !$0.name.isEmpty || $0.role == "AXCheckBox" }.map { n -> String in
            var name = n.name
            while let k = GeminiKeyPattern.find(in: name) { name = name.replacingOccurrences(of: k, with: "[key]") }
            return "\(n.role)\t\(n.domID ?? "")\t\(name.replacingOccurrences(of: "\n", with: " "))"
        }
        return snap.url + "\n" + lines.joined(separator: "\n")
    }

    /// For the test harness: presses the callout's Next (I Copied It on the last step).
    func debugPressNext() { next() }

    /// For the test harness: opens or closes the I'm Stuck list.
    func debugSetStuck(_ open: Bool) { calloutView.setStuck(open) }

    // MARK: Start and stop

    /// Opens `script.startURL` in the default browser (or uses `browserPid` if given) and starts guiding.
    func start(script: GuideScript, browserPid: pid_t? = nil, openPage: Bool = true, browserApp: URL? = nil,
               emit: @escaping Emit) {
        stop(silently: true)
        browserURL = browserApp
        self.script = script
        self.emit = emit
        index = 0
        held = nil
        keyDelivered = false
        paused = false
        stepShownAt = Date()
        noKeyAt = nil
        windowMissingSince = nil
        toldNoWindow = false
        pasteboardCount = NSPasteboard.general.changeCount
        cardMode = forceCardMode || !AXIsProcessTrusted()
        if cardMode { emit(["type": "needsAccess"]) }

        if let browserPid {
            attach(pid: browserPid)
            if openPage { openInBrowser(script.startURL) }
        } else {
            openInBrowser(script.startURL)
        }
        timers = [
            Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { _ in MainActor.assumeIsolated { self.takeSnapshot() } },
            Timer.scheduledTimer(withTimeInterval: 1 / 30, repeats: true) { _ in MainActor.assumeIsolated { self.followTarget() } },
            Timer.scheduledTimer(withTimeInterval: 0.35, repeats: true) { _ in MainActor.assumeIsolated { self.watchPasteboard() } },
        ]
        timers.forEach { RunLoop.main.add($0, forMode: .common) }
        publish()
    }

    func stop(silently: Bool = false) {
        timers.forEach { $0.invalidate() }
        timers = []
        dimPanel?.orderOut(nil)
        calloutPanel?.orderOut(nil)
        let wasRunning = script != nil
        script = nil
        snapshot = nil
        target = nil
        if wasRunning && !silently { emit?(["type": "stopped"]) }
        emit = nil
    }

    /// Brings the browser forward and shows the guide again, after the person went back to Zero. If the browser
    /// window was closed, or quit, opens Google's key page again.
    func resume() {
        guard let script else { return }
        paused = false
        let app = NSRunningApplication(processIdentifier: pid)
        let hasWindow = cardMode ? cgWindowFrame() != nil : snapshot?.window != nil
        if app == nil || app?.isTerminated == true || !hasWindow {
            windowMissingSince = nil
            toldNoWindow = false
            openInBrowser(script.startURL)
        } else {
            app?.activate()
        }
        publish()
    }

    private func attach(pid: pid_t) {
        self.pid = pid
        browserURL = NSRunningApplication(processIdentifier: pid)?.bundleURL
        // Chrome and other Chromium browsers build their web accessibility tree only when asked.
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetAttributeValue(app, "AXManualAccessibility" as CFString, kCFBooleanTrue)
    }

    private func openInBrowser(_ url: URL) {
        let appURL = browserURL ?? NSWorkspace.shared.urlForApplication(toOpen: url)
        guard let appURL else { NSWorkspace.shared.open(url); return }
        let cfg = NSWorkspace.OpenConfiguration()
        cfg.activates = true
        NSWorkspace.shared.open([url], withApplicationAt: appURL, configuration: cfg) { app, _ in
            guard let app else { return }
            let p = app.processIdentifier
            Task { @MainActor in if self.pid != p { self.attach(pid: p) } }
        }
    }

    // MARK: Reading the page

    struct Node: @unchecked Sendable {
        let element: AXUIElement
        let role: String
        var name: String
        let frame: CGRect
        let checked: Bool?
        let domID: String?
        let parent: Int
        /// Inside an open dialog, which sits on top of the page: matches there win.
        var inDialog = false
    }

    struct Snapshot: @unchecked Sendable {
        var window: CGRect?
        /// Host, port and path of the page, without the query. Sign-in pages carry the next page's address in
        /// their query ("continue=https://aistudio.google.com/..."), which must not count as being there.
        var url: String = ""
        var nodes: [Node] = []
        var allText = ""
        var keyOnPage: String?
    }

    private func takeSnapshot() {
        guard let script, !snapshotBusy, pid != 0 else { return }
        if NSRunningApplication(processIdentifier: pid)?.isTerminated ?? true {
            emit?(["type": "lost"]); stop(silently: true); return
        }
        if cardMode {
            noteWindow(cgWindowFrame() != nil)
            publish()
            return
        }
        snapshotBusy = true
        let pid = self.pid, keyHosts = script.keyHosts
        axQueue.async {
            let snap = Self.read(pid: pid, keyHosts: keyHosts)
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.snapshotBusy = false
                    self.snapshot = snap
                    self.noteWindow(snap.window != nil)
                    self.advance()
                }
            }
        }
    }

    nonisolated private static func read(pid: pid_t, keyHosts: [String]) -> Snapshot {
        var snap = Snapshot()
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 0.5)
        guard let window = (attr(app, kAXMainWindowAttribute) ?? attr(app, kAXFocusedWindowAttribute)).map({ $0 as! AXUIElement }) else { return snap }
        snap.window = frame(of: window)
        guard let web = findWebArea(in: window) else { return snap }
        var full: URL?
        if let u = attr(web, "AXURL") {
            if CFGetTypeID(u) == CFURLGetTypeID() { full = (u as! URL) }
            else if let s = u as? String { full = URL(string: s) }
        }
        if let full, let host = full.host {
            snap.url = host + (full.port.map { ":\($0)" } ?? "") + full.path
        }
        let onKeyHost = keyHosts.contains { snap.url.contains($0) }
        var nodes: [Node] = []
        var stack: [(AXUIElement, Int)] = [(web, -1)]
        let names = [kAXRoleAttribute, kAXTitleAttribute, kAXDescriptionAttribute, kAXValueAttribute,
                     kAXPositionAttribute, kAXSizeAttribute, kAXChildrenAttribute, "AXDOMIdentifier", kAXSubroleAttribute] as CFArray
        while nodes.count < 4000, let item = stack.popLast() {
            let (el, parent) = item
            var raw: CFArray?
            guard AXUIElementCopyMultipleAttributeValues(el, names, AXCopyMultipleAttributeOptions(rawValue: 0), &raw) == .success,
                  let vals = raw as? [AnyObject], vals.count == 9 else {
                // Keep going below an element that wouldn't answer, so one odd node can't hide a whole dialog.
                for k in ((attr(el, kAXChildrenAttribute) as? [AXUIElement]) ?? []).reversed() { stack.append((k, parent)) }
                continue
            }
            let role = (vals[0] as? String) ?? ""
            let subrole = (vals[8] as? String) ?? ""
            let title = (vals[1] as? String) ?? ""
            let desc = (vals[2] as? String) ?? ""
            var checked: Bool?
            var value = ""
            if let s = vals[3] as? String { value = s }
            else if let n = vals[3] as? NSNumber, role == "AXCheckBox" { checked = n.intValue == 1 }
            var r = CGRect.zero
            if CFGetTypeID(vals[4]) == AXValueGetTypeID(), CFGetTypeID(vals[5]) == AXValueGetTypeID() {
                var p = CGPoint.zero, s = CGSize.zero
                AXValueGetValue(vals[4] as! AXValue, .cgPoint, &p)
                AXValueGetValue(vals[5] as! AXValue, .cgSize, &s)
                r = CGRect(origin: p, size: s)
            }
            // Never read what someone types into a password box.
            if role == "AXSecureTextField" { value = "" }
            let name = [title, desc, value].first { !$0.trimmingCharacters(in: .whitespaces).isEmpty } ?? ""
            let me = nodes.count
            let inDialog = subrole == "AXApplicationDialog" || role == "AXSheet" || (parent >= 0 && nodes[parent].inDialog)
            nodes.append(Node(element: el, role: role, name: name, frame: r, checked: checked,
                              domID: vals[7] as? String, parent: parent, inDialog: inDialog))
            if onKeyHost, snap.keyOnPage == nil, ["AXStaticText", "AXTextField", "AXTextArea", "AXCell"].contains(role) {
                snap.keyOnPage = GeminiKeyPattern.find(in: value.isEmpty ? name : value)
            }
            // Tables list each cell under its row and again under its column; the rows are enough.
            if role != "AXColumn", let kids = vals[6] as? [AXUIElement] {
                for k in kids.reversed() { stack.append((k, me)) }
            }
        }
        // Buttons and links often carry their words in child text; give them that name.
        let named: Set<String> = ["AXButton", "AXLink", "AXCheckBox", "AXMenuItem", "AXTab", "AXRadioButton", "AXPopUpButton"]
        for i in nodes.indices where nodes[i].role == "AXStaticText" && !nodes[i].name.isEmpty {
            var p = nodes[i].parent, hops = 0
            while p >= 0, hops < 4 {
                if named.contains(nodes[p].role) {
                    nodes[p].name = nodes[p].name.isEmpty ? nodes[i].name : nodes[p].name
                    break
                }
                p = nodes[p].parent; hops += 1
            }
        }
        // A checkbox without a label takes the words right after it, as a reader would.
        for i in nodes.indices where nodes[i].role == "AXCheckBox" && nodes[i].name.isEmpty {
            if let next = nodes[(i + 1)..<min(nodes.count, i + 8)].first(where: { $0.role == "AXStaticText" && !$0.name.isEmpty }) {
                nodes[i].name = next.name
            }
        }
        snap.nodes = nodes
        snap.allText = GuideText.normalize(nodes.map(\.name).joined(separator: "\n"))
        return snap
    }

    nonisolated private static func attr(_ e: AXUIElement, _ name: String) -> AnyObject? {
        var v: AnyObject?
        return AXUIElementCopyAttributeValue(e, name as CFString, &v) == .success ? v : nil
    }

    nonisolated private static func frame(of e: AXUIElement) -> CGRect? {
        guard let pv = attr(e, kAXPositionAttribute), let sv = attr(e, kAXSizeAttribute) else { return nil }
        var p = CGPoint.zero, s = CGSize.zero
        AXValueGetValue(pv as! AXValue, .cgPoint, &p)
        AXValueGetValue(sv as! AXValue, .cgSize, &s)
        return CGRect(origin: p, size: s)
    }

    nonisolated private static func findWebArea(in root: AXUIElement) -> AXUIElement? {
        var queue: [(AXUIElement, Int)] = [(root, 0)]
        var seen = 0
        while !queue.isEmpty, seen < 600 {
            let (e, depth) = queue.removeFirst()
            seen += 1
            if (attr(e, kAXRoleAttribute) as? String) == "AXWebArea" { return e }
            if depth < 14, let kids = attr(e, kAXChildrenAttribute) as? [AXUIElement] {
                queue.append(contentsOf: kids.map { ($0, depth + 1) })
            }
        }
        return nil
    }

    /// Tells Zero's window once when the browser has had no window for two seconds (the person closed it), and
    /// again when it is back, so the window can offer to open the page again.
    private func noteWindow(_ present: Bool) {
        if present {
            windowMissingSince = nil
            if toldNoWindow {
                toldNoWindow = false
                if let s = script?.steps[index] { emit?(["type": "step", "index": index, "id": s.id, "title": s.title]) }
            }
            return
        }
        let since = windowMissingSince ?? Date()
        windowMissingSince = since
        if !toldNoWindow, Date().timeIntervalSince(since) > 2 {
            toldNoWindow = true
            emit?(["type": "noWindow"])
        }
    }

    // MARK: Deciding the step

    private func resolve(_ targets: [GuideTarget], in snap: Snapshot) -> Node? {
        let usable = { (n: Node) in n.frame.width > 1 && n.frame.height > 1 }
        for t in targets {
            if let id = t.domID, let n = snap.nodes.first(where: { $0.domID == id && usable($0) }) { return n }
            for w in t.text {
                let hits = snap.nodes.filter { (t.roles.isEmpty || t.roles.contains($0.role)) && usable($0)
                    && GuideText.matches($0.name, w, t.match) }
                // An open dialog covers the page, so the copy in the dialog is the one the person can click.
                if let n = hits.first(where: \.inDialog) ?? hits.first { return n }
            }
        }
        return nil
    }

    private func check(_ c: GuideCondition, _ step: GuideStep, _ snap: Snapshot, _ node: Node?) -> Bool {
        switch c {
        case .urlContains(let parts): return parts.contains { snap.url.contains($0) }
        case .textPresent(let words): return words.contains { snap.allText.contains(GuideText.normalize($0)) }
        case .targetPresent: return node != nil
        case .present(let t): return resolve([t], in: snap) != nil
        case .targetChecked: return node?.checked == true
        case .keyFound: return snap.keyOnPage != nil
        case .not(let inner): return !check(inner, step, snap, node)
        case .all(let list): return list.allSatisfy { check($0, step, snap, node) }
        case .any(let list): return list.contains { check($0, step, snap, node) }
        }
    }

    private func advance() {
        guard let script, let snap = snapshot, !keyDelivered else { return }
        if let key = snap.keyOnPage { deliver(key); return }
        var moved = 0
        while index < script.steps.count, moved < script.steps.count {
            let step = script.steps[index]
            let node = resolve(step.targets, in: snap)
            let done = check(step.doneWhen, step, snap, node)
            if let h = held, h.index == index {
                if done && !h.done { held = nil; go(to: index + 1); moved += 1; continue }
                held = (index, done)
                break
            }
            if let skip = step.skipWhen, check(skip, step, snap, node) { go(to: index + 1); moved += 1; continue }
            if done { go(to: index + 1); moved += 1; continue }
            break
        }
        if index >= script.steps.count { index = script.steps.count - 1 }
        let step = script.steps[index]
        target = resolve(step.targets, in: snap)
        if let t = target, scrolledFor != index, let w = snap.window, !w.intersects(t.frame) || t.frame.maxY > w.maxY {
            scrolledFor = index
            let el = t.element
            axQueue.async { AXUIElementPerformAction(el, "AXScrollToVisible" as CFString) }
        }
        publish()
    }

    private func go(to i: Int) {
        guard let script else { return }
        index = max(0, min(i, script.steps.count - 1))
        stepShownAt = Date()
        target = nil
        let s = script.steps[index]
        emit?(["type": "step", "index": index, "id": s.id, "title": s.title])
    }

    private func status(for step: GuideStep) -> CalloutContent.Status {
        if cardMode { return .pointing }
        // No address yet means the page is still loading, not that the person went somewhere else.
        guard let snap = snapshot, !snap.url.isEmpty else { return .searching }
        if !step.pages.isEmpty, !step.pages.contains(where: { snap.url.contains($0) }) {
            // Pages of other steps count as "on the guide", so a step that just finished doesn't flash.
            let allPages = script?.steps.flatMap(\.pages) ?? []
            return allPages.contains(where: { snap.url.contains($0) }) ? .searching : .offPage
        }
        if target != nil { return .pointing }
        return Date().timeIntervalSince(stepShownAt) < 4 ? .searching : .notFound
    }

    // MARK: The key

    private func watchPasteboard() {
        guard let script, !keyDelivered, !paused else { return }
        let pb = NSPasteboard.general
        guard pb.changeCount != pasteboardCount else { return }
        pasteboardCount = pb.changeCount
        // Only right after a copy in the browser on AI Studio's pages (or anywhere in card mode, where Zero can't
        // see the page). A copy anywhere else is none of Zero's business.
        let browserFront = NSWorkspace.shared.frontmostApplication?.processIdentifier == pid
        let onStudio = script.keyHosts.contains { snapshot?.url.contains($0) ?? false }
        guard browserFront && (onStudio || cardMode) else { return }
        // Peek at the kinds of data first; reading text is what macOS may ask the person about.
        guard pb.types?.contains(.string) == true, let text = pb.string(forType: .string),
              let key = GeminiKeyPattern.find(in: text) else { return }
        deliver(key)
    }

    private func deliver(_ key: String) {
        guard !keyDelivered else { return }
        keyDelivered = true
        dimPanel?.orderOut(nil)
        calloutPanel?.orderOut(nil)
        emit?(["type": "key", "key": key])
        stop(silently: true)
        // Back to Zero's window, which shows the check and the result, as its guide screen promised.
        if debugHook == nil { NSApplication.shared.activate() }
    }

    // MARK: Drawing

    private var primaryHeight: CGFloat { NSScreen.screens.first?.frame.height ?? 0 }

    /// Accessibility frames start at the top left of the main screen; windows start at the bottom left.
    private func toScreen(_ r: CGRect) -> CGRect {
        CGRect(x: r.minX, y: primaryHeight - r.maxY, width: r.width, height: r.height)
    }

    private func followTarget() {
        guard script != nil, !paused, !frameBusy, let t = target, !cardMode else { return }
        frameBusy = true
        let el = t.element, app = AXUIElementCreateApplication(pid)
        frameQueue.async {
            let f = Self.frame(of: el)
            let w = (Self.attr(app, kAXMainWindowAttribute)).flatMap { Self.frame(of: $0 as! AXUIElement) }
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.frameBusy = false
                    guard self.target?.element == el, let f, f.width > 1 else { return }
                    let moved = f != self.target?.frame || w != self.snapshot?.window
                    if moved {
                        self.target = Node(element: el, role: t.role, name: t.name, frame: f, checked: t.checked, domID: t.domID, parent: t.parent, inDialog: t.inDialog)
                        if let w { self.snapshot?.window = w }
                        self.layout()
                    }
                }
            }
        }
    }

    private func publish() {
        guard let script else { return }
        let step = script.steps[index]
        var c = CalloutContent(title: step.title, body: step.body, lookFor: step.lookFor)
        c.status = status(for: step)
        c.canGoBack = index > 0
        c.nextTitle = step.id == "copy" ? "I Copied It" : "Next"
        if cardMode {
            c = CalloutContent(title: script.cardTitle, body: script.cardBody, lookFor: "", status: .pointing,
                               canGoBack: false, nextTitle: "I Copied It")
        }
        if let noKeyAt, Date().timeIntervalSince(noKeyAt) < 8 { c.status = .noKey } else { noKeyAt = nil }
        content = c
        layout()
        debugHook?(step, c.status, target?.element)
    }

    private func layout() {
        guard script != nil, !paused else { return }
        let browserFront = NSWorkspace.shared.frontmostApplication?.processIdentifier == pid
        let window = cardMode ? cgWindowFrame() : snapshot?.window
        guard browserFront || debugHook != nil, let window else {
            dimPanel?.orderOut(nil); calloutPanel?.orderOut(nil); return
        }
        ensurePanels()
        let pad = script?.steps[index].targets.first?.pad ?? 6
        var hole: CGRect?
        // The highlight stays on Copy while the callout says the clipboard has no key.
        if !cardMode, content.status == .pointing || content.status == .noKey, let t = target {
            hole = t.frame.insetBy(dx: -pad, dy: -pad).intersection(window)
            if hole?.isNull ?? true { hole = nil }
        }
        // Dim layer: the browser window only, so the rest of the screen stays usable.
        if !cardMode {
            dimPanel?.setFrame(toScreen(window), display: false)
            dimView.frame = NSRect(origin: .zero, size: window.size)
            dimView.show(hole: hole.map { $0.offsetBy(dx: -window.minX, dy: -window.minY) })
            dimPanel?.orderFrontRegardless()
        } else {
            dimPanel?.orderOut(nil)
        }
        // Callout: under the target if it fits, else above, else in the window's upper middle.
        let margin = CalloutView.shadowMargin
        // Measure without a pointer first; the pointer side is picked from where the card fits.
        calloutView.update(content: content, arrow: .none)
        let w = calloutView.fitted.width, h = calloutView.fitted.height
        var origin: CGPoint
        var arrow = CalloutArrow.none
        if let hole {
            let placement = script?.steps[index].placement ?? .auto
            let inSidebar = hole.maxX < window.minX + window.width * 0.3 && hole.width < window.width * 0.25
            let x = min(max(hole.midX - w / 2, window.minX + 12 - margin), window.maxX - w - 12 + margin)
            let below = hole.maxY + 6 - margin
            let fitsBelow = below + h - margin < window.maxY - 8
            let fitsAbove = hole.minY - h - 6 + margin > window.minY + 8
            if placement == .beside || (placement == .auto && inSidebar) {
                // To the right of the target, pointer on the card's left edge level with the target.
                let y = min(max(hole.midY - 60 - margin, window.minY + 8 - margin), window.maxY - h - 8 + margin)
                origin = CGPoint(x: hole.maxX + 4 - margin, y: y)
                arrow = .left(min(max(hole.midY - y - margin, 22), h - 2 * margin - 22))
            } else if (placement == .above && fitsAbove) || !fitsBelow {
                origin = CGPoint(x: x, y: hole.minY - h - 6 + margin); arrow = .down(clampArrow(hole.midX - x - margin))
            } else {
                origin = CGPoint(x: x, y: below); arrow = .up(clampArrow(hole.midX - x - margin))
            }
        } else if cardMode {
            origin = CGPoint(x: window.maxX - w - 16 + margin, y: window.maxY - h - 16 + margin)
        } else {
            origin = CGPoint(x: window.midX - w / 2, y: window.minY + window.height * 0.22)
        }
        calloutView.update(content: content, arrow: arrow)
        let final = calloutView.fitted
        // A pointer under the card makes the view taller downward; lift it so the tip stays clear of the target.
        if case .down = arrow { origin.y -= 11 }
        calloutPanel?.setFrame(toScreen(CGRect(origin: origin, size: final)), display: true)
        calloutPanel?.orderFrontRegardless()
    }

    private func clampArrow(_ x: CGFloat) -> CGFloat { min(max(x, 26), CalloutView.cardWidth - 26) }

    /// The browser's front window from the window server, which needs no permission. Used in card mode.
    private func cgWindowFrame() -> CGRect? {
        guard let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else { return nil }
        let mine = list.filter { ($0[kCGWindowOwnerPID as String] as? pid_t) == pid && ($0[kCGWindowLayer as String] as? Int) == 0 }
        guard let b = mine.first?[kCGWindowBounds as String] as? [String: CGFloat] else { return nil }
        return CGRect(x: b["X"] ?? 0, y: b["Y"] ?? 0, width: b["Width"] ?? 0, height: b["Height"] ?? 0)
    }

    private func ensurePanels() {
        if dimPanel == nil {
            let p = Self.panel()
            p.ignoresMouseEvents = true
            p.level = .floating
            p.contentView = dimView
            dimPanel = p
        }
        if calloutPanel == nil {
            let p = Self.panel()
            p.level = NSWindow.Level(rawValue: NSWindow.Level.floating.rawValue + 1)
            p.becomesKeyOnlyIfNeeded = true
            p.appearance = NSAppearance(named: .aqua)
            calloutView.onBack = { [weak self] in self?.back() }
            calloutView.onNext = { [weak self] in self?.next() }
            calloutView.onStuck = { [weak self] in self?.stuck($0) }
            calloutView.onResize = { [weak self] in DispatchQueue.main.async { self?.layout() } }
            p.contentView = calloutView
            calloutPanel = p
        }
    }

    private static func panel() -> NSPanel {
        let p = NSPanel(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        p.isOpaque = false
        p.backgroundColor = .clear
        p.hasShadow = false
        p.isFloatingPanel = true
        p.hidesOnDeactivate = false
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        return p
    }

    // MARK: Buttons

    private func back() {
        guard index > 0 else { return }
        go(to: index - 1)
        held = (index, true)
        advance(); publish()
    }

    private func next() {
        guard let script else { return }
        if cardMode || script.steps[index].id == "copy" {
            // "I Copied It": look at the clipboard now, even if the copy happened before Zero noticed.
            pasteboardCount = -1
            watchPasteboard()
            if !keyDelivered { noKeyAt = Date(); publish() }
            return
        }
        held = nil
        go(to: index + 1)
        advance(); publish()
    }

    private func stuck(_ choice: StuckChoice) {
        guard let script else { return }
        switch choice {
        case .findAgain:
            stepShownAt = Date(); scrolledFor = -1; takeSnapshot(); publish()
        case .skipStep:
            next()
        case .reopen:
            held = nil; go(to: 0); openInBrowser(script.startURL); publish()
        case .paste:
            paused = true
            dimPanel?.orderOut(nil); calloutPanel?.orderOut(nil)
            emit?(["type": "paste"])
            NSApplication.shared.activate()
        case .stop:
            stop()
        }
    }
}
