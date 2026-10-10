package ca.nimaste.newman.core.live

import ca.nimaste.newman.core.model.ActivityEntry
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.model.asDoubleOrNull
import ca.nimaste.newman.core.model.asStringOrNull
import ca.nimaste.newman.core.people.Person
import kotlinx.serialization.json.JsonObject

/**
 * What GET /api/ws sends (api.py live_updates / broadcast):
 * {type:"hello", time, version} · {type:"entity", entity} · {type:"event", entityId, event}
 * · {type:"activity", entry} · {type:"dashboard", …} · {type:"settings"} · {type:"person", person}
 * · {type:"entity-removed", id} · {type:"live", activities, ended} · {type:"pong"}.
 * [Reconnected] is ours: emitted after the socket comes back, so stores reload once.
 */
sealed interface LiveMessage {
    data class Hello(val time: Double?, val version: String?) : LiveMessage
    data class EntityChanged(val entity: Entity) : LiveMessage
    /** {type:"entity-removed", id}: a device was removed from the hub. */
    data class EntityRemoved(val id: String) : LiveMessage
    data class DeviceEvent(val entityId: String, val event: String) : LiveMessage
    data class Activity(val entry: ActivityEntry) : LiveMessage
    data class DashboardChanged(val dashboardId: String?, val userId: String?) : LiveMessage
    data object SettingsChanged : LiveMessage
    /** {type:"person", person}: someone's location changed (the People tab and maps). */
    data class PersonChanged(val person: Person) : LiveMessage
    /** {type:"live", activities, ended}: the signed-in person's live statuses changed (the whole list). */
    data class LiveChanged(val update: ca.nimaste.newman.core.livestatus.LiveUpdate) : LiveMessage
    data object Pong : LiveMessage
    data object Reconnected : LiveMessage
    data class Unknown(val type: String) : LiveMessage

    companion object {
        /** Parses one text frame; null for anything that isn't a typed JSON object. */
        fun parse(text: String): LiveMessage? {
            val obj = try {
                NewmanJson.parseToJsonElement(text) as? JsonObject
            } catch (e: Exception) {
                null
            } ?: return null
            val type = obj["type"].asStringOrNull ?: return null
            return try {
                when (type) {
                    "hello" -> Hello(obj["time"].asDoubleOrNull, obj["version"].asStringOrNull)
                    "entity" -> obj["entity"]?.let { EntityChanged(NewmanJson.decodeFromJsonElement(Entity.serializer(), it)) }
                    "entity-removed" -> obj["id"].asStringOrNull?.takeIf { it.isNotEmpty() }?.let { EntityRemoved(it) }
                    "event" -> DeviceEvent(obj["entityId"].asStringOrNull ?: "", obj["event"].asStringOrNull ?: "")
                    "activity" -> obj["entry"]?.let { Activity(NewmanJson.decodeFromJsonElement(ActivityEntry.serializer(), it)) }
                    "dashboard" -> DashboardChanged(obj["id"].asStringOrNull, obj["userId"].asStringOrNull)
                    "settings" -> SettingsChanged
                    "person" -> obj["person"]?.let { PersonChanged(NewmanJson.decodeFromJsonElement(Person.serializer(), it)) }
                    "live" -> LiveChanged(NewmanJson.decodeFromJsonElement(ca.nimaste.newman.core.livestatus.LiveUpdate.serializer(), obj))
                    "pong" -> Pong
                    else -> Unknown(type)
                }
            } catch (e: Exception) {
                null
            }
        }
    }
}
