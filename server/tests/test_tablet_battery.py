"""Wall tablets report their own battery (POST /api/tablet/battery): one device per tablet account that
automations (charge between 20 and 80 %) and dashboards use like any other; offline after 15 minutes
(screen on), 35 minutes (screen off, still checking in) or 12 hours (screen off, no background reports)."""
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
from newman.integrations.tablets import OFFLINE_AFTER_S, SCREEN_OFF_AFTER_S, SCREEN_OFF_SILENT_AFTER_S  # noqa: E402
from newman.kinds import KINDS, properties_for  # noqa: E402
from test_auth import Browser, FakeLocks, H  # noqa: E402

GOOD = {"level": 54, "charging": True, "plugged": "ac", "temperatureC": 31.46, "voltageMv": 4012}


class TabletBatteryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", self.data)
        self.h = H(self.data)
        self.plugs = FakeLocks(self.h.integrations._ctx)
        self.h.integrations.instances["fake"] = self.plugs
        self.h.hub.upsert("fake.plug", kind="switch", name="Tablet charger", integration="fake")
        await self.h.hub.update("fake.plug", state="off")
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        r = await self.client.post("/api/users", json={"username": "kitchen", "name": "Kitchen tablet", "role": "tablet",
                                                       "password": "wall tablet pass"})
        self.tablet = await r.json()
        self.tab = Browser(self.client)
        r = await self.tab.post("/api/auth/login", json={"username": "kitchen", "password": "wall tablet pass", "kind": "tablet"})
        self.auth = {"Authorization": "Bearer " + (await r.json())["token"]}
        self.eid = "tablets." + self.tablet["id"]

    async def asyncTearDown(self):
        await self.tab.close()
        await self.client.close()
        self.tmp.cleanup()

    async def report(self, body, **kw):
        return await self.tab.post("/api/tablet/battery", json=body, headers=kw.get("headers", self.auth))

    async def test_a_report_makes_one_device_per_tablet_and_updates_it(self):
        r = await self.report(GOOD)
        self.assertEqual(r.status, 204)
        e = self.h.hub.get(self.eid)
        self.assertEqual((e.kind, e.name, e.state, e.available), ("tablet", "Kitchen tablet", "charging", True))
        props = {p["id"]: p for p in properties_for(e)}
        self.assertEqual((props["battery"]["value"], props["battery"]["unit"]), (54, "%"))
        self.assertEqual((props["temperature"]["value"], props["temperature"]["unit"]), (31.5, "°C"))
        self.assertEqual((props["voltage"]["value"], props["voltage"]["unit"]), (4.01, "V"))
        self.assertEqual(props["plugged"]["value"], "ac")
        self.assertEqual(e.health_issues, [])

        r = await self.report({"level": 19, "charging": False, "plugged": "none", "temperatureC": None, "voltageMv": None})
        self.assertEqual(r.status, 204)
        e = self.h.hub.get(self.eid)
        self.assertEqual((e.state, e.attrs["batteryLevel"]), ("battery", 19))
        self.assertIn("Battery 19%", e.health_issues)  # low and not charging: needs attention
        self.assertEqual(len(self.h.hub.all("tablet")), 1)
        # It shows like any device, from its own connection.
        ents = await (await self.client.get("/api/entities")).json()
        dev = next(x for x in ents if x["id"] == self.eid)
        self.assertEqual((dev["integrationName"], dev["stateLabel"]), ("Wall tablets", "On battery"))

    async def test_renaming_the_account_renames_its_device_straight_away(self):
        await self.report(GOOD)
        r = await self.client.patch("/api/users/" + self.tablet["id"], json={"name": "Kitchen Samsung"})
        self.assertEqual(r.status, 200)
        self.assertEqual(self.h.hub.get(self.eid).name, "Kitchen Samsung")  # not at its next report
        await self.report(GOOD)
        self.assertEqual(self.h.hub.get(self.eid).name, "Kitchen Samsung")
        # A name someone gave the device itself is left alone.
        self.h.store.rename_entity(self.eid, "Hallway battery")
        self.h.hub.set_name_overrides(self.h.store.entity_names())
        await self.h.hub.update(self.eid, name="Hallway battery")
        await self.client.patch("/api/users/" + self.tablet["id"], json={"name": "Fire tablet"})
        self.assertEqual(self.h.hub.get(self.eid).name, "Hallway battery")

    async def test_the_sign_in_name_can_change(self):
        r = await self.client.patch("/api/users/" + self.tablet["id"], json={"username": "Kitchen"})
        self.assertEqual(r.status, 200)
        self.assertEqual((await r.json())["username"], "kitchen")
        self.assertEqual((await self.report(GOOD)).status, 204)  # still signed in
        r = await self.tab.post("/api/auth/login", json={"username": "kitchen", "password": "wall tablet pass", "kind": "tablet"})
        self.assertEqual(r.status, 200)
        self.assertEqual((await self.client.patch("/api/users/" + self.tablet["id"], json={"username": "chris"})).status, 409)
        self.assertEqual((await self.client.patch("/api/users/" + self.tablet["id"], json={"username": "a b"})).status, 400)

    async def test_battery_readings_start_automations(self):
        await self.report({**GOOD, "level": 25, "charging": False})  # the device exists once it has reported
        self.h.engine.save({
            "name": "Charge the tablet", "recipients": [], "notify": {"enabled": False},
            "trigger": {"items": [{"type": "property", "entities": [self.eid], "property": "battery", "op": "below",
                                   "value": 20}]},
            "actions": [{"entities": ["fake.plug"], "command": "turn_on"}]})
        self.h.engine.save({
            "name": "Stop charging", "recipients": [], "notify": {"enabled": False},
            "trigger": {"items": [{"type": "property", "entities": [self.eid], "property": "battery", "op": "above",
                                   "value": 80}]},
            "actions": [{"entities": ["fake.plug"], "command": "turn_off"}]})
        await self.report({**GOOD, "level": 22, "charging": False})
        await asyncio.sleep(0.05)
        self.assertEqual(self.plugs.calls, [])
        await self.report({**GOOD, "level": 19, "charging": False})
        await asyncio.sleep(0.05)
        self.assertEqual(self.plugs.calls, [("fake.plug", "turn_on")])
        await self.h.hub.update("fake.plug", state="on")  # the plug did it
        await self.report({**GOOD, "level": 81, "charging": True})
        await asyncio.sleep(0.05)
        self.assertEqual(self.plugs.calls, [("fake.plug", "turn_on"), ("fake.plug", "turn_off")])

    async def test_a_bad_report_is_refused(self):
        for bad in ({"level": 150, "charging": True}, {"level": 50, "charging": "yes"}, {"charging": True},
                    {"level": 50, "charging": True, "plugged": "mains"},
                    {"level": 50, "charging": True, "temperatureC": "hot"},
                    {"level": 50, "charging": True, "screen": "dim"}, {"level": 50, "charging": True, "screen": False},
                    {"level": 50, "charging": True, "background": "yes"}, [1, 2]):
            r = await self.report(bad)
            self.assertEqual(r.status, 400, bad)
        r = await self.tab.post("/api/tablet/battery", data="not json", headers=self.auth)
        self.assertEqual(r.status, 400)
        self.assertIsNone(self.h.hub.get(self.eid))

    async def test_the_wall_apps_cookie_session_is_enough(self):
        # The wall apps send the web view's session cookie, no Bearer token; a tablet account may report.
        r = await self.report(GOOD, headers={})
        self.assertEqual(r.status, 204)
        self.assertEqual(self.h.hub.get(self.eid).attrs["batteryLevel"], 54)
        # …and so may a family member's or an admin's session (a tablet signed in with one).
        await self.client.post("/api/users", json={"username": "dante", "name": "Dante", "role": "family",
                                                   "password": "another pass"})
        fam = Browser(self.client)
        r = await fam.post("/api/auth/login", json={"username": "dante", "password": "another pass", "kind": "browser"})
        self.assertEqual(r.status, 200)
        uid = (await r.json())["user"]["id"]
        self.assertEqual((await fam.post("/api/tablet/battery", json=GOOD)).status, 204)
        self.assertEqual((await self.client.post("/api/tablet/battery", json=GOOD)).status, 204)
        await fam.close()
        self.assertEqual(self.h.hub.get("tablets." + uid).name, "Dante")
        self.assertEqual(len(self.h.hub.all("tablet")), 3)
        # Still nothing else: a tablet can't change settings.
        self.assertEqual((await self.tab.post("/api/automations", json={})).status, 403)

    async def test_signed_out_is_refused(self):
        anon = Browser(self.client)
        r = await anon.post("/api/tablet/battery", json=GOOD)
        self.assertEqual(r.status, 401)
        await anon.close()
        self.assertIsNone(self.h.hub.get(self.eid))

    async def test_offline_after_15_minutes_and_back_after_a_restart(self):
        await self.report(GOOD)
        inst = next(i for i in self.h.integrations.instances.values() if i.type == "tablets")
        await inst.check_offline(now=time.time() + 14 * 60)
        self.assertTrue(self.h.hub.get(self.eid).available)
        await inst.check_offline(now=time.time() + 16 * 60)
        self.assertFalse(self.h.hub.get(self.eid).available)
        await self.report({**GOOD, "level": 60})
        self.assertTrue(self.h.hub.get(self.eid).available)

        # A restart: the tablet is back at once with its last values (offline, as that report is old).
        f = self.data / "integrations" / inst.id / "tablets.json"
        saved = json.loads(f.read_text())
        saved[self.tablet["id"]]["at"] -= 20 * 60
        f.write_text(json.dumps(saved))
        h2 = H(self.data)
        h2.integrations.start_all()
        for _ in range(50):
            if h2.hub.get(self.eid):
                break
            await asyncio.sleep(0.02)
        e = h2.hub.get(self.eid)
        self.assertEqual((e.name, e.state, e.attrs["batteryLevel"], e.available), ("Kitchen tablet", "charging", 60, False))
        await h2.integrations.stop_all()

    def inst(self):
        return next(i for i in self.h.integrations.instances.values() if i.type == "tablets")

    async def label(self):
        ents = await (await self.client.get("/api/entities")).json()
        return next(x for x in ents if x["id"] == self.eid)["stateLabel"]

    async def test_screen_off_shows_as_such_not_offline(self):
        self.assertEqual((OFFLINE_AFTER_S, SCREEN_OFF_AFTER_S, SCREEN_OFF_SILENT_AFTER_S), (900, 2100, 43200))
        await self.report({**GOOD, "screen": "on", "background": True})
        self.assertEqual(await self.label(), "Charging")
        await self.report({**GOOD, "charging": False, "plugged": "none", "screen": "off", "background": True})
        e = self.h.hub.get(self.eid)
        self.assertEqual((e.state, e.available, e.attrs["screen"]), ("battery", True, "off"))
        self.assertEqual(await self.label(), "Screen off")  # the state stays "battery" for automations
        props = {p["id"]: p for p in properties_for(e)}
        self.assertEqual(props["screen"]["value"], "off")
        self.assertNotIn("batteryStale", props)
        self.assertNotIn("backgroundReports", props)
        meta = KINDS["tablet"].to_json()
        self.assertEqual(meta["notes"], [{"attr": "screen", "value": "off", "label": "Screen off"}])
        # On again: its usual label.
        await self.report({**GOOD, "screen": "on", "background": True})
        self.assertEqual(await self.label(), "Charging")
        # An older app that doesn't say: screen on.
        await self.report(GOOD)
        self.assertEqual(self.h.hub.get(self.eid).attrs["screen"], "on")

    async def test_screen_off_with_background_reports_is_offline_after_35_minutes(self):
        await self.report({**GOOD, "screen": "off", "background": True})
        now = time.time()
        await self.inst().check_offline(now=now + 20 * 60)  # past 15 minutes: fine, screen off
        e = self.h.hub.get(self.eid)
        self.assertTrue(e.available)
        self.assertFalse(e.attrs["batteryStale"])  # it checks in: its battery isn't in doubt
        await self.inst().check_offline(now=now + 34 * 60)
        self.assertTrue(self.h.hub.get(self.eid).available)
        await self.inst().check_offline(now=now + 36 * 60)
        self.assertFalse(self.h.hub.get(self.eid).available)
        await self.report({**GOOD, "screen": "off", "background": True})  # a late check-in brings it back
        self.assertTrue(self.h.hub.get(self.eid).available)

    async def test_an_announced_next_check_in_says_how_long_to_wait(self):
        # The wall says when its next check-in is due: asleep until then plus 20 minutes' grace (never
        # less than the usual 35).
        await self.report({**GOOD, "screen": "off", "background": True, "nextReportIn": 900, "dozeExempt": False})
        e = self.h.hub.get(self.eid)
        self.assertEqual((e.attrs["nextReportIn"], e.attrs["dozeExempt"]), (900, False))
        self.assertAlmostEqual(e.attrs["nextReportAt"], time.time() + 900, delta=5)
        props = {p["id"] for p in properties_for(e)}
        self.assertFalse(props & {"nextReportIn", "nextReportAt", "dozeExempt"})  # details, not automation fodder
        now = time.time()
        self.assertTrue(self.inst().tablet_asleep(self.tablet["id"], now=now + 30 * 60))
        await self.inst().check_offline(now=now + 34 * 60)
        self.assertTrue(self.h.hub.get(self.eid).available)
        await self.inst().check_offline(now=now + 36 * 60)
        self.assertFalse(self.h.hub.get(self.eid).available)
        self.assertFalse(self.inst().tablet_asleep(self.tablet["id"], now=now + 36 * 60))
        # A later check-in (an inexact alarm, say 50 minutes away): offline only after 70.
        await self.report({**GOOD, "screen": "off", "background": True, "nextReportIn": 3000})
        now = time.time()
        await self.inst().check_offline(now=now + 69 * 60)
        self.assertTrue(self.h.hub.get(self.eid).available)
        await self.inst().check_offline(now=now + 71 * 60)
        self.assertFalse(self.h.hub.get(self.eid).available)
        # A report without one (an older app, or not armed) forgets the old announcement.
        await self.report({**GOOD, "screen": "off", "background": True})
        e = self.h.hub.get(self.eid)
        self.assertEqual((e.attrs["nextReportIn"], e.attrs["nextReportAt"]), (None, None))
        # Screen on / no background reports: never "asleep".
        await self.report({**GOOD, "screen": "on", "background": True, "nextReportIn": 900})
        self.assertFalse(self.inst().tablet_asleep(self.tablet["id"]))
        await self.report({**GOOD, "screen": "off", "background": False})
        self.assertFalse(self.inst().tablet_asleep(self.tablet["id"]))
        for bad in ({**GOOD, "nextReportIn": -1}, {**GOOD, "nextReportIn": "soon"}, {**GOOD, "nextReportIn": True},
                    {**GOOD, "dozeExempt": "yes"}):
            self.assertEqual((await self.report(bad)).status, 400, bad)

    async def test_screen_off_without_background_reports_stays_screen_off_for_12_hours(self):
        # An iPad: a last report as it goes to sleep, then nothing until it's woken.
        await self.report({**GOOD, "screen": "off", "background": False})
        now = time.time()
        await self.inst().check_offline(now=now + 10 * 60)
        self.assertFalse(self.h.hub.get(self.eid).attrs["batteryStale"])
        await self.inst().check_offline(now=now + 16 * 60)
        e = self.h.hub.get(self.eid)
        self.assertEqual((e.available, e.attrs["batteryStale"]), (True, True))  # battery unknown now
        self.assertEqual(await self.label(), "Screen off")
        await self.inst().check_offline(now=now + 11.9 * 3600)
        self.assertTrue(self.h.hub.get(self.eid).available)
        await self.inst().check_offline(now=now + 12.1 * 3600)
        self.assertFalse(self.h.hub.get(self.eid).available)
        # Woken: on again, its battery known again.
        await self.report({**GOOD, "screen": "on"})
        e = self.h.hub.get(self.eid)
        self.assertEqual((e.available, e.attrs["batteryStale"], e.attrs["screen"]), (True, False, "on"))

    async def test_after_a_restart_a_sleeping_ipad_is_screen_off_with_an_unknown_battery(self):
        await self.report({**GOOD, "screen": "off"})
        inst = self.inst()
        f = self.data / "integrations" / inst.id / "tablets.json"
        saved = json.loads(f.read_text())
        saved[self.tablet["id"]]["at"] -= 2 * 3600
        f.write_text(json.dumps(saved))
        h2 = H(self.data)
        h2.integrations.start_all()
        for _ in range(50):
            if h2.hub.get(self.eid):
                break
            await asyncio.sleep(0.02)
        e = h2.hub.get(self.eid)
        self.assertEqual((e.available, e.attrs["screen"], e.attrs["batteryStale"]), (True, "off", True))
        await h2.integrations.stop_all()
