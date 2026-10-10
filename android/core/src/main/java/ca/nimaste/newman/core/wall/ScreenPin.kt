package ca.nimaste.newman.core.wall

/**
 * When the wall tablet (without device owner) asks Android to pin the screen: once after each
 * resume, as soon as the window has focus, while pinning is wanted and the screen isn't pinned yet.
 * Android shows its confirm prompt every time. Never in a loop: a declined prompt waits for the
 * next real resume, and the resume right after the wall settings (or the PIN check before them)
 * is skipped.
 */
class PinPrompt {
    private var due = false
    private var skipNext = false
    private var asking = false
    private var lostFocusWhileAsking = false

    fun resumed() {
        // A resume while our prompt was up means the prompt itself paused us (some Android builds
        // show it as its own screen): that was the answer - wait for the next resume.
        due = !skipNext && !asking
        skipNext = false
        asking = false
        lostFocusWhileAsking = false
    }

    fun paused() {
        due = false
    }

    /** The wall settings (or the PIN check before them) are opening or just closed. */
    fun skipNextResume() {
        skipNext = true
    }

    fun focusChanged(hasFocus: Boolean) {
        if (!asking) return
        if (!hasFocus) lostFocusWhileAsking = true
        else if (lostFocusWhileAsking) { // the prompt (a dialog over us) was answered
            asking = false
            lostFocusWhileAsking = false
        }
    }

    /** With the window focused: true = call startLockTask() now. Consumes this resume's turn. */
    fun shouldAsk(wanted: Boolean, deviceOwner: Boolean, pinned: Boolean): Boolean {
        if (!due) return false
        due = false
        if (!wanted || deviceOwner || pinned) return false
        asking = true
        lostFocusWhileAsking = false
        return true
    }

    /** startLockTask() threw: no prompt is showing. */
    fun askFailed() {
        asking = false
        lostFocusWhileAsking = false
    }
}

/**
 * Getting Android's "Display over other apps" for the wall. Fire OS may lack the per-app overlay
 * screen (or ignore the package), so the wall tries these in order, moving on when one can't be
 * opened or closes at once, and finally shows the adb command.
 */
object OverlayGrant {
    enum class Step { OWN_OVERLAY_SCREEN, OVERLAY_LIST, APP_DETAILS, ADB_COMMAND }

    val steps: List<Step> = Step.values().toList()

    /** The step after [step]; ADB_COMMAND is the last. */
    fun next(step: Step): Step = steps.getOrElse(steps.indexOf(step) + 1) { Step.ADB_COMMAND }

    fun adbCommand(packageName: String) = "adb shell appops set $packageName SYSTEM_ALERT_WINDOW allow"
}
