"""Sprinkler controllers, offline: Hunter Hydrawise, Rain Bird and Rachio against fakes."""
import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs

import httpx

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.integrations import REGISTRY  # noqa: E402
from newman.integrations.base import CommandError, Context, mask_config  # noqa: E402
from newman.integrations.hydrawise import SUSPENDED, HydrawiseIntegration  # noqa: E402
from newman.integrations.rachio import RachioIntegration  # noqa: E402
from newman.integrations.rainbird import RainBirdIntegration  # noqa: E402
from newman.kinds import KINDS, properties_for, render  # noqa: E402
from newman.state import Hub  # noqa: E402


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        self.ctx = Context(self.hub, Path(self.tmp.name), Path(self.tmp.name))
        self.events = []

        async def on_event(ent, event, data):
            self.events.append((ent.id, event))
        self.hub.subscribe_events(on_event)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def settle(self):
        for _ in range(3):
            await asyncio.sleep(0)


class KindTests(unittest.TestCase):
    def test_kinds_and_registry(self):
        for t in ("hydrawise", "rainbird", "rachio"):
            self.assertIn(t, REGISTRY)
            self.assertEqual(REGISTRY[t].provides, ("sprinkler", "sprinkler_zone"))
        z = KINDS["sprinkler_zone"]
        run = z.command("run")
        self.assertEqual((run.value_type, run.unit, run.max), ("number", "min", 180))
        self.assertEqual(render(z.event("finished").body, "Front lawn", z, z.event("finished")),
                         "Front lawn finished watering")
        self.assertEqual(KINDS["sprinkler"].state_label("rain_delay"), "Rain delay")

    def test_rain_bird_password_is_masked_and_explained(self):
        masked = mask_config(RainBirdIntegration, {"host": "10.0.0.5", "password": "hunter2"})
        self.assertNotIn("hunter2", json.dumps(masked))
        self.assertIn("no tokens", RainBirdIntegration.description)


# -- Hydrawise --------------------------------------------------------------------------------

class FakeHydrawise:
    def __init__(self):
        self.calls = []
        self.running = False
        self.suspended = False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        q = {k: v[0] for k, v in parse_qs(req.url.query.decode()).items()}
        self.calls.append((req.url.path.rsplit("/", 1)[-1], q))
        if q.get("api_key") != "KEY":
            return httpx.Response(404, text="API key not valid")
        if req.url.path.endswith("customerdetails.php"):
            return httpx.Response(200, json={"customer_id": 7, "controllers": [
                {"controller_id": 11, "name": "Backyard", "serial_number": "HPC-1", "last_contact": 1790000000,
                 "status": "All good!"}]})
        if req.url.path.endswith("statusschedule.php"):
            sus = SUSPENDED if self.suspended else None
            relays = [{"relay_id": 501, "relay": 1, "name": "Front lawn", "time": 1 if self.running else (sus or 3600),
                       "run": 600},
                      {"relay_id": 502, "relay": 2, "name": "Garden", "time": sus or 7200, "run": 900}]
            body = {"nextpoll": 30, "relays": relays}
            if self.running:
                body["running"] = [{"relay_id": 501, "time_left": 420}]
            return httpx.Response(200, json=body)
        if req.url.path.endswith("setzone.php"):
            return httpx.Response(200, json={"message": "ok", "message_type": "info"})
        return httpx.Response(404)


