package ca.nimaste.newman.core.people

import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.model.TileFit
import kotlinx.serialization.Serializable
import kotlin.math.asin
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.roundToLong
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * The household on the map: GET /api/people, GET/PUT /api/me/location, POST /api/me/location/report,
 * POST /api/people/{id}/refresh (tap-to-refresh), each person's colour and photo, and the live
 * {"type":"person"} message. Pure logic (wording, the sharing switch, the refresh states, when to
 * report) lives here so both apps say the same and it is unit-tested. Mirrors iOS Shared/Core/People.swift.
 */

/** One person as GET /api/people and the live "person" message send them. */
@Serializable
data class Person(
    val id: String,
    val name: String = "",
    val sharing: Boolean = false,
    val home: Boolean = false,
    val place: String? = null,
    val distanceM: Double? = null,
    val lat: Double? = null,
    val lon: Double? = null,
    val accuracyM: Double? = null,
    /** Epoch seconds of the last fix. */
    val at: Double? = null,
    /** "#RRGGBB" (the hub always sets one; null on older hubs). */
    val color: String? = null,
    /** "/api/people/<id>/photo?v=<hash>", or null (no photo: the coloured initial). */
    val photo: String? = null,
) {
    val hasFix: Boolean get() = lat != null && lon != null
}

/** GET/PUT /api/me/location. */
@Serializable
data class LocationSettings(
    /** "off" | "choice" | "required" */
    val mode: String = "off",
    val enabled: Boolean = false,
    val requiredBy: String? = null,
    val everySeconds: Int = 300,
) {
    /** True when this phone should be reporting. */
    val sharing: Boolean get() = mode == "required" || (mode == "choice" && enabled)
}

@Serializable
data class LocationEnable(val enabled: Boolean)

/** POST /api/me/location/report body. */
@Serializable
data class LocationReport(
    val lat: Double,
    val lon: Double,
    val accuracyM: Double? = null,
    /** Epoch seconds. */
    val at: Double,
    val speedMps: Double? = null,
    /** "background" | "app" | "refresh" (one fix someone asked for) */
    val source: String,
)

/** The reply ({"live": false, "everySeconds": 300}; `live` is ignored - there is no live mode). */
@Serializable
data class ReportReply(val everySeconds: Int = 300)

/** GET /api/places. Home's centre is null until the hub knows where home is. */
@Serializable
data class Place(val id: String, val name: String = "", val lat: Double? = null, val lon: Double? = null, val radiusM: Double = 150.0)

/** How a person reads in the People list and on the map. */
object PeopleText {
    /** Who's home, in one line (both apps): "Chris is home", "2 people home", "No one is home". */
    fun summary(people: List<Person>): String {
        if (people.isEmpty()) return "Where everyone is"
        val home = people.filter { it.home }
        return when (home.size) {
            0 -> "No one is home"
            1 -> "${home[0].name.ifBlank { "Someone" }} is home"
            else -> "${home.size} people home"
        }
    }

    /** "850 m", "3.2 km", "12 km". */
    fun distance(meters: Double): String {
        val m10 = (meters / 10).roundToLong().coerceAtLeast(1) * 10
        if (m10 < 1000) return "$m10 m"
        val tenths = (meters / 100).roundToInt()
        if (tenths < 100) return if (tenths % 10 == 0) "${tenths / 10} km" else "${tenths / 10}.${tenths % 10} km"
        return "${(meters / 1000).roundToLong()} km"
    }

    /** "Home", "School", "3.2 km away", or why there's nothing to show. */
    fun where(p: Person): String = when {
        !p.sharing -> "Not sharing location"
        p.home -> "Home"
        !p.place.isNullOrBlank() -> p.place
        p.distanceM != null -> "${distance(p.distanceM)} away"
        p.hasFix -> "Away"
        else -> "No location yet"
    }

    /** "updated just now", "updated 4 min ago", "updated 2 h ago", "updated 3 days ago". */
    fun updated(at: Double?, now: Double): String? {
        if (at == null || at <= 0) return null
        val s = (now - at).coerceAtLeast(0.0)
        val min = (s / 60).toLong()
        return when {
            s < 60 -> "updated just now"
            min < 60 -> "updated $min min ago"
            min < 24 * 60 -> "updated ${min / 60} h ago"
            min < 48 * 60 -> "updated yesterday"
            else -> "updated ${min / (24 * 60)} days ago"
        }
    }

    data class Row(val id: String, val name: String, val where: String, val updated: String?, val home: Boolean, val located: Boolean)

