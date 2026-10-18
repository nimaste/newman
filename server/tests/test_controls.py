"""A remote's Controls: its lights and what each press does - a setting of the remote, not automations."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import build_app  # noqa: E402
from newman.automations import engine as engine_mod  # noqa: E402
from newman.automations.engine import Engine  # noqa: E402
from newman.config import config  # noqa: E402
from newman.controls import MIGRATION, SETTING, presses  # noqa: E402
from newman.integrations.base import Context, Integration  # noqa: E402
from newman.integrations.manager import IntegrationManager  # noqa: E402
from newman.push.notifier import Notifier  # noqa: E402
from newman import replace as R  # noqa: E402
from newman.state import Hub  # noqa: E402
from newman.store import Store  # noqa: E402
from test_auth import H  # noqa: E402

PICO = "lutron.remote_14"  # the kitchen paddle Pico: top button_1, bottom button_3
LIGHTS = ["mqtt.coffee", "mqtt.work", "mqtt.kitchenette"]
BUTTONS = [{"id": "button_1", "label": "Top", "role": "on"}, {"id": "button_3", "label": "Bottom", "role": "off"}]


class Calls(Integration):
    type = "fake"
    name = "Fake"

    def __init__(self, calls):
        self.calls, self.id, self.display_name, self.config = calls, "fake", "Fake", {}

    async def command(self, eid, command, value=None):
        self.calls.append((eid, command, value))


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.store = Store(data)
        self.hub = Hub()
        self.integrations = IntegrationManager(self.store, Context(self.hub, data, data / "snapshots"))
        self.calls = []
        self.integrations.instances["fake"] = Calls(self.calls)
        for eid, name in zip(LIGHTS + ["fake.kitchen"], ["Coffee", "Kitchen Work", "Kitchenette", "Kitchen"]):
            self.hub.upsert(eid, kind="light", name=name, integration="fake")
            await self.hub.update(eid, state="off", attrs={"dimmable": True})
        self.hub.upsert("fake.side", kind="lock", name="Side Door", integration="fake")
        await self.hub.update("fake.side", state="LOCKED")
        await self.remote()
        fast = mock.patch.object(engine_mod, "RAMP_EVERY_S", 0.03)
        fast.start()
        self.addCleanup(fast.stop)
        self.engine = Engine(self.hub, self.store, Notifier(self.store), self.integrations)

    async def remote(self, eid=PICO, buttons=BUTTONS, name="Kitchen Under Cabinet"):
        self.hub.upsert(eid, kind="remote", name=name, integration="fake")
        events = [e for b in buttons for e in (b["id"], f"{b['id']}_long", f"{b['id']}_released")]
        await self.hub.update(eid, state="idle", attrs={"events": events, "buttons": buttons})

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def press(self, event, eid=PICO):
        await self.hub.emit(eid, event, {})
        await asyncio.sleep(0.05)

    async def settle(self):
        for _ in range(300):
            if not self.engine._ramps:
                return
            await asyncio.sleep(0.01)
        self.fail("the ramp never stopped")

    def button_autos(self, level=60, holds=True, remote=PICO, on="button_1", off="button_3", lights=LIGHTS):
        out = [self.engine.save({"name": "top on", "notify": {"enabled": False},
                                 "trigger": {"type": "event", "entity": remote, "event": on},
                                 "actions": [{"entities": lights, "command": "set_brightness", "value": level}]}),
               self.engine.save({"name": "bottom off", "notify": {"enabled": False},
                                 "trigger": {"type": "event", "entity": remote, "event": off},
                                 "actions": [{"entities": lights, "command": "turn_off"}]})]
        if holds:
            out += [self.engine.save({"name": "hold top", "notify": {"enabled": False},
                                      "trigger": {"type": "event", "entity": remote, "event": f"{on}_long"},
                                      "actions": [{"entities": lights, "command": "brighten_while_held"}]}),
                    self.engine.save({"name": "hold bottom", "notify": {"enabled": False},
                                      "trigger": {"type": "event", "entity": remote, "event": f"{off}_long"},
                                      "actions": [{"entities": lights, "command": "dim_while_held"}]})]
        return out


class PressTests(Base):
    async def test_rows_say_what_each_press_does(self):
        self.engine.controls.set(PICO, {"lights": LIGHTS, "onLevel": 60, "hold": True})
        rows = self.engine.controls.json(self.hub.get(PICO))["rows"]
        self.assertEqual([(r["key"], r["label"], r["does"], r["custom"]) for r in rows], [
            ("button_1", "Tap top", "On at 60%", False), ("button_1_long", "Hold top", "Brighten until you let go", False),
            ("button_3", "Tap bottom", "Off", False), ("button_3_long", "Hold bottom", "Dim until you let go", False)])
        self.engine.controls.set(PICO, {"lights": LIGHTS, "onLevel": None, "hold": False, "buttons": {
            "button_3_long": {"do": "device", "entity": "fake.kitchen", "command": "turn_off"},
            "button_1_long": {"do": "nothing"}}})
        rows = {r["key"]: (r["does"], r["custom"]) for r in self.engine.controls.rows(self.hub.get(PICO))}
        self.assertEqual(rows, {"button_1": ("On", False), "button_1_long": ("Nothing", True),
                                "button_3": ("Off", False), "button_3_long": ("Turn off Kitchen", True)})

    async def test_tap_top_and_bottom_switch_every_light(self):
        self.engine.controls.set(PICO, {"lights": LIGHTS, "onLevel": 60})
        await self.press("button_1")
        self.assertEqual(sorted(self.calls), sorted((x, "set_brightness", 60) for x in LIGHTS))
        self.calls.clear()
        for x in LIGHTS:
            await self.hub.update(x, state="on", attrs={"brightness": 60})
        await self.press("button_3")
        self.assertEqual(sorted(self.calls), sorted((x, "turn_off", None) for x in LIGHTS))
        self.calls.clear()
        for x in LIGHTS:
            await self.hub.update(x, state="off")
        self.engine.controls.set(PICO, {"lights": LIGHTS, "onLevel": None})  # their last brightness
        await self.press("button_1")
        self.assertEqual({c[1] for c in self.calls}, {"turn_on"})

    async def test_hold_ramps_until_let_go_and_hold_off_does_nothing(self):
        self.engine.controls.set(PICO, {"lights": ["mqtt.coffee"], "onLevel": 60, "hold": True})
        await self.hub.update("mqtt.coffee", state="on", attrs={"brightness": 50})
        await self.hub.emit(PICO, "button_1_long", {})
        await asyncio.sleep(0.08)
        await self.hub.emit(PICO, "button_1_released", {})
        await self.settle()
        steps = [v for _, c, v in self.calls]
        self.assertTrue(steps and steps[0] == 60 and steps == sorted(steps), self.calls)
        n = len(self.calls)
        await asyncio.sleep(0.1)
        self.assertEqual(len(self.calls), n)  # let go: it stopped
        self.assertEqual(self.store.runs(None, 10), [])  # no automation run saved for a remote's press
        self.calls.clear()
        self.engine.controls.set(PICO, {"lights": ["mqtt.coffee"], "hold": False})
        await self.press("button_1_long")
        await self.settle()
        self.assertEqual(self.calls, [])

    async def test_overrides_device_automation_nothing_and_no_lights(self):
        morning = self.engine.save({"name": "Morning lights", "notify": {"enabled": False},
                                    "trigger": {"type": "time", "time": "06:15"},
                                    "actions": [{"entity": "fake.kitchen", "command": "turn_on"}]})
        self.engine.controls.set(PICO, {"lights": [], "buttons": {
            "button_1": {"do": "automation", "automation": morning["id"]},
            "button_3": {"do": "device", "entity": "fake.side", "command": "unlock"},
            "button_3_long": {"do": "device", "entity": "fake.kitchen", "command": "dim_while_held"},
            "button_1_long": {"do": "nothing"}}})
        await self.press("button_1")
        self.assertEqual(self.calls, [("fake.kitchen", "turn_on", None)])
        self.calls.clear()
        await self.press("button_3")
        self.assertEqual(self.calls, [("fake.side", "unlock", None)])
        self.calls.clear()
        await self.press("button_1_long")
        self.assertEqual(self.calls, [])
        rows = {r["key"]: r["does"] for r in self.engine.controls.rows(self.hub.get(PICO))}
        self.assertEqual(rows["button_1"], "Run Morning lights")
        # Deleting the automation leaves that press doing nothing, and says so.
        self.engine.delete(morning["id"])
        self.assertEqual(self.engine.controls.get(PICO)["buttons"]["button_1"], {"do": "nothing"})
        self.assertIn("a press now does nothing", self.store.events(5)[0]["title"])

    async def test_raise_and_lower_step_and_favourite_is_nothing_by_default(self):
        pico = [{"id": "on", "label": "On", "role": "on"}, {"id": "favorite", "label": "★", "role": None},
                {"id": "off", "label": "Off", "role": "off"}, {"id": "raise", "label": "Raise", "role": "raise"},
                {"id": "lower", "label": "Lower", "role": "lower"}]
        await self.remote("lutron.remote_7", pico, "Dining Room Pico Remote")
        self.engine.controls.set("lutron.remote_7", {"lights": ["mqtt.coffee"], "onLevel": None})
        rows = {r["label"]: r["does"] for r in self.engine.controls.rows(self.hub.get("lutron.remote_7"))}
        self.assertEqual((rows["Tap ★"], rows["Tap Raise"], rows["Hold Lower"]),
                         ("Nothing", "A bit brighter", "Dim until you let go"))
        await self.hub.update("mqtt.coffee", state="on", attrs={"brightness": 40})
        await self.press("raise", "lutron.remote_7")
        await self.press("favorite", "lutron.remote_7")
        await self.hub.update("mqtt.coffee", attrs={"brightness": 50})
        await self.press("lower", "lutron.remote_7")
        self.assertEqual(self.calls, [("mqtt.coffee", "set_brightness", 50), ("mqtt.coffee", "set_brightness", 40)])

    async def test_checked_before_it_is_kept(self):
        c = self.engine.controls
        bad = [({"lights": ["nope"]}, "There's no device nope"),
               ({"lights": ["fake.side"]}, "Side Door can't be switched on and off"),
               ({"lights": LIGHTS, "onLevel": 0}, "The brightness to turn on at is 1 to 100%"),
               ({"buttons": {"button_9": {"do": "nothing"}}}, "Kitchen Under Cabinet has no press called button_9"),
               ({"buttons": {"button_1": {"do": "device", "entity": "fake.side", "command": "fly"}}},
                "Tap top: Side Door can't do that"),
               ({"buttons": {"button_1": {"do": "device", "entity": "fake.kitchen", "command": "dim_while_held"}}},
                "Tap top: “Dim while held” only works on a press that's held"),
               ({"buttons": {"button_1": {"do": "device", "entity": "fake.kitchen", "command": "set_brightness"}}},
                "Tap top: set brightness needs a value"),
               ({"buttons": {"button_1": {"do": "automation", "automation": "auto_x"}}},
                "Tap top: pick an automation that exists")]
        for body, msg in bad:
            with self.assertRaises(ValueError) as e:
                c.set(PICO, body)
            self.assertEqual(str(e.exception), msg)
        with self.assertRaises(ValueError):
            c.set("fake.side", {"lights": LIGHTS})  # no buttons
        self.assertIsNone(c.get(PICO))
        kept = c.set(PICO, {"lights": LIGHTS + ["mqtt.coffee"], "buttons": {
            "button_1": {"do": "device", "entity": "fake.kitchen", "command": "set_brightness", "value": 30}}})
        self.assertEqual((kept["lights"], kept["hold"], kept["onLevel"]), (LIGHTS, True, None))
        self.assertEqual(self.store.settings()[SETTING][PICO], kept)

    async def test_presses_follow_the_events_the_remote_has(self):
        self.hub.upsert("mqtt.remote", kind="remote", name="Zigbee remote", integration="fake")
        await self.hub.update("mqtt.remote", state="idle", attrs={"events": ["button_1", "button_2", "button_2_long"]})
        self.assertEqual([p["label"] for p in presses(self.hub.get("mqtt.remote"))],
                         ["Tap Button 1", "Tap Button 2", "Hold Button 2"])


class MigrationTests(Base):
    async def test_standard_button_automations_become_controls_once(self):
        mine = self.button_autos(level=10)
        bilresa = "matter.ed60d62e2b644dfa_1_buttons"
        await self.remote(bilresa, [{"id": "button_1", "label": "Top", "role": "on"},
                                    {"id": "button_2", "label": "Bottom", "role": "off"}], "BILRESA dual button")
        dining = self.button_autos(remote=bilresa, on="button_1", off="button_2", lights=["fake.kitchen"])
        morning = self.engine.save({"name": "New automation", "notify": {"enabled": False},
                                    "trigger": {"type": "time", "time": "06:15"},
                                    "actions": [{"entity": "mqtt.coffee", "command": "set_brightness", "value": 50}]})
        said = self.engine.controls.migrate()
        self.assertEqual(sorted(said), ["BILRESA dual button's 4 button automations became its Controls",
                                        "Kitchen Under Cabinet's 4 button automations became its Controls"])
        self.assertEqual(list(self.engine.autos), [morning["id"]])  # the 6:15 one is untouched
        self.assertEqual(self.engine.controls.get(PICO), {"lights": LIGHTS, "onLevel": 10, "hold": True, "buttons": {}})
        self.assertEqual(self.engine.controls.get(bilresa)["lights"], ["fake.kitchen"])
        self.assertEqual(self.store.settings()[MIGRATION], {"done": True})
        self.assertIn("became its Controls", self.store.events(5, kind="system")[0]["title"])
        # Done: a new set made later stays as automations.
        later = self.button_autos(remote=bilresa, on="button_1", off="button_2", lights=["mqtt.coffee"])
        self.engine.controls.remove(bilresa)
        self.assertEqual(self.engine.controls.migrate(), [])
        self.assertTrue(all(a["id"] in self.engine.autos for a in later))
        self.assertTrue(dining and mine)

    async def test_anything_but_exactly_the_standard_set_is_left_alone(self):
        cases = []
        a = self.button_autos()
        self.engine.save({**a[0], "condition": {"days": [0]}})  # only on Mondays: not just a light switch
        cases.append(a)
        for case in cases:
            self.assertEqual(self.engine.controls.migrate(), [])
            self.assertTrue(all(x["id"] in self.engine.autos for x in case))
        self.assertIsNone(self.engine.controls.get(PICO))

    async def test_only_taps_without_holds_converts_with_hold_off_and_mixed_lights_do_not(self):
        self.button_autos(holds=False, level=60)
        self.engine.controls.migrate()
        self.assertEqual(self.engine.controls.get(PICO), {"lights": LIGHTS, "onLevel": 60, "hold": False, "buttons": {}})

    async def test_different_lights_per_button_are_left_alone(self):
        a = self.button_autos(holds=False)
        self.engine.save({**a[1], "actions": [{"entities": ["mqtt.coffee"], "command": "turn_off"}]})
        self.assertEqual(self.engine.controls.migrate(), [])
        self.assertEqual(len(self.engine.autos), 2)

    async def test_devices_that_are_not_remotes_are_not_waited_for(self):
        self.engine.save({"name": "Locked", "notify": {"enabled": False},
                          "trigger": {"type": "event", "entity": "fake.side", "event": "jammed"},
                          "actions": [{"entity": "fake.kitchen", "command": "turn_on"}]})
        self.engine.controls.migrate()
        self.assertEqual(self.store.settings()[MIGRATION], {"done": True})

    async def test_a_remote_not_here_yet_waits(self):
        self.button_autos()
        self.hub.remove(PICO)
        self.assertEqual(self.engine.controls.migrate(), [])
        self.assertEqual(self.store.settings()[MIGRATION], {"pending": [PICO]})
        await self.remote()
        self.assertEqual(len(self.engine.controls.migrate()), 1)
        self.assertEqual(self.engine.autos, {})


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.calls = []
        self.h.integrations.instances["fake"] = Calls(self.calls)
        for eid, name in (("fake.coffee", "Coffee"), ("fake.kitchen", "Kitchen")):
            self.h.hub.upsert(eid, kind="light", name=name, integration="fake")
            await self.h.hub.update(eid, state="off")
        self.h.hub.upsert(PICO, kind="remote", name="Kitchen Under Cabinet", integration="fake")
        await self.h.hub.update(PICO, state="idle", attrs={"events": ["button_1", "button_1_long", "button_3", "button_3_long"],
                                                          "buttons": BUTTONS})
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def test_get_put_delete_and_entity_json(self):
        r = await self.client.get(f"/api/entities/{PICO}/controls")
        self.assertEqual(r.status, 200, await r.text())
        out = await r.json()
        self.assertEqual((r.status, out["controls"], [b["label"] for b in out["buttons"]]), (200, None, ["Top", "Bottom"]))
        devs = {d["id"]: d for d in out["choices"]["devices"]}
        self.assertNotIn(PICO, devs)
        self.assertIn("dim_while_held", [c["id"] for c in devs["fake.kitchen"]["commands"]])
        r = await self.client.put(f"/api/entities/{PICO}/controls", json={"lights": ["fake.coffee"], "onLevel": 101})
        self.assertEqual((r.status, (await r.json())["error"]), (400, "The brightness to turn on at is 1 to 100%"))
        r = await self.client.put(f"/api/entities/{PICO}/controls", json={"lights": ["fake.coffee"], "onLevel": 60, "buttons": {
            "button_3_long": {"do": "device", "entity": "fake.kitchen", "command": "turn_off"}}})
        out = await r.json()
        self.assertEqual(r.status, 200, out)
        self.assertEqual([x["does"] for x in out["rows"]], ["On at 60%", "Brighten until you let go", "Off", "Turn off Kitchen"])
        ents = {e["id"]: e for e in await (await self.client.get("/api/entities")).json()}
        self.assertEqual(ents[PICO]["controls"]["lights"], ["fake.coffee"])
        self.assertIsNone(ents["fake.coffee"]["controls"])
        # What uses Kitchen now: the remote's controls (so removing it asks first); swapping moves it.
        self.assertEqual(R.references(self.h, "fake.kitchen"),
                         [{"type": "remoteControls", "id": PICO, "name": "Kitchen Under Cabinet's controls"}])
        self.assertIn("remote controls", R.uses(self.h, "fake.coffee"))
        self.h.hub.upsert("fake.kitchen2", kind="light", name="Kitchen 2", integration="fake")
        counts = await R.replace_entities(self.h, [{"from": "fake.kitchen", "to": "fake.kitchen2"}])
        self.assertEqual(counts["controls"], 1)
        self.assertEqual(self.h.engine.controls.get(PICO)["buttons"]["button_3_long"]["entity"], "fake.kitchen2")
        r = await self.client.delete(f"/api/entities/{PICO}/controls")
        self.assertIsNone((await r.json())["controls"])
        self.assertEqual(list(self.h.engine.autos), [])  # setting Controls never makes automations

    async def test_only_admins_change_them(self):
        await self.client.post("/api/users", json={"username": "marlina", "name": "Marlina", "role": "family",
                                                   "password": "another pass"})
        from test_auth import Browser
        c = Browser(self.client)
        await c.post("/api/auth/login", json={"username": "marlina", "password": "another pass"})
        self.assertEqual((await c.get(f"/api/entities/{PICO}/controls")).status, 200)
        self.assertEqual((await c.put(f"/api/entities/{PICO}/controls", json={"lights": []})).status, 403)
        await c.close()


if __name__ == "__main__":
    unittest.main()
