import CryptoKit
import Foundation
import Security

enum HubTrust {
    private static let key = "hubPins"

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

    static func removePin(for server: String) {
        guard let url = URL(string: server), let id = id(url) else { return }
        var pins = (UserDefaults.standard.dictionary(forKey: key) as? [String: String]) ?? [:]
        pins[id] = nil
        UserDefaults.standard.set(pins, forKey: key)
    }

    // MARK: Pairing

    private static let candidateLock = NSLock()
    nonisolated(unsafe) private static var candidates: [String: String] = [:]

    static func candidate(for url: URL) -> String? {
        guard let id = id(url) else { return nil }
        candidateLock.lock(); defer { candidateLock.unlock() }
        return candidates[id]
    }

    static func effectivePin(for url: URL) -> String? { pin(for: url) ?? candidate(for: url) }

    enum PairCheck: Equatable {
        case go(candidate: String?)
        case refuse(String)
    }

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

    static func beginPairing(_ url: URL, candidate: String?) {
        guard let candidate, let id = id(url) else { return }
        candidateLock.lock(); candidates[id] = candidate; candidateLock.unlock()
    }

    static func finishPairing(_ url: URL, succeeded: Bool) {
        guard let id = id(url) else { return }
        candidateLock.lock()
        let c = candidates.removeValue(forKey: id)
        candidateLock.unlock()
        if succeeded, let c, pin(for: url) == nil { setPin(c, for: url.absoluteString) }
    }

    static func normalized(_ fp: String) -> String? {
        let hex = fp.lowercased().filter { !":- ".contains($0) }
        return hex.count == 64 && hex.allSatisfy(\.isHexDigit) ? hex : nil
    }

    static func isLocal(_ url: URL) -> Bool {
        guard let host = url.host?.lowercased() else { return false }
        if host == "127.0.0.1" || host == "localhost" || host == "::1" { return false }
        return host.hasSuffix(".local") || host.hasPrefix("10.") || host.hasPrefix("192.168.")
            || host.range(of: #"^172\.(1[6-9]|2[0-9]|3[01])\."#, options: .regularExpression) != nil
    }

    static func leafFingerprint(_ trust: SecTrust) -> String? {
        guard let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate], let leaf = chain.first else {
            return nil
        }
        let der = SecCertificateCopyData(leaf) as Data
        return SHA256.hash(data: der).map { String(format: "%02x", $0) }.joined()
    }

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
