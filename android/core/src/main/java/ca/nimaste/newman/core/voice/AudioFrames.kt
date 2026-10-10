package ca.nimaste.newman.core.voice

import kotlin.math.log10
import kotlin.math.sqrt

/**
 * Audio plumbing for the wall's voice (16 kHz, 16-bit mono throughout):
 * fixed-size frames out of whatever sizes the microphone hands over, loudness, and the
 * little-endian PCM bytes the hub takes (POST /api/voice/utterance, audio/L16).
 */
object Audio {
    const val RATE = 16_000
    /** openWakeWord works on 80 ms chunks. */
    const val FRAME = 1_280
    const val FRAME_MS = FRAME * 1000 / RATE

    /** Root mean square in sample units (0..32768). */
    fun rms(frame: ShortArray, n: Int = frame.size): Double {
        if (n <= 0) return 0.0
        var sum = 0.0
        for (i in 0 until n) {
            val s = frame[i].toDouble()
            sum += s * s
        }
        return sqrt(sum / n)
    }

    /** Share of neighbouring samples that change sign (hiss is high, voices are low). */
    fun zeroCrossings(frame: ShortArray, n: Int = frame.size): Double {
        if (n < 2) return 0.0
        var c = 0
        for (i in 1 until n) if ((frame[i] >= 0) != (frame[i - 1] >= 0)) c++
        return c.toDouble() / (n - 1)
    }

    /** Loudness for an animation: -60 dBFS (and below) = 0, -10 dBFS (and above) = 1. */
    fun level(frame: ShortArray, n: Int = frame.size): Float {
        val r = rms(frame, n)
        if (r < 1.0) return 0f
        val db = 20 * log10(r / 32768.0)
        return ((db + 60) / 50).toFloat().coerceIn(0f, 1f)
    }

    fun pcm16le(samples: ShortArray, n: Int = samples.size): ByteArray {
        val out = ByteArray(n * 2)
        for (i in 0 until n) {
            val s = samples[i].toInt()
            out[2 * i] = (s and 0xFF).toByte()
            out[2 * i + 1] = ((s shr 8) and 0xFF).toByte()
        }
        return out
    }

    fun toFloats(samples: ShortArray, n: Int = samples.size): FloatArray = FloatArray(n) { samples[it].toFloat() }
}

/** Cuts a stream of reads of any size into fixed frames, keeping the remainder for next time. */
class Framer(private val size: Int = Audio.FRAME) {
    private val carry = ShortArray(size)
    private var have = 0

    fun push(data: ShortArray, n: Int = data.size, out: (ShortArray) -> Unit) {
        var i = 0
        while (i < n) {
            val take = minOf(size - have, n - i)
            System.arraycopy(data, i, carry, have, take)
            have += take
            i += take
            if (have == size) {
                out(carry.copyOf())
                have = 0
            }
        }
    }

    fun reset() {
        have = 0
    }
}

/** What was said after the wake word, up to a limit (frames appended as they come). */
class Utterance(maxSeconds: Int = 10) {
    private val buf = ShortArray(Audio.RATE * maxSeconds)
    var size = 0
        private set

    fun add(frame: ShortArray) {
        val take = minOf(frame.size, buf.size - size)
        System.arraycopy(frame, 0, buf, size, take)
        size += take
    }

    fun bytes(): ByteArray = Audio.pcm16le(buf, size)
    val seconds: Double get() = size.toDouble() / Audio.RATE
    fun clear() {
        size = 0
    }
}

/**
 * The room's background loudness, from the last few seconds before the wake word (so the end of
 * speech is judged against the room, not against the first word after "Hey Newman").
 */
class NoiseFloor(private val frames: Int = 40) {
    private val values = DoubleArray(frames)
    private var count = 0
    private var next = 0

    fun add(rms: Double) {
        values[next] = rms
        next = (next + 1) % frames
        if (count < frames) count++
    }

    /** The quieter end of what was heard (20th percentile); [fallback] before anything was. */
    fun floor(fallback: Double = 200.0): Double {
        if (count == 0) return fallback
        val sorted = values.copyOf(count).sorted()
        return sorted[(count - 1) / 5]
    }
}
