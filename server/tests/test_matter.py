"""Matter, offline: a stand-in Matter server speaking the real websocket API (server_info, start_listening,
events, commission_with_code, device_command, remove_node…) with nodes in the server's raw attribute format."""
import asyncio
import copy
import os
import tempfile
import time
import unittest
from unittest import mock
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.integrations.base import CommandError, Context  # noqa: E402
from newman.integrations.matter import COMMISSION_TIMEOUT_S, MatterIntegration, host_ipv6, setup_code  # noqa: E402
from newman.kinds import properties_for  # noqa: E402
from newman.state import Hub  # noqa: E402
from test_auth import FakeLocks, H  # noqa: E402

WIFI, THREAD = 1, 2


def node(nid, eps, label="", product=None, uid=None, network=WIFI, root=None, available=True):
    a = {"0/29/0": [{"0": 22, "1": 1}], "0/29/1": [29, 40, 49], "0/40/1": "Acme", "0/40/3": product or f"Product {nid}",
         "0/40/5": label, "0/40/10": "1.2.3", "0/49/65532": network}
    if uid:
        a["0/40/18"] = uid
    a.update(root or {})
    for ep, (types, servers, values) in eps.items():
        a[f"{ep}/29/0"] = [{"0": t, "1": 1} for t in types]
        a[f"{ep}/29/1"] = servers
        a.update({f"{ep}/{k}": v for k, v in values.items()})
    return {"node_id": nid, "date_commissioned": "2026-10-01T00:00:00", "last_interview": "2026-10-01T00:00:00",
            "interview_version": 6, "available": available, "is_bridge": False, "attributes": a,
            "attribute_subscriptions": []}


NODES = [
    # A Wi-Fi plug with power and energy on the plug's own endpoint (mW, mV, mA, mWh).
    node(1, {1: ([266], [3, 4, 6, 29, 144, 145], {"6/0": True, "144/8": 12500, "144/4": 230100, "144/5": 54,
                                                   "145/1": {"0": 2_500_000, "1": 0}})}, label="Coffee plug", uid="UID-PLUG-1"),
    node(2, {1: ([257], [6, 8, 29], {"6/0": False, "8/0": 127})}, label="Hall bulb"),
    # A Thread motion sensor that also measures light, on two AAA batteries (Power Source on the root).
    node(3, {1: ([263], [1030, 29], {"1030/0": 0}), 2: ([262], [1024, 29], {"1024/0": 10001})},
         product="Motion sensor M1", network=THREAD,
         root={"0/29/1": [29, 40, 47, 49], "0/47/12": 160, "0/47/14": 0, "0/47/15": False, "0/47/19": "AAA", "0/47/25": 2}),
    node(4, {1: ([67], [47, 69, 29], {"69/0": False, "47/12": 30})}, product="Leak sensor L1", network=THREAD),
    node(5, {1: ([21], [69, 29], {"69/0": False})}, label="Side window"),
    node(6, {1: ([770], [1026, 29], {"1026/0": 2137}), 2: ([775], [1029, 29], {"1029/0": 4820})}, label="Patio"),
    # A plug whose meter is its own Electrical Sensor endpoint.
    node(7, {1: ([266], [6, 29], {"6/0": False}), 2: ([1296], [144, 145, 29], {"144/8": 1500})}, label="Kettle plug"),
    # A bridge with a contact sensor behind it.
    node(8, {1: ([14], [29], {}), 3: ([21, 19], [69, 57, 29], {"57/5": "Back door", "57/18": "BR-CONTACT", "57/17": True,
                                                                  "69/0": True})}, label="Bridge"),
    node(9, {1: ([15], [59, 29], {}), 2: ([15], [59, 29], {})}, label="Bedside button"),
    node(10, {1: ([10], [257, 29], {"257/0": 1})}, label="Front door"),
    # An unknown device type: what it is comes from its clusters.
    node(11, {1: ([0xFFF1], [6, 8, 29], {"6/0": True, "8/0": 254})}, label="Odd lamp"),
]
NEW = node(12, {1: ([266], [6, 29], {"6/0": False})}, product="Smart plug P1", uid="NEW-PLUG")


class MatterServerError(Exception):
    def __init__(self, code, details):
        super().__init__(details)
        self.code, self.details = code, details


