package ca.nimaste.newman.ui.icons

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Fill
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import ca.nimaste.newman.ui.theme.N

/**
 * The web dashboard's stroke icons (web/src/icons.jsx), 24x24, drawn with the current colour.
 * Names come from the hub (command icons) or from HubModel.kindIcon; unknown names fall back by
 * keyword, then to a dot - so a new kind never breaks the UI.
 */
object NIcons {
    private fun circle(cx: Float, cy: Float, r: Float) = "M${cx - r} ${cy}a$r $r 0 1 0 ${2 * r} 0a$r $r 0 1 0 ${-2 * r} 0"
    private fun rect(x: Float, y: Float, w: Float, h: Float, r: Float) =
        "M${x + r} ${y}h${w - 2 * r}a$r $r 0 0 1 $r ${r}v${h - 2 * r}a$r $r 0 0 1 ${-r} ${r}h${-(w - 2 * r)}a$r $r 0 0 1 ${-r} ${-r}v${-(h - 2 * r)}a$r $r 0 0 1 $r ${-r}z"

    val PATHS: Map<String, List<String>> = mapOf(
        "lock" to listOf(rect(5f, 11f, 14f, 10f, 2f), "M8 11V7a4 4 0 0 1 8 0v4"),
        "unlock" to listOf(rect(5f, 11f, 14f, 10f, 2f), "M8 11V7a4 4 0 0 1 7.5-2"),
        "clock" to listOf(circle(12f, 12f, 9f), "M12 7v5l3 2"),
        "x" to listOf("M6 6l12 12M18 6L6 18"),
        "check" to listOf("M5 12l5 5 9-10"),
        "plus" to listOf("M12 5v14M5 12h14"),
        "minus" to listOf("M5 12h14"),
        "chevronRight" to listOf("M9 6l6 6-6 6"),
        "chevronLeft" to listOf("M15 5l-7 7 7 7"),
        "play" to listOf("M7 5v14l11-7z"),
        "alert" to listOf("M12 3l10 18H2z", "M12 10v4M12 17.5v.5"),
        "bell" to listOf("M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9", "M10.3 21a1.9 1.9 0 0 0 3.4 0"),
        "shield" to listOf("M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"),
        "shieldOff" to listOf("M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z", "M4 4l16 16"),
        "home" to listOf("M3 11l9-7 9 7", "M5 10v10h14V10"),
        "door" to listOf("M6 21V4h12v17M3 21h18", "M14 12v1"),
        "doorOpen" to listOf("M6 21V4h12v17M3 21h18", "M6 4l7 2v15"),
        "garage" to listOf("M3 21V9l9-5 9 5v12", "M7 21v-8h10v8M7 16h10"),
        "light" to listOf("M9 18h6M10 21h4", "M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"),
        "bulb" to listOf("M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.8.8 1 1.5 1 2.5h6c0-1 .2-1.7 1-2.5A6 6 0 0 0 12 3z"),
        "power" to listOf("M12 3v9", "M6.3 7a8 8 0 1 0 11.4 0"),
        "camera" to listOf("M4 8h3l2-3h6l2 3h3v11H4z", circle(12f, 13f, 3.5f)),
        "people" to listOf(circle(9f, 8f, 3.2f), "M3 20a6 6 0 0 1 12 0", circle(17f, 9f, 2.6f), "M15.5 14.2A5 5 0 0 1 21 19"),
        "user" to listOf(circle(12f, 8f, 4f), "M4 21a8 8 0 0 1 16 0"),
        "grid" to listOf(rect(4f, 4f, 7f, 7f, 1.5f), rect(13f, 4f, 7f, 7f, 1.5f), rect(4f, 13f, 7f, 7f, 1.5f), rect(13f, 13f, 7f, 7f, 1.5f)),
        "list" to listOf("M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"),
        "sliders" to listOf("M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4"),
        "thermometer" to listOf("M10 14V5a2 2 0 0 1 4 0v9", circle(12f, 17f, 3.5f)),
        "motion" to listOf(circle(13f, 4.5f, 1.8f), "M9 21l2.5-6 2.5 2v4M8 11l3-3 3 2 3 1M11 8l-1.5 5"),
        "vacuum" to listOf(circle(12f, 12f, 9f), "M5.6 8.2a7.6 7.6 0 0 1 12.8 0", circle(12f, 8f, 1.6f), "M9 15.5h6"),
        "pause" to listOf("M8 5v14M16 5v14"),
        "stop" to listOf(rect(6f, 6f, 12f, 12f, 2f)),
        "refresh" to listOf("M20 11a8 8 0 0 0-14.5-4.5L4 8M4 4v4h4", "M4 13a8 8 0 0 0 14.5 4.5L20 16M20 20v-4h-4"),
        "dot" to listOf(circle(12f, 12f, 3f)),
        "key" to listOf(circle(8f, 15f, 4f), "M10.8 12.2L20 3M16 7l3 3M14 9l2 2"),
        "moon" to listOf("M20 14a8 8 0 1 1-10-10 7 7 0 0 0 10 10z"),
        "sun" to listOf(circle(12f, 12f, 4f), "M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"),
        "fan" to listOf(circle(12f, 12f, 1.6f), "M12 10.4c-1-3.4.3-6.4 3-6.4 2.2 0 2.6 2.8.4 4.4L13.4 11M13.6 12c3.4-1 6.4.3 6.4 3 0 2.2-2.8 2.6-4.4.4L13 13.4M12 13.6c1 3.4-.3 6.4-3 6.4-2.2 0-2.6-2.8-.4-4.4l2-1.6M10.4 12c-3.4 1-6.4-.3-6.4-3 0-2.2 2.8-2.6 4.4-.4l2 2"),
        "blinds" to listOf("M4 4h16M5 4v12M19 4v12M5 8h14M5 12h14M5 16h14M12 16v4M10.5 21h3"),
        "gauge" to listOf("M4.5 18a8.5 8.5 0 1 1 15 0", "M12 14l4-5M12 14.1v.01"),
        "droplet" to listOf("M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"),
        "flame" to listOf("M12 21c-3.9 0-6.5-2.6-6.5-6.1 0-3.7 3-5.6 3.6-9.4 2.4 1.5 3.4 3.6 3.5 5.6 1-.6 1.7-1.7 1.9-3 2.4 1.9 4 4.5 4 6.8 0 3.5-2.6 6.1-6.5 6.1z"),
        "toggle" to listOf(rect(3f, 7f, 18f, 10f, 5f), circle(16f, 12f, 2.6f)),
        "megaphone" to listOf("M4 10v4a1 1 0 0 0 1 1h2l8 4V5L7 9H5a1 1 0 0 0-1 1zM7 15l1.5 5h2.5L9.8 15.6M18.5 9.5a3.5 3.5 0 0 1 0 5"),
        "pointer" to listOf("M9 11V5.5a1.5 1.5 0 0 1 3 0V11M12 10.5V9a1.5 1.5 0 0 1 3 0v2M15 10.5a1.5 1.5 0 0 1 3 0V15a6 6 0 0 1-6 6h-.7a6 6 0 0 1-4.9-2.6L4 15a1.6 1.6 0 0 1 2.5-2L9 15.5"),
        "up" to listOf("M12 19V5M6 11l6-6 6 6"),
        "down" to listOf("M12 5v14M6 13l6 6 6-6"),
        "bellOff" to listOf("M6.3 6.3A6 6 0 0 0 6 8c0 7-3 9-3 9h14M18 13.5c-.1-1.3-.1-2.9 0-5.5a6 6 0 0 0-8.9-5.2M10.3 21a1.9 1.9 0 0 0 3.4 0M3 3l18 18"),
        "speaker" to listOf(rect(6f, 3f, 12f, 18f, 2f), circle(12f, 14f, 3f), "M12 7.5v.01"),
        "webhook" to listOf("M9 8.5a3.5 3.5 0 1 1 5.3 3L17 16M8.2 12.6 5.5 17a3.5 3.5 0 1 0 6 1.5h6M17 16.5a3.5 3.5 0 1 1-1.5 6"),
        "send" to listOf("M21 3 10 14M21 3l-7 18-4-7-7-4z"),
        "volume" to listOf("M4 9v6h4l5 4V5L8 9zM16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"),
        "cloud" to listOf("M7 18h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 9a4.5 4.5 0 0 0 1 9z"),
        "calendar" to listOf(rect(4f, 5f, 16f, 16f, 2f), "M4 10h16M9 3v4M15 3v4"),
        "fingerprint" to listOf("M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M9 9v1M15 9v1M12 9v4h-1M9 16c1.7 1.3 4.3 1.3 6 0"),
        "expand" to listOf("M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"),
        "more" to listOf("M6 12h.01M12 12h.01M18 12h.01"),
        "bolt" to listOf("M13 2L4 14h7l-1 8 9-12h-7z"),
        "search" to listOf(circle(11f, 11f, 7f), "M20 20l-3.5-3.5"),
        "settings" to listOf(circle(12f, 12f, 3f), "M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"),
    )

