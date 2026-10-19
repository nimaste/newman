"""The phone redesign's hub side: house settings, a device's warnings, groups and rooms, who may change
automations, "Don't repeat for", camera alerts for the phone, quiet hours and alert ids on notifications,
and GET /api/home."""
import asyncio
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman import controls, house as HS, quiet as Q, state  # noqa: E402
from newman.api import build_app  # noqa: E402
from newman.automations import engine as engine_mod  # noqa: E402
from newman.config import config  # noqa: E402
from newman.integrations import lutron  # noqa: E402
from newman.integrations.base import Integration  # noqa: E402
from newman.kinds import KINDS  # noqa: E402
from newman.push.notifier import Notification  # noqa: E402
from newman.store import Phone  # noqa: E402
from test_auth import Browser, H  # noqa: E402


class FakeLights(Integration):
    type = "fake"
    name = "Fake"

    def __init__(self, ctx, hub):
        super().__init__("fake", "Fake", {}, ctx)
        self.calls, self._hub = [], hub

    async def command(self, eid, command, value=None):
        self.calls.append((eid, command, value))
        if command in ("turn_on", "set_brightness"):
            await self._hub.update(eid, state="on", attrs={"brightness": value} if value is not None else None)
        elif command == "turn_off":
            await self._hub.update(eid, state="off")

    def has_alerts(self, entity_id):
        return entity_id in (None, "fake.cam")

    async def alerts(self, entity_id, limit, before):
        return [{"id": "r1", "start": time.time() - 60, "objects": ["person"], "zones": ["Driveway"],
                 "snapshotUrl": "api/cameras/fake/reviews/r1/snapshot.jpg", "clip": "frig:r:r1"}]


def reset_globals():
    state.BATTERY_LOW.update(default=20, devices={})
    engine_mod.RAMP_FULL_S = 3.5
    controls.STEP = 10
    lutron.HOLD_S = 0.5


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        hub = self.h.hub
        self.fake = FakeLights(self.h.integrations._ctx, hub)
        self.h.integrations.instances["fake"] = self.fake
        for eid, name in (("fake.a", "Under Cabinet - Coffee"), ("fake.b", "Under Cabinet - Kitchen Work"),
                          ("fake.c", "Kitchen")):
            hub.upsert(eid, kind="light", name=name, integration="fake")
            await hub.update(eid, state="off", attrs={"dimmable": True, "brightness": 0, "area": "Kitchen" if eid == "fake.c" else None})
        hub.upsert("fake.plug", kind="switch", name="Plug", integration="fake")
        await hub.update("fake.plug", state="off")
        hub.upsert("fake.garage", kind="garage", name="Garage Door", integration="fake")
        await hub.update("fake.garage", state="closed")
        hub.upsert("fake.side", kind="lock", name="Side Door", integration="fake")
        await hub.update("fake.side", state="LOCKED", attrs={"batteryLevel": 30, "acStatus": "error"})
        hub.upsert("fake.cam", kind="camera", name="Garage", integration="fake")
        await hub.update("fake.cam", state="online")
        hub.upsert("fake.t", kind="temperature", name="Temp Sensor - Upper Floor", integration="fake")
        await hub.update("fake.t", state="22.2")
        self.app = build_app(self.h)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        r = await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        self.assertEqual(r.status, 200)
        self.admin_id = (await r.json())["user"]["id"]

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()
        reset_globals()

    async def family(self, can=None):
        r = await self.client.post("/api/users", json={"username": "marlina", "name": "Marlina", "role": "family",
                                                       "password": "another pass"})
        uid = (await r.json())["id"]
        if can is not None:
            r = await self.client.patch(f"/api/users/{uid}", json={"canAutomate": can})
            self.assertEqual(r.status, 200, await r.text())
        c = Browser(self.client)
        await c.post("/api/auth/login", json={"username": "marlina", "password": "another pass"})
        return c, uid


