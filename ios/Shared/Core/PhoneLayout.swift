import Foundation

/// How a dashboard page (laid out for the wall's 12 x 8 grid) flows onto a phone: one column of
/// rows, 4 columns wide. Tiles keep dashboard (reading) order; wide tiles span 4 columns, small
/// ones 2; a "devices" tile in the big-tiles look becomes one card per device so each is an easy
/// target. Nothing here knows about kinds of device - only tile types and their settings.
enum PhoneLayout {
    static let columns = 4

    enum CellContent: Hashable {
        case tile(Tile)
        /// One device card of a "devices" tile (style "tiles").
        case device(tile: Tile, entityId: String)
        /// A small heading: a devices tile's title or a room.
        case heading(String, scale: Double)
    }

    struct Cell: Hashable, Identifiable {
        var id: String
        var span: Int
        var content: CellContent
    }

    struct Row: Hashable, Identifiable {
        var id: String
        var cells: [Cell]
        var used: Int { cells.reduce(0) { $0 + $1.span } }
    }

    /// Tile types that are lists or wide by nature: always the full phone width.
    static let fullWidthTypes: Set<String> = ["alerts", "calendar", "attention", "mode", "values", "camera", "today", "todo"]

    /// Wide on the wall (5 or more of 12 columns) = the whole phone width.
    static func isWide(_ t: Tile) -> Bool { t.w >= 5 }

    static func span(of t: Tile) -> Int {
        if fullWidthTypes.contains(t.type) || isWide(t) { return 4 }
        if t.type == "devices" && t.string("style") == "list" { return 4 }
        return 2
    }

    /// Reading order on the wall: top to bottom, then left to right.
    static func ordered(_ tiles: [Tile]) -> [Tile] {
        tiles.enumerated().sorted { a, b in
            if a.element.y != b.element.y { return a.element.y < b.element.y }
            if a.element.x != b.element.x { return a.element.x < b.element.x }
            return a.offset < b.offset
        }.map { $0.element }
    }

    /// The cells of a page. `areaOf` gives a device's room (for groupBy "area") and `exists`
    /// filters out devices the hub no longer has.
    static func cells(for page: DashboardPage, areaOf: (String) -> String? = { _ in nil },
                      exists: (String) -> Bool = { _ in true }) -> [Cell] {
        var out: [Cell] = []
        for t in ordered(page.tiles ?? []) {
            // The phone's page header already greets (with today's summary under it).
            if t.type == "greeting" { continue }
            let scale = t.textScale
            if t.type == "devices" && t.string("style") != "list" {
                let title = (t.string("title") ?? "").trimmingCharacters(in: .whitespaces)
                // A title that just repeats the page's name (a "Lights" tile on the Lights page) is dropped.
                if !title.isEmpty && title.lowercased() != page.name.lowercased() {
                    out.append(Cell(id: t.id + ":title", span: 4, content: .heading(title, scale: scale)))
                }
                var ids = t.strings("entities").filter(exists)
                let cardSpan = isWide(t) ? 4 : 2
                if t.string("groupBy") == "area" {
                    ids = ids.enumerated().sorted { a, b in
                        let x = areaOf(a.element) ?? "~", y = areaOf(b.element) ?? "~"
                        return x == y ? a.offset < b.offset : x.localizedCompare(y) == .orderedAscending
                    }.map { $0.element }
                    var lastArea: String??
                    let anyArea = ids.contains { areaOf($0) != nil }
                    for id in ids {
                        let area = areaOf(id)
                        if anyArea && (lastArea == nil || lastArea! != area) {
                            out.append(Cell(id: t.id + ":area:" + (area ?? "-"), span: 4,
                                            content: .heading(area ?? "No room", scale: scale)))
                            lastArea = .some(area)
                        }
                        out.append(Cell(id: t.id + ":" + id, span: cardSpan, content: .device(tile: t, entityId: id)))
                    }
                } else {
                    for id in ids {
                        out.append(Cell(id: t.id + ":" + id, span: cardSpan, content: .device(tile: t, entityId: id)))
                    }
                }
                continue
            }
            out.append(Cell(id: t.id, span: span(of: t), content: .tile(t)))
        }
        return out
    }

    /// Packs cells into rows of `columns`. A half-width cell fills the gap left beside an earlier
    /// half-width cell when a full-width one came between (like CSS grid's dense flow), so the
    /// grid has no holes; headings always start a fresh row and close any open gap.
    static func rows(_ cells: [Cell]) -> [Row] {
        var rows: [Row] = []
        var openRow: Int?  // index of a row with a free half
        for c in cells {
            let isHeading: Bool
            if case .heading = c.content { isHeading = true } else { isHeading = false }
            if isHeading { openRow = nil }
            if c.span < columns, let i = openRow, rows[i].used + c.span <= columns {
                rows[i].cells.append(c)
                if rows[i].used >= columns { openRow = nil }
                continue
            }
            rows.append(Row(id: c.id, cells: [c]))
            if c.span < columns { openRow = rows.count - 1 }
        }
        return rows
    }
}
