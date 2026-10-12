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
 * The screen going off while the wall is away ([screenOff]) brings it back at once, whatever the
 * settings or anything playing: nobody is looking at the other app any more, and a dark tablet falls
 * into deep sleep (no check-ins with Newman). It comes back [Decision.dimmed] - the screen turned on
 * into the wall's dimmed screen (the date and time), never left fully off - as long as the wall keeps
 * the screen on (NewmanWall.keepAwake, on by default). The screen going off while the wall itself is
 * in front can only be the power button (the wall holds the screen on): that is honoured - the wall
 * isn't away, so nothing here turns it back on until someone wakes it.
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
     * The screen went off (a timeout over another app, or the power button there - Android doesn't
     * say which). [keepsScreenOn]: the wall keeps the screen on while it's in front (keep awake).
     * A return that didn't happen (the wall never came to the front) is tried again only after
     * [SCREEN_OFF_RETRY_MS], so a tablet that can't bring the wall back doesn't flash on and off.
     */
    fun screenOff(now: Long, keepsScreenOn: Boolean): Decision {
        if (!away) return Decision(false, "the wall is in front: the screen was turned off on purpose, it stays off until woken")
        if (!keepsScreenOn) return Decision(false, "the wall lets the screen sleep (keep awake is off)")
        val last = screenOffReturnAt
        if (last != null && now - last < SCREEN_OFF_RETRY_MS) {
            return Decision(false, "already tried ${(now - last) / 1000} s ago (trying again after ${SCREEN_OFF_RETRY_MS / 1000} s)")
        }
        screenOffReturnAt = now
        awaySince = now
        return Decision(true, "the screen went off while another app was in front", dimmed = true)
    }

    companion object {
        /** The least time between two screen-off returns that didn't bring the wall to the front. */
        const val SCREEN_OFF_RETRY_MS = 60_000L

        /** The choices offered in the wall settings. */
        val MINUTE_CHOICES = listOf(1, 2, 5, 10)
        const val DEFAULT_MINUTES = 2

        /** A saved value that isn't one of the choices (an older or hand-edited save): the default. */
        fun minutesSetting(saved: Int): Int = if (saved in MINUTE_CHOICES) saved else DEFAULT_MINUTES

        private fun minutesMs(m: Int) = minutesSetting(m) * 60_000L
    }
}
