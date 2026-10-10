package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.model.Page
import ca.nimaste.newman.core.model.PhoneTabs
import ca.nimaste.newman.core.model.Tile
import ca.nimaste.newman.core.model.TileFlow
import ca.nimaste.newman.core.model.jsonOf
import ca.nimaste.newman.core.model.string
import ca.nimaste.newman.core.today.TodayBriefing
import ca.nimaste.newman.core.today.TodayView
import ca.nimaste.newman.core.today.TodayView.Button
import ca.nimaste.newman.core.today.TodayView.Tone
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TodayTest {
    private val now = 1_790_000_000.0

    /** Shaped like server/newman/today.py build() (a phone: this person's items and everyone's). */
    private val briefing = NewmanJson.decodeFromString(TodayBriefing.serializer(), """{
      "date": "2026-10-02",
      "todo": [
        {"id": "upkeep.furnace", "source": "upkeep", "entity": "upkeep.furnace", "title": "Furnace filter", "owner": "Chris",
         "ownerId": "p1", "askWho": false, "every": "3 months", "due": "2026-09-28", "icon": "", "level": "overdue",
         "status": "4 days late", "sort": -4, "action": "done"},
        {"id": "ring.side_door:health", "source": "device", "entity": "ring.side_door", "level": "device",
         "title": "Side door: battery low", "status": "battery low", "sort": 0, "action": "open", "askWho": false},
        {"id": "upkeep.plants", "source": "upkeep", "entity": "upkeep.plants", "title": "Water the plants", "owner": "",
         "level": "due", "status": "Due today", "action": "done"},
        {"id": "lg.dishwasher:need:rinse aid low", "source": "appliance", "entity": "lg.dishwasher", "level": "appliance",
         "title": "Dishwasher: rinse aid low", "status": "Reported by the dishwasher", "action": "dismiss"}
      ],
      "soon": [
        {"id": "upkeep.smoke", "source": "upkeep", "entity": "upkeep.smoke", "title": "Test smoke alarms", "level": "soon",
         "status": "In 3 days", "action": "done"}
      ],
      "events": [
        {"title": "Dentist", "allDay": false, "time": "3:30 PM", "start": "2026-10-02T15:30:00", "end": "2026-10-02T16:30:00",
         "location": "Main St", "calendar": "Family", "colour": "#7FB8FF", "now": false}
      ],
      "headsUp": [{"id": "weather.home:rain", "source": "weather", "icon": "rain", "entity": "weather.home",
                   "text": "Rain likely from 3 PM (70%)", "short": "rain from 3 PM"}],
      "counts": {"todo": 4, "soon": 1, "events": 1}
    }""")

    @Test
    fun cardShowsWhatNeedsDoingMostUrgentFirst() {
        val card = TodayView.card(briefing, emptyMap(), now)
        assertEquals("4 things to do · 1 event", card.summary)
        assertEquals(listOf("upkeep.furnace", "ring.side_door:health", "upkeep.plants", "lg.dishwasher:need:rinse aid low"), card.rows.map { it.id })
        assertEquals(1, card.more) // the smoke alarms are coming up but there's no room
        val furnace = card.rows[0]
        assertEquals(Tone.URGENT, furnace.tone)
        assertEquals("4 days late · Chris", furnace.status)
        assertEquals(listOf(Button.DONE, Button.SNOOZE), furnace.buttons)
        assertEquals(listOf(Button.OPEN), card.rows[1].buttons)
        assertEquals(Tone.DUE, card.rows[2].tone)
        assertEquals("Due today", card.rows[2].status) // shared items name no owner
        assertEquals(listOf(Button.DISMISS), card.rows[3].buttons)
        assertEquals("Dentist", card.events.single().title)
        assertEquals("Rain likely from 3 PM (70%)", card.headsUp.single().text)
    }

    @Test
    fun doneRowsStayWithUndoThenComingUpFillsTheRoom() {
        val furnace = briefing.todo[0]
        val done = mapOf(furnace.id to TodayView.Done(furnace, TodayView.upkeepId(furnace), now - 30))
        // The hub no longer lists it as to do once done.
        val after = briefing.copy(todo = briefing.todo.drop(1))
        val card = TodayView.card(after, done, now)
        assertEquals("3 things to do · 1 event", card.summary)
        val doneRow = card.rows.first { it.id == furnace.id }
        assertEquals(Tone.DONE, doneRow.tone)
        assertEquals(listOf(Button.UNDO), doneRow.buttons)
        assertEquals("Done just now", doneRow.status)
        // After ten minutes it drops off.
        val later = TodayView.card(after, done, now + 700)
        assertTrue(later.rows.none { it.id == furnace.id })
        assertEquals(listOf("ring.side_door:health", "upkeep.plants", "lg.dishwasher:need:rinse aid low", "upkeep.smoke"), later.rows.map { it.id })
        assertEquals(0, later.more)
    }

    @Test
    fun allDoneAndEmpty() {
        val plants = briefing.todo[2]
        val onlyDone = TodayBriefing(todo = emptyList())
        val card = TodayView.card(onlyDone, mapOf(plants.id to TodayView.Done(plants, "plants", now)), now)
        assertEquals("All done for today", card.summary)
        assertTrue(!card.empty)
        val empty = TodayView.card(TodayBriefing(), emptyMap(), now)
        assertTrue(empty.empty)
        assertEquals("", empty.summary)
        assertEquals("1 thing to do", TodayView.summary(1, 0, 0))
        assertEquals("2 events", TodayView.summary(0, 0, 2))
    }

    @Test
    fun upkeepIdsAndGreeting() {
        assertEquals("furnace", TodayView.upkeepId(briefing.todo[0]))
        assertEquals("Good morning", TodayView.greeting(7))
        assertEquals("Good afternoon", TodayView.greeting(12))
        assertEquals("Good evening", TodayView.greeting(18))
    }

    private fun tile(id: String, type: String, vararg config: Pair<String, Any?>) =
        Tile(id, type, 0, 0, 4, 4, JsonObject(config.associate { it.first to jsonOf(it.second) }))

    @Test
    fun tabsAreThreePagesThenPeopleAndAlerts() {
        val pages = Fixtures.dashboards.first().pages
        val plan = PhoneTabs.plan(pages)
        assertEquals(listOf("overview", "security", "lights"), plan.tabs.map { it.id })
        assertNull(plan.today)
        assertEquals(listOf("climate"), plan.more.map { it.id })
        // The Overview's thermostat tile opens Climate.
        val dial = pages.first { it.id == "overview" }.tiles.first { it.type == "dial" }
        assertEquals("climate", PhoneTabs.pageForDial(plan, dial.config.string("entity"))?.id)
        assertNull(PhoneTabs.pageForDial(plan, "nothing.here"))

        // A Today page first (the starter dashboard since Today) is the Today card, not a tab.
        val today = Page("today", "Today", "sun", listOf(tile("g", "greeting"), tile("t", "todo"), tile("c", "calendar"), tile("w", "weather")))
        val withToday = PhoneTabs.plan(listOf(today) + pages)
        assertEquals("today", withToday.today?.id)
        assertEquals(listOf("overview", "security", "lights"), withToday.tabs.map { it.id })
        assertTrue(PhoneTabs.isTodayPage(today))
        // A page that mixes a To do tile with devices is an ordinary page.
        assertTrue(!PhoneTabs.isTodayPage(Page("x", "Home", tiles = listOf(tile("t", "todo"), tile("d", "devices")))))
        // The greeting tile isn't drawn on the phone (the header greets); To do is a full-width card.
        val cells = TileFlow.cells(today)
        assertEquals(listOf("t", "c", "w"), cells.map { it.key })
        assertEquals(4, cells.first().span)
        assertTrue(PhoneTabs.hasTodayTile(today))
    }
}
