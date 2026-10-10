"""Frigate NVR: cameras and zones as Newman devices, ALERTS as triggers.

Frigate sorts everything it sees into review items of two severities:
alerts (what you care about, recorded) and detections (everything else,
often not kept). Newman works from alerts only - one Activity entry, one
trigger per object type, one snapshot and one video per alert:

* each camera -> a `camera` device; its events fire when an alert involves
  that object ("person detected"), with a snapshot attached;
* each zone -> a `zone` device, Occupied/Clear with live per-object counts
  ("2 people in Driveway"); its events are "person entered".

Frigate publishes over MQTT in its own format (not Home Assistant's discovery
format), so this reads its topics directly; the camera/zone list and MQTT
details come from Frigate's own /api/config.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any

import aiomqtt
import httpx

from ..kinds import DETECTABLE
from .base import Field, Integration, address_of, is_local_address

log = logging.getLogger(__name__)


def nice(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").strip().title()


FILTERS = ("cameras", "zones")  # each has a hide_<name> setting (and an older include list, see extra_config_keys)
REFRESH_S = 600  # re-read Frigate's config this often, in case its restart messages were missed
# Frigate writes recordings in segments of about 10 s, so an alert's last seconds (and the lead-out
# open_clip adds) reach its clip a little after the alert ends; fetched sooner, the clip would be
# cached cut short.
CLIP_SETTLE_S = 30


def enabled_cameras(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {k: v for k, v in (cfg.get("cameras") or {}).items() if isinstance(v, dict) and v.get("enabled") is not False}


def everything(cfg: dict[str, Any]) -> dict[str, list[str]]:
    """The cameras and zones Frigate offers (what the filters choose from)."""
    cams = enabled_cameras(cfg)
    return {"cameras": list(cams), "zones": [z for c in cams.values() for z in (c.get("zones") or {})]}


def config_hash(cfg: dict[str, Any]) -> str:
    """Fingerprint of what Newman's devices are built from: cameras (name, on/off, tracked objects, live
    streams, inputs), their zones, and go2rtc's streams."""
    cams = {}
    for name, c in (cfg.get("cameras") or {}).items():
        c = c if isinstance(c, dict) else {}
        cams[name] = {
            "enabled": c.get("enabled"), "name": c.get("friendly_name"),
            "track": (c.get("objects") or {}).get("track"), "live": c.get("live"),
            "inputs": [i.get("path") for i in (c.get("ffmpeg") or {}).get("inputs") or [] if isinstance(i, dict)],
            "zones": {z: {"name": (zv or {}).get("friendly_name"), "objects": (zv or {}).get("objects")}
                      for z, zv in (c.get("zones") or {}).items()}}
    rel = {"cameras": cams, "streams": (cfg.get("go2rtc") or {}).get("streams")}
    return hashlib.sha256(json.dumps(rel, sort_keys=True, default=str).encode()).hexdigest()


