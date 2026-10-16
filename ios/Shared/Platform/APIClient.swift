import Foundation

/// A failed call to the hub, with the hub's own words when it gave some.
struct APIError: LocalizedError {
    var status: Int
    var message: String
    var signInRequired: Bool = false

    var errorDescription: String? { message }

    static func network(_ error: Error) -> APIError {
        let ns = error as NSError
        if ns.domain == NSURLErrorDomain {
            switch ns.code {
            case NSURLErrorNotConnectedToInternet: return APIError(status: 0, message: "You're offline.")
            case NSURLErrorTimedOut: return APIError(status: 0, message: "The Newman hub took too long to answer.")
            case NSURLErrorClientCertificateRequired, NSURLErrorClientCertificateRejected:
                return APIError(status: 0, message: "The server wants a valid client certificate.")
            case NSURLErrorSecureConnectionFailed, NSURLErrorServerCertificateUntrusted:
                return APIError(status: 0, message: "A secure connection couldn't be made (\(ns.localizedDescription)).")
            default: break
            }
        }
        return APIError(status: 0, message: "Can't reach the Newman hub. \(ns.localizedDescription)")
    }
}

extension Notification.Name {
    /// The hub said the session is gone (401 {signIn: true}).
    static let newmanSignedOut = Notification.Name("ca.nimaste.newman.signedOut")
}

/// JSON calls to the hub (API.md), through the mTLS session with the Cloudflare and Bearer headers.
final class APIClient {
    static let shared = APIClient()

    var session: URLSession { NewmanURLSession.shared }
    var base: URL? { ConnectionStore.shared.serverURL }

    func url(_ path: String) throws -> URL {
        guard let base, let u = ServerAddress.url(base, path) else {
            throw APIError(status: 0, message: "No Newman hub is set up yet.")
        }
        return u
    }

    /// Raw call: (status, body). Network failures throw APIError.
    func send(_ method: String, _ path: String, body: Data? = nil, includeToken: Bool = true) async throws -> (Int, Data) {
        let req = NewmanURLSession.request(try url(path), method: method, body: body ?? (method == "GET" ? nil : Data("{}".utf8)),
                                           includeToken: includeToken)
        do {
            let (data, resp) = try await session.data(for: req)
            return ((resp as? HTTPURLResponse)?.statusCode ?? 0, data)
        } catch {
            throw APIError.network(error)
        }
    }

    /// Checked call: throws the hub's `error` text for non-2xx replies.
    @discardableResult
    func call(_ method: String, _ path: String, body: [String: JSONValue]? = nil) async throws -> Data {
        let (status, data) = try await send(method, path, body: body.map { JSONBody.encode($0) })
        guard (200..<300).contains(status) else {
            let signIn = JSONBody.isSignInRequired(status: status, data: data)
            if signIn { NotificationCenter.default.post(name: .newmanSignedOut, object: nil) }
            let fallback: String
            switch status {
            case 401: fallback = "Please sign in again."
            case 403: fallback = "Your account can't do that."
            case 404: fallback = "Not found."
            case 502, 503, 504: fallback = "The Newman hub isn't answering right now."
            default: fallback = "\(method) \(path) failed (\(status))."
            }
            throw APIError(status: status, message: JSONBody.errorText(data) ?? fallback, signInRequired: signIn)
        }
        return data
    }

