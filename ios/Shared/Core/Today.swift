import Foundation

// GET /api/today (server/newman/today.py): what needs someone today, what's on and heads-up,
// worked out on the hub so the wall, the phone and voice say the same. On a phone the hub sends
// this person's items and everyone's. Done / Undo / Remind tomorrow go to /api/upkeep/{id}/...;
// the hub records the signed-in person as who did it. Mirrors android core/today/Today.kt.

struct TodayItem: Decodable, Hashable, Identifiable {
    var id: String
    /// "upkeep" | "device" | "appliance"
    var source: String
    var entity: String?
    var title: String
    var status: String
    /// "overdue" | "device" | "due" | "appliance" | "soon"
    var level: String
    /// "done" | "open" | "dismiss"
    var action: String
    var owner: String

    enum CodingKeys: String, CodingKey { case id, source, entity, title, status, level, action, owner }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        source = (try? c.decodeIfPresent(String.self, forKey: .source)) ?? ""
        entity = try? c.decodeIfPresent(String.self, forKey: .entity)
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? ""
        status = (try? c.decodeIfPresent(String.self, forKey: .status)) ?? ""
        level = (try? c.decodeIfPresent(String.self, forKey: .level)) ?? ""
        action = (try? c.decodeIfPresent(String.self, forKey: .action)) ?? ""
        owner = (try? c.decodeIfPresent(String.self, forKey: .owner)) ?? ""
    }
}

struct TodayEvent: Decodable, Hashable {
    var title: String?
    var allDay: Bool?
    var time: String?
    var start: String?
    var end: String?
    var location: String?
    var calendar: String?
    var colour: String?
    var now: Bool?
}

struct HeadsUp: Decodable, Hashable, Identifiable {
    var id: String
    var source: String?
    /// "warning" | "rain" | "calendar" | "clock"
    var icon: String?
    var text: String
    var entity: String?
    /// A few lower-case words for a one-line summary ("rain from 3 PM").
    var short: String?
    /// A weather alert's type: "warning" | "watch" | "advisory" | "statement".
    var level: String?
}

struct TodayBriefing: Decodable, Hashable {
    var date: String?
    var todo: [TodayItem]
    var soon: [TodayItem]
    var events: [TodayEvent]
    var headsUp: [HeadsUp]

    enum CodingKeys: String, CodingKey { case date, todo, soon, events, headsUp }

    init(date: String? = nil, todo: [TodayItem] = [], soon: [TodayItem] = [], events: [TodayEvent] = [], headsUp: [HeadsUp] = []) {
        self.date = date
        self.todo = todo
        self.soon = soon
        self.events = events
        self.headsUp = headsUp
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = try? c.decodeIfPresent(String.self, forKey: .date)
        todo = (try? c.decodeIfPresent([TodayItem].self, forKey: .todo)) ?? []
        soon = (try? c.decodeIfPresent([TodayItem].self, forKey: .soon)) ?? []
        events = (try? c.decodeIfPresent([TodayEvent].self, forKey: .events)) ?? []
        headsUp = (try? c.decodeIfPresent([HeadsUp].self, forKey: .headsUp)) ?? []
    }

    var isEmpty: Bool { todo.isEmpty && soon.isEmpty && events.isEmpty && headsUp.isEmpty }
}

/// The phone's Today card, from the briefing plus what was just done on this phone.
enum TodayView {
    static let rows = 4
    static let maxEvents = 3
    static let maxHeadsUp = 2
    /// A row just marked done stays (struck through, with Undo) this long.
    static let keepDoneSeconds = 600.0

    enum Tone { case urgent, due, later, done }
    enum Button { case done, snooze, undo, open, dismiss }

    struct Row: Hashable, Identifiable {
        var id: String
        var title: String
        var status: String
        var tone: Tone
        var buttons: [Button]
        var entity: String?
        var item: TodayItem
    }

    /// Something done on this phone (kept for Undo). `upkeepId` is nil for a dismissed appliance need.
    struct Done: Hashable {
        var item: TodayItem
        var upkeepId: String?
        var at: Double
    }

