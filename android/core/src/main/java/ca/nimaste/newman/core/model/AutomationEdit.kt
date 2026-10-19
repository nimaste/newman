package ca.nimaste.newman.core.model

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * The phone's automation editor works on the hub's own JSON (GET /api/automations/{id}): every edit
 * replaces only the keys it's about, so whatever the phone doesn't know (notifications, wall, live
 * statuses, a newer trigger type) goes back to the hub exactly as it came.
 *
 * Shapes (server/newman/automations/describe.py): trigger.items =
 *   {"type": "time", "time": "21:00", "days": [0..6]}            (Monday = 0)
 *   {"type": "state", "entities": [...], "to": state | "any"}
 *   {"type": "event", "entity": id, "event": id}
 *   {"type": "property", "entities": [...], "property": id, "op": above|below|equals|changes, "value": x}
 * actions = [{"entities": [...], "command": id, "value"?, "for"?: {"amount", "unit"}}]
 * condition.devices = [{"entities": [...], "state": id | "active", "match": any|all|none}]
 */
object AutoEdit {
    val TRIGGER_TYPES = listOf("time", "state", "event", "property")
    val ALL_DAYS = listOf(0, 1, 2, 3, 4, 5, 6)
    /** "Don't repeat for" choices, in minutes: null = the default (none, or the camera default for a camera detection), 0 = never wait. */
    val COOLDOWNS: List<Int?> = listOf(null, 0, 1, 5, 15, 30, 60)

    /** A new automation from the phone: one time part, nothing done yet, no notification. */
    fun blank(): JsonObject = buildJsonObject {
        put("name", "")
        put("enabled", true)
        put("trigger", buildJsonObject {
            put("items", JsonArray(listOf(timeItem("07:00", ALL_DAYS))))
            put("match", "any")
        })
        put("actions", JsonArray(emptyList()))
        put("notify", buildJsonObject { put("enabled", false) })
        put("recipients", JsonArray(emptyList()))
    }

    fun name(a: JsonObject): String = a.string("name") ?: ""
    fun withName(a: JsonObject, name: String) = a.withKey("name", JsonPrimitive(name))
    fun enabled(a: JsonObject): Boolean = a.bool("enabled") ?: true
    fun withEnabled(a: JsonObject, on: Boolean) = a.withKey("enabled", JsonPrimitive(on))

    // -- When --------------------------------------------------------------------------------------

    fun items(a: JsonObject): List<JsonObject> = a.obj("trigger")?.array("items")?.mapNotNull { it as? JsonObject } ?: emptyList()

    fun withItems(a: JsonObject, items: List<JsonObject>): JsonObject {
        val t = a.obj("trigger") ?: JsonObject(emptyMap())
        return a.withKey("trigger", t.withKey("items", JsonArray(items)))
    }

    fun isKnownItem(i: JsonObject): Boolean = i.string("type") in TRIGGER_TYPES

    fun timeItem(time: String, days: List<Int>): JsonObject = buildJsonObject {
        put("type", "time")
        put("time", time)
        put("days", JsonArray(days.sorted().map { JsonPrimitive(it) }))
    }

    fun days(i: JsonObject): List<Int> = i.array("days")?.mapNotNull { it.asNumberOrNull?.toInt() } ?: ALL_DAYS

    fun toggleDay(i: JsonObject, day: Int): JsonObject {
        val d = days(i).toMutableSet()
        if (day in d) d.remove(day) else d.add(day)
        return i.withKey("days", JsonArray(d.sorted().map { JsonPrimitive(it) }))
    }

    fun stateItem(entity: String, to: String = "any"): JsonObject = buildJsonObject {
        put("type", "state"); put("entities", JsonArray(listOf(JsonPrimitive(entity)))); put("to", to)
    }

    fun eventItem(entity: String, event: String): JsonObject = buildJsonObject {
        put("type", "event"); put("entity", entity); put("event", event)
    }

    fun propertyItem(entity: String, property: String, op: String = "above", value: Double? = null): JsonObject = buildJsonObject {
        put("type", "property"); put("entities", JsonArray(listOf(JsonPrimitive(entity)))); put("property", property); put("op", op)
        put("value", value?.let { JsonPrimitive(it) } ?: JsonNull)
    }

