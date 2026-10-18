package ca.nimaste.newman.ui.device

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.model.Command
import ca.nimaste.newman.core.model.ControlType
import ca.nimaste.newman.core.model.Controls
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HealthItem
import ca.nimaste.newman.core.model.Muting
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.Kind
import ca.nimaste.newman.core.model.RemoteControls
import ca.nimaste.newman.core.model.asNumberOrNull
import ca.nimaste.newman.core.model.asStringOrNull
import ca.nimaste.newman.core.model.jsonOf
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.GlassButton
import ca.nimaste.newman.ui.common.CardHeader
import ca.nimaste.newman.ui.common.MuteSheet
import ca.nimaste.newman.ui.common.GlassCard
import ca.nimaste.newman.ui.common.NSwitch
import ca.nimaste.newman.ui.common.NTextField
import ca.nimaste.newman.ui.common.heading
import ca.nimaste.newman.core.model.Spoken
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import ca.nimaste.newman.widgets.LauncherShortcuts
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.rememberRunner
import ca.nimaste.newman.ui.tiles.FullDial
import ca.nimaste.newman.ui.tiles.DialModel
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import java.util.Locale
import kotlin.math.roundToInt

/**
 * A device's sheet: its state, every command with the control its valueType asks for, and every
 * reading it reports. Built only from /api/meta and the entity - no kind is special here.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DeviceSheet(entityId: String, onDismiss: () -> Unit, onCamera: ((String) -> Unit)? = null) {
    val app = LocalApp.current
    val state by app.state.collectAsState()
    val e = state.named[entityId]
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var openLight by remember { mutableStateOf<String?>(null) } // a light a remote's Controls card opened
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet, containerColor = Color(0xFF141A33), contentColor = N.Text) {
        if (e == null) {
            Text("This device is gone.", style = T.body, modifier = Modifier.padding(24.dp))
            return@ModalBottomSheet
        }
        val k = state.kind(e)
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp).padding(bottom = 24.dp).navigationBarsPadding(),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Header(e, k)
            if (e.still != null && onCamera != null) {
                GlassButton("Watch ${e.name}", Modifier.fillMaxWidth(), icon = "camera", height = 48.dp) { onCamera(e.id) }
            }
            // A dial-able device (a thermostat) gets the round dial, as on the wall.
            val dialCmd = k?.commands?.firstOrNull { it.valueType == "number" }
            if (dialCmd != null && dialCmd.attr.isNotEmpty() && e.attrs[dialCmd.attr] != null) {
                val modeCmd = k.commands.firstOrNull { it.valueType == "state" }
                FullDial(DialModel(e, k, dialCmd, modeCmd), showHeader = false)
                DeviceControls(e, k, skip = setOfNotNull(dialCmd.id, modeCmd?.id))
            } else {
                DeviceControls(e, k, skip = emptySet())
            }
            e.controls?.let { RemoteControlsCards(it) { id -> openLight = id } }
            Readings(e)
            AddToHomeScreen(e, k)
        }
    }
    openLight?.let { DeviceSheet(it, onDismiss = { openLight = null }, onCamera = onCamera) }
}

/**
 * A remote's Controls (entity.controls, set on the dashboard): the lights it works, each with its level
 * (tap = that light's sheet), All on / All off, then what each press does. Read-only here.
 */
