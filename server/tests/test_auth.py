"""Sign-in over the real HTTP API: first-run setup, home vs away, the away policy, optional 2FA,
tablet restrictions and PINs, roles, and lockouts."""
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

import aiohttp  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman import auth as A  # noqa: E402
from newman.api import build_app  # noqa: E402
from newman.automations.engine import Engine  # noqa: E402
from newman.config import config  # noqa: E402
from newman.integrations.base import Context, Integration  # noqa: E402
from newman.integrations.manager import IntegrationManager  # noqa: E402
from newman.push.notifier import Notifier  # noqa: E402
from newman.state import Hub  # noqa: E402
from newman.store import Store  # noqa: E402

AWAY = {"X-Forwarded-For": "203.0.113.7"}  # some proxy we don't trust = away, nothing it claims believed


class H:
    def __init__(self, data: Path) -> None:
        self.snapshot_dir, self.clip_dir = data / "snapshots", data / "clips"
        self.store = Store(data)
        self.hub = Hub()
        self.notifier = Notifier(self.store)
        self.integrations = IntegrationManager(self.store, Context(self.hub, data, self.snapshot_dir))
        self.engine = Engine(self.hub, self.store, self.notifier, self.integrations)
        self.discovery = None


class FakeLocks(Integration):
    type = "fake"
    name = "Fake locks"

    def __init__(self, ctx):
        super().__init__("fake", "Fake locks", {}, ctx)
        self.calls = []

    async def command(self, eid, command, value=None):
        self.calls.append((eid, command))


