package ca.nimaste.newman.core

import ca.nimaste.newman.core.wall.OverlayGrant
import ca.nimaste.newman.core.wall.OverlayGrant.Step
import ca.nimaste.newman.core.wall.PinPrompt
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Asking to pin the wall's screen again (without device owner), and the overlay permission steps. */
class ScreenPinTest {

    private fun ask(p: PinPrompt, wanted: Boolean = true, owner: Boolean = false, pinned: Boolean = false) =
        p.shouldAsk(wanted, owner, pinned)

    @Test
    fun asksOncePerResume() {
        val p = PinPrompt()
        assertFalse(ask(p)) // not resumed yet
        p.resumed()
        assertTrue(ask(p))
        assertFalse(ask(p)) // focus comes and goes: still once
        p.focusChanged(false)
        p.focusChanged(true)
        assertFalse(ask(p))
        p.paused()
        p.resumed() // opened again after unpinning
        assertTrue(ask(p))
    }

    @Test
    fun notWhenOffOwnerOrAlreadyPinned() {
        val p = PinPrompt()
        p.resumed(); assertFalse(ask(p, wanted = false))
        p.paused(); p.resumed(); assertFalse(ask(p, owner = true))
        p.paused(); p.resumed(); assertFalse(ask(p, pinned = true))
        p.paused(); p.resumed(); assertTrue(ask(p))
    }

    @Test
    fun declinedDialogWaitsForTheNextResume() {
        val p = PinPrompt()
        p.resumed()
        assertTrue(ask(p))
        p.focusChanged(false) // the prompt is up
        p.focusChanged(true)  // declined
        assertFalse(ask(p))
        p.paused(); p.resumed()
        assertTrue(ask(p))
    }

    @Test
    fun promptShownAsItsOwnScreenDoesNotLoop() {
        val p = PinPrompt()
        p.resumed()
        assertTrue(ask(p))
        p.focusChanged(false)
        p.paused()
        p.resumed() // back from the declined prompt
        p.focusChanged(true)
        assertFalse(ask(p))
        p.paused(); p.resumed() // a real resume later
        assertTrue(ask(p))
    }

    @Test
    fun skipsTheResumeAfterSettings() {
        val p = PinPrompt()
        p.resumed(); assertTrue(ask(p)); p.askFailed()
        p.paused()
        p.skipNextResume()
        p.resumed()
        assertFalse(ask(p))
        p.paused(); p.resumed()
        assertTrue(ask(p))
    }

    @Test
    fun failedAskDoesNotSwallowTheNextResume() {
        val p = PinPrompt()
        p.resumed(); assertTrue(ask(p)); p.askFailed()
        p.paused(); p.resumed()
        assertTrue(ask(p))
    }

    @Test
    fun overlayStepsInOrderEndingWithAdb() {
        assertEquals(listOf(Step.OWN_OVERLAY_SCREEN, Step.OVERLAY_LIST, Step.APP_DETAILS, Step.ADB_COMMAND), OverlayGrant.steps)
        assertEquals(Step.OVERLAY_LIST, OverlayGrant.next(Step.OWN_OVERLAY_SCREEN))
        assertEquals(Step.ADB_COMMAND, OverlayGrant.next(Step.APP_DETAILS))
        assertEquals(Step.ADB_COMMAND, OverlayGrant.next(Step.ADB_COMMAND))
        assertEquals("adb shell appops set ca.nimaste.newman.wall.dev SYSTEM_ALERT_WINDOW allow",
            OverlayGrant.adbCommand("ca.nimaste.newman.wall.dev"))
    }
}
