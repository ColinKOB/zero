import CryptoKit
import Foundation

enum MacGrant {
    static func message(id: String, bot: String, task: String, said: String, expires: Int) -> Data {
        let fields = ["zero-mac-grant/2", id, bot, task, said, String(expires)]
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.withoutEscapingSlashes]
        return (try? encoder.encode(fields)) ?? Data()
    }

    static func verify(key: P256.Signing.PublicKey, signature: Data, id: String, bot: String, task: String,
                       said: String, expires: Int, now: Date = Date()) -> Bool {
        guard Int(now.timeIntervalSince1970) <= expires,
              let sig = try? P256.Signing.ECDSASignature(derRepresentation: signature) else { return false }
        return key.isValidSignature(sig, for: message(id: id, bot: bot, task: task, said: said, expires: expires))
    }

    static func fingerprint(_ publicKeyX963: Data) -> String {
        let hex = SHA256.hash(data: publicKeyX963).prefix(8).map { String(format: "%02X", $0) }.joined()
        return stride(from: 0, to: 16, by: 4).map { i in
            String(hex[hex.index(hex.startIndex, offsetBy: i)..<hex.index(hex.startIndex, offsetBy: i + 4)])
        }.joined(separator: " ")
    }
}
