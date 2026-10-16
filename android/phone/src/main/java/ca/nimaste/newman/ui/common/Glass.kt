package ca.nimaste.newman.ui.common

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T

/**
 * Set by the phone-layout grid around a tile: the tile's outer card grows to at least this height
 * so it fills its cell (cards inside it don't).
 */
val LocalFillHeight = compositionLocalOf { Dp.Unspecified }

/** A frosted card (rgba(255,255,255,.07) with a hairline border), the boards' basic surface. */
@Composable
fun GlassCard(
    modifier: Modifier = Modifier,
    radius: Dp = 22.dp,
    background: Color = N.Glass,
    border: Color = N.Border,
    padding: PaddingValues = PaddingValues(14.dp),
    onClick: (() -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    val shape = RoundedCornerShape(radius)
    val fill = LocalFillHeight.current
    Column(
        (if (fill != Dp.Unspecified) Modifier.heightIn(min = fill) else Modifier)
            .then(modifier)
            .clip(shape)
            .background(background, shape)
            .border(1.dp, border, shape)
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(padding),
    ) {
        if (fill == Dp.Unspecified) content()
        else {
            val scope = this
            CompositionLocalProvider(LocalFillHeight provides Dp.Unspecified) { scope.content() }
        }
    }
}

/**
 * A card's heading: small capitals ("CAMERAS", "QUICK CONTROLS"), with an optional action on the
 * right ("All 8", "Show all"). Every card of the phone uses this one look.
 */
@Composable
fun CardHeader(title: String, modifier: Modifier = Modifier, action: String? = null, onAction: (() -> Unit)? = null) {
    Row(modifier.fillMaxWidth().heightIn(min = 22.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(title.uppercase(), style = T.caps, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f).heading())
        if (action != null) {
            Text(action, style = T.small.copy(color = N.Blue),
                modifier = (if (onAction != null) Modifier.clickable(role = Role.Button, onClick = onAction) else Modifier).padding(start = 8.dp))
        }
    }
}

/** A rounded glass button (the boards' 44-48dp secondary buttons). */
@Composable
fun GlassButton(
    text: String,
    modifier: Modifier = Modifier,
    icon: String? = null,
    enabled: Boolean = true,
    busy: Boolean = false,
    height: Dp = 44.dp,
    radius: Dp = 14.dp,
    style: TextStyle = T.label.copy(fontSize = 14.sp),
    background: Color = N.Glass,
    foreground: Color = N.Text,
    border: Color = N.BorderHi,
    onClick: () -> Unit,
) {
    val shape = RoundedCornerShape(radius)
    Row(
        modifier
            .minimumInteractiveComponentSize() // a 48dp touch target, whatever it looks like
            .heightIn(min = height) // grows with a bigger font instead of cutting the label off
            .defaultMinSize(minWidth = 44.dp)
            .clip(shape)
            .background(background, shape)
            .border(1.dp, border, shape)
            .clickable(enabled = enabled && !busy, role = Role.Button, onClick = onClick)
            .then(if (busy) Modifier.semantics { stateDescription = "Working" } else Modifier)
            .alpha(if (enabled) 1f else 0.45f)
            .padding(horizontal = 14.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp, Alignment.CenterHorizontally),
    ) {
        if (busy) CircularProgressIndicator(Modifier.size(16.dp), color = foreground, strokeWidth = 2.dp)
        else if (icon != null) NIcon(icon, size = 16.dp, color = foreground)
        Text(text, style = style.copy(color = foreground, textAlign = TextAlign.Center), maxLines = 2, overflow = TextOverflow.Ellipsis)
    }
}

/** The solid light button (primary action). */
@Composable
fun PrimaryButton(text: String, modifier: Modifier = Modifier, enabled: Boolean = true, busy: Boolean = false, onClick: () -> Unit) =
    GlassButton(text, modifier.fillMaxWidth(), enabled = enabled, busy = busy, height = 52.dp, radius = 16.dp,
        style = T.label, background = N.Text, foreground = N.Night, border = N.Text, onClick = onClick)

/** A small rounded chip ("All secure", "17° Clear"). */
@Composable
fun Chip(
    text: String,
    modifier: Modifier = Modifier,
    icon: String? = null,
    tone: Color? = null,
    dot: Color? = null,
    content: (@Composable RowScope.() -> Unit)? = null,
) {
    val shape = RoundedCornerShape(99.dp)
    val bg = tone?.copy(alpha = 0.16f) ?: N.Glass
    val border = tone?.copy(alpha = 0.35f) ?: N.Border
    Row(
        modifier.heightIn(min = 32.dp).clip(shape).background(bg, shape).border(1.dp, border, shape).padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        if (dot != null) Box(Modifier.size(7.dp).background(dot, CircleShape))
        if (icon != null) NIcon(icon, size = 15.dp, color = tone ?: N.Text, stroke = 2.4f)
        content?.invoke(this)
        Text(text, style = T.small, maxLines = 1)
    }
}

/** A selectable pill (camera picker, filters): light when on. */
@Composable
fun Pill(text: String, selected: Boolean, modifier: Modifier = Modifier, onClick: () -> Unit) {
    val shape = RoundedCornerShape(99.dp)
    Box(
        modifier
            .minimumInteractiveComponentSize()
            .heightIn(min = 36.dp)
            .clip(shape)
            .background(if (selected) N.Text else Color(0x0FFFFFFF), shape)
            .border(1.dp, Color(0x1FFFFFFF), shape)
            .selectable(selected = selected, role = Role.Tab, onClick = onClick)
            .padding(horizontal = 14.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, style = T.small.copy(fontSize = 13.sp, fontWeight = androidx.compose.ui.text.font.FontWeight.ExtraBold,
            color = if (selected) N.Night else N.Text), maxLines = 1)
    }
}

/** The boards' 54x32 switch. [label]: what it switches, for TalkBack ("Confirm before unlocking"). */
@Composable
fun NSwitch(checked: Boolean, enabled: Boolean = true, label: String? = null, onChange: (Boolean) -> Unit) {
    val shape = RoundedCornerShape(99.dp)
    Box(
        Modifier
            .minimumInteractiveComponentSize()
            .then(if (label != null) Modifier.semantics { contentDescription = label } else Modifier)
            .toggleable(value = checked, enabled = enabled, role = Role.Switch, onValueChange = onChange)
            .size(54.dp, 32.dp)
            .clip(shape)
            .background(if (checked) N.Teal else Color(0x2EFFFFFF), shape)
            .alpha(if (enabled) 1f else 0.5f),
    ) {
        Box(Modifier.padding(start = if (checked) 25.dp else 3.dp, top = 3.dp).size(26.dp).background(Color.White, CircleShape))
    }
}

/** "Back" pill of the detail screens. */
@Composable
fun BackPill(text: String = "Back", onClick: () -> Unit) {
    val shape = RoundedCornerShape(99.dp)
    Row(
        Modifier.heightIn(min = 48.dp).clip(shape).background(Color(0x14FFFFFF), shape).border(1.dp, N.BorderHi, shape)
            .clickable(role = Role.Button, onClick = onClick).padding(start = 10.dp, end = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        NIcon("chevronLeft", size = 22.dp, stroke = 2.4f)
        Text(text, style = T.label)
    }
}

/** A row in a settings-style glass section. */
@Composable
fun SectionRow(
    title: String,
    subtitle: String? = null,
    last: Boolean = false,
    onClick: (() -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null,
) {
    Column {
        Row(
            Modifier.fillMaxWidth().defaultMinSize(minHeight = 58.dp)
                .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClick = onClick) else Modifier)
                .padding(vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Column(Modifier.weight(1f)) {
                Text(title, style = T.label)
                if (subtitle != null) Text(subtitle, style = T.tiny)
            }
            trailing?.invoke()
        }
        if (!last) Box(Modifier.fillMaxWidth().height(1.dp).background(N.Divider))
    }
}

@Composable
fun NTextField(
    value: String,
    onChange: (String) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    password: Boolean = false,
    keyboard: KeyboardType = KeyboardType.Text,
    placeholder: String? = null,
    singleLine: Boolean = true,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        label = { Text(label) },
        placeholder = placeholder?.let { { Text(it, color = N.TabOff) } },
        singleLine = singleLine,
        visualTransformation = if (password) PasswordVisualTransformation() else VisualTransformation.None,
        keyboardOptions = KeyboardOptions(keyboardType = if (password) KeyboardType.Password else keyboard, autoCorrectEnabled = false),
        shape = RoundedCornerShape(14.dp),
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = N.Blue, unfocusedBorderColor = N.BorderHi, focusedLabelColor = N.Blue,
            unfocusedLabelColor = N.Muted, cursorColor = N.Text, focusedTextColor = N.Text, unfocusedTextColor = N.Text,
            focusedContainerColor = N.Glass, unfocusedContainerColor = N.Glass,
        ),
        textStyle = T.body.copy(fontSize = 16.sp),
        modifier = modifier.fillMaxWidth(),
    )
}

