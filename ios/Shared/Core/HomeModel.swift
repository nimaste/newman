import Foundation

// The phone's Home tab (GET /api/home) and the screens built from devices: Lights by room (a light or
// switch group shows as one row), the Climate room bars and Edit Home. The hub words every card, so the
// iPhone and Android apps say the same thing; Android's HomeModel.kt follows the same rules. Checked on
// Linux (Tests/LinuxRunner/HomeTests.swift).

// MARK: - GET /api/home

struct HomeData: Decodable, Hashable {
    /// A category card ("Climate", "21.8°", "Heat · set to 22°"); `camera` is the cameras card's little picture.
    struct Card: Decodable, Hashable, Identifiable {
        var id: String
        var on: Bool
        var title: String
        var value: String
        var detail: String
        var tone: String
        var camera: String?

        enum CodingKeys: String, CodingKey { case id, on, title, value, detail, tone, camera }

        init(id: String, on: Bool = true, title: String = "", value: String = "", detail: String = "", tone: String = "normal",
             camera: String? = nil) {
            self.id = id
            self.on = on
            self.title = title
            self.value = value
            self.detail = detail
            self.tone = tone
            self.camera = camera
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            id = try c.decode(String.self, forKey: .id)
            on = (try? c.decodeIfPresent(Bool.self, forKey: .on)) ?? true
            title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? DeviceLogic.humanize(id)
            value = (try? c.decodeIfPresent(String.self, forKey: .value)) ?? ""
            detail = (try? c.decodeIfPresent(String.self, forKey: .detail)) ?? ""
            tone = (try? c.decodeIfPresent(String.self, forKey: .tone)) ?? "normal"
            camera = try? c.decodeIfPresent(String.self, forKey: .camera)
        }
    }

    /// What a danger's button does ("Close" the garage door).
    struct Action: Decodable, Hashable {
        var command: String
        var label: String
    }

    struct Danger: Decodable, Hashable, Identifiable {
        var id: String
        var title: String
        var detail: String
        var entity: String?
        var action: Action?

        enum CodingKeys: String, CodingKey { case id, title, detail, entity, action }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? ""
            id = (try? c.decodeIfPresent(String.self, forKey: .id)) ?? title
            detail = (try? c.decodeIfPresent(String.self, forKey: .detail)) ?? ""
            entity = try? c.decodeIfPresent(String.self, forKey: .entity)
            action = try? c.decodeIfPresent(Action.self, forKey: .action)
        }
    }

    /// A device problem shown in amber ("Motion Detector · Battery low"), mutable from Home.
    struct Warning: Decodable, Hashable, Identifiable {
        var entity: String
        var issue: String
        var text: String
        var id: String { entity + "/" + issue }
    }

    struct Status: Decodable, Hashable {
        var level: String
        var summary: String
        var danger: [Danger]
        var warnings: [Warning]

        enum CodingKeys: String, CodingKey { case level, summary, danger, warnings }

        init(level: String = "ok", summary: String = "", danger: [Danger] = [], warnings: [Warning] = []) {
            self.level = level
            self.summary = summary
            self.danger = danger
            self.warnings = warnings
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            level = (try? c.decodeIfPresent(String.self, forKey: .level)) ?? "ok"
            summary = (try? c.decodeIfPresent(String.self, forKey: .summary)) ?? ""
            danger = (try? c.decodeIfPresent([Danger].self, forKey: .danger)) ?? []
            warnings = (try? c.decodeIfPresent([Warning].self, forKey: .warnings)) ?? []
        }
    }

    /// The Cameras tab's big picture: the latest detection, or the featured camera.
    struct TopCamera: Decodable, Hashable {
        var camera: String
        var label: String?
        var zone: String?
        var at: Double?
        var snapshot: String?
        var clip: String?
    }

    struct Cameras: Decodable, Hashable {
        var mode: String
        var featured: String?
        var top: TopCamera?

        enum CodingKeys: String, CodingKey { case mode, featured, top }

        init(mode: String = "latest", featured: String? = nil, top: TopCamera? = nil) {
            self.mode = mode
            self.featured = featured
            self.top = top
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            mode = (try? c.decodeIfPresent(String.self, forKey: .mode)) ?? "latest"
            featured = try? c.decodeIfPresent(String.self, forKey: .featured)
            top = try? c.decodeIfPresent(TopCamera.self, forKey: .top)
        }

        var isFeatured: Bool { mode == "featured" }
        /// "Latest activity" or "Featured camera" - the label over the big picture, exactly as chosen.
        var label: String { isFeatured ? "Featured camera" : "Latest activity" }
        /// The camera in the big picture: the featured one, else the latest detection's.
        var bigCamera: String? { isFeatured ? featured : top?.camera }
    }

    struct Room: Decodable, Hashable, Identifiable {
        var entity: String
        var name: String
        var value: Double?
        var id: String { entity }
    }

    /// A colour band: "below" (cool), "above" (warm), or neither (the comfortable middle).
    struct Band: Decodable, Hashable {
        var name: String
        var below: Double?
        var above: Double?
        var color: String
    }

    struct Climate: Decodable, Hashable {
        var thermostat: String?
        var rooms: [Room]
        var range: [Double]
        var bands: [Band]

        enum CodingKeys: String, CodingKey { case thermostat, rooms, range, bands }

        init(thermostat: String? = nil, rooms: [Room] = [], range: [Double] = HomeLogic.defaultRange, bands: [Band] = HomeLogic.defaultBands) {
            self.thermostat = thermostat
            self.rooms = rooms
            self.range = range
            self.bands = bands
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            thermostat = try? c.decodeIfPresent(String.self, forKey: .thermostat)
            rooms = (try? c.decodeIfPresent([Room].self, forKey: .rooms)) ?? []
            let r = (try? c.decodeIfPresent([Double].self, forKey: .range)) ?? []
            range = r.count == 2 && r[0] < r[1] ? r : HomeLogic.defaultRange
            let b = (try? c.decodeIfPresent([Band].self, forKey: .bands)) ?? []
            bands = b.isEmpty ? HomeLogic.defaultBands : b
        }
    }

    var cards: [Card]
    var favourites: [String]
    var status: Status
    var cameras: Cameras
    var climate: Climate
    var canAutomate: Bool

    enum CodingKeys: String, CodingKey { case cards, favourites, status, cameras, climate, canAutomate }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        cards = (try? c.decodeIfPresent([Card].self, forKey: .cards)) ?? []
        favourites = (try? c.decodeIfPresent([String].self, forKey: .favourites)) ?? []
        status = (try? c.decodeIfPresent(Status.self, forKey: .status)) ?? Status()
        cameras = (try? c.decodeIfPresent(Cameras.self, forKey: .cameras)) ?? Cameras()
        climate = (try? c.decodeIfPresent(Climate.self, forKey: .climate)) ?? Climate()
        canAutomate = (try? c.decodeIfPresent(Bool.self, forKey: .canAutomate)) ?? false
    }

    /// The cards Home shows, in order.
    var shownCards: [Card] { cards.filter { $0.on } }
}

