package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.Category
import ca.nimaste.newman.core.model.Command
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HomeCard
import ca.nimaste.newman.core.model.HomeData
import ca.nimaste.newman.core.model.HomeLogic
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.Kind
import ca.nimaste.newman.core.model.KindState
import ca.nimaste.newman.core.model.Meta
import ca.nimaste.newman.core.model.NewmanJson
import kotlinx.serialization.builtins.ListSerializer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Round 2: every category is data — cards, screens, sections and icons come from the hub's metadata. */
class CategoriesTest {

    private val meta = Meta(
        categories = listOf(Category("lighting", "Lighting", "lights"), Category("security", "Security", "security"),
            Category("garage", "Garage", "security"), Category("climate", "Climate", "climate"),
            Category("appliances", "Appliances"), Category("cat.pool", "Pool", builtIn = false)),
        kinds = listOf(
            Kind("light", category = "lighting", states = listOf(KindState("on"), KindState("off")), activeState = "on",
                commands = listOf(Command("turn_on", confirmsTo = "on"), Command("turn_off", confirmsTo = "off"))),
            Kind("switch", category = "appliances", states = listOf(KindState("on"), KindState("off")), activeState = "on",
                commands = listOf(Command("turn_on", confirmsTo = "on"), Command("turn_off", confirmsTo = "off"))),
            Kind("lock", plural = "Locks", category = "security", states = listOf(KindState("locked"), KindState("unlocked")), secureState = "locked",
                commands = listOf(Command("lock", confirmsTo = "locked"), Command("unlock", confirmsTo = "unlocked"))),
            Kind("garage", plural = "Garage doors", category = "garage", states = listOf(KindState("closed"), KindState("open")), secureState = "closed",
                commands = listOf(Command("open", confirmsTo = "open"), Command("close", confirmsTo = "closed"))),
            Kind("contact", plural = "Doors", category = "security", states = listOf(KindState("closed"), KindState("open")), secureState = "closed"),
            Kind("leak", plural = "Leak sensors", category = "security", states = listOf(KindState("dry"), KindState("wet")), secureState = "dry"),
            Kind("temperature", category = "climate", unit = "°C"),
        ),
    )

    @Test fun decodesRoundTwoHome() {
        val h = NewmanJson.decodeFromString(HomeData.serializer(), """
            {"cards": [{"id": "lighting", "on": true, "title": "Lights", "category": "lighting", "screen": "lights", "value": "1 on"},
                       {"id": "cat.pool", "on": false, "new": true, "title": "Pool", "category": "cat.pool", "screen": null},
                       {"id": "today", "on": true, "title": "Today", "category": null, "screen": "today"}],
             "canEditHome": false, "layout": "household"}
        """.trimIndent())
        assertEquals(listOf("lights", null, "today"), h.cards.map { it.screen })
        assertTrue(h.cards[1].new)
        assertEquals("cat.pool", h.cards[1].category)
        assertFalse(h.canEditHome)
        assertEquals("household", h.layout)
        // A hub from before round 2 lets people edit.
        assertTrue(NewmanJson.decodeFromString(HomeData.serializer(), "{}").canEditHome)
    }

    @Test fun decodesCategories() {
        val list = NewmanJson.decodeFromString(ListSerializer(Category.serializer()), """
            [{"id": "garage", "name": "Garage", "builtIn": true, "screen": "security", "count": 1},
             {"id": "cat.pool", "name": "Pool", "builtIn": false, "screen": null, "count": 2, "extra": 1}]
        """.trimIndent())
        assertEquals("security", list[0].screen)
        assertFalse(list[1].builtIn)
        assertEquals(2, list[1].count)
        assertEquals("security", meta.copy(categories = list).screenOf("garage"))
    }

    @Test fun anOverriddenCategoryMovesTheDevice() {
        val porch = Entity("switch.porch", "Porch", kind = "switch", state = "off")
        assertFalse(HomeLogic.isLight(porch, meta))
        assertEquals("appliances", HomeLogic.categoryOf(porch, meta))
        val moved = porch.copy(category = "lighting")
        assertTrue(HomeLogic.isLight(moved, meta))
        val bulb = Entity("light.k", "K", kind = "light", state = "on", category = "cat.pool")
        assertFalse(HomeLogic.isLight(bulb, meta))
        assertEquals(listOf("light.k"), HomeLogic.categoryDevices(listOf(bulb, porch), meta, "cat.pool").map { it.id })
        val t = Entity("sensor.t", "T", kind = "temperature", state = "21")
        assertEquals(listOf("sensor.t"), HomeLogic.climateSensors(listOf(t, t.copy(id = "sensor.u", category = "appliances")), meta).map { it.id })
    }

    @Test fun securityIsEveryKindInASecurityScreenCategory() {
        val list = listOf(
            Entity("lock.side", "Side", kind = "lock", state = "locked"),
            Entity("garage.main", "Garage", kind = "garage", state = "closed"),
            Entity("contact.front", "Front", kind = "contact", state = "open"),
            Entity("leak.kitchen", "Kitchen", kind = "leak", state = "dry"),
            Entity("contact.shed", "Shed", kind = "contact", state = "closed", category = "appliances"),
        )
        val p = HomeLogic.securityParts(list, meta)
        assertEquals(listOf("Locks", "Garage doors"), HomeLogic.controlGroups(p, meta).map { it.first.plural })
        assertEquals(setOf("Doors", "Leak sensors"), p.sensors.map { it.first.plural }.toSet())
        assertFalse(p.all.any { it.id == "contact.shed" })
    }

    @Test fun deviceIcons() {
        val light = meta.kind("light")
        val e = Entity("light.k", "K", kind = "light")
        assertEquals(HubModel.kindIcon(light), HubModel.deviceIcon(e, light))
        assertEquals(HubModel.kindIcon(meta.kind("lock")), HubModel.deviceIcon(e.copy(icon = "lock"), light))
        assertEquals("droplet", HubModel.deviceIcon(e.copy(icon = "pool"), light))
        assertEquals("power", HubModel.iconName("plug"))
        assertEquals(HubModel.kindIcon(light), HubModel.deviceIcon(e.copy(icon = " "), light))
        assertEquals("dot", HubModel.deviceIcon(null, null))
    }

    @Test fun cardIcons() {
        val list = listOf(Entity("a", "A", kind = "switch", category = "cat.pool", icon = "pump"),
            Entity("b", "B", kind = "switch", category = "cat.pool", icon = "pool"),
            Entity("c", "C", kind = "switch", category = "cat.pool", icon = "heater"))
        assertEquals("droplet", HomeLogic.categoryIcon(list, meta, "cat.pool"))
        assertEquals("droplet", HomeLogic.cardIcon(HomeCard(id = "cat.pool", category = "cat.pool"), list, meta))
        assertEquals("bulb", HomeLogic.cardIcon(HomeCard(id = "lighting", category = "lighting", screen = "lights"), list, meta))
        assertEquals("user", HomeLogic.cardIcon(HomeCard(id = "people", screen = "people"), list, meta))
        assertEquals("grid", HomeLogic.cardIcon(HomeCard(id = "cat.empty", category = "cat.empty"), list, meta))
        assertNull(meta.screenOf("cat.pool"))
    }
}
