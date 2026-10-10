package ca.nimaste.newman.core

import ca.nimaste.newman.core.net.ConnectCheck
import ca.nimaste.newman.core.net.ConnectLink
import ca.nimaste.newman.core.net.ConnectProblem
import ca.nimaste.newman.core.net.ConnectResult
import ca.nimaste.newman.core.net.HubChoice
import ca.nimaste.newman.core.net.HubDiscovery
import ca.nimaste.newman.core.net.HubHello
import ca.nimaste.newman.core.net.NoServerException
import ca.nimaste.newman.core.net.SavedHub
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.security.cert.CertPathValidatorException
import java.security.cert.CertificateException
import javax.net.ssl.SSLHandshakeException
import javax.net.ssl.SSLPeerUnverifiedException

/** "Connect to your Newman": the connect link, which address to use, and what went wrong. */
class ConnectTest {

    // -- the connect link ----------------------------------------------------------------------------

    @Test
    fun connectLinkIsParsed() {
        val l = ConnectLink.parse("newman://connect?v=1&id=abc123&name=Our%20home%20%26%20co" +
            "&local=http%3A%2F%2F10.1.1.2%3A8093%2F&outside=https%3A%2F%2Fhome.example.com%2F")!!
        assertEquals(ConnectLink("abc123", "Our home & co", "http://10.1.1.2:8093/", "https://home.example.com/"), l)
        val noOutside = ConnectLink.parse("newman://connect?v=1&id=x&name=N&local=http%3A%2F%2F192.168.1.5%3A8093%2F&outside=")!!
        assertNull(noOutside.outsideUrl)
        assertEquals("http://192.168.1.5:8093/", noOutside.localUrl)
        // A newer link version still works; unknown parts are ignored.
        assertEquals("x", ConnectLink.parse("NEWMAN://connect?v=2&id=x&outside=home.example.com&extra=1")!!.id)
        assertEquals("Newman", ConnectLink.parse("newman://connect?v=1&id=x&outside=home.example.com")!!.name)
    }

    @Test
    fun badConnectLinksAreRefused() {
        listOf(
            null, "", "https://home.example.com/", "newman://other?v=1&id=x&outside=home.example.com",
            "newman://connect?id=x&outside=home.example.com", // no version
            "newman://connect?v=1&outside=home.example.com", // no id
            "newman://connect?v=1&id=x&local=&outside=", // no address
            "newman://connect?v=1&id=x&outside=http%3A%2F%2Fhome.example.com%2F", // plain http outside the home
            "not a link at all %%%",
        ).forEach { assertNull(it, ConnectLink.parse(it)) }
    }

    // -- what is saved (which address: HubAddressTest) ------------------------------------------------

    @Test
    fun whatIsSavedAfterConnecting() {
        val hello = HubHello("newman", "abc", "Home", "1", "https://home.example.com/", listOf("http://10.1.1.2:8093/"))
        assertEquals(SavedHub("abc", "Home", "http://10.1.1.2:8093/", "https://home.example.com/"),
            HubChoice.saved("http://10.1.1.2:8093", hello))
        // Connected from outside through another name: that name is the outside address.
        assertEquals(SavedHub("abc", "Home", "http://10.1.1.2:8093/", "https://other.example.com/"),
            HubChoice.saved("https://other.example.com/", hello))
        // The link's addresses win over what the hub reports.
        val link = ConnectLink("abc", "Home", "http://192.168.1.9:8093/", "https://link.example.com/")
        assertEquals(SavedHub("abc", "Home", "http://192.168.1.9:8093/", "https://link.example.com/"),
            HubChoice.saved("https://link.example.com/", hello.copy(localUrls = emptyList()), link))
    }

    // -- what went wrong ----------------------------------------------------------------------------

    private fun answer(code: Int, location: String? = null, body: String? = null, expectId: String? = null) =
        (ConnectCheck.classify("https://h.example.com/", code, location, body, expectId) as ConnectCheck.Answer.Done).result

    private fun problem(code: Int, location: String? = null, body: String? = null, expectId: String? = null) =
        (answer(code, location, body, expectId) as ConnectResult.Failed).problem

