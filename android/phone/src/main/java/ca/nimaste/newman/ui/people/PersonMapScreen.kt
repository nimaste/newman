package ca.nimaste.newman.ui.people

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Typeface
import android.graphics.drawable.BitmapDrawable
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import ca.nimaste.newman.BuildConfig
import ca.nimaste.newman.core.people.PeopleText
import ca.nimaste.newman.core.people.Person
import ca.nimaste.newman.core.people.Place
import ca.nimaste.newman.core.net.HubHttp
import ca.nimaste.newman.core.people.PersonLook
import ca.nimaste.newman.core.people.PersonRefresh
import ca.nimaste.newman.core.people.ReportSchedule
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.BackPill
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import okhttp3.Request
import kotlinx.coroutines.launch
import org.osmdroid.config.Configuration
import org.osmdroid.tileprovider.tilesource.TileSourceFactory
import org.osmdroid.util.GeoPoint
import org.osmdroid.views.CustomZoomButtonsController
import org.osmdroid.views.MapView
import org.osmdroid.views.overlay.Marker
import org.osmdroid.views.overlay.Polygon
import org.osmdroid.views.overlay.TilesOverlay
import java.io.File

/** osmdroid needs a user agent naming the app (OpenStreetMap's tile policy) and a cache it may write. */
private fun configureOsm(ctx: Context) {
    val c = Configuration.getInstance()
    c.userAgentValue = "Newman-Android/${BuildConfig.VERSION_NAME} (${BuildConfig.APPLICATION_ID}; self-hosted home hub)"
    val base = File(ctx.cacheDir, "osmdroid")
    c.osmdroidBasePath = base
    c.osmdroidTileCache = File(base, "tiles")
}

/**
 * One person on a full-screen map (OpenStreetMap, no Google needed): their dot, the accuracy
 * circle, "Home" / place / distance and when. The refresh button (and the tap that opened it) asks
 * their phone for ONE fresh fix ("Updating…" until it arrives); updates arrive over the live
 * connection, with a 30 s re-read of the hub as a fallback (that never wakes their phone). [light]: the standard map (a People tile's "Map
 * look"); otherwise the night one.
 */
@Composable
fun PersonMapScreen(personId: String, onBack: () -> Unit, light: Boolean = false) {
    val app = LocalApp.current
    val ctx = LocalContext.current
    val st by app.people.state.collectAsState()
    val places by app.people.places.collectAsState()
    val index = st.people.indexOfFirst { it.id == personId }
    val person = st.people.getOrNull(index)
    var now by remember { mutableStateOf(System.currentTimeMillis() / 1000.0) }
    val refreshes by app.people.refreshes.collectAsState()
    val refresh = refreshes[personId] ?: PersonRefresh.IDLE
    // The pin shows their photo too (through the mTLS client; the ?v= URL changes with the picture).
    val server by app.store.changes.collectAsState()
    val photoUrl = remember(person?.photo, server.serverUrl) { PersonLook.photoPath(person)?.let { HubHttp.absolute(server.serverUrl, it) } }
    var photo by remember { mutableStateOf<Bitmap?>(null) }
    LaunchedEffect(photoUrl) {
        photo = if (photoUrl == null) null else withContext(Dispatchers.IO) {
            runCatching {
                app.http.newCall(Request.Builder().url(photoUrl).build()).execute().use { r ->
                    if (r.isSuccessful) r.body?.bytes()?.let { BitmapFactory.decodeByteArray(it, 0, it.size) } else null
                }
            }.getOrNull()
        }
    }

    LaunchedEffect(personId) {
        launch { app.people.refreshPlaces() }
        while (true) {
            app.people.refresh()
            now = System.currentTimeMillis() / 1000.0
            delay(ReportSchedule.PEOPLE_REFRESH_SECONDS * 1000L)
        }
    }
    // Keep "updated N min ago" ticking between refreshes.
    LaunchedEffect(Unit) {
        while (true) {
            delay(15_000)
            now = System.currentTimeMillis() / 1000.0
        }
    }

    val colour = personColour(person, index.coerceAtLeast(0))
    val map = remember {
        configureOsm(ctx)
        MapView(ctx).apply {
            setTileSource(TileSourceFactory.MAPNIK)
            setMultiTouchControls(true)
            zoomController.setVisibility(CustomZoomButtonsController.Visibility.NEVER)
            // Night-glass: the standard map, inverted to dark (unless asked for the light look).
            if (!light) overlayManager.tilesOverlay.setColorFilter(TilesOverlay.INVERT_COLORS)
            isTilesScaledToDpi = true
            controller.setZoom(15.5)
        }
    }
    val holder = remember { MapLayers(map, light) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val obs = LifecycleEventObserver { _, e ->
            when (e) {
                Lifecycle.Event.ON_RESUME -> map.onResume()
                Lifecycle.Event.ON_PAUSE -> map.onPause()
                else -> Unit
            }
        }
        lifecycle.addObserver(obs)
        map.onResume()
        onDispose {
            lifecycle.removeObserver(obs)
            map.onPause()
            map.onDetach()
        }
    }

    Box(Modifier.fillMaxSize().background(N.Night)) {
        AndroidView({ map }, Modifier.fillMaxSize()) { holder.show(person, colour, places, photo) }
        Row(Modifier.statusBarsPadding().padding(horizontal = 14.dp, vertical = 10.dp)) { BackPill(onClick = onBack) }
        Column(Modifier.align(Alignment.BottomCenter).fillMaxWidth().navigationBarsPadding().padding(12.dp)) {
            Text("© OpenStreetMap contributors", style = T.tiny.copy(fontSize = 10.sp, color = N.Muted),
                modifier = Modifier.align(Alignment.End).background(Color(0xB30B1026), RoundedCornerShape(6.dp)).padding(horizontal = 6.dp, vertical = 2.dp))
            Row(
                Modifier.padding(top = 6.dp).fillMaxWidth().background(Color(0xE6141A33), RoundedCornerShape(22.dp)).padding(PaddingValues(14.dp)),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                PersonAvatar(person, index.coerceAtLeast(0))
                Column(Modifier.weight(1f)) {
                    Text(person?.name ?: "…", style = T.title.copy(fontSize = 20.sp))
                    Text(person?.let { PeopleText.where(it) } ?: "", style = T.small.copy(color = if (person?.home == true) N.Teal else N.Soft))
                    if (refresh.phase != PersonRefresh.Phase.IDLE) RefreshNote(refresh, System.currentTimeMillis() / 1000.0)
                    else person?.let { PeopleText.updated(it.at, now) }?.let { Text(it, style = T.tiny) }
                }
                if (person?.sharing == true) {
                    val busy = refresh.phase == PersonRefresh.Phase.UPDATING
                    ca.nimaste.newman.ui.common.IconAction("refresh", "Update ${person.name}'s location", size = 44.dp, iconSize = 20.dp,
                        color = if (busy) N.Muted else N.Text, background = Color(0x1AFFFFFF), enabled = !busy) { app.people.refreshPerson(personId) }
                }
            }
        }
    }
}

