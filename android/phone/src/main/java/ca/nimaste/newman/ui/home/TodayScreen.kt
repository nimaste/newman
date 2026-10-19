package ca.nimaste.newman.ui.home

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.hub.HubState
import ca.nimaste.newman.core.model.string
import ca.nimaste.newman.core.today.TodayView
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.NTextField
import ca.nimaste.newman.ui.common.spoken
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.theme.H
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/** An event coming up on a calendar (a calendar device's attrs.upcoming). */
private data class Upcoming(val day: LocalDate, val title: String)

private fun startDay(s: String?): LocalDate? {
    if (s.isNullOrBlank()) return null
    return runCatching { OffsetDateTime.parse(s).atZoneSameInstant(ZoneId.systemDefault()).toLocalDate() }.getOrNull()
        ?: runCatching { LocalDateTime.parse(s).toLocalDate() }.getOrNull()
        ?: runCatching { LocalDate.parse(s.take(10)) }.getOrNull()
}

/** The next events of the coming days, from every calendar the hub has (devices whose kind's noun is "calendar"). */
private fun upcoming(state: HubState, today: LocalDate, max: Int = 5): List<Upcoming> =
    state.named.values.filter { state.kind(it)?.noun == "calendar" }.flatMap { e ->
        (e.attrs["upcoming"] as? kotlinx.serialization.json.JsonArray)?.mapNotNull { el ->
            val o = el as? JsonObject ?: return@mapNotNull null
            val d = startDay(o.string("start")) ?: return@mapNotNull null
            if (d.isAfter(today) && d.isBefore(today.plusDays(8))) Upcoming(d, o.string("title")?.ifBlank { null } ?: "Busy") else null
        } ?: emptyList()
    }.sortedBy { it.day }.take(max)

/**
 * Today (Today board): CALENDAR - today's events, or "Nothing on the Family calendar today"; TO DO -
 * tap one to tick it done (Undo while it's just done), "+ Add" for admins (an upkeep item first due
 * today); COMING UP - the next days' events and the upkeep coming due, with their status.
 */