    struct Card {
        var summary: String
        var rows: [Row]
        var more: Int
        var events: [TodayEvent]
        var headsUp: [HeadsUp]
        var empty: Bool
    }

    static func greeting(hour: Int) -> String {
        hour < 12 ? "Good morning" : (hour < 18 ? "Good afternoon" : "Good evening")
    }

    /// "3 things to do · 2 events today · rain from 3 PM" (the wall's greeting line, web
    /// TodayPage.jsx summaryLine): open To do (less what was just done here), today's events, the
    /// most serious weather alert and rain.
    static func greetingLine(_ b: TodayBriefing, done: [String: Done], now: Double) -> String {
        let recent = done.filter { now - $0.value.at < keepDoneSeconds }
        let open = b.todo.filter { recent[$0.id] == nil }.count
        let events = b.events.count
        let weather = b.headsUp.filter { $0.source == "weather" }
        // The most serious official alert: a warning before a watch, an advisory or a statement.
        var warning: HeadsUp?
        for h in weather where h.icon == "warning" {
            if let w = warning, alertRank(w.level) <= alertRank(h.level) { continue }
            warning = h
        }
        let rain = weather.first { $0.icon == "rain" }
        var parts = [open > 0 ? "\(open) \(open == 1 ? "thing" : "things") to do" : "Nothing to do"]
        if events > 0 { parts.append("\(events) \(events == 1 ? "event" : "events") today") }
        for h in [warning, rain].compactMap({ $0 }) {
            let s = shortText(h)
            if !s.isEmpty { parts.append(s) }
        }
        return parts.joined(separator: " · ")
    }

    private static func alertRank(_ level: String?) -> Int {
        switch level {
        case "warning": return 1
        case "watch": return 2
        case "advisory": return 3
        default: return 9
        }
    }

    /// A heads-up in a few words: the hub's `short`, else its text in lower case.
    static func shortText(_ h: HeadsUp) -> String {
        if let s = h.short, !s.trimmingCharacters(in: .whitespaces).isEmpty { return s }
        return h.text.lowercased()
    }

    /// The greeting tile's look for a card `height` points inside: the greeting's size, and
    /// whether the line fits under it.
    struct GreetingFit: Equatable {
        var size: Double
        var twoLines: Bool
    }

    static func greetingFit(height: Double) -> GreetingFit {
        let size = height == .infinity ? 26 : min(26, max(14, (height * 0.42).rounded()))
        return GreetingFit(size: size, twoLines: height >= size * 1.3 + TileFit.line)
    }

    /// The upkeep item id behind a To do entry: its entity is "upkeep.<id>"; the API takes the id.
    static func upkeepId(_ item: TodayItem) -> String {
        let s = item.entity ?? item.id
        guard let dot = s.firstIndex(of: ".") else { return s }
        return String(s[s.index(after: dot)...])
    }

    static func tone(_ level: String) -> Tone {
        switch level {
        case "overdue", "device": return .urgent
        case "due", "appliance": return .due
        default: return .later
        }
    }

    static func buttons(_ item: TodayItem) -> [Button] {
        switch item.action {
        case "open": return [.open]
        case "dismiss": return [.dismiss]
        default: return item.source == "upkeep" ? [.done, .snooze] : [.done]
        }
    }

    /// "2 things to do · 1 event", "All done for today", or "" when there's nothing at all.
    static func summary(open: Int, doneHere: Int, events: Int) -> String {
        var parts: [String] = []
        if open > 0 { parts.append("\(open) \(open == 1 ? "thing" : "things") to do") } else if doneHere > 0 { parts.append("All done for today") }
        if events > 0 { parts.append("\(events) \(events == 1 ? "event" : "events")") }
        return parts.joined(separator: " · ")
    }

