package ca.nimaste.newman.widgets

import android.content.Context
import android.content.Intent
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.ColorFilter
import androidx.glance.GlanceModifier
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.action.Action
import androidx.glance.action.actionParametersOf
import androidx.glance.action.clickable
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.cornerRadius
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.layout.width
import androidx.glance.semantics.contentDescription
import androidx.glance.semantics.semantics
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider
import ca.nimaste.newman.MainActivity
import ca.nimaste.newman.core.model.Spoken
import ca.nimaste.newman.core.widgets.WidgetDevice
import ca.nimaste.newman.core.widgets.WidgetTiles
import ca.nimaste.newman.core.widgets.WidgetTone
import ca.nimaste.newman.push.Notifications
import ca.nimaste.newman.ui.home.OPEN_DEVICE

/**
 * What the home-screen widgets share: Newman's colours (the iPhone widgets' too), a device's tap, and
 * the device tile and row faces. A state's colour is its tone (WidgetTiles.tone): secure green, on or
 * not secure amber, a leak red, else grey.
 */
internal object WidgetColors {
    val background = Color(0xFF0B1026)
    val card = Color(0x12FFFFFF)
    val text = Color(0xFFEEF2FF)
    val muted = Color(0xFFA9B4D6)
    val faint = Color(0xFF6F7CA3)
    val good = Color(0xFF3CCFB4)
    val active = Color(0xFFF5B942)
    val alert = Color(0xFFFF5C5C)
    val secureTop = Color(0xFF0F6B55)
    val insecureTop = Color(0xFF9B2C2C)
    val fixInk = Color(0xFF5C1616)

    fun ink(tone: WidgetTone): Color = when (tone) {
        WidgetTone.GOOD -> good
        WidgetTone.ACTIVE -> active
        WidgetTone.ALERT -> alert
        WidgetTone.OFFLINE -> faint
        WidgetTone.NEUTRAL -> muted
    }

    fun fill(tone: WidgetTone): Color = when (tone) {
        WidgetTone.ACTIVE -> Color(0x29F5B942)
        WidgetTone.ALERT -> Color(0x33FF5C5C)
        else -> card
    }
}

internal fun provider(c: Color): ColorProvider = ColorProvider(c)

/** The app, opened on a device's sheet (a sensor's tap: it has nothing to run). */
internal fun openDeviceIntent(context: Context, widgetId: Int, deviceId: String): Intent =
    Intent(context, MainActivity::class.java)
        .setData(widgetUri("open", widgetId, deviceId))
        .putExtra(Notifications.EXTRA_OPEN, OPEN_DEVICE + deviceId)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)

/** A device's tap: its action now (asking first when it's sensitive and the widget says so), else the app on it. */
internal fun deviceClick(context: Context, widgetId: Int, d: WidgetDevice, confirm: Boolean): Action {
    val action = WidgetTiles.tapAction(d)
    return when {
        action == null -> actionStartActivity(openDeviceIntent(context, widgetId, d.id))
        WidgetTiles.needsConfirm(action, confirm) -> actionStartActivity(ConfirmActivity.intent(context, widgetId, d, action))
        else -> actionRunCallback<TapCallback>(actionParametersOf(DeviceKey to d.id, CommandKey to action.command))
    }
}

/** What a screen reader says: "Side Door, locked. Tap to unlock". */
internal fun spoken(d: WidgetDevice): String =
    WidgetTiles.describe(d) + (WidgetTiles.tapAction(d)?.let { ". Tap to ${Spoken.phrase(it.label)}" } ?: "")

@Composable
internal fun DeviceName(d: WidgetDevice, big: Boolean, maxLines: Int, modifier: GlanceModifier = GlanceModifier) {
    Text(d.name, modifier = modifier, maxLines = maxLines,
        style = TextStyle(color = provider(WidgetColors.text), fontSize = if (big) 22.sp else 16.sp, fontWeight = FontWeight.Bold))
}

@Composable
internal fun DeviceState(d: WidgetDevice, big: Boolean) {
    Text(WidgetTiles.stateText(d), maxLines = 1,
        style = TextStyle(color = provider(WidgetColors.ink(WidgetTiles.tone(d))), fontSize = if (big) 19.sp else 15.sp,
            fontWeight = FontWeight.Medium))
}

/** A tile: icon, name and state stacked; a [compact] (short) one puts the icon beside them. */
@Composable
internal fun DeviceTileFace(d: WidgetDevice, click: Action, compact: Boolean, big: Boolean) {
    val tone = WidgetTiles.tone(d)
    val modifier = GlanceModifier.fillMaxSize().background(WidgetColors.fill(tone)).cornerRadius(16.dp).clickable(click)
        .semantics { contentDescription = spoken(d) }
    val iconSize = if (big) 30.dp else if (compact) 20.dp else 22.dp
    val icon = @Composable {
        Image(ImageProvider(WidgetIcons.bitmap(d.icon, d.kind)), contentDescription = null,
            modifier = GlanceModifier.size(iconSize), colorFilter = ColorFilter.tint(provider(WidgetColors.ink(tone))))
    }
    if (compact) {
        Row(modifier.padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            icon()
            Spacer(GlanceModifier.width(10.dp))
            Column {
                DeviceName(d, big, maxLines = 1)
                DeviceState(d, big)
            }
        }
    } else {
        Column(modifier.padding(10.dp)) {
            icon()
            Spacer(GlanceModifier.defaultWeight())
            DeviceName(d, big, maxLines = if (big) 2 else 3)
            DeviceState(d, big)
        }
    }
}

/** A row: the name, the state under it when the row is tall enough ([stacked]), else on the right. */
@Composable
internal fun DeviceRowFace(d: WidgetDevice, click: Action, stacked: Boolean, big: Boolean) {
    val modifier = GlanceModifier.fillMaxSize().background(WidgetColors.fill(WidgetTiles.tone(d))).cornerRadius(12.dp)
        .clickable(click).semantics { contentDescription = spoken(d) }.padding(horizontal = 12.dp)
    if (stacked) {
        Column(modifier, verticalAlignment = Alignment.CenterVertically) {
            DeviceName(d, big, maxLines = 2)
            DeviceState(d, big)
        }
    } else {
        Row(modifier, verticalAlignment = Alignment.CenterVertically) {
            DeviceName(d, big, maxLines = 1, modifier = GlanceModifier.defaultWeight())
            Spacer(GlanceModifier.width(8.dp))
            DeviceState(d, big)
        }
    }
}

/** A message filling the widget ("Sign in to Newman", "Choose devices"); tapping it does [onClick]. */
@Composable
internal fun WidgetMessage(text: String, onClick: Action) {
    Box(GlanceModifier.fillMaxSize().background(WidgetColors.background).cornerRadius(18.dp).padding(12.dp).clickable(onClick),
        contentAlignment = Alignment.Center) {
        Text(text, style = TextStyle(color = provider(WidgetColors.text), fontSize = 15.sp, fontWeight = FontWeight.Medium))
    }
}
