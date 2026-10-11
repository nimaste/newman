package ca.nimaste.newman.core.push

import ca.nimaste.newman.core.api.ApiErrors
import ca.nimaste.newman.core.model.ActionRequest
import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.net.HubHttp
import ca.nimaste.newman.core.net.HubInterceptor
import ca.nimaste.newman.core.settings.ConnectionStore
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.Serializable
import okhttp3.Call
import okhttp3.Callback
import okhttp3.Connection
import okhttp3.ConnectionPool
import okhttp3.Dispatcher
import okhttp3.EventListener
import okhttp3.Handshake
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.io.InterruptedIOException
import java.net.InetSocketAddress
import java.net.Proxy
import java.util.Locale
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * How long each try at a notification button may take - the same numbers as the iPhone app
 * (ios/Shared/Core/Protocol.swift PushPayload.attemptSeconds): the first try (the address in use)
 * up to 20 s, since right after the phone wakes for a button the network may take a while; each
 * other address up to 8 s; all of them within 27 s.
 */
object PressTiming {
    const val FIRST_ATTEMPT_SECONDS = 20.0
    const val ATTEMPT_SECONDS = 8.0
    const val BUDGET_SECONDS = 27.0
    /** Kept back from a background time limit (iOS only; Android has none to report) for the notice and the report. */
    const val RESERVE_SECONDS = 3.0
    /** A try shorter than this isn't started. */
    const val MIN_ATTEMPT_SECONDS = 2.0

    /** How long try number [index] (0 = first) may take after [elapsed] seconds of tries; null = no time left. */
    fun attemptSeconds(index: Int, elapsed: Double, backgroundLeft: Double? = null): Double? {
        var budget = BUDGET_SECONDS - elapsed
        if (backgroundLeft != null) budget = minOf(budget, backgroundLeft - RESERVE_SECONDS)
        val t = minOf(if (index == 0) FIRST_ATTEMPT_SECONDS else ATTEMPT_SECONDS, budget)
        return if (t >= MIN_ATTEMPT_SECONDS) t else null
    }
}

// -- the press report (POST /api/public/press-report, server/newman/press_reports.py) ------------
// Field names are the iPhone app's (ios/Shared/Core/Protocol.swift PressReport), so the hub logs
// both phones the same way. Nulls are left out (NewmanJson: explicitNulls = false).

/** One try at sending a notification button, as the phone saw it. */
@Serializable
data class PressAttempt(
    /** When the try started (seconds since 1970, the phone's clock). */
    val at: Double,
    /** scheme://host[:port]/path only - never a query or a token. */
    val url: String,
    /** How the app's process was running when the try started (foreground, background, fg service…). */
    val appState: String,
    /** iOS background time left; always null on Android (no such limit to read). */
    val backgroundLeft: Double? = null,
    val timeoutSeconds: Double,
    val ms: Int,
    val status: Int? = null,
    val error: PressError? = null,
    /** 1 when the TLS handshake sent this phone's client certificate (so the hub asked for it), else 0. */
    val certChallenges: Int = 0,
    /** true = certificate sent; false = TLS without one and none stored; null = unknown / plain http. */
    val certAvailable: Boolean? = null,
    val otherChallenges: List<String>? = null,
    val metrics: PressMetrics? = null,
)

/** A try's failure. Android: [domain] is the exception's class, [code] the errno underneath (0 if none). */
@Serializable
data class PressError(
    val domain: String,
    val code: Int,
    val description: String,
    /** The causes underneath, "class: message", outermost first. */
    val underlying: String? = null,
    val streamCode: Int? = null,
    val streamDomain: Int? = null,
    val failingHost: String? = null,
    /** The phone's network when it failed, e.g. "wifi validated" or "cellular blocked". */
    val path: String? = null,
) {
    val line: String get() {
        val extra = listOfNotNull(underlying?.let { "underlying $it" }, failingHost?.let { "host $it" }, path?.let { "path $it" })
        return "$domain $code $description" + if (extra.isEmpty()) "" else extra.joinToString("; ", " [", "]")
    }

    companion object {
        fun of(t: Throwable, host: String?, path: String?): PressError {
            val causes = generateSequence(t.cause) { c -> c.cause?.takeIf { it !== c } }.take(4).toList()
            return PressError(
                domain = t.javaClass.name,
                code = (listOf(t) + causes).firstNotNullOfOrNull { errno(it) } ?: 0,
                description = (t.message ?: t.javaClass.simpleName).take(300),
                underlying = causes.joinToString(" <- ") { "${it.javaClass.name}: ${it.message}" }.ifEmpty { null }?.take(300),
                failingHost = host, path = path,
            )
        }

        /** android.system.ErrnoException.errno (ECONNREFUSED 111, ENETUNREACH 101…), read by name so this runs on the JVM. */
        private fun errno(t: Throwable): Int? = if (t.javaClass.name != "android.system.ErrnoException") null
        else runCatching { t.javaClass.getField("errno").getInt(t) }.getOrNull()
    }
}

