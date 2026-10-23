package ca.nimaste.newman.wall

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.util.Log
import ca.nimaste.newman.core.wall.CheckInTiming
import ca.nimaste.newman.core.wall.WallRinging
import java.util.concurrent.ConcurrentHashMap

/**
 * Newman's alarms and timers ringing on the tablet: the chosen alarm or timer sound (the tablet's own alarm
 * tones, WallSettings) looping on Android's alarm channel (USAGE_ALARM: the tablet's alarm volume, not the
 * media volume), until stopped or for the "Stop ringing after" time the page passes. One rings at a time.
 * [backup]: rung by the app itself (AlarmBackup) because the page didn't - WallActivity then shows its own
 * Stop / Snooze over the page.
 */
object AlarmRinger {
    private const val TAG = "NewmanWall"
    private val main = Handler(Looper.getMainLooper())
    private var player: MediaPlayer? = null
    private var wake: PowerManager.WakeLock? = null

    data class Current(val ring: WallRinging.Ring, val backup: Boolean)

    @Volatile var current: Current? = null
        private set

    /** id -> when the page last asked to ring it (ms): the backup leaves those alone. */
    val rung: MutableMap<String, Long> = ConcurrentHashMap()

    /** Listeners (WallActivity) hear when ringing starts or stops. Main thread. */
    val listeners = mutableSetOf<() -> Unit>()

    val isRinging: Boolean get() = current != null

    /** The sound for an alarm or a timer: the one chosen in the settings, else the tablet's default alarm sound. */
    fun toneFor(context: Context, timer: Boolean): Uri? {
        val s = WallSettings(context)
        val chosen = if (timer) s.timerTone else s.alarmTone
        if (chosen.isNotBlank()) return Uri.parse(chosen)
        return defaultTone(context)
    }

    fun defaultTone(context: Context): Uri? =
        RingtoneManager.getActualDefaultRingtoneUri(context, RingtoneManager.TYPE_ALARM)
            ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)

    /** Rings [ring] (any thread). */
    fun start(context: Context, ring: WallRinging.Ring, backup: Boolean) {
        val app = context.applicationContext
        if (!backup) rung[ring.id] = System.currentTimeMillis()
        main.post {
            stopNow()
            current = Current(ring, backup)
            play(app, toneFor(app, ring.isTimer), looping = true)
            try {
                wake = app.getSystemService(PowerManager::class.java)
                    ?.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "NewmanWall:ringing")
                    ?.apply { setReferenceCounted(false); acquire(ring.capMs + 5_000) }
            } catch (e: Exception) {
                // the window keeps the screen on anyway
            }
            main.postDelayed(capReached, ring.capMs)
            listeners.toList().forEach { it() }
        }
    }

    private val capReached = Runnable { stop() }

    /** Stops whatever rings (any thread). */
    fun stop() {
        main.post {
            val was = current != null
            stopNow()
            if (was) listeners.toList().forEach { it() }
        }
    }

    /** A sound for the settings' ▶ Play / Test alarm sound: [uri] (null = the default) for 2 seconds. */
    fun preview(context: Context, uri: Uri?) {
        val app = context.applicationContext
        main.post {
            if (current != null) return@post // not over a real ring
            stopPlayer()
            play(app, uri ?: defaultTone(app), looping = false)
            main.postDelayed({ if (current == null) stopPlayer() }, 2_000)
        }
    }

    fun stopPreview() = main.post { if (current == null) stopPlayer() }

    private fun play(context: Context, uri: Uri?, looping: Boolean) {
        val tries = listOfNotNull(uri, defaultTone(context)).distinct()
        for (u in tries) {
            val p = MediaPlayer()
            try {
                p.setAudioAttributes(
                    AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build(),
                )
                p.setDataSource(context, u)
                p.isLooping = looping
                p.prepare()
                p.start()
                player = p
                return
            } catch (e: Exception) {
                Log.w(TAG, "Couldn't play the alarm sound $u: $e")
                p.release()
            }
        }
    }

    private fun stopPlayer() {
        try {
            player?.stop()
        } catch (e: Exception) {
            // not started
        }
        player?.release()
        player = null
    }

    private fun stopNow() {
        main.removeCallbacks(capReached)
        stopPlayer()
        try {
            if (wake?.isHeld == true) wake?.release()
        } catch (e: RuntimeException) {
            // timed out
        }
        wake = null
        current = null
    }

    /** The tablet's alarm sounds (Android's RingtoneManager, TYPE_ALARM): title -> address. */
    fun alarmTones(context: Context): List<Pair<String, String>> {
        val out = mutableListOf<Pair<String, String>>()
        try {
            val rm = RingtoneManager(context.applicationContext).apply { setType(RingtoneManager.TYPE_ALARM) }
            val c = rm.cursor
            while (c.moveToNext()) {
                val title = c.getString(RingtoneManager.TITLE_COLUMN_INDEX) ?: continue
                out += title to rm.getRingtoneUri(c.position).toString()
            }
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't list the alarm sounds: $e")
        }
        return out.distinctBy { it.second }
    }
}

