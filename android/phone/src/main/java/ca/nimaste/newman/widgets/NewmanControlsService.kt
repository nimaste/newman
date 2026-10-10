package ca.nimaste.newman.widgets

import android.app.KeyguardManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import android.net.Uri
import android.service.controls.Control
import android.service.controls.ControlsProviderService
import android.service.controls.DeviceTypes
import android.service.controls.actions.BooleanAction
import android.service.controls.actions.ControlAction
import android.service.controls.templates.ControlButton
import android.service.controls.templates.ControlTemplate
import android.service.controls.templates.StatelessTemplate
import android.service.controls.templates.ToggleTemplate
import android.util.Log
import androidx.annotation.RequiresApi
import ca.nimaste.newman.MainActivity
import ca.nimaste.newman.core.api.ApiErrors
import ca.nimaste.newman.core.api.hubCall
import ca.nimaste.newman.core.settings.ConnectionStore
import ca.nimaste.newman.core.widgets.DeviceControlPlan
import ca.nimaste.newman.core.widgets.WidgetChoice
import ca.nimaste.newman.core.widgets.WidgetDevice
import ca.nimaste.newman.push.Notifications
import ca.nimaste.newman.ui.home.OPEN_DEVICE
import dagger.hilt.android.EntryPointAccessors
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.concurrent.Flow
import java.util.function.Consumer

/**
 * Android's Device Controls (Android 11+: the power menu / the Quick Settings "Device controls" panel),
 * the counterpart of the iPhone's Control Center controls. The devices a widget can show
 * (GET /api/widgets/devices) are offered; each chosen one shows its state (GET /api/widgets/state,
 * every few seconds while the panel is open, and from the app's live connection when it runs) and runs
 * what a tap does now (POST /api/entities/{id}/command, via WidgetSync like the widgets):
 * - on / off devices are switches, one-action devices (a button, a scene) are buttons, sensors show
 *   their reading;
 * - an action the hub marks sensitive (unlock, open, disarm) asks for the phone to be unlocked first;
 *   everything else works from the lock screen.
 * Long-press a control to open its device in the app.
 */
@RequiresApi(30)
class NewmanControlsService : ControlsProviderService() {
    private val sync get() = WidgetSync.from(this)

    override fun createPublisherForAllAvailable(): Flow.Publisher<Control> = LoopPublisher(sync.scope) { emit ->
        if (!sync.signedIn) return@LoopPublisher
        val api = EntryPointAccessors.fromApplication(applicationContext, WidgetApiAccess::class.java).api()
        val devices = try {
            hubCall { api.widgetDevices() }.devices
        } catch (e: Exception) {
            if (e is kotlinx.coroutines.CancellationException) throw e
            Log.w(TAG, "Device controls list failed: ${ApiErrors.message(e)}")
            return@LoopPublisher
        }
        val structure = ConnectionStore.get(this).hub.name
        devices.forEach { emit(available(it, structure)) }
    }

    override fun createPublisherFor(controlIds: List<String>): Flow.Publisher<Control> = LoopPublisher(sync.scope, endless = true) { emit ->
        val s = sync
        val unwatch = s.watch(controlIds)
        val structure = ConnectionStore.get(this).hub.name
        val poll: Job = s.scope.launch {
            while (true) {
                s.refresh(controlIds)
                delay(DeviceControlPlan.POLL_MS)
            }
        }
        try {
            // Every change to the cache (a poll, a tap, the live connection) redraws the controls.
            s.version.collect { controlIds.forEach { id -> emit(stateful(id, s.cached(id), s.signedIn, structure)) } }
        } finally {
            poll.cancel()
            unwatch()
        }
    }

    override fun performControlAction(controlId: String, action: ControlAction, consumer: Consumer<Int>) {
        val s = sync
        val d = s.cached(controlId)
        val run = DeviceControlPlan.actionFor(d, (action as? BooleanAction)?.newState)
        if (d == null || run == null) {
            consumer.accept(ControlAction.RESPONSE_OK)
            s.scope.launch { s.refresh(listOf(controlId)) } // changed since it was drawn: show it as it is
            return
        }
        // The panel asks for the unlock itself (setAuthRequired); this is the backstop.
        if (run.sensitive && getSystemService(KeyguardManager::class.java)?.isKeyguardLocked == true) {
            consumer.accept(ControlAction.RESPONSE_FAIL)
            return
        }
        consumer.accept(ControlAction.RESPONSE_OK)
        s.scope.launch { s.run(controlId, run) }
    }

