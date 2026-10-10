package ca.nimaste.newman.core.net

import ca.nimaste.newman.core.model.NewmanJson
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException
import java.net.URI
import java.net.URLDecoder
import java.security.cert.CertPathValidatorException
import java.security.cert.CertificateException
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLException
import javax.net.ssl.SSLPeerUnverifiedException

/** GET /api/hello: which hub answered (no sign-in needed). */
@Serializable
data class HubHello(
    val app: String? = null,
    val id: String? = null,
    val name: String? = null,
    val version: String? = null,
    val outsideUrl: String? = null,
    val localUrls: List<String> = emptyList(),
) {
    val isNewman: Boolean get() = app == "newman" && !id.isNullOrBlank()
    val displayName: String get() = name?.takeIf { it.isNotBlank() } ?: "Newman"

    companion object {
        fun parse(body: String?): HubHello? = try {
            body?.let { NewmanJson.decodeFromString(serializer(), it) }
        } catch (e: Exception) {
            null
        }
    }
}

/**
 * The hub as this app keeps it: its id and name, the home-network address and the outside one.
 * Which one is used at a given moment is [HubChoice]'s call.
 */
data class SavedHub(val id: String?, val name: String?, val localUrl: String?, val outsideUrl: String?)

/** newman://connect?v=1&id=..&name=..&local=..&outside=.. (the QR code under Household → Accounts). */
data class ConnectLink(val id: String, val name: String, val localUrl: String?, val outsideUrl: String?) {
    companion object {
        /** The link, or null when it isn't a usable Newman connect link. */
        fun parse(text: String?): ConnectLink? {
            val uri = try {
                URI(text?.trim() ?: return null)
            } catch (e: Exception) {
                return null
            }
            if (!uri.scheme.equals("newman", ignoreCase = true) || !uri.host.equals("connect", ignoreCase = true)) return null
            val q = query(uri.rawQuery ?: return null)
            val v = q["v"]?.toIntOrNull() ?: return null
            if (v < 1) return null
            val id = q["id"]?.trim()?.takeIf { it.isNotEmpty() && it.length <= 64 } ?: return null
            val local = q["local"]?.takeIf { it.isNotBlank() }?.let { ServerAddress.normalize(it) }
            val outside = q["outside"]?.takeIf { it.isNotBlank() }?.let { ServerAddress.normalize(it) }
            if (local == null && outside == null) return null
            val name = q["name"]?.trim()?.takeIf { it.isNotEmpty() }?.take(48) ?: "Newman"
            return ConnectLink(id, name, local, outside)
        }

        private fun query(raw: String): Map<String, String> = raw.split('&').mapNotNull { part ->
            val i = part.indexOf('=')
            if (i <= 0) null
            else try {
                URLDecoder.decode(part.substring(0, i), "UTF-8") to URLDecoder.decode(part.substring(i + 1), "UTF-8")
            } catch (e: Exception) {
                null
            }
        }.toMap()
    }
}

/** Why an address didn't work, in words a person can act on. */
enum class ConnectProblem(val message: String) {
    UNREACHABLE("Can't reach that address. Check it, and that this device is on the right network."),
    NEEDS_CERTIFICATE("This address needs a client certificate"),
    NEEDS_CLOUDFLARE("This address needs a Cloudflare Access token"),
    UNTRUSTED("That address's security certificate isn't trusted"),
    NOT_NEWMAN("That isn't a Newman hub"),
    OTHER_HUB("That's a different Newman hub"),
    HUB_DOWN("The address answered, but Newman behind it didn't"),
    PLAIN_HTTP("Use https:// for an address outside your home network"),
}

sealed interface ConnectResult {
    data class Ok(val url: String, val hello: HubHello) : ConnectResult
    data class Failed(val problem: ConnectProblem) : ConnectResult {
        val message: String get() = problem.message
    }
}

