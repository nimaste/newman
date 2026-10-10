"""APNs sender: HTTP/2 + token (.p8 / ES256 JWT) auth, one pooled connection."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
import jwt

from ..config import config

log = logging.getLogger(__name__)

HOSTS = {"production": "https://api.push.apple.com", "sandbox": "https://api.sandbox.push.apple.com"}
DEAD_REASONS = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}


class PushError(Exception):
    def __init__(self, message: str, dead_token: bool = False) -> None:
        super().__init__(message)
        self.dead_token = dead_token


class ApnsSender:
    def __init__(self) -> None:
        self.enabled = bool(config.apns_key_path and config.apns_key_id and config.apns_team_id)
        if not self.enabled:
            log.warning("APNs disabled - set APNS_KEY_PATH, APNS_KEY_ID and APNS_TEAM_ID")
            return
        self._key = Path(config.apns_key_path).read_text()
        self._jwt = ""
        self._jwt_at = 0.0
        self._client = httpx.AsyncClient(http2=True, timeout=15)

    def _token(self) -> str:
        # Apple rejects tokens older than 60 min and throttles ones refreshed
        # more often than every 20 - refresh at 50.
        if not self._jwt or time.time() - self._jwt_at > 50 * 60:
            self._jwt_at = time.time()
            self._jwt = jwt.encode(
                {"iss": config.apns_team_id, "iat": int(self._jwt_at)},
                self._key, algorithm="ES256", headers={"kid": config.apns_key_id},
            )
        return self._jwt

    async def send(self, token: str, payload: dict[str, Any], *, env: str, collapse_id: str | None = None,
                   priority: int = 10, expiration_s: int = 86400, push_type: str = "alert",
                   topic: str | None = None) -> None:
        if not self.enabled:
            raise PushError("APNs not configured")
        headers = {
            "authorization": f"bearer {self._token()}",
            "apns-topic": topic or config.apns_bundle_id,
            "apns-push-type": push_type,
            "apns-priority": str(priority),
            "apns-expiration": str(int(time.time()) + expiration_s),
        }
        if collapse_id:
            headers["apns-collapse-id"] = collapse_id[:64]
        url = f"{HOSTS.get(env, HOSTS['production'])}/3/device/{token}"
        resp = await self._client.post(url, content=json.dumps(payload, separators=(",", ":")), headers=headers)
        if resp.status_code == 200:
            return
        try:
            reason = resp.json().get("reason", "")
        except ValueError:
            reason = resp.text
        raise PushError(f"APNs {resp.status_code} {reason}",
                        dead_token=resp.status_code == 410 or reason in DEAD_REASONS)

    async def close(self) -> None:
        if self.enabled:
            await self._client.aclose()
