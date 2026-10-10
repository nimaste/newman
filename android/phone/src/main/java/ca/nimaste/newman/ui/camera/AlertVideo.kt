package ca.nimaste.newman.ui.camera

import android.content.Context
import android.net.Uri
import androidx.annotation.OptIn
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.ui.PlayerView
import ca.nimaste.newman.core.model.CameraAlert
import ca.nimaste.newman.core.model.CameraClip
import ca.nimaste.newman.core.net.HubHttp
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.HubImage
import ca.nimaste.newman.ui.common.Pill
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.util.concurrent.TimeUnit

private sealed interface ClipState {
    data object Loading : ClipState
    data object Failed : ClipState
    /** A finished clip: streamed from the hub with byte ranges (starts at once, seeks). */
    data class Stream(val url: HttpUrl) : ClipState
    /** A still-recording clip (the hub has no ranges for it yet): downloaded whole, played from the file. */
    data class Downloaded(val file: File) : ClipState
}

/**
 * A camera alert's video (GET api/clips/<integration>/<clip id>, CameraClip.clipPath), played with
 * ExoPlayer and its controls through the app's hub client (client certificate, Cloudflare headers,
 * session token). A first two-byte request ([CameraClip.PROBE_RANGE]) decides: a finished clip
 * answers 206 and is streamed, seeking with byte ranges; a still-recording one ignores the range and
 * sends the whole video, which is kept in the cache and played from there. The snapshot shows until
 * the video is ready to play, and when the hub has no video yet ("The video isn't ready yet." + Retry).
 * An alert without a clip just shows its snapshot. The player is released (and any file deleted)
 * when the video goes away.
 */
@Composable
fun AlertVideo(alert: CameraAlert, modifier: Modifier = Modifier) {
    val snapshot = alert.snapshotUrl ?: alert.thumbUrl
    val path = remember(alert.clip) { CameraClip.clipPath(alert.clip) }
    if (path == null) {
        HubImage(snapshot, modifier, contentScale = ContentScale.Fit)
        return
    }
    val app = LocalApp.current
    val context = LocalContext.current
    val server by app.store.changes.collectAsState()
    var attempt by remember { mutableIntStateOf(0) }
    var state by remember { mutableStateOf<ClipState>(ClipState.Loading) }
    LaunchedEffect(path, server.serverUrl, attempt) {
        state = ClipState.Loading
        val url = HubHttp.absolute(server.serverUrl, path)
        state = url?.let { openClip(app.http, it, File(context.cacheDir, "alert-clips")) } ?: ClipState.Failed
    }
    val downloaded = (state as? ClipState.Downloaded)?.file
    DisposableEffect(downloaded) { onDispose { downloaded?.delete() } }

    val uri = when (val s = state) {
        is ClipState.Stream -> s.url.toString()
        is ClipState.Downloaded -> Uri.fromFile(s.file).toString()
        else -> null
    }
    var ready by remember(uri) { mutableStateOf(false) }
    val player = remember(uri) { uri?.let { clipPlayer(context, app.http) } }
    DisposableEffect(player) {
        val listener = object : Player.Listener {
            override fun onPlaybackStateChanged(playbackState: Int) {
                if (playbackState == Player.STATE_READY) ready = true
            }

            override fun onPlayerError(error: PlaybackException) {
                state = ClipState.Failed
            }
        }
        if (player != null && uri != null) {
            player.addListener(listener)
            player.setMediaItem(MediaItem.fromUri(uri))
            player.playWhenReady = true
            player.prepare()
        }
        onDispose {
            player?.removeListener(listener)
            player?.release()
        }
    }
    val label = "Video of ${alert.title.ifEmpty { "the alert" }}" + (alert.cameraName.ifEmpty { null }?.let { " on $it" } ?: "")

    Box(modifier.background(Color.Black).semantics { contentDescription = label }) {
        if (player != null) {
            AndroidView(
                factory = { ctx -> PlayerView(ctx).apply { this.player = player } },
                update = { it.player = player },
                modifier = Modifier.fillMaxSize(),
                onRelease = { it.player = null },
            )
        }
        if (!ready) {
            // Over the (not yet playing) video, on black so nothing shows around a letterboxed snapshot.
            Box(Modifier.fillMaxSize().background(Color.Black)) {
                HubImage(snapshot, Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
                if (state is ClipState.Failed) {
                    Column(
                        Modifier.align(Alignment.Center).background(Color(0xB305070F), RoundedCornerShape(14.dp)).padding(14.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Text("The video isn't ready yet.", style = T.small.copy(color = N.Text))
                        Pill("Retry", selected = false) { attempt++ }
                    }
                } else {
                    CircularProgressIndicator(Modifier.align(Alignment.Center), color = N.Text)
                }
            }
        }
    }
}

/** ExoPlayer reading http(s) through the hub client (certificate, headers, token) and local files directly. */
@OptIn(UnstableApi::class)
private fun clipPlayer(context: Context, http: OkHttpClient): ExoPlayer {
    val hub = OkHttpDataSource.Factory(http.newBuilder().readTimeout(60, TimeUnit.SECONDS).build())
    return ExoPlayer.Builder(context)
        .setMediaSourceFactory(DefaultMediaSourceFactory(DefaultDataSource.Factory(context, hub)))
        .build()
}

/**
 * Asks the hub for the clip's first two bytes. 206: it has byte ranges (a finished clip) - stream it.
 * 200: still recording, so the reply is the whole video - saved into [dir] (one alert video at a time:
 * older files there are removed). Failed when the hub has none (yet) or can't be reached.
 */
private suspend fun openClip(http: OkHttpClient, url: HttpUrl, dir: File): ClipState = withContext(Dispatchers.IO) {
    dir.mkdirs()
    dir.listFiles()?.forEach { it.delete() }
    val out = File(dir, "alert-${System.currentTimeMillis()}.mp4")
    // The hub fetches the video from Frigate first: allow it a while before the first bytes.
    val call = http.newBuilder().readTimeout(60, TimeUnit.SECONDS).build()
        .newCall(Request.Builder().url(url).header("Accept", "video/mp4").header("Range", CameraClip.PROBE_RANGE).build())
    try {
        call.execute().use { r ->
            if (r.code == 206) {
                val ranged = CameraClip.parseContentRange(r.header("Content-Range"))?.total != null
                return@withContext if (ranged) ClipState.Stream(url) else ClipState.Failed
            }
            val body = r.body
            if (!r.isSuccessful || body == null) return@withContext ClipState.Failed
            out.outputStream().use { o ->
                body.byteStream().use { i ->
                    val buf = ByteArray(256 * 1024)
                    while (true) {
                        coroutineContext.ensureActive() // left the screen: stop downloading
                        val n = i.read(buf)
                        if (n < 0) break
                        o.write(buf, 0, n)
                    }
                }
            }
        }
        if (out.length() > 0) ClipState.Downloaded(out) else ClipState.Failed.also { out.delete() }
    } catch (e: CancellationException) {
        call.cancel()
        out.delete()
        throw e
    } catch (e: Exception) {
        out.delete()
        ClipState.Failed
    }
}
