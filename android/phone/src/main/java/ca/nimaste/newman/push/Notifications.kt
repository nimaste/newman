package ca.nimaste.newman.push

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import ca.nimaste.newman.MainActivity
import ca.nimaste.newman.R
import ca.nimaste.newman.core.model.PushButton
import ca.nimaste.newman.core.push.PushPayload
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit

/**
 * Builds the hub's notifications on the phone (FCM messages are data-only so the app picks the
 * channel and attaches the snapshot). Buttons come from the payload; sensitive ones open the app
 * to confirm with the fingerprint / screen lock first.
 */
object Notifications {
    const val CHANNEL_ALERTS = "alerts"
    const val CHANNEL_URGENT = "urgent"
    const val CHANNEL_DOORBELL = "doorbell"

    const val EXTRA_BUTTON_ID = "newman.button.id"
    const val EXTRA_BUTTON_LABEL = "newman.button.label"
    const val EXTRA_BUTTON_SENSITIVE = "newman.button.sensitive"
    const val EXTRA_ACTION_URL = "newman.action.url"
    const val EXTRA_SUBJECT = "newman.action.subject"
    const val EXTRA_EXP = "newman.action.exp"
    const val EXTRA_TOKEN = "newman.action.token"
    const val EXTRA_NOTIFICATION_ID = "newman.notification.id"
    const val EXTRA_TITLE = "newman.notification.title"
    const val EXTRA_OPEN = "newman.open"
    const val EXTRA_CAMERA = "newman.camera"
    /** A camera alert's clip id and review id: tapping the notification opens that alert's video. */
    const val EXTRA_CLIP = "newman.clip"
    const val EXTRA_REVIEW = "newman.review"
    const val ACTION_PRESS = "ca.nimaste.newman.PRESS"
    const val ACTION_OPEN = "ca.nimaste.newman.OPEN"

