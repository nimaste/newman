package ca.nimaste.newman.ui.common

import android.graphics.BitmapFactory
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.net.HubHttp
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.theme.T
import coil3.compose.AsyncImage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl
import okhttp3.Request

/** The last frame of every camera, so a screen shows something the moment it opens. */
private val lastFrames = LruCache<String, ImageBitmap>(12)

/**
 * A camera's current picture (GET still.jpg?h=<px>), refreshed about every [periodMs] while the
 * screen is showing. The previous frame stays up while the next one loads, so it never flickers.
 */
@Composable
fun rememberStill(entity: Entity?, heightPx: Int, periodMs: Long = 2_000, refresh: Boolean = true): ImageBitmap? {
    val app = LocalApp.current
    val server by app.store.changes.collectAsState()
    val url: HttpUrl? = remember(entity?.still, server.serverUrl, heightPx) {
        entity?.still?.let { HubHttp.absolute(server.serverUrl, it)?.newBuilder()?.setQueryParameter("h", heightPx.toString())?.build() }
    }
    val key = entity?.id ?: ""
    var frame by remember(key) { mutableStateOf(lastFrames.get(key)) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(url, refresh) {
        if (url == null || !refresh) return@LaunchedEffect
        lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            while (true) {
                val next = withContext(Dispatchers.IO) {
                    try {
                        app.http.newCall(Request.Builder().url(url).build()).execute().use { r ->
                            if (!r.isSuccessful) null
                            else r.body?.bytes()?.let { BitmapFactory.decodeByteArray(it, 0, it.size)?.asImageBitmap() }
                        }
                    } catch (e: Exception) {
                        null
                    }
                }
                if (next != null) {
                    frame = next
                    lastFrames.put(key, next)
                }
                delay(periodMs)
            }
        }
    }
    return frame
}

/** A refreshing camera picture filling its box, with a dark placeholder until the first frame. */
@Composable
fun CameraStill(entity: Entity?, modifier: Modifier = Modifier, heightPx: Int = 480, refresh: Boolean = true,
                contentScale: ContentScale = ContentScale.Crop, overlay: @Composable BoxScope.() -> Unit = {}) {
    val frame = rememberStill(entity, heightPx, refresh = refresh)
    Box(modifier.background(Brush.linearGradient(listOf(Color(0xFF26314F), Color(0xFF121A33))))) {
        if (frame != null) {
            Image(frame, entity?.name, Modifier.fillMaxSize(), contentScale = contentScale)
        } else {
            Text(
                if (entity == null) "No camera" else if (!entity.available) "${entity.name} is offline" else "Loading ${entity.name}…",
                style = T.small.copy(color = Color(0xFF6F7CA3)), modifier = Modifier.align(Alignment.Center),
            )
        }
        overlay()
    }
}

/** A picture the hub serves (alert thumbnails, snapshots): a relative path or an absolute URL. */
@Composable
fun HubImage(path: String?, modifier: Modifier = Modifier, contentScale: ContentScale = ContentScale.Crop) {
    val app = LocalApp.current
    val server by app.store.changes.collectAsState()
    val url = remember(path, server.serverUrl) { path?.let { HubHttp.absolute(server.serverUrl, it)?.toString() } }
    Box(modifier.background(Brush.linearGradient(listOf(Color(0xFF2B3758), Color(0xFF161E38))))) {
        if (url != null) AsyncImage(model = url, contentDescription = null, modifier = Modifier.fillMaxSize(), contentScale = contentScale)
    }
}
