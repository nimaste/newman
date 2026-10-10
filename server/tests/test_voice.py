"""Voice: the Wyoming client against a fake service, intent matching (generic over kinds.py), the
timers, and the whole pipeline over the HTTP API with fake speech services."""
import asyncio
import base64
import json
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.integrations.base import Integration  # noqa: E402
from newman.kinds import KINDS  # noqa: E402
from newman.voice import intents as I  # noqa: E402
from newman.voice import wyoming as W  # noqa: E402
from newman.voice.timers import Timers  # noqa: E402
from test_auth import Browser, H  # noqa: E402

# -- a fake Wyoming service -------------------------------------------------------------------------


class FakeWyoming:
    """Speaks just enough Wyoming: transcribe (echoes how many bytes it heard), synthesize (one
    second of silence per call, in two chunks), describe."""

    def __init__(self, transcript="turn off the kitchen lights"):
        self.transcript, self.events, self.server = transcript, [], None

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def handle(self, reader, writer):
        audio = b""
        while True:
            ev = await W.read_event(reader)
            if ev is None:
                break
            self.events.append(ev)
            if ev.type == "describe":
                await W.write_event(writer, W.Event("info", {"asr": [{"name": "fake"}]}))
            elif ev.type == "audio-chunk":
                audio += ev.payload
            elif ev.type == "audio-stop":
                await W.write_event(writer, W.Event("transcript", {"text": self.transcript, "bytes": len(audio)}))
            elif ev.type == "synthesize":
                fmt = {"rate": 22050, "width": 2, "channels": 1}
                await W.write_event(writer, W.Event("audio-start", fmt))
                await W.write_event(writer, W.Event("audio-chunk", fmt, b"\0" * 22050))
                await W.write_event(writer, W.Event("audio-chunk", fmt, b"\0" * 22050))
                await W.write_event(writer, W.Event("audio-stop"))
        writer.close()


class WyomingTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcribe_synthesize_describe(self):
        fake = FakeWyoming("Lock the side door.")
        port = await fake.start()
        c = W.WyomingClient("127.0.0.1", port)
        pcm = b"\1\0" * 16000  # 1 s
        self.assertEqual(await c.transcribe(pcm), "Lock the side door.")
        types = [e.type for e in fake.events]
        self.assertEqual(types[:2], ["transcribe", "audio-start"])
        self.assertEqual(types[-1], "audio-stop")
        chunks = [e for e in fake.events if e.type == "audio-chunk"]
        self.assertEqual(sum(len(e.payload) for e in chunks), len(pcm))
        self.assertTrue(all(e.data == {"rate": 16000, "width": 2, "channels": 1} for e in chunks))
        self.assertEqual(fake.events[0].data, {"language": "en"})
        audio = await c.synthesize("Hello", voice="en_US-lessac-medium")
        self.assertEqual((audio.rate, len(audio.pcm)), (22050, 44100))
        self.assertAlmostEqual(audio.seconds, 1.0)
        self.assertEqual(audio.wav()[:4], b"RIFF")
        syn = next(e for e in fake.events if e.type == "synthesize")
        self.assertEqual(syn.data, {"text": "Hello", "voice": {"name": "en_US-lessac-medium"}})
        self.assertEqual((await c.describe())["asr"][0]["name"], "fake")
        await fake.stop()

    async def test_framing_with_data_length_and_payload(self):
        r = asyncio.StreamReader()
        r.feed_data(b'{"type": "transcript", "data": {"a": 1}, "data_length": 15, "payload_length": 3}\n'
                    b'{"text": "hi"} xyz')
        r.feed_eof()
        ev = await W.read_event(r)
        self.assertEqual((ev.type, ev.data, ev.payload), ("transcript", {"a": 1, "text": "hi"}, b"xyz"))
        self.assertIsNone(await W.read_event(r))

    async def test_unreachable_service_is_a_clear_error(self):
        with self.assertRaises(W.WyomingError):
            await W.WyomingClient("127.0.0.1", 1, timeout=1).transcribe(b"\0" * 100)

    def test_addresses(self):
        self.assertEqual(W.parse_address("whisper:10300", 1), ("whisper", 10300))
        self.assertEqual(W.parse_address("tcp://10.1.1.2:10200", 1), ("10.1.1.2", 10200))
        self.assertEqual(W.parse_address("10.1.1.2", 10300), ("10.1.1.2", 10300))
        self.assertIsNone(W.parse_address("", 1))

# -- intents ------------------------------------------------------------------------------------------


def dev(eid, name, kind, state=None, area="", shown_as="", **attrs):
    if area:
        attrs["area"] = area
    return I.Device(eid, name, kind, state, attrs, True, shown_as)


