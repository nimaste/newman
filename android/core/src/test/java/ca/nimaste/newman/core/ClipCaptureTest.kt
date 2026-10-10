package ca.nimaste.newman.core

import ca.nimaste.newman.core.voice.Audio
import ca.nimaste.newman.core.voice.ClipCapture
import ca.nimaste.newman.core.voice.ClipCapture.Result
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.PI
import kotlin.math.sin
import kotlin.random.Random

/** "Teach Hey Newman" recordings: when a spoken clip starts and ends, what's kept, the fixed-length talking clip. */
class ClipCaptureTest {
    private val f = Audio.FRAME

    private fun voice(amp: Double = 3000.0) = ShortArray(f) { (sin(2 * PI * 220.0 * it / Audio.RATE) * amp).toInt().toShort() }
    private fun room(seed: Int = 1): ShortArray {
        val r = Random(seed)
        return ShortArray(f) { r.nextInt(-40, 41).toShort() }
    }

    /** Pushes frames until it's no longer CONTINUE; returns (result, frames pushed). */
    private fun feed(c: ClipCapture, frames: List<ShortArray>): Pair<Result, Int> {
        var n = 0
        for (fr in frames) {
            n++
            val r = c.push(fr)
            if (r != Result.CONTINUE) return r to n
        }
        return Result.CONTINUE to n
    }

    @Test
    fun aSpokenClipIsTrimmedToTheWordsAndEndsAtThePause() {
        val c = ClipCapture(5000, fixed = false)
        // 1 s of room, 0.8 s of voice, then quiet.
        val frames = List(12) { room(it) } + List(10) { voice() } + List(20) { room(100 + it) }
        val (r, pushed) = feed(c, frames)
        assertEquals(Result.DONE, r)
        assertEquals(12 + 10 + 8, pushed) // ended after 640 ms of quiet
        // kept: 240 ms before the voice + the voice + 240 ms after
        assertEquals(240 + 800 + 240, c.keptMs)
        assertEquals(c.keptMs * Audio.RATE / 1000 * 2, c.pcm().size)
    }

    @Test
    fun nobodySpeaking() {
        val c = ClipCapture(5000, fixed = false, waitMs = 2000)
        val (r, pushed) = feed(c, List(100) { room(it) })
        assertEquals(Result.NO_SPEECH, r)
        assertEquals(25, pushed)
        assertEquals(0, c.pcm().size)
    }

    @Test
    fun aClickIsntSpeechButALoudRoomIsLearned() {
        // One loud frame (a click) in quiet is not a start.
        val c = ClipCapture(5000, fixed = false, waitMs = 1600)
        val (r, _) = feed(c, List(5) { room(it) } + listOf(voice(8000.0)) + List(20) { room(50 + it) })
        assertEquals(Result.NO_SPEECH, r)
        // A steady hum the room starts with isn't speech either.
        val hum = ClipCapture(5000, fixed = false, waitMs = 1600)
        assertEquals(Result.NO_SPEECH, feed(hum, List(30) { voice(400.0) }).first)
    }

    @Test
    fun speechIsCappedAtMax() {
        val c = ClipCapture(1000, fixed = false)
        val (r, pushed) = feed(c, List(3) { room(it) } + List(40) { voice() })
        assertEquals(Result.DONE, r)
        assertTrue(pushed <= 3 + 1 + 13)
        assertTrue(c.keptMs in 1000..1400)
    }

    @Test
    fun theTalkingClipIsAFixedLengthAndCanStopEarly() {
        val c = ClipCapture(2000, fixed = true)
        val (r, pushed) = feed(c, List(40) { room(it) }) // even silence: the hub judges it
        assertEquals(Result.DONE, r)
        assertEquals(25, pushed)
        assertEquals(2000, c.keptMs)
        val early = ClipCapture(30000, fixed = true)
        feed(early, List(10) { voice() })
        assertEquals(Result.DONE, early.finishEarly())
        assertEquals(800, early.keptMs)
        assertEquals(Result.NO_SPEECH, ClipCapture(5000, fixed = false).finishEarly())
    }

    @Test
    fun limitsThePageCanAskFor() {
        assertEquals(1000, ClipCapture.clampMs(10, fixed = false))
        assertEquals(6000, ClipCapture.clampMs(60000, fixed = false))
        assertEquals(35000, ClipCapture.clampMs(60000, fixed = true))
        assertEquals(30000, ClipCapture(30000, fixed = true).maxMs)
    }
}
