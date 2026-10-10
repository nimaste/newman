package ca.nimaste.newman.core.voice

/**
 * One recording for "Teach Hey Newman" (the page asks: NewmanWall.recordClip(maxMs, fixed)), fed
 * 80 ms frames from the microphone.
 *
 *  - speech (fixed = false): waits up to [waitMs] for someone to speak, then keeps from just before
 *    the first word ([preRollMs]) to just after the pause that follows it ([endSilenceMs] of quiet,
 *    [tailMs] kept), at most [maxMs] of speech. The first [learnFrames] frames measure the room.
 *  - fixed (fixed = true): exactly [maxMs] of audio (the "just talk normally" step), or less when
 *    stopped early ([finishEarly]).
 *
 * [push] says CONTINUE until DONE (take [pcm]) or NO_SPEECH (nobody spoke).
 */
class ClipCapture(
    maxMs: Int,
    val fixed: Boolean,
    private val waitMs: Int = 10_000,
    private val endSilenceMs: Int = 640,
    private val preRollMs: Int = 240,
    private val tailMs: Int = 240,
    private val minSpeechMs: Int = 160,
    private val learnFrames: Int = 3,
    private val frameMs: Int = Audio.FRAME_MS,
) {
    enum class Result { CONTINUE, DONE, NO_SPEECH }

    val maxMs: Int = clampMs(maxMs, fixed)
    private val frames = ArrayList<ShortArray>()
    private var floor = 0.0
    private var learned = 0
    private var run = 0
    private var startFrame = -1 // where speech began (frame index)
    private var lastSpeech = -1
    private var speechMs = 0
    private var silenceMs = 0
    var result = Result.CONTINUE
        private set

    val started: Boolean get() = startFrame >= 0

    /** A frame is speech when well above the room (and never below [MIN_RMS]); hiss (many zero crossings) needs to be louder. */
    fun isSpeech(rms: Double, zcr: Double): Boolean {
        val t = maxOf(floor * 2.5, MIN_RMS)
        return rms > t && (zcr < 0.35 || rms > t * 2)
    }

    fun push(frame: ShortArray, n: Int = frame.size): Result {
        if (result != Result.CONTINUE) return result
        frames.add(frame.copyOf(n))
        val i = frames.size - 1
        val elapsed = frames.size * frameMs
        if (fixed) {
            if (elapsed >= maxMs) result = Result.DONE
            return result
        }
        val rms = Audio.rms(frame, n)
        if (learned < learnFrames) { // the room, before anyone speaks
            floor = (floor * learned + rms) / (learned + 1)
            learned++
            return result
        }
        if (isSpeech(rms, Audio.zeroCrossings(frame, n))) {
            run++
            silenceMs = 0
            lastSpeech = i
            if (started) speechMs += frameMs
            if (!started && run >= 2) { // two frames in a row: a voice, not a click
                startFrame = i - 1
                speechMs = 2 * frameMs
            }
        } else {
            run = 0
            if (started) silenceMs += frameMs else floor = floor * 0.9 + rms * 0.1
        }
        result = when {
            started && speechMs >= minSpeechMs && silenceMs >= endSilenceMs -> Result.DONE
            started && (i - startFrame + 1) * frameMs >= maxMs -> Result.DONE
            !started && elapsed >= waitMs -> Result.NO_SPEECH
            else -> Result.CONTINUE
        }
        return result
    }

    /** Stop now: a fixed recording keeps what it has (if any); a speech one keeps what was said so far. */
    fun finishEarly(): Result {
        if (result == Result.CONTINUE) {
            result = if (fixed) (if (frames.isNotEmpty()) Result.DONE else Result.NO_SPEECH)
            else if (started) Result.DONE else Result.NO_SPEECH
        }
        return result
    }

    /** The kept audio as 16-bit little-endian PCM (16 kHz mono). */
    fun pcm(): ByteArray {
        val (a, b) = keptRange()
        if (a > b) return ByteArray(0)
        val size = (a..b).sumOf { frames[it].size }
        val all = ShortArray(size)
        var o = 0
        for (k in a..b) {
            System.arraycopy(frames[k], 0, all, o, frames[k].size)
            o += frames[k].size
        }
        return Audio.pcm16le(all)
    }

    val keptMs: Int get() = keptRange().let { (a, b) -> if (a > b) 0 else (b - a + 1) * frameMs }

    private fun keptRange(): Pair<Int, Int> {
        if (frames.isEmpty()) return 0 to -1
        if (fixed) return 0 to frames.size - 1
        if (!started) return 0 to -1
        val a = maxOf(0, startFrame - preRollMs / frameMs)
        val b = minOf(frames.size - 1, lastSpeech + tailMs / frameMs)
        return a to b
    }

    companion object {
        const val MIN_RMS = 150.0
        const val MAX_SPEECH_MS = 6_000
        const val MAX_FIXED_MS = 35_000

        /** What the page may ask for: a spoken clip 1-6 s, a fixed one 1-35 s. */
        fun clampMs(ms: Int, fixed: Boolean): Int = ms.coerceIn(1_000, if (fixed) MAX_FIXED_MS else MAX_SPEECH_MS)
    }
}
