package ca.nimaste.newman.core

import ca.nimaste.newman.core.wall.WallShell
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The wall's shell colour from the page's theme (same cases as the iPad wall's Linux checks). */
class WallShellTest {

    @Test
    fun themesFromThePage() {
        assertEquals(WallShell("#0b1026", true), WallShell.parse("#0b1026", "dark"))
        assertEquals(WallShell("#e6def5", false), WallShell.parse("#E6DEF5", "light"))
        assertEquals(WallShell("#6f9dd4", true), WallShell.parse(" #6f9dd4 ", "DARK"))
    }

    @Test
    fun shortHexAndSchemeFromTheColour() {
        assertEquals(WallShell("#ffffff", false), WallShell.parse("#fff", null))
        assertEquals(WallShell("#112233", true), WallShell.parse("#123", ""))
        assertFalse(WallShell.parse("#e6def5", "sepia")!!.dark)
        assertTrue(WallShell.parse("#0b1026", null)!!.dark)
    }

    @Test
    fun anythingElseIsRefused() {
        for (bad in listOf(null, "", "0b1026", "#0b10", "#0b1026ff", "#zzzzzz", "red", "rgb(1,2,3)", "url(x)")) {
            assertNull(bad, WallShell.parse(bad, "dark"))
        }
        assertEquals(WallShell.MIDNIGHT, WallShell.saved(null, null))
        assertEquals(WallShell.MIDNIGHT, WallShell.saved("nope", "light"))
        assertEquals(WallShell("#e6def5", false), WallShell.saved("#e6def5", "light"))
    }

    @Test
    fun colourAndInk() {
        assertEquals(0xFF0B1026.toInt(), WallShell.MIDNIGHT.argb)
        assertEquals(0xFFE6DEF5.toInt(), WallShell.parse("#e6def5", "light")!!.argb)
        assertEquals("#A9B4D6", WallShell.MIDNIGHT.ink)
        assertEquals("#55556A", WallShell.parse("#e6def5", "light")!!.ink)
    }
}
