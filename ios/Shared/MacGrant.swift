import CryptoKit
import Foundation

/// What an iPhone signs to allow a Zip's task on the Mac, and how the Mac checks it. One file, built into both the
/// iPhone app (MacApprover.swift) and the Mac bridge (TaffyMacBridge+Phone.swift), so the two can never disagree.
enum MacGrant {
    /// The signed words: a JSON array of the request's fields, so a task or a quote with a new line in it cannot be
    /// read as a different set of fields.
    static func message(id: String, bot: String, task: String, said: String, expires: Int) -> Data {
        let fields = ["zero-mac-grant/2", id, bot, task, said, String(expires)]
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.withoutEscapingSlashes]
        return (try? encoder.encode(fields)) ?? Data()
    }

    /// True only for a signature by `key` over exactly this request, before it expires.
    static func verify(key: P256.Signing.PublicKey, signature: Data, id: String, bot: String, task: String,
                       said: String, expires: Int, now: Date = Date()) -> Bool {
        guard Int(now.timeIntervalSince1970) <= expires,
              let sig = try? P256.Signing.ECDSASignature(derRepresentation: signature) else { return false }
        return key.isValidSignature(sig, for: message(id: id, bot: bot, task: task, said: said, expires: expires))
    }

    /// A phone key's short fingerprint, shown on the Mac's trust card and in the iPhone's Settings so Colin can see
    /// they are the same key: the first 8 bytes of SHA-256 of the public key, as four groups of four characters.
    static func fingerprint(_ publicKeyX963: Data) -> String {
        let hex = SHA256.hash(data: publicKeyX963).prefix(8).map { String(format: "%02X", $0) }.joined()
        return stride(from: 0, to: 16, by: 4).map { i in
            String(hex[hex.index(hex.startIndex, offsetBy: i)..<hex.index(hex.startIndex, offsetBy: i + 4)])
        }.joined(separator: " ")
    }
}
