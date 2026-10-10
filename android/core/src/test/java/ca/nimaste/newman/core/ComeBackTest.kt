package ca.nimaste.newman.core

import ca.nimaste.newman.core.wall.ComeBack
import ca.nimaste.newman.core.wall.ComeBack.Options
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The wall coming back to the front by itself after it left (Home, Recents, another app). */
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
