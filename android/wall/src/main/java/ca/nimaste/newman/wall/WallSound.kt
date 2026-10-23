package ca.nimaste.newman.wall

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import android.net.Uri
import ca.nimaste.newman.core.wall.WallVolume

/**
 * The tablet's own volumes for the wall settings' Sound section: media (Alexa's voice, music, the chime,
 * "Hey Newman" replies - what the volume buttons change) and alarms (Newman's alarms and timers ringing),
 * and the alarm sounds to choose from (AlarmRinger plays them).
 * Android may refuse a change while Do Not Disturb is on: [set] then returns false.
 */
class WallSound(context: Context) {
    private val app = context.applicationContext
    private val am = app.getSystemService(AudioManager::class.java)

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

    /** An alarm sound ([uri]: one of [alarmTones]; "" = the tablet's default) for 2 seconds, on the alarm stream. */
    fun testAlarm(uri: String = "") = AlarmRinger.preview(app, if (uri.isBlank()) null else Uri.parse(uri))

    fun stopAlarm() = AlarmRinger.stopPreview()

    /** The tablet's own alarm sounds: title -> address. */
    fun alarmTones(): List<Pair<String, String>> = AlarmRinger.alarmTones(app)

    /** The address of the tablet's default alarm sound (to show it chosen when nothing else is). */
    fun defaultAlarmTone(): String = AlarmRinger.defaultTone(app)?.toString() ?: ""
}
