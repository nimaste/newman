import Foundation

// The household on the map: GET /api/people, GET/PUT /api/me/location, POST
// /api/me/location/report, POST /api/people/{id}/refresh, the person's colour and photo, and the
// live {"type":"person"} message. The wording, the sharing switch, the tap-to-refresh states and
// when to report live here (pure Foundation) so both apps say the same and it is tested on Linux.
// Mirrors android core/people/People.kt.

/// One person as GET /api/people and the live "person" message send them.
struct Person: Decodable, Hashable, Identifiable {
    var id: String
    var name: String
    var sharing: Bool
    var home: Bool
    var place: String?
    var distanceM: Double?
    var lat: Double?
    var lon: Double?
    var accuracyM: Double?
    /// Epoch seconds of the last fix.
    var at: Double?
    /// "#RRGGBB" (the hub always sets one; nil on older hubs).
    var color: String?
    /// "/api/people/<id>/photo?v=<hash>", or nil (no photo: the coloured initial).
    var photo: String?

    enum CodingKeys: String, CodingKey { case id, name, sharing, home, place, distanceM, lat, lon, accuracyM, at, color, photo }

    init(id: String, name: String = "", sharing: Bool = false, home: Bool = false, place: String? = nil, distanceM: Double? = nil,
         lat: Double? = nil, lon: Double? = nil, accuracyM: Double? = nil, at: Double? = nil, color: String? = nil, photo: String? = nil) {
        self.id = id
        self.name = name
        self.sharing = sharing
        self.home = home
        self.place = place
        self.distanceM = distanceM
        self.lat = lat
        self.lon = lon
        self.accuracyM = accuracyM
        self.at = at
        self.color = color
        self.photo = photo
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? ""
        sharing = (try? c.decodeIfPresent(Bool.self, forKey: .sharing)) ?? false
        home = (try? c.decodeIfPresent(Bool.self, forKey: .home)) ?? false
        place = try? c.decodeIfPresent(String.self, forKey: .place)
        distanceM = try? c.decodeIfPresent(Double.self, forKey: .distanceM)
        lat = try? c.decodeIfPresent(Double.self, forKey: .lat)
        lon = try? c.decodeIfPresent(Double.self, forKey: .lon)
        accuracyM = try? c.decodeIfPresent(Double.self, forKey: .accuracyM)
        at = try? c.decodeIfPresent(Double.self, forKey: .at)
        color = try? c.decodeIfPresent(String.self, forKey: .color)
        photo = try? c.decodeIfPresent(String.self, forKey: .photo)
    }

    var hasFix: Bool { lat != nil && lon != nil }
}

/// GET/PUT /api/me/location.
struct LocationSettings: Decodable, Hashable {
    /// "off" | "choice" | "required"
    var mode: String
    var enabled: Bool
    var requiredBy: String?
    var everySeconds: Int

    enum CodingKeys: String, CodingKey { case mode, enabled, requiredBy, everySeconds }

    init(mode: String = "off", enabled: Bool = false, requiredBy: String? = nil, everySeconds: Int = 300) {
        self.mode = mode
        self.enabled = enabled
        self.requiredBy = requiredBy
        self.everySeconds = everySeconds
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mode = (try? c.decodeIfPresent(String.self, forKey: .mode)) ?? "off"
        enabled = (try? c.decodeIfPresent(Bool.self, forKey: .enabled)) ?? false
        requiredBy = try? c.decodeIfPresent(String.self, forKey: .requiredBy)
        everySeconds = (try? c.decodeIfPresent(Int.self, forKey: .everySeconds)) ?? 300
    }

    /// True when this phone should be reporting.
    var sharing: Bool { mode == "required" || (mode == "choice" && enabled) }
}

/// POST /api/me/location/report reply ({"live": false, "everySeconds": 300}; `live` is ignored).
struct ReportReply: Decodable, Hashable {
    var everySeconds: Int

    enum CodingKeys: String, CodingKey { case everySeconds }

    init(everySeconds: Int) {
        self.everySeconds = everySeconds
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        everySeconds = (try? c.decodeIfPresent(Int.self, forKey: .everySeconds)) ?? 300
    }
}

/// GET /api/places. Home's centre is null until the hub knows where home is.
struct Place: Decodable, Hashable, Identifiable {
    var id: String
    var name: String?
    var lat: Double?
    var lon: Double?
    var radiusM: Double?
}

