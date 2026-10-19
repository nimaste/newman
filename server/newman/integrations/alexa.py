"""Amazon Alexa (experimental): the timers running on your Echos, for the wall tablets.

Amazon has no official way to read them, so this uses the unofficial connection the Alexa app itself
uses (the same one Home Assistant's Alexa Media Player, alexapy and ioBroker's alexa-cookie use). It can
stop working whenever Amazon changes things.

Signing in, once: the admin opens Amazon's own sign-in page (a link Newman makes, with a fresh one-time
challenge), signs in there - password, codes and all go to Amazon only - and pastes back the address of
the page Amazon finishes on (…/ap/maplanding?…). Newman registers itself with Amazon as a device using
the code in that address and keeps only what Amazon hands back to stay signed in: its refresh token
(in the connection's data folder, readable by Newman only). No password, no authenticator key and no
cookies are kept - every start, and whenever Amazon asks, the cookies are made again from the token.
Only when Amazon refuses the token (it was cancelled - a password change, the device removed from the
Amazon account) does the connection ask to be signed in again; it doesn't keep trying meanwhile.

Then, every "Check for timers every (seconds)", it reads Amazon's list of timers and alarms
(/api/notifications) and keeps the running timers: name, which Echo, and when each ends on the hub's
clock. Echo names come from Amazon's device list, read again every half hour. The wall tablets get them
from GET /api/alexa/timers and live {"type": "alexaTimers"} pushes. A timer at zero shows as done until
Amazon's list drops it (it's ringing on the Echo) - at most DONE_CAP_S after it ended."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .base import Field, Integration

log = logging.getLogger(__name__)

# What the Alexa app says it is (as alexa-cookie sends it).
APP_VERSION = "2.2.651540.0"
APP_USER_AGENT = "AmazonWebView/Amazon Alexa/2.2.651540.0/iOS/18.3.1/iPhone"
WEB_USER_AGENT = ("Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/83.0.4103.101 Mobile Safari/537.36")
APP_NAME = "Newman"
DEVICE_TYPE = "A2IVLV5VM2W81"  # the Alexa app on an iPhone
# Signing in and registering always go through amazon.com (as alexapy does, for every country).
IDENTITY = "amazon.com"
# The Alexa web API for each site's region, when Amazon doesn't name one itself (/api/endpoints).
API_BASE = {"amazon.com": "https://na-api-alexa.amazon.com", "amazon.ca": "https://na-api-alexa.amazon.com",
            "amazon.com.mx": "https://na-api-alexa.amazon.com", "amazon.com.br": "https://na-api-alexa.amazon.com",
            "amazon.co.uk": "https://eu-api-alexa.amazon.com", "amazon.de": "https://eu-api-alexa.amazon.com",
            "amazon.fr": "https://eu-api-alexa.amazon.com", "amazon.es": "https://eu-api-alexa.amazon.com",
            "amazon.it": "https://eu-api-alexa.amazon.com", "amazon.in": "https://fe-api-alexa.amazon.com",
            "amazon.co.jp": "https://fe-api-alexa.amazon.com", "amazon.com.au": "https://fe-api-alexa.amazon.com",
            "amazon.co.nz": "https://fe-api-alexa.amazon.com"}
LANGUAGE = {"amazon.ca": "en_CA", "amazon.com": "en_US", "amazon.co.uk": "en_GB", "amazon.de": "de_DE",
            "amazon.fr": "fr_FR", "amazon.es": "es_ES", "amazon.it": "it_IT", "amazon.in": "en_IN",
            "amazon.co.jp": "ja_JP", "amazon.com.au": "en_AU", "amazon.co.nz": "en_NZ", "amazon.com.mx": "es_MX",
            "amazon.com.br": "pt_BR"}
SITES = ("amazon.ca", "amazon.com", "amazon.co.uk", "amazon.de", "amazon.fr", "amazon.es", "amazon.it",
         "amazon.com.au", "amazon.co.nz", "amazon.in", "amazon.co.jp", "amazon.com.mx", "amazon.com.br")
DEVICES_EVERY_S = 30 * 60
DONE_CAP_S = 15 * 60  # a timer at zero that Amazon still lists is shown done for this long at most
NOT_SIGNED_IN = "Not signed in to Amazon. Open settings to sign in."
SIGNED_OUT_BY_AMAZON = "Amazon no longer accepts Newman's sign-in. Open settings to sign in again."


class SignedOut(Exception):
    """Amazon refused the refresh token: it was cancelled, and signing in again is the only way back."""


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign_in_address(client_id: str, challenge: str, site: str) -> str:
    """Amazon's own sign-in page, asking for a one-time code for this Newman (as the Alexa app does)."""
    q = {
        "openid.return_to": f"https://www.{IDENTITY}/ap/maplanding",
        "openid.assoc_handle": "amzn_dp_project_dee_ios",
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "pageId": "amzn_dp_project_dee_ios",
        "accountStatusPolicy": "P1",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.mode": "checkid_setup",
        "openid.ns.oa2": f"http://www.{IDENTITY}/ap/ext/oauth/2",
        "openid.oa2.client_id": f"device:{client_id}",
        "openid.ns.pape": "http://specs.openid.net/extensions/pape/1.0",
        "openid.oa2.response_type": "code",
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.pape.max_auth_age": "0",
        "openid.oa2.scope": "device_auth_access",
        "openid.oa2.code_challenge_method": "S256",
        "openid.oa2.code_challenge": challenge,
        "language": LANGUAGE.get(site, "en_US"),
    }
    return f"https://www.{IDENTITY}/ap/signin?" + urlencode(q)


