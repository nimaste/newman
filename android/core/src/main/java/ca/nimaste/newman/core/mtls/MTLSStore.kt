package ca.nimaste.newman.core.mtls

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.Base64

/**
 * EncryptedSharedPreferences-backed storage for the optional client certificate (the raw .p12,
 * base64, plus its password). Its own file, separate from the sign-in: signing out never removes
 * the certificate, which is what lets the app reach the hub from outside at all.
 * Initialised once from Application.onCreate (GChat's pattern).
 */
object MTLSStore {
    private const val FILE = "newman_mtls"
    private const val KEY_P12_B64 = "p12_base64"
    private const val KEY_PASSWORD = "p12_password"
    private const val KEY_NAME = "cert_name"
    private const val KEY_NOT_AFTER = "cert_not_after"

    data class Info(val name: String, val notAfterMillis: Long)

    @Volatile private var prefs: SharedPreferences? = null
    private val _info = MutableStateFlow<Info?>(null)
    /** The installed certificate (null = none); changes on import / remove. */
    val info: StateFlow<Info?> = _info.asStateFlow()

    /** Bumped on every change, so WebViews can forget a cached client-certificate choice. */
    @Volatile var version: Int = 0
        private set

    fun init(context: Context) {
        if (prefs != null) return
        synchronized(this) {
            if (prefs != null) return
            val ctx = context.applicationContext
            prefs = try {
                val key = MasterKey.Builder(ctx).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
                EncryptedSharedPreferences.create(
                    ctx, FILE, key,
                    EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                    EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
                )
            } catch (e: Exception) {
                ctx.getSharedPreferences("${FILE}_fallback", Context.MODE_PRIVATE)
            }
            _info.value = readInfo()
        }
    }

    private fun readInfo(): Info? {
        val p = prefs ?: return null
        if (p.getString(KEY_P12_B64, null) == null) return null
        return Info(p.getString(KEY_NAME, null) ?: "Client certificate", p.getLong(KEY_NOT_AFTER, 0L))
    }

    val isConfigured: Boolean get() = prefs?.getString(KEY_P12_B64, null) != null && prefs?.getString(KEY_PASSWORD, null) != null

    fun getP12Bytes(): ByteArray? = prefs?.getString(KEY_P12_B64, null)?.let {
        try {
            Base64.getDecoder().decode(it)
        } catch (e: IllegalArgumentException) {
            null
        }
    }

    fun getPassword(): String? = prefs?.getString(KEY_PASSWORD, null)

    /** Store a certificate that [P12Validator] accepted. */
    fun setCertificate(p12: ByteArray, password: String, valid: P12Validator.Result.Valid) {
        prefs?.edit()
            ?.putString(KEY_P12_B64, Base64.getEncoder().encodeToString(p12))
            ?.putString(KEY_PASSWORD, password)
            ?.putString(KEY_NAME, valid.name)
            ?.putLong(KEY_NOT_AFTER, valid.notAfterMillis)
            ?.commit()
        version++
        _info.value = readInfo()
    }

    fun clearCertificate() {
        prefs?.edit()?.remove(KEY_P12_B64)?.remove(KEY_PASSWORD)?.remove(KEY_NAME)?.remove(KEY_NOT_AFTER)?.commit()
        version++
        _info.value = null
    }

    /** The private key and chain to present (WebView's ClientCertRequest needs them directly). */
    fun keyAndChain(): Pair<java.security.PrivateKey, Array<java.security.cert.X509Certificate>>? {
        val p12 = getP12Bytes() ?: return null
        val pw = getPassword() ?: return null
        return P12Validator.keyAndChain(p12, pw)
    }
}