/** A try's OkHttp events: each step in ms after the call started (null = it never happened). */
@Serializable
data class PressMetrics(
    /** Connections tried or requests sent (more than 1 = OkHttp tried another route or retried). */
    val transactions: Int,
    val fetchStartAt: Double? = null,
    val dnsStart: Int? = null,
    val dnsEnd: Int? = null,
    val connectStart: Int? = null,
    val connectEnd: Int? = null,
    val secureStart: Int? = null,
    val secureEnd: Int? = null,
    val requestStart: Int? = null,
    val requestEnd: Int? = null,
    val responseStart: Int? = null,
    val responseEnd: Int? = null,
    val remoteAddress: String? = null,
    val remotePort: Int? = null,
    val localAddress: String? = null,
    val networkProtocol: String? = null,
    val reusedConnection: Boolean? = null,
    val proxyConnection: Boolean? = null,
    val cellular: Boolean? = null,
    /** Metered network. */
    val expensive: Boolean? = null,
    /** Data Saver on. */
    val constrained: Boolean? = null,
) {
    val line: String get() {
        fun span(a: Int?, b: Int?) = if (a == null) "-" else "$a-${b ?: "?"}"
        val s = "dns ${span(dnsStart, dnsEnd)} connect ${span(connectStart, connectEnd)} tls ${span(secureStart, secureEnd)}" +
            " request ${span(requestStart, requestEnd)} response ${span(responseStart, responseEnd)}"
        val who = buildList {
            remoteAddress?.let { add(if (remotePort != null) "$it:$remotePort" else it) }
            localAddress?.let { add("from $it") }
            networkProtocol?.let { add(it) }
            reusedConnection?.let { add(if (it) "reused" else "new") }
            if (proxyConnection == true) add("proxy")
            if (cellular == true) add("cellular")
            if (constrained == true) add("low data")
            if (transactions != 1) add("$transactions transactions")
        }
        return if (who.isEmpty()) s else s + " | " + who.joinToString(" ")
    }
}

/** The phone's network at a try (ConnectivityManager on the phone). */
data class PressNetwork(val path: String, val cellular: Boolean?, val metered: Boolean?, val dataSaver: Boolean?)

