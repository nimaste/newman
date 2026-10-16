package ca.nimaste.newman.ui.home

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.hub.HubState
import ca.nimaste.newman.core.live.LiveStatus
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.Page
import ca.nimaste.newman.core.model.PhoneTabs
import ca.nimaste.newman.core.model.Tile
import ca.nimaste.newman.core.model.TileFlow
import ca.nimaste.newman.core.model.number
import ca.nimaste.newman.core.model.string
import ca.nimaste.newman.core.model.strings
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.more.MoreTab
import ca.nimaste.newman.ui.more.MoreView
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import ca.nimaste.newman.ui.common.BackPill
import ca.nimaste.newman.ui.common.CappedFontScale
import ca.nimaste.newman.core.model.TextScale
import ca.nimaste.newman.ui.common.heading
import androidx.compose.foundation.selection.selectable
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.selected
import ca.nimaste.newman.ui.common.Chip
import ca.nimaste.newman.ui.common.GlassCard
import ca.nimaste.newman.ui.device.DeviceSheet
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.people.PeopleScreen
import ca.nimaste.newman.ui.today.TodayCard
import ca.nimaste.newman.ui.today.todaySummary
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.NightBackground
import ca.nimaste.newman.ui.theme.T
import ca.nimaste.newman.ui.tiles.AlertsTile
import ca.nimaste.newman.ui.tiles.AttentionTile
import ca.nimaste.newman.ui.tiles.CalendarTile
import ca.nimaste.newman.ui.tiles.CameraTile
import ca.nimaste.newman.ui.tiles.CompactDial
import ca.nimaste.newman.ui.tiles.DeviceCard
import ca.nimaste.newman.ui.tiles.DeviceListTile
import ca.nimaste.newman.ui.tiles.DialModel
import ca.nimaste.newman.ui.tiles.FullDial
import ca.nimaste.newman.ui.tiles.GreetingTileView
import ca.nimaste.newman.ui.tiles.HorizontalDial
import ca.nimaste.newman.ui.tiles.LocalTileNav
import ca.nimaste.newman.ui.tiles.ModeTile
import ca.nimaste.newman.ui.tiles.PeopleTileView
import ca.nimaste.newman.ui.tiles.SoonTile
import ca.nimaste.newman.ui.tiles.TileNav
import ca.nimaste.newman.ui.tiles.ValuesTile
import ca.nimaste.newman.ui.tiles.WeatherTile
import ca.nimaste.newman.ui.tiles.WideDeviceCard
import ca.nimaste.newman.ui.tiles.WideDialTile
import ca.nimaste.newman.ui.tiles.weatherChip
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.LocalTime
import java.time.format.DateTimeFormatter
import java.util.Locale

private const val MORE_TAB = "__more"
private const val PEOPLE_TAB = "__people"

/** openRequest "device:<id>": open that device's sheet (Device Controls). */
const val OPEN_DEVICE = "device:"

/**
 * Up to five tabs. With a phone layout from the hub (PhoneGrid): its pages marked as tabs (at most
 * three), placed tile by tile on a 4-column grid, then People and More. On older hubs, the
 * dashboard flows instead (PhoneTabs, TileFlow): its first three pages (Overview, Security,
 * Lights), People and More; a Today page becomes the Today card at the top of the first tab.
 * Other pages (Climate) open from their thermostat tile or a link at the bottom of the first tab.
 * More holds Alerts (its count is on the More tab), every device, and the automations.
 */
