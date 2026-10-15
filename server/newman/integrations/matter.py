"""Matter devices (Wi-Fi, Ethernet or Thread), through the Open Home Foundation's Matter server.

Newman doesn't speak Matter itself. The open-source Matter server (matterjs-server,
github.com/matter-js/matterjs-server, maintained by the matter.js team for the Open Home Foundation)
runs as its own container on the host network and holds Newman's own Matter fabric - the keys that
make Newman one of a device's controllers. Newman talks to it over its websocket API (the one
python-matter-server introduced; matterjs-server is its drop-in successor), so no Matter library is
needed here.

Devices are added with a setup code: a new device's own code, or the one-time code another controller
makes when it shares a device (Apple Home: "Turn On Pairing Mode"; Google Home: "Linked Matter apps &
services"). Newman asks the server to add it over the network (multi-admin), then forgets the code.
Removing a device takes it off Newman's fabric only - other controllers keep it.

What each endpoint becomes is read from Matter's own metadata, never from brand or model names:
its device types (Matter's device library: "On/Off Plug-in Unit", "Water Leak Detector"…) decide the
kind, and the clusters it serves decide what's read (OnOff, OccupancySensing, BooleanState,
measurements, PowerSource for batteries, electrical power and energy). Each physical device (a node, or
each device behind a bridge) is one Newman device for its readings: a temperature + humidity sensor is
one temperature sensor reporting humidity and battery too, and a motion sensor that measures light is
one motion sensor with a light level. Things that can be switched or moved (each outlet of a power
strip, a lock, a blind) stay devices of their own.

Thread devices are reached over IPv6 through a Thread border router (an Apple TV, HomePod, Nest hub):
the host needs a route to the Thread network's prefix, which it learns from the border router's
router advertisements. The connection's status says so in plain words when that route is missing.
"""
from __future__ import annotations

import asyncio
import ipaddress
import itertools
import logging
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiohttp

from ..kinds import KINDS
from ._common import TaskKeeper, clean_id, device_summary, plural
from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

# -- Matter metadata --------------------------------------------------------------------------------

# Cluster ids (Matter application cluster specification).
DESCRIPTOR, BASIC, BRIDGED, POWER_SOURCE, NETWORK = 0x1D, 0x28, 0x39, 0x2F, 0x31
ON_OFF, LEVEL, DOOR_LOCK, COVER, THERMOSTAT, FAN = 0x06, 0x08, 0x101, 0x102, 0x201, 0x202
BOOLEAN, OCCUPANCY, TEMPERATURE, HUMIDITY, ILLUMINANCE, SMOKE_CO = 0x45, 0x406, 0x402, 0x405, 0x400, 0x5C
SWITCH, POWER, ENERGY = 0x3B, 0x90, 0x91
# Switch cluster (buttons): FeatureMap bits - it reports a release, a long press, counts presses - and events.
SW_RELEASE, SW_LONG, SW_MULTI = 0x04, 0x08, 0x10
INITIAL_PRESS, LONG_PRESS, SHORT_RELEASE, LONG_RELEASE, MULTI_PRESS_COMPLETE = 1, 2, 3, 4, 6

# Matter device types -> (role, Newman kind). The role decides which cluster is read and how; the kind is
# what Newman shows. Types not listed fall back to the clusters the endpoint serves (CLUSTER_ROLES).
DEVICE_TYPES: dict[int, tuple[str, str]] = {
    0x0100: ("onoff", "light"),         # On/Off Light
    0x0101: ("onoff", "light"),         # Dimmable Light
    0x010C: ("onoff", "light"),         # Color Temperature Light
    0x010D: ("onoff", "light"),         # Extended Color Light
    0x010A: ("onoff", "switch"),        # On/Off Plug-in Unit
    0x010B: ("onoff", "switch"),        # Dimmable Plug-in Unit
    0x0110: ("onoff", "switch"),        # Mounted On/Off Control
    0x0111: ("onoff", "switch"),        # Mounted Dimmable Load Control
    0x000A: ("lock", "lock"),           # Door Lock
    0x0202: ("cover", "cover"),         # Window Covering
    0x0301: ("thermostat", "thermostat"),
    0x002B: ("fan", "fan"),
    0x0015: ("boolean", "contact"),     # Contact Sensor
    0x0043: ("boolean", "leak"),        # Water Leak Detector
    0x0041: ("boolean", "binary"),      # Water Freeze Detector
    0x0044: ("boolean", "binary"),      # Rain Sensor
    0x0107: ("motion", "motion"),       # Occupancy Sensor
    0x0302: ("temperature", "temperature"),
    0x0307: ("humidity", "sensor"),     # Humidity Sensor
    0x0106: ("illuminance", "sensor"),  # Light Sensor
    0x0076: ("smoke", "smoke"),         # Smoke CO Alarm
    0x000F: ("buttons", "remote"),      # Generic Switch (buttons)
}
# Endpoints that are structure, not devices: Root Node, Aggregator (a bridge), Power Source, Electrical Sensor.
STRUCTURE_TYPES = {0x0016, 0x000E, 0x0011, 0x0510}
# Device types that aren't in DEVICE_TYPES: what each server cluster means. Outputs: the first one wins.
OUTPUT_ROLES = [(DOOR_LOCK, "lock", "lock"), (COVER, "cover", "cover"), (THERMOSTAT, "thermostat", "thermostat"),
                (FAN, "fan", "fan"), (ON_OFF, "onoff", "switch")]
SENSOR_ROLES = [(BOOLEAN, "boolean", "binary"), (OCCUPANCY, "motion", "motion"),
                (TEMPERATURE, "temperature", "temperature"), (HUMIDITY, "humidity", "sensor"),
                (ILLUMINANCE, "illuminance", "sensor"), (SMOKE_CO, "smoke", "smoke"), (SWITCH, "buttons", "remote")]
ROLE_CLUSTER = {"onoff": ON_OFF, "lock": DOOR_LOCK, "cover": COVER, "thermostat": THERMOSTAT, "fan": FAN,
                "boolean": BOOLEAN, "motion": OCCUPANCY, "temperature": TEMPERATURE, "humidity": HUMIDITY,
                "illuminance": ILLUMINANCE, "smoke": SMOKE_CO, "buttons": SWITCH, "power": POWER}
OUTPUTS = {"onoff", "lock", "cover", "thermostat", "fan"}
# Readings: each one's unit and name (the device class on a "sensor" device, the attribute when it rides along
# on another device). In priority order: one physical device's readings are one Newman device, and the first
# of these it has is its main reading (a temperature + humidity sensor is a temperature sensor with humidity).
READINGS = {"temperature": ("°C", "temperature"), "humidity": ("%", "humidity"), "illuminance": ("lx", "illuminance"),
            "power": ("W", "power")}
