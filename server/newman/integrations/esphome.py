"""ESPHome devices over ESPHome's native API (aioesphomeapi - the library behind Home Assistant's
ESPHome integration). Fully local; the device pushes every change as it happens.

One connection = one ESPHome device. Its switches, lights, sensors, binary sensors, covers (a ratgdo's
garage door becomes a garage door), fans, buttons, sirens and locks each map onto an existing kind.
Diagnostic and configuration entities (Wi-Fi signal, restart buttons…) are left out, like Home
Assistant hides them by default.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from ._common import TaskKeeper, clean_id, device_summary
from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

CONTACT_CLASSES = {"door", "window", "opening", "garage_door"}
MOTION_CLASSES = {"motion", "occupancy", "presence"}
LEAK_CLASSES = {"moisture"}
SMOKE_CLASSES = {"smoke", "gas", "carbon_monoxide"}
LOCK_STATES = {1: "LOCKED", 2: "UNLOCKED", 3: "JAMMED", 4: "LOCKING", 5: "UNLOCKING", 6: "UNLOCKING", 7: "UNLOCKED"}
BINARY_STATES = {"contact": ("open", "closed"), "motion": ("detected", "clear"), "leak": ("wet", "dry"),
                 "smoke": ("detected", "clear")}


def kind_for(info: Any) -> tuple[str, str] | None:
    """(Newman kind, ESPHome platform) for one ESPHome entity, or None to leave it out."""
    import aioesphomeapi as E
    dc = getattr(info, "device_class", "") or ""
    if isinstance(info, E.SwitchInfo):
        return "switch", "switch"
    if isinstance(info, E.LightInfo):
        return "light", "light"
    if isinstance(info, E.SensorInfo):
        unit = (info.unit_of_measurement or "").strip()
        return ("temperature" if dc == "temperature" and unit in ("°C", "C") else "sensor"), "sensor"
    if isinstance(info, E.BinarySensorInfo):
        if getattr(info, "is_status_binary_sensor", False):
            return None  # "is the device connected" - Newman shows that as available / offline
        for kind, classes in (("contact", CONTACT_CLASSES), ("motion", MOTION_CLASSES), ("leak", LEAK_CLASSES),
                              ("smoke", SMOKE_CLASSES)):
            if dc in classes:
                return kind, "binary_sensor"
        return "binary", "binary_sensor"
    if isinstance(info, E.CoverInfo):
        return ("garage" if dc in ("garage", "gate") else "cover"), "cover"
    if isinstance(info, E.FanInfo):
        return "fan", "fan"
    if isinstance(info, E.ButtonInfo):
        return "button", "button"
    if isinstance(info, E.SirenInfo):
        return "siren", "siren"
    if isinstance(info, E.LockInfo):
        return "lock", "lock"
    return None


def _dimmable(info: Any) -> bool:
    import aioesphomeapi as E
    modes = getattr(info, "supported_color_modes", None) or []
    return bool(getattr(info, "legacy_supports_brightness", False)) or \
        any(int(m) & int(E.LightColorCapability.BRIGHTNESS) for m in modes)


class EsphomeIntegration(Integration):
    type = "esphome"
    name = "ESPHome"
    category = "other"
    provides = ("switch", "light", "sensor", "temperature", "contact", "motion", "leak", "smoke", "garage", "cover", "fan", "button", "siren", "lock")  # kinds of device it can bring in (shown on its catalog card)
    description = ("A device running ESPHome (a ratgdo garage opener, plugs, sensors, relays…), connected directly "
                   "on your network. Changes show up the moment they happen.")
    fields = [
        Field("host", "Device address", required=True, placeholder="10.1.1.80",
              help="Found automatically if the device is on your network."),
        Field("encryption_key", "Encryption key", "password",
              help="If the device's ESPHome settings have an api: encryption: key, paste it here."),
        Field("password", "API password (older devices)", "password", advanced=True),
        Field("port", "Port", "number", default=6053, advanced=True),
    ]
    zeroconf = ["_esphomelib._tcp.local."]

    @classmethod
    def from_discovery(cls, name: str, host: str, props: dict[str, str]) -> dict[str, Any] | None:
        title = props.get("friendly_name") or name.split(".")[0]
        note = None
        if props.get("api_encryption"):
            note = "Needs its encryption key (the api: encryption: key in its ESPHome settings)"
        return {"title": f"{title} (ESPHome)", "config": {"host": host}, "note": note}

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._tasks = TaskKeeper()
        self._client: Any = None
        self._mac = ""
        self._device = "ESPHome"
        self._sleeps = False
        self._by_key: dict[tuple[int, int], str] = {}     # (device_id, key) -> entity id
        self._specs: dict[str, dict[str, Any]] = {}       # entity id -> {"kind", "platform", "info"}
        self._battery: float | None = None

    # -- running -----------------------------------------------------------------------------

    async def run(self) -> None:
        import aioesphomeapi as E
        attempt = 0
        while True:
            client = E.APIClient(self.config["host"], int(self.config.get("port") or 6053),
                                 password=self.config.get("password") or "", client_info="Newman",
                                 noise_psk=(self.config.get("encryption_key") or "").strip() or None)
            stopped = asyncio.Event()

            async def on_stop(expected_disconnect: bool) -> None:
                stopped.set()
            try:
                await client.connect(on_stop=on_stop, login=True)
                info, entities, _services = await client.device_info_and_list_entities()
                await self._setup(info, entities)
                self._client = client
                client.subscribe_states(self._on_state)
                self.set_status("connected", f"{self._device} · {device_summary(s['kind'] for s in self._specs.values())}"
                                             " · reports changes instantly")
                attempt = 0
                await stopped.wait()
                raise ConnectionError("connection closed")
            except asyncio.CancelledError:
                raise
            except (E.InvalidEncryptionKeyAPIError, E.RequiresEncryptionAPIError) as e:
                msg = ("This device needs its encryption key - paste it in the settings and save"
                       if isinstance(e, E.RequiresEncryptionAPIError) or not self.config.get("encryption_key")
                       else "The encryption key doesn't match this device - check it and save again")
                self.set_status("error", msg)
                await self._unavailable()
                await asyncio.Event().wait()  # wait for corrected settings (saving restarts us)
            except E.InvalidAuthAPIError:
                self.set_status("error", "The API password was refused - check it and save again")
                await self._unavailable()
                await asyncio.Event().wait()
            except (E.APIConnectionError, OSError, asyncio.TimeoutError) as e:
                log.info("ESPHome %s: %s: %s", self.config["host"], e.__class__.__name__, e)
                if self._sleeps:
                    self.set_status("connected", f"{self._device} · asleep - it reports in when it wakes")
                else:
                    self.set_status("error", f"Can't reach {self._device} at {self.config['host']} - retrying")
                    await self._unavailable()
            finally:
                self._client = None
                try:
                    await client.disconnect()
                except Exception:
                    pass
            attempt += 1
            await self.backoff_sleep(min(attempt, 5))

    async def _unavailable(self) -> None:
        for eid in self._specs:
            await self.hub.update(eid, available=False)

    async def _setup(self, info: Any, entities: list[Any]) -> None:
        import aioesphomeapi as E
        self._mac = clean_id(info.mac_address)
        self._device = info.friendly_name or info.name or "ESPHome"
        self._sleeps = bool(getattr(info, "has_deep_sleep", False))
        self._by_key.clear()
        self._specs.clear()
        found = [(e, kind_for(e)) for e in entities]
        found = [(e, k) for e, k in found if k and getattr(e, "entity_category", None) in (None, E.EntityCategory.NONE)]
        # The device's one "main" thing (a ratgdo's door, a plug's relay) keeps the device's name.
        for ent, (kind, platform) in found:
            own = (ent.name or "").strip()
            if not own or own.lower() == self._device.lower():
                name = self._device
            elif own.lower().startswith(self._device.lower()):
                name = own
            else:
                name = f"{self._device} {own}"
            sub = f"_{ent.device_id}" if getattr(ent, "device_id", 0) else ""
            eid = self.entity_id(f"{self._mac}{sub}_{platform}_{clean_id(ent.object_id) or ent.key}")
            self.hub.upsert(eid, kind=kind, name=name, integration=self.id)
            self._by_key[(getattr(ent, "device_id", 0), ent.key)] = eid
            self._specs[eid] = {"kind": kind, "platform": platform, "info": ent}
            attrs: dict[str, Any] = {"model": info.model or info.project_name or None,
                                     "manufacturer": info.manufacturer or None,
                                     "firmware": info.project_version or info.esphome_version or None,
                                     "host": self.config["host"], "deviceClass": getattr(ent, "device_class", "") or None}
            if kind == "sensor" and ent.unit_of_measurement:
                attrs["unitOfMeasurement"] = ent.unit_of_measurement
            if kind == "light":
                attrs["dimmable"] = _dimmable(ent)
            if kind == "cover" and not ent.supports_position:
                attrs["dimmable"] = False  # no position slider: open / close / stop only
            if kind == "fan" and not (ent.supports_speed and ent.supported_speed_count):
                attrs["dimmable"] = False
            attrs = {k: v for k, v in attrs.items() if v is not None}
            await self.hub.update(eid, available=True, attrs=attrs)

    # -- live states ---------------------------------------------------------------------------

    def _on_state(self, state: Any) -> None:
        eid = self._by_key.get((getattr(state, "device_id", 0), state.key))
        if eid:
            self._tasks.spawn(self._apply(eid, state))

    async def _apply(self, eid: str, st: Any) -> None:
        spec = self._specs[eid]
        kind, info = spec["kind"], spec["info"]
        attrs: dict[str, Any] = {}
        state: str | None = None
        missing = getattr(st, "missing_state", False)
        if kind in ("switch", "siren"):
            state = "on" if st.state else "off"
        elif kind == "light":
            state = "on" if st.state else "off"
            if _dimmable(info) and st.state:  # off keeps the level it'll come back on at
                attrs["brightness"] = max(1, round(st.brightness * 100))
        elif kind == "fan":
            state = "on" if st.state else "off"
            if info.supports_speed and info.supported_speed_count and st.speed_level:
                attrs["percentage"] = round(st.speed_level / info.supported_speed_count * 100)
        elif kind in ("temperature", "sensor"):
            if not missing and st.state == st.state:  # NaN = no reading
                decimals = max(0, int(info.accuracy_decimals or 0))
                state = f"{st.state:.{decimals}f}" if kind == "sensor" else f"{st.state:.1f}"
                if getattr(info, "device_class", "") == "battery":
                    self._battery = round(st.state)
                    await self._share_battery()
        elif kind in BINARY_STATES or kind == "binary":
            if not missing:
                state = BINARY_STATES.get(kind, ("on", "off"))[0 if st.state else 1]
        elif kind in ("cover", "garage"):
            op = int(getattr(st, "current_operation", 0) or 0)
            pos = float(getattr(st, "position", 0.0))
            if info.supports_position:
                attrs["position"] = round(pos * 100)
            state = {1: "opening", 2: "closing"}.get(op) or ("closed" if pos <= 0.0 else "open")
        elif kind == "lock":
            state = LOCK_STATES.get(int(st.state or 0))
        if self._battery is not None and kind not in ("sensor",):
            attrs["batteryLevel"] = self._battery
        await self.hub.update(eid, available=True, **({"state": state} if state is not None else {}),
                              attrs=attrs or None)

    async def _share_battery(self) -> None:
        for eid, spec in self._specs.items():
            if spec["kind"] != "sensor":
                await self.hub.update(eid, attrs={"batteryLevel": self._battery})

    # -- commands -----------------------------------------------------------------------------

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        spec = self._specs.get(entity_id)
        if not spec:
            raise CommandError("That ESPHome device isn't known")
        c = self._client
        if not c:
            raise CommandError(f"{self._device} isn't connected right now")
        kind, info = spec["kind"], spec["info"]
        key, dev = info.key, getattr(info, "device_id", 0)
        kw = {"device_id": dev} if dev else {}
        on = command in ("turn_on", "open", "lock")
        if kind in ("switch",) and command in ("turn_on", "turn_off"):
            result = c.switch_command(key, on, **kw)
        elif kind == "siren" and command in ("turn_on", "turn_off"):
            result = c.siren_command(key, state=on, **kw)
        elif kind == "light" and command in ("turn_on", "turn_off"):
            result = c.light_command(key, state=on, **kw)
        elif kind == "light" and command == "set_brightness" and _dimmable(info):
            result = c.light_command(key, state=True, brightness=max(1, min(100, float(value))) / 100, **kw)
        elif kind == "fan" and command in ("turn_on", "turn_off"):
            result = c.fan_command(key, state=on, **kw)
        elif kind == "fan" and command == "set_speed" and info.supports_speed and info.supported_speed_count:
            level = max(1, round(float(value) / 100 * info.supported_speed_count))
            result = c.fan_command(key, state=True, speed_level=level, **kw)
        elif kind in ("cover", "garage") and command in ("open", "close"):
            result = c.cover_command(key, position=1.0 if command == "open" else 0.0, **kw)
        elif kind == "cover" and command == "stop":
            result = c.cover_command(key, stop=True, **kw)
        elif kind == "cover" and command == "set_position" and info.supports_position:
            result = c.cover_command(key, position=max(0, min(100, float(value))) / 100, **kw)
        elif kind == "button" and command == "press":
            result = c.button_command(key, **kw)
        elif kind == "lock" and command in ("lock", "unlock"):
            import aioesphomeapi as E
            result = c.lock_command(key, E.LockCommand.LOCK if on else E.LockCommand.UNLOCK, **kw)
        else:
            raise CommandError(f"{self.hub.name(entity_id)} can't {command.replace('_', ' ')}")
        if inspect.isawaitable(result):  # older library versions made these coroutines
            await result
