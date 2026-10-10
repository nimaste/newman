package ca.nimaste.newman.core.api

import ca.nimaste.newman.core.model.LoginResponse
import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.model.User
import ca.nimaste.newman.core.model.asStringOrNull
import ca.nimaste.newman.core.model.bool
import ca.nimaste.newman.core.model.string
import ca.nimaste.newman.core.net.NoServerException
import kotlinx.serialization.json.JsonObject
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException

/** The hub's error replies are {"error": "…"} (sometimes {"ok": false, "error"}); show them as-is. */
object ApiErrors {
    fun errorObject(body: String?): JsonObject? = try {
        body?.takeIf { it.isNotBlank() }?.let { NewmanJson.parseToJsonElement(it) as? JsonObject }
    } catch (e: Exception) {
        null
    }

    fun errorText(body: String?): String? = errorObject(body)?.string("error")?.takeIf { it.isNotBlank() }

    /** A message a person can act on, for any failure. */
    fun message(t: Throwable): String = when (t) {
        is HubException -> t.error ?: httpFallback(t.code)
        is HttpException -> errorText(t.response()?.errorBody()?.string()) ?: httpFallback(t.code())
        is NoServerException -> t.message ?: "Choose your Newman address first"
        is UnknownHostException -> "Can't find the Newman hub at that address"
        is SocketTimeoutException -> "The Newman hub didn't answer in time"
        is SSLException -> "A secure connection to the hub failed - check the address and the certificate"
        is IOException -> "Can't reach the Newman hub"
        else -> t.message ?: "Something went wrong"
    }

    fun httpFallback(code: Int): String = when (code) {
        401 -> "Please sign in"
        403 -> "Your account can't do that"
        404 -> "Not found"
        429 -> "Too many attempts - try again later"
        in 500..599 -> "The Newman hub had a problem ($code)"
        else -> "The Newman hub said no ($code)"
    }

    /** True for the hub's "your session is gone" answer: 401 {"error": "Please sign in", "signIn": true}. */
    fun isSignedOut(t: Throwable): Boolean =
        t is HubException && t.code == 401 && errorObject(t.body)?.bool("signIn") == true
}

/** An HTTP error from the hub with its body read once ([error] = the hub's own words). */
class HubException(val code: Int, val body: String?) : IOException("HTTP $code") {
    val error: String? = ApiErrors.errorText(body)
    override val message: String get() = error ?: ApiErrors.httpFallback(code)
}

/** Runs a Retrofit call, turning HTTP errors into [HubException]. */
suspend fun <T> hubCall(block: suspend () -> T): T = try {
    block()
} catch (e: HttpException) {
    throw HubException(e.code(), e.response()?.errorBody()?.string())
}

/** For calls returning Response<T>: the body, or a [HubException]. */
fun <T> Response<T>.orThrow(): T? {
    if (isSuccessful) return body()
    throw HubException(code(), errorBody()?.string())
}

/** The outcome of POST /api/auth/login. */
sealed interface LoginResult {
    data class Success(val token: String, val user: User?) : LoginResult
    /** 401 {needs: "totp"}: ask for the 6-digit code; [error] when a code was wrong. */
    data class NeedsCode(val error: String?) : LoginResult
    /** 401/403/429…: show [message] as-is. */
    data class Failed(val message: String) : LoginResult

    companion object {
        fun from(code: Int, body: LoginResponse?, errorBody: String?): LoginResult {
            if (code in 200..299) {
                val token = body?.token
                return if (!token.isNullOrEmpty()) Success(token, body.user)
                else Failed("The hub didn't give this phone a session")
            }
            val err = ApiErrors.errorObject(errorBody)
            if (code == 401 && err != null && err["needs"].asStringOrNull == "totp") return NeedsCode(err.string("error"))
            return Failed(err?.string("error") ?: ApiErrors.httpFallback(code))
        }

        fun from(response: Response<LoginResponse>): LoginResult =
            from(response.code(), response.body(), response.errorBody()?.string())
    }
}
