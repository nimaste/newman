package ca.nimaste.newman.ui.home

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import ca.nimaste.newman.core.hub.HubState
import ca.nimaste.newman.core.model.Page
import ca.nimaste.newman.core.model.PhoneGrid
import ca.nimaste.newman.core.model.PhoneTabs
import ca.nimaste.newman.core.model.Tile
import ca.nimaste.newman.core.model.string
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.LocalFillHeight
import ca.nimaste.newman.ui.common.CappedFontScale
import ca.nimaste.newman.core.model.TextScale
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import ca.nimaste.newman.ui.tiles.DeviceListTile
import ca.nimaste.newman.ui.tiles.DevicesGridTile
import ca.nimaste.newman.ui.today.TodayCard
import kotlinx.coroutines.launch

private val GAP = 10.dp

/**
 * A page of the hub's phone layout: tiles placed exactly at their x/y/w/h on 4 columns of square
 * cells (PhoneGrid), scrolling vertically. Empty cells stay empty; nothing reflows.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun GridPageView(page: Page, first: Boolean, state: HubState, plan: PhoneTabs.Plan, onCamera: (String, String?) -> Unit, onMe: () -> Unit,
                          onSheet: (String) -> Unit, onOpenPage: (String) -> Unit, onBack: (() -> Unit)? = null) {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    var refreshing by remember { mutableStateOf(false) }
    val entities = state.named
    val tiles = remember(page) { PhoneGrid.placed(page.tiles) }
    var dialSheet by remember { mutableStateOf<Tile?>(null) }
    var moreOf by remember { mutableStateOf<Tile?>(null) }
    val onDial: (Tile) -> Unit = { t ->
        // A thermostat tile opens its own page (Climate) when that page has no tab.
        val target = PhoneTabs.pageForDial(plan, t.config.string("entity"))
        if (target != null && target.id != page.id) onOpenPage(target.id) else dialSheet = t
    }
    PullToRefreshBox(
        isRefreshing = refreshing,
        onRefresh = { scope.launch { refreshing = true; app.hub.refresh(); refreshing = false } },
        modifier = Modifier.fillMaxSize(),
    ) {
        Column(
            Modifier.fillMaxSize().statusBarsPadding().verticalScroll(rememberScrollState())
                .padding(start = 16.dp, end = 16.dp, bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(GAP),
        ) {
            if (first) OverviewHeader(state, onMe) else PageHeader(page, state, onBack)
            if (first && PhoneTabs.showsTodayCard(plan, page)) TodayCard(showWhenEmpty = PhoneTabs.todayWhenEmpty(plan), onOpenDevice = onSheet)
            if (tiles.isEmpty()) {
                Text("Nothing on this page yet - add tiles in the Newman dashboard.", style = T.body.copy(color = N.Muted), modifier = Modifier.padding(8.dp))
            } else {
                BoxWithConstraints(Modifier.fillMaxWidth()) {
                    val width = maxWidth.value
                    val gap = GAP.value
                    Box(Modifier.fillMaxWidth().height(PhoneGrid.height(tiles, width, gap).dp)) {
                        tiles.forEach { t ->
                            key(t.id) {
                                val r = PhoneGrid.rect(t, width, gap)
                                Box(Modifier.offset(r.x.dp, r.y.dp).size(r.width.dp, r.height.dp)) {
                                    // Square cells: a bigger system font only up to what the cell has room for
                                    // (a tile fitted line by line - TileFit - has none to spare).
                                    CappedFontScale(if (t.type == "people" || PhoneGrid.isCompact(t)) TextScale.FITTED_MAX else TextScale.CELL_MAX) {
                                    if (t.type == "devices") {
                                        DevicesGridTile(t, r.width.dp, r.height.dp, entities, { state.kind(it) }, { onSheet(it.id) },
                                            onMore = { moreOf = t }, textScale = t.textScale)
                                    } else {
                                        // The tile's card fills its cell; a tile with more than fits scrolls inside it.
                                        Box(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
                                            CompositionLocalProvider(LocalFillHeight provides r.height.dp) {
                                                TileBody(t, compact = PhoneGrid.isCompact(t), state, entities, onCamera, onSheet, onDial)
                                            }
                                        }
                                    }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            if (first) plan.more.forEach { p -> MoreLink(p) { onOpenPage(p.id) } }
        }
    }
    dialSheet?.let { t -> DialSheet(t, state, onSheet) { dialSheet = null } }
    moreOf?.let { t ->
        ModalBottomSheet(onDismissRequest = { moreOf = null }, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
            containerColor = Color(0xFF141A33), contentColor = N.Text) {
            Column(Modifier.padding(horizontal = 16.dp).padding(bottom = 24.dp).navigationBarsPadding().verticalScroll(rememberScrollState())) {
                DeviceListTile(t, entities, { state.kind(it) }, { moreOf = null; onSheet(it.id) }, t.textScale)
            }
        }
    }
}
