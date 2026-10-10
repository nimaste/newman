package ca.nimaste.newman.widgets

import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.lifecycleScope
import ca.nimaste.newman.core.api.ApiErrors
import ca.nimaste.newman.core.api.NewmanApi
import ca.nimaste.newman.core.api.hubCall
import ca.nimaste.newman.core.widgets.WidgetAction
import ca.nimaste.newman.core.widgets.WidgetChoice
import ca.nimaste.newman.core.widgets.WidgetDevice
import ca.nimaste.newman.core.widgets.WidgetPicker
import ca.nimaste.newman.ui.theme.N
import ca.nimaste.newman.ui.theme.NewmanTheme
import ca.nimaste.newman.ui.theme.T
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.launch

@EntryPoint
@InstallIn(SingletonComponent::class)
interface WidgetApiAccess {
    fun api(): NewmanApi
}

/** The devices list for a picker, or why it couldn't be loaded. */
private suspend fun loadChoices(context: Context): Result<List<WidgetChoice>> {
    val sync = WidgetSync.from(context)
    if (!sync.signedIn) return Result.failure(IllegalStateException("Sign in to Newman first"))
    val api = EntryPointAccessors.fromApplication(context.applicationContext, WidgetApiAccess::class.java).api()
    return try {
        Result.success(hubCall { api.widgetDevices() }.devices)
    } catch (e: Exception) {
        Result.failure(IllegalStateException(ApiErrors.message(e)))
    }
}

/** Adding a "Newman devices" widget, and changing one later (its reconfigure). */
class WidgetConfigActivity : ComponentActivity() {
    private var devices by mutableStateOf<List<WidgetChoice>?>(null)
    private var error by mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val widgetId = intent?.extras?.getInt(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID)
            ?: AppWidgetManager.INVALID_APPWIDGET_ID
        setResult(RESULT_CANCELED, Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, widgetId))
        if (widgetId == AppWidgetManager.INVALID_APPWIDGET_ID) return finish()
        val sync = WidgetSync.from(this)
        val initial = sync.devicesOf(widgetId)
        val initialConfirm = sync.confirmSensitive(widgetId)
        lifecycleScope.launch {
            loadChoices(this@WidgetConfigActivity)
                .onSuccess { list -> devices = list }
                .onFailure { error = it.message }
        }
        setContent {
            NewmanTheme {
                val list = devices
                DevicePicker("Newman widget", list, error, multi = true,
                    initial = if (list == null) initial else WidgetPicker.keepKnown(initial, list),
                    initialConfirm = initialConfirm,
                    onSave = { ids, confirm ->
                        sync.setWidget(widgetId, ids, confirm)
                        sync.scope.launch { sync.refresh() }
                        setResult(RESULT_OK, Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, widgetId))
                        finish()
                    },
                    onCancel = { finish() })
            }
        }
    }

    companion object {
        fun intent(context: Context, widgetId: Int): Intent = Intent(context, WidgetConfigActivity::class.java)
            .putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, widgetId)
            .setData(widgetUri("configure", widgetId))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }
}

/** A Quick Settings tile's settings (long-press the tile): which device it shows and runs. */
class TileSettingsActivity : ComponentActivity() {
    private var devices by mutableStateOf<List<WidgetChoice>?>(null)
    private var error by mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val component: ComponentName? = intent?.let {
            @Suppress("DEPRECATION") it.getParcelableExtra(Intent.EXTRA_COMPONENT_NAME)
        }
        val slot = QuickTiles.slotOf(component?.className) ?: intent?.getIntExtra(EXTRA_SLOT, 0)?.takeIf { it in 1..QuickTiles.SLOTS }
            ?: return finish()
        val sync = WidgetSync.from(this)
        val initial = listOfNotNull(sync.tileDevice(slot))
        lifecycleScope.launch {
            loadChoices(this@TileSettingsActivity)
                .onSuccess { list -> devices = list }
                .onFailure { error = it.message }
        }
        setContent {
            NewmanTheme {
                DevicePicker("Newman $slot tile", devices, error, multi = false, initial = initial, initialConfirm = false,
                    onSave = { ids, _ ->
                        sync.setTileDevice(slot, ids.firstOrNull())
                        sync.scope.launch { sync.refresh() }
                        finish()
                    },
                    onCancel = { finish() })
            }
        }
    }

    companion object {
        const val EXTRA_SLOT = "ca.nimaste.newman.TILE_SLOT"
    }
}

/** "Unlock Side Door?" before a sensitive tap, on widgets with "Confirm first" on. */
class ConfirmActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val id = intent?.getStringExtra(EXTRA_DEVICE) ?: return finish()
        val command = intent?.getStringExtra(EXTRA_COMMAND) ?: return finish()
        val label = intent?.getStringExtra(EXTRA_LABEL) ?: command
        val name = intent?.getStringExtra(EXTRA_NAME) ?: ""
        val sync = WidgetSync.from(this)
        setContent {
            NewmanTheme {
                AlertDialog(
                    onDismissRequest = { finish() },
                    containerColor = N.Night,
                    title = { Text("$label $name?".replace(" ?", "?"), style = T.title) },
                    confirmButton = {
                        TextButton(onClick = {
                            // What a tap does may have changed since the widget was drawn: run it only if it still is.
                            val now = sync.cached(id)?.actions?.firstOrNull { it.primary }
                            val action = now?.takeIf { it.command == command } ?: WidgetAction(command, label, sensitive = true)
                            sync.scope.launch { sync.run(id, action) }
                            finish()
                        }) { Text(label, style = T.label.copy(color = N.Teal)) }
                    },
                    dismissButton = { TextButton(onClick = { finish() }) { Text("Cancel", style = T.label.copy(color = N.Muted)) } },
                )
            }
        }
    }

    companion object {
        private const val EXTRA_DEVICE = "device"
        private const val EXTRA_COMMAND = "command"
        private const val EXTRA_LABEL = "label"
        private const val EXTRA_NAME = "name"

        fun intent(context: Context, widgetId: Int, d: WidgetDevice, a: WidgetAction): Intent =
            Intent(context, ConfirmActivity::class.java)
                .setData(widgetUri("confirm", widgetId, d.id))
                .putExtra(EXTRA_DEVICE, d.id).putExtra(EXTRA_COMMAND, a.command).putExtra(EXTRA_LABEL, a.label)
                .putExtra(EXTRA_NAME, d.name)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_HISTORY or Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS)
    }
}
