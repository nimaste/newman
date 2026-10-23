package ca.nimaste.newman.wall

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.util.Log
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.core.voice.Audio
import ca.nimaste.newman.core.voice.Endpointer
import ca.nimaste.newman.core.voice.Framer
import ca.nimaste.newman.core.voice.NoiseFloor
import ca.nimaste.newman.core.voice.Utterance
import ca.nimaste.newman.core.voice.VoicePageConfig
import ca.nimaste.newman.core.voice.WakeFeatures
import ca.nimaste.newman.core.voice.WakeTrigger
import java.io.File

/**
 * "Hey Newman" on the tablet itself. While the wall is in the foreground and the page says voice
 * is on, one background thread reads the microphone (16 kHz mono, VOICE_RECOGNITION - not a
 * privacy-sensitive source, so the system's own "Hey Google" can keep listening alongside) and runs
 * openWakeWord on every 80 ms. On the wake word: a chime, the screen wakes, the page shows
 * "Listening…" and what's said is recorded until the speaker stops (Endpointer, at most 8 s). The
 * audio goes to the page (which sends it to the hub and speaks the reply) and the microphone is
 * let go until the page says the reply is done (or 30 s pass). Nothing is recorded or sent before
 * the wake word; nothing is kept. While another app records (Alexa…) the microphone is let go
 * ([setYield], from AssistantWatch), so Android doesn't silence that app in favour of the wall.
 *
 * [toPage] gets the page events as JSON ({"type": "wake"|"level"|"audio"|"cancel"|"error", …});
 * [onWake] runs on the main thread when the wake word is heard.
 */
