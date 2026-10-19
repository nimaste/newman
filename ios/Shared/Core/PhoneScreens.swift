import Foundation

// The phone screens' shared wording and maths (Home cards and status, the one-camera screen's
// detections, Today's Coming up, More's lines, a tapped notification's alert), the same on Android
// (core/home). Checked on Linux (Tests/LinuxRunner/ScreensTests.swift).

// MARK: - Home cards and status

enum HomeCards {
    struct Style: Equatable {
        /// SF Symbol name.
        var symbol: String
        var color: UInt32
    }

    static let blue: UInt32 = 0x7AA2FF
    static let orange: UInt32 = 0xFF9A4D
    static let amber: UInt32 = 0xF5C451
    static let green: UInt32 = 0x4FD1A5
    static let purple: UInt32 = 0xB79CFF
    /// A card for a category without a purpose-built screen.
    static let neutral: UInt32 = 0xA3AED0

    /// The purpose-built screens' icons and colours, by screen role (the app's own screens - not
    /// categories or kinds; any category the hub points at one of them gets its look).
    static let styles: [String: Style] = [
        "cameras": Style(symbol: "video.fill", color: blue),
        "climate": Style(symbol: "thermometer.medium", color: orange),
        "lights": Style(symbol: "lightbulb.fill", color: amber),
        "security": Style(symbol: "shield.lefthalf.filled", color: green),
        "today": Style(symbol: "calendar", color: blue),
        "people": Style(symbol: "person.fill", color: purple),
    ]

    /// The screens the app has.
    static var roles: Set<String> { Set(styles.keys) }

    /// A card's look by its screen role; nil for a category with no screen (it's drawn with its devices'
    /// most common icon in the neutral colour - CategoryLogic.mostCommonIcon).
    static func style(role: String?) -> Style? { role.flatMap { styles[$0] } }

    /// The value's text size: a number (≤ 5 characters) 30, a word or two ("All locked", ≤ 12) 22, else 18.
    static func valueSize(_ value: String) -> Int {
        let n = value.count
        return n <= 5 ? 30 : (n <= 12 ? 22 : 18)
    }
}

enum HomeStatusMode: Equatable {
    /// "All good at home" + the summary.
    case ok
    /// "Needs a look" + the summary, then the amber rows.
    case warn
    /// A red banner per danger, then the amber rows.
    case danger
}

extension HomeData.Status {
    var mode: HomeStatusMode {
        if !danger.isEmpty { return .danger }
        if level == "warn" || level == "danger" || !warnings.isEmpty { return .warn }
        return .ok
    }
}

// MARK: - More

enum MoreLines {
    /// "Chris is home", "2 people home", "No one is home".
    static func people(_ people: [Person]) -> String {
        let home = people.filter { $0.home }
        switch home.count {
        case 0: return "No one is home"
        case 1: return "\(home[0].name) is home"
        default: return "\(home.count) people home"
        }
    }

    /// "9 · all on", "9 · 2 off".
    static func automations(_ list: [AutomationSummary]) -> String {
        let off = list.filter { !$0.enabled }.count
        return off == 0 ? "\(list.count) · all on" : "\(list.count) · \(off) off"
    }

    static let account = "Look, location sharing, password, sign out"
}

// MARK: - One camera: what it saw (GET /api/alerts?entity=<camera>)

/// One detection: {"id", "at", "label", "zone", "snapshot", "clip" (the signed-in api/clips/… path)}
/// plus the connection's own fields (thumbUrl, objects, title…).
struct Detection: Decodable, Hashable, Identifiable {
    var id: String
    var at: Double?
    var label: String
    var zone: String?
    var snapshot: String?
    var clip: String?
    var thumbUrl: String?
    var title: String?
    var entityId: String?
    var cameraName: String?
    var objects: [String]?
    var zones: [String]?

    enum CodingKeys: String, CodingKey {
        case id, at, start, label, zone, snapshot, clip, thumbUrl, snapshotUrl, title, entityId, cameraName, objects, zones
    }

