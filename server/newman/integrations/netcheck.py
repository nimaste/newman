"""Network checks: is something on the network answering? Each check is a device - a ping, a TCP
port (host:port) or a web address (status code / a word the page must contain) - that's online or
offline, with its response time, the last change and how much of the last 24 hours it was up.

It only says "went offline" after several failures in a row (and "came back" on the first success
after that), so one dropped ping doesn't wake anyone up. Ping works without special rights
(unprivileged ICMP); where the system doesn't allow that, it falls back to "does the host answer
on a TCP port" (a refused connection still proves the host is there)."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..store import slug
from .base import Field, Integration

log = logging.getLogger(__name__)

DAY_S = 24 * 3600
TYPES = ({"value": "ping", "label": "Ping"}, {"value": "tcp", "label": "TCP port"},
         {"value": "http", "label": "Web address"},
         {"value": "http_insecure", "label": "Web address (any certificate)"})


def split_host_port(target: str, default_port: int | None = None) -> tuple[str, int | None]:
    t = target.strip()
    if "://" in t:
        u = urlsplit(t)
        return u.hostname or "", u.port or default_port
    if t.startswith("[") and "]" in t:  # [ipv6]:port
        host, _, rest = t[1:].partition("]")
        return host, int(rest[1:]) if rest.startswith(":") and rest[1:].isdigit() else default_port
    if t.count(":") == 1:
        host, _, port = t.partition(":")
        return host, int(port) if port.isdigit() else default_port
    return t, default_port


def checks_from_config(config: dict[str, Any]) -> list[dict[str, Any]]:
    out, seen = [], set()
    for c in config.get("checks") or []:
        if not c or not str(c.get("target") or "").strip():
            continue
        name = str(c.get("name") or c["target"]).strip()
        local = slug(name)
        while local in seen:
            local += "_2"
        seen.add(local)
        try:
            every = max(5, int(float(c.get("every") or 60)))
        except (TypeError, ValueError):
            every = 60
        kind = c.get("type") if c.get("type") in {t["value"] for t in TYPES} else "ping"
        out.append({"local": local, "name": name, "type": kind, "target": str(c["target"]).strip(),
                    "expect": str(c.get("expect") or "").strip(), "every": every})
    return out


class NetCheckIntegration(Integration):
    type = "netcheck"
    name = "Network checks"
    category = "servers"
    provides = ("netcheck",)
    description = ("Ping a device, check a port or a web address and know when something goes offline - "
                   "and when it's back. Each check is a device you can use in automations.")
    fields = [
        Field("checks", "Checks", "devices",
              help="Ping: an address (10.1.1.1). TCP port: address:port (nas:445). Web address: the full link; "
                   "“Expect” can be a status code (200) or a word the page must contain.",
              item_fields=(Field("name", "Name", required=True, placeholder="Router"),
                           Field("type", "Check", "select", default="ping", options=TYPES),
                           Field("target", "Address", required=True, placeholder="10.1.1.1"),
                           Field("expect", "Expect (optional)", placeholder="200"),
                           Field("every", "Every (seconds)", "number", default=60))),
        Field("failures", "Offline after this many failures in a row", "number", default=3,
              help="Stops one dropped ping from counting as an outage."),
        Field("timeout", "Give up waiting after (seconds)", "number", default=5, advanced=True),
    ]

    @classmethod
    def validate(cls, config: dict[str, Any]) -> list[str]:
        errors = super().validate(config)
        checks = checks_from_config(config)
        if not checks:
            errors.append("Add at least one check")
        for c in checks:
            if c["type"].startswith("http") and not c["target"].lower().startswith(("http://", "https://")):
                errors.append(f"{c['name']}: a web address starts with http:// or https://")
            if c["type"] == "tcp" and split_host_port(c["target"])[1] is None:
                errors.append(f"{c['name']}: a TCP check needs a port (address:port)")
        return errors

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._checks = checks_from_config(self.config)
        self._history: dict[str, deque] = {c["local"]: deque() for c in self._checks}  # (when, ok)
        self._fails: dict[str, int] = {}
        self._online: dict[str, bool | None] = {}
        self._icmp_ok = True  # False once unprivileged ping turned out not to be allowed here

    @property
    def threshold(self) -> int:
        try:
            return max(1, int(float(self.config.get("failures") or 3)))
        except (TypeError, ValueError):
            return 3

    @property
    def timeout(self) -> float:
        try:
            return max(0.5, float(self.config.get("timeout") or 5))
        except (TypeError, ValueError):
            return 5.0

    # -- probes (each returns ok, latency ms, status code, a few words on what happened) ----------

    async def _ping(self, host: str) -> tuple[bool, float | None]:
        """One ICMP echo without special rights. Raises PermissionError where that isn't allowed."""
        from icmplib import async_ping
        from icmplib.exceptions import SocketPermissionError
        try:
            r = await async_ping(host, count=1, timeout=self.timeout, privileged=False)
        except SocketPermissionError as e:
            raise PermissionError(str(e)) from e
        return r.is_alive, (round(r.avg_rtt, 1) if r.is_alive else None)

    async def _tcp(self, host: str, port: int, refused_ok: bool = False) -> tuple[bool, float | None, str]:
        start = time.perf_counter()
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), self.timeout)
            writer.close()
            return True, round((time.perf_counter() - start) * 1000, 1), ""
        except ConnectionRefusedError:
            if refused_ok:  # the host answered - just not on that port
                return True, round((time.perf_counter() - start) * 1000, 1), ""
            return False, None, f"port {port} refused the connection"
        except (asyncio.TimeoutError, TimeoutError):
            return False, None, "no answer"
        except OSError as e:
            return False, None, e.strerror or str(e)

    async def _http(self, url: str, expect: str, verify: bool) -> tuple[bool, float | None, int | None, str]:
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(verify=verify, timeout=self.timeout, follow_redirects=True,
                                         headers={"User-Agent": "Newman network check"}) as http:
                r = await http.get(url)
        except httpx.TimeoutException:
            return False, None, None, "no answer"
        except httpx.HTTPError as e:
            return False, None, None, str(e) or type(e).__name__
        ms = round((time.perf_counter() - start) * 1000, 1)
        if expect.isdigit():
            ok = r.status_code == int(expect)
            return ok, ms, r.status_code, "" if ok else f"answered {r.status_code}, expected {expect}"
        if r.status_code >= 400:
            return False, ms, r.status_code, f"answered {r.status_code}"
        if expect and expect.lower() not in r.text.lower():
            return False, ms, r.status_code, f"the page doesn't contain “{expect}”"
        return True, ms, r.status_code, ""

    async def probe(self, c: dict[str, Any]) -> dict[str, Any]:
        status = None
        if c["type"].startswith("http"):
            ok, ms, status, why = await self._http(c["target"], c["expect"], verify=c["type"] == "http")
        elif c["type"] == "tcp":
            host, port = split_host_port(c["target"])
            ok, ms, why = await self._tcp(host, port or 80)
        else:
            host, port = split_host_port(c["target"])
            why = ""
            if self._icmp_ok:
                try:
                    ok, ms = await self._ping(host)
                    why = "" if ok else "no answer to ping"
                except PermissionError:
                    log.info("[%s] unprivileged ping isn't allowed here - checking TCP instead", self.display_name)
                    self._icmp_ok = False
            if not self._icmp_ok:
                ok, ms, why = await self._tcp(host, port or 80, refused_ok=True)
                if not ok and port is None:
                    ok, ms, why = await self._tcp(host, 443, refused_ok=True)
        return {"ok": ok, "latency": ms, "status": status, "why": why}

    # -- results ------------------------------------------------------------------------------

    def uptime(self, local: str, now: float) -> float | None:
        h = self._history.setdefault(local, deque())
        while h and h[0][0] < now - DAY_S:
            h.popleft()
        return round(100 * sum(1 for _, ok in h if ok) / len(h), 1) if h else None

    async def record(self, c: dict[str, Any], res: dict[str, Any], now: float | None = None) -> None:
        now = time.time() if now is None else now
        eid = self.entity_id(c["local"])
        self._history.setdefault(c["local"], deque()).append((now, res["ok"]))
        was = self._online.get(c["local"])
        if res["ok"]:
            self._fails[c["local"]] = 0
            online = True
        else:
            self._fails[c["local"]] = self._fails.get(c["local"], 0) + 1
            # Until it has failed enough times in a row it keeps its last state (a brand-new check that
            # fails straight away just starts out offline - nothing to announce).
            online = False if self._fails[c["local"]] >= self.threshold or was is None else was
        attrs: dict[str, Any] = {"latency": res["latency"], "uptime": self.uptime(c["local"], now),
                                 "failures": self._fails[c["local"]], "problem": res["why"] or "",
                                 "protocol": c["type"], "host": c["target"]}
        if res["status"] is not None:
            attrs["statusCode"] = res["status"]
        if was is None or was != online:
            attrs["lastChange"] = datetime.fromtimestamp(now).strftime("%Y-%m-%dT%H:%M:%S")
        self._online[c["local"]] = online
        await self.hub.update(eid, available=True, state="online" if online else "offline", attrs=attrs)
        if was is not None and was != online:
            detail = res["why"] or ("answering again" if online else "not answering")
            await self.hub.emit(eid, "online" if online else "offline", {"detail": detail})
            self.ctx.hub_log("netcheck", f"{c['name']} {'is back online' if online else 'went offline'}", detail, eid, {})

    async def check_once(self, c: dict[str, Any], now: float | None = None) -> None:
        try:
            res = await self.probe(c)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # a check must never take the others down
            res = {"ok": False, "latency": None, "status": None, "why": str(e) or type(e).__name__}
        await self.record(c, res, now)

    async def _loop(self, c: dict[str, Any]) -> None:
        while True:
            started = time.monotonic()
            await self.check_once(c)
            self._status()
            await asyncio.sleep(max(1.0, c["every"] - (time.monotonic() - started)))

    def _status(self) -> None:
        down = [c["name"] for c in self._checks if self._online.get(c["local"]) is False]
        n = len(self._checks)
        self.set_status("connected", (f"{len(down)} of {n} offline: {', '.join(down)}" if down
                                      else f"{n} check{'s' if n != 1 else ''}, all online"))

    def register(self) -> None:
        for c in self._checks:
            self.hub.upsert(self.entity_id(c["local"]), kind="netcheck", name=c["name"], integration=self.id)

    async def run(self) -> None:
        if not self._checks:
            self.set_status("error", "Add at least one check")
            return
        self.register()
        self.set_status("connected", "Checking…")
        await asyncio.gather(*(self._loop(c) for c in self._checks))
