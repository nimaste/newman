package ca.nimaste.newman.core.live

import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.model.asStringOrNull
import ca.nimaste.newman.core.net.HubHttp
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import java.util.Base64

/**
 * Live camera video for the phone app. The app owns the hub's live socket
 * (GET api/entities/{id}/live?quality=sub|main, with the mTLS client, Bearer token and Cloudflare
 * headers); the shared player page (mobile-shared/live-player.html) only decodes. This class is
 * the routing between the two - see the protocol at the top of that page:
 *
 *   page "ready"        -> NewmanLive.fit(...) and, while running, NewmanLive.start()
 *   page "codecs" value -> open the socket, send {"type":"mse","value":value}
 *   socket text / bytes -> NewmanLive.text(<json string>) / NewmanLive.data("<base64>")
 *   page "state"        -> loading / playing / failed (still again, retry in 15 s) / retry (now)
 *
 * Plain JVM (no Android types), so it is unit-tested; the app supplies the page, socket and timer.
 */
class LiveVideoSession(
    private val page: Page,
    private val opener: SocketOpener,
    private val scheduler: Scheduler,
    private val onState: (State) -> Unit,
    /** "cover" for tiles, "contain" full screen. */
    private val fit: String = "contain",
    private val retryDelayMs: Long = 15_000,
) {
    enum class State { IDLE, LOADING, PLAYING, FAILED }

    /** Runs JavaScript in the player page (in call order). */
    fun interface Page {
        fun eval(js: String)
    }

    interface Socket {
        fun send(text: String): Boolean
        fun close()
    }

    interface SocketEvents {
        fun onText(text: String)
        fun onBytes(bytes: ByteArray)
        fun onClosed()
    }

    fun interface SocketOpener {
        /** Open the live socket; null when there is nowhere to connect (no server / no sign-in). */
        fun open(events: SocketEvents): Socket?
    }

    fun interface Scheduler {
        /** Run [block] after [delayMs]; returns a canceller. */
        fun schedule(delayMs: Long, block: () -> Unit): () -> Unit
    }

    private var pageReady = false
    private var running = false
    private var socket: Socket? = null
    private var generation = 0
    private var cancelRetry: (() -> Unit)? = null
    var state: State = State.IDLE
        private set

    /** Begin (or keep) playing. Safe to call before the page has loaded. */
    @Synchronized
    fun start() {
        if (running) return
        running = true
        cancelRetry?.invoke()
        cancelRetry = null
        if (pageReady) page.eval("NewmanLive.start()")
    }

    /** Stop playing: the page tears down, the socket closes, no retries. */
    @Synchronized
    fun stop() {
        if (!running) return
        running = false
        cancelRetry?.invoke()
        cancelRetry = null
        closeSocket()
        if (pageReady) page.eval("NewmanLive.stop()")
        setState(State.IDLE)
    }

    val isRunning: Boolean @Synchronized get() = running

    /** A message from the page (NewmanLiveHost.post / messageHandlers.newmanLive). */
    @Synchronized
    fun onPageMessage(json: String) {
        val msg = try {
            NewmanJson.parseToJsonElement(json) as? JsonObject
        } catch (e: Exception) {
            null
        } ?: return
        when (msg["type"].asStringOrNull) {
            "ready" -> {
                pageReady = true
                page.eval("NewmanLive.fit(${jsString(fit)})")
                if (running) page.eval("NewmanLive.start()")
            }
            "codecs" -> if (running) connect(msg["value"].asStringOrNull ?: "")
            "state" -> when (msg["value"].asStringOrNull) {
                "loading" -> setState(State.LOADING)
                "playing" -> setState(State.PLAYING)
                "failed" -> failed()
                "retry" -> if (running) {
                    // The page dropped H.265; start over now (it sends new codecs).
                    closeSocket()
                    page.eval("NewmanLive.start()")
                }
            }
        }
    }

    private fun connect(codecs: String) {
        closeSocket()
        val gen = ++generation
        val events = object : SocketEvents {
            override fun onText(text: String) = onSocket(gen) { page.eval("NewmanLive.text(${jsString(text)})") }
            override fun onBytes(bytes: ByteArray) = onSocket(gen) {
                page.eval("NewmanLive.data(\"${Base64.getEncoder().encodeToString(bytes)}\")")
            }
            override fun onClosed() = onSocket(gen) {
                // The hub or the network dropped the stream: freeze-frame is worse than the still.
                socket = null
                page.eval("NewmanLive.stop()")
                failed()
            }
        }
        val s = opener.open(events)
        if (s == null) {
            failed()
            return
        }
        socket = s
        s.send(buildJsonObject { put("type", "mse"); put("value", codecs) }.toString())
    }

    private fun onSocket(gen: Int, block: () -> Unit) {
        synchronized(this) {
            if (gen != generation || !running || socket == null) return
            block()
        }
    }

    private fun failed() {
        closeSocket()
        setState(State.FAILED)
        if (!running) return
        cancelRetry?.invoke()
        cancelRetry = scheduler.schedule(retryDelayMs) {
            synchronized(this) {
                cancelRetry = null
                if (running && pageReady) page.eval("NewmanLive.start()")
            }
        }
    }

    private fun closeSocket() {
        generation++
        socket?.close()
        socket = null
    }

    private fun setState(s: State) {
        if (state == s) return
        state = s
        onState(s)
    }

    companion object {
        /** A JSON string literal, safe inside a JS expression. */
        fun jsString(s: String): String =
            JsonPrimitive(s).toString().replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

        /** <server>/api/entities/{id}/live?quality=sub|main for an entity's `live` path. */
        fun url(serverUrl: String?, livePath: String, quality: String): HttpUrl? =
            HubHttp.absolute(serverUrl, livePath)?.newBuilder()?.setQueryParameter("quality", quality)?.build()
    }
}