@Composable
fun HomeScreen(openRequest: MutableStateFlow<String?>, onCamera: (String, String?) -> Unit, onMe: () -> Unit,
               onPerson: (id: String, light: Boolean) -> Unit) {
    val app = LocalApp.current
    val state by app.state.collectAsState()
    val seen by app.alertsSeen.collectAsState()
    val open by openRequest.collectAsState()
    val plan = state.plan
    val pages = plan.tabs + plan.more
    var tab by rememberSaveable { mutableStateOf<String?>(null) }
    var openPage by rememberSaveable { mutableStateOf<String?>(null) }
    var moreView by rememberSaveable { mutableStateOf<String?>(null) }
    var sheetFor by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(open) {
        if (open == "alerts" || open == "people") {
            tab = if (open == "alerts") MORE_TAB else PEOPLE_TAB
            if (open == "alerts") moreView = MoreView.ALERTS
            openPage = null
            openRequest.value = null
        } else if (open?.startsWith(OPEN_DEVICE) == true) {
            // From Device Controls (long-press a control): that device's sheet.
            sheetFor = open!!.removePrefix(OPEN_DEVICE).ifEmpty { null }
            openRequest.value = null
        }
    }
    val current = tab?.takeIf { t -> t == MORE_TAB || t == PEOPLE_TAB || plan.tabs.any { it.id == t } } ?: plan.tabs.firstOrNull()?.id ?: MORE_TAB
    val overlay = openPage?.let { id -> pages.firstOrNull { it.id == id } }
    val unseen = state.activity.count { it.ts > seen }
    BackHandler(enabled = overlay != null) { openPage = null }
    BackHandler(enabled = overlay == null && current == MORE_TAB && moreView != null) { moreView = MoreView.parent(moreView) }
    // A People tile opens someone's map (in its own map look) or, from "+N more", the People tab.
    val tileNav = TileNav(person = onPerson, peopleTab = {
        tab = PEOPLE_TAB
        openPage = null
    })

    CompositionLocalProvider(LocalTileNav provides tileNav) {
    NightBackground(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize()) {
            Box(Modifier.weight(1f)) {
                when {
                    overlay != null && state.loaded ->
                        PageView(overlay, first = false, state, plan, onCamera, onMe, onSheet = { sheetFor = it }, onOpenPage = { openPage = it },
                            onBack = { openPage = null })
                    current == MORE_TAB -> MoreTab(moreView, onView = { moreView = it }, unseen = unseen, onCamera = onCamera,
                        onSheet = { sheetFor = it })
                    current == PEOPLE_TAB -> PeopleScreen(onPerson = { onPerson(it, false) }, onMe = onMe)
                    !state.loaded -> Loading(state)
                    else -> {
                        val page = plan.tabs.first { it.id == current }
                        PageView(page, first = page.id == plan.tabs.firstOrNull()?.id, state, plan, onCamera, onMe, onSheet = { sheetFor = it },
                            onOpenPage = { openPage = it })
                    }
                }
            }
            TabBar(plan.tabs, current, unseen) {
                // More again (or More from elsewhere): its list, not the screen last open in it.
                if (it == MORE_TAB) moreView = null
                tab = it
                openPage = null
            }
        }
    }
    }
    sheetFor?.let { id -> DeviceSheet(id, onDismiss = { sheetFor = null }, onCamera = { sheetFor = null; onCamera(it, null) }) }
}

@Composable
private fun Loading(state: HubState) {
    Column(Modifier.fillMaxSize().statusBarsPadding().padding(24.dp), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
        if (state.error == null) CircularProgressIndicator(color = N.Text)
        else {
            Text("Can't load the house", style = T.title)
            Text(state.error!!, style = T.body.copy(color = N.Muted))
            Text("Retrying when the connection comes back…", style = T.tiny, modifier = Modifier.padding(top = 8.dp))
        }
    }
}

@Composable
private fun PageView(page: Page, first: Boolean, state: HubState, plan: PhoneTabs.Plan, onCamera: (String, String?) -> Unit, onMe: () -> Unit,
                     onSheet: (String) -> Unit, onOpenPage: (String) -> Unit, onBack: (() -> Unit)? = null) {
    if (plan.explicit) GridPageView(page, first, state, plan, onCamera, onMe, onSheet, onOpenPage, onBack)
    else FlowPageView(page, first, state, plan, onCamera, onMe, onSheet, onOpenPage, onBack)
}

