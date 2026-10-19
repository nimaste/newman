import Foundation
import WidgetKit

/// How the widgets, Control Center controls and Siri actions reach the hub - from the widget
/// extension or the app, signed in or not running. Everything comes from the Keychain shared with
/// the app (group.ca.nimaste.newman, like the notification and location push extensions): the
/// address in use (the hub's other saved address as a fallback when it can't be reached at all),
/// the session token, the Cloudflare Access headers and the mTLS client certificate
/// (NewmanURLSession presents it). Small caches live in the App Group's defaults so a widget still
/// shows something while the hub can't be reached.
enum WidgetHub {
    static let appGroup = "group.ca.nimaste.newman"
    /// The phone app's bundle id: the APNs topic for widget pushes is "<this>.push-type.widgets".
    static let appBundleId = "ca.nimaste.newman"

    static var defaults: UserDefaults? { UserDefaults(suiteName: appGroup) }

    private enum K {
        static let meta = "widgets.meta"
        static let metaAt = "widgets.metaAt"
        static let states = "widgets.states"
        static let home = "widgets.home"
        static let catalog = "widgets.catalog"
        static let lastAction = "widgets.lastActionAt"
        static let pushToken = "widgets.pushToken"
        static let pushStamp = "widgets.pushStamp"
    }

    static var isSignedIn: Bool { ConnectionStore.shared.token != nil && ConnectionStore.shared.serverURL != nil }

    // MARK: calls

    /// One call: the address in use, then the hub's other address if the first can't be reached.
    static func send(_ method: String, _ path: String, body: Data? = nil, timeout: TimeInterval = 12) async throws -> (Int, Data) {
        let store = ConnectionStore.shared
        let addresses = store.addresses
        guard !addresses.isEmpty, store.token != nil else {
            throw APIError(status: 401, message: "Open Newman and sign in.", signInRequired: true)
        }
        var lastError: Error = URLError(.cannotConnectToHost)
        for (i, base) in addresses.enumerated() {
            guard let url = ServerAddress.url(base, path) else { continue }
            var req = NewmanURLSession.request(url, method: method, body: body ?? (method == "GET" ? nil : Data("{}".utf8)))
            // Leave time for the other address.
            req.timeoutInterval = addresses.count > 1 && i == 0 ? min(timeout, 6) : timeout
            do {
                let (data, resp) = try await NewmanURLSession.shared.data(for: req)
                return ((resp as? HTTPURLResponse)?.statusCode ?? 0, data)
            } catch {
                lastError = error
            }
        }
        throw APIError.network(lastError)
    }

    /// A call that must succeed: the hub's own words otherwise.
    @discardableResult
    static func checked(_ method: String, _ path: String, body: Data? = nil) async throws -> Data {
        let (status, data) = try await send(method, path, body: body)
        guard (200..<300).contains(status) else {
            let signIn = JSONBody.isSignInRequired(status: status, data: data)
            let fallback: String
            switch status {
            case 401: fallback = "Open Newman and sign in again."
            case 403: fallback = "Your account can't do that."
            case 404: fallback = "Not found."
            case 502, 503, 504: fallback = "The Newman hub isn't answering right now."
            default: fallback = "The hub said no (\(status))."
            }
            throw APIError(status: status, message: JSONBody.errorText(data) ?? fallback, signInRequired: signIn)
        }
        return data
    }

    // MARK: kinds (GET /api/meta, kept for 6 hours)

    static func kinds(maxAge: TimeInterval = 6 * 3600) async -> [String: Kind] {
        let d = defaults
        let at = d?.double(forKey: K.metaAt) ?? 0
        if let cached = d?.data(forKey: K.meta), Date().timeIntervalSince1970 - at < maxAge,
           let meta = try? JSONDecoder().decode(Meta.self, from: cached) {
            return meta.kindsById
        }
        if let data = try? await checked("GET", "api/meta"), let meta = try? JSONDecoder().decode(Meta.self, from: data) {
            d?.set(data, forKey: K.meta)
            d?.set(Date().timeIntervalSince1970, forKey: K.metaAt)
            return meta.kindsById
        }
        if let cached = d?.data(forKey: K.meta), let meta = try? JSONDecoder().decode(Meta.self, from: cached) {
            return meta.kindsById // stale beats nothing
        }
        return [:]
    }

    static func entities() async throws -> [Entity] {
        try JSONDecoder().decode([Entity].self, from: try await checked("GET", "api/entities"))
    }

    // MARK: device states (GET /api/widgets/state?ids=…)

    /// The devices' current state, in the order asked for. Older hubs without the widget endpoint:
    /// built from /api/entities and the kinds. Kept, so an unreachable hub still shows the last state.
    static func states(ids: [String]) async throws -> [WidgetDevice] {
        guard !ids.isEmpty else { return [] }
        let allowed = CharacterSet.urlQueryAllowed.subtracting(CharacterSet(charactersIn: "&,=+#"))
        let list = ids.map { $0.addingPercentEncoding(withAllowedCharacters: allowed) ?? $0 }.joined(separator: ",")
        let (status, data) = try await send("GET", "api/widgets/state?ids=\(list)")
        var devices: [WidgetDevice]
        if (200..<300).contains(status), let parsed = WidgetStateReply.parse(data) {
            devices = parsed
        } else if status == 404 {
            let kindMap = await kinds()
            let all = try await entities()
            devices = all.filter { ids.contains($0.id) }.map { WidgetLogic.device($0, kindMap[$0.kind]) }
        } else {
            let signIn = JSONBody.isSignInRequired(status: status, data: data)
            throw APIError(status: status, message: JSONBody.errorText(data) ?? "The hub couldn't send the devices (\(status)).",
                           signInRequired: signIn)
        }
        devices = WidgetLogic.ordered(devices, ids: ids)
        remember(devices)
        return devices
    }

