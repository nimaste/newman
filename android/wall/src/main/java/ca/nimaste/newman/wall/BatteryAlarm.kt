package ca.nimaste.newman.wall

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import ca.nimaste.newman.core.wall.CheckInState
import ca.nimaste.newman.core.wall.CheckInTiming

/**
 * The battery check-in with the screen off that Doze can't hold back. WorkManager's job only runs in
 * Doze's maintenance windows - on the Fire tablet overnight those came at 23:44, 01:38 and 05:34 - so
 * the check-in is an alarm allowed to go off while idle: setExactAndAllowWhileIdle where the app may set
 * exact alarms without asking (CheckInTiming.exact: Fire OS 8 is Android 11), setAndAllowWhileIdle
 * otherwise, set again each time it goes off. Android lets one through Doze at most every 9 minutes
 * and lets the app use the network for ~10 seconds after it (a temporary allow-list entry), so the
 * receiver reports straight away, inside that window. No foreground service, no notification.
 *
 * Set on every app start (WallApp), after a restart or an update (BootReceiver), when the screen goes
 * off (BatteryWork.watch) and each time it goes off. A force stop clears it until the app next starts;
 * the report then says it isn't armed (no "background"), so the hub doesn't wait for check-ins that
 * won't come.
 */
object BatteryAlarm {
    private const val ACTION = "ca.nimaste.newman.wall.BATTERY_CHECK_IN"
    private const val PREFS = "battery_alarm"
    private const val DUE = "due_elapsed"
    private const val EXACT = "exact"
    private const val TAG = "NewmanWall"

    private fun pending(c: Context, flags: Int): PendingIntent? =
        PendingIntent.getBroadcast(c, 0, Intent(c, BatteryAlarmReceiver::class.java).setAction(ACTION),
            flags or PendingIntent.FLAG_IMMUTABLE)

    /** (Re)set the next check-in, an interval from now. */
    fun arm(context: Context) {
        val c = context.applicationContext
        val am = c.getSystemService(AlarmManager::class.java) ?: return
        val pi = pending(c, PendingIntent.FLAG_UPDATE_CURRENT) ?: return
        val mayExact = CheckInTiming.exact(Build.VERSION.SDK_INT, Build.VERSION.SDK_INT >= 31 && am.canScheduleExactAlarms())
        var exact = mayExact
        var due = SystemClock.elapsedRealtime() + CheckInTiming.delayMs(exact)
        try {
            if (exact) {
                try {
                    am.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, due, pi)
                } catch (e: SecurityException) { // exact alarms taken away meanwhile
                    exact = false
                    due = SystemClock.elapsedRealtime() + CheckInTiming.delayMs(false)
                    am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, due, pi)
                }
            } else {
                am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, due, pi)
            }
        } catch (e: Exception) {
            Log.w(TAG, "battery check-in alarm not set: ${e.javaClass.simpleName}: ${e.message}")
            return
        }
        c.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putLong(DUE, due).putBoolean(EXACT, exact).apply()
    }

    /** Where it stands: armed, how long until the next check-in at the latest, battery optimisation. */
    fun state(context: Context): CheckInState {
        val c = context.applicationContext
        val p = c.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        return CheckInTiming.state(
            pendingSet = pending(c, PendingIntent.FLAG_NO_CREATE) != null,
            dueAtMs = p.getLong(DUE, 0L),
            nowMs = SystemClock.elapsedRealtime(),
            exact = p.getBoolean(EXACT, false),
            dozeExempt = dozeExempt(c),
        )
    }

    /** Android lets the app ignore battery optimisation (on the deviceidle allow-list); null if unknown. */
    fun dozeExempt(context: Context): Boolean? =
        context.getSystemService(PowerManager::class.java)?.isIgnoringBatteryOptimizations(context.packageName)
}

/**
 * The check-in alarm went off: set the next one first, then report within Doze's network window
 * (goAsync: the alarm's wake lock is held until finish(); a short one of our own too). It also keeps the
 * wall up (keepWallUp): the screen back on, dimmed, if it's off; the wall started again if its process
 * was killed (this alarm starts the process).
 */
class BatteryAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val c = context.applicationContext
        BatteryAlarm.arm(c)
        keepWallUp(c, "check-in alarm", charger = false)
        val done = goAsync()
        val wake = c.getSystemService(PowerManager::class.java)?.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "NewmanWall:battery")
        wake?.acquire(WAKE_MS)
        Thread {
            try {
                BatteryPost.background(c, event = false, retryWithinMs = WINDOW_MS)
            } catch (e: Exception) {
                Log.w("NewmanWall", "battery check-in failed: ${e.javaClass.simpleName}: ${e.message}")
            } finally {
                try {
                    if (wake?.isHeld == true) wake.release()
                } catch (e: RuntimeException) {
                    // already timed out
                }
                done.finish()
            }
        }.start()
    }

    private companion object {
        /** allow_while_idle_whitelist_duration on the Fire tablet (and stock Android): 10 s of network. */
        const val WINDOW_MS = 10_000L
        const val WAKE_MS = 20_000L
    }
}
