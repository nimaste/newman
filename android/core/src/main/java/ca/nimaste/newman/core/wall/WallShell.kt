package ca.nimaste.newman.core.wall

/**
 * The colour the wall app paints behind the hub's page - while it loads, on "can't reach Newman", in the
 * display cutout - so a light theme doesn't flash Midnight blue. The page reports its theme's plain page
 * colour with NewmanWall.setTheme({background, scheme}) (web/src/wall/theme.js SHELL); the app keeps it for
 * the next start. Same rules as the iPad wall (ios/Shared/Core/WallScreen.swift WallShell).
 */
data class WallShell(val background: String, val dark: Boolean) {
    /** 0xAARRGGBB of [background]. */
    val argb: Int get() = (0xFF shl 24) or background.substring(1).toInt(16)

    /** Text on this background (the "can't reach" message). */
    val ink: String get() = if (dark) "#A9B4D6" else "#55556A"

    companion object {
        /** Midnight, the original look: before any page has said otherwise. */
        val MIDNIGHT = WallShell("#0b1026", dark = true)

        /**
         * "#rgb" / "#rrggbb" (any case) and "light" / "dark"; without a known scheme, dark when the colour
         * is. Null for anything else (the page's word is never trusted blindly).
         */
        fun parse(background: String?, scheme: String?): WallShell? {
            val hex = normalize(background) ?: return null
            val dark = when (scheme?.trim()?.lowercase()) {
                "dark" -> true
                "light" -> false
                else -> luminance(hex) < 0.5
            }
            return WallShell(hex, dark)
        }

        /** What was saved, else Midnight. */
        fun saved(background: String?, scheme: String?): WallShell = parse(background, scheme) ?: MIDNIGHT

        private fun normalize(v: String?): String? {
            val s = v?.trim()?.lowercase() ?: return null
            if (!s.startsWith("#")) return null
            val h = s.substring(1)
            if (h.any { it !in '0'..'9' && it !in 'a'..'f' }) return null
            return when (h.length) {
                6 -> "#$h"
                3 -> "#" + h.map { "$it$it" }.joinToString("")
                else -> null
            }
        }

        /** Relative luminance 0..1 (sRGB weights, no gamma: only light vs dark matters). */
        private fun luminance(hex: String): Double {
            val n = hex.substring(1).toInt(16)
            val r = (n shr 16) and 0xFF
            val g = (n shr 8) and 0xFF
            val b = n and 0xFF
            return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
        }
    }
}
