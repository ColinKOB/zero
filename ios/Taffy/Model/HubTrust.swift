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
            return (.cancelAuthenticationChallenge, nil)
        }
        if local { return (.cancelAuthenticationChallenge, nil) }
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
        let d = HubTrust.decide(host: space.host, port: space.port, trust: trust, pin: HubTrust.pin(for: url),
                                local: HubTrust.isLocal(url))
        completionHandler(d.0, d.1)
    }
}

enum HubSession {
    static let shared = URLSession(configuration: .default, delegate: HubSessionDelegate(), delegateQueue: nil)
}
