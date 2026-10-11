package ca.nimaste.newman.core

import ca.nimaste.newman.core.api.ApiErrors
import ca.nimaste.newman.core.api.HubException
import ca.nimaste.newman.core.api.LoginResult
import ca.nimaste.newman.core.api.NewmanApi
import ca.nimaste.newman.core.api.hubCall
import ca.nimaste.newman.core.model.LoginRequest
import ca.nimaste.newman.core.model.LoginResponse
import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.model.User
import ca.nimaste.newman.core.mtls.MTLSKeyManager
import ca.nimaste.newman.core.mtls.P12Validator
import ca.nimaste.newman.core.net.HubHttp
import ca.nimaste.newman.core.net.HubInterceptor
import ca.nimaste.newman.core.net.NoServerException
import ca.nimaste.newman.core.net.SavedHub
import ca.nimaste.newman.core.net.ServerAddress
import ca.nimaste.newman.core.push.ActionRoutes
import ca.nimaste.newman.core.push.PushPayload
import ca.nimaste.newman.core.settings.ConnectionStore
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.util.Date

class ConnectionTest {

    // -- certificates ----------------------------------------------------------------------------

    @Test
    fun validP12IsAccepted() {
        val r = P12Validator.validate(Fixtures.bytes("p12/valid.p12"), "testpass")
        r as P12Validator.Result.Valid
        assertEquals("Test Phone", r.name)
        assertTrue(r.notAfterMillis > System.currentTimeMillis())
        val (key, chain) = P12Validator.keyAndChain(Fixtures.bytes("p12/valid.p12"), "testpass")!!
        assertEquals("EC", key.algorithm)
        assertEquals(1, chain.size)
    }

    @Test
    fun badP12sAreRejectedWithAClearReason() {
        val wrong = P12Validator.validate(Fixtures.bytes("p12/valid.p12"), "nope") as P12Validator.Result.Invalid
        assertTrue(wrong.message, wrong.message.contains("password"))
        val expired = P12Validator.validate(Fixtures.bytes("p12/expired.p12"), "testpass") as P12Validator.Result.Invalid
        assertTrue(expired.message, expired.message.contains("expired"))
        val noKey = P12Validator.validate(Fixtures.bytes("p12/certonly.p12"), "testpass") as P12Validator.Result.Invalid
        assertTrue(noKey.message, noKey.message.contains("no private key"))
        val junk = P12Validator.validate("hello, not a certificate".toByteArray(), "x") as P12Validator.Result.Invalid
        assertTrue(junk.message, junk.message.contains(".p12"))
        assertTrue(P12Validator.validate(ByteArray(0), "x") is P12Validator.Result.Invalid)
        // Valid today, not before it starts.
        val early = P12Validator.validate(Fixtures.bytes("p12/valid.p12"), "testpass", Date(0)) as P12Validator.Result.Invalid
        assertTrue(early.message, early.message.contains("isn't valid until"))
    }

    @Test
    fun keyManagerOffersTheCertificateOnlyWhenStored() {
        var stored: Pair<java.security.PrivateKey, Array<java.security.cert.X509Certificate>>? = null
        val km = MTLSKeyManager { stored }
        assertNull(km.chooseClientAlias(arrayOf("EC"), null, null))
        stored = P12Validator.keyAndChain(Fixtures.bytes("p12/valid.p12"), "testpass")
        val alias = km.chooseClientAlias(arrayOf("EC"), null, null)
        assertNotNull(alias)
        assertEquals(stored!!.first, km.getPrivateKey(alias))
        assertEquals("Test Phone", P12Validator.commonName(km.getCertificateChain(alias)!![0].subjectX500Principal.name))
    }

    // -- addresses -------------------------------------------------------------------------------

    @Test
    fun notificationButtonsGoToTheHubsAddressesFirstAndTheActionUrlLast() {
        val action = "https://home.garito.ca/api/public/actions"
        fun snap(server: String?, local: String?, outside: String?) =
            ConnectionStore.Snapshot(server, null, null, null, null, SavedHub("hub1", "Home", local, outside))
        // At home: the home address, then outside (which is the action_url - not twice).
        assertEquals(listOf("http://10.1.1.2:8093/api/public/actions", action),
            ActionRoutes.urls(snap("http://10.1.1.2:8093/", "http://10.1.1.2:8093/", "https://home.garito.ca/"), action))
        // Away on another outside address (with a path prefix): that first, home next, action_url last.
        assertEquals(listOf("https://away.example/newman/api/public/actions", "http://10.1.1.2:8093/api/public/actions", action),
            ActionRoutes.urls(snap("https://away.example/newman/", "http://10.1.1.2:8093/", "https://away.example/newman/"), action))
        // No hub saved: only the push's own address.
        assertEquals(listOf(action), ActionRoutes.urls(snap(null, null, null), action))
        assertEquals(emptyList<String>(), ActionRoutes.urls(snap(null, null, null), null))
        assertTrue(ActionRoutes.tryNext(null))
        assertTrue(ActionRoutes.tryNext(502) && ActionRoutes.tryNext(530))
        assertFalse(ActionRoutes.tryNext(200) || ActionRoutes.tryNext(401) || ActionRoutes.tryNext(409))
        assertTrue(ca.nimaste.newman.core.push.PressTiming.BUDGET_SECONDS < 30)
    }

