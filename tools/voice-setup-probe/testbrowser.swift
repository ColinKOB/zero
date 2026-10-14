// A bare WebKit browser window for testing Zero's guide without touching anyone's real browser.
// It uses the same WebKit and accessibility tree as Safari, Safari's user agent, and a fresh signed-out session.
// Usage: testbrowser <url> [x y w h]
import AppKit
import WebKit

let args = CommandLine.arguments
let start = URL(string: args.count > 1 ? args[1] : "about:blank")!
let frame = args.count > 5
    ? NSRect(x: Double(args[2])!, y: Double(args[3])!, width: Double(args[4])!, height: Double(args[5])!)
    : NSRect(x: 60, y: 60, width: 1240, height: 800)

final class Browser: NSObject, WKNavigationDelegate, NSApplicationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    let address = NSTextField(labelWithString: "")
    var pending: URL?

    func applicationDidFinishLaunching(_ n: Notification) {
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .nonPersistent()
        // Lets the practice page's Copy button write to the clipboard when the harness presses it.
        cfg.preferences.setValue(true, forKey: "javaScriptCanAccessClipboard")
        web = WKWebView(frame: .zero, configuration: cfg)
        web.customUserAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15"
        web.navigationDelegate = self
        address.font = .systemFont(ofSize: 12)
        address.textColor = .secondaryLabelColor
        address.lineBreakMode = .byTruncatingMiddle
        address.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        let bar = NSView()
        bar.wantsLayer = true
        bar.layer?.backgroundColor = NSColor(white: 0.95, alpha: 1).cgColor
        for v in [bar, address, web!] as [NSView] { v.translatesAutoresizingMaskIntoConstraints = false }
        let root = NSView()
        root.addSubview(bar); bar.addSubview(address); root.addSubview(web)
        NSLayoutConstraint.activate([
            bar.topAnchor.constraint(equalTo: root.topAnchor), bar.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            bar.trailingAnchor.constraint(equalTo: root.trailingAnchor), bar.heightAnchor.constraint(equalToConstant: 30),
            address.centerYAnchor.constraint(equalTo: bar.centerYAnchor), address.leadingAnchor.constraint(equalTo: bar.leadingAnchor, constant: 14),
            address.trailingAnchor.constraint(equalTo: bar.trailingAnchor, constant: -14),
            web.topAnchor.constraint(equalTo: bar.bottomAnchor), web.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            web.trailingAnchor.constraint(equalTo: root.trailingAnchor), web.bottomAnchor.constraint(equalTo: root.bottomAnchor),
        ])
        window = NSWindow(contentRect: frame, styleMask: [.titled, .closable, .resizable, .miniaturizable], backing: .buffered, defer: false)
        window.contentView = root
        window.isReleasedWhenClosed = false
        window.title = "Test Browser"
        // AppKit frames start at the bottom left; the arguments are top-left like the accessibility frames.
        let top = NSScreen.screens[0].frame.height
        window.setFrame(NSRect(x: frame.minX, y: top - frame.minY - frame.height, width: frame.width, height: frame.height), display: true)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        web.load(URLRequest(url: pending ?? start))
    }

    /// Opened as an app (ZeroTestBrowser.app) with a URL, the way Zero's guide opens the person's browser.
    func application(_ application: NSApplication, open urls: [URL]) {
        guard let u = urls.first else { return }
        if let web { web.load(URLRequest(url: u)); window.makeKeyAndOrderFront(nil) } else { pending = u }
        NSApp.activate(ignoringOtherApps: true)
    }

    func webView(_ w: WKWebView, didCommit n: WKNavigation!) { address.stringValue = w.url?.absoluteString ?? "" }
    func webView(_ w: WKWebView, didFinish n: WKNavigation!) {
        address.stringValue = w.url?.absoluteString ?? ""
        window.title = w.title ?? "Test Browser"
    }
}

let app = NSApplication.shared
let delegate = Browser()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
