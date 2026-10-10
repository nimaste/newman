package ca.nimaste.newman.core.net

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Handler
import android.os.Looper
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.net.Inet6Address
import java.net.InetAddress

/**
 * Newman hubs announcing themselves on the home network (`_newman._tcp`, see the hub's connect.py),
 * found with Android's NsdManager (no Google Play services). Services are resolved one at a time
 * (older Android allows only one resolve in flight).
 */
class HubDiscovery(context: Context) {

    data class Found(val serviceName: String, val name: String, val id: String?, val localUrl: String, val outsideUrl: String?)

    private val nsd = context.applicationContext.getSystemService(NsdManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    private val _found = MutableStateFlow<List<Found>>(emptyList())
    val found: StateFlow<List<Found>> = _found.asStateFlow()

    private val _searching = MutableStateFlow(false)
    val searching: StateFlow<Boolean> = _searching.asStateFlow()

    private var listener: NsdManager.DiscoveryListener? = null
    private val queue = ArrayDeque<NsdServiceInfo>()
    private var resolving = false

    fun start() {
        if (listener != null || nsd == null) return
        val l = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) {
                main.post { _searching.value = true }
            }
            override fun onDiscoveryStopped(serviceType: String) {
                main.post { _searching.value = false }
            }
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                main.post {
                    _searching.value = false
                    listener = null
                }
            }
            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) = Unit
            override fun onServiceFound(info: NsdServiceInfo) {
                main.post { enqueue(info) }
            }
            override fun onServiceLost(info: NsdServiceInfo) {
                main.post { _found.value = _found.value.filterNot { it.serviceName == info.serviceName } }
            }
        }
        listener = l
        try {
            nsd.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, l)
        } catch (e: Exception) {
            listener = null
        }
    }

    fun stop() {
        val l = listener ?: return
        listener = null
        queue.clear()
        try {
            nsd?.stopServiceDiscovery(l)
        } catch (e: Exception) {
            // already stopped
        }
        _searching.value = false
    }

    private fun enqueue(info: NsdServiceInfo) {
        if (listener == null) return
        queue.addLast(info)
        resolveNext()
    }

    @Suppress("DEPRECATION")
    private fun resolveNext() {
        if (resolving || listener == null) return
        val next = queue.removeFirstOrNull() ?: return
        resolving = true
        try {
            nsd?.resolveService(next, object : NsdManager.ResolveListener {
                override fun onResolveFailed(info: NsdServiceInfo, errorCode: Int) {
                    main.post { done() }
                }
                override fun onServiceResolved(info: NsdServiceInfo) {
                    main.post {
                    val attrs = info.attributes.mapValues { (_, v) -> v?.toString(Charsets.UTF_8) }
                    fromService(info.serviceName, info.host, info.port, attrs)?.let { f ->
                        _found.value = (_found.value.filterNot { it.serviceName == f.serviceName || (f.id != null && it.id == f.id) } + f)
                            .sortedBy { it.name.lowercase() }
                    }
                    done()
                    }
                }
            })
        } catch (e: Exception) {
            done()
        }
    }

    private fun done() {
        resolving = false
        resolveNext()
    }

    companion object {
        const val SERVICE_TYPE = "_newman._tcp"

        /** One resolved announcement as a hub to offer (null when it isn't usable). */
        fun fromService(serviceName: String, host: InetAddress?, port: Int, txt: Map<String, String?>): Found? {
            host ?: return null
            if (port <= 0) return null
            val addr = host.hostAddress?.substringBefore('%') ?: return null
            val hostPart = if (host is Inet6Address) "[$addr]" else addr
            return fromParts(serviceName, hostPart, port, txt)
        }

        fun fromParts(serviceName: String, host: String, port: Int, txt: Map<String, String?>): Found? {
            val path = txt["path"]?.takeIf { it.startsWith("/") } ?: "/"
            val local = ServerAddress.normalize("http://$host:$port$path") ?: return null
            val name = txt["name"]?.takeIf { it.isNotBlank() } ?: serviceName.ifBlank { "Newman" }
            val outside = txt["ext"]?.takeIf { it.isNotBlank() }?.let { ServerAddress.normalize(it) }
            return Found(serviceName, name, txt["id"]?.takeIf { it.isNotBlank() }, local, outside)
        }
    }
}
