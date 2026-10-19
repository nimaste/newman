import Foundation

// Categories as the hub describes them (GET /api/categories, else /api/meta's): their names, their order
// and which purpose-built phone screen shows each ("screen"). Nothing here knows a category by its id - a
// new one (made in Newman, or a future kind's) just works: a generic card and screen, its own Devices
// section. Checked on Linux (Tests/LinuxRunner/CategoryTests.swift).

/// One category from GET /api/categories.
struct CategoryInfo: Decodable, Hashable, Identifiable {
    var id: String
    var name: String
    var builtIn: Bool
    var screen: String?
    var count: Int

    enum CodingKeys: String, CodingKey { case id, name, builtIn, screen, count }

    init(id: String, name: String, builtIn: Bool = true, screen: String? = nil, count: Int = 0) {
        self.id = id
        self.name = name
        self.builtIn = builtIn
        self.screen = screen
        self.count = count
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? DeviceLogic.humanize(id)
        builtIn = (try? c.decodeIfPresent(Bool.self, forKey: .builtIn)) ?? true
        screen = try? c.decodeIfPresent(String.self, forKey: .screen)
        count = (try? c.decodeIfPresent(Int.self, forKey: .count)) ?? 0
    }

    var meta: MetaCategory { MetaCategory(id: id, name: name, screen: screen) }
}

enum CategoryLogic {
    /// The categories in the hub's order: GET /api/categories when the hub has it (built-in ones, then the
    /// ones made in Newman), else /api/meta's.
    static func list(_ fetched: [CategoryInfo], meta: [MetaCategory]) -> [MetaCategory] {
        fetched.isEmpty ? meta : fetched.map { $0.meta }
    }

    /// Category id → the purpose-built screen that shows it; categories without one aren't in it.
    static func screens(_ categories: [MetaCategory]) -> [String: String] {
        var out: [String: String] = [:]
        for c in categories {
            if let s = c.screen?.trimmingCharacters(in: .whitespaces), !s.isEmpty { out[c.id] = s }
        }
        return out
    }

    /// The purpose-built screen a device belongs on, by its effective category (nil: its category's own screen).
    static func screen(_ e: Entity, _ kind: Kind?, screens: [String: String]) -> String? {
        screens[DeviceDirectory.category(e, kind)]
    }

    static func name(_ id: String, categories: [MetaCategory]) -> String {
        categories.first { $0.id == id }?.name ?? DeviceLogic.humanize(id)
    }

    /// A category's devices: needing attention first, then by name.
    static func devices(_ category: String, entities: [Entity], kinds: [String: Kind]) -> [Entity] {
        entities.filter { DeviceDirectory.category($0, kinds[$0.kind]) == category }.sorted { a, b in
            let pa = DeviceDirectory.needsAttention(a), pb = DeviceDirectory.needsAttention(b)
            if pa != pb { return pa }
            return a.name.localizedCaseInsensitiveCompare(b.name) == .orderedAscending
        }
    }

    /// The icon a category's card shows: the one most of its devices are drawn with (a tie: the first by
    /// name), else "grid".
    static func mostCommonIcon(_ category: String, entities: [Entity], kinds: [String: Kind]) -> String {
        var counts: [String: Int] = [:]
        for e in entities where DeviceDirectory.category(e, kinds[e.kind]) == category {
            counts[DeviceLogic.icon(e, kinds[e.kind], kinds: kinds), default: 0] += 1
        }
        let best = counts.sorted { a, b in a.value != b.value ? a.value > b.value : a.key < b.key }
        return best.first?.key ?? "grid"
    }
}

/// The Security screen's parts, from the devices whose category the hub shows on it (screen "security"),
/// the alarm itself (a mode device) and cameras aside: one section per kind present, titled with the
/// kind's plural - never a list of kinds.
enum SecuritySections {
    struct Section: Hashable, Identifiable {
        var kind: String
        var title: String
        var ids: [String]
        /// Its devices have a button (lock, open…); else they show as tiles.
        var actionable: Bool
        var id: String { kind }
    }

    static func members(_ entities: [Entity], kinds: [String: Kind], screens: [String: String]) -> [Entity] {
        entities.filter { e in
            let k = kinds[e.kind]
            return screens[DeviceDirectory.category(e, k)] == "security" && k?.hasCamera != true && !DeviceLogic.isMode(k)
        }
    }

    /// Sections with buttons first (they're what you act on), then the rest; each by title.
    static func sections(_ entities: [Entity], kinds: [String: Kind], screens: [String: String]) -> [Section] {
        let byKind = Dictionary(grouping: members(entities, kinds: kinds, screens: screens)) { $0.kind }
        let out: [Section] = byKind.map { pair in
            let k = kinds[pair.key]
            let title = k?.plural ?? k?.name ?? DeviceLogic.humanize(pair.key)
            let ids = pair.value.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }.map { $0.id }
            return Section(kind: pair.key, title: title, ids: ids, actionable: !DeviceLogic.plainCommands(k).isEmpty)
        }
        return out.sorted { a, b in
            if a.actionable != b.actionable { return a.actionable }
            return a.title.localizedCaseInsensitiveCompare(b.title) == .orderedAscending
        }
    }
}
