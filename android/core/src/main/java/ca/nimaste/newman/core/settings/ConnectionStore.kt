package ca.nimaste.newman.core.settings

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import ca.nimaste.newman.core.net.SavedHub
import ca.nimaste.newman.core.net.ServerAddress
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.UUID

/**
 * How to reach the hub and who is signed in, kept in EncryptedSharedPreferences: the hub (its id,
 * name, home-network and outside addresses), the address in use right now ([serverUrl]), the optional Cloudflare Access service token, the session token and this install's
 * device id. (The client certificate lives separately in MTLSStore so it survives sign-out.)
 */
class ConnectionStore private constructor(private val prefs: SharedPreferences) {

    data class Snapshot(
        val serverUrl: String?,
        val cfClientId: String?,
        val cfClientSecret: String?,
        val token: String?,
        val userName: String?,
        val hub: SavedHub = SavedHub(null, null, null, null),
    ) {
        val hasCloudflare: Boolean get() = !cfClientId.isNullOrBlank() && !cfClientSecret.isNullOrBlank()
    }

    private val _changes = MutableStateFlow(read())
    /** Re-emits whenever anything here changes (server, token…). */
    val changes: StateFlow<Snapshot> = _changes.asStateFlow()

    private fun read() = Snapshot(
        serverUrl = prefs.getString(KEY_SERVER, null),
        cfClientId = prefs.getString(KEY_CF_ID, null),
        cfClientSecret = prefs.getString(KEY_CF_SECRET, null),
        token = prefs.getString(KEY_TOKEN, null),
        userName = prefs.getString(KEY_USER_NAME, null),
        hub = SavedHub(prefs.getString(KEY_HUB_ID, null), prefs.getString(KEY_HUB_NAME, null),
            prefs.getString(KEY_LOCAL, null), prefs.getString(KEY_OUTSIDE, null)),
    )

    private fun edit(block: SharedPreferences.Editor.() -> Unit) {
        prefs.edit().apply(block).apply()
        _changes.value = read()
    }

    val snapshot: Snapshot get() = _changes.value

    init {
        migrate()
    }

    /** Before hubs had two addresses there was one: keep it as the home or the outside address. */
    private fun migrate() {
        val server = prefs.getString(KEY_SERVER, null) ?: return
        if (prefs.contains(KEY_LOCAL) || prefs.contains(KEY_OUTSIDE)) return
        val home = server.toHttpUrlOrNull()?.let { ServerAddress.isPrivateHost(it.host) } == true
        edit { putString(if (home) KEY_LOCAL else KEY_OUTSIDE, server) }
    }

    /** The hub this app is connected to (null addresses when it isn't yet). */
    val hub: SavedHub get() = snapshot.hub

    /** True once a hub has been chosen ("Connect to your Newman" is done). */
    val hasHub: Boolean get() = snapshot.serverUrl != null || hub.localUrl != null || hub.outsideUrl != null

    /** Save the hub just connected to, and use [active] (the address that answered) from now on. */
    fun saveHub(hub: SavedHub, active: String?) = edit {
        putString(KEY_HUB_ID, hub.id)
        putString(KEY_HUB_NAME, hub.name)
        putString(KEY_LOCAL, hub.localUrl?.let { ServerAddress.normalize(it) })
        putString(KEY_OUTSIDE, hub.outsideUrl?.let { ServerAddress.normalize(it) })
        val use = (active ?: hub.localUrl ?: hub.outsideUrl)?.let { ServerAddress.normalize(it) ?: it }
        putString(KEY_SERVER, use)
        putString(KEY_CHOSEN, use)
    }

    /** The address picked when connecting (Connect / Change server); the wall tablet stays on it. Null before it was kept. */
    val chosenUrl: String? get() = prefs.getString(KEY_CHOSEN, null)

