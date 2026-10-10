package ca.nimaste.newman.wall

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.mtls.MTLSStore
import ca.nimaste.newman.core.mtls.P12Validator
import ca.nimaste.newman.core.net.ConnectCheck
import ca.nimaste.newman.core.net.ConnectLink
import ca.nimaste.newman.core.net.ConnectResult
import ca.nimaste.newman.core.net.HubChoice
import ca.nimaste.newman.core.net.HubDiscovery
import ca.nimaste.newman.core.net.SavedHub
import ca.nimaste.newman.core.net.ServerAddress
import ca.nimaste.newman.core.settings.ConnectionStore
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.DateFormat
import java.util.Date

/**
 * "Connect to your Newman" on the wall tablet, before anyone signs in: hubs found on the network,
 * scanning the hub's QR code, or an address typed in - plus the client certificate and Cloudflare
 * token ("Connection security"). Also opened by a newman://connect link and from the sign-in page.
 */
class ConnectActivity : ComponentActivity() {

    private val link = mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        link.value = intent?.dataString
        val connection = ConnectionStore.get(this)
        val discovery = HubDiscovery(this)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(background = Night, surface = Color(0xFF151B36), primary = Color(0xFF7FB8FF))) {
                Screen(connection, discovery)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        intent.dataString?.let { link.value = it }
    }

    private data class Found(val hub: SavedHub, val url: String)

    @Composable
    private fun Screen(connection: ConnectionStore, discovery: HubDiscovery) {
        val scope = rememberCoroutineScope()
        val conn by connection.changes.collectAsState()
        val cert by MTLSStore.info.collectAsState()
        val hubs by discovery.found.collectAsState()
        val searching by discovery.searching.collectAsState()
        var address by remember { mutableStateOf(conn.hub.outsideUrl ?: conn.hub.localUrl ?: "") }
        var cfId by remember { mutableStateOf(conn.cfClientId.orEmpty()) }
        var cfSecret by remember { mutableStateOf(conn.cfClientSecret.orEmpty()) }
        var security by remember { mutableStateOf(conn.hasCloudflare) }
        var testing by remember { mutableStateOf(false) }
        var found by remember { mutableStateOf<Found?>(null) }
        var error by remember { mutableStateOf<String?>(null) }
        var message by remember { mutableStateOf<String?>(null) }
        var picked by remember { mutableStateOf<Pair<ByteArray, String>?>(null) }
        var p12Password by remember { mutableStateOf("") }
        var p12Error by remember { mutableStateOf<String?>(null) }

        DisposableEffect(Unit) {
            discovery.start()
            onDispose { discovery.stop() }
        }

        fun cloudflare(): Pair<String, String>? =
            if (cfId.isNotBlank() && cfSecret.isNotBlank()) cfId.trim() to cfSecret.trim() else null

        fun run(block: suspend () -> ConnectResult, saved: (ConnectResult.Ok) -> SavedHub) {
            if (testing) return
            if (cfId.isBlank() != cfSecret.isBlank()) {
                error = "Enter both the Cloudflare client id and the secret, or neither"
                security = true
                return
            }
            testing = true
            error = null
            found = null
            scope.launch {
                when (val r = block()) {
                    is ConnectResult.Ok -> found = Found(saved(r), r.url)
                    is ConnectResult.Failed -> error = r.message
                }
                testing = false
            }
        }

        fun testAddress() {
            when (val p = ServerAddress.parse(address)) {
                is ServerAddress.Parsed.Invalid -> error = p.message
                is ServerAddress.Parsed.Ok -> run({ ConnectCheck.test(WallApp.http, p.url.toString(), cloudflare()) }) { HubChoice.saved(it.url, it.hello) }
            }
        }

        fun scanned(text: String) {
            val l = ConnectLink.parse(text)
            if (l == null) {
                if (!text.trim().startsWith("newman:", ignoreCase = true) && ServerAddress.normalize(text) != null) {
                    address = text.trim()
                    testAddress()
                } else error = "That isn't a Newman connect code"
                return
            }
            address = l.outsideUrl ?: l.localUrl.orEmpty()
            run({ ConnectCheck.testHub(WallApp.http, l.localUrl, l.outsideUrl, cloudflare(), l.id) }) { HubChoice.saved(it.url, it.hello, l) }
        }

        val pending by link
        LaunchedEffect(pending) {
            pending?.let {
                link.value = null
                scanned(it)
            }
        }

        val scan = rememberLauncherForActivityResult(ScanContract()) { r -> r.contents?.let { scanned(it) } }
        val pickFile = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
            uri ?: return@rememberLauncherForActivityResult
            scope.launch {
                picked = withContext(Dispatchers.IO) {
                    runCatching {
                        val name = contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
                            if (it.moveToFirst()) it.getString(0) else null
                        } ?: "certificate.p12"
                        (contentResolver.openInputStream(uri)?.use { s -> s.readBytes() } ?: ByteArray(0)) to name
                    }.getOrNull()
                }
                p12Password = ""
                p12Error = null
            }
        }

        Column(
            Modifier.fillMaxSize().background(Night).safeDrawingPadding().verticalScroll(rememberScrollState()).padding(32.dp),
            verticalArrangement = Arrangement.spacedBy(18.dp),
        ) {
            Text("Connect to your Newman", color = Ink, fontSize = 30.sp, fontWeight = FontWeight.SemiBold)
            Text("Pick your hub, scan its QR code, or type its address.", color = Muted)

            Section("On this network") {
                if (hubs.isEmpty()) Text(if (searching) "Looking…" else "No Newman found on this network", color = Muted)
                hubs.forEach { h ->
                    Column(
                        Modifier.fillMaxWidth().widthIn(max = 640.dp).background(Glass, RoundedCornerShape(14.dp))
                            .clickable {
                                address = h.localUrl
                                run({ ConnectCheck.test(WallApp.http, h.localUrl, null, h.id) }) {
                                    HubChoice.saved(it.url, it.hello).let { s -> s.copy(outsideUrl = s.outsideUrl ?: h.outsideUrl) }
                                }
                            }
                            .padding(16.dp),
                    ) {
                        Text(h.name, color = Ink, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
                        Text(h.localUrl, color = Muted)
                    }
                }
                OutlinedButton(onClick = {
                    scan.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE).setPrompt("Scan the Newman QR code")
                        .setBeepEnabled(false).setOrientationLocked(false))
                }) { Text("Scan QR code") }
            }

            Section("Address") {
                OutlinedTextField(address, { address = it; error = null; found = null }, singleLine = true,
                    modifier = Modifier.fillMaxWidth().widthIn(max = 640.dp), label = { Text("e.g. https://home.example.com") })
                TextButton(onClick = { security = !security }) { Text(if (security) "Connection security  ▴" else "Connection security  ▾") }
                if (security) {
                    Text("Client certificate", color = Ink, fontWeight = FontWeight.Bold)
                    Text(cert?.let { "${it.name} · until ${DateFormat.getDateInstance().format(Date(it.notAfterMillis))}" } ?: "None on this tablet", color = Muted)
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        OutlinedButton(onClick = { pickFile.launch(arrayOf("application/x-pkcs12", "application/octet-stream", "*/*")) }) {
                            Text(if (cert == null) "Import .p12" else "Replace")
                        }
                        if (cert != null) OutlinedButton(onClick = { MTLSStore.clearCertificate(); message = "Certificate removed" }) { Text("Remove") }
                    }
                    Text("Cloudflare Access service token", color = Ink, fontWeight = FontWeight.Bold)
                    OutlinedTextField(cfId, { cfId = it; error = null }, singleLine = true, label = { Text("Client id") }, modifier = Modifier.fillMaxWidth().widthIn(max = 640.dp))
                    OutlinedTextField(cfSecret, { cfSecret = it; error = null }, singleLine = true, label = { Text("Client secret") },
                        visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth().widthIn(max = 640.dp))
                }
            }

            message?.let { Text(it, color = Teal) }
            error?.let { Text(it, color = Coral, fontSize = 18.sp) }
            val f = found
            if (f != null) {
                Column(Modifier.fillMaxWidth().widthIn(max = 640.dp).background(TealBg, RoundedCornerShape(14.dp)).padding(16.dp)) {
                    Text("Found ${f.hub.name ?: "Newman"}", color = Ink, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
                    Text(f.url, color = Muted)
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                if (f != null) {
                    Button(onClick = {
                        val cf = cloudflare()
                        connection.setCloudflare(cf?.first, cf?.second)
                        connection.saveHub(f.hub, f.url)
                        setResult(RESULT_OK)
                        finish()
                    }, colors = ButtonDefaults.buttonColors(containerColor = Ink, contentColor = Night), shape = RoundedCornerShape(14.dp)) {
                        Text("Use ${f.hub.name ?: "this Newman"}")
                    }
                } else {
                    Button(onClick = { testAddress() }, enabled = !testing && address.isNotBlank(),
                        colors = ButtonDefaults.buttonColors(containerColor = Ink, contentColor = Night), shape = RoundedCornerShape(14.dp)) {
                        Text(if (testing) "Checking…" else "Test connection")
                    }
                }
                if (conn.serverUrl != null) TextButton(onClick = { finish() }) { Text("Cancel", color = Muted) }
            }
        }

        val current = picked
        if (current != null) {
            AlertDialog(
                onDismissRequest = { picked = null },
                title = { Text("Certificate password") },
                text = {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Password for ${current.second}")
                        OutlinedTextField(p12Password, { p12Password = it; p12Error = null }, singleLine = true,
                            visualTransformation = PasswordVisualTransformation())
                        p12Error?.let { Text(it, color = Coral) }
                    }
                },
                confirmButton = {
                    TextButton(onClick = {
                        when (val r = P12Validator.validate(current.first, p12Password)) {
                            is P12Validator.Result.Invalid -> p12Error = r.message
                            is P12Validator.Result.Valid -> {
                                MTLSStore.setCertificate(current.first, p12Password, r)
                                picked = null
                                message = "Certificate “${r.name}” added"
                            }
                        }
                    }) { Text("Add") }
                },
                dismissButton = { TextButton(onClick = { picked = null }) { Text("Cancel") } },
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
private val TealBg = Color(0x293CCFB4)
private val Coral = Color(0xFFFFB29E)
