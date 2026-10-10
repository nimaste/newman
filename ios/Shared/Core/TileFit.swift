import Foundation

/// Fitting rows into a tile's cell rectangle on a phone page (web/src/wall/PhoneTiles.jsx fitList,
/// the layout editor's preview): the rows that fit, the rest counted for a "+N more". Sizes are
/// points; an infinite height (a flowed page, no rectangle) fits everything. Mirrors android
/// core/model/TileFit.kt.
enum TileFit {
    /// One line of small text.
    static let line = 18.0

    /// How many rows of `row` with `gap` between them fit in `avail`.
    static func rowsIn(_ avail: Double, row: Double, gap: Double = 0) -> Int {
        if avail == .infinity { return Int.max }
        let n = ((avail + gap) / (row + gap)).rounded(.down)
        return n > 0 ? Int(n) : 0
    }

    /// `shown` rows, `more` left out, `left` = the height still free under the rows.
    struct Fit: Equatable {
        var shown: Int
        var more: Int
        var left: Double
    }

    /// Rows that fit, the rest counted. Room is kept for a `moreH` "+N more" line unless that
    /// would leave no row at all (then the count shows only where `left` has room for it).
    static func list(count: Int, avail: Double, row: Double, gap: Double, moreH: Double) -> Fit {
        let all = rowsIn(avail, row: row, gap: gap)
        if all >= count { return Fit(shown: count, more: 0, left: avail - Double(count) * (row + gap) + gap) }
        let n = max(min(all, 1), moreH > 0 ? rowsIn(avail - moreH, row: row, gap: gap) : all)
        return Fit(shown: n, more: count - n, left: avail - Double(n) * (row + gap) + (n > 0 ? gap : 0))
    }
}
