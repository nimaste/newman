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

from .kinds import KINDS
from .state import Entity

MODES = {"day": 86400, "week": 7 * 86400, "fixed": None, "always": None}
SETTING = "mutedIssues"
# A device's Warnings tab (web): {entity id: {"batteryLow": int | None, "warnings": {issue id: False}}} - its
# own low-battery level, and problems switched off for good (never shown, unlike a mute that can end).
WARNINGS = "deviceWarnings"


def switched_off(warnings: dict[str, Any], eid: str) -> set[str]:
    own = warnings.get(eid) or {}
    return {iid for iid, on in (own.get("warnings") or {}).items() if on is False}


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


def health(e: Entity, muted: dict[str, Any], now: float | None = None, off: set[str] | None = None) -> dict[str, Any]:
    """The entity's "health" as clients get it: ok / issues count only problems not muted; items lists
    every current problem with its mute (or null). Problems switched off on its Warnings tab (`off`) are
    left out altogether."""
    now = time.time() if now is None else now
    mine = muted.get(e.id) or {}
    items, issues = [], []
    for iid, text in e.health_items:
        if off and iid in off:
            continue
        m = mine.get(iid)
        on = _live(m, True, now)
        items.append({"id": iid, "text": text,
                      "muted": {"mode": m["mode"], "until": m.get("until"), "by": m.get("by")} if on else None})
        if not on:
            issues.append(text)
    return {"ok": not issues, "issues": issues, "items": items}


def unmuted_issues(e: Entity, muted: dict[str, Any], now: float | None = None,
                   off: set[str] | None = None) -> list[str]:
    return health(e, muted, now, off)["issues"]


def cleaned(e: Entity | None, entries: dict[str, Any], now: float) -> dict[str, Any]:
    """An entity's mutes without the ones that no longer hold (ended, or "fixed" and the problem gone)."""
    present = {iid for iid, _ in e.health_items} if e else set()
    return {iid: m for iid, m in entries.items() if _live(m, iid in present, now)}


class DeviceWarnings:
    """Each device's Warnings tab: its own low-battery level and the problems switched off for it."""

    def __init__(self, store: Any) -> None:
        self.store = store
        saved = store.settings().get(WARNINGS)
        self._w: dict[str, dict[str, Any]] = saved if isinstance(saved, dict) else {}
        self._apply()

    @property
    def all(self) -> dict[str, dict[str, Any]]:
        return self._w

    def _apply(self) -> None:
        from . import state
        state.BATTERY_LOW["devices"] = {eid: w["batteryLow"] for eid, w in self._w.items()
                                        if isinstance(w.get("batteryLow"), (int, float))}

    def off(self, eid: str) -> set[str]:
        return switched_off(self._w, eid)

    def left_open(self, eid: str) -> int | None:
        """Its own "left open" minutes (None: the house's)."""
        return (self._w.get(eid) or {}).get("leftOpenMinutes")

    def json(self, e: Entity, house: dict[str, Any]) -> dict[str, Any]:
        """house: the house settings' "devices" part (its battery level and left-open minutes)."""
        own = self._w.get(e.id) or {}
        off = self.off(e.id)
        return {"batteryLow": own.get("batteryLow"), "houseBatteryLow": house["batteryLow"],
                "hasBattery": any(i == "battery_low" for i, _ in e.possible_issues),
                "leftOpenMinutes": own.get("leftOpenMinutes"), "houseLeftOpenMinutes": house["leftOpenMinutes"],
                "hasSecureState": bool(KINDS[e.kind].secure_state),
                "warnings": [{"id": iid, "label": name, "on": iid not in off} for iid, name in e.possible_issues]}

    def set(self, e: Entity, data: Any) -> None:
        """{"batteryLow": 5-50 | null, "leftOpenMinutes": 1-240 | null, "warnings": {issue id: bool}};
        ValueError: refused, in words."""
        if not isinstance(data, dict):
            raise ValueError("Send the warnings")
        own = dict(self._w.get(e.id) or {})
        if "batteryLow" in data:
            v = data["batteryLow"]
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not 5 <= v <= 50):
                raise ValueError("Its own low battery warning: 5 to 50 (or none, for the house setting)")
            own["batteryLow"] = v
        if "leftOpenMinutes" in data:
            v = data["leftOpenMinutes"]
            if v is not None and not KINDS[e.kind].secure_state:
                raise ValueError(f"{e.name} is never left open")
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 240):
                raise ValueError("Its own left open warning: 1 to 240 minutes (or none, for the house setting)")
            own["leftOpenMinutes"] = v
        if "warnings" in data:
            w = data["warnings"]
            known = {iid for iid, _ in e.possible_issues}
            if not isinstance(w, dict) or not all(isinstance(v, bool) for v in w.values()):
                raise ValueError("Warnings: each problem on (true) or off (false)")
            if unknown := [k for k in w if k not in known]:
                raise ValueError(f"{e.name} doesn't report {', '.join(unknown)}")
            merged = {**(own.get("warnings") or {}), **w}
            own["warnings"] = {k: False for k, v in merged.items() if v is False}
        own = {k: v for k, v in own.items() if v not in (None, {})}
        if own:
            self._w[e.id] = own
        else:
            self._w.pop(e.id, None)
        self.store.set_setting(WARNINGS, self._w)
        self._apply()

    def replace_ids(self, mapping: dict[str, str]) -> int:
        n = 0
        for old, new in mapping.items():
            if old in self._w:
                self._w[new] = {**self._w.pop(old), **self._w.get(new, {})}
                n += 1
        if n:
            self.store.set_setting(WARNINGS, self._w)
            self._apply()
        return n

    def forget(self, eid: str) -> None:
        if self._w.pop(eid, None) is not None:
            self.store.set_setting(WARNINGS, self._w)
            self._apply()


class Muting:
    def __init__(self, store: Any, hub: Any, clock: Callable[[], float] = time.time) -> None:
        self.store, self.hub, self.clock = store, hub, clock
        saved = store.settings().get(SETTING)
        self._m: dict[str, dict[str, Any]] = saved if isinstance(saved, dict) else {}
        self.warnings = DeviceWarnings(store)

    @property
    def muted(self) -> dict[str, Any]:
        return self._m

    def _save(self) -> None:
        self.store.set_setting(SETTING, self._m)

    def health(self, e: Entity) -> dict[str, Any]:
        self.tidy(e.id)
        return health(e, self._m, self.clock(), self.warnings.off(e.id))

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
