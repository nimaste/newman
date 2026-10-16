package ca.nimaste.newman.ui.tiles

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.model.Command
import ca.nimaste.newman.core.model.PhoneGrid
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.Kind
import ca.nimaste.newman.core.model.Tile
import ca.nimaste.newman.core.model.asNumberOrNull
import ca.nimaste.newman.core.model.jsonOf
import ca.nimaste.newman.core.model.string
import ca.nimaste.newman.core.model.strings
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.CardHeader
import ca.nimaste.newman.ui.common.GlassButton
import ca.nimaste.newman.ui.common.GlassCard
import ca.nimaste.newman.ui.common.IconBubble
import ca.nimaste.newman.ui.common.tapAndHold
import ca.nimaste.newman.ui.common.IconAction
import ca.nimaste.newman.ui.common.spoken
import ca.nimaste.newman.ui.common.spokenDevice
import ca.nimaste.newman.core.model.Spoken
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.setProgress
import androidx.compose.ui.semantics.stateDescription
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.rememberRunner
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import kotlin.math.roundToInt

/** Colours for a device: amber when a non-security device is on, coral/teal for security states. */
private data class Look(val bg: Color, val fg: Color, val stateColor: Color)

/** What TalkBack says a tap on a device tile does: its action ("turn off"), else showing its controls. */
private fun tapLabel(action: Command?, e: Entity, busy: Boolean): String =
    if (action != null && e.available && !busy) Spoken.phrase(action.label) else SHOW_ALL

private const val SHOW_ALL = "show all controls"

private fun look(e: Entity, k: Kind?): Look {
    val lit = HubModel.isActive(e, k) && k?.secureState == null && e.available
    val tone = HubModel.stateTone(e, k, HubModel.vitals(e).problem)
    return when {
        lit -> Look(N.Amber, N.AmberText, N.AmberText.copy(alpha = 0.8f))
        tone == "warn" -> Look(N.Glass, N.Text, N.CoralText)
        tone == "calm" -> Look(N.Glass, N.Text, N.Teal)
        else -> Look(N.Glass, N.Text, N.Text.copy(alpha = 0.8f))
    }
}

private fun stateLine(e: Entity, k: Kind?, withArea: Boolean = false): String {
    val v = HubModel.vitals(e)
    val parts = listOfNotNull(
        if (withArea) e.attrs.string("area")?.takeIf { it.isNotBlank() } else null,
        HubModel.entityStateText(e, k).ifEmpty { null },
        v.battery?.let { "$it%" },
        v.problem,
    )
    return parts.joinToString(" · ")
}

