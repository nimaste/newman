import Foundation

// Home-screen / Lock Screen widgets, Control Center controls and Siri actions (the NewmanWidgets
// extension and the app's App Intents). Pure Foundation, so the Linux checks cover it. What a
// device shows and offers comes from the hub's JSON and /api/meta kind metadata only - no list
// of kinds here.

// MARK: - GET /api/widgets/state?ids=a,b,c

struct WidgetAction: Codable, Hashable {
    var command: String
    var label: String
    var sensitive: Bool?

    var isSensitive: Bool { sensitive ?? false }
}

struct WidgetReading: Codable, Hashable {
    var value: JSONValue?
    var unit: String?

    /// "22.5 °C", "64 %", "" when there's no value.
    var text: String {
        guard let v = value, !v.isNull, v.displayText != "" else { return "" }
        let num: String
        if let n = v.doubleValue, v.stringValue == nil {
            num = abs(n) >= 100 ? JSONValue.format(n.rounded()) : JSONValue.format((n * 10).rounded() / 10)
        } else {
            num = v.displayText
        }
        let u = unit ?? ""
        if u.isEmpty { return num }
        return u == "%" ? "\(num)%" : "\(num) \(u)"
    }
}

/// One device as a widget shows it. Every field but the id may be missing (older or newer hub).
struct WidgetDevice: Codable, Hashable, Identifiable {
    var id: String
    var name: String
    var kind: String?
    var state: String?
    var stateLabel: String?
    var available: Bool?
    var icon: String?
    var area: String?
    var actions: [WidgetAction]?
    var reading: WidgetReading?

    enum CodingKeys: String, CodingKey { case id, name, kind, state, stateLabel, available, icon, area, actions, reading }

    init(id: String, name: String, kind: String? = nil, state: String? = nil, stateLabel: String? = nil,
         available: Bool? = nil, icon: String? = nil, area: String? = nil, actions: [WidgetAction]? = nil,
         reading: WidgetReading? = nil) {
        self.id = id
        self.name = name
        self.kind = kind
        self.state = state
        self.stateLabel = stateLabel
        self.available = available
        self.icon = icon
        self.area = area
        self.actions = actions
        self.reading = reading
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? id
        kind = try? c.decodeIfPresent(String.self, forKey: .kind)
        if let raw = try? c.decodeIfPresent(JSONValue.self, forKey: .state), !raw.isNull { state = raw.displayText }
        stateLabel = try? c.decodeIfPresent(String.self, forKey: .stateLabel)
        available = try? c.decodeIfPresent(Bool.self, forKey: .available)
        icon = try? c.decodeIfPresent(String.self, forKey: .icon)
        area = (try? c.decodeIfPresent(String.self, forKey: .area)).flatMap { $0.isEmpty ? nil : $0 }
        actions = try? c.decodeIfPresent([WidgetAction].self, forKey: .actions)
        reading = try? c.decodeIfPresent(WidgetReading.self, forKey: .reading)
    }

    var isAvailable: Bool { available != false && state != "offline" }
    var actionList: [WidgetAction] { actions ?? [] }
}

struct WidgetStateReply: Decodable {
    var devices: [WidgetDevice]

    enum CodingKeys: String, CodingKey { case devices }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // One device the app can't read doesn't hide the others.
        let raw = (try? c.decodeIfPresent([JSONValue].self, forKey: .devices)) ?? []
        devices = raw.compactMap { v in
            guard let d = try? JSONEncoder().encode(v) else { return nil }
            return try? JSONDecoder().decode(WidgetDevice.self, from: d)
        }
    }

    static func parse(_ data: Data) -> [WidgetDevice]? {
        (try? JSONDecoder().decode(WidgetStateReply.self, from: data))?.devices
    }
}

/// What Siri / Shortcuts can ask for by name ("Turn on the kitchen light with Newman").
enum DeviceVerb: String, CaseIterable {
    case turnOn, turnOff, open, close, lock, unlock

    /// Words for "<verb> <device>".
    var label: String {
        switch self {
        case .turnOn: return "Turn on"
        case .turnOff: return "Turn off"
        case .open: return "Open"
        case .close: return "Close"
        case .lock: return "Lock"
        case .unlock: return "Unlock"
        }
    }

    var past: String {
        switch self {
        case .turnOn: return "Turned on"
        case .turnOff: return "Turned off"
        case .open: return "Opening"
        case .close: return "Closing"
        case .lock: return "Locked"
        case .unlock: return "Unlocked"
        }
    }
}

enum WidgetLogic {
    // MARK: which devices a widget can show

