package ca.nimaste.newman.widgets

import android.app.PendingIntent
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import ca.nimaste.newman.core.widgets.WidgetTiles
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/**
 * Three Quick Settings tiles ("Newman 1/2/3"). Long-press one to choose its device; the tile shows the
 * device's name, its state (on / off highlighted) and runs what a tap does now. A sensitive action
 * (unlock, open, disarm) asks for the phone to be unlocked first when it's locked.
 */
object QuickTiles {
    const val SLOTS = 3
    private val classes = listOf(NewmanTile1::class.java, NewmanTile2::class.java, NewmanTile3::class.java)

    fun slotOf(className: String?): Int? = classes.indexOfFirst { it.name == className }.takeIf { it >= 0 }?.plus(1)

    /** Ask the system to redraw every tile (it binds them while the shade is open). */
    fun requestUpdate(context: Context) {
        for (c in classes) runCatching { TileService.requestListeningState(context, ComponentName(context, c)) }
    }
}

abstract class NewmanTileBase(private val slot: Int) : TileService() {
    private var job: Job? = null
    private var listener: Job? = null

    private val sync get() = WidgetSync.from(this)

    override fun onStartListening() {
        super.onStartListening()
        render()
        listener?.cancel()
        listener = sync.scope.launch { sync.version.collect { launchMain { render() } } }
        val id = sync.tileDevice(slot) ?: return
        job?.cancel()
        job = sync.scope.launch { sync.refresh(listOf(id)) }
    }

    override fun onStopListening() {
        listener?.cancel()
        listener = null
        super.onStopListening()
    }

    override fun onClick() {
        super.onClick()
        val id = sync.tileDevice(slot) ?: return openSettings()
        val action = WidgetTiles.tapAction(sync.cached(id))
        if (action == null) {
            sync.scope.launch { sync.refresh(listOf(id)) }
            return
        }
        val go = { sync.scope.launch { sync.run(id, action) }; Unit }
        if (action.sensitive && isLocked) unlockAndRun { go() } else go()
    }

    private fun openSettings() {
        val intent = Intent(this, TileSettingsActivity::class.java)
            .putExtra(TileSettingsActivity.EXTRA_SLOT, slot)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (Build.VERSION.SDK_INT >= 34) {
            startActivityAndCollapse(PendingIntent.getActivity(this, slot, intent,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
        } else {
            @Suppress("DEPRECATION", "StartActivityAndCollapseDeprecated")
            startActivityAndCollapse(intent)
        }
    }

    private fun launchMain(block: () -> Unit) {
        android.os.Handler(mainLooper).post { runCatching(block) }
    }

    private fun render() {
        val tile = qsTile ?: return
        val id = sync.tileDevice(slot)
        val d = sync.cached(id)
        tile.label = d?.name ?: "Newman $slot"
        tile.state = when {
            id == null || d == null -> Tile.STATE_INACTIVE
            !d.available -> Tile.STATE_UNAVAILABLE
            d.active -> Tile.STATE_ACTIVE
            else -> Tile.STATE_INACTIVE
        }
        if (Build.VERSION.SDK_INT >= 29) {
            tile.subtitle = when {
                id == null -> "Not set up"
                d == null -> if (sync.signedIn) "…" else "Signed out"
                else -> WidgetTiles.stateText(d)
            }
        }
        // TalkBack: "Den plug, on" (the hub's own words for the state).
        tile.contentDescription = when {
            id == null -> "Newman $slot, not set up"
            d == null -> "Newman $slot"
            else -> WidgetTiles.describe(d)
        }
        if (Build.VERSION.SDK_INT >= 30) tile.stateDescription = d?.let { WidgetTiles.stateText(it) }
        tile.icon = if (d == null) Icon.createWithResource(this, ca.nimaste.newman.R.drawable.ic_notification)
        else Icon.createWithBitmap(WidgetIcons.bitmap(d.icon, d.kind))
        tile.updateTile()
    }
}

class NewmanTile1 : NewmanTileBase(1)
class NewmanTile2 : NewmanTileBase(2)
class NewmanTile3 : NewmanTileBase(3)
