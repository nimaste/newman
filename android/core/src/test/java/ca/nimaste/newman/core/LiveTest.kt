package ca.nimaste.newman.core

import ca.nimaste.newman.core.hub.Effect
import ca.nimaste.newman.core.hub.HubReducer
import ca.nimaste.newman.core.hub.HubState
import ca.nimaste.newman.core.live.Backoff
import ca.nimaste.newman.core.live.LiveMessage
import ca.nimaste.newman.core.live.LiveSocket
import ca.nimaste.newman.core.live.LiveStatus
import ca.nimaste.newman.core.model.AuthState
import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.model.User
import ca.nimaste.newman.core.model.jsonOf
import ca.nimaste.newman.core.model.number
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.take
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.JsonObject
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList

class LiveTest {

    private val entityJson = NewmanJson.parseToJsonElement(Fixtures.text("hub/entities.json")).let {
        (it as kotlinx.serialization.json.JsonArray).first { e -> (e as JsonObject)["kind"].toString().contains("light") }
    }

    @Test
    fun parsesEveryMessageTheHubSends() {
        assertEquals(LiveMessage.Hello(1790000000.5, "dev"), LiveMessage.parse("""{"type":"hello","time":1790000000.5,"version":"dev"}"""))
        val e = LiveMessage.parse("""{"type":"entity","entity":$entityJson}""") as LiveMessage.EntityChanged
        assertEquals("light", e.entity.kind)
        assertEquals(LiveMessage.DeviceEvent("ring.x", "pressed"), LiveMessage.parse("""{"type":"event","entityId":"ring.x","event":"pressed"}"""))
        val a = LiveMessage.parse("""{"type":"activity","entry":${NewmanJson.encodeToString(ca.nimaste.newman.core.model.ActivityEntry.serializer(), Fixtures.events.first())}}""")
        assertEquals(Fixtures.events.first(), (a as LiveMessage.Activity).entry)
        assertEquals(LiveMessage.DashboardChanged("d_1", "u_1"), LiveMessage.parse("""{"type":"dashboard","id":"d_1","userId":"u_1"}"""))
        assertEquals(LiveMessage.SettingsChanged, LiveMessage.parse("""{"type":"settings"}"""))
        assertEquals(LiveMessage.Pong, LiveMessage.parse("""{"type":"pong","time":1}"""))
        assertEquals(LiveMessage.Unknown("later"), LiveMessage.parse("""{"type":"later"}"""))
        assertNull(LiveMessage.parse("not json"))
        assertNull(LiveMessage.parse("""{"no":"type"}"""))
        assertNull(LiveMessage.parse("""{"type":"entity","entity":"broken"}"""))
    }

    @Test
    fun reducerReplacesDevicesInPlaceAndAsksForReloads() {
        val list = Fixtures.entities
        var state = HubState(meta = Fixtures.meta, entities = LinkedHashMap(list.associateBy { it.id }),
            auth = AuthState(signedIn = true, user = User(id = "u_me")))
        val changed = list[3].copy(state = "something", stateLabel = "Something")
        val (s1, eff1) = HubReducer.apply(state, LiveMessage.EntityChanged(changed))
        assertEquals(Effect.NONE, eff1)
        assertEquals(changed, s1.entities[changed.id])
        assertEquals(list.map { it.id }, s1.entities.keys.toList())
        state = s1
        assertEquals(Effect.RELOAD_DASHBOARD, HubReducer.apply(state, LiveMessage.DashboardChanged("d", "u_me")).second)
        assertEquals(Effect.RELOAD_DASHBOARD, HubReducer.apply(state, LiveMessage.DashboardChanged(null, null)).second)
        assertEquals(Effect.NONE, HubReducer.apply(state, LiveMessage.DashboardChanged("d", "u_other")).second)
        assertEquals(Effect.RELOAD_AUTH, HubReducer.apply(state, LiveMessage.SettingsChanged).second)
        assertEquals(Effect.RELOAD_ALL, HubReducer.apply(state, LiveMessage.Reconnected).second)
        val entry = Fixtures.events.first().copy(id = 999_999)
        val (s2, _) = HubReducer.apply(state, LiveMessage.Activity(entry))
        assertEquals(entry, s2.activity.first())
    }

