package ca.nimaste.newman.ui.home

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import ca.nimaste.newman.core.model.HomeLogic
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.more.DeviceListRow

/**
 * Any category without a purpose-built screen - Appliances, Servers, one made in Newman, or one that turns
 * up later: its devices (after any moves), each with its quick control; tap one for everything.
 */
@Composable
fun CategoryScreen(category: String, onBack: () -> Unit, onSheet: (String) -> Unit) {
    val app = LocalApp.current
    val state by app.state.collectAsState()
    val list = remember(state.entities, state.meta, category) { HomeLogic.categoryDevices(state.named.values, state.meta, category) }
    val title = state.meta?.categoryName(category) ?: category.substringAfter('.').replaceFirstChar { it.uppercase() }
    HScreen(title, back = "Home", onBack = onBack, line = "${list.size} ${if (list.size == 1) "device" else "devices"}") {
        item(key = "devices") {
            HCard(padding = PaddingValues(horizontal = 16.dp, vertical = 2.dp)) {
                if (list.isEmpty()) Text("Nothing is in this category now.", style = HT.line, modifier = Modifier.padding(vertical = 14.dp))
                list.forEachIndexed { i, e -> DeviceListRow(e, state.kind(e), last = i == list.lastIndex) { onSheet(e.id) } }
            }
        }
    }
}