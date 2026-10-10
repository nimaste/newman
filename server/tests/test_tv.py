"""TV connections, offline: a fake Roku (answers recorded from a real Hisense Roku TV), a fake Samsung (REST +
remote websocket + UPnP), and fake library objects for LG webOS, Android TV, Apple TV and Hisense VIDAA."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp import web  # noqa: E402

from newman.integrations import REGISTRY  # noqa: E402
from newman.integrations import android_tv, apple_tv, roku, samsung_tv, tv_common, vidaa_tv, webos_tv  # noqa: E402
from newman.integrations.base import CommandError, Context, mask_config  # noqa: E402
from newman.kinds import KINDS, properties_for  # noqa: E402
from newman.state import Hub  # noqa: E402

# A real Hisense Roku TV (65U6, Roku OS 15.3), trimmed; ids and serials replaced.
ROKU_DEVICE_INFO = """<?xml version="1.0" encoding="UTF-8" ?>
<device-info>
	<udn>280010c0-0000-1000-8000-000000000001</udn>
	<serial-number>X000000000AA</serial-number>
	<vendor-name>Hisense</vendor-name>
	<model-name>65U6</model-name>
	<model-number>G220X</model-number>
	<is-tv>true</is-tv>
	<is-stick>false</is-stick>
	<wifi-mac>80:cb:bc:00:00:01</wifi-mac>
	<ethernet-mac>a0:62:fb:00:00:02</ethernet-mac>
	<network-type>ethernet</network-type>
	<friendly-device-name>Basement TV</friendly-device-name>
	<friendly-model-name>Hisense•Roku TV</friendly-model-name>
	<user-device-name>Basement TV</user-device-name>
	<user-device-location>Basement</user-device-location>
	<software-version>15.3.4</software-version>
	<ecp-setting-mode>limited</ecp-setting-mode>
	<power-mode>PowerOn</power-mode>
	<supports-suspend>true</supports-suspend>
	<supports-warm-standby>true</supports-warm-standby>
	<supports-wake-on-wlan>true</supports-wake-on-wlan>
	<supports-airplay>true</supports-airplay>
</device-info>
"""
ROKU_ACTIVE_APP = """<?xml version="1.0" encoding="UTF-8" ?>
<active-app>
	<app id="562859" type="home" version="14.10.5" ui-location="home">Roku Dynamic Menu</app>
	<screensaver id="578850" type="ssvr" version="2.3.1">Western Screensaver</screensaver>
</active-app>
"""
ROKU_LIMITED = "ECP command not allowed in Limited mode."
# From Roku's ECP documentation (the real TV refuses /query/apps in Limited mode).
ROKU_APPS = """<?xml version="1.0" encoding="UTF-8" ?>
<apps>
	<app id="tvinput.hdmi1" type="tvin" version="1.0.0">HDMI 1</app>
	<app id="12" type="appl" version="5.2.0">Netflix</app>
	<app id="837" type="appl" version="2.21.1">YouTube</app>
