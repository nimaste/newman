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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.model.Automation
import ca.nimaste.newman.core.model.Automations
import ca.nimaste.newman.core.model.DeviceList
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.Kind
import ca.nimaste.newman.core.people.PeopleText
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
import ca.nimaste.newman.ui.home.AccentButton
import ca.nimaste.newman.ui.home.HCard
import ca.nimaste.newman.ui.home.HDivider
import ca.nimaste.newman.ui.home.HScreen
import ca.nimaste.newman.ui.home.HSwitch
import ca.nimaste.newman.ui.home.HT
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.people.PeopleScreen
import ca.nimaste.newman.ui.rememberRunner
import ca.nimaste.newman.ui.theme.H
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import ca.nimaste.newman.ui.tiles.rememberNow
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId

/** What the More tab shows: its list, or one of the screens it opens. */
object MoreView {
    const val ALERTS = "alerts"
    const val PEOPLE = "people"
    const val AUTOMATIONS = "automations"
    const val NEW_AUTOMATION = "auto-new"
    private const val AUTO = "auto:"
    fun automation(id: String) = AUTO + id
    fun automationId(view: String?): String? = view?.takeIf { it.startsWith(AUTO) }?.removePrefix(AUTO)
    /** Where Back goes from [view]: an automation (or a new one) back to the list, the rest back to More. */
    fun parent(view: String?): String? = if (automationId(view) != null || view == NEW_AUTOMATION) AUTOMATIONS else null
}

/** Rows per section of the Devices screen before "Show all". */
private const val SECTION_ROWS = 5

/**
 * The More tab: Alerts (with its count), People and Automations, then Edit Home and the account.
 * [view] is kept by HomeScreen so the system Back button and a tapped notification can move it.
 */
@Composable
fun MoreTab(view: String?, onView: (String?) -> Unit, unseen: Int, onCamera: (String, String?) -> Unit, onSheet: (String) -> Unit,
            onPerson: (String) -> Unit, onMe: () -> Unit, canEditHome: Boolean = true, onEditHome: () -> Unit) {
    val app = LocalApp.current
    var autos by remember { mutableStateOf<List<Automation>?>(null) }
    var loads by remember { mutableStateOf(0) }
    // Loaded for the More row's line; again whenever the Automations screen opens.
    LaunchedEffect(loads, view == MoreView.AUTOMATIONS) {
        app.automations(quiet = view == null)?.let { autos = it }
    }
    val replace: (Automation) -> Unit = { a -> autos = autos?.map { if (it.id == a.id) a else it } }
    val back = { onView(MoreView.parent(view)) }
    val autoId = MoreView.automationId(view)
    when {
        view == MoreView.ALERTS -> {
            val highlight by app.openAlert.collectAsState()
            AlertsScreen(onCamera = onCamera, onBack = back, highlight = highlight, onLeave = { app.openAlert.value = null })
        }
        view == MoreView.PEOPLE -> PeopleScreen(onPerson = onPerson, onMe = onMe, onBack = back, back = "More")
        view == MoreView.AUTOMATIONS -> AutomationsScreen(autos, onReload = { loads++ }, onBack = back,
            onOpen = { onView(MoreView.automation(it)) }, onNew = { onView(MoreView.NEW_AUTOMATION) }, onChanged = replace)
        autoId != null || view == MoreView.NEW_AUTOMATION -> AutomationEditor(autoId, onBack = back, onSaved = { loads++ })
        else -> MoreHome(unseen, autos, onView, onMe, if (canEditHome) onEditHome else null)
    }
}

@Composable
private fun MoreHome(unseen: Int, autos: List<Automation>?, onView: (String?) -> Unit, onMe: () -> Unit, onEditHome: (() -> Unit)?) {
    val app = LocalApp.current
    val state by app.state.collectAsState()
    val people by app.people.state.collectAsState()
    LaunchedEffect(Unit) { app.people.refresh() }
    val name = state.auth?.user?.name ?: app.store.userName ?: ""
    HScreen("More") {
        item(key = "main") {
            HCard(padding = PaddingValues(horizontal = 16.dp, vertical = 2.dp)) {
                MoreRow("bell", H.DangerIcon, H.RemoveBg, "Alerts", "Cameras, doorbell, locks, leaks", badge = unseen) { onView(MoreView.ALERTS) }
                HDivider()
                MoreRow("user", H.People, H.PeopleBg, "People", PeopleText.summary(people.people),
                    lineColor = if (people.people.isNotEmpty() && people.people.any { it.home }) H.GoodText else H.Sub) { onView(MoreView.PEOPLE) }
                HDivider()
                MoreRow("bolt", H.Info, Color(0xFF1C2A5A), "Automations", autos?.let { Automations.moreLine(it) } ?: "What runs by itself") {
                    onView(MoreView.AUTOMATIONS)
                }
            }
        }
        item(key = "me") {
            HCard(padding = PaddingValues(horizontal = 16.dp, vertical = 2.dp)) {
                // Hidden when the admin keeps this person on the household Home.
                if (onEditHome != null) {
                    MoreRow("pencil", H.Soft, H.Raised, "Edit Home", "Cards and favourites", onClick = onEditHome)
                    HDivider()
                }
                MoreRow(null, H.Text, H.PeopleBg, name.ifBlank { "Your account" }, "Look, location sharing, password, sign out",
                    initial = name.take(1).uppercase().ifBlank { null }, onClick = onMe)
            }
        }
    }
}

