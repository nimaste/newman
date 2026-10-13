package ca.nimaste.newman.core.wall

/**
 * When the wall brings itself back to the front after it left for any reason (Home, Recents, another
 * app, an assistant it didn't come back from) - the fallback behind AssistantReturn's quick return.
 *
 * Away = no Newman screen is in front (the wall's own settings and connect screens count as in front;
 * leaving because the screen turned off doesn't count: the wall is still on top when it comes on).
 *
 * The rule, kept deliberately simple: come back once the wall has been away [Options.minutes] and no
 * other app has played anything for that long (each playback that stops starts the wait over), and at
 * once when the screen wakes (if nothing is playing). Android gives an ordinary app no way to see
 * touches in another app or which app is in front (that needs Usage Access, a separate grant), so
 * "the user is still busy over there" is told only by sound: video, music or Alexa playing holds the
 * wall off; silent use of another app (Settings, Silk) gets [Options.minutes] before the wall comes
 * back - long enough for a quick look, and the setting can be turned off for longer work.
 * Pure: the wall feeds it what the activity, the screen and AudioManager report, with the time.
 *
 * The screen never stays off ([screenOff]): whatever turned it off - the power button, a timeout over
 * another app, Fire OS's charging or Show Mode screen, the charger being plugged in or out - the wall
 * comes back at once, whatever the settings or anything playing, [Decision.dimmed]: the screen turned
 * on into the wall's dimmed screen (the date and time). A dark tablet falls into deep sleep (no
 * check-ins with Newman), and a wall that's off looks dead. Only NewmanWall.keepAwake(false) (on by
 * default) lets it sleep. A return that didn't work is tried again every [SCREEN_OFF_RETRY_MS] while
 * the screen stays off ([Decision.recheckInMs]), so a tablet that can't come back doesn't flash on and
 * off but doesn't give up either.
 *
 * The charger being plugged in or out with the screen on but another app in front (Fire OS shows its
 * own charging screen) brings the wall back at once too ([powerChanged]); a background check-in with
 * the wall away just looks at the usual timer. With no wall running at all (its process was killed and
 * started again for a check-in) [notRunning] says whether to start it.
 */
class ComeBack {
    data class Options(
        /** "Come back after leaving". */
        val afterLeaving: Boolean = true,
        /** Minutes away (and quiet) before coming back. */
        val minutes: Int = DEFAULT_MINUTES,
        /** "Come back when the screen wakes". */
        val onWake: Boolean = true,
    )

    /**
     * [bringBack]: come to the front now ([why] says why, or why not). [recheckInMs]: ask again then.
     * [dimmed]: turn the screen on (it is off) into the wall's dimmed screen, not full brightness.
     */
    data class Decision(val bringBack: Boolean, val why: String, val recheckInMs: Long? = null, val dimmed: Boolean = false)

    var away = false
        private set
    private var awaySince = 0L
    private var playing = false
    private var quietSince = 0L
    private var screenOffReturnAt: Long? = null

    /** No Newman screen is in front any more. Returns why it isn't counted as away, or null when it is. */
    fun left(now: Long, screenOn: Boolean): String? {
        if (!screenOn) return "the screen turned off (the wall is still on top)"
        if (!away) {
            away = true
            awaySince = now
        }
        return null
    }

    /** A Newman screen is in front again. */
    fun entered() {
        away = false
        screenOffReturnAt = null
    }

    /** Whether any other app is playing anything now. */
    fun othersPlaying(on: Boolean, now: Long) {
        if (!on && playing) quietSince = now
        playing = on
    }

    val isPlaying: Boolean get() = playing

    /** The timer: call on every change and again after [Decision.recheckInMs]. */
    fun check(now: Long, options: Options): Decision {
        if (!away) return Decision(false, "the wall is in front")
        if (!options.afterLeaving) return Decision(false, "\"Come back after leaving\" is off")
        if (playing) return Decision(false, "another app is playing (waiting until it stops)")
        val wait = minutesMs(options.minutes)
        val due = maxOf(awaySince, quietSince) + wait
        if (now < due) return Decision(false, "waiting ${(due - now + 999) / 1000} s more", due - now)
        val quiet = if (quietSince > awaySince) ", nothing has played for ${(now - quietSince) / 60_000} min" else ", nothing playing"
        val d = Decision(true, "away ${(now - awaySince) / 60_000} min$quiet", wait)
        awaySince = now // until the wall is in front again ([entered]); if it didn't come, try again later
        return d
    }