class HydrawiseTests(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake = FakeHydrawise()
        self.hw = HydrawiseIntegration("hydrawise", "Hydrawise", {"api_key": "KEY"}, self.ctx)
        self.hw._http = httpx.AsyncClient(transport=httpx.MockTransport(self.fake))
        await self.hw._load()
        await self.hw.poll()

    async def test_reads_controllers_zones_and_next_runs(self):
        c = self.hub.get("hydrawise.controller_11")
        self.assertEqual((c.kind, c.name, c.state), ("sprinkler", "Backyard", "idle"))
        z = self.hub.get("hydrawise.zone_501")
        self.assertEqual((z.kind, z.name, z.state, z.attrs["scheduledMinutes"]), ("sprinkler_zone", "Front lawn", "idle", 10))
        self.assertTrue(z.attrs["nextRun"])
        self.assertEqual(c.attrs["nextRun"], z.attrs["nextRun"])  # the earliest zone
        self.assertEqual(self.hw.status, "connected")
        self.assertEqual(self.hw.detail, "1 controller, 2 zones")
        self.assertEqual(self.hw._next_poll, 60)  # never sooner than a minute, whatever nextpoll says

    async def test_watering_and_finishing_emit_events(self):
        self.fake.running = True
        await self.hw.poll()
        await self.settle()
        z, c = self.hub.get("hydrawise.zone_501"), self.hub.get("hydrawise.controller_11")
        self.assertEqual((z.state, z.attrs["remaining"]), ("watering", 7))
        self.assertEqual((c.state, c.attrs["currentZone"], c.attrs["remaining"]), ("watering", "Front lawn", 7))
        self.fake.running = False
        await self.hw.poll()
        await self.settle()
        z = self.hub.get("hydrawise.zone_501")
        self.assertEqual((z.state, z.attrs["remaining"]), ("idle", None))
        self.assertTrue(z.attrs["lastRun"])
        self.assertEqual(self.events, [("hydrawise.zone_501", "started"), ("hydrawise.zone_501", "finished")])
        p = {x["id"]: x for x in properties_for(z)}
        self.assertEqual(p["remaining"]["unit"], "min")

    async def test_commands(self):
        await self.hw.command("hydrawise.zone_501", "run", 15)
        self.assertEqual(self.fake.calls[-1], ("setzone.php", {"api_key": "KEY", "action": "run", "relay_id": "501",
                                                               "period_id": "999", "custom": "900"}))
        await self.hw.command("hydrawise.zone_501", "stop")
        self.assertEqual(self.fake.calls[-1][1]["action"], "stop")
        await self.hw.command("hydrawise.controller_11", "stop")
        self.assertEqual(self.fake.calls[-1][1]["action"], "stopall")
        await self.hw.command("hydrawise.controller_11", "rain_delay", 2)
        q = self.fake.calls[-1][1]
        self.assertEqual((q["action"], q["controller_id"]), ("suspendall", "11"))
        self.assertAlmostEqual(int(q["custom"]), time.time() + 2 * 86400, delta=5)
        self.fake.suspended = True
        await self.hw.poll()
        c = self.hub.get("hydrawise.controller_11")
        self.assertEqual(c.state, "rain_delay")
        self.assertTrue(c.attrs["rainDelayUntil"])
        await self.hw.command("hydrawise.controller_11", "turn_on")
        self.assertEqual((self.fake.calls[-1][1]["action"], self.fake.calls[-1][1]["period_id"]), ("suspendall", "0"))
        await self.hw.poll()
        self.assertEqual(self.hub.get("hydrawise.controller_11").state, "off")  # still suspended, no delay = standby
        with self.assertRaisesRegex(CommandError, "between 1 and 180"):
            await self.hw.command("hydrawise.zone_501", "run", 500)

    async def test_bad_key_never_leaks(self):
        self.hw.config = {"api_key": "WRONG"}
        with self.assertRaises(Exception) as cm:
            await self.hw.poll()
        self.assertIn("API key", str(cm.exception))
        self.assertNotIn("WRONG", str(cm.exception))


# -- Rain Bird --------------------------------------------------------------------------------

class FakeStates:
    def __init__(self, active):
        self._active = set(active)

    def active(self, n):
        return n in self._active

    @property
    def active_set(self):
        return set(self._active)


class FakeRainBird:
    def __init__(self):
        self.active = set()
        self.rain = False
        self.delay = 0
        self.calls = []
        self.busy = False

    async def _call(self, name, *a):
        assert not self.busy, "two requests at once"
        self.busy = True
        await asyncio.sleep(0)
        self.busy = False
        self.calls.append((name, *a))

    async def get_model_and_version(self):
        await self._call("model")
        return type("M", (), {"model_info": type("I", (), {"name": "ESP-TM2"})(), "major": 2, "minor": 9})()

    async def get_available_stations(self):
        await self._call("stations")
        return FakeStates({1, 2, 3})

    async def get_serial_number(self):
        await self._call("serial")
        return "0000000000000000"

    async def get_zone_states(self):
        await self._call("states")
        return FakeStates(self.active)

    async def get_rain_sensor_state(self):
        await self._call("rain")
        return self.rain

    async def get_rain_delay(self):
        await self._call("delay")
        return self.delay

    async def get_combined_controller_state(self):
        await self._call("combined")
        raise RuntimeError("not supported")

    async def irrigate_zone(self, zone, minutes):
        await self._call("irrigate", zone, minutes)
        self.active = {zone}

    async def stop_irrigation(self):
        await self._call("stop")
        self.active = set()

    async def set_rain_delay(self, days):
        await self._call("set_delay", days)
        self.delay = days


class RainBirdTests(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake = FakeRainBird()
        self.rb = RainBirdIntegration("rainbird", "Rain Bird", {"host": "10.0.0.5", "password": "pw"}, self.ctx)
        self.rb._ctl = self.fake
        await self.rb._setup()
        await self.rb.poll()

    async def test_zones_rain_sensor_and_model(self):
        c = self.hub.get("rainbird.controller")
        self.assertEqual((c.kind, c.name, c.state, c.attrs["rainSensor"]), ("sprinkler", "Rain Bird ESP-TM2", "idle", "dry"))
        self.assertEqual(c.attrs["serial"], "")  # all zeros = not reported
        self.assertEqual([self.hub.get(f"rainbird.zone_{n}").name for n in (1, 2, 3)], ["Zone 1", "Zone 2", "Zone 3"])
        self.assertEqual(self.rb.detail, "3 zones")
        self.assertFalse(self.rb._combined)  # unsupported request isn't asked again
        self.fake.rain, self.fake.delay = True, 2
        await self.rb.poll()
        c = self.hub.get("rainbird.controller")
        self.assertEqual((c.state, c.attrs["rainSensor"], c.attrs["rainDelayDays"]), ("rain_delay", "wet", 2))
        self.assertNotIn("combined", [x[0] for x in self.fake.calls[-3:]])

    async def test_run_stop_and_delay(self):
        await self.rb.command("rainbird.zone_2", "run", 12)
        self.assertEqual(self.fake.calls[-1], ("irrigate", 2, 12))
        await self.rb.poll()
        await self.settle()
        self.assertEqual(self.hub.get("rainbird.zone_2").state, "watering")
        self.assertEqual(self.hub.get("rainbird.controller").attrs["currentZone"], "Zone 2")
        await self.rb.command("rainbird.zone_2", "stop")
        await self.rb.poll()
        await self.settle()
        self.assertEqual(self.hub.get("rainbird.zone_2").state, "idle")
        self.assertEqual(self.events, [("rainbird.zone_2", "started"), ("rainbird.zone_2", "finished")])
        await self.rb.command("rainbird.controller", "rain_delay", 3)
        self.assertEqual(self.fake.calls[-1], ("set_delay", 3))
        with self.assertRaisesRegex(CommandError, "standby"):
            await self.rb.command("rainbird.controller", "turn_off")

    async def test_polls_and_commands_never_overlap(self):
        await asyncio.gather(self.rb.poll(), self.rb.command("rainbird.zone_1", "run", 5), self.rb.poll())


# -- Rachio -----------------------------------------------------------------------------------

DEV = "d1d1d1d1-0000-4000-8000-000000000001"
Z1, Z2 = "a1a1a1a1-0000-4000-8000-000000000001", "b2b2b2b2-0000-4000-8000-000000000002"


class FakeRachio:
    def __init__(self):
        self.calls = []
        self.current = {}
        self.on = True
        self.delay_until = 0
        self.hooks = [{"id": "old", "externalId": "newman:rachio"}]

    def device(self):
        return {"id": DEV, "name": "Rachio 3", "status": "ONLINE", "on": self.on, "model": "GENERATION3_8ZONE",
                "serialNumber": "R3-1", "rainDelayExpirationDate": self.delay_until,
                "zones": [{"id": Z1, "zoneNumber": 1, "name": "Front", "enabled": True, "lastWateredDate": 1790000000000},
                          {"id": Z2, "zoneNumber": 2, "name": "Side", "enabled": True},
                          {"id": "z3", "zoneNumber": 3, "name": "Unused", "enabled": False}]}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.replace("/1/public", "")
        body = json.loads(req.content) if req.content else None
        self.calls.append((req.method, path, body))
        if req.headers.get("Authorization") != "Bearer KEY":
            return httpx.Response(401, json={"code": "401", "message": "Unauthorized"})
        h = {"X-RateLimit-Remaining": "1500"}
        if path == "/person/info":
            return httpx.Response(200, json={"id": "p1"}, headers=h)
        if path == "/person/p1":
            return httpx.Response(200, json={"id": "p1", "devices": [self.device()]}, headers=h)
        if path == f"/device/{DEV}":
            return httpx.Response(200, json=self.device(), headers=h)
        if path == f"/device/{DEV}/current_schedule":
            return httpx.Response(200, json=self.current, headers=h)
        if path == f"/device/{DEV}/scheduleitem":
            return httpx.Response(200, json=[{"startDate": int((time.time() + 86400) * 1000)}], headers=h)
        if path == "/notification/webhook_event_type":
            return httpx.Response(200, json=[{"id": "5", "name": "DEVICE_STATUS_EVENT"},
                                             {"id": "10", "name": "ZONE_STATUS_EVENT"},
                                             {"id": "99", "name": "SOMETHING_ELSE"}], headers=h)
        if path == f"/notification/{DEV}/webhook":
            return httpx.Response(200, json=self.hooks, headers=h)
        if req.method in ("PUT", "POST", "DELETE"):
            if path == "/device/off":
                self.on = False
            elif path == "/device/on":
                self.on = True
            elif path == "/device/rain_delay":
                self.delay_until = int((time.time() + body["duration"]) * 1000) if body["duration"] else 0
            return httpx.Response(204, headers=h)
        return httpx.Response(404, json={"message": "nope"})


class RachioTests(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake = FakeRachio()
        self.ra = RachioIntegration("rachio", "Rachio", {"api_key": "KEY"}, self.ctx)
        self.ra._http = httpx.AsyncClient(transport=httpx.MockTransport(self.fake))
        self.ra._person = "p1"
        await self.ra._load()
        await self.ra._next_runs()
        await self.ra.refresh(DEV)

    async def test_reads_devices_and_zones(self):
        c = self.hub.get("rachio.controller_d1d1d1d1")
        self.assertEqual((c.kind, c.name, c.state, c.attrs["serial"]), ("sprinkler", "Rachio 3", "idle", "R3-1"))
        self.assertTrue(c.attrs["nextRun"])
        self.assertEqual(self.hub.get("rachio.zone_a1a1a1a1").name, "Front")
        self.assertTrue(self.hub.get("rachio.zone_a1a1a1a1").attrs["lastRun"])
        self.assertIsNone(self.hub.get("rachio.zone_z3"))  # disabled zones are left out
        self.assertEqual(self.ra._remaining_calls, 1500)

    async def test_polling_respects_the_daily_limit(self):
        self.assertEqual(self.ra.poll_interval(), 180)
        self.ra._devices.update({f"x{i}": "e" for i in range(4)})  # 5 controllers
        self.assertLessEqual(86400 / self.ra.poll_interval() * 5, 1000)
        self.ra._remaining_calls = 100
        self.assertEqual(self.ra.poll_interval(), 3600)

    async def test_watering_from_the_schedule(self):
        self.fake.current = {"zoneId": Z2, "zoneNumber": 2, "zoneStartDate": int((time.time() - 60) * 1000),
                             "zoneDuration": 600, "status": "PROCESSING"}
        await self.ra.refresh(DEV)
        await self.settle()
        z, c = self.hub.get("rachio.zone_b2b2b2b2"), self.hub.get("rachio.controller_d1d1d1d1")
        self.assertEqual((z.state, z.attrs["remaining"]), ("watering", 9))
        self.assertEqual((c.state, c.attrs["currentZone"]), ("watering", "Side"))
        self.fake.current = {}
        await self.ra.refresh(DEV)
        await self.settle()
        self.assertEqual(self.events, [("rachio.zone_b2b2b2b2", "started"), ("rachio.zone_b2b2b2b2", "finished")])

    async def test_commands(self):
        await self.ra.command("rachio.zone_a1a1a1a1", "run", 20)
        self.assertEqual(self.fake.calls[-1], ("PUT", "/zone/start", {"id": Z1, "duration": 1200}))
        self.assertEqual(self.hub.get("rachio.zone_a1a1a1a1").state, "watering")
        await self.ra.command("rachio.zone_a1a1a1a1", "stop")
        self.assertEqual(self.fake.calls[-1], ("PUT", "/device/stop_water", {"id": DEV}))
        await self.ra.command("rachio.controller_d1d1d1d1", "rain_delay", 2)
        self.assertIn(("PUT", "/device/rain_delay", {"id": DEV, "duration": 172800}), self.fake.calls)
        self.assertEqual(self.hub.get("rachio.controller_d1d1d1d1").state, "rain_delay")
        with self.assertRaisesRegex(CommandError, "up to 7 days"):
            await self.ra.command("rachio.controller_d1d1d1d1", "rain_delay", 10)
        await self.ra.command("rachio.controller_d1d1d1d1", "rain_delay", 0)
        await self.ra.command("rachio.controller_d1d1d1d1", "turn_off")
        self.assertEqual(self.hub.get("rachio.controller_d1d1d1d1").state, "off")
        await self.ra.command("rachio.controller_d1d1d1d1", "turn_on")
        self.assertEqual(self.hub.get("rachio.controller_d1d1d1d1").state, "idle")

    async def test_optional_webhooks(self):
        self.ra.config["webhook_base"] = "https://newman.example.com/"
        await self.ra._register_webhooks()
        self.assertIn(("DELETE", "/notification/webhook/old", None), self.fake.calls)
        post = next(b for m, p, b in self.fake.calls if m == "POST")
        self.assertEqual(post["eventTypes"], [{"id": "5"}, {"id": "10"}])
        url = post["url"]
        self.assertTrue(url.startswith("https://newman.example.com/api/public/webhooks/rachio.controller_d1d1d1d1/"))
        secret = url.rsplit("/", 1)[-1]
        self.assertEqual(self.ra.poll_interval(), 1800)
        eid = "rachio.controller_d1d1d1d1"
        self.assertFalse(await self.ra.receive_webhook(eid, "wrong", b"{}", "application/json"))
        n = len(self.fake.calls)
        ok = await self.ra.receive_webhook(eid, secret, json.dumps(
            {"type": "ZONE_STATUS", "subType": "ZONE_STARTED", "zoneId": Z1, "duration": 300}).encode(), "application/json")
        self.assertTrue(ok)
        self.assertEqual(len(self.fake.calls), n)  # zone start handled without asking Rachio
        self.assertEqual(self.hub.get("rachio.zone_a1a1a1a1").state, "watering")
        await self.ra.receive_webhook(eid, secret, json.dumps({"subType": "ZONE_COMPLETED", "zoneId": Z1}).encode(), "")
        self.assertEqual(self.hub.get("rachio.zone_a1a1a1a1").state, "idle")
        self.assertEqual(self.ra.admin_details()["addresses"][0]["entityId"], eid)

    async def test_bad_key(self):
        self.ra.config = {"api_key": "NOPE"}
        with self.assertRaisesRegex(Exception, "API key"):
            await self.ra.refresh(DEV)


if __name__ == "__main__":
    unittest.main()