/** One device of a "devices" tile, half width (PhoneOverview's shortcuts): tap = its main action. */
@Composable
fun DeviceCard(e: Entity, k: Kind?, onSheet: () -> Unit, textScale: Float = 1f) {
    val run = rememberRunner()
    val busy by LocalApp.current.busy.collectAsState()
    val action = HubModel.tapAction(e, k)
    val l = look(e, k)
    val shape = RoundedCornerShape(20.dp)
    Column(
        Modifier.fillMaxWidth().height(92.dp).clip(shape).background(l.bg, shape)
            .spoken(spokenDevice(e, k, e.id in busy))
            .tapAndHold(onTap = { if (action != null && e.available && e.id !in busy) run.run(e, action) else onSheet() }, onHold = onSheet,
                tapLabel = tapLabel(action, e, e.id in busy), holdLabel = SHOW_ALL)
            .padding(12.dp),
        verticalArrangement = Arrangement.SpaceBetween,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            NIcon(HubModel.kindIcon(k), size = 24.dp, color = l.fg)
            Spacer(Modifier.weight(1f))
            if (e.id in busy) CircularProgressIndicator(Modifier.size(14.dp), color = l.fg, strokeWidth = 2.dp)
            else if (action != null && LocalApp.current.needsConfirmation(e, action)) NIcon("fingerprint", size = 16.dp, color = l.fg.copy(alpha = 0.7f))
        }
        Column {
            Text(e.name, style = T.label.copy(fontSize = 14.sp * textScale, color = l.fg), maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(stateLine(e, k), style = T.tiny.copy(fontSize = 12.sp * textScale, color = l.stateColor), maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

/** A full-width device card with a brightness bar (the PhoneLights board). */
@Composable
fun WideDeviceCard(e: Entity, k: Kind?, onSheet: () -> Unit, textScale: Float = 1f) {
    val run = rememberRunner()
    val action = HubModel.tapAction(e, k)
    val l = look(e, k)
    val pct = HubModel.percentCommand(e, k)
    val shape = RoundedCornerShape(20.dp)
    Column(
        Modifier.fillMaxWidth().clip(shape).background(l.bg, shape).padding(horizontal = 14.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Row(
            Modifier.fillMaxWidth().spoken(spokenDevice(e, k, withArea = e.attrs.string("area")))
                .tapAndHold(onTap = { if (action != null && e.available) run.run(e, action) else onSheet() }, onHold = onSheet,
                    tapLabel = tapLabel(action, e, false), holdLabel = SHOW_ALL),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            IconBubble(HubModel.kindIcon(k), l.fg.copy(alpha = 0.12f), l.fg, size = 44.dp, iconSize = 22.dp)
            Column(Modifier.weight(1f)) {
                Text(e.name, style = T.label.copy(fontSize = 16.sp * textScale, color = l.fg), maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(stateLine(e, k, withArea = true), style = T.tiny.copy(fontSize = 12.sp * textScale, color = l.stateColor), maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            if (HubModel.extraActions(k, action).isNotEmpty()) {
                IconAction("more", "All controls for ${e.name}", size = 44.dp, color = l.fg, shape = RoundedCornerShape(12.dp), stroke = 3f, onClick = onSheet)
            }
        }
        if (pct != null) {
            val lit = HubModel.isActive(e, k)
            ValueBar(e, pct, lit, l.fg)
        }
    }
}

/** A 40dp bar you drag or tap to set a percent value; sends once, on release. */
@Composable
fun ValueBar(e: Entity, cmd: Command, lit: Boolean, fg: Color) {
    val run = rememberRunner()
    val min = (cmd.min ?: 0.0).toFloat()
    val max = (cmd.max ?: 100.0).toFloat()
    val current = HubModel.commandCurrent(e, cmd).asNumberOrNull?.toFloat()
    var dragging by remember { mutableStateOf(false) }
    var value by remember { mutableFloatStateOf(current ?: (cmd.default.asNumberOrNull?.toFloat() ?: 50f)) }
    LaunchedEffect(current) { if (!dragging && current != null) value = current }
    val shown = if (lit || dragging) ((value - min) / (max - min).coerceAtLeast(1f)).coerceIn(0f, 1f) else 0f
    val shape = RoundedCornerShape(12.dp)
    BoxWithConstraints(
        Modifier.fillMaxWidth().height(40.dp).clip(shape).background(fg.copy(alpha = 0.14f), shape),
    ) {
        val widthPx = constraints.maxWidth.toFloat().coerceAtLeast(1f)
        fun at(x: Float) = (min + (x / widthPx).coerceIn(0f, 1f) * (max - min)).let { v ->
            val step = (cmd.step ?: 1.0).toFloat().coerceAtLeast(0.01f)
            (v / step).roundToInt() * step
        }
        fun commit() {
            dragging = false
            run.run(e, cmd, jsonOf(if (value % 1f == 0f) value.roundToInt() else value))
        }
        Box(Modifier.fillMaxHeight().fillMaxWidth(shown).background(if (fg == N.AmberText) N.AmberText.copy(alpha = 0.35f) else N.Amber, shape))
        Box(
            Modifier.fillMaxWidth().fillMaxHeight()
                .pointerInput(e.id, cmd.id) {
                    detectHorizontalDragGestures(
                        onDragStart = { dragging = true; value = at(it.x) },
                        onDragEnd = { commit() },
                        onDragCancel = { dragging = false },
                    ) { change, _ -> value = at(change.position.x) }
                }
                .pointerInput(e.id, "tap") { detectTapGestures { value = at(it.x); commit() } }
                // TalkBack: an adjustable "Kitchen brightness, 60%" (swipe up / down, or set a value).
                .semantics {
                    contentDescription = "${e.name} ${Spoken.phrase(cmd.label)}"
                    stateDescription = if (lit || dragging) HubModel.valueText(cmd, null, jsonOf(value.roundToInt())) else "off"
                    progressBarRangeInfo = ProgressBarRangeInfo(value.coerceIn(min, max), min..max,
                        steps = (((max - min) / (cmd.step ?: 1.0).toFloat()).toInt() - 1).coerceIn(0, 100))
                    setProgress { v -> value = v.coerceIn(min, max); commit(); true }
                },
        )
        Text(
            if (lit || dragging) HubModel.valueText(cmd, null, jsonOf(value.roundToInt())) else "Off",
            style = T.small.copy(color = fg.copy(alpha = 0.85f)),
            modifier = Modifier.align(Alignment.CenterEnd).padding(end = 12.dp),
        )
    }
}

/** A "devices" tile in the list look (PhoneSecurity's locks): name, state and the next action. */
@Composable
fun DeviceListTile(tile: Tile, entities: Map<String, Entity>, kind: (Entity) -> Kind?, onSheet: (Entity) -> Unit, textScale: Float) {
    val byArea = tile.config.string("groupBy") == "area"
    var list = tile.config.strings("entities").mapNotNull { entities[it] }
    if (byArea) list = list.sortedBy { it.attrs.string("area") ?: "~" }
    GlassCard(Modifier.fillMaxWidth(), padding = PaddingValues(horizontal = 14.dp, vertical = 4.dp)) {
        tile.config.string("title")?.takeIf { it.isNotBlank() }?.let {
            CardHeader(it, Modifier.padding(top = 12.dp, bottom = 2.dp))
        }
        if (list.isEmpty()) Text("No devices chosen.", style = T.tiny, modifier = Modifier.padding(vertical = 12.dp))
        list.forEachIndexed { i, e -> DeviceRow(e, kind(e), last = i == list.lastIndex, showArea = byArea, onSheet = { onSheet(e) }, textScale = textScale) }
    }
}

/**
 * A "devices" tile placed at its own size by a phone layout: two columns of device buttons when
 * the tile is wide enough, a list when narrow (PhoneGrid.fitDevices). Devices that don't fit
 * become a "+N more" button ([onMore] shows them all) - the tile itself never scrolls.
 */
@Composable
fun DevicesGridTile(tile: Tile, width: Dp, height: Dp, entities: Map<String, Entity>, kind: (Entity) -> Kind?,
                    onSheet: (Entity) -> Unit, onMore: () -> Unit, textScale: Float) {
    var list = tile.config.strings("entities").mapNotNull { entities[it] }
    if (tile.config.string("groupBy") == "area") list = list.sortedBy { it.attrs.string("area") ?: "~" }
    // The card's heading: its title, else "Quick controls" for a grid of two or more devices across the phone.
    val title = tile.config.string("title")?.takeIf { it.isNotBlank() }
        ?: if (tile.config.string("style") != "list" && list.size > 1 && width.value >= PhoneGrid.TWO_COLUMNS_FROM) "Quick controls" else null
    val fit = PhoneGrid.fitDevices(list.size, width.value, height.value, title != null, list = tile.config.string("style") == "list")
    GlassCard(Modifier.fillMaxSize(), padding = PaddingValues(PhoneGrid.DEVICE_PADDING.dp)) {
        title?.let {
            CardHeader(it, Modifier.height(PhoneGrid.DEVICE_TITLE.dp).padding(start = 4.dp, end = 4.dp))
        }
        if (list.isEmpty()) Text("No devices chosen.", style = T.tiny, modifier = Modifier.padding(4.dp))
        val slots: List<Entity?> = list.take(fit.shown) + (if (fit.more > 0) listOf(null) else emptyList())
        Column(verticalArrangement = Arrangement.spacedBy(PhoneGrid.DEVICE_GAP.dp)) {
            slots.chunked(fit.columns).forEach { row ->
                Row(Modifier.fillMaxWidth().height(PhoneGrid.DEVICE_ROW.dp), horizontalArrangement = Arrangement.spacedBy(PhoneGrid.DEVICE_GAP.dp)) {
                    row.forEach { e ->
                        Box(Modifier.weight(1f).fillMaxHeight()) {
                            if (e == null) MoreButton(fit.more, onMore)
                            else DeviceButton(e, kind(e), onSheet = { onSheet(e) }, textScale = textScale)
                        }
                    }
                    repeat(fit.columns - row.size) { Spacer(Modifier.weight(1f)) }
                }
            }
        }
    }
}

/** One device inside a placed devices tile: tap = its main action, hold = everything it can do. */
@Composable
fun DeviceButton(e: Entity, k: Kind?, onSheet: () -> Unit, textScale: Float = 1f) {
    val run = rememberRunner()
    val app = LocalApp.current
    val busy by app.busy.collectAsState()
    val action = HubModel.tapAction(e, k)
    val l = look(e, k)
    val shape = RoundedCornerShape(16.dp)
    Row(
        Modifier.fillMaxSize().clip(shape).background(l.bg, shape)
            .spoken(spokenDevice(e, k, e.id in busy))
            .tapAndHold(onTap = { if (action != null && e.available && e.id !in busy) run.run(e, action) else onSheet() }, onHold = onSheet,
                tapLabel = tapLabel(action, e, e.id in busy), holdLabel = SHOW_ALL)
            .padding(horizontal = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (e.id in busy) CircularProgressIndicator(Modifier.size(20.dp), color = l.fg, strokeWidth = 2.dp)
        else NIcon(HubModel.kindIcon(k), size = 22.dp, color = l.fg)
        Column(Modifier.weight(1f)) {
            Text(e.name, style = T.label.copy(fontSize = 14.sp * textScale, color = l.fg), maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(stateLine(e, k), style = T.tiny.copy(fontSize = 12.sp * textScale, color = l.stateColor), maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        if (action != null && e.id !in busy && app.needsConfirmation(e, action)) NIcon("fingerprint", size = 16.dp, color = l.fg.copy(alpha = 0.7f))
    }
}

@Composable
private fun MoreButton(more: Int, onClick: () -> Unit) {
    val shape = RoundedCornerShape(16.dp)
    Box(
        Modifier.fillMaxSize().clip(shape).background(N.Glass, shape).clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text("+$more more", style = T.label.copy(fontSize = 14.sp), maxLines = 1)
    }
}

@Composable
fun DeviceRow(e: Entity, k: Kind?, last: Boolean, showArea: Boolean, onSheet: () -> Unit, textScale: Float = 1f) {
    val run = rememberRunner()
    val app = LocalApp.current
    val busy by app.busy.collectAsState()
    val action = HubModel.nextAction(e, k)
    val v = HubModel.vitals(e)
    val tone = HubModel.stateTone(e, k, v.problem)
    val (bubbleBg, bubbleFg, stateColor) = when (tone) {
        "warn" -> Triple(N.CoralBg, N.Coral, N.CoralText)
        "calm" -> Triple(N.TealBg, N.Teal, N.Teal)
        else -> Triple(N.Glass, N.Text, N.Muted)
    }
    Column {
        Row(
            Modifier.fillMaxWidth().height(66.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(Modifier.weight(1f).fillMaxHeight().spoken(spokenDevice(e, k, e.id in busy, withArea = if (showArea) e.attrs.string("area") else null))
                .clickable(onClickLabel = SHOW_ALL, role = Role.Button, onClick = onSheet), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                IconBubble(HubModel.kindIcon(k), bubbleBg, bubbleFg)
                Column(Modifier.weight(1f)) {
                    Text(e.name, style = T.label.copy(fontSize = 15.sp * textScale), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(stateLine(e, k, withArea = showArea), style = T.small.copy(fontSize = 13.sp * textScale, color = stateColor),
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            if (action != null) {
                GlassButton(action.label, Modifier.width(104.dp).semantics { contentDescription = "${action.label} ${e.name}" }, icon = if (app.needsConfirmation(e, action)) "fingerprint" else null,
                    enabled = e.available, busy = e.id in busy) { run.run(e, action) }
            } else if (HubModel.extraActions(k, null).isNotEmpty() || k?.commands?.isNotEmpty() == true) {
                GlassButton("More", Modifier.semantics { contentDescription = "All controls for ${e.name}" }, onClick = onSheet)
            }
        }
        if (!last) Box(Modifier.fillMaxWidth().height(1.dp).background(N.Divider))
    }
}

/** The house mode (an alarm) with its buttons (PhoneSecurity's alarm card). */
@Composable
fun ModeTile(e: Entity?, k: Kind?, onSheet: () -> Unit) {
    val run = rememberRunner()
    val app = LocalApp.current
    val busy by app.busy.collectAsState()
    if (e == null) {
        GlassCard(Modifier.fillMaxWidth()) { Text("This device is missing.", style = T.tiny) }
        return
    }
    val cmds = HubModel.plainCommands(k).filter { it.confirmsTo != e.state }
    GlassCard(Modifier.fillMaxWidth(), onClick = onSheet) {
        Text(e.name, style = T.tiny.copy(fontWeight = androidx.compose.ui.text.font.FontWeight.ExtraBold))
        Text(if (!e.available) "Offline" else HubModel.stateLabel(e, k) ?: "", style = T.title)
        if (cmds.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            cmds.chunked(2).forEach { row ->
                Row(Modifier.fillMaxWidth().padding(bottom = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    row.forEach { c ->
                        GlassButton(c.label, Modifier.weight(1f), icon = if (app.needsConfirmation(e, c)) "fingerprint" else null,
                            height = 48.dp, enabled = e.available, busy = e.id in busy, style = T.label) { run.run(e, c) }
                    }
                    if (row.size == 1) Spacer(Modifier.weight(1f))
                }
            }
        }
    }
}
