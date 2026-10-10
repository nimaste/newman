package ca.nimaste.newman.wall

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import ca.nimaste.newman.core.wall.BatteryCheckIn
import java.util.concurrent.TimeUnit

/**
 * The battery report with the screen off. The wall's own reporter (BatteryReporter) runs only while the
 * screen is on; with it off the tablet sleeps. The check-in that gets through Doze is BatteryAlarm's (an
 * alarm allowed while idle, every 15 minutes); WorkManager is the second path: every 15 minutes (with a
 * network - but in Doze its job waits for a maintenance window, hours apart overnight), and once straight
 * away when the screen goes off or, screen off, the charger is plugged in or out (a receiver registered
 * while the app's process lives). No foreground service, no notification. The report says "screen": "off",
 * and "background": true with "nextReportIn" while the alarm is armed, so the hub shows "Screen off" rather
 * than offline and knows when to expect the next one.
 */
class BatteryWorker(context: Context, params: WorkerParameters) : Worker(context, params) {
    override fun doWork(): Result {
        val event = inputData.getBoolean(EVENT, false)
        val outcome = BatteryPost.background(applicationContext, event)
        // A one-off (the screen just went off) is worth a retry; the periodic one simply comes again.
        return if (outcome == BatteryPost.Outcome.FAILED && event && runAttemptCount < 2) Result.retry() else Result.success()
    }

    companion object {
        const val EVENT = "event"
    }
}

object BatteryWork {
    private const val PERIODIC = "battery-checkin"
    private const val NOW = "battery-now"

    private val online = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    /**
     * Every 15 minutes, for as long as the app is installed. UPDATE, not KEEP, on every app start: it keeps
     * the work's timing but hands it to JobScheduler afresh, so a job lost underneath WorkManager (seen on
     * the Fire tablet: WorkManager's database said scheduled, JobScheduler had nothing) comes back.
     */
    fun schedule(context: Context) {
        val req = PeriodicWorkRequestBuilder<BatteryWorker>(BatteryCheckIn.PERIOD_MINUTES, TimeUnit.MINUTES)
            .setConstraints(online).build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.UPDATE, req)
    }

    /** One report now (expedited on Android 12+; earlier ones would need a notification for that). */
    fun now(context: Context) {
        val b = OneTimeWorkRequestBuilder<BatteryWorker>().setConstraints(online).setInputData(workDataOf(BatteryWorker.EVENT to true))
        if (Build.VERSION.SDK_INT >= 31) b.setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
        WorkManager.getInstance(context).enqueueUniqueWork(NOW, ExistingWorkPolicy.REPLACE, b.build())
    }

    /** While the process lives: the screen going off, or the charger changing with the screen off. */
    fun watch(context: Context) {
        val f = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_OFF)
            addAction(Intent.ACTION_POWER_CONNECTED)
            addAction(Intent.ACTION_POWER_DISCONNECTED)
        }
        ContextCompat.registerReceiver(context, object : BroadcastReceiver() {
            override fun onReceive(c: Context, intent: Intent) {
                // The check-in alarm counts from here (set again, so its state in the report is fresh).
                if (intent.action == Intent.ACTION_SCREEN_OFF) BatteryAlarm.arm(c)
                // Screen on, the wall's own reporter sends the charger change itself.
                if (intent.action == Intent.ACTION_SCREEN_OFF || !BatteryPost.screenOn(c)) now(c)
            }
        }, f, ContextCompat.RECEIVER_NOT_EXPORTED)
    }
}