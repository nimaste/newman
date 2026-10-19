"""Amazon Alexa (experimental), offline against a fake Amazon: signing in with the pasted address (only
the refresh token kept), cookies made again from it, the timer list, hidden Echos, Amazon refusing the
token (sign in again, no retrying), signing out, and the wall tablets' GET /api/alexa/timers."""
import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("TOKEN_SECRET", "test-secret")

import httpx  # noqa: E402

from newman.integrations import REGISTRY  # noqa: E402
from newman.integrations import alexa as AX  # noqa: E402
from newman.integrations.base import Context  # noqa: E402


class FakeAmazon:
    def __init__(self):
        self.refresh = "Atnr|good"
        self.revoked = False
        self.calls = []
        self.registered = []
        self.cookie_domains = []
        self.timers = [
            {"type": "Timer", "status": "ON", "remainingTime": 25_000, "originalDuration": 600_000,
             "timerLabel": "pasta", "deviceSerialNumber": "K1", "id": "t-pasta"},
            {"type": "Timer", "status": "ON", "remainingTime": 1_930_000, "originalDuration": 3_600_000,
             "timerLabel": "", "deviceSerialNumber": "B2", "id": "t-laundry"},
            {"type": "Timer", "status": "OFF", "remainingTime": 0, "deviceSerialNumber": "K1", "id": "t-old"},
            {"type": "Alarm", "status": "ON", "deviceSerialNumber": "K1", "id": "a-1"},
        ]
        self.notification_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.calls.append((request.method, url.host, url.path))
        if url.path == "/auth/register":
            body = json.loads(request.content)
            self.registered.append(body)
            if body["auth_data"]["authorization_code"] != "CODE123":
                return httpx.Response(400, json={"response": {"error": {"code": "InvalidValue"}}})
            return httpx.Response(200, json={"response": {"success": {"tokens": {
                "bearer": {"access_token": "Atna|x", "refresh_token": self.refresh, "expires_in": "3600"},
                "mac_dms": {"device_private_key": "secret-key"}}}}})
        if url.path == "/ap/exchangetoken/cookies":
            form = parse_qs(request.content.decode())
            if self.revoked or form["source_token"][0] != self.refresh:
                return httpx.Response(400, json={"response": {"error": {"code": "InvalidToken"}}})
            domain = form["domain"][0]
            self.cookie_domains.append(domain)
            return httpx.Response(200, json={"response": {"tokens": {"cookies": {
                domain: [{"Name": "at-main", "Value": "at" + domain}, {"Name": "session-id", "Value": "s1"}]}}}})
        if url.path == "/auth/token":
            return httpx.Response(200, json={"access_token": "Atna|y"})
        if url.path == "/auth/deregister":
            return httpx.Response(200, json={})
        if url.host.startswith("alexa.") and url.path == "/api/users/me":
            return httpx.Response(200, json={"marketPlaceDomainName": "www.amazon.ca"})
        if url.host.startswith("alexa.") and url.path == "/api/language":
            return httpx.Response(200, json={}, headers={"set-cookie": "csrf=987; Path=/"})
        if url.host.startswith("alexa.") and url.path == "/api/endpoints":
            return httpx.Response(200, json={"websiteApiUrl": "https://na-api-alexa.amazon.com/"})
        if url.host == "na-api-alexa.amazon.com" and url.path == "/api/devices-v2/device":
            assert request.headers.get("csrf") == "987"
            return httpx.Response(200, json={"devices": [
                {"serialNumber": "K1", "accountName": "Kitchen Echo", "capabilities": ["TIMERS_AND_ALARMS"]},
                {"serialNumber": "B2", "accountName": "Basement Echo", "capabilities": ["TIMERS_AND_ALARMS", "MUSIC_SKILL"]},
                {"serialNumber": "P3", "accountName": "Phone app", "capabilities": ["MUSIC_SKILL"]},
            ]})
        if url.host == "na-api-alexa.amazon.com" and url.path == "/api/notifications":
            if self.notification_status != 200:
                return httpx.Response(self.notification_status, text="nope")
            return httpx.Response(200, json={"notifications": self.timers})
        return httpx.Response(404, text=f"no {url}")


class AlexaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.amazon = FakeAmazon()
        self.sent = []
        AX.AlexaIntegration.transport = httpx.MockTransport(self.amazon)
        self.saved = []

    def tearDown(self):
        AX.AlexaIntegration.transport = None
        self.tmp.cleanup()

    def make(self, config=None):
        async def broadcast(msg):
            self.sent.append(msg)
        ctx = Context(hub=None, data_dir=Path(self.tmp.name), snapshot_dir=Path(self.tmp.name))
        ctx.broadcast = broadcast
        inst = AX.AlexaIntegration("alexa", "Amazon Alexa", dict(config or {"site": "amazon.ca"}), ctx)
        ctx.instances["alexa"] = inst
        inst.save_config = self.saved.append
        return inst

    def run_(self, coro):
        return asyncio.run(coro)

    def sign_in(self, inst):
        url = inst.admin_details()["amazon"]["signInUrl"]
        q = parse_qs(urlparse(url).query)
        self.assertTrue(q["openid.oa2.client_id"][0].startswith("device:"))
        self.assertEqual(q["openid.oa2.code_challenge_method"][0], "S256")
        self.assertEqual(q["openid.return_to"][0], "https://www.amazon.com/ap/maplanding")
        self.assertEqual(q["language"][0], AX.LANGUAGE[inst.site])
        answer = "https://www.amazon.com/ap/maplanding?openid.oa2.authorization_code=CODE123&openid.mode=id_res"
        self.run_(inst.sign_in(answer))

    def test_in_the_catalog_as_experimental(self):
        j = REGISTRY["alexa"].catalog_json()
        self.assertTrue(j["experimental"])
        self.assertEqual(next(f for f in j["fields"] if f["key"] == "site")["default"], "amazon.ca")
        self.assertEqual(next(f for f in j["fields"] if f["key"] == "check_seconds")["min"], 15)
        self.assertFalse(REGISTRY["ring"].catalog_json()["experimental"])

    def test_signing_in_keeps_only_the_refresh_token(self):
        inst = self.make()
        self.assertFalse(inst.admin_details()["amazon"]["signedIn"])
        self.sign_in(inst)
        reg = self.amazon.registered[0]
        self.assertEqual(reg["auth_data"]["code_algorithm"], "SHA-256")
        self.assertTrue(reg["auth_data"]["code_verifier"])
        f = Path(self.tmp.name) / "integrations" / "alexa" / "amazon.json"
        kept = json.loads(f.read_text())
        self.assertEqual(kept["refresh_token"], "Atnr|good")
        self.assertNotIn("secret-key", f.read_text())  # the device key Amazon also sends isn't kept
        self.assertNotIn("Atna|x", f.read_text())  # nor the access token, nor cookies
        self.assertNotIn("at-main", f.read_text())
        self.assertEqual(oct(f.stat().st_mode & 0o777), "0o600")
        d = inst.admin_details()["amazon"]
        self.assertTrue(d["signedIn"])
        self.assertIsNone(d["signInUrl"])

    def test_a_wrong_address_is_refused_in_words(self):
        inst = self.make()
        inst.admin_details()
        with self.assertRaises(ValueError) as e:
            self.run_(inst.sign_in("https://www.amazon.com/gp/yourstore"))
        self.assertIn("maplanding", str(e.exception))
        with self.assertRaises(ValueError) as e:
            self.run_(inst.sign_in("https://www.amazon.com/ap/maplanding?openid.oa2.authorization_code=WRONG"))
        self.assertIn("Open Amazon sign-in again", str(e.exception))
        self.assertFalse((Path(self.tmp.name) / "integrations" / "alexa" / "amazon.json").exists())

    def test_amazons_site_wins(self):
        inst = self.make({"site": "amazon.com"})
        self.sign_in(inst)
        self.assertEqual(inst.config["site"], "amazon.ca")
        self.assertEqual(self.saved[-1]["site"], "amazon.ca")

    def test_timers_come_with_their_echo_and_end_time(self):
        inst = self.make()
        self.sign_in(inst)
        before = time.time()
        self.run_(inst.check())
        timers = inst.timers()
        self.assertEqual([t["name"] for t in timers], ["Pasta", "Timer"])
        self.assertEqual([t["where"] for t in timers], ["Kitchen Echo", "Basement Echo"])
        self.assertAlmostEqual(timers[0]["endsAt"], before + 25, delta=2)
        self.assertEqual(timers[0]["seconds"], 600)
        self.assertEqual(timers[0]["source"], "alexa")
        # Cookies were made from the token for the site (and amazon.com, which the API host is on).
        self.assertIn(".amazon.ca", self.amazon.cookie_domains)
        self.assertIn(".amazon.com", self.amazon.cookie_domains)
        # The wall tablets were told.
        self.assertEqual(self.sent[-1]["type"], "alexaTimers")
        self.assertEqual(len(self.sent[-1]["timers"]), 2)
        echos = inst.admin_details()["amazon"]["echos"]
        self.assertEqual([e["name"] for e in echos], ["Basement Echo", "Kitchen Echo"])  # not the phone app
        self.assertIsNotNone(inst.admin_details()["amazon"]["checkedAt"])

    def test_an_unchanged_list_isnt_sent_again(self):
        inst = self.make()
        self.sign_in(inst)
        self.run_(inst.check())
        n = len(self.sent)
        self.run_(inst.check())
        self.assertEqual(len(self.sent), n)
        self.amazon.timers = self.amazon.timers[1:]
        self.run_(inst.check())
        self.assertEqual(len(self.sent), n + 1)
        self.assertEqual([t["name"] for t in self.sent[-1]["timers"]], ["Timer"])

    def test_hidden_echos_are_left_out(self):
        inst = self.make({"site": "amazon.ca", "hidden_echos": ["B2"]})
        self.sign_in(inst)
        self.run_(inst.check())
        self.assertEqual([t["where"] for t in inst.timers()], ["Kitchen Echo"])
        shown = {e["name"]: e["shown"] for e in inst.admin_details()["amazon"]["echos"]}
        self.assertEqual(shown, {"Basement Echo": False, "Kitchen Echo": True})

    def test_a_long_done_timer_is_dropped(self):
        t = AX.timers_from([{"type": "Timer", "status": "ON", "remainingTime": 0, "deviceSerialNumber": "K1",
                             "id": "x"}], {"K1": "Kitchen Echo"}, set(), time.time() - AX.DONE_CAP_S - 5)
        inst = self.make()
        inst._timers = t
        self.assertEqual(inst.timers(), [])
        inst._timers = AX.timers_from([{"type": "Timer", "status": "ON", "remainingTime": 0,
                                        "deviceSerialNumber": "K1", "id": "x"}], {"K1": "Kitchen Echo"}, set(),
                                      time.time() - 60)
        self.assertEqual(len(inst.timers()), 1)

    def test_a_refused_call_makes_the_cookies_again_once(self):
        inst = self.make()
        self.sign_in(inst)
        self.run_(inst.check())
        made = len(self.amazon.cookie_domains)
        self.amazon.notification_status = 401
        with self.assertRaises(httpx.HTTPError):
            self.run_(inst.check())
        self.assertGreater(len(self.amazon.cookie_domains), made)

    def test_a_refused_token_asks_to_sign_in_again_without_retrying(self):
        inst = self.make()
        self.sign_in(inst)
        self.amazon.revoked = True

        async def go():
            task = asyncio.create_task(inst.run())
            for _ in range(50):
                await asyncio.sleep(0.02)
                if inst.status == "pairing":
                    break
            calls = len(self.amazon.calls)
            await asyncio.sleep(0.3)
            self.assertEqual(len(self.amazon.calls), calls)  # waiting for the admin, not hammering Amazon
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.run_(go())
        self.assertEqual(inst.status, "pairing")
        self.assertIn("sign in again", inst.detail)
        self.assertFalse((Path(self.tmp.name) / "integrations" / "alexa" / "amazon.json").exists())
        self.assertTrue(inst.admin_details()["amazon"]["signInUrl"])

    def test_signing_out_forgets_and_tells_amazon(self):
        inst = self.make()
        self.sign_in(inst)
        self.run_(inst.check())
        self.run_(inst.sign_out())
        self.assertFalse((Path(self.tmp.name) / "integrations" / "alexa" / "amazon.json").exists())
        self.assertIn(("POST", "api.amazon.com", "/auth/deregister"), self.amazon.calls)
        self.assertEqual(inst.timers(), [])
        self.assertEqual(self.sent[-1]["timers"], [])
        self.assertFalse(inst.admin_details()["amazon"]["signedIn"])

    def test_the_wall_tablets_read_every_alexa_connections_timers(self):
        inst = self.make()
        self.sign_in(inst)
        self.run_(inst.check())
        msg = AX.alexa_timers_message({"alexa": inst, "other": object()})
        self.assertEqual([t["name"] for t in msg["timers"]], ["Pasta", "Timer"])


if __name__ == "__main__":
    unittest.main()
