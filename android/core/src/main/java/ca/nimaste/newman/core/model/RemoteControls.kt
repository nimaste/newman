package ca.nimaste.newman.core.model

import kotlinx.serialization.Serializable
import kotlin.math.roundToInt

/**
 * What a remote controls (the hub's entity.controls on a remote): the lights its buttons work, and
 * what each press does ({"key", "label": "Tap top", "does": "On at 60%", "custom"}). Null on an
 * entity = the remote has no controls.
 */
@Serializable
data class RemoteControls(
    val lights: List<String> = emptyList(),
    val onLevel: Int? = null,
    val hold: Boolean = true,
    val rows: List<RemoteControlRow> = emptyList(),
) {
    /** The card's heading: "Controls · 3 lights", or just "Controls" for a remote with no lights. */
    val header: String
        get() = if (lights.isEmpty()) "Controls" else "Controls · ${lights.size} light${if (lights.size == 1) "" else "s"}"

    companion object {
        /** One controlled light's level for its row: "60%" while on (dimmable), "On", "Off", "Offline". */
        fun lightStatus(e: Entity?, k: Kind?): String {
            if (e == null || !e.available) return "Offline"
            val on = if (k?.activeState != null) e.state == k.activeState else e.state == "on"
            if (!on) return "Off"
            val b = e.attrs.number("brightness")
            return if (b != null && e.attrs.bool("dimmable") != false) "${b.roundToInt()}%" else "On"
        }
    }
}

@Serializable
data class RemoteControlRow(
    val key: String = "",
    val label: String = "",
    val does: String = "",
    val custom: Boolean = false,
)
