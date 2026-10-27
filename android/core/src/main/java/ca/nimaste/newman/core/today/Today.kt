package ca.nimaste.newman.core.today

import ca.nimaste.newman.core.model.TileFit
import kotlinx.serialization.Serializable

/**
 * GET /api/today (server/newman/today.py): what needs someone today, what's on and heads-up, worked
 * out on the hub so the wall, the phone and voice say the same. On a phone the hub sends this
 * person's items and everyone's. Done / Undo / Remind tomorrow go to /api/tasks/{done|undo|snooze} with
 * the item's id (GCloud Calendar tasks); the hub records the signed-in person as who did it.
 */
@Serializable
data class TodayItem(
    val id: String,
    /** "task" | "device" | "appliance" */
    val source: String = "",
    val entity: String? = null,
    val title: String = "",
    val status: String = "",
    /** "overdue" | "device" | "due" | "appliance" | "soon" */
    val level: String = "",
    /** "done" | "open" | "dismiss" */
    val action: String = "",
    val owner: String = "",
    val icon: String = "",
)

@Serializable
data class TodayEvent(
    val title: String = "",
    val allDay: Boolean = false,
    val time: String = "",
    val start: String = "",
    val end: String? = null,
    val location: String = "",
    val calendar: String = "",
    val colour: String = "#3CCFB4",
    val now: Boolean = false,
)

@Serializable
data class HeadsUp(
    val id: String = "",
    /** "weather" | "calendar" | "task" */
    val source: String = "",
    /** "warning" | "rain" | "calendar" | "clock" */
    val icon: String = "",
    val text: String = "",
    val entity: String? = null,
    /** A few lower-case words for a one-line summary ("rain from 3 PM"). */
    val short: String = "",
    /** A weather alert's type: "warning" | "watch" | "advisory" | "statement". */
    val level: String = "",
)

@Serializable
data class TodayBriefing(
    val date: String = "",
    val todo: List<TodayItem> = emptyList(),
    val soon: List<TodayItem> = emptyList(),
    val events: List<TodayEvent> = emptyList(),
    val headsUp: List<HeadsUp> = emptyList(),
) {
    val isEmpty: Boolean get() = todo.isEmpty() && soon.isEmpty() && events.isEmpty() && headsUp.isEmpty()
}

/** Body of POST /api/tasks/done, /undo and /snooze: the To do item's id (and for snooze, how many days). */
@Serializable
data class TaskRequest(val id: String, val days: Int? = null)

/** GET /api/tasks/calendars: the calendars holding GCloud tasks and the household (Today's "+ Add"). */
@Serializable
data class TaskCalendars(val calendars: List<Entry> = emptyList(), val people: List<Member> = emptyList()) {
    @Serializable
    data class Entry(val id: String, val name: String = "", val writable: Boolean = true)

    @Serializable
    data class Member(val id: String, val name: String = "")

    /** Where "+ Add" can put a task: the calendars Newman may write. */
    val writable: List<Entry> get() = calendars.filter { it.writable }
}

/** Body of POST /api/tasks: a new GCloud task ("due": "2026-10-30"; "owner": a person's id or ""). */
@Serializable
data class NewTask(val calendar: String, val title: String, val due: String, val every: Int, val unit: String, val owner: String = "")

/** Body of POST /api/today/dismiss. */
@Serializable
data class DismissRequest(val id: String)

/** The phone's Today card, from the briefing plus what was just done on this phone. */
object TodayView {
    /** Rows of To do shown before "N more" (the phone avoids long lists). */
    const val ROWS = 4
    const val EVENTS = 3
    const val HEADS_UP = 2
    /** A row just marked done stays (struck through, with Undo) this long. */
    const val KEEP_DONE_SECONDS = 600.0

    enum class Tone { URGENT, DUE, LATER, DONE }

    enum class Button { DONE, SNOOZE, UNDO, OPEN, DISMISS }

    data class Row(
        val id: String,
        val title: String,
        val status: String,
        val tone: Tone,
        val buttons: List<Button>,
        val entity: String?,
        val item: TodayItem,
    )

    /** Something done on this phone (kept for Undo). [taskId] is null for a dismissed appliance need. */
    data class Done(val item: TodayItem, val taskId: String?, val at: Double)

    data class Card(
        val summary: String,
        val rows: List<Row>,
        val more: Int,
        val events: List<TodayEvent>,
        val headsUp: List<HeadsUp>,
        val empty: Boolean,
    )

    fun greeting(hour: Int): String = when {
        hour < 12 -> "Good morning"
        hour < 18 -> "Good afternoon"
        else -> "Good evening"
    }

