package ca.nimaste.newman.ui.more

import android.app.TimePickerDialog
import android.text.format.DateFormat as AndroidDateFormat
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.minimumInteractiveComponentSize
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
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.hub.HubState
import ca.nimaste.newman.core.model.Automation
import ca.nimaste.newman.core.model.AutoEdit
import ca.nimaste.newman.core.model.Command
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.Kind
import ca.nimaste.newman.core.model.asNumberOrNull
import ca.nimaste.newman.core.model.asStringOrNull
import ca.nimaste.newman.core.model.number
import ca.nimaste.newman.core.model.string
import ca.nimaste.newman.core.model.withKey
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.home.AccentButton
import ca.nimaste.newman.ui.home.BackLink
import ca.nimaste.newman.ui.home.Caps
import ca.nimaste.newman.ui.home.DevicePickerSheet
import ca.nimaste.newman.ui.home.HCard
import ca.nimaste.newman.ui.home.HScreen
import ca.nimaste.newman.ui.home.HSwitch
import ca.nimaste.newman.ui.home.HT
import ca.nimaste.newman.ui.home.RaisedButton
import ca.nimaste.newman.ui.theme.H
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.time.LocalTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

/** Days shown Sunday first; the hub counts Monday = 0. */
private val SHOWN_DAYS = listOf(6, 0, 1, 2, 3, 4, 5)
private val DAY_LETTER = mapOf(0 to "M", 1 to "T", 2 to "W", 3 to "T", 4 to "F", 5 to "S", 6 to "S")
private val DAY_NAME = mapOf(0 to "Monday", 1 to "Tuesday", 2 to "Wednesday", 3 to "Thursday", 4 to "Friday", 5 to "Saturday", 6 to "Sunday")

/** What a device picker is choosing for. */
private sealed interface Pick {
    data class NewTrigger(val type: String) : Pick
    data class TriggerDevice(val index: Int) : Pick
    data object NewAction : Pick
    data class ActionDevice(val index: Int) : Pick
    data object NewCondition : Pick
    data class ConditionDevice(val index: Int) : Pick
}