</apps>
"""
ROKU_NETFLIX = """<?xml version="1.0" encoding="UTF-8" ?>
<active-app><app id="12" type="appl" version="5.2.0">Netflix</app></active-app>"""
ROKU_HDMI2 = """<?xml version="1.0" encoding="UTF-8" ?>
<active-app><app id="tvinput.hdmi2" type="tvin" version="1.0.0">AppleTV</app></active-app>"""
ROKU_PLAYER ="""<?xml version="1.0" encoding="UTF-8" ?><player error="false" state="pause"><plugin id="12" name="Netflix"/></player>"""

# AirPlay announcements seen on the network.
AIRPLAY_HISENSE_ROKU = {"model": "G220X", "integrator": "Roku", "manufacturer": "Hisense", "acl": "0"}
AIRPLAY_SAMSUNG = {"model": "QN65Q80BAFXZC", "manufacturer": "Samsung"}
AIRPLAY_LG = {"model": "OLED55C1PUB", "manufacturer": "LG Electronics"}
AIRPLAY_VIDAA = {"model": "65U8K", "manufacturer": "Hisense"}
APPLE_TV_COMPANION = {"rpMd": "AppleTV14,1", "rpVr": "715.2"}
MAC_COMPANION = {"rpFl": "0x20040", "rpVr": "715.2"}


def ctx(hub, tmp):
    return Context(hub, Path(tmp), Path(tmp))


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        self.saved = []

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def make(self, cls, config, iid=None):
        inst = cls(iid or cls.type, cls.name, dict(config), ctx(self.hub, self.tmp.name))
        inst.save_config = lambda cfg: self.saved.append(dict(cfg))
        return inst

    async def start(self, inst, wait=0.15):
        task = asyncio.create_task(inst.run())
        await asyncio.sleep(wait)
        return task

    async def stop(self, task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class CommonTests(Base):
    def test_kind_is_metadata(self):
        k = KINDS["tv"]
        self.assertEqual([s for s, _ in k.states], ["on", "standby", "off"])
        cmds = {c.id: c for c in k.commands}
        for cid in ("turn_on", "turn_off", "set_volume", "volume_up", "volume_down", "mute", "play", "pause",
                    "next", "previous", "select_source", "launch_app", "send_key"):
            self.assertIn(cid, cmds)
        self.assertEqual((cmds["select_source"].value_type, cmds["select_source"].attr), ("choices", "sources"))
        self.assertEqual(cmds["set_volume"].value_type, "percent")
        for cls in (roku.RokuIntegration, samsung_tv.SamsungTvIntegration, webos_tv.WebOsTvIntegration,
                    android_tv.AndroidTvIntegration, apple_tv.AppleTvIntegration, vidaa_tv.VidaaTvIntegration):
            self.assertIs(REGISTRY[cls.type], cls)
            self.assertEqual(cls.provides, ("tv",))
            self.assertTrue(set(cls.KEYMAP) <= set(tv_common.KEY_NAMES), cls.type)

    def test_magic_packet_and_mac(self):
        self.assertEqual(tv_common.normalise_mac("A0-62-FB-66-E5-23"), "a0:62:fb:66:e5:23")
        self.assertEqual(tv_common.normalise_mac("nope"), "")
        p = tv_common.magic_packet("a0:62:fb:66:e5:23")
        self.assertEqual(len(p), 102)
        self.assertEqual(p[:6], b"\xff" * 6)
        self.assertEqual(p[6:12], bytes.fromhex("a062fb66e523"))
        self.assertEqual(p[-6:], bytes.fromhex("a062fb66e523"))
        arp = Path(self.tmp.name) / "arp"
        arp.write_text("IP address  HW type  Flags  HW address  Mask  Device\n"
                       "10.1.1.243  0x1  0x2  a0:62:fb:66:e5:23  *  br0\n"
                       "10.1.1.9  0x1  0x0  00:00:00:00:00:00  *  br0\n")
        self.assertEqual(tv_common.mac_from_arp("10.1.1.243", arp), "a0:62:fb:66:e5:23")
        self.assertEqual(tv_common.mac_from_arp("10.1.1.9", arp), "")

    def test_wake_on_lan_sends_magic_packet(self):
        sent = []
        with mock.patch.object(tv_common, "_send_wol", lambda pkt, targets: sent.append((pkt, targets))):
            asyncio.run(tv_common.wake_on_lan("a0:62:fb:66:e5:23", "10.1.1.243"))
        pkt, targets = sent[0]
        self.assertEqual(pkt, tv_common.magic_packet("a0:62:fb:66:e5:23"))
        self.assertIn(("255.255.255.255", 9), targets)
        self.assertIn(("10.1.1.243", 9), targets)

    def test_discovery_picks_the_right_connection(self):
        def offers(type_, name, props):
            return {cls.type: cls.from_discovery(name, "10.1.1.243", props)
                    for cls in REGISTRY.values() if type_ in cls.zeroconf}
        o = offers("_airplay._tcp.local.", "Basement TV._airplay._tcp.local.", AIRPLAY_HISENSE_ROKU)
        self.assertEqual(o["roku"]["config"], {"host": "10.1.1.243"})
        self.assertIn("Hisense Roku TV", o["roku"]["title"])
        self.assertIsNone(o["vidaa_tv"])  # a Hisense Roku TV isn't a VIDAA set
        self.assertIsNone(o["samsung_tv"])
        self.assertIsNone(o["webos_tv"])
        o = offers("_airplay._tcp.local.", "Bedroom._airplay._tcp.local.", AIRPLAY_VIDAA)
        self.assertIsNotNone(o["vidaa_tv"])
        self.assertIsNone(o["roku"])
        o = offers("_airplay._tcp.local.", "Living Room._airplay._tcp.local.", AIRPLAY_SAMSUNG)
        self.assertEqual([k for k, v in o.items() if v], ["samsung_tv"])
        o = offers("_airplay._tcp.local.", "Den._airplay._tcp.local.", AIRPLAY_LG)
        self.assertEqual([k for k, v in o.items() if v], ["webos_tv"])
        # An AirPlay speaker / Mac: nobody.
        o = offers("_airplay._tcp.local.", "Mac._airplay._tcp.local.", {"model": "Mac14,2"})
        self.assertFalse(any(o.values()))
        self.assertEqual(apple_tv.AppleTvIntegration.from_discovery(
            "Home Theater._companion-link._tcp.local.", "10.1.1.44", APPLE_TV_COMPANION)["title"],
            "Home Theater (Apple TV)")
        self.assertIsNone(apple_tv.AppleTvIntegration.from_discovery("Mac._companion-link._tcp.local.", "x",
                                                                     MAC_COMPANION))
        self.assertEqual(android_tv.AndroidTvIntegration.from_discovery(
            "Living room TV._androidtvremote2._tcp.local.", "10.1.1.47", {}),
            {"title": "Living room TV", "config": {"host": "10.1.1.47"}})

    async def test_real_libraries_have_what_we_call(self):
        """The fakes below mirror these libraries; check the real ones still look like that."""
        import inspect
        import aiomqtt
        from androidtvremote2 import AndroidTVRemote
        from aiowebostv import WebOsClient
        from pyatv.interface import BaseConfig, PairingHandler
        v = self.make(vidaa_tv.VidaaTvIntegration, {"host": "10.0.0.7", "client_id": "newman-x"})
        self.assertIsInstance(v.make_client(), aiomqtt.Client)
        w = self.make(webos_tv.WebOsTvIntegration, {"host": "10.0.0.9", "client_key": "k"})
        c = w.make_client()
        self.assertIsInstance(c, WebOsClient)
        self.assertEqual(c.client_key, "k")
        for m in ("connect", "register_state_update_callback", "get_connection_info", "button", "set_volume",
                  "set_mute", "set_input", "launch_app", "power_on", "power_off", "play", "pause", "disconnect"):
            self.assertTrue(inspect.iscoroutinefunction(getattr(c, m)), m)
        self.assertTrue(hasattr(c, "tv_state") and hasattr(c, "tv_info"))
        a = self.make(android_tv.AndroidTvIntegration, {"host": "10.1.1.47"})
        r = a.make_remote()
        self.assertIsInstance(r, AndroidTVRemote)
        for m in ("send_key_command", "send_launch_app_command", "keep_reconnecting", "add_is_on_updated_callback",
                  "add_current_app_updated_callback", "add_volume_info_updated_callback", "async_start_pairing",
                  "async_finish_pairing", "async_get_name_and_mac"):
            self.assertTrue(hasattr(r, m), m)
        for m in ("get_service", "set_credentials"):
            self.assertTrue(hasattr(BaseConfig, m), m)
        for m in ("begin", "pin", "finish", "close", "has_paired", "service"):
            self.assertTrue(hasattr(PairingHandler, m), m)

    def test_pairing_sessions_park_and_expire(self):
        tv_common.park("x", "session")
        self.assertEqual(tv_common.unpark("x"), "session")
        self.assertIsNone(tv_common.unpark("x"))
        tv_common.park("y", "old")
        with mock.patch.object(tv_common.time, "monotonic", return_value=tv_common.time.monotonic() + 999):
            self.assertIsNone(tv_common.unpark("y"))


# -- Roku -------------------------------------------------------------------------------------------

class FakeRoku:
    def __init__(self, limited=True):
        self.limited, self.posts, self.app = limited, [], ROKU_ACTIVE_APP
        self.device_info = ROKU_DEVICE_INFO if limited else ROKU_DEVICE_INFO.replace(">limited<", ">default<")

    async def handler(self, request):
        path = request.path
        if request.method == "POST":
            if self.limited and path.startswith("/launch"):
                return web.Response(status=403, text=ROKU_LIMITED)
            self.posts.append(path)
            return web.Response(text="")
        if path == "/query/device-info":
            return web.Response(text=self.device_info, content_type="text/xml")
        if path == "/query/active-app":
            return web.Response(text=self.app, content_type="text/xml")
        if self.limited:
            return web.Response(status=403, text=ROKU_LIMITED)
        if path == "/query/apps":
            return web.Response(text=ROKU_APPS, content_type="text/xml")
        if path == "/query/media-player":
            return web.Response(text=ROKU_PLAYER, content_type="text/xml")
        return web.Response(status=404)


async def serve(handler):
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port


class RokuTests(Base):
    def test_parsers_on_real_answers(self):
        info = roku.parse_device_info(ROKU_DEVICE_INFO)
        self.assertEqual(info["name"], "Basement TV")
        self.assertEqual(info["manufacturer"], "Hisense")
        self.assertEqual(info["model"], "65U6 G220X")
        self.assertEqual(info["state"], "on")
        self.assertEqual(info["mac"], "a0:62:fb:00:00:02")  # wired: the ethernet MAC
        self.assertTrue(info["limited"])
        self.assertEqual(roku.parse_device_info(ROKU_DEVICE_INFO.replace("PowerOn", "DisplayOff"))["state"], "standby")
        self.assertEqual(roku.parse_active_app(ROKU_ACTIVE_APP), {"appId": "home", "app": "Home", "screensaver": True})
        self.assertEqual(roku.parse_active_app(ROKU_NETFLIX)["app"], "Netflix")
        self.assertEqual(roku.parse_apps(ROKU_APPS), [{"id": "12", "name": "Netflix"}, {"id": "837", "name": "YouTube"}])
        self.assertEqual(roku.parse_media_player(ROKU_PLAYER), {"mediaState": "paused", "mediaApp": "Netflix"})

    async def test_limited_mode_state_keys_and_mac(self):
        fake = FakeRoku(limited=True)
        runner, port = await serve(fake.handler)
        r = self.make(roku.RokuIntegration, {"host": "127.0.0.1", "poll_seconds": 1})
        r.port = port
        task = await self.start(r, 0.3)
        try:
            e = self.hub.get("roku.tv")
            self.assertEqual((e.kind, e.name, e.state), ("tv", "Basement TV", "on"))
            self.assertEqual(e.attrs["app"], "Home")
            self.assertEqual(e.attrs["source"], "Roku")
            self.assertIn({"id": "hdmi1", "name": "HDMI 1"}, e.attrs["sources"])
            self.assertIn("Limited", e.attrs["limited"])
            self.assertIn({"id": "home", "name": "Home"}, e.attrs["keys"])
            self.assertEqual(r.status, "connected")
            self.assertEqual(self.saved[-1]["mac"], "a0:62:fb:00:00:02")  # learned for Wake-on-LAN
            await r.command("roku.tv", "send_key", ["home"])
            await r.command("roku.tv", "volume_up")
            await r.command("roku.tv", "select_source", ["hdmi2"])
            await r.command("roku.tv", "turn_off")
            await r.command("roku.tv", "turn_on")
            await r.command("roku.tv", "play")
            self.assertEqual(fake.posts, ["/keypress/Home", "/keypress/VolumeUp", "/keypress/InputHDMI2",
                                          "/keypress/PowerOff", "/keypress/PowerOn", "/keypress/Play"])
            with self.assertRaises(CommandError) as cm:
                await r.command("roku.tv", "launch_app", ["12"])
            self.assertIn("Permissive", str(cm.exception))
            with self.assertRaises(CommandError):
                await r.command("roku.tv", "set_volume", 30)  # Roku has only up / down
            with self.assertRaises(CommandError):
                await r.command("roku.tv", "send_key", "guide")  # not a Roku key
        finally:
            await self.stop(task)
            await runner.cleanup()

    async def test_permissive_mode_apps_and_playback(self):
        fake = FakeRoku(limited=False)
        fake.app = ROKU_NETFLIX
        runner, port = await serve(fake.handler)
        r = self.make(roku.RokuIntegration, {"host": "127.0.0.1", "poll_seconds": 1})
        r.port = port
        task = await self.start(r, 0.3)
        try:
            e = self.hub.get("roku.tv")
            self.assertEqual(e.attrs["apps"], [{"id": "12", "name": "Netflix"}, {"id": "837", "name": "YouTube"}])
            self.assertEqual(e.attrs["mediaState"], "paused")
            self.assertIsNone(e.attrs["limited"])
            props = {p["id"]: p["value"] for p in properties_for(e)}
            self.assertEqual(props["app"], "Netflix")
            await r.command("roku.tv", "launch_app", ["837"])
            self.assertEqual(fake.posts, ["/launch/837"])
            # An input open (seen live: HDMI 2 renamed "AppleTV" on the TV).
            fake.app = ROKU_HDMI2
            await r.refresh()
            e = self.hub.get("roku.tv")
            self.assertEqual((e.attrs["source"], e.attrs["app"], e.attrs["appId"]), ("AppleTV", None, "tvinput.hdmi2"))
        finally:
            await self.stop(task)
            await runner.cleanup()

    async def test_unreachable_is_off_and_wakes_with_wol(self):
        r = self.make(roku.RokuIntegration, {"host": "127.0.0.1", "mac": "a0:62:fb:66:e5:23", "poll_seconds": 1})
        r.port = 1  # nothing listens
        task = await self.start(r, 0.3)
        woke = []

        async def fake_wol(mac, host=""):
            woke.append(mac)
        try:
            self.assertEqual(self.hub.get("roku.tv").state, "off")
            self.assertTrue(self.hub.get("roku.tv").available)
            with mock.patch.object(tv_common, "wake_on_lan", fake_wol):
                await r.command("roku.tv", "turn_on")
            self.assertEqual(woke, ["a0:62:fb:66:e5:23"])
        finally:
            await self.stop(task)


# -- Samsung ----------------------------------------------------------------------------------------

class FakeSamsung:
    def __init__(self, power="on", allow=True):
        self.power, self.allow, self.sent, self.soap, self.tokens_seen = power, allow, [], [], []

    async def handler(self, request):
        if request.path == "/api/v2/":
            return web.json_response({"device": {"PowerState": self.power, "name": "[TV] Samsung Q80",
                                                 "modelName": "QN65Q80B", "wifiMac": "64:1C:AE:00:00:01",
                                                 "TokenAuthSupport": "true"}, "name": "[TV] Samsung Q80"})
        if request.path.startswith("/upnp/"):
            body = await request.text()
            self.soap.append((request.headers.get("SOAPACTION"), body))
            if "GetVolume" in request.headers.get("SOAPACTION", ""):
                return web.Response(text="<CurrentVolume>17</CurrentVolume>")
            if "GetMute" in request.headers.get("SOAPACTION", ""):
                return web.Response(text="<CurrentMute>0</CurrentMute>")
            return web.Response(text="<ok/>")
        if request.path.startswith("/api/v2/channels/samsung.remote.control"):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            self.tokens_seen.append(request.query.get("token"))
            if not self.allow:
                await ws.send_json({"event": "ms.channel.unauthorized"})
                await ws.close()
                return ws
            data = {"clients": []}
            if not request.query.get("token"):
                data["token"] = "48151623"
            await ws.send_json({"event": "ms.channel.connect", "data": data})
            async for m in ws:
                msg = json.loads(m.data)
                self.sent.append(msg)
                if msg.get("params", {}).get("event") == "ed.installedApp.get":
                    await ws.send_json({"event": "ed.installedApp.get", "data": {"data": [
                        {"appId": "3201907018807", "name": "Netflix", "app_type": 2},
                        {"appId": "111299001912", "name": "YouTube", "app_type": 2},
                        {"appId": "org.tizen.browser", "name": "Internet", "app_type": 4}]}})
            return ws
        return web.Response(status=404)


class SamsungTests(Base):
    def tv(self, port, **cfg):
        s = self.make(samsung_tv.SamsungTvIntegration, {"host": "127.0.0.1", "poll_seconds": 1, **cfg})
        s.ws_scheme, s.ws_port, s.rest_port, s.upnp_port = "ws", port, port, port
        return s

    async def test_pairing_saves_only_the_token_and_commands(self):
        fake = FakeSamsung()
        runner, port = await serve(fake.handler)
        s = self.tv(port)
        task = await self.start(s, 0.4)
        try:
            self.assertEqual(self.saved[-1], {"host": "127.0.0.1", "poll_seconds": 1, "token": "48151623",
                                              "mac": "64:1c:ae:00:00:01"})
            self.assertNotIn("password", json.dumps(self.saved))
            self.assertEqual(mask_config(type(s), self.saved[-1])["token"], "••••")
            e = self.hub.get("samsung_tv.tv")
            self.assertEqual((e.state, e.name, e.attrs["volume"], e.attrs["muted"]), ("on", "[TV] Samsung Q80", 17, False))
            self.assertEqual([a["name"] for a in e.attrs["apps"]], ["Netflix", "YouTube", "Internet"])
            self.assertEqual(s.status, "connected")
            await s.command("samsung_tv.tv", "send_key", ["home"])
            await s.command("samsung_tv.tv", "mute")
            await s.command("samsung_tv.tv", "launch_app", ["3201907018807"])
            await s.command("samsung_tv.tv", "launch_app", ["org.tizen.browser"])
            await s.command("samsung_tv.tv", "select_source", ["hdmi1"])
            await s.command("samsung_tv.tv", "set_volume", 30)
            await asyncio.sleep(0.1)
            keys = [m["params"].get("DataOfCmd") for m in fake.sent if m["method"] == "ms.remote.control"]
            self.assertEqual(keys, ["KEY_HOME", "KEY_MUTE", "KEY_HDMI1"])
            self.assertEqual(fake.sent[1]["params"]["TypeOfRemote"], "SendRemoteKey")
            launches = [m["params"]["data"] for m in fake.sent if m["params"].get("event") == "ed.apps.launch"]
            self.assertEqual(launches, [{"appId": "3201907018807", "action_type": "DEEP_LINK"},
                                        {"appId": "org.tizen.browser", "action_type": "NATIVE_LAUNCH"}])
            set_vol = [b for a, b in fake.soap if "SetVolume" in a]
            self.assertIn("<DesiredVolume>30</DesiredVolume>", set_vol[0])
            self.assertEqual(self.hub.get("samsung_tv.tv").attrs["volume"], 30)
        finally:
            await self.stop(task)
            await runner.cleanup()

    async def test_saved_token_is_sent_and_refusal_reported(self):
        fake = FakeSamsung()
        runner, port = await serve(fake.handler)
        s = self.tv(port, token="48151623")
        task = await self.start(s, 0.3)
        await self.stop(task)
        self.assertEqual(fake.tokens_seen[0], "48151623")
        fake2 = FakeSamsung(allow=False)
        runner2, port2 = await serve(fake2.handler)
        s2 = self.tv(port2)
        task = await self.start(s2, 0.3)
        try:
            self.assertEqual(s2.status, "error")
            self.assertIn("refused", s2.detail)
        finally:
            await self.stop(task)
            await runner.cleanup()
            await runner2.cleanup()

    async def test_turn_on_standby_key_or_wol(self):
        fake = FakeSamsung(power="standby")
        runner, port = await serve(fake.handler)
        s = self.tv(port, token="48151623", mac="64:1c:ae:00:00:01")
        task = await self.start(s, 0.3)
        try:
            self.assertEqual(self.hub.get("samsung_tv.tv").state, "standby")
            await s.command("samsung_tv.tv", "turn_on")
            await asyncio.sleep(0.1)
            self.assertEqual(fake.sent[-1]["params"]["DataOfCmd"], "KEY_POWER")
        finally:
            await self.stop(task)
            await runner.cleanup()
        woke = []

        async def fake_wol(mac, host=""):
            woke.append(mac)
        off = self.tv(1, token="t", mac="64:1c:ae:00:00:01")
        task = await self.start(off, 0.2)
        try:
            self.assertEqual(self.hub.get("samsung_tv.tv").state, "off")
            with mock.patch.object(samsung_tv, "wake_on_lan", fake_wol):
                await off.command("samsung_tv.tv", "turn_on")
            self.assertEqual(woke, ["64:1c:ae:00:00:01"])
        finally:
            await self.stop(task)


# -- LG webOS ---------------------------------------------------------------------------------------

class FakeWebOs:
    def __init__(self, key="new-client-key", refuse=False):
        self.client_key, self.calls, self.cb, self.refuse = None, [], None, refuse
        self._key = key
        self.tv_state = NS(muted=False)
        self.tv_info = NS(system={"modelName": "OLED55C1PUB"})

    async def connect(self):
        if self.refuse:
            from aiowebostv import WebOsTvPairError
            raise WebOsTvPairError("refused")
        self.client_key = self._key
        self.connected = True
        return True

    def is_connected(self):
        return True

    async def register_state_update_callback(self, cb):
        self.cb = cb

    async def get_connection_info(self):
        return {"wiredInfo": {"macAddress": "A8:23:FE:00:00:01"}}

    async def disconnect(self):
        pass

    def __getattr__(self, name):
        async def call(*a):
            self.calls.append((name, *a))
        return call


def webos_state(**kw):
    base = dict(power_state={"state": "Active"}, current_app_id="netflix", sound_output="tv_speaker", muted=False,
                volume=12, apps={"netflix": {"title": "Netflix"}, "com.webos.app.hdmi1": {"title": "HDMI 1"}},
                inputs={"HDMI_1": {"label": "Apple TV", "appId": "com.webos.app.hdmi1"}},
                media_state=[{"playState": "playing"}], is_on=True)
    base.update(kw)
    return NS(**base)


class WebOsTests(Base):
    def test_state_mapping(self):
        state, attrs = webos_tv.map_state(webos_state())
        self.assertEqual(state, "on")
        self.assertEqual((attrs["app"], attrs["volume"], attrs["mediaState"]), ("Netflix", 12, "playing"))
        self.assertEqual(attrs["sources"], [{"id": "HDMI_1", "name": "Apple TV"}])
        self.assertIsNone(attrs["source"])
        state, attrs = webos_tv.map_state(webos_state(current_app_id="com.webos.app.hdmi1", is_on=False,
                                                      media_state=[]))
        self.assertEqual((state, attrs["source"], attrs["mediaState"]), ("standby", "Apple TV", None))

    async def test_pairing_keeps_the_client_key_and_commands(self):
        fake = FakeWebOs()
        w = self.make(webos_tv.WebOsTvIntegration, {"host": "10.0.0.9", "poll_seconds": 1})
        w.make_client = lambda: fake
        task = await self.start(w, 0.1)
        try:
            self.assertEqual(self.saved[0], {"host": "10.0.0.9", "poll_seconds": 1, "client_key": "new-client-key"})
            self.assertEqual(self.saved[-1]["mac"], "a8:23:fe:00:00:01")
            await fake.cb(webos_state())
            self.assertEqual(self.hub.get("webos_tv.tv").state, "on")
            self.assertEqual(w.status, "connected")
            await w.command("webos_tv.tv", "send_key", ["home"])
            await w.command("webos_tv.tv", "set_volume", 25)
            await w.command("webos_tv.tv", "mute")
            await w.command("webos_tv.tv", "select_source", ["HDMI_1"])
            await w.command("webos_tv.tv", "launch_app", ["netflix"])
            await w.command("webos_tv.tv", "pause")
            await w.command("webos_tv.tv", "turn_off")
            self.assertEqual(fake.calls, [("button", "HOME"), ("set_volume", 25), ("set_mute", True),
                                          ("set_input", "HDMI_1"), ("launch_app", "netflix"), ("pause",),
                                          ("power_off",)])
        finally:
            await self.stop(task)

    async def test_turn_on_falls_back_to_wol(self):
        fake = FakeWebOs()

        async def no(*a):
            raise RuntimeError("not connected")
        w = self.make(webos_tv.WebOsTvIntegration, {"host": "10.0.0.9", "mac": "a8:23:fe:00:00:01",
                                                    "client_key": "k"})
        w.client = NS(power_on=no)
        woke = []

        async def fake_wol(mac, host=""):
            woke.append((mac, host))
        with mock.patch.object(tv_common, "wake_on_lan", fake_wol):
            w.ensure_device()
            await w.command("webos_tv.tv", "turn_on")
        self.assertEqual(woke, [("a8:23:fe:00:00:01", "10.0.0.9")])
        del fake

    async def test_refused_pairing(self):
        w = self.make(webos_tv.WebOsTvIntegration, {"host": "10.0.0.9"})
        w.make_client = lambda: FakeWebOs(refuse=True)
        task = await self.start(w, 0.1)
        try:
            self.assertEqual(w.status, "error")
            self.assertIn("didn't accept", w.detail)
            self.assertEqual(self.saved, [])
        finally:
            await self.stop(task)


# -- Android TV -------------------------------------------------------------------------------------

class FakeRemote:
    paired = False
    finished = []

    def __init__(self):
        self.keys, self.launched, self.cbs = [], [], []
        self.is_on, self.current_app = True, "com.netflix.ninja"
        self.volume_info = {"level": 10, "max": 50, "muted": False}
        self.device_info = {"manufacturer": "onn.", "model": "Streaming Device 4K pro"}

    async def async_generate_cert_if_missing(self):
        return False

    async def async_connect(self):
        from androidtvremote2 import InvalidAuth
        if not FakeRemote.paired:
            raise InvalidAuth()

    async def async_get_name_and_mac(self):
        return "Living room TV", "FA:8F:81:00:00:01"

    async def async_start_pairing(self):
        self.pairing_started = True

    async def async_finish_pairing(self, code):
        if code != "A1B2C3":
            raise ValueError("bad code")
        FakeRemote.finished.append(code)
        FakeRemote.paired = True

    def keep_reconnecting(self):
        pass

    def disconnect(self):
        pass

    def send_key_command(self, key):
        self.keys.append(key)

    def send_launch_app_command(self, link):
        self.launched.append(link)

    def __getattr__(self, name):
        if name.startswith("add_"):
            return lambda cb: self.cbs.append(cb)
        raise AttributeError(name)


class AndroidTvTests(Base):
    def test_volume_steps(self):
        self.assertEqual(android_tv.volume_steps(10, 50, 40), ("VOLUME_UP", 10))
        self.assertEqual(android_tv.volume_steps(30, 100, 20), ("VOLUME_DOWN", 10))
        self.assertEqual(android_tv.app_name("com.netflix.ninja"), "Netflix")
        self.assertEqual(android_tv.app_name("org.xbmc.kodi"), "Kodi")

    async def test_pairing_with_code_then_live_state_and_commands(self):
        FakeRemote.paired, FakeRemote.finished = False, []
        remotes = []

        def make():
            remotes.append(FakeRemote())
            return remotes[-1]
        a = self.make(android_tv.AndroidTvIntegration, {"host": "10.1.1.47"}, iid="atv1")
        a.make_remote = make
        task = await self.start(a, 0.1)
        self.assertEqual(a.status, "pairing")
        self.assertTrue(remotes[-1].pairing_started)
        self.assertEqual(self.saved[-1]["mac"], "fa:8f:81:00:00:01")
        await self.stop(task)
        # Saving the code restarts the connection: the parked session finishes pairing.
        b = self.make(android_tv.AndroidTvIntegration, {"host": "10.1.1.47", "code": "A1B2C3",
                                                        "mac": "fa:8f:81:00:00:01"}, iid="atv1")
        b.make_remote = make
        task = await self.start(b, 0.2)
        try:
            self.assertEqual(FakeRemote.finished, ["A1B2C3"])
            self.assertNotIn("code", self.saved[-1])  # the code isn't kept
            self.assertEqual(b.status, "connected")
            e = self.hub.get("atv1.tv")
            self.assertEqual((e.state, e.attrs["app"], e.attrs["volume"]), ("on", "Netflix", 20))
            r = b.remote
            await b.command("atv1.tv", "send_key", ["ok"])
            await b.command("atv1.tv", "launch_app", ["com.google.android.youtube.tv"])
            await b.command("atv1.tv", "set_volume", 30)
            await b.command("atv1.tv", "next")
            await b.command("atv1.tv", "turn_off")
            self.assertEqual(r.keys[0], "DPAD_CENTER")
            self.assertEqual(r.launched, ["com.google.android.youtube.tv"])
            self.assertEqual(r.keys[1:6], ["VOLUME_UP"] * 5)  # 10/50 -> 15/50
            self.assertEqual(r.keys[6:], ["MEDIA_NEXT", "POWER"])
            r.is_on = False
            r.cbs[0](False)
            await asyncio.sleep(0.05)
            self.assertEqual(self.hub.get("atv1.tv").state, "standby")
        finally:
            await self.stop(task)

    async def test_wrong_code_asks_again(self):
        FakeRemote.paired, FakeRemote.finished = False, []
        tv_common.park("atv2", FakeRemote())
        a = self.make(android_tv.AndroidTvIntegration, {"host": "10.1.1.47", "code": "nope"}, iid="atv2")
        a.make_remote = FakeRemote
        task = await self.start(a, 0.1)
        try:
            self.assertEqual(a.status, "pairing")
            self.assertIn("didn't work", a.detail)
            self.assertIsNotNone(tv_common.unpark("atv2"))
        finally:
            await self.stop(task)


# -- Apple TV ---------------------------------------------------------------------------------------

class FakeHandler:
    def __init__(self, proto):
        self.proto, self.has_paired, self.closed, self.service = proto, False, False, NS(credentials=None)

    def pin(self, code):
        self.code = code

    async def finish(self):
        if self.code == "1234":
            self.has_paired = True
            self.service.credentials = f"creds-{self.proto.name.lower()}"

    async def close(self):
        self.closed = True


class FakeConf:
    def __init__(self):
        from pyatv.const import PairingRequirement, Protocol
        self.services = {Protocol.AirPlay: NS(pairing=PairingRequirement.Mandatory),
                         Protocol.Companion: NS(pairing=PairingRequirement.Mandatory),
                         Protocol.RAOP: NS(pairing=PairingRequirement.NotNeeded)}
        self.set = {}

    def get_service(self, proto):
        return self.services.get(proto)

    def set_credentials(self, proto, c):
        self.set[proto.name] = c


class FakeAtv:
    def __init__(self):
        from pyatv.const import PowerState
        self.calls = []
        self.power = NS(power_state=PowerState.On, turn_on=self._rec("turn_on"), turn_off=self._rec("turn_off"))
        self.remote_control = NS(**{m: self._rec(m) for m in set(apple_tv.KEYMAP.values())})
        self.audio = NS(volume=35.0, set_volume=self._rec("set_volume"))
        self.apps = NS(app_list=self._apps, launch_app=self._rec("launch_app"))
        self.metadata = NS(playing=self._playing, app=NS(name="Plex", identifier="com.plexapp.plex"))
        self.device_info = NS(model_str="Apple TV 4K (gen 3)")

    def _rec(self, name):
        async def call(*a):
            self.calls.append((name, *a))
        return call

    async def _apps(self):
        return [NS(name="Plex", identifier="com.plexapp.plex"), NS(name="YouTube", identifier="com.google.ios.youtube")]

    async def _playing(self):
        return NS(title="The Bear", artist=None, device_state=NS(name="Paused"))

    def close(self):
        pass


class AppleTvTests(Base):
    async def test_two_code_pairing_saves_only_credentials(self):
        conf = FakeConf()
        handlers = []

        async def start_pairing(c, proto):
            handlers.append(FakeHandler(proto))
            return handlers[-1]

        def make(cfg):
            a = self.make(apple_tv.AppleTvIntegration, cfg, iid="appletv")
            a.scan = lambda: asyncio.sleep(0, conf)
            a.start_pairing = start_pairing
            return a

        a = make({"host": "10.1.1.44", "poll_seconds": 1})
        task = await self.start(a, 0.1)
        self.assertEqual(a.status, "pairing")
        self.assertIn("step 1 of 2", a.detail)
        self.assertEqual(handlers[-1].proto.name, "AirPlay")
        await self.stop(task)

        b = make({"host": "10.1.1.44", "poll_seconds": 1, "code": "1234"})
        task = await self.start(b, 0.1)
        self.assertEqual(json.loads(self.saved[-1]["credentials"]), {"airplay": "creds-airplay"})
        self.assertEqual(handlers[-1].proto.name, "Companion")
        self.assertIn("step 2 of 2", b.detail)
        self.assertTrue(handlers[0].closed)
        await self.stop(task)

        atv = FakeAtv()
        c = make({"host": "10.1.1.44", "poll_seconds": 1, "code": "1234",
                  "credentials": self.saved[-1]["credentials"]})

        async def connect(cf):
            for name, cr in c.creds.items():
                cf.set_credentials(NS(name=name), cr)
            return atv
        c.connect = connect
        task = await self.start(c, 0.15)
        try:
            creds = json.loads(self.saved[-1]["credentials"])
            self.assertEqual(creds, {"airplay": "creds-airplay", "companion": "creds-companion"})
            self.assertNotIn("code", self.saved[-1])
            self.assertEqual(conf.set, {"airplay": "creds-airplay", "companion": "creds-companion"})
            self.assertEqual(c.status, "connected")
            e = self.hub.get("appletv.tv")
            self.assertEqual((e.state, e.attrs["app"], e.attrs["mediaTitle"], e.attrs["mediaState"], e.attrs["volume"]),
                             ("on", "Plex", "The Bear", "paused", 35))
            self.assertEqual(e.attrs["apps"][1], {"id": "com.google.ios.youtube", "name": "YouTube"})
            await c.command("appletv.tv", "send_key", ["back"])
            await c.command("appletv.tv", "play")
            await c.command("appletv.tv", "set_volume", 50)
            await c.command("appletv.tv", "launch_app", ["com.plexapp.plex"])
            await c.command("appletv.tv", "turn_off")
            await c.command("appletv.tv", "turn_on")
            self.assertEqual(atv.calls, [("menu",), ("play",), ("set_volume", 50.0), ("launch_app", "com.plexapp.plex"),
                                         ("turn_off",), ("turn_on",)])
        finally:
            await self.stop(task)


# -- Hisense VIDAA ----------------------------------------------------------------------------------

class FakeMqtt:
    def __init__(self):
        self.published = []

    async def publish(self, t, body):
        self.published.append((t, body))


class VidaaTests(Base):
    def test_state_mapping(self):
        self.assertEqual(vidaa_tv.map_tv_state({"statetype": "fake_sleep_0"}), ("standby", {}))
        self.assertEqual(vidaa_tv.map_tv_state({"statetype": "sourceswitch", "sourceid": "4", "sourcename": "HDMI1",
                                                "displayname": "Xbox"})[1]["source"], "Xbox")
        self.assertEqual(vidaa_tv.map_tv_state({"statetype": "app", "name": "Netflix", "url": "netflix"}),
                         ("on", {"app": "Netflix", "appId": "netflix"}))

    async def test_messages_and_commands(self):
        v = self.make(vidaa_tv.VidaaTvIntegration, {"host": "10.0.0.7", "client_id": "newman-abc"})
        v.client = FakeMqtt()
        v.ensure_device()
        await v.handle("/remoteapp/mobile/broadcast/ui_service/state", b'{"statetype":"app","name":"YouTube"}')
        await v.handle("/remoteapp/mobile/broadcast/platform_service/actions/volumechange",
                       b'{"volume_type":0,"volume_value":14}')
        await v.handle("/remoteapp/mobile/newman-abc/ui_service/data/sourcelist",
                       b'[{"sourceid":"1","sourcename":"TV","displayname":"TV"},'
                       b'{"sourceid":"4","sourcename":"HDMI1","displayname":"Xbox"}]')
        await v.handle("/remoteapp/mobile/newman-abc/ui_service/data/applist",
                       b'[{"appId":"1","name":"Netflix","url":"netflix"}]')
        e = self.hub.get("vidaa_tv.tv")
        self.assertEqual((e.state, e.attrs["app"], e.attrs["volume"]), ("on", "YouTube", 14))
        self.assertEqual(e.attrs["sources"], [{"id": "1", "name": "TV"}, {"id": "4", "name": "Xbox"}])
        await v.command("vidaa_tv.tv", "send_key", ["home"])
        await v.command("vidaa_tv.tv", "set_volume", 22)
        await v.command("vidaa_tv.tv", "select_source", ["4"])
        await v.command("vidaa_tv.tv", "launch_app", ["1"])
        await v.command("vidaa_tv.tv", "turn_on")
        p = v.client.published
        self.assertEqual(p[0], ("/remoteapp/tv/remote_service/newman-abc/actions/sendkey", "KEY_HOME"))
        self.assertEqual(p[1], ("/remoteapp/tv/platform_service/newman-abc/actions/changevolume", "22"))
        self.assertEqual(p[2][0], "/remoteapp/tv/ui_service/newman-abc/actions/changesource")
        self.assertEqual(json.loads(p[2][1]), {"sourceid": "4", "sourcename": "HDMI1"})
        self.assertEqual(json.loads(p[3][1]), {"appId": "1", "name": "Netflix", "url": "netflix"})
        self.assertEqual(p[4][1], "KEY_POWER")

    async def test_pairing_keeps_the_client_id_not_the_code(self):
        v = self.make(vidaa_tv.VidaaTvIntegration, {"host": "10.0.0.7", "client_id": "newman-abc"})
        v.client = FakeMqtt()
        await v.handle("/remoteapp/mobile/newman-abc/ui_service/data/authenticationcode", b'{"result":1}')
        await v.handle("/remoteapp/mobile/newman-abc/platform_service/data/tokenissuance",
                       b'{"accesstoken":"tok-1","refreshtoken":"ref-1"}')
        self.assertTrue(self.saved[0]["paired"])
        self.assertEqual(self.saved[-1]["token"], "tok-1")
        self.assertEqual(v.client.published[0][0], "/remoteapp/tv/platform_service/newman-abc/data/gettoken")

    async def test_run_sends_pairing_code_from_settings(self):
        sent = []

        class Client(FakeMqtt):
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def subscribe(self, t):
                sent.append(("sub", t))

            async def publish(self, t, body):
                sent.append((t, body))

            @property
            def messages(self):
                async def gen():
                    await asyncio.Event().wait()
                    yield None
                return gen()

        v = self.make(vidaa_tv.VidaaTvIntegration, {"host": "10.0.0.7", "code": "4821"}, iid="vidaa")
        v.make_client = Client
        task = await self.start(v, 0.1)
        try:
            cid = self.saved[0]["client_id"]
            self.assertTrue(cid.startswith("newman-"))
            self.assertLessEqual(len(cid), 23)
            self.assertNotIn("code", self.saved[-1])
            auth = [b for t, b in sent if t.endswith("/actions/authenticationcode")]
            self.assertEqual(json.loads(auth[0]), {"authNum": 4821})
        finally:
            await self.stop(task)


if __name__ == "__main__":
    unittest.main()