    /** Fill in what the hub said about itself (id, name, a home address) without switching address. */
    fun learn(id: String?, name: String?, localUrl: String?) = edit {
        if (id != null && prefs.getString(KEY_HUB_ID, null) == null) putString(KEY_HUB_ID, id)
        if (name != null) putString(KEY_HUB_NAME, name)
        if (localUrl != null && prefs.getString(KEY_LOCAL, null) == null) putString(KEY_LOCAL, ServerAddress.normalize(localUrl))
    }

    var serverUrl: String?
        get() = snapshot.serverUrl
        set(v) = edit { putString(KEY_SERVER, v?.let { ServerAddress.normalize(it) ?: it }) }

    var token: String?
        get() = snapshot.token
        set(v) = edit { putString(KEY_TOKEN, v) }

    var userName: String?
        get() = snapshot.userName
        set(v) = edit { putString(KEY_USER_NAME, v) }

    fun setCloudflare(clientId: String?, clientSecret: String?) = edit {
        putString(KEY_CF_ID, clientId?.trim()?.ifEmpty { null })
        putString(KEY_CF_SECRET, clientSecret?.trim()?.ifEmpty { null })
    }

    /** Stable per install; the hub keys this phone's push registration and button tokens on it. */
    val deviceId: String
        get() = prefs.getString(KEY_DEVICE_ID, null) ?: UUID.randomUUID().toString().also {
            prefs.edit().putString(KEY_DEVICE_ID, it).apply()
        }

    var biometricsEnabled: Boolean
        get() = prefs.getBoolean(KEY_BIOMETRICS, true)
        set(v) = edit { putBoolean(KEY_BIOMETRICS, v) }

    /** Newest activity time the person has seen (for the Alerts badge). */
    var alertsSeenAt: Double
        get() = prefs.getLong(KEY_ALERTS_SEEN, 0L).toDouble()
        set(v) = edit { putLong(KEY_ALERTS_SEEN, kotlin.math.ceil(v).toLong()) }

    /** Last FCM token sent to the hub (re-register when it changes). */
    var pushToken: String?
        get() = prefs.getString(KEY_PUSH_TOKEN, null)
        set(v) = edit { putString(KEY_PUSH_TOKEN, v) }

    /** Cached GET /api/push/categories JSON, for notifications that name a category. */
    var pushCategoriesJson: String?
        get() = prefs.getString(KEY_PUSH_CATEGORIES, null)
        set(v) = edit { putString(KEY_PUSH_CATEGORIES, v) }

    fun signOut() = edit {
        remove(KEY_TOKEN)
        remove(KEY_USER_NAME)
        remove(KEY_PUSH_TOKEN)
    }

    companion object {
        private const val FILE = "newman_connection"
        private const val KEY_SERVER = "server_url"
        private const val KEY_CF_ID = "cf_client_id"
        private const val KEY_CF_SECRET = "cf_client_secret"
        private const val KEY_TOKEN = "session_token"
        private const val KEY_USER_NAME = "user_name"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_BIOMETRICS = "biometrics"
        private const val KEY_ALERTS_SEEN = "alerts_seen_at"
        private const val KEY_PUSH_TOKEN = "push_token"
        private const val KEY_PUSH_CATEGORIES = "push_categories"
        private const val KEY_HUB_ID = "hub_id"
        private const val KEY_HUB_NAME = "hub_name"
        private const val KEY_LOCAL = "hub_local_url"
        private const val KEY_OUTSIDE = "hub_outside_url"
        private const val KEY_CHOSEN = "chosen_url"

        @Volatile private var instance: ConnectionStore? = null

        fun get(context: Context): ConnectionStore = instance ?: synchronized(this) {
            instance ?: ConnectionStore(open(context.applicationContext)).also { instance = it }
        }

        private fun open(context: Context): SharedPreferences = try {
            val key = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
            EncryptedSharedPreferences.create(
                context, FILE, key,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
            )
        } catch (e: Exception) {
            // A broken Keystore (seen after some restores) must not brick the app.
            context.getSharedPreferences("${FILE}_fallback", Context.MODE_PRIVATE)
        }
    }
}