class HouseSettingsTests(Base):
    async def test_defaults_patch_and_where_they_apply(self):
        s = await (await self.client.get("/api/settings/house")).json()
        self.assertEqual((s["devices"]["batteryLow"], s["lights"]["step"], s["cameras"]["clipDays"],
                          s["history"]["runDays"]), (20, 10, 3, 90))
        r = await self.client.patch("/api/settings/house", json={"devices": {"batteryLow": 35}, "lights": {
            "rampSeconds": 5, "step": 5, "holdSeconds": 0.8}, "history": {"activityDays": 30, "runDays": 14},
            "cameras": {"clipDays": 1}})
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual((state.BATTERY_LOW["default"], engine_mod.RAMP_FULL_S, controls.STEP, lutron.HOLD_S),
                         (35, 5.0, 5, 0.8))
        self.assertEqual((self.h.store.event_days, self.h.store.run_days, self.h.house.clip_days()), (30, 14, 1))
        # Battery 30% now counts as low on the lock (35% house level).
        ent = await (await self.client.get("/api/entities")).json()
        side = next(e for e in ent if e["id"] == "fake.side")
        self.assertIn("Battery 30%", side["health"]["issues"])
        # Only what differs from the defaults is saved.
        self.assertEqual(self.h.store.settings()["house"]["devices"], {"batteryLow": 35})

    async def test_refusals(self):
        for bad in ({"lights": {"step": 7}}, {"lights": {"rampSeconds": 9}}, {"history": {"runDays": 120}},
                    {"cameras": {"mode": "featured"}}, {"cameras": {"featured": "fake.a"}}, {"nope": {}},
                    {"climate": {"range": [26, 18]}}, {"climate": {"bands": [{"name": "x", "color": "red"}]}},
                    {"devices": {"leftOpenMinutes": 0}}, {"devices": {"leftOpenMinutes": 241}},
                    {"devices": {"leftOpenMinutes": 2.5}}):
            r = await self.client.patch("/api/settings/house", json=bad)
            self.assertEqual(r.status, 400, bad)
        r = await self.client.patch("/api/settings/house", json={"cameras": {"mode": "featured", "featured": "fake.cam"}})
        self.assertEqual(r.status, 200, await r.text())
        fam, _ = await self.family()
        self.assertEqual((await fam.patch("/api/settings/house", json={"lights": {"step": 5}})).status, 403)
        self.assertEqual((await fam.get("/api/settings/house")).status, 200)
        await fam.close()


class WarningsTests(Base):
    async def test_own_battery_level_and_switched_off_problems(self):
        w = await (await self.client.get("/api/entities/fake.side/warnings")).json()
        ids = {x["id"]: x["on"] for x in w["warnings"]}
        self.assertEqual((w["batteryLow"], w["houseBatteryLow"], w["hasBattery"]), (None, 20, True))
        self.assertEqual((w["leftOpenMinutes"], w["houseLeftOpenMinutes"], w["hasSecureState"]), (None, 15, True))
        self.assertTrue(ids["no_ac"] and ids["battery_low"] and ids["offline"])
        ents = {e["id"]: e for e in await (await self.client.get("/api/entities")).json()}
        self.assertEqual(ents["fake.side"]["health"]["issues"], ["No AC power"])
        r = await self.client.put("/api/entities/fake.side/warnings", json={"batteryLow": 35, "warnings": {"no_ac": False}})
        self.assertEqual(r.status, 200, await r.text())
        ents = {e["id"]: e for e in await (await self.client.get("/api/entities")).json()}
        h = ents["fake.side"]["health"]
        self.assertEqual(h["issues"], ["Battery 30%"])  # its own 35% level; no AC power switched off
        self.assertNotIn("no_ac", [i["id"] for i in h["items"]])
        today = await (await self.client.get("/api/today")).json()
        self.assertTrue(all("ac power" not in t["title"].lower() for t in today["todo"]))
        for bad in ({"batteryLow": 80}, {"warnings": {"jammed_door": False}}, {"warnings": {"no_ac": "off"}}):
            self.assertEqual((await self.client.put("/api/entities/fake.side/warnings", json=bad)).status, 400, bad)
        t = await (await self.client.get("/api/entities/fake.t/warnings")).json()
        self.assertFalse(t["hasSecureState"])
        r = await self.client.put("/api/entities/fake.t/warnings", json={"leftOpenMinutes": 10})
        self.assertEqual((r.status, (await r.json())["error"]), (400, "Temp Sensor - Upper Floor is never left open"))
        for bad in (0, 241, True):
            self.assertEqual((await self.client.put("/api/entities/fake.side/warnings",
                                                    json={"leftOpenMinutes": bad})).status, 400, bad)
        r = await self.client.put("/api/entities/fake.side/warnings", json={"leftOpenMinutes": 5})
        self.assertEqual((await r.json())["leftOpenMinutes"], 5)
        # Back to the house level and every warning on.
        await self.client.put("/api/entities/fake.side/warnings", json={"batteryLow": None, "leftOpenMinutes": None,
                                                                         "warnings": {"no_ac": True}})
        self.assertNotIn("fake.side", self.h.store.settings().get("deviceWarnings") or {})