/** What the phone sends the hub after a notification button: every try when it failed, a summary when it worked. */
@Serializable
data class PressReport(
    val id: String,
    val button: String,
    val subject: String,
    val deviceId: String,
    val exp: String,
    val token: String,
    val ok: Boolean,
    /** "pressed" / "refused 409: …" / "failed: The Newman hub took too long to answer". */
    val outcome: String,
    val summary: String,
    val appVersion: String,
    /** Android: the process state when the button was tapped, and how long the work waited ("background +0.3s"). */
    val launchState: String,
    /** How long the app's process had been running. */
    val sinceLaunchSeconds: Double? = null,
    /** Android: the system stopped the work before the press was done. */
    val backgroundExpired: Boolean = false,
    /** The push's own action_url (no query). */
    val actionURL: String,
    val at: Double,
    val attempts: List<PressAttempt>,
) {
    fun toJson(): String = NewmanJson.encodeToString(serializer(), this)

    companion object {
        const val PATH = "api/public/press-report"
        /** Each try at sending a report (all the hub's addresses at once). */
        const val SEND_SECONDS = 4L
        const val PENDING_LIMIT = 10

        fun fromJson(json: String): PressReport? = runCatching { NewmanJson.decodeFromString(serializer(), json) }.getOrNull()

        /** scheme://host[:port]/path - never the query, a fragment or credentials; the port only when it isn't the scheme's own. */
        fun redact(url: HttpUrl): String {
            val port = if (url.port == HttpUrl.defaultPort(url.scheme)) "" else ":${url.port}"
            val host = if (url.host.contains(':')) "[${url.host}]" else url.host
            return "${url.scheme}://$host$port${url.encodedPath}"
        }

        fun redact(url: String?): String = url?.trim()?.toHttpUrlOrNull()?.let { redact(it) } ?: "?"

        /** Where to send a report: each of the hub's addresses, then the push's action_url's hub. */
        fun urls(snapshot: ConnectionStore.Snapshot, actionUrl: String?): List<String> {
            val out = LinkedHashSet<String>()
            listOfNotNull(snapshot.serverUrl, snapshot.hub.localUrl, snapshot.hub.outsideUrl).forEach { base ->
                HubHttp.absolute(base, PATH)?.let { out.add(it.toString()) }
            }
            actionUrl?.trim()?.toHttpUrlOrNull()?.resolve("press-report")?.let { out.add(it.toString()) }
            return out.toList()
        }

        /** One line per try, for the phone's own log. */
        fun line(a: PressAttempt, index: Int, of: Int): String {
            val s = StringBuilder("try ${index + 1}/$of ${a.url} (${a.appState}")
            a.backgroundLeft?.let { s.append(String.format(Locale.US, ", %.1f s background left", it)) }
            s.append(String.format(Locale.US, ", %.0f s allowed): ", a.timeoutSeconds)).append("${a.ms} ms, ")
            s.append(a.status?.let { "HTTP $it" } ?: a.error?.line ?: "no answer")
            s.append("; cert challenges ${a.certChallenges}")
            a.certAvailable?.let { s.append(if (it) " (certificate sent)" else " (no certificate stored)") }
            a.otherChallenges?.takeIf { it.isNotEmpty() }?.let { s.append("; other challenges ${it.joinToString(",")}") }
            s.append("; ").append(a.metrics?.line ?: "no metrics")
            return s.toString()
        }

        /** "pressed at http://10.1.1.2:8093/api/public/actions on try 1 of 2 in 85 ms (background)" */
        fun summary(ok: Boolean, outcome: String, attempts: List<PressAttempt>, tried: Int): String {
            val last = attempts.lastOrNull() ?: return "$outcome (no address to try)"
            if (ok) return "$outcome at ${last.url} on try ${attempts.size} of $tried in ${last.ms} ms (${last.appState})"
            return "$outcome after ${attempts.size} of $tried tries, ${attempts.sumOf { it.ms }} ms in all (${last.appState})"
        }

        /** The queue of reports not delivered yet: newest last, at most [limit]. */
        fun queued(existing: List<String>, adding: String, limit: Int = PENDING_LIMIT): List<String> =
            (existing.filter { it != adding } + adding).takeLast(limit)
    }
}

// -- one try ---------------------------------------------------------------------------------------

/** Records a call's OkHttp events (one listener per call). */
internal class PressEvents : EventListener() {
    private var t0 = 0L
    @Volatile var fetchStartAt: Double? = null
    private val marks = HashMap<String, Int>()
    private var connects = 0
    private var requests = 0
    private var connectedHere = false
    var remote: InetSocketAddress? = null
    var local: String? = null
    var protocol: String? = null
    var proxied: Boolean? = null
    var reused: Boolean? = null
    var tls = false
    var certSent = false

    private fun now() = ((System.nanoTime() - t0) / 1_000_000).toInt()
    @Synchronized private fun first(k: String) { if (k !in marks) marks[k] = now() }
    @Synchronized private fun last(k: String) { marks[k] = now() }
    @Synchronized fun mark(k: String): Int? = marks[k]

