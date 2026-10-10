package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.ActivityEntry
import ca.nimaste.newman.core.model.CameraAlert
import ca.nimaste.newman.core.model.Dashboard
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.Meta
import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.model.PushCategory
import kotlinx.serialization.builtins.ListSerializer

/** JSON captured read-only from the live hub (GET /api/meta, /api/entities, …), tokens scrubbed. */
object Fixtures {
    fun text(path: String): String =
        Fixtures::class.java.classLoader!!.getResourceAsStream(path)!!.bufferedReader().use { it.readText() }

    fun bytes(path: String): ByteArray =
        Fixtures::class.java.classLoader!!.getResourceAsStream(path)!!.use { it.readBytes() }

    val meta: Meta by lazy { NewmanJson.decodeFromString(Meta.serializer(), text("hub/meta.json")) }
    val entities: List<Entity> by lazy { NewmanJson.decodeFromString(ListSerializer(Entity.serializer()), text("hub/entities.json")) }
    val dashboards: List<Dashboard> by lazy { NewmanJson.decodeFromString(ListSerializer(Dashboard.serializer()), text("hub/dashboards.json")) }
    val events: List<ActivityEntry> by lazy { NewmanJson.decodeFromString(ListSerializer(ActivityEntry.serializer()), text("hub/events.json")) }
    val alerts: List<CameraAlert> by lazy { NewmanJson.decodeFromString(ListSerializer(CameraAlert.serializer()), text("hub/alerts.json")) }
    val categories: List<PushCategory> by lazy { NewmanJson.decodeFromString(ListSerializer(PushCategory.serializer()), text("hub/categories.json")) }

    fun entity(kind: String): Entity = entities.first { it.kind == kind }
}
