"""Reconnecting a connection (devices it no longer provides are kept, unavailable), Frigate following its
own config changes (restart message / slow timer), and its camera/zone filter stored as exclusions (older
include lists converted once Frigate's list is known)."""
import asyncio
import copy
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

import httpx  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.integrations import REGISTRY  # noqa: E402
from newman.integrations.base import Integration, merge_config  # noqa: E402
from newman.integrations.frigate import FrigateIntegration  # noqa: E402
from test_auth import Browser, H  # noqa: E402


def frigate_cfg():
    return {"go2rtc": {"streams": {"porch": [], "garage": [], "back": []}},
            "mqtt": {"topic_prefix": "frigate"},
            "cameras": {"porch": {"objects": {"track": ["person", "car"]}, "zones": {"walk": {}, "steps": {}}},
                        "garage": {"objects": {"track": ["person"]}, "zones": {"driveway": {"objects": ["car"]}}},
                        "back": {"objects": {"track": ["person", "dog"]}, "zones": {}}}}


class Resp:
    def __init__(self, data):
        self._data, self.status_code, self.content = data, 200, b""

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self, cfg):
        self.cfg, self.down, self.calls = cfg, False, 0

    async def get(self, url, params=None):
        if self.down:
            raise httpx.ConnectError("Frigate is down")
        if url.endswith("/api/config"):
            self.calls += 1
            return Resp(copy.deepcopy(self.cfg))
        return Resp([])

    async def aclose(self):
        pass


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.ctx = self.h.integrations._ctx
        self.saved = []

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def frigate(self, settings=None, cfg=None):
        f = FrigateIntegration("frigate", "Frigate", FrigateIntegration.migrate_config(
            {"url": "http://frigate:5000", **(settings or {})}), self.ctx)
        await f._http.aclose()
        f._http = FakeHttp(cfg or frigate_cfg())
        f.save_config = lambda c: self.saved.append(dict(c))
        return f

    def ids(self):
        return sorted(e.id for e in self.h.hub.all() if e.integration == "frigate")


class FilterMigrationTests(Base):
    async def test_all_ticked_hides_nothing(self):
        f = await self.frigate({"cameras": ["porch", "garage", "back"], "zones": ["walk", "steps", "driveway"]})
        await f._load_config()
        self.assertEqual(f.config["hide_cameras"], [])
        self.assertEqual(f.config["hide_zones"], [])
        self.assertNotIn("cameras", f.config)
        self.assertNotIn("zones", f.config)
        self.assertEqual(self.saved[-1], f.config)  # converted once, saved

    async def test_some_ticked_hides_the_rest(self):
        f = await self.frigate({"cameras": ["porch", "garage"], "zones": ["walk"]})
        await f._load_config()
        self.assertEqual(f.config["hide_cameras"], ["back"])
        self.assertEqual(f.config["hide_zones"], ["steps", "driveway"])
        self.assertEqual(self.ids(), ["frigate.camera_garage", "frigate.camera_porch", "frigate.zone_walk"])

    async def test_frigate_unreachable_keeps_the_old_choice(self):
        f = await self.frigate({"cameras": ["porch"], "zones": ["walk"]})
        f._http.down = True
        with self.assertRaises(httpx.HTTPError):
            await f._load_config()
        self.assertEqual(f.config["cameras"], ["porch"])
        self.assertEqual(f.config["zones"], ["walk"])
        self.assertNotIn("hide_cameras", f.config)
        self.assertEqual(self.saved, [])
        # Saving (merge) keeps the not-yet-converted list too.
        self.assertEqual(merge_config(FrigateIntegration, f.config, {"severity": "both"})["cameras"], ["porch"])
        # Reachable again: converted then, the same cameras shown.
        f._http.down = False
        await f._load_config()
        self.assertEqual(f.config["hide_cameras"], ["garage", "back"])
        self.assertEqual(self.ids(), ["frigate.camera_porch", "frigate.zone_walk"])

    async def test_empty_include_list_needs_no_frigate(self):
        self.assertEqual(FrigateIntegration.migrate_config({"url": "x", "cameras": [], "zones": []}),
                         {"url": "x", "hide_cameras": [], "hide_zones": []})
        self.assertEqual(FrigateIntegration.migrate_config({"url": "x", "cameras": ["a"]}), {"url": "x", "cameras": ["a"]})
        self.assertEqual(FrigateIntegration.migrate_config({"url": "x", "cameras": None, "hide_cameras": ["a"]}),
                         {"url": "x", "hide_cameras": ["a"]})

    async def test_older_clients_include_lists_are_translated(self):
        f = await self.frigate()
        await f._load_config()
        self.assertEqual(f.translate_config({"cameras": ["porch"], "zones": []}),
                         {"hide_cameras": ["garage", "back"], "cameras": None, "hide_zones": [], "zones": None})
        # A hide list sent now replaces an older list still waiting.
        self.assertEqual(f.translate_config({"hide_cameras": ["back"]}), {"hide_cameras": ["back"], "cameras": None})
        # Frigate's list not known yet: kept, converted on connect.
        g = await self.frigate()
        self.assertEqual(g.translate_config({"cameras": ["porch"]}), {"cameras": ["porch"]})
        self.assertEqual(set((await f.dynamic_options())), {"hide_cameras", "hide_zones", "cameras", "zones"})


