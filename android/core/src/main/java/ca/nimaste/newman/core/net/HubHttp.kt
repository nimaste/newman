package ca.nimaste.newman.core.net

import ca.nimaste.newman.core.mtls.MTLSSslConfig
import ca.nimaste.newman.core.settings.ConnectionStore
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Thrown (as an IOException, so OkHttp/Retrofit report it normally) when there's no usable server. */
class NoServerException(message: String) : IOException(message)

/** What the interceptor needs to know, read fresh on every request. */
fun interface HubConnection {
    fun snapshot(): ConnectionStore.Snapshot
}

/**
 * Points every request at the hub the person chose and adds its credentials:
 *  - Retrofit's placeholder host [PLACEHOLDER_HOST] is rewritten to the server (keeping any path prefix);
 *  - `Authorization: Bearer <token>` goes only to the hub itself (any of its saved addresses);
 *  - Cloudflare Access service-token headers go to the hub's hosts (also WebSockets);
 *  - plain http is refused for anything that isn't a home-network address.
 */
class HubInterceptor(private val connection: HubConnection, private val userAgent: String) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val snap = connection.snapshot()
        val original = chain.request()
        val server = snap.serverUrl?.toHttpUrlOrNull()
        var url = original.url
        if (url.host == PLACEHOLDER_HOST) {
            server ?: throw NoServerException("Choose your Newman address first")
            url = rebase(server, url)
        }
        if (!url.isHttps && !ServerAddress.isPrivateHost(url.host)) {
            throw NoServerException("Newman only uses plain http on your home network")
        }
        val b = original.newBuilder().url(url).header("User-Agent", userAgent)
        // The hub at any of its saved addresses (the one in use, or the other one - a notification
        // button tries both): the same credentials go to each.
        val hubUrls = listOfNotNull(server, snap.hub.localUrl?.toHttpUrlOrNull(), snap.hub.outsideUrl?.toHttpUrlOrNull())
        val toHub = hubUrls.any { url.host.equals(it.host, ignoreCase = true) && url.port == it.port }
        if (toHub) {
            val token = snap.token
            if (!token.isNullOrEmpty() && original.header("Authorization") == null && original.header(NO_AUTH) == null) {
                b.header("Authorization", "Bearer $token")
            }
            if (snap.hasCloudflare) {
                b.header("CF-Access-Client-Id", snap.cfClientId!!)
                b.header("CF-Access-Client-Secret", snap.cfClientSecret!!)
            }
        }
        b.removeHeader(NO_AUTH)
        return chain.proceed(b.build())
    }

    companion object {
        const val PLACEHOLDER_HOST = "newman.invalid"
        const val PLACEHOLDER_BASE = "http://$PLACEHOLDER_HOST/"
        /** Marker header: don't attach the session token (sign-in, public endpoints). */
        const val NO_AUTH = "X-Newman-No-Auth"

        /** server "https://h/prefix/" + "http://newman.invalid/api/meta?x" -> "https://h/prefix/api/meta?x". */
        fun rebase(server: HttpUrl, relative: HttpUrl): HttpUrl {
            val b = server.newBuilder()
            relative.encodedPathSegments.forEach { if (it.isNotEmpty()) b.addEncodedPathSegment(it) }
            if (relative.encodedPath.endsWith("/") && relative.encodedPathSegments.size > 1) b.addPathSegment("")
            b.encodedQuery(relative.encodedQuery)
            return b.build()
        }
    }
}

object HubHttp {
    /**
     * The base client: mTLS on every connection, hub credentials via [HubInterceptor]. Derive
     * others from it with newBuilder() so they share the connection pool and the certificate.
     */
    fun client(connection: HubConnection, userAgent: String): OkHttpClient = OkHttpClient.Builder()
        .sslSocketFactory(MTLSSslConfig.socketFactory, MTLSSslConfig.trustManager)
        .addInterceptor(HubInterceptor(connection, userAgent))
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .pingInterval(30, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    /** Absolute URL of a hub-relative path ("api/entities/x/still.jpg"), or null without a server. */
    fun absolute(serverUrl: String?, path: String): HttpUrl? {
        if (path.startsWith("http://") || path.startsWith("https://")) return path.toHttpUrlOrNull()
        val server = serverUrl?.toHttpUrlOrNull() ?: return null
        val rel = (PLACEHOLDER + path.trimStart('/')).toHttpUrlOrNull() ?: return null
        return HubInterceptor.rebase(server, rel)
    }

    /** The live channel: <server>/api/ws?token=… (the hub also takes the Bearer header). */
    fun liveRequest(snapshot: ConnectionStore.Snapshot): Request? {
        val token = snapshot.token ?: return null
        val url = absolute(snapshot.serverUrl, "api/ws")?.newBuilder()?.addQueryParameter("token", token)?.build() ?: return null
        return Request.Builder().url(url).build()
    }

    private const val PLACEHOLDER = HubInterceptor.PLACEHOLDER_BASE
}
