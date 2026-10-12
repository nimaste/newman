package ca.nimaste.newman.widgets

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.RadioButtonDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import ca.nimaste.newman.core.widgets.WidgetChoice
import ca.nimaste.newman.core.widgets.WidgetGrouping
import ca.nimaste.newman.core.widgets.WidgetPicker
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T

/**
 * Choosing what a widget (several devices, in order) or a Quick Settings tile (one device) shows:
 * every device a widget can show (GET /api/widgets/devices), by room or by type, with search. A widget takes
 * up to [max] devices.
 */
@Composable
fun DevicePicker(
    title: String,
    devices: List<WidgetChoice>?,
    error: String?,
    multi: Boolean,
    initial: List<String>,
    initialConfirm: Boolean,
    /** At most this many can be ticked (a widget: what its largest size shows). */
    max: Int = Int.MAX_VALUE,
    /** How many the widget shows at its size now: the rest are marked as shown once it's bigger. */
    fits: Int = Int.MAX_VALUE,
    onSave: (ids: List<String>, confirmSensitive: Boolean) -> Unit,
    onCancel: () -> Unit,
) {
    var selected by remember(initial) { mutableStateOf(initial) }
    var confirm by rememberSaveable { mutableStateOf(initialConfirm) }
    var query by rememberSaveable { mutableStateOf("") }
    var byType by rememberSaveable { mutableStateOf(false) }
    val known = devices?.associateBy { it.id } ?: emptyMap()

    Column(Modifier.fillMaxSize().background(N.Night).systemBarsPadding().padding(horizontal = 16.dp)) {
        Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(title, style = T.title, modifier = Modifier.weight(1f))
            TextButton(onClick = onCancel) { Text("Cancel", style = T.body.copy(color = N.Muted)) }
        }
        if (multi) {
            Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(N.Glass).clickable { confirm = !confirm }
                .padding(horizontal = 14.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Confirm first for sensitive actions", style = T.body, modifier = Modifier.weight(1f))
                Switch(checked = confirm, onCheckedChange = { confirm = it },
                    colors = SwitchDefaults.colors(checkedTrackColor = N.Teal, checkedThumbColor = N.Night))
            }
            Spacer(Modifier.size(10.dp))
        }
        OutlinedTextField(value = query, onValueChange = { query = it }, singleLine = true,
            placeholder = { Text("Search devices", style = T.body.copy(color = N.Muted)) },
            textStyle = T.body, modifier = Modifier.fillMaxWidth())
        Row(Modifier.padding(vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            for ((label, type) in listOf("By room" to false, "By type" to true)) {
                FilterChip(selected = byType == type, onClick = { byType = type }, label = { Text(label, style = T.small) },
                    colors = FilterChipDefaults.filterChipColors(selectedContainerColor = N.TealBg, selectedLabelColor = N.Text,
                        labelColor = N.Muted))
            }
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            when {
                devices == null && error == null -> CircularProgressIndicator(Modifier.align(Alignment.Center), color = N.Teal)
                devices == null -> Text(error ?: "", style = T.body.copy(color = N.CoralText), modifier = Modifier.align(Alignment.Center))
                else -> {
                    val groups = WidgetPicker.group(devices, if (byType) WidgetGrouping.TYPE else WidgetGrouping.AREA, query)
                    LazyColumn(Modifier.fillMaxSize()) {
                        if (multi && selected.isNotEmpty() && query.isBlank()) {
                            item { Header(if (max == Int.MAX_VALUE) "On this widget" else "On this widget · ${selected.size} of $max") }
                            itemsIndexed(selected, key = { _, id -> "sel:$id" }) { i, id ->
                                SelectedRow(known[id]?.name ?: id, hidden = i >= fits, first = i == 0, last = i == selected.lastIndex,
                                    onUp = { selected = WidgetPicker.move(selected, i, -1) },
                                    onDown = { selected = WidgetPicker.move(selected, i, 1) },
                                    onRemove = { selected = WidgetPicker.toggle(selected, id, max) })
                            }
                        }
                        if (!multi && query.isBlank()) {
                            item { ChoiceRow("Nothing", null, selected.isEmpty(), multi = false) { selected = emptyList() } }
                        }
                        for ((group, list) in groups) {
                            item(key = "h:$group") { Header(group) }
                            items(list, key = { "d:${group}:${it.id}" }) { d ->
                                val sub = if (byType) d.area else d.kindName
                                val ticked = d.id in selected
                                ChoiceRow(d.name, sub, ticked, multi, enabled = ticked || selected.size < max) {
                                    selected = if (multi) WidgetPicker.toggle(selected, d.id, max) else listOf(d.id)
                                }
                            }
                        }
                        if (groups.isEmpty()) item { Header(if (query.isBlank()) "No devices" else "Nothing matches") }
                    }
                }
            }
        }
        Button(onClick = { onSave(selected, confirm) }, enabled = devices != null,
            colors = ButtonDefaults.buttonColors(containerColor = N.Teal, contentColor = N.Night),
            modifier = Modifier.fillMaxWidth().padding(vertical = 12.dp)) {
            Text("Save", style = T.label.copy(color = N.Night))
        }
    }
}

@Composable
private fun Header(text: String) {
    Text(text.uppercase(), style = T.caps, modifier = Modifier.padding(top = 14.dp, bottom = 6.dp))
}

@Composable
private fun ChoiceRow(name: String, sub: String?, checked: Boolean, multi: Boolean, enabled: Boolean = true, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).clickable(enabled = enabled, onClick = onClick).padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically) {
        if (multi) Checkbox(checked = checked, onCheckedChange = { onClick() }, enabled = enabled,
            colors = CheckboxDefaults.colors(checkedColor = N.Teal, checkmarkColor = N.Night, uncheckedColor = N.Muted))
        else RadioButton(selected = checked, onClick = onClick,
            colors = RadioButtonDefaults.colors(selectedColor = N.Teal, unselectedColor = N.Muted))
        Column(Modifier.weight(1f)) {
            Text(name, style = if (enabled) T.body else T.body.copy(color = N.Muted))
            if (!sub.isNullOrEmpty()) Text(sub, style = T.tiny)
        }
    }
}

@Composable
private fun SelectedRow(name: String, hidden: Boolean, first: Boolean, last: Boolean, onUp: () -> Unit, onDown: () -> Unit, onRemove: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp).clip(RoundedCornerShape(12.dp)).background(N.Glass)
        .padding(start = 14.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(name, style = T.body)
            if (hidden) Text("Shown when the widget is bigger", style = T.tiny)
        }
        IconButton(onClick = onUp, enabled = !first) { NIcon("up", size = 18.dp, color = if (first) N.Border else N.Soft) }
        IconButton(onClick = onDown, enabled = !last) { NIcon("down", size = 18.dp, color = if (last) N.Border else N.Soft) }
        IconButton(onClick = onRemove) { NIcon("x", size = 18.dp, color = N.Soft) }
        Spacer(Modifier.width(2.dp))
    }
}
