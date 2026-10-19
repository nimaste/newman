package ca.nimaste.newman.ui.home

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import ca.nimaste.newman.ui.common.heading
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.theme.H
import ca.nimaste.newman.ui.theme.Manrope

/** The phone mockups' type: Manrope throughout, heavy titles. */
object HT {
    val title = TextStyle(fontFamily = Manrope, fontWeight = FontWeight.ExtraBold, fontSize = 30.sp, letterSpacing = (-0.5).sp, color = H.Text)
    val subTitle = TextStyle(fontFamily = Manrope, fontWeight = FontWeight.ExtraBold, fontSize = 28.sp, letterSpacing = (-0.5).sp, color = H.Text)
    val caps = TextStyle(fontFamily = Manrope, fontWeight = FontWeight.ExtraBold, fontSize = 12.sp, letterSpacing = 1.2.sp, color = H.Label)
    val name = TextStyle(fontFamily = Manrope, fontWeight = FontWeight.ExtraBold, fontSize = 15.sp, color = H.Text)
    val row = TextStyle(fontFamily = Manrope, fontWeight = FontWeight.Bold, fontSize = 15.sp, color = H.Text)
    val line = TextStyle(fontFamily = Manrope, fontWeight = FontWeight.Bold, fontSize = 13.sp, color = H.Sub)
    val small = TextStyle(fontFamily = Manrope, fontWeight = FontWeight.Bold, fontSize = 12.sp, color = H.Sub)
    val button = TextStyle(fontFamily = Manrope, fontWeight = FontWeight.ExtraBold, fontSize = 13.sp, color = H.Text)
}

/**
 * A screen of the new phone: "‹ Home" (when it has a parent), its title with a line under it and an
 * action on the right, then its cards in a list.
 */
@Composable
fun HScreen(
    title: String,
    back: String? = null,
    onBack: (() -> Unit)? = null,
    line: String? = null,
    lineColor: Color = H.Sub,
    trailing: (@Composable () -> Unit)? = null,
    big: Boolean = back == null,
    content: LazyListScope.() -> Unit,
) {
    LazyColumn(
        Modifier.fillMaxSize().statusBarsPadding(),
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = if (back == null) 18.dp else 4.dp, bottom = 20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item(key = "_head") {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                if (back != null && onBack != null) BackLink(back, onBack)
                Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(title, style = if (big) HT.title else HT.subTitle, modifier = Modifier.heading(), maxLines = 2, overflow = TextOverflow.Ellipsis)
                        if (!line.isNullOrBlank()) Text(line, style = HT.line.copy(color = lineColor), maxLines = 2, overflow = TextOverflow.Ellipsis)
                    }
                    trailing?.invoke()
                }
            }
        }
        content()
    }
}

/** "‹ Home": back to where this screen opened from. */
@Composable
fun BackLink(text: String, onClick: () -> Unit) {
    Row(
        Modifier.minimumInteractiveComponentSize().heightIn(min = 40.dp).clip(RoundedCornerShape(12.dp))
            .clickable(role = Role.Button, onClickLabel = "go back", onClick = onClick).padding(end = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        NIcon("chevronLeft", size = 20.dp, stroke = 2.4f, color = H.Text)
        Text(text, style = HT.row)
    }
}

/** A solid card (#131b36 with its hairline). */
@Composable
fun HCard(
    modifier: Modifier = Modifier,
    padding: PaddingValues = PaddingValues(horizontal = 16.dp, vertical = 4.dp),
    radius: Dp = 22.dp,
    background: Color = H.Card,
    border: Color = H.CardLine,
    onClick: (() -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    val shape = RoundedCornerShape(radius)
    Column(
        modifier.fillMaxWidth().clip(shape).background(background, shape).border(1.dp, border, shape)
            .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClick = onClick) else Modifier)
            .padding(padding),
        content = content,
    )
}

