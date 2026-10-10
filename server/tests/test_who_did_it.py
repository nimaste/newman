"""Who changed a device: the Ring alarm's "disarmed by" reaches notifications as "by Marlina"."""
import asyncio
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.config import config  # noqa: E402
from newman.integrations.base import Context  # noqa: E402
from newman.integrations import mqtt as M  # noqa: E402
from test_auth import H  # noqa: E402
from test_mqtt_devices import FakeClient  # noqa: E402

RING = "ring/loc1/alarm/panel1"


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


class WhoDidItTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.m = M.MqttIntegration("mqtt", "MQTT devices", {"host": "fake"}, Context(self.h.hub, data, data))
        self.m._client = FakeClient()
        self.seen = []
        orig = self.h.engine._recent_attribution

        def spy(eid):
            who = orig(eid)
            self.seen.append(who)
            return who
        self.h.engine._recent_attribution = spy

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def msg(self, topic, payload):
        await self.m._on_message(topic, payload.encode())

    async def test_alarm_disarmed_by_comes_through(self):
        await self.msg("homeassistant/alarm_control_panel/panel1/alarm/config", json.dumps({
            "name": "Woodbridge Alarm", "unique_id": "panel1_alarm", "state_topic": f"{RING}/alarm/state",
            "json_attributes_topic": f"{RING}/alarm/attributes", "command_topic": f"{RING}/alarm/command",
            "device": {"name": "Woodbridge Alarm", "identifiers": ["panel1"]}}))
        eid = next(e.id for e in self.h.hub.all() if e.kind == "alarm")
        await self.h.hub.update(eid, state="armed_away")
        self.h.engine.save({"name": "Alarm activity", "trigger": {"type": "state", "entities": [eid], "to": "any"},
                            "recipients": [self.h.store.add_person("Chris")["id"]], "notify": {"enabled": True}})
        # Ring sends the new mode first, then (a moment later) who did it.
        await self.msg(f"{RING}/alarm/state", "disarmed")
        self.assertEqual(self.h.hub.get(eid).state, "armed_away")  # held for the name…
        await self.msg(f"{RING}/alarm/attributes", json.dumps({
            "lastDisarmedBy": "Marlina", "lastDisarmedTime": iso(time.time()), "lastArmedBy": "Chris",
            "lastArmedTime": iso(time.time() - 3600), "targetState": "disarmed"}))
        ent = self.h.hub.get(eid)
        self.assertEqual((ent.state, ent.attrs["changedBy"]), ("disarmed", "Marlina"))  # …and released with it
        await asyncio.sleep(0.05)
        self.assertIn("Marlina", self.seen)  # the automation's trigger carries "by Marlina"

    async def test_no_name_still_arrives_and_old_names_dont_count(self):
        await self.msg("homeassistant/alarm_control_panel/panel1/alarm/config", json.dumps({
            "name": "Woodbridge Alarm", "unique_id": "panel1_alarm", "state_topic": f"{RING}/alarm/state",
            "json_attributes_topic": f"{RING}/alarm/attributes", "device": {"name": "Woodbridge Alarm"}}))
        eid = next(e.id for e in self.h.hub.all() if e.kind == "alarm")
        await self.h.hub.update(eid, state="disarmed")
        old = M.HOLD_FOR_WHO_S
        M.HOLD_FOR_WHO_S = 0.05
        try:
            await self.msg(f"{RING}/alarm/state", "armed_home")
            await asyncio.sleep(0.15)
        finally:
            M.HOLD_FOR_WHO_S = old
        self.assertEqual(self.h.hub.get(eid).state, "armed_home")  # no attributes came: shown anyway
        await self.h.hub.update(eid, attrs={"changedBy": "Chris", "changedAt": time.time() - 3600})
        self.assertIsNone(self.h.engine._recent_attribution(eid))  # an hour-old name isn't this change's
