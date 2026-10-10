package ca.nimaste.newman.wall

import android.os.Handler
import android.os.Looper
import android.webkit.JavascriptInterface
import java.lang.ref.WeakReference

/**
 * window.NewmanWall for the hub's wall page:
 *   NewmanWall.version            this app's version ("2026.10.0")
 *   NewmanWall.setBrightness(0..1) screen brightness while awake
 *   NewmanWall.wake()             wake the screen from dim (e.g. the doorbell rang)
 *   NewmanWall.dim()              dim now (the page's dim button); the page draws its own dimmed screen
 *   NewmanWall.isDimmed()         whether the screen is dimmed (the page may load while it is)
 *   The app tells the page with a cancelable window event 'newmanwall-dim' (detail.dimmed); the page
 *   calls preventDefault() when it draws its own dimmed screen, otherwise the app darkens it.
 *   NewmanWall.keepAwake(bool)    keep the screen on (default) or let it sleep normally
 *   NewmanWall.voiceConfig(json)  voice: {"enabled", "threshold", "version"} - listen for "Hey Newman" or not
 *   NewmanWall.voiceDone()        the spoken reply finished: listen for the wake word again
 *   NewmanWall.openSettings()     this app's settings (after the tablet's PIN), from the clock menu
 *   NewmanWall.openServer()       "Connect to your Newman" (which hub; address, certificate, token), from sign-in
 *   NewmanWall.setTheme({background, scheme})  the page's theme colour ("#rrggbb", "light" | "dark"): the app
 *                                 paints it behind the page while it loads or can't be reached, and keeps it
 *   NewmanWall.recordClip(ms, fixed)  "Teach Hey Newman": record one clip (ClipRecorder), answered with
 *                                 NewmanVoice.event({type: "clip" | "clip-level" | "clip-none" | "clip-error"})
 *   NewmanWall.stopClip()         stop it (a fixed-length one hands over what it has; a spoken one is dropped)
 * The app calls back window.NewmanVoice.event({...}) (see VoiceListener / web/src/wall/VoiceLayer.jsx).
 * The native object answers only while the hub's own page is showing.
 */
class WallBridge(activity: WallActivity) {
    private val ref = WeakReference(activity)
    private val main = Handler(Looper.getMainLooper())

    private fun onUi(block: (WallActivity) -> Unit) {
        val a = ref.get() ?: return
        if (!a.bridgeAllowed()) return
        main.post { ref.get()?.let(block) }
    }

    @JavascriptInterface
    fun version(): String = BuildConfig.VERSION_NAME

    @JavascriptInterface
    fun setBrightness(level: Double) {
        if (level.isNaN()) return
        onUi { it.setPageBrightness(level.toFloat()) }
    }

    @JavascriptInterface
    fun wake() = onUi { it.wake() }

    @JavascriptInterface
    fun dim() = onUi { it.dimFromPage() }

    @JavascriptInterface
    fun isDimmed(): Boolean = ref.get()?.let { it.bridgeAllowed() && it.isDimmed } == true

    @JavascriptInterface
    fun keepAwake(on: Boolean) = onUi { it.setKeepAwake(on) }

    @JavascriptInterface
    fun voiceConfig(json: String?) = onUi { it.voiceConfig(json) }

    @JavascriptInterface
    fun voiceDone() = onUi { it.voiceDone() }

    @JavascriptInterface
    fun openSettings() = onUi { it.askForSettings() }

    @JavascriptInterface
    fun openServer() = onUi { it.openServer() }

    @JavascriptInterface
    fun setTheme(background: String?, scheme: String?) = onUi { it.setShellTheme(background, scheme) }

    @JavascriptInterface
    fun recordClip(maxMs: Int, fixed: Boolean) = onUi { it.recordClip(maxMs, fixed) }

    @JavascriptInterface
    fun stopClip() = onUi { it.stopClip() }

    companion object {
        const val NATIVE_NAME = "NewmanWallNative"

        /** Wraps the native object as window.NewmanWall (a plain JS object with a `version` value). */
        val SCRIPT = """
            (function () {
              var n = window.$NATIVE_NAME;
              if (!n || (window.NewmanWall && window.NewmanWall.native === true)) return;
              window.NewmanWall = {
                native: true,
                version: String(n.version()),
                setBrightness: function (v) { n.setBrightness(Math.max(0, Math.min(1, Number(v)))); },
                wake: function () { n.wake(); },
                dim: function () { n.dim(); },
                isDimmed: function () { return n.isDimmed() === true; },
                keepAwake: function (on) { n.keepAwake(on !== false); },
                voiceConfig: function (json) { n.voiceConfig(String(json || '')); },
                voiceDone: function () { n.voiceDone(); },
                openSettings: function () { n.openSettings(); },
                openServer: function () { n.openServer(); },
                setTheme: function (t) { t = t || {}; n.setTheme(String(t.background || ''), String(t.scheme || '')); },
                recordClip: function (ms, fixed) { n.recordClip(Math.round(Number(ms) || 5000), fixed === true); },
                stopClip: function () { n.stopClip(); }
              };
              try { window.dispatchEvent(new Event('newmanwall')); } catch (e) {}
            })();
        """.trimIndent()
    }
}
