"""Wall tablets: their own accounts, their dashboards, alerts shown on them, and alert buttons."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from test_auth import Browser, FakeLocks, H  # noqa: E402


class WallTabletTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.locks = FakeLocks(self.h.integrations._ctx)
        self.h.integrations.instances["fake"] = self.locks
        self.h.hub.upsert("fake.side", kind="lock", name="Side Door", integration="fake")
        await self.h.hub.update("fake.side", state="LOCKED")
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        self.alerts = []

        async def seen(alert):
            self.alerts.append(alert)
        self.h.engine.wall_listeners.append(seen)

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def add_tablet(self, name="Hallway tablet", username="hallway"):
        r = await self.client.post("/api/users", json={"username": username, "name": name, "role": "tablet",
                                                       "password": "wall tablet pass"})
        self.assertEqual(r.status, 200)
        return await r.json()

    async def sign_in(self, username, password, kind):
        c = Browser(self.client)
        r = await c.post("/api/auth/login", json={"username": username, "password": password, "kind": kind})
        return c, r, await r.json()

    async def test_a_tablet_account_is_not_a_person_and_only_signs_in_on_a_tablet(self):
        tab = await self.add_tablet()
        self.assertEqual(tab["role"], "tablet")
        self.assertNotIn("Hallway tablet", [p["name"] for p in self.h.store.people()])  # never notified or located
        for kind in ("browser", "app"):
            c, r, body = await self.sign_in("hallway", "wall tablet pass", kind)
            self.assertEqual(r.status, 403, kind)
            self.assertIn("wall tablet", body["error"])
            await c.close()
        c, r, body = await self.sign_in("hallway", "wall tablet pass", "tablet")
        self.assertEqual(r.status, 200)
        tab_h = {"Authorization": "Bearer " + body["token"]}
        self.assertEqual((await c.get("/api/entities", headers=tab_h)).status, 200)
        self.assertEqual((await c.post("/api/automations", json={}, headers=tab_h)).status, 403)  # changes nothing
        self.assertEqual(await (await c.get("/api/tablets", headers=tab_h)).json(), [{"id": tab["id"], "name": "Hallway tablet"}])
        await c.close()

    async def test_an_admin_lays_out_the_tablets_dashboard(self):
        tab = await self.add_tablet()
        r = await self.client.post("/api/dashboards", json={"name": "Hallway", "userId": tab["id"]})
        self.assertEqual(r.status, 200)
        made = await r.json()
        self.assertEqual(made["userId"], tab["id"])
        c, r, body = await self.sign_in("hallway", "wall tablet pass", "tablet")
        cur = await (await c.get("/api/dashboards/current", headers={"Authorization": "Bearer " + body["token"]})).json()
        self.assertEqual(cur["id"], made["id"])
        await c.close()
        # A family member can't make one for someone else.
        await self.client.post("/api/users", json={"username": "dante", "name": "Dante", "role": "family",
                                                   "password": "another pass"})
        c, r, body = await self.sign_in("dante", "another pass", "browser")
        self.assertEqual((await c.post("/api/dashboards", json={"userId": tab["id"]})).status, 403)
        await c.close()

    async def test_an_automation_shows_its_alert_on_the_tablet(self):
        tab = await self.add_tablet()
        auto = self.h.engine.save({
            "name": "Side door open", "recipients": [],
            "trigger": {"type": "state", "entities": ["fake.side"], "to": "UNLOCKED"},
            "notify": {"enabled": False, "buttons": ["lock:fake.side", "dismiss"]},
            "wall": {"enabled": True, "tablets": [tab["id"]], "style": "full", "sound": True, "wake": True}})
        self.assertIn("show it on Hallway tablet full screen", self.h.engine.describe(auto)["summary"])
        await self.h.hub.update("fake.side", state="UNLOCKED")
        for _ in range(50):
            if self.alerts:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(len(self.alerts), 1)
        a = self.alerts[0]
        self.assertEqual((a["style"], a["tablets"], a["wake"], a["sound"]), ("full", [tab["id"]], True, "chime"))
        self.assertIn("Side Door", a["title"] + a["body"])
        lock = next(b for b in a["buttons"] if b["id"] == "lock:fake.side")
        self.assertEqual((lock["entity"], lock["command"], lock["label"]), ("fake.side", "lock", "Lock Side Door"))

        # The tablet presses it (signed in, no phone token needed); Face ID buttons can't be pressed this way.
        c, r, body = await self.sign_in("hallway", "wall tablet pass", "tablet")
        tab_h = {"Authorization": "Bearer " + body["token"]}
        r = await c.post(f"/api/runs/{a['id']}/press", json={"button": "lock:fake.side"}, headers=tab_h)
        self.assertEqual(r.status, 200)
        self.assertTrue((await r.json())["accepted"])  # replied at once; the lock command follows
        for _ in range(50):
            if ("fake.side", "lock") in self.locks.calls:
                break
            await asyncio.sleep(0.02)
        self.assertIn(("fake.side", "lock"), self.locks.calls)
        r = await c.post(f"/api/runs/{a['id']}/press", json={"button": "dismiss"}, headers=tab_h)
        self.assertEqual(r.status, 200)
        await c.close()
        run = next(x for x in self.h.store.runs(auto["id"]) if x["id"] == a["id"])
        self.assertTrue(any("Hallway tablet" in line["text"] for line in run["log"]))

    async def test_face_id_buttons_cant_be_pressed_from_an_alert(self):
        auto = self.h.engine.save({
            "name": "Unlock offer", "recipients": [],
            "trigger": {"type": "state", "entities": ["fake.side"], "to": "LOCKED"},
            "notify": {"enabled": False, "buttons": ["unlock:fake.side"]},
            "wall": {"enabled": True}})
        await self.h.hub.update("fake.side", state="UNLOCKED")
        await self.h.hub.update("fake.side", state="LOCKED")
        for _ in range(50):
            if self.alerts:
                break
            await asyncio.sleep(0.02)
        a = self.alerts[-1]
        self.assertEqual((a["tablets"], a["style"]), ([], "card"))  # every wall tablet, as a card
        self.assertIn("every wall tablet", self.h.engine.describe(auto)["summary"])
        r = await self.client.post(f"/api/runs/{a['id']}/press", json={"button": "unlock:fake.side"})
        self.assertEqual(r.status, 409)
        self.assertNotIn(("fake.side", "unlock"), self.locks.calls)