def code_from(answer: str) -> str:
    """The one-time code in the address Amazon finished on. ValueError: not that address."""
    text = (answer or "").strip()
    q = parse_qs(urlparse(text).query)
    code = (q.get("openid.oa2.authorization_code") or [""])[0]
    if not code:
        raise ValueError("That isn't the address Amazon finished on - it should have "
                         "\"maplanding\" and \"authorization_code\" in it.")
    return code


def timers_from(notifications: list[dict[str, Any]], echos: dict[str, str], hidden: set[str],
                now: float) -> list[dict[str, Any]]:
    """Amazon's timers and alarms -> the running timers to show: {"id", "name", "where", "endsAt", "seconds"}
    on the hub's clock (`now` = when Amazon answered), soonest first."""
    out = []
    for n in notifications or []:
        if n.get("type") != "Timer" or n.get("status") != "ON":
            continue
        serial = n.get("deviceSerialNumber")
        if serial in hidden:
            continue
        left = n.get("remainingTime")
        if not isinstance(left, (int, float)):
            continue
        total = n.get("originalDuration")
        name = (n.get("timerLabel") or "").strip() or "Timer"
        out.append({"id": "alexa:" + str(n.get("id") or n.get("notificationIndex") or serial),
                    "name": name[:1].upper() + name[1:],
                    "where": echos.get(serial) or "Echo",
                    "endsAt": now + max(0.0, left / 1000.0),
                    "seconds": (total / 1000.0) if isinstance(total, (int, float)) and total > 0 else max(1.0, left / 1000.0)})
    out.sort(key=lambda t: t["endsAt"])
    return out