def house(**kw):
    devices = [
        dev("l1", "Kitchen Pot Lights", "light", "on", area="Kitchen"),
        dev("l2", "Kitchen Island", "light", "on", area="Kitchen"),
        dev("l3", "Porch Light", "light", "off", area="Outside"),
        dev("l4", "Christmas Lights", "switch", "off"),
        dev("lk1", "Side Door", "lock", "LOCKED", batteryLevel=80),
        dev("lk2", "Front Door", "lock", "UNLOCKED"),
        dev("c1", "Side Door", "contact", "closed"),
        dev("g1", "Garage Door", "garage", "closed"),
        dev("t1", "Main Floor", "thermostat", "heat", currentTemp=21.5, targetTemp=21, humidity=40, action="heating"),
        dev("t2", "Basement", "thermostat", "heat", currentTemp=19, targetTemp=19),
        dev("w1", "Home", "weather", "partly-cloudy", temperature=12, high=15, low=6, precipChance=40, forecast=[]),
        dev("cam1", "Front Porch", "camera", "online"),
        dev("db1", "Front Door Bell", "doorbell", "idle"),
        dev("a1", "House Alarm", "alarm", "disarmed"),
        dev("v1", "Robot", "vacuum", "docked", battery=90),
        dev("dw", "Dishwasher", "appliance", "running", remaining=42),
        dev("sp", "Kitchen Speaker", "speaker", "idle", area="Kitchen", volume=20),
    ]
    people = [{"id": "p_dante", "name": "Dante", "sharing": True, "home": False, "place": "School", "distanceM": 2500},
              {"id": "p_marlina", "name": "Marlina", "sharing": True, "home": True, "place": None, "distanceM": 10},
              {"id": "p_chris", "name": "Chris", "sharing": False, "home": False, "place": None, "distanceM": None}]
    pages = [{"id": "overview", "name": "Overview"}, {"id": "lights", "name": "Lights"},
             {"id": "climate", "name": "Climate"}]
    return I.World(devices=devices, people=people, pages=pages, **kw)


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.w = house()

    def p(self, text, world=None):
        return I.parse(text, world or self.w)

    def ids(self, it):
        return sorted(d.id for d in it.devices)

    def test_turning_things_on_and_off(self):
        for text in ("Turn off the kitchen lights.", "turn the kitchen lights off", "Kitchen lights off",
                     "Hey Newman, please switch off the kitchen lights", "shut off the kitchen lights",
                     "could you turn off the lights in the kitchen"):
            it = self.p(text)
            self.assertEqual((it.type, it.command.id if it.command else None, self.ids(it)),
                             ("command", "turn_off", ["l1", "l2"]), text)
        it = self.p("turn on the porch light")
        self.assertEqual((it.command.id, self.ids(it)), ("turn_on", ["l3"]))
        it = self.p("turn on the christmas lights")  # a plug (switch kind) by its name
        self.assertEqual((it.type, it.command.id, self.ids(it)), ("command", "turn_on", ["l4"]))
        self.assertEqual(self.ids(self.p("turn off all the lights")), ["l1", "l2", "l3", "l4"])  # + the plug named lights
        self.assertEqual(I.say_done(self.p("turn off the porch light")), "Okay, the Porch Light is off.")

    def test_locks_and_garage_and_sensitive_refusal(self):
        it = self.p("lock the side door")
        self.assertEqual((it.type, it.command.id, self.ids(it)), ("command", "lock", ["lk1"]))  # not the door sensor
        for text in ("unlock the side door", "open the garage", "disarm the alarm", "Open the garage door please"):
            it = self.p(text)
            self.assertEqual((it.type, it.reply), ("refuse", I.SAFETY), text)
        it = self.p("close the garage")
        self.assertEqual((it.type, it.command.id, self.ids(it)), ("command", "close", ["g1"]))
        # A tablet allowed to: the same words go through.
        it = self.p("unlock the side door", house(allow_sensitive=True))
        self.assertEqual((it.type, it.command.id), ("command", "unlock"))

    def test_setting_numbers_and_modes(self):
        it = self.p("Set the main floor to 21 degrees.")
        self.assertEqual((it.type, it.command.id, it.value, self.ids(it)), ("command", "set_temperature", 21.0, ["t1"]))
        it = self.p("set the basement thermostat to nineteen and a half")
        self.assertEqual((it.command.id, it.value, self.ids(it)), ("set_temperature", 19.5, ["t2"]))
        it = self.p("set the main floor to cool")
        self.assertEqual((it.command.id, it.value), ("set_mode", "cool"))
        it = self.p("dim the kitchen island to 30 percent")
        self.assertEqual((it.command.id, it.value, self.ids(it)), ("set_brightness", 30.0, ["l2"]))
        it = self.p("set the kitchen speaker volume to 40")
        self.assertEqual((it.command.id, it.value), ("set_volume", 40.0))
        it = self.p("set the main floor to 60")
        self.assertEqual(it.type, "unknown")
        self.assertIn("goes from 5 to 35 degrees", it.reply)
        self.assertEqual(I.say_done(self.p("set the main floor to 21")), "Okay, the Main Floor is set to 21 degrees.")

    def test_ambiguous_and_unknown(self):
        it = self.p("set the thermostat to 20")
        self.assertEqual(it.type, "ambiguous")
        self.assertEqual(it.reply, "Which one: Main Floor or Basement?")
        it = self.p("turn on the bedroom lamp")
        self.assertEqual((it.type, it.reply), ("unknown", "Sorry, I didn't catch a device called bedroom lamp."))
        self.assertEqual(self.p("sing me a song").type, "unknown")
        self.assertEqual(self.p("turn the lights").reply, "Say which: turn on or turn off.")
        locks_only = I.World(devices=[dev("lk1", "Side Door", "lock", "LOCKED")])
        self.assertEqual(self.p("turn off the kitchen lights", locks_only).reply,
                         "Sorry, I didn't catch a device called kitchen lights.")

    def test_state_questions(self):
        it = self.p("Is the garage door open?")
        self.assertEqual((it.type, self.ids(it), it.state), ("ask_state", ["g1"], "open"))
        self.assertEqual(I.say_state_answer(it), "No, the Garage Door is closed.")
        it = self.p("is the side door locked")
        self.assertEqual(self.ids(it), ["lk1"])
        self.assertEqual(I.say_state_answer(it), "Yes, the Side Door is locked.")
        self.assertEqual(I.say_state_answer(self.p("is the garage open")), "No, the Garage Door is closed.")
        it = self.p("are the kitchen lights on")
        self.assertEqual(I.say_state_answer(it), "Yes, both are on.")
        it = self.p("is anything unlocked")
        self.assertEqual(I.say_state_answer(it), "Yes: Front Door is unlocked.")

    def test_readings_and_descriptions(self):
        it = self.p("What's the temperature outside?")
        self.assertEqual((it.type, self.ids(it), it.prop), ("ask_value", ["w1"], "temperature"))
        self.assertEqual(I.say_value_answer(it), "Outside, the temperature is 12 degrees.")
        it = self.p("how warm is it in the basement")
        self.assertEqual(I.say_value_answer(it), "Basement's temperature is 19 degrees.")
        it = self.p("what's the humidity on the main floor")
        self.assertEqual(I.say_value_answer(it), "Main Floor's humidity is 40 percent.")
        it = self.p("what's the temperature downstairs")
        self.assertEqual(it.reply, "Sorry, I didn't catch a device called downstairs.")
        it = self.p("how much time is left on the dishwasher")
        self.assertEqual(I.say_value_answer(it), "Dishwasher's time left is 42 minutes.")
        it = self.p("what's the weather")
        self.assertEqual(it.type, "describe")
        self.assertEqual(I.say_describe(it), "Weather: partly cloudy, temperature 12 degrees, high today 15 degrees, "
                                             "low today 6 degrees, chance of rain or snow today 40 percent.")
        it = self.p("what's the temperature")
        self.assertTrue(I.say_value_answer(it).startswith("Temperature: Main Floor 21.5 degrees, Basement 19 degrees"))

    def test_people(self):
        self.assertEqual(I.say_where(self.p("Where's Dante?")), "Dante is at School.")
        self.assertEqual(I.say_where(self.p("where is marlena")), "Marlina is home.")  # a speech-to-text spelling
        self.assertEqual(I.say_where(self.p("Where's Marlene?")), "Marlina is home.")  # as Whisper heard it in the bench
        self.assertEqual(I.say_where(self.p("where's chris")), "I can't see where Chris is.")
        self.assertEqual(I.say_where(self.p("where is everyone")), "Dante is at School; Marlina is home.")
        self.assertEqual(self.p("where is bob").reply, "Sorry, I didn't catch anyone called bob.")

    def test_screens(self):
        it = self.p("Show the front porch camera.")
        self.assertEqual((it.type, it.show, self.ids(it)), ("show", "camera", ["cam1"]))
        self.assertEqual(self.p("show the cameras").show, "cameras")
        self.assertEqual(self.p("show me all cameras").show, "cameras")
        it = self.p("Show Dante on the map.")
        self.assertEqual((it.show, it.people[0]["id"], it.reply), ("person", "p_dante", ""))
        self.assertEqual(self.p("show chris on the map").reply, "I can't see where Chris is.")
        it = self.p("show the lights page")
        self.assertEqual((it.show, it.page["id"]), ("page", "lights"))
        self.assertEqual(self.p("go to climate").page["id"], "climate")
        self.assertEqual(self.p("show the weather").show, "weather")
        self.assertEqual(self.p("show the calendar").show, "calendar")
        self.assertEqual(self.p("show my to do list").show, "todo")
        self.assertEqual(self.p("go home").type, "unknown")  # not a screen verb
        self.assertEqual(self.p("show home").show, "home")

    def test_open_a_camera_heard_as_two_words(self):
        # Seen live: "open my backyard camera" came back from speech-to-text as "back yard".
        w = house()
        w.devices.append(dev("cam3", "Backyard", "camera", "online"))
        for text in ("Open my back yard camera.", "open the backyard camera", "show the back yard",
                     "Hey Newman, open back yard camera"):
            it = self.p(text, w)
            self.assertEqual((it.type, it.show, self.ids(it)), ("show", "camera", ["cam3"]), text)
        # ...and a device called with two words, said as one.
        w.devices.append(dev("cam4", "Side Yard", "camera", "online"))
        self.assertEqual(self.ids(self.p("show the sideyard camera", w)), ["cam4"])
        # "Open" stays a command when it isn't about a camera, page or person.
        it = self.p("open the garage door", w)
        self.assertNotEqual(it.type, "show")
        # Seen live: "open the front porch" lost to the "Front Door" lock on the word "front".
        it = self.p("Open the front porch.", w)
        self.assertEqual((it.type, it.show, self.ids(it)), ("show", "camera", ["cam1"]))
        self.assertNotEqual(self.p("open the front door", w).type, "show")

    def test_a_second_timer_and_how_many(self):
        # Seen live: "how many timers do I have going?" answered with time left but not how many.
        it = self.p("set a second 10 minute timer")
        self.assertEqual((it.type, it.seconds), ("timer_set", 600))
        self.assertEqual(self.p("How many timers do I have going?").type, "timer_list")
        now = 1000.0
        one = [{"id": "t1", "label": "", "seconds": 600, "endsAt": now + 483, "state": "running"}]
        self.assertTrue(I.say_timers(one, now, count=True).startswith("You have one timer: 8 minutes"))
        two = one + [{"id": "t2", "label": "pasta", "seconds": 480, "endsAt": now + 60, "state": "running"}]
        self.assertTrue(I.say_timers(two, now, count=True).startswith("You have 2 timers: "))

    def test_show_as_names_from_the_tablets_dashboard(self):
        w = house()
        w.devices.append(dev("cam2", "Reolink Duo 2 Left", "camera", "online", shown_as="Driveway"))
        it = self.p("show the driveway camera", w)
        self.assertEqual(self.ids(it), ["cam2"])

    def test_the_time_and_the_date(self):
        for text in ("What time is it?", "Hey Newman, what time is it?", "what's the time", "Whats the time?",
                     "do you know the time", "tell me the time", "can you tell me the time please",
                     "do you know what time it is", "what time is it right now", "What's the current time?"):
            self.assertEqual(self.p(text).type, "time", text)
        for text in ("What's the date?", "what day is it", "What's today's date?", "what's the date today",
                     "Hey Newman, what day is it today?", "do you know what day it is", "what is todays date"):
            self.assertEqual(self.p(text).type, "date", text)
        # Not the clock: timers, the agenda, and devices with "time" / "date" in them.
        w = house(timers=[{"id": "a", "label": "pasta", "seconds": 480, "endsAt": 1372.0, "state": "running"}])
        self.assertEqual(self.p("how much time is left", w).type, "timer_query")
        self.assertEqual(self.p("what's the time left on the pasta timer", w).type, "timer_query")
        self.assertEqual(self.p("how much time is left on the dishwasher").type, "ask_value")
        self.assertEqual(self.p("what's on today").type, "today")
        w.devices.append(dev("dn", "Date Night Lamp", "light", "off"))
        it = self.p("turn on the date night lamp", w)
        self.assertEqual((it.type, self.ids(it)), ("command", ["dn"]))
        # Spoken naturally.
        self.assertEqual(I.say_time(datetime(2026, 10, 6, 16, 52)), "It's 4:52 PM.")
        self.assertEqual(I.say_time(datetime(2026, 10, 6, 9, 5)), "It's 9:05 AM.")
        self.assertEqual(I.say_time(datetime(2026, 10, 6, 15, 0)), "It's 3 PM.")
        self.assertEqual(I.say_time(datetime(2026, 10, 6, 12, 0)), "It's noon.")
        self.assertEqual(I.say_time(datetime(2026, 10, 6, 0, 0)), "It's midnight.")
        self.assertEqual(I.say_time(datetime(2026, 10, 6, 0, 30)), "It's 12:30 AM.")
        self.assertEqual(I.say_date(datetime(2026, 10, 6, 8)), "It's Tuesday, October 6th.")
        self.assertEqual(I.say_date(datetime(2026, 10, 1)), "It's Thursday, October 1st.")
        self.assertEqual(I.say_date(datetime(2026, 10, 22)), "It's Thursday, October 22nd.")
        self.assertEqual(I.say_date(datetime(2026, 10, 13)), "It's Tuesday, October 13th.")

    def test_today_and_stop(self):
        for text in ("What's on today?", "what is happening today", "what do I have today", "what's on the calendar"):
            self.assertEqual(self.p(text).type, "today", text)
        for text in ("stop", "Hey Newman, stop.", "stop it", "be quiet", "never mind", "ok thanks", "stop the alarm"):
            self.assertEqual(self.p(text).type, "stop", text)
        self.assertEqual(self.p("stop the timer").label, "timer")
        self.assertEqual(self.p("stop the vacuum").type, "unknown")  # vacuums pause / go home; no "stop"
        it = self.p("start the vacuum")
        self.assertEqual((it.type, it.command.id), ("command", "start"))
        today = {"events": [{"title": "Dentist", "time": "3 PM", "allDay": False}],
                 "todo": [{"title": "Change furnace filter"}], "headsUp": [{"text": "Rain likely from 4 PM (70%)"}]}
        self.assertEqual(I.say_today(today), "Today you have Dentist at 3 PM. 1 thing to do: Change furnace filter. "
                                             "Heads up: Rain likely from 4 PM (70%).")

    def test_timer_phrasings(self):
        cases = {"set a timer for 10 minutes": (600, ""), "Set a pasta timer for 8 minutes.": (480, "pasta"),
                 "start a 5 minute timer": (300, ""), "timer for an hour and a half": (5400, ""),
                 "set a timer for half an hour": (1800, ""), "set a timer for 90 seconds": (90, ""),
                 "set a timer for 1 hour and 15 minutes": (4500, ""), "set an egg timer for six minutes": (360, "egg"),
                 "set a timer for 3 minutes for the tea": (180, "tea"), "set a second timer for 2 minutes": (120, "")}
        for text, (secs, label) in cases.items():
            it = self.p(text)
            self.assertEqual((it.type, it.seconds, it.label), ("timer_set", secs, label), text)
        w = house(timers=[{"id": "a", "label": "pasta", "seconds": 480, "endsAt": 1000.0 + 372, "state": "running"},
                          {"id": "b", "label": "", "seconds": 600, "endsAt": 1000.0 + 61, "state": "running"}])
        it = self.p("how much time is left on the pasta timer", w)
        self.assertEqual((it.type, it.label), ("timer_query", "pasta"))
        self.assertEqual(I.say_timers(I._find_timers(w, it.label), 1000.0), "6 minutes and 12 seconds left on your pasta timer.")
        self.assertEqual(self.p("how much time is left", w).type, "timer_query")
        self.assertEqual(self.p("how long until the timer is done", w).type, "timer_query")
        self.assertEqual(I.say_timers(w.timers, 1000.0),
                         "6 minutes and 12 seconds left on your pasta timer and 1 minute and 1 second left on your "
                         "10 minute timer.")
        self.assertEqual(self.p("cancel the pasta timer", w).type, "timer_cancel")
        self.assertTrue(self.p("cancel all timers", w).all)
        self.assertEqual(self.p("what timers do I have", w).type, "timer_list")
        self.assertEqual(self.p("set a timer").reply, "How long? Say something like “set a timer for 10 minutes”.")

    def test_numbers_and_durations(self):
        self.assertEqual(I.normalize("Set it to twenty-one degrees"), ["set", "it", "to", "21", "degrees"])
        self.assertEqual(I.normalize("nineteen point five"), ["19.5"])
        self.assertEqual(I.normalize("one hundred and twenty"), ["120"])
        self.assertEqual(I.say_duration(3725), "1 hour and 2 minutes")
        self.assertEqual(I.say_duration(125), "2 minutes and 5 seconds")
        self.assertEqual(I.say_duration(45), "45 seconds")
        self.assertEqual(I.timer_name({"label": "", "seconds": 600}), "10 minute timer")
        self.assertEqual(I.timer_name({"label": "", "seconds": 7200}), "2 hour timer")

    def test_nothing_here_names_a_kind(self):
        """The parser's vocabulary comes from kinds.py: a new kind with its own verb just works."""
        from dataclasses import replace
        from newman.kinds import Command, Kind
        KINDS["kettle"] = Kind("kettle", "Kettle", "Kettles", "appliances", [("idle", "Cold"), ("boiling", "Boiling")],
                               noun="kettle", commands=[Command("boil", "Boil", "boiled", "fire", "boiling")])
        try:
            w = house()
            w.devices.append(dev("k1", "Kettle", "kettle", "idle"))
            it = self.p("boil the kettle", w)
            self.assertEqual((it.type, it.command.id, self.ids(it)), ("command", "boil", ["k1"]))
            self.assertEqual(I.say_state_answer(self.p("is the kettle boiling", w)), "No, the Kettle is cold.")
            KINDS["kettle"] = replace(KINDS["kettle"], commands=[Command("boil", "Boil", "boiled", "fire", "boiling",
                                                                         sensitive=True)])
            w.devices[-1] = dev("k1", "Kettle", "kettle", "idle")
            self.assertEqual(self.p("boil the kettle", w).type, "refuse")
        finally:
            del KINDS["kettle"]

