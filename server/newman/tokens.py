"""HMAC-signed, expiring, per-device tokens for the two things a phone may do
without a full API session: fetch a notification's snapshot, and press a
notification's action button. Bound to (purpose, subject, device) so a leaked
snapshot link can't be replayed as a lock command, or used from another phone.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time

from .config import config

log = logging.getLogger(__name__)

SNAPSHOT_TTL_S = 24 * 3600
ACTION_TTL_S = 12 * 3600

_secret = config.token_secret
if not _secret:
    _secret = secrets.token_hex(32)
    log.warning("TOKEN_SECRET not set - using an ephemeral secret; links in pushes sent "
                "before a restart will stop working. Generate one with `openssl rand -hex 32`.")
_KEY = _secret.encode()


def _mac(purpose: str, subject: str, device_id: str, exp: int) -> str:
    msg = f"{purpose}.{subject}.{device_id}.{exp}".encode()
    return base64.urlsafe_b64encode(hmac.new(_KEY, msg, hashlib.sha256).digest()).rstrip(b"=").decode()


def sign(purpose: str, subject: str, device_id: str, ttl_s: int) -> tuple[int, str]:
    exp = int(time.time()) + ttl_s
    return exp, _mac(purpose, subject, device_id, exp)


def verify(purpose: str, subject: str, device_id: str, exp: str | int | None, token: str | None) -> bool:
    if not (device_id and exp and token):
        return False
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i < time.time():
        return False
    return hmac.compare_digest(_mac(purpose, subject, device_id, exp_i), token)
