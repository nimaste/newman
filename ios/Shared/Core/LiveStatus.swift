import Foundation

// Live status: while a device is in one of its "live status" states (or an automation's "Show
// live status" runs), the hub shows it on the Lock Screen and in the Dynamic Island as a Live
// Activity. The hub starts, updates and ends them by APNs (push type liveactivity), so this is
// the content-state JSON those pushes carry - decoded by ActivityKit into NewmanLiveAttributes'
// ContentState - plus the token bodies for POST /api/live/tokens and the reply of
// GET /api/live/current. Pure Foundation, so the Linux checks test it.

/// One button on a live status. `sensitive` (unlock, open the garage…) never runs from a locked phone.
struct LiveStatusAction: Codable, Hashable, Sendable {
    var command: String
    var label: String
    var sensitive: Bool

    enum CodingKeys: String, CodingKey { case command, label, sensitive }
}

extension LiveStatusAction {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        command = try c.decode(String.self, forKey: .command)
        label = (try? c.decodeIfPresent(String.self, forKey: .label)) ?? command
        sensitive = (try? c.decodeIfPresent(Bool.self, forKey: .sensitive)) ?? false
    }
}

/// One device on a live status: its name, icon and state as the hub words them.
struct LiveStatusDevice: Codable, Hashable, Sendable, Identifiable {
    var id: String
    var name: String
    var icon: String?
    var stateLabel: String?
    /// Still in one of its live status states (false: it has left them, e.g. in the final state).
    var active: Bool
    var actions: [LiveStatusAction]

    enum CodingKeys: String, CodingKey { case id, name, icon, stateLabel, active, actions }
}

extension LiveStatusDevice {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? id
        icon = try? c.decodeIfPresent(String.self, forKey: .icon)
        stateLabel = try? c.decodeIfPresent(String.self, forKey: .stateLabel)
        active = (try? c.decodeIfPresent(Bool.self, forKey: .active)) ?? true
        actions = (try? c.decodeIfPresent([LiveStatusAction].self, forKey: .actions)) ?? []
    }
}

/// The content-state of a live status, exactly as the hub sends it:
/// {"title","subtitle","since":epoch,"active":bool,"devices":[…],"more":int}.
struct LiveStatusState: Codable, Hashable, Sendable {
    var title: String
    var subtitle: String?
    /// When it started (seconds since 1970): the phone counts the time itself.
    var since: Double
    /// False in the final state the hub sends with the end event ("Garage closed").
    var active: Bool
    var devices: [LiveStatusDevice]
    /// The combined "N more" status: how many more devices it stands for than `devices` lists.
    var more: Int

    enum CodingKeys: String, CodingKey { case title, subtitle, since, active, devices, more }
}

extension LiveStatusState {
    /// Lenient: a field the hub leaves out (or a newer hub words differently) must not make
    /// ActivityKit drop the whole push.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? ""
        subtitle = try? c.decodeIfPresent(String.self, forKey: .subtitle)
        since = (try? c.decodeIfPresent(Double.self, forKey: .since)) ?? 0
        active = (try? c.decodeIfPresent(Bool.self, forKey: .active)) ?? true
        devices = (try? c.decodeIfPresent([LiveStatusDevice].self, forKey: .devices)) ?? []
        more = max(0, (try? c.decodeIfPresent(Int.self, forKey: .more)) ?? 0)
    }
}

/// One live status the hub is showing this phone right now (GET /api/live/current).
struct LiveStatusCurrent: Equatable {
    var key: String
    var title: String
    var state: LiveStatusState
}

/// A Live Activity running on this phone: its ActivityKit id and the hub's key for it.
struct LiveStatusLocal: Equatable {
    var id: String
    var key: String
}

/// What to do so this phone shows what the hub says is current.
struct LiveStatusPlan: Equatable {
    /// Activity ids to end now (the hub isn't showing them any more, or a second one for a key).
    var end: [String] = []
    /// Keys to start here (the hub couldn't start them by push: the app hadn't been opened yet).
    var start: [String] = []
    /// Key -> the activity id showing it, to bring up to date.
    var update: [String: String] = [:]
}

enum LiveStatusLogic {
    /// Device rows a live status shows on the Lock Screen / in the expanded Dynamic Island (a Live
    /// Activity is at most 160 points tall); the rest are counted in "+N more".
    static let rowLimit = 2

    static func shown(_ s: LiveStatusState, limit: Int = rowLimit) -> [LiveStatusDevice] {
        Array(s.devices.prefix(max(0, limit)))
    }

    /// The "+N more" count under the rows: the hub's `more` plus listed devices without room.
    static func moreCount(_ s: LiveStatusState, shown: Int) -> Int {
        s.more + max(0, s.devices.count - shown)
    }

    static func moreText(_ n: Int) -> String? { n > 0 ? "+\(n) more" : nil }

    /// The device the compact Dynamic Island and the tap stand for: the first still active one.
    static func lead(_ s: LiveStatusState) -> LiveStatusDevice? {
        s.devices.first { $0.active } ?? s.devices.first
    }

