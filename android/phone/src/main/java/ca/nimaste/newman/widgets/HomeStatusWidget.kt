package ca.nimaste.newman.widgets

import android.content.Context
import android.content.Intent
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
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.LocalSize
import androidx.glance.action.ActionParameters
import androidx.glance.action.actionParametersOf
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.SizeMode
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.provideContent
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.ColumnScope
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxHeight
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.semantics.contentDescription
import androidx.glance.semantics.semantics
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import ca.nimaste.newman.MainActivity
import ca.nimaste.newman.core.widgets.WidgetAction
import ca.nimaste.newman.core.widgets.WidgetHome
import ca.nimaste.newman.core.widgets.WidgetHomeProblem

/**
 * "Home status": the house at a glance (GET /api/widgets/home - everything with a secure state, from the
 * kind metadata). Green "All secure" with a detail line ("2 locks · garage door closed"); otherwise red,
 * naming what isn't secure ("Side Door unlocked") with a button that fixes it (Lock, Close). Wide enough
 * (4x2), it adds the list (Locks: All locked / Garage door: Closed / Alarm: Disarmed) and up to two
 * buttons. Nothing to choose: it covers the whole house.
 */
class HomeStatusWidget : GlanceAppWidget() {
    override val sizeMode: SizeMode = SizeMode.Exact

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val sync = WidgetSync.from(context)
        provideContent {
            val version by sync.version.collectAsState()
            val home = remember(version) { sync.home() }
            val error = remember(version) { sync.homeError }
            HomeBody(context, sync.signedIn, home, error)
        }
    }
}

class HomeStatusReceiver : NewmanWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = HomeStatusWidget()
}

private val LabelKey = ActionParameters.Key<String>("label")
private val SensitiveKey = ActionParameters.Key<Boolean>("sensitive")

/** Wide enough for the list beside the headline (a 4x2 widget). */
private fun showsList(widthDp: Float, heightDp: Float): Boolean = widthDp >= 250f && heightDp >= 100f

private val white = provider(Color.White)

@Composable
private fun HomeBody(context: Context, signedIn: Boolean, home: WidgetHome?, error: String?) {
    val open = actionStartActivity(Intent(context, MainActivity::class.java))
    when {
        !signedIn -> WidgetMessage("Sign in to Newman", open)
        home == null -> WidgetMessage(error ?: "Loading…", actionRunCallback<RefreshCallback>())
        else -> {
            val size = LocalSize.current
            val bg = when (home.secure) {
                true -> WidgetColors.secureTop
                false -> WidgetColors.insecureTop
                null -> WidgetColors.background
            }
            val root = GlanceModifier.fillMaxSize().background(bg).cornerRadius(18.dp).padding(12.dp).clickable(open)
            if (showsList(size.width.value, size.height.value)) {
                Row(root) {
                    Column(GlanceModifier.defaultWeight().fillMaxHeight().padding(end = 10.dp)) {
                        Headline(home, big = true)
                        val fixes = home.fixable.take(2)
                        if (fixes.isNotEmpty()) {
                            Spacer(GlanceModifier.height(6.dp))
                            Row(GlanceModifier.fillMaxWidth()) {
                                fixes.forEachIndexed { i, p ->
                                    Box(GlanceModifier.defaultWeight().padding(end = if (i < fixes.lastIndex) 6.dp else 0.dp)) {
                                        FixButton(context, p)
                                    }
                                }
                            }
                        }
                    }
                    val fit = ((size.height.value - 24f) / 38f).toInt().coerceIn(1, 4)
                    Column(GlanceModifier.defaultWeight().fillMaxHeight()) {
                        home.groups.take(fit).forEach { g ->
                            Column(GlanceModifier.padding(bottom = 6.dp).semantics { contentDescription = "${g.label}: ${g.text}" }) {
                                Text(g.label, maxLines = 1,
                                    style = TextStyle(color = provider(Color(0xD9FFFFFF)), fontSize = 12.sp, fontWeight = FontWeight.Medium))
                                Text(g.text, maxLines = 1, style = TextStyle(color = white, fontSize = 15.sp, fontWeight = FontWeight.Bold))
                            }
                        }
                    }
                }
            } else {
                Column(root) {
                    Headline(home, big = false)
                    home.fixable.firstOrNull()?.let {
                        Spacer(GlanceModifier.height(6.dp))
                        FixButton(context, it)
                    }
                }
            }
        }
    }
}

@Composable
private fun ColumnScope.Headline(home: WidgetHome, big: Boolean) {
    val icon = when (home.secure) {
        true -> "shield"
        false -> "alert"
        null -> "home"
    }
    Column(GlanceModifier.fillMaxWidth().defaultWeight().semantics { contentDescription = "${home.headline}. ${home.detail}" }) {
        Image(ImageProvider(WidgetIcons.bitmap(icon, null)), contentDescription = null, modifier = GlanceModifier.size(22.dp),
            colorFilter = ColorFilter.tint(white))
        Spacer(GlanceModifier.defaultWeight())
        Text(home.headline, maxLines = 2,
            style = TextStyle(color = white, fontSize = if (big) 22.sp else 20.sp, fontWeight = FontWeight.Bold))
        if (home.detail.isNotEmpty()) {
            Text(home.detail, maxLines = 2, style = TextStyle(color = provider(Color(0xD9FFFFFF)), fontSize = 13.sp,
                fontWeight = FontWeight.Medium))
        }
    }
}

/** "Lock" / "Close": runs the problem's fix. */
@Composable
private fun FixButton(context: Context, p: WidgetHomeProblem) {
    val fix = p.fix ?: return
    val click = actionRunCallback<FixCallback>(actionParametersOf(DeviceKey to p.id, CommandKey to fix.command,
        LabelKey to fix.label, SensitiveKey to fix.sensitive))
    Box(GlanceModifier.fillMaxWidth().background(Color.White).cornerRadius(18.dp).padding(vertical = 7.dp, horizontal = 12.dp)
        .clickable(click).semantics { contentDescription = "${fix.label} ${p.name}" },
        contentAlignment = Alignment.Center) {
        Text(fix.label, maxLines = 1,
            style = TextStyle(color = provider(WidgetColors.fixInk), fontSize = 15.sp, fontWeight = FontWeight.Bold))
    }
}

/** A fix button's tap: the command that brings the device back to its secure state. */
class FixCallback : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val id = parameters[DeviceKey] ?: return
        val command = parameters[CommandKey] ?: return
        WidgetSync.from(context).run(id, WidgetAction(command, parameters[LabelKey] ?: command,
            sensitive = parameters[SensitiveKey] ?: false))
    }
}