/** A dashboard page flowed onto the phone (hubs without phone layouts). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun FlowPageView(page: Page, first: Boolean, state: HubState, plan: PhoneTabs.Plan, onCamera: (String, String?) -> Unit, onMe: () -> Unit,
                         onSheet: (String) -> Unit, onOpenPage: (String) -> Unit, onBack: (() -> Unit)? = null) {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    var refreshing by remember { mutableStateOf(false) }
    val entities = state.named
    val cells = remember(page) { TileFlow.cells(page) }
    var dialSheet by remember { mutableStateOf<Tile?>(null) }
    PullToRefreshBox(
        isRefreshing = refreshing,
        onRefresh = { scope.launch { refreshing = true; app.hub.refresh(); refreshing = false } },
        modifier = Modifier.fillMaxSize(),
    ) {
        LazyVerticalGrid(
            columns = GridCells.Fixed(TileFlow.COLUMNS),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 16.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
            modifier = Modifier.fillMaxSize().statusBarsPadding(),
        ) {
            item(key = "header", span = { GridItemSpan(maxLineSpan) }) {
                if (first) OverviewHeader(state, onMe, page) else PageHeader(page, state, onBack)
            }
            if (first && PhoneTabs.showsTodayCard(plan, page)) {
                item(key = "today", span = { GridItemSpan(maxLineSpan) }) {
                    TodayCard(showWhenEmpty = PhoneTabs.todayWhenEmpty(plan), onOpenDevice = onSheet)
                }
            }
            if (cells.isEmpty()) {
                item(key = "empty", span = { GridItemSpan(maxLineSpan) }) {
                    Text("Nothing on this page yet - add tiles in the Newman dashboard.", style = T.body.copy(color = N.Muted), modifier = Modifier.padding(8.dp))
                }
            }
            items(cells, key = { it.key }, span = { GridItemSpan(it.span) }) { cell ->
                Cell(cell, state, entities, onCamera, onSheet, onDial = { t ->
                    // A thermostat tile opens its own page (Climate) when that page has no tab.
                    val target = PhoneTabs.pageForDial(plan, t.config.string("entity"))
                    if (target != null && target.id != page.id) onOpenPage(target.id) else dialSheet = t
                })
            }
            if (first) {
                items(plan.more, key = { "more:${it.id}" }, span = { GridItemSpan(maxLineSpan) }) { p ->
                    MoreLink(p) { onOpenPage(p.id) }
                }
            }
        }
    }
    dialSheet?.let { t -> DialSheet(t, state, onSheet) { dialSheet = null } }
}

/** A page without a tab, at the bottom of the first tab. */
@Composable
internal fun MoreLink(p: Page, onClick: () -> Unit) {
    GlassCard(Modifier.fillMaxWidth(), radius = 18.dp, padding = PaddingValues(horizontal = 14.dp, vertical = 12.dp), onClick = onClick) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            NIcon(p.icon, size = 22.dp)
            Text(p.name, style = T.label, modifier = Modifier.weight(1f))
            NIcon("chevronRight", size = 20.dp, color = N.TabOff)
        }
    }
}

/** A thermostat's full dial in a sheet (a compact dial tile whose page has a tab, or none). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun DialSheet(t: Tile, state: HubState, onSheet: (String) -> Unit, onDismiss: () -> Unit) {
    val e = state.named[t.config.string("entity")]
    val m = DialModel.of(t, e, state.kind(e))
    if (m == null) {
        LaunchedEffect(t.id) { onDismiss() }
        return
    }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = Color(0xFF141A33), contentColor = N.Text) {
        Column(Modifier.padding(horizontal = 20.dp).padding(bottom = 28.dp).navigationBarsPadding()) {
            FullDial(m)
            Box(Modifier.padding(top = 14.dp)) {
                ca.nimaste.newman.ui.common.GlassButton("More about ${m.e.name}", Modifier.fillMaxWidth(), height = 46.dp) {
                    onDismiss()
                    onSheet(m.e.id)
                }
            }
        }
    }
}

@Composable
private fun Cell(cell: TileFlow.Cell, state: HubState, entities: Map<String, Entity>, onCamera: (String, String?) -> Unit,
                 onSheet: (String) -> Unit, onDial: (Tile) -> Unit) {
    when (val c = cell.content) {
        is TileFlow.Content.Header -> Text(c.title, style = T.label.copy(fontSize = 15.sp), modifier = Modifier.padding(top = 6.dp, start = 4.dp).heading())
        is TileFlow.Content.DeviceCard -> {
            val e = entities[c.entityId]
            if (e == null) GlassCard(Modifier.fillMaxWidth().height(92.dp)) { Text("Missing device", style = T.tiny) }
            else if (c.wide) WideDeviceCard(e, state.kind(e), onSheet = { onSheet(e.id) }, textScale = c.tile.textScale)
            else CappedFontScale(TextScale.CARD_MAX) { DeviceCard(e, state.kind(e), onSheet = { onSheet(e.id) }, textScale = c.tile.textScale) } // a fixed 92dp card
        }
        is TileFlow.Content.WholeTile ->
            if (c.compact) CappedFontScale(TextScale.FITTED_MAX) { TileBody(c.tile, c.compact, state, entities, onCamera, onSheet, onDial) } // half-width, fixed height
            else TileBody(c.tile, c.compact, state, entities, onCamera, onSheet, onDial)
    }
}

/**
 * One whole tile by its type (both the flowed and the placed grid). [fullWidth]: a placed tile
 * across all 4 columns (a short one still gets its wide look: the thermostat side by side).
 */
