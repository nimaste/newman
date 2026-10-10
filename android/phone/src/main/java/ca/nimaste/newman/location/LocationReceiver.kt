package ca.nimaste.newman.location

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import javax.inject.Inject

/** A location update (PendingIntent from the fused provider or LocationManager): post it if it's time. */
@AndroidEntryPoint
class LocationReceiver : BroadcastReceiver() {
    @Inject lateinit var sharing: LocationSharing
    @Inject lateinit var scope: CoroutineScope

    override fun onReceive(context: Context, intent: Intent) {
        val fix = LocationTracker.fixFrom(intent) ?: return
        val pending = goAsync()
        scope.launch {
            try {
                withTimeoutOrNull(9_000) { sharing.onFix(fix) }
            } finally {
                pending.finish()
            }
        }
    }
}

/** Location updates don't survive a reboot or an app update: start them again when sharing. */
@AndroidEntryPoint
class LocationRestartReceiver : BroadcastReceiver() {
    @Inject lateinit var sharing: LocationSharing

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) sharing.resume()
    }
}
