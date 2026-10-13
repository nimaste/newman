package ca.nimaste.newman.wall

import android.app.admin.DevicePolicyManager
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.OpenableColumns
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import ca.nimaste.newman.core.mtls.MTLSStore
import ca.nimaste.newman.core.mtls.P12Validator
import ca.nimaste.newman.core.net.ServerAddress
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.core.wall.CheckInState
import ca.nimaste.newman.core.wall.ComeBack
import ca.nimaste.newman.core.wall.OverlayGrant
import ca.nimaste.newman.core.wall.WallBrightness
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.DateFormat
import java.util.Date
import kotlin.math.roundToInt

/**
 * The tablet's settings (reached by holding the top-left corner for 3 s, then the tablet's
 * PIN / pattern / biometrics): dimming, brightness, reload and kiosk controls, and a way to the
 * hub connection (ConnectActivity: server, certificate, Cloudflare token).
 */
class WallSettingsActivity : ComponentActivity() {

    companion object {
        const val EXTRA_ACTION = "action"
        const val ACTION_RELOAD = "reload"
        const val ACTION_EXIT_KIOSK = "exit_kiosk"
        const val ACTION_RELEASE_OWNER = "release_owner"
        const val ACTION_CLEAR_WEB = "clear_web"
    }

    /** "Display over other apps" (needed to come back, and to keep the screen on, unless device owner); checked on every resume. */
    private val overlayAllowed = mutableStateOf(false)
    /** The adb command, shown when no settings screen on this tablet opens for it. */
    private val overlayAdb = mutableStateOf<String?>(null)
    private val main = Handler(Looper.getMainLooper())
    private var overlayStep: OverlayGrant.Step? = null

    /** The battery check-in (BatteryAlarm) and whether it may ignore battery optimisation; on every resume. */
    private val checkIn = mutableStateOf<CheckInState?>(null)
    /** The adb command for the battery-optimisation allow-list, shown when no settings screen opens for it. */
    private val dozeAdb = mutableStateOf<String?>(null)

    override fun onResume() {
        super.onResume()
        overlayAllowed.value = Settings.canDrawOverlays(this)
        if (overlayAllowed.value) overlayAdb.value = null
        if (!BatteryAlarm.state(this).armed) BatteryAlarm.arm(this) // (lost somehow: set it again)
        checkIn.value = BatteryAlarm.state(this)
        if (checkIn.value?.dozeExempt == true) dozeAdb.value = null
    }

