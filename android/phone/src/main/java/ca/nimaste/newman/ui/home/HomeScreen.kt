package ca.nimaste.newman.ui.home

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Text
import androidx.compose.material3.minimumInteractiveComponentSize
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.hub.HubState
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HealthItem
import ca.nimaste.newman.core.model.HomeCard
import ca.nimaste.newman.core.model.HomeData
import ca.nimaste.newman.core.model.HomeLogic
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.cameras.CamerasTab
import ca.nimaste.newman.ui.common.CameraStill
import ca.nimaste.newman.ui.common.CappedFontScale
import ca.nimaste.newman.ui.common.MuteSheet
import ca.nimaste.newman.ui.common.heading
import ca.nimaste.newman.ui.common.spoken
import ca.nimaste.newman.ui.common.tapAndHold
import ca.nimaste.newman.ui.device.DeviceSheet
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.more.DevicesScreen
import ca.nimaste.newman.ui.more.MoreTab
import ca.nimaste.newman.ui.more.MoreView
import ca.nimaste.newman.ui.people.PeopleScreen
import ca.nimaste.newman.ui.rememberRunner
import ca.nimaste.newman.ui.theme.H
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

/** openRequest "device:<id>": open that device's sheet (Device Controls, widgets, live statuses). */
const val OPEN_DEVICE = "device:"

/** The four tabs. */
object Tabs {
    const val HOME = "home"
    const val CAMERAS = "cameras"
    const val DEVICES = "devices"
    const val MORE = "more"
}

/** What the Home tab shows: Home itself, or one of the screens its cards open. */
object HomeView {
    const val CLIMATE = "climate"
    const val CLIMATE_ROOMS = "climate-rooms"
    const val LIGHTS = "lights"
    const val SECURITY = "security"
    const val TODAY = "today"
    const val PEOPLE = "people"
    const val EDIT = "edit"

    /** Where Back goes: Climate's rooms back to Climate, the rest back to Home. */
    fun parent(view: String?): String? = if (view == CLIMATE_ROOMS) CLIMATE else null
}

/**
 * The phone: Home · Cameras · Devices · More. Home's cards open Climate, Lights, Security, Today and
 * People (and Cameras its tab); a device anywhere opens its sheet. More holds Alerts (its count is on
 * the More tab), People, Automations, Edit Home and the account.
 */
