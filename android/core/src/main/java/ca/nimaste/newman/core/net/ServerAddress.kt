package ca.nimaste.newman.core.net

import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * The hub's address as typed by a person: "home.example.com", "https://home.example.com/",
 * "http://10.1.1.2:8093". Plain http is allowed only for private (home network) addresses.
 */
object ServerAddress {
    sealed interface Parsed {
        data class Ok(val url: HttpUrl) : Parsed
        data class Invalid(val message: String) : Parsed
    }

    fun parse(input: String): Parsed {
        var s = input.trim()
        if (s.isEmpty()) return Parsed.Invalid("Enter your Newman address")
        if (!s.contains("://")) s = "https://$s"
        val url = s.toHttpUrlOrNull() ?: return Parsed.Invalid("That doesn't look like a web address")
        if (url.host.isEmpty()) return Parsed.Invalid("That doesn't look like a web address")
        if (!url.isHttps && !isPrivateHost(url.host)) {
            return Parsed.Invalid("Use https:// for an address outside your home network")
        }
        // Keep any path prefix (a hub behind /newman/), always ending in "/"; drop query and fragment.
        val b = url.newBuilder().query(null).fragment(null)
        val path = url.encodedPath
        if (!path.endsWith("/")) b.encodedPath("$path/")
        return Parsed.Ok(b.build())
    }

    /** Normalised string form, or null when invalid. */
    fun normalize(input: String): String? = (parse(input) as? Parsed.Ok)?.url?.toString()

    /** RFC 1918 / loopback / link-local IPv4, private IPv6, and names that only exist at home. */
    fun isPrivateHost(host: String): Boolean {
        val h = host.trim().trim('[', ']').lowercase()
        if (h == "localhost") return true
        val homeSuffixes = listOf(".local", ".lan", ".home.arpa", ".internal", ".localdomain")
        if (homeSuffixes.any { h.endsWith(it) }) return true
        val v4 = h.split('.')
        if (v4.size == 4 && v4.all { p -> p.toIntOrNull()?.let { it in 0..255 } == true }) {
            val (a, b) = v4[0].toInt() to v4[1].toInt()
            return a == 10 || a == 127 || (a == 172 && b in 16..31) || (a == 192 && b == 168) || (a == 169 && b == 254)
        }
        if (h.contains(':')) {
            return h == "::1" || h.startsWith("fc") || h.startsWith("fd") || h.startsWith("fe8") ||
                h.startsWith("fe9") || h.startsWith("fea") || h.startsWith("feb")
        }
        return false
    }
}
