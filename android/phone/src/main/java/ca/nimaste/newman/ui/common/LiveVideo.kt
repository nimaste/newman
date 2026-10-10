package ca.nimaste.newman.ui.common

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Color
import android.os.Handler
import android.os.Looper
import android.view.MotionEvent
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.webkit.WebViewAssetLoader
import ca.nimaste.newman.core.live.LiveVideoSession
import ca.nimaste.newman.core.live.OkHttpLiveOpener
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.ui.LocalApp
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

/**
 * A camera: its refreshing still, with live video on top once it plays (like the wall's
 * LiveVideo). Devices without a `live` stream keep the refreshing still. Tiles use "sub" and
 * share a pool of 4 streams; full screen uses "main" with [priority] (never waits).
 * Streaming stops when this leaves the screen or the app goes to the background.
 */
@Composable
fun LiveCamera(
    entity: Entity?,
    modifier: Modifier = Modifier,
    quality: String = "sub",
    priority: Boolean = false,
    heightPx: Int = 480,
    contentScale: ContentScale = ContentScale.Crop,
    overlay: @Composable BoxScope.() -> Unit = {},
) {
    var playing by remember(entity?.id, quality) { mutableStateOf(false) }
    Box(modifier) {
        CameraStill(entity, Modifier.fillMaxSize(), heightPx, refresh = !playing, contentScale = contentScale)
        if (entity?.live != null) {
            LivePlayer(entity, entity.live!!, quality, priority, if (contentScale == ContentScale.Crop) "cover" else "contain",
                onPlaying = { playing = it }, modifier = Modifier.fillMaxSize().alpha(if (playing) 1f else 0f))
        }
        overlay()
    }
}

@Composable
private fun LivePlayer(entity: Entity, livePath: String, quality: String, priority: Boolean, fit: String,
                       onPlaying: (Boolean) -> Unit, modifier: Modifier) {
    val app = LocalApp.current
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val latestOnPlaying by rememberUpdatedState(onPlaying)
    val player = remember(entity.id, livePath, quality, fit) {
        LivePlayerView(context, app.http, app.store, livePath, quality, fit) { latestOnPlaying(it) }
    }
    DisposableEffect(player, priority) {
        val key = "${entity.id}:$quality:${System.identityHashCode(player)}"
        var granted = false
        fun startIfAllowed() {
            if (granted && lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) player.session.start()
        }
        app.livePool.request(key, priority) {
            granted = true
            player.main.post { startIfAllowed() }
        }
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_START -> startIfAllowed()
                Lifecycle.Event.ON_STOP -> player.session.stop()
                else -> Unit
            }
        }
        lifecycle.addObserver(observer)
        onDispose {
            lifecycle.removeObserver(observer)
            granted = false
            app.livePool.release(key)
            player.release()
        }
    }
    AndroidView(factory = { (player.webView.parent as? android.view.ViewGroup)?.removeView(player.webView); player.webView }, modifier = modifier)
}

/** The player page in a WebView, wired to a [LiveVideoSession] that owns the hub's socket. */
@SuppressLint("SetJavaScriptEnabled")
private class LivePlayerView(
    context: Context,
    http: OkHttpClient,
    store: ConnectionStore,
    livePath: String,
    quality: String,
    fit: String,
    onPlaying: (Boolean) -> Unit,
) {
    val main = Handler(Looper.getMainLooper())
    @Volatile private var released = false
    val webView: WebView = NoTouchWebView(context)
    val session: LiveVideoSession

    init {
        val assets = WebViewAssetLoader.Builder().addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(context)).build()
        val socketClient = http.newBuilder().readTimeout(0, TimeUnit.MILLISECONDS).build()
        session = LiveVideoSession(
            page = { js -> main.post { if (!released) webView.evaluateJavascript(js, null) } },
            opener = OkHttpLiveOpener(socketClient) { LiveVideoSession.url(store.snapshot.serverUrl, livePath, quality) },
            scheduler = { ms, block ->
                val r = Runnable { block() }
                main.postDelayed(r, ms)
                ({ main.removeCallbacks(r) })
            },
            onState = { s -> main.post { if (!released) onPlaying(s == LiveVideoSession.State.PLAYING) } },
            fit = fit,
        )
        webView.apply {
            setBackgroundColor(Color.BLACK)
            isVerticalScrollBarEnabled = false
            isHorizontalScrollBarEnabled = false
            overScrollMode = WebView.OVER_SCROLL_NEVER
            isFocusable = false
            settings.javaScriptEnabled = true
            settings.mediaPlaybackRequiresUserGesture = false
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.setSupportZoom(false)
            addJavascriptInterface(Host(), "NewmanLiveHost")
            webViewClient = object : WebViewClient() {
                override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
                    assets.shouldInterceptRequest(request.url)
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) = true
            }
            loadUrl(PLAYER_URL)
        }
    }

    inner class Host {
        @JavascriptInterface
        fun post(json: String) {
            if (!released) session.onPageMessage(json)
        }
    }

    fun release() {
        session.stop()
        released = true
        main.post {
            (webView.parent as? android.view.ViewGroup)?.removeView(webView)
            webView.destroy()
        }
    }

    companion object {
        /** Served from the APK's assets on a secure origin, so Media Source Extensions are available. */
        const val PLAYER_URL = "https://appassets.androidplatform.net/assets/live-player.html"
    }
}

/** The video takes no touches: taps go to the tile / screen underneath. */
private class NoTouchWebView(context: Context) : WebView(context) {
    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(event: MotionEvent?): Boolean = false
    override fun dispatchTouchEvent(event: MotionEvent?): Boolean = false
}