// MARK: - Climate bars, Edit Home

enum HomeLogic {
    static let defaultRange: [Double] = [18, 26]
    static let defaultBands: [HomeData.Band] = [
        HomeData.Band(name: "Cool", below: 21.5, above: nil, color: "#9db8ff"),
        HomeData.Band(name: "Comfortable", below: nil, above: nil, color: "#f5b14c"),
        HomeData.Band(name: "Warm", below: nil, above: 22.0, color: "#ff9a4d"),
    ]
    /// Favourites Home has room for.
    static let maxFavourites = 4

    /// How full a room's bar is: its temperature on the house range (an empty bar is the low end).
    static func barFraction(_ value: Double, range: [Double]) -> Double {
        let lo = range.count == 2 ? range[0] : defaultRange[0], hi = range.count == 2 ? range[1] : defaultRange[1]
        guard hi > lo else { return 0 }
        return Swift.min(1, Swift.max(0, (value - lo) / (hi - lo)))
    }

    /// The band a temperature falls in: below a "below" band's edge, above an "above" band's edge,
    /// else the middle band.
    static func band(_ value: Double, bands: [HomeData.Band]) -> HomeData.Band? {
        let list = bands.isEmpty ? defaultBands : bands
        if let cool = list.first(where: { $0.below.map { value < $0 } ?? false }) { return cool }
        if let warm = list.first(where: { $0.above.map { value > $0 } ?? false }) { return warm }
        return list.first { $0.below == nil && $0.above == nil }
    }

    /// "#9db8ff" → 0x9DB8FF; nil when it isn't a colour.
    static func hex(_ s: String?) -> UInt32? {
        guard var t = s?.trimmingCharacters(in: .whitespaces) else { return nil }
        if t.hasPrefix("#") { t.removeFirst() }
        guard t.count == 6, let v = UInt32(t, radix: 16) else { return nil }
        return v
    }

    static func bandColor(_ value: Double, bands: [HomeData.Band]) -> UInt32 {
        hex(band(value, bands: bands)?.color) ?? 0xC9D4F5
    }

