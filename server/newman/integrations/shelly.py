"""Shelly relays, plugs, dimmers, blinds and sensors, fully local - no Shelly account.

Newer Shellys (Plus, Pro, Gen3, Gen4 - "RPC" devices) are driven with aioshelly, the library behind
Home Assistant's Shelly integration: Newman keeps a websocket open to the device and the device pushes
every change down it straight away. First-generation Shellys (Shelly 1, 2.5, Plug S, Dimmer, RGBW2…)
are read over their simple HTTP API every few seconds.

One connection = one Shelly (a Pro 4PM still becomes four switches). Each output, cover and sensor
maps onto an existing kind; every reading (power, energy, voltage…) goes into attrs.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from ._common import TaskKeeper, clean_id, device_summary
from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

LIGHT_PREFIXES = {"light": "Light", "rgb": "RGB", "rgbw": "RGBW", "cct": "CCT"}
# Gen2+ status component prefix -> (kind, name suffix for sensors on a device with outputs)
SENSOR_PREFIXES = {"temperature": ("temperature", "temperature"), "humidity": ("sensor", "humidity"),
                   "illuminance": ("sensor", "light level"), "flood": ("leak", "leak"),
                   "smoke": ("smoke", "smoke alarm"), "pm1": ("sensor", "power"), "em1": ("sensor", "power"),
                   "em": ("sensor", "power")}
COVER_STATES = {"open": "open", "closed": "closed", "opening": "opening", "closing": "closing",
                "stopped": "stopped", "calibrating": "stopped"}


def _num(v: Any, digits: int = 2) -> float | None:
    try:
        return round(float(v), digits)
    except (TypeError, ValueError):
        return None


def _kwh(wh: Any) -> float | None:
    v = _num(wh, 3)
    return round(v / 1000, 3) if v is not None else None


def _nth(items: Any, i: int) -> dict[str, Any]:
    """items[i] if it's there and a dict, else {} (Gen1 lists: relays, meters, settings…)."""
    return items[i] if isinstance(items, list) and i < len(items) and isinstance(items[i], dict) else {}