class GroupsTests(Base):
    async def test_a_group_is_one_light_that_switches_its_members_together(self):
        r = await self.client.post("/api/groups", json={"name": "Kitchen Under Cabinet", "members": ["fake.a", "fake.b"],
                                                        "room": "Kitchen"})
        self.assertEqual(r.status, 200, await r.text())
        g = await r.json()
        self.assertEqual((g["id"], g["kind"], g["room"]), ("group.kitchen_under_cabinet", "light", "Kitchen"))
        gid = g["id"]
        r = await self.client.post(f"/api/entities/{gid}/command", json={"command": "set_brightness", "value": 40})
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual(sorted(c[0] for c in self.fake.calls), ["fake.a", "fake.b"])
        for _ in range(50):
            if self.h.hub.get(gid).state == "on":
                break
            await asyncio.sleep(0.02)
        grp = self.h.hub.get(gid)
        self.assertEqual((grp.state, grp.attrs["brightness"], grp.attrs["members"]), ("on", 40, ["fake.a", "fake.b"]))
        ents = {e["id"]: e for e in await (await self.client.get("/api/entities")).json()}
        self.assertEqual((ents["fake.a"]["group"], ents["fake.c"]["group"]), (gid, None))
        self.assertEqual((ents[gid]["room"], ents["fake.c"]["room"], ents["fake.a"]["room"]), ("Kitchen", "Kitchen", None))
        # A member can't be in two groups; only lights and switches.
        self.assertEqual((await self.client.post("/api/groups", json={"name": "X", "members": ["fake.a"]})).status, 400)
        self.assertEqual((await self.client.post("/api/groups", json={"name": "X", "members": ["fake.side"]})).status, 400)
        # Mixed with a plug: a switch.
        r = await self.client.put(f"/api/groups/{gid}", json={"members": ["fake.a", "fake.plug"]})
        self.assertEqual((r.status, (await r.json())["kind"]), (200, "switch"))
        # Removing a member device is "in use" by the group.
        await self.h.hub.update("fake.plug", available=False)
        r = await self.client.post("/api/entities/remove", json={"ids": ["fake.plug"], "dryRun": True})
        self.assertIn("group", [u["type"] for u in (await r.json())["devices"][0]["usedBy"]])
        self.assertEqual((await self.client.delete(f"/api/groups/{gid}")).status, 200)
        self.assertIsNone(self.h.hub.get(gid))

    async def test_hold_to_dim_moves_every_member(self):
        moved = []
        self.fake.can_move = lambda eid, attr: True
        async def move(eid, attr, direction, full_s):
            moved.append((eid, direction))
        self.fake.move = move
        g = await (await self.client.post("/api/groups", json={"name": "Pair", "members": ["fake.a", "fake.b"]})).json()
        gi = self.h.integrations.instances["groups"]
        self.assertTrue(gi.can_move(g["id"], "brightness"))
        await gi.move(g["id"], "brightness", 1, 3.5)
        self.assertEqual(sorted(moved), [("fake.a", 1), ("fake.b", 1)])

    async def test_rooms(self):
        r = await self.client.put("/api/entities/fake.a/room", json={"room": "Kitchen"})
        self.assertEqual((r.status, (await r.json())["room"]), (200, "Kitchen"))
        self.assertEqual(await (await self.client.get("/api/rooms")).json(), ["Kitchen"])
        r = await self.client.put("/api/entities/fake.a/room", json={"room": None})
        self.assertIsNone((await r.json())["room"])


