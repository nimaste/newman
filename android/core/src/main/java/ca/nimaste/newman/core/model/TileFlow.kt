package ca.nimaste.newman.core.model

/**
 * How a dashboard page (laid out for the 12x8 wall grid) flows onto a phone: a 4-column grid in
 * dashboard order. Wide tiles span all 4 columns, small ones span 2. A "devices" tile in the
 * big-tiles look becomes one card per device (the PhoneOverview board's shortcuts), so a phone
 * shows the same devices as the wall without squeezing a grid into a grid.
 *
 * Half-width cells pack densely: when a half cell would leave a gap before a wide one, the next
 * half cell (up to the next section header) moves up to fill it.
 */
object TileFlow {
    const val COLUMNS = 4

    sealed interface Content {
        /** A whole wall tile drawn as one phone tile. [compact] = the half-width version. */
        data class WholeTile(val tile: Tile, val compact: Boolean) : Content
        /** One device of a "devices" tile. [wide] cards carry a brightness bar. */
        data class DeviceCard(val tile: Tile, val entityId: String, val wide: Boolean) : Content
        /** The title of a "devices" tile split into cards. */
        data class Header(val title: String, val tile: Tile) : Content
    }

    data class Cell(val key: String, val span: Int, val content: Content)

    fun cells(page: Page): List<Cell> {
        val out = mutableListOf<Cell>()
        val several = page.tiles.size > 1
        for (t in page.tiles) {
            val c = t.config
            when (t.type) {
                "devices" -> if (c.string("style") == "list") {
                    out += Cell(t.id, COLUMNS, Content.WholeTile(t, compact = false))
                } else {
                    val title = c.string("title")?.trim().orEmpty()
                    if (several && title.isNotEmpty() && !title.equals(page.name, ignoreCase = true)) {
                        out += Cell("${t.id}:head", COLUMNS, Content.Header(title, t))
                    }
                    // A big devices tile (the Lights page) gets full-width cards with a brightness bar.
                    val wide = t.w >= 6
                    c.strings("entities").forEach { id ->
                        out += Cell("${t.id}:$id", if (wide) COLUMNS else 2, Content.DeviceCard(t, id, wide))
                    }
                }
                "dial" -> {
                    val big = t.w >= 5 && t.h >= 5
                    out += Cell(t.id, if (big) COLUMNS else 2, Content.WholeTile(t, compact = !big))
                }
                "weather" -> {
                    val big = t.w >= 6 || c.string("show") == "hours"
                    out += Cell(t.id, if (big) COLUMNS else 2, Content.WholeTile(t, compact = !big))
                }
                // The phone's page header already greets (with today's summary under it).
                "greeting" -> Unit
                // camera, alerts, calendar, values, attention, mode, today/todo (the Today card) -
                // and any type this app doesn't know yet.
                else -> out += Cell(t.id, COLUMNS, Content.WholeTile(t, compact = false))
            }
        }
        return pack(out)
    }

    /** Reorder so half-width cells pair up; never moves a cell across a section header. */
    fun pack(cells: List<Cell>): List<Cell> {
        val queue = cells.toMutableList()
        val out = mutableListOf<Cell>()
        var used = 0 // columns used in the current row
        while (queue.isNotEmpty()) {
            var next = queue.first()
            if (used > 0 && used + next.span > COLUMNS) {
                // The next cell doesn't fit beside the open half: pull a later half cell up to fill it.
                val fill = queue.indexOfFirst { it.span <= COLUMNS - used }
                val header = queue.indexOfFirst { it.content is Content.Header }
                if (fill > 0 && (header < 0 || fill < header)) next = queue[fill]
            }
            queue.remove(next)
            out += next
            used = if (used + next.span > COLUMNS) next.span else used + next.span
            if (used >= COLUMNS) used = 0
        }
        return out
    }

    /** Rows of the flowed grid (for UIs that lay out row by row). */
    fun rows(cells: List<Cell>): List<List<Cell>> {
        val rows = mutableListOf<MutableList<Cell>>()
        var used = COLUMNS
        for (c in cells) {
            if (used + c.span > COLUMNS) {
                rows += mutableListOf<Cell>()
                used = 0
            }
            rows.last() += c
            used += c.span
        }
        return rows
    }
}
