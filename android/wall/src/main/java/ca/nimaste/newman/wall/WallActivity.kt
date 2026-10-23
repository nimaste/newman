package ca.nimaste.newman.wall

import android.Manifest
import android.annotation.SuppressLint
import android.app.ActivityManager
import android.app.KeyguardManager
import android.app.admin.DevicePolicyManager
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Rect
import android.net.http.SslError
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import android.view.Gravity
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.ClientCertRequest
import android.webkit.CookieManager
import android.webkit.RenderProcessGoneDetail
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import ca.nimaste.newman.core.mtls.MTLSStore
import ca.nimaste.newman.core.net.WallAddress
import ca.nimaste.newman.core.net.ServerAddress
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.core.voice.VoicePageConfig
import ca.nimaste.newman.core.wall.PinPrompt
import ca.nimaste.newman.core.wall.WallBrightness
import ca.nimaste.newman.core.wall.WallRinging
import ca.nimaste.newman.core.wall.WallShell
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * The wall tablet: the hub's own wall screen (<server>/wall) full screen in a WebView, as a kiosk.
 * Which hub (and how to reach it) is chosen first, on ConnectActivity; the hub's home-network or
 * outside address chosen there is kept for good (WallAddress: never switched home/away by itself). The tablet signs in inside that page. This shell adds the client certificate and Cloudflare
 * headers, keeps the screen on, dims after a while without a touch, exposes window.NewmanWall,
 * reports the tablet's battery to the hub (BatteryReporter) and, while on screen, listens for
 * "Hey Newman" when the page turns voice on (VoiceListener) - giving the microphone to Alexa or any
 * other app that starts recording, coming back after an assistant took over the screen (AssistantWatch), and coming back after
 * leaving the front any other way, when the screen wakes, or - dimmed, the screen turned back on - when
 * the screen goes off (ComeBackWatch). While the wall is in front the screen never goes off by itself
 * (FLAG_KEEP_SCREEN_ON; dimming only lowers the brightness), and whatever turns it off - the power
 * button too - it comes straight back on into the dimmed screen (unless the page calls keepAwake(false)).
 */
class WallActivity : ComponentActivity() {

    private lateinit var settings: WallSettings
    private lateinit var connection: ConnectionStore
    private lateinit var battery: BatteryReporter
    private lateinit var voice: VoiceListener
    private lateinit var clips: ClipRecorder
    private lateinit var assistant: AssistantWatch
    private lateinit var comeBack: ComeBackWatch
    private lateinit var root: FrameLayout
    private var web: WebView? = null
    private lateinit var dimOverlay: View
    private lateinit var errorView: LinearLayout
    private lateinit var errorText: TextView
    private lateinit var ringView: LinearLayout
    private lateinit var ringTitle: TextView
    private lateinit var ringSnooze: Button
    /** The origin the WebView's start script was set up for. */
    private var webOrigin: String? = null
    private val main = Handler(Looper.getMainLooper())

    private var lastTouch = SystemClock.elapsedRealtime()
    @Volatile private var dimmed = false
    private var swallowGesture = false
    private var swallowKey = KeyEvent.KEYCODE_UNKNOWN
    private var powerSaveWarned = false
    /** Set by the page (window.NewmanWall.setBrightness); null = the setting. */
    private var pageBrightness: Float? = null
    private var keepAwake = true
    private var loadedServer: String? = null
    private var loadedCertVersion = -1
    @Volatile private var pageIsHub = false
    private var cornerDownAt = 0L
    private var kioskTried = false
    /** Screen pinning (no device owner): asked again after every resume while not pinned. */
    private val pinPrompt = PinPrompt()
    /** "Exit kiosk until restart": no more pin prompts from this wall. */
    private var kioskExited = false
    /** When the wall last opened a screen of its own (settings, PIN check, permission, pin prompt): not an assistant taking over. */
    private var ownScreenAt = -1L
    private var userLeaveHint = false

    private fun openingOwnScreen() {
        ownScreenAt = SystemClock.elapsedRealtime()
    }

