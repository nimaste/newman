package ca.nimaste.newman.core

import ca.nimaste.newman.core.wall.WallBrightness
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The wall's brightness levels: never below the visible floor, Battery Saver made up for. */
class WallBrightnessTest {

    @Test
    fun oldDimLevelsAreRaisedToTheFloor() {
        assertEquals(WallBrightness.FLOOR, WallBrightness.dimSetting(0.02f), 0f)
        assertEquals(WallBrightness.FLOOR, WallBrightness.dimSetting(0f), 0f)
        assertEquals(WallBrightness.FLOOR, WallBrightness.dimSetting(-3f), 0f)
        assertEquals(0.2f, WallBrightness.dimSetting(0.2f), 0f)
        assertEquals(1f, WallBrightness.dimSetting(4f), 0f)
        assertEquals(WallBrightness.DEFAULT_DIM, WallBrightness.dimSetting(Float.NaN), 0f)
    }

    @Test
    fun dimmedIsNeverBelowTheFloor() {
        for (l in listOf(0f, 0.01f, 0.02f, 0.05f, 0.08f, 0.15f, 0.5f, 1f)) {
            for (saver in listOf(false, true)) {
                val v = WallBrightness.dimmed(l, saver)
                assertTrue("$l saver=$saver -> $v", v >= WallBrightness.FLOOR && v <= 1f)
            }
        }
        assertEquals(0.15f, WallBrightness.dimmed(0.15f, powerSave = false), 0f)
    }

    @Test
    fun batterySaverIsMadeUpFor() {
        // Battery Saver halves the window's level: what the screen shows stays at the setting.
        assertEquals(0.3f, WallBrightness.dimmed(0.15f, powerSave = true), 1e-6f)
        assertEquals(0.16f, WallBrightness.dimmed(0.02f, powerSave = true), 1e-6f)
        assertEquals(1f, WallBrightness.dimmed(0.8f, powerSave = true), 0f)
        val shown = WallBrightness.dimmed(0f, powerSave = true) * WallBrightness.POWER_SAVE_FACTOR
        assertTrue("shown $shown", shown >= WallBrightness.FLOOR)
    }

    @Test
    fun awakeKeepsTheSystemLevelOrClamps() {
        assertEquals(WallBrightness.SYSTEM, WallBrightness.awake(-1f), 0f)
        assertEquals(WallBrightness.SYSTEM, WallBrightness.awake(Float.NaN), 0f)
        assertEquals(WallBrightness.FLOOR, WallBrightness.awake(0f), 0f) // the page asked for 0
        assertEquals(WallBrightness.FLOOR, WallBrightness.awake(0.01f), 0f)
        assertEquals(0.6f, WallBrightness.awake(0.6f), 0f)
        assertEquals(1f, WallBrightness.awake(1.5f), 0f)
    }

    @Test
    fun defaultsAreVisible() {
        assertTrue(WallBrightness.DEFAULT_DIM >= WallBrightness.FLOOR)
        assertEquals(5, WallBrightness.DEFAULT_DIM_MINUTES)
    }
}