    static func card(_ b: TodayBriefing, done: [String: Done], now: Double, rows: Int = TodayView.rows) -> Card {
        let recent = done.filter { now - $0.value.at < keepDoneSeconds }
        let open = b.todo.filter { recent[$0.id] == nil }
        let soon = b.soon.filter { recent[$0.id] == nil }
        let doneRows = recent.values.sorted { $0.at < $1.at }.map { d in
            Row(id: d.item.id, title: d.item.title, status: "Done just now", tone: .done,
                buttons: d.upkeepId != nil ? [.undo] : [], entity: d.item.entity, item: d.item)
        }
        func row(_ i: TodayItem) -> Row {
            Row(id: i.id, title: i.title, status: [i.status, i.owner].filter { !$0.isEmpty }.joined(separator: " · "),
                tone: tone(i.level), buttons: buttons(i), entity: i.entity, item: i)
        }
        let openShown = Array(open.prefix(max(1, rows - doneRows.count)))
        let soonShown = Array(soon.prefix(max(0, rows - openShown.count - doneRows.count)))
        return Card(
            summary: summary(open: open.count, doneHere: doneRows.count, events: b.events.count),
            rows: openShown.map(row) + doneRows + soonShown.map(row),
            more: open.count - openShown.count + soon.count - soonShown.count,
            events: Array(b.events.prefix(maxEvents)),
            headsUp: Array(b.headsUp.prefix(maxHeadsUp)),
            empty: b.isEmpty && doneRows.isEmpty
        )
    }
}

/// Which dashboard pages become the phone's bottom tabs (mirrors android core/model/PhoneTabs.kt):
/// the first three pages, then People and Alerts. A Today page isn't a tab - its content is the
/// Today card at the top of the first tab; pages beyond the third (Climate) open from their
/// thermostat tile or a link at the bottom of the first tab.
enum PhoneTabs {
    static let pageTabs = 3
    static let todayTypes: Set<String> = ["greeting", "todo", "today"]
    private static let todayMarkers: Set<String> = ["greeting", "todo"]
    private static let infoTypes: Set<String> = ["calendar", "weather"]

    struct Plan: Equatable {
        var tabs: [DashboardPage]
        var today: DashboardPage?
        var more: [DashboardPage]
        /// From a phone layout (PhoneGrid): tiles sit at their own x/y/w/h instead of flowing.
        var explicit: Bool = false
        /// Phone layouts: the layout asks for the Today card on the first tab.
        var todayCard: Bool = false
    }

    /// Whether the first tab shows the Today card above `page`'s tiles.
    static func showsTodayCard(_ plan: Plan, page: DashboardPage) -> Bool {
        plan.explicit ? plan.todayCard : !hasTodayTile(page)
    }

    /// Whether that card stays up on a day with nothing in it (it was asked for).
    static func todayWhenEmpty(_ plan: Plan) -> Bool { plan.explicit || plan.today != nil }

    static func isTodayPage(_ p: DashboardPage) -> Bool {
        let tiles = p.tiles ?? []
        return !tiles.isEmpty && tiles.contains { todayMarkers.contains($0.type) }
            && tiles.allSatisfy { todayTypes.contains($0.type) || infoTypes.contains($0.type) }
    }

    static func plan(_ pages: [DashboardPage]) -> Plan {
        let today = pages.first(where: isTodayPage)
        let rest = pages.filter { $0.id != today?.id }
        return Plan(tabs: Array(rest.prefix(pageTabs)), today: today, more: Array(rest.dropFirst(pageTabs)))
    }

    /// The page a compact dial tile opens: a page without a tab holding a dial for the same device.
    static func pageForDial(_ plan: Plan, entityId: String?) -> DashboardPage? {
        guard let entityId else { return nil }
        return plan.more.first { p in (p.tiles ?? []).contains { $0.type == "dial" && $0.string("entity") == entityId } }
    }

    /// True when `page` already shows today's briefing through a tile of its own.
    static func hasTodayTile(_ page: DashboardPage) -> Bool {
        (page.tiles ?? []).contains { $0.type == "today" || $0.type == "todo" }
    }
}
