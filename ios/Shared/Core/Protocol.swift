import Foundation

// The hub's wire protocol beyond plain JSON models: live messages, sign-in replies, push
// payloads and server addresses. Pure Foundation so it is type-checked and tested on Linux.

// MARK: - Live updates (GET /api/ws)

enum LiveMessage {
    case hello(time: Double?)
    case entity(Entity)
    /// {"type":"entity-removed","id"}: a device was removed from the hub.
    case entityRemoved(String)
    case event(entityId: String?, event: String?)
    case activity(ActivityEvent)
    case dashboard
    case settings
    /// Someone's location changed (the People tab and maps).
    case person(Person)
    case pong
    case unknown(String)

    private struct Envelope: Decodable {
        var type: String
    }

    private struct EntityMsg: Decodable { var entity: Entity }
    private struct PersonMsg: Decodable { var person: Person }
    private struct ActivityMsg: Decodable { var entry: ActivityEvent }
    private struct EventMsg: Decodable {
        var entityId: String?
        var event: String?
    }
    private struct HelloMsg: Decodable { var time: Double? }
    private struct RemovedMsg: Decodable { var id: String }

    /// Parses one text frame; nil for anything that isn't a hub message.
    static func parse(_ text: String) -> LiveMessage? {
        guard let data = text.data(using: .utf8) else { return nil }
        let dec = JSONDecoder()
        guard let env = try? dec.decode(Envelope.self, from: data) else { return nil }
        switch env.type {
        case "hello":
            return .hello(time: (try? dec.decode(HelloMsg.self, from: data))?.time)
        case "entity":
            guard let m = try? dec.decode(EntityMsg.self, from: data) else { return nil }
            return .entity(m.entity)
        case "entity-removed":
            guard let m = try? dec.decode(RemovedMsg.self, from: data), !m.id.isEmpty else { return nil }
            return .entityRemoved(m.id)
        case "event":
            let m = try? dec.decode(EventMsg.self, from: data)
            return .event(entityId: m?.entityId, event: m?.event)
        case "activity":
            guard let m = try? dec.decode(ActivityMsg.self, from: data) else { return nil }
            return .activity(m.entry)
        case "dashboard":
            return .dashboard
        case "settings":
            return .settings
        case "person":
            guard let m = try? dec.decode(PersonMsg.self, from: data) else { return nil }
            return .person(m.person)
        case "pong":
            return .pong
        default:
            return .unknown(env.type)
        }
    }
}

/// The devices a live message leaves: "entity" adds or replaces one, "entity-removed" drops one.
enum EntityUpdate {
    static func apply(_ entities: [String: Entity], _ m: LiveMessage) -> [String: Entity] {
        var out = entities
        switch m {
        case .entity(let e): out[e.id] = e
        case .entityRemoved(let id): out[id] = nil
        default: break
        }
        return out
    }
}

/// Reconnect delays: 1 s doubling to 30 s, reset once a connection opens.
struct Backoff {
    private(set) var next: Double = 1
    let maximum: Double = 30

    mutating func take() -> Double {
        let d = next
        next = min(maximum, next * 2)
        return d
    }

    mutating func reset() { next = 1 }
}

// MARK: - Sign-in (POST /api/auth/login)

struct LoginReply: Decodable {
    var ok: Bool?
    var user: User?
    var token: String?
    var needs: String?
    var error: String?
}

enum LoginOutcome: Equatable {
    case signedIn(token: String, user: User?)
    /// 401 {needs: "totp"}: ask for the 6-digit code (with the hub's message when a code failed).
    case needsCode(message: String?)
    /// Anything else, with the hub's own words.
    case failed(String)

    static func classify(status: Int, body: Data) -> LoginOutcome {
        let reply = try? JSONDecoder().decode(LoginReply.self, from: body)
        if (200..<300).contains(status) {
            if let t = reply?.token, !t.isEmpty { return .signedIn(token: t, user: reply?.user) }
            return .failed("The hub signed you in but sent no token. Is this a Newman hub?")
        }
        if status == 401, reply?.needs == "totp" { return .needsCode(message: reply?.error) }
        if let e = reply?.error, !e.isEmpty { return .failed(e) }
        switch status {
        case 401: return .failed("Wrong username or password")
        case 403: return .failed("Signing in from here isn't allowed")
        case 429: return .failed("Too many attempts. Try again later.")
        case 502, 503, 504: return .failed("The Newman hub isn't answering right now.")
        default: return .failed("Sign-in failed (\(status)).")
        }
    }

