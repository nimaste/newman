"""Hunter Hydrawise sprinkler controllers, through Hydrawise's API with the account's API key.

Hydrawise's key-based API (the "v1" one the Hydrawise apps' API-key page documents, and what pydrawise's
RestClient speaks) is used directly: newer GraphQL access needs the account password on file, and Newman
keeps only keys. It reports every zone's next run, what's watering and how long is left; it can run a zone
for N minutes, stop, and suspend (rain delay / standby). It doesn't report the rain sensor.

Hydrawise says how soon to ask again ("nextpoll"); Newman never asks sooner than that, nor more than once
a minute.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from ._common import plural
from ._sprinklers import command_number, controller_state, iso, set_zone
from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

API = "https://api.hydrawise.com/api/v1"
RUNNING = 1                # a relay's "time" while it waters
SUSPENDED = 1576800000     # a relay's "time" while suspended (or held off by a sensor)
STANDBY_DAYS = 365
MIN_POLL_S = 60
RELOAD_S = 3600            # re-read the list of controllers (names, last contact) hourly


class BadKey(Exception):
    pass


class HydrawiseIntegration(Integration):
    type = "hydrawise"
    name = "Hunter Hydrawise"
    category = "other"
    provides = ("sprinkler", "sprinkler_zone")
    description = ("Hunter Hydrawise sprinkler controllers (HPC, Pro-HC, HC, X2…): what's watering and for how long, "
                   "each zone's next run; water a zone for N minutes, stop, rain delay and standby. Uses the "
                   "Hydrawise account's API key - no password.")
    fields = [
        Field("api_key", "API key", "password", required=True,
              help="In app.hydrawise.com (or the Hydrawise app): Account → Account Details → API Key → Generate. "
                   "Copy the key here."),
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._http = httpx.AsyncClient(timeout=30)
        self._ctrls: dict[str, str] = {}                  # controller id -> entity id
        self._zones: dict[str, tuple[str, str]] = {}      # zone entity id -> (relay id, controller id)
        self._delay_until: dict[str, float] = {}          # controller id -> rain delay end Newman set
        self._next_poll = MIN_POLL_S
        self._wake = asyncio.Event()

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        key = str(self.config.get("api_key") or "")
        try:
            r = await self._http.get(f"{API}/{path}", params={"api_key": key, **params})
        except httpx.HTTPError as e:  # never let the key (it's in the address) reach a message
            raise CommandError(f"couldn't reach Hydrawise ({type(e).__name__})") from None
        if r.status_code in (401, 403) or (r.status_code == 404 and "API key not valid" in r.text):
            raise BadKey("Hydrawise refused the API key - generate a new one and paste it here")
        if r.status_code == 429:
            raise CommandError("Hydrawise asked Newman to slow down; try again in a minute")
        if r.status_code >= 400:
            raise CommandError(f"Hydrawise said no (HTTP {r.status_code})")
        try:
            j = r.json()
        except ValueError:
            raise CommandError("Hydrawise sent something unexpected") from None
        if isinstance(j, dict):
            if j.get("message_type") == "error" or j.get("error_msg"):
                raise CommandError(str(j.get("message") or j.get("error_msg")))
            if isinstance(j.get("nextpoll"), (int, float)):
                self._next_poll = max(MIN_POLL_S, int(j["nextpoll"]))
        return j if isinstance(j, dict) else {}

    # -- running --------------------------------------------------------------------------

    async def run(self) -> None:
        attempt, loaded = 0, 0.0
        while True:
            try:
                if time.monotonic() - loaded > RELOAD_S:
                    await self._load()
                    loaded = time.monotonic()
                await self.poll()
                attempt = 0
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), self._next_poll)
                    await asyncio.sleep(3)  # give Hydrawise a moment to apply what was just asked
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except BadKey as e:
                self.set_status("error", str(e))
                await asyncio.sleep(1800)
            except Exception as e:
                log.warning("Hydrawise: %s", e)
                self.set_status("error", f"Hydrawise: {e}")
                await self.backoff_sleep(attempt + 5)
                attempt += 1

    async def _load(self) -> None:
        j = await self._get("customerdetails.php", type="controllers")
        for c in j.get("controllers") or []:
            cid = str(c.get("controller_id"))
            eid = self.entity_id(f"controller_{cid}")
            self._ctrls[cid] = eid
            self.hub.upsert(eid, kind="sprinkler", name=c.get("name") or "Sprinklers", integration=self.id)
            await self.hub.update(eid, attrs={"manufacturer": "Hunter", "serial": c.get("serial_number") or "",
                                              "lastContact": iso(c.get("last_contact")),
                                              "message": c.get("status") or None})
        n = len(self._ctrls)
        if not n:
            self.set_status("error", "No controllers on this Hydrawise account")

    async def poll(self) -> None:
        for cid in list(self._ctrls):
            await self._apply(cid, await self._get("statusschedule.php", controller_id=cid))
        zones = len(self._zones)
        if self._ctrls:
            self.set_status("connected", f"{plural(len(self._ctrls), 'controller')}, {plural(zones, 'zone')}")

    async def _apply(self, cid: str, j: dict[str, Any]) -> None:
        now = time.time()
        left = {str(r.get("relay_id")): r.get("time_left") for r in j.get("running") or [] if isinstance(r, dict)}
        watering_names, next_runs, suspended, count, remaining = [], [], 0, 0, None
        for relay in j.get("relays") or []:
            rid = str(relay.get("relay_id"))
            eid = self.entity_id(f"zone_{rid}")
            self._zones[eid] = (rid, cid)
            name = relay.get("name") or f"Zone {relay.get('relay')}"
            self.hub.upsert(eid, kind="sprinkler_zone", name=name, integration=self.id)
            t, run = relay.get("time"), relay.get("run")
            running = t == RUNNING or rid in left
            is_suspended = not running and (t == SUSPENDED or relay.get("type") == 110)
            count += 1
            suspended += is_suspended
            attrs: dict[str, Any] = {"zoneNumber": relay.get("relay"), "suspended": is_suspended}
            if running:
                secs = left.get(rid) if isinstance(left.get(rid), (int, float)) else run
                attrs["remaining"] = round(float(secs or 0) / 60)
                attrs["nextRun"] = None
                watering_names.append(name)
                remaining = attrs["remaining"]
            elif is_suspended or not isinstance(t, (int, float)):
                attrs["nextRun"] = None
            else:
                attrs["nextRun"] = iso(now + t)
                next_runs.append(now + t)
                if isinstance(run, (int, float)):
                    attrs["scheduledMinutes"] = round(run / 60)
            await set_zone(self.hub, eid, running, **attrs)
        eid = self._ctrls.get(cid)
        if not eid:
            return
        all_suspended = count > 0 and suspended == count
        delay_until = self._delay_until.get(cid, 0)
        delayed = all_suspended and delay_until > now
        standby = all_suspended and not delayed  # suspended without a rain delay from Newman = standby
        await self.hub.update(eid, available=True,
                              state=controller_state(bool(watering_names), standby, delayed),
                              attrs={"currentZone": ", ".join(watering_names) or None, "remaining": remaining,
                                     "nextRun": iso(min(next_runs)) if next_runs else None,
                                     "rainDelayUntil": iso(delay_until) if delayed else None})

    # -- commands -------------------------------------------------------------------------

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        if entity_id in self._zones:
            rid, _ = self._zones[entity_id]
            if command == "run":
                minutes = command_number("sprinkler_zone", "run", value)
                await self._get("setzone.php", action="run", relay_id=rid, period_id=999, custom=minutes * 60)
            elif command == "stop":
                await self._get("setzone.php", action="stop", relay_id=rid)
            else:
                raise CommandError(f"A sprinkler zone can't {command.replace('_', ' ')}")
        else:
            cid = next((c for c, e in self._ctrls.items() if e == entity_id), None)
            if cid is None:
                raise CommandError("That sprinkler controller isn't connected")
            if command == "stop":
                await self._get("setzone.php", action="stopall", controller_id=cid)
            elif command == "rain_delay":
                days = command_number("sprinkler", "rain_delay", value)
                await self._suspend(cid, days)
                self._delay_until.pop(cid, None)
                if days:
                    self._delay_until[cid] = time.time() + days * 86400
            elif command == "turn_off":
                await self._suspend(cid, STANDBY_DAYS)
                self._delay_until.pop(cid, None)
            elif command == "turn_on":
                await self._suspend(cid, 0)
                self._delay_until.pop(cid, None)
            else:
                raise CommandError(f"A sprinkler controller can't {command.replace('_', ' ')}")
        self._wake.set()

    async def _suspend(self, cid: str, days: int) -> None:
        if days <= 0:  # period 0 = resume the schedule
            await self._get("setzone.php", action="suspendall", controller_id=cid, period_id=0)
        else:
            await self._get("setzone.php", action="suspendall", controller_id=cid, period_id=999,
                            custom=int(time.time() + days * 86400))
