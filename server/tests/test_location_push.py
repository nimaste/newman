"""iPhones' Location Push Service Extension, hub side: registering / forgetting the location push
token (POST /api/me/location/push-token), a refresh sends ONE APNs location push (topic
<bundle>.location-query, push type location, priority 10, the phone's own env) to iPhones with a token
and the silent locate push to the rest, the 2-minute limit, no repeats, and a dead token being
dropped (falling back to the silent push)."""
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

import aiohttp  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import LOCATOR, build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.push.apns import ApnsSender, PushError  # noqa: E402
from newman.store import Phone  # noqa: E402
from test_locations import HOME, FakeApns, H  # noqa: E402

LOC_TOKEN = "ab" * 32
LOC_TOKEN_2 = "cd" * 32


class FailingApns(FakeApns):
    """Location pushes come back 410 Unregistered; everything else goes."""

    async def send(self, token, payload, **kw):
        self.sent.append((token, payload, kw))
        if kw.get("push_type") == "location":
            raise PushError("APNs 410 Unregistered", dead_token=True)


class LocationPushTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.h.store.save_integration({"id": "weather", "type": "weather", "name": "Weather",
                                       "config": {"place": "Toronto", "latitude": HOME[0], "longitude": HOME[1]}})
        self.app = build_app(self.h)
        self.loc = self.app[LOCATOR]
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        r = await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        self.assertEqual(r.status, 200)
        for name in ("Dante", "Marlina"):
            await self.client.post("/api/users", json={"username": name.lower(), "name": name, "role": "family",
                                                       "password": "another pass"})
        self.chris = await self.token("chris", "correct horse")
        self.dante, self.marlina = await self.token("dante"), await self.token("marlina")
        self.pid = {p["name"]: p["id"] for p in self.h.store.people()}
        self.apns = FakeApns()
        self.h.notifier._apns = self.apns
        # Dante: an iPhone that will have a location token, an older iPhone without, an Android.
        await self.register(self.dante, "ios-d", "tok-ios", sandbox=True)
        await self.register(self.dante, "ios-old", "tok-old")
        self.h.store.register_phone(Phone(device_id="and-d", token="tok-and", platform="android",
                                          person_id=self.pid["Dante"]))
        await self.register(self.marlina, "ios-m", "tok-m")
        await self.call("PUT", "/api/me/location", self.dante, json={"enabled": True})

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def token(self, username, password="another pass"):
        s = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        r = await s.post(self.client.make_url("/api/auth/login"),
                         json={"username": username, "password": password, "kind": "app"})
        body = await r.json()
        await s.close()
        self.assertEqual(r.status, 200, body)
        return {"Authorization": "Bearer " + body["token"]}

    async def call(self, method, path, who, **kw):
        r = await self.client.request(method, path, headers=who, **kw)
        try:
            body = await r.json()
        except Exception:
            body = None
        return r.status, body

    async def register(self, who, device_id, token, sandbox=False):
        body = {"token": token, "platform": "ios", "deviceId": device_id}
        if sandbox:
            body["apnsEnvironment"] = "sandbox"
        st, reply = await self.call("POST", "/api/devices/register", who, json=body)
        self.assertEqual(st, 200, reply)

    async def push_token(self, who, **body):
        return await self.call("POST", "/api/me/location/push-token", who, json=body)

    def tokens(self):
        return self.h.notifier.location_tokens

    # -- registering the token -------------------------------------------------------------------------

    async def test_register_and_forget_the_token(self):
        st, body = await self.push_token(self.dante, deviceId="ios-d", token=LOC_TOKEN.upper(), env="sandbox")
        self.assertEqual((st, body), (200, {"ok": True, "kept": True}))
        self.assertEqual(self.tokens().get("ios-d"), (LOC_TOKEN, "sandbox"))
        self.assertEqual(self.tokens().for_person(self.pid["Dante"]), {"ios-d": (LOC_TOKEN, "sandbox")})
        # A new token replaces it; the env defaults to production.
        await self.push_token(self.dante, deviceId="ios-d", token=LOC_TOKEN_2)
        self.assertEqual(self.tokens().get("ios-d"), (LOC_TOKEN_2, "production"))
        # Only for your own registered iPhone, a hex token, while sharing.
        self.assertEqual((await self.push_token(self.dante, deviceId="ios-m", token=LOC_TOKEN))[0], 404)
        self.assertEqual((await self.push_token(self.dante, deviceId="and-d", token=LOC_TOKEN))[0], 404)
        self.assertEqual((await self.push_token(self.dante, deviceId="nope", token=LOC_TOKEN))[0], 404)
        self.assertEqual((await self.push_token(self.dante, deviceId="ios-d", token="not hex!"))[0], 400)
        self.assertEqual((await self.push_token(self.dante, token=LOC_TOKEN))[0], 400)
        self.assertEqual((await self.push_token(self.marlina, deviceId="ios-m", token=LOC_TOKEN))[0], 403)
        # A null token forgets it (also fine when there's nothing to forget).
        st, body = await self.push_token(self.dante, deviceId="ios-d", token=None)
        self.assertEqual((st, body), (200, {"ok": True, "kept": False}))
        self.assertIsNone(self.tokens().get("ios-d"))
        self.assertEqual((await self.push_token(self.dante, deviceId="ios-d", token=None))[0], 200)
        self.assertEqual((await self.push_token(self.dante, deviceId="ios-m", token=None))[0], 404)

    async def test_stopping_sharing_or_removing_the_phone_drops_it(self):
        await self.push_token(self.dante, deviceId="ios-d", token=LOC_TOKEN)
        await self.call("PUT", "/api/me/location", self.dante, json={"enabled": False})
        self.assertIsNone(self.tokens().get("ios-d"))
        await self.call("PUT", "/api/me/location", self.dante, json={"enabled": True})
        await self.push_token(self.dante, deviceId="ios-d", token=LOC_TOKEN)
        self.h.store.remove_phone("ios-d")
        self.assertEqual(self.tokens().for_person(self.pid["Dante"]), {})

    # -- refreshing ---------------------------------------------------------------------------------------

    async def test_refresh_sends_a_location_push_to_iphones_with_a_token(self):
        await self.push_token(self.dante, deviceId="ios-d", token=LOC_TOKEN, env="sandbox")
        st, _ = await self.call("POST", f"/api/people/{self.pid['Dante']}/refresh", self.chris)
        self.assertEqual(st, 202)
        sent = {tok: (payload, kw) for tok, payload, kw in self.apns.sent}
        self.assertEqual(set(sent), {LOC_TOKEN, "tok-old"})  # not the iPhone's regular token; no Android (no FCM)
        payload, kw = sent[LOC_TOKEN]
        self.assertEqual(payload, {})
        self.assertEqual((kw["push_type"], kw["topic"], kw["priority"], kw["env"]),
                         ("location", "ca.nimaste.newman.location-query", 10, "sandbox"))
        payload, kw = sent["tok-old"]  # no location token: the silent push as before
        self.assertEqual(payload, {"aps": {"content-available": 1}, "newman": {"type": "locate"}})
        self.assertEqual((kw["push_type"], kw["priority"], kw.get("topic")), ("background", 5, None))
        # Asking again straight away doesn't push again (2 minutes per person).
        st, body = await self.call("POST", f"/api/people/{self.pid['Dante']}/refresh", self.marlina)
        self.assertEqual(st, 429)
        self.assertEqual(len(self.apns.sent), 2)

    async def test_one_shot_no_repeats(self):
        await self.push_token(self.dante, deviceId="ios-d", token=LOC_TOKEN)
        offset = [0.0]
        real = time.time
        self.loc.clock = lambda: real() + offset[0]
        await self.call("POST", f"/api/people/{self.pid['Dante']}/refresh", self.chris)
        self.assertEqual(len(self.apns.sent), 2)
        for offset[0] in (21, 41, 100):
            await asyncio.sleep(0.05)
        self.assertEqual(len(self.apns.sent), 2)

    async def test_a_dead_location_token_is_dropped_and_the_silent_push_goes_instead(self):
        self.h.notifier._apns = apns = FailingApns()
        await self.push_token(self.dante, deviceId="ios-d", token=LOC_TOKEN)
        await self.call("POST", f"/api/people/{self.pid['Dante']}/refresh", self.chris)
        types = sorted((t, kw["push_type"]) for t, _, kw in apns.sent)
        self.assertEqual(types, [(LOC_TOKEN, "location"), ("tok-ios", "background"), ("tok-old", "background")])
        self.assertIsNone(self.tokens().get("ios-d"))
        self.assertIsNotNone(self.h.store.phone("ios-d"))  # the phone itself stays
    # -- the APNs request itself -----------------------------------------------------------------------

    async def test_apns_location_push_headers_and_errors(self):
        class Resp:
            def __init__(self, status, body):
                self.status_code, self._body, self.text = status, body, str(body)

            def json(self):
                return self._body

        class Client:
            def __init__(self):
                self.calls, self.reply = [], Resp(200, {})

            async def post(self, url, content, headers):
                self.calls.append((url, content, headers))
                return self.reply

        sender = ApnsSender.__new__(ApnsSender)
        sender.enabled, sender._client = True, Client()
        sender._token = lambda: "jwt"
        await sender.send(LOC_TOKEN, {}, env="sandbox", priority=10, push_type="location",
                          topic="ca.nimaste.newman.location-query", expiration_s=60)
        url, content, headers = sender._client.calls[0]
        self.assertEqual(url, f"https://api.sandbox.push.apple.com/3/device/{LOC_TOKEN}")
        self.assertEqual(content, "{}")
        self.assertEqual((headers["apns-push-type"], headers["apns-topic"], headers["apns-priority"]),
                         ("location", "ca.nimaste.newman.location-query", "10"))
        self.assertEqual(headers["authorization"], "bearer jwt")
        # Without a topic it's still the app's bundle id.
        await sender.send("tok", {"aps": {}}, env="production")
        self.assertEqual(sender._client.calls[1][2]["apns-topic"], config.apns_bundle_id)
        for status, reason in ((410, "Unregistered"), (400, "BadDeviceToken")):
            sender._client.reply = Resp(status, {"reason": reason})
            with self.assertRaises(PushError) as e:
                await sender.send(LOC_TOKEN, {}, env="sandbox", push_type="location")
            self.assertTrue(e.exception.dead_token)
