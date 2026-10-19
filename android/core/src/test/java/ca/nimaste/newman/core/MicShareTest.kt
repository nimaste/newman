package ca.nimaste.newman.core

import ca.nimaste.newman.core.voice.AssistantReturn
import ca.nimaste.newman.core.voice.AssistantReturn.Action
import ca.nimaste.newman.core.voice.MicGate
import ca.nimaste.newman.core.voice.Recording
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Giving the microphone to Alexa (and other apps) and coming back to the front afterwards. */
class MicShareTest {

    private val ours = Recording(1, ours = true)
    private val alexa = Recording(7, ours = false)

    @Test
    fun anotherAppRecordingTakesTheMicUntilAGraceAfterItStops() {
        val g = MicGate()
        g.update(listOf(ours), 0)
        assertTrue(g.mayRecord(0)) // our own listening (and our own capture after the wake word) never blocks us
        g.update(listOf(ours, alexa), 1_000)
        assertFalse(g.mayRecord(1_000))
        assertEquals("another app is recording", g.reason)
        g.update(listOf(alexa), 1_100) // we let go
        assertFalse(g.mayRecord(5_000))
        g.update(emptyList(), 6_000)
        assertFalse(g.mayRecord(7_000))
        assertEquals(1_500L, g.recheckIn(6_000))
        assertTrue(g.mayRecord(7_500))
        assertNull(g.recheckIn(7_500))
    }

    @Test
    fun hotwordDetectorsAreNotWaitedFor() {
        val g = MicGate()
        g.update(listOf(ours, Recording(9, ours = false, hotword = true)), 0)
        assertTrue(g.mayRecord(0))
    }

    @Test
    fun beingSilencedBacksOffLongerEachTime() {
        val g = MicGate()
        g.update(listOf(ours.copy(silenced = true)), 0)
        assertFalse(g.mayRecord(0))
        assertEquals("silenced by Android", g.reason)
        assertTrue(g.mayRecord(1_500))
        g.update(listOf(ours.copy(id = 2, silenced = true)), 2_000)
        assertFalse(g.mayRecord(4_999))
        assertTrue(g.mayRecord(5_000)) // 3 s
        g.update(listOf(ours.copy(id = 3, silenced = true)), 6_000)
        assertFalse(g.mayRecord(11_999))
        assertTrue(g.mayRecord(12_000)) // 6 s
        g.update(listOf(ours.copy(id = 4, silenced = true)), 200_000) // long after: from the start again
        assertTrue(g.mayRecord(201_500))
    }

    @Test
    fun anAlwaysOnRecorderIsGivenUpOnButANewOneIsStillWaitedFor() {
        val g = MicGate()
        g.update(listOf(alexa), 0)
        assertFalse(g.mayRecord(179_999))
        assertTrue(g.mayRecord(180_000))
        g.update(listOf(alexa, Recording(8, ours = false)), 200_000)
        assertFalse(g.mayRecord(200_000))
        g.update(listOf(alexa), 210_000)
        assertFalse(g.mayRecord(211_000))
        assertTrue(g.mayRecord(211_500))
    }

    @Test
    fun returnsAfterAnAssistantTookOverAndWentQuiet() {
        // As logged on a Fire tablet: "Alexa, what's the weather?" - Alexa's screen comes over the
        // wall, its sound, then the answer (both on the music stream), then nothing.
        val r = AssistantReturn()
        assertNull(r.left(55_990, null))
        assertTrue(r.armed)
        r.othersPlaying(true, 56_110)
        r.othersPlaying(false, 56_920)
        r.othersPlaying(true, 58_600)
        assertEquals(Action.NONE, r.check(59_000))
        r.othersPlaying(false, 59_410)
        assertEquals(6_000L, r.nextCheckIn(59_410))
        assertEquals(Action.NONE, r.check(65_400))
        assertEquals(Action.RETURN, r.check(65_410))
        assertEquals("the answer finished: nothing else has played for 6 s", r.returnReason)
        assertFalse(r.armed)
        assertEquals(Action.NONE, r.check(70_000))
    }

    @Test
    fun waitsForASlowAnswerToStartAndFinish() {
        // Alexa+: its screen shows the answer text first; the spoken answer starts 12 s later.
        val r = AssistantReturn()
        r.left(0, null)
        assertFalse(r.answerStarted)
        assertEquals(Action.NONE, r.check(6_000)) // the old rule came back here and cut the answer off
        assertEquals(Action.NONE, r.check(11_999))
        r.othersPlaying(true, 12_000)
        assertTrue(r.answerStarted)
        assertEquals(Action.NONE, r.check(17_000))
        r.othersPlaying(false, 19_000)
        assertEquals(6_000L, r.nextCheckIn(19_000))
        assertEquals(Action.NONE, r.check(24_999))
        assertEquals(Action.RETURN, r.check(25_000))
        assertTrue(r.returnReason.startsWith("the answer finished"))
    }

    @Test
    fun noAnswerAtAllComesBackAfterThirtySeconds() {
        // A text-only answer, or the user just looked at Alexa's screen.
        val r = AssistantReturn()
        r.left(0, null)
        assertEquals(30_000L, r.nextCheckIn(0))
        assertEquals(Action.NONE, r.check(29_999))
        assertEquals(Action.RETURN, r.check(30_000))
        assertEquals("no answer played within 30 s", r.returnReason)
    }

