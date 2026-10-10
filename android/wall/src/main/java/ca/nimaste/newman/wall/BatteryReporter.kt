package ca.nimaste.newman.wall

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import androidx.core.content.ContextCompat
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.core.wall.BatterySchedule
import ca.nimaste.newman.core.wall.TabletBattery
import okhttp3.Call
import okhttp3.Callback
import okhttp3.Response
import java.io.IOException

/**
 * Tells the hub how the tablet's battery is doing (POST /api/tablet/battery) while the screen is on:
 * when the wall starts and the page is signed in, every 5 minutes, early when charging starts/stops or
 * the level moves 5 points, and at once when the screen comes back on. With the screen off the
 * background check-ins (BatteryAlarm, BatteryWork) report instead - this one stays quiet then. The request (session
 * cookie or the tablet's token, client certificate, Cloudflare) is BatteryPost's. A failed report (401,
 * an older hub's 404, no network) is logged (NewmanWall) and tried again an interval later. Lives as
 * long as the activity.
 */
class BatteryReporter(private val context: Context, private val connection: ConnectionStore) {

    private val main = Handler(Looper.getMainLooper())
    private val schedule = BatterySchedule()
    private var latest: TabletBattery? = null
    private var running = false

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            BatteryPost.read(intent)?.let { latest = it }
            check()
        }
    }

    private val screen = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action != Intent.ACTION_SCREEN_ON) return
            schedule.forceNext() // "screen on" for the hub straight away
            check()
        }
    }

    private val tick = object : Runnable {
        override fun run() {
            check()
            main.postDelayed(this, TICK_MS)
        }
    }

    fun start() {
        if (running) return
        running = true
        // Sticky: the current state comes back at once (and onReceive follows for it too). A
        // system-only broadcast, so no exported flag is needed (Android 14+) and no permission.
        val sticky = context.registerReceiver(receiver, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        sticky?.let { BatteryPost.read(it) }?.let { latest = it }
        ContextCompat.registerReceiver(context, screen, IntentFilter(Intent.ACTION_SCREEN_ON), ContextCompat.RECEIVER_NOT_EXPORTED)
        main.postDelayed(tick, TICK_MS)
    }

    fun stop() {
        if (!running) return
        running = false
        main.removeCallbacks(tick)
        for (r in listOf(receiver, screen)) {
            try {
                context.unregisterReceiver(r)
            } catch (e: IllegalArgumentException) {
                // not registered
            }
        }
    }

    /** Look now (the page may have just signed in). Main thread. */
    fun check() {
        if (!running) return
        if (!BatteryPost.screenOn(context)) return // the background check-in's job (BatteryWork)
        val battery = latest ?: return
        val now = SystemClock.elapsedRealtime()
        if (!schedule.shouldSend(now, battery)) return
        // Not signed in yet: look again next tick, no request.
        val request = BatteryPost.request(connection, BatteryPost.body(context, battery, screen = true)) ?: return
        val host = request.url.host
        schedule.started()
        WallApp.http.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                Log.w(TAG, "battery report to $host failed: ${e.javaClass.simpleName}: ${e.message}")
                main.post { schedule.failed(SystemClock.elapsedRealtime()) }
            }

            override fun onResponse(call: Call, response: Response) {
                val ok = response.use { it.isSuccessful }
                if (ok) BatteryPost.succeeded(context) else Log.w(TAG, "battery report to $host failed: HTTP ${response.code}")
                main.post {
                    val t = SystemClock.elapsedRealtime()
                    if (ok) schedule.succeeded(t, battery) else schedule.failed(t)
                }
            }
        })
    }

    companion object {
        /** The schedule decides; a cheap look once a minute keeps "every 5 minutes" close. */
        private const val TICK_MS = 60_000L
        private const val TAG = "NewmanWall"
    }
}