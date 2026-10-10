"""A Linux machine's health (Debian, Ubuntu, Raspberry Pi OS…): CPU, load, memory, temperatures,
uptime, OS, and each filesystem (how full) - plus SMART per disk when the host shares it.

Linux has no built-in way to ask a machine how it's doing without signing in to it, so Newman reads
a small, widely used agent running on the host:

- Glances (REST API v4 or v3, port 61208) - the main way: `glances -w --disable-webui` or the
  nicolargo/glances container. Reads sensors (temperatures) and, with root, SMART.
- Prometheus node_exporter (port 9100, /metrics) - if it's already there.

No credentials are kept: both agents are read-only, keep them on your LAN.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from ..store import slug
from ._common import plural
from ._health import THRESHOLD_FIELDS, HealthIntegration, gb, num
from .base import Field

log = logging.getLogger(__name__)

# Filesystems that aren't real storage (snap squashfs, Docker overlays, tmpfs…).
SKIP_FS = {"squashfs", "overlay", "tmpfs", "devtmpfs", "ramfs", "iso9660", "autofs", "nsfs", "fuse.lxcfs",
           "efivarfs", "proc", "sysfs", "cgroup", "cgroup2"}
SKIP_MOUNTS = re.compile(r"^/(snap|var/lib/docker|run|dev|sys|proc|boot/efi)(/|$)")


def uptime_days(text: Any) -> float | None:
    """Glances: "13 days, 2:03:04" / "2:03:04" / seconds."""
    n = num(text)
    if n is not None:
        return round(n / 86400, 1)
    m = re.match(r"\s*(?:(\d+)\s+days?,\s*)?(\d+):(\d+)(?::(\d+))?", str(text or ""))
    if not m:
        return None
    d, h, mi = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    return round(d + (h * 60 + mi) / 1440, 1)


def fs_id(mount: str) -> str:
    return "root" if mount == "/" else slug(mount)


def wanted_fs(fs_type: str, mount: str) -> bool:
    return fs_type not in SKIP_FS and not SKIP_MOUNTS.match(mount or "")


def smart_reading(disk: dict[str, Any]) -> dict[str, Any]:
    """Glances' smart plugin entry for one disk ({"DeviceName", "<id>": {"name", "raw", "value",
    "threshold"}…}) -> power-on hours, temperature, errors and a passed/warning verdict."""
    attrs: dict[str, Any] = {}
    errors = 0
    worn = False
    for a in disk.values():
        if not isinstance(a, dict):
            continue
        name, raw = str(a.get("name") or ""), num(str(a.get("raw", "")).split()[0] if a.get("raw") is not None else None)
        if name == "Power_On_Hours":
            attrs["powerOnHours"] = int(raw) if raw is not None else None
        elif name in ("Temperature_Celsius", "Airflow_Temperature_Cel", "Temperature") and "temperature" not in attrs:
            attrs["temperature"] = raw
        elif name in ("Reallocated_Sector_Ct", "Current_Pending_Sector", "Offline_Uncorrectable",
                      "Media_and_Data_Integrity_Errors"):
            errors += int(raw or 0)
        value, threshold = num(a.get("value")), num(a.get("threshold"))
        if value is not None and threshold and value <= threshold:
            worn = True
    attrs["errors"] = errors
    attrs["smart"] = "failed" if worn else "warning" if errors else "passed"
    return attrs


def parse_metrics(text: str) -> list[tuple[str, dict[str, str], float]]:
    """Prometheus text format -> [(name, labels, value)]."""
    out = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^([a-zA-Z_:][\w:]*)(?:\{(.*)\})?\s+(\S+)', line)
        if not m:
            continue
        labels = dict(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', m.group(2) or ""))
        v = num(m.group(3))
        if v is not None:
            out.append((m.group(1), labels, v))
    return out


class LinuxHostIntegration(HealthIntegration):
    type = "linux_host"
    name = "Linux computer"
    provides = ("server", "disk")
    description = ("A Debian / Ubuntu / Raspberry Pi OS machine's health through Glances (or node_exporter): CPU, "
                   "load, memory, temperatures, uptime and how full each disk is - with alerts when it goes offline, "
                   "runs hot or a disk fills up.")
    fields = [
        Field("address", "Address", required=True, placeholder="http://192.168.1.20:61208",
              help="Glances' web address (port 61208), or node_exporter's (port 9100)."),
        Field("method", "Reads it through", "select", default="glances",
              options=({"value": "glances", "label": "Glances"}, {"value": "node_exporter", "label": "node_exporter"})),
        Field("host_name", "Name", placeholder="Uses the computer's own name"),
        *THRESHOLD_FIELDS,
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._http = httpx.AsyncClient(base_url=str(self.config.get("address") or "").strip().rstrip("/"),
                                       timeout=15, follow_redirects=True)
        self._cpu_prev: tuple[float, float] | None = None  # node_exporter: (idle seconds, total seconds)
        self._glances_api = ""

    async def _get(self, path: str, optional: bool = False) -> Any:
        r = await self._http.get(path)
        if optional and r.status_code >= 400:
            return None
        r.raise_for_status()
        return r.json() if path.startswith("/api") else r.text

    async def poll(self) -> None:
        if self.config.get("method") == "node_exporter":
            await self._poll_node_exporter()
        else:
            await self._poll_glances()

    # -- Glances -----------------------------------------------------------------------------------

    async def _poll_glances(self) -> None:
        if not self._glances_api:  # Glances 4 serves /api/4; Glances 3 (Debian 12, Ubuntu 24.04 packages) /api/3
            for v in ("4", "3"):
                r = await self._http.get(f"/api/{v}/system")
                if r.status_code != 404:
                    r.raise_for_status()
                    self._glances_api = f"/api/{v}"
                    break
            else:
                raise RuntimeError("no Glances API there (is it running with -w?)")
        g = self._glances_api
        system = await self._get(f"{g}/system")
        cpu = await self._get(f"{g}/cpu", True) or {}
        load = await self._get(f"{g}/load", True) or {}
        mem = await self._get(f"{g}/mem", True) or {}
        sensors = await self._get(f"{g}/sensors", True) or []
        fs = await self._get(f"{g}/fs", True) or []
        uptime = await self._get(f"{g}/uptime", True)
        smart = await self._get(f"{g}/smart", True) or {}

        host = str(self.config.get("host_name") or system.get("hostname") or self.display_name)
        temps = {str(s.get("label")): num(s.get("value")) for s in sensors
                 if str(s.get("type", "")).startswith("temperature") and num(s.get("value"))}
        core = [num(s.get("value")) for s in sensors if s.get("type") == "temperature_core" and num(s.get("value"))]
        problems: list[str] = []
        count = 0
        for f in fs:
            mount = str(f.get("mnt_point") or "")
            if not wanted_fs(str(f.get("fs_type") or ""), mount):
                continue
            state = await self.report_disk(f"fs_{fs_id(mount)}", f"{host} {mount}", {
                "usedPercent": num(f.get("percent")), "free": gb(f.get("free")), "size": gb(f.get("size")),
                "mount": mount, "fsType": f.get("fs_type"), "device": f.get("device_name")})
            count += 1
            if state != "ok":
                problems.append(f"{mount} needs a look")
        for dev, d in (smart.items() if isinstance(smart, dict) else []):
            if not isinstance(d, dict):
                continue
            state = await self.report_disk(f"smart_{slug(dev)}", f"{host} {d.get('DeviceName') or dev}",
                                           {**smart_reading(d), "device": dev})
            if state != "ok":
                problems.append(f"{dev} needs a look")
        await self.report_server("host", host, {
            "cpu": num(cpu.get("total")), "load": num(load.get("min1")), "memoryPercent": num(mem.get("percent")),
            "temperature": max(core) if core else (max(temps.values()) if temps else None),
            "temps": temps or None, "uptimeDays": uptime_days(uptime),
            "os": system.get("linux_distro") or system.get("os_name"), "version": system.get("os_version"),
        }, problems)
        self.set_status("connected", f"{host} · {plural(count, 'filesystem')}")

    # -- node_exporter -----------------------------------------------------------------------------

    async def _poll_node_exporter(self) -> None:
        samples = parse_metrics(await self._get("/metrics"))

        def first(name: str, **match: str) -> float | None:
            return next((v for n, lab, v in samples if n == name and all(lab.get(k) == x for k, x in match.items())), None)

        uname = next((lab for n, lab, _ in samples if n == "node_uname_info"), {})
        osinfo = next((lab for n, lab, _ in samples if n == "node_os_info"), {})
        host = str(self.config.get("host_name") or uname.get("nodename") or self.display_name)
        idle = sum(v for n, lab, v in samples if n == "node_cpu_seconds_total" and lab.get("mode") in ("idle", "iowait"))
        total = sum(v for n, _, v in samples if n == "node_cpu_seconds_total")
        cpu = None
        if self._cpu_prev and total > self._cpu_prev[1]:
            cpu = round((1 - (idle - self._cpu_prev[0]) / (total - self._cpu_prev[1])) * 100, 1)
        self._cpu_prev = (idle, total)
        mtotal, mavail = first("node_memory_MemTotal_bytes"), first("node_memory_MemAvailable_bytes")
        boot, now = first("node_boot_time_seconds"), first("node_time_seconds")
        temps: dict[str, float] = {}
        for n, lab, v in samples:
            if n == "node_hwmon_temp_celsius" and v > 0:
                temps[f"{lab.get('chip', '').replace('platform_', '') or 'hwmon'} {lab.get('sensor', '')}".strip()] = v
            elif n == "node_thermal_zone_temp" and v > 0:
                temps[lab.get("type") or f"zone {lab.get('zone')}"] = v
        problems: list[str] = []
        count = 0
        for n, lab, size in samples:
            if n != "node_filesystem_size_bytes" or not size or not wanted_fs(lab.get("fstype", ""), lab.get("mountpoint", "")):
                continue
            mount = lab["mountpoint"]
            avail = first("node_filesystem_avail_bytes", mountpoint=mount) or 0
            free = first("node_filesystem_free_bytes", mountpoint=mount) or avail
            state = await self.report_disk(f"fs_{fs_id(mount)}", f"{host} {mount}", {
                "usedPercent": round((size - free) / size * 100, 1), "free": gb(avail), "size": gb(size),
                "mount": mount, "fsType": lab.get("fstype"), "device": lab.get("device")})
            count += 1
            if state != "ok":
                problems.append(f"{mount} needs a look")
        upgrades = sum(v for n, _, v in samples if n == "apt_upgrades_pending")
        has_apt = any(n == "apt_upgrades_pending" for n, _, _ in samples)
        await self.report_server("host", host, {
            "cpu": cpu, "load": first("node_load1"),
            "memoryPercent": round((1 - mavail / mtotal) * 100, 1) if mtotal and mavail is not None else None,
            "temperature": max(temps.values()) if temps else None, "temps": temps or None,
            "uptimeDays": round((now - boot) / 86400, 1) if boot and now else None,
            "os": osinfo.get("pretty_name") or uname.get("sysname"), "version": uname.get("release"),
            "updateAvailable": bool(upgrades) if has_apt else None, "updates": int(upgrades) if has_apt else None,
        }, problems)
        self.set_status("connected", f"{host} · {plural(count, 'filesystem')}")
