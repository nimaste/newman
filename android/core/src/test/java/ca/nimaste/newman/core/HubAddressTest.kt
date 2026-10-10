package ca.nimaste.newman.core

import ca.nimaste.newman.core.net.HubAddressPolicy
import ca.nimaste.newman.core.net.SavedHub
import ca.nimaste.newman.core.net.WallAddress
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Which address of the hub: the wall never moves; the phone rides out a hub restart. */
class HubAddressTest {

    private val hub = SavedHub("abc", "Home", "http://10.1.1.2:8093/", "https://home.example.com/")
    private val local = hub.localUrl!!
    private val outside = hub.outsideUrl!!

    // -- the wall ----------------------------------------------------------------------------------

    @Test
    fun theWallStaysOnTheAddressItWasSetUpWith() {
        // Set up at home: home, whatever answers (there's no probing at all).
        assertEquals(local, WallAddress.address(hub, chosen = local, current = local))
        // Moved outside by an older build during a hub restart: back home.
        assertEquals(local, WallAddress.address(hub, chosen = local, current = outside))
        // Chosen outside in Connect: stays outside.
        assertEquals(outside, WallAddress.address(hub, chosen = outside, current = outside))
        // Installs from before the choice was kept: the home address.
        assertEquals(local, WallAddress.address(hub, chosen = null, current = outside))
        assertEquals(outside, WallAddress.address(hub.copy(localUrl = null), chosen = null, current = null))
        assertNull(WallAddress.address(SavedHub(null, null, null, null), null, null))
    }

    // -- the phone ---------------------------------------------------------------------------------

    private class Run(val policy: HubAddressPolicy = HubAddressPolicy()) {
        var current: String? = null
        val asked = mutableListOf<String>()
        fun at(hub: SavedHub, now: Long, up: Set<String>): String? = runBlocking {
            asked.clear()
            policy.pick(hub, current, now) { asked += it; it in up }.also { current = it }
        }
    }

    @Test
    fun aHubRestartDoesNotMoveThePhone() {
        val r = Run().apply { current = local }
        // The hub restarts: home and outside both fail for ~40 s, probed every 30 s.
        assertEquals(local, r.at(hub, 0, emptySet()))
        assertEquals(listOf(local), r.asked) // not even an outside probe before 2 minutes
        assertTrue(r.policy.probing(hub, r.current))
        assertEquals(local, r.at(hub, 30_000, setOf(outside))) // outside answers, but home only failed 30 s
        assertEquals(local, r.at(hub, 60_000, setOf(local, outside))) // back
        assertNull(r.policy.localDownSince)
        assertFalse(r.policy.probing(hub, r.current))
        // Failing again later starts a fresh 2 minutes.
        assertEquals(local, r.at(hub, 500_000, setOf(outside)))
        assertEquals(local, r.at(hub, 590_000, setOf(outside)))
    }

    @Test
    fun outsideOnlyAfterTwoMinutesAndWhenItIsThisHub() {
        val r = Run().apply { current = local }
        assertEquals(local, r.at(hub, 0, emptySet()))
        assertEquals(local, r.at(hub, 119_999, setOf(outside)))
        // 2 minutes, but outside doesn't answer as this hub: stays home.
        assertEquals(local, r.at(hub, 120_000, emptySet()))
        // ... it does now (150 s since home first failed).
        assertEquals(outside, r.at(hub, 150_000, setOf(outside)))
        // On the outside address: home is probed every 30 s, and wins as soon as it answers.
        assertTrue(r.policy.probing(hub, r.current))
        assertEquals(outside, r.at(hub, 180_000, setOf(outside)))
        assertEquals(listOf(local), r.asked)
        assertEquals(local, r.at(hub, 210_000, setOf(local, outside)))
        assertFalse(r.policy.probing(hub, r.current))
    }

    @Test
    fun aNetworkChangeMayMoveAtOnce() {
        val r = Run().apply { current = local }
        r.policy.networkChanged() // left home: wifi gone
        assertEquals(outside, r.at(hub, 0, setOf(outside)))
        // A network change with no outside answer: stays home.
        val r2 = Run().apply { current = local }
        r2.policy.networkChanged()
        assertEquals(local, r2.at(hub, 0, emptySet()))
        // ... and it was used up: the next failure waits the 2 minutes again.
        assertEquals(local, r2.at(hub, 1_000, setOf(outside)))
    }

    @Test
    fun firstStartAndSingleAddresses() {
        assertEquals(local, Run().at(hub, 0, setOf(local, outside)))
        assertEquals(outside, Run().at(hub, 0, setOf(outside)))
        assertEquals(local, Run().at(hub, 0, emptySet()))
        assertEquals(local, Run().at(hub.copy(outsideUrl = null), 0, emptySet()))
        assertEquals("https://h.example.com/", Run().at(SavedHub(null, null, null, "https://h.example.com/"), 0, emptySet()))
    }
}
