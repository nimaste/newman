package ca.nimaste.newman.ui.me

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import ca.nimaste.newman.core.people.SharingSwitch
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.GlassButton
import ca.nimaste.newman.ui.common.GlassCard
import ca.nimaste.newman.ui.common.NSwitch
import ca.nimaste.newman.ui.common.SectionRow
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import kotlinx.coroutines.launch

/**
 * "Share my location" (GET /api/me/location): hidden when the hub doesn't offer it; a switch for
 * "choice"; on and locked ("Required by Chris") for "required". Turning it on asks Android for
 * location in two plain steps - while using the app, then (Android 10+) all the time, which
 * Android 11+ shows as a settings page - each explained first.
 */
@Composable
fun LocationSharingCard() {
    val app = LocalApp.current
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val settings by app.sharing.settings.collectAsState()
    val perms by app.sharing.permissions.collectAsState()
    var step by remember { mutableStateOf(Step.NONE) }
    var busy by remember { mutableStateOf(false) }
    /** Turn sharing on at the hub once Android said yes (a "choice" switch flipped on). */
    var enableAfter by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { app.sharing.refresh() }
    // Back from Android's settings: permissions may have changed.
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val obs = LifecycleEventObserver { _, e -> if (e == Lifecycle.Event.ON_RESUME) app.sharing.permissionsChanged() }
        lifecycle.addObserver(obs)
        onDispose { lifecycle.removeObserver(obs) }
    }

    val sw = SharingSwitch.state(settings)
    if (!sw.visible) return
    val fg = remember(perms) { app.sharing.hasForeground() }
    val bg = remember(perms) { app.sharing.hasBackground() }

    fun finish(foregroundOk: Boolean) {
        app.sharing.permissionsChanged()
        if (enableAfter) {
            enableAfter = false
            if (!foregroundOk) return // the "Location is off for Newman" dialog explains
            busy = true
            scope.launch {
                app.sharing.setEnabled(true).onFailure { app.toast(it.message ?: "Couldn't turn on sharing", ok = false) }
                busy = false
            }
        }
    }

    val askBackground = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        finish(true)
    }
    val askForeground = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { res ->
        val ok = res.values.any { it }
        if (ok && Build.VERSION.SDK_INT >= 29 && !app.sharing.hasBackground()) {
            app.sharing.permissionsChanged()
            step = Step.EXPLAIN_BACKGROUND
        } else if (!ok) {
            step = Step.DENIED
            finish(false)
        } else finish(true)
    }

    fun start() {
        when {
            !app.sharing.hasForeground() -> step = Step.EXPLAIN_FOREGROUND
            Build.VERSION.SDK_INT >= 29 && !app.sharing.hasBackground() -> step = Step.EXPLAIN_BACKGROUND
            else -> finish(true)
        }
    }

    val note = when {
        !sw.on -> sw.note
        !fg -> "${sw.note} · location permission is off"
        !bg -> "${sw.note} · only while Newman is open"
        else -> sw.note
    }

    GlassCard(Modifier.fillMaxWidth(), radius = 20.dp, padding = PaddingValues(horizontal = 14.dp, vertical = 4.dp)) {
        SectionRow("Share my location", note, last = !(sw.on && (!fg || !bg))) {
            NSwitch(sw.on, enabled = !sw.locked && !busy) { on ->
                if (on) {
                    enableAfter = true
                    start()
                } else {
                    busy = true
                    scope.launch {
                        app.sharing.setEnabled(false).onFailure { app.toast(it.message ?: "Couldn't turn off sharing", ok = false) }
                        busy = false
                    }
                }
            }
        }
        if (sw.on && (!fg || !bg)) {
            SectionRow(if (!fg) "Allow location" else "Allow all the time",
                if (!fg) "Needed so your household can see you" else "So sharing keeps working when Newman is closed", last = true) {
                GlassButton("Allow", height = 38.dp) { start() }
            }
        }
    }

    when (step) {
        Step.EXPLAIN_FOREGROUND -> Explain(
            title = if (settings?.mode == "required") "Your household requires location sharing" else "Share your location",
            text = SharingSwitch.permissionReason(settings) + " Android will ask next.",
            onOk = {
                step = Step.NONE
                askForeground.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION))
            },
            onCancel = { step = Step.NONE; enableAfter = false },
        )
        Step.EXPLAIN_BACKGROUND -> Explain(
            title = "Keep sharing in the background",
            text = SharingSwitch.BACKGROUND_REASON + if (Build.VERSION.SDK_INT >= 30) " On the next screen choose \"Allow all the time\"." else "",
            onOk = {
                step = Step.NONE
                if (Build.VERSION.SDK_INT >= 29) askBackground.launch(Manifest.permission.ACCESS_BACKGROUND_LOCATION) else finish(true)
            },
            // Without it, sharing still works while Newman is open.
            onCancel = { step = Step.NONE; finish(true) },
        )
        Step.DENIED -> Explain(
            title = "Location is off for Newman",
            text = "Turn on location for Newman in Android's settings to share where you are.",
            ok = "Open settings",
            onOk = {
                step = Step.NONE
                ctx.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", ctx.packageName, null))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            },
            onCancel = { step = Step.NONE },
        )
        Step.NONE -> Unit
    }
}

private enum class Step { NONE, EXPLAIN_FOREGROUND, EXPLAIN_BACKGROUND, DENIED }

@Composable
private fun Explain(title: String, text: String, ok: String = "Continue", onOk: () -> Unit, onCancel: () -> Unit) {
    AlertDialog(
        onDismissRequest = onCancel,
        containerColor = Color(0xFF151B36),
        title = { Text(title, style = T.title) },
        text = { Text(text, style = T.body.copy(color = N.Muted)) },
        confirmButton = { TextButton(onClick = onOk) { Text(ok, style = T.label) } },
        dismissButton = { TextButton(onClick = onCancel) { Text("Not now", style = T.label.copy(color = N.Muted)) } },
    )
}
