package ca.nimaste.newman.ui.cameras

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.live.LiveMessage
import ca.nimaste.newman.core.model.CameraAlert
import ca.nimaste.newman.core.model.HomeCameras
import ca.nimaste.newman.core.model.HomeData
import kotlinx.coroutines.launch
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.verticalScroll
import ca.nimaste.newman.core.model.HomeLogic
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.camera.AlertVideo
import ca.nimaste.newman.ui.common.CameraStill
import ca.nimaste.newman.ui.common.HubImage
import ca.nimaste.newman.ui.common.IconAction
import ca.nimaste.newman.ui.common.LiveCamera
import ca.nimaste.newman.ui.common.spoken
import ca.nimaste.newman.ui.home.HCard
import ca.nimaste.newman.ui.home.HDivider
import ca.nimaste.newman.ui.home.HScreen
import ca.nimaste.newman.ui.home.HT
import ca.nimaste.newman.ui.theme.H
import java.text.DateFormat
import java.time.LocalDate
import java.time.ZoneId
import java.util.Date

private fun clock(t: Double?): String? = t?.let { DateFormat.getTimeInstance(DateFormat.SHORT).format(Date((it * 1000).toLong())) }

/** "Person at Garage Walkway · 8:36 AM" / "Car · 7:48 AM". */
private fun activityLine(what: String, zone: String?, at: Double?): String =
    listOfNotNull(if (zone.isNullOrBlank()) what else "$what at $zone", clock(at)).joinToString(" · ")

/**
 * The Cameras tab: the big picture (the latest activity's camera, or the featured one - Settings ›
 * Cameras on the web picks), then every other camera; one opens on its own screen ([view]).
 */