@Composable
fun HomeScreen(openRequest: MutableStateFlow<String?>, onCamera: (String, String?) -> Unit, onMe: () -> Unit,
               onPerson: (id: String, light: Boolean) -> Unit) {
    val app = LocalApp.current
    val state by app.state.collectAsState()
    val home by app.home.collectAsState()
    val seen by app.alertsSeen.collectAsState()
    val open by openRequest.collectAsState()
    var tab by rememberSaveable { mutableStateOf(Tabs.HOME) }
    var homeView by rememberSaveable { mutableStateOf<String?>(null) }
    var cameraView by rememberSaveable { mutableStateOf<String?>(null) }
    var moreView by rememberSaveable { mutableStateOf<String?>(null) }
    var sheetFor by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(open) {
        val o = open ?: return@LaunchedEffect
        when {
            o == "alerts" -> { tab = Tabs.MORE; moreView = MoreView.ALERTS; openRequest.value = null }
            o == "people" -> { tab = Tabs.HOME; homeView = HomeView.PEOPLE; openRequest.value = null }
            o == "camera-clip" -> { tab = Tabs.CAMERAS; cameraView = app.openClip.value?.entityId; openRequest.value = null }
            o.startsWith(OPEN_DEVICE) -> { sheetFor = o.removePrefix(OPEN_DEVICE).ifEmpty { null }; openRequest.value = null }
        }
    }
    val unseen = state.activity.count { it.ts > seen }
    BackHandler(enabled = tab == Tabs.HOME && homeView != null) { homeView = HomeView.parent(homeView) }
    BackHandler(enabled = tab == Tabs.CAMERAS && cameraView != null) { cameraView = null }
    BackHandler(enabled = tab == Tabs.MORE && moreView != null) { moreView = MoreView.parent(moreView) }
    BackHandler(enabled = tab != Tabs.HOME && (tab != Tabs.CAMERAS || cameraView == null) && (tab != Tabs.MORE || moreView == null)) { tab = Tabs.HOME }
    val onSheet: (String) -> Unit = { sheetFor = it }

    Box(Modifier.fillMaxSize().background(H.Page)) {
        Column(Modifier.fillMaxSize()) {
            Box(Modifier.weight(1f)) {
                when (tab) {
                    Tabs.CAMERAS -> CamerasTab(home, cameraView, onOpen = { cameraView = it }, onFullScreen = onCamera)
                    Tabs.DEVICES -> DevicesScreen(onBack = null, onSheet = onSheet)
                    Tabs.MORE -> MoreTab(moreView, onView = { moreView = it }, unseen = unseen, onCamera = onCamera, onSheet = onSheet,
                        onPerson = { onPerson(it, false) }, onMe = onMe, onEditHome = { tab = Tabs.HOME; homeView = HomeView.EDIT })
                    else -> when (homeView) {
                        HomeView.CLIMATE -> ClimateScreen(home, onBack = { homeView = null }, onEditRooms = { homeView = HomeView.CLIMATE_ROOMS }, onSheet = onSheet)
                        HomeView.CLIMATE_ROOMS -> ClimateRoomsScreen(home, onDone = { homeView = HomeView.CLIMATE })
                        HomeView.LIGHTS -> LightsScreen(onBack = { homeView = null }, onSheet = onSheet)
                        HomeView.SECURITY -> SecurityScreen(home, onBack = { homeView = null }, onSheet = onSheet,
                            onAlerts = { tab = Tabs.MORE; moreView = MoreView.ALERTS })
                        HomeView.TODAY -> TodayScreen(onBack = { homeView = null }, onSheet = onSheet)
                        HomeView.PEOPLE -> PeopleScreen(onPerson = { onPerson(it, false) }, onMe = onMe, onBack = { homeView = null }, back = "Home")
                        HomeView.EDIT -> EditHomeScreen(home, onDone = { homeView = null })
                        else -> HomeMain(home, state, onView = { homeView = it }, onTab = { tab = it; cameraView = null }, onMe = onMe, onSheet = onSheet)
                    }
                }
            }
            TabBar(tab, unseen) {
                // A tab again (or from elsewhere): its first screen, not the one last open in it.
                when (it) {
                    Tabs.HOME -> homeView = null
                    Tabs.CAMERAS -> cameraView = null
                    Tabs.MORE -> moreView = null
                }
                tab = it
            }
        }
    }
    sheetFor?.let { id -> DeviceSheet(id, onDismiss = { sheetFor = null }, onCamera = { sheetFor = null; onCamera(it, null) }) }
}

