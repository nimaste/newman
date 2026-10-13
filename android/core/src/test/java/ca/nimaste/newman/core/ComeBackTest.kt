package ca.nimaste.newman.core

import ca.nimaste.newman.core.wall.ComeBack
import ca.nimaste.newman.core.wall.ComeBack.Options
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The wall coming back to the front by itself after it left (Home, Recents, another app), and its screen never staying off. */
class ComeBackTest {
    private val min = 60_000L
    private val on = Options(afterLeaving = true, minutes = 2, onWake = true)

    @Test
    fun comesBackAfterTheMinutesAway() {
        val c = ComeBack()
        assertNull(c.left(0, screenOn = true))
        val first = c.check(0, on)
        assertFalse(first.bringBack)
        assertEquals(2 * min, first.recheckInMs)
        assertFalse(c.check(2 * min - 1, on).bringBack)
        assertTrue(c.check(2 * min, on).bringBack)
    }

    @Test
    fun inFrontOrSettingOffNeverComesBack() {
        val c = ComeBack()
        assertFalse(c.check(10 * min, on).bringBack) // never left
        c.left(0, true)
        assertFalse(c.check(5 * min, on.copy(afterLeaving = false)).bringBack)
        c.entered()
        assertFalse(c.check(5 * min, on).bringBack)
        assertFalse(c.screenWoke(5 * min, on).bringBack)
    }

    @Test
    fun screenTurningOffIsNotLeaving() {
        val c = ComeBack()
        assertNotNull(c.left(0, screenOn = false))
        assertFalse(c.away)
        assertFalse(c.check(10 * min, on).bringBack)
    }

    @Test
    fun playbackHoldsItOffAndStartsTheWaitOver() {
        val c = ComeBack()
        c.left(0, true)
        c.othersPlaying(true, 30_000) // Prime Video, music, Alexa
        val d = c.check(10 * min, on)
        assertFalse(d.bringBack)
        assertNull(d.recheckInMs) // the playback callback asks again
        c.othersPlaying(false, 10 * min)
        assertEquals(2 * min, c.check(10 * min, on).recheckInMs)
        assertFalse(c.check(11 * min, on).bringBack)
        assertTrue(c.check(12 * min, on).bringBack)
    }

    @Test
    fun playbackBeforeLeavingCountsFromLeaving() {
        val c = ComeBack()
        c.othersPlaying(true, 0)
        c.othersPlaying(false, 1_000)
        c.left(5 * min, true)
        assertFalse(c.check(6 * min, on).bringBack)
        assertTrue(c.check(7 * min, on).bringBack)
    }

    @Test
    fun screenWakeBringsItBackAtOnceUnlessPlayingOrOff() {
        val c = ComeBack()
        c.left(0, true)
        assertFalse(c.screenWoke(1_000, on.copy(onWake = false)).bringBack)
        c.othersPlaying(true, 1_000)
        assertFalse(c.screenWoke(2_000, on).bringBack)
        c.othersPlaying(false, 3_000)
        assertTrue(c.screenWoke(4_000, on).bringBack)
        // Even with "after leaving" off, the screen waking brings it back.
        val d = ComeBack()
        d.left(0, true)
        assertTrue(d.screenWoke(1_000, on.copy(afterLeaving = false)).bringBack)
    }

    @Test
    fun triesAgainLaterIfItDidNotCome() {
        val c = ComeBack()
        c.left(0, true)
        assertTrue(c.check(2 * min, on).bringBack)
        assertTrue(c.away) // until the wall reports being in front
        assertFalse(c.check(2 * min + 1, on).bringBack)
        assertTrue(c.check(4 * min, on).bringBack)
        c.entered()
        assertFalse(c.away)
    }

    @Test
    fun leavingAgainKeepsTheFirstTime() {
        val c = ComeBack()
        c.left(0, true)
        c.left(1 * min, true) // (the wall's own screen came and went while away)
        assertTrue(c.check(2 * min, on).bringBack)
    }

    @Test
    fun screenOffWhileAwayComesBackDimmedAtOnce() {
        val c = ComeBack()
        c.left(0, true) // Alexa (or anything) came over the wall
        c.othersPlaying(true, 1_000) // even with something playing
        val d = c.screenOff(30_000, keepsScreenOn = true)
        assertTrue(d.bringBack)
        assertTrue(d.dimmed)
        // Whatever the come-back settings say.
        val e = ComeBack()
        e.left(0, true)
        assertTrue(e.screenOff(1_000, keepsScreenOn = true).bringBack)
    }