/**
 * One automation, editable (a new one when [id] is null): its name, on/off, When (times with days,
 * device states, events, readings), What happens, Only if, and Don't repeat for. The hub's own JSON
 * is edited in place (AutoEdit), so parts this editor doesn't know stay exactly as they were and show
 * "Edit on the web". Accounts that can't change automations see it read-only.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun AutomationEditor(id: String?, onBack: () -> Unit, onSaved: () -> Unit) {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    val state by app.state.collectAsState()
    val home by app.home.collectAsState()
    val can = home?.canAutomate ?: state.auth?.user?.canAutomate ?: false
    var savedId by remember { mutableStateOf(id) }
    var original by remember { mutableStateOf<JsonObject?>(null) }
    var a by remember { mutableStateOf<JsonObject?>(null) }
    var summary by remember { mutableStateOf("") }
    var saving by remember { mutableStateOf(false) }
    var running by remember { mutableStateOf(false) }
    var asking by remember { mutableStateOf<String?>(null) } // "discard" | "delete"
    var pick by remember { mutableStateOf<Pick?>(null) }
    LaunchedEffect(id) {
        val loaded = if (id == null) AutoEdit.blank() else app.automationJson(id)
        original = loaded
        a = loaded
    }
    // The hub's own words for it, a moment after each change.
    LaunchedEffect(a) {
        val cur = a ?: return@LaunchedEffect
        delay(400)
        summary = app.describeAutomation(cur)
    }
    val ro = !can
    val dirty = AutoEdit.dirty(original, a)
    val leave: () -> Unit = { if (dirty && !ro) { asking = "discard" } else { onBack() } }
    BackHandler(onBack = leave)

    val cur = a
    if (cur == null) {
        HScreen("Automation", back = "Automations", onBack = onBack) {
            item(key = "loading") { Text("Loading…", style = HT.line, modifier = Modifier.padding(4.dp)) }
        }
        return
    }
    val update: (JsonObject) -> Unit = { if (!ro) a = it }

    HScreen(AutoEdit.name(cur).ifBlank { if (savedId == null) "New automation" else "Automation" }, back = "Automations", onBack = leave, big = false,
        trailing = if (ro) null else { {
            AccentButton("Save", enabled = !saving && AutoEdit.name(cur).isNotBlank()) {
                saving = true
                scope.launch {
                    val saved = app.saveAutomation(savedId, cur)
                    saving = false
                    if (saved != null) {
                        savedId = saved.string("id") ?: savedId
                        original = cur
                        onSaved()
                        onBack()
                    }
                }
            }
        } }) {
        if (ro) item(key = "ro") {
            Text("Your account can't change automations.", style = HT.line.copy(color = H.WarnText), modifier = Modifier.padding(horizontal = 4.dp))
        }
        item(key = "name") {
            Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                BasicTextField(
                    AutoEdit.name(cur), { update(AutoEdit.withName(cur, it)) }, readOnly = ro, singleLine = true,
                    textStyle = HT.name.copy(fontSize = 20.sp), cursorBrush = SolidColor(H.Text),
                    modifier = Modifier.weight(1f).heightIn(min = 44.dp).semantics { contentDescription = "Name" },
                    decorationBox = { inner ->
                        Column {
                            Box(Modifier.padding(vertical = 8.dp)) {
                                if (AutoEdit.name(cur).isEmpty()) Text("Name", style = HT.name.copy(fontSize = 20.sp, color = H.Placeholder))
                                inner()
                            }
                            Box(Modifier.fillMaxWidth().padding(top = 0.dp).background(H.RaisedLine).heightIn(min = 1.dp))
                        }
                    },
                )
                HSwitch(AutoEdit.enabled(cur), label = if (AutoEdit.enabled(cur)) "On" else "Off", enabled = !ro, small = true) {
                    update(AutoEdit.withEnabled(cur, it))
                }
            }
        }
        if (summary.isNotBlank()) item(key = "summary") {
            Text(summary, style = HT.line.copy(color = H.Soft, fontWeight = FontWeight.SemiBold, lineHeight = 20.sp),
                modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(H.Inset).border(1.dp, H.CardLine, RoundedCornerShape(16.dp))
                    .padding(horizontal = 14.dp, vertical = 12.dp))
        }

        // -- When --
        item(key = "when") {
            val items = AutoEdit.items(cur)
            Section("When") {
                if (items.isEmpty()) Text("Only when run by hand", style = HT.line)
                items.forEachIndexed { i, item ->
                    if (i > 0) Text("or", style = HT.small.copy(color = H.Label))
                    val set: (JsonObject) -> Unit = { n -> update(AutoEdit.withItems(cur, items.toMutableList().also { it[i] = n })) }
                    val remove = if (items.size > 1 && !ro) ({ update(AutoEdit.withItems(cur, items.filterIndexed { j, _ -> j != i })) }) else null
                    TriggerItem(item, state, ro, set, remove, onDevice = { pick = Pick.TriggerDevice(i) })
                }
                if (!ro) AddMenu("+ Or when something else happens", listOf("time" to "At a time", "state" to "A device changes",
                    "event" to "A device event", "property" to "A reading goes above or below")) { type ->
                    if (type == "time") update(AutoEdit.withItems(cur, items + AutoEdit.timeItem("07:00", AutoEdit.ALL_DAYS)))
                    else pick = Pick.NewTrigger(type)
                }
            }
        }

        // -- What happens --
        item(key = "what") {
            val actions = AutoEdit.actions(cur)
            Section("What happens") {
                if (actions.isEmpty()) Text("Nothing yet", style = HT.line)
                actions.forEachIndexed { i, act ->
                    val set: (JsonObject) -> Unit = { n -> update(AutoEdit.withActions(cur, actions.toMutableList().also { it[i] = n })) }
                    val remove = if (!ro) ({ update(AutoEdit.withActions(cur, actions.filterIndexed { j, _ -> j != i })) }) else null
                    ActionItem(act, state, ro, set, remove, onDevice = { pick = Pick.ActionDevice(i) })
                }
                if (!ro) LinkButton("+ Do something else too") { pick = Pick.NewAction }
            }
        }

        // -- Only if --
        item(key = "if") {
            val rows = AutoEdit.conditionRows(cur)
            Section("Only if") {
                if (rows.isEmpty() && !AutoEdit.hasOtherLimits(cur)) Text("Always — no conditions", style = HT.line.copy(fontWeight = FontWeight.SemiBold))
                rows.forEachIndexed { i, row ->
                    val set: (JsonObject) -> Unit = { n -> update(AutoEdit.withConditionRows(cur, rows.toMutableList().also { it[i] = n })) }
                    val remove = if (!ro) ({ update(AutoEdit.withConditionRows(cur, rows.filterIndexed { j, _ -> j != i })) }) else null
                    ConditionItem(row, state, ro, set, remove, onDevice = { pick = Pick.ConditionDevice(i) })
                }
                if (AutoEdit.hasOtherLimits(cur)) ReadOnlyPart("Also limited by time, days, daylight or dates")
                if (!ro) LinkButton("+ Only if… (someone's home, a door is closed…)") { pick = Pick.NewCondition }
            }
        }

        // -- Don't repeat for --
        item(key = "cooldown") {
            HCard(padding = PaddingValues(horizontal = 16.dp, vertical = 14.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Caps("Don't repeat for")
                        Text("After it runs, ignore new triggers for a while", style = HT.line.copy(fontWeight = FontWeight.SemiBold))
                    }
                    var open by remember { mutableStateOf(false) }
                    Box {
                        RaisedButton(AutoEdit.cooldownText(AutoEdit.cooldown(cur)) + " ▾", height = 38.dp, radius = 12.dp, enabled = !ro,
                            style = HT.button.copy(fontSize = 14.sp)) { open = true }
                        DropdownMenu(open, onDismissRequest = { open = false }, containerColor = H.Raised) {
                            AutoEdit.COOLDOWNS.forEach { m ->
                                DropdownMenuItem(text = { Text(AutoEdit.cooldownText(m), style = HT.row) }, onClick = {
                                    open = false
                                    update(AutoEdit.withCooldown(cur, m))
                                })
                            }
                        }
                    }
                }
            }
        }

        if (savedId != null && !ro) item(key = "buttons") {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                RaisedButton(if (running) "Running…" else "Run now", Modifier.weight(1f), height = 46.dp, radius = 14.dp, enabled = !running && !dirty,
                    style = HT.button.copy(fontSize = 14.sp)) {
                    running = true
                    scope.launch {
                        app.runAutomation(Automation(savedId!!, AutoEdit.name(cur)))
                        running = false
                    }
                }
                RaisedButton("Delete", Modifier.weight(1f), height = 46.dp, radius = 14.dp, background = H.Hot, border = H.HotLine, color = H.HotText,
                    style = HT.button.copy(fontSize = 14.sp)) { asking = "delete" }
            }
        }
    }

    when (asking) {
        "discard" -> AlertDialog(
            onDismissRequest = { asking = null },
            title = { Text("Discard your changes?") },
            confirmButton = { TextButton(onClick = { asking = null; onBack() }) { Text("Discard") } },
            dismissButton = { TextButton(onClick = { asking = null }) { Text("Keep editing") } },
            containerColor = H.Card,
        )
        "delete" -> AlertDialog(
            onDismissRequest = { asking = null },
            title = { Text("Delete ${AutoEdit.name(cur).ifBlank { "this automation" }}?") },
            confirmButton = {
                TextButton(onClick = {
                    asking = null
                    val sid = savedId ?: return@TextButton
                    scope.launch { if (app.deleteAutomation(sid)) { onSaved(); onBack() } }
                }) { Text("Delete") }
            },
            dismissButton = { TextButton(onClick = { asking = null }) { Text("Cancel") } },
            containerColor = H.Card,
        )
    }

    pick?.let { p ->
        val (title, filter) = pickerFor(p, state)
        DevicePickerSheet(state, title = title, filter = filter, onDismiss = { pick = null }) { eid ->
            pick = null
            val k = state.kind(state.named[eid])
            when (p) {
                is Pick.NewTrigger -> {
                    val item = newTrigger(p.type, eid, state.named[eid], k) ?: return@DevicePickerSheet
                    update(AutoEdit.withItems(cur, AutoEdit.items(cur) + item))
                }
                is Pick.TriggerDevice -> {
                    val items = AutoEdit.items(cur)
                    val old = items.getOrNull(p.index) ?: return@DevicePickerSheet
                    val item = newTrigger(old.string("type") ?: return@DevicePickerSheet, eid, state.named[eid], k) ?: return@DevicePickerSheet
                    update(AutoEdit.withItems(cur, items.toMutableList().also { it[p.index] = item }))
                }
                Pick.NewAction -> {
                    val cmd = actionCommands(k).firstOrNull() ?: return@DevicePickerSheet
                    update(AutoEdit.withActions(cur, AutoEdit.actions(cur) + AutoEdit.action(listOf(eid), cmd.id, defaultValue(cmd))))
                }
                is Pick.ActionDevice -> {
                    val actions = AutoEdit.actions(cur)
                    val old = actions.getOrNull(p.index) ?: return@DevicePickerSheet
                    val cmds = actionCommands(k)
                    // Keep the command when the new device has it too; else its first.
                    val keep = cmds.firstOrNull { it.id == old.string("command") }
                    val n = if (keep != null) JsonObject(old - "entity").withKey("entities", JsonArray(listOf(JsonPrimitive(eid))))
                    else AutoEdit.action(listOf(eid), cmds.firstOrNull()?.id ?: return@DevicePickerSheet, cmds.firstOrNull()?.let { defaultValue(it) })
                    update(AutoEdit.withActions(cur, actions.toMutableList().also { it[p.index] = n }))
                }
                Pick.NewCondition -> update(AutoEdit.withConditionRows(cur, AutoEdit.conditionRows(cur) + AutoEdit.conditionRow(eid, defaultState(k))))
                is Pick.ConditionDevice -> {
                    val rows = AutoEdit.conditionRows(cur)
                    if (p.index in rows.indices) update(AutoEdit.withConditionRows(cur, rows.toMutableList().also {
                        it[p.index] = AutoEdit.conditionRow(eid, defaultState(k), rows[p.index].string("match") ?: "any")
                    }))
                }
            }
        }
    }
}

// -- device rules (all from kinds metadata) ---------------------------------------------------------

private fun numberProps(e: Entity?): List<ca.nimaste.newman.core.model.Property> = e?.properties?.filter { it.type == "number" } ?: emptyList()

private fun actionCommands(k: Kind?): List<Command> = (k?.commands ?: emptyList()) + (k?.automationCommands ?: emptyList())

/** A condition's first state: the kind's secure state (locked, closed), else its active one. */
private fun defaultState(k: Kind?): String = k?.secureState ?: k?.activeState ?: HubModel.stateChoices(k).firstOrNull()?.id ?: "active"

