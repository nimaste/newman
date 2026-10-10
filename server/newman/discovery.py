"""Network discovery: listen for devices announcing themselves over mDNS /
zeroconf (the way Home Assistant finds them) and offer each one for setup.

Every integration lists the service types its devices announce
(`Integration.zeroconf`) and turns a sighting into a pre-filled setup
(`from_discovery`), so a new integration gets discovery for free.

Multicast doesn't cross Docker's bridge network - the container needs
`network_mode: host` for this to see anything.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from zeroconf import IPVersion, ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from .integrations import REGISTRY

log = logging.getLogger(__name__)


class Discovery:
    def __init__(self) -> None:
        self._types: dict[str, list[type]] = {}
        for cls in REGISTRY.values():
            for t in cls.zeroconf:
                self._types.setdefault(t, []).append(cls)
        self._found: dict[str, dict[str, Any]] = {}  # "<type>:<host>" -> sighting
        self._names: dict[str, set[str]] = {}         # mDNS name -> keys it produced
        self.running = False

    async def run(self, shared: AsyncZeroconf | None = None) -> None:
        """Listen until cancelled. [shared]: the hub's zeroconf instance (also announcing the hub;
        left open here), else one of its own."""
        if not self._types:
            return
        azc = shared or AsyncZeroconf(ip_version=IPVersion.V4Only)
        browser = AsyncServiceBrowser(azc.zeroconf, list(self._types), handlers=[self._on_change])
        self.running = True
        log.info("Discovery listening for %s", ", ".join(self._types))
        try:
            await asyncio.Event().wait()
        finally:
            self.running = False
            await browser.async_cancel()
            if shared is None:
                await azc.async_close()

    def _on_change(self, zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
        if state_change is ServiceStateChange.Removed:
            for key in self._names.pop(name, set()):
                self._found.pop(key, None)
            return
        asyncio.get_running_loop().create_task(self._resolve(zeroconf, service_type, name))

    async def _resolve(self, zc: Zeroconf, service_type: str, name: str) -> None:
        info = AsyncServiceInfo(service_type, name)
        if not await info.async_request(zc, 3000):
            return
        hosts = info.parsed_addresses(IPVersion.V4Only)
        if not hosts:
            return
        props = {(k.decode(errors="replace") if isinstance(k, bytes) else str(k)):
                 (v.decode(errors="replace") if isinstance(v, bytes) else str(v or ""))
                 for k, v in (info.properties or {}).items()}
        for cls in self._types.get(service_type, []):
            try:
                offer = cls.from_discovery(name, hosts[0], props)
            except Exception:
                log.exception("from_discovery failed for %s", cls.type)
                continue
            if not offer:
                continue
            key = f"{cls.type}:{hosts[0]}"
            if key not in self._found:
                log.info("Discovered %s at %s (%s)", offer["title"], hosts[0], cls.name)
            self._found[key] = {"key": key, "type": cls.type, "integrationName": cls.name, "category": cls.category,
                                "available": cls.available, "title": offer["title"], "host": hosts[0],
                                "config": offer.get("config", {}), "note": offer.get("note"), "seenAt": time.time(),
                                "provides": offer.get("provides") or list(cls.provides)}
            self._names.setdefault(name, set()).add(key)

    def found(self, configured: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sightings not already set up (same integration type + same host)."""
        taken = {f"{r['type']}:{r['config'].get('host')}" for r in configured if r["config"].get("host")}
        # HomeKit setups are keyed by accessory id, not address.
        taken |= {f"{r['type']}:{f['host']}" for r in configured for f in self._found.values()
                  if r["type"] == f["type"] and r["config"].get("device_id")
                  and r["config"].get("device_id") == f["config"].get("device_id")}
        # A device with its own native integration often *also* announces HomeKit (the Lutron bridge
        # does); list it once, under the integration that's actually usable.
        native_hosts = {f["host"] for f in self._found.values() if f["available"]}
        native_hosts |= {r["config"].get("host") for r in configured}
        items = [f for k, f in self._found.items()
                 if k not in taken and (f["available"] or f["host"] not in native_hosts)]
        return sorted(items, key=lambda f: (not f["available"], f["integrationName"], f["title"]))
