package ca.nimaste.newman.ui.common

import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Density
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.Kind
import ca.nimaste.newman.core.model.Spoken
import ca.nimaste.newman.core.model.TextScale

/** A section or page title, for TalkBack's "jump by heading". */
fun Modifier.heading(): Modifier = semantics { this.heading() }

/** One spoken label for a whole tile/row (its texts and icons merged into it). */
fun Modifier.spoken(label: String): Modifier = semantics(mergeDescendants = true) { contentDescription = label }

/**
 * "Den plug, on" / "Front door, locked, battery 80%, working": the device's name and its state as the
 * tile shows it (the hub's own stateLabel first), for TalkBack.
 */
fun spokenDevice(e: Entity, k: Kind?, busy: Boolean = false, withArea: String? = null): String {
    val v = HubModel.vitals(e)
    val name = listOfNotNull(e.name, withArea?.takeIf { it.isNotBlank() }).joinToString(", ") // a room keeps its capitals
    return Spoken.of(name, HubModel.entityStateText(e, k).let(Spoken::fromDots), Spoken.battery(v.battery), v.problem,
        if (busy) "working" else null)
}

/**
 * Text inside a fixed-size cell (the phone layout's square grid, the tab bar): the system font size,
 * but at most [max] times, so nothing is cut off. Text everywhere else follows the setting fully.
 */
@Composable
fun CappedFontScale(max: Float = TextScale.CELL_MAX, content: @Composable () -> Unit) {
    val d = LocalDensity.current
    val capped = TextScale.capped(d.fontScale, max)
    if (capped == d.fontScale) content()
    else CompositionLocalProvider(LocalDensity provides Density(d.density, capped), content = content)
}
