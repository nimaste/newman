"""Rain Bird sprinkler controllers with a LNK / LNK2 Wi-Fi module (or built-in Wi-Fi: ESP-ME3, ESP-TM2, ARC8…),
locally through pyrainbird - the library Home Assistant uses.

Rain Bird's local protocol has no tokens: every request is encrypted with the controller's own password
(the one set in the Rain Bird app), so Newman has to keep that password. It's stored masked and never
shown back.

The controller handles one request at a time and answers "busy" otherwise, so Newman asks gently: every
minute when idle, every 20 seconds while a zone waters, one request at a time.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any, Callable

import aiohttp

from ._common import plural
from ._sprinklers import command_number, controller_state, set_zone
from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

WATERING_POLL_S = 20       # while a zone waters (or sooner, if the setting is shorter)


def _create(session: aiohttp.ClientSession, host: str, password: str) -> Any:
    from pyrainbird.async_client import CreateController  # imported here: only this connection needs it
    return CreateController(session, host, password)


def _auth_error(e: Exception) -> bool:
    return type(e).__name__ == "RainbirdAuthException"


class RainBirdIntegration(Integration):
    type = "rainbird"
    name = "Rain Bird"
    category = "other"
    provides = ("sprinkler", "sprinkler_zone")
    description = ("Rain Bird controllers with a LNK / LNK2 Wi-Fi module or built-in Wi-Fi, locally: what's watering, "
                   "rain sensor, rain delay; water a zone for N minutes, stop, set a rain delay. Rain Bird has no "
                   "tokens - the controller's own password goes with every request, so Newman keeps it (masked).")
    fields = [
        Field("host", "Controller address", required=True, placeholder="192.168.1.60",
              help="The Wi-Fi module's IP address (Rain Bird app → Controller settings → Network info, or your "
                   "router's device list). Give it a fixed address in the router."),
        Field("password", "Controller password", "password", required=True,
              help="The password set for the controller in the Rain Bird app. Rain Bird's local protocol has no "
                   "tokens, so this is kept (masked) and sent, encrypted, with every request."),
        Field("poll_seconds", "Check every (seconds)", "number", default=60, minimum=20, advanced=True,
              help="While a zone waters Newman checks every 20 seconds. The controller takes one request at a "
                   "time, so don't go much lower."),
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._factory: Callable[[aiohttp.ClientSession], Any] = \
            lambda s: _create(s, str(self.config.get("host") or "").strip(), str(self.config.get("password") or ""))
        self._ctl: Any = None
        self._lock = asyncio.Lock()   # the controller takes one request at a time
        self._wake = asyncio.Event()
        self._zones: dict[str, int] = {}   # entity id -> station number
        self._ctrl_eid = self.entity_id("controller")
        self._combined = True  # does this model answer the combined-state request (time left)?

    # -- running --------------------------------------------------------------------------

    def poll_wait(self, watering: bool) -> float:
        """Seconds until the next check: the setting, or WATERING_POLL_S while a zone waters."""
        idle = self.seconds_every("poll_seconds", unit=1)
        return min(WATERING_POLL_S, idle) if watering else idle

    async def run(self) -> None:
        attempt = 0
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    if self._ctl is None:
                        self._ctl = self._factory(session)
                        await self._setup()
                    watering = await self.poll()
                    attempt = 0
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), self.poll_wait(watering))
                        await asyncio.sleep(2)
                    except asyncio.TimeoutError:
                        pass
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if _auth_error(e):
                        self.set_status("error", "The controller refused the password - check it in the Rain Bird app")
                        self._ctl = None
                        await asyncio.sleep(600)
                        continue
                    log.warning("Rain Bird: %s", e)
                    self.set_status("error", f"Rain Bird: {e}")
                    if self.hub.get(self._ctrl_eid):
                        await self.hub.update(self._ctrl_eid, available=False)
                    await self.backoff_sleep(attempt + 4)
                    attempt += 1

    async def _setup(self) -> None:
        async with self._lock:
            model = await self._ctl.get_model_and_version()
            stations = await self._ctl.get_available_stations()
            try:
                serial = await self._ctl.get_serial_number()
            except Exception:  # some models don't say
                serial = ""
        info = getattr(model, "model_info", None)
        name = getattr(info, "name", None) or "Rain Bird"
        self.hub.upsert(self._ctrl_eid, kind="sprinkler", name=name if name.startswith("Rain") else f"Rain Bird {name}",
                        integration=self.id)
        await self.hub.update(self._ctrl_eid, attrs={
            "manufacturer": "Rain Bird", "model": name, "host": self.config.get("host"),
            "serial": serial if serial and str(serial).strip("0") else "",
            "firmware": f"{getattr(model, 'major', '')}.{getattr(model, 'minor', '')}"})
        for n in sorted(stations.active_set):
            eid = self.entity_id(f"zone_{n}")
            self._zones[eid] = n
            self.hub.upsert(eid, kind="sprinkler_zone", name=f"Zone {n}", integration=self.id)
            await self.hub.update(eid, attrs={"zoneNumber": n})

    async def poll(self) -> bool:
        """Read zone states, rain sensor and rain delay; returns whether anything is watering."""
        async with self._lock:
            states = await self._ctl.get_zone_states()
            rain = await self._ctl.get_rain_sensor_state()
            delay = await self._ctl.get_rain_delay()
            remaining = None
            if self._combined:
                try:
                    combined = await self._ctl.get_combined_controller_state()
                    remaining = getattr(combined, "remaining_runtime", None)
                except Exception:  # older models don't have it; don't ask again
                    self._combined = False
        active = [eid for eid, n in self._zones.items() if states.active(n)]
        mins = round(remaining / 60) if isinstance(remaining, (int, float)) and remaining > 0 else None
        for eid, n in self._zones.items():
            if eid in active:
                await set_zone(self.hub, eid, True, remaining=mins)
            else:
                await set_zone(self.hub, eid, False)
        delay = int(delay or 0)
        await self.hub.update(self._ctrl_eid, available=True, state=controller_state(bool(active), delayed=delay > 0),
                              attrs={"currentZone": ", ".join(self.hub.name(e) for e in active) or None,
                                     "remaining": mins if active else None,
                                     "rainSensor": "wet" if rain else "dry",
                                     "rainDelayDays": delay,
                                     "rainDelayUntil": (date.today() + timedelta(days=delay)).isoformat() if delay else None})
        self.set_status("connected", plural(len(self._zones), "zone"))
        return bool(active)

    # -- commands -------------------------------------------------------------------------

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        if self._ctl is None:
            raise CommandError("The Rain Bird controller isn't connected")
        try:
            async with self._lock:
                if entity_id in self._zones:
                    if command == "run":
                        await self._ctl.irrigate_zone(self._zones[entity_id],
                                                      command_number("sprinkler_zone", "run", value))
                    elif command == "stop":  # Rain Bird only knows "stop watering" (whichever zone)
                        await self._ctl.stop_irrigation()
                    else:
                        raise CommandError(f"A sprinkler zone can't {command.replace('_', ' ')}")
                elif entity_id == self._ctrl_eid:
                    if command == "stop":
                        await self._ctl.stop_irrigation()
                    elif command == "rain_delay":
                        await self._ctl.set_rain_delay(command_number("sprinkler", "rain_delay", value))
                    elif command in ("turn_off", "turn_on"):
                        raise CommandError("Rain Bird controllers can't be put on standby over Wi-Fi - use a rain "
                                           "delay, or turn the dial to Off")
                    else:
                        raise CommandError(f"A sprinkler controller can't {command.replace('_', ' ')}")
                else:
                    raise CommandError("Unknown Rain Bird device")
        except CommandError:
            raise
        except Exception as e:
            raise CommandError(f"Rain Bird: {e}") from None
        self._wake.set()
