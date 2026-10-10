"""Per-device video settings, for any device with live video or pictures, from any connection:

* streams: which of a device's live streams its tiles ("sub") and its full-screen view ("main") play,
  picked from the streams its connection offers (Integration.live_streams). Unset = Automatic: the
  connection decides, exactly as before. A picked stream its connection no longer offers counts as
  Automatic (kept, in case it comes back).
* "Show in All Cameras" (on unless switched off): only the All Cameras lists leave a device out.
* "Video from": another connection's live video and pictures shown for this device (a doorbell whose
  presses come from one connection and whose video comes from another). Its own events stay its own.

Only video ORIGINS can be shown elsewhere: devices with live video of their own that isn't simply another
of their connection's devices' (Integration.video_derived_from - e.g. an area seen by one camera). Such a
derived device follows that device: no stream choice of its own, and a link to it means its origin (a link
that comes back to the device itself means none; it's dropped on the next save). The choices offered are
per connection: the device's own, then each other connection with origins - with the one at the same
network address (Integration.video_address) picked automatically, else any of that connection's origins
picked by hand. The link kept is still a device id; links aren't followed further, so a chain or loop
can't happen at play time; saving one that would loop, or a link to itself, is refused. A link to a device
that's gone or has lost its live video is ignored: the device's own picture is used.

Nothing here knows about any particular connection or kind of device.
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .integrations.manager import IntegrationManager
    from .state import Hub
    from .store import Store

log = logging.getLogger(__name__)

STREAM_FOR = {"sub": "tileStream", "main": "fullStream"}  # live quality -> the setting that picks its stream
FIELDS = ("tileStream", "fullStream", "videoFrom", "showInCameras")


class Video:
    def __init__(self, store: "Store", hub: "Hub", integrations: "IntegrationManager", snapshot_dir: Path) -> None:
        self.store, self.hub, self.integrations, self.snapshot_dir = store, hub, integrations, snapshot_dir
        self.reload()
        hub.video_source = self.source

    def reload(self) -> None:
        self._saved = self.store.entity_settings()

    # -- reads ---------------------------------------------------------------------------------

    def _inst(self, eid: str) -> Any:
        e = self.hub.get(eid)
        return self.integrations.instances.get(e.integration) if e else None

    def _ask(self, eid: str, hook: str) -> Any:
        inst = self._inst(eid)
        if not inst:
            return None
        try:
            return getattr(inst, hook)(eid)
        except Exception:
            log.exception("%s for %s failed", hook, eid)
            return None

    def saved(self, eid: str) -> dict[str, Any]:
        return dict(self._saved.get(eid) or {})

    def own_live(self, eid: str) -> bool:
        inst = self._inst(eid)
        return bool(inst and inst.has_live(eid))

    def _own_still(self, eid: str) -> bool:
        inst = self._inst(eid)
        return bool(inst and inst.has_still(eid))

    def derived_from(self, eid: str) -> str | None:
        """The device of the same connection whose video `eid`'s simply is, or None (its own)."""
        d = self._ask(eid, "video_derived_from")
        return d if isinstance(d, str) and d != eid and self.hub.get(d) else None

    def is_origin(self, eid: str) -> bool:
        """Has live video of its own that isn't another device's: can be shown by other devices."""
        return self.own_live(eid) and not self.derived_from(eid)

    def address(self, eid: str) -> str | None:
        a = self._ask(eid, "video_address")
        return a.lower() if isinstance(a, str) and a else None

    def _resolve(self, eid: str, target: str | None) -> str | None:
        """A link as meant: a derived device means its origin; one back to `eid` itself means none."""
        if not target:
            return None
        target = self.derived_from(target) or target
        return None if target == eid else target

    def link(self, eid: str) -> str | None:
        """The device `eid` is set to take its video from (resolved), working or not."""
        return self._resolve(eid, self.saved(eid).get("videoFrom"))

    def streams(self, eid: str) -> list[dict[str, str]]:
        """The device's own connection's streams, [{"id", "label"}] (none for a derived device)."""
        inst = self._inst(eid)
        if not inst or self.derived_from(eid):
            return []
        try:
            raw = inst.live_streams(eid) or []
        except Exception:
            log.exception("Listing %s's streams failed", eid)
            return []
        out: list[dict[str, str]] = []
        for s in raw:
            sid = str((s or {}).get("id") or "")
            if sid and all(o["id"] != sid for o in out):
                out.append({"id": sid, "label": str(s.get("label") or sid)})
        return out

    def stream(self, eid: str, quality: str) -> str | None:
        """The stream picked for this quality, if its connection still offers it; None = Automatic."""
        want = self.saved(eid).get(STREAM_FOR.get(quality, "fullStream"))
        return want if want and any(s["id"] == want for s in self.streams(eid)) else None

    def source(self, eid: str) -> str | None:
        """The device whose video and pictures `eid` shows instead of its own, or None (its own)."""
        target = self.link(eid)
        if not target or not self.hub.get(target) or not self.is_origin(target):
            return None
        return target

    def _player(self, eid: str) -> str:
        """The device whose streams `eid`'s live video plays."""
        return self.source(eid) or self.derived_from(eid) or eid

    def linked_to(self, eid: str) -> list[str]:
        """Devices set to show `eid`'s video."""
        return [k for k, v in self._saved.items() if k != eid and (v.get("videoFrom") == eid or self.link(k) == eid)]

    def shown_in_cameras(self, eid: str) -> bool:
        return self.saved(eid).get("showInCameras", True) is not False

    def has_live(self, eid: str) -> bool:
        return self.own_live(self.source(eid) or eid)

    def live_url(self, eid: str, quality: str) -> str | None:
        src = self.source(eid) or eid
        inst = self._inst(src)
        if not inst or not inst.has_live(src):
            return None
        chosen = self.stream(self._player(eid), quality)
        return inst.live_url(src, quality, stream=chosen) if chosen else inst.live_url(src, quality)

    def picture_of(self, eid: str) -> str | None:
        """The device whose current picture `eid` shows: its "Video from" device, else its own (None: none)."""
        src = self.source(eid)
        if src and self._own_still(src):
            return src
        return eid if self._own_still(eid) else None

    async def still(self, eid: str, height: int) -> bytes | None:
        pic = self.picture_of(eid)
        inst = self._inst(pic) if pic else None
        return await inst.still(pic, height) if inst else None

    def key(self, eid: str) -> str:
        """Changes whenever what its live video plays changes (players reconnect)."""
        src = self.source(eid)
        play = self._player(eid)
        return "|".join((src or "", self.stream(play, "sub") or "", self.stream(play, "main") or ""))

    # -- "Video from" choices ------------------------------------------------------------------

    def origins(self) -> list[dict[str, Any]]:
        """Every device whose video others can show: [{"id", "name", "integration", "address"}]. Pass the
        result to to_json() when building many devices' JSON at once."""
        out = []
        for e in self.hub.all():
            if self.is_origin(e.id):
                out.append({"id": e.id, "name": e.name, "integration": e.integration, "address": self.address(e.id)})
        return sorted(out, key=lambda o: o["name"].lower())

    def _integration_name(self, iid: str) -> str:
        inst = self.integrations.instances.get(iid)
        return getattr(inst, "display_name", None) or iid

    def choices(self, eid: str, origins: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """What "Video from" offers `eid`: {"own": label, "options": [{"integration", "label", "device", "cameras"}]}
        - one option per OTHER connection that has origins; "device": its origin at `eid`'s address (picked
        automatically) or None (pick from "cameras", that connection's origins [{"id", "name"}])."""
        e = self.hub.get(eid)
        own_iid = e.integration if e else ""
        has_own = self.own_live(eid) or self._own_still(eid)
        own = f"{self._integration_name(own_iid)} ({'this device' if has_own else 'no video'})"
        if self.derived_from(eid) and not self.link(eid):
            return {"own": own, "options": []}  # follows its origin: nothing to choose
        if origins is None:
            origins = self.origins()
        here = self.address(eid)
        groups: dict[str, list[dict[str, Any]]] = {}
        for o in origins:
            if o["integration"] != own_iid and o["id"] != eid:
                groups.setdefault(o["integration"], []).append(o)
        options = []
        for iid, devs in groups.items():
            match = next((o["id"] for o in devs if here and o["address"] == here), None)
            options.append({"integration": iid, "label": self._integration_name(iid), "device": match,
                            "cameras": [{"id": o["id"], "name": o["name"]} for o in devs]})
        options.sort(key=lambda o: o["label"].lower())
        return {"own": own, "options": options}

    def to_json(self, eid: str, origins: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        link = self.link(eid)
        return {"from": link, "source": self.source(eid),
                "fromIntegration": (self.hub.get(link).integration if link and self.hub.get(link) else None),
                "tileStream": self.stream(eid, "sub"), "fullStream": self.stream(eid, "main"),
                "streams": self.streams(eid), "ownLive": self.own_live(eid),
                "derivedFrom": self.derived_from(eid), "choices": self.choices(eid, origins),
                "showInCameras": self.shown_in_cameras(eid), "key": self.key(eid)}

    # -- changes -------------------------------------------------------------------------------

    def check(self, eid: str, patch: dict[str, Any]) -> dict[str, Any]:
        """The device's settings with `patch` applied; ValueError (a reason to show) when it can't be."""
        if not self.hub.get(eid):
            raise ValueError("Unknown device")
        out = self.saved(eid)
        if out.get("videoFrom") and out["videoFrom"] != self._resolve(eid, out["videoFrom"]) and self.hub.get(out["videoFrom"]):
            resolved = self._resolve(eid, out["videoFrom"])  # an old link to a derived device: its origin
            if resolved:
                out["videoFrom"] = resolved
            else:
                out.pop("videoFrom")
        for key in FIELDS:
            if key not in patch:
                continue
            v = patch[key]
            if key == "showInCameras":
                if not isinstance(v, bool):
                    raise ValueError("showInCameras is true or false")
                if v:
                    out.pop(key, None)  # the default: nothing kept
                else:
                    out[key] = False
                continue
            if v in (None, ""):
                out.pop(key, None)
                continue
            if not isinstance(v, str):
                raise ValueError(f"{key} is a text id")
            if key in ("tileStream", "fullStream"):
                if all(s["id"] != v for s in self.streams(eid)):
                    raise ValueError(f"{self.hub.name(eid)} has no stream {v!r}")
            else:
                if v == eid:
                    raise ValueError("A device can't take video from itself")
                if not self.hub.get(v) or not self.own_live(v):
                    raise ValueError(f"{self.hub.name(v)} has no live video to take")
                v = self._resolve(eid, v)
                if not v:  # one that is really its own video: the same as none
                    out.pop(key, None)
                    continue
                seen, at = {eid}, v
                while at:  # would this close a loop (… -> v -> … -> eid)?
                    if at in seen:
                        raise ValueError(f"{self.hub.name(v)} already takes its video from {self.hub.name(eid)}")
                    seen.add(at)
                    at = self.link(at)
            out[key] = v
        return out
    def save(self, eid: str, patch: dict[str, Any]) -> list[str]:
        """Apply and keep `patch`; returns the devices whose video or pictures may have changed."""
        body = self.check(eid, patch)
        self.store.set_entity_settings(eid, body)
        self.reload()
        return [eid, *self.linked_to(eid)]

    async def snapshot(self, eid: str) -> str | None:
        """For a device showing another device's video: that device's picture right now, kept like a
        connection's snapshot (its id). None when the device has no working link or no picture came."""
        src = self.source(eid)
        if not src or not self._own_still(src):
            return None
        try:
            jpeg = await self._inst(src).still(src, 1080)
        except Exception as e:
            log.warning("Picture from %s for %s failed: %s", src, eid, e)
            return None
        if not jpeg:
            return None
        sid = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        (self.snapshot_dir / f"{sid}.jpg").write_bytes(jpeg)
        return sid

    def replace_ids(self, mapping: dict[str, str]) -> int:
        """Swapping devices (replace.py): settings move to the new device (unless it has its own) and
        "Video from" links to an old device point at the new one. Returns how many changed."""
        n = 0
        saved = self.store.entity_settings()
        for old, new in mapping.items():
            if old in saved and new not in saved:
                saved[new] = saved[old]
                self.store.set_entity_settings(new, saved[new])
                n += 1
        for eid, body in self.store.entity_settings().items():
            if body.get("videoFrom") in mapping and mapping[body["videoFrom"]] != eid:
                self.store.set_entity_settings(eid, {**body, "videoFrom": mapping[body["videoFrom"]]})
                n += 1
        self.reload()
        return n

    def forget(self, ids: list[str]) -> list[str]:
        """Removed devices: their settings go, and "Video from" links to them fall back to Automatic.
        Returns the other devices whose link was dropped (their screens need telling)."""
        gone, linked = set(ids), []
        for eid, body in self.store.entity_settings().items():
            if eid in gone:
                self.store.set_entity_settings(eid, {})
            elif body.get("videoFrom") in gone:
                self.store.set_entity_settings(eid, {k: v for k, v in body.items() if k != "videoFrom"})
                linked.append(eid)
        self.reload()
        return linked
