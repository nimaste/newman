package ca.nimaste.newman.ui.me

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.net.Uri
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.selection.selectable
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import ca.nimaste.newman.core.people.PersonLook
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.GlassButton
import ca.nimaste.newman.ui.common.GlassCard
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.people.PersonAvatar
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream

/**
 * Your colour and photo, as your family sees you (People tab, maps, the wall): twelve swatches
 * (PUT /api/me/profile) and a photo from Android's photo picker (no storage permission), made square
 * and at most 1024 px here, then uploaded (PUT /api/me/photo; the hub makes it 256 px) - or removed.
 * Hidden until the hub lists this account's person (older hubs, or an account without one).
 */
@Composable
fun MyLookCard(personId: String?) {
    val app = LocalApp.current
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val st by app.people.state.collectAsState()
    var saving by remember { mutableStateOf<String?>(null) }
    var uploading by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { if (st.people.isEmpty()) app.people.refresh() }

    val index = st.people.indexOfFirst { it.id == personId }
    val me = st.people.getOrNull(index) ?: return
    val current = saving ?: PersonLook.normalized(me.color) ?: PersonLook.PRESETS[index % PersonLook.FALLBACK_COUNT]
    val swatches = PersonLook.PRESETS + (if (PersonLook.PRESETS.any { PersonLook.same(it, current) }) emptyList() else listOf(current))

    val pick = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri: Uri? ->
        uri ?: return@rememberLauncherForActivityResult
        uploading = true
        scope.launch {
            val jpeg = withContext(Dispatchers.IO) { runCatching { squareJpeg(ctx, uri) }.getOrNull() }
            val error = if (jpeg == null) "That picture couldn't be read" else app.people.setMyPhoto(jpeg)
            uploading = false
            app.toast(error ?: "Photo saved")
        }
    }

    GlassCard(Modifier.fillMaxWidth(), radius = 20.dp, padding = PaddingValues(14.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            PersonAvatar(me, index, size = 52)
            Column(Modifier.weight(1f)) {
                Text("How your family sees you", style = T.label)
                Text("Your colour and photo on maps and in People", style = T.tiny)
            }
            if (uploading || saving != null) CircularProgressIndicator(Modifier.size(18.dp), color = N.Text, strokeWidth = 2.dp)
        }
        Text("Colour", style = T.tiny, modifier = Modifier.padding(top = 12.dp, bottom = 6.dp))
        swatches.chunked(6).forEach { line ->
            Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                line.forEach { hex ->
                    val on = PersonLook.same(hex, current)
                    Box(
                        Modifier.size(44.dp).clip(CircleShape)
                            .then(if (on) Modifier.border(3.dp, Color.White, CircleShape) else Modifier)
                            .semantics { contentDescription = "Colour $hex" }
                            .selectable(selected = on, enabled = saving == null && !on, role = Role.RadioButton) {
                                saving = hex
                                scope.launch {
                                    app.people.setMyColor(hex)?.let { app.toast(it) }
                                    saving = null
                                }
                            },
                        contentAlignment = Alignment.Center,
                    ) {
                        Box(Modifier.size(34.dp).background(Color((PersonLook.rgb(hex) ?: 0x7FB8FF) or 0xFF000000.toInt()), CircleShape),
                            contentAlignment = Alignment.Center) {
                            if (on) NIcon("check", size = 16.dp, color = N.Night)
                        }
                    }
                }
                repeat(6 - line.size) { Box(Modifier.size(44.dp)) }
            }
        }
        Text("Photo", style = T.tiny, modifier = Modifier.padding(top = 10.dp, bottom = 6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            GlassButton(if (me.photo == null) "Choose photo" else "Change photo", height = 38.dp) {
                if (!uploading) pick.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
            }
            if (me.photo != null) GlassButton("Remove photo", height = 38.dp, foreground = N.CoralText) {
                if (!uploading) {
                    uploading = true
                    scope.launch {
                        app.people.removeMyPhoto()?.let { app.toast(it) }
                        uploading = false
                    }
                }
            }
        }
    }
}

/** The picture's centred square, at most 1024 px, as a JPEG under the hub's 3 MB (orientation applied). */
private fun squareJpeg(ctx: Context, uri: Uri): ByteArray? {
    val full: Bitmap = if (Build.VERSION.SDK_INT >= 28) {
        ImageDecoder.decodeBitmap(ImageDecoder.createSource(ctx.contentResolver, uri)) { decoder, info, _ ->
            // Decode no bigger than needed: the short side about 1024 px.
            val short = minOf(info.size.width, info.size.height)
            if (short > PersonLook.UPLOAD_MAX_PIXELS) {
                val k = PersonLook.UPLOAD_MAX_PIXELS.toDouble() / short
                decoder.setTargetSize((info.size.width * k).toInt().coerceAtLeast(1), (info.size.height * k).toInt().coerceAtLeast(1))
            }
            decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
        }
    } else {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        ctx.contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
        var sample = 1
        while (minOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= PersonLook.UPLOAD_MAX_PIXELS) sample *= 2
        ctx.contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, BitmapFactory.Options().apply { inSampleSize = sample }) }
            ?: return null
    }
    val (x, y, side) = PersonLook.squareCrop(full.width, full.height)
    val target = PersonLook.fitted(side, side).first
    val square = Bitmap.createBitmap(full, x, y, side, side)
    val scaled = if (target != side) Bitmap.createScaledBitmap(square, target, target, true) else square
    var quality = 85
    while (true) {
        val out = ByteArrayOutputStream()
        scaled.compress(Bitmap.CompressFormat.JPEG, quality, out)
        if (out.size() <= PersonLook.UPLOAD_MAX_BYTES || quality <= 35) return out.toByteArray()
        quality -= 20
    }
}
