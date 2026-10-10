package ca.nimaste.newman.widgets

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.ColorFilter
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.GlanceTheme
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.LocalSize
import androidx.glance.action.Action
import androidx.glance.action.ActionParameters
import androidx.glance.action.actionParametersOf
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetManager
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.SizeMode
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.lazy.GridCells
import androidx.glance.appwidget.lazy.LazyVerticalGrid
import androidx.glance.appwidget.lazy.items
import androidx.glance.appwidget.provideContent
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.layout.width
import androidx.glance.semantics.contentDescription
import androidx.glance.semantics.semantics
import ca.nimaste.newman.core.model.Spoken
import androidx.glance.material3.ColorProviders
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import ca.nimaste.newman.MainActivity
import ca.nimaste.newman.core.widgets.WidgetDevice
import ca.nimaste.newman.core.widgets.WidgetTiles

/**
 * The "Newman devices" home-screen widget: a resizable grid of device tiles (icon, name, state).
 * Tapping a device that can do something runs what a tap does now (the hub's primary action), shown
 * at once and then as the hub reports it; a sensitive one asks first when the widget's "Confirm first"
 * is on. Tapping a sensor refreshes. Material 3: the wallpaper's colours on Android 12+, Newman's
 * otherwise.
 */
class DeviceGridWidget : GlanceAppWidget() {
    override val sizeMode: SizeMode = SizeMode.Exact

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val sync = WidgetSync.from(context)
        val widgetId = GlanceAppWidgetManager(context).getAppWidgetId(id)
        provideContent {
            val version by sync.version.collectAsState()
            val devices = remember(version) { sync.shown(widgetId) }
            val configured = remember(version) { sync.devicesOf(widgetId).isNotEmpty() }
            val confirm = remember(version) { sync.confirmSensitive(widgetId) }
            GlanceTheme(colors = if (Build.VERSION.SDK_INT >= 31) GlanceTheme.colors else NewmanColors) {
                Grid(context, widgetId, sync.signedIn, configured, devices, confirm)
            }
        }
    }

    companion object {
        val NewmanColors = ColorProviders(
            light = lightColorScheme(primaryContainer = Color(0xFFBFF3E8), onPrimaryContainer = Color(0xFF00382F),
                secondaryContainer = Color(0xFFE3E7F5), onSecondaryContainer = Color(0xFF151B36),
                surface = Color(0xFFF7F8FC), onSurface = Color(0xFF151B36)),
            dark = darkColorScheme(primaryContainer = Color(0xFF0F5E57), onPrimaryContainer = Color(0xFFEEF2FF),
                secondaryContainer = Color(0xFF1E2546), onSecondaryContainer = Color(0xFFEEF2FF),
                surface = Color(0xFF0B1026), onSurface = Color(0xFFEEF2FF)),
        )
    }
}

val DeviceKey = ActionParameters.Key<String>("device")
val CommandKey = ActionParameters.Key<String>("command")

@Composable
private fun Grid(context: Context, widgetId: Int, signedIn: Boolean, configured: Boolean, devices: List<WidgetDevice>,
                 confirm: Boolean) {
    val size = LocalSize.current
    val root = GlanceModifier.fillMaxSize().background(GlanceTheme.colors.widgetBackground).cornerRadius(18.dp).padding(6.dp)
    when {
        !signedIn -> Message(root, "Sign in to Newman", actionStartActivity(Intent(context, MainActivity::class.java)))
        !configured -> Message(root, "Choose devices", actionStartActivity(WidgetConfigActivity.intent(context, widgetId)))
        devices.isEmpty() -> Message(root, "Loading…", actionRunCallback<RefreshCallback>())
        else -> {
            val cols = WidgetTiles.columns(size.width.value)
            Box(root) {
                LazyVerticalGrid(gridCells = GridCells.Fixed(cols), modifier = GlanceModifier.fillMaxSize()) {
                    items(devices, itemId = { it.id.hashCode().toLong() }) { d ->
                        Box(GlanceModifier.padding(3.dp)) { Tile(context, widgetId, d, confirm) }
                    }
                }
            }
        }
    }
}

