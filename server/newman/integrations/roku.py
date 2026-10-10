"""Roku TVs and players (Roku, TCL Roku TV, Hisense Roku TV, onn Roku…) over Roku's External Control
Protocol: plain HTTP on port 8060, no pairing and no account. Hand-written (a few GETs and POSTs) rather
than a library - rokuecp, the one Home Assistant uses, adds nothing we need.

State is polled: /query/device-info (power mode, name, MACs), /query/active-app, and - when the Roku allows
it - /query/apps and /query/media-player. Roku TVs set to "Control by mobile apps: Limited" (the default
on recent Roku OS) refuse the app list, media player and app launching with 403; keys still work.
Power: the PowerOn / PowerOff keys (a Roku TV in standby keeps listening); Wake-on-LAN as fallback.
"""
from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

import aiohttp

from .base import CommandError
from .tv_common import HOST, MAC, POLL, TvIntegration, items

log = logging.getLogger(__name__)

PORT = 8060

# Newman key -> ECP key name.
KEYMAP = {
    "power": "Power", "home": "Home", "back": "Back", "up": "Up", "down": "Down", "left": "Left",
    "right": "Right", "ok": "Select", "info": "Info", "play_pause": "Play", "rewind": "Rev",
    "fast_forward": "Fwd", "volume_up": "VolumeUp", "volume_down": "VolumeDown", "mute": "VolumeMute",
    "channel_up": "ChannelUp", "channel_down": "ChannelDown", "search": "Search",
}

# A Roku TV's inputs: they're keys (InputHDMI1…) and show up as the active app "tvinput.<id>".
TV_SOURCES = [("tuner", "Live TV"), ("hdmi1", "HDMI 1"), ("hdmi2", "HDMI 2"), ("hdmi3", "HDMI 3"),
              ("hdmi4", "HDMI 4"), ("av1", "AV")]
SOURCE_KEYS = {"tuner": "InputTuner", "hdmi1": "InputHDMI1", "hdmi2": "InputHDMI2", "hdmi3": "InputHDMI3",
               "hdmi4": "InputHDMI4", "av1": "InputAV1"}
TV_INPUT_APPS = {"tvinput.dtv": "tuner", "tvinput.hdmi1": "hdmi1", "tvinput.hdmi2": "hdmi2",
                 "tvinput.hdmi3": "hdmi3", "tvinput.hdmi4": "hdmi4", "tvinput.cvbs": "av1"}

MEDIA_STATES = {"play": "playing", "pause": "paused", "buffer": "playing", "stop": "idle", "close": "idle",
                "open": "idle", "startup": "idle"}

LIMITED = ("Roku's “Control by mobile apps” is set to Limited, so the app list, what's playing and starting "
           "apps aren't available. On the TV: Settings › System › Advanced system settings › Control by mobile "
           "apps › Network access › Permissive.")


class Forbidden(Exception):
    pass


def _text(root: ET.Element, tag: str) -> str:
    el = root.find(tag)
    return (el.text or "").strip() if el is not None and el.text else ""


def parse_device_info(xml: str) -> dict[str, Any]:
    r = ET.fromstring(xml)
    is_tv = _text(r, "is-tv") == "true"
    mode = _text(r, "power-mode") or "PowerOn"
    wired = _text(r, "network-type") == "ethernet"
    mac = _text(r, "ethernet-mac") if wired else _text(r, "wifi-mac")
    return {
        "name": _text(r, "user-device-name") or _text(r, "friendly-device-name") or _text(r, "friendly-model-name")
        or "Roku",
        "manufacturer": _text(r, "vendor-name") or "Roku",
        "model": " ".join(x for x in (_text(r, "model-name"), _text(r, "model-number")) if x),
        "firmware": _text(r, "software-version"),
        "isTv": is_tv,
        # A Roku player has no screen of its own: when it answers, it's on.
        "state": "on" if mode == "PowerOn" or not is_tv else "standby",
        "powerMode": mode,
        "mac": mac or _text(r, "wifi-mac") or _text(r, "ethernet-mac"),
        "limited": _text(r, "ecp-setting-mode") == "limited",
        "id": _text(r, "udn") or _text(r, "serial-number"),
    }


def parse_active_app(xml: str) -> dict[str, Any]:
    r = ET.fromstring(xml)
    app = r.find("app")
    out: dict[str, Any] = {"appId": None, "app": None, "screensaver": r.find("screensaver") is not None}
    if app is not None:
        app_id = app.get("id") or ""
        out["appId"] = app_id or None
        out["app"] = (app.text or "").strip() or None
        if app.get("type") == "home" or not app_id:
            out["appId"], out["app"] = "home", "Home"
    return out


def parse_apps(xml: str) -> list[dict[str, str]]:
    r = ET.fromstring(xml)
    return items((a.get("id"), (a.text or "").strip()) for a in r.findall("app")
                 if a.get("type") in (None, "appl", "menu") and not str(a.get("id", "")).startswith("tvinput."))


