package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.model.Weather
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Test

/** The one-row weather card's right side, as the iPhone's WeatherModel.wetToday says it. */
class WeatherTest {
    private fun attrs(json: String) = NewmanJson.parseToJsonElement(json) as JsonObject

    @Test fun todaysForecastFirst() {
        val a = attrs("""{"temperature": 10, "precipChance": 5, "rainToday": 9,
            "forecast": [{"date": "2026-10-16", "precipChance": 71.4, "rain": 1.25}, {"date": "2026-10-17", "rain": 7}]}""")
        assertEquals(listOf("71% chance", "1.3 mm"), Weather.wetToday(a, "2026-10-16"))
    }

    @Test fun snowWinsAndWholeAmountsHaveNoDecimal() {
        val a = attrs("""{"forecast": [{"date": "2026-10-16", "precipChance": 90, "snow": 3.0, "rain": 2}]}""")
        assertEquals(listOf("90% chance", "3 cm"), Weather.wetToday(a, "2026-10-16"))
    }

    @Test fun theDevicesOwnWhenNoForecastForToday() {
        assertEquals(listOf("40% chance", "0.5 mm"), Weather.wetToday(attrs("""{"precipChance": 40, "rainToday": 0.5}"""), "2026-10-16"))
        assertEquals(emptyList<String>(), Weather.wetToday(attrs("""{"precipChance": 0, "rainToday": 0}"""), "2026-10-16"))
    }

    @Test fun alerts() {
        assertEquals(listOf("Rainfall warning"),
            Weather.alerts(attrs("""{"alerts": [{"name": "Rainfall warning", "colour": "yellow"}]}""")).map { (it["name"] as kotlinx.serialization.json.JsonPrimitive).content })
        assertEquals(0, Weather.alerts(attrs("{}")).size)
    }
}
