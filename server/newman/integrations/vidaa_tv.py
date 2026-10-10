"""Hisense / Toshiba TVs running VIDAA (not Hisense Roku TVs or Google TVs - those have their own connections).

VIDAA TVs run an MQTT broker on port 36669 (TLS) that Hisense's RemoteNOW / VIDAA phone app talks to. There is no
official documentation; this follows what the community worked out (the hisense_tv Home Assistant custom
integration, vidaa-control, and Hisense-TV MQTT write-ups):
- broker user "hisenseservice", password "multimqttservice" - fixed values baked into every TV and the app,
  not anything of yours;
- many 2020+ sets also want the app's TLS client certificate. Newman doesn't ship it: put the community's
  certificate and key files in Newman's data folder and give their paths under "More settings";
- pairing: the TV shows a 4-digit code once; after that the TV trusts Newman's client id, which is saved
  (together with any token the TV issues) - the code itself is not kept;
- commands are MQTT messages: /remoteapp/tv/<service>/<client id>/actions/<action>; answers come back on
  /remoteapp/mobile/<client id>/… and /remoteapp/mobile/broadcast/….
Some 2023+ firmware moved to per-device, time-based broker passwords; those sets aren't supported yet.
Off = the broker is gone: Wake-on-LAN (or KEY_POWER while it's in its "fake sleep" standby).
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
from typing import Any

from .base import Field
from .tv_common import CODE, HOST, MAC, TvIntegration, items

log = logging.getLogger(__name__)

PORT = 36669
USER, PASSWORD = "hisenseservice", "multimqttservice"

KEYMAP = {
    "power": "KEY_POWER", "home": "KEY_HOME", "back": "KEY_RETURNS", "up": "KEY_UP", "down": "KEY_DOWN",
    "left": "KEY_LEFT", "right": "KEY_RIGHT", "ok": "KEY_OK", "menu": "KEY_MENU", "info": "KEY_INFO",
    "play": "KEY_PLAY", "pause": "KEY_PAUSE", "stop": "KEY_STOP", "rewind": "KEY_BACKS", "fast_forward": "KEY_FORWARDS",
    "volume_up": "KEY_VOLUMEUP", "volume_down": "KEY_VOLUMEDOWN", "mute": "KEY_MUTE",
    "channel_up": "KEY_CHANNELUP", "channel_down": "KEY_CHANNELDOWN", "input": "KEY_INPUT",
}


def topic(service: str, client_id: str, action: str, kind: str = "actions") -> str:
    return f"/remoteapp/tv/{service}/{client_id}/{kind}/{action}"


def map_tv_state(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The TV's "state" broadcast -> (state, attrs). statetype: sourceswitch | app | livetv | fake_sleep_0…"""
    st = str(data.get("statetype") or "")
    if st.startswith("fake_sleep"):
        return "standby", {}
    if st == "sourceswitch":
        return "on", {"source": data.get("displayname") or data.get("sourcename"), "app": None,
                      "sourceId": data.get("sourceid")}
    if st == "app":
        return "on", {"app": data.get("name"), "appId": data.get("url") or data.get("name")}
    if st == "livetv":
        return "on", {"source": "TV", "app": None, "mediaTitle": data.get("progname") or None}
    return "on", {}


