"""Docker containers (Unraid, Synology, any Docker host): running or not, health, CPU and memory;
start, stop and restart; "crashed" and "unhealthy" for automations.

Newman talks to Docker's API - best through a socket proxy that only lets through what Newman needs
(read-only unless start/stop/restart are allowed), or directly through /var/run/docker.sock when
that is mounted into Newman. Changes arrive on Docker's own event stream the moment they happen;
CPU and memory are read every minute.

Containers come and go: a container appears in Newman once it has existed for YOUNG_S, and never if
it is a throwaway one - started with `docker run --rm` (Docker deletes it when it stops), or carrying
the label `newman.ignore=true` - so build and test runs don't show up as devices that go "offline". One Docker no longer has at all (deleted - not merely
stopped) is removed from Newman once it has been missing for GONE_GRACE_S (riding out a Docker restart).
When this connection starts with Docker answering, devices for containers already gone are removed
straight away. A gone container something still uses (an automation, a dashboard or phone layout tile,
a phone widget) is kept instead, unavailable and labelled "Removed", so nothing that uses it breaks. While
Docker can't be reached nothing is removed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx

from ..store import slug
from ._common import plural
from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

STATES = {"running": "running", "paused": "paused", "restarting": "restarting"}  # anything else: stopped
# A container that dies within this long of a stop/kill was stopped on purpose, not crashed.
STOP_GRACE_S = 30
# A container shows up in Newman once it has existed this long (seconds since Docker created it).
YOUNG_S = 120
# A device whose container Docker no longer has is removed after it's been missing this long.
GONE_GRACE_S = 600
# Docker's container list is read again this often (gone containers, young ones growing up).
SWEEP_S = 60


def health_of(status: str) -> str | None:
    """"Up 3 hours (healthy)" -> "healthy"; "(health: starting)" -> "starting"; none -> None."""
    m = re.search(r"\((?:health: )?(healthy|unhealthy|starting)\)", status or "")
    return m.group(1) if m else None


def usage(stats: dict[str, Any]) -> dict[str, Any]:
    """Docker's stats -> {"cpu": %, "memory": MB, "memoryPercent": %} (the way `docker stats` counts them)."""
    out: dict[str, Any] = {}
    cpu, pre = stats.get("cpu_stats") or {}, stats.get("precpu_stats") or {}
    try:
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sys_delta = cpu["system_cpu_usage"] - pre["system_cpu_usage"]
        cpus = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or []) or 1
        if sys_delta > 0:
            out["cpu"] = round(cpu_delta / sys_delta * cpus * 100, 1)
    except (KeyError, TypeError):
        pass
    mem = stats.get("memory_stats") or {}
    if mem.get("usage") is not None:
        extra = mem.get("stats") or {}
        used = mem["usage"] - (extra.get("inactive_file") or extra.get("total_inactive_file") or 0)
        out["memory"] = round(used / 1048576)
        if mem.get("limit"):
            out["memoryPercent"] = round(used / mem["limit"] * 100, 1)
    return out


