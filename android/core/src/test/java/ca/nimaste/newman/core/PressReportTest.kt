package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.ActionRequest
import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.net.HubInterceptor
import ca.nimaste.newman.core.net.SavedHub
import ca.nimaste.newman.core.push.ActionRoutes
import ca.nimaste.newman.core.push.PressAttempt
import ca.nimaste.newman.core.push.PressError
import ca.nimaste.newman.core.push.PressMetrics
import ca.nimaste.newman.core.push.PressNetwork
import ca.nimaste.newman.core.push.PressReport
import ca.nimaste.newman.core.push.PressRun
import ca.nimaste.newman.core.push.PressTiming
import ca.nimaste.newman.core.settings.ConnectionStore
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
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
import org.junit.Test

class PressReportTest {

    // The hub's schema (server/newman/press_reports.py clean(), API.md POST /api/public/press-report).
    private val reportKeys = setOf("id", "button", "subject", "deviceId", "exp", "token", "ok", "outcome", "summary",
        "appVersion", "launchState", "sinceLaunchSeconds", "backgroundExpired", "actionURL", "at", "attempts")
    private val attemptKeys = setOf("at", "url", "appState", "backgroundLeft", "timeoutSeconds", "ms", "status", "error",
        "certChallenges", "certAvailable", "otherChallenges", "metrics")
    private val errorKeys = setOf("domain", "code", "description", "underlying", "streamCode", "streamDomain", "failingHost", "path")
    private val metricKeys = setOf("transactions", "fetchStartAt", "dnsStart", "dnsEnd", "connectStart", "connectEnd",
        "secureStart", "secureEnd", "requestStart", "requestEnd", "responseStart", "responseEnd", "remoteAddress", "remotePort",
        "localAddress", "networkProtocol", "reusedConnection", "proxyConnection", "cellular", "expensive", "constrained")

    @Test
    fun triesGetTheIphonesTimesWithinOneBudget() {
        assertEquals(20.0, PressTiming.attemptSeconds(0, 0.0)!!, 0.0)
        assertEquals(8.0, PressTiming.attemptSeconds(1, 5.0)!!, 0.0)
        assertEquals(6.5, PressTiming.attemptSeconds(1, 20.5)!!, 1e-9) // what's left of the 27 s
        assertNull(PressTiming.attemptSeconds(2, 25.5)) // under 2 s left: not started
        assertEquals(7.0, PressTiming.attemptSeconds(0, 0.0, backgroundLeft = 10.0)!!, 0.0) // iOS's reserve
        // Three tries that each use all their time never pass the budget.
        var elapsed = 0.0
        for (i in 0 until 3) elapsed += PressTiming.attemptSeconds(i, elapsed) ?: 0.0
        assertTrue(elapsed <= PressTiming.BUDGET_SECONDS)
    }

    @Test
    fun reportJsonHasTheHubsFieldsOnly() {
        val attempt = PressAttempt(at = 1_790_000_000.5, url = "http://10.1.1.2:8093/api/public/actions", appState = "background",
            timeoutSeconds = 20.0, ms = 20004,
            error = PressError.of(java.net.SocketTimeoutException("timeout").apply { initCause(java.io.IOException("reset")) },
                "10.1.1.2", "wifi validated"),
            certAvailable = false,
            metrics = PressMetrics(transactions = 1, fetchStartAt = 1_790_000_000.5, connectStart = 1, remoteAddress = "10.1.1.2",
                remotePort = 8093, reusedConnection = false, cellular = false))
        val report = PressReport(id = "r1", button = "lock_laundry", subject = "run_1", deviceId = "dev", exp = "1790003600",
            token = "tok", ok = false, outcome = "failed: The Newman hub took too long to answer",
            summary = PressReport.summary(false, "failed", listOf(attempt), 3), appVersion = "Android 1.2.3 (10203)",
            launchState = "background +0.3s", sinceLaunchSeconds = 12.5, actionURL = "https://home.garito.ca/api/public/actions",
            at = 1_790_000_000.0, attempts = listOf(attempt))
        val o = NewmanJson.parseToJsonElement(report.toJson()).jsonObject
        assertEquals(reportKeys, o.keys)
        assertEquals("tok", o["token"]!!.jsonPrimitive.content)
        assertEquals("false", o["backgroundExpired"]!!.jsonPrimitive.content)
        val a = o["attempts"]!!.jsonArray.single().jsonObject
        assertTrue(a.keys.toString(), attemptKeys.containsAll(a.keys))
        assertFalse("nulls are left out", a.containsKey("status") || a.containsKey("backgroundLeft"))
        assertEquals("0", a["certChallenges"]!!.jsonPrimitive.content)
        val e = a["error"]!!.jsonObject
        assertTrue(errorKeys.containsAll(e.keys))
        assertEquals("java.net.SocketTimeoutException", e["domain"]!!.jsonPrimitive.content)
        assertEquals("java.io.IOException: reset", e["underlying"]!!.jsonPrimitive.content)
        assertEquals("wifi validated", e["path"]!!.jsonPrimitive.content)
        assertTrue(metricKeys.containsAll(a["metrics"]!!.jsonObject.keys))
        assertEquals(report, PressReport.fromJson(report.toJson()))
        // A press that worked: the summary only.
        val ok = NewmanJson.parseToJsonElement(report.copy(ok = true, attempts = emptyList()).toJson()).jsonObject
        assertEquals(JsonArray(emptyList()), ok["attempts"])
        assertTrue(PressReport.line(attempt, 0, 3).startsWith("try 1/3 http://10.1.1.2:8093/api/public/actions (background, 20 s allowed): 20004 ms, java.net.SocketTimeoutException 0 timeout"))
    }

