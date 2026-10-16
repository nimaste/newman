"""Reolink cameras, doorbells and NVRs - all of them in ONE connection - via
each device's own "Baichuan" push channel (the same library and mechanism as
Home Assistant's Reolink integration), independent of Frigate.

The connection holds one shared login and a list of devices. Newman suggests
devices to add by reading the camera addresses Frigate uses and checking which
of them answer like a Reolink. Per device:
* doorbells -> a `doorbell` device: "pressed" (button) plus detections;
* other cameras -> a `camera` device (motion + Reolink AI: person / vehicle /
  animal / package…); NVRs give one per channel;
* floodlights -> `light` devices.
Each device has its own "Use for": "Button presses & controls only" avoids
duplicate alerts for cameras Frigate already watches.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any

import httpx
from reolink_aio.api import Host

from ..store import slug
from .base import CommandError, Field, Integration, address_of, check_every_field

log = logging.getLogger(__name__)

ALARM_EVENT_CMD_ID = 33  # Baichuan AlarmEvent push (motion / AI / visitor)
AI_TYPES = {"people": "person", "vehicle": "car", "dog_cat": "animal", "package": "package", "face": "face"}
DETECTION_COOLDOWN_S = 30
USE_OPTIONS = ({"value": "everything", "label": "Everything (detections, buttons, controls)"},
               {"value": "buttons", "label": "Button presses & controls only (Frigate handles detections)"})


def devices_from_config(config: dict[str, Any]) -> list[dict[str, Any]]:
    """The device list, including the single-address form older setups used."""
    devs = [d for d in config.get("devices") or [] if d.get("host")]
    if not devs and config.get("host"):
        devs = [{"host": config["host"], "name": config.get("name") or "", "use": config.get("use") or "everything"}]
    return devs


class ReolinkDevice:
    """One camera / doorbell / NVR inside the Reolink connection."""

    def __init__(self, parent: "ReolinkIntegration", spec: dict[str, Any], first: bool) -> None:
        self.p, self.spec, self.first = parent, spec, first
        self.hub = parent.hub
        self.host: Host | None = None
        self.chans: dict[int, dict[str, Any]] = {}
        self.lights: dict[str, int] = {}
        self.status, self.summary = "connecting", spec["host"]

    @property
    def detections_on(self) -> bool:
        return (self.spec.get("use") or "everything") == "everything"

    def local_id(self, what: str, ch: int, single: bool) -> str:
        # The first standalone doorbell keeps "<connection>.doorbell" so existing automations keep working.
        if what == "doorbell" and single and self.first:
            return "doorbell"
        return f"{slug(self.spec['host'])}_{what}_{ch}"

    async def run(self) -> None:
        attempt = 0
        while True:
            again: asyncio.Task | None = None
            try:
                await self.connect()
                attempt = 0
                again = asyncio.create_task(self.check_again())
                while True:
                    await asyncio.sleep(60)
                    await self.host.baichuan.check_subscribe_events()
                    await self.refresh_lights()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.status, self.summary = "error", f"{self.spec['host']}: {e}"
                self.p.refresh_status()
            finally:
                if again:
                    again.cancel()
                    await asyncio.gather(again, return_exceptions=True)
                await self.disconnect()
            attempt += 1
            await asyncio.sleep(min(2 ** (attempt + 2), 300))

    async def connect(self) -> None:
        cfg = self.p.config
        host = Host(self.spec["host"], cfg.get("username") or "admin", cfg["password"],
                    port=int(cfg.get("port") or 80), use_https=bool(cfg.get("https")))
        await host.get_host_data()
        self.host = host
        single = len(host.channels) == 1 and not host.is_nvr
        kinds: list[str] = []
        for ch in host.channels:
            doorbell = host.is_doorbell(ch)
            name = (self.spec.get("name") if single and self.spec.get("name") else None) or host.camera_name(ch)
            eid = self.p.entity_id(self.local_id("doorbell" if doorbell else "camera", ch, single))
            ai = [AI_TYPES[t] for t in host.ai_supported_types(ch) if t in AI_TYPES]
            detections = (["motion"] + ai) if self.detections_on else []
            events = (["pressed"] if doorbell else []) + detections
            self.hub.upsert(eid, kind="doorbell" if doorbell else "camera", name=name, integration=self.p.id)
            await self.hub.update(eid, available=True, state="idle" if doorbell else "online", attrs={
                "model": host.camera_model(ch), "firmware": host.sw_version, "channel": ch, "events": events,
                "host": self.spec["host"], "source": "reolink"})
            self.chans[ch] = {"eid": eid, "doorbell": doorbell, "flags": {}, "last": {}}
            kinds.append("doorbell" if doorbell else "camera")
            if host.supported(ch, "floodLight"):
                leid = self.p.entity_id(f"{slug(self.spec['host'])}_floodlight_{ch}")
                self.hub.upsert(leid, kind="light", name=f"{name} floodlight", integration=self.p.id)
                self.lights[leid] = ch
                self.p.light_owner[leid] = self
                await self.hub.update(leid, available=True, state="on" if host.whiteled_state(ch) else "off",
                                      attrs={"dimmable": False, "model": host.camera_model(ch)})
        host.baichuan.register_callback(f"newman_{self.p.id}_{slug(self.spec['host'])}", self.on_event,
                                        ALARM_EVENT_CMD_ID)
        await host.baichuan.subscribe_events()
        self.status = "connected"
        self.summary = f"{self.spec['host']} · {host.nvr_name if host.is_nvr else ', '.join(kinds)}"
        self.p.refresh_status()

    async def check_again(self) -> None:
        """Every "Check everything every" minutes while connected: the device's names, firmware and
        floodlights read again over its HTTP API, as on connecting (a doorbell's own state is left alone)."""
        while True:
            await asyncio.sleep(self.p.seconds_every("check_minutes"))
            host = self.host
            if not host:
                continue
            try:
                await host.get_host_data()
                await host.get_states()
            except Exception as e:  # noqa: BLE001 - the event subscription carries on either way
                log.info("Reolink %s: checking everything again failed: %s", self.spec["host"], e)
                continue
            single = len(host.channels) == 1 and not host.is_nvr
            for ch, info in self.chans.items():
                name = (self.spec.get("name") if single and self.spec.get("name") else None) or host.camera_name(ch)
                self.hub.upsert(info["eid"], kind="doorbell" if info["doorbell"] else "camera", name=name,
                                integration=self.p.id)
                await self.hub.update(info["eid"], available=True,
                                      attrs={"model": host.camera_model(ch), "firmware": host.sw_version})
            for leid, ch in self.lights.items():
                await self.hub.update(leid, available=True, state="on" if host.whiteled_state(ch) else "off")

    async def disconnect(self) -> None:
        host, self.host = self.host, None
        for info in self.chans.values():
            await self.hub.update(info["eid"], available=False)
        if host:
            try:
                await host.baichuan.unsubscribe_events()
                await host.logout()
            except Exception:
                pass

    async def refresh_lights(self) -> None:
        if not self.lights or not self.host:
            return
        try:
            await self.host.get_states()
        except Exception:
            return
        for leid, ch in self.lights.items():
            await self.hub.update(leid, state="on" if self.host.whiteled_state(ch) else "off")

    def on_event(self) -> None:
        # Called (no args) by reolink_aio for every AlarmEvent push, after it updated its states.
        host = self.host
        if not host:
            return
        for ch, info in self.chans.items():
            flags = {"pressed": info["doorbell"] and bool(host.visitor_detected(ch))}
            if self.detections_on:
                flags["motion"] = bool(host.motion_detected(ch))
                for raw, label in AI_TYPES.items():
                    if host.ai_supported(ch, raw):
                        flags[label] = bool(host.ai_detected(ch, raw))
            rising = [k for k, v in flags.items() if v and not info["flags"].get(k)]
            info["flags"] = flags
            for event in rising:
                asyncio.get_running_loop().create_task(self.fire(ch, info, event))

    async def fire(self, ch: int, info: dict[str, Any], event: str) -> None:
        now = time.time()
        cooldown = float(self.p.config.get("cooldown") or 15) if event == "pressed" else DETECTION_COOLDOWN_S
        if now - info["last"].get(event, 0) < cooldown:
            return
        info["last"][event] = now
        eid = info["eid"]
        if event == "pressed":
            await self.hub.update(eid, state="pressed", attrs={"lastPress": now})
        snapshot_id = await self.snapshot(ch)
        await self.hub.emit(eid, event, {"snapshot_id": snapshot_id} if snapshot_id else {})
        if event == "pressed":
            await asyncio.sleep(5)
            await self.hub.update(eid, state="idle")

    async def snapshot(self, ch: int | None = None) -> str | None:
        if not self.host:
            return None
        if ch is None:
            ch = next(iter(self.chans), 0)
        try:
            jpeg = await asyncio.wait_for(self.host.get_snapshot(ch, "main"), timeout=5)
        except Exception as e:
            log.warning("Reolink snapshot failed: %s", e)
            return None
        if not jpeg:
            return None
        snapshot_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        (self.p.ctx.snapshot_dir / f"{snapshot_id}.jpg").write_bytes(jpeg)
        return snapshot_id


class ReolinkIntegration(Integration):
    type = "reolink"
    name = "Reolink"
    category = "security"
    provides = ("camera", "doorbell", "light")  # kinds of device it can bring in (shown on its catalog card)
    description = ("All your Reolink cameras, doorbells and NVRs in one connection, straight from each device - no "
                   "Frigate needed: doorbell button presses, motion and Reolink's person / vehicle / animal "
                   "detection, snapshots and floodlights. Newman suggests the Reolink cameras it can see.")
    fields = [
        Field("username", "Username (shared by your Reolink devices)", default="admin", required=True),
        Field("password", "Password", "password", required=True),
        Field("devices", "Devices", "devices", dynamic=True,
              help="Each Reolink camera, doorbell or NVR by address, with what Newman should use it for."),
        Field("cooldown", "Ignore repeat doorbell presses for (seconds)", "number", default=15, advanced=True),
        Field("port", "HTTP port", "number", default=80, advanced=True),
        Field("https", "Use HTTPS", "boolean", default=False, advanced=True),
        check_every_field(15, 1, help="Presses and detections arrive as they happen; this also reads every "
                                      "device's names, firmware and floodlights again, in case a change was missed."),
    ]
    # Sub-fields of each entry in "devices" (for the dashboard's list editor).
    device_fields = [
        Field("host", "Address", required=True, placeholder="10.1.1.54"),
        Field("name", "Name", help="Optional - otherwise the camera's own name."),
        Field("use", "Use for", "select", default="everything", options=USE_OPTIONS),
    ]

    @classmethod
    def catalog_json(cls) -> dict[str, Any]:
        out = super().catalog_json()
        for f in out["fields"]:
            if f["key"] == "devices":
                f["itemFields"] = [x.to_json() for x in cls.device_fields]
        return out

    @classmethod
    def migrate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        """Older setups held one address; move it into the device list."""
        if "devices" in config or not config.get("host"):
            return config
        out = {k: v for k, v in config.items() if k not in ("host", "name", "use", "channel")}
        out["devices"] = devices_from_config(config)
        return out

    @classmethod
    def validate(cls, config: dict[str, Any]) -> list[str]:
        errors = super().validate(config)
        if not devices_from_config(config):
            errors.append("Add at least one Reolink device by address")
        return errors

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.devices: list[ReolinkDevice] = []
        self.light_owner: dict[str, ReolinkDevice] = {}
        self.ctx.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def refresh_status(self) -> None:
        ok = [d for d in self.devices if d.status == "connected"]
        bad = [d for d in self.devices if d.status == "error"]
        if bad and not ok:
            self.set_status("error", "; ".join(d.summary for d in bad))
        elif bad:
            self.set_status("error", f"{len(ok)} of {len(self.devices)} connected · problem: "
                                     + "; ".join(d.summary for d in bad))
        elif len(ok) == len(self.devices):
            self.set_status("connected", " · ".join(d.summary for d in ok) if len(ok) == 1
                            else f"{len(ok)} devices")
        else:
            self.set_status("connecting", f"{len(ok)} of {len(self.devices)} connected")

    async def run(self) -> None:
        specs = devices_from_config(self.config)
        self.devices = [ReolinkDevice(self, s, first=i == 0) for i, s in enumerate(specs)]
        self.refresh_status()
        await asyncio.gather(*(d.run() for d in self.devices))

    # -- suggestions: Reolink devices Newman can see ------------------------------------------------

    async def dynamic_options(self) -> dict[str, list[dict[str, Any]]]:
        """Camera addresses Frigate uses that answer like a Reolink (plus the ones already added)."""
        candidates: dict[str, str] = {}
        async with httpx.AsyncClient(timeout=4) as http:
            for inst in self.ctx.instances.values():
                if getattr(inst, "type", "") != "frigate":
                    continue
                try:
                    cfg = (await http.get(f"{inst.url}/api/config")).json()
                except Exception:
                    continue
                def add(url: Any, name: str) -> None:
                    m = re.search(r"://(?:[^@/]*@)?(\d{1,3}(?:\.\d{1,3}){3})", str(url))
                    if m and not m.group(1).startswith("127."):
                        candidates.setdefault(m.group(1), name.replace("_", " ").title())
                for cam, c in (cfg.get("cameras") or {}).items():
                    for i in (c.get("ffmpeg") or {}).get("inputs", []):
                        add(i.get("path"), c.get("friendly_name") or cam)
                # Cameras usually read through Frigate's restream; the real addresses are in go2rtc.
                for stream, srcs in ((cfg.get("go2rtc") or {}).get("streams") or {}).items():
                    for s in srcs if isinstance(srcs, list) else [srcs]:
                        add(s, re.sub(r"_(sub|main|hd|sd|ext)$", "", stream))

            async def is_reolink(ip: str) -> bool:
                try:
                    r = await http.get(f"http://{ip}/api.cgi", params={"cmd": "GetDevInfo"})
                    return '"cmd"' in r.text and "login" in r.text.lower()
                except Exception:
                    return False
            found = await asyncio.gather(*(is_reolink(ip) for ip in candidates))
        added = {d["host"] for d in devices_from_config(self.config)}
        return {"devices": [{"value": ip, "label": f"{name} ({ip})", "group": "Already added" if ip in added else "Found"}
                            for (ip, name), ok in zip(candidates.items(), found) if ok]}

    # -- commands / snapshots ---------------------------------------------------------------------

    async def snapshot(self, entity_id: str | None = None) -> str | None:
        """A snapshot from the camera behind `entity_id` (default: the first device)."""
        for dev in self.devices:
            for ch, info in dev.chans.items():
                if entity_id is None or info["eid"] == entity_id:
                    return await dev.snapshot(ch)
        return None

    def _channel(self, entity_id: str) -> tuple[ReolinkDevice, int] | None:
        for dev in self.devices:
            for ch, info in dev.chans.items():
                if info["eid"] == entity_id:
                    return dev, ch
        return None

    def has_still(self, entity_id: str) -> bool:
        return self._channel(entity_id) is not None

    def video_address(self, entity_id: str) -> str | None:
        """The configured address of the camera / doorbell / NVR behind the device (also before it has
        connected: its id names it)."""
        if entity_id in self.light_owner:
            return None
        found = self._channel(entity_id)
        if found:
            return address_of(found[0].spec.get("host"))
        local = entity_id.split(".", 1)[1] if entity_id.startswith(f"{self.id}.") else ""
        specs = devices_from_config(self.config)
        if local == "doorbell" and specs:
            return address_of(specs[0]["host"])
        for spec in specs:
            if re.fullmatch(re.escape(slug(spec["host"])) + r"_(camera|doorbell)_\d+", local):
                return address_of(spec["host"])
        return None

    async def still(self, entity_id: str, height: int) -> bytes | None:
        found = self._channel(entity_id)
        if not found or not found[0].host:
            return None
        dev, ch = found
        try:
            return await asyncio.wait_for(dev.host.get_snapshot(ch, "sub" if height <= 480 else "main"), timeout=5)
        except Exception:
            return None

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        dev = self.light_owner.get(entity_id)
        if not dev or not dev.host:
            raise CommandError("This Reolink device can't do that")
        if command not in ("turn_on", "turn_off"):
            raise CommandError("The floodlight can only be turned on or off")
        await dev.host.set_whiteled(dev.lights[entity_id], state=command == "turn_on")
        await self.hub.update(entity_id, state="on" if command == "turn_on" else "off")