/// POST /api/me/location/report body.
enum LocationReportBody {
    /// "background" | "app" | "refresh" (one fix someone asked for)
    static func make(lat: Double, lon: Double, accuracyM: Double?, at: Double, speedMps: Double?, source: String) -> [String: JSONValue] {
        [
            "lat": .number(lat), "lon": .number(lon),
            "accuracyM": accuracyM.map(JSONValue.number) ?? JSONValue.null,
            "at": .number(at),
            "speedMps": speedMps.map(JSONValue.number) ?? JSONValue.null,
            "source": .string(source),
        ]
    }
}

/// How a person reads in the People list and on the map.
enum PeopleText {
    /// "850 m", "3.2 km", "12 km".
    static func distance(_ meters: Double) -> String {
        let m10 = max(1, (meters / 10).rounded()) * 10
        if m10 < 1000 { return "\(Int(m10)) m" }
        let tenths = Int((meters / 100).rounded())
        if tenths < 100 { return tenths % 10 == 0 ? "\(tenths / 10) km" : "\(tenths / 10).\(tenths % 10) km" }
        return "\(Int((meters / 1000).rounded())) km"
    }

    /// "Home", "School", "3.2 km away", or why there's nothing to show.
    static func whereText(_ p: Person) -> String {
        if !p.sharing { return "Not sharing location" }
        if p.home { return "Home" }
        if let place = p.place, !place.trimmingCharacters(in: .whitespaces).isEmpty { return place }
        if let d = p.distanceM { return "\(distance(d)) away" }
        return p.hasFix ? "Away" : "No location yet"
    }

    /// "updated just now", "updated 4 min ago", "updated 2 h ago", "updated yesterday", "updated 3 days ago".
    static func updated(_ at: Double?, now: Double) -> String? {
        guard let at, at > 0 else { return nil }
        let s = max(0, now - at)
        let min = Int(s / 60)
        if s < 60 { return "updated just now" }
        if min < 60 { return "updated \(min) min ago" }
        if min < 24 * 60 { return "updated \(min / 60) h ago" }
        if min < 48 * 60 { return "updated yesterday" }
        return "updated \(min / (24 * 60)) days ago"
    }

    struct Row: Hashable {
        var id: String
        var name: String
        var whereText: String
        var updated: String?
        var home: Bool
        var located: Bool
    }

    static func row(_ p: Person, now: Double) -> Row {
        Row(id: p.id, name: p.name.isEmpty ? "Someone" : p.name, whereText: whereText(p),
            updated: p.sharing ? updated(p.at, now: now) : nil, home: p.sharing && p.home,
            located: p.sharing && p.hasFix)
    }

    /// One line under the map: "Home · updated 4 min ago".
    static func mapLine(_ p: Person, now: Double) -> String {
        [whereText(p), p.sharing ? updated(p.at, now: now) : nil].compactMap { $0 }.joined(separator: " · ")
    }

    static func initial(_ name: String) -> String {
        let t = name.trimmingCharacters(in: .whitespaces)
        return t.isEmpty ? "?" : String(t.prefix(1)).uppercased()
    }
}

/// The "Share my location" switch on the Me screen, from GET /api/me/location.
enum SharingSwitch {
    struct State: Equatable {
        var visible: Bool
        var on: Bool
        var locked: Bool
        /// Under the switch: "Required by Chris" or what sharing means.
        var note: String
    }

    static let hidden = State(visible: false, on: false, locked: false, note: "")

    /// `settings` nil = the hub doesn't offer location sharing (older hub: 404).
    static func state(_ settings: LocationSettings?) -> State {
        guard let s = settings else { return hidden }
        switch s.mode {
        case "choice":
            return State(visible: true, on: s.enabled, locked: false,
                         note: s.enabled ? "Your household can see where this phone is" : "Off - your household can't see where you are")
        case "required":
            let who = (s.requiredBy ?? "").trimmingCharacters(in: .whitespaces)
            return State(visible: true, on: true, locked: true, note: "Required by \(who.isEmpty ? "your household" : who)")
        default:
            return hidden
        }
    }

    /// Words for the explanation before the system asks.
    static func permissionReason(_ settings: LocationSettings?) -> String {
        let base = "Newman sends where this phone is to your hub so your family can see you on the map - nothing goes anywhere else."
        return settings?.mode == "required" ? "Your household requires location sharing. " + base : base
    }

