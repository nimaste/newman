import Foundation

/// GET /api/phone-layout: the signed-in person's phone layout ("own") or the house default.
struct PhoneLayoutReply: Decodable, Hashable {
    /// "default" | "own"
    var source: String
    var layout: PhoneGridLayout

    enum CodingKeys: String, CodingKey { case source, layout }

    init(source: String = "default", layout: PhoneGridLayout = PhoneGridLayout()) {
        self.source = source
        self.layout = layout
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        source = (try? c.decodeIfPresent(String.self, forKey: .source)) ?? "default"
        layout = try c.decodeIfPresent(PhoneGridLayout.self, forKey: .layout) ?? PhoneGridLayout()
    }
}

/// A layout made for the phone: pages of tiles placed on a 4-column grid of square cells. Pages
/// marked `tab` are the bottom tabs (at most three); the others are links at the bottom of the
/// first tab (and open from their thermostat tile).
struct PhoneGridLayout: Decodable, Hashable {
    /// Show the Today card at the top of the first tab.
    var today: Bool
    var pages: [DashboardPage]

    enum CodingKeys: String, CodingKey { case today, pages }

    init(today: Bool = false, pages: [DashboardPage] = []) {
        self.today = today
        self.pages = pages
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        today = (try? c.decodeIfPresent(Bool.self, forKey: .today)) ?? false
        pages = try c.decodeIfPresent([DashboardPage].self, forKey: .pages) ?? []
    }
}

/// The phone layout from the hub, when it has one (older hubs answer 404 and the phone flows the
/// wall dashboard instead - PhoneLayout, PhoneTabs). Mirrors android core/model/PhoneGrid.kt.
/// Tiles sit exactly at x/y/w/h on 4 columns of square cells; nothing reflows and empty cells
/// stay empty.
enum PhoneGrid {
    static let columns = 4
    static let maxRows = 8

    static func parse(_ data: Data) throws -> PhoneLayoutReply {
        try JSONDecoder().decode(PhoneLayoutReply.self, from: data)
    }

    // MARK: which layout to use

    /// What one GET /api/phone-layout came back with.
    enum Fetch: Equatable {
        case got(PhoneLayoutReply)
        /// 404: a hub without phone layouts - flow the dashboard as before.
        case unsupported
        /// Offline, a 5xx, an unreadable reply: keep whatever the phone showed.
        case failed
    }

    static func outcome(status: Int, body: Data?) -> Fetch {
        if status == 404 { return .unsupported }
        guard (200..<300).contains(status), let body, let reply = try? parse(body) else { return .failed }
        return .got(reply)
    }

    /// The layout to show after `fetch`; nil = the automatic flow of the dashboard.
    static func next(_ current: PhoneGridLayout?, _ fetch: Fetch) -> PhoneGridLayout? {
        switch fetch {
        case .got(let reply): return reply.layout
        case .unsupported: return nil
        case .failed: return current
        }
    }

    // MARK: tabs

    /// Pages marked as tabs, in order, at most three (then People and Alerts); every other page is
    /// a link at the bottom of the first tab. A layout with pages but no tab shows its first page.
    static func plan(_ layout: PhoneGridLayout) -> PhoneTabs.Plan {
        let pages = layout.pages.map { p -> DashboardPage in
            var q = p
            q.tiles = placed(p.tiles ?? [])
            return q
        }
        var tabs = Array(pages.filter { $0.tab == true }.prefix(PhoneTabs.pageTabs))
        if tabs.isEmpty { tabs = Array(pages.prefix(1)) }
        let tabIds = Set(tabs.map { $0.id })
        let more = pages.filter { !tabIds.contains($0.id) }
        return PhoneTabs.Plan(tabs: tabs, today: nil, more: more, explicit: true, todayCard: layout.today)
    }

    // MARK: geometry

    /// A tile kept inside the 4 columns: x 0-3, w 1-4 (not past the edge), y >= 0, h 1-8.
    static func clamp(_ t: Tile) -> Tile {
        var p = t
        p.x = Swift.min(Swift.max(t.x, 0), columns - 1)
        p.y = Swift.max(t.y, 0)
        p.w = Swift.min(Swift.max(t.w, 1), columns - p.x)
        p.h = Swift.min(Swift.max(t.h, 1), maxRows)
        return p
    }

    /// The page's tiles, clamped, top to bottom then left to right.
    static func placed(_ tiles: [Tile]) -> [Tile] {
        tiles.map(clamp).enumerated().sorted { a, b in
            if a.element.y != b.element.y { return a.element.y < b.element.y }
            if a.element.x != b.element.x { return a.element.x < b.element.x }
            return a.offset < b.offset
        }.map { $0.element }
    }

    struct Rect: Equatable {
        var x: Double
        var y: Double
        var width: Double
        var height: Double
    }

    /// One square cell's side for a grid `width` wide with `gap` between cells.
    static func cellSize(width: Double, gap: Double) -> Double {
        Swift.max(0, (width - gap * Double(columns - 1)) / Double(columns))
    }

    /// Where `t` sits in a grid `width` wide (same units as `gap`).
    static func rect(_ t: Tile, width: Double, gap: Double) -> Rect {
        let c = cellSize(width: width, gap: gap)
        let p = clamp(t)
        return Rect(x: Double(p.x) * (c + gap), y: Double(p.y) * (c + gap),
                    width: Double(p.w) * c + Double(p.w - 1) * gap, height: Double(p.h) * c + Double(p.h - 1) * gap)
    }

    /// Height of a page's grid: down to the bottom of its lowest tile (0 when empty).
    static func height(_ tiles: [Tile], width: Double, gap: Double) -> Double {
        guard let rows = tiles.map({ t -> Int in let p = clamp(t); return p.y + p.h }).max() else { return 0 }
        let c = cellSize(width: width, gap: gap)
        return Double(rows) * c + Double(rows - 1) * gap
    }

    /// Narrower than the phone (or a dial shorter than 4 rows): draw the tile's small version
    /// (the compact thermostat that opens its page, the short weather).
    static func isCompact(_ t: Tile) -> Bool {
        let p = clamp(t)
        return p.w < columns || (t.type == "dial" && p.h < 4)
    }

    // MARK: a "devices" tile inside its own rectangle

    /// Sizes (points) the phone uses for a devices tile's contents, shared with the UI.
    static let devicePadding = 10.0
    static let deviceTitle = 28.0
    static let deviceRow = 56.0
    static let deviceGap = 8.0
    /// From this width a devices tile shows two columns of buttons; narrower is a list.
    static let twoColumnsFrom = 240.0

    /// `shown` devices in `columns`; when `more` > 0 the last slot is a "+N more" button.
    struct DeviceFit: Equatable {
        var columns: Int
        var rows: Int
        var shown: Int
        var more: Int
    }

    static func fitDevices(count: Int, width: Double, height: Double, titled: Bool, list: Bool = false) -> DeviceFit {
        let cols = !list && width >= twoColumnsFrom ? 2 : 1
        let avail = height - 2 * devicePadding - (titled ? deviceTitle : 0)
        let rows = Swift.max(1, Int(((avail + deviceGap) / (deviceRow + deviceGap)).rounded(.down)))
        let capacity = rows * cols
        if count <= capacity { return DeviceFit(columns: cols, rows: rows, shown: count, more: 0) }
        return DeviceFit(columns: cols, rows: rows, shown: capacity - 1, more: count - (capacity - 1))
    }
}
