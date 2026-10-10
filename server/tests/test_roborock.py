"""Roborock, offline: sign-in by email code, both protocol generations, events and commands."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.integrations.base import Context  # noqa: E402
from newman.integrations.roborock import RoborockIntegration, q7_state, v1_state  # noqa: E402
from newman.kinds import properties_for  # noqa: E402
from newman.state import Hub  # noqa: E402


class FakeV1:
    def __init__(self):
        self.sent = []
        self.status = NS(state_name="charging_complete", battery=100, fan_speed_name="balanced",
                         square_meter_clean_area=0, clean_time=0, error_code=0, error_code_name="none",
                         refresh=self._noop)
        self.consumables = NS(filter_time_left=75 * 3600, main_brush_time_left=300 * 3600,
                              side_brush_time_left=20 * 3600, sensor_time_left=0, refresh=self._noop)
        self.rooms = NS(rooms=[NS(segment_id=16, name="Kitchen"), NS(segment_id=17, name="Living room")],
                        refresh=self._noop)
        self.command = NS(send=self._send)

    async def _noop(self):
        pass

    async def _send(self, cmd, params=None):
        self.sent.append((cmd, params))


class FakeQ7:
    def __init__(self):
        self.calls = []

    async def query_values(self, props):
        return NS(status=NS(value="sweep_moping"), fault=None, quantity=64, cleaning_area=12, cleaning_time=18,
                  wind=NS(name="STRONG"), hypa=80, main_brush=91, side_brush=70, main_sensor=100)

    def __getattr__(self, name):
        async def call(*a):
            self.calls.append((name, a))
        return call


class RoborockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        self.saved = []
        self.r = RoborockIntegration("roborock", "Roborock", {"email": "chris@example.com"},
                                     Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        self.r.save_config = self.saved.append
        self.events = []

        async def on_event(ent, event, data):
            self.events.append((event, data))
        self.hub.subscribe_events(on_event)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def test_state_maps(self):
        self.assertEqual([v1_state(s) for s in ("segment_cleaning", "returning_home", "charging_complete",
                                                "washing_the_mop", "something_new")],
                         ["cleaning", "returning", "docked", "washing", "idle"])
        self.assertEqual(q7_state(NS(value="mop_airdrying")), "drying")

    async def test_sign_in_asks_for_a_code_then_keeps_only_the_session(self):
        import roborock.web_api as web
        calls = []

        class FakeApi:
            def __init__(self, username, **kw):
                self._device_identifier = os.urandom(4).hex()  # random per client, like the library

            async def request_code_v4(self):
                calls.append(("code requested", self._device_identifier))

            async def code_login_v4(self, code):
                calls.append(("login", code, self._device_identifier))
                return NS(as_dict=lambda: {"token": "abc", "rriot": {}})
        real, web.RoborockApiClient = web.RoborockApiClient, FakeApi
        try:
            task = asyncio.create_task(self.r._sign_in())
            await asyncio.sleep(0.05)
            self.assertEqual(self.r.status, "pairing")
            self.assertIn("emailed a code", self.r.detail)
            task.cancel()
            self.r.config = {"email": "chris@example.com", "code": "123456"}
            import roborock.data.containers as c
            real_ud, c.UserData = c.UserData, NS(from_dict=lambda d: d)
            try:
                await self.r._sign_in()
            finally:
                c.UserData = real_ud
        finally:
            web.RoborockApiClient = real
        # The code is tied to the client that asked for it: the sign-in must reuse that client's id.
        asked_with = calls[0][1]
        self.assertEqual(calls[-1], ("login", "123456", asked_with))
        self.assertEqual(json.loads(self.r._session_file.read_text()), {"token": "abc", "rriot": {}})
        self.assertEqual(self.saved[-1], {"email": "chris@example.com"})  # the one-time code isn't kept

    async def test_v1_vacuum_status_events_and_commands(self):
        p = FakeV1()
        d = NS(v1_properties=p, b01_q7_properties=None, b01_q10_properties=None, name="Qrevo",
               product=NS(model="roborock.vacuum.test"), is_local_connected=True)
        eid = "roborock.qrevo"
        self.r._devices[eid] = d
        self.hub.upsert(eid, kind="vacuum", name="Qrevo", integration="roborock")
        await self.r._refresh(eid, d)
        ent = self.hub.get(eid)
        props = {x["id"]: x["value"] for x in properties_for(ent)}
        self.assertEqual((ent.state, props["battery"], props["filterLeft"], props["sideBrushLeft"]), ("docked", 100, 50, 10))
        self.assertEqual(ent.attrs["rooms"], [{"id": 16, "name": "Kitchen"}, {"id": 17, "name": "Living room"}])
        # A clean, then home: "finished" fires once.
        p.status.state_name = "segment_cleaning"
        await self.r._refresh(eid, d)
        p.status.state_name = "charging"
        await self.r._refresh(eid, d)
        await asyncio.sleep(0.02)
        self.assertEqual([e for e, _ in self.events], ["finished"])
        # Stuck: "error" with the problem.
        p.status.state_name, p.status.error_code, p.status.error_code_name = "error", 2, "main_brush_jammed"
        await self.r._refresh(eid, d)
        await asyncio.sleep(0.02)
        self.assertEqual(self.events[-1], ("error", {"problem": "main brush jammed"}))
        p.status.error_code = 0
        await self.r.command(eid, "clean_rooms", [16, "17"])
        await self.r.command(eid, "dock")
        self.assertEqual(p.sent, [("app_segment_clean", [{"segments": [16, 17], "repeat": 1}]), ("app_charge", None)])

    async def test_room_names_in_the_automation_sentence(self):
        from newman.automations.describe import action_text
        self.hub.upsert("roborock.robo", kind="vacuum", name="Robo", integration="roborock")
        await self.hub.update("roborock.robo", state="docked",
                              attrs={"rooms": [{"id": 16, "name": "Kitchen"}, {"id": 20, "name": "Office"}]})
        self.assertEqual(action_text({"entities": ["roborock.robo"], "command": "clean_rooms", "value": [16, 20]}, self.hub),
                         "clean rooms Kitchen and Office on Robo")

    async def test_newer_q_series(self):
        q = FakeQ7()
        d = NS(v1_properties=None, b01_q7_properties=q, b01_q10_properties=None, name="Q7", product=NS(model="q7"),
               is_local_connected=True)
        eid = "roborock.q7"
        self.r._devices[eid] = d
        self.hub.upsert(eid, kind="vacuum", name="Q7", integration="roborock")
        await self.r._refresh(eid, d)
        ent = self.hub.get(eid)
        self.assertEqual((ent.state, ent.attrs["battery"], ent.attrs["fanSpeed"]), ("cleaning", 64, "strong"))
        await self.r.command(eid, "locate")
        await self.r.command(eid, "clean_rooms", [3])
        self.assertEqual([c[0] for c in q.calls], ["find_me", "clean_segments"])
