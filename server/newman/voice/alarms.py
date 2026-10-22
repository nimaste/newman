"""Alarms set on a wall tablet (by voice or by touch), kept on the hub per account like the timers: they
belong to the tablet they were set on and still go off after the wall app, the tablet or the hub restarts.

An alarm is a time of day (hour, minute) in the hub's own time zone (its TZ, like the clock and the
scheduler) with the days it repeats on (0 = Sunday … 6 = Saturday; none = once). A one-time alarm has
the date it goes off. Each one is on or off, may skip one date ("skip my weekday alarm tomorrow"), and
is set, ringing or snoozed. Ringing stops with Stop, or by itself after the tablet's "Stop ringing after"
(ring_for); a one-time alarm then turns off, a repeating one waits for its next day. Times are worked
out on the calendar, so a 7:00 alarm stays at 7:00 across a daylight-saving change (a time the change
skips rings at the same moment the clock shows after it, e.g. 2:30 -> 3:30).

Listeners hear every change: listener(user_id) - the API pushes {"type": "alarms"} to that account's walls.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta, tzinfo
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

MAX_PER_ACCOUNT = 20
RING_FOR_S = 600   # when the tablet hasn't said its own "Stop ringing after"
SNOOZE_S = 540     # … or its own "Snooze for"
DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
WEEKDAYS = frozenset({1, 2, 3, 4, 5})
WEEKEND = frozenset({0, 6})

SCHEMA = """CREATE TABLE IF NOT EXISTS voice_alarms (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', hour INTEGER NOT NULL,
    minute INTEGER NOT NULL, days TEXT NOT NULL DEFAULT '', on_date TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1, skip TEXT NOT NULL DEFAULT '', after REAL NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'set', rang_at REAL, snooze_until REAL, created_at REAL NOT NULL
)"""


def js_day(d: date) -> int:
    """0 = Sunday … 6 = Saturday (the wall's day circles: S M T W T F S)."""
    return (d.weekday() + 1) % 7


def say_clock(hour: int, minute: int) -> str:
    """"7:00 AM", "12:30 PM"."""
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def repeat_title(days: set[int] | frozenset[int]) -> str:
    """"Weekdays", "Weekends", "Every day", "Saturdays", "Mondays and Wednesdays", "Mon Wed Fri"; "" for once."""
    d = frozenset(days)
    if not d:
        return ""
    if len(d) == 7:
        return "Every day"
    if d == WEEKDAYS:
        return "Weekdays"
    if d == WEEKEND:
        return "Weekends"
    order = sorted(d, key=lambda x: (x + 6) % 7)  # Monday first
    if len(order) <= 2:
        return " and ".join(DAY_NAMES[x] + "s" for x in order)
    return " ".join(DAY_NAMES[x][:3] for x in order)


def say_repeat(days: set[int] | frozenset[int]) -> str:
    """For replies: "on weekdays", "every day", "every Saturday", "on Mondays and Wednesdays"."""
    d = frozenset(days)
    if len(d) == 7:
        return "every day"
    if d == WEEKDAYS:
        return "on weekdays"
    if d == WEEKEND:
        return "on weekends"
    if len(d) == 1:
        return "every " + DAY_NAMES[next(iter(d))]
    order = sorted(d, key=lambda x: (x + 6) % 7)
    names = [DAY_NAMES[x] + "s" for x in order]
    return "on " + (" and ".join(names) if len(names) == 2 else ", ".join(names[:-1]) + " and " + names[-1])


class Alarms:
    def __init__(self, db: sqlite3.Connection, clock: Callable[[], float] = time.time,
                 ring_for: Callable[[str], float] | None = None, tz: tzinfo | None = None) -> None:
        """`tz`: the time zone the alarms' times are in (None = the hub's own, its TZ)."""
        self.db, self.clock, self.tz = db, clock, tz
        self.ring_for = ring_for or (lambda _uid: RING_FOR_S)
        self.db.execute(SCHEMA)
        self.db.commit()
        self.listeners: list[Callable[[str], Awaitable[None]]] = []
        self._wake = asyncio.Event()

    # -- the calendar ---------------------------------------------------------------------------------

    def local(self, ts: float) -> datetime:
        return datetime.fromtimestamp(ts, self.tz)

    def at(self, d: date, hour: int, minute: int) -> float:
        """The moment the clock shows hour:minute on day d (DST-aware)."""
        if self.tz is not None:
            return datetime(d.year, d.month, d.day, hour, minute, tzinfo=self.tz).timestamp()
        return datetime(d.year, d.month, d.day, hour, minute).timestamp()

    def today(self) -> date:
        return self.local(self.clock()).date()

    def coming(self, hour: int, minute: int, days: set[int] | frozenset[int] = frozenset(),
               after: float | None = None, skip: str = "") -> float | None:
        """The next time hour:minute comes after `after` (now) on one of `days` (any day when none),
        not on the `skip` date."""
        after = self.clock() if after is None else after
        start = self.local(after).date()
        for i in range(0, 9):
            d = start + timedelta(days=i)
            if days and js_day(d) not in days:
                continue
            if skip and d.isoformat() == skip:
                continue
            ts = self.at(d, hour, minute)
            if ts > after:
                return ts
        return None

    def day_word(self, ts: float) -> str:
        """"today", "tomorrow", "on Friday" (within the week), "on October 30"."""
        d = self.local(ts).date()
        n = (d - self.today()).days
        if n == 0:
            return "today"
        if n == 1:
            return "tomorrow"
        if 1 < n < 7:
            return "on " + DAY_NAMES[js_day(d)]
        return f"on {d.strftime('%B')} {d.day}"

    def say_when(self, a: dict[str, Any]) -> str:
        """"on weekdays" / "tomorrow" for an alarm (json)."""
        if a["days"]:
            return say_repeat(set(a["days"]))
        return self.day_word(a["nextAt"]) if a.get("nextAt") else "once"

    def _next(self, r: sqlite3.Row | dict[str, Any], now: float) -> float | None:
        """When an alarm next goes off (its snooze first), or None when it's off / its day has gone."""
        if r["state"] == "snoozed" and r["snooze_until"]:
            return r["snooze_until"]
        if not r["enabled"]:
            return None
        after = max(now, r["after"] or 0)
        days = _days(r["days"])
        if days:
            return self.coming(r["hour"], r["minute"], days, after, r["skip"])
        if not r["on_date"]:
            return None
        ts = self.at(date.fromisoformat(r["on_date"]), r["hour"], r["minute"])
        return ts if ts > after else None

    # -- reading ---------------------------------------------------------------------------------------

    def _json(self, r: sqlite3.Row, now: float) -> dict[str, Any]:
        days = _days(r["days"])
        nxt = self._next(r, now)
        skip = r["skip"] if days and r["skip"] and r["skip"] >= self.local(now).date().isoformat() else None
        a = {"id": r["id"], "label": r["label"], "hour": r["hour"], "minute": r["minute"], "days": sorted(days),
             "enabled": bool(r["enabled"]), "state": r["state"], "nextAt": nxt, "skip": skip,
             "snoozeUntil": r["snooze_until"] if r["state"] == "snoozed" else None,
             "ringingSince": r["rang_at"] if r["state"] == "ringing" else None,
             "time": say_clock(r["hour"], r["minute"]), "repeat": repeat_title(days), "createdAt": r["created_at"]}
        a["name"] = alarm_name(a)
        return a

    def list(self, user_id: str) -> list[dict[str, Any]]:
        """By time of day."""
        now = self.clock()
        rows = self.db.execute("SELECT * FROM voice_alarms WHERE user_id = ? ORDER BY hour, minute, created_at",
                               (user_id,)).fetchall()
        return [self._json(r, now) for r in rows]

    def get(self, user_id: str, alarm_id: str) -> dict[str, Any] | None:
        r = self._row(user_id, alarm_id)
        return self._json(r, self.clock()) if r else None

    def _row(self, user_id: str, alarm_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM voice_alarms WHERE user_id = ? AND id = ?", (user_id, alarm_id)).fetchone()

    # -- changing --------------------------------------------------------------------------------------

    @staticmethod
    def _check(hour: Any, minute: Any, days: Any) -> tuple[int, int, set[int]]:
        try:
            h, m = int(hour), int(minute)
            d = {int(x) for x in (days or [])}
        except (TypeError, ValueError):
            raise ValueError("An alarm needs an hour (0-23) and a minute (0-59).") from None
        if not (0 <= h <= 23 and 0 <= m <= 59) or not d <= set(range(7)):
            raise ValueError("An alarm needs an hour (0-23) and a minute (0-59), and days 0 (Sunday) to 6.")
        return h, m, d

    async def add(self, user_id: str, hour: int, minute: int, days: list[int] | set[int] | None = None,
                  label: str = "", on_date: date | None = None) -> dict[str, Any]:
        """A repeating alarm (days), or once: on `on_date`, else the next time hour:minute comes."""
        h, m, d = self._check(hour, minute, days)
        n = self.db.execute("SELECT COUNT(*) FROM voice_alarms WHERE user_id = ?", (user_id,)).fetchone()[0]
        if n >= MAX_PER_ACCOUNT:
            raise ValueError(f"You already have {MAX_PER_ACCOUNT} alarms.")
        now = self.clock()
        when = ""
        if not d:
            if on_date is None or self.at(on_date, h, m) <= now:
                when = self.local(self.coming(h, m, after=now)).date().isoformat()  # type: ignore[arg-type]
            else:
                when = on_date.isoformat()
        aid = "al_" + secrets.token_hex(4)
        self.db.execute("INSERT INTO voice_alarms (id, user_id, label, hour, minute, days, on_date, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (aid, user_id, label.strip()[:40], h, m, _days_text(d), when, now))
        self.db.commit()
        await self._changed(user_id)
        return self.get(user_id, aid)  # type: ignore[return-value]

    async def update(self, user_id: str, alarm_id: str, **fields: Any) -> dict[str, Any] | None:
        """Change hour / minute / days / label / enabled (the Edit screen's Save, the on/off switch,
        "change my 7 o'clock alarm to 7:30"). A change clears a skipped day; a one-time alarm keeps its day
        while that's still to come, else (turned on again, say) it's set for the next time its time comes."""
        r = self._row(user_id, alarm_id)
        if not r:
            return None
        h, m, d = self._check(fields.get("hour", r["hour"]), fields.get("minute", r["minute"]),
                              fields["days"] if "days" in fields else _days(r["days"]))
        label = str(fields["label"]).strip()[:40] if "label" in fields else r["label"]
        enabled = bool(fields["enabled"]) if "enabled" in fields else bool(r["enabled"])
        now = self.clock()
        when = r["on_date"]
        timing = any(k in fields for k in ("hour", "minute", "days")) or (enabled and not r["enabled"])
        if d:
            when = ""
        elif not (when and self.at(date.fromisoformat(when), h, m) > now):
            # (a one-time alarm keeps its day while that's still to come, else the next time it comes)
            when = self.local(self.coming(h, m, after=now)).date().isoformat()  # type: ignore[arg-type]
        state, snooze = r["state"], r["snooze_until"]
        if timing or not enabled:
            state, snooze = ("set", None) if state != "ringing" else (state, snooze)
        self.db.execute("UPDATE voice_alarms SET hour = ?, minute = ?, days = ?, label = ?, enabled = ?, on_date = ?, "
                        "skip = '', after = ?, state = ?, snooze_until = ? WHERE id = ?",
                        (h, m, _days_text(d), label, int(enabled), when, now if timing else r["after"], state, snooze,
                         alarm_id))
        self.db.commit()
        await self._changed(user_id)
        return self.get(user_id, alarm_id)

    async def skip_next(self, user_id: str, alarm_id: str) -> dict[str, Any] | None:
        """Skip its next day only ("just tomorrow"); a one-time alarm is simply turned off."""
        r = self._row(user_id, alarm_id)
        if not r:
            return None
        days = _days(r["days"])
        if not days:
            return await self.update(user_id, alarm_id, enabled=False)
        nxt = self.coming(r["hour"], r["minute"], days, max(self.clock(), r["after"] or 0))
        if nxt is not None:
            self.db.execute("UPDATE voice_alarms SET skip = ? WHERE id = ?", (self.local(nxt).date().isoformat(), alarm_id))
            self.db.commit()
            await self._changed(user_id)
        return self.get(user_id, alarm_id)

    async def remove(self, user_id: str, alarm_id: str) -> bool:
        n = self.db.execute("DELETE FROM voice_alarms WHERE user_id = ? AND id = ?", (user_id, alarm_id)).rowcount
        self.db.commit()
        if n:
            await self._changed(user_id)
        return bool(n)

    async def snooze(self, user_id: str, alarm_id: str, seconds: float) -> dict[str, Any] | None:
        r = self._row(user_id, alarm_id)
        if not r:
            return None
        self.db.execute("UPDATE voice_alarms SET state = 'snoozed', snooze_until = ?, rang_at = NULL WHERE id = ?",
                        (self.clock() + max(60.0, float(seconds)), alarm_id))
        self.db.commit()
        await self._changed(user_id)
        return self.get(user_id, alarm_id)

    async def stop(self, user_id: str, alarm_id: str) -> dict[str, Any] | None:
        """Stop it ringing, or call off its snooze: a one-time alarm turns off, a repeating one waits for its next day."""
        r = self._row(user_id, alarm_id)
        if not r:
            return None
        self._stop(r)
        self.db.commit()
        await self._changed(user_id)
        return self.get(user_id, alarm_id)

    def _stop(self, r: sqlite3.Row) -> None:
        enabled = r["enabled"] if _days(r["days"]) else 0
        self.db.execute("UPDATE voice_alarms SET state = 'set', snooze_until = NULL, rang_at = NULL, enabled = ? "
                        "WHERE id = ?", (enabled, r["id"]))

    async def stop_ringing(self, user_id: str) -> int:
        rows = self.db.execute("SELECT * FROM voice_alarms WHERE user_id = ? AND state = 'ringing'", (user_id,)).fetchall()
        for r in rows:
            self._stop(r)
        if rows:
            self.db.commit()
            await self._changed(user_id)
        return len(rows)

    async def _changed(self, user_id: str) -> None:
        self._wake.set()
        for fn in list(self.listeners):
            try:
                await fn(user_id)
            except Exception:  # a listener's problem isn't the alarm's
                log.exception("alarm listener failed")

    # -- the clock -------------------------------------------------------------------------------------

    async def check(self) -> float:
        """Ring the alarms that are due (a snooze that ran out too), stop the ones that rang for the
        tablet's "Stop ringing after"; one missed by more than that (the hub was off) is let go. Seconds
        until the next thing to do."""
        now = self.clock()
        users: set[str] = set()
        nxt: list[float] = []
        for r in self.db.execute("SELECT * FROM voice_alarms").fetchall():
            cap = self.ring_for(r["user_id"])
            if r["state"] == "ringing":
                until = (r["rang_at"] or now) + cap
                if until <= now:
                    self._stop(r)
                    users.add(r["user_id"])
                else:
                    nxt.append(until)
                continue
            due = self._next(r, now - cap - 1)  # (a time just gone counts, up to the ring time)
            if due is None:
                if r["enabled"] and not _days(r["days"]) and r["state"] == "set":
                    self.db.execute("UPDATE voice_alarms SET enabled = 0 WHERE id = ?", (r["id"],))  # its day went by
                    users.add(r["user_id"])
                continue
            if due > now:
                nxt.append(due)
                continue
            snoozed = r["state"] == "snoozed"
            occurrence = due if not snoozed else (r["after"] or 0)
            skip = r["skip"] if r["skip"] and r["skip"] >= self.local(now).date().isoformat() else ""
            self.db.execute("UPDATE voice_alarms SET state = 'ringing', rang_at = ?, snooze_until = NULL, after = ?, "
                            "skip = ? WHERE id = ?", (now, max(occurrence, r["after"] or 0), skip, r["id"]))
            users.add(r["user_id"])
            nxt.append(now + cap)
        if users:
            self.db.commit()
        for u in users:
            await self._changed(u)
        return max(0.05, min(nxt) - now) if nxt else 60.0

    async def run(self) -> None:
        while True:
            try:
                wait = await self.check()
            except Exception:
                log.exception("alarm check failed")
                wait = 5.0
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), min(wait, 30.0))
            except asyncio.TimeoutError:
                pass


def _days(text: str) -> set[int]:
    return {int(c) for c in (text or "") if c.isdigit() and int(c) < 7}


def _days_text(days: set[int]) -> str:
    return "".join(str(d) for d in sorted(days))


def alarm_name(a: dict[str, Any]) -> str:
    """How replies name it: "gym alarm", or by its time "7:00 AM alarm"."""
    return f"{a['label']} alarm" if a.get("label") else f"{say_clock(a['hour'], a['minute'])} alarm"
