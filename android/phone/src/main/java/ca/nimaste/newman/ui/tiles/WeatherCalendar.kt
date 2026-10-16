package ca.nimaste.newman.ui.tiles

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.Meta
import ca.nimaste.newman.core.model.Tile
import ca.nimaste.newman.core.model.array
import ca.nimaste.newman.core.model.asObjectOrNull
import ca.nimaste.newman.core.model.bool
import ca.nimaste.newman.core.model.number
import ca.nimaste.newman.core.model.string
import ca.nimaste.newman.core.model.strings
import ca.nimaste.newman.ui.common.GlassCard
import ca.nimaste.newman.ui.common.LocalFillHeight
import androidx.compose.ui.unit.Dp
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.icons.WeatherIcon
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import kotlinx.coroutines.delay
import kotlinx.serialization.json.JsonObject
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.time.format.TextStyle as JTextStyle
import java.util.Locale
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/** Devices are picked by what they report (a forecast, upcoming events), never by kind. */
fun hasForecast(e: Entity?) = e?.attrs?.array("forecast") != null
fun hasAgenda(e: Entity?) = e?.attrs?.array("upcoming") != null

private fun deg(v: Double?) = if (v == null) "–" else "${v.roundToInt()}°"

@Composable
fun rememberNow(periodMs: Long = 60_000): Long {
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(periodMs) {
        while (true) {
            delay(periodMs)
            now = System.currentTimeMillis()
        }
    }
    return now
}

/**
 * Weather: PhoneOverview's half tile; across the phone, one row (icon, temperature, sky and
 * high/low, humidity and rain on the right), with the week ahead below when the tile is tall enough.
 */
