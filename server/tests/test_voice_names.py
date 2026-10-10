"""Voice: the hub's name index and putting misheard names right (voice/names.py, the pipeline's second look),
"run <automation>", and the Whisper hint (voice/hint.py)."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.voice import hint as HT  # noqa: E402
from newman.voice import intents as I  # noqa: E402
from newman.voice import names as N  # noqa: E402
from newman.voice.pipeline import Pipeline, Sources, VoiceViewer  # noqa: E402
from newman.voice.timers import Timers  # noqa: E402
from test_auth import Browser, H  # noqa: E402
from test_voice import FakeSTT, FakeTTS, Gear  # noqa: E402


def dev(eid, name, kind, state=None, area="", **attrs):
    if area:
        attrs["area"] = area
    return I.Device(eid, name, kind, state, attrs, True, "")


def world(**kw):
    devices = [
        dev("l1", "Kitchen Pot Lights", "light", "on", area="Kitchen"),
        dev("l2", "String Lights", "light", "off", area="Backyard"),
        dev("l3", "Night Light", "light", "off"),
        dev("l4", "Porch Light", "light", "off"),
        dev("lk1", "Side Door", "lock", "LOCKED"),
        dev("cam1", "Front Porch", "camera", "online"),
        dev("dw", "Dishwasher", "appliance", "running"),
    ]
    people = [{"id": "p_massimo", "name": "Massimo", "sharing": True, "home": True, "place": None, "distanceM": 5},
              {"id": "p_dante", "name": "Dante", "sharing": True, "home": False, "place": "School", "distanceM": 2500},
              {"id": "p_marlina", "name": "Marlina", "sharing": True, "home": True, "place": None, "distanceM": 5}]
    autos = [{"id": "a_night", "name": "Good Night", "sensitive": False},
             {"id": "a_morning", "name": "Good Morning", "sensitive": False},
             {"id": "a_leave", "name": "Leaving Home", "sensitive": True},
             {"id": "a_dishes", "name": "Dishes Done", "sensitive": False}]
    recipes = [{"id": "r1", "title": "Fettuccine Alfredo"}, {"id": "r2", "title": "Sesame Chicken"}]
    pages = [{"id": "overview", "name": "Overview"}, {"id": "climate", "name": "Climate"}]
    base = dict(devices=devices, people=people, pages=pages, automations=autos, recipes=recipes)
    base.update(kw)
    return I.World(**base)


def index_of(w):
    """The name index a hub would have for world `w`."""
    idx = N.NameIndex()
    idx.source("devices", lambda: [N.Name(d.name, "device", d.id) for d in w.devices])
    idx.source("rooms", lambda: [N.Name(a, "room", a.lower()) for a in sorted({d.area for d in w.devices if d.area})])
    idx.source("people", lambda: [N.Name(p["name"], "person", p["id"]) for p in w.people])
    idx.source("automations", lambda: [N.Name(a["name"], "automation", a["id"]) for a in w.automations or []])
    idx.source("recipes", lambda: [N.Name(r["title"], "recipe", r["id"]) for r in w.recipes or []])
    idx.source("pages", lambda: [N.Name(p["name"], "page", p["id"]) for p in w.pages])
    return idx


class FakeKitchen:
    def __init__(self):
        self.calls = []

    async def __call__(self, viewer, req):
        self.calls.append(req)
        if req.get("do") == "open" and req.get("id"):
            return {"reply": f"Here's recipe {req['id']}."}
        return {"reply": "I couldn't find a recipe called " + " ".join(req.get("words") or []) + "."}


class Rig:
    """A pipeline over a fake world: what it commands, runs and cooks is recorded."""

    def __init__(self, w=None, names=True):
        self.w = w or world()
        self.commands, self.ran, self.logged = [], [], []
        self.kitchen = FakeKitchen()
        self.tmp = tempfile.TemporaryDirectory()
        import sqlite3
        db = sqlite3.connect(":memory:")

        async def command(eid, cmd, who, value=None):
            self.commands.append((eid, cmd))

        async def run_automation(viewer, aid):
            self.ran.append(aid)
            return f"Okay, running {next(a['name'] for a in self.w.automations if a['id'] == aid)}."

        self.p = Pipeline(Sources(world=lambda v: self.w, command=command, today=lambda v: {},
                                  log_event=lambda *a, **k: self.logged.append(a), cook=self.kitchen,
                                  names=index_of(self.w) if names else None, run_automation=run_automation),
                          Timers(db), None, None)
        self.viewer = VoiceViewer({"id": "u1", "name": "Hall"}, {})

    async def say(self, text):
        return await self.p.answer(text, self.viewer)


class ScoreTests(unittest.TestCase):
    def test_sounds_and_splits(self):
        self.assertGreaterEqual(N.score("back yard", "Backyard"), N.HIGH)
        self.assertGreaterEqual(N.score("good nite", "Good Night"), N.HIGH)
        self.assertEqual(N.sound("nite"), N.sound("night"))
        self.assertLess(N.score("flux capacitor", "Front Porch"), N.MEDIUM)
        self.assertLess(N.score("kitchen", "Dishes Done"), N.MEDIUM)

    def test_correct_picks_a_stretch_and_stays_within_the_allowed_names(self):
        allowed = [N.Name("Good Night", "automation", "a1"), N.Name("Good Morning", "automation", "a2")]
        fix = N.correct(["good", "nite"], allowed)
        self.assertEqual((fix.name.ref, fix.band, fix.start, fix.end), ("a1", "high", 0, 2))
        self.assertIsNone(N.correct(["pizza", "party"], allowed))
        # Said exactly as some other name (a device): not bent into an automation.
        everything = allowed + [N.Name("Good Knight", "device", "d1")]
        self.assertIsNone(N.correct(["good", "knight"], allowed, everything))
        # Kind words and little words never start a stretch on their own.
        self.assertIsNone(N.correct(["lights"], [N.Name("Lights Out", "automation", "a3")], skip={"lights"}))

    def test_the_index_rebuilds_only_what_changed(self):
        people = [{"id": "p1", "name": "Dante"}]
        devices = [("d1", "Porch Light")]
        idx = N.NameIndex()
        idx.source("people", lambda: [N.Name(p["name"], "person", p["id"]) for p in people],
                   lambda: tuple((p["id"], p["name"]) for p in people))
        idx.source("devices", lambda: [N.Name(n, "device", i) for i, n in devices])
        self.assertEqual([n.text for n in idx.names()], ["Dante", "Porch Light"])
        idx.names()
        self.assertEqual((idx.builds["people"], idx.builds["devices"]), (1, 1))
        devices[0] = ("d1", "Night Light")  # renamed, but nobody said so yet
        self.assertEqual([n.text for n in idx.names("device")], ["Porch Light"])
        idx.mark("devices")  # the hub's rename event
        self.assertEqual([n.text for n in idx.names("device")], ["Night Light"])
        people.append({"id": "p2", "name": "Massimo"})  # seen by its fingerprint
        self.assertEqual([n.text for n in idx.names("person")], ["Dante", "Massimo"])
        self.assertEqual((idx.builds["people"], idx.builds["devices"]), (2, 2))


class CorrectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_room_heard_as_two_words(self):
        r = Rig()
        out = await r.say("turn on the back yard lights")
        self.assertEqual(r.commands, [("l2", "turn_on")])
        self.assertEqual(out["action"]["corrected"], "Backyard")
        self.assertEqual(out["reply"], "Okay, the String Lights are on.")

    async def test_a_device_name_heard_by_its_sound(self):
        r = Rig()
        out = await r.say("turn on the nite light")
        self.assertEqual(r.commands, [("l3", "turn_on")])
        self.assertEqual(out["action"]["corrected"], "Night Light")

    async def test_an_automation(self):
        r = Rig()
        out = await r.say("run good nite")
        self.assertEqual((r.ran, out["reply"]), (["a_night"], "Okay, running Good Night."))
        out = await r.say("run the good morning automation")  # as named: no correcting needed
        self.assertEqual((r.ran[-1], out["action"]["type"]), ("a_morning", "automation_run"))
        self.assertNotIn("corrected", out["action"])

    async def test_a_person_fairly_sure_asks_first(self):
        r = Rig()
        out = await r.say("where's masimo")  # close enough as said
        self.assertEqual(out["reply"], "Massimo is home.")
        out = await r.say("where is ma seemo")
        self.assertEqual((out["reply"], out["action"]["type"]), ("Did you mean Massimo?", "confirm"))
        out = await r.say("yes")
        self.assertEqual(out["reply"], "Massimo is home.")
        out = await r.say("where is ma seemo")
        out = await r.say("no")
        self.assertEqual(out["reply"], "Okay, never mind.")
        out = await r.say("yes")  # nothing asked any more
        self.assertNotEqual(out["reply"], "Massimo is home.")

    async def test_the_question_lapses_when_something_else_is_said(self):
        r = Rig()
        await r.say("where is ma seemo")
        out = await r.say("turn on the porch light")
        self.assertEqual(r.commands, [("l4", "turn_on")])
        out = await r.say("yes")
        self.assertNotEqual(out["reply"], "Massimo is home.")

    async def test_a_recipe(self):
        r = Rig()
        out = await r.say("pull up the fetuchini alfredo recipe")
        if out["action"]["type"] == "confirm":
            self.assertEqual(out["reply"], "Did you mean Fettuccine Alfredo?")
            out = await r.say("yes")
        self.assertEqual(r.kitchen.calls[-1], {"do": "open", "id": "r1"})
        self.assertEqual(out["reply"], "Here's recipe r1.")

    async def test_unrelated_words_stay_unmatched(self):
        r = Rig()
        for text, reply in (("turn on the flux capacitor", "Sorry, I didn't catch a device called flux capacitor."),
                            ("where is santa claus", "Sorry, I didn't catch anyone called santa claus."),
                            ("run the pizza party", "Sorry, I didn't catch an automation called pizza party.")):
            out = await r.say(text)
            self.assertEqual((out["reply"], out["action"]["type"]), (reply, "unknown"), text)
        self.assertEqual((r.commands, r.ran), ([], []))
        # A real name of another kind is never bent into the kind the request needs.
        out = await r.say("where is the dishwasher")
        self.assertEqual(out["action"]["type"], "unknown")
        out = await r.say("run the dishwasher")
        self.assertEqual((out["action"]["type"], r.ran), ("unknown", []))
        out = await r.say("lock the nite light")  # only locks lock
        self.assertEqual((out["action"]["type"], r.commands), ("unknown", []))
        # As before: names said right, and a kind word alone.
        await r.say("turn on the porch light")
        out = await r.say("turn off all the lights")
        self.assertEqual((out["action"]["type"], len(r.commands)), ("command", 5))
        self.assertNotIn("corrected", out["action"])
        # Words that fit no name keep the request as it was understood (nothing is guessed for them).
        out = await r.say("turn on the kitchen lights quickly")
        self.assertEqual((out["action"]["type"], r.commands[-1]), ("command", ("l1", "turn_on")))
        self.assertNotIn("corrected", out["action"])

    async def test_without_an_index_nothing_is_corrected(self):
        r = Rig(names=False)
        out = await r.say("run good nite")
        self.assertEqual((out["action"]["type"], r.ran), ("unknown", []))

    async def test_running_a_sensitive_automation_needs_the_tablets_setting(self):
        r = Rig()
        out = await r.say("run leaving home")
        self.assertEqual((out["reply"], r.ran), (I.SAFETY, []))
        r.w = world(allow_sensitive=True)
        out = await r.say("run leaving home")
        self.assertEqual(r.ran, ["a_leave"])

    def test_start_and_activate_need_an_automation_named(self):
        w = world(devices=world().devices + [dev("v1", "Robot", "vacuum", "docked")])
        self.assertEqual(I.parse("start the vacuum", w).type, "command")
        self.assertEqual(I.parse("start good night", w).value, "a_night")
        self.assertEqual(I.parse("start the good night routine", w).type, "automation_run")
        self.assertEqual(I.parse("run", w).reply, "Which automation?")
        self.assertIsNone(I.parse("run good night", world(automations=None)).value)


# -- the Whisper hint ------------------------------------------------------------------------------------

class HintTests(unittest.TestCase):
    def test_priority_dedupe_and_punctuation(self):
        text = HT.build([["Dante", "Massimo"], ["Backyard", "dante"], ["Kitchen, Lights", "  Front   Porch "],
                         ["Good Night"], ["1234", "Newman"]])
        self.assertEqual(text, "Newman. Dante, Massimo, Backyard, Kitchen Lights, Front Porch, Good Night.")
        self.assertEqual(HT.build([]), "Newman.")

    def test_the_budget(self):
        many = [f"Device Number {chr(65 + i % 26)}{i}" for i in range(400)]
        text = HT.build([["Dante"], many])
        self.assertLessEqual(HT.estimate_tokens(text), HT.MAX_TOKENS)
        self.assertLessEqual(len(text.split()), HT.MAX_WORDS)
        self.assertTrue(text.startswith("Newman. Dante, Device Number A0, "))
        self.assertTrue(text.endswith("."))
        # The first groups win: a person is never pushed out by devices.
        self.assertIn("Dante", HT.build([["Dante"], many], max_tokens=20))
        # A name too long for what's left is skipped; shorter ones after it still fit.
        text = HT.build([["Chris"], ["A Very Long Device Name Here"], ["Den"]], max_tokens=10)
        self.assertEqual(text, "Newman. Chris, Den.")
        self.assertEqual(HT.build([["x" * 41, "Ok Name"]]), "Newman. Ok Name.")

    def test_groups_order_devices_by_use(self):
        idx = N.NameIndex()
        idx.source("d", lambda: [N.Name("Porch Light", "device", "a", 1), N.Name("Kitchen", "device", "b", 31),
                                 N.Name("Thermometer", "device", "c", 0)])
        idx.source("p", lambda: [N.Name("Dante", "person", "p1")])
        idx.source("m", lambda: [N.Name("Queen", "artist", "q", 12), N.Name("Unplayed", "artist", "u", 0),
                                 N.Name("Van Halen", "artist", "v", 40)])
        idx.source("a", lambda: [N.Name("Good Night", "automation", "g")])
        self.assertEqual(HT.build(HT.groups_for(idx)),
                         "Newman. Dante, Kitchen, Porch Light, Thermometer, Good Night, Van Halen, Queen.")

    def test_the_file_is_written_only_when_it_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            names = ["Dante"]
            idx = N.NameIndex()
            idx.source("p", lambda: [N.Name(n, "person", n) for n in names], lambda: tuple(names))
            f = HT.HintFile(idx, Path(tmp) / "voice" / "whisper_prompt.txt")
            self.assertTrue(f.write())
            self.assertEqual(f.path.read_text(), "Newman. Dante.\n")
            self.assertEqual(oct(f.path.stat().st_mode & 0o777), "0o644")
            self.assertFalse(f.write())
            names.append("Massimo")
            self.assertTrue(f.write())
            self.assertEqual(f.info()["text"], "Newman. Dante, Massimo.")
            self.assertTrue(f.info()["written"])


# -- over the API: renames picked up live, the hint route --------------------------------------------------

class NamesApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.h.voice_stt, self.h.voice_tts = FakeSTT(), FakeTTS()

        async def fetch(url):
            return b"model" + b"\0" * 2048
        self.h.voice_fetch = fetch
        self.gear = Gear(self.h.integrations._ctx)
        self.h.integrations.instances["fake"] = self.gear
        for eid, kind, name in (("fake.k1", "light", "Kitchen Lights"), ("fake.p", "light", "Porch Light")):
            self.h.hub.upsert(eid, kind=kind, name=name, integration="fake")
            await self.h.hub.update(eid, state="off")
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        r = await self.client.post("/api/users", json={"username": "hall", "name": "Hallway tablet", "role": "tablet",
                                                       "password": "wall tablet pass"})
        self.tab = Browser(self.client)
        r = await self.tab.post("/api/auth/login", json={"username": "hall", "password": "wall tablet pass", "kind": "tablet"})
        self.auth = {"Authorization": "Bearer " + (await r.json())["token"]}

    async def asyncTearDown(self):
        await self.tab.close()
        await self.client.close()
        self.tmp.cleanup()

    async def say(self, text):
        r = await self.tab.post("/api/voice/utterance", json={"text": text}, headers=self.auth, params={"speak": "0"})
        self.assertEqual(r.status, 200, await r.text())
        return await r.json()

    async def test_a_rename_is_heard_straight_away(self):
        out = await self.say("turn on the nite light")
        self.assertEqual(self.gear.calls, [])
        r = await self.client.patch("/api/entities/fake.k1", json={"name": "Night Light"})
        self.assertEqual(r.status, 200)
        await asyncio.sleep(0.05)  # the hub tells its listeners in a task
        out = await self.say("turn on the nite light")
        self.assertEqual(self.gear.calls, [("fake.k1", "turn_on", None)])
        self.assertEqual(out["action"]["corrected"], "Night Light")

    async def test_run_an_automation_by_voice_and_saved_ones_are_known(self):
        r = await self.client.post("/api/automations", json={
            "name": "Good Night", "trigger": {"type": "time", "at": "22:00"}, "notify": {"enabled": False},
            "actions": [{"entity": "fake.p", "command": "turn_on"}]})
        self.assertEqual(r.status, 200, await r.text())
        out = await self.say("run good nite")
        self.assertEqual(out["reply"], "Okay, running Good Night.")
        self.assertEqual(out["action"]["corrected"], "Good Night")
        self.assertIn(("fake.p", "turn_on", None), self.gear.calls)

    async def test_the_hint_route(self):
        r = await self.tab.get("/api/voice/hint", headers=self.auth)
        self.assertEqual(r.status, 403)
        r = await self.client.post("/api/people", json={"name": "Massimo"})
        self.assertIn(r.status, (200, 201), await r.text())
        info = await (await self.client.get("/api/voice/hint")).json()
        self.assertTrue(info["text"].startswith("Newman. Chris, Massimo, "), info["text"])  # the household first
        self.assertIn("Kitchen Lights", info["text"])
        self.assertLessEqual(info["tokens"], HT.MAX_TOKENS)
        self.assertTrue(info["file"].endswith("voice/whisper_prompt.txt"))
        self.assertEqual(info["whisper"]["container"], "newman-whisper")  # restarted when it changes
        self.assertFalse(info["whisper"]["auto"])  # no VOICE_STT here: nothing to restart


if __name__ == "__main__":
    unittest.main()
