"""Proxmox VE: each node's health (CPU, load, memory, uptime, version, pending updates), its storage
and physical disks (SMART health, wear), and every VM and LXC container - running or not, CPU and
memory, with start / shut down / force stop / reboot.

Uses the Proxmox REST API (https://<host>:8006/api2/json) with an API token - only the token is kept.
Proxmox's API doesn't report temperatures, so nodes have none here (add the node as a Linux computer
through Glances for those).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from ..store import slug
from ._common import plural
from ._health import THRESHOLD_FIELDS, HealthIntegration, gb, num
from .base import CommandError, Field

log = logging.getLogger(__name__)

VM_STATES = {"running": "running", "paused": "paused", "suspended": "paused"}  # anything else: stopped
COMMANDS = ("start", "shutdown", "stop", "reboot")
TYPE_NAMES = {"qemu": "VM", "lxc": "LXC container"}


def pct(part: Any, whole: Any) -> float | None:
    p, w = num(part), num(whole)
    return round(p / w * 100, 1) if p is not None and w else None


def smart_of(health: Any) -> str | None:
    h = str(health or "").upper()
    return "passed" if h in ("PASSED", "OK") else "failed" if h == "FAILED" else None


class ProxmoxIntegration(HealthIntegration):
    type = "proxmox"
    name = "Proxmox VE"
    provides = ("server", "disk", "vm")
    description = ("Proxmox VE nodes, storage and disks, and every VM and LXC container: running or not, CPU and memory, "
                   "start / shut down / reboot - with alerts when a node goes offline or a disk fails or fills up.")
    fields = [
        Field("address", "Proxmox address", required=True, placeholder="https://192.168.1.30:8006"),
        Field("token_id", "API token ID", required=True, placeholder="newman@pve!monitor",
              help="Datacenter → Permissions → API Tokens → Add (user@realm!name). Give it the PVEAuditor role on / "
                   "to watch; add PVEVMUser (or VM.PowerMgmt) to start and stop guests."),
        Field("token_secret", "API token secret", "password", required=True),
        Field("verify_ssl", "Check the HTTPS certificate", "boolean", default=False, advanced=True,
              help="Leave off for Proxmox's own self-signed certificate."),
        Field("guests", "Include VMs and containers", "boolean", default=True),
        *THRESHOLD_FIELDS,
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        base = str(self.config.get("address") or "").strip().rstrip("/")
        token = f"PVEAPIToken={self.config.get('token_id')}={self.config.get('token_secret')}"
        self._http = httpx.AsyncClient(base_url=f"{base}/api2/json", verify=bool(self.config.get("verify_ssl")),
                                       timeout=20, headers={"Authorization": token})
        self._guests: dict[str, tuple[str, str, int]] = {}  # entity id -> (node, qemu|lxc, vmid)

    async def _get(self, path: str, optional: bool = False, **params: Any) -> Any:
        r = await self._http.get(path, params=params or None)
        if r.status_code == 401:
            raise RuntimeError("Proxmox didn't accept the API token")
        if optional and r.status_code >= 400:  # e.g. the token may not read disks or updates
            return None
        r.raise_for_status()
        return r.json().get("data")

    async def poll(self) -> None:
        nodes = await self._get("/nodes") or []
        online = 0
        for n in nodes:
            node = str(n.get("node"))
            local = f"node_{slug(node)}"
            if n.get("status") != "online":
                self._servers[local] = node
                eid = self.entity_id(local)
                if self.hub.get(eid):
                    await self.server_offline(local, "the cluster reports it offline")
                else:  # already down when Newman started: show it, but it isn't news
                    self.hub.upsert(eid, kind="server", name=node, integration=self.id)
                    await self.hub.update(eid, state="offline", attrs={"problem": "Not responding"})
                continue
            online += 1
            await self._node(node, local, n)
        if self.config.get("guests", True):
            await self._vms()
        self.set_status("connected", f"{plural(len(nodes), 'node')} · {online} online"
                        + (f" · {plural(len(self._guests), 'guest')}" if self._guests else ""))

    async def _node(self, node: str, local: str, summary: dict[str, Any]) -> None:
        st = await self._get(f"/nodes/{node}/status", True) or {}
        problems: list[str] = []
        for s in await self._get(f"/nodes/{node}/storage", True) or []:
            if not s.get("enabled", 1) or not s.get("total"):
                continue
            inactive = not s.get("active", 1)
            state = await self.report_disk(
                f"storage_{slug(node)}_{slug(s['storage'])}", f"{node} {s['storage']}",
                {"usedPercent": pct(s.get("used"), s.get("total")), "free": gb(s.get("avail")), "size": gb(s.get("total")),
                 "fsType": s.get("type")}, problems=["Not active"] if inactive else None)
            if state != "ok":
                problems.append(f"{s['storage']} needs a look")
        for d in await self._get(f"/nodes/{node}/disks/list", True) or []:
            dev = str(d.get("devpath") or "")
            wear = num(d.get("wearout"))
            smart = smart_of(d.get("health"))
            if smart == "passed" and wear is not None and wear <= 10:
                smart = "warning"  # SSD nearly worn out (wearout = % life left)
            state = await self.report_disk(
                f"disk_{slug(node)}_{slug(dev.rsplit('/', 1)[-1])}", f"{node} {d.get('model') or dev}",
                {"smart": smart, "size": gb(d.get("size")), "lifeLeft": wear, "diskType": d.get("type"), "device": dev})
            if state != "ok":
                problems.append(f"{dev} needs a look")
        updates = await self._get(f"/nodes/{node}/apt/update", True)
        mem = st.get("memory") or {}
        load = (st.get("loadavg") or [None])[0]
        await self.report_server(local, node, {
            "cpu": round((num(st.get("cpu", summary.get("cpu"))) or 0) * 100, 1),
            "load": num(load),
            "memoryPercent": pct(mem.get("used", summary.get("mem")), mem.get("total", summary.get("maxmem"))),
            "uptimeDays": round((num(st.get("uptime", summary.get("uptime"))) or 0) / 86400, 1),
            "os": "Proxmox VE", "version": st.get("pveversion"), "kernel": st.get("kversion"),
            "updateAvailable": bool(updates) if updates is not None else None,
            "updates": len(updates) if updates is not None else None,
        }, problems)

    async def _vms(self) -> None:
        for g in await self._get("/cluster/resources", type="vm") or []:
            if g.get("template") or g.get("type") not in TYPE_NAMES:
                continue
            vmid = int(g["vmid"])
            eid = self.entity_id(f"vm_{vmid}")
            self._guests[eid] = (str(g.get("node")), g["type"], vmid)
            self.child(eid)
            self.hub.upsert(eid, kind="vm", name=str(g.get("name") or f"{TYPE_NAMES[g['type']]} {vmid}"),
                            integration=self.id)
            running = g.get("status") == "running"
            await self.hub.update(eid, available=True, state=VM_STATES.get(str(g.get("status")), "stopped"), attrs={
                "cpu": round((num(g.get("cpu")) or 0) * 100, 1) if running else 0,
                "memoryPercent": pct(g.get("mem"), g.get("maxmem")) if running else 0,
                "memory": round((num(g.get("mem")) or 0) / 1048576) if running else 0,
                "uptimeDays": round((num(g.get("uptime")) or 0) / 86400, 1),
                "vmType": TYPE_NAMES[g["type"]], "vmid": vmid, "node": g.get("node")})

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        if entity_id not in self._guests:
            raise CommandError("Only Proxmox VMs and containers take commands")
        if command not in COMMANDS:
            raise CommandError(f"A virtual machine can't {command.replace('_', ' ')}")
        node, kind_, vmid = self._guests[entity_id]
        r = await self._http.post(f"/nodes/{node}/{kind_}/{vmid}/status/{command}", timeout=30)
        if r.status_code in (401, 403):
            raise CommandError("Newman's Proxmox token may only watch - give it the PVEVMUser role (VM.PowerMgmt) "
                               "to start and stop guests")
        if r.status_code >= 400:
            try:
                msg = (r.json() or {}).get("message") or r.reason_phrase
            except ValueError:
                msg = r.reason_phrase
            raise CommandError(f"Proxmox said no: {msg}")
        if command == "start" or command == "reboot":
            await self.hub.update(entity_id, state="running")