    /** In the "add controls" list: name, room, the app's icon for it. */
    private fun available(c: WidgetChoice, structure: String?): Control =
        Control.StatelessBuilder(c.id, openDevice(this, c.id))
            .setTitle(c.name)
            .setSubtitle(c.kindName.ifEmpty { c.area ?: "" })
            .setZone(DeviceControlPlan.zone(c.area))
            .apply { if (!structure.isNullOrBlank()) setStructure(structure) }
            .setDeviceType(DeviceTypes.TYPE_UNKNOWN)
            .setCustomIcon(Icon.createWithBitmap(WidgetIcons.bitmap(c.icon, c.kind)))
            .build()

    private fun stateful(id: String, d: WidgetDevice?, signedIn: Boolean, structure: String?): Control {
        val b = Control.StatefulBuilder(id, openDevice(this, id))
        if (d == null) {
            return b.setTitle(id)
                .setStatus(if (signedIn) Control.STATUS_NOT_FOUND else Control.STATUS_DISABLED)
                .setStatusText(if (signedIn) "Not found" else "Signed out")
                .setDeviceType(DeviceTypes.TYPE_UNKNOWN)
                .build()
        }
        val style = DeviceControlPlan.style(d)
        val tap = ca.nimaste.newman.core.widgets.WidgetTiles.tapAction(d)
        val template: ControlTemplate = when (style) {
            DeviceControlPlan.Style.TOGGLE -> ToggleTemplate("toggle", ControlButton(d.active, tap?.label ?: d.name))
            DeviceControlPlan.Style.PUSH -> StatelessTemplate("push")
            DeviceControlPlan.Style.STATUS -> ControlTemplate.getNoTemplateObject()
        }
        return b.setTitle(d.name)
            .setSubtitle(d.area ?: "")
            .setZone(DeviceControlPlan.zone(d.area))
            .apply { if (!structure.isNullOrBlank()) setStructure(structure) }
            .setDeviceType(if (style == DeviceControlPlan.Style.TOGGLE) DeviceTypes.TYPE_GENERIC_ON_OFF else DeviceTypes.TYPE_UNKNOWN)
            .setCustomIcon(Icon.createWithBitmap(WidgetIcons.bitmap(d.icon, d.kind)))
            .setStatus(Control.STATUS_OK)
            .setStatusText(DeviceControlPlan.statusText(d))
            .setControlTemplate(template)
            .setAuthRequired(DeviceControlPlan.authRequired(d))
            .build()
    }

    companion object {
        private const val TAG = "NewmanControls"

        /** Long-press a control: the app on that device's sheet. */
        fun openDevice(context: Context, id: String): PendingIntent =
            PendingIntent.getActivity(
                context, id.hashCode(),
                Intent(context, MainActivity::class.java)
                    .setData(Uri.parse("newman-control://device/${Uri.encode(id)}"))
                    .putExtra(Notifications.EXTRA_OPEN, OPEN_DEVICE + id)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
    }
}

/**
 * A java.util.concurrent.Flow.Publisher (what ControlsProviderService wants) fed by a coroutine: [body]
 * emits, honouring the subscriber's demand; it completes when [body] returns unless [endless], and stops
 * when the system cancels.
 */
@RequiresApi(30)
private class LoopPublisher(
    private val scope: CoroutineScope,
    private val endless: Boolean = false,
    private val body: suspend (emit: suspend (Control) -> Unit) -> Unit,
) : Flow.Publisher<Control> {
    override fun subscribe(subscriber: Flow.Subscriber<in Control>) {
        val demand = MutableStateFlow(0L)
        var job: Job? = null
        val cancelled = java.util.concurrent.atomic.AtomicBoolean(false)
        subscriber.onSubscribe(object : Flow.Subscription {
            override fun request(n: Long) {
                if (n <= 0) return
                demand.value = if (Long.MAX_VALUE - demand.value < n) Long.MAX_VALUE else demand.value + n
            }

            override fun cancel() {
                cancelled.set(true)
                job?.cancel()
            }
        })
        if (cancelled.get()) return
        job = scope.launch {
            try {
                body { c ->
                    demand.first { it > 0 }
                    if (cancelled.get()) throw kotlinx.coroutines.CancellationException("cancelled")
                    demand.update { if (it == Long.MAX_VALUE) it else it - 1 }
                    subscriber.onNext(c)
                }
                if (!endless) subscriber.onComplete()
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                subscriber.onError(e)
            }
        }
    }
}