@Composable
fun WeatherTile(tile: Tile, entities: Map<String, Entity>, meta: Meta?, compact: Boolean, textScale: Float) {
    val now = rememberNow()
    val fill = LocalFillHeight.current // read before the card takes it
    val chosen = entities[tile.config.string("entity")]
    val e = if (hasForecast(chosen)) chosen else entities.values.firstOrNull { hasForecast(it) }
    GlassCard(Modifier.fillMaxWidth().then(if (compact) Modifier.height(172.dp) else Modifier), padding = PaddingValues(if (compact) 12.dp else 14.dp)) {
        if (e == null || e.attrs.number("temperature") == null) {
            Text(if (e == null) "Add a Weather connection in the Newman dashboard." else "Waiting for the weather…", style = T.tiny)
            return@GlassCard
        }
        val a = e.attrs
        val label = HubModel.stateLabel(e, meta?.kind(e.kind)) ?: ""
        val today = LocalDate.now().toString()
        val forecast = a.array("forecast")?.mapNotNull { it.asObjectOrNull } ?: emptyList()
        val todayF = forecast.firstOrNull { it.string("date") == today }
        val chance = todayF?.number("precipChance") ?: a.number("precipChance")
        val wet = listOfNotNull(
            chance?.takeIf { it > 0 }?.let { "${it.roundToInt()}% chance today" },
            (todayF?.number("snow") ?: a.number("snowToday"))?.takeIf { it > 0 }?.let { "${fmt1(it)} cm snow" }
                ?: (todayF?.number("rain") ?: a.number("rainToday"))?.takeIf { it > 0 }?.let { "${fmt1(it)} mm rain" },
        )
        if (!compact) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                WeatherIcon(e.state, a.bool("isDay") != false, size = 40.dp)
                Text(deg(a.number("temperature")), style = T.number.copy(fontSize = 32.sp * textScale))
                Column(Modifier.weight(1f)) {
                    Text(label, style = T.label.copy(fontSize = 15.sp * textScale), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text("H ${deg(a.number("high"))} · L ${deg(a.number("low"))}", style = T.small.copy(fontSize = 13.sp * textScale, color = N.Muted), maxLines = 1)
                }
                val side = listOfNotNull(a.number("humidity")?.let { "${it.roundToInt()}% humidity" }) + wet
                if (side.isNotEmpty()) {
                    Column(horizontalAlignment = Alignment.End) {
                        side.take(2).forEach { Text(it, style = T.tiny.copy(fontSize = 12.sp * textScale), maxLines = 1) }
                    }
                }
            }
        } else {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                WeatherIcon(e.state, a.bool("isDay") != false, size = 34.dp)
                Text(deg(a.number("temperature")), style = T.number.copy(fontSize = 30.sp * textScale))
            }
            Text("$label · H ${deg(a.number("high"))} L ${deg(a.number("low"))}", style = T.small.copy(fontSize = 13.sp * textScale),
                maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (wet.isNotEmpty()) Text(wet.joinToString(" · "), style = T.tiny)
        }
        val alerts = a.array("alerts")?.mapNotNull { it.asObjectOrNull } ?: emptyList()
        alerts.maxByOrNull { mapOf("red" to 3, "orange" to 2, "yellow" to 1)[it.string("colour")] ?: 0 }?.let { top ->
            Row(Modifier.padding(top = 4.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                NIcon("alert", size = 14.dp, color = N.Amber)
                Text(top.string("name") ?: "Weather alert", style = T.tiny.copy(color = N.Amber), maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        // The week ahead: a flowed page, or a placed tile with room for it.
        if (!compact && (fill == Dp.Unspecified || fill >= 200.dp)) {
            val days = forecast.filter { (it.string("date") ?: "") > today }.take(6)
            val lows = days.mapNotNull { it.number("low") }
            val highs = days.mapNotNull { it.number("high") }
            val lo = lows.minOrNull() ?: 0.0
            val span = max(1.0, (highs.maxOrNull() ?: 0.0) - lo)
            Column(Modifier.padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                days.forEach { d -> DayRow(d, lo, span) }
            }
        }
    }
}

private fun fmt1(v: Double) = if (v % 1.0 == 0.0) v.toInt().toString() else String.format(Locale.getDefault(), "%.1f", v)

@Composable
private fun DayRow(d: JsonObject, lo: Double, span: Double) {
    val low = d.number("low")
    val high = d.number("high")
    Row(Modifier.fillMaxWidth().height(26.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(d.string("day") ?: "", style = T.small, modifier = Modifier.width(40.dp))
        WeatherIcon(d.string("condition"), true, size = 20.dp)
        Text(d.number("precipChance")?.let { "${it.roundToInt()}%" } ?: "", style = T.tiny.copy(color = N.Blue), modifier = Modifier.width(38.dp))
        Text(deg(low), style = T.small.copy(color = N.Muted), modifier = Modifier.width(32.dp))
        Box(Modifier.weight(1f).height(6.dp).background(Color(0x14FFFFFF), RoundedCornerShape(3.dp))) {
            if (low != null && high != null) {
                val start = ((low - lo) / span).toFloat().coerceIn(0f, 1f)
                val width = max(0.04, (high - low) / span).toFloat().coerceIn(0.04f, 1f - start)
                Row(Modifier.fillMaxWidth().fillMaxHeight()) {
                    if (start > 0f) Box(Modifier.weight(start))
                    Box(Modifier.weight(width).fillMaxHeight().background(N.Amber, RoundedCornerShape(3.dp)))
                    val rest = 1f - start - width
                    if (rest > 0.001f) Box(Modifier.weight(rest))
                }
            }
        }
        Text(deg(high), style = T.small, modifier = Modifier.width(32.dp))
    }
}

/** "Coming up" from the calendars (all of them, or the tile's), in their colours. */
@Composable
fun CalendarTile(tile: Tile, entities: Map<String, Entity>, textScale: Float) {
    val now = rememberNow(30_000)
    val ids = tile.config.strings("entities")
    val cals = entities.values.filter { hasAgenda(it) && (ids.isEmpty() || it.id in ids) }
    val days = (tile.config.number("days")?.toInt() ?: 31).coerceIn(1, 31)
    val zone = ZoneId.systemDefault()
    val todayStart = LocalDate.now(zone).atStartOfDay(zone).toInstant().toEpochMilli()
    val until = LocalDate.now(zone).plusDays(days.toLong()).atStartOfDay(zone).toInstant().toEpochMilli()
    data class Ev(val title: String, val start: Long, val end: Long, val allDay: Boolean, val location: String, val colour: Color, val day: LocalDate, val on: Boolean)
    val events = cals.flatMap { c ->
        val colour = parseColour(c.attrs.string("colour")) ?: N.Teal
        (c.attrs.array("upcoming") ?: emptyList()).mapNotNull { it.asObjectOrNull }.mapNotNull { ev ->
            val s = parseTime(ev.string("start"), zone) ?: return@mapNotNull null
            val e = parseTime(ev.string("end"), zone) ?: s
            if (e <= now || s >= until) return@mapNotNull null
            val allDay = ev.bool("allDay") == true
            val shownOn = java.time.Instant.ofEpochMilli(max(s, todayStart)).atZone(zone).toLocalDate()
            Ev(ev.string("title") ?: "", s, e, allDay, ev.string("location") ?: "", colour, shownOn, !allDay && s <= now && now < e)
        }
    }.sortedWith(compareBy<Ev>({ it.day }, { !it.allDay }, { it.start })).take(8)

    GlassCard(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            NIcon("calendar", size = 18.dp)
            Text("Coming up", style = T.label.copy(fontSize = 15.sp * textScale))
        }
        Column(Modifier.padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            when {
                entities.values.none { hasAgenda(it) } -> Text("Add a Calendars connection in the Newman dashboard.", style = T.tiny)
                events.isEmpty() -> Text("Nothing coming up.", style = T.tiny)
            }
            events.forEach { ev ->
                Row(Modifier.fillMaxWidth().heightIn(min = 36.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Box(Modifier.width(4.dp).height(38.dp).background(ev.colour, RoundedCornerShape(4.dp)))
                    Column {
                        Text((if (ev.on) "Now · " else "") + ev.title, style = T.label.copy(fontSize = 14.sp * textScale), maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(listOf(dayLabel(ev.day), timeText(ev.allDay, ev.start, ev.end, zone)).joinToString(" · ") +
                            (if (ev.location.isNotBlank()) " · ${ev.location}" else ""), style = T.tiny, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
    }
}

private fun parseColour(s: String?): Color? = try {
    s?.let { Color(android.graphics.Color.parseColor(it)) }
} catch (e: Exception) {
    null
}

/** ISO local ("2026-10-01T00:00:00") or with an offset. */
private fun parseTime(s: String?, zone: ZoneId): Long? {
    if (s.isNullOrBlank()) return null
    return try {
        java.time.OffsetDateTime.parse(s).toInstant().toEpochMilli()
    } catch (e: Exception) {
        try {
            LocalDateTime.parse(s).atZone(zone).toInstant().toEpochMilli()
        } catch (e: Exception) {
            try {
                LocalDate.parse(s).atStartOfDay(zone).toInstant().toEpochMilli()
            } catch (e: Exception) {
                null
            }
        }
    }
}

private fun dayLabel(d: LocalDate): String {
    val today = LocalDate.now()
    return when (d) {
        today -> "Today"
        today.plusDays(1) -> "Tomorrow"
        else -> d.dayOfWeek.getDisplayName(JTextStyle.SHORT, Locale.getDefault()) + " " +
            d.format(DateTimeFormatter.ofPattern("MMM d", Locale.getDefault()))
    }
}

private fun timeText(allDay: Boolean, start: Long, end: Long, zone: ZoneId): String {
    if (allDay) return "All day"
    val f = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT)
    val s = java.time.Instant.ofEpochMilli(start).atZone(zone)
    val e = java.time.Instant.ofEpochMilli(end).atZone(zone)
    if (s.toLocalDate() != e.toLocalDate()) return "From ${s.format(f)}"
    if (end - start < 60_000) return s.format(f)
    return "${s.format(f)} – ${e.format(f)}"
}

/** "17° Clear" for the first page's chips (the first device with a forecast). */
fun weatherChip(entities: Collection<Entity>, meta: Meta?): String? {
    val e = entities.firstOrNull { hasForecast(it) } ?: return null
    val t = e.attrs.number("temperature") ?: return null
    return "${deg(t)} ${HubModel.stateLabel(e, meta?.kind(e.kind)) ?: ""}"
}

@Suppress("unused")
private fun clamp01(v: Float) = min(1f, max(0f, v))
