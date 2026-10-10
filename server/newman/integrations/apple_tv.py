"""Apple TV (tvOS) with pyatv - the library behind Home Assistant's Apple TV integration.

Pairing: the Apple TV shows a 4-digit code for each protocol Newman pairs with - AirPlay (remote keys, what's
playing, through its tunnel) and then Companion (apps, power, volume) - so expect two codes in a row. Each code
unlocks credentials (keys, never an Apple ID password), which are all Newman keeps.
State (power, what's playing, the open app, volume) is read every few seconds; the Apple TV wakes itself on
"turn on" (no Wake-on-LAN needed).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .base import Field
from .tv_common import CODE, HOST, POLL, NeedsPairing, TvIntegration, items, park, unpark

log = logging.getLogger(__name__)

# Newman key -> pyatv RemoteControl method.
KEYMAP = {
    "home": "home", "back": "menu", "menu": "top_menu", "up": "up", "down": "down", "left": "left",
    "right": "right", "ok": "select", "play_pause": "play_pause", "play": "play", "pause": "pause", "stop": "stop",
    "rewind": "skip_backward", "fast_forward": "skip_forward", "next": "next", "previous": "previous",
    "volume_up": "volume_up", "volume_down": "volume_down", "channel_up": "channel_up",
    "channel_down": "channel_down", "guide": "guide",
}
# Pairing order (pyatv Protocol names).
PAIR_ORDER = ("AirPlay", "Companion")
MEDIA = {"Playing": "playing", "Paused": "paused", "Loading": "playing", "Seeking": "playing"}


def protocols_to_pair(conf: Any, creds: dict[str, str]) -> list[Any]:
    """The protocols this Apple TV offers that want pairing and that Newman has no credentials for yet."""
    from pyatv.const import PairingRequirement, Protocol
    out = []
    for name in PAIR_ORDER:
        proto = getattr(Protocol, name)
        svc = conf.get_service(proto)
        if svc is None or name.lower() in creds:
            continue
        if svc.pairing in (PairingRequirement.Mandatory, PairingRequirement.Optional):
            out.append(proto)
    return out


class AppleTvIntegration(TvIntegration):
    type = "apple_tv"
    name = "Apple TV"
    description = ("An Apple TV on your network: on / off (sleep), remote keys, volume, apps, what's playing. Pair "
                   "with the codes the Apple TV shows (two, one after the other); Newman keeps only the pairing "
                   "credentials, never an Apple ID or password.")
    fields = [HOST, CODE,
              Field("credentials", "Pairing credentials", "password", advanced=True,
                    help="Filled in automatically while pairing. Clear it to pair again."),
              POLL]
    zeroconf = ["_companion-link._tcp.local."]
    KEYMAP = KEYMAP
    wakes_itself = True

    @classmethod
    def from_discovery(cls, name: str, host: str, props: dict[str, str]) -> dict[str, Any] | None:
        model = props.get("rpMd") or ""
        if not model.startswith("AppleTV"):
            return None  # Macs, iPads, HomePods announce Companion too
        return {"title": f"{name.split('._companion-link.')[0]} (Apple TV)", "config": {"host": host}}

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.atv: Any = None
        self._apps: list[dict[str, str]] = []

    @property
    def creds(self) -> dict[str, str]:
        try:
            return dict(json.loads(self.config.get("credentials") or "{}"))
        except ValueError:
            return {}

    async def scan(self) -> Any:
        import pyatv
        found = await pyatv.scan(asyncio.get_running_loop(), hosts=[self.host], timeout=5)
        if not found:
            raise ConnectionError("no Apple TV answered")
        return found[0]

    async def start_pairing(self, conf: Any, proto: Any) -> Any:
        import pyatv
        handler = await pyatv.pair(conf, proto, asyncio.get_running_loop())
        await handler.begin()
        return handler

    # -- pairing ------------------------------------------------------------------------------------

    async def pair_step(self, conf: Any) -> bool:
        """True when nothing is left to pair. Otherwise one step: finish with the saved code, or ask the
        Apple TV for a new one (parked until the code is saved)."""
        creds = self.creds
        todo = protocols_to_pair(conf, creds)
        if not todo:
            return True
        proto = todo[0]
        code = str(self.config.get("code") or "").strip()
        parked = unpark(self.id)
        if code and parked and parked[0] == proto.name:
            self.forget("code")
            handler = parked[1]
            try:
                handler.pin(code)
                await handler.finish()
                if handler.has_paired:
                    creds[proto.name.lower()] = handler.service.credentials
                    self.remember(credentials=json.dumps(creds))
                    return await self.pair_step(conf)
            finally:
                await handler.close()
            self.set_status("pairing", "That code didn't work - a new one is on the Apple TV; enter it and save")
        handler = await self.start_pairing(conf, proto)
        park(self.id, (proto.name, handler))
        step = f" (step {PAIR_ORDER.index(proto.name) + 1} of {len(PAIR_ORDER)})" if len(todo) > 1 or creds else ""
        if self.status != "pairing" or "didn't work" not in self.detail:
            self.set_status("pairing", f"Enter the 4-digit code the Apple TV is showing, then save{step}")
        return False

    # -- running ------------------------------------------------------------------------------------

    async def connect(self, conf: Any) -> Any:
        import pyatv
        from pyatv.const import Protocol
        for name, c in self.creds.items():
            proto = next((p for p in Protocol if p.name.lower() == name), None)
            if proto is not None:
                conf.set_credentials(proto, c)
        return await pyatv.connect(conf, asyncio.get_running_loop())

    async def refresh(self) -> None:
        from pyatv.const import PowerState
        atv = self.atv
        attrs: dict[str, Any] = {}
        on = atv.power.power_state != PowerState.Off
        try:
            playing = await atv.metadata.playing()
            attrs["mediaTitle"] = playing.title or None
            attrs["mediaArtist"] = getattr(playing, "artist", None) or None
            attrs["mediaState"] = MEDIA.get(getattr(playing.device_state, "name", ""), "idle")
        except Exception:
            pass
        try:
            app = atv.metadata.app
            attrs["app"], attrs["appId"] = (app.name, app.identifier) if app else (None, None)
        except Exception:
            pass
        try:
            attrs["volume"] = round(atv.audio.volume)
        except Exception:
            pass
        if not self._apps:
            try:
                self._apps = items((a.identifier, a.name) for a in await atv.apps.app_list())
            except Exception:
                pass
        attrs["apps"] = self._apps
        await self.publish("on" if on else "standby", **attrs)

    async def run(self) -> None:
        self.ensure_device()
        attempt = 0
        try:
            while True:
                try:
                    conf = await self.scan()
                    if not await self.pair_step(conf):
                        await asyncio.Event().wait()  # saving the code restarts this
                    self.atv = await self.connect(conf)
                    attempt = 0
                    model = getattr(self.atv.device_info, "model_str", "") or "Apple TV"
                    self.set_status("connected", f"{model} · on your network")
                    while True:
                        await self.refresh()
                        await asyncio.sleep(self.poll_seconds)
                except asyncio.CancelledError:
                    raise
                except NeedsPairing as e:
                    self.set_status("error", str(e))
                    await asyncio.Event().wait()
                except Exception as e:
                    log.debug("Apple TV %s: %s: %s", self.host, e.__class__.__name__, e)
                    self._close()
                    await self.mark_off()
                    if self.status != "pairing":
                        self.set_status("error", f"The Apple TV isn't answering at {self.host}")
                    attempt += 1
                    await self.backoff_sleep(attempt + 2)
        finally:
            self._close()

    def _close(self) -> None:
        if self.atv:
            try:
                self.atv.close()
            except Exception:
                pass
            self.atv = None

    # -- commands -----------------------------------------------------------------------------------

    async def do_key(self, key: str) -> None:
        await getattr(self.atv.remote_control, KEYMAP[key])()

    async def do_media(self, action: str) -> None:
        await self.do_key(action)

    async def do_power_on(self) -> None:
        await self.atv.power.turn_on()

    async def do_power_off(self) -> None:
        await self.atv.power.turn_off()

    async def do_set_volume(self, level: int) -> None:
        await self.atv.audio.set_volume(float(level))

    async def do_launch_app(self, app_id: str) -> None:
        await self.atv.apps.launch_app(app_id)