    /** The devices an item, action or condition row names (`entities`, or an older single `entity`). */
    fun entities(o: JsonObject): List<String> = o.strings("entities").ifEmpty { listOfNotNull(o.string("entity")) }

    // -- What happens ------------------------------------------------------------------------------

    fun actions(a: JsonObject): List<JsonObject> = a.array("actions")?.mapNotNull { it as? JsonObject } ?: emptyList()
    fun withActions(a: JsonObject, list: List<JsonObject>) = a.withKey("actions", JsonArray(list))

    /** An action the phone can edit: a command on some devices. */
    fun isKnownAction(o: JsonObject): Boolean = o.string("command") != null && entities(o).isNotEmpty()

    fun action(entities: List<String>, command: String, value: JsonElement? = null): JsonObject = buildJsonObject {
        put("entities", JsonArray(entities.map { JsonPrimitive(it) }))
        put("command", command)
        if (value != null) put("value", value)
    }

    /** "Then put it back after N minutes" (null = not set). Older ones say forMinutes. */
    fun forMinutes(o: JsonObject): Int? {
        o.obj("for")?.let { f ->
            val n = f.number("amount") ?: return null
            return when (f.string("unit")) {
                "seconds" -> (n / 60).toInt()
                "hours" -> (n * 60).toInt()
                else -> n.toInt()
            }
        }
        return o.number("forMinutes")?.toInt()?.takeIf { it > 0 }
    }

    fun withForMinutes(o: JsonObject, minutes: Int?): JsonObject {
        val base = JsonObject(o - "forMinutes")
        return if (minutes == null || minutes <= 0) JsonObject(base - "for")
        else base.withKey("for", buildJsonObject { put("amount", minutes); put("unit", "minutes") })
    }

    // -- Only if -----------------------------------------------------------------------------------

    fun conditionRows(a: JsonObject): List<JsonObject> = a.obj("condition")?.array("devices")?.mapNotNull { it as? JsonObject } ?: emptyList()

    fun withConditionRows(a: JsonObject, rows: List<JsonObject>): JsonObject {
        val c = a.obj("condition") ?: JsonObject(emptyMap())
        // The flat `entities`/`mode` are the hub's mirror of the rows; it rebuilds them from `devices`.
        return a.withKey("condition", JsonObject(c - "entities" - "mode").withKey("devices", JsonArray(rows)))
    }

    fun conditionRow(entity: String, state: String = "active", match: String = "any"): JsonObject = buildJsonObject {
        put("entities", JsonArray(listOf(JsonPrimitive(entity)))); put("state", state); put("match", match)
    }

    /** Other "Only if" limits the phone doesn't edit (a time window, days, daylight, dates) - kept as they are. */
    fun hasOtherLimits(a: JsonObject): Boolean {
        val c = a.obj("condition") ?: return false
        return listOf("between", "days", "sun", "dates").any { k -> c[k] != null && c[k] !is JsonNull }
    }

    // -- Don't repeat for --------------------------------------------------------------------------

    fun cooldown(a: JsonObject): Int? = a.number("cooldownMinutes")?.toInt()?.coerceAtLeast(0)
    fun withCooldown(a: JsonObject, minutes: Int?) = a.withKey("cooldownMinutes", minutes?.coerceAtLeast(0)?.let { JsonPrimitive(it) } ?: JsonNull)

    fun cooldownText(minutes: Int?): String = when (minutes) {
        null -> "Default"
        0 -> "No wait"
        1 -> "1 minute"
        60 -> "1 hour"
        else -> if (minutes % 60 == 0) "${minutes / 60} hours" else "$minutes minutes"
    }

    /** Changed since loading (the editor asks before throwing it away). */
    fun dirty(original: JsonObject?, current: JsonObject?): Boolean = original != current

    /** "07:05" from hour and minute; hour/minute from "HH:MM". */
    fun hhmm(hour: Int, minute: Int): String = "%02d:%02d".format(hour, minute)
    fun parseTime(s: String?): Pair<Int, Int> {
        val p = s?.split(":") ?: return 7 to 0
        return (p.getOrNull(0)?.toIntOrNull() ?: 7).coerceIn(0, 23) to (p.getOrNull(1)?.toIntOrNull() ?: 0).coerceIn(0, 59)
    }

}

/** Replace one key of a JSON object (a copy). */
fun JsonObject.withKey(key: String, value: JsonElement): JsonObject = JsonObject(this + (key to value))