    private val ALIASES = mapOf(
        "arm" to "shield", "arm_home" to "shield", "arm_away" to "shield", "disarm" to "shieldOff", "alarm" to "shield",
        "snooze" to "clock", "dismiss" to "x", "close" to "door", "contact" to "door", "open_door" to "doorOpen",
        "on" to "power", "off" to "power", "turn_on" to "power", "turn_off" to "power", "switch" to "power",
        "doorbell" to "bell", "climate" to "thermometer", "sensor" to "gauge", "bell-off" to "bellOff", "bell_off" to "bellOff",
        "siren" to "megaphone", "press" to "pointer", "set_volume" to "volume", "play_pause" to "play", "leak" to "droplet",
        "smoke" to "flame", "cover" to "blinds", "dock" to "home", "locate" to "bell", "cleaning" to "vacuum", "robot" to "vacuum",
        "bulb-off" to "bulb", "shield-off" to "shieldOff", "thermostat" to "thermometer", "lighting" to "light",
        "security" to "shield", "cameras" to "camera", "garage-door" to "garage",
    )

    private val KEYWORDS = listOf(
        Regex("unlock") to "unlock", Regex("lock") to "lock", Regex("garage|door") to "garage",
        Regex("bulb|light|bright|dim|lamp") to "light", Regex("temp|thermo|heat|cool|climate") to "thermometer",
        Regex("power|off|on$") to "power", Regex("bell|ring") to "bell", Regex("shield|arm|alarm") to "shield",
    )

