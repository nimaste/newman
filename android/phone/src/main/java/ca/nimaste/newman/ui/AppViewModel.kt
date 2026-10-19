package ca.nimaste.newman.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import ca.nimaste.newman.core.api.NewmanApi
import ca.nimaste.newman.core.api.ApiErrors
import ca.nimaste.newman.core.api.hubCall
import ca.nimaste.newman.core.api.orThrow
import ca.nimaste.newman.core.model.Automation
import ca.nimaste.newman.core.model.AutomationRun
import ca.nimaste.newman.core.model.Automations
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import ca.nimaste.newman.core.hub.HubRepository
import ca.nimaste.newman.core.hub.HubState
import ca.nimaste.newman.core.live.LiveMessage
import ca.nimaste.newman.core.live.LiveStatus
import ca.nimaste.newman.core.model.CameraAlert
import ca.nimaste.newman.core.model.ClimateRoomsBody
import ca.nimaste.newman.core.model.Command
import ca.nimaste.newman.core.model.HomeData
import ca.nimaste.newman.core.model.HomePrefs
import ca.nimaste.newman.core.model.PasswordChange
import ca.nimaste.newman.core.model.asStringOrNull
import ca.nimaste.newman.core.model.string
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.MuteRequest
import ca.nimaste.newman.core.model.Sensitive
import ca.nimaste.newman.core.mtls.MTLSStore
import ca.nimaste.newman.core.net.HubSelector
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.data.Account
import ca.nimaste.newman.data.PeopleRepository
import ca.nimaste.newman.data.TodayRepository
import ca.nimaste.newman.location.LocationSharing
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ProcessLifecycleOwner
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import okhttp3.OkHttpClient
import javax.inject.Inject

