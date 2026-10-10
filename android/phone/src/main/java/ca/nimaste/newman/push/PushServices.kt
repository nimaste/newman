package ca.nimaste.newman.push

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationManagerCompat
import ca.nimaste.newman.core.model.ActionRequest
import ca.nimaste.newman.core.people.LocatePush
import ca.nimaste.newman.core.push.PushPayload
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.data.Account
import ca.nimaste.newman.location.LocationSharing
import ca.nimaste.newman.widgets.WidgetSync
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import javax.inject.Inject

/** FCM entry point (only active when the build has google-services.json). */
@AndroidEntryPoint
class NewmanMessagingService : FirebaseMessagingService() {
    @Inject lateinit var http: OkHttpClient
    @Inject lateinit var store: ConnectionStore
    @Inject lateinit var account: Account
    @Inject lateinit var scope: CoroutineScope

    @Inject lateinit var sharing: LocationSharing

    override fun onMessageReceived(message: RemoteMessage) {
        if (store.token.isNullOrEmpty()) return // signed out: nothing should show
        if (message.data["type"] == "widgets") {
            // Something a home-screen widget shows changed: fresh states for them (silently).
            runBlocking { withTimeoutOrNull(9_000) { WidgetSync.from(this@NewmanMessagingService).refresh() } }
            return
        }
        if (message.data["type"] == "live") {
            // A live status started, changed or ended: its ongoing notification follows (silently).
            LiveStatusNotifier.from(this).applyPush(message.data)
            return
        }
        if (LocatePush.isLocate(message.data)) {
            // Someone tapped this person on their map: ONE fresh fix (source "refresh"), silently.
            // This runs on FCM's worker thread, which may block for a few seconds.
            runBlocking { withTimeoutOrNull(9_000) { sharing.locateNow() } }
            return
        }
        val categories = PushPayload.parseCategories(store.pushCategoriesJson)
        val payload = PushPayload.parse(message.data, categories)
        Notifications.show(this, payload, http)
    }

    override fun onNewToken(token: String) {
        scope.launch { account.registerPush(token) }
    }
}

/** A plain (non-sensitive) notification button: posted to the hub straight from the shade. */
@AndroidEntryPoint
class ActionReceiver : BroadcastReceiver() {
    @Inject lateinit var account: Account
    @Inject lateinit var store: ConnectionStore
    @Inject lateinit var scope: CoroutineScope

    override fun onReceive(context: Context, intent: Intent) {
        val press = Notifications.Press.from(intent) ?: return
        val pending = goAsync()
        scope.launch {
            try {
                press(context.applicationContext, account, store, press)
            } finally {
                pending.finish()
            }
        }
    }

    companion object {
        suspend fun press(context: Context, account: Account, store: ConnectionStore, p: Notifications.Press): Result<Unit> {
            val result = account.pressAction(p.url, ActionRequest(p.buttonId, p.subject, store.deviceId, p.exp, p.token))
            if (result.isSuccess) {
                if (p.buttonId == "dismiss") NotificationManagerCompat.from(context).cancel(p.notificationId)
                else Notifications.showOutcome(context, p.notificationId, p.title, "Done: ${p.label}", ok = true)
            } else {
                Notifications.showOutcome(context, p.notificationId, p.title,
                    "Couldn't ${p.label.lowercase()}: ${result.exceptionOrNull()?.message ?: "try again"}", ok = false)
            }
            return result
        }
    }
}
