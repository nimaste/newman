package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.AutoEdit
import ca.nimaste.newman.core.model.CameraAlert
import ca.nimaste.newman.core.model.Category
import ca.nimaste.newman.core.model.ClimateBand
import ca.nimaste.newman.core.model.Command
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HomeCameras
import ca.nimaste.newman.core.model.HomeData
import ca.nimaste.newman.core.model.HomeLogic
import ca.nimaste.newman.core.model.HomePrefs
import ca.nimaste.newman.core.model.CardPref
import ca.nimaste.newman.core.model.Kind
import ca.nimaste.newman.core.model.KindState
import ca.nimaste.newman.core.model.Meta
import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.model.TopActivity
import ca.nimaste.newman.core.people.PeopleText
import ca.nimaste.newman.core.people.Person
import ca.nimaste.newman.core.push.PushPayload
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HomeTest {

    // -- GET /api/home ----------------------------------------------------------------------------

    private val homeJson = """
        {"cards": [{"id": "cameras", "on": true, "title": "Cameras", "value": "Person", "detail": "8:36 AM", "tone": "normal", "camera": "cam.garage_side"},
                   {"id": "climate", "on": true, "title": "Climate", "value": "21.8°", "detail": "Heat · set to 22°", "tone": "warm"},
                   {"id": "appliances", "on": false, "title": "Appliances", "value": "", "detail": "", "tone": "normal", "future": 1}],
         "favourites": ["group.kitchen_under_cabinet", "lock.side"],
         "status": {"level": "danger", "summary": "Garage open",
                    "danger": [{"id": "garage", "title": "Garage door open", "detail": "For 15 minutes · since 8:52 PM", "entity": "cover.garage",
                                "action": {"command": "close", "label": "Close"}},
                               {"id": "leak", "title": "Leak", "detail": "", "entity": "leak.kitchen", "action": null}],
                    "warnings": [{"entity": "motion.hall", "issue": "battery_low", "text": "Motion Detector · Battery low"}]},
         "cameras": {"mode": "featured", "featured": "cam.front", "top": {"camera": "cam.garage", "label": "Car", "zone": null, "at": 1760900000,
                     "snapshot": null, "clip": "api/clips/x"}},
         "climate": {"thermostat": "climate.main", "rooms": [{"entity": "sensor.up", "name": "Upper Floor", "value": 22.2}], "range": [18, 26],
                     "bands": [{"name": "Cool", "below": 21.5, "color": "#9db8ff"}, {"name": "Comfortable", "color": "#f5b14c"},
                               {"name": "Warm", "above": 22.0, "color": "#ff9a4d"}]},
         "canAutomate": true, "somethingNew": {"x": 1}}
    """.trimIndent()

    @Test fun parsesHome() {
        val h = NewmanJson.decodeFromString(HomeData.serializer(), homeJson)
        assertEquals(listOf("cameras", "climate", "appliances"), h.cards.map { it.id })
        assertEquals("cam.garage_side", h.cards[0].camera)
        assertFalse(h.cards[2].on)
        assertEquals("warm", h.cards[1].tone)
        assertEquals(listOf("group.kitchen_under_cabinet", "lock.side"), h.favourites)
        assertEquals("danger", h.status.level)
        assertEquals("close", h.status.danger[0].action?.command)
        assertEquals("Close", h.status.danger[0].action?.label)
        assertNull(h.status.danger[1].action)
        assertEquals("battery_low", h.status.warnings[0].issue)
        assertEquals("featured", h.cameras.mode)
        assertEquals("Car", h.cameras.top?.label)
        assertNull(h.cameras.top?.zone)
        assertEquals(listOf(18.0, 26.0), h.climate.range)
        assertEquals(21.5, h.climate.bands[0].below!!, 0.0)
        assertTrue(h.canAutomate)
    }

    @Test fun parsesAnEmptyHome() {
        val h = NewmanJson.decodeFromString(HomeData.serializer(), "{}")
        assertTrue(h.cards.isEmpty())
        assertEquals("ok", h.status.level)
        assertEquals("latest", h.cameras.mode)
        assertFalse(h.canAutomate)
    }

    @Test fun editHomeBody() {
        val body = NewmanJson.encodeToString(HomePrefs.serializer(), HomePrefs(listOf(CardPref("lights", true), CardPref("cameras", false)), listOf("lock.side")))
        val o = NewmanJson.parseToJsonElement(body).jsonObject
        assertEquals("lights", o["cards"]!!.jsonArray[0].jsonObject["id"]!!.jsonPrimitive.content)
        assertEquals("false", o["cards"]!!.jsonArray[1].jsonObject["on"]!!.jsonPrimitive.content)
        assertEquals("lock.side", o["favourites"]!!.jsonArray[0].jsonPrimitive.content)
    }

    // -- bands and bars -----------------------------------------------------------------------------

    private val bands = listOf(ClimateBand("Cool", below = 21.5, color = "#9db8ff"), ClimateBand("Comfortable", color = "#f5b14c"),
        ClimateBand("Warm", above = 22.0, color = "#ff9a4d"))

    @Test fun bands() {
        assertEquals("Cool", HomeLogic.bandFor(21.0, bands)?.name)
        assertEquals("Comfortable", HomeLogic.bandFor(21.8, bands)?.name)
        assertEquals("Comfortable", HomeLogic.bandFor(21.5, bands)?.name) // the edge itself isn't below
        assertEquals("Comfortable", HomeLogic.bandFor(22.0, bands)?.name)
        assertEquals("Warm", HomeLogic.bandFor(22.2, bands)?.name)
        assertNull(HomeLogic.bandFor(null, bands))
        assertNull(HomeLogic.bandFor(20.0, emptyList()))
        // No middle band: the last one.
        assertEquals("Warm", HomeLogic.bandFor(21.8, listOf(bands[0], bands[2]))?.name)
    }

    @Test fun bars() {
        assertEquals(0.5f, HomeLogic.barFraction(22.0, listOf(18.0, 26.0)), 0.0001f)
        assertEquals(1f, HomeLogic.barFraction(30.0, listOf(18.0, 26.0)), 0f)
        assertEquals(0f, HomeLogic.barFraction(10.0, listOf(18.0, 26.0)), 0f)
        assertEquals(0f, HomeLogic.barFraction(null, listOf(18.0, 26.0)), 0f)
        assertEquals(0f, HomeLogic.barFraction(20.0, listOf(26.0, 18.0)), 0f)
    }

    @Test fun colours() {
        assertEquals(0xFF9DB8FFL, HomeLogic.argb("#9db8ff"))
        assertEquals(0xFFAABBCCL, HomeLogic.argb("#abc"))
        assertNull(HomeLogic.argb("blue"))
        assertNull(HomeLogic.argb(null))
    }

    @Test fun camerasTop() {
        val cams = listOf(Entity("cam.front"), Entity("cam.garage"))
        val latest = HomeCameras("latest", "cam.front", TopActivity(camera = "cam.garage", label = "Car"))
        assertEquals("Latest activity", HomeLogic.topLabel(latest))
        assertEquals("cam.garage", HomeLogic.topCamera(latest, cams)?.id)
        val featured = latest.copy(mode = "featured")
        assertEquals("Featured camera", HomeLogic.topLabel(featured))
        assertEquals("cam.front", HomeLogic.topCamera(featured, cams)?.id)
        assertEquals("cam.front", HomeLogic.topCamera(HomeCameras(), cams)?.id) // nothing yet: the first
    }

    // -- Lights: rooms and groups -----------------------------------------------------------------------

    private val on = Command("turn_on", "Turn on", confirmsTo = "on")
    private val off = Command("turn_off", "Turn off", confirmsTo = "off")
    private val toggle = Command("toggle", "Toggle")
    private val bright = Command("set_brightness", "Brightness", takesValue = true, valueType = "percent", attr = "brightness")
    private val meta = Meta(categories = listOf(Category("lighting", "Lighting", "lights"), Category("security", "Security", "security"),
        Category("climate", "Climate", "climate"), Category("other", "Other")), kinds = listOf(
        Kind("light", category = "lighting", states = listOf(KindState("on"), KindState("off")), activeState = "on", commands = listOf(on, off, toggle, bright)),
        Kind("switch", category = "lighting", states = listOf(KindState("on"), KindState("off")), activeState = "on", commands = listOf(on, off)),
        Kind("remote", category = "lighting"),
        Kind("lock", category = "security", states = listOf(KindState("locked"), KindState("unlocked")), secureState = "locked",
            commands = listOf(Command("lock", confirmsTo = "locked"), Command("unlock", confirmsTo = "unlocked", sensitive = true))),
        Kind("contact", plural = "Doors", category = "security", states = listOf(KindState("closed"), KindState("open")), secureState = "closed"),
        Kind("alarm", category = "security", states = listOf(KindState("disarmed"), KindState("home"), KindState("away")),
            commands = listOf(Command("set_mode", takesValue = true, valueType = "state"))),
        Kind("temperature", category = "climate", unit = "°C"),
    ))

    private fun members(vararg ids: String) = JsonObject(mapOf("members" to JsonArray(ids.map { JsonPrimitive(it) })))

    private val lights = listOf(
        Entity("light.kitchen", "Kitchen", kind = "light", state = "off", room = "Kitchen"),
        Entity("group.kitchen_under_cabinet", "Kitchen Under Cabinet", kind = "light", state = "on", room = "Kitchen",
            attrs = members("light.uc1", "light.uc2", "light.uc3")),
        Entity("light.uc1", "Under Cabinet 1", kind = "light", state = "on", room = "Kitchen", group = "group.kitchen_under_cabinet"),
        Entity("light.uc2", "Under Cabinet 2", kind = "light", state = "off", room = "Kitchen", group = "group.kitchen_under_cabinet"),
        Entity("light.uc3", "Under Cabinet 3", kind = "light", state = "off", room = "Kitchen", group = "group.kitchen_under_cabinet"),
        Entity("switch.porch", "Porch", kind = "switch", state = "off"),
        Entity("light.dining", "Dining Room", kind = "light", state = "off", room = "Dining Room"),
        Entity("remote.pico", "Pico", kind = "remote", room = "Kitchen", attrs = JsonObject(mapOf("events" to JsonArray(listOf(JsonPrimitive("press")))))),
        Entity("lock.side", "Side Door", kind = "lock", state = "locked", room = "Kitchen"),
    )

    @Test fun lightRoomsGroupTheirMembers() {
        val rooms = HomeLogic.lightRooms(lights, meta)
        assertEquals(listOf("Kitchen", "Dining Room", "Other"), rooms.map { it.name })
        val kitchen = rooms[0]
        assertEquals(listOf("light.kitchen", "group.kitchen_under_cabinet"), kitchen.rows.map { it.entity.id })
        assertEquals(listOf("light.uc1", "light.uc2", "light.uc3"), kitchen.rows[1].members.map { it.id })
        assertTrue(kitchen.rows[0].members.isEmpty())
        // No room: "Other", last; remotes and locks aren't lights.
        assertEquals(listOf("switch.porch"), rooms[2].rows.map { it.entity.id })
        assertEquals(1, HomeLogic.onCount(rooms, meta)) // the group counts once
    }

    @Test fun aMemberOfAMissingGroupShowsOnItsOwn() {
        val rooms = HomeLogic.lightRooms(listOf(Entity("light.a", "A", kind = "light", state = "off", room = "Hall", group = "group.gone")), meta)
        assertEquals(listOf("light.a"), rooms.single().rows.map { it.entity.id })
    }

    @Test fun groupMembersFromTheGroupsListToo() {
        // A member that doesn't name its group is still hidden under it.
        val list = listOf(Entity("group.g", "G", kind = "light", state = "off", room = "Hall", attrs = members("light.x")),
            Entity("light.x", "X", kind = "light", state = "off", room = "Hall"))
        val row = HomeLogic.lightRooms(list, meta).single().rows.single()
        assertEquals("group.g", row.entity.id)
        assertEquals(listOf("light.x"), row.members.map { it.id })
    }

    @Test fun onOffFromTheKind() {
        val duo = HomeLogic.onOff(meta.kind("light"))!!
        assertEquals("turn_on", duo.on.id)
        assertEquals("turn_off", duo.off.id)
        assertNull(HomeLogic.onOff(meta.kind("remote")))
    }

    @Test fun roomLines() {
        val rooms = HomeLogic.lightRooms(lights, meta)
        assertEquals("Under cabinet on", HomeLogic.roomLine(rooms[0], meta))
        assertEquals("Off", HomeLogic.roomLine(rooms[1], meta))
        assertEquals("Kitchen", HomeLogic.shortName("Kitchen", "Kitchen"))
        assertEquals("Under cabinet", HomeLogic.shortName("Kitchen Under Cabinet", "Kitchen"))
        assertEquals("Porch", HomeLogic.shortName("Porch", "Kitchen"))
    }

    @Test fun securityParts() {
        val list = lights + listOf(Entity("contact.front", "Front", kind = "contact", state = "closed"),
            Entity("alarm.ring", "Ring", kind = "alarm", state = "disarmed", category = "security"))
        val p = HomeLogic.securityParts(list, meta)
        assertEquals(listOf("alarm.ring"), p.alarms.map { it.id })
        assertEquals(listOf("lock.side"), p.controls.map { it.id })
        assertEquals("Doors", p.sensors.single().first.plural)
        assertEquals(listOf("contact.front"), p.sensors.single().second.map { it.id })
    }

    @Test fun climateSensors() {
        val list = listOf(Entity("sensor.up", "Up", kind = "temperature", state = "22.2"), Entity("sensor.bad", "Bad", kind = "temperature", state = "unavailable"),
            Entity("light.kitchen", "Kitchen", kind = "light", state = "off"))
        assertEquals(listOf("sensor.up"), HomeLogic.climateSensors(list, meta).map { it.id })
    }

    // -- the automation editor keeps what it doesn't know -------------------------------------------------

    private val automation = NewmanJson.parseToJsonElement("""
        {"id": "a1", "name": "Under cabinet", "enabled": true, "cooldownMinutes": null,
         "trigger": {"match": "any", "items": [
            {"type": "time", "time": "06:20", "days": [0, 1, 2, 3, 4, 5, 6]},
            {"type": "geofence", "zone": "home", "who": ["p1"]}]},
         "actions": [{"entities": ["group.kitchen_under_cabinet"], "command": "set_brightness", "value": 1, "for": {"amount": 105, "unit": "minutes"}},
                     {"type": "wall", "sound": "chime"}],
         "condition": {"devices": [{"entities": ["lock.side"], "state": "locked", "match": "any"}], "entities": ["lock.side"], "mode": "any",
                       "between": {"from": "22:00", "to": "06:00"}},
         "notify": {"enabled": true, "title": "Hi", "buttons": ["open"]},
         "recipients": ["p1"], "wall": {"tablets": ["t1"]}, "liveStatus": {"x": 1}}
    """.trimIndent()).jsonObject

    @Test fun editsKeepEverythingElse() {
        var a = automation
        a = AutoEdit.withName(a, "Morning lights")
        a = AutoEdit.withEnabled(a, false)
        val items = AutoEdit.items(a)
        assertTrue(AutoEdit.isKnownItem(items[0]))
        assertFalse(AutoEdit.isKnownItem(items[1]))
        a = AutoEdit.withItems(a, listOf(AutoEdit.toggleDay(items[0], 6), items[1]))
        a = AutoEdit.withCooldown(a, 15)
        a = AutoEdit.withActions(a, AutoEdit.actions(a) + AutoEdit.action(listOf("light.dining"), "turn_on"))
        // What was changed:
        assertEquals("Morning lights", AutoEdit.name(a))
        assertFalse(AutoEdit.enabled(a))
        assertEquals(listOf(0, 1, 2, 3, 4, 5), AutoEdit.days(AutoEdit.items(a)[0]))
        assertEquals(15, AutoEdit.cooldown(a))
        assertEquals(3, AutoEdit.actions(a).size)
        // ...and everything else exactly as it came: other keys, the unknown trigger and action, the trigger's match.
        for (k in listOf("id", "notify", "recipients", "wall", "liveStatus", "condition")) assertEquals(k, automation[k], a[k])
        assertEquals(automation["trigger"]!!.jsonObject["items"]!!.jsonArray[1], AutoEdit.items(a)[1])
        assertEquals("any", a["trigger"]!!.jsonObject["match"]!!.jsonPrimitive.content)
        assertEquals(automation["actions"]!!.jsonArray[1], AutoEdit.actions(a)[1])
        assertFalse(AutoEdit.isKnownAction(AutoEdit.actions(a)[1]))
        // A round trip through the hub's JSON changes nothing more.
        val back = NewmanJson.parseToJsonElement(a.toString()).jsonObject
        assertEquals(a, back)
        assertTrue(AutoEdit.dirty(automation, a))
        assertFalse(AutoEdit.dirty(automation, NewmanJson.parseToJsonElement(automation.toString()).jsonObject))
    }

    @Test fun putItBackAfter() {
        val act = AutoEdit.actions(automation)[0]
        assertEquals(105, AutoEdit.forMinutes(act))
        val older = buildJsonObject { put("entities", JsonArray(listOf(JsonPrimitive("x")))); put("command", "set_brightness"); put("forMinutes", 30) }
        assertEquals(30, AutoEdit.forMinutes(older))
        val set = AutoEdit.withForMinutes(older, 45)
        assertNull(set["forMinutes"])
        assertEquals(45.0, set["for"]!!.jsonObject["amount"]!!.jsonPrimitive.content.toDouble(), 0.0)
        assertEquals("minutes", set["for"]!!.jsonObject["unit"]!!.jsonPrimitive.content)
        val cleared = AutoEdit.withForMinutes(set, null)
        assertNull(cleared["for"])
        assertNull(AutoEdit.forMinutes(cleared))
        assertEquals(2, AutoEdit.forMinutes(buildJsonObject { put("for", buildJsonObject { put("amount", 2); put("unit", "hours") }) })?.div(60))
    }

    @Test fun cooldowns() {
        assertNull(AutoEdit.cooldown(automation))
        val a = AutoEdit.withCooldown(AutoEdit.withCooldown(automation, 5), null)
        assertEquals(JsonNull, a["cooldownMinutes"])
        assertEquals(0, AutoEdit.cooldown(AutoEdit.withCooldown(automation, 0)))
        assertEquals("Default", AutoEdit.cooldownText(null))
        assertEquals("No wait", AutoEdit.cooldownText(0))
        assertEquals("1 minute", AutoEdit.cooldownText(1))
        assertEquals("15 minutes", AutoEdit.cooldownText(15))
        assertEquals("1 hour", AutoEdit.cooldownText(60))
    }

    @Test fun conditionRowsReplaceTheMirror() {
        val a = AutoEdit.withConditionRows(automation, AutoEdit.conditionRows(automation) + AutoEdit.conditionRow("contact.front", "closed"))
        val c = a["condition"]!!.jsonObject
        assertNull(c["entities"])
        assertNull(c["mode"])
        assertEquals(automation["condition"]!!.jsonObject["between"], c["between"])
        assertEquals(2, AutoEdit.conditionRows(a).size)
        assertTrue(AutoEdit.hasOtherLimits(a))
    }

    @Test fun aNewOne() {
        val a = AutoEdit.blank()
        assertEquals("", AutoEdit.name(a))
        assertEquals("07:00", AutoEdit.items(a).single()["time"]!!.jsonPrimitive.content)
        assertEquals(AutoEdit.ALL_DAYS, AutoEdit.days(AutoEdit.items(a).single()))
        assertEquals("false", a["notify"]!!.jsonObject["enabled"]!!.jsonPrimitive.content)
        assertEquals("21:05", AutoEdit.hhmm(21, 5))
        assertEquals(6 to 20, AutoEdit.parseTime("06:20"))
        assertEquals(7 to 0, AutoEdit.parseTime(null))
    }

    // -- notifications, people, camera alerts --------------------------------------------------------------

    @Test fun pushAlertIdAndQuiet() {
        val p = PushPayload.parse(mapOf("title" to "Person", "alertId" to "ev-42", "quiet" to "1"))
        assertEquals("ev-42", p.alertId)
        assertTrue(p.quiet)
        val q = PushPayload.parse(mapOf("title" to "Person"))
        assertNull(q.alertId)
        assertFalse(q.quiet)
    }

    @Test fun peopleSummary() {
        assertEquals("Chris is home", PeopleText.summary(listOf(Person("1", "Chris", sharing = true, home = true))))
        assertEquals("2 people home", PeopleText.summary(listOf(Person("1", "A", home = true), Person("2", "B", home = true))))
        assertEquals("No one is home", PeopleText.summary(listOf(Person("1", "A"), Person("2", "B"))))
        assertEquals("B is home", PeopleText.summary(listOf(Person("1", "A"), Person("2", "B", home = true))))
    }

    @Test fun moreLinesAndSizes() {
        val list = listOf(ca.nimaste.newman.core.model.Automation("a"), ca.nimaste.newman.core.model.Automation("b", enabled = false))
        assertEquals("2 · 1 off", ca.nimaste.newman.core.model.Automations.moreLine(list))
        assertEquals("1 · all on", ca.nimaste.newman.core.model.Automations.moreLine(list.take(1)))
        assertEquals(30, HomeLogic.valueSize("21.8°"))
        assertEquals(22, HomeLogic.valueSize("All locked"))
        assertEquals(18, HomeLogic.valueSize("Nothing today"))
        assertEquals("api/clips/frigate/r1", ca.nimaste.newman.core.model.CameraClip.clipPath("api/clips/frigate/r1"))
        assertEquals("api/clips/frigate/r%3A1", ca.nimaste.newman.core.model.CameraClip.clipPath("frigate:r:1"))
    }

    @Test fun cameraAlertShapes() {
        val old = CameraAlert("r1", objects = listOf("person"), zones = listOf("garage_walkway"), start = 10.0, snapshotUrl = "s.jpg")
        assertEquals("Person", old.what)
        assertEquals("Garage walkway", old.where)
        assertEquals(10.0, old.time!!, 0.0)
        assertEquals("s.jpg", old.picture)
        val flat = NewmanJson.decodeFromString(CameraAlert.serializer(), """{"id": "a", "at": 20, "label": "Car", "zone": "Driveway", "snapshot": "x.jpg", "clip": "c"}""")
        assertEquals("Car", flat.what)
        assertEquals("Driveway", flat.where)
        assertEquals(20.0, flat.time!!, 0.0)
        assertEquals("x.jpg", flat.picture)
    }
}
