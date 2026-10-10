package ca.nimaste.newman.core.model

import kotlinx.serialization.Serializable
import kotlin.math.floor

/** GET /api/phone-layout: the signed-in person's phone layout ("own") or the house default. */
@Serializable
data class PhoneLayoutReply(
    /** "default" | "own" */
    val source: String = "default",
    val layout: PhoneGridLayout = PhoneGridLayout(),
)

/**
 * A layout made for the phone: pages of tiles placed on a 4-column grid of square cells. Pages
 * marked [Page.tab] are the bottom tabs (at most [PhoneGrid.PAGE_TABS]); the others are links at
 * the bottom of the first tab (and open from their thermostat tile).
 */
@Serializable
data class PhoneGridLayout(
    /** Show the Today card at the top of the first tab. */
    val today: Boolean = false,
    val pages: List<Page> = emptyList(),
)

/**
 * The phone layout from the hub, when it has one (older hubs answer 404 and the phone flows the
 * wall dashboard instead - TileFlow, PhoneTabs). Tiles sit exactly at x/y/w/h on 4 columns of
 * square cells; nothing reflows and empty cells stay empty.
 */
object PhoneGrid {
    const val COLUMNS = 4
    const val MAX_ROWS = 8
    const val PAGE_TABS = PhoneTabs.PAGE_TABS

    fun parse(json: String): PhoneLayoutReply = NewmanJson.decodeFromString(PhoneLayoutReply.serializer(), json)

    // -- which layout to use -------------------------------------------------------------------------

    /** What one GET /api/phone-layout came back with. */
    sealed interface Fetch {
        data class Got(val reply: PhoneLayoutReply) : Fetch
        /** 404: a hub without phone layouts - flow the dashboard as before. */
        data object Unsupported : Fetch
        /** Offline, a 5xx, an unreadable reply: keep whatever the phone showed. */
        data object Failed : Fetch
    }

    fun outcome(code: Int, body: String?): Fetch = when {
        code == 404 -> Fetch.Unsupported
        code in 200..299 -> body?.let { runCatching { Fetch.Got(parse(it)) }.getOrNull() } ?: Fetch.Failed
        else -> Fetch.Failed
    }

    /** The layout to show after [fetch]; null = the automatic flow of the dashboard. */
    fun next(current: PhoneGridLayout?, fetch: Fetch): PhoneGridLayout? = when (fetch) {
        is Fetch.Got -> fetch.reply.layout
        Fetch.Unsupported -> null
        Fetch.Failed -> current
    }

    // -- tabs ----------------------------------------------------------------------------------------

    /**
     * Pages marked as tabs, in order, at most three (then People and Alerts); every other page is a
     * link at the bottom of the first tab. A layout with pages but no tab shows its first page as one.
     */
    fun plan(layout: PhoneGridLayout): PhoneTabs.Plan {
        val pages = layout.pages.map { it.copy(tiles = placed(it.tiles)) }
        var tabs = pages.filter { it.tab }.take(PAGE_TABS)
        if (tabs.isEmpty()) tabs = pages.take(1)
        val more = pages.filter { p -> tabs.none { it.id == p.id } }
        return PhoneTabs.Plan(tabs, today = null, more = more, explicit = true, todayCard = layout.today)
    }

    // -- geometry ------------------------------------------------------------------------------------

    /** A tile kept inside the 4 columns: x 0-3, w 1-4 (not past the edge), y >= 0, h 1-8. */
    fun clamp(t: Tile): Tile {
        val x = t.x.coerceIn(0, COLUMNS - 1)
        return t.copy(x = x, y = t.y.coerceAtLeast(0), w = t.w.coerceIn(1, COLUMNS - x), h = t.h.coerceIn(1, MAX_ROWS))
    }

    /** The page's tiles, clamped, top to bottom then left to right. */
    fun placed(tiles: List<Tile>): List<Tile> = tiles.map(::clamp).sortedWith(compareBy({ it.y }, { it.x }))

    data class Rect(val x: Float, val y: Float, val width: Float, val height: Float)

    /** One square cell's side for a grid [width] wide with [gap] between cells. */
    fun cellSize(width: Float, gap: Float): Float = ((width - gap * (COLUMNS - 1)) / COLUMNS).coerceAtLeast(0f)

    /** Where [t] sits in a grid [width] wide (same units as [gap]). */
    fun rect(t: Tile, width: Float, gap: Float): Rect {
        val c = cellSize(width, gap)
        val p = clamp(t)
        return Rect(p.x * (c + gap), p.y * (c + gap), p.w * c + (p.w - 1) * gap, p.h * c + (p.h - 1) * gap)
    }

    /** Height of a page's grid: down to the bottom of its lowest tile (0 when empty). */
    fun height(tiles: List<Tile>, width: Float, gap: Float): Float {
        val rows = tiles.maxOfOrNull { clamp(it).let { p -> p.y + p.h } } ?: return 0f
        val c = cellSize(width, gap)
        return rows * c + (rows - 1) * gap
    }

    /**
     * Narrower than the phone (or a dial shorter than 4 rows): draw the tile's small version
     * (the compact thermostat that opens its page, the short weather).
     */
    fun isCompact(t: Tile): Boolean {
        val p = clamp(t)
        return p.w < COLUMNS || (t.type == "dial" && p.h < 4)
    }

    // -- a "devices" tile inside its own rectangle -----------------------------------------------------

    /** Sizes (dp) the phone uses for a devices tile's contents, shared with the UI. */
    const val DEVICE_PADDING = 10f
    const val DEVICE_TITLE = 28f
    const val DEVICE_ROW = 56f
    const val DEVICE_GAP = 8f
    /** From this width (dp) a devices tile shows two columns of buttons; narrower is a list. */
    const val TWO_COLUMNS_FROM = 240f

    /** [shown] devices in [columns]; when [more] > 0 the last slot is a "+[more] more" button. */
    data class DeviceFit(val columns: Int, val rows: Int, val shown: Int, val more: Int)

    fun fitDevices(count: Int, width: Float, height: Float, titled: Boolean, list: Boolean = false): DeviceFit {
        val columns = if (!list && width >= TWO_COLUMNS_FROM) 2 else 1
        val avail = height - 2 * DEVICE_PADDING - (if (titled) DEVICE_TITLE else 0f)
        val rows = floor((avail + DEVICE_GAP) / (DEVICE_ROW + DEVICE_GAP)).toInt().coerceAtLeast(1)
        val capacity = rows * columns
        if (count <= capacity) return DeviceFit(columns, rows, count, 0)
        val shown = capacity - 1
        return DeviceFit(columns, rows, shown, count - shown)
    }
}
