"""LG appliances, through LG's official ThinQ Connect service.

Setup takes a personal access token from connect-pat.lgthinq.com (sign in with the LG account the
ThinQ app uses) - Newman keeps that token, never a password. Appliances are read every 30 seconds
while one is running and every 2 minutes otherwise. A two-in-one unit (a WashTower, a washer with a
mini washer) shows up as one appliance per part.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx

from ..kinds import humanize
from ._common import clean_id, plural
from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

API_KEY = "v6GFvkweNo7DK7yD3ylIZ9w52aKBU0eJ7wLXkSR3"  # LG's published key for ThinQ Connect clients
AMERICAS = {"CA", "US", "MX", "BR", "AR", "CL", "CO", "PE", "PA", "CR", "GT", "DO", "EC", "PR", "JM", "HN", "SV",
            "UY", "PY", "BO", "VE", "TT", "BS", "BB", "NI"}
EUROPE = {"GB", "IE", "DE", "FR", "ES", "IT", "PT", "NL", "BE", "LU", "AT", "CH", "SE", "NO", "DK", "FI", "IS",
          "PL", "CZ", "SK", "HU", "RO", "BG", "GR", "HR", "SI", "RS", "BA", "ME", "MK", "AL", "EE", "LV", "LT",
          "UA", "TR", "CY", "MT", "MA", "TN", "DZ", "EG", "SA", "AE", "IL", "JO", "KW", "QA", "BH", "OM",
          "ZA", "NG", "KE", "GH"}
COUNTRIES = tuple({"value": c, "label": n} for c, n in (
    ("CA", "Canada"), ("US", "United States"), ("GB", "United Kingdom"), ("AU", "Australia"), ("NZ", "New Zealand"),
    ("IE", "Ireland"), ("DE", "Germany"), ("FR", "France"), ("ES", "Spain"), ("IT", "Italy"), ("NL", "Netherlands"),
    ("MX", "Mexico"), ("BR", "Brazil"), ("KR", "South Korea"), ("IN", "India"), ("JP", "Japan")))

RUN_STATES = {"POWER_OFF": "off", "SLEEP": "off", "INITIAL": "ready", "STANDBY": "ready", "DISPLAY_LOADSIZE": "ready",
              "PAUSE": "paused", "RESERVED": "delayed", "END": "finished", "ERROR": "error"}
# Newman's appliance commands -> the operation values LG uses for them, first one the appliance offers.
OPERATIONS = {"start": ("START",), "resume": ("RESUME", "START"), "pause": ("PAUSE", "STOP"),
              "stop": ("CANCEL", "STOP"), "turn_off": ("POWER_OFF",)}
FRIENDLY_ERRORS = {"2301": "Turn on remote control on the appliance first",
                   "2302": "The appliance can't do that right now",
                   "2304": "The appliance is off", "1222": "The appliance isn't connected to Wi-Fi",
                   "1306": "LG's request limit was reached; try again later",
                   "1103": "LG no longer accepts the access token - make a new one",
                   "1218": "LG no longer accepts the access token - make a new one"}
BUSY = {"running", "paused", "delayed", "attention"}


def region(country: str) -> str:
    c = (country or "").upper()
    return "aic" if c in AMERICAS else "eic" if c in EUROPE else "kic"


def _enum(v: Any) -> Any:
    return v.replace("_", " ").lower() if isinstance(v, str) and v.upper() == v and any(ch.isalpha() for ch in v) else v


def parts(state: Any) -> list[tuple[str, dict[str, Any], Callable[[dict], dict]]]:
    """An appliance's state -> its parts: [(part name or "", part state, wrap a control payload)]."""
    if isinstance(state, list):  # per-location (e.g. a washer with a mini washer)
        out = []
        for el in state:
            loc = str(((el or {}).get("location") or {}).get("locationName") or "")
            out.append((loc if len(state) > 1 else "", el or {},
                        (lambda p, loc=loc: {"location": {"locationName": loc}, **p}) if loc else (lambda p: p)))
        return out
    if isinstance(state, dict) and state and all(isinstance(v, dict) and "runState" in v for v in state.values()):
        return [(k, v, lambda p, k=k: {k: p}) for k, v in state.items()]  # a WashTower: {"washer": …, "dryer": …}
    return [("", state or {}, lambda p: p)]


