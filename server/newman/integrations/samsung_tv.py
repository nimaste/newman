"""Samsung TVs (Tizen, 2016 and later) over the TV's own remote-control websocket - what the SmartThings
app's remote and Home Assistant's samsungtv integration (samsungtvws) use. Hand-written on aiohttp: it's
one websocket and a few JSON messages, and samsungtvws would add a second websocket library.

- Info and power state: REST, http://TV:8001/api/v2/ (answers in standby on 2019+ sets: PowerState).
- Control: wss://TV:8002/api/v2/channels/samsung.remote.control?name=…&token=…. The first time, the TV
  shows "Allow Newman to connect?"; once allowed it sends a token, which is all Newman keeps.
- Keys are KEY_* codes; apps come from "ed.installedApp.get" and start with "ed.apps.launch".
- Volume level: UPnP RenderingControl on port 9197 (the websocket only has up / down).
- On: the websocket is gone when the TV is fully off, so Wake-on-LAN (the TV's "Power On with Mobile"
  setting must be on); in standby, KEY_POWER.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Any

import aiohttp

from .base import CommandError, Field
from .tv_common import HOST, MAC, POLL, NeedsPairing, TvIntegration, items, wake_on_lan

log = logging.getLogger(__name__)

CLIENT_NAME = "Newman"

KEYMAP = {
    "power": "KEY_POWER", "home": "KEY_HOME", "back": "KEY_RETURN", "up": "KEY_UP", "down": "KEY_DOWN",
    "left": "KEY_LEFT", "right": "KEY_RIGHT", "ok": "KEY_ENTER", "menu": "KEY_MENU", "info": "KEY_INFO",
    "guide": "KEY_GUIDE", "play": "KEY_PLAY", "pause": "KEY_PAUSE", "stop": "KEY_STOP", "rewind": "KEY_REWIND",
    "fast_forward": "KEY_FF", "volume_up": "KEY_VOLUP", "volume_down": "KEY_VOLDOWN", "mute": "KEY_MUTE",
    "channel_up": "KEY_CHUP", "channel_down": "KEY_CHDOWN", "input": "KEY_SOURCE",
}
SOURCES = [("tv", "TV"), ("hdmi1", "HDMI 1"), ("hdmi2", "HDMI 2"), ("hdmi3", "HDMI 3"), ("hdmi4", "HDMI 4")]
SOURCE_KEYS = {"tv": "KEY_TV", "hdmi1": "KEY_HDMI1", "hdmi2": "KEY_HDMI2", "hdmi3": "KEY_HDMI3",
               "hdmi4": "KEY_HDMI4"}

RC_URL = "/upnp/control/RenderingControl1"
RC_NS = "urn:schemas-upnp-org:service:RenderingControl:1"


def ws_url(host: str, token: str = "", scheme: str = "wss", port: int = 8002) -> str:
    name = base64.b64encode(CLIENT_NAME.encode()).decode()
    url = f"{scheme}://{host}:{port}/api/v2/channels/samsung.remote.control?name={name}"
    return url + (f"&token={token}" if token else "")


def key_message(code: str) -> dict[str, Any]:
    return {"method": "ms.remote.control",
            "params": {"Cmd": "Click", "DataOfCmd": code, "Option": "false", "TypeOfRemote": "SendRemoteKey"}}


def apps_request() -> dict[str, Any]:
    return {"method": "ms.channel.emit", "params": {"event": "ed.installedApp.get", "to": "host"}}


def launch_message(app_id: str, app_type: Any) -> dict[str, Any]:
    # app_type 2 = a web app opened by deep link; 4 (and the rest) = native.
    return {"method": "ms.channel.emit",
            "params": {"event": "ed.apps.launch", "to": "host",
                       "data": {"appId": app_id, "action_type": "DEEP_LINK" if str(app_type) == "2" else "NATIVE_LAUNCH"}}}


def parse_info(j: dict[str, Any]) -> dict[str, Any]:
    d = j.get("device") or {}
    power = str(d.get("PowerState") or "on").lower()
    return {"name": d.get("name") or j.get("name") or "Samsung TV", "model": d.get("modelName") or j.get("type") or "",
            "state": "on" if power == "on" else "standby", "mac": d.get("wifiMac") or "",
            "tokenAuth": str(d.get("TokenAuthSupport", "true")).lower() == "true"}


def soap_body(action: str, args: str = "") -> str:
    return ('<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{RC_NS}"><InstanceID>0</InstanceID><Channel>Master</Channel>{args}</u:{action}>'
            '</s:Body></s:Envelope>')


class SamsungTvIntegration(TvIntegration):
    type = "samsung_tv"
    name = "Samsung TV"
    description = ("A Samsung smart TV (Tizen, 2016 or newer) on your network: on / off, volume, inputs, apps, "
                   "remote keys. The first time, allow Newman on the TV's screen; Newman keeps only the key the TV "
                   "hands back, never a password. Turning it on needs “Power On with Mobile” on the TV.")
    fields = [HOST, MAC,
              Field("token", "TV's key for Newman", "password", advanced=True,
                    help="Filled in automatically when you allow Newman on the TV. Clear it to pair again."),
              POLL]
    zeroconf = ["_airplay._tcp.local."]
    KEYMAP = KEYMAP
    # Fixed by the TV; class attributes so tests can point them at a fake.
    ws_scheme, ws_port, rest_port, upnp_port = "wss", 8002, 8001, 9197

    @classmethod
    def from_discovery(cls, name: str, host: str, props: dict[str, str]) -> dict[str, Any] | None:
        if not (props.get("manufacturer") or "").lower().startswith("samsung"):
            return None
        title = name.split("._airplay.")[0]
        model = props.get("model") or ""
        return {"title": f"{title} ({model})" if model and model not in title else title, "config": {"host": host}}

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        self._apps: dict[str, Any] = {}  # appId -> app_type
        self._state = "off"

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))
        return self._session

    # -- websocket ----------------------------------------------------------------------------------

    async def _connect(self, wait_for_allow: float = 45) -> None:
        """Open the remote websocket; the first time the TV asks its owner to allow Newman."""
        if self._ws and not self._ws.closed:
            return
        token = str(self.config.get("token") or "")
        if not token:
            self.set_status("pairing", "Allow Newman on the TV - a prompt is showing on its screen")
        ws = await self._http().ws_connect(ws_url(self.host, token, self.ws_scheme, self.ws_port), ssl=False, heartbeat=30,
                                           timeout=aiohttp.ClientWSTimeout(ws_receive=wait_for_allow))
        try:
            msg = await ws.receive_json(timeout=wait_for_allow if not token else 10)
        except Exception:
            await ws.close()
            raise
        event = msg.get("event")
        if event == "ms.channel.unauthorized":
            await ws.close()
            self.forget("token")
            raise NeedsPairing("The TV refused Newman. Allow it on the TV (Settings › General › External "
                               "Device Manager › Device Connection Manager) and save again")
        if event != "ms.channel.connect":
            await ws.close()
            raise ConnectionError(f"unexpected first message {event!r}")
        new_token = (msg.get("data") or {}).get("token")
        if new_token:
            self.remember(token=str(new_token))
        self._ws = ws
        self._reader = asyncio.get_running_loop().create_task(self._read(ws))
        await self._send(apps_request())

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for m in ws:
            if m.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                await self.handle(json.loads(m.data))
            except Exception:
                log.debug("Samsung message", exc_info=True)

    async def handle(self, msg: dict[str, Any]) -> None:
        if msg.get("event") == "ed.installedApp.get":
            apps = ((msg.get("data") or {}).get("data")) or []
            self._apps = {a.get("appId"): a.get("app_type") for a in apps if a.get("appId")}
            await self.publish(apps=items((a.get("appId"), a.get("name")) for a in apps))

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self._ws or self._ws.closed:
            await self._connect()
        assert self._ws is not None
        await self._ws.send_str(json.dumps(payload))

    async def _close_ws(self) -> None:
        if self._reader:
            self._reader.cancel()
            self._reader = None
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None

    # -- volume (UPnP) ------------------------------------------------------------------------------

    async def _soap(self, action: str, args: str = "") -> str:
        headers = {"Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": f'"{RC_NS}#{action}"'}
        async with self._http().post(f"http://{self.host}:{self.upnp_port}{RC_URL}", data=soap_body(action, args),
                                     headers=headers) as r:
            r.raise_for_status()
            return await r.text()

    async def read_volume(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        try:
            if m := re.search(r"<CurrentVolume>(\d+)</CurrentVolume>", await self._soap("GetVolume")):
                out["volume"] = int(m.group(1))
            if m := re.search(r"<CurrentMute>(\d)</CurrentMute>", await self._soap("GetMute")):
                out["muted"] = m.group(1) == "1"
        except Exception as e:
            log.debug("Samsung volume: %s", e)
        return out

    # -- running ------------------------------------------------------------------------------------

    async def refresh(self) -> dict[str, Any]:
        async with self._http().get(f"http://{self.host}:{self.rest_port}/api/v2/") as r:
            r.raise_for_status()
            info = parse_info(await r.json(content_type=None))
        self.learn_mac(info["mac"])
        self._state = info["state"]
        attrs: dict[str, Any] = {"model": info["model"], "manufacturer": "Samsung", "sources": items(SOURCES)}
        if info["state"] == "on":
            await self._connect()
            attrs.update(await self.read_volume())
        else:
            await self._close_ws()
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
                    self.set_status("connected", f"Samsung {info['model']} · on your network".replace("  ", " "))
                    await asyncio.sleep(self.poll_seconds)
                except asyncio.CancelledError:
                    raise
                except NeedsPairing as e:
                    self.set_status("error", str(e))
                    await asyncio.Event().wait()  # saving the settings restarts this
                except Exception as e:
                    log.debug("Samsung %s: %s: %s", self.host, e.__class__.__name__, e)
                    await self._close_ws()
                    self._state = "off"
                    await self.mark_off()
                    if self.status == "pairing":
                        self.set_status("pairing", "Waiting for the TV - switch it on and allow Newman when asked")
                    else:
                        self.set_status("connected" if self.config.get("mac") else "error",
                                        f"The TV isn't answering at {self.host} (off, or its address changed)")
                    attempt += 1
                    await asyncio.sleep(min(self.poll_seconds * (1 + attempt), 60))
        finally:
            await self._close_ws()
            if self._session:
                await self._session.close()

    # -- commands -----------------------------------------------------------------------------------

    async def turn_on(self) -> None:
        if self._state == "standby":
            await self.do_key("power")
            await self.publish("on")
            return
        if not self.config.get("mac"):
            raise CommandError("A Samsung TV that's fully off can only be woken with its network (MAC) address - "
                               "add it in the connection's settings")
        await wake_on_lan(self.config["mac"], self.host)

    async def do_key(self, key: str) -> None:
        await self._send(key_message(KEYMAP[key]))

    async def do_set_volume(self, level: int) -> None:
        await self._soap("SetVolume", f"<DesiredVolume>{level}</DesiredVolume>")

    async def do_select_source(self, source_id: str) -> None:
        await self._send(key_message(SOURCE_KEYS[source_id]))

    async def do_launch_app(self, app_id: str) -> None:
        await self._send(launch_message(app_id, self._apps.get(app_id, 4)))
        await self.publish(appId=app_id)