/** Testing an address with GET <address>api/hello, and making sense of what comes back. */
object ConnectCheck {

    /** What one HTTP answer means: done, a problem, or a redirect to follow. */
    sealed interface Answer {
        data class Done(val result: ConnectResult) : Answer
        data class Follow(val location: String) : Answer
    }

    fun isCloudflareAccess(location: String?): Boolean {
        val host = location?.toHttpUrlOrNull()?.host ?: return false
        return host == "cloudflareaccess.com" || host.endsWith(".cloudflareaccess.com")
    }

    fun classify(url: String, code: Int, location: String?, body: String?, expectId: String? = null): Answer {
        val text = body.orEmpty().take(4000).lowercase()
        return when {
            code in 300..399 && isCloudflareAccess(location) -> fail(ConnectProblem.NEEDS_CLOUDFLARE)
            code in 300..399 && !location.isNullOrBlank() -> Answer.Follow(location)
            (code == 401 || code == 403) && ("cloudflare access" in text || "cloudflareaccess" in text) -> fail(ConnectProblem.NEEDS_CLOUDFLARE)
            code == 495 || code == 496 -> fail(ConnectProblem.NEEDS_CERTIFICATE)
            code in 400..403 && "certificate" in text -> fail(ConnectProblem.NEEDS_CERTIFICATE)
            code in 200..299 -> {
                val hello = HubHello.parse(body)
                when {
                    hello == null || !hello.isNewman -> fail(ConnectProblem.NOT_NEWMAN)
                    expectId != null && hello.id != expectId -> fail(ConnectProblem.OTHER_HUB)
                    else -> Answer.Done(ConnectResult.Ok(url, hello))
                }
            }
            code in 502..504 -> fail(ConnectProblem.HUB_DOWN)
            else -> fail(ConnectProblem.NOT_NEWMAN)
        }
    }

    /** A connection that failed before any answer. */
    fun classify(error: Throwable): ConnectProblem {
        if (error is NoServerException) return ConnectProblem.PLAIN_HTTP
        val chain = generateSequence(error) { it.cause }.take(8).toList()
        val words = chain.joinToString(" ") { (it.message ?: "") }.lowercase()
        val certRequired = listOf("certificate_required", "certificate required", "bad_certificate", "bad certificate",
            "handshake_failure", "alert_handshake").any { it in words }
        return when {
            certRequired -> ConnectProblem.NEEDS_CERTIFICATE
            chain.any { it is SSLPeerUnverifiedException || it is CertPathValidatorException || it is CertificateException } ||
                "trust anchor" in words -> ConnectProblem.UNTRUSTED
            chain.any { it is SSLException } -> ConnectProblem.NEEDS_CERTIFICATE
            else -> ConnectProblem.UNREACHABLE
        }
    }

    /**
     * GET <address>api/hello through [client] (the app's mTLS client), with the Cloudflare token
     * when given. [expectId]: only that hub counts.
     */
    suspend fun test(
        client: OkHttpClient,
        address: String,
        cloudflare: Pair<String, String>? = null,
        expectId: String? = null,
        timeoutMs: Long = 10_000,
    ): ConnectResult = withContext(Dispatchers.IO) {
        val base = ServerAddress.normalize(address) ?: return@withContext ConnectResult.Failed(ConnectProblem.UNREACHABLE)
        val c = client.newBuilder().followRedirects(false).followSslRedirects(false)
            .callTimeout(timeoutMs, TimeUnit.MILLISECONDS)
            .connectTimeout(minOf(timeoutMs, 10_000), TimeUnit.MILLISECONDS)
            .build()
        var url = base.toHttpUrlOrNull()!!.newBuilder().addPathSegments("api/hello").build().toString()
        repeat(4) {
            val req = Request.Builder().url(url).header(HubInterceptor.NO_AUTH, "1").apply {
                cloudflare?.let { (id, secret) ->
                    header("CF-Access-Client-Id", id)
                    header("CF-Access-Client-Secret", secret)
                }
            }.build()
            val answer = try {
                c.newCall(req).execute().use { res ->
                    classify(base, res.code, res.header("Location")?.let { res.request.url.resolve(it)?.toString() ?: it },
                        if (res.code in 200..499) res.body?.string() else null, expectId)
                }
            } catch (e: IOException) {
                return@withContext ConnectResult.Failed(classify(e))
            }
            when (answer) {
                is Answer.Done -> return@withContext answer.result
                is Answer.Follow -> url = answer.location
            }
        }
        ConnectResult.Failed(ConnectProblem.NOT_NEWMAN)
    }

