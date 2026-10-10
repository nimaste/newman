"""LG TVs (webOS, 2014 and later) over the TV's SSAP websocket (ws :3000 / wss :3001), using aiowebostv -
the library behind Home Assistant's LG webOS integration (small: aiohttp only, kept up to date there).

The first time, the TV asks "Allow this device to connect?"; once accepted it hands back a client key, and
that key is all Newman keeps. The TV then pushes power, volume, input, app and playback changes as they
happen. A webOS TV drops off the network when switched off, so "on" is Wake-on-LAN (LG: Settings › General ›
Devices › TV Management › "Turn on via Wi-Fi", or Mobile TV On) - except in quick-start standby, where the
TV still answers and takes the power-on command.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import Field
from .tv_common import HOST, MAC, POLL, NeedsPairing, TvIntegration, items

log = logging.getLogger(__name__)

# Newman key -> SSAP pointer-socket button name.
KEYMAP = {
    "home": "HOME", "back": "BACK", "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT", "ok": "ENTER",
    "menu": "MENU", "info": "INFO", "guide": "GUIDE", "play": "PLAY", "pause": "PAUSE", "stop": "STOP",
    "rewind": "REWIND", "fast_forward": "FASTFORWARD", "volume_up": "VOLUMEUP", "volume_down": "VOLUMEDOWN",
    "mute": "MUTE", "channel_up": "CHANNELUP", "channel_down": "CHANNELDOWN", "power": "POWER",
}

MEDIA = {"playing": "playing", "paused": "paused", "stopped": "idle", "unloaded": "idle", "loaded": "idle"}


def map_state(st: Any) -> tuple[str, dict[str, Any]]:
    """aiowebostv's WebOsTvState -> (state, attrs)."""
    apps = st.apps or {}
    inputs = st.inputs or {}
    attrs: dict[str, Any] = {
        "volume": st.volume, "muted": st.muted,
        "apps": items((a_id, (a or {}).get("title")) for a_id, a in apps.items()),
        "sources": items((i_id, (i or {}).get("label")) for i_id, i in inputs.items()),
        "appId": st.current_app_id,
        "app": ((apps.get(st.current_app_id) or {}).get("title")) if st.current_app_id else None,
        "source": next(((i or {}).get("label") for i in inputs.values()
                        if st.current_app_id and (i or {}).get("appId") == st.current_app_id), None),
    }
    play = next((m.get("playState") for m in (st.media_state or []) if isinstance(m, dict) and m.get("playState")),
                None)
    attrs["mediaState"] = MEDIA.get(play, "idle") if play else None
    return ("on" if st.is_on else "standby"), attrs


class WebOsTvIntegration(TvIntegration):
    type = "webos_tv"
    name = "LG TV (webOS)"
    description = ("An LG smart TV (webOS) on your network: on / off, volume, inputs, apps, remote keys, live "
                   "updates. The first time, accept Newman on the TV's screen; Newman keeps only the key the TV "
                   "hands back, never a password. Turning it on needs “Turn on via Wi-Fi” on the TV.")
    fields = [HOST, MAC,
              Field("client_key", "TV's key for Newman", "password", advanced=True,
                    help="Filled in automatically when you accept Newman on the TV. Clear it to pair again."),
              POLL]
    zeroconf = ["_airplay._tcp.local."]
    KEYMAP = KEYMAP
    wakes_itself = True

    @classmethod
    def from_discovery(cls, name: str, host: str, props: dict[str, str]) -> dict[str, Any] | None:
        if not (props.get("manufacturer") or "").lower().startswith("lg"):
            return None
        title = name.split("._airplay.")[0]
        return {"title": title if "lg" in title.lower() else f"{title} (LG)", "config": {"host": host}}

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.client: Any = None

    def make_client(self) -> Any:
        from aiowebostv import WebOsClient
        return WebOsClient(self.host, client_key=self.config.get("client_key") or None)

    async def on_state(self, st: Any) -> None:
        state, attrs = map_state(st)
        await self.publish(state, **attrs)

    async def _connect(self) -> None:
        from aiowebostv import WebOsTvPairError
        self.client = self.client or self.make_client()
        if not self.config.get("client_key"):
            self.set_status("pairing", "Accept Newman on the TV - a prompt is showing on its screen")
        try:
            await self.client.connect()
        except WebOsTvPairError as e:
            raise NeedsPairing("The TV didn't accept Newman. Switch it on, save again and accept the prompt") from e
        self.remember(client_key=self.client.client_key)
        await self.client.register_state_update_callback(self.on_state)
        try:
            info = await self.client.get_connection_info() or {}
            mac = (info.get("wiredInfo") or {}).get("macAddress") or (info.get("wifiInfo") or {}).get("macAddress")
            self.learn_mac(mac or "")
        except Exception:
            self.learn_mac()

    async def run(self) -> None:
        self.ensure_device()
        attempt = 0
        try:
            while True:
                try:
                    await self._connect()
                    attempt = 0
                    model = ((getattr(self.client, "tv_info", None) and self.client.tv_info.system) or {}).get("modelName")
                    self.set_status("connected", f"LG {model or 'TV'} · live updates")
                    while self.client.is_connected():
                        await asyncio.sleep(self.poll_seconds)
                    raise ConnectionError("disconnected")
                except asyncio.CancelledError:
                    raise
                except NeedsPairing as e:
                    self.set_status("error", str(e))
                    await asyncio.Event().wait()
                except Exception as e:
                    log.debug("webOS %s: %s: %s", self.host, e.__class__.__name__, e)
                    await self.mark_off()
                    if self.status != "pairing":
                        self.set_status("connected" if self.config.get("mac") else "error",
                                        f"The TV isn't answering at {self.host} (off, or its address changed)")
                    attempt += 1
                    await asyncio.sleep(min(self.poll_seconds * (1 + attempt), 60))
        finally:
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass

    # -- commands -----------------------------------------------------------------------------------

    async def do_key(self, key: str) -> None:
        if key == "mute":
            await self.client.set_mute(not bool(self.client.tv_state.muted))
        elif key == "volume_up":
            await self.client.volume_up()
        elif key == "volume_down":
            await self.client.volume_down()
        else:
            await self.client.button(KEYMAP[key])

    async def do_power_on(self) -> None:
        await self.client.power_on()

    async def do_power_off(self) -> None:
        await self.client.power_off()

    async def do_set_volume(self, level: int) -> None:
        await self.client.set_volume(level)

    async def do_media(self, action: str) -> None:
        if action == "play":
            await self.client.play()
        elif action == "pause":
            await self.client.pause()
        else:
            await self.client.button({"next": "FASTFORWARD", "previous": "REWIND"}[action])

    async def do_select_source(self, source_id: str) -> None:
        await self.client.set_input(source_id)

    async def do_launch_app(self, app_id: str) -> None:
        await self.client.launch_app(app_id)