    static let backgroundReason = "To keep sharing when Newman is closed, allow location \"Always\". Newman checks only when you move a good distance (or when someone taps you on their map), so it's easy on the battery."
}

/// Each person's look: their colour ("#RRGGBB", PUT /api/me/profile) and photo (PUT/DELETE
/// /api/me/photo), shown everywhere they appear; no photo = their single initial on their colour.
enum PersonLook {
    /// The Me screen's swatches. The first six are what an older hub (no "color") falls back to,
    /// by position in the household, as before.
    static let presets: [String] = ["#7FB8FF", "#C58BFF", "#3CCFB4", "#F5B942", "#FF8A6B", "#8BD46B",
                                    "#FF7EB6", "#5ED3F3", "#A3A1FF", "#FFD166", "#E0A47A", "#9AA5C4"]
    static let fallbackCount = 6

    /// Photos are made smaller than this (long side, pixels) before they're uploaded.
    static let uploadMaxPixels = 1024.0
    /// The hub refuses bigger uploads.
    static let uploadMaxBytes = 3 * 1024 * 1024

    /// "#7fb8ff" / "7FB8FF" → 0x7FB8FF; nil when it isn't six hex digits.
    static func rgb(_ hex: String?) -> UInt32? {
        guard var s = hex?.trimmingCharacters(in: .whitespaces) else { return nil }
        if s.hasPrefix("#") { s.removeFirst() }
        guard s.count == 6, s.allSatisfy({ $0.isHexDigit }), let v = UInt32(s, radix: 16) else { return nil }
        return v
    }

    /// The person's colour: theirs, or (older hub) a preset by their place in the household.
    static func rgb(_ p: Person, index: Int) -> UInt32 {
        if let v = rgb(p.color) { return v }
        let i = ((index % fallbackCount) + fallbackCount) % fallbackCount
        return rgb(presets[i]) ?? 0x7FB8FF
    }

    /// "#7FB8FF" (upper case), or nil when it isn't a colour.
    static func normalized(_ hex: String?) -> String? {
        guard let v = rgb(hex) else { return nil }
        let digits = String(v, radix: 16, uppercase: true)
        return "#" + String(repeating: "0", count: max(0, 6 - digits.count)) + digits
    }

    /// Whether two colours are the same (the swatch to highlight).
    static func same(_ a: String?, _ b: String?) -> Bool {
        guard let x = normalized(a), let y = normalized(b) else { return false }
        return x == y
    }

    /// The photo's hub path (its ?v= changes with the picture, so it is also the cache key).
    static func photoPath(_ p: Person?) -> String? {
        guard let s = p?.photo?.trimmingCharacters(in: .whitespaces), !s.isEmpty else { return nil }
        return s
    }

    /// PUT /api/me/profile body.
    static func profileBody(color: String) -> [String: JSONValue] {
        ["color": .string(normalized(color) ?? color)]
    }

    /// The size to draw a `width`×`height` picture at so its long side is at most `maxSide`.
    static func fitted(width: Double, height: Double, maxSide: Double = uploadMaxPixels) -> (width: Double, height: Double) {
        let long = max(width, height)
        guard long > maxSide, long > 0 else { return (width, height) }
        let k = maxSide / long
        return ((width * k).rounded(), (height * k).rounded())
    }

    /// The centred square of a `width`×`height` picture (the hub crops too; this keeps uploads small).
    static func squareCrop(width: Double, height: Double) -> (x: Double, y: Double, side: Double) {
        let side = min(width, height)
        return (((width - side) / 2).rounded(.down), ((height - side) / 2).rounded(.down), side)
    }
}

/// Tap-to-refresh: tapping a person (or the refresh button on their map) asks the hub for ONE fresh
/// fix (POST /api/people/<id>/refresh). "Updating…" shows until a "person" update with a newer fix
/// arrives, or 45 s pass ("Couldn't reach their phone right now"). The hub allows one every 2 minutes
/// per person; a 429 says so quietly. An older hub (404) just does nothing.
enum PersonRefresh {
    static let timeoutSeconds = 45.0
    /// How long "Couldn't reach…" / "Updated recently…" stay up.
    static let messageSeconds = 8.0
    /// A 429 without nextAllowedAt: the hub's 2 minutes.
    static let defaultWaitSeconds = 120.0

