package ca.nimaste.newman.push

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import ca.nimaste.newman.MainActivity
import ca.nimaste.newman.R
import ca.nimaste.newman.core.api.ApiErrors
import ca.nimaste.newman.core.api.NewmanApi
import ca.nimaste.newman.core.api.hubCall
import ca.nimaste.newman.core.hub.HubRepository
import ca.nimaste.newman.core.live.LiveMessage
import ca.nimaste.newman.core.livestatus.LiveButton
import ca.nimaste.newman.core.livestatus.LiveNotifications
import ca.nimaste.newman.core.livestatus.LivePlan
import ca.nimaste.newman.core.livestatus.LiveSpec
import ca.nimaste.newman.core.livestatus.LiveUpdate
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.core.widgets.WidgetAction
import ca.nimaste.newman.ui.home.OPEN_DEVICE
import ca.nimaste.newman.widgets.WidgetSync
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Live statuses as ongoing notifications (channel "Live status", silent): one per live status the hub
 * shows this person (server/newman/live.py), updated in place, with its devices' buttons. Kept current
 * from the app's live connection ({"type": "live"} - the whole list), GET /api/live/current when the app
 * opens or reconnects, and FCM data messages when the hub has FCM. Plain buttons run straight from the
 * shade; sensitive ones (open, unlock, disarm) open the app to confirm with the fingerprint / screen lock.
 * What each one looks like is worked out in core (LiveNotifications), which tests cover.
 */