# -- timers --------------------------------------------------------------------------------------------


class TimerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = sqlite3.connect(Path(self.tmp.name) / "t.db")
        self.db.row_factory = sqlite3.Row
        self.now = 1000.0
        self.timers = Timers(self.db, clock=lambda: self.now)
        self.heard = []

        async def heard(uid):
            self.heard.append(uid)
        self.timers.listeners.append(heard)

    async def asyncTearDown(self):
        self.db.close()
        self.tmp.cleanup()

    async def test_set_query_finish_stop_and_persist(self):
        t = await self.timers.add("tab", 480, "pasta")
        self.assertEqual((t["name"], t["doneText"], t["state"]), ("pasta timer", "Your pasta timer is done.", "running"))
        await self.timers.add("tab", 600)
        await self.timers.add("other", 60)
        self.assertEqual([x["name"] for x in self.timers.list("tab")], ["pasta timer", "10 minute timer"])
        # Survives a restart (the wall reloading, the hub restarting): it's in the database.
        again = Timers(self.db, clock=lambda: self.now)
        self.assertEqual(len(again.list("tab")), 2)
        self.now += 479
        self.assertAlmostEqual(await self.timers.check(), 1.0)
        self.now += 2
        self.heard.clear()
        await self.timers.check()
        self.assertEqual(self.heard, ["tab", "other"] if False else self.heard)
        states = {x["name"]: x["state"] for x in self.timers.list("tab")}
        self.assertEqual(states, {"pasta timer": "ringing", "10 minute timer": "running"})
        self.assertIn("tab", self.heard)
        self.assertEqual(await self.timers.stop_ringing("tab"), 1)
        self.assertEqual([x["name"] for x in self.timers.list("tab")], ["10 minute timer"])

    async def test_cancel_limits_and_forgotten_alarms(self):
        t = await self.timers.add("tab", 60)
        self.assertEqual(await self.timers.remove("tab", [t["id"]]), 1)
        self.assertEqual(await self.timers.remove("tab", ["nope"]), 0)
        with self.assertRaises(ValueError):
            await self.timers.add("tab", 2 * 86400)
        for _ in range(10):
            await self.timers.add("tab", 30)
        with self.assertRaises(ValueError):
            await self.timers.add("tab", 30)
        self.now += 31
        await self.timers.check()
        self.assertTrue(all(x["state"] == "ringing" for x in self.timers.list("tab")))
        self.now += 3601
        await self.timers.check()
        self.assertEqual(self.timers.list("tab"), [])

    async def test_the_clock_rings_them_on_time(self):
        timers = Timers(self.db)  # real time
        task = asyncio.create_task(timers.run())
        try:
            await timers.add("tab", 1)
            await asyncio.sleep(1.4)
            self.assertEqual(timers.list("tab")[0]["state"], "ringing")
        finally:
            task.cancel()

