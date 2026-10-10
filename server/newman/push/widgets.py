"""Telling phones' widgets to reload when what they show changes.

Each phone gives the hub its widget push token (POST /api/widgets/push-token): an iPhone its WidgetKit
push token ("ios-widget"), an Android phone its FCM token ("android"). Kept per phone (deviceId) and
kind, with the account and the sign-in it came with, in the hub's database. A token only counts while
that sign-in does: signing out, revoking the device or removing the account stops its pushes (and the
row is dropped).

When a widget-showable device changes in a way a widget would show (its name, state, state label,
availability, what a tap does or its reading), each token gets ONE reload push - at most one per
PUSH_EVERY_S per token: a change inside that window is sent when it ends, folded into one. When an
account's widgets have asked for particular devices (GET /api/widgets/state?ids=), only changes to
those count for that account's phones.

iPhone: an APNs widget push (apns-push-type "widgets", topic "<bundle id>.push-type.widgets", payload
{"aps": {"content-changed": true}}); WidgetKit then reloads the widgets' timelines. Android: an FCM
data message {"type": "widgets"} - skipped when FCM isn't set up (Android widgets also refresh on their
own: on a tap, from the app's live connection, and every 15 minutes).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from ..config import config
from ..state import Entity
from .apns import PushError

log = logging.getLogger(__name__)

PUSH_EVERY_S = 30.0
SHOWN_KEEP_S = 3 * 86400  # an account's "devices its widgets show", forgotten when not asked for again
KINDS = ("ios-widget", "android")
ENVS = ("production", "sandbox")
HEX = re.compile(r"[0-9a-fA-F]{16,256}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS widget_push_tokens (
    device_id TEXT NOT NULL, kind TEXT NOT NULL, token TEXT NOT NULL, user_id TEXT NOT NULL,
    session_id TEXT, apns_env TEXT NOT NULL DEFAULT 'production', updated_at REAL NOT NULL,
    PRIMARY KEY (device_id, kind)
);
"""


@dataclass(frozen=True)
class WidgetToken:
    device_id: str
    kind: str
    token: str
    user_id: str
    apns_env: str


class WidgetTokens:
    def __init__(self, store: Any) -> None:
        self._db = store._db  # the hub's one SQLite connection (store.py)
        self._db.executescript(SCHEMA)
        self._db.commit()

    def _exec(self, sql: str, args: tuple = ()) -> int:
        cur = self._db.execute(sql, args)
        self._db.commit()
        return cur.rowcount

    def register(self, user: dict[str, Any], session: dict[str, Any], data: dict[str, Any]) -> bool:
        """Keep (or, with a null / empty token, forget) one phone's token. True when one is kept."""
        kind, device_id, token = data.get("kind"), data.get("deviceId"), data.get("token")
        if kind not in KINDS:
            raise ValueError('kind must be "ios-widget" or "android"')
        if not isinstance(device_id, str) or not 0 < len(device_id.strip()) <= 128:
            raise ValueError("deviceId is required")
        device_id = device_id.strip()
        if token is None or token == "":
            self._exec("DELETE FROM widget_push_tokens WHERE device_id = ? AND kind = ?", (device_id, kind))
            return False
        if not isinstance(token, str) or not token.strip() or len(token) > 4096:
            raise ValueError("token must be a string")
        token = token.strip()
        if kind == "ios-widget":
            if not HEX.fullmatch(token):
                raise ValueError("token must be the widget push token as a hex string")
            token = token.lower()
        env = data.get("apnsEnvironment")
        if env not in ENVS:  # else the environment this phone registered for notifications with
            row = self._db.execute("SELECT apns_env FROM phones WHERE device_id = ?", (device_id,)).fetchone()
            env = row[0] if row and row[0] in ENVS else "production"
        self._exec("""INSERT INTO widget_push_tokens (device_id, kind, token, user_id, session_id, apns_env, updated_at)
                      VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(device_id, kind) DO UPDATE SET token=excluded.token,
                      user_id=excluded.user_id, session_id=excluded.session_id, apns_env=excluded.apns_env,
                      updated_at=excluded.updated_at""",
                   (device_id, kind, token, str(user["id"]), session.get("id"), env, time.time()))
        # A token belongs to one install: anyone else holding it is stale.
        self._exec("DELETE FROM widget_push_tokens WHERE token = ? AND NOT (device_id = ? AND kind = ?)",
                   (token, device_id, kind))
        return True

    def live(self) -> list[WidgetToken]:
        """Tokens whose sign-in is still valid (and whose account is enabled)."""
        rows = self._db.execute("""SELECT t.device_id, t.kind, t.token, t.user_id, t.apns_env FROM widget_push_tokens t
                                   JOIN sessions s ON s.id = t.session_id JOIN users u ON u.id = t.user_id
                                   WHERE s.user_id = t.user_id AND s.expires_at > ? AND u.disabled = 0""",
                                (time.time(),))
        return [WidgetToken(*r) for r in rows]

    def get(self, device_id: str, kind: str) -> WidgetToken | None:
        return next((t for t in self.live() if (t.device_id, t.kind) == (device_id, kind)), None)

    def all(self) -> list[tuple[str, str, str]]:
        """(device id, kind, user id) of every kept row (tests, diagnostics)."""
        return [tuple(r) for r in self._db.execute(
            "SELECT device_id, kind, user_id FROM widget_push_tokens ORDER BY device_id, kind")]

    def forget(self, device_id: str, kind: str | None = None) -> int:
        if kind:
            return self._exec("DELETE FROM widget_push_tokens WHERE device_id = ? AND kind = ?", (device_id, kind))
        return self._exec("DELETE FROM widget_push_tokens WHERE device_id = ?", (device_id,))

    def prune(self) -> int:
        """Drop tokens whose sign-in or account is gone (signed out, revoked, removed, expired)."""
        return self._exec("""DELETE FROM widget_push_tokens WHERE session_id IS NULL
                             OR session_id NOT IN (SELECT id FROM sessions WHERE expires_at > ?)
                             OR user_id NOT IN (SELECT id FROM users)""", (time.time(),))


