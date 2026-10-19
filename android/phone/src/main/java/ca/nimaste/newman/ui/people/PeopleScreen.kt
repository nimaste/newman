package ca.nimaste.newman.ui.people

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.net.HubHttp
import ca.nimaste.newman.core.people.PeopleText
import ca.nimaste.newman.core.people.Person
import ca.nimaste.newman.core.people.PersonLook
import ca.nimaste.newman.core.people.PersonRefresh
import ca.nimaste.newman.core.people.ReportSchedule
import ca.nimaste.newman.core.people.SharingSwitch
import ca.nimaste.newman.data.PeopleRepository
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.GlassButton
import ca.nimaste.newman.ui.common.GlassCard
import ca.nimaste.newman.ui.common.heading
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import coil3.compose.AsyncImage
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** The person's colour; [index] (their place in the household) only matters on older hubs that send none. */
fun personColour(p: Person?, index: Int): Color = Color(PersonLook.argb(p, index))

/**
 * A person's round avatar, everywhere they appear: their photo (Coil through the app's mTLS client,
 * cached by its ?v= URL, which changes with the picture) over their single initial on their colour.
 */
@Composable
fun PersonAvatar(person: Person?, index: Int, size: Int = 46, dimmed: Boolean = false) {
    val app = LocalApp.current
    val server by app.store.changes.collectAsState()
    val url = remember(person?.photo, server.serverUrl) {
        PersonLook.photoPath(person)?.let { HubHttp.absolute(server.serverUrl, it)?.toString() }
    }
    Box(Modifier.size(size.dp).alpha(if (dimmed) 0.45f else 1f).clip(CircleShape).background(personColour(person, index)),
        contentAlignment = Alignment.Center) {
        Text(PeopleText.initial(person?.name ?: ""), style = T.title.copy(color = N.Night, fontSize = (size * 0.42f).sp))
        if (url != null) AsyncImage(model = url, contentDescription = null, modifier = Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
    }
}

/** "Updating…" with a spinner, or why it didn't (tap-to-refresh). */
@Composable
fun RefreshNote(state: PersonRefresh.State, now: Double) {
    val text = PersonRefresh.text(state, now) ?: return
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        if (state.phase == PersonRefresh.Phase.UPDATING) CircularProgressIndicator(Modifier.size(12.dp), color = N.Muted, strokeWidth = 2.dp)
        Text(text, style = T.tiny.copy(color = if (state.phase == PersonRefresh.Phase.FAILED) N.CoralText else N.Muted), maxLines = 1)
    }
}

