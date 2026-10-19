import Foundation

// Editing an automation on the phone: the whole automation as the hub sent it (GET /api/automations/{id}),
// with typed views onto the parts the phone edits - When (trigger items), What happens (actions), Only if
// (device conditions) and Don't repeat for. Everything else (notifications, follow-ups, buttons, trigger
// types the phone doesn't know) rides along untouched and is sent back as it came. Android's
// AutomationDraft.kt follows the same rules; checked on Linux (Tests/LinuxRunner/HomeTests.swift).

struct AutomationDraft: Equatable {
    var raw: [String: JSONValue]

    init(raw: [String: JSONValue]) {
        var r = raw
        // What the list adds; never saved back.
        for k in ["summary", "lastRun", "nextRun"] { r[k] = nil }
        self.raw = r
    }

    /// A new automation: 7:00 every day, nothing happening yet, no notification.
    static func new() -> AutomationDraft {
        AutomationDraft(raw: [
            "name": .string("New automation"),
            "enabled": .bool(true),
            "trigger": .object(["match": .string("any"),
                                "items": .array([.object(TriggerItem.newTime().raw)])]),
            "condition": .object(["mode": .string("any"), "devices": .array([])]),
            "actions": .array([]),
            "recipients": .array([]),
            "notify": .object(["enabled": .bool(false)]),
        ])
    }

    var id: String? { raw["id"]?.stringValue }
    var isNew: Bool { id == nil }

    var name: String {
        get { raw["name"]?.stringValue ?? "" }
        set { raw["name"] = .string(newValue) }
    }

    var enabled: Bool {
        get { raw["enabled"]?.boolValue ?? true }
        set { raw["enabled"] = .bool(newValue) }
    }

    /// Minutes to ignore new triggers after a run: nil = Default (no wait, except a camera detection
    /// uses the camera default from Settings), 0 = No wait.
    var cooldownMinutes: Int? {
        get { raw["cooldownMinutes"]?.doubleValue.map { Int($0) } }
        set { raw["cooldownMinutes"] = newValue.map { JSONValue.number(Double($0)) } ?? JSONValue.null }
    }

    static let cooldownChoices: [Int?] = [nil, 0, 1, 5, 15, 30, 60]

    /// "Default", "No wait", "1 min" … "60 min".
    static func cooldownText(_ m: Int?) -> String {
        guard let m else { return "Default" }
        return m <= 0 ? "No wait" : "\(m) min"
    }

    // MARK: When

    var triggerItems: [TriggerItem] {
        get {
            let t = raw["trigger"]?.objectValue ?? [:]
            if let items = t["items"]?.arrayValue { return items.compactMap { $0.objectValue.map(TriggerItem.init) } }
            // An older automation with only the flat fields: one item of its type.
            return t.isEmpty ? [] : [TriggerItem(raw: t)]
        }
        set {
            var t = raw["trigger"]?.objectValue ?? [:]
            // The hub works out the type and the flat copies from the items.
            for k in ["type", "entities", "to", "time", "days", "entity", "event"] { t[k] = nil }
            t["items"] = .array(newValue.map { .object($0.raw) })
            if t["match"] == nil { t["match"] = .string("any") }
            raw["trigger"] = .object(t)
        }
    }

    // MARK: What happens

    var actions: [ActionItem] {
        get { (raw["actions"]?.arrayValue ?? []).compactMap { $0.objectValue.map(ActionItem.init) } }
        set { raw["actions"] = .array(newValue.map { .object($0.raw) }) }
    }

    // MARK: Only if

    var conditionDevices: [ConditionDevice] {
        get { (raw["condition"]?["devices"]?.arrayValue ?? []).compactMap { $0.objectValue.map(ConditionDevice.init) } }
        set {
            var c = raw["condition"]?.objectValue ?? ["mode": .string("any")]
            c["devices"] = .array(newValue.map { .object($0.raw) })
            // The flat list mirrors the devices (older hubs read it).
            var ids: [JSONValue] = []
            for d in newValue { for e in d.entities where !ids.contains(.string(e)) { ids.append(.string(e)) } }
            c["entities"] = .array(ids)
            raw["condition"] = .object(c)
        }
    }

