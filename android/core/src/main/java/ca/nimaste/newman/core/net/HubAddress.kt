package ca.nimaste.newman.core.net

/**
 * The wall tablet's address: the one it was set up with (chosen in Connect / Change server), always.
 * It never moves between the home and outside addresses by itself - through a hub restart or an outage
 * it just keeps retrying that address. Installs from before the choice was kept: the home address.
 */
object WallAddress {
    fun address(hub: SavedHub, chosen: String?, current: String?): String? =
        chosen ?: hub.localUrl ?: hub.outsideUrl ?: current
}

/**
 * Which address of the saved hub the phone uses, over time. The home-network address whenever it
 * answers as this hub. A hub restart (seconds of no answer) doesn't move it: it goes to the outside
 * address only after the home one has failed for [failoverMs] in a row - or at once after the phone's
 * network changed (left home) - AND the outside one answers as the same hub. While on the outside
 * address (or while the home one is failing) the home one is probed every [PROBE_MS], and the phone
 * goes back as soon as it answers.
 */
class HubAddressPolicy(private val failoverMs: Long = FAILOVER_MS) {
    /** When the home address first failed in the current run of failures (elapsed ms), else null. */
    var localDownSince: Long? = null
        private set
    private var networkChanged = false

    /** The phone's network changed: the next failing home probe may switch at once. */
    fun networkChanged() {
        networkChanged = true
    }

    /** Probe again in [PROBE_MS]: on the outside address, or the home one is failing. */
    fun probing(hub: SavedHub, current: String?): Boolean =
        hub.localUrl != null && (localDownSince != null || (current != null && current == hub.outsideUrl))

    /** The address to use now ([now]: elapsed ms). [answers]: does that address answer /api/hello as this hub. */
    suspend fun pick(hub: SavedHub, current: String?, now: Long, answers: suspend (String) -> Boolean): String? {
        val moved = networkChanged
        networkChanged = false
        val local = hub.localUrl ?: return hub.outsideUrl ?: current
        if (answers(local)) {
            localDownSince = null
            return local
        }
        val since = localDownSince ?: now.also { localDownSince = it }
        val outside = hub.outsideUrl ?: return local
        if (current == outside) return outside // already outside: stays until the home address answers
        if (current == local) {
            return if ((moved || now - since >= failoverMs) && answers(outside)) outside else local
        }
        // No address chosen yet (or one from before): whichever answers, the home one first.
        return if (answers(outside)) outside else local
    }

    companion object {
        const val FAILOVER_MS = 120_000L
        const val PROBE_MS = 30_000L
    }
}