private fun defaultValue(c: Command): JsonElement? = when (c.valueType) {
    "" -> null
    "percent" -> c.default ?: JsonPrimitive(50)
    "number" -> c.default ?: JsonPrimitive(c.min ?: 0.0)
    else -> c.default
}

/** A new When part for a picked device; its events and readings are the device's own. */
private fun newTrigger(type: String, eid: String, e: Entity?, k: Kind?): JsonObject? = when (type) {
    "state" -> AutoEdit.stateItem(eid)
    "event" -> HubModel.eventsFor(e, k).firstOrNull()?.let { AutoEdit.eventItem(eid, it.id) } ?: AutoEdit.eventItem(eid, "")
    "property" -> AutoEdit.propertyItem(eid, numberProps(e).firstOrNull()?.id ?: "", "above", null)
    else -> null
}

private fun pickerFor(p: Pick, state: HubState): Pair<String, (Entity) -> Boolean> {
    val type = when (p) {
        is Pick.NewTrigger -> p.type
        is Pick.TriggerDevice -> "trigger"
        Pick.NewAction, is Pick.ActionDevice -> "action"
        Pick.NewCondition, is Pick.ConditionDevice -> "condition"
    }
    return when (type) {
        "state", "condition" -> "Pick a device" to { e: Entity -> HubModel.stateChoices(state.kind(e)).isNotEmpty() }
        "event" -> "Pick a device" to { e: Entity -> HubModel.eventsFor(e, state.kind(e)).isNotEmpty() }
        "property" -> "Pick a device" to { e: Entity -> numberProps(e).isNotEmpty() }
        "action" -> "Pick a device" to { e: Entity -> actionCommands(state.kind(e)).isNotEmpty() }
        else -> "Pick a device" to { _: Entity -> true }
    }
}

