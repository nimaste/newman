package ca.nimaste.newman.widgets

import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import android.util.Log
import androidx.glance.appwidget.updateAll
import ca.nimaste.newman.core.api.HubException
import ca.nimaste.newman.core.api.ApiErrors
import ca.nimaste.newman.core.api.NewmanApi
import ca.nimaste.newman.core.api.hubCall
import ca.nimaste.newman.core.hub.HubRepository
import ca.nimaste.newman.core.live.LiveMessage
import ca.nimaste.newman.core.model.CommandRequest
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.core.widgets.WidgetAction
import ca.nimaste.newman.core.widgets.WidgetDevice
import ca.nimaste.newman.core.widgets.WidgetHome
import ca.nimaste.newman.core.widgets.WidgetTiles
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import javax.inject.Inject
import javax.inject.Singleton

/**
 * What the home-screen widgets and Quick Settings tiles show, and running their taps.
 *
 * Each widget keeps its devices (in order) and its "Confirm first for sensitive actions" switch; each of
 * the three tiles its one device. The devices' last states (GET /api/widgets/state) are cached here, so
 * a widget draws straight away and while the hub can't be reached. Fresh states come on a tap, when the
 * app's live connection hears about one of the shown devices, from FCM when the build has it, and every
 * 15 minutes (WidgetRefreshWorker). Calls go through the app's own client (mTLS, Cloudflare, the hub's
 * home or outside address).
 */
