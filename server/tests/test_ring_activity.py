"""A lock notification waiting a moment for "who" (whoLookup) - used for ring-mqtt's locks and alarm
while the Ring connection runs alongside it. Ring's own sign-in and live "who" are in test_ring.py
(the separate "Ring activity" connection became part of it)."""
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.automations import engine as E  # noqa: E402
from newman.config import config  # noqa: E402
from test_auth import H  # noqa: E402


class WaitForWhoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_lock_notification_waits_for_the_name(self):
        hub, engine = self.h.hub, self.h.engine
        hub.upsert("mqtt.side", kind="lock", name="Side Door", integration="mqtt")
        await hub.update("mqtt.side", state="LOCKED", attrs={"whoLookup": True})
        await asyncio.sleep(0)
        engine.save({"name": "Lock activity", "trigger": {"type": "state", "entities": ["mqtt.side"], "to": "any"},
                     "recipients": [self.h.store.add_person("Chris")["id"]], "notify": {"enabled": True}})
        fired = []
        orig = engine._hit

        async def spy(a, hits, ent, trig):
            fired.append(trig)
            await orig(a, hits, ent, trig)
        engine._hit = spy
        await hub.update("mqtt.side", state="UNLOCKED")
        await asyncio.sleep(0.6)
        self.assertEqual(fired, [])  # waiting for who…
        await hub.update("mqtt.side", attrs={"changedBy": "Marlina", "changedAt": time.time()})
        for _ in range(30):
            if fired:
                break
            await asyncio.sleep(0.1)
        self.assertEqual(fired[0]["by"], "Marlina")

    async def test_locked_by_hand_says_so_without_waiting(self):
        hub, engine = self.h.hub, self.h.engine
        hub.upsert("ring.side", kind="lock", name="Side Door", integration="ring")
        await hub.update("ring.side", state="UNLOCKED", attrs={"whoLookup": True})
        await asyncio.sleep(0)
        engine.save({"name": "Lock activity", "trigger": {"type": "state", "entities": ["ring.side"], "to": "any"},
                            "recipients": [self.h.store.add_person("Chris")["id"]], "notify": {"enabled": True}})
        fired = []
        orig = engine._hit

        async def spy(a, hits, ent, trig):
            fired.append(trig)
            await orig(a, hits, ent, trig)
        engine._hit = spy
        started = time.time()
        await hub.update("ring.side", state="LOCKED")
        await asyncio.sleep(0.2)
        await hub.update("ring.side", attrs={"changedHow": "manually", "changedHowAt": time.time(), "changedByNoOne": True})
        for _ in range(40):
            if fired:
                break
            await asyncio.sleep(0.1)
        self.assertLess(time.time() - started, 3)  # didn't sit out the whole wait for a name
        self.assertEqual((fired[0]["by"], fired[0]["how"]), (None, "manually"))
        from newman.automations import describe as D
        _, body = D.notification_text(next(iter(engine.autos.values())), hub, pending=[], trigger=fired[0])
        self.assertTrue(body.startswith("Locked manually at"), body)

    async def test_gives_up_and_notifies_anyway(self):
        hub, engine = self.h.hub, self.h.engine
        hub.upsert("mqtt.side", kind="lock", name="Side Door", integration="mqtt")
        await hub.update("mqtt.side", state="LOCKED", attrs={"whoLookup": True})
        await asyncio.sleep(0)
        engine.save({"name": "Lock activity", "trigger": {"type": "state", "entities": ["mqtt.side"], "to": "any"},
                     "recipients": [self.h.store.add_person("Chris")["id"]], "notify": {"enabled": True}})
        fired = []
        orig = engine._hit

        async def spy(a, hits, ent, trig):
            fired.append(trig)
            await orig(a, hits, ent, trig)
        engine._hit = spy
        old = E.WHO_WAIT_S
        E.WHO_WAIT_S = 0.6
        try:
            await hub.update("mqtt.side", state="UNLOCKED")
            for _ in range(30):
                if fired:
                    break
                await asyncio.sleep(0.1)
        finally:
            E.WHO_WAIT_S = old
        self.assertEqual((len(fired), fired[0]["by"]), (1, None))
