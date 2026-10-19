package ca.nimaste.newman.core.voice

/**
 * Sharing the tablet's microphone with other apps (Alexa on a Fire tablet, Google's assistant…).
 *
 * Android 10+ lets several apps record at once but gives the foreground app's recording priority and
 * silences the others - so while the wall listens for "Hey Newman", "Alexa, …" opens Alexa but Alexa
 * hears nothing. [MicGate] decides when the wall lets go of the microphone (another app started
 * recording, or the wall's own recording is being silenced) and when it may take it back;
 * [AssistantReturn] decides when to bring the wall back to the front after an assistant took over the
 * screen. Both are pure: the wall feeds them what AudioManager and the activity report, with the time.
 */

/** One active recording as AudioManager reports it ([id] = the client's audio session). */
data class Recording(val id: Int, val ours: Boolean, val silenced: Boolean = false, val hotword: Boolean = false)

class MicGate(
    /** After the other app stops, wait this long before listening again. */
    private val graceMs: Long = 1_500,
    /** Our own recording silenced again and again (by something we can't see): wait longer each time, up to this. */
    private val maxBackoffMs: Long = 30_000,
    /** A silencing this long after the last one starts the back-off over. */
    private val strikeResetMs: Long = 60_000,
    /** Another app recording this long without a break is an always-on recorder: stop giving way to it. */
    private val staleMs: Long = 180_000,
) {
    private val firstSeen = HashMap<Int, Long>()  // other apps' recordings now active -> since
    private val ignored = HashSet<Int>()          // always-on recorders we no longer give way to
    private var resumeAt = 0L
    private var strikes = 0
    private var lastStrike = -1L

    /** Why the microphone is (or was last) held off: "another app is recording" / "silenced by Android". */
    var reason = ""
        private set

    /**
     * The recordings changed. A "hotword" recording (an assistant's always-on wake-word detector) isn't
     * silenced by ours and doesn't take turns, so it's ignored.
     */
    fun update(recordings: List<Recording>, now: Long) {
        val others = recordings.filter { !it.ours && !it.hotword }.map { it.id }.toSet()
        val hadOthers = othersActive()
        firstSeen.keys.retainAll(others)
        ignored.retainAll(others)
        for (id in others) firstSeen.getOrPut(id) { now }
        if (othersActive()) reason = "another app is recording"
        if (hadOthers && !othersActive()) resumeAt = maxOf(resumeAt, now + graceMs)
        if (recordings.any { it.ours && it.silenced }) {
            strikes = if (lastStrike >= 0 && now - lastStrike > strikeResetMs) 1 else (strikes + 1).coerceAtMost(10)
            lastStrike = now
            resumeAt = maxOf(resumeAt, now + minOf(graceMs shl (strikes - 1), maxBackoffMs))
            reason = "silenced by Android"
        }
    }

    private fun othersActive() = firstSeen.keys.any { it !in ignored }

    /** Another app (not an ignored always-on one) is recording now. */
    fun othersRecording(now: Long): Boolean {
        expireStale(now)
        return othersActive()
    }

    private fun expireStale(now: Long) {
        for ((id, since) in firstSeen) if (id !in ignored && now - since >= staleMs) ignored += id
    }

    /** May the wall listen now? */
    fun mayRecord(now: Long): Boolean = !othersRecording(now) && now >= resumeAt

    /** Ask [mayRecord] again in this many ms (something is waiting to happen), or null. */
    fun recheckIn(now: Long): Long? {
        expireStale(now)
        val stale = firstSeen.filterKeys { it !in ignored }.values.minOrNull()?.let { it + staleMs - now }
        val resume = if (now < resumeAt) resumeAt - now else null
        return listOfNotNull(stale, resume).minOrNull()?.coerceAtLeast(1)
    }
}

/**
 * Bring the wall back after a voice assistant took over the screen (Fire OS's full-screen Alexa
 * stays up until it's swiped away).
 *
 * Fire OS hides Alexa's recording from other apps (its wake word and capture run on a privileged
 * path), so a takeover is told by how the wall left the front: another app's screen simply came over
 * it - not the wall opening a screen of its own, not the screen turning off ([leaveBlocker]), and not
 * the Home or Recents button (Android's "close system dialogs" with reason homekey / recentapps,
 * within [userLeaveMs] of leaving, before or after: [userLeft]).
 *
 * Then: wait for the answer to start - another app's playback beginning after the takeover (on Fire
 * OS the assistant's sounds and spoken answer play on the music stream, so every kind of playback
 * counts; Alexa+ can take well over 6 s to answer, and coming to the front over it cuts its speech).
 * Once something has played, return when no other app has played anything for [quietMs] (a new
 * playback in that window - a follow-up answer - starts it over), at least [minAwayMs] after leaving.
 * [quietMs] is the tablet's "Wait after Alexa goes quiet" setting ([quietSetting]).
 * If nothing plays within [noAnswerMs] (a text-only answer, or the user just looked), return then.
 * If another app plays for [longPlayMs] without a break (music the user asked for), return anyway -
 * it keeps playing behind the wall. After [giveUpMs] it gives up. Coming back by hand ([entered])
 * cancels the wait.
 */
