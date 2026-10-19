package ca.nimaste.newman.ui.home

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.setProgress
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.model.ClimateRoomPref
import ca.nimaste.newman.core.model.ClimateRoomsBody
import ca.nimaste.newman.core.model.HomeData
import ca.nimaste.newman.core.model.HomeLogic
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.jsonOf
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.NTextField
import ca.nimaste.newman.ui.common.spoken
import ca.nimaste.newman.ui.rememberRunner
import ca.nimaste.newman.ui.theme.H
import ca.nimaste.newman.ui.tiles.DialModel
import ca.nimaste.newman.ui.tiles.DialRing
import ca.nimaste.newman.ui.tiles.MODE_COLOR
import ca.nimaste.newman.ui.tiles.rememberDialTarget
import kotlinx.coroutines.launch
import java.util.Locale

private fun deg(v: Double?): String = v?.let { String.format(Locale.getDefault(), "%.1f°", it) } ?: "—"

/** Climate: the thermostat's dial, −/+, its modes, and the rooms' temperatures on the house's range. */
@Composable
fun ClimateScreen(home: HomeData?, onBack: () -> Unit, onEditRooms: () -> Unit, onSheet: (String) -> Unit) {
    val app = LocalApp.current
    val state by app.state.collectAsState()
    val climate = home?.climate
    val e = climate?.thermostat?.let { state.named[it] }
    val k = state.kind(e)
    val cmd = k?.commands?.firstOrNull { it.valueType == "number" }
    val m = if (e != null && cmd != null) DialModel(e, k, cmd, k?.commands?.firstOrNull { it.valueType == "state" }) else null
    HScreen("Climate", back = "Home", onBack = onBack,
        trailing = if (e != null) { { Text(listOfNotNull(e.room, e.integrationName.ifBlank { null }).joinToString(" · "), style = HT.line) } } else null) {
        if (m != null) item(key = "dial") { DialCard(m, onSheet) }
        else if (home != null) item(key = "nodial") {
            HCard(padding = PaddingValues(16.dp)) { Text("No thermostat yet.", style = HT.line) }
        }
        val rooms = climate?.rooms ?: emptyList()
        item(key = "rooms") {
            HCard(padding = PaddingValues(horizontal = 16.dp, vertical = 6.dp)) {
                Caps("Rooms", Modifier.padding(top = 4.dp), action = "Edit", onAction = onEditRooms)
                if (rooms.isEmpty()) Text("No rooms on Climate yet.", style = HT.line, modifier = Modifier.padding(vertical = 12.dp))
                rooms.forEachIndexed { i, r ->
                    val band = HomeLogic.bandFor(r.value, climate!!.bands)
                    val colour = HomeLogic.argb(band?.color)?.let { Color(it) } ?: H.LightText
                    HRow(last = i == rooms.lastIndex, modifier = Modifier.spoken(listOfNotNull(r.name, deg(r.value), band?.name).joinToString(", "))) {
                        Text(r.name, style = HT.name, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Box(Modifier.width(90.dp).height(6.dp).clip(RoundedCornerShape(3.dp)).background(H.Track)) {
                            Box(Modifier.fillMaxHeight().fillMaxWidth(HomeLogic.barFraction(r.value, climate.range)).clip(RoundedCornerShape(3.dp)).background(colour))
                        }
                        Text(deg(r.value), style = HT.name, textAlign = TextAlign.End, modifier = Modifier.width(56.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun DialCard(m: DialModel, onSheet: (String) -> Unit) {
    val run = rememberRunner()
    val (target, drag, bump) = rememberDialTarget(m)
    HCard(padding = PaddingValues(start = 16.dp, end = 16.dp, top = 18.dp, bottom = 16.dp), radius = 24.dp) {
        Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Box(Modifier.semantics(mergeDescendants = true) {
                contentDescription = "${m.e.name} ${m.cmd.label.lowercase()}"
                stateDescription = listOfNotNull(m.text(target), m.nowLine(false).ifEmpty { null }).joinToString(", ")
                if (!m.off) {
                    progressBarRangeInfo = ProgressBarRangeInfo((target ?: m.min).toFloat().coerceIn(m.min.toFloat(), m.max.toFloat()),
                        m.min.toFloat()..m.max.toFloat(), steps = (((m.max - m.min) / m.step).toInt() - 1).coerceIn(0, 200))
                    setProgress { v -> drag(m.clamp(v.toDouble()), true); true }
                }
            }.clickable(role = Role.Button, onClickLabel = "show all controls") { onSheet(m.e.id) }) {
                DialRing(m, target, 230.dp, 20.dp, onDrag = drag) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(m.word.uppercase(), style = HT.caps.copy(fontSize = 13.sp, color = m.color))
                        Text(m.text(target), style = HT.name.copy(fontSize = 52.sp, letterSpacing = (-2).sp))
                        val now = listOfNotNull(m.nowProp?.let { "Now " + HubModel.formatProp(it, it.value).replace(" °C", "°").replace(" °F", "°") }, m.activityText)
                        if (now.isNotEmpty()) Text(now.joinToString(" · "), style = HT.row.copy(fontSize = 14.sp, color = H.Sub))
                    }
                }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                listOf(-1 to "−", 1 to "+").forEach { (dir, sign) ->
                    val next = (target ?: m.current)?.let { m.clamp(it + dir * m.step) }
                    Box(
                        Modifier.weight(1f).heightIn(min = 52.dp).clip(RoundedCornerShape(16.dp)).background(Color(0xFF1A2350))
                            .border(1.dp, H.RaisedLine, RoundedCornerShape(16.dp))
                            .clickable(enabled = !m.off, role = Role.Button) { bump(dir * m.step) }
                            .clearAndSetSemantics {
                                contentDescription = (if (dir < 0) "Lower" else "Raise") + (next?.let { " to ${m.text(it)}" } ?: "")
                                role = Role.Button
                            },
                        contentAlignment = Alignment.Center,
                    ) { Text(sign, style = HT.name.copy(fontSize = 26.sp, fontWeight = FontWeight.SemiBold, color = if (m.off) H.Label else H.Text)) }
                }
            }
            if (m.modes.isNotEmpty() && m.modeCmd != null) {
                Segmented(m.modes.map { it.id to it.label }, m.mode, enabled = m.e.available, height = 38.dp,
                    lit = { id -> (MODE_COLOR[id] ?: H.Soft).let { it.copy(alpha = 0.22f) to it } }) { id -> run.run(m.e, m.modeCmd, jsonOf(id)) }
            }
            val humidity = m.pctProp?.let { "${it.label} ${HubModel.formatProp(it, it.value)}" }
            val range = m.otherSetpoints.takeIf { it.size >= 2 }?.let { "Auto range ${deg(it.first())} – ${deg(it.last())}" }
            if (humidity != null || range != null) {
                Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(humidity ?: "", style = HT.line)
                    Text(range ?: "", style = HT.line)
                }
            }
        }
    }
}

private data class RoomRow(val id: String, val name: String, val on: Boolean)

/** Climate › Edit: which temperature sensors show, in what order, under what name (the household's). */
@Composable
fun ClimateRoomsScreen(home: HomeData?, onDone: () -> Unit) {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    val state by app.state.collectAsState()
    val sensors = remember(state.entities, state.meta) { HomeLogic.climateSensors(state.named.values, state.meta) }
    val chosen = home?.climate?.rooms ?: emptyList()
    // Shown rooms first (in their order, with their names), then every other sensor, hidden.
    var rows by remember(home == null) {
        mutableStateOf<List<RoomRow>>(
            chosen.map { RoomRow(it.entity, it.name, true) } +
                sensors.filter { s -> chosen.none { it.entity == s.id } }.map { RoomRow(it.id, it.room ?: it.name, false) },
        )
    }
    var renaming by remember { mutableStateOf<RoomRow?>(null) }
    var saving by remember { mutableStateOf(false) }
    HScreen("Rooms on Climate", big = false, trailing = {
        AccentButton("Done", enabled = !saving) {
            saving = true
            scope.launch {
                val ok = app.saveClimateRooms(ClimateRoomsBody(rows.filter { it.on }.map { ClimateRoomPref(it.id, it.name.trim().ifEmpty { state.named[it.id]?.name ?: it.id }) }))
                saving = false
                if (ok) onDone()
            }
        }
    }) {
        item(key = "help") {
            Text("Every temperature sensor, whatever makes it. Tick the ones to show, drag to order, tap a name to rename it here.",
                style = HT.line.copy(fontWeight = FontWeight.SemiBold), modifier = Modifier.padding(horizontal = 4.dp))
        }
        item(key = "list") {
            HCard(padding = PaddingValues(horizontal = 16.dp, vertical = 2.dp)) {
                if (rows.isEmpty()) Text("No temperature sensors yet.", style = HT.line, modifier = Modifier.padding(vertical = 12.dp))
                ReorderColumn(rows, key = { it.id }, onMove = { a, b -> rows = rows.moved(a, b) }) { r, i, handle ->
                    val e = state.named[r.id]
                    val k = state.kind(e)
                    val sub = listOfNotNull(e?.name?.takeIf { it != r.name }, e?.integrationName?.ifBlank { null },
                        e?.let { HubModel.readingText(it, k, withLabel = false).ifBlank { null } }, if (!r.on) "hidden" else null).joinToString(" · ")
                    HRow(last = i == rows.lastIndex) {
                        Checkbox(r.on, onCheckedChange = { on -> rows = rows.map { if (it.id == r.id) it.copy(on = on) else it } },
                            colors = CheckboxDefaults.colors(checkedColor = H.Accent, uncheckedColor = H.Label, checkmarkColor = H.Page),
                            modifier = Modifier.semantics { contentDescription = "Show ${r.name}" })
                        Column(Modifier.weight(1f).clickable(role = Role.Button, onClickLabel = "rename") { renaming = r }) {
                            Text(r.name, style = if (r.on) HT.name else HT.row.copy(color = H.Soft), maxLines = 1, overflow = TextOverflow.Ellipsis)
                            if (sub.isNotBlank()) Text(sub, style = HT.small.copy(color = H.Label, fontWeight = FontWeight.SemiBold), maxLines = 1,
                                overflow = TextOverflow.Ellipsis)
                        }
                        DragHandle(handle, "Drag to reorder ${r.name}")
                    }
                }
            }
        }
    }
    renaming?.let { r ->
        var text by remember(r.id) { mutableStateOf(r.name) }
        AlertDialog(
            onDismissRequest = { renaming = null },
            title = { Text("Name on Climate") },
            text = { NTextField(text, { text = it }, label = "Name") },
            confirmButton = {
                TextButton(onClick = {
                    rows = rows.map { if (it.id == r.id) it.copy(name = text.trim().ifEmpty { it.name }) else it }
                    renaming = null
                }) { Text("OK") }
            },
            dismissButton = { TextButton(onClick = { renaming = null }) { Text("Cancel") } },
            containerColor = H.Card,
        )
    }
}