# On/off sensors with kinds of their own: a physical device with exactly one of these is that device, and its
# readings (light level, temperature…) ride along on it.
DETECTORS = {"boolean", "motion", "smoke"}
WORDS = {"temperature": "temperature", "humidity": "humidity", "illuminance": "light level", "motion": "motion",
         "power": "power", "buttons": "buttons"}
LOCK_STATES = {1: "LOCKED", 2: "UNLOCKED", 3: "UNLOCKED", 0: "JAMMED"}  # 0 = "not fully locked"
THERMOSTAT_MODES = {0: "off", 1: "auto", 3: "cool", 4: "heat"}
NETWORKS = {1: "Wi-Fi", 2: "Thread", 4: "Ethernet"}  # Network Commissioning feature bits
# The server's error codes (websocket API).
COMMISSION_FAILED, NODE_NOT_READY, NODE_NOT_RESOLVING, NODE_NOT_EXISTS, INVALID_ARGS, ICD_MULTI_ADMIN = 1, 3, 4, 5, 8, 100
MIN_SCHEMA = 11
COMMISSION_TIMEOUT_S = 300.0
APPLE_WINDOW = "Apple Home's “Turn On Pairing Mode” code works for about 15 minutes"


def _p(ep: int, cluster: int, attr: int) -> str:
    return f"{ep}/{cluster}/{attr}"


def _field(struct: Any, num: int, name: str) -> Any:
    """A Matter struct's field, whichever way the server spells it (by field id or by name)."""
    if not isinstance(struct, dict):
        return None
    return struct.get(str(num), struct.get(name))


def _num(value: Any, scale: float = 1) -> float | int | None:
    """A reported number in its real unit, unrounded (whole numbers stay whole)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = value / scale
    return int(out) if float(out).is_integer() else out


def _text(value: float | int) -> str:
    return f"{value:.12g}"


# -- setup codes ----------------------------------------------------------------------------------

_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
      [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
      [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
      [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
      [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
      [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def setup_code(text: Any) -> str:
    """A Matter setup code as the server wants it: the 11 or 21 digits of a manual code (its last digit is
    a check digit, so a typo is caught here) or the MT:… text of a QR code. Raises ValueError, in words."""
    raw = str(text or "").strip()
    if raw.upper().startswith("MT:"):
        body = raw[3:].strip().upper()
        if not re.fullmatch(r"[0-9A-Z.\-]{10,}", body):
            raise ValueError("That QR text isn't complete: it starts with MT: followed by letters, digits, dots and dashes.")
        return "MT:" + body
    digits = re.sub(r"[\s\-]", "", raw)
    if not digits.isdigit() or len(digits) not in (11, 21):
        raise ValueError("A Matter setup code is 11 digits (sometimes 21), like 3497-011-2332, or QR text starting with MT:.")
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _D[c][_P[i % 8][int(ch)]]
    if c:
        raise ValueError("That code has a typo: its digits don't add up. Check each digit against the code you were given.")
    return digits


# -- the host's IPv6 view (Thread devices are reached through a border router's route) -------------

def host_ipv6(proc: Path = Path("/proc")) -> dict[str, Any] | None:
    """What this computer's network looks like to Thread devices: {"routes": ["fd…::/64 via fe80::…"],
    "problems": [plain words]}; None when it can't be read (not Linux). Newman shares the host's
    network (like the Matter server), so /proc shows the host's."""
    try:
        lines = (proc / "net" / "ipv6_route").read_text().splitlines()
    except OSError:
        all_off = proc / "sys" / "net" / "ipv6" / "conf" / "all" / "disable_ipv6"
        if all_off.is_file():
            return {"routes": [], "problems": ["IPv6 is turned off on this computer"]}
        return None
    routes = []
    for line in lines:
        f = line.split()
        if len(f) < 10:
            continue
        dest, plen, hop, flags, iface = f[0], int(f[1], 16), f[4], int(f[8], 16), f[9]
        # A unique-local /64 reached through a router (a border router announces the Thread network this way).
        if dest[:2].lower() in ("fc", "fd") and plen == 64 and flags & 0x2 and hop.strip("0"):
            routes.append(f"{_v6(dest)}/64 via {_v6(hop)} on {iface}")
    problems: list[str] = []
    if not routes:
        conf = proc / "sys" / "net" / "ipv6" / "conf"

        def val(iface: str, key: str) -> int | None:
            try:
                return int((conf / iface / key).read_text().strip())
            except (OSError, ValueError):
                return None
        if val("all", "disable_ipv6") == 1:
            problems.append("IPv6 is turned off on this computer")
        else:
            for iface in sorted(p.name for p in conf.iterdir()) if conf.is_dir() else []:
                if iface in ("all", "default", "lo") or iface.startswith(("docker", "veth", "br-", "virbr", "vnet", "tun", "wg")):
                    continue
                if val(iface, "disable_ipv6") == 1:
                    continue
                ra, fwd, plen = val(iface, "accept_ra"), val(iface, "forwarding"), val(iface, "accept_ra_rt_info_max_plen")
                if ra == 0 or (ra == 1 and fwd == 1):
                    problems.append(f"{iface} ignores router advertisements (accept_ra is {ra}"
                                    f"{' while forwarding is on' if fwd == 1 else ''}; it needs to be 2)")
                elif plen is not None and plen < 64:
                    problems.append(f"{iface} ignores the routes in router advertisements "
                                    f"(accept_ra_rt_info_max_plen is {plen}; it needs to be 64)")
    return {"routes": routes, "problems": problems}


def _v6(hexstr: str) -> str:
    try:
        return str(ipaddress.IPv6Address(bytes.fromhex(hexstr)))
    except ValueError:
        return hexstr


class MatterError(Exception):
    def __init__(self, code: int, details: str) -> None:
        super().__init__(details)
        self.code, self.details = code, details


# -- the connection -----------------------------------------------------------------------------------