/** The map's overlays for one person; recentres when they move. */
private class MapLayers(private val map: MapView, private val light: Boolean) {
    private var marker: Marker? = null
    private var circle: Polygon? = null
    private var placeShapes: List<Polygon> = emptyList()
    private var shownPlaces: List<Place>? = null
    private var last: GeoPoint? = null
    private var iconKey: Triple<String, Color, Bitmap?>? = null

    fun show(p: Person?, colour: Color, places: List<Place>, photo: Bitmap?) {
        if (places != shownPlaces) {
            placeShapes.forEach { map.overlays.remove(it) }
            placeShapes = places.mapNotNull { pl ->
                val plat = pl.lat ?: return@mapNotNull null
                val plon = pl.lon ?: return@mapNotNull null
                Polygon(map).apply {
                    points = Polygon.pointsAsCircle(GeoPoint(plat, plon), pl.radiusM)
                    fillPaint.color = if (light) 0x140B1026 else 0x14FFFFFF
                    outlinePaint.color = if (light) 0x400B1026 else 0x40FFFFFF
                    outlinePaint.strokeWidth = 2f
                    title = pl.name
                    infoWindow = null
                }
            }
            map.overlays.addAll(0, placeShapes)
            shownPlaces = places
        }
        val lat = p?.lat
        val lon = p?.lon
        if (p == null || lat == null || lon == null) {
            map.invalidate()
            return
        }
        val at = GeoPoint(lat, lon)
        val c = circle ?: Polygon(map).also {
            it.fillPaint.color = colour.copy(alpha = 0.18f).toArgb()
            it.outlinePaint.color = colour.copy(alpha = 0.6f).toArgb()
            it.outlinePaint.strokeWidth = 3f
            it.infoWindow = null
            map.overlays.add(it)
            circle = it
        }
        c.points = Polygon.pointsAsCircle(at, (p.accuracyM ?: 0.0).coerceAtLeast(15.0))
        val m = marker ?: Marker(map).also {
            it.setAnchor(Marker.ANCHOR_CENTER, Marker.ANCHOR_CENTER)
            it.setInfoWindow(null)
            map.overlays.add(it)
            marker = it
        }
        val key = Triple(PeopleText.initial(p.name), colour, photo)
        if (key != iconKey) {
            m.icon = BitmapDrawable(map.resources, dot(map.context, key.first, colour, photo))
            c.fillPaint.color = colour.copy(alpha = 0.18f).toArgb()
            c.outlinePaint.color = colour.copy(alpha = 0.6f).toArgb()
            iconKey = key
        }
        m.position = at
        m.title = p.name
        if (last == null) map.controller.setCenter(at)
        else if (last != at) map.controller.animateTo(at)
        last = at
        map.invalidate()
    }
}

