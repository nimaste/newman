package ca.nimaste.newman.ui.home

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.hub.HubState
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HomeLogic
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.asNumberOrNull
import ca.nimaste.newman.core.model.jsonOf
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.heading
import ca.nimaste.newman.ui.rememberRunner
import ca.nimaste.newman.ui.theme.H
import kotlin.math.roundToInt

/**
 * Lights by room (the device's room; none: "Other"). A group is one row ("Group · 3 lights", its
 * brightness) with its lights behind "Show the 3 lights"; a room's switch turns every row in it on or
 * off; All off turns off everything that's on.
 */
@Composable
fun LightsScreen(onBack: () -> Unit, onSheet: (String) -> Unit) {
    val app = LocalApp.current
    val state by app.state.collectAsState()
    val run = rememberRunner()
    val rooms = remember(state.entities, state.meta) { HomeLogic.lightRooms(state.named.values, state.meta) }
    val open = remember { mutableStateMapOf<String, Boolean>() }
    val onRows = rooms.flatMap { it.rows }.filter { HubModel.isActive(it.entity, state.kind(it.entity)) }
    fun set(e: Entity, on: Boolean) {
        val duo = HomeLogic.onOff(state.kind(e)) ?: return
        if (HubModel.isActive(e, state.kind(e)) != on) run.run(e, if (on) duo.on else duo.off)
    }
    HScreen("Lights", back = "Home", onBack = onBack,
        line = if (onRows.isEmpty()) "All off" else onRows.joinToString(", ") { it.entity.name } + " on",
        lineColor = if (onRows.isEmpty()) H.Sub else H.LightText,
        trailing = { RaisedButton("All off", height = 40.dp, enabled = onRows.isNotEmpty()) { onRows.forEach { set(it.entity, false) } } }) {
        if (rooms.isEmpty()) item(key = "none") { Text("No lights yet.", style = HT.line, modifier = Modifier.padding(4.dp)) }
        rooms.forEach { room ->
            item(key = "room:${room.name}") {
                val anyOn = room.rows.any { HubModel.isActive(it.entity, state.kind(it.entity)) }
                HCard(padding = PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 6.dp)) {
                    Row(Modifier.fillMaxWidth().padding(top = 8.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Column(Modifier.weight(1f)) {
                            Text(room.name, style = HT.name.copy(fontSize = 17.sp), modifier = Modifier.heading())
                            Text(HomeLogic.roomLine(room, state.meta), style = HT.small.copy(color = if (anyOn) H.LightText else H.Sub))
                        }
                        HSwitch(anyOn, label = if (anyOn) "${room.name} all off" else "${room.name} all on", on = H.Light, knobOn = H.LightBg) { want ->
                            room.rows.forEach { set(it.entity, want) }
                        }
                    }
                    room.rows.forEach { r ->
                        HDivider()
                        LightRowView(r.entity, state,
                            sub = if (r.members.isEmpty()) null else "Group · ${r.members.size} ${if (r.members.size == 1) "light" else "lights"}",
                            onSheet = onSheet, onSet = { set(r.entity, it) })
                        if (r.members.isNotEmpty()) {
                            val shown = open[r.entity.id] == true
                            Text(if (shown) "Hide the ${r.members.size} lights" else "Show the ${r.members.size} lights ›",
                                style = HT.small.copy(color = H.Label),
                                modifier = Modifier.minimumInteractiveComponentSize().clickable(role = Role.Button) { open[r.entity.id] = !shown }
                                    .padding(vertical = 4.dp))
                            if (shown) r.members.forEach { mem ->
                                Column(Modifier.padding(start = 14.dp)) {
                                    HDivider()
                                    LightRowView(mem, state, sub = null, onSheet = onSheet, onSet = { set(mem, it) })
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

/** One light: name (and what it is), brightness or Off, its switch; a dimmable one that's on gets its slider. */
@Composable
private fun LightRowView(e: Entity, state: HubState, sub: String?, onSheet: (String) -> Unit, onSet: (Boolean) -> Unit) {
    val k = state.kind(e)
    val run = rememberRunner()
    val on = HubModel.isActive(e, k)
    val pct = HubModel.percentCommand(e, k)
    val level = pct?.let { e.attrs[it.attr].asNumberOrNull }
    Column(Modifier.padding(vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Column(Modifier.weight(1f).heightIn(min = 40.dp).clickable(role = Role.Button, onClickLabel = "show all controls") { onSheet(e.id) },
                verticalArrangement = Arrangement.Center) {
                Text(e.name, style = HT.row, maxLines = 2, overflow = TextOverflow.Ellipsis)
                if (sub != null) Text(sub, style = HT.small.copy(color = H.Label, fontWeight = FontWeight.SemiBold))
            }
            Text(if (!e.available) "Offline" else if (on) level?.let { "${it.roundToInt()}%" } ?: "On" else "Off",
                style = HT.line.copy(color = if (on) H.LightText else H.Sub))
            LightSwitch(e, state, onSet)
        }
        if (on && pct != null && level != null) {
            var v by remember(e.id, level) { mutableFloatStateOf(level.toFloat()) }
            val lo = (pct.min ?: 1.0).toFloat()
            val hi = (pct.max ?: 100.0).toFloat()
            Slider(v, onValueChange = { v = it }, valueRange = lo..hi,
                onValueChangeFinished = { run.run(e, pct, jsonOf(v.roundToInt())) },
                colors = SliderDefaults.colors(thumbColor = H.Light, activeTrackColor = H.Light, inactiveTrackColor = H.LightTrack),
                modifier = Modifier.fillMaxWidth().semantics { contentDescription = "${e.name} brightness" })
        }
    }
}

@Composable
private fun LightSwitch(e: Entity, state: HubState, onSet: (Boolean) -> Unit) {
    val app = LocalApp.current
    val busy by app.busy.collectAsState()
    val on = HubModel.isActive(e, state.kind(e))
    HSwitch(on, label = if (on) "Turn ${e.name} off" else "Turn ${e.name} on", enabled = e.available && e.id !in busy &&
        HomeLogic.onOff(state.kind(e)) != null, on = H.Light, knobOn = H.LightBg, onChange = onSet)
}
