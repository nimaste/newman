package ca.nimaste.newman.core.wall

import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json

/**
 * Newman's alarms and timers ringing on a wall tablet (the wall app's side; the hub keeps the alarms):
 * the settings' "Snooze for" / "Stop ringing after" choices, what the page asks to ring
 * (NewmanWall.ring), and the coming alarms the app keeps as a backup (NewmanWall.alarmSchedule) so it can
 * ring one itself when the page didn't - the page rings an alarm itself 30 s after its time when the hub
 * hasn't said; the app BACKUP_AFTER_MS after it, if nothing rang it by then.
 */
object WallRinging {
    val SNOOZE_CHOICES = listOf(5, 9, 10, 15)
    val RING_CHOICES = listOf(5, 10, 15, 30)
    const val DEFAULT_SNOOZE = 9
    const val DEFAULT_RING = 10
    const val BACKUP_AFTER_MS = 60_000L
    /** A ring the page asked for this long before an alarm's time still counts as that alarm's ring. */
    private const val RUNG_EARLY_MS = 120_000L

    fun snoozeSetting(v: Int): Int = if (v in SNOOZE_CHOICES) v else DEFAULT_SNOOZE
    fun ringSetting(v: Int): Int = if (v in RING_CHOICES) v else DEFAULT_RING

    /** NewmanWall.ringSettings() for the page (which passes them to the hub). */
    fun settingsJson(snoozeMinutes: Int, ringMinutes: Int): String =
        """{"snoozeMinutes":${snoozeSetting(snoozeMinutes)},"ringMinutes":${ringSetting(ringMinutes)}}"""

    /** NewmanWall.ring(json): {"kind": "alarm" | "timer", "id", "title", "ringMs"}. */
    @Serializable
    data class Ring(val kind: String = "alarm", val id: String = "", val title: String = "", val ringMs: Long = DEFAULT_RING * 60_000L) {
        val isTimer: Boolean get() = kind == "timer"
        /** How long it may ring: what the page says, kept between 1 and 60 minutes. */
        val capMs: Long get() = ringMs.coerceIn(60_000L, 3_600_000L)
    }

    /** One coming alarm (at: ms since the epoch, the tablet's clock). */
    @Serializable
    data class Planned(val id: String, val at: Long, val title: String = "")

    private val json = Json { ignoreUnknownKeys = true }

    fun parseRing(text: String?): Ring? =
        try {
            if (text.isNullOrBlank()) null else json.decodeFromString(Ring.serializer(), text).takeIf { it.id.isNotBlank() }
        } catch (e: Exception) {
            null
        }

    fun parseSchedule(text: String?): List<Planned> =
        try {
            if (text.isNullOrBlank()) emptyList() else json.decodeFromString(ListSerializer(Planned.serializer()), text)
                .filter { it.id.isNotBlank() && it.at > 0 }.sortedBy { it.at }
        } catch (e: Exception) {
            emptyList()
        }

    fun scheduleJson(list: List<Planned>): String = json.encodeToString(ListSerializer(Planned.serializer()), list)

    /** Whether the page rang [p] already ([rung]: id -> when the page asked to ring it, ms). */
    fun wasRung(p: Planned, rung: Map<String, Long>): Boolean = (rung[p.id] ?: Long.MIN_VALUE) >= p.at - RUNG_EARLY_MS

    /** The next backup to set: the first coming alarm whose backup time is still ahead and that hasn't rung. */
    fun nextBackup(list: List<Planned>, nowMs: Long, rung: Map<String, Long>): Planned? =
        list.filter { it.at + BACKUP_AFTER_MS > nowMs && !wasRung(it, rung) }.minByOrNull { it.at }

    /** The alarm the app should ring itself now (its backup time came, it hasn't rung, it's not past ringing). */
    fun dueNow(list: List<Planned>, nowMs: Long, rung: Map<String, Long>, ringMs: Long): Planned? =
        list.filter { nowMs >= it.at + BACKUP_AFTER_MS - 5_000 && nowMs < it.at + ringMs && !wasRung(it, rung) }.minByOrNull { it.at }
}
