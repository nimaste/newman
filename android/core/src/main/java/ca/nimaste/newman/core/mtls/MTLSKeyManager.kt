package ca.nimaste.newman.core.mtls

import java.net.Socket
import java.security.KeyStore
import java.security.Principal
import java.security.PrivateKey
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLEngine
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509ExtendedKeyManager
import javax.net.ssl.X509TrustManager

/**
 * Presents the stored client certificate during a TLS handshake when one is configured.
 * Re-reads the PKCS#12 from [MTLSStore] on every handshake rather than caching a KeyStore, so
 * a certificate imported (or removed) mid-session applies to the very next connection without
 * rebuilding any OkHttpClient. A no-op when nothing is stored, so it is wired into every client.
 */
class MTLSKeyManager(private val source: () -> Pair<PrivateKey, Array<X509Certificate>>? = { MTLSStore.keyAndChain() }) :
    X509ExtendedKeyManager() {

    private val alias = "newman-client"

    private fun chosenAlias(): String? = if (source() != null) alias else null

    override fun chooseClientAlias(keyTypes: Array<out String>?, issuers: Array<out Principal>?, socket: Socket?): String? =
        chosenAlias()

    override fun chooseEngineClientAlias(keyType: Array<out String>?, issuers: Array<out Principal>?, engine: SSLEngine?): String? =
        chosenAlias()

    override fun getCertificateChain(alias: String?): Array<X509Certificate>? = source()?.second

    override fun getPrivateKey(alias: String?): PrivateKey? = source()?.first

    // Server-side lookups: unused for a client-only key manager.
    override fun getClientAliases(keyType: String?, issuers: Array<out Principal>?): Array<String>? =
        if (source() != null) arrayOf(alias) else null
    override fun getServerAliases(keyType: String?, issuers: Array<out Principal>?): Array<String>? = null
    override fun chooseServerAlias(keyType: String?, issuers: Array<out Principal>?, socket: Socket?): String? = null
}

/**
 * The one SSL setup every OkHttpClient in the apps is built with (Retrofit, the live WebSocket,
 * Coil, notification pictures and button presses). The trust manager is the normal system store;
 * the key manager is what offers the client certificate.
 */
object MTLSSslConfig {
    val trustManager: X509TrustManager
    val socketFactory: SSLSocketFactory

    init {
        val tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
        tmf.init(null as KeyStore?)
        trustManager = tmf.trustManagers.filterIsInstance<X509TrustManager>().first()
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(arrayOf(MTLSKeyManager()), arrayOf(trustManager), null)
        socketFactory = ctx.socketFactory
    }
}