@Composable
fun TodayScreen(onBack: () -> Unit, onSheet: (String) -> Unit) {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    val state by app.state.collectAsState()
    val b by app.today.briefing.collectAsState()
    val done by app.today.done.collectAsState()
    var now by remember { mutableStateOf(System.currentTimeMillis() / 1000.0) }
    var busy by remember { mutableStateOf<String?>(null) }
    var adding by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        while (true) {
            app.today.refresh()
            now = System.currentTimeMillis() / 1000.0
            delay(60_000)
        }
    }
    val today = LocalDate.now()
    val admin = state.auth?.user?.role == "admin"
    fun act(id: String, block: suspend () -> String?) {
        busy = id
        scope.launch {
            block()?.let { app.toast(it, ok = false) }
            busy = null
            now = System.currentTimeMillis() / 1000.0
        }
    }
    HScreen("Today", back = "Home", onBack = onBack, line = today.format(DateTimeFormatter.ofPattern("EEEE, MMMM d", Locale.getDefault()))) {
        val brief = b
        item(key = "calendar") {
            HCard(padding = PaddingValues(horizontal = 16.dp, vertical = 14.dp)) {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Caps("Calendar")
                    val events = brief?.events ?: emptyList()
                    if (events.isEmpty()) {
                        val cals = state.named.values.filter { state.kind(it)?.noun == "calendar" }
                        Text(when (cals.size) {
                            0 -> "Nothing on the calendar today"
                            1 -> "Nothing on the ${cals[0].name} calendar today"
                            else -> "Nothing on your calendars today"
                        }, style = HT.row.copy(color = H.Soft))
                    }
                    events.forEach { ev ->
                        Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Text(if (ev.now) "Now" else if (ev.allDay) "All day" else ev.time, style = HT.line.copy(color = if (ev.now) H.GoodText else H.Info,
                                fontWeight = FontWeight.ExtraBold), modifier = Modifier.width(64.dp), maxLines = 1)
                            Column(Modifier.weight(1f)) {
                                Text(ev.title, style = HT.row.copy(fontSize = 14.sp), maxLines = 2, overflow = TextOverflow.Ellipsis)
                                if (ev.location.isNotBlank()) Text(ev.location, style = HT.small, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            }
                        }
                    }
                }
            }
        }
        item(key = "todo") {
            val card = brief?.let { TodayView.card(it, done, now, rows = 99) }
            val todoIds = brief?.todo?.map { it.id }?.toSet() ?: emptySet()
            val rows = card?.rows?.filter { it.id in todoIds || it.tone == TodayView.Tone.DONE } ?: emptyList()
            HCard(padding = PaddingValues(horizontal = 16.dp, vertical = 14.dp)) {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Caps("To do", Modifier.weight(1f))
                        if (admin) RaisedButton("+ Add", height = 32.dp) { adding = true }
                    }
                    if (rows.isEmpty()) Text(if (brief == null) "Loading…" else "Nothing to do", style = HT.row.copy(color = H.Soft))
                    rows.forEach { r ->
                        val isDone = r.tone == TodayView.Tone.DONE
                        val open = TodayView.Button.OPEN in r.buttons
                        Row(
                            Modifier.fillMaxWidth().clickable(enabled = busy != r.id, role = Role.Checkbox, onClickLabel = when {
                                isDone -> "undo"; open -> "open"; else -> "mark done"
                            }) {
                                when {
                                    isDone -> if (TodayView.Button.UNDO in r.buttons) act(r.id) { app.today.undo(r.item) }
                                    open -> r.entity?.let(onSheet)
                                    else -> act(r.id) { app.today.done(r.item) }
                                }
                            }.spoken(listOf(r.title, r.status, if (isDone) "done" else "").filter { it.isNotBlank() }.joinToString(", ")).padding(vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp),
                        ) {
                            Box(Modifier.size(24.dp).background(if (isDone) H.Good else H.Inset, CircleShape).border(1.5.dp, if (isDone) H.Good else H.RaisedLine, CircleShape),
                                contentAlignment = Alignment.Center) { if (isDone) NIcon("check", size = 14.dp, color = H.Page, stroke = 3f) }
                            Column(Modifier.weight(1f)) {
                                Text(r.title, style = HT.row.copy(textDecoration = if (isDone) TextDecoration.LineThrough else null,
                                    color = if (isDone) H.Sub else H.Text), maxLines = 2, overflow = TextOverflow.Ellipsis)
                                if (r.status.isNotBlank()) Text(r.status, style = HT.small.copy(color = toneColour(r.tone)))
                            }
                            if (isDone && TodayView.Button.UNDO in r.buttons) Text("Undo", style = HT.small.copy(color = H.Info))
                        }
                    }
                }
            }
        }
        item(key = "coming") {
            val next = upcoming(state, today)
            val soon = brief?.soon ?: emptyList()
            HCard(padding = PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 6.dp)) {
                Caps("Coming up", Modifier.padding(top = 10.dp, bottom = 2.dp))
                if (next.isEmpty() && soon.isEmpty()) Text("Nothing coming up", style = HT.row.copy(color = H.Soft), modifier = Modifier.padding(vertical = 10.dp))
                val total = next.size + soon.size
                var i = 0
                next.forEach { u ->
                    i++
                    HRow(last = i == total) {
                        Text(if (u.day == today.plusDays(1)) "Tomorrow" else u.day.format(DateTimeFormatter.ofPattern("EEE d", Locale.getDefault())),
                            style = HT.line.copy(color = H.Info, fontWeight = FontWeight.ExtraBold), modifier = Modifier.width(72.dp), maxLines = 1)
                        Text(u.title, style = HT.row.copy(fontSize = 14.sp), modifier = Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
                    }
                }
                soon.forEach { s ->
                    i++
                    HRow(last = i == total, onClick = s.entity?.let { { onSheet(it) } }) {
                        Text("Upkeep", style = HT.line.copy(color = H.Info, fontWeight = FontWeight.ExtraBold), modifier = Modifier.width(72.dp), maxLines = 1)
                        Column(Modifier.weight(1f)) {
                            Text(s.title, style = HT.row.copy(fontSize = 14.sp), maxLines = 2, overflow = TextOverflow.Ellipsis)
                            if (s.status.isNotBlank()) Text(s.status, style = HT.small.copy(color = toneColour(TodayView.tone(s.level))))
                        }
                    }
                }
            }
        }
    }
    if (adding) AddTodo(onDone = { adding = false })
}

private fun toneColour(t: TodayView.Tone) = when (t) {
    TodayView.Tone.URGENT -> H.HotText
    TodayView.Tone.DUE -> H.WarnText
    TodayView.Tone.LATER -> H.GoodText
    TodayView.Tone.DONE -> H.Sub
}

/** "+ Add": a to-do's name and how often it comes back (the hub's upkeep items repeat); first due today. */
@Composable
private fun AddTodo(onDone: () -> Unit) {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    var name by remember { mutableStateOf("") }
    var every by remember { mutableStateOf("1") }
    var unit by remember { mutableStateOf("weeks") }
    var saving by remember { mutableStateOf(false) }
    val n = every.toIntOrNull()?.takeIf { it > 0 }
    AlertDialog(
        onDismissRequest = onDone,
        containerColor = H.Card,
        title = { Text("Add a to-do") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                NTextField(name, { name = it }, label = "What needs doing")
                NTextField(every, { every = it.filter(Char::isDigit).take(3) }, label = "Comes back every",
                    keyboard = androidx.compose.ui.text.input.KeyboardType.Number)
                Segmented(listOf("days" to "Days", "weeks" to "Weeks", "months" to "Months"), unit, height = 36.dp) { unit = it }
            }
        },
        confirmButton = {
            TextButton(enabled = !saving && name.isNotBlank() && n != null, onClick = {
                saving = true
                scope.launch {
                    val ok = app.addTodo(name.trim(), n ?: 1, unit)
                    saving = false
                    if (ok) onDone()
                }
            }) { Text("Add") }
        },
        dismissButton = { TextButton(onClick = onDone) { Text("Cancel") } },
    )
}