    /// PUT /api/home/me
    static func editHomeBody(cards: [HomeData.Card], favourites: [String]) -> [String: JSONValue] {
        ["cards": .array(cards.map { JSONValue.object(["id": .string($0.id), "on": .bool($0.on)]) }),
         "favourites": .array(favourites.prefix(maxFavourites).map { JSONValue.string($0) })]
    }

    /// PUT /api/home/climate
    static func climateBody(_ rooms: [HomeData.Room]) -> [String: JSONValue] {
        ["rooms": .array(rooms.map { JSONValue.object(["entity": .string($0.entity), "name": .string($0.name)]) })]
    }

    /// Every device reporting a temperature (any brand), for Rooms on Climate: its reading's value.
    static func temperature(_ e: Entity) -> Double? {
        let p = (e.properties ?? []).first { ($0.type ?? "number") == "number" && ($0.unit ?? "").hasPrefix("°") }
        return p?.value?.doubleValue
    }

    static func temperatureSensors(_ entities: [Entity]) -> [Entity] {
        entities.filter { temperature($0) != nil }.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }
}

// MARK: - Lights by room

enum LightRooms {
    /// One row on the Lights screen: a light or switch, or a group (its members tucked behind it).
    struct Row: Hashable, Identifiable {
        var id: String
        var members: [String]
        var isGroup: Bool { !members.isEmpty }
    }

    struct Room: Hashable, Identifiable {
        var name: String
        var rows: [Row]
        var id: String { name }
    }

    static let otherRoom = "Other"

    static func members(_ e: Entity) -> [String] { (e.attr("members")?.arrayValue ?? []).compactMap { $0.stringValue } }

    /// On the Lights screen: lighting devices (the kind's category) that switch on and off.
    static func isLight(_ e: Entity, _ kind: Kind?) -> Bool {
        DeviceDirectory.category(e, kind) == "lighting" && DeviceDirectory.switchPair(kind) != nil
    }

    /// The room a device is in: the one set in Newman, else its integration's area.
    static func room(_ e: Entity) -> String? {
        if let r = e.room?.trimmingCharacters(in: .whitespaces), !r.isEmpty { return r }
        return e.area
    }

    /// Rooms by name ("Other" last); in each, groups and lights by name. A light that's in a group shows
    /// only inside it.
    static func rooms(_ entities: [Entity], kinds: [String: Kind]) -> [Room] {
        let lights = entities.filter { isLight($0, kinds[$0.kind]) }
        let ids = Set(lights.map { $0.id })
        var inGroup = Set<String>()
        for g in lights { for m in members(g) { inGroup.insert(m) } }
        var byRoom: [String: [Row]] = [:]
        for e in lights {
            if inGroup.contains(e.id) { continue }
            if let g = e.group, ids.contains(g), g != e.id { continue }
            let ms = members(e)
            byRoom[room(e) ?? otherRoom, default: []].append(Row(id: e.id, members: ms))
        }
        let names: [String: String] = Dictionary(lights.map { ($0.id, $0.name) }, uniquingKeysWith: { a, _ in a })
        let order = byRoom.keys.sorted { a, b in
            if a == otherRoom { return false }
            if b == otherRoom { return true }
            return a.localizedCaseInsensitiveCompare(b) == .orderedAscending
        }
        return order.map { r in
            let rows = (byRoom[r] ?? []).sorted { a, b in
                if a.isGroup != b.isGroup { return a.isGroup }
                return (names[a.id] ?? a.id).localizedCaseInsensitiveCompare(names[b.id] ?? b.id) == .orderedAscending
            }
            return Room(name: r, rows: rows)
        }
    }

    static func isOn(_ id: String, entities: [String: Entity], kinds: [String: Kind]) -> Bool {
        guard let e = entities[id] else { return false }
        return DeviceDirectory.isOn(e, kinds[e.kind])
    }

    /// "Under cabinet on" / "2 on" / "All off" under a room's name.
    static func roomLine(_ room: Room, entities: [String: Entity], kinds: [String: Kind]) -> String {
        let on = room.rows.filter { isOn($0.id, entities: entities, kinds: kinds) }
        if on.isEmpty { return "All off" }
        if on.count == room.rows.count && room.rows.count > 1 { return "All on" }
        return on.count == 1 ? (entities[on[0].id]?.name ?? "1") + " on" : "\(on.count) on"
    }

    /// Lights on in the whole house (a group counts once).
    static func onCount(_ rooms: [Room], entities: [String: Entity], kinds: [String: Kind]) -> Int {
        rooms.reduce(0) { n, r in n + r.rows.filter { isOn($0.id, entities: entities, kinds: kinds) }.count }
    }

    /// A light's brightness (0–100) when it reports one.
    static func brightness(_ e: Entity) -> Double? { e.attr("brightness")?.doubleValue }
}
