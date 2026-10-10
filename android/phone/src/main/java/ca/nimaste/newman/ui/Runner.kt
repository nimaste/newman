package ca.nimaste.newman.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.platform.LocalContext
import ca.nimaste.newman.core.model.Command
import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.security.BiometricGate
import ca.nimaste.newman.security.findFragmentActivity
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement

val LocalApp = staticCompositionLocalOf<AppViewModel> { error("AppViewModel not provided") }

/** Runs a device command; sensitive ones pass BiometricPrompt (or the screen lock) first. */
fun interface CommandRunner {
    fun run(entity: Entity, command: Command, value: JsonElement?)
    fun run(entity: Entity, command: Command) = run(entity, command, null)
}

@Composable
fun rememberRunner(): CommandRunner {
    val vm = LocalApp.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    return remember(vm, context) {
        CommandRunner { e, c, v ->
            scope.launch {
                if (vm.needsConfirmation(e, c)) {
                    val activity = context.findFragmentActivity()
                    // No activity to show the prompt on means no way to confirm: don't send.
                    if (activity == null || !BiometricGate.confirm(activity, "${c.label} ${e.name}?", "Confirm it's you")) return@launch
                }
                vm.send(e, c, v)
            }
        }
    }
}