// -- parts ------------------------------------------------------------------------------------------

@Composable
private fun Section(title: String, content: @Composable () -> Unit) {
    HCard(padding = PaddingValues(horizontal = 16.dp, vertical = 14.dp)) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Caps(title)
            content()
        }
    }
}

@Composable
private fun LinkButton(text: String, onClick: () -> Unit) {
    Text(text, style = HT.line.copy(color = H.Info),
        modifier = Modifier.minimumInteractiveComponentSize().clickable(role = Role.Button, onClick = onClick).padding(vertical = 4.dp))
}

@Composable
private fun AddMenu(text: String, options: List<Pair<String, String>>, onPick: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        LinkButton(text) { open = true }
        DropdownMenu(open, onDismissRequest = { open = false }, containerColor = H.Raised) {
            options.forEach { (id, label) ->
                DropdownMenuItem(text = { Text(label, style = HT.row) }, onClick = { open = false; onPick(id) })
            }
        }
    }
}

/** A part made elsewhere (a newer or older editor): shown, kept as it is, changed on the web. */
@Composable
private fun ReadOnlyPart(text: String) {
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(H.Raised).border(1.dp, H.TileLine, RoundedCornerShape(16.dp)).padding(12.dp)) {
        Text(text, style = HT.row.copy(fontSize = 14.sp))
        Text("Edit on the web", style = HT.small.copy(color = H.Label))
    }
}

