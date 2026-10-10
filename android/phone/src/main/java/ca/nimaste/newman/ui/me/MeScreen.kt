package ca.nimaste.newman.ui.me

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.BuildConfig
import ca.nimaste.newman.core.mtls.MTLSStore
import ca.nimaste.newman.push.Notifications
import ca.nimaste.newman.security.BiometricGate
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.BackPill
import ca.nimaste.newman.ui.common.GlassButton
import ca.nimaste.newman.ui.common.GlassCard
import ca.nimaste.newman.ui.common.NSwitch
import ca.nimaste.newman.ui.common.NTextField
import ca.nimaste.newman.ui.common.SectionRow
import ca.nimaste.newman.ui.common.heading
import androidx.compose.ui.semantics.Role
import ca.nimaste.newman.ui.common.certificateSummary
import ca.nimaste.newman.ui.common.rememberCertificateImport
import ca.nimaste.newman.ui.people.PersonAvatar
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.NightBackground
import ca.nimaste.newman.ui.theme.T
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/** The account screen (PhoneMe board): who, where from, the certificate, biometrics, sign out. */
@Composable
fun MeScreen(onBack: () -> Unit, onServer: () -> Unit = {}) {
    val app = LocalApp.current
    val context = LocalContext.current
    val state by app.state.collectAsState()
    val cert by MTLSStore.info.collectAsState()
    val conn by app.store.changes.collectAsState()
    val bio by app.biometrics.collectAsState()
    val people by app.people.state.collectAsState()
    var confirmRemove by remember { mutableStateOf(false) }
    var confirmSignOut by remember { mutableStateOf(false) }
    var editCf by remember { mutableStateOf(false) }
    val importCert = rememberCertificateImport { msg -> app.toast(msg) }
    LaunchedEffect(Unit) { app.hub.reloadAuth() }

    val auth = state.auth
    val user = auth?.user
    val name = user?.name ?: conn.userName ?: "You"
    val where = when {
        auth == null -> "Connecting…"
        auth.where == "home" -> "at home"
        auth.certificate -> "connected securely from outside"
        else -> "connected from outside"
    }

    NightBackground(Modifier.fillMaxSize(), teal = false) {
        Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
            Row(Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) { BackPill(onClick = onBack) }
            Column(
                Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp).padding(bottom = 24.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Row(Modifier.padding(4.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    val meIndex = people.people.indexOfFirst { it.id == user?.personId }
                    if (meIndex >= 0) PersonAvatar(people.people[meIndex], meIndex, size = 60)
                    else Box(Modifier.size(60.dp).background(N.Blue, CircleShape), contentAlignment = Alignment.Center) {
                        Text(name.take(1).uppercase(), style = T.title.copy(color = N.Night, fontSize = 24.sp))
                    }
                    Column {
                        Text(name, style = T.title.copy(fontSize = 24.sp), modifier = Modifier.heading())
                        val role = user?.role?.replaceFirstChar { it.uppercase() }
                        Text(listOfNotNull(role, where).joinToString(" · ").replaceFirstChar { it.uppercase() }, style = T.small.copy(color = N.Muted))
                    }
                }

                GlassCard(Modifier.fillMaxWidth(), radius = 20.dp, padding = PaddingValues(horizontal = 14.dp, vertical = 4.dp)) {
                    SectionRow(
                        "Confirm before unlocking, opening or disarming",
                        if (BiometricGate.available(context)) "Fingerprint, face or screen lock on this phone" else "This phone has no screen lock set up",
                        last = true,
                    ) { NSwitch(bio, label = "Confirm before unlocking, opening or disarming") { app.setBiometrics(it) } }
                }

                MyLookCard(user?.personId)

                LocationSharingCard()

                GlassCard(Modifier.fillMaxWidth(), radius = 20.dp, padding = PaddingValues(horizontal = 14.dp, vertical = 4.dp)) {
                    // The Connect step: new addresses for this hub, or another hub (signs out of this one first).
                    SectionRow("Server", serverText(conn.hub.name, conn.serverUrl), onClick = onServer) {
                        Text("Change", style = T.small.copy(color = N.Muted))
                    }
                    SectionRow("Certificate", certificateSummary(cert) ?: "None on this phone (only needed away from home)") {
                        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            GlassButton(if (cert == null) "Import" else "Replace", height = 38.dp) { importCert() }
                            if (cert != null) GlassButton("Remove", height = 38.dp, foreground = N.CoralText) { confirmRemove = true }
                        }
                    }
                    SectionRow("Cloudflare Access", if (conn.hasCloudflare) "Service token set" else "Not used", onClick = { editCf = true }) {
                        Text(if (conn.hasCloudflare) "Change" else "Set", style = T.small.copy(color = N.Muted))
                    }
                    SectionRow("Notifications", when {
                        !BuildConfig.FCM_ENABLED -> "Not set up in this build (no google-services.json)"
                        !app.account.pushAvailable -> "Firebase isn't available on this phone"
                        !Notifications.canPost(context) -> "Turned off for Newman in Android settings"
                        else -> "On for this phone"
                    })
                    SectionRow("Version", "${BuildConfig.VERSION_NAME} · hub ${auth?.version ?: state.meta?.version ?: "?"}", last = true)
                }

                Box(
                    Modifier.fillMaxWidth().height(52.dp).clip(RoundedCornerShape(16.dp)).background(N.CoralCard)
                        .border(1.dp, Color(0x66FF7A59), RoundedCornerShape(16.dp)).clickable(role = Role.Button) { confirmSignOut = true },
                    contentAlignment = Alignment.Center,
                ) { Text("Sign out of this phone", style = T.label.copy(color = N.CoralText)) }
                Text("Signing out keeps the server address and the certificate on this phone.", style = T.tiny)
            }
        }
    }

    if (confirmRemove) {
        Confirm("Remove the certificate?", "Away from home, Newman may not let this phone in without it.", "Remove",
            onYes = { MTLSStore.clearCertificate(); app.toast("Certificate removed"); confirmRemove = false }, onNo = { confirmRemove = false })
    }
    if (confirmSignOut) {
        Confirm("Sign out of this phone?", "You'll stop getting Newman's notifications here.", "Sign out",
            onYes = { confirmSignOut = false; app.signOut() }, onNo = { confirmSignOut = false })
    }
    if (editCf) {
        var id by remember { mutableStateOf(conn.cfClientId.orEmpty()) }
        var secret by remember { mutableStateOf(conn.cfClientSecret.orEmpty()) }
        AlertDialog(
            onDismissRequest = { editCf = false },
            containerColor = Color(0xFF151B36),
            title = { Text("Cloudflare Access", style = T.title) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("Sent with every request when your Newman address is behind Cloudflare Access.", style = T.body.copy(color = N.Muted))
                    NTextField(id, { id = it }, "Client id")
                    NTextField(secret, { secret = it }, "Client secret", password = true)
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    app.store.setCloudflare(id, secret)
                    app.account.connectionChanged()
                    editCf = false
                }) { Text("Save", style = T.label) }
            },
            dismissButton = {
                TextButton(onClick = {
                    app.store.setCloudflare(null, null)
                    app.account.connectionChanged()
                    editCf = false
                }) { Text("Remove", style = T.label.copy(color = N.CoralText)) }
            },
        )
    }
}

/** "Home · 10.1.1.2:8093" - the hub's name and the address in use. */
private fun serverText(name: String?, url: String?): String {
    val addr = url?.toHttpUrlOrNull()?.let { u -> u.host + (if (u.port != 443 && u.port != 80) ":${u.port}" else "") }
    return listOfNotNull(name?.trim()?.ifEmpty { null }, addr).joinToString(" · ").ifEmpty { "Not set" }
}

@Composable
private fun Confirm(title: String, text: String, yes: String, onYes: () -> Unit, onNo: () -> Unit) {
    AlertDialog(
        onDismissRequest = onNo,
        containerColor = Color(0xFF151B36),
        title = { Text(title, style = T.title) },
        text = { Text(text, style = T.body.copy(color = N.Muted)) },
        confirmButton = { TextButton(onClick = onYes) { Text(yes, style = T.label.copy(color = N.CoralText)) } },
        dismissButton = { TextButton(onClick = onNo) { Text("Cancel", style = T.label) } },
    )
}
