"""What the server-health connections (Unraid, Linux hosts, Proxmox) share: a polling loop, and
turning raw readings into the `server` and `disk` kinds' states and events.

Each connection only reads its source and calls report_server / report_disk with plain numbers;
the thresholds (how hot is too hot, how long is "sustained") and the event rules live here once:

- server: "online", "warning" (something needs a look - attrs.problem says what) or "offline".
  Events: offline / online (on the change), too_hot (hysteresis 5 °C), high_cpu / high_memory (above
  the limit for the whole "sustained" time, then not again until it drops back below).
- disk: "ok", "warning", "failed" or "standby" (spun down). Events: failed, smart_warning,
  almost_full (hysteresis 2 %), too_hot (hysteresis 3 °C).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from .base import Field, Integration

log = logging.getLogger(__name__)

# Thresholds every health connection offers (tucked under "More settings").
THRESHOLD_FIELDS = [
    Field("poll_every", "Check every (seconds)", "number", default=60, advanced=True),
    Field("cpu_high", "CPU counts as high above (%)", "number", default=90, advanced=True),
    Field("memory_high", "Memory counts as high above (%)", "number", default=90, advanced=True),
    Field("sustained_min", "…for at least (minutes)", "number", default=10, advanced=True,
          help="High CPU or memory only counts (and alerts) once it has lasted this long."),
    Field("temp_high", "CPU too hot above (°C)", "number", default=85, advanced=True),
    Field("disk_full", "Disk almost full above (%)", "number", default=90, advanced=True),
    Field("disk_hot", "Disk too hot above (°C)", "number", default=55, advanced=True),
]


def num(v: Any) -> float | None:
    """A reading as a float (APIs send big numbers as strings); None when missing or not a number."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN


def gb(n_bytes: Any) -> float | None:
    v = num(n_bytes)
    return round(v / 1e9, 1) if v is not None else None


def clean(attrs: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in attrs.items() if v is not None}


