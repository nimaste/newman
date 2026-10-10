package ca.nimaste.newman.data

import ca.nimaste.newman.core.api.ApiErrors
import ca.nimaste.newman.core.api.HubException
import ca.nimaste.newman.core.api.NewmanApi
import ca.nimaste.newman.core.api.hubCall
import ca.nimaste.newman.core.api.orThrow
import ca.nimaste.newman.core.hub.HubRepository
import ca.nimaste.newman.core.live.LiveMessage
import ca.nimaste.newman.core.today.DismissRequest
import ca.nimaste.newman.core.today.SnoozeRequest
import ca.nimaste.newman.core.today.TodayBriefing
import ca.nimaste.newman.core.today.TodayItem
import ca.nimaste.newman.core.today.TodayView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Today's briefing (GET /api/today) and its actions: Done / Undo / Remind tomorrow for upkeep,
 * dismissing an appliance's need. The hub records the signed-in person as who did it. Reloaded
 * when devices change (at most every 15 s) and when the Today card is shown.
 */
@Singleton
class TodayRepository @Inject constructor(
    private val api: NewmanApi,
    hub: HubRepository,
    private val scope: CoroutineScope,
) {
    /** null = not loaded, or an older hub without Today (404): the card stays hidden. */
    private val _briefing = MutableStateFlow<TodayBriefing?>(null)
    val briefing: StateFlow<TodayBriefing?> = _briefing.asStateFlow()

    /** Items done on this phone lately (kept with Undo). */
    private val _done = MutableStateFlow<Map<String, TodayView.Done>>(emptyMap())
    val done: StateFlow<Map<String, TodayView.Done>> = _done.asStateFlow()

    private var lastLoad = 0L
    private var pending: Job? = null

    init {
        scope.launch {
            hub.liveMessages.collect { m ->
                if ((m is LiveMessage.EntityChanged || m is LiveMessage.EntityRemoved || m == LiveMessage.Reconnected) && _briefing.value != null) soon()
            }
        }
    }

    private fun soon() {
        if (pending?.isActive == true) return
        val wait = maxOf(2_000L, 15_000L - (System.currentTimeMillis() - lastLoad))
        pending = scope.launch {
            delay(wait)
            refresh()
        }
    }

    suspend fun refresh() {
        lastLoad = System.currentTimeMillis()
        try {
            _briefing.value = hubCall { api.today() }
        } catch (e: Exception) {
            if (e is HubException && e.code == 404) _briefing.value = null
        }
    }

    private fun now() = System.currentTimeMillis() / 1000.0

    /** Done (upkeep) or dismiss (an appliance's need). Null, or the hub's words when it failed. */
    suspend fun done(item: TodayItem): String? = act {
        if (item.action == "dismiss") {
            api.todayDismiss(DismissRequest(item.id)).orThrow()
            _done.value = _done.value + (item.id to TodayView.Done(item, null, now()))
        } else {
            val id = TodayView.upkeepId(item)
            api.upkeepDone(id).orThrow()
            _done.value = _done.value + (item.id to TodayView.Done(item, id, now()))
        }
    }

    suspend fun undo(item: TodayItem): String? = act {
        _done.value[item.id]?.upkeepId?.let { api.upkeepUndo(it).orThrow() }
        _done.value = _done.value - item.id
    }

    /** Remind tomorrow. */
    suspend fun snooze(item: TodayItem): String? = act {
        api.upkeepSnooze(TodayView.upkeepId(item), SnoozeRequest(1)).orThrow()
    }

    private suspend fun act(block: suspend () -> Unit): String? = try {
        block()
        refresh()
        null
    } catch (e: Exception) {
        ApiErrors.message(e)
    }

    fun clear() {
        _briefing.value = null
        _done.value = emptyMap()
    }
}
