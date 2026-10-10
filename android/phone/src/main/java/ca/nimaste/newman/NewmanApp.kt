package ca.nimaste.newman

import android.app.Application
import ca.nimaste.newman.core.mtls.MTLSStore
import ca.nimaste.newman.core.net.HubSelector
import ca.nimaste.newman.data.Account
import ca.nimaste.newman.push.Notifications
import ca.nimaste.newman.widgets.WidgetSync
import coil3.ImageLoader
import coil3.PlatformContext
import coil3.SingletonImageLoader
import coil3.network.okhttp.OkHttpNetworkFetcherFactory
import coil3.request.crossfade
import dagger.hilt.android.HiltAndroidApp
import okhttp3.OkHttpClient
import javax.inject.Inject

@HiltAndroidApp
class NewmanApp : Application(), SingletonImageLoader.Factory {

    @Inject lateinit var http: OkHttpClient
    @Inject lateinit var selector: HubSelector
    @Inject lateinit var account: Account
    @Inject lateinit var widgets: WidgetSync
    @Inject lateinit var live: ca.nimaste.newman.push.LiveStatusNotifier
    @Inject lateinit var shortcuts: ca.nimaste.newman.widgets.LauncherShortcuts

    override fun onCreate() {
        MTLSStore.init(this)
        super.onCreate()
        Notifications.createChannels(this)
        ca.nimaste.newman.push.LiveStatusNotifier.createChannel(this)
        // The hub's home-network address at home, its outside one elsewhere (checked on network changes).
        selector.onChanged = { account.connectionChanged() }
        selector.start()
        widgets.start() // home-screen widgets / tiles follow the live connection while the app runs
        live.start() // live statuses (ongoing notifications) too
        if (account.signedIn) shortcuts.updateSoon(10_000) // the app icon's shortcuts: names may have changed
    }

    /** Coil loads every picture through the same mTLS client (and the hub's credentials). */
    override fun newImageLoader(context: PlatformContext): ImageLoader =
        ImageLoader.Builder(context)
            .components { add(OkHttpNetworkFetcherFactory(callFactory = { http })) }
            .crossfade(true)
            .build()
}
