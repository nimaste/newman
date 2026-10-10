package ca.nimaste.newman.core

import ca.nimaste.newman.core.live.LiveStreamPool
import ca.nimaste.newman.core.live.LiveVideoSession
import ca.nimaste.newman.core.live.LiveVideoSession.State
import ca.nimaste.newman.core.live.OkHttpLiveOpener
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class LiveVideoTest {

    // -- fakes -----------------------------------------------------------------------------------

    private class FakeSocket : LiveVideoSession.Socket {
        val sent = mutableListOf<String>()
        var closed = false
        override fun send(text: String): Boolean { sent += text; return true }
        override fun close() { closed = true }
    }

    private class Rig {
        val evals = mutableListOf<String>()
        val states = mutableListOf<State>()
        val sockets = mutableListOf<Pair<FakeSocket, LiveVideoSession.SocketEvents>>()
        val timers = mutableListOf<Pair<Long, () -> Unit>>()
        var canOpen = true
        val session = LiveVideoSession(
            page = { evals += it },
            opener = { ev -> if (!canOpen) null else FakeSocket().also { sockets += it to ev } },
            scheduler = { ms, block -> val t = ms to block; timers += t; { timers.remove(t) } },
            onState = { states += it },
            fit = "cover",
        )
        fun fireTimers() = timers.toList().also { timers.clear() }.forEach { it.second() }
    }

    // -- the session ---------------------------------------------------------------------------

    @Test
    fun startsWhenThePageIsReadyAndOpensTheSocketWithTheCodecs() {
        val r = Rig()
        r.session.start()
        assertTrue("nothing to call before the page loads", r.evals.isEmpty())
        r.session.onPageMessage("""{"type":"ready"}""")
        assertEquals(listOf("NewmanLive.fit(\"cover\")", "NewmanLive.start()"), r.evals)
        r.session.onPageMessage("""{"type":"state","value":"loading"}""")
        r.session.onPageMessage("""{"type":"codecs","value":"avc1.640029,mp4a.40.2"}""")
        val (sock, _) = r.sockets.single()
        assertEquals(listOf("""{"type":"mse","value":"avc1.640029,mp4a.40.2"}"""), sock.sent)
        r.session.onPageMessage("""{"type":"state","value":"playing"}""")
        assertEquals(listOf(State.LOADING, State.PLAYING), r.states)
    }

    @Test
    fun socketMessagesAreRoutedToThePageInOrder() {
        val r = Rig()
        r.session.onPageMessage("""{"type":"ready"}""")
        r.session.start()
        r.session.onPageMessage("""{"type":"codecs","value":"avc1.640029"}""")
        val ev = r.sockets.single().second
        r.evals.clear()
        ev.onText("""{"type":"mse","value":"video/mp4; codecs=\"avc1.640029\""}""")
        ev.onBytes(byteArrayOf(0, 1, 2, -1))
        ev.onText("line sep")
        assertEquals("NewmanLive.text(\"{\\\"type\\\":\\\"mse\\\",\\\"value\\\":\\\"video/mp4; codecs=\\\\\\\"avc1.640029\\\\\\\"\\\"}\")", r.evals[0])
        assertEquals("NewmanLive.data(\"${Base64.getEncoder().encodeToString(byteArrayOf(0, 1, 2, -1))}\")", r.evals[1])
        assertEquals("NewmanLive.text(\"line\\u2028sep\")", r.evals[2])
    }

    @Test
    fun failedShowsTheStillAndRetriesAfterFifteenSeconds() {
        val r = Rig()
        r.session.onPageMessage("""{"type":"ready"}""")
        r.session.start()
        r.session.onPageMessage("""{"type":"codecs","value":"avc1.640029"}""")
        val first = r.sockets.single().first
        r.session.onPageMessage("""{"type":"state","value":"failed"}""")
        assertTrue(first.closed)
        assertEquals(State.FAILED, r.session.state)
        assertEquals(15_000L, r.timers.single().first)
        r.evals.clear()
        r.fireTimers()
        assertEquals(listOf("NewmanLive.start()"), r.evals)
    }

    @Test
    fun retryReconnectsRightAway() {
        val r = Rig()
        r.session.onPageMessage("""{"type":"ready"}""")
        r.session.start()
        r.session.onPageMessage("""{"type":"codecs","value":"avc1.640029,hvc1.1.6.L153.B0"}""")
        val first = r.sockets.single().first
        r.evals.clear()
        r.session.onPageMessage("""{"type":"state","value":"retry"}""")
        assertTrue(first.closed)
        assertEquals(listOf("NewmanLive.start()"), r.evals)
        assertTrue(r.timers.isEmpty())
        r.session.onPageMessage("""{"type":"codecs","value":"avc1.640029"}""")
        assertEquals(2, r.sockets.size)
        assertFalse(r.sockets[1].first.closed)
    }

    @Test
    fun aDroppedSocketStopsThePageAndRetriesLater() {
        val r = Rig()
        r.session.onPageMessage("""{"type":"ready"}""")
        r.session.start()
        r.session.onPageMessage("""{"type":"codecs","value":"avc1.640029"}""")
        r.evals.clear()
        r.sockets.single().second.onClosed()
        assertEquals(listOf("NewmanLive.stop()"), r.evals)
        assertEquals(State.FAILED, r.session.state)
        assertEquals(1, r.timers.size)
    }

    @Test
    fun stopClosesEverythingAndIgnoresLateMessages() {
        val r = Rig()
        r.session.onPageMessage("""{"type":"ready"}""")
        r.session.start()
        r.session.onPageMessage("""{"type":"codecs","value":"avc1.640029"}""")
        r.session.onPageMessage("""{"type":"state","value":"failed"}""") // a retry is pending
        r.session.start() // (still running: no-op)
        val (sock, ev) = r.sockets.single()
        r.session.stop()
        assertTrue(sock.closed)
        assertTrue("pending retry cancelled", r.timers.isEmpty())
        assertEquals("NewmanLive.stop()", r.evals.last())
        assertEquals(State.IDLE, r.session.state)
        r.evals.clear()
        ev.onText("late")
        ev.onBytes(byteArrayOf(1))
        r.session.onPageMessage("""{"type":"codecs","value":"avc1.640029"}""")
        assertTrue(r.evals.isEmpty())
        assertEquals(1, r.sockets.size)
        // Started again (e.g. the app came back to the foreground).
        r.session.start()
        assertEquals(listOf("NewmanLive.start()"), r.evals)
    }

    @Test
    fun noServerMeansFailedWithARetry() {
        val r = Rig()
        r.canOpen = false
        r.session.onPageMessage("""{"type":"ready"}""")
        r.session.start()
        r.session.onPageMessage("""{"type":"codecs","value":"avc1.640029"}""")
        assertEquals(State.FAILED, r.session.state)
        assertEquals(1, r.timers.size)
        r.session.onPageMessage("not json")
        r.session.onPageMessage("""{"type":"later"}""")
    }

    @Test
    fun liveUrls() {
        val e = Fixtures.entities.first { it.live != null }
        assertEquals("https://home.garito.ca/${e.live}?quality=sub",
            LiveVideoSession.url("https://home.garito.ca/", e.live!!, "sub").toString())
        assertNull(LiveVideoSession.url(null, e.live!!, "main"))
    }

    @Test
    fun okHttpOpenerCarriesTextAndBinaryBothWays() = runBlocking {
        val server = MockWebServer()
        val got = mutableListOf<String>()
        val serverGot = CountDownLatch(1)
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                got += text
                serverGot.countDown()
                webSocket.send("""{"type":"mse","value":"video/mp4"}""")
                webSocket.send(byteArrayOf(9, 8, 7).toByteString())
            }
            override fun onOpen(webSocket: WebSocket, response: Response) = Unit
        }))
        server.start()
        val received = mutableListOf<String>()
        val done = CountDownLatch(2)
        val opener = OkHttpLiveOpener(OkHttpClient()) { server.url("/api/entities/x/live?quality=sub") }
        val sock = opener.open(object : LiveVideoSession.SocketEvents {
            override fun onText(text: String) { received += "t:$text"; done.countDown() }
            override fun onBytes(bytes: ByteArray) { received += "b:${bytes.toList()}"; done.countDown() }
            override fun onClosed() = Unit
        })!!
        sock.send("""{"type":"mse","value":"avc1.640029"}""")
        assertTrue(serverGot.await(5, TimeUnit.SECONDS))
        assertTrue(done.await(5, TimeUnit.SECONDS))
        assertEquals(listOf("""{"type":"mse","value":"avc1.640029"}"""), got)
        assertEquals(listOf("""t:{"type":"mse","value":"video/mp4"}""", "b:[9, 8, 7]"), received)
        assertEquals("/api/entities/x/live?quality=sub", server.takeRequest().path)
        sock.close()
        runCatching { server.shutdown() }
        Unit
    }

    // -- the pool -----------------------------------------------------------------------------

    @Test
    fun poolCapsTileStreamsAndHandsSlotsOnInOrder() {
        val pool = LiveStreamPool(4)
        val granted = mutableListOf<String>()
        (1..6).forEach { i -> pool.request("t$i", priority = false) { granted += "t$i" } }
        assertEquals(listOf("t1", "t2", "t3", "t4"), granted)
        assertEquals(4, pool.activeCount)
        assertEquals(2, pool.waitingCount)
        // Full screen never waits and doesn't take a tile's slot.
        pool.request("full", priority = true) { granted += "full" }
        assertEquals("full", granted.last())
        assertEquals(4, pool.activeCount)
        pool.release("full")
        assertEquals(2, pool.waitingCount)
        // A waiting tile that goes away is forgotten.
        pool.release("t6")
        assertEquals(1, pool.waitingCount)
        pool.release("t2")
        assertEquals("t5", granted.last())
        assertTrue(pool.isActive("t5"))
        assertFalse(pool.isActive("t2"))
        pool.release("t1")
        assertEquals(3, pool.activeCount)
        assertEquals(0, pool.waitingCount)
        // Asking twice doesn't grant twice.
        pool.request("t3", priority = false) { granted += "again" }
        assertFalse("again" in granted)
    }

    @Suppress("unused")
    private fun unusedByteString(b: ByteString) = b
}
