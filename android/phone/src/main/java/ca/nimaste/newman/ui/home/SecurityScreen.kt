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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import ca.nimaste.newman.core.live.LiveMessage
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.model.HomeData
import ca.nimaste.newman.core.model.HomeLogic
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.jsonOf
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.spoken
import ca.nimaste.newman.ui.rememberRunner
import ca.nimaste.newman.ui.theme.H
import ca.nimaste.newman.ui.today.TodayCard
import java.text.DateFormat
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Date
import java.util.Locale

/**
 * Security: the alarm's modes, locks and doors you control (each with its next action), the sensors
 * by kind (doors, water…), and today's events from them.
 */
@Composable
fun SecurityScreen(home: HomeData?, onBack: () -> Unit, onSheet: (String) -> Unit, onAlerts: () -> Unit) {
    val app = LocalApp.current
    val state by app.state.collectAsState()
    val busy by app.busy.collectAsState()
    val run = rememberRunner()
    val parts = remember(state.entities, state.meta) { HomeLogic.securityParts(state.named.values, state.meta) }
    val card = home?.cards?.firstOrNull { it.id == "security" }
    val start = LocalDate.now().atStartOfDay(ZoneId.systemDefault()).toEpochSecond().toDouble()
    // Today's events: GET /api/alerts?category=security (older hubs: the activity of these devices), again when activity arrives.
    var events by remember { mutableStateOf<List<Pair<Double, String>>?>(null) }
    var reload by remember { mutableIntStateOf(0) }
    LaunchedEffect(reload) {
        events = app.securityEvents(start)?.map { (it.time ?: 0.0) to it.title.ifBlank { it.body }.ifBlank { it.what } }
    }
    LaunchedEffect(Unit) { app.liveMessages.collect { m -> if (m is LiveMessage.Activity || m is LiveMessage.Reconnected) reload++ } }
    HScreen("Security", back = "Home", onBack = onBack,
        line = card?.let { listOf(it.value, it.detail).filter { s -> s.isNotBlank() }.joinToString(" · ") },
        lineColor = when (card?.tone) { "danger" -> H.HotText; "good" -> H.GoodText; else -> H.Sub }) {
        parts.alarms.forEach { e ->
            item(key = "alarm:${e.id}") {
                val k = state.kind(e)
                val cmd = k?.commands?.firstOrNull { it.valueType == "state" }
                HCard(padding = PaddingValues(horizontal = 16.dp, vertical = 14.dp)) {
                    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Row(Modifier.fillMaxWidth()) {
                            Text(e.name.uppercase(), style = HT.caps, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(e.integrationName, style = HT.small.copy(color = H.Label))
                        }
                        Segmented(HubModel.stateChoices(k).map { it.id to it.label }, e.state, enabled = e.available && e.id !in busy && cmd != null,
                            height = 42.dp) { id -> if (cmd != null) run.run(e, cmd, jsonOf(id)) }
                    }
                }
            }
        }
        if (parts.controls.isNotEmpty()) item(key = "controls") {
            HCard {
                Caps("Locks & garage", Modifier.padding(top = 10.dp, bottom = 2.dp))
                parts.controls.forEachIndexed { i, e ->
                    val k = state.kind(e)
                    val action = HubModel.nextAction(e, k)
                    val open = HubModel.notSecure(e, k)
                    val battery = HubModel.vitals(e).battery
                    HRow(last = i == parts.controls.lastIndex) {
                        Column(Modifier.weight(1f).clickable(role = Role.Button, onClickLabel = "show all controls") { onSheet(e.id) }) {
                            Text(e.name, style = HT.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(listOfNotNull(HubModel.stateLabel(e, k), battery?.let { "battery $it%" }).joinToString(" · "),
                                style = HT.small.copy(color = if (open) H.HotText else H.GoodText))
                        }
                        if (action != null) RaisedButton(action.label, enabled = e.available && e.id !in busy,
                            onClickLabel = "${action.label} ${e.name}") { run.run(e, action) }
                    }
                }
            }
        }
        if (parts.sensors.isNotEmpty()) item(key = "sensors") {
            HCard(padding = PaddingValues(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 14.dp)) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    parts.sensors.forEachIndexed { gi, (kind, list) ->
                        Caps(kind.plural, Modifier.padding(top = if (gi == 0) 0.dp else 6.dp))
                        list.chunked(3).forEach { rowList ->
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                rowList.forEach { e ->
                                    val k = state.kind(e)
                                    val open = HubModel.notSecure(e, k)
                                    val shape = RoundedCornerShape(14.dp)
                                    Column(
                                        Modifier.weight(1f).clip(shape).background(if (open) H.Hot else H.Raised).border(1.dp, if (open) H.HotLine else H.TileLine, shape)
                                            .clickable(role = Role.Button) { onSheet(e.id) }.spoken("${e.name}, ${HubModel.entityStateText(e, k)}").padding(10.dp),
                                    ) {
                                        Text(e.name, style = HT.name.copy(fontSize = 13.sp), maxLines = 1, overflow = TextOverflow.Ellipsis)
                                        Text(HubModel.entityStateText(e, k), style = HT.small.copy(color = if (open) H.HotText else H.GoodText), maxLines = 1,
                                            overflow = TextOverflow.Ellipsis)
                                    }
                                }
                                repeat(3 - rowList.size) { Box(Modifier.weight(1f)) }
                            }
                        }
                    }
                }
            }
        }
        item(key = "today") {
            val ids = parts.all.map { it.id }.toSet()
            val events = events ?: state.activity.filter { it.ts >= start && (it.entityId in ids || it.kind == "alert") }.sortedByDescending { it.ts }
                .map { it.ts to it.title.ifBlank { it.body } }
            HCard(padding = PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 6.dp)) {
                Caps("Today", Modifier.padding(top = 6.dp), action = "All alerts", onAction = onAlerts)
                if (events.isEmpty()) Text("Nothing yet today.", style = HT.line, modifier = Modifier.padding(vertical = 10.dp))
                val fmt = DateFormat.getTimeInstance(DateFormat.SHORT)
                events.take(12).forEachIndexed { i, (ts, text) ->
                    Column {
                        Row(Modifier.fillMaxWidth().padding(vertical = 9.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Text(fmt.format(Date((ts * 1000).toLong())), style = HT.line.copy(color = H.Info, fontWeight = FontWeight.ExtraBold),
                                modifier = Modifier.width(64.dp), maxLines = 1)
                            Text(text, style = HT.row.copy(fontSize = 14.sp), maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                        if (i < minOf(events.size, 12) - 1) HDivider()
                    }
                }
            }
        }
    }
}