    /**
     * "3 things to do · 2 events today · rain from 3 PM" (the wall's greeting line, web
     * TodayPage.jsx summaryLine): open To do (less what was just done here), today's events, the most
     * serious weather alert and rain.
     */
    fun greetingLine(b: TodayBriefing, done: Map<String, Done>, now: Double): String {
        val recent = done.filterValues { now - it.at < KEEP_DONE_SECONDS }
        val open = b.todo.count { it.id !in recent }
        val events = b.events.size
        val weather = b.headsUp.filter { it.source == "weather" }
        // The most serious official alert: a warning before a watch, an advisory or a statement.
        val warning = weather.filter { it.icon == "warning" }.minByOrNull { alertRank(it.level) }
        val rain = weather.firstOrNull { it.icon == "rain" }
        return listOfNotNull(
            if (open > 0) "$open ${if (open == 1) "thing" else "things"} to do" else "Nothing to do",
            if (events > 0) "$events ${if (events == 1) "event" else "events"} today" else null,
            warning?.let(::shortText),
            rain?.let(::shortText),
        ).filter { it.isNotEmpty() }.joinToString(" · ")
    }

    private fun alertRank(level: String): Int = when (level) {
        "warning" -> 1
        "watch" -> 2
        "advisory" -> 3
        else -> 9
    }

    /** A heads-up in a few words: the hub's "short", else its text in lower case. */
    fun shortText(h: HeadsUp): String = h.short.ifBlank { h.text.lowercase() }

    /** The greeting tile's look for a card [height] dp high inside: the greeting's size, and whether the line fits under it. */
    data class GreetingFit(val size: Float, val twoLines: Boolean)

    fun greetingFit(height: Float): GreetingFit {
        val size = if (height == Float.POSITIVE_INFINITY) 26f else Math.round(height * 0.42f).toFloat().coerceIn(14f, 26f)
        return GreetingFit(size, height >= size * 1.3f + TileFit.LINE)
    }

    fun tone(level: String): Tone = when (level) {
        "overdue", "device" -> Tone.URGENT
        "due", "appliance" -> Tone.DUE
        else -> Tone.LATER
    }

    fun buttons(item: TodayItem): List<Button> = when (item.action) {
        "open" -> listOf(Button.OPEN)
        "dismiss" -> listOf(Button.DISMISS)
        else -> if (item.source == "task") listOf(Button.DONE, Button.SNOOZE) else listOf(Button.DONE)
    }

    /** "2 things to do · 1 event", "All done for today", or "" when there's nothing at all. */
    fun summary(open: Int, doneHere: Int, events: Int): String = listOf(
        when {
            open > 0 -> "$open ${if (open == 1) "thing" else "things"} to do"
            doneHere > 0 -> "All done for today"
            else -> ""
        },
        if (events > 0) "$events ${if (events == 1) "event" else "events"}" else "",
    ).filter { it.isNotEmpty() }.joinToString(" · ")

    /**
     * The card: open To do first (most urgent, as the hub ordered it), rows just done here kept in
     * place with Undo, then what's coming up if there's room; the rest counted in [Card.more].
     */
    fun card(b: TodayBriefing, done: Map<String, Done>, now: Double, rows: Int = ROWS): Card {
        val recent = done.filterValues { now - it.at < KEEP_DONE_SECONDS }
        val open = b.todo.filter { it.id !in recent }
        val soon = b.soon.filter { it.id !in recent }
        val doneRows = recent.values.sortedBy { it.at }.map { d ->
            Row(d.item.id, d.item.title, "Done just now", Tone.DONE, if (d.taskId != null) listOf(Button.UNDO) else emptyList(), d.item.entity, d.item)
        }
        fun row(i: TodayItem) = Row(i.id, i.title, listOf(i.status, i.owner).filter { it.isNotBlank() }.joinToString(" · "),
            tone(i.level), buttons(i), i.entity, i)
        val openShown = open.take((rows - doneRows.size).coerceAtLeast(1))
        val soonShown = soon.take((rows - openShown.size - doneRows.size).coerceAtLeast(0))
        val shown = openShown.map(::row) + doneRows + soonShown.map(::row)
        val more = open.size - openShown.size + soon.size - soonShown.size
        return Card(
            summary = summary(open.size, doneRows.size, b.events.size),
            rows = shown,
            more = more,
            events = b.events.take(EVENTS),
            headsUp = b.headsUp.take(HEADS_UP),
            empty = b.isEmpty && doneRows.isEmpty(),
        )
    }
}
