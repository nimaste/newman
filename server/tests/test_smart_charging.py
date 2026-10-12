"""Smart charging (charging.py): the hub keeps a wall tablet between its two levels by switching the
plug it charges from - only when the battery crosses a level, so a plug switched by hand is left
alone - learns that plug by watching, and tells the admins when the tablet is nearly flat."""
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.store import Phone  # noqa: E402
from test_auth import Browser, FakeLocks, H  # noqa: E402

W = 0.05  # the learning window in these tests (10 s for real)


class SmartChargingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", self.data)
        self.h = H(self.data)
        self.plugs = FakeLocks(self.h.integrations._ctx)
        self.h.integrations.instances["fake"] = self.plugs
        for eid, name in (("fake.plug", "Tablet charger"), ("fake.plug2", "Hall plug")):
            self.h.hub.upsert(eid, kind="switch", name=name, integration="fake")
            await self.h.hub.update(eid, state="off")
        self.h.hub.upsert("fake.motion", kind="motion", name="Hall motion", integration="fake")
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        self.ch = self.h.charging
        self.ch.learn_window = W
        self.ch.plug_grace = 0.1
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        r = await self.client.post("/api/users", json={"username": "kitchen", "name": "Kitchen tablet", "role": "tablet",
                                                       "password": "wall tablet pass"})
        self.uid = (await r.json())["id"]
        self.tab = Browser(self.client)
        r = await self.tab.post("/api/auth/login", json={"username": "kitchen", "password": "wall tablet pass", "kind": "tablet"})
        self.auth = {"Authorization": "Bearer " + (await r.json())["token"]}
        self.eid = "tablets." + self.uid
        admin = next(u for u in self.h.store.users() if u["username"] == "chris")
        self.h.store.register_phone(Phone(device_id="test-chris", token="t-chris", platform="test",
                                          person_id=admin["person_id"]))

    async def asyncTearDown(self):
        await self.tab.close()
        await self.client.close()
        self.tmp.cleanup()

    async def report(self, level, charging=False, **more):
        r = await self.tab.post("/api/tablet/battery", headers=self.auth,
                                json={"level": level, "charging": charging, "plugged": "ac" if charging else "none", **more})
        self.assertEqual(r.status, 204)
        await asyncio.sleep(0.02)  # the hub's listeners run as tasks

    async def setup(self, **changes):
        r = await self.client.patch("/api/charging/" + self.uid, json=changes)
        return r.status, await r.json()

    async def plug(self, state, eid="fake.plug", available=True):
        await self.h.hub.update(eid, state=state, available=available)
        await asyncio.sleep(0.01)

    def pushes(self):
        return [m["body"] for m in self.h.notifier.outbox]

    def activity(self, kind="automation"):
        return [(e["title"], e["entityId"]) for e in self.h.store.events(50, kind=kind)]

    # -- keeping it between the two levels --------------------------------------------------------------

    async def test_off_by_default_then_on_below_start_and_off_above_stop(self):
        st, s = await self.setup()
        self.assertEqual(st, 200)
        self.assertEqual((s["enabled"], s["startBelow"], s["stopAbove"], s["charger"], s["learned"]),
                         (False, 20, 80, None, False))
        await self.report(15)
        self.assertEqual(self.plugs.calls, [])  # off: nothing happens
        await self.report(50)
        st, s = await self.setup(enabled=True, charger="fake.plug")
        self.assertEqual((st, s["enabled"], s["charger"], s["chargerName"], s["device"]),
                         (200, True, "fake.plug", "Tablet charger", self.eid))
        self.assertEqual(self.plugs.calls, [])  # inside the band
        await self.report(22)
        self.assertEqual(self.plugs.calls, [])
        await self.report(19)
        self.assertEqual(self.plugs.calls, [("fake.plug", "turn_on")])
        self.assertIn(("Turned Tablet charger on: battery 19%", self.eid), self.activity())
        await self.plug("on")
        await self.report(50, charging=True)
        await self.report(80, charging=True)
        self.assertEqual(len(self.plugs.calls), 1)  # 80 isn't above 80
        await self.report(81, charging=True)
        self.assertEqual(self.plugs.calls[-1], ("fake.plug", "turn_off"))
        self.assertIn(("Turned Tablet charger off: battery 81%", self.eid), self.activity())
        # Its own levels.
        await self.plug("off")
        st, s = await self.setup(startBelow=40, stopAbove=60)
        self.assertEqual((s["startBelow"], s["stopAbove"]), (40, 60))
        await self.report(50)
        await self.report(39)
        self.assertEqual(self.plugs.calls[-1], ("fake.plug", "turn_on"))
        self.assertEqual(len(self.plugs.calls), 3)

    async def test_only_crossings_count_so_a_plug_switched_by_hand_is_left_alone(self):
        await self.report(50)
        await self.setup(enabled=True, charger="fake.plug")
        await self.report(19)
        self.assertEqual(self.plugs.calls, [("fake.plug", "turn_on")])
        await self.plug("on")
        await self.plug("off")  # someone switched it off by hand
        for level in (18, 17, 15):
            await self.report(level)
        self.assertEqual(len(self.plugs.calls), 1)  # still low, but no new crossing
        await self.report(30)
        await self.report(19)
        self.assertEqual(self.plugs.calls[-1], ("fake.plug", "turn_on"))  # the next crossing
        self.assertEqual(len(self.plugs.calls), 2)
        # The other way: above 80 and switched back on by hand.
        await self.plug("on")
        await self.report(85, charging=True)
        self.assertEqual(self.plugs.calls[-1], ("fake.plug", "turn_off"))
        await self.plug("off")
        await self.plug("on")
        await self.report(88, charging=True)
        await self.report(90, charging=True)
        self.assertEqual(len(self.plugs.calls), 3)
        # Already as it should be at a crossing: nothing sent.
        await self.report(50, charging=True)
        await self.report(15, charging=True)
        self.assertEqual(len(self.plugs.calls), 3)

    async def test_turning_it_on_outside_the_band_acts_once_straight_away(self):
        await self.report(12)
        st, _ = await self.setup(enabled=True, charger="fake.plug")
        self.assertEqual(st, 200)
        self.assertEqual(self.plugs.calls, [("fake.plug", "turn_on")])
        await self.report(11)
        self.assertEqual(len(self.plugs.calls), 1)
        # A restart: the hub looks once more (the battery is still low and the plug off).
        self.ch._zone.clear()
        await self.report(10)
        self.assertEqual(len(self.plugs.calls), 2)
        # No charger yet: nothing to switch, nothing breaks.
        await self.setup(charger=None)
        await self.report(30)
        await self.report(15)
        self.assertEqual(len(self.plugs.calls), 2)

    # -- learning the charger ---------------------------------------------------------------------------

    async def learn_wait(self):
        await asyncio.sleep(2 * W + 0.12)

    async def test_learns_the_plug_from_one_clean_match_and_relearns_when_moved(self):
        await self.setup(enabled=True)
        await self.report(50)
        await self.plug("on")
        await self.report(50, charging=True)
        await self.learn_wait()
        st, s = await self.setup()
        self.assertEqual((s["charger"], s["learned"]), ("fake.plug", True))
        self.assertAlmostEqual(s["learnedAt"], time.time(), delta=5)
        self.assertIn(("Learned: charges from Tablet charger", self.eid), self.activity())
        self.assertEqual(self.plugs.calls, [])  # learned by watching, never by switching plugs
        # Moved to the hall plug: unplugged (no plug changed - nothing learned), then plugged in there.
        await self.report(50, charging=False)
        await self.learn_wait()
        await self.plug("on", "fake.plug2")
        await self.report(50, charging=True)
        await self.learn_wait()
        _, s = await self.setup()
        self.assertEqual((s["charger"], s["learned"]), ("fake.plug2", True))
        self.assertIn(("Learned: charges from Hall plug", self.eid), self.activity())
        # Switching it off stops the charging: the same plug, nothing new to learn.
        n = len(self.activity())
        await self.plug("off", "fake.plug2")
        await self.report(50, charging=False)
        await self.learn_wait()
        self.assertEqual(len(self.activity()), n)
        # A plug picked by hand isn't "learned".
        _, s = await self.setup(charger="fake.plug")
        self.assertEqual((s["charger"], s["learned"], s["learnedAt"]), ("fake.plug", False, None))

    async def test_an_ambiguous_match_is_ignored(self):
        await self.setup(enabled=True)
        await self.report(50)
        # Two plugs switched on together.
        await self.plug("on")
        await self.plug("on", "fake.plug2")
        await self.report(50, charging=True)
        await self.learn_wait()
        self.assertIsNone((await self.setup())[1]["charger"])
        # The wrong way round: a plug switched on and the tablet stopped charging.
        await self.plug("off")
        await self.plug("off", "fake.plug2")
        await self.learn_wait()
        await self.plug("on")
        await self.report(50, charging=False)
        await self.learn_wait()
        self.assertIsNone((await self.setup())[1]["charger"])
        # Two tablets started charging when one plug went on.
        await self.plug("off")
        await self.learn_wait()
        self.h.hub.upsert("tablets.other", kind="tablet", name="Hall tablet", integration="tablets")
        await self.h.hub.update("tablets.other", state="battery", attrs={"batteryLevel": 40, "charging": False})
        await asyncio.sleep(0.01)
        await self.plug("on")
        await self.report(50, charging=True)
        await self.h.hub.update("tablets.other", state="charging", attrs={"charging": True, "plugged": "ac"})
        await self.learn_wait()
        self.assertIsNone((await self.setup())[1]["charger"])
        # Nothing learned for a tablet without smart charging.
        await self.setup(enabled=False)
        await self.report(50, charging=False)
        await self.plug("off")
        await self.learn_wait()
        await self.plug("on")
        await self.report(50, charging=True)
        await self.learn_wait()
        self.assertIsNone((await self.setup())[1]["charger"])
        self.assertFalse([t for t, _ in self.activity() if t.startswith("Learned")])

    # -- telling the admins -----------------------------------------------------------------------------

    async def test_nearly_flat_and_not_charging_alerts_once_an_hour_until_it_charges(self):
        await self.report(50)
        await self.setup(enabled=True)
        await self.report(12)
        self.assertEqual(self.pushes(), [])
        await self.report(9)
        self.assertEqual(self.pushes(), ["Kitchen tablet is at 9% and not charging"])
        self.assertEqual(self.h.notifier.outbox[0]["title"], "Smart charging")
        await self.report(8)
        await self.report(7)
        self.assertEqual(len(self.pushes()), 1)  # once an hour
        real = time.time
        self.ch.clock = lambda: real() + 3601
        await self.report(6)
        self.assertEqual(self.pushes()[0], "Kitchen tablet is at 6% and not charging")
        self.assertEqual(len(self.pushes()), 2)
        # It charged again: the next time it's that low is worth telling at once.
        await self.report(6, charging=True)
        self.assertEqual(len(self.pushes()), 2)
        await self.report(5, charging=False)
        self.assertEqual(len(self.pushes()), 3)
        self.assertIn(("Kitchen tablet is at 5% and not charging", self.eid), self.activity("problem"))
        # Without smart charging: no alerts.
        await self.setup(enabled=False)
        self.ch.clock = lambda: real() + 9000
        await self.report(4)
        self.assertEqual(len(self.pushes()), 3)

    async def test_a_charger_that_is_not_responding(self):
        await self.report(50)
        await self.setup(enabled=True, charger="fake.plug")
        await self.plug("off", available=False)
        await self.report(19)
        self.assertEqual(self.plugs.calls, [])
        self.assertEqual(self.pushes(), [])  # a grace period first (a restart, still connecting)
        await asyncio.sleep(0.2)
        self.assertEqual(self.pushes(), ["Kitchen tablet couldn't turn on Tablet charger: it's not responding"])
        # Back: it does what it couldn't then.
        await self.plug("off", available=True)
        self.assertEqual(self.plugs.calls, [("fake.plug", "turn_on")])
        await self.plug("on")
        # Back within the grace period: switched, nobody bothered.
        await self.report(85, charging=True)
        self.assertEqual(self.plugs.calls[-1], ("fake.plug", "turn_off"))
        await self.plug("off")
        await self.report(50)
        real = time.time
        self.ch.clock = lambda: real() + 3601
        await self.plug("off", available=False)
        await self.report(19)
        await self.plug("off", available=True)
        await asyncio.sleep(0.2)
        self.assertEqual(self.plugs.calls[-1], ("fake.plug", "turn_on"))
        self.assertEqual(len(self.pushes()), 1)
        # A charger that's gone altogether.
        self.h.hub.remove("fake.plug")
        await self.report(50)
        await self.report(19)
        await asyncio.sleep(0.2)
        self.assertEqual(len(self.pushes()), 2)

    async def test_a_charger_registered_after_the_tablet_is_not_an_alert(self):
        # The hub starting: the tablet's last report is restored before the charger's connection is up.
        await self.report(50)
        await self.setup(enabled=True, charger="fake.plug")
        self.h.hub.remove("fake.plug")
        await self.report(19)
        self.assertEqual(self.plugs.calls, [])
        # The charger registers within the grace period, already on: nothing to do, nobody told.
        self.h.hub.upsert("fake.plug", kind="switch", name="Tablet charger", integration="fake")
        await self.plug("on")
        await asyncio.sleep(0.2)
        self.assertEqual((self.plugs.calls, self.pushes()), ([], []))
        # Registered off: switched on then.
        await self.report(50)
        self.h.hub.remove("fake.plug")
        await self.report(19)
        self.h.hub.upsert("fake.plug", kind="switch", name="Tablet charger", integration="fake")
        await self.h.hub.update("fake.plug", state="off", available=True)
        await asyncio.sleep(0.2)
        self.assertEqual(self.plugs.calls, [("fake.plug", "turn_on")])
        self.assertEqual(self.pushes(), [])

    async def test_a_tablet_that_stops_reporting_is_told_once(self):
        await self.report(50)
        # Without smart charging: nobody is told.
        await self.h.hub.update(self.eid, available=False)
        await asyncio.sleep(0.02)
        self.assertEqual(self.pushes(), [])
        await self.report(50)
        await self.setup(enabled=True, charger="fake.plug")
        # 15 minutes without a report: the tablets integration marks it offline -> one alert.
        await self.h.hub.update(self.eid, available=False)
        await asyncio.sleep(0.02)
        self.assertEqual(self.pushes(), ["Kitchen tablet stopped reporting its battery"])
        self.assertIn(("Kitchen tablet stopped reporting its battery", self.eid), self.activity("problem"))
        await self.h.hub.update(self.eid, available=False)  # still silent: no more
        await asyncio.sleep(0.02)
        self.assertEqual(len(self.pushes()), 1)
        # It reports again, then goes silent again within the hour: not again yet.
        await self.report(50)
        await self.h.hub.update(self.eid, available=False)
        await asyncio.sleep(0.02)
        self.assertEqual(len(self.pushes()), 1)
        # An hour later, after another report: told again.
        await self.report(50)
        real = time.time
        self.ch.clock = lambda: real() + 3601
        await self.h.hub.update(self.eid, available=False)
        await asyncio.sleep(0.02)
        self.assertEqual(self.pushes()[-1], "Kitchen tablet stopped reporting its battery")
        self.assertEqual(len(self.pushes()), 2)

    def tablets(self):
        return next(i for i in self.h.integrations.instances.values() if i.type == "tablets")

    async def test_screen_off_within_its_time_is_not_stopped_reporting(self):
        # On its charger Android doesn't hold its check-ins back: it should get through.
        await self.report(50, charging=True, screen="off", background=True)
        await self.setup(enabled=True, charger="fake.plug")
        now = time.time()
        await self.tablets().check_offline(now=now + 30 * 60)  # an Android wall asleep, checking in late
        await asyncio.sleep(0.02)
        self.assertEqual(self.pushes(), [])
        await self.tablets().check_offline(now=now + 36 * 60)  # truly silent: told
        await asyncio.sleep(0.02)
        self.assertEqual(self.pushes(), ["Kitchen tablet stopped reporting its battery"])

    async def asleep_on_battery(self, level, **more):
        await self.report(level, screen="off", background=True, nextReportIn=900, dozeExempt=False, **more)

    async def test_asleep_on_battery_is_expected_silence_until_8_hours(self):
        # Screen off, on battery, not exempt from Doze: deep sleep may hold its check-ins back for hours.
        await self.setup(enabled=True, charger="fake.plug", startBelow=21, stopAbove=80)
        await self.asleep_on_battery(60)
        now = time.time()
        for minutes in (36, 120, 300, 7.9 * 60):
            await self.tablets().check_offline(now=now + minutes * 60)
            await asyncio.sleep(0.02)
            e = self.h.hub.get(self.eid)
            self.assertTrue(e.available, minutes)
            self.assertTrue(e.attrs["deepSleep"], minutes)
        self.assertEqual(self.pushes(), [])
        self.assertEqual(self.activity("problem"), [])
        self.assertEqual(self.plugs.calls, [])  # 60% is nowhere near the bottom of the band
        # Eight hours without a word, even asleep: something's wrong.
        await self.tablets().check_offline(now=now + 8.1 * 3600)
        await asyncio.sleep(0.02)
        self.assertFalse(self.h.hub.get(self.eid).available)
        self.assertEqual(self.pushes(), ["Kitchen tablet hasn't checked in for 8 hours"])
        # It reports again: no longer asleep or offline.
        await self.asleep_on_battery(58)
        e = self.h.hub.get(self.eid)
        self.assertEqual((e.available, e.attrs["deepSleep"]), (True, False))

    async def test_silent_when_it_should_report_is_told(self):
        await self.setup(enabled=True, charger="fake.plug")
        # Exempt from Doze: its check-ins get through, so missing one is a problem.
        await self.report(50, screen="off", background=True, nextReportIn=900, dozeExempt=True)
        now = time.time()
        await self.tablets().check_offline(now=now + 34 * 60)
        await asyncio.sleep(0.02)
        self.assertEqual(self.pushes(), [])
        await self.tablets().check_offline(now=now + 36 * 60)
        await asyncio.sleep(0.02)
        self.assertEqual(self.pushes(), ["Kitchen tablet stopped reporting its battery"])
        # Screen on: 15 minutes.
        real = time.time
        self.ch.clock = lambda: real() + 3601
        await self.report(50, screen="on", background=True)
        now = time.time()
        await self.tablets().check_offline(now=now + 16 * 60)
        await asyncio.sleep(0.02)
        self.assertEqual(self.pushes()[-1], "Kitchen tablet stopped reporting its battery")
        self.assertEqual(len(self.pushes()), 2)

    async def test_asleep_near_the_bottom_of_the_band_turns_the_charger_on_early(self):
        self.ch.start_wait = 0.1
        await self.setup(enabled=True, charger="fake.plug", startBelow=21, stopAbove=80)
        await self.asleep_on_battery(30)  # within 10 points of 21
        now = time.time()
        await self.tablets().check_offline(now=now + 20 * 60)  # not due yet: nothing
        await asyncio.sleep(0.02)
        self.assertEqual(self.plugs.calls, [])
        await self.tablets().check_offline(now=now + 36 * 60)  # missed its check-in: deep sleep
        await asyncio.sleep(0.02)
        self.assertEqual(self.plugs.calls, [("fake.plug", "turn_on")])
        self.assertIn(("Turned Tablet charger on: early, Kitchen tablet is asleep at 30%", self.eid), self.activity())
        await self.plug("on")
        await self.tablets().check_offline(now=now + 60 * 60)  # still asleep: once
        await asyncio.sleep(0.2)  # past the wrong-plug wait: no question from a level from before it slept
        self.assertEqual(len(self.plugs.calls), 1)
        self.assertEqual(self.pushes(), [])
        # Charging, it reports again and smart charging carries on as usual.
        await self.report(33, charging=True, screen="off", background=True, nextReportIn=900)
        await self.report(81, charging=True, screen="off", background=True, nextReportIn=900)
        self.assertEqual(self.plugs.calls[-1], ("fake.plug", "turn_off"))
        self.assertEqual(self.pushes(), [])
        # Higher up when it went quiet: left alone (the usual crossing will do).
        await self.plug("off")
        await self.asleep_on_battery(32)
        await self.tablets().check_offline(now=time.time() + 36 * 60)
        await asyncio.sleep(0.02)
        self.assertEqual(self.plugs.calls[-1], ("fake.plug", "turn_off"))
        self.assertTrue(self.h.hub.get(self.eid).attrs["deepSleep"])

    async def test_a_sleeping_ipad_has_an_unknown_battery(self):
        await self.report(50, screen="off")  # no background reports
        await self.setup(enabled=True, charger="fake.plug")
        now = time.time()
        await self.tablets().check_offline(now=now + 2 * 3600)
        await asyncio.sleep(0.02)
        self.assertTrue(self.h.hub.get(self.eid).attrs["batteryStale"])
        self.assertEqual(self.pushes(), [])  # not "stopped reporting": it's asleep
        # Its last level crossing (as an admin changes the band) does nothing while it's unknown.
        await self.setup(startBelow=60, stopAbove=90)
        await asyncio.sleep(0.02)
        self.assertEqual(self.plugs.calls, [])
        # Even a nearly-flat last level: no alert, no switching.
        await self.h.hub.update(self.eid, attrs={"batteryLevel": 5})
        await asyncio.sleep(0.02)
        self.assertEqual((self.plugs.calls, self.pushes()), ([], []))
        # Woken: known again, and acted on.
        await self.report(50, screen="on")
        self.assertEqual(self.plugs.calls, [("fake.plug", "turn_on")])
        # After 12 hours silent it is offline: then told.
        await self.report(70, screen="off")
        await self.tablets().check_offline(now=time.time() + 12.5 * 3600)
        await asyncio.sleep(0.02)
        self.assertEqual(self.pushes()[-1], "Kitchen tablet stopped reporting its battery")

    async def test_a_new_charger_acts_at_once_even_in_the_same_zone(self):
        await self.report(50)
        await self.setup(enabled=True, charger="fake.plug")
        await self.report(19)
        self.assertEqual(self.plugs.calls, [("fake.plug", "turn_on")])
        await self.plug("on")
        # Still low; the admin says it's really the hall plug: that one goes on now.
        await self.report(18)
        self.assertEqual(len(self.plugs.calls), 1)
        await self.setup(charger="fake.plug2")
        self.assertEqual(self.plugs.calls[-1], ("fake.plug2", "turn_on"))
        await self.plug("on", eid="fake.plug2")
        # Even while the tablet is offline (it stopped reporting): from its last level.
        await self.h.hub.update(self.eid, available=False)
        await asyncio.sleep(0.02)
        await self.plug("off")
        await self.setup(charger="fake.plug")
        self.assertEqual(self.plugs.calls[-1], ("fake.plug", "turn_on"))
        # ... but no "nearly flat" alert from an old level.
        self.assertFalse([p for p in self.pushes() if "not charging" in p])

    # -- the wrong plug ------------------------------------------------------------------------------------

    async def more_plugs(self):
        """A plug in the tablet's room, one that measures power, and a lamp that's on."""
        for eid, name, state, attrs in (("fake.plug3", "Kitchen plug", "off", {"area": "Kitchen"}),
                                        ("fake.plug4", "Desk plug", "off", {"power": 0.0}),
                                        ("fake.lamp", "Lamp", "on", {})):
            self.h.hub.upsert(eid, kind="switch", name=name, integration="fake")
            await self.h.hub.update(eid, state=state, attrs=attrs)
        await self.report(50)
        await self.h.hub.update(self.eid, attrs={"area": "Kitchen"})
        self.ch.start_wait = 0.15

    async def test_not_charging_after_turning_the_plug_on_asks_which_plug_once(self):
        await self.more_plugs()
        await self.setup(enabled=True, charger="fake.plug")
        await self.report(19)
        self.assertEqual(self.plugs.calls, [("fake.plug", "turn_on")])
        await self.plug("on")
        await self.report(19)
        self.assertEqual(self.pushes(), [])  # it gets a minute first
        await asyncio.sleep(0.3)
        text = "Kitchen tablet didn't start charging from Tablet charger. Which plug is it on?"
        self.assertEqual(self.pushes(), [text])
        msg = self.h.notifier.outbox[0]
        buttons = __import__("json").loads(msg["buttons"])
        # Same room first, then one that measures power, then one that's off - never the lamp that's on.
        self.assertEqual([(b["id"], b["label"]) for b in buttons],
                         [("charger:fake.plug3", "Kitchen plug"), ("charger:fake.plug4", "Desk plug"),
                          ("charger:fake.plug2", "Hall plug"), ("charger_none", "None of these")])
        self.assertTrue(msg["action_subject"].startswith("charging:" + self.uid + ":"))
        self.assertTrue(msg["category"])
        cats = await (await self.client.get("/api/push/categories")).json()
        self.assertIn(msg["category"], [c["id"] for c in cats])
        self.assertIn((text, self.eid), self.activity("problem"))
        _, s = await self.setup()
        self.assertEqual((s["question"]["text"], [c["id"] for c in s["question"]["choices"]]),
                         (text, ["fake.plug3", "fake.plug4", "fake.plug2"]))
        self.assertEqual(len(self.plugs.calls), 1)  # no plug switched to find out
        # Once per attempt.
        await self.report(18)
        await asyncio.sleep(0.3)
        self.assertEqual(len(self.pushes()), 1)
        # Answered from the phone: that plug becomes the charger and goes on.
        r = await self.client.post("/api/public/actions", json={
            "deviceId": msg["deviceId"], "subject": msg["action_subject"], "exp": msg["action_exp"],
            "token": msg["action_token"], "button": "charger:fake.plug3"})
        self.assertEqual(r.status, 200, await r.text())
        self.assertTrue((await r.json())["accepted"])  # replied at once; the plug is switched after
        _, s = await self.setup()
        self.assertEqual((s["charger"], s["learned"], s["chosenBy"], s["question"]), ("fake.plug3", False, "user", None))
        for _ in range(50):
            if self.plugs.calls[-1:] == [("fake.plug3", "turn_on")]:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(self.plugs.calls[-1], ("fake.plug3", "turn_on"))
        self.assertIn(("Charges from Kitchen plug", self.eid), self.activity())
        # The same answer twice: already answered.
        r = await self.client.post("/api/public/actions", json={
            "deviceId": msg["deviceId"], "subject": msg["action_subject"], "exp": msg["action_exp"],
            "token": msg["action_token"], "button": "charger:fake.plug3"})
        self.assertEqual(r.status, 409)
        # It starts charging: the usual learning confirms it, and nobody is asked again.
        await self.plug("on", "fake.plug3")
        await self.report(19, charging=True)
        await self.learn_wait()
        await asyncio.sleep(0.2)
        _, s = await self.setup()
        self.assertEqual((s["charger"], s["learned"], s["chosenBy"]), ("fake.plug3", True, "user"))
        self.assertIn(("Confirmed: charges from Kitchen plug", self.eid), self.activity())
        self.assertEqual(len(self.pushes()), 1)

    async def test_the_question_in_the_settings_and_none_of_these(self):
        await self.more_plugs()
        await self.setup(enabled=True, charger="fake.plug")
        await self.report(19)
        await self.plug("on")
        await asyncio.sleep(0.3)
        _, s = await self.setup()
        q = s["question"]
        r = await self.client.post(f"/api/charging/{self.uid}/answer", json={"question": q["id"], "plug": "fake.motion"})
        self.assertEqual(r.status, 409)  # only one of the choices
        r = await self.client.post(f"/api/charging/{self.uid}/answer", json={"question": q["id"], "plug": None})
        self.assertEqual(r.status, 200)
        s = await r.json()
        self.assertEqual((s["charger"], s["question"]), ("fake.plug", None))
        self.assertEqual(len(self.plugs.calls), 1)
        # The next attempt (a new crossing) may ask again; one that charges doesn't.
        await self.plug("off")
        await self.report(30)
        await self.report(19)
        await self.plug("on")
        await asyncio.sleep(0.3)
        self.assertEqual(len(self.pushes()), 2)
        _, s = await self.setup()
        r = await self.client.post(f"/api/charging/{self.uid}/answer", json={"question": s["question"]["id"],
                                                                            "plug": "fake.plug2"})
        self.assertEqual((await r.json())["charger"], "fake.plug2")
        self.assertEqual(self.plugs.calls[-1], ("fake.plug2", "turn_on"))
        await self.plug("on", "fake.plug2")
        await self.report(19, charging=True)
        await asyncio.sleep(0.3)
        self.assertEqual(len(self.pushes()), 2)
        # Not reporting (offline): nothing to conclude.
        await self.plug("off", "fake.plug2")
        await self.report(30)
        await self.report(19)
        self.assertEqual(self.plugs.calls[-1], ("fake.plug2", "turn_on"))
        await self.plug("on", "fake.plug2")
        await self.h.hub.update(self.eid, available=False)
        await asyncio.sleep(0.3)
        self.assertEqual(len([p for p in self.pushes() if "didn't start charging" in p]), 2)

    # -- settings ------------------------------------------------------------------------------------------

    async def test_settings_are_checked_and_admin_only(self):
        for bad in ({"startBelow": 80, "stopAbove": 20}, {"startBelow": 50, "stopAbove": 50}, {"startBelow": 3},
                    {"stopAbove": 96}, {"startBelow": 20.5}, {"startBelow": True}, {"stopAbove": "80"},
                    {"enabled": "yes"}, {"charger": "fake.nothing"}, {"charger": "fake.motion"}, {"charger": 5}):
            st, j = await self.setup(**bad)
            self.assertEqual(st, 400, bad)
            self.assertTrue(j["error"])
        st, s = await self.setup(startBelow=5, stopAbove=95)
        self.assertEqual((st, s["startBelow"], s["stopAbove"]), (200, 5, 95))
        st, _ = await self.setup(stopAbove=4)
        self.assertEqual(st, 400)
        # Only devices that switch on and off can be the charger.
        await self.report(50)
        ids = [c["id"] for c in s["choices"]]
        self.assertEqual(ids, ["fake.plug2", "fake.plug"])  # by name ("Hall plug", "Tablet charger")
        self.assertEqual(s["choices"][0]["group"], "Switches & plugs")
        self.assertNotIn(self.eid, [c["id"] for c in (await self.setup())[1]["choices"]])
        # Kept with the account across a restart of the hub's settings.
        await self.setup(enabled=True, charger="fake.plug2")
        self.assertEqual(self.h.store.settings()["smartCharging"][self.uid]["charger"], "fake.plug2")
        # Not for other accounts, and only an admin may see or change it.
        admin = next(u for u in self.h.store.users() if u["username"] == "chris")
        self.assertEqual((await self.client.get("/api/charging/" + admin["id"])).status, 404)
        self.assertEqual((await self.client.get("/api/charging/nope")).status, 404)
        await self.client.post("/api/users", json={"username": "dante", "name": "Dante", "role": "family",
                                                   "password": "another pass"})
        fam = Browser(self.client)
        await fam.post("/api/auth/login", json={"username": "dante", "password": "another pass", "kind": "browser"})
        self.assertEqual((await fam.get("/api/charging/" + self.uid)).status, 403)
        self.assertEqual((await fam.patch("/api/charging/" + self.uid, json={"enabled": False})).status, 403)
        await fam.close()
        self.assertEqual((await self.tab.patch("/api/charging/" + self.uid, json={"enabled": False},
                                               headers=self.auth)).status, 403)
        self.assertEqual((await self.tab.get("/api/charging/" + self.uid, headers=self.auth)).status, 403)
        self.assertTrue((await self.setup())[1]["enabled"])


if __name__ == "__main__":
    unittest.main()
