import AppKit
import CryptoKit

/// Allowing a Zip's task from the iPhone, without giving the hub the power to allow anything.
///
/// Each iPhone makes a signing key in its Secure Enclave that only Face ID or the passcode unlocks, and the hub passes
/// its public half here. This Mac trusts a phone only after Colin says so once, on this Mac. An Allow from the phone
/// arrives as `grant` with a signature over the request on this Mac's card (its id, the Zip, the task, his words and
/// when the card closes), and it starts the grant only if that signature checks out against a trusted phone. A hub
/// that was changed or broken into can pass requests along or close the card, but it cannot allow one.
extension TaffyMacBridge {
    private static let trustedKey = "zero.mac.trustedPhones"
    private static let declinedKey = "zero.mac.declinedPhones"
    private static var lastTrustAskAt = Date.distantPast
    private var lastTrustAsk: Date {
        get { Self.lastTrustAskAt }
        set { Self.lastTrustAskAt = newValue }
    }

    static var trustedPhones: [String: String] {
        get { UserDefaults.standard.dictionary(forKey: trustedKey) as? [String: String] ?? [:] }
        set { UserDefaults.standard.set(newValue, forKey: trustedKey) }
    }

    func grantFromPhone(_ args: [String: Any], _ ok: (Any) -> Void, _ fail: (String) -> Void) {
        guard let p = pendingApproval, (args["approval_id"] as? String) == p.id else {
            return fail("That request is no longer showing on the Mac.")
        }
        guard Int(Date().timeIntervalSince1970) <= p.expires else { return fail("That request timed out.") }
        guard let keyID = args["key_id"] as? String, let keyText = Self.trustedPhones[keyID],
              let keyData = Data(base64Encoded: keyText),
              let key = try? P256.Signing.PublicKey(x963Representation: keyData) else {
            return fail("This phone isn't trusted on the Mac yet. The Mac asks once, the next time \(who) is at it.")
        }
        guard let sigData = Data(base64Encoded: args["signature"] as? String ?? ""),
              MacGrant.verify(key: key, signature: sigData, id: p.id, bot: p.bot, task: p.task, said: p.said,
                              expires: p.expires) else {
            return fail("The phone's approval did not match the request on the Mac, so the Mac did not allow it.")
        }
        answeredVia = "phone"
        allowPressed()
        ok(true)
    }

    /// Phones the hub says can allow from afar. A new one gets a one-time question on this Mac, with its key's
    /// fingerprint to compare against the phone's Settings, because the name and the key both come through the hub:
    /// a hub that was broken into could offer its own key under the phone's name.
    func phonesSeen(_ keys: [[String: Any]]) {
        let declined = Set(UserDefaults.standard.stringArray(forKey: Self.declinedKey) ?? [])
        var trusted = Self.trustedPhones
        let ids = Set(keys.compactMap { $0["id"] as? String })
        if !ids.isEmpty {
            trusted = trusted.filter { ids.contains($0.key) }
            Self.trustedPhones = trusted
        }
        guard trustPanel == nil, Date().timeIntervalSince(lastTrustAsk) > 600 else { return }
        for k in keys {
            guard let id = k["id"] as? String, let key = k["key"] as? String, !key.isEmpty,
                  trusted[id] != key, !declined.contains(key), let raw = Data(base64Encoded: key) else { continue }
            lastTrustAsk = Date()
            askToTrust(id: id, key: key, name: (k["name"] as? String) ?? "your iPhone",
                       fingerprint: MacGrant.fingerprint(raw), changed: trusted[id] != nil)
            return
        }
    }

    private func askToTrust(id: String, key: String, name: String, fingerprint: String, changed: Bool) {
        let p = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 420, height: 220),
                        styleMask: [.nonactivatingPanel, .titled, .fullSizeContentView], backing: .buffered, defer: false)
        p.level = .statusBar
        p.titleVisibility = .hidden
        p.titlebarAppearsTransparent = true
        p.isMovableByWindowBackground = true
        p.hidesOnDeactivate = false
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 10
        stack.edgeInsets = NSEdgeInsets(top: 22, left: 22, bottom: 18, right: 22)
        let title = NSTextField(wrappingLabelWithString: changed
            ? "A different key is asking to approve for \(name)"
            : "Let \(name) allow Zips to use this Mac?")
        title.font = .systemFont(ofSize: 15, weight: .semibold)
        title.preferredMaxLayoutWidth = 376
        let body = NSTextField(wrappingLabelWithString: changed
            ? "If you didn't just reinstall Zero on \(name), choose Don't Allow. If you did, check that the code below "
              + "matches the one in Zero's Settings on \(name) before you allow it."
            : "When you are away, a Zip's request can show on \(name). Allowing it there takes Face ID or your "
              + "passcode, and this Mac still shows Stop and ends the task after 30 minutes on its own clock. "
              + "First check that the code below matches the one in Zero's Settings on \(name), under Allow Mac Use "
              + "From This iPhone.")
        body.font = .systemFont(ofSize: 12)
        body.textColor = .secondaryLabelColor
        body.preferredMaxLayoutWidth = 376
        let code = NSTextField(labelWithString: fingerprint)
        code.font = .monospacedSystemFont(ofSize: 20, weight: .semibold)
        code.setAccessibilityLabel("Code \(fingerprint)")
        let no = NSButton(title: "Don't Allow", target: self, action: #selector(trustDeclined))
        no.keyEquivalent = "\u{1b}"
        let yes = NSButton(title: changed ? "Allow the New Key" : "The Codes Match, Allow", target: self, action: #selector(trustAllowed))
        let buttons = NSStackView(views: [no, yes])
        buttons.spacing = 10
        for v in [title, body, code, buttons] as [NSView] { stack.addArrangedSubview(v) }
        p.contentView = stack
        p.setContentSize(stack.fittingSize)
        if let screen = NSScreen.main {
            let f = screen.visibleFrame
            p.setFrameOrigin(NSPoint(x: f.midX - p.frame.width / 2, y: f.maxY - p.frame.height - 40))
        }
        trustPanel = p
        trustCandidate = (id, key)
        p.orderFrontRegardless()
    }

    @objc private func trustAllowed() {
        if let c = trustCandidate { Self.trustedPhones[c.id] = c.key }
        closeTrust()
    }

    @objc private func trustDeclined() {
        if let c = trustCandidate {
            var declined = UserDefaults.standard.stringArray(forKey: Self.declinedKey) ?? []
            declined.append(c.key)
            UserDefaults.standard.set(declined, forKey: Self.declinedKey)
        }
        closeTrust()
    }

    private func closeTrust() {
        trustPanel?.orderOut(nil)
        trustPanel = nil
        trustCandidate = nil
    }
}