class FakeMatterServer:
    """Just enough of matterjs-server's websocket API."""

    def __init__(self, nodes=NODES):
        self.nodes = {n["node_id"]: copy.deepcopy(n) for n in nodes}
        self.calls, self.sockets = [], []
        self.commission_error = None  # (code, details) to refuse the next commissioning
        self.command_error = None
        self.commission_hold = None  # an asyncio.Event: commissioning waits for it (a slow device)
        self.held = []
        self.app = web.Application()
        self.app.router.add_get("/ws", self.ws)

    async def start(self):
        self.server = TestServer(self.app)
        await self.server.start_server()
        return f"ws://127.0.0.1:{self.server.port}/ws"

    async def close(self):
        for t in self.held:
            t.cancel()
        for ws in list(self.sockets):
            await ws.close()
        await self.server.close()

    async def event(self, name, data):
        if name == "attribute_updated" and data[0] in self.nodes:  # the server keeps its copy current
            self.nodes[data[0]]["attributes"][data[1]] = data[2]
        for ws in list(self.sockets):
            await ws.send_json({"event": name, "data": data})

    async def ws(self, req):
        ws = web.WebSocketResponse()
        await ws.prepare(req)
        self.sockets.append(ws)
        await ws.send_json({"fabric_id": 1, "compressed_fabric_id": 77, "schema_version": 13,
                            "min_supported_schema_version": 11, "sdk_version": "fake-matter-server",
                            "wifi_credentials_set": False, "thread_credentials_set": False, "bluetooth_enabled": False})
        async def answer(mid, cmd, args):
            try:
                result = await self.handle(cmd, args)
            except MatterServerError as e:
                await ws.send_json({"message_id": mid, "error_code": e.code, "details": e.details})
                return
            if not ws.closed:
                await ws.send_json({"message_id": mid, "result": result})
        async for msg in ws:
            data = msg.json()
            mid, cmd, args = data["message_id"], data["command"], data.get("args") or {}
            self.calls.append((cmd, args))
            if cmd == "commission_with_code" and self.commission_hold:  # the real one answers others meanwhile
                self.held.append(asyncio.create_task(answer(mid, cmd, args)))
                continue
            await answer(mid, cmd, args)
        if ws in self.sockets:
            self.sockets.remove(ws)
        return ws

    async def handle(self, cmd, args):
        if cmd in ("start_listening", "get_nodes"):
            return copy.deepcopy(list(self.nodes.values()))
        if cmd == "commission_with_code":
            await asyncio.sleep(0.05)
            if self.commission_hold:
                await self.commission_hold.wait()
            if self.commission_error:
                raise MatterServerError(*self.commission_error)
            self.nodes[NEW["node_id"]] = copy.deepcopy(NEW)
            await self.event("node_added", NEW)
            return NEW
        if cmd == "remove_node":
            self.nodes.pop(args["node_id"], None)
            await self.event("node_removed", args["node_id"])
            return None
        if cmd == "device_command":
            if self.command_error:
                raise MatterServerError(*self.command_error)
            if args["cluster_id"] == 6:  # the device does it and reports back
                path = f"{args['endpoint_id']}/6/0"
                self.nodes[args["node_id"]]["attributes"][path] = args["command_name"] == "On"
                await self.event("attribute_updated", [args["node_id"], path, args["command_name"] == "On"])
            return None
        if cmd == "write_attribute":
            return None
        raise MatterServerError(9, f"unknown command {cmd}")


def fake_proc(root, route=True, accept_ra=2, forwarding=1, plen=64):
    (root / "net").mkdir(parents=True, exist_ok=True)
    lines = ["00000000000000000000000000000001 80 00000000000000000000000000000000 00 "
             "00000000000000000000000000000000 00000000 00000001 00000000 00200001 lo"]
    if route:
        lines.append("fd4aad96cdb900000000000000000000 40 00000000000000000000000000000000 00 "
                     "fe8000000000000014e97353c34e52f5 00000400 00000001 00000000 00000003 br0")
    (root / "net" / "ipv6_route").write_text("\n".join(lines) + "\n")
    for iface, vals in {"all": {"disable_ipv6": 0}, "br0": {"disable_ipv6": 0, "accept_ra": accept_ra,
                                                              "forwarding": forwarding, "accept_ra_rt_info_max_plen": plen}}.items():
        d = root / "sys" / "net" / "ipv6" / "conf" / iface
        d.mkdir(parents=True, exist_ok=True)
        for k, v in vals.items():
            (d / k).write_text(f"{v}\n")
    return root


