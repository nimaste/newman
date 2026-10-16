"""Reolink's "Check everything every (minutes)": names, firmware and floodlights read again while connected."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.integrations import reolink as R  # noqa: E402
from newman.integrations.base import Context  # noqa: E402
from newman.state import Hub  # noqa: E402


class FakeBaichuan:
    def register_callback(self, *a):
        pass

    async def subscribe_events(self):
        pass

    async def check_subscribe_events(self):
        pass

    async def unsubscribe_events(self):
        pass


class FakeHost:
    made = []

    def __init__(self, host, user, password, port=80, use_https=False):
        self.channels, self.is_nvr, self.nvr_name, self.sw_version = [0], False, "", "v1"
        self.light, self.name, self.reads = False, "Driveway", 0
        self.baichuan = FakeBaichuan()
        FakeHost.made.append(self)

    async def get_host_data(self):
        self.reads += 1

    async def get_states(self):
        pass

    async def logout(self):
        pass

    def is_doorbell(self, ch):
        return False

    def camera_name(self, ch):
        return self.name

    def ai_supported_types(self, ch):
        return []

    def camera_model(self, ch):
        return "RLC-811A"

    def supported(self, ch, what):
        return what == "floodLight"

    def whiteled_state(self, ch):
        return self.light


class ReolinkCheckTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        FakeHost.made.clear()
        self.patches = [mock.patch.object(R, "Host", FakeHost),
                        mock.patch.object(R.ReolinkIntegration, "seconds_every", lambda self, key, unit=60.0: 0.05)]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    async def test_changes_are_read_again_and_it_stops_with_the_connection(self):
        d = Path(self.tmp.name)
        r = R.ReolinkIntegration("reolink", "Reolink", {"password": "p", "devices": [{"host": "10.0.0.5"}]},
                                 Context(self.hub, d, d))
        task = asyncio.create_task(r.run())
        for _ in range(50):
            await asyncio.sleep(0.02)
            if FakeHost.made and r.status == "connected":
                break
        host = FakeHost.made[-1]
        light = "reolink.10_0_0_5_floodlight_0"
        cam = "reolink.10_0_0_5_camera_0"
        self.assertEqual((self.hub.get(light).state, self.hub.get(cam).name), ("off", "Driveway"))
        host.light, host.name, host.sw_version = True, "Front drive", "v2"  # changed; no push came
        for _ in range(50):
            await asyncio.sleep(0.02)
            if self.hub.get(light).state == "on":
                break
        self.assertEqual(self.hub.get(light).state, "on")
        self.assertEqual((self.hub.get(cam).name, self.hub.get(cam).attrs["firmware"]), ("Front drive", "v2"))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        n = host.reads
        await asyncio.sleep(0.2)
        self.assertEqual(host.reads, n)

    def test_the_setting(self):
        cfg = {"username": "admin", "password": "p", "devices": [{"host": "10.0.0.5"}]}
        self.assertEqual(R.ReolinkIntegration.validate({**cfg, "check_minutes": 0}),
                         ["Check everything every (minutes): 1 or more"])
        self.assertEqual(R.ReolinkIntegration.validate({**cfg, "check_minutes": 15}), [])
