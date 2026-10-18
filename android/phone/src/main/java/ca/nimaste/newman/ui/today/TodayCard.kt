package ca.nimaste.newman.ui.today

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HealthItem
import ca.nimaste.newman.core.model.Muting
import ca.nimaste.newman.core.today.TodayView
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.GlassButton
import ca.nimaste.newman.ui.common.MuteSheet
import ca.nimaste.newman.ui.common.heading
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import ca.nimaste.newman.ui.common.GlassCard
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private fun toneColor(t: TodayView.Tone): Color = when (t) {
    TodayView.Tone.URGENT -> N.Coral
    TodayView.Tone.DUE -> N.Amber
    TodayView.Tone.DONE -> N.Teal
    TodayView.Tone.LATER -> Color(0x59EEF2FF)
}

private fun headsColor(icon: String): Color = when (icon) {
    "warning" -> N.Coral
    "rain" -> N.Blue
    else -> N.Amber
}

private fun parseColour(hex: String): Color = try {
    Color(android.graphics.Color.parseColor(if (hex.startsWith("#")) hex else "#$hex"))
} catch (e: Exception) {
    N.Teal
}

/** Today's summary for the page header ("2 things to do · 1 event"), or null. */
@Composable
fun todaySummary(): String? {
    val app = LocalApp.current
    val b by app.today.briefing.collectAsState()
    val done by app.today.done.collectAsState()
    val brief = b ?: return null
    return TodayView.card(brief, done, System.currentTimeMillis() / 1000.0).summary.ifEmpty { null }
}

/**
 * The Today card (the wall's Today page, phone-sized): To do with Done / Remind tomorrow / Open
 * right there, today's events and heads-up. Calm: a few rows, the rest behind "N more".
 * [showWhenEmpty]: the dashboard has a Today page, so say "Nothing needs doing today" rather than
 * hiding.
 */
