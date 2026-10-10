package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.AuthState
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.model.asNumberOrNull
import ca.nimaste.newman.core.model.number
import ca.nimaste.newman.core.model.strings
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The real hub's JSON decodes into the models, and the models agree with what the hub says. */
class HubParsingTest {

    @Test
    fun metaDecodesEveryKindWithCommandsAndValueTypes() {
        val meta = Fixtures.meta
        assertTrue(meta.kinds.size >= 20)
        assertTrue(meta.categories.any { it.id == "security" })
        val lock = meta.kind("lock")!!
        assertEquals("UNLOCKED", lock.activeState)
        assertEquals("LOCKED", lock.secureState)
        assertTrue(lock.command("unlock")!!.sensitive)
        assertFalse(lock.command("lock")!!.sensitive)
        // Every valueType the hub uses is one the apps know.
        val types = meta.kinds.flatMap { k -> k.commands.map { it.valueType } }.toSet()
        assertTrue(types.containsAll(setOf("percent", "number", "state", "choices", "text")))
        assertTrue((types - setOf("", "percent", "number", "state", "choices", "text")).isEmpty())
        val setTemp = meta.kinds.flatMap { it.commands }.first { it.valueType == "number" }
        assertNotNull(setTemp.min)
        assertNotNull(setTemp.max)
        assertNotNull(setTemp.step)
    }

    @Test
    fun entitiesDecodeAndAllHaveKnownKinds() {
        val list = Fixtures.entities
        assertTrue(list.size > 20)
        val raw = NewmanJson.parseToJsonElement(Fixtures.text("hub/entities.json")).jsonArray
        assertEquals(raw.size, list.size)
        list.forEach { e -> assertNotNull("kind ${e.kind} of ${e.id} is in meta", Fixtures.meta.kind(e.kind)) }
        // Labels the hub computed match the kind's own state labels.
        list.filter { Fixtures.meta.kind(it.kind)!!.states.isNotEmpty() && it.state != null }.forEach { e ->
            val k = Fixtures.meta.kind(e.kind)
            val label = HubModel.stateLabelOf(k, e.state)
            if (k!!.states.any { it.id == e.state }) assertEquals(e.stateLabel, label)
        }
    }

    @Test
    fun entityPropertiesCarryValues() {
        val t = Fixtures.entity("thermostat")
        val temp = t.properties.first { it.id == "temperature" }
        assertEquals("°C", temp.unit)
        assertEquals(t.attrs.number("currentTemp"), temp.value.asNumberOrNull)
        val cams = Fixtures.entities.filter { it.still != null }
        assertTrue(cams.isNotEmpty())
        assertTrue(cams.all { it.still!!.startsWith("api/entities/") && it.still!!.endsWith("/still.jpg") })
    }

    @Test
    fun dashboardsDecodeWithPagesTilesAndConfig() {
        val d = Fixtures.dashboards.first()
        assertTrue(d.pages.isNotEmpty())
        val tiles = d.pages.flatMap { it.tiles }
        val known = NewmanJson.parseToJsonElement(Fixtures.text("hub/tiles.json")).jsonObject["types"]!!.jsonArray
            .map { it.jsonObject["type"]!!.jsonPrimitive.content }.toSet()
        assertTrue(tiles.all { it.type in known })
        val cam = tiles.first { it.type == "camera" }
        assertTrue(cam.config.strings("entities").isNotEmpty())
        // Every device a tile names exists on the hub.
        val ids = Fixtures.entities.map { it.id }.toSet()
        tiles.flatMap { it.config.strings("entities") }.forEach { assertTrue("$it exists", it in ids) }
    }

    @Test
    fun eventsAlertsCategoriesAndAuthStateDecode() {
        assertTrue(Fixtures.events.isNotEmpty())
        assertTrue(Fixtures.events.all { it.ts > 0 && it.kind.isNotEmpty() })
        val a = Fixtures.alerts.first()
        assertTrue(a.thumbUrl!!.startsWith("api/"))
        assertNotNull(a.entityId)
        assertTrue(Fixtures.categories.all { it.id.startsWith("c_") && it.buttons.isNotEmpty() })
        val auth = NewmanJson.decodeFromString(AuthState.serializer(), Fixtures.text("hub/auth_state.json"))
        assertEquals("home", auth.where)
        assertFalse(auth.signedIn)
        assertNull(auth.user)
    }

    @Test
    fun numericStatesDecodeAsText() {
        val e = NewmanJson.decodeFromString(
            ca.nimaste.newman.core.model.Entity.serializer(),
            """{"id":"x","kind":"temperature","state":21.5,"attrs":{}}""",
        )
        assertEquals("21.5", e.state)
        assertEquals(JsonObject(emptyMap()), e.attrs)
    }
}
