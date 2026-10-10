import Foundation

// The numbers behind the dial, weather and calendar tiles (web/src/wall/Dial.jsx and
// WeatherCalendar.jsx). Devices are picked by what they report (a "number" command, a
// forecast, upcoming events), never by kind.

// MARK: - Dial

struct DialModel {
    let entity: Entity
    let kind: Kind?
    let command: Command
    let modeCommand: Command?

    /// Colours by mode (state id), as on the wall; anything else gets a calm neutral.
    static let modeColors: [String: UInt32] = ["heat": 0xFF8A5B, "cool": 0x5AA9FF, "auto": 0x3CCFB4, "heat_cool": 0x3CCFB4,
                                               "off": 0x6B7599, "eco": 0x3CCFB4, "fan_only": 0xA9B4D6]
    static let neutral: UInt32 = 0xC9D4F5
    static let modeWords: [String: String] = ["heat": "Heat to", "cool": "Cool to", "auto": "Keep at", "heat_cool": "Keep at"]

    init?(entity: Entity?, kind: Kind?, tile: Tile) {
        guard let entity, let kind, let cmd = kind.command(tile.string("command")) else { return nil }
        self.entity = entity
        self.kind = kind
        self.command = cmd
        self.modeCommand = kind.command(tile.string("modeCommand"))
    }

    init(entity: Entity, kind: Kind?, command: Command, modeCommand: Command?) {
        self.entity = entity
        self.kind = kind
        self.command = command
        self.modeCommand = modeCommand
    }

    var min: Double { command.min ?? 0 }
    var max: Double { command.max ?? 100 }
    var step: Double { (command.step ?? 0) > 0 ? command.step! : 1 }
    var unit: String { command.unit ?? "" }

    var current: Double? {
        guard let a = command.attr, !a.isEmpty, let v = entity.attr(a), v.stringValue == nil else { return nil }
        return v.doubleValue
    }

    var modes: [KindState] { modeCommand == nil ? [] : DeviceLogic.stateChoices(kind) }
    var mode: String? { modeCommand == nil ? nil : entity.state }
    var isOff: Bool { mode == "off" }
    var colorHex: UInt32 { mode.map { DialModel.modeColors[$0] ?? DialModel.neutral } ?? DialModel.neutral }

    var word: String {
        if let m = mode, let w = DialModel.modeWords[m] { return w }
        return isOff ? "Off" : command.label
    }

    func clamp(_ v: Double) -> Double {
        let stepped = (v / step).rounded() * step
        return Swift.min(max, Swift.max(min, stepped))
    }

    /// The ring runs from 135° to 405° (clockwise, 0° = 3 o'clock).
    func angle(for v: Double) -> Double {
        135 + (clamp(v) - min) / ((max - min) == 0 ? 1 : (max - min)) * 270
    }

    /// The value for a touch at `degrees` (atan2 of the touch around the centre, 0 = 3 o'clock).
    func value(atDegrees degrees: Double) -> Double {
        var a = degrees
        if a < 0 { a += 360 }
        if a < 135 { a += 360 }
        if a > 405 { a = a > 450 ? 135 : 405 }
        return clamp(min + ((a - 135) / 270) * (max - min))
    }

    var decimals: Int {
        let s = JSONValue.format(step, maxDecimals: 3)
        guard let dot = s.firstIndex(of: ".") else { return 0 }
        return s.distance(from: dot, to: s.endIndex) - 1
    }

    func text(_ v: Double?) -> String {
        guard let v, !isOff else { return "—" }
        return String(format: "%.\(decimals)f", v) + (unit.hasPrefix("°") ? "°" : "")
    }

    private var props: [DeviceProperty] { entity.properties ?? [] }
    var nowProperty: DeviceProperty? { props.first { $0.type == "number" && ($0.unit ?? "") == unit && $0.attr != command.attr } }
    var percentProperty: DeviceProperty? { props.first { $0.type == "number" && $0.unit == "%" && $0.attr != command.attr } }
    var activityProperty: DeviceProperty? { props.first { $0.type == "text" && !($0.options ?? []).isEmpty && !($0.attr ?? "").isEmpty } }

