package ca.nimaste.newman.core.voice

/**
 * Decides when someone has finished speaking after the wake word: energy against the room's
 * noise floor (with a zero-crossing check so hiss doesn't count as a voice), on 80 ms frames.
 *
 *   NO_SPEECH  nobody spoke within [noSpeechMs]
 *   END        they spoke (at least [minSpeechMs]) and then were quiet for [endSilenceMs]
 *   MAX        [maxMs] reached (someone kept talking, or a TV in the background)
 */
class Endpointer(
    noiseFloor: Double,
    private val frameMs: Int = Audio.FRAME_MS,
    private val maxMs: Int = 8_000,
    private val noSpeechMs: Int = 5_000,
    private val endSilenceMs: Int = 800,
    private val minSpeechMs: Int = 240,
    /** A frame is speech when it's this many times louder than the floor (≈ 6 dB)… */
    private val ratio: Double = 2.0,
    /** …and never below this (quiet rooms: the floor can be near zero). */
    private val minRms: Double = 150.0,
) {
    enum class Result { CONTINUE, END, NO_SPEECH, MAX }

    private var floor = noiseFloor.coerceAtLeast(30.0)
    private var elapsedMs = 0
    private var speechMs = 0
    private var silenceMs = 0
    private var run = 0
    var started = false
        private set

    val threshold: Double get() = maxOf(floor * ratio, minRms)

    fun isSpeech(rms: Double, zcr: Double): Boolean =
        rms > threshold && (zcr < 0.35 || rms > threshold * 2)

    fun push(frame: ShortArray, n: Int = frame.size): Result = push(Audio.rms(frame, n), Audio.zeroCrossings(frame, n))

    fun push(rms: Double, zcr: Double): Result {
        elapsedMs += frameMs
        val speech = isSpeech(rms, zcr)
        if (speech) {
            run++
            speechMs += frameMs
            silenceMs = 0
            if (run >= 2) started = true // two frames in a row (160 ms): a voice, not a click
        } else {
            run = 0
            silenceMs += frameMs
            if (!started) floor = floor * 0.9 + rms * 0.1 // still learning the room
        }
        return when {
            started && speechMs >= minSpeechMs && silenceMs >= endSilenceMs -> Result.END
            !started && elapsedMs >= noSpeechMs -> Result.NO_SPEECH
            elapsedMs >= maxMs -> if (started) Result.MAX else Result.NO_SPEECH
            else -> Result.CONTINUE
        }
    }
}
