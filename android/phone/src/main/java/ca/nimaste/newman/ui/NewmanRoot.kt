package ca.nimaste.newman.ui

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import ca.nimaste.newman.core.model.string
import ca.nimaste.newman.ui.camera.CameraScreen
import ca.nimaste.newman.ui.home.HomeScreen
import ca.nimaste.newman.ui.me.MeScreen
import ca.nimaste.newman.ui.onboarding.OnboardingScreen
import ca.nimaste.newman.ui.people.PersonMapScreen
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import java.net.URLDecoder
import java.net.URLEncoder

object Routes {
    const val ONBOARDING = "onboarding"
    const val HOME = "home"
    const val ME = "me"
    /** Me -> Server (and a newman://connect link opened while signed in): the Connect step, signed in. */
    const val SERVER = "server?changing={changing}&link={link}"
    fun server(link: String? = null) = "server?changing=true" + (link?.let { "&link=${URLEncoder.encode(it, "UTF-8")}" } ?: "")
    const val CAMERA = "camera/{id}?alert={alert}"
    const val PERSON = "person/{id}?look={look}"
    /** [light]: the standard map instead of the night one (a People tile's map look). */
    fun person(id: String, light: Boolean = false) = "person/${URLEncoder.encode(id, "UTF-8")}" + if (light) "?look=light" else ""
    fun camera(id: String, alertId: String? = null) =
        "camera/${URLEncoder.encode(id, "UTF-8")}" + (alertId?.let { "?alert=${URLEncoder.encode(it, "UTF-8")}" } ?: "")
}

/** The openRequest for a tapped alert notification: "alert:<clip id>\n<review id>\n<alert id>". */
object OpenAlert {
    private const val PREFIX = "alert:"
    fun request(clip: String?, reviewId: String?, alertId: String? = null): String =
        PREFIX + clip.orEmpty() + "\n" + reviewId.orEmpty() + "\n" + alertId.orEmpty()
    /** (clip, review id, alert id), or null when [request] isn't one. */
    fun parse(request: String?): Triple<String?, String?, String?>? {
        if (request == null || !request.startsWith(PREFIX)) return null
        val parts = request.removePrefix(PREFIX).split("\n", limit = 3)
        return Triple(parts[0].ifEmpty { null }, parts.getOrNull(1)?.ifEmpty { null }, parts.getOrNull(2)?.ifEmpty { null })
    }
}