    /// The headline: the hub's title, else the attributes' title, else the lead device's name.
    static func title(_ s: LiveStatusState, fallback: String) -> String {
        let t = s.title.trimmingCharacters(in: .whitespacesAndNewlines)
        if !t.isEmpty { return t }
        let f = fallback.trimmingCharacters(in: .whitespacesAndNewlines)
        if !f.isEmpty { return f }
        return lead(s)?.name ?? "Newman"
    }

    /// The short state for the compact Dynamic Island: the lead device's state, "N live" for several.
    static func compactText(_ s: LiveStatusState) -> String {
        let total = s.devices.count + s.more
        if total > 1 { return "\(total) live" }
        return lead(s)?.stateLabel ?? ""
    }

    /// Buttons a device row offers: none once the status is over, at most two.
    static func actions(_ d: LiveStatusDevice, in s: LiveStatusState) -> [LiveStatusAction] {
        guard s.active, d.active else { return [] }
        return Array(d.actions.prefix(2))
    }

    /// Where a tap goes: the lead device's sheet in the app, else the app.
    static func link(_ s: LiveStatusState) -> String {
        guard let id = lead(s)?.id, !id.isEmpty else { return "newman://open" }
        let allowed = CharacterSet.urlQueryAllowed.subtracting(CharacterSet(charactersIn: "&=+#"))
        return "newman://device?id=" + (id.addingPercentEncoding(withAllowedCharacters: allowed) ?? id)
    }

    // MARK: tokens (POST / DELETE /api/live/tokens)

    static func hexToken(_ token: Data) -> String {
        token.map { String(format: "%02x", $0) }.joined()
    }

    /// The ActivityKit push-to-start token (iOS 17.2+): lets the hub start a live status on this phone.
    static func pushToStartBody(token: String, deviceId: String, sandbox: Bool) -> [String: JSONValue] {
        var b: [String: JSONValue] = ["kind": .string("push-to-start"), "token": .string(token), "deviceId": .string(deviceId)]
        if sandbox { b["apnsEnvironment"] = .string("sandbox") }
        return b
    }

    /// One running Live Activity's own push token: the hub's updates and end go to it.
    static func updateBody(activityKey: String, token: String, deviceId: String, sandbox: Bool) -> [String: JSONValue] {
        var b: [String: JSONValue] = [
            "kind": .string("update"), "activityKey": .string(activityKey), "token": .string(token), "deviceId": .string(deviceId),
        ]
        if sandbox { b["apnsEnvironment"] = .string("sandbox") }
        return b
    }

    /// GET /api/live/current for this phone (the hub shows it its person's live statuses).
    static func currentPath(deviceId: String) -> String {
        let allowed = CharacterSet.urlQueryAllowed.subtracting(CharacterSet(charactersIn: "&=+#"))
        return "api/live/current?deviceId=" + (deviceId.addingPercentEncoding(withAllowedCharacters: allowed) ?? deviceId)
    }

    /// DELETE /api/live/tokens: that Live Activity has ended on this phone.
    static func endBody(activityKey: String, deviceId: String) -> [String: JSONValue] {
        ["kind": .string("update"), "activityKey": .string(activityKey), "deviceId": .string(deviceId)]
    }

    // MARK: reconciling with GET /api/live/current

    /// Ends what the hub no longer shows (and duplicates), starts what it shows but this phone
    /// doesn't, and updates the rest.
    static func plan(local: [LiveStatusLocal], current: [String]) -> LiveStatusPlan {
        var p = LiveStatusPlan()
        let wanted = Set(current)
        for l in local {
            if !wanted.contains(l.key) || p.update[l.key] != nil {
                p.end.append(l.id)
            } else {
                p.update[l.key] = l.id
            }
        }
        var seen = Set<String>()
        for k in current where p.update[k] == nil && !seen.contains(k) {
            seen.insert(k)
            p.start.append(k)
        }
        return p
    }

    /// GET /api/live/current?deviceId=…: {"activities": [{key, slot, title, contentState,
    /// attributes: {key, title}}]} (also a bare list, the key / title only under "attributes", the
    /// state as "content-state" / "state"). Nil when the reply isn't a list (an older hub, an error
    /// page): then nothing is ended.
    static func parseCurrent(_ data: Data) -> [LiveStatusCurrent]? {
        guard let root = try? JSONDecoder().decode(JSONValue.self, from: data) else { return nil }
        var list = root.arrayValue
        if list == nil {
            for k in ["activities", "live", "current"] {
                if let a = root[k]?.arrayValue {
                    list = a
                    break
                }
            }
        }
        guard let items = list else { return nil }
        return items.compactMap { item -> LiveStatusCurrent? in
            let attrs = item["attributes"]
            guard let key = item["key"]?.stringValue ?? item["activityKey"]?.stringValue ?? attrs?["key"]?.stringValue,
                  !key.isEmpty else { return nil }
            guard let raw = item["contentState"] ?? item["content-state"] ?? item["state"],
                  raw.objectValue != nil,
                  let data = try? JSONEncoder().encode(raw),
                  let state = try? JSONDecoder().decode(LiveStatusState.self, from: data) else { return nil }
            let title = item["title"]?.stringValue ?? attrs?["title"]?.stringValue ?? state.title
            return LiveStatusCurrent(key: key, title: title, state: state)
        }
    }
}