/** A part's box: the device (with Change) on top, its settings below, and remove (×) when it may go. */
@Composable
private fun PartBox(names: String, sub: String?, ro: Boolean, onDevice: (() -> Unit)?, onRemove: (() -> Unit)?, content: @Composable () -> Unit) {
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(H.Raised).border(1.dp, H.TileLine, RoundedCornerShape(16.dp)).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Column(Modifier.weight(1f)) {
                Text(names, style = HT.name, maxLines = 2, overflow = TextOverflow.Ellipsis)
                if (!sub.isNullOrBlank()) Text(sub, style = HT.small.copy(color = H.Label, fontWeight = FontWeight.SemiBold))
            }
            if (!ro && onDevice != null) RaisedButton("Change", height = 32.dp, radius = 10.dp, background = H.Inset, color = H.Soft,
                style = HT.small.copy(fontWeight = FontWeight.Bold), onClickLabel = "pick another device", onClick = onDevice)
            if (!ro && onRemove != null) Box(
                Modifier.minimumInteractiveComponentSize().size(30.dp).clip(CircleShape).background(H.RemoveBg).clickable(role = Role.Button, onClick = onRemove)
                    .clearAndSetSemantics { contentDescription = "Remove $names"; role = Role.Button },
                contentAlignment = Alignment.Center,
            ) { Text("×", style = HT.name.copy(fontSize = 16.sp, color = H.DangerIcon)) }
        }
        content()
    }
}

private fun deviceNames(ids: List<String>, state: HubState): String =
    ids.map { state.named[it]?.name ?: it }.let { if (it.isEmpty()) "No device" else if (it.size <= 3) it.joinToString(", ") else "${it.take(2).joinToString(", ")} and ${it.size - 2} more" }

