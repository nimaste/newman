package ca.nimaste.newman.ui.common

import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import ca.nimaste.newman.core.mtls.MTLSStore
import ca.nimaste.newman.core.mtls.P12Validator
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.DateFormat
import java.util.Date

/**
 * Pick a .p12 file, ask for its password, check it and store it. Returns a function that starts
 * the flow; [onDone] gets a message for the person once the certificate is stored.
 */
@Composable
fun rememberCertificateImport(onDone: (String) -> Unit): () -> Unit {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var picked by remember { mutableStateOf<Pair<ByteArray, String>?>(null) }
    var password by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }

    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
        uri ?: return@rememberLauncherForActivityResult
        scope.launch {
            val read = withContext(Dispatchers.IO) {
                runCatching {
                    val name = context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
                        if (it.moveToFirst()) it.getString(0) else null
                    } ?: "certificate.p12"
                    val bytes = context.contentResolver.openInputStream(uri)?.use { s -> s.readBytes() } ?: ByteArray(0)
                    bytes to name
                }.getOrNull()
            }
            if (read == null || read.first.size > 256 * 1024) {
                onDone("That file couldn't be read")
            } else {
                password = ""
                error = null
                picked = read
            }
        }
    }

    val current = picked
    if (current != null) {
        AlertDialog(
            onDismissRequest = { if (!busy) picked = null },
            containerColor = androidx.compose.ui.graphics.Color(0xFF151B36),
            title = { Text("Certificate password", style = T.title) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("Enter the password for ${current.second}.", style = T.body.copy(color = N.Muted))
                    NTextField(password, { password = it; error = null }, "Password", password = true)
                    if (error != null) Text(error!!, style = T.small.copy(color = N.CoralText))
                }
            },
            confirmButton = {
                TextButton(enabled = !busy, onClick = {
                    busy = true
                    scope.launch {
                        val result = withContext(Dispatchers.Default) { P12Validator.validate(current.first, password) }
                        busy = false
                        when (result) {
                            is P12Validator.Result.Invalid -> error = result.message
                            is P12Validator.Result.Valid -> {
                                withContext(Dispatchers.IO) { MTLSStore.setCertificate(current.first, password, result) }
                                picked = null
                                onDone("Certificate “${result.name}” added (valid until ${DateFormat.getDateInstance().format(Date(result.notAfterMillis))})")
                            }
                        }
                    }
                }) { Text(if (busy) "Checking…" else "Add", style = T.label) }
            },
            dismissButton = {
                TextButton(enabled = !busy, onClick = { picked = null }) { Text("Cancel", style = T.label.copy(color = N.Muted)) }
            },
        )
    }

    return {
        launcher.launch(arrayOf("application/x-pkcs12", "application/pkcs12", "application/x-pkcs12-certificates", "application/octet-stream", "*/*"))
    }
}

/** "Chris phone · until May 3, 2027" for the installed certificate, or null. */
fun certificateSummary(info: MTLSStore.Info?): String? = info?.let {
    if (it.notAfterMillis > 0) "${it.name} · until ${DateFormat.getDateInstance().format(Date(it.notAfterMillis))}" else it.name
}