    @Test
    fun screenOffWithTheWallInFrontComesBackOnDimmedToo() {
        // The power button, Show Mode, the charger: the screen never stays off.
        val c = ComeBack()
        assertNotNull(c.left(0, screenOn = false)) // the wall paused because the screen went off
        assertFalse(c.away)
        val d = c.screenOff(0, keepsScreenOn = true)
        assertTrue(d.bringBack)
        assertTrue(d.dimmed)
        assertEquals(ComeBack.SCREEN_OFF_RETRY_MS, d.recheckInMs) // look again then: still off = try again
        // Back on, the wall in front: pressed again, back on again at once.
        c.entered()
        assertTrue(c.screenOff(1_000, keepsScreenOn = true).bringBack)
    }

    @Test
    fun screenOffWithKeepAwakeOffLetsItSleep() {
        val c = ComeBack()
        c.left(0, true)
        val d = c.screenOff(1_000, keepsScreenOn = false)
        assertFalse(d.bringBack)
        assertNull(d.recheckInMs) // and nothing comes back to try again
        val e = ComeBack()
        assertFalse(e.screenOff(1_000, keepsScreenOn = false).bringBack) // nor with the wall in front
    }

    @Test
    fun screenOffReturnThatDidNotHappenIsRetriedSoonNotGivenUp() {
        val c = ComeBack()
        c.left(0, true)
        assertTrue(c.screenOff(0, true).bringBack)
        assertEquals(30_000L, ComeBack.SCREEN_OFF_RETRY_MS)
        // Too soon (the screen went off again right away): not now, but asked again when it's time.
        val early = c.screenOff(10_000, true)
        assertFalse(early.bringBack)
        assertEquals(ComeBack.SCREEN_OFF_RETRY_MS - 10_000, early.recheckInMs)
        assertTrue(c.screenOff(ComeBack.SCREEN_OFF_RETRY_MS, true).bringBack)
        // And again, for as long as the screen stays off.
        for (i in 2..10) assertTrue(c.screenOff(i * ComeBack.SCREEN_OFF_RETRY_MS, true).bringBack)
        // Once the wall was in front again, the next time away starts afresh.
        c.entered()
        c.left(11 * ComeBack.SCREEN_OFF_RETRY_MS, true)
        assertTrue(c.screenOff(11 * ComeBack.SCREEN_OFF_RETRY_MS + 1_000, true).bringBack)
    }

    @Test
    fun chargerOverAnotherAppComesBackDimmedAtOnce() {
        val c = ComeBack()
        assertFalse(c.powerChanged(0, on).bringBack) // the wall is in front: nothing to do
        c.left(0, true) // Fire OS's charging screen came over the wall
        val d = c.powerChanged(1_000, on)
        assertTrue(d.bringBack)
        assertTrue(d.dimmed)
        // Not while something plays, nor with "Come back after leaving" off.
        val e = ComeBack()
        e.left(0, true)
        e.othersPlaying(true, 0)
        assertFalse(e.powerChanged(1_000, on).bringBack)
        val f = ComeBack()
        f.left(0, true)
        assertFalse(f.powerChanged(1_000, on.copy(afterLeaving = false)).bringBack)
    }

    @Test
    fun noWallRunningIsStarted() {
        // Screen off: always (dimmed).
        val off = ComeBack.notRunning(screenOn = false, othersPlaying = true, afterLeaving = false)
        assertTrue(off.bringBack)
        assertTrue(off.dimmed)
        assertFalse(ComeBack.notRunning(screenOn = false, othersPlaying = false, afterLeaving = true, keepsScreenOn = false).bringBack)
        // Screen on: unless someone is busy in another app.
        assertTrue(ComeBack.notRunning(screenOn = true, othersPlaying = false, afterLeaving = true).bringBack)
        assertFalse(ComeBack.notRunning(screenOn = true, othersPlaying = true, afterLeaving = true).bringBack)
        assertFalse(ComeBack.notRunning(screenOn = true, othersPlaying = false, afterLeaving = false).bringBack)
    }

    @Test
    fun otherReturnsAreNotDimmed() {
        val c = ComeBack()
        c.left(0, true)
        assertFalse(c.screenWoke(1_000, on).dimmed)
        val d = ComeBack()
        d.left(0, true)
        assertFalse(d.check(2 * min, on).dimmed)
    }

    @Test
    fun minuteChoices() {
        assertEquals(listOf(1, 2, 5, 10), ComeBack.MINUTE_CHOICES)
        assertEquals(5, ComeBack.minutesSetting(5))
        assertEquals(2, ComeBack.minutesSetting(0))
        assertEquals(2, ComeBack.minutesSetting(7))
        val c = ComeBack()
        c.left(0, true)
        assertFalse(c.check(9 * min, on.copy(minutes = 10)).bringBack)
        assertTrue(c.check(10 * min, on.copy(minutes = 10)).bringBack)
    }
}
