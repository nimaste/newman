package ca.nimaste.newman.ui.alerts

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import ca.nimaste.newman.ui.common.heading
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import ca.nimaste.newman.core.live.LiveMessage
import ca.nimaste.newman.core.model.ActivityEntry
import ca.nimaste.newman.core.model.CameraAlert
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.string
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.HubImage
import ca.nimaste.newman.ui.common.IconBubble
import ca.nimaste.newman.ui.common.Pill
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import ca.nimaste.newman.ui.tiles.AlertRow
import kotlinx.coroutines.launch
import java.text.DateFormat
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Date
import java.util.Locale

private const val ALL = "all"
private const val CAMERAS = "__cameras"
private const val HOME = "__home"

/**
 * Alerts (PhoneAlerts board): the hub's activity (GET /api/events, kept live), filtered by the
 * category of the device each entry is about; "Cameras" lists GET /api/alerts.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
/**
 * [highlight]: a tapped notification's entry - the list starts on Everything, scrolled to it and
 * outlined (added when it's older than the activity loaded); [onLeave] when the screen goes.
 */
fun AlertsScreen(onCamera: (String, String?) -> Unit, onBack: (() -> Unit)? = null, highlight: ActivityEntry? = null, onLeave: () -> Unit = {}) {
    val app = LocalApp.current
    val state by app.state.collectAsState()
    val scope = rememberCoroutineScope()
    var filter by rememberSaveable { mutableStateOf(ALL) }
    var cameraAlerts by remember { mutableStateOf<List<CameraAlert>?>(null) }
    var refreshing by remember { mutableStateOf(false) }
    var reloadCams by remember { mutableStateOf(0) }
    val listState = androidx.compose.foundation.lazy.rememberLazyListState()
    androidx.compose.runtime.DisposableEffect(Unit) { onDispose(onLeave) }
    LaunchedEffect(highlight?.id) { if (highlight != null) filter = ALL }

    LaunchedEffect(state.activity.firstOrNull()?.ts) { state.activity.firstOrNull()?.let { app.markAlertsSeen(it.ts) } }
    LaunchedEffect(reloadCams) { cameraAlerts = app.cameraAlerts(null, 50) }
    LaunchedEffect(Unit) {
        app.liveMessages.collect { m -> if (m is LiveMessage.Activity && m.entry.kind == "alert") reloadCams++ }
    }

    val catOf = { a: ActivityEntry -> a.entityId?.let { state.entities[it]?.category } ?: HOME }
    val cats = state.activity.map(catOf).distinct()
    val filters = buildList {
        add(ALL to "All")
        if (!cameraAlerts.isNullOrEmpty()) add(CAMERAS to "Cameras")
        // Camera alerts have their own filter: the category whose screen is the Cameras one isn't listed again.
        state.meta?.categories?.filter { it.id in cats && it.screen != "cameras" }?.forEach { add(it.id to it.name) }
        if (HOME in cats) add(HOME to "Home")
    }

    // The activity shown (with a tapped notification's entry when it's older than what's loaded), and where that entry is.
    val shownActivity = (if (highlight != null && state.activity.none { it.id == highlight.id }) (state.activity + highlight).sortedByDescending { it.ts }
        else state.activity).filter { filter == ALL || catOf(it) == filter }
    val target = if (filter == CAMERAS || highlight == null) -1 else {
        var index = 0
        var last: String? = null
        var found = -1
        for (a in shownActivity) {
            val day = dayHeading(a.ts)
            if (day != last) { last = day; index++ }
            if (a.id == highlight.id) { found = index; break }
            index++
        }
        found
    }
    LaunchedEffect(highlight?.id, target >= 0) { if (target >= 0) listState.scrollToItem(target) }

    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        // Opened from More: a way back.
        if (onBack != null) Box(Modifier.padding(start = 16.dp, top = 10.dp)) { ca.nimaste.newman.ui.common.BackPill("More", onClick = onBack) }
        Text("Alerts", style = T.display, modifier = Modifier.padding(start = 18.dp, end = 18.dp, top = 14.dp, bottom = 10.dp).heading())
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 16.dp).padding(bottom = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            filters.forEach { (id, name) -> Pill(name, id == filter) { filter = id } }
        }
        PullToRefreshBox(
            isRefreshing = refreshing,
            onRefresh = { scope.launch { refreshing = true; app.hub.reloadActivity(); reloadCams++; refreshing = false } },
            modifier = Modifier.weight(1f),
        ) {
            LazyColumn(Modifier.fillMaxSize(), state = listState, contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (filter == CAMERAS) {
                    val list = cameraAlerts ?: emptyList()
                    if (list.isEmpty()) item { Empty("No camera alerts lately.") }
                    var last: String? = null
                    list.forEach { a ->
                        val day = dayHeading(a.start ?: 0.0)
                        if (day != last) {
                            last = day
                            item(key = "h:$day") { Heading(day) }
                        }
                        item(key = "c:${a.id}") { AlertRow(a, showCamera = true) { a.entityId?.let { onCamera(it, a.id) } } }
                    }
                } else {
                    val list = shownActivity
                    if (list.isEmpty()) item { Empty(if (state.loaded) "Nothing here yet." else "Loading…") }
                    var last: String? = null
                    list.forEach { a ->
                        val day = dayHeading(a.ts)
                        if (day != last) {
                            last = day
                            item(key = "h:$day") { Heading(day) }
                        }
                        item(key = "a:${a.id}:${a.ts}") { ActivityCard(a, onCamera, highlighted = a.id == highlight?.id) }
                    }
                }
            }
        }
    }
}

