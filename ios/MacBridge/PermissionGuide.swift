import AppKit
import ApplicationServices

/// The guided setup for what macOS makes a person allow before Zips can work on this Mac: Accessibility, Screen
/// Recording and Full Disk Access. It works like the Google key guide: one step at a time, each with one sentence of
/// why, the exact System Settings pane opened for the person, and Zero's icon floating over that pane so it can be
/// dragged straight into the list. Each step checks itself off the moment macOS reports it allowed.
///
/// Screen Recording only takes effect after Zero restarts. macOS offers "Quit & Reopen" for that; the guide also has
/// its own Restart Zero button. Either way it saves where it was and opens again at that step after the restart.
///
/// The panel over System Settings, and the way it follows that window without needing any permission itself, are
/// ported from T3 Code's MacPermissionHelper (github.com/pingdotgg/t3code, MIT License, Copyright (c) T3 Tools Inc.).
@MainActor
final class PermissionGuide: NSObject {
    static let shared = PermissionGuide()

    enum Step: String, CaseIterable {
        case accessibility, screen, files

        var title: String {
            switch self {
            case .accessibility: return "Accessibility"
            case .screen: return "Screen Recording"
            case .files: return "Full Disk Access"
            }
        }
        var why: String {
            switch self {
            case .accessibility: return "This lets a Zip read and press the buttons in your apps without moving your mouse."
            case .screen: return "This lets a Zip see your screen when an app's buttons can't be read."
            case .files: return "This lets a Zip find your files in Documents, Desktop, Downloads and iCloud Drive."
            }
        }
        var symbol: String {
            switch self {
            case .accessibility: return "accessibility"
            case .screen: return "rectangle.dashed.badge.record"
            case .files: return "folder"
            }
        }
        var pane: URL {
            switch self {
            case .accessibility: return URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!
            case .screen: return URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")!
            case .files: return URL(string: "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_AllFiles")!
            }
        }
    }

    static func allowed(_ step: Step) -> Bool {
        switch step {
        case .accessibility: return AXIsProcessTrusted()
        case .screen: return CGPreflightScreenCaptureAccess()
        case .files:
            // A file only Full Disk Access can open. Reading it opens nothing else and changes nothing.
            let probe = ("~/Library/Application Support/com.apple.TCC/TCC.db" as NSString).expandingTildeInPath
            guard let h = FileHandle(forReadingAtPath: probe) else { return false }
            try? h.close()
            return true
        }
    }

    static func status() -> [String: Bool] {
        Dictionary(uniqueKeysWithValues: Step.allCases.map { ($0.rawValue, allowed($0)) })
    }

    private static let resumeKey = "zero.permissionGuide.resume"
    private static let resumeAtKey = "zero.permissionGuide.resumeAt"

    private var window: NSPanel?
    private var rows: [Step: StepRow] = [:]
    private var current: Step?
    private var timer: Timer?
    private var helper: SettingsHelper?
    private var footer: NSTextField?
    private var doneButton: NSButton?
    private var openedAt = Date.distantPast
    private var lastOffer = Date.distantPast
    /// Told when a step changes, for Zero's window (Settings shows the same check marks).
    var onChange: (([String: Bool]) -> Void)?

    // MARK: Starting

