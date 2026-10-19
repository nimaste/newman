package ca.nimaste.newman.core

import ca.nimaste.newman.core.wall.WallVolume
import org.junit.Assert.assertEquals
import org.junit.Test

/** The wall's volume sliders and Android's stream steps. */
class WallVolumeTest {

    @Test
    fun percentToStepAndBack() {
        assertEquals(0, WallVolume.indexOf(0, 15))
        assertEquals(15, WallVolume.indexOf(100, 15))
        assertEquals(9, WallVolume.indexOf(60, 15))
        assertEquals(4, WallVolume.indexOf(50, 7))
        assertEquals(60, WallVolume.percentOf(9, 15))
        assertEquals(100, WallVolume.percentOf(7, 7))
    }

    @Test
    fun aStepReadBackIsTheSameStep() {
        for (max in listOf(7, 15, 25, 30)) {
            for (i in 0..max) assertEquals("max $max step $i", i, WallVolume.indexOf(WallVolume.percentOf(i, max), max))
        }
    }

    @Test
    fun outOfRangeIsClamped() {
        assertEquals(15, WallVolume.indexOf(150, 15))
        assertEquals(0, WallVolume.indexOf(-5, 15))
        assertEquals(0, WallVolume.indexOf(50, 0))
        assertEquals(0, WallVolume.percentOf(3, 0))
    }
}