/** The floating tab bar: Home · Cameras · Devices · More (More with a badge for unseen alerts). */
@Composable
private fun TabBar(current: String, unseen: Int, onPick: (String) -> Unit) {
    val tabs = listOf(Triple(Tabs.HOME, "home", "Home"), Triple(Tabs.CAMERAS, "camera", "Cameras"),
        Triple(Tabs.DEVICES, "grid", "Devices"), Triple(Tabs.MORE, "more", "More"))
    CappedFontScale {
        Row(
            Modifier.fillMaxWidth().navigationBarsPadding().padding(start = 16.dp, end = 16.dp, top = 6.dp, bottom = 14.dp)
                .clip(RoundedCornerShape(30.dp)).background(H.Raised).border(1.dp, H.RaisedLine, RoundedCornerShape(30.dp)).padding(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            tabs.forEach { (id, icon, name) ->
                val on = id == current
                Box(
                    Modifier.weight(1f).heightIn(min = 52.dp).clip(RoundedCornerShape(24.dp)).background(if (on) H.Selected else Color.Transparent)
                        .selectable(selected = on, role = Role.Tab) { onPick(id) }
                        .clearAndSetSemantics {
                            contentDescription = if (id == Tabs.MORE && unseen > 0) "$name, $unseen new alerts" else name
                            selected = on
                            role = Role.Tab
                        },
                    contentAlignment = Alignment.Center,
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(3.dp),
                        modifier = Modifier.padding(vertical = 6.dp)) {
                        NIcon(icon, size = 22.dp, color = if (on) H.Text else H.Sub)
                        Text(name, style = HT.small.copy(fontSize = 11.sp, color = if (on) H.Text else H.Sub,
                            fontWeight = if (on) FontWeight.ExtraBold else FontWeight.Bold), maxLines = 1)
                    }
                    if (id == Tabs.MORE && unseen > 0) {
                        Box(
                            Modifier.align(Alignment.TopCenter).padding(start = 34.dp, top = 2.dp).height(18.dp).widthIn(min = 18.dp)
                                .background(H.Badge, CircleShape).padding(horizontal = 5.dp),
                            contentAlignment = Alignment.Center,
                        ) { Text(if (unseen > 99) "99+" else unseen.toString(), style = HT.small.copy(fontSize = 11.sp, color = Color.White, fontWeight = FontWeight.ExtraBold)) }
                    }
                }
            }
        }
    }
}

// -- Home ------------------------------------------------------------------------------------------

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun HomeMain(home: HomeData?, state: HubState, onView: (String) -> Unit, onTab: (String) -> Unit, onMe: () -> Unit, onSheet: (String) -> Unit) {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    var refreshing by remember { mutableStateOf(false) }
    var muting by remember { mutableStateOf<Pair<Entity, HealthItem>?>(null) }
    PullToRefreshBox(
        isRefreshing = refreshing,
        onRefresh = { scope.launch { refreshing = true; app.reloadHome(); app.hub.refresh(); refreshing = false } },
        modifier = Modifier.fillMaxSize(),
    ) {
        HScreen("Home", trailing = {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                RoundIcon("pencil", "Edit Home") { onView(HomeView.EDIT) }
                val name = state.auth?.user?.name ?: app.store.userName ?: ""
                Box(
                    Modifier.minimumInteractiveComponentSize().size(40.dp).clip(CircleShape).background(H.Raised).border(1.dp, H.RaisedLine, CircleShape)
                        .clickable(role = Role.Button, onClick = onMe).clearAndSetSemantics { contentDescription = "Your account"; role = Role.Button },
                    contentAlignment = Alignment.Center,
                ) {
                    if (name.isNotBlank()) Text(name.take(1).uppercase(), style = HT.name)
                    else NIcon("user", size = 20.dp, color = H.Text)
                }
            }
        }) {
            if (home == null) {
                item(key = "loading") { Loading(state) }
                return@HScreen
            }
            item(key = "status") { StatusArea(home, state, onSheet, onMute = { e, item -> muting = e to item }) }
            val cards = home.cards.filter { it.on }
            items(cards.chunked(2).size, key = { "cards$it" }) { r ->
                val pair = cards.chunked(2)[r]
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    pair.forEach { c ->
                        Box(Modifier.weight(1f)) {
                            HomeCardView(c, state) {
                                when (c.id) {
                                    "cameras" -> onTab(Tabs.CAMERAS)
                                    "climate" -> onView(HomeView.CLIMATE)
                                    "lights" -> onView(HomeView.LIGHTS)
                                    "security" -> onView(HomeView.SECURITY)
                                    "today" -> onView(HomeView.TODAY)
                                    "people" -> onView(HomeView.PEOPLE)
                                    else -> onTab(Tabs.DEVICES)
                                }
                            }
                        }
                    }
                    if (pair.size == 1) Box(Modifier.weight(1f))
                }
            }
            val favs = home.favourites.mapNotNull { state.named[it] }.take(HomeLogic.MAX_FAVOURITES)
            if (favs.isNotEmpty()) item(key = "favs") { Favourites(favs, state, onSheet) }
        }
    }
    muting?.let { (e, item) -> MuteSheet(e, item) { muting = null } }
}