/** A card's small capitals heading, with an optional link on the right ("Edit", "All alerts"). */
@Composable
fun Caps(text: String, modifier: Modifier = Modifier, action: String? = null, onAction: (() -> Unit)? = null) {
    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(text.uppercase(), style = HT.caps, modifier = Modifier.weight(1f).heading())
        if (action != null && onAction != null) {
            Text(action, style = HT.line.copy(color = H.Text),
                modifier = Modifier.minimumInteractiveComponentSize().clickable(role = Role.Button, onClick = onAction).padding(horizontal = 4.dp))
        }
    }
}

@Composable
fun HDivider() = Box(Modifier.fillMaxWidth().height(1.dp).background(H.CardLine))

/** The mockups' switch (52x32 or 48x28): [on] colours it when on (yellow for lights, blue elsewhere). */
@Composable
fun HSwitch(
    checked: Boolean,
    label: String,
    enabled: Boolean = true,
    on: Color = H.Accent,
    knobOn: Color = H.Page,
    small: Boolean = false,
    onChange: (Boolean) -> Unit,
) {
    val w = if (small) 48.dp else 52.dp
    val h = if (small) 28.dp else 32.dp
    val knob = h - 6.dp
    val shape = RoundedCornerShape(99.dp)
    Box(
        Modifier.minimumInteractiveComponentSize()
            .semantics { contentDescription = label }
            .toggleable(value = checked, enabled = enabled, role = Role.Switch, onValueChange = onChange)
            .size(w, h).clip(shape).background(if (checked) on else H.SwitchOff, shape).alpha(if (enabled) 1f else 0.5f),
    ) {
        Box(Modifier.padding(start = if (checked) w - knob - 3.dp else 3.dp, top = 3.dp).size(knob).background(if (checked) knobOn else H.Knob, CircleShape))
    }
}

/** The blue pill ("Done", "Save", "+ New"). */
@Composable
fun AccentButton(text: String, enabled: Boolean = true, onClick: () -> Unit) {
    Box(
        Modifier.minimumInteractiveComponentSize().heightIn(min = 38.dp).clip(RoundedCornerShape(99.dp)).background(H.Accent)
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick).alpha(if (enabled) 1f else 0.5f).padding(horizontal = 16.dp),
        contentAlignment = Alignment.Center,
    ) { Text(text, style = HT.button.copy(fontSize = 14.sp, color = H.Page)) }
}

/** A raised pill or rounded button ("All off", "Unlock", "Change"). */
@Composable
fun RaisedButton(
    text: String,
    modifier: Modifier = Modifier,
    height: Dp = 36.dp,
    radius: Dp = 99.dp,
    background: Color = H.Raised,
    border: Color = H.RaisedLine,
    color: Color = H.Text,
    style: TextStyle = HT.button,
    enabled: Boolean = true,
    onClickLabel: String? = null,
    onClick: () -> Unit,
) {
    val shape = RoundedCornerShape(radius)
    Box(
        modifier.minimumInteractiveComponentSize().heightIn(min = height).clip(shape).background(background, shape).border(1.dp, border, shape)
            .clickable(enabled = enabled, role = Role.Button, onClickLabel = onClickLabel, onClick = onClick)
            .alpha(if (enabled) 1f else 0.45f).padding(horizontal = 14.dp),
        contentAlignment = Alignment.Center,
    ) { Text(text, style = style.copy(color = color, textAlign = TextAlign.Center), maxLines = 1) }
}

