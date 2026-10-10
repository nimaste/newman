"""Accounts and sign-in.

* At home (a private address, straight to Newman) a username + password is enough.
* Away (anything relayed by the reverse proxy) an admin setting decides what else is needed:
  a device certificate (mTLS, checked by Caddy and passed on as headers), a 2FA code, either,
  or nothing. 2FA itself is optional per person.
* A device signs in once and keeps a long-lived session (revocable). Tablet sessions can't
  change settings and need the person's PIN for sensitive commands (unlock, open, disarm).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
import struct
import time
from dataclasses import dataclass
from typing import Any

# "tablet": a shared wall tablet's own account - it signs in only on a wall tablet, isn't a person
# (never notified, never on People), and can use devices but change nothing.
# "guest": someone staying over - uses the house like family, but isn't a person (never on People,
# never notified) and can't see where anyone is.
ROLES = ("admin", "family", "guest", "tablet")
NOT_PEOPLE = ("guest", "tablet")  # accounts that aren't one of the household's people
KINDS = ("browser", "app", "tablet")
AWAY_POLICIES = ("cert_or_2fa", "cert", "2fa", "password")
TABLET_SENSITIVE = ("none", "confirm", "pin")
DEFAULT_SETTINGS = {
    "awayPolicy": "cert_or_2fa",
    # What a wall tablet does before unlock / open / disarm: "none" (just do it - it's inside the
    # house), "confirm" (an "Are you sure?" tap, for accidental touches) or "pin".
    "tabletSensitive": "none",
    "homeTwoFactor": False,
    # Where the reverse proxy connects from (Docker networks by default: Caddy runs in one).
    "trustedProxies": ["172.16.0.0/12"],
    # Headers the proxy sets for a verified client certificate.
    "certHeader": "X-Client-Cert-Fingerprint",
    "certSubjectHeader": "X-Client-Cert-Subject",
}
SESSION_COOKIE = "newman_session"
SESSION_DAYS = {"browser": 30, "app": 365, "tablet": 365}
SHORT_BROWSER_HOURS = 12

# -- passwords & PINs ---------------------------------------------------------------------------


def hash_secret(secret: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(secret.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def check_secret(secret: str, stored: str | None) -> bool:
    try:
        algo, n, r, p, salt, digest = (stored or "").split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(secret.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def password_problem(pw: str) -> str | None:
    if len(pw) < 8:
        return "Use at least 8 characters"
    if len(pw) > 200:
        return "That's too long"
    return None


# -- TOTP (RFC 6238, what authenticator apps use) ----------------------------------------------


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = mac[-1] & 0x0F
    return f"{(struct.unpack('>I', mac[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def totp_match(secret: str, code: str, now: float | None = None) -> int | None:
    """The time step the code belongs to (±30 s of clock drift), or None."""
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != 6 or not secret:
        return None
    step = int((now or time.time()) // 30)
    for s in (step - 1, step, step + 1):
        if hmac.compare_digest(_hotp(secret, s), code):
            return s
    return None


def totp_uri(secret: str, username: str) -> str:
    from urllib.parse import quote
    return f"otpauth://totp/Newman:{quote(username)}?secret={secret}&issuer=Newman&digits=6&period=30"


def new_recovery_codes() -> list[str]:
    return ["-".join(secrets.token_hex(3) for _ in range(2)) for _ in range(10)]


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# -- where a request comes from ---------------------------------------------------------------


# Private networks (a home LAN, a home VPN): someone relayed from one of these is at home.
HOME_NETWORKS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"]
# A certificate fingerprint as a proxy passes it: hex (SHA-1 or longer), optionally colon-separated.
FINGERPRINT = re.compile(r"(?:[0-9A-Fa-f]{2}:?){20,64}")
# What a proxy sends when there's no certificate but it hashes anyway (Caddy: SHA-256 of nothing).
EMPTY_HASHES = {hashlib.sha256(b"").hexdigest(), hashlib.sha1(b"").hexdigest()}


@dataclass
class Origin:
    ip: str            # the client's address (through the proxy when relayed)
    home: bool         # from a private address: directly, or relayed by the trusted proxy
    relayed: bool      # came through the trusted reverse proxy
    cert: str | None   # fingerprint of a client certificate the proxy verified
    cert_subject: str | None


def _in(ip: str, nets: list[str]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in ipaddress.ip_network(n, strict=False) for n in nets)
    except ValueError:
        return False


def origin_of(peer: str | None, headers: Any, settings: dict[str, Any]) -> Origin:
    peer = peer or ""
    try:
        addr = ipaddress.ip_address(peer)
        private = addr.is_private or addr.is_loopback
    except ValueError:
        private = False
    proxies = settings.get("trustedProxies") or []
    relayed = _in(peer, proxies)
    forwarded = "X-Forwarded-For" in headers or "Forwarded" in headers
    if relayed:
        # The proxy appends the address it saw; earlier entries are whatever the client claimed.
        chain = [p.strip() for p in str(headers.get("X-Forwarded-For", "")).split(",") if p.strip()]
        ip = chain[-1] if chain else peer
        # Someone on the home network (or the home VPN) using the public address is still at home -
        # the same rule as the proxy's own "no certificate needed on the LAN" exclusion.
        home = bool(chain) and _in(ip, HOME_NETWORKS) and not _in(ip, proxies)
        # A proxy passes its placeholder text through when no certificate was given ("{http.request.
        # tls.client.fingerprint}"): only a real fingerprint counts.
        cert = str(headers.get(settings.get("certHeader") or DEFAULT_SETTINGS["certHeader"]) or "").strip()
        cert = cert if FINGERPRINT.fullmatch(cert) and cert.replace(":", "").lower() not in EMPTY_HASHES else None
        subject = headers.get(settings.get("certSubjectHeader") or DEFAULT_SETTINGS["certSubjectHeader"]) if cert else None
        return Origin(ip=ip, home=home, relayed=True, cert=cert, cert_subject=subject)
    # Straight to Newman. A forwarding header from anyone but the trusted proxy means some other
    # proxy is in front of us: treat that as away too, and trust nothing it claims.
    return Origin(ip=peer, home=private and not forwarded, relayed=False, cert=None, cert_subject=None)


# -- attempts & lockouts ----------------------------------------------------------------------


class Throttle:
    """Slows down guessing: too many failures from one address, or against one account from
    outside, locks sign-in for a while. At home the limits are looser."""

    WINDOW = 15 * 60

    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        now = time.time()
        hits = [t for t in self._fails.get(key, []) if now - t < self.WINDOW]
        self._fails[key] = hits
        return hits

    def blocked(self, ip: str, username: str, home: bool) -> float:
        """Seconds until sign-in is allowed again (0 = go ahead)."""
        limit_ip, limit_user = (20, 50) if home else (5, 10)
        waits = []
        for key, limit in ((f"ip:{ip}", limit_ip), (f"user:{username}:{'home' if home else 'away'}", limit_user)):
            hits = self._recent(key)
            if len(hits) >= limit:
                waits.append(hits[-limit] + self.WINDOW - time.time())
        return max(waits, default=0.0)

    def fail(self, ip: str, username: str, home: bool) -> None:
        for key in (f"ip:{ip}", f"user:{username}:{'home' if home else 'away'}"):
            self._fails.setdefault(key, []).append(time.time())

    def clear(self, ip: str, username: str, home: bool) -> None:
        self._fails.pop(f"ip:{ip}", None)
        self._fails.pop(f"user:{username}:{'home' if home else 'away'}", None)


def needs_second_factor(user: dict[str, Any], origin: Origin, settings: dict[str, Any]) -> str | None:
    """What sign-in still needs after the password: None, "totp", or an error to show.
    Someone who turned 2FA on is always asked for it away (and at home when that switch is on);
    the away policy decides what's required of everyone else."""
    has_totp = bool(user.get("totp_enabled"))
    if origin.home:
        return "totp" if has_totp and settings.get("homeTwoFactor") else None
    policy = settings.get("awayPolicy", "cert_or_2fa")
    if policy == "cert" and not origin.cert:
        return "error:This connection needs a device certificate (mTLS) to sign in from outside the house."
    if has_totp:
        return "totp"
    if policy == "2fa" or (policy == "cert_or_2fa" and not origin.cert):
        return ("error:Signing in from outside the house needs two-factor codes. Turn them on in your "
                "profile at home, or use a device with a certificate.")
    return None
