"""Sonos speakers, on your network (SoCo - the library behind Home Assistant's Sonos integration).
No Sonos account. Each speaker becomes a "speaker": play, pause, volume, and what's playing as
readings. Play / pause go to the group's coordinator, so a grouped speaker controls its group.

SoCo is synchronous (UPnP over HTTP), so every call runs in a worker thread; state is read every
few seconds rather than through UPnP event subscriptions (those need Newman to run a callback
web server the speakers can reach).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ._common import clean_id, device_summary
from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

TRANSPORT = {"PLAYING": "playing", "TRANSITIONING": "playing", "PAUSED_PLAYBACK": "paused", "STOPPED": "idle"}


def hosts_from_config(config: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for d in config.get("speakers") or []:
        host = str((d or {}).get("host") or "").strip()
        if host and host not in out:
            out.append(host)
    return out


def read_speaker(spk: Any) -> dict[str, Any]:
    """One blocking read of a speaker (runs in a thread)."""
    coord = spk.group.coordinator if spk.group else spk
    transport = coord.get_current_transport_info().get("current_transport_state", "")
    track = coord.get_current_track_info() or {}
    return {"uid": spk.uid, "name": spk.player_name, "state": TRANSPORT.get(transport, "idle"),
            "volume": int(spk.volume), "muted": bool(spk.mute), "track": track.get("title") or None,
            "artist": track.get("artist") or None, "album": track.get("album") or None,
            "grouped": len(spk.group.members) > 1 if spk.group else False,
            "model": (spk.get_speaker_info() or {}).get("model_name")}


class SonosIntegration(Integration):
    type = "sonos"
    name = "Sonos"
    category = "other"
    provides = ("speaker",)  # kinds of device it can bring in (shown on its catalog card)
    description = ("Sonos speakers on your network - play, pause and volume from automations, and what's playing. "
                   "No Sonos account needed.")
    fields = [
        Field("speakers", "Speakers", "devices", dynamic=True,
              help="Each speaker by address - or use “Scan my network” to find them.",
              item_fields=(Field("host", "Address", required=True, placeholder="10.1.1.90"),)),
        Field("poll_seconds", "Check every (seconds)", "number", default=5, advanced=True),
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._speakers: dict[str, Any] = {}   # entity id -> soco.SoCo
        self._problems: dict[str, str] = {}

    def _soco(self, host: str) -> Any:
        import soco
        return soco.SoCo(host)

    async def dynamic_options(self) -> dict[str, list[dict[str, Any]]]:
        """Speakers answering a network search right now (only when the owner asks)."""
        import soco
        found = await asyncio.to_thread(soco.discover, 3) or set()
        added = set(hosts_from_config(self.config))
        out = []
        for s in sorted(found, key=lambda s: s.ip_address):
            name = await asyncio.to_thread(lambda: s.player_name)
            out.append({"value": s.ip_address, "label": f"{name} ({s.ip_address})",
                        "group": "Already added" if s.ip_address in added else "Found"})
        return {"speakers": out}

    async def run(self) -> None:
        hosts = hosts_from_config(self.config)
        if not hosts:
            self.set_status("pairing", "Open the settings and use “Scan my network” to find your speakers, "
                                       "or add one by address")
            await asyncio.Event().wait()
        by_host: dict[str, str] = {}
        while True:
            for host in hosts:
                try:
                    spk = self._speakers.get(by_host.get(host, "")) or self._soco(host)
                    data = await asyncio.wait_for(asyncio.to_thread(read_speaker, spk), 15)
                    eid = self.entity_id(clean_id(data["uid"]))
                    if eid not in self._speakers:
                        self.hub.upsert(eid, kind="speaker", name=data["name"] or "Speaker", integration=self.id)
                        self._speakers[eid], by_host[host] = spk, eid
                    attrs = {k: data[k] for k in ("volume", "muted", "track", "artist", "album", "grouped", "model")}
                    attrs["host"] = host
                    await self.hub.update(eid, available=True, state=data["state"], attrs=attrs)
                    self._problems.pop(host, None)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.info("Sonos %s: %s: %s", host, e.__class__.__name__, e)
                    self._problems[host] = f"can't reach {host}"
                    if host in by_host:
                        await self.hub.update(by_host[host], available=False)
            ok = len(hosts) - len(self._problems)
            if self._problems and not ok:
                msg = "; ".join(self._problems.values())
                self.set_status("error", msg[:1].upper() + msg[1:])
            elif self._problems:
                self.set_status("error", f"{ok} of {len(hosts)} speakers on your network · "
                                         + "; ".join(self._problems.values()))
            else:
                self.set_status("connected", f"{device_summary(['speaker'] * len(hosts))} · on your network")
            await asyncio.sleep(float(self.config.get("poll_seconds") or 5))

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        spk = self._speakers.get(entity_id)
        if not spk:
            raise CommandError("That speaker isn't connected")

        def do() -> None:
            coord = spk.group.coordinator if spk.group else spk
            if command == "play":
                coord.play()
            elif command == "pause":
                coord.pause()
            elif command == "set_volume":
                spk.volume = max(0, min(100, int(float(value if value is not None else 30))))
            else:
                raise CommandError(f"A speaker can't {command.replace('_', ' ')}")
        try:
            await asyncio.wait_for(asyncio.to_thread(do), 15)
        except CommandError:
            raise
        except Exception as e:
            raise CommandError(f"The speaker didn't accept that ({e.__class__.__name__})") from e
        data = await asyncio.to_thread(read_speaker, spk)
        await self.hub.update(entity_id, state=data["state"], attrs={"volume": data["volume"]})