    @Test
    fun theHubsOtherAddressGetsTheSameCredentials() = runBlocking {
        val home = MockWebServer().apply { start() }
        val outside = MockWebServer().apply { start() }
        try {
            val snap = ConnectionStore.Snapshot(home.url("/").toString(), "cf-id", "cf-secret", "tok123", "Chris",
                SavedHub("hub1", "Home", home.url("/").toString(), outside.url("/").toString()))
            val api = NewmanApi.create(OkHttpClient.Builder().addInterceptor(HubInterceptor({ snap }, "Newman-Test")).build())
            outside.enqueue(MockResponse().setBody("""{"ok":true,"accepted":true}"""))
            val res = api.pressAction(ActionRoutes.urls(snap, null)[1],
                ca.nimaste.newman.core.model.ActionRequest("lock_all", "run", "dev", "1", "t"))
            assertTrue(res.isSuccessful)
            val o = outside.takeRequest()
            assertEquals("/api/public/actions", o.path)
            assertEquals("cf-id", o.getHeader("CF-Access-Client-Id"))
            assertEquals("cf-secret", o.getHeader("CF-Access-Client-Secret"))
            assertNull(o.getHeader("Authorization")) // a public action carries the push's token, not the session
        } finally {
            home.shutdown()
            outside.shutdown()
        }
    }

    @Test
    fun serverAddresses() {
        assertEquals("https://home.garito.ca/", ServerAddress.normalize("home.garito.ca"))
        assertEquals("https://home.garito.ca/", ServerAddress.normalize(" https://home.garito.ca "))
        assertEquals("http://10.1.1.2:8093/", ServerAddress.normalize("http://10.1.1.2:8093"))
        assertEquals("https://example.com/newman/", ServerAddress.normalize("https://example.com/newman?x=1"))
        assertNull(ServerAddress.normalize("http://example.com"))
        assertNull(ServerAddress.normalize(""))
        listOf("10.1.1.2", "192.168.0.10", "172.20.1.1", "127.0.0.1", "localhost", "newman.local", "hub.home.arpa", "fd00::1")
            .forEach { assertTrue(it, ServerAddress.isPrivateHost(it)) }
        listOf("8.8.8.8", "172.32.0.1", "home.garito.ca", "11.0.0.1").forEach { assertFalse(it, ServerAddress.isPrivateHost(it)) }
    }

    @Test
    fun interceptorRewritesToTheServerAndAddsCredentialsOnlyThere() = runBlocking {
        val hub = MockWebServer().apply { start() }
        val other = MockWebServer().apply { start() }
        try {
            var snap = ConnectionStore.Snapshot(hub.url("/prefix/").toString(), "cf-id", "cf-secret", "tok123", "Chris")
            val client = OkHttpClient.Builder().addInterceptor(HubInterceptor({ snap }, "Newman-Test")).build()
            val api = NewmanApi.create(client)
            hub.enqueue(MockResponse().setBody(Fixtures.text("hub/meta.json")))
            val meta = api.meta()
            assertTrue(meta.kinds.isNotEmpty())
            val req = hub.takeRequest()
            assertEquals("/prefix/api/meta", req.path)
            assertEquals("Bearer tok123", req.getHeader("Authorization"))
            assertEquals("cf-id", req.getHeader("CF-Access-Client-Id"))
            assertEquals("cf-secret", req.getHeader("CF-Access-Client-Secret"))
            assertEquals("Newman-Test", req.getHeader("User-Agent"))

            // Sign-in never carries an old token.
            hub.enqueue(MockResponse().setBody("""{"ok":true,"token":"new","user":{"id":"u","name":"Chris"}}"""))
            val login = LoginResult.from(api.login(LoginRequest("chris", "pw", deviceName = "Pixel")))
            assertEquals(LoginResult.Success("new", User(id = "u", name = "Chris")), login)
            val lr = hub.takeRequest()
            assertNull(lr.getHeader("Authorization"))
            assertNull(lr.getHeader(HubInterceptor.NO_AUTH))
            val sent = NewmanJson.parseToJsonElement(lr.body.readUtf8()).jsonObject
            assertEquals("app", sent["kind"]!!.jsonPrimitive.content)
            assertFalse("no code unless asked", sent.containsKey("code"))

            // Another host (a notification's absolute action_url elsewhere) gets no hub credentials.
            other.enqueue(MockResponse().setBody("""{"ok":true}"""))
            api.pressAction(other.url("/api/public/actions").toString(),
                ca.nimaste.newman.core.model.ActionRequest("dismiss", "run", "dev", "1", "t"))
            val o = other.takeRequest()
            assertNull(o.getHeader("Authorization"))
            assertNull(o.getHeader("CF-Access-Client-Id"))

            // Errors carry the hub's own words.
            hub.enqueue(MockResponse().setResponseCode(401).setBody("""{"error":"Please sign in","signIn":true}"""))
            try {
                hubCall { api.entities() }
                fail()
            } catch (e: HubException) {
                assertEquals("Please sign in", ApiErrors.message(e))
                assertTrue(ApiErrors.isSignedOut(e))
            }

            // No server chosen yet.
            snap = snap.copy(serverUrl = null)
            try {
                api.meta()
                fail()
            } catch (e: NoServerException) {
                assertTrue(ApiErrors.message(e).contains("address"))
            }
        } finally {
            hub.shutdown()
            other.shutdown()
        }
    }

