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
 * accident, another app, Alexa or another assistant AssistantWatch didn't come back from - (ComeBack:
 * after the wall settings' minutes with no other app playing anything, when the screen wakes, and at
 * once when the screen goes off over the other app).
 *
 * "In front" is any Newman screen resumed (the wall, its settings, Connect), from the app's activity
 * lifecycle; leaving because the screen turned off doesn't count. Other apps' sound is every started
 * playback AudioManager reports (Android hides which app plays, so the wall's own - silent while
 * it's away - can't be told apart). Coming to the front from the background uses "Display over other
 * apps" (or device owner), like AssistantWatch. Decisions are logged at warning level (NewmanWall).
 *
 * The screen going off while another app is in front (Fire OS's own timeout - the wall only holds the
 * screen on while it is itself in front) would otherwise leave the tablet dark and in deep sleep:
 * the wall comes back and turns the screen on into its dimmed screen ([WallActivity.returnDimmed], a
 * short wake lock that wakes the screen; the wall's window then holds it on). Main thread.
 */
class ComeBackWatch(
    private val app: Application,
    private val settings: WallSettings,
    /** Whether the wall holds the screen on while it's in front (NewmanWall.keepAwake). */
    private val keepsScreenOn: () -> Boolean = { true },
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
        // The screen went off in the second before this (its broadcast found the wall not yet away).
        if (why == null && power?.isInteractive == false) screenWentOff("already off when the wall's leaving was confirmed")
        evaluate()
    }

    private fun screenWentOff(how: String) {
        val d = logic.screenOff(now(), keepsScreenOn())
        if (d.bringBack) bringBack(d.why, dimmed = d.dimmed)
        else if (logic.away) Log.w(TAG, "Come back: the screen went off ($how) - not coming back: ${d.why}")
    }

    private val playbackCallback = object : AudioManager.AudioPlaybackCallback() {
        override fun onPlaybackConfigChanged(configs: MutableList<AudioPlaybackConfiguration>) = playback(configs)
    }

    private val screen = object : BroadcastReceiver() {
        override fun onReceive(c: Context, intent: Intent) {
            if (intent.action == Intent.ACTION_SCREEN_OFF) {
                screenWentOff("screen off broadcast")
                return
            }
            // (Screen on and unlocked arrive together: one return. Also the screen this watch just
            // turned on itself for a dimmed return.)
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
        val f = IntentFilter(Intent.ACTION_SCREEN_ON).apply {
            addAction(Intent.ACTION_USER_PRESENT)
            addAction(Intent.ACTION_SCREEN_OFF)
        }
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

    private fun bringBack(reason: String, dimmed: Boolean = false) {
        broughtAt = now()
        beforeReturn()
        if (dimmed) WallActivity.returnDimmed() // before the screen comes on: no full-brightness wake
        bringWallToFront(app, TAG, "Come back: bringing the wall back" + (if (dimmed) ", dimmed" else "") + " - $reason")
        if (dimmed) wakeScreen()
    }

    /**
     * Turns the screen on (it is off) for the returning wall: a dim wake lock that wakes the device,
     * held for a few seconds - the wall's own window (FLAG_KEEP_SCREEN_ON) holds it on from there.
     */
    @Suppress("DEPRECATION") // SCREEN_DIM_WAKE_LOCK: still the way for an app to wake the screen itself
    private fun wakeScreen() {
        try {
            power?.newWakeLock(PowerManager.SCREEN_DIM_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP, "newman:comeback")
                ?.apply { setReferenceCounted(false) }
                ?.acquire(WAKE_HOLD_MS)
        } catch (e: Exception) {
            Log.w(TAG, "Come back: couldn't turn the screen on: $e")
        }
    }

    companion object {
        private const val TAG = "NewmanWall"
        private const val WAKE_HOLD_MS = 10_000L
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