class VoiceListener(
    private val context: Context,
    connection: ConnectionStore,
    private val toPage: (String) -> Unit,
    private val onWake: () -> Unit,
) {
    private val store = WakeModelStore(context, connection)
    private val main = Handler(Looper.getMainLooper())
    private val lock = Object()

    @Volatile private var config = VoicePageConfig()
    @Volatile private var foreground = false
    @Volatile private var permitted = false
    @Volatile private var replyDone = false
    private var thread: Thread? = null
    @Volatile private var generation = 0          // bumped on every start / stop
    private val myGeneration = ThreadLocal<Int>()  // the generation a listening thread belongs to

    /** From the page (NewmanWall.voiceConfig). Main thread. */
    fun configure(cfg: VoicePageConfig) {
        config = cfg
        update()
    }

    fun setPermitted(on: Boolean) {
        permitted = on
        update()
    }

    fun setForeground(on: Boolean) {
        foreground = on
        update()
    }

    /**
     * The page finished answering (spoke the reply): listen for the wake word again - or, when Newman asked
     * a question ([listen]: "Which one: pasta or eggs?"), for the answer straight away, no wake word needed
     * (a chime, "Listening…"; nobody speaking within FOLLOW_UP_MS lets it go quietly).
     */
    fun replyFinished(listen: Boolean = false) {
        synchronized(lock) {
            followUp = listen
            replyDone = true
            lock.notifyAll()
        }
    }

    @Volatile private var followUp = false

    val wanted: Boolean get() = config.enabled

    @Volatile private var paused = false
    private var stopping: Thread? = null

    /** While a "Teach Hey Newman" clip is recorded (ClipRecorder) the wake word isn't listened for. Main thread. */
    fun pause(on: Boolean) {
        paused = on
        update()
    }

    /** The listening thread that is letting go of the microphone (to wait for), if any. */
    fun stoppingThread(): Thread? = stopping?.takeIf { it.isAlive }

    @Volatile private var yielded = false

    /** Another app is recording (Alexa…), or Android is silencing ours: let go of the microphone. Main thread. */
    fun setYield(on: Boolean) {
        yielded = on
        update()
    }

    private fun shouldRun() = config.enabled && foreground && permitted && !paused && !yielded

    private fun update() {
        val run = shouldRun()
        val t = thread
        if (run && (t == null || !t.isAlive)) {
            val g = ++generation
            thread = Thread({ myGeneration.set(g); loop() }, "newman-voice").apply {
                isDaemon = true
                start()
            }
        } else if (!run && t != null) {
            generation++
            t.interrupt()
            synchronized(lock) { lock.notifyAll() }
            stopping = t
            thread = null
        }
    }

    private fun page(json: String) = main.post { toPage(json) }

    // -- the listening thread -------------------------------------------------------------------------

    private var models: OnnxWakeModels? = null
    private var modelDir: File? = null

    private fun ensureModels(): Boolean {
        val wantVersion = config.version
        val dir = modelDir
        if (models != null && dir != null && (wantVersion.isBlank() || dir.name == wantVersion.filter { it.isLetterOrDigit() }.take(32))) {
            return true
        }
        val got = try {
            store.fetch()
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't get the wake-word model: $e")
            null
        } ?: return false
        models?.close()
        models = OnnxWakeModels(got.first)
        modelDir = got.first
        return true
    }

    private fun alive() = !Thread.currentThread().isInterrupted && shouldRun() && myGeneration.get() == generation

    private fun openMic(): AudioRecord? = openVoiceMic()

    companion object {
        private const val TAG = "NewmanVoice"
        private const val CHIME_FRAMES = 5 // 400 ms
        private const val REPLY_TIMEOUT_MS = 30_000L
        /** How long it listens for the answer to a question before letting it go. */
        private const val FOLLOW_UP_MS = 6_000

        /** The audio sessions of this app's own recordings (so AssistantWatch can tell them from other apps'). */
        val ourSessions: MutableSet<Int> = java.util.concurrent.ConcurrentHashMap.newKeySet()

        /** Stop and release a microphone from [openVoiceMic]. */
        fun closeVoiceMic(rec: AudioRecord) {
            val session = rec.audioSessionId
            try {
                rec.stop()
            } catch (e: Exception) {
                // already stopped
            }
            rec.release()
            ourSessions.remove(session)
        }

        /** The microphone as voice uses it: 16 kHz mono, VOICE_RECOGNITION, started; null if unavailable. */
        @SuppressLint("MissingPermission") // checked by the activity first
        fun openVoiceMic(): AudioRecord? {
            val min = AudioRecord.getMinBufferSize(Audio.RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            val size = maxOf(min, Audio.FRAME * 2 * 4)
            return try {
                val rec = if (Build.VERSION.SDK_INT >= 23) {
                    AudioRecord.Builder()
                        .setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
                        .setAudioFormat(AudioFormat.Builder().setSampleRate(Audio.RATE).setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setChannelMask(AudioFormat.CHANNEL_IN_MONO).build())
                        .setBufferSizeInBytes(size)
                        .apply { if (Build.VERSION.SDK_INT >= 30) setPrivacySensitive(false) }
                        .build()
                } else {
                    AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, Audio.RATE, AudioFormat.CHANNEL_IN_MONO,
                        AudioFormat.ENCODING_PCM_16BIT, size)
                }
                if (rec.state != AudioRecord.STATE_INITIALIZED) {
                    rec.release()
                    null
                } else {
                    ourSessions.add(rec.audioSessionId)
                    try {
                        rec.startRecording()
                    } catch (e: Exception) {
                        closeVoiceMic(rec)
                        throw e
                    }
                    rec
                }
            } catch (e: Exception) {
                Log.w(TAG, "Microphone: $e")
                null
            }
        }
    }

    private fun loop() {
        try {
            while (alive()) {
                if (!ensureModels()) {
                    sleepQuietly(60_000)
                    continue
                }
                listenOnce()
            }
        } catch (e: InterruptedException) {
            // stopped
        } catch (e: Exception) {
            Log.e(TAG, "Voice stopped", e)
        }
    }

    /** Listen until a request was captured and answered (or listening should stop). */
    private fun listenOnce() {
        val m = models ?: return
        val rec = openMic()
        if (rec == null) {
            page("""{"type":"error","message":"The microphone isn't available."}""")
            sleepQuietly(30_000)
            return
        }
        val features = WakeFeatures(m)
        val trigger = WakeTrigger(threshold = config.threshold)
        val noise = NoiseFloor()
        val framer = Framer()
        val buf = ShortArray(Audio.FRAME)
        var capturing = false
        var endpointer: Endpointer? = null
        val utterance = Utterance()
        var frameNo = 0
        var sent = false
        val version = config.version
        // The answer to a question Newman just asked: capturing from the start, no wake word.
        val answering = followUp
        followUp = false
        var quietCancel = answering
        if (answering) {
            capturing = true
            endpointer = Endpointer(noise.floor(), noSpeechMs = FOLLOW_UP_MS)
            Chime.play()
            main.post(onWake)
            page("""{"type":"wake","followUp":true}""")
        }
        try {
            while (alive() && !sent && (capturing || config.version == version)) { // a new model: load it
                val n = rec.read(buf, 0, buf.size)
                if (n <= 0) {
                    if (n < 0) return
                    continue
                }
                framer.push(buf, n) { frame ->
                    if (sent) return@push
                    if (!capturing) {
                        noise.add(Audio.rms(frame))
                        trigger.threshold = config.threshold
                        val score = features.push(frame)
                        if (score != null && trigger.push(score)) {
                            quietCancel = false
                            capturing = true
                            frameNo = 0
                            utterance.clear()
                            endpointer = Endpointer(noise.floor())
                            Chime.play()
                            main.post(onWake)
                            page("""{"type":"wake"}""")
                        }
                        return@push
                    }
                    utterance.add(frame)
                    frameNo++
                    if (frameNo % 2 == 0) page("""{"type":"level","value":${"%.3f".format(java.util.Locale.ROOT, Audio.level(frame))}}""")
                    // The chime itself (its first ~0.4 s) mustn't count as someone speaking.
                    val result = if (frameNo <= CHIME_FRAMES) Endpointer.Result.CONTINUE else endpointer!!.push(frame)
                    when (result) {
                        Endpointer.Result.END, Endpointer.Result.MAX -> {
                            val b64 = Base64.encodeToString(utterance.bytes(), Base64.NO_WRAP)
                            page("""{"type":"audio","rate":${Audio.RATE},"pcm":"$b64"}""")
                            sent = true
                        }
                        Endpointer.Result.NO_SPEECH -> {
                            // (no answer to a question: let it go quietly, back to the wake word)
                            page(if (quietCancel) """{"type":"cancel","quiet":true}""" else """{"type":"cancel"}""")
                            quietCancel = false
                            capturing = false
                            trigger.reset()
                        }
                        Endpointer.Result.CONTINUE -> {}
                    }
                }
            }
        } finally {
            closeVoiceMic(rec)
            // Let go mid-request (another app took the microphone, the wall left the screen): not "Listening…" any more.
            if (capturing && !sent) page("""{"type":"cancel"}""")
        }
        if (sent) waitForReply()
    }

    /** The microphone is free while the hub answers and the reply is spoken. */
    private fun waitForReply() {
        synchronized(lock) {
            replyDone = false
            val until = System.currentTimeMillis() + REPLY_TIMEOUT_MS
            while (!replyDone && alive()) {
                val left = until - System.currentTimeMillis()
                if (left <= 0) break
                lock.wait(left)
            }
        }
    }

    private fun sleepQuietly(ms: Long) {
        synchronized(lock) {
            val until = System.currentTimeMillis() + ms
            while (alive()) {
                val left = until - System.currentTimeMillis()
                if (left <= 0) break
                lock.wait(left)
            }
        }
    }

}
