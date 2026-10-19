package ca.nimaste.newman.ui.tiles

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.selection.selectable
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.setProgress
import androidx.compose.ui.semantics.stateDescription
import ca.nimaste.newman.core.model.Spoken
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.model.Command
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.Kind
import ca.nimaste.newman.core.model.Property
import ca.nimaste.newman.core.model.Tile
import ca.nimaste.newman.core.model.asNumberOrNull
import ca.nimaste.newman.core.model.asStringOrNull
import ca.nimaste.newman.core.model.jsonOf
import ca.nimaste.newman.core.model.string
import ca.nimaste.newman.ui.common.GlassButton
import ca.nimaste.newman.ui.common.GlassCard
import ca.nimaste.newman.ui.common.LocalFillHeight
import androidx.compose.foundation.layout.PaddingValues
import ca.nimaste.newman.ui.rememberRunner
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.Sora
import ca.nimaste.newman.ui.theme.T
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.util.Locale
import kotlin.math.PI
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * The round dial (TabletClimate / PhoneClimate) for any "number" command - a thermostat's target.
 * Range, step and unit come from the command in /api/meta; the value from attrs[command.attr];
 * modes from a "state" command. Colours by mode id, as the web dial does.
 */
internal val MODE_COLOR = mapOf("heat" to Color(0xFFFF8A5B), "cool" to Color(0xFF5AA9FF), "auto" to Color(0xFF3CCFB4),
    "heat_cool" to Color(0xFF3CCFB4), "off" to Color(0xFF6B7599), "eco" to Color(0xFF3CCFB4), "fan_only" to Color(0xFFA9B4D6))
private val MODE_WORD = mapOf("heat" to "Heat to", "cool" to "Cool to", "auto" to "Keep at", "heat_cool" to "Keep at")

class DialModel(val e: Entity, val k: Kind?, val cmd: Command, val modeCmd: Command?) {
    val min = cmd.min ?: 0.0
    val max = cmd.max ?: 100.0
    val step = cmd.step ?: 1.0
    val unit = cmd.unit
    val current: Double? = e.attrs[cmd.attr].asNumberOrNull
    val modes = if (modeCmd != null) HubModel.stateChoices(k) else emptyList()
    val mode: String? = if (modeCmd != null) e.state else null
    val off = mode == "off"
    val color: Color = mode?.let { MODE_COLOR[it] } ?: N.Neutral
    val word: String = mode?.let { MODE_WORD[it] } ?: if (off) "Off" else cmd.label
    private val props: List<Property> = e.properties
    val nowProp = props.firstOrNull { it.type == "number" && it.unit == unit && it.attr != cmd.attr }
    val pctProp = props.firstOrNull { it.type == "number" && it.unit == "%" && it.attr != cmd.attr }
    private val activity = props.firstOrNull { it.type == "text" && it.options.isNotEmpty() && it.attr.isNotEmpty() }
    /** What it's doing now ("idle", "heating"), when it says. */
    val activityText: String? = activity?.value?.asStringOrNull?.takeIf { it.isNotEmpty() }?.let { HubModel.formatProp(activity, activity.value).lowercase() }
    /** The other readings in the dial's unit (an auto range's two ends), lowest first. */
    val otherSetpoints: List<Double> = props.filter { it.type == "number" && it.unit == unit && it.attr != cmd.attr && it !== nowProp }.mapNotNull { it.value.asNumberOrNull }.sorted()
    val status: String = listOfNotNull(mode?.let { HubModel.stateLabel(e, k) },
        activity?.value?.asStringOrNull?.takeIf { it.isNotEmpty() }?.let { HubModel.formatProp(activity, activity.value).lowercase() })
        .joinToString(" · ")
    fun nowLine(compact: Boolean): String = listOfNotNull(
        nowProp?.let { "Now ${HubModel.formatProp(it, it.value)}" },
        pctProp?.let { if (compact) HubModel.formatProp(it, it.value) else "${it.label} ${HubModel.formatProp(it, it.value)}" },
    ).joinToString(" · ")
    private val decimals = step.toString().substringAfter('.', "").trimEnd('0').length
    fun clamp(v: Double) = (Math.round(v / step) * step).coerceIn(min, max)
    fun text(v: Double?): String = if (off || v == null) "—" else String.format(Locale.getDefault(), "%.${decimals}f", v) + if (unit.startsWith("°")) "°" else ""
    fun angle(v: Double) = 135.0 + ((clamp(v) - min) / (max - min).coerceAtLeast(1e-9)) * 270.0