    /// "Cool · idle"
    var status: String {
        var parts: [String] = []
        if let m = mode { parts.append(DeviceLogic.stateLabel(kind, m)) }
        if let a = activityProperty, let v = a.value, !v.isNull, v.displayText != "" {
            parts.append(DeviceLogic.formatProp(a, v).lowercased())
        }
        return parts.joined(separator: " · ")
    }

    /// "Now 22.6 °C · Humidity 50%" (compact: "Now 22.6 °C · 50%").
    func nowLine(compact: Bool) -> String {
        var parts: [String] = []
        if let p = nowProperty { parts.append("Now " + DeviceLogic.formatProp(p, p.value)) }
        if let p = percentProperty {
            parts.append(compact ? DeviceLogic.formatProp(p, p.value) : "\(p.label ?? "") \(DeviceLogic.formatProp(p, p.value))")
        }
        return parts.joined(separator: " · ")
    }

    var nowValue: Double? {
        guard let v = nowProperty?.value, v.stringValue == nil else { return nil }
        return v.doubleValue
    }
}

// MARK: - Dates

enum HubDates {
    private static func formatter(_ f: String) -> DateFormatter {
        let d = DateFormatter()
        d.locale = Locale(identifier: "en_US_POSIX")
        d.timeZone = TimeZone.current
        d.dateFormat = f
        return d
    }

    private static let localFormats = ["yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd'T'HH:mm:ss.SSS", "yyyy-MM-dd'T'HH:mm", "yyyy-MM-dd"].map(formatter)
    private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
    private static let isoFrac: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    /// The hub's dates: local "2026-10-01T07:16" (weather, calendars) or ISO 8601 with a zone.
    static func parse(_ s: String?) -> Date? {
        guard let s, !s.isEmpty else { return nil }
        if let d = iso.date(from: s) ?? isoFrac.date(from: s) { return d }
        for f in localFormats { if let d = f.date(from: s) { return d } }
        return nil
    }

    static func dayKey(_ d: Date, calendar: Calendar = .current) -> String {
        let c = calendar.dateComponents([.year, .month, .day], from: d)
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }

    static func time(_ d: Date) -> String {
        let f = DateFormatter()
        f.timeStyle = .short
        f.dateStyle = .none
        return f.string(from: d)
    }

    /// "Today", "Yesterday", "Tomorrow" or "Fri, Oct 3".
    static func dayLabel(_ d: Date, now: Date = Date(), calendar: Calendar = .current) -> String {
        if calendar.isDate(d, inSameDayAs: now) { return "Today" }
        if let y = calendar.date(byAdding: .day, value: -1, to: now), calendar.isDate(d, inSameDayAs: y) { return "Yesterday" }
        if let t = calendar.date(byAdding: .day, value: 1, to: now), calendar.isDate(d, inSameDayAs: t) { return "Tomorrow" }
        let f = DateFormatter()
        f.setLocalizedDateFormatFromTemplate("EEE MMM d")
        return f.string(from: d)
    }

    /// "7:42 PM" today, else "Yesterday 7:42 PM" / "Fri, Oct 3 7:42 PM".
    static func shortWhen(_ ts: Double, now: Date = Date()) -> String {
        let d = Date(timeIntervalSince1970: ts)
        let day = dayLabel(d, now: now)
        return day == "Today" ? time(d) : "\(day) \(time(d))"
    }
}

// MARK: - Weather

struct WeatherDay: Hashable {
    var date: String
    var day: String
    var condition: String?
    var high: Double?
    var low: Double?
    var precipChance: Double?
    var rain: Double?
    var snow: Double?
}

struct WeatherModel {
    let entity: Entity
    let kind: Kind?

    static func hasForecast(_ e: Entity) -> Bool { e.attr("forecast")?.arrayValue != nil }

    /// The tile's chosen place, else the first device that reports a forecast.
    static func pick(tile: Tile?, entities: [Entity]) -> Entity? {
        if let id = tile?.string("entity"), let e = entities.first(where: { $0.id == id }), hasForecast(e) { return e }
        return entities.first(where: hasForecast)
    }