class AssistantReturn(
    private val minAwayMs: Long = 4_000,
    /** Quiet this long after the answer before coming back; the wall sets it from its settings. */
    var quietMs: Long = 6_000,
    private val noAnswerMs: Long = 30_000,
    private val longPlayMs: Long = 20_000,
    private val giveUpMs: Long = 120_000,
    private val userLeaveMs: Long = 2_000,
) {
    enum class Action { NONE, RETURN, GIVE_UP }

    var armed = false
        private set
    /** Why the last [Action.RETURN] happened, for the log. */
    var returnReason = ""
        private set
    private var armedAt = 0L
    private var playing = false
    private var playingSince = 0L
    private var quietSince = 0L
    /** Another app started playing since the takeover (the answer has begun). */
    private var heard = false
    private var userLeftAt = -1L

    /**
     * The wall left the front. [blocker]: why that wasn't a takeover ([leaveBlocker]), or null.
     * Returns why it isn't waiting to come back, or null when it is.
     */
    fun left(now: Long, blocker: String?): String? {
        val why = blocker ?: if (userLeftAt >= 0 && now - userLeftAt <= userLeaveMs) "the Home or Recents button was pressed" else null
        if (why != null) {
            armed = false
            return why
        }
        armed = true
        armedAt = now
        heard = false
        return null
    }

    /** The wall is in front again (came back by hand, or we brought it back). */
    fun entered() {
        armed = false
    }

    /** The Home or Recents button was pressed. True = that cancelled a wait that had just started. */
    fun userLeft(now: Long): Boolean {
        userLeftAt = now
        if (armed && now - armedAt <= userLeaveMs) {
            armed = false
            return true
        }
        return false
    }

    /** Whether any other app is playing anything now (not what was already playing when the wall left). */
    fun othersPlaying(on: Boolean, now: Long) {
        if (on && !playing) playingSince = now
        if (!on && playing) quietSince = now
        if (on && armed) heard = true
        playing = on
    }

    /** Whether the answer has started playing since the takeover. */
    val answerStarted: Boolean get() = heard

    private fun returnAt(): Long = maxOf(
        armedAt + minAwayMs,
        when {
            playing -> playingSince + longPlayMs
            heard -> quietSince + quietMs
            else -> armedAt + noAnswerMs
        },
    )

    fun check(now: Long): Action {
        if (!armed) return Action.NONE
        if (now - armedAt >= giveUpMs) {
            armed = false
            return Action.GIVE_UP
        }
        if (now < returnAt()) return Action.NONE
        returnReason = when {
            playing -> "another app has been playing for ${(now - playingSince) / 1000} s (it carries on behind the wall)"
            heard -> "the answer finished: nothing else has played for ${(now - quietSince) / 1000} s"
            else -> "no answer played within ${(now - armedAt) / 1000} s"
        }
        armed = false
        return Action.RETURN
    }

    /** Call [check] again in this many ms, or null when not waiting. */
    fun nextCheckIn(now: Long): Long? {
        if (!armed) return null
        return minOf(armedAt + giveUpMs, returnAt()).minus(now).coerceAtLeast(1)
    }

    companion object {
        /** "Wait after Alexa goes quiet": its slider's range and starting value, in seconds. */
        const val QUIET_MIN_S = 6
        const val QUIET_MAX_S = 30
        const val QUIET_DEFAULT_S = 12

        /** A saved wait, kept within the slider's range. */
        fun quietSetting(seconds: Int): Int = seconds.coerceIn(QUIET_MIN_S, QUIET_MAX_S)

        /** Why the wall leaving the front isn't an assistant taking over, or null when it may be. */
        fun leaveBlocker(screenOn: Boolean, ownScreen: Boolean, closing: Boolean): String? = when {
            closing -> "the wall is closing"
            !screenOn -> "the screen turned off"
            ownScreen -> "the wall opened a screen of its own"
            else -> null
        }

        /** Android's "close system dialogs" reasons that mean the user left with Home or Recents. */
        fun isUserLeaveReason(reason: String?): Boolean = reason == "homekey" || reason == "recentapps"

        /**
         * Is this playback (AudioPlaybackConfiguration.toString(), "… state:started …") playing now?
         * The player state isn't public API; without it, assume it is.
         */
        fun isStarted(description: String): Boolean =
            Regex("""state:(\w+)""").find(description)?.groupValues?.get(1)?.let { it == "started" } ?: true
    }
}
