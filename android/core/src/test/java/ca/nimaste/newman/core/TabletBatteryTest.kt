package ca.nimaste.newman.core

import ca.nimaste.newman.core.wall.BatteryCheckIn
import ca.nimaste.newman.core.wall.BatterySchedule
import ca.nimaste.newman.core.wall.CheckInState
import ca.nimaste.newman.core.wall.CheckInTiming
import ca.nimaste.newman.core.wall.TabletBattery
import ca.nimaste.newman.core.wall.TabletBattery.Companion.MISSING
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TabletBatteryTest {

    @Test
    fun extrasMapToTheBody() {
        val b = TabletBattery.fromExtras(level = 87, scale = 100, status = 2, plugged = 2, temperature = 312, voltage = 4012)!!
        assertEquals(TabletBattery(87, true, "usb", 31.2, 4012), b)
        val o = Json.parseToJsonElement(b.json()).jsonObject
        assertEquals(setOf("level", "charging", "plugged", "temperatureC", "voltageMv"), o.keys)
        assertEquals("87", o["level"]!!.jsonPrimitive.content)
        assertEquals("true", o["charging"]!!.jsonPrimitive.content)
        assertEquals("usb", o["plugged"]!!.jsonPrimitive.content)
        assertEquals(31.2, o["temperatureC"]!!.jsonPrimitive.content.toDouble(), 0.0001)
        assertEquals("4012", o["voltageMv"]!!.jsonPrimitive.content)
    }

    @Test
    fun scaleStatusAndPlugged() {
        assertEquals(50, TabletBattery.fromExtras(128, 255, 3, 0, 250, 3900)!!.level)
        assertEquals(100, TabletBattery.fromExtras(100, 100, 5, 1, 250, 4200)!!.level)
        assertTrue(TabletBattery.fromExtras(100, 100, 5, 1, 250, 4200)!!.charging) // full counts as charging
        assertFalse(TabletBattery.fromExtras(40, 100, 4, 1, 250, 4200)!!.charging) // not charging while plugged
        assertFalse(TabletBattery.fromExtras(40, 100, 3, 0, 250, 4200)!!.charging) // discharging
        assertEquals("ac", TabletBattery.fromExtras(40, 100, 2, 1, 250, 4200)!!.plugged)
        assertEquals("wireless", TabletBattery.fromExtras(40, 100, 2, 4, 250, 4200)!!.plugged)
        assertEquals("ac", TabletBattery.fromExtras(40, 100, 2, 8, 250, 4200)!!.plugged) // dock
        assertEquals("none", TabletBattery.fromExtras(40, 100, 3, 0, 250, 4200)!!.plugged)
        assertEquals("none", TabletBattery.fromExtras(40, 100, 3, MISSING, 250, 4200)!!.plugged)
    }

    @Test
    fun missingValuesBecomeNull() {
        assertNull(TabletBattery.fromExtras(MISSING, 100, 2, 1, 250, 4000))
        val b = TabletBattery.fromExtras(40, 100, 3, 0, MISSING, 0)!!
        assertNull(b.temperatureC)
        assertNull(b.voltageMv)
        val o = Json.parseToJsonElement(b.json()).jsonObject
        assertEquals(JsonNull, o["temperatureC"])
        assertEquals(JsonNull, o["voltageMv"])
        assertEquals(4000, TabletBattery.fromExtras(40, 100, 3, 0, 250, 4)!!.voltageMv) // volts -> mV
        assertEquals(40, TabletBattery.fromExtras(40, MISSING, 3, 0, 250, 4000)!!.level) // no scale
    }

    @Test
    fun sessionCookie() {
        assertTrue(TabletBattery.hasSession("CF_Authorization=x; newman_session=abc"))
        assertTrue(TabletBattery.hasSession("newman_session=abc"))
        assertTrue(TabletBattery.hasSession("CF_Authorization=x; newman_wall=abc"))
        assertFalse(TabletBattery.hasSession("newman_wall="))
        assertFalse(TabletBattery.hasSession("newman_session="))
        assertFalse(TabletBattery.hasSession("CF_Authorization=x"))
        assertFalse(TabletBattery.hasSession(null))
        assertFalse(TabletBattery.hasSession("my_newman_session=abc"))
    }

    private fun b(level: Int, charging: Boolean = false) = TabletBattery(level, charging, if (charging) "ac" else "none", null, null)

    @Test
    fun scheduleSendsFirstThenEveryFiveMinutesOrOnChange() {
        val s = BatterySchedule()
        val min = 60_000L
        assertTrue("first report", s.shouldSend(0, b(80)))
        s.started()
        assertFalse("one at a time", s.shouldSend(1, b(80)))
        s.succeeded(1_000, b(80))
        assertFalse(s.shouldSend(2 * min, b(80)))
        assertFalse("4 points is not enough", s.shouldSend(2 * min, b(76)))
        assertTrue("5 points down", s.shouldSend(2 * min, b(75)))
        assertTrue("5 points up", s.shouldSend(2 * min, b(85)))
        assertTrue("charging started", s.shouldSend(2 * min, b(80, charging = true)))
        assertTrue("five minutes (timer a little early)", s.shouldSend(1_000 + 5 * min - 5_000, b(80)))
        assertFalse(s.shouldSend(1_000 + 4 * min, b(80)))
    }

    @Test
    fun scheduleWaitsAnIntervalAfterAFailure() {
        val s = BatterySchedule()
        val min = 60_000L
        s.started()
        s.failed(0)
        assertFalse(s.shouldSend(1 * min, b(80)))
        assertFalse("no retry storm on change", s.shouldSend(1 * min, b(50, charging = true)))
        assertTrue(s.shouldSend(5 * min, b(80)))
        s.started()
        s.succeeded(5 * min, b(80))
        assertFalse(s.shouldSend(6 * min, b(80)))
        assertTrue(s.shouldSend(6 * min, b(80, charging = true)))
    }

    @Test
    fun screenAndBackgroundAreSentWhenGiven() {
        val b = TabletBattery(60, false, "none", null, null)
        val off = Json.parseToJsonElement(b.json(screen = false, background = true)).jsonObject
        assertEquals("off", off["screen"]!!.jsonPrimitive.content)
        assertEquals("true", off["background"]!!.jsonPrimitive.content)
        val on = Json.parseToJsonElement(b.json(screen = true, background = true)).jsonObject
        assertEquals("on", on["screen"]!!.jsonPrimitive.content)
        assertFalse("screen" in Json.parseToJsonElement(b.json()).jsonObject)
    }

    @Test
    fun forceNextSendsAtOnce() {
        val s = BatterySchedule()
        val b = TabletBattery(60, false, "none", null, null)
        s.started(); s.succeeded(0, b)
        assertFalse(s.shouldSend(1_000, b))
        s.forceNext()
        assertTrue(s.shouldSend(1_000, b))
        s.started(); s.failed(2_000)
        s.forceNext()
        assertTrue(s.shouldSend(3_000, b)) // even right after a failure
    }

    @Test
    fun checkInStateGoesInTheReport() {
        val b = TabletBattery(60, false, "none", null, null)
        val armed = Json.parseToJsonElement(b.json(false, CheckInState(true, 900, false))).jsonObject
        assertEquals("off", armed["screen"]!!.jsonPrimitive.content)
        assertEquals("true", armed["background"]!!.jsonPrimitive.content)
        assertEquals("900", armed["nextReportIn"]!!.jsonPrimitive.content)
        assertEquals("false", armed["dozeExempt"]!!.jsonPrimitive.content)
        // Not armed: no "background" (the hub then treats it like a tablet that can't check in) and no time.
        val lost = Json.parseToJsonElement(b.json(false, CheckInState(false, null, true))).jsonObject
        assertFalse("background" in lost)
        assertFalse("nextReportIn" in lost)
        assertEquals("true", lost["dozeExempt"]!!.jsonPrimitive.content)
        // An older call without it: as before.
        assertFalse("nextReportIn" in Json.parseToJsonElement(b.json(screen = true, background = true)).jsonObject)
    }

    @Test
    fun canComeBackAndTheAppVersionGoInTheReport() {
        val b = TabletBattery(60, false, "none", null, null)
        val o = Json.parseToJsonElement(b.json(true, CheckInState(true, 900, false), canComeBack = false, appVersion = "1.4.2")).jsonObject
        assertEquals("false", o["canComeBack"]!!.jsonPrimitive.content)
        assertEquals("1.4.2", o["appVersion"]!!.jsonPrimitive.content)
        // Not said: not sent (the hub assumes nothing).
        val none = Json.parseToJsonElement(b.json(true, CheckInState(true, 900, false))).jsonObject
        assertFalse("canComeBack" in none)
        assertFalse("appVersion" in none)
        // The hub takes up to 32 characters; blank is left out.
        val long = Json.parseToJsonElement(b.json(screen = true, appVersion = "1".repeat(40))).jsonObject
        assertEquals(TabletBattery.APP_VERSION_MAX, long["appVersion"]!!.jsonPrimitive.content.length)
        assertFalse("appVersion" in Json.parseToJsonElement(b.json(screen = true, appVersion = " ")).jsonObject)
    }

    @Test
    fun checkInTiming() {
        val min = 60_000L
        // Fire OS 8 (Android 11): exact, no permission needed; 12+: only when allowed.
        assertTrue(CheckInTiming.exact(sdk = 30, canScheduleExact = false))
        assertTrue(CheckInTiming.exact(sdk = 31, canScheduleExact = true))
        assertFalse(CheckInTiming.exact(sdk = 34, canScheduleExact = false))
        assertEquals(15 * min, CheckInTiming.delayMs(exact = true))
        assertEquals(10 * min, CheckInTiming.delayMs(exact = false))
        // Both stay above Doze's once-per-9-minutes limit for alarms allowed while idle.
        assertTrue(CheckInTiming.delayMs(false) >= CheckInTiming.WHILE_IDLE_MIN_MS)
        // Exact: due in 15 minutes = 900 s; inexact: due in 10, up to 10 more late = 1200 s.
        assertEquals(900, CheckInTiming.nextReportInS(nowMs = 0, dueAtMs = 15 * min, exact = true))
        assertEquals(1200, CheckInTiming.nextReportInS(nowMs = 0, dueAtMs = 10 * min, exact = false))
        assertEquals(0, CheckInTiming.nextReportInS(nowMs = 20 * min, dueAtMs = 15 * min, exact = true)) // overdue
        assertEquals(1, CheckInTiming.nextReportInS(nowMs = 0, dueAtMs = 1, exact = true)) // rounds up
    }

    @Test
    fun checkInArmedOnlyWhileTheAlarmIsSetAndNotLost() {
        val min = 60_000L
        assertTrue(CheckInTiming.armed(pendingSet = true, dueAtMs = 15 * min, nowMs = 0, exact = true))
        assertFalse("force-stopped / rebooted: no PendingIntent", CheckInTiming.armed(false, 15 * min, 0, true))
        assertFalse("never set", CheckInTiming.armed(true, 0, 0, true))
        assertTrue("a little late is normal", CheckInTiming.armed(true, 15 * min, 20 * min, true))
        assertFalse("a whole interval overdue: lost", CheckInTiming.armed(true, 15 * min, 31 * min, true))
        assertTrue("inexact: its window too", CheckInTiming.armed(true, 10 * min, 29 * min, false))
        assertFalse(CheckInTiming.armed(true, 10 * min, 31 * min, false))
        assertEquals(CheckInState(true, 900, true), CheckInTiming.state(true, 15 * min, 0, true, dozeExempt = true))
        assertEquals(CheckInState(false, null, null), CheckInTiming.state(false, 15 * min, 0, true, dozeExempt = null))
    }

    @Test
    fun backgroundCheckIn() {
        // Events always report; the periodic one with the screen off, or when the wall has been quiet.
        assertTrue(BatteryCheckIn.shouldSend(event = true, screenOn = true, sinceLastReportMs = 1_000))
        assertTrue(BatteryCheckIn.shouldSend(event = false, screenOn = false, sinceLastReportMs = 1_000))
        assertFalse(BatteryCheckIn.shouldSend(event = false, screenOn = true, sinceLastReportMs = 4 * 60_000L))
        assertTrue(BatteryCheckIn.shouldSend(event = false, screenOn = true, sinceLastReportMs = 11 * 60_000L))
        assertTrue(BatteryCheckIn.shouldSend(event = false, screenOn = true, sinceLastReportMs = null))
        assertTrue(BatteryCheckIn.shouldSend(event = false, screenOn = true, sinceLastReportMs = -5)) // clock changed
    }
}