@Composable
private fun Message(modifier: GlanceModifier, text: String, onClick: Action) {
    Box(modifier.clickable(onClick), contentAlignment = Alignment.Center) {
        Text(text, style = TextStyle(color = GlanceTheme.colors.onSurface, fontSize = 14.sp, fontWeight = FontWeight.Medium))
    }
}

@Composable
private fun Tile(context: Context, widgetId: Int, d: WidgetDevice, confirm: Boolean) {
    val action = WidgetTiles.tapAction(d)
    val on = d.active && d.available
    val bg: ColorProvider = if (on) GlanceTheme.colors.primaryContainer else GlanceTheme.colors.secondaryContainer
    val fg: ColorProvider = if (on) GlanceTheme.colors.onPrimaryContainer else GlanceTheme.colors.onSecondaryContainer
    val click: Action = when {
        action == null -> actionRunCallback<RefreshCallback>()
        WidgetTiles.needsConfirm(action, confirm) -> actionStartActivity(ConfirmActivity.intent(context, widgetId, d, action))
        else -> actionRunCallback<TapCallback>(actionParametersOf(DeviceKey to d.id, CommandKey to action.command))
    }
    Column(
        GlanceModifier.fillMaxWidth().height(68.dp).background(bg).cornerRadius(14.dp).padding(horizontal = 10.dp, vertical = 8.dp)
            .clickable(click)
            .semantics { contentDescription = WidgetTiles.describe(d) + (action?.let { ". Tap to ${Spoken.phrase(it.label)}" } ?: "") },
        verticalAlignment = Alignment.Top,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Image(ImageProvider(WidgetIcons.bitmap(d.icon, d.kind)), contentDescription = null,
                modifier = GlanceModifier.size(18.dp), colorFilter = ColorFilter.tint(fg))
            Spacer(GlanceModifier.width(6.dp))
            Text(d.name, maxLines = 1, style = TextStyle(color = fg, fontSize = 13.sp, fontWeight = FontWeight.Bold))
        }
        Spacer(GlanceModifier.height(4.dp))
        Text(WidgetTiles.stateText(d), maxLines = 2, style = TextStyle(color = fg, fontSize = 12.sp))
    }
}

/** A tile's tap: run the action (what a tap did when the widget was drawn). */
class TapCallback : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val id = parameters[DeviceKey] ?: return
        val sync = WidgetSync.from(context)
        val action = WidgetTiles.tapAction(sync.cached(id))?.takeIf { it.command == parameters[CommandKey] }
        if (action == null) sync.refresh(listOf(id)) // changed since it was drawn: show it as it is now
        else sync.run(id, action)
    }
}

/** A sensor's tap (and an empty widget's): fresh states for every widget. */
class RefreshCallback : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        WidgetSync.from(context).refresh()
    }
}

class DeviceGridReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = DeviceGridWidget()

    override fun onUpdate(context: Context, appWidgetManager: AppWidgetManager, appWidgetIds: IntArray) {
        super.onUpdate(context, appWidgetManager, appWidgetIds)
        WidgetSync.from(context).refreshSoon(0)
    }

    override fun onEnabled(context: Context) {
        super.onEnabled(context)
        WidgetRefreshWorker.schedule(context)
    }

    override fun onDeleted(context: Context, appWidgetIds: IntArray) {
        super.onDeleted(context, appWidgetIds)
        WidgetSync.from(context).removeWidgets(appWidgetIds)
    }
}

/** A distinct address per widget and device, so each tile's PendingIntent stays its own. */
internal fun widgetUri(what: String, widgetId: Int, deviceId: String = ""): Uri =
    Uri.parse("newman-widget://$what/$widgetId/${Uri.encode(deviceId)}")
