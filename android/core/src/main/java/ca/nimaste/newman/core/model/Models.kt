package ca.nimaste.newman.core.model

import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Shapes of the hub's JSON (server/newman/api.py, kinds.py, dashboards.py). Everything a client
 * needs to draw a device comes from [Meta]; nothing here knows any particular kind of device.
 */

val NewmanJson: Json = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
    coerceInputValues = true
    isLenient = true
    encodeDefaults = true
}

/** A string that may arrive as any JSON primitive (a sensor's state can be 21.5 or "21.5"). */
object LenientStringSerializer : KSerializer<String?> {
    override val descriptor: SerialDescriptor = PrimitiveSerialDescriptor("LenientString", PrimitiveKind.STRING)
    override fun deserialize(decoder: Decoder): String? {
        val json = decoder as? JsonDecoder ?: return decoder.decodeString()
        return when (val el = json.decodeJsonElement()) {
            is JsonNull -> null
            is JsonPrimitive -> el.content
            else -> el.toString()
        }
    }

    override fun serialize(encoder: Encoder, value: String?) {
        if (value == null) encoder.encodeNull() else encoder.encodeString(value)
    }
}

// -- meta ----------------------------------------------------------------------------------------

@Serializable
data class Meta(
    val version: String = "",
    val categories: List<Category> = emptyList(),
    val kinds: List<Kind> = emptyList(),
) {
    val kindsById: Map<String, Kind> by lazy { kinds.associateBy { it.id } }
    fun kind(id: String?): Kind? = id?.let { kindsById[it] }
    fun categoryName(id: String?): String? = categories.firstOrNull { it.id == id }?.name
}

@Serializable
data class Category(val id: String, val name: String = id)

@Serializable
data class KindState(val id: String, val label: String = id)

@Serializable
data class Command(
    val id: String,
    val label: String = id,
    val past: String = "",
    val icon: String = "",
    val confirmsTo: String = "",
    val sensitive: Boolean = false,
    val takesValue: Boolean = false,
    /** percent | number | state | choices | text ("" = a plain one-tap command). */
    val valueType: String = "",
    val min: Double? = null,
    val max: Double? = null,
    val step: Double? = null,
    val unit: String = "",
    val default: JsonElement? = null,
    val attr: String = "",
)

@Serializable
data class KindEvent(
    val id: String,
    val label: String = id,
    val phrase: String = "",
    val title: String = "",
    val body: String = "",
    val sound: String = "",
)

@Serializable
data class PropOption(val value: JsonElement? = null, val label: String = "")

@Serializable
data class PropertyDef(
    val id: String,
    val label: String = id,
    val unit: String = "",
    val attr: String = "",
    val type: String = "",
    val options: List<PropOption> = emptyList(),
)

@Serializable
data class Kind(
    val id: String,
    val name: String = id,
    val plural: String = name,
    val category: String = "other",
    val noun: String = "device",
    val states: List<KindState> = emptyList(),
    val activeState: String? = null,
    val commands: List<Command> = emptyList(),
    val events: List<KindEvent> = emptyList(),
    val hasCamera: Boolean = false,
    val transitional: List<String> = emptyList(),
    val unit: String = "",
    val secureState: String? = null,
    val isMode: Boolean = false,
    val urgent: Boolean = false,
    val properties: List<PropertyDef> = emptyList(),
) {
    fun command(id: String?): Command? = commands.firstOrNull { it.id == id }
}

// -- entities ------------------------------------------------------------------------------------

@Serializable
data class Health(val ok: Boolean = true, val issues: List<String> = emptyList())

/** One value a device reports, with its current value (kinds.properties_for). */
@Serializable
data class Property(
    val id: String,
    val label: String = id,
    val unit: String = "",
    val attr: String = "",
    val type: String = "",
    val options: List<PropOption> = emptyList(),
    val value: JsonElement? = null,
)

@Serializable
data class Entity(
    val id: String,
    val name: String = id,
    val kind: String = "",
    val category: String = "",
    val integration: String = "",
    val integrationName: String = "",
    @Serializable(with = LenientStringSerializer::class)
    val state: String? = null,
    val stateLabel: String = "",
    val available: Boolean = true,
    val attrs: JsonObject = JsonObject(emptyMap()),
    val health: Health = Health(),
    val properties: List<Property> = emptyList(),
    /** Relative to the server ("api/entities/x/still.jpg"); null when the device has no picture. */
    val still: String? = null,
    val alerts: String? = null,
    val live: String? = null,
    val usedBy: List<String> = emptyList(),
    val updatedAt: Double = 0.0,
)

