package ca.nimaste.newman.location

import android.Manifest
import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.CancellationSignal
import android.util.Log
import androidx.core.content.ContextCompat
import androidx.core.content.IntentCompat
import androidx.core.location.LocationManagerCompat
import ca.nimaste.newman.core.people.ReportSchedule
import com.google.android.gms.common.ConnectionResult
import com.google.android.gms.common.GoogleApiAvailability
import com.google.android.gms.location.CurrentLocationRequest
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import com.google.android.gms.tasks.CancellationTokenSource
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.resume

/**
 * Where this phone is, as cheaply as possible. Updates arrive as a PendingIntent broadcast
 * ([LocationReceiver]) so nothing has to keep running - no foreground service, no notification.
 *
 *  - Background: the fused provider at "balanced" power (Wi-Fi and cell towers, about block-level)
 *    every few minutes (~5) or on movement; Android batches and throttles these further when Newman
 *    is closed. There is no live mode.
 *  - Tap-to-refresh (someone tapped this person): ONE high-accuracy fix ([currentFix]), then nothing.
 *  - Without Google Play services: LocationManager, network provider (or passive) in the
 *    background, GPS for a one-shot precise fix.
 */
object LocationTracker {
    private const val TAG = "NewmanLocation"
    const val ACTION_FIX = "ca.nimaste.newman.LOCATION_FIX"
    private const val REQUEST_CODE = 0x10c

    fun hasForeground(ctx: Context): Boolean =
        granted(ctx, Manifest.permission.ACCESS_FINE_LOCATION) || granted(ctx, Manifest.permission.ACCESS_COARSE_LOCATION)

    /** Android 10+ asks separately for "all the time"; before that, foreground permission covers it. */
    fun hasBackground(ctx: Context): Boolean =
        hasForeground(ctx) && (Build.VERSION.SDK_INT < 29 || granted(ctx, Manifest.permission.ACCESS_BACKGROUND_LOCATION))

    private fun granted(ctx: Context, p: String) = ContextCompat.checkSelfPermission(ctx, p) == PackageManager.PERMISSION_GRANTED

    fun hasPlayServices(ctx: Context): Boolean = try {
        GoogleApiAvailability.getInstance().isGooglePlayServicesAvailable(ctx) == ConnectionResult.SUCCESS
    } catch (e: Exception) {
        false
    }

    private fun pendingIntent(ctx: Context): PendingIntent =
        PendingIntent.getBroadcast(ctx, REQUEST_CODE, Intent(ctx, LocationReceiver::class.java).setAction(ACTION_FIX),
            // Mutable: the location services add the fix to it.
            PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0))

    /** (Re)start the cheap background updates. False when there's no permission. */
    @SuppressLint("MissingPermission")
    fun start(ctx: Context, everySeconds: Int): Boolean {
        if (!hasForeground(ctx)) return false
        val interval = ReportSchedule.intervalSeconds(everySeconds) * 1000L
        return try {
            if (hasPlayServices(ctx)) {
                val req = LocationRequest.Builder(Priority.PRIORITY_BALANCED_POWER_ACCURACY, interval)
                    .setMinUpdateIntervalMillis(ReportSchedule.MIN_BACKGROUND_SECONDS * 1000L)
                    .setMinUpdateDistanceMeters(0f) // standing still still gets fixes
                    .build()
                LocationServices.getFusedLocationProviderClient(ctx).requestLocationUpdates(req, pendingIntent(ctx))
            } else {
                val lm = ctx.getSystemService(LocationManager::class.java) ?: return false
                lm.removeUpdates(pendingIntent(ctx))
                val provider = provider(lm, precise = false)
                lm.requestLocationUpdates(provider, interval, 50f, pendingIntent(ctx))
            }
            true
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't start location updates: $e")
            false
        }
    }

    fun stop(ctx: Context) {
        try {
            if (hasPlayServices(ctx)) LocationServices.getFusedLocationProviderClient(ctx).removeLocationUpdates(pendingIntent(ctx))
            ctx.getSystemService(LocationManager::class.java)?.removeUpdates(pendingIntent(ctx))
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't stop location updates: $e")
        }
    }

    private fun provider(lm: LocationManager, precise: Boolean): String = when {
        precise && lm.isProviderEnabled(LocationManager.GPS_PROVIDER) -> LocationManager.GPS_PROVIDER
        lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER) -> LocationManager.NETWORK_PROVIDER
        else -> LocationManager.PASSIVE_PROVIDER
    }

    /** The newest fix in a [LocationReceiver] broadcast (fused or LocationManager). */
    fun fixFrom(intent: Intent): Location? {
        if (LocationResult.hasResult(intent)) return LocationResult.extractResult(intent)?.lastLocation
        return IntentCompat.getParcelableExtra(intent, LocationManager.KEY_LOCATION_CHANGED, Location::class.java)
    }

    /** One fix now (the app opened, or the hub asked for one), within [timeoutMs]. */
    @SuppressLint("MissingPermission")
    suspend fun currentFix(ctx: Context, precise: Boolean, timeoutMs: Long = 20_000, maxAgeMs: Long? = null): Location? {
        if (!hasForeground(ctx)) return null
        return withTimeoutOrNull(timeoutMs) {
            try {
                if (hasPlayServices(ctx)) {
                    val cts = CancellationTokenSource()
                    val req = CurrentLocationRequest.Builder()
                        .setPriority(if (precise) Priority.PRIORITY_HIGH_ACCURACY else Priority.PRIORITY_BALANCED_POWER_ACCURACY)
                        .setMaxUpdateAgeMillis(maxAgeMs ?: if (precise) 10_000L else 60_000L)
                        .build()
                    suspendCancellableCoroutine { cont ->
                        LocationServices.getFusedLocationProviderClient(ctx).getCurrentLocation(req, cts.token)
                            .addOnCompleteListener { t -> if (cont.isActive) cont.resume(if (t.isSuccessful) t.result else null) }
                        cont.invokeOnCancellation { cts.cancel() }
                    }
                } else {
                    val lm = ctx.getSystemService(LocationManager::class.java) ?: return@withTimeoutOrNull null
                    val provider = provider(lm, precise)
                    if (provider == LocationManager.PASSIVE_PROVIDER) return@withTimeoutOrNull lm.getLastKnownLocation(provider)
                    val signal = CancellationSignal()
                    suspendCancellableCoroutine { cont ->
                        LocationManagerCompat.getCurrentLocation(lm, provider, signal, ContextCompat.getMainExecutor(ctx)) { loc ->
                            if (cont.isActive) cont.resume(loc)
                        }
                        cont.invokeOnCancellation { signal.cancel() }
                    }
                }
            } catch (e: Exception) {
                Log.w(TAG, "No current fix: $e")
                null
            }
        }
    }
}
