package ca.nimaste.newman.widgets

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import androidx.core.graphics.PathParser
import ca.nimaste.newman.core.model.HubModel
import ca.nimaste.newman.core.model.Kind
import ca.nimaste.newman.ui.icons.NIcons

/**
 * The app's stroke icons (ui/icons/Icons.kt) as small white bitmaps, for widgets and Quick Settings
 * tiles (neither can draw Compose). Widgets tint them; tiles are tinted by the system.
 */
object WidgetIcons {
    private const val PX = 72
    private val cache = HashMap<String, Bitmap>()

    /** The device's icon name, else its kind's (HubModel.kindIcon), resolved like the app's tiles. */
    fun nameFor(icon: String?, kind: String?): String {
        if (!icon.isNullOrEmpty() && NIcons.PATHS.containsKey(icon)) return icon
        val byKind = HubModel.kindIcon(Kind(id = kind ?: ""))
        if (byKind != "dot") return NIcons.resolve(byKind)
        return NIcons.resolve(icon?.ifEmpty { null } ?: kind)
    }

    @Synchronized
    fun bitmap(icon: String?, kind: String?): Bitmap {
        val name = nameFor(icon, kind)
        return cache.getOrPut(name) {
            val bmp = Bitmap.createBitmap(PX, PX, Bitmap.Config.ARGB_8888)
            val c = Canvas(bmp)
            val s = PX / 24f
            c.scale(s, s)
            val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                color = Color.WHITE
                style = Paint.Style.STROKE
                strokeWidth = 2f
                strokeCap = Paint.Cap.ROUND
                strokeJoin = Paint.Join.ROUND
            }
            (NIcons.PATHS[name] ?: NIcons.PATHS.getValue("dot")).forEach { d ->
                runCatching { PathParser.createPathFromPathData(d) }.getOrNull()?.let { c.drawPath(it, paint) }
            }
            bmp
        }
    }
}
