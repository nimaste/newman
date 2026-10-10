"""HMAC-signed, expiring, per-device tokens for the two things a phone may do
without a full API session: fetch a notification's snapshot, and press a
notification's action button. Bound to (purpose, subject, device) so a leaked
snapshot link can't be replayed as a lock command, or used from another phone.

The signing secret: TOKEN_SECRET when it's set; otherwise the data folder's .token_secret, made
(random, mode 600) on first start. That file is in backups, so a restore brings it back.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from pathlib import Path

from .config import config

log = logging.getLogger(__name__)

SNAPSHOT_TTL_S = 24 * 3600
ACTION_TTL_S = 12 * 3600
SECRET_FILE = ".token_secret"

_key_cache: bytes | None = None


def load_secret(data_dir: Path, from_env: str = "") -> str:
    """TOKEN_SECRET if set, else <data_dir>/.token_secret - made (and logged once) when missing."""
    if from_env:
        return from_env
    path = Path(data_dir) / SECRET_FILE
    try:
        existing = path.read_text().strip()
    except FileNotFoundError:
        existing = ""
    if existing:
        return existing
    secret = secrets.token_hex(32)
    try:
        write_secret_file(path, secret)
    except OSError as e:
        log.warning("Couldn't save a token secret in %s (%s) - using one for this run only; links in "
                    "notifications sent before a restart will stop working", path, e)
        return secret
    log.info("Made a token secret in %s (TOKEN_SECRET isn't set); it's kept in backups", path)
    return secret


def write_secret_file(path: Path, secret: str) -> None:
    """Write the secret readable by its owner only (0600), replacing any file there."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secret + "\n")
    os.chmod(tmp, 0o600)  # in case the .part file was already there with other permissions
    os.replace(tmp, path)


def _key() -> bytes:
    # Loaded on first use, not at import: a restore is swapped in (with its .token_secret) at start-up
    # after this module is imported.
    global _key_cache
    if _key_cache is None:
        _key_cache = load_secret(config.data_dir, config.token_secret).encode()
    return _key_cache


def init() -> None:
    """Load (or make) the secret now - at start-up, after a pending restore is swapped in."""
    _key()


def _reset() -> None:
    """Forget the loaded secret (tests)."""
    global _key_cache
    _key_cache = None


def _mac(purpose: str, subject: str, device_id: str, exp: int) -> str:
    msg = f"{purpose}.{subject}.{device_id}.{exp}".encode()
    return base64.urlsafe_b64encode(hmac.new(_key(), msg, hashlib.sha256).digest()).rstrip(b"=").decode()


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