@Composable
fun NewmanRoot(app: AppViewModel, openRequest: MutableStateFlow<String?>, connectLink: MutableStateFlow<String?>) {
    val signedIn by app.signedIn.collectAsState()
    val link by connectLink.collectAsState()
    val nav = rememberNavController()
    // A newman://connect link while signed in: the Server screen with that hub filled in (as on iOS).
    LaunchedEffect(link, signedIn) {
        val l = link
        if (l != null && signedIn) {
            connectLink.value = null
            nav.navigate(Routes.server(l))
        }
    }
    // A tapped alert notification (any age): a camera alert's video on its camera, with More › Alerts
    // underneath (just Alerts for any other alert, or when the hub doesn't list it).
    val open by openRequest.collectAsState()
    LaunchedEffect(open, signedIn) {
        val (clip, review, alertId) = OpenAlert.parse(open) ?: return@LaunchedEffect
        if (!signedIn) return@LaunchedEffect
        if (alertId != null) {
            // The run's activity entry: a camera clip plays on that camera's screen; anything else is
            // More › Alerts, scrolled to it and highlighted.
            val entry = app.runEntry(alertId)
            val clipId = entry?.data?.string("clip")
            val eid = entry?.entityId
            nav.popBackStack(Routes.HOME, inclusive = false)
            if (entry != null && clipId != null && eid != null) {
                app.openClip.value = ca.nimaste.newman.core.model.CameraAlert("run:$alertId", entityId = eid, clip = clipId, title = entry.title,
                    start = entry.ts, snapshotUrl = entry.data?.string("snapshotUrl"))
                openRequest.value = "camera-clip"
            } else {
                app.openAlert.value = entry
                openRequest.value = "alerts"
            }
            return@LaunchedEffect
        }
        val a = app.findAlert(null, clip, review)
        val eid = a?.entityId
        if (a != null && eid != null) {
            nav.popBackStack(Routes.HOME, inclusive = false)
            nav.navigate(Routes.camera(eid, a.id))
        }
        openRequest.value = "alerts"
    }
    var toast by remember { mutableStateOf<AppViewModel.Toast?>(null) }

    LaunchedEffect(Unit) {
        app.toasts.collect {
            toast = it
        }
    }
    LaunchedEffect(toast) {
        if (toast != null) {
            delay(3200)
            toast = null
        }
    }
    LaunchedEffect(signedIn) {
        val target = if (signedIn) Routes.HOME else Routes.ONBOARDING
        if (nav.currentDestination?.route != target) {
            nav.navigate(target) { popUpTo(nav.graph.id) { inclusive = true } }
        }
    }

    // Android 13+: ask once for notification permission when push is set up in this build.
    val askNotifications = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    LaunchedEffect(signedIn) {
        if (signedIn && app.account.pushAvailable && Build.VERSION.SDK_INT >= 33) {
            askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    CompositionLocalProvider(LocalApp provides app) {
        Box(Modifier.fillMaxSize().background(N.Night)) {
            NavHost(nav, startDestination = if (signedIn) Routes.HOME else Routes.ONBOARDING) {
                composable(Routes.ONBOARDING) {
                    OnboardingScreen(onDone = { app.onSignedIn() }, connectLink = link, onLinkUsed = { connectLink.value = null })
                }
                composable(Routes.HOME) {
                    HomeScreen(
                        openRequest = openRequest,
                        onCamera = { id, alert -> nav.navigate(Routes.camera(id, alert)) },
                        onMe = { nav.navigate(Routes.ME) },
                        onPerson = { id, light -> nav.navigate(Routes.person(id, light)) },
                    )
                }
                composable(
                    Routes.PERSON,
                    arguments = listOf(
                        navArgument("id") { type = NavType.StringType },
                        navArgument("look") { type = NavType.StringType; nullable = true; defaultValue = null },
                    ),
                ) { entry ->
                    val id = URLDecoder.decode(entry.arguments?.getString("id") ?: "", "UTF-8")
                    PersonMapScreen(personId = id, light = entry.arguments?.getString("look") == "light", onBack = { nav.popBackStack() })
                }
                composable(Routes.ME) {
                    MeScreen(onBack = { nav.popBackStack() }, onServer = { nav.navigate(Routes.server()) })
                }
                composable(
                    Routes.SERVER,
                    arguments = listOf(
                        navArgument("changing") { type = NavType.BoolType; defaultValue = true },
                        navArgument("link") { type = NavType.StringType; nullable = true; defaultValue = null },
                    ),
                ) {
                    OnboardingScreen(
                        onDone = {},
                        onClose = { nav.popBackStack() },
                        leaveHub = { app.leaveHub() },
                        onChanged = { same, name ->
                            if (same) {
                                app.toast("Saved the addresses for ${name ?: "your Newman"}")
                                nav.popBackStack()
                            } else app.leftHub(name) // signed out: on to signing in to the new hub
                        },
                    )
                }
                composable(
                    Routes.CAMERA,
                    arguments = listOf(
                        navArgument("id") { type = NavType.StringType },
                        navArgument("alert") { type = NavType.StringType; nullable = true; defaultValue = null },
                    ),
                ) { entry ->
                    val id = URLDecoder.decode(entry.arguments?.getString("id") ?: "", "UTF-8")
                    val alert = entry.arguments?.getString("alert")?.let { URLDecoder.decode(it, "UTF-8") }
                    CameraScreen(entityId = id, alertId = alert, onBack = { nav.popBackStack() })
                }
            }
            AnimatedVisibility(
                visible = toast != null,
                enter = fadeIn(), exit = fadeOut(),
                modifier = Modifier.align(Alignment.BottomCenter).navigationBarsPadding().padding(start = 16.dp, end = 16.dp, bottom = 96.dp),
            ) {
                val t = toast
                if (t != null) {
                    Text(
                        t.text,
                        style = T.label.copy(fontSize = 14.sp, color = if (t.ok) androidx.compose.ui.graphics.Color(0xFF04211C) else N.Night, textAlign = TextAlign.Center),
                        modifier = Modifier.fillMaxWidth()
                            .background(if (t.ok) N.Teal.copy(alpha = 0.95f) else N.CoralText, RoundedCornerShape(14.dp))
                            .padding(horizontal = 16.dp, vertical = 14.dp),
                    )
                }
            }
        }
    }
}
