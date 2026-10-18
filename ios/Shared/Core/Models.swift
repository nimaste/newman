import Foundation

// The hub's JSON, as read by the apps. Field names follow server/newman/api.py and kinds.py
// exactly; nearly everything is optional so an older or newer hub never breaks decoding.

// MARK: - Meta (GET /api/meta)

struct Meta: Decodable {
    var version: String?
    var categories: [MetaCategory]?
    var kinds: [Kind]?

    var kindsById: [String: Kind] {
        var out: [String: Kind] = [:]
        for k in kinds ?? [] { out[k.id] = k }
        return out
    }
}

struct MetaCategory: Decodable, Hashable {
    var id: String
    var name: String
}

struct KindState: Decodable, Hashable {
    var id: String
    var label: String
}

struct KindEvent: Decodable, Hashable {
    var id: String
    var label: String?
}

struct PropertySpec: Decodable, Hashable {
    var id: String
    var label: String?
    var unit: String?
    var attr: String?
    var type: String?
}

struct Kind: Decodable, Hashable {
    var id: String
    var name: String?
    var plural: String?
    var category: String?
    var noun: String?
    var states: [KindState]?
    var activeState: String?
    var commands: [Command]?
    var events: [KindEvent]?
    var hasCamera: Bool?
    var transitional: [String]?
    var unit: String?
    var secureState: String?
    var isMode: Bool?
    var urgent: Bool?
    var properties: [PropertySpec]?

    var stateList: [KindState] { states ?? [] }
    var commandList: [Command] { commands ?? [] }
    var eventList: [KindEvent] { events ?? [] }

    func command(_ id: String?) -> Command? {
        guard let id else { return nil }
        return commandList.first { $0.id == id }
    }
}

/// One thing a device can be told to do. `valueType` says which control to show:
/// percent | number | state | choices | text, or "" for a plain button.
struct Command: Decodable, Hashable, Identifiable {
    var id: String
    var label: String
    var past: String?
    var icon: String?
    var confirmsTo: String?
    var sensitive: Bool?
    var takesValue: Bool?
    var valueType: String?
    var min: Double?
    var max: Double?
    var step: Double?
    var unit: String?
    var defaultValue: JSONValue?
    var attr: String?

    enum CodingKeys: String, CodingKey {
        case id, label, past, icon, confirmsTo, sensitive, takesValue, valueType, min, max, step, unit, attr
        case defaultValue = "default"
    }

    var isSensitive: Bool { sensitive ?? false }
    /// The value type, with the hub's own fallback: a command that takes a value but names no type is text.
    var resolvedValueType: String {
        let v = valueType ?? ""
        if v.isEmpty && (takesValue ?? false) { return "text" }
        return v
    }
    var needsValue: Bool { (takesValue ?? false) || !resolvedValueType.isEmpty }
}

// MARK: - Entities (GET /api/entities)

/// A device's problems. `ok`/`issues` leave out the ones someone muted; `items` (newer hubs) lists every
/// problem with its id and, when muted, until when (MuteLogic).
struct Health: Decodable, Hashable {
    /// Who muted a problem and for how long: mode "day" | "week" (until a time), "fixed", "always".
    struct Muted: Decodable, Hashable {
        var mode: String?
        var until: Double?
        var by: String?
    }

    struct Item: Decodable, Hashable, Identifiable {
        var id: String
        var text: String
        var muted: Muted?
    }

    var ok: Bool?
    var issues: [String]?
    var items: [Item]?

    enum CodingKeys: String, CodingKey { case ok, issues, items }

    init(ok: Bool? = nil, issues: [String]? = nil, items: [Item]? = nil) {
        self.ok = ok
        self.issues = issues
        self.items = items
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = try? c.decodeIfPresent(Bool.self, forKey: .ok)
        issues = try? c.decodeIfPresent([String].self, forKey: .issues)
        // An older hub has no items; one it can't read falls back to the plain issues.
        items = try? c.decodeIfPresent([Item].self, forKey: .items)
    }
}

struct PropertyOption: Decodable, Hashable {
    var value: JSONValue?
    var label: String?
}

/// A value a device reports (with its current value).
struct DeviceProperty: Decodable, Hashable, Identifiable {
    var id: String
    var label: String?
    var unit: String?
    var attr: String?
    var type: String?
    var options: [PropertyOption]?
    var value: JSONValue?
}

