"""Network checks (fake web server, fake ping) and the Sun: local sunrise/sunset maths, the Sun
device, "at sunset" triggers, "only when it's dark" conditions and their wording."""
import asyncio
import os
import socket
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman import sun  # noqa: E402
from newman.automations import describe as d  # noqa: E402
from newman.automations.engine import Engine  # noqa: E402
from newman.integrations.base import Context  # noqa: E402
from newman.integrations.manager import IntegrationManager  # noqa: E402
from newman.integrations.netcheck import NetCheckIntegration, split_host_port  # noqa: E402
from newman.integrations.sun import SunIntegration  # noqa: E402
from newman.kinds import KINDS, properties_for  # noqa: E402
from newman.push.notifier import Notifier  # noqa: E402
from newman.state import Hub  # noqa: E402
from newman.store import Store  # noqa: E402

TORONTO = (43.6532, -79.3832)
EDT, EST = timezone(timedelta(hours=-4)), timezone(timedelta(hours=-5))
# Reference times (astral / timeanddate.com agree to the minute), local clock time.
KNOWN = [
    (date(2024, 6, 20), EDT, {"dawn": "04:59", "sunrise": "05:36", "sunset": "21:02", "dusk": "21:39"}),
    (date(2024, 12, 21), EST, {"dawn": "07:15", "sunrise": "07:48", "sunset": "16:43", "dusk": "17:17"}),
    (date(2025, 3, 20), EDT, {"dawn": "06:51", "sunrise": "07:20", "sunset": "19:30", "dusk": "19:59"}),
    (date(2026, 11, 1), EST, {"dawn": "06:23", "sunrise": "06:54", "sunset": "17:08", "dusk": "17:38"}),
]


def minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


class SunMathTests(unittest.TestCase):
    def tearDown(self):
        sun.set_location(None, None)

    def test_toronto_within_two_minutes(self):
        for day, tz, expected in KNOWN:
            sun.set_location(*TORONTO, tz=tz)
            for event, hhmm in expected.items():
                got = sun.event_on(day, event)
                self.assertIsNotNone(got)
                self.assertEqual(got.date(), day)
                diff = abs(got.hour * 60 + got.minute + got.second / 60 - minutes(hhmm))
                self.assertLessEqual(diff, 2, f"{day} {event}: {got:%H:%M} vs {hhmm}")

    def test_position(self):
        sun.set_location(*TORONTO, tz=EDT)
        noon = sun.event_on(date(2024, 6, 20), "noon")
        elev, az = sun.position(noon)
        self.assertAlmostEqual(elev, 90 - 43.65 + 23.44, delta=0.7)  # highest of the year
        self.assertAlmostEqual(az, 180, delta=1.5)
        elev, az = sun.position(datetime(2024, 6, 20, 7, 0, tzinfo=EDT))
        self.assertTrue(0 < elev < 25 and 45 < az < 90, (elev, az))  # morning, north-east-ish
        self.assertLess(sun.position(datetime(2024, 6, 21, 1, 0, tzinfo=EDT))[0], -10)

    def test_polar_night_has_no_sunrise(self):
        self.assertIsNone(sun.event_on(date(2024, 12, 21), "sunrise", where=(78.2, 15.6)))  # Svalbard

    def test_no_location(self):
        sun.set_location(None, None)
        sun.use_store(type("S", (), {"settings": lambda s: {}, "integrations": lambda s: []})())
        self.assertIsNone(sun.location())
        self.assertTrue(sun.sun_ok({"only": "dark"}))  # can't tell: never blocks (errors() says why)

    def test_location_from_store(self):
        sun.set_location(None, None)
        weather = {"type": "weather", "config": {"latitude": 45.4, "longitude": -75.7}}
        st = type("S", (), {"settings": lambda s: {}, "integrations": lambda s: [weather]})()
        sun.use_store(st)
        self.assertEqual(sun.location(), (45.4, -75.7))  # the weather connection's spot
        st2 = type("S", (), {"settings": lambda s: {"homePlace": {"lat": 43.7, "lon": -79.4}},
                             "integrations": lambda s: [weather]})()
        sun.use_store(st2)
        self.assertEqual(sun.location(), (43.7, -79.4))  # the Home place wins