    /// Conditions the phone shows but doesn't edit (set on the web): their names.
    var otherConditions: [String] {
        let c = raw["condition"]?.objectValue ?? [:]
        var out: [String] = []
        if let b = c["between"], !b.isNull { out.append("At certain times") }
        if let d = c["days"], !d.isNull { out.append("On certain days") }
        if let s = c["sun"], !s.isNull { out.append("Depending on the sun") }
        if let d = c["dates"], !d.isNull { out.append("On certain dates") }
        return out
    }

    /// What's sent to save it (POST / PUT, and POST /api/automations/describe).
    var body: [String: JSONValue] { raw }
}

/// One "When": a time, a device changing, a device event, or a reading crossing a value - or something
/// the phone doesn't edit (kept as it is).
struct TriggerItem: Equatable {
    var raw: [String: JSONValue]

    static let editableTypes: Set<String> = ["time", "state", "event", "property"]

    var type: String { raw["type"]?.stringValue ?? "time" }
    var isEditable: Bool { TriggerItem.editableTypes.contains(type) }

    static func newTime() -> TriggerItem {
        TriggerItem(raw: ["type": .string("time"), "time": .string("07:00"), "days": .array((0...6).map { .number(Double($0)) })])
    }
    static func newState(_ entity: String, to: String) -> TriggerItem {
        TriggerItem(raw: ["type": .string("state"), "entities": .array([.string(entity)]), "to": .string(to)])
    }
    static func newEvent(_ entity: String, event: String) -> TriggerItem {
        TriggerItem(raw: ["type": .string("event"), "entity": .string(entity), "event": .string(event)])
    }
    static func newProperty(_ entity: String, property: String, value: Double) -> TriggerItem {
        TriggerItem(raw: ["type": .string("property"), "entities": .array([.string(entity)]), "property": .string(property),
                          "op": .string("above"), "value": .number(value)])
    }

    /// "HH:MM" (24 h).
    var time: String {
        get { raw["time"]?.stringValue ?? "07:00" }
        set { raw["time"] = .string(newValue) }
    }
    /// 0 = Monday … 6 = Sunday.
    var days: [Int] {
        get { (raw["days"]?.arrayValue ?? []).compactMap { $0.doubleValue.map { Int($0) } } }
        set { raw["days"] = .array(newValue.sorted().map { .number(Double($0)) }) }
    }
    var entities: [String] {
        get {
            if let list = raw["entities"]?.arrayValue { return list.compactMap { $0.stringValue } }
            return raw["entity"]?.stringValue.map { [$0] } ?? []
        }
        set {
            if type == "event" {
                raw["entity"] = newValue.first.map { JSONValue.string($0) } ?? JSONValue.null
            } else {
                raw["entities"] = .array(newValue.map { JSONValue.string($0) })
            }
        }
    }
    var to: String {
        get { raw["to"]?.stringValue ?? "any" }
        set { raw["to"] = .string(newValue) }
    }
    var event: String {
        get { raw["event"]?.stringValue ?? "" }
        set { raw["event"] = .string(newValue) }
    }
    var property: String {
        get { raw["property"]?.stringValue ?? "" }
        set { raw["property"] = .string(newValue) }
    }
    /// "above" or "below".
    var op: String {
        get { raw["op"]?.stringValue ?? "above" }
        set { raw["op"] = .string(newValue) }
    }
    var value: Double {
        get { raw["value"]?.doubleValue ?? 0 }
        set { raw["value"] = .number(newValue) }
    }
    /// "for 15 minutes" on a state or reading: in minutes, nil when not set.
    var forMinutes: Int? {
        get { Durations.minutes(raw["for"]) }
        set { raw["for"] = newValue.map { Durations.json(minutes: $0) } ?? JSONValue.null }
    }