@Singleton
class WidgetSync @Inject constructor(
    @ApplicationContext private val context: Context,
    private val api: NewmanApi,
    private val store: ConnectionStore,
    private val hub: HubRepository,
    val scope: CoroutineScope,
    private val shortcuts: LauncherShortcuts,
) {
    /** Devices the Device Controls panel shows right now (NewmanControlsService), refreshed like widgets. */
    private val watched = java.util.concurrent.ConcurrentHashMap<String, Int>()

    /** While the controls panel shows [ids]: the live connection refreshes them too. Call the result to stop. */
    fun watch(ids: List<String>): () -> Unit {
        ids.forEach { watched.merge(it, 1, Int::plus) }
        return { ids.forEach { id -> watched.computeIfPresent(id) { _, n -> if (n <= 1) null else n - 1 } } }
    }

    /** Everything to refresh when the live connection hears of a change (the Home status widget's devices too). */
    private fun liveIds(): List<String> =
        (deviceIds() + (if (homeWidgetIds().isNotEmpty()) home()?.ids.orEmpty() else emptyList())).distinct()

    /** The devices whose states to fetch: the widgets', the tiles' and the controls panel's. */
    private fun deviceIds(): List<String> = (shownIds() + watched.keys).distinct()

    private val prefs = context.getSharedPreferences("newman_widgets", Context.MODE_PRIVATE)
    private val _version = MutableStateFlow(0)
    /** Bumps whenever anything a widget or tile shows changes. */
    val version: StateFlow<Int> = _version.asStateFlow()
    private val lock = Mutex()
    private var liveJob: Job? = null
    private var soon: Job? = null

    val signedIn: Boolean get() = !store.token.isNullOrEmpty() && store.hasHub

    // -- what each widget / tile shows --------------------------------------------------------------

    /** Every device widget (tiles and rows). */
    fun widgetIds(): IntArray = AppWidgetManager.getInstance(context).let { m ->
        m.getAppWidgetIds(ComponentName(context, DeviceGridReceiver::class.java)) +
            m.getAppWidgetIds(ComponentName(context, DeviceRowsReceiver::class.java))
    }

    /** Every Home status widget (nothing to choose: they all show the whole house). */
    fun homeWidgetIds(): IntArray = AppWidgetManager.getInstance(context)
        .getAppWidgetIds(ComponentName(context, HomeStatusReceiver::class.java))

    /** Something on the home screen or in Quick Settings needs fresh states. */
    fun anyShown(): Boolean = shownIds().isNotEmpty() || homeWidgetIds().isNotEmpty()

    fun devicesOf(widgetId: Int): List<String> =
        prefs.getString("w.$widgetId.ids", null)?.split('\n')?.filter { it.isNotEmpty() } ?: emptyList()

    fun isConfigured(widgetId: Int): Boolean = prefs.contains("w.$widgetId.ids")

    fun confirmSensitive(widgetId: Int): Boolean = prefs.getBoolean("w.$widgetId.confirm", false)

    fun setWidget(widgetId: Int, ids: List<String>, confirm: Boolean) {
        prefs.edit().putString("w.$widgetId.ids", ids.joinToString("\n")).putBoolean("w.$widgetId.confirm", confirm).apply()
        changed()
        WidgetRefreshWorker.schedule(context)
    }

    fun removeWidgets(ids: IntArray) {
        prefs.edit().apply { ids.forEach { remove("w.$it.ids"); remove("w.$it.confirm") } }.apply()
        if (!anyShown()) WidgetRefreshWorker.cancel(context)
    }

    fun tileDevice(slot: Int): String? = prefs.getString("tile.$slot", null)?.ifEmpty { null }

    fun setTileDevice(slot: Int, id: String?) {
        prefs.edit().putString("tile.$slot", id ?: "").apply()
        changed()
        if (id != null) WidgetRefreshWorker.schedule(context)
    }

    /** Every device some widget or tile shows. */
    fun shownIds(): List<String> =
        (widgetIds().flatMap { devicesOf(it) } + (1..QuickTiles.SLOTS).mapNotNull { tileDevice(it) }).distinct()

    // -- cached states ------------------------------------------------------------------------------

    private fun cache(): Map<String, WidgetDevice> =
        WidgetTiles.decode(prefs.getString("cache", null)).associateBy { it.id }

    private fun saveCache(map: Map<String, WidgetDevice>) {
        prefs.edit().putString("cache", WidgetTiles.encode(map.values.toList())).apply()
    }

    fun cached(id: String?): WidgetDevice? = id?.let { cache()[it] }

    /** The widget's devices with their last states, in its order. */
    fun shown(widgetId: Int): List<WidgetDevice> = WidgetTiles.merge(devicesOf(widgetId), cache().values.toList())

    private fun update(block: (MutableMap<String, WidgetDevice>) -> Unit) {
        val map = cache().toMutableMap()
        block(map)
        saveCache(map)
        changed()
    }

    // -- talking to the hub -------------------------------------------------------------------------

    /** The Home status widgets' last summary (GET /api/widgets/home). */
    fun home(): WidgetHome? = WidgetTiles.decodeHome(prefs.getString("home", null))

    /** Why the Home status couldn't be fetched, while there's none to show. */
    @Volatile var homeError: String? = null
        private set

    /** Fresh states of [ids] (default: everything shown), and the Home status when one is out. False when
     * the hub couldn't be asked. */
    suspend fun refresh(ids: List<String> = shownIds()): Boolean = lock.withLock {
        val wantHome = homeWidgetIds().isNotEmpty()
        if (!signedIn || (ids.isEmpty() && !wantHome)) {
            changed()
            return false
        }
        var ok = true
        if (ids.isNotEmpty()) {
            try {
                val fresh = hubCall { api.widgetState(ids.joinToString(",")) }.devices
                update { map ->
                    ids.forEach { map.remove(it) } // gone from the hub (or no longer showable): drop them
                    fresh.forEach { map[it.id] = it }
                }
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                Log.w(TAG, "Widget refresh failed: ${ApiErrors.message(e)}")
                ok = false
            }
        }
        if (wantHome) {
            try {
                val h = hubCall { api.widgetHome() }
                prefs.edit().putString("home", WidgetTiles.encodeHome(h)).apply()
                homeError = null
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                Log.w(TAG, "Home status refresh failed: ${ApiErrors.message(e)}")
                homeError = if (e is HubException && e.code == 404) "Update the Newman hub to see this" else ApiErrors.message(e)
                ok = false
            }
        }
        changed()
        ok
    }

    /** Refresh in the background, folding bursts (live updates) into one. */
    fun refreshSoon(delayMs: Long = 700) {
        if (soon?.isActive == true) return
        soon = scope.launch {
            delay(delayMs)
            refresh(deviceIds()) // and the Home status, when one is out
        }
    }

    /** Run [action] on a device: shown right away ("Unlock…"), then as the hub reports it. */
    suspend fun run(deviceId: String, action: WidgetAction): Boolean {
        cached(deviceId)?.let { d -> update { it[deviceId] = WidgetTiles.optimistic(d, action) } }
        val ok = try {
            val res = api.command(deviceId, CommandRequest(action.command))
            if (!res.isSuccessful) throw HubException(res.code(), res.errorBody()?.string())
            true
        } catch (e: Exception) {
            if (e is kotlinx.coroutines.CancellationException) throw e
            val why = ApiErrors.message(e)
            Log.w(TAG, "Widget ${action.command} on $deviceId failed: $why")
            cached(deviceId)?.let { d -> update { it[deviceId] = d.copy(pending = "Couldn't ${action.label.lowercase()}") } }
            false
        }
        if (ok) shortcuts.record(deviceId) // the app icon's shortcuts: the devices run most
        delay(if (ok) 1200 else 2500)
        refresh(listOf(deviceId))
        if (ok) scope.launch { delay(4000); refresh(listOf(deviceId)) } // slow devices (a lock's motor, a garage)
        return ok
    }

    /** Signed out: nothing of the house stays on the home screen. */
    fun signedOut() {
        prefs.edit().remove("cache").remove("home").apply()
        changed()
    }

    /** Redraw every widget and tile from the cache. */
    fun changed() {
        _version.value++
        scope.launch {
            runCatching { DeviceGridWidget().updateAll(context) }
            runCatching { DeviceRowsWidget().updateAll(context) }
            runCatching { HomeStatusWidget().updateAll(context) }
        }
        QuickTiles.requestUpdate(context)
    }

    /** While the app runs: the live connection's device updates refresh the widgets showing them. */
    fun start() {
        if (liveJob != null) return
        liveJob = scope.launch {
            hub.liveMessages.collect { msg ->
                val id = when (msg) {
                    is LiveMessage.EntityChanged -> msg.entity.id
                    is LiveMessage.EntityRemoved -> msg.id
                    is LiveMessage.Reconnected -> ""
                    else -> null
                } ?: return@collect
                val shown = liveIds()
                if (shown.isNotEmpty() && (id.isEmpty() || id in shown)) refreshSoon()
            }
        }
        if (anyShown()) WidgetRefreshWorker.schedule(context)
    }

    @EntryPoint
    @InstallIn(SingletonComponent::class)
    interface Access {
        fun widgetSync(): WidgetSync
    }

    companion object {
        private const val TAG = "NewmanWidgets"

        /** For the widget, its receivers, the tiles and the worker (none of them injected by Hilt). */
        fun from(context: Context): WidgetSync =
            EntryPointAccessors.fromApplication(context.applicationContext, Access::class.java).widgetSync()
    }
}
