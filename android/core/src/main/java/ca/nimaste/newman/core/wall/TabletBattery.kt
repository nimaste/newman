package ca.nimaste.newman.core.wall

import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * The wall tablet's battery as the hub wants it: POST /api/tablet/battery with
 * {"level": 0-100, "charging": bool, "plugged": "ac"|"usb"|"wireless"|"none",
 *  "temperatureC": float|null, "voltageMv": int|null}, plus - from a wall that says so - "screen": "on"|"off",
 * "background": true (its check-in alarm is set: it keeps reporting with the screen off, so the hub waits
 * longer before calling it offline), "nextReportIn" (seconds until the next check-in at the latest) and
 * "dozeExempt" (Android lets it ignore battery optimisation).
 */
data class TabletBattery(
    val level: Int,
    val charging: Boolean,
    val plugged: String,
    val temperatureC: Double?,
    val voltageMv: Int?,
) {
    fun json(screen: Boolean? = null, background: Boolean = false, nextReportIn: Int? = null, dozeExempt: Boolean? = null): String =
        buildJsonObject {
            put("level", level)
            put("charging", charging)
            put("plugged", plugged)
            put("temperatureC", temperatureC?.let { JsonPrimitive(it) } ?: JsonNull)
            put("voltageMv", voltageMv?.let { JsonPrimitive(it) } ?: JsonNull)
            if (screen != null) put("screen", if (screen) "on" else "off")
            if (background) put("background", true)
            if (background && nextReportIn != null) put("nextReportIn", nextReportIn)
            if (dozeExempt != null) put("dozeExempt", dozeExempt)
        }.toString()

    /** The body with where the background check-in stands ([CheckInState]). */
    fun json(screen: Boolean, checkIn: CheckInState): String =
        json(screen = screen, background = checkIn.armed, nextReportIn = checkIn.nextReportInS, dozeExempt = checkIn.dozeExempt)

    companion object {
        const val PATH = "api/tablet/battery"
        const val MISSING = Int.MIN_VALUE

        // android.os.BatteryManager values (copied so this stays plain, unit-testable Kotlin).
        const val STATUS_CHARGING = 2
        const val STATUS_FULL = 5
        const val PLUGGED_AC = 1
        const val PLUGGED_USB = 2
        const val PLUGGED_WIRELESS = 4
        const val PLUGGED_DOCK = 8

        /**
         * From ACTION_BATTERY_CHANGED's extras (pass [MISSING] for an absent extra). Null when the
         * intent has no usable level. Temperature comes in tenths of a degree; voltage in mV
         * (some devices report volts, which is converted).
         */
        fun fromExtras(level: Int, scale: Int, status: Int, plugged: Int, temperature: Int, voltage: Int): TabletBattery? {
            if (level == MISSING || level < 0) return null
            val pct = if (scale > 0) (level * 100.0 / scale).roundToInt() else level
            val source = when {
                plugged == MISSING || plugged <= 0 -> "none"
                (plugged and (PLUGGED_AC or PLUGGED_DOCK)) != 0 -> "ac"
                (plugged and PLUGGED_USB) != 0 -> "usb"
                (plugged and PLUGGED_WIRELESS) != 0 -> "wireless"
                else -> "none"
            }
            val mv = when {
                voltage == MISSING || voltage <= 0 -> null
                voltage < 100 -> voltage * 1000 // reported in volts
                else -> voltage
            }
            return TabletBattery(
                level = pct.coerceIn(0, 100),
                charging = status == STATUS_CHARGING || status == STATUS_FULL,
                plugged = source,
                temperatureC = if (temperature == MISSING) null else temperature / 10.0,
                voltageMv = mv,
            )
        }

        /** True when a cookie header ("a=1; newman_session=x") carries the hub's session. */
        fun hasSession(cookieHeader: String?): Boolean =
            cookieHeader?.split(';')?.any { c ->
                val parts = c.trim().split('=', limit = 2)
                parts.size == 2 && parts[0] == SESSION_COOKIE && parts[1].isNotEmpty()
            } == true

        const val SESSION_COOKIE = "newman_session"
    }
}

/**
 * When to send: at once the first time, then every [intervalMs], and early when charging starts or
 * stops or the level has moved [levelStep] points since the last report. After a failed send
 * (signed out, older hub, no network) it waits a full interval before trying again.
 */