@Composable
fun TodayCard(showWhenEmpty: Boolean, onOpenDevice: (String) -> Unit) {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    val b by app.today.briefing.collectAsState()
    val done by app.today.done.collectAsState()
    var expanded by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf<String?>(null) }
    var now by remember { mutableStateOf(System.currentTimeMillis() / 1000.0) }
    LaunchedEffect(Unit) {
        while (true) {
            app.today.refresh()
            now = System.currentTimeMillis() / 1000.0
            delay(60_000)
        }
    }
    val brief = b ?: return
    val card = TodayView.card(brief, done, now, rows = if (expanded) 99 else TodayView.ROWS)
    if (card.empty && !showWhenEmpty) return

    fun act(id: String, block: suspend () -> String?) {
        busy = id
        scope.launch {
            block()?.let { app.toast(it, ok = false) }
            busy = null
            now = System.currentTimeMillis() / 1000.0
        }
    }

    // Devices with a problem get their own warning card on top: one row per problem that shows, each with
    // Mute (muted ones are gone; a device whose problems are all muted drops out until the hub's list catches up).
    val state by app.state.collectAsState()
    var muting by remember { mutableStateOf<Pair<Entity, HealthItem>?>(null) }
    val deviceRows = card.rows.filter { it.item.source == "device" }.filter { r ->
        val e = r.entity?.let { state.named[it] }
        e == null || e.health.items.isEmpty() || Muting.active(e.health).isNotEmpty()
    }
    val rows = card.rows - deviceRows.toSet()
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
    if (deviceRows.isNotEmpty()) {
        GlassCard(Modifier.fillMaxWidth(), background = N.CoralCard, border = N.CoralBorder, padding = PaddingValues(horizontal = 14.dp, vertical = 6.dp)) {
            // (device row, the problem it shows: one of health.items, or null on an older hub - then all its issues)
            val lines = deviceRows.flatMap { r ->
                val e = r.entity?.let { state.named[it] }
                val problems = e?.health?.items?.takeIf { it.isNotEmpty() }?.let { Muting.active(e.health) }
                if (problems.isNullOrEmpty()) listOf(Triple(r, e, null as HealthItem?)) else problems.map { Triple(r, e, it) }
            }
            lines.forEachIndexed { i, (r, e, problem) ->
                val issues = e?.health?.issues?.takeIf { it.isNotEmpty() }?.joinToString(" · ")
                Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Box(Modifier.size(36.dp).background(N.CoralBg, CircleShape), contentAlignment = Alignment.Center) {
                        NIcon("alert", size = 18.dp, color = N.Coral, stroke = 2.2f)
                    }
                    Column(Modifier.weight(1f).clickable(enabled = r.entity != null, onClickLabel = "open ${e?.name ?: "the device"}", role = Role.Button) {
                        r.entity?.let(onOpenDevice)
                    }) {
                        Text(e?.name ?: r.title.substringBefore(':'), style = T.label, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(problem?.text ?: issues ?: r.status, style = T.small.copy(color = N.CoralText), maxLines = 3, overflow = TextOverflow.Ellipsis)
                    }
                    if (e != null && problem != null && problem.id.isNotEmpty()) {
                        GlassButton("Mute", height = 36.dp, background = N.CoralBg, border = N.CoralBorder) { muting = e to problem }
                    } else if (r.entity != null) {
                        GlassButton("Open", height = 36.dp, background = N.CoralBg, border = N.CoralBorder) { onOpenDevice(r.entity!!) }
                    }
                }
                if (i < lines.lastIndex) Box(Modifier.fillMaxWidth().height(1.dp).background(N.CoralBorder))
            }
        }
    }
    if (card.empty || rows.isNotEmpty() || card.events.isNotEmpty() || card.headsUp.isNotEmpty()) GlassCard(Modifier.fillMaxWidth(), padding = PaddingValues(horizontal = 14.dp, vertical = 12.dp)) {
        if (card.empty) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                NIcon("check", size = 20.dp, color = N.Teal)
                Text("Nothing needs doing today.", style = T.body.copy(color = N.Muted))
            }
            return@GlassCard
        }
        if (rows.isNotEmpty()) {
            Text("TO DO", style = T.caps, modifier = Modifier.padding(bottom = 4.dp).heading())
            rows.forEachIndexed { i, r ->
                TodoRow(r, busy = busy == r.id,
                    onDone = { act(r.id) { app.today.done(r.item) } },
                    onSnooze = { act(r.id) { app.today.snooze(r.item).also { if (it == null) app.toast("We'll remind you tomorrow") } } },
                    onUndo = { act(r.id) { app.today.undo(r.item) } },
                    onOpen = { r.entity?.let(onOpenDevice) })
                if (i < rows.lastIndex) Box(Modifier.fillMaxWidth().height(1.dp).background(N.Divider))
            }
            if (card.more > 0) {
                Text("${card.more} more", style = T.small.copy(color = N.Muted),
                    modifier = Modifier.fillMaxWidth().clickable(role = Role.Button) { expanded = true }.padding(vertical = 14.dp))
            } else if (expanded && card.rows.size > TodayView.ROWS) {
                Text("Show less", style = T.small.copy(color = N.Muted),
                    modifier = Modifier.fillMaxWidth().clickable(role = Role.Button) { expanded = false }.padding(vertical = 14.dp))
            }
        }
        if (card.events.isNotEmpty()) {
            Text("ON TODAY", style = T.caps, modifier = Modifier.padding(top = if (rows.isEmpty()) 0.dp else 10.dp, bottom = 6.dp).heading())
            card.events.forEach { ev ->
                Row(Modifier.fillMaxWidth().padding(vertical = 5.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(if (ev.now) "Now" else ev.time, style = T.small.copy(color = if (ev.now) N.Teal else N.Muted), modifier = Modifier.width(68.dp), maxLines = 1)
                    Box(Modifier.width(3.dp).height(30.dp).background(parseColour(ev.colour), RoundedCornerShape(2.dp)))
                    Column(Modifier.padding(start = 10.dp).weight(1f)) {
                        Text(ev.title, style = T.label.copy(fontSize = 14.sp), maxLines = 1, overflow = TextOverflow.Ellipsis)
                        if (ev.location.isNotBlank()) Text(ev.location, style = T.tiny, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
        if (card.headsUp.isNotEmpty()) {
            Column(Modifier.padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                card.headsUp.forEach { h ->
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        NIcon(when (h.icon) { "warning" -> "alert"; "rain" -> "cloud"; "calendar" -> "calendar"; else -> "clock" },
                            size = 18.dp, color = headsColor(h.icon), stroke = 2.2f)
                        Text(h.text, style = T.body, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
    }
    }
    muting?.let { (e, problem) -> MuteSheet(e, problem) { muting = null } }
}

@Composable
private fun TodoRow(r: TodayView.Row, busy: Boolean, onDone: () -> Unit, onSnooze: () -> Unit, onUndo: () -> Unit, onOpen: () -> Unit) {
    val done = r.tone == TodayView.Tone.DONE
    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.width(4.dp).height(34.dp).clip(RoundedCornerShape(2.dp)).background(toneColor(r.tone)))
        Column(Modifier.weight(1f).padding(start = 10.dp, end = 8.dp)) {
            Text(r.title, style = T.label.copy(fontSize = 14.sp, color = if (done) N.Muted else N.Text,
                textDecoration = if (done) TextDecoration.LineThrough else null), maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (r.status.isNotBlank()) Text(r.status, style = T.tiny.copy(color = if (r.tone == TodayView.Tone.LATER) N.Muted else toneColor(r.tone)), maxLines = 1)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
            r.buttons.forEach { b ->
                when (b) {
                    TodayView.Button.DONE -> GlassButton("Done", height = 36.dp, busy = busy, background = N.Text, foreground = N.Night, border = N.Text, onClick = onDone)
                    TodayView.Button.DISMISS -> GlassButton("Done", height = 36.dp, busy = busy, onClick = onDone)
                    TodayView.Button.UNDO -> GlassButton("Undo", height = 36.dp, busy = busy, onClick = onUndo)
                    TodayView.Button.OPEN -> GlassButton("Open", height = 36.dp, onClick = onOpen)
                    TodayView.Button.SNOOZE -> Box(
                        Modifier.minimumInteractiveComponentSize().size(36.dp).clip(RoundedCornerShape(12.dp)).border(1.dp, N.BorderHi, RoundedCornerShape(12.dp))
                            .clickable(enabled = !busy, role = Role.Button, onClick = onSnooze)
                            .semantics { contentDescription = "Remind me tomorrow: ${r.title}" },
                        contentAlignment = Alignment.Center,
                    ) { NIcon("clock", size = 18.dp, color = N.Muted) }
                }
            }
        }
    }
}