    companion object {
        fun of(tile: Tile, e: Entity?, k: Kind?): DialModel? {
            e ?: return null
            val cmd = k?.command(tile.config.string("command"))
                ?: k?.commands?.firstOrNull { it.valueType == "number" } ?: return null
            val mode = tile.config.string("modeCommand")?.let { k?.command(it) }
            return DialModel(e, k, cmd, mode)
        }
    }
}

/** The ring itself; [onDrag] null = not draggable (the small overview dial). */
@Composable
fun DialRing(m: DialModel, shown: Double?, size: Dp, stroke: Dp, onDrag: ((Double, Boolean) -> Unit)?, center: @Composable () -> Unit) {
    val latestDrag = androidx.compose.runtime.rememberUpdatedState(onDrag)
    val latestModel = androidx.compose.runtime.rememberUpdatedState(m)
    Box(Modifier.size(size), contentAlignment = Alignment.Center) {
        Canvas(
            Modifier.size(size).then(
                if (onDrag == null || m.off) Modifier
                else Modifier.pointerInput(m.e.id) {
                    fun valueAt(p: Offset): Double {
                        val c = Offset(this.size.width / 2f, this.size.height / 2f)
                        var a = Math.toDegrees(atan2((p.y - c.y).toDouble(), (p.x - c.x).toDouble()))
                        if (a < 0) a += 360.0
                        if (a < 135) a += 360.0
                        if (a > 405) a = if (a > 450) 135.0 else 405.0
                        val lm = latestModel.value
                        return lm.clamp(lm.min + ((a - 135) / 270.0) * (lm.max - lm.min))
                    }
                    awaitEachGesture {
                        val down = awaitFirstDown()
                        latestDrag.value?.invoke(valueAt(down.position), false)
                        while (true) {
                            val ev = awaitPointerEvent()
                            val ch = ev.changes.firstOrNull() ?: break
                            if (!ch.pressed) {
                                latestDrag.value?.invoke(valueAt(ch.position), true)
                                break
                            }
                            ch.consume()
                            latestDrag.value?.invoke(valueAt(ch.position), false)
                        }
                    }
                },
            ),
        ) {
            val sw = stroke.toPx()
            val inset = sw / 2 + 2
            val arcSize = Size(this.size.width - inset * 2, this.size.height - inset * 2)
            val tl = Offset(inset, inset)
            drawArc(Color(0x1AFFFFFF), 135f, 270f, false, tl, arcSize, style = Stroke(sw, cap = StrokeCap.Round))
            if (!m.off && shown != null) {
                val end = m.angle(shown)
                drawArc(m.color, 135f, (end - 135).toFloat().coerceAtLeast(0.1f), false, tl, arcSize, style = Stroke(sw, cap = StrokeCap.Round))
                val r = arcSize.width / 2
                val c = Offset(this.size.width / 2, this.size.height / 2)
                val rad = end * PI / 180
                val k = Offset(c.x + (r * cos(rad)).toFloat(), c.y + (r * sin(rad)).toFloat())
                drawCircle(N.Text, sw * 0.72f, k)
                if (onDrag != null) drawCircle(m.color, sw * 0.3f, k)
                val now = m.nowProp?.value.asNumberOrNull
                if (now != null && onDrag != null) {
                    val nr = m.angle(now) * PI / 180
                    drawCircle(N.Text.copy(alpha = 0.7f), 5.dp.toPx(), Offset(c.x + (r * cos(nr)).toFloat(), c.y + (r * sin(nr)).toFloat()))
                }
            }
        }
        center()
    }
}

/** State of a dial being changed: shows the target at once, sends one command ~600 ms after the last change. */
@Composable
internal fun rememberDialTarget(m: DialModel): Triple<Double?, (Double, Boolean) -> Unit, (Double) -> Unit> {
    val run = rememberRunner()
    val scope = rememberCoroutineScope()
    var target by remember(m.e.id) { mutableStateOf(m.current) }
    var pending by remember { mutableStateOf<Job?>(null) }
    var dragging by remember { mutableStateOf(false) }
    LaunchedEffect(m.current) { if (!dragging && pending?.isActive != true) target = m.current }
    val commit: (Double) -> Unit = { v ->
        pending?.cancel()
        pending = scope.launch {
            delay(600)
            if (v != m.current) run.run(m.e, m.cmd, jsonOf(v))
        }
    }
    val drag: (Double, Boolean) -> Unit = { v, done ->
        dragging = !done
        target = v
        if (done) commit(v)
    }
    val bump: (Double) -> Unit = { d ->
        val base = target ?: m.current
        if (!m.off && base != null) {
            val v = m.clamp(base + d)
            target = v
            commit(v)
        }
    }
    return Triple(target, drag, bump)
}

