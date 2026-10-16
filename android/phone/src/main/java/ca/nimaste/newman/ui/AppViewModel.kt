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
import ca.nimaste.newman.core.model.Command
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HubModel
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

    init {
        if (account.signedIn) hub.start()
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

    // -- camera alerts (the Alerts tab's Cameras filter and the camera screen) --------------------

    suspend fun cameraAlerts(entity: Entity?, limit: Int = 50): List<CameraAlert> = try {
        if (entity?.alerts != null) hubCall { api.entityAlerts(entity.alerts!!, limit) }
        else hubCall { api.alerts(limit) }
    } catch (e: Exception) {
        emptyList()
    }
}
