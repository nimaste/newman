"""Apple Maps (MapKit JS) for the web and wall maps: short-lived tokens signed with a MapKit key.

The key (a .p8 from the Apple Developer account, made for a Maps ID with MapKit JS) stays on the hub;
browsers only ever get a signed token (ES256 JWT, 30 minutes), which MapKit JS asks for again as it
runs out. No key set up (or one that can't be read) -> the maps use OpenFreeMap through the hub instead.

The token carries no `origin` claim: the hub is reached at several addresses (its LAN IP:port, the
public name behind the proxy), and a token tied to one origin would break the others. Only signed-in
users get one.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import jwt

from .config import config

log = logging.getLogger(__name__)

DEFAULT_KEY_PATH = "/app/keys/mapkit.p8"
TOKEN_LIFE_S = 30 * 60
REUSE_UNTIL_S = 5 * 60  # a new token once the cached one has less than this left


class MapKitTokens:
    def __init__(self) -> None:
        self._token = ""
        self._exp = 0
        self._sig: tuple[str, str, str] | None = None  # the settings the cached token was made with
        self._warned: tuple[str, str, str] | None = None

    @staticmethod
    def settings() -> tuple[str, str, str]:
        """(key id, team id, key file). The team defaults to the APNs one; the file to the APNs key's when
        it's the same key (one Apple key can have both the push and the Maps service), else mapkit.p8."""
        key_id = config.mapkit_key_id
        path = config.mapkit_key_path
        if not path:
            same = key_id and key_id == config.apns_key_id and config.apns_key_path
            path = config.apns_key_path if same else DEFAULT_KEY_PATH
        return (key_id, config.mapkit_team_id or config.apns_team_id, path)

    def provider(self) -> dict[str, Any]:
        """What the maps should use: {"provider": "apple", "token", "expires"} or {"provider": "openfreemap"}."""
        sig = self.settings()
        key_id, team_id, path = sig
        if not key_id or not team_id:
            return {"provider": "openfreemap"}
        now = int(time.time())
        if self._token and self._sig == sig and self._exp - now > REUSE_UNTIL_S:
            return {"provider": "apple", "token": self._token, "expires": self._exp}
        try:
            key = Path(path).read_text()
            token = jwt.encode({"iss": team_id, "iat": now, "exp": now + TOKEN_LIFE_S}, key, algorithm="ES256",
                               headers={"kid": key_id, "typ": "JWT"})
        except Exception as e:  # noqa: BLE001 - a missing or bad key just means the other map
            if self._warned != sig:
                self._warned = sig
                log.warning("Apple Maps off: can't use the MapKit key %s (%s) - maps use OpenFreeMap",
                            path, type(e).__name__)
            self._token, self._sig = "", None
            return {"provider": "openfreemap"}
        self._token, self._exp, self._sig, self._warned = token, now + TOKEN_LIFE_S, sig, None
        return {"provider": "apple", "token": token, "expires": self._exp}