def parse_media_player(xml: str) -> dict[str, Any]:
    r = ET.fromstring(xml)
    plugin = r.find("plugin")
    return {"mediaState": MEDIA_STATES.get(r.get("state") or "", "idle"),
            "mediaApp": plugin.get("name") if plugin is not None else None}


class RokuIntegration(TvIntegration):
    type = "roku"
    name = "Roku TV"
    description = ("A Roku TV or Roku player (Roku, TCL, Hisense, onn…) on your network: on / off, volume, inputs, "
                   "apps, remote keys. No pairing and no Roku account - Roku's local control is open to your "
                   "network, so Newman keeps nothing secret for it.")
    fields = [HOST, MAC, POLL]
    zeroconf = ["_airplay._tcp.local."]
    KEYMAP = KEYMAP
    wakes_itself = True
    port = PORT

    @classmethod
    def from_discovery(cls, name: str, host: str, props: dict[str, str]) -> dict[str, Any] | None:
        # Roku TVs announce AirPlay with integrator=Roku (seen live on a Hisense Roku TV); that tells them apart
        # from a VIDAA Hisense or a Samsung, which announce AirPlay too.
        if (props.get("integrator") or "").lower() != "roku":
            return None
        title = name.split("._airplay.")[0]
        maker = props.get("manufacturer") or "Roku"
        return {"title": f"{title} ({maker} Roku TV)" if maker.lower() != "roku" else f"{title} (Roku)",
                "config": {"host": host}}

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._session: aiohttp.ClientSession | None = None
        self._is_tv = True
        self._limited = False

    # -- HTTP ---------------------------------------------------------------------------------------

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6))
        return self._session

    async def _get(self, path: str) -> str:
        async with self._http().get(f"http://{self.host}:{self.port}{path}") as r:
            if r.status == 403:
                raise Forbidden(path)
            r.raise_for_status()
            return await r.text()

    async def _post(self, path: str) -> None:
        async with self._http().post(f"http://{self.host}:{self.port}{path}", data=b"") as r:
            if r.status == 403:
                raise Forbidden(path)
            r.raise_for_status()

    # -- state --------------------------------------------------------------------------------------

    async def refresh(self) -> dict[str, Any]:
        info = parse_device_info(await self._get("/query/device-info"))
        self._is_tv, self._limited = info["isTv"], info["limited"]
        self.learn_mac(info["mac"])
        attrs: dict[str, Any] = {"manufacturer": info["manufacturer"], "model": info["model"],
                                 "firmware": info["firmware"], "powerMode": info["powerMode"]}
        attrs["sources"] = items(TV_SOURCES) if info["isTv"] else []
        if info["state"] == "on":
            active = parse_active_app(await self._get("/query/active-app"))
            app_id = active["appId"] or ""
            attrs["appId"] = app_id or None
            if app_id.startswith("tvinput."):
                # An input is open: the app's name is the input's own name (renamed on the TV, e.g. "AppleTV").
                attrs["source"] = active["app"] or dict(TV_SOURCES).get(TV_INPUT_APPS.get(app_id, ""), app_id)
                attrs["app"] = None
            else:
                attrs["source"], attrs["app"] = "Roku", active["app"]
            try:
                attrs["apps"] = parse_apps(await self._get("/query/apps"))
                attrs.update(parse_media_player(await self._get("/query/media-player")))
                attrs["limited"] = None
            except Forbidden:
                attrs["limited"] = LIMITED
        await self.publish(info["state"], name=info["name"], **attrs)
        return info

    async def run(self) -> None:
        self.ensure_device()
        attempt = 0
        try:
            while True:
                try:
                    info = await self.refresh()
                    attempt = 0
                    where = f"{info['manufacturer']} {info['model']}".strip()
                    self.set_status("connected", f"{where} · on your network" + (" · app list off (Limited mode)"
                                                                                if info["limited"] else ""))
                    await asyncio.sleep(self.poll_seconds)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.debug("Roku %s: %s: %s", self.host, e.__class__.__name__, e)
                    await self.mark_off()
                    self.set_status("connected" if self.config.get("mac") else "error",
                                    f"The TV isn't answering at {self.host} (off, or its address changed)")
                    attempt += 1
                    await asyncio.sleep(min(self.poll_seconds * (1 + attempt), 60))
        finally:
            if self._session:
                await self._session.close()

    # -- commands -----------------------------------------------------------------------------------

    async def do_key(self, key: str) -> None:
        await self._post(f"/keypress/{KEYMAP[key]}")

    async def do_power_on(self) -> None:
        await self._post("/keypress/PowerOn")

    async def do_power_off(self) -> None:
        await self._post("/keypress/PowerOff")

    async def do_select_source(self, source_id: str) -> None:
        if source_id not in SOURCE_KEYS:
            raise ValueError(f"unknown input {source_id}")
        await self._post(f"/keypress/{SOURCE_KEYS[source_id]}")

    async def do_launch_app(self, app_id: str) -> None:
        try:
            await self._post(f"/launch/{quote(app_id, safe='')}")
        except Forbidden:
            raise CommandError(LIMITED) from None