/** A person's map pin: their initial on their colour in a white ring, their photo over it when they have one. */
internal fun dot(ctx: Context, initial: String, colour: Color, photo: Bitmap?): Bitmap {
    val d = ctx.resources.displayMetrics.density
    val size = (44 * d).toInt()
    val bmp = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
    val cv = Canvas(bmp)
    val p = Paint(Paint.ANTI_ALIAS_FLAG)
    p.color = 0xFFFFFFFF.toInt()
    cv.drawCircle(size / 2f, size / 2f, size / 2f, p)
    p.color = colour.toArgb()
    cv.drawCircle(size / 2f, size / 2f, size / 2f - 3 * d, p)
    p.color = N.Night.toArgb()
    p.textSize = 18 * d
    p.typeface = Typeface.DEFAULT_BOLD
    p.textAlign = Paint.Align.CENTER
    cv.drawText(initial, size / 2f, size / 2f - (p.descent() + p.ascent()) / 2, p)
    if (photo != null) {
        // The photo (square from the hub) clipped to the inner circle.
        val inner = size / 2f - 3 * d
        val path = Path().apply { addCircle(size / 2f, size / 2f, inner, Path.Direction.CW) }
        cv.save()
        cv.clipPath(path)
        cv.drawBitmap(photo, null, RectF(size / 2f - inner, size / 2f - inner, size / 2f + inner, size / 2f + inner), Paint(Paint.FILTER_BITMAP_FLAG))
        cv.restore()
    }
    return bmp
}

/**
 * Everyone who shares their location on one small map (the People screen's): each person's pin, the
 * map fitted around them. Taps go to the list below it.
 */
@Composable
fun PeopleMap(people: List<Person>, modifier: Modifier = Modifier) {
    val ctx = LocalContext.current
    val map = remember {
        configureOsm(ctx)
        MapView(ctx).apply {
            setTileSource(TileSourceFactory.MAPNIK)
            setMultiTouchControls(true)
            zoomController.setVisibility(CustomZoomButtonsController.Visibility.NEVER)
            overlayManager.tilesOverlay.setColorFilter(TilesOverlay.INVERT_COLORS)
            isTilesScaledToDpi = true
            controller.setZoom(14.0)
        }
    }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val obs = LifecycleEventObserver { _, e ->
            when (e) {
                Lifecycle.Event.ON_RESUME -> map.onResume()
                Lifecycle.Event.ON_PAUSE -> map.onPause()
                else -> Unit
            }
        }
        lifecycle.addObserver(obs)
        map.onResume()
        onDispose {
            lifecycle.removeObserver(obs)
            map.onPause()
            map.onDetach()
        }
    }
    val located = people.withIndex().filter { it.value.lat != null && it.value.lon != null }
    val key = located.map { Triple(it.value.id, it.value.lat, it.value.lon) }
    var shown by remember { mutableStateOf<List<Triple<String, Double?, Double?>>?>(null) }
    Box(modifier) {
        AndroidView({ map }, Modifier.fillMaxSize()) { m ->
            if (shown == key) return@AndroidView
            m.overlays.clear()
            located.forEach { (i, p) ->
                m.overlays.add(Marker(m).apply {
                    position = GeoPoint(p.lat!!, p.lon!!)
                    setAnchor(Marker.ANCHOR_CENTER, Marker.ANCHOR_CENTER)
                    setInfoWindow(null)
                    icon = BitmapDrawable(m.resources, dot(m.context, PeopleText.initial(p.name), personColour(p, i), null))
                    title = p.name
                })
            }
            val points = located.map { GeoPoint(it.value.lat!!, it.value.lon!!) }
            when {
                points.size == 1 -> m.controller.setCenter(points[0])
                points.size > 1 -> m.post {
                    m.zoomToBoundingBox(org.osmdroid.util.BoundingBox.fromGeoPointsSafe(points).increaseByScale(1.6f), false)
                }
            }
            m.invalidate()
            shown = key
        }
        Text("© OpenStreetMap contributors", style = T.tiny.copy(fontSize = 10.sp, color = N.Muted),
            modifier = Modifier.align(Alignment.BottomEnd).padding(6.dp).background(Color(0xB30B1026), RoundedCornerShape(6.dp)).padding(horizontal = 6.dp, vertical = 2.dp))
    }
}