class HealthIntegration(Integration):
    """Polls `poll()` every poll_every seconds. Subclasses implement poll() (raise on "can't reach")
    and name their servers in `self._servers` (local id -> name) via report_server."""
    category = "servers"
    now: Callable[[], float] = staticmethod(time.monotonic)  # tests move the clock

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._servers: dict[str, str] = {}   # local id -> name
        self._children: set[str] = set()     # disks / VMs: unavailable while their host is unreachable
        self._since: dict[tuple[str, str], float] = {}  # (eid, what) -> when it went over the limit
        self._latched: set[tuple[str, str]] = set()     # (eid, event) already sent, until it clears
        self._misses = 0

    def limit(self, key: str) -> float:
        default = next(f.default for f in THRESHOLD_FIELDS if f.key == key)
        return num(self.config.get(key)) or float(default)

    async def poll(self) -> None:
        raise NotImplementedError

    async def run(self) -> None:
        while True:
            try:
                await self.poll()
                self._misses = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - any failure = can't read it right now
                await self.unreachable(e)
            await asyncio.sleep(max(15, int(num(self.config.get("poll_every")) or 60)))

    async def unreachable(self, e: Exception) -> None:
        """The source didn't answer. One miss could be a blip; the second marks everything offline."""
        self._misses += 1
        log.warning("%s: %s", self.display_name, e)
        self.set_status("error", f"Can't reach {self.config.get('address') or self.name}: {e}")
        if self._misses >= 2:
            for local in list(self._servers):
                await self.server_offline(local, str(e))
            for eid in self._children:
                await self.hub.update(eid, available=False)

    # -- servers -----------------------------------------------------------------------------------

    async def server_offline(self, local: str, detail: str = "") -> None:
        eid = self.entity_id(local)
        ent = self.hub.get(eid)
        if ent and ent.state != "offline":
            await self.hub.update(eid, state="offline", attrs={"problem": "Not responding"})
            await self.hub.emit(eid, "offline", {"detail": detail or "not responding"})

    def _sustained(self, eid: str, what: str, value: float | None, high: float) -> bool:
        """True once `value` has been at or above `high` for the whole sustained time."""
        key = (eid, what)
        if value is None or value < high:
            self._since.pop(key, None)
            self._latched.discard(key)
            return False
        start = self._since.setdefault(key, self.now())
        return self.now() - start >= self.limit("sustained_min") * 60

    async def _once(self, eid: str, event: str, on: bool, clear: bool, detail: str, quiet: bool = False) -> None:
        """Emit `event` when `on` becomes true; re-arm only once `clear` (hysteresis). `quiet` = the first
        reading since Newman started: remember it without alerting (no repeat alerts on every restart)."""
        key = (eid, event)
        if on and key not in self._latched:
            self._latched.add(key)
            if not quiet:
                await self.hub.emit(eid, event, {"detail": detail})
        elif clear:
            self._latched.discard(key)

    async def report_server(self, local: str, name: str, attrs: dict[str, Any],
                            problems: list[str] | None = None) -> None:
        """One reading of a host. attrs use the server kind's names (cpu, memoryPercent, temperature…);
        `problems` = things the source itself flags (a stopped array, a failed disk…)."""
        eid = self.entity_id(local)
        self._servers[local] = name
        before = self.hub.get(eid)
        self.hub.upsert(eid, kind="server", name=name, integration=self.id)
        problems = list(problems or [])
        temp, cpu, mem = num(attrs.get("temperature")), num(attrs.get("cpu")), num(attrs.get("memoryPercent"))
        t_high = self.limit("temp_high")
        if temp is not None and temp >= t_high:
            problems.append(f"Running hot ({temp:g} °C)")
        cpu_high = self._sustained(eid, "high_cpu", cpu, self.limit("cpu_high"))
        mem_high = self._sustained(eid, "high_memory", mem, self.limit("memory_high"))
        if cpu_high:
            problems.append(f"CPU high ({cpu:g}%)")
        if mem_high:
            problems.append(f"Memory high ({mem:g}%)")
        state = "warning" if problems else "online"
        await self.hub.update(eid, available=True, state=state,
                              attrs={**clean(attrs), "problem": "; ".join(problems) or "none"})
        if before is not None and before.state == "offline":
            await self.hub.emit(eid, "online", {"detail": "back online"})
        if temp is not None:
            await self._once(eid, "too_hot", temp >= t_high, temp < t_high - 5, f"{temp:g} °C",
                             before is None or before.state is None)
        if cpu_high:
            await self._once(eid, "high_cpu", True, False, f"{cpu:g}%")
        if mem_high:
            await self._once(eid, "high_memory", True, False, f"{mem:g}%")

    # -- disks -------------------------------------------------------------------------------------

    async def report_disk(self, local: str, name: str, attrs: dict[str, Any], *, failed: bool = False,
                          standby: bool = False, problems: list[str] | None = None) -> str:
        """One reading of a disk / filesystem / storage pool. attrs: usedPercent, free (GB), size (GB),
        temperature, smart ("passed" | "warning" | "failed"), errors, powerOnHours. Returns its state."""
        eid = self.entity_id(local)
        self._children.add(eid)
        self.hub.upsert(eid, kind="disk", name=name, integration=self.id)
        problems = list(problems or [])
        smart = attrs.get("smart")
        used, temp, errors = num(attrs.get("usedPercent")), num(attrs.get("temperature")), num(attrs.get("errors"))
        full, hot = self.limit("disk_full"), self.limit("disk_hot")
        failed = failed or smart == "failed"
        if smart == "failed":
            problems.append("SMART test failed")
        elif smart == "warning":
            problems.append("SMART warning")
        if errors:
            problems.append(f"{errors:g} errors")
        if used is not None and used >= full:
            problems.append(f"{used:g}% full")
        if temp is not None and temp >= hot:
            problems.append(f"Running hot ({temp:g} °C)")
        state = "failed" if failed else "warning" if problems else "standby" if standby else "ok"
        first = (before := self.hub.get(eid)) is None or before.state is None
        await self.hub.update(eid, available=True, state=state,
                              attrs={**clean(attrs), "problem": "; ".join(problems) or "none"})
        if state == "failed" and not first and before.state != "failed":
            await self.hub.emit(eid, "failed", {"detail": "; ".join(problems) or "disabled"})
        await self._once(eid, "smart_warning", smart == "warning", smart in ("passed", None), "SMART warning", first)
        if used is not None:
            await self._once(eid, "almost_full", used >= full, used < full - 2, f"{used:g}% full", first)
        if temp is not None:
            await self._once(eid, "too_hot", temp >= hot, temp < hot - 3, f"{temp:g} °C", first)
        return state

    def child(self, eid: str) -> None:
        """Mark a non-disk entity (a VM) as belonging to the host: unavailable when it's unreachable."""
        self._children.add(eid)