class RefreshTests(Base):
    async def test_new_camera_appears_hidden_one_stays_out(self):
        f = await self.frigate({"hide_cameras": ["back"]})
        await f._load_config()
        self.assertNotIn("frigate.camera_back", self.ids())
        self.assertFalse(await f.refresh())  # nothing changed
        cfg = frigate_cfg()
        cfg["cameras"]["shed"] = {"objects": {"track": ["person"]}, "zones": {"lawn": {}}}
        cfg["cameras"]["back"]["objects"]["track"].append("cat")
        f._http.cfg = cfg
        self.assertTrue(await f.refresh())
        self.assertIn("frigate.camera_shed", self.ids())
        self.assertIn("frigate.zone_lawn", self.ids())
        self.assertNotIn("frigate.camera_back", self.ids())

    async def test_gone_camera_is_kept_unavailable_and_counts_survive(self):
        f = await self.frigate()
        await f._load_config()
        await f._set_count("porch", {"person": 2})
        await f._set_count("walk", {"person": 1})
        cfg = frigate_cfg()
        del cfg["cameras"]["garage"]
        cfg["cameras"]["porch"]["friendly_name"] = "Front Porch"
        cfg["cameras"]["porch"]["zones"]["walk"]["objects"] = ["person", "dog"]
        f._http.cfg = cfg
        changed = []

        async def heard(old, new):
            changed.append(new.id)
        self.h.hub.subscribe_changes(heard)
        await f.refresh()
        await asyncio.sleep(0)
        garage, zone = self.h.hub.get("frigate.camera_garage"), self.h.hub.get("frigate.zone_driveway")
        self.assertIsNotNone(garage)
        self.assertFalse(garage.available)
        self.assertFalse(zone.available)
        porch, walk = self.h.hub.get("frigate.camera_porch"), self.h.hub.get("frigate.zone_walk")
        self.assertEqual(porch.name, "Front Porch")
        self.assertEqual(porch.attrs["counts"], {"person": 2, "car": 0})
        self.assertEqual(walk.attrs["counts"], {"person": 1, "dog": 0})
        self.assertEqual(walk.state, "occupied")
        self.assertIn("frigate.camera_porch", changed)  # pushed to the screens like any update
        # Frigate back online must not bring the gone camera back.
        await f._on_message("frigate", "frigate/available", b"online")
        self.assertFalse(self.h.hub.get("frigate.camera_garage").available)

    async def test_frigate_coming_back_online_rereads_its_config(self):
        f = await self.frigate()
        await f._load_config()
        calls = f._http.calls
        await f._on_message("frigate", "frigate/available", b"online")  # the broker's kept state: no re-read
        self.assertIsNone(f._refresh_task)
        cfg = frigate_cfg()
        cfg["cameras"]["porch"]["zones"]["gate"] = {}
        f._http.cfg = cfg
        await f._on_message("frigate", "frigate/available", b"offline")
        self.assertFalse(self.h.hub.get("frigate.camera_porch").available)
        await f._on_message("frigate", "frigate/available", b"online")
        await f._refresh_task
        self.assertEqual(f._http.calls, calls + 1)
        self.assertIn("frigate.zone_gate", self.ids())
        self.assertTrue(self.h.hub.get("frigate.camera_porch").available)

    async def test_slow_timer_rereads_when_messages_were_missed(self):
        import newman.integrations.frigate as fr
        f = await self.frigate()
        await f._load_config()
        cfg = frigate_cfg()
        cfg["cameras"]["attic"] = {"objects": {"track": ["person"]}}
        f._http.cfg = cfg
        old, fr.REFRESH_S = fr.REFRESH_S, 0.01
        try:
            timer = asyncio.create_task(f._refresh_timer())
            for _ in range(50):
                await asyncio.sleep(0.01)
                if "frigate.camera_attic" in self.ids():
                    break
            timer.cancel()
        finally:
            fr.REFRESH_S = old
        self.assertIn("frigate.camera_attic", self.ids())