    init(id: String, at: Double?, label: String, zone: String? = nil, snapshot: String? = nil, clip: String? = nil) {
        self.id = id
        self.at = at
        self.label = label
        self.zone = zone
        self.snapshot = snapshot
        self.clip = clip
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let raw = try? c.decodeIfPresent(JSONValue.self, forKey: .id)
        id = raw.map { $0.displayText } ?? UUID().uuidString
        at = (try? c.decodeIfPresent(Double.self, forKey: .at)) ?? (try? c.decodeIfPresent(Double.self, forKey: .start))
        objects = try? c.decodeIfPresent([String].self, forKey: .objects)
        label = (try? c.decodeIfPresent(String.self, forKey: .label))
            ?? objects?.first.map { $0.prefix(1).uppercased() + $0.dropFirst() } ?? "Activity"
        zones = try? c.decodeIfPresent([String].self, forKey: .zones)
        zone = (try? c.decodeIfPresent(String.self, forKey: .zone)) ?? zones?.first
        snapshot = (try? c.decodeIfPresent(String.self, forKey: .snapshot)) ?? (try? c.decodeIfPresent(String.self, forKey: .snapshotUrl))
        clip = try? c.decodeIfPresent(String.self, forKey: .clip)
        thumbUrl = try? c.decodeIfPresent(String.self, forKey: .thumbUrl)
        title = try? c.decodeIfPresent(String.self, forKey: .title)
        entityId = try? c.decodeIfPresent(String.self, forKey: .entityId)
        cameraName = try? c.decodeIfPresent(String.self, forKey: .cameraName)
    }

    /// The camera screen's alert for it (its clip as "<connection>:<clip id>", AlertClip).
    func cameraAlert(camera: String) -> CameraAlert {
        CameraAlert(id: id, camera: nil, cameraName: cameraName, entityId: entityId ?? camera, severity: nil,
                    title: zone.map { "\(label) at \($0)" } ?? label, objects: objects, zones: zones ?? zone.map { [$0] },
                    start: at, end: nil, clip: AlertClip.clipId(fromPath: clip), thumbUrl: thumbUrl ?? snapshot,
                    snapshotUrl: snapshot, integration: nil)
    }
}

extension AlertClip {
    /// "api/clips/frigate/r:abc" → "frigate:r:abc" (what `path` turns back into the address); a clip
    /// that is already "<connection>:<id>" stays as it is.
    static func clipId(fromPath p: String?) -> String? {
        guard var s = p, !s.isEmpty else { return nil }
        if s.hasPrefix("/") { s.removeFirst() }
        let prefix = "api/clips/"
        guard s.hasPrefix(prefix) else { return path(s) != nil ? s : nil }
        let rest = s.dropFirst(prefix.count)
        guard let slash = rest.firstIndex(of: "/") else { return nil }
        let iid = String(rest[..<slash]), ev = String(rest[rest.index(after: slash)...])
        let decodedEv = ev.removingPercentEncoding ?? ev
        return iid.isEmpty || decodedEv.isEmpty ? nil : "\(iid):\(decodedEv)"
    }
}

enum DetectionLogic {
    struct Chip: Equatable {
        var label: String
        var count: Int
    }

    /// One chip per label present, in the order first seen, with how many.
    static func chips(_ list: [Detection]) -> [Chip] {
        var out: [Chip] = []
        for d in list {
            if let i = out.firstIndex(where: { $0.label == d.label }) { out[i].count += 1 } else { out.append(Chip(label: d.label, count: 1)) }
        }
        return out
    }

    /// From when to load: the start of today, then a day earlier for each "Earlier days" tap.
    static func since(daysBack: Int, now: Date, calendar: Calendar = .current) -> Double {
        let start = calendar.startOfDay(for: now)
        return (calendar.date(byAdding: .day, value: -daysBack, to: start) ?? start).timeIntervalSince1970
    }

