package ca.nimaste.newman.di

import android.content.Context
import ca.nimaste.newman.BuildConfig
import ca.nimaste.newman.core.api.NewmanApi
import ca.nimaste.newman.core.hub.HubRepository
import ca.nimaste.newman.core.live.LiveSocket
import ca.nimaste.newman.core.net.HubHttp
import ca.nimaste.newman.core.net.HubSelector
import ca.nimaste.newman.core.settings.ConnectionStore
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object AppModule {

    @Provides
    @Singleton
    fun connectionStore(@ApplicationContext context: Context): ConnectionStore = ConnectionStore.get(context)

    /** The one mTLS-enabled client: API, live socket, images, notification pictures and buttons. */
    @Provides
    @Singleton
    fun httpClient(store: ConnectionStore): OkHttpClient =
        HubHttp.client({ store.snapshot }, "Newman-Android/${BuildConfig.VERSION_NAME}")

    @Provides
    @Singleton
    fun api(client: OkHttpClient): NewmanApi = NewmanApi.create(client)

    @Provides
    @Singleton
    fun appScope(): CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    @Provides
    @Singleton
    fun liveSocket(client: OkHttpClient, store: ConnectionStore, scope: CoroutineScope): LiveSocket =
        LiveSocket(client.newBuilder().readTimeout(0, TimeUnit.MILLISECONDS).build(), scope, { HubHttp.liveRequest(store.snapshot) })

    /** Home-network or outside address of the hub, re-checked on network changes (NewmanApp starts it). */
    @Provides
    @Singleton
    fun hubSelector(@ApplicationContext context: Context, store: ConnectionStore, client: OkHttpClient, scope: CoroutineScope): HubSelector =
        HubSelector(context, store, client, scope, learnHome = true)

    @Provides
    @Singleton
    fun hub(api: NewmanApi, live: LiveSocket, scope: CoroutineScope): HubRepository = HubRepository(api, live, scope)
}
