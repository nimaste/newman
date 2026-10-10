package ca.nimaste.newman.data

import android.content.Context
import android.os.Build
import android.util.Log
import ca.nimaste.newman.BuildConfig
import ca.nimaste.newman.core.api.ApiErrors
import ca.nimaste.newman.core.api.LoginResult
import ca.nimaste.newman.core.api.NewmanApi
import ca.nimaste.newman.core.api.hubCall
import ca.nimaste.newman.core.hub.HubRepository
import ca.nimaste.newman.core.model.ActionRequest
import ca.nimaste.newman.core.model.LoginRequest
import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.model.PushCategory
import ca.nimaste.newman.core.model.RegisterRequest
import ca.nimaste.newman.core.push.ActionRoutes
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.core.widgets.WidgetPushToken
import ca.nimaste.newman.location.LocationSharing
import ca.nimaste.newman.widgets.WidgetSync
import ca.nimaste.newman.widgets.LauncherShortcuts
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.builtins.ListSerializer
import okhttp3.OkHttpClient
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.coroutines.resume

/** Signing this phone in and out, and its push registration. */
@Singleton
class Account @Inject constructor(
    @ApplicationContext private val context: Context,
    private val api: NewmanApi,
    private val store: ConnectionStore,
    private val hub: HubRepository,
    private val http: OkHttpClient,
    private val scope: CoroutineScope,
    private val sharing: LocationSharing,
    private val widgets: WidgetSync,
    private val shortcuts: LauncherShortcuts,
) {
    val signedIn: Boolean get() = !store.token.isNullOrEmpty() && !store.serverUrl.isNullOrEmpty()

    /** "Pixel 8" - how this phone appears in the hub's list of signed-in devices. */
    val deviceName: String get() = Build.MODEL?.takeIf { it.isNotBlank() } ?: "Android phone"

    suspend fun login(username: String, password: String, code: String?): LoginResult {
        val result = try {
            LoginResult.from(api.login(LoginRequest(username.trim().lowercase(), password,
                code = code?.filter { it.isDigit() }?.ifEmpty { null }, kind = "app", deviceName = deviceName)))
        } catch (e: Exception) {
            LoginResult.Failed(ApiErrors.message(e))
        }
        if (result is LoginResult.Success) {
            store.token = result.token
            store.userName = result.user?.name
            http.connectionPool.evictAll()
            hub.reconnect()
            scope.launch { registerPush(); refreshPushCategories(); widgets.refresh() }
            shortcuts.updateSoon()
        }
        return result
    }

    /** Sign out of this phone: ends the hub session, keeps the server and the certificate. */
    suspend fun signOut() {
        runCatching { hubCall { api.logout() } }
        sharing.signedOut()
        store.signOut()
        hub.stop()
        widgets.signedOut() // the hub drops this sign-in's widget push token with it
        shortcuts.signedOut()
    }

    /** The hub no longer knows this session (revoked, expired): forget it locally. */
    fun forgetSession() {
        sharing.signedOut()
        store.signOut()
        hub.stop()
        widgets.signedOut()
        shortcuts.signedOut()
    }

    /** Re-apply connection changes (server, certificate, Cloudflare token) to open connections. */
    fun connectionChanged() {
        http.connectionPool.evictAll()
        if (signedIn) hub.reconnect()
    }

    // -- push -------------------------------------------------------------------------------------

    val pushAvailable: Boolean get() = BuildConfig.FCM_ENABLED && FirebaseApp.getApps(context).isNotEmpty()

    /** Register (or refresh) this phone's FCM token with POST /api/devices/register. */
    suspend fun registerPush(token: String? = null) {
        if (!signedIn || !pushAvailable) return
        val fcm = token ?: fetchFcmToken() ?: return
        try {
            hubCall {
                api.registerDevice(RegisterRequest(token = fcm, platform = "android", deviceId = store.deviceId,
                    friendlyName = deviceName, deviceModel = "${Build.MANUFACTURER} ${Build.MODEL}".trim()))
            }
            store.pushToken = fcm
        } catch (e: Exception) {
            Log.w(TAG, "Push registration failed: ${ApiErrors.message(e)}")
        }
        // The same token reloads the home-screen widgets (FCM data message {"type": "widgets"}).
        runCatching { api.widgetPushToken(WidgetPushToken(token = fcm, kind = "android", deviceId = store.deviceId)) }
    }

    private suspend fun fetchFcmToken(): String? = suspendCancellableCoroutine { cont ->
        try {
            FirebaseMessaging.getInstance().token.addOnCompleteListener { t ->
                cont.resume(if (t.isSuccessful) t.result else null)
            }
        } catch (e: Exception) {
            cont.resume(null)
        }
    }

    /** GET /api/push/categories, cached for notifications that name a category without buttons. */
    suspend fun refreshPushCategories() {
        if (!signedIn) return
        runCatching { hubCall { api.pushCategories() } }.onSuccess {
            store.pushCategoriesJson = NewmanJson.encodeToString(ListSerializer(PushCategory.serializer()), it)
        }
    }

    /**
     * Press a notification button: POST {button, subject, deviceId, exp, token} to the hub's address in
     * use, then its other address, and the push's [actionUrl] last ([ActionRoutes]) - through the
     * same client as every call (client certificate, Cloudflare headers), each try up to 8 s. The
     * first answer from the hub itself (done, or refused with its reason) is final.
     */
    suspend fun pressAction(actionUrl: String?, request: ActionRequest): Result<Unit> {
        var last: Result<Unit> = Result.failure(IllegalStateException("Choose your Newman address first"))
        for (url in ActionRoutes.urls(store.snapshot, actionUrl)) {
            val status: Int? = try {
                val res = withTimeout(ActionRoutes.ATTEMPT_MS) { api.pressAction(url, request) }
                if (res.isSuccessful) return Result.success(Unit)
                last = Result.failure(IllegalStateException(ApiErrors.errorText(res.errorBody()?.string()) ?: ApiErrors.httpFallback(res.code())))
                res.code()
            } catch (e: TimeoutCancellationException) {
                last = Result.failure(IllegalStateException("The Newman hub took too long to answer"))
                null
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                last = Result.failure(IllegalStateException(ApiErrors.message(e)))
                null
            }
            Log.i(TAG, "Button ${request.button} at $url: ${status ?: "no answer"}")
            if (!ActionRoutes.tryNext(status)) break
        }
        return last
    }

    companion object {
        private const val TAG = "NewmanAccount"
    }
}