class MatterIntegration(Integration):
    type = "matter"
    name = "Matter"
    category = "other"
    provides = ("switch", "light", "motion", "leak", "contact", "temperature", "sensor", "lock", "cover",
                "thermostat", "fan", "smoke", "binary", "remote")
    description = ("Matter plugs, lights, sensors, locks and blinds of any brand - Wi-Fi, Ethernet or Thread - "
                   "through the open-source Matter server running next to Newman. Add devices with their setup "
                   "code, including ones shared from Apple Home or Google Home.")
    fields = [
        Field("url", "Matter server address", required=True, default="ws://127.0.0.1:5580/ws",
              placeholder="ws://127.0.0.1:5580/ws",
              help="The Matter server's websocket. It runs on this computer's network, so 127.0.0.1 unless it's elsewhere."),
    ]
    add_by_code = {
        "label": "Setup code",
        "placeholder": "3497-011-2332 or MT:Y.K90…",
        "help": ("The device's 11-digit setup code or its QR text (MT:…). For a device that's already in Apple Home: "
                 "open it there, Settings, Turn On Pairing Mode, and use the code it shows within about 15 minutes. "
                 "Google Home: the device's settings, Linked Matter apps & services, Link apps & services."),
    }
    extra_config_keys = ("pairing_code",)  # an older Newman added devices through this setting

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._tasks = TaskKeeper()
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ids = itertools.count(1)
        self._pending: dict[str, tuple[asyncio.Future, Callable[[Any], Awaitable[None]] | None]] = {}
        self._server: dict[str, Any] = {}
        self._nodes: dict[int, dict[str, Any]] = {}       # node id -> {"available", "attributes"}
        self._specs: dict[str, dict[str, Any]] = {}       # entity id -> what it reads
        self._by_node: dict[int, list[str]] = {}
        # (node, endpoint) -> (remote id, its event, what the button can tell apart - see _button)
        self._buttons: dict[tuple[int, int], tuple[str, str, dict[str, bool]]] = {}
        self._held: set[tuple[int, int]] = set()          # buttons in a long press (since its LongPress)
        self._moved: set[str] = set()                     # old per-reading device ids already handed over
        self.adding: dict[str, Any] = {}                  # the last "add a device": {"state", "message", …}
        self._attempt = 0                                 # which "add a device" counts; older ones are ignored
        self._add_task: asyncio.Task | None = None

    proc = Path("/proc")  # where the host's network is read (Newman shares the host's network); tests use a fake

    @classmethod
    def migrate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in config.items() if k != "pairing_code"}  # codes are used once, never kept

    # -- running ----------------------------------------------------------------------------------

    async def run(self) -> None:
        url = str(self.config.get("url") or "").strip()
        attempt = 0
        async with aiohttp.ClientSession() as http:
            while True:
                try:
                    async with http.ws_connect(url, heartbeat=30, max_msg_size=0) as ws:
                        info = await ws.receive_json(timeout=20)
                        if not isinstance(info, dict) or "schema_version" not in info:
                            raise ConnectionError("what answered there isn't a Matter server")
                        if int(info.get("schema_version") or 0) < MIN_SCHEMA:
                            raise ConnectionError(f"that Matter server is too old (API version {info.get('schema_version')}; "
                                                  f"Newman needs {MIN_SCHEMA} or newer)")
                        self._server, self._ws = info, ws
                        reader = asyncio.create_task(self._read(ws))
                        try:
                            await self._call("start_listening", timeout=120, on_result=self._load)
                            attempt = 0
                            await reader
                        finally:
                            reader.cancel()
                            await asyncio.gather(reader, return_exceptions=True)
                            self._disconnected()
                    raise ConnectionError("the Matter server closed the connection")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._disconnected()
                    log.info("Matter %s: %s: %s", url, e.__class__.__name__, e)
                    self.set_status("error", self._unreachable(url, e))
                    for eid in self._specs:
                        await self.hub.update(eid, available=False)
                attempt += 1
                await self.backoff_sleep(attempt)

    @staticmethod
    def _unreachable(url: str, e: Exception) -> str:
        if isinstance(e, aiohttp.ClientConnectorError):
            why = "nothing is answering there"
        elif isinstance(e, aiohttp.WSServerHandshakeError):
            why = f"it answered, but not as a websocket ({e.status})"
        elif isinstance(e, (asyncio.TimeoutError, TimeoutError)):
            why = "it didn't answer in time"
        elif isinstance(e, aiohttp.InvalidURL) or not url.startswith(("ws://", "wss://")):
            why = "that isn't a websocket address (ws://…)"
        else:
            why = str(e) or e.__class__.__name__
        return f"Can't reach the Matter server at {url}: {why}. Retrying."

    def _disconnected(self) -> None:
        self._ws = None
        for fut, _cb in self._pending.values():
            if not fut.done():
                fut.set_exception(MatterError(-1, "the Matter server connection closed"))
        self._pending.clear()
        if self.adding.get("state") == "working":
            self._attempt += 1  # whatever that attempt still says is too late
            self._finish("error", "The connection to the Matter server dropped while adding the device. Try again.")

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                break
            try:
                data = msg.json()
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            if "event" in data:
                try:
                    await self._on_event(str(data["event"]), data.get("data"))
                except Exception:
                    log.exception("Matter: handling %s failed", data.get("event"))
                continue
            pending = self._pending.get(str(data.get("message_id")))
            if not pending:
                continue
            fut, cb = pending
            if "error_code" in data:
                if not fut.done():
                    fut.set_exception(MatterError(int(data.get("error_code") or 0), str(data.get("details") or "")))
                continue
            try:
                if cb:
                    await cb(data.get("result"))  # before the next message, so no event overtakes it
                if not fut.done():
                    fut.set_result(data.get("result"))
            except Exception as e:
                if not fut.done():
                    fut.set_exception(e)

    async def _call(self, command: str, args: dict[str, Any] | None = None, timeout: float = 30.0,
                    on_result: Callable[[Any], Awaitable[None]] | None = None) -> Any:
        ws = self._ws
        if ws is None or ws.closed:
            raise MatterError(-1, "the Matter server isn't connected")
        mid = str(next(self._ids))
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[mid] = (fut, on_result)
        try:
            await ws.send_json({"message_id": mid, "command": command, "args": args or {}})
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(mid, None)

    async def _load(self, nodes: Any) -> None:
        seen = set()
        for node in nodes or []:
            if isinstance(node, dict) and isinstance(node.get("node_id"), int):
                seen.add(node["node_id"])
                await self._setup_node(node)
        for nid in [n for n in self._nodes if n not in seen]:  # removed while Newman was away
            self._drop_node(nid)
        self._refresh_status()

    async def _on_event(self, event: str, data: Any) -> None:
        if event == "attribute_updated" and isinstance(data, list) and len(data) == 3:
            await self._on_attribute(int(data[0]), str(data[1]), data[2])
        elif event in ("node_added", "node_updated") and isinstance(data, dict):
            if event == "node_added" and self.adding.get("state") == "working":
                self._step("adding")  # paired: what's left is Newman setting it up
            await self._setup_node(data)
            self._refresh_status()
        elif event == "node_removed":
            nid = data.get("node_id") if isinstance(data, dict) else data
            if isinstance(nid, int):
                self._drop_node(nid)
                self._refresh_status()
        elif event == "node_event" and isinstance(data, dict):
            if data.get("cluster_id") == SWITCH:
                await self._on_button(data)
        elif event == "server_shutdown" and self._ws is not None:
            await self._ws.close()

    async def _on_button(self, data: dict[str, Any]) -> None:
        """A Matter button's event -> its remote's event. A button that reports its release fires "button_N"
        then (ShortRelease; MultiPressComplete after one press when it counts presses, two = "_double"), so
        a long press never starts with a short one: that's "_long" (LongPress) and "_released" (LongRelease).
        A button that can't tell any of that fires "button_N" on InitialPress, as it always did."""
        key = (data.get("node_id"), data.get("endpoint_id"))
        found = self._buttons.get(key)
        if not found:
            return
        eid, base, can = found
        ev, out = data.get("event_id"), None
        if ev == INITIAL_PRESS:
            self._held.discard(key)
            out = None if can["release"] or can["multi"] else base
        elif ev == LONG_PRESS and can["long"]:
            self._held.add(key)
            out = f"{base}_long"
        elif ev == LONG_RELEASE and can["long"]:
            out = f"{base}_released"  # still held: a MultiPressComplete after a long press isn't a press
        elif ev == SHORT_RELEASE and can["release"] and not can["multi"]:
            out = base
        elif ev == MULTI_PRESS_COMPLETE and can["multi"] and key not in self._held:
            n = _field(data.get("data"), 1, "totalNumberOfPressesCounted")
            n = n if isinstance(n, int) and not isinstance(n, bool) else 1
            out = base if n == 1 else f"{base}_double" if n == 2 and can["double"] else None
        if out:
            await self.hub.emit(eid, out, {})

    # -- what a node is ------------------------------------------------------------------------------

    @staticmethod
    def _endpoints(values: dict[str, Any]) -> list[int]:
        return sorted({int(p.split("/")[0]) for p in values if p.split("/")[0].isdigit()})

    @staticmethod
    def _device_types(values: dict[str, Any], ep: int) -> list[int]:
        out = []
        for d in values.get(_p(ep, DESCRIPTOR, 0)) or []:
            t = _field(d, 0, "deviceType")
            if isinstance(t, int):
                out.append(t)
        return out

    @staticmethod
    def _button(values: dict[str, Any], ep: int) -> dict[str, bool]:
        """What one button (a Switch cluster endpoint) can tell apart, from its FeatureMap: a release, a long
        press, presses counted - and a double press when it counts at least two (MultiPressMax)."""
        f = values.get(_p(ep, SWITCH, 65532))
        f = f if isinstance(f, int) else 0
        most = values.get(_p(ep, SWITCH, 2))
        return {"release": bool(f & SW_RELEASE), "long": bool(f & SW_LONG), "multi": bool(f & SW_MULTI),
                "double": bool(f & SW_MULTI) and isinstance(most, int) and most >= 2}

    @staticmethod
    def _bridged(values: dict[str, Any], ep: int) -> bool:
        return any(_p(ep, BRIDGED, a) in values for a in (1, 3, 5, 17, 18))

    def _owner(self, values: dict[str, Any], ep: int) -> int:
        """Which physical device an endpoint is part of: a device behind a bridge (the endpoint with its
        Bridged Device Basic Information; its other endpoints are in that one's parts list), else the
        node itself (0)."""
        if self._bridged(values, ep):
            return ep
        for e in self._endpoints(values):
            if e != ep and self._bridged(values, e) and ep in (values.get(_p(e, DESCRIPTOR, 3)) or []):
                return e
        return 0

    @classmethod
    def _info(cls, values: dict[str, Any], ep: int) -> dict[str, Any]:
        """Name, model, ids: from the bridged device's own info when the endpoint is behind a bridge."""
        bridged = cls._bridged(values, ep)
        src, cluster = (ep, BRIDGED) if bridged else (0, BASIC)
        g = lambda a: values.get(_p(src, cluster, a))  # noqa: E731
        label = str(g(5) or "").strip()
        product = str(g(20) or g(3) or "").strip()
        return {"name": label or product, "model": product, "manufacturer": g(1), "firmware": g(10), "serial": g(15),
                "unique": g(18) or g(15), "bridged": bridged, "reachable": g(17) if bridged else None}

    @staticmethod
    def _network(values: dict[str, Any]) -> str:
        features = 0
        for path, v in values.items():
            if path.endswith(f"/{NETWORK}/65532") and isinstance(v, int):
                features |= v
        return next((name for bit, name in NETWORKS.items() if features & bit), "")

    def _plan(self, values: dict[str, Any]) -> list[dict[str, Any]]:
        """What Newman devices a node is: one spec per (endpoint, role)."""
        plan: list[dict[str, Any]] = []
        meters: list[int] = []
        for ep in self._endpoints(values):
            types = self._device_types(values, ep)
            servers = set(values.get(_p(ep, DESCRIPTOR, 1)) or [])
            if ep == 0 and not (servers & set(ROLE_CLUSTER.values())):
                continue
            if POWER in servers or ENERGY in servers:
                meters.append(ep)
            roles: dict[str, str] = {}
            for t in types:
                role, kind = DEVICE_TYPES.get(t, ("", ""))
                if role and ROLE_CLUSTER[role] in servers and role not in roles:
                    roles[role] = kind
            if not roles and types and all(t in STRUCTURE_TYPES for t in types):
                continue
            if not roles.keys() & OUTPUTS:
                for cluster, role, kind in OUTPUT_ROLES:
                    if cluster in servers:
                        roles[role] = "light" if role == "onoff" and LEVEL in servers else kind
                        break
            for cluster, role, kind in SENSOR_ROLES:
                if cluster in servers and role not in roles and not (role == "temperature" and "thermostat" in roles):
                    roles[role] = kind
            for role, kind in roles.items():
                spec: dict[str, Any] = {"ep": ep, "role": role, "kind": kind}
                if role == "onoff":
                    spec["dimmable"] = LEVEL in servers
                if role == "buttons" and any(s["role"] == "buttons" for s in plan):
                    next(s for s in plan if s["role"] == "buttons")["buttons"].append(ep)
                    continue
                if role == "buttons":
                    spec["buttons"] = [ep]
                plan.append(spec)
        # Electrical power / energy: on the plug itself, or on its own endpoint next to the only output.
        outputs = [s for s in plan if s["role"] == "onoff"]
        for ep in meters:
            own = next((s for s in outputs if s["ep"] == ep), None)
            target = own or (outputs[0] if len(outputs) == 1 else None)
            if target:
                target["meter"] = ep
            else:
                plan.append({"ep": ep, "role": "power", "kind": "sensor"})
        for s in plan:
            if s["role"] in READINGS and s["kind"] == "sensor":
                s["unit"], s["device_class"] = READINGS[s["role"]]
        return plan

    def _merge(self, values: dict[str, Any], plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """One Newman device per physical device for its readings: they join its only on/off sensor (motion,
        leak, door…) when it has exactly one, else its main reading (READINGS order). Each merged reading is
        kept as {"ep", "role", "kind"} in the host's "extras" (its kind: what it was on its own, for moving
        what used it). A second reading of the same kind (two probes) stays a device of its own."""
        groups: dict[int, list[dict[str, Any]]] = {}
        for s in plan:
            groups.setdefault(self._owner(values, s["ep"]), []).append(s)
        merged: set[int] = set()
        for specs in groups.values():
            firsts: dict[str, dict[str, Any]] = {}
            for s in specs:
                if s["role"] in READINGS and s["role"] not in firsts:
                    firsts[s["role"]] = s
            readings = [firsts[r] for r in READINGS if r in firsts]
            detectors = [s for s in specs if s["role"] in DETECTORS]
            host = detectors[0] if len(detectors) == 1 else readings[0] if readings else None
            extras = [s for s in readings if s is not host]
            if host is None or not extras:
                continue
            host["extras"] = [{"ep": s["ep"], "role": s["role"], "kind": s["kind"]} for s in extras]
            merged.update(id(s) for s in extras)
        return [s for s in plan if id(s) not in merged]

    def _local(self, values: dict[str, Any], nid: int, spec: dict[str, Any]) -> str:
        """A spec's own device id (within this connection) - the same one it has always had."""
        info = self._info(values, spec["ep"])
        unique = clean_id(info["unique"]) or f"node{nid}"
        return f"{unique}_{spec['role']}" if info["bridged"] else f"{unique}_{spec['ep']}_{spec['role']}"

    @staticmethod
    def _moved_properties(old_kind: str, new_kind: str, role: str) -> dict[str, str]:
        """A reading that was a device of its own and now rides along on another: what its values are
        called there ({"value": "humidity"}), so an automation on the old device keeps working."""
        def pid(kind_id: str, attr: str) -> str:
            return next((p.id for p in KINDS[kind_id].properties if p.attr == attr), attr)
        out = {}
        for old_attr, new_attr in (("", READINGS[role][1]), ("batteryLevel", "batteryLevel"), ("batteryLow", "batteryLow")):
            old, new = pid(old_kind, old_attr), pid(new_kind, new_attr)
            if old and old != new:
                out[old] = new
        return out

    def _battery(self, values: dict[str, Any]) -> dict[str, Any]:
        """The node's battery (Power Source cluster): level %, and what to replace it with."""
        out: dict[str, Any] = {}
        low: bool | None = None
        for path, v in sorted(values.items()):
            ep, cluster, attr = (path.split("/") + ["", "", ""])[:3]
            if cluster != str(POWER_SOURCE):
                continue
            if attr == "12" and (pct := _num(v, 2)) is not None:   # BatPercentRemaining, in half-percent steps
                out.setdefault("batteryLevel", pct)
            elif attr == "14" and isinstance(v, int):              # BatChargeLevel: 0 ok, 1 warning, 2 critical
                low = bool(low) or v in (1, 2)
            elif attr == "15" and isinstance(v, bool):             # BatReplacementNeeded
                low = bool(low) or v
            elif attr == "19" and isinstance(v, str) and v.strip():  # BatReplacementDescription: "CR2032"
                count = values.get(f"{ep}/{POWER_SOURCE}/25")       # BatQuantity
                out.setdefault("batteryType", f"{v.strip()} × {count}" if isinstance(count, int) and count > 1 else v.strip())
        if low is not None:
            out["batteryLow"] = low
        return out

    async def _setup_node(self, node: dict[str, Any]) -> None:
        nid = node["node_id"]
        values = dict(node.get("attributes") or {})
        self._nodes[nid] = {"available": bool(node.get("available", True)), "attributes": values}
        before = set(self._by_node.get(nid, []))
        for eid in before:
            self._specs.pop(eid, None)
        self._by_node[nid] = []
        self._buttons = {k: v for k, v in self._buttons.items() if k[0] != nid}
        plan = self._merge(values, self._plan(values))
        battery = self._battery(values)
        node_name = self._info(values, 0)["name"] or f"Matter device {nid}"
        for s in plan:
            s["owner"] = self._owner(values, s["ep"])
        names = [self._info(values, s["owner"])["name"] or node_name for s in plan]
        moved: list[dict[str, Any]] = []
        for spec, base in zip(plan, names):
            info = self._info(values, spec["owner"])
            same = [s for s, n in zip(plan, names) if n == base]
            if len(same) == 1 or (spec is same[0] and not any(s["role"] in OUTPUTS for s in same)):
                name = base  # e.g. a motion sensor that also measures light: the motion sensor keeps the name
            elif spec["role"] in OUTPUTS:
                mine = [s for s in same if s["role"] in OUTPUTS]
                name = base if len(mine) == 1 else f"{base} {mine.index(spec) + 1}"
            else:
                name = f"{base} {WORDS.get(spec['role'], '')}".strip()
                if sum(1 for s in same if s["role"] == spec["role"]) > 1:
                    name += f" {spec['ep']}"
            # The device keeps the id its main reading (or its sensor) always had; readings that were devices
            # of their own before they were merged into it hand over whatever used them (below).
            eid = self.entity_id(self._local(values, nid, spec))
            self.hub.upsert(eid, kind=spec["kind"], name=name, integration=self.id)
            for x in spec.get("extras", ()):
                old = self.entity_id(self._local(values, nid, x))
                if old not in self._moved:
                    moved.append({"from": old, "to": eid, "properties": self._moved_properties(x["kind"], spec["kind"], x["role"])})
            spec["node"] = nid
            self._specs[eid] = spec
            self._by_node[nid].append(eid)
            attrs: dict[str, Any] = {k: info[k] for k in ("model", "manufacturer", "firmware", "serial") if info.get(k)}
            if spec.get("unit"):
                attrs["unitOfMeasurement"] = spec["unit"]
                attrs["deviceClass"] = spec["device_class"]
            if spec["kind"] == "light":
                attrs["dimmable"] = bool(spec.get("dimmable"))
            if (network := self._network(values)):
                attrs["protocol"] = f"Matter over {network}"
            if spec["role"] == "buttons":
                attrs["events"] = []
                for i, bep in enumerate(spec["buttons"]):
                    base, can = f"button_{i + 1}", self._button(values, bep)
                    attrs["events"] += [base] + [f"{base}_{x}" for x, ok in (("double", can["double"]), ("long", can["long"]),
                                                                            ("released", can["long"])) if ok]
                    self._buttons[(nid, bep)] = (eid, base, can)
            attrs.update(battery)
            await self.hub.update(eid, attrs=attrs)
            await self._publish(eid)
        for eid in before - set(self._by_node[nid]):  # no longer part of this device
            self.hub.remove(eid)
        if moved:
            await self._hand_over(moved)

    async def _hand_over(self, pairs: list[dict[str, Any]]) -> None:
        """Readings that used to be devices of their own (an older Newman made one per reading): what used
        them - automations, dashboards, a name given to one - moves to the device they're part of now, and
        the old device is gone. Done once per run; nothing happens when nothing used them."""
        for p in pairs:
            self._moved.add(p["from"])
            self.hub.remove(p["from"])
        replace = self.ctx.replace_entities
        if replace is None:
            return
        try:
            counts = await replace(pairs)
        except Exception:  # noqa: BLE001 - the devices themselves work either way
            log.exception("Matter: moving what used %s failed", ", ".join(p["from"] for p in pairs))
            return
        if counts.get("references") or counts.get("names"):
            log.info("Matter: merged %s into the devices they're part of (%s)", ", ".join(p["from"] for p in pairs), counts)

    def _drop_node(self, nid: int) -> None:
        """Gone from Newman's fabric (removed here or by another app using the same server)."""
        self._nodes.pop(nid, None)
        for eid in self._by_node.pop(nid, []):
            self._specs.pop(eid, None)
            self.hub.remove(eid)
        self._buttons = {k: v for k, v in self._buttons.items() if k[0] != nid}

    async def _on_attribute(self, nid: int, path: str, value: Any) -> None:
        node = self._nodes.get(nid)
        if node is None:
            return
        node["attributes"][path] = value
        parts = path.split("/")
        if len(parts) != 3:
            return
        ep, cluster = int(parts[0]), int(parts[1])
        if cluster == DESCRIPTOR:
            return  # the server re-reads the device and sends it whole (node_updated)
        battery = self._battery(node["attributes"]) if cluster == POWER_SOURCE else None
        for eid in self._by_node.get(nid, []):
            spec = self._specs.get(eid)
            if not spec:
                continue
            if battery is not None:
                await self.hub.update(eid, attrs=battery)
            elif ep in (spec["ep"], spec.get("meter"), spec.get("owner") or None) or any(x["ep"] == ep for x in spec.get("extras", ())):
                await self._publish(eid)

    # -- values -> states ----------------------------------------------------------------------------

    @staticmethod
    def _reading(v: dict[str, Any], role: str, ep: int) -> float | int | None:
        """A measurement in its real unit (READINGS), unrounded."""
        g = lambda c, a: v.get(_p(ep, c, a))  # noqa: E731
        if role in ("temperature", "humidity"):
            return _num(g(TEMPERATURE if role == "temperature" else HUMIDITY, 0), 100)
        if role == "illuminance":
            raw = g(ILLUMINANCE, 0)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
                return _num(10 ** ((raw - 1) / 10000))  # MeasuredValue = 10000 × log10(lux) + 1
            return 0 if raw == 0 else None
        if role == "power":
            return _num(g(POWER, 8), 1000)
        return None

    async def _publish(self, eid: str) -> None:
        spec = self._specs[eid]
        node = self._nodes.get(spec["node"]) or {"attributes": {}, "available": False}
        v, ep, role = node["attributes"], spec["ep"], spec["role"]
        g = lambda c, a, e=ep: v.get(_p(e, c, a))  # noqa: E731
        state: str | None = None
        attrs: dict[str, Any] = {}
        if role == "onoff":
            on = g(ON_OFF, 0)
            state = None if on is None else ("on" if on else "off")
            level = g(LEVEL, 0)
            if spec.get("dimmable") and spec["kind"] == "light" and isinstance(level, (int, float)):
                attrs["brightness"] = max(1, round(level / 254 * 100))  # a slider position, 1-100
        elif role == "lock":
            ls = g(DOOR_LOCK, 0)
            state = LOCK_STATES.get(ls) if isinstance(ls, int) else None
        elif role == "cover":
            closed = g(COVER, 14)  # 0 = fully open, 10000 = fully closed
            if (pct := _num(closed, 100)) is not None:
                attrs["position"] = _num(100 - pct)
            moving = (g(COVER, 10) or 0) & 0b11
            state = {1: "opening", 2: "closing"}.get(moving) or (
                None if closed is None else "closed" if closed >= 10000 else "open")
        elif role == "thermostat":
            state = THERMOSTAT_MODES.get(g(THERMOSTAT, 0x1C))
            cur, heat, cool = _num(g(THERMOSTAT, 0), 100), _num(g(THERMOSTAT, 0x12), 100), _num(g(THERMOSTAT, 0x11), 100)
            attrs.update(currentTemp=cur, heatTo=heat, coolTo=cool, unit="°C", targetTemp=cool if state == "cool" else heat)
            running = g(THERMOSTAT, 0x29)
            if isinstance(running, int):
                attrs["action"] = "heating" if running & 0b1 else "cooling" if running & 0b10 else "idle"
        elif role == "fan":
            if isinstance(pct := g(FAN, 3), int):
                attrs["percentage"] = pct
            mode = g(FAN, 0)
            state = None if mode is None else ("off" if mode == 0 else "on")
        elif role == "boolean":
            b = g(BOOLEAN, 0)
            if b is not None:
                # StateValue: a contact sensor's true = in contact (closed); a leak detector's true = leak.
                state = {"contact": ("closed", "open"), "leak": ("wet", "dry")}.get(spec["kind"], ("on", "off"))[0 if b else 1]
        elif role == "motion":
            o = g(OCCUPANCY, 0)
            state = None if not isinstance(o, int) else ("detected" if o & 1 else "clear")
        elif role in READINGS:
            if (x := self._reading(v, role, ep)) is not None:
                state = _text(x)
        elif role == "smoke":
            smoke, co = g(SMOKE_CO, 1), g(SMOKE_CO, 2)
            state = "detected" if (smoke or 0) > 0 or (co or 0) > 0 else "clear"
        elif role == "buttons":
            state = "idle"
        units: dict[str, str] = {}
        for x in spec.get("extras", ()):  # the device's other readings, as values of their own
            unit, key = READINGS[x["role"]]
            attrs[key], units[key] = self._reading(v, x["role"], x["ep"]), unit
        if (m := spec.get("meter")) is not None:
            # Electrical Power Measurement: mW, mV, mA; Electrical Energy Measurement: mWh.
            for key, attr, scale in (("power", 8, 1000), ("voltage", 4, 1000), ("current", 5, 1000)):
                if (x := _num(g(POWER, attr, m), scale)) is not None:
                    attrs[key] = x
            for attr in (1, 5):  # cumulative imported energy; periodic (when that's all it reports)
                energy = _num(_field(g(ENERGY, attr, m), 0, "energy"), 1_000_000)
                if energy is not None:
                    attrs.setdefault("energy", energy)
            if "voltage" in attrs or "current" in attrs:
                units.update(voltage="V", current="A")
        if units:
            attrs["units"] = units
        info = self._info(v, spec.get("owner", ep))
        available = bool(node.get("available")) and info.get("reachable") is not False
        changes: dict[str, Any] = {"available": available}
        if state is not None:
            changes["state"] = state
        await self.hub.update(eid, **changes, attrs={k: x for k, x in attrs.items() if x is not None} or None)

    # -- status ---------------------------------------------------------------------------------------

    def _thread_nodes(self) -> list[int]:
        return [nid for nid, n in self._nodes.items() if self._network(n["attributes"]) == "Thread"]

    def thread_check(self) -> dict[str, Any]:
        """Can this computer reach Thread devices? {"ok", "routes", "text"} in plain words."""
        host = host_ipv6(self.proc)
        threads = self._thread_nodes()
        down = [nid for nid in threads if not self._nodes[nid]["available"]]
        if host is None:
            return {"ok": None, "routes": [], "text": ""}
        routes = host["routes"]
        if routes:
            text = "Thread network reachable" if not down else (
                f"{plural(len(down), 'Thread device')} not answering, though this computer has a route to the Thread "
                "network: they may be out of range of a border router, or have no power")
            return {"ok": not down, "routes": routes, "text": text}
        why = "; ".join(host["problems"])
        reason = ("this computer has no IPv6 route to a Thread network. It needs IPv6 on, router advertisements "
                  "accepted with their routes (accept_ra=2 and accept_ra_rt_info_max_plen=64), and to be on the same "
                  "network as a Thread border router (an Apple TV, HomePod or Nest hub)")
        if why:
            reason += f". Right now: {why}"
        if threads:
            return {"ok": False, "routes": [], "text": f"Thread devices can't be reached: {reason}"}
        return {"ok": False, "routes": [], "text": f"Wi-Fi and Ethernet devices only for now: {reason}"}

    def _refresh_status(self) -> None:
        if self._ws is None:
            return
        parts = [device_summary(s["kind"] for s in self._specs.values())]
        offline = [nid for nid, n in self._nodes.items() if not n["available"]]
        if offline:
            parts.append(f"{plural(len(offline), 'device')} not answering")
        if self.adding.get("state") == "working":
            parts.append("adding a device…")
        thread = self.thread_check()
        if thread["text"]:
            parts.append(thread["text"])
        self.set_status("connected", " · ".join(parts))

    def admin_details(self) -> dict[str, Any]:
        devices = []
        for nid, n in sorted(self._nodes.items()):
            info = self._info(n["attributes"], 0)
            entities = list(self._by_node.get(nid, []))
            first = self.hub.get(entities[0]) if entities else None
            devices.append({"id": str(nid), "name": (first.name if first and len(entities) == 1 else None) or info["name"]
                            or f"Matter device {nid}", "model": info["model"] or "", "manufacturer": info["manufacturer"] or "",
                            "network": self._network(n["attributes"]), "available": n["available"], "entityIds": entities})
        return {"pairedDevices": devices, "adding": self._adding_view(),
                "thread": {k: v for k, v in self.thread_check().items() if k in ("ok", "routes")}}

    # -- adding and removing devices ----------------------------------------------------------------

    # What a running "add a device" is doing, in words. The Matter server answers once at the end, so
    # finding and pairing are one step; Newman sees the device appear, then sets it up.
    ADD_STEPS = {
        "looking": ("Looking for the device and pairing it…",
                    "This can take a minute or two. The device needs power and its pairing window open."),
        "adding": ("Found it. Adding it to Newman…", "Reading what the device can do."),
    }

    def _adding_view(self) -> dict[str, Any]:
        out = dict(self.adding)
        if out.get("state") == "working":
            out["elapsed"] = max(0, int(time.time() - float(out.get("startedAt") or out.get("at") or time.time())))
        return out

    def _step(self, step: str) -> None:
        title, message = self.ADD_STEPS[step]
        self.adding = {**self.adding, "step": step, "title": title, "message": message}

    def _finish(self, state: str, message: str, **extra: Any) -> None:
        """An attempt ended: kept until the person has seen it ("seen" false until add_seen)."""
        self.adding = {"state": state, "at": time.time(), "startedAt": self.adding.get("startedAt"),
                       "message": message, "seen": False, **extra}
        self._add_task = None
        self._refresh_status()

    def _stale(self) -> bool:
        """A "working" left over from an attempt that's had longer than the Matter server ever takes."""
        started = float(self.adding.get("startedAt") or self.adding.get("at") or 0)
        return time.time() - started > COMMISSION_TIMEOUT_S + 30

    async def add_device(self, code: str) -> dict[str, Any]:
        clean = setup_code(code)
        if self._ws is None:
            raise ValueError("The Matter server isn't connected right now, so nothing can be added.")
        if self.adding.get("state") == "working":
            if not self._stale():
                raise ValueError("Newman is still adding the last device. Cancel it to try another code.")
            log.info("Matter: forgetting an add that never finished")
            await self._stop_attempt()
        self._attempt += 1
        now = time.time()
        self.adding = {"state": "working", "at": now, "startedAt": now, "seen": False}
        self._step("looking")
        self._refresh_status()
        self._add_task = self._tasks.spawn(self._commission(clean, self._attempt))
        return self._adding_view()

    async def _stop_attempt(self) -> None:
        """Newman stops waiting: whatever that attempt still hears is ignored. The Matter server can't be
        told to stop part way, so if the device does finish pairing it shows up by itself."""
        self._attempt += 1
        task, self._add_task = self._add_task, None
        # Only while it's still waiting on the server: once the device is in, its setup finishes.
        if task and not task.done() and self.adding.get("step") == "looking":
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def cancel_add(self) -> dict[str, Any]:
        if self.adding.get("state") != "working":
            raise ValueError("Nothing is being added right now.")
        await self._stop_attempt()
        self._finish("cancelled", "If the device finishes pairing anyway, it shows up in Newman by itself.",
                     seen=True)
        log.info("Matter: adding cancelled")
        return self._adding_view()

    def add_seen(self) -> None:
        if self.adding.get("state") not in (None, "working"):
            self.adding = {**self.adding, "seen": True}

    async def _commission(self, code: str, attempt: int) -> None:
        def current() -> bool:
            return attempt == self._attempt
        try:
            node = await self._call("commission_with_code", {"code": code, "network_only": True},
                                    timeout=COMMISSION_TIMEOUT_S)
            if not current():
                return
            self._step("adding")
            ids: list[str] = []
            label = "the device"
            if isinstance(node, dict) and isinstance(node.get("node_id"), int):
                if node["node_id"] not in self._nodes or not self._by_node.get(node["node_id"]):
                    await self._setup_node(node)
                ids = list(self._by_node.get(node["node_id"], []))
                label = self._info(node.get("attributes") or {}, 0)["name"] or label
            if not current():
                return
            what = device_summary(self._specs[e]["kind"] for e in ids if e in self._specs)
            self._finish("done", f"{label}: {what}." if ids else f"{label}.", deviceIds=ids)
            log.info("Matter: added %s (%s)", label, ", ".join(ids))
        except asyncio.CancelledError:
            raise
        except MatterError as e:
            log.info("Matter: adding failed (%s): %s", e.code, e.details)
            if current():
                self._finish("error", self.explain(e))
        except (asyncio.TimeoutError, TimeoutError):
            if current():
                self._finish("error", "The Matter server gave up waiting for the device. " + self._reach_hint())
        except Exception as e:  # noqa: BLE001 - shown to the person adding it
            log.exception("Matter: adding failed")
            if current():
                self._finish("error", f"Couldn't add it: {e}")

    def _reach_hint(self) -> str:
        thread = self.thread_check()
        text = ("If it's a Thread device, this computer needs an IPv6 route to the Thread network"
                + (": right now it has none." if thread["ok"] is False and not thread["routes"] else
                   " (it has one)." if thread["routes"] else "."))
        return f"Make sure it's powered and nearby. {text}"

    def explain(self, e: MatterError) -> str:
        """The server's reason a device couldn't be added, in plain words."""
        d = (e.details or "").lower()
        if e.code == -1:
            return "The connection to the Matter server dropped while adding the device. Try again."
        if e.code == INVALID_ARGS or ("invalid" in d and ("code" in d or "payload" in d)):
            return "The Matter server didn't accept that setup code. Check it and try again."
        if e.code == ICD_MULTI_ADMIN or re.search(r"\bicd\b", d):
            return ("This is a sleepy battery device that the Matter server can't share between controllers yet. "
                    "Wake it (press its button) and try again with a new code.")
        if any(w in d for w in ("pase", "spake", "pake", "passcode", "verifier", "incorrect", "wrong", "authenticat")):
            return ("The device turned that code down. Check the code; a code from another app's pairing mode works "
                    f"once and only for a while ({APPLE_WINDOW}), so make a new one if needed.")
        if any(w in d for w in ("discover", "not found", "no device", "commissionable", "could not find", "unable to find",
                                "no matching")):
            return ("No device waiting to be added answered. Its pairing window may have closed: " + APPLE_WINDOW
                    + ", so make a new code and try again. " + self._reach_hint())
        if e.code == NODE_NOT_RESOLVING or any(w in d for w in ("timeout", "timed out", "unreachable", "no route",
                                                                "resolve")):
            return "The device was found but couldn't be reached while adding it. " + self._reach_hint()
        if "fabric" in d and ("full" in d or "table" in d or "resource" in d):
            return ("The device already has as many controllers as it can (usually 5). Remove it from an app you no "
                    "longer use, then try again.")
        return f"The Matter server couldn't add it: {e.details or 'no reason given'}."

    async def remove_device(self, device_id: str) -> None:
        try:
            nid = int(device_id)
        except (TypeError, ValueError):
            raise KeyError(device_id) from None
        if nid not in self._nodes:
            raise KeyError(device_id)
        try:
            await self._call("remove_node", {"node_id": nid}, timeout=60)
        except MatterError as e:
            if e.code != NODE_NOT_EXISTS:
                raise CommandError(f"The Matter server couldn't remove it: {e.details or e.code}") from e
        self._drop_node(nid)
        self._refresh_status()

    # -- commands ---------------------------------------------------------------------------------------

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        spec = self._specs.get(entity_id)
        if not spec:
            raise CommandError("That Matter device isn't known")
        if self._ws is None:
            raise CommandError("The Matter server isn't connected right now")
        nid, ep, role = spec["node"], spec["ep"], spec["role"]

        def pct(x: Any) -> float:
            return max(0.0, min(100.0, float(x if x is not None else 100)))

        async def send(cluster: int, name: str, payload: dict[str, Any] | None = None, timed: int | None = None) -> None:
            args = {"node_id": nid, "endpoint_id": ep, "cluster_id": cluster, "command_name": name, "payload": payload or {}}
            if timed:
                args["timed_request_timeout_ms"] = timed
            await self._call("device_command", args, timeout=30)

        async def write(cluster: int, attr: int, val: Any) -> None:
            await self._call("write_attribute", {"node_id": nid, "attribute_path": _p(ep, cluster, attr), "value": val})
        try:
            if role == "onoff" and command in ("turn_on", "turn_off"):
                await send(ON_OFF, "On" if command == "turn_on" else "Off")
            elif role == "onoff" and command == "set_brightness" and spec.get("dimmable"):
                await send(LEVEL, "MoveToLevelWithOnOff", {"level": max(1, round(pct(value) / 100 * 254)),
                                                           "transitionTime": 0, "optionsMask": 0, "optionsOverride": 0})
            elif role == "lock" and command in ("lock", "unlock"):
                await send(DOOR_LOCK, "LockDoor" if command == "lock" else "UnlockDoor", timed=1000)
            elif role == "cover" and command in ("open", "close", "stop"):
                await send(COVER, {"open": "UpOrOpen", "close": "DownOrClose", "stop": "StopMotion"}[command])
            elif role == "cover" and command == "set_position":
                await send(COVER, "GoToLiftPercentage", {"liftPercent100thsValue": round((100 - pct(value)) * 100)})
            elif role == "fan" and command in ("turn_on", "turn_off", "set_speed"):
                await write(FAN, 2, int(0 if command == "turn_off" else pct(value) if command == "set_speed" else 100))
            elif role == "thermostat" and command == "set_mode":
                modes = {m: k for k, m in THERMOSTAT_MODES.items()}
                if value not in modes:
                    raise CommandError(f"Mode must be one of {', '.join(modes)}")
                await write(THERMOSTAT, 0x1C, modes[value])
            elif role == "thermostat" and command == "set_temperature":
                ent = self.hub.get(entity_id)
                await write(THERMOSTAT, 0x11 if ent and ent.state == "cool" else 0x12, round(float(value) * 100))
            else:
                raise CommandError(f"{self.hub.name(entity_id)} can't {command.replace('_', ' ')}")
        except (MatterError, asyncio.TimeoutError, TimeoutError) as e:
            raise self._command_error(entity_id, e) from e

    def _command_error(self, entity_id: str, e: Exception) -> CommandError:
        if isinstance(e, MatterError):
            if e.code in (NODE_NOT_READY, NODE_NOT_RESOLVING) or "timeout" in e.details.lower():
                return CommandError(f"{self.hub.name(entity_id)} isn't answering right now")
            return CommandError(f"The device didn't accept that ({e.details or e.code})")
        return CommandError(f"{self.hub.name(entity_id)} didn't answer in time")

    # A dimmable light ramps its own level (Level Control Move, in level units 0-254 per second): up with
    # MoveWithOnOff (an off light comes on), down with a plain Move (it stops at the lowest level, never
    # off), Stop where it is. Its new level comes back through the node's subscription.
    def can_move(self, entity_id: str, attr: str) -> bool:
        spec = self._specs.get(entity_id)
        return bool(spec) and attr == "brightness" and spec["role"] == "onoff" and bool(spec.get("dimmable"))

    async def move(self, entity_id: str, attr: str, direction: int, full_s: float) -> None:
        if not self.can_move(entity_id, attr):
            await super().move(entity_id, attr, direction, full_s)
        if self._ws is None:
            raise CommandError("The Matter server isn't connected right now")
        spec = self._specs[entity_id]
        opts = {"optionsMask": 0, "optionsOverride": 0}
        rate = max(1, round(254 / max(full_s, 0.1)))
        name, payload = ("Stop", opts) if direction == 0 else \
            ("MoveWithOnOff", {"moveMode": 0, "rate": rate, **opts}) if direction > 0 else \
            ("Move", {"moveMode": 1, "rate": rate, **opts})
        try:
            await self._call("device_command", {"node_id": spec["node"], "endpoint_id": spec["ep"], "cluster_id": LEVEL,
                                                "command_name": name, "payload": payload}, timeout=30)
        except (MatterError, asyncio.TimeoutError, TimeoutError) as e:
            raise self._command_error(entity_id, e) from e
