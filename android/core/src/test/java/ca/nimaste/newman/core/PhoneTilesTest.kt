package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.model.TileFit
import ca.nimaste.newman.core.people.PeopleText
import ca.nimaste.newman.core.people.PeopleTile
import ca.nimaste.newman.core.people.PeopleTile.Look
import ca.nimaste.newman.core.people.Person
import ca.nimaste.newman.core.today.TodayBriefing
import ca.nimaste.newman.core.today.TodayView
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The People and Greeting tiles on a phone page (web/src/wall/PhoneTiles.jsx). */
class PhoneTilesTest {
    private val now = 1_790_000_000.0

    private fun person(id: String, name: String, sharing: Boolean = true, home: Boolean = false) =
        Person(id = id, name = name, sharing = sharing, home = home, lat = if (sharing) 43.6 else null, lon = if (sharing) -79.4 else null, at = now - 240)

    private val household = listOf(
        person("a", "Chris", home = true), person("b", "Marlina"), person("c", "Massimo", sharing = false), person("d", "Nonna"), person("e", "Zia"),
    )

    @Test
    fun picksPeopleInTheHubsOrder() {
        assertEquals(listOf("a", "b", "c", "d", "e"), PeopleTile.select(household, emptyList()).map { it.id })
        assertEquals(listOf("b", "d"), PeopleTile.select(household, listOf("d", "b", "gone")).map { it.id })
        assertTrue(PeopleTile.select(household, listOf("gone")).isEmpty())
    }

    @Test
    fun fitsRowsAndCountsTheRest() {
        assertEquals(TileFit.Fit(2, 3, 6f), TileFit.list(5, 100f, 44f, 6f, 0f))
        assertEquals(TileFit.Fit(2, 0, 6f), TileFit.list(2, 100f, 44f, 6f, 18f))
        // Room kept for the "+N more" line.
        assertEquals(TileFit.Fit(1, 2, 56f), TileFit.list(3, 100f, 44f, 6f, 18f))
        assertEquals(TileFit.Fit(0, 3, 30f), TileFit.list(3, 30f, 44f, 6f, 18f))
        assertEquals(4, TileFit.list(4, Float.POSITIVE_INFINITY, 44f, 6f, 0f).shown)
        assertEquals(0, TileFit.rowsIn(-20f, 44f, 6f))
    }

    @Test
    fun peopleTileLayoutBySize() {
        // A 390 dp phone: 82 dp cells, 10 dp gaps; inside a card's 14 dp padding.
        // 4x3: four rows, the fifth person counted beside the title.
        assertEquals(PeopleTile.Layout(Look.ROWS, true, 4, 1, true, false, true), PeopleTile.layout(5, 330f, 238f))
        assertEquals(PeopleTile.Layout(Look.ROWS, true, 3, 0, false, false, true), PeopleTile.layout(3, 330f, 238f))
        // 2x2.
        assertEquals(PeopleTile.Layout(Look.ROWS, true, 2, 3, true, false, true), PeopleTile.layout(5, 146f, 146f))
        // 1x2: no initials, "+3 more" under the rows.
        assertEquals(PeopleTile.Layout(Look.ROWS, true, 2, 3, false, true, false), PeopleTile.layout(5, 54f, 146f))
        // 4x1: who's there on one line.
        assertEquals(Look.ONE_LINE, PeopleTile.layout(5, 330f, 54f).look)
        // One person in one row of cells: their row, no title.
        assertEquals(PeopleTile.Layout(Look.ROWS, false, 1, 0, false, false, true), PeopleTile.layout(1, 330f, 54f))
        // A flowed page: everyone.
        assertEquals(PeopleTile.Layout(Look.ROWS, true, 5, 0, false, false, true), PeopleTile.layout(5, 330f, Float.POSITIVE_INFINITY))
    }

    @Test
    fun peopleTileWording() {
        assertEquals("Chris (home) · Marlina · Massimo", PeopleTile.oneLine(household.take(3)))
        val chris = PeopleText.row(household[0], now)
        assertEquals(listOf("Home", "updated 4 min ago"), listOfNotNull(chris.where, chris.updated))
        assertTrue(chris.located && chris.home)
        val massimo = PeopleText.row(household[2], now)
        assertEquals("Not sharing location", massimo.where)
        assertFalse(massimo.located) // dim, not tappable
        assertEquals(null, massimo.updated)
    }

    private val briefing = NewmanJson.decodeFromString(TodayBriefing.serializer(), """{
      "todo": [
        {"id": "upkeep.furnace", "source": "upkeep", "title": "Furnace filter", "level": "overdue", "action": "done"},
        {"id": "upkeep.plants", "source": "upkeep", "title": "Water the plants", "level": "due", "action": "done"},
        {"id": "lg.dishwasher:need", "source": "appliance", "title": "Dishwasher: rinse aid low", "level": "appliance", "action": "dismiss"}
      ],
      "events": [{"title": "Dentist"}, {"title": "Soccer"}],
      "headsUp": [
        {"id": "w:1", "source": "weather", "icon": "warning", "text": "Special weather statement", "short": "special weather statement", "level": "statement"},
        {"id": "w:2", "source": "weather", "icon": "warning", "text": "Wind warning", "level": "warning"},
        {"id": "cal", "source": "calendar", "icon": "calendar", "text": "Dentist tomorrow"},
        {"id": "w:rain", "source": "weather", "icon": "rain", "text": "Rain likely from 3 PM (70%)", "short": "rain from 3 PM"}
      ]
    }""")

    @Test
    fun greetingLine() {
        assertEquals("3 things to do · 2 events today · wind warning · rain from 3 PM", TodayView.greetingLine(briefing, emptyMap(), now))
        val furnace = briefing.todo[0]
        val doneHere = mapOf(furnace.id to TodayView.Done(furnace, "furnace", now - 30))
        assertEquals("2 things to do · 2 events today · wind warning · rain from 3 PM", TodayView.greetingLine(briefing, doneHere, now))
        assertEquals("3 things to do", TodayView.greetingLine(briefing, doneHere, now + 700).substringBefore(" · "))
        assertEquals("Nothing to do", TodayView.greetingLine(TodayBriefing(), emptyMap(), now))
        val one = briefing.copy(todo = briefing.todo.take(1), events = briefing.events.take(1), headsUp = emptyList())
        assertEquals("1 thing to do · 1 event today", TodayView.greetingLine(one, emptyMap(), now))
    }

    @Test
    fun greetingByHourAndSize() {
        assertEquals("Good morning", TodayView.greeting(0))
        assertEquals("Good morning", TodayView.greeting(11))
        assertEquals("Good afternoon", TodayView.greeting(12))
        assertEquals("Good afternoon", TodayView.greeting(17))
        assertEquals("Good evening", TodayView.greeting(18))
        // One row of cells (82 dp less padding): both lines fit.
        assertEquals(TodayView.GreetingFit(23f, true), TodayView.greetingFit(54f))
        assertEquals(TodayView.GreetingFit(14f, false), TodayView.greetingFit(30f))
        assertEquals(TodayView.GreetingFit(26f, true), TodayView.greetingFit(200f))
        assertEquals(TodayView.GreetingFit(26f, true), TodayView.greetingFit(Float.POSITIVE_INFINITY))
    }
}
