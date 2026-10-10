package ca.nimaste.newman.ui.tiles

import androidx.compose.foundation.clickable
import androidx.compose.ui.semantics.semantics
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.model.Tile
import ca.nimaste.newman.core.model.TileFit
import ca.nimaste.newman.core.model.string
import ca.nimaste.newman.core.model.strings
import ca.nimaste.newman.core.people.PeopleText
import ca.nimaste.newman.core.people.PeopleTile
import ca.nimaste.newman.core.people.Person
import ca.nimaste.newman.core.today.TodayView
import ca.nimaste.newman.data.PeopleRepository
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.GlassCard
import ca.nimaste.newman.ui.common.LocalFillHeight
import ca.nimaste.newman.ui.people.PersonAvatar
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import kotlinx.coroutines.delay
import java.time.Instant
import java.time.ZoneId

/** What a tile can open outside itself (set by HomeScreen): someone's map (light or dark), the People tab. */
class TileNav(val person: (id: String, light: Boolean) -> Unit = { _, _ -> }, val peopleTab: () -> Unit = {})

val LocalTileNav = staticCompositionLocalOf { TileNav() }

private val PAD = 14.dp

/** The card's inside height in its cell (dp), or infinite on a flowed page (no rectangle). */
@Composable
private fun innerHeight(): Float {
    val fill = LocalFillHeight.current
    return if (fill == Dp.Unspecified) Float.POSITIVE_INFINITY else (fill.value - 2 * PAD.value).coerceAtLeast(0f)
}

/**
 * Where everyone is - the People tab's rows (initial, name, "Home" / a place / how far, when),
 * fitted to the tile: fewer rows and "+N more" (opens the People tab) when they don't all fit, one
 * line when it's too short for rows. Tap someone sharing to follow them on the map (the tile's map
 * look). config.people picks who's shown. Same data as the People tab (kept current by live
 * "person" messages); only loaded here when nothing has loaded it yet.
 */
