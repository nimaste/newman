package ca.nimaste.newman.core.wall

import kotlin.math.roundToInt

/**
 * The wall settings' volume sliders (0-100 %) and Android's stream volume steps (0..max index, e.g. 0..15
 * for media, 0..7 for alarms on a Fire tablet). The two convert both ways without drifting: a step read back
 * as a percent turns into the same step again.
 */
object WallVolume {
    fun indexOf(percent: Int, max: Int): Int {
        if (max <= 0) return 0
        return (percent.coerceIn(0, 100) * max / 100.0).roundToInt().coerceIn(0, max)
    }

    fun percentOf(index: Int, max: Int): Int {
        if (max <= 0) return 0
        return (index.coerceIn(0, max) * 100.0 / max).roundToInt()
    }
}
