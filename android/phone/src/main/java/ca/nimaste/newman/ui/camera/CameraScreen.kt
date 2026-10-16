package ca.nimaste.newman.ui.camera

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
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
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.live.LiveMessage
import ca.nimaste.newman.core.model.CameraAlert
import ca.nimaste.newman.core.model.strings
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.BackPill
import ca.nimaste.newman.ui.common.IconAction
import ca.nimaste.newman.ui.common.heading
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.selection.selectable
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.selected
import ca.nimaste.newman.ui.common.LiveCamera
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import ca.nimaste.newman.ui.tiles.AlertRow
import java.text.DateFormat
import java.time.LocalDate
import java.time.ZoneId
import java.util.Date

/**
 * A camera full screen (PhoneCamera board): live video at full quality (LiveCamera: its refreshing
 * still until the stream plays; a camera without a stream keeps the still), a tapped alert's picture in
 * its place ("Replay": its video, AlertVideo), arrows to step through the cameras, and today's alerts for this camera or all.
 */
@Composable
fun CameraScreen(entityId: String, alertId: String?, onBack: () -> Unit) {
    val app = LocalApp.current
    val state by app.state.collectAsState()
    // The cameras in dashboard order first, then every other camera (not a camera's zones: the camera
    // tile's "All N" counts the same ones).
    val dashCams = state.dashboard?.pages?.flatMap { p -> p.tiles.filter { it.type == "camera" }.flatMap { it.config.strings("entities") } }
        ?.distinct().orEmpty()
    val others = ca.nimaste.newman.core.model.HubModel.cameras(state.named.values) { state.kind(it) }.filter { it.id !in dashCams }
    val cams = (dashCams.mapNotNull { state.named[it] } + others)
        .filter { it.still != null }
    var camId by rememberSaveable { mutableStateOf(entityId) }
    val cam = state.named[camId] ?: cams.firstOrNull()
    var all by rememberSaveable { mutableStateOf(false) }
    var playing by remember { mutableStateOf<CameraAlert?>(null) }
    var alerts by remember { mutableStateOf<List<CameraAlert>?>(null) }
    var reload by remember { mutableIntStateOf(0) }
    val startOfDay = LocalDate.now().atStartOfDay(ZoneId.systemDefault()).toEpochSecond().toDouble()

    LaunchedEffect(cam?.id, all, reload) {
        val list = app.cameraAlerts(if (all) null else cam, 50)
        alerts = list.filter { (it.start ?: 0.0) >= startOfDay }
        if (alertId != null && playing == null) playing = list.firstOrNull { it.id == alertId }
    }
    LaunchedEffect(Unit) {
        app.liveMessages.collect { m -> if ((m is LiveMessage.Activity && m.entry.kind == "alert") || m is LiveMessage.Reconnected) reload++ }
    }

    fun step(d: Int) {
        if (cams.isEmpty()) return
        val i = cams.indexOfFirst { it.id == cam?.id }.coerceAtLeast(0)
        camId = cams[(i + d + cams.size) % cams.size].id
        playing = null
    }

    val p = playing
    Column(Modifier.fillMaxSize().background(N.Black).statusBarsPadding()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            if (p == null) BackPill(onClick = onBack) else BackPill("Live") { playing = null }
            Box(Modifier.height(22.dp).background(if (p == null) N.Live else N.Amber, RoundedCornerShape(6.dp)).padding(horizontal = 7.dp),
                contentAlignment = Alignment.Center) {
                Text(if (p == null) "LIVE" else "REPLAY", style = T.tiny.copy(fontSize = 11.sp, letterSpacing = 1.sp,
                    color = if (p == null) Color.White else N.AmberText))
            }
            Text(
                if (p == null) cam?.name ?: "No camera"
                else listOfNotNull(p.objects.joinToString(" & ").replaceFirstChar { it.uppercase() }.ifEmpty { null },
                    p.start?.let { DateFormat.getTimeInstance(DateFormat.SHORT).format(Date((it * 1000).toLong())) }).joinToString(" · "),
                style = T.label.copy(fontSize = 16.sp), maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f),
            )
        }
        Box(Modifier.fillMaxWidth().aspectRatio(16f / 9f)) {
            if (p != null) {
                androidx.compose.runtime.key(p.id) { AlertVideo(p, Modifier.fillMaxSize()) }
            } else {
                androidx.compose.runtime.key(cam?.id) {
                    LiveCamera(cam, Modifier.fillMaxSize(), quality = "main", priority = true, heightPx = 720, contentScale = ContentScale.Fit)
                }
                if (cams.size > 1) {
                    Arrow("chevronLeft", "Previous camera", Modifier.align(Alignment.CenterStart).padding(8.dp)) { step(-1) }
                    Arrow("chevronRight", "Next camera", Modifier.align(Alignment.CenterEnd).padding(8.dp)) { step(1) }
                }
            }
        }
        if (p != null && p.clip != null && p.end == null) {
            Text("Still recording — the video will be complete when it ends.", style = T.small.copy(color = N.Muted),
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp))
        }
        if (cams.size > 1) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp, Alignment.CenterHorizontally)) {
                cams.forEach { c ->
                    val on = c.id == cam?.id
                    // A 7dp dot to see, a 48dp-high strip to touch.
                    Box(Modifier.height(48.dp).selectable(selected = on, role = Role.Tab) { camId = c.id; playing = null }
                        .clearAndSetSemantics { contentDescription = c.name; selected = on; role = Role.Tab },
                        contentAlignment = Alignment.Center) {
                        Box(Modifier.height(7.dp).width(if (on) 22.dp else 7.dp).background(if (on) N.Text else Color(0x40FFFFFF), CircleShape))
                    }
                }
            }
        }
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            val n = alerts?.size ?: 0
            Text("Today", style = T.label.copy(fontSize = 17.sp), modifier = Modifier.heading())
            Text(" · $n ${if (n == 1) "alert" else "alerts"}", style = T.small.copy(color = N.Muted), modifier = Modifier.weight(1f))
            Row(Modifier.background(Color(0x12FFFFFF), RoundedCornerShape(99.dp)).padding(3.dp), horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                Toggle("This camera", !all) { all = false }
                Toggle("All", all) { all = true }
            }
        }
        LazyColumn(Modifier.weight(1f), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            val list = alerts
            if (list == null) item { Text("Loading…", style = T.small.copy(color = N.Muted)) }
            else if (list.isEmpty()) item {
                Text(if (all) "Nothing on any camera today." else "Nothing on this camera today.", style = T.body.copy(color = N.Muted),
                    modifier = Modifier.fillMaxWidth().padding(vertical = 20.dp))
            }
            items(list ?: emptyList(), key = { it.id }) { a ->
                AlertRow(a, showCamera = all, selected = a.id == p?.id) {
                    playing = a
                    a.entityId?.let { if (it in state.named) camId = it }
                }
            }
        }
    }
}

@Composable
private fun Arrow(icon: String, label: String, modifier: Modifier, onClick: () -> Unit) {
    IconAction(icon, label, modifier, background = Color(0x8C05070F), stroke = 2.4f, onClick = onClick)
}

@Composable
private fun Toggle(text: String, on: Boolean, onClick: () -> Unit) {
    Box(
        Modifier.minimumInteractiveComponentSize().heightIn(min = 34.dp).clip(RoundedCornerShape(99.dp)).background(if (on) N.Text else Color.Transparent)
            .selectable(selected = on, role = Role.Tab, onClick = onClick)
            .padding(horizontal = 12.dp),
        contentAlignment = Alignment.Center,
    ) { Text(text, style = T.tiny.copy(color = if (on) N.Black else N.Text, fontWeight = androidx.compose.ui.text.font.FontWeight.ExtraBold)) }
}
