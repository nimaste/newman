package ca.nimaste.newman.core.live

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

/** Reconnect delays: 1 s, doubling to 30 s; back to 1 s once a connection opens. */
class Backoff(private val initialMs: Long = 1_000, private val maxMs: Long = 30_000) {
    private var next = initialMs
    fun nextDelay(): Long = next.also { next = (next * 2).coerceAtMost(maxMs) }
    fun reset() {
        next = initialMs
    }
}

enum class LiveStatus { CONNECTING, OPEN, DOWN, STOPPED }

/**
 * The hub's push channel (GET /api/ws) over one OkHttp WebSocket, sharing the app's mTLS client.
 * Headers (Bearer token, Cloudflare Access) come from the client's interceptors; [request] may
 * also put `?token=` on the URL. Reconnects with [Backoff] and emits [LiveMessage.Reconnected]
 * after every reconnect so the app reloads what it missed.
 */
class LiveSocket(
    private val client: OkHttpClient,
    private val scope: CoroutineScope,
    /** Builds the request to open, or null while there is no server / no sign-in. */
    private val request: () -> Request?,
    private val backoff: Backoff = Backoff(),
    private val pingEveryMs: Long = 25_000,
) {
    private val _messages = MutableSharedFlow<LiveMessage>(extraBufferCapacity = 256)
    val messages: SharedFlow<LiveMessage> = _messages.asSharedFlow()

    private val _status = MutableStateFlow(LiveStatus.STOPPED)
    val status: StateFlow<LiveStatus> = _status.asStateFlow()

    @Volatile private var socket: WebSocket? = null
    @Volatile private var generation = 0
    private var everOpen = false
    private var retryJob: Job? = null
    private var pingJob: Job? = null

    @Synchronized
    fun start() {
        if (_status.value != LiveStatus.STOPPED) return
        _status.value = LiveStatus.CONNECTING
        connect()
    }

    @Synchronized
    fun stop() {
        generation++
        retryJob?.cancel()
        pingJob?.cancel()
        socket?.close(1000, "bye")
        socket = null
        everOpen = false
        backoff.reset()
        _status.value = LiveStatus.STOPPED
    }

    /** Drop the current connection and connect again now (e.g. after the server changed). */
    @Synchronized
    fun restart() {
        stop()
        start()
    }

    @Synchronized
    private fun connect() {
        if (_status.value == LiveStatus.STOPPED) return
        val req = request()
        if (req == null) {
            _status.value = LiveStatus.DOWN
            scheduleRetry()
            return
        }
        val gen = ++generation
        socket = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) = onEvent(gen) {
                backoff.reset()
                _status.value = LiveStatus.OPEN
                if (everOpen) _messages.tryEmit(LiveMessage.Reconnected)
                everOpen = true
                pingJob?.cancel()
                pingJob = scope.launch {
                    while (isActive) {
                        delay(pingEveryMs)
                        webSocket.send("ping")
                    }
                }
            }

            override fun onMessage(webSocket: WebSocket, text: String) = onEvent(gen) {
                LiveMessage.parse(text)?.let { _messages.tryEmit(it) }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = onEvent(gen) { dropped() }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = onEvent(gen) { dropped() }
        })
    }

    /** Runs [block] only for events of the current connection (late callbacks of old ones are ignored). */
    private fun onEvent(gen: Int, block: () -> Unit) {
        synchronized(this) {
            if (gen != generation || _status.value == LiveStatus.STOPPED) return
            block()
        }
    }

    private fun dropped() {
        pingJob?.cancel()
        socket = null
        _status.value = LiveStatus.DOWN
        scheduleRetry()
    }

    private fun scheduleRetry() {
        retryJob?.cancel()
        val wait = backoff.nextDelay()
        retryJob = scope.launch {
            delay(wait)
            synchronized(this@LiveSocket) {
                if (_status.value != LiveStatus.STOPPED) {
                    _status.value = LiveStatus.CONNECTING
                    connect()
                }
            }
        }
    }
}
