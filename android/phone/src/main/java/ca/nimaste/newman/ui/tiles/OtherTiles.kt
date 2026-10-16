package ca.nimaste.newman.ui.tiles

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.live.LiveMessage
import ca.nimaste.newman.core.model.CameraAlert
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.Kind
import ca.nimaste.newman.core.model.Meta
import ca.nimaste.newman.core.model.Tile
import ca.nimaste.newman.core.model.asObjectOrNull
import ca.nimaste.newman.core.model.number
import ca.nimaste.newman.core.model.string
import ca.nimaste.newman.core.model.strings
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.LiveCamera
import ca.nimaste.newman.ui.common.CardHeader
import ca.nimaste.newman.ui.common.GlassCard
import ca.nimaste.newman.ui.common.LocalFillHeight
import androidx.compose.ui.unit.Dp
import ca.nimaste.newman.ui.common.HubImage
import ca.nimaste.newman.ui.common.Pill
import ca.nimaste.newman.ui.common.heading
import androidx.compose.foundation.layout.heightIn
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import java.text.DateFormat
import java.util.Date

/**
 * Live video of the tile's cameras (LiveCamera): a heading with "All N" (every camera full screen,
 * as on the iPhone), the cameras as pills above the picture (so nothing sits under the tab bar),
 * LIVE and the time on it; tap for full screen.
 */
@Composable
fun CameraTile(tile: Tile, entities: Map<String, Entity>, kind: (Entity) -> Kind?, onOpen: (String) -> Unit) {
    val cams = tile.config.strings("entities").mapNotNull { entities[it] }
    var pick by rememberSaveable(tile.id) { mutableStateOf<String?>(null) }
    val cam = cams.firstOrNull { it.id == pick } ?: cams.firstOrNull()
    val fill = LocalFillHeight.current // read before the card takes it
    val now = rememberNow(30_000)
    val every = HubModel.cameras(entities.values) { kind(it) }
    GlassCard(Modifier.fillMaxWidth(), padding = PaddingValues(12.dp)) {
        // "All N": the camera screen, which steps through every camera, starting at this one.
        CardHeader(tile.config.string("title")?.takeIf { it.isNotBlank() } ?: if (cams.size == 1) cams[0].name else "Cameras",
            action = if (every.size > 1) "All ${every.size}" else null,
            onAction = { (cam?.takeIf { c -> every.any { it.id == c.id } } ?: every.first()).let { onOpen(it.id) } })
        if (cam == null) {
            Text("No cameras chosen for this tile.", style = T.tiny, modifier = Modifier.padding(top = 8.dp))
            return@GlassCard
        }
        if (cams.size > 1) {
            // Every camera of the tile in order, starting at the left; the one shown is kept in view.
            val pills = rememberLazyListState()
            LaunchedEffect(cam.id) {
                val i = cams.indexOfFirst { it.id == cam.id }
                if (i >= 0 && pills.layoutInfo.visibleItemsInfo.none { it.index == i && it.offset >= 0 &&
                        it.offset + it.size <= pills.layoutInfo.viewportEndOffset }) pills.animateScrollToItem(i)
            }
            LazyRow(Modifier.fillMaxWidth().padding(top = 6.dp), state = pills, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                items(cams, key = { it.id }) { c -> Pill(c.name, c.id == cam.id) { pick = c.id } }
            }
        }
        // A placed tile: the picture takes the room its cell leaves.
        val video = if (fill == Dp.Unspecified) 190.dp
            else (fill - 24.dp - 22.dp - (if (cams.size > 1) 54.dp else 0.dp) - 8.dp).coerceIn(120.dp, 320.dp)
        LiveCamera(cam, Modifier.padding(top = 8.dp).fillMaxWidth().height(video).clip(RoundedCornerShape(16.dp))
            .clickable(onClickLabel = "open full screen", role = Role.Button) { onOpen(cam.id) }.semantics { contentDescription = "${cam.name}, live" }, quality = "sub") {
            LiveLabel("LIVE", Modifier.align(Alignment.TopStart).padding(10.dp))
            Text(DateFormat.getTimeInstance(DateFormat.SHORT).format(Date(now)),
                style = T.tiny.copy(color = N.Text, fontWeight = androidx.compose.ui.text.font.FontWeight.ExtraBold),
                modifier = Modifier.align(Alignment.TopEnd).padding(10.dp).height(26.dp)
                    .background(Color(0xB80B1026), RoundedCornerShape(99.dp)).padding(horizontal = 9.dp, vertical = 5.dp))
            NIcon("expand", Modifier.align(Alignment.BottomEnd).padding(10.dp), size = 20.dp)
        }
    }
}

