import Foundation

/// Where the hub is and how to reach it: the hub and the address in use, the optional Cloudflare
/// Access service token, the session token and this install's device id - all in the Keychain
/// (shared with the notification and location push extensions on the phone).
final class ConnectionStore {
    static let shared = ConnectionStore()

    private enum K {
        static let server = "server_url"
        static let cfId = "cf_access_client_id"
        static let cfSecret = "cf_access_client_secret"
        static let token = "session_token"
        static let deviceId = "device_id"
        static let hubId = "hub_id"
        static let hubName = "hub_name"
        static let hubLocal = "hub_local_url"
        static let hubOutside = "hub_outside_url"
    }

    private init() {}

    /// The address in use right now (one of `hub`'s, picked by the app's HubRoute; or the only
    /// address an older install saved). Everything - app, extensions, wall - talks to this one.
    var serverURL: URL? {
        get { KeychainService.get(key: K.server).flatMap { ServerAddress.normalize($0) } }
        set {
            if let u = newValue { KeychainService.save(key: K.server, value: u.absoluteString) } else { KeychainService.delete(key: K.server) }
        }
    }

    /// The hub chosen on the Server screen: its id, name, home and outside addresses. nil on
    /// installs that saved only an address (the app learns the rest from GET /api/hello).
    var hub: SavedHub? {
        get {
            guard let id = nonEmpty(KeychainService.get(key: K.hubId)) else { return nil }
            let local = nonEmpty(KeychainService.get(key: K.hubLocal)).flatMap { ServerAddress.normalize($0) }
            let outside = nonEmpty(KeychainService.get(key: K.hubOutside)).flatMap { ServerAddress.normalize($0) }
            guard local != nil || outside != nil else { return nil }
            return SavedHub(id: id, name: KeychainService.get(key: K.hubName) ?? "", localURL: local, outsideURL: outside)
        }
        set {
            guard let h = newValue else {
                for k in [K.hubId, K.hubName, K.hubLocal, K.hubOutside] { KeychainService.delete(key: k) }
                return
            }
            KeychainService.save(key: K.hubId, value: h.id)
            KeychainService.save(key: K.hubName, value: h.name)
            if let l = h.localURL { KeychainService.save(key: K.hubLocal, value: l.absoluteString) } else { KeychainService.delete(key: K.hubLocal) }
            if let o = h.outsideURL { KeychainService.save(key: K.hubOutside, value: o.absoluteString) } else { KeychainService.delete(key: K.hubOutside) }
        }
    }

    /// Every address the hub may be reached at, the one in use first.
    var addresses: [URL] {
        var list: [URL] = []
        for u in [serverURL].compactMap({ $0 }) + (hub?.addresses ?? []) where !list.contains(u) { list.append(u) }
        return list
    }

    var cfClientId: String? { nonEmpty(KeychainService.get(key: K.cfId)) }
    var cfClientSecret: String? { nonEmpty(KeychainService.get(key: K.cfSecret)) }
    var hasCloudflareToken: Bool { cfClientId != nil && cfClientSecret != nil }

    func setCloudflare(id: String?, secret: String?) {
        let i = nonEmpty(id?.trimmingCharacters(in: .whitespacesAndNewlines))
        let s = nonEmpty(secret?.trimmingCharacters(in: .whitespacesAndNewlines))
        if let i, let s {
            KeychainService.save(key: K.cfId, value: i)
            KeychainService.save(key: K.cfSecret, value: s)
        } else {
            KeychainService.delete(key: K.cfId)
            KeychainService.delete(key: K.cfSecret)
        }
    }

    var token: String? {
        get { nonEmpty(KeychainService.get(key: K.token)) }
        set {
            if let t = newValue, !t.isEmpty { KeychainService.save(key: K.token, value: t) } else { KeychainService.delete(key: K.token) }
        }
    }

    /// This install's id for /api/devices/register and notification buttons; made once.
    var deviceId: String {
        if let d = nonEmpty(KeychainService.get(key: K.deviceId)) { return d }
        let d = "ios-" + UUID().uuidString.lowercased()
        KeychainService.save(key: K.deviceId, value: d)
        return d
    }

    /// The Cloudflare Access headers, when a service token is set.
    var cloudflareHeaders: [String: String] {
        guard let i = cfClientId, let s = cfClientSecret else { return [:] }
        return ["CF-Access-Client-Id": i, "CF-Access-Client-Secret": s]
    }

    /// Headers every request to the hub carries: Cloudflare Access (if set) and the session.
    func headers(includeToken: Bool = true) -> [String: String] {
        var h = cloudflareHeaders
        if includeToken, let t = token { h["Authorization"] = "Bearer \(t)" }
        return h
    }

    private func nonEmpty(_ s: String?) -> String? {
        guard let s, !s.isEmpty else { return nil }
        return s
    }
}
