package ca.nimaste.newman.core

import ca.nimaste.newman.core.live.LiveMessage
import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.people.LocatePush
import ca.nimaste.newman.core.people.LocationReport
import ca.nimaste.newman.core.people.LocationSettings
import ca.nimaste.newman.core.people.PeopleList
import ca.nimaste.newman.core.people.PeopleText
import ca.nimaste.newman.core.people.Person
import ca.nimaste.newman.core.people.PersonLook
import ca.nimaste.newman.core.people.PersonRefresh
import ca.nimaste.newman.core.people.ProfileUpdate
import ca.nimaste.newman.core.people.Place
import ca.nimaste.newman.core.people.ReportReply
import ca.nimaste.newman.core.people.ReportSchedule
import ca.nimaste.newman.core.people.SharingSwitch
import kotlinx.serialization.builtins.ListSerializer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PeopleTest {
    private val now = 1_790_000_000.0

    @Test
    fun parsesThePeopleListAndTheLiveMessage() {
        val json = """[
            {"id":"p1","name":"Chris","sharing":true,"home":true,"place":null,"distanceM":null,"lat":43.6,"lon":-79.4,"accuracyM":12,"at":1790000000.5,"color":"#3ccfb4","photo":"/api/people/p1/photo?v=ab12"},
            {"id":"p2","name":"Dante","sharing":true,"home":false,"place":"School","distanceM":2100,"lat":43.61,"lon":-79.41,"accuracyM":30,"at":1789999700,"live":true,"color":"#FF8A6B","photo":null},
            {"id":"p3","name":"Massimo","sharing":false,"home":false,"place":null,"distanceM":null,"lat":null,"lon":null,"accuracyM":null,"at":null}
        ]"""
        val list = NewmanJson.decodeFromString(ListSerializer(Person.serializer()), json)
        assertEquals(3, list.size)
        assertEquals("School", list[1].place)
        assertTrue(list[0].hasFix)
        assertFalse(list[2].hasFix)
        val msg = LiveMessage.parse("""{"type":"person","person":{"id":"p2","name":"Dante","sharing":true,"home":true,"at":1790000100}}""")
        assertTrue(msg is LiveMessage.PersonChanged)
        val updated = PeopleList.upsert(list, (msg as LiveMessage.PersonChanged).person)
        assertEquals(listOf("p1", "p2", "p3"), updated.map { it.id })
        assertTrue(updated[1].home)
        assertEquals(4, PeopleList.upsert(list, Person("p4", "Nicholas")).size)
    }

    @Test
    fun distancesReadNaturally() {
        assertEquals("850 m", PeopleText.distance(846.0))
        assertEquals("10 m", PeopleText.distance(3.0))
        assertEquals("1 km", PeopleText.distance(999.9))
        assertEquals("3.2 km", PeopleText.distance(3_240.0))
        assertEquals("4 km", PeopleText.distance(3_990.0))
        assertEquals("9.9 km", PeopleText.distance(9_940.0))
        assertEquals("12 km", PeopleText.distance(12_400.0))
        assertEquals("250 km", PeopleText.distance(250_000.0))
    }

    @Test
    fun whereSaysHomeThePlaceOrHowFar() {
        assertEquals("Home", PeopleText.where(Person("a", sharing = true, home = true, place = "School")))
        assertEquals("School", PeopleText.where(Person("a", sharing = true, place = "School", distanceM = 2000.0)))
        assertEquals("3.2 km away", PeopleText.where(Person("a", sharing = true, distanceM = 3200.0)))
        assertEquals("Not sharing location", PeopleText.where(Person("a", sharing = false, home = true)))
        assertEquals("No location yet", PeopleText.where(Person("a", sharing = true)))
    }

    @Test
    fun updatedAgo() {
        assertEquals("updated just now", PeopleText.updated(now - 20, now))
        assertEquals("updated 4 min ago", PeopleText.updated(now - 4 * 60 - 30, now))
        assertEquals("updated 2 h ago", PeopleText.updated(now - 2 * 3600 - 100, now))
        assertEquals("updated yesterday", PeopleText.updated(now - 30 * 3600, now))
        assertEquals("updated 3 days ago", PeopleText.updated(now - 3 * 86400 - 5, now))
        assertEquals("updated just now", PeopleText.updated(now + 30, now)) // a fix from a fast clock
        assertNull(PeopleText.updated(null, now))
        val row = PeopleText.row(Person("p", "Marlina", sharing = true, distanceM = 3200.0, lat = 1.0, lon = 2.0, at = now - 240), now)
        assertEquals("3.2 km away", row.where)
        assertEquals("updated 4 min ago", row.updated)
        assertEquals("3.2 km away · updated 4 min ago", PeopleText.mapLine(Person("p", "Marlina", sharing = true, distanceM = 3200.0, at = now - 240), now))
        assertNull(PeopleText.row(Person("p", "Massimo", sharing = false, at = now), now).updated)
        assertEquals("M", PeopleText.initial(" massimo"))
    }

    @Test
    fun sharingSwitchFollowsTheHub() {
        assertFalse(SharingSwitch.state(null).visible) // older hub: 404
        assertFalse(SharingSwitch.state(LocationSettings(mode = "off")).visible)
        val choiceOff = SharingSwitch.state(LocationSettings(mode = "choice", enabled = false))
        assertTrue(choiceOff.visible && !choiceOff.on && !choiceOff.locked)
        val choiceOn = SharingSwitch.state(LocationSettings(mode = "choice", enabled = true))
        assertTrue(choiceOn.on && !choiceOn.locked)
        val required = SharingSwitch.state(LocationSettings(mode = "required", enabled = true, requiredBy = "Chris"))
        assertTrue(required.visible && required.on && required.locked)
        assertEquals("Required by Chris", required.note)
        assertEquals("Required by your household", SharingSwitch.state(LocationSettings(mode = "required")).note)
        assertTrue(SharingSwitch.permissionReason(LocationSettings(mode = "required")).startsWith("Your household requires location sharing"))
        assertTrue(LocationSettings(mode = "required", enabled = false).sharing)
        assertFalse(LocationSettings(mode = "choice", enabled = false).sharing)
        assertFalse(LocationSettings(mode = "off", enabled = true).sharing)
        val parsed = NewmanJson.decodeFromString(LocationSettings.serializer(),
            """{"mode":"required","enabled":true,"requiredBy":"Chris","everySeconds":300,"live":false}""")
        assertEquals(300, parsed.everySeconds)
    }

    @Test
    fun reportsAreCheapInTheBackground() {
        assertEquals(300, ReportSchedule.intervalSeconds(everySeconds = 300))
        assertEquals(60, ReportSchedule.intervalSeconds(everySeconds = 5))
        assertEquals(3600, ReportSchedule.intervalSeconds(everySeconds = 99_999))

        val last = ReportSchedule.Last(now, 43.6500, -79.3800)
        assertTrue(ReportSchedule.shouldSend(null, now, 43.65, -79.38, everySeconds = 300))
        // Not again before the interval while standing still…
        assertFalse(ReportSchedule.shouldSend(last, now + 120, 43.6500, -79.3800, everySeconds = 300))
        assertTrue(ReportSchedule.shouldSend(last, now + 300, 43.6500, -79.3800, everySeconds = 300))
        // …but a real move (about 110 m north) goes after a minute.
        assertFalse(ReportSchedule.shouldSend(last, now + 30, 43.6510, -79.3800, everySeconds = 300))
        assertTrue(ReportSchedule.shouldSend(last, now + 61, 43.6510, -79.3800, everySeconds = 300))
        val d = ReportSchedule.distanceMeters(43.6500, -79.3800, 43.6510, -79.3800)
        assertTrue("about 111 m: $d", d in 105.0..117.0)
    }

    @Test
    fun aLocateIsAnsweredWithOneReport() {
        assertTrue(LocatePush.shouldAnswer(null, now))
        assertFalse(LocatePush.shouldAnswer(now, now + 5)) // the same tap delivered twice
        assertTrue(LocatePush.shouldAnswer(now, now + 121)) // the next tap
        assertTrue(LocatePush.shouldAnswer(now + 500, now)) // the clock moved back
        var answeredAt: Double? = null
        var reports = 0
        for (t in listOf(now, now + 1, now + 3, now + 20)) {
            if (LocatePush.shouldAnswer(answeredAt, t)) {
                answeredAt = t
                reports++
            }
        }
        assertEquals(1, reports)
        assertEquals("refresh", LocatePush.SOURCE)
        assertTrue(LocatePush.isLocate(mapOf("type" to "refresh")))
    }

    @Test
    fun colourAndPhoto() {
        val list = NewmanJson.decodeFromString(ListSerializer(Person.serializer()),
            """[{"id":"p1","name":"Chris","color":"#3ccfb4","photo":"/api/people/p1/photo?v=ab12"},{"id":"p2","name":"Dante"}]""")
        assertEquals("/api/people/p1/photo?v=ab12", PersonLook.photoPath(list[0]))
        assertNull(PersonLook.photoPath(list[1]))
        assertEquals(0x3CCFB4, PersonLook.rgb(list[0], 0))
        assertEquals(0xC58BFF, PersonLook.rgb(list[1], 1)) // older hub: by position
        assertEquals(0xC58BFF, PersonLook.rgb(Person("x", color = "nonsense"), 7))
        assertEquals(0xFF3CCFB4.toInt(), PersonLook.argb(list[0], 0))
        assertNull(PersonLook.rgb("#12345"))
        assertNull(PersonLook.rgb("+12345"))
        assertEquals("#3CCFB4", PersonLook.normalized("3ccfb4"))
        assertEquals("#00000A", PersonLook.normalized("#00000a"))
        assertTrue(PersonLook.same("#3ccfb4", "#3CCFB4"))
        assertFalse(PersonLook.same("#3ccfb4", null))
        assertEquals(12, PersonLook.PRESETS.size)
        assertTrue(PersonLook.PRESETS.all { PersonLook.rgb(it) != null })
        assertEquals(1024 to 768, PersonLook.fitted(4032, 3024))
        assertEquals(800 to 600, PersonLook.fitted(800, 600))
        assertEquals(Triple(128, 0, 768), PersonLook.squareCrop(1024, 768))
        assertEquals("""{"color":"#FF7EB6"}""", NewmanJson.encodeToString(ProfileUpdate.serializer(), ProfileUpdate("#FF7EB6")))
        val msg = LiveMessage.parse("""{"type":"person","person":{"id":"p2","name":"Dante","at":1790000100,"color":"#C58BFF","photo":"/api/people/p2/photo?v=9"}}""")
        assertEquals("/api/people/p2/photo?v=9", (msg as LiveMessage.PersonChanged).person.photo)
    }

    @Test
    fun tapToRefresh() {
        val dante = Person("p2", "Dante", sharing = true, at = now - 300)
        var s = PersonRefresh.start(PersonRefresh.IDLE, dante, now)
        assertEquals(PersonRefresh.Phase.UPDATING, s.phase)
        assertEquals("Updating…", PersonRefresh.text(s, now))
        assertEquals(s, PersonRefresh.start(s, dante, now + 3)) // a second tap changes nothing
        s = PersonRefresh.replied(s, 202, now + 120, now + 1)
        assertEquals(PersonRefresh.Phase.UPDATING, s.phase)
        assertEquals(PersonRefresh.Phase.UPDATING, PersonRefresh.personChanged(s, dante).phase) // same fix
        assertEquals(PersonRefresh.Phase.UPDATING, PersonRefresh.personChanged(s, dante.copy(at = null)).phase)
        assertEquals(PersonRefresh.IDLE, PersonRefresh.personChanged(s, dante.copy(at = now + 10))) // newer fix: done
        assertEquals(s, PersonRefresh.tick(s, now + 30))
        val timedOut = PersonRefresh.tick(s, now + 45)
        assertEquals(PersonRefresh.Phase.FAILED, timedOut.phase)
        assertEquals("Couldn't reach their phone right now", PersonRefresh.text(timedOut, now + 46))
        assertEquals(PersonRefresh.IDLE, PersonRefresh.tick(timedOut, now + 53))
        val tooSoon = PersonRefresh.replied(PersonRefresh.start(PersonRefresh.IDLE, dante, now), 429, now + 74.2, now)
        assertEquals(PersonRefresh.Phase.TOO_SOON, tooSoon.phase)
        assertEquals("Updated recently - try again in 75 s", PersonRefresh.text(tooSoon, now))
        assertEquals(now + 120, PersonRefresh.replied(PersonRefresh.start(PersonRefresh.IDLE, null, now), 429, null, now).until, 0.0)
        assertEquals(PersonRefresh.IDLE, PersonRefresh.replied(PersonRefresh.start(PersonRefresh.IDLE, null, now), 404, null, now))
        assertEquals(PersonRefresh.Phase.FAILED, PersonRefresh.replied(PersonRefresh.start(PersonRefresh.IDLE, null, now), 0, null, now).phase)
        assertEquals(PersonRefresh.IDLE, PersonRefresh.replied(PersonRefresh.IDLE, 429, now, now)) // a late reply
        assertEquals(PersonRefresh.IDLE,
            PersonRefresh.personChanged(PersonRefresh.start(PersonRefresh.IDLE, Person("n"), now), Person("n", at = now + 5)))
        assertEquals(1_790_000_120.0, PersonRefresh.nextAllowedAt("""{"error":"Updated recently","nextAllowedAt":1790000120}""")!!, 0.0)
        assertNull(PersonRefresh.nextAllowedAt("nope"))
    }
    @Test
    fun reportBodyAndReply() {
        val body = NewmanJson.encodeToString(LocationReport.serializer(),
            LocationReport(43.6, -79.4, accuracyM = null, at = 1790000000.25, speedMps = 1.5, source = "background"))
        assertTrue(body, body.contains("\"lat\":43.6") && body.contains("\"source\":\"background\"") && body.contains("\"speedMps\":1.5"))
        val at = NewmanJson.decodeFromString(LocationReport.serializer(), body).at
        assertEquals(1790000000.25, at, 0.0001)
        val reply = NewmanJson.decodeFromString(ReportReply.serializer(), """{"live":false,"everySeconds":300}""")
        assertEquals(300, reply.everySeconds)
        val places = NewmanJson.decodeFromString(ListSerializer(Place.serializer()),
            """[{"id":"home","name":"Home","lat":null,"lon":null,"radiusM":150,"builtIn":true},
                {"id":"pl_1","name":"School","lat":43.66,"lon":-79.39,"radiusM":250,"builtIn":false}]""")
        assertNull(places[0].lat) // Home isn't placed yet
        assertEquals(250.0, places[1].radiusM, 0.0)
        assertTrue(LocatePush.isLocate(mapOf("type" to "locate")))
        assertFalse(LocatePush.isLocate(mapOf("kind" to "automation", "title" to "Door open")))
    }
}