    static let dayLetters = ["M", "T", "W", "T", "F", "S", "S"]
    static let dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    /// The day chips' order on screen, Sunday first: S M T W T F S (the data stays 0 = Monday).
    static let displayDays = [6, 0, 1, 2, 3, 4, 5]

    /// "Every day", "Weekdays", "Weekends", "Mon, Wed", "No days".
    static func daysText(_ days: [Int]) -> String {
        let d = Array(Set(days)).sorted()
        if d.count == 7 { return "Every day" }
        if d == [0, 1, 2, 3, 4] { return "Weekdays" }
        if d == [5, 6] { return "Weekends" }
        if d.isEmpty { return "No days" }
        return d.filter { (0...6).contains($0) }.map { dayNames[$0] }.joined(separator: ", ")
    }

    /// "06:20" → (6, 20); nil when it isn't a time.
    static func hourMinute(_ hhmm: String) -> (Int, Int)? {
        let parts = hhmm.split(separator: ":")
        guard parts.count >= 2, let h = Int(parts[0]), let m = Int(parts[1]), (0..<24).contains(h), (0..<60).contains(m) else { return nil }
        return (h, m)
    }

    static func hhmm(_ h: Int, _ m: Int) -> String { String(format: "%02d:%02d", h, m) }
}

/// One "What happens": devices, a command and its value, and optionally "then put it back after N minutes".
struct ActionItem: Equatable {
    var raw: [String: JSONValue]

    static func new(_ entities: [String], command: String) -> ActionItem {
        ActionItem(raw: ["entities": .array(entities.map { .string($0) }), "command": .string(command)])
    }

    /// The phone edits device commands; anything else (set on the web) is shown and kept as it is.
    var isEditable: Bool { raw["command"]?.stringValue != nil && raw["entities"]?.arrayValue != nil }

    var entities: [String] {
        get { (raw["entities"]?.arrayValue ?? []).compactMap { $0.stringValue } }
        set { raw["entities"] = .array(newValue.map { .string($0) }) }
    }
    var command: String {
        get { raw["command"]?.stringValue ?? "" }
        set { raw["command"] = .string(newValue) }
    }
    var value: JSONValue? {
        get { raw["value"].flatMap { $0.isNull ? nil : $0 } }
        set { raw["value"] = newValue }
    }
    var forMinutes: Int? {
        get { Durations.minutes(raw["for"]) }
        set { raw["for"] = newValue.map { Durations.json(minutes: $0) } }
    }
}

/// One "Only if": these devices are in this state.
struct ConditionDevice: Equatable {
    var raw: [String: JSONValue]

    static func new(_ entity: String, state: String) -> ConditionDevice {
        ConditionDevice(raw: ["entities": .array([.string(entity)]), "state": .string(state), "match": .string("any")])
    }

    var entities: [String] {
        get { (raw["entities"]?.arrayValue ?? []).compactMap { $0.stringValue } }
        set { raw["entities"] = .array(newValue.map { .string($0) }) }
    }
    var state: String {
        get { raw["state"]?.stringValue ?? "" }
        set { raw["state"] = .string(newValue) }
    }
}

/// The hub's {"amount", "unit"} durations, in minutes.
enum Durations {
    static func minutes(_ v: JSONValue?) -> Int? {
        guard let o = v?.objectValue, let amount = o["amount"]?.doubleValue else { return nil }
        switch o["unit"]?.stringValue ?? "minutes" {
        case "seconds": return Int((amount / 60).rounded(.up))
        case "hours": return Int(amount * 60)
        default: return Int(amount)
        }
    }

    static func json(minutes: Int) -> JSONValue { .object(["amount": .number(Double(minutes)), "unit": .string("minutes")]) }
}

/// POST /api/automations/describe: the hub's plain-English summary and anything stopping a save.
struct AutomationDescription: Decodable, Equatable {
    var summary: String?
    var errors: [String]?
}