    override fun callStart(call: Call) {
        t0 = System.nanoTime()
        fetchStartAt = System.currentTimeMillis() / 1000.0
    }
    override fun dnsStart(call: Call, domainName: String) = first("dnsStart")
    override fun dnsEnd(call: Call, domainName: String, inetAddressList: List<java.net.InetAddress>) = last("dnsEnd")
    override fun connectStart(call: Call, inetSocketAddress: InetSocketAddress, proxy: Proxy) {
        first("connectStart")
        connects++
        connectedHere = true
        remote = inetSocketAddress
        proxied = proxy.type() != Proxy.Type.DIRECT
    }
    override fun secureConnectStart(call: Call) { tls = true; first("secureStart") }
    override fun secureConnectEnd(call: Call, handshake: Handshake?) {
        last("secureEnd")
        if (handshake?.localCertificates?.isNotEmpty() == true) certSent = true
    }
    override fun connectEnd(call: Call, inetSocketAddress: InetSocketAddress, proxy: Proxy, protocol: Protocol?) {
        last("connectEnd")
        protocol?.let { this.protocol = it.toString() }
    }
    override fun connectionAcquired(call: Call, connection: Connection) {
        reused = !connectedHere
        protocol = connection.protocol().toString()
        runCatching { connection.socket() }.getOrNull()?.let { s ->
            (s.remoteSocketAddress as? InetSocketAddress)?.let { remote = it }
            local = s.localAddress?.hostAddress
        }
        if (connection.handshake()?.localCertificates?.isNotEmpty() == true) certSent = true
    }
    override fun requestHeadersStart(call: Call) { requests++; first("requestStart") }
    override fun requestHeadersEnd(call: Call, request: Request) = last("requestEnd")
    override fun requestBodyEnd(call: Call, byteCount: Long) = last("requestEnd")
    override fun responseHeadersStart(call: Call) = first("responseStart")
    override fun responseHeadersEnd(call: Call, response: Response) = last("responseEnd")
    override fun responseBodyEnd(call: Call, byteCount: Long) = last("responseEnd")

    fun metrics(network: PressNetwork?): PressMetrics = PressMetrics(
        transactions = maxOf(1, connects, requests), fetchStartAt = fetchStartAt,
        dnsStart = mark("dnsStart"), dnsEnd = mark("dnsEnd"), connectStart = mark("connectStart"), connectEnd = mark("connectEnd"),
        secureStart = mark("secureStart"), secureEnd = mark("secureEnd"), requestStart = mark("requestStart"),
        requestEnd = mark("requestEnd"), responseStart = mark("responseStart"), responseEnd = mark("responseEnd"),
        remoteAddress = remote?.address?.hostAddress ?: remote?.hostString, remotePort = remote?.port, localAddress = local,
        networkProtocol = protocol, reusedConnection = reused, proxyConnection = proxied,
        cellular = network?.cellular, expensive = network?.metered, constrained = network?.dataSaver,
    )
}

/**
 * One try at sending a notification button, on its own connection: a client made from the app's
 * hub client with newBuilder() (same client certificate, trust and Cloudflare headers) but its own
 * empty connection pool and dispatcher, so a press never waits behind - or reuses a dead one of -
 * the live socket's, video's and pictures' connections. Records the try for the press report.
 */
object PressProbe {
    class Outcome(val status: Int?, val body: String?, val error: Throwable?, val attempt: PressAttempt)

    private val JSON = "application/json; charset=utf-8".toMediaType()

    fun client(base: OkHttpClient, timeoutSeconds: Double, events: EventListener? = null): OkHttpClient {
        val ms = (timeoutSeconds * 1000).toLong()
        return base.newBuilder()
            .connectionPool(ConnectionPool(0, 1, TimeUnit.SECONDS))
            .dispatcher(Dispatcher())
            .apply { if (events != null) eventListener(events) }
            .callTimeout(ms, TimeUnit.MILLISECONDS)
            .connectTimeout(ms, TimeUnit.MILLISECONDS)
            .readTimeout(ms, TimeUnit.MILLISECONDS)
            .writeTimeout(ms, TimeUnit.MILLISECONDS)
            .build()
    }