@Composable
private fun RemoteControlsCards(c: RemoteControls, onOpen: (String) -> Unit) {
    val app = LocalApp.current
    val state by app.state.collectAsState()
    val busy by app.busy.collectAsState()
    val run = rememberRunner()
    val lights = c.lights.mapNotNull { state.named[it] }
    fun all(commandId: String) = lights.forEach { l ->
        state.kind(l)?.commands?.firstOrNull { it.id == commandId }?.let { cmd -> if (l.available && l.id !in busy) run.run(l, cmd) }
    }
    GlassCard(Modifier.fillMaxWidth(), padding = androidx.compose.foundation.layout.PaddingValues(horizontal = 14.dp, vertical = 12.dp)) {
        CardHeader(c.header)
        lights.forEachIndexed { i, l ->
            val status = RemoteControls.lightStatus(l, state.kind(l))
            val on = status != "Off" && status != "Offline"
            Row(
                Modifier.fillMaxWidth().heightIn(min = 46.dp).clickable(onClickLabel = "open ${l.name}", role = Role.Button) { onOpen(l.id) },
                verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Box(Modifier.size(10.dp).background(if (on) N.Amber else N.BorderHi, CircleShape))
                Text(l.name, style = T.label, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                Text(status, style = T.small.copy(color = if (on) N.Amber else N.Muted))
            }
            if (i < lights.lastIndex) Box(Modifier.fillMaxWidth().height(1.dp).background(N.Divider))
        }
        if (lights.isNotEmpty()) {
            val enabled = lights.any { it.available }
            Row(Modifier.fillMaxWidth().padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                GlassButton("All on", Modifier.weight(1f), enabled = enabled) { all("turn_on") }
                GlassButton("All off", Modifier.weight(1f), enabled = enabled) { all("turn_off") }
            }
        }
    }
    if (c.rows.isNotEmpty()) {
        GlassCard(Modifier.fillMaxWidth(), padding = androidx.compose.foundation.layout.PaddingValues(horizontal = 14.dp, vertical = 4.dp)) {
            c.rows.forEachIndexed { i, r ->
                Row(
                    Modifier.fillMaxWidth().heightIn(min = 44.dp).semantics(mergeDescendants = true) {},
                    verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Text(r.label, style = T.label, modifier = Modifier.width(110.dp))
                    Text(r.does, style = T.body.copy(color = N.Muted), modifier = Modifier.weight(1f))
                }
                if (i < c.rows.lastIndex) Box(Modifier.fillMaxWidth().height(1.dp).background(N.Divider))
            }
        }
    }
}

@Composable
private fun Header(e: Entity, k: Kind?) {
    val v = HubModel.vitals(e)
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Box(Modifier.size(48.dp).background(N.Glass, CircleShape), contentAlignment = Alignment.Center) {
            NIcon(HubModel.kindIcon(k), size = 24.dp)
        }
        Column(Modifier.weight(1f)) {
            Text(e.name, style = T.title, maxLines = 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.heading())
            val tone = HubModel.stateTone(e, k, v.problem)
            Text(
                listOfNotNull(HubModel.entityStateText(e, k, long = true).ifEmpty { null }, v.battery?.let { "Battery $it%" }, v.problem).joinToString(" · "),
                style = T.small.copy(color = when (tone) { "warn" -> N.CoralText; "calm" -> N.Teal; else -> N.Muted }),
            )
            Text(listOfNotNull(k?.name, e.integrationName.ifEmpty { null }, e.attrs["area"].asStringOrNull).joinToString(" · "), style = T.tiny)
        }
    }
    Problems(e)
}

/**
 * The device's problems: each one that shows with Mute (a day, a week, until it's fixed, always), then
 * the muted ones, greyed, with when they come back and Unmute. An older hub sends plain issues: shown as before.
 */
@Composable
private fun Problems(e: Entity) {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    var muting by remember { mutableStateOf<HealthItem?>(null) }
    var busy by remember { mutableStateOf<String?>(null) }
    val active = Muting.active(e.health)
    val muted = Muting.muted(e.health)
    if (active.isNotEmpty()) {
        GlassCard(Modifier.fillMaxWidth(), background = N.CoralCard, border = N.CoralBorder,
            padding = androidx.compose.foundation.layout.PaddingValues(horizontal = 14.dp, vertical = 4.dp)) {
            if (e.health.items.isEmpty()) {
                Text(active.joinToString(" · ") { it.text }, style = T.small.copy(color = N.CoralText), modifier = Modifier.padding(vertical = 10.dp))
            } else {
                active.forEachIndexed { i, p ->
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text(p.text, style = T.label.copy(color = N.CoralText), modifier = Modifier.weight(1f))
                        GlassButton("Mute", height = 36.dp, background = N.CoralBg, border = N.CoralBorder) { muting = p }
                    }
                    if (i < active.lastIndex) Box(Modifier.fillMaxWidth().height(1.dp).background(N.CoralBorder))
                }
            }
        }
    }
    if (muted.isNotEmpty()) {
        GlassCard(Modifier.fillMaxWidth(), padding = androidx.compose.foundation.layout.PaddingValues(horizontal = 14.dp, vertical = 10.dp)) {
            CardHeader("Muted")
            muted.forEachIndexed { i, p ->
                Row(Modifier.fillMaxWidth().heightIn(min = 52.dp), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Column(Modifier.weight(1f)) {
                        Text(p.text, style = T.label.copy(color = N.Muted))
                        Text(p.muted?.let { Muting.label(it) } ?: "Muted", style = T.tiny)
                    }
                    GlassButton("Unmute", height = 36.dp, busy = busy == p.id, enabled = busy == null) {
                        busy = p.id
                        scope.launch { app.unmute(e, p.id); busy = null }
                    }
                }
                if (i < muted.lastIndex) Box(Modifier.fillMaxWidth().height(1.dp).background(N.Divider))
            }
        }
    }
    muting?.let { MuteSheet(e, it) { muting = null } }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun DeviceControls(e: Entity, k: Kind?, skip: Set<String>) {
    val app = LocalApp.current
    val run = rememberRunner()
    val busy by app.busy.collectAsState()
    val off = !e.available || e.id in busy
    Controls.rowsFor(e, k).forEach { row ->
        when (row) {
            is Controls.Row.Switch -> {
                val on = HubModel.isActive(e, k)
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Text(HubModel.stateLabel(e, k) ?: if (on) "On" else "Off",
                        style = T.label, modifier = Modifier.weight(1f))
                    NSwitch(on, enabled = !off, label = e.name) { run.run(e, if (it) row.pair.on else row.pair.off) }
                }
            }
            is Controls.Row.Value -> if (row.command.id !in skip) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(row.command.label, style = T.caps, modifier = Modifier.heading())
                    when (row.control) {
                        ControlType.SLIDER -> PercentSlider(e, row.command, !off)
                        ControlType.STEPPER -> Stepper(e, row.command, !off)
                        ControlType.STATE_PICKER -> StatePicker(e, k, row.command, !off)
                        ControlType.CHOICE_CHIPS -> ChoiceChips(e, row.command, !off)
                        ControlType.TEXT -> TextCommand(e, row.command, !off)
                        ControlType.BUTTON -> Unit
                    }
                }
            }
            is Controls.Row.Buttons -> {
                val duo = HubModel.toggleDuo(k, row.commands)
                val isOn = HubModel.isActive(e, k)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    row.commands.filter { duo == null || it != (if (isOn) duo.on else duo.off) }.forEach { c ->
                        val done = c.confirmsTo.isNotEmpty() && c.confirmsTo == e.state
                        GlassButton(
                            c.label, icon = if (app.needsConfirmation(e, c)) "fingerprint" else c.icon.ifEmpty { null },
                            height = 48.dp, enabled = !off && !done, busy = busy.contains(e.id),
                            background = if (c.sensitive) N.CoralCard else N.Glass, border = if (c.sensitive) N.CoralBorder else N.BorderHi,
                        ) { run.run(e, c) }
                    }
                }
            }
        }
    }
}

