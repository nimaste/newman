package ca.nimaste.newman.core.model

import kotlinx.serialization.Serializable
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

/** One automation from GET /api/automations (the hub sends more than the phone reads). */
@Serializable
data class Automation(
    val id: String,
    val name: String = "",
    val enabled: Boolean = true,
    /** The hub's own plain-English description ("When … is pressed, turn off …"). */
    val summary: String = "",
    val lastRun: AutomationRun? = null,
)

/** One run: an automation's lastRun, GET /api/runs, POST /api/automations/{id}/run's answer. */
@Serializable
data class AutomationRun(
    val id: String = "",
    /** Unix seconds. */
    val startedAt: Double? = null,
    /** "done" | "sent" (waiting on answers) | "skipped" (nothing to do) | "dismissed" | "expired" | "resolved" */
    val status: String = "",
)

/** The phone's Automations screen (More › Automations): order and words, from what the hub sends. */
object Automations {

    /** Most recently run first; never-run last (in the hub's order). */
    fun sorted(list: List<Automation>): List<Automation> =
        list.sortedByDescending { it.lastRun?.startedAt ?: Double.NEGATIVE_INFINITY }

    fun ranToday(list: List<Automation>, now: Instant, zone: ZoneId): Int {
        val today = now.atZone(zone).toLocalDate()
        return list.count { a -> a.lastRun?.startedAt?.let { day(it, zone) == today } == true }
    }

    /** "13 automations · all on" (the More row), plus " · 4 ran today" on the screen itself. */
    /** More's Automations row (both apps): "9 · all on", "9 · 2 off". */
    fun moreLine(list: List<Automation>): String {
        val off = list.count { !it.enabled }
        return "${list.size} · " + if (off == 0) "all on" else "$off off"
    }

    fun summary(list: List<Automation>, now: Instant? = null, zone: ZoneId = ZoneId.systemDefault()): String {
        val n = list.size
        val parts = mutableListOf(if (n == 1) "1 automation" else "$n automations")
        if (n > 0) {
            val off = list.count { !it.enabled }
            parts += when (off) {
                0 -> "all on"
                n -> "all off"
                else -> "$off off"
            }
        }
        if (now != null) ranToday(list, now, zone).takeIf { it > 0 }?.let { parts += "$it ran today" }
        return parts.joinToString(" · ")
    }

    /** "today 2:54 PM" / "yesterday 6:20 PM" / "Oct 12, 2:53 PM". */
    fun whenText(t: Double, now: Instant, zone: ZoneId, locale: Locale = Locale.getDefault()): String {
        val at = Instant.ofEpochMilli((t * 1000).toLong()).atZone(zone)
        val time = at.format(DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(locale))
        val today = now.atZone(zone).toLocalDate()
        return when (at.toLocalDate()) {
            today -> "today $time"
            today.minusDays(1) -> "yesterday $time"
            else -> at.format(DateTimeFormatter.ofPattern("MMM d", locale)) + ", " + time
        }
    }

    /** The line under an automation: when it last ran, and how it went. */
    fun lastRunText(run: AutomationRun?, now: Instant, zone: ZoneId, locale: Locale = Locale.getDefault()): String {
        val t = run?.startedAt ?: return "Hasn't run yet"
        val w = whenText(t, now, zone, locale)
        return when (run.status) {
            "sent" -> "Sent $w"
            "skipped" -> "Checked $w · nothing to do"
            "dismissed" -> "Dismissed $w"
            else -> "Ran $w"
        }
    }

    /** What "Run now" answered, for a short message. */
    fun runResult(name: String, run: AutomationRun?): String = when (run?.status) {
        "skipped" -> "$name: nothing to do right now"
        "sent" -> "$name: sent"
        else -> "Ran $name"
    }

    private fun day(t: Double, zone: ZoneId): LocalDate = Instant.ofEpochMilli((t * 1000).toLong()).atZone(zone).toLocalDate()
}

/**
 * The phone's Devices screen (More › Devices): every device the hub has, grouped by category
 * (the hub's categories, in /api/meta's order), searchable. Nothing here knows a kind by its id.
 */
object DeviceList {
    enum class Filter { ALL, ATTENTION, ON }

    data class Section(val id: String, val title: String, val devices: List<Entity>)

    /** Offline, or the hub reports a problem with it (health). */
    fun needsAttention(e: Entity): Boolean = !e.health.ok || !e.available

    /** On / running now (an active, non-security device - HubModel.onCount's rule). */
    fun isOn(e: Entity, meta: Meta?): Boolean = HubModel.onCount(listOf(e), meta) == 1

    fun categoryOf(e: Entity, meta: Meta?): String =
        e.category.ifEmpty { meta?.kind(e.kind)?.category ?: "" }.ifEmpty { "other" }

    fun matches(e: Entity, query: String): Boolean {
        val q = query.trim()
        return q.isEmpty() || e.name.contains(q, ignoreCase = true)
    }

    fun filtered(list: Collection<Entity>, meta: Meta?, query: String, filter: Filter): List<Entity> = list.filter { e ->
        matches(e, query) && when (filter) {
            Filter.ALL -> true
            Filter.ATTENTION -> needsAttention(e)
            Filter.ON -> isOn(e, meta)
        }
    }

    /** Sections in the hub's category order (unknown categories after, as they first appear); devices needing attention first. */
    fun sections(list: Collection<Entity>, meta: Meta?, query: String = "", filter: Filter = Filter.ALL): List<Section> {
        val byCat = filtered(list, meta, query, filter).groupBy { categoryOf(it, meta) }
        val known = meta?.categories?.map { it.id } ?: emptyList()
        val order = known.filter { it in byCat } + byCat.keys.filter { it !in known }
        return order.map { id ->
            Section(id, meta?.categoryName(id) ?: title(id), byCat.getValue(id).sortedBy { if (needsAttention(it)) 0 else 1 })
        }
    }

    /** "80 devices · 1 needs attention" (the More row). */
    fun summary(list: Collection<Entity>): String {
        val n = list.size
        val bad = list.count(::needsAttention)
        val head = if (n == 1) "1 device" else "$n devices"
        return if (bad == 0) head else "$head · ${if (bad == 1) "1 needs" else "$bad need"} attention"
    }

    /** The status line: the device's issues when it has some, else its state in words. */
    fun statusText(e: Entity, k: Kind?): String =
        if (needsAttention(e) && e.health.issues.isNotEmpty()) e.health.issues.joinToString(" · ")
        else HubModel.entityStateText(e, k).ifEmpty { HubModel.stateLabel(e, k) ?: "" }

    private fun title(id: String) = id.replace('_', ' ').replaceFirstChar { it.uppercase() }
}
