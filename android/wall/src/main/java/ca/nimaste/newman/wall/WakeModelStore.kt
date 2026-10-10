package ca.nimaste.newman.wall

import android.content.Context
import android.webkit.CookieManager
import ca.nimaste.newman.core.net.HubConnection
import ca.nimaste.newman.core.net.HubHttp
import ca.nimaste.newman.core.net.HubInterceptor
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.core.voice.WakeConfig
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.IOException

/**
 * The wake-word model, from the hub (GET /api/voice/wakeword, then its three .onnx files), kept in
 * the app's files by version: a retrained model on the hub is picked up without a new app. Uses
 * the wall page's session (its cookies), the client certificate and Cloudflare headers like every
 * other request (see BatteryReporter). Blocking - call it off the main thread.
 */
class WakeModelStore(private val context: Context, private val connection: ConnectionStore) {

    private val client: OkHttpClient by lazy {
        HubHttp.client(HubConnection { connection.snapshot }, "NewmanWall/${BuildConfig.VERSION_NAME} (Android)")
    }

    private fun get(path: String): okhttp3.Response {
        val url = HubHttp.absolute(connection.serverUrl, path) ?: throw IOException("No hub address")
        val cookies = try {
            CookieManager.getInstance().getCookie(url.toString())
        } catch (e: Exception) {
            null
        }
        val req = Request.Builder().url(url).header(HubInterceptor.NO_AUTH, "1")
        if (!cookies.isNullOrBlank()) req.header("Cookie", cookies)
        return client.newCall(req.build()).execute()
    }

    /** The current model's folder (downloading it first if needed) and its config; null when the hub has none. */
    @Throws(IOException::class)
    fun fetch(): Pair<File, WakeConfig>? {
        val cfg = get(PATH).use { r ->
            if (r.code == 404) return null
            if (!r.isSuccessful) throw IOException("HTTP ${r.code}")
            WakeConfig.parse(r.body?.string() ?: "")
        }
        if (!cfg.complete) return null
        val root = File(context.filesDir, "voice")
        val dir = File(root, cfg.version.filter { it.isLetterOrDigit() }.take(32))
        if (!WakeConfig.FILES.all { File(dir, "$it.onnx").length() > 0 }) {
            val tmp = File(root, "download").apply { deleteRecursively(); mkdirs() }
            for (name in WakeConfig.FILES) {
                get(cfg.files.getValue(name)).use { r ->
                    if (!r.isSuccessful) throw IOException("HTTP ${r.code} for $name")
                    File(tmp, "$name.onnx").outputStream().use { out -> r.body!!.byteStream().copyTo(out) }
                }
            }
            dir.deleteRecursively()
            if (!tmp.renameTo(dir)) throw IOException("Couldn't keep the model")
        }
        // Only the current version is kept.
        root.listFiles()?.filter { it.isDirectory && it.name != dir.name }?.forEach { it.deleteRecursively() }
        return dir to cfg
    }

    companion object {
        const val PATH = "api/voice/wakeword"
    }
}
