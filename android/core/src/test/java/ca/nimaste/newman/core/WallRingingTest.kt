package ca.nimaste.newman.core

import ca.nimaste.newman.core.wall.WallRinging
import ca.nimaste.newman.core.wall.WallRinging.Planned
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Newman's alarms and timers ringing on the wall: the settings, what the page asks, the app's backup. */
class WallRingingTest {

    @Test
    fun settingsKeepToTheirChoices() {
        assertEquals(9, WallRinging.snoozeSetting(9))
        assertEquals(9, WallRinging.snoozeSetting(7))
        assertEquals(30, WallRinging.ringSetting(30))
        assertEquals(10, WallRinging.ringSetting(0))
        assertEquals("""{"snoozeMinutes":5,"ringMinutes":15}""", WallRinging.settingsJson(5, 15))
    }

    @Test
    fun whatThePageAsksToRing() {
        val r = WallRinging.parseRing("""{"kind":"timer","id":"tm_1","title":"pasta timer","ringMs":600000,"extra":1}""")!!
        assertTrue(r.isTimer)
        assertEquals(600_000L, r.capMs)
        assertEquals(3_600_000L, WallRinging.parseRing("""{"id":"a","ringMs":99999999}""")!!.capMs)
        assertNull(WallRinging.parseRing("""{"kind":"alarm"}"""))
        assertNull(WallRinging.parseRing("not json"))
    }

    @Test
    fun theBackupRingsOnlyWhatNothingRang() {
        val list = WallRinging.parseSchedule(
            """[{"id":"b","at":2000000,"title":"ALARM"},{"id":"a","at":1000000,"title":"GYM ALARM"},{"id":"","at":5}]""",
        )
        assertEquals(listOf("a", "b"), list.map { it.id })
        assertEquals("a", WallRinging.nextBackup(list, 0, emptyMap())?.id)
        // The page rang "a" at its time: its backup isn't needed.
        val rung = mapOf("a" to 1_000_500L)
        assertTrue(WallRinging.wasRung(list[0], rung))
        assertEquals("b", WallRinging.nextBackup(list, 0, rung)?.id)
        // Nothing rang "a": a minute after its time the app rings it, until "Stop ringing after".
        assertNull(WallRinging.dueNow(list, 1_030_000, emptyMap(), 600_000))
        assertEquals("a", WallRinging.dueNow(list, 1_060_000, emptyMap(), 600_000)?.id)
        assertNull(WallRinging.dueNow(list, 1_600_001, emptyMap(), 600_000))
        assertNull(WallRinging.dueNow(list, 1_060_000, rung, 600_000))
        // A ring for the same alarm's earlier day doesn't count.
        assertFalse(WallRinging.wasRung(Planned("a", 1_000_000 + 86_400_000), rung))
        assertEquals(list, WallRinging.parseSchedule(WallRinging.scheduleJson(list)))
        assertEquals(emptyList<Planned>(), WallRinging.parseSchedule("oops"))
    }
}