struct Entity: Decodable, Hashable, Identifiable {
    var id: String
    var name: String
    var kind: String
    var category: String?
    var integration: String?
    var integrationName: String?
    var state: String?
    var stateLabel: String?
    var available: Bool?
    var attrs: [String: JSONValue]?
    var health: Health?
    var properties: [DeviceProperty]?
    var still: String?
    var alerts: String?
    var live: String?
    var usedBy: [String]?
    var updatedAt: Double?
    /// A remote's Controls (the lights it controls and what each press does); nil for everything else.
    var controls: RemoteControls?

    enum CodingKeys: String, CodingKey {
        case id, name, kind, category, integration, integrationName, state, stateLabel, available, attrs, health
        case properties, still, alerts, live, usedBy, updatedAt, controls
    }

    init(id: String, name: String, kind: String, state: String? = nil, attrs: [String: JSONValue]? = nil) {
        self.id = id
        self.name = name
        self.kind = kind
        self.state = state
        self.attrs = attrs
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? id
        kind = (try? c.decodeIfPresent(String.self, forKey: .kind)) ?? ""
        category = try? c.decodeIfPresent(String.self, forKey: .category)
        integration = try? c.decodeIfPresent(String.self, forKey: .integration)
        integrationName = try? c.decodeIfPresent(String.self, forKey: .integrationName)
        // A reading's state can arrive as a number; keep it as text like the web does.
        if let raw = try? c.decodeIfPresent(JSONValue.self, forKey: .state) {
            state = raw.isNull ? nil : raw.displayText
        }
        stateLabel = try? c.decodeIfPresent(String.self, forKey: .stateLabel)
        available = try? c.decodeIfPresent(Bool.self, forKey: .available)
        attrs = try? c.decodeIfPresent([String: JSONValue].self, forKey: .attrs)
        health = try? c.decodeIfPresent(Health.self, forKey: .health)
        properties = try? c.decodeIfPresent([DeviceProperty].self, forKey: .properties)
        still = try? c.decodeIfPresent(String.self, forKey: .still)
        alerts = try? c.decodeIfPresent(String.self, forKey: .alerts)
        live = try? c.decodeIfPresent(String.self, forKey: .live)
        usedBy = try? c.decodeIfPresent([String].self, forKey: .usedBy)
        updatedAt = try? c.decodeIfPresent(Double.self, forKey: .updatedAt)
        controls = try? c.decodeIfPresent(RemoteControls.self, forKey: .controls)
    }

    var isAvailable: Bool { available != false && state != "offline" }
    var issues: [String] { health?.issues ?? [] }
    func attr(_ key: String) -> JSONValue? { attrs?[key] }
    var area: String? { attr("area")?.stringValue.flatMap { $0.isEmpty ? nil : $0 } }
}

/// A remote's Controls, as the hub describes them: the lights it controls, the brightness "on" uses (nil =
/// what they had last), whether holding brightens and dims, and one row per press ("Tap top" → "On at 60%").
struct RemoteControls: Decodable, Hashable {
    struct Row: Decodable, Hashable, Identifiable {
        var key: String
        var label: String
        var does: String
        var custom: Bool
        var id: String { key }

        enum CodingKeys: String, CodingKey { case key, label, does, custom }

        init(key: String, label: String, does: String, custom: Bool = false) {
            self.key = key
            self.label = label
            self.does = does
            self.custom = custom
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            label = (try? c.decodeIfPresent(String.self, forKey: .label)) ?? ""
            key = (try? c.decodeIfPresent(String.self, forKey: .key)) ?? label
            does = (try? c.decodeIfPresent(String.self, forKey: .does)) ?? ""
            custom = (try? c.decodeIfPresent(Bool.self, forKey: .custom)) ?? false
        }
    }

    var lights: [String]
    var onLevel: Int?
    var hold: Bool
    var rows: [Row]

    enum CodingKeys: String, CodingKey { case lights, onLevel, hold, rows }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        lights = (try? c.decodeIfPresent([String].self, forKey: .lights)) ?? []
        onLevel = try? c.decodeIfPresent(Int.self, forKey: .onLevel)
        hold = (try? c.decodeIfPresent(Bool.self, forKey: .hold)) ?? false
        rows = (try? c.decodeIfPresent([Row].self, forKey: .rows)) ?? []
    }

    /// "Controls · 3 lights", "Controls · 1 light", or "Controls" when it controls no lights.
    var heading: String {
        switch lights.count {
        case 0: return "Controls"
        case 1: return "Controls · 1 light"
        default: return "Controls · \(lights.count) lights"
        }
    }
}

// MARK: - Dashboards (GET /api/dashboards/current)

