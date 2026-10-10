package ca.nimaste.newman.ui.onboarding

import android.content.Context
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import ca.nimaste.newman.R
import ca.nimaste.newman.core.api.ApiErrors
import ca.nimaste.newman.core.api.LoginResult
import ca.nimaste.newman.core.api.NewmanApi
import ca.nimaste.newman.core.api.hubCall
import ca.nimaste.newman.core.mtls.MTLSStore
import ca.nimaste.newman.core.net.ConnectCheck
import ca.nimaste.newman.core.net.ConnectLink
import ca.nimaste.newman.core.net.ConnectResult
import ca.nimaste.newman.core.net.HubChoice
import ca.nimaste.newman.core.net.HubDiscovery
import ca.nimaste.newman.core.net.HubSelector
import ca.nimaste.newman.core.net.HubPlan
import androidx.lifecycle.SavedStateHandle
import ca.nimaste.newman.ui.common.BackPill
import ca.nimaste.newman.core.net.SavedHub
import ca.nimaste.newman.core.net.ServerAddress
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.data.Account
import ca.nimaste.newman.ui.common.Gap
import ca.nimaste.newman.ui.common.heading
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import ca.nimaste.newman.ui.common.GlassButton
import ca.nimaste.newman.ui.common.GlassCard
import ca.nimaste.newman.ui.common.NTextField
import ca.nimaste.newman.ui.common.PrimaryButton
import ca.nimaste.newman.ui.common.certificateSummary
import ca.nimaste.newman.ui.common.rememberCertificateImport
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.NightBackground
import ca.nimaste.newman.ui.theme.T
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import javax.inject.Inject

/**
 * Connect to your Newman (find it on the network, scan its QR code or type its address; certificate and
 * Cloudflare token under "Connection security") -> sign in (2FA if asked). All before signing in.
 *
 * The same Connect step, signed in ([changing]: Me -> Server, or a newman://connect link): the same hub
 * at new addresses just keeps them; another hub signs this phone out of the old one first, then on to
 * signing in to the new one (HubPlan).
 */