private fun groupLine(ids: List<String>, state: HubState): String? {
    val e = ids.singleOrNull()?.let { state.named[it] } ?: return null
    val members = ca.nimaste.newman.core.model.HomeLogic.members(e)
    return if (members.isEmpty()) state.kind(e)?.name else "Group · ${members.size} lights"
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Choices(options: List<Pair<String, String>>, selected: String?, ro: Boolean, onPick: (String) -> Unit) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        options.forEach { (id, label) ->
            val on = id == selected
            Box(
                Modifier.heightIn(min = 34.dp).clip(RoundedCornerShape(17.dp)).background(if (on) H.Selected else H.Inset)
                    .border(1.dp, if (on) H.Accent else H.CardLine, RoundedCornerShape(17.dp))
                    .selectable(selected = on, enabled = !ro, role = Role.RadioButton) { onPick(id) }.padding(horizontal = 12.dp),
                contentAlignment = Alignment.Center,
            ) { Text(label, style = HT.button.copy(color = if (on) H.Text else H.Soft)) }
        }
    }
}

// -- When ---------------------------------------------------------------------------------------------

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TriggerItem(item: JsonObject, state: HubState, ro: Boolean, set: (JsonObject) -> Unit, onRemove: (() -> Unit)?, onDevice: () -> Unit) {
    val type = item.string("type")
    val sun = item["sun"]?.takeIf { it !is JsonNull }
    if (!AutoEdit.isKnownItem(item) || (type == "time" && sun != null)) {
        ReadOnlyPart(when {
            type == "time" -> "At ${sun?.asStringOrNull ?: "a time of day"}"
            type.isNullOrBlank() -> "Another kind of trigger"
            else -> "When: ${type.replace('_', ' ')}"
        })
        return
    }
    when (type) {
        "time" -> TimeItem(item, ro, set, onRemove)
        "state" -> {
            val ids = AutoEdit.entities(item)
            val k = state.kind(ids.firstOrNull()?.let { state.named[it] })
            PartBox(deviceNames(ids, state), "changes to", ro, onDevice, onRemove) {
                Choices(listOf("any" to "Anything") + HubModel.stateChoices(k).map { it.id to it.label }, item.string("to") ?: "any", ro) {
                    set(item.withKey("to", JsonPrimitive(it)))
                }
            }
        }
        "event" -> {
            val ids = AutoEdit.entities(item)
            val e = ids.firstOrNull()?.let { state.named[it] }
            PartBox(deviceNames(ids, state), "when this happens", ro, onDevice, onRemove) {
                Choices(HubModel.eventsFor(e, state.kind(e)).map { it.id to it.label }, item.string("event"), ro) {
                    set(item.withKey("event", JsonPrimitive(it)))
                }
            }
        }
        "property" -> {
            val ids = AutoEdit.entities(item)
            val e = ids.firstOrNull()?.let { state.named[it] }
            val props = numberProps(e)
            val prop = props.firstOrNull { it.id == item.string("property") }
            PartBox(deviceNames(ids, state), prop?.label ?: item.string("property"), ro, onDevice, onRemove) {
                if (props.size > 1) Choices(props.map { it.id to it.label }, item.string("property"), ro) { set(item.withKey("property", JsonPrimitive(it))) }
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Choices(listOf("above" to "Above", "below" to "Below"), item.string("op"), ro) { set(item.withKey("op", JsonPrimitive(it))) }
                    NumberField(item["value"].asNumberOrNull, prop?.unit ?: "", ro) { v -> set(item.withKey("value", v?.let { JsonPrimitive(it) } ?: JsonNull)) }
                }
            }
        }
    }
}

