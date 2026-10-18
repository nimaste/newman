package ca.nimaste.newman.core.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/** One of a device's problems (health.items): its id for muting, its words, and whether it's muted. */
@Serializable
data class HealthItem(val id: String = "", val text: String = "", val muted: Muted? = null)

/** A muted problem: how long ("day", "week", "fixed" = until it clears, "always"), until when, by whom. */
@Serializable
data class Muted(val mode: String = "", val until: Double? = null, val by: String = "")

/** PUT api/entities/{id}/mute. */
@Serializable
data class MuteRequest(val issue: String, @SerialName("for") val duration: String)

/** The hub's answer to muting or unmuting: the device's health now. */
@Serializable
data class MuteReply(val health: Health? = null)

/** Muting a device's problems: which ones show, which are muted, and how a mute reads. */
object Muting {
    /** The choices, in the order offered: mode to its words. */
    val CHOICES = listOf("day" to "For 1 day", "week" to "For 1 week", "fixed" to "Until it's fixed", "always" to "Always")

    /**
     * The problems that still show. From health.items when the hub sends them (only those can be muted);
     * an older hub's plain issues otherwise, with no id (no Mute for those).
     */
    fun active(h: Health): List<HealthItem> =
        if (h.items.isNotEmpty()) h.items.filter { it.muted == null } else h.issues.map { HealthItem(text = it) }

    fun muted(h: Health): List<HealthItem> = h.items.filter { it.muted != null }

    /** "Muted until Sunday, Oct 25, 9:15 AM" / "Muted until it's fixed" / "Muted always". */
    fun label(m: Muted, zone: ZoneId = ZoneId.systemDefault(), locale: Locale = Locale.getDefault()): String = when {
        m.mode == "always" -> "Muted always"
        m.mode == "fixed" -> "Muted until it's fixed"
        m.until != null -> "Muted until " + DateTimeFormatter.ofPattern("EEEE, MMM d, h:mm a", locale)
            .format(Instant.ofEpochMilli((m.until * 1000).toLong()).atZone(zone))
        else -> "Muted"
    }
}
