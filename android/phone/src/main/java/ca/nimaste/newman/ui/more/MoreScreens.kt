package ca.nimaste.newman.ui.more

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.model.Automation
import ca.nimaste.newman.core.model.AutomationRun
import ca.nimaste.newman.core.model.Automations
import ca.nimaste.newman.core.model.DeviceList
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.Kind
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.alerts.AlertsScreen
import ca.nimaste.newman.ui.common.BackPill
import ca.nimaste.newman.ui.common.CardHeader
import ca.nimaste.newman.ui.common.GlassButton
import ca.nimaste.newman.ui.common.GlassCard
import ca.nimaste.newman.ui.common.IconBubble
import ca.nimaste.newman.ui.common.NSwitch
import ca.nimaste.newman.ui.common.NTextField
import ca.nimaste.newman.ui.common.Pill
import ca.nimaste.newman.ui.common.heading
import ca.nimaste.newman.ui.common.spoken
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.rememberRunner
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import ca.nimaste.newman.ui.tiles.rememberNow
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId

/** What the More tab shows: its list, or one of the screens it opens. */
object MoreView {
    const val ALERTS = "alerts"
    const val DEVICES = "devices"
    const val AUTOMATIONS = "automations"
    private const val AUTO = "auto:"
    fun automation(id: String) = AUTO + id
    fun automationId(view: String?): String? = view?.takeIf { it.startsWith(AUTO) }?.removePrefix(AUTO)
    /** Where Back goes from [view]: an automation back to the list, the rest back to More. */
    fun parent(view: String?): String? = if (automationId(view) != null) AUTOMATIONS else null
}

/** Rows per section of the Devices screen before "Show all". */
private const val SECTION_ROWS = 5

/**
 * The More tab: Alerts (with its count), Devices and Automations, each its own screen. [view] is
 * kept by HomeScreen so the system Back button and a tapped notification can move it.
 */
@Composable
fun MoreTab(view: String?, onView: (String?) -> Unit, unseen: Int, onCamera: (String, String?) -> Unit, onSheet: (String) -> Unit) {
    val app = LocalApp.current
    var autos by remember { mutableStateOf<List<Automation>?>(null) }
    var loads by remember { mutableStateOf(0) }
    // Loaded for the More row's count; again whenever the Automations screen opens.
    LaunchedEffect(loads, view == MoreView.AUTOMATIONS) {
        app.automations(quiet = view == null)?.let { autos = it }
    }
    val replace: (Automation) -> Unit = { a -> autos = autos?.map { if (it.id == a.id) a else it } }
    val back = { onView(MoreView.parent(view)) }
    val autoId = MoreView.automationId(view)
    when {
        view == MoreView.ALERTS -> AlertsScreen(onCamera = onCamera, onBack = back)
        view == MoreView.DEVICES -> DevicesScreen(onBack = back, onSheet = onSheet)
        view == MoreView.AUTOMATIONS -> AutomationsScreen(autos, onReload = { loads++ }, onBack = back,
            onOpen = { onView(MoreView.automation(it)) }, onChanged = replace)
        autoId != null -> AutomationScreen(autos?.firstOrNull { it.id == autoId }, onBack = back, onChanged = replace,
            onRan = { loads++ })
        else -> MoreHome(unseen, autos, onView)
    }
}

@Composable
private fun MoreHome(unseen: Int, autos: List<Automation>?, onView: (String?) -> Unit) {
    val app = LocalApp.current
    val state by app.state.collectAsState()
    val devices = state.named.values
    val sick = devices.count(DeviceList::needsAttention)
    Column(Modifier.fillMaxSize().statusBarsPadding().verticalScroll(rememberScrollState()).padding(start = 16.dp, end = 16.dp, bottom = 16.dp)) {
        Text("More", style = T.display, modifier = Modifier.padding(start = 2.dp, top = 14.dp, bottom = 14.dp).heading())
        GlassCard(Modifier.fillMaxWidth(), padding = PaddingValues(horizontal = 16.dp, vertical = 4.dp)) {
            MoreRow("bell", N.Coral, "Alerts", "Recent alerts and what happened at home", badge = unseen) { onView(MoreView.ALERTS) }
            Divider()
            val all = DeviceList.summary(devices)
            val head = all.substringBefore(" · ")
            MoreRow("grid", N.Blue, "Devices", buildAnnotatedString {
                append(head)
                if (sick > 0) {
                    append(" · ")
                    withStyle(SpanStyle(color = N.CoralText)) { append(all.substringAfter(" · ")) }
                }
            }) { onView(MoreView.DEVICES) }
            Divider()
            MoreRow("bolt", N.Teal, "Automations", autos?.let { Automations.summary(it) } ?: "What runs by itself") { onView(MoreView.AUTOMATIONS) }
        }
    }
}