@Composable
private fun PercentSlider(e: Entity, c: Command, enabled: Boolean) {
    val run = rememberRunner()
    val min = (c.min ?: 0.0).toFloat()
    val max = (c.max ?: 100.0).toFloat()
    val current = HubModel.commandCurrent(e, c).asNumberOrNull?.toFloat()
    var value by remember { mutableFloatStateOf(current ?: min) }
    var dragging by remember { mutableStateOf(false) }
    LaunchedEffect(current) { if (!dragging && current != null) value = current }
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Slider(
            value = value,
            onValueChange = { dragging = true; value = it },
            onValueChangeFinished = {
                dragging = false
                run.run(e, c, jsonOf(value.roundToInt()))
            },
            valueRange = min..max,
            enabled = enabled,
            colors = SliderDefaults.colors(thumbColor = N.Text, activeTrackColor = N.Amber, inactiveTrackColor = Color(0x24FFFFFF)),
            modifier = Modifier.weight(1f),
        )
        Text(HubModel.valueText(c, null, jsonOf(value.roundToInt())), style = T.label, modifier = Modifier.width(56.dp))
    }
}

@Composable
private fun Stepper(e: Entity, c: Command, enabled: Boolean) {
    val run = rememberRunner()
    val scope = rememberCoroutineScope()
    val current = HubModel.commandCurrent(e, c).asNumberOrNull
    var value by remember { mutableStateOf(current) }
    var job by remember { mutableStateOf<Job?>(null) }
    LaunchedEffect(current) { if (job?.isActive != true) value = current }
    val step = c.step ?: 1.0
    val decimals = step.toString().substringAfter('.', "").trimEnd('0').length
    fun bump(d: Double) {
        var next = Math.round(((value ?: current ?: c.default.asNumberOrNull ?: 0.0) + d) / step) * step
        c.min?.let { next = maxOf(it, next) }
        c.max?.let { next = minOf(it, next) }
        value = next
        job?.cancel()
        job = scope.launch {
            delay(800)
            if (next != current) run.run(e, c, jsonOf(next))
        }
    }
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
        StepButton("−", "Lower ${Spoken.phrase(c.label)}", enabled) { bump(-step) }
        Text((value?.let { String.format(Locale.getDefault(), "%.${decimals}f", it) } ?: "—") + if (c.unit.isNotEmpty()) " ${c.unit}" else "",
            style = T.title.copy(fontSize = 26.sp), modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        StepButton("+", "Raise ${Spoken.phrase(c.label)}", enabled) { bump(step) }
    }
}

