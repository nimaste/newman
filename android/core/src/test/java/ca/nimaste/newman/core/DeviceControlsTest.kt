package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.Spoken
import ca.nimaste.newman.core.model.TextScale
import ca.nimaste.newman.core.net.HubPlan
import ca.nimaste.newman.core.net.SavedHub
import ca.nimaste.newman.core.widgets.DeviceControlPlan
import ca.nimaste.newman.core.widgets.DeviceControlPlan.Style
import ca.nimaste.newman.core.widgets.DeviceUse
import ca.nimaste.newman.core.widgets.UsageRank
import ca.nimaste.newman.core.widgets.WidgetAction
import ca.nimaste.newman.core.widgets.WidgetDevice
import ca.nimaste.newman.core.widgets.WidgetReading
import ca.nimaste.newman.core.widgets.WidgetTiles
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DeviceControlsTest {
    private val plug = WidgetDevice("p", "Den plug", state = "on", stateLabel = "On", active = true,
        actions = listOf(WidgetAction("turn_off", "Turn off", primary = true), WidgetAction("turn_on", "Turn on")))
    private val door = WidgetDevice("d", "Side Door", state = "locked", stateLabel = "Locked",
        actions = listOf(WidgetAction("unlock", "Unlock", sensitive = true, primary = true), WidgetAction("lock", "Lock")))
    private val bell = WidgetDevice("b", "Chime", actions = listOf(WidgetAction("press", "Press", primary = true)))
    private val mode = WidgetDevice("m", "House", state = "home", stateLabel = "Home", actions = listOf(
        WidgetAction("away", "Away", primary = true), WidgetAction("night", "Night"), WidgetAction("home", "Home")))
    private val temp = WidgetDevice("t", "Basement", stateLabel = "21.5 °C", reading = WidgetReading(JsonPrimitive(21.5), "°C"))
    private val upkeep = WidgetDevice("u", "Filter", actions = listOf(WidgetAction("done", "Mark done")))

    @Test
    fun stylesComeFromTheHubsActions() {
        assertEquals(Style.TOGGLE, DeviceControlPlan.style(plug))
        assertEquals(Style.TOGGLE, DeviceControlPlan.style(door))
        assertEquals(Style.PUSH, DeviceControlPlan.style(bell))
        assertEquals(Style.PUSH, DeviceControlPlan.style(mode))
        assertEquals(Style.STATUS, DeviceControlPlan.style(temp))
        assertEquals(Style.STATUS, DeviceControlPlan.style(upkeep)) // nothing is what a tap does
        assertEquals(Style.STATUS, DeviceControlPlan.style(null))
    }

    @Test
    fun onlySensitiveTapsNeedTheUnlockedPhone() {
        assertTrue(DeviceControlPlan.authRequired(door))
        assertFalse(DeviceControlPlan.authRequired(plug))
        assertFalse(DeviceControlPlan.authRequired(temp))
        // Unlocked now: what a tap does is "Lock", which works from the lock screen.
        val open = door.copy(active = true, actions = listOf(WidgetAction("lock", "Lock", primary = true), WidgetAction("unlock", "Unlock", sensitive = true)))
        assertFalse(DeviceControlPlan.authRequired(open))
    }

    @Test
    fun aSwitchRunsTheTapActionOnlyWhenItChangesSomething() {
        assertEquals("turn_off", DeviceControlPlan.actionFor(plug, false)?.command)
        assertNull(DeviceControlPlan.actionFor(plug, true)) // already on
        assertEquals("press", DeviceControlPlan.actionFor(bell, null)?.command)
        assertEquals("away", DeviceControlPlan.actionFor(mode, null)?.command)
        assertNull(DeviceControlPlan.actionFor(temp, null))
        assertNull(DeviceControlPlan.actionFor(plug.copy(available = false), false))
    }

    @Test
    fun statusAndZone() {
        assertEquals("On", DeviceControlPlan.statusText(plug))
        assertEquals("21.5 °C", DeviceControlPlan.statusText(temp))
        assertEquals("Offline", DeviceControlPlan.statusText(plug.copy(available = false)))
        assertEquals("Kitchen", DeviceControlPlan.zone(" Kitchen "))
        assertEquals("Other", DeviceControlPlan.zone(null))
        assertEquals("Other", DeviceControlPlan.zone(""))
    }

    @Test
    fun usageRanksByCountThenRecency() {
        var u = emptyMap<String, DeviceUse>()
        u = UsageRank.record(u, "a", 10)
        u = UsageRank.record(u, "b", 20)
        u = UsageRank.record(u, "b", 30)
        u = UsageRank.record(u, "c", 40)
        u = UsageRank.record(u, "d", 5)
        u = UsageRank.record(u, "e", 50)
        assertEquals(DeviceUse(2, 30), u["b"])
        assertEquals(listOf("b", "e", "c", "a"), UsageRank.top(u, 4))
        assertEquals(listOf("b"), UsageRank.top(u, 1))
        assertEquals(emptyList<String>(), UsageRank.top(emptyMap(), 4))
        assertEquals(DeviceUse(3, 1712345678), UsageRank.decode(UsageRank.encode(DeviceUse(3, 1712345678))))
        assertNull(UsageRank.decode("x:1"))
        assertNull(UsageRank.decode(null))
    }

    @Test
    fun spokenLabelsReadAsASentence() {
        assertEquals("Den plug, on", WidgetTiles.describe(plug))
        assertEquals("Basement, 21.5 °C", WidgetTiles.describe(temp))
        assertEquals("Den plug, offline", WidgetTiles.describe(plug.copy(available = false)))
        assertEquals("Front door, locked, battery 80%", Spoken.of("Front door", "Locked", Spoken.battery(80), null, " "))
        assertEquals("TV, on", Spoken.of("TV", "On"))
        assertEquals("UV index", Spoken.phrase("UV index"))
        assertEquals("turn off", Spoken.phrase("Turn off"))
        assertEquals("on, battery 5%", Spoken.fromDots("On · battery 5%"))
    }

    @Test
    fun cellsCapTheFontScale() {
        assertEquals(1.3f, TextScale.capped(2f), 0f)
        assertEquals(1.15f, TextScale.capped(1.15f), 0f)
        assertEquals(0.85f, TextScale.capped(0.85f), 0f) // smaller text stays smaller
        assertEquals(1f, TextScale.capped(1.5f, TextScale.FITTED_MAX), 0f)
        assertEquals(1f, TextScale.capped(Float.NaN), 0f)
    }

    @Test
    fun changingServerKnowsTheSameHub() {
        val home = SavedHub("hub-1", "Home", "http://10.1.1.2:8093/", "https://home.example.com/")
        // Same id: new addresses for the same hub.
        assertTrue(HubPlan.isSameHub(home, "http://10.1.1.2:8093/", SavedHub("hub-1", "Home", "http://10.1.1.9:8093/", null), "http://10.1.1.9:8093/"))
        // Another id: another hub, even at an address the old one had.
        assertFalse(HubPlan.isSameHub(home, "http://10.1.1.2:8093/", SavedHub("hub-2", "Cottage", "http://10.1.1.2:8093/", null), "http://10.1.1.2:8093/"))
        // No ids (older hub): a shared address decides.
        val old = SavedHub(null, null, null, "https://home.example.com/")
        assertTrue(HubPlan.isSameHub(old, "https://home.example.com", SavedHub(null, "Home", "http://10.1.1.2:8093/", "https://home.example.com/"), "http://10.1.1.2:8093/"))
        assertFalse(HubPlan.isSameHub(old, "https://home.example.com/", SavedHub(null, "Other", null, "https://other.example.com/"), "https://other.example.com/"))
        // Nothing saved yet: nothing to leave.
        assertTrue(HubPlan.isSameHub(null, null, SavedHub("hub-2", "Cottage", null, null), "https://cottage.example.com/"))
    }

    @Test
    fun theHubsStateLabelWinsOverTheKinds() {
        val k = ca.nimaste.newman.core.model.Kind("tablet", states = listOf(ca.nimaste.newman.core.model.KindState("off", "Off")))
        val e = ca.nimaste.newman.core.model.Entity("w", "Wall", state = "off", stateLabel = "Screen off")
        assertEquals("Screen off", ca.nimaste.newman.core.model.HubModel.stateLabel(e, k))
        assertEquals("Off", ca.nimaste.newman.core.model.HubModel.stateLabel(e.copy(stateLabel = ""), k))
        assertNull(ca.nimaste.newman.core.model.HubModel.stateLabel(null, k))
    }

    @Test
    fun theSameHubKeepsAddressesItDidntMention() {
        val old = SavedHub("hub-1", "Home", "http://10.1.1.2:8093/", "https://home.example.com/")
        val m = HubPlan.merged(old, SavedHub("hub-1", "Home sweet", "http://10.1.1.9:8093/", null))
        assertEquals(SavedHub("hub-1", "Home sweet", "http://10.1.1.9:8093/", "https://home.example.com/"), m)
    }
}