@HiltViewModel
class OnboardingViewModel @Inject constructor(
    @ApplicationContext context: Context,
    private val store: ConnectionStore,
    private val api: NewmanApi,
    private val account: Account,
    private val http: OkHttpClient,
    private val selector: HubSelector,
    savedState: SavedStateHandle,
) : ViewModel() {
    /** Signed in, changing the server (the "server" route). */
    val changing: Boolean = savedState.get<Boolean>(ARG_CHANGING) == true

    enum class Step { CONNECT, SIGN_IN, CODE }

    /** A hub that answered, waiting for "Continue". */
    data class Found(val hub: SavedHub, val url: String)

    data class UiState(
        val step: Step,
        val address: String,
        val cfId: String,
        val cfSecret: String,
        val security: Boolean = false,
        val testing: Boolean = false,
        val found: Found? = null,
        val hubName: String? = null,
        val username: String = "",
        val password: String = "",
        val code: String = "",
        val busy: Boolean = false,
        val error: String? = null,
        val note: String? = null,
    )

    val discovery = HubDiscovery(context)

    val hasHub: Boolean get() = store.hasHub

    private val _ui = MutableStateFlow(
        UiState(
            // Signed out earlier: the hub (and certificate) are still set, go straight to sign-in.
            step = if (store.hasHub && !changing) Step.SIGN_IN else Step.CONNECT,
            address = store.hub.outsideUrl ?: store.hub.localUrl ?: "",
            cfId = store.snapshot.cfClientId.orEmpty(),
            cfSecret = store.snapshot.cfClientSecret.orEmpty(),
            security = store.snapshot.hasCloudflare,
            hubName = store.hub.name,
        ),
    )
    val ui = _ui.asStateFlow()

    init {
        if (_ui.value.step == Step.SIGN_IN) checkReachable()
        savedState.get<String>(ARG_LINK)?.takeIf { it.isNotBlank() }?.let { scanned(it) }
    }

    /** Changing server: is what was found the hub this phone is signed in to? */
    fun isSameHub(f: Found): Boolean = HubPlan.isSameHub(store.hub.takeIf { store.hasHub }, store.serverUrl, f.hub, f.url)

    /**
     * Changing server: keep what was found. Another hub: [leave] signs out of the old one first (while its
     * addresses are still the ones in use). Then [done] (same hub or not).
     */
    fun saveChange(leave: suspend () -> Unit, done: (sameHub: Boolean) -> Unit) {
        val f = _ui.value.found ?: return
        if (_ui.value.busy) return
        val same = isSameHub(f)
        _ui.value = _ui.value.copy(busy = true, error = null)
        viewModelScope.launch {
            if (!same) leave()
            val cf = cloudflare()
            store.setCloudflare(cf?.first, cf?.second)
            store.saveHub(if (same) HubPlan.merged(store.hub, f.hub) else f.hub, f.url)
            account.connectionChanged()
            selector.recheck(500)
            _ui.value = _ui.value.copy(busy = false, hubName = f.hub.name)
            done(same)
        }
    }

    fun update(f: (UiState) -> UiState) {
        _ui.value = f(_ui.value)
    }

    fun go(step: Step) {
        _ui.value = _ui.value.copy(step = step, error = null, note = null, found = null, testing = false)
    }

    fun back() {
        when (_ui.value.step) {
            Step.CONNECT -> if (store.hasHub) go(Step.SIGN_IN)
            Step.SIGN_IN -> go(Step.CONNECT)
            Step.CODE -> go(Step.SIGN_IN)
        }
    }

    private fun cloudflare(): Pair<String, String>? {
        val s = _ui.value
        return if (s.cfId.isNotBlank() && s.cfSecret.isNotBlank()) s.cfId.trim() to s.cfSecret.trim() else null
    }

    private fun run(block: suspend () -> Found?) {
        val s = _ui.value
        if (s.testing) return
        if (s.cfId.isBlank() != s.cfSecret.isBlank()) {
            _ui.value = s.copy(error = "Enter both the Cloudflare client id and the secret, or neither", security = true)
            return
        }
        _ui.value = s.copy(testing = true, error = null, found = null)
        viewModelScope.launch {
            val found = try {
                block()
            } catch (e: Exception) {
                _ui.value = _ui.value.copy(error = ApiErrors.message(e))
                null
            }
            _ui.value = _ui.value.copy(testing = false, found = found)
        }
    }

    private fun failed(r: ConnectResult): Found? {
        if (r is ConnectResult.Failed) _ui.value = _ui.value.copy(error = r.message)
        return null
    }

    /** "Test connection" with the typed address. */
    fun testAddress() {
        when (val p = ServerAddress.parse(_ui.value.address)) {
            is ServerAddress.Parsed.Invalid -> _ui.value = _ui.value.copy(error = p.message, found = null)
            is ServerAddress.Parsed.Ok -> run {
                when (val r = ConnectCheck.test(http, p.url.toString(), cloudflare())) {
                    is ConnectResult.Ok -> Found(HubChoice.saved(r.url, r.hello), r.url)
                    else -> failed(r)
                }
            }
        }
    }

    /** A hub found on this network. */
    fun pick(f: HubDiscovery.Found) {
        _ui.value = _ui.value.copy(address = f.localUrl)
        run {
            when (val r = ConnectCheck.test(http, f.localUrl, null, f.id)) {
                is ConnectResult.Ok -> HubChoice.saved(r.url, r.hello).let { Found(it.copy(outsideUrl = it.outsideUrl ?: f.outsideUrl), r.url) }
                else -> failed(r)
            }
        }
    }

    /** A scanned QR code or an opened newman://connect link (a plain address is tested as typed). */
    fun scanned(text: String) {
        val link = ConnectLink.parse(text)
        if (link == null) {
            if (!text.trim().startsWith("newman:", ignoreCase = true) && ServerAddress.normalize(text) != null) {
                _ui.value = _ui.value.copy(address = text.trim())
                testAddress()
            } else _ui.value = _ui.value.copy(error = "That isn't a Newman connect code", found = null)
            return
        }
        if (_ui.value.step != Step.CONNECT) go(Step.CONNECT)
        _ui.value = _ui.value.copy(address = link.outsideUrl ?: link.localUrl.orEmpty())
        run {
            when (val r = ConnectCheck.testHub(http, link.localUrl, link.outsideUrl, cloudflare(), link.id)) {
                is ConnectResult.Ok -> Found(HubChoice.saved(r.url, r.hello, link), r.url)
                else -> failed(r)
            }
        }
    }

    /** "Continue": keep this hub and go to sign-in. */
    fun useFound() {
        val f = _ui.value.found ?: return
        val cf = cloudflare()
        store.setCloudflare(cf?.first, cf?.second)
        store.saveHub(f.hub, f.url)
        account.connectionChanged()
        selector.recheck(500)
        _ui.value = _ui.value.copy(hubName = f.hub.name)
        go(Step.SIGN_IN)
        checkReachable()
    }

    /** Before sign-in: say where we are ("at home" / "from outside") or why the hub can't be reached. */
    fun checkReachable() = viewModelScope.launch {
        try {
            val s = hubCall { api.authState() }
            val where = if (s.where == "home") "Reached Newman at home" else if (s.certificate) "Reached Newman securely from outside" else "Reached Newman from outside"
            _ui.value = _ui.value.copy(note = where)
        } catch (e: Exception) {
            _ui.value = _ui.value.copy(note = ApiErrors.message(e))
        }
    }

    fun signIn(onDone: () -> Unit) {
        val s = _ui.value
        if (s.busy) return
        if (s.step == Step.SIGN_IN && (s.username.isBlank() || s.password.isEmpty())) {
            _ui.value = s.copy(error = "Enter your username and password")
            return
        }
        _ui.value = s.copy(busy = true, error = null)
        viewModelScope.launch {
            val code = if (s.step == Step.CODE) s.code else null
            when (val r = account.login(s.username, s.password, code)) {
                is LoginResult.Success -> {
                    _ui.value = _ui.value.copy(busy = false, password = "", code = "")
                    onDone()
                }
                is LoginResult.NeedsCode -> _ui.value = _ui.value.copy(busy = false, step = Step.CODE, error = r.error, code = "")
                is LoginResult.Failed -> _ui.value = _ui.value.copy(busy = false, error = r.message)
            }
        }
    }

    override fun onCleared() {
        discovery.stop()
    }

    companion object {
        const val ARG_CHANGING = "changing"
        const val ARG_LINK = "link"
    }
}