    private func num(_ k: String) -> Double? {
        guard let v = entity.attr(k), v.stringValue == nil else { return nil }
        return v.doubleValue
    }

    var temperature: Double? { num("temperature") }
    var high: Double? { num("high") }
    var low: Double? { num("low") }
    var isDay: Bool { entity.attr("isDay")?.boolValue != false }
    var condition: String { entity.state ?? "" }
    var label: String {
        let l = DeviceLogic.stateLabel(kind, entity.state)
        return l.isEmpty ? (entity.stateLabel ?? entity.state ?? "") : l
    }

    static func deg(_ v: Double?) -> String {
        guard let v, v.isFinite else { return "–" }
        return "\(Int(v.rounded()))°"
    }

    var forecast: [WeatherDay] {
        (entity.attr("forecast")?.arrayValue ?? []).compactMap { d in
            guard let date = d["date"]?.stringValue else { return nil }
            return WeatherDay(date: date, day: d["day"]?.stringValue ?? "", condition: d["condition"]?.stringValue,
                              high: d["high"]?.doubleValue, low: d["low"]?.doubleValue,
                              precipChance: d["precipChance"]?.doubleValue,
                              rain: d["rain"]?.doubleValue ?? d["precip"]?.doubleValue, snow: d["snow"]?.doubleValue)
        }
    }

    func nextDays(now: Date = Date()) -> [WeatherDay] {
        let today = HubDates.dayKey(now)
        return forecast.filter { $0.date > today }
    }

    var alerts: [[String: JSONValue]] {
        (entity.attr("alerts")?.arrayValue ?? []).compactMap { $0.objectValue }
    }

    /// "H 24° · L 18° · 40% · 0.1 mm"
    func highLowLine(now: Date = Date()) -> String {
        let today = forecast.first { $0.date == HubDates.dayKey(now) }
        let chance = today?.precipChance ?? num("precipChance")
        let rain = today?.rain ?? num("rainToday")
        let snow = today?.snow ?? num("snowToday")
        var wet: [String] = []
        if let c = chance, c > 0 { wet.append("\(Int(c.rounded()))%") }
        if let s = snow, s > 0 { wet.append("\(JSONValue.format(s, maxDecimals: 1)) cm") } else if let r = rain, r > 0 { wet.append("\(JSONValue.format(r, maxDecimals: 1)) mm") }
        return "H \(WeatherModel.deg(high)) · L \(WeatherModel.deg(low))" + (wet.isEmpty ? "" : " · " + wet.joined(separator: " · "))
    }

    /// The next day with rain or snow worth mentioning: "Fri: rain 7.3 mm".
    func nextWetDay(now: Date = Date()) -> String? {
        for d in nextDays(now: now) {
            if let s = d.snow, s >= 1 { return "\(d.day): snow \(JSONValue.format(s, maxDecimals: 1)) cm" }
            if let r = d.rain, r >= 1 { return "\(d.day): rain \(JSONValue.format(r, maxDecimals: 1)) mm" }
        }
        return nil
    }
}

// MARK: - Calendar

struct AgendaItem: Hashable, Identifiable {
    var id: String
    var title: String
    var start: Date
    var end: Date
    var allDay: Bool
    var location: String
    var colourHex: String
    var calendarName: String
    /// The local day it's listed under (an event running since before today shows under today).
    var dayKey: String
    var isNow: Bool
}

enum AgendaModel {
    static func hasAgenda(_ e: Entity) -> Bool { e.attr("upcoming")?.arrayValue != nil }