    @Test
    fun answersAreClassified() {
        val ok = answer(200, body = """{"app":"newman","id":"abc","name":"Home","version":"1","outsideUrl":null,"localUrls":[]}""")
        assertEquals("Home", (ok as ConnectResult.Ok).hello.name)
        assertEquals(ConnectProblem.NOT_NEWMAN, problem(200, body = """{"status":"ok"}"""))
        assertEquals(ConnectProblem.NOT_NEWMAN, problem(200, body = "<html>Welcome to nginx</html>"))
        assertEquals(ConnectProblem.NOT_NEWMAN, problem(404, body = "Not found"))
        assertEquals(ConnectProblem.OTHER_HUB, problem(200, body = """{"app":"newman","id":"zzz"}""", expectId = "abc"))
        assertEquals(ConnectProblem.NEEDS_CLOUDFLARE, problem(302, location = "https://garito.cloudflareaccess.com/cdn-cgi/access/login/h"))
        assertEquals(ConnectProblem.NEEDS_CLOUDFLARE, problem(403, body = "<title>Forbidden · Cloudflare Access</title>"))
        assertEquals(ConnectProblem.NEEDS_CERTIFICATE, problem(400, body = "<center>No required SSL certificate was sent</center>"))
        assertEquals(ConnectProblem.NEEDS_CERTIFICATE, problem(496))
        assertEquals(ConnectProblem.HUB_DOWN, problem(502))
        val follow = ConnectCheck.classify("http://h.lan/", 301, "https://h.example.com/api/hello", null)
        assertEquals(ConnectCheck.Answer.Follow("https://h.example.com/api/hello"), follow)
    }

    @Test
    fun connectionFailuresAreClassified() {
        assertEquals(ConnectProblem.UNREACHABLE, ConnectCheck.classify(UnknownHostException("home.example.com")))
        assertEquals(ConnectProblem.UNREACHABLE, ConnectCheck.classify(ConnectException("Connection refused")))
        assertEquals(ConnectProblem.UNREACHABLE, ConnectCheck.classify(SocketTimeoutException("timeout")))
        assertEquals(ConnectProblem.NEEDS_CERTIFICATE,
            ConnectCheck.classify(SSLHandshakeException("Read error: ssl=0x7: SSLV3_ALERT_BAD_CERTIFICATE")))
        assertEquals(ConnectProblem.NEEDS_CERTIFICATE, ConnectCheck.classify(javax.net.ssl.SSLException("TLSV13_ALERT_CERTIFICATE_REQUIRED")))
        assertEquals(ConnectProblem.NEEDS_CERTIFICATE, ConnectCheck.classify(SSLHandshakeException("Handshake failed")))
        val untrusted = SSLHandshakeException("java.security.cert.CertPathValidatorException: Trust anchor for certification path not found.")
        untrusted.initCause(CertificateException(CertPathValidatorException("Trust anchor for certification path not found.")))
        assertEquals(ConnectProblem.UNTRUSTED, ConnectCheck.classify(untrusted))
        assertEquals(ConnectProblem.UNTRUSTED, ConnectCheck.classify(SSLPeerUnverifiedException("Hostname h.example.com not verified")))
        assertEquals(ConnectProblem.PLAIN_HTTP, ConnectCheck.classify(NoServerException("plain http")))
    }

    @Test
    fun testingAnAddressAgainstAHub() = runBlocking {
        val server = MockWebServer()
        server.enqueue(MockResponse().setBody("""{"app":"newman","id":"abc","name":"Home","localUrls":["http://10.1.1.2:8093/"]}"""))
        server.enqueue(MockResponse().setResponseCode(302).setHeader("Location", "https://x.cloudflareaccess.com/login"))
        server.enqueue(MockResponse().setResponseCode(301).setHeader("Location", "/newman/api/hello"))
        server.enqueue(MockResponse().setBody("""{"app":"newman","id":"abc","name":"Home"}"""))
        server.start()
        try {
            val base = server.url("/").toString()
            val client = OkHttpClient()
            val ok = ConnectCheck.test(client, base, "id" to "secret") as ConnectResult.Ok
            assertEquals("Home", ok.hello.name)
            val first = server.takeRequest()
            assertEquals("/api/hello", first.path)
            assertEquals("id", first.getHeader("CF-Access-Client-Id"))
            assertNull(first.getHeader("Authorization"))
            assertEquals(ConnectProblem.NEEDS_CLOUDFLARE, (ConnectCheck.test(client, base) as ConnectResult.Failed).problem)
            assertTrue(ConnectCheck.test(client, base, expectId = "abc") is ConnectResult.Ok) // after following the redirect
        } finally {
            server.shutdown()
        }
        val down = ConnectCheck.test(OkHttpClient(), "http://127.0.0.1:1/", timeoutMs = 2_000) as ConnectResult.Failed
        assertEquals(ConnectProblem.UNREACHABLE, down.problem)
    }

    // -- hubs found on the network -------------------------------------------------------------------

    @Test
    fun announcedHubsBecomeChoices() {
        val f = HubDiscovery.fromParts("The Garitos", "10.1.1.2", 8093,
            mapOf("id" to "abc", "name" to "The Garitos", "v" to "1", "ext" to "https://home.example.com/", "path" to "/"))!!
        assertEquals(HubDiscovery.Found("The Garitos", "The Garitos", "abc", "http://10.1.1.2:8093/", "https://home.example.com/"), f)
        val bare = HubDiscovery.fromParts("Newman (2)", "192.168.0.4", 8093, emptyMap())!!
        assertEquals("Newman (2)", bare.name)
        assertNull(bare.outsideUrl)
        assertNull(bare.id)
    }
}
