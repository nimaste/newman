package ca.nimaste.newman.core.model

import kotlinx.serialization.json.JsonObject
import java.util.Locale

/** A weather device's own words for the phone (the iPhone's WeatherModel, ios/Shared/Core/TileLogic.swift). */
object Weather {
    /**
     * Today's rain or snow for the one-row weather card's right side: ["71% chance", "1.2 mm"].
     * Today's forecast entry first ([today] is its "date", yyyy-MM-dd), else the device's own
     * attrs; snow wins over rain.
     */
    fun wetToday(attrs: JsonObject, today: String): List<String> {
        val day = attrs.array("forecast")?.mapNotNull { it.asObjectOrNull }?.firstOrNull { it.string("date") == today }
        val out = mutableListOf<String>()
        (day?.number("precipChance") ?: attrs.number("precipChance"))?.takeIf { it > 0 }?.let { out += "${Math.round(it)}% chance" }
        val snow = (day?.number("snow") ?: attrs.number("snowToday"))?.takeIf { it > 0 }
        val rain = (day?.number("rain") ?: attrs.number("rainToday"))?.takeIf { it > 0 }
        when {
            snow != null -> out += "${oneDecimal(snow)} cm"
            rain != null -> out += "${oneDecimal(rain)} mm"
        }
        return out
    }

    /** The weather alerts the device reports (attrs.alerts: {name, colour, type, area, text}). */
    fun alerts(attrs: JsonObject): List<JsonObject> = attrs.array("alerts")?.mapNotNull { it.asObjectOrNull } ?: emptyList()

    /** At most one decimal, none when whole: 1.2, 3, 0.5. */
    fun oneDecimal(v: Double): String {
        val r = Math.round(v * 10) / 10.0
        return if (r % 1.0 == 0.0) r.toLong().toString() else String.format(Locale.US, "%.1f", r)
    }
}
