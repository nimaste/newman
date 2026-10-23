package ca.nimaste.newman.core.wall

/**
 * The wall settings' "Keep the alarm volume at full" (Android): Alexa on a Fire tablet turns the alarm
 * volume down each time one of her alarms or timers rings (25 to 17, again every ~6 s round), so with the
 * switch on the wall app puts it straight back to full. It only ever raises the alarm volume.
 */
object AlarmVolumeKeep {
    /** At most one "put back to full" log line this often (a ringing Alexa alarm lowers it every ~6 s). */
    const val LOG_EVERY_MS = 60_000L

    /** Put the alarm volume back to [max]? Only with the switch [on] and [current] below a real maximum. */
    fun shouldRestore(on: Boolean, current: Int, max: Int): Boolean = on && max > 0 && current < max

    /** Log this one? The first, then at most one every [LOG_EVERY_MS] ([lastLogMs] < 0: none logged yet). */
    fun shouldLog(nowMs: Long, lastLogMs: Long): Boolean = lastLogMs < 0 || nowMs - lastLogMs >= LOG_EVERY_MS
}