@Singleton
class LiveStatusNotifier @Inject constructor(
    @ApplicationContext private val context: Context,
    private val api: NewmanApi,
    private val store: ConnectionStore,
    private val hub: HubRepository,
    val scope: CoroutineScope,
) {
    private val lock = Mutex()
    private var job: Job? = null

    fun start() {
        if (job != null) return
        job = scope.launch {
            hub.liveMessages.collect { msg ->
                when (msg) {
                    is LiveMessage.LiveChanged -> apply(msg.update)
                    is LiveMessage.Reconnected -> sync()
                    else -> Unit
                }
            }
        }
    }

    /** The hub's list now (the app opened, came back to the front, or reconnected). */
    fun sync() {
        if (store.token.isNullOrEmpty()) return
        scope.launch {
            try {
                apply(LiveUpdate(hubCall { api.liveCurrent(store.deviceId) }.activities))
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                Log.w(TAG, "Live statuses: ${ApiErrors.message(e)}") // an older hub, or unreachable: leave them
            }
        }
    }

    /** An FCM {"type": "live", …} message. */
    fun applyPush(data: Map<String, String>) {
        val (event, entry) = LiveUpdate.fromPush(data) ?: return
        scope.launch { lock.withLock { show(LiveNotifications.planOne(event, entry)) } }
    }

    suspend fun apply(update: LiveUpdate) = lock.withLock {
        if (store.token.isNullOrEmpty()) return@withLock
        show(LiveNotifications.plan(shownTags(), update))
    }

    /** Signed out: nothing of the house stays in the shade. */
    fun clear() {
        val nm = NotificationManagerCompat.from(context)
        shownTags().forEach { nm.cancel(it, NOTIFICATION_ID) }
    }

    private fun shownTags(): Set<String> = try {
        context.getSystemService(NotificationManager::class.java)?.activeNotifications
            ?.filter { it.id == NOTIFICATION_ID && it.tag != null }?.map { it.tag }?.toSet() ?: emptySet()
    } catch (e: Exception) {
        emptySet()
    }

    private fun show(plan: LivePlan) {
        val nm = NotificationManagerCompat.from(context)
        plan.cancel.forEach { nm.cancel(it, NOTIFICATION_ID) }
        if (!Notifications.canPost(context)) return
        plan.post.forEach { s ->
            try {
                nm.notify(s.tag, NOTIFICATION_ID, build(s))
            } catch (e: SecurityException) {
                // notifications turned off meanwhile
            }
        }
    }

    private fun build(s: LiveSpec): android.app.Notification {
        val b = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(ContextCompat.getColor(context, R.color.notification_accent))
            .setContentTitle(s.title)
            .setContentText(s.text)
            .setOngoing(s.ongoing)
            .setAutoCancel(!s.ongoing)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(openIntent(s))
        if (s.sinceMillis != null) b.setWhen(s.sinceMillis!!).setShowWhen(true).setUsesChronometer(true)
        else b.setShowWhen(false)
        if (s.lines.isNotEmpty()) {
            b.setStyle(NotificationCompat.InboxStyle().also { st -> s.lines.forEach { st.addLine(it) } }.setSummaryText(s.text))
        }
        s.timeoutMillis?.let { b.setTimeoutAfter(it) }
        s.buttons.forEachIndexed { i, btn -> b.addAction(0, btn.label, buttonIntent(s, btn, i)) }
        return b.build()
    }

    private fun code(s: LiveSpec, i: Int) = (s.tag.hashCode() and 0xffffff) * 8 + i

    private fun openIntent(s: LiveSpec): PendingIntent {
        val i = Intent(context, MainActivity::class.java).setAction(Notifications.ACTION_OPEN)
            .setData(Uri.parse("newman-live://open/" + Uri.encode(s.tag)))
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        s.openDevice?.let { i.putExtra(Notifications.EXTRA_OPEN, OPEN_DEVICE + it) }
        return PendingIntent.getActivity(context, code(s, 7), i, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    private fun buttonIntent(s: LiveSpec, btn: LiveButton, index: Int): PendingIntent {
        val data = Uri.parse("newman-live://press/" + Uri.encode(s.tag) + "/" + index)
        val extras = Intent().putExtra(EXTRA_DEVICE, btn.deviceId).putExtra(EXTRA_COMMAND, btn.command)
            .putExtra(EXTRA_LABEL, btn.label).putExtra(EXTRA_SENSITIVE, btn.sensitive).putExtra(EXTRA_TITLE, s.title)
        return if (btn.sensitive) {
            // Open / unlock / disarm: the app confirms it's the phone's owner first.
            val i = Intent(context, MainActivity::class.java).setAction(ACTION_LIVE_PRESS).setData(data).putExtras(extras)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            PendingIntent.getActivity(context, code(s, index), i, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        } else {
            val i = Intent(context, LiveActionReceiver::class.java).setAction(ACTION_LIVE_PRESS).setData(data).putExtras(extras)
            PendingIntent.getBroadcast(context, code(s, index), i, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        }
    }

    /** A live status button (from its intent's extras): runs the device's command through the hub. */
    data class Press(val deviceId: String, val command: String, val label: String, val sensitive: Boolean, val title: String) {
        suspend fun run(context: Context): Boolean =
            WidgetSync.from(context).run(deviceId, WidgetAction(command, label, sensitive))

        companion object {
            fun from(i: Intent?): Press? {
                i ?: return null
                return Press(i.getStringExtra(EXTRA_DEVICE) ?: return null, i.getStringExtra(EXTRA_COMMAND) ?: return null,
                    i.getStringExtra(EXTRA_LABEL) ?: "", i.getBooleanExtra(EXTRA_SENSITIVE, false), i.getStringExtra(EXTRA_TITLE) ?: "")
            }
        }
    }

    @EntryPoint
    @InstallIn(SingletonComponent::class)
    interface Access {
        fun liveStatusNotifier(): LiveStatusNotifier
    }

    companion object {
        private const val TAG = "NewmanLive"
        const val CHANNEL = "live"
        const val NOTIFICATION_ID = 7300
        const val ACTION_LIVE_PRESS = "ca.nimaste.newman.LIVE_PRESS"
        private const val EXTRA_DEVICE = "newman.live.device"
        private const val EXTRA_COMMAND = "newman.live.command"
        private const val EXTRA_LABEL = "newman.live.label"
        private const val EXTRA_SENSITIVE = "newman.live.sensitive"
        private const val EXTRA_TITLE = "newman.live.title"

        fun createChannel(context: Context) {
            val nm = context.getSystemService(NotificationManager::class.java) ?: return
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Live status", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Devices shown while they matter (a garage left open), updated quietly"
                setSound(null, null)
                enableVibration(false)
            })
        }

        fun from(context: Context): LiveStatusNotifier =
            EntryPointAccessors.fromApplication(context.applicationContext, Access::class.java).liveStatusNotifier()
    }
}

/** A plain (not sensitive) live status button: run straight from the shade. */
class LiveActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val press = LiveStatusNotifier.Press.from(intent) ?: return
        val pending = goAsync()
        val live = LiveStatusNotifier.from(context)
        live.scope.launch {
            try {
                press.run(context.applicationContext)
            } finally {
                pending.finish()
            }
        }
    }
}
