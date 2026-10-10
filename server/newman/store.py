"""SQLite persistence: people, phones, automations, runs, integrations,
entity renames and the activity log. Live entity state is not stored - the
integrations rebuild it on startup."""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dashboards import with_home


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"


@dataclass
class Phone:
    device_id: str
    token: str
    platform: str  # ios | android | test
    person_id: str | None = None
    apns_env: str = "production"
    friendly_name: str = ""
    device_model: str = ""
    registered_at: float = 0.0
    last_seen: float = 0.0
    failures: int = 0

    @property
    def display_name(self) -> str:
        return self.friendly_name or self.device_model or f"{self.platform} (…{self.token[-6:]})"

    def to_json(self) -> dict[str, Any]:
        return {"deviceId": self.device_id, "name": self.display_name, "platform": self.platform,
                "personId": self.person_id, "registeredAt": self.registered_at, "lastSeen": self.last_seen}


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS people (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS phones (
    device_id TEXT PRIMARY KEY, token TEXT NOT NULL, platform TEXT NOT NULL,
    person_id TEXT, apns_env TEXT NOT NULL DEFAULT 'production',
    friendly_name TEXT NOT NULL DEFAULT '', device_model TEXT NOT NULL DEFAULT '',
    registered_at REAL NOT NULL, last_seen REAL NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS automations (
    id TEXT PRIMARY KEY, body TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, automation_id TEXT NOT NULL, started_at REAL NOT NULL, body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_auto ON runs(automation_id, started_at DESC);
CREATE TABLE IF NOT EXISTS integrations (
    id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
    config TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS holds (
    key TEXT PRIMARY KEY, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entity_names (
    entity_id TEXT PRIMARY KEY, name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entity_settings (
    entity_id TEXT PRIMARY KEY, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '', entity_id TEXT, data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts DESC);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, name TEXT NOT NULL, role TEXT NOT NULL,
    password_hash TEXT NOT NULL, pin_hash TEXT, person_id TEXT,
    totp_secret TEXT, totp_enabled INTEGER NOT NULL DEFAULT 0, totp_last_step INTEGER NOT NULL DEFAULT 0,
    recovery TEXT NOT NULL DEFAULT '[]', disabled INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL, password_changed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, user_id TEXT NOT NULL, kind TEXT NOT NULL,
    device_name TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, last_seen REAL NOT NULL,
    last_ip TEXT NOT NULL DEFAULT '', where_from TEXT NOT NULL DEFAULT '', expires_at REAL NOT NULL,
    remember INTEGER NOT NULL DEFAULT 0, cert TEXT
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dashboards (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, body TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS dashboards_user ON dashboards(user_id);
CREATE TABLE IF NOT EXISTS phone_layouts (
    user_id TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS person_locations (
    person_id TEXT PRIMARY KEY, lat REAL NOT NULL, lon REAL NOT NULL, accuracy_m REAL, at REAL NOT NULL,
    speed_mps REAL, source TEXT NOT NULL DEFAULT 'app', received_at REAL NOT NULL
);
"""
# Location sharing, per account (see locations.py): "off" | "choice" | "required", whether it's on
# (the person's own switch, for "choice"), who may see a "required" account (JSON person ids and wall
# tablet account ids; NULL = the whole household) and the admin who made it required. A wall tablet's
# account: whether it can see where people are (on by default).
USER_COLUMNS = {"loc_mode": "TEXT NOT NULL DEFAULT 'choice'", "loc_enabled": "INTEGER NOT NULL DEFAULT 0",
                "loc_visible": "TEXT", "loc_required_by": "TEXT", "tablet_people": "INTEGER NOT NULL DEFAULT 1"}
# How a person looks on maps (profiles.py): their colour ("#RRGGBB") and their photo's version (a hash).
PEOPLE_COLUMNS = {"color": "TEXT", "photo": "TEXT"}


class Store:
    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(data_dir / "newman.db", check_same_thread=False)
        # Called with each new activity entry (the API pushes them to open dashboards).
        self.event_listeners: list[Any] = []
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        # Camera activity is now one entry per Frigate *alert*; per-object "detection" entries
        # from earlier builds pointed at clips Frigate doesn't keep.
        self._db.execute("DELETE FROM events WHERE kind = 'detection'")
        # The Reolink connection now covers cameras and NVRs, not just the doorbell.
        self._db.execute("UPDATE integrations SET name = 'Reolink' WHERE type = 'reolink' AND name = 'Reolink doorbell'")
        # Sessions can pin a dashboard (a wall tablet showing a particular one).
        if "dashboard_id" not in {r["name"] for r in self._db.execute("PRAGMA table_info(sessions)")}:
            self._db.execute("ALTER TABLE sessions ADD COLUMN dashboard_id TEXT")
        have = {r["name"] for r in self._db.execute("PRAGMA table_info(users)")}
        for col, decl in USER_COLUMNS.items():
            if col not in have:
                self._db.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
        have = {r["name"] for r in self._db.execute("PRAGMA table_info(people)")}
        for col, decl in PEOPLE_COLUMNS.items():
            if col not in have:
                self._db.execute(f"ALTER TABLE people ADD COLUMN {col} {decl}")
        self._db.commit()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        cur = self._db.execute(sql, args)
        self._db.commit()
        return cur

    # -- accounts ----------------------------------------------------------------

    def users(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._db.execute("SELECT * FROM users ORDER BY created_at")]

    def user(self, user_id: str) -> dict[str, Any] | None:
        r = self._db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(r) if r else None

    def user_by_name(self, username: str) -> dict[str, Any] | None:
        r = self._db.execute("SELECT * FROM users WHERE username = ?", (username.strip().lower(),)).fetchone()
        return dict(r) if r else None

    def add_user(self, username: str, name: str, role: str, password_hash: str, person_id: str | None) -> dict[str, Any]:
        uid, now = new_id("u"), time.time()
        self._exec("INSERT INTO users (id, username, name, role, password_hash, person_id, created_at, "
                   "password_changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (uid, username.strip().lower(), name, role, password_hash, person_id, now, now))
        return self.user(uid)  # type: ignore[return-value]

    USER_FIELDS = {"username", "name", "role", "password_hash", "pin_hash", "person_id", "totp_secret", "totp_enabled",
                   "totp_last_step", "recovery", "disabled", "password_changed_at", *USER_COLUMNS}

    def update_user(self, user_id: str, **fields: Any) -> None:
        fields = {k: v for k, v in fields.items() if k in self.USER_FIELDS}
        if fields:
            self._exec(f"UPDATE users SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?",
                       (*fields.values(), user_id))

    def remove_user(self, user_id: str) -> None:
        self._exec("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        if user_id:
            self._exec("DELETE FROM phone_layouts WHERE user_id = ?", (user_id,))
        self._exec("DELETE FROM users WHERE id = ?", (user_id,))

    def add_session(self, row: dict[str, Any]) -> None:
        self._exec("INSERT INTO sessions (id, token_hash, user_id, kind, device_name, created_at, last_seen, last_ip, "
                   "where_from, expires_at, remember, cert) VALUES (:id, :token_hash, :user_id, :kind, :device_name, "
                   ":created_at, :last_seen, :last_ip, :where_from, :expires_at, :remember, :cert)", row)

    def session_by_token(self, token_hash: str) -> dict[str, Any] | None:
        r = self._db.execute("SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)).fetchone()
        return dict(r) if r else None

    def sessions(self, user_id: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM sessions", ()
        if user_id:
            sql, args = sql + " WHERE user_id = ?", (user_id,)
        return [dict(r) for r in self._db.execute(sql + " ORDER BY last_seen DESC", args)]

    def touch_session(self, sid: str, ip: str, where: str, expires_at: float) -> None:
        self._exec("UPDATE sessions SET last_seen = ?, last_ip = ?, where_from = ?, expires_at = ? WHERE id = ?",
                   (time.time(), ip, where, expires_at, sid))

    def remove_session(self, sid: str) -> None:
        self._exec("DELETE FROM sessions WHERE id = ?", (sid,))

    def remove_sessions(self, user_id: str, keep: str | None = None) -> None:
        self._exec("DELETE FROM sessions WHERE user_id = ? AND id != ?", (user_id, keep or ""))

    def purge_sessions(self) -> None:
        self._exec("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))

    def set_session_dashboard(self, sid: str, dashboard_id: str | None) -> None:
        self._exec("UPDATE sessions SET dashboard_id = ? WHERE id = ?", (dashboard_id, sid))

    # -- dashboards --------------------------------------------------------------

    def _dash(self, r: sqlite3.Row) -> dict[str, Any]:
        return {"id": r["id"], "userId": r["user_id"], "isDefault": bool(r["is_default"]),
                "updatedAt": r["updated_at"], **with_home(json.loads(r["body"]))}

    def dashboards(self, user_id: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM dashboards", ()
        if user_id:
            sql, args = sql + " WHERE user_id = ?", (user_id,)
        return [self._dash(r) for r in self._db.execute(sql + " ORDER BY created_at", args)]

    def dashboard_times(self) -> tuple[tuple[str, float], ...]:
        """(id, updated at) of every dashboard - changes whenever one is added, saved or removed."""
        return tuple((r["id"], r["updated_at"]) for r in self._db.execute("SELECT id, updated_at FROM dashboards ORDER BY id"))

    def dashboard(self, dashboard_id: str) -> dict[str, Any] | None:
        r = self._db.execute("SELECT * FROM dashboards WHERE id = ?", (dashboard_id,)).fetchone()
        return self._dash(r) if r else None

    def save_dashboard(self, user_id: str, body: dict[str, Any], dashboard_id: str | None = None) -> dict[str, Any]:
        now = time.time()
        if dashboard_id and self.dashboard(dashboard_id):
            self._exec("UPDATE dashboards SET name = ?, body = ?, updated_at = ? WHERE id = ?",
                       (body["name"], json.dumps(body), now, dashboard_id))
            return self.dashboard(dashboard_id)  # type: ignore[return-value]
        did = new_id("d")
        first = not self.dashboards(user_id)
        self._exec("INSERT INTO dashboards (id, user_id, name, body, is_default, created_at, updated_at) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?)", (did, user_id, body["name"], json.dumps(body), int(first), now, now))
        return self.dashboard(did)  # type: ignore[return-value]

    def set_default_dashboard(self, user_id: str, dashboard_id: str) -> None:
        self._exec("UPDATE dashboards SET is_default = (id = ?) WHERE user_id = ?", (dashboard_id, user_id))

    def remove_dashboard(self, dashboard_id: str) -> None:
        d = self.dashboard(dashboard_id)
        self._exec("DELETE FROM dashboards WHERE id = ?", (dashboard_id,))
        self._exec("UPDATE sessions SET dashboard_id = NULL WHERE dashboard_id = ?", (dashboard_id,))
        if d and d["isDefault"]:
            rest = self.dashboards(d["userId"])
            if rest:
                self.set_default_dashboard(d["userId"], rest[0]["id"])

    # -- phone layouts: the household default (user id "") and people's own ----------------------

    PHONE_DEFAULT = ""

    def phone_layout(self, user_id: str) -> dict[str, Any] | None:
        """{"layout", "updatedAt"} or None."""
        r = self._db.execute("SELECT * FROM phone_layouts WHERE user_id = ?", (user_id,)).fetchone()
        return {"layout": json.loads(r["body"]), "updatedAt": r["updated_at"]} if r else None

    def phone_layout_times(self) -> dict[str, float]:
        """{user id: updated at} for everyone with a layout of their own."""
        return {r["user_id"]: r["updated_at"] for r in self._db.execute("SELECT user_id, updated_at FROM phone_layouts")
                if r["user_id"] != self.PHONE_DEFAULT}

    def save_phone_layout(self, user_id: str, layout: dict[str, Any]) -> None:
        self._exec("INSERT INTO phone_layouts (user_id, body, updated_at) VALUES (?, ?, ?) ON CONFLICT(user_id) "
                   "DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at",
                   (user_id, json.dumps(layout), time.time()))

    def remove_phone_layout(self, user_id: str) -> bool:
        return self._exec("DELETE FROM phone_layouts WHERE user_id = ?", (user_id,)).rowcount > 0

    def settings(self) -> dict[str, Any]:
        return {r["key"]: json.loads(r["value"]) for r in self._db.execute("SELECT * FROM settings")}

    def set_setting(self, key: str, value: Any) -> None:
        self._exec("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, json.dumps(value)))

    # -- people ------------------------------------------------------------------

    def people(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._db.execute("SELECT * FROM people ORDER BY created_at")]

    def person(self, person_id: str) -> dict[str, Any] | None:
        r = self._db.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
        return dict(r) if r else None

    def add_person(self, name: str) -> dict[str, Any]:
        pid = f"p_{slug(name)}"
        if self.person(pid):
            pid = new_id("p")
        self._exec("INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)", (pid, name, time.time()))
        return self.person(pid)  # type: ignore[return-value]

    def rename_person(self, person_id: str, name: str) -> None:
        self._exec("UPDATE people SET name = ? WHERE id = ?", (name, person_id))

    def set_person(self, person_id: str, **fields: Any) -> None:
        """Their colour and / or photo version (PEOPLE_COLUMNS)."""
        cols = [c for c in fields if c in PEOPLE_COLUMNS]
        if cols:
            self._exec(f"UPDATE people SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                       (*(fields[c] for c in cols), person_id))

    def remove_person(self, person_id: str) -> None:
        self._exec("DELETE FROM people WHERE id = ?", (person_id,))
        self._exec("UPDATE phones SET person_id = NULL WHERE person_id = ?", (person_id,))
        self.remove_location(person_id)

    # -- where people are (the latest fix only - no history is kept) ---------------------

    def location(self, person_id: str) -> dict[str, Any] | None:
        r = self._db.execute("SELECT * FROM person_locations WHERE person_id = ?", (person_id,)).fetchone()
        return dict(r) if r else None

    def save_location(self, person_id: str, lat: float, lon: float, accuracy_m: float | None, at: float,
                      speed_mps: float | None, source: str) -> None:
        self._exec("""INSERT INTO person_locations (person_id, lat, lon, accuracy_m, at, speed_mps, source, received_at)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(person_id) DO UPDATE SET lat=excluded.lat,
                      lon=excluded.lon, accuracy_m=excluded.accuracy_m, at=excluded.at, speed_mps=excluded.speed_mps,
                      source=excluded.source, received_at=excluded.received_at""",
                   (person_id, lat, lon, accuracy_m, at, speed_mps, source, time.time()))

    def remove_location(self, person_id: str) -> None:
        self._exec("DELETE FROM person_locations WHERE person_id = ?", (person_id,))

    # -- phones ------------------------------------------------------------------

    def _phone(self, r: sqlite3.Row) -> Phone:
        return Phone(device_id=r["device_id"], token=r["token"], platform=r["platform"], person_id=r["person_id"],
                     apns_env=r["apns_env"], friendly_name=r["friendly_name"], device_model=r["device_model"],
                     registered_at=r["registered_at"], last_seen=r["last_seen"], failures=r["failures"])

    def register_phone(self, p: Phone) -> Phone:
        # Keyed by the app's stable deviceId, so a rotated push token replaces
        # the old one. An explicit person in the request wins; otherwise the
        # phone keeps whoever it was already assigned to.
        now = time.time()
        self._exec(
            """
            INSERT INTO phones (device_id, token, platform, person_id, apns_env, friendly_name, device_model,
                                registered_at, last_seen, failures)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(device_id) DO UPDATE SET
                token=excluded.token, platform=excluded.platform, apns_env=excluded.apns_env,
                person_id=COALESCE(excluded.person_id, phones.person_id),
                friendly_name=CASE WHEN excluded.friendly_name != '' THEN excluded.friendly_name ELSE phones.friendly_name END,
                device_model=excluded.device_model, last_seen=excluded.last_seen, failures=0
            """,
            (p.device_id, p.token, p.platform, p.person_id, p.apns_env, p.friendly_name, p.device_model, now, now),
        )
        self._exec("DELETE FROM phones WHERE token = ? AND device_id != ?", (p.token, p.device_id))
        return self.phone(p.device_id)  # type: ignore[return-value]

    def phones(self, person_id: str | None = None) -> list[Phone]:
        if person_id:
            rows = self._db.execute("SELECT * FROM phones WHERE person_id = ? ORDER BY registered_at", (person_id,))
        else:
            rows = self._db.execute("SELECT * FROM phones ORDER BY registered_at")
        return [self._phone(r) for r in rows]

    def phone(self, device_id: str) -> Phone | None:
        r = self._db.execute("SELECT * FROM phones WHERE device_id = ?", (device_id,)).fetchone()
        return self._phone(r) if r else None

    def update_phone(self, device_id: str, *, person_id: str | None | bool = False, name: str | None = None) -> None:
        if person_id is not False:
            self._exec("UPDATE phones SET person_id = ? WHERE device_id = ?", (person_id, device_id))
        if name is not None:
            self._exec("UPDATE phones SET friendly_name = ? WHERE device_id = ?", (name, device_id))

    def remove_phone(self, device_id: str) -> bool:
        return self._exec("DELETE FROM phones WHERE device_id = ?", (device_id,)).rowcount > 0

    def record_push_failure(self, device_id: str, dead_token: bool) -> None:
        # A token the push service reports as dead gets one retry (tokens can
        # briefly look invalid around an app update) before it's removed.
        if not dead_token:
            return
        r = self._db.execute("SELECT failures FROM phones WHERE device_id = ?", (device_id,)).fetchone()
        if r and r["failures"] >= 1:
            self.remove_phone(device_id)
        else:
            self._exec("UPDATE phones SET failures = failures + 1 WHERE device_id = ?", (device_id,))

    def record_push_success(self, device_id: str) -> None:
        self._exec("UPDATE phones SET failures = 0 WHERE device_id = ? AND failures != 0", (device_id,))

    # -- automations ---------------------------------------------------------------

    def automations(self) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT body FROM automations ORDER BY created_at")
        return [json.loads(r["body"]) for r in rows]

    def save_automation(self, auto: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        self._exec(
            """INSERT INTO automations (id, body, created_at, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET body=excluded.body, updated_at=excluded.updated_at""",
            (auto["id"], json.dumps(auto), now, now),
        )
        return auto

    def remove_automation(self, auto_id: str) -> bool:
        return self._exec("DELETE FROM automations WHERE id = ?", (auto_id,)).rowcount > 0

    # -- runs ----------------------------------------------------------------------

    def save_run(self, run: dict[str, Any]) -> None:
        self._exec(
            """INSERT INTO runs (id, automation_id, started_at, body) VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET body=excluded.body""",
            (run["id"], run["automationId"], run["startedAt"], json.dumps(run)),
        )
        self._exec("DELETE FROM runs WHERE started_at < ?", (time.time() - 90 * 86400,))

    def run(self, run_id: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT body FROM runs WHERE id = ?", (run_id,)).fetchone()
        return json.loads(row["body"]) if row else None

    def runs(self, automation_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if automation_id:
            rows = self._db.execute("SELECT body FROM runs WHERE automation_id = ? ORDER BY started_at DESC LIMIT ?",
                                    (automation_id, limit))
        else:
            rows = self._db.execute("SELECT body FROM runs ORDER BY started_at DESC LIMIT ?", (limit,))
        return [json.loads(r["body"]) for r in rows]

    # -- integrations -------------------------------------------------------------

    def integrations(self) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT * FROM integrations ORDER BY created_at")
        return [{"id": r["id"], "type": r["type"], "name": r["name"], "enabled": bool(r["enabled"]),
                 "config": json.loads(r["config"])} for r in rows]

    def save_integration(self, rec: dict[str, Any]) -> None:
        self._exec(
            """INSERT INTO integrations (id, type, name, enabled, config, created_at) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, enabled=excluded.enabled, config=excluded.config""",
            (rec["id"], rec["type"], rec["name"], int(rec.get("enabled", True)), json.dumps(rec["config"]), time.time()),
        )

    def change_integration_type(self, integration_id: str, type_: str, name: str) -> None:
        """A connection type folded into another (save_integration never changes a record's type)."""
        self._exec("UPDATE integrations SET type = ?, name = ? WHERE id = ?", (type_, name, integration_id))

    def remove_integration(self, integration_id: str) -> bool:
        return self._exec("DELETE FROM integrations WHERE id = ?", (integration_id,)).rowcount > 0

    # -- timed actions waiting to be put back ------------------------------------------

    def save_hold(self, key: str, hold: dict[str, Any]) -> None:
        self._exec("INSERT INTO holds (key, body) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET body=excluded.body",
                   (key, json.dumps(hold)))

    def remove_hold(self, key: str) -> None:
        self._exec("DELETE FROM holds WHERE key = ?", (key,))

    def holds(self) -> dict[str, dict[str, Any]]:
        return {r["key"]: json.loads(r["body"]) for r in self._db.execute("SELECT * FROM holds")}

    # -- entity renames -------------------------------------------------------------

    def entity_names(self) -> dict[str, str]:
        return {r["entity_id"]: r["name"] for r in self._db.execute("SELECT * FROM entity_names")}

    def rename_entity(self, entity_id: str, name: str) -> None:
        self._exec("INSERT INTO entity_names (entity_id, name) VALUES (?, ?) "
                   "ON CONFLICT(entity_id) DO UPDATE SET name=excluded.name", (entity_id, name))

    # -- per-device settings kept by Newman (video.py: streams, All Cameras, "Video from") ----------

    def entity_settings(self) -> dict[str, dict[str, Any]]:
        return {r["entity_id"]: json.loads(r["body"]) for r in self._db.execute("SELECT * FROM entity_settings")}

    def set_entity_settings(self, entity_id: str, body: dict[str, Any]) -> None:
        """Replace one device's settings; nothing set removes its row."""
        if not body:
            self._exec("DELETE FROM entity_settings WHERE entity_id = ?", (entity_id,))
            return
        self._exec("INSERT INTO entity_settings (entity_id, body) VALUES (?, ?) "
                   "ON CONFLICT(entity_id) DO UPDATE SET body=excluded.body", (entity_id, json.dumps(body)))

    def forget_entity(self, entity_id: str) -> None:
        """A removed device: drop the name given to it and its settings (it comes back fresh, if ever)."""
        self._exec("DELETE FROM entity_names WHERE entity_id = ?", (entity_id,))
        self._exec("DELETE FROM entity_settings WHERE entity_id = ?", (entity_id,))

    # -- activity log ------------------------------------------------------------------

    def log_event(self, kind: str, title: str, body: str = "", entity_id: str | None = None,
                  data: dict[str, Any] | None = None) -> None:
        self._exec("INSERT INTO events (ts, kind, title, body, entity_id, data) VALUES (?, ?, ?, ?, ?, ?)",
                   (time.time(), kind, title, body, entity_id, json.dumps(data or {})))
        self._exec("DELETE FROM events WHERE ts < ?", (time.time() - 90 * 86400,))
        if self.event_listeners:
            entry = self.events(1)[0]
            for listener in list(self.event_listeners):
                listener(entry)

    def entity_use_counts(self, since: float) -> dict[str, int]:
        """How often each device was commanded or spoken about (screens, voice, automations) since `since`."""
        return {r[0]: r[1] for r in self._db.execute(
            "SELECT entity_id, COUNT(*) FROM events WHERE entity_id IS NOT NULL AND kind IN ('command', 'voice') "
            "AND ts >= ? GROUP BY entity_id", (since,))}

    def events(self, limit: int = 100, before: float | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM events WHERE ts < ?", [before or time.time() + 1]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(min(limit, 500))
        return [{"id": r["id"], "ts": r["ts"], "kind": r["kind"], "title": r["title"], "body": r["body"],
                 "entityId": r["entity_id"], "data": json.loads(r["data"])}
                for r in self._db.execute(sql, args)]