    fun resolve(name: String?): String {
        if (name.isNullOrEmpty()) return "dot"
        if (PATHS.containsKey(name)) return name
        ALIASES[name]?.let { if (PATHS.containsKey(it)) return it }
        val n = name.lowercase()
        return KEYWORDS.firstOrNull { it.first.containsMatchIn(n) }?.second ?: "dot"
    }

    private val cache = HashMap<String, List<Path>>()

    @Synchronized
    fun paths(name: String?): List<Path> = cache.getOrPut(resolve(name)) {
        (PATHS[resolve(name)] ?: PATHS.getValue("dot")).map { PathParser().parsePathString(it).toPath() }
    }

    @Synchronized
    fun raw(d: String): Path = cache.getOrPut("raw:$d") { listOf(PathParser().parsePathString(d).toPath()) }.first()
}

/** A stroke icon by name. */
@Composable
fun NIcon(name: String?, modifier: Modifier = Modifier, size: Dp = 22.dp, color: Color = N.Text, stroke: Float = 2f) {
    val paths = remember(name) { NIcons.paths(name) }
    Canvas(modifier.size(size)) {
        val s = this.size.minDimension / 24f
        scale(s, s, pivot = androidx.compose.ui.geometry.Offset.Zero) {
            paths.forEach { drawPath(it, color, style = Stroke(width = stroke, cap = StrokeCap.Round, join = StrokeJoin.Round)) }
        }
    }
}

/** Weather condition icons (web/src/wall/WeatherIcon.jsx); the moon stands in for the sun at night. */
@Composable
fun WeatherIcon(condition: String?, isDay: Boolean = true, modifier: Modifier = Modifier, size: Dp = 34.dp, stroke: Float = 1.7f) {
    val layers = remember(condition, isDay) { weatherLayers(condition ?: "cloudy", isDay) }
    Canvas(modifier.size(size)) {
        val s = this.size.minDimension / 24f
        scale(s, s, pivot = androidx.compose.ui.geometry.Offset.Zero) {
            layers.forEach { l ->
                val p = NIcons.raw(l.d)
                if (l.fill != null) drawPath(p, l.fill, style = Fill)
                drawPath(p, l.color, style = Stroke(width = stroke + l.extra, cap = StrokeCap.Round, join = StrokeJoin.Round))
            }
        }
    }
}

