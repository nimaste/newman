package ca.nimaste.newman.location

import android.content.Context
import android.location.Location
import android.util.Log
import ca.nimaste.newman.core.api.ApiErrors
import ca.nimaste.newman.core.api.HubException
import ca.nimaste.newman.core.api.NewmanApi
import ca.nimaste.newman.core.api.hubCall
import ca.nimaste.newman.core.people.LocatePush
import ca.nimaste.newman.core.people.LocationEnable
import ca.nimaste.newman.core.people.LocationReport
import ca.nimaste.newman.core.people.LocationSettings
import ca.nimaste.newman.core.people.ReportSchedule
import ca.nimaste.newman.core.settings.ConnectionStore
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Sharing this phone's location with the household (GET/PUT /api/me/location, POST
 * /api/me/location/report). The hub decides whether this phone shares ("choice" lets the person
 * switch it, "required" doesn't, "off" hides it); this keeps the cheap background location updates
 * running to match (~5 min at balanced power, or on movement) and posts fixes. There is no live
 * mode: the phone never streams its location.
 *
 * Tap-to-refresh: when someone taps this person on their map, the hub (at most once every 2 minutes
 * per person) sends an FCM data message {"type": "locate"}; [locateNow] takes ONE fresh fix, posts
 * it with `source: "refresh"` and stops. The tracked person is never told they are being viewed.
 */
@Singleton
class LocationSharing @Inject constructor(
    @ApplicationContext private val ctx: Context,
    private val api: NewmanApi,
    private val store: ConnectionStore,
    @Suppress("unused") private val scope: CoroutineScope,
) {
    private val prefs = ctx.getSharedPreferences("newman_location", Context.MODE_PRIVATE)
    private val lock = Mutex()

    /** GET /api/me/location; null = not known yet, or the hub doesn't offer location sharing (404). */
    private val _settings = MutableStateFlow<LocationSettings?>(null)
    val settings: StateFlow<LocationSettings?> = _settings.asStateFlow()

    /** Bumped when permissions may have changed, so screens re-read them. */
    private val _permissions = MutableStateFlow(0)
    val permissions: StateFlow<Int> = _permissions.asStateFlow()

    private var sharing: Boolean
        get() = prefs.getBoolean(KEY_SHARING, false)
        set(v) = prefs.edit().putBoolean(KEY_SHARING, v).apply()
    private var everySeconds: Int
        get() = prefs.getInt(KEY_EVERY, 300)
        set(v) = prefs.edit().putInt(KEY_EVERY, v).apply()
    /** When this phone last answered a locate (epoch s); 0 = never. */
    private var locateAnsweredAt: Double
        get() = prefs.getLong(KEY_LOCATE_AT, 0L) / 1000.0
        set(v) = prefs.edit().putLong(KEY_LOCATE_AT, (v * 1000).toLong()).apply()

    private fun lastSent(): ReportSchedule.Last? {
        val at = prefs.getLong(KEY_LAST_AT, 0L)
        if (at == 0L) return null
        return ReportSchedule.Last(at / 1000.0, Double.fromBits(prefs.getLong(KEY_LAST_LAT, 0L)), Double.fromBits(prefs.getLong(KEY_LAST_LON, 0L)))
    }

    private fun saveLast(at: Double, lat: Double, lon: Double) = prefs.edit()
        .putLong(KEY_LAST_AT, (at * 1000).toLong()).putLong(KEY_LAST_LAT, lat.toBits()).putLong(KEY_LAST_LON, lon.toBits()).apply()

    private val signedIn: Boolean get() = !store.token.isNullOrEmpty() && !store.serverUrl.isNullOrEmpty()

    init {
        // Left over from the live-follow days.
        prefs.edit().remove(OLD_KEY_LIVE_AT).remove(OLD_KEY_TRACKING_LIVE).apply()
    }

    fun hasForeground() = LocationTracker.hasForeground(ctx)
    fun hasBackground() = LocationTracker.hasBackground(ctx)

    fun permissionsChanged() {
        _permissions.value++
        resume()
    }

    /** Reads the hub's setting and starts or stops updates to match. Quiet on any failure. */
    suspend fun refresh(): LocationSettings? {
        if (!signedIn) return null
        val s = try {
            hubCall { api.myLocation() }
        } catch (e: Exception) {
            if (e is HubException && (e.code == 404 || e.code == 403)) {
                // An older hub (or sharing switched off for this account): nothing to share.
                _settings.value = null
                apply(false, everySeconds)
            }
            return _settings.value
        }
        _settings.value = s
        apply(s.sharing, s.everySeconds)
        return s
    }

    /** The Me screen's switch ("choice" accounts only). The hub's reply, or its words on failure. */
    suspend fun setEnabled(on: Boolean): Result<LocationSettings> = try {
        val s = hubCall { api.setMyLocation(LocationEnable(on)) }
        _settings.value = s
        apply(s.sharing, s.everySeconds)
        if (s.sharing) reportNow("app")
        Result.success(s)
    } catch (e: Exception) {
        Result.failure(IllegalStateException(ApiErrors.message(e)))
    }

    private fun apply(on: Boolean, every: Int) {
        sharing = on
        everySeconds = every
        if (on) LocationTracker.start(ctx, every) else LocationTracker.stop(ctx)
    }

    /** After a reboot, an app update or a permission change: start updates again if sharing. */
    fun resume() {
        if (sharing && signedIn) LocationTracker.start(ctx, everySeconds) else LocationTracker.stop(ctx)
    }

    /** Signed out: stop everything and forget the cached state. */
    fun signedOut() {
        LocationTracker.stop(ctx)
        prefs.edit().clear().apply()
        _settings.value = null
    }

    /** One fix now (the app came to the front) - cheap, and only when sharing. */
    suspend fun reportNow(source: String) {
        if (!sharing || !signedIn) return
        val fix = LocationTracker.currentFix(ctx, precise = false, timeoutMs = 15_000) ?: return
        onFix(fix, source, force = true)
    }

    /**
     * The hub's one-shot locate (someone tapped this person): ONE fresh fix, posted with source
     * "refresh", then nothing more. A duplicate delivery of the same tap is ignored.
     */
    suspend fun locateNow() {
        if (!sharing || !signedIn) return
        val t = now()
        if (!LocatePush.shouldAnswer(locateAnsweredAt.takeIf { it > 0 }, t)) return
        locateAnsweredAt = t
        val fix = LocationTracker.currentFix(ctx, precise = true, timeoutMs = 8_000) ?: return
        onFix(fix, LocatePush.SOURCE, force = true)
    }

    /** A fix arrived (from updates or a one-shot request): post it if it's time. */
    suspend fun onFix(loc: Location, source: String? = null, force: Boolean = false) {
        lock.withLock { post(loc, source, force) }
    }

    private suspend fun post(loc: Location, source: String?, force: Boolean) {
        if (!sharing || !signedIn) {
            LocationTracker.stop(ctx)
            return
        }
        val t = now()
        if (!force && !ReportSchedule.shouldSend(lastSent(), t, loc.latitude, loc.longitude, everySeconds)) return
        val report = LocationReport(
            lat = loc.latitude, lon = loc.longitude,
            accuracyM = if (loc.hasAccuracy()) loc.accuracy.toDouble() else null,
            at = (if (loc.time > 0) loc.time else System.currentTimeMillis()) / 1000.0,
            speedMps = if (loc.hasSpeed()) loc.speed.toDouble() else null,
            source = source ?: "background",
        )
        try {
            val reply = hubCall { api.reportLocation(report) }
            saveLast(t, loc.latitude, loc.longitude)
            if (reply.everySeconds > 0 && reply.everySeconds != everySeconds) {
                everySeconds = reply.everySeconds
                LocationTracker.start(ctx, everySeconds)
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            when {
                // The hub says this phone isn't sharing (switched off elsewhere): stop.
                e is HubException && (e.code == 403 || e.code == 404) -> apply(false, everySeconds)
                else -> Log.i(TAG, "Location report not sent: ${ApiErrors.message(e)}")
            }
        }
    }

    private fun now() = System.currentTimeMillis() / 1000.0

    companion object {
        private const val TAG = "NewmanLocation"
        private const val KEY_SHARING = "sharing"
        private const val KEY_EVERY = "every_seconds"
        private const val KEY_LOCATE_AT = "locate_answered_at_ms"
        private const val OLD_KEY_LIVE_AT = "live_reply_at_ms"
        private const val OLD_KEY_TRACKING_LIVE = "tracking_live"
        private const val KEY_LAST_AT = "last_at_ms"
        private const val KEY_LAST_LAT = "last_lat"
        private const val KEY_LAST_LON = "last_lon"
    }
}
