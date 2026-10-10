package ca.nimaste.newman.wall

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import android.webkit.CookieManager
import ca.nimaste.newman.core.net.HubHttp
import ca.nimaste.newman.core.net.HubInterceptor
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.core.wall.BatteryCheckIn
import ca.nimaste.newman.core.wall.TabletBattery
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * One battery report (POST /api/tablet/battery), shared by the wall's own reporter (BatteryReporter,
 * screen on) and the background check-ins (BatteryAlarm's receiver and BatteryWorker, also with the screen
 * off): the battery from the sticky ACTION_BATTERY_CHANGED, whether the screen is on, whether the check-in
 * alarm is armed and when it's next due, and the request with the wall's session - the page's cookie for
 * the address or the tablet's token (HubHttp adds it, with the client certificate and Cloudflare's
 * headers). Also remembers when a report last got through.
 */
object BatteryPost {
    private val JSON = "application/json".toMediaType()
    private const val PREFS = "battery_report"
    private const val LAST_OK = "last_ok_at"

    /** The battery now (a sticky broadcast: no receiver needed), or null when it can't be read. */
    fun current(context: Context): TabletBattery? =
        context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))?.let { read(it) }

    fun read(i: Intent): TabletBattery? {
        val m = TabletBattery.MISSING
        return TabletBattery.fromExtras(
            level = i.getIntExtra(BatteryManager.EXTRA_LEVEL, m),
            scale = i.getIntExtra(BatteryManager.EXTRA_SCALE, m),
            status = i.getIntExtra(BatteryManager.EXTRA_STATUS, m),
            plugged = i.getIntExtra(BatteryManager.EXTRA_PLUGGED, m),
            temperature = i.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, m),
            voltage = i.getIntExtra(BatteryManager.EXTRA_VOLTAGE, m),
        )
    }

    fun screenOn(context: Context): Boolean =
        context.getSystemService(PowerManager::class.java)?.isInteractive ?: true

    /**
     * The request, or null when the wall isn't signed in (no cookie, no token) or has no hub.
     * [preferToken]: the background check-in uses the tablet's token when it has one, so it needn't
     * start the WebView just to read a cookie.
     */
    fun request(connection: ConnectionStore, body: String, preferToken: Boolean = false): Request? {
        val url = HubHttp.absolute(connection.serverUrl, TabletBattery.PATH) ?: return null
        val hasToken = !connection.token.isNullOrEmpty()
        val cookies = if (preferToken && hasToken) null else try {
            CookieManager.getInstance().getCookie(url.toString())
        } catch (e: Exception) {
            null // no WebView provider
        }
        val b = Request.Builder().url(url).post(body.toRequestBody(JSON))
        if (TabletBattery.hasSession(cookies)) {
            // The page's session cookie for this address (plus Cloudflare's), not a bearer token.
            b.header("Cookie", cookies!!).header(HubInterceptor.NO_AUTH, "1")
        } else if (!hasToken) {
            return null // not signed in yet
        } else if (!cookies.isNullOrEmpty()) {
            b.header("Cookie", cookies) // (Cloudflare's); HubHttp adds the tablet's token
        }
        // else: HubHttp adds the tablet's own token (kept from the page) - works on any address of the hub.
        return b.build()
    }

    /** The body for [battery] now, with where the background check-in stands (BatteryAlarm). */
    fun body(context: Context, battery: TabletBattery, screen: Boolean): String =
        battery.json(screen, BatteryAlarm.state(context))

    enum class Outcome { SENT, SKIPPED, FAILED }

    /** The background ones answer within Doze's ~10 s of network after a check-in alarm. */
    private val quick: OkHttpClient by lazy { WallApp.http.newBuilder().callTimeout(8, TimeUnit.SECONDS).build() }

    /**
     * One background report (the check-in alarm, WorkManager), blocking: [event] = the screen just went
     * off or the charger changed (always sent). [retryWithinMs]: after a failure that came quickly (the
     * network not let through yet), try once more if there's still this much time.
     */
    fun background(context: Context, event: Boolean, retryWithinMs: Long = 0L): Outcome {
        val screen = screenOn(context)
        if (!BatteryCheckIn.shouldSend(event, screen, sinceLast(context))) return Outcome.SKIPPED
        val battery = current(context) ?: return Outcome.SKIPPED
        val started = SystemClock.elapsedRealtime()
        var attempt = 0
        while (true) {
            // Not signed in: nothing to do.
            val request = request(ConnectionStore.get(context), body(context, battery, screen), preferToken = true)
                ?: return Outcome.SKIPPED
            try {
                quick.newCall(request).execute().use { r ->
                    if (r.isSuccessful) {
                        succeeded(context)
                        return Outcome.SENT
                    }
                    Log.w(TAG, "background battery report failed: HTTP ${r.code}")
                    return Outcome.FAILED // the hub answered: no point asking again now
                }
            } catch (e: IOException) {
                Log.w(TAG, "background battery report failed: ${e.javaClass.simpleName}: ${e.message}")
                attempt++
                val left = retryWithinMs - (SystemClock.elapsedRealtime() - started)
                if (attempt > 1 || left < 6_000L) return Outcome.FAILED
                try {
                    Thread.sleep(1_500L)
                } catch (ie: InterruptedException) {
                    return Outcome.FAILED
                }
            }
        }
    }

    private const val TAG = "NewmanWall"

    fun succeeded(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putLong(LAST_OK, System.currentTimeMillis()).apply()
    }

    /** Milliseconds since a report last got through, or null for never. */
    fun sinceLast(context: Context): Long? {
        val at = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getLong(LAST_OK, 0L)
        return if (at <= 0L) null else System.currentTimeMillis() - at
    }
}