"""Quiet hours, per person (their Profile): during them their phones get everyday notifications silently
- still delivered, without a sound - while what they let through rings as usual.

Settings "quietHours": {person id: {"enabled", "from": "22:00", "to": "07:00", "days": "all"|"weekdays"|
"weekends", "allow": ["alarm", "doorbell", "people", "urgent"]}}. A window that ends before it starts runs
over midnight, and belongs to the day it started on.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Callable

SETTING = "quietHours"
ALLOW = ("alarm", "doorbell", "people", "urgent")
DAYS = ("all", "weekdays", "weekends")
DEFAULT = {"enabled": False, "from": "22:00", "to": "07:00", "days": "all", "allow": ["alarm", "doorbell", "urgent"]}
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def clean(data: Any) -> dict[str, Any]:
    """A person's quiet hours from what a client sent; ValueError: refused, in words."""
    if not isinstance(data, dict):
        raise ValueError("Send the quiet hours")
    out = {**DEFAULT, **{k: v for k, v in data.items() if k in DEFAULT}}
    if not isinstance(out["enabled"], bool):
        raise ValueError("Quiet hours on (true) or off (false)")
    for k in ("from", "to"):
        if not isinstance(out[k], str) or not HHMM.match(out[k]):
            raise ValueError("Quiet hours: times like 22:00 and 07:00")
    if out["from"] == out["to"]:
        raise ValueError("Quiet hours need to end at a different time than they start")
    if out["days"] not in DAYS:
        raise ValueError('Quiet hours days: "all", "weekdays" or "weekends"')
    allow = out["allow"]
    if not isinstance(allow, list) or any(a not in ALLOW for a in allow):
        raise ValueError("Still ring through: any of " + ", ".join(ALLOW))
    out["allow"] = [a for a in ALLOW if a in allow]
    return out


def _day_ok(days: str, d: datetime) -> bool:
    return days == "all" or (days == "weekdays") == (d.weekday() < 5)


def in_window(q: dict[str, Any], now: datetime) -> bool:
    if not q.get("enabled"):
        return False
    hm = now.strftime("%H:%M")
    start, end = q["from"], q["to"]
    if start < end:
        return start <= hm < end and _day_ok(q["days"], now)
    return (hm >= start and _day_ok(q["days"], now)) or (hm < end and _day_ok(q["days"], now - timedelta(days=1)))


class QuietHours:
    def __init__(self, store: Any, clock: Callable[[], datetime] = datetime.now) -> None:
        self.store, self.clock = store, clock

    def _all(self) -> dict[str, Any]:
        v = self.store.settings().get(SETTING)
        return v if isinstance(v, dict) else {}

    def get(self, person_id: str) -> dict[str, Any]:
        return {**DEFAULT, **(self._all().get(person_id) or {})}

    def set(self, person_id: str, data: Any) -> dict[str, Any]:
        q = clean(data)
        allq = self._all()
        allq[person_id] = q
        self.store.set_setting(SETTING, allq)
        return q

    def silent(self, person_id: str | None, topics: set[str]) -> bool:
        """Whether a notification of these topics (alarm / doorbell / people / urgent) goes to this person
        silently right now."""
        if not person_id:
            return False
        q = self.get(person_id)
        return in_window(q, self.clock()) and not (topics & set(q["allow"]))
