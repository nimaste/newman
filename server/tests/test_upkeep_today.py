"""Upkeep items (due by date or running hours, done / who / undo / snooze) and the Today briefing."""
import asyncio
import os
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman import today as T  # noqa: E402
from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.integrations.base import Context  # noqa: E402
from newman.integrations.upkeep import UpkeepIntegration, add_interval, clean_item  # noqa: E402
from newman.state import Hub  # noqa: E402
from test_auth import Browser, H  # noqa: E402

DAY = 86400


def ago(days):
    return time.time() - days * DAY


class UpkeepTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        self.up = UpkeepIntegration("upkeep", "Upkeep", {}, Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        self.up._people = lambda: [{"id": "p_chris", "name": "Chris"}, {"id": "p_marlina", "name": "Marlina"}]
        self.events = []

        async def seen(ent, event, data):
            self.events.append((ent.id, event))
        self.hub.subscribe_events(seen)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def test_intervals_and_checks(self):
        self.assertEqual(add_interval(date(2026, 1, 31), 1, "months"), date(2026, 2, 28))
        self.assertEqual(add_interval(date(2026, 11, 15), 3, "months"), date(2027, 2, 15))
        self.assertEqual(add_interval(date(2026, 10, 2), 2, "weeks"), date(2026, 10, 16))
        with self.assertRaisesRegex(ValueError, "name"):
            clean_item({"name": " "})
        with self.assertRaisesRegex(ValueError, "how often"):
            clean_item({"name": "Filter", "every": 0})
        with self.assertRaisesRegex(ValueError, "device whose running hours"):
            clean_item({"name": "Filter", "every": 0, "runtimeHours": 500})

    async def test_states_by_date(self):
        late = await self.up.add({"name": "Change oil · BMW X5", "every": 30, "unit": "days", "owner": "p_chris",
                                  "lastDone": datetime.fromtimestamp(ago(33)).date().isoformat()})
        due = await self.up.add({"name": "Softener salt", "every": 30, "lastDone": datetime.fromtimestamp(ago(30)).date().isoformat()})
        soon = await self.up.add({"name": "Furnace filter", "every": 90, "lead": 7,
                                  "lastDone": datetime.fromtimestamp(ago(86)).date().isoformat()})
        fine = await self.up.add({"name": "Smoke alarms", "every": 6, "unit": "months"})
        self.assertEqual([late["state"], due["state"], soon["state"], fine["state"]], ["overdue", "due", "soon", "ok"])
        self.assertEqual((late["daysLeft"], late["ownerName"]), (-3, "Chris"))
        ent = self.hub.get(late["entity"])
        self.assertEqual((ent.kind, ent.state, ent.attrs["owner"], ent.attrs["every"]),
                         ("upkeep", "overdue", "Chris", "every 30 days"))
        self.assertEqual(self.hub.get(fine["entity"]).attrs["owner"], "everyone")

    async def test_done_who_undo_snooze_and_history(self):
        it = await self.up.add({"name": "Softener salt", "every": 30, "lastDone": datetime.fromtimestamp(ago(31)).date().isoformat()})
        done = await self.up.done(it["id"], "Marlina")
        self.assertEqual((done["state"], done["lastDoneBy"], done["daysLeft"]), ("ok", "Marlina", 30))
        back = await self.up.undo(it["id"])
        self.assertEqual(back["state"], "overdue")
        snoozed = await self.up.snooze(it["id"])
        self.assertTrue(snoozed["snoozed"])
        # Saved to disk: a restart keeps it.
        again = UpkeepIntegration("upkeep", "Upkeep", {}, self.up.ctx)
        self.assertEqual([i["name"] for i in again.items], ["Softener salt"])

    async def test_running_hours_count_from_the_thermostat(self):
        self.hub.upsert("hk.thermostat", kind="thermostat", name="Main Floor", integration="hk")
        await self.hub.update("hk.thermostat", state="heat", attrs={"action": "idle"})
        it = await self.up.add({"name": "Furnace filter", "every": 0, "runtimeHours": 500,
                                "runtimeEntity": "hk.thermostat"})
        self.assertEqual(it["state"], "ok")
        self.up.hub.subscribe_changes(self.up._on_change)
        self.up.items[0]["runtimeSec"] = 495 * 3600  # it has run 495 hours
        self.assertEqual(self.up.due_status(self.up.items[0])["state"], "soon")  # within 10 h
        await self.hub.update("hk.thermostat", attrs={"action": "heating"})
        await asyncio.sleep(0)
        self.assertIn(it["id"], self.up._running_since)
        self.up._running_since[it["id"]] -= 6 * 3600  # six hours of heating later
        await self.hub.update("hk.thermostat", attrs={"action": "idle"})
        await asyncio.sleep(0)
        self.assertEqual(self.up.due_status(self.up.items[0])["state"], "due")
        self.assertAlmostEqual(self.up.items[0]["runtimeSec"] / 3600, 501, places=0)
        self.up._last_state[it["id"]] = "soon"
        await self.up.publish_all()
        await asyncio.sleep(0)
        self.assertIn(("upkeep." + it["id"], "due"), self.events)  # automations can notify


class TodayTests(unittest.TestCase):
    def setUp(self):
        self.hub = Hub()
        self.now = datetime(2026, 10, 2, 18, 30)

    async def _fill(self):
        h = self.hub
        h.upsert("upkeep.oil", kind="upkeep", name="Change oil · BMW X5", integration="upkeep")
        await h.update("upkeep.oil", state="overdue", attrs={"daysLeft": -3, "ownerId": "p_chris", "owner": "Chris", "due": "2026-09-29"})
        h.upsert("upkeep.salt", kind="upkeep", name="Softener salt", integration="upkeep")
        await h.update("upkeep.salt", state="due", attrs={"daysLeft": 0, "ownerId": "", "askWho": True, "due": "2026-10-02"})
        h.upsert("upkeep.filter", kind="upkeep", name="Furnace filter", integration="upkeep")
        await h.update("upkeep.filter", state="soon", attrs={"daysLeft": 4, "ownerId": ""})
        h.upsert("upkeep.recycle", kind="upkeep", name="Recycling bins", integration="upkeep")
        await h.update("upkeep.recycle", state="soon", attrs={"daysLeft": 1, "eveningBefore": True, "due": "2026-10-03"})
        h.upsert("ring.keypad", kind="binary", name="Hallway Keypad", integration="ring")
        await h.update("ring.keypad", state="off", attrs={"acStatus": "error"})
        h.upsert("hc.dish", kind="appliance", name="Dishwasher", integration="hc")
        await h.update("hc.dish", state="off", attrs={"needs": ["rinse aid nearly empty"]})
        h.upsert("cal.family", kind="calendar", name="Family", integration="calendar")
        await h.update("cal.family", state="free", attrs={"colour": "#3CCFB4", "upcoming": [
            {"title": "Dentist · Massimo", "start": "2026-10-02T09:30", "end": "2026-10-02T10:30", "allDay": False, "location": "Main St."},
            {"title": "Pizza night", "start": "2026-10-02T18:00", "end": "2026-10-02T20:00", "allDay": False, "location": ""},
            {"title": "Soccer", "start": "2026-10-03T10:00", "end": "2026-10-03T11:00", "allDay": False, "location": ""}]})
        h.upsert("cal.garbage", kind="calendar", name="Garbage", integration="calendar")
        await h.update("cal.garbage", state="free", attrs={"headsUp": "evening", "upcoming": [
            {"title": "Garbage Day", "start": "2026-10-03T00:00", "end": "2026-10-04T00:00", "allDay": True, "location": ""}]})
        h.upsert("wx.home", kind="weather", name="Home", integration="weather")
        await h.update("wx.home", state="rain", attrs={"alerts": [{"id": "a1", "name": "Rainfall warning", "type": "warning"}],
                                                       "hourly": [{"time": "2026-10-02T18:00", "precipChance": 10},
                                                                  {"time": "2026-10-02T20:00", "precipChance": 70, "rain": 2}]})

    def test_briefing(self):
        asyncio.run(self._fill())
        b = T.build(self.hub, {}, now=self.now)
        self.assertEqual([t["title"] for t in b["todo"]],
                         ["Change oil · BMW X5", "Hallway Keypad: no ac power", "Softener salt",
                          "Dishwasher: rinse aid nearly empty"])
        self.assertEqual([t["action"] for t in b["todo"]], ["done", "open", "done", "dismiss"])
        oil, salt = b["todo"][0], b["todo"][2]
        self.assertEqual((oil["status"], oil["owner"], oil["askWho"]), ("3 days late", "Chris", False))
        self.assertTrue(salt["askWho"])  # everyone's: the wall asks who did it
        self.assertEqual([s["title"] for s in b["soon"]], ["Recycling bins", "Furnace filter"])
        self.assertEqual([e["title"] for e in b["events"]], ["Pizza night"])  # the dentist was this morning
        heads = [x["text"] for x in b["headsUp"]]
        self.assertEqual(heads[0], "Rainfall warning")
        self.assertIn("Recycling bins is due tomorrow", heads)
        self.assertIn("Garbage Day tomorrow", heads)
        self.assertIn("Rain likely from 8 PM (70%)", heads)
        # Mornings: no "tomorrow" heads-ups yet, and the dentist still shows.
        morning = T.build(self.hub, {}, now=datetime(2026, 10, 2, 7, 12))
        self.assertNotIn("Garbage Day tomorrow", [x["text"] for x in morning["headsUp"]])
        self.assertEqual([e["title"] for e in morning["events"]], ["Dentist · Massimo", "Pizza night"])
        # On Marlina's phone: not Chris's oil change. A dismissed appliance reminder stays hidden.
        mine = T.build(self.hub, {}, person_id="p_marlina", dismissed={"hc.dish:need:rinse aid nearly empty": 1},
                       now=self.now)
        self.assertEqual([t["title"] for t in mine["todo"]], ["Hallway Keypad: no ac power", "Softener salt"])
        # Rain threshold off.
        self.assertFalse(any("Rain likely" in x["text"] for x in T.build(self.hub, {"todayRainThreshold": 0}, now=self.now)["headsUp"]))


class UpkeepApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        await self.client.post("/api/users", json={"username": "marlina", "name": "Marlina", "role": "family", "password": "another pass"})
        await self.client.post("/api/users", json={"username": "hallway", "name": "Hallway tablet", "role": "tablet", "password": "wall tablet pass"})

    async def asyncTearDown(self):
        await self.h.integrations.stop_all()
        await self.client.close()
        self.tmp.cleanup()

    async def sign_in(self, username, password, kind):
        c = Browser(self.client)
        r = await c.post("/api/auth/login", json={"username": username, "password": password, "kind": kind})
        return c, {"Authorization": "Bearer " + (await r.json())["token"]} if kind != "browser" else {}

    async def test_add_mark_done_from_the_wall_and_today(self):
        last = (date.today() - timedelta(days=31)).isoformat()
        r = await self.client.post("/api/upkeep", json={"name": "Softener salt", "every": 1, "unit": "months", "lastDone": last})
        self.assertEqual(r.status, 200, await r.text())
        salt = await r.json()
        self.assertEqual(salt["state"], "overdue")
        self.assertIn("upkeep", [i["type"] for i in self.h.integrations.list()])  # made on first use
        r = await self.client.post("/api/upkeep", json={"name": "Change oil", "every": 6, "unit": "months", "owner": "nobody"})
        self.assertEqual(r.status, 200)

        # The wall: the household name tapped is recorded.
        wall, hdr = await self.sign_in("hallway", "wall tablet pass", "tablet")
        today = await (await wall.get("/api/today", headers=hdr)).json()
        self.assertEqual([t["title"] for t in today["todo"]], ["Softener salt"])
        self.assertTrue(today["todo"][0]["askWho"])
        r = await wall.post(f"/api/upkeep/{salt['id']}/done", json={"who": "Marlina"}, headers=hdr)
        self.assertEqual((await r.json())["lastDoneBy"], "Marlina")
        r = await wall.post(f"/api/upkeep/{salt['id']}/undo", headers=hdr)
        self.assertEqual((await r.json())["state"], "overdue")
        r = await wall.post(f"/api/upkeep/{salt['id']}/done", json={}, headers=hdr)  # skipped "who?"
        self.assertEqual((await r.json())["lastDoneBy"], "Hallway tablet")
        self.assertEqual((await wall.post("/api/upkeep", json={"name": "x", "every": 1}, headers=hdr)).status, 403)
        await wall.close()

        # Marlina's phone: she is who did it, whatever it says. She can't add items.
        phone, hdr = await self.sign_in("marlina", "another pass", "app")
        await phone.post(f"/api/upkeep/{salt['id']}/undo", headers=hdr)
        r = await phone.post(f"/api/upkeep/{salt['id']}/done", json={"who": "Chris"}, headers=hdr)
        self.assertEqual((await r.json())["lastDoneBy"], "Marlina")
        self.assertEqual((await phone.post("/api/upkeep", json={"name": "x", "every": 1}, headers=hdr)).status, 403)
        listing = await (await phone.get("/api/upkeep", headers=hdr)).json()
        self.assertEqual({i["name"] for i in listing["items"]}, {"Softener salt", "Change oil"})
        self.assertIn("Marlina", [p["name"] for p in listing["people"]])
        await phone.close()

        r = await self.client.patch("/api/today/settings", json={"todayRainThreshold": 40, "todayEveningFrom": "18:30"})
        self.assertEqual(await r.json(), {"todayRainThreshold": 40, "todayEveningFrom": "18:30"})