struct Dashboard: Decodable, Hashable {
    var id: String?
    var userId: String?
    var isDefault: Bool?
    var name: String?
    var theme: String?
    var pages: [DashboardPage]?
    var names: [String: String]?
}

struct DashboardPage: Decodable, Hashable, Identifiable {
    var id: String
    var name: String
    var icon: String?
    var tiles: [Tile]?
    /// Phone layouts only (GET /api/phone-layout): this page is a bottom tab (PhoneGrid).
    var tab: Bool?
}

struct Tile: Decodable, Hashable, Identifiable {
    var id: String
    var type: String
    var x: Int
    var y: Int
    var w: Int
    var h: Int
    var config: [String: JSONValue]?
    /// Phone layouts: the tile's text size in % of normal (dashboards keep it in config.textSize).
    var textSize: Double?

    enum CodingKeys: String, CodingKey { case id, type, x, y, w, h, config, textSize }

    init(id: String, type: String, x: Int, y: Int, w: Int, h: Int, config: [String: JSONValue]? = nil, textSize: Double? = nil) {
        self.id = id
        self.type = type
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.config = config
        self.textSize = textSize
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? c.decodeIfPresent(String.self, forKey: .id)) ?? UUID().uuidString
        type = (try? c.decodeIfPresent(String.self, forKey: .type)) ?? ""
        x = (try? c.decodeIfPresent(Int.self, forKey: .x)) ?? 0
        y = (try? c.decodeIfPresent(Int.self, forKey: .y)) ?? 0
        w = (try? c.decodeIfPresent(Int.self, forKey: .w)) ?? 1
        h = (try? c.decodeIfPresent(Int.self, forKey: .h)) ?? 1
        config = try? c.decodeIfPresent([String: JSONValue].self, forKey: .config)
        textSize = try? c.decodeIfPresent(Double.self, forKey: .textSize)
    }

    func string(_ key: String) -> String? { config?[key]?.stringValue }
    func number(_ key: String) -> Double? { config?[key]?.doubleValue }
    func strings(_ key: String) -> [String] { (config?[key]?.arrayValue ?? []).compactMap { $0.stringValue } }
    /// The tile's text scale (textSize, else config.textSize; % of normal), clamped like the wall does.
    var textScale: Double {
        guard let v = textSize ?? number("textSize"), v > 0 else { return 1 }
        return Swift.max(0.5, Swift.min(1.6, v / 100))
    }
}

// MARK: - Alerts and activity

/// A camera alert (GET /api/alerts, GET /api/entities/{id}/alerts).
struct CameraAlert: Decodable, Hashable, Identifiable {
    var id: String
    var camera: String?
    var cameraName: String?
    var entityId: String?
    var severity: String?
    var title: String?
    var objects: [String]?
    var zones: [String]?
    var start: Double?
    var end: Double?
    var clip: String?
    var thumbUrl: String?
    var snapshotUrl: String?
    var integration: String?
}

/// An alert's video: the hub's `/api/clips/<integration>/<clip id>` for its `clip`
/// ("frigate:r:<review id>"), the same address the web dashboard plays (Camera.jsx clipUrl).
enum AlertClip {
    /// nil without both halves.
    static func path(_ clip: String?) -> String? {
        guard let clip, let colon = clip.firstIndex(of: ":") else { return nil }
        let integration = String(clip[..<colon]), id = String(clip[clip.index(after: colon)...])
        guard !integration.isEmpty, !id.isEmpty else { return nil }
        return "api/clips/\(component(integration))/\(component(id))"
    }

    /// Percent-encodes like JavaScript's encodeURIComponent.
    static func component(_ s: String) -> String {
        let allowed = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.!~*'()")
        return s.addingPercentEncoding(withAllowedCharacters: allowed) ?? s
    }

    /// The alert a camera notification is about: the one with its clip, else its review id.
    static func match(_ alerts: [CameraAlert], clip: String, reviewId: String?) -> CameraAlert? {
        if let a = alerts.first(where: { $0.clip == clip }) { return a }
        guard let reviewId, !reviewId.isEmpty else { return nil }
        return alerts.first { $0.id == reviewId }
    }
}

/// Byte ranges for streaming an alert's video. A finished clip is a file on the hub, served with
/// ranges (206, "Content-Range: bytes 0-1/15477162"); a still-recording one only comes whole (200).
/// Content-Range read like Android's CameraClip.parseContentRange.
struct ClipRange: Equatable {
    var start: Int64
    var end: Int64
    /// nil when the hub says "*" (length unknown).
    var total: Int64?

