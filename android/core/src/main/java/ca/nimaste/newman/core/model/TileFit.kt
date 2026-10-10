package ca.nimaste.newman.core.model

import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min

/**
 * Fitting rows into a tile's cell rectangle on a phone page (web/src/wall/PhoneTiles.jsx fitList,
 * the layout editor's preview): the rows that fit, the rest counted for a "+N more". Sizes are dp;
 * an infinite height (a flowed page, no rectangle) fits everything.
 */
object TileFit {
    /** One line of small text. */
    const val LINE = 18f

    /** How many rows of [row] with [gap] between them fit in [avail]. */
    fun rowsIn(avail: Float, row: Float, gap: Float = 0f): Int {
        if (avail == Float.POSITIVE_INFINITY) return Int.MAX_VALUE
        return floor((avail + gap) / (row + gap)).toInt().coerceAtLeast(0)
    }

    /** [shown] rows, [more] left out, [left] = the height still free under the rows. */
    data class Fit(val shown: Int, val more: Int, val left: Float)

    /**
     * Rows that fit, the rest counted. Room is kept for a [moreH] "+N more" line unless that would
     * leave no row at all (then the count shows only where [Fit.left] has room for it).
     */
    fun list(count: Int, avail: Float, row: Float, gap: Float, moreH: Float): Fit {
        val all = rowsIn(avail, row, gap)
        if (all >= count) return Fit(count, 0, avail - count * (row + gap) + gap)
        val n = max(min(all, 1), if (moreH > 0f) rowsIn(avail - moreH, row, gap) else all)
        return Fit(n, count - n, avail - n * (row + gap) + (if (n > 0) gap else 0f))
    }
}