class AutomationAccessTests(Base):
    AUTO = {"name": "Night light", "recipients": [], "notify": {"enabled": False},
            "trigger": {"type": "time", "time": "21:00"}, "actions": [{"entities": ["fake.c"], "command": "turn_on"}]}

    async def test_only_people_allowed_change_automations(self):
        a = await (await self.client.post("/api/automations", json=self.AUTO)).json()
        fam, uid = await self.family()
        st = await (await fam.get("/api/auth/state")).json()
        self.assertFalse(st["user"]["canAutomate"])
        r = await fam.post("/api/automations", json=self.AUTO)
        self.assertEqual((r.status, (await r.json())["error"]), (403, "Your account can't change automations"))
        self.assertEqual((await fam.patch(f"/api/automations/{a['id']}", json={"enabled": False})).status, 403)
        self.assertEqual((await fam.post(f"/api/automations/{a['id']}/run", json={})).status, 403)
        self.assertEqual((await fam.post("/api/automations/describe", json=self.AUTO)).status, 200)
        self.assertEqual((await fam.get("/api/automations")).status, 200)
        r = await self.client.patch(f"/api/users/{uid}", json={"canAutomate": True})
        self.assertTrue((await r.json())["canAutomate"])
        self.assertEqual((await fam.post("/api/automations", json=self.AUTO)).status, 200)
        self.assertEqual((await fam.patch(f"/api/automations/{a['id']}", json={"enabled": False})).status, 200)
        me = await (await self.client.get("/api/auth/state")).json()
        self.assertTrue(me["user"]["canAutomate"])  # admins always
        await fam.close()
        tab = await self.client.post("/api/users", json={"username": "hall", "name": "Hall", "role": "tablet",
                                                         "password": "wall tablet pass"})
        self.assertEqual((await self.client.patch(f"/api/users/{(await tab.json())['id']}",
                                                  json={"canAutomate": True})).status, 400)

    async def test_dont_repeat_for(self):
        auto = self.h.engine.save({**self.AUTO, "cooldownMinutes": 5})
        first = await self.h.engine.trigger(auto, {"type": "time"})
        second = await self.h.engine.trigger(auto, {"type": "time"})
        self.assertNotEqual(first["status"], "skipped")
        self.assertEqual(second["status"], "skipped")
        self.assertIn("waits 5 minutes", second["log"][-1]["text"])
        # A camera detection with no own wait uses Settings > Cameras (1 minute by default); 0 = no wait.
        cam = self.h.engine.save({**self.AUTO, "name": "Person", "trigger": {"items": [
            {"type": "event", "entity": "fake.cam", "event": "person"}]}})
        trig = {"type": "event", "entity": "fake.cam", "event": "person"}
        self.assertEqual(self.h.engine.cooldown_minutes(cam, trig), 1)
        await self.h.engine.trigger(cam, trig)
        self.assertEqual((await self.h.engine.trigger(cam, trig))["status"], "skipped")
        none = self.h.engine.save({**cam, "id": None, "cooldownMinutes": 0})
        self.assertEqual(self.h.engine.cooldown_minutes(none, trig), 0)
        self.assertEqual((await self.client.post("/api/automations", json={**self.AUTO, "cooldownMinutes": -3})).status, 400)


