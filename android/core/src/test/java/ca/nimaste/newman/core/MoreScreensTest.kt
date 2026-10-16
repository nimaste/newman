package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.Automation
import ca.nimaste.newman.core.model.AutomationRun
import ca.nimaste.newman.core.model.Automations
import ca.nimaste.newman.core.model.Category
import ca.nimaste.newman.core.model.DeviceList
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.Health
import ca.nimaste.newman.core.model.Kind
import ca.nimaste.newman.core.model.KindState
import ca.nimaste.newman.core.model.Meta
import ca.nimaste.newman.core.model.NewmanJson
import kotlinx.serialization.builtins.ListSerializer
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalDateTime
import java.time.ZoneId
import java.util.Locale

/** More › Devices and More › Automations: grouping, filtering, order and wording. */
class MoreScreensTest {
    private val zone = ZoneId.of("America/Toronto")
    private val now = LocalDateTime.of(2026, 10, 16, 15, 0).atZone(zone).toInstant()
    private fun at(day: Int, h: Int, m: Int) = LocalDateTime.of(2026, 10, day, h, m).atZone(zone).toEpochSecond().toDouble()

    // -- automations ---------------------------------------------------------------------------------

    /** Shaped like GET /api/automations (the hub sends more than the phone reads). */
    private val list = NewmanJson.decodeFromString(ListSerializer(Automation.serializer()), """[
      {"id": "auto_a", "name": "Doorbell alert", "enabled": true, "trigger": {"type": "event"}, "actions": [],
       "summary": "When someone rings the Front Door doorbell, notify Chris.",
       "lastRun": {"id": "run_1", "automationId": "auto_a", "startedAt": ${at(12, 14, 53)}, "status": "done", "log": []}},
      {"id": "auto_b", "name": "Kitchen water leak", "enabled": true, "summary": "When the kitchen leaks, notify Chris.", "lastRun": null},
      {"id": "auto_c", "name": "Lock Notification", "enabled": false, "summary": "When a lock changes, notify Chris.",
       "lastRun": {"startedAt": ${at(16, 14, 54)}, "status": "sent"}},
      {"id": "auto_d", "name": "Garage door left open", "summary": "At 9 PM, if the garage is open, notify Chris.",
       "lastRun": {"startedAt": ${at(15, 21, 0)}, "status": "skipped"}}
    ]""")

    @Test fun automationsSortMostRecentFirstNeverRunLast() {
        assertEquals(listOf("auto_c", "auto_d", "auto_a", "auto_b"), Automations.sorted(list).map { it.id })
    }

    @Test fun summaryCountsOffAndToday() {
        assertEquals("4 automations · 1 off", Automations.summary(list))
        assertEquals("4 automations · 1 off · 1 ran today", Automations.summary(list, now, zone))
        assertEquals("1 automation · all on", Automations.summary(list.take(1)))
        assertEquals("0 automations", Automations.summary(emptyList()))
    }

    @Test fun lastRunWording() {
        val byId = list.associateBy { it.id }
        fun text(id: String) = Automations.lastRunText(byId.getValue(id).lastRun, now, zone, Locale.US).replace(' ', ' ')
        assertEquals("Sent today 2:54 PM", text("auto_c"))
        assertEquals("Checked yesterday 9:00 PM · nothing to do", text("auto_d"))
        assertEquals("Ran Oct 12, 2:53 PM", text("auto_a"))
        assertEquals("Hasn't run yet", text("auto_b"))
    }

    @Test fun runResultWording() {
        assertEquals("Ran Doorbell alert", Automations.runResult("Doorbell alert", AutomationRun(status = "done")))
        assertEquals("Garage: nothing to do right now", Automations.runResult("Garage", AutomationRun(status = "skipped")))
    }

    // -- devices -------------------------------------------------------------------------------------

    private val meta = Meta(
        categories = listOf(Category("security", "Security"), Category("lighting", "Lighting"), Category("climate", "Climate")),
        kinds = listOf(
            Kind("light", category = "lighting", states = listOf(KindState("on"), KindState("off")), activeState = "on"),
            Kind("lock", category = "security", states = listOf(KindState("locked"), KindState("unlocked")), secureState = "locked"),
            Kind("tablet", category = "other"),
        ),
    )
    private val devices = listOf(
        Entity("l1", "Kitchen", kind = "light", category = "lighting", state = "off"),
        Entity("l2", "TV Area", kind = "light", category = "lighting", state = "on"),
        Entity("k1", "Laundry", kind = "lock", category = "security", state = "locked"),
        Entity("k2", "Hallway Keypad", kind = "lock", category = "security", state = "locked",
            health = Health(false, listOf("Battery failed", "No AC power", "Not responding to the hub"))),
        Entity("t1", "Newman Kitchen", kind = "tablet", state = "battery"),
        Entity("x1", "Old plug", kind = "light", category = "lighting", state = "off", available = false),
    )

    @Test fun sectionsFollowTheHubsCategoriesWithAttentionFirst() {
        val s = DeviceList.sections(devices, meta)
        assertEquals(listOf("Security", "Lighting", "Other"), s.map { it.title })
        assertEquals(listOf("Hallway Keypad", "Laundry"), s[0].devices.map { it.name })
        assertEquals(listOf("Old plug", "Kitchen", "TV Area"), s[1].devices.map { it.name })
    }

    @Test fun searchAndFilters() {
        assertEquals(listOf("Kitchen", "Newman Kitchen"), DeviceList.filtered(devices, meta, " kitch", DeviceList.Filter.ALL).map { it.name })
        assertEquals(listOf("Hallway Keypad", "Old plug"), DeviceList.filtered(devices, meta, "", DeviceList.Filter.ATTENTION).map { it.name })
        assertEquals(listOf("TV Area"), DeviceList.filtered(devices, meta, "", DeviceList.Filter.ON).map { it.name })
    }

    @Test fun everyCameraButNotZones() {
        val camKind = Kind("camera", category = "cameras", hasCamera = true)
        val zoneKind = Kind("zone", category = "cameras", hasCamera = true)
        val kinds = mapOf("camera" to camKind, "zone" to zoneKind)
        val list = listOf(
            Entity("c1", "Garage", kind = "camera", still = "api/entities/c1/still.jpg"),
            Entity("z1", "Driveway", kind = "zone", still = "api/entities/z1/still.jpg",
                attrs = NewmanJson.parseToJsonElement("""{"camera": "c1"}""") as kotlinx.serialization.json.JsonObject),
            Entity("c2", "Backyard", kind = "camera", still = "api/entities/c2/still.jpg"),
            Entity("c3", "Old camera", kind = "camera"),
        )
        assertEquals(listOf("c1", "c2"), ca.nimaste.newman.core.model.HubModel.cameras(list) { kinds[it.kind] }.map { it.id })
    }

    @Test fun summaryAndStatus() {
        assertEquals("6 devices · 2 need attention", DeviceList.summary(devices))
        assertEquals("1 device", DeviceList.summary(devices.take(1)))
        assertEquals("Battery failed · No AC power · Not responding to the hub", DeviceList.statusText(devices[3], meta.kind("lock")))
    }
}
