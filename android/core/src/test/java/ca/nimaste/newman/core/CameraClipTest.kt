package ca.nimaste.newman.core

import ca.nimaste.newman.core.model.CameraAlert
import ca.nimaste.newman.core.model.CameraClip
import ca.nimaste.newman.core.push.PushPayload
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class CameraClipTest {
    @Test
    fun clipPathLikeTheWeb() {
        assertEquals("api/clips/frigate/r%3A1791546301.32669-9penfv", CameraClip.clipPath("frigate:r:1791546301.32669-9penfv"))
        assertEquals("api/clips/frigate/1790000000.1-abc", CameraClip.clipPath("frigate:1790000000.1-abc"))
        assertEquals("api/clips/my%20cams/a%2Fb%3Fc%23d", CameraClip.clipPath("my cams:a/b?c#d"))
        assertEquals("api/clips/x/caf%C3%A9!~*'()", CameraClip.clipPath("x:café!~*'()"))
        assertNull(CameraClip.clipPath(null))
        assertNull(CameraClip.clipPath(" "))
        assertNull(CameraClip.clipPath("frigate"))
        assertNull(CameraClip.clipPath(":r:1"))
        assertNull(CameraClip.clipPath("frigate:"))
    }

    @Test
    fun contentRangeLikeTheHubSendsIt() {
        assertEquals(CameraClip.ContentRange(0, 1, 15477162), CameraClip.parseContentRange("bytes 0-1/15477162"))
        assertEquals(CameraClip.ContentRange(1000, 15477161, 15477162), CameraClip.parseContentRange(" Bytes 1000-15477161/15477162 "))
        assertEquals(CameraClip.ContentRange(0, 1, null), CameraClip.parseContentRange("bytes 0-1/*"))
        for (bad in listOf(null, "", "bytes */100", "bytes 5-1/100", "bytes 0-100/100", "items 0-1/2", "bytes 0-1", "bytes a-b/3", "bytes -1-2/3")) {
            assertNull(bad, CameraClip.parseContentRange(bad))
        }
        assertEquals("bytes=0-1", CameraClip.PROBE_RANGE)
    }

    @Test
    fun matchAlertByClipThenReviewId() {
        val a = CameraAlert(id = "1.1-a", clip = "frigate:r:1.1-a", entityId = "frigate.camera_garage")
        val b = CameraAlert(id = "2.2-b", clip = "frigate:r:2.2-b")
        val c = CameraAlert(id = "3.3-c")
        val list = listOf(a, b, c)
        assertEquals(b, CameraClip.matchAlert(list, "frigate:r:2.2-b", "1.1-a"))
        assertEquals(c, CameraClip.matchAlert(list, "frigate:r:9.9-z", "3.3-c"))
        assertEquals(a, CameraClip.matchAlert(list, null, "1.1-a"))
        assertNull(CameraClip.matchAlert(list, "frigate:r:9.9-z", "9.9-z"))
        assertNull(CameraClip.matchAlert(list, "", ""))
        assertNull(CameraClip.matchAlert(emptyList(), "frigate:r:1.1-a", "1.1-a"))
    }

    @Test
    fun pushCarriesTheAlertsClip() {
        val p = PushPayload.parse(mapOf("title" to "Person at Driveway", "clip" to "frigate:r:1.1-a",
            "frigate_event_id" to "1.1-a", "camera" to "garage",
            "clip_url" to "https://home.garito.ca/api/public/clips/frigate/r:1.1-a?device=d&exp=1&token=t"))
        assertEquals("frigate:r:1.1-a", p.clip)
        assertEquals("1.1-a", p.reviewId)
        val q = PushPayload.parse(mapOf("title" to "x", "clip" to "", "frigate_event_id" to ""))
        assertNull(q.clip)
        assertNull(q.reviewId)
    }
}
