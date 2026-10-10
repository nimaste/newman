import Foundation
import Security

/// Small string store in the Keychain (GChat's KeychainService). The phone app and its
/// notification extension share items through the App Group `group.ca.nimaste.newman` (an App
/// Group is also a valid keychain access group), so the extension can present the same client
/// certificate. Each target names its group in Info.plist (`NewmanKeychainGroup`); the wall app
/// has none and keeps its own Keychain.
enum KeychainService {
    private static let service = "ca.nimaste.newman"

    static var accessGroup: String? {
        guard let g = Bundle.main.object(forInfoDictionaryKey: "NewmanKeychainGroup") as? String,
              !g.isEmpty, !g.contains("$(") else { return nil }
        return g
    }

    private static func baseQuery(_ key: String) -> [String: Any] {
        var q: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
        if let g = accessGroup { q[kSecAttrAccessGroup as String] = g }
        return q
    }

    static func save(key: String, value: String) {
        saveData(key: key, value: Data(value.utf8))
    }

    static func get(key: String) -> String? {
        guard let d = getData(key: key) else { return nil }
        return String(data: d, encoding: .utf8)
    }

    static func saveData(key: String, value: Data) {
        let query = baseQuery(key)
        SecItemDelete(query as CFDictionary)
        var item = query
        item[kSecValueData as String] = value
        // Readable by the notification extension while the phone is locked (after first unlock).
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(item as CFDictionary, nil)
    }

    static func getData(key: String) -> Data? {
        var q = baseQuery(key)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &result) == errSecSuccess else { return nil }
        return result as? Data
    }

    static func delete(key: String) {
        SecItemDelete(baseQuery(key) as CFDictionary)
    }
}
