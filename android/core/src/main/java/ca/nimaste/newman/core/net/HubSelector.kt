package ca.nimaste.newman.core.net

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.os.SystemClock
import ca.nimaste.newman.core.settings.ConnectionStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import okhttp3.OkHttpClient

/**
 * The phone's choice between the saved hub's home-network and outside addresses ([HubAddressPolicy]):
 * home whenever /api/hello there answers as this hub; outside only after home failed for 2 minutes (or
 * the phone's network changed) and outside answers as the same hub; back home as soon as it answers.
 * Checked at start, on every network change, whenever [recheck] is called (back in front, a failed
 * load), and every 30 s while on the outside address or while home is failing. (The wall tablet doesn't
 * use this: it stays on the address it was set up with - [WallAddress].)
 *
 * [learnHome]: fill in a missing home address from the hub's own /api/hello.
 */
class HubSelector(
    context: Context,
    private val store: ConnectionStore,
    private val client: OkHttpClient,
    private val scope: CoroutineScope,
    private val learnHome: Boolean,
) {
    /** Called (off the main thread) after the address in use changed. */
    @Volatile var onChanged: (String) -> Unit = {}

    private val cm = context.applicationContext.getSystemService(ConnectivityManager::class.java)
    private val lock = Mutex()
    private var pending: Job? = null
    private var poll: Job? = null
    private val policy = HubAddressPolicy()
    private var started = false

    private val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) = networkChanged()
        override fun onLost(network: Network) = networkChanged()
    }

    private fun networkChanged() {
        policy.networkChanged()
        recheck(1_500)
    }

    fun start() {
        if (started) return
        started = true
        try {
            cm?.registerDefaultNetworkCallback(callback)
        } catch (e: Exception) {
            // (some builds limit callbacks) - still checked at start and on recheck()
        }
        recheck(0)
    }

    fun stop() {
        if (!started) return
        started = false
        pending?.cancel()
        poll?.cancel()
        try {
            cm?.unregisterNetworkCallback(callback)
        } catch (e: Exception) {
            // not registered
        }
    }

    /** Check again (after [delayMs], so a burst of network events runs it once). */
    fun recheck(delayMs: Long = 0) {
        pending?.cancel()
        pending = scope.launch {
            if (delayMs > 0) delay(delayMs)
            lock.withLock { check() }
        }
    }

    private suspend fun answers(url: String, id: String?): Boolean {
        val r = ConnectCheck.test(client, url, null, id, timeoutMs = 2_500)
        if (r is ConnectResult.Ok) store.learn(r.hello.id, r.hello.name, null)
        return r is ConnectResult.Ok
    }

    private suspend fun check() {
        val hub = store.hub
        if (hub.localUrl == null && hub.outsideUrl == null) return
        val current = store.serverUrl
        val chosen = policy.pick(hub, current, SystemClock.elapsedRealtime()) { answers(it, hub.id) } ?: return
        if (chosen != current) {
            store.serverUrl = chosen
            onChanged(chosen)
        }
        if (started && policy.probing(hub, chosen)) {
            poll?.cancel()
            poll = scope.launch {
                delay(HubAddressPolicy.PROBE_MS)
                recheck(0)
            }
        }
        if (learnHome && store.hub.localUrl == null && chosen == hub.outsideUrl) {
            // Through the outside address: ask the hub for its home address (used from the next check).
            val r = ConnectCheck.test(client, chosen, null, hub.id, timeoutMs = 8_000)
            if (r is ConnectResult.Ok) store.learn(r.hello.id, r.hello.name, r.hello.localUrls.firstOrNull())
        }
    }
}
