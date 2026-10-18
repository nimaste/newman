"""Muting a device's problem: "don't tell me about the keypad's No AC power" - for a day, a week, until
it's fixed, or always. Per problem, never per device, so a muted keypad still says when its battery runs low.

Settings "mutedIssues": {entity id: {issue id: {"mode", "until", "by", "at"}}}. "day" / "week" end at
"until"; "fixed" lasts while the device reports that problem and goes the moment it doesn't (so it shows
again if it comes back); "always" stays until unmuted. A muted problem is left out wherever people are told
(the entity's health ok/issues, Today's To do); the device's own page still lists it ("items").
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .state import Entity

MODES = {"day": 86400, "week": 7 * 86400, "fixed": None, "always": None}
SETTING = "mutedIssues"


def _live(entry: Any, present: bool, now: float) -> bool:
    """Whether a saved mute still holds."""
    if not isinstance(entry, dict) or entry.get("mode") not in MODES:
        return False
    if entry["mode"] == "fixed":
        return present
    if entry["mode"] == "always":
        return True
    until = entry.get("until")
    return isinstance(until, (int, float)) and until > now


def health(e: Entity, muted: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """The entity's "health" as clients get it: ok / issues count only problems not muted; items lists
    every current problem with its mute (or null)."""
    now = time.time() if now is None else now
    mine = muted.get(e.id) or {}
    items, issues = [], []
    for iid, text in e.health_items:
        m = mine.get(iid)
        on = _live(m, True, now)
        items.append({"id": iid, "text": text,
                      "muted": {"mode": m["mode"], "until": m.get("until"), "by": m.get("by")} if on else None})
        if not on:
            issues.append(text)
    return {"ok": not issues, "issues": issues, "items": items}


def unmuted_issues(e: Entity, muted: dict[str, Any], now: float | None = None) -> list[str]:
    return health(e, muted, now)["issues"]


def cleaned(e: Entity | None, entries: dict[str, Any], now: float) -> dict[str, Any]:
    """An entity's mutes without the ones that no longer hold (ended, or "fixed" and the problem gone)."""
    present = {iid for iid, _ in e.health_items} if e else set()
    return {iid: m for iid, m in entries.items() if _live(m, iid in present, now)}


class Muting:
    def __init__(self, store: Any, hub: Any, clock: Callable[[], float] = time.time) -> None:
        self.store, self.hub, self.clock = store, hub, clock
        saved = store.settings().get(SETTING)
        self._m: dict[str, dict[str, Any]] = saved if isinstance(saved, dict) else {}

    @property
    def muted(self) -> dict[str, Any]:
        return self._m

    def _save(self) -> None:
        self.store.set_setting(SETTING, self._m)

    def health(self, e: Entity) -> dict[str, Any]:
        self.tidy(e.id)
        return health(e, self._m, self.clock())

    def tidy(self, eid: str) -> bool:
        """Drops this device's mutes that no longer hold; True when something went."""
        entries = self._m.get(eid)
        if not entries:
            return False
        keep = cleaned(self.hub.get(eid), entries, self.clock())
        if keep == entries:
            return False
        if keep:
            self._m[eid] = keep
        else:
            self._m.pop(eid, None)
        self._save()
        return True

    async def on_change(self, old: Entity | None, new: Entity) -> None:
        """A device changed: a "fixed" mute goes the moment its problem does."""
        self.tidy(new.id)

    def mute(self, e: Entity, issue: str, mode: str, by: str) -> str:
        """Mutes one of its current problems; returns the problem's words. ValueError: refused, in words."""
        if mode not in MODES:
            raise ValueError('Mute for "day", "week", "fixed" or "always"')
        text = dict(e.health_items).get(issue)
        if text is None:
            raise ValueError(f"{e.name} isn't reporting that problem right now")
        now = self.clock()
        secs = MODES[mode]
        self._m.setdefault(e.id, {})[issue] = {"mode": mode, "until": now + secs if secs else None, "by": by, "at": now}
        self._save()
        return text

    def unmute(self, e: Entity, issue: str) -> bool:
        entries = self._m.get(e.id) or {}
        if issue not in entries:
            return False
        entries.pop(issue)
        if not entries:
            self._m.pop(e.id, None)
        self._save()
        return True

    def replace_ids(self, mapping: dict[str, str]) -> int:
        """A device swapped for another: its mutes go with it (the new one's own win)."""
        n = 0
        for old, new in mapping.items():
            if old in self._m:
                moved = self._m.pop(old)
                self._m[new] = {**moved, **self._m.get(new, {})}
                n += 1
        if n:
            self._save()
        return n

    def forget(self, eid: str) -> None:
        if self._m.pop(eid, None) is not None:
            self._save()