    static func cachedStates(ids: [String]) -> [WidgetDevice] {
        guard let data = defaults?.data(forKey: K.states),
              let all = try? JSONDecoder().decode([String: WidgetDevice].self, from: data) else { return [] }
        return ids.compactMap { all[$0] }
    }

    private static func remember(_ devices: [WidgetDevice]) {
        var all: [String: WidgetDevice] = [:]
        if let data = defaults?.data(forKey: K.states), let old = try? JSONDecoder().decode([String: WidgetDevice].self, from: data) {
            all = old
        }
        for d in devices { all[d.id] = d }
        if all.count > 200 { all = Dictionary(uniqueKeysWithValues: devices.map { ($0.id, $0) }) }
        if let data = try? JSONEncoder().encode(all) { defaults?.set(data, forKey: K.states) }
    }

    // MARK: the "Home status" widget (GET /api/widgets/home)

    /// Is everything secure, what isn't and what fixes it. Kept, so an unreachable hub still shows the last one.
    static func home() async throws -> WidgetHome {
        let data = try await checked("GET", "api/widgets/home")
        guard let home = WidgetHome.parse(data) else {
            throw APIError(status: 0, message: "Newman's reply couldn't be read.", signInRequired: false)
        }
        defaults?.set(data, forKey: K.home)
        return home
    }

    static var cachedHome: WidgetHome? {
        guard let data = defaults?.data(forKey: K.home) else { return nil }
        return WidgetHome.parse(data)
    }

    // MARK: commands (POST /api/entities/{id}/command, as the app sends them)

    static func run(deviceId: String, command: String) async throws {
        let id = deviceId.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(CharacterSet(charactersIn: "/?#"))) ?? deviceId
        try await checked("POST", "api/entities/\(id)/command", body: JSONBody.encode(["command": JSONValue.string(command)]))
        defaults?.set(Date().timeIntervalSince1970, forKey: K.lastAction)
    }

    static var lastActionAt: Double? {
        let v = defaults?.double(forKey: K.lastAction) ?? 0
        return v > 0 ? v : nil
    }

    // MARK: the device list for pickers (all of the hub's devices a widget can show)

    struct CatalogItem: Codable, Hashable {
        var id: String
        var name: String
        var detail: String
        var icon: String
        var section: String
        var kind: String
    }

    /// Every showable device, grouped by room / type; the last good list when the hub can't be reached.
    static func catalog() async -> [CatalogItem] {
        do {
            let kindMap = await kinds()
            let all = try await entities()
            let byId = Dictionary(all.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
            var items: [CatalogItem] = []
            for s in WidgetLogic.sections(all, kinds: kindMap) {
                for id in s.ids {
                    guard let e = byId[id] else { continue }
                    let k = kindMap[e.kind]
                    items.append(CatalogItem(id: e.id, name: e.name, detail: WidgetLogic.detail(e, k),
                                             icon: DeviceLogic.icon(e, k, kinds: kindMap), section: s.title, kind: e.kind))
                }
            }
            if let data = try? JSONEncoder().encode(items) { defaults?.set(data, forKey: K.catalog) }
            return items
        } catch {
            return cachedCatalog
        }
    }

    static var cachedCatalog: [CatalogItem] {
        guard let data = defaults?.data(forKey: K.catalog),
              let items = try? JSONDecoder().decode([CatalogItem].self, from: data) else { return [] }
        return items
    }

    // MARK: widget push token (POST /api/widgets/push-token)

    /// WidgetKit gave the extension a push token: kept for the app, and sent now if signed in.
    static func pushTokenChanged(_ token: Data) async {
        let hex = token.map { String(format: "%02x", $0) }.joined()
        defaults?.set(hex, forKey: K.pushToken)
        await registerPushTokenIfNeeded()
    }

    /// Sends the widget push token to the hub when it, the address or the session changed since
    /// the last time (the app calls this after signing in and on every start).
    static func registerPushTokenIfNeeded() async {
        let store = ConnectionStore.shared
        guard let token = defaults?.string(forKey: K.pushToken), !token.isEmpty,
              let session = store.token, let server = store.serverURL?.absoluteString else { return }
        let stamp = WidgetLogic.registrationStamp(token: token, server: server, session: session)
        guard defaults?.string(forKey: K.pushStamp) != stamp else { return }
        #if DEBUG
        let sandbox = true
        #else
        let sandbox = false
        #endif
        let body = WidgetLogic.pushTokenBody(token: token, deviceId: store.deviceId, sandbox: sandbox, appBundleId: appBundleId)
        if (try? await checked("POST", "api/widgets/push-token", body: JSONBody.encode(body))) != nil {
            defaults?.set(stamp, forKey: K.pushStamp)
        }
    }
}

/// Asks WidgetKit to redraw the widgets and controls (after an action, when the app goes to the
/// background, or when the hub's widget push arrives - WidgetKit does that one itself).
enum WidgetRefresh {
    static func reloadAll() {
        WidgetCenter.shared.reloadAllTimelines()
        #if compiler(>=6.0)
        if #available(iOS 18.0, *) { ControlCenter.shared.reloadAllControls() }
        #endif
    }
}