    /**
     * POST [json] to [url] within [timeoutSeconds]. If the coroutine is cancelled (the system
     * stopped the work), the try is still handed to [onCancelled] before the cancellation goes on.
     */
    suspend fun send(
        base: OkHttpClient, url: String, json: String, timeoutSeconds: Double, appState: String,
        network: () -> PressNetwork? = { null }, certStored: Boolean = false,
        onCancelled: (PressAttempt) -> Unit = {},
    ): Outcome {
        val events = PressEvents()
        val started = System.currentTimeMillis()
        val t0 = System.nanoTime()
        val parsed = url.trim().toHttpUrlOrNull()
        val blank = PressAttempt(at = started / 1000.0, url = parsed?.let { PressReport.redact(it) } ?: "?", appState = appState,
            timeoutSeconds = timeoutSeconds, ms = 0)
        fun finish(status: Int?, error: Throwable?): PressAttempt {
            val net = network()
            val tlsWithoutCert = events.tls && !events.certSent && !certStored
            return blank.copy(
                ms = ((System.nanoTime() - t0) / 1_000_000).toInt(), status = status,
                error = error?.let { PressError.of(it, parsed?.host, net?.path) },
                certChallenges = if (events.certSent) 1 else 0,
                certAvailable = if (events.certSent) true else if (tlsWithoutCert) false else null,
                metrics = if (events.fetchStartAt != null) events.metrics(net) else null,
            )
        }
        if (parsed == null) {
            val e = IllegalArgumentException("Not a web address")
            return Outcome(null, null, e, finish(null, e))
        }
        val request = Request.Builder().url(parsed).post(json.toRequestBody(JSON))
            .header(HubInterceptor.NO_AUTH, "1") // signed by the push's own token, not the session
            .build()
        return try {
            val (status, body) = execute(client(base, timeoutSeconds, events), request)
            Outcome(status, body, null, finish(status, null))
        } catch (e: CancellationException) {
            onCancelled(finish(null, e))
            throw e
        } catch (e: Exception) {
            Outcome(null, null, e, finish(null, e))
        }
    }

    private suspend fun execute(client: OkHttpClient, request: Request): Pair<Int, String?> =
        suspendCancellableCoroutine { cont ->
            val call = client.newCall(request)
            cont.invokeOnCancellation { call.cancel() }
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    cont.resumeWithException(e)
                }

                override fun onResponse(call: Call, response: Response) {
                    val r = try {
                        response.use { it.code to runCatching { it.body?.string()?.take(4000) }.getOrNull() }
                    } catch (e: Exception) {
                        cont.resumeWithException(e)
                        return
                    }
                    cont.resume(r)
                }
            })
        }
}

/**
 * A notification button, sent to each address in turn ([ActionRoutes.urls]) with [PressTiming]'s
 * time per try, each on a fresh connection ([PressProbe]); the hub's first own answer is final.
 */
class PressRun(
    private val base: OkHttpClient,
    private val appState: () -> String,
    private val network: () -> PressNetwork? = { null },
    private val certStored: () -> Boolean = { false },
    private val log: (String) -> Unit = {},
) {
    data class Result(val ok: Boolean, val outcome: String, val problem: String?, val tried: Int)

    /** Every try is added to [attempts] as it ends (also when the work is stopped mid-try). */
    suspend fun press(urls: List<String>, request: ActionRequest, attempts: MutableList<PressAttempt>): Result {
        if (urls.isEmpty()) return Result(false, "failed: no address", "Choose your Newman address first", 0)
        val json = NewmanJson.encodeToString(ActionRequest.serializer(), request)
        val started = System.nanoTime()
        var problem: String? = null
        var outcome = "failed"
        for ((i, url) in urls.withIndex()) {
            val seconds = PressTiming.attemptSeconds(i, (System.nanoTime() - started) / 1e9)
            if (seconds == null) {
                log("no time left for try ${i + 1} of ${urls.size}")
                break
            }
            val res = PressProbe.send(base, url, json, seconds, appState(), network, certStored(), onCancelled = { attempts.add(it) })
            attempts.add(res.attempt)
            log(PressReport.line(res.attempt, i, urls.size))
            val status = res.status
            if (status != null) {
                if (status in 200..299) return Result(true, "pressed", null, urls.size)
                problem = ApiErrors.errorText(res.body) ?: if (status == 401) "That notification has expired" else ApiErrors.httpFallback(status)
                outcome = "refused $status: $problem"
                if (!ActionRoutes.tryNext(status)) break
            } else {
                val e = res.error
                problem = if (e is InterruptedIOException) "The Newman hub took too long to answer" else e?.let { ApiErrors.message(it) } ?: "Can't reach the Newman hub"
                outcome = "failed: $problem"
            }
        }
        if (attempts.isEmpty()) problem = problem ?: "There was no time left to reach the Newman hub"
        return Result(false, outcome, problem, urls.size)
    }
}
