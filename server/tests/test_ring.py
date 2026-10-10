"""Ring, offline, against a fake Ring: sign-in with the texted code, the move from the old "Ring activity"
connection, the live connection (device list -> devices, live updates, who did it, commands,
reconnecting, token renewal), and swapping ring-mqtt's devices for Ring's everywhere they're used."""
import asyncio
import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.integrations import REGISTRY  # noqa: E402
from newman.integrations import ring as R  # noqa: E402
from newman.integrations.base import CommandError, Context, Integration  # noqa: E402
from newman.integrations.manager import IntegrationManager  # noqa: E402
from newman.state import Hub  # noqa: E402
from newman.store import Store  # noqa: E402
from test_auth import H  # noqa: E402


def dev(zid, type_, name, general=None, **device):
    return {"general": {"v2": {"zid": zid, "deviceType": type_, "name": name, **(general or {})}},
            "device": {"v1": device}}


def upd(zid, impulse=None, general=None, **device):
    item = {"general": {"v2": {"zid": zid, **(general or {})}}, "device": {"v1": device}}
    if impulse:
        item["impulse"] = {"v1": [{"impulseType": impulse, "data": {}}]}
    return item


def devices():
    return [
        # Ring tags the real panel "hidden" (seen live) - it must still come in.
        dev("z-panel", "security-panel", "Security Panel", {"tags": ["hidden"]}, mode="none", siren={"state": "off"},
            alarmInfo=None),
        dev("z-base", "hub.redsky", "Base Station", {"batteryLevel": 99, "acStatus": "ok", "commStatus": "ok"}),
        dev("z-side", "lock", "Side Door", {"batteryLevel": 80, "tamperStatus": "ok", "commStatus": "ok",
                                             "manufacturerName": "Yale", "serialNumber": "Y123"}, locked="locked"),
        dev("z-front", "sensor.contact", "Front Door", {"batteryLevel": 15, "tamperStatus": "tamper", "commStatus": "ok"},
            faulted=False),
        dev("z-hall", "sensor.motion", "Hallway Motion", {"batteryLevel": 90, "batteryStatus": "ok"}, faulted=False),
        dev("z-smoke", "alarm.smoke", "Kitchen Smoke", alarmStatus="inactive"),
        dev("z-co", "alarm.co", "Kitchen CO", alarmStatus="active"),
        dev("z-flood", "sensor.flood-freeze", "Basement", flood={"faulted": False}, freeze={"faulted": False}),
        dev("z-keypad", "security-keypad", "Front Keypad", {"batteryLevel": 90, "acStatus": "error"}),
        dev("z-siren", "siren", "Upstairs Siren", sirenStatus="inactive"),
        dev("z-code-lisa", "access-code", "Lisa"),
        dev("z-zwave", "adapter.zwave", "Z-Wave"),
    ]


def flatten_zid(item):
    return item["general"]["v2"]["zid"]


PEOPLE = {"u-marlina": {"firstName": "Marlina", "lastName": "Garito"}, "u-chris": {"firstName": "Chris"}}


class FakeRing:
    """Ring's REST endpoints plus the live websocket, speaking the protocol as ring-client-api's
    location.js expects it."""

    def __init__(self):
        self.devices = devices()
        self.refresh, self.access, self.n = "ref0", {"acc0"}, 0
        self.oauth_calls, self.hardware_ids = [], set()
        self.tickets, self.sockets, self.sent, self.lookups = [], [], [], []
        self.panel_mode, self.refuse_arm = "none", False
        self.history = []  # Ring's activity history (newest first), as rs/history returns it
        self.port = 0

    def app(self):
        app = web.Application()
        app.router.add_post("/oauth/token", self.oauth)
        app.router.add_post("/clients_api/session", lambda r: self.authed(r, {}))
        app.router.add_get("/devices/v1/locations", lambda r: self.authed(
            r, {"user_locations": [{"location_id": "loc1", "name": "Woodbridge"}]}))
        app.router.add_get("/api/v1/clap/tickets", self.ticket)
        app.router.add_post("/api/v1/rs/users/summaries", self.users)
        app.router.add_get("/api/v1/rs/history", lambda r: self.authed(r, self.history))
        app.router.add_get("/ws", self.ws)
        return app

    def ok(self, req):
        return req.headers.get("Authorization", "")[7:] in self.access

    async def authed(self, req, body):
        return web.json_response(body) if self.ok(req) else web.json_response({"error": "unauthorized"}, status=401)

    def issue(self):
        self.n += 1
        self.access, self.refresh = {f"acc{self.n}"}, f"ref{self.n}"
        return web.json_response({"access_token": f"acc{self.n}", "refresh_token": self.refresh, "expires_in": 3600})

    async def oauth(self, req):
        body = await req.json()
        self.oauth_calls.append(body["grant_type"])
        self.hardware_ids.add(req.headers.get("hardware_id"))
        if body["grant_type"] == "password":
            code = req.headers.get("2fa-code")
            if not code:
                return web.json_response({"tsv_state": "sms", "phone": "+1xxxxxx5309"}, status=412)
            if code != "123456":
                return web.json_response({"error": "Verification Code is invalid or expired"}, status=400)
            return self.issue()
        if body.get("refresh_token") != self.refresh:  # Ring retires a refresh token once it's used
            return web.json_response({"error": "invalid_grant"}, status=401)
        return self.issue()

    async def ticket(self, req):
        if not self.ok(req):
            return web.json_response({"error": "unauthorized"}, status=401)
        self.tickets.append(f"tick{len(self.tickets) + 1}")
        return web.json_response({"assets": [{"uuid": "asset1", "kind": "base_station_v1"},
                                             {"uuid": "cam1", "kind": "doorbell_v4"}],
                                  "ticket": self.tickets[-1], "host": f"127.0.0.1:{self.port}"})

    async def users(self, req):
        if not self.ok(req):
            return web.json_response({"error": "unauthorized"}, status=401)
        ids = await req.json()
        self.lookups += ids
        return web.json_response([PEOPLE[i] for i in ids if i in PEOPLE])

    async def ws(self, req):
        if req.query.get("authcode") not in self.tickets or req.query.get("ack") != "false":
            return web.Response(status=403)
        ws = web.WebSocketResponse()
        await ws.prepare(req)
        self.sockets.append(ws)
        async for m in ws:
            outer = json.loads(m.data)
            self.sent.append(outer)
            msg = outer["msg"]
            if msg["msg"] == "DeviceInfoDocGetList":
                await ws.send_json({"channel": "message", "msg": {
                    "msg": "DeviceInfoDocGetList", "datatype": "DeviceInfoDocType", "src": msg["dst"],
                    "body": self.devices}})
            elif msg["msg"] == "DeviceInfoSet":
                for c in (msg["body"][0].get("command") or {}).get("v1") or []:
                    if c["commandType"] == "security-panel.switch-mode":
                        if not self.refuse_arm:
                            self.panel_mode = c["data"]["mode"]
                        await self.push(upd("z-panel", mode=self.panel_mode))
        return ws

    async def push(self, *items, ctx=None, channel="DataUpdate", datatype="DeviceInfoDocType"):
        for item in items:  # Ring's next device list says the same
            for d in self.devices:
                if d["general"]["v2"]["zid"] == item["general"]["v2"]["zid"]:
                    d["general"]["v2"].update(item["general"]["v2"])
                    d["device"]["v1"].update(item["device"]["v1"])
        msg = {"msg": "DataUpdate", "datatype": datatype, "src": "asset1", "body": list(items)}
        if ctx:
            msg["context"] = ctx
        await self.sockets[-1].send_json({"channel": channel, "msg": msg})

    def commands(self):
        return [o["msg"] for o in self.sent if o["msg"]["msg"] == "DeviceInfoSet"]


