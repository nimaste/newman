import Foundation

// The More tab's lists: every device the hub has (Devices) and the house's automations
// (Automations). The Android app's More tab follows the same rules; checked on Linux
// (Tests/LinuxRunner/MoreTests.swift).

// MARK: - Automations (GET /api/automations, GET /api/runs)

/// One run of an automation: when it started and how it ended ("done", "sent", "skipped", "failed"…).
struct AutomationRun: Decodable, Hashable, Identifiable {
    var id: String
    var startedAt: Double?
    var status: String?

    enum CodingKeys: String, CodingKey { case id, startedAt, status }

    init(id: String = "", startedAt: Double? = nil, status: String? = nil) {
        self.id = id
        self.startedAt = startedAt
        self.status = status
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        startedAt = try? c.decodeIfPresent(Double.self, forKey: .startedAt)
        status = try? c.decodeIfPresent(String.self, forKey: .status)
        id = (try? c.decodeIfPresent(String.self, forKey: .id)) ?? "\(startedAt ?? 0)"
    }
}

/// An automation as the phone lists it: its name, the hub's own one-line summary, on or off, and
/// its last run. (The full automation stays on the hub; the phone only switches it on or off.)
struct AutomationSummary: Decodable, Hashable, Identifiable {
    var id: String
    var name: String
    var enabled: Bool
    var summary: String?
    var lastRun: AutomationRun?

    enum CodingKeys: String, CodingKey { case id, name, enabled, summary, lastRun }

    init(id: String, name: String, enabled: Bool = true, summary: String? = nil, lastRun: AutomationRun? = nil) {
        self.id = id
        self.name = name
        self.enabled = enabled
        self.summary = summary
        self.lastRun = lastRun
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? id
        enabled = (try? c.decodeIfPresent(Bool.self, forKey: .enabled)) ?? true
        summary = try? c.decodeIfPresent(String.self, forKey: .summary)
        lastRun = try? c.decodeIfPresent(AutomationRun.self, forKey: .lastRun)
    }
}

enum AutomationList {
    /// Most recently run first; never run last (by name).
    static func sorted(_ list: [AutomationSummary]) -> [AutomationSummary] {
        list.sorted { a, b in
            let ta = a.lastRun?.startedAt ?? -1, tb = b.lastRun?.startedAt ?? -1
            if ta != tb { return ta > tb }
            return a.name.localizedCaseInsensitiveCompare(b.name) == .orderedAscending
        }
    }

    static func ranToday(_ list: [AutomationSummary], now: Date, calendar: Calendar = .current) -> Int {
        list.filter { a in
            guard let t = a.lastRun?.startedAt else { return false }
            return calendar.isDate(Date(timeIntervalSince1970: t), inSameDayAs: now)
        }.count
    }

    /// "13 automations · all on" / "13 automations · 2 off" (More's row).
    static func countLine(_ list: [AutomationSummary]) -> String {
        let n = list.count
        if n == 0 { return "No automations yet" }
        let off = list.filter { !$0.enabled }.count
        let head = n == 1 ? "1 automation" : "\(n) automations"
        if off == 0 { return head + (n == 1 ? " · on" : " · all on") }
        if off == n { return head + (n == 1 ? " · off" : " · all off") }
        return head + " · \(off) off"
    }

    /// "13 automations · all on · 4 ran today" (the Automations screen's line).
    static func headerLine(_ list: [AutomationSummary], now: Date, calendar: Calendar = .current) -> String {
        let ran = ranToday(list, now: now, calendar: calendar)
        return countLine(list) + (ran > 0 ? " · \(ran) ran today" : "")
    }

    /// "Ran today 2:54 PM", "Sent yesterday 3:07 PM", "Checked Oct 12, 2:53 PM · nothing to do",
    /// "Hasn't run yet".
    static func lastRunLine(_ run: AutomationRun?, now: Date, calendar: Calendar = .current) -> String {
        guard let t = run?.startedAt else { return "Hasn't run yet" }
        let status = run?.status ?? ""
        let verb: String
        switch status {
        case "sent": verb = "Sent"
        case "skipped": verb = "Checked"
        case "failed", "error": verb = "Failed"
        default: verb = "Ran"
        }
        return verb + " " + when(Date(timeIntervalSince1970: t), now: now, calendar: calendar)
            + (status == "skipped" ? " · nothing to do" : "")
    }

    /// "today 2:54 PM", "yesterday 6:20 PM", "Oct 12, 2:53 PM".
    static func when(_ d: Date, now: Date, calendar: Calendar = .current) -> String {
        let time = HubDates.time(d)
        if calendar.isDate(d, inSameDayAs: now) { return "today \(time)" }
        if let y = calendar.date(byAdding: .day, value: -1, to: now), calendar.isDate(d, inSameDayAs: y) { return "yesterday \(time)" }
        let f = DateFormatter()
        f.calendar = calendar
        f.timeZone = calendar.timeZone
        f.setLocalizedDateFormatFromTemplate("MMMd")
        return "\(f.string(from: d)), \(time)"
    }

