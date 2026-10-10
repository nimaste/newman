package ca.nimaste.newman.widgets

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.util.Log
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.graphics.PathParser
import androidx.core.graphics.drawable.IconCompat
import ca.nimaste.newman.core.api.ApiErrors
import ca.nimaste.newman.core.api.NewmanApi
import ca.nimaste.newman.core.api.hubCall
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.core.widgets.DeviceUse
import ca.nimaste.newman.core.widgets.UsageRank
import ca.nimaste.newman.core.widgets.WidgetTiles
import ca.nimaste.newman.ui.icons.NIcons
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import javax.inject.Inject
import javax.inject.Singleton

/**
 * The app icon's shortcuts (long-press it): the four devices run most from this phone, each doing what
 * a tap does at that moment (the hub's primary action, as on a widget tile) without opening the app;
 * and "Add to home screen" from a device's sheet (a pinned shortcut doing the same). Both go through
 * [ShortcutActivity]. Device ids, counts and names stay on this phone.
 */
@Singleton
class LauncherShortcuts @Inject constructor(
    @ApplicationContext private val context: Context,
    private val api: NewmanApi,
    private val store: ConnectionStore,
    private val scope: CoroutineScope,
) {
    private val prefs = context.getSharedPreferences("newman_usage", Context.MODE_PRIVATE)
    private var soon: Job? = null

    private val signedIn: Boolean get() = !store.token.isNullOrEmpty() && store.hasHub

    private fun uses(): Map<String, DeviceUse> =
        prefs.all.mapNotNull { (k, v) -> if (k.startsWith(KEY)) UsageRank.decode(v as? String)?.let { k.removePrefix(KEY) to it } else null }.toMap()

    /** A device was run (app, widget, tile, control or shortcut): count it, re-rank soon. */
    fun record(deviceId: String) {
        val next = UsageRank.record(uses(), deviceId, System.currentTimeMillis() / 1000)[deviceId] ?: return
        prefs.edit().putString(KEY + deviceId, UsageRank.encode(next)).apply()
        updateSoon()
    }

    fun updateSoon(delayMs: Long = 2_000) {
        soon?.cancel()
        soon = scope.launch {
            delay(delayMs)
            update()
        }
    }

    /** Publish the top devices as dynamic shortcuts (only when the list changed: the launcher rate-limits). */
    suspend fun update() {
        if (!signedIn) return clearDynamic()
        val max = minOf(TOP, runCatching { ShortcutManagerCompat.getMaxShortcutCountPerActivity(context) }.getOrDefault(TOP))
        val ids = UsageRank.top(uses(), max + 2) // a couple spare, for devices with nothing to tap
        if (ids.isEmpty()) return clearDynamic()
        val devices = try {
            hubCall { api.widgetState(ids.joinToString(",")) }.devices
        } catch (e: Exception) {
            if (e is kotlinx.coroutines.CancellationException) throw e
            Log.w(TAG, "Shortcut update skipped: ${ApiErrors.message(e)}")
            return
        }
        val byId = devices.associateBy { it.id }
        val chosen = ids.mapNotNull { byId[it] }.filter { WidgetTiles.tapAction(it.copy(available = true)) != null }.take(max)
        val signature = chosen.joinToString("|") { "${it.id}=${it.name}=${it.icon}" }
        if (signature == prefs.getString(KEY_PUBLISHED, null)) return
        val list = chosen.mapIndexed { i, d -> info(context, d.id, d.name, d.icon, d.kind, rank = i) }
        runCatching { ShortcutManagerCompat.setDynamicShortcuts(context, list) }
            .onSuccess { prefs.edit().putString(KEY_PUBLISHED, signature).apply() }
            .onFailure { Log.w(TAG, "Couldn't publish shortcuts: ${it.message}") }
    }

    /** Signed out: no device names under the app icon (the counts stay for next time). */
    fun signedOut() {
        soon?.cancel()
        clearDynamic()
    }

    /** Another hub: forget this one's counts too. */
    fun hubChanged() {
        signedOut()
        prefs.edit().clear().apply()
    }

    private fun clearDynamic() {
        runCatching { ShortcutManagerCompat.removeAllDynamicShortcuts(context) }
        prefs.edit().remove(KEY_PUBLISHED).apply()
    }

    companion object {
        private const val TAG = "NewmanShortcuts"
        private const val KEY = "use."
        private const val KEY_PUBLISHED = "published"
        private const val TOP = 4

        fun shortcutId(deviceId: String) = "device:$deviceId"

        fun canPin(context: Context): Boolean = ShortcutManagerCompat.isRequestPinShortcutSupported(context)

        /** "Add to home screen": the launcher asks the person where to put it. False when it can't. */
        fun requestPin(context: Context, deviceId: String, name: String, icon: String?, kind: String?): Boolean =
            canPin(context) && runCatching {
                ShortcutManagerCompat.requestPinShortcut(context, info(context, deviceId, name, icon, kind, rank = 0), null)
            }.getOrDefault(false)

        private fun info(context: Context, deviceId: String, name: String, icon: String?, kind: String?, rank: Int): ShortcutInfoCompat =
            ShortcutInfoCompat.Builder(context, shortcutId(deviceId))
                .setShortLabel(name.ifBlank { "Device" })
                .setLongLabel(name.ifBlank { "Device" })
                .setIcon(IconCompat.createWithAdaptiveBitmap(adaptive(icon, kind)))
                .setIntent(ShortcutActivity.intent(context, deviceId))
                .setRank(rank)
                .build()

        /** The device's icon in teal on Newman's night blue, inside an adaptive icon's safe zone. */
        private fun adaptive(icon: String?, kind: String?): Bitmap {
            val px = 216
            val bmp = Bitmap.createBitmap(px, px, Bitmap.Config.ARGB_8888)
            val c = Canvas(bmp)
            c.drawColor(Color.rgb(0x0B, 0x10, 0x26))
            val glyph = 84f // of the 144 the launcher always shows
            c.translate((px - glyph) / 2f, (px - glyph) / 2f)
            c.scale(glyph / 24f, glyph / 24f)
            val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                color = Color.rgb(0x3C, 0xCF, 0xB4)
                style = Paint.Style.STROKE
                strokeWidth = 2f
                strokeCap = Paint.Cap.ROUND
                strokeJoin = Paint.Join.ROUND
            }
            val name = WidgetIcons.nameFor(icon, kind)
            (NIcons.PATHS[name] ?: NIcons.PATHS.getValue("dot")).forEach { d ->
                runCatching { PathParser.createPathFromPathData(d) }.getOrNull()?.let { c.drawPath(it, paint) }
            }
            return bmp
        }
    }
}
