"""Meross garage door openers (MSG100/MSG200) over Meross's local HTTP protocol.

This is how Home Assistant's "Meross LAN" integration talks to them: signed
JSON messages to http://<device>/config, no cloud involved. The one secret is
the device key Meross assigns when the opener is set up in their app. Because
it never touches HomeKit, the opener keeps working in Apple Home / Siri /
CarPlay alongside Newman.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import random
import string
import time
import uuid
from typing import Any

import httpx

from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

CLOUD_BASE = "https://iotx-us.meross.com"
CLOUD_SECRET = "23x17ahWarFH6w29"  # the public app secret every Meross client signs sign-in requests with
NS_ALL = "Appliance.System.All"
NS_GARAGE = "Appliance.GarageDoor.State"


class MerossError(Exception):
    pass


class MerossIntegration(Integration):
    type = "meross"
    name = "Meross garage opener"
    category = "garage"
    provides = ("garage",)  # kinds of device it can bring in (shown on its catalog card)
    description = ("Controls a Meross garage opener directly on your network, and it stays in Apple Home / CarPlay. "
                   "Sign in with your Meross account once: Newman fetches the opener's device key, keeps only the key "
                   "and forgets your password - after that it never talks to Meross's cloud.")
    fields = [
        Field("host", "Opener address", required=True, placeholder="10.1.1.35"),
        Field("email", "Meross account email", help="Used once to fetch the device key, then removed."),
        Field("password", "Meross account password", "password", help="Used once, then removed."),
        Field("mfa", "Meross verification code", help="Only if your Meross account uses two-step verification."),
        Field("key", "Device key (advanced)", "password",
              help="Filled in automatically after signing in. Only enter it yourself if you already know it."),
        Field("name", "Name", default="Garage"),
        Field("channel", "Channel", "number", default=0, help="0 for a single-door opener."),
        Field("travel_seconds", "Door travel time (seconds)", "number", default=15,
              help="How long the door takes to open or close - Newman shows Opening / Closing for this long."),
        Field("poll_seconds", "Check every (seconds)", "number", default=3),
    ]

    # Meross openers also announce HomeKit; that's how Newman spots them on the network.
    zeroconf = ["_hap._tcp.local."]

    @classmethod
    def from_discovery(cls, name: str, host: str, props: dict[str, str]) -> dict[str, Any] | None:
        model = props.get("md", "")
        if not model.upper().startswith(("MSG1", "MSG2")):
            return None
        return {"title": f"Meross {model} garage opener", "config": {"host": host}}

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.eid = self.entity_id("garage")
        # The opener's tiny web server hangs on a reused (keep-alive) connection: one request per connection.
        self._http = httpx.AsyncClient(timeout=5, limits=httpx.Limits(max_keepalive_connections=0),
                                       headers={"Connection": "close"})
        self._uuid = ""
        self._moving_until = 0.0
        self._moving_to: str | None = None

    # -- protocol ---------------------------------------------------------------------

    async def _request(self, method: str, namespace: str, payload: dict[str, Any]) -> dict[str, Any]:
        host = self.config["host"]
        ts, mid = int(time.time()), uuid.uuid4().hex
        sign = hashlib.md5(f"{mid}{self.config['key']}{ts}".encode()).hexdigest()
        msg = {"header": {"messageId": mid, "namespace": namespace, "method": method, "payloadVersion": 1,
                          "from": f"http://{host}/config", "timestamp": ts, "timestampMs": 0, "sign": sign},
               "payload": payload}
        resp = await self._http.post(f"http://{host}/config", json=msg)
        resp.raise_for_status()
        data = resp.json()
        if data.get("header", {}).get("method") == "ERROR":
            err = data.get("payload", {}).get("error", {})
            raise MerossError("wrong device key" if err.get("code") == 5001 else f"device error {err}")
        return data.get("payload", {})

    @classmethod
    def validate(cls, config: dict[str, Any]) -> list[str]:
        errors = [e for e in super().validate(config)]
        if not config.get("key") and not (config.get("email") and config.get("password")):
            errors.append("Sign in with your Meross email and password (or enter the device key)")
        return errors

    # -- one-time cloud sign-in to fetch the device key -------------------------------------

    async def _fetch_key(self) -> str:
        """Meross's app sign-in (the same call Meross's own app and meross_lan make). Returns the account key
        every device on the account signs its local messages with."""
        email, password = self.config["email"].strip(), self.config["password"]
        base = CLOUD_BASE
        async with httpx.AsyncClient(timeout=15) as http:
            for attempt in range(3):
                params = {"email": email, "password": hashlib.md5(password.encode()).hexdigest(), "encryption": 1,
                          "agree": 0, "mobileInfo": {"deviceModel": "Newman", "mobileOsVersion": "1",
                                                     "mobileOs": "Linux", "uuid": uuid.uuid4().hex, "carrier": ""}}
                if self.config.get("mfa"):
                    params["mfaCode"] = str(self.config["mfa"]).strip()
                encoded = base64.b64encode(json.dumps(params).encode()).decode()
                nonce = "".join(random.choices(string.ascii_uppercase + string.digits, k=16))
                ts = int(time.time() * 1000)
                sign = hashlib.md5(f"{CLOUD_SECRET}{ts}{nonce}{encoded}".encode()).hexdigest()
                resp = await http.post(f"{base}/v1/Auth/signIn",
                                       json={"params": encoded, "sign": sign, "timestamp": ts, "nonce": nonce},
                                       headers={"Authorization": "Basic", "vender": "meross", "AppVersion": "0.4.4.4",
                                                "AppType": "MerossIOT", "AppLanguage": "EN"})
                resp.raise_for_status()
                body = resp.json()
                status, data = body.get("apiStatus"), body.get("data") or {}
                if status == 0 and data.get("key"):
                    return data["key"]
                if status == 1030 and data.get("domain"):  # account lives in another region: retry there
                    base = data["domain"].rstrip("/")
                    continue
                if status == 1033:
                    raise MerossError("Meross sent a verification code - enter it in “Meross verification code” "
                                      "and save again")
                raise MerossError(f"Meross sign-in failed: {body.get('info') or body.get('apiStatus')}")
        raise MerossError("Meross sign-in failed (region redirect loop)")

    # -- running ------------------------------------------------------------------------

    async def run(self) -> None:
        self.hub.upsert(self.eid, kind="garage", name=self.config.get("name") or "Garage", integration=self.id)
        if not self.config.get("key"):
            self.set_status("pairing", "Signing in to Meross once to fetch the opener's device key…")
            try:
                key = await self._fetch_key()
            except (MerossError, httpx.HTTPError) as e:
                self.set_status("error", str(e))
                await asyncio.Event().wait()  # wait for corrected settings (saving restarts the integration)
            # Keep only the key: the account email / password / code are dropped from the saved settings.
            self.config = {k: v for k, v in self.config.items() if k not in ("email", "password", "mfa")}
            self.config["key"] = key
            if self.save_config:
                self.save_config(self.config)
            log.info("Fetched the Meross device key; account credentials discarded")
        attempt = 0
        while True:
            try:
                info = await self._request("GET", NS_ALL, {})
                system = info.get("all", {}).get("system", {})
                hw = system.get("hardware", {})
                self._uuid = hw.get("uuid", "")
                await self.hub.update(self.eid, available=True, attrs={
                    "model": hw.get("type"), "firmware": system.get("firmware", {}).get("version")})
                self.set_status("connected", f"{self.config['host']} · {hw.get('type', 'Meross')}")
                attempt = misses = 0
                while True:
                    try:
                        await self._poll()
                        misses = 0
                    except (httpx.HTTPError, MerossError) as e:
                        # The opener's little web server occasionally stalls; only a run of misses
                        # means it's really gone (then the outer loop marks it offline and reconnects).
                        misses += 1
                        if misses >= 3:
                            raise
                        log.info("Garage poll missed (%s) - retrying", e.__class__.__name__)
                    await asyncio.sleep(float(self.config.get("poll_seconds") or 3))
            except asyncio.CancelledError:
                raise
            except MerossError as e:
                self.set_status("error", f"{self.config['host']}: {e}")
                await self.hub.update(self.eid, available=False)
                if "key" in str(e):
                    await asyncio.Event().wait()  # wrong key: wait for the config to be fixed (restarts us)
            except Exception as e:
                self.set_status("error", f"{self.config['host']}: {e.__class__.__name__}: {e}")
                await self.hub.update(self.eid, available=False)
            attempt += 1
            await self.backoff_sleep(attempt)

    async def _poll(self) -> None:
        ch = int(self.config.get("channel") or 0)
        # Must name the channel: an empty GET makes the MSG100 hang until timeout (seen live).
        payload = await self._request("GET", NS_GARAGE, {"state": {"channel": ch}})
        states = payload.get("state", [])
        st = next((s for s in (states if isinstance(states, list) else [states]) if s.get("channel", 0) == ch), None)
        if st is None:
            raise MerossError(f"no garage door on channel {ch}")
        actual = "open" if st.get("open") else "closed"
        if self._moving_to and time.time() < self._moving_until and actual != self._moving_to:
            # The MSG100 only knows open/closed from its reed switch; show motion while the door travels.
            state = "opening" if self._moving_to == "open" else "closing"
        else:
            self._moving_to = None
            state = actual
        await self.hub.update(self.eid, state=state, available=True)

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        if command not in ("open", "close"):
            raise CommandError(f"The garage can't {command}")
        if not self._uuid:
            raise CommandError("The garage opener isn't connected")
        target = "open" if command == "open" else "closed"
        try:
            await self._request("SET", NS_GARAGE, {"state": {"channel": int(self.config.get("channel") or 0),
                                                             "open": 1 if command == "open" else 0,
                                                             "uuid": self._uuid}})
        except (MerossError, httpx.HTTPError) as e:
            raise CommandError(f"Garage opener didn't accept the command: {e}") from e
        self._moving_to = target
        self._moving_until = time.time() + float(self.config.get("travel_seconds") or 15) + 5
        await self.hub.update(self.eid, state="opening" if command == "open" else "closing")