    fun row(p: Person, now: Double): Row = Row(
        id = p.id,
        name = p.name.ifBlank { "Someone" },
        where = where(p),
        updated = if (p.sharing) updated(p.at, now) else null,
        home = p.sharing && p.home,
        located = p.sharing && p.hasFix,
    )

    /** One line under the map: "Home · updated 4 min ago". */
    fun mapLine(p: Person, now: Double): String = listOfNotNull(where(p), if (p.sharing) updated(p.at, now) else null).joinToString(" · ")

    fun initial(name: String): String = name.trim().take(1).uppercase().ifEmpty { "?" }
}

/** The "Share my location" switch on the Me screen, from GET /api/me/location. */
object SharingSwitch {
    data class State(
        val visible: Boolean,
        val on: Boolean,
        val locked: Boolean,
        /** Under the switch: "Required by Chris" or what sharing means. */
        val note: String,
    )

    val HIDDEN = State(visible = false, on = false, locked = false, note = "")

    /** [settings] null = the hub doesn't offer location sharing (older hub: 404). */
    fun state(settings: LocationSettings?): State = when (settings?.mode) {
        "choice" -> State(true, settings.enabled, false,
            if (settings.enabled) "Your household can see where this phone is" else "Off - your household can't see where you are")
        "required" -> State(true, true, true, "Required by ${settings.requiredBy?.takeIf { it.isNotBlank() } ?: "your household"}")
        else -> HIDDEN
    }

    /** Words for the permission explanation before the system asks. */
    fun permissionReason(settings: LocationSettings?): String =
        if (settings?.mode == "required") "Your household requires location sharing. Newman sends where this phone is to your hub " +
            "so your family can see you on the map - nothing goes anywhere else."
        else "Newman sends where this phone is to your hub so your family can see you on the map - nothing goes anywhere else."

    const val BACKGROUND_REASON = "To keep sharing when Newman is closed, allow location all the time. Newman checks only " +
        "now and then (or when someone taps you on their map), so it's easy on the battery."
}

/**
 * When to post a fix in the background: when the interval (the reply's everySeconds, 300) is up, or
 * sooner after a real move - never more than once a minute. There is no live mode: a viewer's tap
 * asks for ONE fix (a locate push, [LocatePush]).
 */
object ReportSchedule {
    const val MIN_BACKGROUND_SECONDS = 60
    const val MAX_BACKGROUND_SECONDS = 3600
    /** Moving at least this far is worth a report even before the interval is up. */
    const val MOVE_METERS = 75.0
    /** The People list's fallback refresh (reads the hub; never wakes anyone's phone). */
    const val PEOPLE_REFRESH_SECONDS = 30

    fun intervalSeconds(everySeconds: Int): Int = everySeconds.coerceIn(MIN_BACKGROUND_SECONDS, MAX_BACKGROUND_SECONDS)

    data class Last(val at: Double, val lat: Double, val lon: Double)

    /**
     * True when a new fix should be posted. The first fix always goes; then when the interval is up,
     * or sooner after a real move (but never more than once a minute).
     */
    fun shouldSend(last: Last?, now: Double, lat: Double, lon: Double, everySeconds: Int): Boolean {
        if (last == null) return true
        val elapsed = now - last.at
        if (elapsed < 0) return true // the clock moved back
        if (elapsed >= intervalSeconds(everySeconds) - 5) return true
        return elapsed >= MIN_BACKGROUND_SECONDS && distanceMeters(last.lat, last.lon, lat, lon) >= MOVE_METERS
    }

    /** Great-circle distance in meters. */
    fun distanceMeters(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
        val r = 6_371_000.0
        val dLat = Math.toRadians(lat2 - lat1)
        val dLon = Math.toRadians(lon2 - lon1)
        val a = sin(dLat / 2) * sin(dLat / 2) + cos(Math.toRadians(lat1)) * cos(Math.toRadians(lat2)) * sin(dLon / 2) * sin(dLon / 2)
        return 2 * r * asin(sqrt(a.coerceIn(0.0, 1.0)))
    }
}

/**
 * The hub's one-shot locate (someone tapped this person): FCM data {"type": "locate"}, never shown
 * to anyone. It is answered with exactly ONE report (`source: "refresh"`), then nothing more.
 */
object LocatePush {
    fun isLocate(data: Map<String, String>): Boolean = data["type"] == "locate" || data["type"] == "refresh"

    /** The report's source. */
    const val SOURCE = "refresh"
    /** A locate this soon after the last answer is the same tap (FCM may deliver twice; the hub allows one per 2 min). */
    const val DEDUPE_SECONDS = 30.0