    /// The first request: two bytes, to learn whether ranges work and the clip's length.
    static let probe = "bytes=0-1"

    /// "bytes 0-1/15477162"; nil for anything else.
    init?(contentRange: String?) {
        guard let s = contentRange?.trimmingCharacters(in: .whitespaces), s.lowercased().hasPrefix("bytes ") else { return nil }
        let halves = s.dropFirst(6).trimmingCharacters(in: .whitespaces).split(separator: "/", omittingEmptySubsequences: false)
        guard halves.count == 2 else { return nil }
        let ends = halves[0].split(separator: "-", omittingEmptySubsequences: false)
        guard ends.count == 2, let a = Int64(ends[0]), let b = Int64(ends[1]), a >= 0, b >= a else { return nil }
        if halves[1] == "*" {
            total = nil
        } else {
            guard let t = Int64(halves[1]), t > b else { return nil }
            total = t
        }
        start = a
        end = b
    }

    /// The Range header for a player's data request: `length` bytes from `offset`, or everything from
    /// there (`toEnd`), never past the clip's end. Nil when nothing is left to fetch.
    static func header(offset: Int64, length: Int64, toEnd: Bool, total: Int64?) -> String? {
        guard offset >= 0 else { return nil }
        if let total, offset >= total { return nil }
        if toEnd { return "bytes=\(offset)-" }
        guard length > 0 else { return nil }
        var last = offset + length - 1
        if let total { last = min(last, total - 1) }
        return "bytes=\(offset)-\(last)"
    }
}

/// An activity entry (GET /api/events, ws {type:"activity"}).
struct ActivityEvent: Decodable, Hashable, Identifiable {
    var rawId: JSONValue?
    var ts: Double
    var kind: String
    var title: String
    var body: String?
    var entityId: String?
    var data: [String: JSONValue]?

    enum CodingKeys: String, CodingKey { case rawId = "id", ts, kind, title, body, entityId, data }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        rawId = try? c.decodeIfPresent(JSONValue.self, forKey: .rawId)
        ts = (try? c.decodeIfPresent(Double.self, forKey: .ts)) ?? 0
        kind = (try? c.decodeIfPresent(String.self, forKey: .kind)) ?? "other"
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? ""
        body = try? c.decodeIfPresent(String.self, forKey: .body)
        entityId = try? c.decodeIfPresent(String.self, forKey: .entityId)
        data = try? c.decodeIfPresent([String: JSONValue].self, forKey: .data)
    }

    var id: String { rawId?.displayText ?? "\(ts)-\(title)" }
    var thumbPath: String? { data?["thumbUrl"]?.stringValue }
    var snapshotPath: String? { data?["snapshotUrl"]?.stringValue }
    var reviewId: String? { data?["reviewId"]?.stringValue }
}

// MARK: - Accounts

struct User: Decodable, Hashable {
    var id: String
    var username: String?
    var name: String?
    var role: String?
    var personId: String?
    var twoFactor: Bool?
    var hasPin: Bool?
}

struct SessionInfo: Decodable, Hashable {
    var id: String?
    var kind: String?
    var deviceName: String?
    var `where`: String?
    var certificate: Bool?
}

/// GET /api/auth/state.
struct AuthState: Decodable, Hashable {
    var setupNeeded: Bool?
    var signedIn: Bool?
    var user: User?
    var session: SessionInfo?
    var `where`: String?
    var certificate: Bool?
    var awayPolicy: String?
    var tabletSensitive: String?
    var time: Double?
    var version: String?

    var isHome: Bool { (self.where ?? "") == "home" }
    var hasCertificate: Bool { certificate ?? false }

    /// "at home" / "connected securely from outside" / "from outside", for the account screen.
    var connectionText: String {
        if isHome { return "at home" }
        return hasCertificate ? "connected securely from outside" : "from outside"
    }
}

// MARK: - Push

struct PushButton: Decodable, Hashable {
    var id: String
    var label: String
    var icon: String?
    var sensitive: Bool?
}

/// GET /api/push/categories: every button combination a notification can carry.
struct PushCategory: Decodable, Hashable {
    var id: String
    var buttons: [PushButton]
}

struct RegisterReply: Decodable {
    var ok: Bool?
    var personId: String?
}

/// The `{error}` the hub puts in failed replies.
struct ErrorReply: Decodable {
    var error: String?
    var needs: String?
    var signIn: Bool?
    var needsPin: Bool?
    var retryAfter: Double?
}