    enum Phase: Equatable { case idle, updating, failed, tooSoon }

    struct State: Equatable {
        var phase: Phase = .idle
        /// When this phase began.
        var since: Double = 0
        /// The person's fix time when the refresh was asked for (a newer one ends it).
        var baselineAt: Double? = nil
        /// tooSoon: when the hub allows the next one.
        var until: Double = 0

        static let idle = State()
    }

    /// The tap: start a refresh (nothing new while one is already running).
    static func start(_ s: State, person: Person?, now: Double) -> State {
        if s.phase == .updating { return s }
        return State(phase: .updating, since: now, baselineAt: person?.at)
    }

    /// The hub's answer to POST .../refresh (status 0 = no answer at all).
    static func replied(_ s: State, status: Int, nextAllowedAt: Double?, now: Double) -> State {
        guard s.phase == .updating else { return s }
        switch status {
        case 200..<300: return s
        case 429: return State(phase: .tooSoon, since: now, until: nextAllowedAt ?? now + defaultWaitSeconds)
        case 404, 405: return .idle // an older hub without refresh: quietly nothing
        default: return State(phase: .failed, since: now)
        }
    }

    /// A "person" update (or a reload) arrived: a fix newer than the one we had ends "Updating…".
    static func personChanged(_ s: State, _ p: Person) -> State {
        guard s.phase == .updating, let at = p.at else { return s }
        if let b = s.baselineAt, at <= b { return s }
        return .idle
    }

    /// Time passing: the 45 s timeout, and messages going away.
    static func tick(_ s: State, now: Double) -> State {
        switch s.phase {
        case .idle: return s
        case .updating: return now - s.since >= timeoutSeconds ? State(phase: .failed, since: now) : s
        case .failed, .tooSoon: return now - s.since >= messageSeconds ? .idle : s
        }
    }

    /// The words under the person, or nil.
    static func text(_ s: State, now: Double) -> String? {
        switch s.phase {
        case .idle: return nil
        case .updating: return "Updating…"
        case .failed: return "Couldn't reach their phone right now"
        case .tooSoon:
            let n = Int((s.until - now).rounded(.up))
            return n > 0 ? "Updated recently - try again in \(n) s" : "Updated recently"
        }
    }

    private struct Reply: Decodable { var nextAllowedAt: Double? }

    /// nextAllowedAt (epoch s) from the hub's 202 / 429 body.
    static func nextAllowedAt(_ data: Data) -> Double? {
        (try? JSONDecoder().decode(Reply.self, from: data))?.nextAllowedAt
    }
}

/// When to post a fix in the background: when the interval (the reply's everySeconds, 300) is up,
/// or sooner after a real move - never more than once a minute. There is no live mode: a viewer's
/// tap asks for ONE fix (a locate push, `LocatePush`).
enum ReportSchedule {
    static let minBackgroundSeconds = 60
    static let maxBackgroundSeconds = 3600
    /// Moving at least this far is worth a report even before the interval is up.
    static let moveMeters = 75.0
    /// The People list's fallback refresh (reads the hub; asks no phone for anything).
    static let peopleRefreshSeconds = 30

    static func intervalSeconds(everySeconds: Int) -> Int {
        min(maxBackgroundSeconds, max(minBackgroundSeconds, everySeconds))
    }

    struct Last: Equatable {
        var at: Double
        var lat: Double
        var lon: Double
    }

    /// True when a new fix should be posted (see the Android twin for the rules).
    static func shouldSend(last: Last?, now: Double, lat: Double, lon: Double, everySeconds: Int) -> Bool {
        guard let last else { return true }
        let elapsed = now - last.at
        if elapsed < 0 { return true }
        if elapsed >= Double(intervalSeconds(everySeconds: everySeconds) - 5) { return true }
        return elapsed >= Double(minBackgroundSeconds) && distanceMeters(last.lat, last.lon, lat, lon) >= moveMeters
    }

    /// Great-circle distance in meters.
    static func distanceMeters(_ lat1: Double, _ lon1: Double, _ lat2: Double, _ lon2: Double) -> Double {
        let r = 6_371_000.0
        let rad = Double.pi / 180
        let dLat = (lat2 - lat1) * rad
        let dLon = (lon2 - lon1) * rad
        let a = sin(dLat / 2) * sin(dLat / 2) + cos(lat1 * rad) * cos(lat2 * rad) * sin(dLon / 2) * sin(dLon / 2)
        return 2 * r * asin(sqrt(min(1, max(0, a))))
    }
}