@Composable
private fun RoundIcon(icon: String, label: String, onClick: () -> Unit) {
    Box(
        Modifier.minimumInteractiveComponentSize().size(40.dp).clip(CircleShape).background(H.Raised).border(1.dp, H.RaisedLine, CircleShape)
            .clickable(role = Role.Button, onClick = onClick).clearAndSetSemantics { contentDescription = label; role = Role.Button },
        contentAlignment = Alignment.Center,
    ) { NIcon(icon, size = 18.dp, color = H.Soft, stroke = 2.2f) }
}

@Composable
private fun Loading(state: HubState) {
    Column(Modifier.fillMaxWidth().padding(top = 80.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (state.error == null) CircularProgressIndicator(color = H.Text)
        else {
            Text("Can't load the house", style = HT.name.copy(fontSize = 20.sp))
            Text(state.error!!, style = HT.line)
            Text("Retrying when the connection comes back…", style = HT.small)
        }
    }
}

/**
 * The top of Home: each danger (red, with its fix: "Close") or "All good at home" with the hub's
 * summary; then the devices' warnings (amber, each with Mute).
 */
@Composable
private fun StatusArea(home: HomeData, state: HubState, onSheet: (String) -> Unit, onMute: (Entity, HealthItem) -> Unit) {
    val run = rememberRunner()
    val app = LocalApp.current
    val busy by app.busy.collectAsState()
    val s = home.status
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        // ok: the green "All good at home"; warn (no danger): a neutral "Needs a look"; danger: only its banners.
        if (s.danger.isEmpty()) {
            val ok = s.level == "ok"
            val title = if (ok) "All good at home" else "Needs a look"
            Row(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(if (ok) H.GoodBg else H.Card)
                    .border(1.dp, if (ok) H.GoodLine else H.CardLine, RoundedCornerShape(18.dp)).padding(horizontal = 16.dp, vertical = 12.dp)
                    .spoken(listOf(title, s.summary).filter { it.isNotBlank() }.joinToString(", ")),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Box(Modifier.size(10.dp).background(if (ok) H.Good else H.Light, CircleShape))
                Column(Modifier.weight(1f)) {
                    Text(title, style = HT.name.copy(color = if (ok) H.GoodHead else H.Text))
                    if (s.summary.isNotBlank()) Text(s.summary, style = HT.small.copy(color = if (ok) H.GoodSub else H.Sub, fontWeight = FontWeight.SemiBold))
                }
            }
        }
        s.danger.forEach { d ->
            val e = d.entity?.let { state.named[it] }
            Row(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(H.DangerBg).border(1.dp, H.DangerLine, RoundedCornerShape(18.dp))
                    .then(if (e != null) Modifier.clickable(role = Role.Button, onClickLabel = "show its controls") { onSheet(e.id) } else Modifier)
                    .padding(start = 14.dp, end = 12.dp, top = 12.dp, bottom = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Box(Modifier.size(34.dp).background(H.DangerBubble, CircleShape), contentAlignment = Alignment.Center) {
                    NIcon(e?.let { HubModel.kindIcon(state.kind(it)) } ?: "alert", size = 18.dp, color = H.DangerIcon, stroke = 2.2f)
                }
                Column(Modifier.weight(1f)) {
                    Text(d.title, style = HT.name.copy(color = H.DangerHead))
                    if (d.detail.isNotBlank()) Text(d.detail, style = HT.small.copy(color = H.DangerSub, fontWeight = FontWeight.SemiBold))
                }
                val a = d.action
                val cmd = if (e != null && a != null) state.kind(e)?.command(a.command) else null
                if (e != null && a != null && cmd != null) {
                    RaisedButton(a.label, background = H.DangerButton, border = H.DangerButton, color = H.DangerButtonText,
                        enabled = e.id !in busy, onClickLabel = "${a.label} ${e.name}") { run.run(e, cmd) }
                }
            }
        }
        s.warnings.forEach { w ->
            val e = state.named[w.entity]
            Row(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(H.WarnBg).border(1.dp, H.WarnLine, RoundedCornerShape(14.dp))
                    .then(if (e != null) Modifier.clickable(role = Role.Button, onClickLabel = "show its controls") { onSheet(e.id) } else Modifier)
                    .padding(start = 14.dp, end = 6.dp, top = 4.dp, bottom = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                NIcon("alert", size = 16.dp, color = H.WarnText, stroke = 2.2f)
                Text(w.text, style = HT.line.copy(color = H.WarnText), modifier = Modifier.weight(1f).padding(vertical = 8.dp))
                if (e != null) {
                    Text("Mute", style = HT.small.copy(color = H.WarnSub),
                        modifier = Modifier.minimumInteractiveComponentSize().clickable(role = Role.Button, onClickLabel = "mute ${w.text}") {
                            onMute(e, e.health.items.firstOrNull { it.id == w.issue } ?: HealthItem(w.issue, w.text))
                        }.padding(horizontal = 8.dp))
                }
            }
        }
    }
}

/** One Home card: its name and icon, the hub's value and detail (the cameras card: its little picture). */
@Composable
private fun HomeCardView(c: HomeCard, state: HubState, onClick: () -> Unit) {
    val danger = c.tone == "danger"
    val detailColor = when (c.tone) {
        "danger" -> H.HotText
        "good" -> H.GoodText
        "warm" -> if (c.id == "lights") H.LightText else H.WarmText
        else -> H.Sub
    }
    val shape = RoundedCornerShape(20.dp)
    Column(
        Modifier.fillMaxWidth().height(118.dp).clip(shape).background(if (danger) H.Hot else H.Card).border(1.dp, if (danger) H.HotLine else H.CardLine, shape)
            .clickable(role = Role.Button, onClick = onClick).spoken(listOf(c.title, c.value, c.detail).filter { it.isNotBlank() }.joinToString(", "))
            .padding(if (c.id == "cameras") 10.dp else 14.dp),
        verticalArrangement = if (c.id == "cameras") Arrangement.spacedBy(8.dp) else Arrangement.SpaceBetween,
    ) {
        if (c.id == "cameras") {
            val cam = c.camera?.let { state.named[it] }
            Box(Modifier.fillMaxWidth().height(58.dp).clip(RoundedCornerShape(12.dp)).background(H.Picture)) {
                if (cam != null) CameraStill(cam, Modifier.fillMaxSize(), heightPx = 240)
                Row(Modifier.align(Alignment.TopEnd).padding(6.dp).background(Color(0x990A1024), RoundedCornerShape(6.dp)).padding(horizontal = 4.dp),
                    verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    Box(Modifier.size(6.dp).background(H.LiveDot, CircleShape))
                    Text("LIVE", style = HT.small.copy(fontSize = 10.sp, color = H.Text, fontWeight = FontWeight.ExtraBold))
                }
                if (cam != null) Text(cam.name, style = HT.small.copy(fontSize = 11.sp, color = H.Soft, fontWeight = FontWeight.ExtraBold),
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.align(Alignment.BottomStart).padding(start = 8.dp, bottom = 4.dp))
            }
            Column(Modifier.padding(horizontal = 2.dp)) {
                Text(c.title, style = HT.name.copy(fontSize = 14.sp), maxLines = 1)
                Text(listOf(c.value, c.detail).filter { it.isNotBlank() }.joinToString(" · "), style = HT.small.copy(color = detailColor),
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            return@Column
        }
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(c.title, style = HT.name.copy(fontSize = 14.sp), modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
            val (icon, tint) = cardIcon(c.id)
            NIcon(icon, size = 20.dp, color = if (danger) H.HotText else tint, stroke = 2.2f)
        }
        if (c.id == "people") {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Box(Modifier.size(34.dp).clip(CircleShape).background(H.PeopleBg).border(2.dp, if (c.tone == "good") H.Good else H.RaisedLine, CircleShape),
                    contentAlignment = Alignment.Center) { Text(c.value.take(1).uppercase(), style = HT.name.copy(fontSize = 14.sp)) }
                Column {
                    Text(c.value, style = HT.name.copy(fontSize = 14.sp), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(c.detail, style = HT.small.copy(color = detailColor), maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
        } else {
            Column {
                val size = HomeLogic.valueSize(c.value).sp
                Text(c.value, style = HT.name.copy(fontSize = size, letterSpacing = (-0.5).sp), maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(c.detail, style = HT.small.copy(color = detailColor), maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

/** Each card's icon and colour - the one table (the cards are the hub's fixed list; the cameras card shows its picture). */
internal fun cardIcon(id: String): Pair<String, Color> = when (id) {
    "cameras" -> "camera" to H.Info
    "climate" -> "thermometer" to H.Warm
    "lights" -> "bulb" to H.Light
    "security" -> "shield" to H.Good
    "today" -> "calendar" to H.Info
    "people" -> "user" to H.People
    "cleaning" -> "vacuum" to H.Info
    "appliances" -> "power" to H.Info
    else -> "dot" to H.Sub
}

/** Up to four favourites: tap does what a tap does (on/off, lock…), hold shows everything. */
@Composable
private fun Favourites(favs: List<Entity>, state: HubState, onSheet: (String) -> Unit) {
    val run = rememberRunner()
    val app = LocalApp.current
    val busy by app.busy.collectAsState()
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("FAVOURITES", style = HT.caps, modifier = Modifier.padding(horizontal = 4.dp).heading())
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            favs.forEach { e ->
                val k = state.kind(e)
                val lit = HubModel.isActive(e, k) && k?.secureState == null
                val open = HubModel.notSecure(e, k)
                val (bg, line, fg) = when {
                    lit -> Triple(H.LightBg, H.LightLine, H.LightText)
                    open -> Triple(H.Hot, H.HotLine, H.HotText)
                    k?.secureState != null -> Triple(H.Raised, H.TileLine, H.GoodText)
                    else -> Triple(H.Raised, H.TileLine, H.Sub)
                }
                val tap = HubModel.tapAction(e, k)
                val shape = RoundedCornerShape(18.dp)
                Column(
                    Modifier.weight(1f).height(86.dp).clip(shape).background(bg, shape).border(1.dp, line, shape)
                        .tapAndHold(onTap = { if (tap != null && e.id !in busy) run.run(e, tap) else onSheet(e.id) }, onHold = { onSheet(e.id) },
                            tapLabel = tap?.label?.lowercase() ?: "show its controls", holdLabel = "show all controls")
                        .spoken(listOf(e.name, HubModel.entityStateText(e, k)).joinToString(", "))
                        .padding(10.dp),
                    verticalArrangement = Arrangement.SpaceBetween,
                ) {
                    NIcon(HubModel.kindIcon(k), size = 18.dp, color = if (lit) H.Light else fg, stroke = 2.2f)
                    Column {
                        Text(e.name, style = HT.small.copy(fontSize = 11.sp, color = H.Text, fontWeight = FontWeight.ExtraBold), maxLines = 2,
                            overflow = TextOverflow.Ellipsis, lineHeight = 13.sp)
                        Text(HubModel.entityStateText(e, k), style = HT.small.copy(fontSize = 11.sp, color = fg), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
            repeat(HomeLogic.MAX_FAVOURITES - favs.size) { Box(Modifier.weight(1f)) }
        }
    }
}