@Composable
private fun MoreRow(icon: String, tint: Color, title: String, subtitle: String, badge: Int = 0, onClick: () -> Unit) =
    MoreRow(icon, tint, title, androidx.compose.ui.text.AnnotatedString(subtitle), badge, onClick)

@Composable
private fun MoreRow(icon: String, tint: Color, title: String, subtitle: androidx.compose.ui.text.AnnotatedString, badge: Int = 0, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().defaultMinSize(minHeight = 72.dp).clickable(role = Role.Button, onClick = onClick)
            .spoken(listOfNotNull(title, subtitle.text, if (badge > 0) "$badge new" else null).joinToString(", ")).padding(vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        IconBubble(icon, tint.copy(alpha = 0.16f), tint, size = 44.dp, iconSize = 22.dp, shape = RoundedCornerShape(14.dp))
        Column(Modifier.weight(1f)) {
            Text(title, style = T.label.copy(fontSize = 16.sp))
            Text(subtitle, style = T.small.copy(color = N.Muted), maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        if (badge > 0) {
            Box(Modifier.height(24.dp).widthIn(min = 24.dp).background(N.Live, CircleShape).padding(horizontal = 7.dp), contentAlignment = Alignment.Center) {
                Text(if (badge > 99) "99+" else badge.toString(), style = T.tiny.copy(color = Color.White, fontSize = 12.sp))
            }
        }
        NIcon("chevronRight", size = 18.dp, color = N.TabOff, stroke = 2.4f)
    }
}

@Composable
private fun Divider() = Box(Modifier.fillMaxWidth().height(1.dp).background(N.Divider))

@Composable
private fun ScreenTitle(title: String, back: String, onBack: () -> Unit, line: String? = null) {
    Column(Modifier.padding(top = 10.dp, bottom = 6.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        BackPill(back, onClick = onBack)
        Column {
            Text(title, style = T.display, modifier = Modifier.heading())
            if (line != null) Text(line, style = T.small.copy(color = N.Muted))
        }
    }
}

// -- Devices ---------------------------------------------------------------------------------------

/** Every device the hub has, by category, searchable; tap one for its controls. */
@Composable
fun DevicesScreen(onBack: () -> Unit, onSheet: (String) -> Unit) {
    val app = LocalApp.current
    val state by app.state.collectAsState()
    var query by rememberSaveable { mutableStateOf("") }
    var filter by rememberSaveable { mutableStateOf(DeviceList.Filter.ALL) }
    val open = remember { mutableStateMapOf<String, Boolean>() }
    val all = state.named.values
    val sick = all.count(DeviceList::needsAttention)
    val on = all.count { DeviceList.isOn(it, state.meta) }
    val sections = DeviceList.sections(all, state.meta, query, filter)
    // Searching or filtering shows every match; otherwise a few per section.
    val capped = query.isBlank() && filter == DeviceList.Filter.ALL
    LazyColumn(Modifier.fillMaxSize().statusBarsPadding(), contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item(key = "title") { ScreenTitle("Devices", "More", onBack) }
        item(key = "search") {
            NTextField(query, { query = it }, label = "Search", placeholder = "Search ${all.size} devices")
        }
        item(key = "filters") {
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Pill("All ${all.size}", filter == DeviceList.Filter.ALL) { filter = DeviceList.Filter.ALL }
                if (sick > 0 || filter == DeviceList.Filter.ATTENTION) {
                    Pill("Needs attention $sick", filter == DeviceList.Filter.ATTENTION) { filter = DeviceList.Filter.ATTENTION }
                }
                if (on > 0 || filter == DeviceList.Filter.ON) Pill("On now $on", filter == DeviceList.Filter.ON) { filter = DeviceList.Filter.ON }
            }
        }
        if (sections.isEmpty()) {
            item(key = "none") {
                Text(if (query.isNotBlank()) "No device is called “${query.trim()}”." else "Nothing here.",
                    style = T.body.copy(color = N.Muted), modifier = Modifier.padding(8.dp))
            }
        }
        items(sections, key = { "s:${it.id}" }) { s ->
            val expanded = open[s.id] == true
            val rows = if (capped && !expanded) s.devices.take(SECTION_ROWS) else s.devices
            GlassCard(Modifier.fillMaxWidth(), padding = PaddingValues(start = 16.dp, end = 16.dp, top = 14.dp, bottom = 4.dp)) {
                CardHeader("${s.title} · ${s.devices.size}",
                    action = when {
                        !capped || s.devices.size <= SECTION_ROWS -> null
                        expanded -> "Show less"
                        else -> "Show all ${s.devices.size}"
                    },
                    onAction = { open[s.id] = !expanded })
                rows.forEachIndexed { i, e ->
                    DeviceListRow(e, state.kind(e), last = i == rows.lastIndex) { onSheet(e.id) }
                }
            }
        }
    }
}

/** One device: icon, name, its state (or its problems) and its quick control; tap for everything. */
@Composable
private fun DeviceListRow(e: Entity, k: Kind?, last: Boolean, onSheet: () -> Unit) {
    val app = LocalApp.current
    val run = rememberRunner()
    val busy by app.busy.collectAsState()
    val bad = DeviceList.needsAttention(e)
    val lit = HubModel.isActive(e, k) && k?.secureState == null && e.available
    val tone = HubModel.stateTone(e, k, HubModel.vitals(e).problem)
    val (bubbleBg, bubbleFg, stateColor) = when {
        bad -> Triple(N.CoralBg, N.Coral, N.CoralText)
        lit -> Triple(N.Amber, N.AmberText, N.Amber)
        tone == "warn" -> Triple(N.CoralBg, N.Coral, N.CoralText)
        tone == "calm" -> Triple(N.TealBg, N.Teal, N.Teal)
        else -> Triple(N.Glass, N.Text, N.Muted)
    }
    val duo = HubModel.switchPair(k)
    val action = if (duo == null) HubModel.nextAction(e, k) else null
    Column {
        Row(Modifier.fillMaxWidth().heightIn(min = 64.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(
                Modifier.weight(1f).heightIn(min = 64.dp).clickable(onClickLabel = "show all controls", role = Role.Button, onClick = onSheet)
                    .spoken(listOf(e.name, DeviceList.statusText(e, k)).filter { it.isNotBlank() }.joinToString(", ")),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                IconBubble(if (bad) "alert" else HubModel.kindIcon(k), bubbleBg, bubbleFg, size = 38.dp, iconSize = 19.dp)
                Column(Modifier.weight(1f).padding(vertical = 8.dp)) {
                    Text(e.name, style = T.label, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(DeviceList.statusText(e, k), style = T.small.copy(color = stateColor), maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
            }
            when {
                duo != null && e.available -> NSwitch(HubModel.isActive(e, k), enabled = e.id !in busy, label = e.name) { want ->
                    run.run(e, if (want) duo.on else duo.off)
                }
                action != null -> GlassButton(action.label, Modifier.semantics { contentDescription = "${action.label} ${e.name}" },
                    icon = if (app.needsConfirmation(e, action)) "fingerprint" else null, height = 38.dp,
                    enabled = e.available, busy = e.id in busy) { run.run(e, action) }
            }
        }
        if (!last) Divider()
    }
}

// -- Automations -----------------------------------------------------------------------------------

/** The hub's automations, most recent first: what each does, when it last ran, and an on/off switch. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AutomationsScreen(autos: List<Automation>?, onReload: () -> Unit, onBack: () -> Unit, onOpen: (String) -> Unit, onChanged: (Automation) -> Unit) {
    val nowMs = rememberNow()
    val now = Instant.ofEpochMilli(nowMs)
    val zone = ZoneId.systemDefault()
    var refreshing by remember { mutableStateOf(false) }
    LaunchedEffect(autos) { refreshing = false }
    PullToRefreshBox(isRefreshing = refreshing, onRefresh = { refreshing = true; onReload() }, modifier = Modifier.fillMaxSize()) {
        LazyColumn(Modifier.fillMaxSize().statusBarsPadding(), contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)) {
            item(key = "title") { ScreenTitle("Automations", "More", onBack, autos?.let { Automations.summary(it, now, zone) }) }
            when {
                autos == null -> item(key = "loading") { Text("Loading…", style = T.body.copy(color = N.Muted), modifier = Modifier.padding(8.dp)) }
                autos.isEmpty() -> item(key = "none") { Text("No automations yet.", style = T.body.copy(color = N.Muted), modifier = Modifier.padding(8.dp)) }
                else -> item(key = "list") {
                    val list = Automations.sorted(autos)
                    GlassCard(Modifier.fillMaxWidth(), padding = PaddingValues(horizontal = 16.dp, vertical = 4.dp)) {
                        list.forEachIndexed { i, a ->
                            AutomationRow(a, Automations.lastRunText(a.lastRun, now, zone), onOpen = { onOpen(a.id) }, onChanged = onChanged)
                            if (i < list.lastIndex) Divider()
                        }
                    }
                }
            }
        }
    }
}

/** The switch: shows the change at once; the hub's refusal (an account that can't change automations) puts it back. */
@Composable
private fun AutomationSwitch(a: Automation, onChanged: (Automation) -> Unit) {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    var pending by remember(a.id) { mutableStateOf<Boolean?>(null) }
    NSwitch(pending ?: a.enabled, enabled = pending == null, label = a.name) { want ->
        pending = want
        scope.launch {
            app.setAutomationEnabled(a.id, want)?.let(onChanged)
            pending = null
        }
    }
}

@Composable
private fun AutomationRow(a: Automation, lastRun: String, onOpen: () -> Unit, onChanged: (Automation) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Column(
            Modifier.weight(1f).clickable(onClickLabel = "show details", role = Role.Button, onClick = onOpen),
            verticalArrangement = Arrangement.spacedBy(3.dp),
        ) {
            Text(a.name.ifBlank { "Automation" }, style = T.label)
            if (a.summary.isNotBlank()) Text(a.summary, style = T.small.copy(color = N.Muted, fontSize = 13.sp), maxLines = 3, overflow = TextOverflow.Ellipsis)
            Text(lastRun, style = T.tiny.copy(color = if (a.lastRun?.startedAt != null) N.Teal else N.TabOff))
        }
        AutomationSwitch(a, onChanged)
    }
}

/** One automation: what it does, on or off, its recent runs and Run now. */
@Composable
fun AutomationScreen(a: Automation?, onBack: () -> Unit, onChanged: (Automation) -> Unit, onRan: () -> Unit) {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    val nowMs = rememberNow()
    val now = Instant.ofEpochMilli(nowMs)
    val zone = ZoneId.systemDefault()
    var runs by remember(a?.id) { mutableStateOf<List<AutomationRun>?>(null) }
    var reload by remember { mutableStateOf(0) }
    var running by remember { mutableStateOf(false) }
    LaunchedEffect(a?.id, reload) { if (a != null) runs = app.automationRuns(a.id) }
    Column(Modifier.fillMaxSize().statusBarsPadding().verticalScroll(rememberScrollState()).padding(start = 16.dp, end = 16.dp, bottom = 16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        ScreenTitle(a?.name?.ifBlank { null } ?: "Automation", "Automations", onBack)
        if (a == null) {
            Text("Loading…", style = T.body.copy(color = N.Muted))
            return@Column
        }
        GlassCard(Modifier.fillMaxWidth(), padding = PaddingValues(16.dp)) {
            if (a.summary.isNotBlank()) Text(a.summary, style = T.body.copy(fontSize = 15.sp))
            Row(Modifier.fillMaxWidth().padding(top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(if (a.enabled) "On" else "Off", style = T.label, modifier = Modifier.weight(1f))
                AutomationSwitch(a, onChanged)
            }
            GlassButton("Run now", Modifier.fillMaxWidth().padding(top = 8.dp), icon = "play", busy = running, height = 46.dp) {
                running = true
                scope.launch {
                    if (app.runAutomation(a) != null) {
                        reload++
                        onRan()
                    }
                    running = false
                }
            }
        }
        GlassCard(Modifier.fillMaxWidth(), padding = PaddingValues(start = 16.dp, end = 16.dp, top = 14.dp, bottom = 8.dp)) {
            CardHeader("Recent runs")
            val list = runs
            when {
                list == null -> Text("Loading…", style = T.small.copy(color = N.Muted), modifier = Modifier.padding(vertical = 10.dp))
                list.isEmpty() -> Text("Hasn't run yet.", style = T.small.copy(color = N.Muted), modifier = Modifier.padding(vertical = 10.dp))
                else -> list.forEachIndexed { i, r ->
                    Text(Automations.lastRunText(r, now, zone), style = T.body, modifier = Modifier.padding(vertical = 10.dp))
                    if (i < list.lastIndex) Divider()
                }
            }
        }
    }
}