@Composable
private fun MoreRow(icon: String?, tint: Color, bubble: Color, title: String, subtitle: String, badge: Int = 0, lineColor: Color = H.Sub,
                    initial: String? = null, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().defaultMinSize(minHeight = 68.dp).clickable(role = Role.Button, onClick = onClick)
            .spoken(listOfNotNull(title, subtitle, if (badge > 0) "$badge new" else null).joinToString(", ")).padding(vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Box(Modifier.size(40.dp).background(bubble, if (initial != null) CircleShape else RoundedCornerShape(13.dp)), contentAlignment = Alignment.Center) {
            if (initial != null) Text(initial, style = HT.name.copy(fontSize = 16.sp))
            else if (icon != null) NIcon(icon, size = 20.dp, color = tint, stroke = 2.2f)
        }
        Column(Modifier.weight(1f)) {
            Text(title, style = HT.name.copy(fontSize = 16.sp))
            Text(subtitle, style = HT.small.copy(color = lineColor), maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        if (badge > 0) {
            Box(Modifier.height(24.dp).widthIn(min = 24.dp).background(H.Badge, CircleShape).padding(horizontal = 7.dp), contentAlignment = Alignment.Center) {
                Text(if (badge > 99) "99+" else badge.toString(), style = HT.small.copy(color = Color.White, fontWeight = FontWeight.ExtraBold))
            }
        }
    }
}

@Composable
private fun Divider() = Box(Modifier.fillMaxWidth().height(1.dp).background(N.Divider))

@Composable
private fun ScreenTitle(title: String, back: String?, onBack: (() -> Unit)?, line: String? = null) {
    Column(Modifier.padding(top = 10.dp, bottom = 6.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (back != null && onBack != null) BackPill(back, onClick = onBack)
        Column(Modifier.padding(top = if (onBack == null) 8.dp else 0.dp)) {
            Text(title, style = T.display, modifier = Modifier.heading())
            if (line != null) Text(line, style = T.small.copy(color = N.Muted))
        }
    }
}


// -- Devices ---------------------------------------------------------------------------------------

/** Every device the hub has, by category, searchable; tap one for its controls. */
@Composable
fun DevicesScreen(onBack: (() -> Unit)?, onSheet: (String) -> Unit) {
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
internal fun DeviceListRow(e: Entity, k: Kind?, last: Boolean, onSheet: () -> Unit) {
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
                IconBubble(if (bad) "alert" else HubModel.deviceIcon(e, k), bubbleBg, bubbleFg, size = 38.dp, iconSize = 19.dp)
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

/** The hub's automations, most recent first: what each does, when it last ran, and an on/off switch; "+ New" for accounts that may. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AutomationsScreen(autos: List<Automation>?, onReload: () -> Unit, onBack: () -> Unit, onOpen: (String) -> Unit, onNew: () -> Unit,
                      onChanged: (Automation) -> Unit) {
    val app = LocalApp.current
    val home by app.home.collectAsState()
    val state by app.state.collectAsState()
    val can = home?.canAutomate ?: state.auth?.user?.canAutomate ?: false
    val nowMs = rememberNow()
    val now = Instant.ofEpochMilli(nowMs)
    val zone = ZoneId.systemDefault()
    var refreshing by remember { mutableStateOf(false) }
    LaunchedEffect(autos) { refreshing = false }
    PullToRefreshBox(isRefreshing = refreshing, onRefresh = { refreshing = true; onReload() }, modifier = Modifier.fillMaxSize()) {
        HScreen("Automations", back = "More", onBack = onBack, line = autos?.let { Automations.summary(it, now, zone) },
            trailing = if (can) { { AccentButton("+ New", onClick = onNew) } } else null) {
            when {
                autos == null -> item(key = "loading") { Text("Loading…", style = HT.line, modifier = Modifier.padding(8.dp)) }
                autos.isEmpty() -> item(key = "none") { Text("No automations yet.", style = HT.line, modifier = Modifier.padding(8.dp)) }
                else -> item(key = "list") {
                    val list = Automations.sorted(autos)
                    HCard(padding = PaddingValues(horizontal = 16.dp, vertical = 2.dp)) {
                        list.forEachIndexed { i, a ->
                            AutomationRow(a, Automations.lastRunText(a.lastRun, now, zone), can, onOpen = { onOpen(a.id) }, onChanged = onChanged)
                            if (i < list.lastIndex) HDivider()
                        }
                    }
                }
            }
        }
    }
}

/** The switch: shows the change at once; the hub's refusal (an account that can't change automations) puts it back. */
@Composable
private fun AutomationSwitch(a: Automation, enabled: Boolean, onChanged: (Automation) -> Unit) {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    var pending by remember(a.id) { mutableStateOf<Boolean?>(null) }
    HSwitch(pending ?: a.enabled, label = a.name, enabled = enabled && pending == null, small = true) { want ->
        pending = want
        scope.launch {
            app.setAutomationEnabled(a.id, want)?.let(onChanged)
            pending = null
        }
    }
}

@Composable
private fun AutomationRow(a: Automation, lastRun: String, can: Boolean, onOpen: () -> Unit, onChanged: (Automation) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 11.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Column(
            Modifier.weight(1f).clickable(onClickLabel = if (can) "edit" else "show details", role = Role.Button, onClick = onOpen),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            Text(a.name.ifBlank { "Automation" }, style = HT.name)
            if (a.summary.isNotBlank()) Text(a.summary, style = HT.small.copy(fontWeight = FontWeight.SemiBold), maxLines = 3, overflow = TextOverflow.Ellipsis)
            Text(lastRun, style = HT.small.copy(color = if (a.lastRun?.startedAt != null) H.GoodText else H.Label))
        }
        AutomationSwitch(a, can, onChanged)
    }
}
