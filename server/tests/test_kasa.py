"""TP-Link Kasa / Tapo, offline: a power strip, a dimmable bulb and a Tapo plug that wants the account."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from kasa import AuthenticationError, DeviceType  # noqa: E402

from newman.integrations.base import CommandError, Context, mask_config  # noqa: E402
from newman.integrations.kasa import KasaIntegration  # noqa: E402
from newman.kinds import properties_for  # noqa: E402
from newman.state import Hub  # noqa: E402


class FakeLight:
    def __init__(self, brightness):
        self.brightness = brightness

    def has_feature(self, name):
        return name == "brightness"

    async def set_brightness(self, v):
        self.brightness = v


class FakeDev:
    def __init__(self, dtype, alias, mac, model, children=(), modules=None, on=True, device_id=None):
        self.device_type, self.alias, self.mac, self.model = dtype, alias, mac, model
        self.device_id = device_id or mac.replace(":", "")
        self.children, self.modules, self.is_on = list(children), modules or {}, on
        self.hw_info, self.rssi, self.updates, self.calls = {"sw_ver": "1.0.12"}, -52, 0, []

    async def update(self, update_children=True):
        self.updates += 1

    async def turn_on(self, **kw):
        self.is_on = True
        self.calls.append("on")

    async def turn_off(self, **kw):
        self.is_on = False
        self.calls.append("off")

    async def disconnect(self):
        pass


def energy(w, total):
    return NS(current_consumption=w, consumption_total=total, consumption_today=0.25, voltage=120.44, current=0.0412)


class KasaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        import kasa
        self.real = (kasa.Discover.discover_single, kasa.Discover.try_connect_all, kasa.Discover.discover)
        self.strip = FakeDev(DeviceType.Strip, "Desk strip", "50:C7:BF:00:00:01", "HS300", children=[
            FakeDev(DeviceType.StripSocket, "Monitor", "50:C7:BF:00:00:01", "HS300", device_id="8006ABC00",
                    modules={"Energy": energy(23.456, 12.3456)}),
            FakeDev(DeviceType.StripSocket, "", "50:C7:BF:00:00:01", "HS300", device_id="8006ABC01", on=False,
                    modules={"Energy": energy(0, 1.0)})])
        self.bulb = FakeDev(DeviceType.Bulb, "Reading lamp", "B0:95:75:00:00:02", "KL130",
                            modules={"Light": FakeLight(40)})
        self.seen_creds = {}

        async def discover_single(host, **kw):
            self.seen_creds[host] = kw.get("credentials")
            if host == "10.0.0.3":
                if not kw.get("credentials"):
                    raise AuthenticationError("needs login")
                return FakeDev(DeviceType.Plug, "Kettle", "3C:52:A1:00:00:03", "P110",
                               modules={"Energy": energy(1800, 3.2)})
            return {"10.0.0.1": self.strip, "10.0.0.2": self.bulb}[host]

        async def try_connect_all(host, **kw):
            return None

        async def discover(**kw):
            return {"10.0.0.1": self.strip, "10.0.0.9": FakeDev(DeviceType.Plug, "Garage plug", "AA", "KP115")}
        kasa.Discover.discover_single = staticmethod(discover_single)
        kasa.Discover.try_connect_all = staticmethod(try_connect_all)
        kasa.Discover.discover = staticmethod(discover)

    async def asyncTearDown(self):
        import kasa
        kasa.Discover.discover_single, kasa.Discover.try_connect_all, kasa.Discover.discover = \
            (staticmethod(f) for f in self.real)
        self.tmp.cleanup()

    def make(self, **cfg):
        return KasaIntegration("kasa", "TP-Link", {"poll_seconds": 0.02, **cfg},
                               Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))

    async def run_briefly(self, k, secs=0.08):
        task = asyncio.create_task(k.run())
        await asyncio.sleep(secs)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_strip_outlets_bulb_and_energy(self):
        k = self.make(devices=[{"host": "10.0.0.1"}, {"host": "10.0.0.2", "name": "Lamp"}])
        await self.run_briefly(k)
        ents = {e.name: e for e in self.hub.all()}
        monitor, second, lamp = ents["Monitor"], ents["Desk strip 2"], ents["Lamp"]
        self.assertEqual(monitor.id, "kasa.50c7bf000001_8006abc00")  # MAC + outlet id: stable
        self.assertEqual((monitor.kind, monitor.state, monitor.attrs["power"], monitor.attrs["energy"]),
                         ("switch", "on", 23.46, 12.346))
        props = {p["id"]: p["value"] for p in properties_for(monitor)}
        self.assertEqual((props["voltage"], props["energyToday"], props["wifiSignal"]), (120.4, 0.25, -52))
        self.assertEqual((second.state, lamp.kind, lamp.attrs["brightness"], lamp.attrs["dimmable"]),
                         ("off", "light", 40, True))
        self.assertEqual((k.status, k.detail), ("connected", "2 switches and 1 light · on your network"))
        self.assertGreater(self.strip.updates, 1)  # polled
        await k.command(second.id, "turn_on")
        await k.command(lamp.id, "set_brightness", 75)
        self.assertEqual((self.strip.children[1].calls, self.bulb.modules["Light"].brightness), (["on"], 75))
        self.assertEqual(self.hub.get(second.id).state, "on")
        with self.assertRaises(CommandError):
            await k.command(monitor.id, "set_brightness", 10)

    async def test_tapo_needs_account_and_masks_it(self):
        k = self.make(devices=[{"host": "10.0.0.3"}])
        await self.run_briefly(k)
        self.assertEqual(k.status, "error")
        self.assertIn("needs your TP-Link account email and password", k.detail)
        k2 = self.make(devices=[{"host": "10.0.0.3"}], username="me@example.com", password="hunter2")
        await self.run_briefly(k2)
        self.assertEqual(self.seen_creds["10.0.0.3"].username, "me@example.com")
        kettle = next(e for e in self.hub.all() if e.name == "Kettle")
        self.assertEqual((kettle.state, kettle.attrs["power"]), ("on", 1800))
        masked = mask_config(KasaIntegration, k2.config)
        self.assertEqual((masked["password"], masked["username"]), ("••••", "me@example.com"))

    async def test_scan_my_network_only_when_asked(self):
        k = self.make(devices=[{"host": "10.0.0.1"}])
        options = await k.dynamic_options()
        self.assertEqual(options["devices"], [
            {"value": "10.0.0.1", "label": "Desk strip (HS300, 10.0.0.1)", "group": "Already added"},
            {"value": "10.0.0.9", "label": "Garage plug (KP115, 10.0.0.9)", "group": "Found"}])
        empty = self.make()
        task = asyncio.create_task(empty.run())
        await asyncio.sleep(0.02)
        self.assertEqual(empty.status, "pairing")
        self.assertIn("Scan my network", empty.detail)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