    /// A run's status in words ("Done", "Sent", "Nothing to do"…).
    static func statusLabel(_ status: String?) -> String {
        switch status ?? "" {
        case "": return ""
        case "skipped": return "Nothing to do"
        default: return DeviceLogic.humanize(status ?? "")
        }
    }

    /// PATCH /api/automations/{id}
    static func enabledBody(_ on: Bool) -> [String: JSONValue] { ["enabled": .bool(on)] }

    /// The list with one automation switched (the phone shows it at once; put back on error).
    static func setting(_ list: [AutomationSummary], id: String, enabled: Bool) -> [AutomationSummary] {
        list.map { a in
            guard a.id == id else { return a }
            var b = a
            b.enabled = enabled
            return b
        }
    }
}

// MARK: - Devices (every device the hub has, by category)

enum DeviceDirectory {
    enum Filter: String, CaseIterable { case all, attention, on }

    struct Section: Hashable, Identifiable {
        var id: String
        var title: String
        /// Needing attention first, then by name.
        var ids: [String]
    }

    /// Something to look at: the hub reports a problem, or it's offline.
    static func needsAttention(_ e: Entity) -> Bool { e.health?.ok == false || !e.isAvailable }

    /// On right now, like a light that's on (the kind's active state; not a lock or door).
    static func isOn(_ e: Entity, _ kind: Kind?) -> Bool { e.isAvailable && DeviceLogic.isLit(e, kind) }

    static func category(_ e: Entity, _ kind: Kind?) -> String {
        let c = (e.category ?? kind?.category ?? "").trimmingCharacters(in: .whitespaces)
        return c.isEmpty ? "other" : c
    }

    /// The devices a search and a filter leave.
    static func matching(_ entities: [Entity], kinds: [String: Kind], query: String, filter: Filter) -> [Entity] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        return entities.filter { e in
            if !q.isEmpty && !e.name.localizedCaseInsensitiveContains(q) { return false }
            switch filter {
            case .all: return true
            case .attention: return needsAttention(e)
            case .on: return isOn(e, kinds[e.kind])
            }
        }
    }

    /// By category in the hub's order (GET /api/meta categories), unknown ones after them by name.
    static func sections(_ entities: [Entity], kinds: [String: Kind], categories: [MetaCategory]) -> [Section] {
        var groups: [String: [Entity]] = [:]
        for e in entities { groups[category(e, kinds[e.kind]), default: []].append(e) }
        let order = categories.map { $0.id }
        let known = order.filter { groups[$0] != nil }
        let unknown = groups.keys.filter { !order.contains($0) }.sorted()
        return (known + unknown).map { id in
            let list = (groups[id] ?? []).sorted { a, b in
                let pa = needsAttention(a), pb = needsAttention(b)
                if pa != pb { return pa }
                return a.name.localizedCaseInsensitiveCompare(b.name) == .orderedAscending
            }
            let title = categories.first { $0.id == id }?.name ?? DeviceLogic.humanize(id)
            return Section(id: id, title: title, ids: list.map { $0.id })
        }
    }

    /// "80 devices · 1 needs attention" (More's row).
    static func countLine(_ entities: [Entity]) -> String {
        let n = entities.count
        let bad = entities.filter(needsAttention).count
        let head = n == 1 ? "1 device" : "\(n) devices"
        if bad == 0 { return head }
        return head + (bad == 1 ? " · 1 needs attention" : " · \(bad) need attention")
    }

    /// A row's second line: the problems when there are some, else its state (and battery).
    static func statusLine(_ e: Entity, _ kind: Kind?) -> String {
        if !e.issues.isEmpty { return e.issues.joined(separator: " · ") }
        if !e.isAvailable { return "Offline" }
        let battery = DeviceLogic.vitals(e).battery
        return [DeviceLogic.stateText(e, kind), battery.map { "battery \($0)%" }]
            .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
    }

    /// An on/off pair the row can show as a switch (the same pair a device tile's tap uses).
    static func switchPair(_ kind: Kind?) -> (on: Command, off: Command)? {
        guard let kind, let active = kind.activeState else { return nil }
        let plain = DeviceLogic.plainCommands(kind)
        guard plain.count == 2, !plain.contains(where: { $0.isSensitive }),
              let on = plain.first(where: { $0.confirmsTo == active }),
              let off = plain.first(where: { $0 != on && !($0.confirmsTo ?? "").isEmpty }) else { return nil }
        return (on, off)
    }
}