class VidaaTvIntegration(TvIntegration):
    type = "vidaa_tv"
    name = "Hisense TV (VIDAA)"
    description = ("A Hisense or Toshiba TV running VIDAA: on / off, volume, inputs, apps, remote keys. Pair once with "
                   "the code the TV shows; Newman keeps only the id the TV then trusts (and its token), never a "
                   "password. Hisense Roku TVs use the Roku connection, Hisense Google TVs Android TV / Google TV. "
                   "Built from community research and untested on a real set.")
    fields = [HOST, CODE, MAC,
              Field("paired", "Paired", "boolean", default=False, advanced=True,
                    help="Ticked automatically once the TV accepts the code. Untick to pair again."),
              Field("client_id", "Newman's id on the TV", advanced=True,
                    help="Made up the first time and trusted by the TV after pairing. Clear it to pair again."),
              Field("token", "TV's token for Newman", "password", advanced=True,
                    help="Filled in automatically if the TV hands one out after pairing."),
              Field("certfile", "Client certificate file", advanced=True, placeholder="/data/vidaa/client.cer",
                    help="Only for TVs that insist on the phone app's certificate (see the README)."),
              Field("keyfile", "Client key file", advanced=True, placeholder="/data/vidaa/client.pkcs8")]
    zeroconf = ["_airplay._tcp.local."]
    KEYMAP = KEYMAP
    wakes_itself = True

    @classmethod
    def from_discovery(cls, name: str, host: str, props: dict[str, str]) -> dict[str, Any] | None:
        # Hisense Roku TVs announce AirPlay too, with integrator=Roku: those are the Roku connection's.
        maker = (props.get("manufacturer") or "").lower()
        if not maker.startswith(("hisense", "toshiba")) or (props.get("integrator") or "").lower() == "roku":
            return None
        return {"title": f"{name.split('._airplay.')[0]} (VIDAA)", "config": {"host": host}}

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.client: Any = None
        self.cid = str(self.config.get("client_id") or "")
        self._paired = asyncio.Event()
        self._apps: dict[str, dict[str, Any]] = {}
        self._sources: dict[str, dict[str, Any]] = {}

    def tls_context(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # the TV's certificate is self-signed
        if self.config.get("certfile"):
            ctx.load_cert_chain(self.config["certfile"], self.config.get("keyfile") or None)
        return ctx

    def make_client(self) -> Any:
        import aiomqtt
        return aiomqtt.Client(self.host, PORT, username=USER, password=PASSWORD, identifier=self.cid,
                              tls_context=self.tls_context(), keepalive=30, timeout=10)

    async def send(self, service: str, action: str, payload: Any = "", kind: str = "actions") -> None:
        body = payload if isinstance(payload, str) else json.dumps(payload)
        await self.client.publish(topic(service, self.cid, action, kind), body)

    # -- incoming -----------------------------------------------------------------------------------

    async def handle(self, t: str, raw: bytes | str) -> None:
        text = raw.decode(errors="replace") if isinstance(raw, bytes) else raw
        try:
            data: Any = json.loads(text) if text.strip() else {}
        except ValueError:
            data = text
        if t.endswith("/ui_service/state"):
            state, attrs = map_tv_state(data if isinstance(data, dict) else {})
            await self.publish(state, **attrs)
        elif t.endswith("/actions/volumechange") and isinstance(data, dict):
            await self.publish(volume=int(data.get("volume_value", 0)))
        elif t.endswith("/data/sourcelist") and isinstance(data, list):
            self._sources = {str(s.get("sourceid")): s for s in data if s.get("sourceid") is not None}
            await self.publish(sources=items((i, s.get("displayname") or s.get("sourcename"))
                                             for i, s in self._sources.items()))
        elif t.endswith("/data/applist") and isinstance(data, list):
            self._apps = {str(a.get("appId") or a.get("name")): a for a in data}
            await self.publish(apps=items((i, a.get("name")) for i, a in self._apps.items()))
        elif t.endswith("/data/authenticationcode") and isinstance(data, dict):
            if str(data.get("result")) == "1":
                self._paired.set()
                self.remember(paired=True)
                self.set_status("connected", "Paired · Hisense VIDAA · on your network")
                await self.send("platform_service", "gettoken", {"refreshtoken": ""}, kind="data")
        elif t.endswith("/data/tokenissuance") and isinstance(data, dict):
            self.remember(token=str(data.get("accesstoken") or ""))

    async def ask_for_everything(self) -> None:
        await self.send("ui_service", "gettvstate")
        await self.send("platform_service", "getvolume")
        await self.send("ui_service", "sourcelist")
        await self.send("ui_service", "applist")

    # -- running ------------------------------------------------------------------------------------

    async def run(self) -> None:
        import aiomqtt
        if not self.cid:
            self.cid = f"newman-{self.ctx.install_id}-{self.id}"[:23]
            self.remember(client_id=self.cid)
        self.ensure_device()
        attempt = 0
        while True:
            try:
                async with self.make_client() as client:
                    self.client = client
                    await client.subscribe("/remoteapp/mobile/#")
                    paired = bool(self.config.get("paired"))
                    code = str(self.config.get("code") or "").strip()
                    if code:
                        self.forget("code")
                        await self.send("ui_service", "authenticationcode", {"authNum": int(code) if code.isdigit()
                                                                            else code})
                    elif not paired:
                        await self.send("ui_service", "vidaa_app_connect",
                                        {"app_version": 2, "connect_result": 0, "device_type": "Mobile App"})
                        self.set_status("pairing", "Enter the code the TV is showing, then save")
                    await self.ask_for_everything()
                    attempt = 0
                    if self.status != "pairing":
                        self.set_status("connected", "Hisense VIDAA · on your network")
                    self.learn_mac()
                    async for msg in client.messages:
                        await self.handle(str(msg.topic), msg.payload)
            except asyncio.CancelledError:
                raise
            except (aiomqtt.MqttError, OSError, ssl.SSLError) as e:
                log.debug("VIDAA %s: %s", self.host, e)
                self.client = None
                await self.mark_off()
                if self.status != "pairing":
                    self.set_status("connected" if self.config.get("mac") else "error",
                                    f"The TV isn't answering at {self.host} (off, or it wants the client "
                                    "certificate - see the README)")
                attempt += 1
                await self.backoff_sleep(attempt + 2)

    # -- commands -----------------------------------------------------------------------------------

    async def do_key(self, key: str) -> None:
        await self.send("remote_service", "sendkey", KEYMAP[key])

    async def do_power_on(self) -> None:
        if self.client is None:
            raise ConnectionError("not connected")
        await self.do_key("power")

    async def do_set_volume(self, level: int) -> None:
        await self.send("platform_service", "changevolume", str(level))

    async def do_select_source(self, source_id: str) -> None:
        s = self._sources.get(source_id) or {"sourceid": source_id}
        await self.send("ui_service", "changesource", {"sourceid": s.get("sourceid"),
                                                       "sourcename": s.get("sourcename") or s.get("displayname")})

    async def do_launch_app(self, app_id: str) -> None:
        a = self._apps.get(app_id) or {"appId": app_id, "name": app_id, "url": app_id}
        await self.send("ui_service", "launchapp", {"appId": a.get("appId"), "name": a.get("name"),
                                                    "url": a.get("url")})
