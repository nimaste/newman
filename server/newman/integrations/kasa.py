"""TP-Link Kasa and Tapo plugs, power strips, switches, dimmers and bulbs, on your network
(python-kasa - the library behind Home Assistant's TP-Link integration).

Older Kasa devices need nothing at all. Tapo devices and newer Kasa firmware insist on the TP-Link
account's email and password even for local control: the device checks them itself (nothing goes
to TP-Link's cloud), so they're kept - masked - as this connection's settings.

Devices are added by address, or found with "Scan my network" (a UDP broadcast TP-Link devices
answer), which only runs when asked. Every reading - power, energy, voltage - goes into attrs.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ._common import clean_id, device_summary
from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

# python-kasa DeviceType values -> kind. Others (cameras, hubs, sensors, thermostats…) are skipped.
KINDS_BY_TYPE = {"plug": "switch", "strip": "switch", "stripsocket": "switch", "wallswitch": "switch",
                 "bulb": "light", "lightstrip": "light", "dimmer": "light"}


def hosts_from_config(config: dict[str, Any]) -> list[dict[str, Any]]:
    out, seen = [], set()
    for d in config.get("devices") or []:
        host = str((d or {}).get("host") or "").strip()
        if host and host not in seen:
            seen.add(host)
            out.append({"host": host, "name": str(d.get("name") or "").strip()})
    return out


def _safe(fn: Any, default: Any = None) -> Any:
    try:
        return fn()
    except Exception:
        return default


class KasaIntegration(Integration):
    type = "kasa"
    name = "TP-Link Kasa / Tapo"
    category = "lighting"
    provides = ("switch", "light")  # kinds of device it can bring in (shown on its catalog card)
    description = ("TP-Link Kasa and Tapo plugs, power strips, switches, dimmers and bulbs, controlled directly on "
                   "your network, with power and energy readings. Older Kasa devices need no account; Tapo and newer "
                   "Kasa ones check your TP-Link email and password themselves (they never go to the cloud).")
    fields = [
        Field("devices", "Devices", "devices", dynamic=True,
              help="Each plug, strip, switch or bulb by address - or use “Scan my network” to find them.",
              item_fields=(Field("host", "Address", required=True, placeholder="10.1.1.70"),
                           Field("name", "Name", help="Optional - otherwise the device's own name."))),
        Field("username", "TP-Link account email", placeholder="you@example.com",
              help="Only for Tapo devices and newer Kasa ones - they check it themselves, locally."),
        Field("password", "TP-Link account password", "password",
              help="Only for Tapo devices and newer Kasa ones."),
        Field("poll_seconds", "Check every (seconds)", "number", default=10, advanced=True),
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._devices: dict[str, Any] = {}          # host -> python-kasa Device
        self._targets: dict[str, tuple[Any, str]] = {}  # entity id -> (Device or child, kind)
        self._by_host: dict[str, list[str]] = {}    # host -> entity ids
        self._problems: dict[str, str] = {}         # host -> plain-English problem

    def _credentials(self) -> Any:
        from kasa import Credentials
        if self.config.get("username") and self.config.get("password"):
            return Credentials(str(self.config["username"]).strip(), str(self.config["password"]))
        return None

    # -- "Scan my network" ----------------------------------------------------------------

    async def dynamic_options(self) -> dict[str, list[dict[str, Any]]]:
        """TP-Link devices answering a broadcast right now (only runs when the owner asks)."""
        from kasa import Discover
        found = await Discover.discover(credentials=self._credentials(), discovery_timeout=3)
        added = {d["host"] for d in hosts_from_config(self.config)}
        out = []
        for ip, dev in sorted(found.items()):
            name = _safe(lambda: dev.alias) or _safe(lambda: dev.model) or "TP-Link device"
            model = _safe(lambda: dev.model) or ""
            out.append({"value": ip, "label": f"{name} ({model + ', ' if model and model != name else ''}{ip})",
                        "group": "Already added" if ip in added else "Found"})
            try:
                await dev.disconnect()
            except Exception:
                pass
        return {"devices": out}

    # -- running ---------------------------------------------------------------------------

    async def run(self) -> None:
        specs = hosts_from_config(self.config)
        if not specs:
            self.set_status("pairing", "Open the settings and use “Scan my network” to find your TP-Link devices, "
                                       "or add one by address")
            await asyncio.Event().wait()
        names = {s["host"]: s["name"] for s in specs}
        failures: dict[str, int] = {}
        try:
            while True:
                for host in names:
                    try:
                        if host not in self._devices:
                            await self._connect(host, names[host])
                        else:
                            await self._devices[host].update()
                        await self._publish(host)
                        failures[host] = 0
                        self._problems.pop(host, None)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        failures[host] = failures.get(host, 0) + 1
                        self._problems[host] = self._explain(host, e)
                        for eid in self._by_host.get(host, []):
                            await self.hub.update(eid, available=False)
                        if failures[host] >= 3 and host in self._devices:
                            dev = self._devices.pop(host)  # start afresh next time round
                            _ = await asyncio.gather(dev.disconnect(), return_exceptions=True)
                self._update_status(len(names))
                await asyncio.sleep(float(self.config.get("poll_seconds") or 10))
        finally:
            for dev in self._devices.values():
                await asyncio.gather(dev.disconnect(), return_exceptions=True)

    def _explain(self, host: str, e: Exception) -> str:
        from kasa import AuthenticationError
        if isinstance(e, AuthenticationError):
            if not self._credentials():
                return f"{host} needs your TP-Link account email and password (Tapo and newer Kasa)"
            return f"{host} didn't accept your TP-Link email and password"
        log.info("Kasa %s: %s: %s", host, e.__class__.__name__, e)
        return f"can't reach {host}"

    def _update_status(self, total: int) -> None:
        kinds = [k for _, k in self._targets.values()]
        ok = total - len(self._problems)
        if self._problems and not ok:
            msg = "; ".join(self._problems.values())
            self.set_status("error", msg[:1].upper() + msg[1:])
        elif self._problems:
            self.set_status("error", f"{device_summary(kinds)} · {ok} of {total} on your network · "
                                     + "; ".join(self._problems.values()))
        else:
            self.set_status("connected", f"{device_summary(kinds)} · on your network")

    async def _connect(self, host: str, name: str) -> None:
        from kasa import Discover
        creds = self._credentials()
        try:
            dev = await Discover.discover_single(host, credentials=creds, timeout=10)
        except Exception as e:
            from kasa import AuthenticationError
            if isinstance(e, AuthenticationError):
                raise
            # Some networks drop the discovery packet; try each protocol directly instead.
            dev = await Discover.try_connect_all(host, credentials=creds, timeout=10)
            if dev is None:
                raise
        if dev is None:
            raise ConnectionError("no answer")
        await dev.update()
        self._devices[host] = dev
        self._register(host, dev, name)

    def _register(self, host: str, dev: Any, name: str) -> None:
        kind = KINDS_BY_TYPE.get(str(getattr(dev.device_type, "value", dev.device_type)).lower())
        if kind is None:
            raise ConnectionError(f"{_safe(lambda: dev.model) or 'This device'} isn't a plug, switch or bulb")
        mac = clean_id(dev.mac)
        base = name or dev.alias or dev.model
        eids: list[str] = []
        children = list(dev.children or [])
        if children:
            for i, child in enumerate(children):
                ckind = KINDS_BY_TYPE.get(str(getattr(child.device_type, "value", child.device_type)).lower(), "switch")
                eid = self.entity_id(f"{mac}_{clean_id(child.device_id) or i}")
                self.hub.upsert(eid, kind=ckind, name=child.alias or f"{base} {i + 1}", integration=self.id)
                self._targets[eid] = (child, ckind)
                eids.append(eid)
        else:
            eid = self.entity_id(mac)
            self.hub.upsert(eid, kind=kind, name=base, integration=self.id)
            self._targets[eid] = (dev, kind)
            eids.append(eid)
        self._by_host[host] = eids

    async def _publish(self, host: str) -> None:
        dev = self._devices[host]
        info = {"model": _safe(lambda: dev.model), "host": host,
                "firmware": _safe(lambda: dev.hw_info.get("sw_ver")), "wifiSignal": _safe(lambda: dev.rssi)}
        for eid in self._by_host.get(host, []):
            target, kind = self._targets[eid]
            attrs = dict(info)
            light = self._light(target)
            if kind == "light":
                dimmable = bool(light and _safe(lambda: light.has_feature("brightness"), False))
                attrs["dimmable"] = dimmable
                if dimmable:
                    attrs["brightness"] = _safe(lambda: int(light.brightness))
            if energy := self._module(target, "Energy"):
                attrs.update(power=_safe(lambda: round(float(energy.current_consumption), 2)),
                             energy=_safe(lambda: round(float(energy.consumption_total), 3)),
                             energyToday=_safe(lambda: round(float(energy.consumption_today), 3)),
                             voltage=_safe(lambda: round(float(energy.voltage), 1)),
                             current=_safe(lambda: round(float(energy.current), 3)))
            attrs = {k: v for k, v in attrs.items() if v is not None}
            await self.hub.update(eid, available=True, state="on" if target.is_on else "off", attrs=attrs)

    @staticmethod
    def _module(target: Any, name: str) -> Any:
        mods = getattr(target, "modules", None) or {}
        return _safe(lambda: mods.get(name))

    def _light(self, target: Any) -> Any:
        return self._module(target, "Light")

    # -- commands ---------------------------------------------------------------------------

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        found = self._targets.get(entity_id)
        if not found:
            raise CommandError("That TP-Link device isn't connected")
        target, kind = found
        host = next((h for h, eids in self._by_host.items() if entity_id in eids), None)
        if host not in self._devices:
            raise CommandError("That TP-Link device isn't reachable right now")
        try:
            if command == "turn_on":
                await target.turn_on()
            elif command == "turn_off":
                await target.turn_off()
            elif command == "set_brightness" and kind == "light" and (light := self._light(target)):
                await light.set_brightness(max(1, min(100, int(float(value if value is not None else 100)))))
            else:
                raise CommandError(f"This device can't {command.replace('_', ' ')}")
            await self._devices[host].update()
        except CommandError:
            raise
        except Exception as e:
            raise CommandError(f"The device didn't accept that ({self._explain(host, e)})") from e
        await self._publish(host)
