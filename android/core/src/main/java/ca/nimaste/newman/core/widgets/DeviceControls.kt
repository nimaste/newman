package ca.nimaste.newman.core.widgets

/**
 * The pure parts of Android's Device Controls (the power-menu / Quick Settings "Device controls" panel)
 * and of the app icon's shortcuts, from the same GET /api/widgets/state the widgets use. No kind is
 * named here: the hub's actions decide (what a tap does first, which ones are sensitive).
 */
object DeviceControlPlan {
    /** While the controls panel shows our devices, fresh states this often (plus the app's live socket). */
    const val POLL_MS = 10_000L

    enum class Style {
        /** On / off (two one-tap actions, one of them what a tap does now): a switch. */
        TOGGLE,
        /** A button: one thing to do (a Press, a scene), or the next of several. */
        PUSH,
        /** Nothing to do by tapping (a sensor): its state only. */
        STATUS,
    }

    fun style(d: WidgetDevice?): Style {
        val actions = d?.actions.orEmpty()
        val primary = actions.firstOrNull { it.primary }
        return when {
            primary == null -> Style.STATUS
            actions.size == 2 -> Style.TOGGLE
            else -> Style.PUSH // one action, or several (a mode): the button runs what a tap does now
        }
    }

    /**
     * Whether the phone must be unlocked before this control works: what a tap does now is sensitive
     * (unlock, open, disarm). Everything else works from the lock screen.
     */
    fun authRequired(d: WidgetDevice?): Boolean = WidgetTiles.tapAction(d)?.sensitive == true

    /**
     * The action to run for a control press. A switch moved to [wantActive] runs what a tap does now
     * only when that changes the device (already there: nothing); a button runs it always.
     */
    fun actionFor(d: WidgetDevice?, wantActive: Boolean?): WidgetAction? {
        val a = WidgetTiles.tapAction(d) ?: return null
        if (wantActive != null && style(d) == Style.TOGGLE && d!!.active == wantActive) return null
        return a
    }

    /** The control's status line: what the widgets show ("On", "21.5 °C", "Unlock…", "Offline"). */
    fun statusText(d: WidgetDevice): String = WidgetTiles.stateText(d)

    /** Where the panel groups it: its room, else "Other". */
    fun zone(area: String?): String = area?.trim()?.takeIf { it.isNotEmpty() } ?: WidgetPicker.OTHER
}

/** How often each device was run from this phone, for the app icon's shortcuts. */
data class DeviceUse(val count: Int, val lastAt: Long)

object UsageRank {
    /** The [n] most-used devices: most runs first, then the most recent, then by id (stable). */
    fun top(uses: Map<String, DeviceUse>, n: Int = 4): List<String> =
        uses.entries.filter { it.value.count > 0 }
            .sortedWith(compareByDescending<Map.Entry<String, DeviceUse>> { it.value.count }
                .thenByDescending { it.value.lastAt }.thenBy { it.key })
            .take(n.coerceAtLeast(0))
            .map { it.key }

    /** One more run of [id] at [now]. */
    fun record(uses: Map<String, DeviceUse>, id: String, now: Long): Map<String, DeviceUse> {
        val old = uses[id]
        return uses + (id to DeviceUse((old?.count ?: 0) + 1, now))
    }

    /** "3:1712345678" <-> DeviceUse, as kept in preferences. */
    fun encode(u: DeviceUse): String = "${u.count}:${u.lastAt}"

    fun decode(s: String?): DeviceUse? {
        val parts = s?.split(':') ?: return null
        if (parts.size != 2) return null
        val c = parts[0].toIntOrNull() ?: return null
        val t = parts[1].toLongOrNull() ?: return null
        return DeviceUse(c, t)
    }
}