@Composable
fun CamerasTab(home: HomeData?, view: String?, onOpen: (String?) -> Unit, onFullScreen: (String, String?) -> Unit) {
    val app = LocalApp.current
    val state by app.state.collectAsState()
    val cams = remember(state.entities, state.meta) { HubModel.cameras(state.named.values) { state.kind(it) } }
    val opened = view?.let { state.named[it] }
    if (opened != null) {
        CameraDetail(opened.id, onBack = { onOpen(null) }, onFullScreen = onFullScreen)
        return
    }
    // The latest alert per camera, for the line under each picture.
    var latest by remember { mutableStateOf<Map<String, CameraAlert>>(emptyMap()) }
    var reload by remember { mutableIntStateOf(0) }
    LaunchedEffect(reload) {
        latest = app.cameraAlerts(null, 50).filter { it.entityId != null }.groupBy { it.entityId!! }
            .mapValues { (_, l) -> l.maxBy { it.time ?: 0.0 } }
    }
    LaunchedEffect(Unit) {
        app.liveMessages.collect { m -> if ((m is LiveMessage.Activity && m.entry.kind == "alert") || m is LiveMessage.Reconnected) reload++ }
    }
    val c = home?.cameras
    val top = c?.let { HomeLogic.topCamera(it, cams) } ?: cams.firstOrNull()
    val admin = state.auth?.user?.role == "admin"
    var changing by remember { mutableStateOf(false) }
    if (changing) TopPicker(c, cams, onDismiss = { changing = false })
    HScreen("Cameras", trailing = { Text(if (cams.size == 1) "1 camera" else "${cams.size} cameras", style = HT.line) }) {
        if (cams.isEmpty()) item(key = "none") { Text("No cameras yet.", style = HT.line, modifier = Modifier.padding(4.dp)) }
        if (top != null) item(key = "top") {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text((c?.let { HomeLogic.topLabel(it) } ?: "Latest activity").uppercase(), style = HT.caps, modifier = Modifier.weight(1f))
                    // Admins only (the house setting): what the big picture shows.
                    if (admin) ca.nimaste.newman.ui.home.RaisedButton("Change", height = 30.dp, color = H.Soft,
                        style = HT.small.copy(fontWeight = FontWeight.Bold), onClickLabel = "change what the big picture shows") { changing = true }
                }
                val act = c?.top?.takeIf { it.camera == top.id }
                val line = act?.let { activityLine(it.label, it.zone, it.at) } ?: latest[top.id]?.let { activityLine(it.what, it.where, it.time) }
                Box(
                    Modifier.fillMaxWidth().height(200.dp).clip(RoundedCornerShape(20.dp)).background(H.Picture).border(1.dp, H.CardLine, RoundedCornerShape(20.dp))
                        .clickable(role = Role.Button) { onOpen(top.id) }.spoken(listOfNotNull(top.name, "live", line).joinToString(", ")),
                ) {
                    LiveCamera(top, Modifier.fillMaxSize(), heightPx = 480)
                    LiveBadge(Modifier.align(Alignment.TopStart).padding(10.dp))
                    Column(Modifier.align(Alignment.BottomStart).fillMaxWidth().background(Color(0x660A1024)).padding(horizontal = 12.dp, vertical = 8.dp)) {
                        Text(top.name, style = HT.name.copy(fontSize = 16.sp))
                        if (line != null) Text(line, style = HT.small.copy(color = H.Soft))
                    }
                }
            }
        }
        val rest = cams.filter { it.id != top?.id }
        items(rest.chunked(2).size, key = { "row$it" }) { r ->
            val pair = rest.chunked(2)[r]
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                pair.forEach { cam ->
                    val l = latest[cam.id]?.takeIf { (it.time ?: 0.0) >= startOfToday() }
                    Column(Modifier.weight(1f).clickable(role = Role.Button) { onOpen(cam.id) }
                        .spoken(listOfNotNull(cam.name, l?.let { activityLine(it.what, null, it.time) }).joinToString(", ")),
                        verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        CameraStill(cam, Modifier.fillMaxWidth().height(88.dp).clip(RoundedCornerShape(14.dp)).border(1.dp, H.CardLine, RoundedCornerShape(14.dp)),
                            heightPx = 240)
                        Row(Modifier.padding(horizontal = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text(cam.name, style = HT.name.copy(fontSize = 13.sp), modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                            if (l != null) Text(activityLine(l.what, null, l.time), style = HT.small.copy(fontSize = 11.sp, color = H.LightText), maxLines = 1)
                        }
                    }
                }
                if (pair.size == 1) Box(Modifier.weight(1f))
            }
        }
    }
}

private fun startOfToday(): Double = LocalDate.now().atStartOfDay(ZoneId.systemDefault()).toEpochSecond().toDouble()