    fun createChannels(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        nm.createNotificationChannel(NotificationChannel(CHANNEL_ALERTS, "Alerts", NotificationManager.IMPORTANCE_DEFAULT).apply {
            description = "Notifications from your automations"
        })
        nm.createNotificationChannel(NotificationChannel(CHANNEL_URGENT, "Time-sensitive alerts", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "Automations marked time-sensitive (an unlocked door at night, a leak)"
            enableVibration(true)
        })
        nm.createNotificationChannel(NotificationChannel(CHANNEL_DOORBELL, "Doorbell", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "Someone at the door"
            enableVibration(true)
            setSound(RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE),
                AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE).build())
        })
    }

    fun canPost(context: Context): Boolean =
        (Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) &&
            NotificationManagerCompat.from(context).areNotificationsEnabled()

    /** Downloads the snapshot (through the mTLS client) and posts the notification. Call off the main thread. */
    fun show(context: Context, p: PushPayload, http: OkHttpClient) {
        if (!canPost(context)) return
        val picture = p.snapshotUrl?.let { fetchBitmap(http, it) }
        val channel = when {
            p.isDoorbell -> CHANNEL_DOORBELL
            p.timeSensitive -> CHANNEL_URGENT
            else -> CHANNEL_ALERTS
        }
        val b = NotificationCompat.Builder(context, channel)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(ContextCompat.getColor(context, R.color.notification_accent))
            .setContentTitle(p.title)
            .setContentText(p.body)
            .setAutoCancel(true)
            .setCategory(if (p.isDoorbell) NotificationCompat.CATEGORY_CALL else NotificationCompat.CATEGORY_EVENT)
            .setPriority(if (p.timeSensitive || p.isDoorbell) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT)
            .setContentIntent(openIntent(context, p))
        p.thread?.let { b.setGroup(it) }
        if (picture != null) {
            b.setLargeIcon(picture)
            b.setStyle(NotificationCompat.BigPictureStyle().bigPicture(picture).bigLargeIcon(null as Bitmap?))
        } else {
            b.setStyle(NotificationCompat.BigTextStyle().bigText(p.body))
        }
        val id = p.notificationId
        if (p.canAct) {
            p.buttons.take(3).forEachIndexed { i, button ->
                b.addAction(0, button.label, buttonIntent(context, p, button, id, i))
            }
        }
        try {
            NotificationManagerCompat.from(context).notify(id, b.build())
        } catch (e: SecurityException) {
            // Notifications were turned off between the check and now.
        }
    }

    /** Replace a notification with the outcome of a button press. */
    fun showOutcome(context: Context, id: Int, title: String, text: String, ok: Boolean) {
        if (!canPost(context)) return
        val b = NotificationCompat.Builder(context, CHANNEL_ALERTS)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(ContextCompat.getColor(context, R.color.notification_accent))
            .setContentTitle(title)
            .setContentText(text)
            .setOnlyAlertOnce(true)
            .setAutoCancel(true)
            .setSilent(true)
            .setContentIntent(PendingIntent.getActivity(context, id, Intent(context, MainActivity::class.java)
                .putExtra(EXTRA_OPEN, "alerts"), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
        if (ok) b.setTimeoutAfter(6_000)
        try {
            NotificationManagerCompat.from(context).notify(id, b.build())
        } catch (e: SecurityException) {
            // ignore
        }
    }

    private fun openIntent(context: Context, p: PushPayload): PendingIntent {
        val i = Intent(context, MainActivity::class.java).setAction(ACTION_OPEN)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            .putExtra(EXTRA_OPEN, "alerts")
        if (p.clip != null || p.reviewId != null) i.putExtra(EXTRA_CLIP, p.clip).putExtra(EXTRA_REVIEW, p.reviewId)
        return PendingIntent.getActivity(context, p.notificationId, i, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    private fun buttonIntent(context: Context, p: PushPayload, button: PushButton, id: Int, index: Int): PendingIntent {
        val code = id * 8 + index
        if (button.id == "open") return openIntent(context, p)
        val extras = Intent()
            .putExtra(EXTRA_BUTTON_ID, button.id)
            .putExtra(EXTRA_BUTTON_LABEL, button.label)
            .putExtra(EXTRA_BUTTON_SENSITIVE, button.sensitive)
            .putExtra(EXTRA_ACTION_URL, p.actionUrl)
            .putExtra(EXTRA_SUBJECT, p.actionSubject)
            .putExtra(EXTRA_EXP, p.actionExp)
            .putExtra(EXTRA_TOKEN, p.actionToken)
            .putExtra(EXTRA_NOTIFICATION_ID, id)
            .putExtra(EXTRA_TITLE, p.title)
        return if (button.sensitive) {
            // Unlock / open / disarm: the app confirms it's the phone's owner first.
            val i = Intent(context, MainActivity::class.java).setAction(ACTION_PRESS).putExtras(extras)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            PendingIntent.getActivity(context, code, i, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        } else {
            val i = Intent(context, ActionReceiver::class.java).setAction(ACTION_PRESS).putExtras(extras)
            PendingIntent.getBroadcast(context, code, i, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        }
    }

    private fun fetchBitmap(http: OkHttpClient, url: String): Bitmap? = try {
        val client = http.newBuilder().callTimeout(10, TimeUnit.SECONDS).build()
        client.newCall(Request.Builder().url(url).build()).execute().use { r ->
            if (!r.isSuccessful) null else r.body?.bytes()?.let { BitmapFactory.decodeByteArray(it, 0, it.size) }
        }
    } catch (e: Exception) {
        null
    }

    /** A pressed button, from a notification intent's extras. */
    data class Press(
        val buttonId: String, val label: String, val sensitive: Boolean, val url: String, val subject: String,
        val exp: String, val token: String, val notificationId: Int, val title: String,
    ) {
        companion object {
            fun from(i: Intent?): Press? {
                i ?: return null
                return Press(
                    i.getStringExtra(EXTRA_BUTTON_ID) ?: return null,
                    i.getStringExtra(EXTRA_BUTTON_LABEL) ?: "",
                    i.getBooleanExtra(EXTRA_BUTTON_SENSITIVE, false),
                    i.getStringExtra(EXTRA_ACTION_URL) ?: return null,
                    i.getStringExtra(EXTRA_SUBJECT) ?: return null,
                    i.getStringExtra(EXTRA_EXP) ?: return null,
                    i.getStringExtra(EXTRA_TOKEN) ?: return null,
                    i.getIntExtra(EXTRA_NOTIFICATION_ID, 0),
                    i.getStringExtra(EXTRA_TITLE) ?: "Newman",
                )
            }
        }
    }
}
