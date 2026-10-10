"""Removing offline devices (POST /api/entities/remove): admins only; online or unknown devices are refused
(nothing removed); what still uses a device is reported by name (automations, dashboards, phone layouts) and
blocks the removal until "force"; a removal drops the device (the screens are told), the name and settings
given to it and "Video from" links to it; automations left pointing at it say so; and a connection that
reports it again brings it back as a new device."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import build_app  # noqa: E402
from newman.automations import describe as d  # noqa: E402
from newman.config import config  # noqa: E402
from test_auth import Browser, FakeLocks, H  # noqa: E402


def tile(t, x, y, w, h, **cfg):
    return {"id": f"{t}{x}{y}", "type": t, "x": x, "y": y, "w": w, "h": h, "config": cfg}


class RemoveDeviceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.h.integrations.instances["fake"] = FakeLocks(self.h.integrations._ctx)
        hub = self.h.hub
        for eid, kind, name, state, available in (
                ("fake.side", "lock", "Side Door", "LOCKED", True),
                ("fake.old", "lock", "Old Lock", "LOCKED", False),
                ("fake.shed", "contact", "Shed Door", "closed", False),
                ("fake.cam", "camera", "Old Camera", "offline", True),
                ("fake.zone", "camera", "Driveway", "online", True)):
            hub.upsert(eid, kind=kind, name=name, integration="fake")
            await hub.update(eid, state=state, available=available)
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        r = await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        self.assertEqual(r.status, 200)
        await self.client.post("/api/users", json={"username": "dante", "name": "Dante", "role": "family",
                                                   "password": "another pass"})
        self.removed = []
        self.h.hub.subscribe_registry(self._added, self._gone)
        self.open = []

    async def _added(self, ent):
        pass

    async def _gone(self, ids):
        self.removed.extend(ids)

    async def asyncTearDown(self):
        for c in reversed(self.open):
            await c.close()
        await self.client.close()
        self.tmp.cleanup()

    async def remove(self, ids, **extra):
        r = await self.client.post("/api/entities/remove", json={"ids": ids, **extra})
        return r.status, await r.json()

    async def test_only_admins(self):
        c = Browser(self.client)
        self.open.append(c)
        b = await (await c.post("/api/auth/login", json={"username": "dante", "password": "another pass", "kind": "app"})).json()
        r = await c.post("/api/entities/remove", json={"ids": ["fake.old"]}, headers={"Authorization": "Bearer " + b["token"]})
        self.assertEqual(r.status, 403)
        self.assertIsNotNone(self.h.hub.get("fake.old"))

    async def test_refuses_online_and_unknown_devices(self):
        status, body = await self.remove(["fake.old", "fake.side"])
        self.assertEqual(status, 400)
        self.assertIn("Side Door is online", body["error"])
        self.assertIsNotNone(self.h.hub.get("fake.old"))  # nothing removed
        status, body = await self.remove(["fake.nope"])
        self.assertEqual(status, 400)
        self.assertIn("Unknown device", body["error"])
        self.assertEqual((await self.remove([]))[0], 400)

    async def test_removes_offline_devices_and_what_was_given_to_them(self):
        # A name and video settings given to it, and another camera showing its video.
        r = await self.client.patch("/api/entities/fake.cam", json={"name": "Garage Camera"})
        self.assertEqual(r.status, 200)
        self.h.store.set_entity_settings("fake.cam", {"showInCameras": False})
        self.h.store.set_entity_settings("fake.zone", {"videoFrom": "fake.cam", "showInCameras": True})
        self.h.video.reload()
        status, body = await self.remove(["fake.old", "fake.cam", "fake.old"])
        self.assertEqual(status, 200, body)
        self.assertEqual(body["removed"], ["fake.old", "fake.cam"])
        self.assertIsNone(self.h.hub.get("fake.old"))
        self.assertIsNone(self.h.hub.get("fake.cam"))
        await asyncio.sleep(0.05)
        self.assertEqual(sorted(self.removed), ["fake.cam", "fake.old"])  # entity-removed to the screens
        self.assertNotIn("fake.cam", self.h.store.entity_names())
        settings = self.h.store.entity_settings()
        self.assertNotIn("fake.cam", settings)
        self.assertEqual(settings["fake.zone"], {"showInCameras": True})
        ids = [e["id"] for e in await (await self.client.get("/api/entities")).json()]
        self.assertNotIn("fake.old", ids)
        # Its connection reports it again: a new device, under the connection's own name.
        self.h.hub.upsert("fake.cam", kind="camera", name="Camera 3", integration="fake")
        await asyncio.sleep(0.05)
        self.assertEqual(self.h.hub.get("fake.cam").name, "Camera 3")

    async def test_reports_what_uses_it_and_needs_force(self):
        a = await (await self.client.post("/api/automations", json={
            "name": "Shed open", "trigger": {"type": "state", "entities": ["fake.shed"], "to": "open"},
            "condition": {"mode": "always", "entities": []}, "notify": {"enabled": False, "buttons": []},
            "actions": [{"entity": "fake.old", "command": "lock"}], "recipients": []})).json()
        self.assertIn("id", a, a)
        r = await self.client.post("/api/dashboards", json={"name": "Garden", "pages": [
            {"id": "p", "name": "Main", "icon": "home", "tiles": [tile("devices", 0, 0, 4, 4, entities=["fake.shed"])]}]})
        self.assertEqual(r.status, 200, await r.text())
        status, body = await self.remove(["fake.shed", "fake.old"], dryRun=True)
        self.assertEqual(status, 200)
        used = {x["id"]: x["usedBy"] for x in body["devices"]}
        self.assertEqual(used["fake.old"], [{"type": "automation", "id": a["id"], "name": "Shed open"}])
        self.assertEqual({(u["type"], u["name"]) for u in used["fake.shed"]}, {("automation", "Shed open"), ("dashboard", "Garden")})
        self.assertIsNotNone(self.h.hub.get("fake.shed"))  # a dry run removes nothing
        status, body = await self.remove(["fake.shed", "fake.old"])
        self.assertEqual(status, 409)
        self.assertEqual(len(body["devices"]), 2)
        self.assertIsNotNone(self.h.hub.get("fake.old"))
        status, body = await self.remove(["fake.shed", "fake.old"], force=True)
        self.assertEqual(status, 200)
        self.assertIsNone(self.h.hub.get("fake.shed"))
        # The automation is kept, still lists fine, and says its device is missing.
        r = await self.client.get("/api/automations")
        self.assertEqual(r.status, 200)
        self.assertIn(a["id"], [x["id"] for x in await r.json()])
        errs = d.errors(self.h.engine.autos[a["id"]], self.h.hub)
        self.assertIn("An action refers to a device that no longer exists", errs)
        r = await self.client.get("/api/dashboards")
        self.assertEqual(r.status, 200)


if __name__ == "__main__":
    unittest.main()