class BatterySchedule(
    private val intervalMs: Long = 5 * 60_000L,
    private val levelStep: Int = 5,
    /** Timer ticks aren't exact: a report due within this much counts as due. */
    private val slackMs: Long = 15_000L,
) {
    var lastSent: TabletBattery? = null
        private set
    private var lastSentAt = 0L
    private var lastFailureAt: Long? = null
    var inFlight = false
        private set

    fun shouldSend(now: Long, current: TabletBattery): Boolean {
        if (inFlight) return false
        lastFailureAt?.let { if (now - it < intervalMs - slackMs) return false }
        val last = lastSent ?: return true
        if (now - lastSentAt >= intervalMs - slackMs) return true
        if (current.charging != last.charging) return true
        return abs(current.level - last.level) >= levelStep
    }

    /** The next look sends whatever the timer says (the screen just came back on). */
    fun forceNext() {
        lastSent = null
        lastFailureAt = null
    }

    fun started() {
        inFlight = true
    }

    fun succeeded(now: Long, sent: TabletBattery) {
        inFlight = false
        lastSent = sent
        lastSentAt = now
        lastFailureAt = null
    }

    fun failed(now: Long) {
        inFlight = false
        lastFailureAt = now
    }
}

/** Where the background check-in stands, as the report tells the hub. */
data class CheckInState(val armed: Boolean, val nextReportInS: Int?, val dozeExempt: Boolean?)

/**
 * When the check-in alarm goes off (the wall's BatteryAlarm: an alarm allowed to go off in Doze, set again
 * each time). Exact where the app may set exact alarms without asking - below Android 12 (Fire OS 8 is
 * Android 11), or on 12 / 13 with SCHEDULE_EXACT_ALARM granted at install - every 15 minutes. Otherwise
 * inexact, asked for every 10 minutes: Android may deliver an inexact alarm up to [INEXACT_WINDOW_MS]
 * late. Either way Doze lets one through at most every 9 minutes per app, which both clear.
 */
object CheckInTiming {
    const val EXACT_MS = 15 * 60_000L
    const val INEXACT_MS = 10 * 60_000L
    const val INEXACT_WINDOW_MS = 10 * 60_000L
    /** Doze's limit on alarms allowed while idle (allow_while_idle_long_time). */
    const val WHILE_IDLE_MIN_MS = 9 * 60_000L

    /** May it use an exact alarm: [canScheduleExact] is AlarmManager.canScheduleExactAlarms() (12+). */
    fun exact(sdk: Int, canScheduleExact: Boolean): Boolean = sdk < 31 || canScheduleExact

    fun delayMs(exact: Boolean): Long = if (exact) EXACT_MS else INEXACT_MS

    /** Seconds until the check-in at the latest (what the hub is told), from when it is due. */
    fun nextReportInS(nowMs: Long, dueAtMs: Long, exact: Boolean): Int {
        val wait = (dueAtMs - nowMs).coerceAtLeast(0L) + if (exact) 0L else INEXACT_WINDOW_MS
        return ((wait + 999L) / 1000L).toInt()
    }

    /**
     * Armed: the alarm is set ([pendingSet]: its PendingIntent exists - a force stop or a restart clears
     * it) and isn't overdue by more than its own window plus a full interval (then it was lost).
     */
    fun armed(pendingSet: Boolean, dueAtMs: Long, nowMs: Long, exact: Boolean): Boolean =
        pendingSet && dueAtMs > 0L && nowMs - dueAtMs <= delayMs(exact) + if (exact) 0L else INEXACT_WINDOW_MS

    fun state(pendingSet: Boolean, dueAtMs: Long, nowMs: Long, exact: Boolean, dozeExempt: Boolean?): CheckInState {
        val armed = armed(pendingSet, dueAtMs, nowMs, exact)
        return CheckInState(armed, if (armed) nextReportInS(nowMs, dueAtMs, exact) else null, dozeExempt)
    }
}

/**
 * The background check-in (the alarm or WorkManager, also with the screen off): whether this run reports. An event
 * (the screen went off, the charger was plugged in or out) always does. The periodic one (every 15
 * minutes) does with the screen off, or when nothing has reported for [quietMs] - with the screen on the
 * wall itself reports every 5 minutes, so the periodic one is then only a fallback.
 */
object BatteryCheckIn {
    const val PERIOD_MINUTES = 15L
    const val QUIET_MS = 10 * 60_000L

    fun shouldSend(event: Boolean, screenOn: Boolean, sinceLastReportMs: Long?, quietMs: Long = QUIET_MS): Boolean =
        event || !screenOn || sinceLastReportMs == null || sinceLastReportMs < 0 || sinceLastReportMs >= quietMs
}
