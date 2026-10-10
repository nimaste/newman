package ca.nimaste.newman.core.mtls

import java.io.ByteArrayInputStream
import java.io.IOException
import java.security.KeyStore
import java.security.PrivateKey
import java.security.UnrecoverableKeyException
import java.security.cert.X509Certificate
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Checks a .p12 (PKCS#12) file before it is stored: it must open with the password, hold a
 * private key with its certificate, and be valid today. Pure JVM, so it is unit-tested.
 */
object P12Validator {

    sealed interface Result {
        data class Valid(val name: String, val notAfterMillis: Long, val issuer: String) : Result
        data class Invalid(val message: String) : Result
    }

    fun validate(bytes: ByteArray, password: String, now: Date = Date()): Result {
        if (bytes.isEmpty()) return Result.Invalid("That file is empty")
        val ks = try {
            KeyStore.getInstance("PKCS12").apply { load(ByteArrayInputStream(bytes), password.toCharArray()) }
        } catch (e: IOException) {
            val wrongPassword = e.cause is UnrecoverableKeyException ||
                (e.message ?: "").contains("password", ignoreCase = true) ||
                (e.message ?: "").contains("mac", ignoreCase = true)
            return Result.Invalid(
                if (wrongPassword) "That password doesn't open this certificate"
                else "That isn't a .p12 certificate file",
            )
        } catch (e: Exception) {
            return Result.Invalid("That isn't a .p12 certificate file")
        }
        val alias = ks.aliases().toList().firstOrNull { ks.isKeyEntry(it) }
            ?: return Result.Invalid("This file has a certificate but no private key - export it together with its key")
        val key = try {
            ks.getKey(alias, password.toCharArray()) as? PrivateKey
        } catch (e: UnrecoverableKeyException) {
            return Result.Invalid("That password doesn't open this certificate's key")
        } catch (e: Exception) {
            null
        } ?: return Result.Invalid("This file's private key can't be read")
        val chain = ks.getCertificateChain(alias)?.mapNotNull { it as? X509Certificate }.orEmpty()
        val cert = chain.firstOrNull() ?: return Result.Invalid("This file has a key but no certificate")
        if (key.algorithm.isNullOrEmpty()) return Result.Invalid("This file's private key can't be read")
        val day = SimpleDateFormat("MMMM d, yyyy", Locale.getDefault())
        if (now.after(cert.notAfter)) return Result.Invalid("This certificate expired on ${day.format(cert.notAfter)}")
        if (now.before(cert.notBefore)) return Result.Invalid("This certificate isn't valid until ${day.format(cert.notBefore)}")
        return Result.Valid(
            name = commonName(cert.subjectX500Principal.name) ?: cert.subjectX500Principal.name,
            notAfterMillis = cert.notAfter.time,
            issuer = commonName(cert.issuerX500Principal.name) ?: cert.issuerX500Principal.name,
        )
    }

    /** The key and its chain, or null when the file no longer opens. */
    fun keyAndChain(bytes: ByteArray, password: String): Pair<PrivateKey, Array<X509Certificate>>? = try {
        val ks = KeyStore.getInstance("PKCS12").apply { load(ByteArrayInputStream(bytes), password.toCharArray()) }
        val alias = ks.aliases().toList().first { ks.isKeyEntry(it) }
        val key = ks.getKey(alias, password.toCharArray()) as PrivateKey
        val chain = ks.getCertificateChain(alias).map { it as X509Certificate }.toTypedArray()
        key to chain
    } catch (e: Exception) {
        null
    }

    /** The CN of an RFC 2253 name ("CN=Chris phone,O=Newman" -> "Chris phone"). */
    internal fun commonName(dn: String): String? =
        Regex("(?:^|,)\\s*CN=((?:\\\\.|[^,])+)", RegexOption.IGNORE_CASE).find(dn)
            ?.groupValues?.get(1)?.replace(Regex("\\\\(.)"), "$1")?.trim()
}
