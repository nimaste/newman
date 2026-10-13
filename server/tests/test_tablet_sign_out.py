"""A wall tablet's devices (its battery and its music player) last only while its account has a wall-tablet
sign-in that sign-in still accepts: signing out, an admin ending the sign-in, the account removed or the
sign-in expired (the check at start and every hour) removes them and their saved data - without a smart
charging alert or a plug switched, and keeping the account's smart charging settings. A tablet that's
signed in keeps them, however quiet it is."""
import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.integrations.navidrome import NavidromeIntegration  # noqa: E402
from newman.integrations.tablets import TabletsIntegration  # noqa: E402
from test_auth import Browser, FakeLocks, H  # noqa: E402

LOW = {"level": 15, "charging": False, "plugged": "none"}


class TabletSignOutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", self.data)
        self.h = H(self.data)
        self.plugs = FakeLocks(self.h.integrations._ctx)
        self.h.integrations.instances["fake"] = self.plugs
        self.h.hub.upsert("fake.plug", kind="switch", name="Kitchen plug", integration="fake")
        await self.h.hub.update("fake.plug", state="off")
        # The music connection, not signed in to Navidrome: the wall page's reports still make its player.
        self.music = NavidromeIntegration("navidrome", "Navidrome", {}, self.h.integrations._ctx)
        self.h.integrations.instances["navidrome"] = self.music
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        r = await self.client.post("/api/users", json={"username": "kitchen", "name": "Newman Kitchen", "role": "tablet",
                                                       "password": "wall tablet pass"})
        self.kitchen = (await r.json())["id"]
        self.admin = self.h.store.user_by_name("chris")["id"]
        self.open = []

    async def asyncTearDown(self):
        for c in self.open:
            await c.close()
        await self.client.close()
        self.tmp.cleanup()

    async def sign_in(self, username, password):
        """A wall tablet signing in: (its client, its auth header)."""
        c = Browser(self.client)
        self.open.append(c)
        r = await c.post("/api/auth/login", json={"username": username, "password": password, "kind": "tablet"})
        self.assertEqual(r.status, 200)
        return c, {"Authorization": "Bearer " + (await r.json())["token"]}

    async def use(self, c, auth, battery=LOW):
        """What a wall page does: report its battery and its music player."""
        self.assertEqual((await c.post("/api/tablet/battery", json=battery, headers=auth)).status, 204)
        self.assertEqual((await c.post("/api/music/report", json={}, headers=auth)).status, 200)

    def tablets(self) -> TabletsIntegration:
        return next(i for i in self.h.integrations.instances.values() if isinstance(i, TabletsIntegration))

    def saved(self, path: Path) -> dict:
        return json.loads(path.read_text()) if path.exists() else {}

    def tablet_sessions(self, uid):
        return [s for s in self.h.store.sessions(uid) if s["kind"] == "tablet"]

    async def test_signing_out_removes_its_battery_and_player_without_charging_alerts(self):
        c, auth = await self.sign_in("kitchen", "wall tablet pass")
        r = await self.client.patch(f"/api/charging/{self.kitchen}", json={"enabled": True, "charger": "fake.plug"})
        self.assertEqual(r.status, 200, await r.text())
        await self.use(c, auth)
        await asyncio.sleep(0.05)
        self.assertEqual(self.plugs.calls, [("fake.plug", "turn_on")])  # below 20 %: smart charging at work
        self.plugs.calls.clear()
        tab, player = "tablets." + self.kitchen, "navidrome." + self.kitchen
        self.assertIsNotNone(self.h.hub.get(tab))
        self.assertIsNotNone(self.h.hub.get(player))
        problems = len(self.h.store.events(kind="problem"))

        self.assertEqual((await c.post("/api/auth/logout", headers=auth)).status, 200)
        await asyncio.sleep(0.05)
        self.assertIsNone(self.h.hub.get(tab))
        self.assertIsNone(self.h.hub.get(player))
        self.assertNotIn(self.kitchen, self.saved(self.data / "integrations" / self.tablets().id / "tablets.json"))
        self.assertNotIn(self.kitchen, self.saved(self.data / "integrations" / "navidrome" / "players.json"))
        self.assertNotIn(self.kitchen, self.music.players)
        # No plug switched, no alert; a plain line in Activity.
        self.assertEqual(self.plugs.calls, [])
        self.assertEqual(len(self.h.store.events(kind="problem")), problems)
        titles = [e["title"] for e in self.h.store.events(kind="system")]
        self.assertIn("Removed Newman Kitchen's tablet: no longer signed in on a tablet", titles)
        # Its smart charging settings stay for when it signs in again.
        s = await (await self.client.get(f"/api/charging/{self.kitchen}")).json()
        self.assertEqual((s["enabled"], s["charger"], s["device"]), (True, "fake.plug", None))
        # Signing in again brings the battery back, charged from the same plug as before.
        c, auth = await self.sign_in("kitchen", "wall tablet pass")
        await self.use(c, auth)
        await asyncio.sleep(0.05)
        self.assertIsNotNone(self.h.hub.get(tab))
        self.assertEqual(self.plugs.calls, [("fake.plug", "turn_on")])

    async def test_an_admin_ending_the_sign_in_removes_them(self):
        # Someone's own account on a wall tablet for a while (as happened with the kitchen tablet).
        c, auth = await self.sign_in("chris", "correct horse")
        await self.use(c, auth, {**LOW, "level": 50})
        self.assertIsNotNone(self.h.hub.get("tablets." + self.admin))
        sid = self.tablet_sessions(self.admin)[0]["id"]
        r = await self.client.delete(f"/api/auth/sessions/{sid}")
        self.assertEqual(r.status, 200)
        await asyncio.sleep(0.05)
        self.assertIsNone(self.h.hub.get("tablets." + self.admin))
        self.assertIsNone(self.h.hub.get("navidrome." + self.admin))
        self.assertNotIn(self.admin, self.music.players)
        # The admin's own browser sign-in is untouched (it isn't a wall tablet's).
        self.assertEqual((await self.client.get("/api/auth/state")).status, 200)
        self.assertTrue((await (await self.client.get("/api/auth/state")).json())["signedIn"])

    async def test_kept_while_signed_in_however_quiet(self):
        c, auth = await self.sign_in("kitchen", "wall tablet pass")
        c2, auth2 = await self.sign_in("kitchen", "wall tablet pass")  # signed in again (a reinstalled app)
        await self.use(c, auth, {**LOW, "level": 50})
        tab, player = "tablets." + self.kitchen, "navidrome." + self.kitchen
        # Quiet for days: offline, and its sign-in not seen in that time - still signed in.
        t = self.tablets().tablets[self.kitchen]
        t["at"] -= 5 * 86400
        await self.tablets().check_offline()
        self.assertFalse(self.h.hub.get(tab).available)
        self.h.store._exec("UPDATE sessions SET last_seen = ? WHERE user_id = ?", (time.time() - 5 * 86400, self.kitchen))
        self.h.forget_signed_out_tablets()
        self.assertIsNotNone(self.h.hub.get(tab))
        self.assertIsNotNone(self.h.hub.get(player))
        # One of its two sign-ins ends: the other keeps them.
        self.assertEqual((await c2.post("/api/auth/logout", headers=auth2)).status, 200)
        self.assertIsNotNone(self.h.hub.get(tab))
        # Another account signing out of a wall tablet leaves them alone; so does editing the account.
        c3, auth3 = await self.sign_in("chris", "correct horse")
        self.assertEqual((await c3.post("/api/auth/logout", headers=auth3)).status, 200)
        self.assertEqual((await self.client.patch(f"/api/users/{self.kitchen}", json={"name": "Kitchen"})).status, 200)
        self.assertIsNotNone(self.h.hub.get(tab))
        self.assertIn(self.kitchen, self.music.players)
        self.assertIn(self.kitchen, self.tablets().tablets)
        self.assertFalse(any("no longer signed in" in e["title"] for e in self.h.store.events(kind="system")))

    async def test_the_check_at_start_and_hourly_removes_leftovers(self):
        c, auth = await self.sign_in("kitchen", "wall tablet pass")
        await self.use(c, auth, {**LOW, "level": 50})
        tablets_id = self.tablets().id
        # What a hub restart finds: a leftover tablet and player for an account with no tablet sign-in
        # (signed out before this check existed), next to the kitchen tablet's.
        left = {"name": "Chris", "attrs": {"batteryLevel": 67, "charging": False, "plugged": "none"},
                "state": "battery", "at": time.time() - 3600}
        tf = self.data / "integrations" / tablets_id / "tablets.json"
        saved = self.saved(tf)
        saved[self.admin] = left
        tf.write_text(json.dumps(saved))
        pf = self.data / "integrations" / "navidrome" / "players.json"
        players = self.saved(pf)
        players[self.admin] = {**players[self.kitchen], "name": "Chris"}
        pf.write_text(json.dumps(players))
        # The connections start again (as at hub start, before they bring their devices back).
        for iid in (tablets_id, "navidrome"):
            self.h.hub.remove_integration(iid, grace=None)
        tablets = TabletsIntegration(tablets_id, "Wall tablets", {}, self.h.integrations._ctx)
        music = NavidromeIntegration("navidrome", "Navidrome", {}, self.h.integrations._ctx)
        self.h.integrations.instances[tablets_id], self.h.integrations.instances["navidrome"] = tablets, music
        self.assertIn(self.admin, tablets.tablets)
        self.h.forget_signed_out_tablets()
        self.assertNotIn(self.admin, tablets.tablets)
        self.assertNotIn(self.admin, music.players)
        self.assertNotIn(self.admin, self.saved(tf))
        self.assertNotIn(self.admin, self.saved(pf))
        self.assertIn(self.kitchen, self.saved(tf))
        task = asyncio.create_task(tablets.run())
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertIsNone(self.h.hub.get(f"{tablets_id}.{self.admin}"))
        self.assertIsNotNone(self.h.hub.get(f"{tablets_id}.{self.kitchen}"))
        self.assertIn("Removed Chris's tablet: no longer signed in on a tablet",
                      [e["title"] for e in self.h.store.events(kind="system")])
        # Later its last sign-in expires: the hourly check removes it.
        self.h.store._exec("UPDATE sessions SET expires_at = ? WHERE user_id = ?", (time.time() - 1, self.kitchen))
        self.h.forget_signed_out_tablets()
        self.assertIsNone(self.h.hub.get(f"{tablets_id}.{self.kitchen}"))
        self.assertNotIn(self.kitchen, self.saved(tf))

    async def test_removing_or_disabling_the_account_removes_them(self):
        c, auth = await self.sign_in("kitchen", "wall tablet pass")
        await self.use(c, auth, {**LOW, "level": 50})
        self.assertEqual((await self.client.patch(f"/api/users/{self.kitchen}", json={"disabled": True})).status, 200)
        self.assertIsNone(self.h.hub.get("tablets." + self.kitchen))
        r = await self.client.post("/api/users", json={"username": "hall", "name": "Hall", "role": "tablet",
                                                       "password": "wall tablet pass"})
        hall = (await r.json())["id"]
        c, auth = await self.sign_in("hall", "wall tablet pass")
        await self.use(c, auth, {**LOW, "level": 50})
        self.assertIsNotNone(self.h.hub.get("navidrome." + hall))
        self.assertEqual((await self.client.delete(f"/api/users/{hall}")).status, 200)
        self.assertIsNone(self.h.hub.get("tablets." + hall))
        self.assertIsNone(self.h.hub.get("navidrome." + hall))
        self.assertIn("Removed Hall's tablet: no longer signed in on a tablet",
                      [e["title"] for e in self.h.store.events(kind="system")])


if __name__ == "__main__":
    unittest.main()
