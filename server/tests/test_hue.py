"""Philips Hue, offline: link-button pairing, a bridge's lights / sensors / dimmer switch, live events."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.integrations import hue as hue_mod  # noqa: E402
from newman.integrations.base import CommandError, Context  # noqa: E402
from newman.integrations.hue import HueIntegration, lux  # noqa: E402
from newman.kinds import properties_for  # noqa: E402
from newman.state import Hub  # noqa: E402

UPDATE = NS(value="update")


def T(v):
    return NS(value=v)


def device(id_, name, product):
    return NS(id=id_, metadata=NS(name=name), services=[],
              product_data=NS(product_name=product, manufacturer_name="Signify Netherlands B.V.",
                              software_version="1.122.2"))


class FakeLights(list):
    def __init__(self, items):
        super().__init__(items)
        self.calls = []

    async def turn_on(self, id_):
        self.calls.append(("on", id_))

    async def turn_off(self, id_):
        self.calls.append(("off", id_))

    async def set_brightness(self, id_, b):
        self.calls.append(("brightness", id_, b))


def fake_bridge():
    lamp = NS(id="l-1", type=T("light"), owner=NS(rid="d-lamp"), on=NS(on=True), dimming=NS(brightness=54.3),
              metadata=NS(name="Hue color lamp 1"))
    plug = NS(id="l-2", type=T("light"), owner=NS(rid="d-plug"), on=NS(on=False), dimming=None, metadata=NS(name="Plug"))
    motion = NS(id="m-1", type=T("motion"), owner=NS(rid="d-motion"), motion=NS(motion_report=NS(motion=False)))
    temp = NS(id="t-1", type=T("temperature"), owner=NS(rid="d-motion"),
              temperature=NS(temperature_report=NS(temperature=19.37)))
    level = NS(id="ll-1", type=T("light_level"), owner=NS(rid="d-motion"),
               light=NS(light_level_report=NS(light_level=20001)))
    contact = NS(id="c-1", type=T("contact"), owner=NS(rid="d-door"), contact_report=NS(state=T("no_contact")))
    buttons = [NS(id=f"b-{n}", type=T("button"), owner=NS(rid="d-dimmer"), metadata=NS(control_id=n),
                  button=NS(button_report=None, last_event=None)) for n in (1, 2, 3, 4)]
    power = NS(id="p-1", type=T("device_power"), owner=NS(rid="d-motion"), power_state=NS(battery_level=81))
    zc = NS(id="z-1", type=T("zigbee_connectivity"), owner=NS(rid="d-lamp"), status=T("connected"))
    return NS(
        devices=[device("d-lamp", "Living room lamp", "Hue color lamp"), device("d-plug", "Fan plug", "Hue smart plug"),
                 device("d-motion", "Hallway", "Hue motion sensor"), device("d-door", "Back door", "Hue secure contact"),
                 device("d-dimmer", "Bedside", "Hue dimmer switch")],
        lights=FakeLights([lamp, plug]),
        sensors=NS(motion=[motion], temperature=[temp], light_level=[level], contact=[contact], button=buttons,
                   device_power=[power], zigbee_connectivity=[zc]),
        events=NS(connected=True))


class HueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        self.h = HueIntegration("hue", "Philips Hue", {"host": "10.0.0.30"},
                                Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        self.events = []

        async def on_event(ent, event, data):
            self.events.append((ent.name, event))
        self.hub.subscribe_events(on_event)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def test_lux_and_discovery(self):
        self.assertEqual(lux(20001), 100)
        self.assertIsNone(lux(None))
        self.assertIn("square", HueIntegration.from_discovery("x", "10.0.0.30", {"modelid": "BSB001"})["note"])
        self.assertEqual(HueIntegration.from_discovery("x", "10.0.0.30", {"modelid": "BSB002"})["config"],
                         {"host": "10.0.0.30"})

    async def test_pairing_waits_for_the_button_and_keeps_only_a_key_file(self):
        import aiohue
        from aiohue.errors import LinkButtonNotPressed
        tries = []

        async def create_app_key(host, device_type, websession=None):
            tries.append(host)
            if len(tries) < 3:
                raise LinkButtonNotPressed("press it")
            return "app-key-123"
        real, aiohue.create_app_key = aiohue.create_app_key, create_app_key
        real_every, hue_mod.PAIR_EVERY_S = hue_mod.PAIR_EVERY_S, 0.01
        statuses = []
        real_set = self.h.set_status
        self.h.set_status = lambda s, d="": (statuses.append((s, d)), real_set(s, d))
        try:
            key = await self.h._app_key()
        finally:
            aiohue.create_app_key, hue_mod.PAIR_EVERY_S = real, real_every
        self.assertEqual(key, "app-key-123")
        self.assertEqual(statuses[0], ("pairing", "Press the round button on top of the Hue bridge now"))
        key_file = Path(self.tmp.name) / "integrations" / "hue" / "app_key"
        self.assertEqual(key_file.read_text(), "app-key-123")
        self.assertNotIn("key", self.h.config)  # never in the settings
        self.assertEqual(await self.h._app_key(), "app-key-123")  # next start: no pairing
        self.assertEqual(len(tries), 3)

    async def test_lights_sensors_remote_events_and_commands(self):
        bridge = fake_bridge()
        self.h._bridge = bridge
        await self.h._setup(bridge)
        ents = {e.name: e for e in self.hub.all()}
        lamp, plug = ents["Living room lamp"], ents["Fan plug"]
        self.assertEqual((lamp.kind, lamp.state, lamp.attrs["brightness"], lamp.attrs["dimmable"]), ("light", "on", 54, True))
        self.assertEqual((plug.state, plug.attrs["dimmable"]), ("off", False))
        hall = ents["Hallway"]
        self.assertEqual((hall.kind, hall.state, hall.attrs["batteryLevel"], hall.attrs["model"]),
                         ("motion", "clear", 81, "Hue motion sensor"))
        self.assertEqual((ents["Hallway temperature"].kind, ents["Hallway temperature"].state), ("temperature", "19.4"))
        level = ents["Hallway light level"]
        p = {q["id"]: q for q in properties_for(level)}["value"]
        self.assertEqual((level.kind, p["value"], p["unit"]), ("sensor", "100", "lx"))
        self.assertEqual((ents["Back door"].kind, ents["Back door"].state), ("contact", "open"))
        remote = ents["Bedside"]
        self.assertEqual((remote.kind, remote.attrs["events"]), ("remote", ["button_1", "button_2", "button_3", "button_4"]))
        self.assertEqual(self.h.status, "connected")
        self.assertIn("on your network", self.h.detail)
        # Live updates from the event stream.
        self.h._on_event(UPDATE, NS(id="m-1", type=T("motion"), owner=NS(rid="d-motion"),
                                    motion=NS(motion_report=NS(motion=True))))
        self.h._on_event(UPDATE, NS(id="b-2", type=T("button"), owner=NS(rid="d-dimmer"),
                                    button=NS(button_report=NS(event=T("initial_press")))))
        self.h._on_event(UPDATE, NS(id="b-2", type=T("button"), owner=NS(rid="d-dimmer"),
                                    button=NS(button_report=NS(event=T("short_release")))))
        self.h._on_event(UPDATE, NS(id="p-1", type=T("device_power"), owner=NS(rid="d-motion"),
                                    power_state=NS(battery_level=15)))
        self.h._on_event(UPDATE, NS(id="z-1", type=T("zigbee_connectivity"), owner=NS(rid="d-lamp"),
                                    status=T("connectivity_issue")))
        self.h._on_event(UPDATE, NS(id="l-1", type=T("light"), owner=NS(rid="d-lamp"), on=NS(on=False), dimming=None))
        await self.h._tasks.drain()
        await asyncio.sleep(0.01)
        self.assertEqual(self.hub.get(hall.id).state, "detected")
        self.assertEqual(self.events, [("Bedside", "button_2")])
        self.assertIn("Battery 15%", self.hub.get(hall.id).health_issues)
        self.assertEqual((self.hub.get(lamp.id).available, self.hub.get(lamp.id).state), (False, "off"))
        # Commands.
        await self.h.command(lamp.id, "set_brightness", 30)
        await self.h.command(plug.id, "turn_on")
        self.assertEqual(bridge.lights.calls, [("brightness", "l-1", 30.0), ("on", "l-2")])
        with self.assertRaises(CommandError):
            await self.h.command(hall.id, "turn_on")
