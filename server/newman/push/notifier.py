"""Deliver a Notification to people: every phone they own gets a copy with
its own signed snapshot link and action token. Test phones (platform "test")
get nothing pushed - their copy lands in an in-memory outbox instead, so
scenarios can be exercised end to end without the mobile apps."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from .. import tokens
from ..config import config
from ..store import Phone, Store
from .apns import ApnsSender, PushError
from .fcm import FcmSender
from .location_tokens import LocationPushTokens

log = logging.getLogger(__name__)


def category_for(buttons: list[dict[str, Any]]) -> str | None:
    """Stable iOS category id for a set of buttons. The app registers every set it can foresee
    (GET /api/push/categories) on launch, in the foreground and on background wakes; the
    Notification Service Extension never touches categories."""
    if not buttons:
        return None
    key = json.dumps([[b["id"], b["label"], b.get("icon"), b.get("sensitive")] for b in buttons], separators=(",", ":"))
    return "c_" + hashlib.sha1(key.encode()).hexdigest()[:12]


@dataclass
class Notification:
    kind: str                      # automation | followup | test
    title: str
    body: str
    buttons: list[dict[str, Any]] = field(default_factory=list)  # [{id,label,icon,sensitive}]
    category: str | None = None    # iOS category id (category_for(buttons) when not given)
    thread: str | None = None
    sound: str = "default"
    time_sensitive: bool = False
    collapse_id: str | None = None
    snapshot_id: str | None = None
    action_subject: str | None = None  # run id the buttons act on
    data: dict[str, str] = field(default_factory=dict)
    # The alert it's about (the automation run's id): the app opens it from the Alerts list when tapped,
    # however old the notification - never through a link that expires.
    alert_id: str | None = None
    # What it is, for quiet hours (quiet.py): "alarm" (a leak, smoke, the alarm going off, a wall-tablet
    # alarm), "people" (someone on a camera); "doorbell" and "urgent" come from the sound and time_sensitive.
    topic: str = ""

    def topics(self) -> set[str]:
        out = {self.topic} if self.topic else set()
        if self.sound.startswith("doorbell"):
            out.add("doorbell")
        if self.time_sensitive:
            out.add("urgent")
        return out

    def __post_init__(self) -> None:
        if self.buttons and not self.category:
            self.category = category_for(self.buttons)


class Notifier:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._apns = ApnsSender()
        self._fcm = FcmSender()
        self.location_tokens = LocationPushTokens(store)  # iPhones' Location Push Service tokens
        self.outbox: deque[dict[str, Any]] = deque(maxlen=300)
        # (person id, topics) -> deliver silently now (quiet hours, quiet.py); set by the API.
        self.quiet: Any = None

    def snapshot_url(self, snapshot_id: str, device_id: str) -> str:
        exp, tok = tokens.sign("snapshot", snapshot_id, device_id, tokens.SNAPSHOT_TTL_S)
        q = urlencode({"device": device_id, "exp": exp, "token": tok})
        return f"{config.token_base_url}/api/public/snapshots/{snapshot_id}?{q}"

    def _fields(self, n: Notification, phone: Phone) -> dict[str, str]:
        out: dict[str, str] = {"kind": n.kind, "title": n.title, "body": n.body,
                               "time_sensitive": "true" if n.time_sensitive else "false", **n.data}
        if n.alert_id:
            out["alertId"] = n.alert_id
        if n.thread:
            out["thread"] = n.thread
        if n.buttons:
            out["buttons"] = json.dumps(n.buttons, separators=(",", ":"))
        if n.snapshot_id:
            out["snapshot_id"] = n.snapshot_id
            out["snapshot_url"] = self.snapshot_url(n.snapshot_id, phone.device_id)
        if clip := n.data.get("clip"):
            # A camera detection's video, played in the app itself (no Optiq needed), via the hub.
            exp, tok = tokens.sign("clip", clip, phone.device_id, tokens.SNAPSHOT_TTL_S)
            iid, _, ev = clip.partition(":")
            q = urlencode({"device": phone.device_id, "exp": exp, "token": tok})
            out["clip_url"] = f"{config.token_base_url}/api/public/clips/{iid}/{ev}?{q}"
        if n.action_subject:
            exp, tok = tokens.sign("action", n.action_subject, phone.device_id, tokens.ACTION_TTL_S)
            out.update(action_subject=n.action_subject, action_exp=str(exp), action_token=tok,
                       action_url=f"{config.token_base_url}/api/public/actions")
        return out

    async def _send_one(self, n: Notification, phone: Phone) -> None:
        fields = self._fields(n, phone)
        quiet = bool(self.quiet and self.quiet(phone.person_id, n.topics()))
        if quiet:
            fields["quiet"] = "1"
        try:
            if phone.platform == "test":
                self.outbox.appendleft({"ts": time.time(), "deviceId": phone.device_id,
                                        "personId": phone.person_id, "category": n.category, **fields})
            elif phone.platform == "ios":
                aps: dict[str, Any] = {
                    "alert": {"title": n.title, "body": n.body},
                    "sound": n.sound,
                    "interruption-level": "time-sensitive" if n.time_sensitive else "active",
                }
                if quiet:  # quiet hours: delivered without a sound or lighting up the screen
                    aps.pop("sound", None)
                    aps["interruption-level"] = "passive"
                if n.category:
                    aps["category"] = n.category
                if n.thread:
                    aps["thread-id"] = n.thread
                if n.snapshot_id:
                    # Runs the Notification Service Extension, which attaches the snapshot. Not for
                    # buttons alone: on iOS 26 a push whose extension registered its category never
                    # showed at all (2026-10-10), so a buttons-only push goes straight to the phone.
                    aps["mutable-content"] = 1
                ios_fields = {k: v for k, v in fields.items() if k not in ("title", "body")}
                if n.snapshot_id:
                    # The extension only attaches the snapshot; buttons come from the categories the app
                    # registers itself. (An extension registering a push's own buttons jammed every later
                    # Newman notification on iOS 26 - 2026-10-10 - so it never gets them.)
                    ios_fields.pop("buttons", None)
                await self._apns.send(phone.token, {"aps": aps, **ios_fields}, env=phone.apns_env,
                                      collapse_id=n.collapse_id)
            else:
                if n.category:
                    fields["category"] = n.category
                if n.collapse_id:
                    fields["collapse_id"] = n.collapse_id
                fields["sound"] = n.sound  # the app picks its channel by it (e.g. the doorbell's own)
                await self._fcm.send(phone.token, fields, collapse_key=n.collapse_id)
            self._store.record_push_success(phone.device_id)
            log.info("Push %s -> %s", n.kind, phone.display_name)
        except PushError as e:
            log.warning("Push %s -> %s failed: %s", n.kind, phone.display_name, e)
            self._store.record_push_failure(phone.device_id, e.dead_token)
        except Exception:
            log.exception("Push %s -> %s crashed", n.kind, phone.display_name)

    async def send(self, n: Notification, *, people: list[str] | None = None,
                   device_ids: list[str] | None = None) -> int:
        """Send to every phone of `people` (and/or explicit `device_ids`). Returns phones targeted."""
        targets: dict[str, Phone] = {}
        for pid in people or []:
            for ph in self._store.phones(pid):
                targets[ph.device_id] = ph
        for did in device_ids or []:
            if ph := self._store.phone(did):
                targets[did] = ph
        if not targets:
            log.info("Push %s %r: no phones for %s", n.kind, n.title, people or device_ids)
            return 0
        await asyncio.gather(*(self._send_one(n, ph) for ph in targets.values()))
        return len(targets)

    async def _locate_one(self, phone: Phone) -> None:
        try:
            if phone.platform == "test":
                self.outbox.appendleft({"ts": time.time(), "deviceId": phone.device_id, "personId": phone.person_id,
                                        "kind": "locate", "type": "locate"})
            elif phone.platform == "ios":
                if not self._apns.enabled:
                    return
                if await self._location_push(phone):
                    return
                # Silent: wakes the app in the background, shows nothing.
                await self._apns.send(phone.token, {"aps": {"content-available": 1}, "newman": {"type": "locate"}},
                                      env=phone.apns_env, priority=5, push_type="background", expiration_s=120,
                                      collapse_id="newman-locate")
            else:
                if not self._fcm.enabled:
                    return  # no FCM set up: the Android app catches up at its next report
                await self._fcm.send(phone.token, {"type": "locate"}, collapse_key="newman-locate", ttl_s=120)
            self._store.record_push_success(phone.device_id)
        except PushError as e:
            log.warning("Locate push -> %s failed: %s", phone.display_name, e)
            self._store.record_push_failure(phone.device_id, e.dead_token)
        except Exception:
            log.exception("Locate push -> %s crashed", phone.display_name)

    async def _location_push(self, phone: Phone) -> bool:
        """An iPhone with a location push token: wake its LocationPushExtension (works even when the
        app was swiped away). True when it went out; False means use the silent push instead."""
        found = self.location_tokens.get(phone.device_id)
        if not found:
            return False
        token, env = found
        try:
            await self._apns.send(token, {}, env=env, priority=10, push_type="location",
                                  topic=f"{config.apns_bundle_id}.location-query", expiration_s=60)
            return True
        except PushError as e:
            log.warning("Location push -> %s failed: %s", phone.display_name, e)
            if e.dead_token:
                self.location_tokens.forget(phone.device_id)  # the app sends a new one when it has one
            return False

    async def locate(self, person_id: str) -> int:
        """Ask a person's phones for one location fix now (someone tapped "refresh" on them): an APNs
        location push to iPhones with a location push token, else the silent push; Android a data message."""
        phones = self._store.phones(person_id)
        await asyncio.gather(*(self._locate_one(ph) for ph in phones))
        return len(phones)

    async def close(self) -> None:
        await asyncio.gather(self._apns.close(), self._fcm.close())