@Composable
fun LiveLabel(name: String, modifier: Modifier = Modifier, dot: Color = N.Red) {
    Row(
        modifier.height(26.dp).background(Color(0xB80B1026), RoundedCornerShape(99.dp)).padding(horizontal = 9.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Box(Modifier.size(7.dp).background(dot, CircleShape))
        Text(name, style = T.tiny.copy(color = N.Text, fontWeight = androidx.compose.ui.text.font.FontWeight.ExtraBold))
    }
}

/** The latest camera alerts (all cameras, or the tile's); tap one to see it. */
@Composable
fun AlertsTile(tile: Tile, entities: Map<String, Entity>, onOpen: (String, String?) -> Unit) {
    val app = LocalApp.current
    val ids = tile.config.strings("entities")
    val limit = (tile.config.number("limit")?.toInt() ?: 4).coerceIn(1, 12)
    var items by remember { mutableStateOf<List<CameraAlert>?>(null) }
    var reload by remember { mutableStateOf(0) }
    LaunchedEffect(ids, reload) {
        val one = if (ids.size == 1) entities[ids[0]] else null
        items = app.cameraAlerts(one, if (ids.size > 1) 50 else limit)
            .filter { ids.size <= 1 || it.entityId in ids }.take(limit)
    }
    LaunchedEffect(Unit) {
        app.liveMessages.collect { m ->
            if ((m is LiveMessage.Activity && m.entry.kind == "alert") || m is LiveMessage.Reconnected) reload++
        }
    }
    GlassCard(Modifier.fillMaxWidth()) {
        Text("Recent alerts", style = T.label, modifier = Modifier.heading())
        Column(Modifier.padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            val list = items
            when {
                list == null -> Text("Loading…", style = T.tiny)
                list.isEmpty() -> Text("Nothing lately.", style = T.tiny)
            }
            list?.forEach { a -> AlertRow(a, showCamera = true) { a.entityId?.let { onOpen(it, a.id) } } }
        }
    }
}

@Composable
fun AlertRow(a: CameraAlert, showCamera: Boolean, selected: Boolean = false, onClick: () -> Unit) {
    val shape = RoundedCornerShape(16.dp)
    Row(
        Modifier.fillMaxWidth().clip(shape).background(Color(0x0DFFFFFF), shape)
            .then(if (selected) Modifier.background(N.Amber.copy(alpha = 0.18f), shape) else Modifier)
            .clickable(onClickLabel = "play", onClick = onClick).padding(8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Box(Modifier.size(96.dp, 56.dp).clip(RoundedCornerShape(10.dp))) {
            HubImage(a.thumbUrl ?: a.snapshotUrl, Modifier.size(96.dp, 56.dp))
            NIcon("play", Modifier.align(Alignment.Center), size = 16.dp, color = N.Neutral)
        }
        Column(Modifier.weight(1f)) {
            Text(a.objects.joinToString(" & ") { it.replace('_', ' ') }.replaceFirstChar { it.uppercase() }.ifEmpty { a.title },
                style = T.label, maxLines = 1, overflow = TextOverflow.Ellipsis)
            val where = listOfNotNull(if (showCamera) a.cameraName.ifEmpty { null } else null, a.zones.joinToString(", ").ifEmpty { null })
            if (where.isNotEmpty()) Text(where.joinToString(" · "), style = T.tiny, maxLines = 1, overflow = TextOverflow.Ellipsis)
            a.start?.let { Text(DateFormat.getTimeInstance(DateFormat.SHORT).format(Date((it * 1000).toLong())), style = T.tiny.copy(color = N.Text)) }
        }
    }
}

/** Numbers and states devices report ("Around the house"). */
@Composable
fun ValuesTile(tile: Tile, entities: Map<String, Entity>, textScale: Float) {
    val items = (tile.config["items"] as? kotlinx.serialization.json.JsonArray)?.mapNotNull { it.asObjectOrNull }?.mapNotNull { it ->
        val e = entities[it.string("entity")] ?: return@mapNotNull null
        e to e.properties.firstOrNull { p -> p.id == it.string("property") }
    } ?: emptyList()
    GlassCard(Modifier.fillMaxWidth(), padding = PaddingValues(horizontal = 14.dp, vertical = 4.dp)) {
        tile.config.string("title")?.takeIf { it.isNotBlank() }?.let { Text(it, style = T.label, modifier = Modifier.padding(top = 10.dp)) }
        if (items.isEmpty()) Text("No readings chosen.", style = T.tiny, modifier = Modifier.padding(vertical = 12.dp))
        items.forEachIndexed { i, (e, p) ->
            Row(Modifier.fillMaxWidth().heightIn(min = 54.dp).semantics(mergeDescendants = true) {}, verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(e.name, style = T.label.copy(fontSize = 15.sp * textScale), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(p?.label ?: "Not reported", style = T.tiny)
                }
                Text(if (p != null) HubModel.formatProp(p, p.value) else "—", style = T.title.copy(fontSize = 20.sp * textScale))
            }
            if (i < items.lastIndex) Box(Modifier.fillMaxWidth().height(1.dp).background(N.Divider))
        }
    }
}

/** Devices that are offline, low on battery or tampered - urgent alarms (a leak) first. */
@Composable
fun AttentionTile(entities: Map<String, Entity>, meta: Meta?) {
    val bad = HubModel.attention(entities.values, meta)
    GlassCard(
        Modifier.fillMaxWidth(),
        background = if (bad.isNotEmpty()) N.CoralCard else N.Glass,
        border = if (bad.isNotEmpty()) N.CoralBorder else N.Border,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            NIcon(if (bad.isNotEmpty()) "alert" else "check", size = 18.dp, color = if (bad.isNotEmpty()) N.Coral else N.Teal, stroke = 2.2f)
            Text(if (bad.isNotEmpty()) "Needs attention" else "All devices OK", style = T.label, modifier = Modifier.heading())
        }
        Column(Modifier.padding(top = if (bad.isNotEmpty()) 8.dp else 0.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            bad.take(8).forEach { a ->
                Column {
                    Text(a.entity.name, style = T.label.copy(fontSize = 14.sp))
                    Text(a.issues.joinToString(" · "), style = T.tiny.copy(color = if (a.alarm) N.Coral else N.CoralText))
                }
            }
            if (bad.size > 8) Text("+${bad.size - 8} more", style = T.tiny)
        }
    }
}

/** A tile type this app doesn't know yet. */
@Composable
fun SoonTile(tile: Tile) {
    GlassCard(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            NIcon("grid", size = 24.dp, color = N.Muted)
            Column {
                Text(tile.config.string("title") ?: tile.type.replaceFirstChar { it.uppercase() }, style = T.label)
                Text("Coming soon to the app", style = T.tiny)
            }
        }
    }
}
