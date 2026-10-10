package ca.nimaste.newman.core

import ca.nimaste.newman.core.hub.Effect
import ca.nimaste.newman.core.hub.HubReducer
import ca.nimaste.newman.core.hub.HubState
import ca.nimaste.newman.core.live.LiveMessage
import ca.nimaste.newman.core.model.AuthState
import ca.nimaste.newman.core.model.Dashboard
import ca.nimaste.newman.core.model.Page
import ca.nimaste.newman.core.model.PhoneGrid
import ca.nimaste.newman.core.model.PhoneGridLayout
import ca.nimaste.newman.core.model.PhoneTabs
import ca.nimaste.newman.core.model.Tile
import ca.nimaste.newman.core.model.User
import ca.nimaste.newman.core.model.jsonOf
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class PhoneGridTest {

    private val reply = """
        {"source": "own", "futureField": {"x": 1},
         "layout": {"today": true, "density": "cosy",
           "pages": [
             {"id": "home", "name": "Home", "icon": "home", "tab": true, "colour": "teal",
              "tiles": [
                {"id": "t1", "type": "devices", "x": 0, "y": 0, "w": 4, "h": 2, "config": {"entities": ["a", "b"]}, "textSize": 120, "pinned": true},
                {"id": "t2", "type": "dial", "x": 2, "y": 2, "w": 2, "h": 2, "config": {"entity": "th"}}
              ]},
             {"id": "climate", "name": "Climate", "icon": "thermo", "tab": false,
              "tiles": [{"id": "c1", "type": "dial", "x": 0, "y": 0, "w": 4, "h": 4, "config": {"entity": "th"}}]},
             {"id": "sec", "name": "Security", "icon": "shield", "tab": true, "tiles": []}
           ]}}
    """.trimIndent()

    private fun page(id: String, tab: Boolean, vararg tiles: Tile) = Page(id, id.replaceFirstChar { it.uppercase() }, tiles = tiles.toList(), tab = tab)
    private fun tile(id: String, x: Int, y: Int, w: Int, h: Int, type: String = "camera") = Tile(id, type, x, y, w, h)

    @Test
    fun parsesTheLayoutIgnoringUnknownFields() {
        val r = PhoneGrid.parse(reply)
        assertEquals("own", r.source)
        assertTrue(r.layout.today)
        assertEquals(listOf("home", "climate", "sec"), r.layout.pages.map { it.id })
        assertEquals(listOf(true, false, true), r.layout.pages.map { it.tab })
        val t1 = r.layout.pages[0].tiles[0]
        assertEquals(listOf(0, 0, 4, 2), listOf(t1.x, t1.y, t1.w, t1.h))
        assertEquals(120.0, t1.textSize!!, 0.0)
        assertEquals(1.2f, t1.textScale, 0.001f)
        // No textSize: null, normal text.
        val t2 = r.layout.pages[0].tiles[1]
        assertNull(t2.textSize)
        assertEquals(1f, t2.textScale, 0f)
        assertEquals("th", (t2.config["entity"] as kotlinx.serialization.json.JsonPrimitive).content)
    }

    @Test
    fun missingFieldsTakeDefaults() {
        val r = PhoneGrid.parse("""{"layout": {"pages": [{"id": "p", "tiles": [{"id": "t", "type": "weather"}]}]}}""")
        assertEquals("default", r.source)
        assertFalse(r.layout.today)
        assertFalse(r.layout.pages[0].tab)
        assertEquals(1, r.layout.pages[0].tiles[0].w)
        // Dashboards' config.textSize still works when the tile has none of its own.
        val t = Tile("t", "values", config = JsonObject(mapOf("textSize" to jsonOf(80))))
        assertEquals(0.8f, t.textScale, 0.001f)
    }

    @Test
    fun tabPlanTakesTabPagesInOrderMaxThreeAndLinksTheRest() {
        val layout = PhoneGridLayout(today = true, pages = listOf(
            page("a", true), page("b", false), page("c", true), page("d", true), page("e", true), page("f", false),
        ))
        val plan = PhoneGrid.plan(layout)
        assertEquals(listOf("a", "c", "d"), plan.tabs.map { it.id })
        assertEquals(listOf("b", "e", "f"), plan.more.map { it.id })
        assertTrue(plan.explicit)
        assertTrue(plan.todayCard)
        assertNull(plan.today)
        assertTrue(PhoneTabs.showsTodayCard(plan, plan.tabs[0]))
        assertTrue(PhoneTabs.todayWhenEmpty(plan))

        val parsed = PhoneGrid.plan(PhoneGrid.parse(reply).layout)
        assertEquals(listOf("home", "sec"), parsed.tabs.map { it.id })
        assertEquals(listOf("climate"), parsed.more.map { it.id })
        // The compact thermostat on Home opens Climate, which has no tab.
        assertEquals("climate", PhoneTabs.pageForDial(parsed, "th")?.id)
    }

    @Test
    fun todayCardOnlyWhenTheLayoutAsks() {
        val plan = PhoneGrid.plan(PhoneGridLayout(today = false, pages = listOf(page("a", true, tile("t", 0, 0, 4, 4, "todo")))))
        assertFalse(PhoneTabs.showsTodayCard(plan, plan.tabs[0]))
        // Without a layout, the automatic flow keeps its rule: the card unless the page has its own.
        val flow = PhoneTabs.plan(listOf(page("x", false, tile("t", 0, 0, 4, 4, "camera"))))
        assertFalse(flow.explicit)
        assertTrue(PhoneTabs.showsTodayCard(flow, flow.tabs[0]))
        assertFalse(PhoneTabs.todayWhenEmpty(flow))
    }

    @Test
    fun aLayoutWithoutTabsShowsItsFirstPage() {
        val plan = PhoneGrid.plan(PhoneGridLayout(pages = listOf(page("a", false), page("b", false))))
        assertEquals(listOf("a"), plan.tabs.map { it.id })
        assertEquals(listOf("b"), plan.more.map { it.id })
    }

    @Test
    fun cellRectanglesAreSquareCellsWithGaps() {
        // 358 wide, 10 gaps: (358 - 30) / 4 = 82 per cell.
        assertEquals(82f, PhoneGrid.cellSize(358f, 10f), 0.001f)
        assertEquals(PhoneGrid.Rect(0f, 0f, 82f, 82f), PhoneGrid.rect(tile("a", 0, 0, 1, 1), 358f, 10f))
        assertEquals(PhoneGrid.Rect(184f, 92f, 174f, 266f), PhoneGrid.rect(tile("b", 2, 1, 2, 3), 358f, 10f))
        assertEquals(PhoneGrid.Rect(0f, 0f, 358f, 82f), PhoneGrid.rect(tile("c", 0, 0, 4, 1), 358f, 10f))
        // Bottom of the lowest tile; empty cells above it stay empty.
        assertEquals(4 * 82f + 3 * 10f, PhoneGrid.height(listOf(tile("a", 0, 0, 1, 1), tile("b", 2, 1, 2, 3)), 358f, 10f), 0.001f)
        assertEquals(0f, PhoneGrid.height(emptyList(), 358f, 10f), 0f)
    }

    @Test
    fun tilesAreKeptInsideTheGrid() {
        val c = PhoneGrid.clamp(tile("x", 3, -2, 3, 12))
        assertEquals(listOf(3, 0, 1, 8), listOf(c.x, c.y, c.w, c.h))
        val d = PhoneGrid.clamp(tile("y", 9, 1, 0, 0))
        assertEquals(listOf(3, 1, 1, 1), listOf(d.x, d.y, d.w, d.h))
        assertEquals(listOf("b", "c", "a"), PhoneGrid.placed(listOf(tile("a", 0, 3, 1, 1), tile("b", 2, 0, 1, 1), tile("c", 0, 1, 1, 1))).map { it.id })
        assertTrue(PhoneGrid.isCompact(tile("w", 0, 0, 2, 2, "weather")))
        assertFalse(PhoneGrid.isCompact(tile("w", 0, 0, 4, 2, "weather")))
        assertTrue(PhoneGrid.isCompact(tile("d", 0, 0, 4, 3, "dial")))
        assertFalse(PhoneGrid.isCompact(tile("d", 0, 0, 4, 4, "dial")))
    }

    @Test
    fun devicesFitTheirRectangle() {
        // Wide: two columns. 266 high, no title: (266 - 20 + 8) / 64 = 3 rows -> 6 slots.
        assertEquals(PhoneGrid.DeviceFit(2, 3, 5, 0), PhoneGrid.fitDevices(5, 358f, 266f, titled = false))
        // Too many: the last slot becomes "+N more".
        assertEquals(PhoneGrid.DeviceFit(2, 3, 5, 4), PhoneGrid.fitDevices(9, 358f, 266f, titled = false))
        // Narrow: a list. 174 high with a title: (174 - 20 - 28 + 8) / 64 = 2 rows.
        assertEquals(PhoneGrid.DeviceFit(1, 2, 1, 3), PhoneGrid.fitDevices(4, 174f, 174f, titled = true))
        // The list look stays a list even when wide.
        assertEquals(1, PhoneGrid.fitDevices(2, 358f, 174f, titled = false, list = true).columns)
        // Always at least one row.
        assertEquals(PhoneGrid.DeviceFit(1, 1, 0, 3), PhoneGrid.fitDevices(3, 82f, 40f, titled = true))
    }

    @Test
    fun fallbackOn404KeepOnOtherFailures() {
        val current = PhoneGridLayout(today = true)
        val got = PhoneGrid.outcome(200, reply)
        assertTrue(got is PhoneGrid.Fetch.Got)
        assertEquals(3, PhoneGrid.next(current, got)!!.pages.size)
        // An older hub: no layout, flow the dashboard.
        assertEquals(PhoneGrid.Fetch.Unsupported, PhoneGrid.outcome(404, """{"error": "Not found"}"""))
        assertNull(PhoneGrid.next(current, PhoneGrid.outcome(404, null)))
        // Offline, a hub error or a garbled reply: keep what we had.
        assertEquals(PhoneGrid.Fetch.Failed, PhoneGrid.outcome(500, null))
        assertEquals(PhoneGrid.Fetch.Failed, PhoneGrid.outcome(200, "<html>"))
        assertSame(current, PhoneGrid.next(current, PhoneGrid.Fetch.Failed))
        assertNull(PhoneGrid.next(null, PhoneGrid.Fetch.Failed))
    }

    @Test
    fun hubStatePlansFromTheLayoutWhenThereIsOne() {
        val dash = Dashboard(pages = Fixtures.dashboards.first().pages)
        val flow = HubState(dashboard = dash)
        assertFalse(flow.plan.explicit)
        assertEquals(PhoneTabs.plan(dash.pages), flow.plan)
        val withLayout = flow.copy(phoneLayout = PhoneGrid.parse(reply).layout)
        assertTrue(withLayout.plan.explicit)
        assertEquals(listOf("home", "sec"), withLayout.plan.tabs.map { it.id })
    }

    @Test
    fun anotherPersonsDashboardEditReloadsTheLayoutOnly() {
        val me = HubState(auth = AuthState(user = User(id = "u_me")))
        assertEquals(Effect.NONE, HubReducer.apply(me, LiveMessage.DashboardChanged("d", "u_other")).second)
        val withLayout = me.copy(phoneLayout = PhoneGridLayout())
        assertEquals(Effect.RELOAD_LAYOUT, HubReducer.apply(withLayout, LiveMessage.DashboardChanged("d", "u_other")).second)
        assertEquals(Effect.RELOAD_DASHBOARD, HubReducer.apply(withLayout, LiveMessage.DashboardChanged("d", "u_me")).second)
    }
}
