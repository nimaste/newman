package ca.nimaste.newman.core.hub

import ca.nimaste.newman.core.live.LiveMessage
import ca.nimaste.newman.core.model.ActivityEntry
import ca.nimaste.newman.core.model.AuthState
import ca.nimaste.newman.core.model.Command
import ca.nimaste.newman.core.model.Dashboard
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.Kind
import ca.nimaste.newman.core.model.Meta
import ca.nimaste.newman.core.model.PhoneGrid
import ca.nimaste.newman.core.model.PhoneGridLayout
import ca.nimaste.newman.core.model.PhoneTabs
import ca.nimaste.newman.core.model.asStringOrNull
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/** Everything the phone shows, in one immutable snapshot. */
data class HubState(
    val meta: Meta? = null,
    /** Devices in the hub's order. */
    val entities: Map<String, Entity> = emptyMap(),
    val dashboard: Dashboard? = null,
    /** GET /api/phone-layout; null on hubs without phone layouts (the dashboard flows instead). */
    val phoneLayout: PhoneGridLayout? = null,
    val auth: AuthState? = null,
    /** The newest activity first (from GET /api/events, then live "activity" messages). */
    val activity: List<ActivityEntry> = emptyList(),
    val loaded: Boolean = false,
    val error: String? = null,
) {
    fun kind(e: Entity?): Kind? = meta?.kind(e?.kind)

    /** Entities with the dashboard's "Show as" names. */
    val named: Map<String, Entity> by lazy { HubModel.withNames(entities, dashboard?.names ?: emptyMap()) }

    /** The tabs: from the phone layout when the hub has one, else flowed from the dashboard. */
    val plan: PhoneTabs.Plan by lazy { phoneLayout?.let { PhoneGrid.plan(it) } ?: PhoneTabs.plan(dashboard?.pages ?: emptyList()) }
}

/** What applying a live message asks the repository to do next. */
enum class Effect { NONE, RELOAD_DASHBOARD, RELOAD_LAYOUT, RELOAD_AUTH, RELOAD_ALL, RELOAD_ACTIVITY }

object HubReducer {
    const val ACTIVITY_LIMIT = 200

    fun apply(state: HubState, msg: LiveMessage): Pair<HubState, Effect> = when (msg) {
        is LiveMessage.EntityChanged -> replaceEntity(state, msg.entity) to Effect.NONE
        is LiveMessage.EntityRemoved -> removeEntity(state, msg.id) to Effect.NONE
        is LiveMessage.Activity -> {
            val list = (listOf(msg.entry) + state.activity.filter { it.id != msg.entry.id }).take(ACTIVITY_LIMIT)
            state.copy(activity = list) to Effect.NONE
        }
        is LiveMessage.DashboardChanged -> {
            // Only reload when it can be ours: no user id, or the signed-in user's.
            val mine = msg.userId == null || state.auth?.user?.id == null || msg.userId == state.auth.user.id
            // Someone else's edit can still be the house default a phone layout follows.
            state to when {
                mine -> Effect.RELOAD_DASHBOARD
                state.phoneLayout != null -> Effect.RELOAD_LAYOUT
                else -> Effect.NONE
            }
        }
        LiveMessage.SettingsChanged -> state to Effect.RELOAD_AUTH
        LiveMessage.Reconnected -> state to Effect.RELOAD_ALL
        else -> state to Effect.NONE
    }

    /** Replace one device in place (new devices go last), keeping the hub's order. */
    fun replaceEntity(state: HubState, e: Entity): HubState {
        val map = LinkedHashMap(state.entities)
        map[e.id] = e
        return state.copy(entities = map)
    }

    /** Drop a removed device (the rest keep the hub's order). */
    fun removeEntity(state: HubState, id: String): HubState {
        if (!state.entities.containsKey(id)) return state
        val map = LinkedHashMap(state.entities)
        map.remove(id)
        return state.copy(entities = map)
    }

    /**
     * What a device most likely looks like once [cmd] lands: a "state" value becomes the state; a
     * value command with an attr sets that attr; a command that confirms to a state sets the state.
     */
    fun optimistic(e: Entity, k: Kind?, cmd: Command, value: JsonElement?): Entity {
        var out = e
        if (cmd.valueType == "state" && value.asStringOrNull != null) {
            val s = value.asStringOrNull!!
            return out.copy(state = s, stateLabel = HubModel.stateLabelOf(k, s) ?: s)
        }
        if (cmd.attr.isNotEmpty() && value != null) {
            out = out.copy(attrs = JsonObject(out.attrs + (cmd.attr to value)))
        }
        if (cmd.confirmsTo.isNotEmpty()) {
            out = out.copy(state = cmd.confirmsTo, stateLabel = HubModel.stateLabelOf(k, cmd.confirmsTo) ?: cmd.confirmsTo)
        }
        return out
    }

    /**
     * Undo an optimistic change after the hub refused the command - unless the device has
     * reported something new in the meantime (then the hub's word stands).
     */
    fun rollback(state: HubState, optimistic: Entity, previous: Entity): HubState {
        val current = state.entities[previous.id] ?: return state
        return if (current == optimistic) replaceEntity(state, previous) else state
    }
}
