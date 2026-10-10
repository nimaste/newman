package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.jsonOf
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The plain-words state texts and tile helpers match web/src/model.js on real devices. */
class HubModelTest {
    private val meta = Fixtures.meta

    @Test
    fun stateTexts() {
        val t = Fixtures.entity("thermostat")
        assertEquals("Cool · 22.6 °C → 22.5", HubModel.entityStateText(t, meta.kind(t.kind)))
        val light = Fixtures.entity("light").copy(state = "on", stateLabel = "On",
            attrs = JsonObject(mapOf("brightness" to JsonPrimitive(45), "dimmable" to JsonPrimitive(true))))
        assertEquals("On · 45%", HubModel.entityStateText(light, meta.kind("light")))
        assertEquals("Offline", HubModel.entityStateText(light.copy(available = false), meta.kind("light")))
        val garage = Fixtures.entity("garage").copy(state = "opening", stateLabel = "Opening")
        assertEquals("Opening…", HubModel.entityStateText(garage, meta.kind("garage")))
    }

    @Test
    fun securityHelpers() {
        val lock = Fixtures.entity("lock")
        val k = meta.kind("lock")
        val unlocked = lock.copy(state = "UNLOCKED", stateLabel = "Unlocked")
        assertTrue(HubModel.notSecure(unlocked, k))
        assertEquals("unlock", HubModel.nextAction(lock.copy(state = "LOCKED"), k)!!.id)
        assertEquals("lock", HubModel.nextAction(unlocked, k)!!.id)
        assertEquals(false to "${lock.name} unlocked", HubModel.securitySummary(listOf(unlocked), meta))
        assertEquals(true to "All secure", HubModel.securitySummary(listOf(lock.copy(state = "LOCKED")), meta))
        assertNull(HubModel.securitySummary(listOf(Fixtures.entity("light")), meta))
    }

    @Test
    fun tapTogglesAnOnOffPair() {
        val light = Fixtures.entity("light").copy(state = "off")
        val k = meta.kind("light")
        assertEquals("turn_on", HubModel.tapAction(light, k)!!.id)
        assertEquals("turn_off", HubModel.tapAction(light.copy(state = "on"), k)!!.id)
        assertTrue(HubModel.extraActions(k, HubModel.tapAction(light, k)).isEmpty())
    }

    @Test
    fun choicesAndValueTexts() {
        val vac = Fixtures.entity("vacuum")
        val k = meta.kind("vacuum")
        val cmd = HubModel.choiceCommands(k).single()
        val items = HubModel.choiceItems(vac, cmd)
        assertTrue(items.size >= 2)
        val two = JsonArray(items.take(2).map { it.id })
        assertEquals("${items[0].name} and ${items[1].name}", HubModel.valueText(cmd, k, two, vac))
        val setTemp = meta.kind("thermostat")!!.command("set_temperature")
        assertEquals("21.5 °C", HubModel.valueText(setTemp, meta.kind("thermostat"), jsonOf(21.5)))
        val mode = meta.kind("thermostat")!!.command("set_mode")
        assertEquals("Heat", HubModel.valueText(mode, meta.kind("thermostat"), jsonOf("heat")))
    }

    @Test
    fun propertiesFormatLikeTheWeb() {
        val t = Fixtures.entity("thermostat")
        val hum = t.properties.first { it.id == "humidity" }
        assertEquals("50%", HubModel.formatProp(hum, hum.value))
        val act = t.properties.first { it.id == "action" }
        assertEquals("Idle", HubModel.formatProp(act, act.value))
        val temp = t.properties.first { it.id == "temperature" }
        assertEquals("22.6 °C", HubModel.formatProp(temp, temp.value))
    }

    @Test
    fun attentionPutsUrgentAlarmsFirst() {
        val leak = meta.kind("leak")!!
        val wet = Fixtures.entity("lock").copy(id = "leak.1", name = "Basement", kind = leak.id, state = "wet")
        val sick = Fixtures.entity("light").copy(health = ca.nimaste.newman.core.model.Health(false, listOf("Offline")))
        val list = HubModel.attention(listOf(sick, wet), meta)
        assertEquals(listOf("leak.1", sick.id), list.map { it.entity.id })
        assertTrue(list.first().alarm)
        assertEquals("Leak!", list.first().issues.first())
    }

    @Test
    fun dashboardNamesOverrideDeviceNames() {
        val map = Fixtures.entities.associateBy { it.id }
        val id = Fixtures.entity("garage").id
        val named = HubModel.withNames(map, mapOf(id to "  Garage  ", "missing" to ""))
        assertEquals("Garage", named[id]!!.name)
        assertEquals(map.size, named.size)
    }
}
