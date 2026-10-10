package ca.nimaste.newman.data

import ca.nimaste.newman.core.api.ApiErrors
import ca.nimaste.newman.core.api.HubException
import ca.nimaste.newman.core.api.NewmanApi
import ca.nimaste.newman.core.api.hubCall
import ca.nimaste.newman.core.api.orThrow
import ca.nimaste.newman.core.hub.HubRepository
import ca.nimaste.newman.core.live.LiveMessage
import ca.nimaste.newman.core.people.PeopleList
import ca.nimaste.newman.core.people.Person
import ca.nimaste.newman.core.people.PersonLook
import ca.nimaste.newman.core.people.PersonRefresh
import ca.nimaste.newman.core.people.Place
import ca.nimaste.newman.core.people.ProfileUpdate
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import javax.inject.Inject
import javax.inject.Singleton

/**
 * The household for the People tab (GET /api/people), kept current by live "person" messages;
 * tap-to-refresh (POST /api/people/{id}/refresh) and the signed-in person's colour and photo.
 */
@Singleton
class PeopleRepository @Inject constructor(
    private val api: NewmanApi,
    hub: HubRepository,
    private val scope: CoroutineScope,
) {
    enum class Status { LOADING, READY, NOT_SET_UP, UNREACHABLE }

    data class State(val people: List<Person> = emptyList(), val status: Status = Status.LOADING)

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    private val _places = MutableStateFlow<List<Place>>(emptyList())
    val places: StateFlow<List<Place>> = _places.asStateFlow()

    /** Tap-to-refresh per person id ("Updating…", timeouts, "Updated recently"); idle ones are absent. */
    private val _refreshes = MutableStateFlow<Map<String, PersonRefresh.State>>(emptyMap())
    val refreshes: StateFlow<Map<String, PersonRefresh.State>> = _refreshes.asStateFlow()
    private var ticker: Job? = null

    init {
        scope.launch {
            hub.liveMessages.collect { m ->
                when (m) {
                    is LiveMessage.PersonChanged -> {
                        _state.update { it.copy(people = PeopleList.upsert(it.people, m.person)) }
                        personArrived(m.person)
                    }
                    LiveMessage.Reconnected -> if (_state.value.status == Status.READY) refresh()
                    else -> Unit
                }
            }
        }
    }

    suspend fun refresh() {
        try {
            val list = hubCall { api.people() }
            _state.value = State(list, Status.READY)
            list.forEach { personArrived(it) }
        } catch (e: Exception) {
            val notSetUp = e is HubException && e.code == 404
            _state.update {
                when {
                    notSetUp -> State(emptyList(), Status.NOT_SET_UP)
                    it.people.isNotEmpty() -> it // keep what we had; the next refresh may work
                    else -> it.copy(status = Status.UNREACHABLE)
                }
            }
        }
    }

    suspend fun refreshPlaces() {
        runCatching { hubCall { api.places() } }.onSuccess { _places.value = it }
    }

    fun person(id: String): Person? = _state.value.people.firstOrNull { it.id == id }

    // ---- tap-to-refresh

    /** Asks the hub for ONE fresh fix of this person (it allows one per 2 minutes per person). */
    fun refreshPerson(id: String) {
        val now = now()
        val before = _refreshes.value[id] ?: PersonRefresh.IDLE
        val started = PersonRefresh.start(before, person(id), now)
        if (started == before) return // already updating
        _refreshes.update { it + (id to started) }
        startTicker()
        scope.launch {
            var status = 0
            var next: Double? = null
            try {
                val r = api.refreshPerson(id)
                status = r.code()
                next = PersonRefresh.nextAllowedAt(if (r.isSuccessful) r.body()?.string() else r.errorBody()?.string())
            } catch (e: Exception) {
                // No answer: status 0 says so.
            }
            change(id) { PersonRefresh.replied(it, status, next, now()) }
        }
    }

    private fun personArrived(p: Person) {
        if (_refreshes.value.containsKey(p.id)) change(p.id) { PersonRefresh.personChanged(it, p) }
    }

    private fun change(id: String, f: (PersonRefresh.State) -> PersonRefresh.State) {
        _refreshes.update { map ->
            val cur = map[id] ?: return@update map
            val next = f(cur)
            if (next == PersonRefresh.IDLE) map - id else map + (id to next)
        }
    }

    /** Once a second while any refresh shows something: timeouts and countdowns. */
    private fun startTicker() {
        synchronized(this) {
            if (ticker?.isActive == true) return
            ticker = scope.launch {
                while (true) {
                    delay(1_000)
                    val now = now()
                    _refreshes.update { map ->
                        map.mapValues { PersonRefresh.tick(it.value, now) }.filterValues { it != PersonRefresh.IDLE }
                    }
                    if (_refreshes.value.isEmpty()) break
                }
            }
        }
    }

    // ---- my colour and photo

    /** PUT /api/me/profile; null on success, else the hub's words. */
    suspend fun setMyColor(color: String): String? = try {
        api.setProfile(ProfileUpdate(PersonLook.normalized(color) ?: color)).orThrow()
        refresh()
        null
    } catch (e: Exception) {
        ApiErrors.message(e)
    }

    /** PUT /api/me/photo with a JPEG (already square and small); null on success, else the hub's words. */
    suspend fun setMyPhoto(jpeg: ByteArray): String? = try {
        api.uploadPhoto(jpeg.toRequestBody("image/jpeg".toMediaType())).orThrow()
        refresh()
        null
    } catch (e: Exception) {
        ApiErrors.message(e)
    }

    suspend fun removeMyPhoto(): String? = try {
        api.deletePhoto().orThrow()
        refresh()
        null
    } catch (e: Exception) {
        ApiErrors.message(e)
    }

    fun clear() {
        _state.value = State()
        _refreshes.value = emptyMap()
    }

    private fun now() = System.currentTimeMillis() / 1000.0
}
