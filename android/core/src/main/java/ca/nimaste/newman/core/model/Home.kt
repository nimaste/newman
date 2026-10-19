package ca.nimaste.newman.core.model

import kotlinx.serialization.Serializable

/**
 * GET /api/home: everything the phone's Home screen shows, worded by the hub so both apps say the
 * same thing (cards, favourites, the status line, the Cameras tab's big picture, Climate's rooms).
 */
@Serializable
data class HomeData(
    val cards: List<HomeCard> = emptyList(),
    val favourites: List<String> = emptyList(),
    val status: HomeStatus = HomeStatus(),
    val cameras: HomeCameras = HomeCameras(),
    val climate: HomeClimate = HomeClimate(),
    val canAutomate: Boolean = false,
    /** May this person change their own Home (Edit Home); false: the admin's household layout, no Edit Home. */
    val canEditHome: Boolean = true,
    /** Which layout this Home is: "household" or "own". */
    val layout: String = "household",
)

/**
 * One Home card: a category in use (its id), or "today" / "people". [screen] is the purpose-built screen it opens
 * (cameras, climate, lights, security, today, people); null opens the generic category screen for [category].
 */
@Serializable
data class HomeCard(
    val id: String,
    val on: Boolean = true,
    /** A category that turned up since the layout was saved: off until someone switches it on. */
    val new: Boolean = false,
    val category: String? = null,
    val screen: String? = null,
    val title: String = "",
    val value: String = "",
    val detail: String = "",
    /** normal | good | warm | danger */
    val tone: String = "normal",
    /** The cameras card's little picture. */
    val camera: String? = null,
)

@Serializable
data class HomeAction(val command: String, val label: String = command)

@Serializable
data class HomeDanger(
    val id: String = "",
    val title: String = "",
    val detail: String = "",
    val entity: String? = null,
    val action: HomeAction? = null,
)

@Serializable
data class HomeWarning(val entity: String = "", val issue: String = "", val text: String = "")

@Serializable
data class HomeStatus(
    /** ok | warn | danger */
    val level: String = "ok",
    val summary: String = "",
    val danger: List<HomeDanger> = emptyList(),
    val warnings: List<HomeWarning> = emptyList(),
)

@Serializable
data class TopActivity(
    val camera: String = "",
    val label: String = "",
    val zone: String? = null,
    val at: Double? = null,
    val snapshot: String? = null,
    val clip: String? = null,
)

@Serializable
data class HomeCameras(
    /** latest | featured */
    val mode: String = "latest",
    val featured: String? = null,
    val top: TopActivity? = null,
)

@Serializable
data class ClimateRoom(val entity: String, val name: String = "", val value: Double? = null)

/** A colour band: below a temperature, above one, or (neither) everything in between. */
@Serializable
data class ClimateBand(val name: String = "", val below: Double? = null, val above: Double? = null, val color: String = "#f5b14c")

@Serializable
data class HomeClimate(
    val thermostat: String? = null,
    val rooms: List<ClimateRoom> = emptyList(),
    val range: List<Double> = listOf(18.0, 26.0),
    val bands: List<ClimateBand> = emptyList(),
)

/** PUT /api/home/me: the person's own Edit Home. */
@Serializable
data class HomePrefs(val cards: List<CardPref>, val favourites: List<String>)

@Serializable
data class CardPref(val id: String, val on: Boolean)

/** PUT /api/home/climate: Climate's rooms, in order. */
@Serializable
data class ClimateRoomsBody(val rooms: List<ClimateRoomPref>)

@Serializable
data class ClimateRoomPref(val entity: String, val name: String)

/** POST /api/auth/password. */
@Serializable
data class PasswordChange(val current: String, val new: String)

/** The new Home and category screens' rules - plain logic, no UI. */
object HomeLogic {
    const val MAX_FAVOURITES = 4

    /** The band a room's temperature falls in: below the "below" band's edge, above the "above" one's, else the middle one. */
    fun bandFor(value: Double?, bands: List<ClimateBand>): ClimateBand? {
        if (value == null || bands.isEmpty()) return null
        bands.firstOrNull { it.below != null && value < it.below }?.let { return it }
        bands.firstOrNull { it.above != null && value > it.above }?.let { return it }
        return bands.firstOrNull { it.below == null && it.above == null } ?: bands.last()
    }

    /** How full a room's bar is (0..1) on the house's range. */
    fun barFraction(value: Double?, range: List<Double>): Float {
        if (value == null) return 0f
        val lo = range.getOrNull(0) ?: 18.0
        val hi = range.getOrNull(1) ?: 26.0
        if (hi <= lo) return 0f
        return ((value - lo) / (hi - lo)).toFloat().coerceIn(0f, 1f)
    }

