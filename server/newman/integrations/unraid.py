"""Unraid server health through the official Unraid API (GraphQL; built into Unraid 7.x, the
"Unraid Connect" plugin on 6.12): CPU, memory, uptime, the array (started or not, parity check
running and how far, the last check's result) and every array / parity / cache disk (used, free,
temperature, errors, spun down).

Design: the server entity carries the array - arrayState, parityCheck (running / paused / idle),
parityProgress, parityErrors and lastParityCheck are its properties, and parity_started /
parity_finished / array_stopped its events - since an Unraid box has exactly one array. Each disk
is a `disk` entity. Disk details come from Unraid's own disk table, which never wakes a sleeping
disk; SMART status (which may) is opt-in.

Needs an API key (Settings → Management Access → API Keys, role "viewer" is enough). Only the key
is kept.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from ..store import slug
from ._common import plural
from ._health import THRESHOLD_FIELDS, HealthIntegration, num
from .base import Field

log = logging.getLogger(__name__)

QUERY = """query Newman {
  info { os { hostname distro release uptime kernel } versions { core { unraid } } }
  metrics { cpu { percentTotal } memory { total used percentTotal } }
  array {
    state
    capacity { kilobytes { free used total } }
    parityCheckStatus { status progress errors running paused correcting date duration }
    parities { ...D } disks { ...D } caches { ...D }
  }
}
fragment D on ArrayDisk { idx name device size status temp numErrors fsSize fsFree fsUsed type warning critical isSpinning }"""
DOCKER_QUERY = "query NewmanDocker { docker { containers { state } } }"
SMART_QUERY = "query NewmanDisks { disks { device smartStatus temperature } }"

# Unraid's disk states: anything not here (and not an empty slot) means the disk is out of the array.
OK_STATUS = {"DISK_OK", "DISK_NEW"}
EMPTY_SLOT = {"DISK_NP", "DISK_NP_DSBL"}
DISK_PROBLEM = {"DISK_DSBL": "disabled", "DISK_INVALID": "invalid", "DISK_WRONG": "wrong disk",
                "DISK_NP_MISSING": "missing", "DISK_DSBL_NEW": "disabled (new)"}


def uptime_days(v: Any) -> float | None:
    """Unraid gives the boot time (ISO); older builds give seconds."""
    n = num(v)
    if n is not None:
        return round(n / 86400, 1)
    try:
        boot = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    if boot.tzinfo is None:
        boot = boot.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - boot).total_seconds() / 86400, 1)


def disk_reading(d: dict[str, Any]) -> tuple[dict[str, Any], bool, bool, list[str]]:
    """An Unraid ArrayDisk -> (attrs, failed, standby, problems). Sizes are in KB."""
    fs_size, fs_free = num(d.get("fsSize")), num(d.get("fsFree"))
    size = fs_size or num(d.get("size"))  # parity disks have no filesystem
    used = num(d.get("fsUsed"))
    attrs: dict[str, Any] = {
        "size": round(size * 1024 / 1e9, 1) if size else None,
        "free": round(fs_free * 1024 / 1e9, 1) if fs_free is not None else None,
        "usedPercent": round(used / fs_size * 100, 1) if used is not None and fs_size else None,
        "temperature": num(d.get("temp")) or None,  # 0 / null while spun down
        "errors": int(num(d.get("numErrors")) or 0),
        "diskType": str(d.get("type") or "").lower(), "device": d.get("device"),
    }
    status = str(d.get("status") or "")
    failed = status not in OK_STATUS and status not in EMPTY_SLOT
    problems = [f"Disk {DISK_PROBLEM.get(status, status.lower())}"] if failed else []
    return attrs, failed, d.get("isSpinning") is False, problems


class UnraidIntegration(HealthIntegration):
    type = "unraid"
    name = "Unraid"
    provides = ("server", "disk")
    description = ("Your Unraid server's health: CPU, memory, uptime, the array and parity checks, and every disk "
                   "(how full, temperature, errors) - with alerts when a disk fails, fills up or runs hot.")
    fields = [
        Field("address", "Unraid address", required=True, placeholder="http://192.168.1.10",
              help="The address you open the Unraid web page at (include the port if it isn't 80/443)."),
        Field("api_key", "API key", "password", required=True,
              help="In Unraid: Settings → Management Access → API Keys → Create, role “viewer”. Only the key is kept."),
        Field("verify_ssl", "Check the HTTPS certificate", "boolean", default=False, advanced=True,
              help="Leave off for Unraid's own self-signed certificate."),
        Field("smart", "Read SMART status", "boolean", default=False, advanced=True,
              help="Asks every disk for its SMART health - this can wake disks that have spun down."),
        *THRESHOLD_FIELDS,
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._http = httpx.AsyncClient(base_url=str(self.config.get("address") or "").strip().rstrip("/"),
                                       verify=bool(self.config.get("verify_ssl")), follow_redirects=True,
                                       timeout=20, headers={"x-api-key": str(self.config.get("api_key") or "")})
        self._parity_running: bool | None = None
        self._array_state: str | None = None

    async def gql(self, query: str) -> dict[str, Any]:
        r = await self._http.post("/graphql", json={"query": query})
        if r.status_code in (401, 403):
            raise RuntimeError("Unraid didn't accept the API key")
        r.raise_for_status()
        body = r.json()
        errors = body.get("errors") or []
        if errors and not body.get("data"):
            msg = errors[0].get("message", "error")
            if (errors[0].get("extensions") or {}).get("code") == "UNAUTHENTICATED":
                raise RuntimeError(f"Unraid didn't accept the API key ({msg})")
            raise RuntimeError(msg)
        return body.get("data") or {}

    async def _optional(self, query: str) -> dict[str, Any]:
        try:
            return await self.gql(query)
        except (RuntimeError, httpx.HTTPError, ValueError) as e:
            log.debug("Unraid optional query: %s", e)
            return {}

    async def poll(self) -> None:
        data = await self.gql(QUERY)
        info, metrics, array = data.get("info") or {}, data.get("metrics") or {}, data.get("array") or {}
        os_ = info.get("os") or {}
        name = os_.get("hostname") or self.display_name
        docker = ((await self._optional(DOCKER_QUERY)).get("docker") or {}).get("containers")
        smart = {}
        if self.config.get("smart"):
            smart = {d.get("device"): d for d in (await self._optional(SMART_QUERY)).get("disks") or []}

        # Disks first: the server's state takes their problems into account.
        problems: list[str] = []
        count = 0
        for group in ("parities", "disks", "caches"):
            for d in array.get(group) or []:
                if str(d.get("status") or "") in EMPTY_SLOT:
                    continue
                attrs, failed, standby, dprobs = disk_reading(d)
                s = smart.get(d.get("device"))
                if s:
                    attrs["smart"] = "passed" if str(s.get("smartStatus")).upper() == "OK" else None
                    attrs["temperature"] = attrs["temperature"] or num(s.get("temperature"))
                disk_name = str(d.get("name") or d.get("device") or f"disk{d.get('idx')}")
                label = disk_name.capitalize() if disk_name.islower() else disk_name
                state = await self.report_disk(f"disk_{slug(disk_name)}", f"{name} {label}", attrs, failed=failed,
                                               standby=standby, problems=dprobs)
                count += 1
                if state == "failed":
                    problems.append(f"{label} failed")
                elif state == "warning":
                    problems.append(f"{label} needs a look")

        astate = str(array.get("state") or "").upper()
        pc = array.get("parityCheckStatus") or {}
        pstatus = str(pc.get("status") or "").lower()
        running = bool(pc.get("running")) or pstatus in ("running", "paused")
        parity = "paused" if pc.get("paused") or pstatus == "paused" else "running" if running else "idle"
        if astate and astate != "STARTED":
            problems.append("Array stopped" if astate == "STOPPED" else f"Array: {astate.replace('_', ' ').lower()}")
        cap = (array.get("capacity") or {}).get("kilobytes") or {}
        total, used = num(cap.get("total")), num(cap.get("used"))
        mem, cpu = metrics.get("memory") or {}, metrics.get("cpu") or {}
        attrs = {
            "cpu": round(num(cpu.get("percentTotal")), 1) if num(cpu.get("percentTotal")) is not None else None,
            "memoryPercent": round(num(mem.get("percentTotal")), 1) if num(mem.get("percentTotal")) is not None else None,
            "uptimeDays": uptime_days(os_.get("uptime")),
            "os": "Unraid", "version": ((info.get("versions") or {}).get("core") or {}).get("unraid") or os_.get("release"),
            "kernel": os_.get("kernel"),
            "arrayState": astate.lower() or None,
            "arrayUsedPercent": round(used / total * 100, 1) if total and used is not None else None,
            "parityCheck": parity,
            "parityProgress": round(num(pc.get("progress")) or 0, 1) if running else None,
            "parityErrors": int(num(pc.get("errors")) or 0) if pstatus and pstatus != "never_run" else None,
            "lastParityCheck": pc.get("date"),
            "parityResult": pstatus or None,
        }
        if isinstance(docker, list):
            attrs["containersRunning"] = sum(1 for c in docker if str(c.get("state")).upper() == "RUNNING")
            attrs["containersTotal"] = len(docker)
        await self.report_server("server", name, attrs, problems)

        eid = self.entity_id("server")
        if self._parity_running is not None and running != self._parity_running:
            if running:
                await self.hub.emit(eid, "parity_started", {"detail": "parity check started"})
            else:
                errs = attrs["parityErrors"] or 0
                await self.hub.emit(eid, "parity_finished", {
                    "detail": f"{pstatus or 'finished'}, {plural(errs, 'error')}", "errors": errs, "result": pstatus})
        if self._array_state == "STARTED" and astate != "STARTED":
            await self.hub.emit(eid, "array_stopped", {"detail": astate.replace("_", " ").lower()})
        self._parity_running, self._array_state = running, astate
        detail = f"{plural(count, 'disk')} · array {astate.lower() or 'unknown'}"
        if running:
            detail += f" · parity check {attrs['parityProgress']:g}%"
        self.set_status("connected", detail)
