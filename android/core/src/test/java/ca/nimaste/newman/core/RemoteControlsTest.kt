package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.Kind
import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.model.RemoteControls
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** A remote's controls (entity.controls): read when the hub sends them, absent otherwise. */
class RemoteControlsTest {
    private fun entity(json: String) = NewmanJson.decodeFromString(Entity.serializer(), json)
    private val light = NewmanJson.decodeFromString(Kind.serializer(), """{"id": "light", "name": "Light", "activeState": "on",
        "states": [{"id": "on", "label": "On"}, {"id": "off", "label": "Off"}]}""")

    @Test fun controlsAreReadWhenSent() {
        val e = entity("""{"id": "lutron.remote_14", "kind": "remote", "controls": {"lights": ["mqtt.a", "mqtt.b", "mqtt.c"],
            "onLevel": 60, "hold": true, "rows": [{"key": "button_1", "label": "Tap top", "does": "On at 60%", "custom": false},
            {"key": "button_3_long", "label": "Hold bottom", "does": "Kitchen: turn off", "custom": true}], "later": 1}}""")
        val c = e.controls!!
        assertEquals(listOf("mqtt.a", "mqtt.b", "mqtt.c"), c.lights)
        assertEquals(60, c.onLevel)
        assertEquals("Controls · 3 lights", c.header)
        assertEquals(listOf("Tap top" to "On at 60%", "Hold bottom" to "Kitchen: turn off"), c.rows.map { it.label to it.does })
        assertEquals(listOf(false, true), c.rows.map { it.custom })
    }

    @Test fun noControlsWhenAbsentOrNull() {
        assertNull(entity("""{"id": "lutron.remote_7", "kind": "remote"}""").controls)
        assertNull(entity("""{"id": "lutron.remote_7", "kind": "remote", "controls": null}""").controls)
    }

    @Test fun headerWithoutLightsAndOneLight() {
        assertEquals("Controls", RemoteControls().header)
        assertEquals("Controls · 1 light", RemoteControls(lights = listOf("x")).header)
    }

    @Test fun eachLightsLevel() {
        assertEquals("60%", RemoteControls.lightStatus(entity("""{"id": "l", "state": "on", "attrs": {"brightness": 60.4}}"""), light))
        assertEquals("On", RemoteControls.lightStatus(entity("""{"id": "l", "state": "on", "attrs": {"brightness": 60, "dimmable": false}}"""), light))
        assertEquals("On", RemoteControls.lightStatus(entity("""{"id": "l", "state": "on"}"""), light))
        assertEquals("Off", RemoteControls.lightStatus(entity("""{"id": "l", "state": "off", "attrs": {"brightness": 60}}"""), light))
        assertEquals("Offline", RemoteControls.lightStatus(entity("""{"id": "l", "state": "on", "available": false}"""), light))
        assertEquals("Offline", RemoteControls.lightStatus(null, light))
    }
}
