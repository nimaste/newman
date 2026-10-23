package ca.nimaste.newman.wall

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.database.ContentObserver
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import androidx.core.content.ContextCompat
import ca.nimaste.newman.core.wall.AlarmVolumeKeep

/**
 * "Keep the alarm volume at full" (wall settings › Sound): while it's on, whenever the alarm volume drops
 * below full - Alexa lowers it each time her alarms and timers ring - put it straight back. Hears Android's
 * volume-changed broadcast for the alarm stream, watches the volume settings as a backup, and is checked
 * again when the wall comes back to the front and when the switch is turned on. Runs for as long as the
 * process does (started in WallApp). Only ever raises the alarm stream; never touches another one.
 */
object AlarmVolumeKeeper {
    private const val TAG = "NewmanWall"
    private const val VOLUME_CHANGED = "android.media.VOLUME_CHANGED_ACTION"
    private const val EXTRA_STREAM = "android.media.EXTRA_VOLUME_STREAM_TYPE"

    private var app: Context? = null
    private var lastLogAt = -1L
    private var notLogged = 0
    private var lastRefusedLogAt = -1L

    /** From WallApp.onCreate: watch from now on (and check once now). */
    fun start(context: Context) {
        if (app != null) return
        val a = context.applicationContext
        app = a
        ContextCompat.registerReceiver(a, object : BroadcastReceiver() {
            override fun onReceive(c: Context, intent: Intent) {
                if (intent.getIntExtra(EXTRA_STREAM, -1) == AudioManager.STREAM_ALARM) check()
            }
        }, IntentFilter(VOLUME_CHANGED), ContextCompat.RECEIVER_NOT_EXPORTED)
        // (Android saves each volume in its settings, so this hears a change the broadcast missed.)
        a.contentResolver.registerContentObserver(Settings.System.CONTENT_URI, true, object : ContentObserver(Handler(Looper.getMainLooper())) {
            override fun onChange(selfChange: Boolean) = check()
        })
        check()
    }

    /** Below full with the switch on: back to full. On the main thread. */
    fun check() {
        val a = app ?: return
        val am = a.getSystemService(AudioManager::class.java) ?: return
        val max = am.getStreamMaxVolume(AudioManager.STREAM_ALARM)
        val now = am.getStreamVolume(AudioManager.STREAM_ALARM)
        if (!AlarmVolumeKeep.shouldRestore(WallSettings(a).keepAlarmFull, now, max)) return
        val t = SystemClock.elapsedRealtime()
        try {
            am.setStreamVolume(AudioManager.STREAM_ALARM, max, 0)
        } catch (e: SecurityException) {
            if (AlarmVolumeKeep.shouldLog(t, lastRefusedLogAt)) {
                lastRefusedLogAt = t
                Log.w(TAG, "Alarm volume: Android didn't allow putting it back to full (was $now) - Do Not Disturb may be on: $e")
            }
            return
        }
        if (AlarmVolumeKeep.shouldLog(t, lastLogAt)) {
            Log.w(TAG, "Alarm volume put back to full (was $now)" + if (notLogged > 0) " - and $notLogged more times since the last line" else "")
            lastLogAt = t
            notLogged = 0
        } else {
            notLogged++
        }
    }
}
