"""Shelly, offline: Gen2+ (RPC, pushed over a websocket) and Gen1 (HTTP) devices as fakes."""
import asyncio
import copy
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.integrations.base import Context  # noqa: E402
from newman.integrations.shelly import ShellyIntegration  # noqa: E402
from newman.kinds import properties_for  # noqa: E402
from newman.state import Hub  # noqa: E402

PRO2PM_INFO = {"name": None, "id": "shellypro2pm-a8032ab12345", "mac": "A8032AB12345", "model": "SPSW-202PE16EU",
               "gen": 2, "fw_id": "20240625-122918/1.3.3", "ver": "1.3.3", "app": "Pro2PM"}
PRO2PM_CONFIG = {"sys": {"device": {"name": "Kitchen"}, "ui_data": {"consumption_types": ["", "lights"]}},
                 "switch:0": {"id": 0, "name": None}, "switch:1": {"id": 1, "name": "Pendant"},
                 "input:0": {"id": 0, "type": "button"}, "input:1": {"id": 1, "type": "switch"}}
PRO2PM_STATUS = {"sys": {"uptime": 10}, "wifi": {"rssi": -60},
                 "switch:0": {"id": 0, "output": True, "apower": 12.5, "voltage": 230.1, "current": 0.054,
                              "aenergy": {"total": 1234.5}, "temperature": {"tC": 41.2}},
                 "switch:1": {"id": 1, "output": False, "apower": 0, "aenergy": {"total": 10}},
                 "input:0": {"id": 0, "state": None}, "input:1": {"id": 1, "state": False}}


class FakeRpc:
    """Stands in for aioshelly's RpcDevice."""
    instances = []

    def __init__(self, info, config, status, fail=None):
        self.shelly, self.config, self._status = info, config, status
        self.calls, self.listener, self.fail, self.event = [], None, fail, None
        self.shut = False
        FakeRpc.instances.append(self)

    @property
    def status(self):
        return self._status

    def subscribe_updates(self, cb):
        self.listener = cb

    async def initialize(self):
        if self.fail:
            raise self.fail

    async def poll(self):
        pass

    async def shutdown(self):
        self.shut = True

    async def call_rpc(self, method, params=None):
        self.calls.append((method, params))
        return {}


class ShellyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        self.events = []

        async def on_event(ent, event, data):
            self.events.append((ent.id, event))
        self.hub.subscribe_events(on_event)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def make(self, **cfg):
        return ShellyIntegration("shelly", "Shelly", {"host": "10.0.0.5", **cfg},
                                 Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))

    def by_name(self):
        return {e.name: e for e in self.hub.all()}

    def test_discovery(self):
        self.assertIsNone(ShellyIntegration.from_discovery("printer._http._tcp.local.", "10.0.0.9", {}))
        offer = ShellyIntegration.from_discovery("shellyplus1pm-a8032ab12345._shelly._tcp.local.", "10.0.0.5",
                                                 {"gen": "2", "app": "Plus1PM"})
        self.assertEqual(offer, {"title": "Shelly Plus1PM (10.0.0.5)", "config": {"host": "10.0.0.5"}})
        self.assertIsNotNone(ShellyIntegration.from_discovery("shelly1-34945478A1B2._http._tcp.local.", "10.0.0.6", {}))

    async def test_gen2_relays_energy_buttons_and_commands(self):
        s = self.make()
        s._gen = 2
        await s._rpc_setup(PRO2PM_INFO, PRO2PM_CONFIG, copy.deepcopy(PRO2PM_STATUS))
        ents = self.by_name()
        one, pendant, buttons = ents["Kitchen 1"], ents["Pendant"], ents["Kitchen buttons"]
        self.assertEqual(one.id, "shelly.a8032ab12345_switch_0")  # stable: based on the MAC
        self.assertEqual((one.kind, one.state, one.attrs["power"], one.attrs["energy"]), ("switch", "on", 12.5, 1.234))
        self.assertEqual(one.attrs["deviceTemperature"], 41.2)
        props = {p["id"]: p for p in properties_for(one)}
        self.assertEqual((props["power"]["unit"], props["energy"]["value"], props["voltage"]["value"]), ("W", 1.234, 230.1))
        self.assertNotIn("model", props)  # descriptive, not a reading
        self.assertEqual((pendant.kind, pendant.state, pendant.attrs["dimmable"]), ("light", "off", False))
        self.assertEqual((buttons.kind, buttons.attrs["events"]), ("remote", ["button_1"]))
        self.assertNotIn("Kitchen 2", ents)
        # Pushed changes (NotifyStatus merged into the device's status) and a button press (NotifyEvent).
        status = copy.deepcopy(PRO2PM_STATUS)
        status["switch:0"]["output"] = False
        await s._rpc_publish(status)
        self.assertEqual(self.hub.get(one.id).state, "off")
        await s._rpc_event({"events": [{"component": "input:0", "id": 0, "event": "single_push"},
                                       {"component": "input:1", "id": 1, "event": "single_push"}]})
        await asyncio.sleep(0.01)
        self.assertEqual(self.events, [(buttons.id, "button_1")])
        s._rpc = FakeRpc(PRO2PM_INFO, PRO2PM_CONFIG, status)
        await s.command(one.id, "turn_on")
        await s.command(pendant.id, "turn_off")
        self.assertEqual(s._rpc.calls, [("Switch.Set", {"id": 0, "on": True}), ("Switch.Set", {"id": 1, "on": False})])
        from newman.integrations.base import CommandError
        with self.assertRaises(CommandError):
            await s.command(pendant.id, "set_brightness", 40)  # a relay wired to a lamp isn't dimmable

    async def test_gen2_dimmer_cover_and_battery_sensors(self):
        s = self.make()
        s._gen = 2
        await s._rpc_setup({"mac": "AA:BB:CC:00:11:22", "model": "S3DM-0010WW", "gen": 3, "ver": "1.4"},
                           {"sys": {"device": {"name": "Lounge"}}, "light:0": {"name": None},
                            "cover:0": {"name": "Blind", "pos_control": True}},
                           {"light:0": {"output": True, "brightness": 37, "apower": 8.2, "aenergy": {"total": 5000}},
                            "cover:0": {"state": "stopped", "current_pos": 40, "apower": 0}})
        ents = self.by_name()
        dimmer, blind = ents["Lounge 1"], ents["Blind"]
        self.assertEqual((dimmer.kind, dimmer.state, dimmer.attrs["brightness"], dimmer.attrs["dimmable"]),
                         ("light", "on", 37, True))
        self.assertEqual((blind.kind, blind.state, blind.attrs["position"]), ("cover", "stopped", 40))
        s._rpc = FakeRpc({}, {}, {})
        await s.command(dimmer.id, "set_brightness", 60)
        await s.command(blind.id, "set_position", 75)
        await s.command(blind.id, "stop")
        self.assertEqual(s._rpc.calls, [("Light.Set", {"id": 0, "on": True, "brightness": 60}),
                                        ("Cover.GoToPosition", {"id": 0, "pos": 75}), ("Cover.Stop", {"id": 0})])
        # A battery H&T: temperature + humidity with the battery level on each.
        ht = ShellyIntegration("shelly_2", "Shelly", {"host": "10.0.0.7"},
                               Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        await ht._rpc_setup({"mac": "B0B21C000001", "gen": 2, "app": "PlusHT"}, {"sys": {"device": {"name": "Attic"}}},
                            {"temperature:0": {"tC": 21.46}, "humidity:0": {"rh": 48.2},
                             "devicepower:0": {"battery": {"V": 5.6, "percent": 87}}})
        ents = self.by_name()
        temp, hum = ents["Attic temperature"], ents["Attic humidity"]
        self.assertEqual((temp.kind, temp.state, temp.attrs["batteryLevel"]), ("temperature", "21.5", 87))
        p = {x["id"]: x for x in properties_for(hum)}["value"]
        self.assertEqual((hum.kind, p["value"], p["unit"], p["label"]), ("sensor", "48.2", "%", "Humidity"))
        # A Flood on its own keeps its own name.
        flood = ShellyIntegration("shelly_3", "Shelly", {"host": "10.0.0.8"},
                                  Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        await flood._rpc_setup({"mac": "C0C0C0000002", "gen": 4}, {"sys": {"device": {"name": "Under Sink"}}},
                               {"flood:0": {"alarm": True, "mute": False}})
        self.assertEqual((self.by_name()["Under Sink"].kind, self.by_name()["Under Sink"].state), ("leak", "wet"))

    async def test_gen1_plug_roller_and_sensor(self):
        s = self.make()
        s._gen = 1
        info = {"type": "SHSW-25", "mac": "34945478A1B2", "auth": False, "fw": "20230913-112003/v1.14.0-gcb84623"}
        settings = {"name": "Porch", "mode": "relay", "relays": [{"name": "Porch light", "appliance_type": "lights"},
                                                                  {"name": None}]}
        status = {"relays": [{"ison": True}, {"ison": False}], "meters": [{"power": 60.5, "total": 120000},
                                                                         {"power": 0, "total": 0}]}
        await s._gen1_setup(info, settings, status)
        ents = self.by_name()
        light, relay = ents["Porch light"], ents["Porch 2"]
        self.assertEqual((light.kind, light.state, light.attrs["power"], light.attrs["energy"]), ("light", "on", 60.5, 2.0))
        self.assertEqual((relay.kind, relay.state), ("switch", "off"))
        sent = []

        async def fake_get(path, params=None):
            sent.append((path, params))
            return {"relays": [{"ison": True}, {"ison": True}], "meters": status["meters"]}
        s._gen1_get = fake_get
        await s.command(relay.id, "turn_on")
        self.assertEqual(sent, [("relay/1", {"turn": "on"}), ("status", None)])
        self.assertEqual(self.hub.get(relay.id).state, "on")

        roller = ShellyIntegration("shelly_r", "Shelly", {"host": "10.0.0.9"},
                                   Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        roller._gen = 1
        await roller._gen1_setup({"type": "SHSW-25", "mac": "34945478A1B3"},
                                 {"name": "Bedroom blind", "mode": "roller", "rollers": [{"positioning": True}]},
                                 {"relays": [{"ison": False}, {"ison": False}],
                                  "rollers": [{"state": "close", "current_pos": 30, "power": 80}]})
        blind = self.by_name()["Bedroom blind"]
        self.assertEqual((blind.kind, blind.state, blind.attrs["position"]), ("cover", "closing", 30))
        await roller._gen1_publish({"rollers": [{"state": "stop", "current_pos": 0}]})
        self.assertEqual(self.hub.get(blind.id).state, "closed")

        flood = ShellyIntegration("shelly_f", "Shelly", {"host": "10.0.0.10"},
                                  Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        await flood._gen1_setup({"type": "SHWT-1", "mac": "AABBCC000003"}, {"name": "Laundry"},
                                {"flood": False, "tmp": {"tC": 12.34, "is_valid": True}, "bat": {"value": 90}})
        ents = self.by_name()
        self.assertEqual((ents["Laundry"].kind, ents["Laundry"].state, ents["Laundry"].attrs["batteryLevel"]),
                         ("leak", "dry", 90))
        self.assertEqual(ents["Laundry temperature"].state, "12.3")

    async def test_run_connects_pushes_and_reports_password_problems(self):
        import aioshelly.common as common
        import aioshelly.rpc_device as rpc
        from aioshelly.exceptions import InvalidAuthError
        from aioshelly.rpc_device import RpcUpdateType
        real_info, real_dev = common.get_info, rpc.RpcDevice
        status = copy.deepcopy(PRO2PM_STATUS)

        async def fake_info(http, host, *a, **kw):
            return PRO2PM_INFO

        class Factory:
            fail = None

            @staticmethod
            async def create(http, ws, options):
                Factory.options = options
                return FakeRpc(PRO2PM_INFO, PRO2PM_CONFIG, status, fail=Factory.fail)
        common.get_info, rpc.RpcDevice = fake_info, Factory
        try:
            s = self.make(password="pw")
            task = asyncio.create_task(s.run())
            await asyncio.sleep(0.05)
            self.assertEqual(s.status, "connected")
            self.assertEqual(s.detail, "Kitchen · 1 remote, 1 switch and 1 light · reports changes instantly")
            self.assertEqual((Factory.options.username, Factory.options.password), ("admin", "pw"))
            dev = FakeRpc.instances[-1]
            status["switch:0"]["output"] = False
            dev.listener(dev, RpcUpdateType.STATUS)
            await asyncio.sleep(0.02)
            self.assertEqual(self.hub.get("shelly.a8032ab12345_switch_0").state, "off")
            s.backoff_sleep = lambda attempt: asyncio.sleep(10)
            dev.listener(dev, RpcUpdateType.DISCONNECTED)
            await asyncio.sleep(0.02)
            self.assertEqual(s.status, "error")
            self.assertTrue(dev.shut)
            self.assertFalse(self.hub.get("shelly.a8032ab12345_switch_0").available)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

            Factory.fail = InvalidAuthError("auth")
            s2 = self.make()
            task = asyncio.create_task(s2.run())
            await asyncio.sleep(0.05)
            self.assertEqual((s2.status, s2.detail), ("error", "This Shelly has a password - enter it in the settings and save"))
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            common.get_info, rpc.RpcDevice = real_info, real_dev