class FrigateIntegration(Integration):
    type = "frigate"
    name = "Frigate"
    category = "cameras"
    provides = ("camera", "zone")  # kinds of device it can bring in (shown on its catalog card)
    description = ("Your Frigate cameras and zones as devices: detections (person, car, dog…) can start automations "
                   "with a snapshot, and zones show live counts (“2 people in Driveway”). Only needs Frigate's "
                   "address - its MQTT settings are read from Frigate itself.")
    fields = [
        Field("url", "Frigate address", required=True, placeholder="http://10.1.1.2:5000", default="http://10.1.1.2:5000"),
        Field("severity", "Use", "select", default="alerts",
              options=({"value": "alerts", "label": "Alerts only"},
                       {"value": "detections", "label": "Detections only"},
                       {"value": "both", "label": "Alerts and detections"}),
              help="Which Frigate review items show in Activity and can start automations. "
                   "Detections are often not recorded, so their video may be missing."),
        Field("hide_cameras", "Leave out these cameras", "multiselect", dynamic=True,
              help="Ticked cameras stay out of Newman. Every other camera appears, including ones added to "
                   "Frigate later."),
        Field("hide_zones", "Leave out these zones", "multiselect", dynamic=True,
              help="Ticked zones stay out of Newman. Every other zone appears, including ones added to Frigate later."),
        Field("mqtt_host", "MQTT broker (optional)", help="Leave empty to use the broker Frigate is configured with."),
        Field("mqtt_port", "MQTT port (optional)", "number"),
        Field("mqtt_username", "MQTT username (optional)"),
        Field("mqtt_password", "MQTT password (optional)", "password"),
    ]
    # Older Newman kept the cameras/zones to INCLUDE ("cameras", "zones": empty = all), so a camera added
    # to Frigate later never appeared. Still accepted from older clients; turned into hide_cameras /
    # hide_zones (everything Frigate has minus those) as soon as Frigate's list can be read.
    extra_config_keys = FILTERS

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._http = httpx.AsyncClient(timeout=10)
        self._applied_hash: str | None = None          # the part of Frigate's config the devices were built from
        self._avail_seen = False                        # a <prefix>/available message has arrived since connecting
        self._refresh_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None
        self._cams: dict[str, dict[str, Any]] = {}   # frigate camera name -> {eid, track, zones}
        self._zones: dict[str, dict[str, Any]] = {}  # frigate zone name -> {eid, camera, objects}
        self._fired: dict[str, set[str]] = {}         # frigate event id -> {"camera", "zone:<name>"...} already emitted
        self._snapshots: dict[str, str] = {}          # frigate event id -> Newman snapshot id

    @property
    def url(self) -> str:
        return str(self.config["url"]).rstrip("/")

    # -- running --------------------------------------------------------------------------

    async def run(self) -> None:
        attempt = 0
        while True:
            try:
                cfg = await self._load_config()
                mq = cfg.get("mqtt", {})
                prefix = mq.get("topic_prefix") or "frigate"
                host = self.config.get("mqtt_host") or mq.get("host") or "localhost"
                port = int(self.config.get("mqtt_port") or mq.get("port") or 1883)
                await self._seed_counts()
                async with aiomqtt.Client(host, port, identifier=f"newman-{self.ctx.install_id}-{self.id}",
                                          username=self.config.get("mqtt_username") or mq.get("user") or None,
                                          password=self.config.get("mqtt_password") or None, keepalive=30) as client:
                    for topic in (f"{prefix}/available", f"{prefix}/reviews", f"{prefix}/+/+"):
                        await client.subscribe(topic)
                    self._set_connected()
                    attempt = 0
                    self._avail_seen = False
                    timer = asyncio.create_task(self._refresh_timer())
                    try:
                        async for msg in client.messages:
                            await self._on_message(prefix, msg.topic.value, msg.payload)
                    finally:
                        timer.cancel()
                        if self._refresh_task:
                            self._refresh_task.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.set_status("error", f"{e.__class__.__name__}: {e}")
                for eid in self._all_eids():
                    await self.hub.update(eid, available=False)
            attempt += 1
            await self.backoff_sleep(attempt + 1)

    def _set_connected(self) -> None:
        self.set_status("connected", f"{len(self._cams)} cameras · {len(self._zones)} zones")

    def _all_eids(self) -> list[str]:
        return [c["eid"] for c in self._cams.values()] + [z["eid"] for z in self._zones.values()]

    async def _fetch_config(self) -> dict[str, Any]:
        resp = await self._http.get(f"{self.url}/api/config")
        resp.raise_for_status()
        return resp.json()

    async def _load_config(self) -> dict[str, Any]:
        self.set_status("connecting", self.url)
        cfg = await self._fetch_config()
        self._convert_filters(cfg)
        await self._apply_config(cfg)
        return cfg

    # -- which cameras/zones ----------------------------------------------------------------------

    @classmethod
    def migrate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        """Older include lists that need no camera list to convert: empty (= everything) hides nothing;
        None (replaced by a newer choice, see translate_config) goes. Others wait for Frigate's list."""
        out = dict(config)
        for key in FILTERS:
            if key in out and not out[key]:
                if out.pop(key) is not None:
                    out.setdefault(f"hide_{key}", [])
        return out

    def _convert_filters(self, cfg: dict[str, Any]) -> None:
        """Turn an older include list into the hide list: what Frigate has now minus what was included
        (all ticked -> nothing hidden), so cameras added later show up. Saved once."""
        pending = [k for k in FILTERS if isinstance(self.config.get(k), list) and self.config[k]]
        if not pending:
            return
        have = everything(cfg)
        new = dict(self.config)
        for key in pending:
            included = set(new.pop(key))
            new[f"hide_{key}"] = [x for x in have[key] if x not in included]
        self.config = new
        log.info("[%s] Camera/zone choice converted: leaving out %s", self.display_name,
                 ", ".join(list(new.get("hide_cameras") or []) + list(new.get("hide_zones") or [])) or "nothing")
        if self.save_config:
            self.save_config(self.config)

    def translate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """An older client sends include lists ("cameras"/"zones"): converted with Frigate's current list
        when known, else kept to convert on the next connect. A hide list sent now replaces an older
        include list still waiting to be converted."""
        out = dict(config)
        have = everything(self._frigate_cfg) if getattr(self, "_frigate_cfg", None) else None
        for key in FILTERS:
            hide = f"hide_{key}"
            if key in out:
                included = out.pop(key)
                if hide in out:
                    out[key] = None  # both sent: the newer setting wins
                elif not included:
                    out[hide], out[key] = [], None
                elif have is not None:
                    out[hide] = [x for x in have[key] if x not in set(included)]
                    out[key] = None
                else:
                    out[key] = list(included)
            elif hide in out:
                out[key] = None
        return out

    def _wanted(self, key: str, name: str) -> bool:
        included = self.config.get(key)  # an older include list not converted yet (Frigate unreachable)
        if isinstance(included, list) and included:
            return name in included
        return name not in (self.config.get(f"hide_{key}") or [])

    async def _apply_config(self, cfg: dict[str, Any]) -> None:
        """Build/refresh the devices from Frigate's config. New cameras and zones are added, changed ones
        updated (keeping their live counts), and ones Frigate no longer has are kept but unavailable."""
        self._frigate_cfg = cfg
        self._applied_hash = config_hash(cfg)
        streams = set((cfg.get("go2rtc") or {}).get("streams") or {})
        cams: dict[str, dict[str, Any]] = {}
        zones: dict[str, dict[str, Any]] = {}
        for cam, c in enabled_cameras(cfg).items():
            if not self._wanted("cameras", cam):
                continue
            track = [o for o in (c.get("objects") or {}).get("track", []) if o in DETECTABLE]
            live = list(((c.get("live") or {}).get("streams") or {}).values())
            eid = self.entity_id(f"camera_{cam}")
            cams[cam] = {"eid": eid, "track": track, "zones": list((c.get("zones") or {}).keys())}
            counts = self._counts_now(eid, track)
            self.hub.upsert(eid, kind="camera", name=c.get("friendly_name") or nice(cam), integration=self.id)
            await self.hub.update(eid, available=True, state="online", attrs={
                "events": track, "zones": [nice(z) for z in (c.get("zones") or {})],
                "counts": counts,
                "streams": live or [s for s in (cam, f"{cam}_live", f"{cam}_sub") if s in streams]})
            for zone, z in (c.get("zones") or {}).items():
                if not self._wanted("zones", zone):
                    continue
                z = z or {}
                objects = [o for o in (z.get("objects") or track) if o in DETECTABLE]
                zeid = self.entity_id(f"zone_{zone}")
                zones[zone] = {"eid": zeid, "camera": cam, "objects": objects}
                counts = self._counts_now(zeid, objects)
                self.hub.upsert(zeid, kind="zone", name=z.get("friendly_name") or nice(zone), integration=self.id)
                await self.hub.update(zeid, available=True, state="occupied" if any(counts.values()) else "clear",
                                      attrs={"events": objects, "counts": counts, "camera": nice(cam), "area": nice(cam)})
        for gone in [c["eid"] for k, c in self._cams.items() if k not in cams]:
            await self.hub.update(gone, available=False, state="offline")
        for gone in [z["eid"] for k, z in self._zones.items() if k not in zones]:
            await self.hub.update(gone, available=False)
        self._cams, self._zones = cams, zones

    def _counts_now(self, eid: str, objects: list[str]) -> dict[str, int]:
        """Live counts for these objects, keeping what's already known (a refresh mustn't zero them)."""
        ent = self.hub.get(eid)
        known = (ent.attrs.get("counts") or {}) if ent else {}
        return {o: int(known.get(o) or 0) for o in objects}

    # -- following Frigate's config changes ---------------------------------------------------------

    async def _refresh_timer(self) -> None:
        while True:
            await asyncio.sleep(REFRESH_S)
            await self.refresh()

    def _refresh_soon(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            return
        self._refresh_task = asyncio.create_task(self.refresh(retries=5), name=f"frigate-refresh:{self.id}")

    async def refresh(self, retries: int = 1) -> bool:
        """Re-read Frigate's config and apply it if what the devices are built from changed (a restart
        after editing its config, say). Returns whether anything was applied."""
        async with self._refresh_lock:
            cfg = None
            for attempt in range(retries):
                try:
                    cfg = await self._fetch_config()
                    break
                except (httpx.HTTPError, ValueError) as e:
                    if attempt == retries - 1:
                        log.warning("[%s] Couldn't re-read Frigate's config: %s", self.display_name, e)
                        return False
                    await asyncio.sleep(3 * (attempt + 1))  # Frigate's API may still be starting
            if not isinstance(cfg, dict) or config_hash(cfg) == self._applied_hash:
                if isinstance(cfg, dict):
                    self._frigate_cfg = cfg
                return False
            log.info("[%s] Frigate's cameras or zones changed - updating", self.display_name)
            self._convert_filters(cfg)
            await self._apply_config(cfg)
            if self.status == "connected":
                self._set_connected()
            return True

    async def _seed_counts(self) -> None:
        """Objects already in view when Newman starts (counts only arrive on change)."""
        try:
            resp = await self._http.get(f"{self.url}/api/events", params={"in_progress": 1, "limit": 200})
            events = resp.json() if resp.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            return
        cams: dict[str, dict[str, int]] = {}
        zones: dict[str, dict[str, int]] = {}
        for ev in events:
            label, cam = ev.get("label"), ev.get("camera")
            cams.setdefault(cam, {}).setdefault(label, 0)
            cams[cam][label] += 1
            for zone in ev.get("zones") or []:
                zones.setdefault(zone, {}).setdefault(label, 0)
                zones[zone][label] += 1
        for cam, counts in cams.items():
            await self._set_count(cam, counts)
        for zone, counts in zones.items():
            await self._set_count(zone, counts)

    async def _set_count(self, name: str, counts: dict[str, int]) -> None:
        target = self._cams.get(name) or self._zones.get(name)
        if not target:
            return
        ent = self.hub.get(target["eid"])
        if not ent:
            return
        merged = {**ent.attrs.get("counts", {}), **{k: int(v) for k, v in counts.items()}}
        changes: dict[str, Any] = {"attrs": {"counts": merged}}
        if name in self._zones:
            changes["state"] = "occupied" if any(merged.values()) else "clear"
        await self.hub.update(target["eid"], **changes)

    # -- messages ---------------------------------------------------------------------------

    async def _on_message(self, prefix: str, topic: str, raw: Any) -> None:
        if topic == f"{prefix}/available":
            online = (raw.decode(errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)) == "online"
            for cam in self._cams.values():
                await self.hub.update(cam["eid"], available=online, state="online" if online else "offline")
            for zone in self._zones.values():
                await self.hub.update(zone["eid"], available=online)
            # Back online after a restart (which is how Frigate applies a config change): re-read it. The
            # first message after connecting is just Frigate's current state (kept by the broker).
            if online and self._avail_seen:
                self._refresh_soon()
            self._avail_seen = True
            return
        if topic == f"{prefix}/reviews":
            try:
                await self._on_review(json.loads(raw))
            except (ValueError, TypeError, AttributeError):
                pass
            return
        parts = topic.split("/")
        if len(parts) == 3 and parts[2] in DETECTABLE:
            try:
                count = int(raw)
            except (TypeError, ValueError):
                return
            await self._set_count(parts[1], {parts[2]: count})

    async def _on_review(self, msg: dict[str, Any]) -> None:
        """One Frigate review item (an alert or a detection) = one Activity entry, and a trigger per
        object type / zone it involves - each fired once, even as the review is updated."""
        after = msg.get("after") or {}
        rid, cam = after.get("id"), after.get("camera")
        if not rid or cam not in self._cams:
            return
        if msg.get("type") == "end":
            self._fired.pop(rid, None)
            self._snapshots.pop(rid, None)
            return
        use = self.config.get("severity") or "alerts"
        if (after.get("severity") == "alert" and use == "detections") or (
                after.get("severity") != "alert" and use == "alerts"):
            return  # not the kind of review item this connection is set to use
        data = after.get("data") or {}
        objects = [o for o in data.get("objects") or [] if o in DETECTABLE]
        if not objects:
            return
        fired = self._fired.setdefault(rid, set())
        clip = f"{self.id}:r:{rid}"
        new_entry = "logged" not in fired
        to_emit: list[tuple[str, str]] = []  # (entity id, object)
        for obj in objects:
            if f"cam:{obj}" not in fired:
                fired.add(f"cam:{obj}")
                to_emit.append((self._cams[cam]["eid"], obj))
            for zone in data.get("zones") or []:
                if zone in self._zones and obj in self._zones[zone]["objects"] and f"zone:{zone}:{obj}" not in fired:
                    fired.add(f"zone:{zone}:{obj}")
                    to_emit.append((self._zones[zone]["eid"], obj))
        if not to_emit and not new_entry:
            return
        snapshot = await self._snapshot(rid, cam, (data.get("detections") or [None])[0])
        if new_entry:
            fired.add("logged")
            what = " & ".join(o.replace("_", " ") for o in objects).capitalize()
            zones = ", ".join(nice(z) for z in data.get("zones") or [])
            self.ctx.hub_log("alert", f"{what} at {nice(cam)}", zones, self._cams[cam]["eid"],
                             {"snapshotId": snapshot, "clip": clip, "severity": after.get("severity"),
                              "camera": cam, "reviewId": rid,
                              "thumbUrl": f"api/cameras/{self.id}/reviews/{rid}/thumb.webp",
                              "snapshotUrl": f"api/cameras/{self.id}/reviews/{rid}/snapshot.jpg"})
        for eid, obj in to_emit:
            await self.hub.emit(eid, obj, {"snapshot_id": snapshot, "frigate_event_id": rid, "camera": cam,
                                           "clip": clip})
        if len(self._fired) > 2000:
            for k in list(self._fired)[:1000]:
                self._fired.pop(k, None)

    # -- history, read straight from Frigate (survives Newman restarts) ----------------------------

    async def reviews(self, camera: str | None = None, zone: str | None = None, limit: int = 10,
                      before: float | None = None) -> list[dict[str, Any]]:
        """Recent alerts (per this connection's Alerts/Detections choice) for the cameras/zones it covers."""
        use = self.config.get("severity") or "alerts"
        params: dict[str, Any] = {"limit": min(limit * (4 if zone else 1), 200)}
        if use != "both":
            params["severity"] = "alert" if use == "alerts" else "detection"
        cams = [camera] if camera else list(self._cams)
        if cams:
            params["cameras"] = ",".join(cams)
        if before:
            params["before"] = before
        resp = await self._http.get(f"{self.url}/api/review", params=params)
        resp.raise_for_status()
        out = []
        for r in resp.json():
            data = r.get("data") or {}
            if zone and zone not in (data.get("zones") or []):
                continue
            if r.get("camera") not in self._cams:
                continue
            rid = r["id"]
            objects = [o for o in data.get("objects") or [] if o in DETECTABLE]
            out.append({
                "id": rid, "camera": r["camera"], "cameraName": self.hub.name(self._cams[r["camera"]]["eid"]),
                "entityId": self._cams[r["camera"]]["eid"], "severity": r.get("severity"),
                "title": f"{' & '.join(o.replace('_', ' ') for o in objects).capitalize() or 'Activity'} at "
                         f"{self.hub.name(self._cams[r['camera']]['eid'])}",
                "objects": objects, "zones": [nice(z) for z in data.get("zones") or []],
                "start": r.get("start_time"), "end": r.get("end_time"),
                "clip": f"{self.id}:r:{rid}",
                "thumbUrl": f"api/cameras/{self.id}/reviews/{rid}/thumb.webp",
                "snapshotUrl": f"api/cameras/{self.id}/reviews/{rid}/snapshot.jpg",
            })
            if len(out) >= limit:
                break
        return out

    # -- the generic picture/alerts contract (see Integration) ------------------------------------

    provides_alerts = True

    def _where(self, entity_id: str | None) -> tuple[str | None, str | None]:
        """(camera, zone) behind one of this connection's devices."""
        for cam, c in self._cams.items():
            if c["eid"] == entity_id:
                return cam, None
        for zone, z in self._zones.items():
            if z["eid"] == entity_id:
                return z["camera"], zone
        return None, None

    def has_still(self, entity_id: str) -> bool:
        return self._where(entity_id)[0] is not None

    async def still(self, entity_id: str, height: int) -> bytes | None:
        cam, _ = self._where(entity_id)
        if not cam:
            return None
        resp = await self._http.get(f"{self.url}/api/{cam}/latest.jpg", params={"h": height})
        return resp.content if resp.status_code == 200 else None

    def _go2rtc_streams(self) -> set[str]:
        return set(((getattr(self, "_frigate_cfg", None) or {}).get("go2rtc") or {}).get("streams") or {})

    def has_live(self, entity_id: str) -> bool:
        cam, _ = self._where(entity_id)
        return bool(cam) and cam in self._go2rtc_streams()

    def live_streams(self, entity_id: str) -> list[dict[str, Any]]:
        """The camera's go2rtc streams (named <cam> or <cam>_…; a stream that also fits a camera with a
        longer name is that one's) plus any its Frigate config lists under live.streams."""
        cam, _ = self._where(entity_id)
        streams = self._go2rtc_streams()
        if not cam or cam not in streams:
            return []
        cfg = getattr(self, "_frigate_cfg", None) or {}
        cams = list((cfg.get("cameras") or {}).keys())

        def owner(s: str) -> str | None:
            fits = [c for c in cams if s == c or s.startswith(c + "_")]
            return max(fits, key=len) if fits else None
        ids = [cam] + sorted(s for s in streams if s != cam and owner(s) == cam)
        live = ((cfg.get("cameras") or {}).get(cam) or {}).get("live") or {}
        listed = live.get("streams") or {}
        listed = list(listed.values() if isinstance(listed, dict) else listed) + [live.get("stream_name")]  # older Frigate
        for s in listed:
            if isinstance(s, str) and s and s not in ids:
                ids.append(s)
        return [{"id": s, "label": s} for s in ids]

    def live_url(self, entity_id: str, quality: str, stream: str | None = None) -> str | None:
        cam, _ = self._where(entity_id)
        streams = self._go2rtc_streams()
        if not cam or cam not in streams:
            return None
        if stream and stream in {s["id"] for s in self.live_streams(entity_id)}:
            src = stream
        else:
            src = f"{cam}_sub" if quality == "sub" and f"{cam}_sub" in streams else cam
        base = self.url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return f"{base}/live/mse/api/ws?src={src}"

    def video_derived_from(self, entity_id: str) -> str | None:
        """A zone shows its camera's video."""
        cam, zone = self._where(entity_id)
        return self._cams[cam]["eid"] if cam and zone else None

    def video_address(self, entity_id: str) -> str | None:
        """Where the camera really is: its ffmpeg inputs and go2rtc streams, following Frigate's own
        restream (rtsp://127.0.0.1:8554/<stream>) and stream-to-stream references to their sources."""
        cam, _ = self._where(entity_id)
        cfg = getattr(self, "_frigate_cfg", None) or {}
        if not cam:
            return None
        go = (cfg.get("go2rtc") or {}).get("streams") or {}
        inputs = (((cfg.get("cameras") or {}).get(cam) or {}).get("ffmpeg") or {}).get("inputs") or []
        todo: list[Any] = [i.get("path") for i in inputs if isinstance(i, dict)]
        todo += [s["id"] for s in self.live_streams(entity_id)] or ([cam] if cam in go else [])
        seen: set[str] = set()
        while todo:
            item = str(todo.pop(0) or "").strip()
            if not item or item in seen:
                continue
            seen.add(item)
            if item in go:  # a go2rtc stream (by name): its sources
                srcs = go[item]
                todo[:0] = srcs if isinstance(srcs, list) else [srcs]
                continue
            host = address_of(item) if "://" in item else None
            if host and not is_local_address(host):
                return host
            if host:  # Frigate's restream: the go2rtc stream it names
                name = item.split("#", 1)[0].split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
                if name in go:
                    todo.insert(0, name)
        return None

    def has_alerts(self, entity_id: str | None) -> bool:
        return entity_id is None or self._where(entity_id)[0] is not None

    async def alerts(self, entity_id: str | None, limit: int, before: float | None) -> list[dict[str, Any]]:
        cam, zone = self._where(entity_id) if entity_id else (None, None)
        if entity_id and not cam:
            return []
        return await self.reviews(cam, zone, limit, before)

    async def review_image(self, rid: str, which: str) -> tuple[bytes, str]:
        """An alert's small thumbnail ("thumb") or full-size snapshot of the object ("snapshot")."""
        r = await self._review(rid)
        if which == "snapshot":
            det = ((r.get("data") or {}).get("detections") or [None])[0]
            if det:
                resp = await self._http.get(f"{self.url}/api/events/{det}/snapshot.jpg", params={"bbox": 1, "h": 1080})
                if resp.status_code == 200:
                    return resp.content, "image/jpeg"
        path = str(r.get("thumb_path", "")).replace("/media/frigate", "")
        resp = await self._http.get(f"{self.url}{path}")
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type", "image/webp")

    async def dynamic_options(self) -> dict[str, list[dict[str, Any]]]:
        cfg = getattr(self, "_frigate_cfg", None)
        if cfg is None:
            resp = await self._http.get(f"{self.url}/api/config")
            resp.raise_for_status()
            cfg = resp.json()
        cams = enabled_cameras(cfg)
        cam_opts = [{"value": k, "label": v.get("friendly_name") or nice(k)} for k, v in cams.items()]
        zone_opts = [{"value": z, "label": (zv or {}).get("friendly_name") or nice(z), "group": c.get("friendly_name") or nice(k)}
                     for k, c in cams.items() for z, zv in (c.get("zones") or {}).items()]
        # The older keys too, for older clients.
        return {"hide_cameras": cam_opts, "hide_zones": zone_opts, "cameras": cam_opts, "zones": zone_opts}

    # -- clips & snapshots -----------------------------------------------------------------------

    async def _review(self, rid: str) -> dict[str, Any]:
        resp = await self._http.get(f"{self.url}/api/review/{rid}")
        resp.raise_for_status()
        return resp.json()

    async def event_finished(self, clip_id: str) -> bool:
        """Whether the clip is complete (and may be cached): ended, and long enough ago that Frigate
        has written the recording segments of its last seconds (CLIP_SETTLE_S)."""
        if clip_id.startswith("r:"):
            end = (await self._review(clip_id[2:])).get("end_time")
        else:
            resp = await self._http.get(f"{self.url}/api/events/{clip_id}")
            end = resp.json().get("end_time") if resp.status_code == 200 else None
        return end is not None and time.time() - float(end) >= CLIP_SETTLE_S

    async def open_clip(self, clip_id: str, range_header: str | None) -> httpx.Response:
        """Stream an alert's video from Frigate's recordings ("r:<review id>"), or a single
        detection's clip (older entries)."""
        if clip_id.startswith("r:"):
            r = await self._review(clip_id[2:])
            start = float(r["start_time"]) - 3  # a moment of lead-in, like Frigate's own player
            end = float(r.get("end_time") or time.time()) + 3
            url = f"{self.url}/api/{r['camera']}/start/{start:.0f}/end/{end:.0f}/clip.mp4"
        else:
            url = f"{self.url}/api/events/{clip_id}/clip.mp4"
        req = self._http.build_request("GET", url, headers={"Range": range_header} if range_header else {},
                                       timeout=httpx.Timeout(30, read=120))
        return await self._http.send(req, stream=True)

    async def _snapshot(self, rid: str, cam: str, detection_id: str | None) -> str | None:
        """Frigate's best picture of the object (with its box), else the camera's current frame."""
        if rid in self._snapshots:
            return self._snapshots[rid]
        content = None
        if detection_id:
            try:
                resp = await self._http.get(f"{self.url}/api/events/{detection_id}/snapshot.jpg",
                                            params={"bbox": 1, "h": 720})
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
                    content = resp.content
            except httpx.HTTPError:
                pass
        if content is None:
            try:
                resp = await self._http.get(f"{self.url}/api/{cam}/latest.jpg", params={"bbox": 1, "h": 720})
                resp.raise_for_status()
                content = resp.content
            except httpx.HTTPError as e:
                log.warning("Frigate snapshot for %s failed: %s", cam, e)
                return None
        self.ctx.snapshot_dir.mkdir(parents=True, exist_ok=True)
        sid = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        (self.ctx.snapshot_dir / f"{sid}.jpg").write_bytes(content)
        self._snapshots[rid] = sid
        return sid