/** The half-width overview dial: tap for the full dial. */
@Composable
fun CompactDial(m: DialModel, onOpen: () -> Unit, textScale: Float) {
    GlassCard(Modifier.fillMaxWidth().height(172.dp).semantics(mergeDescendants = true) {
        contentDescription = Spoken.of(m.e.name, m.mode?.let { HubModel.stateLabel(m.e, m.k) }, m.text(m.current).takeIf { !m.off },
            m.nowLine(false).ifEmpty { null })
    }, padding = androidx.compose.foundation.layout.PaddingValues(12.dp), onClick = onOpen) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(m.e.name, style = T.small.copy(fontSize = 13.sp * textScale, fontWeight = androidx.compose.ui.text.font.FontWeight.ExtraBold),
                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            Text(m.mode?.let { HubModel.stateLabel(m.e, m.k) } ?: "", style = T.small.copy(color = m.color))
        }
        Box(Modifier.fillMaxWidth().padding(top = 4.dp), contentAlignment = Alignment.Center) {
            DialRing(m, m.current, 124.dp, 14.dp, onDrag = null) {
                Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(top = 8.dp)) {
                    Text(m.text(m.current), style = T.number.copy(fontSize = 30.sp * textScale))
                    val now = m.nowProp?.let { "Now ${HubModel.formatProp(it, it.value).replace(" °C", "°").replace(" °F", "°")}" }
                    if (now != null) Text(now, style = T.tiny.copy(fontSize = 11.sp))
                }
            }
        }
    }
}

/** The full dial (PhoneClimate board): drag the ring or use −/+, mode chips below. */
@Composable
fun FullDial(m: DialModel, showHeader: Boolean = true) {
    val run = rememberRunner()
    val (target, drag, bump) = rememberDialTarget(m)
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(14.dp)) {
        if (showHeader) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(m.e.name, style = T.title, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(m.status, style = T.small.copy(color = m.color))
            }
        }
        // TalkBack: "Living room target, 21.5°" - adjustable (swipe up / down, or set a value).
        Box(Modifier.semantics(mergeDescendants = true) {
            contentDescription = "${m.e.name} ${Spoken.phrase(m.cmd.label)}"
            stateDescription = listOfNotNull(m.text(target), m.nowLine(false).ifEmpty { null }).joinToString(", ")
            if (!m.off) {
                progressBarRangeInfo = ProgressBarRangeInfo((target ?: m.min).toFloat().coerceIn(m.min.toFloat(), m.max.toFloat()),
                    m.min.toFloat()..m.max.toFloat(), steps = (((m.max - m.min) / m.step).toInt() - 1).coerceIn(0, 200))
                setProgress { v -> drag(m.clamp(v.toDouble()), true); true }
            }
        }) {
        DialRing(m, target, 280.dp, 24.dp, onDrag = drag) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(m.word.uppercase(), style = T.caps.copy(color = m.color, letterSpacing = 1.5.sp))
                Text(m.text(target), style = T.number.copy(fontFamily = Sora, fontSize = 60.sp, letterSpacing = (-2).sp))
                val now = m.nowLine(false)
                if (now.isNotEmpty()) Text(now, style = T.small.copy(color = N.Muted))
            }
        }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(40.dp)) {
            RoundButton("−", "Lower ${Spoken.phrase(m.cmd.label)}", enabled = !m.off) { bump(-m.step) }
            RoundButton("+", "Raise ${Spoken.phrase(m.cmd.label)}", enabled = !m.off) { bump(m.step) }
        }
        if (m.modes.isNotEmpty() && m.modeCmd != null) {
            val shape = RoundedCornerShape(18.dp)
            Row(Modifier.fillMaxWidth().background(Color(0x0FFFFFFF), shape).padding(5.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                m.modes.forEach { s ->
                    val on = s.id == m.mode
                    val c = MODE_COLOR[s.id] ?: N.Neutral
                    Box(
                        Modifier.weight(1f).height(48.dp).clip(RoundedCornerShape(14.dp))
                            .background(if (on) c else Color.Transparent)
                            .selectable(selected = on, enabled = !on && m.e.available, role = Role.RadioButton) { run.run(m.e, m.modeCmd, jsonOf(s.id)) },
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(s.label, style = T.label.copy(fontSize = 14.sp, color = if (on) N.Night else N.Text), maxLines = 1)
                    }
                }
            }
        }
    }
}

