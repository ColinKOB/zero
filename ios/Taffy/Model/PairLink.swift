import Foundation

// MARK: Pairing links and codes

/// `zero://pair?server=https://host:port&code=ABCD2345`, from a QR code or a link in a message. A Mac home's link
/// adds `fp`, its certificate's SHA-256 fingerprint. Reading a link stores nothing: the fingerprint is trusted for the
/// pairing request alone and kept as the home's pin only after that pairing works (HubTrust.pairingCheck).
struct PairLink: Equatable {
    let server: String
    let code: String
    var fingerprint: String? = nil

    static func parse(_ text: String) -> PairLink? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let candidate: String = {
            guard let r = trimmed.range(of: "zero://", options: .caseInsensitive) else { return trimmed }
            return String(trimmed[r.lowerBound...].prefix { !$0.isWhitespace })
        }()
        guard let comps = URLComponents(string: candidate), comps.scheme?.lowercased() == "zero",
              (comps.host?.lowercased() == "pair" || comps.path.lowercased().hasPrefix("pair")) else { return nil }
        let items = comps.queryItems ?? []
        let server = items.first { $0.name == "server" }?.value?.trimmingCharacters(in: .whitespaces) ?? ""
        let code = (items.first { $0.name == "code" }?.value ?? "").uppercased().filter { $0.isLetter || $0.isNumber }
        guard !server.isEmpty || !code.isEmpty else { return nil }
        let fp = (items.first { $0.name == "fp" }?.value).flatMap(HubTrust.normalized)
        return PairLink(server: server, code: code, fingerprint: fp)
    }

    /// The link for a code made on this device, so the new device gets the address with it.
    var url: URL? {
        var c = URLComponents()
        c.scheme = "zero"
        c.host = "pair"
        c.queryItems = [URLQueryItem(name: "server", value: server), URLQueryItem(name: "code", value: code)]
        if let fp = fingerprint ?? URL(string: server).flatMap(HubTrust.pin(for:)) {
            c.queryItems?.append(URLQueryItem(name: "fp", value: fp))
        }
        return c.url
    }
}
