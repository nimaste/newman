"""Live statuses on iPhones: ActivityKit tokens and the APNs "liveactivity" pushes (live.py decides what
each phone shows; this file only keeps tokens and talks to APNs).

Each iPhone gives the hub (POST /api/live/tokens) its push-to-start token - one per phone, for the
`NewmanLiveAttributes` activity type (iOS 17.2+) - and, for every live status running on it, that
activity's own update token (with the activity's key). Tokens are kept per phone (deviceId) with the
account and the sign-in they came with, like widget push tokens (push/widgets.py): a token only counts
while that sign-in does (signing out, revoking the device or removing the account stops its pushes and
the rows are dropped).

What the hub last sent each phone for each activity is kept too (live_sent), so a restart neither starts
an activity twice nor forgets to end one.

Pushes (apns-push-type "liveactivity", topic "<bundle id>.push-type.liveactivity"):
  start  -> the push-to-start token: {"aps": {"timestamp", "event": "start", "attributes-type":
            "NewmanLiveAttributes", "attributes": {"key", "title"}, "content-state", "alert": {"title", "body"}}}
  update -> the activity's token: {"aps": {"timestamp", "event": "update", "content-state"}}
  end    -> the activity's token: {"aps": {"timestamp", "event": "end", "content-state", "dismissal-date"}}
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from ..config import config

ATTRIBUTES_TYPE = "NewmanLiveAttributes"
KINDS = ("push-to-start", "update")
ENVS = ("production", "sandbox")
HEX = re.compile(r"[0-9a-fA-F]{16,512}")
KEY = re.compile(r"[A-Za-z0-9:._@+-]{1,200}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_tokens (
    device_id TEXT NOT NULL, kind TEXT NOT NULL, activity_key TEXT NOT NULL DEFAULT '', token TEXT NOT NULL,
    user_id TEXT NOT NULL, session_id TEXT, apns_env TEXT NOT NULL DEFAULT 'production', updated_at REAL NOT NULL,
    PRIMARY KEY (device_id, kind, activity_key)
);
CREATE TABLE IF NOT EXISTS live_sent (
    device_id TEXT NOT NULL, activity_key TEXT NOT NULL, slot TEXT NOT NULL, started_at REAL NOT NULL,
    content TEXT, PRIMARY KEY (device_id, activity_key)
);
"""


@dataclass(frozen=True)
class LiveToken:
    device_id: str
    kind: str
    activity_key: str
    token: str
    user_id: str
    apns_env: str


