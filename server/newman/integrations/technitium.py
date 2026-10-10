"""Technitium DNS Server: blocking on / off / paused, the last day's queries and blocks, clients,
blocked zones and updates.

Signs in with an API token (Administration → Sessions → Create Token in Technitium's web console):
Newman keeps only the token, never a password, and the token can be revoked there at any time.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx

from ._dnsfilter import DnsFilterIntegration, Reading, ServerSaid, percent
from .base import Field

VERSION_EVERY_S = 6 * 3600


def seconds_until(iso: Any) -> float | None:
    """"2026-10-04T15:30:00Z" -> seconds from now (None when unset, unreadable or past)."""
    if not iso:
        return None
    try:
        left = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp() - time.time()
    except ValueError:
        return None
    return left if left > 0 else None


class TechnitiumIntegration(DnsFilterIntegration):
    type = "technitium"
    name = "Technitium DNS"
    product = "Technitium DNS Server"
    description = ("Technitium DNS Server: blocking on / off, pause for a few minutes, the last day's queries and "
                   "blocks, clients, blocked zones and updates. Uses an API token (revocable), never a password.")
    fields = [
        Field("address", "Technitium address", required=True, placeholder="http://192.168.1.2:5380",
              help="The address of Technitium's web console."),
        Field("token", "API token", "password", required=True,
              help="In Technitium's web console: Administration → Sessions → Create Token. Newman keeps only "
                   "the token; revoke it there whenever you like."),
        Field("poll_every", "Check every (seconds)", "number", default=30, advanced=True),
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._token = str(self.config.get("token") or "")
        self._version_at = 0.0
        self._update: dict[str, Any] = {}

    async def _call(self, path: str, **params: Any) -> dict[str, Any]:
        r = await self._http.get(path, params={"token": self._token, **params})
        r.raise_for_status()
        data = r.json()
        if data.get("status") == "invalid-token":
            raise ServerSaid("Technitium didn't accept the API token")
        if data.get("status") != "ok":
            raise ServerSaid(f"Technitium said: {data.get('errorMessage') or data.get('status')}")
        return data.get("response") or {}

    async def read(self) -> Reading:
        settings = await self._call("/api/settings/get")
        stats = (await self._call("/api/dashboard/stats/get", type="LastDay")).get("stats") or {}
        total, blocked = stats.get("totalQueries"), stats.get("totalBlocked")
        attrs: dict[str, Any] = {
            "queriesToday": total, "blockedToday": blocked, "blockedPercent": percent(blocked, total),
            "clients": stats.get("totalClients"),
            "rules": (stats.get("blockedZones") or 0) + (stats.get("blockListZones") or 0)
            if "blockedZones" in stats or "blockListZones" in stats else None,
            "version": settings.get("version"),
        }
        attrs.update(await self._update_info())
        enabled = bool(settings.get("enableBlocking"))
        resumes_in = None if enabled else seconds_until(settings.get("temporaryDisableBlockingTill"))
        return Reading(enabled, resumes_in, attrs)

    async def _update_info(self) -> dict[str, Any]:
        if time.time() - self._version_at < VERSION_EVERY_S:
            return self._update
        self._version_at = time.time()
        try:
            u = await self._call("/api/user/checkForUpdate")
        except (httpx.HTTPError, ServerSaid, ValueError):
            return self._update
        self._update = {"updateAvailable": bool(u.get("updateAvailable")),
                        "latestVersion": u.get("updateVersion") or None}
        return self._update

    async def set_protection(self, enabled: bool, minutes: float | None = None) -> None:
        if not enabled and minutes:
            await self._call("/api/settings/temporaryDisableBlocking", minutes=max(1, round(minutes)))
        else:
            await self._call("/api/settings/set", enableBlocking="true" if enabled else "false")