    /** The screen came on (or was unlocked). */
    fun screenWoke(now: Long, options: Options): Decision {
        if (!away) return Decision(false, "the wall is in front")
        if (!options.onWake) return Decision(false, "\"Come back when the screen wakes\" is off")
        if (playing) return Decision(false, "another app is playing")
        awaySince = now
        return Decision(true, "the screen woke")
    }

    /**
     * The screen went off, or is still off - for any reason (Android doesn't say which). [keepsScreenOn]:
     * the wall keeps the screen on (keep awake). Back dimmed at once; a return that didn't happen (the
     * screen is still off, or the wall never came to the front) is tried again after [SCREEN_OFF_RETRY_MS]:
     * [Decision.recheckInMs] says when to ask again if the screen is still off.
     */
    fun screenOff(now: Long, keepsScreenOn: Boolean): Decision {
        if (!keepsScreenOn) return Decision(false, "the wall lets the screen sleep (keep awake is off)")
        val last = screenOffReturnAt
        if (last != null && now - last < SCREEN_OFF_RETRY_MS) {
            val wait = SCREEN_OFF_RETRY_MS - (now - last)
            return Decision(false, "already tried ${(now - last) / 1000} s ago (trying again in ${(wait + 999) / 1000} s)", wait)
        }
        screenOffReturnAt = now
        if (away) awaySince = now
        val why = if (away) "the screen went off while another app was in front" else "the screen went off with the wall in front"
        return Decision(true, why, SCREEN_OFF_RETRY_MS, dimmed = true)
    }

    /**
     * The charger was plugged in or out with the screen on. Over another app (Fire OS's own charging
     * screen, most often) the wall comes back at once, dimmed - unless "Come back after leaving" is off or
     * something is playing. (With the screen off: [screenOff].)
     */
    fun powerChanged(now: Long, options: Options): Decision {
        if (!away) return Decision(false, "the wall is in front")
        if (!options.afterLeaving) return Decision(false, "\"Come back after leaving\" is off")
        if (playing) return Decision(false, "another app is playing")
        awaySince = now
        return Decision(true, "the charger was plugged in or out while another app was in front", dimmed = true)
    }

    companion object {
        /** The least time between two screen-off returns; while the screen stays off, the next try. */
        const val SCREEN_OFF_RETRY_MS = 30_000L

        /**
         * A background check-in or the charger changing found no wall running (its process was killed, or
         * the wall was closed): start it, dimmed - with the screen off always, with it on unless something
         * is playing or "Come back after leaving" is off (someone is using another app).
         */
        fun notRunning(screenOn: Boolean, othersPlaying: Boolean, afterLeaving: Boolean, keepsScreenOn: Boolean = true): Decision = when {
            !screenOn && keepsScreenOn -> Decision(true, "the wall wasn't running and the screen was off", dimmed = true)
            !screenOn -> Decision(false, "the wall lets the screen sleep (keep awake is off)")
            !afterLeaving -> Decision(false, "the wall isn't running, but \"Come back after leaving\" is off")
            othersPlaying -> Decision(false, "the wall isn't running, but another app is playing")
            else -> Decision(true, "the wall wasn't running", dimmed = true)
        }

        /** The choices offered in the wall settings. */
        val MINUTE_CHOICES = listOf(1, 2, 5, 10)
        const val DEFAULT_MINUTES = 2

        /** A saved value that isn't one of the choices (an older or hand-edited save): the default. */
        fun minutesSetting(saved: Int): Int = if (saved in MINUTE_CHOICES) saved else DEFAULT_MINUTES

        private fun minutesMs(m: Int) = minutesSetting(m) * 60_000L
    }
}
