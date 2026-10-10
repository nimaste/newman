"""Live statuses (live.py, push/live.py): a device's "Live status while…" setting (validation from its kind),
starting / updating / ending iPhone Live Activities with ActivityKit pushes (payloads, tokens, the 8-hour
restart), recipients, more than five at once ("N more"), the automation action "Show a live status" with
each kind of "until", Android / web over the live socket and test phones' outbox, GET /api/live/current."""
import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

import aiohttp  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman import live as L  # noqa: E402
from newman.api import build_app  # noqa: E402
from newman.automations import describe as d  # noqa: E402
from newman.config import config  # noqa: E402
from newman.kinds import KINDS  # noqa: E402
from newman.push.apns import PushError  # noqa: E402
from test_auth import FakeLocks  # noqa: E402
from test_locations import FakeApns, H  # noqa: E402

PTS_C, PTS_D = "a1" * 32, "d1" * 32
UPD_1, UPD_2 = "b1" * 32, "b2" * 32


class DeadApns(FakeApns):
    async def send(self, token, payload, **kw):
        self.sent.append((token, payload, kw))
        raise PushError("APNs 410 Unregistered", dead_token=True)


class LiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.fake = FakeLocks(self.h.integrations._ctx)
        self.h.integrations.instances["fake"] = self.fake
        hub = self.h.hub
        self.devices = [("fake.garage", "garage", "Garage Door", "closed"), ("fake.side", "lock", "Side Door", "LOCKED"),
                        ("fake.porch", "light", "Porch", "off"), ("fake.temp", "temperature", "Basement", "21.5"),
                        ("fake.bell", "doorbell", "Front Door", "idle")]
        self.devices += [(f"fake.d{i}", "contact", f"Window {i}", "closed") for i in range(7)]
        for eid, kind, name, state in self.devices:
            hub.upsert(eid, kind=kind, name=name, integration="fake")
            await hub.update(eid, state=state)
        self.app = build_app(self.h)
        self.live = self.h.live
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        r = await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        self.assertEqual(r.status, 200)
        await self.client.post("/api/users", json={"username": "dante", "name": "Dante", "role": "family",
                                                   "password": "another pass"})
        self.chris = await self.token("chris", "correct horse")
        self.dante = await self.token("dante")
        people = {p["name"]: p["id"] for p in self.h.store.people()}
        self.pc, self.pd = people["Chris"], people["Dante"]
        self.apns = FakeApns()
        self.h.notifier._apns = self.apns
        await asyncio.sleep(0.05)

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

    async def settle(self, s=0.15):
        await asyncio.sleep(s)

    async def setting(self, eid, who=None, **body):
        return await self.call("PUT", f"/api/entities/{eid}/live-status", who or self.chris, json=body)

    async def tokens(self, who, device_id, pts):
        st, body = await self.call("POST", "/api/live/tokens", who, json={"kind": "push-to-start", "token": pts,
                                                                          "deviceId": device_id})
        self.assertEqual(st, 200, body)

    def pushes(self, event=None):
        out = [(tok, p["aps"], kw) for tok, p, kw in self.apns.sent]
        return [x for x in out if event is None or x[1]["event"] == event]

    # -- the setting ---------------------------------------------------------------------------------------

    async def test_setting_comes_from_the_kind_and_is_checked(self):
        st, ents = await self.call("GET", "/api/entities", self.chris)
        by_id = {e["id"]: e for e in ents}
        lock = by_id["fake.side"]["liveStatus"]
        self.assertEqual(lock, {"enabled": False, "states": ["UNLOCKED"], "recipients": None, "showing": False,
                                "choices": [{"id": "LOCKED", "label": "Locked"}, {"id": "UNLOCKED", "label": "Unlocked"},
                                            {"id": "JAMMED", "label": "Jammed"}]})  # no moments in between
        self.assertIsNone(by_id["fake.temp"]["liveStatus"])  # a reading: no states to pick
        for k in KINDS.values():  # every kind's default is one of its own choices
            self.assertTrue(set(L.default_states(k)) <= {c["id"] for c in L.state_choices(k)}, k.id)
        bad = [({"states": ["OPEN"]}, "Pick states"), ({"enabled": True, "states": []}, "at least one"),
               ({"enabled": True, "states": ["LOCKED", "UNLOCKED", "JAMMED"]}, "never ends"),
               ({"recipients": []}, "who sees"), ({"recipients": ["p_nobody"]}, "household"), ({"enabled": "yes"}, "true")]
        for body, words in bad:
            st, b = await self.setting("fake.side", **body)
            self.assertEqual(st, 400, body)
            self.assertIn(words, b["error"])
        self.assertEqual((await self.setting("fake.temp", enabled=True))[0], 400)
        self.assertEqual((await self.setting("fake.side", self.dante, enabled=True))[0], 403)  # admins only
        st, e = await self.setting("fake.side", enabled=True, states=["JAMMED", "UNLOCKED"], recipients=[self.pd])
        self.assertEqual(st, 200)
        self.assertEqual(e["liveStatus"]["states"], ["UNLOCKED", "JAMMED"])  # the kind's order
        self.assertEqual(e["liveStatus"]["recipients"], [self.pd])
        # Turned back to the defaults: nothing kept.
        await self.setting("fake.side", enabled=False, states=["UNLOCKED"], recipients=None)
        self.assertEqual(self.live.settings, {})

    # -- a device's live status on an iPhone -----------------------------------------------------------

    async def test_start_update_end_with_activitykit_pushes(self):
        await self.tokens(self.chris, "iph-c", PTS_C)
        await self.setting("fake.garage", enabled=True, states=["open", "stopped"])
        self.assertEqual(self.apns.sent, [])  # closed: nothing to show
        before = time.time()
        await self.h.hub.update("fake.garage", state="open")
        await self.settle()
        starts = self.pushes("start")
        self.assertEqual(len(starts), 1)
        tok, aps, kw = starts[0]
        self.assertEqual(tok, PTS_C)
        self.assertEqual((kw["push_type"], kw["topic"], kw["priority"]),
                         ("liveactivity", f"{config.apns_bundle_id}.push-type.liveactivity", 10))
        self.assertEqual(aps["attributes-type"], "NewmanLiveAttributes")
        key = aps["attributes"]["key"]
        self.assertTrue(key.startswith("device:fake.garage."))
        self.assertEqual(aps["attributes"]["title"], "Garage Door")
        self.assertGreaterEqual(aps["timestamp"], int(before))
        cs = aps["content-state"]
        self.assertEqual({k: cs[k] for k in ("title", "subtitle", "active", "more")},
                         {"title": "Garage Door", "subtitle": "Open", "active": True, "more": 0})
        self.assertEqual(cs["devices"], [{"id": "fake.garage", "name": "Garage Door", "icon": "garage", "stateLabel": "Open",
                                          "active": True, "actions": [{"command": "close", "label": "Close",
                                                                       "sensitive": False}]}])
        self.assertAlmostEqual(cs["since"], before, delta=2)
        self.assertEqual(aps["alert"], {"title": "Garage Door", "body": "Open"})
        self.assertLess(len(json.dumps(cs)), 4096)
        # The phone hands over the activity's own token: changes go to it.
        st, _ = await self.call("POST", "/api/live/tokens", self.chris,
                                json={"kind": "update", "activityKey": key, "token": UPD_1, "deviceId": "iph-c"})
        self.assertEqual(st, 200)
        await self.settle()
        self.assertEqual(self.pushes("update"), [])  # it already shows this
        await self.h.hub.update("fake.garage", state="closing")  # moving: still its live status
        await self.settle()
        upd = self.pushes("update")
        self.assertEqual(len(upd), 1)
        self.assertEqual(upd[0][0], UPD_1)
        self.assertEqual(upd[0][1]["content-state"]["subtitle"], "Closing")
        self.assertEqual(upd[0][2]["priority"], 10)
        await self.h.hub.update("fake.garage", state="stopped")
        await self.settle()
        self.assertEqual(len(self.pushes("update")), 2)
        self.assertEqual(len(self.pushes("start")), 1)
        # Closed: it ends, showing "Garage Door closed" for two minutes.
        await self.h.hub.update("fake.garage", state="closed")
        await self.settle()
        ends = self.pushes("end")
        self.assertEqual(len(ends), 1)
        tok, aps, _ = ends[0]
        self.assertEqual(tok, UPD_1)
        self.assertEqual(aps["content-state"]["title"], "Garage Door closed")
        self.assertFalse(aps["content-state"]["active"])
        self.assertEqual(aps["content-state"]["devices"][0]["actions"], [])
        self.assertAlmostEqual(aps["dismissal-date"], aps["timestamp"] + 120, delta=1)
        self.assertEqual(self.live.tokens.all(), [("iph-c", "push-to-start", "")])  # the activity's token went
        self.assertEqual(self.live.tokens.all_sent(), [])

    async def test_without_a_push_to_start_token_the_app_starts_it_itself(self):
        await self.setting("fake.garage", enabled=True)
        await self.h.hub.update("fake.garage", state="open")
        await self.settle()
        self.assertEqual(self.apns.sent, [])
        st, cur = await self.call("GET", "/api/live/current?deviceId=iph-c", self.chris)
        self.assertEqual(st, 200)
        self.assertEqual((cur["attributesType"], cur["max"]), ("NewmanLiveAttributes", 5))
        [a] = cur["activities"]
        self.assertEqual(a["attributes"], {"key": a["key"], "title": "Garage Door"})
        self.assertEqual(a["slot"], "device:fake.garage")
        self.assertEqual(a["contentState"]["subtitle"], "Open")
        # It started that activity and gives its token: from now on it gets updates, and the end.
        await self.call("POST", "/api/live/tokens", self.chris,
                        json={"kind": "update", "activityKey": a["key"], "token": UPD_1, "deviceId": "iph-c"})
        await self.settle()
        self.assertEqual(len(self.pushes("update")), 1)  # what it started with isn't known: sent once
        await self.h.hub.update("fake.garage", state="closed")
        await self.settle()
        self.assertEqual(len(self.pushes("end")), 1)
        # A token for an activity that isn't wanted (any more) is ended straight away.
        await self.call("POST", "/api/live/tokens", self.chris,
                        json={"kind": "update", "activityKey": "device:fake.garage.1", "token": UPD_2, "deviceId": "iph-c"})
        await self.settle()
        end = self.pushes("end")[-1]
        self.assertEqual(end[0], UPD_2)
        self.assertEqual(end[1]["dismissal-date"], end[1]["timestamp"])

    async def test_recipients(self):
        await self.tokens(self.chris, "iph-c", PTS_C)
        await self.tokens(self.dante, "iph-d", PTS_D)
        await self.setting("fake.side", enabled=True, recipients=[self.pd])
        await self.h.hub.update("fake.side", state="UNLOCKED")
        await self.settle()
        self.assertEqual([t for t, _, _ in self.pushes("start")], [PTS_D])
        a = self.pushes("start")[0][1]
        self.assertEqual(a["content-state"]["devices"][0]["actions"],
                         [{"command": "lock", "label": "Lock", "sensitive": False}])
        self.assertEqual((await self.call("GET", "/api/live/current", self.chris))[1]["activities"], [])
        # Everyone.
        await self.setting("fake.porch", enabled=True)
        await self.h.hub.update("fake.porch", state="on")
        await self.settle()
        self.assertEqual(sorted(t for t, _, _ in self.pushes("start")), sorted([PTS_D, PTS_D, PTS_C]))

    async def test_more_than_five_share_one_more_status(self):
        now = [1_800_000_000.0]
        self.live.clock = lambda: now[0]
        await self.tokens(self.chris, "iph-c", PTS_C)
        for i in range(7):
            await self.setting(f"fake.d{i}", enabled=True)
        for i in range(7):
            now[0] += 60
            await self.h.hub.update(f"fake.d{i}", state="open")
            await self.settle(0.1)
        view = self.live.view(self.pc)
        self.assertEqual(len(view), 5)
        self.assertEqual([v["slot"] for v in view[:4]], [f"device:fake.d{i}" for i in (6, 5, 4, 3)])  # newest first
        more = view[4]
        self.assertEqual(more["slot"], "more")
        self.assertEqual((more["contentState"]["title"], more["contentState"]["more"]), ("3 more", 0))  # all 3 listed
        self.assertEqual([r["name"] for r in more["contentState"]["devices"]], ["Window 2", "Window 1", "Window 0"])
        # The phone never had more than five: the 6th made the oldest move into "N more" (ended at once).
        starts = [a["attributes"]["key"] for _, a, _ in self.pushes("start")]
        self.assertEqual(len(set(starts)), 8)  # each window once on its own, and "more" once
        ends = self.pushes("end")
        self.assertEqual(ends, [])  # no update tokens given: nothing to end by push...
        self.assertEqual(sorted(k for _, k, _ in self.live.tokens.all_sent()), sorted(v["key"] for v in view))
        # Fewer rows than grouped statuses: "more" counts the ones not listed (rows + more = all of them).
        old_rows = L.MORE_ROWS
        L.MORE_ROWS = 2
        try:
            cs = self.live.view(self.pc)[4]["contentState"]
        finally:
            L.MORE_ROWS = old_rows
        self.assertEqual((cs["title"], len(cs["devices"]), cs["more"]), ("3 more", 2, 1))
        # One closes: still six -> "2 more", updated.
        await self.h.hub.update("fake.d1", state="closed")
        await self.settle()
        self.assertEqual(self.live.view(self.pc)[4]["contentState"]["title"], "2 more")
        # Down to five: everyone their own again, "more" gone.
        await self.h.hub.update("fake.d6", state="closed")
        await self.settle()
        view = self.live.view(self.pc)
        self.assertEqual([v["slot"] for v in view], [f"device:fake.d{i}" for i in (5, 4, 3, 2, 0)])
        self.assertNotIn("more", {s for _, _, s in self.live.tokens.all_sent()})

    async def test_restart_before_eight_hours(self):
        now = [1_800_000_000.0]
        self.live.clock = lambda: now[0]
        await self.tokens(self.chris, "iph-c", PTS_C)
        await self.setting("fake.garage", enabled=True)
        await self.h.hub.update("fake.garage", state="open")
        await self.settle()
        key = self.pushes("start")[0][1]["attributes"]["key"]
        await self.call("POST", "/api/live/tokens", self.chris,
                        json={"kind": "update", "activityKey": key, "token": UPD_1, "deviceId": "iph-c"})
        await self.settle()
        now[0] += 7.6 * 3600
        self.live.tick()
        await self.settle()
        end = self.pushes("end")
        self.assertEqual(len(end), 1)
        self.assertEqual(end[0][0], UPD_1)
        self.assertEqual(end[0][1]["dismissal-date"], end[0][1]["timestamp"])  # moved on: gone at once
        self.assertTrue(end[0][1]["content-state"]["devices"])
        starts = self.pushes("start")
        self.assertEqual(len(starts), 2)
        self.assertEqual(starts[1][1]["attributes"]["key"], key + ".1")
        self.assertEqual(starts[1][1]["content-state"]["since"], starts[0][1]["content-state"]["since"])

    async def test_dead_tokens_and_signing_out(self):
        await self.tokens(self.chris, "iph-c", PTS_C)
        self.h.notifier._apns = dead = DeadApns()
        await self.setting("fake.garage", enabled=True)
        await self.h.hub.update("fake.garage", state="open")
        await self.settle()
        self.assertEqual(len(dead.sent), 1)
        self.assertEqual(self.live.tokens.all(), [])
        await self.tokens(self.dante, "iph-d", PTS_D)
        bad = [{"kind": "pager", "token": PTS_D, "deviceId": "x"}, {"kind": "push-to-start", "token": "no", "deviceId": "x"},
               {"kind": "update", "token": PTS_D, "deviceId": "x"}, {"kind": "push-to-start", "token": PTS_D, "deviceId": ""}]
        for b in bad:
            self.assertEqual((await self.call("POST", "/api/live/tokens", self.dante, json=b))[0], 400, b)
        self.assertEqual((await self.call("DELETE", "/api/live/tokens?kind=push-to-start&deviceId=iph-d", self.chris))[0],
                         200)  # an admin may
        await self.tokens(self.dante, "iph-d", PTS_D)
        st, _ = await self.call("POST", "/api/auth/logout", self.dante)
        self.assertEqual(st, 200)
        self.assertEqual(self.live.tokens.all(), [])

    # -- automations ---------------------------------------------------------------------------------------

    async def auto(self, live, trigger=None, **extra):
        body = {"name": "Garage watch", "trigger": trigger or {"items": [{"type": "state", "entities": ["fake.garage"],
                                                                         "to": "open"}]},
                "notify": {"enabled": False}, "live": {"enabled": True, **live}, **extra}
        st, a = await self.call("POST", "/api/automations", self.chris, json=body)
        self.assertEqual(st, 200, a)
        return self.h.engine.autos[a["id"]]

    async def test_automation_wording_and_errors(self):
        hub, people = self.h.hub, self.h.store.people()
        a = d.normalize({"trigger": {"items": [{"type": "state", "entities": ["fake.garage"], "to": "open"}]},
                         "notify": {"enabled": False}, "live": {"enabled": True, "entities": ["fake.garage"]}})
        self.assertEqual(d.errors(a, hub), [])
        self.assertEqual(d.summary(a, hub, people),
                         "When Garage Door becomes open, show a live status of Garage Door to everyone until Garage Door is "
                         "no longer open.")
        a["live"].update(title="Garage", recipients=[self.pd], until={"type": "duration", "amount": 30, "unit": "minutes"})
        self.assertIn("show a live status called “Garage” of Garage Door to Dante for 30 minutes", d.summary(a, hub, people))
        a["live"]["until"] = {"type": "trigger", "item": {"type": "event", "entity": "fake.bell", "event": "pressed"}}
        self.assertIn("until someone rings the Front Door doorbell", d.summary(a, hub, people))
        a["live"]["until"] = {"type": "leaves", "entities": ["fake.d0", "fake.d1"], "state": "open"}
        self.assertIn("until none of Window 0 or Window 1 is open", d.summary(a, hub, people))
        bad = [({"entities": []}, "Pick the devices"), ({"recipients": []}, "Pick who sees"),
               ({"until": {"type": "duration", "amount": 0}}, "positive amount"),
               ({"until": {"type": "leaves", "state": "LOCKED"}}, "is never “LOCKED”"),
               ({"until": {"type": "trigger", "item": {"type": "event", "entity": "fake.bell"}}}, "what it does"),
               ({"until": {"type": "trigger", "item": {"type": "state", "entities": []}}}, "whose change")]
        for live, words in bad:
            b = d.normalize({**a, "live": {"enabled": True, "entities": ["fake.garage"], **live}})
            self.assertTrue(any(words in e for e in d.errors(b, hub)), (live, d.errors(b, hub)))
        # Uses the devices it names.
        auto = await self.auto({"entities": ["fake.porch"], "until": {"type": "leaves", "entities": ["fake.side"],
                                                                      "state": "UNLOCKED"}})
        self.assertIn(auto["id"], self.h.engine.used_by("fake.side"))
        self.assertIn(auto["id"], self.h.engine.used_by("fake.porch"))

    async def test_automation_until_it_leaves(self):
        auto = await self.auto({"entities": ["fake.garage", "fake.porch"], "title": "Garage open",
                                "until": {"type": "leaves", "entities": ["fake.garage"], "state": "active"}})
        await self.h.hub.update("fake.garage", state="open")
        await self.settle()
        run = await self.h.engine.trigger(auto, {"type": "state", "entity": "fake.garage", "to": "open"})
        self.assertIn("Showing a live status to everyone until Garage Door is no longer open", run["log"][-1]["text"])
        [v] = self.live.view(self.pc)
        self.assertEqual(v["slot"], f"automation:{auto['id']}")
        self.assertEqual(v["contentState"]["title"], "Garage open")
        self.assertEqual([x["active"] for x in v["contentState"]["devices"]], [True, False])
        await self.h.hub.update("fake.garage", state="closing")
        await self.settle()
        self.assertEqual(len(self.live.view(self.pc)), 1)  # moving is in between
        await self.h.hub.update("fake.garage", state="closed")
        await self.settle()
        self.assertEqual(self.live.view(self.pc), [])
        # Already left: nothing starts.
        run = await self.h.engine.trigger(auto, {"type": "state", "entity": "fake.garage", "to": "open"}, force=True)
        self.assertIn("No live status", " ".join(x["text"] for x in run["log"]))
        self.assertEqual(self.live.view(self.pc), [])

    async def test_automation_for_a_while_and_until_something_happens(self):
        now = [1_800_000_000.0]
        self.live.clock = lambda: now[0]
        auto = await self.auto({"entities": ["fake.porch"], "until": {"type": "duration", "amount": 10, "unit": "minutes"}})
        await self.h.engine.trigger(auto, {"type": "state", "entity": "fake.garage", "to": "open"}, force=True)
        self.assertEqual(len(self.live.view(self.pc)), 1)
        now[0] += 9 * 60
        self.live.tick()
        self.assertEqual(len(self.live.view(self.pc)), 1)
        now[0] += 2 * 60
        self.live.tick()
        self.assertEqual(self.live.view(self.pc), [])
        # Until the doorbell rings.
        auto = await self.auto({"entities": ["fake.porch"], "until": {"type": "trigger", "item": {
            "type": "event", "entity": "fake.bell", "event": "pressed"}}})
        await self.h.engine.trigger(auto, {"type": "state", "entity": "fake.garage", "to": "open"}, force=True)
        self.assertEqual(len(self.live.view(self.pc)), 1)
        await self.h.hub.emit("fake.bell", "pressed")
        await self.settle()
        self.assertEqual(self.live.view(self.pc), [])
        # Until a device changes to a state; until a clock time.
        auto = await self.auto({"entities": ["fake.porch"], "until": {"type": "trigger", "item": {
            "type": "state", "entities": ["fake.side"], "to": "UNLOCKED"}}})
        await self.h.engine.trigger(auto, {"type": "state", "entity": "fake.garage", "to": "open"}, force=True)
        await self.h.hub.update("fake.side", state="JAMMED")
        await self.settle()
        self.assertEqual(len(self.live.view(self.pc)), 1)
        await self.h.hub.update("fake.side", state="UNLOCKED")
        await self.settle()
        self.assertEqual(self.live.view(self.pc), [])
        hhmm = time.strftime("%H:%M", time.localtime(now[0] + 120))
        auto = await self.auto({"entities": ["fake.porch"], "until": {"type": "trigger", "item": {
            "type": "time", "time": hhmm, "days": [0, 1, 2, 3, 4, 5, 6]}}})
        await self.h.engine.trigger(auto, {"type": "state", "entity": "fake.garage", "to": "open"}, force=True)
        self.live.tick()
        self.assertEqual(len(self.live.view(self.pc)), 1)
        now[0] += 120
        self.live.tick()
        self.assertEqual(self.live.view(self.pc), [])
        # Turning the automation's live status off ends a running one.
        auto = await self.auto({"entities": ["fake.porch"]}, trigger={"items": [{"type": "state", "entities": ["fake.porch"],
                                                                                 "to": "on"}]})
        await self.h.hub.update("fake.porch", state="on")
        await self.h.engine.trigger(auto, {"type": "state", "entity": "fake.porch", "to": "on"})
        self.assertEqual(len(self.live.view(self.pc)), 1)
        st, _ = await self.call("PUT", f"/api/automations/{auto['id']}", self.chris,
                                json={**auto, "live": {**auto["live"], "enabled": False}, "notify": {"enabled": True},
                                      "recipients": [self.pc]})
        self.assertEqual(st, 200)
        self.assertEqual(self.live.view(self.pc), [])

    async def test_many_devices_keep_the_state_small(self):
        for i in range(60):
            self.h.hub.upsert(f"fake.x{i}", kind="light", name=f"Very long light name number {i:02d} in the hall",
                              integration="fake")
            await self.h.hub.update(f"fake.x{i}", state="on")
        auto = await self.auto({"entities": [f"fake.x{i}" for i in range(60)],
                                "until": {"type": "duration", "amount": 1, "unit": "hours"}})
        await self.h.engine.trigger(auto, {"type": "state", "entity": "fake.garage", "to": "open"}, force=True)
        [v] = self.live.view(self.pc)
        size = len(json.dumps(v["contentState"], separators=(",", ":")).encode())
        self.assertLess(size, 4096)
        self.assertEqual(len(v["contentState"]["devices"]) + v["contentState"]["more"], 60)

    # -- Android, the web, test phones -----------------------------------------------------------------------

    async def test_live_socket_and_test_phone(self):
        st, ph = await self.call("POST", "/api/test/phones", self.chris, json={"personId": self.pc})
        self.assertEqual(st, 200)
        ws = await self.client.ws_connect("/api/ws?token=" + self.dante["Authorization"][7:])
        hello = json.loads((await ws.receive()).data)
        self.assertEqual(hello["type"], "hello")
        await self.setting("fake.garage", enabled=True)
        await self.h.hub.update("fake.garage", state="open")
        msg = None
        for _ in range(10):
            m = json.loads((await asyncio.wait_for(ws.receive(), 2)).data)
            if m["type"] == "live":
                msg = m
                break
        self.assertIsNotNone(msg)
        [a] = msg["activities"]
        self.assertEqual((a["slot"], a["title"], a["contentState"]["subtitle"]), ("device:fake.garage", "Garage Door", "Open"))
        await self.settle()
        out = [m for m in self.h.notifier.outbox if m.get("kind") == "live"]
        self.assertEqual([(m["deviceId"], m["event"]) for m in out], [(ph["deviceId"], "start")])
        self.assertEqual(json.loads(out[0]["state"])["devices"][0]["stateLabel"], "Open")
        await self.h.hub.update("fake.garage", state="closed")
        for _ in range(10):
            m = json.loads((await asyncio.wait_for(ws.receive(), 2)).data)
            if m["type"] == "live":
                break
        self.assertEqual(m["activities"], [])
        [e] = m["ended"]
        self.assertEqual((e["reason"], e["contentState"]["title"]), ("ended", "Garage Door closed"))
        self.assertAlmostEqual(e["dismissAt"], time.time() + 120, delta=3)
        await ws.close()
        # A socket opened while one shows hears about it straight away.
        await self.h.hub.update("fake.garage", state="open")
        await self.settle()
        ws2 = await self.client.ws_connect("/api/ws?token=" + self.dante["Authorization"][7:])
        await ws2.receive()
        first = json.loads((await ws2.receive()).data)
        self.assertEqual((first["type"], len(first["activities"])), ("live", 1))
        await ws2.close()
        await self.h.hub.update("fake.garage", state="closed")
        await self.settle()
        out = [m for m in self.h.notifier.outbox if m.get("kind") == "live"]
        self.assertEqual(out[0]["event"], "end")
        self.assertIn("dismissAt", out[0])

    async def test_removing_a_device_ends_its_live_status_and_setting(self):
        await self.setting("fake.garage", enabled=True)
        await self.h.hub.update("fake.garage", state="open")
        await self.settle()
        self.assertEqual(len(self.live.view(self.pc)), 1)
        self.h.hub.remove("fake.garage")
        await self.settle()
        self.assertEqual(self.live.view(self.pc), [])
        self.assertEqual(self.live.settings, {})


if __name__ == "__main__":
    unittest.main()