    /// "Driveway · 8:35 AM".
    static func line(_ d: Detection, time: (Date) -> String) -> String {
        [d.zone ?? "", d.at.map { time(Date(timeIntervalSince1970: $0)) } ?? ""].filter { !$0.isEmpty }.joined(separator: " · ")
    }
}

// MARK: - Security: today's events (GET /api/alerts?category=security&since=…)

struct SecurityEntry: Decodable, Hashable, Identifiable {
    var id: String
    var at: Double
    var title: String
    var entity: String?
    var clip: String?
    var snapshot: String?

    enum CodingKeys: String, CodingKey { case id, at, title, entity, clip, snapshot }

    init(id: String, at: Double, title: String, entity: String? = nil) {
        self.id = id
        self.at = at
        self.title = title
        self.entity = entity
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? c.decodeIfPresent(JSONValue.self, forKey: .id))?.displayText ?? UUID().uuidString
        at = (try? c.decodeIfPresent(Double.self, forKey: .at)) ?? 0
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? ""
        entity = try? c.decodeIfPresent(String.self, forKey: .entity)
        clip = try? c.decodeIfPresent(String.self, forKey: .clip)
        snapshot = try? c.decodeIfPresent(String.self, forKey: .snapshot)
    }

    /// Newest first.
    static func sorted(_ list: [SecurityEntry]) -> [SecurityEntry] { list.sorted { $0.at > $1.at } }
}

// MARK: - A tapped notification (its alertId = the automation run: GET /api/events?runId=)

enum NotificationAlert {
    enum Target: Equatable {
        /// The camera's screen, playing this clip.
        case camera(String, CameraAlert)
        /// More › Alerts, at this entry.
        case alerts(String)
    }

    static func target(_ ev: ActivityEvent) -> Target {
        if let clip = ev.data?["clip"]?.stringValue, AlertClip.path(clip) != nil, let cam = ev.entityId {
            var snap = ev.snapshotPath
            if snap == nil, let sid = ev.data?["snapshotId"]?.stringValue { snap = "api/snapshots/\(sid)" }
            let alert = CameraAlert(id: ev.id, camera: nil, cameraName: nil, entityId: cam, severity: nil, title: ev.title,
                                    objects: nil, zones: nil, start: ev.ts, end: nil, clip: clip,
                                    thumbUrl: ev.thumbPath ?? snap, snapshotUrl: snap, integration: nil)
            return .camera(cam, alert)
        }
        return .alerts(ev.id)
    }
}

// MARK: - Today: Coming up

/// GET /api/upkeep's items (the upkeep list): name, state and how far off.
struct UpkeepItem: Decodable, Hashable, Identifiable {
    var id: String
    var name: String
    /// "overdue" | "due" | "soon" | "ok"
    var state: String
    var daysLeft: Int?
    var due: String?
    /// Its device id (Today's items name it as `entity`).
    var entity: String?

    enum CodingKeys: String, CodingKey { case id, name, state, daysLeft, due, entity }

    init(id: String, name: String, state: String, daysLeft: Int? = nil, entity: String? = nil) {
        self.id = id
        self.entity = entity
        self.name = name
        self.state = state
        self.daysLeft = daysLeft
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? c.decodeIfPresent(String.self, forKey: .id)) ?? ""
        name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? ""
        state = (try? c.decodeIfPresent(String.self, forKey: .state)) ?? "ok"
        daysLeft = (try? c.decodeIfPresent(Double.self, forKey: .daysLeft)).map { Int($0) }
        due = try? c.decodeIfPresent(String.self, forKey: .due)
        entity = try? c.decodeIfPresent(String.self, forKey: .entity)
    }

    /// "3 days late", "Due today", "Tomorrow", "In 5 days", "Up to date".
    var statusText: String {
        switch state {
        case "overdue":
            let late = max(1, -(daysLeft ?? -1))
            return late == 1 ? "1 day late" : "\(late) days late"
        case "due": return "Due today"
        case "soon":
            guard let d = daysLeft, d <= 999 else { return "Running hours almost up" }
            return d <= 1 ? "Tomorrow" : "In \(d) days"
        default: return "Up to date"
        }
    }
}