@Composable
private fun TimeItem(item: JsonObject, ro: Boolean, set: (JsonObject) -> Unit, onRemove: (() -> Unit)?) {
    val context = LocalContext.current
    val (h, m) = AutoEdit.parseTime(item.string("time"))
    val shown = LocalTime.of(h, m).format(DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT))
    val days = AutoEdit.days(item)
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("At a time", style = HT.name, modifier = Modifier.weight(1f))
            RaisedButton(shown, height = 40.dp, radius = 12.dp, enabled = !ro, style = HT.name.copy(fontSize = 17.sp), onClickLabel = "change the time") {
                TimePickerDialog(context, { _, hh, mm -> set(item.withKey("time", JsonPrimitive(AutoEdit.hhmm(hh, mm)))) }, h, m,
                    AndroidDateFormat.is24HourFormat(context)).show()
            }
            if (onRemove != null) Box(
                Modifier.minimumInteractiveComponentSize().size(30.dp).clip(CircleShape).background(H.RemoveBg).clickable(role = Role.Button, onClick = onRemove)
                    .clearAndSetSemantics { contentDescription = "Remove this time"; role = Role.Button },
                contentAlignment = Alignment.Center,
            ) { Text("×", style = HT.name.copy(fontSize = 16.sp, color = H.DangerIcon)) }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            SHOWN_DAYS.forEach { d ->
                val on = d in days
                Box(
                    Modifier.weight(1f).heightIn(min = 36.dp).clip(RoundedCornerShape(18.dp)).background(if (on) H.Selected else H.Inset)
                        .selectable(selected = on, enabled = !ro, role = Role.Checkbox) {
                            // At least one day stays.
                            if (!(on && days.size == 1)) set(AutoEdit.toggleDay(item, d))
                        }
                        .clearAndSetSemantics { contentDescription = DAY_NAME[d] ?: ""; selected = on; role = Role.Checkbox },
                    contentAlignment = Alignment.Center,
                ) { Text(DAY_LETTER[d] ?: "", style = HT.button.copy(color = if (on) H.Text else H.Label)) }
            }
        }
    }
}

@Composable
private fun NumberField(value: Double?, unit: String, ro: Boolean, onChange: (Double?) -> Unit) {
    var text by remember(value) { mutableStateOf(value?.let { if (it == Math.floor(it)) it.toLong().toString() else it.toString() } ?: "") }
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        BasicTextField(text, { t ->
            text = t
            val v = t.replace(',', '.').toDoubleOrNull()
            if (v != null || t.isBlank()) onChange(v)
        }, readOnly = ro, singleLine = true, textStyle = HT.name, cursorBrush = SolidColor(H.Text),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            modifier = Modifier.width(72.dp).clip(RoundedCornerShape(10.dp)).background(H.Inset).border(1.dp, H.CardLine, RoundedCornerShape(10.dp))
                .padding(horizontal = 10.dp, vertical = 8.dp).semantics { contentDescription = "Value" })
        if (unit.isNotBlank()) Text(unit, style = HT.line)
    }
}

// -- What happens -------------------------------------------------------------------------------------