// -- dashboards ----------------------------------------------------------------------------------

@Serializable
data class Dashboard(
    val id: String = "",
    val userId: String = "",
    val isDefault: Boolean = false,
    val name: String = "",
    val theme: String = "",
    val pages: List<Page> = emptyList(),
    /** "Show as" names on this dashboard only: {entity id: name}. */
    val names: Map<String, String> = emptyMap(),
)

@Serializable
data class Page(
    val id: String,
    val name: String = "Page",
    val icon: String = "home",
    val tiles: List<Tile> = emptyList(),
    /** Phone layouts only: this page is a bottom tab (PhoneGrid). */
    val tab: Boolean = false,
)

@Serializable
data class Tile(
    val id: String,
    val type: String,
    val x: Int = 0,
    val y: Int = 0,
    val w: Int = 1,
    val h: Int = 1,
    val config: JsonObject = JsonObject(emptyMap()),
    /** Phone layouts: the tile's text size in % of normal (dashboards keep it in config.textSize). */
    val textSize: Double? = null,
) {
    /** Text scale from [textSize] or config.textSize, clamped like the wall (1 = normal). */
    val textScale: Float
        get() {
            val v = textSize ?: config.number("textSize") ?: return 1f
            return if (v > 0 && v != 100.0) (v / 100.0).toFloat().coerceIn(0.5f, 1.6f) else 1f
        }
}

// -- accounts --------------------------------------------------------------------------------------

@Serializable
data class User(
    val id: String = "",
    val username: String = "",
    val name: String = "",
    val role: String = "",
    val personId: String? = null,
    val twoFactor: Boolean = false,
    val hasPin: Boolean = false,
    val disabled: Boolean = false,
)

@Serializable
data class SessionInfo(
    val id: String = "",
    val kind: String = "",
    val deviceName: String = "",
    val where: String? = null,
    val certificate: Boolean = false,
)

@Serializable
data class AuthState(
    val setupNeeded: Boolean = false,
    val signedIn: Boolean = false,
    val user: User? = null,
    val session: SessionInfo? = null,
    /** "home" | "away" */
    val where: String = "home",
    val certificate: Boolean = false,
    val awayPolicy: String = "",
    val tabletSensitive: String = "",
    val time: Double = 0.0,
    val version: String = "",
)

@Serializable
data class LoginRequest(
    val username: String,
    val password: String,
    val code: String? = null,
    val kind: String = "app",
    val deviceName: String,
    val remember: Boolean = true,
)

@Serializable
data class LoginResponse(val ok: Boolean = false, val user: User? = null, val token: String? = null)

@Serializable
data class RegisterRequest(
    val token: String,
    val platform: String = "android",
    val deviceId: String,
    val friendlyName: String = "",
    val deviceModel: String = "",
    val personId: String? = null,
)

@Serializable
data class RegisterResponse(val ok: Boolean = false, val personId: String? = null)

@Serializable
data class CommandRequest(val command: String, val value: JsonElement? = null)

@Serializable
data class OkResponse(val ok: Boolean = false, val error: String? = null)

// -- activity, alerts, push -------------------------------------------------------------------------

@Serializable
data class ActivityEntry(
    val id: Long = 0,
    val ts: Double = 0.0,
    val kind: String = "",
    val title: String = "",
    val body: String = "",
    val entityId: String? = null,
    val data: JsonObject? = null,
)

@Serializable
data class CameraAlert(
    val id: String,
    val camera: String = "",
    val cameraName: String = "",
    val entityId: String? = null,
    val severity: String? = null,
    val title: String = "",
    val objects: List<String> = emptyList(),
    val zones: List<String> = emptyList(),
    val start: Double? = null,
    val end: Double? = null,
    val clip: String? = null,
    val thumbUrl: String? = null,
    val snapshotUrl: String? = null,
    val integration: String? = null,
)

@Serializable
data class PushButton(val id: String, val label: String = id, val icon: String = "", val sensitive: Boolean = false)

@Serializable
data class PushCategory(val id: String, val buttons: List<PushButton> = emptyList())

/** Body of POST /api/public/actions (a notification button). */
@Serializable
data class ActionRequest(
    val button: String,
    val subject: String,
    val deviceId: String,
    val exp: String,
    val token: String,
)