    /// Opens the guide, at `focus` if given, otherwise at the first step that still needs allowing.
    func start(focus: Step? = nil, quiet: Bool = false) {
        if window == nil { build() }
        refresh()
        window?.center()
        window?.orderFrontRegardless()
        if !quiet {
            NSApp.activate(ignoringOtherApps: true)
            window?.makeKey()
            if let focus, !Self.allowed(focus) { begin(focus) }
        }
        if timer == nil {
            timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                MainActor.assumeIsolated { self?.tick() }
            }
        }
    }

    /// When a Zip needs a permission that is off: shows the guide without taking focus (a Zip may be mid-task, and
    /// Colin may be typing), and opens nothing until he presses Set Up. At most once every ten minutes; the bridge
    /// also shows it at most once per grant.
    func offer(_ step: Step) {
        guard window?.isVisible != true, Date().timeIntervalSince(lastOffer) > 600 else { return }
        lastOffer = Date()
        start(focus: step, quiet: true)
    }

    /// After a restart (macOS's Quit & Reopen, or Restart Zero): opens the guide again where it was.
    func resumeIfNeeded() {
        let d = UserDefaults.standard
        guard let raw = d.string(forKey: Self.resumeKey), let step = Step(rawValue: raw),
              Date().timeIntervalSince1970 - d.double(forKey: Self.resumeAtKey) < 15 * 60 else { return }
        d.removeObject(forKey: Self.resumeKey)
        let next = Step.allCases.first { !Self.allowed($0) }
        if Self.allowed(step) { rows[step]?.flash = true }
        start(focus: next)
    }

    func stop() {
        timer?.invalidate()
        timer = nil
        helper?.close()
        helper = nil
        window?.orderOut(nil)
        current = nil
        UserDefaults.standard.removeObject(forKey: Self.resumeKey)
    }

    // MARK: Steps

    private func begin(_ step: Step) {
        current = step
        openedAt = Date()
        if step == .screen {
            // Puts Zero in the Screen Recording list (macOS may show its own prompt too), and remembers the step,
            // since turning it on asks to quit Zero.
            UserDefaults.standard.set(step.rawValue, forKey: Self.resumeKey)
            UserDefaults.standard.set(Date().timeIntervalSince1970, forKey: Self.resumeAtKey)
            if !CGPreflightScreenCaptureAccess() { CGRequestScreenCaptureAccess() }
        }
        NSWorkspace.shared.open(step.pane)
        helper?.close()
        helper = SettingsHelper(step: step)
        helper?.start()
        refresh()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.9) { [weak self] in
            MainActor.assumeIsolated { self?.placeBesideSettings() }
        }
    }

    private func placeBesideSettings() {
        guard let window, let settings = SettingsHelper.settingsWindow(), let screen = NSScreen.main else {
            window?.orderFrontRegardless()
            return
        }
        let primary = NSScreen.screens.first?.frame.height ?? screen.frame.height
        let size = window.frame.size
        let visible = screen.visibleFrame
        var x = settings.frame.minX - size.width - 16
        if x < visible.minX { x = min(settings.frame.maxX + 16, visible.maxX - size.width) }
        let y = primary - settings.frame.minY - size.height
        window.setFrameOrigin(NSPoint(x: max(visible.minX, x), y: max(visible.minY, min(y, visible.maxY - size.height))))
        window.orderFrontRegardless()
    }

    private func tick() {
        let before = rows.mapValues { $0.allowed }
        refresh()
        if let step = current, Self.allowed(step) {
            helper?.close()
            helper = nil
            current = nil
            if step == .screen { UserDefaults.standard.removeObject(forKey: Self.resumeKey) }
            if let next = Step.allCases.first(where: { !Self.allowed($0) }) {
                begin(next)
            } else {
                NSApp.activate(ignoringOtherApps: true)
                window?.orderFrontRegardless()
            }
        }
        if before != rows.mapValues({ $0.allowed }) { onChange?(Self.status()) }
    }

    private func refresh() {
        var allDone = true
        for step in Step.allCases {
            let ok = Self.allowed(step)
            allDone = allDone && ok
            rows[step]?.update(allowed: ok, active: current == step,
                               restartHint: step == .screen && current == .screen && Date().timeIntervalSince(openedAt) > 12)
        }
        footer?.stringValue = allDone ? "Zips can now work on this Mac when you allow a task."
                                      : "You can stop at any time and come back from Settings."
        doneButton?.title = allDone ? "Done" : "Not Now"
    }

    @objc private func setUp(_ sender: NSButton) {
        guard let step = Step(rawValue: sender.identifier?.rawValue ?? "") else { return }
        begin(step)
    }

    @objc private func donePressed() { stop() }

    /// Quits and reopens Zero, for Screen Recording. The guide opens again at the same step.
    @objc func restartZero() {
        UserDefaults.standard.set(Step.screen.rawValue, forKey: Self.resumeKey)
        UserDefaults.standard.set(Date().timeIntervalSince1970, forKey: Self.resumeAtKey)
        let path = Bundle.main.bundlePath
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = ["-c", "sleep 1; /usr/bin/open \"$0\"", path]
        try? p.run()
        NSApp.terminate(nil)
    }

    // MARK: The window

    private func build() {
        let p = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 460, height: 420),
                        styleMask: [.titled, .closable, .fullSizeContentView], backing: .buffered, defer: false)
        p.title = "Let Zips Use This Mac"
        p.titleVisibility = .hidden
        p.titlebarAppearsTransparent = true
        p.level = .floating
        p.isReleasedWhenClosed = false
        p.hidesOnDeactivate = false
        p.collectionBehavior = [.fullScreenAuxiliary, .moveToActiveSpace]
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 14
        stack.edgeInsets = NSEdgeInsets(top: 36, left: 24, bottom: 20, right: 24)
        let title = NSTextField(labelWithString: "Let Zips Use This Mac")
        title.font = .systemFont(ofSize: 20, weight: .semibold)
        stack.addArrangedSubview(title)
        let intro = NSTextField(wrappingLabelWithString: "macOS asks you to allow three things once. Each step opens the right "
            + "page in System Settings. Drag Zero into the list there, or turn on the switch next to Zero.")
        intro.font = .systemFont(ofSize: 13)
        intro.textColor = .secondaryLabelColor
        intro.preferredMaxLayoutWidth = 410
        stack.addArrangedSubview(intro)
        for step in Step.allCases {
            let row = StepRow(step: step, target: self, action: #selector(setUp(_:)), restart: #selector(restartZero))
            rows[step] = row
            stack.addArrangedSubview(row)
            row.widthAnchor.constraint(equalToConstant: 412).isActive = true
        }
        let foot = NSTextField(wrappingLabelWithString: "")
        foot.font = .systemFont(ofSize: 12)
        foot.textColor = .secondaryLabelColor
        foot.preferredMaxLayoutWidth = 300
        footer = foot
        let done = NSButton(title: "Not Now", target: self, action: #selector(donePressed))
        done.keyEquivalent = "\r"
        doneButton = done
        let bottom = NSStackView(views: [foot, NSView(), done])
        bottom.distribution = .fill
        bottom.widthAnchor.constraint(equalToConstant: 412).isActive = true
        stack.addArrangedSubview(bottom)
        p.contentView = stack
        p.setContentSize(stack.fittingSize)
        p.delegate = self
        window = p
    }
}

