package ca.nimaste.newman.core.api

import ca.nimaste.newman.core.model.ActionRequest
import ca.nimaste.newman.core.model.ActivityEntry
import ca.nimaste.newman.core.model.AuthState
import ca.nimaste.newman.core.model.CameraAlert
import ca.nimaste.newman.core.model.CommandRequest
import ca.nimaste.newman.core.model.Dashboard
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.LoginRequest
import ca.nimaste.newman.core.model.LoginResponse
import ca.nimaste.newman.core.model.Meta
import ca.nimaste.newman.core.model.NewmanJson
import ca.nimaste.newman.core.model.OkResponse
import ca.nimaste.newman.core.model.PushCategory
import ca.nimaste.newman.core.model.RegisterRequest
import ca.nimaste.newman.core.model.RegisterResponse
import ca.nimaste.newman.core.net.HubInterceptor
import ca.nimaste.newman.core.people.LocationEnable
import ca.nimaste.newman.core.people.LocationReport
import ca.nimaste.newman.core.people.LocationSettings
import ca.nimaste.newman.core.people.Person
import ca.nimaste.newman.core.people.Place
import ca.nimaste.newman.core.people.ProfileUpdate
import ca.nimaste.newman.core.people.ReportReply
import ca.nimaste.newman.core.today.DismissRequest
import ca.nimaste.newman.core.today.SnoozeRequest
import ca.nimaste.newman.core.today.TodayBriefing
import ca.nimaste.newman.core.widgets.WidgetDevicesResponse
import ca.nimaste.newman.core.widgets.WidgetHome
import ca.nimaste.newman.core.widgets.WidgetPushToken
import ca.nimaste.newman.core.widgets.WidgetStateResponse
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.RequestBody
import okhttp3.ResponseBody
import retrofit2.Response
import retrofit2.Retrofit
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query
import retrofit2.http.Url

/** The hub's API (server/newman/api.py). Paths are relative to the server the person chose. */
interface NewmanApi {
    @GET("api/meta")
    suspend fun meta(): Meta

    @GET("api/auth/state")
    suspend fun authState(): AuthState

    @POST("api/auth/login")
    suspend fun login(@Body body: LoginRequest, @Header(HubInterceptor.NO_AUTH) noAuth: String = "1"): Response<LoginResponse>

    @POST("api/auth/logout")
    suspend fun logout(): Response<OkResponse>

    @GET("api/entities")
    suspend fun entities(): List<Entity>

    @GET("api/dashboards/current")
    suspend fun currentDashboard(): Dashboard

    /** The signed-in person's phone layout (PhoneGrid.outcome reads it; 404 on older hubs). */
    @GET("api/phone-layout")
    suspend fun phoneLayout(): Response<ResponseBody>

    @POST("api/entities/{id}/command")
    suspend fun command(@Path("id") entityId: String, @Body body: CommandRequest): Response<OkResponse>

    @GET("api/events")
    suspend fun events(@Query("limit") limit: Int = 100, @Query("before") before: Double? = null): List<ActivityEntry>

    @GET("api/alerts")
    suspend fun alerts(@Query("limit") limit: Int = 50, @Query("before") before: Double? = null): List<CameraAlert>

    /** A device's own alerts: [path] is the entity's `alerts` link ("api/entities/x/alerts"). */
    @GET
    suspend fun entityAlerts(@Url path: String, @Query("limit") limit: Int = 50): List<CameraAlert>

    @POST("api/devices/register")
    suspend fun registerDevice(@Body body: RegisterRequest): RegisterResponse

    @GET("api/push/categories")
    suspend fun pushCategories(): List<PushCategory>

    @GET("api/push/categories")
    suspend fun pushCategoriesRaw(): JsonElement

    /** A notification button; [url] is the payload's absolute `action_url`. */
    @POST
    suspend fun pressAction(@Url url: String, @Body body: ActionRequest, @Header(HubInterceptor.NO_AUTH) noAuth: String = "1"): Response<OkResponse>

    // -- home-screen widgets & Quick Settings tiles (server/newman/widgets.py) ------------------------

    /** Every device a widget can show, for the pickers. */
    @GET("api/widgets/devices")
    suspend fun widgetDevices(): WidgetDevicesResponse

    /** These devices now ([ids] comma-separated, in the widget's order). */
    @GET("api/widgets/state")
    suspend fun widgetState(@Query("ids") ids: String): WidgetStateResponse

    /** The "Home status" widget: all secure or not, what isn't and the command that fixes it. */
    @GET("api/widgets/home")
    suspend fun widgetHome(): WidgetHome

    @POST("api/widgets/push-token")
    suspend fun widgetPushToken(@Body body: WidgetPushToken): Response<OkResponse>

    // -- live statuses (server/newman/live.py): the signed-in person's, for this phone ------------------

    @GET("api/live/current")
    suspend fun liveCurrent(@Query("deviceId") deviceId: String? = null): ca.nimaste.newman.core.livestatus.LiveUpdate

    // -- Today & upkeep (the hub records the signed-in person as who did it) -----------------------

    @GET("api/today")
    suspend fun today(): TodayBriefing

    @POST("api/upkeep/{id}/done")
    suspend fun upkeepDone(@Path("id") id: String, @Body body: JsonObject = JsonObject(emptyMap())): Response<Unit>

    @POST("api/upkeep/{id}/undo")
    suspend fun upkeepUndo(@Path("id") id: String, @Body body: JsonObject = JsonObject(emptyMap())): Response<Unit>

    @POST("api/upkeep/{id}/snooze")
    suspend fun upkeepSnooze(@Path("id") id: String, @Body body: SnoozeRequest = SnoozeRequest(1)): Response<Unit>

    @POST("api/today/dismiss")
    suspend fun todayDismiss(@Body body: DismissRequest): Response<Unit>

    // -- people & location ----------------------------------------------------------------------

    @GET("api/people")
    suspend fun people(): List<Person>

    /**
     * Tap-to-refresh: the hub asks that person's phone for ONE fresh fix. 202 {"requested", "nextAllowedAt"},
     * 429 {"error", "nextAllowedAt"} when it was asked less than 2 minutes ago (PersonRefresh reads both).
     */
    @POST("api/people/{id}/refresh")
    suspend fun refreshPerson(@Path("id") personId: String, @Body body: JsonObject = JsonObject(emptyMap())): Response<ResponseBody>

    /** The signed-in person's colour. */
    @PUT("api/me/profile")
    suspend fun setProfile(@Body body: ProfileUpdate): Response<ResponseBody>

    /** The signed-in person's photo (image/jpeg body; the hub crops it square, 256 px). */
    @PUT("api/me/photo")
    suspend fun uploadPhoto(@Body body: RequestBody): Response<ResponseBody>

    @DELETE("api/me/photo")
    suspend fun deletePhoto(): Response<ResponseBody>

    @GET("api/places")
    suspend fun places(): List<Place>

    @GET("api/me/location")
    suspend fun myLocation(): LocationSettings

    @PUT("api/me/location")
    suspend fun setMyLocation(@Body body: LocationEnable): LocationSettings

    @POST("api/me/location/report")
    suspend fun reportLocation(@Body body: LocationReport): ReportReply

    companion object {
        fun create(client: OkHttpClient): NewmanApi = Retrofit.Builder()
            .baseUrl(HubInterceptor.PLACEHOLDER_BASE)
            .client(client)
            .addConverterFactory(NewmanJson.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(NewmanApi::class.java)
    }
}
