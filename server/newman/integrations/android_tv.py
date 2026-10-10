"""Android TV and Google TV (Chromecast with Google TV, onn, TCL / Hisense / Sony Google TVs, Shield…) over
the Android TV Remote protocol v2 - what the Google TV phone app's remote uses - with androidtvremote2, the
library behind Home Assistant's Android TV Remote integration.

Pairing: Newman makes its own certificate; the TV shows a 6-character code, typed into the connection's
settings. The certificate (kept in Newman's data folder, never a password) is what stays paired.
The TV pushes power, current app and volume. There's no app list and no input list in this protocol: apps
start by link or package name (a list of common ones is offered), and "Input" is a key.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import Field
from .tv_common import CODE, HOST, MAC, TvIntegration, items, park, unpark

log = logging.getLogger(__name__)

KEYMAP = {
    "power": "POWER", "home": "HOME", "back": "BACK", "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT",
    "right": "DPAD_RIGHT", "ok": "DPAD_CENTER", "menu": "MENU", "info": "INFO", "guide": "GUIDE",
    "play_pause": "MEDIA_PLAY_PAUSE", "play": "MEDIA_PLAY", "pause": "MEDIA_PAUSE", "stop": "MEDIA_STOP",
    "rewind": "MEDIA_REWIND", "fast_forward": "MEDIA_FAST_FORWARD", "next": "MEDIA_NEXT",
    "previous": "MEDIA_PREVIOUS", "volume_up": "VOLUME_UP", "volume_down": "VOLUME_DOWN", "mute": "VOLUME_MUTE",
    "channel_up": "CHANNEL_UP", "channel_down": "CHANNEL_DOWN", "input": "TV_INPUT", "search": "SEARCH",
}

# Package -> name: the apps offered by "Launch app" (and how the current app is named).
COMMON_APPS = {
    "com.google.android.youtube.tv": "YouTube", "com.netflix.ninja": "Netflix",
    "com.amazon.amazonvideo.livingroom": "Prime Video", "com.disney.disneyplus": "Disney+",
    "com.google.android.youtube.tvunplugged": "YouTube TV", "com.plexapp.android": "Plex",
    "com.spotify.tv.android": "Spotify", "com.apple.atve.androidtv.appletv": "Apple TV",
    "com.hbo.hbonow": "Max", "com.bell.cravetv": "Crave", "ca.cbc.android.cbctv": "CBC Gem",
    "com.teamsmart.videomanager.tv": "SmartTube", "org.jellyfin.androidtv": "Jellyfin",
    "com.google.android.tvlauncher": "Home", "com.google.android.apps.tv.launcherx": "Home",
}


def app_name(package: str | None) -> str | None:
    if not package:
        return None
    return COMMON_APPS.get(package) or package.rsplit(".", 1)[-1].replace("_", " ").title()


def volume_steps(current: int, maximum: int, target_percent: int) -> tuple[str, int]:
    """No absolute volume in this protocol: how many presses of which key reach `target_percent`."""
    maximum = maximum or 100
    target = round(target_percent * maximum / 100)
    diff = target - current
    return ("VOLUME_UP" if diff > 0 else "VOLUME_DOWN"), abs(diff)


class AndroidTvIntegration(TvIntegration):
    type = "android_tv"
    name = "Android TV / Google TV"
    description = ("A Google TV or Android TV (Chromecast, onn, TCL, Hisense, Sony, Shield…) on your network: on / "
                   "off, volume, apps, remote keys, what's open. Pair once with the code the TV shows; Newman keeps "
                   "only its own pairing certificate, never a password.")
    fields = [HOST, CODE, MAC,
              Field("apps", "More apps", "textarea", advanced=True, placeholder="Kodi = org.xbmc.kodi",
                    help="One per line: name = package name or link, added to the apps Newman offers.")]
    zeroconf = ["_androidtvremote2._tcp.local."]
    KEYMAP = KEYMAP
    wakes_itself = True

    @classmethod
    def from_discovery(cls, name: str, host: str, props: dict[str, str]) -> dict[str, Any] | None:
        return {"title": name.split("._androidtvremote2.")[0], "config": {"host": host}}

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._dir = self.ctx.data_dir / "integrations" / self.id
        self.remote: Any = None
        self._tasks: set = set()

    def make_remote(self) -> Any:
        from androidtvremote2 import AndroidTVRemote
        self._dir.mkdir(parents=True, exist_ok=True)
        return AndroidTVRemote("Newman", str(self._dir / "cert.pem"), str(self._dir / "key.pem"), self.host)

    def apps(self) -> dict[str, str]:
        out = {p: n for p, n in COMMON_APPS.items() if n != "Home"}
        for line in str(self.config.get("apps") or "").splitlines():
            name, sep, target = line.partition("=")
            if sep and name.strip() and target.strip():
                out[target.strip()] = name.strip()
        return out

    # -- pairing ------------------------------------------------------------------------------------

    async def pair(self) -> bool:
        """True once paired. Otherwise asks the TV to show a code and parks the session for the save."""
        code = str(self.config.get("code") or "").strip()
        parked = unpark(self.id)
        if code and parked is not None:
            self.forget("code")
            try:
                await parked.async_finish_pairing(code)
                return True
            except Exception as e:
                log.info("Android TV pairing: %s: %s", e.__class__.__name__, e)
                self.set_status("pairing", "That code didn't work - a new one is on the TV; enter it and save")
        remote = self.make_remote()
        await remote.async_generate_cert_if_missing()
        try:
            name, mac = await remote.async_get_name_and_mac()
            self.learn_mac(mac)
        except Exception:
            pass
        await remote.async_start_pairing()
        park(self.id, remote)
        if self.status != "pairing":
            self.set_status("pairing", "Enter the code the TV is showing, then save")
        return False

    # -- running ------------------------------------------------------------------------------------

    def _publish_now(self) -> None:
        r = self.remote
        vol = r.volume_info or {}
        attrs: dict[str, Any] = {"appId": r.current_app, "app": app_name(r.current_app),
                                 "apps": items(self.apps())}
        if vol:
            attrs["volume"] = round(100 * vol.get("level", 0) / (vol.get("max") or 100))
            attrs["muted"] = bool(vol.get("muted"))
        info = r.device_info or {}
        if info:
            attrs["manufacturer"], attrs["model"] = info.get("manufacturer"), info.get("model")
        task = asyncio.get_running_loop().create_task(self.publish("on" if r.is_on else "standby", **attrs))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def run(self) -> None:
        from androidtvremote2 import CannotConnect, ConnectionClosed, InvalidAuth
        self.ensure_device()
        attempt = 0
        while True:
            try:
                self.remote = self.make_remote()
                await self.remote.async_generate_cert_if_missing()
                try:
                    await self.remote.async_connect()
                except InvalidAuth:
                    if not await self.pair():
                        await asyncio.Event().wait()  # the save with the code restarts this
                    continue
                attempt = 0
                for add in ("add_is_on_updated_callback", "add_current_app_updated_callback",
                            "add_volume_info_updated_callback", "add_is_available_updated_callback"):
                    getattr(self.remote, add)(lambda *_: self._publish_now())
                self.remote.keep_reconnecting()
                self._publish_now()
                info = self.remote.device_info or {}
                self.set_status("connected", f"{info.get('manufacturer', '')} {info.get('model', '')}".strip()
                                + " · live updates")
                self.learn_mac()
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if self.remote:
                    self.remote.disconnect()
                raise
            except (CannotConnect, ConnectionClosed, OSError) as e:
                log.debug("Android TV %s: %s", self.host, e)
                await self.mark_off()
                self.set_status("error", f"The TV isn't answering at {self.host}")
                attempt += 1
                await self.backoff_sleep(attempt + 2)

    # -- commands -----------------------------------------------------------------------------------

    async def do_key(self, key: str) -> None:
        self.remote.send_key_command(KEYMAP[key])

    async def do_power_on(self) -> None:
        if not self.remote.is_on:
            self.remote.send_key_command("POWER")

    async def do_power_off(self) -> None:
        if self.remote.is_on:
            self.remote.send_key_command("POWER")

    async def do_set_volume(self, level: int) -> None:
        vol = self.remote.volume_info
        if not vol:
            raise NotImplementedError
        key, n = volume_steps(int(vol.get("level", 0)), int(vol.get("max") or 100), level)
        for _ in range(min(n, 100)):
            self.remote.send_key_command(key)
            await asyncio.sleep(0.05)

    async def do_launch_app(self, app_id: str) -> None:
        self.remote.send_launch_app_command(app_id)