/** Choices side by side in an inset (Mode, Alarm): the picked one lit. */
@Composable
fun Segmented(
    options: List<Pair<String, String>>,
    selected: String?,
    enabled: Boolean = true,
    height: Dp = 40.dp,
    lit: (String) -> Pair<Color, Color> = { H.Text to H.Page },
    onPick: (String) -> Unit,
) {
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(H.Inset).border(1.dp, H.CardLine, RoundedCornerShape(14.dp)).padding(4.dp)) {
        options.forEach { (id, label) ->
            val on = id == selected
            val (bg, fg) = if (on) lit(id) else Color.Transparent to H.Soft
            Box(
                Modifier.weight(1f).heightIn(min = height).clip(RoundedCornerShape(10.dp)).background(bg)
                    .selectable(selected = on, enabled = enabled && !on, role = Role.RadioButton) { onPick(id) },
                contentAlignment = Alignment.Center,
            ) {
                Text(label, style = HT.button.copy(fontSize = 14.sp, color = fg, fontWeight = if (on) FontWeight.ExtraBold else FontWeight.Bold),
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

/** A list row: content on the left, a divider under it unless [last]. */
@Composable
fun HRow(last: Boolean, modifier: Modifier = Modifier, onClick: (() -> Unit)? = null, content: @Composable RowScope.() -> Unit) {
    Column {
        Row(
            modifier.fillMaxWidth().heightIn(min = 52.dp)
                .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClick = onClick) else Modifier)
                .padding(vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            content = content,
        )
        if (!last) HDivider()
    }
}

/**
 * Rows dragged into a new order by their handle (the [row]'s handle modifier); TalkBack users get
 * "Move up" / "Move down" on each row instead.
 */
@Composable
fun <T> ReorderColumn(items: List<T>, key: (T) -> Any, onMove: (from: Int, to: Int) -> Unit, row: @Composable (item: T, index: Int, handle: Modifier) -> Unit) {
    var dragKey by remember { mutableStateOf<Any?>(null) }
    var offset by remember { mutableFloatStateOf(0f) }
    val heights = remember { mutableStateMapOf<Any, Int>() }
    val current by rememberUpdatedState(items)
    val move by rememberUpdatedState(onMove)
    Column {
        items.forEachIndexed { index, item ->
            val k = key(item)
            val dragging = dragKey == k
            Box(
                Modifier.zIndex(if (dragging) 1f else 0f)
                    .graphicsLayer { translationY = if (dragging) offset else 0f }
                    .onSizeChanged { heights[k] = it.height }
                    .semantics {
                        customActions = listOfNotNull(
                            if (index > 0) CustomAccessibilityAction("Move up") { move(index, index - 1); true } else null,
                            if (index < items.lastIndex) CustomAccessibilityAction("Move down") { move(index, index + 1); true } else null,
                        )
                    },
            ) {
                val handle = Modifier.pointerInput(k) {
                    detectDragGestures(
                        onDragStart = { dragKey = k; offset = 0f },
                        onDragEnd = { dragKey = null; offset = 0f },
                        onDragCancel = { dragKey = null; offset = 0f },
                    ) { change, amount ->
                        change.consume()
                        offset += amount.y
                        val list = current
                        val i = list.indexOfFirst { key(it) == k }
                        if (i < 0) return@detectDragGestures
                        if (offset > 0 && i < list.lastIndex) {
                            val h = heights[key(list[i + 1])] ?: return@detectDragGestures
                            if (offset > h / 2f) { move(i, i + 1); offset -= h }
                        } else if (offset < 0 && i > 0) {
                            val h = heights[key(list[i - 1])] ?: return@detectDragGestures
                            if (-offset > h / 2f) { move(i, i - 1); offset += h }
                        }
                    }
                }
                row(item, index, handle)
            }
        }
    }
}

/** The drag handle's look (three lines), 48dp to touch. */
@Composable
fun DragHandle(modifier: Modifier, label: String) {
    Box(modifier.size(40.dp).semantics { contentDescription = label }, contentAlignment = Alignment.Center) {
        NIcon("grip", size = 18.dp, color = H.Label, stroke = 2.4f)
    }
}

/** Move an item of a list (a copy). */
fun <T> List<T>.moved(from: Int, to: Int): List<T> {
    if (from !in indices || to !in indices || from == to) return this
    val m = toMutableList()
    m.add(to, m.removeAt(from))
    return m
}
