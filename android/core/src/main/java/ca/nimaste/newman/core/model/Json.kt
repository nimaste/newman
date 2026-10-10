package ca.nimaste.newman.core.model

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull

/** Small, forgiving readers for the free-form parts of the hub's JSON (attrs, tile config). */

val JsonElement?.asStringOrNull: String?
    get() = when (this) {
        null, is JsonNull -> null
        is JsonPrimitive -> content
        else -> null
    }

val JsonElement?.asDoubleOrNull: Double?
    get() = when (this) {
        is JsonPrimitive -> if (isString) content.toDoubleOrNull() else doubleOrNull
        else -> null
    }

/** A JSON number (not a numeric string): what JS `typeof v === 'number'` means. */
val JsonElement?.asNumberOrNull: Double?
    get() = (this as? JsonPrimitive)?.takeIf { !it.isString }?.doubleOrNull

val JsonElement?.asBooleanOrNull: Boolean?
    get() = (this as? JsonPrimitive)?.takeIf { !it.isString }?.booleanOrNull

val JsonElement?.asObjectOrNull: JsonObject? get() = this as? JsonObject
val JsonElement?.asArrayOrNull: JsonArray? get() = this as? JsonArray

fun JsonObject.string(key: String): String? = this[key].asStringOrNull
fun JsonObject.number(key: String): Double? = this[key].asNumberOrNull
fun JsonObject.bool(key: String): Boolean? = this[key].asBooleanOrNull
fun JsonObject.array(key: String): JsonArray? = this[key].asArrayOrNull
fun JsonObject.obj(key: String): JsonObject? = this[key].asObjectOrNull
fun JsonObject.strings(key: String): List<String> =
    array(key)?.mapNotNull { it.asStringOrNull } ?: emptyList()

fun jsonOf(value: Any?): JsonElement = when (value) {
    null -> JsonNull
    is JsonElement -> value
    is Boolean -> JsonPrimitive(value)
    is Number -> JsonPrimitive(value)
    is String -> JsonPrimitive(value)
    is Iterable<*> -> JsonArray(value.map { jsonOf(it) })
    else -> JsonPrimitive(value.toString())
}

/** 21.0 -> "21", 21.5 -> "21.5" (like JS's number to string). */
fun Double.plain(): String {
    if (this == Math.floor(this) && !isInfinite() && kotlin.math.abs(this) < 1e15) return toLong().toString()
    return toString()
}