    /// A widget shows a device's state or reading and (maybe) a tap action. Not cameras (pictures
    /// and streams don't fit a widget) and not remotes (only button events, nothing to show).
    static func isShowable(_ e: Entity, _ kind: Kind?) -> Bool {
        if kind?.hasCamera ?? false { return false }
        if DeviceLogic.isRemote(e, kind) { return false }
        if !DeviceLogic.plainCommands(kind).isEmpty { return true }
        return !(kind?.stateList.isEmpty ?? true) || !DeviceLogic.readingText(e, kind).isEmpty || !(e.state ?? "").isEmpty
    }

    /// "Kitchen · Light" under a device's name in the pickers.
    static func detail(_ e: Entity, _ kind: Kind?) -> String {
        let type = kind?.name ?? DeviceLogic.humanize(e.kind)
        guard let area = e.area else { return type }
        return "\(area) · \(type)"
    }

    struct Section: Equatable {
        var title: String
        var ids: [String]
    }

    /// The picker's sections: one per room (A-Z), then the devices without a room by type
    /// (the kind's plural name, A-Z). Devices A-Z inside each.
    static func sections(_ entities: [Entity], kinds: [String: Kind]) -> [Section] {
        let shown = entities.filter { isShowable($0, kinds[$0.kind]) }
        var rooms: [String: [Entity]] = [:]
        var types: [String: [Entity]] = [:]
        for e in shown {
            if let a = e.area {
                rooms[a, default: []].append(e)
            } else {
                let k = kinds[e.kind]
                types[k?.plural ?? k?.name ?? DeviceLogic.humanize(e.kind), default: []].append(e)
            }
        }
        func sorted(_ list: [Entity]) -> [String] {
            list.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }.map(\.id)
        }
        let byTitle: (String, String) -> Bool = { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
        return rooms.keys.sorted(by: byTitle).map { Section(title: $0, ids: sorted(rooms[$0]!)) }
            + types.keys.sorted(by: byTitle).map { Section(title: $0, ids: sorted(types[$0]!)) }
    }

    /// Search in the pickers: name, room or type.
    static func matches(_ e: Entity, _ kind: Kind?, query: String) -> Bool {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        if q.isEmpty { return true }
        return [e.name, e.area ?? "", kind?.name ?? "", kind?.plural ?? ""].contains { $0.localizedCaseInsensitiveContains(q) }
    }

    // MARK: building a widget device without /api/widgets/state (older hubs)

    static func device(_ e: Entity, _ kind: Kind?) -> WidgetDevice {
        let tap = DeviceLogic.tapAction(e, kind)
        var actions: [WidgetAction] = []
        for c in [tap].compactMap({ $0 }) + DeviceLogic.plainCommands(kind) where !actions.contains(where: { $0.command == c.id }) {
            actions.append(WidgetAction(command: c.id, label: c.label, sensitive: c.isSensitive))
        }
        var reading: WidgetReading?
        if let kind, kind.stateList.isEmpty, let raw = e.state, let v = Double(raw) {
            var unit = e.attr("unitOfMeasurement")?.stringValue ?? ""
            if unit.isEmpty { unit = (e.properties ?? []).first { ($0.attr ?? "").isEmpty }?.unit ?? "" }
            if unit.isEmpty { unit = kind.unit ?? "" }
            reading = WidgetReading(value: .number(v), unit: unit.isEmpty ? nil : unit)
        }
        return WidgetDevice(id: e.id, name: e.name, kind: e.kind, state: e.state, stateLabel: DeviceLogic.stateText(e, kind),
                            available: e.isAvailable, icon: DeviceLogic.kindIcon(kind), area: e.area, actions: actions,
                            reading: reading)
    }

    /// The devices in the order the widget was set up with (ids it couldn't get are skipped).
    static func ordered(_ devices: [WidgetDevice], ids: [String]) -> [WidgetDevice] {
        var byId: [String: WidgetDevice] = [:]
        for d in devices { byId[d.id] = d }
        return ids.compactMap { byId[$0] }
    }

    // MARK: what a tile shows and does

    /// "Offline", the reading ("22.5 °C"), else the hub's words for the state.
    static func stateText(_ d: WidgetDevice) -> String {
        if !d.isAvailable { return "Offline" }
        let r = d.reading?.text ?? ""
        if !r.isEmpty { return r }
        if let l = d.stateLabel, !l.isEmpty { return l }
        return d.state.map { DeviceLogic.humanize($0) } ?? ""
    }

    /// Lit like a light that's on (the kind's active state, not a secure/insecure kind).
    static func isLit(_ d: WidgetDevice, _ kind: Kind?) -> Bool {
        guard let kind, let active = kind.activeState, kind.secureState == nil else { return false }
        return d.state == active
    }

    /// Away from the kind's secure state (unlocked, garage open): shown in the warning colour.
    static func isWarning(_ d: WidgetDevice, _ kind: Kind?) -> Bool {
        guard d.isAvailable, let kind, let secure = kind.secureState, let s = d.state, !s.isEmpty else { return false }
        return s != secure && !(kind.transitional ?? []).contains(s)
    }

