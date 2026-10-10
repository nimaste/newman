package ca.nimaste.newman.core.hub

import ca.nimaste.newman.core.api.ApiErrors
import ca.nimaste.newman.core.api.HubException
import ca.nimaste.newman.core.api.NewmanApi
import ca.nimaste.newman.core.api.hubCall
import ca.nimaste.newman.core.live.LiveMessage
import ca.nimaste.newman.core.live.LiveSocket
import ca.nimaste.newman.core.live.LiveStatus
import ca.nimaste.newman.core.model.Command
import ca.nimaste.newman.core.model.CommandRequest
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.PhoneGrid
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonElement

/**
 * The phone's live picture of the house: meta, devices, the dashboard and sign-in state, loaded
 * from the API and kept current by the live WebSocket. Commands are optimistic and rolled back
 * when the hub refuses them.
 */
class HubRepository(
    private val api: NewmanApi,
    private val live: LiveSocket,
    private val scope: CoroutineScope,
) {
    private val _state = MutableStateFlow(HubState())
    val state: StateFlow<HubState> = _state.asStateFlow()
    val liveStatus: StateFlow<LiveStatus> get() = live.status

    private val _signedOut = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    /** Emits when the hub no longer accepts this phone's session. */
    val signedOut: SharedFlow<Unit> = _signedOut.asSharedFlow()

    private val _events = MutableSharedFlow<LiveMessage>(extraBufferCapacity = 64)
    /** Every live message, for screens that react to some (alerts lists, camera events). */
    val liveMessages: SharedFlow<LiveMessage> = _events.asSharedFlow()

    private var liveJob: Job? = null
    private var statusJob: Job? = null
    private val loadLock = Mutex()

    /** Start following the hub: load everything, then apply live messages. */
    fun start() {
        if (statusJob == null) {
            // The hub came back (or was unreachable at launch): load what we couldn't.
            statusJob = scope.launch {
                live.status.collect { if (it == LiveStatus.OPEN && !_state.value.loaded) refresh() }
            }
        }
        if (liveJob == null) {
            liveJob = scope.launch {
                live.messages.collect { msg ->
                    _events.tryEmit(msg)
                    val (next, effect) = HubReducer.apply(_state.value, msg)
                    _state.value = next
                    when (effect) {
                        Effect.RELOAD_ALL -> refresh()
                        Effect.RELOAD_DASHBOARD -> launch { reloadDashboard() }
                        Effect.RELOAD_LAYOUT -> launch { reloadLayout() }
                        Effect.RELOAD_AUTH -> launch { reloadAuth() }
                        Effect.RELOAD_ACTIVITY -> launch { reloadActivity() }
                        Effect.NONE -> Unit
                    }
                }
            }
        }
        live.start()
        scope.launch { refresh() }
    }

    fun stop() {
        live.stop()
        liveJob?.cancel()
        liveJob = null
        statusJob?.cancel()
        statusJob = null
        _state.value = HubState()
    }

    /** Reconnect the live channel (after the server, token or certificate changed). */
    fun reconnect() {
        if (liveJob == null) return start()
        live.restart()
        scope.launch { refresh() }
    }

    val started: Boolean get() = liveJob != null

    /** Reload meta, devices, the dashboard, sign-in state and recent activity together. */
    suspend fun refresh(): Result<Unit> = loadLock.withLock {
        try {
            coroutineScope {
                val meta = async { hubCall { api.meta() } }
                val entities = async { hubCall { api.entities() } }
                val dash = async { hubCall { api.currentDashboard() } }
                val auth = async { hubCall { api.authState() } }
                val activity = async { runCatching { hubCall { api.events(limit = 100) } }.getOrNull() }
                val layout = async { fetchLayout() }
                val m = meta.await()
                val list = entities.await()
                val d = dash.await()
                val a = auth.await()
                val act = activity.await()
                val l = layout.await()
                _state.update {
                    it.copy(meta = m, entities = LinkedHashMap(list.associateBy { e -> e.id }), dashboard = d, auth = a,
                        phoneLayout = PhoneGrid.next(it.phoneLayout, l), activity = act ?: it.activity, loaded = true, error = null)
                }
            }
            Result.success(Unit)
        } catch (e: Exception) {
            if (ApiErrors.isSignedOut(e)) _signedOut.tryEmit(Unit)
            _state.update { it.copy(error = ApiErrors.message(e)) }
            Result.failure(e)
        }
    }

    suspend fun reloadDashboard() {
        runCatching { hubCall { api.currentDashboard() } }.onSuccess { d -> _state.update { it.copy(dashboard = d) } }
        reloadLayout()
    }

    suspend fun reloadLayout() {
        val l = fetchLayout()
        _state.update { it.copy(phoneLayout = PhoneGrid.next(it.phoneLayout, l)) }
    }

    /** GET /api/phone-layout; never throws (a 404 means an older hub: flow the dashboard). */
    private suspend fun fetchLayout(): PhoneGrid.Fetch = try {
        val res = api.phoneLayout()
        val body = if (res.isSuccessful) res.body()?.string() else res.errorBody()?.string()
        PhoneGrid.outcome(res.code(), body)
    } catch (e: Exception) {
        if (e is kotlinx.coroutines.CancellationException) throw e
        PhoneGrid.Fetch.Failed
    }

    suspend fun reloadAuth() {
        runCatching { hubCall { api.authState() } }.onSuccess { a -> _state.update { it.copy(auth = a) } }
    }

    suspend fun reloadActivity() {
        runCatching { hubCall { api.events(limit = 100) } }.onSuccess { l -> _state.update { it.copy(activity = l) } }
    }

    sealed interface CommandOutcome {
        data object Sent : CommandOutcome
        data class Refused(val message: String) : CommandOutcome
    }

    /** Send [cmd] to a device, showing the expected result right away; undone if the hub says no. */
    suspend fun command(entity: Entity, cmd: Command, value: JsonElement? = null): CommandOutcome {
        val kind = _state.value.kind(entity)
        val previous = _state.value.entities[entity.id] ?: entity
        val optimistic = HubReducer.optimistic(previous, kind, cmd, value)
        if (optimistic != previous) _state.update { HubReducer.replaceEntity(it, optimistic) }
        return try {
            val res = api.command(entity.id, CommandRequest(cmd.id, value))
            if (!res.isSuccessful) throw HubException(res.code(), res.errorBody()?.string())
            CommandOutcome.Sent
        } catch (e: Exception) {
            if (optimistic != previous) _state.update { HubReducer.rollback(it, optimistic, previous) }
            if (ApiErrors.isSignedOut(e)) _signedOut.tryEmit(Unit)
            CommandOutcome.Refused(ApiErrors.message(e))
        }
    }
}
