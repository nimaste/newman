package ca.nimaste.newman.push

import android.app.ActivityManager
import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.NetworkInfo
import android.os.Build
import android.os.Process
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.ForegroundInfo
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import ca.nimaste.newman.BuildConfig
import ca.nimaste.newman.R
import ca.nimaste.newman.core.model.ActionRequest
import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.mtls.MTLSStore
import ca.nimaste.newman.core.push.ActionRoutes
import ca.nimaste.newman.core.push.PressAttempt
import ca.nimaste.newman.core.push.PressNetwork
import ca.nimaste.newman.core.push.PressProbe
import ca.nimaste.newman.core.push.PressReport
import ca.nimaste.newman.core.push.PressRun
import ca.nimaste.newman.core.settings.ConnectionStore
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.util.Locale
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.coroutines.resume

/**
 * Sends a tapped notification button to the hub (POST /api/public/actions, signed by the push):
 * to the address in use, then the hub's other address, the push's action_url last
 * ([ActionRoutes]); each try on its own fresh connection ([PressProbe]) - the first up to 20 s, the
 * others up to 8 s, 27 s in all ([ca.nimaste.newman.core.push.PressTiming]), as on the iPhone. The
 * notification shows the outcome; every press is then reported to the hub ([PressReports]).
 */
@Singleton
class ButtonPress @Inject constructor(
    @ApplicationContext private val context: Context,
    private val store: ConnectionStore,
    private val http: OkHttpClient,
    private val reports: PressReports,
) {
    /**
     * [launchState]: how the app was running when the button was tapped (and how long the work
     * waited); [workNote]: how the work ran, for the report's summary; [stopped]: the system
     * stopped the work. Returns the outcome (for the in-app toast).
     */
    suspend fun run(p: Notifications.Press, launchState: String, workNote: String? = null, stopped: () -> Boolean = { false }): Result<Unit> {
        val request = ActionRequest(p.buttonId, p.subject, store.deviceId, p.exp, p.token)
        val urls = ActionRoutes.urls(store.snapshot, p.url)
        val startedAt = System.currentTimeMillis()
        val attempts = mutableListOf<PressAttempt>()
        val runner = PressRun(http, appState = { PressEnvironment.processState(context) },
            network = { PressEnvironment.network(context) }, certStored = { MTLSStore.isConfigured },
            log = { Log.i(TAG, "Button ${p.buttonId}: $it") })
        var result: PressRun.Result? = null
        try {
            result = runner.press(urls, request, attempts)
        } finally {
            withContext(NonCancellable) {
                val r = result ?: PressRun.Result(false, "failed: stopped by Android", "Android stopped it - try again", urls.size)
                if (r.ok) {
                    if (p.buttonId == "dismiss") NotificationManagerCompat.from(context).cancel(p.notificationId)
                    else Notifications.showOutcome(context, p.notificationId, p.title, "Done: ${p.label}", ok = true)
                } else {
                    Notifications.showOutcome(context, p.notificationId, p.title,
                        "Couldn't ${p.label.lowercase()}: ${r.problem ?: "try again"}", ok = false)
                }
                val summary = PressReport.summary(r.ok, r.outcome, attempts, r.tried) + (workNote?.let { "; $it" } ?: "")
                val report = PressReport(
                    id = UUID.randomUUID().toString(), button = p.buttonId, subject = p.subject, deviceId = request.deviceId,
                    exp = p.exp, token = p.token, ok = r.ok, outcome = r.outcome, summary = summary,
                    appVersion = PressEnvironment.appVersion, launchState = launchState.take(20),
                    sinceLaunchSeconds = PressEnvironment.uptimeSeconds(), backgroundExpired = result == null || stopped(),
                    actionURL = PressReport.redact(p.url), at = startedAt / 1000.0, attempts = if (r.ok) emptyList() else attempts.toList(),
                )
                Log.i(TAG, "press ${report.summary}")
                reports.send(report, post = result != null)
            }
        }
        val r = result!!
        return if (r.ok) Result.success(Unit) else Result.failure(IllegalStateException(r.problem ?: "That didn't work"))
    }

    @EntryPoint
    @InstallIn(SingletonComponent::class)
    interface Access {
        fun buttonPress(): ButtonPress
    }

    companion object {
        private const val TAG = "NewmanPress"

        fun from(context: Context): ButtonPress =
            EntryPointAccessors.fromApplication(context.applicationContext, Access::class.java).buttonPress()
    }
}

/**
 * Press reports (POST /api/public/press-report): queued first (encrypted, [ConnectionStore.pressReportsJson])
 * so one survives the process being stopped, then sent to all the hub's addresses at once (short
 * timeouts, fresh connections); kept and sent again when the app next comes to the foreground.
 */
