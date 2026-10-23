package ca.nimaste.newman.core

import ca.nimaste.newman.core.wall.AlarmVolumeKeep
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** "Keep the alarm volume at full": when the wall puts the alarm volume back, and when it says so. */
class AlarmVolumeKeepTest {

    @Test
    fun restoresOnlyWhenOnAndBelowFull() {
        assertTrue(AlarmVolumeKeep.shouldRestore(on = true, current = 17, max = 25)) // Alexa's ring
        assertTrue(AlarmVolumeKeep.shouldRestore(on = true, current = 0, max = 7))
        assertTrue(AlarmVolumeKeep.shouldRestore(on = true, current = 24, max = 25))
        assertFalse(AlarmVolumeKeep.shouldRestore(on = true, current = 25, max = 25)) // already full
        assertFalse(AlarmVolumeKeep.shouldRestore(on = false, current = 17, max = 25)) // switch off
        assertFalse(AlarmVolumeKeep.shouldRestore(on = false, current = 0, max = 25))
    }

    @Test
    fun neverLowersOrActsWithoutARealMaximum() {
        assertFalse(AlarmVolumeKeep.shouldRestore(on = true, current = 30, max = 25))
        assertFalse(AlarmVolumeKeep.shouldRestore(on = true, current = 0, max = 0))
        assertFalse(AlarmVolumeKeep.shouldRestore(on = true, current = 0, max = -1))
    }

    @Test
    fun logsTheFirstThenAtMostOnceAMinute() {
        assertTrue(AlarmVolumeKeep.shouldLog(nowMs = 5_000, lastLogMs = -1))
        assertFalse(AlarmVolumeKeep.shouldLog(nowMs = 11_000, lastLogMs = 5_000)) // the next ~6 s round
        assertFalse(AlarmVolumeKeep.shouldLog(nowMs = 64_999, lastLogMs = 5_000))
        assertTrue(AlarmVolumeKeep.shouldLog(nowMs = 65_000, lastLogMs = 5_000))
    }
}