    static func items(tile: Tile?, entities: [Entity], now: Date = Date(), calendar: Calendar = .current) -> [AgendaItem] {
        let ids = tile?.strings("entities") ?? []
        let cals = entities.filter(hasAgenda).filter { ids.isEmpty || ids.contains($0.id) }
        let days = Int(Swift.max(1, Swift.min(31, tile?.number("days") ?? 31)))
        let todayStart = calendar.startOfDay(for: now)
        let until = calendar.date(byAdding: .day, value: days, to: todayStart) ?? now
        var out: [AgendaItem] = []
        for c in cals {
            for (i, ev) in (c.attr("upcoming")?.arrayValue ?? []).enumerated() {
                guard let s = HubDates.parse(ev["start"]?.stringValue) else { continue }
                let e = HubDates.parse(ev["end"]?.stringValue) ?? s
                if e <= now || s >= until { continue }
                let allDay = ev["allDay"]?.boolValue ?? false
                let shownOn = s < todayStart ? todayStart : s
                out.append(AgendaItem(id: "\(c.id):\(i)", title: ev["title"]?.stringValue ?? "", start: s, end: e, allDay: allDay,
                                      location: ev["location"]?.stringValue ?? "",
                                      colourHex: c.attr("colour")?.stringValue ?? "#3CCFB4", calendarName: c.name,
                                      dayKey: HubDates.dayKey(shownOn, calendar: calendar),
                                      isNow: !allDay && s <= now && now < e))
            }
        }
        out.sort { a, b in
            if a.dayKey != b.dayKey { return a.dayKey < b.dayKey }
            if a.allDay != b.allDay { return a.allDay }
            return a.start < b.start
        }
        return out
    }

    /// "All day", "From 9:00 PM", "9:30 AM – 10:30 AM".
    static func timeText(_ ev: AgendaItem, calendar: Calendar = .current) -> String {
        if ev.allDay { return "All day" }
        if !calendar.isDate(ev.start, inSameDayAs: ev.end) { return "From " + HubDates.time(ev.start) }
        if ev.end.timeIntervalSince(ev.start) < 60 { return HubDates.time(ev.start) }
        return "\(HubDates.time(ev.start)) – \(HubDates.time(ev.end))"
    }
}

// MARK: - Attention and status

enum HouseStatus {
    struct Problem: Hashable, Identifiable {
        var id: String
        var name: String
        var issues: [String]
        var isAlarm: Bool
    }

    /// Urgent kinds away from their secure state first (a leak), then devices with health issues.
    static func attention(_ entities: [Entity], kinds: [String: Kind]) -> [Problem] {
        let alarms = entities.filter { e in
            guard let k = kinds[e.kind] else { return false }
            return (k.urgent ?? false) && k.secureState != nil && DeviceLogic.notSecure(e, k)
        }
        let alarmIds = Set(alarms.map { $0.id })
        let sick = entities.filter { $0.health?.ok == false && !alarmIds.contains($0.id) }
        return alarms.map { e in
            Problem(id: e.id, name: e.name, issues: [DeviceLogic.stateLabel(e, kinds[e.kind])] + e.issues, isAlarm: true)
        } + sick.map { Problem(id: $0.id, name: $0.name, issues: $0.issues, isAlarm: false) }
    }

    /// The things worth a look among devices with a secure state (an unlocked door).
    static func notSecure(_ entities: [Entity], kinds: [String: Kind]) -> [Entity] {
        entities.filter { DeviceLogic.notSecure($0, kinds[$0.kind]) }
    }

    static func hasSecureDevices(_ entities: [Entity], kinds: [String: Kind]) -> Bool {
        entities.contains { kinds[$0.kind]?.secureState != nil }
    }

    /// "All secure" / "Side Door unlocked" / "2 not secure".
    static func securitySummary(_ entities: [Entity], kinds: [String: Kind]) -> String? {
        guard hasSecureDevices(entities, kinds: kinds) else { return nil }
        let open = notSecure(entities, kinds: kinds)
        if open.isEmpty { return "All secure" }
        if open.count == 1 { return "\(open[0].name) \(DeviceLogic.stateLabel(open[0], kinds[open[0].kind]).lowercased())" }
        return "\(open.count) not secure"
    }

    static func modeDevice(_ entities: [Entity], kinds: [String: Kind]) -> Entity? {
        entities.first { DeviceLogic.isMode(kinds[$0.kind]) }
    }

    /// Lights and other things that are "on" (active, no secure state): "1 on".
    static func litCount(_ ids: [String], entities: [String: Entity], kinds: [String: Kind]) -> Int {
        ids.compactMap { entities[$0] }.filter { DeviceLogic.isLit($0, kinds[$0.kind]) }.count
    }
}
