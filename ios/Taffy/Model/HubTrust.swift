import CryptoKit
import Foundation
import Security

/// Certificate pins for Mac homes on the Wi-Fi. A Mac home serves TLS with its own self-signed certificate, and its
/// pairing QR code carries that certificate's SHA-256 fingerprint (`zero://pair?server=...&code=...&fp=...`). The app
/// keeps the fingerprint for that address and, from then on, accepts exactly that certificate there and nothing else
/// (docs/plans/2026-10-08-mac-home-packaging-plan.md, "certificate pinned at pairing").
///
/// Addresses without a pin (a server home such as alien behind Tailscale's real certificate) are checked by the system
/// exactly as before. The fingerprint is not a secret, so it lives in UserDefaults.
enum HubTrust {
    private static let key = "hubPins"

    /// "host:port" in lower case, the way pins are kept.
    static func id(_ url: URL) -> String? {
        guard let host = url.host?.lowercased() else { return nil }
        let port = url.port ?? (url.scheme?.lowercased() == "https" ? 443 : 80)
        return "\(host):\(port)"
    }

    static func pin(for url: URL) -> String? {
        guard let id = id(url) else { return nil }
        return (UserDefaults.standard.dictionary(forKey: key) as? [String: String])?[id]
    }

    static func setPin(_ fingerprint: String, for server: String) {
        guard let url = URL(string: server), let id = id(url), let fp = normalized(fingerprint) else { return }
        var pins = (UserDefaults.standard.dictionary(forKey: key) as? [String: String]) ?? [:]
        pins[id] = fp
        UserDefaults.standard.set(pins, forKey: key)
    }

    static func clearPins() { UserDefaults.standard.removeObject(forKey: key) }

    /// Forgets the pin for one home (unpair, log out, remove).
    static func removePin(for server: String) {
        guard let url = URL(string: server), let id = id(url) else { return }
        var pins = (UserDefaults.standard.dictionary(forKey: key) as? [String: String]) ?? [:]
        pins[id] = nil
        UserDefaults.standard.set(pins, forKey: key)
    }

    // MARK: Pairing

    /// A link's fingerprint while its pairing request runs: trusted for that one address only until the pairing
    /// answers, and stored as the pin only if the pairing worked. Reading a link never stores anything.
    private static let candidateLock = NSLock()
    nonisolated(unsafe) private static var candidates: [String: String] = [:]

    static func candidate(for url: URL) -> String? {
        guard let id = id(url) else { return nil }
        candidateLock.lock(); defer { candidateLock.unlock() }
        return candidates[id]
    }

    /// The pin that applies to a connection now: the stored one, else a pairing request's candidate.
    static func effectivePin(for url: URL) -> String? { pin(for: url) ?? candidate(for: url) }

    enum PairCheck: Equatable {
        /// Go ahead; `candidate` is the link's fingerprint to trust for this one pairing request.
        case go(candidate: String?)
        case refuse(String)
    }

    /// When a certificate was last refused, so the cancelled request that follows reads as a trust problem
    /// (HubClient.describe) instead of "Something went wrong".
    nonisolated(unsafe) private static var refusedAt: Date?
    static func noteRefusal() { candidateLock.lock(); refusedAt = Date(); candidateLock.unlock() }
    static var refusedJustNow: Bool {
        candidateLock.lock(); defer { candidateLock.unlock() }
        return refusedAt.map { Date().timeIntervalSince($0) < 15 } ?? false
    }
    static let notYourMac = "Zero couldn't confirm this is your Mac. If you reinstalled Zero there, pair again from Settings."

    static let mismatch = "This doesn't match the Mac you paired before. If you reinstalled Zero there, remove it in Settings first."
    static let insecure = "That address isn't secure. Scan the code on your Mac to connect."
    static let unpinned = "Scan the code on your Mac to connect, so Zero can check it is really your Mac."

