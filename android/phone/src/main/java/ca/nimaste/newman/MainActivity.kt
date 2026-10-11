package ca.nimaste.newman

import android.content.Intent
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.lifecycleScope
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.data.Account
import ca.nimaste.newman.push.ButtonPress
import ca.nimaste.newman.push.LiveStatusNotifier
import ca.nimaste.newman.push.Notifications
import ca.nimaste.newman.push.PressReports
import ca.nimaste.newman.security.BiometricGate
import ca.nimaste.newman.ui.AppViewModel
import ca.nimaste.newman.ui.NewmanRoot
import ca.nimaste.newman.ui.OpenAlert
import ca.nimaste.newman.ui.theme.NewmanTheme
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

/** FragmentActivity (not ComponentActivity) because BiometricPrompt needs one. */
@AndroidEntryPoint
class MainActivity : FragmentActivity() {

    @Inject lateinit var account: Account
    @Inject lateinit var store: ConnectionStore
    @Inject lateinit var buttonPress: ButtonPress
    @Inject lateinit var pressReports: PressReports
    @Inject lateinit var scope: CoroutineScope

    private val app: AppViewModel by viewModels()

    /** A newman://connect link this app was opened with ("Connect to your Newman"). */
    val connectLink = MutableStateFlow<String?>(null)

    /** "alerts" when opened from a notification; OpenAlert.request(...) for a camera alert's. */
    val openRequest = MutableStateFlow<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        handle(intent)
        setContent {
            NewmanTheme {
                NewmanRoot(app, openRequest, connectLink)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handle(intent)
    }

    private fun handle(intent: Intent?) {
        intent ?: return
        if (intent.action == Intent.ACTION_VIEW && intent.data?.scheme.equals("newman", ignoreCase = true)) {
            connectLink.value = intent.dataString
            intent.data = null // not again on rotation
            return
        }
        val clip = intent.getStringExtra(Notifications.EXTRA_CLIP)
        val review = intent.getStringExtra(Notifications.EXTRA_REVIEW)
        if (!clip.isNullOrEmpty() || !review.isNullOrEmpty()) {
            // A camera alert: its video on the camera screen (over the Alerts tab).
            openRequest.value = OpenAlert.request(clip, review)
            intent.removeExtra(Notifications.EXTRA_CLIP) // not again on rotation
            intent.removeExtra(Notifications.EXTRA_REVIEW)
        } else {
            intent.getStringExtra(Notifications.EXTRA_OPEN)?.let { openRequest.value = it }
        }
        if (intent.action == LiveStatusNotifier.ACTION_LIVE_PRESS) {
            // A sensitive live status button (open, unlock…): always confirmed with the phone's lock first.
            val press = LiveStatusNotifier.Press.from(intent) ?: return
            intent.action = null
            lifecycleScope.launch {
                if (!BiometricGate.confirm(this@MainActivity, "${press.label}?", press.title)) return@launch
                val ok = press.run(applicationContext)
                app.toast(if (ok) "Done: ${press.label}" else "Couldn't ${press.label.lowercase()}", ok)
            }
            return
        }
        if (intent.action == Notifications.ACTION_PRESS) {
            val press = Notifications.Press.from(intent) ?: return
            intent.action = null // don't press again on rotation
            lifecycleScope.launch {
                if (press.sensitive && store.biometricsEnabled &&
                    !BiometricGate.confirm(this@MainActivity, "${press.label}?", press.title)) return@launch
                // Sent from the app's own scope: leaving the app mid-press doesn't cut it short.
                val r = scope.async { buttonPress.run(press, launchState = "foreground", workNote = "sent from the open app") }.await()
                app.toast(if (r.isSuccess) "Done: ${press.label}" else (r.exceptionOrNull()?.message ?: "That didn't work"), r.isSuccess)
            }
        }
    }

    override fun onStart() {
        super.onStart()
        // Press reports the hub didn't get yet (no address answered when the button was pressed).
        scope.launch { pressReports.resend() }
    }
}