class AlertsAndNotificationTests(Base):
    async def test_camera_alerts_for_the_phone(self):
        r = await self.client.get("/api/alerts?entity=fake.cam&limit=5")
        a = (await r.json())[0]
        self.assertEqual((a["label"], a["zone"], a["clip"]), ("Person", "Driveway", "api/clips/frig/r:r1"))
        self.assertEqual(len(await (await self.client.get(f"/api/alerts?entity=fake.cam&since={time.time()}")).json()), 0)
        self.h.store.log_event("command", "Side Door locked", "by hand", "fake.side")
        self.h.store.log_event("command", "Kitchen on", "", "fake.c")
        sec = await (await self.client.get(f"/api/alerts?category=security&since={time.time() - 60}")).json()
        self.assertEqual([x["title"] for x in sec], ["Side Door locked"])

    async def test_alert_id_and_quiet_hours(self):
        pid = self.h.store.people()[0]["id"]
        self.h.store.register_phone(Phone(device_id="test-1", token="t", platform="test", person_id=pid))
        n = Notification(kind="automation", title="Person at Garage", body="", alert_id="run_1", topic="people")
        await self.h.notifier.send(n, people=[pid])
        self.assertEqual((self.h.notifier.outbox[0]["alertId"], self.h.notifier.outbox[0].get("quiet")), ("run_1", None))
        r = await self.client.put("/api/me/quiet-hours", json={"enabled": True, "from": "00:00", "to": "23:59",
                                                               "days": "all", "allow": ["doorbell"]})
        self.assertEqual(r.status, 200, await r.text())
        await self.h.notifier.send(n, people=[pid])
        self.assertEqual(self.h.notifier.outbox[0].get("quiet"), "1")
        await self.h.notifier.send(Notification(kind="automation", title="Ding", body="", sound="doorbell.caf"), people=[pid])
        self.assertIsNone(self.h.notifier.outbox[0].get("quiet"))  # the doorbell still rings
        self.assertEqual((await self.client.put("/api/me/quiet-hours", json={"from": "25:00"})).status, 400)
        ev = self.h.store.log_event("automation", "Hi", "", None, {"runId": "run_9"})
        found = await (await self.client.get("/api/events?runId=run_9")).json()
        self.assertEqual([e["title"] for e in found], ["Hi"])

    def test_quiet_window_over_midnight(self):
        q = {"enabled": True, "from": "22:00", "to": "07:00", "days": "weekdays", "allow": []}
        self.assertTrue(Q.in_window(q, datetime(2026, 10, 19, 23, 0)))   # Monday night
        self.assertTrue(Q.in_window(q, datetime(2026, 10, 20, 6, 0)))    # Tuesday morning (Monday's window)
        self.assertFalse(Q.in_window(q, datetime(2026, 10, 19, 6, 0)))   # Monday morning = Sunday's window
        self.assertFalse(Q.in_window(q, datetime(2026, 10, 20, 12, 0)))