    /**
     * A hub known by two addresses (a scanned link, or a saved hub): the home one first (quickly), then
     * the outside one. The outside one's problem is the one reported when both fail.
     */
    suspend fun testHub(
        client: OkHttpClient,
        local: String?,
        outside: String?,
        cloudflare: Pair<String, String>? = null,
        expectId: String? = null,
    ): ConnectResult {
        var last: ConnectResult = ConnectResult.Failed(ConnectProblem.UNREACHABLE)
        if (local != null) {
            last = test(client, local, null, expectId, timeoutMs = if (outside != null) 4_000 else 10_000)
            if (last is ConnectResult.Ok) return last
        }
        if (outside != null) last = test(client, outside, cloudflare, expectId)
        return last
    }

    private fun fail(p: ConnectProblem) = Answer.Done(ConnectResult.Failed(p))
}

/** Changing the server while signed in (Me → Server): the same hub at new addresses, or another hub. */
object HubPlan {
    /**
     * True when [newHub] reached at [newAddress] is the hub this app already uses ([oldHub], [oldAddress]):
     * its addresses are just updated. False means another hub: sign out of the old one first. Hub ids
     * decide when both are known; otherwise a shared address does. Nothing saved yet counts as the same.
     */
    fun isSameHub(oldHub: SavedHub?, oldAddress: String?, newHub: SavedHub?, newAddress: String): Boolean {
        val oldId = oldHub?.id?.takeIf { it.isNotBlank() }
        val newId = newHub?.id?.takeIf { it.isNotBlank() }
        if (oldId != null && newId != null) return oldId == newId
        val olds = listOfNotNull(oldAddress, oldHub?.localUrl, oldHub?.outsideUrl).map(::norm).toSet()
        if (olds.isEmpty()) return true
        val news = listOfNotNull(newAddress, newHub?.localUrl, newHub?.outsideUrl).map(::norm).toSet()
        return olds.any { it in news }
    }

    /** What to keep for the same hub: what it says now, the saved addresses where it says nothing. */
    fun merged(old: SavedHub, new: SavedHub): SavedHub = SavedHub(
        id = new.id ?: old.id,
        name = new.name ?: old.name,
        localUrl = new.localUrl ?: old.localUrl,
        outsideUrl = new.outsideUrl ?: old.outsideUrl,
    )

    private fun norm(url: String): String = ServerAddress.normalize(url) ?: url.trim()
}

/** Which of a hub's addresses to use right now. */
object HubChoice {
    /** What to save after connecting through [connectedVia]: its addresses as the hub told them. */
    fun saved(connectedVia: String, hello: HubHello, link: ConnectLink? = null): SavedHub {
        val via = ServerAddress.normalize(connectedVia) ?: connectedVia
        val viaHome = via.toHttpUrlOrNull()?.let { ServerAddress.isPrivateHost(it.host) } == true
        val outside = (if (viaHome) null else via) ?: link?.outsideUrl ?: hello.outsideUrl?.let { ServerAddress.normalize(it) }
        val local = (if (viaHome) via else null) ?: link?.localUrl ?: hello.localUrls.firstNotNullOfOrNull { ServerAddress.normalize(it) }
        return SavedHub(hello.id, hello.displayName, local, outside)
    }
}
