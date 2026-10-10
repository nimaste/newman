package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.Page
import ca.nimaste.newman.core.model.Tile
import ca.nimaste.newman.core.model.TileFlow
import ca.nimaste.newman.core.model.TileFlow.Content
import ca.nimaste.newman.core.model.jsonOf
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class TileFlowTest {

    private fun tile(id: String, type: String, w: Int = 4, h: Int = 3, vararg config: Pair<String, Any?>) =
        Tile(id, type, 0, 0, w, h, JsonObject(config.associate { it.first to jsonOf(it.second) }))

    private fun rowsOk(cells: List<TileFlow.Cell>) =
        TileFlow.rows(cells).forEach { row -> assertTrue("row fits: $row", row.sumOf { it.span } <= TileFlow.COLUMNS) }

    @Test
    fun overviewOfTheRealDashboardFlowsIntoFourColumns() {
        val overview = Fixtures.dashboards.first().pages.first { it.id == "overview" }
        val cells = TileFlow.cells(overview)
        rowsOk(cells)
        // Every tile shows up; the shortcuts devices tile became one half-width card per device.
        val devicesTile = overview.tiles.first { it.type == "devices" }
        val cards = cells.filter { it.content is Content.DeviceCard }
        assertEquals(TileFlow.COLUMNS / 2, cards.first().span)
        assertEquals(devicesTile.config["entities"].toString().count { it == ',' } + 1, cards.size)
        val whole = cells.mapNotNull { (it.content as? Content.WholeTile)?.tile?.id }
        overview.tiles.filter { it.type != "devices" }.forEach { assertTrue(it.id in whole) }
        // Wide tiles span 4; the small dial and the weather span 2.
        assertEquals(4, cells.first { (it.content as? Content.WholeTile)?.tile?.type == "camera" }.span)
        assertEquals(4, cells.first { (it.content as? Content.WholeTile)?.tile?.type == "calendar" }.span)
        assertEquals(2, cells.first { (it.content as? Content.WholeTile)?.tile?.type == "dial" }.span)
        assertEquals(2, cells.first { (it.content as? Content.WholeTile)?.tile?.type == "weather" }.span)
        // Dense packing leaves no half-empty rows except possibly the last.
        TileFlow.rows(cells).dropLast(1).forEach { row -> assertEquals(4, row.sumOf { it.span }) }
    }

    @Test
    fun bigDevicesTileGivesFullWidthCardsWithoutAHeaderWhenAlone() {
        val lights = Fixtures.dashboards.first().pages.first { it.id == "lights" }
        val cells = TileFlow.cells(lights)
        assertTrue(cells.all { it.content is Content.DeviceCard && it.span == 4 })
        assertTrue(cells.all { (it.content as Content.DeviceCard).wide })
    }

    @Test
    fun listDevicesAndUnknownTypesSpanTheWholeRow() {
        val page = Page("p", "Security", tiles = listOf(
            tile("a", "devices", 7, 3, "style" to "list", "entities" to listOf("x", "y")),
            tile("b", "something-new"),
        ))
        val cells = TileFlow.cells(page)
        assertEquals(listOf(4, 4), cells.map { it.span })
    }

    @Test
    fun keepsDashboardOrderWhenRowsAlreadyFit() {
        val page = Page("p", tiles = listOf(
            tile("d", "dial", 3, 3, "entity" to "t"),
            tile("w", "weather", 4, 3),
            tile("c", "camera", 5, 5),
        ))
        assertEquals(listOf("d", "w", "c"), TileFlow.cells(page).map { it.key })
    }

    @Test
    fun aLaterHalfCellFillsTheGapBeforeAWideOne() {
        val page = Page("p", tiles = listOf(
            tile("d", "dial", 3, 3),
            tile("c", "camera", 5, 5),
            tile("w", "weather", 4, 3),
            tile("k", "calendar", 4, 5),
        ))
        assertEquals(listOf("d", "w", "c", "k"), TileFlow.cells(page).map { it.key })
    }

    @Test
    fun cardsDoNotJumpAcrossASectionHeader() {
        val page = Page("p", "Home", tiles = listOf(
            tile("d", "dial", 3, 3),
            tile("s", "devices", 3, 4, "title" to "Shortcuts", "entities" to listOf("e1", "e2")),
        ))
        val keys = TileFlow.cells(page).map { it.key }
        assertEquals(listOf("d", "s:head", "s:e1", "s:e2"), keys)
        rowsOk(TileFlow.cells(page))
    }

    @Test
    fun bigDialIsWide() {
        val page = Page("p", tiles = listOf(tile("d", "dial", 8, 8)))
        val cell = TileFlow.cells(page).single()
        assertEquals(4, cell.span)
        assertEquals(false, (cell.content as Content.WholeTile).compact)
    }
}
