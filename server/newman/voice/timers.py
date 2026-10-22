"""Kitchen timers set by voice or by touch, kept on the hub per account (a wall tablet's timers survive
the wall page reloading or the tablet restarting).

A timer is running until it ends, then ringing until someone stops it (a tap on the wall, or "Hey
Newman, stop"); one nobody stops is removed after the tablet's "Stop ringing after" time (ring_for).
A running timer can be paused (it keeps the time it had left), resumed, given more or less time,
restarted (back to its full time, `seconds`) or changed (a new full time, from now). Listeners hear
every change: listener(user_id) - the API then pushes {"type": "timers"} to that account's open walls.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import sqlite3
import time
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

MAX_PER_ACCOUNT = 10
MAX_SECONDS = 24 * 3600
RING_FOR_S = 600  # when the tablet hasn't said its own "Stop ringing after"

SCHEMA = """CREATE TABLE IF NOT EXISTS voice_timers (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', seconds INTEGER NOT NULL,
    created_at REAL NOT NULL, ends_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'running', rang_at REAL
)"""


class Timers:
    def __init__(self, db: sqlite3.Connection, clock: Callable[[], float] = time.time,
                 ring_for: Callable[[str], float] | None = None) -> None:
        self.db, self.clock = db, clock
        self.ring_for = ring_for or (lambda _uid: RING_FOR_S)
        self.db.execute(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(voice_timers)")}
        if "left_s" not in cols:  # paused: the time it had left
            self.db.execute("ALTER TABLE voice_timers ADD COLUMN left_s REAL")
        self.db.commit()
        self.listeners: list[Callable[[str], Awaitable[None]]] = []
        self._wake = asyncio.Event()

    def _json(self, r: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        from .intents import timer_name  # (words for "Your pasta timer is done")
        t = {"id": r["id"], "label": r["label"], "seconds": r["seconds"], "createdAt": r["created_at"],
             "endsAt": r["ends_at"], "state": r["state"]}
        if r["state"] == "paused":
            t["left"] = r["left_s"] or 0.0
        t["name"] = timer_name(t)
        t["doneText"] = f"Your {t['name']} is done."
        return t

    def list(self, user_id: str) -> list[dict[str, Any]]:
        """In the order they were set ("the second timer")."""
        rows = self.db.execute("SELECT * FROM voice_timers WHERE user_id = ? ORDER BY created_at, rowid", (user_id,))
        return [self._json(r) for r in rows]

    def get(self, user_id: str, timer_id: str) -> dict[str, Any] | None:
        r = self._row(user_id, timer_id)
        return self._json(r) if r else None

    def _row(self, user_id: str, timer_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM voice_timers WHERE user_id = ? AND id = ?", (user_id, timer_id)).fetchone()

    async def add(self, user_id: str, seconds: int, label: str = "") -> dict[str, Any]:
        if not 1 <= seconds <= MAX_SECONDS:
            raise ValueError("Timers can be up to 24 hours.")
        if len(self.list(user_id)) >= MAX_PER_ACCOUNT:
            raise ValueError(f"You already have {MAX_PER_ACCOUNT} timers.")
        now = self.clock()
        tid = "tm_" + secrets.token_hex(4)
        self.db.execute("INSERT INTO voice_timers (id, user_id, label, seconds, created_at, ends_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (tid, user_id, label.strip()[:40], int(seconds), now, now + seconds))
        self.db.commit()
        self._wake.set()
        await self._changed(user_id)
        return self.get(user_id, tid)  # type: ignore[return-value]

    async def remove(self, user_id: str, ids: list[str]) -> int:
        """Cancel running timers / stop ringing ones."""
        if not ids:
            return 0
        n = 0
        for tid in ids:
            n += self.db.execute("DELETE FROM voice_timers WHERE user_id = ? AND id = ?", (user_id, tid)).rowcount
        self.db.commit()
        if n:
            self._wake.set()
            await self._changed(user_id)
        return n

    async def stop_ringing(self, user_id: str) -> int:
        ids = [t["id"] for t in self.list(user_id) if t["state"] == "ringing"]
        return await self.remove(user_id, ids)

    def left(self, t: dict[str, Any], now: float | None = None) -> float:
        """Seconds left on a timer (0 when it's ringing)."""
        if t["state"] == "paused":
            return float(t.get("left") or 0.0)
        if t["state"] == "ringing":
            return 0.0
        return max(0.0, t["endsAt"] - (self.clock() if now is None else now))

    async def change(self, user_id: str, timer_id: str, do: str, seconds: float = 0) -> dict[str, Any] | None:
        """do: pause | resume | restart | add (seconds more; a ringing one runs again) | take (seconds
        less; it rings once nothing is left) | set (a new full time of `seconds`, from now). None when
        there's no such timer."""
        r = self._row(user_id, timer_id)
        if not r:
            return None
        now = self.clock()
        state, ends, left, full = r["state"], r["ends_at"], r["left_s"], r["seconds"]
        cur = (left or 0.0) if state == "paused" else 0.0 if state == "ringing" else max(0.0, ends - now)
        if do == "pause":
            if state == "running":
                state, left = "paused", cur
        elif do == "resume":
            if state == "paused":
                state, ends, left = "running", now + cur, None
        elif do == "restart":
            state, ends, left = "running", now + full, None
        elif do in ("add", "take"):
            delta = float(seconds) if do == "add" else -float(seconds)
            new = cur + delta
            if new > MAX_SECONDS:
                raise ValueError("Timers can be up to 24 hours.")
            if state == "paused":
                left = max(0.0, new)
                if left <= 0:
                    state, ends, left = "running", now, None  # nothing left: it rings
            elif state == "ringing":
                if delta > 0:
                    state, ends = "running", now + delta
            else:
                ends = now + max(0.0, new)
        elif do == "set":
            if not 1 <= seconds <= MAX_SECONDS:
                raise ValueError("Timers can be up to 24 hours.")
            full = int(round(seconds))
            if state == "paused":
                left = float(full)
            else:
                state, ends, left = "running", now + full, None
        else:
            raise ValueError(f"Unknown change: {do}")
        self.db.execute("UPDATE voice_timers SET state = ?, ends_at = ?, left_s = ?, seconds = ?, rang_at = NULL "
                        "WHERE id = ?", (state, ends, left, full, timer_id))
        self.db.commit()
        self._wake.set()
        await self._changed(user_id)
        return self.get(user_id, timer_id)

    async def _changed(self, user_id: str) -> None:
        for fn in list(self.listeners):
            try:
                await fn(user_id)
            except Exception:  # a listener's problem isn't the timer's
                log.exception("timer listener failed")

    async def check(self) -> float:
        """Ring the timers that ended, remove the ones that rang for the tablet's "Stop ringing after";
        seconds until the next thing to do."""
        now = self.clock()
        due = self.db.execute("SELECT * FROM voice_timers WHERE state = 'running' AND ends_at <= ?", (now,)).fetchall()
        users = set()
        for r in due:
            self.db.execute("UPDATE voice_timers SET state = 'ringing', rang_at = ? WHERE id = ?", (now, r["id"]))
            users.add(r["user_id"])
        nxt: list[float] = []
        for r in self.db.execute("SELECT * FROM voice_timers WHERE state = 'ringing'").fetchall():
            until = (r["rang_at"] or now) + self.ring_for(r["user_id"])
            if until <= now:
                self.db.execute("DELETE FROM voice_timers WHERE id = ?", (r["id"],))
                users.add(r["user_id"])
            else:
                nxt.append(until)
        if due or users:
            self.db.commit()
        for u in users:
            await self._changed(u)
        end = self.db.execute("SELECT MIN(ends_at) FROM voice_timers WHERE state = 'running'").fetchone()[0]
        if end:
            nxt.append(end)
        return max(0.05, min(nxt) - now) if nxt else 60.0

    async def run(self) -> None:
        while True:
            try:
                wait = await self.check()
            except Exception:
                log.exception("timer check failed")
                wait = 5.0
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), min(wait, 60.0))
            except asyncio.TimeoutError:
                pass