@Composable
fun PeopleTileView(tile: Tile) {
    val app = LocalApp.current
    val nav = LocalTileNav.current
    val st by app.people.state.collectAsState()
    val now = rememberNow(30_000) / 1000.0
    LaunchedEffect(Unit) {
        if (app.people.state.value.status == PeopleRepository.Status.LOADING) app.people.refresh()
    }
    val list = PeopleTile.select(st.people, tile.config.strings("people"))
    val light = tile.config.string("mapLook") != "dark"
    val ts = tile.textScale
    val ih = innerHeight()
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        val lay = PeopleTile.layout(list.size, maxWidth.value - 2 * PAD.value, ih)
        GlassCard(Modifier.fillMaxWidth(), padding = PaddingValues(PAD)) {
            val message = when {
                st.status == PeopleRepository.Status.NOT_SET_UP -> "Location sharing isn't set up on your hub yet."
                list.isNotEmpty() -> null
                st.status == PeopleRepository.Status.LOADING -> "Loading…"
                st.status == PeopleRepository.Status.UNREACHABLE -> "Can't reach Newman right now."
                else -> "Nobody to show."
            }
            when {
                message != null -> {
                    val titled = ih >= PeopleTile.HEAD + PeopleTile.HEAD_GAP + TileFit.LINE
                    val room = if (titled) ih - PeopleTile.HEAD - PeopleTile.HEAD_GAP else ih
                    val lines = if (room == Float.POSITIVE_INFINITY) 3 else (room / TileFit.LINE).toInt().coerceAtLeast(1)
                    if (titled) Head(null, ts, nav.peopleTab)
                    Text(message, style = T.tiny.copy(fontSize = 13.sp, lineHeight = 18.sp), maxLines = lines, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.padding(top = if (titled) PeopleTile.HEAD_GAP.dp else 0.dp))
                }
                lay.look == PeopleTile.Look.ONE_LINE -> Column(Modifier.fillMaxWidth().clickable(onClick = nav.peopleTab)) {
                    Head(null, ts, null)
                    Text(PeopleTile.oneLine(list), style = T.tiny.copy(fontSize = 13.sp), maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                else -> {
                    if (lay.head) Head(if (lay.moreInHead) "+${lay.more} more" else null, ts, nav.peopleTab)
                    Column(Modifier.padding(top = if (lay.head) PeopleTile.HEAD_GAP.dp else 0.dp), verticalArrangement = Arrangement.spacedBy(PeopleTile.GAP.dp)) {
                        list.take(lay.shown).forEach { p ->
                            val i = st.people.indexOfFirst { it.id == p.id }.coerceAtLeast(0)
                            PersonTileRow(p, i, now, lay.avatars, ts) {
                                app.people.refreshPerson(p.id)
                                nav.person(p.id, light)
                            }
                        }
                    }
                    if (lay.moreBelow) {
                        Text("+${lay.more} more", style = T.tiny.copy(fontSize = 13.sp, fontWeight = FontWeight.ExtraBold), maxLines = 1,
                            modifier = Modifier.fillMaxWidth().height(TileFit.LINE.dp).clickable(onClick = nav.peopleTab))
                    }
                }
            }
        }
    }
}

@Composable
private fun Head(note: String?, ts: Float, onClick: (() -> Unit)?) {
    Row(
        Modifier.fillMaxWidth().height(PeopleTile.HEAD.dp).then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("People", style = T.label.copy(fontSize = 15.sp * ts), maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        if (note != null) Text(note, style = T.tiny.copy(fontSize = 13.sp, fontWeight = FontWeight.ExtraBold), maxLines = 1)
    }
}

/** One person, as on the People tab but one row high; only someone with a location opens the map. */
@Composable
private fun PersonTileRow(p: Person, index: Int, now: Double, avatar: Boolean, ts: Float, onOpen: () -> Unit) {
    val row = PeopleText.row(p, now)
    Row(
        Modifier.fillMaxWidth().height(PeopleTile.ROW.dp).clip(RoundedCornerShape(12.dp))
            .then(if (row.located) Modifier.clickable(onClickLabel = "show on the map", onClick = onOpen) else Modifier.semantics(mergeDescendants = true) {}),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (avatar) PersonAvatar(p, index, size = 36, dimmed = !p.sharing)
        Column(Modifier.weight(1f)) {
            Text(row.name, style = T.label.copy(fontSize = 15.sp * ts, color = if (p.sharing) N.Text else N.Muted),
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(listOfNotNull(row.where, row.updated).joinToString(" · "),
                style = T.tiny.copy(fontSize = 12.sp * ts, color = if (row.home) N.Teal else if (p.sharing) N.Soft else N.Muted),
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

/**
 * "Good morning" and today in one line ("3 things to do · 2 events today · rain from 3 PM"), from
 * the Today card's briefing. The greeting is sized to the tile; when the line doesn't fit under it,
 * both go on one line.
 */
@Composable
fun GreetingTileView() {
    val app = LocalApp.current
    val b by app.today.briefing.collectAsState()
    val done by app.today.done.collectAsState()
    val nowMs = rememberNow(60_000)
    LaunchedEffect(Unit) {
        // Like the Today card: fresh on show, then every minute (live device changes reload it too).
        while (true) {
            app.today.refresh()
            delay(60_000)
        }
    }
    val ih = innerHeight()
    val fit = TodayView.greetingFit(ih)
    val hello = TodayView.greeting(Instant.ofEpochMilli(nowMs).atZone(ZoneId.systemDefault()).hour)
    val line = b?.let { TodayView.greetingLine(it, done, nowMs / 1000.0) }
    val big = T.title.copy(fontSize = fit.size.sp)
    GlassCard(Modifier.fillMaxWidth(), padding = PaddingValues(PAD)) {
        Column(
            Modifier.fillMaxWidth().then(if (ih != Float.POSITIVE_INFINITY) Modifier.height(ih.dp) else Modifier),
            verticalArrangement = Arrangement.Center,
        ) {
            if (line == null || fit.twoLines) {
                Text(hello, style = big, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (line != null) Text(line, style = T.small.copy(color = N.Muted), maxLines = 1, overflow = TextOverflow.Ellipsis)
            } else {
                Text(
                    buildAnnotatedString {
                        append(hello)
                        withStyle(SpanStyle(fontFamily = T.small.fontFamily, fontWeight = FontWeight.Bold, fontSize = 13.sp, color = N.Muted)) {
                            append("  ·  $line")
                        }
                    },
                    style = big, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}