private data class Layer(val d: String, val color: Color = N.Neutral, val extra: Float = 0f, val fill: Color? = null)

private const val CLOUD = "M7 18h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 9a4.5 4.5 0 0 0 1 9z"
private const val CLOUD_HIGH = "M7 14h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 5a4.5 4.5 0 0 0 1 9z"
private const val SUN_RAYS = "M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"
private const val MOON = "M20 14a8 8 0 1 1-10-10 7 7 0 0 0 10 10z"
private const val SMALL_CLOUD = "M10 21h8a3.2 3.2 0 0 0 .4-6.4A4.6 4.6 0 0 0 9.6 15a3 3 0 0 0 .4 6z"
private const val TINY_CLOUD = "M14.5 21.5h5a2.3 2.3 0 0 0 .3-4.6 3.3 3.3 0 0 0-6.3 1A1.8 1.8 0 0 0 14.5 21.5z"
private val SUN = Color(0xFFF5B942)
private val WET = Color(0xFF7FB8FF)
private val CLOUD_FILL = Color(0xFF1A2447)

private fun sun(small: Boolean) = if (small) listOf(Layer("M5 8a3 3 0 1 0 6 0a3 3 0 1 0 -6 0", SUN), Layer("M8 2v1.3M2 8h1.3M3.8 3.8l.9.9M12.2 3.8l-.9.9M3.8 12.2l.9-.9", SUN))
else listOf(Layer("M8 12a4 4 0 1 0 8 0a4 4 0 1 0 -8 0", SUN), Layer(SUN_RAYS, SUN))

private fun moon(small: Boolean) = listOf(Layer(if (small) "M11.5 9.5A4.8 4.8 0 0 1 6.5 3.5a4.8 4.8 0 1 0 5 6z" else MOON))

private fun weatherLayers(c: String, day: Boolean): List<Layer> = when (c) {
    "clear" -> if (day) sun(false) else moon(false)
    "mostly-clear" -> (if (day) sun(false) else moon(false)) + Layer(TINY_CLOUD, fill = CLOUD_FILL)
    "partly-cloudy" -> (if (day) sun(true) else moon(true)) + Layer(SMALL_CLOUD, fill = CLOUD_FILL)
    "fog" -> listOf(Layer(CLOUD_HIGH), Layer("M5 17.5h14M7 21h10"))
    "drizzle" -> listOf(Layer(CLOUD_HIGH), Layer("M8 18v.01M12 19v.01M16 18v.01M10 21.5v.01M14 21.5v.01", WET, 0.8f))
    "rain" -> listOf(Layer(CLOUD_HIGH), Layer("M8 17l-1.2 3.5M12 17l-1.2 3.5M16 17l-1.2 3.5", WET))
    "showers" -> listOf(Layer(CLOUD_HIGH), Layer("M9.5 17l-1.5 4.5M14.5 17L13 21.5", WET))
    "freezing-rain" -> listOf(Layer(CLOUD_HIGH), Layer("M8 17l-1.2 3.5M16 17l-1.2 3.5", WET), Layer("M12 18v.01M11.5 21.5v.01", WET, 0.8f))
    "snow" -> listOf(Layer(CLOUD_HIGH), Layer("M12 16.5v5M9.8 17.8l4.4 2.4M14.2 17.8l-4.4 2.4"), Layer("M6.5 19v.01M17.5 19v.01", extra = 0.8f))
    "snow-showers" -> listOf(Layer(CLOUD_HIGH), Layer("M12 16.5v5M9.8 17.8l4.4 2.4M14.2 17.8l-4.4 2.4"), Layer("M7.5 17L6.3 20.5", WET))
    "thunderstorm" -> listOf(Layer(CLOUD_HIGH), Layer("M13 15l-3 4h4l-3 4", SUN))
    else -> listOf(Layer(CLOUD))
}