    @Test
    fun removedDevicesAreDropped() {
        assertEquals(LiveMessage.EntityRemoved("zwave.12"), LiveMessage.parse("""{"type":"entity-removed","id":"zwave.12"}"""))
        assertNull(LiveMessage.parse("""{"type":"entity-removed"}"""))
        assertEquals(LiveMessage.Unknown("entity-renamed"), LiveMessage.parse("""{"type":"entity-renamed","id":"x"}"""))
        val list = Fixtures.entities
        val state = HubState(meta = Fixtures.meta, entities = LinkedHashMap(list.associateBy { it.id }))
        val gone = list[2].id
        val (s1, eff) = HubReducer.apply(state, LiveMessage.EntityRemoved(gone))
        assertEquals(Effect.NONE, eff)
        assertNull(s1.entities[gone])
        assertEquals(list.map { it.id }.filter { it != gone }, s1.entities.keys.toList())
        assertEquals(state, HubReducer.apply(state, LiveMessage.EntityRemoved("not.here")).first)
        // A newly registered device arrives as an ordinary "entity" message and goes last.
        val added = list[0].copy(id = "new.device")
        assertEquals("new.device", HubReducer.apply(s1, LiveMessage.EntityChanged(added)).first.entities.keys.last())
    }

    @Test
    fun optimisticCommandsAndRollback() {
        val meta = Fixtures.meta
        val lock = Fixtures.entity("lock").copy(state = "UNLOCKED", stateLabel = "Unlocked")
        val k = meta.kind("lock")!!
        val opt = HubReducer.optimistic(lock, k, k.command("lock")!!, null)
        assertEquals("LOCKED", opt.state)
        assertEquals("Locked", opt.stateLabel)
        val t = Fixtures.entity("thermostat")
        val tk = meta.kind("thermostat")!!
        assertEquals("heat", HubReducer.optimistic(t, tk, tk.command("set_mode")!!, jsonOf("heat")).state)
        assertEquals(19.5, HubReducer.optimistic(t, tk, tk.command("set_temperature")!!, jsonOf(19.5)).attrs.number("targetTemp"))

        val state = HubState(entities = LinkedHashMap(mapOf(lock.id to opt)))
        assertEquals(lock, HubReducer.rollback(state, opt, lock).entities[lock.id])
        // The hub reported something newer meanwhile: keep that.
        val newer = opt.copy(updatedAt = 1.0)
        val state2 = HubState(entities = LinkedHashMap(mapOf(lock.id to newer)))
        assertEquals(newer, HubReducer.rollback(state2, opt, lock).entities[lock.id])
    }

    @Test
    fun backoffDoublesToThirtySecondsAndResets() {
        val b = Backoff()
        assertEquals(listOf(1000L, 2000L, 4000L, 8000L, 16000L, 30000L, 30000L), (1..7).map { b.nextDelay() })
        b.reset()
        assertEquals(1000L, b.nextDelay())
    }

    @Test
    fun socketDeliversMessagesAndReconnectsAfterADrop() = runBlocking {
        val server = MockWebServer()
        val serverSockets = CopyOnWriteArrayList<WebSocket>()
        fun upgrade() = MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                serverSockets += webSocket
                webSocket.send("""{"type":"hello","time":1,"version":"t"}""")
                webSocket.send("""{"type":"entity","entity":$entityJson}""")
            }
        })
        server.enqueue(upgrade())
        server.enqueue(upgrade())
        server.start()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val socket = LiveSocket(OkHttpClient(), scope, { Request.Builder().url(server.url("/api/ws?token=abc")).build() },
            Backoff(initialMs = 50, maxMs = 200))
        try {
            val got = scope.async(start = CoroutineStart.UNDISPATCHED) {
                socket.messages.filter { it !is LiveMessage.Pong }.take(5).toList()
            }
            socket.start()
            withTimeout(5000) { socket.status.first { it == LiveStatus.OPEN } }
            // The hub goes away; the app reconnects by itself and says so.
            withTimeout(5000) {
                while (serverSockets.isEmpty()) kotlinx.coroutines.delay(10)
            }
            serverSockets.first().close(1001, "restart")
            val msgs = withTimeout(10_000) { got.await() }
            assertTrue(msgs[0] is LiveMessage.Hello)
            assertTrue(msgs[1] is LiveMessage.EntityChanged)
            assertEquals(LiveMessage.Reconnected, msgs[2])
            assertTrue(msgs[3] is LiveMessage.Hello)
            val first = server.takeRequest()
            assertEquals("/api/ws?token=abc", first.path)
        } finally {
            socket.stop()
            scope.cancel()
            serverSockets.forEach { runCatching { it.close(1000, null) } }
            // MockWebServer may still be tearing down the last socket; that's not what's tested.
            runCatching { server.shutdown() }
        }
    }
}
