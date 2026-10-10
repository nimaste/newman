package ca.nimaste.newman.core.model

/**
 * Words for screen readers (TalkBack) and the font-size caps for fixed-size cells. Built from what the
 * hub already says about a device (its name, its state text, its actions' labels) - never from a list
 * of kinds.
 */
object Spoken {
    /** "Den plug, on" / "Front door, locked, battery 80%": the name, then each non-empty part. */
    fun of(name: String, vararg parts: String?): String =
        (listOf(name.trim()) + parts.mapNotNull { p -> p?.trim()?.takeIf { it.isNotEmpty() }?.let(::phrase) })
            .filter { it.isNotEmpty() }
            .joinToString(", ")

    /**
     * A label as part of a sentence: "On" -> "on", "Turn off" -> "turn off"; words that are all capitals
     * ("TV", "UV index") and numbers stay as they are.
     */
    fun phrase(text: String): String {
        val t = text.trim()
        if (t.length >= 2 && t[0].isUpperCase() && t[1].isLowerCase()) return t[0].lowercaseChar() + t.substring(1)
        if (t.length == 1 && t[0].isUpperCase()) return t.lowercase()
        return t
    }

    /** A battery level for speech: "battery 80%". */
    fun battery(percent: Int?): String? = percent?.let { "battery $it%" }

    /** The part of a label a tile's "1 · 2 · 3" separators would be read out as dots in. */
    fun fromDots(text: String): String = text.split(" · ").map { it.trim() }.filter { it.isNotEmpty() }.joinToString(", ") { phrase(it) }
}

/**
 * Font scaling ("Font size" in Android's settings). Text elsewhere grows and wraps freely; cells of a
 * fixed size (the phone layout's square grid, the tab bar) cap it so nothing is cut off.
 */
object TextScale {
    /** Placed grid cells and device buttons: room was left for this much. */
    const val CELL_MAX = 1.3f

    /** Half-width device cards of a fixed height (92dp): icon, name and state. */
    const val CARD_MAX = 1.2f

    /** Tiles laid out line by line to fit their cell (TileFit), and compact tiles of a fixed height
     * (the small thermostat dial): no extra room at all. */
    const val FITTED_MAX = 1.0f

    /** The scale to use inside a cell allowing [max]: the system's, at most [max] (smaller ones stay). */
    fun capped(system: Float, max: Float = CELL_MAX): Float = if (system.isNaN() || system <= 0f) 1f else minOf(system, max)
}