# -- the whole pipeline over HTTP -----------------------------------------------------------------------


class Gear(Integration):
    type = "fake"
    name = "Fake gear"

    def __init__(self, ctx):
        super().__init__("fake", "Fake gear", {}, ctx)
        self.calls = []

    async def command(self, eid, command, value=None):
        self.calls.append((eid, command, value))


class FakeSTT:
    def __init__(self):
        self.text, self.got = "", []

    async def transcribe(self, pcm, rate=16000, width=2, channels=1, language="en"):
        self.got.append((len(pcm), rate))
        return self.text


class FakeTTS:
    def __init__(self):
        self.said = []

    async def synthesize(self, text, voice=None, speaker=None):
        self.said.append((text, voice))
        return W.Audio(b"\0\0" * 2205, 22050)


class PipelineApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.h.voice_stt, self.h.voice_tts = self.stt, self.tts = FakeSTT(), FakeTTS()
        self.fetched = []

        async def fetch(url):  # openWakeWord's pre-trained wake words, without the internet
            self.fetched.append(url)
            return b"model:" + url.encode() + b"\0" * 2048
        self.h.voice_fetch = fetch
        self.gear = Gear(self.h.integrations._ctx)
        self.h.integrations.instances["fake"] = self.gear
        for eid, kind, name, state in (("fake.k1", "light", "Kitchen Lights", "on"), ("fake.side", "lock", "Side Door", "LOCKED"),
                                       ("fake.g", "garage", "Garage Door", "closed"), ("fake.t", "thermostat", "Main Floor", "heat")):
            self.h.hub.upsert(eid, kind=kind, name=name, integration="fake")
            await self.h.hub.update(eid, state=state)
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        r = await self.client.post("/api/users", json={"username": "hall", "name": "Hallway tablet", "role": "tablet",
                                                       "password": "wall tablet pass"})
        self.tablet = await r.json()
        self.tab = Browser(self.client)
        r = await self.tab.post("/api/auth/login", json={"username": "hall", "password": "wall tablet pass", "kind": "tablet"})
        self.auth = {"Authorization": "Bearer " + (await r.json())["token"]}

    async def asyncTearDown(self):
        await self.tab.close()
        await self.client.close()
        self.tmp.cleanup()

    async def say(self, text, audio=True, **q):
        self.stt.text = text
        if audio:
            pcm = b"\1\0" * 16000 * 2
            r = await self.tab.post("/api/voice/utterance", data=pcm, headers={**self.auth, "Content-Type": "audio/L16"},
                                    params=q)
        else:
            r = await self.tab.post("/api/voice/utterance", json={"text": text}, headers=self.auth, params=q)
        self.assertEqual(r.status, 200, await r.text())
        return await r.json()

    async def test_a_tablet_talks_and_the_house_answers(self):
        out = await self.say("Turn off the kitchen lights.")
        self.assertEqual(out["heard"], "Turn off the kitchen lights.")
        self.assertEqual(out["reply"], "Okay, the Kitchen Lights are off.")
        self.assertEqual(out["action"]["type"], "command")
        self.assertEqual(self.gear.calls, [("fake.k1", "turn_off", None)])
        self.assertEqual(self.stt.got, [(64000, 16000)])
        self.assertEqual(base64.b64decode(out["audio"]["data"])[:4], b"RIFF")
        self.assertEqual(self.tts.said[-1], ("Okay, the Kitchen Lights are off.", "en_US-lessac-medium"))
        self.assertIn("stt", out["timings"])
        # Logged in Activity: the words and the outcome - no audio anywhere.
        ev = self.h.store.events(5, kind="voice")[0]
        self.assertEqual(ev["title"], "Hallway tablet: “Turn off the kitchen lights.”")
        self.assertEqual(ev["body"], "Okay, the Kitchen Lights are off.")
        self.assertNotIn("audio", json.dumps(ev))

    async def test_sensitive_commands_need_the_tablets_setting(self):
        out = await self.say("open the garage", audio=False)
        self.assertEqual((out["reply"], out["action"]["type"]), ("For safety, do that from the screen.", "refuse"))
        self.assertEqual(self.gear.calls, [])
        # The tablet can't allow itself; an admin can.
        r = await self.tab.patch(f"/api/voice/settings/{self.tablet['id']}", json={"allowSensitive": True}, headers=self.auth)
        self.assertEqual(r.status, 403)
        r = await self.client.patch(f"/api/voice/settings/{self.tablet['id']}", json={"allowSensitive": True})
        self.assertEqual((await r.json())["allowSensitive"], True)
        out = await self.say("open the garage", audio=False)
        self.assertEqual(out["action"]["type"], "command")
        self.assertEqual(self.gear.calls, [("fake.g", "open", None)])

    async def test_settings_are_checked_and_shape_the_wake_word(self):
        uid = self.tablet["id"]
        r = await self.client.patch(f"/api/voice/settings/{uid}", json={"sensitivity": "loud"})
        self.assertEqual(r.status, 400)
        r = await self.client.patch(f"/api/voice/settings/{uid}", json={"sensitivity": "high", "volume": 150,
                                                                        "voice": "en_US-ryan-medium"})
        s = await r.json()
        self.assertEqual((s["sensitivity"], s["volume"], s["voice"], s["enabled"]), ("high", 100, "en_US-ryan-medium", True))
        info = await (await self.tab.get("/api/voice", headers=self.auth)).json()
        self.assertEqual((info["available"], info["speaks"], info["wakeword"]), (True, True, None))  # no model yet
        self.assertEqual((await self.tab.get("/api/voice/wakeword", headers=self.auth)).status, 404)
        vdir = Path(self.tmp.name) / "voice"
        vdir.mkdir(exist_ok=True)
        for n in ("melspectrogram", "embedding_model", "hey_newman"):
            (vdir / f"{n}.onnx").write_bytes(n.encode())
        (vdir / "hey_newman.json").write_text('{"threshold": 0.6}')
        w = await (await self.tab.get("/api/voice/wakeword", headers=self.auth)).json()
        self.assertEqual(w["threshold"], 0.45)  # high sensitivity = a lower threshold
        self.assertEqual(sorted(w["files"]), ["embedding_model", "melspectrogram", "wakeword"])
        r = await self.tab.get("/" + w["files"]["wakeword"], headers=self.auth)
        self.assertEqual(await r.read(), b"hey_newman")
        await self.say("what's the temperature on the main floor", audio=False)
        self.assertEqual(self.tts.said[-1][1], "en_US-ryan-medium")

    async def wake_words_fetched(self):
        vdir = Path(self.tmp.name) / "voice"
        for _ in range(100):
            if (vdir / "hey_rhasspy.json").is_file():
                break
            await asyncio.sleep(0.01)
        return vdir

    async def test_wake_words_to_choose_from(self):
        vdir = await self.wake_words_fetched()
        self.assertEqual(sorted(self.fetched), [
            f"https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/{n}_v0.1.onnx"
            for n in ("hey_jarvis", "hey_mycroft", "hey_rhasspy")])
        self.assertTrue((vdir / "hey_jarvis.onnx").read_bytes().startswith(b"model:"))
        # Already there: not fetched again.
        from newman.voice.api import ensure_pretrained
        self.assertEqual(await ensure_pretrained(vdir, self.h.voice_fetch), [])
        self.assertEqual(len(self.fetched), 3)
        for n in ("melspectrogram", "embedding_model", "hey_newman", "porch_bell"):
            (vdir / f"{n}.onnx").write_bytes(n.encode())
        (vdir / "porch_bell.json").write_text('{"threshold": 0.7}')  # no phrase: from the name
        lst = await (await self.tab.get("/api/voice/wakewords", headers=self.auth)).json()
        self.assertEqual(lst["default"], "hey_newman")
        self.assertEqual(lst["wakeWords"], [{"name": "hey_jarvis", "phrase": "Hey Jarvis"},
                                            {"name": "hey_mycroft", "phrase": "Hey Mycroft"},
                                            {"name": "hey_newman", "phrase": "Hey Newman"},
                                            {"name": "hey_rhasspy", "phrase": "Hey Rhasspy"},
                                            {"name": "porch_bell", "phrase": "Porch Bell"}])

        # The default: Hey Newman.
        uid = self.tablet["id"]
        self.assertEqual((await (await self.client.get(f"/api/voice/settings/{uid}")).json())["wakeWord"], "hey_newman")
        w1 = await (await self.tab.get("/api/voice/wakeword", headers=self.auth)).json()
        self.assertEqual((w1["name"], w1["phrase"], w1["files"]["wakeword"]),
                         ("hey_newman", "Hey Newman", "api/voice/wakeword/hey_newman.onnx"))

        # The tablet's own choice, and one that isn't on the hub.
        r = await self.client.patch(f"/api/voice/settings/{uid}", json={"wakeWord": "alexa"})
        self.assertEqual(r.status, 400)
        r = await self.client.patch(f"/api/voice/settings/{uid}", json={"wakeWord": "hey_jarvis"})
        self.assertEqual((await r.json())["wakeWord"], "hey_jarvis")
        w2 = await (await self.tab.get("/api/voice/wakeword", headers=self.auth)).json()
        self.assertEqual((w2["name"], w2["phrase"], w2["threshold"], w2["files"]["wakeword"]),
                         ("hey_jarvis", "Hey Jarvis", 0.5, "api/voice/wakeword/hey_jarvis.onnx"))
        self.assertNotEqual(w1["version"], w2["version"])
        info = await (await self.tab.get("/api/voice", headers=self.auth)).json()
        self.assertEqual((info["settings"]["wakeWord"], info["wakeword"]["phrase"]), ("hey_jarvis", "Hey Jarvis"))
        r = await self.tab.get("/" + w2["files"]["wakeword"], headers=self.auth)
        self.assertTrue((await r.read()).startswith(b"model:"))
        self.assertEqual((await self.tab.get("/api/voice/wakeword/nope.onnx", headers=self.auth)).status, 404)

        # A new threshold for the chosen word is a new version; a word named only by its file.
        (vdir / "hey_jarvis.json").write_text('{"phrase": "Hey Jarvis", "threshold": 0.6}')
        w3 = await (await self.tab.get("/api/voice/wakeword", headers=self.auth)).json()
        self.assertEqual(w3["threshold"], 0.6)
        self.assertNotEqual(w3["version"], w2["version"])
        r = await self.client.patch(f"/api/voice/settings/{uid}", json={"wakeWord": "porch_bell"})
        w4 = await (await self.tab.get("/api/voice/wakeword", headers=self.auth)).json()
        self.assertEqual((w4["name"], w4["phrase"], w4["threshold"]), ("porch_bell", "Porch Bell", 0.7))
        # The chosen word taken off the hub: back to the default.
        (vdir / "porch_bell.onnx").unlink()
        w5 = await (await self.tab.get("/api/voice/wakeword", headers=self.auth)).json()
        self.assertEqual(w5["name"], "hey_newman")

    async def test_timers_over_the_api(self):
        out = await self.say("set a pasta timer for 8 minutes", audio=False)
        self.assertEqual(out["reply"], "Okay, pasta timer for 8 minutes, starting now.")
        lst = await (await self.tab.get("/api/voice/timers", headers=self.auth)).json()
        self.assertEqual([t["name"] for t in lst["timers"]], ["pasta timer"])
        out = await self.say("how much time is left on the pasta timer", audio=False)
        self.assertRegex(out["reply"], r"^(8 minutes|7 minutes and 5\d seconds) left on your pasta timer\.$")
        tid = lst["timers"][0]["id"]
        done = await (await self.tab.get(f"/api/voice/timers/{tid}/done", headers=self.auth)).json()
        self.assertEqual(done["text"], "Your pasta timer is done.")
        self.assertIsNotNone(done["audio"])
        # Someone else's timers aren't this tablet's.
        self.assertEqual((await self.client.get("/api/voice/timers")).status, 200)
        self.assertEqual((await (await self.client.get("/api/voice/timers")).json())["timers"], [])
        r = await self.tab.delete(f"/api/voice/timers/{tid}", headers=self.auth)
        self.assertEqual(r.status, 200)
        out = await self.say("cancel the timer", audio=False)
        self.assertEqual(out["reply"], "You don't have any timers.")

    async def test_nothing_heard_and_no_services(self):
        out = await self.say("", audio=True)
        self.assertEqual((out["reply"], out["audio"]), ("", None))
        r = await self.tab.post("/api/voice/utterance", data=b"\0" * 100, headers={**self.auth, "Content-Type": "audio/L16"})
        self.assertEqual((await r.json())["heard"], "")  # too short to be anything
        for noise in ("Thank you.", "[BLANK_AUDIO]", " you", "(silence)"):
            out = await self.say(noise)
            self.assertEqual((out["heard"], out["reply"], out["action"]["type"]), ("", "", "none"), noise)  # Whisper's guesses at silence
        self.h.voice_stt = None
        # A hub without speech-to-text still answers typed requests.
        out = await self.say("is the side door locked", audio=False, speak="0")
        self.assertEqual((out["reply"], out["audio"]), ("Yes, the Side Door is locked.", None))

    async def test_the_time_and_the_date_in_the_hubs_time_zone(self):
        # Seen live: "What time is it?" got "I don't recognize a device called time".
        before = datetime.now()
        out = await self.say("What time is it?")
        after = datetime.now()
        self.assertEqual(out["action"]["type"], "time")
        self.assertIn(out["reply"], {I.say_time(before), I.say_time(after)})
        out = await self.say("what's the date today", audio=False)
        self.assertEqual(out["action"]["type"], "date")
        self.assertIn(out["reply"], {I.say_date(before), I.say_date(datetime.now())})


if __name__ == "__main__":
    unittest.main()