class DockerIntegration(Integration):
    type = "docker"
    name = "Docker"
    category = "servers"
    provides = ("container",)
    description = ("Docker containers on Unraid, Synology or any Docker host: running or stopped, health, CPU and "
                   "memory, start / stop / restart, and alerts when one crashes or turns unhealthy.")
    fields = [
        Field("address", "Docker address", required=True, placeholder="http://192.168.1.10:2375",
              help="Safest: run a Docker socket proxy (e.g. lscr.io/linuxserver/socket-proxy with CONTAINERS=1, "
                   "EVENTS=1 - plus POST=1, ALLOW_START=1, ALLOW_STOP=1, ALLOW_RESTARTS=1 if Newman may start and "
                   "stop containers) and enter its address. Or mount /var/run/docker.sock into Newman and enter "
                   "unix:///var/run/docker.sock."),
        Field("hide_containers", "Leave out these containers", "multiselect", dynamic=True,
              help="Ticked containers stay out of Newman. Every other container appears, including ones added "
                   "to Docker later."),
        Field("stats_every", "Read CPU and memory every (seconds)", "number", default=60, advanced=True),
    ]
    # Older Newman kept the containers to INCLUDE ("containers": empty = all), so a container added later
    # never appeared. Still accepted from older clients; turned into hide_containers (everything Docker
    # has minus those) as soon as Docker's list can be read - until then the include list still applies.
    extra_config_keys = ("containers",)

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        addr = str(self.config.get("address") or "").strip()
        if addr.startswith("unix://"):
            self._http = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=addr[len("unix://"):]),
                                           base_url="http://docker", timeout=20)
        else:
            self._http = httpx.AsyncClient(base_url=addr.rstrip("/"), timeout=20)
        self._ids: dict[str, str] = {}        # container name -> current container id
        self._stopping: dict[str, float] = {}  # name -> when a stop/kill was seen
        self._names: list[str] | None = None  # every container Docker has, as last listed
        self._now = time.time                 # the clock (tests move it)
        self._missing: dict[str, float] = {}  # device id -> when Docker was first seen not to have it
        self._kept: set[str] = set()          # gone but still used: kept as "Removed" (logged once)
        self._young_until: float | None = None  # when the next too-young container is old enough
        self._wake = asyncio.Event()          # a young container appeared: re-time the sweep
        self._throwaway: dict[str, bool] = {}  # container id -> started with --rm (asked Docker once)

    # -- which containers ---------------------------------------------------------------------------

    @classmethod
    def migrate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        """An older include list that needs no container list to convert: empty (= everything) hides
        nothing; None (replaced by a newer choice, see translate_config) goes. Others wait for Docker."""
        out = dict(config)
        if "containers" in out and not out["containers"]:
            if out.pop("containers") is not None:
                out.setdefault("hide_containers", [])
        return out

    def _convert_filter(self, names: list[str]) -> None:
        """Turn an older include list into the hide list: what Docker has now minus what was included
        (all ticked -> nothing hidden), so containers added later show up. Saved once."""
        included = self.config.get("containers")
        if not (isinstance(included, list) and included):
            return
        new = {k: v for k, v in self.config.items() if k != "containers"}
        new["hide_containers"] = [n for n in names if n not in set(included)]
        self.config = new
        log.info("[%s] Container choice converted: leaving out %s", self.display_name,
                 ", ".join(new["hide_containers"]) or "nothing")
        if self.save_config:
            self.save_config(self.config)

    def translate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """An older client sends an include list ("containers"): converted with Docker's current list when
        known, else kept to convert on the next connect. A hide list sent now replaces an older include
        list still waiting to be converted."""
        out = dict(config)
        if "containers" in out:
            included = out.pop("containers")
            if "hide_containers" in out:
                out["containers"] = None  # both sent: the newer setting wins
            elif not included:
                out["hide_containers"], out["containers"] = [], None
            elif self._names is not None:
                out["hide_containers"] = [n for n in self._names if n not in set(included)]
                out["containers"] = None
            else:
                out["containers"] = list(included)
        elif "hide_containers" in out:
            out["containers"] = None
        return out

    def _wanted(self, name: str) -> bool:
        included = self.config.get("containers")  # an older include list not converted yet (Docker unreachable)
        if isinstance(included, list) and included:
            return name in included
        return name not in (self.config.get("hide_containers") or [])

    def _eid(self, name: str) -> str:
        return self.entity_id(slug(name))

    async def _get(self, path: str, **params: Any) -> Any:
        r = await self._http.get(path, params=params or None)
        if r.status_code == 403:
            raise RuntimeError("the socket proxy doesn't allow reading containers (set CONTAINERS=1)")
        r.raise_for_status()
        return r.json()

    async def dynamic_options(self) -> dict[str, list[dict[str, Any]]]:
        names = sorted(c["Names"][0].lstrip("/") for c in await self._get("/containers/json", all="1") if c.get("Names"))
        opts = [{"value": n, "label": n} for n in names]
        return {"hide_containers": opts, "containers": opts}  # "containers": older clients' include list

    async def run(self) -> None:
        attempt = 0
        while True:
            try:
                await self._list()
                attempt = 0
                await asyncio.gather(self._events(), self._stats_loop(), self._sweep_loop())
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Docker: %s", e)
                self.set_status("error", f"Can't reach Docker at {self.config.get('address')}: {e}")
                for eid in [self._eid(n) for n in self._ids]:
                    await self.hub.update(eid, available=False)
                await self.backoff_sleep(attempt + 2)
                attempt += 1

    async def _list(self) -> None:
        containers = await self._get("/containers/json", all="1")  # raises while Docker can't be reached
        first = self._names is None
        self._names = sorted(n for n in ((c.get("Names") or ["/"])[0].lstrip("/") for c in containers) if n)
        self._convert_filter(self._names)
        now = self._now()
        seen = set()
        young: list[float] = []
        for c in containers:
            name = (c.get("Names") or ["/"])[0].lstrip("/")
            if not name or not self._wanted(name):
                continue
            created = c.get("Created")
            if (isinstance(created, (int, float)) and now - created < YOUNG_S
                    and not self.hub.get(self._eid(name))):
                young.append(created + YOUNG_S)  # too new to show yet (a recreated one keeps its device)
                continue
            if not self.hub.get(self._eid(name)) and await self._is_throwaway(c):
                continue  # a build or test run: never a device (one already shown stays)
            seen.add(name)
            self._ids[name] = c["Id"]
            eid = self._eid(name)
            ent = self.hub.upsert(eid, kind="container", name=name, integration=self.id)
            attrs = {"health": health_of(c.get("Status", "")), "image": c.get("Image", ""), "containerId": c["Id"][:12]}
            if ent.attrs.get("removed"):
                attrs["removed"] = False  # back in Docker (recreated under the same name)
            await self.hub.update(eid, available=True, state=STATES.get(c.get("State"), "stopped"), attrs=attrs)
        for gone in set(self._ids) - seen:  # no longer listed (left out, or deleted)
            await self.hub.update(self._eid(gone), available=False)
        self._young_until = min(young) if young else None
        if young:
            self._wake.set()
        # Docker answered: devices for containers it doesn't have at all. On the first list (this
        # connection starting) they go straight away - unless Docker listed nothing, which may just be
        # Docker starting up; then, like any later list, after GONE_GRACE_S.
        await self._forget_gone(now, at_once=first and bool(containers))
        self._status()

    async def _forget_gone(self, now: float, at_once: bool) -> None:
        there = {self._eid(n) for n in self._names or []}
        mine = [e for e in self.hub.all() if e.integration == self.id]
        for eid in [i for i in self._missing if i in there or not self.hub.get(i)]:
            self._missing.pop(eid, None)
            self._kept.discard(eid)
        for ent in mine:
            if ent.id in there:
                continue
            since = self._missing.setdefault(ent.id, now)
            if not at_once and now - since < GONE_GRACE_S:
                continue
            self._ids = {n: cid for n, cid in self._ids.items() if self._eid(n) != ent.id}
            used = self.in_use(ent.id)
            if not used:
                self.hub.remove(ent.id)
                self._missing.pop(ent.id, None)
                log.info("[%s] %s is gone from Docker - removed", self.display_name, ent.name)
                continue
            await self.hub.update(ent.id, available=False, attrs={"removed": True})
            if ent.id not in self._kept:
                self._kept.add(ent.id)
                log.info("[%s] %s is gone from Docker but still used (%s) - kept as Removed",
                         self.display_name, ent.name, ", ".join(used))

    async def _is_throwaway(self, c: dict[str, Any]) -> bool:
        """Started with `--rm`, or labelled newman.ignore=true. The list doesn't say --rm, so the
        container is asked once (an answer Docker can't give counts as "keep it")."""
        if str((c.get("Labels") or {}).get("newman.ignore", "")).lower() in ("true", "1", "yes"):
            return True
        cid = c.get("Id") or ""
        if cid not in self._throwaway:
            try:
                info = await self._get(f"/containers/{cid}/json")
                self._throwaway[cid] = bool((info.get("HostConfig") or {}).get("AutoRemove"))
            except Exception:
                return False  # ask again next time
        return self._throwaway[cid]

    def in_use(self, entity_id: str) -> list[str]:
        """What still uses a device (automations, dashboards, phone layouts, widgets) - empty: nothing."""
        check = getattr(self.ctx, "in_use", None)
        return list(check(entity_id)) if check else []

    async def _sweep_loop(self) -> None:
        """Reads Docker's list again every SWEEP_S - sooner when a young container is about to be old
        enough to show."""
        while True:
            wait = SWEEP_S
            if self._young_until is not None:
                wait = max(1.0, min(wait, self._young_until - self._now() + 1))
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), wait)
                continue  # a young container appeared meanwhile: work out the wait again
            except asyncio.TimeoutError:
                pass
            await self._list()

    def _status(self) -> None:
        ents = [self.hub.get(self._eid(n)) for n in self._ids]
        running = sum(1 for e in ents if e and e.state == "running")
        self.set_status("connected", f"{plural(len(ents), 'container')} · {running} running")

    async def _events(self) -> None:
        filters = json.dumps({"type": ["container"]})
        async with self._http.stream("GET", "/events", params={"filters": filters},
                                     timeout=httpx.Timeout(20, read=None)) as r:
            if r.status_code == 403:
                raise RuntimeError("the socket proxy doesn't allow events (set EVENTS=1)")
            r.raise_for_status()
            async for line in r.aiter_lines():
                if line.strip():
                    try:
                        await self.handle(json.loads(line))
                    except ValueError:
                        continue
        raise RuntimeError("Docker closed the event stream")

    async def handle(self, ev: dict[str, Any]) -> None:
        """One Docker event."""
        action = str(ev.get("Action") or ev.get("status") or "")
        actor = ev.get("Actor") or {}
        attrs = actor.get("Attributes") or {}
        name = attrs.get("name") or ""
        if not name or not self._wanted(name):
            return
        eid = self._eid(name)
        if action in ("create", "start") and (name not in self._ids or not self.hub.get(eid)):
            await self._list()  # a new container (or one recreated by an update)
        ent = self.hub.get(eid)
        if not ent:
            return  # left out, or too young to show yet
        if actor.get("ID"):
            self._ids[name] = actor["ID"]
        if action in ("stop", "kill"):
            self._stopping[name] = time.time()
        elif action == "start":
            self._stopping.pop(name, None)  # Docker reports "stop" after "die": don't carry it into this run
            await self.hub.update(eid, available=True, state="running", attrs={"containerId": actor.get("ID", "")[:12]})
        elif action == "restart":
            await self.hub.update(eid, state="running")
        elif action == "die":
            on_purpose = time.time() - self._stopping.pop(name, 0) < STOP_GRACE_S
            code = str(attrs.get("exitCode") or "0")
            await self.hub.update(eid, state="stopped", attrs={"cpu": 0, "memory": 0, "memoryPercent": 0})
            if not on_purpose and code != "0":
                await self.hub.emit(eid, "crashed", {"detail": f"exit code {code}", "exitCode": code})
        elif action == "pause":
            await self.hub.update(eid, state="paused")
        elif action == "unpause":
            await self.hub.update(eid, state="running")
        elif action.startswith("health_status"):
            health = action.split(":", 1)[-1].strip()
            before = ent.attrs.get("health")
            await self.hub.update(eid, attrs={"health": health})
            if health == "unhealthy" and before != "unhealthy":
                await self.hub.emit(eid, "unhealthy", {})
        elif action == "destroy":
            await self.hub.update(eid, available=False, state="stopped")
        else:
            return
        self._status()

    async def _stats_loop(self) -> None:
        every = max(15, int(self.config.get("stats_every") or 60))
        sem = asyncio.Semaphore(6)

        async def one(name: str, cid: str) -> None:
            async with sem:
                try:
                    r = await self._http.get(f"/containers/{cid}/stats", params={"stream": "false"})
                    if r.status_code == 200:
                        await self.hub.update(self._eid(name), attrs=usage(r.json()))
                except httpx.HTTPError as e:
                    log.debug("Docker stats %s: %s", name, e)

        while True:
            running = [(n, cid) for n, cid in self._ids.items()
                       if (e := self.hub.get(self._eid(n))) and e.state == "running"]
            await asyncio.gather(*(one(n, cid) for n, cid in running))
            await asyncio.sleep(every)

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        name = next((n for n in self._ids if self._eid(n) == entity_id), None)
        if not name:
            raise CommandError("That container isn't known to Docker any more")
        if command not in ("start", "stop", "restart"):
            raise CommandError(f"A container can't {command.replace('_', ' ')}")
        if command in ("stop", "restart"):
            self._stopping[name] = time.time()  # its "die" is on purpose
        r = await self._http.post(f"/containers/{self._ids[name]}/{command}", timeout=60)
        self._post_result(r, name)

    # -- for other parts of Newman (by container name, hidden or not) ---------------------------------

    def knows(self, name: str) -> bool:
        """Whether Docker had this container when last listed (True before the first list)."""
        return self._names is None or name in self._names

    async def container_info(self, name: str) -> dict[str, Any] | None:
        """Docker's inspect of one container by name (its "State": Running, StartedAt, Health…), or None."""
        r = await self._http.get(f"/containers/{name}/json")
        if r.status_code == 404:
            return None
        if r.status_code == 403:
            raise RuntimeError("the socket proxy doesn't allow reading containers (set CONTAINERS=1)")
        r.raise_for_status()
        return r.json()

    async def restart_container(self, name: str, wait: int = 10) -> None:
        """Restarts a container by name (`wait` seconds to stop before it's killed). CommandError when
        Docker or the socket proxy says no."""
        self._stopping[name] = time.time()  # its "die" is on purpose
        r = await self._http.post(f"/containers/{name}/restart", params={"t": str(wait)}, timeout=wait + 60)
        self._post_result(r, name)

    @staticmethod
    def _post_result(r: httpx.Response, name: str) -> None:
        if r.status_code == 403:
            raise CommandError("Newman's Docker connection is read-only - allow start, stop and restart on the "
                               "socket proxy (POST=1, ALLOW_START=1, ALLOW_STOP=1, ALLOW_RESTARTS=1)")
        if r.status_code == 404:
            raise CommandError(f"Docker doesn't have {name} any more")
        if r.status_code >= 400:
            try:
                msg = r.json().get("message")
            except ValueError:
                msg = None
            raise CommandError(msg or f"Docker said no (HTTP {r.status_code})")