    @Test
    fun urlsAreRedactedAndReportsGoToEveryAddress() {
        assertEquals("https://home.garito.ca/api/public/actions", PressReport.redact("https://home.garito.ca:443/api/public/actions?token=x#y"))
        assertEquals("http://10.1.1.2:8093/api/public/actions", PressReport.redact("http://u:p@10.1.1.2:8093/api/public/actions?x=1"))
        assertEquals("?", PressReport.redact("not a url"))
        val snap = ConnectionStore.Snapshot("http://10.1.1.2:8093/", null, null, null, null,
            SavedHub("hub1", "Home", "http://10.1.1.2:8093/", "https://home.garito.ca/"))
        assertEquals(listOf("http://10.1.1.2:8093/api/public/press-report", "https://home.garito.ca/api/public/press-report"),
            PressReport.urls(snap, "https://home.garito.ca/api/public/actions"))
        assertEquals(listOf("https://other.example/api/public/press-report"),
            PressReport.urls(ConnectionStore.Snapshot(null, null, null, null, null), "https://other.example/api/public/actions"))
        val q = (1..12).fold(emptyList<String>()) { acc, i -> PressReport.queued(acc, "r$i") }
        assertEquals((3..12).map { "r$it" }, q)
        assertEquals(listOf("r1", "r2"), PressReport.queued(listOf("r2", "r1"), "r2"))
    }

    @Test
    fun eachTryIsAFreshConnectionAndTheHubsOwnAnswerIsFinal() = runBlocking {
        val gone = MockWebServer().apply { start() }
        val goneUrl = gone.url("/api/public/actions").toString()
        gone.shutdown() // connection refused
        val proxy = MockWebServer().apply { start() }
        val hub = MockWebServer().apply { start() }
        try {
            val snap = ConnectionStore.Snapshot(hub.url("/").toString(), "cf-id", "cf-secret", "session", "Chris",
                SavedHub("hub1", "Home", hub.url("/").toString(), proxy.url("/").toString()))
            val base = OkHttpClient.Builder().addInterceptor(HubInterceptor({ snap }, "Newman-Test")).build()
            val run = PressRun(base, appState = { "background" },
                network = { PressNetwork("wifi validated", cellular = false, metered = false, dataSaver = false) })
            val req = ActionRequest("lock_laundry", "run_1", "dev", "1", "tok")

            // Refused, then a Cloudflare 502, then the hub itself.
            proxy.enqueue(MockResponse().setResponseCode(502))
            hub.enqueue(MockResponse().setBody("""{"ok":true,"accepted":true}"""))
            val attempts = mutableListOf<PressAttempt>()
            val r = run.press(listOf(goneUrl, proxy.url("/api/public/actions").toString(), hub.url("/api/public/actions").toString()), req, attempts)
            assertTrue(r.ok)
            assertEquals("pressed", r.outcome)
            assertEquals(3, attempts.size)
            assertEquals("java.net.ConnectException", attempts[0].error!!.domain)
            assertEquals(20.0, attempts[0].timeoutSeconds, 0.0)
            assertEquals(502, attempts[1].status)
            assertEquals(8.0, attempts[1].timeoutSeconds, 0.0)
            assertEquals(200, attempts[2].status)
            val m = attempts[2].metrics!!
            assertEquals(false, m.reusedConnection)
            assertNotNull(m.connectStart)
            assertNotNull(m.responseEnd)
            assertEquals(hub.port, m.remotePort)
            assertEquals("http/1.1", m.networkProtocol)
            assertNull(attempts[2].certAvailable) // plain http: no TLS
            val sent = hub.takeRequest()
            assertNull("signed by the push, not the session", sent.getHeader("Authorization"))
            assertEquals("cf-id", sent.getHeader("CF-Access-Client-Id"))
            assertEquals("tok", NewmanJson.parseToJsonElement(sent.body.readUtf8()).jsonObject["token"]!!.jsonPrimitive.content)

            // A second press: a new connection again (never one from the first press, nor the app's pool).
            hub.enqueue(MockResponse().setResponseCode(409).setBody("""{"error":"Already locked"}"""))
            val again = mutableListOf<PressAttempt>()
            val r2 = run.press(ActionRoutes.urls(snap, null), req, again)
            assertFalse(r2.ok)
            assertEquals("refused 409: Already locked", r2.outcome)
            assertEquals("the hub's answer is final", 1, again.size)
            assertEquals(0, hub.takeRequest().sequenceNumber)
            assertEquals(false, again[0].metrics!!.reusedConnection)
        } finally {
            proxy.shutdown()
            hub.shutdown()
        }
    }

    @Test
    fun noAddressMeansNoTries() = runBlocking {
        val r = PressRun(OkHttpClient(), appState = { "foreground" }).press(emptyList(), ActionRequest("b", "s", "d", "1", "t"), mutableListOf())
        assertFalse(r.ok)
        assertEquals(0, r.tried)
    }
}