    /** "#9db8ff" -> 0xFF9DB8FF (ARGB); a bad colour gives null. */
    fun argb(hex: String?): Long? {
        val h = hex?.trim()?.removePrefix("#") ?: return null
        val six = when (h.length) {
            3 -> h.map { "$it$it" }.joinToString("")
            6 -> h
            else -> return null
        }
        return six.toLongOrNull(16)?.let { 0xFF000000L or it }
    }

    /** A Home card's value size (both apps): up to 5 characters 30, up to 12 22, else 18. */
    fun valueSize(value: String): Int = when {
        value.length <= 5 -> 30
        value.length <= 12 -> 22
        else -> 18
    }

    /** The Cameras tab's big picture: "Latest activity" or "Featured camera". */
    fun topLabel(c: HomeCameras): String = if (c.mode == "featured") "Featured camera" else "Latest activity"

    /** Which camera the big picture shows: the featured one, else the latest activity's (else the first camera). */
    fun topCamera(c: HomeCameras, cameras: List<Entity>): Entity? {
        val id = if (c.mode == "featured") c.featured else c.top?.camera
        return cameras.firstOrNull { it.id == id } ?: cameras.firstOrNull()
    }

    // -- Lights ----------------------------------------------------------------------------------

    /** One row of the Lights screen: a light/switch, or a group with its lights behind it. */
    data class LightRow(val entity: Entity, val members: List<Entity> = emptyList())

    data class LightRoom(val name: String, val rows: List<LightRow>)

    const val NO_ROOM = "Other"

    /** A group: a device listing its members (attrs.members). */
    fun members(e: Entity): List<String> = e.attrs.strings("members")

    /** The device's category after any move in Newman (its own, else its kind's; "other" when neither says). */
    fun categoryOf(e: Entity, meta: Meta?): String = e.category.ifEmpty { meta?.kind(e.kind)?.category ?: "" }.ifEmpty { "other" }

    /** The purpose-built screen of the category it's in ("lights", "security"…), from the category's metadata. */
    fun screenOf(e: Entity, meta: Meta?): String? = meta?.screenOf(categoryOf(e, meta))

    /** Devices in a category whose screen is the Lights one that turn on and off (not remotes), by kinds metadata. */
    fun isLight(e: Entity, meta: Meta?): Boolean {
        val k = meta?.kind(e.kind) ?: return false
        return screenOf(e, meta) == "lights" && k.activeState != null && k.commands.isNotEmpty() && !HubModel.isRemote(e, k)
    }

    /**
     * The Lights screen: rooms in the order they first appear (no room last, as "Other"); a group is one row
     * and its members appear only behind it.
     */
    fun lightRooms(entities: Collection<Entity>, meta: Meta?): List<LightRoom> {
        val lights = entities.filter { isLight(it, meta) }
        val byId = lights.associateBy { it.id }
        // A member of a group that's on this screen shows only behind it (by the group's list or its own `group`).
        val grouped = lights.flatMap { members(it) }.toSet()
        val rows = lights.filter { e -> e.id !in grouped && (e.group == null || e.group !in byId) }
            .map { e -> LightRow(e, members(e).mapNotNull { byId[it] }) }
        val order = LinkedHashMap<String, MutableList<LightRow>>()
        rows.forEach { r -> order.getOrPut(r.entity.room?.ifBlank { null } ?: NO_ROOM) { mutableListOf() }.add(r) }
        val named = order.filterKeys { it != NO_ROOM }.map { LightRoom(it.key, it.value) }
        return named + (order[NO_ROOM]?.let { listOf(LightRoom(NO_ROOM, it)) } ?: emptyList())
    }

    /**
     * A light's on and off commands (by the kind: the plain command that confirms to its active state, and
     * one that confirms to another state), whatever else it can do (toggle, brightness).
     */
    fun onOff(k: Kind?): HubModel.Duo? {
        val active = k?.activeState ?: return null
        val plain = HubModel.plainCommands(k)
        val on = plain.firstOrNull { it.confirmsTo == active } ?: return null
        val off = plain.firstOrNull { it !== on && it.confirmsTo.isNotEmpty() && it.confirmsTo != active } ?: return null
        return HubModel.Duo(on, off)
    }