    /// The JSON body for the login request.
    static func body(username: String, password: String, code: String?, kind: String, deviceName: String) -> [String: JSONValue] {
        var b: [String: JSONValue] = [
            "username": .string(username.trimmingCharacters(in: .whitespaces)),
            "password": .string(password),
            "kind": .string(kind),
            "deviceName": .string(deviceName),
            "remember": .bool(true),
        ]
        if let code, !code.isEmpty { b["code"] = .string(code.trimmingCharacters(in: .whitespaces)) }
        return b
    }
}

// MARK: - Push

enum PushPayload {
    /// The fields `/api/public/actions` expects for a tapped notification button, from the
    /// push's custom keys (server/newman/push/notifier.py) - nil when the push carries no
    /// action token (a test push, a follow-up).
    static func actionRequest(userInfo: [AnyHashable: Any], button: String, deviceId: String) -> (url: URL, body: [String: String])? {
        func s(_ k: String) -> String? {
            if let v = userInfo[k] as? String, !v.isEmpty { return v }
            if let v = userInfo[k] as? NSNumber { return v.stringValue }
            return nil
        }
        guard let urlString = s("action_url"), let url = URL(string: urlString),
              let subject = s("action_subject"), let exp = s("action_exp"), let token = s("action_token") else { return nil }
        return (url, ["button": button, "subject": subject, "deviceId": deviceId, "exp": exp, "token": token])
    }

    static let actionPath = "api/public/actions"
    /// One try at a fallback address (the hub replies at once; commands follow).
    static let actionAttemptSeconds: Double = 8
    /// The first try - the address in use - gets longer: right after iOS wakes the app for a button
    /// the network may take a while to be usable (the Home Assistant app waits up to 60 s per try).
    static let actionFirstAttemptSeconds: Double = 20
    /// All tries together, at most (iOS gives a background button about 30 s).
    static let actionBudgetSeconds: Double = 27
    /// Kept back from iOS's remaining background time for the failure notice and the press report.
    static let actionReserveSeconds: Double = 3
    /// A try shorter than this isn't started.
    static let actionMinAttemptSeconds: Double = 2

    /// How long try number `index` (0 = first) may take, after `elapsed` seconds of tries, with
    /// `backgroundLeft` seconds of iOS background time (nil in the foreground). nil = no time left.
    static func attemptSeconds(index: Int, elapsed: Double, backgroundLeft: Double?) -> Double? {
        var budget = actionBudgetSeconds - elapsed
        if let left = backgroundLeft { budget = min(budget, left - actionReserveSeconds) }
        let t = min(index == 0 ? actionFirstAttemptSeconds : actionAttemptSeconds, budget)
        return t >= actionMinAttemptSeconds ? t : nil
    }

    /// Where to send a pressed button, in order: each of the hub's addresses as the rest of the app
    /// uses them (`addresses`: the one in use first - HubRoute's pick - then the other), and the push's
    /// own `action_url` (the hub's fixed public address) only as the last resort.
    static func actionURLs(actionURL: URL, addresses: [URL]) -> [URL] {
        var list: [URL] = []
        for base in addresses {
            if let u = ServerAddress.url(base, actionPath), !list.contains(u) { list.append(u) }
        }
        if !list.contains(actionURL) { list.append(actionURL) }
        return list
    }

    /// After a try at one address: whether to try the next. nil = no answer at all (offline, timed
    /// out, TLS…); a proxy or Cloudflare saying the hub is unreachable counts the same. Any other
    /// answer is the hub's own (pressed, or refused with its reason) - never sent again elsewhere.
    static func tryNextAddress(status: Int?) -> Bool {
        guard let status else { return true }
        return [502, 503, 504, 521, 522, 523, 524, 530].contains(status)
    }

    /// The picture to attach (a signed /api/public/snapshots link).
    static func snapshotURL(userInfo: [AnyHashable: Any]) -> URL? {
        guard let s = userInfo["snapshot_url"] as? String, !s.isEmpty else { return nil }
        return URL(string: s)
    }