extension PermissionGuide: NSWindowDelegate {
    nonisolated func windowWillClose(_ notification: Notification) {
        MainActor.assumeIsolated { self.stop() }
    }
}

/// One step in the guide: its symbol, name and reason, and a Set Up button that becomes a check mark.
@MainActor
private final class StepRow: NSStackView {
    let step: PermissionGuide.Step
    private let icon = NSImageView()
    private let status = NSImageView()
    private let button: NSButton
    private let restart: NSButton
    private let hint = NSTextField(wrappingLabelWithString: "")
    private(set) var allowed = false
    var flash = false

    init(step: PermissionGuide.Step, target: AnyObject, action: Selector, restart restartAction: Selector) {
        self.step = step
        button = NSButton(title: "Set Up", target: target, action: action)
        button.identifier = NSUserInterfaceItemIdentifier(step.rawValue)
        button.bezelStyle = .rounded
        restart = NSButton(title: "Restart Zero", target: target, action: restartAction)
        restart.bezelStyle = .rounded
        super.init(frame: .zero)
        orientation = .horizontal
        alignment = .top
        spacing = 12
        icon.image = NSImage(systemSymbolName: step.symbol, accessibilityDescription: nil)
        icon.symbolConfiguration = .init(pointSize: 20, weight: .regular)
        icon.contentTintColor = .controlAccentColor
        icon.widthAnchor.constraint(equalToConstant: 28).isActive = true
        let name = NSTextField(labelWithString: step.title)
        name.font = .systemFont(ofSize: 14, weight: .semibold)
        let why = NSTextField(wrappingLabelWithString: step.why)
        why.font = .systemFont(ofSize: 12)
        why.textColor = .secondaryLabelColor
        why.preferredMaxLayoutWidth = 250
        hint.font = .systemFont(ofSize: 12)
        hint.textColor = .secondaryLabelColor
        hint.preferredMaxLayoutWidth = 250
        hint.isHidden = true
        let text = NSStackView(views: [name, why, hint, restart])
        text.orientation = .vertical
        text.alignment = .leading
        text.spacing = 3
        restart.isHidden = true
        status.image = NSImage(systemSymbolName: "checkmark.circle.fill", accessibilityDescription: "Allowed")
        status.symbolConfiguration = .init(pointSize: 20, weight: .regular)
        status.contentTintColor = .systemGreen
        status.isHidden = true
        let end = NSStackView(views: [button, status])
        end.orientation = .vertical
        addArrangedSubview(icon)
        addArrangedSubview(text)
        addArrangedSubview(NSView())
        addArrangedSubview(end)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    func update(allowed: Bool, active: Bool, restartHint: Bool) {
        self.allowed = allowed
        status.isHidden = !allowed
        button.isHidden = allowed
        button.title = active ? "Show Me" : "Set Up"
        if active && !allowed {
            hint.stringValue = step == .screen && restartHint
                ? "If the switch for Zero is on, Zero has to restart to use it."
                : "System Settings is open. Drag Zero into the list, or turn on its switch."
            hint.isHidden = false
        } else {
            hint.isHidden = true
        }
        restart.isHidden = !(step == .screen && restartHint && !allowed)
    }
}

/// The small panel that sits inside System Settings' content column, under the list, with Zero's icon to drag in.
/// It follows the Settings window by reading only window positions, which needs no permission, and closes when
/// Settings closes.
@MainActor
private final class SettingsHelper: NSObject {
    private let step: PermissionGuide.Step
    private var panel: NSPanel?
    private var timer: Timer?
    private var sawSettings = false