/**
 * The coming alarms (NewmanWall.alarmSchedule from the page), kept so the app can ring one itself a
 * minute after its time when nothing rang it - the page gone, the hub unreachable. Set again on every
 * change, app start and restart (BootReceiver). Like the battery check-in, an alarm allowed through Doze.
 */
object AlarmBackup {
    private const val PREFS = "alarm_backup"
    private const val KEY = "schedule"
    private const val ACTION = "ca.nimaste.newman.wall.ALARM_BACKUP"
    private const val TAG = "NewmanWall"

    fun save(context: Context, json: String?) {
        val list = WallRinging.parseSchedule(json)
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString(KEY, WallRinging.scheduleJson(list)).apply()
        arm(context)
    }

    private fun load(context: Context): List<WallRinging.Planned> =
        WallRinging.parseSchedule(context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null))

    /** A snooze of a ring the app made itself: rings again in [minutes]. */
    fun snooze(context: Context, ring: WallRinging.Ring, minutes: Int) {
        val at = System.currentTimeMillis() + minutes * 60_000L - WallRinging.BACKUP_AFTER_MS
        val list = load(context).filter { it.id != ring.id } + WallRinging.Planned(ring.id, at, ring.title)
        save(context, WallRinging.scheduleJson(list))
    }

    /** Stopped: that alarm's ring (this day) is done. */
    fun stopped(ring: WallRinging.Ring) {
        AlarmRinger.rung[ring.id] = System.currentTimeMillis()
    }

    private fun pending(c: Context, flags: Int): PendingIntent? =
        PendingIntent.getBroadcast(c, 1, Intent(c, AlarmBackupReceiver::class.java).setAction(ACTION),
            flags or PendingIntent.FLAG_IMMUTABLE)

    fun arm(context: Context) {
        val c = context.applicationContext
        val am = c.getSystemService(AlarmManager::class.java) ?: return
        val next = WallRinging.nextBackup(load(c), System.currentTimeMillis(), AlarmRinger.rung)
        if (next == null) {
            pending(c, PendingIntent.FLAG_NO_CREATE)?.let { am.cancel(it) }
            return
        }
        val pi = pending(c, PendingIntent.FLAG_UPDATE_CURRENT) ?: return
        val at = next.at + WallRinging.BACKUP_AFTER_MS
        try {
            val exact = CheckInTiming.exact(Build.VERSION.SDK_INT, Build.VERSION.SDK_INT >= 31 && am.canScheduleExactAlarms())
            if (exact) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
            else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
        } catch (e: Exception) {
            try {
                am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
            } catch (e2: Exception) {
                Log.w(TAG, "alarm backup not set: $e2")
            }
        }
    }

    /** The backup time came: ring what nothing rang, bring the wall to the front, set the next one. */
    fun fire(context: Context) {
        val c = context.applicationContext
        val ringMs = WallSettings(c).ringMinutes * 60_000L
        val due = WallRinging.dueNow(load(c), System.currentTimeMillis(), AlarmRinger.rung, ringMs)
        if (due != null && !AlarmRinger.isRinging) {
            Log.w(TAG, "Alarm backup: ringing ${due.title} - nothing else rang it")
            AlarmRinger.rung[due.id] = System.currentTimeMillis() // (once)
            AlarmRinger.start(c, WallRinging.Ring("alarm", due.id, due.title, ringMs), backup = true)
            bringWallBack(c, "Alarm: bringing the wall back to ring ${due.title}", dimmed = false)
        }
        arm(c)
    }
}

class AlarmBackupReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        AlarmBackup.fire(context)
    }
}
