"""Pi-hole: blocking on / off / paused, today's queries and blocks, clients, blocklist size, updates.

Pi-hole v6 signs in with an *app password* (Settings → Web interface / API → Configure app
password): a separate, revocable key just for apps like Newman - not the password you sign in
with. Newman keeps only that. Sessions expire after a while of not being used; Newman signs in
again when Pi-hole says the session is over, and signs out when the connection stops (Pi-hole
only allows a few sessions at once).

Pi-hole v5 (admin/api.php) works too, with its API token (Settings → API → Show API token).
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from ._dnsfilter import DnsFilterIntegration, Reading, ServerSaid, percent
from .base import Field

VERSION_EVERY_S = 3600


class PiholeIntegration(DnsFilterIntegration):
    type = "pihole"
    name = "Pi-hole"
    product = "Pi-hole"
    description = ("Pi-hole network ad blocker: blocking on / off, pause for a few minutes, today's queries and "
                   "blocks, clients, blocklist size and updates. Uses a Pi-hole app password (a revocable key), "
                   "never your sign-in password.")
    fields = [
        Field("address", "Pi-hole address", required=True, placeholder="http://192.168.1.2",
              help="The address of Pi-hole's web page, without /admin."),
        Field("password", "App password", "password",
              help="Pi-hole v6: Settings → Web interface / API → Configure app password (Expert mode), and paste "
                   "it here - a key you can revoke, not your sign-in password. Pi-hole v5: the API token "
                   "(Settings → API → Show API token). Leave empty if Pi-hole has no password."),
        Field("poll_every", "Check every (seconds)", "number", default=30, advanced=True),
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._secret = str(self.config.get("password") or "")
        self._sid: str | None = None
        self._v5 = False
        self._v5_resumes: float | None = None  # v5 doesn't say when a pause ends: remember it
        self._version_at = 0.0
        self._update: dict[str, Any] = {}

    # -- v6 ------------------------------------------------------------------------------------
    async def _login(self) -> None:
        r = await self._http.post("/api/auth", json={"password": self._secret})
        if r.status_code == 404:
            self._v5 = True
            return
        if r.status_code in (400, 401):
            raise ServerSaid("Pi-hole didn't accept the app password")
        if r.status_code == 429:
            raise ServerSaid("Pi-hole has too many sessions open - try again in a few minutes")
        r.raise_for_status()
        session = r.json().get("session") or {}
        if not session.get("valid"):
            raise ServerSaid("Pi-hole didn't accept the app password")
        self._sid = session.get("sid") or ""  # "" = Pi-hole has no password

    async def _api(self, method: str, path: str, body: Any = None) -> Any:
        for attempt in (0, 1):
            if self._sid is None:
                await self._login()
                if self._v5:
                    raise _UseV5()
            headers = {"X-FTL-SID": self._sid} if self._sid else {}
            r = await self._http.request(method, path, json=body, headers=headers)
            if r.status_code == 401 and attempt == 0:
                self._sid = None  # the session ran out: sign in again
                continue
            if r.status_code == 401:
                raise ServerSaid("Pi-hole didn't accept the app password")
            r.raise_for_status()
            return r.json() if r.content else None
        return None

    async def read(self) -> Reading:
        if self._v5:
            return await self._read_v5()
        try:
            summary = await self._api("GET", "/api/stats/summary")
            blocking = await self._api("GET", "/api/dns/blocking")
        except _UseV5:
            return await self._read_v5()
        q = summary.get("queries") or {}
        attrs: dict[str, Any] = {
            "queriesToday": q.get("total"), "blockedToday": q.get("blocked"),
            "blockedPercent": round(float(q["percent_blocked"]), 1) if q.get("percent_blocked") is not None
            else percent(q.get("blocked"), q.get("total")),
            "clients": (summary.get("clients") or {}).get("active"),
            "rules": (summary.get("gravity") or {}).get("domains_being_blocked"),
        }
        attrs.update(await self._version_v6())
        enabled = blocking["blocking"] == "enabled"
        timer = blocking.get("timer")
        return Reading(enabled, float(timer) if not enabled and timer else None, attrs)

    async def _version_v6(self) -> dict[str, Any]:
        if time.time() - self._version_at < VERSION_EVERY_S:
            return self._update
        self._version_at = time.time()
        try:
            v = (await self._api("GET", "/api/info/version") or {}).get("version") or {}
        except (httpx.HTTPError, ServerSaid, ValueError, AttributeError):
            return self._update
        update = False
        for part in ("core", "web", "ftl"):
            local = ((v.get(part) or {}).get("local") or {}).get("version")
            remote = ((v.get(part) or {}).get("remote") or {}).get("version")
            update = update or bool(local and remote and local != remote)
        docker = v.get("docker") or {}
        if docker.get("local") and docker.get("remote"):
            update = docker["local"] != docker["remote"]  # in Docker, the image is what gets updated
        version = docker.get("local") or ((v.get("core") or {}).get("local") or {}).get("version")
        self._update = {"version": str(version).lstrip("v") if version else None, "updateAvailable": update}
        return self._update

    async def set_protection(self, enabled: bool, minutes: float | None = None) -> None:
        if self._v5:
            return await self._set_v5(enabled, minutes)
        try:
            await self._api("POST", "/api/dns/blocking",
                            {"blocking": enabled, "timer": int(minutes * 60) if minutes and not enabled else None})
        except _UseV5:
            await self._set_v5(enabled, minutes)

    async def close(self) -> None:
        if self._sid:
            await self._http.delete("/api/auth", headers={"X-FTL-SID": self._sid})
            self._sid = None

    # -- v5 ------------------------------------------------------------------------------------
    async def _v5_get(self, query: str) -> Any:
        url = f"/admin/api.php?{query}" + (f"&auth={self._secret}" if self._secret else "")
        r = await self._http.get(url)
        r.raise_for_status()
        data = r.json()
        if data == [] or data == {}:  # v5 answers [] when the token is wrong
            raise ServerSaid("Pi-hole didn't accept the API token")
        return data

    async def _read_v5(self) -> Reading:
        s = await self._v5_get("summaryRaw")
        attrs: dict[str, Any] = {
            "queriesToday": s.get("dns_queries_today"), "blockedToday": s.get("ads_blocked_today"),
            "blockedPercent": round(float(s.get("ads_percentage_today") or 0), 1),
            "clients": s.get("unique_clients"), "rules": s.get("domains_being_blocked"),
        }
        if time.time() - self._version_at >= VERSION_EVERY_S:
            self._version_at = time.time()
            try:
                v = await self._v5_get("versions")
                self._update = {"version": str(v.get("core_current") or "").lstrip("v") or None,
                                "updateAvailable": any(v.get(k) for k in ("core_update", "web_update", "FTL_update"))}
            except (httpx.HTTPError, ServerSaid, ValueError):
                pass
        attrs.update(self._update)
        enabled = s.get("status") == "enabled"
        left = (self._v5_resumes - time.time()) if self._v5_resumes and not enabled else None
        if enabled or (left is not None and left <= 0):
            self._v5_resumes, left = None, None
        return Reading(enabled, left, attrs)

    async def _set_v5(self, enabled: bool, minutes: float | None) -> None:
        if enabled:
            await self._v5_get("enable")
            self._v5_resumes = None
        else:
            await self._v5_get(f"disable={int(minutes * 60)}" if minutes else "disable")
            self._v5_resumes = time.time() + minutes * 60 if minutes else None


class _UseV5(Exception):
    """/api/auth isn't there: this is a Pi-hole v5."""
