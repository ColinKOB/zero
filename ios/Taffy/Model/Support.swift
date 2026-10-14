import Foundation
import Security

enum Keychain {
    private static let service = "com.example.Taffy"

    static func read(_ key: String) -> String? {
        if let v = keychainRead(key) { return v }
        #if targetEnvironment(macCatalyst)
        if let v = LegacyFile.read(key) {
            keychainWrite(key, v)
            if keychainRead(key) == v { LegacyFile.delete(key) }
            return v
        }
        #endif
        return nil
    }

    static func write(_ key: String, _ value: String) {
        keychainWrite(key, value)
        #if targetEnvironment(macCatalyst)
        if keychainRead(key) == value { LegacyFile.delete(key) } else { LegacyFile.write(key, value) }
        #endif
    }

    static func writeKeychainOnly(_ key: String, _ value: String) -> Bool {
        keychainWrite(key, value) == errSecSuccess && keychainRead(key) == value
    }

    static func readKeychainOnly(_ key: String) -> String? { keychainRead(key) }

    static func delete(_ key: String) {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: key]
        SecItemDelete(q as CFDictionary)
        #if targetEnvironment(macCatalyst)
        LegacyFile.delete(key)
        #endif
    }

    private static func keychainRead(_ key: String) -> String? {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: key, kSecReturnData as String: true]
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let d = out as? Data else { return nil }
        return String(data: d, encoding: .utf8)
    }

    @discardableResult
    private static func keychainWrite(_ key: String, _ value: String) -> OSStatus {
        let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                   kSecAttrAccount as String: key]
        SecItemDelete(base as CFDictionary)
        var q = base
        q[kSecValueData as String] = Data(value.utf8)
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(q as CFDictionary, nil)
    }

    #if targetEnvironment(macCatalyst)
    private enum LegacyFile {
        static var url: URL {
            FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("Taffy", isDirectory: true).appendingPathComponent("secrets.json")
        }

        static func load() -> [String: String] {
            guard let d = try? Data(contentsOf: url) else { return [:] }
            return (try? JSONDecoder().decode([String: String].self, from: d)) ?? [:]
        }

        static func save(_ values: [String: String]) {
            if values.isEmpty { try? FileManager.default.removeItem(at: url); return }
            try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
                                                     attributes: [.posixPermissions: 0o700])
            guard let d = try? JSONEncoder().encode(values) else { return }
            try? d.write(to: url, options: .atomic)
            try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        }

        static func read(_ key: String) -> String? { load()[key] }
        static func write(_ key: String, _ value: String) { var v = load(); v[key] = value; save(v) }
        static func delete(_ key: String) { var v = load(); guard v[key] != nil else { return }; v[key] = nil; save(v) }
    }
    #endif
}



import Network

final class NetworkWatch: @unchecked Sendable {
    static let shared = NetworkWatch()
    private let monitor = NWPathMonitor()
    private(set) var constrained = false

    private init() {
        monitor.pathUpdateHandler = { [weak self] path in
            self?.constrained = path.isExpensive || path.isConstrained
        }
        monitor.start(queue: DispatchQueue(label: "taffy.network"))
    }
}