@Composable
private fun ActionItem(act: JsonObject, state: HubState, ro: Boolean, set: (JsonObject) -> Unit, onRemove: (() -> Unit)?, onDevice: () -> Unit) {
    if (!AutoEdit.isKnownAction(act)) {
        ReadOnlyPart(act.string("type")?.let { "Then: ${it.replace('_', ' ')}" } ?: "Another kind of action")
        return
    }
    val ids = AutoEdit.entities(act)
    val e = ids.firstOrNull()?.let { state.named[it] }
    val k = state.kind(e)
    // The commands every chosen device supports.
    val cmds = ids.map { id -> actionCommands(state.kind(state.named[id])) }.reduceOrNull { acc, l -> acc.filter { c -> l.any { it.id == c.id } } } ?: emptyList()
    val cmd = cmds.firstOrNull { it.id == act.string("command") }
    PartBox(deviceNames(ids, state), groupLine(ids, state), ro, onDevice, onRemove) {
        if (cmds.size > 1 || cmd == null) {
            Choices(cmds.map { it.id to it.label }, act.string("command"), ro) { id ->
                val c = cmds.first { it.id == id }
                set(AutoEdit.withForMinutes(AutoEdit.action(ids, c.id, defaultValue(c)), null).let { fresh ->
                    // Keep the parts of the action this editor doesn't show (delays, conditions…).
                    JsonObject(act - "command" - "value" - "for" - "forMinutes" - "entity" + fresh)
                })
            }
        }
        if (cmd == null) return@PartBox
        when (cmd.valueType) {
            "percent" -> {
                val v = act["value"].asNumberOrNull ?: 50.0
                var shown by remember(v) { mutableFloatStateOf(v.toFloat()) }
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(cmd.label, style = HT.row.copy(fontSize = 14.sp, color = H.Soft))
                    Slider(shown, { shown = it }, valueRange = ((cmd.min ?: 1.0).toFloat())..((cmd.max ?: 100.0).toFloat()), enabled = !ro,
                        onValueChangeFinished = { set(act.withKey("value", JsonPrimitive(shown.toInt()))) },
                        colors = SliderDefaults.colors(thumbColor = H.Light, activeTrackColor = H.Light, inactiveTrackColor = H.LightTrack),
                        modifier = Modifier.weight(1f).semantics { contentDescription = cmd.label })
                    Text("${shown.toInt()}%", style = HT.name.copy(color = H.LightText), modifier = Modifier.width(44.dp))
                }
                // "Then put it back after N minutes" (how the web editor saves it: for {amount, unit}).
                val mins = AutoEdit.forMinutes(act)
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("Then put it back after", style = HT.row.copy(fontSize = 14.sp, color = H.Soft), modifier = Modifier.weight(1f))
                    Stepper(mins?.let { "$it min" } ?: "Never", ro,
                        onMinus = { set(AutoEdit.withForMinutes(act, mins?.let { if (it <= 5) null else it - 5 })) },
                        onPlus = { set(AutoEdit.withForMinutes(act, (mins ?: 0) + 5)) }, what = "minutes")
                }
            }
            "number" -> {
                val v = act["value"].asNumberOrNull ?: cmd.min ?: 0.0
                val step = cmd.step ?: 1.0
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(cmd.label, style = HT.row.copy(fontSize = 14.sp, color = H.Soft), modifier = Modifier.weight(1f))
                    Stepper(HubModel.valueText(cmd, k, JsonPrimitive(v), e), ro, what = cmd.label.lowercase(),
                        onMinus = { set(act.withKey("value", JsonPrimitive((v - step).coerceAtLeast(cmd.min ?: Double.NEGATIVE_INFINITY)))) },
                        onPlus = { set(act.withKey("value", JsonPrimitive((v + step).coerceAtMost(cmd.max ?: Double.POSITIVE_INFINITY)))) })
                }
            }
            "state" -> Choices(HubModel.stateChoices(k).map { it.id to it.label }, act["value"].asStringOrNull, ro) {
                set(act.withKey("value", JsonPrimitive(it)))
            }
            "" -> Unit
            else -> Text(HubModel.valueText(cmd, k, act["value"], e), style = HT.line)
        }
    }
}

@Composable
private fun Stepper(text: String, ro: Boolean, what: String, onMinus: () -> Unit, onPlus: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        StepButton("−", "Fewer $what", !ro, onMinus)
        Text(text, style = HT.name)
        StepButton("+", "More $what", !ro, onPlus)
    }
}

@Composable
private fun StepButton(sign: String, label: String, enabled: Boolean, onClick: () -> Unit) {
    Box(
        Modifier.minimumInteractiveComponentSize().size(34.dp).clip(CircleShape).background(H.Inset).border(1.dp, H.RaisedLine, CircleShape)
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick).clearAndSetSemantics { contentDescription = label; role = Role.Button },
        contentAlignment = Alignment.Center,
    ) { Text(sign, style = HT.name.copy(fontSize = 18.sp, color = if (enabled) H.Text else H.Label)) }
}

// -- Only if ------------------------------------------------------------------------------------------

@Composable
private fun ConditionItem(row: JsonObject, state: HubState, ro: Boolean, set: (JsonObject) -> Unit, onRemove: (() -> Unit)?, onDevice: () -> Unit) {
    val ids = AutoEdit.entities(row)
    if (ids.isEmpty()) {
        ReadOnlyPart("Another kind of condition")
        return
    }
    val k = state.kind(state.named[ids.first()])
    PartBox(deviceNames(ids, state), if (ids.size > 1) "are" else "is", ro, onDevice, onRemove) {
        val options = HubModel.stateChoices(k).map { it.id to it.label } +
            (if (k?.activeState != null) emptyList() else listOf("active" to "Active"))
        Choices(options, row.string("state"), ro) { set(row.withKey("state", JsonPrimitive(it))) }
        if (ids.size > 1) Choices(listOf("any" to "Any of them", "all" to "All of them", "none" to "None of them"), row.string("match") ?: "any", ro) {
            set(row.withKey("match", JsonPrimitive(it)))
        }
    }
}
