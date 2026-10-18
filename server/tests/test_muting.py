"""Muting a device's problem (muting.py): per problem, for a day / a week / until it's fixed / always; left
out of health ok/issues and Today's To do, still listed on the device ("items")."""
import asyncio
import dataclasses
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman import muting as M  # noqa: E402
from newman import today as T  # noqa: E402
from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.replace import replace_entities  # noqa: E402
from test_auth import H, Browser  # noqa: E402

KEYPAD = "ring.keypad"


class ProblemIdTests(unittest.TestCase):
    def test_every_problem_has_a_stable_id(self):
        from newman.state import Entity
        e = Entity(id="x.y", kind="lock", name="Lock", integration="x", available=False, state="JAMMED",
                   attrs={"batteryLevel": 9, "tamperStatus": "tamper", "acStatus": "error", "commStatus": "error",
                          "linkQuality": "poor", "commandFailures": 2, "canComeBack": False})
        self.assertEqual([i for i, _ in e.health_items],
                         ["offline", "battery_low", "tamper", "no_ac", "not_responding", "weak_signal",
                          "ignored_commands", "jammed", "overlay_permission"])
        self.assertEqual(e.health_issues[1], "Battery 9%")
        e = dataclasses.replace(e, attrs={"batteryStatus": "failed"}, available=True, state="LOCKED")
        self.assertEqual(e.health_items, [("battery_failed", "Battery failed")])
        e = dataclasses.replace(e, attrs={"batteryLow": True, "batteryType": "CR2032"})
        self.assertEqual(e.health_items, [("battery_low", "Battery low (takes CR2032)")])


class MutingApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.now = [1_800_000_000.0]
        self.h.muting = M.Muting(self.h.store, self.h.hub, clock=lambda: self.now[0])
        self.h.hub.upsert(KEYPAD, kind="motion", name="Hallway Keypad", integration="ring")
        await self.h.hub.update(KEYPAD, state="clear", attrs={"acStatus": "error", "batteryLevel": 15})
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def health(self, c=None):
        ents = await ((c or self.client).get("/api/entities")).__aenter__()
        rows = await ents.json()
        return next(e for e in rows if e["id"] == KEYPAD)["health"]

    async def mute(self, issue, mode, c=None):
        return await (c or self.client).put(f"/api/entities/{KEYPAD}/mute", json={"issue": issue, "for": mode})

    async def test_mute_always_leaves_it_out_but_lists_it(self):
        h = await self.health()
        self.assertEqual((h["ok"], h["issues"]), (False, ["Battery 15%", "No AC power"]))
        r = await self.mute("no_ac", "always")
        self.assertEqual(r.status, 200)
        h = (await r.json())["health"]
        self.assertEqual(h["issues"], ["Battery 15%"])
        self.assertEqual([(i["id"], i["text"], i["muted"] and i["muted"]["mode"]) for i in h["items"]],
                         [("battery_low", "Battery 15%", None), ("no_ac", "No AC power", "always")])
        self.assertEqual(h["items"][1]["muted"]["by"], "Chris")
        await self.mute("battery_low", "always")
        h = await self.health()
        self.assertEqual((h["ok"], h["issues"]), (True, []))
        self.assertIn(KEYPAD, self.h.store.settings()["mutedIssues"])
        # Unmute brings it back; the activity log says who did both.
        r = await self.client.delete(f"/api/entities/{KEYPAD}/mute/no_ac")
        self.assertEqual((await r.json())["health"]["issues"], ["No AC power"])
        titles = [e["title"] for e in self.h.store.events(10)]
        self.assertIn("Chris muted “No AC power” on Hallway Keypad (always)", titles)
        self.assertIn("Chris unmuted “No AC power” on Hallway Keypad", titles)

    async def test_refusals(self):
        self.assertEqual((await self.mute("jammed", "always")).status, 400)   # not reporting it
        self.assertEqual((await self.mute("nonsense", "day")).status, 400)
        self.assertEqual((await self.mute("no_ac", "forever")).status, 400)
        r = await self.client.put("/api/entities/x.none/mute", json={"issue": "no_ac", "for": "day"})
        self.assertEqual(r.status, 404)

    async def test_day_and_week_end_by_themselves(self):
        await self.mute("no_ac", "day")
        await self.mute("battery_low", "week")
        self.assertEqual((await self.health())["issues"], [])
        self.now[0] += 86400 + 1
        self.assertEqual((await self.health())["issues"], ["No AC power"])
        self.now[0] += 6 * 86400
        self.assertEqual((await self.health())["issues"], ["Battery 15%", "No AC power"])
        self.assertNotIn(KEYPAD, self.h.store.settings().get("mutedIssues") or {})

    async def test_until_fixed_goes_when_the_problem_does(self):
        await self.mute("no_ac", "fixed")
        self.assertEqual((await self.health())["issues"], ["Battery 15%"])
        await self.h.hub.update(KEYPAD, attrs={"acStatus": "ok"})           # plugged in: fixed
        await asyncio.sleep(0.05)
        self.assertNotIn("no_ac", (self.h.store.settings().get("mutedIssues") or {}).get(KEYPAD, {}))
        await self.h.hub.update(KEYPAD, attrs={"acStatus": "error"})        # unplugged again: told again
        self.assertEqual((await self.health())["issues"], ["Battery 15%", "No AC power"])

    async def test_family_and_wall_tablets_may_mute(self):
        await self.client.post("/api/users", json={"username": "marlina", "name": "Marlina", "role": "family",
                                                    "password": "another pass"})
        await self.client.post("/api/users", json={"username": "hall", "name": "Hallway", "role": "tablet",
                                                    "password": "wall tablet pass"})
        fam, tab = Browser(self.client), Browser(self.client)
        await fam.post("/api/auth/login", json={"username": "marlina", "password": "another pass"})
        r = await tab.post("/api/auth/login", json={"username": "hall", "password": "wall tablet pass", "kind": "tablet"})
        tauth = {"Authorization": "Bearer " + (await r.json())["token"]}
        r = await fam.put(f"/api/entities/{KEYPAD}/mute", json={"issue": "no_ac", "for": "always"})
        self.assertEqual(r.status, 200)
        r = await tab.put(f"/api/entities/{KEYPAD}/mute", json={"issue": "battery_low", "for": "day"}, headers=tauth)
        self.assertEqual(r.status, 200, await r.text())
        r = await tab.delete(f"/api/entities/{KEYPAD}/mute/battery_low", headers=tauth)
        self.assertEqual(r.status, 200)
        self.assertEqual((await r.json())["health"]["issues"], ["Battery 15%"])
        self.assertEqual((await tab.post("/api/automations", json={}, headers=tauth)).status, 403)  # still no config
        await fam.close()
        await tab.close()

    async def test_today_leaves_muted_problems_out(self):
        def todo():
            return [t for t in T.build(self.h.hub, self.h.store.settings(), now=datetime.fromtimestamp(self.now[0]))["todo"]
                    if t["entity"] == KEYPAD]
        self.assertEqual(len(todo()), 1)
        await self.mute("no_ac", "always")
        self.assertEqual([t["title"] for t in todo()], ["Hallway Keypad: battery 15%"])
        await self.mute("battery_low", "week")
        self.assertEqual(todo(), [])

    async def test_a_swapped_device_keeps_its_mutes_and_a_removed_one_drops_them(self):
        await self.mute("no_ac", "always")
        self.h.hub.upsert("ring.keypad2", kind="motion", name="New Keypad", integration="ring")
        await self.h.hub.update("ring.keypad2", attrs={"acStatus": "error"})
        counts = await replace_entities(self.h, [{"from": KEYPAD, "to": "ring.keypad2"}])
        self.assertEqual(counts.get("muted"), 1)
        self.assertIn("ring.keypad2", self.h.store.settings()["mutedIssues"])
        await self.h.hub.update("ring.keypad2", available=False)
        r = await self.client.post("/api/entities/remove", json={"ids": ["ring.keypad2"], "force": True})
        self.assertEqual(r.status, 200, await r.text())
        self.assertNotIn("ring.keypad2", self.h.store.settings().get("mutedIssues") or {})


if __name__ == "__main__":
    unittest.main()
