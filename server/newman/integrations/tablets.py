"""Wall tablets' batteries: each signed-in wall tablet reports its own battery (POST /api/tablet/battery)
and becomes one device of kind "tablet" - charging / on battery, with its level, temperature and
voltage as readings - so smart charging (charging.py) can switch the smart plug it charges from on below
20 % and off above 80 % (a tablet kept at 100 % all day on a wall mount swells its battery).

The connection is made the first time a tablet reports. The last report of each tablet is kept in
this connection's data folder (tablets.json), so after a restart the devices are back straight away
with their last values.

A report may say whether the tablet's screen is on ("screen": "on" | "off") and whether it keeps
reporting with the screen off ("background": true - the Android wall sends it only while its check-in
alarm is set, every 15 minutes even in Doze), when its next check-in is due ("nextReportIn": seconds)
and whether Android lets it ignore battery optimisation ("dozeExempt"). A tablet whose screen is off
shows as "Screen off" (the kind's state note), not offline - and so does its music player
(navidrome.py asks tablet_asleep()). Offline is:
 - screen on (or not said): no report for 15 minutes;
 - screen off, reporting in the background: no report for 35 minutes (background check-ins run late),
   or its next check-in plus 20 minutes when it said that's later;
 - screen off, no background reports (an iPad can't): no report for 12 hours. Its battery is unknown
   after 15 minutes though ("batteryStale"), so smart charging leaves it alone until it reports again.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from .base import Integration

log = logging.getLogger(__name__)

OFFLINE_AFTER_S = 15 * 60              # screen on
SCREEN_OFF_AFTER_S = 35 * 60           # screen off, still checking in (late check-ins are normal)
SCREEN_OFF_SILENT_AFTER_S = 12 * 3600  # screen off, no background reports: "Screen off" this long
LATE_GRACE_S = 20 * 60                 # a check-in it announced may come this late before it's offline
PLUGGED = ("ac", "usb", "wireless", "none")
SCREEN = ("on", "off")


def offline_after(attrs: dict[str, Any]) -> float:
    """How long this tablet may go without a report before it counts as offline (by its last one)."""
    if attrs.get("screen") != "off":
        return OFFLINE_AFTER_S
    if not attrs.get("backgroundReports"):
        return SCREEN_OFF_SILENT_AFTER_S
    nxt = attrs.get("nextReportIn")
    if isinstance(nxt, (int, float)) and not isinstance(nxt, bool):
        return min(max(SCREEN_OFF_AFTER_S, nxt + LATE_GRACE_S), SCREEN_OFF_SILENT_AFTER_S)
    return SCREEN_OFF_AFTER_S


def asleep(attrs: dict[str, Any], age: float) -> bool:
    """Screen off but still checking in (and not overdue): asleep, will report - not offline."""
    return attrs.get("screen") == "off" and bool(attrs.get("backgroundReports")) and age <= offline_after(attrs)


def reporting(attrs: dict[str, Any], age: float) -> tuple[bool, bool]:
    """(available, battery stale) for a tablet whose last report is [age] seconds old: stale = its
    screen is off, it doesn't report in the background and it's been quiet past the usual 15 minutes -
    the level it last said may be long gone."""
    available = age <= offline_after(attrs)
    stale = available and age > OFFLINE_AFTER_S and not attrs.get("backgroundReports")
    return available, stale


def clean_report(body: Any) -> dict[str, Any]:
    """The wall app's report -> attrs, or ValueError with what's wrong."""
    if not isinstance(body, dict):
        raise ValueError("Send a JSON object")
    level, charging = body.get("level"), body.get("charging")
    if isinstance(level, bool) or not isinstance(level, (int, float)) or not 0 <= level <= 100:
        raise ValueError("level must be a number from 0 to 100")
    if not isinstance(charging, bool):
        raise ValueError("charging must be true or false")
    plugged = body.get("plugged", "none")
    if plugged not in PLUGGED:
        raise ValueError("plugged must be ac, usb, wireless or none")
    out: dict[str, Any] = {"batteryLevel": int(round(level)), "charging": charging, "plugged": plugged}
    temp, volts = body.get("temperatureC"), body.get("voltageMv")
    if temp is not None:
        if isinstance(temp, bool) or not isinstance(temp, (int, float)) or not -40 <= temp <= 120:
            raise ValueError("temperatureC must be a number (°C) or null")
        out["temperature"] = round(float(temp), 1)
    if volts is not None:
        if isinstance(volts, bool) or not isinstance(volts, (int, float)) or not 0 <= volts <= 20000:
            raise ValueError("voltageMv must be a number (mV) or null")
        out["voltage"] = round(float(volts) / 1000, 2)
    screen, background = body.get("screen", "on"), body.get("background", False)
    if screen not in SCREEN:
        raise ValueError("screen must be on or off")
    if not isinstance(background, bool):
        raise ValueError("background must be true or false")
    out.update(screen=screen, backgroundReports=background, batteryStale=False)
    nxt = body.get("nextReportIn")
    if nxt is not None:
        if isinstance(nxt, bool) or not isinstance(nxt, (int, float)) or not 0 <= nxt <= 86400:
            raise ValueError("nextReportIn must be a number of seconds (0 to 86400) or null")
        out["nextReportIn"] = int(nxt)
    exempt = body.get("dozeExempt")
    if exempt is not None:
        if not isinstance(exempt, bool):
            raise ValueError("dozeExempt must be true or false")
        out["dozeExempt"] = exempt
    return out


class TabletsIntegration(Integration):
    type = "tablets"
    name = "Wall tablets"
    category = "other"
    provides = ("tablet",)
    description = ("Each signed-in wall tablet's own battery - level, charging, temperature - as a device, so smart "
                   "charging can keep it between 20 and 80 % from a smart plug. Comes by itself: the wall app "
                   "reports it; nothing to set up here.")
    fields: list = []

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._file = Path(self.ctx.data_dir) / "integrations" / self.id / "tablets.json"
        # account id -> {"name", "attrs", "state", "at"}
        self.tablets: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self._file.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._file.with_suffix(".part")
            tmp.write_text(json.dumps(self.tablets, indent=1))
            tmp.replace(self._file)
        except OSError:
            log.warning("Couldn't keep the wall tablets' last reports", exc_info=True)

    def _detail(self) -> str:
        n = len(self.tablets)
        return f"{n} wall tablet{'s' if n != 1 else ''}" if n else "Waiting for a wall tablet to report"

    async def run(self) -> None:
        # Back after a restart with the last values; offline if that report is old.
        for uid, t in list(self.tablets.items()):
            eid = self.entity_id(uid)
            if self.hub.get(eid):
                continue  # reported again already
            self.hub.upsert(eid, kind="tablet", name=t.get("name") or "Wall tablet", integration=self.id)
            attrs = t.get("attrs") or {}
            available, stale = reporting(attrs, time.time() - float(t.get("at") or 0))
            await self.hub.update(eid, state=t.get("state"), attrs={**attrs, "batteryStale": stale}, available=available)
        self.set_status("connected", self._detail())
        while True:
            await asyncio.sleep(60)
            await self.check_offline()

    def rename_account(self, user_id: str, name: str) -> None:
        """The tablet's account was renamed: its battery is called after the new name from now on."""
        t = self.tablets.get(user_id)
        if t is not None and name and t.get("name") != name:
            t["name"] = name
            self._save()

    async def report(self, user_id: str, name: str, attrs: dict[str, Any], now: float | None = None) -> None:
        """One tablet's battery, from its signed-in account."""
        now = now or time.time()
        eid = self.entity_id(user_id)
        self.hub.upsert(eid, kind="tablet", name=name or "Wall tablet", integration=self.id)
        state = "charging" if attrs.get("charging") else "battery"
        if "nextReportIn" in attrs:
            attrs = {**attrs, "nextReportAt": int(now + attrs["nextReportIn"])}
        else:  # (an older app, or not armed: nothing announced)
            attrs = {**attrs, "nextReportIn": None, "nextReportAt": None}
        await self.hub.update(eid, state=state, attrs=attrs, available=True)
        self.tablets[user_id] = {"name": name, "attrs": attrs, "state": state, "at": now}
        self._save()
        self.set_status("connected", self._detail())

    def tablet_asleep(self, user_id: str, now: float | None = None) -> bool:
        """This account's tablet has its screen off but is still checking in on time: asleep, not
        offline (its music player shows "Screen off" too, see navidrome.py)."""
        t = self.tablets.get(user_id)
        if not t:
            return False
        ent = self.hub.get(self.entity_id(user_id))
        if ent is not None and not ent.available:
            return False
        return asleep(t.get("attrs") or {}, (now or time.time()) - float(t.get("at") or 0))

    async def check_offline(self, now: float | None = None) -> None:
        now = now or time.time()
        for uid, t in self.tablets.items():
            ent = self.hub.get(self.entity_id(uid))
            if not ent or not ent.available:
                continue  # offline until it reports again
            available, stale = reporting(t.get("attrs") or {}, now - float(t.get("at") or 0))
            if not available:
                await self.hub.update(ent.id, available=False)
            elif stale and not ent.attrs.get("batteryStale"):
                await self.hub.update(ent.id, attrs={"batteryStale": True})