class Browser:
    """Another client against the same test server, with its own cookies."""

    def __init__(self, client: TestClient) -> None:
        self._client, self._s = client, aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))

    def __getattr__(self, method):
        return lambda path, **kw: self._s.request(method.upper(), self._client.make_url(path), **kw)

    async def close(self):
        await self._s.close()


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.locks = FakeLocks(self.h.integrations._ctx)
        self.h.integrations.instances["fake"] = self.locks
        self.h.hub.upsert("fake.side", kind="lock", name="Side Door", integration="fake")
        await self.h.hub.update("fake.side", state="LOCKED")
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def setup_admin(self):
        r = await self.client.post("/api/auth/setup", json={"username": "Chris", "name": "Chris", "password": "correct horse"})
        self.assertEqual(r.status, 200)
        return r

    async def login(self, username="chris", password="correct horse", headers=None, **extra):
        # A fresh browser per sign-in, so cookies from one don't leak into the next.
        c = Browser(self.client)
        r = await c.post("/api/auth/login", json={"username": username, "password": password, **extra}, headers=headers or {})
        return c, r, await r.json()

    async def test_first_run_setup_then_everything_needs_a_session(self):
        state = await (await self.client.get("/api/auth/state")).json()
        self.assertTrue(state["setupNeeded"])
        self.assertEqual(state["where"], "home")
        self.assertEqual((await self.client.get("/api/entities")).status, 401)
        await self.setup_admin()
        self.assertEqual((await self.client.get("/api/entities")).status, 200)  # the setup cookie signs you in
        self.assertEqual((await self.client.post("/api/auth/setup", json={})).status, 409)
        self.assertEqual(self.h.store.users()[0]["person_id"], self.h.store.people()[0]["id"])  # also a person

    async def test_away_policy_and_optional_two_factor(self):
        await self.setup_admin()
        # Default policy: away needs a certificate or 2FA, and this account has neither.
        c, r, body = await self.login(headers=AWAY)
        self.assertEqual(r.status, 403)
        self.assertIn("two-factor", body["error"])
        await c.close()
        # 2FA is optional: turn it on, and away sign-in asks for a code.
        secret = (await (await self.client.post("/api/auth/2fa/start", json={"password": "correct horse"})).json())["secret"]
        code = A._hotp(secret, int(time.time() // 30))
        r = await self.client.post("/api/auth/2fa/enable", json={"code": code})
        self.assertEqual(len((await r.json())["recoveryCodes"]), 10)
        c, r, body = await self.login(headers=AWAY)
        self.assertEqual((r.status, body.get("needs")), (401, "totp"))
        await c.close()
        c, r, body = await self.login(headers=AWAY, code=A._hotp(secret, int(time.time() // 30) + 1))
        self.assertEqual(r.status, 200)
        self.assertEqual((await c.get("/api/entities")).status, 200)
        await c.close()
        c, r, body = await self.login(headers=AWAY, code=A._hotp(secret, int(time.time() // 30) + 1))
        self.assertEqual(r.status, 401)  # the same code can't be used twice
        await c.close()
        # At home the password is enough (unless "2FA at home" is switched on).
        c, r, _ = await self.login()
        self.assertEqual(r.status, 200)
        await c.close()
        # "Password is enough" away: nothing forced (someone who turned 2FA on still gets asked).
        await self.client.patch("/api/auth/settings", json={"awayPolicy": "password"})
        await self.client.post("/api/users", json={"username": "marlina", "name": "Marlina", "role": "family",
                                                    "password": "another pass"})
        c, r, _ = await self.login("marlina", "another pass", headers=AWAY)
        self.assertEqual(r.status, 200)
        await c.close()

    async def test_certificate_from_the_trusted_proxy(self):
        await self.setup_admin()
        await self.client.patch("/api/auth/settings", json={"awayPolicy": "cert", "trustedProxies": ["127.0.0.1/32"]})
        relayed = {"X-Forwarded-For": "198.51.100.4"}
        c, r, body = await self.login(headers=relayed)
        self.assertEqual(r.status, 403)
        self.assertIn("certificate", body["error"])
        await c.close()
        # Caddy passes its placeholder text through when no certificate was given: that isn't one.
        # ...or the hash of nothing (what Caddy really sends on a connection without one).
        for fake in ("{http.request.tls.client.fingerprint}",
                     "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"):
            c, r, body = await self.login(headers={**relayed, "X-Client-Cert-Fingerprint": fake})
            self.assertEqual(r.status, 403, fake)
            await c.close()
        # Through the proxy the browser cookie is HTTPS-only, so sign in the way the app does (a token).
        fp = "3f" * 32  # a SHA-256 fingerprint
        c, r, body = await self.login(headers={**relayed, "X-Client-Cert-Fingerprint": fp}, kind="app")
        self.assertEqual(r.status, 200)
        app = {**relayed, "X-Client-Cert-Fingerprint": fp, "Authorization": "Bearer " + body["token"]}
        state = await (await c.get("/api/auth/state", headers=app)).json()
        self.assertEqual((state["where"], state["certificate"], state["signedIn"]), ("away", True, True))
        await c.close()

    async def test_maintenance_scripts_looking_at_the_dashboard_save_nothing(self):
        token = (Path(config.data_dir) / ".local_token").read_text().strip()
        r = await self.client.get("/api/dashboards/current", headers={"X-Newman-Local-Token": token})
        self.assertEqual(r.status, 200)
        self.assertTrue((await r.json())["pages"])
        self.assertEqual(self.h.store.dashboards("local"), [])

    async def test_home_network_through_the_proxy_is_home(self):
        # The proxy lets the LAN / home VPN in without a certificate; Newman agrees they're at home.
        await self.setup_admin()
        await self.client.patch("/api/auth/settings", json={"awayPolicy": "cert", "trustedProxies": ["127.0.0.1/32"]})
        for client_ip, where in (("10.1.1.40", "home"), ("10.8.0.6", "home"), ("198.51.100.4", "away"),
                                 ("127.0.0.1", "away")):  # the proxy itself isn't someone at home
            state = await (await self.client.get("/api/auth/state", headers={"X-Forwarded-For": client_ip})).json()
            self.assertEqual(state["where"], where, client_ip)
        # Only the proxy's own entry counts - a client can't claim to be at home.
        state = await (await self.client.get("/api/auth/state",
                                              headers={"X-Forwarded-For": "10.1.1.40, 198.51.100.4"})).json()
        self.assertEqual(state["where"], "away")
        c, r, _ = await self.login(headers={"X-Forwarded-For": "10.1.1.40"}, kind="app")
        self.assertEqual(r.status, 200)  # no certificate needed at home
        await c.close()

    async def test_tablet_sessions_cant_configure_and_need_a_pin_to_unlock(self):
        await self.setup_admin()
        c, r, body = await self.login(kind="tablet", deviceName="Hallway tablet")
        self.assertTrue(body["token"])
        tab = {"Authorization": "Bearer " + body["token"]}
        self.assertEqual((await c.get("/api/entities", headers=tab)).status, 200)
        self.assertEqual((await c.post("/api/automations", json={}, headers=tab)).status, 403)
        # By default a wall tablet just does it - it's inside the house.
        self.assertEqual((await c.post("/api/entities/fake.side/command", json={"command": "unlock"}, headers=tab)).status, 200)
        self.locks.calls.clear()
        await self.h.hub.update("fake.side", state="LOCKED")
        r = await self.client.patch("/api/auth/settings", json={"tabletSensitive": "pin"})
        self.assertEqual((await r.json())["tabletSensitive"], "pin")
        r = await c.post("/api/entities/fake.side/command", json={"command": "unlock"}, headers=tab)
        self.assertEqual(r.status, 403)
        self.assertIn("PIN", (await r.json())["error"])
        await self.client.post("/api/auth/pin", json={"password": "correct horse", "pin": "2468"})
        r = await c.post("/api/entities/fake.side/command", json={"command": "unlock", "pin": "1111"}, headers=tab)
        self.assertEqual(r.status, 403)
        r = await c.post("/api/entities/fake.side/command", json={"command": "unlock", "pin": "2468"}, headers=tab)
        self.assertEqual(r.status, 200)
        await self.h.hub.update("fake.side", state="UNLOCKED")
        r = await c.post("/api/entities/fake.side/command", json={"command": "lock"}, headers=tab)  # not sensitive
        self.assertEqual(r.status, 200)
        self.assertEqual(self.locks.calls, [("fake.side", "unlock"), ("fake.side", "lock")])
        await c.close()
        # The admin sees it in their devices and can sign it out.
        sessions = await (await self.client.get("/api/auth/sessions")).json()
        tablet = next(s for s in sessions if s["kind"] == "tablet")
        self.assertEqual(tablet["deviceName"], "Hallway tablet")
        await self.client.delete(f"/api/auth/sessions/{tablet['id']}")
        c2 = Browser(self.client)
        self.assertEqual((await c2.get("/api/entities", headers=tab)).status, 401)
        await c2.close()

    async def test_abandoned_sign_ins_are_not_live_and_expired_ones_are_gone(self):
        # A wall tablet that signs in again leaves its old sign-in behind (valid for a year): listed,
        # but not live, so "shown on" lists skip it. Expired sign-ins are removed altogether.
        await self.setup_admin()
        for name in ("Kitchen tablet", "Old tablet", "Expired tablet"):
            c, r, _ = await self.login(kind="tablet", deviceName=name)
            self.assertEqual(r.status, 200)
            await c.close()
        now, by = time.time(), {s["device_name"]: s["id"] for s in self.h.store.sessions()}
        self.h.store._exec("UPDATE sessions SET last_seen = ? WHERE id = ?", (now - 5 * 86400, by["Old tablet"]))
        self.h.store._exec("UPDATE sessions SET expires_at = ? WHERE id = ?", (now - 60, by["Expired tablet"]))
        sessions = await (await self.client.get("/api/auth/sessions")).json()
        self.assertEqual({s["deviceName"]: s["live"] for s in sessions},
                         {"Browser": True, "Kitchen tablet": True, "Old tablet": False})
        self.assertNotIn("Expired tablet", {s["device_name"] for s in self.h.store.sessions()})

    async def test_family_can_use_but_not_change(self):
        await self.setup_admin()
        await self.client.post("/api/users", json={"username": "marlina", "name": "Marlina", "role": "family",
                                                    "password": "another pass"})
        c, r, _ = await self.login("marlina", "another pass")
        await self.h.hub.update("fake.side", state="UNLOCKED")
        self.assertEqual((await c.get("/api/automations")).status, 200)
        self.assertEqual((await c.post("/api/entities/fake.side/command", json={"command": "lock"})).status, 200)
        self.assertEqual((await c.post("/api/automations", json={})).status, 403)
        self.assertEqual((await c.get("/api/users")).status, 403)
        self.assertEqual(self.locks.calls[-1], ("fake.side", "lock"))
        await c.close()

    async def test_guessing_from_outside_gets_locked_out(self):
        await self.setup_admin()
        await self.client.patch("/api/auth/settings", json={"awayPolicy": "password"})
        for _ in range(5):
            c, r, _ = await self.login(password="nope", headers=AWAY)
            self.assertEqual(r.status, 401)
            await c.close()
        c, r, body = await self.login(headers=AWAY)  # even the right password waits now
        self.assertEqual(r.status, 429)
        await c.close()
        failed = [e for e in self.h.store.events(50) if e["kind"] == "signin" and e["data"].get("failed")]
        self.assertEqual(len(failed), 5)

    async def test_dashboards_per_person_and_what_a_tablet_shows(self):
        await self.setup_admin()
        self.h.hub.upsert("fake.cam", kind="camera", name="Front Porch", integration="fake")
        self.h.hub.upsert("fake.light", kind="light", name="Dining Room", integration="fake")  # sorts first
        self.h.hub.upsert("fake.stat", kind="thermostat", name="Main Floor", integration="fake")
        await self.h.hub.update("fake.stat", state="cool", attrs={"currentTemp": 22.6, "targetTemp": 23})
        # A tablet's first visit lays out a starter dashboard from the house's devices.
        c, r, body = await self.login(kind="tablet", deviceName="Hallway tablet")
        tab = {"Authorization": "Bearer " + body["token"]}
        d = await (await c.get("/api/dashboards/current", headers=tab)).json()
        self.assertEqual([p["name"] for p in d["pages"]], ["Today", "Overview", "Security", "Lights", "Climate"])
        self.assertEqual((d["home"]["page"], d["home"]["morning"]["page"]), ("overview", "today"))  # Today in the mornings
        overview = {t["type"]: t for t in d["pages"][1]["tiles"]}
        self.assertEqual(overview["camera"]["config"]["entities"], ["fake.cam"])
        self.assertEqual(overview["dial"]["config"], {"entity": "fake.stat", "command": "set_temperature",
                                                     "modeCommand": "set_mode"})
        # The tablet can't rearrange it; the owner can, from the dashboard.
        self.assertEqual((await c.put(f"/api/dashboards/{d['id']}", json=d, headers=tab)).status, 403)
        d["pages"][0]["tiles"].append({"type": "attention", "x": 0, "y": 0, "w": 3, "h": 2})
        r = await self.client.put(f"/api/dashboards/{d['id']}", json=d)
        self.assertEqual(r.status, 400)
        self.assertIn("overlap", (await r.json())["error"])
        d["pages"][0]["tiles"].pop()
        d["name"] = "Hallway"
        d["names"] = {"fake.stat": "Upstairs", "fake.cam": "  "}  # "show as" names; blanks dropped
        d["pages"][0]["tiles"][0]["config"]["textSize"] = 85
        saved = await (await self.client.put(f"/api/dashboards/{d['id']}", json=d)).json()
        self.assertEqual((saved["names"], saved["pages"][0]["tiles"][0]["config"]["textSize"]), ({"fake.stat": "Upstairs"}, 85))
        self.assertEqual(self.h.hub.get("fake.stat").name, "Main Floor")  # the device itself keeps its name
        # A second dashboard, pinned to the tablet.
        other = await (await self.client.post("/api/dashboards", json={"name": "Kitchen"})).json()
        tablet = next(s for s in await (await self.client.get("/api/auth/sessions")).json() if s["kind"] == "tablet")
        await self.client.patch(f"/api/auth/sessions/{tablet['id']}", json={"dashboardId": other["id"]})
        self.assertEqual((await (await c.get("/api/dashboards/current", headers=tab)).json())["name"], "Kitchen")
        await c.close()
        # Family members arrange their own dashboards but can't touch someone else's.
        await self.client.post("/api/users", json={"username": "marlina", "name": "Marlina", "role": "family",
                                                    "password": "another pass"})
        m, _, _ = await self.login("marlina", "another pass")
        mine = await (await m.post("/api/dashboards", json={"name": "Mine"})).json()
        self.assertEqual((await m.put(f"/api/dashboards/{mine['id']}", json=mine)).status, 200)
        self.assertEqual((await m.get(f"/api/dashboards/{d['id']}")).status, 404)
        await m.close()

    async def test_webhook_secrets_only_reach_admins(self):
        import asyncio
        await self.setup_admin()
        r = await self.client.post("/api/integrations", json={"type": "webhooks", "config": {"hooks": [{"name": "Garden gate"}]}})
        self.assertEqual(r.status, 200, await r.text())
        await asyncio.sleep(0.2)
        mine = next(i for i in await (await self.client.get("/api/integrations")).json() if i["type"] == "webhooks")
        path = mine["adminDetails"]["addresses"][0]["path"]
        secret = path.rsplit("/", 1)[1]
        self.assertTrue(len(secret) > 20)
        self.assertEqual((await self.client.post(path, json={"hello": "world"})).status, 200)
        self.assertEqual((await self.client.post(path[:-4] + "xxxx")).status, 404)
        await self.client.post("/api/users", json={"username": "marlina", "name": "Marlina", "role": "family",
                                                    "password": "another pass"})
        c, _, _ = await self.login("marlina", "another pass")
        for url in ("/api/integrations", "/api/status", "/api/entities"):
            self.assertNotIn(secret, await (await c.get(url)).text(), url)
        await c.close()

    async def test_totp_matches_the_rfc_vectors(self):
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # RFC 6238's SHA-1 key, base32
        self.assertEqual(A._hotp(secret, 59 // 30), "287082")
        self.assertEqual(A._hotp(secret, 1111111109 // 30), "081804")