async def until(cond, what="condition", timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


class MatterBase(unittest.IsolatedAsyncioTestCase):
    route = True
    nodes = NODES

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.old_proc = MatterIntegration.proc
        MatterIntegration.proc = fake_proc(self.data / "proc", route=self.route)
        self.server = FakeMatterServer(self.nodes)
        self.url = await self.server.start()

    async def asyncTearDown(self):
        MatterIntegration.proc = self.old_proc
        await self.server.close()
        self.tmp.cleanup()

    async def start(self, hub=None):
        self.hub = hub or Hub()
        self.m = MatterIntegration("matter", "Matter", {"url": self.url}, Context(self.hub, self.data, self.data))
        self.saved = []
        self.m.save_config = self.saved.append
        self.task = asyncio.create_task(self.m.run())
        await until(lambda: self.m.status == "connected", "connected")
        return self.m

    async def stop(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    def by_name(self):
        return {e.name: e for e in self.hub.all()}


class CheckAgainTests(MatterBase):
    """Every "Check everything every" minutes: the server's own copy of every device, run through setup again."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fast = mock.patch.object(MatterIntegration, "seconds_every", lambda self, key, unit=60.0: 0.05)
        self.fast.start()

    async def asyncTearDown(self):
        self.fast.stop()
        await super().asyncTearDown()

    def reads(self):
        return sum(1 for c, _ in self.server.calls if c == "get_nodes")

    async def test_a_missed_change_is_picked_up_and_nothing_else_moves(self):
        hub = Hub()
        added, removed = [], []

        async def on_added(e):
            added.append(e.id)

        async def on_removed(ids):
            removed.extend(ids)
        hub.subscribe_registry(on_added, on_removed)
        await self.start(hub)
        await asyncio.sleep(0.1)
        added.clear()
        ids = {e.id for e in hub.all()}
        await until(lambda: self.reads() >= 2, "two checks")
        self.assertEqual(({e.id for e in hub.all()}, added, removed), (ids, [], []))
        self.assertTrue(all(e.available for e in hub.all()))
        # A change the server has but whose event never arrived.
        bulb = self.by_name()["Hall bulb"].id
        self.server.nodes[2]["attributes"]["1/6/0"] = True
        await until(lambda: hub.get(bulb).state == "on", "the missed change")
        self.assertEqual((added, removed), ([], []))
        # Only the server's copy is read: nothing is asked of the devices themselves.
        self.assertFalse(any(c == "read_attribute" for c, _ in self.server.calls))
        await self.stop()
        n = self.reads()
        await asyncio.sleep(0.2)
        self.assertEqual(self.reads(), n)  # stops with the connection

    async def test_one_check_per_connection_after_reconnects(self):
        await self.start()
        for ws in list(self.server.sockets):
            await ws.close()  # the server restarts: Newman reconnects
        await until(lambda: len(self.server.sockets) == 1 and self.m.status == "connected", "reconnected")
        before = self.reads()
        await asyncio.sleep(0.3)
        self.assertLessEqual(self.reads() - before, 0.3 / 0.05 + 1)  # one loop, not two
        await self.stop()

    def test_the_setting(self):
        f = next(f for f in MatterIntegration.fields if f.key == "check_minutes")
        self.assertEqual((f.type, f.default, f.minimum), ("number", 15, 1))
        self.assertEqual(MatterIntegration.validate({"url": "ws://x", "check_minutes": 0}),
                         ["Check everything every (minutes): 1 or more"])
        self.assertEqual(MatterIntegration.validate({"url": "ws://x", "check_minutes": 5}), [])


class MappingTests(MatterBase):
    async def test_device_types_and_clusters_map_onto_kinds(self):
        m = await self.start()
        e = self.by_name()
        plug = e["Coffee plug"]
        self.assertEqual((plug.id, plug.kind, plug.state), ("matter.uid_plug_1_1_onoff", "switch", "on"))
        # Readings as reported, in their real units - not rounded.
        self.assertEqual((plug.attrs["power"], plug.attrs["voltage"], plug.attrs["current"], plug.attrs["energy"]),
                         (12.5, 230.1, 0.054, 2.5))
        props = {p["id"]: p for p in properties_for(plug)}
        self.assertEqual((props["power"]["unit"], props["energy"]["unit"], props["voltage"]["unit"]), ("W", "kWh", "V"))
        self.assertEqual(plug.attrs["protocol"], "Matter over Wi-Fi")
        bulb = e["Hall bulb"]
        self.assertEqual((bulb.kind, bulb.state, bulb.attrs["dimmable"], bulb.attrs["brightness"]), ("light", "off", True, 50))
        # A motion sensor that also measures light: one motion sensor with a light level.
        motion = e["Motion sensor M1"]
        self.assertNotIn("Motion sensor M1 light level", e)
        self.assertEqual((motion.kind, motion.state, motion.attrs["batteryLevel"]), ("motion", "clear", 80))
        self.assertEqual((motion.attrs["batteryType"], motion.attrs["batteryLow"], motion.attrs["protocol"]),
                         ("AAA × 2", False, "Matter over Thread"))
        lux = {p["id"]: p for p in properties_for(motion)}["illuminance"]
        self.assertEqual((motion.id, lux["value"], lux["unit"]), ("matter.node3_1_motion", 10, "lx"))
        self.assertEqual(motion.health_issues, [])
        leak = e["Leak sensor L1"]
        self.assertEqual((leak.kind, leak.state, leak.attrs["batteryLevel"]), ("leak", "dry", 15))
        self.assertIn("Battery 15%", leak.health_issues)
        self.assertEqual({p["id"]: p["value"] for p in properties_for(leak)}["battery"], 15)
        self.assertEqual((e["Side window"].kind, e["Side window"].state), ("contact", "open"))
        patio = e["Patio"]
        self.assertEqual((patio.kind, patio.state, patio.id), ("temperature", "21.37", "matter.node6_1_temperature"))
        self.assertNotIn("Patio humidity", e)
        value = {p["id"]: p for p in properties_for(patio)}["humidity"]
        self.assertEqual((value["value"], value["unit"]), (48.2, "%"))
        kettle = e["Kettle plug"]
        self.assertEqual((kettle.kind, kettle.state, kettle.attrs["power"]), ("switch", "off", 1.5))
        self.assertNotIn("Kettle plug power", e)  # its meter endpoint belongs to the plug
        back = e["Back door"]
        self.assertEqual((back.kind, back.state, back.id), ("contact", "closed", "matter.br_contact_boolean"))
        self.assertEqual((e["Bedside button"].kind, e["Bedside button"].attrs["events"]), ("remote", ["button_1", "button_2"]))
        self.assertEqual((e["Front door"].kind, e["Front door"].state), ("lock", "LOCKED"))
        self.assertEqual((e["Odd lamp"].kind, e["Odd lamp"].state, e["Odd lamp"].attrs["brightness"]), ("light", "on", 100))
        self.assertNotIn("Bridge", e)
        self.assertEqual(m.status, "connected")
        self.assertIn("Thread network reachable", m.detail)
        await self.stop()

    async def test_live_updates_availability_and_buttons(self):
        m = await self.start()
        motion_id = self.by_name()["Motion sensor M1"].id
        leak_id = self.by_name()["Leak sensor L1"].id
        events = []

        async def on_event(ent, event, data):
            events.append((ent.name, event))
        self.hub.subscribe_events(on_event)
        await self.server.event("attribute_updated", [3, "1/1030/0", 1])
        await self.server.event("attribute_updated", [4, "1/69/0", True])
        await self.server.event("attribute_updated", [1, "1/144/8", 3_210_987])
        await self.server.event("attribute_updated", [3, "0/47/12", 37])
        await self.server.event("attribute_updated", [3, "0/47/14", 1])
        await self.server.event("node_event", {"node_id": 9, "endpoint_id": 2, "cluster_id": 59, "event_id": 1,
                                               "event_number": 5, "priority": 1, "timestamp": 0, "timestamp_type": 1,
                                               "data": {"newPosition": 1}})
        await until(lambda: self.hub.get(motion_id).attrs.get("batteryLow") is True, "battery low")
        self.assertEqual(self.hub.get(motion_id).state, "detected")
        self.assertEqual(self.hub.get(leak_id).state, "wet")
        self.assertEqual(self.by_name()["Coffee plug"].attrs["power"], 3210.987)
        self.assertEqual(self.hub.get(motion_id).attrs["batteryLevel"], 18.5)
        self.assertEqual(self.hub.get(motion_id).health_issues, ["Battery 18.5%"])
        await self.server.event("attribute_updated", [3, "0/47/12", 120])
        await until(lambda: self.hub.get(motion_id).attrs["batteryLevel"] == 60, "battery 60")
        self.assertEqual(self.hub.get(motion_id).health_issues, ["Battery low (takes AAA × 2)"])
        await until(lambda: events == [("Bedside button", "button_2")], "button")
        # The server loses the device: unavailable (state kept), and the status says so.
        gone = copy.deepcopy(self.server.nodes[4])
        gone["available"] = False
        await self.server.event("node_updated", gone)
        await until(lambda: not self.hub.get(leak_id).available, "leak offline")
        self.assertEqual(self.hub.get(leak_id).state, "wet")
        self.assertIn("1 device not answering", m.detail)
        self.assertIn("1 Thread device not answering, though this computer has a route", m.detail)
        # A bridged device the bridge can't reach.
        await self.server.event("attribute_updated", [8, "3/57/17", False])
        await until(lambda: not self.by_name()["Back door"].available, "bridged offline")
        # The server goes away: everything unavailable, and back when it returns.
        for ws in list(self.server.sockets):
            await ws.close()
        await until(lambda: m.status == "error", "error")
        self.assertIn("Can't reach the Matter server", m.detail)
        self.assertFalse(self.by_name()["Coffee plug"].available)
        await until(lambda: m.status == "connected", "reconnected", timeout=6)
        await until(lambda: self.by_name()["Coffee plug"].available, "plug back")
        await self.stop()

    async def test_commands_round_trip_through_the_server(self):
        m = await self.start()
        e = self.by_name()
        plug, bulb, lock = e["Coffee plug"].id, e["Hall bulb"].id, e["Front door"].id
        await m.command(plug, "turn_off")
        await until(lambda: self.hub.get(plug).state == "off", "plug off")  # the device's own report
        await m.command(plug, "turn_on")
        await until(lambda: self.hub.get(plug).state == "on", "plug on")
        await m.command(bulb, "set_brightness", 50)
        await m.command(lock, "unlock")
        sent = [a for c, a in self.server.calls if c == "device_command"]
        self.assertEqual([(a["node_id"], a["endpoint_id"], a["cluster_id"], a["command_name"]) for a in sent],
                         [(1, 1, 6, "Off"), (1, 1, 6, "On"), (2, 1, 8, "MoveToLevelWithOnOff"), (10, 1, 257, "UnlockDoor")])
        self.assertEqual(sent[2]["payload"]["level"], 127)
        self.assertEqual(sent[3]["timed_request_timeout_ms"], 1000)
        # Hold to brighten / dim: the light's own Level Control move - up with on/off, down without, stop.
        self.assertTrue(m.can_move(bulb, "brightness"))
        self.assertFalse(m.can_move(plug, "brightness"))
        n = len(sent)
        for direction in (1, -1, 0):
            await m.move(bulb, "brightness", direction, 3.5)
        sent = [a for c, a in self.server.calls if c == "device_command"][n:]
        opts = {"optionsMask": 0, "optionsOverride": 0}
        self.assertEqual([(a["node_id"], a["endpoint_id"], a["cluster_id"], a["command_name"], a["payload"]) for a in sent],
                         [(2, 1, 8, "MoveWithOnOff", {"moveMode": 0, "rate": 73, **opts}),
                          (2, 1, 8, "Move", {"moveMode": 1, "rate": 73, **opts}), (2, 1, 8, "Stop", opts)])
        with self.assertRaises(CommandError):
            await m.move(plug, "brightness", 1, 3.5)
        with self.assertRaises(CommandError):
            await m.command(lock, "set_brightness", 5)
        self.server.command_error = (3, "Node 1 is not (yet) available")
        with self.assertRaisesRegex(CommandError, "Coffee plug isn't answering right now"):
            await m.command(plug, "turn_off")
        await self.stop()

    async def test_tagged_buttons_say_where_they_are_and_what_they_do(self):
        # IKEA's BILRESA tags its endpoints (Descriptor TagList): Top + On + Up, Bottom + Off + Down.
        a = self.server.nodes[9]["attributes"]
        a.update({"1/29/4": [{"0": None, "1": 8, "2": 2}, {"0": None, "1": 67, "2": 0}, {"0": None, "1": 67, "2": 3},
                             {"0": None, "1": 67, "2": 8, "3": "button 1"}],
                  "2/29/4": [{"0": None, "1": 8, "2": 3}, {"0": None, "1": 67, "2": 1}, {"0": None, "1": 67, "2": 4}]})
        await self.start()
        remote = self.by_name()["Bedside button"]
        self.assertEqual(remote.attrs["buttons"], [{"id": "button_1", "label": "Top", "role": "on"},
                                                   {"id": "button_2", "label": "Bottom", "role": "off"}])
        await self.stop()

    async def test_untagged_buttons_are_numbered(self):
        await self.start()
        self.assertEqual(self.by_name()["Bedside button"].attrs["buttons"],
                         [{"id": "button_1", "label": "Button 1", "role": None},
                          {"id": "button_2", "label": "Button 2", "role": None}])
        await self.stop()

    async def test_buttons_tell_a_press_from_a_long_press(self):
        a = self.server.nodes[9]["attributes"]
        # Button 1 is what IKEA's BILRESA reports (FeatureMap 30: release, long press, counts up to 2 presses);
        # button 2 only reports its release; button 3 is an older one that only says it was pressed.
        a.update({"1/59/65532": 30, "1/59/2": 2, "2/59/65532": 6, "3/29/0": [{"0": 15, "1": 1}], "3/29/1": [59, 29],
                  "3/59/65532": 2})
        await self.start()
        remote = self.by_name()["Bedside button"]
        self.assertEqual(remote.attrs["events"], ["button_1", "button_1_double", "button_1_long", "button_1_released",
                                                  "button_2", "button_3"])
        events = []

        async def on_event(ent, event, data):
            events.append(event)
        self.hub.subscribe_events(on_event)

        async def send(ep, *evs):
            for ev, data in evs:
                await self.server.event("node_event", {"node_id": 9, "endpoint_id": ep, "cluster_id": 59, "event_id": ev,
                                                       "event_number": 1, "priority": 1, "timestamp": 0,
                                                       "timestamp_type": 1, "data": data})
        down, up = (1, {"newPosition": 1}), (3, {"previousPosition": 1})
        done = lambda n: (6, {"previousPosition": 1, "totalNumberOfPressesCounted": n})  # noqa: E731
        # A short press: nothing as it goes down, "button_1" once the button has counted it.
        await send(1, down, up)
        await asyncio.sleep(0.1)
        self.assertEqual(events, [])
        await send(1, done(1))
        await until(lambda: events == ["button_1"], "short press")
        # Held down, then let go - never a short press too, even if the button counts it afterwards.
        await send(1, down, (2, {"newPosition": 1}), (4, {"previousPosition": 1}), done(1))
        await until(lambda: events[1:] == ["button_1_long", "button_1_released"], "long press")
        await send(1, down, up, down, up, done(2))
        await until(lambda: events[3:] == ["button_1_double"], "double press")
        # Release only: on the release. No release at all: on the press, as Matter buttons always did.
        await send(2, down)
        await send(3, down)
        await send(2, up)
        await until(lambda: events[4:] == ["button_3", "button_2"], "older buttons")
        await asyncio.sleep(0.1)
        self.assertEqual(len(events), 6)
        await self.stop()

    async def test_server_not_running(self):
        await self.server.close()
        self.hub = Hub()
        m = MatterIntegration("matter", "Matter", {"url": self.url}, Context(self.hub, self.data, self.data))
        task = asyncio.create_task(m.run())
        await until(lambda: m.status == "error", "error")
        self.assertIn(f"Can't reach the Matter server at {self.url}: nothing is answering there", m.detail)
        with self.assertRaisesRegex(ValueError, "isn't connected"):
            await m.add_device("34970112332")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class AddRemoveTests(MatterBase):
    async def test_setup_codes(self):
        self.assertEqual(setup_code(" 3497-011-2332 "), "34970112332")
        self.assertEqual(setup_code("mt:y.k9042c00kg0648g00"), "MT:Y.K9042C00KG0648G00")
        with self.assertRaisesRegex(ValueError, "typo"):
            setup_code("3497-011-2333")
        with self.assertRaisesRegex(ValueError, "11 digits"):
            setup_code("1234")

    async def test_adding_with_a_code_then_removing(self):
        m = await self.start()
        res = await m.add_device("3497-011-2332")
        self.assertEqual(res["state"], "working")
        self.assertIn("adding a device", m.detail)
        await until(lambda: m.adding["state"] == "done", "added")
        self.assertEqual([a for c, a in self.server.calls if c == "commission_with_code"],
                         [{"code": "34970112332", "network_only": True}])
        new = self.by_name()["Smart plug P1"]
        self.assertEqual((new.kind, new.state, m.adding["deviceIds"]), ("switch", "off", [new.id]))
        self.assertEqual(m.adding["message"], "Smart plug P1: 1 switch.")
        self.assertEqual(self.saved, [])  # the code is never kept
        dump = repr(m.config) + repr(m.admin_details())
        for code in ("34970112332", "3497-011-2332"):  # the whole code, so a timestamp can't match by chance
            self.assertNotIn(code, dump)
        paired = {d["id"]: d for d in m.admin_details()["pairedDevices"]}
        self.assertEqual((paired["12"]["name"], paired["12"]["network"], paired["12"]["entityIds"]),
                         ("Smart plug P1", "Wi-Fi", [new.id]))
        self.assertEqual(paired["3"]["network"], "Thread")
        # Removing takes it off Newman's fabric and out of Newman.
        await m.remove_device("12")
        self.assertIn(("remove_node", {"node_id": 12}), self.server.calls)
        self.assertIsNone(self.hub.get(new.id))
        self.assertNotIn("12", {d["id"] for d in m.admin_details()["pairedDevices"]})
        with self.assertRaises(KeyError):
            await m.remove_device("12")
        await self.stop()

    async def test_progress_cancel_and_a_new_attempt(self):
        m = await self.start()
        self.server.commission_hold = asyncio.Event()
        res = await m.add_device("34970112332")
        self.assertEqual((res["state"], res["step"], res["title"], res["elapsed"], res["seen"]),
                         ("working", "looking", "Looking for the device and pairing it…", 0, False))
        await until(lambda: any(c == "commission_with_code" for c, _ in self.server.calls), "sent")
        m.adding["startedAt"] -= 42  # it's been going a while
        self.assertEqual(m.admin_details()["adding"]["elapsed"], 42)
        with self.assertRaisesRegex(ValueError, "Cancel it to try another code"):
            await m.add_device("34970112332")
        out = await m.cancel_add()
        self.assertEqual((out["state"], out["seen"]), ("cancelled", True))
        self.assertNotIn("elapsed", out)
        self.assertNotIn("adding a device", m.detail)
        with self.assertRaisesRegex(ValueError, "Nothing is being added"):
            await m.cancel_add()
        # The server finishes the old one after all: its answer is ignored, the device simply shows up.
        self.server.commission_hold.set()
        await until(lambda: "Smart plug P1" in self.by_name(), "late device")
        await asyncio.sleep(0.1)
        self.assertEqual(m.adding["state"], "cancelled")
        # A new attempt starts straight away and its result waits to be seen.
        self.server.commission_hold = None
        await self.server.event("node_removed", 12)
        await until(lambda: "Smart plug P1" not in self.by_name(), "removed")
        await m.add_device("34970112332")
        await until(lambda: m.adding["state"] == "done", "added")
        self.assertIs(m.admin_details()["adding"]["seen"], False)
        m.add_seen()
        self.assertIs(m.admin_details()["adding"]["seen"], True)
        await self.stop()

    async def test_a_stale_working_never_blocks(self):
        m = await self.start()
        long_ago = time.time() - COMMISSION_TIMEOUT_S - 60
        m.adding = {"state": "working", "at": long_ago, "startedAt": long_ago, "step": "looking"}
        await m.add_device("34970112332")
        await until(lambda: m.adding["state"] == "done", "added")
        self.assertEqual(m.adding["message"], "Smart plug P1: 1 switch.")
        await self.stop()

    async def test_why_adding_failed_in_plain_words(self):
        m = await self.start()
        cases = [
            ((1, "PASE session establishment failed: incorrect passcode"), "turned that code down"),
            ((1, "Commissioning failed: no commissionable device discovered"), "about 15 minutes"),
            ((1, "Operation timed out"), "couldn't be reached while adding it"),
            ((8, "Invalid setup payload"), "didn't accept that setup code"),
            ((1, "Fabric table full"), "as many controllers as it can"),
        ]
        for err, words in cases:
            self.server.commission_error = err
            await m.add_device("34970112332")
            await until(lambda: m.adding["state"] == "error", "error")
            self.assertIn(words, m.adding["message"], err)
            if "15 minutes" in words:
                self.assertIn("Apple Home's “Turn On Pairing Mode” code works for about 15 minutes", m.adding["message"])
        await self.stop()


class NoThreadRouteTests(MatterBase):
    route = False

    async def test_status_explains_a_missing_thread_route(self):
        MatterIntegration.proc = fake_proc(self.data / "proc2", route=False, accept_ra=1, forwarding=1)
        m = await self.start()
        gone = copy.deepcopy(self.server.nodes[3])
        gone["available"] = False
        await self.server.event("node_updated", gone)
        await until(lambda: "Thread devices can't be reached" in m.detail, "thread text")
        self.assertIn("no IPv6 route to a Thread network", m.detail)
        self.assertIn("br0 ignores router advertisements (accept_ra is 1 while forwarding is on; it needs to be 2)", m.detail)
        self.server.commission_error = (1, "No commissionable device discovered")
        await m.add_device("34970112332")
        await until(lambda: m.adding["state"] == "error", "error")
        self.assertIn("If it's a Thread device, this computer needs an IPv6 route to the Thread network: right now it has none.",
                      m.adding["message"])
        await self.stop()

    def test_host_ipv6_reads_routes_and_settings(self):
        ok = host_ipv6(fake_proc(self.data / "a"))
        self.assertEqual(ok["routes"], ["fd4a:ad96:cdb9::/64 via fe80::14e9:7353:c34e:52f5 on br0"])
        plen = host_ipv6(fake_proc(self.data / "b", route=False, plen=0))
        self.assertEqual(plen["problems"], ["br0 ignores the routes in router advertisements "
                                            "(accept_ra_rt_info_max_plen is 0; it needs to be 64)"])
        self.assertIsNone(host_ipv6(self.data / "nowhere"))


class HubBase(MatterBase):
    """Through the hub's API and automations, like any other connection."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        object.__setattr__(config, "data_dir", self.data)
        self.h = H(self.data)
        self.plugs = FakeLocks(self.h.integrations._ctx)
        self.h.integrations.instances["fake"] = self.plugs
        self.h.hub.upsert("fake.plug", kind="switch", name="Charger", integration="fake")
        await self.h.hub.update("fake.plug", state="off")
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        self.seed()
        r = await self.client.post("/api/integrations", json={"type": "matter", "config": {"url": self.url}})
        self.iid = (await r.json())["id"]
        self.inst = self.h.integrations.instances[self.iid]
        await until(lambda: self.inst.status == "connected", "connected")

    def seed(self):
        """What the household had before the Matter connection starts (subclasses)."""

    async def asyncTearDown(self):
        await self.h.integrations.stop_all()
        await self.client.close()
        await super().asyncTearDown()


class HubTests(HubBase):
    async def test_catalog_add_and_remove_through_the_api(self):
        cat = {c["type"]: c for c in await (await self.client.get("/api/integrations/catalog")).json()}
        self.assertEqual(cat["matter"]["addByCode"]["label"], "Setup code")
        self.assertEqual([f["key"] for f in cat["matter"]["fields"]], ["url", "check_minutes"])
        r = await self.client.post(f"/api/integrations/{self.iid}/devices", json={"code": "3497-011-2333"})
        self.assertEqual((r.status, "typo" in (await r.json())["error"]), (400, True))
        r = await self.client.post(f"/api/integrations/{self.iid}/devices", json={"code": "3497-011-2332"})
        self.assertEqual((r.status, (await r.json())["adding"]["state"]), (200, "working"))

        async def details():
            rows = await (await self.client.get("/api/integrations")).json()
            return next(i for i in rows if i["id"] == self.iid)
        for _ in range(100):
            if (await details())["adminDetails"]["adding"]["state"] == "done":
                break
            await asyncio.sleep(0.03)
        info = await details()
        self.assertNotIn("3497", repr(info))
        self.assertIs(info["adminDetails"]["adding"]["seen"], False)
        self.assertEqual((await self.client.post(f"/api/integrations/{self.iid}/adding/seen")).status, 200)
        self.assertIs((await details())["adminDetails"]["adding"]["seen"], True)
        r = await self.client.post(f"/api/integrations/{self.iid}/adding/cancel")
        self.assertEqual((r.status, (await r.json())["error"]), (409, "Nothing is being added right now."))
        paired = {d["id"]: d for d in info["adminDetails"]["pairedDevices"]}
        self.assertIn("12", paired)
        r = await self.client.delete(f"/api/integrations/{self.iid}/devices/12")
        self.assertEqual(r.status, 200)
        self.assertIsNone(self.h.hub.get(paired["12"]["entityIds"][0]))
        r = await self.client.delete(f"/api/integrations/{self.iid}/devices/12")
        self.assertEqual(r.status, 404)

    async def test_battery_and_leak_start_automations(self):
        motion = next(e for e in self.h.hub.all() if e.name == "Motion sensor M1")
        leak = next(e for e in self.h.hub.all() if e.name == "Leak sensor L1")
        self.h.engine.save({
            "name": "Sensor battery low", "recipients": [], "notify": {"enabled": False},
            "trigger": {"items": [{"type": "property", "entities": [motion.id], "property": "battery", "op": "below",
                                   "value": 20}]},
            "actions": [{"entities": ["fake.plug"], "command": "turn_on"}]})
        self.h.engine.save({
            "name": "Leak", "recipients": [], "notify": {"enabled": False},
            "trigger": {"items": [{"type": "state", "entities": [leak.id], "to": "wet"}]},
            "actions": [{"entities": ["fake.plug"], "command": "turn_off"}]})
        await self.server.event("attribute_updated", [3, "0/47/12", 44])  # 22 %
        await asyncio.sleep(0.1)
        self.assertEqual(self.plugs.calls, [])
        await self.server.event("attribute_updated", [3, "0/47/12", 36])  # 18 %
        await until(lambda: self.plugs.calls == [("fake.plug", "turn_on")], "battery automation")
        await self.h.hub.update("fake.plug", state="on")  # the plug did it
        await self.server.event("attribute_updated", [4, "1/69/0", True])
        await until(lambda: self.plugs.calls[-1:] == [("fake.plug", "turn_off")], "leak automation")


    async def test_a_device_that_tells_its_unique_id_later_keeps_its_automations(self):
        # The BILRESA button: first seen without its unique id ("node9"), then it said it while asleep the night after.
        auto = self.h.engine.save({
            "name": "Top button", "recipients": [], "notify": {"enabled": False},
            "trigger": {"items": [{"type": "event", "entity": "matter.node9_1_buttons", "event": "button_1"}]},
            "actions": [{"entities": ["fake.plug"], "command": "turn_on"}]})
        later = copy.deepcopy(self.server.nodes[9])
        later["attributes"]["0/40/18"] = "BTN-9"
        await self.server.event("node_updated", later)
        await until(lambda: self.h.hub.get("matter.btn_9_1_buttons") is not None, "the button's new id")
        self.assertIsNone(self.h.hub.get("matter.node9_1_buttons"))
        item = self.h.engine.autos[auto["id"]]["trigger"]["items"][0]
        self.assertEqual(item["entity"], "matter.btn_9_1_buttons")


MERGE_NODES = [
    # A temperature + humidity sensor on a battery (Power Source on the root), like most Thread climate sensors.
    node(21, {1: ([770], [1026, 29], {"1026/0": 1950}), 2: ([775], [1029, 29], {"1029/0": 5125})},
         product="Climate sensor C1", network=THREAD, root={"0/29/1": [29, 40, 47, 49], "0/47/12": 180, "0/47/19": "AAA"}),
    # A leak sensor with its battery on the same endpoint (the id the live one has always had).
    node(22, {1: ([67], [47, 69, 29], {"69/0": False, "47/12": 200, "47/19": "AAA"})}, product="Water leak sensor W1",
         uid="6bc20bc67c9ff1a5b6a335bc47b0c1b1", network=THREAD),
    # Two outlets, each with its own power reading.
    node(23, {1: ([266], [6, 29, 144], {"6/0": True, "144/8": 5000}), 2: ([266], [6, 29, 144], {"6/0": False, "144/8": 7250})},
         label="Desk strip"),
    # A bridge: two climate sensors on one endpoint each, and one whose readings are on endpoints of their own.
    node(24, {1: ([14], [29], {}),
              3: ([770, 19], [1026, 1029, 57, 29], {"57/5": "Attic", "57/18": "BR-ATTIC", "57/17": True,
                                                    "1026/0": 1500, "1029/0": 4000}),
              4: ([770, 19], [1026, 1029, 57, 29], {"57/5": "Basement", "57/18": "BR-BASE", "57/17": True,
                                                    "1026/0": 1700, "1029/0": 6000}),
              5: ([19], [57, 29], {"57/5": "Shed", "57/18": "BR-SHED", "57/17": True, "29/3": [6, 7]}),
              6: ([770], [1026, 29], {"1026/0": 900}), 7: ([775], [1029, 29], {"1029/0": 7000})}, label="Hub bridge"),
    NODES[2],  # the motion sensor that measures light
]


class MergeTests(MatterBase):
    """One Newman device per physical device for its readings; switchable things stay one each."""
    nodes = MERGE_NODES

    def of_node(self, nid):
        return [self.hub.get(eid) for eid in self.m._by_node[nid]]

    async def test_readings_of_one_device_are_one_device(self):
        await self.start()
        [climate] = self.of_node(21)
        props = {p["id"]: p for p in properties_for(climate)}
        self.assertEqual((climate.id, climate.name, climate.kind, climate.state),
                         ("matter.node21_1_temperature", "Climate sensor C1", "temperature", "19.5"))
        self.assertEqual({k: (props[k]["value"], props[k]["unit"]) for k in ("temperature", "humidity", "battery")},
                         {"temperature": ("19.5", "°C"), "humidity": (51.25, "%"), "battery": (90, "%")})
        self.assertEqual(climate.attrs["batteryType"], "AAA")
        await self.server.event("attribute_updated", [21, "2/1029/0", 6033])
        await until(lambda: self.hub.get(climate.id).attrs["humidity"] == 60.33, "humidity update")
        [motion] = self.of_node(3)
        props = {p["id"]: p for p in properties_for(motion)}
        self.assertEqual((motion.kind, motion.name, props["illuminance"]["value"], props["battery"]["value"]),
                         ("motion", "Motion sensor M1", 10, 80))
        await self.server.event("attribute_updated", [3, "2/1024/0", 20001])
        await until(lambda: self.hub.get(motion.id).attrs["illuminance"] == 100, "light level update")
        await self.stop()

    async def test_leak_sensor_keeps_its_id(self):
        await self.start()
        [leak] = self.of_node(22)
        self.assertEqual((leak.id, leak.kind, leak.state, leak.attrs["batteryLevel"]),
                         ("matter.6bc20bc67c9ff1a5b6a335bc47b0c1b1_1_boolean", "leak", "dry", 100))
        await self.server.event("attribute_updated", [22, "1/69/0", True])
        await until(lambda: self.hub.get(leak.id).state == "wet", "leak")
        await self.stop()

    async def test_each_outlet_and_each_bridged_device_is_its_own(self):
        await self.start()
        strip = {e.name: e for e in self.of_node(23)}
        self.assertEqual({n: (e.id, e.kind, e.attrs["power"]) for n, e in strip.items()},
                         {"Desk strip 1": ("matter.node23_1_onoff", "switch", 5),
                          "Desk strip 2": ("matter.node23_2_onoff", "switch", 7.25)})
        bridged = {e.name: e for e in self.of_node(24)}
        self.assertEqual({n: (e.id, e.kind, e.state, e.attrs["humidity"]) for n, e in bridged.items()},
                         {"Attic": ("matter.br_attic_temperature", "temperature", "15", 40),
                          "Basement": ("matter.br_base_temperature", "temperature", "17", 60),
                          "Shed": ("matter.node24_6_temperature", "temperature", "9", 70)})
        await self.server.event("attribute_updated", [24, "5/57/17", False])  # the bridge lost the shed
        await until(lambda: not self.hub.get(bridged["Shed"].id).available, "shed offline")
        self.assertTrue(self.hub.get(bridged["Attic"].id).available)
        await self.stop()


class MigrationTests(HubBase):
    """An older Newman made one device per reading: what used those moves to the device they're part of."""

    def seed(self):
        user = self.h.store.users()[0]["id"]
        # The old devices as an older Newman had them, while the automation was made; gone after a restart.
        for eid, unit in (("matter.node6_2_humidity", "%"), ("matter.node3_2_illuminance", "lx")):
            self.h.hub.upsert(eid, kind="sensor", name=eid, integration="matter")
            self.h.hub._entities[eid] = replace(self.h.hub.get(eid), state="50", attrs={"unitOfMeasurement": unit})
        self.auto = self.h.engine.save({
            "name": "Damp patio", "recipients": [], "notify": {"enabled": False},
            "trigger": {"items": [{"type": "property", "entities": ["matter.node6_2_humidity"], "property": "value",
                                   "op": "above", "value": 60}]},
            "actions": [{"entities": ["fake.plug"], "command": "turn_on"}]})
        self.dash = self.h.store.save_dashboard(user, {"name": "Wall", "theme": "night-glass", "pages": [
            {"id": "p1", "name": "Home", "icon": "home", "tiles": [
                {"id": "t1", "type": "values", "x": 0, "y": 0, "w": 4, "h": 3, "config": {"items": [
                    {"entity": "matter.node6_2_humidity", "property": "value"},
                    {"entity": "matter.node3_2_illuminance", "property": "value"}]}}]}]})
        for eid, name in (("matter.node6_2_humidity", "Deck sensor"), ("matter.node3_2_illuminance", "Hall lux"),
                          ("matter.node3_1_motion", "Hall motion")):
            self.h.store.rename_entity(eid, name)
        self.h.hub.set_name_overrides(self.h.store.entity_names())
        for eid in ("matter.node6_2_humidity", "matter.node3_2_illuminance"):
            self.h.hub._entities.pop(eid)  # Newman restarted (with this version)

    async def test_old_per_reading_devices_hand_over(self):
        hub = self.h.hub
        self.assertIsNone(hub.get("matter.node6_2_humidity"))
        self.assertIsNone(hub.get("matter.node3_2_illuminance"))
        patio, motion = hub.get("matter.node6_1_temperature"), hub.get("matter.node3_1_motion")
        # The name given to the old humidity sensor goes to the device (it had none); the motion sensor keeps its own.
        self.assertEqual((patio.name, motion.name), ("Deck sensor", "Hall motion"))
        item = self.h.engine.autos[self.auto["id"]]["trigger"]["items"][0]
        self.assertEqual((item["entities"], item["property"]), (["matter.node6_1_temperature"], "humidity"))
        tiles = self.h.store.dashboard(self.dash["id"])["pages"][0]["tiles"]
        self.assertEqual(tiles[0]["config"]["items"], [{"entity": "matter.node6_1_temperature", "property": "humidity"},
                                                       {"entity": "matter.node3_1_motion", "property": "illuminance"}])
        # And the automation still fires on the humidity, now reported by the patio sensor.
        await self.server.event("attribute_updated", [6, "2/1029/0", 5900])
        await asyncio.sleep(0.1)
        self.assertEqual(self.plugs.calls, [])
        await self.server.event("attribute_updated", [6, "2/1029/0", 6500])
        await until(lambda: self.plugs.calls == [("fake.plug", "turn_on")], "humidity automation")

class SetupCodeCheckTests(unittest.TestCase):
    def test_real_setup_codes_pass_the_check(self):
        from newman.integrations.matter import setup_code
        # The Matter spec's example and a real Apple Home pairing code (the check-digit table had a wrong row).
        self.assertEqual(setup_code("3497-011-2332"), "34970112332")
        self.assertEqual(setup_code("2195 780 2517"), "21957802517")
        with self.assertRaises(ValueError):
            setup_code("21957802518")
