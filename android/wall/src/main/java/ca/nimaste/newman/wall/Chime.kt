package ca.nimaste.newman.wall

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import kotlin.math.PI
import kotlin.math.exp
import kotlin.math.sin

/** The short, soft two-note chime played when the tablet hears "Hey Newman" (made here, no files). */
object Chime {
    private const val RATE = 22_050
    private val pcm: ShortArray by lazy { notes(listOf(0.0 to 880.0, 0.11 to 1318.5), 0.42) }

    private fun notes(list: List<Pair<Double, Double>>, seconds: Double): ShortArray {
        val n = (RATE * seconds).toInt()
        val out = DoubleArray(n)
        for ((start, freq) in list) {
            val s0 = (start * RATE).toInt()
            for (i in s0 until n) {
                val t = (i - s0).toDouble() / RATE
                val env = (1 - exp(-t * 400)) * exp(-t * 9)
                out[i] += sin(2 * PI * freq * t) * env * 0.22 + sin(4 * PI * freq * t) * env * 0.04
            }
        }
        return ShortArray(n) { (out[it].coerceIn(-1.0, 1.0) * 32767).toInt().toShort() }
    }

    /** [usage]: which volume it plays at - the wake chime's own, or media for the settings' test sound. */
    fun play(usage: Int = AudioAttributes.USAGE_ASSISTANCE_SONIFICATION) {
        try {
            val track = AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(usage)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build(),
                )
                .setAudioFormat(
                    AudioFormat.Builder().setSampleRate(RATE).setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build(),
                )
                .setTransferMode(AudioTrack.MODE_STATIC)
                .setBufferSizeInBytes(pcm.size * 2)
                .build()
            track.write(pcm, 0, pcm.size)
            track.setNotificationMarkerPosition(pcm.size - 1)
            track.setPlaybackPositionUpdateListener(object : AudioTrack.OnPlaybackPositionUpdateListener {
                override fun onMarkerReached(t: AudioTrack) {
                    t.release()
                }

                override fun onPeriodicNotification(t: AudioTrack) {}
            })
            track.play()
        } catch (e: Exception) {
            // no sound this time; the card on screen still shows it's listening
        }
    }
}