class ShellyIntegration(Integration):
    type = "shelly"
    name = "Shelly"
    category = "lighting"
    provides = ("switch", "light", "cover", "temperature", "sensor", "leak", "smoke", "remote")  # kinds of device it can bring in (shown on its catalog card)
    description = ("Shelly relays, plugs, dimmers, blinds and sensors, controlled directly on your network - no "
                   "Shelly account needed. Newer Shellys report every change instantly; first-generation ones are "
                   "checked every few seconds. Power and energy readings come along too.")
    fields = [
        Field("host", "Shelly address", required=True, placeholder="10.1.1.60",
              help="Found automatically if the Shelly is on your network."),
        Field("password", "Shelly password", "password",
              help="Only if you turned on a password in the Shelly's own settings."),
        Field("username", "Shelly username", default="admin", advanced=True,
              help="First-generation Shellys only; newer ones always use admin."),
        Field("poll_seconds", "Check first-generation Shellys every (seconds)", "number", default=5, advanced=True),
    ]
    # Every Shelly answers on _http; Plus/Pro/Gen3+ also announce _shelly.
    zeroconf = ["_shelly._tcp.local.", "_http._tcp.local."]

    @classmethod
    def from_discovery(cls, name: str, host: str, props: dict[str, str]) -> dict[str, Any] | None:
        base = name.split(".")[0]
        if not base.lower().startswith("shelly"):
            return None
        model = props.get("app") or base.rsplit("-", 1)[0].removeprefix("shelly").removeprefix("Shelly")
        return {"title": f"Shelly {model}".strip() + f" ({host})", "config": {"host": host}}

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._tasks = TaskKeeper()
        self._rpc: Any = None                     # aioshelly RpcDevice (Gen2+)
        self._http: aiohttp.ClientSession | None = None
        self._gen = 0
        self._mac = ""
        self._device_name = "Shelly"
        self._specs: dict[str, dict[str, Any]] = {}   # entity id -> {"kind", "key" | "gen1", ...}
        self._battery: int | None = None

    # -- helpers ------------------------------------------------------------------------

    def _eid(self, part: str) -> str:
        return self.entity_id(f"{self._mac}_{clean_id(part.replace(':', '_'))}")

    def _add(self, part: str, kind: str, name: str, **spec: Any) -> str:
        eid = self._eid(part)
        self.hub.upsert(eid, kind=kind, name=name, integration=self.id)
        self._specs[eid] = {"kind": kind, **spec}
        return eid

    def _connected(self) -> None:
        how = "reports changes instantly" if self._gen >= 2 else "on your network"
        self.set_status("connected", f"{self._device_name} · {device_summary(s['kind'] for s in self._specs.values())}"
                                     f" · {how}")

    async def _all_unavailable(self) -> None:
        for eid in self._specs:
            await self.hub.update(eid, available=False)

    # -- running ------------------------------------------------------------------------

    async def run(self) -> None:
        from aioshelly.common import get_info
        from aioshelly.exceptions import InvalidAuthError, MacAddressMismatchError, ShellyError
        attempt = 0
        async with aiohttp.ClientSession() as http:
            self._http = http
            while True:
                try:
                    info = await get_info(http, self.config["host"])
                    self._gen = int(info.get("gen") or 1)
                    if self._gen >= 2:
                        await self._run_rpc(http)
                    else:
                        await self._run_gen1(info)
                    attempt = 0
                except asyncio.CancelledError:
                    raise
                except InvalidAuthError:
                    self.set_status("error", "This Shelly has a password - enter it in the settings and save")
                    await self._all_unavailable()
                    await asyncio.Event().wait()  # saving new settings restarts us
                except MacAddressMismatchError:
                    self.set_status("error", f"A different Shelly now answers at {self.config['host']}")
                    await self._all_unavailable()
                except (ShellyError, aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:
                    log.info("Shelly %s: %s: %s", self.config["host"], e.__class__.__name__, e)
                    self.set_status("error", f"Can't reach the Shelly at {self.config['host']} - retrying")
                    await self._all_unavailable()
                except Exception as e:
                    log.exception("Shelly %s failed", self.config["host"])
                    self.set_status("error", f"{self.config['host']}: {e.__class__.__name__}: {e}")
                    await self._all_unavailable()
                attempt += 1
                await self.backoff_sleep(attempt)

    # -- newer Shellys (RPC over a websocket the device pushes changes down) -------------------

    async def _run_rpc(self, http: aiohttp.ClientSession) -> None:
        from aioshelly.common import ConnectionOptions
        from aioshelly.rpc_device import RpcDevice, RpcUpdateType
        pw = self.config.get("password") or None
        options = ConnectionOptions(self.config["host"], "admin" if pw else None, pw)
        device = await RpcDevice.create(http, None, options)
        lost = asyncio.Event()

        def on_update(dev: Any, kind: Any) -> None:
            if kind == RpcUpdateType.DISCONNECTED:
                lost.set()
            elif kind == RpcUpdateType.EVENT:
                self._tasks.spawn(self._rpc_event(dev.event or {}))
            elif kind in (RpcUpdateType.STATUS, RpcUpdateType.INITIALIZED):
                self._tasks.spawn(self._rpc_publish(dev.status))

        device.subscribe_updates(on_update)
        try:
            await device.initialize()
            self._rpc = device
            await self._rpc_setup(device.shelly, device.config, device.status)
            self._connected()
            # Changes arrive pushed; a slow check catches anything the websocket missed.
            while not lost.is_set():
                try:
                    await asyncio.wait_for(lost.wait(), 60)
                except asyncio.TimeoutError:
                    await device.poll()
                    await self._rpc_publish(device.status)
            raise ConnectionError("the Shelly closed the connection")
        finally:
            self._rpc = None
            await device.shutdown()

    async def _rpc_setup(self, info: dict[str, Any], config: dict[str, Any], status: dict[str, Any]) -> None:
        self._mac = clean_id(info.get("mac"))
        self._device_name = ((config.get("sys") or {}).get("device") or {}).get("name") or info.get("name") \
            or f"Shelly {info.get('app') or info.get('model') or ''}".strip()
        self._specs.clear()
        consumption = ((config.get("sys") or {}).get("ui_data") or {}).get("consumption_types") or []
        keys = sorted(k for k in status if ":" in k)
        outputs = [k for k in keys if k.split(":")[0] in ("switch", "cover", *LIGHT_PREFIXES)]
        sensors = [k for k in keys if k.split(":")[0] in SENSOR_PREFIXES]
        for key in keys:
            prefix, _, idx = key.partition(":")
            if not idx.isdigit():
                continue
            n, own = int(idx), (config.get(key) or {}).get("name")
            same = [k for k in keys if k.split(":")[0] == prefix]
            if prefix in ("switch", "cover", *LIGHT_PREFIXES):
                name = own or (f"{self._device_name} {n + 1}" if len(outputs) > 1 else self._device_name)
                if prefix == "switch":
                    as_light = n < len(consumption) and str(consumption[n]).lower().startswith("light")
                    self._add(key, "light" if as_light else "switch", name, key=key, method="Switch", dimmable=False)
                elif prefix == "cover":
                    self._add(key, "cover", name, key=key,
                              positions=bool((config.get(key) or {}).get("pos_control", True)))
                else:
                    self._add(key, "light", name, key=key, method=LIGHT_PREFIXES[prefix], dimmable=True)
            elif prefix in SENSOR_PREFIXES:
                kind, word = SENSOR_PREFIXES[prefix]
                # A sensor-only Shelly (a Flood) is just its name; next to outputs or other sensors, say what it is.
                name = own or (f"{self._device_name} {word}" if outputs or len(sensors) > 1 else self._device_name)
                if not own and len(same) > 1:
                    name = f"{name} {n + 1}" if n < 100 else f"{name} {n - 99}"  # add-on sensors are 100+
                self._add(key, kind, name, key=key)
            elif prefix == "input" and (config.get(key) or {}).get("type") == "button" and n < 4:
                eid = self._eid("buttons")
                if eid not in self._specs:
                    self._add("buttons", "remote", f"{self._device_name} buttons", key="buttons", events=[])
                self._specs[eid]["events"].append(f"button_{n + 1}")
        info_attrs = {"model": info.get("model"), "firmware": info.get("ver") or info.get("fw_id"),
                      "host": self.config["host"]}
        for eid, spec in self._specs.items():
            attrs = dict(info_attrs)
            if spec["kind"] == "light":
                attrs["dimmable"] = spec.get("dimmable", False)
            if spec["kind"] == "cover" and not spec.get("positions"):
                attrs["dimmable"] = False  # no position slider: this cover only knows open / closed
            if spec["kind"] == "remote":
                attrs["events"] = spec["events"]
                await self.hub.update(eid, state="idle", available=True, attrs=attrs)
            else:
                await self.hub.update(eid, attrs=attrs)
        await self._rpc_publish(status)

    async def _rpc_publish(self, status: dict[str, Any]) -> None:
        if (power := status.get("devicepower:0")) and isinstance(power.get("battery"), dict):
            self._battery = power["battery"].get("percent")
        for eid, spec in self._specs.items():
            s = status.get(spec["key"])
            if not isinstance(s, dict):
                continue
            state, attrs = self._rpc_values(spec, s)
            if self._battery is not None and spec["kind"] in ("temperature", "sensor", "leak", "smoke", "remote"):
                attrs["batteryLevel"] = self._battery
            attrs = {k: v for k, v in attrs.items() if v is not None}
            await self.hub.update(eid, available=True, **({"state": state} if state is not None else {}),
                                  attrs=attrs or None)

    @staticmethod
    def _rpc_values(spec: dict[str, Any], s: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        prefix = spec["key"].split(":")[0]
        attrs: dict[str, Any] = {}
        if "apower" in s or "aenergy" in s:
            attrs.update(power=_num(s.get("apower")), energy=_kwh((s.get("aenergy") or {}).get("total")),
                         voltage=_num(s.get("voltage")), current=_num(s.get("current"), 3))
        if isinstance(s.get("temperature"), dict) and prefix != "temperature":
            attrs["deviceTemperature"] = _num(s["temperature"].get("tC"), 1)
        kind = spec["kind"]
        if prefix in ("switch", *LIGHT_PREFIXES):
            if prefix in LIGHT_PREFIXES and s.get("brightness") is not None:
                attrs["brightness"] = int(s["brightness"])
            return ("on" if s.get("output") else "off") if "output" in s else None, attrs
        if prefix == "cover":
            pos = s.get("current_pos")
            attrs["position"] = int(pos) if isinstance(pos, (int, float)) else None
            return COVER_STATES.get(str(s.get("state")), None), attrs
        if prefix == "temperature":
            t = _num(s.get("tC"), 1)
            return (f"{t:.1f}" if t is not None else None), attrs
        if prefix == "humidity":
            attrs.update(unitOfMeasurement="%", deviceClass="humidity")
            return (f"{_num(s.get('rh'), 1):g}" if s.get("rh") is not None else None), attrs
        if prefix == "illuminance":
            attrs.update(unitOfMeasurement="lx", deviceClass="illuminance")
            return (str(s["lux"]) if s.get("lux") is not None else None), attrs
        if prefix in ("pm1", "em1", "em"):
            watts = s.get("total_act_power", s.get("act_power", s.get("apower")))
            attrs.update(unitOfMeasurement="W", deviceClass="power")
            return (f"{_num(watts):g}" if _num(watts) is not None else None), attrs
        if prefix in ("flood", "smoke") and "alarm" in s:
            on = bool(s["alarm"])
            return ({"leak": ("wet", "dry"), "smoke": ("detected", "clear")}[kind][0 if on else 1]), attrs
        return None, attrs

    async def _rpc_event(self, event: dict[str, Any]) -> None:
        """Gen2+ NotifyEvent: button presses on inputs set to "button" mode."""
        eid = self._eid("buttons")
        if eid not in self._specs:
            return
        for ev in event.get("events") or []:
            comp, what = str(ev.get("component", "")), ev.get("event")
            if comp.startswith("input:") and what == "single_push":
                n = int(comp.split(":")[1])
                if f"button_{n + 1}" in self._specs[eid]["events"]:
                    await self.hub.emit(eid, f"button_{n + 1}", {})

    # -- first-generation Shellys (HTTP, polled) ------------------------------------------------

    async def _gen1_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self._http is not None
        auth = None
        if self.config.get("password"):
            auth = aiohttp.BasicAuth(self.config.get("username") or "admin", self.config["password"])
        async with self._http.get(f"http://{self.config['host']}/{path}", params=params, auth=auth,
                                  timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 401:
                from aioshelly.exceptions import InvalidAuthError
                raise InvalidAuthError("password needed")
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def _run_gen1(self, info: dict[str, Any]) -> None:
        settings = await self._gen1_get("settings")
        status = await self._gen1_get("status")
        await self._gen1_setup(info, settings, status)
        self._connected()
        misses = 0
        while True:
            await asyncio.sleep(float(self.config.get("poll_seconds") or 5))
            try:
                await self._gen1_publish(await self._gen1_get("status"))
                misses = 0
            except (aiohttp.ClientError, asyncio.TimeoutError):
                misses += 1
                if misses >= 3:
                    raise

    async def _gen1_setup(self, info: dict[str, Any], settings: dict[str, Any], status: dict[str, Any]) -> None:
        self._mac = clean_id(info.get("mac") or (settings.get("device") or {}).get("mac"))
        self._device_name = settings.get("name") or (settings.get("device") or {}).get("hostname") or \
            f"Shelly {info.get('type', '')}".strip()
        self._specs.clear()
        roller_mode = settings.get("mode") == "roller"
        groups = {"relays": [] if roller_mode else (status.get("relays") or []),
                  "lights": status.get("lights") or [],
                  "rollers": (status.get("rollers") or []) if roller_mode else []}
        n_outputs = sum(len(v) for v in groups.values())
        for group, items in groups.items():
            for i, _ in enumerate(items):
                own = _nth(settings.get(group), i)
                name = own.get("name") or (f"{self._device_name} {i + 1}" if n_outputs > 1 else self._device_name)
                if group == "relays":
                    lights = str(own.get("appliance_type", "")).lower() == "lights"
                    self._add(f"relay_{i}", "light" if lights else "switch", name, gen1=("relay", i), dimmable=False)
                elif group == "lights":
                    self._add(f"light_{i}", "light", name, gen1=("light", i), dimmable=True)
                else:
                    self._add(f"roller_{i}", "cover", name, gen1=("roller", i),
                              positions=bool(own.get("positioning", True)))
        for i, _ in enumerate(status.get("emeters") or []):
            self._add(f"emeter_{i}", "sensor", f"{self._device_name} power {i + 1}", gen1=("emeter", i))
        if isinstance(status.get("tmp"), dict) and status["tmp"].get("is_valid", True) and "tC" in status["tmp"]:
            alone = not n_outputs and not any(k in status for k in ("hum", "flood", "smoke", "lux"))
            self._add("temperature", "temperature",
                      self._device_name if alone else f"{self._device_name} temperature", gen1=("tmp", 0))
        for sid in sorted((status.get("ext_temperature") or {}), key=str):
            self._add(f"ext_temperature_{sid}", "temperature", f"{self._device_name} temperature {int(sid) + 1}",
                      gen1=("ext_temperature", sid))
        if isinstance(status.get("hum"), dict):
            self._add("humidity", "sensor", f"{self._device_name} humidity", gen1=("hum", 0))
        if isinstance(status.get("lux"), dict):
            self._add("light_level", "sensor", f"{self._device_name} light level", gen1=("lux", 0))
        if "flood" in status:
            self._add("flood", "leak", self._device_name, gen1=("flood", 0))
        if "smoke" in status:
            self._add("smoke", "smoke", self._device_name, gen1=("smoke", 0))
        base = {"model": info.get("type"), "firmware": info.get("fw"), "host": self.config["host"]}
        for eid, spec in self._specs.items():
            attrs = dict(base)
            if spec["kind"] == "light":
                attrs["dimmable"] = spec["dimmable"]
            if spec["kind"] == "cover" and not spec.get("positions"):
                attrs["dimmable"] = False
            await self.hub.update(eid, attrs=attrs)
        await self._gen1_publish(status)

    async def _gen1_publish(self, status: dict[str, Any]) -> None:
        battery = (status.get("bat") or {}).get("value") if isinstance(status.get("bat"), dict) else None
        for eid, spec in self._specs.items():
            group, i = spec["gen1"]
            attrs: dict[str, Any] = {}
            state: str | None = None
            if group in ("relay", "light", "roller") and (m := _nth(status.get("meters"), i)):
                # Gen1 meters count energy in watt-minutes.
                total = m.get("total")
                attrs.update(power=_num(m.get("power")),
                             energy=round(float(total) / 60000, 3) if isinstance(total, (int, float)) else None)
            if group == "relay":
                state = "on" if _nth(status.get("relays"), i).get("ison") else "off"
            elif group == "light":
                lt = _nth(status.get("lights"), i)
                state = "on" if lt.get("ison") else "off"
                bright = lt.get("brightness", lt.get("gain"))
                attrs["brightness"] = int(bright) if isinstance(bright, (int, float)) else None
                if "power" in lt:
                    attrs["power"] = _num(lt.get("power"))
            elif group == "roller":
                r = _nth(status.get("rollers"), i)
                pos = r.get("current_pos")
                pos = int(pos) if isinstance(pos, (int, float)) and pos >= 0 else None
                attrs.update(position=pos, power=_num(r.get("power")))
                st = r.get("state")
                if st == "open":
                    state = "opening"
                elif st == "close":
                    state = "closing"
                elif pos is not None:
                    state = "closed" if pos == 0 else "open"
                else:
                    state = "closed" if r.get("last_direction") == "close" else "open"
            elif group == "emeter":
                em = _nth(status.get("emeters"), i)
                w = _num(em.get("power"))
                state = f"{w:g}" if w is not None else None
                attrs.update(unitOfMeasurement="W", deviceClass="power", voltage=_num(em.get("voltage")),
                             energy=_kwh(em.get("total")))
            elif group == "tmp":
                t = _num((status.get("tmp") or {}).get("tC"), 1)
                state = f"{t:.1f}" if t is not None else None
            elif group == "ext_temperature":
                t = _num(((status.get("ext_temperature") or {}).get(i) or {}).get("tC"), 1)
                state = f"{t:.1f}" if t is not None else None
            elif group == "hum":
                h = _num((status.get("hum") or {}).get("value"), 1)
                state = f"{h:g}" if h is not None else None
                attrs.update(unitOfMeasurement="%", deviceClass="humidity")
            elif group == "lux":
                lx = (status.get("lux") or {}).get("value")
                state = str(lx) if lx is not None else None
                attrs.update(unitOfMeasurement="lx", deviceClass="illuminance")
            elif group == "flood":
                state = "wet" if status.get("flood") else "dry"
            elif group == "smoke":
                state = "detected" if status.get("smoke") else "clear"
            if battery is not None and spec["kind"] in ("temperature", "sensor", "leak", "smoke"):
                attrs["batteryLevel"] = battery
            attrs = {k: v for k, v in attrs.items() if v is not None}
            await self.hub.update(eid, available=True, **({"state": state} if state is not None else {}),
                                  attrs=attrs or None)

    # -- commands -------------------------------------------------------------------------

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        spec = self._specs.get(entity_id)
        if not spec:
            raise CommandError("That Shelly isn't connected")
        try:
            if self._gen >= 2:
                await self._rpc_command(spec, command, value)
            else:
                await self._gen1_command(spec, command, value)
        except CommandError:
            raise
        except Exception as e:
            raise CommandError(f"The Shelly didn't accept that: {e.__class__.__name__}") from e

    async def _rpc_command(self, spec: dict[str, Any], command: str, value: Any) -> None:
        if not self._rpc:
            raise CommandError("The Shelly isn't connected right now")
        prefix, _, idx = spec["key"].partition(":")
        n = int(idx) if idx.isdigit() else 0
        call = self._rpc.call_rpc
        if prefix == "cover":
            if command == "open":
                await call("Cover.Open", {"id": n})
            elif command == "close":
                await call("Cover.Close", {"id": n})
            elif command == "stop":
                await call("Cover.Stop", {"id": n})
            elif command == "set_position" and spec.get("positions"):
                await call("Cover.GoToPosition", {"id": n, "pos": max(0, min(100, int(float(value))))})
            else:
                raise CommandError(f"This cover can't {command.replace('_', ' ')}")
            return
        method = spec.get("method")
        if not method:
            raise CommandError(f"This Shelly can't {command.replace('_', ' ')}")
        if command == "turn_on":
            await call(f"{method}.Set", {"id": n, "on": True})
        elif command == "turn_off":
            await call(f"{method}.Set", {"id": n, "on": False})
        elif command == "set_brightness" and spec.get("dimmable"):
            await call(f"{method}.Set", {"id": n, "on": True, "brightness": max(1, min(100, int(float(value))))})
        else:
            raise CommandError(f"This Shelly can't {command.replace('_', ' ')}")

    async def _gen1_command(self, spec: dict[str, Any], command: str, value: Any) -> None:
        group, i = spec["gen1"]
        if group == "relay" and command in ("turn_on", "turn_off"):
            await self._gen1_get(f"relay/{i}", {"turn": "on" if command == "turn_on" else "off"})
        elif group == "light" and command in ("turn_on", "turn_off"):
            await self._gen1_get(f"light/{i}", {"turn": "on" if command == "turn_on" else "off"})
        elif group == "light" and command == "set_brightness":
            await self._gen1_get(f"light/{i}", {"turn": "on", "brightness": max(1, min(100, int(float(value))))})
        elif group == "roller" and command in ("open", "close", "stop"):
            await self._gen1_get(f"roller/{i}", {"go": command})
        elif group == "roller" and command == "set_position" and spec.get("positions"):
            await self._gen1_get(f"roller/{i}", {"go": "to_pos", "roller_pos": max(0, min(100, int(float(value))))})
        else:
            raise CommandError(f"This Shelly can't {command.replace('_', ' ')}")
        await self._gen1_publish(await self._gen1_get("status"))