/// The hub's one-shot locate (someone tapped this person): a silent push
/// {"aps":{"content-available":1},"newman":{"type":"locate"}} or an APNs location push
/// (LocationPushExtension). Either answers with exactly ONE report (`source: "refresh"`) and stops.
enum LocatePush {
    static func isLocate(_ userInfo: [AnyHashable: Any]) -> Bool {
        let type = (userInfo["newman"] as? [String: Any])?["type"] as? String
        return type == "locate" || type == "refresh"
    }

    /// The report's source.
    static let source = "refresh"
    /// Epoch seconds in the App Group's defaults: when this phone last answered a locate (the app
    /// and the extension may both be woken for the same tap - only the first answers).
    static let answeredKey = "location.locateAnsweredAt"
    /// A locate this soon after the last answer is the same tap (the hub allows one per 2 minutes).
    static let dedupeSeconds = 30.0

    static func shouldAnswer(lastAnsweredAt: Double?, now: Double) -> Bool {
        guard let last = lastAnsweredAt, last > 0, now >= last else { return true } // never, or the clock moved back
        return now - last >= dedupeSeconds
    }

    /// Checks and claims the answer in `defaults` (the App Group's): false = already answered.
    static func claim(_ defaults: UserDefaults?, now: Double) -> Bool {
        let last = defaults?.double(forKey: answeredKey)
        guard shouldAnswer(lastAnsweredAt: last, now: now) else { return false }
        defaults?.set(now, forKey: answeredKey)
        return true
    }
}

/// The People tile on a phone page (web/src/wall/PhoneTiles.jsx PhonePeople): who it shows and
/// how many rows fit its cell; the rest is "+N more", which opens the People tab. Sizes are points
/// inside the card; an infinite height (a flowed page) shows everyone. Mirrors android PeopleTile.
enum PeopleTileLogic {
    /// The "People" title row and the space under it.
    static let head = 22.0
    static let headGap = 8.0
    static let row = 44.0
    static let gap = 6.0
    /// Rows show the initial circle from this width.
    static let avatarFrom = 100.0
    /// "+N more" fits beside the title from this width; narrower it goes under the rows.
    static let noteFrom = 130.0

    /// The tile's config.people (person ids) in the hub's order; none chosen = everyone.
    static func select(_ all: [Person], pick: [String]) -> [Person] {
        pick.isEmpty ? all : all.filter { pick.contains($0.id) }
    }

    enum Look: Equatable { case rows, oneLine }

    struct Layout: Equatable {
        var look: Look
        var head: Bool
        var shown: Int
        var more: Int
        /// "+N more" beside the title.
        var moreInHead: Bool
        /// "+N more" under the rows.
        var moreBelow: Bool
        var avatars: Bool
    }

    static func layout(count: Int, width: Double, height: Double) -> Layout {
        let titled = height >= head + 40 + headGap
        // Too short for rows: the title and who's there on one line.
        if !titled && count > 1 && height >= head + TileFit.line {
            return Layout(look: .oneLine, head: true, shown: count, more: 0, moreInHead: false, moreBelow: false, avatars: false)
        }
        let avail = height - (titled ? head + headGap : 0)
        let fit = TileFit.list(count: count, avail: avail, row: row, gap: gap, moreH: titled ? 0 : TileFit.line)
        let inHead = titled && fit.more > 0 && width >= noteFrom
        let below = fit.more > 0 && !inHead && fit.left >= TileFit.line
        return Layout(look: .rows, head: titled, shown: fit.shown, more: fit.more, moreInHead: inHead, moreBelow: below,
                      avatars: width >= avatarFrom)
    }

    /// "Marlina (home) · Massimo" - the one-line look.
    static func oneLine(_ people: [Person]) -> String {
        people.map { ($0.name.isEmpty ? "Someone" : $0.name) + ($0.sharing && $0.home ? " (home)" : "") }.joined(separator: " · ")
    }
}

enum PeopleList {
    /// Keeps the hub's order; replaces one person (new ones go last).
    static func upsert(_ list: [Person], _ p: Person) -> [Person] {
        if let i = list.firstIndex(where: { $0.id == p.id }) {
            var out = list
            out[i] = p
            return out
        }
        return list + [p]
    }
}