def sun_auto(**over):
    a = {"name": "Porch light", "trigger": {"items": [{"type": "time", "sun": "sunset", "offset": -30,
                                                       "days": [0, 1, 2, 3, 4, 5, 6]}]},
         "notify": {"enabled": False}, "actions": []}
    a.update(over)
    return d.normalize(a)


class SunAutomationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.store, self.hub = Store(data), Hub()
        ctx = Context(self.hub, data, data / "snapshots")
        self.engine = Engine(self.hub, self.store, Notifier(self.store), IntegrationManager(self.store, ctx))
        sun.set_location(*TORONTO, tz=EDT)

    async def asyncTearDown(self):
        sun.set_location(None, None)
        self.tmp.cleanup()

    def test_trigger_fires_at_offset(self):
        a = sun_auto()
        day = date(2024, 6, 20)  # sunset 21:02 -> 30 min before = 20:32
        wd = day.weekday()
        self.assertEqual(d.time_item_hit(a, "20:32", wd, day), [0])
        self.assertEqual(d.time_item_hit(a, "20:31", wd, day), [])
        self.assertEqual(d.time_item_hit(a, "21:02", wd, day), [])
        after = sun_auto(trigger={"items": [{"type": "time", "sun": "sunrise", "offset": 45, "days": [wd]}]})
        at = (sun.event_on(day, "sunrise") + timedelta(minutes=45)).strftime("%H:%M")
        self.assertLessEqual(abs(minutes(at) - minutes("06:21")), 2)  # 05:36 + 45
        self.assertEqual(d.time_item_hit(after, at, wd, day), [0])
        self.assertEqual(d.time_item_hit(after, at, (wd + 1) % 7, day), [])  # not that day

    def test_next_run_follows_the_sun(self):
        a = dict(sun_auto(), enabled=True)
        nxt = self.engine.next_run(a, after=datetime(2024, 6, 20, 12, 0))
        self.assertEqual(nxt.strftime("%Y-%m-%d %H:%M"), "2024-06-20 20:32")
        nxt = self.engine.next_run(a, after=datetime(2024, 6, 20, 20, 40))
        self.assertEqual(nxt.strftime("%Y-%m-%d"), "2024-06-21")

    def test_plain_times_unchanged(self):
        a = d.normalize({"trigger": {"type": "time", "time": "21:00", "days": [0, 1, 2, 3, 4, 5, 6]}})
        self.assertNotIn("sun", a["trigger"]["items"][0])
        self.assertIsNone(a["condition"]["sun"])
        self.assertEqual(d.time_item_hit(a, "21:00", 2, date(2024, 6, 19)), [0])
        self.assertEqual(d.trigger_text(a, self.hub), "At 9:00 PM every day")
        self.assertTrue(d.sun_allows(a))

    def test_dark_across_midnight(self):
        dark = {"only": "dark", "sunset": 0, "sunrise": 0}
        at = lambda h, m, day=20: datetime(2024, 6, day, h, m)  # noqa: E731
        self.assertTrue(sun.sun_ok(dark, at(23, 30)))
        self.assertTrue(sun.sun_ok(dark, at(0, 30, 21)))
        self.assertTrue(sun.sun_ok(dark, at(5, 30)))
        self.assertFalse(sun.sun_ok(dark, at(5, 40)))
        self.assertFalse(sun.sun_ok(dark, at(12, 0)))
        self.assertTrue(sun.sun_ok(dark, at(21, 5)))
        late = {"only": "dark", "sunset": 30, "sunrise": -15}  # 21:32 until 05:21
        self.assertFalse(sun.sun_ok(late, at(21, 20)))
        self.assertTrue(sun.sun_ok(late, at(21, 40)))
        self.assertTrue(sun.sun_ok(late, at(5, 15)))
        self.assertFalse(sun.sun_ok(late, at(5, 25)))
        light = {"only": "light", "sunrise": 30, "sunset": -60}  # 06:06 until 20:02
        self.assertFalse(sun.sun_ok(light, at(6, 0)))
        self.assertTrue(sun.sun_ok(light, at(6, 10)))
        self.assertTrue(sun.sun_ok(light, at(19, 55)))
        self.assertFalse(sun.sun_ok(light, at(20, 5)))
        self.assertFalse(sun.sun_ok(light, at(1, 0)))
        self.assertTrue(sun.sun_ok(None, at(12, 0)))

    def test_wording(self):
        self.assertEqual(d.trigger_text(sun_auto(), self.hub), "At 30 min before sunset every day")
        self.assertEqual(d.trigger_text(sun_auto(trigger={"items": [{"type": "time", "sun": "sunrise", "offset": 0,
                                                                     "days": [0, 1, 2, 3, 4]}]}), self.hub),
                         "At sunrise on weekdays")
        self.assertEqual(d.sun_moment("sunrise", 90), "1 h 30 min after sunrise")
        self.assertEqual(d.sun_moment("sunset", -60), "1 hour before sunset")
        w = lambda s: d.window_text(sun_auto(condition={"sun": s}))  # noqa: E731
        self.assertEqual(w({"only": "dark"}), "only between sunset and sunrise")
        self.assertEqual(w({"only": "light"}), "only during daylight")
        self.assertEqual(w({"only": "dark", "sunset": 30, "sunrise": -30}),
                         "only between 30 min after sunset and 30 min before sunrise")
        self.assertEqual(w({"only": "nonsense"}), "")
        a = sun_auto(trigger={"type": "state", "entities": []}, condition={"sun": {"only": "dark"}, "days": [5, 6]})
        self.assertEqual(d.window_text(a), "only on weekends and between sunset and sunrise")
        auto = sun_auto(condition={"sun": {"only": "dark"}}, trigger={"items": [
            {"type": "time", "time": "22:00", "days": [0, 1, 2, 3, 4, 5, 6]},
            {"type": "time", "sun": "dusk", "offset": 0, "days": [0, 1, 2, 3, 4, 5, 6]}]})
        self.assertEqual(d.summary(auto, self.hub, []),
                         "When it's 10:00 PM or it's dusk (last light), only between sunset and sunrise, do nothing yet.")

    def test_errors(self):
        a = sun_auto(actions=[], notify={"enabled": True}, recipients=["p"])
        self.assertEqual([e for e in d.errors(a, self.hub) if "sun" in e.lower()], [])
        far = sun_auto(trigger={"items": [{"type": "time", "sun": "sunset", "offset": 300, "days": [0]}]},
                       notify={"enabled": True}, recipients=["p"])
        self.assertIn("Keep it within 4 hours of sunrise or sunset", d.errors(far, self.hub))
        sun.set_location(None, None)
        sun.use_store(self.store)  # no Home place, no weather connection
        self.assertIn("Sunrise and sunset need your home's location", d.errors(a, self.hub))

    async def test_engine_skips_outside_dark(self):
        a = {"id": "auto_dark", "name": "Dark only", "trigger": {"type": "event", "entity": "x.y", "event": "pressed"},
             "condition": {"sun": {"only": "dark"}}, "notify": {"enabled": False}, "actions": []}
        orig = sun.sun_ok
        try:
            sun.sun_ok = lambda spec, now=None: False
            run = await self.engine.trigger(a, {"type": "event", "entity": "x.y", "event": "pressed"})
            self.assertEqual(run["status"], "skipped")
            self.assertIn("between sunset and sunrise", run["log"][0]["text"])
        finally:
            sun.sun_ok = orig


class SunDeviceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        self.events = []

        async def on_event(ent, event, data):
            self.events.append((ent.id, event))
        self.hub.subscribe_events(on_event)
        self.sun = SunIntegration("sun", "Sun", {}, Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        self.hub.upsert(self.sun.eid, kind="sun", name="Sun", integration="sun")

    async def asyncTearDown(self):
        sun.set_location(None, None)
        self.tmp.cleanup()

    async def test_state_properties_and_events(self):
        sun.set_location(*TORONTO, tz=EDT)
        await self.sun.tick(datetime(2024, 6, 20, 21, 0, tzinfo=EDT))
        e = self.hub.get(self.sun.eid)
        self.assertEqual(e.state, "above_horizon")
        self.assertEqual(e.attrs["nextSunset"], "2024-06-20T21:02")
        self.assertTrue(e.attrs["nextSunrise"].startswith("2024-06-21T05:3"), e.attrs["nextSunrise"])
        self.assertEqual(self.sun.status, "connected")
        props = {p["id"] for p in properties_for(e)}
        self.assertTrue({"elevation", "azimuth", "nextSunrise", "nextSunset", "nextDawn", "nextDusk"} <= props)
        await self.sun.tick(datetime(2024, 6, 20, 21, 4, tzinfo=EDT))
        await asyncio.sleep(0.01)
        self.assertEqual(self.events, [(self.sun.eid, "sunset")])
        self.assertEqual(self.hub.get(self.sun.eid).state, "below_horizon")
        await self.sun.tick(datetime(2024, 6, 20, 21, 5, tzinfo=EDT))
        await asyncio.sleep(0.01)
        self.assertEqual(len(self.events), 1)  # once

    async def test_no_location_explains(self):
        sun.set_location(None, None)
        sun.use_store(type("S", (), {"settings": lambda s: {}, "integrations": lambda s: []})())
        await self.sun.tick()
        self.assertEqual(self.sun.status, "error")
        self.assertIn("where home is", self.sun.detail)

    def test_kind_metadata(self):
        k = KINDS["sun"]
        self.assertEqual({e.id for e in k.events}, {"sunrise", "sunset", "dawn", "dusk"})
        self.assertEqual({s for s, _ in k.states}, {"above_horizon", "below_horizon"})


# -- network checks -----------------------------------------------------------------------------

def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class NetCheckTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        app = web.Application()
        app.router.add_get("/", lambda r: web.Response(text="Hello from newman"))
        app.router.add_get("/down", lambda r: web.Response(status=503, text="maintenance"))
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.hub = Hub()
        self.events = []

        async def on_event(ent, event, data):
            self.events.append((ent.id, event, data.get("detail")))
        self.hub.subscribe_events(on_event)

    async def asyncTearDown(self):
        await self.runner.cleanup()
        self.tmp.cleanup()

    def make(self, checks, **cfg):
        inst = NetCheckIntegration("net", "Network checks", {"checks": checks, "timeout": 2, **cfg},
                                   Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        inst.register()
        return inst

    async def test_http_tcp_checks(self):
        closed = free_port()
        inst = self.make([
            {"name": "Site", "type": "http", "target": self.base + "/", "expect": "newman"},
            {"name": "Missing word", "type": "http", "target": self.base + "/", "expect": "goodbye"},
            {"name": "Maintenance", "type": "http", "target": self.base + "/down", "expect": "503"},
            {"name": "Down page", "type": "http", "target": self.base + "/down"},
            {"name": "Port", "type": "tcp", "target": f"127.0.0.1:{self.port}"},
            {"name": "Closed", "type": "tcp", "target": f"127.0.0.1:{closed}"},
        ], failures=1)
        for c in inst._checks:
            await inst.check_once(c)
        await asyncio.sleep(0.01)
        st = {e.name: e for e in self.hub.all("netcheck")}
        self.assertEqual(st["Site"].state, "online")
        self.assertEqual(st["Site"].attrs["statusCode"], 200)
        self.assertIsInstance(st["Site"].attrs["latency"], float)
        self.assertEqual(st["Missing word"].state, "offline")
        self.assertIn("goodbye", st["Missing word"].attrs["problem"])
        self.assertEqual(st["Maintenance"].state, "online")
        self.assertEqual(st["Down page"].state, "offline")
        self.assertEqual(st["Down page"].attrs["statusCode"], 503)
        self.assertEqual(st["Port"].state, "online")
        self.assertEqual(st["Closed"].state, "offline")
        self.assertEqual(self.events, [])  # first look: nothing to announce
        props = {p["id"] for p in properties_for(st["Site"])}
        self.assertTrue({"latency", "uptime", "statusCode", "lastChange"} <= props)
        self.assertNotIn("host", props)

    async def test_failures_in_a_row_and_uptime(self):
        inst = self.make([{"name": "Router", "type": "ping", "target": "10.9.9.9"}], failures=3)
        answers = iter([True, False, False, True, False, False, False, True])

        async def fake_ping(host):
            ok = next(answers)
            return ok, (1.5 if ok else None)
        inst._ping = fake_ping
        c = inst._checks[0]
        eid = "net.router"
        t0 = 1_000_000.0
        states = []
        for n in range(8):
            await inst.check_once(c, now=t0 + n * 60)
            await asyncio.sleep(0.01)
            states.append(self.hub.get(eid).state)
        self.assertEqual(states, ["online", "online", "online", "online", "online", "online", "offline", "online"])
        self.assertEqual([e[1] for e in self.events], ["offline", "online"])
        self.assertEqual(self.hub.get(eid).attrs["uptime"], 37.5)  # 3 of 8
        self.assertEqual(self.hub.get(eid).attrs["latency"], 1.5)
        # Samples older than a day stop counting.
        self.assertEqual(inst.uptime("router", t0 + 7 * 60 + 24 * 3600 + 30), None)

    async def test_ping_falls_back_to_tcp(self):
        inst = self.make([{"name": "Box", "type": "ping", "target": f"127.0.0.1:{self.port}"}])

        async def no_icmp(host):
            raise PermissionError("not allowed")
        inst._ping = no_icmp
        await inst.check_once(inst._checks[0])
        self.assertEqual(self.hub.get("net.box").state, "online")
        self.assertFalse(inst._icmp_ok)
        # A refused port still proves the host answered.
        inst2 = self.make([{"name": "Box2", "type": "ping", "target": f"127.0.0.1:{free_port()}"}])
        inst2._icmp_ok = False
        await inst2.check_once(inst2._checks[0])
        self.assertEqual(self.hub.get("net.box2").state, "online")

    def test_validate_and_parse(self):
        errs = NetCheckIntegration.validate({"checks": [{"name": "A", "type": "http", "target": "example.com"},
                                                        {"name": "B", "type": "tcp", "target": "nas"}]})
        self.assertIn("A: a web address starts with http:// or https://", errs)
        self.assertIn("B: a TCP check needs a port (address:port)", errs)
        self.assertIn("Add at least one check", NetCheckIntegration.validate({"checks": []}))
        self.assertEqual(split_host_port("nas:445"), ("nas", 445))
        self.assertEqual(split_host_port("[fe80::1]:22"), ("fe80::1", 22))
        self.assertEqual(split_host_port("https://x.y:8443/p"), ("x.y", 8443))
        self.assertEqual(split_host_port("10.1.1.1"), ("10.1.1.1", None))

    def test_kind_metadata(self):
        k = KINDS["netcheck"]
        self.assertEqual({e.id for e in k.events}, {"offline", "online"})
        self.assertEqual(k.category, "servers")


if __name__ == "__main__":
    unittest.main()