@Composable
private fun LiveBadge(modifier: Modifier, text: String = "LIVE", dot: Color = H.LiveDot) {
    Row(modifier.height(24.dp).background(H.Page, RoundedCornerShape(12.dp)).padding(horizontal = 9.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Box(Modifier.size(7.dp).background(dot, CircleShape))
        Text(text, style = HT.small.copy(fontSize = 11.sp, color = H.Text, fontWeight = FontWeight.ExtraBold, letterSpacing = 0.6.sp))
    }
}

/**
 * One camera: live, full screen a tap away, and its detections - filtered by what was seen; tapping
 * one plays its clip in place of the live view.
 */
@Composable
private fun CameraDetail(id: String, onBack: () -> Unit, onFullScreen: (String, String?) -> Unit) {
    val app = LocalApp.current
    val state by app.state.collectAsState()
    val cam = state.named[id] ?: return
    var alerts by remember(id) { mutableStateOf<List<CameraAlert>?>(null) }
    var reload by remember { mutableIntStateOf(0) }
    var filter by rememberSaveable(id) { mutableStateOf<String?>(null) }
    var playing by remember(id) { mutableStateOf<CameraAlert?>(null) }
    // From today's start; each "Earlier days" reaches back another 24 hours.
    var days by rememberSaveable(id) { mutableIntStateOf(0) }
    val earlier = days > 0
    val since = startOfToday() - days * 86_400.0
    LaunchedEffect(id, reload, days) { alerts = app.cameraAlerts(cam, 200, since = since).sortedByDescending { it.time ?: 0.0 } }
    // A tapped notification's clip on this camera: play it.
    val pendingClip by app.openClip.collectAsState()
    LaunchedEffect(pendingClip) {
        val p = pendingClip ?: return@LaunchedEffect
        if (p.entityId == id) { playing = p; app.openClip.value = null }
    }
    LaunchedEffect(Unit) {
        app.liveMessages.collect { m -> if ((m is LiveMessage.Activity && m.entry.kind == "alert") || m is LiveMessage.Reconnected) reload++ }
    }
    val shown = (alerts ?: emptyList()).filter { (it.time ?: 0.0) >= since }
    val labels = shown.groupBy { it.what }.mapValues { it.value.size }
    val list = shown.filter { filter == null || it.what == filter }
    HScreen(cam.name, back = "Cameras", onBack = onBack) {
        item(key = "live") {
            val p = playing
            Box(Modifier.fillMaxWidth().height(210.dp).clip(RoundedCornerShape(20.dp)).background(H.Picture).border(1.dp, H.CardLine, RoundedCornerShape(20.dp))) {
                if (p != null) {
                    androidx.compose.runtime.key(p.id) { AlertVideo(p, Modifier.fillMaxSize()) }
                    Box(Modifier.align(Alignment.TopStart).padding(10.dp).clip(RoundedCornerShape(12.dp)).clickable(role = Role.Button, onClickLabel = "back to live") { playing = null }) {
                        LiveBadge(Modifier, text = "BACK TO LIVE")
                    }
                } else {
                    LiveCamera(cam, Modifier.fillMaxSize(), heightPx = 540)
                    LiveBadge(Modifier.align(Alignment.TopStart).padding(10.dp))
                    IconAction("expand", "Full screen", Modifier.align(Alignment.BottomEnd).padding(6.dp), size = 40.dp, iconSize = 18.dp,
                        background = H.Page) { onFullScreen(cam.id, null) }
                }
            }
        }
        if (labels.size > 0) item(key = "filters") {
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip("All ${shown.size}", filter == null) { filter = null }
                labels.forEach { (l, n) -> FilterChip("${plural(l)} $n", filter == l) { filter = if (filter == l) null else l } }
            }
        }
        item(key = "list") {
            HCard(padding = PaddingValues(horizontal = 14.dp, vertical = 4.dp)) {
                Text(if (earlier) "SINCE " + java.time.Instant.ofEpochSecond(since.toLong()).atZone(ZoneId.systemDefault()).toLocalDate()
                    .format(java.time.format.DateTimeFormatter.ofPattern("EEEE", java.util.Locale.getDefault())).uppercase() else "TODAY", style = HT.caps, modifier = Modifier.padding(top = 10.dp, bottom = 4.dp))
                when {
                    alerts == null -> Text("Loading…", style = HT.line, modifier = Modifier.padding(vertical = 10.dp))
                    list.isEmpty() -> Text(if (earlier) "Nothing on this camera lately." else "Nothing on this camera today.", style = HT.line,
                        modifier = Modifier.padding(vertical = 10.dp))
                }
                list.forEachIndexed { i, a ->
                    Column {
                        Row(
                            Modifier.fillMaxWidth().clickable(role = Role.Button, onClickLabel = "play") { playing = a }
                                .spoken(activityLine(a.what, a.where, a.time)).padding(vertical = 10.dp),
                            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp),
                        ) {
                            Box(Modifier.width(92.dp).height(56.dp).clip(RoundedCornerShape(10.dp)).border(1.dp, if (a.id == playing?.id) H.Accent else H.CardLine,
                                RoundedCornerShape(10.dp))) {
                                HubImage(a.picture, Modifier.fillMaxSize())
                                PlayMark()
                            }
                            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                                Text(a.what, style = HT.name)
                                Text(listOfNotNull(a.where, if (earlier) dayAndTime(a.time) else clock(a.time)).joinToString(" · "), style = HT.small)
                            }
                        }
                        if (i < list.lastIndex) HDivider()
                    }
                }
            }
        }
        item(key = "earlier") {
            Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                Text("Earlier days", style = HT.row.copy(fontSize = 14.sp),
                    modifier = Modifier.heightIn(min = 48.dp).clickable(role = Role.Button) { days++ }.padding(12.dp))
            }
        }
    }
}

