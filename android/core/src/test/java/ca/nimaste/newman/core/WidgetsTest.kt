package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.widgets.WidgetAction
import ca.nimaste.newman.core.widgets.WidgetChoice
import ca.nimaste.newman.core.widgets.WidgetDevice
import ca.nimaste.newman.core.widgets.WidgetDevicesResponse
import ca.nimaste.newman.core.widgets.WidgetGrouping
import ca.nimaste.newman.core.widgets.WidgetPicker
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
    }
}