    fun shouldAnswer(lastAnsweredAt: Double?, now: Double): Boolean {
        if (lastAnsweredAt == null || lastAnsweredAt <= 0 || now < lastAnsweredAt) return true // never, or the clock moved back
        return now - lastAnsweredAt >= DEDUPE_SECONDS
    }
}

/**
 * Each person's look: their colour ("#RRGGBB", PUT /api/me/profile) and photo (PUT/DELETE
 * /api/me/photo), shown everywhere they appear; no photo = their single initial on their colour.
 */
object PersonLook {
    /** The Me screen's swatches. The first six are what an older hub (no "color") falls back to, by position. */
    val PRESETS = listOf("#7FB8FF", "#C58BFF", "#3CCFB4", "#F5B942", "#FF8A6B", "#8BD46B",
        "#FF7EB6", "#5ED3F3", "#A3A1FF", "#FFD166", "#E0A47A", "#9AA5C4")
    const val FALLBACK_COUNT = 6
    /** Photos are made smaller than this (long side, pixels) before they're uploaded. */
    const val UPLOAD_MAX_PIXELS = 1024
    /** The hub refuses bigger uploads. */
    const val UPLOAD_MAX_BYTES = 3 * 1024 * 1024

    private val HEX = Regex("^#?[0-9A-Fa-f]{6}$")

    /** "#7fb8ff" / "7FB8FF" → 0x7FB8FF; null when it isn't six hex digits. */
    fun rgb(hex: String?): Int? {
        val s = hex?.trim() ?: return null
        if (!HEX.matches(s)) return null
        return s.removePrefix("#").toInt(16)
    }

    /** The person's colour as 0xRRGGBB: theirs, or (older hub) a preset by their place in the household. */
    fun rgb(p: Person?, index: Int): Int =
        rgb(p?.color) ?: rgb(PRESETS[Math.floorMod(index, FALLBACK_COUNT)]) ?: 0x7FB8FF

    /** As an opaque ARGB int (Compose Color(…) / Android paint). */
    fun argb(p: Person?, index: Int): Int = rgb(p, index) or 0xFF000000.toInt()

    /** "#7FB8FF" (upper case), or null when it isn't a colour. */
    fun normalized(hex: String?): String? = rgb(hex)?.let { "#%06X".format(it) }

    fun same(a: String?, b: String?): Boolean {
        val x = normalized(a) ?: return false
        return x == normalized(b)
    }

    /** The photo's hub path (its ?v= changes with the picture, so it is also the cache key). */
    fun photoPath(p: Person?): String? = p?.photo?.trim()?.takeIf { it.isNotEmpty() }

    /** The size to draw a [width]×[height] picture at so its long side is at most [maxSide]. */
    fun fitted(width: Int, height: Int, maxSide: Int = UPLOAD_MAX_PIXELS): Pair<Int, Int> {
        val long = maxOf(width, height)
        if (long <= maxSide || long <= 0) return width to height
        val k = maxSide.toDouble() / long
        return (width * k).roundToInt() to (height * k).roundToInt()
    }

    /** The centred square of a [width]×[height] picture: (x, y, side). */
    fun squareCrop(width: Int, height: Int): Triple<Int, Int, Int> {
        val side = minOf(width, height)
        return Triple((width - side) / 2, (height - side) / 2, side)
    }
}

@Serializable
data class ProfileUpdate(val color: String)

/**
 * Tap-to-refresh: tapping a person (or the refresh button on their map) asks the hub for ONE fresh
 * fix (POST /api/people/<id>/refresh). "Updating…" shows until a "person" update with a newer fix
 * arrives, or 45 s pass ("Couldn't reach their phone right now"). The hub allows one every 2 minutes
 * per person; a 429 says so quietly. An older hub (404) just does nothing.
 */
object PersonRefresh {
    const val TIMEOUT_SECONDS = 45.0
    /** How long "Couldn't reach…" / "Updated recently…" stay up. */
    const val MESSAGE_SECONDS = 8.0
    /** A 429 without nextAllowedAt: the hub's 2 minutes. */
    const val DEFAULT_WAIT_SECONDS = 120.0

    enum class Phase { IDLE, UPDATING, FAILED, TOO_SOON }

    data class State(
        val phase: Phase = Phase.IDLE,
        /** When this phase began. */
        val since: Double = 0.0,
        /** The person's fix time when the refresh was asked for (a newer one ends it). */
        val baselineAt: Double? = null,
        /** TOO_SOON: when the hub allows the next one. */
        val until: Double = 0.0,
    )

    val IDLE = State()

    /** The tap: start a refresh (nothing new while one is already running). */
    fun start(s: State, person: Person?, now: Double): State =
        if (s.phase == Phase.UPDATING) s else State(Phase.UPDATING, now, person?.at)

