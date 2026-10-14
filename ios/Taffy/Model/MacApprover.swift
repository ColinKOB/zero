import CryptoKit
import Foundation
import LocalAuthentication
import Security

struct MacRequest: Codable, Hashable, Identifiable {
    let id: String
    let bot_id: String
    let bot: String
    let task: String
    let said: String
    let said_at: Double?
    let expires: Int
    let state: String
    let via: String?
    let feed_id: Int?
}

struct MacState: Decodable, Equatable {
    struct Grant: Decodable, Equatable { let bot_id: String; let bot: String; let task: String; let minutes: Int }
    let mac_online: Bool
    let approvals: [MacRequest]
    let grant: Grant?
}

@MainActor
final class MacApprover {
    static let shared = MacApprover()

    private static let service = "zero.mac-approver"
    private var registered = false

    var available: Bool { !ProcessInfo.processInfo.isMacCatalystApp && SecureEnclave.isAvailable }

    func register(client: HubClient, name: String) async {
        guard available, !registered, let key = try? publicKey() else { return }
        if (try? await client.registerMacApprover(key: key.x963Representation.base64EncodedString(), name: name)) != nil {
            registered = true
        }
    }

    func fingerprint() -> String? {
        guard available, let key = try? publicKey() else { return nil }
        return MacGrant.fingerprint(key.x963Representation)
    }

    func allow(_ request: MacRequest, client: HubClient) async throws -> String {
        guard available else { throw HubClient.HubError.http(400, "This device can't allow Mac use. Allow it on the Mac.") }
        let context = LAContext()
        context.localizedReason = "Allow \(request.bot) to use your Mac"
        let key = try privateKey(context: context)
        let message = MacGrant.message(id: request.id, bot: request.bot, task: request.task, said: request.said,
                                       expires: request.expires)
        let signature = try key.signature(for: message)   // Face ID or the passcode happens here
        let keyID = try await client.macKeyID()
        return try await client.answerMac(request.id, allow: true, keyID: keyID,
                                          signature: signature.derRepresentation.base64EncodedString())
    }

    // MARK: The key

    private func publicKey() throws -> P256.Signing.PublicKey { try privateKey(context: nil).publicKey }

    /// The Secure Enclave key, made the first time. Its stored form only works in this phone's Secure Enclave.
    private func privateKey(context: LAContext?) throws -> SecureEnclave.P256.Signing.PrivateKey {
        if let data = Self.load() {
            return try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: data, authenticationContext: context)
        }
        var error: Unmanaged<CFError>?
        guard let access = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
                                                           [.privateKeyUsage, .userPresence], &error) else {
            throw error!.takeRetainedValue() as Error
        }
        let key = try SecureEnclave.P256.Signing.PrivateKey(accessControl: access, authenticationContext: context)
        Self.save(key.dataRepresentation)
        return key
    }

    private static func load() -> Data? {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var out: AnyObject?
        return SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess ? out as? Data : nil
    }

    private static func save(_ data: Data) {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service]
        SecItemDelete(q as CFDictionary)
        var add = q
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        SecItemAdd(add as CFDictionary, nil)
    }
}

extension HubClient {
    func macState() async throws -> MacState {
        try await send(try request("/api/mac/approvals"), as: MacState.self)
    }

    func answerMac(_ id: String, allow: Bool, keyID: String = "", signature: String = "") async throws -> String {
        struct Body: Encodable { let allow: Bool; let key_id: String; let signature: String }
        struct Resp: Decodable { let text: String }
        return try await send(try request("/api/mac/approvals/\(id)", method: "POST",
                                          body: Body(allow: allow, key_id: keyID, signature: signature)), as: Resp.self).text
    }

    func registerMacApprover(key: String, name: String) async throws {
        struct Body: Encodable { let key: String; let name: String }
        struct Resp: Decodable { let ok: Bool }
        _ = try await send(try request("/api/mac/approver", method: "POST", body: Body(key: key, name: name)), as: Resp.self)
    }

    func macKeyID() async throws -> String {
        guard let token else { throw HubError.notPaired }
        return SHA256.hash(data: Data(token.utf8)).map { String(format: "%02x", $0) }.joined().prefix(16).description
    }

    func stopMac() async throws -> String {
        struct Resp: Decodable { let text: String }
        return try await send(try request("/api/mac/stop", method: "POST"), as: Resp.self).text
    }
}
