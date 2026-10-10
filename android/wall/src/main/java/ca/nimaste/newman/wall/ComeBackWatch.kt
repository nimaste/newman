package ca.nimaste.newman.wall

import android.app.Activity
import android.app.Application
import android.app.admin.DevicePolicyManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioManager
import android.media.AudioPlaybackConfiguration
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import androidx.core.content.ContextCompat
import ca.nimaste.newman.core.voice.AssistantReturn
import ca.nimaste.newman.core.wall.ComeBack

/**
 * Brings the wall back to the front by itself after it left for any reason - Home or Recents by
 * accident, another app, an assistant AssistantWatch didn't come back from - (ComeBack: after the
 * wall settings' minutes with no other app playing anything, and when the screen wakes).
 *
 * "In front" is any Newman screen resumed (the wall, its settings, Connect), from the app's activity
 * lifecycle; leaving because the screen turned off doesn't count. Other apps' sound is every started
 * playback AudioManager reports (Android hides which app plays, so the wall's own - silent while
 * it's away - can't be told apart). Coming to the front from the background uses "Display over other
 * apps" (or device owner), like AssistantWatch. Decisions are logged at warning level (NewmanWall).
 * Main thread.
 */
class ComeBackWatch(
    private val app: Application,
    private val settings: WallSettings,
    /** Just before the wall brings itself back. */
    private val beforeReturn: () -> Unit = {},
) {
    private val am = app.getSystemService(AudioManager::class.java)
    private val power = app.getSystemService(PowerManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    private val logic = ComeBack()
    private val resumed = HashSet<Int>()
    private var started = false
    private var screenOnAtPause = true
    private var broughtAt = -10_000L

    private fun now() = SystemClock.elapsedRealtime()

    private fun options() = ComeBack.Options(
        afterLeaving = settings.comeBackAfterLeaving,
        minutes = settings.comeBackMinutes,
        onWake = settings.comeBackOnWake,
    )

    private val lifecycle = object : Application.ActivityLifecycleCallbacks {
        override fun onActivityResumed(a: Activity) {
            resumed += System.identityHashCode(a)
            main.removeCallbacks(confirmLeft)
            if (logic.away) Log.w(TAG, "Come back: a Newman screen is in front again (${a.javaClass.simpleName}) - no longer waiting")
            logic.entered()
            evaluate()
        }

        override fun onActivityPaused(a: Activity) {
            resumed -= System.identityHashCode(a)
            if (resumed.isNotEmpty()) return
            screenOnAtPause = power?.isInteractive != false
            // Moving between the wall's own screens passes through "none resumed" for a moment.
            main.removeCallbacks(confirmLeft)
            main.postDelayed(confirmLeft, 1_000)
        }

        override fun onActivityCreated(a: Activity, b: Bundle?) {}
        override fun onActivityStarted(a: Activity) {}
        override fun onActivityStopped(a: Activity) {}
        override fun onActivitySaveInstanceState(a: Activity, b: Bundle) {}
        override fun onActivityDestroyed(a: Activity) {}
    }

    private val confirmLeft = Runnable {
        if (resumed.isNotEmpty() || logic.away) return@Runnable
        val why = logic.left(now(), screenOnAtPause)
        val o = options()
        Log.w(TAG, "Come back: the wall left the front - " + when {
            why != null -> "not waiting: $why"
            !o.afterLeaving -> "\"Come back after leaving\" is off" + if (o.onWake) " (still coming back when the screen wakes)" else ""
            else -> "coming back after ${ComeBack.minutesSetting(o.minutes)} min with no other app playing" +
                if (logic.isPlaying) " (something is playing now)" else ""
        })
        evaluate()
    }

    private val playbackCallback = object : AudioManager.AudioPlaybackCallback() {
        override fun onPlaybackConfigChanged(configs: MutableList<AudioPlaybackConfiguration>) = playback(configs)
    }

    private val screen = object : BroadcastReceiver() {
        override fun onReceive(c: Context, intent: Intent) {
            // (Screen on and unlocked arrive together: one return.)
            if (!logic.away || now() - broughtAt < 3_000) return
            val d = logic.screenWoke(now(), options())
            if (d.bringBack) bringBack(d.why) else Log.w(TAG, "Come back: the screen woke (${intent.action}) - not coming back: ${d.why}")
        }
    }

    private val tick = Runnable { evaluate() }

    fun start() {
        if (started) return
        started = true
        app.registerActivityLifecycleCallbacks(lifecycle)
        am?.registerAudioPlaybackCallback(playbackCallback, main)
        val f = IntentFilter(Intent.ACTION_SCREEN_ON).apply { addAction(Intent.ACTION_USER_PRESENT) }
        ContextCompat.registerReceiver(app, screen, f, ContextCompat.RECEIVER_NOT_EXPORTED)
        am?.let { playback(it.activePlaybackConfigurations) }
    }

    fun stop() {
        if (!started) return
        started = false
        app.unregisterActivityLifecycleCallbacks(lifecycle)
        am?.unregisterAudioPlaybackCallback(playbackCallback)
        try {
            app.unregisterReceiver(screen)
        } catch (e: Exception) {
            // not registered
        }
        main.removeCallbacks(tick)
        main.removeCallbacks(confirmLeft)
    }

    private fun playback(configs: List<AudioPlaybackConfiguration>) {
        val on = configs.any { AssistantReturn.isStarted(it.toString()) }
        if (on != logic.isPlaying && logic.away) {
            Log.w(TAG, "Come back: other apps " + if (on) "playing - waiting until they stop" else "stopped playing - the wait starts over")
        }
        logic.othersPlaying(on, now())
        evaluate()
    }

    private fun evaluate() {
        main.removeCallbacks(tick)
        val d = logic.check(now(), options())
        if (d.bringBack) bringBack(d.why)
        d.recheckInMs?.let { main.postDelayed(tick, it.coerceAtLeast(1_000)) }
    }

    private fun bringBack(reason: String) {
        broughtAt = now()
        beforeReturn()
        bringWallToFront(app, TAG, "Come back: bringing the wall back - $reason")
    }

    companion object {
        private const val TAG = "NewmanWall"
    }
}

/**
 * Brings WallActivity to the front from the background (singleTask: the wall's own settings on top of
 * it close). Needs "Display over other apps" or device owner on Android 10+; logged as [message].
 */
fun bringWallToFront(context: Context, tag: String, message: String) {
    val overlay = Settings.canDrawOverlays(context)
    val owner = context.getSystemService(DevicePolicyManager::class.java)?.isDeviceOwnerApp(context.packageName) == true
    Log.w(tag, "$message (display over other apps: $overlay, device owner: $owner)")
    try {
        context.startActivity(Intent(context, WallActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
    } catch (e: Exception) {
        Log.w(tag, "Couldn't bring the wall back: $e")
    }
}