/**
 * The People tab: everyone the hub returns, "Home" / a named place / how far, and when that was.
 * Tapping someone asks their phone for ONE fresh location ("Updating…" until it arrives) and, when
 * they have one, opens them on a map. The list re-reads the hub every 30 s while shown (live messages
 * in between) - that never wakes anyone's phone.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PeopleScreen(onPerson: (String) -> Unit, onMe: () -> Unit, onBack: (() -> Unit)? = null, back: String = "Back") {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    val st by app.people.state.collectAsState()
    val settings by app.sharing.settings.collectAsState()
    val perms by app.sharing.permissions.collectAsState()
    val refreshes by app.people.refreshes.collectAsState()
    var refreshing by remember { mutableStateOf(false) }
    var now by remember { mutableStateOf(System.currentTimeMillis() / 1000.0) }
    LaunchedEffect(Unit) {
        while (true) {
            app.people.refresh()
            now = System.currentTimeMillis() / 1000.0
            delay(ReportSchedule.PEOPLE_REFRESH_SECONDS * 1000L)
        }
    }
    // The household requires sharing but this phone can't: one calm line, with the fix.
    val needsPermission = remember(settings, perms) { settings?.sharing == true && !app.sharing.hasForeground() }

    PullToRefreshBox(
        isRefreshing = refreshing,
        onRefresh = { scope.launch { refreshing = true; app.people.refresh(); now = System.currentTimeMillis() / 1000.0; refreshing = false } },
        modifier = Modifier.fillMaxSize(),
    ) {
        LazyColumn(
            Modifier.fillMaxSize().statusBarsPadding(),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item("head") {
                Column(Modifier.padding(top = if (onBack == null) 14.dp else 4.dp, bottom = 4.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    if (onBack != null) ca.nimaste.newman.ui.home.BackLink(back, onBack)
                    Column(Modifier.padding(horizontal = 4.dp)) {
                        Text("People", style = ca.nimaste.newman.ui.home.HT.subTitle, modifier = Modifier.heading())
                        if (st.people.isNotEmpty()) Text(PeopleText.summary(st.people), style = ca.nimaste.newman.ui.home.HT.line.copy(
                            color = if (st.people.any { it.home }) ca.nimaste.newman.ui.theme.H.GoodText else ca.nimaste.newman.ui.theme.H.Sub))
                    }
                }
            }
            if (st.people.any { it.lat != null && it.lon != null }) item("map") {
                PeopleMap(st.people, Modifier.fillMaxWidth().height(300.dp).clip(androidx.compose.foundation.shape.RoundedCornerShape(22.dp)))
            }
            if (needsPermission) item("perm") {
                GlassCard(Modifier.fillMaxWidth(), background = N.CoralCard, border = N.CoralBorder, onClick = onMe) {
                    Text(if (settings?.mode == "required") "Your household requires location sharing" else "Location sharing is on",
                        style = T.label)
                    Text("Allow location for Newman so your family can see you. Tap to set it up.", style = T.tiny.copy(color = N.CoralText))
                }
            }
            when {
                st.status == PeopleRepository.Status.NOT_SET_UP -> item("none") {
                    Message("Location sharing isn't set up on your hub yet")
                }
                st.people.isEmpty() && st.status == PeopleRepository.Status.LOADING -> item("loading") {
                    Box(Modifier.fillMaxWidth().padding(top = 60.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator(color = N.Text) }
                }
                st.people.isEmpty() && st.status == PeopleRepository.Status.UNREACHABLE -> item("down") {
                    Message("Can't reach Newman right now. Pull to try again.")
                }
                st.people.isEmpty() -> item("empty") { Message("No one in your household yet") }
                else -> itemsIndexed(st.people, key = { _, p -> p.id }) { i, p ->
                    val row = PeopleText.row(p, now)
                    val refresh = refreshes[p.id] ?: PersonRefresh.IDLE
                    GlassCard(Modifier.fillMaxWidth(), radius = 20.dp, padding = PaddingValues(12.dp),
                        onClick = if (p.sharing) ({
                            app.people.refreshPerson(p.id)
                            if (row.located) onPerson(p.id)
                        }) else null) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            PersonAvatar(p, i, dimmed = !p.sharing)
                            Column(Modifier.weight(1f)) {
                                Text(row.name, style = T.label.copy(fontSize = 16.sp), maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                    if (row.home) Box(Modifier.size(7.dp).background(N.Teal, CircleShape))
                                    Text(row.where, style = T.small.copy(color = if (row.home) N.Teal else if (p.sharing) N.Soft else N.Muted),
                                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                                }
                                if (refresh.phase != PersonRefresh.Phase.IDLE) RefreshNote(refresh, System.currentTimeMillis() / 1000.0)
                                else row.updated?.let { Text(it, style = T.tiny) }
                            }
                            if (row.located) NIcon("chevronRight", size = 20.dp, color = N.TabOff)
                        }
                    }
                }
            }
            if (st.status == PeopleRepository.Status.READY && SharingSwitch.state(settings).visible && settings?.sharing == false) item("me") {
                GlassButton("Share my location too", Modifier.fillMaxWidth(), icon = "user", onClick = onMe)
            }
        }
    }
}

@Composable
private fun Message(text: String) {
    Column(Modifier.fillMaxWidth().padding(top = 80.dp, start = 24.dp, end = 24.dp), horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp)) {
        NIcon("people", size = 40.dp, color = N.TabOff)
        Text(text, style = T.body.copy(color = N.Muted, textAlign = TextAlign.Center))
    }
}