@Composable
internal fun TileBody(t: Tile, compact: Boolean, state: HubState, entities: Map<String, Entity>, onCamera: (String, String?) -> Unit,
                      onSheet: (String) -> Unit, onDial: (Tile) -> Unit, fullWidth: Boolean = false) {
    val ts = t.textScale
    when (t.type) {
        "camera" -> CameraTile(t, entities, { state.kind(it) }) { onCamera(it, null) }
        "alerts" -> AlertsTile(t, entities, onCamera)
        "dial" -> {
            val e = entities[t.config.string("entity")]
            val m = DialModel.of(t, e, state.kind(e))
            when {
                m == null -> GlassCard(Modifier.fillMaxWidth()) { Text("This dial's device is missing.", style = T.tiny) }
                // Full width but short (the Overview's thermostat): dial left, controls right.
                compact && fullWidth -> HorizontalDial(m, onOpen = { onDial(t) }, textScale = ts)
                compact -> CompactDial(m, onOpen = { onDial(t) }, textScale = ts)
                else -> WideDialTile(m)
            }
        }
        "devices" -> DeviceListTile(t, entities, { state.kind(it) }, { onSheet(it.id) }, ts)
        "mode" -> {
            val e = entities[t.config.string("entity")]
            ModeTile(e, state.kind(e), onSheet = { e?.let { onSheet(it.id) } })
        }
        "values" -> ValuesTile(t, entities, ts)
        "attention" -> AttentionTile(entities, state.meta)
        "weather" -> WeatherTile(t, entities, state.meta, compact, ts)
        "calendar" -> CalendarTile(t, entities, ts)
        "today", "todo" -> TodayCard(showWhenEmpty = true, onOpenDevice = onSheet)
        "people" -> PeopleTileView(t)
        "greeting" -> GreetingTileView()
        else -> SoonTile(t)
    }
}

