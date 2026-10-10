"""Bosch, Thermador, Siemens, Gaggenau and Neff appliances, through Home Connect (BSH's service).

Home Connect only talks to apps registered with it, so setup is: register a free "application" on
developer.home-connect.com (that gives a Client ID), paste the ID here, then approve Newman once on
Home Connect's own sign-in page. Newman keeps the token Home Connect issues - never a password.

After the first read, everything arrives on Home Connect's live event stream (one connection for
all appliances) instead of polling: Home Connect allows only 1000 requests a day per app.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from ..kinds import humanize
from ._common import clean_id, plural
from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

API = "https://api.home-connect.com"
MEDIA = "application/vnd.bsh.sdk.v1+json"
SCOPE = "IdentifyAppliance Monitor Control Settings"

OPERATION_STATE = "BSH.Common.Status.OperationState"
OPERATION = {"Inactive": "off", "Ready": "ready", "DelayedStart": "delayed", "Run": "running", "Pause": "paused",
             "ActionRequired": "attention", "Finished": "finished", "Error": "error", "Aborting": "running"}
# Readings with a name the appliance kind knows; everything else keeps Home Connect's own name.
NAMED = {"BSH.Common.Status.DoorState": "door", "BSH.Common.Status.RemoteControlStartAllowed": "remoteStart",
         "BSH.Common.Status.RemoteControlActive": "remoteControl", "BSH.Common.Setting.PowerState": "power",
         "BSH.Common.Option.RemainingProgramTime": "remaining", "BSH.Common.Option.ProgramProgress": "progress",
         "BSH.Common.Root.ActiveProgram": "program", "BSH.Common.Root.SelectedProgram": "selectedProgram"}
PROGRAMS = ("BSH.Common.Root.ActiveProgram", "BSH.Common.Root.SelectedProgram")
# Appliances that are simply on all the time (no programs to run).
ALWAYS_ON = {"FridgeFreezer", "Refrigerator", "Freezer", "WineCooler"}
# Events that only repeat what the operation state already says.
NOT_ALERTS = ("ProgramFinished", "ProgramAborted")


class NeedsSignIn(Exception):
    pass


def _last(key: Any) -> str:
    return str(key).rsplit(".", 1)[-1]


def _enum(v: Any) -> Any:
    """"BSH.Common.EnumType.DoorState.Open" -> "open"; anything else unchanged."""
    return humanize(_last(v)).lower() if isinstance(v, str) and ".EnumType." in v else v


def readings(items: list[dict[str, Any]]) -> tuple[str | None, dict[str, Any], dict[str, str]]:
    """Home Connect status/setting/option items -> (operation state, attrs, units)."""
    state, attrs, units = None, {}, {}
    for it in items or []:
        key, v = str(it.get("key") or ""), it.get("value")
        if key == OPERATION_STATE:
            state = OPERATION.get(_last(v))
            continue
        name = NAMED.get(key) or (_last(key)[:1].lower() + _last(key)[1:])
        if not name or isinstance(v, (dict, list)):
            continue
        unit = str(it.get("unit") or "")
        if key in PROGRAMS:
            v = it.get("displayvalue") or it.get("name") or (humanize(_last(v)) if v else None)
        elif unit == "seconds" and isinstance(v, (int, float)):
            v, unit = round(v / 60), "min"
        else:
            v = _enum(v)
        attrs[name] = v
        if unit and unit not in ("enum",):
            units[name] = unit
    return state, attrs, units


class HomeConnectIntegration(Integration):
    type = "homeconnect"
    name = "Home Connect"
    category = "appliances"
    provides = ("appliance",)
    description = ("Bosch, Thermador, Siemens, Gaggenau and Neff appliances - dishwashers, washers, dryers, ovens, "
                   "fridges, coffee machines: status, time left, door, finished and needs-something alerts; start, "
                   "pause and stop where the appliance allows it. Approve Newman once on Home Connect's sign-in page; "
                   "Newman keeps only the token.")
    fields = [
        Field("client_id", "Client ID", required=True,
              help="On developer.home-connect.com: sign up, then Applications → Register: any name, OAuth flow "
                   "“Device Flow”, and for “Home Connect User Account for Testing” the email you use in the "
                   "Home Connect app. Copy the Client ID it gives you here and save; Newman then shows a link "
                   "to approve it."),
        Field("client_secret", "Client Secret", "password", advanced=True,
              help="Shown next to the Client ID. Only needed if Home Connect asks for it when renewing access."),
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._dir = Path(self.ctx.data_dir) / "integrations" / self.id
        self._http = httpx.AsyncClient(timeout=30)
        self._tok: dict[str, Any] | None = None
        self._eids: dict[str, str] = {}   # haId -> entity id
        self._types: dict[str, str] = {}  # haId -> Home Connect appliance type

    # -- sign-in --------------------------------------------------------------------------

    @property
    def _token_file(self) -> Path:
        return self._dir / "token.json"

    def _save_token(self, j: dict[str, Any]) -> None:
        self._tok = {"access_token": j["access_token"],
                     "refresh_token": j.get("refresh_token") or (self._tok or {}).get("refresh_token"),
                     "expires_at": time.time() + float(j.get("expires_in") or 86400)}
        self._dir.mkdir(parents=True, exist_ok=True)
        self._token_file.write_text(json.dumps(self._tok))
        self._token_file.chmod(0o600)

    def _secret(self) -> dict[str, str]:
        s = str(self.config.get("client_secret") or "")
        return {"client_secret": s} if s else {}

    async def _pair(self) -> None:
        """Home Connect's device sign-in: show a link and a code, wait for the owner to approve."""
        r = await self._http.post(f"{API}/security/oauth/device_authorization",
                                  data={"client_id": self.config["client_id"], "scope": SCOPE})
        d = r.json()
        if r.status_code >= 400 or "device_code" not in d:
            raise RuntimeError(d.get("error_description") or d.get("error") or f"HTTP {r.status_code}")
        link = d.get("verification_uri_complete") or d.get("verification_uri")
        self.set_status("pairing", f"Open {link} and sign in with your Home Connect account to let Newman in "
                                   f"(code {d.get('user_code', '')}). Waiting for you…")
        interval, deadline = float(d.get("interval") or 5), time.time() + float(d.get("expires_in") or 600)
        while time.time() < deadline:
            await asyncio.sleep(interval)
            r = await self._http.post(f"{API}/security/oauth/token",
                                      data={"grant_type": "device_code", "device_code": d["device_code"],
                                            "client_id": self.config["client_id"], **self._secret()})
            j = r.json()
            if "access_token" in j:
                self._save_token(j)
                return
            err = j.get("error")
            if err == "slow_down":
                interval += 5
            elif err != "authorization_pending":
                raise RuntimeError(j.get("error_description") or err or f"HTTP {r.status_code}")
        raise RuntimeError("the sign-in link expired before it was approved")

    async def _refresh(self) -> None:
        r = await self._http.post(f"{API}/security/oauth/token",
                                  data={"grant_type": "refresh_token", "refresh_token": self._tok["refresh_token"],
                                        **self._secret()})
        j = r.json() if r.content else {}
        if "access_token" not in j:
            if r.status_code in (400, 401):
                raise NeedsSignIn(j.get("error_description") or "access was withdrawn")
            raise RuntimeError(f"couldn't renew access (HTTP {r.status_code})")
        self._save_token(j)

    async def _auth(self) -> str:
        if self._tok["expires_at"] - time.time() < 600:
            await self._refresh()
        return self._tok["access_token"]

    async def _api(self, method: str, path: str, data: Any = None) -> Any:
        for attempt in (0, 1):
            headers = {"Authorization": f"Bearer {await self._auth()}", "Accept": MEDIA, "Accept-Language": "en-US"}
            if data is not None:
                headers["Content-Type"] = MEDIA
            r = await self._http.request(method, f"{API}{path}", headers=headers,
                                         content=json.dumps({"data": data}) if data is not None else None)
            if r.status_code == 401 and attempt == 0:
                self._tok["expires_at"] = 0  # renew and try once more
                continue
            break
        if r.status_code == 429:
            raise CommandError("Home Connect's daily limit for Newman is used up; it resets within a day")
        body = r.json() if r.content else {}
        if r.status_code >= 400:
            err = body.get("error") or {}
            if err.get("key") in ("SDK.Error.NoProgramActive", "SDK.Error.NoProgramSelected"):
                return None
            raise CommandError(err.get("description") or f"Home Connect said no (HTTP {r.status_code})")
        return body.get("data") if isinstance(body, dict) else None

    # -- running --------------------------------------------------------------------------

    async def run(self) -> None:
        if self._token_file.exists():
            self._tok = json.loads(self._token_file.read_text())
        attempt = 0
        while True:
            try:
                if not self._tok:
                    await self._pair()
                await self._load()
                attempt = 0
                await self._stream()
            except asyncio.CancelledError:
                raise
            except NeedsSignIn as e:
                log.info("Home Connect: sign in again (%s)", e)
                self._tok = None
                self._token_file.unlink(missing_ok=True)
            except Exception as e:
                log.warning("Home Connect: %s", e)
                self.set_status("error", f"Home Connect: {e}")
                await self.backoff_sleep(attempt + 4)
                attempt += 1

    async def _load(self) -> None:
        data = await self._api("GET", "/api/homeappliances") or {}
        for a in data.get("homeappliances") or []:
            ha = a["haId"]
            eid = self.entity_id(clean_id(ha))
            self._eids[ha], self._types[ha] = eid, str(a.get("type") or "")
            self.hub.upsert(eid, kind="appliance", name=a.get("name") or humanize(a.get("type") or "Appliance"),
                            integration=self.id)
            await self.hub.update(eid, attrs={"applianceType": humanize(a.get("type") or "appliance").lower(),
                                              "manufacturer": a.get("brand") or "", "model": a.get("vib") or ""})
            if a.get("connected"):
                await self._read(ha)
            else:
                await self._offline(ha)
        n = len(self._eids)
        self.set_status("connected" if n else "error", plural(n, "appliance") if n else
                        "No appliances on this Home Connect account yet - add them in the Home Connect app")

    async def _read(self, ha: str) -> None:
        """Everything about one appliance (3 requests): its status, settings and running program."""
        items: list[dict[str, Any]] = []
        for path, field in (("status", "status"), ("settings", "settings")):
            try:
                items += (await self._api("GET", f"/api/homeappliances/{ha}/{path}") or {}).get(field) or []
            except CommandError as e:
                log.info("Home Connect %s %s: %s", ha, path, e)
        try:
            prog = await self._api("GET", f"/api/homeappliances/{ha}/programs/active")
        except CommandError:
            prog = None
        items.append({"key": "BSH.Common.Root.ActiveProgram", "value": (prog or {}).get("key"),
                      "name": (prog or {}).get("name")})
        items += (prog or {}).get("options") or []
        await self.hub.update(self._eids[ha], attrs={"connected": True})
        await self._apply(ha, items, available=True)

    async def _apply(self, ha: str, items: list[dict[str, Any]], available: bool | None = None) -> None:
        eid = self._eids.get(ha)
        ent = self.hub.get(eid) if eid else None
        if not ent:
            return
        state, attrs, units = readings(items)
        if units:
            attrs["units"] = {**(ent.attrs.get("units") or {}), **units}
        if state is None and ent.state is None:
            state = "on" if self._types.get(ha) in ALWAYS_ON else "ready"
        if state == "off" and self._types.get(ha) in ALWAYS_ON:
            state = "on"
        changes: dict[str, Any] = {"attrs": attrs}
        if state:
            changes["state"] = state
        if available is not None:
            changes["available"] = available
        prev = ent.state
        new = await self.hub.update(eid, **changes)
        if state == "finished" and prev != "finished":
            await self.hub.emit(eid, "finished", {"program": (new.attrs if new else attrs).get("program")})
        if state == "error" and prev not in (None, "error"):
            await self.hub.emit(eid, "error", {"detail": "it reported an error"})

    async def _alerts(self, ha: str, items: list[dict[str, Any]]) -> None:
        eid = self._eids.get(ha)
        ent = self.hub.get(eid) if eid else None
        if not ent:
            return
        needs = list(ent.attrs.get("needs") or [])
        for it in items or []:
            key = _last(it.get("key"))
            if key in NOT_ALERTS:
                continue
            text = humanize(key).lower()
            if _last(it.get("value")) == "Present":
                if text not in needs:
                    needs.append(text)
                await self.hub.emit(eid, "alert", {"detail": text, "key": it.get("key")})
            elif text in needs:  # Off / Confirmed: the appliance says it's handled
                needs.remove(text)
        # Kept on the appliance until it says otherwise, so Today's To do can show it.
        await self.hub.update(eid, attrs={"needs": needs})

    async def _stream(self) -> None:
        """Home Connect's live event stream for every appliance; returns to renew access."""
        headers = {"Authorization": f"Bearer {await self._auth()}", "Accept": "text/event-stream"}
        timeout = httpx.Timeout(30, read=150)  # it sends KEEP-ALIVE every 55 s
        async with self._http.stream("GET", f"{API}/api/homeappliances/events", headers=headers,
                                     timeout=timeout) as r:
            if r.status_code == 401:
                self._tok["expires_at"] = 0
                return
            if r.status_code >= 400:
                raise RuntimeError(f"event stream refused (HTTP {r.status_code})")
            ev: dict[str, str] = {}
            async for line in r.aiter_lines():
                if line:
                    field, _, value = line.partition(":")
                    ev[field] = ev.get(field, "") + value.lstrip()
                    continue
                if ev:
                    await self.handle(ev.get("event", ""), ev.get("id", ""), ev.get("data", ""))
                    ev = {}
                if self._tok["expires_at"] - time.time() < 600:
                    return  # reconnect with fresh access

    async def handle(self, event: str, ha: str, data: str) -> None:
        """One message from the event stream."""
        try:
            body = json.loads(data) if data else {}
        except ValueError:
            body = {}
        ha = ha or body.get("haId") or ""
        items = body.get("items") or []
        if event in ("STATUS", "NOTIFY"):
            await self._apply(ha, items)
        elif event == "EVENT":
            await self._alerts(ha, items)
        elif event in ("CONNECTED", "PAIRED"):
            if ha not in self._eids:
                await self._load()
            else:
                await self._read(ha)
        elif event in ("DISCONNECTED", "DEPAIRED") and ha in self._eids:
            await self._offline(ha)

    async def _offline(self, ha: str) -> None:
        """Dishwashers, ovens, washers… drop off Home Connect when switched off: show them as off.
        Something that should always be on (a fridge) disappearing really is offline."""
        eid = self._eids[ha]
        if self._types.get(ha) in ALWAYS_ON:
            await self.hub.update(eid, available=False, attrs={"connected": False})
        else:
            await self.hub.update(eid, available=True, state="off",
                                  attrs={"connected": False, "remaining": None, "progress": None})

    # -- commands -------------------------------------------------------------------------

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        ha = next((h for h, e in self._eids.items() if e == entity_id), None)
        ent = self.hub.get(entity_id)
        if not ha or not ent:
            raise CommandError("That appliance isn't connected")
        base = f"/api/homeappliances/{ha}"
        if command == "start":
            if ent.attrs.get("remoteStart") is False:
                raise CommandError(f"Turn on remote start on the {ent.name} first (the appliance only allows "
                                   "starting from an app after that)")
            sel = await self._api("GET", f"{base}/programs/selected")
            if not sel or not sel.get("key"):
                raise CommandError(f"Choose a program on the {ent.name} first")
            await self._api("PUT", f"{base}/programs/active",
                            {"key": sel["key"], "options": [{"key": o["key"], "value": o["value"]}
                                                            for o in sel.get("options") or [] if "value" in o]})
        elif command in ("pause", "resume"):
            key = f"BSH.Common.Command.{'PauseProgram' if command == 'pause' else 'ResumeProgram'}"
            await self._api("PUT", f"{base}/commands/{key}", {"key": key, "value": True})
        elif command == "stop":
            await self._api("DELETE", f"{base}/programs/active")
        elif command == "turn_off":
            key = "BSH.Common.Setting.PowerState"
            try:
                await self._api("PUT", f"{base}/settings/{key}", {"key": key, "value": "BSH.Common.EnumType.PowerState.Off"})
            except CommandError:  # many can only go to standby
                await self._api("PUT", f"{base}/settings/{key}",
                                {"key": key, "value": "BSH.Common.EnumType.PowerState.Standby"})
        else:
            raise CommandError(f"An appliance can't {command.replace('_', ' ')}")