/** Opens the live socket with the app's OkHttp client (mTLS + hub credentials via its interceptor). */
class OkHttpLiveOpener(private val client: OkHttpClient, private val url: () -> HttpUrl?) : LiveVideoSession.SocketOpener {
    override fun open(events: LiveVideoSession.SocketEvents): LiveVideoSession.Socket? {
        val u = url() ?: return null
        val ws = client.newWebSocket(Request.Builder().url(u).build(), object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) = events.onText(text)
            override fun onMessage(webSocket: WebSocket, bytes: ByteString) = events.onBytes(bytes.toByteArray())
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
                events.onClosed()
            }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = events.onClosed()
        })
        return object : LiveVideoSession.Socket {
            override fun send(text: String) = ws.send(text)
            override fun close() {
                ws.cancel()
            }
        }
    }
}

/**
 * At most [max] tile streams at once; [priority] requests (full screen) never wait or count.
 * Waiting requests are granted in order as slots free up.
 */
class LiveStreamPool(val max: Int = 4) {
    private val active = LinkedHashSet<String>()
    private val priorityKeys = HashSet<String>()
    private val waiting = LinkedHashMap<String, () -> Unit>()

    /** Ask for a slot; [onGrant] runs now or when one frees up (never after [release]). */
    fun request(key: String, priority: Boolean, onGrant: () -> Unit) {
        val grantNow = synchronized(this) {
            when {
                key in active || key in priorityKeys -> false
                priority -> { priorityKeys += key; true }
                active.size < max -> { active += key; true }
                else -> { waiting[key] = onGrant; false }
            }
        }
        if (grantNow) onGrant()
    }

    fun release(key: String) {
        val granted = mutableListOf<() -> Unit>()
        synchronized(this) {
            priorityKeys -= key
            waiting.remove(key)
            if (active.remove(key)) {
                while (active.size < max && waiting.isNotEmpty()) {
                    val (k, grant) = waiting.entries.first()
                    waiting.remove(k)
                    active += k
                    granted += grant
                }
            }
        }
        granted.forEach { it() }
    }

    val activeCount: Int @Synchronized get() = active.size
    val waitingCount: Int @Synchronized get() = waiting.size
    @Synchronized fun isActive(key: String) = key in active || key in priorityKeys
}
