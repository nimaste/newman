"""Upkeep: things around the house that come due again and again - the furnace filter, softener salt,
an oil change, testing the smoke alarms. Each item repeats every N days / weeks / months, and/or
after a device has run for N hours (the furnace and AC, counted from the thermostat's own
heating / cooling). Each is a device of kind "upkeep" (up to date / coming up / due today /
overdue), so automations can notify when one comes due, and Today's To do lists it.

The items live in this connection's data folder (items.json, so backups carry them), not in its
settings: they're edited on the dashboard's Upkeep page, and marked done from anywhere.
"""
from __future__ import annotations

import asyncio
import calendar
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..store import new_id
from .base import CommandError, Integration

log = logging.getLogger(__name__)

UNITS = ("days", "weeks", "months")
HISTORY_KEEP = 20


def add_interval(start: date, every: int, unit: str) -> date:
    if unit == "weeks":
        return start + timedelta(weeks=every)
    if unit == "months":
        m = start.month - 1 + every
        y, m = start.year + m // 12, m % 12 + 1
        return date(y, m, min(start.day, calendar.monthrange(y, m)[1]))
    return start + timedelta(days=every)


def is_running(ent: Any) -> bool:
    """Is a device "running" right now, for runtime-based items: a thermostat heating or cooling
    (its activity), anything else when it's in its active state."""
    if ent is None or not ent.available:
        return False
    action = (ent.attrs or {}).get("action")
    if action is not None:
        return str(action).lower() not in ("", "idle", "off", "none")
    return ent.is_active


def clean_item(raw: dict[str, Any], old: dict[str, Any] | None = None) -> dict[str, Any]:
    """An item as the API sends it -> what's stored (keeping its history and clocks)."""
    old = old or {}
    name = str(raw.get("name", old.get("name", ""))).strip()[:60]
    if not name:
        raise ValueError("Give it a name")
    every = raw.get("every", old.get("every", 30))
    try:
        every = int(every) if every not in (None, "") else 0
    except (TypeError, ValueError):
        raise ValueError("How often must be a whole number")
    unit = raw.get("unit", old.get("unit", "days"))
    if unit not in UNITS:
        raise ValueError("How often is in days, weeks or months")
    hours = raw.get("runtimeHours", old.get("runtimeHours"))
    try:
        hours = float(hours) if hours not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        raise ValueError("Running hours must be a number")
    entity = str(raw.get("runtimeEntity", old.get("runtimeEntity")) or "") or None
    if hours and not entity:
        raise ValueError("Pick the device whose running hours count")
    if every <= 0 and not (hours and entity):
        raise ValueError("Say how often - every so many days, weeks or months, or after so many running hours")
    lead = raw.get("lead", old.get("lead", 7))
    try:
        lead = max(0, int(lead))
    except (TypeError, ValueError):
        lead = 7
    last = raw.get("lastDone", old.get("lastDone"))
    if isinstance(last, str) and last:
        try:
            last = datetime.fromisoformat(last).timestamp()
        except ValueError:
            raise ValueError("Last done must be a date like 2026-09-02")
    return {
        "id": old.get("id") or new_id("up"),
        "name": name, "every": every, "unit": unit,
        "runtimeEntity": entity, "runtimeHours": hours,
        "owner": str(raw.get("owner", old.get("owner")) or ""),          # a person id; "" = everyone
        "lead": lead,                                                       # days ahead it shows as coming up
        "eveningBefore": bool(raw.get("eveningBefore", old.get("eveningBefore", False))),
        "askWho": bool(raw.get("askWho", old.get("askWho", True))),
        "icon": str(raw.get("icon", old.get("icon")) or ""),
        "lastDone": float(last) if last else time.time(),
        "runtimeSec": float(old.get("runtimeSec", 0.0)),
        "history": list(old.get("history", [])),
        "snoozeUntil": old.get("snoozeUntil"),
    }


