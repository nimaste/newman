package ca.nimaste.newman.wall

import android.app.Application
import android.app.admin.DeviceAdminReceiver
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.os.PowerManager
import ca.nimaste.newman.core.mtls.MTLSStore
import ca.nimaste.newman.core.net.HubConnection
import ca.nimaste.newman.core.net.HubHttp
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.core.wall.ComeBack
import ca.nimaste.newman.core.wall.WallBrightness
import ca.nimaste.newman.core.wall.WallRinging
import ca.nimaste.newman.core.wall.WallShell
import ca.nimaste.newman.core.voice.AssistantReturn
import okhttp3.OkHttpClient

class WallApp : Application() {
    override fun onCreate() {
        super.onCreate()
        MTLSStore.init(this)
        val connection = ConnectionStore.get(this)
        http = HubHttp.client(HubConnection { connection.snapshot }, "NewmanWall/${BuildConfig.VERSION_NAME} (Android)")
        // The battery with the screen off: the check-in alarm (through Doze) and WorkManager every 15 minutes,
        // and at once when the screen goes off. Both set afresh on every start, so a lost one comes back.
        BatteryAlarm.arm(this)
        BatteryWork.schedule(this)
        BatteryWork.watch(this)
        AlarmBackup.arm(this) // (the coming alarms, kept as a backup: AlarmRinging.kt)
        AlarmVolumeKeeper.start(this) // ("Keep the alarm volume at full", when it's on)
    }

    companion object {
        /** The mTLS client for the wall's own requests (connecting, choosing the hub's address). */
        lateinit var http: OkHttpClient
            private set
    }
}

/** The tablet's own settings (the server and Cloudflare token live in core's ConnectionStore). */
class WallSettings(context: Context) {
    private val prefs: SharedPreferences = context.applicationContext.getSharedPreferences("wall_settings", Context.MODE_PRIVATE)

    /** Minutes without a touch before dimming; 0 = never. */
    var dimMinutes: Int
        get() = prefs.getInt("dim_minutes", WallBrightness.DEFAULT_DIM_MINUTES)
        set(v) = prefs.edit().putInt("dim_minutes", v.coerceIn(0, 120)).apply()

    /** Brightness while awake, WallBrightness.FLOOR..1, or -1 for the system's own setting. */
    var brightLevel: Float
        get() = WallBrightness.awake(prefs.getFloat("bright_level", -1f))
        set(v) = prefs.edit().putFloat("bright_level", WallBrightness.awake(v)).apply()

    /** Brightness while dimmed: never below WallBrightness.FLOOR, so a dimmed wall still looks on (older saves of 0.02 read as the floor). */
    var dimLevel: Float
        get() = WallBrightness.dimSetting(prefs.getFloat("dim_level", WallBrightness.DEFAULT_DIM))
        set(v) = prefs.edit().putFloat("dim_level", WallBrightness.dimSetting(v)).apply()

    /** The page's theme colour (NewmanWall.setTheme), painted behind it while it loads or can't be reached. */
    var shell: WallShell
        get() = WallShell.saved(prefs.getString("shell_background", null), prefs.getString("shell_scheme", null))
        set(v) = prefs.edit().putString("shell_background", v.background).putString("shell_scheme", if (v.dark) "dark" else "light").apply()

    /** Without device owner: pin the screen (asked again whenever the wall opens unpinned). */
    var pinScreen: Boolean
        get() = prefs.getBoolean("pin_screen", false)
        set(v) = prefs.edit().putBoolean("pin_screen", v).apply()

    /** Come back to the front after Alexa (or another assistant) took over the screen and finished. */
    var returnAfterAssistant: Boolean
        get() = prefs.getBoolean("return_after_assistant", true)
        set(v) = prefs.edit().putBoolean("return_after_assistant", v).apply()

    /** Seconds of quiet after the assistant's answer before coming back ("Wait after Alexa goes quiet"). */
    var returnQuietSeconds: Int
        get() = AssistantReturn.quietSetting(prefs.getInt("return_quiet_seconds", AssistantReturn.QUIET_DEFAULT_S))
        set(v) = prefs.edit().putInt("return_quiet_seconds", AssistantReturn.quietSetting(v)).apply()

    /** Come back to the front after leaving it any other way (Home, Recents, another app): ComeBack. */
    var comeBackAfterLeaving: Boolean
        get() = prefs.getBoolean("come_back_after_leaving", true)
        set(v) = prefs.edit().putBoolean("come_back_after_leaving", v).apply()

    /** Minutes away with no other app playing before coming back (ComeBack.MINUTE_CHOICES). */
    var comeBackMinutes: Int
        get() = ComeBack.minutesSetting(prefs.getInt("come_back_minutes", ComeBack.DEFAULT_MINUTES))
        set(v) = prefs.edit().putInt("come_back_minutes", ComeBack.minutesSetting(v)).apply()

    /** Come back to the front when the screen wakes. */
    var comeBackOnWake: Boolean
        get() = prefs.getBoolean("come_back_on_wake", true)
        set(v) = prefs.edit().putBoolean("come_back_on_wake", v).apply()

    /** Newman's alarms ringing: one of the tablet's alarm sounds (its address), "" = the tablet's default. */
    var alarmTone: String
        get() = prefs.getString("alarm_tone", "") ?: ""
        set(v) = prefs.edit().putString("alarm_tone", v).apply()

    /** Newman's timers ringing: the same choice, its own. */
    var timerTone: String
        get() = prefs.getString("timer_tone", "") ?: ""
        set(v) = prefs.edit().putString("timer_tone", v).apply()

    /** "Snooze for" (minutes, WallRinging.SNOOZE_CHOICES). */
    var snoozeMinutes: Int
        get() = WallRinging.snoozeSetting(prefs.getInt("snooze_minutes", WallRinging.DEFAULT_SNOOZE))
        set(v) = prefs.edit().putInt("snooze_minutes", WallRinging.snoozeSetting(v)).apply()

    /** "Stop ringing after" (minutes, WallRinging.RING_CHOICES). */
    var ringMinutes: Int
        get() = WallRinging.ringSetting(prefs.getInt("ring_minutes", WallRinging.DEFAULT_RING))
        set(v) = prefs.edit().putInt("ring_minutes", WallRinging.ringSetting(v)).apply()

    /** "Keep the alarm volume at full": put the alarm volume back to full whenever anything lowers it (AlarmVolumeKeeper). */
    var keepAlarmFull: Boolean
        get() = prefs.getBoolean("keep_alarm_full", false)
        set(v) = prefs.edit().putBoolean("keep_alarm_full", v).apply()
}

/**
 * Restart the wall after the tablet boots (or the app is updated), and its battery check-ins with it.
 * Started dimmed with the screen turned on when it's off (keepWallUp's rule for a wall that isn't running);
 * should that be refused, the next check-in alarm tries again.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        // (WallApp.onCreate set them as this process started; again here in case it was already running.)
        BatteryAlarm.arm(context)
        BatteryWork.schedule(context)
        AlarmBackup.arm(context)
        val screenOn = context.getSystemService(PowerManager::class.java)?.isInteractive != false
        // Android 10+ may refuse a start from the background unless the app is the home app, the device
        // owner, or has "display over other apps" - see the README (bringWallToFront logs it).
        bringWallBack(context.applicationContext, "Starting the wall after ${if (intent.action == Intent.ACTION_BOOT_COMPLETED) "a restart" else "an update"}",
            dimmed = !screenOn)
    }
}

/** Device admin, so the app can be made device owner (adb) and use lock task mode. */
class WallAdminReceiver : DeviceAdminReceiver()
