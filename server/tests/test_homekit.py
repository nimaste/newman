"""HomeKit, offline: "Check everything every" asks a Wi-Fi accessory for its values again; one on Bluetooth or
Thread is only listened to (asking would wake it)."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.integrations.base import Context  # noqa: E402
from newman.integrations.homekit import HomeKitIntegration  # noqa: E402
from newman.state import Hub  # noqa: E402


class FakePairing:
    def __init__(self):
        self.reads = 0

    async def get_characteristics(self, keys):
        self.reads += 1
        return {(1, 10): {"value": 20.5 + self.reads}}


class FakeController:
    def __init__(self, pairing):
        self.pairing = pairing

    def load_pairing(self, _id, _data):
        return self.pairing

    async def async_stop(self):
        pass


class CheckEverythingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        p = mock.patch.object(HomeKitIntegration, "seconds_every", lambda self, key, unit=60.0: 0.05)
        p.start()
        self.addCleanup(p.stop)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def started(self, connection):
        data = Path(self.tmp.name)
        hk = HomeKitIntegration("homekit", "HomeKit device", {"device_id": "AA:BB", "code": "123-45-678"},
                                Context(self.hub, data, data))
        hk._dir.mkdir(parents=True, exist_ok=True)
        hk._file.write_text(json.dumps({"Connection": connection, "AccessoryPairingID": "AA:BB"}))
        pairing = FakePairing()

        async def controller():
            return FakeController(pairing)

        async def connect():
            hk.hub.upsert("homekit.temp_1_9", kind="temperature", name="Hall", integration=hk.id)
            hk._chars = {(1, 10): ("homekit.temp_1_9", "temperature")}
        hk._controller, hk._connect = controller, connect
        task = asyncio.create_task(hk.run())
        await asyncio.sleep(0.3)
        return hk, pairing, task

    async def test_a_wifi_accessory_is_asked_again_until_stopped(self):
        hk, pairing, task = await self.started("IP")
        self.assertGreaterEqual(pairing.reads, 2)
        await asyncio.sleep(0.05)
        self.assertEqual(self.hub.get("homekit.temp_1_9").state, f"{20.5 + pairing.reads:.1f}")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        n = pairing.reads
        await asyncio.sleep(0.15)
        self.assertEqual(pairing.reads, n)

    async def test_a_thread_or_bluetooth_accessory_is_never_asked(self):
        for connection in ("CoAP", "BLE"):
            hk, pairing, task = await self.started(connection)
            self.assertEqual(pairing.reads, 0, connection)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def test_the_setting(self):
        f = next(x for x in HomeKitIntegration.fields if x.key == "check_minutes")
        self.assertEqual((f.default, f.minimum), (1, 1))  # what it did before: every minute


if __name__ == "__main__":
    unittest.main()