    /// The camera alert a notification is about (`clip` "frigate:r:<review id>" and
    /// `frigate_event_id`), nil for other notifications. Tapping it opens that alert's video.
    static func alertClip(userInfo: [AnyHashable: Any]) -> (clip: String, reviewId: String?)? {
        guard let clip = userInfo["clip"] as? String, !clip.isEmpty else { return nil }
        let rid = userInfo["frigate_event_id"] as? String
        return (clip, rid?.isEmpty == false ? rid : nil)
    }

    /// Buttons handled on the phone without calling the hub.
    static func isLocalButton(_ id: String) -> Bool { id == "open" }

    /// The push's own button set: its category id (aps.category) and `buttons` (the hub's JSON
    /// list, server/newman/push/notifier.py). nil when the push has no buttons. Never registered
    /// from the Notification Service Extension: on iOS 26 that stopped every later Newman
    /// notification until a restart (2026-10-10) - only the app registers categories (PushManager).
    static func category(userInfo: [AnyHashable: Any]) -> PushCategory? {
        guard let json = userInfo["buttons"] as? String,
              let buttons = try? JSONDecoder().decode([PushButton].self, from: Data(json.utf8)), !buttons.isEmpty else { return nil }
        let aps = userInfo["aps"] as? [String: Any]
        guard let id = (aps?["category"] as? String) ?? (userInfo["category"] as? String), !id.isEmpty else { return nil }
        return PushCategory(id: id, buttons: buttons)
    }

    /// How iOS shows a notification button: Face ID / passcode first for sensitive ones (unlock,
    /// open), the app opened for local ones, red for Dismiss.
    struct ActionStyle: Equatable {
        var authenticationRequired: Bool
        var foreground: Bool
        var destructive: Bool
    }

    static func actionStyle(_ b: PushButton) -> ActionStyle {
        ActionStyle(authenticationRequired: b.sensitive ?? false, foreground: isLocalButton(b.id), destructive: b.id == "dismiss")
    }

    // MARK: Notification Service Extension timing

    /// The extension hands the notification back by then, whatever is still running (iOS allows
    /// about 30 s).
    static let extensionDeadlineSeconds: Double = 25
    /// The snapshot download, at most - less when the deadline is nearer.
    static let snapshotFetchSeconds: Double = 20

    static func snapshotTimeout(elapsed: Double) -> Double {
        max(1, min(snapshotFetchSeconds, extensionDeadlineSeconds - 1 - elapsed))
    }

    /// The app refreshes the button sets (GET /api/push/categories) whenever iOS wakes it in the
    /// background, at most this often.
    static let categoryRefreshMinSeconds: Double = 15 * 60

    static func shouldRefreshCategories(lastAt: Double?, now: Double) -> Bool {
        guard let lastAt, lastAt <= now else { return true } // never, or the clock moved back
        return now - lastAt >= categoryRefreshMinSeconds
    }

    /// Hex string of an APNs device token.
    static func hexToken(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }

    /// The /api/devices/register body (server/newman/api.py register()).
    static func registerBody(token: String, deviceId: String, friendlyName: String, deviceModel: String,
                             sandbox: Bool) -> [String: JSONValue] {
        var b: [String: JSONValue] = [
            "token": .string(token), "platform": .string("ios"), "deviceId": .string(deviceId),
            "friendlyName": .string(friendlyName), "deviceModel": .string(deviceModel),
        ]
        if sandbox { b["apnsEnvironment"] = .string("sandbox") }
        return b
    }
}

// MARK: - Press reports (POST /api/public/press-report)

/// One try at sending a notification button, as the phone saw it - so a press the hub never
/// received can still be explained from the hub's log.
struct PressAttempt: Codable, Equatable {
    /// When the try started (seconds since 1970, the phone's clock).
    var at: Double
    /// scheme://host:port/path only - never a query or a token.
    var url: String
    /// How the app was running when the try started: active / inactive / background.
    var appState: String
    /// iOS background time left when the try started (nil in the foreground).
    var backgroundLeft: Double?
    /// The time this try was allowed.
    var timeoutSeconds: Double
    var ms: Int
    /// HTTP status, when something answered.
    var status: Int?
    var error: PressError?
    /// TLS client-certificate challenges on this try, and whether a certificate was there to answer
    /// the last one (nil = none asked). No certificate material.
    var certChallenges: Int = 0
    var certAvailable: Bool?
    /// Other challenges (server trust…), by authentication method.
    var otherChallenges: [String]?
    var metrics: PressMetrics?
}