/** An icon in a tinted circle (list rows, alerts). */
@Composable
fun IconBubble(icon: String, background: Color, foreground: Color, size: Dp = 40.dp, iconSize: Dp = 20.dp, shape: Shape = CircleShape) {
    Box(Modifier.size(size).background(background, shape), contentAlignment = Alignment.Center) {
        NIcon(icon, size = iconSize, color = foreground)
    }
}

@Composable
fun Gap(h: Dp = 0.dp, w: Dp = 0.dp) = Spacer(Modifier.height(h).width(w))

/**
 * Tap and hold (device tiles: tap = what a tap does, hold = everything it can do). The labels are what
 * TalkBack offers: "Double-tap to turn off, double-tap and hold to show all controls".
 */
@OptIn(ExperimentalFoundationApi::class)
fun Modifier.tapAndHold(onTap: () -> Unit, onHold: (() -> Unit)?, tapLabel: String? = null, holdLabel: String? = null): Modifier =
    combinedClickable(onClickLabel = tapLabel, role = Role.Button, onLongClickLabel = holdLabel, onLongClick = onHold, onClick = onTap)

/** An icon-only button: 48dp to touch, [label] for TalkBack ("Next camera"). */
@Composable
fun IconAction(
    icon: String,
    label: String,
    modifier: Modifier = Modifier,
    size: Dp = 48.dp,
    iconSize: Dp = 22.dp,
    color: Color = N.Text,
    background: Color = Color.Transparent,
    shape: Shape = CircleShape,
    enabled: Boolean = true,
    stroke: Float = 2f,
    onClick: () -> Unit,
) {
    Box(
        modifier.minimumInteractiveComponentSize().size(size).clip(shape).background(background, shape)
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick)
            .semantics { contentDescription = label },
        contentAlignment = Alignment.Center,
    ) { NIcon(icon, size = iconSize, color = color, stroke = stroke) }
}