def readings(state: dict[str, Any]) -> tuple[str | None, dict[str, Any], dict[str, str]]:
    """One part's ThinQ state -> (appliance state, attrs, units)."""
    attrs: dict[str, Any] = {}
    units: dict[str, str] = {}
    run = None
    for resource, val in state.items():
        if resource == "location":
            continue
        rows = val if isinstance(val, list) else [val] if isinstance(val, dict) else []
        for row in rows:
            suffix = humanize(str(row.get("locationName") or "").lower()).replace(" ", "") if len(rows) > 1 else ""
            for prop, v in row.items():
                if prop in ("locationName", "unit") or isinstance(v, (dict, list)):
                    continue
                if prop == "currentState":
                    run = str(v)
                    continue
                name = prop + suffix
                attrs[name] = _enum(v)
                if "emperature" in prop and row.get("unit") in ("C", "F"):
                    units[name] = f"°{row['unit']}"
    h, m = attrs.pop("remainHour", None), attrs.pop("remainMinute", None)
    if h is not None or m is not None:
        attrs["remaining"] = int(h or 0) * 60 + int(m or 0)
    for k in ("totalHour", "totalMinute"):
        attrs.pop(k, None)
    if "remoteControlEnabled" in attrs:
        attrs["remoteStart"] = bool(attrs.pop("remoteControlEnabled"))
    doors = [v for k, v in attrs.items() if k.startswith("doorState")]
    if doors:
        attrs["door"] = "open" if "open" in doors else "closed"
    state_ = None if run is None else RUN_STATES.get(run.upper(), "running")
    return state_, attrs, units


def operation(profile: Any) -> tuple[str, list[str]] | None:
    """Find the "…OperationMode" control in an appliance's profile: (its name, values it accepts)."""
    if isinstance(profile, dict):
        for k, v in profile.items():
            if k.endswith("OperationMode") and isinstance(v, dict):
                vals = (v.get("value") or {}).get("w") if isinstance(v.get("value"), dict) else None
                return k, [str(x) for x in vals or []]
            if found := operation(v):
                return found
    elif isinstance(profile, list):
        for v in profile:
            if found := operation(v):
                return found
    return None