    /// Whether a pairing request may go to `url` with the link's fingerprint `fp` (nil when the address was typed or
    /// the link had none). An existing pin is never replaced: a different fingerprint for a pinned address is
    /// refused. Plain http is only for the Mac app's own loopback line. A local address needs a fingerprint.
    static func pairingCheck(_ url: URL, fingerprint fp: String?) -> PairCheck {
        let loopback = ["127.0.0.1", "localhost", "::1"].contains(url.host?.lowercased() ?? "")
        let scheme = url.scheme?.lowercased() ?? ""
        if scheme == "http" { return loopback ? .go(candidate: nil) : .refuse(insecure) }
        let fp = fp.flatMap(normalized)
        if let stored = pin(for: url) {
            if let fp, fp != stored { return .refuse(mismatch) }
            return .go(candidate: nil)
        }
        if let fp { return .go(candidate: fp) }
        return isLocal(url) ? .refuse(unpinned) : .go(candidate: nil)
    }

    /// Starts trusting a candidate for one pairing request.
    static func beginPairing(_ url: URL, candidate: String?) {
        guard let candidate, let id = id(url) else { return }
        candidateLock.lock(); candidates[id] = candidate; candidateLock.unlock()
    }

    /// Ends the pairing request: the candidate becomes the pin only when the pairing worked.
    static func finishPairing(_ url: URL, succeeded: Bool) {
        guard let id = id(url) else { return }
        candidateLock.lock()
        let c = candidates.removeValue(forKey: id)
        candidateLock.unlock()
        if succeeded, let c, pin(for: url) == nil { setPin(c, for: url.absoluteString) }
    }

    /// 64 hex digits, lower case, with any colons or spaces taken out; nil for anything else.
    static func normalized(_ fp: String) -> String? {
        let hex = fp.lowercased().filter { !":- ".contains($0) }
        return hex.count == 64 && hex.allSatisfy(\.isHexDigit) ? hex : nil
    }

    /// An address on the local network (a Mac home), where a connection without a pin is refused.
    static func isLocal(_ url: URL) -> Bool {
        guard let host = url.host?.lowercased() else { return false }
        if host == "127.0.0.1" || host == "localhost" || host == "::1" { return false }
        return host.hasSuffix(".local") || host.hasPrefix("10.") || host.hasPrefix("192.168.")
            || host.range(of: #"^172\.(1[6-9]|2[0-9]|3[01])\."#, options: .regularExpression) != nil
    }

    /// The SHA-256 of the server's own (leaf) certificate.
    static func leafFingerprint(_ trust: SecTrust) -> String? {
        guard let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate], let leaf = chain.first else {
            return nil
        }
        let der = SecCertificateCopyData(leaf) as Data
        return SHA256.hash(data: der).map { String(format: "%02x", $0) }.joined()
    }

    /// What to do with a server's certificate: the pinned one for a pinned address, nothing at all for an unpinned
    /// address on the local network, and the system's own check everywhere else.
    static func decide(host: String, port: Int, trust: SecTrust, pin: String?, local: Bool)
        -> (URLSession.AuthChallengeDisposition, URLCredential?) {
        if let pin {
            if let fp = leafFingerprint(trust), fp == pin { return (.useCredential, URLCredential(trust: trust)) }
            noteRefusal()
            return (.cancelAuthenticationChallenge, nil)
        }
        if local { noteRefusal(); return (.cancelAuthenticationChallenge, nil) }
        return (.performDefaultHandling, nil)
    }
}

/// The session every request to a home goes through, so pins apply to chats, streams, media and the Mac's link.
final class HubSessionDelegate: NSObject, URLSessionDelegate {
    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        let space = challenge.protectionSpace
        guard space.authenticationMethod == NSURLAuthenticationMethodServerTrust, let trust = space.serverTrust,
              let url = URL(string: "https://\(space.host):\(space.port)") else {
            return completionHandler(.performDefaultHandling, nil)
        }
        let d = HubTrust.decide(host: space.host, port: space.port, trust: trust, pin: HubTrust.effectivePin(for: url),
                                local: HubTrust.isLocal(url))
        completionHandler(d.0, d.1)
    }
}

enum HubSession {
    static let shared = URLSession(configuration: .default, delegate: HubSessionDelegate(), delegateQueue: nil)
}
