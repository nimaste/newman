"""Lutron Caséta (Smart Bridge / Smart Bridge Pro), fully local over LEAP.

Setup is one button press: the first time it runs, the integration asks the
bridge for a client certificate, which the bridge only grants while its small
black button has just been pressed. The certificate is kept in the data dir,
so this happens once. Uses pylutron-caseta - the library behind Home
Assistant's Lutron Caséta integration.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from pylutron_caseta import BUTTON_STATUS_PRESSED, BUTTON_STATUS_RELEASED
from pylutron_caseta.pairing import async_pair
from pylutron_caseta.smartbridge import Smartbridge

from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

PAIR_WINDOW_S = 180
LIGHT_TYPES = {"light", "switch"}
# A Pico button still down this long is a long press ("raise_long"); let go sooner, it's a plain press.
HOLD_S = 0.5

# LEAP button_number -> event, per Pico model (same mapping Home Assistant uses).
PICO_BUTTONS = {
    "Pico2Button": {0: "on", 2: "off"},
    "Pico2ButtonRaiseLower": {0: "on", 2: "off", 3: "raise", 4: "lower"},
    "Pico3Button": {0: "on", 1: "favorite", 2: "off"},
    "Pico3ButtonRaiseLower": {0: "on", 1: "favorite", 2: "off", 3: "raise", 4: "lower"},
}


def _names(devices: dict[str, dict], areas: dict[str, str]) -> dict[str, str]:
    """Display names: the device's own name ("Kitchen"), not the bridge's "Kitchen_Kitchen";
    the room is added only where two devices would otherwise share a name."""
    base = {i: (d.get("device_name") or d.get("name", "").replace("_", " ")).strip() for i, d in devices.items()}
    counts: dict[str, int] = {}
    for n in base.values():
        counts[n] = counts.get(n, 0) + 1
    out = {}
    for i, n in base.items():
        area = areas.get(str(devices[i].get("area")), "")
        out[i] = f"{area} {n}" if counts[n] > 1 and area and area != "root" else n
    return out


class LutronIntegration(Integration):
    type = "lutron"
    name = "Lutron Caséta"
    category = "lighting"
    provides = ("light", "remote")  # kinds of device it can bring in (shown on its catalog card)
    description = ("Pairs locally with a Caséta Smart Bridge - you'll be asked to press the small black button on "
                   "the back of the bridge once. Dimmers and switches appear under Lighting; Pico remote buttons "
                   "can start automations.")
    fields = [Field("host", "Bridge address", required=True, placeholder="10.1.1.23",
                    help="Found automatically if the bridge is on your network.")]
    zeroconf = ["_lutron._tcp.local."]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._bridge: Smartbridge | None = None
        self._dir = self.ctx.data_dir / "integrations" / self.id
        self._device_ids: dict[str, str] = {}  # entity id -> bridge device id
        # Pico buttons that are down: (remote id, event) -> the timer that makes it a long press, or None once
        # it is one (see _on_button).
        self._down: dict[tuple[str, str], asyncio.TimerHandle | None] = {}

    @classmethod
    def from_discovery(cls, name: str, host: str, props: dict[str, str]) -> dict[str, Any] | None:
        return {"title": "Lutron Smart Bridge", "config": {"host": host}}

    # -- certificates -------------------------------------------------------------------

    @property
    def _files(self) -> tuple[Path, Path, Path]:
        return self._dir / "client.key", self._dir / "client.crt", self._dir / "bridge-ca.crt"

    def _paired(self) -> bool:
        return all(p.is_file() and p.stat().st_size for p in self._files)

    async def _pair(self) -> None:
        host = self.config["host"]
        self.set_status("pairing", f"Press the small black button on the back of the Smart Bridge ({host}) now")
        while True:
            try:
                data = await asyncio.wait_for(async_pair(host), timeout=PAIR_WINDOW_S)
                break
            except asyncio.TimeoutError:
                self.set_status("pairing", "Still waiting - press the small black button on the back of the "
                                           f"Smart Bridge ({host})")
            except OSError as e:
                self.set_status("error", f"Can't reach the bridge at {host}: {e}")
                await asyncio.sleep(30)
                self.set_status("pairing", f"Press the small black button on the back of the Smart Bridge ({host})")
        self._dir.mkdir(parents=True, exist_ok=True)
        key, cert, ca = self._files
        key.write_text(data["key"])
        cert.write_text(data["cert"])
        ca.write_text(data["ca"])
        for p in self._files:
            p.chmod(0o600)
        log.info("Paired with Lutron bridge %s", host)

    # -- running --------------------------------------------------------------------------

    async def run(self) -> None:
        if not self._paired():
            await self._pair()
        attempt = 0
        while True:
            try:
                await self._connect()
                attempt = 0
                while self._bridge and self._bridge.is_connected():
                    await asyncio.sleep(30)
                raise ConnectionError("connection lost")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.set_status("error", f"{self.config['host']}: {e}")
            finally:
                await self._close()
            attempt += 1
            await self.backoff_sleep(attempt + 1)

    async def _connect(self) -> None:
        self.set_status("connecting", self.config["host"])
        key, cert, ca = self._files
        bridge = Smartbridge.create_tls(self.config["host"], str(key), str(cert), str(ca))
        await asyncio.wait_for(bridge.connect(), timeout=30)
        if not bridge.is_connected():
            raise ConnectionError("bridge refused the connection")
        self._bridge = bridge
        devices = bridge.get_devices()
        areas = {k: v.get("name", "") for k, v in (getattr(bridge, "areas", None) or {}).items()}
        names = _names(devices, areas)
        lights = 0
        for dev_id, dev in devices.items():
            domain = next((d for d, types in _domains().items() if dev.get("type") in types), None)
            if domain in LIGHT_TYPES:
                eid = self.entity_id(f"device_{dev_id}")
                self._device_ids[eid] = dev_id
                self.hub.upsert(eid, kind="light", name=names[dev_id], integration=self.id)
                await self._sync(eid, dev, dimmable=domain == "light",
                                 area=areas.get(str(dev.get("area")), ""))
                bridge.add_subscriber(dev_id, self._on_update(eid, dev_id, domain == "light"))
                lights += 1
        # One "remote" device per Pico; each physical button becomes its events: pressed, long-pressed and let go.
        remotes: dict[str, list[str]] = {}
        for btn_id, btn in bridge.get_buttons().items():
            parent_id = str(btn.get("parent_device"))
            parent = devices.get(parent_id, {})
            mapping = PICO_BUTTONS.get(parent.get("type", ""), {})
            num = btn.get("button_number")
            event = mapping.get(num, f"button_{int(num) + 1}" if isinstance(num, int) and num < 4 else None)
            if not event:
                continue
            eid = self.entity_id(f"remote_{parent_id}")
            if parent_id not in remotes:
                remotes[parent_id] = []
                self.hub.upsert(eid, kind="remote", name=names.get(parent_id, "Remote"), integration=self.id)
            remotes[parent_id] += [event, f"{event}_long", f"{event}_released"]
            bridge.add_button_subscriber(btn_id, self._on_button(eid, event))
        for parent_id, events in remotes.items():
            await self.hub.update(self.entity_id(f"remote_{parent_id}"), state="idle", available=True, attrs={
                "events": events, "model": devices.get(parent_id, {}).get("model"),
                "area": areas.get(str(devices.get(parent_id, {}).get("area")), "")})
        n = len(remotes)
        self.set_status("connected", f"{self.config['host']} · {lights} light{'s' if lights != 1 else ''}"
                                     + (f", {n} remote{'s' if n != 1 else ''}" if n else ""))

    async def _close(self) -> None:
        bridge, self._bridge = self._bridge, None
        for timer in self._down.values():
            if timer:
                timer.cancel()
        self._down.clear()
        for eid in self._device_ids:
            await self.hub.update(eid, available=False)
        if bridge:
            try:
                await bridge.close()
            except Exception:
                pass

    async def _sync(self, eid: str, dev: dict[str, Any], dimmable: bool, area: str | None = None) -> None:
        level = dev.get("current_state")
        level = int(level) if isinstance(level, (int, float)) and level >= 0 else None
        attrs = {"brightness": level, "dimmable": dimmable, "model": dev.get("model")}
        if area is not None:
            attrs["area"] = area
        await self.hub.update(eid, available=True, state=("on" if level else "off") if level is not None else None,
                              attrs=attrs)

    def _on_update(self, eid: str, dev_id: str, dimmable: bool):
        def cb() -> None:
            if self._bridge:
                dev = self._bridge.get_devices().get(dev_id, {})
                asyncio.get_running_loop().create_task(self._sync(eid, dev, dimmable))
        return cb

    def _on_button(self, eid: str, event: str):
        """A Pico button's Press / Release -> its remote's events. Let go within HOLD_S, it's a press ("raise",
        fired on the release); still down after HOLD_S, it's a long press ("raise_long") and letting go is
        "raise_released" - so holding a button is never also a press."""
        key = (eid, event)

        def emit(name: str) -> None:
            asyncio.get_running_loop().create_task(self.hub.emit(eid, name, {}))

        def held() -> None:
            if key in self._down:
                self._down[key] = None
                emit(f"{event}_long")

        def cb(event_type: str) -> None:
            if event_type == BUTTON_STATUS_PRESSED:
                if key not in self._down:  # a repeated Press while it's down changes nothing
                    self._down[key] = asyncio.get_running_loop().call_later(HOLD_S, held)
            elif event_type == BUTTON_STATUS_RELEASED and key in self._down:
                timer = self._down.pop(key)
                if timer:
                    timer.cancel()
                    emit(event)
                else:
                    emit(f"{event}_released")
        return cb

    # -- commands -------------------------------------------------------------------------

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        dev_id = self._device_ids.get(entity_id)
        if not dev_id or not self._bridge:
            raise CommandError("The Lutron bridge isn't connected")
        ent = self.hub.get(entity_id)
        dimmable = bool(ent and ent.attrs.get("dimmable"))
        if command == "turn_on":
            if dimmable:
                await self._bridge.set_value(dev_id, 100 if value is None else int(value))
            else:
                await self._bridge.turn_on(dev_id)
        elif command == "turn_off":
            await self._bridge.turn_off(dev_id)
        elif command == "set_brightness" and dimmable:
            await self._bridge.set_value(dev_id, max(0, min(100, int(value if value is not None else 100))))
        else:
            raise CommandError(f"{ent.name if ent else 'This light'} can't {command.replace('_', ' ')}")


def _domains() -> dict[str, list[str]]:
    import pylutron_caseta
    return pylutron_caseta._LEAP_DEVICE_TYPES  # type -> domain table the library itself uses
