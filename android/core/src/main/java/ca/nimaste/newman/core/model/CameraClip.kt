package ca.nimaste.newman.core.model

/**
 * A camera alert's video, played in the app (the hub's GET /api/clips/<integration>/<clip id>,
 * cached and seekable once the alert has ended). Same rules as the web (Camera.jsx clipUrl) and iOS.
 */
object CameraClip {
    /** "frigate:r:1791546301.32669-9penfv" -> "api/clips/frigate/r%3A1791546301.32669-9penfv"; null when it isn't a clip id. */
    fun clipPath(clip: String?): String? {
        if (clip.isNullOrBlank()) return null
        val i = clip.indexOf(':')
        if (i <= 0 || i == clip.length - 1) return null
        return "api/clips/${encodeComponent(clip.substring(0, i))}/${encodeComponent(clip.substring(i + 1))}"
    }

    /** The alert a notification is about: the one with its clip, else its review id (frigate_event_id). */
    fun matchAlert(alerts: List<CameraAlert>, clip: String?, reviewId: String?): CameraAlert? =
        clip?.takeIf { it.isNotEmpty() }?.let { c -> alerts.firstOrNull { it.clip == c } }
            ?: reviewId?.takeIf { it.isNotEmpty() }?.let { r -> alerts.firstOrNull { it.id == r } }

    /** The first request for a clip: two bytes, to learn whether the hub has byte ranges for it and its length. */
    const val PROBE_RANGE = "bytes=0-1"

    /** A Content-Range reply header ("bytes 0-1/15477162"); [total] null for "*". */
    data class ContentRange(val start: Long, val end: Long, val total: Long?)

    /**
     * Reads a Content-Range header like iOS's ClipRange: a finished clip is a file on the hub, served
     * with ranges (206 + this header); a still-recording one only comes whole (200, none). Null when malformed.
     */
    fun parseContentRange(header: String?): ContentRange? {
        val s = header?.trim() ?: return null
        if (!s.startsWith("bytes ", ignoreCase = true)) return null
        val halves = s.substring(6).trim().split('/')
        if (halves.size != 2) return null
        val ends = halves[0].split('-')
        if (ends.size != 2) return null
        val a = ends[0].toLongOrNull() ?: return null
        val b = ends[1].toLongOrNull() ?: return null
        if (a < 0 || b < a) return null
        val total = if (halves[1] == "*") null else (halves[1].toLongOrNull()?.takeIf { it > b } ?: return null)
        return ContentRange(a, b, total)
    }

    /** Like JavaScript's encodeURIComponent: UTF-8, keeping A-Z a-z 0-9 - _ . ! ~ * ' ( ). */
    fun encodeComponent(s: String): String {
        val out = StringBuilder()
        for (b in s.toByteArray(Charsets.UTF_8)) {
            val c = b.toInt() and 0xFF
            if ((c in 'A'.code..'Z'.code) || (c in 'a'.code..'z'.code) || (c in '0'.code..'9'.code) || c.toChar() in "-_.!~*'()") {
                out.append(c.toChar())
            } else {
                out.append('%').append("0123456789ABCDEF"[c shr 4]).append("0123456789ABCDEF"[c and 15])
            }
        }
        return out.toString()
    }
}