class HomeTests(Base):
    async def test_cards_status_favourites_and_climate(self):
        home = await (await self.client.get("/api/home")).json()
        cards = {c["id"]: c for c in home["cards"]}
        self.assertEqual(list(cards)[:4], ["cameras", "climate", "lights", "security"])
        self.assertEqual((cards["lights"]["value"], cards["security"]["value"], cards["today"]["value"]),
                         ("All off", "All secure", "Nothing today"))
        self.assertTrue(home["canAutomate"])
        self.assertEqual(home["status"]["level"], "warn")  # the lock's No AC power
        self.assertEqual(home["status"]["warnings"][0]["text"], "Side Door · No AC power")
        # The garage left open for 20 minutes needs you, with its fix.
        await self.h.hub.update("fake.garage", state="open")
        await asyncio.sleep(0.05)  # let the hub's listeners see the change first
        self.h.home._since["fake.garage"] = time.time() - 20 * 60
        home = await (await self.client.get("/api/home")).json()
        d = home["status"]["danger"][0]
        self.assertEqual(d["detail"][:15], "For 20 minutes ")
        self.assertEqual((home["status"]["level"], d["title"], d["action"]), ("danger", "Garage Door open",
                                                                               {"command": "close", "label": "Close"}))
        # Left open: the house's minutes (Settings > Devices), else the device's own.
        await self.client.patch("/api/settings/house", json={"devices": {"leftOpenMinutes": 30}})
        self.assertEqual((await (await self.client.get("/api/home")).json())["status"]["danger"], [])
        await self.client.put("/api/entities/fake.garage/warnings", json={"leftOpenMinutes": 10})
        self.assertEqual(len((await (await self.client.get("/api/home")).json())["status"]["danger"]), 1)
        await self.client.put("/api/entities/fake.garage/warnings", json={"leftOpenMinutes": None})
        await self.client.patch("/api/settings/house", json={"devices": {"leftOpenMinutes": 15}})
        r = await self.client.put("/api/home/me", json={"cards": [{"id": "lights", "on": True}, {"id": "cameras", "on": False}],
                                                        "favourites": ["fake.garage", "fake.side"]})
        home = await r.json()
        self.assertEqual([c["id"] for c in home["cards"]][:2], ["lights", "cameras"])
        self.assertFalse(home["cards"][1]["on"])
        self.assertEqual(home["favourites"], ["fake.garage", "fake.side"])
        self.assertEqual((await self.client.put("/api/home/me", json={"favourites": ["a", "b", "c", "d", "e"]})).status, 400)
        r = await self.client.put("/api/home/climate", json={"rooms": [{"entity": "fake.t", "name": "Upper Floor"}]})
        home = await r.json()
        self.assertEqual(home["climate"]["rooms"], [{"entity": "fake.t", "name": "Upper Floor", "value": 22.2}])
        self.assertEqual((await self.client.put("/api/home/climate", json={"rooms": [{"entity": "fake.c"}]})).status, 400)
        self.assertEqual(home["cameras"]["mode"], "latest")
        # Lights counts a group once.
        await self.client.post("/api/groups", json={"name": "Under Cabinet", "members": ["fake.a", "fake.b"]})
        home = await (await self.client.get("/api/home")).json()
        lights = next(c for c in home["cards"] if c["id"] == "lights")
        self.assertEqual(lights["detail"], "2 lights")  # the group and Kitchen

    async def test_frigate_keeps_its_custom_labels(self):
        from test_reconnect_refresh import FakeHttp
        from newman.integrations.frigate import FrigateIntegration
        f = FrigateIntegration("frigate", "Frigate", FrigateIntegration.migrate_config({"url": "http://frigate:5000"}),
                               self.h.integrations._ctx)
        await f._http.aclose()
        f._http = FakeHttp({"cameras": {"porch": {"objects": {"track": ["person", "fedex", "amazon"]}, "zones": {}}}})
        f.save_config = lambda c: None
        await f._load_config()
        cam = self.h.hub.get(f.entity_id("camera_porch"))
        self.assertEqual(cam.attrs["events"], ["person", "fedex", "amazon"])
        self.assertTrue({"fedex", "amazon"} <= f._labels)
        self.assertEqual(KINDS["camera"].event("amazon", cam.attrs).label, "Amazon detected")

    def test_custom_frigate_label_is_worded_like_the_others(self):
        ev = KINDS["camera"].event("fedex", {"events": ["person", "fedex"]})
        self.assertEqual(ev.label, "Fedex detected")
        self.assertEqual(KINDS["camera"].event("person", {"events": ["person"]}).label, "Person detected")


if __name__ == "__main__":
    unittest.main()