    @Test
    fun liveAndImageUrls() {
        val snap = ConnectionStore.Snapshot("https://home.garito.ca/", null, null, "abc", null)
        assertEquals("https://home.garito.ca/api/ws?token=abc", HubHttp.liveRequest(snap)!!.url.toString())
        assertNull(HubHttp.liveRequest(snap.copy(token = null)))
        assertEquals("https://home.garito.ca/api/entities/frigate.camera_garage/still.jpg?h=480",
            HubHttp.absolute(snap.serverUrl, "api/entities/frigate.camera_garage/still.jpg?h=480").toString())
        assertEquals("https://x.y/z", HubHttp.absolute(snap.serverUrl, "https://x.y/z").toString())
    }

    // -- sign-in replies -------------------------------------------------------------------------

    @Test
    fun loginReplies() {
        assertEquals(LoginResult.Success("t", null), LoginResult.from(200, LoginResponse(true, null, "t"), null))
        assertEquals(LoginResult.NeedsCode(null), LoginResult.from(401, null, """{"needs":"totp"}"""))
        assertEquals(LoginResult.NeedsCode("That code didn't work"), LoginResult.from(401, null, """{"needs":"totp","error":"That code didn't work"}"""))
        assertEquals(LoginResult.Failed("Wrong username or password"), LoginResult.from(401, null, """{"ok":false,"error":"Wrong username or password"}"""))
        val policy = "This connection needs a device certificate (mTLS) to sign in from outside the house."
        assertEquals(LoginResult.Failed(policy), LoginResult.from(403, null, """{"ok":false,"error":"$policy"}"""))
        assertEquals(LoginResult.Failed("Too many attempts. Try again in 3 min."), LoginResult.from(429, null, """{"error":"Too many attempts. Try again in 3 min.","retryAfter":170}"""))
        assertTrue(LoginResult.from(502, null, "<html>") is LoginResult.Failed)
    }

    // -- push ------------------------------------------------------------------------------------

    @Test
    fun pushPayloadFromTheHubsFields() {
        val buttons = Fixtures.categories.first().buttons
        val data = mapOf(
            "kind" to "automation", "title" to "Nightly lock check", "body" to "Laundry is unlocked",
            "run_id" to "r_1", "automation_id" to "auto_1", "time_sensitive" to "true", "thread" to "auto_1",
            "buttons" to NewmanJson.encodeToString(kotlinx.serialization.builtins.ListSerializer(ca.nimaste.newman.core.model.PushButton.serializer()), buttons),
            "action_url" to "https://home.garito.ca/api/public/actions", "action_subject" to "r_1",
            "action_exp" to "1790000000", "action_token" to "tok", "collapse_id" to "auto_1",
            "snapshot_url" to "https://home.garito.ca/api/public/snapshots/s1?device=d&exp=1&token=t",
        )
        val p = PushPayload.parse(data)
        assertEquals(buttons, p.buttons)
        assertTrue(p.timeSensitive)
        assertTrue(p.canAct)
        assertEquals("auto_1".hashCode(), p.notificationId)
        val req = p.actionRequest(buttons.first(), "device-1")!!
        assertEquals(buttons.first().id, req.button)
        assertEquals("r_1", req.subject)
        assertEquals("device-1", req.deviceId)
        assertEquals("1790000000", req.exp)
        // A category id without inline buttons falls back to GET /api/push/categories.
        val cat = Fixtures.categories.last()
        val q = PushPayload.parse(mapOf("title" to "x", "category" to cat.id), Fixtures.categories)
        assertEquals(cat.buttons, q.buttons)
        assertFalse(q.canAct)
        assertNull(q.actionRequest(cat.buttons.first(), "d"))
        assertTrue(PushPayload.parse(mapOf("kind" to "test", "title" to "Newman test", "buttons" to "garbage")).buttons.isEmpty())
    }
}