    private val openSettings = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        pinPrompt.skipNextResume() // (results arrive just before onResume)
        when (r.data?.getStringExtra(WallSettingsActivity.EXTRA_ACTION)) {
            WallSettingsActivity.ACTION_RELOAD -> reload()
            WallSettingsActivity.ACTION_EXIT_KIOSK -> {
                kioskExited = true
                stopKiosk()
            }
            WallSettingsActivity.ACTION_RELEASE_OWNER -> releaseDeviceOwner()
            WallSettingsActivity.ACTION_CLEAR_WEB -> {
                CookieManager.getInstance().removeAllCookies(null)
                android.webkit.WebStorage.getInstance().deleteAllData()
                web?.clearCache(true)
                load()
            }
        }
    }
    private val openConnect = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        pinPrompt.skipNextResume() // (a new hub is loaded in onResume)
    }
    private val confirmCredential = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        pinPrompt.skipNextResume()
        if (r.resultCode == RESULT_OK) startSettings()
    }
    private val askMic = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        voice.setPermitted(granted)
        if (!granted) toPage("""{"type":"error","message":"Allow the microphone for Newman Wall in Android's settings to use voice."}""")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        settings = WallSettings(this)
        connection = ConnectionStore.get(this)
        pinAddress()
        battery = BatteryReporter(applicationContext, connection)
        voice = VoiceListener(applicationContext, connection, toPage = ::toPage, onWake = ::wake)
        voice.setPermitted(hasMic())
        clips = ClipRecorder(toPage = ::toPage, listener = voice)
        // Coming back by itself after an assistant: no new pin prompt (pinned, the assistant couldn't have come over).
        assistant = AssistantWatch(applicationContext, voice, settings) { pinPrompt.skipNextResume() }
        assistant.start()
        // Coming back after Home / Recents / another app, or when the screen wakes (no new pin prompt either).
        // Also the screen going off, for any reason: back at once, the screen on and dimmed.
        comeBack = ComeBackWatch(application, settings, keepsScreenOn = { keepAwake }) { pinPrompt.skipNextResume() }
        comeBack.start()
        turnScreenOnWhenShown() // (a return with the screen off: ComeBackWatch)

        WindowCompat.setDecorFitsSystemWindows(window, false)
        if (Build.VERSION.SDK_INT >= 28) {
            window.attributes = window.attributes.apply { layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES }
        }
        // Painted in the page's theme colour (NewmanWall.setTheme, kept from the last run): applyShell().
        root = FrameLayout(this)
        errorText = TextView(this).apply {
            textSize = 20f
            gravity = Gravity.CENTER
            setPadding(48, 48, 48, 24)
        }
        errorView = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            visibility = View.GONE
            addView(errorText)
            addView(Button(this@WallActivity).apply {
                text = "Server"
                isAllCaps = false
                textSize = 18f
                setOnClickListener { openServer() }
            }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        // Dimmed: the hub's page shows its own dimmed screen (the time, faintly); this overlay only
        // darkens a page that doesn't (an error, an older hub). Touches never reach it: see dispatchTouchEvent.
        dimOverlay = View(this).apply {
            setBackgroundColor(Color.TRANSPARENT)
            visibility = View.GONE
        }
        setContentView(root)
        createWebView()
        root.addView(errorView, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        root.addView(dimOverlay, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        ringView = buildRingView()
        root.addView(ringView, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        AlarmRinger.listeners += ringChanged
        applyShell(settings.shell)

        // A kiosk has no "back out of the app": back goes back inside the page only.
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                web?.takeIf { it.canGoBack() }?.goBack()
            }
        })
        applyKeepAwake()
        // The screen coming back on by hand (with keepAwake off it may have slept): fully bright.
        ContextCompat.registerReceiver(this, screenOn, IntentFilter(Intent.ACTION_SCREEN_ON), ContextCompat.RECEIVER_NOT_EXPORTED)
        battery.start()
        if (!connection.hasHub) openServer() // first start: connect to a Newman before anything else
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun createWebView() {
        web?.let { root.removeView(it); it.destroy() }
        val w = WebView(this)
        w.setBackgroundColor(settings.shell.argb)
        w.settings.apply {
            // The wall is laid out for the tablet's real size: ignore the system font size, so the
            // tablet looks exactly like the dashboard editor's preview.
            textZoom = 100
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false // camera tiles play by themselves
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            userAgentString = "$userAgentString NewmanWall/${BuildConfig.VERSION_NAME}"
            setSupportZoom(false)
            builtInZoomControls = false
            displayZoomControls = false
        }
        CookieManager.getInstance().setAcceptCookie(true)
        w.webChromeClient = WebChromeClient()
        w.webViewClient = Client()
        w.addJavascriptInterface(WallBridge(this), WallBridge.NATIVE_NAME)
        if (WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
            hubOrigin()?.let { WebViewCompat.addDocumentStartJavaScript(w, WallBridge.SCRIPT, setOf(it)) }
        }
        webOrigin = hubOrigin()
        seedSession(w)
        root.addView(w, 0, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        web = w
        loadedServer = null
    }

    override fun onResume() {
        super.onResume()
        hideSystemBars()
        if (!kioskTried) {
            kioskTried = true
            startKiosk()
        }
        pinPrompt.resumed()
        pinAddress()
        if (hasWindowFocus()) askToPin() // otherwise once the window gets focus
        val server = connection.serverUrl
        if (MTLSStore.version != loadedCertVersion) {
            // A new or removed certificate: WebView remembers the old answer until told to forget.
            WebView.clearClientCertPreferences(null)
            loadedCertVersion = MTLSStore.version
            if (loadedServer != null) loadedServer = null
        }
        if (server != loadedServer) {
            if (loadedServer != null || webOrigin != hubOrigin()) createWebView()
            load()
        }
        // Back because the screen went off (or the wall was started again): on, but dimmed (the date and time).
        if (dimmedReturnPending()) returnedDimmed() else wake()
        scheduleDimCheck()
        voice.setPermitted(hasMic()) // (it may have been allowed in Android's settings meanwhile)
        voice.setForeground(true)
        userLeaveHint = false
        assistant.entered()
        ringChanged() // (an alarm the app rang itself while away: its own Stop / Snooze, fully bright)
        // The page asks again what it may (the microphone allowed meanwhile, the settings changed).
        if (pageIsHub) web?.evaluateJavascript("try{window.dispatchEvent(new Event('newmanwall-resume'))}catch(e){}", null)
    }

    override fun onUserLeaveHint() {
        super.onUserLeaveHint()
        userLeaveHint = true
    }

    override fun onPause() {
        super.onPause()
        pinPrompt.paused()
        main.removeCallbacks(dimCheck)
        voice.setForeground(false) // the microphone is only used while the wall is on screen
        clips.cancel()
        val ownScreen = ownScreenAt >= 0 && SystemClock.elapsedRealtime() - ownScreenAt < 3_000
        ownScreenAt = -1
        assistant.left(ownScreen = ownScreen, closing = isFinishing, userLeaveHint = userLeaveHint)
        userLeaveHint = false
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        pinPrompt.focusChanged(hasFocus)
        if (hasFocus) {
            hideSystemBars()
            askToPin()
        }
    }

    override fun onDestroy() {
        try {
            unregisterReceiver(screenOn)
        } catch (e: Exception) {
            // not registered
        }
        AlarmRinger.listeners -= ringChanged
        battery.stop()
        assistant.stop()
        comeBack.stop()
        voice.setForeground(false)
        clips.cancel()
        main.removeCallbacksAndMessages(null)
        web?.destroy()
        web = null
        super.onDestroy()
    }

    // -- loading ----------------------------------------------------------------------------------

    private fun serverUrl(): HttpUrl? = connection.serverUrl?.toHttpUrlOrNull()

    /** "https://home.garito.ca" (no trailing slash): who may use the bridge. */
    private fun hubOrigin(): String? = serverUrl()?.let { u ->
        val default = (u.isHttps && u.port == 443) || (!u.isHttps && u.port == 80)
        "${u.scheme}://${u.host}" + if (default) "" else ":${u.port}"
    }

    private fun isHub(url: String?): Boolean {
        val u = url?.toHttpUrlOrNull() ?: return false
        val s = serverUrl() ?: return false
        return u.host.equals(s.host, ignoreCase = true) && u.port == s.port && u.scheme == s.scheme
    }

    private fun cloudflareHeaders(): Map<String, String> {
        val c = connection.snapshot
        return if (c.hasCloudflare) mapOf("CF-Access-Client-Id" to c.cfClientId!!, "CF-Access-Client-Secret" to c.cfClientSecret!!) else emptyMap()
    }

    fun load() {
        val s = serverUrl()
        if (s == null) {
            showError("Not connected to a Newman yet.")
            return
        }
        if (!s.isHttps && !ServerAddress.isPrivateHost(s.host)) {
            showError("Newman only uses plain http on the home network. Use https:// for ${s.host}.")
            return
        }
        errorView.visibility = View.GONE
        loadedServer = connection.serverUrl
        // CF Access answers the first request with a CF_Authorization cookie; the page's own
        // requests and websockets then carry that cookie.
        web?.loadUrl(s.newBuilder().addPathSegment("wall").build().toString(), cloudflareHeaders())
    }

    fun reload() {
        errorView.visibility = View.GONE
        if (web?.url.isNullOrEmpty()) load() else web?.reload()
    }

    private val retry = Runnable { load() }

    private fun showError(text: String) {
        errorText.text = text
        errorView.visibility = View.VISIBLE
    }

    private inner class Client : WebViewClient() {
        override fun onReceivedClientCertRequest(view: WebView, request: ClientCertRequest) {
            // Only the hub gets this tablet's certificate.
            val pair = MTLSStore.keyAndChain()
            val hub = serverUrl()
            if (pair != null && hub != null && request.host.equals(hub.host, ignoreCase = true)) {
                request.proceed(pair.first, pair.second)
            } else {
                request.ignore()
            }
        }

        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            // A kiosk stays on the hub: links elsewhere are ignored.
            return !isHub(request.url.toString())
        }

        override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
            pageIsHub = isHub(url)
            voice.configure(VoicePageConfig()) // off until the (signed-in) page turns voice on again
            clips.cancel()
            main.removeCallbacks(retry)
        }

        override fun onPageFinished(view: WebView, url: String?) {
            pageIsHub = isHub(url)
            if (pageIsHub) {
                view.evaluateJavascript(WallBridge.SCRIPT, null)
                syncSession()
                battery.check() // the page may have just signed in: report without waiting for the timer
            }
        }

        override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) {
            pageIsHub = isHub(url)
            if (pageIsHub) syncSession()
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (!request.isForMainFrame) return
            showError("Can't reach Newman (${error.description}).\nTrying again in 15 seconds…")
            main.removeCallbacks(retry)
            main.postDelayed(retry, 15_000)
        }

        override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
            handler.cancel()
            showError("The hub's certificate isn't trusted (${error.url}). Check the address in settings.")
        }

        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            // The page crashed or was killed for memory: start a fresh WebView rather than the app dying.
            main.post {
                createWebView()
                load()
            }
            return true
        }
    }

    // -- address and session -----------------------------------------------------------------------

    /**
     * The wall stays on the address it was set up with (WallAddress): through a hub restart or an
     * outage it keeps retrying that one; it never moves home/away by itself. Only Connect changes it.
     */
    private fun pinAddress() {
        val a = WallAddress.address(connection.hub, connection.chosenUrl, connection.serverUrl) ?: return
        if (a != connection.serverUrl) {
            Log.i(TAG, "back to the address this wall was set up with: $a (was ${connection.serverUrl})")
            connection.serverUrl = a
        }
    }

    /**
     * The page's tablet sign-in (its token, newman.tablet.token in the page's storage) is kept here too:
     * native requests (battery reports) use it when there's no session cookie for the address, and a page
     * on an address that has no sign-in of its own yet gets it before its scripts run.
     */
    private fun syncSession() {
        val w = web ?: return
        if (!pageIsHub) return
        w.evaluateJavascript("(function(){try{return localStorage.getItem('$TOKEN_KEY');}catch(e){return null;}})()") { v ->
            if (!pageIsHub) return@evaluateJavascript
            val token = try {
                org.json.JSONTokener(v ?: "null").nextValue() as? String
            } catch (e: Exception) {
                null
            }
            if (token.isNullOrEmpty()) {
                if (v == "null" && connection.token != null) connection.token = null // signed out
            } else if (token != connection.token) {
                connection.token = token
            }
        }
    }

    private fun seedSession(w: WebView) {
        val token = connection.token ?: return
        val origin = webOrigin ?: return
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) return
        val js = "try{if(!localStorage.getItem('$TOKEN_KEY'))localStorage.setItem('$TOKEN_KEY',${org.json.JSONObject.quote(token)});}catch(e){}"
        WebViewCompat.addDocumentStartJavaScript(w, js, setOf(origin))
    }

    /** From the page (NewmanWall.setTheme): its theme's colour, kept for the next start. */
    fun setShellTheme(background: String?, scheme: String?) {
        val shell = WallShell.parse(background, scheme) ?: return
        if (shell != settings.shell) settings.shell = shell
        applyShell(shell)
    }

    /** The shell behind the page (loading, "can't reach", the display cutout) in the page's colour. */
    private fun applyShell(shell: WallShell) {
        root.setBackgroundColor(shell.argb)
        window.setBackgroundDrawable(android.graphics.drawable.ColorDrawable(shell.argb))
        errorView.setBackgroundColor(shell.argb)
        errorText.setTextColor(Color.parseColor(shell.ink))
        web?.setBackgroundColor(shell.argb)
    }

    /** True while the hub's own page is showing (the bridge answers only to it). */
    fun bridgeAllowed(): Boolean = pageIsHub

    // -- voice ---------------------------------------------------------------------------------------

    private fun hasMic() =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    /** window.NewmanVoice.event(json) on the hub's page. */
    private fun toPage(json: String) {
        if (!pageIsHub) return
        web?.evaluateJavascript("window.NewmanVoice && window.NewmanVoice.event($json);", null)
    }

    /** From the page: voice on/off and the wake threshold. The microphone is asked for once. */
    fun voiceConfig(json: String?) {
        val cfg = VoicePageConfig.parse(json)
        voice.configure(cfg)
        if (cfg.enabled && !hasMic()) askForMic()
    }

    /** From the page: the reply was spoken - listen for the wake word, or ([listen]) for the answer to its question. */
    fun voiceDone(listen: Boolean) = voice.replyFinished(listen)

    fun micAllowed(): Boolean = hasMic()

    /**
     * From the page's "Allow the microphone": Newman Wall's own screen in Android's settings (its
     * permissions). A pinned or locked screen is let go for it; coming back pins / locks it again.
     */
    fun openMicSettings() {
        if (getSystemService(ActivityManager::class.java)?.lockTaskModeState != ActivityManager.LOCK_TASK_MODE_NONE) {
            stopKiosk()
            kioskTried = false // (device owner: locked again on the way back)
        }
        openingOwnScreen()
        try {
            startActivity(Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS, android.net.Uri.parse("package:$packageName")))
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't open the app's settings: $e")
        }
    }

    // -- alarms and timers ringing (AlarmRinging.kt) -------------------------------------------------

    /** From the page: something rings - the chosen sound on the alarm channel, the screen woken fully bright. */
    fun ring(json: String?) {
        val r = WallRinging.parseRing(json) ?: return
        AlarmRinger.start(this, r, backup = false)
    }

    /** From the page: it was stopped, snoozed, or someone is talking to Newman over it. */
    fun stopRing() {
        if (AlarmRinger.current?.backup != true) AlarmRinger.stop()
    }

    fun ringSettings(): String = WallRinging.settingsJson(settings.snoozeMinutes, settings.ringMinutes)

    fun alarmSchedule(json: String?) = AlarmBackup.save(this, json)

    /** Ringing (or not) changed: full brightness and no dimming while it rings; the app's own Stop / Snooze for a backup ring. */
    private val ringChanged: () -> Unit = {
        val c = AlarmRinger.current
        if (c != null) {
            wake()
            setWindowBrightness(1f)
        } else if (!dimmed) {
            setWindowBrightness(awakeLevel())
        }
        showBackupRing(c?.takeIf { it.backup }?.ring)
    }

    private fun showBackupRing(r: WallRinging.Ring?) {
        if (r == null) {
            ringView.visibility = View.GONE
            return
        }
        ringTitle.text = r.title.ifBlank { "ALARM" }
        ringSnooze.text = "Snooze ${settings.snoozeMinutes} min"
        ringView.visibility = View.VISIBLE
    }

    /** The app's own ringing screen, for an alarm it rang itself (the page wasn't there to). */
    private fun buildRingView(): LinearLayout {
        val pad = (24 * resources.displayMetrics.density).toInt()
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(Color.rgb(11, 16, 38))
            visibility = View.GONE
            isClickable = true // (touches stop here)
            ringTitle = TextView(this@WallActivity).apply {
                textSize = 34f
                setTextColor(Color.rgb(245, 185, 66))
                gravity = Gravity.CENTER
                setPadding(pad, pad, pad, pad)
            }
            addView(ringTitle)
            val row = LinearLayout(this@WallActivity).apply { gravity = Gravity.CENTER }
            ringSnooze = Button(this@WallActivity).apply {
                textSize = 26f
                isAllCaps = false
                setOnClickListener {
                    val c = AlarmRinger.current ?: return@setOnClickListener
                    AlarmRinger.stop()
                    AlarmBackup.snooze(this@WallActivity, c.ring, settings.snoozeMinutes)
                }
            }
            row.addView(ringSnooze)
            row.addView(Button(this@WallActivity).apply {
                text = "Stop"
                textSize = 26f
                isAllCaps = false
                setOnClickListener {
                    AlarmRinger.current?.let { AlarmBackup.stopped(it.ring) }
                    AlarmRinger.stop()
                }
            })
            addView(row)
        }
    }

    /** From the page ("Teach Hey Newman"): record one clip with the microphone (ClipRecorder). */
    fun recordClip(maxMs: Int, fixed: Boolean) {
        if (!hasMic()) {
            askForMic()
            toPage("""{"type":"clip-error","message":"Allow the microphone for Newman Wall in Android's settings first."}""")
            return
        }
        clips.start(maxMs, fixed)
    }

    fun stopClip() = clips.stop()

    private fun askForMic() {
        if (isDeviceOwner()) {
            // A kiosk tablet (device owner) grants itself the microphone - no dialog on the wall.
            try {
                getSystemService(DevicePolicyManager::class.java)?.setPermissionGrantState(admin(), packageName,
                    Manifest.permission.RECORD_AUDIO, DevicePolicyManager.PERMISSION_GRANT_STATE_GRANTED)
            } catch (e: Exception) {
                // fall back to asking
            }
            if (hasMic()) {
                voice.setPermitted(true)
                return
            }
        }
        val prefs = getSharedPreferences("newman_wall_voice", MODE_PRIVATE)
        if (prefs.getBoolean("micAsked", false)) return // asked once; Android's settings from here on
        prefs.edit().putBoolean("micAsked", true).apply()
        openingOwnScreen()
        askMic.launch(Manifest.permission.RECORD_AUDIO)
    }

    // -- kiosk -------------------------------------------------------------------------------------

    private fun hideSystemBars() {
        val c = WindowInsetsControllerCompat(window, window.decorView)
        c.hide(WindowInsetsCompat.Type.systemBars())
        c.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    }

    private fun admin() = ComponentName(this, WallAdminReceiver::class.java)

    fun isDeviceOwner(): Boolean = getSystemService(DevicePolicyManager::class.java)?.isDeviceOwnerApp(packageName) == true

    /** Lock task mode when the app is device owner (once per start); screen pinning is [askToPin]. */
    private fun startKiosk() {
        val am = getSystemService(ActivityManager::class.java)
        if (am?.lockTaskModeState != ActivityManager.LOCK_TASK_MODE_NONE) return
        try {
            if (isDeviceOwner()) {
                val dpm = getSystemService(DevicePolicyManager::class.java)
                dpm.setLockTaskPackages(admin(), arrayOf(packageName))
                // Be the home app, so the wall is what the tablet shows after any restart.
                val home = IntentFilter(Intent.ACTION_MAIN).apply {
                    addCategory(Intent.CATEGORY_HOME)
                    addCategory(Intent.CATEGORY_DEFAULT)
                }
                dpm.addPersistentPreferredActivity(admin(), home, ComponentName(this, WallActivity::class.java))
                startLockTask()
            }
        } catch (e: Exception) {
            // Not allowed here: run as a normal full-screen app.
        }
    }

    /**
     * Without device owner: screen pinning, asked for again after every resume while the screen
     * isn't pinned (unpinned with Back + Recents, then the wall opened again). Android shows its
     * confirm prompt each time; a declined prompt waits for the next resume (PinPrompt).
     */
    private fun askToPin() {
        val pinned = getSystemService(ActivityManager::class.java)?.lockTaskModeState != ActivityManager.LOCK_TASK_MODE_NONE
        if (!pinPrompt.shouldAsk(settings.pinScreen && !kioskExited, isDeviceOwner(), pinned)) return
        openingOwnScreen() // (some Android builds show the prompt as its own screen)
        try {
            startLockTask()
        } catch (e: Exception) {
            // Not allowed here (e.g. a Fire OS build without pinning): run as a normal full-screen app.
            pinPrompt.askFailed()
        }
    }

    fun stopKiosk() {
        try {
            stopLockTask()
        } catch (e: Exception) {
            // not in lock task
        }
    }

    /** Give up device ownership (so the app can be uninstalled or the tablet reset normally). */
    fun releaseDeviceOwner() {
        val dpm = getSystemService(DevicePolicyManager::class.java) ?: return
        if (!isDeviceOwner()) return
        stopKiosk()
        try {
            dpm.clearPackagePersistentPreferredActivities(admin(), packageName)
            dpm.setLockTaskPackages(admin(), arrayOf())
        } catch (e: Exception) {
            // best effort
        }
        @Suppress("DEPRECATION")
        dpm.clearDeviceOwnerApp(packageName)
    }

    // -- screen: awake, dimmed, brightness --------------------------------------------------------

    private fun applyKeepAwake() {
        if (keepAwake) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    fun setKeepAwake(on: Boolean) {
        keepAwake = on
        applyKeepAwake()
    }

    /** Sets the window's brightness now (synchronously: a wake is visible on the touch itself). */
    private fun setWindowBrightness(level: Float) {
        window.attributes = window.attributes.apply {
            screenBrightness = if (level < 0f) WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE else level
        }
    }

    private fun powerSave(): Boolean {
        val on = getSystemService(PowerManager::class.java)?.isPowerSaveMode == true
        if (on && !powerSaveWarned) Log.w(TAG, "Battery Saver is on: the display lowers brightness; the wall sets its own levels explicitly")
        powerSaveWarned = on
        return on
    }

    /** The awake level: the page's, else the setting; with Battery Saver on and the system's own level, that level explicitly. */
    private fun awakeLevel(): Float {
        val level = WallBrightness.awake(pageBrightness ?: settings.brightLevel)
        if (level >= 0f || !powerSave()) return level
        val system = try {
            android.provider.Settings.System.getInt(contentResolver, android.provider.Settings.System.SCREEN_BRIGHTNESS) / 255f
        } catch (e: Exception) {
            return level
        }
        return WallBrightness.awake(system)
    }

    /** From the page: 0..1 while awake (until the next wake-up from dim); never below WallBrightness.FLOOR. */
    fun setPageBrightness(level: Float) {
        pageBrightness = WallBrightness.awake(level.coerceIn(0f, 1f))
        if (!dimmed) setWindowBrightness(pageBrightness!!)
    }

    fun wake() {
        dimmedReturnAt = -1L // a touch, a key, the page or the screen woken by hand: awake from here on
        lastTouch = SystemClock.elapsedRealtime()
        val was = dimmed
        dimmed = false
        dimOverlay.visibility = View.GONE
        setWindowBrightness(awakeLevel())
        if (was) {
            Log.i(TAG, "wake: brightness ${window.attributes.screenBrightness}")
            hideSystemBars()
            setGestureGuard(false)
            dimToPage(false)
        }
        // Turn the screen on if it went off (keepAwake false, or the page asked to wake it).
        turnScreenOnWhenShown()
    }

    /** The screen comes on (over the lock screen) whenever the wall is shown. */
    private fun turnScreenOnWhenShown() {
        if (Build.VERSION.SDK_INT >= 27) {
            setTurnScreenOn(true)
            setShowWhenLocked(true)
            getSystemService(KeyguardManager::class.java)?.let { if (!it.isKeyguardSecure) it.requestDismissKeyguard(this, null) }
        }
    }

    /**
     * Back in front with the screen just turned on by ComeBackWatch / keepWallUp (it went off):
     * the dimmed screen, as if nobody had touched the wall for a while - never full brightness, and
     * the window holds the screen on (FLAG_KEEP_SCREEN_ON) as always.
     */
    private fun returnedDimmed() {
        Log.i(TAG, "back with the screen turned on: dimmed")
        turnScreenOnWhenShown()
        applyKeepAwake()
        if (dimmed) {
            setWindowBrightness(WallBrightness.dimmed(settings.dimLevel, powerSave()))
            hideSystemBars()
            setGestureGuard(true)
            dimToPage(true)
        } else dim()
    }

    /** For the page (NewmanWall.isDimmed()): it may have loaded while the screen was dimmed. */
    val isDimmed: Boolean get() = dimmed

    /** From the page (NewmanWall.dim()): its dim button, or its dimmed screen is showing - which it draws itself. */
    fun dimFromPage() {
        dim()
        dimOverlay.setBackgroundColor(Color.TRANSPARENT)
    }

    /** Dims now, after the idle time (or for the page's dim button). */
    private fun dim() {
        if (dimmed) return
        dimmed = true
        pageBrightness = null
        dimOverlay.setBackgroundColor(Color.TRANSPARENT)
        dimOverlay.visibility = View.VISIBLE
        val level = WallBrightness.dimmed(settings.dimLevel, powerSave())
        setWindowBrightness(level)
        Log.i(TAG, "dim: brightness $level")
        hideSystemBars()
        setGestureGuard(true)
        if (pageIsHub) dimToPage(true) else dimOverlay.setBackgroundColor(FALLBACK_DIM)
    }

    /**
     * Tells the page (a "newmanwall-dim" event, detail.dimmed). The hub's page answers by drawing its
     * own dimmed screen and preventDefault(); a page that doesn't gets this overlay's darkening.
     */
    private fun dimToPage(on: Boolean) {
        val w = web ?: return
        if (!pageIsHub) return
        val js = "(function(){try{var e=new CustomEvent('newmanwall-dim',{detail:{dimmed:$on},cancelable:true});" +
            "window.dispatchEvent(e);return e.defaultPrevented;}catch(x){return false;}})()"
        w.evaluateJavascript(js) { handled ->
            if (on && dimmed && handled != "true") dimOverlay.setBackgroundColor(FALLBACK_DIM)
        }
    }

    /** While dimmed, keep the edges' system gestures (back) from taking the waking touch (Android 10+, as far as it allows). */
    private fun setGestureGuard(on: Boolean) {
        if (Build.VERSION.SDK_INT < 29) return
        val v = window.decorView
        v.systemGestureExclusionRects = if (on) listOf(Rect(0, 0, v.width, v.height)) else emptyList()
    }

    private val screenOn = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            // (Not the screen ComeBackWatch turned on for a dimmed return: onResume dims that.)
            if (intent.action == Intent.ACTION_SCREEN_ON && !dimmedReturnPending()) wake()
        }
    }

    /** Any hardware key on a dimmed screen only wakes it (its press and release go no further). */
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (dimmed && event.action == KeyEvent.ACTION_DOWN) {
            wake()
            swallowKey = event.keyCode
            return true
        }
        if (swallowKey == event.keyCode) {
            if (event.action == KeyEvent.ACTION_UP) swallowKey = KeyEvent.KEYCODE_UNKNOWN
            return true
        }
        lastTouch = SystemClock.elapsedRealtime()
        return super.dispatchKeyEvent(event)
    }

    private val dimCheck = object : Runnable {
        override fun run() {
            val minutes = settings.dimMinutes
            if (!dimmed && minutes > 0 && SystemClock.elapsedRealtime() - lastTouch > minutes * 60_000L && !AlarmRinger.isRinging) dim()
            syncSession()
            main.postDelayed(this, 10_000)
        }
    }

    private fun scheduleDimCheck() {
        main.removeCallbacks(dimCheck)
        main.postDelayed(dimCheck, 10_000)
    }

    private val cornerHold = Runnable { askForSettings() }

    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
        lastTouch = SystemClock.elapsedRealtime()
        // The first touch on a dimmed screen only wakes it: the whole gesture goes no further.
        if (ev.actionMasked == MotionEvent.ACTION_DOWN && dimmed) {
            wake() // brightness back synchronously, before the page hears anything
            Log.i(TAG, "first touch on the dimmed screen: awake ${SystemClock.uptimeMillis() - ev.eventTime} ms after it")
            swallowGesture = true
            return true
        }
        if (swallowGesture) {
            if (ev.actionMasked == MotionEvent.ACTION_UP || ev.actionMasked == MotionEvent.ACTION_CANCEL) swallowGesture = false
            return true
        }
        // Settings: hold the top-left corner for 3 seconds.
        val corner = 72 * resources.displayMetrics.density
        when (ev.actionMasked) {
            MotionEvent.ACTION_DOWN -> if (ev.x < corner && ev.y < corner) {
                cornerDownAt = SystemClock.elapsedRealtime()
                main.postDelayed(cornerHold, 3_000)
            }
            MotionEvent.ACTION_MOVE -> if (ev.x > corner * 1.5f || ev.y > corner * 1.5f) main.removeCallbacks(cornerHold)
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL, MotionEvent.ACTION_POINTER_DOWN -> main.removeCallbacks(cornerHold)
        }
        return super.dispatchTouchEvent(ev)
    }

    /** "Connect to your Newman": which hub, its address, certificate and token (the sign-in page's "Server"). */
    fun openServer() {
        openingOwnScreen()
        openConnect.launch(Intent(this, ConnectActivity::class.java))
    }

    /** Settings need the tablet's PIN / pattern / biometrics when it has one. */
    fun askForSettings() {
        val km = getSystemService(KeyguardManager::class.java)
        @Suppress("DEPRECATION")
        val intent = if (km?.isDeviceSecure == true) km.createConfirmDeviceCredentialIntent("Newman Wall settings", "Confirm it's you") else null
        if (intent != null) {
            openingOwnScreen()
            confirmCredential.launch(intent)
        } else startSettings()
    }

    private fun startSettings() {
        openingOwnScreen()
        openSettings.launch(Intent(this, WallSettingsActivity::class.java))
    }

    companion object {
        private const val TAG = "NewmanWall"
        /** Where the hub's wall page keeps its tablet sign-in (web/src/api.js). */
        private const val TOKEN_KEY = "newman.tablet.token"
        /** Darkening for a dimmed page that doesn't draw its own dimmed screen: dark, still visibly on. */
        private val FALLBACK_DIM = Color.argb(140, 0, 0, 0)
        /** How long after [returnDimmed] the wall's showing (and the screen coming on) counts as that return. */
        private const val DIMMED_RETURN_MS = 15_000L

        /** When the wall was last brought back dimmed (bringWallBack, elapsedRealtime), or -1. */
        @Volatile private var dimmedReturnAt = -1L

        /** bringWallBack, just before bringing the wall back and turning the screen on: show it dimmed. */
        fun returnDimmed() {
            dimmedReturnAt = SystemClock.elapsedRealtime()
        }

        private fun dimmedReturnPending(): Boolean =
            dimmedReturnAt >= 0 && SystemClock.elapsedRealtime() - dimmedReturnAt < DIMMED_RETURN_MS
    }
}