struct PressError: Codable, Equatable {
    var domain: String
    var code: Int
    var description: String
    /// NSUnderlyingError, "domain code description".
    var underlying: String?
    /// _kCFStreamErrorCodeKey / _kCFStreamErrorDomainKey.
    var streamCode: Int?
    var streamDomain: Int?
    /// The host of NSURLErrorFailingURLErrorKey.
    var failingHost: String?
    /// The network path when it failed (_NSURLErrorNWPathKey), e.g. "unsatisfied (Local network prohibited)".
    var path: String?

    /// The fields of any Error (an NSError underneath). Keys are spelled out so this builds on Linux.
    static func of(_ error: Error) -> PressError {
        let ns = error as NSError
        var out = PressError(domain: ns.domain, code: ns.code, description: ns.localizedDescription)
        let info = ns.userInfo
        if let u = info["NSUnderlyingError"] as? NSError {
            out.underlying = "\(u.domain) \(u.code) \(u.localizedDescription)"
        }
        out.streamCode = (info["_kCFStreamErrorCodeKey"] as? NSNumber)?.intValue ?? info["_kCFStreamErrorCodeKey"] as? Int
        out.streamDomain = (info["_kCFStreamErrorDomainKey"] as? NSNumber)?.intValue ?? info["_kCFStreamErrorDomainKey"] as? Int
        if let failing = info["NSErrorFailingURLKey"] as? URL {
            out.failingHost = failing.host
        } else if let s = info["NSErrorFailingURLStringKey"] as? String {
            out.failingHost = URL(string: s)?.host
        }
        if let path = info["_NSURLErrorNWPathKey"] { out.path = String(String(describing: path).prefix(300)) }
        return out
    }

    var line: String {
        var s = "\(domain) \(code) \(description)"
        var extra: [String] = []
        if let underlying { extra.append("underlying \(underlying)") }
        if let streamCode { extra.append("stream \(streamDomain.map { "\($0)/" } ?? "")\(streamCode)") }
        if let failingHost { extra.append("host \(failingHost)") }
        if let path { extra.append("path \(path)") }
        if !extra.isEmpty { s += " [" + extra.joined(separator: "; ") + "]" }
        return s
    }
}

/// URLSessionTaskMetrics of a try, flattened: each step in ms after the task's fetch start (nil =
/// it never happened - e.g. connectStart without connectEnd: the connection was never made).
struct PressMetrics: Codable, Equatable {
    var transactions: Int
    var fetchStartAt: Double?
    var dnsStart: Int?
    var dnsEnd: Int?
    var connectStart: Int?
    var connectEnd: Int?
    var secureStart: Int?
    var secureEnd: Int?
    var requestStart: Int?
    var requestEnd: Int?
    var responseStart: Int?
    var responseEnd: Int?
    var remoteAddress: String?
    var remotePort: Int?
    var localAddress: String?
    var networkProtocol: String?
    var reusedConnection: Bool?
    var proxyConnection: Bool?
    var cellular: Bool?
    var expensive: Bool?
    var constrained: Bool?

    static func offset(_ date: Date?, from start: Date?) -> Int? {
        guard let date, let start else { return nil }
        return Int((date.timeIntervalSince(start) * 1000).rounded())
    }

    /// "dns 0-2 connect 2-? tls - request - response - | 10.1.1.2:8093 http/1.1 new"
    var line: String {
        func span(_ a: Int?, _ b: Int?) -> String {
            guard let a else { return "-" }
            return "\(a)-\(b.map(String.init) ?? "?")"
        }
        var s = "dns \(span(dnsStart, dnsEnd)) connect \(span(connectStart, connectEnd)) tls \(span(secureStart, secureEnd))"
        s += " request \(span(requestStart, requestEnd)) response \(span(responseStart, responseEnd))"
        var who: [String] = []
        if let remoteAddress { who.append(remotePort.map { "\(remoteAddress):\($0)" } ?? remoteAddress) }
        if let localAddress { who.append("from \(localAddress)") }
        if let networkProtocol, !networkProtocol.isEmpty { who.append(networkProtocol) }
        if let reusedConnection { who.append(reusedConnection ? "reused" : "new") }
        if proxyConnection == true { who.append("proxy") }
        if cellular == true { who.append("cellular") }
        if constrained == true { who.append("low data") }
        if transactions != 1 { who.append("\(transactions) transactions") }
        return who.isEmpty ? s : s + " | " + who.joined(separator: " ")
    }
}

