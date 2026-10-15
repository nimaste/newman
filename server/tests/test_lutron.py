"""Lutron Caséta, offline: a stand-in Smart Bridge (pylutron-caseta's Smartbridge) with one Pico, whose buttons
report Press and Release as LEAP's ButtonStatus does."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.automations import engine as engine_mod  # noqa: E402
from newman.integrations import lutron as lutron_mod  # noqa: E402
from newman.integrations.base import Context, Integration  # noqa: E402
from newman.integrations.lutron import LutronIntegration  # noqa: E402
from newman.kinds import KINDS  # noqa: E402
from newman.state import Hub  # noqa: E402
from test_engine import Base  # noqa: E402

PRESS, RELEASE = "Press", "Release"  # LEAP ButtonEvent EventType


class FakeBridge:
    """A Smart Bridge with a Pico 3-button raise/lower in the dining room (button ids 101-105)."""

    def __init__(self):
        self.devices = {"1": {"type": "SmartBridge", "name": "Smart Bridge", "area": None},
                        "7": {"type": "Pico3ButtonRaiseLower", "name": "Dining Room_Pico Remote",
                              "device_name": "Pico Remote", "area": "3", "model": "PJ2-3BRL-GXX-X01"}}
        self.areas = {"3": {"name": "Dining Room"}}
        self.buttons = {str(101 + n): {"parent_device": "7", "button_number": n} for n in range(5)}
        self.subscribers = {}

    async def connect(self):
        pass

    def is_connected(self):
        return True

    async def close(self):
        pass

    def get_devices(self):
        return self.devices

    def get_buttons(self):
        return self.buttons

    def add_subscriber(self, dev_id, cb):
        pass

    def add_button_subscriber(self, button_id, cb):
        self.subscribers[button_id] = cb


async def pico(hub, data):
    """A connected Lutron integration with the fake bridge; returns (integration, bridge, emitted events)."""
    bridge = FakeBridge()
    lut = LutronIntegration("lutron", "Lutron Caséta", {"host": "10.0.0.9"}, Context(hub, data, data))
    with mock.patch.object(lutron_mod.Smartbridge, "create_tls", return_value=bridge):
        await lut._connect()
    events = []

    async def on_event(ent, event, data_):
        events.append((ent.id, event))
    hub.subscribe_events(on_event)
    return lut, bridge, events


RAISE, LOWER = "104", "105"


class PicoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        fast = mock.patch.object(lutron_mod, "HOLD_S", 0.1)
        fast.start()
        self.addCleanup(fast.stop)
        self.lut, self.bridge, self.events = await pico(self.hub, Path(self.tmp.name))

    async def asyncTearDown(self):
        await self.lut._close()
        self.tmp.cleanup()

    def button(self, btn, what):
        self.bridge.subscribers[btn](what)

    def fired(self):
        return [e for _, e in self.events]

    async def test_each_button_is_pressed_long_pressed_and_let_go(self):
        remote = self.hub.get("lutron.remote_7")
        self.assertEqual(remote.name, "Pico Remote")
        # The old ids are kept, each followed by its long press and its let go.
        self.assertEqual(remote.attrs["events"], [x for b in ("on", "favorite", "off", "raise", "lower")
                                                  for x in (b, f"{b}_long", f"{b}_released")])
        kind = KINDS["remote"]
        self.assertEqual(kind.event("raise").label, "“Raise” is pressed")
        self.assertEqual(kind.event("raise_long").label, "“Raise” is long-pressed")
        self.assertEqual(kind.event("raise_released").label, "“Raise” is let go after being long-pressed")
        self.assertEqual(kind.event("lower_long").ends, "lower_released")  # what a "…while held" stops on

    async def test_a_short_press_fires_once_let_go(self):
        self.button(RAISE, PRESS)
        await asyncio.sleep(0.03)
        self.assertEqual(self.events, [])  # not yet: it might be a long press
        self.button(RAISE, RELEASE)
        await asyncio.sleep(0.2)
        self.assertEqual(self.events, [("lutron.remote_7", "raise")])

    async def test_holding_is_a_long_press_and_letting_go_never_a_press(self):
        self.button(LOWER, PRESS)
        await asyncio.sleep(0.2)
        self.assertEqual(self.fired(), ["lower_long"])
        self.button(LOWER, PRESS)  # a repeated Press while it's down changes nothing
        await asyncio.sleep(0.2)
        self.button(LOWER, RELEASE)
        await asyncio.sleep(0.05)
        self.assertEqual(self.fired(), ["lower_long", "lower_released"])
        # A Release it never saw go down (say, from before a reconnect) is nothing.
        self.button(LOWER, RELEASE)
        await asyncio.sleep(0.05)
        self.assertEqual(self.fired(), ["lower_long", "lower_released"])

    async def test_buttons_are_told_apart_and_a_lost_connection_forgets_them(self):
        self.button(RAISE, PRESS)
        self.button("101", PRESS)
        self.button("101", RELEASE)
        await asyncio.sleep(0.2)
        self.assertEqual(self.fired(), ["on", "raise_long"])
        await self.lut._close()  # the bridge went away mid-hold: no long press from before is pending
        self.button("103", PRESS)
        await self.lut._close()
        await asyncio.sleep(0.2)
        self.assertEqual(self.fired(), ["on", "raise_long"])


class PicoHoldToDimTests(Base):
    """Holding a Pico's raise brightens a light until it's let go - and is never also its "raise"."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        calls = self.calls = []

        class FakeLights(Integration):  # can't ramp or fade by itself: plain steps
            def __init__(self):
                pass

            async def command(self, eid, command, value=None):
                calls.append((command, value))
        self.integrations.instances["lutron"] = FakeLights()
        self.hub.upsert("lutron.device_5", kind="light", name="Chandelier", integration="lutron")
        await self.hub.update("lutron.device_5", state="on", attrs={"dimmable": True, "brightness": 20})
        for target, value in ((engine_mod, ("RAMP_EVERY_S", 0.03)), (lutron_mod, ("HOLD_S", 0.1))):
            p = mock.patch.object(target, *value)
            p.start()
            self.addCleanup(p.stop)
        self.lut, self.bridge, _ = await pico(self.hub, Path(self.tmp.name))
        self.addAsyncCleanup(self.lut._close)
        for event, command in (("raise", "turn_off"), ("raise_long", "brighten_while_held")):
            self.engine.save({"name": event, "notify": {"enabled": False},
                              "trigger": {"type": "event", "entity": "lutron.remote_7", "event": event},
                              "actions": [{"entity": "lutron.device_5", "command": command}]})

    async def test_described_in_words(self):
        out = self.engine.describe({"trigger": {"type": "event", "entity": "lutron.remote_7", "event": "raise_long"},
                                    "notify": {"enabled": False},
                                    "actions": [{"entity": "lutron.device_5", "command": "brighten_while_held"}]})
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["summary"], "When the Raise button on Pico Remote is pressed and held, "
                                         "brighten Chandelier while held.")

    async def test_hold_ramps_until_let_go(self):
        self.bridge.subscribers[RAISE](PRESS)
        await asyncio.sleep(0.25)
        self.bridge.subscribers[RAISE](RELEASE)
        for _ in range(200):
            await asyncio.sleep(0.01)
            if not self.engine._ramps:
                break
        else:
            self.fail("the ramp never stopped")
        steps = [v for c, v in self.calls]
        self.assertTrue(all(c == "set_brightness" for c, _ in self.calls), self.calls)  # never the "raise" turn off
        self.assertGreaterEqual(len(steps), 2)
        self.assertEqual(steps, list(range(30, 30 + 10 * len(steps), 10)))
        n = len(self.calls)
        await asyncio.sleep(0.15)
        self.assertEqual(len(self.calls), n)  # let go: no more steps

    async def test_a_tap_is_just_the_press(self):
        self.bridge.subscribers[RAISE](PRESS)
        self.bridge.subscribers[RAISE](RELEASE)
        await asyncio.sleep(0.25)
        self.assertEqual(self.calls, [("turn_off", None)])


if __name__ == "__main__":
    unittest.main()