class ThinQIntegration(Integration):
    type = "lg_thinq"
    name = "LG ThinQ"
    category = "appliances"
    provides = ("appliance",)
    description = ("LG appliances - washers, dryers, WashTowers, dishwashers, ovens, fridges, stylers: status, time "
                   "left, door, finished alerts; start, pause and off where remote control is on. Uses LG's "
                   "official ThinQ Connect service with an access token, never your LG password.")
    fields = [
        Field("token", "Personal access token", "password", required=True,
              help="Go to connect-pat.lgthinq.com, sign in with the LG account your ThinQ app uses, choose "
                   "“Add new token”, tick every permission, create it and paste it here."),
        Field("country", "Country of your LG account", "select", default="CA", options=COUNTRIES),
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._dir = Path(self.ctx.data_dir) / "integrations" / self.id
        self._http = httpx.AsyncClient(timeout=20)
        # entity id -> (LG device id, wrap for control payloads, device type, part name)
        self._parts: dict[str, tuple[str, Callable[[dict], dict], str, str]] = {}
        self._profiles: dict[str, Any] = {}  # device id -> what LG says it can report and do
        self._client_id = ""

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config['token']}", "x-country": str(self.config.get("country") or "CA"),
                "x-message-id": base64.urlsafe_b64encode(uuid.uuid4().bytes)[:-2].decode(),
                "x-client-id": self._client_id, "x-api-key": API_KEY, "x-service-phase": "OP",
                "Content-Type": "application/json", **(extra or {})}

    async def _api(self, method: str, path: str, body: Any = None, extra: dict[str, str] | None = None) -> Any:
        url = f"https://api-{region(str(self.config.get('country') or 'CA'))}.lgthinq.com/{path}"
        r = await self._http.request(method, url, headers=self._headers(extra), json=body)
        payload = r.json() if r.content else {}
        if r.status_code >= 400:
            err = payload.get("error") or {}
            code = str(err.get("code") or "")
            raise CommandError(FRIENDLY_ERRORS.get(code) or err.get("message") or f"LG said no (HTTP {r.status_code})")
        return payload.get("response")

    async def run(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        cid = self._dir / "client_id"
        if not cid.exists():
            cid.write_text(f"newman-{uuid.uuid4()}")
        self._client_id = cid.read_text().strip()
        attempt, listed_at = 0, 0.0
        devices: list[dict[str, Any]] = []
        while True:
            try:
                if time.time() - listed_at > 1800:
                    devices = await self._api("GET", "devices") or []
                    listed_at = time.time()
                for d in devices:
                    await self._refresh(d)
                n = len({p[0] for p in self._parts.values()})
                self.set_status("connected" if n else "error", plural(n, "appliance") if n else
                                "No appliances on this LG account - add them in the ThinQ app")
                attempt = 0
                busy = any((self.hub.get(e) and self.hub.get(e).state in BUSY) for e in self._parts)
                await asyncio.sleep(30 if busy else 120)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("LG ThinQ: %s", e)
                self.set_status("error", f"LG ThinQ: {e}")
                await self.backoff_sleep(attempt + 4)
                attempt += 1

    async def _refresh(self, d: dict[str, Any]) -> None:
        dev = d.get("deviceId")
        info = d.get("deviceInfo") or {}
        dtype = str(info.get("deviceType") or "").replace("DEVICE_", "")
        if not dev:
            return
        if dev not in self._profiles:
            try:
                self._profiles[dev] = await self._api("GET", f"devices/{dev}/profile")
            except CommandError as e:
                log.info("LG ThinQ profile %s: %s", dev, e)
                self._profiles[dev] = None
        try:
            state = await self._api("GET", f"devices/{dev}/state")
            ok = True
        except CommandError as e:
            log.info("LG ThinQ %s: %s", dev, e)
            state, ok = None, False
        base = info.get("alias") or humanize(dtype.lower())
        for part, pstate, wrap in parts(state) if ok else [("", {}, lambda p: p)]:
            eid = self.entity_id(clean_id(dev) + (f"_{clean_id(part)}" if part else ""))
            name = f"{base} {humanize(part.lower())}" if part else base
            self._parts[eid] = (dev, wrap, dtype, part)
            ent = self.hub.upsert(eid, kind="appliance", name=name, integration=self.id)
            if not ok:
                await self.hub.update(eid, available=False)
                continue
            st, attrs, units = readings(pstate)
            attrs.update(applianceType=humanize((part or dtype).lower()).lower(), manufacturer="LG",
                         model=info.get("modelName") or "")
            if units:
                attrs["units"] = {**(ent.attrs.get("units") or {}), **units}
            st = st or "on"
            prev = ent.state
            await self.hub.update(eid, available=True, state=st, attrs=attrs)
            if st == "finished" and prev in BUSY:
                await self.hub.emit(eid, "finished", {"program": attrs.get("currentCourse")})
            if st == "error" and prev not in (None, "error"):
                await self.hub.emit(eid, "error", {"detail": "it reported an error"})

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        part = self._parts.get(entity_id)
        if not part:
            raise CommandError("That appliance isn't connected")
        dev, wrap, dtype, part_name = part
        profile = self._profiles.get(dev)
        if isinstance(profile, dict) and isinstance(profile.get(part_name), dict):
            profile = profile[part_name]  # one part of a WashTower
        op = operation(profile)
        name, allowed = op if op else (f"{dtype.lower().title().replace('_', '')[:1].lower()}"
                                       f"{dtype.lower().title().replace('_', '')[1:]}OperationMode", [])
        wanted = OPERATIONS.get(command)
        if not wanted:
            raise CommandError(f"An appliance can't {command.replace('_', ' ')}")
        mode = next((m for m in wanted if m in allowed), None) if allowed else wanted[0]
        if not mode:
            raise CommandError(f"This appliance can't {command.replace('_', ' ')} remotely")
        await self._api("POST", f"devices/{dev}/control", wrap({"operation": {name: mode}}),
                        extra={"x-conditional-control": "true"})
        await asyncio.sleep(2)
        try:
            for d in await self._api("GET", "devices") or []:
                if d.get("deviceId") == dev:
                    await self._refresh(d)
        except CommandError:
            pass
