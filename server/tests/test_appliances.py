"""Appliances, offline: Home Connect (Bosch, Thermador…) and LG ThinQ against fake services."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

import httpx

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.integrations.base import CommandError, Context  # noqa: E402
from newman.integrations.homeconnect import HomeConnectIntegration  # noqa: E402
from newman.integrations.lg_thinq import ThinQIntegration, parts, readings, region  # noqa: E402
from newman.kinds import KINDS, properties_for, render  # noqa: E402
from newman.state import Hub  # noqa: E402

DW, FRIDGE = "BOSCH-SHX-0001", "THERMADOR-FR-02"
OP = "BSH.Common.EnumType.OperationState."


def sse(*events):
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\nid: {ha}\n\n" for e, ha, d in events)


class FakeHomeConnect:
    def __init__(self):
        self.requests = []
        self.pending = 1          # "authorization_pending" answers before approval
        self.access = "tok-1"
        self.stream = ""
        self.remote_start = True

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path, form = req.url.path, dict(httpx.QueryParams(req.content.decode() if req.method == "POST" else ""))
        self.requests.append((req.method, path, req.content.decode()))
        if path == "/security/oauth/device_authorization":
            return httpx.Response(200, json={"device_code": "dev-code", "user_code": "WXYZ-1234", "interval": 0.01,
                                             "verification_uri_complete": "https://hc.example/verify?code=WXYZ",
                                             "expires_in": 60})
        if path == "/security/oauth/token":
            if form.get("grant_type") == "refresh_token":
                self.access = "tok-2"
                return httpx.Response(200, json={"access_token": "tok-2", "refresh_token": "ref-2", "expires_in": 86400})
            if self.pending:
                self.pending -= 1
                return httpx.Response(400, json={"error": "authorization_pending"})
            return httpx.Response(200, json={"access_token": "tok-1", "refresh_token": "ref-1", "expires_in": 86400})
        if req.headers.get("Authorization") != f"Bearer {self.access}":
            return httpx.Response(401, json={"error": {"key": "invalid_token"}})
        if path == "/api/homeappliances/events":
            return httpx.Response(200, text=self.stream)
        if path == "/api/homeappliances":
            return httpx.Response(200, json={"data": {"homeappliances": [
                {"haId": DW, "name": "Dishwasher", "type": "Dishwasher", "brand": "Bosch", "vib": "SHX88", "connected": True},
                {"haId": FRIDGE, "name": "Fridge", "type": "FridgeFreezer", "brand": "Thermador", "vib": "T36",
                 "connected": True}]}})
        if path == f"/api/homeappliances/{DW}/status":
            return httpx.Response(200, json={"data": {"status": [
                {"key": "BSH.Common.Status.OperationState", "value": OP + "Run"},
                {"key": "BSH.Common.Status.DoorState", "value": "BSH.Common.EnumType.DoorState.Closed"},
                {"key": "BSH.Common.Status.RemoteControlStartAllowed", "value": self.remote_start}]}})
        if path == f"/api/homeappliances/{FRIDGE}/status":
            return httpx.Response(200, json={"data": {"status": [
                {"key": "BSH.Common.Status.DoorState", "value": "BSH.Common.EnumType.DoorState.Closed"}]}})
        if path == f"/api/homeappliances/{FRIDGE}/settings":
            return httpx.Response(200, json={"data": {"settings": [
                {"key": "Refrigeration.FridgeFreezer.Setting.SetpointTemperatureRefrigerator", "value": 4, "unit": "°C"}]}})
        if path.endswith("/settings"):
            return httpx.Response(200, json={"data": {"settings": [
                {"key": "BSH.Common.Setting.PowerState", "value": "BSH.Common.EnumType.PowerState.On"}]}})
        if path == f"/api/homeappliances/{DW}/programs/active" and req.method == "GET":
            return httpx.Response(200, json={"data": {"key": "Dishcare.Dishwasher.Program.Eco50", "name": "Eco 50°", "options": [
                {"key": "BSH.Common.Option.RemainingProgramTime", "value": 3600, "unit": "seconds"},
                {"key": "BSH.Common.Option.ProgramProgress", "value": 40, "unit": "%"}]}})
        if path.endswith("/programs/active") and req.method == "GET":
            return httpx.Response(404, json={"error": {"key": "SDK.Error.NoProgramActive"}})
        if path.endswith("/programs/selected"):
            return httpx.Response(200, json={"data": {"key": "Dishcare.Dishwasher.Program.Auto2", "options": [
                {"key": "Dishcare.Dishwasher.Option.HalfLoad", "value": False}]}})
        if req.method in ("PUT", "DELETE"):
            return httpx.Response(204)
        return httpx.Response(404, json={"error": {"key": "SDK.Error.NotFound", "description": "nope"}})


class HomeConnectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        self.fake = FakeHomeConnect()
        self.hc = HomeConnectIntegration("homeconnect", "Home Connect", {"client_id": "CID"},
                                         Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        self.hc._http = httpx.AsyncClient(transport=httpx.MockTransport(self.fake))
        self.events = []

        async def on_event(ent, event, data):
            self.events.append((ent.id, event, data))
        self.hub.subscribe_events(on_event)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _signed_in(self):
        await self.hc._pair()
        await self.hc._load()
        await asyncio.sleep(0)

    async def test_device_sign_in_shows_a_link_and_keeps_only_the_token(self):
        task = asyncio.create_task(self.hc._pair())
        for _ in range(100):
            if self.hc.status == "pairing":
                break
            await asyncio.sleep(0.001)
        self.assertEqual(self.hc.status, "pairing")
        self.assertIn("https://hc.example/verify?code=WXYZ", self.hc.detail)
        self.assertIn("WXYZ-1234", self.hc.detail)
        await task
        saved = json.loads((Path(self.tmp.name) / "integrations" / "homeconnect" / "token.json").read_text())
        self.assertEqual((saved["access_token"], saved["refresh_token"]), ("tok-1", "ref-1"))
        self.assertNotIn("password", json.dumps(self.hc.config))

    async def test_reads_every_appliance(self):
        await self._signed_in()
        dw = self.hub.get("homeconnect.bosch_shx_0001")
        self.assertEqual((dw.kind, dw.name, dw.state), ("appliance", "Dishwasher", "running"))
        self.assertEqual((dw.attrs["remaining"], dw.attrs["progress"], dw.attrs["door"], dw.attrs["program"]),
                         (60, 40, "closed", "Eco 50°"))
        self.assertEqual(dw.attrs["applianceType"], "dishwasher")
        fridge = self.hub.get("homeconnect.thermador_fr_02")
        self.assertEqual(fridge.state, "on")  # a fridge is simply on
        p = {x["id"]: x for x in properties_for(fridge)}
        self.assertEqual(p["setpointTemperatureRefrigerator"]["unit"], "°C")
        self.assertEqual(p["door"]["value"], "closed")
        self.assertEqual(self.hc.status, "connected")
        self.assertEqual(self.hc.detail, "2 appliances")

    async def test_live_stream_finishes_alerts_and_drops(self):
        await self._signed_in()
        dw = "homeconnect.bosch_shx_0001"
        self.fake.stream = sse(
            ("KEEP-ALIVE", "", {}),
            ("NOTIFY", DW, {"items": [{"key": "BSH.Common.Option.RemainingProgramTime", "value": 120, "unit": "seconds"}]}),
            ("STATUS", DW, {"items": [{"key": "BSH.Common.Status.OperationState", "value": OP + "Finished"}]}),
            ("EVENT", DW, {"items": [{"key": "BSH.Common.Event.ProgramFinished",
                                      "value": "BSH.Common.EnumType.EventPresentState.Present"},
                                     {"key": "Dishcare.Dishwasher.Event.RinseAidNearlyEmpty",
                                      "value": "BSH.Common.EnumType.EventPresentState.Present"}]}),
            ("DISCONNECTED", FRIDGE, {}))
        await self.hc._stream()
        await asyncio.sleep(0)
        self.assertEqual(self.hub.get(dw).state, "finished")
        self.assertEqual(self.hub.get(dw).attrs["remaining"], 2)
        self.assertEqual([(e, d.get("detail")) for _, e, d in self.events],
                         [("finished", None), ("alert", "rinse aid nearly empty")])  # finished only once
        self.assertFalse(self.hub.get("homeconnect.thermador_fr_02").available)  # a fridge gone is offline
        await self.hc.handle("DISCONNECTED", DW, "")
        self.assertEqual((self.hub.get(dw).state, self.hub.get(dw).available), ("off", True))  # a dishwasher: off
        k = KINDS["appliance"]
        self.assertEqual(render(k.event("alert").body, "Dishwasher", k, k.event("alert"), "rinse aid nearly empty"),
                         "Dishwasher: rinse aid nearly empty")

    async def test_commands(self):
        await self._signed_in()
        dw = "homeconnect.bosch_shx_0001"
        await self.hc.command(dw, "start")
        method, path, body = self.fake.requests[-1]
        self.assertEqual((method, path), ("PUT", f"/api/homeappliances/{DW}/programs/active"))
        self.assertEqual(json.loads(body)["data"]["key"], "Dishcare.Dishwasher.Program.Auto2")
        await self.hc.command(dw, "pause")
        self.assertEqual(self.fake.requests[-1][1],
                         f"/api/homeappliances/{DW}/commands/BSH.Common.Command.PauseProgram")
        await self.hc.command(dw, "stop")
        self.assertEqual(self.fake.requests[-1][:2], ("DELETE", f"/api/homeappliances/{DW}/programs/active"))
        await self.hub.update(dw, attrs={"remoteStart": False})
        with self.assertRaisesRegex(CommandError, "remote start"):
            await self.hc.command(dw, "start")

    async def test_renews_access_when_refused(self):
        await self._signed_in()
        self.hc._tok["access_token"] = "stale"  # Home Connect no longer accepts it
        await self.hc.command("homeconnect.bosch_shx_0001", "pause")  # 401 -> renew -> retried with tok-2
        self.assertEqual(self.hc._tok["access_token"], "tok-2")
        self.assertEqual(self.fake.requests[-1][1],
                         f"/api/homeappliances/{DW}/commands/BSH.Common.Command.PauseProgram")


WASHER = "aa11-washer"
TOWER = "bb22-tower"
FRIDGE_LG = "cc33-fridge"


class FakeThinQ:
    def __init__(self):
        self.requests = []
        self.washer_state = "RUNNING"

    def __call__(self, req: httpx.Request) -> httpx.Response:
        assert req.url.host == "api-aic.lgthinq.com"
        assert req.headers["x-api-key"] and req.headers["Authorization"] == "Bearer PAT"
        self.requests.append((req.method, req.url.path, req.content.decode(), dict(req.headers)))
        p = req.url.path

        def ok(body):
            return httpx.Response(200, json={"messageId": "x", "response": body})
        if p == "/devices":
            return ok([{"deviceId": WASHER, "deviceInfo": {"deviceType": "DEVICE_WASHER", "alias": "Washer", "modelName": "WM4000"}},
                       {"deviceId": TOWER, "deviceInfo": {"deviceType": "DEVICE_WASHTOWER", "alias": "WashTower"}},
                       {"deviceId": FRIDGE_LG, "deviceInfo": {"deviceType": "DEVICE_REFRIGERATOR", "alias": "Garage fridge"}}])
        if p == f"/devices/{WASHER}/state":
            return ok({"runState": {"currentState": self.washer_state},
                       "timer": {"remainHour": 1, "remainMinute": 5, "totalHour": 1, "totalMinute": 30},
                       "remoteControlEnable": {"remoteControlEnabled": True}})
        if p == f"/devices/{WASHER}/profile":
            return ok({"property": {"operation": {"washerOperationMode": {
                "type": "enum", "mode": ["w"], "value": {"w": ["START", "STOP", "POWER_OFF", "WAKE_UP"]}}}}})
        if p == f"/devices/{TOWER}/state":
            return ok({"washer": {"runState": {"currentState": "END"}}, "dryer": {"runState": {"currentState": "DRYING"}}})
        if p == f"/devices/{TOWER}/profile":
            return ok({"washer": {"property": {"operation": {"washerOperationMode": {"value": {"w": ["START", "STOP"]}}}}},
                       "dryer": {"property": {"operation": {"dryerOperationMode": {"value": {"w": ["START", "STOP"]}}}}}})
        if p == f"/devices/{FRIDGE_LG}/state":
            return ok({"temperature": [{"targetTemperature": 3, "unit": "C", "locationName": "FRIDGE"},
                                       {"targetTemperature": -18, "unit": "C", "locationName": "FREEZER"}],
                       "doorStatus": [{"doorState": "CLOSE", "locationName": "MAIN"}]})
        if p.endswith("/profile"):
            return httpx.Response(400, json={"error": {"code": "2201", "message": "no profile"}})
        if p.endswith("/control"):
            if b"POWER_OFF" in req.content and TOWER in p:
                return httpx.Response(400, json={"error": {"code": "2301", "message": "remote off"}})
            return ok({})
        return httpx.Response(404, json={"error": {"code": "1205", "message": "no device"}})


class ThinQTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        self.fake = FakeThinQ()
        self.lg = ThinQIntegration("lg", "LG ThinQ", {"token": "PAT", "country": "CA"},
                                   Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        self.lg._http = httpx.AsyncClient(transport=httpx.MockTransport(self.fake))
        self.lg._client_id = "newman-test"
        self.events = []

        async def on_event(ent, event, data):
            self.events.append((ent.id, event))
        self.hub.subscribe_events(on_event)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _poll(self):
        for d in await self.lg._api("GET", "devices"):
            await self.lg._refresh(d)
        await asyncio.sleep(0)

    def test_shapes(self):
        self.assertEqual(region("CA"), "aic")
        self.assertEqual(region("DE"), "eic")
        self.assertEqual([p[0] for p in parts({"washer": {"runState": {}}, "dryer": {"runState": {}}})], ["washer", "dryer"])
        self.assertEqual(parts({"runState": {}})[0][0], "")
        state, attrs, _ = readings({"runState": {"currentState": "SPINNING"}, "timer": {"remainHour": 0, "remainMinute": 7}})
        self.assertEqual((state, attrs["remaining"]), ("running", 7))

    async def test_reads_appliances_and_parts(self):
        await self._poll()
        w = self.hub.get("lg.aa11_washer")
        self.assertEqual((w.state, w.attrs["remaining"], w.attrs["remoteStart"], w.attrs["model"]),
                         ("running", 65, True, "WM4000"))
        self.assertEqual(self.hub.get("lg.bb22_tower_washer").name, "WashTower Washer")
        self.assertEqual(self.hub.get("lg.bb22_tower_washer").state, "finished")
        self.assertEqual(self.hub.get("lg.bb22_tower_dryer").state, "running")
        f = self.hub.get("lg.cc33_fridge")
        p = {x["id"]: x for x in properties_for(f)}
        self.assertEqual((f.state, p["targetTemperatureFridge"]["value"], p["targetTemperatureFridge"]["unit"]),
                         ("on", 3, "°C"))
        self.assertEqual(p["targetTemperatureFreezer"]["value"], -18)
        self.assertEqual(p["door"]["value"], "closed")
        self.fake.washer_state = "END"
        await self._poll()
        self.assertEqual(self.events, [("lg.aa11_washer", "finished")])  # only the washer was running before

    async def test_commands(self):
        await self._poll()
        await self.lg.command("lg.aa11_washer", "start")
        ctl = [r for r in self.fake.requests if r[1].endswith("/control")]
        self.assertEqual(json.loads(ctl[-1][2]), {"operation": {"washerOperationMode": "START"}})
        self.assertEqual(ctl[-1][3]["x-conditional-control"], "true")
        await self.lg.command("lg.aa11_washer", "pause")  # LG pauses with STOP when there's no PAUSE
        ctl = [r for r in self.fake.requests if r[1].endswith("/control")]
        self.assertEqual(json.loads(ctl[-1][2]), {"operation": {"washerOperationMode": "STOP"}})
        await self.lg.command("lg.bb22_tower_dryer", "start")
        ctl = [r for r in self.fake.requests if r[1].endswith("/control")]
        self.assertEqual(json.loads(ctl[-1][2]), {"dryer": {"operation": {"dryerOperationMode": "START"}}})
        with self.assertRaisesRegex(CommandError, "can't turn off remotely"):
            await self.lg.command("lg.bb22_tower_dryer", "turn_off")
