"""FCM HTTP v1 sender. Talks to the REST API directly with a service-account
OAuth token instead of pulling in firebase-admin (and its gRPC stack) - keeps
the image and memory footprint small."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
import jwt

from ..config import config
from .apns import PushError

log = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


class FcmSender:
    def __init__(self) -> None:
        self.enabled = bool(config.fcm_service_account_path)
        if not self.enabled:
            log.warning("FCM disabled - set FCM_SERVICE_ACCOUNT_PATH")
            return
        with open(config.fcm_service_account_path) as fh:
            self._sa = json.load(fh)
        self._project = self._sa["project_id"]
        self._access_token = ""
        self._access_exp = 0.0
        self._client = httpx.AsyncClient(timeout=15)

    async def _auth(self) -> str:
        if self._access_token and time.time() < self._access_exp - 300:
            return self._access_token
        now = int(time.time())
        assertion = jwt.encode(
            {"iss": self._sa["client_email"], "scope": SCOPE, "aud": self._sa["token_uri"],
             "iat": now, "exp": now + 3600},
            self._sa["private_key"], algorithm="RS256",
            headers={"kid": self._sa.get("private_key_id", "")},
        )
        resp = await self._client.post(self._sa["token_uri"], data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion,
        })
        resp.raise_for_status()
        body = resp.json()
        self._access_token = body["access_token"]
        self._access_exp = time.time() + int(body.get("expires_in", 3600))
        return self._access_token

    async def send(self, token: str, data: dict[str, str], *, collapse_key: str | None = None,
                   ttl_s: int = 86400) -> None:
        if not self.enabled:
            raise PushError("FCM not configured")
        android: dict[str, Any] = {"priority": "high", "ttl": f"{ttl_s}s"}
        if collapse_key:
            android["collapse_key"] = collapse_key
        # Data-only on purpose: the app builds the notification itself so it can
        # pick the channel (doorbell chime vs lock) and attach the snapshot.
        message = {"message": {"token": token, "data": data, "android": android}}
        url = f"https://fcm.googleapis.com/v1/projects/{self._project}/messages:send"
        resp = await self._client.post(url, json=message, headers={"Authorization": f"Bearer {await self._auth()}"})
        if resp.status_code == 200:
            return
        try:
            err = resp.json().get("error", {})
        except ValueError:
            err = {}
        codes = {d.get("errorCode") for d in err.get("details", []) if isinstance(d, dict)}
        dead = resp.status_code == 404 or "UNREGISTERED" in codes or (
            resp.status_code == 400 and "INVALID_ARGUMENT" in codes and "token" in err.get("message", "").lower())
        raise PushError(f"FCM {resp.status_code} {err.get('status', '')} {err.get('message', resp.text)[:200]}",
                        dead_token=dead)

    async def close(self) -> None:
        if self.enabled:
            await self._client.aclose()
