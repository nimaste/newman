package ca.nimaste.newman.core

import ca.nimaste.newman.core.api.NewmanApi
import ca.nimaste.newman.core.model.Controls
import ca.nimaste.newman.core.net.HubInterceptor
import ca.nimaste.newman.core.settings.ConnectionStore
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test

/**
 * Read-only checks against a real hub, run only with -Pnewman.liveHub=http://10.1.1.2:8093.
 * Uses the endpoints that are open without signing in (GET /api/meta, GET /api/auth/state); it
 * never sends a command or changes anything.
 */
class LiveHubTest {
    private val hub: String? = System.getProperty("newman.liveHub")

    private fun api(): NewmanApi {
        val snap = ConnectionStore.Snapshot(hub, null, null, null, null)
        return NewmanApi.create(OkHttpClient.Builder().addInterceptor(HubInterceptor({ snap }, "Newman-Android-Test")).build())
    }

    @Test
    fun realMetaParsesAndEveryCommandHasAControl() = runBlocking {
        assumeTrue("set -Pnewman.liveHub to run", hub != null)
        val meta = api().meta()
        println("Live hub ${meta.version}: ${meta.kinds.size} kinds, ${meta.categories.size} categories")
        assertTrue(meta.kinds.isNotEmpty())
        meta.kinds.flatMap { it.commands }.forEach { Controls.controlFor(it) }
        assertTrue(meta.kinds.flatMap { it.commands }.any { it.sensitive })
    }

    /**
     * With -Pnewman.localTokenFile=<path to the hub's .local_token> on the hub's own machine
     * (127.0.0.1): read devices, dashboards, activity, alerts and push categories - GETs only.
     * (Not /api/dashboards/current: for the maintenance user that would create a dashboard.)
     */
    @Test
    fun realDevicesDashboardsAndActivityParse() = runBlocking {
        val file = System.getProperty("newman.localTokenFile")
        assumeTrue("set -Pnewman.liveHub and -Pnewman.localTokenFile to run", hub != null && file != null && java.io.File(file).canRead())
        val token = java.io.File(file!!).readText().trim()
        val snap = ConnectionStore.Snapshot(hub, null, null, null, null)
        val client = OkHttpClient.Builder()
            .addInterceptor { chain -> chain.proceed(chain.request().newBuilder().header("X-Newman-Local-Token", token).build()) }
            .addInterceptor(HubInterceptor({ snap }, "Newman-Android-Test"))
            .build()
        val api = NewmanApi.create(client)
        val meta = api.meta()
        val entities = api.entities()
        entities.forEach { e -> assertTrue("${e.kind} is in meta", meta.kind(e.kind) != null) }
        // Every device's state reads as plain words, and every command finds a control.
        entities.forEach { e ->
            ca.nimaste.newman.core.model.HubModel.entityStateText(e, meta.kind(e.kind))
            ca.nimaste.newman.core.model.Controls.rowsFor(e, meta.kind(e.kind))
        }
        val raw = client.newCall(okhttp3.Request.Builder().url("http://newman.invalid/api/dashboards?all=1").build()).execute().use { it.body!!.string() }
        val dashboards = ca.nimaste.newman.core.model.NewmanJson.decodeFromString(
            kotlinx.serialization.builtins.ListSerializer(ca.nimaste.newman.core.model.Dashboard.serializer()), raw)
        dashboards.flatMap { it.pages }.forEach { page ->
            val cells = ca.nimaste.newman.core.model.TileFlow.cells(page)
            ca.nimaste.newman.core.model.TileFlow.rows(cells).forEach { row -> assertTrue(row.sumOf { it.span } <= 4) }
        }
        val events = api.events(limit = 20)
        val alerts = api.alerts(limit = 5)
        val cats = api.pushCategories()
        println("Live hub (signed in locally): ${entities.size} devices, ${dashboards.size} dashboards " +
            "(${dashboards.sumOf { it.pages.size }} pages), ${events.size} events, ${alerts.size} camera alerts, ${cats.size} push categories")
        assertTrue(entities.isNotEmpty())
    }

    @Test
    fun realAuthStateParses() = runBlocking {
        assumeTrue("set -Pnewman.liveHub to run", hub != null)
        val s = api().authState()
        println("Live hub auth state: where=${s.where} certificate=${s.certificate} signedIn=${s.signedIn}")
        assertFalse(s.signedIn)
        assertEquals(true, s.where == "home" || s.where == "away")
    }
}