/**
 * First page: the date, a greeting, the account button and the house's chips (wrapping onto more
 * lines rather than running off the screen; no weather chip when [page] shows the weather itself).
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun OverviewHeader(state: HubState, onMe: () -> Unit, page: Page? = null) {
    val app = LocalApp.current
    val live by app.liveStatus.collectAsState()
    val name = state.auth?.user?.name ?: app.store.userName ?: ""
    val hour = LocalTime.now().hour
    val greeting = when (hour) { in 5..11 -> "Good morning"; in 12..17 -> "Good afternoon"; else -> "Good evening" }
    Column(Modifier.padding(top = 14.dp, bottom = 4.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Column(Modifier.weight(1f)) {
                Text(LocalDate.now().format(DateTimeFormatter.ofPattern("EEEE, MMMM d", Locale.getDefault())), style = T.small.copy(color = N.Muted))
                Text(if (name.isNotBlank()) "$greeting, ${name.substringBefore(' ')}" else greeting, style = T.title.copy(fontSize = 26.sp),
                    maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.heading())
                todaySummary()?.let { Text(it, style = T.small.copy(color = N.Soft), maxLines = 1, overflow = TextOverflow.Ellipsis) }
            }
            Avatar(name, onMe)
        }
        FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (live != LiveStatus.OPEN && state.loaded) Chip("Reconnecting…", dot = N.Amber)
            HubModel.securitySummary(state.named.values, state.meta)?.let { (ok, text) ->
                Chip(text, icon = if (ok) "shield" else "alert", tone = if (ok) N.Teal else N.Coral)
            }
            state.named.values.firstOrNull { HubModel.isMode(state.kind(it)) }?.let { m ->
                Chip("${m.name}: ${(HubModel.stateLabel(m, state.kind(m)) ?: "").lowercase()}")
            }
            if (page?.tiles?.none { it.type == "weather" } != false) weatherChip(state.entities.values, state.meta)?.let { Chip(it) }
            val shownPages = if (state.plan.explicit) state.plan.tabs + state.plan.more else state.dashboard?.pages
            val dashIds = shownPages?.flatMap { p -> p.tiles.flatMap { it.config.strings("entities") } }?.toSet() ?: emptySet()
            val on = HubModel.onCount(state.entities.values.filter { it.id in dashIds }, state.meta)
            if (on > 0) Chip(if (on == 1) "1 on" else "$on on")
            WhereChip(state)
            val sick = state.entities.values.count { !it.health.ok }
            if (sick > 0) Chip(if (sick == 1) "1 device needs attention" else "$sick devices need attention", icon = "alert", tone = N.Coral)
        }
    }
}

@Composable
private fun WhereChip(state: HubState) {
    val a = state.auth ?: return
    if (a.where == "home") Chip("At home", dot = N.Teal)
    else Chip(if (a.certificate) "Away · secure link" else "Away", dot = N.Blue)
}

/** Other pages: the page's name and a summary of its devices. */
@Composable
internal fun PageHeader(page: Page, state: HubState, onBack: (() -> Unit)? = null) {
    val ids = page.tiles.flatMap { it.config.strings("entities") + listOfNotNull(it.config.string("entity")) }.toSet()
    val list = state.named.values.filter { it.id in ids }
    val security = HubModel.securitySummary(list, state.meta)
    val on = HubModel.onCount(list, state.meta)
    Column {
        if (onBack != null) Box(Modifier.padding(top = 10.dp)) { BackPill(onClick = onBack) }
        Row(Modifier.fillMaxWidth().padding(top = 14.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(page.name, style = T.display, modifier = Modifier.weight(1f).heading(), maxLines = 2, overflow = TextOverflow.Ellipsis)
            when {
                security != null -> Chip(security.second, tone = if (security.first) N.Teal else N.Coral)
                on > 0 -> Chip(if (on == 1) "1 on" else "$on on")
                list.isNotEmpty() && list.any { state.kind(it)?.activeState != null && state.kind(it)?.secureState == null } -> Chip("All off")
            }
        }
    }
}

@Composable
fun Avatar(name: String, onClick: () -> Unit) {
    Box(
        Modifier.minimumInteractiveComponentSize().size(44.dp).background(Color(0x1AFFFFFF), CircleShape).border(1.dp, N.BorderHi, CircleShape)
            .clickable(role = Role.Button, onClick = onClick).clearAndSetSemantics { contentDescription = "Your account and settings"; role = Role.Button },
        contentAlignment = Alignment.Center,
    ) {
        if (name.isNotBlank()) Text(name.take(1).uppercase(), style = T.label.copy(fontWeight = FontWeight.ExtraBold))
        else NIcon("user", size = 22.dp)
    }
}

/** The tab bar: each page's icon and name, then People, then More with a badge for unseen activity (Alerts is in More). */
@Composable
private fun TabBar(pages: List<Page>, current: String, unseen: Int, onPick: (String) -> Unit) {
    val tabs = pages.map { Triple(it.id, it.icon, it.name) } + Triple(PEOPLE_TAB, "people", "People") + Triple(MORE_TAB, "more", "More")
    val scroll = tabs.size > 5
    CappedFontScale { // a fixed 64dp bar
    Box(Modifier.fillMaxWidth().background(N.Bar).navigationBarsPadding()) {
        Box(Modifier.fillMaxWidth().height(1.dp).background(N.Divider))
        Row(
            Modifier.fillMaxWidth().height(64.dp).then(if (scroll) Modifier.horizontalScroll(rememberScrollState()) else Modifier).padding(horizontal = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            tabs.forEach { (id, icon, name) ->
                val on = id == current
                Box(
                    (if (scroll) Modifier.widthIn(min = 76.dp) else Modifier.weight(1f)).height(56.dp)
                        .selectable(selected = on, role = Role.Tab) { onPick(id) }
                        .clearAndSetSemantics {
                            contentDescription = if (id == MORE_TAB && unseen > 0) "$name, $unseen new alerts" else name
                            selected = on
                            role = Role.Tab
                        },
                    contentAlignment = Alignment.Center,
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        NIcon(icon, size = 24.dp, color = if (on) N.Text else N.TabOff, stroke = if (on) 2.2f else 2f)
                        Text(name, style = T.tiny.copy(fontSize = 11.sp, color = if (on) N.Text else N.TabOff,
                            fontWeight = if (on) FontWeight.ExtraBold else FontWeight.Bold), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                    if (id == MORE_TAB && unseen > 0) { // the Alerts screen marks them seen
                        Box(
                            Modifier.align(Alignment.TopCenter).padding(start = 30.dp, top = 2.dp).height(18.dp).widthIn(min = 18.dp)
                                .background(N.Red, CircleShape).padding(horizontal = 5.dp),
                            contentAlignment = Alignment.Center,
                        ) { Text(if (unseen > 99) "99+" else unseen.toString(), style = T.tiny.copy(fontSize = 11.sp, color = N.Text)) }
                    }
                }
            }
        }
    }
    }
}