    @Test
    fun aFollowUpAnswerStartsTheQuietWaitOver() {
        val r = AssistantReturn()
        r.left(0, null)
        r.othersPlaying(true, 1_000)
        r.othersPlaying(false, 3_000)
        assertEquals(Action.NONE, r.check(8_000))
        r.othersPlaying(true, 8_500) // "…would you like to hear more?"
        r.othersPlaying(false, 10_000)
        assertEquals(Action.NONE, r.check(15_999))
        assertEquals(Action.RETURN, r.check(16_000))
    }

    @Test
    fun staysAwayAtLeastFourSeconds() {
        val r = AssistantReturn(quietMs = 1_000)
        r.left(0, null)
        r.othersPlaying(true, 100)
        r.othersPlaying(false, 200)
        assertEquals(Action.NONE, r.check(3_999))
        assertEquals(Action.RETURN, r.check(4_000))
    }

    @Test
    fun playbackBeforeTheTakeoverIsNotTheAnswer() {
        val r = AssistantReturn()
        r.othersPlaying(true, 0)
        r.othersPlaying(false, 500)
        r.left(1_000, null)
        assertFalse(r.answerStarted)
        assertEquals(Action.NONE, r.check(10_000))
        assertEquals(Action.RETURN, r.check(31_000))
    }

    @Test
    fun musicTheUserAskedForPlaysOnBehindTheWall() {
        val r = AssistantReturn()
        r.left(0, null)
        r.othersPlaying(true, 3_000) // "Alexa, play some jazz"
        assertEquals(Action.NONE, r.check(22_999))
        assertEquals(20_000L, r.nextCheckIn(3_000))
        assertEquals(Action.RETURN, r.check(23_000))
        assertTrue(r.returnReason.startsWith("another app has been playing"))
    }

    @Test
    fun notAfterHomeRecentsTheWallsOwnScreensOrTheScreenTurningOff() {
        assertEquals("the screen turned off", AssistantReturn.leaveBlocker(screenOn = false, ownScreen = false, closing = false))
        assertEquals("the wall opened a screen of its own", AssistantReturn.leaveBlocker(screenOn = true, ownScreen = true, closing = false))
        assertEquals("the wall is closing", AssistantReturn.leaveBlocker(screenOn = true, ownScreen = false, closing = true))
        assertNull(AssistantReturn.leaveBlocker(screenOn = true, ownScreen = false, closing = false))

        val blocked = AssistantReturn()
        assertEquals("the screen turned off", blocked.left(0, "the screen turned off"))
        assertFalse(blocked.armed)
        assertEquals(Action.NONE, blocked.check(10_000))

        // Home pressed: Android's "close system dialogs" may arrive just before or just after the pause.
        val before = AssistantReturn()
        before.userLeft(1_000)
        assertEquals("the Home or Recents button was pressed", before.left(1_200, null))
        assertFalse(before.armed)
        val after = AssistantReturn()
        after.left(1_000, null)
        assertTrue(after.userLeft(1_300))
        assertFalse(after.armed)
        assertEquals(Action.NONE, after.check(10_000))
        // ...but a Home press long ago doesn't stop a later takeover.
        val later = AssistantReturn()
        later.userLeft(0)
        assertNull(later.left(60_000, null))
        assertFalse(later.userLeft(65_000)) // nor does one long after it began (that's Home on Alexa's screen)

        assertTrue(AssistantReturn.isUserLeaveReason("homekey"))
        assertTrue(AssistantReturn.isUserLeaveReason("recentapps"))
        assertFalse(AssistantReturn.isUserLeaveReason("assist"))
        assertFalse(AssistantReturn.isUserLeaveReason(null))
    }

    @Test
    fun comingBackByHandCancelsTheWait() {
        val r = AssistantReturn()
        r.left(0, null)
        r.entered()
        assertEquals(Action.NONE, r.check(20_000))
        assertNull(r.nextCheckIn(20_000))
    }

    @Test
    fun givesUpAfterTwoMinutes() {
        val r = AssistantReturn(longPlayMs = 1_000_000)
        r.left(0, null)
        r.othersPlaying(true, 100)
        assertEquals(Action.NONE, r.check(60_000))
        assertEquals(120_000L, r.nextCheckIn(0))
        assertEquals(Action.GIVE_UP, r.check(120_000))
        assertFalse(r.armed)
    }

    @Test
    fun onlyStartedPlayersArePlaying() {
        val started = "AudioPlaybackConfiguration piid:57 type:unknown u/pid:-1/-1 state:started attr:AudioAttributes: usage=USAGE_MEDIA content=CONTENT_TYPE_UNKNOWN sessionId:0"
        assertTrue(AssistantReturn.isStarted(started))
        assertFalse(AssistantReturn.isStarted(started.replace("state:started", "state:paused")))
        assertFalse(AssistantReturn.isStarted(started.replace("state:started", "state:idle")))
        assertTrue(AssistantReturn.isStarted("AudioPlaybackConfiguration piid:57")) // no state shown: assume playing
    }

    @Test
    fun quietWaitComesFromTheSetting() {
        val r = AssistantReturn()
        r.quietMs = 12_000L
        assertNull(r.left(0, null))
        r.othersPlaying(true, 3_000)
        r.othersPlaying(false, 4_500) // Alexa's tone, then thinking
        assertEquals(Action.NONE, r.check(10_500)) // 6 s would have come back here and cut her off
        assertEquals(Action.NONE, r.check(16_400))
        assertEquals(Action.RETURN, r.check(16_500))
    }

    @Test
    fun quietSettingStaysInRange() {
        assertEquals(6, AssistantReturn.quietSetting(1))
        assertEquals(12, AssistantReturn.quietSetting(12))
        assertEquals(30, AssistantReturn.quietSetting(90))
    }
}
