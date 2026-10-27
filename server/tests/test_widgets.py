"""Phone widgets, hub side: the device picker list (GET /api/widgets/devices), device states with what a
tap does and readings (GET /api/widgets/state), the device command endpoint running a widget's action,
widget push tokens (POST /api/widgets/push-token: register, replace, forget, dropped on sign-out and
account removal), and the reload pushes (APNs widget push, at most one per window per token, only for
widget-visible changes of devices the account's widgets show; Android skipped without FCM)."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

import aiohttp  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman import widgets as W  # noqa: E402
from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.kinds import KINDS  # noqa: E402
from newman.push.apns import PushError  # noqa: E402
from test_auth import FakeLocks  # noqa: E402
from test_locations import FakeApns, H  # noqa: E402

WTOK = "ab" * 32
WTOK_2 = "cd" * 32


class DeadApns(FakeApns):
    async def send(self, token, payload, **kw):
        self.sent.append((token, payload, kw))
        raise PushError("APNs 410 Unregistered", dead_token=True)


class WidgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.fake = FakeLocks(self.h.integrations._ctx)
        self.h.integrations.instances["fake"] = self.fake
        hub = self.h.hub
        for eid, kind, name, state, attrs in (
                ("fake.side", "lock", "Side Door", "LOCKED", {"area": "Mudroom"}),
                ("fake.porch", "light", "Porch", "off", {"area": "Outside", "brightness": 40}),
                ("fake.temp", "temperature", "Basement", "21.5", {"area": "Basement", "humidity": 48}),
                ("fake.stat", "thermostat", "Main Floor", "heat", {"currentTemp": 20.5, "targetTemp": 21}),
                ("fake.cam", "camera", "Driveway", "online", {}),
                ("fake.pico", "remote", "Kitchen Pico", "idle", {}),
                ("fake.alarm", "alarm", "Alarm", "disarmed", {}),
                ("fake.bell", "button", "Chime", None, {}),
                ("fake.door", "contact", "Back Door", "closed", {"area": "Mudroom"})):
            hub.upsert(eid, kind=kind, name=name, integration="fake")
            await hub.update(eid, state=state, attrs=attrs)
        self.app = build_app(self.h)
        self.push = self.h.widget_push
        self.push.every_s = 0.3
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        r = await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        self.assertEqual(r.status, 200)
        await self.client.post("/api/users", json={"username": "dante", "name": "Dante", "role": "family",
                                                   "password": "another pass"})
        self.chris = await self.token("chris", "correct horse")
        self.dante = await self.token("dante")
        self.apns = FakeApns()
        self.h.notifier._apns = self.apns
        await asyncio.sleep(0.05)  # the setup updates' listeners

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def token(self, username, password="another pass"):
        s = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        r = await s.post(self.client.make_url("/api/auth/login"),
                         json={"username": username, "password": password, "kind": "app"})
        body = await r.json()
        await s.close()
        self.assertEqual(r.status, 200, body)
        return {"Authorization": "Bearer " + body["token"]}

    async def call(self, method, path, who, **kw):
        r = await self.client.request(method, path, headers=who, **kw)
        try:
            body = await r.json()
        except Exception:
            body = None
        return r.status, body

    async def state(self, ids, who=None):
        st, body = await self.call("GET", "/api/widgets/state?ids=" + ",".join(ids), who or self.dante)
        self.assertEqual(st, 200, body)
        return {d["id"]: d for d in body["devices"]}, [d["id"] for d in body["devices"]]

    async def settle(self, s=0.05):
        await asyncio.sleep(s)

    # -- the picker list ---------------------------------------------------------------------------------

    async def test_devices_lists_what_a_widget_can_show(self):
        st, body = await self.call("GET", "/api/widgets/devices", self.dante)
        self.assertEqual(st, 200)
        by_id = {d["id"]: d for d in body["devices"]}
        # A camera (a picture, not a state) and a remote (one state, nothing to tap) aren't offered.
        self.assertEqual(set(by_id), {"fake.side", "fake.porch", "fake.temp", "fake.stat", "fake.alarm", "fake.bell",
                                      "fake.door"})
        self.assertEqual(by_id["fake.porch"], {"id": "fake.porch", "name": "Porch", "kind": "light", "kindName": "Lights",
                                               "area": "Outside", "icon": "light", "controllable": True})
        self.assertFalse(by_id["fake.temp"]["controllable"])
        self.assertFalse(by_id["fake.door"]["controllable"])
        self.assertIsNone(by_id["fake.stat"]["area"])
        # Sorted by area (devices without one last), then name.
        self.assertEqual([d["id"] for d in body["devices"]][:4], ["fake.temp", "fake.door", "fake.side", "fake.porch"])
        async with aiohttp.ClientSession() as anon:  # no sign-in (the test client has the setup cookie)
            self.assertEqual((await anon.get(self.client.make_url("/api/widgets/devices"))).status, 401)

    async def test_showable_comes_from_kind_metadata_alone(self):
        from newman.state import Entity
        for k in KINDS.values():
            e = Entity(id="x", kind=k.id, name="X", integration="fake")
            expect = bool(W.plain_commands(k)) or W.kind_reads(k) or (len(k.states) > 1 and not k.has_camera)
            self.assertEqual(W.showable(e), expect, k.id)
            W.state_json(e)  # every kind renders, even with no state yet

    # -- states ------------------------------------------------------------------------------------------

    async def test_state_in_order_with_actions_and_readings(self):
        by_id, order = await self.state(["fake.porch", "nope", "fake.side", "fake.temp", "fake.cam", "fake.porch"])
        self.assertEqual(order, ["fake.porch", "fake.side", "fake.temp"])  # unknown / not showable left out
        porch = by_id["fake.porch"]
        self.assertEqual({k: porch[k] for k in ("name", "kind", "state", "stateLabel", "available", "icon", "area",
                                                 "reading")},
                         {"name": "Porch", "kind": "light", "state": "off", "stateLabel": "Off", "available": True,
                          "icon": "light", "area": "Outside", "reading": None})
        self.assertEqual(porch["actions"], [
            {"command": "turn_on", "label": "Turn on", "sensitive": False, "primary": True},
            {"command": "turn_off", "label": "Turn off", "sensitive": False, "primary": False}])
        # A lock: the next action is the one that isn't its state - here the sensitive Unlock.
        self.assertEqual(by_id["fake.side"]["actions"][0],
                         {"command": "unlock", "label": "Unlock", "sensitive": True, "primary": True})
        # A temperature sensor's state is its reading; exact, with the kind's unit.
        self.assertEqual(by_id["fake.temp"]["reading"], {"value": 21.5, "unit": "°C"})
        self.assertEqual(by_id["fake.temp"]["actions"], [])
        by_id, _ = await self.state(["fake.stat", "fake.alarm", "fake.bell"])
        self.assertEqual(by_id["fake.stat"]["reading"], {"value": 20.5, "unit": "°C"})  # first summary number
        self.assertEqual(by_id["fake.stat"]["actions"], [])  # its commands all need a value
        self.assertEqual([a["command"] for a in by_id["fake.alarm"]["actions"]], ["arm_home", "arm_away", "disarm"])
        self.assertEqual(by_id["fake.bell"]["actions"], [{"command": "press", "label": "Press", "sensitive": False,
                                                          "primary": True}])
        # Turned on: a tap turns it off now.
        await self.h.hub.update("fake.porch", state="on")
        by_id, _ = await self.state(["fake.porch"])
        self.assertEqual(by_id["fake.porch"]["actions"][0]["command"], "turn_off")
        self.assertTrue(by_id["fake.porch"]["active"])  # the kind's active state (shown highlighted)
        self.assertFalse(porch["active"])
        # Without ids: every showable device.
        st, body = await self.call("GET", "/api/widgets/state", self.dante)
        self.assertEqual(len(body["devices"]), 7)

    async def test_the_command_endpoint_runs_a_widget_action(self):
        by_id, _ = await self.state(["fake.porch", "fake.side"])
        for eid in ("fake.porch", "fake.side"):
            cmd = by_id[eid]["actions"][0]["command"]
            st, body = await self.call("POST", f"/api/entities/{eid}/command", self.dante, json={"command": cmd})
            self.assertEqual((st, body), (200, {"ok": True}))
        self.assertEqual(self.fake.calls, [("fake.porch", "turn_on"), ("fake.side", "unlock")])

    def test_tap_action_matches_the_apps(self):
        from newman.state import Entity
        e = lambda kind, state: Entity(id="x", kind=kind, name="X", integration="f", state=state)  # noqa: E731
        tap = lambda kind, state: (W.tap_action(e(kind, state), KINDS[kind]) or type("N", (), {"id": None})).id  # noqa: E731
        self.assertEqual(tap("lock", "UNLOCKED"), "lock")
        self.assertEqual(tap("garage", "closed"), "open")
        self.assertEqual(tap("garage", "open"), "close")
        self.assertEqual(tap("switch", "on"), "turn_off")
        self.assertEqual(tap("vacuum", "docked"), "start")
        self.assertEqual(tap("vacuum", "cleaning"), "pause")
        self.assertEqual(tap("alarm", "armed_away"), "arm_home")
        self.assertEqual(tap("webhook", "idle"), None)

    # -- push tokens -------------------------------------------------------------------------------------

    async def register(self, who, device_id="iph-d", token=WTOK, kind="ios-widget", **extra):
        return await self.call("POST", "/api/widgets/push-token", who,
                               json={"token": token, "kind": kind, "deviceId": device_id, **extra})

    async def test_register_replace_and_forget_tokens(self):
        self.assertEqual(await self.register(self.dante), (200, {"ok": True, "kept": True}))
        self.assertEqual(self.push.tokens.get("iph-d", "ios-widget").token, WTOK)
        await self.register(self.dante, token=WTOK_2.upper(), apnsEnvironment="sandbox")
        t = self.push.tokens.get("iph-d", "ios-widget")
        self.assertEqual((t.token, t.apns_env), (WTOK_2, "sandbox"))
        self.assertEqual(len(self.push.tokens.all()), 1)
        # The same token from another install moves to it.
        await self.register(self.chris, device_id="iph-c", token=WTOK_2)
        self.assertEqual([r[0] for r in self.push.tokens.all()], ["iph-c"])
        # Android keeps any FCM token string.
        self.assertEqual((await self.register(self.dante, device_id="and-d", token="fcm:abc-123", kind="android"))[0], 200)
        # Bad input.
        self.assertEqual((await self.register(self.dante, kind="pager"))[0], 400)
        self.assertEqual((await self.register(self.dante, token="not hex"))[0], 400)
        self.assertEqual((await self.register(self.dante, device_id=""))[0], 400)
        # A null token forgets it.
        self.assertEqual(await self.register(self.dante, device_id="and-d", token=None, kind="android"),
                         (200, {"ok": True, "kept": False}))
        self.assertEqual([r[0] for r in self.push.tokens.all()], ["iph-c"])

    async def test_signing_out_or_removing_the_account_drops_tokens(self):
        await self.register(self.dante)
        await self.register(self.chris, device_id="iph-c", token=WTOK_2)
        st, _ = await self.call("POST", "/api/auth/logout", self.dante)
        self.assertEqual(st, 200)
        self.assertEqual([r[0] for r in self.push.tokens.all()], ["iph-c"])
        self.dante = await self.token("dante")
        await self.register(self.dante)
        uid = next(u["id"] for u in self.h.store.users() if u["username"] == "dante")
        self.assertEqual((await self.call("DELETE", f"/api/users/{uid}", self.chris))[0], 200)
        self.assertEqual([r[0] for r in self.push.tokens.all()], ["iph-c"])

    # -- reload pushes -----------------------------------------------------------------------------------

    async def test_a_change_sends_one_widget_push_then_waits_for_the_window(self):
        await self.register(self.dante, apnsEnvironment="sandbox")
        await self.h.hub.update("fake.porch", state="on")
        await self.settle()
        self.assertEqual(len(self.apns.sent), 1)
        tok, payload, kw = self.apns.sent[0]
        self.assertEqual((tok, payload), (WTOK, {"aps": {"content-changed": True}}))
        self.assertEqual((kw["push_type"], kw["topic"], kw["env"]),
                         ("widgets", f"{config.apns_bundle_id}.push-type.widgets", "sandbox"))
        # More changes inside the window: nothing now, ONE push when it ends.
        await self.h.hub.update("fake.porch", state="off")
        await self.h.hub.update("fake.side", state="UNLOCKED")
        await self.settle()
        self.assertEqual(len(self.apns.sent), 1)
        await self.settle(0.4)
        self.assertEqual(len(self.apns.sent), 2)
        await self.settle(0.4)
        self.assertEqual(len(self.apns.sent), 2)

    async def test_only_widget_visible_changes_of_shown_devices_push(self):
        await self.register(self.dante)
        # Something a widget doesn't show (a light's brightness) or a device it can't show (a camera): no push.
        await self.h.hub.update("fake.porch", attrs={"brightness": 80})
        await self.h.hub.update("fake.cam", state="offline")
        await self.settle()
        self.assertEqual(self.apns.sent, [])
        # A reading is shown.
        await self.h.hub.update("fake.temp", state="22")
        await self.settle()
        self.assertEqual(len(self.apns.sent), 1)
        # Once Dante's widgets have asked for the lock only, the light changing doesn't reach his phone.
        await self.state(["fake.side"])
        await self.settle(0.4)
        await self.h.hub.update("fake.porch", state="on")
        await self.settle()
        self.assertEqual(len(self.apns.sent), 1)
        await self.h.hub.update("fake.side", state="UNLOCKED")
        await self.settle()
        self.assertEqual(len(self.apns.sent), 2)

    async def test_signed_out_android_without_fcm_and_dead_tokens(self):
        await self.register(self.dante)
        await self.register(self.dante, device_id="and-d", token="fcm-token", kind="android")
        self.assertFalse(self.h.notifier._fcm.enabled)
        await self.h.hub.update("fake.porch", state="on")
        await self.settle()
        self.assertEqual([t for t, _, _ in self.apns.sent], [WTOK])  # Android: no FCM on this hub, skipped
        # A dead token is dropped.
        self.h.notifier._apns = dead = DeadApns()
        await self.settle(0.4)
        await self.h.hub.update("fake.porch", state="off")
        await self.settle()
        self.assertEqual(len(dead.sent), 1)
        self.assertIsNone(self.push.tokens.get("iph-d", "ios-widget"))
        # A token whose sign-in was revoked gets nothing.
        await self.register(self.chris, device_id="iph-c", token=WTOK_2)
        self.h.notifier._apns = self.apns
        sid = self.h.store.sessions(next(u["id"] for u in self.h.store.users() if u["username"] == "chris"))
        for s in sid:
            self.h.store.remove_session(s["id"])
        await self.h.hub.update("fake.porch", state="on")
        await self.settle()
        self.assertNotIn(WTOK_2, [t for t, _, _ in self.apns.sent])

    # -- tones and the "Home status" widget ------------------------------------------------------------

    async def home(self, who=None):
        st, body = await self.call("GET", "/api/widgets/home", who or self.dante)
        self.assertEqual(st, 200, body)
        return body

    async def test_state_tone_comes_from_the_kind(self):
        by_id, _ = await self.state(["fake.side", "fake.porch", "fake.temp", "fake.door"])
        self.assertEqual({i: d["tone"] for i, d in by_id.items()},
                         {"fake.side": "good", "fake.porch": "", "fake.temp": "", "fake.door": "good"})
        await self.h.hub.update("fake.side", state="UNLOCKED")
        await self.h.hub.update("fake.porch", state="on")
        by_id, _ = await self.state(["fake.side", "fake.porch"])
        self.assertEqual((by_id["fake.side"]["tone"], by_id["fake.porch"]["tone"]), ("active", "active"))
        await self.h.hub.update("fake.side", state="LOCKING")  # between states: neutral, not "not secure"
        await self.h.hub.update("fake.porch", available=False)
        by_id, _ = await self.state(["fake.side", "fake.porch"])
        self.assertEqual((by_id["fake.side"]["tone"], by_id["fake.porch"]["tone"]), ("", "offline"))

    async def test_home_status_all_secure(self):
        body = await self.home()
        self.assertEqual((body["secure"], body["headline"], body["detail"]), (True, "All secure", "lock locked · door closed"))
        self.assertEqual(body["problems"], [])
        self.assertEqual([(g["label"], g["text"], g["secure"]) for g in body["groups"]],
                         [("Lock", "Locked", True), ("Door sensor", "Closed", True), ("Alarm", "Disarmed", None)])
        self.assertEqual(set(body["ids"]), {"fake.side", "fake.door", "fake.alarm"})
        # More of a kind: counted, "All locked".
        self.h.hub.upsert("fake.front", kind="lock", name="Front Door", integration="fake")
        await self.h.hub.update("fake.front", state="LOCKED")
        body = await self.home()
        self.assertEqual(body["detail"], "2 locks · door closed")
        self.assertEqual(body["groups"][0]["label"], "Locks")
        self.assertEqual(body["groups"][0]["text"], "All locked")

    async def test_home_status_names_what_isnt_secure_and_how_to_fix_it(self):
        await self.h.hub.update("fake.side", state="UNLOCKED")
        body = await self.home()
        self.assertFalse(body["secure"])
        self.assertEqual((body["headline"], body["detail"]), ("Side Door unlocked", "door closed"))
        self.assertEqual(body["problems"], [{"id": "fake.side", "name": "Side Door", "kind": "lock", "icon": "lock",
                                             "state": "UNLOCKED", "stateLabel": "Unlocked", "urgent": False,
                                             "fix": {"command": "lock", "label": "Lock", "sensitive": False}}])
        self.assertEqual(body["groups"][0], {"kind": "lock", "label": "Lock", "text": "Unlocked", "secure": False,
                                             "icon": "lock"})
        # A garage door left open, a leak (urgent, nothing to tap) and an open door sensor (nothing to tap).
        for eid, kind, name, state in (("fake.garage", "garage", "Garage", "open"), ("fake.leak", "leak", "Laundry", "wet")):
            self.h.hub.upsert(eid, kind=kind, name=name, integration="fake")
            await self.h.hub.update(eid, state=state)
        await self.h.hub.update("fake.door", state="open")
        body = await self.home()
        self.assertEqual(body["headline"], "4 not secure")
        self.assertEqual([p["id"] for p in body["problems"]], ["fake.leak", "fake.garage", "fake.side", "fake.door"])
        self.assertEqual(body["problems"][1]["fix"], {"command": "close", "label": "Close", "sensitive": False})
        self.assertIsNone(body["problems"][0]["fix"])
        self.assertEqual(body["detail"], "Laundry leak! · Garage open · Side Door unlocked · Back Door open")
        self.assertTrue(all(g["secure"] is False for g in body["groups"][:4]))
        # Two the same: "2 open".
        await self.h.hub.update("fake.leak", state="dry")
        await self.h.hub.update("fake.side", state="LOCKED")
        self.assertEqual((await self.home())["headline"], "2 open")
        # Offline ones aren't "not secure"; they're counted apart.
        await self.h.hub.update("fake.garage", state="closed")
        await self.h.hub.update("fake.door", available=False)
        body = await self.home()
        self.assertEqual((body["secure"], body["headline"]), (True, "All secure"))
        self.assertEqual(body["detail"], "lock locked · garage door closed · leak sensor dry · 1 offline")
        door = next(g for g in body["groups"] if g["kind"] == "contact")
        self.assertEqual((door["text"], door["secure"]), ("Offline", None))
        # Running the fix is the ordinary command endpoint.
        await self.h.hub.update("fake.side", state="UNLOCKED")
        fix = (await self.home())["problems"][0]["fix"]["command"]
        st, _ = await self.call("POST", "/api/entities/fake.side/command", self.dante, json={"command": fix})
        self.assertEqual(st, 200)
        self.assertIn(("fake.side", "lock"), self.fake.calls)

    def test_home_status_from_kind_metadata_alone(self):
        from newman.state import Entity
        for k in KINDS.values():
            e = Entity(id="x", kind=k.id, name="X", integration="fake")
            body = W.home_json([e])  # every kind renders, even with no state yet
            covered = k.secure_state is not None or k.is_mode
            self.assertEqual(body["ids"], ["x"] if covered else [], k.id)
            if k.secure_state is None:
                self.assertIsNone(body["secure"], k.id)
            fix = W.fix_action(k)
            if fix:
                self.assertEqual(fix.confirms_to, k.secure_state)
        self.assertEqual(W.home_json([])["headline"], "Nothing to keep secure")

    async def test_home_status_widget_follows_what_it_covers(self):
        await self.register(self.dante)
        await self.home()  # Dante's home widget covers the lock, the door sensor and the alarm
        await self.settle(0.4)
        await self.h.hub.update("fake.porch", state="on")
        await self.settle()
        self.assertEqual(self.apns.sent, [])
        await self.h.hub.update("fake.door", state="open")
        await self.settle()
        self.assertEqual(len(self.apns.sent), 1)


if __name__ == "__main__":
    unittest.main()
