package ca.nimaste.newman.widgets

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.unit.dp
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.LocalSize
import androidx.glance.action.ActionParameters
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetManager
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.SizeMode
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.provideContent
import androidx.glance.background
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.fillMaxHeight
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.padding
import ca.nimaste.newman.MainActivity
import ca.nimaste.newman.core.widgets.WidgetDevice
import ca.nimaste.newman.core.widgets.WidgetLayout
import ca.nimaste.newman.core.widgets.WidgetTiles

/**
 * The home-screen device widgets, in two styles: "Devices – tiles" (icon, name, state) and "Devices – rows"
 * (one full-width row per device). The person chooses which devices, and how many (up to 8), when adding
 * or reconfiguring one; the widget shows as many as its current size takes - 2 on a small one, 4 on a
 * medium (or narrow) one, 8 on a large one - and lays them out to fill it, again on every resize. Tapping
 * a device that can do something runs what a tap does now (the hub's primary action), shown at once and
 * then as the hub reports it; a sensitive one asks first when the widget's "Confirm first" is on. Tapping
 * a sensor opens the app on it.
 */
enum class DevicesStyle { TILES, ROWS }

abstract class DevicesWidget(private val style: DevicesStyle) : GlanceAppWidget() {
    override val sizeMode: SizeMode = SizeMode.Exact

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val sync = WidgetSync.from(context)
        val widgetId = GlanceAppWidgetManager(context).getAppWidgetId(id)
        provideContent {
            val version by sync.version.collectAsState()
            val devices = remember(version) { sync.shown(widgetId) }
            val configured = remember(version) { sync.devicesOf(widgetId).isNotEmpty() }
            val confirm = remember(version) { sync.confirmSensitive(widgetId) }
            DevicesBody(context, widgetId, style, sync.signedIn, configured, devices, confirm)
        }
    }
}

/** "Devices – tiles". The class (and its receiver) keep the first widget's names, so widgets already on a
 * home screen keep working. */
class DeviceGridWidget : DevicesWidget(DevicesStyle.TILES)

/** "Devices – rows". */
class DeviceRowsWidget : DevicesWidget(DevicesStyle.ROWS)

val DeviceKey = ActionParameters.Key<String>("device")
val CommandKey = ActionParameters.Key<String>("command")

private const val PAD = 6
private const val GAP = 6

@Composable
private fun DevicesBody(context: Context, widgetId: Int, style: DevicesStyle, signedIn: Boolean, configured: Boolean,
                        devices: List<WidgetDevice>, confirm: Boolean) {
    when {
        !signedIn -> WidgetMessage("Sign in to Newman", actionStartActivity(Intent(context, MainActivity::class.java)))
        !configured -> WidgetMessage("Choose devices", actionStartActivity(WidgetConfigActivity.intent(context, widgetId)))
        devices.isEmpty() -> WidgetMessage("Loading…", actionRunCallback<RefreshCallback>())
        else -> {
            val size = LocalSize.current
            val w = size.width.value
            val h = size.height.value
            val root = GlanceModifier.fillMaxSize().background(WidgetColors.background).cornerRadius(18.dp).padding(PAD.dp)
            if (style == DevicesStyle.ROWS) {
                val shown = devices.take(minOf(WidgetLayout.maxDevices(w, h), WidgetLayout.rowsThatFit(h - 2 * PAD)))
                Rows(context, widgetId, root, shown, confirm, innerHeight = h - 2 * PAD)
            } else {
                val shown = devices.take(WidgetLayout.maxDevices(w, h))
                Tiles(context, widgetId, root, shown, confirm, wide = WidgetLayout.isWide(w, h), innerHeight = h - 2 * PAD)
            }
        }
    }
}

@Composable
private fun Tiles(context: Context, widgetId: Int, root: GlanceModifier, shown: List<WidgetDevice>, confirm: Boolean,
                  wide: Boolean, innerHeight: Float) {
    val lines = WidgetLayout.lines(shown.size, WidgetLayout.tileColumns(shown.size, wide))
    val starts = lines.runningFold(0) { a, n -> a + n }
    val tileHeight = (innerHeight - GAP * (lines.size - 1)) / lines.size.coerceAtLeast(1)
    val compact = WidgetLayout.tileCompact(tileHeight)
    val big = shown.size == 1
    // Gaps are padding, not Spacers: a Glance Row / Column takes at most 10 children.
    Column(root) {
        lines.forEachIndexed { i, n ->
            val line = shown.subList(starts[i], starts[i] + n)
            Row(GlanceModifier.fillMaxWidth().defaultWeight().padding(bottom = if (i < lines.lastIndex) GAP.dp else 0.dp)) {
                line.forEachIndexed { j, d ->
                    Box(GlanceModifier.defaultWeight().fillMaxHeight().padding(end = if (j < line.lastIndex) GAP.dp else 0.dp)) {
                        DeviceTileFace(d, deviceClick(context, widgetId, d, confirm), compact = compact, big = big)
                    }
                }
            }
        }
    }
}

@Composable
private fun Rows(context: Context, widgetId: Int, root: GlanceModifier, shown: List<WidgetDevice>, confirm: Boolean,
                 innerHeight: Float) {
    val gap = if (shown.size > 4) 4 else GAP
    val rowHeight = (innerHeight - gap * (shown.size - 1)) / shown.size.coerceAtLeast(1)
    val stacked = WidgetLayout.rowStacked(rowHeight)
    val big = shown.size == 1
    Column(root) {
        shown.forEachIndexed { i, d ->
            Box(GlanceModifier.fillMaxWidth().defaultWeight().padding(bottom = if (i < shown.lastIndex) gap.dp else 0.dp)) {
                DeviceRowFace(d, deviceClick(context, widgetId, d, confirm), stacked = stacked, big = big)
            }
        }
    }
}

/** A device's tap: run the action (what a tap did when the widget was drawn). */
class TapCallback : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val id = parameters[DeviceKey] ?: return
        val sync = WidgetSync.from(context)
        val action = WidgetTiles.tapAction(sync.cached(id))?.takeIf { it.command == parameters[CommandKey] }
        if (action == null) sync.refresh(listOf(id)) // changed since it was drawn: show it as it is now
        else sync.run(id, action)
    }
}

/** An empty widget's tap: fresh states for every widget. */
class RefreshCallback : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        WidgetSync.from(context).refresh()
    }
}

/** The receivers: a refresh when a widget is placed or updated, the 15-minute refresh while any is out. */
abstract class NewmanWidgetReceiver : GlanceAppWidgetReceiver() {
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

class DeviceGridReceiver : NewmanWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = DeviceGridWidget()
}

class DeviceRowsReceiver : NewmanWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = DeviceRowsWidget()
}

/** A distinct address per widget and device, so each tile's PendingIntent stays its own. */
internal fun widgetUri(what: String, widgetId: Int, deviceId: String = ""): Uri =
    Uri.parse("newman-widget://$what/$widgetId/${Uri.encode(deviceId)}")
