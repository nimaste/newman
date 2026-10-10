"""Kitchen timers set by voice, kept on the hub per account (a wall tablet's timers survive the
wall page reloading or the tablet restarting).

A timer is running until it ends, then ringing until someone stops it (a tap on the wall, or
"Hey Newman, stop"); one nobody stops is cleared after an hour. Listeners hear every change:
listener(user_id) - the API then pushes {"type": "timers"} to that account's open walls.
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
RING_FOR_S = 3600

SCHEMA = """CREATE TABLE IF NOT EXISTS voice_timers (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', seconds INTEGER NOT NULL,
    created_at REAL NOT NULL, ends_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'running', rang_at REAL
)"""


class Timers:
    def __init__(self, db: sqlite3.Connection, clock: Callable[[], float] = time.time) -> None:
        self.db, self.clock = db, clock
        self.db.execute(SCHEMA)
        self.db.commit()
        self.listeners: list[Callable[[str], Awaitable[None]]] = []
        self._wake = asyncio.Event()

    @staticmethod
    def _json(r: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        from .intents import timer_name  # (words for "Your pasta timer is done")
        t = {"id": r["id"], "label": r["label"], "seconds": r["seconds"], "createdAt": r["created_at"],
             "endsAt": r["ends_at"], "state": r["state"]}
        t["name"] = timer_name(t)
        t["doneText"] = f"Your {t['name']} is done."
        return t

    def list(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM voice_timers WHERE user_id = ? ORDER BY ends_at", (user_id,))
        return [self._json(r) for r in rows]

    def get(self, user_id: str, timer_id: str) -> dict[str, Any] | None:
        r = self.db.execute("SELECT * FROM voice_timers WHERE user_id = ? AND id = ?", (user_id, timer_id)).fetchone()
        return self._json(r) if r else None

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

    async def _changed(self, user_id: str) -> None:
        for fn in list(self.listeners):
            try:
                await fn(user_id)
            except Exception:  # a listener's problem isn't the timer's
                log.exception("timer listener failed")

    async def check(self) -> float:
        """Ring the timers that ended, clear the long-forgotten ones; seconds until the next end."""
        now = self.clock()
        due = self.db.execute("SELECT * FROM voice_timers WHERE state = 'running' AND ends_at <= ?", (now,)).fetchall()
        stale = self.db.execute("SELECT * FROM voice_timers WHERE state = 'ringing' AND rang_at < ?",
                                (now - RING_FOR_S,)).fetchall()
        users = set()
        for r in due:
            self.db.execute("UPDATE voice_timers SET state = 'ringing', rang_at = ? WHERE id = ?", (now, r["id"]))
            users.add(r["user_id"])
        for r in stale:
            self.db.execute("DELETE FROM voice_timers WHERE id = ?", (r["id"],))
            users.add(r["user_id"])
        if due or stale:
            self.db.commit()
        for u in users:
            await self._changed(u)
        nxt = self.db.execute("SELECT MIN(ends_at) FROM voice_timers WHERE state = 'running'").fetchone()[0]
        return max(0.05, nxt - now) if nxt else 60.0

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
