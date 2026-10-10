package ca.nimaste.newman.core.wall

/**
 * The wall tablet's window brightness (WindowManager.LayoutParams.screenBrightness, 0..1).
 *
 * A dimmed wall must still look ON. On a Fire HD panel 0.02 already looks nearly black, and with
 * Android's Battery Saver on the display halves whatever the window asks for (BatterySaverPolicy's
 * brightness factor, 0.5 by default): 0.02 became 0.01 "override [low_pwr]" and the screen read as
 * off. So every level the app sets is at least [FLOOR] - 0.08, about 30 nits on a ~400-nit panel:
 * clearly lit in a dark hallway, yet low enough to sit unnoticed at night - and while Battery Saver
 * is on, the dimmed level is doubled back up (capped at 1), so it still lands at the floor or above.
 */
object WallBrightness {
    /** Never below this, whatever the setting or the page asks for. */
    const val FLOOR = 0.08f
    /** Dimmed brightness on a new install: low but obviously on. */
    const val DEFAULT_DIM = 0.15f
    /** Minutes without a touch before dimming, on a new install. */
    const val DEFAULT_DIM_MINUTES = 5
    /** Battery Saver's default brightness factor (BatterySaverPolicy adjust_brightness_factor). */
    const val POWER_SAVE_FACTOR = 0.5f
    /** Largest dimmed level the settings offer. */
    const val MAX_DIM = 0.5f
    /** "Use the system's own brightness" (BRIGHTNESS_OVERRIDE_NONE). */
    const val SYSTEM = -1f

    /** A saved or chosen dimmed level, kept in [FLOOR]..1 (old installs saved 0.02). */
    fun dimSetting(level: Float): Float = if (level.isNaN()) DEFAULT_DIM else level.coerceIn(FLOOR, 1f)

    /** The window brightness while dimmed: the setting, raised back up while Battery Saver halves it. */
    fun dimmed(level: Float, powerSave: Boolean): Float {
        val l = dimSetting(level)
        return if (powerSave) (l / POWER_SAVE_FACTOR).coerceIn(FLOOR, 1f) else l
    }

    /** The window brightness while awake: [SYSTEM] stays the system's own, anything else [FLOOR]..1. */
    fun awake(level: Float): Float = if (level.isNaN() || level < 0f) SYSTEM else level.coerceIn(FLOOR, 1f)
}
