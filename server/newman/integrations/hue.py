"""Philips Hue, through the Hue bridge on your network (aiohue - the library behind Home Assistant's
Hue integration). Needs a square (v2) bridge; nothing goes through Hue's cloud.

Pairing is one button press: while the round button on the bridge has just been pressed, the
bridge hands out an app key. Newman keeps that key in its data folder (never in the settings), so
this happens once. Changes arrive live over the bridge's event stream.

Lights become lights; motion, temperature, light-level and contact sensors become motion,
temperature, sensor and door-sensor devices; Hue dimmers, Tap switches and buttons become remotes
whose buttons can start automations (like a Lutron Pico).
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from ._common import TaskKeeper, clean_id, device_summary
from .base import CommandError, Field, Integration, check_every_field

log = logging.getLogger(__name__)

PAIR_FOR_S = 300   # keep asking the bridge this long after a save, waiting for the button press
PAIR_EVERY_S = 3
CONNECTED_CHECK_S = 30  # how often a running connection checks the bridge's event stream is up


def _v(obj: Any, *path: str) -> Any:
    """Follow attributes, tolerating anything missing (Hue resources vary by model and firmware)."""
    for p in path:
        obj = getattr(obj, p, None)
        if obj is None:
            return None
    return obj


def _enum(v: Any) -> str:
    return str(getattr(v, "value", v) or "")


def lux(light_level: Any) -> int | None:
    """Hue reports light level as 10000 * log10(lux) + 1."""
    try:
        return round(10 ** ((float(light_level) - 1) / 10000))
    except (TypeError, ValueError):
        return None


class HueIntegration(Integration):
    type = "hue"
    name = "Philips Hue"
    category = "lighting"
    provides = ("light", "motion", "temperature", "sensor", "contact", "remote")  # kinds of device it can bring in (shown on its catalog card)
    description = ("Hue lights, motion sensors and remotes through your Hue bridge, entirely on your network. When "
                   "you connect, press the round button on top of the bridge - that's the whole setup.")
    fields = [Field("host", "Bridge address", required=True, placeholder="10.1.1.40",
                    help="Found automatically if the bridge is on your network."),
              check_every_field(15, 1, help="The bridge sends changes as they happen; this also asks it for "
                                            "everything now and then, in case one was missed. The bridge answers "
                                            "from what it holds - no sensor or remote is woken.")]
    zeroconf = ["_hue._tcp.local."]

    @classmethod
    def from_discovery(cls, name: str, host: str, props: dict[str, str]) -> dict[str, Any] | None:
        model = props.get("modelid", "")
        note = "This is a first-generation (round) bridge - Newman needs the square one" if model == "BSB001" else None
        return {"title": "Philips Hue bridge", "config": {"host": host}, "note": note}

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._dir = Path(self.ctx.data_dir) / "integrations" / self.id
        self._tasks = TaskKeeper()
        self._bridge: Any = None
        self._handlers: dict[str, tuple[str, str]] = {}   # Hue resource id -> (entity id, what it reports)
        self._devices: dict[str, list[str]] = {}          # Hue device id -> its entity ids
        self._kinds: dict[str, str] = {}                  # entity id -> kind
        self._battery: dict[str, int] = {}                # Hue device id -> battery %

    # -- pairing ------------------------------------------------------------------------

    @property
    def _key_file(self) -> Path:
        return self._dir / "app_key"

    async def _app_key(self) -> str:
        if self._key_file.is_file() and self._key_file.read_text().strip():
            return self._key_file.read_text().strip()
        from aiohue import create_app_key
        from aiohue.errors import LinkButtonNotPressed
        host = self.config["host"]
        until = time.monotonic() + PAIR_FOR_S
        self.set_status("pairing", "Press the round button on top of the Hue bridge now")
        while True:
            try:
                key = await create_app_key(host, "newman#hub")
                break
            except LinkButtonNotPressed:
                if time.monotonic() > until:
                    self.set_status("pairing", "Press the round button on top of the Hue bridge, then save")
                    await asyncio.Event().wait()  # saving restarts us and the wait begins again
            except Exception as e:
                self.set_status("error", f"Can't reach the Hue bridge at {host} ({e.__class__.__name__}) - retrying")
                await asyncio.sleep(30)
                self.set_status("pairing", "Press the round button on top of the Hue bridge now")
                continue
            await asyncio.sleep(PAIR_EVERY_S)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._key_file.write_text(key)
        self._key_file.chmod(0o600)
        log.info("Paired with the Hue bridge at %s", host)
        return key

    # -- running --------------------------------------------------------------------------

    async def run(self) -> None:
        from aiohue import HueBridgeV2
        from aiohue.errors import Unauthorized
        attempt = 0
        while True:
            key = await self._app_key()
            bridge = HueBridgeV2(self.config["host"], key)
            try:
                await asyncio.wait_for(bridge.initialize(), timeout=60)
                self._bridge = bridge
                await self._setup(bridge)
                unsubscribe = bridge.subscribe(self._on_event)
                attempt = 0
                recheck = asyncio.get_running_loop().create_task(self._recheck(bridge))
                try:
                    while True:
                        await asyncio.sleep(CONNECTED_CHECK_S)
                        live = bool(_v(bridge, "events", "connected"))
                        if live:
                            self._connected()
                        else:
                            self.set_status("connecting", "Reconnecting to the Hue bridge…")
                finally:
                    recheck.cancel()
                    unsubscribe()
            except asyncio.CancelledError:
                raise
            except Unauthorized:
                # The app key was removed from the bridge (e.g. in the Hue app): pair again.
                self._key_file.unlink(missing_ok=True)
                self.set_status("pairing", "The Hue bridge forgot Newman - press the round button on the bridge")
            except Exception as e:
                log.info("Hue %s: %s: %s", self.config["host"], e.__class__.__name__, e)
                self.set_status("error", f"Can't reach the Hue bridge at {self.config['host']} - retrying")
                for eid in self._kinds:
                    await self.hub.update(eid, available=False)
            finally:
                self._bridge = None
                try:
                    await bridge.close()
                except Exception:
                    pass
            attempt += 1
            await self.backoff_sleep(attempt + 1)

    async def _recheck(self, bridge: Any) -> None:
        """While connected: every "Check everything every" minutes ask the bridge for everything again. aiohue
        treats it as after a long disconnect - what changed comes through the usual update events (_on_event)."""
        while True:
            await asyncio.sleep(self.seconds_every("check_minutes"))
            try:
                await bridge.fetch_full_state()
            except Exception as e:  # noqa: BLE001 - asked again next time
                log.info("Hue %s: couldn't ask for everything: %s: %s", self.config["host"], e.__class__.__name__, e)

    def _connected(self) -> None:
        self.set_status("connected", f"{device_summary(self._kinds.values())} · on your network")

    async def _setup(self, bridge: Any) -> None:
        self._handlers.clear()
        self._devices.clear()
        devices = {d.id: d for d in bridge.devices}

        def device_of(res: Any) -> Any:
            return devices.get(_v(res, "owner", "rid"))

        def add(local: str, kind: str, name: str, dev: Any, res_id: str, what: str) -> str:
            eid = self.entity_id(local)
            if eid not in self._kinds:
                self.hub.upsert(eid, kind=kind, name=name, integration=self.id)
                self._kinds[eid] = kind
                if dev is not None:
                    self._devices.setdefault(dev.id, []).append(eid)
            self._handlers[res_id] = (eid, what)
            return eid

        for p in bridge.sensors.device_power:
            if (level := _v(p, "power_state", "battery_level")) is not None:
                self._battery[_v(p, "owner", "rid")] = level
        for light in bridge.lights:
            dev = device_of(light)
            name = _v(dev, "metadata", "name") or _v(light, "metadata", "name") or "Hue light"
            eid = add(f"light_{clean_id(light.id)}", "light", name, dev, light.id, "light")
            await self.hub.update(eid, attrs=self._info(dev) | {"dimmable": light.dimming is not None})
        sensors = bridge.sensors
        groups = (("motion", "motion", ""), ("temperature", "temperature", " temperature"),
                  ("light_level", "sensor", " light level"), ("contact", "contact", ""))
        for attr, kind, suffix in groups:
            for res in getattr(sensors, attr, []):
                dev = device_of(res)
                base = _v(dev, "metadata", "name") or "Hue sensor"
                eid = add(f"{attr}_{clean_id(_v(dev, 'id') or res.id)}", kind, base + suffix, dev, res.id, attr)
                extra = {"unitOfMeasurement": "lx", "deviceClass": "illuminance"} if attr == "light_level" else {}
                await self.hub.update(eid, attrs=self._info(dev) | extra)
        remotes: dict[str, list[str]] = {}
        for btn in sensors.button:
            dev = device_of(btn)
            n = _v(btn, "metadata", "control_id")
            if dev is None or not isinstance(n, int) or not 1 <= n <= 4:
                continue
            add(f"remote_{clean_id(dev.id)}", "remote", _v(dev, "metadata", "name") or "Hue remote", dev, btn.id,
                f"button_{n}")
            remotes.setdefault(dev.id, []).append(f"button_{n}")
        for dev_id, events in remotes.items():
            await self.hub.update(self.entity_id(f"remote_{clean_id(dev_id)}"), state="idle", available=True,
                                  attrs=self._info(devices.get(dev_id)) | {"events": sorted(events)})
        # Current values of everything just registered; then each device's own reachability.
        for eid in self._kinds:
            await self.hub.update(eid, available=True)
        for res in [*bridge.lights, *(r for a, _, _ in groups for r in getattr(sensors, a, [])),
                    *getattr(sensors, "zigbee_connectivity", [])]:
            await self._apply(res)
        self._connected()

    def _info(self, dev: Any) -> dict[str, Any]:
        out = {"model": _v(dev, "product_data", "product_name"), "manufacturer": _v(dev, "product_data", "manufacturer_name"),
               "firmware": _v(dev, "product_data", "software_version"), "host": self.config["host"]}
        if dev is not None and dev.id in self._battery:
            out["batteryLevel"] = self._battery[dev.id]
        return {k: v for k, v in out.items() if v is not None}

    # -- live updates --------------------------------------------------------------------------

    def _on_event(self, event_type: Any, resource: Any) -> None:
        if _enum(event_type) == "update" and resource is not None:
            self._tasks.spawn(self._apply(resource))

    async def _apply(self, res: Any) -> None:
        rtype = _enum(getattr(res, "type", ""))
        if rtype == "device_power":
            dev_id, level = _v(res, "owner", "rid"), _v(res, "power_state", "battery_level")
            if level is not None:
                self._battery[dev_id] = level
                for eid in self._devices.get(dev_id, []):
                    await self.hub.update(eid, attrs={"batteryLevel": level})
            return
        if rtype in ("zigbee_connectivity", "zgp_connectivity"):
            ok = _enum(_v(res, "status")) == "connected"
            for eid in self._devices.get(_v(res, "owner", "rid"), []):
                await self.hub.update(eid, available=ok)
            return
        found = self._handlers.get(res.id)
        if not found:
            return
        eid, what = found
        if what == "light":
            attrs = {}
            if (b := _v(res, "dimming", "brightness")) is not None:
                attrs["brightness"] = max(1, round(b))
            on = _v(res, "on", "on")  # reachability comes from the device's zigbee_connectivity, not here
            await self.hub.update(eid, **({"state": "on" if on else "off"} if on is not None else {}),
                                  attrs=attrs or None)
        elif what == "motion":
            m = _v(res, "motion", "motion_report", "motion")
            m = _v(res, "motion", "motion") if m is None else m
            if m is not None:
                await self.hub.update(eid, state="detected" if m else "clear")
        elif what == "temperature":
            t = _v(res, "temperature", "temperature_report", "temperature")
            t = _v(res, "temperature", "temperature") if t is None else t
            if t is not None:
                await self.hub.update(eid, state=f"{float(t):.1f}")
        elif what == "light_level":
            lvl = _v(res, "light", "light_level_report", "light_level")
            lvl = _v(res, "light", "light_level") if lvl is None else lvl
            if (lx := lux(lvl)) is not None:
                await self.hub.update(eid, state=str(lx))
        elif what == "contact":
            c = _enum(_v(res, "contact_report", "state"))
            if c:
                await self.hub.update(eid, state="closed" if c == "contact" else "open")
        elif what.startswith("button_"):
            ev = _enum(_v(res, "button", "button_report", "event")) or _enum(_v(res, "button", "last_event"))
            if ev == "initial_press":  # like a Pico: the moment it's pressed (Tap switches only send this)
                await self.hub.emit(eid, what, {})

    # -- commands --------------------------------------------------------------------------------

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        if not self._bridge:
            raise CommandError("The Hue bridge isn't connected right now")
        light_id = next((rid for rid, (eid, what) in self._handlers.items() if eid == entity_id and what == "light"),
                        None)
        if not light_id:
            raise CommandError(f"{self.hub.name(entity_id)} can't {command.replace('_', ' ')}")
        lights = self._bridge.lights
        try:
            if command == "turn_on":
                await lights.turn_on(light_id)
            elif command == "turn_off":
                await lights.turn_off(light_id)
            elif command == "set_brightness":
                await lights.set_brightness(light_id, max(1, min(100, float(value if value is not None else 100))))
            else:
                raise CommandError(f"A Hue light can't {command.replace('_', ' ')}")
        except CommandError:
            raise
        except Exception as e:
            raise CommandError(f"The Hue bridge didn't accept that ({e.__class__.__name__})") from e
