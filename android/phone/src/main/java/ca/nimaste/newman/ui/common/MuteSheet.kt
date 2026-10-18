package ca.nimaste.newman.ui.common

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.HealthItem
import ca.nimaste.newman.core.model.Muting
import ca.nimaste.newman.ui.LocalApp
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.T
import kotlinx.coroutines.launch

/**
 * “Mute "No AC power"?” - how long to stop showing one of a device's problems: a day, a week, until it
 * clears, or always. Its other problems keep showing. Closes itself once the hub has it.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MuteSheet(e: Entity, problem: HealthItem, onDismiss: () -> Unit) {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var busy by remember { mutableStateOf<String?>(null) }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet, containerColor = Color(0xFF141A33), contentColor = N.Text) {
        Column(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp).padding(bottom = 24.dp).navigationBarsPadding(),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text("Mute “${problem.text}”?", style = T.label.copy(fontSize = 17.sp), modifier = Modifier.heading())
            Text("${e.name}. Its other problems still show.", style = T.small.copy(color = N.Muted), modifier = Modifier.padding(bottom = 8.dp))
            Muting.CHOICES.forEach { (mode, words) ->
                GlassButton(words, Modifier.fillMaxWidth(), height = 48.dp, busy = busy == mode, enabled = busy == null) {
                    busy = mode
                    scope.launch {
                        if (app.mute(e, problem.id, mode)) onDismiss() else busy = null
                    }
                }
            }
            GlassButton("Cancel", Modifier.fillMaxWidth(), height = 48.dp, background = Color.Transparent, border = Color.Transparent,
                foreground = N.Muted, onClick = onDismiss)
        }
    }
}