@Composable
private fun Heading(text: String) {
    Text(text.uppercase(), style = T.caps, modifier = Modifier.padding(top = 6.dp).semantics { heading(); contentDescription = text })
}

@Composable
private fun Empty(text: String) {
    Text(text, style = T.body.copy(color = N.Muted), modifier = Modifier.fillMaxWidth().padding(vertical = 24.dp))
}

/** An icon for an activity entry by what it is about: its device's kind, else the entry's own kind. */
@Composable
private fun ActivityCard(a: ActivityEntry, onCamera: (String, String?) -> Unit, highlighted: Boolean = false) {
    val app = LocalApp.current
    val state by app.state.collectAsState()
    val e = a.entityId?.let { state.entities[it] }
    val k = state.kind(e)
    val thumb = a.data?.string("thumbUrl") ?: a.data?.string("snapshotUrl")
    val failed = a.data?.get("failed")?.toString() == "true"
    val (bg, fg) = when {
        failed || a.kind == "error" -> N.CoralBg to N.Coral
        e != null && HubModel.notSecure(e, k) -> N.CoralBg to N.Coral
        e != null && k?.secureState != null -> N.TealBg to N.Teal
        else -> androidx.compose.ui.graphics.Color(0x1AFFFFFF) to N.Text
    }
    val icon = when {
        k != null -> HubModel.deviceIcon(e, k)
        a.kind == "signin" || a.kind == "account" -> "user"
        a.kind == "automation" || a.kind == "followup" -> "bell"
        a.kind == "system" -> "gauge"
        else -> "bell"
    }
    val shape = RoundedCornerShape(18.dp)
    Row(
        Modifier.fillMaxWidth().clip(shape).background(if (highlighted) N.GlassHi else N.Glass, shape)
            .border(if (highlighted) 2.dp else 1.dp, if (highlighted) ca.nimaste.newman.ui.theme.H.Accent else N.Border, shape)
            .then(if (thumb != null && a.entityId != null) Modifier.clickable(onClickLabel = "play") { onCamera(a.entityId!!, a.data?.string("reviewId")) }
                else Modifier.semantics(mergeDescendants = true) {})
            .padding(12.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (thumb != null) {
            Box(Modifier.size(84.dp, 62.dp).clip(RoundedCornerShape(10.dp))) {
                HubImage(thumb, Modifier.size(84.dp, 62.dp))
                NIcon("play", Modifier.align(Alignment.Center), size = 16.dp, color = N.Neutral)
            }
        } else {
            IconBubble(icon, bg, fg)
        }
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(a.title, style = T.label, modifier = Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(DateFormat.getTimeInstance(DateFormat.SHORT).format(Date((a.ts * 1000).toLong())), style = T.tiny)
            }
            if (a.body.isNotBlank()) Text(a.body, style = T.body.copy(fontSize = androidx.compose.ui.unit.TextUnit(13f, androidx.compose.ui.unit.TextUnitType.Sp), color = N.Soft))
        }
    }
}

private fun dayHeading(ts: Double): String {
    val d = Instant.ofEpochMilli((ts * 1000).toLong()).atZone(ZoneId.systemDefault()).toLocalDate()
    val today = LocalDate.now()
    return when (d) {
        today -> "Today"
        today.minusDays(1) -> "Yesterday"
        else -> d.format(DateTimeFormatter.ofPattern("EEEE, MMMM d", Locale.getDefault()))
    }
}
