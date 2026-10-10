"""AdGuard Home: protection on / off / paused, today's queries and blocks, rules and updates.

AdGuard Home's API has no tokens or API keys - only its web sign-in (HTTP basic auth). So this is
one of the few connections that has to keep a password: make a separate AdGuard Home user for
Newman (AdGuardHome.yaml `users:`), so the main admin password is never stored here. It is kept
in Newman's data folder and never shown again (masked like every other secret).
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from ._dnsfilter import DnsFilterIntegration, Reading, ServerSaid, percent
from .base import Field

# How often to ask whether a new version is out (AdGuard Home checks GitHub itself; this reads its answer).
VERSION_EVERY_S = 6 * 3600


class AdGuardIntegration(DnsFilterIntegration):
    type = "adguard"
    name = "AdGuard Home"
    product = "AdGuard Home"
    description = ("AdGuard Home network ad blocker: protection on / off, pause for a few minutes, today's queries "
                   "and blocks, rule count and updates. AdGuard Home has no API tokens, so Newman has to keep a "
                   "password: create a separate AdGuard Home user just for Newman.")
    fields = [
        Field("address", "AdGuard Home address", required=True, placeholder="http://192.168.1.2:3000",
              help="The address of AdGuard Home's web page."),
        Field("username", "User", required=True,
              help="AdGuard Home has no API tokens or keys - its API only accepts a user and password. Add a "
                   "user just for Newman (users: in AdGuardHome.yaml) rather than using your own."),
        Field("password", "Password", "password", required=True,
              help="Kept in Newman's data folder and never shown again."),
        Field("poll_every", "Check every (seconds)", "number", default=30, advanced=True),
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._auth = httpx.BasicAuth(str(self.config.get("username") or ""), str(self.config.get("password") or ""))
        self._version_at = 0.0
        self._update: dict[str, Any] = {}

    async def _call(self, method: str, path: str, body: Any = None) -> Any:
        r = await self._http.request(method, path, json=body, auth=self._auth)
        if r.status_code in (401, 403):
            raise ServerSaid("AdGuard Home didn't accept the user and password")
        r.raise_for_status()
        return r.json() if r.content and "json" in r.headers.get("content-type", "") else None

    async def read(self) -> Reading:
        status = await self._call("GET", "/control/status")
        stats = await self._call("GET", "/control/stats") or {}
        enabled = bool(status["protection_enabled"])
        resumes_in = None
        if not enabled:
            ms = status.get("protection_disabled_duration") or 0
            resumes_in = ms / 1000 if ms > 0 else None
        queries = stats.get("num_dns_queries")
        blocked = None
        if queries is not None:
            blocked = sum(int(stats.get(k) or 0) for k in
                          ("num_blocked_filtering", "num_replaced_safebrowsing", "num_replaced_parental"))
        attrs: dict[str, Any] = {
            "queriesToday": queries, "blockedToday": blocked, "blockedPercent": percent(blocked, queries),
            "clients": len(stats["top_clients"]) if isinstance(stats.get("top_clients"), list) else None,
            "version": str(status.get("version") or "").lstrip("v") or None,
        }
        try:
            filt = await self._call("GET", "/control/filtering/status") or {}
            attrs["rules"] = sum(int(f.get("rules_count") or 0) for f in filt.get("filters") or [] if f.get("enabled")) \
                + len([r for r in filt.get("user_rules") or [] if r and not str(r).startswith(("!", "#"))])
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        attrs.update(await self._update_info())
        return Reading(enabled, resumes_in, attrs)

    async def _update_info(self) -> dict[str, Any]:
        if time.time() - self._version_at < VERSION_EVERY_S:
            return self._update
        self._version_at = time.time()
        try:
            v = await self._call("POST", "/control/version.json", {"recheck_now": False}) or {}
        except (httpx.HTTPError, ServerSaid, ValueError):
            return self._update  # can't tell (offline, updates switched off): keep what we knew
        if v.get("disabled"):
            self._update = {}
        else:
            new = str(v.get("new_version") or "").lstrip("v")
            self._update = {"updateAvailable": bool(new), "latestVersion": new or None}
        return self._update

    async def set_protection(self, enabled: bool, minutes: float | None = None) -> None:
        body: dict[str, Any] = {"enabled": enabled}
        if not enabled and minutes:
            body["duration"] = int(minutes * 60_000)
        await self._call("POST", "/control/protection", body)