def _same(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    """The same timers (an end time drifting by a second or two between checks isn't a change)."""
    if [t["id"] for t in a] != [t["id"] for t in b]:
        return False
    return all(abs(x["endsAt"] - y["endsAt"]) < 2.5 and x["name"] == y["name"] and x["where"] == y["where"]
               for x, y in zip(a, b))


class AlexaIntegration(Integration):
    type = "alexa"
    name = "Amazon Alexa"
    category = "other"
    experimental = True
    no_devices = True
    description = ("Shows the timers set on your Echos on the wall tablets. This uses Amazon's unofficial "
                   "connection, so it can stop working when Amazon changes things.")
    fields = [
        Field("site", "Your Amazon site", "select", default="amazon.ca",
              options=tuple({"value": s, "label": s} for s in SITES),
              help="Where your Alexa account is. If Amazon says otherwise when you sign in, Newman uses Amazon's."),
        Field("check_seconds", "Check for timers every (seconds)", "number", default=30, minimum=15, advanced=True),
    ]
    # Which Echos' timers not to show (their serial numbers) - set from the sign-in panel, not the form.
    extra_config_keys = ("hidden_echos",)
    # Tests swap in a fake Amazon.
    transport: Any = None

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._dir = Path(self.ctx.data_dir) / "integrations" / self.id
        self._token: dict[str, Any] = {}
        self._pending: dict[str, str] | None = None
        self._cookies: dict[str, dict[str, str]] = {}  # domain -> cookies, made from the token (never saved)
        self._csrf = ""
        self._api = ""
        self._echos: dict[str, str] = {}  # serial -> name
        self._echo_list: list[dict[str, str]] = []
        self._devices_at = 0.0
        self._checked_at: float | None = None
        self._timers: list[dict[str, Any]] = []
        self._wake = asyncio.Event()
        self._http: httpx.AsyncClient | None = None

    # -- what's kept -----------------------------------------------------------------

    @property
    def site(self) -> str:
        s = self.config.get("site")
        return s if s in SITES else "amazon.ca"

    def _load(self) -> None:
        f = self._dir / "amazon.json"
        try:
            self._token = json.loads(f.read_text()) if f.exists() else {}
        except (OSError, ValueError):
            self._token = {}

    def _save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        f = self._dir / "amazon.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._token))
        os.chmod(tmp, 0o600)
        tmp.replace(f)

    def _forget(self) -> None:
        (self._dir / "amazon.json").unlink(missing_ok=True)
        self._token, self._cookies, self._csrf, self._api = {}, {}, "", ""
        self._set_timers([])

    def _new_challenge(self) -> None:
        serial = secrets.token_hex(16)
        verifier = _b64url(secrets.token_bytes(32))
        self._pending = {"serial": serial, "clientId": serial.encode().hex() + "23413249564c5635564d32573831",
                         "verifier": verifier,
                         "challenge": _b64url(hashlib.sha256(verifier.encode()).digest())}

    def admin_details(self) -> dict[str, Any]:
        signed_in = bool(self._token.get("refresh_token"))
        if not signed_in and not self._pending:
            self._new_challenge()
        hidden = set(self.config.get("hidden_echos") or [])
        return {"amazon": {
            "signedIn": signed_in,
            "signInUrl": None if signed_in else sign_in_address(self._pending["clientId"], self._pending["challenge"],
                                                                self.site),
            "checkedAt": self._checked_at,
            "echos": [{"serial": e["serial"], "name": e["name"], "shown": e["serial"] not in hidden}
                      for e in self._echo_list],
        }}

    def timers(self) -> list[dict[str, Any]]:
        """The running timers to show (done ones dropped DONE_CAP_S after they ended)."""
        now = time.time()
        return [dict(t, source="alexa") for t in self._timers if t["endsAt"] > now - DONE_CAP_S]

    def _set_timers(self, timers: list[dict[str, Any]]) -> None:
        if _same(timers, self._timers):
            self._timers = timers
            return
        self._timers = timers
        if self.ctx.broadcast:
            asyncio.ensure_future(self.ctx.broadcast(alexa_timers_message(self.ctx.instances)))

    # -- Amazon ----------------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=20, follow_redirects=False, transport=self.transport)
        return self._http

    @staticmethod
    def _json(r: httpx.Response) -> dict[str, Any]:
        try:
            j = r.json()
        except ValueError:
            return {}
        return j if isinstance(j, dict) else {}

    async def sign_in(self, answer: str) -> None:
        code = code_from(answer)
        if not self._pending:
            raise ValueError("That sign-in has run out. Open Amazon sign-in again.")
        p = self._pending
        body = {
            "requested_extensions": ["device_info", "customer_info"],
            "cookies": {"website_cookies": [], "domain": f".{IDENTITY}"},
            "registration_data": {"domain": "Device", "app_version": APP_VERSION, "device_type": DEVICE_TYPE,
                                  "device_name": "%FIRST_NAME%'s%DUPE_STRATEGY_1ST%" + APP_NAME,
                                  "os_version": "18.3.1", "device_serial": p["serial"], "device_model": "iPhone",
                                  "app_name": APP_NAME, "software_version": "1"},
            "auth_data": {"client_id": p["clientId"], "authorization_code": code, "code_verifier": p["verifier"],
                          "code_algorithm": "SHA-256", "client_domain": "DeviceLegacy"},
            "requested_token_type": ["bearer", "mac_dms", "website_cookies"],
        }
        try:
            r = await self._client().post(f"https://api.{IDENTITY}/auth/register", json=body, headers={
                "User-Agent": APP_USER_AGENT, "Accept": "application/json", "Accept-Charset": "utf-8",
                "x-amzn-identity-auth-domain": f"api.{IDENTITY}"})
        except httpx.HTTPError as e:
            raise ValueError(f"Couldn't reach Amazon: {e}") from e
        tokens = (((self._json(r).get("response") or {}).get("success") or {}).get("tokens") or {})
        refresh = (tokens.get("bearer") or {}).get("refresh_token")
        if r.status_code >= 400 or not refresh:
            # The code works once and only with this challenge: start again with a new one.
            self._new_challenge()
            raise ValueError("Amazon didn't accept that sign-in. Open Amazon sign-in again and paste the new address.")
        self._pending = None
        self._token = {"refresh_token": refresh, "device_serial": p["serial"], "signed_in": time.time()}
        self._save()
        # Which Amazon site the account's Echos are on, as Amazon says.
        try:
            site = await self._marketplace()
            if site and site in SITES and site != self.config.get("site"):
                self.config = dict(self.config, site=site)
                if self.save_config:
                    self.save_config(self.config)
        except (httpx.HTTPError, SignedOut):
            pass
        self._cookies, self._api, self._devices_at = {}, "", 0.0
        self.set_status("connecting", "")
        self._wake.set()

    async def sign_out(self) -> None:
        refresh = self._token.get("refresh_token")
        self._forget()
        self._echos, self._echo_list, self._checked_at = {}, [], None
        self._pending = None
        self.set_status("pairing", NOT_SIGNED_IN)
        if refresh:
            # Take this Newman off the Amazon account too (best effort).
            try:
                access = await self._access_token(refresh)
                await self._client().post(f"https://api.{IDENTITY}/auth/deregister",
                                          json={"deregister_all_existing_accounts": "false"},
                                          headers={"Authorization": f"Bearer {access}", "User-Agent": APP_USER_AGENT,
                                                   "x-amzn-identity-auth-domain": f"api.{IDENTITY}"})
            except (httpx.HTTPError, SignedOut, KeyError):
                pass
        self._wake.set()

    async def _access_token(self, refresh: str) -> str:
        r = await self._client().post(f"https://api.{IDENTITY}/auth/token", data={
            "app_name": APP_NAME, "app_version": APP_VERSION, "di.sdk.version": "6.12.4", "source_token": refresh,
            "package_name": "com.amazon.echo", "di.hw.version": "iPhone", "platform": "iOS",
            "requested_token_type": "access_token", "source_token_type": "refresh_token", "di.os.name": "iOS",
            "di.os.version": "16.6", "current_version": "6.12.4"},
            headers={"User-Agent": APP_USER_AGENT, "x-amzn-identity-auth-domain": f"api.{IDENTITY}"})
        j = self._json(r)
        if 400 <= r.status_code < 500 and r.status_code != 429:
            raise SignedOut()
        r.raise_for_status()
        return j["access_token"]

    async def _make_cookies(self, domain: str) -> dict[str, str]:
        """Amazon's cookies for one site, made from the refresh token. SignedOut: the token was refused."""
        r = await self._client().post(f"https://www.{domain}/ap/exchangetoken/cookies", data={
            "di.os.name": "iOS", "app_version": APP_VERSION, "domain": f".{domain}",
            "source_token": self._token.get("refresh_token", ""), "requested_token_type": "auth_cookies",
            "source_token_type": "refresh_token", "di.hw.version": "iPhone", "di.sdk.version": "6.12.4",
            "app_name": APP_NAME, "di.os.version": "16.6"},
            headers={"User-Agent": APP_USER_AGENT, "Accept": "*/*", "x-amzn-identity-auth-domain": f"api.{domain}"})
        j = self._json(r)
        if 400 <= r.status_code < 500 and r.status_code != 429:
            raise SignedOut()
        r.raise_for_status()
        found = (((j.get("response") or {}).get("tokens") or {}).get("cookies") or {}).get(f".{domain}") or []
        cookies = {c["Name"]: c["Value"] for c in found if c.get("Name")}
        if not cookies:
            raise httpx.HTTPError(f"Amazon sent no cookies for {domain}")
        return cookies

    @staticmethod
    def _cookie_header(cookies: dict[str, str]) -> str:
        return "; ".join(f"{k}={v}" for k, v in cookies.items())

    def _cookies_for(self, url: str) -> dict[str, str]:
        host = urlparse(url).hostname or ""
        for domain, c in self._cookies.items():
            if host == domain or host.endswith("." + domain):
                return c
        return self._cookies.get(self.site) or {}

    async def _marketplace(self) -> str | None:
        """Amazon's own word for which site the account is on ("amazon.ca"), or None."""
        cookies = await self._make_cookies(IDENTITY)
        r = await self._client().get(f"https://alexa.{IDENTITY}/api/users/me",
                                     params={"platform": "ios", "version": APP_VERSION},
                                     headers={"User-Agent": APP_USER_AGENT, "Accept": "application/json",
                                              "Cookie": self._cookie_header(cookies)})
        name = self._json(r).get("marketPlaceDomainName") if r.status_code == 200 else None
        return name.split(".", 1)[1] if isinstance(name, str) and "." in name else None

    async def _connect(self) -> None:
        """Cookies, the anti-forgery token and the API address, all made fresh from the refresh token."""
        site = self.site
        self._cookies = {site: await self._make_cookies(site)}
        if site != IDENTITY:
            try:
                self._cookies[IDENTITY] = await self._make_cookies(IDENTITY)
            except httpx.HTTPError:
                pass
        self._csrf = ""
        web = f"https://alexa.{site}"
        for path in ("/api/language", "/spa/index.html", "/api/devices-v2/device?cached=false"):
            r = await self._client().get(web + path, headers={
                "User-Agent": WEB_USER_AGENT, "Accept": "application/json",
                "Cookie": self._cookie_header(self._cookies[site]), "Referer": f"{web}/spa/index.html"})
            if csrf := r.cookies.get("csrf"):
                self._csrf = csrf
                self._cookies[site]["csrf"] = csrf
                break
        self._api = ""
        try:
            r = await self._client().get(web + "/api/endpoints", headers={
                "User-Agent": WEB_USER_AGENT, "Accept": "application/json",
                "Cookie": self._cookie_header(self._cookies[site])})
            if r.status_code == 200:
                self._api = str(self._json(r).get("websiteApiUrl") or "").rstrip("/")
        except httpx.HTTPError:
            pass
        self._api = self._api or API_BASE.get(site, web)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        url = self._api + path
        web = f"https://alexa.{self.site}"
        headers = {"User-Agent": WEB_USER_AGENT, "Accept": "application/json", "Accept-Language": "en-US",
                   "Cookie": self._cookie_header(self._cookies_for(url)), "Referer": f"{web}/spa/index.html",
                   "Origin": web}
        if self._csrf:
            headers["csrf"] = self._csrf
        return await self._client().get(url, params=params, headers=headers)

    async def _read(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """A GET on the Alexa API; a refused one makes the cookies again (once). None: Amazon is busy."""
        if not self._cookies:
            await self._connect()
        r = await self._get(path, params)
        if r.status_code in (401, 403):
            await self._connect()
            r = await self._get(path, params)
        if r.status_code == 400 and "Rate exceeded" in r.text:
            return None
        if r.status_code in (401, 403):
            raise httpx.HTTPError(f"Amazon refused {path} ({r.status_code})")
        r.raise_for_status()
        return self._json(r)

    async def _read_devices(self) -> None:
        j = await self._read("/api/devices-v2/device", {"cached": "true"})
        if j is None:
            return
        echos = []
        for d in j.get("devices") or []:
            caps = d.get("capabilities")
            if not d.get("serialNumber") or (isinstance(caps, list) and caps and "TIMERS_AND_ALARMS" not in caps):
                continue
            echos.append({"serial": d["serialNumber"], "name": d.get("accountName") or d["serialNumber"]})
        echos.sort(key=lambda e: e["name"].lower())
        self._echo_list = echos
        self._echos = {e["serial"]: e["name"] for e in echos}
        self._devices_at = time.time()

    async def check(self) -> None:
        """Once: the device list now and then, then Amazon's timers."""
        if time.time() - self._devices_at > DEVICES_EVERY_S:
            await self._read_devices()
        j = await self._read("/api/notifications", {"cached": "true", "_": str(int(time.time() * 1000))})
        if j is None:
            return
        now = time.time()
        self._checked_at = now
        self._set_timers(timers_from(j.get("notifications") or [], self._echos,
                                     set(self.config.get("hidden_echos") or []), now))

    async def _wait(self, seconds: float) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), seconds)
        except asyncio.TimeoutError:
            pass

    async def run(self) -> None:
        self._load()
        attempt = 0
        try:
            while True:
                if not self._token.get("refresh_token"):
                    self.set_status("pairing", NOT_SIGNED_IN if not self._token.get("signed_out") else SIGNED_OUT_BY_AMAZON)
                    await self._wait(3600)
                    continue
                try:
                    await self.check()
                    attempt = 0
                    self.set_status("connected", "")
                    await self._wait(self.seconds_every("check_seconds", unit=1.0))
                except SignedOut:
                    log.warning("[%s] Amazon refused the sign-in token - signing in again is needed", self.display_name)
                    self._forget()
                    self._token = {"signed_out": True}
                    self._echos, self._echo_list, self._checked_at = {}, [], None
                except (httpx.HTTPError, KeyError, ValueError) as e:
                    attempt += 1
                    self._cookies = {}
                    self.set_status("error", f"Couldn't read the timers from Amazon: {e}")
                    await self._wait(min(30 * 2 ** (attempt - 1), 600))
        finally:
            self._set_timers([])
            if self._http:
                await self._http.aclose()
                self._http = None


def alexa_timers_message(instances: dict[str, Any]) -> dict[str, Any]:
    """Every Alexa connection's running timers, for the wall tablets."""
    timers = [t for inst in instances.values() if isinstance(inst, AlexaIntegration) for t in inst.timers()]
    timers.sort(key=lambda t: t["endsAt"])
    return {"type": "alexaTimers", "now": time.time(), "timers": timers}