/// What the phone sends the hub after a notification button: every try in full when the press
/// failed, one summary line when it worked. Signed with that push's own action token.
struct PressReport: Codable, Equatable {
    var id: String
    var button: String
    var subject: String
    var deviceId: String
    var exp: String
    var token: String
    var ok: Bool
    /// "pressed" / "refused 409: …" / "failed: The Newman hub took too long to answer."
    var outcome: String
    var summary: String
    var appVersion: String
    /// How the app was running when the button arrived, and how long it had been running.
    var launchState: String
    var sinceLaunchSeconds: Double?
    /// Whether iOS ended the background time before the press was done.
    var backgroundExpired: Bool = false
    /// The push's own action_url (no query), where a report may go too.
    var actionURL: String
    var at: Double
    var attempts: [PressAttempt]

    static let path = "api/public/press-report"
    /// Each try at sending a report (all the hub's addresses at once).
    static let sendSeconds: Double = 4
    static let pendingLimit = 10

    /// scheme://host[:port]/path - never the query, a fragment or credentials.
    static func redact(_ url: URL) -> String {
        guard let c = URLComponents(url: url, resolvingAgainstBaseURL: true), let host = c.host else { return "?" }
        let port = c.port.map { ":\($0)" } ?? ""
        return "\(c.scheme ?? "?")://\(host)\(port)\(c.path)"
    }

    /// Where to send a report: each of the hub's addresses, then the push's action_url's hub.
    static func urls(addresses: [URL], actionURL: URL?) -> [URL] {
        var list: [URL] = []
        for base in addresses {
            if let u = ServerAddress.url(base, path), !list.contains(u) { list.append(u) }
        }
        if let a = actionURL, let u = URL(string: "press-report", relativeTo: a)?.absoluteURL, !list.contains(u) { list.append(u) }
        return list
    }

    /// One line per try, for the phone's own log.
    static func line(_ a: PressAttempt, index: Int, of count: Int) -> String {
        var s = "try \(index + 1)/\(count) \(a.url) (\(a.appState)"
        if let left = a.backgroundLeft { s += String(format: ", %.1f s background left", left) }
        s += String(format: ", %.0f s allowed): ", a.timeoutSeconds) + "\(a.ms) ms, "
        if let status = a.status { s += "HTTP \(status)" } else if let e = a.error { s += e.line } else { s += "no answer" }
        s += "; cert challenges \(a.certChallenges)"
        if let avail = a.certAvailable { s += avail ? " (certificate sent)" : " (no certificate stored)" }
        if let other = a.otherChallenges, !other.isEmpty { s += "; other challenges \(other.joined(separator: ","))" }
        if let m = a.metrics { s += "; " + m.line } else { s += "; no metrics" }
        return s
    }

    /// "pressed at http://10.1.1.2:8093/api/public/actions on try 1 of 2 in 85 ms (background)"
    static func summary(ok: Bool, outcome: String, attempts: [PressAttempt], tried: Int) -> String {
        guard let last = attempts.last else { return outcome + " (no address to try)" }
        let total = attempts.reduce(0) { $0 + $1.ms }
        if ok { return "\(outcome) at \(last.url) on try \(attempts.count) of \(tried) in \(last.ms) ms (\(last.appState))" }
        return "\(outcome) after \(attempts.count) of \(tried) tries, \(total) ms in all (\(last.appState))"
    }

    /// The phone's queue of reports not delivered yet: newest last, at most `limit`.
    static func queued(_ existing: [Data], adding: Data, limit: Int = pendingLimit) -> [Data] {
        var q = existing.filter { $0 != adding }
        q.append(adding)
        return Array(q.suffix(limit))
    }
}

// MARK: - Server address