@Singleton
class PressReports @Inject constructor(
    private val store: ConnectionStore,
    private val http: OkHttpClient,
) {
    private val lock = Any()
    private val resending = AtomicBoolean(false)
    private val listSerializer = ListSerializer(String.serializer())

    private fun pending(): List<String> = store.pressReportsJson?.let {
        runCatching { NewmanJson.decodeFromString(listSerializer, it) }.getOrNull()
    } ?: emptyList()

    private fun save(list: List<String>) {
        store.pressReportsJson = if (list.isEmpty()) null else NewmanJson.encodeToString(listSerializer, list)
    }

    suspend fun send(report: PressReport, post: Boolean = true) {
        val json = report.toJson()
        synchronized(lock) { save(PressReport.queued(pending(), json)) }
        if (post && post(json, report.actionURL)) drop(listOf(json))
    }

    /** Reports that couldn't be sent before (the app came to the foreground). */
    suspend fun resend() {
        if (!resending.compareAndSet(false, true)) return
        try {
            val sent = pending().filter { json -> post(json, PressReport.fromJson(json)?.actionURL) }
            drop(sent)
        } finally {
            resending.set(false)
        }
    }

    private fun drop(sent: List<String>) {
        if (sent.isEmpty()) return
        synchronized(lock) { save(pending().filter { it !in sent }) }
    }

    /** To every hub address at once; true when one took it (the hub keeps a report once, by its id). */
    private suspend fun post(json: String, actionUrl: String?): Boolean {
        val urls = PressReport.urls(store.snapshot, actionUrl?.takeIf { it != "?" })
        if (urls.isEmpty()) return false
        val client = PressProbe.client(http, PressReport.SEND_SECONDS.toDouble())
        return coroutineScope {
            urls.map { url -> async { postOne(client, url, json) } }.awaitAll().any { it }
        }
    }

    private suspend fun postOne(client: OkHttpClient, url: String, json: String): Boolean = try {
        // The session token goes too (HubInterceptor), as on the iPhone: the hub takes either.
        val request = Request.Builder().url(url).post(json.toRequestBody(JSON)).build()
        val status = suspendCancellableCoroutine<Int?> { cont ->
            val call = client.newCall(request)
            cont.invokeOnCancellation { call.cancel() }
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) = cont.resume(null)
                override fun onResponse(call: Call, response: Response) = cont.resume(response.use { it.code })
            })
        }
        if (status != null && status !in 200..299) Log.w(TAG, "press report to ${PressReport.redact(url)}: HTTP $status")
        status in 200..299
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        false
    }

    companion object {
        private const val TAG = "NewmanPress"
        private val JSON = "application/json; charset=utf-8".toMediaType()
    }
}

/**
 * A plain notification button, sent from expedited work (not from the BroadcastReceiver, which
 * Android may stop ~10 s after the tap - too soon for three tries). If Android has no expedited
 * quota left it runs as ordinary work. On Android 11 and older expedited work runs as a short
 * foreground service with a silent "Sending…" notice on the low "Live status" channel.
 */
class PressWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    @Volatile private var asForeground = false

    override suspend fun doWork(): Result {
        val p = pressFrom(inputData) ?: return Result.success()
        val tappedAt = inputData.getLong(KEY_TAPPED_AT, 0L)
        val waited = if (tappedAt > 0) (System.currentTimeMillis() - tappedAt).coerceAtLeast(0) / 1000.0 else null
        val tapState = inputData.getString(KEY_TAP_STATE) ?: "?"
        val launch = if (waited != null) "$tapState +${fmt(waited)}s" else tapState
        val how = if (asForeground) "as a foreground service" else if (Build.VERSION.SDK_INT >= 31) "expedited unless out of quota" else "as ordinary work"
        val note = "work started ${waited?.let { fmt(it) } ?: "?"} s after the tap, $how (tapped while $tapState)"
        ButtonPress.from(applicationContext).run(p, launch, note, stopped = { isStopped })
        return Result.success() // never retried: the outcome is on the notification and in the report
    }

    override suspend fun getForegroundInfo(): ForegroundInfo {
        asForeground = true
        val label = inputData.getString(KEY_LABEL).orEmpty()
        val n = NotificationCompat.Builder(applicationContext, LiveStatusNotifier.CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(ContextCompat.getColor(applicationContext, R.color.notification_accent))
            .setContentTitle(if (label.isNotEmpty()) "Sending “$label”…" else "Sending…")
            .setSilent(true)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
        return ForegroundInfo(FOREGROUND_ID, n)
    }

    companion object {
        private const val KEY_BUTTON = "button"
        private const val KEY_LABEL = "label"
        private const val KEY_URL = "url"
        private const val KEY_SUBJECT = "subject"
        private const val KEY_EXP = "exp"
        private const val KEY_TOKEN = "token"
        private const val KEY_NOTIFICATION = "notification"
        private const val KEY_TITLE = "title"
        private const val KEY_TAPPED_AT = "tappedAt"
        private const val KEY_TAP_STATE = "tapState"
        private const val FOREGROUND_ID = 0x4e4d5052 // the "Sending…" notice only (never an alert's id)

        private fun fmt(s: Double) = String.format(Locale.US, "%.1f", s)

        /** From the button's broadcast: the press as expedited work (the same button twice at once runs once). */
        fun enqueue(context: Context, p: Notifications.Press) {
            val data = Data.Builder()
                .putString(KEY_BUTTON, p.buttonId).putString(KEY_LABEL, p.label).putString(KEY_URL, p.url)
                .putString(KEY_SUBJECT, p.subject).putString(KEY_EXP, p.exp).putString(KEY_TOKEN, p.token)
                .putInt(KEY_NOTIFICATION, p.notificationId).putString(KEY_TITLE, p.title)
                .putLong(KEY_TAPPED_AT, System.currentTimeMillis())
                .putString(KEY_TAP_STATE, PressEnvironment.tapState(context))
                .build()
            val request = OneTimeWorkRequestBuilder<PressWorker>()
                .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
                .setInputData(data)
                .addTag("newman-press")
                .build()
            WorkManager.getInstance(context)
                .enqueueUniqueWork("newman-press-${p.notificationId}-${p.buttonId}", ExistingWorkPolicy.KEEP, request)
        }

        private fun pressFrom(d: Data): Notifications.Press? {
            return Notifications.Press(
                buttonId = d.getString(KEY_BUTTON) ?: return null, label = d.getString(KEY_LABEL).orEmpty(), sensitive = false,
                url = d.getString(KEY_URL) ?: return null, subject = d.getString(KEY_SUBJECT) ?: return null,
                exp = d.getString(KEY_EXP) ?: return null, token = d.getString(KEY_TOKEN) ?: return null,
                notificationId = d.getInt(KEY_NOTIFICATION, 0), title = d.getString(KEY_TITLE) ?: "Newman",
            )
        }
    }
}

/** How the app and the phone's network were doing, for press reports. */
object PressEnvironment {
    val appVersion: String = "Android ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})"

    /** Seconds the app's process has been running. */
    fun uptimeSeconds(): Double =
        Math.round((SystemClock.elapsedRealtime() - Process.getStartElapsedRealtime()) / 100.0) / 10.0

    /** The process's importance: foreground, fg service, visible, perceptible, service or background. */
    fun processState(context: Context): String {
        val info = ActivityManager.RunningAppProcessInfo()
        ActivityManager.getMyMemoryState(info)
        return when {
            info.importance <= ActivityManager.RunningAppProcessInfo.IMPORTANCE_FOREGROUND -> "foreground"
            info.importance <= ActivityManager.RunningAppProcessInfo.IMPORTANCE_FOREGROUND_SERVICE -> "fg service"
            info.importance <= ActivityManager.RunningAppProcessInfo.IMPORTANCE_VISIBLE -> "visible"
            info.importance <= ActivityManager.RunningAppProcessInfo.IMPORTANCE_PERCEPTIBLE -> "perceptible"
            info.importance <= ActivityManager.RunningAppProcessInfo.IMPORTANCE_SERVICE -> "service"
            else -> "background"
        }
    }

    /** At the tap: "cold" when Android started the app for this button, else [processState]. */
    fun tapState(context: Context): String = if (uptimeSeconds() < 5) "cold" else processState(context)

    /** "wifi validated", "cellular vpn", "wifi blocked" (no network for this app right now), "none". */
    @Suppress("DEPRECATION")
    fun network(context: Context): PressNetwork? = runCatching { networkNow(context) }.getOrNull()

    @Suppress("DEPRECATION")
    private fun networkNow(context: Context): PressNetwork? {
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return null
        val net = cm.activeNetwork ?: return PressNetwork("none", null, null, null)
        val caps = cm.getNetworkCapabilities(net)
        val parts = mutableListOf<String>()
        if (caps != null) {
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) parts.add("wifi")
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) parts.add("cellular")
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) parts.add("ethernet")
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) parts.add("vpn")
            parts.add(if (caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)) "validated" else "not validated")
        }
        if (cm.activeNetworkInfo?.detailedState == NetworkInfo.DetailedState.BLOCKED) parts.add("blocked")
        return PressNetwork(
            path = parts.joinToString(" ").ifEmpty { "unknown" },
            cellular = caps?.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR),
            metered = cm.isActiveNetworkMetered,
            dataSaver = cm.restrictBackgroundStatus == ConnectivityManager.RESTRICT_BACKGROUND_STATUS_ENABLED,
        )
    }
}