    /** The hub's answer to POST .../refresh (status 0 = no answer at all). */
    fun replied(s: State, status: Int, nextAllowedAt: Double?, now: Double): State {
        if (s.phase != Phase.UPDATING) return s
        return when (status) {
            in 200..299 -> s
            429 -> State(Phase.TOO_SOON, now, until = nextAllowedAt ?: (now + DEFAULT_WAIT_SECONDS))
            404, 405 -> IDLE // an older hub without refresh: quietly nothing
            else -> State(Phase.FAILED, now)
        }
    }

    /** A "person" update (or a reload) arrived: a fix newer than the one we had ends "Updating…". */
    fun personChanged(s: State, p: Person): State {
        if (s.phase != Phase.UPDATING) return s
        val at = p.at ?: return s
        val b = s.baselineAt
        if (b != null && at <= b) return s
        return IDLE
    }

    /** Time passing: the 45 s timeout, and messages going away. */
    fun tick(s: State, now: Double): State = when (s.phase) {
        Phase.IDLE -> s
        Phase.UPDATING -> if (now - s.since >= TIMEOUT_SECONDS) State(Phase.FAILED, now) else s
        Phase.FAILED, Phase.TOO_SOON -> if (now - s.since >= MESSAGE_SECONDS) IDLE else s
    }

    /** The words under the person, or null. */
    fun text(s: State, now: Double): String? = when (s.phase) {
        Phase.IDLE -> null
        Phase.UPDATING -> "Updating…"
        Phase.FAILED -> "Couldn't reach their phone right now"
        Phase.TOO_SOON -> {
            val n = ceil(s.until - now).toInt()
            if (n > 0) "Updated recently - try again in $n s" else "Updated recently"
        }
    }

    @Serializable
    private data class Reply(val nextAllowedAt: Double? = null)

    /** nextAllowedAt (epoch s) from the hub's 202 / 429 body. */
    fun nextAllowedAt(body: String?): Double? = try {
        body?.let { NewmanJson.decodeFromString(Reply.serializer(), it).nextAllowedAt }
    } catch (e: Exception) {
        null
    }
}
/**
 * The People tile on a phone page (web/src/wall/PhoneTiles.jsx PhonePeople): who it shows and how
 * many rows fit its cell; the rest is "+N more", which opens the People tab. Sizes are dp inside the
 * card; an infinite height (a flowed page) shows everyone.
 */
object PeopleTile {
    /** The "People" title row and the space under it. */
    const val HEAD = 22f
    const val HEAD_GAP = 8f
    const val ROW = 44f
    const val GAP = 6f
    /** Rows show the initial circle from this width. */
    const val AVATAR_FROM = 100f
    /** "+N more" fits beside the title from this width; narrower it goes under the rows. */
    const val NOTE_FROM = 130f

    /** The tile's config.people (person ids) in the hub's order; none chosen = everyone. */
    fun select(all: List<Person>, pick: List<String>): List<Person> =
        if (pick.isEmpty()) all else all.filter { it.id in pick }

    enum class Look { ROWS, ONE_LINE }

    data class Layout(
        val look: Look,
        val head: Boolean,
        val shown: Int,
        val more: Int,
        /** "+N more" beside the title. */
        val moreInHead: Boolean,
        /** "+N more" under the rows. */
        val moreBelow: Boolean,
        val avatars: Boolean,
    )

    fun layout(count: Int, width: Float, height: Float): Layout {
        val head = height >= HEAD + 40f + HEAD_GAP
        // Too short for rows: the title and who's there on one line.
        if (!head && count > 1 && height >= HEAD + TileFit.LINE) return Layout(Look.ONE_LINE, true, count, 0, false, false, false)
        val avail = height - if (head) HEAD + HEAD_GAP else 0f
        val fit = TileFit.list(count, avail, ROW, GAP, if (head) 0f else TileFit.LINE)
        val inHead = head && fit.more > 0 && width >= NOTE_FROM
        val below = fit.more > 0 && !inHead && fit.left >= TileFit.LINE
        return Layout(Look.ROWS, head, fit.shown, fit.more, inHead, below, width >= AVATAR_FROM)
    }

    /** "Marlina (home) · Massimo" - the one-line look. */
    fun oneLine(people: List<Person>): String =
        people.joinToString(" · ") { p -> p.name.ifBlank { "Someone" } + if (p.sharing && p.home) " (home)" else "" }
}

/** The People list: keeps the hub's order, replaces one person on a live message. */
object PeopleList {
    fun upsert(list: List<Person>, p: Person): List<Person> =
        if (list.any { it.id == p.id }) list.map { if (it.id == p.id) p else it } else list + p
}