struct UpkeepList: Decodable {
    var items: [UpkeepItem]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        items = (try? c.decodeIfPresent([UpkeepItem].self, forKey: .items)) ?? []
    }

    enum CodingKeys: String, CodingKey { case items }
}

enum TodayScreenLogic {
    struct Upcoming: Hashable {
        var day: String
        var title: String
        var calendar: String
        var start: String
    }

    /// The calendars: devices carrying an "upcoming" list (any calendar connection).
    static func calendars(_ entities: [Entity]) -> [Entity] {
        entities.filter { $0.attr("upcoming")?.arrayValue != nil }
    }

    /// "Nothing on the Family calendar today", "Nothing on your calendars today", "No calendar yet".
    static func emptyCalendarLine(_ calendars: [Entity]) -> String {
        switch calendars.count {
        case 0: return "No calendar yet"
        case 1: return "Nothing on the \(calendars[0].name) calendar today"
        default: return "Nothing on your calendars today"
        }
    }

    /// The events of the next 7 days after today, soonest first ("Tomorrow", "Thursday").
    static func comingUp(_ calendars: [Entity], now: Date, days: Int = 7, calendar: Calendar = .current) -> [Upcoming] {
        let tomorrow = calendar.date(byAdding: .day, value: 1, to: calendar.startOfDay(for: now)) ?? now
        let until = calendar.date(byAdding: .day, value: days, to: tomorrow) ?? tomorrow
        var out: [(Date, Upcoming)] = []
        for c in calendars {
            for ev in c.attr("upcoming")?.arrayValue ?? [] {
                guard let start = ev["start"]?.stringValue, let d = parse(start), d >= tomorrow, d < until else { continue }
                out.append((d, Upcoming(day: dayText(d, now: now, calendar: calendar),
                                        title: ev["title"]?.stringValue ?? "Busy", calendar: c.name, start: start)))
            }
        }
        return out.sorted { $0.0 < $1.0 }.map { $0.1 }
    }

    /// "Tomorrow", a weekday within the week, else "Oct 28".
    static func dayText(_ d: Date, now: Date, calendar: Calendar = .current) -> String {
        let days = calendar.dateComponents([.day], from: calendar.startOfDay(for: now), to: calendar.startOfDay(for: d)).day ?? 0
        if days == 1 { return "Tomorrow" }
        let f = DateFormatter()
        f.calendar = calendar
        f.timeZone = calendar.timeZone
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = days < 7 ? "EEEE" : "MMM d"
        return f.string(from: d)
    }

    /// "2026-10-21T09:00:00-04:00", "2026-10-21T09:00:00" (local) or "2026-10-21" (all day).
    static func parse(_ s: String) -> Date? {
        let iso = ISO8601DateFormatter()
        if let d = iso.date(from: s) { return d }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        for format in ["yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd'T'HH:mm", "yyyy-MM-dd"] {
            f.dateFormat = format
            if let d = f.date(from: s) { return d }
        }
        return nil
    }

    /// The upkeep rows of Coming up: the items coming due (not already in To do).
    static func upkeep(_ items: [UpkeepItem], todo: [TodayItem]) -> [UpkeepItem] {
        let inTodo = Set(todo.compactMap { $0.entity })
        return items.filter { !inTodo.contains($0.entity ?? "") && $0.state == "soon" }
    }

    /// "+ Add": its last time done one interval ago, so the new to-do is due today ("2026-09-19").
    static func lastDoneForDueToday(every: Int, unit: String, now: Date, calendar: Calendar = .current) -> String {
        let component: Calendar.Component = unit == "weeks" ? .weekOfYear : (unit == "months" ? .month : .day)
        let d = calendar.date(byAdding: component, value: -every, to: now) ?? now
        let c = calendar.dateComponents([.year, .month, .day], from: d)
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }
}
