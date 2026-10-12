package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.widgets.WidgetAction
import ca.nimaste.newman.core.widgets.WidgetChoice
import ca.nimaste.newman.core.widgets.WidgetDevice
import ca.nimaste.newman.core.widgets.WidgetDevicesResponse
import ca.nimaste.newman.core.widgets.WidgetGrouping
import ca.nimaste.newman.core.widgets.WidgetHome
import ca.nimaste.newman.core.widgets.WidgetLayout
import ca.nimaste.newman.core.widgets.WidgetPicker
import ca.nimaste.newman.core.widgets.WidgetTone
import ca.nimaste.newman.core.widgets.WidgetReading
import ca.nimaste.newman.core.widgets.WidgetTiles
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WidgetsTest {
    // What GET /api/widgets/state returns (server/tests/test_widgets.py has the hub side).
    private val stateJson = """
        {"devices": [
          {"id": "fake.porch", "name": "Porch", "kind": "light", "state": "off", "stateLabel": "Off", "available": true,
           "active": false, "icon": "light", "area": "Outside", "reading": null,
           "actions": [{"command": "turn_on", "label": "Turn on", "sensitive": false, "primary": true},
                       {"command": "turn_off", "label": "Turn off", "sensitive": false, "primary": false}]},
          {"id": "fake.side", "name": "Side Door", "kind": "lock", "state": "LOCKED", "stateLabel": "Locked",
           "available": true, "icon": "lock", "area": "Mudroom", "reading": null,
           "actions": [{"command": "unlock", "label": "Unlock", "sensitive": true, "primary": true},
                       {"command": "lock", "label": "Lock", "sensitive": false, "primary": false}]},
          {"id": "fake.temp", "name": "Basement", "kind": "temperature", "state": 21.5, "stateLabel": "21.5 °C",
           "available": true, "icon": "temperature", "area": "Basement", "actions": [],
           "reading": {"value": 21.5, "unit": "°C"}, "somethingNew": 1},
          {"id": "fake.upkeep", "name": "Furnace filter", "kind": "upkeep", "state": "ok", "stateLabel": "Up to date",
           "actions": [{"command": "done", "label": "Mark done", "sensitive": false, "primary": false}]}
        ]}
    """.trimIndent()

    @Test
    fun parsesTheHubsStateAndChoosesTheTapAction() {
        val list = WidgetTiles.parseState(stateJson)
        assertEquals(listOf("fake.porch", "fake.side", "fake.temp", "fake.upkeep"), list.map { it.id })
        val (porch, side, temp, upkeep) = list
        assertEquals("turn_on", WidgetTiles.tapAction(porch)?.command)
        assertEquals("unlock", WidgetTiles.tapAction(side)?.command)
        assertNull(WidgetTiles.tapAction(temp)) // a sensor: nothing to run
        assertNull(WidgetTiles.tapAction(upkeep)) // nothing primary right now
        assertNull(WidgetTiles.tapAction(porch.copy(available = false))) // offline: no taps
        assertEquals("21.5", temp.state) // a number state reads as text
    }

    @Test
    fun confirmsOnlySensitiveActionsAndOnlyWhenAsked() {
        val unlock = WidgetAction("unlock", "Unlock", sensitive = true, primary = true)
        val on = WidgetAction("turn_on", "Turn on", primary = true)
        assertFalse(WidgetTiles.needsConfirm(unlock, confirmSensitive = false)) // default OFF
        assertTrue(WidgetTiles.needsConfirm(unlock, confirmSensitive = true))
        assertFalse(WidgetTiles.needsConfirm(on, confirmSensitive = true))
        assertFalse(WidgetTiles.needsConfirm(null, confirmSensitive = true))
    }

    @Test
    fun stateTextShowsReadingsExactlyThenStates() {
        val list = WidgetTiles.parseState(stateJson)
        assertEquals("Off", WidgetTiles.stateText(list[0]))
        assertEquals("21.5 °C", WidgetTiles.stateText(list[2]))
        assertEquals("Offline", WidgetTiles.stateText(list[0].copy(available = false)))
        assertEquals("64%", WidgetTiles.readingText(WidgetReading(JsonPrimitive(64), "%")))
        assertEquals("1013.25 hPa", WidgetTiles.readingText(WidgetReading(JsonPrimitive(1013.25), "hPa"))) // never rounded
        assertEquals("3", WidgetTiles.readingText(WidgetReading(JsonPrimitive(3), "")))
        assertNull(WidgetTiles.readingText(null))
        val tapped = WidgetTiles.optimistic(list[1], WidgetTiles.tapAction(list[1])!!)
        assertEquals("Unlock…", WidgetTiles.stateText(tapped))
        assertEquals("Side Door", tapped.name)
    }

    @Test
    fun mergeKeepsTheWidgetsOrderAndCacheRoundTrips() {
        val list = WidgetTiles.parseState(stateJson)
        val merged = WidgetTiles.merge(listOf("fake.temp", "gone", "fake.porch"), list)
        assertEquals(listOf("fake.temp", "fake.porch"), merged.map { it.id })
        assertEquals(merged, WidgetTiles.decode(WidgetTiles.encode(merged)))
        assertEquals(emptyList<WidgetDevice>(), WidgetTiles.decode("not json"))
        assertEquals(emptyList<WidgetDevice>(), WidgetTiles.decode(null))
    }

    @Test
    fun columnsFitTheWidget() {
        assertEquals(1, WidgetTiles.columns(60f))
        assertEquals(2, WidgetTiles.columns(160f))
        assertEquals(4, WidgetTiles.columns(320f))
        assertEquals(5, WidgetTiles.columns(2000f))
    }

    private val choices = NewmanJson.decodeFromString(WidgetDevicesResponse.serializer(), """
        {"devices": [
          {"id": "l1", "name": "Porch", "kind": "light", "kindName": "Lights", "area": "Outside", "icon": "light", "controllable": true},
          {"id": "l2", "name": "Island", "kind": "light", "kindName": "Lights", "area": "Kitchen", "icon": "light", "controllable": true},
          {"id": "k1", "name": "Side Door", "kind": "lock", "kindName": "Locks", "area": "Mudroom", "icon": "lock", "controllable": true},
          {"id": "t1", "name": "Main Floor", "kind": "thermostat", "kindName": "Thermostats", "area": null, "icon": "thermostat"},
          {"id": "s1", "name": "Attic", "kind": "temperature", "kindName": "Temperature", "area": "", "icon": "temperature"},
          {"id": "l3", "name": "Back Yard", "kind": "light", "kindName": "Lights", "area": "outside", "icon": "light", "controllable": true}
        ]}
    """.trimIndent()).devices

    @Test
    fun pickerGroupsByRoomWithRoomlessLast() {
        val groups = WidgetPicker.group(choices, WidgetGrouping.AREA)
        assertEquals(listOf("Kitchen", "Mudroom", "Outside", "outside", "No room"), groups.map { it.first })
        assertEquals(listOf("Attic", "Main Floor"), groups.last().second.map { it.name })
    }

    @Test
    fun pickerGroupsByTypeAndSearches() {
        val groups = WidgetPicker.group(choices, WidgetGrouping.TYPE)
        assertEquals(listOf("Lights", "Locks", "Temperature", "Thermostats"), groups.map { it.first })
        assertEquals(listOf("Back Yard", "Island", "Porch"), groups.first().second.map { it.name }) // by name
        // Search matches name, room or type, every word.
        assertEquals(listOf("l1", "l3"), WidgetPicker.group(choices, WidgetGrouping.AREA, "outside").flatMap { g -> g.second.map { it.id } }.sorted())
        assertEquals(listOf("k1"), WidgetPicker.group(choices, WidgetGrouping.TYPE, " locks  side ").flatMap { g -> g.second.map { it.id } })
        assertEquals(emptyList<Pair<String, List<WidgetChoice>>>(), WidgetPicker.group(choices, WidgetGrouping.TYPE, "garage"))
    }

    @Test
    fun pickerSelectionAndReorder() {
        var sel = listOf<String>()
        sel = WidgetPicker.toggle(sel, "l1")
        sel = WidgetPicker.toggle(sel, "k1")
        sel = WidgetPicker.toggle(sel, "t1")
        assertEquals(listOf("l1", "k1", "t1"), sel)
        assertEquals(listOf("l1", "t1", "k1"), WidgetPicker.move(sel, 2, -1))
        assertEquals(listOf("k1", "l1", "t1"), WidgetPicker.move(sel, 0, 1))
        assertEquals(sel, WidgetPicker.move(sel, 0, -1)) // already first
        assertEquals(sel, WidgetPicker.move(sel, 9, 1))
        assertEquals(listOf("l1", "t1"), WidgetPicker.toggle(sel, "k1"))
        assertEquals(listOf("l1", "t1"), WidgetPicker.keepKnown(listOf("l1", "gone", "t1"), choices))
        assertEquals(listOf("l1", "k1"), WidgetPicker.toggle(listOf("l1", "k1"), "t1", max = 2)) // full
        assertEquals(listOf("l1"), WidgetPicker.toggle(listOf("l1", "k1"), "k1", max = 2)) // unticking still works
    }

    @Test
    fun layoutFillsTheWidgetWithWhatWasChosen() {
        // A small (2x2) widget shows 2, a medium (4x2) or narrow one 4, a large (4x4) one 8.
        assertEquals(2, WidgetLayout.maxDevices(150f, 150f))
        assertEquals(4, WidgetLayout.maxDevices(330f, 160f))
        assertEquals(4, WidgetLayout.maxDevices(150f, 330f))
        assertEquals(8, WidgetLayout.maxDevices(330f, 380f))
        assertTrue(WidgetLayout.isWide(330f, 160f))
        assertFalse(WidgetLayout.isWide(150f, 150f))
        fun lines(n: Int, wide: Boolean) = WidgetLayout.lines(n, WidgetLayout.tileColumns(n, wide))
        assertEquals(listOf(1), lines(1, wide = false)) // one fills it
        assertEquals(listOf(1, 1), lines(2, wide = false)) // two halves, stacked on a square widget
        assertEquals(listOf(2), lines(2, wide = true)) // side by side on a wide one
        assertEquals(listOf(4), lines(4, wide = true)) // medium: four in a row
        assertEquals(listOf(1, 1, 1), lines(3, wide = false))
        assertEquals(listOf(2, 2, 1), lines(5, wide = false)) // the last one widens
        assertEquals(listOf(2, 2, 2, 2), lines(8, wide = false))
        assertEquals(emptyList<Int>(), WidgetLayout.lines(0, 2))
        assertTrue(WidgetLayout.tileCompact(70f))
        assertFalse(WidgetLayout.tileCompact(150f))
        assertTrue(WidgetLayout.rowStacked(70f))
        assertFalse(WidgetLayout.rowStacked(36f))
        assertEquals(1, WidgetLayout.rowsThatFit(60f))
        assertEquals(4, WidgetLayout.rowsThatFit(150f))
    }

    @Test
    fun toneComesFromTheHubWithAFallback() {
        val d = WidgetDevice(id = "x", tone = "good")
        assertEquals(WidgetTone.GOOD, WidgetTiles.tone(d))
        assertEquals(WidgetTone.ACTIVE, WidgetTiles.tone(d.copy(tone = "active")))
        assertEquals(WidgetTone.ALERT, WidgetTiles.tone(d.copy(tone = "alert")))
        assertEquals(WidgetTone.NEUTRAL, WidgetTiles.tone(d.copy(tone = "")))
        assertEquals(WidgetTone.OFFLINE, WidgetTiles.tone(d.copy(available = false)))
        // An older hub without tones: its "active" flag.
        assertEquals(WidgetTone.ACTIVE, WidgetTiles.tone(d.copy(tone = null, active = true)))
        assertEquals(WidgetTone.NEUTRAL, WidgetTiles.tone(d.copy(tone = null)))
        val parsed = WidgetTiles.parseState("""{"devices": [{"id": "a", "tone": "alert"}, {"id": "b"}]}""")
        assertEquals(listOf("alert", null), parsed.map { it.tone })
    }

    @Test
    fun parsesTheHomeStatus() {
        // What GET /api/widgets/home returns (server/tests/test_widgets.py has the hub side).
        val json = """
            {"secure": false, "headline": "Side Door unlocked", "detail": "door closed",
             "problems": [{"id": "fake.side", "name": "Side Door", "kind": "lock", "icon": "lock", "state": "UNLOCKED",
                           "stateLabel": "Unlocked", "urgent": false, "fix": {"command": "lock", "label": "Lock", "sensitive": false}},
                          {"id": "fake.leak", "name": "Laundry", "kind": "leak", "icon": "leak", "state": "wet",
                           "stateLabel": "Leak!", "urgent": true, "fix": null}],
             "groups": [{"kind": "lock", "label": "Lock", "text": "Unlocked", "secure": false, "icon": "lock"},
                        {"kind": "alarm", "label": "Alarm", "text": "Disarmed", "secure": null, "icon": "alarm"}],
             "ids": ["fake.side", "fake.leak", "fake.alarm"], "somethingNew": 1}
        """.trimIndent()
        val home = NewmanJson.decodeFromString(WidgetHome.serializer(), json)
        assertEquals(false, home.secure)
        assertEquals("Side Door unlocked", home.headline)
        assertEquals(listOf("fake.side"), home.fixable.map { it.id })
        assertEquals(WidgetAction("lock", "Lock", sensitive = false), home.fixable.first().fix)
        assertEquals(listOf("Unlocked", "Disarmed"), home.groups.map { it.text })
        assertNull(home.groups[1].secure)
        // Cached and read back as it was.
        assertEquals(home, WidgetTiles.decodeHome(WidgetTiles.encodeHome(home)))
        assertNull(WidgetTiles.decodeHome("not json"))
        val bare = NewmanJson.decodeFromString(WidgetHome.serializer(), """{"secure": null, "headline": "Nothing to keep secure"}""")
        assertNull(bare.secure)
        assertTrue(bare.problems.isEmpty() && bare.groups.isEmpty())
    }
}