    /// What tapping the tile does: the app's own tap action (on/off pair, else the action that
    /// isn't the current state) when the kind is known, else the hub's first action.
    static func tapAction(_ d: WidgetDevice, _ kind: Kind?) -> WidgetAction? {
        guard d.isAvailable else { return nil }
        let list = d.actionList
        if let kind, let kindId = d.kind {
            let e = Entity(id: d.id, name: d.name, kind: kindId, state: d.state)
            if let c = DeviceLogic.tapAction(e, kind) {
                if let a = list.first(where: { $0.command == c.id }) { return a }
                if list.isEmpty { return WidgetAction(command: c.id, label: c.label, sensitive: c.isSensitive) }
            }
        }
        return list.first
    }

    static func isSensitive(_ a: WidgetAction, _ kind: Kind?) -> Bool {
        a.isSensitive || (kind?.command(a.command)?.isSensitive ?? false)
    }

    /// A command Siri / a switch resolved for a device: sensitive when the kind metadata or the
    /// hub's widget state says so.
    static func isSensitive(command: String, _ d: WidgetDevice?, _ kind: Kind?) -> Bool {
        (kind?.command(command)?.isSensitive ?? false)
            || (d?.actionList.contains { $0.command == command && $0.isSensitive } ?? false)
    }

    /// Whether to refuse running a command now: a sensitive one, from an intent iOS doesn't
    /// already make authenticate (`.alwaysAllowed`), while the phone is locked.
    static func mustUnlockFirst(sensitive: Bool, intentAuthenticates: Bool, phoneUnlocked: Bool) -> Bool {
        sensitive && !intentAuthenticates && !phoneUnlocked
    }

    // MARK: Control Center

    /// An on/off pair a Control Center switch can drive: the kind's active state and the two plain
    /// commands that reach it and leave it. Never a sensitive one (unlock, open the garage: those
    /// are buttons).
    static func toggle(_ kind: Kind?) -> (on: Command, off: Command)? {
        guard let kind, let active = kind.activeState else { return nil }
        let plain = DeviceLogic.plainCommands(kind)
        guard let on = plain.first(where: { $0.confirmsTo == active }),
              let off = plain.first(where: { $0 != on && !($0.confirmsTo ?? "").isEmpty && $0.confirmsTo != active }),
              !on.isSensitive, !off.isSensitive else { return nil }
        return (on, off)
    }

    static func isOn(_ d: WidgetDevice, _ kind: Kind?) -> Bool {
        guard let active = kind?.activeState else { return false }
        return d.state == active
    }

    // MARK: Siri

    /// The command for "<verb> <device>": turn on/off is the kind's on/off pair (sensitive ones
    /// too - it was asked for by name); open/close/lock/unlock are the kind's commands of that id.
    static func command(_ verb: DeviceVerb, _ kind: Kind?) -> Command? {
        guard let kind else { return nil }
        switch verb {
        case .turnOn, .turnOff:
            guard let active = kind.activeState else { return nil }
            let plain = DeviceLogic.plainCommands(kind)
            guard let on = plain.first(where: { $0.confirmsTo == active }) else { return nil }
            if verb == .turnOn { return on }
            return plain.first { $0 != on && !($0.confirmsTo ?? "").isEmpty && $0.confirmsTo != active }
        default:
            return kind.command(verb.rawValue).flatMap { ($0.takesValue ?? false) ? nil : $0 }
        }
    }

    // MARK: freshness

    /// When WidgetKit should ask again: soon after an action here (the device may still be
    /// moving), else every 15 minutes (the hub's widget push reloads sooner on changes).
    static func nextRefresh(now: Double, lastActionAt: Double?) -> Double {
        if let a = lastActionAt, now - a < 120 { return now + 30 }
        return now + 15 * 60
    }

    // MARK: widget push token (POST /api/widgets/push-token)

    static func pushTokenBody(token: String, deviceId: String, sandbox: Bool, appBundleId: String) -> [String: JSONValue] {
        var b: [String: JSONValue] = [
            "token": .string(token), "kind": .string("ios-widget"), "deviceId": .string(deviceId),
            // APNs topic for widget pushes: <app bundle id>.push-type.widgets (apns-push-type: widgets).
            "topic": .string(appBundleId + ".push-type.widgets"),
        ]
        if sandbox { b["apnsEnvironment"] = .string("sandbox") }
        return b
    }

    /// What was last sent to the hub: re-sent when the token, the hub address or the session changes.
    static func registrationStamp(token: String, server: String, session: String) -> String {
        // A hash of the session, not the session: the stamp lives in the App Group's defaults.
        var h: UInt64 = 0xcbf29ce484222325 // FNV-1a
        for b in session.utf8 {
            h ^= UInt64(b)
            h = h &* 0x100000001b3
        }
        return "\(token)|\(server)|\(String(h, radix: 16))"
    }
}