class FakeConn(Integration):
    """A connection whose devices come from a list the test changes."""
    type = "fakeconn"
    name = "Fake connection"
    provide = ["a", "b"]
    starts = 0

    async def run(self):
        FakeConn.starts += 1
        for d in FakeConn.provide:
            self.hub.upsert(self.entity_id(d), kind="switch", name=d.upper(), integration=self.id)
            await self.hub.update(self.entity_id(d), state="on")
        self.set_status("connected", f"{len(FakeConn.provide)} devices")
        await asyncio.Event().wait()


class ReconnectTests(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        REGISTRY["fakeconn"] = FakeConn
        FakeConn.provide, FakeConn.starts = ["a", "b"], 0
        self.h.integrations.SETTLE_S = 0
        self.h.store.save_integration({"id": "fc", "type": "fakeconn", "name": "Fake", "config": {}})
        self.h.integrations.start_all()
        await asyncio.sleep(0.05)
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})

    async def asyncTearDown(self):
        await self.client.close()
        await self.h.integrations.stop_all()
        REGISTRY.pop("fakeconn", None)
        await super().asyncTearDown()

    async def test_reconnect_restarts_with_saved_settings_and_keeps_missing_devices(self):
        removed = []

        async def gone(ids):
            removed.extend(ids)

        async def added(e):
            pass
        self.h.hub.subscribe_registry(added, gone)
        FakeConn.provide = ["a", "c"]  # "b" is no longer there
        r = await self.client.post("/api/integrations/fc/reconnect")
        self.assertEqual(r.status, 200)
        body = await r.json()
        self.assertEqual(body["status"], "connected")
        self.assertEqual(body["detail"], "2 devices")
        self.assertEqual(FakeConn.starts, 2)
        for _ in range(50):
            await asyncio.sleep(0.02)
            if not self.h.integrations._settling:
                break
        b = self.h.hub.get("fc.b")
        self.assertIsNotNone(b)  # kept: automations refer to it
        self.assertFalse(b.available)
        self.assertTrue(self.h.hub.get("fc.a").available)
        self.assertTrue(self.h.hub.get("fc.c").available)
        self.assertEqual(removed, [])
        self.assertEqual(self.h.store.integrations()[0]["config"], {})  # settings untouched

    async def test_reconnect_is_admin_only_and_needs_a_running_connection(self):
        self.assertEqual((await self.client.post("/api/integrations/nope/reconnect")).status, 404)
        await self.client.post("/api/users", json={"username": "marlina", "name": "Marlina", "role": "family",
                                                    "password": "another pass"})
        c = Browser(self.client)
        await c.post("/api/auth/login", json={"username": "marlina", "password": "another pass"})
        self.assertEqual((await c.post("/api/integrations/fc/reconnect")).status, 403)
        await c.close()
        await self.client.patch("/api/integrations/fc", json={"enabled": False})
        self.assertEqual((await self.client.post("/api/integrations/fc/reconnect")).status, 400)


if __name__ == "__main__":
    unittest.main()
