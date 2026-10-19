package ca.nimaste.newman.wall

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioManager
import android.media.AudioPlaybackConfiguration
import android.media.AudioRecordingConfiguration
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import androidx.core.content.ContextCompat
import ca.nimaste.newman.core.voice.AssistantReturn
import ca.nimaste.newman.core.voice.MicGate
import ca.nimaste.newman.core.voice.Recording

/**
 * Plays nicely with the tablet's other voice assistant (Alexa on a Fire tablet):
 *  - lets go of the microphone the moment another app starts recording, or Android silences ours, and
 *    listens again once it's free (MicGate, from AudioManager's recording callback) - so "Alexa, …" is
 *    heard the first time;
 *  - when another app's screen came over the wall (an assistant: Fire OS hides Alexa's own recording,
 *    so it's told by how the wall left the front), brings the wall back once the assistant's spoken answer
 *    has started and finished (AssistantReturn, from AudioManager's playback callback), if the tablet's
 *    "Return to Newman after Alexa" setting is on. Starting an activity from the background needs
 *    "Display over other apps" (or the app being device owner).
 * Return decisions are logged at warning level (NewmanVoice): Fire OS hides info-level logs.
 * Main thread.
 */
class AssistantWatch(
    private val context: Context,
    private val voice: VoiceListener,
    private val settings: WallSettings,
    /** Just before the wall brings itself back. */
    private val beforeReturn: () -> Unit = {},
) {
    private val am = context.getSystemService(AudioManager::class.java)
    private val power = context.getSystemService(PowerManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    private val gate = MicGate()
    private val back = AssistantReturn()
    private var yielded = false
    private var started = false
    private var othersPlaying = false
    /** What was already playing when the wall left the front (its own sounds, music…): not the assistant. */
    private var baseline: Set<AudioPlaybackConfiguration> = emptySet()

    private val recordingCallback = object : AudioManager.AudioRecordingCallback() {
        override fun onRecordingConfigChanged(configs: MutableList<AudioRecordingConfiguration>) = recordings(configs)
    }
    private val playbackCallback = object : AudioManager.AudioPlaybackCallback() {
        override fun onPlaybackConfigChanged(configs: MutableList<AudioPlaybackConfiguration>) = playback(configs)
    }
    /** Home / Recents: Android tells every app to close its system dialogs, with the reason. */
    private val closeDialogs = object : BroadcastReceiver() {
        override fun onReceive(c: Context, intent: Intent) {
            val reason = intent.getStringExtra("reason")
            if (!AssistantReturn.isUserLeaveReason(reason)) return
            val cancelled = back.userLeft(now())
            Log.w(TAG, "Return: system button \"$reason\"" + if (cancelled) " - not an assistant, not coming back" else "")
            evaluate()
        }
    }
    private val tick = Runnable { evaluate() }

    private fun now() = SystemClock.elapsedRealtime()

    fun start() {
        if (started || am == null) return
        started = true
        am.registerAudioRecordingCallback(recordingCallback, main)
        am.registerAudioPlaybackCallback(playbackCallback, main)
        @Suppress("DEPRECATION")
        ContextCompat.registerReceiver(context, closeDialogs, IntentFilter(Intent.ACTION_CLOSE_SYSTEM_DIALOGS), ContextCompat.RECEIVER_EXPORTED)
        recordings(am.activeRecordingConfigurations)
    }

    fun stop() {
        if (!started) return
        started = false
        am?.unregisterAudioRecordingCallback(recordingCallback)
        am?.unregisterAudioPlaybackCallback(playbackCallback)
        try {
            context.unregisterReceiver(closeDialogs)
        } catch (e: Exception) {
            // not registered
        }
        main.removeCallbacks(tick)
    }

    /** The wall is in front. */
    fun entered() {
        if (back.armed) Log.w(TAG, "Return: the wall is in front again - no longer waiting")
        back.entered()
        evaluate()
    }

    /**
     * The wall left the front (onPause). [ownScreen]: it opened a screen of its own (settings, a
     * permission or PIN prompt). [userLeaveHint] is only logged: Android gives it for any app's
     * activity started over the wall too, not only for Home.
     */
    fun left(ownScreen: Boolean, closing: Boolean, userLeaveHint: Boolean) {
        val all = am?.activePlaybackConfigurations ?: emptyList()
        baseline = all.filter { started(it) }.toSet()
        val screenOn = power?.isInteractive != false
        back.quietMs = settings.returnQuietSeconds * 1000L
        val why = if (!settings.returnAfterAssistant) "the setting is off"
        else back.left(now(), AssistantReturn.leaveBlocker(screenOn = screenOn, ownScreen = ownScreen, closing = closing))
        Log.w(TAG, "Return: the wall left the front (user leave hint: $userLeaveHint, screen on: $screenOn, " +
            "already playing: ${baseline.size}) - " + if (why == null) "another app came over it: waiting for its answer to play (up to 30 s)" else "not coming back: $why")
        playback(all)
    }

    private fun recordings(configs: List<AudioRecordingConfiguration>) {
        val recs = configs.map {
            Recording(
                id = it.clientAudioSessionId,
                ours = it.clientAudioSessionId in VoiceListener.ourSessions,
                silenced = Build.VERSION.SDK_INT >= 29 && it.isClientSilenced,
                hotword = it.clientAudioSource == HOTWORD,
            )
        }
        Log.i(TAG, "Recordings: " + recs.joinToString { r ->
            "${if (r.ours) "ours" else "app"}#${r.id}" + (if (r.hotword) " hotword" else "") + (if (r.silenced) " silenced" else "")
        }.ifEmpty { "none" })
        gate.update(recs, now())
        evaluate()
    }

    private fun started(c: AudioPlaybackConfiguration) = AssistantReturn.isStarted(c.toString())

    private fun playback(configs: List<AudioPlaybackConfiguration>) {
        val others = configs.filter { started(it) && it !in baseline }
        val on = others.isNotEmpty()
        if (on != othersPlaying && back.armed) {
            Log.w(TAG, "Return: other apps " + if (on) "playing: " + others.joinToString { describe(it) } else "stopped playing")
        }
        othersPlaying = on
        val wasStarted = back.answerStarted
        back.othersPlaying(on, now())
        if (back.armed && !wasStarted && back.answerStarted) {
            Log.w(TAG, "Return: the answer started playing - coming back once nothing else has played for 6 s")
        }
        evaluate()
    }

    private fun describe(c: AudioPlaybackConfiguration): String {
        val a = c.audioAttributes
        return "usage ${a.usage}/content ${a.contentType}"
    }

    private fun evaluate() {
        main.removeCallbacks(tick)
        val t = now()
        val hold = !gate.mayRecord(t)
        if (hold != yielded) {
            yielded = hold
            Log.i(TAG, if (hold) "Yielding the microphone: ${gate.reason}" else "Microphone free again: listening for the wake word")
            voice.setYield(hold)
        }
        when (back.check(t)) {
            AssistantReturn.Action.RETURN -> if (settings.returnAfterAssistant) returnToWall(back.returnReason)
                else Log.w(TAG, "Return: not coming back - the setting was turned off")
            AssistantReturn.Action.GIVE_UP -> Log.w(TAG, "Return: not coming back - another app was still playing after 2 minutes")
            AssistantReturn.Action.NONE -> {}
        }
        val next = listOfNotNull(gate.recheckIn(t), back.nextCheckIn(t)).minOrNull()
        if (next != null) main.postDelayed(tick, next)
    }

    private fun returnToWall(reason: String) {
        beforeReturn()
        bringWallToFront(context, TAG, "Return: bringing the wall back - $reason")
    }

    companion object {
        private const val TAG = "NewmanVoice"
        private const val HOTWORD = 1999 // MediaRecorder.AudioSource.HOTWORD (a system API)
    }
}