@Composable
private fun RoundButton(text: String, label: String, enabled: Boolean, onClick: () -> Unit) {
    Box(
        Modifier.size(64.dp).clip(CircleShape).background(Color(0x0FFFFFFF)).border(1.dp, N.BorderHi, CircleShape)
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick).clearAndSetSemantics { contentDescription = label; role = Role.Button },
        contentAlignment = Alignment.Center,
    ) {
        Text(text, style = T.number.copy(fontSize = 30.sp, color = if (enabled) N.Text else N.TabOff))
    }
}

/**
 * A full-width but short dial tile (the Overview's thermostat): the ring on the left (tap it for
 * the full dial), −/+ and the device's modes on the right; the name and what it's doing on top.
 */
@Composable
fun HorizontalDial(m: DialModel, onOpen: () -> Unit, textScale: Float) {
    val run = rememberRunner()
    val fill = LocalFillHeight.current // read before the card takes it
    val (target, _, bump) = rememberDialTarget(m)
    val ring = if (fill == Dp.Unspecified) 140.dp else (fill - 74.dp).coerceIn(56.dp, 140.dp) // as big as the row allows, 140 at most
    GlassCard(Modifier.fillMaxWidth(), padding = PaddingValues(14.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(m.e.name.uppercase(), style = T.caps, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            if (m.status.isNotEmpty()) {
                Text(m.status, style = T.tiny.copy(color = m.color, fontWeight = androidx.compose.ui.text.font.FontWeight.ExtraBold), maxLines = 1,
                    modifier = Modifier.background(m.color.copy(alpha = 0.16f), RoundedCornerShape(99.dp)).padding(horizontal = 10.dp, vertical = 4.dp))
            }
        }
        Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            Box(Modifier.clip(CircleShape).clickable(onClickLabel = "open the full dial", role = Role.Button, onClick = onOpen)
                .semantics(mergeDescendants = true) {
                    contentDescription = Spoken.of(m.e.name, m.text(target).takeIf { !m.off }, m.nowLine(false).ifEmpty { null })
                }) {
                DialRing(m, target, ring, 12.dp, onDrag = null) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(top = 6.dp)) {
                        // Grows and shrinks with the ring.
                        Text(m.text(target), style = T.number.copy(fontSize = (ring.value * 0.19f).sp * textScale), maxLines = 1)
                        val now = m.nowProp?.let { "Now ${HubModel.formatProp(it, it.value).replace(" °C", "°").replace(" °F", "°")}" }
                        if (now != null) Text(now, style = T.tiny.copy(fontSize = (ring.value * 0.08f).coerceAtLeast(9f).sp), maxLines = 1)
                    }
                }
            }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    GlassButton("−", Modifier.weight(1f).semantics { contentDescription = "Lower ${Spoken.phrase(m.cmd.label)}" },
                        enabled = !m.off, height = 44.dp, style = T.number.copy(fontSize = 22.sp)) { bump(-m.step) }
                    GlassButton("+", Modifier.weight(1f).semantics { contentDescription = "Raise ${Spoken.phrase(m.cmd.label)}" },
                        enabled = !m.off, height = 44.dp, style = T.number.copy(fontSize = 22.sp)) { bump(m.step) }
                }
                if (m.modes.isNotEmpty() && m.modeCmd != null) {
                    val shape = RoundedCornerShape(14.dp)
                    Row(Modifier.fillMaxWidth().background(Color(0x0FFFFFFF), shape).padding(3.dp), horizontalArrangement = Arrangement.spacedBy(3.dp)) {
                        m.modes.forEach { s ->
                            val on = s.id == m.mode
                            val c = MODE_COLOR[s.id] ?: N.Neutral
                            Box(
                                Modifier.weight(1f).height(36.dp).clip(RoundedCornerShape(11.dp))
                                    .background(if (on) c else Color.Transparent)
                                    .selectable(selected = on, enabled = !on && m.e.available, role = Role.RadioButton) { run.run(m.e, m.modeCmd, jsonOf(s.id)) },
                                contentAlignment = Alignment.Center,
                            ) {
                                Text(s.label, style = T.small.copy(fontSize = 12.sp, color = if (on) N.Night else N.Text), maxLines = 1, overflow = TextOverflow.Ellipsis)
                            }
                        }
                    }
                }
            }
        }
    }
}

/** A wide dial tile on a page (the Climate page). */
@Composable
fun WideDialTile(m: DialModel) {
    GlassCard(Modifier.fillMaxWidth()) {
        FullDial(m)
        Spacer(Modifier.width(1.dp))
    }
}

fun Double.roundTo(step: Double) = (this / step).roundToInt() * step