    /** A name inside its room, without the room's name in front ("Kitchen Under Cabinet" in Kitchen: "Under cabinet"). */
    fun shortName(name: String, room: String): String {
        if (!name.startsWith(room, ignoreCase = true) || name.length <= room.length) return name
        val rest = name.substring(room.length).trimStart(' ', '-', '·', ':').ifBlank { return name }
        return rest.replaceFirstChar { it.uppercase() }.let { it.first() + it.drop(1).lowercase() }
    }

    /** Under a room's name: what's on ("Under cabinet on"), else "All off" (just "Off" for a room of one). */
    fun roomLine(room: LightRoom, meta: Meta?): String {
        val on = room.rows.filter { HubModel.isActive(it.entity, meta?.kind(it.entity.kind)) }
        if (on.isEmpty()) return if (room.rows.size == 1) "Off" else "All off"
        if (room.rows.size == 1) return "On"
        return on.joinToString(", ") { shortName(it.entity.name, room.name) } + " on"
    }

    /** How many rows are on (a group counts once). */
    fun onCount(rooms: List<LightRoom>, meta: Meta?): Int =
        rooms.sumOf { r -> r.rows.count { HubModel.isActive(it.entity, meta?.kind(it.entity.kind)) } }

    // -- Security --------------------------------------------------------------------------------

    /** The Security screen's parts, by kinds metadata: alarm panels, locks & doors you control, and sensors by kind. */
    data class SecurityParts(val alarms: List<Entity>, val controls: List<Entity>, val sensors: List<Pair<Kind, List<Entity>>>) {
        val all: List<Entity> get() = alarms + controls + sensors.flatMap { it.second }
    }

    /**
     * Everything in a category whose screen is the Security one (after any moves): alarm panels; things you lock
     * or open (a secure state and commands); and every other device there, one section per kind.
     */
    fun securityParts(entities: Collection<Entity>, meta: Meta?): SecurityParts {
        val mine = entities.filter { screenOf(it, meta) == "security" }
        val alarms = mine.filter { e ->
            val k = meta?.kind(e.kind) ?: return@filter false
            k.secureState == null && !k.isMode && k.commands.any { it.valueType == "state" } && HubModel.stateChoices(k).isNotEmpty()
        }
        val rest = mine.filter { it !in alarms }
        val controls = rest.filter { meta?.kind(it.kind)?.secureState != null && HubModel.plainCommands(meta?.kind(it.kind)).isNotEmpty() }
        val sensors = rest.filter { it !in controls }.groupBy { it.kind }
            .mapNotNull { (kind, list) -> meta?.kind(kind)?.let { it to list } }
        return SecurityParts(alarms, controls, sensors)
    }

    /** The controls grouped by kind, each section titled by the kind's plural name. */
    fun controlGroups(parts: SecurityParts, meta: Meta?): List<Pair<Kind, List<Entity>>> =
        parts.controls.groupBy { it.kind }.mapNotNull { (kind, list) -> meta?.kind(kind)?.let { it to list } }

    /** Temperature sensors Climate can list: climate devices whose state is a number. */
    fun climateSensors(entities: Collection<Entity>, meta: Meta?): List<Entity> = entities.filter { e ->
        val k = meta?.kind(e.kind)
        screenOf(e, meta) == "climate" && k?.states?.isEmpty() == true && e.state?.trim()?.toDoubleOrNull() != null
    }

    // -- any category: its own screen and its card's icon -------------------------------------------

    /** A category's devices (after any moves), by name. */
    fun categoryDevices(entities: Collection<Entity>, meta: Meta?, category: String): List<Entity> =
        entities.filter { categoryOf(it, meta) == category }.sortedBy { it.name.lowercase() }

    /** The icon most of a category's devices are drawn with (their own, else their kind's); ties by name. */
    fun categoryIcon(entities: Collection<Entity>, meta: Meta?, category: String): String =
        categoryDevices(entities, meta, category).map { HubModel.deviceIcon(it, meta?.kind(it.kind)) }
            .groupingBy { it }.eachCount().entries
            .sortedWith(compareByDescending<Map.Entry<String, Int>> { it.value }.thenBy { it.key })
            .firstOrNull()?.key ?: "grid"

    /** Each purpose-built screen's card icon; a card with no screen uses its devices' most common icon. */
    val SCREEN_ICONS = mapOf("cameras" to "camera", "climate" to "thermometer", "lights" to "bulb", "security" to "shield",
        "today" to "calendar", "people" to "user")

    fun cardIcon(card: HomeCard, entities: Collection<Entity>, meta: Meta?): String =
        card.screen?.let { SCREEN_ICONS[it] } ?: card.category?.let { categoryIcon(entities, meta, it) } ?: "grid"
}
