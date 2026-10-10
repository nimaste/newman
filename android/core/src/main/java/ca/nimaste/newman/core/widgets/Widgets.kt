package ca.nimaste.newman.core.widgets

import ca.nimaste.newman.core.model.NewmanJson
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive

/**
 * Home-screen widgets and Quick Settings tiles: the hub's GET /api/widgets/devices and
 * GET /api/widgets/state (server/newman/widgets.py), and the pure parts of showing them - grouping
 * the picker, the text a tile shows, what a tap does. Nothing here knows a kind of device by its id:
 * the hub works out each device's actions (what a tap does first) from kinds.py.
 */

@Serializable
data class WidgetAction(
    val command: String,
    val label: String = command,
    val sensitive: Boolean = false,
    /** What a tap does right now (the hub lists it first). */
    val primary: Boolean = false,
)

@Serializable
data class WidgetReading(val value: JsonElement? = null, val unit: String = "")

/** One device as a widget shows it (GET /api/widgets/state). */
@Serializable
data class WidgetDevice(
    val id: String,
    val name: String = id,
    val kind: String = "",
    @Serializable(with = ca.nimaste.newman.core.model.LenientStringSerializer::class)
    val state: String? = null,
    val stateLabel: String = "",
    val available: Boolean = true,
    /** In its kind's active state (on, unlocked, open…): shown highlighted. */
    val active: Boolean = false,
    val icon: String = "",
    val area: String? = null,
    val actions: List<WidgetAction> = emptyList(),
    val reading: WidgetReading? = null,
    /** Ours, not the hub's: a tap was just sent and the hub hasn't answered yet. */
    val pending: String? = null,
)

@Serializable
data class WidgetStateResponse(val devices: List<WidgetDevice> = emptyList())

/** One device a widget can show (GET /api/widgets/devices), for the pickers. */
@Serializable
data class WidgetChoice(
    val id: String,
    val name: String = id,
    val kind: String = "",
    /** The kind's plural ("Lights"), for grouping by type. */
    val kindName: String = "",
    val area: String? = null,
    val icon: String = "",
    val controllable: Boolean = false,
)

@Serializable
data class WidgetDevicesResponse(val devices: List<WidgetChoice> = emptyList())

/** POST /api/widgets/push-token. A null token forgets it. */
@Serializable
data class WidgetPushToken(val token: String?, val kind: String = "android", val deviceId: String)

/** How a widget's picker groups devices. */
enum class WidgetGrouping { AREA, TYPE }

object WidgetPicker {
    const val NO_ROOM = "No room"
    const val OTHER = "Other"

    /** Devices matching [query] (name, room or type; any word order), grouped: groups sorted, those
     * without a room last; devices by name within each. */
    fun group(devices: List<WidgetChoice>, by: WidgetGrouping, query: String = ""): List<Pair<String, List<WidgetChoice>>> {
        val words = query.trim().lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }
        val matching = devices.filter { d ->
            val hay = listOfNotNull(d.name, d.area, d.kindName).joinToString(" ").lowercase()
            words.all { hay.contains(it) }
        }
        val key = { d: WidgetChoice ->
            when (by) {
                WidgetGrouping.AREA -> d.area?.trim()?.ifEmpty { null }
                WidgetGrouping.TYPE -> d.kindName.trim().ifEmpty { null }
            }
        }
        return matching.groupBy(key).entries
            .sortedWith(compareBy<Map.Entry<String?, List<WidgetChoice>>>({ it.key == null }, { it.key?.lowercase() ?: "" }))
            .map { (k, list) ->
                (k ?: if (by == WidgetGrouping.AREA) NO_ROOM else OTHER) to list.sortedBy { it.name.lowercase() }
            }
    }

    /** Ticking a device adds it at the end; unticking removes it. Order is the widget's order. */
    fun toggle(selected: List<String>, id: String): List<String> =
        if (id in selected) selected - id else selected + id

    /** Move the device at [from] by [by] places (-1 up, +1 down), clamped. */
    fun move(selected: List<String>, from: Int, by: Int): List<String> {
        if (from !in selected.indices) return selected
        val to = (from + by).coerceIn(0, selected.lastIndex)
        if (to == from) return selected
        return selected.toMutableList().apply { add(to, removeAt(from)) }
    }

    /** Saved ids that still exist, in their order (devices removed from the hub drop out). */
    fun keepKnown(selected: List<String>, devices: List<WidgetChoice>): List<String> {
        val known = devices.map { it.id }.toSet()
        return selected.filter { it in known }
    }
}

object WidgetTiles {
    /** What tapping the tile does now: the hub's primary action; null = nothing to run (a sensor). */
    fun tapAction(d: WidgetDevice?): WidgetAction? =
        d?.takeIf { it.available }?.actions?.firstOrNull { it.primary }

    /** A tap that should ask first: a sensitive action on a widget with "Confirm first" on. */
    fun needsConfirm(a: WidgetAction?, confirmSensitive: Boolean): Boolean = a != null && a.sensitive && confirmSensitive

    /** A reading exactly as the hub reported it: "21.5 °C", "64%", "3". Never rounded. */
    fun readingText(r: WidgetReading?): String? {
        val p = r?.value as? JsonPrimitive ?: return null
        if (p.isString && p.content.isBlank()) return null
        val v = p.content
        return when {
            r.unit.isEmpty() -> v
            r.unit == "%" -> "$v%"
            else -> "$v ${r.unit}"
        }
    }

    /** The line under the name: offline, a tap in flight, the reading, else the state. */
    fun stateText(d: WidgetDevice): String = when {
        !d.available -> "Offline"
        d.pending != null -> d.pending
        else -> readingText(d.reading) ?: d.stateLabel.ifEmpty { d.state ?: "" }
    }

    /** What a screen reader says for a tile: "Den plug, on". */
    fun describe(d: WidgetDevice): String = ca.nimaste.newman.core.model.Spoken.of(d.name, stateText(d))

    /** Right after a tap: "Unlock…" until the hub says what happened. */
    fun optimistic(d: WidgetDevice, a: WidgetAction): WidgetDevice = d.copy(pending = a.label + "…")

    /** Fresh states from the hub over the cached ones, in the widget's [order]; devices the hub no
     * longer returns stay out. */
    fun merge(order: List<String>, fresh: List<WidgetDevice>): List<WidgetDevice> {
        val byId = fresh.associateBy { it.id }
        return order.mapNotNull { byId[it] }
    }

    /** Columns that fit a widget [widthDp] wide (tiles at least [minTileDp]), 1..5 (what a home-screen
     * grid can have). */
    fun columns(widthDp: Float, minTileDp: Float = 76f): Int = (widthDp / minTileDp).toInt().coerceIn(1, 5)

    private val listSerializer = ListSerializer(WidgetDevice.serializer())

    fun encode(list: List<WidgetDevice>): String = NewmanJson.encodeToString(listSerializer, list)

    fun decode(json: String?): List<WidgetDevice> = try {
        if (json.isNullOrBlank()) emptyList() else NewmanJson.decodeFromString(listSerializer, json)
    } catch (e: Exception) {
        emptyList()
    }

    fun parseState(json: String): List<WidgetDevice> =
        NewmanJson.decodeFromString(WidgetStateResponse.serializer(), json).devices
}
