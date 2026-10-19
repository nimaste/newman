package ca.nimaste.newman.wall

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.Ringtone
import android.media.RingtoneManager
import android.os.Handler
import android.os.Looper
import ca.nimaste.newman.core.wall.WallVolume

/**
 * The tablet's own volumes for the wall settings' Sound section: media (Alexa's voice, music, the chime,
 * "Hey Newman" replies - what the volume buttons change) and alarms (timers and alarms ringing).
 * Android may refuse a change while Do Not Disturb is on: [set] then returns false.
 */
class WallSound(context: Context) {
    private val app = context.applicationContext
    private val am = app.getSystemService(AudioManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    private var ringing: Ringtone? = null

    fun percent(stream: Int): Int {
        val a = am ?: return 0
        return WallVolume.percentOf(a.getStreamVolume(stream), a.getStreamMaxVolume(stream))
    }

    /** Sets a stream to [percent]; false when Android refuses (Do Not Disturb). */
    fun set(stream: Int, percent: Int): Boolean {
        val a = am ?: return false
        return try {
            a.setStreamVolume(stream, WallVolume.indexOf(percent, a.getStreamMaxVolume(stream)), 0)
            true
        } catch (e: SecurityException) {
            false
        }
    }

    /** The chime, on the media stream. */
    fun testMedia() = Chime.play(AudioAttributes.USAGE_MEDIA)

    /** The default alarm tone for 2 seconds, on the alarm stream. */
    fun testAlarm() {
        stopAlarm()
        val uri = RingtoneManager.getActualDefaultRingtoneUri(app, RingtoneManager.TYPE_ALARM)
            ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM) ?: return
        val r = try {
            RingtoneManager.getRingtone(app, uri)
        } catch (e: Exception) {
            null
        } ?: return
        r.audioAttributes = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()
        ringing = r
        try {
            r.play()
        } catch (e: Exception) {
            ringing = null
            return
        }
        main.postDelayed({ stopAlarm() }, 2_000)
    }

    fun stopAlarm() {
        main.removeCallbacksAndMessages(null)
        try {
            ringing?.stop()
        } catch (e: Exception) {
            // already stopped
        }
        ringing = null
    }
}
