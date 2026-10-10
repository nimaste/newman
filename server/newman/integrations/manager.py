"""Loads integration instances from the database, runs each as its own task,
restarts them when their config changes and routes commands to the owner."""
from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any

from ..state import RESTART_GRACE_S
from ..config import config
from ..store import Store, new_id, slug
from . import REGISTRY
from .base import CommandError, Context, Integration, mask_config, merge_config

log = logging.getLogger(__name__)

# Connection types that became part of another: {old type: (new type, the old type's default name)}.
# The record keeps its id, so its data folder (a sign-in token, say) carries over and nobody has to
# set it up again; a name still at the old default takes the new type's.
FOLDED_TYPES = {"ring_activity": ("ring", "Ring activity")}


class IntegrationManager:
    RECONNECT_WAIT_S = 10.0  # how long a reconnect request waits for "connected" (or an error)
    SETTLE_S = 5.0  # after reconnecting: time to register every device before missing ones count as gone

    def __init__(self, store: Store, ctx: Context) -> None:
        self._store = store
        self._ctx = ctx
        self.instances: dict[str, Integration] = {}
        ctx.instances = self.instances
        self._tasks: dict[str, asyncio.Task] = {}
        self._settling: dict[str, asyncio.Task] = {}  # reconnects waiting to release their held devices

    # -- lifecycle -------------------------------------------------------------------

    def seed_from_env(self) -> None:
        """First boot: turn the phase-1 env settings into integration records."""
        if self._store.integrations():
            return
        if config.mqtt_host:
            self._store.save_integration({"id": "mqtt", "type": "mqtt", "name": "MQTT devices", "config": {
                "host": config.mqtt_host, "port": config.mqtt_port, "username": config.mqtt_username,
                "password": config.mqtt_password, "birth": config.mqtt_birth,
                "discovery_prefix": config.mqtt_discovery_prefix}})
        if config.reolink_host and config.reolink_password:
            self._store.save_integration({"id": "reolink", "type": "reolink", "name": "Reolink", "config": {
                "devices": [{"host": config.reolink_host, "name": config.doorbell_name, "use": "everything"}],
                "username": config.reolink_username, "password": config.reolink_password,
                "port": config.reolink_port, "https": config.reolink_https,
                "cooldown": config.doorbell_cooldown_s}})

    def migrate_types(self) -> None:
        for rec in self._store.integrations():
            if rec["type"] in FOLDED_TYPES:
                new_type, old_default = FOLDED_TYPES[rec["type"]]
                cls = REGISTRY[new_type]
                name = cls.name if rec["name"] == old_default else rec["name"]
                self._store.change_integration_type(rec["id"], new_type, name)
                log.info("Connection %s (%s) is now part of %s", rec["id"], rec["type"], cls.name)

    def start_all(self) -> None:
        self.migrate_types()
        for rec in self._store.integrations():
            self._start(rec)

    def _start(self, rec: dict[str, Any]) -> None:
        cls = REGISTRY.get(rec["type"])
        if not cls:
            log.warning("Unknown integration type %s (%s) - skipped", rec["type"], rec["id"])
            return
        migrated = cls.migrate_config(dict(rec["config"]))
        if migrated != rec["config"]:
            rec["config"] = migrated
            self._store.save_integration(rec)
        inst = cls(rec["id"], rec["name"], rec["config"], self._ctx)
        inst.save_config = lambda cfg, iid=rec["id"]: self._persist(iid, cfg)
        self.instances[rec["id"]] = inst
        if not rec.get("enabled", True):
            inst.set_status("disabled")
            return
        self._tasks[rec["id"]] = asyncio.create_task(self._supervise(inst), name=f"integration:{rec['id']}")

    def _persist(self, iid: str, cfg: dict[str, Any]) -> None:
        rec = next((r for r in self._store.integrations() if r["id"] == iid), None)
        if rec:
            rec["config"] = dict(cfg)
            self._store.save_integration(rec)

    async def _supervise(self, inst: Integration) -> None:
        while True:
            try:
                await inst.run()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("Integration %s crashed; restarting in 30s", inst.id)
                inst.set_status("error", str(e))
                await asyncio.sleep(30)

    async def _stop(self, instance_id: str, grace: float | None = None, keep_devices: bool = False) -> None:
        """`grace`: see Hub.remove_integration (0 = gone now, RESTART_GRACE_S = a restart, None = shutdown).
        `keep_devices`: leave its devices on the hub (a reconnect - see reconnect())."""
        if task := self._tasks.pop(instance_id, None):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.instances.pop(instance_id, None)
        if not keep_devices:
            self._ctx.hub.remove_integration(instance_id, grace=grace)

    async def stop_all(self) -> None:
        for task in self._settling.values():
            task.cancel()
        for iid in list(self._tasks):
            await self._stop(iid, grace=None)

    # -- API --------------------------------------------------------------------------

    def catalog(self) -> list[dict[str, Any]]:
        return [cls.catalog_json() for cls in REGISTRY.values()]

    def list(self) -> list[dict[str, Any]]:
        out = []
        for rec in self._store.integrations():
            cls = REGISTRY.get(rec["type"])
            inst = self.instances.get(rec["id"])
            out.append({
                "id": rec["id"], "type": rec["type"], "name": rec["name"], "enabled": rec.get("enabled", True),
                "category": cls.category if cls else "other",
                "hasAlerts": bool(inst and inst.has_alerts(None)),
                "status": inst.status if inst else "error", "detail": inst.detail if inst else "Unknown type",
                "entityCount": sum(1 for e in self._ctx.hub.all() if e.integration == rec["id"]),
                "config": mask_config(cls, rec["config"]) if cls else {},
            })
        return out

    async def create(self, type_: str, name: str | None, cfg: dict[str, Any]) -> dict[str, Any]:
        cls = REGISTRY.get(type_)
        if not cls:
            raise ValueError(f"Unknown integration {type_}")
        if not cls.available:
            raise ValueError(f"{cls.name} isn't available yet")
        cfg = merge_config(cls, {f.key: f.default for f in cls.fields if f.default is not None}, cfg)
        if errors := cls.validate(cfg):
            raise ValueError("; ".join(errors))
        existing = {r["id"] for r in self._store.integrations()}
        iid = slug(type_) if slug(type_) not in existing else new_id(slug(type_))
        rec = {"id": iid, "type": type_, "name": name or cls.name, "enabled": True, "config": cfg}
        self._store.save_integration(rec)
        self._start(rec)
        return next(i for i in self.list() if i["id"] == iid)

    async def update(self, iid: str, *, name: str | None = None, cfg: dict[str, Any] | None = None,
                     enabled: bool | None = None) -> dict[str, Any]:
        rec = next((r for r in self._store.integrations() if r["id"] == iid), None)
        if not rec:
            raise KeyError(iid)
        cls = REGISTRY[rec["type"]]
        if cfg is not None:
            if inst := self.instances.get(iid):
                cfg = inst.translate_config(dict(cfg))
            rec["config"] = merge_config(cls, rec["config"], cfg)
            if errors := cls.validate(rec["config"]):
                raise ValueError("; ".join(errors))
        if name:
            rec["name"] = name
        if enabled is not None:
            rec["enabled"] = enabled
        self._store.save_integration(rec)
        await self._stop(iid, grace=RESTART_GRACE_S)
        self._start(rec)
        return next(i for i in self.list() if i["id"] == iid)

    async def reconnect(self, iid: str) -> dict[str, Any]:
        """Start a connection again with its saved settings (nothing changes). Its devices stay as they
        are meanwhile; any it no longer provides once it's back are shown unavailable, not removed
        (automations refer to them). Answers once it has connected or failed (or after RECONNECT_WAIT_S)."""
        rec = next((r for r in self._store.integrations() if r["id"] == iid), None)
        if not rec:
            raise KeyError(iid)
        if not rec.get("enabled", True):
            raise ValueError(f"{rec['name']} is paused")
        held = self._ctx.hub.hold(iid)
        await self._stop(iid, keep_devices=True)
        self._start(rec)
        inst = self.instances.get(iid)
        if old := self._settling.pop(iid, None):
            old.cancel()
        self._settling[iid] = asyncio.create_task(self._settle(iid, inst, held), name=f"reconnect:{iid}")
        loop = asyncio.get_running_loop()
        until = loop.time() + self.RECONNECT_WAIT_S
        while inst and inst.status == "connecting" and loop.time() < until:
            await asyncio.sleep(0.2)
        return next(i for i in self.list() if i["id"] == iid)

    async def _settle(self, iid: str, inst: Integration | None, held: list[str]) -> None:
        """Once a reconnected connection is up (and has had a moment to register everything), or after
        the restart grace, its held devices that didn't come back are shown unavailable."""
        loop = asyncio.get_running_loop()
        until = loop.time() + RESTART_GRACE_S
        while inst and inst.status != "connected" and loop.time() < until:
            await asyncio.sleep(1)
        await asyncio.sleep(self.SETTLE_S)
        self._settling.pop(iid, None)
        gone = await self._ctx.hub.release(held)
        if gone:
            log.info("%s no longer provides %d device(s) - shown unavailable: %s", iid, len(gone), ", ".join(gone[:10]))

    async def remove(self, iid: str) -> bool:
        await self._stop(iid, grace=0.0)
        # Drop anything the integration kept (e.g. Lutron's pairing certificates).
        shutil.rmtree(self._ctx.data_dir / "integrations" / iid, ignore_errors=True)
        return self._store.remove_integration(iid)

    # -- commands ------------------------------------------------------------------------

    def owner(self, entity_id: str) -> Integration:
        ent = self._ctx.hub.get(entity_id)
        if not ent:
            raise CommandError("Unknown device")
        inst = self.instances.get(ent.integration)
        if not inst:
            raise CommandError("Its integration isn't running")
        return inst

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        await self.owner(entity_id).command(entity_id, command, value)