    init(step: PermissionGuide.Step) { self.step = step }

    func start() {
        let p = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 420, height: 110),
                        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        p.isOpaque = false
        p.backgroundColor = .clear
        p.hasShadow = true
        p.level = .floating
        p.hidesOnDeactivate = false
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient]
        let box = NSVisualEffectView()
        box.material = .popover
        box.state = .active
        box.wantsLayer = true
        box.layer?.cornerRadius = 18
        box.layer?.masksToBounds = true
        let arrow = NSImageView(image: NSImage(systemSymbolName: "arrow.up", accessibilityDescription: nil) ?? NSImage())
        arrow.symbolConfiguration = .init(pointSize: 13, weight: .semibold)
        let title = NSTextField(labelWithString: "Drag Zero into the list above")
        title.font = .systemFont(ofSize: 14, weight: .semibold)
        let head = NSStackView(views: [arrow, title])
        head.spacing = 6
        let drag = DragAppView()
        drag.toolTip = "Drag Zero into the list in System Settings. Click to show Zero in Finder."
        let alt = NSTextField(labelWithString: "If Zero is already in the list, turn on its switch.")
        alt.font = .systemFont(ofSize: 11)
        alt.textColor = .secondaryLabelColor
        let stack = NSStackView(views: [head, drag, alt])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 8
        stack.edgeInsets = NSEdgeInsets(top: 14, left: 16, bottom: 12, right: 16)
        drag.widthAnchor.constraint(equalToConstant: 388).isActive = true
        drag.heightAnchor.constraint(equalToConstant: 44).isActive = true
        box.addSubview(stack)
        stack.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo: box.leadingAnchor),
                                     stack.trailingAnchor.constraint(equalTo: box.trailingAnchor),
                                     stack.topAnchor.constraint(equalTo: box.topAnchor),
                                     stack.bottomAnchor.constraint(equalTo: box.bottomAnchor)])
        p.contentView = box
        panel = p
        timer = Timer.scheduledTimer(withTimeInterval: 0.4, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.follow() }
        }
    }

    func close() {
        timer?.invalidate()
        timer = nil
        panel?.orderOut(nil)
        panel = nil
    }

    /// System Settings' main window, in screen coordinates with the origin at the top left, if it is on screen.
    static func settingsWindow() -> (frame: CGRect, front: Bool)? {
        guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.systempreferences").first
        else { return nil }
        let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
        for w in list where (w[kCGWindowOwnerPID as String] as? pid_t) == app.processIdentifier
            && (w[kCGWindowLayer as String] as? Int) == 0 {
            guard let b = w[kCGWindowBounds as String] as? [String: CGFloat] else { continue }
            let r = CGRect(x: b["X"] ?? 0, y: b["Y"] ?? 0, width: b["Width"] ?? 0, height: b["Height"] ?? 0)
            if r.width >= 500 && r.height >= 350 {
                return (r, NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier)
            }
        }
        return nil
    }

    private func follow() {
        guard let panel else { return }
        guard let settings = Self.settingsWindow() else {
            if sawSettings { close() } else { panel.orderOut(nil) }
            return
        }
        sawSettings = true
        guard settings.front || NSApp.isActive else { return panel.orderOut(nil) }
        let sidebar: CGFloat = 216
        let width = min(420, settings.frame.width - sidebar - 32)
        let height = panel.frame.height
        let x = settings.frame.minX + sidebar + (settings.frame.width - sidebar - width) / 2
        let topY = settings.frame.maxY - height - 44
        let primary = NSScreen.screens.first?.frame.height ?? 0
        let frame = NSRect(x: x, y: primary - topY - height, width: width, height: height)
        if panel.frame != frame { panel.setFrame(frame, display: true) }
        if !panel.isVisible { panel.orderFrontRegardless() }
    }
}

