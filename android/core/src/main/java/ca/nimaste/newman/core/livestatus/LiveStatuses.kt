package ca.nimaste.newman.core.livestatus

import ca.nimaste.newman.core.model.NewmanJson
import kotlinx.serialization.Serializable

/**
 * Live statuses (server/newman/live.py): devices shown while they matter, as an ongoing notification on
 * Android (a Live Activity on iPhone). The hub sends the signed-in person's whole list - over the live
 * socket ({"type": "live", "activities", "ended"}), from GET /api/live/current, and per change as an FCM
 * data message ({"type": "live", "event", "key", "slot", "title", "state", "dismissAt"?}) when it has FCM.
 * Everything here is pure: what each one looks like as a notification ([LiveNotifications.spec]) and what
 * to post / cancel for a new list ([LiveNotifications.plan]). Nothing knows a kind of device: labels,
 * icons and buttons come from the hub (kinds.py).
 */

@Serializable
data class LiveAction(val command: String, val label: String = command, val sensitive: Boolean = false)

@Serializable
data class LiveDevice(
    val id: String,
    val name: String = id,
    val icon: String = "",
    val stateLabel: String = "",
    val active: Boolean = false,
    val actions: List<LiveAction> = emptyList(),
)

/** The content-state (the same JSON an iPhone's activity gets). */
@Serializable
data class LiveContent(
    val title: String = "",
    val subtitle: String = "",
    /** When it started (epoch seconds): the notification counts up from it. */
    val since: Long = 0,
    val active: Boolean = true,
    val devices: List<LiveDevice> = emptyList(),
    /** How many more beyond the devices listed: devices.size + more = everything it's about. */
    val more: Int = 0,
)

@Serializable
data class LiveEntry(
    /** Changes when the hub restarts an activity (iPhones' 8-hour limit); [slot] stays. */
    val key: String,
    /** One per live status ("device:<id>", "automation:<id>", "more"): the notification's tag. */
    val slot: String,
    val title: String = "",
    val contentState: LiveContent = LiveContent(),
    /** Ended ones: "ended" (really over) or "moved" (shown on, elsewhere), and when to take it away. */
    val reason: String? = null,
    val dismissAt: Long? = null,
)

@Serializable
data class LiveUpdate(val activities: List<LiveEntry> = emptyList(), val ended: List<LiveEntry> = emptyList()) {
    companion object {
        /** An FCM data message ({"type": "live", "event": "start"|"update"|"end", …}) as one change. */
        fun fromPush(data: Map<String, String>): Pair<String, LiveEntry>? {
            if (data["type"] != "live") return null
            val key = data["key"] ?: return null
            val slot = data["slot"] ?: return null
            val state = try {
                NewmanJson.decodeFromString(LiveContent.serializer(), data["state"] ?: "{}")
            } catch (e: Exception) {
                return null
            }
            val event = data["event"] ?: "update"
            return event to LiveEntry(key, slot, data["title"] ?: state.title, state,
                reason = if (event == "end") "ended" else null, dismissAt = data["dismissAt"]?.toLongOrNull())
        }
    }
}

/** One button on the notification: [command] on device [deviceId]. */
data class LiveButton(val deviceId: String, val command: String, val label: String, val sensitive: Boolean)

/** How one live status looks as a notification. */
data class LiveSpec(
    val tag: String,
    val title: String,
    val text: String,
    /** Expanded: one line per device ("Garage Door · Open"), and "and 3 more". */
    val lines: List<String>,
    val ongoing: Boolean,
    /** Counts up from here (epoch millis); null for an ended one. */
    val sinceMillis: Long?,
    val buttons: List<LiveButton>,
    /** Tapping it opens this device (null: the app). */
    val openDevice: String?,
    /** An ended one goes after this long (0: straight away). */
    val timeoutMillis: Long?,
)

/** What to do with the shown notifications for a new list. */
data class LivePlan(val post: List<LiveSpec>, val cancel: List<String>)

object LiveNotifications {
    const val MAX_BUTTONS = 3

    fun total(c: LiveContent): Int = c.devices.size + c.more

    fun spec(e: LiveEntry, ended: Boolean = false, nowMillis: Long = System.currentTimeMillis()): LiveSpec {
        val c = e.contentState
        val many = c.devices.size > 1 || c.more > 0
        val lines = c.devices.map { d -> if (d.stateLabel.isEmpty()) d.name else "${d.name} · ${d.stateLabel}" } +
            (if (c.more > 0) listOf("and ${c.more} more") else emptyList())
        val buttons = if (ended) emptyList() else c.devices.flatMap { d ->
            d.actions.map { a -> LiveButton(d.id, a.command, if (many) "${a.label} ${d.name}" else a.label, a.sensitive) }
        }.take(MAX_BUTTONS)
        val timeout = if (ended) ((e.dismissAt ?: 0L) * 1000 - nowMillis).coerceAtLeast(0) else null
        return LiveSpec(
            tag = e.slot,
            title = c.title.ifEmpty { e.title },
            text = c.subtitle.ifEmpty { c.devices.firstOrNull()?.stateLabel ?: "" },
            lines = if (many) lines else emptyList(),
            ongoing = !ended,
            sinceMillis = if (ended || c.since <= 0) null else c.since * 1000,
            buttons = buttons,
            openDevice = c.devices.singleOrNull()?.id?.takeIf { c.more == 0 && e.slot != "more" },
            timeoutMillis = timeout,
        )
    }

    /**
     * For a whole new list, given the tags shown now: post every activity, post ended ones that are
     * really over (they stay a moment with their final state), cancel the rest - moved ones (now inside
     * "N more", or restarted under the same tag) and whatever the hub no longer lists.
     */
    fun plan(shown: Set<String>, update: LiveUpdate, nowMillis: Long = System.currentTimeMillis()): LivePlan {
        val post = update.activities.map { spec(it, nowMillis = nowMillis) }.toMutableList()
        val live = post.map { it.tag }.toSet()
        val endings = update.ended.filter { it.slot !in live && it.reason != "moved" }
            .map { spec(it, ended = true, nowMillis = nowMillis) }
            .filter { (it.timeoutMillis ?: 0) > 0 }
        post += endings
        val keep = post.map { it.tag }.toSet()
        return LivePlan(post, (shown - keep).sorted())
    }

    /** One FCM change (no whole list): start / update posts it, end shows its final state or cancels it. */
    fun planOne(event: String, e: LiveEntry, nowMillis: Long = System.currentTimeMillis()): LivePlan {
        if (event != "end") return LivePlan(listOf(spec(e, nowMillis = nowMillis)), emptyList())
        val s = spec(e, ended = true, nowMillis = nowMillis)
        return if ((s.timeoutMillis ?: 0) > 0) LivePlan(listOf(s), emptyList()) else LivePlan(emptyList(), listOf(e.slot))
    }

    fun parse(json: String): LiveUpdate = NewmanJson.decodeFromString(LiveUpdate.serializer(), json)
}