class UpkeepIntegration(Integration):
    type = "upkeep"
    name = "Upkeep"
    category = "other"
    provides = ("upkeep",)
    description = ("Reminders for things that come due again and again - the furnace filter, softener salt, an oil "
                   "change, smoke alarm tests. Every so many days, weeks or months, or after so many hours a device "
                   "has run. They show in Today's To do, and automations can notify when one comes due. Add items "
                   "on the Upkeep page.")
    fields: list = []

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._file = Path(self.ctx.data_dir) / "integrations" / self.id / "items.json"
        self.items: list[dict[str, Any]] = self._load()
        self._running_since: dict[str, float] = {}  # item id -> when its device started running
        self._last_state: dict[str, str] = {}
        self._people: Any = None  # set by the API: () -> [{"id","name"}]
        self._dirty_at = 0.0

    # -- storage --------------------------------------------------------------------------

    def _load(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self._file.read_text())
        except (OSError, ValueError):
            return []

    def _save(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".part")
        tmp.write_text(json.dumps(self.items, indent=1))
        tmp.replace(self._file)
        self._dirty_at = 0.0

    def _item(self, item_id: str) -> dict[str, Any]:
        it = next((i for i in self.items if i["id"] == item_id), None)
        if not it:
            raise CommandError("That upkeep item is gone")
        return it

    def item_for_entity(self, entity_id: str) -> dict[str, Any]:
        return self._item(entity_id.split(".", 1)[1])

    # -- what's due ---------------------------------------------------------------------

    def runtime_hours(self, it: dict[str, Any], now: float | None = None) -> float:
        now = now or time.time()
        extra = now - self._running_since[it["id"]] if it["id"] in self._running_since else 0.0
        return (it.get("runtimeSec", 0.0) + extra) / 3600

    def due_status(self, it: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        """{"state", "due" (date), "daysLeft", "runtimeCount" (hours run since last done)} for one item."""
        now = now or time.time()
        today = datetime.fromtimestamp(now).date()
        last = datetime.fromtimestamp(it["lastDone"]).date()
        due = add_interval(last, it["every"], it["unit"]) if it["every"] > 0 else None
        hours = self.runtime_hours(it, now) if it.get("runtimeHours") and it.get("runtimeEntity") else None
        if hours is not None and hours >= it["runtimeHours"]:
            due = min(due, today) if due else today  # ran its hours: due now
        days = (due - today).days if due else None
        if days is None:
            state = "ok"
        elif days < 0:
            state = "overdue"
        elif days == 0:
            state = "due"
        elif days <= it.get("lead", 7):
            state = "soon"
        else:
            state = "ok"
        # Running hours getting close count as coming up too.
        if state == "ok" and hours is not None and it["runtimeHours"] - hours <= max(10.0, it["runtimeHours"] * 0.05):
            state = "soon"
        return {"state": state, "due": due.isoformat() if due else None, "daysLeft": days,
                "runtimeCount": round(hours, 1) if hours is not None else None}

    def _person_name(self, person_id: str) -> str:
        people = self._people() if self._people else []
        return next((p["name"] for p in people if p["id"] == person_id), "")

    def item_json(self, it: dict[str, Any]) -> dict[str, Any]:
        st = self.due_status(it)
        return {**{k: v for k, v in it.items() if k not in ("runtimeSec",)}, **st,
                "entity": self.entity_id(it["id"]), "ownerName": self._person_name(it["owner"]) if it["owner"] else "",
                "lastDoneDate": datetime.fromtimestamp(it["lastDone"]).date().isoformat(),
                "lastDoneBy": it["history"][0]["who"] if it.get("history") else "",
                "snoozed": bool(it.get("snoozeUntil") and it["snoozeUntil"] > time.time())}

    async def _publish(self, it: dict[str, Any]) -> None:
        eid = self.entity_id(it["id"])
        self.hub.upsert(eid, kind="upkeep", name=it["name"], integration=self.id)
        st = self.due_status(it)
        every = (f"every {it['every']} {it['unit'][:-1] if it['every'] == 1 else it['unit']}" if it["every"] else "")
        if it.get("runtimeHours"):
            every = (every + " or " if every else "") + f"after {it['runtimeHours']:g} running hours"
        await self.hub.update(eid, available=True, state=st["state"], attrs={
            "due": st["due"], "daysLeft": st["daysLeft"], "runtimeHours": st["runtimeCount"],
            "runtimeLimit": it.get("runtimeHours"), "every": every,
            "owner": self._person_name(it["owner"]) if it["owner"] else "everyone", "ownerId": it["owner"] or "",
            "lastDone": datetime.fromtimestamp(it["lastDone"]).date().isoformat(),
            "lastDoneBy": it["history"][0]["who"] if it.get("history") else "",
            "askWho": it.get("askWho", True), "eveningBefore": it.get("eveningBefore", False),
            "lead": it.get("lead", 7), "icon": it.get("icon", ""),
            "snoozedUntil": it.get("snoozeUntil") if it.get("snoozeUntil") and it["snoozeUntil"] > time.time() else None,
            "history": it.get("history", [])[:5],
        })
        prev = self._last_state.get(it["id"])
        self._last_state[it["id"]] = st["state"]
        if prev is not None and prev != st["state"]:
            if st["state"] in ("due", "overdue") and prev in ("ok", "soon"):
                await self.hub.emit(eid, "due", {"detail": "due today" if st["state"] == "due" else "overdue"})
            elif st["state"] == "soon" and prev == "ok":
                await self.hub.emit(eid, "soon", {"detail": f"due {st['due']}"})

    async def publish_all(self) -> None:
        present = {self.entity_id(i["id"]) for i in self.items}
        for e in [e for e in self.hub.all() if e.integration == self.id and e.id not in present]:
            await self.hub.update(e.id, available=False)  # removed items disappear on the next restart
        for it in self.items:
            await self._publish(it)
        due = sum(1 for i in self.items if self.due_status(i)["state"] in ("due", "overdue"))
        self.set_status("connected", f"{len(self.items)} item{'s' if len(self.items) != 1 else ''}"
                        + (f" · {due} due" if due else ""))

    # -- running ----------------------------------------------------------------------

    async def run(self) -> None:
        self.hub.subscribe_changes(self._on_change)
        for it in self.items:  # devices already running when we start
            if it.get("runtimeEntity") and is_running(self.hub.get(it["runtimeEntity"])):
                self._running_since[it["id"]] = time.time()
        await self.publish_all()
        while True:
            await asyncio.sleep(60)
            await self.publish_all()
            if self._dirty_at and time.time() - self._dirty_at > 300:
                self._fold_runtime()
                self._save()

    def _fold_runtime(self) -> None:
        now = time.time()
        for iid, since in list(self._running_since.items()):
            it = next((i for i in self.items if i["id"] == iid), None)
            if it:
                it["runtimeSec"] = it.get("runtimeSec", 0.0) + now - since
                self._running_since[iid] = now

    async def _on_change(self, old: Any, new: Any) -> None:
        for it in self.items:
            if it.get("runtimeEntity") != new.id:
                continue
            on = is_running(new)
            if on and it["id"] not in self._running_since:
                self._running_since[it["id"]] = time.time()
            elif not on and it["id"] in self._running_since:
                it["runtimeSec"] = it.get("runtimeSec", 0.0) + time.time() - self._running_since.pop(it["id"])
                self._dirty_at = self._dirty_at or time.time()

    # -- changes (from the Upkeep page and from Done buttons) ---------------------------

    async def add(self, raw: dict[str, Any]) -> dict[str, Any]:
        it = clean_item(raw)
        self.items.append(it)
        self._save()
        self._last_state[it["id"]] = self.due_status(it)["state"]
        await self.publish_all()
        return self.item_json(it)

    async def change(self, item_id: str, raw: dict[str, Any]) -> dict[str, Any]:
        old = self._item(item_id)
        new = clean_item(raw, old)
        self.items[self.items.index(old)] = new
        self._save()
        await self.publish_all()
        return self.item_json(new)

    async def remove(self, item_id: str) -> None:
        it = self._item(item_id)
        self.items.remove(it)
        self._running_since.pop(item_id, None)
        self._save()
        await self.hub.update(self.entity_id(item_id), available=False)
        await self.publish_all()

    async def done(self, item_id: str, who: str) -> dict[str, Any]:
        it = self._item(item_id)
        now = time.time()
        # What Undo puts back: the previous "last done" and the running hours counted until now.
        prev = {"prev": it["lastDone"], "prevRuntime": it.get("runtimeSec", 0.0) + (
            now - self._running_since[it["id"]] if it["id"] in self._running_since else 0.0)}
        it["lastDone"] = now
        it["runtimeSec"] = 0.0
        if it["id"] in self._running_since:
            self._running_since[it["id"]] = now
        it["snoozeUntil"] = None
        it["history"] = [{"at": now, "who": who, **prev}] + it.get("history", [])[:HISTORY_KEEP - 1]
        self._save()
        self.ctx.hub_log("upkeep", f"{it['name']}: done", f"By {who}", self.entity_id(it["id"]))
        await self._publish(it)
        return self.item_json(it)

    async def undo(self, item_id: str) -> dict[str, Any]:
        """Take back the last Done (a mis-tap)."""
        it = self._item(item_id)
        if not it.get("history"):
            raise CommandError("Nothing to undo")
        last, it["history"] = it["history"][0], it["history"][1:]
        it["lastDone"] = last.get("prev", it["history"][0]["at"] if it["history"] else it["lastDone"])
        it["runtimeSec"] = last.get("prevRuntime", it.get("runtimeSec", 0.0))
        if it["id"] in self._running_since:
            self._running_since[it["id"]] = time.time()
        self._save()
        await self._publish(it)
        return self.item_json(it)

    async def snooze(self, item_id: str, days: int = 1) -> dict[str, Any]:
        it = self._item(item_id)
        tomorrow = datetime.combine(date.today() + timedelta(days=max(1, days)), datetime.min.time())
        it["snoozeUntil"] = tomorrow.timestamp()
        self._save()
        await self._publish(it)
        return self.item_json(it)

    async def replace_entities(self, mapping: dict[str, str]) -> int:
        """Items counting a device's running hours follow it to its replacement (hours so far are kept)."""
        changed = 0
        for it in self.items:
            new = mapping.get(it.get("runtimeEntity") or "")
            if not new:
                continue
            if it["id"] in self._running_since:  # bank the old device's time before switching over
                it["runtimeSec"] = it.get("runtimeSec", 0.0) + time.time() - self._running_since.pop(it["id"])
            it["runtimeEntity"] = new
            if is_running(self.hub.get(new)):
                self._running_since[it["id"]] = time.time()
            changed += 1
        if changed:
            self._save()
            await self.publish_all()
        return changed

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        it = self.item_for_entity(entity_id)
        if command == "done":
            await self.done(it["id"], str(value or self._person_name(it["owner"]) or "Newman"))
        elif command == "snooze":
            await self.snooze(it["id"], int(value or 1))
        else:
            raise CommandError(f"An upkeep item can't {command.replace('_', ' ')}")
