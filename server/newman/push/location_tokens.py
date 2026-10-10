"""iPhones' location push tokens (Apple's Location Push Service Extension).

An iPhone that shares its location with "Always" permission gives the hub a second APNs token, made
by `CLLocationManager.startMonitoringLocationPushes`. A push to it (`apns-push-type: location`,
topic `<bundle id>.location-query`) wakes the app's LocationPushExtension, which takes one fix and
reports it - even when the app has been swiped away, which a silent push can't do.

Kept per phone (its deviceId from /api/devices/register) with the APNs environment it was made in,
in the hub's database (its own table, so it comes along in backups). A token only counts while its
phone is registered; removing the phone (or its person stopping sharing) drops it.
"""
from __future__ import annotations

import re
import time
from typing import Any

ENVS = ("production", "sandbox")
TOKEN_RE = re.compile(r"[0-9a-fA-F]{16,256}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS location_push_tokens (
    device_id TEXT PRIMARY KEY, token TEXT NOT NULL, apns_env TEXT NOT NULL DEFAULT 'production',
    updated_at REAL NOT NULL
);
"""


def clean_token(token: Any) -> str:
    """A hex APNs token (ValueError otherwise)."""
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token.strip()):
        raise ValueError("token must be the location push token as a hex string")
    return token.strip().lower()


class LocationPushTokens:
    def __init__(self, store: Any) -> None:
        self._db = store._db  # the hub's one SQLite connection (store.py)
        self._db.executescript(SCHEMA)
        self._db.commit()

    def _exec(self, sql: str, args: tuple = ()) -> int:
        cur = self._db.execute(sql, args)
        self._db.commit()
        return cur.rowcount

    def set(self, device_id: str, token: str, env: str) -> None:
        env = env if env in ENVS else "production"
        self._exec("""INSERT INTO location_push_tokens (device_id, token, apns_env, updated_at) VALUES (?, ?, ?, ?)
                      ON CONFLICT(device_id) DO UPDATE SET token=excluded.token, apns_env=excluded.apns_env,
                      updated_at=excluded.updated_at""", (device_id, clean_token(token), env, time.time()))
        # A token belongs to one install: another device id holding it is stale.
        self._exec("DELETE FROM location_push_tokens WHERE token = ? AND device_id != ?", (token.strip().lower(), device_id))

    def get(self, device_id: str) -> tuple[str, str] | None:
        """(token, env) for a registered phone, else None."""
        r = self._db.execute("""SELECT t.token, t.apns_env FROM location_push_tokens t
                                JOIN phones p ON p.device_id = t.device_id WHERE t.device_id = ?""",
                             (device_id,)).fetchone()
        return (r[0], r[1]) if r else None

    def for_person(self, person_id: str) -> dict[str, tuple[str, str]]:
        """device id -> (token, env) for a person's registered iPhones that have one."""
        rows = self._db.execute("""SELECT t.device_id, t.token, t.apns_env FROM location_push_tokens t
                                   JOIN phones p ON p.device_id = t.device_id
                                   WHERE p.person_id = ? AND p.platform = 'ios'""", (person_id,))
        return {r[0]: (r[1], r[2]) for r in rows}

    def forget(self, device_id: str) -> bool:
        return self._exec("DELETE FROM location_push_tokens WHERE device_id = ?", (device_id,)) > 0

    def forget_person(self, person_id: str) -> int:
        """Drop every token of a person's phones (they stopped sharing), and any orphans."""
        n = self._exec("""DELETE FROM location_push_tokens WHERE device_id IN
                          (SELECT device_id FROM phones WHERE person_id = ?)""", (person_id,))
        self._exec("DELETE FROM location_push_tokens WHERE device_id NOT IN (SELECT device_id FROM phones)")
        return n
