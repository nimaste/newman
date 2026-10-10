package ca.nimaste.newman.wall

import android.media.AudioRecord
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.util.Log
import ca.nimaste.newman.core.voice.Audio
import ca.nimaste.newman.core.voice.ClipCapture
import ca.nimaste.newman.core.voice.Framer

/**
 * "Teach Hey Newman" recordings (the page: NewmanWall.recordClip(maxMs, fixed) / stopClip()). One
 * recording at a time on its own thread: the wake-word listener is paused and has let go of the
 * microphone first, the same microphone setup records (16 kHz mono), ClipCapture decides when it ends
 * (a spoken "Hey Newman": first word to the pause after it; the talking step: a fixed length), and
 * the page gets {"type":"clip","pcm","rate","ms"} - or "clip-none" (nobody spoke) / "clip-error" - plus
 * "clip-level" for its meter. The microphone is always released, and the listener resumed, afterwards.
 * Nothing is kept on the tablet.
 */
class ClipRecorder(
    private val toPage: (String) -> Unit,
    private val listener: VoiceListener,
) {
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var generation = 0
    @Volatile private var stopAsked = false
    private var thread: Thread? = null

    private fun page(json: String) = main.post { toPage(json) }

    /** Main thread. A recording already under way is dropped first. */
    fun start(maxMs: Int, fixed: Boolean) {
        val g = ++generation
        stopAsked = false
        listener.pause(true)
        val waitFor = listOfNotNull(thread?.takeIf { it.isAlive }, listener.stoppingThread())
        thread = Thread({ record(g, maxMs, fixed, waitFor) }, "newman-clip").apply {
            isDaemon = true
            start()
        }
    }

    /** The page's "stop": a fixed-length recording hands over what it has; a spoken one is dropped. */
    fun stop() {
        stopAsked = true
    }

    /** Drop any recording (the wall left the screen, another page loaded). Main thread. */
    fun cancel() {
        if (thread?.isAlive == true) generation++
        thread = null
        listener.pause(false)
    }

    private fun record(g: Int, maxMs: Int, fixed: Boolean, waitFor: List<Thread>) {
        try {
            waitFor.forEach { it.join(1_500) } // the listener / an older recording let go of the microphone
            var rec: AudioRecord? = null
            for (attempt in 0 until 6) {
                if (generation != g) return
                rec = VoiceListener.openVoiceMic()
                if (rec != null) break
                Thread.sleep(250)
            }
            if (rec == null) {
                if (generation == g) page("""{"type":"clip-error","message":"The microphone isn't available."}""")
                return
            }
            val cap = ClipCapture(maxMs, fixed)
            val framer = Framer()
            val buf = ShortArray(Audio.FRAME)
            var frames = 0
            var failed = false
            var dropped = false
            try {
                while (generation == g && cap.result == ClipCapture.Result.CONTINUE) {
                    if (stopAsked) {
                        if (fixed) cap.finishEarly() else dropped = true
                        break
                    }
                    val n = rec.read(buf, 0, buf.size)
                    if (n < 0) {
                        failed = true
                        break
                    }
                    framer.push(buf, n) { frame ->
                        if (cap.result == ClipCapture.Result.CONTINUE) {
                            cap.push(frame)
                            if (++frames % 2 == 0) {
                                page("""{"type":"clip-level","value":${"%.3f".format(java.util.Locale.ROOT, Audio.level(frame))}}""")
                            }
                        }
                    }
                }
            } finally {
                VoiceListener.closeVoiceMic(rec)
            }
            if (generation != g || dropped) return
            when {
                failed -> page("""{"type":"clip-error","message":"The microphone stopped."}""")
                cap.result == ClipCapture.Result.DONE && cap.keptMs > 0 -> {
                    val b64 = Base64.encodeToString(cap.pcm(), Base64.NO_WRAP)
                    page("""{"type":"clip","rate":${Audio.RATE},"ms":${cap.keptMs},"pcm":"$b64"}""")
                }
                else -> page("""{"type":"clip-none"}""")
            }
        } catch (e: InterruptedException) {
            // dropped
        } catch (e: Exception) {
            Log.w("NewmanClip", "Recording failed: $e")
            if (generation == g) page("""{"type":"clip-error","message":"Recording failed."}""")
        } finally {
            main.post { if (generation == g) listener.pause(false) }
        }
    }
}