class FakeMqtt(Integration):
    """The MQTT connection ring-mqtt's devices come in through (not connected to anything)."""
    type = "mqtt"
    name = "MQTT devices"

    def __init__(self, ctx):
        super().__init__("mqtt", "MQTT devices", {}, ctx)


class RingBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.hub = Hub()
        self.instances = {}
        self.saved = []
        self.fake = FakeRing()
        self.server = TestServer(self.fake.app())
        await self.server.start_server()
        self.fake.port = self.server.port
        base = f"http://127.0.0.1:{self.server.port}"
        for name, value in (("OAUTH", base + "/oauth/token"), ("CLIENT_API", base + "/clients_api/"),
                            ("DEVICE_API", base + "/devices/v1/"), ("APP_API", base + "/api/v1/"),
                            ("USERS_API", base + "/api/v1/"), ("WS_URL", "ws://{host}/ws?authcode={ticket}&ack=false"),
                            ("RECONNECT_MIN_S", 0.05), ("TWIN_CHECK_S", 0.2), ("KEYPAD_MOTION_S", 0.3),
                            ("ARM_CHECK_S", 2.0)):
            p = mock.patch.object(R, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.tasks = []

    async def asyncTearDown(self):
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.server.close()
        self.tmp.cleanup()

    def signed_in(self, iid="ring", expires=None):
        d = self.data / "integrations" / iid
        d.mkdir(parents=True, exist_ok=True)
        (d / "token.json").write_text(json.dumps({"access": "acc0", "refresh": "ref0",
                                                   "expires": time.time() + 3600 if expires is None else expires}))

    def make(self, cfg=None, iid="ring"):
        ctx = Context(self.hub, self.data, self.data)
        ctx.instances = self.instances
        r = R.RingIntegration(iid, "Ring", cfg or {"email": "chris@example.com"}, ctx)
        r.save_config = self.saved.append
        self.instances[iid] = r
        return r

    async def until(self, cond, timeout=5.0, what="it"):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return
            await asyncio.sleep(0.02)
        self.fail(f"timed out waiting for {what}")

    async def started(self, cfg=None):
        self.signed_in()
        r = self.make(cfg)
        self.tasks.append(asyncio.create_task(r.run()))
        await self.until(lambda: self.hub.get("ring.z_siren") is not None, what="the device list")
        return r

    def twins(self):
        """ring-mqtt's copies, as the MQTT connection shows them."""
        self.instances["mqtt"] = FakeMqtt(Context(self.hub, self.data, self.data))
        for eid, kind, name in (("mqtt.z-side", "lock", "Side Door"), ("mqtt.ring_alarm_x", "alarm", "Woodbridge Alarm"),
                                ("mqtt.z-panel_siren", "siren", "Woodbridge Alarm Siren"),
                                ("mqtt.z-front", "contact", "Front Door")):
            self.hub.upsert(eid, kind=kind, name=name, integration="mqtt")


class SignInTests(RingBase):
    async def test_sign_in_with_the_texted_code_then_forget_the_password(self):
        r = self.make({"email": "chris@example.com", "password": "secret"})
        self.assertFalse(await r._sign_in())
        self.assertEqual(r.status, "pairing")
        self.assertIn("+1xxxxxx5309", r.detail)
        r = self.make({"email": "chris@example.com", "password": "secret", "code": "999999"})
        self.assertFalse(await r._sign_in())
        self.assertIn("didn't work", r.detail)
        r = self.make({"email": "chris@example.com", "password": "secret", "code": "123456", "bypass_open": True})
        self.assertTrue(await r._sign_in())
        self.assertEqual(self.saved[-1], {"email": "chris@example.com", "bypass_open": True})  # password, code gone
        tok = self.data / "integrations" / "ring" / "token.json"
        self.assertEqual(json.loads(tok.read_text())["refresh"], "ref1")
        self.assertEqual(oct(tok.stat().st_mode & 0o777), "0o600")
        # The same device id asked for the code and signed in (Ring ties them together).
        self.assertEqual(len(self.fake.hardware_ids), 1)
        self.assertEqual(self.fake.hardware_ids, {(self.data / "integrations" / "ring" / "hardware_id").read_text()})

    async def test_ring_activity_becomes_ring_without_signing_in_again(self):
        self.assertNotIn("ring_activity", REGISTRY)
        self.assertIn("ring", REGISTRY)
        store = Store(self.data)
        store.save_integration({"id": "ring_activity", "type": "ring_activity", "name": "Ring activity",
                                "config": {"email": "chris@example.com"}})
        store.save_integration({"id": "ring2", "type": "ring_activity", "name": "Cottage Ring", "enabled": False,
                                "config": {"email": "chris@example.com"}})
        self.signed_in("ring_activity")
        hw = self.data / "integrations" / "ring_activity" / "hardware_id"
        hw.write_text("hw-kept")
        mgr = IntegrationManager(store, Context(self.hub, self.data, self.data))
        mgr.start_all()
        try:
            recs = {r["id"]: r for r in store.integrations()}
            self.assertEqual((recs["ring_activity"]["type"], recs["ring_activity"]["name"]), ("ring", "Ring"))
            self.assertEqual((recs["ring2"]["type"], recs["ring2"]["name"]), ("ring", "Cottage Ring"))
            self.assertEqual(recs["ring_activity"]["config"], {"email": "chris@example.com"})
            self.assertIsInstance(mgr.instances["ring_activity"], R.RingIntegration)
            # Its token and device id carried over: it connects straight away, as the same device.
            await self.until(lambda: self.hub.get("ring_activity.z_side") is not None, what="devices")
            self.assertEqual(self.fake.oauth_calls, [])
            self.assertEqual(self.hub.get("ring_activity.z_side").integration, "ring_activity")
            self.assertEqual(mgr.list()[0]["status"], "connected")
            mgr.migrate_types()  # running it again changes nothing
            self.assertEqual(len(store.integrations()), 2)
        finally:
            await mgr.stop_all()


class LiveTests(RingBase):
    async def test_device_list_becomes_devices(self):
        r = await self.started()
        e = self.hub.get
        expect = {
            "ring.z_panel": ("alarm", "Woodbridge Alarm", "disarmed"),
            "ring.z_panel_siren": ("siren", "Woodbridge Alarm Siren", "off"),
            "ring.z_base": ("binary", "Woodbridge Base Station", "on"),
            "ring.z_side": ("lock", "Side Door", "LOCKED"),
            "ring.z_front": ("contact", "Front Door", "closed"),
            "ring.z_hall": ("motion", "Hallway Motion", "clear"),
            "ring.z_smoke": ("smoke", "Kitchen Smoke", "clear"),
            "ring.z_co": ("smoke", "Kitchen CO", "detected"),
            "ring.z_flood": ("leak", "Basement", "dry"),
            "ring.z_flood_freeze": ("binary", "Basement Freeze", "off"),
            "ring.z_keypad": ("motion", "Front Keypad", "clear"),
            "ring.z_siren": ("siren", "Upstairs Siren", "off"),
        }
        for eid, (kind, name, state) in expect.items():
            self.assertEqual((e(eid).kind, e(eid).name, e(eid).state, e(eid).integration), (kind, name, state, "ring"), eid)
        self.assertEqual(sorted(x.id for x in self.hub.all()), sorted(expect))  # no codes, no adapters
        lock = e("ring.z_side").attrs
        self.assertEqual((lock["batteryLevel"], lock["tamperStatus"], lock["commStatus"], lock["manufacturer"],
                          lock["serial"], lock["model"]), (80, "ok", "ok", "Yale", "Y123", "Lock"))
        self.assertEqual(e("ring.z_side").health_issues, [])
        self.assertEqual(e("ring.z_front").health_issues, ["Battery 15%", "Tamper reported"])
        self.assertEqual(e("ring.z_keypad").health_issues, ["No AC power"])
        self.assertEqual(e("ring.z_base").attrs["batteryLevel"], 100)  # Ring's "99" is full
        self.assertNotIn("batteryLevel", e("ring.z_flood_freeze").attrs)  # health once per device
        # Asked the alarm's base station (not the doorbell) for its devices, on a fresh ticket.
        lists = [o for o in self.fake.sent if o["msg"]["msg"] == "DeviceInfoDocGetList"]
        self.assertEqual([(o["channel"], o["msg"]["dst"]) for o in lists], [("message", "asset1")])
        self.assertEqual(r.status, "connected")
        self.assertIn("Woodbridge", r.detail)

    async def test_who_from_history_when_the_update_has_none(self):
        # Seen live: a lock unlocked with its own keypad code - Ring's live update says nothing about who.
        old = R.HISTORY_LOOKUPS_AT
        R.HISTORY_LOOKUPS_AT = (0.2, 0.5)
        try:
            await self.started()
            ent = self.hub.get("ring.z_side")
            self.assertTrue(ent.attrs.get("whoLookup"))  # automations wait a moment for the name
            now = time.time()
            other = {"msg": "DataUpdate", "context": {"initiatingEntityId": "u-marlina", "initiatingEntityType": "user",
                                                      "eventOccurredTsMs": int(now * 1000)},
                     "body": [upd("z-front", faulted=True)]}  # someone else's doing, another device
            # Ring's real entries (seen live): "unlocked", then "unlocked.by-keypad.access-code" naming the user.
            mine = {"msg": "DataUpdate", "context": {"initiatingEntityId": "u-chris", "initiatingEntityType": "user",
                                                     "eventOccurredTsMs": int(now * 1000)},
                    "body": [upd("z-side", impulse="unlocked.by-keypad.access-code")]}
            plain = {"msg": "DataUpdate", "context": {"eventOccurredTsMs": int(now * 1000)},
                     "body": [upd("z-side", impulse="unlocked", locked="unlocked")]}  # the app's plain "Unlocked"
            self.fake.history = [other, mine, plain]
            await self.fake.push(upd("z-side", impulse="unlocked", locked="unlocked"))
            await self.until(lambda: self.hub.get("ring.z_side").state == "UNLOCKED", what="unlocked")
            self.assertIsNone(self.hub.get("ring.z_side").attrs.get("changedBy"))
            await self.until(lambda: self.hub.get("ring.z_side").attrs.get("changedBy") == "Chris", what="who from history")
            samples = json.loads((self.data / "integrations" / "ring" / "who-samples.json").read_text())
            self.assertEqual(flatten_zid(samples[-1]["item"]), "z-side")

            # Locked by hand a few seconds later: the unlock's "by Chris" entry is still in the history,
            # but it's older than this change and about an unlock - no name.
            await asyncio.sleep(R.HISTORY_BEFORE_S + 0.2)
            hand = {"msg": "DataUpdate", "context": {"eventOccurredTsMs": int(time.time() * 1000)},
                    "body": [upd("z-side", impulse="locked.by-manual")]}
            self.fake.history = [hand, other, mine, plain]
            await self.fake.push(upd("z-side", impulse="locked", locked="locked"))
            await self.until(lambda: self.hub.get("ring.z_side").state == "LOCKED", what="locked by hand")
            await asyncio.sleep(0.8)  # both history lookups done
            self.assertIsNone(self.hub.get("ring.z_side").attrs.get("changedBy"))
        finally:
            R.HISTORY_LOOKUPS_AT = old

    async def test_keypad_code_unlock_as_ring_sends_it(self):
        # Seen live: two messages ~0.2 s apart. The first changes the state with no "who"; the second only
        # says "unlocked.by-keypad.access-code" with the user in the message's context. Each lock item also
        # carries its own "context" - Z-Wave adapter details, which must not hide the message's.
        await self.started()
        adapter = {"v1": {"adapter": {"v1": {"ackChannel": 2, "address": "fd96::24"}}}}
        first = upd("z-side", impulse="unlocked", locked="unlocked")
        first["context"] = adapter
        await self.fake.push(first, ctx={"eventOccurredTsMs": int(time.time() * 1000), "affectedEntityId": "z-side"})
        await self.until(lambda: self.hub.get("ring.z_side").state == "UNLOCKED", what="unlocked")
        second = upd("z-side", impulse="unlocked.by-keypad.access-code")
        second["context"] = adapter
        await self.fake.push(second, ctx={"initiatingEntityType": "user", "initiatingEntityId": "u-chris",
                                          "eventOccurredTsMs": int(time.time() * 1000)})
        await self.until(lambda: self.hub.get("ring.z_side").attrs.get("changedBy") == "Chris", what="named live")
        self.assertEqual(self.hub.get("ring.z_side").attrs["changedHow"], "with a keypad code")

        # Then locked by hand: no one to name, and Ring says so ("locked.by-manual").
        await self.fake.push(upd("z-side", impulse="locked", locked="locked"),
                             ctx={"eventOccurredTsMs": int(time.time() * 1000)})
        await self.until(lambda: self.hub.get("ring.z_side").state == "LOCKED", what="locked")
        self.assertIsNone(self.hub.get("ring.z_side").attrs.get("changedBy"))
        await self.fake.push(upd("z-side", impulse="locked.by-manual"), ctx={"eventOccurredTsMs": int(time.time() * 1000)})
        await self.until(lambda: self.hub.get("ring.z_side").attrs.get("changedHow") == "manually", what="manually")
        a = self.hub.get("ring.z_side").attrs
        self.assertEqual((a.get("changedBy"), a.get("changedByNoOne")), (None, True))
    def test_how_from_ring_impulses(self):
        self.assertEqual(R.how_from(["locked", "locked.by-manual"]), "manually")
        self.assertEqual(R.how_from(["locked.by-one-touch"]), "by one-touch locking")
        self.assertEqual(R.how_from(["unlocked.by-keypad.access-code"]), "with a keypad code")
        self.assertEqual(R.how_from(["unlocked.by-something-new"]), "by something new")
        self.assertIsNone(R.how_from(["unlocked", "security-panel.mode-switched.some"]))

    async def test_live_updates_and_who_did_it(self):
        self.twins()
        r = await self.started()
        changes = []

        async def watch(old, new):
            changes.append((new.id, new.state, new.attrs.get("changedBy")))
        self.hub.subscribe(watch)
        f = self.fake
        # ring-mqtt's copies of things Ring says "who" for wait a moment for the name.
        await self.until(lambda: self.hub.get("mqtt.z-side").attrs.get("whoLookup"), what="whoLookup")
        self.assertTrue(self.hub.get("mqtt.ring_alarm_x").attrs.get("whoLookup"))
        self.assertFalse(self.hub.get("mqtt.z-front").attrs.get("whoLookup"))

        # A door opens.
        await f.push(upd("z-front", faulted=True))
        await self.until(lambda: self.hub.get("ring.z_front").state == "open", what="door open")

        # Marlina unlocks the side door in the Ring app: the name arrives with the state, in one update.
        ctx = {"initiatingEntityId": "u-marlina", "initiatingEntityType": "user", "eventOccurredTsMs": int(time.time() * 1000)}
        await f.push(upd("z-side", impulse="lock.unlocked", locked="unlocked"), ctx=ctx)
        await self.until(lambda: ("ring.z_side", "UNLOCKED", "Marlina") in changes, what="unlocked by Marlina")
        self.assertNotIn(("ring.z_side", "UNLOCKED", None), changes)
        self.assertEqual(self.hub.get("mqtt.z-side").attrs["changedBy"], "Marlina")  # ring-mqtt's copy too
        self.assertAlmostEqual(self.hub.get("ring.z_side").attrs["changedAt"], ctx["eventOccurredTsMs"] / 1000, 2)
        await f.push(upd("z-side", impulse="lock.locked", locked="locked"), ctx=ctx)
        await self.until(lambda: ("ring.z_side", "LOCKED", "Marlina") in changes, what="locked by Marlina")
        self.assertEqual(f.lookups, ["u-marlina"])  # looked up once, then remembered

        # Locked by hand: no "who", so Marlina isn't credited - until Ring says who, a moment later.
        await f.push(upd("z-side", locked="unlocked"))
        await self.until(lambda: ("ring.z_side", "UNLOCKED", None) in changes, what="unlocked by hand")
        await f.push(upd("z-side", impulse="lock.unlocked"), ctx={"initiatingEntityId": "u-chris",
                                                                  "initiatingEntityType": "user"})
        await self.until(lambda: self.hub.get("ring.z_side").attrs.get("changedBy") == "Chris", what="late who")

        # Lisa arms the alarm with her keypad code: arming (exit delay), then armed - both "by Lisa".
        ends = int((time.time() + 0.4) * 1000)
        await f.push(upd("z-panel", impulse="security-panel.mode-switched.some", mode="some",
                         transitionDelayEndTimestamp=ends),
                     ctx={"initiatingEntityId": "z-code-lisa", "initiatingEntityType": "access-code"})
        await self.until(lambda: ("ring.z_panel", "arming", "Lisa") in changes, what="arming by Lisa")
        await self.until(lambda: ("ring.z_panel", "armed_home", "Lisa") in changes, what="armed (home)")
        self.assertEqual(self.hub.get("mqtt.ring_alarm_x").attrs["changedBy"], "Lisa")  # matched by name

        # Someone comes home: entry delay, then the alarm goes off; the siren sounds.
        await f.push(upd("z-panel", alarmInfo={"state": "entry-delay"}))
        await self.until(lambda: self.hub.get("ring.z_panel").state == "pending", what="entry delay")
        await f.push(upd("z-panel", alarmInfo={"state": "burglar-alarm"}, siren={"state": "on"}))
        await self.until(lambda: self.hub.get("ring.z_panel").state == "triggered", what="triggered")
        self.assertEqual(self.hub.get("ring.z_panel_siren").state, "on")

        # Disarmed by someone Ring names itself (no lookup needed); the siren stops.
        await f.push(upd("z-panel", impulse="security-panel.mode-switched.none", mode="none", alarmInfo=None,
                         siren={"state": "off"}),
                     ctx={"initiatingEntityId": "u-other", "initiatingEntityType": "user", "initiatingEntityName": "Grandma"})
        await self.until(lambda: ("ring.z_panel", "disarmed", "Grandma") in changes, what="disarmed by Grandma")
        await self.until(lambda: ("ring.z_panel_siren", "off", "Grandma") in changes, what="siren off")
        self.assertEqual(self.hub.get("mqtt.z-panel_siren").attrs["changedBy"], "Grandma")
        self.assertNotIn("u-other", f.lookups)

        # Sensors: a leak, the keypad noticing someone (which clears by itself), a dead sensor.
        await f.push(upd("z-flood", flood={"faulted": True}))
        await self.until(lambda: self.hub.get("ring.z_flood").state == "wet", what="leak")
        await f.push(upd("z-keypad", impulse="keypad.motion"))
        await self.until(lambda: self.hub.get("ring.z_keypad").state == "detected", what="keypad motion")
        await self.until(lambda: self.hub.get("ring.z_keypad").state == "clear", what="keypad clear")
        await f.push(upd("z-hall", general={"commStatus": "error"}))
        await self.until(lambda: "Not responding to the hub" in self.hub.get("ring.z_hall").health_issues, what="comm")

    async def test_commands(self):
        r = await self.started()
        f = self.fake
        await r.command("ring.z_side", "unlock")
        await self.until(lambda: f.commands(), what="a command")
        sent = [o for o in f.sent if o["msg"]["msg"] == "DeviceInfoSet"][-1]
        self.assertEqual(sent["channel"], "message")
        self.assertEqual({k: v for k, v in sent["msg"].items() if k != "seq"}, {
            "msg": "DeviceInfoSet", "datatype": "DeviceInfoSetType", "dst": "asset1",
            "body": [{"zid": "z-side", "command": {"v1": [{"commandType": "lock.unlock", "data": {}}]}}]})
        self.assertIsInstance(sent["msg"]["seq"], int)

        await r.command("ring.z_panel", "arm_away")
        self.assertEqual(f.commands()[-1]["body"][0], {"zid": "z-panel", "command": {"v1": [
            {"commandType": "security-panel.switch-mode", "data": {"mode": "all", "bypass": []}}]}})
        await self.until(lambda: self.hub.get("ring.z_panel").state == "armed_away", what="armed away")
        await r.command("ring.z_panel", "disarm")
        await self.until(lambda: self.hub.get("ring.z_panel").state == "disarmed", what="disarmed")

        # Ring won't arm with a door open: said plainly.
        f.refuse_arm = True
        await f.push(upd("z-front", faulted=True))
        await self.until(lambda: self.hub.get("ring.z_front").state == "open", what="door open")
        with self.assertRaises(CommandError) as err:
            await r.command("ring.z_panel", "arm_home")
        self.assertIn("door or window", str(err.exception))
        # …unless open doors and windows are to be bypassed.
        f.refuse_arm = False
        r.config["bypass_open"] = True
        await r.command("ring.z_panel", "arm_home")
        self.assertEqual(f.commands()[-1]["body"][0]["command"]["v1"][0]["data"], {"mode": "some", "bypass": ["z-front"]})

        await r.command("ring.z_panel_siren", "turn_on")
        await r.command("ring.z_panel_siren", "turn_off")
        await r.command("ring.z_siren", "turn_on")
        await self.until(lambda: len(f.commands()) >= 8, what="siren commands")
        cmds = f.commands()
        self.assertEqual([c["body"][0]["command"]["v1"][0]["commandType"] for c in cmds[-3:-1]],
                         ["security-panel.sound-siren", "security-panel.silence-siren"])
        self.assertEqual(cmds[-3]["body"][0]["zid"], "z-panel")
        self.assertEqual(cmds[-1]["body"][0], {"zid": "z-siren", "device": {"v1": {"on": True}}})
        seqs = [c["seq"] for c in cmds]
        self.assertEqual(seqs, sorted(set(seqs)))

        with self.assertRaises(CommandError):
            await r.command("ring.z_hall", "lock")
        with self.assertRaises(CommandError):
            await r.command("ring.nothing", "lock")
        r._locations["loc1"].ws = None
        with self.assertRaises(CommandError) as err:
            await r.command("ring.z_side", "lock")
        self.assertIn("isn't connected", str(err.exception))

    async def test_a_stalled_send_fails_the_command_and_reconnects(self):
        # Seen live: "Lock all" waited on Ring's socket with no end, so the phone gave up first.
        r = await self.started()
        f = self.fake
        loc = r._locations["loc1"]
        stuck = loc.ws

        async def never(*a, **k):
            await asyncio.Event().wait()
        stuck.send_str = never  # the socket takes the write and never finishes it
        before = len(f.commands())
        with mock.patch.object(R, "SEND_TIMEOUT_S", 0.2):
            t0 = time.time()
            with self.assertRaises(CommandError) as err:
                await r.command("ring.z_side", "lock")
            self.assertLess(time.time() - t0, 2.0)
        self.assertIn("Ring didn't take the command", str(err.exception))
        self.assertIsNot(loc.ws, stuck)  # never written to again (a half-written frame would corrupt it)
        # Dropped: _follow reconnects with a fresh ticket, and commands go through again.
        await self.until(lambda: len(f.tickets) == 2 and loc.ws is not None and loc.ws is not stuck, what="reconnect")
        self.assertTrue(stuck.closed)
        await self.until(lambda: self.hub.get("ring.z_side").available, what="devices back")
        await r.command("ring.z_side", "lock")
        await self.until(lambda: len(f.commands()) == before + 1, what="the lock command")
        self.assertEqual(f.commands()[-1]["body"][0]["zid"], "z-side")

    async def test_reconnects_with_a_fresh_ticket(self):
        await self.started()
        f = self.fake
        await f.sockets[0].close()  # Ring drops the connection
        await self.until(lambda: len(f.sockets) == 2 and len(f.tickets) == 2, what="reconnect")
        await self.until(lambda: sum(o["msg"]["msg"] == "DeviceInfoDocGetList" for o in f.sent) == 2, what="new list")
        await f.push(upd("z-front", faulted=True))
        await self.until(lambda: self.hub.get("ring.z_front").state == "open", what="updates after reconnecting")
        # Ring asking for a reconnect (its hub moved) is honoured the same way.
        await f.sockets[-1].send_json({"channel": "DataUpdate", "msg": {
            "msg": "DataUpdate", "datatype": "HubDisconnectionEventType", "body": []}})
        await self.until(lambda: len(f.tickets) == 3, what="reconnect on request")
        self.assertEqual(self.hub.get("ring.z_front").state, "open")  # kept across reconnects

    async def test_token_renewal_saves_the_new_refresh_token(self):
        self.signed_in(expires=0)  # access token already expired
        r = self.make()
        self.tasks.append(asyncio.create_task(r.run()))
        await self.until(lambda: self.hub.get("ring.z_side") is not None, what="devices")
        tok = self.data / "integrations" / "ring" / "token.json"
        self.assertEqual(json.loads(tok.read_text())["refresh"], "ref1")
        self.assertEqual(oct(tok.stat().st_mode & 0o777), "0o600")
        # Ring stops accepting the access token: the next ticket is refused, renewed once, retried.
        f = self.fake
        f.access = set()
        await f.sockets[0].close()
        await self.until(lambda: len(f.tickets) == 2, what="a ticket after renewing")
        self.assertEqual(json.loads(tok.read_text())["refresh"], "ref2")
        self.assertEqual(f.oauth_calls, ["refresh_token", "refresh_token"])

    async def test_signed_out_by_ring(self):
        self.signed_in(expires=0)
        self.fake.refresh = "someone-elses"
        r = self.make()
        self.tasks.append(asyncio.create_task(r.run()))
        await self.until(lambda: r.status == "error", what="signed out")
        self.assertIn("password again", r.detail)
        self.assertFalse((self.data / "integrations" / "ring" / "token.json").exists())


class Upkeepish(Integration):
    """A connection with something of its own that points at a device."""
    type = "upkeepish"
    name = "Keeps a device"

    def __init__(self, ctx):
        super().__init__("upkeepish", "Keeps", {}, ctx)
        self.target = "mqtt.z-front"

    async def replace_entities(self, mapping):
        if self.target in mapping:
            self.target = mapping[self.target]
            return 1
        return 0


class SwapTests(unittest.IsolatedAsyncioTestCase):
    """GET /api/integrations/{id}/replacements and POST /api/entities/replace."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        hub, inst = self.h.hub, self.h.integrations.instances
        # ring-mqtt's devices through MQTT, plus something unrelated.
        inst["mqtt"] = FakeMqtt(self.h.integrations._ctx)
        for eid, kind, name, state in (("mqtt.z-side", "lock", "Side Door", "LOCKED"),
                                       ("mqtt.ring_alarm_x", "alarm", "Woodbridge Alarm", "disarmed"),
                                       ("mqtt.z-panel_siren", "siren", "Woodbridge Alarm Siren", "off"),
                                       ("mqtt.z-front", "contact", "Front Door", "closed"),
                                       ("mqtt.garage", "garage", "Garage", "closed")):
            hub.upsert(eid, kind=kind, name=name, integration="mqtt")
            await hub.update(eid, state=state)
        # Ring's own devices, as its live connection would make them.
        ctx = self.h.integrations._ctx
        self.ring = R.RingIntegration("ring", "Ring", {"email": "chris@example.com"}, ctx)
        inst["ring"] = self.ring
        loc = R.Location("loc1", "Woodbridge")
        self.ring._locations["loc1"] = loc
        await self.ring._on_message(loc, {"channel": "message", "msg": {
            "msg": "DeviceInfoDocGetList", "datatype": "DeviceInfoDocType", "src": "asset1", "body": devices()}})
        self.keeps = Upkeepish(ctx)
        inst["upkeepish"] = self.keeps
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        r = await self.client.post("/api/auth/setup", json={"username": "Chris", "name": "Chris", "password": "correct horse"})
        self.assertEqual(r.status, 200)

    async def asyncTearDown(self):
        await self.client.close()
        await self.h.integrations.stop_all()
        self.tmp.cleanup()

    async def test_suggested_pairs(self):
        r = await self.client.get("/api/integrations/ring/replacements")
        pairs = await r.json()
        self.assertEqual(sorted((p["from"], p["to"], p["name"], p["kind"]) for p in pairs), [
            ("mqtt.ring_alarm_x", "ring.z_panel", "Woodbridge Alarm", "alarm"),   # by name
            ("mqtt.z-front", "ring.z_front", "Front Door", "contact"),            # by ring-mqtt's id
            ("mqtt.z-panel_siren", "ring.z_panel_siren", "Woodbridge Alarm Siren", "siren"),
            ("mqtt.z-side", "ring.z_side", "Side Door", "lock"),
        ])
        self.assertEqual(await (await self.client.get("/api/integrations/upkeepish/replacements")).json(), [])
        self.assertEqual((await self.client.get("/api/integrations/nope/replacements")).status, 404)

    async def test_swapping_rewrites_every_use(self):
        h = self.h
        chris = h.store.people()[0]["id"]
        a1 = h.engine.save({
            "name": "Side door left unlocked at night",
            "trigger": {"match": "any", "items": [
                {"type": "state", "entities": ["mqtt.z-side", "mqtt.garage"], "to": "UNLOCKED",
                 "for": {"amount": 5, "unit": "minutes"}},
                {"type": "time", "time": "22:00", "days": [0, 1, 2, 3, 4, 5, 6]}]},
            "condition": {"devices": [{"entities": ["mqtt.ring_alarm_x"], "state": "disarmed", "match": "any"},
                                      {"entities": ["mqtt.z-side", "mqtt.z-front"], "state": "active", "match": "any"}],
                          "between": {"after": "21:00", "before": "06:00"}},
            "actions": [{"entities": ["mqtt.z-panel_siren"], "command": "turn_on", "for": {"amount": 1, "unit": "minutes"}},
                        {"entity": "mqtt.z-side", "command": "lock"}],
            "notify": {"enabled": True, "title": "Side door", "buttons": ["lock:mqtt.z-side", "lock_all", "snooze:60",
                                                                           "dismiss"]},
            "autoCommand": {"enabled": True, "command": "lock"},
            "recipients": [chris]})
        a2 = h.engine.save({"name": "Garage at 9", "trigger": {"type": "time", "time": "21:00"},
                            "condition": {"devices": [{"entities": ["mqtt.garage"], "state": "active", "match": "any"}]},
                            "recipients": [chris], "notify": {"enabled": True}})
        a3 = h.engine.save({"name": "Alarm armed", "trigger": {"items": [
            {"type": "state", "entities": ["mqtt.ring_alarm_x"], "to": "armed_away"}]},
            "recipients": [chris], "notify": {"enabled": True, "buttons": ["disarm:mqtt.ring_alarm_x"]}})
        user = h.store.users()[0]["id"]
        dash = h.store.save_dashboard(user, {"name": "Wall", "theme": "night-glass", "pages": [
            {"id": "p1", "name": "Home", "icon": "home", "tiles": [
                {"id": "t1", "type": "devices", "x": 0, "y": 0, "w": 4, "h": 3,
                 "config": {"title": "Locks", "entities": ["mqtt.z-side", "mqtt.garage"], "style": "list"}},
                {"id": "t2", "type": "mode", "x": 4, "y": 0, "w": 8, "h": 1, "config": {"entity": "mqtt.ring_alarm_x"}},
                {"id": "t3", "type": "values", "x": 4, "y": 1, "w": 4, "h": 3,
                 "config": {"items": [{"entity": "mqtt.z-front", "property": "battery"}]}}]}],
            "names": {"mqtt.z-side": "Side", "mqtt.garage": "Big door"}})
        other = h.store.save_dashboard(user, {"name": "Garage", "theme": "night-glass", "pages": [
            {"id": "p1", "name": "Home", "icon": "home", "tiles": [
                {"id": "t1", "type": "devices", "x": 0, "y": 0, "w": 4, "h": 3, "config": {"entities": ["mqtt.garage"]}}]}]})
        h.store.rename_entity("mqtt.z-side", "Side door lock")
        h.store.rename_entity("mqtt.garage", "Big door")
        h.hub.set_name_overrides(h.store.entity_names())

        a2_before = copy.deepcopy(a2)
        pairs = await (await self.client.get("/api/integrations/ring/replacements")).json()
        r = await self.client.post("/api/entities/replace", json={"pairs": [{"from": p["from"], "to": p["to"]} for p in pairs]})
        self.assertEqual(r.status, 200, await r.text())
        counts = await r.json()
        self.assertEqual({k: counts[k] for k in ("automations", "dashboards", "names", "upkeepish")},
                         {"automations": 2, "dashboards": 1, "names": 1, "upkeepish": 1})

        def check(a):
            t, c = a["trigger"], a["condition"]
            self.assertEqual(t["items"][0], {"type": "state", "entities": ["ring.z_side", "mqtt.garage"], "to": "UNLOCKED",
                                             "for": {"amount": 5, "unit": "minutes"}})
            self.assertEqual(t["entities"], ["ring.z_side", "mqtt.garage"])
            self.assertEqual([row["entities"] for row in c["devices"]], [["ring.z_panel"], ["ring.z_side", "ring.z_front"]])
            self.assertEqual(c["entities"], ["ring.z_panel", "ring.z_side", "ring.z_front"])
            self.assertEqual(a["actions"], [{"entities": ["ring.z_panel_siren"], "command": "turn_on",
                                             "for": {"amount": 1, "unit": "minutes"}},
                                            {"entity": "ring.z_side", "command": "lock"}])
            self.assertEqual(a["notify"]["buttons"], ["lock:ring.z_side", "lock_all", "snooze:60", "dismiss"])
            self.assertEqual((a["name"], a["recipients"], a["autoCommand"]),
                             ("Side door left unlocked at night", [chris], {"enabled": True, "command": "lock"}))
        check(h.engine.autos[a1["id"]])
        check(next(a for a in h.store.automations() if a["id"] == a1["id"]))  # saved, not just in memory
        self.assertEqual(h.engine.autos[a2["id"]], a2_before)  # untouched
        self.assertEqual(h.engine.autos[a3["id"]]["trigger"]["items"][0]["entities"], ["ring.z_panel"])
        self.assertEqual(h.engine.autos[a3["id"]]["notify"]["buttons"], ["disarm:ring.z_panel"])
        self.assertEqual(sorted(h.engine.used_by("ring.z_side")), [a1["id"]])
        self.assertEqual(h.engine.used_by("mqtt.z-side"), [])
        self.assertEqual(sorted(h.engine.used_by("ring.z_panel")), sorted([a1["id"], a3["id"]]))

        d = h.store.dashboard(dash["id"])
        tiles = d["pages"][0]["tiles"]
        self.assertEqual(tiles[0]["config"], {"title": "Locks", "entities": ["ring.z_side", "mqtt.garage"], "style": "list"})
        self.assertEqual(tiles[1]["config"], {"entity": "ring.z_panel"})
        self.assertEqual(tiles[2]["config"], {"items": [{"entity": "ring.z_front", "property": "battery"}]})
        self.assertEqual(d["names"], {"ring.z_side": "Side", "mqtt.garage": "Big door"})
        self.assertEqual((d["name"], d["theme"], d["isDefault"]), ("Wall", "midnight", True))  # saved as the old name: the same look
        self.assertEqual(h.store.dashboard(other["id"])["pages"], other["pages"])

        self.assertEqual(h.store.entity_names()["ring.z_side"], "Side door lock")
        self.assertEqual(h.hub.get("ring.z_side").name, "Side door lock")
        self.assertEqual(self.keeps.target, "ring.z_front")
        # The automations still describe and run: the new devices are real.
        r = await self.client.get("/api/automations")
        self.assertEqual(r.status, 200)

    async def test_upkeep_follows_the_device(self):
        h = self.h
        rec = await h.integrations.create("upkeep", "Upkeep", {})
        up = h.integrations.instances[rec["id"]]
        item = await up.add({"name": "Test the siren", "every": 0, "runtimeHours": 10, "runtimeEntity": "mqtt.z-panel_siren"})
        r = await self.client.post("/api/entities/replace", json={"pairs": [{"from": "mqtt.z-panel_siren",
                                                                             "to": "ring.z_panel_siren"}]})
        self.assertEqual((await r.json())["upkeep"], 1)
        self.assertEqual(up._item(item["id"])["runtimeEntity"], "ring.z_panel_siren")
        self.assertIn("ring.z_panel_siren", (Path(self.tmp.name) / "integrations" / rec["id"] / "items.json").read_text())

    async def test_bad_requests(self):
        for body in ({}, {"pairs": []}, {"pairs": [{"from": "mqtt.z-side"}]},
                     {"pairs": [{"from": "mqtt.z-side", "to": "ring.missing"}]},
                     {"pairs": [{"from": "ring.z_side", "to": "ring.z_side"}]},
                     {"pairs": [{"from": "mqtt.z-side", "to": "ring.z_side"}, {"from": "mqtt.z-side", "to": "ring.z_front"}]}):
            r = await self.client.post("/api/entities/replace", json=body)
            self.assertEqual(r.status, 400, body)
            self.assertTrue((await r.json())["error"])


if __name__ == "__main__":
    unittest.main()
