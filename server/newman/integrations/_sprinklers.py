"""What the sprinkler connections (Hydrawise, Rain Bird, Rachio) share: turning "is this zone watering"
into states and started/finished events, and reading the minutes / days a command was given."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from ..kinds import KINDS
from .base import CommandError


def iso(ts: float | None) -> str | None:
    """A Unix time as local ISO time ("2026-10-04T06:00-04:00"), None stays None."""
    if not ts:
        return None
    return datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="minutes")


def command_number(kind: str, command: str, value: Any) -> int:
    """The number a command was given, checked against the kind's own limits (min/max/default)."""
    c = KINDS[kind].command(command)
    if value in (None, ""):
        value = c.default
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        raise CommandError(f"{c.label} needs a number of {c.unit}") from None
    if (c.min is not None and n < c.min) or (c.max is not None and n > c.max):
        raise CommandError(f"{c.label}: between {c.min:g} and {c.max:g} {c.unit}")
    return n


async def set_zone(hub: Any, eid: str, watering: bool, **attrs: Any) -> None:
    """Update one zone; on a change (not the first reading) emit started / finished and note the last run."""
    ent = hub.get(eid)
    if not ent:
        return
    prev, state = ent.state, ("watering" if watering else "idle")
    if not watering:
        attrs["remaining"] = None
    if prev == "watering" and not watering and "lastRun" not in attrs:
        attrs["lastRun"] = iso(time.time())
    await hub.update(eid, state=state, available=True, attrs=attrs)
    if prev is not None and prev != state:
        await hub.emit(eid, "started" if watering else "finished", {})


def controller_state(watering: bool, standby: bool = False, delayed: bool = False) -> str:
    return "watering" if watering else "off" if standby else "rain_delay" if delayed else "idle"
