package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.Entity
import ca.nimaste.newman.core.model.MuteRequest
import ca.nimaste.newman.core.model.Muted
import ca.nimaste.newman.core.model.Muting
import ca.nimaste.newman.core.model.NewmanJson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.ZoneId
import java.util.Locale

/** A device's problems (health.items) and muting them. */
class MutingTest {
    private fun entity(json: String) = NewmanJson.decodeFromString(Entity.serializer(), json)

    @Test fun itemsAreReadAndSplitIntoShowingAndMuted() {
        val e = entity("""{"id": "ring.keypad", "health": {"ok": false, "issues": ["Battery low"], "items": [
            {"id": "ac", "text": "No AC power", "muted": {"mode": "always", "until": null, "by": "Chris"}},
            {"id": "battery_low", "text": "Battery low", "muted": null, "later": 1}]}}""")
        assertEquals(listOf("battery_low"), Muting.active(e.health).map { it.id })
        assertEquals(listOf("ac"), Muting.muted(e.health).map { it.id })
        assertEquals("Chris", Muting.muted(e.health).single().muted!!.by)
    }

    @Test fun anOlderHubsPlainIssuesStillShowWithoutIds() {
        val e = entity("""{"id": "x", "health": {"ok": false, "issues": ["Offline", "Battery 5%"]}}""")
        assertEquals(listOf("Offline", "Battery 5%"), Muting.active(e.health).map { it.text })
        assertEquals(listOf("", ""), Muting.active(e.health).map { it.id })
        assertEquals(emptyList<Any>(), Muting.muted(e.health))
        assertNull(entity("""{"id": "y"}""").health.items.firstOrNull())
    }

    @Test fun howAMuteReads() {
        val z = ZoneId.of("America/Toronto")
        assertEquals("Muted always", Muting.label(Muted("always"), z, Locale.US))
        assertEquals("Muted until it's fixed", Muting.label(Muted("fixed"), z, Locale.US))
        // 2026-10-25 13:15 UTC = 9:15 AM in Toronto (EDT); newer JDKs put a narrow space before AM
        assertEquals("Muted until Sunday, Oct 25, 9:15 AM",
            Muting.label(Muted("week", until = 1792934100.0), z, Locale.US).replace('\u202f', ' '))
    }

    @Test fun theRequestSaysFor() {
        assertEquals("""{"issue":"ac","for":"always"}""", NewmanJson.encodeToString(MuteRequest.serializer(), MuteRequest("ac", "always")))
        assertEquals(listOf("day", "week", "fixed", "always"), Muting.CHOICES.map { it.first })
    }
}