    func get<T: Decodable>(_ path: String, as type: T.Type = T.self) async throws -> T {
        let data = try await call("GET", path)
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError(status: 200, message: "The hub's reply to \(path) wasn't understood (\(error.localizedDescription)).")
        }
    }

    // MARK: endpoints

    func meta() async throws -> Meta { try await get("api/meta") }
    func entities() async throws -> [Entity] { try await get("api/entities") }
    func currentDashboard() async throws -> Dashboard { try await get("api/dashboards/current") }

    /// GET /api/phone-layout; never throws (a 404 means an older hub: flow the dashboard).
    func phoneLayout() async -> PhoneGrid.Fetch {
        guard let reply = try? await send("GET", "api/phone-layout") else { return .failed }
        return PhoneGrid.outcome(status: reply.0, body: reply.1)
    }
    func authState() async throws -> AuthState { try await get("api/auth/state") }
    func events(limit: Int = 100) async throws -> [ActivityEvent] { try await get("api/events?limit=\(limit)") }
    func alerts(limit: Int = 30) async throws -> [CameraAlert] { try await get("api/alerts?limit=\(limit)") }
    func pushCategories() async throws -> [PushCategory] { try await get("api/push/categories") }

    /// A camera's own alerts (entity.alerts is the hub's path for them).
    func alerts(path: String, limit: Int = 50) async throws -> [CameraAlert] {
        try await get(path + (path.contains("?") ? "&" : "?") + "limit=\(limit)")
    }

    func command(entityId: String, command: Command, value: JSONValue?) async throws {
        let id = entityId.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(CharacterSet(charactersIn: "/?#"))) ?? entityId
        try await call("POST", "api/entities/\(id)/command", body: JSONBody.command(command, value: value))
    }

    func login(username: String, password: String, code: String?, kind: String, deviceName: String) async throws -> LoginOutcome {
        let body = LoginOutcome.body(username: username, password: password, code: code, kind: kind, deviceName: deviceName)
        let (status, data) = try await send("POST", "api/auth/login", body: JSONBody.encode(body), includeToken: false)
        return LoginOutcome.classify(status: status, body: data)
    }

    func logout() async {
        _ = try? await send("POST", "api/auth/logout")
    }

    // MARK: Today & upkeep (the hub records the signed-in person as who did it)

    func today() async throws -> TodayBriefing { try await get("api/today") }

    private func pathPart(_ s: String) -> String {
        s.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(CharacterSet(charactersIn: "/?#"))) ?? s
    }

    func upkeepDone(_ id: String) async throws { try await call("POST", "api/upkeep/\(pathPart(id))/done", body: [:]) }
    func upkeepUndo(_ id: String) async throws { try await call("POST", "api/upkeep/\(pathPart(id))/undo", body: [:]) }
    func upkeepSnooze(_ id: String, days: Int = 1) async throws {
        try await call("POST", "api/upkeep/\(pathPart(id))/snooze", body: ["days": .number(Double(days))])
    }
    func todayDismiss(_ id: String) async throws { try await call("POST", "api/today/dismiss", body: ["id": .string(id)]) }

    // MARK: Automations (More › Automations; only admins may switch or run them - 403 otherwise)

    func automations() async throws -> [AutomationSummary] { try await get("api/automations") }

    func automationRuns(_ id: String, limit: Int = 10) async throws -> [AutomationRun] {
        let q = id.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed.subtracting(CharacterSet(charactersIn: "&=+?#"))) ?? id
        return try await get("api/runs?automationId=\(q)&limit=\(limit)")
    }

    /// PATCH /api/automations/{id} {"enabled"}: the saved automation back.
    func setAutomation(_ id: String, enabled: Bool) async throws -> AutomationSummary {
        let data = try await call("PATCH", "api/automations/\(pathPart(id))", body: AutomationList.enabledBody(enabled))
        do {
            return try JSONDecoder().decode(AutomationSummary.self, from: data)
        } catch {
            throw APIError(status: 200, message: "The hub's reply wasn't understood.")
        }
    }

    /// POST /api/automations/{id}/run: the run it started (its status, when the hub says).
    func runAutomation(_ id: String) async throws -> AutomationRun? {
        let data = try await call("POST", "api/automations/\(pathPart(id))/run", body: [:])
        return try? JSONDecoder().decode(AutomationRun.self, from: data)
    }

    // MARK: People & location

    func people() async throws -> [Person] { try await get("api/people") }
    func places() async throws -> [Place] { try await get("api/places") }
    /// Tap-to-refresh: the hub asks that person's phone for ONE fresh fix. The raw (status, body):
    /// 202 or 429 {"nextAllowedAt"} (PersonRefresh reads both); network failures throw.
    func refreshPerson(_ personId: String) async throws -> (Int, Data) {
        try await send("POST", "api/people/\(pathPart(personId))/refresh")
    }

    /// PUT /api/me/profile {"color": "#RRGGBB"} (the signed-in person's colour).
    func setMyColor(_ color: String) async throws {
        try await call("PUT", "api/me/profile", body: PersonLook.profileBody(color: color))
    }

    /// PUT /api/me/photo with the picture itself (JPEG); the hub crops it square and makes it 256 px.
    func uploadMyPhoto(_ jpeg: Data) async throws {
        var req = NewmanURLSession.request(try url("api/me/photo"), method: "PUT", body: jpeg)
        req.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        let pair: (Data, URLResponse)
        do {
            pair = try await session.data(for: req)
        } catch {
            throw APIError.network(error)
        }
        let status = (pair.1 as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            throw APIError(status: status, message: JSONBody.errorText(pair.0) ?? (status == 413 ? "That picture is too big." : "The photo wasn't saved (\(status))."))
        }
    }

    func deleteMyPhoto() async throws {
        let (status, data) = try await send("DELETE", "api/me/photo")
        guard (200..<300).contains(status) else {
            throw APIError(status: status, message: JSONBody.errorText(data) ?? "The photo wasn't removed (\(status)).")
        }
    }

    func myLocation() async throws -> LocationSettings { try await get("api/me/location") }

    func setMyLocation(enabled: Bool) async throws -> LocationSettings {
        let data = try await call("PUT", "api/me/location", body: ["enabled": .bool(enabled)])
        return (try? JSONDecoder().decode(LocationSettings.self, from: data)) ?? LocationSettings(mode: "choice", enabled: enabled)
    }

    func reportLocation(_ body: [String: JSONValue]) async throws -> ReportReply {
        let data = try await call("POST", "api/me/location/report", body: body)
        return (try? JSONDecoder().decode(ReportReply.self, from: data)) ?? ReportReply(everySeconds: 300)
    }

    func register(body: [String: JSONValue]) async throws -> RegisterReply {
        let data = try await call("POST", "api/devices/register", body: body)
        return (try? JSONDecoder().decode(RegisterReply.self, from: data)) ?? RegisterReply(ok: true, personId: nil)
    }
}