/// Zero's icon and name as something to drag. Dropped on a System Settings list, it adds Zero the same way the +
/// button would; a click shows Zero in Finder instead, where it can be dragged from too.
private final class DragAppView: NSView, NSDraggingSource {
    private let bundleURL = Bundle.main.bundleURL
    private var downAt: NSPoint?

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.cornerRadius = 10
        layer?.backgroundColor = NSColor.quaternaryLabelColor.cgColor
        let icon = NSImageView(image: NSWorkspace.shared.icon(forFile: bundleURL.path))
        icon.imageScaling = .scaleProportionallyUpOrDown
        let name = NSTextField(labelWithString: FileManager.default.displayName(atPath: bundleURL.path)
            .replacingOccurrences(of: ".app", with: ""))
        name.font = .systemFont(ofSize: 15, weight: .semibold)
        let row = NSStackView(views: [icon, name])
        row.spacing = 10
        row.edgeInsets = NSEdgeInsets(top: 6, left: 10, bottom: 6, right: 10)
        icon.widthAnchor.constraint(equalToConstant: 32).isActive = true
        icon.heightAnchor.constraint(equalToConstant: 32).isActive = true
        addSubview(row)
        row.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([row.leadingAnchor.constraint(equalTo: leadingAnchor),
                                     row.centerYAnchor.constraint(equalTo: centerYAnchor)])
        setAccessibilityRole(.button)
        setAccessibilityLabel("Drag Zero into System Settings, or click to show it in Finder")
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    override func resetCursorRects() { addCursorRect(bounds, cursor: .openHand) }

    override func mouseDown(with event: NSEvent) { downAt = event.locationInWindow }

    override func mouseDragged(with event: NSEvent) {
        guard let start = downAt, hypot(event.locationInWindow.x - start.x, event.locationInWindow.y - start.y) > 3 else { return }
        downAt = nil
        let item = NSDraggingItem(pasteboardWriter: bundleURL as NSURL)
        let image = NSWorkspace.shared.icon(forFile: bundleURL.path)
        image.size = NSSize(width: 48, height: 48)
        item.setDraggingFrame(NSRect(x: 10, y: (bounds.height - 48) / 2, width: 48, height: 48), contents: image)
        beginDraggingSession(with: [item], event: event, source: self)
    }

    override func mouseUp(with event: NSEvent) {
        if downAt != nil { NSWorkspace.shared.activateFileViewerSelecting([bundleURL]) }
        downAt = nil
    }

    func draggingSession(_ session: NSDraggingSession, sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation {
        context == .outsideApplication ? [.copy, .link, .generic] : []
    }
}
