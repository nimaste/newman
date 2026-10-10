"""What AdGuard Home and Pi-hole share: one "DNS filter" device per server, read every 30 s, with
protection on / off / paused for a while, today's numbers and whether an update is out.

Each connection only says how to read its server (`read()` -> a Reading) and how to switch
protection (`set_protection()`); the polling, events, errors and commands live here.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .base import CommandError, Integration

log = logging.getLogger(__name__)

KIND = "dns_filter"
# Most a pause may last (a day): longer than that, turn it off.
MAX_PAUSE_MIN = 1440


@dataclass
class Reading:
    """One look at the server: protection now, and whatever numbers it reports (missing = not known)."""
    enabled: bool
    resumes_in: float | None = None  # seconds until protection turns itself back on (paused)
    attrs: dict[str, Any] = field(default_factory=dict)


class ServerSaid(Exception):
    """A plain-English problem to show as the connection's status."""


def percent(part: Any, whole: Any) -> float | None:
    try:
        return round(float(part) / float(whole) * 100, 1) if float(whole) else 0.0
    except (TypeError, ValueError):
        return None


class DnsFilterIntegration(Integration):
    category = "servers"
    provides = (KIND,)
    product: str = ""  # "AdGuard Home" - used in status lines

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.address = str(self.config.get("address") or "").strip().rstrip("/")
        if self.address and "://" not in self.address:
            self.address = "http://" + self.address
        self._http = httpx.AsyncClient(base_url=self.address, timeout=15, verify=False)
        self._eid = self.entity_id("filter")
        self._last: str | None = None  # last state seen, for "turned off" / "back on" events

    # -- what each connection supplies ----------------------------------------------------------
    async def read(self) -> Reading:
        raise NotImplementedError

    async def set_protection(self, enabled: bool, minutes: float | None = None) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        """Let go of anything held on the server (e.g. a Pi-hole session)."""

    # -- shared --------------------------------------------------------------------------------
    def _every(self) -> float:
        try:
            return max(5.0, float(self.config.get("poll_every") or 30))
        except (TypeError, ValueError):
            return 30.0

    async def run(self) -> None:
        self.hub.upsert(self._eid, kind=KIND, name=self.display_name, integration=self.id)
        try:
            while True:
                await self.refresh()
                await asyncio.sleep(self._every())
        finally:
            try:
                await self.close()
            except Exception:  # noqa: BLE001 - shutting down anyway
                pass

    async def refresh(self) -> bool:
        """Read the server once and update the device. False (and the device unavailable) on failure."""
        try:
            r = await self.read()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            msg = self._plain(e)
            log.warning("%s: %s", self.display_name, msg)
            self.set_status("error", msg)
            await self.hub.update(self._eid, available=False)
            return False
        state = "on" if r.enabled else ("paused" if r.resumes_in else "off")
        resumes = round(time.time() + r.resumes_in) if state == "paused" else None
        await self.hub.update(self._eid, available=True, state=state, attrs={**r.attrs, "resumesAt": resumes})
        await self._events(state, resumes)
        self.set_status("connected", self._summary(state, r.attrs, resumes))
        return True

    async def _events(self, state: str, resumes: int | None) -> None:
        before, self._last = self._last, state
        if before is None or (before == "on") == (state == "on"):
            return
        if state == "on":
            await self.hub.emit(self._eid, "protection_on", {})
        else:
            detail = f"back on at {time.strftime('%H:%M', time.localtime(resumes))}" if resumes else "until turned back on"
            await self.hub.emit(self._eid, "protection_off", {"detail": detail, "resumesAt": resumes})

    def _summary(self, state: str, attrs: dict[str, Any], resumes: int | None) -> str:
        if state == "on":
            head = "Protecting"
        elif resumes:
            head = f"Paused until {time.strftime('%H:%M', time.localtime(resumes))}"
        else:
            head = "Protection is off"
        if attrs.get("queriesToday") is not None:
            head += f" · {attrs['queriesToday']:,} queries today"
            if attrs.get("blockedPercent") is not None:
                head += f", {attrs['blockedPercent']:g}% blocked"
        return head

    def _plain(self, e: Exception) -> str:
        where = self.address or "(no address)"
        if isinstance(e, ServerSaid):
            return str(e)
        if isinstance(e, httpx.ConnectError):
            return f"Can't reach {self.product} at {where}"
        if isinstance(e, httpx.TimeoutException):
            return f"{self.product} at {where} isn't answering"
        if isinstance(e, httpx.HTTPStatusError):
            return f"{self.product} answered HTTP {e.response.status_code}"
        if isinstance(e, (ValueError, KeyError, TypeError)):
            return f"{where} doesn't look like {self.product} (unexpected answer)"
        return f"{self.product}: {e}"

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        if entity_id != self._eid:
            raise CommandError(f"{self.product} doesn't know that device")
        if command == "turn_on":
            enabled, minutes = True, None
        elif command == "turn_off":
            enabled, minutes = False, None
        elif command == "pause":
            try:
                minutes = float(value if value not in (None, "") else 5)
            except (TypeError, ValueError):
                raise CommandError("Pause for how many minutes?") from None
            if not 0 < minutes <= MAX_PAUSE_MIN:
                raise CommandError(f"Pause for 1 to {MAX_PAUSE_MIN} minutes")
            enabled = False
        else:
            raise CommandError(f"A DNS filter can't {command.replace('_', ' ')}")
        try:
            await self.set_protection(enabled, minutes)
        except CommandError:
            raise
        except Exception as e:  # noqa: BLE001
            raise CommandError(self._plain(e)) from None
        await self.refresh()