    /**
     * Optional: let the check-ins ignore battery optimisation (Android asks to confirm). Fire OS 8 has
     * the per-app request screen; else the list of apps; else the adb command.
     */
    private fun askDozeExempt() {
        val tries = listOf(
            Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")),
            Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS),
        )
        for (intent in tries) {
            if (intent.resolveActivity(packageManager) == null) continue
            try {
                startActivity(intent)
                return
            } catch (e: Exception) {
                // try the next one
            }
        }
        dozeAdb.value = "adb shell dumpsys deviceidle whitelist +$packageName"
    }

    override fun onDestroy() {
        main.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    private fun openOverlaySettings() = tryOverlayStep(OverlayGrant.steps.first())

    /**
     * Fire OS may lack the per-app overlay screen or ignore the package: try the app's own overlay
     * screen, the overlay list, the app's details, then show the adb command (OverlayGrant).
     */
    private fun tryOverlayStep(step: OverlayGrant.Step) {
        main.removeCallbacks(overlayCheck)
        val own = Uri.parse("package:$packageName")
        val intent = when (step) {
            OverlayGrant.Step.OWN_OVERLAY_SCREEN -> Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, own)
            OverlayGrant.Step.OVERLAY_LIST -> Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION)
            OverlayGrant.Step.APP_DETAILS -> Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, own)
            OverlayGrant.Step.ADB_COMMAND -> null
        }
        if (intent == null) {
            overlayAdb.value = OverlayGrant.adbCommand(packageName)
            return
        }
        val started = intent.resolveActivity(packageManager) != null && try {
            startActivity(intent)
            true
        } catch (e: Exception) {
            false
        }
        if (!started) {
            tryOverlayStep(OverlayGrant.next(step))
            return
        }
        // A screen that "opens" but closes at once (or never shows) leaves us in front: try the next one.
        overlayStep = step
        main.postDelayed(overlayCheck, 1_500)
    }

    private val overlayCheck = Runnable {
        val step = overlayStep ?: return@Runnable
        overlayStep = null
        if (lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED) && !Settings.canDrawOverlays(this)) {
            tryOverlayStep(OverlayGrant.next(step))
        }
    }

    private fun finishWith(action: String?) {
        setResult(RESULT_OK, Intent().apply { if (action != null) putExtra(EXTRA_ACTION, action) })
        finish()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val settings = WallSettings(this)
        val connection = ConnectionStore.get(this)
        val owner = getSystemService(DevicePolicyManager::class.java)?.isDeviceOwnerApp(packageName) == true
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(background = Night, surface = Color(0xFF151B36), primary = Color(0xFF7FB8FF))) {
                Screen(settings, connection, owner)
            }
        }
    }

    @Composable
    private fun Screen(settings: WallSettings, connection: ConnectionStore, owner: Boolean) {
        val scope = rememberCoroutineScope()
        val conn by connection.changes.collectAsState()
        val cert by MTLSStore.info.collectAsState()
        var dimMinutes by remember { mutableFloatStateOf(settings.dimMinutes.toFloat()) }
        var systemBrightness by remember { mutableStateOf(settings.brightLevel < 0f) }
        var bright by remember { mutableFloatStateOf(if (settings.brightLevel < 0f) 0.8f else settings.brightLevel) }
        var dimLevel by remember { mutableFloatStateOf(settings.dimLevel.coerceIn(WallBrightness.FLOOR, WallBrightness.MAX_DIM)) }
        var pin by remember { mutableStateOf(settings.pinScreen) }
        var returnAfter by remember { mutableStateOf(settings.returnAfterAssistant) }
        var comeBackAfter by remember { mutableStateOf(settings.comeBackAfterLeaving) }
        var comeBackMinutes by remember { mutableStateOf(settings.comeBackMinutes) }
        var comeBackOnWake by remember { mutableStateOf(settings.comeBackOnWake) }
        val overlay by overlayAllowed
        var message by remember { mutableStateOf<String?>(null) }
        var confirmRelease by remember { mutableStateOf(false) }

        fun save(): Boolean {
            settings.dimMinutes = dimMinutes.roundToInt()
            settings.brightLevel = if (systemBrightness) -1f else bright
            settings.dimLevel = dimLevel
            settings.pinScreen = pin
            settings.returnAfterAssistant = returnAfter
            settings.comeBackAfterLeaving = comeBackAfter
            settings.comeBackMinutes = comeBackMinutes
            settings.comeBackOnWake = comeBackOnWake
            return true
        }

        Column(
            Modifier.fillMaxSize().background(Night).safeDrawingPadding().verticalScroll(rememberScrollState()).padding(32.dp),
            verticalArrangement = Arrangement.spacedBy(18.dp),
        ) {
            Text("Newman Wall settings", color = Ink, fontSize = 30.sp, fontWeight = FontWeight.SemiBold)
            Text("Version ${BuildConfig.VERSION_NAME}" + if (owner) " · device owner (kiosk lock on)" else " · not device owner", color = Muted)

            Section("Server") {
                Text(conn.hub.name ?: "Newman", color = Ink)
                Text(conn.serverUrl ?: "Not connected", color = Muted)
                Text(cert?.let { "Client certificate ${it.name} · until ${DateFormat.getDateInstance().format(Date(it.notAfterMillis))}" } ?: "No client certificate", color = Muted)
                if (conn.hasCloudflare) Text("Cloudflare Access token set", color = Muted)
                OutlinedButton(onClick = { startActivity(Intent(this@WallSettingsActivity, ConnectActivity::class.java)) }) { Text("Change server…") }
            }

            Section("Screen") {
                Text(if (dimMinutes.roundToInt() == 0) "Never dim" else "Dim after ${dimMinutes.roundToInt()} min without a touch", color = Ink)
                Slider(dimMinutes, { dimMinutes = it }, valueRange = 0f..60f, steps = 59, modifier = Modifier.widthIn(max = 640.dp))
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Switch(systemBrightness, { systemBrightness = it })
                    Text("Use the tablet's own brightness while awake", color = Ink)
                }
                if (!systemBrightness) {
                    Text("Awake brightness ${(bright * 100).roundToInt()}%", color = Ink)
                    Slider(bright, { bright = it }, valueRange = WallBrightness.FLOOR..1f, modifier = Modifier.widthIn(max = 640.dp))
                }
                Text("Dimmed brightness ${(dimLevel * 100).roundToInt()}% (${(WallBrightness.FLOOR * 100).roundToInt()}% at the lowest, so a dimmed screen still looks on)", color = Ink)
                Slider(dimLevel, { dimLevel = it }, valueRange = WallBrightness.FLOOR..WallBrightness.MAX_DIM, modifier = Modifier.widthIn(max = 640.dp))
            }

            Section("Coming back to Newman") {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Switch(returnAfter, { returnAfter = it })
                    Text("Return to Newman after Alexa or another assistant", color = Ink)
                }
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Switch(comeBackAfter, { comeBackAfter = it })
                    Text("Come back after leaving (Home, Recents, another app)", color = Ink)
                }
                if (comeBackAfter) {
                    Text("After $comeBackMinutes min with no other app playing sound (video, music or Alexa playing holds it off)", color = Muted)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        for (m in ComeBack.MINUTE_CHOICES) {
                            if (m == comeBackMinutes) Button(onClick = {}) { Text("$m min") }
                            else OutlinedButton(onClick = { comeBackMinutes = m }) { Text("$m min") }
                        }
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Switch(comeBackOnWake, { comeBackOnWake = it })
                    Text("Come back when the screen wakes", color = Ink)
                }
                Text("If the screen turns off for any reason, the wall always turns it straight back on, dimmed.", color = Muted)
                // (That return is always on, so the permission it needs always matters.)
                if (!owner && !overlay) {
                    Text("Coming back needs Android's “Display over other apps” for Newman Wall.", color = Coral)
                    OutlinedButton(onClick = { openOverlaySettings() }) { Text("Allow display over other apps") }
                } else if (!owner) {
                    Text("“Display over other apps” is allowed.", color = Muted)
                }
            }

            Section("Battery reports while asleep") {
                val ci = checkIn.value
                val since = BatteryPost.sinceLast(this@WallSettingsActivity)
                if (ci?.armed == true) {
                    val within = ((ci.nextReportInS ?: 0) + 59) / 60
                    Text("With the screen off the wall checks in with Newman every 15 minutes (next within $within min), " +
                        "so Newman shows it as asleep, not offline.", color = Ink)
                } else if (ci != null) {
                    Text("The background check-in couldn't be set: Newman will show this tablet offline while its screen is off.", color = Coral)
                }
                if (since != null) {
                    Text("Last report got through at ${DateFormat.getTimeInstance(DateFormat.SHORT).format(Date(System.currentTimeMillis() - since))}", color = Muted)
                }
                when (ci?.dozeExempt) {
                    true -> Text("Battery optimisation is off for Newman Wall: its check-ins always get through.", color = Muted)
                    false -> {
                        Text("If Newman still shows this tablet offline overnight, let it ignore battery optimisation (Android asks to confirm).", color = Muted)
                        OutlinedButton(onClick = { askDozeExempt() }) { Text("Let it report while asleep") }
                    }
                    null -> {}
                }
            }

            Section("Kiosk") {
                if (!owner) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Switch(pin, { pin = it })
                        Text("Pin the screen whenever the wall opens (Android asks to confirm)", color = Ink)
                    }
                    Text("For a locked kiosk, make the app device owner over USB - see the README.", color = Muted)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    OutlinedButton(onClick = { if (save()) finishWith(ACTION_EXIT_KIOSK) }) { Text("Exit kiosk until restart") }
                    if (owner) OutlinedButton(onClick = { confirmRelease = true }) { Text("Stop being device owner") }
                }
            }

            message?.let { Text(it, color = Teal) }

            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(onClick = { if (save()) finishWith(ACTION_RELOAD) }, colors = ButtonDefaults.buttonColors(containerColor = Ink, contentColor = Night),
                    shape = RoundedCornerShape(14.dp)) { Text("Save and reload") }
                OutlinedButton(onClick = { if (save()) finishWith(null) }) { Text("Save") }
                OutlinedButton(onClick = { if (save()) finishWith(ACTION_CLEAR_WEB) }) { Text("Sign the wall out (clear web data)") }
                TextButton(onClick = { finishWith(null) }) { Text("Cancel", color = Muted) }
            }
        }

        val adb by overlayAdb
        adb?.let { command ->
            AlertDialog(
                onDismissRequest = { overlayAdb.value = null },
                title = { Text("Allow it over USB") },
                text = {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("This tablet has no settings screen for “Display over other apps”. Run once from a computer:")
                        SelectionContainer { Text(command, fontFamily = FontFamily.Monospace) }
                    }
                },
                confirmButton = { TextButton(onClick = { overlayAdb.value = null }) { Text("OK") } },
            )
        }
        dozeAdb.value?.let { command ->
            AlertDialog(
                onDismissRequest = { dozeAdb.value = null },
                title = { Text("Allow it over USB") },
                text = {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("This tablet has no settings screen for battery optimisation. Run once from a computer:")
                        SelectionContainer { Text(command, fontFamily = FontFamily.Monospace) }
                    }
                },
                confirmButton = { TextButton(onClick = { dozeAdb.value = null }) { Text("OK") } },
            )
        }
        if (confirmRelease) {
            AlertDialog(
                onDismissRequest = { confirmRelease = false },
                title = { Text("Stop being device owner?") },
                text = { Text("The wall stops locking the tablet and can then be uninstalled. Making it device owner again needs USB (adb) and no accounts on the tablet.") },
                confirmButton = { TextButton(onClick = { confirmRelease = false; finishWith(ACTION_RELEASE_OWNER) }) { Text("Stop", color = Coral) } },
                dismissButton = { TextButton(onClick = { confirmRelease = false }) { Text("Cancel") } },
            )
        }
    }

    @Composable
    private fun Section(title: String, content: @Composable () -> Unit) {
        Column(
            Modifier.fillMaxWidth().background(Glass, RoundedCornerShape(20.dp)).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(title, color = Ink, fontSize = 18.sp, fontWeight = FontWeight.Bold)
            content()
        }
    }
}

private val Night = Color(0xFF0B1026)
private val Glass = Color(0x12FFFFFF)
private val Ink = Color(0xFFEEF2FF)
private val Muted = Color(0xFFA9B4D6)
private val Teal = Color(0xFF3CCFB4)
private val Coral = Color(0xFFFFB29E)