@Composable
private fun BoxScope.PlayMark() {
    Box(Modifier.align(Alignment.Center).size(24.dp).background(Color(0x990A1024), CircleShape), contentAlignment = Alignment.Center) {
        ca.nimaste.newman.ui.icons.NIcon("play", size = 12.dp, color = H.Text)
    }
}

private fun dayAndTime(t: Double?): String? =
    t?.let { DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT).format(Date((it * 1000).toLong())) }

/** A filter's name: "Person" -> "People", "Car" -> "Cars". */
private fun plural(label: String): String =
    HubModel.countLabel(label.lowercase().replace(' ', '_'), 2).substringAfter(' ').replaceFirstChar { it.uppercase() }

@Composable
private fun FilterChip(text: String, on: Boolean, onClick: () -> Unit) {
    Box(
        Modifier.heightIn(min = 34.dp).clip(RoundedCornerShape(17.dp)).background(if (on) H.Text else H.Raised)
            .border(1.dp, if (on) H.Text else H.RaisedLine, RoundedCornerShape(17.dp))
            .selectable(selected = on, role = Role.Tab, onClick = onClick).padding(horizontal = 14.dp),
        contentAlignment = Alignment.Center,
    ) { Text(text, style = HT.button.copy(color = if (on) H.Page else H.Soft, fontWeight = if (on) FontWeight.ExtraBold else FontWeight.Bold)) }
}

/** Admins: the big picture shows the latest activity, or a featured camera (PATCH /api/settings/house). */
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
private fun TopPicker(c: HomeCameras?, cams: List<ca.nimaste.newman.core.model.Entity>, onDismiss: () -> Unit) {
    val app = LocalApp.current
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    var mode by remember { mutableStateOf(c?.mode ?: "latest") }
    var featured by remember { mutableStateOf(c?.featured ?: cams.firstOrNull()?.id) }
    var saving by remember { mutableStateOf(false) }
    androidx.compose.material3.ModalBottomSheet(onDismissRequest = onDismiss,
        sheetState = androidx.compose.material3.rememberModalBottomSheetState(skipPartiallyExpanded = true), containerColor = H.Card, contentColor = H.Text) {
        Column(Modifier.padding(horizontal = 16.dp).padding(bottom = 16.dp).navigationBarsPadding(),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("The big picture shows", style = HT.name.copy(fontSize = 20.sp))
            ca.nimaste.newman.ui.home.Segmented(listOf("latest" to "Latest activity", "featured" to "Featured camera"), mode) { mode = it }
            if (mode == "featured") {
                Text("CAMERA", style = HT.caps)
                Column(Modifier.heightIn(max = 360.dp).verticalScroll(rememberScrollState())) {
                    cams.forEach { cam ->
                        Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).selectable(selected = cam.id == featured, role = Role.RadioButton) { featured = cam.id },
                            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Box(Modifier.size(18.dp).clip(CircleShape).background(if (cam.id == featured) H.Accent else H.Inset)
                                .border(1.dp, H.RaisedLine, CircleShape))
                            Text(cam.name, style = HT.row)
                        }
                    }
                }
            }
            ca.nimaste.newman.ui.home.AccentButton("Save", enabled = !saving && (mode == "latest" || featured != null)) {
                saving = true
                scope.launch {
                    val ok = app.setCamerasTop(mode, if (mode == "featured") featured else c?.featured)
                    saving = false
                    if (ok) onDismiss()
                }
            }
        }
    }
}
