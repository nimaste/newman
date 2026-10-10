package ca.nimaste.newman.core

import ca.nimaste.newman.core.live.LiveMessage
import ca.nimaste.newman.core.livestatus.LiveNotifications
import ca.nimaste.newman.core.livestatus.LiveUpdate
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LiveStatusesTest {
    // What the hub's live socket sends (server/tests/test_live_status.py has the hub side).
    private val socket = """
        {"type": "live", "activities": [
          {"key": "device:fake.garage.1800000000", "slot": "device:fake.garage", "title": "Garage Door",
           "contentState": {"title": "Garage Door", "subtitle": "Open", "since": 1800000000, "active": true, "more": 0,
             "devices": [{"id": "fake.garage", "name": "Garage Door", "icon": "garage", "stateLabel": "Open", "active": true,
               "actions": [{"command": "close", "label": "Close", "sensitive": false}]}]}},
          {"key": "more.1800000100", "slot": "more", "title": "9 more",
           "contentState": {"title": "9 more", "subtitle": "Window 1, Window 2", "since": 1800000100, "active": true, "more": 7,
             "devices": [{"id": "fake.d1", "name": "Window 1", "stateLabel": "Open", "active": true, "actions": []},
                         {"id": "fake.d2", "name": "Window 2", "stateLabel": "Open", "active": true, "actions": []}]}}],
         "ended": [
          {"key": "device:fake.side.1799990000", "slot": "device:fake.side", "title": "Side Door", "reason": "ended",
           "dismissAt": 1800000120, "contentState": {"title": "Side Door locked", "subtitle": "Locked", "since": 1799990000,
             "active": false, "more": 0, "devices": [{"id": "fake.side", "name": "Side Door", "stateLabel": "Locked", "actions": []}]}},
          {"key": "device:fake.d3.1799990000", "slot": "device:fake.d3", "title": "Window 3", "reason": "moved",
           "dismissAt": 1800000000, "contentState": {"title": "Window 3", "devices": []}}]}
    """.trimIndent()

    private val now = 1_800_000_000_000L

    private fun update(): LiveUpdate = (LiveMessage.parse(socket) as LiveMessage.LiveChanged).update

    @Test
    fun oneDeviceIsAnOngoingNotificationWithItsButtonsAndOpensTheDevice() {
        val s = LiveNotifications.spec(update().activities[0], nowMillis = now)
        assertEquals("device:fake.garage", s.tag)
        assertEquals("Garage Door", s.title)
        assertEquals("Open", s.text)
        assertTrue(s.ongoing)
        assertEquals(1_800_000_000_000L, s.sinceMillis)
        assertEquals(listOf("Close"), s.buttons.map { it.label })
        assertEquals("fake.garage", s.buttons[0].deviceId)
        assertFalse(s.buttons[0].sensitive)
        assertEquals("fake.garage", s.openDevice)
        assertTrue(s.lines.isEmpty())
        assertNull(s.timeoutMillis)
    }

    @Test
    fun moreCountsDevicesPlusMore() {
        val more = update().activities[1]
        assertEquals(9, LiveNotifications.total(more.contentState))
        val s = LiveNotifications.spec(more, nowMillis = now)
        assertEquals(listOf("Window 1 · Open", "Window 2 · Open", "and 7 more"), s.lines)
        assertNull(s.openDevice)
        assertTrue(s.buttons.isEmpty())
    }

    @Test
    fun aPlanPostsActivitiesKeepsReallyEndedOnesAMomentAndCancelsTheRest() {
        val plan = LiveNotifications.plan(setOf("device:fake.d3", "device:fake.old", "more"), update(), nowMillis = now)
        assertEquals(listOf("device:fake.garage", "more", "device:fake.side"), plan.post.map { it.tag })
        val ended = plan.post.last()
        assertFalse(ended.ongoing)
        assertEquals("Side Door locked", ended.title)
        assertEquals(120_000L, ended.timeoutMillis)
        assertTrue(ended.buttons.isEmpty())
        assertNull(ended.sinceMillis)
        assertEquals(listOf("device:fake.d3", "device:fake.old"), plan.cancel)
    }

    @Test
    fun severalDevicesNameTheirButtonsAndTakeAtMostThree() {
        val json = """{"activities": [{"key": "automation:a.1", "slot": "automation:a", "title": "Doors",
          "contentState": {"title": "Doors", "subtitle": "Open, Unlocked", "since": 5, "devices": [
            {"id": "a", "name": "Back", "stateLabel": "Open", "actions": [{"command": "close", "label": "Close"}]},
            {"id": "b", "name": "Side", "stateLabel": "Unlocked", "actions": [{"command": "lock", "label": "Lock"},
               {"command": "unlock", "label": "Unlock", "sensitive": true}]},
            {"id": "c", "name": "Front", "stateLabel": "Unlocked", "actions": [{"command": "lock", "label": "Lock"}]}]}}]}"""
        val s = LiveNotifications.spec(LiveNotifications.parse(json).activities[0], nowMillis = now)
        assertEquals(listOf("Close Back", "Lock Side", "Unlock Side"), s.buttons.map { it.label })
        assertTrue(s.buttons[2].sensitive)
        assertEquals(listOf("Back · Open", "Side · Unlocked", "Front · Unlocked"), s.lines)
    }

    @Test
    fun fcmChangesMapToOnePostOrACancel() {
        val start = LiveUpdate.fromPush(mapOf("type" to "live", "event" to "start", "key" to "device:x.1", "slot" to "device:x",
            "title" to "X", "state" to """{"title":"X","subtitle":"On","since":1,"devices":[{"id":"x","name":"X","stateLabel":"On"}]}"""))!!
        assertEquals("start", start.first)
        assertEquals(listOf("device:x"), LiveNotifications.planOne(start.first, start.second, now).post.map { it.tag })
        val moved = LiveUpdate.fromPush(mapOf("type" to "live", "event" to "end", "key" to "device:x.1", "slot" to "device:x",
            "state" to "{}", "dismissAt" to "1800000000"))!!
        assertEquals(listOf("device:x"), LiveNotifications.planOne(moved.first, moved.second, now).cancel)
        val ended = LiveUpdate.fromPush(mapOf("type" to "live", "event" to "end", "key" to "device:x.1", "slot" to "device:x",
            "state" to "{}", "dismissAt" to "1800000060"))!!
        assertEquals(60_000L, LiveNotifications.planOne(ended.first, ended.second, now).post.single().timeoutMillis)
        assertNull(LiveUpdate.fromPush(mapOf("type" to "widgets")))
    }
}