enum ServerAddress {
    /// "home.garito.ca" -> "https://home.garito.ca/"; "10.1.1.2:8093" -> "http://10.1.1.2:8093/"
    /// (a bare private address is assumed to be plain http on the LAN). Always ends in "/".
    static func normalize(_ input: String) -> URL? {
        var s = input.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return nil }
        if !s.lowercased().hasPrefix("http://") && !s.lowercased().hasPrefix("https://") {
            let host = String(s.split(separator: "/").first ?? "").split(separator: ":").first.map(String.init) ?? ""
            s = (isPrivateHost(host) ? "http://" : "https://") + s
        }
        // The wall page and the API both live at the root; drop a pasted "#/wall" or "/wall".
        if let hash = s.firstIndex(of: "#") { s = String(s[..<hash]) }
        while s.hasSuffix("/") { s.removeLast() }
        if s.lowercased().hasSuffix("/wall") { s.removeLast(5) }
        guard var comps = URLComponents(string: s + "/"), let host = comps.host, !host.isEmpty else { return nil }
        comps.query = nil
        return comps.url
    }

    static func isPrivateHost(_ host: String) -> Bool {
        let h = host.lowercased()
        if h == "localhost" || h.hasSuffix(".local") || h.hasSuffix(".lan") || h.hasSuffix(".home.arpa") { return true }
        let parts = h.split(separator: ".").compactMap { Int($0) }
        guard parts.count == 4 else { return false }
        switch (parts[0], parts[1]) {
        case (10, _), (127, _), (192, 168), (169, 254): return true
        case (172, let b) where (16...31).contains(b): return true
        default: return false
        }
    }

    /// An absolute URL for a hub path ("api/entities", "api/entities/x/still.jpg?h=480").
    static func url(_ base: URL, _ path: String) -> URL? {
        if path.lowercased().hasPrefix("http://") || path.lowercased().hasPrefix("https://") { return URL(string: path) }
        var p = path
        while p.hasPrefix("/") { p.removeFirst() }
        return URL(string: p, relativeTo: base)?.absoluteURL
    }

    /// ws(s):// for the live channel.
    static func webSocketURL(_ base: URL, path: String = "api/ws") -> URL? {
        guard let u = url(base, path), var c = URLComponents(url: u, resolvingAgainstBaseURL: true) else { return nil }
        c.scheme = c.scheme == "https" ? "wss" : "ws"
        return c.url
    }

    /// http://host:port/ for a hub found with Bonjour (an IPv6 address in brackets, without its
    /// "%en0" interface suffix).
    static func lanURL(host: String, port: Int) -> URL? {
        var h = host
        if let pct = h.firstIndex(of: "%") { h = String(h[..<pct]) }
        guard !h.isEmpty, port > 0 else { return nil }
        if h.contains(":") { h = "[\(h)]" }
        return normalize("http://\(h):\(port)")
    }

    /// The hub's wall screen.
    static func wallURL(_ base: URL) -> URL? { url(base, "wall") }

    /// Adds or replaces one query item (used for ?h= on stills and a cache-buster).
    static func with(_ url: URL, _ name: String, _ value: String) -> URL {
        guard var c = URLComponents(url: url, resolvingAgainstBaseURL: true) else { return url }
        var items = (c.queryItems ?? []).filter { $0.name != name }
        items.append(URLQueryItem(name: name, value: value))
        c.queryItems = items
        return c.url ?? url
    }
}

// MARK: - Request bodies

enum JSONBody {
    static func encode(_ body: [String: JSONValue]) -> Data {
        (try? JSONEncoder().encode(body)) ?? Data("{}".utf8)
    }

    static func encode(_ body: [String: String]) -> Data {
        (try? JSONEncoder().encode(body)) ?? Data("{}".utf8)
    }

    /// POST /api/entities/{id}/command body.
    static func command(_ cmd: Command, value: JSONValue?) -> [String: JSONValue] {
        var b: [String: JSONValue] = ["command": .string(cmd.id)]
        if let value { b["value"] = value }
        return b
    }

    /// The hub's `error` text from a failed reply, if any.
    static func errorText(_ data: Data) -> String? {
        guard let r = try? JSONDecoder().decode(ErrorReply.self, from: data), let e = r.error, !e.isEmpty else { return nil }
        return e
    }

    static func isSignInRequired(status: Int, data: Data) -> Bool {
        guard status == 401 else { return false }
        return (try? JSONDecoder().decode(ErrorReply.self, from: data))?.signIn ?? false
    }
}
