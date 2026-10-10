"""Webhooks: web addresses that start automations, and addresses Newman sends to.

Incoming: each webhook gets a hard-to-guess address,
    POST /api/public/webhooks/<device id>/<secret>
Anything posted there fires the webhook device's "received" event (its body, if any, comes along as
the event's data and as the device's `lastMessage` reading), so an iPhone Shortcut, IFTTT, a script
or another server can start an automation. The secret is made for you and kept as a masked setting.

Outgoing: give a webhook a "Send to" address and its Send command POSTs there - as JSON
{"text": ..., "from": "Newman", "device": ...} or as plain text - with the command's optional text.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import parse_qsl, urlparse

import aiohttp

from ..store import slug
from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

MIN_GAP_S = 1.0          # calls to one webhook closer together than this are answered but not repeated
MAX_TEXT = 500           # longest body kept as the device's last message


def hooks_from_config(config: dict[str, Any]) -> list[dict[str, Any]]:
    out, seen = [], set()
    for h in config.get("hooks") or []:
        name = str((h or {}).get("name") or "").strip()
        if name and slug(name) not in seen:
            seen.add(slug(name))
            out.append({**h, "name": name})
    return out


def parse_body(body: bytes, content_type: str) -> tuple[str, dict[str, Any]]:
    """(text, data): the body as text, plus its simple fields when it's JSON or a form."""
    text = body.decode("utf-8", errors="replace").strip()
    data: dict[str, Any] = {}
    if "json" in (content_type or "") or text[:1] in ("{", "["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                data = {str(k): v for k, v in parsed.items() if isinstance(v, (str, int, float, bool))}
                text = str(parsed.get("text") or parsed.get("message") or parsed.get("value") or text)
        except ValueError:
            pass
    elif "form" in (content_type or ""):
        data = dict(parse_qsl(text))
        text = data.get("text") or data.get("message") or text
    return text[:MAX_TEXT], data


class WebhooksIntegration(Integration):
    type = "webhooks"
    name = "Webhooks"
    category = "other"
    provides = ("webhook",)  # kinds of device it can bring in (shown on its catalog card)
    description = ("Web addresses that start automations when something calls them (an iPhone Shortcut, IFTTT, a "
                   "script…), and addresses Newman can send a message to from an automation.")
    fields = [
        Field("hooks", "Webhooks", "devices",
              help="Each one gets its own secret address to call. Add a “Send to” address to send from automations too.",
              item_fields=(
                  Field("name", "Name", required=True, placeholder="Garden gate"),
                  Field("secret", "Secret", "password",
                        help="Part of its address. Leave empty and Newman makes a long random one."),
                  Field("url", "Send to (optional)", "password", placeholder="https://example.com/hook",
                        help="Where its Send command posts. Kept hidden, since these often contain a key."),
                  Field("format", "Send as", "select", default="json",
                        options=({"value": "json", "label": "JSON"}, {"value": "text", "label": "Plain text"})),
              )),
    ]

    @classmethod
    def validate(cls, config: dict[str, Any]) -> list[str]:
        errors = super().validate(config)
        for h in hooks_from_config(config):
            url = str(h.get("url") or "").strip()
            if url and urlparse(url).scheme not in ("http", "https"):
                errors.append(f"{h['name']}: the Send to address must start with http:// or https://")
        return errors

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._hooks: dict[str, dict[str, Any]] = {}   # entity id -> its settings
        self._last_call: dict[str, float] = {}

    def address(self, entity_id: str) -> str:
        return f"/api/public/webhooks/{entity_id}/{self._hooks[entity_id]['secret']}"

    async def run(self) -> None:
        hooks = hooks_from_config(self.config)
        if any(not h.get("secret") for h in hooks):
            for h in hooks:
                h.setdefault("secret", "")
                if not h["secret"]:
                    h["secret"] = secrets.token_urlsafe(24)
            self.config = {**self.config, "hooks": hooks}
            if self.save_config:
                self.save_config(self.config)
        for h in hooks:
            eid = self.entity_id(slug(h["name"]))
            self._hooks[eid] = h
            self.hub.upsert(eid, kind="webhook", name=h["name"], integration=self.id)
            await self.hub.update(eid, state="idle", available=True)
        if not hooks:
            self.set_status("pairing", "Add a webhook in the settings")
        else:
            # Names only: the status reaches every account; the addresses (they contain the secret)
            # are in admin_details(), which only admins get.
            self.set_status("connected", f"{len(hooks)} webhook{'s' if len(hooks) != 1 else ''} ready · "
                                         + " · ".join(h["name"] for h in self._hooks.values()))
        await asyncio.Event().wait()

    def admin_details(self) -> dict[str, Any]:
        return {"addresses": [{"name": h["name"], "entityId": eid, "path": self.address(eid)}
                              for eid, h in self._hooks.items()]}

    # -- incoming ----------------------------------------------------------------------------

    async def receive_webhook(self, entity_id: str, secret: str, body: bytes, content_type: str) -> bool:
        hook = self._hooks.get(entity_id)
        if not hook or not secrets.compare_digest(str(hook.get("secret") or "").encode(), str(secret).encode()):
            return False
        now = time.monotonic()
        if now - self._last_call.get(entity_id, -MIN_GAP_S) < MIN_GAP_S:
            return True  # a retry or a double tap: accepted, not repeated
        self._last_call[entity_id] = now
        text, data = parse_body(body or b"", content_type)
        await self.hub.update(entity_id, attrs={"lastMessage": text, "updated": int(time.time())})
        await self.hub.emit(entity_id, "received", {**data, "text": text})
        return True

    # -- outgoing ------------------------------------------------------------------------------

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        hook = self._hooks.get(entity_id)
        if not hook:
            raise CommandError("Unknown webhook")
        if command != "send":
            raise CommandError(f"A webhook can't {command.replace('_', ' ')}")
        url = str(hook.get("url") or "").strip()
        if not url:
            raise CommandError(f"{hook['name']} has no Send to address - add one in its settings")
        text = "" if value is None else str(value)
        if hook.get("format") == "text":
            kwargs: dict[str, Any] = {"data": text.encode(), "headers": {"Content-Type": "text/plain; charset=utf-8"}}
        else:
            kwargs = {"json": {"text": text, "from": "Newman", "device": self.hub.name(entity_id)}}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as http:
                async with http.post(url, **kwargs) as resp:
                    status = resp.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise CommandError(f"Couldn't reach {urlparse(url).hostname or 'the address'} ({e.__class__.__name__})") from e
        await self.hub.update(entity_id, attrs={"lastSendStatus": status, "updated": int(time.time())})
        if status >= 400:
            raise CommandError(f"{urlparse(url).hostname} answered {status}")