class WidgetPush:
    def __init__(self, store: Any, notifier: Any, *, view: Callable[[Entity], Any],
                 showable: Callable[[Entity], bool], every_s: float = PUSH_EVERY_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.tokens = WidgetTokens(store)
        self._notifier = notifier
        self._view, self._showable = view, showable
        self.every_s, self.clock = every_s, clock
        self._shown: dict[str, dict[str, float]] = {}   # user id -> {device id: when its widget asked}
        self._last: dict[tuple[str, str], float] = {}   # (device id, kind) -> when its last push went
        self._waiting: dict[tuple[str, str], asyncio.TimerHandle] = {}
        self._tasks: set[asyncio.Task] = set()
        self.sent = 0

    # -- what each account's widgets show ------------------------------------------------------------

    def saw(self, user_id: str, ids: list[str]) -> None:
        now = time.time()
        mine = self._shown.setdefault(user_id, {})
        for i in ids:
            mine[i] = now
        for i in [i for i, t in mine.items() if now - t > SHOWN_KEEP_S]:
            del mine[i]

    def showing(self, device_id: str) -> bool:
        """Whether any account's widget asked for this device lately (known since the last restart)."""
        now = time.time()
        return any(now - mine[device_id] <= SHOWN_KEEP_S for mine in self._shown.values() if device_id in mine)

    def _wants(self, user_id: str, device_id: str) -> bool:
        mine = self._shown.get(user_id)
        if not mine:
            return True  # nothing known about this account's widgets yet (e.g. after a restart)
        t = mine.get(device_id)
        return t is not None and time.time() - t <= SHOWN_KEEP_S

    # -- changes ---------------------------------------------------------------------------------------

    async def changed(self, old: Entity | None, new: Entity) -> None:
        if not self._showable(new):
            return
        if old is not None and self._view(old) == self._view(new):
            return  # nothing a widget shows changed (another reading, a timestamp…)
        for t in self.tokens.live():
            if self._wants(t.user_id, new.id):
                self._schedule((t.device_id, t.kind))

    def _schedule(self, key: tuple[str, str]) -> None:
        if key in self._waiting:
            return  # already due: this change goes with it
        wait = self._last.get(key, -1e18) + self.every_s - self.clock()
        if wait <= 0:
            self._last[key] = self.clock()
            self._spawn(self._send(key))
            return
        loop = asyncio.get_running_loop()
        self._waiting[key] = loop.call_later(wait, self._due, key)

    def _due(self, key: tuple[str, str]) -> None:
        self._waiting.pop(key, None)
        self._last[key] = self.clock()
        self._spawn(self._send(key))

    def _spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send(self, key: tuple[str, str]) -> None:
        t = self.tokens.get(*key)
        if t is None:
            return  # forgotten / signed out meanwhile
        try:
            if t.kind == "ios-widget":
                apns = self._notifier._apns
                if not apns.enabled:
                    return
                await apns.send(t.token, {"aps": {"content-changed": True}}, env=t.apns_env, push_type="widgets",
                                topic=f"{config.apns_bundle_id}.push-type.widgets", priority=5, expiration_s=3600)
            else:
                fcm = self._notifier._fcm
                if not fcm.enabled:
                    return  # no FCM on this hub: Android widgets refresh by themselves
                await fcm.send(t.token, {"type": "widgets"}, collapse_key="newman-widgets", ttl_s=900)
            self.sent += 1
        except PushError as e:
            log.warning("Widget push -> %s (%s) failed: %s", t.device_id, t.kind, e)
            if e.dead_token:
                self.tokens.forget(t.device_id, t.kind)
        except Exception:
            log.exception("Widget push -> %s (%s) crashed", t.device_id, t.kind)

    async def close(self) -> None:
        for h in self._waiting.values():
            h.cancel()
        self._waiting.clear()
        for task in list(self._tasks):
            task.cancel()