/** App-wide state: sign-in, the hub snapshot, commands and the messages shown as toasts. */
@HiltViewModel
class AppViewModel @Inject constructor(
    val hub: HubRepository,
    val account: Account,
    val store: ConnectionStore,
    private val api: NewmanApi,
    /** The shared mTLS client, for refreshing camera stills. */
    val http: OkHttpClient,
    val people: PeopleRepository,
    val today: TodayRepository,
    val sharing: LocationSharing,
    private val selector: HubSelector,
    private val shortcuts: ca.nimaste.newman.widgets.LauncherShortcuts,
    private val live: ca.nimaste.newman.push.LiveStatusNotifier,
) : ViewModel() {

    /** Back in front: a fresh fix (when sharing) and today's briefing. */
    private val foregroundObserver = object : DefaultLifecycleObserver {
        override fun onStart(owner: LifecycleOwner) {
            selector.recheck(300) // home or away now?
            if (_signedIn.value) live.sync() // the live statuses the hub shows now
            if (_signedIn.value) viewModelScope.launch {
                sharing.refresh()
                sharing.reportNow("app")
            }
        }
    }

    val state: StateFlow<HubState> = hub.state
    val liveStatus: StateFlow<LiveStatus> = hub.liveStatus
    val liveMessages: SharedFlow<LiveMessage> = hub.liveMessages

    private val _signedIn = MutableStateFlow(account.signedIn)
    val signedIn: StateFlow<Boolean> = _signedIn.asStateFlow()

    private val _toasts = MutableSharedFlow<Toast>(extraBufferCapacity = 8)
    val toasts: SharedFlow<Toast> = _toasts.asSharedFlow()

    data class Toast(val text: String, val ok: Boolean)

    /** Device ids with a command in flight (their controls show busy). */
    private val _busy = MutableStateFlow<Set<String>>(emptySet())
    val busy: StateFlow<Set<String>> = _busy.asStateFlow()

    private val _biometrics = MutableStateFlow(store.biometricsEnabled)
    val biometrics: StateFlow<Boolean> = _biometrics.asStateFlow()

    /** At most 4 camera tiles stream live video at once (full screen is exempt). */
    val livePool = ca.nimaste.newman.core.live.LiveStreamPool(4)

    private val _alertsSeen = MutableStateFlow(store.alertsSeenAt)
    val alertsSeen: StateFlow<Double> = _alertsSeen.asStateFlow()

    /** GET /api/home: the Home screen's cards, favourites and status (null until loaded). */
    private val _home = MutableStateFlow<HomeData?>(null)
    val home: StateFlow<HomeData?> = _home.asStateFlow()
    private var homeJob: kotlinx.coroutines.Job? = null

    /** Reload the Home data; a newer call replaces one still waiting for its answer. */
    fun reloadHome() {
        homeJob?.cancel()
        homeJob = viewModelScope.launch {
            runCatching { hubCall { api.home() } }.onSuccess { _home.value = it }
        }
    }

    /** May this account create and change automations: the hub's word (Home data, else the account). */
    val canAutomate: Boolean
        get() = _home.value?.canAutomate ?: (state.value.auth?.user?.let { it.canAutomate ?: (it.role == "admin") } ?: false)

    init {
        if (account.signedIn) hub.start()
        if (account.signedIn) reloadHome()
        viewModelScope.launch {
            // The hub says the Home data or the house settings changed, or the connection came back.
            hub.liveMessages.collect { m ->
                if ((m is LiveMessage.Unknown && (m.type == "home" || m.type == "settings-house" || m.type == "categories")) ||
                    m is LiveMessage.Reconnected) reloadHome()
                // A category was made, renamed or emptied: every screen's names and screens come from the list.
                if (m is LiveMessage.Unknown && m.type == "categories") viewModelScope.launch { hub.reloadCategories() }
            }
        }
        viewModelScope.launch {
            hub.signedOut.collect {
                account.forgetSession()
                live.clear()
                _signedIn.value = false
                toast("Please sign in again", ok = false)
            }
        }
        // A new or removed certificate applies to the next connections.
        viewModelScope.launch {
            MTLSStore.info.map { it?.notAfterMillis }.distinctUntilChanged().drop(1).collect { account.connectionChanged() }
        }
        if (account.signedIn) viewModelScope.launch { account.registerPush(); account.refreshPushCategories() }
        ProcessLifecycleOwner.get().lifecycle.addObserver(foregroundObserver)
        viewModelScope.launch {
            hub.signedOut.collect { people.clear(); today.clear() }
        }
    }

    override fun onCleared() {
        ProcessLifecycleOwner.get().lifecycle.removeObserver(foregroundObserver)
    }

    fun onSignedIn() {
        _signedIn.value = true
        reloadHome()
        live.sync()
        viewModelScope.launch {
            sharing.refresh()
            sharing.reportNow("app")
        }
    }

    fun signOut() = viewModelScope.launch {
        account.signOut()
        live.clear()
        people.clear()
        today.clear()
        _signedIn.value = false
    }

    fun refresh() = viewModelScope.launch { hub.refresh() }

    /**
     * Changing server to another hub (Me -> Server): sign out of this one first, while its addresses are
     * still the ones in use. The screen then saves the new hub and calls [leftHub].
     */
    suspend fun leaveHub() {
        account.signOut()
        live.clear()
        people.clear()
        today.clear()
        shortcuts.hubChanged() // the old hub's devices mean nothing on the new one
    }

    /** After [leaveHub] and the new hub is saved: on to signing in to it. */
    fun leftHub(name: String?) {
        _signedIn.value = false
        toast("Now sign in to ${name ?: "your Newman"}")
    }

    fun toast(text: String, ok: Boolean = true) {
        _toasts.tryEmit(Toast(text, ok))
    }

    fun setBiometrics(on: Boolean) {
        store.biometricsEnabled = on
        _biometrics.value = on
    }

    fun markAlertsSeen(newest: Double) {
        if (newest > _alertsSeen.value) {
            store.alertsSeenAt = newest
            _alertsSeen.value = store.alertsSeenAt
        }
    }

    /** True when this command must pass the fingerprint / screen lock first. */
    fun needsConfirmation(entity: Entity, cmd: Command): Boolean =
        Sensitive.needsConfirmation(state.value.kind(entity), cmd.id, _biometrics.value)

    /** Send a command (already confirmed if it had to be). Errors show the hub's own words. */
    fun send(entity: Entity, cmd: Command, value: JsonElement? = null) = viewModelScope.launch {
        _busy.value = _busy.value + entity.id
        val k = state.value.kind(entity)
        when (val r = hub.command(entity, cmd, value)) {
            is HubRepository.CommandOutcome.Sent -> {
                if (value == null) shortcuts.record(entity.id) // one-tap runs: the app icon's shortcuts
                else toast("${entity.name}: ${cmd.label.lowercase()} ${HubModel.valueText(cmd, k, value, entity)}")
            }
            is HubRepository.CommandOutcome.Refused -> toast(r.message, ok = false)
        }
        _busy.value = _busy.value - entity.id
    }

    // -- automations (More › Automations) ------------------------------------------------------

    /** The hub's automations; null when they couldn't be loaded (the error is shown unless [quiet]). */
    suspend fun automations(quiet: Boolean = false): List<Automation>? = try {
        hubCall { api.automations() }
    } catch (e: Exception) {
        if (!quiet) toast(ApiErrors.message(e), ok = false)
        null
    }

    /** Switch one on or off: the saved automation, or null after showing the hub's answer (403 for family accounts). */
    suspend fun setAutomationEnabled(id: String, on: Boolean): Automation? = try {
        api.patchAutomation(id, JsonObject(mapOf("enabled" to JsonPrimitive(on)))).orThrow()
    } catch (e: Exception) {
        toast(ApiErrors.message(e), ok = false)
        null
    }

    suspend fun automationRuns(id: String): List<AutomationRun> = try {
        hubCall { api.runs(id, 10) }
    } catch (e: Exception) {
        emptyList()
    }

    /** "Run now": the run, or null after showing why not. */
    suspend fun runAutomation(a: Automation): AutomationRun? = try {
        api.runAutomation(a.id).orThrow().also { toast(Automations.runResult(a.name, it)) }
    } catch (e: Exception) {
        toast(ApiErrors.message(e), ok = false)
        null
    }

    /** The automation as the hub keeps it (the editor's starting point); null after showing why not. */
    suspend fun automationJson(id: String): JsonObject? = try {
        hubCall { api.automationJson(id) }
    } catch (e: Exception) {
        toast(ApiErrors.message(e), ok = false)
        null
    }

    /** Save (a new one when [id] is null): the saved automation, or null after showing the hub's answer. */
    suspend fun saveAutomation(id: String?, body: JsonObject): JsonObject? = try {
        (if (id == null) api.createAutomation(body) else api.putAutomation(id, body)).orThrow().also { toast("Saved") }
    } catch (e: Exception) {
        toast(ApiErrors.message(e), ok = false)
        null
    }

    suspend fun deleteAutomation(id: String): Boolean = try {
        api.deleteAutomation(id).orThrow()
        toast("Deleted")
        true
    } catch (e: Exception) {
        toast(ApiErrors.message(e), ok = false)
        false
    }

    /** The hub's plain-English summary of an automation being edited ("" when it can't say). */
    suspend fun describeAutomation(body: JsonObject): String = try {
        hubCall { api.describeAutomation(body) }["summary"].asStringOrNull ?: ""
    } catch (e: Exception) {
        ""
    }

    // -- Home: Edit Home, Climate's rooms, the password ---------------------------------------------

    suspend fun saveHomePrefs(prefs: HomePrefs): Boolean = try {
        api.putHome(prefs).orThrow()
        reloadHome()
        true
    } catch (e: Exception) {
        toast(ApiErrors.message(e), ok = false)
        false
    }

    suspend fun saveClimateRooms(body: ClimateRoomsBody): Boolean = try {
        api.putClimateRooms(body).orThrow()
        reloadHome()
        true
    } catch (e: Exception) {
        toast(ApiErrors.message(e), ok = false)
        false
    }

    /** Admin: what the Cameras tab's big picture shows (latest activity, or a featured camera). */
    suspend fun setCamerasTop(mode: String, featured: String?): Boolean = try {
        api.patchHouseSettings(JsonObject(mapOf("cameras" to JsonObject(mapOf("mode" to JsonPrimitive(mode),
            "featured" to (featured?.let { JsonPrimitive(it) } ?: kotlinx.serialization.json.JsonNull)))))).orThrow()
        reloadHome()
        true
    } catch (e: Exception) {
        toast(ApiErrors.message(e), ok = false)
        false
    }

    /** Admin: a to-do from Today's "+ Add" - an upkeep item first due today, repeating every [every] [unit]. */
    suspend fun addTodo(name: String, every: Int, unit: String): Boolean = try {
        val today = java.time.LocalDate.now()
        val last = when (unit) { "weeks" -> today.minusWeeks(every.toLong()); "months" -> today.minusMonths(every.toLong()); else -> today.minusDays(every.toLong()) }
        api.addUpkeep(JsonObject(mapOf("name" to JsonPrimitive(name), "every" to JsonPrimitive(every), "unit" to JsonPrimitive(unit),
            "lastDone" to JsonPrimitive(last.toString())))).orThrow()
        this.today.refresh()
        toast("Added")
        true
    } catch (e: Exception) {
        toast(ApiErrors.message(e), ok = false)
        false
    }

    /** A tapped notification's alert to show: More › Alerts scrolled to it and highlighted. */
    val openAlert = MutableStateFlow<ca.nimaste.newman.core.model.ActivityEntry?>(null)
    /** ...or a camera alert's clip, played on its camera's screen. */
    val openClip = MutableStateFlow<CameraAlert?>(null)

    /** The activity entry of one automation run (a notification's alertId). */
    suspend fun runEntry(runId: String): ca.nimaste.newman.core.model.ActivityEntry? = try {
        hubCall { api.events(limit = 5, runId = runId) }.firstOrNull()
    } catch (e: Exception) {
        null
    }

    /** Change this account's password: true when done (the hub signs out every other device). */
    suspend fun changePassword(current: String, new: String): Boolean = try {
        api.changePassword(PasswordChange(current, new)).orThrow()
        toast("Password changed")
        true
    } catch (e: Exception) {
        toast(ApiErrors.message(e), ok = false)
        false
    }

    // -- muting a device's problems -------------------------------------------------------------

    /** Mute one of [e]'s problems ([mode]: day, week, fixed, always); false after showing why not. */
    suspend fun mute(e: Entity, issue: String, mode: String): Boolean = try {
        api.mute(e.id, MuteRequest(issue, mode)).orThrow()?.health?.let { hub.setHealth(e.id, it) }
        today.refresh()
        reloadHome() // its warning leaves Home
        true
    } catch (ex: Exception) {
        toast(ApiErrors.message(ex), ok = false)
        false
    }

    suspend fun unmute(e: Entity, issue: String): Boolean = try {
        api.unmute(e.id, issue).orThrow()?.health?.let { hub.setHealth(e.id, it) }
        today.refresh()
        reloadHome()
        true
    } catch (ex: Exception) {
        toast(ApiErrors.message(ex), ok = false)
        false
    }

    // -- camera alerts (the Alerts tab's Cameras filter and the camera screen) --------------------

    /**
     * The camera alert a tapped notification is about. Its alertId is the automation run's id: the run's
     * activity (GET /api/events?runId=) names the clip, found then among the alerts, paging back
     * (a notification of any age); without one, the push's own clip or review id among recent alerts.
     */
    suspend fun findAlert(alertId: String?, clip: String?, review: String?): CameraAlert? {
        var c = clip
        var rv = review
        if (alertId != null) {
            val entries = try { hubCall { api.events(limit = 20, runId = alertId) } } catch (e: Exception) { emptyList() }
            entries.firstNotNullOfOrNull { it.data }?.let { d ->
                c = c ?: d.string("clip")
                rv = rv ?: d.string("reviewId") ?: d.string("frigate_event_id") ?: d.string("alertId")
            }
        }
        if (c == null && rv == null) return null
        var before: Double? = null
        repeat(if (alertId != null) 10 else 1) {
            val list = try { hubCall { api.alerts(100, before) } } catch (e: Exception) { return null }
            ca.nimaste.newman.core.model.CameraClip.matchAlert(list, c, rv)?.let { return it }
            if (list.size < 100) return null
            before = list.mapNotNull { it.time }.minOrNull() ?: return null
        }
        return null
    }

    /** Today's security events (GET /api/alerts?category=security&since=), newest first; null when they couldn't be loaded. */
    suspend fun securityEvents(since: Double): List<CameraAlert>? = try {
        hubCall { api.alerts(100, category = "security", since = since) }.sortedByDescending { it.time ?: 0.0 }
    } catch (e: Exception) {
        null
    }

    suspend fun cameraAlerts(entity: Entity?, limit: Int = 50, since: Double? = null): List<CameraAlert> = try {
        // One camera: GET /api/alerts?entity=&since= (older hubs: the camera's own alerts link).
        if (entity != null) runCatching { hubCall { api.alerts(limit, entity = entity.id, since = since) } }.getOrNull()
            ?: entity.alerts?.let { l -> hubCall { api.entityAlerts(l, limit) }.filter { since == null || (it.time ?: 0.0) >= since } } ?: emptyList()
        else hubCall { api.alerts(limit) }
    } catch (e: Exception) {
        emptyList()
    }
}