class LiveTokens:
    def __init__(self, store: Any) -> None:
        self._db = store._db  # the hub's one SQLite connection (store.py)
        self._db.executescript(SCHEMA)
        self._db.commit()

    def _exec(self, sql: str, args: tuple = ()) -> int:
        cur = self._db.execute(sql, args)
        self._db.commit()
        return cur.rowcount

    def register(self, user: dict[str, Any], session: dict[str, Any], data: dict[str, Any]) -> tuple[str, str, str]:
        """Keep one token: {kind: "push-to-start" | "update", token, deviceId, activityKey (update only),
        apnsEnvironment?}. Returns (device id, kind, activity key). ValueError on bad input."""
        kind, device_id, token = data.get("kind"), data.get("deviceId"), data.get("token")
        if kind not in KINDS:
            raise ValueError('kind must be "push-to-start" or "update"')
        if not isinstance(device_id, str) or not 0 < len(device_id.strip()) <= 128:
            raise ValueError("deviceId is required")
        device_id = device_id.strip()
        key = ""
        if kind == "update":
            key = data.get("activityKey")
            if not isinstance(key, str) or not KEY.fullmatch(key):
                raise ValueError("activityKey is required (the activity's attributes.key)")
        if not isinstance(token, str) or not HEX.fullmatch(token.strip()):
            raise ValueError("token must be the ActivityKit push token as a hex string")
        token = token.strip().lower()
        env = data.get("apnsEnvironment")
        if env not in ENVS:  # else the environment this phone registered for notifications with
            row = self._db.execute("SELECT apns_env FROM phones WHERE device_id = ?", (device_id,)).fetchone()
            env = row[0] if row and row[0] in ENVS else "production"
        self._exec("""INSERT INTO live_tokens (device_id, kind, activity_key, token, user_id, session_id, apns_env,
                      updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(device_id, kind, activity_key) DO UPDATE
                      SET token=excluded.token, user_id=excluded.user_id, session_id=excluded.session_id,
                      apns_env=excluded.apns_env, updated_at=excluded.updated_at""",
                   (device_id, kind, key, token, str(user["id"]), session.get("id"), env, time.time()))
        # A token belongs to one install (and one activity): anyone else holding it is stale.
        self._exec("DELETE FROM live_tokens WHERE token = ? AND NOT (device_id = ? AND kind = ? AND activity_key = ?)",
                   (token, device_id, kind, key))
        return device_id, kind, key

    def live(self) -> list[LiveToken]:
        """Tokens whose sign-in is still valid (and whose account is enabled)."""
        rows = self._db.execute("""SELECT t.device_id, t.kind, t.activity_key, t.token, t.user_id, t.apns_env
                                   FROM live_tokens t JOIN sessions s ON s.id = t.session_id JOIN users u ON u.id = t.user_id
                                   WHERE s.user_id = t.user_id AND s.expires_at > ? AND u.disabled = 0""",
                                (time.time(),))
        return [LiveToken(*r) for r in rows]

    def all(self) -> list[tuple[str, str, str]]:
        """(device id, kind, activity key) of every kept row (tests, diagnostics)."""
        return [tuple(r) for r in self._db.execute(
            "SELECT device_id, kind, activity_key FROM live_tokens ORDER BY device_id, kind, activity_key")]

    def forget(self, device_id: str, kind: str | None = None, activity_key: str | None = None) -> int:
        sql, args = "DELETE FROM live_tokens WHERE device_id = ?", [device_id]
        if kind:
            sql, args = sql + " AND kind = ?", args + [kind]
        if activity_key is not None:
            sql, args = sql + " AND activity_key = ?", args + [activity_key]
        n = self._exec(sql, tuple(args))
        if kind is None and activity_key is None:
            self._exec("DELETE FROM live_sent WHERE device_id = ?", (device_id,))
        return n

    def prune(self) -> int:
        """Drop tokens whose sign-in or account is gone (signed out, revoked, removed, expired)."""
        n = self._exec("""DELETE FROM live_tokens WHERE session_id IS NULL
                          OR session_id NOT IN (SELECT id FROM sessions WHERE expires_at > ?)
                          OR user_id NOT IN (SELECT id FROM users)""", (time.time(),))
        self._exec("DELETE FROM live_sent WHERE device_id NOT IN (SELECT device_id FROM live_tokens)")
        return n

    # -- what each phone was last sent ---------------------------------------------------------------

    def sent(self, device_id: str) -> dict[str, dict[str, Any]]:
        rows = self._db.execute("SELECT activity_key, slot, started_at, content FROM live_sent WHERE device_id = ?",
                                (device_id,))
        return {r[0]: {"slot": r[1], "startedAt": r[2], "content": json.loads(r[3]) if r[3] else None} for r in rows}

    def all_sent(self) -> list[tuple[str, str, str]]:
        return [tuple(r) for r in self._db.execute(
            "SELECT device_id, activity_key, slot FROM live_sent ORDER BY device_id, activity_key")]

    def save_sent(self, device_id: str, key: str, slot: str, content: dict[str, Any] | None,
                  started_at: float | None = None) -> None:
        self._exec("""INSERT INTO live_sent (device_id, activity_key, slot, started_at, content) VALUES (?, ?, ?, ?, ?)
                      ON CONFLICT(device_id, activity_key) DO UPDATE SET content=excluded.content""",
                   (device_id, key, slot, started_at or time.time(),
                    json.dumps(content, separators=(",", ":")) if content is not None else None))

    def drop_sent(self, device_id: str, key: str) -> None:
        self._exec("DELETE FROM live_sent WHERE device_id = ? AND activity_key = ?", (device_id, key))


# -- payloads ------------------------------------------------------------------------------------------

def start_payload(key: str, title: str, state: dict[str, Any], alert: dict[str, str], now: float) -> dict[str, Any]:
    return {"aps": {"timestamp": int(now), "event": "start", "attributes-type": ATTRIBUTES_TYPE,
                    "attributes": {"key": key, "title": title}, "content-state": state, "alert": alert}}


def update_payload(state: dict[str, Any], now: float) -> dict[str, Any]:
    return {"aps": {"timestamp": int(now), "event": "update", "content-state": state}}


def end_payload(state: dict[str, Any], now: float, dismiss_at: float) -> dict[str, Any]:
    return {"aps": {"timestamp": int(now), "event": "end", "content-state": state, "dismissal-date": int(dismiss_at)}}


def topic() -> str:
    return f"{config.apns_bundle_id}.push-type.liveactivity"


async def send(apns: Any, t: LiveToken, payload: dict[str, Any], *, priority: int = 10) -> None:
    """One liveactivity push (PushError from the sender on failure)."""
    await apns.send(t.token, payload, env=t.apns_env, push_type="liveactivity", topic=topic(), priority=priority,
                    expiration_s=3600)
