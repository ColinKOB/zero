// Probe: how does Google treat a plain WKWebView on macOS?
// Usage: swift probe.swift <outdir> <mode: default|safari> [email]
// Loads AI Studio's key page (signed out), saves a snapshot and the page text, then types a made-up
// address into Google's sign-in form, presses Next and saves what Google shows. Never signs in.
import AppKit
import WebKit

let args = CommandLine.arguments
let outDir = URL(fileURLWithPath: args.count > 1 ? args[1] : "/tmp/probe")
let mode = args.count > 2 ? args[2] : "default"
let email = args.count > 3 ? args[3] : "zero.probe.nobody.4471x@gmail.com"
try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

final class Probe: NSObject, WKNavigationDelegate {
    let web: WKWebView
    let window: NSWindow
    var step = 0
    init(mode: String) {
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .nonPersistent()
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: 1280, height: 860), configuration: cfg)
        if mode == "safari" {
            web.customUserAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15"
        }
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860), styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = web
        super.init()
        web.navigationDelegate = self
    }

    func save(_ name: String, done: @escaping () -> Void) {
        web.evaluateJavaScript("location.href + '\\n----\\n' + document.title + '\\n----\\n' + document.body.innerText") { r, _ in
            try? ((r as? String) ?? "").write(to: outDir.appendingPathComponent(name + ".txt"), atomically: true, encoding: .utf8)
            self.web.evaluateJavaScript("navigator.userAgent") { ua, _ in
                print(name, "UA:", ua ?? "?")
                self.web.takeSnapshot(with: nil) { img, _ in
                    if let img, let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
                       let png = rep.representation(using: .png, properties: [:]) {
                        try? png.write(to: outDir.appendingPathComponent(name + ".png"))
                    }
                    print("saved", name)
                    done()
                }
            }
        }
    }

    func after(_ s: Double, _ f: @escaping () -> Void) { DispatchQueue.main.asyncAfter(deadline: .now() + s, execute: f) }

    func run() {
        window.orderFrontRegardless()
        web.load(URLRequest(url: URL(string: "https://aistudio.google.com/apikey")!))
        after(9) {
            self.save("1-apikey-signed-out") {
                // Follow the sign-in path the page offers, or go straight to Google's sign-in.
                self.web.load(URLRequest(url: URL(string: "https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Faistudio.google.com%2Fapikey")!))
                self.after(7) {
                    self.save("2-signin-identifier") {
                        self.web.evaluateJavaScript("[...document.querySelectorAll('input,button')].map(e=>e.outerHTML.slice(0,220)).join('\\n')") { r, _ in
                            try? ((r as? String) ?? "").write(to: outDir.appendingPathComponent("2-inputs.txt"), atomically: true, encoding: .utf8)
                        }
                        let js = """
                        (function(){
                          const i = document.querySelector('input[type=email], #identifierId, input[name=identifier], input[name=Email], input[type=text]');
                          if (!i) return 'no email field';
                          i.focus(); i.value = '\(email)';
                          i.dispatchEvent(new Event('input', {bubbles:true}));
                          const b = [...document.querySelectorAll('button, input[type=submit], [role=button]')].find(b => (b.innerText||b.value||'').trim() === 'Next');
                          if (!b) return 'no Next button';
                          b.click(); return 'clicked';
                        })()
                        """
                        self.web.evaluateJavaScript(js) { r, e in
                            print("type+next:", r ?? e ?? "?")
                            self.after(8) { self.save("3-after-next") { exit(0) } }
                        }
                    }
                }
            }
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let probe = Probe(mode: mode)
probe.run()
DispatchQueue.main.asyncAfter(deadline: .now() + 60) { print("timeout"); exit(1) }
app.run()
