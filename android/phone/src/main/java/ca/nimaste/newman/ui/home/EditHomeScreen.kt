package ca.nimaste.newman.ui.home

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.hub.HubState
import ca.nimaste.newman.core.model.CardPref
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HomeData
import ca.nimaste.newman.core.model.HomeLogic
import ca.nimaste.newman.core.model.HomePrefs
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.common.NTextField
import ca.nimaste.newman.ui.icons.NIcon
import ca.nimaste.newman.ui.theme.H
import kotlinx.coroutines.launch

/** Edit Home: the cards (on or off, dragged into order) and up to four favourites - this person's own. */
@Composable
fun EditHomeScreen(home: HomeData?, onDone: () -> Unit) {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    val state by app.state.collectAsState()
    // Set once when Home loads: the live reloads while editing don't undo the edits.
    var cards by remember(home == null) { mutableStateOf<List<CardPref>>(home?.cards?.map { CardPref(it.id, it.on) } ?: emptyList()) }
    var favs by remember(home == null) { mutableStateOf<List<String>>(home?.favourites ?: emptyList()) }
    var picking by remember { mutableStateOf(false) }
    var saving by remember { mutableStateOf(false) }
    val titles = home?.cards?.associate { it.id to it.title } ?: emptyMap()
    val fresh = home?.cards?.filter { it.new }?.map { it.id }?.toSet() ?: emptySet()
    HScreen("Edit Home", big = false, trailing = {
        AccentButton("Done", enabled = !saving && home != null) {
            saving = true
            scope.launch {
                val ok = app.saveHomePrefs(HomePrefs(cards, favs))
                saving = false
                if (ok) onDone()
            }
        }
    }) {
        item(key = "cards") {
            HCard(padding = PaddingValues(horizontal = 16.dp, vertical = 2.dp)) {
                Caps("Cards · drag to reorder", Modifier.padding(top = 10.dp, bottom = 2.dp))
                ReorderColumn(cards, key = { it.id }, onMove = { a, b -> cards = cards.moved(a, b) }) { c, i, handle ->
                    val title = titles[c.id]?.ifBlank { null } ?: c.id.replaceFirstChar { it.uppercase() }
                    HRow(last = i == cards.lastIndex) {
                        DragHandle(handle, "Drag to reorder $title")
                        Row(Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text(title, style = HT.row.copy(color = if (c.on) H.Text else H.Sub), maxLines = 1, overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.weight(1f, fill = false))
                            // A category that turned up since this layout was saved (off until switched on).
                            if (c.id in fresh) Text("NEW", style = HT.small.copy(fontSize = 11.sp, color = H.Info, fontWeight = FontWeight.ExtraBold),
                                modifier = Modifier.background(H.NewBg, RoundedCornerShape(10.dp)).padding(horizontal = 7.dp, vertical = 2.dp))
                        }
                        HSwitch(c.on, label = if (c.on) "Hide $title" else "Show $title", small = true) { on ->
                            cards = cards.map { if (it.id == c.id) it.copy(on = on) else it }
                        }
                    }
                }
            }
        }
        item(key = "favs") {
            HCard(padding = PaddingValues(start = 16.dp, end = 16.dp, top = 2.dp, bottom = 10.dp)) {
                Caps("Favourites · up to ${HomeLogic.MAX_FAVOURITES}", Modifier.padding(top = 10.dp, bottom = 2.dp))
                favs.forEachIndexed { i, id ->
                    val e = state.named[id]
                    HRow(last = i == favs.lastIndex && favs.size >= HomeLogic.MAX_FAVOURITES) {
                        Column(Modifier.weight(1f)) {
                            Text(e?.name ?: id, style = HT.row, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            e?.let { HomeLogic.members(it) }?.takeIf { it.isNotEmpty() }?.let {
                                Text("${it.size} lights", style = HT.small.copy(color = H.Label, fontWeight = FontWeight.SemiBold))
                            }
                        }
                        Box(
                            Modifier.minimumInteractiveComponentSize().size(30.dp).clip(CircleShape).background(H.RemoveBg)
                                .clickable(role = Role.Button) { favs = favs - id }
                                .clearAndSetSemantics { contentDescription = "Remove ${e?.name ?: id}"; role = Role.Button },
                            contentAlignment = Alignment.Center,
                        ) { Text("−", style = HT.name.copy(fontSize = 18.sp, color = H.DangerIcon)) }
                    }
                }
                if (favs.size < HomeLogic.MAX_FAVOURITES) {
                    Text("+ Add a favourite", style = HT.line.copy(color = H.Info),
                        modifier = Modifier.minimumInteractiveComponentSize().clickable(role = Role.Button) { picking = true }.padding(vertical = 8.dp))
                }
            }
        }
    }
    if (picking) DevicePickerSheet(state, exclude = favs.toSet(), title = "Add a favourite",
        filter = { e -> HubModel.tapAction(e, state.kind(e)) != null }, onDismiss = { picking = false }) { id ->
        favs = (favs + id).take(HomeLogic.MAX_FAVOURITES)
        picking = false
    }
}

/** Devices you can do something with (a tap or a command), searchable; for favourites and automations. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DevicePickerSheet(state: HubState, exclude: Set<String> = emptySet(), title: String, filter: (Entity) -> Boolean = { e ->
    state.kind(e)?.commands?.isNotEmpty() == true
}, onDismiss: () -> Unit, onPick: (String) -> Unit) {
    var query by rememberSaveable { mutableStateOf("") }
    val list = state.named.values.filter { it.id !in exclude && filter(it) && (query.isBlank() || it.name.contains(query.trim(), ignoreCase = true)) }
        .sortedBy { it.name.lowercase() }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = H.Card, contentColor = H.Text) {
        Column(Modifier.padding(horizontal = 16.dp).navigationBarsPadding(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(title, style = HT.name.copy(fontSize = 20.sp))
            NTextField(query, { query = it }, label = "Search")
            LazyColumn(Modifier.fillMaxWidth().heightIn(max = 480.dp)) {
                items(list, key = { it.id }) { e ->
                    val k = state.kind(e)
                    Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).clickable(role = Role.Button) { onPick(e.id) }.padding(vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        NIcon(HubModel.deviceIcon(e, k), size = 20.dp, color = H.Soft)
                        Column(Modifier.weight(1f)) {
                            Text(e.name, style = HT.row, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(listOfNotNull(e.room, k?.name).joinToString(" · "), style = HT.small)
                        }
                    }
                    HDivider()
                }
            }
        }
    }
}
