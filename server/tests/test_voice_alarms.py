"""Alarms on the wall tablets and the richer timers: what can be said, the questions back, the alarm clock
(repeats, skip once, snooze, daylight saving, a restart) and the API the wall's screens use."""
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.voice import intents as I  # noqa: E402
from newman.voice.alarms import Alarms, repeat_title, say_repeat  # noqa: E402
from newman.voice.timers import Timers  # noqa: E402
from test_auth import Browser, H  # noqa: E402
from test_voice import FakeSTT, FakeTTS, house  # noqa: E402

TZ = ZoneInfo("America/Toronto")


def ts(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp()


def timer(tid, label, seconds, state="running", ends=2000.0, **kw):
    t = {"id": tid, "label": label, "seconds": seconds, "endsAt": ends, "state": state, **kw}
    t["name"] = I.timer_name(t)
    return t


def alarm(aid, hour, minute, days=(), label="", state="set", enabled=True):
    a = {"id": aid, "hour": hour, "minute": minute, "days": list(days), "label": label, "state": state,
         "enabled": enabled, "nextAt": None, "time": f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"}
    a["name"] = f"{label} alarm" if label else f"{a['time']} alarm"
    return a


class AlarmPhraseTests(unittest.TestCase):
    def setUp(self):
        self.w = house()

    def p(self, text, world=None):
        return I.parse(text, world or self.w)

    def v(self, text, world=None):
        it = self.p(text, world)
        return it.type, {k: it.value.get(k) for k in ("hour", "minute", "ampm", "days", "once", "label")} if it.value else {}

    def test_setting_alarms(self):
        self.assertEqual(self.v("Hey Newman, set an alarm for 7"),
                         ("alarm_set", {"hour": 7, "minute": 0, "ampm": "", "days": [], "once": "", "label": ""}))
        self.assertEqual(self.v("set an alarm for 6:30 tomorrow")[1] | {},
                         {"hour": 6, "minute": 30, "ampm": "", "days": [], "once": "tomorrow", "label": ""})
        self.assertEqual(self.v("wake me up at 7")[1]["hour"], 7)
        self.assertEqual(self.v("wake me up at 7 a.m.")[1]["ampm"], "am")
        self.assertEqual(self.v("set an alarm for 7:30 pm")[1]["hour"], 19)
        self.assertEqual(self.v("set an alarm for 6:30 on weekdays")[1]["days"], [1, 2, 3, 4, 5])
        self.assertEqual(self.v("set an alarm for every Saturday at 8")[1]["days"], [6])
        self.assertEqual(self.v("set an alarm for 8 on Saturdays")[1]["days"], [6])
        self.assertEqual(self.v("set an alarm every day at 7")[1]["days"], list(range(7)))
        self.assertEqual(self.v("set an alarm for 9 on Friday")[1]["once"], "d5")
        got = self.v("set a gym alarm for 5:45")[1]
        self.assertEqual((got["label"], got["hour"], got["minute"]), ("gym", 5, 45))
        self.assertEqual(self.v("set an alarm called gym for 5:45")[1]["label"], "gym")
        self.assertEqual(self.v("set an alarm for noon")[1]["hour"], 12)
        self.assertEqual(self.v("set an alarm for half past 6")[1]["minute"], 30)
        self.assertEqual(self.v("set an alarm for quarter to 7")[1]["hour"], 6)
        self.assertEqual(self.v("set an alarm for six thirty tomorrow morning")[1]["ampm"], "am")
        self.assertEqual(self.p("set an alarm").reply, "What time? Say something like “set an alarm for 7”.")

    def test_listing_cancelling_changing_skipping(self):
        for text in ("What alarms do I have?", "do I have any alarms", "list my alarms", "when is my alarm"):
            self.assertEqual(self.p(text).type, "alarm_list", text)
        it = self.p("Cancel my 7 o'clock alarm")
        self.assertEqual((it.type, it.value["hour"], it.value["minute"]), ("alarm_cancel", 7, 0))
        self.assertEqual(self.p("Cancel the gym alarm").value["label"], "gym")
        it = self.p("Turn off my weekday alarm")
        self.assertEqual((it.type, it.value["days"]), ("alarm_off", [1, 2, 3, 4, 5]))
        self.assertEqual(self.p("turn on my saturday alarm").type, "alarm_on")
        it = self.p("Change my 7 o'clock alarm to 7:30")
        self.assertEqual((it.type, it.value["target"]["hour"], it.value["hour"], it.value["minute"]),
                         ("alarm_change", 7, 7, 30))
        it = self.p("Skip my weekday alarm tomorrow")
        self.assertEqual((it.type, it.value["days"], it.value["once"]), ("alarm_skip", [1, 2, 3, 4, 5], "tomorrow"))
        self.assertTrue(self.p("cancel all my alarms").all)

    def test_the_security_alarm_stays_a_device(self):
        """"Alarm" alone is the security alarm unless it's one of these (kinds.py words, not a list here)."""
        self.assertEqual(self.p("Disarm the alarm").type, "refuse")
        self.assertNotIn(self.p("turn off the alarm").type, ("alarm_off", "stop"))
        self.assertNotEqual(self.p("what's the alarm").type, "alarm_list")
        ringing = house(alarms=[alarm("a", 6, 30, (1, 2, 3, 4, 5), state="ringing")])
        self.assertEqual(self.p("turn off the alarm", ringing).type, "stop")
        self.assertEqual(self.p("stop the alarm").type, "stop")
        self.assertEqual(self.p("cancel the alarm", ringing).type, "stop")
        self.assertEqual(self.p("cancel the alarm").type, "alarm_cancel")

    def test_snooze_and_another_minute(self):
        self.assertEqual((self.p("snooze").type, self.p("snooze").seconds), ("snooze", None))
        self.assertEqual(self.p("snooze for 5 minutes").seconds, 300)
        self.assertEqual(self.p("snooze it").type, "snooze")
        ringing = house(timers=[timer("t", "pasta", 480, state="ringing")])
        for text in ("another minute", "one more minute", "give me another minute"):
            it = self.p(text, ringing)
            self.assertEqual((it.type, it.seconds), ("another_minute", 60), text)
        self.assertNotEqual(self.p("another minute").type, "another_minute")  # nothing ringing


class TimerPhraseTests(unittest.TestCase):
    def setUp(self):
        self.w = house(timers=[timer("a", "pasta", 480), timer("b", "eggs", 300), timer("c", "", 600)])

    def p(self, text):
        return I.parse(text, self.w)

    def test_every_new_way_to_change_a_timer(self):
        cases = {
            "Extend the timer by 4 minutes": ("timer_add", 240, ""),
            "Add 4 minutes to the pasta timer": ("timer_add", 240, "pasta"),
            "Give it 2 more minutes": ("timer_add", 120, ""),
            "add a minute": ("timer_add", 60, ""),
            "Take 2 minutes off the timer": ("timer_take", 120, ""),
            "Knock a minute off the eggs": ("timer_take", 60, "eggs"),
            "Pause the timer": ("timer_pause", None, ""),
            "Resume the pasta timer": ("timer_resume", None, "pasta"),
            "unpause the timer": ("timer_resume", None, ""),
            "Restart the timer": ("timer_restart", None, ""),
            "Change the timer to 15 minutes": ("timer_change", 900, ""),
            "Make the pasta timer 12 minutes": ("timer_change", 720, "pasta"),
        }
        for text, want in cases.items():
            it = self.p(text)
            self.assertEqual((it.type, it.seconds, it.label), want, text)
        it = self.p("Cancel all timers")
        self.assertEqual((it.type, it.all), ("timer_cancel", True))

    def test_naming_one_of_several(self):
        it = self.p("cancel the 10 minute timer")
        self.assertEqual((it.type, it.ref), ("timer_cancel", {"seconds": 600}))
        self.assertEqual([t["id"] for t in I.find_timers(self.w.timers, it.label, it.ref)], ["c"])
        it = self.p("pause the second timer")
        self.assertEqual((it.type, it.ref), ("timer_pause", {"ordinal": 2}))
        self.assertEqual([t["id"] for t in I.find_timers(self.w.timers, it.label, it.ref)], ["b"])
        it = self.p("add 2 minutes to the 10 minute timer")
        self.assertEqual((it.type, it.seconds, it.ref), ("timer_add", 120, {"seconds": 600}))
        self.assertEqual(I.find_timers(self.w.timers, "", {"ordinal": -1})[0]["id"], "c")
        self.assertEqual(I.which_timer(self.w.timers[:2]), "Which one: pasta or eggs?")
        self.assertEqual(I.which_timer([timer("x", "", 600), timer("y", "", 300)]),
                         "Which one: the 10 minute timer or the 5 minute timer?")
        self.assertEqual(I.which_timer([timer("x", "", 600), timer("y", "", 600)]),
                         "Which one: the first one or the second one?")
        # The answers to "Which one…?"
        two = self.w.timers[:2]
        self.assertEqual([t["id"] for t in I.pick_answer(["eggs"], two)], ["b"])
        self.assertEqual([t["id"] for t in I.pick_answer(I.clean("the pasta one"), two)], ["a"])
        self.assertEqual([t["id"] for t in I.pick_answer(I.clean("the second one"), two)], ["b"])
        self.assertEqual(len(I.pick_answer(["both"], two)), 2)
        self.assertEqual([t["id"] for t in I.pick_answer(I.clean("the 10 minute one"), self.w.timers)], ["c"])
        self.assertIsNone(I.pick_answer(I.clean("turn on the lights"), two))

    def test_new_timers_still_start(self):
        for text, want in (("set a timer for 10 minutes", (600, "")), ("Set a pasta timer for 8 minutes", (480, "pasta")),
                           ("add a timer for 5 minutes", (300, "")), ("set a second timer for 2 minutes", (120, ""))):
            it = self.p(text)
            self.assertEqual((it.type, it.seconds, it.label), ("timer_set", *want), text)
        self.assertEqual(self.p("how much time is left on the pasta timer").type, "timer_query")
        self.assertEqual(self.p("what timers do I have").type, "timer_list")
        paused = [timer("a", "pasta", 480, state="paused", left=372.0)]
        self.assertEqual(I.say_timers(paused, 0.0), "Your pasta timer is paused with 6 minutes and 12 seconds left.")


class TimerChangeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = sqlite3.connect(Path(self.tmp.name) / "t.db")
        self.db.row_factory = sqlite3.Row
        self.now = 1000.0
        self.ring = 300.0
        self.timers = Timers(self.db, clock=lambda: self.now, ring_for=lambda _u: self.ring)

    async def asyncTearDown(self):
        self.db.close()
        self.tmp.cleanup()

    async def test_pause_resume_add_take_restart_change(self):
        t = await self.timers.add("tab", 480, "pasta")
        self.now += 100
        t = await self.timers.change("tab", t["id"], "pause")
        self.assertEqual((t["state"], t["left"]), ("paused", 380.0))
        self.now += 1000  # paused: no time goes by
        self.assertEqual(await self.timers.check(), 60.0)
        t = await self.timers.change("tab", t["id"], "add", 60)
        self.assertEqual(t["left"], 440.0)
        t = await self.timers.change("tab", t["id"], "resume")
        self.assertEqual((t["state"], t["endsAt"]), ("running", self.now + 440))
        t = await self.timers.change("tab", t["id"], "take", 40)
        self.assertEqual(self.timers.left(t), 400.0)
        t = await self.timers.change("tab", t["id"], "restart")
        self.assertEqual((t["endsAt"], t["seconds"]), (self.now + 480, 480))
        t = await self.timers.change("tab", t["id"], "set", 720)
        self.assertEqual((t["endsAt"], t["seconds"], t["name"]), (self.now + 720, 720, "pasta timer"))
        self.assertIsNone(await self.timers.change("tab", "nope", "pause"))
        with self.assertRaises(ValueError):
            await self.timers.change("tab", t["id"], "add", 2 * 86400)

    async def test_taking_off_everything_rings_and_a_ringing_one_gets_more(self):
        t = await self.timers.add("tab", 120)
        t = await self.timers.change("tab", t["id"], "take", 600)
        await self.timers.check()
        self.assertEqual(self.timers.get("tab", t["id"])["state"], "ringing")
        t = await self.timers.change("tab", t["id"], "add", 60)  # "another minute"
        self.assertEqual((t["state"], t["endsAt"]), ("running", self.now + 60))
        self.now += 61
        await self.timers.check()
        self.assertEqual(self.timers.get("tab", t["id"])["state"], "ringing")
        # Rings for the tablet's "Stop ringing after", then goes.
        self.now += self.ring - 1
        await self.timers.check()
        self.assertIsNotNone(self.timers.get("tab", t["id"]))
        self.now += 2
        await self.timers.check()
        self.assertIsNone(self.timers.get("tab", t["id"]))

    async def test_listed_in_the_order_set(self):
        await self.timers.add("tab", 600, "first")
        self.now += 1
        await self.timers.add("tab", 60, "second")
        self.assertEqual([t["label"] for t in self.timers.list("tab")], ["first", "second"])


class AlarmClockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = sqlite3.connect(Path(self.tmp.name) / "a.db")
        self.db.row_factory = sqlite3.Row
        self.now = ts(2026, 10, 22, 9)  # Thursday 9 AM
        self.ring = 600.0
        self.heard = []
        self.alarms = self.make()

    def make(self):
        a = Alarms(self.db, clock=lambda: self.now, ring_for=lambda _u: self.ring, tz=TZ)

        async def heard(uid):
            self.heard.append(uid)
        a.listeners.append(heard)
        return a

    async def asyncTearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def local(self, t):
        return datetime.fromtimestamp(t, TZ)

    async def test_once_rings_stops_and_turns_off(self):
        a = await self.alarms.add("tab", 7, 0)
        self.assertEqual(a["nextAt"], ts(2026, 10, 23, 7))  # the next 7:00 coming: tomorrow
        self.assertEqual((a["time"], a["repeat"], a["name"]), ("7:00 AM", "", "7:00 AM alarm"))
        self.assertEqual(self.alarms.say_when(a), "tomorrow")
        self.now = ts(2026, 10, 23, 6, 59)
        await self.alarms.check()
        self.assertEqual(self.alarms.get("tab", a["id"])["state"], "set")
        self.now = ts(2026, 10, 23, 7)
        await self.alarms.check()
        got = self.alarms.get("tab", a["id"])
        self.assertEqual((got["state"], got["ringingSince"]), ("ringing", self.now))
        # Rings until the tablet's "Stop ringing after", then stops by itself; a one-time alarm turns off.
        self.now += self.ring + 1
        await self.alarms.check()
        got = self.alarms.get("tab", a["id"])
        self.assertEqual((got["state"], got["enabled"], got["nextAt"]), ("set", False, None))
        # On again: the next time 7:00 comes.
        got = await self.alarms.update("tab", a["id"], enabled=True)
        self.assertEqual(got["nextAt"], ts(2026, 10, 24, 7))

    async def test_weekdays_skip_the_weekend_and_skip_once(self):
        a = await self.alarms.add("tab", 6, 30, [1, 2, 3, 4, 5], "")
        self.assertEqual((a["repeat"], a["nextAt"]), ("Weekdays", ts(2026, 10, 23, 6, 30)))
        self.assertEqual(say_repeat({1, 2, 3, 4, 5}), "on weekdays")
        self.now = ts(2026, 10, 23, 6, 30)
        await self.alarms.check()
        await self.alarms.stop("tab", a["id"])
        got = self.alarms.get("tab", a["id"])
        self.assertEqual((got["state"], got["enabled"], got["nextAt"]), ("set", True, ts(2026, 10, 26, 6, 30)))  # Monday
        # "Skip my weekday alarm tomorrow" (Sunday night -> Monday): Tuesday is next, and only Monday is skipped.
        self.now = ts(2026, 10, 25, 21)
        got = await self.alarms.skip_next("tab", a["id"])
        self.assertEqual((got["skip"], got["nextAt"]), ("2026-10-26", ts(2026, 10, 27, 6, 30)))
        self.now = ts(2026, 10, 26, 6, 31)
        await self.alarms.check()
        self.assertEqual(self.alarms.get("tab", a["id"])["state"], "set")
        self.now = ts(2026, 10, 27, 6, 30)
        await self.alarms.check()
        got = self.alarms.get("tab", a["id"])
        self.assertEqual((got["state"], got["skip"]), ("ringing", None))

    async def test_snooze_rings_again_then_waits_for_the_next_day(self):
        a = await self.alarms.add("tab", 7, 0, list(range(7)))
        self.now = ts(2026, 10, 23, 7)
        await self.alarms.check()
        got = await self.alarms.snooze("tab", a["id"], 540)
        self.assertEqual((got["state"], got["snoozeUntil"], got["nextAt"]), ("snoozed", self.now + 540, self.now + 540))
        self.now += 539
        await self.alarms.check()
        self.assertEqual(self.alarms.get("tab", a["id"])["state"], "snoozed")
        self.now += 1
        await self.alarms.check()
        self.assertEqual(self.alarms.get("tab", a["id"])["state"], "ringing")
        got = await self.alarms.stop("tab", a["id"])
        self.assertEqual((got["state"], got["nextAt"]), ("set", ts(2026, 10, 24, 7)))
        # The snooze can be called off before it rings again (the ✕ on its card).
        self.now = ts(2026, 10, 24, 7)
        await self.alarms.check()
        await self.alarms.snooze("tab", a["id"], 300)
        got = await self.alarms.stop("tab", a["id"])
        self.assertEqual((got["state"], got["nextAt"]), ("set", ts(2026, 10, 25, 7)))

    async def test_daylight_saving_keeps_the_clock_time(self):
        a = await self.alarms.add("tab", 7, 0, list(range(7)))
        self.now = ts(2026, 10, 31, 8)  # Saturday; clocks go back early Sunday Nov 1
        got = self.alarms.get("tab", a["id"])
        self.assertEqual((self.local(got["nextAt"]).hour, self.local(got["nextAt"]).minute), (7, 0))
        self.assertEqual(got["nextAt"] - ts(2026, 10, 31, 7), 25 * 3600)
        # A time the spring change skips (2:30 on March 8) rings when the clock shows 3:30.
        b = await self.alarms.add("tab", 2, 30, [0])
        self.now = ts(2026, 3, 7, 12)
        got = self.alarms.get("tab", b["id"])
        self.assertEqual((self.local(got["nextAt"]).hour, self.local(got["nextAt"]).minute), (3, 30))

    async def test_kept_through_a_restart_and_missed_ones_let_go(self):
        a = await self.alarms.add("tab", 7, 0)
        b = await self.alarms.add("tab", 8, 0, [1, 2, 3, 4, 5], "work")
        again = self.make()  # the hub restarted
        self.assertEqual([x["id"] for x in again.list("tab")], [a["id"], b["id"]])
        self.now = ts(2026, 10, 23, 7, 0) + 5
        await again.check()
        self.assertEqual(again.get("tab", a["id"])["state"], "ringing")
        # The hub was off at 8:00 and back at 11:00: not rung three hours late; the weekday one waits for Monday.
        await again.stop("tab", a["id"])
        self.now = ts(2026, 10, 23, 11)
        await again.check()
        got = again.get("tab", b["id"])
        self.assertEqual((got["state"], got["nextAt"]), ("set", ts(2026, 10, 26, 8)))
        c = await again.add("tab", 12, 0)
        self.now = ts(2026, 10, 23, 15)
        await again.check()
        got = again.get("tab", c["id"])
        self.assertEqual((got["state"], got["enabled"]), ("set", False))

    async def test_edits_names_and_limits(self):
        a = await self.alarms.add("tab", 7, 0, [], "gym", on_date=date(2026, 10, 25))
        self.assertEqual((a["name"], a["nextAt"], self.alarms.say_when(a)), ("gym alarm", ts(2026, 10, 25, 7), "on Sunday"))
        got = await self.alarms.update("tab", a["id"], hour=7, minute=30, days=[6])
        self.assertEqual((got["repeat"], got["nextAt"]), ("Saturdays", ts(2026, 10, 24, 7, 30)))
        self.assertEqual(repeat_title({1, 3}), "Mondays and Wednesdays")
        self.assertEqual(repeat_title({1, 3, 5}), "Mon Wed Fri")
        self.assertEqual(repeat_title(set(range(7))), "Every day")
        with self.assertRaises(ValueError):
            await self.alarms.add("tab", 25, 0)
        self.assertTrue(await self.alarms.remove("tab", a["id"]))
        self.assertFalse(await self.alarms.remove("tab", a["id"]))
        self.assertIn("tab", self.heard)


class AlarmApiTests(unittest.IsolatedAsyncioTestCase):
    """The whole thing over HTTP, as a wall tablet: what's said, the questions back, the touch screens."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.h.voice_stt, self.h.voice_tts = self.stt, self.tts = FakeSTT(), FakeTTS()

        async def fetch(url):
            return b"model" + b"\0" * 2048
        self.h.voice_fetch = fetch
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        await self.client.post("/api/users", json={"username": "hall", "name": "Hallway tablet", "role": "tablet",
                                                   "password": "wall tablet pass"})
        self.tab = Browser(self.client)
        r = await self.tab.post("/api/auth/login", json={"username": "hall", "password": "wall tablet pass", "kind": "tablet"})
        self.auth = {"Authorization": "Bearer " + (await r.json())["token"]}

    async def asyncTearDown(self):
        await self.tab.close()
        await self.client.close()
        self.tmp.cleanup()

    async def say(self, text):
        r = await self.tab.post("/api/voice/utterance", json={"text": text}, headers=self.auth)
        self.assertEqual(r.status, 200, await r.text())
        return await r.json()

    async def call(self, method, path, **kw):
        r = await getattr(self.tab, method)(path, headers=self.auth, **kw)
        return r.status, await r.json()

    async def test_which_timer_is_asked_and_answered(self):
        await self.say("set a pasta timer for 8 minutes")
        await self.say("set an eggs timer for 5 minutes")
        out = await self.say("pause the timer")
        self.assertEqual((out["reply"], out["action"].get("listen")), ("Which one: pasta or eggs?", True))
        out = await self.say("eggs")
        self.assertRegex(out["reply"], r"^Okay, paused with (5 minutes|4 minutes and 5\d seconds) left\.$")
        _, lst = await self.call("get", "/api/voice/timers")
        self.assertEqual({t["label"]: t["state"] for t in lst["timers"]}, {"pasta": "running", "eggs": "paused"})
        out = await self.say("resume the eggs timer")
        self.assertEqual(out["reply"], "Okay, the eggs timer is running again.")
        out = await self.say("Add 4 minutes to the pasta timer")
        self.assertRegex(out["reply"], r"^Okay, the pasta timer has (12|11) minutes left\.$")
        out = await self.say("Make the pasta timer 12 minutes")
        self.assertEqual(out["reply"], "Okay, the pasta timer is now 12 minutes.")
        out = await self.say("restart the pasta timer")
        self.assertEqual(out["reply"], "Okay, the pasta timer is back to 12 minutes.")
        # Something that isn't an answer lets the question go.
        out = await self.say("take a minute off the timer")
        self.assertEqual(out["reply"], "Which one: pasta or eggs?")
        out = await self.say("what time is it")
        self.assertTrue(out["reply"].startswith("It's"))
        out = await self.say("the second one")
        self.assertNotIn("paused", out["reply"])
        out = await self.say("Cancel all timers")
        self.assertEqual(out["reply"], "Okay, both timers are cancelled.")

    async def test_alarms_by_voice(self):
        out = await self.say("set an alarm for 6:30 on weekdays")
        self.assertEqual((out["reply"], out["action"]["listen"]), ("6:30 in the morning or the evening?", True))
        out = await self.say("in the morning")
        self.assertEqual(out["reply"], "Okay, alarm set for 6:30 AM on weekdays.")
        out = await self.say("set a gym alarm for 5:45 am tomorrow")
        self.assertEqual(out["reply"], "Okay, gym alarm set for 5:45 AM tomorrow.")
        out = await self.say("What alarms do I have?")
        self.assertEqual(out["reply"], "You have 2 alarms: the gym alarm at 5:45 AM tomorrow, and 6:30 AM on weekdays.")
        self.assertEqual(out["action"]["show"], "alarms")
        out = await self.say("Turn off my weekday alarm")
        self.assertRegex(out["reply"], r"^Just (tomorrow|today|Monday|\w+day), or every time\?$")
        out = await self.say("every time")
        self.assertEqual(out["reply"], "Okay, your 6:30 AM alarm is off.")
        out = await self.say("Change the gym alarm to 6")
        self.assertEqual(out["reply"], "Okay, the gym alarm is now 6:00 AM, tomorrow.")
        out = await self.say("Cancel the gym alarm")
        self.assertEqual(out["reply"], "Okay, your gym alarm is cancelled.")
        out = await self.say("turn on my weekday alarm")
        self.assertTrue(out["reply"].startswith("Okay, your 6:30 AM alarm is on, on weekdays"))
        out = await self.say("cancel my 6:30 alarm")
        self.assertTrue(out["reply"].startswith("Just "))
        out = await self.say("just that one")
        self.assertTrue(out["reply"].startswith("Okay, your 6:30 AM alarm won't go off "))
        _, lst = await self.call("get", "/api/voice/alarms")
        self.assertIsNotNone(lst["alarms"][0]["skip"])
        out = await self.say("snooze")
        self.assertEqual(out["reply"], "Nothing is ringing.")

    async def test_the_wall_screens_api(self):
        # + New timer, then the timer sheet's buttons.
        s, t = await self.call("post", "/api/voice/timers", json={"seconds": 480, "label": "Pasta"})
        self.assertEqual((s, t["name"]), (201, "Pasta timer"))
        s, t = await self.call("patch", f"/api/voice/timers/{t['id']}", json={"do": "add", "seconds": 60})
        self.assertEqual((s, t["seconds"]), (200, 480))
        s, t = await self.call("patch", f"/api/voice/timers/{t['id']}", json={"do": "pause"})
        self.assertEqual(t["state"], "paused")
        s, _ = await self.call("patch", f"/api/voice/timers/{t['id']}", json={"do": "fly"})
        self.assertEqual(s, 400)
        s, _ = await self.call("post", "/api/voice/timers", json={"seconds": "soon"})
        self.assertEqual(s, 400)
        # New alarm / Edit / on-off / Delete.
        s, a = await self.call("post", "/api/voice/alarms", json={"hour": 6, "minute": 30, "days": [1, 2, 3, 4, 5]})
        self.assertEqual((s, a["repeat"], a["time"]), (201, "Weekdays", "6:30 AM"))
        s, a = await self.call("patch", f"/api/voice/alarms/{a['id']}", json={"enabled": False})
        self.assertEqual((a["enabled"], a["nextAt"]), (False, None))
        s, a = await self.call("patch", f"/api/voice/alarms/{a['id']}", json={"label": "Work", "enabled": True})
        self.assertEqual(a["name"], "Work alarm")
        s, _ = await self.call("patch", f"/api/voice/alarms/{a['id']}", json={"hour": 30})
        self.assertEqual(s, 400)
        s, b = await self.call("post", f"/api/voice/alarms/{a['id']}/snooze", json={})
        self.assertEqual(b["state"], "snoozed")
        self.assertAlmostEqual(b["snoozeUntil"] - time.time(), 9 * 60, delta=5)  # the default "Snooze for"
        s, b = await self.call("post", f"/api/voice/alarms/{a['id']}/stop")
        self.assertEqual(b["state"], "set")
        s, _ = await self.call("delete", f"/api/voice/alarms/{a['id']}")
        self.assertEqual(s, 200)
        s, _ = await self.call("delete", f"/api/voice/alarms/{a['id']}")
        self.assertEqual(s, 404)

    async def test_the_wall_apps_ringing_settings(self):
        s, got = await self.call("put", "/api/voice/ringing", json={"snoozeMinutes": 5, "ringMinutes": 30})
        self.assertEqual((s, got), (200, {"snoozeMinutes": 5, "ringMinutes": 30}))
        s, _ = await self.call("put", "/api/voice/ringing", json={"snoozeMinutes": 7})
        self.assertEqual(s, 400)
        s, info = await self.call("get", "/api/voice")
        self.assertEqual(info["ringing"], {"snoozeMinutes": 5, "ringMinutes": 30})
        s, a = await self.call("post", "/api/voice/alarms", json={"hour": 6, "minute": 0})
        s, b = await self.call("post", f"/api/voice/alarms/{a['id']}/snooze", json={})
        self.assertAlmostEqual(b["snoozeUntil"] - time.time(), 5 * 60, delta=5)

    async def test_why_it_cant_listen(self):
        s, info = await self.call("get", "/api/voice")
        self.assertIn(info["reason"], ("the wake word is still downloading", ""))


if __name__ == "__main__":
    unittest.main()