/**
 * [onClose], [leaveHub], [onChanged]: only when changing server while signed in (vm.changing).
 */
@Composable
fun OnboardingScreen(
    onDone: () -> Unit,
    connectLink: String? = null,
    onLinkUsed: () -> Unit = {},
    onClose: () -> Unit = {},
    leaveHub: suspend () -> Unit = {},
    onChanged: (sameHub: Boolean, name: String?) -> Unit = { _, _ -> },
    vm: OnboardingViewModel = hiltViewModel(),
) {
    val s by vm.ui.collectAsState()
    val cert by MTLSStore.info.collectAsState()
    val hubs by vm.discovery.found.collectAsState()
    val searching by vm.discovery.searching.collectAsState()
    val certImport = rememberCertificateImport { msg -> vm.update { it.copy(note = msg, error = null) } }
    val scan = rememberLauncherForActivityResult(ScanContract()) { r -> r.contents?.let { vm.scanned(it) } }
    BackHandler(enabled = vm.changing || s.step != OnboardingViewModel.Step.CONNECT || vm.hasHub) { if (vm.changing) onClose() else vm.back() }
    LaunchedEffect(connectLink) {
        if (connectLink != null) {
            vm.scanned(connectLink)
            onLinkUsed()
        }
    }
    val connecting = s.step == OnboardingViewModel.Step.CONNECT
    DisposableEffect(connecting) {
        if (connecting) vm.discovery.start()
        onDispose { vm.discovery.stop() }
    }

    NightBackground(Modifier.fillMaxSize()) {
        Column(
            Modifier.fillMaxSize().systemBarsPadding().imePadding().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 24.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            if (vm.changing) BackPill(onClick = onClose)
            else Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                AppLogo(44.dp)
                Text("Newman", style = T.title)
            }
            Gap(8.dp)
            when (s.step) {
                OnboardingViewModel.Step.CONNECT -> {
                    if (vm.changing) Heading("Server", "New addresses for ${s.hubName ?: "your Newman"}, or another Newman: pick it, scan its QR code, or type its address.")
                    else Heading("Connect to your Newman", "Pick your hub, scan its QR code, or type its address.")
                    Text("ON THIS NETWORK", style = T.caps, modifier = Modifier.semantics { heading(); contentDescription = "On this network" })
                    if (hubs.isEmpty()) {
                        Text(if (searching) "Looking…" else "No Newman found on this network", style = T.small.copy(color = N.Muted))
                    }
                    hubs.forEach { h ->
                        GlassCard(Modifier.fillMaxWidth(), onClick = { vm.pick(h) }) {
                            Text(h.name, style = T.label)
                            Text(h.localUrl, style = T.tiny)
                        }
                    }
                    GlassButton("Scan QR code", Modifier.fillMaxWidth(), height = 48.dp) {
                        scan.launch(
                            ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE).setPrompt("Scan the Newman QR code")
                                .setBeepEnabled(false).setOrientationLocked(false),
                        )
                    }
                    Text("ADDRESS", style = T.caps, modifier = Modifier.semantics { heading(); contentDescription = "Address" })
                    NTextField(
                        s.address, { v -> vm.update { it.copy(address = v, error = null, found = null) } }, "Address",
                        keyboard = KeyboardType.Uri, placeholder = "https://home.example.com",
                    )
                    GlassButton(if (s.security) "Connection security  ▴" else "Connection security  ▾", Modifier.fillMaxWidth(), height = 44.dp) {
                        vm.update { it.copy(security = !it.security) }
                    }
                    if (s.security) {
                        GlassCard(Modifier.fillMaxWidth()) {
                            Text("Client certificate", style = T.label)
                            Text(certificateSummary(cert) ?: "None on this phone", style = T.tiny)
                            Gap(8.dp)
                            GlassButton(if (cert == null) "Choose a .p12 file" else "Replace certificate", Modifier.fillMaxWidth(), icon = "key", height = 44.dp) { certImport() }
                            if (cert != null) {
                                Gap(8.dp)
                                GlassButton("Remove certificate", Modifier.fillMaxWidth(), height = 40.dp) { MTLSStore.clearCertificate() }
                            }
                            Gap(14.dp)
                            Text("Cloudflare Access service token", style = T.label)
                            NTextField(s.cfId, { v -> vm.update { it.copy(cfId = v, error = null) } }, "Client id (CF-Access-Client-Id)")
                            NTextField(s.cfSecret, { v -> vm.update { it.copy(cfSecret = v, error = null) } }, "Client secret", password = true)
                        }
                    }
                    Note(s.note)
                    Problem(s.error)
                    val f = s.found
                    if (f != null) {
                        GlassCard(Modifier.fillMaxWidth(), background = N.TealBg, border = N.TealBorder) {
                            Text("Found ${f.hub.name ?: "Newman"}", style = T.label)
                            Text(f.url, style = T.tiny)
                        }
                        if (vm.changing) {
                            val same = vm.isSameHub(f)
                            if (!same) Text("This phone will sign out of ${s.hubName ?: "the Newman it uses now"}, then you sign in to ${f.hub.name ?: "the new one"}.",
                                style = T.small.copy(color = N.Muted))
                            PrimaryButton(if (s.busy) "Saving…" else if (same) "Save" else "Sign out and switch", busy = s.busy) {
                                vm.saveChange(leaveHub) { sameHub -> onChanged(sameHub, f.hub.name) }
                            }
                        } else PrimaryButton("Continue") { vm.useFound() }
                    } else {
                        PrimaryButton(if (s.testing) "Checking…" else "Test connection", busy = s.testing, enabled = s.address.isNotBlank() || s.testing) { vm.testAddress() }
                    }
                }
                OnboardingViewModel.Step.SIGN_IN -> {
                    Heading("Sign in", "Use your Newman account. This phone stays signed in until you sign it out.")
                    Text(s.hubName ?: "Newman", style = T.tiny)
                    NTextField(s.username, { v -> vm.update { it.copy(username = v, error = null) } }, "Username")
                    NTextField(s.password, { v -> vm.update { it.copy(password = v, error = null) } }, "Password", password = true)
                    Note(s.note)
                    Problem(s.error)
                    PrimaryButton(if (s.busy) "Signing in…" else "Sign in", busy = s.busy) { vm.signIn(onDone) }
                    GlassButton("Server", Modifier.fillMaxWidth(), height = 44.dp) { vm.go(OnboardingViewModel.Step.CONNECT) }
                }
                OnboardingViewModel.Step.CODE -> {
                    Heading("Enter your code", "Type the 6-digit code from your authenticator app.")
                    androidx.compose.material3.OutlinedTextField(
                        value = s.code,
                        onValueChange = { v -> vm.update { it.copy(code = v.filter { c -> c.isDigit() }.take(6), error = null) } },
                        label = { Text("6-digit code") },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                        modifier = Modifier.fillMaxWidth(),
                        textStyle = T.number,
                    )
                    Problem(s.error)
                    PrimaryButton(if (s.busy) "Checking…" else "Sign in", enabled = s.code.length == 6, busy = s.busy) { vm.signIn(onDone) }
                    GlassButton("Back", Modifier.fillMaxWidth()) { vm.go(OnboardingViewModel.Step.SIGN_IN) }
                }
            }
        }
    }
}

/** The app icon's artwork (the launcher's adaptive foreground, cropped to its rounded square). */
@Composable
fun AppLogo(size: androidx.compose.ui.unit.Dp) {
    androidx.compose.foundation.layout.Box(Modifier.size(size).clip(RoundedCornerShape(size * 0.225f))) {
        Image(
            painterResource(R.mipmap.ic_launcher_foreground), null,
            Modifier.size(size).graphicsLayer(scaleX = 1.8f, scaleY = 1.8f),
        )
    }
}

@Composable
private fun Heading(title: String, text: String) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(title, style = T.display, modifier = Modifier.heading())
        Text(text, style = T.body.copy(color = N.Muted))
    }
}

@Composable
private fun Problem(error: String?) {
    if (error != null) Text(error, style = T.small.copy(color = N.CoralText), modifier = Modifier.semantics { liveRegion = LiveRegionMode.Assertive })
}

@Composable
private fun Note(note: String?) {
    if (note != null) Text(note, style = T.small.copy(color = N.Teal), modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
}
