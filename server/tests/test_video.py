"""Per-device video settings (video.py): stream choice, "Show in All Cameras" and "Video from" - for any
device from any connection. Frigate is used as one connection that offers streams; a made-up doorbell
connection (pictures, no live video) and a lock stand in for everything else."""
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import build_app  # noqa: E402
from newman.automations import describe as d  # noqa: E402
from newman.config import config  # noqa: E402
from newman.integrations.base import Integration, address_of  # noqa: E402
from newman.integrations.frigate import CLIP_SETTLE_S, FrigateIntegration  # noqa: E402
from newman.integrations.reolink import ReolinkIntegration  # noqa: E402
from newman.replace import replace_entities  # noqa: E402
from newman.store import Store  # noqa: E402
from test_auth import Browser, FakeLocks, H  # noqa: E402


def frigate_config(extra_live=True):
    # Where each camera really is: front_porch read through Frigate's restream (its go2rtc sources say),
    # front only in go2rtc (via a stream that names another), garage straight from its ffmpeg input.
    streams = {"front_porch": ["rtsp://admin:s3cret@10.1.1.53:554/cam?subtype=0"],
               "front_porch_sub": ["rtsp://admin:s3cret@10.1.1.53:554/cam?subtype=3"],
               "front": ["front_main"], "front_main": ["ffmpeg:http://10.1.1.56/flv?user=admin&password=pw#video=copy"],
               "front_sub": ["rtsp://admin:pw@10.1.1.56:554/sub"], "garage": []}
    if extra_live:
        streams["front_porch_live"] = []
    return {"go2rtc": {"streams": streams},
            "cameras": {"front_porch": {"live": {"streams": {"front_porch_live": "front_porch_live"}} if extra_live else {},
                                        "ffmpeg": {"inputs": [{"path": "rtsp://127.0.0.1:8554/front_porch_sub"}]},
                                        "zones": {"front_patio": {}}},
                        "front": {},
                        "garage": {"live": {"streams": {"Wide": "garage_wide"}},
                                   "ffmpeg": {"inputs": [{"path": "rtsp://user:pw@Cam-Garage.lan:554/x"}]}}}}


class Resp:
    def __init__(self, data=None, content=b""):
        self._data, self.content, self.status_code = data, content, 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self, cfg):
        self.cfg = cfg

    async def get(self, url, params=None):
        if url.endswith("/api/config"):
            return Resp(self.cfg)
        return Resp(content=b"JPEG " + url.split("/api/")[1].split("/")[0].encode())

    async def aclose(self):
        pass


class FakeBell(Integration):
    """A doorbell connection: presses and its own picture, no live video."""
    type = "fakebell"
    name = "Fake bell"

    def __init__(self, ctx):
        super().__init__("bell", "Bell", {}, ctx)

    def has_still(self, eid):
        return eid == "bell.front"

    async def still(self, eid, height):
        return b"BELL OWN"


def src_of(url):
    return url.split("src=")[1] if url else None


class VideoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        ctx = self.h.integrations._ctx
        self.h.integrations.instances["fake"] = FakeLocks(ctx)
        self.h.hub.upsert("fake.side", kind="lock", name="Side Door", integration="fake")
        self.frigate = FrigateIntegration("frigate", "Frigate", {"url": "http://frigate:5000"}, ctx)
        await self.frigate._http.aclose()
        self.frigate._http = FakeHttp(frigate_config())
        self.h.integrations.instances["frigate"] = self.frigate
        await self.frigate._load_config()
        self.bell = FakeBell(ctx)
        self.h.integrations.instances["bell"] = self.bell
        self.h.hub.upsert("bell.front", kind="doorbell", name="Front Door", integration="bell")
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        self.video = self.h.video
        await asyncio.sleep(0)

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def ent(self, eid):
        return next(e for e in await (await self.client.get("/api/entities")).json() if e["id"] == eid)

    async def put(self, eid, body, client=None, **kw):
        return await (client or self.client).put(f"/api/entities/{eid}/video", json=body, **kw)

    async def reload_frigate(self, cfg):
        self.frigate._http.cfg = cfg
        await self.frigate._load_config()

    # -- the store ------------------------------------------------------------------------------

    def test_store_keeps_settings_and_drops_empty_ones(self):
        s = Store(Path(self.tmp.name) / "other")
        self.assertEqual(s.entity_settings(), {})
        s.set_entity_settings("a.b", {"showInCameras": False})
        self.assertEqual(s.entity_settings(), {"a.b": {"showInCameras": False}})
        s.set_entity_settings("a.b", {})
        self.assertEqual(s.entity_settings(), {})

    # -- streams --------------------------------------------------------------------------------

    def test_frigate_lists_each_cameras_own_streams(self):
        ids = lambda eid: [s["id"] for s in self.frigate.live_streams(eid)]  # noqa: E731
        self.assertEqual(ids("frigate.camera_front_porch"), ["front_porch", "front_porch_live", "front_porch_sub"])
        self.assertEqual(ids("frigate.camera_front"), ["front", "front_main", "front_sub"])  # not front_porch's
        self.assertEqual(ids("frigate.camera_garage"), ["garage", "garage_wide"])  # live.streams adds one
        self.assertEqual(self.frigate.live_streams("frigate.nope"), [])
        self.assertEqual(Integration.live_streams(self.bell, "bell.front"), [])  # connections without: no choice

    async def test_automatic_is_exactly_the_old_choice(self):
        for eid, sub, main in (("frigate.camera_front_porch", "front_porch_sub", "front_porch"),
                               ("frigate.camera_front", "front_sub", "front"),
                               ("frigate.camera_garage", "garage", "garage")):
            self.assertEqual(src_of(self.frigate.live_url(eid, "sub")), sub)
            self.assertEqual(src_of(self.frigate.live_url(eid, "main")), main)
            self.assertEqual(self.video.live_url(eid, "sub"), self.frigate.live_url(eid, "sub"))
            self.assertEqual(self.video.live_url(eid, "main"), self.frigate.live_url(eid, "main"))
        e = await self.ent("frigate.camera_front_porch")
        self.assertEqual(e["live"], "api/entities/frigate.camera_front_porch/live")
        self.assertEqual(e["still"], "api/entities/frigate.camera_front_porch/still.jpg")
        self.assertEqual(e["video"]["tileStream"], None)
        self.assertEqual(e["video"]["showInCameras"], True)
        self.assertEqual(e["video"]["from"], None)
        self.assertEqual(e["video"]["streams"][1], {"id": "front_porch_live", "label": "front_porch_live"})
        bell = await self.ent("bell.front")
        self.assertEqual((bell["live"], bell["still"]), (None, "api/entities/bell.front/still.jpg"))
        self.assertEqual((bell["video"]["streams"], bell["video"]["ownLive"]), ([], False))
        self.assertEqual(self.h.store.entity_settings(), {})

    async def test_a_chosen_stream_is_played(self):
        r = await self.put("frigate.camera_front_porch", {"tileStream": "front_porch_live"})
        self.assertEqual(r.status, 200)
        e = await r.json()
        self.assertEqual((e["video"]["tileStream"], e["video"]["fullStream"]), ("front_porch_live", None))
        self.assertEqual(src_of(self.video.live_url("frigate.camera_front_porch", "sub")), "front_porch_live")
        self.assertEqual(src_of(self.video.live_url("frigate.camera_front_porch", "main")), "front_porch")
        await self.put("frigate.camera_front_porch", {"fullStream": "front_porch_sub"})
        self.assertEqual(src_of(self.video.live_url("frigate.camera_front_porch", "main")), "front_porch_sub")
        await self.put("frigate.camera_garage", {"fullStream": "garage_wide"})  # one only live.streams names
        self.assertEqual(src_of(self.video.live_url("frigate.camera_garage", "main")), "garage_wide")
        # Back to Automatic.
        await self.put("frigate.camera_front_porch", {"tileStream": None, "fullStream": ""})
        self.assertEqual(src_of(self.video.live_url("frigate.camera_front_porch", "sub")), "front_porch_sub")
        self.assertNotIn("frigate.camera_front_porch", self.h.store.entity_settings())

    async def test_unknown_streams_are_refused(self):
        r = await self.put("frigate.camera_front", {"tileStream": "front_porch_live"})
        self.assertEqual(r.status, 400)
        self.assertIn("no stream", (await r.json())["error"])
        self.assertEqual((await self.put("bell.front", {"fullStream": "x"})).status, 400)  # offers none
        self.assertEqual((await self.put("frigate.camera_front", {"showInCameras": "no"})).status, 400)
        self.assertEqual((await self.put("nope.nope", {"showInCameras": False})).status, 404)
        self.assertEqual(self.h.store.entity_settings(), {})

    async def test_a_vanished_stream_falls_back_to_automatic(self):
        await self.put("frigate.camera_front_porch", {"tileStream": "front_porch_live", "fullStream": "front_porch_live"})
        await self.reload_frigate(frigate_config(extra_live=False))
        self.assertEqual(src_of(self.video.live_url("frigate.camera_front_porch", "sub")), "front_porch_sub")
        self.assertEqual(src_of(self.video.live_url("frigate.camera_front_porch", "main")), "front_porch")
        e = await self.ent("frigate.camera_front_porch")
        self.assertEqual((e["video"]["tileStream"], e["video"]["fullStream"]), (None, None))
        # Kept: when it comes back, it's used again.
        await self.reload_frigate(frigate_config())
        self.assertEqual(src_of(self.video.live_url("frigate.camera_front_porch", "sub")), "front_porch_live")

    # -- All Cameras ----------------------------------------------------------------------------

    async def test_hidden_from_all_cameras_but_still_everywhere_else(self):
        r = await self.put("frigate.camera_front", {"showInCameras": False})
        self.assertEqual(r.status, 200)
        self.assertIs((await r.json())["video"]["showInCameras"], False)
        e = await self.ent("frigate.camera_front")  # still listed, with its picture and video
        self.assertEqual((e["video"]["showInCameras"], bool(e["live"]), bool(e["still"])), (False, True, True))
        self.assertEqual((await self.client.get("/api/entities/frigate.camera_front/still.jpg")).status, 200)
        self.assertTrue(self.video.live_url("frigate.camera_front", "main"))
        self.assertIsNotNone(self.h.hub.get("frigate.camera_front"))
        self.assertEqual(self.h.store.entity_settings(), {"frigate.camera_front": {"showInCameras": False}})
        await self.put("frigate.camera_front", {"showInCameras": True})
        self.assertEqual(self.h.store.entity_settings(), {})

    # -- Video from ------------------------------------------------------------------------------

    async def test_a_device_takes_video_and_pictures_from_another(self):
        r = await self.put("bell.front", {"videoFrom": "frigate.camera_front_porch"})
        self.assertEqual(r.status, 200)
        e = await r.json()
        self.assertEqual((e["video"]["from"], e["video"]["source"]), ("frigate.camera_front_porch",) * 2)
        self.assertEqual(e["live"], "api/entities/bell.front/live")
        self.assertEqual(src_of(self.video.live_url("bell.front", "sub")), "front_porch_sub")
        pic = await self.client.get("/api/entities/bell.front/still.jpg")
        self.assertEqual(await pic.read(), b"JPEG front_porch")
        # The camera's own stream choice applies to whoever shows it; players are told to reconnect.
        key = e["video"]["key"]
        await self.put("frigate.camera_front_porch", {"tileStream": "front_porch_live"})
        self.assertEqual(src_of(self.video.live_url("bell.front", "sub")), "front_porch_live")
        self.assertNotEqual((await self.ent("bell.front"))["video"]["key"], key)
        # Alerts' pictures: a fresh one from the camera, kept like a connection's snapshot.
        sid = await self.video.snapshot("bell.front")
        self.assertEqual((self.h.snapshot_dir / f"{sid}.jpg").read_bytes(), b"JPEG front_porch")
        self.assertIsNone(await self.video.snapshot("frigate.camera_front"))  # no link: the connection's own
        self.assertIs(self.h.engine.video, self.video)
        # Its own events stay its own; its alerts list is its connection's.
        self.assertEqual(e["integration"], "bell")
        # Back to its own.
        await self.put("bell.front", {"videoFrom": None})
        e = await self.ent("bell.front")
        self.assertEqual((e["live"], e["video"]["source"]), (None, None))
        self.assertEqual(await (await self.client.get("/api/entities/bell.front/still.jpg")).read(), b"BELL OWN")

    async def test_any_kind_can_take_video(self):
        auto = {"trigger": {"items": [{"type": "event", "entity": "fake.side"}]}}
        self.assertIsNone(d.snapshot_source(auto, self.h.hub))
        self.assertFalse(self.h.hub.has_picture(self.h.hub.get("fake.side")))
        await self.put("fake.side", {"videoFrom": "frigate.camera_garage"})
        self.assertTrue(self.h.hub.has_picture(self.h.hub.get("fake.side")))
        self.assertEqual(d.snapshot_source(auto, self.h.hub).id, "fake.side")
        e = await self.ent("fake.side")
        self.assertEqual((e["live"], e["still"]), ("api/entities/fake.side/live", "api/entities/fake.side/still.jpg"))

    async def test_self_loops_and_dead_ends_are_refused(self):
        bad = [("frigate.camera_front", "frigate.camera_front"),  # itself
               ("frigate.camera_front", "bell.front"),            # no live video
               ("frigate.camera_front", "nope.nope"),             # not there
               ("frigate.camera_front", "fake.side")]
        for eid, target in bad:
            r = await self.put(eid, {"videoFrom": target})
            self.assertEqual(r.status, 400, target)
        self.assertEqual((await self.put("frigate.camera_front", {"videoFrom": "frigate.camera_garage"})).status, 200)
        r = await self.put("frigate.camera_garage", {"videoFrom": "frigate.camera_front"})  # would loop
        self.assertEqual(r.status, 400)
        self.assertIn("already takes", (await r.json())["error"])
        self.assertEqual(self.video.source("frigate.camera_garage"), None)
        # Links aren't followed further: something showing front gets front's own video, never garage's.
        await self.put("bell.front", {"videoFrom": "frigate.camera_front"})
        self.assertEqual(src_of(self.video.live_url("frigate.camera_front", "main")), "garage")
        self.assertEqual(src_of(self.video.live_url("bell.front", "main")), "front")
        self.assertEqual(self.video.source("bell.front"), "frigate.camera_front")

    async def test_a_broken_link_falls_back_to_its_own_picture(self):
        await self.put("bell.front", {"videoFrom": "frigate.camera_front_porch"})
        cfg = frigate_config()
        del cfg["go2rtc"]["streams"]["front_porch"]  # the camera loses its live video
        await self.reload_frigate(cfg)
        e = await self.ent("bell.front")
        self.assertEqual((e["live"], e["video"]["source"], e["video"]["from"]), (None, None, "frigate.camera_front_porch"))
        self.assertEqual(await (await self.client.get("/api/entities/bell.front/still.jpg")).read(), b"BELL OWN")
        self.assertIsNone(await self.video.snapshot("bell.front"))
        self.assertFalse(self.h.hub.has_picture(self.h.hub.get("fake.side")))
        await self.reload_frigate(frigate_config())
        self.assertEqual(self.video.source("bell.front"), "frigate.camera_front_porch")
        self.h.hub.remove("frigate.camera_front_porch")  # gone altogether
        self.assertIsNone(self.video.source("bell.front"))
        self.assertEqual((await self.client.get("/api/entities/bell.front/live")).status, 404)

    async def test_swapping_devices_moves_settings_and_links(self):
        await self.put("bell.front", {"videoFrom": "frigate.camera_front_porch"})
        await self.put("frigate.camera_front_porch", {"showInCameras": False})
        self.h.hub.upsert("frigate.camera_porch2", kind="camera", name="Porch 2", integration="frigate")
        await replace_entities(self.h, [{"from": "frigate.camera_front_porch", "to": "frigate.camera_porch2"}])
        saved = self.h.store.entity_settings()
        self.assertEqual(saved["bell.front"], {"videoFrom": "frigate.camera_porch2"})
        self.assertEqual(saved["frigate.camera_porch2"], {"showInCameras": False})

    # -- who may change them, and screens hearing about it ----------------------------------------

    async def test_only_admins_change_video_settings(self):
        await self.client.post("/api/users", json={"username": "marlina", "name": "Marlina", "role": "family",
                                                   "password": "another pass"})
        await self.client.post("/api/users", json={"username": "hall", "name": "Hall", "role": "tablet",
                                                   "password": "wall tablet pass"})
        fam = Browser(self.client)
        await fam.post("/api/auth/login", json={"username": "marlina", "password": "another pass"})
        tab = Browser(self.client)
        r = await tab.post("/api/auth/login", json={"username": "hall", "password": "wall tablet pass", "kind": "tablet"})
        tab_h = {"Authorization": "Bearer " + (await r.json())["token"]}
        anon = Browser(self.client)
        try:
            self.assertEqual((await self.put("frigate.camera_front", {"showInCameras": False}, fam)).status, 403)
            self.assertEqual((await self.put("frigate.camera_front", {"showInCameras": False}, tab, headers=tab_h)).status, 403)
            self.assertEqual((await self.put("frigate.camera_front", {"showInCameras": False}, anon)).status, 401)
            ents = await (await fam.get("/api/entities")).json()  # everyone sees the settings
            self.assertIn("video", ents[0])
        finally:
            for c in (fam, tab, anon):
                await c.close()
        self.assertEqual(self.h.store.entity_settings(), {})

    async def test_a_clip_is_complete_once_its_last_recordings_are_written(self):
        now = time.time()

        class Reviews:
            end = None

            async def get(self, url, params=None):
                return Resp({"id": "r1", "camera": "garage", "start_time": now - 60, "end_time": Reviews.end})

        self.frigate._http = Reviews()
        self.assertFalse(await self.frigate.event_finished("r:r1"))  # still recording
        Reviews.end = now - 2
        self.assertFalse(await self.frigate.event_finished("r:r1"))  # just ended: not all written yet
        Reviews.end = now - CLIP_SETTLE_S - 1
        self.assertTrue(await self.frigate.event_finished("r:r1"))

    async def test_changes_are_pushed_to_screens(self):
        ws = await self.client.ws_connect("/api/ws")
        self.assertEqual((await ws.receive_json(timeout=2))["type"], "hello")
        await self.put("bell.front", {"videoFrom": "frigate.camera_front_porch"})
        await self.put("frigate.camera_front_porch", {"tileStream": "front_porch_live", "showInCameras": False})
        got = {}
        try:
            while True:
                m = await ws.receive_json(timeout=1)
                if m["type"] == "entity":
                    got.setdefault(m["entity"]["id"], []).append(m["entity"]["video"])
        except asyncio.TimeoutError:
            pass
        await ws.close()
        self.assertEqual(got["frigate.camera_front_porch"][-1]["tileStream"], "front_porch_live")
        self.assertIs(got["frigate.camera_front_porch"][-1]["showInCameras"], False)
        # The doorbell shows that camera: its screens learn the new stream too.
        self.assertEqual(got["bell.front"][0]["source"], "frigate.camera_front_porch")
        self.assertEqual(got["bell.front"][-1]["key"], "frigate.camera_front_porch|front_porch_live|")


    # -- what "Video from" offers -----------------------------------------------------------------

    def opts(self, eid):
        return self.video.choices(eid)

    def test_addresses_are_hosts_only(self):
        for text, want in (("10.1.1.56", "10.1.1.56"), ("10.1.1.56:80", "10.1.1.56"),
                           ("rtsp://admin:pw@10.1.1.50:554/x", "10.1.1.50"),
                           ("ffmpeg:http://10.1.1.54/flv?user=a&password=b#video=copy", "10.1.1.54"),
                           ("http://Cam.LAN", "cam.lan"), ("", None), (None, None)):
            self.assertEqual(address_of(text), want, text)

    def test_frigate_finds_where_each_camera_really_is(self):
        self.assertEqual(self.frigate.video_address("frigate.camera_front_porch"), "10.1.1.53")  # through the restream
        self.assertEqual(self.frigate.video_address("frigate.camera_front"), "10.1.1.56")  # a stream naming another
        self.assertEqual(self.frigate.video_address("frigate.camera_garage"), "cam-garage.lan")  # its own input
        self.assertIsNone(self.frigate.video_address("frigate.nope"))
        self.assertEqual(self.frigate.video_derived_from("frigate.zone_front_patio"), "frigate.camera_front_porch")
        self.assertIsNone(self.frigate.video_derived_from("frigate.camera_front_porch"))
        self.assertIsNone(Integration.video_address(self.bell, "bell.front"))  # connections without: unknown

    def reolink(self, devices):
        r = ReolinkIntegration("reolink", "Reolink", {"username": "admin", "password": "pw", "devices": devices},
                               self.h.integrations._ctx)
        self.h.integrations.instances["reolink"] = r
        return r

    def test_reolink_devices_are_at_their_configured_address(self):
        r = self.reolink([{"host": "10.1.1.56", "name": "Front Door"}, {"host": "10.1.1.60"}])
        self.assertEqual(r.video_address("reolink.doorbell"), "10.1.1.56")
        self.assertEqual(r.video_address("reolink.10_1_1_60_camera_0"), "10.1.1.60")
        self.assertIsNone(r.video_address("reolink.10_1_1_99_camera_0"))
        self.assertIsNone(r.video_address("other.doorbell"))

    async def test_a_doorbell_is_offered_the_camera_at_its_address(self):
        self.reolink([{"host": "10.1.1.56", "name": "Front Door"}])
        self.h.hub.upsert("reolink.doorbell", kind="doorbell", name="Front Door", integration="reolink")
        c = self.opts("reolink.doorbell")
        self.assertEqual(c["own"], "Reolink (no video)")  # not connected here: no picture of its own
        self.assertEqual([o["label"] for o in c["options"]], ["Frigate"])
        f = c["options"][0]
        self.assertEqual(f["device"], "frigate.camera_front")  # 10.1.1.56, picked automatically
        self.assertEqual([x["name"] for x in f["cameras"]], ["Front", "Front Porch", "Garage"])  # no zone
        r = await self.put("reolink.doorbell", {"videoFrom": f["device"]})
        self.assertEqual(r.status, 200)
        v = (await r.json())["video"]
        self.assertEqual((v["from"], v["fromIntegration"]), ("frigate.camera_front", "frigate"))
        self.assertEqual(src_of(self.video.live_url("reolink.doorbell", "main")), "front")

    async def test_without_an_address_any_camera_can_be_picked(self):
        c = (await self.ent("bell.front"))["video"]["choices"]
        self.assertEqual(c["own"], "Bell (this device)")
        self.assertEqual(len(c["options"]), 1)
        self.assertEqual((c["options"][0]["integration"], c["options"][0]["device"]), ("frigate", None))
        self.assertEqual([x["id"] for x in c["options"][0]["cameras"]],
                         ["frigate.camera_front", "frigate.camera_front_porch", "frigate.camera_garage"])
        lock = self.opts("fake.side")
        self.assertTrue(lock["own"].endswith(" (no video)"), lock["own"])
        self.assertEqual([o["integration"] for o in lock["options"]], ["frigate"])
        # The list of every device gives each the same choices as asking for one.
        listed = {e["id"]: e["video"]["choices"] for e in await (await self.client.get("/api/entities")).json()}
        self.assertEqual(listed["bell.front"], c)

    async def test_cameras_of_the_same_connection_are_not_offered(self):
        c = self.opts("frigate.camera_front_porch")
        self.assertEqual(c, {"own": "Frigate (this device)", "options": []})
        # A link made before (by id) still works and is still shown as it is.
        await self.put("frigate.camera_front", {"videoFrom": "frigate.camera_garage"})
        v = (await self.ent("frigate.camera_front"))["video"]
        self.assertEqual((v["from"], v["fromIntegration"], v["source"]), ("frigate.camera_garage", "frigate", "frigate.camera_garage"))

    async def test_derived_devices_follow_their_origin(self):
        zone = "frigate.zone_front_patio"
        z = (await self.ent(zone))["video"]
        self.assertEqual((z["derivedFrom"], z["streams"], z["choices"]["options"]), ("frigate.camera_front_porch", [], []))
        self.assertTrue(all(zone not in [x["id"] for x in o["cameras"]] for o in self.opts("bell.front")["options"]))
        self.assertNotIn(zone, [o["id"] for o in self.video.origins()])
        self.assertEqual((await self.put(zone, {"tileStream": "front_porch_live"})).status, 400)  # none of its own
        await self.put("frigate.camera_front_porch", {"tileStream": "front_porch_live"})
        self.assertEqual(src_of(self.video.live_url(zone, "sub")), "front_porch_live")  # its camera's choice
        self.assertIn("front_porch_live", (await self.ent(zone))["video"]["key"])
        # Linking to a zone means its camera.
        r = await self.put("bell.front", {"videoFrom": zone})
        self.assertEqual(((await r.json())["video"]["from"]), "frigate.camera_front_porch")
        self.assertEqual(self.h.store.entity_settings()["bell.front"], {"videoFrom": "frigate.camera_front_porch"})

    async def test_an_old_link_to_its_own_zone_counts_as_none(self):
        cam = "frigate.camera_front_porch"
        self.h.store.set_entity_settings(cam, {"videoFrom": "frigate.zone_front_patio"})  # the old dropdown allowed it
        self.video.reload()
        v = (await self.ent(cam))["video"]
        self.assertEqual((v["from"], v["source"], v["key"]), (None, None, "||"))
        self.assertEqual(self.opts(cam)["own"], "Frigate (this device)")
        self.assertEqual(src_of(self.video.live_url(cam, "sub")), "front_porch_sub")
        self.assertEqual(self.video.linked_to(cam), [])
        r = await self.put(cam, {"showInCameras": False})  # any save cleans it up
        self.assertEqual(r.status, 200)
        self.assertEqual(self.h.store.entity_settings()[cam], {"showInCameras": False})
        r = await self.put(cam, {"videoFrom": "frigate.zone_front_patio"})  # picked again: the same as none
        self.assertEqual(r.status, 200)
        self.assertEqual(self.h.store.entity_settings()[cam], {"showInCameras": False})


if __name__ == "__main__":
    unittest.main()
