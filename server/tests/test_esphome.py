"""ESPHome, offline: a ratgdo-style garage opener described with aioesphomeapi's real entity models."""
import asyncio
import os
from unittest import mock
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

import aioesphomeapi as E  # noqa: E402

from newman.integrations.base import CommandError, Context  # noqa: E402
from newman.integrations.esphome import EsphomeIntegration  # noqa: E402
from newman.kinds import properties_for  # noqa: E402
from newman.state import Hub  # noqa: E402

INFO = E.DeviceInfo(name="ratgdo-garage", friendly_name="Garage", mac_address="AA:BB:CC:DD:EE:01",
                    model="esp32dev", manufacturer="Espressif", esphome_version="2025.9.0",
                    project_name="ratgdo.esphome", project_version="v32disco")
ENTITIES = [
    E.CoverInfo(object_id="door", key=1, name="Door", device_class="garage", supports_position=True, supports_stop=True),
    E.LightInfo(object_id="light", key=2, name="Light", supported_color_modes=[E.ColorMode.ON_OFF]),
    E.BinarySensorInfo(object_id="obstruction", key=3, name="Obstruction", device_class="problem"),
    E.SensorInfo(object_id="temperature", key=4, name="Temperature", device_class="temperature",
                 unit_of_measurement="°C", accuracy_decimals=1),
    E.SensorInfo(object_id="humidity", key=5, name="Humidity", device_class="humidity", unit_of_measurement="%"),
    E.BinarySensorInfo(object_id="motion", key=6, name="Motion", device_class="motion"),
    E.ButtonInfo(object_id="restart", key=7, name="Restart", entity_category=E.EntityCategory.CONFIG),
    E.SwitchInfo(object_id="lock_remotes", key=8, name="Lock remotes"),
    E.LockInfo(object_id="lock", key=9, name="Lock"),
    E.FanInfo(object_id="fan", key=10, name="Fan", supports_speed=True, supported_speed_count=3),
    E.LightInfo(object_id="lamp", key=11, name="Garage lamp", supported_color_modes=[E.ColorMode.BRIGHTNESS]),
    E.ButtonInfo(object_id="toggle", key=12, name="Toggle door"),
    E.SensorInfo(object_id="battery", key=13, name="Battery", device_class="battery", unit_of_measurement="%"),
]


class FakeClient:
    made = []
    fail = None

    def __init__(self, address, port, password=None, **kw):
        self.address, self.kw, self.calls, self.on_state = address, kw, [], None
        FakeClient.made.append(self)

    async def connect(self, on_stop=None, login=False):
        self.on_stop = on_stop
        if FakeClient.fail:
            raise FakeClient.fail

    async def device_info_and_list_entities(self):
        return INFO, ENTITIES, []

    def subscribe_states(self, cb):
        self.on_state = cb
        self.subscribes = getattr(self, "subscribes", 0) + 1

    def _get_connection(self):
        client = self

        class Conn:
            def send_message(self, msg):  # the device answers a states request with every state it has
                client.calls.append(("send_message", (type(msg).__name__,), {}))
                for st in getattr(client, "states", []):
                    client.on_state(st)
        return Conn()

    async def disconnect(self):
        pass

    def __getattr__(self, name):
        if name.endswith("_command"):
            def call(*a, **kw):
                self.calls.append((name, a, kw))
            return call
        raise AttributeError(name)


class EsphomeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        self.real = E.APIClient
        E.APIClient = FakeClient
        FakeClient.fail = None

    async def asyncTearDown(self):
        E.APIClient = self.real
        self.tmp.cleanup()

    def make(self, **cfg):
        return EsphomeIntegration("esphome", "ESPHome", {"host": "10.0.0.20", **cfg},
                                  Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))

    async def test_check_everything_again_asks_for_every_state(self):
        with mock.patch.object(EsphomeIntegration, "seconds_every", lambda self, key, unit=60.0: 0.05):
            x = self.make()
            task = asyncio.create_task(x.run())
            await asyncio.sleep(0.02)
            client = FakeClient.made[-1]
            client.states = [E.CoverState(key=1, position=1.0)]  # a change whose push never arrived
            door = "esphome.aabbccddee01_cover_door"
            for _ in range(50):
                await asyncio.sleep(0.02)
                await x._tasks.drain()
                if self.hub.get(door).state == "open":
                    break
            self.assertEqual(self.hub.get(door).state, "open")
            asks = [c for c in client.calls if c[0] == "send_message"]
            self.assertTrue(asks and all(c[1] == ("SubscribeStatesRequest",) for c in asks))
            self.assertEqual(client.subscribes, 1)  # one subscription: the answers go into it
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            n = len([c for c in client.calls if c[0] == "send_message"])
            await asyncio.sleep(0.15)
            self.assertEqual(len([c for c in client.calls if c[0] == "send_message"]), n)  # stops with it

    def test_the_states_request_is_real(self):
        from aioesphomeapi.api_pb2 import SubscribeStatesRequest  # what ask_states_again sends
        self.assertTrue(SubscribeStatesRequest())

    def test_discovery(self):
        offer = EsphomeIntegration.from_discovery("ratgdo-garage._esphomelib._tcp.local.", "10.0.0.20",
                                                  {"friendly_name": "Garage", "api_encryption": "Noise_NNpsk0"})
        self.assertEqual(offer["config"], {"host": "10.0.0.20"})
        self.assertIn("encryption key", offer["note"])

    async def test_entities_live_states_and_commands(self):
        x = self.make(encryption_key="  abc=  ")
        task = asyncio.create_task(x.run())
        await asyncio.sleep(0.05)
        client = FakeClient.made[-1]
        self.assertEqual(client.kw["noise_psk"], "abc=")
        self.assertEqual(x.status, "connected")
        ents = {e.name: e for e in self.hub.all()}
        self.assertNotIn("Garage Restart", ents)  # configuration entities stay hidden
        door = ents["Garage Door"]
        self.assertEqual(door.id, "esphome.aabbccddee01_cover_door")
        self.assertEqual(door.kind, "garage")
        self.assertEqual({n: ents[n].kind for n in ("Garage Light", "Garage Obstruction", "Garage Temperature",
                                                    "Garage Humidity", "Garage Motion", "Garage Lock remotes",
                                                    "Garage Lock", "Garage Fan", "Garage lamp", "Garage Toggle door")},
                         {"Garage Light": "light", "Garage Obstruction": "binary", "Garage Temperature": "temperature",
                          "Garage Humidity": "sensor", "Garage Motion": "motion", "Garage Lock remotes": "switch",
                          "Garage Lock": "lock", "Garage Fan": "fan", "Garage lamp": "light",
                          "Garage Toggle door": "button"})
        self.assertEqual((ents["Garage Light"].attrs["dimmable"], ents["Garage lamp"].attrs["dimmable"]), (False, True))
        # Pushed states.
        for st in (E.CoverState(key=1, position=0.0), E.SensorState(key=4, state=21.46),
                   E.SensorState(key=5, state=48.0), E.BinarySensorState(key=6, state=True),
                   E.BinarySensorState(key=3, state=False), E.LightState(key=11, state=True, brightness=0.4),
                   E.FanState(key=10, state=True, speed_level=2), E.LockEntityState(key=9, state=E.LockState.LOCKED),
                   E.SensorState(key=13, state=77.0)):
            client.on_state(st)
        await x._tasks.drain()
        get = self.hub.get
        self.assertEqual(get(door.id).state, "closed")
        self.assertEqual(get(ents["Garage Temperature"].id).state, "21.5")
        hum = get(ents["Garage Humidity"].id)
        p = {q["id"]: q for q in properties_for(hum)}["value"]
        self.assertEqual((p["value"], p["unit"], p["label"]), ("48", "%", "Humidity"))
        self.assertEqual(get(ents["Garage Motion"].id).state, "detected")
        self.assertEqual(get(ents["Garage Obstruction"].id).state, "off")
        self.assertEqual(get(ents["Garage lamp"].id).attrs["brightness"], 40)
        self.assertEqual(get(ents["Garage Fan"].id).attrs["percentage"], 67)
        self.assertEqual(get(ents["Garage Lock"].id).state, "LOCKED")
        self.assertEqual(get(ents["Garage Motion"].id).attrs["batteryLevel"], 77)
        client.on_state(E.CoverState(key=1, position=0.3, current_operation=E.CoverOperation.IS_OPENING))
        await x._tasks.drain()
        self.assertEqual((get(door.id).state, get(door.id).attrs["position"]), ("opening", 30))
        # Commands.
        await x.command(door.id, "open")
        await x.command(ents["Garage lamp"].id, "set_brightness", 25)
        await x.command(ents["Garage Fan"].id, "set_speed", 100)
        await x.command(ents["Garage Lock"].id, "unlock")
        await x.command(ents["Garage Toggle door"].id, "press")
        self.assertEqual(client.calls, [("cover_command", (1,), {"position": 1.0}),
                                        ("light_command", (11,), {"state": True, "brightness": 0.25}),
                                        ("fan_command", (10,), {"state": True, "speed_level": 3}),
                                        ("lock_command", (9, E.LockCommand.UNLOCK), {}),
                                        ("button_command", (12,), {})])
        with self.assertRaises(CommandError):
            await x.command(ents["Garage Light"].id, "set_brightness", 50)
        # The device drops off: everything goes unavailable and it keeps trying.
        x.backoff_sleep = lambda attempt: asyncio.sleep(10)
        await client.on_stop(False)
        await asyncio.sleep(0.02)
        self.assertEqual(x.status, "error")
        self.assertFalse(get(door.id).available)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_encryption_key_problems_are_explained(self):
        FakeClient.fail = E.RequiresEncryptionAPIError("needs key")
        x = self.make()
        task = asyncio.create_task(x.run())
        await asyncio.sleep(0.03)
        self.assertEqual((x.status, x.detail), ("error", "This device needs its encryption key - paste it in the settings and save"))
        task.cancel()
        FakeClient.fail = E.InvalidEncryptionKeyAPIError("bad key")
        y = self.make(encryption_key="wrong")
        task2 = asyncio.create_task(y.run())
        await asyncio.sleep(0.03)
        self.assertIn("doesn't match", y.detail)
        task2.cancel()
        await asyncio.gather(task, task2, return_exceptions=True)
