package ca.nimaste.newman.core.push

import ca.nimaste.newman.core.model.ActionRequest
import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.model.PushButton
import ca.nimaste.newman.core.model.PushCategory
import ca.nimaste.newman.core.net.HubHttp
import ca.nimaste.newman.core.settings.ConnectionStore
import kotlinx.serialization.builtins.ListSerializer
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * A data-only FCM message from the hub (push/notifier.py _fields): every value is a string.
 * kind (automation | followup | test), run_id, automation_id, title, body, buttons (JSON),
 * snapshot_url?, clip_url?, clip? ("frigate:r:<review id>"), frigate_event_id?, camera?, action_url, action_subject, action_exp, action_token,
 * time_sensitive, thread, category?, collapse_id?, sound?.
 */
data class PushPayload(
    val kind: String,
    val title: String,
    val body: String,
    val runId: String?,
    val automationId: String?,
    val buttons: List<PushButton>,
    val snapshotUrl: String?,
    val clipUrl: String?,
    val actionUrl: String?,
    val actionSubject: String?,
    val actionExp: String?,
    val actionToken: String?,
    val timeSensitive: Boolean,
    val thread: String?,
    val category: String?,
    val collapseId: String?,
    val sound: String?,
    val camera: String?,
    /** A camera alert's clip id ("frigate:r:<review id>"): tapping the notification plays its video. */
    val clip: String? = null,
    /** The alert's review id (frigate_event_id), to find it when the clip id doesn't match. */
    val reviewId: String? = null,
) {
    /** Buttons the hub will accept from this phone (it needs the signed action token). */
    val canAct: Boolean get() = !actionUrl.isNullOrEmpty() && !actionSubject.isNullOrEmpty() && !actionExp.isNullOrEmpty() && !actionToken.isNullOrEmpty()

    /** A stable notification id: one per automation (the hub collapses on it), else per run. */
    val notificationId: Int get() = (collapseId ?: automationId ?: runId ?: "$title|$body").hashCode()

    /** The doorbell chime channel: the event's sound says so (a kind's event `sound`). */
    val isDoorbell: Boolean get() = sound?.contains("doorbell", ignoreCase = true) == true

    fun actionRequest(button: PushButton, deviceId: String): ActionRequest? {
        if (!canAct) return null
        return ActionRequest(button.id, actionSubject!!, deviceId, actionExp!!, actionToken!!)
    }

    companion object {
        fun parse(data: Map<String, String>, categories: List<PushCategory> = emptyList()): PushPayload {
            val category = data["category"]?.ifEmpty { null }
            val buttons = parseButtons(data["buttons"]).ifEmpty {
                categories.firstOrNull { it.id == category }?.buttons ?: emptyList()
            }
            return PushPayload(
                kind = data["kind"] ?: "automation",
                title = data["title"].orEmpty().ifEmpty { "Newman" },
                body = data["body"].orEmpty(),
                runId = data["run_id"]?.ifEmpty { null },
                automationId = data["automation_id"]?.ifEmpty { null },
                buttons = buttons,
                snapshotUrl = data["snapshot_url"]?.ifEmpty { null },
                clipUrl = data["clip_url"]?.ifEmpty { null },
                actionUrl = data["action_url"]?.ifEmpty { null },
                actionSubject = data["action_subject"]?.ifEmpty { null },
                actionExp = data["action_exp"]?.ifEmpty { null },
                actionToken = data["action_token"]?.ifEmpty { null },
                timeSensitive = data["time_sensitive"].equals("true", ignoreCase = true),
                thread = data["thread"]?.ifEmpty { null },
                category = category,
                collapseId = data["collapse_id"]?.ifEmpty { null },
                sound = data["sound"]?.ifEmpty { null },
                camera = data["camera"]?.ifEmpty { null },
                clip = data["clip"]?.ifEmpty { null },
                reviewId = data["frigate_event_id"]?.ifEmpty { null },
            )
        }

        fun parseButtons(json: String?): List<PushButton> = try {
            if (json.isNullOrBlank()) emptyList()
            else NewmanJson.decodeFromString(ListSerializer(PushButton.serializer()), json)
        } catch (e: Exception) {
            emptyList()
        }

        fun parseCategories(json: String?): List<PushCategory> = try {
            if (json.isNullOrBlank()) emptyList()
            else NewmanJson.decodeFromString(ListSerializer(PushCategory.serializer()), json)
        } catch (e: Exception) {
            emptyList()
        }
    }
}

/**
 * Where a pressed notification button is sent, in order: the hub's address the app uses right now
 * (HubSelector's pick, [ConnectionStore.Snapshot.serverUrl]), then the hub's other saved address, and
 * the push's own `action_url` (the hub's fixed public address) only as the last resort. Each try is
 * short ([ATTEMPT_MS]): the hub replies at once and sends device commands afterwards.
 */
object ActionRoutes {
    const val PATH = "api/public/actions"
    const val ATTEMPT_MS = 8_000L

    fun urls(snapshot: ConnectionStore.Snapshot, actionUrl: String?): List<String> {
        val out = LinkedHashSet<String>()
        listOfNotNull(snapshot.serverUrl, snapshot.hub.localUrl, snapshot.hub.outsideUrl).forEach { base ->
            HubHttp.absolute(base, PATH)?.let { out.add(it.toString()) }
        }
        actionUrl?.trim()?.takeIf { it.isNotEmpty() }?.let { out.add(it.toHttpUrlOrNull()?.toString() ?: it) }
        return out.toList()
    }

    /**
     * After a try at one address: whether to try the next. null = no answer at all (offline, timed
     * out, TLS…); a proxy or Cloudflare saying the hub is unreachable counts the same. Any other
     * answer is the hub's own (pressed, or refused with its reason) - never sent again elsewhere.
     */
    fun tryNext(status: Int?): Boolean = status == null || status in UNREACHABLE

    private val UNREACHABLE = setOf(502, 503, 504, 521, 522, 523, 524, 530)
}
