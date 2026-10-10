package ca.nimaste.newman.widgets

import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.lifecycleScope
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.core.widgets.WidgetTiles
import ca.nimaste.newman.security.BiometricGate
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

/**
 * A launcher shortcut's tap (the app icon's shortcuts, or one added to the home screen from a device's
 * sheet): runs what a tap on that device does now, without opening the app - a see-through activity
 * that finishes at once. A sensitive action (unlock, open, disarm) needs the phone unlocked first
 * (KeyguardManager), then "Confirm it's you" when that's on in Me (BiometricGate), as in the app.
 */
class ShortcutActivity : FragmentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val id = intent?.getStringExtra(EXTRA_DEVICE)
        if (id.isNullOrEmpty() || savedInstanceState != null) return finish()
        lifecycleScope.launch {
            run(id)
            finish()
        }
    }

    private suspend fun run(id: String) {
        val sync = WidgetSync.from(this)
        if (!sync.signedIn) return say("Sign in to Newman first")
        val reached = sync.refresh(listOf(id)) // what a tap does now, not when the shortcut was made
        val d = sync.cached(id) ?: return say(if (reached) "Newman can't run that device from here any more" else "Can't reach Newman right now")
        val action = WidgetTiles.tapAction(d) ?: return say("${d.name}: ${WidgetTiles.stateText(d)}")
        if (action.sensitive) {
            val keyguard = getSystemService(KeyguardManager::class.java)
            if (keyguard?.isKeyguardLocked == true) {
                if (!dismissKeyguard(keyguard)) return
            } else if (ConnectionStore.get(this).biometricsEnabled &&
                !BiometricGate.confirm(this, "${action.label} ${d.name}?", "Confirm it's you")) return
        }
        runCatching { ShortcutManagerCompat.reportShortcutUsed(this, LauncherShortcuts.shortcutId(id)) }
        say("${d.name}: ${action.label}")
        val app = applicationContext
        sync.scope.launch {
            if (!sync.run(id, action)) post(app, "Couldn't ${action.label.lowercase()} ${d.name}")
        }
    }

    /** Asks for the PIN / fingerprint of the lock screen; true once the phone is unlocked. */
    private suspend fun dismissKeyguard(keyguard: KeyguardManager): Boolean {
        if (Build.VERSION.SDK_INT < 26) return false
        return suspendCancellableCoroutine { cont ->
            keyguard.requestDismissKeyguard(this, object : KeyguardManager.KeyguardDismissCallback() {
                override fun onDismissSucceeded() { if (cont.isActive) cont.resume(true) }
                override fun onDismissCancelled() { if (cont.isActive) cont.resume(false) }
                override fun onDismissError() { if (cont.isActive) cont.resume(false) }
            })
        }
    }

    private fun say(text: String) = post(applicationContext, text)

    companion object {
        private const val EXTRA_DEVICE = "device"
        const val ACTION = "ca.nimaste.newman.RUN_DEVICE"

        fun intent(context: Context, deviceId: String): Intent =
            Intent(ACTION, Uri.parse("newman-shortcut://device/${Uri.encode(deviceId)}"), context, ShortcutActivity::class.java)
                .putExtra(EXTRA_DEVICE, deviceId)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS)

        private fun post(context: Context, text: String) {
            Handler(Looper.getMainLooper()).post { Toast.makeText(context, text, Toast.LENGTH_SHORT).show() }
        }
    }
}