@Composable
private fun StepButton(text: String, label: String, enabled: Boolean, onClick: () -> Unit) {
    Box(
        Modifier.size(52.dp).clip(CircleShape).background(Color(0x0FFFFFFF)).border(1.dp, N.BorderHi, CircleShape)
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick).clearAndSetSemantics { contentDescription = label; role = Role.Button },
        contentAlignment = Alignment.Center,
    ) { Text(text, style = T.title.copy(fontSize = 26.sp)) }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun StatePicker(e: Entity, k: Kind?, c: Command, enabled: Boolean) {
    val run = rememberRunner()
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        HubModel.stateChoices(k).forEach { s ->
            val on = e.state == s.id
            Box(
                Modifier.heightIn(min = 48.dp).clip(RoundedCornerShape(14.dp)).background(if (on) N.Text else Color(0x0FFFFFFF))
                    .border(1.dp, N.Border, RoundedCornerShape(14.dp))
                    .selectable(selected = on, enabled = enabled && !on, role = Role.RadioButton) { run.run(e, c, jsonOf(s.id)) }.padding(horizontal = 16.dp),
                contentAlignment = Alignment.Center,
            ) { Text(s.label, style = T.label.copy(fontSize = 14.sp, color = if (on) N.Night else N.Text)) }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ChoiceChips(e: Entity, c: Command, enabled: Boolean) {
    val run = rememberRunner()
    val items = HubModel.choiceItems(e, c)
    var picked by remember { mutableStateOf(listOf<String>()) }
    if (items.isEmpty()) {
        Text("${e.name} hasn't listed any yet.", style = T.tiny)
        return
    }
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        items.forEach { item ->
            val key = item.id.toString()
            val on = key in picked
            Row(
                Modifier.minimumInteractiveComponentSize().heightIn(min = 40.dp).clip(RoundedCornerShape(99.dp)).background(if (on) N.Teal else Color(0x0FFFFFFF))
                    .toggleable(value = on, role = Role.Checkbox) { picked = if (on) picked - key else picked + key }.padding(horizontal = 14.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                if (on) NIcon("check", size = 16.dp, color = N.Night, stroke = 2.6f)
                Text(item.name, style = T.small.copy(color = if (on) N.Night else N.Text))
            }
        }
    }
    GlassButton(if (picked.isEmpty()) "Pick one or more" else "Go · ${picked.size}", Modifier.fillMaxWidth(), height = 48.dp,
        enabled = enabled && picked.isNotEmpty(), background = N.Text, foreground = N.Night, border = N.Text) {
        run.run(e, c, JsonArray(items.filter { it.id.toString() in picked }.map { it.id }))
        picked = emptyList()
    }
}

@Composable
private fun TextCommand(e: Entity, c: Command, enabled: Boolean) {
    val run = rememberRunner()
    var text by remember { mutableStateOf("") }
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Box(Modifier.weight(1f)) { NTextField(text, { text = it }, "Message (optional)") }
        GlassButton(c.label, icon = c.icon.ifEmpty { "send" }, height = 52.dp, enabled = enabled) {
            run.run(e, c, jsonOf(text))
            text = ""
        }
    }
}

@Composable
private fun Readings(e: Entity) {
    if (e.properties.isEmpty()) return
    GlassCard(Modifier.fillMaxWidth(), padding = androidx.compose.foundation.layout.PaddingValues(horizontal = 14.dp, vertical = 4.dp)) {
        Text("Readings", style = T.caps, modifier = Modifier.padding(top = 10.dp, bottom = 4.dp).heading())
        e.properties.forEachIndexed { i, p ->
            Row(Modifier.fillMaxWidth().heightIn(min = 46.dp).semantics(mergeDescendants = true) {}, verticalAlignment = Alignment.CenterVertically) {
                Text(p.label, style = T.body, modifier = Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(HubModel.formatProp(p, p.value), style = T.label)
            }
            if (i < e.properties.lastIndex) Box(Modifier.fillMaxWidth().height(1.dp).background(N.Divider))
        }
    }
}

/**
 * "Add to home screen": a launcher shortcut that runs what a tap does now (as a widget tile would)
 * without opening the app. Only for a device a tap can do something with, on launchers that pin.
 */
@Composable
private fun AddToHomeScreen(e: Entity, k: Kind?) {
    val app = LocalApp.current
    val context = LocalContext.current
    if (HubModel.tapAction(e, k) == null || !LauncherShortcuts.canPin(context)) return
    GlassButton("Add to home screen", Modifier.fillMaxWidth(), icon = "plus", height = 48.dp) {
        LauncherShortcuts.requestPin(context, e.id, e.name, HubModel.kindIcon(k), k?.id)
            .let { ok -> if (!ok) app.toast("This home screen can't add shortcuts", ok = false) }
    }
}