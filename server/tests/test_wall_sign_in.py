"""The wall page signs in only with a wall tablet's own account, and its sign-in has its own cookie
(newman_wall): signing in on the wall page in a browser never replaces that browser's dashboard sign-in
(newman_session), or the other way round. A wall tablet signed in before the wall cookie keeps working."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from test_auth import Browser, H  # noqa: E402

WALL = "This page is for wall tablet accounts - sign in with the tablet's own account (add one under Accounts)"


class WallSignInTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        # The admin's dashboard sign-in, in this client's cookies.
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        r = await self.client.post("/api/users", json={"username": "kitchen", "name": "Kitchen", "role": "tablet",
                                                       "password": "wall tablet pass"})
        self.kitchen = (await r.json())["id"]
        self.open = []

    async def asyncTearDown(self):
        for c in self.open:
            await c.close()
        await self.client.close()
        self.tmp.cleanup()

    def browser(self) -> Browser:
        b = Browser(self.client)
        self.open.append(b)
        return b

    def cookies(self, jar) -> set[str]:
        return {c.key for c in jar}

    async def state(self, c, **kw):
        return await (await c.get("/api/auth/state", **kw)).json()

    async def test_the_wall_page_takes_only_wall_tablet_accounts(self):
        await self.client.post("/api/users", json={"username": "marlina", "name": "Marlina", "role": "family",
                                                    "password": "another pass"})
        for username, password in (("chris", "correct horse"), ("marlina", "another pass")):
            b = self.browser()
            r = await b.post("/api/auth/login", json={"username": username, "password": password, "kind": "tablet"})
            self.assertEqual(r.status, 403)
            self.assertEqual((await r.json())["error"], WALL)
            self.assertEqual(self.cookies(b._s.cookie_jar), set())
        self.assertEqual([s for s in self.h.store.sessions() if s["kind"] == "tablet"], [])
        # And the other way round, as before: a wall tablet's account only on the wall.
        b = self.browser()
        r = await b.post("/api/auth/login", json={"username": "kitchen", "password": "wall tablet pass"})
        self.assertEqual((r.status, (await r.json())["error"]),
                         (403, "This is a wall tablet's account - sign in with it on the wall tablet"))
        r = await b.post("/api/auth/login", json={"username": "kitchen", "password": "wall tablet pass", "kind": "tablet"})
        self.assertEqual(r.status, 200)
        self.assertEqual(self.cookies(b._s.cookie_jar), {"newman_wall"})

    async def test_wall_and_dashboard_sign_ins_share_a_browser(self):
        # The wall page signed in in the browser the admin uses for the dashboard.
        r = await self.client.post("/api/auth/login", json={"username": "kitchen", "password": "wall tablet pass",
                                                            "kind": "tablet"})
        self.assertEqual(r.status, 200)
        wall = {"Authorization": "Bearer " + (await r.json())["token"]}
        self.assertEqual(self.cookies(self.client.session.cookie_jar), {"newman_session", "newman_wall"})
        # The dashboard is still the admin's own sign-in, and can change things.
        s = await self.state(self.client)
        self.assertEqual((s["user"]["name"], s["session"]["kind"]), ("Chris", "browser"))
        self.assertEqual((await self.client.patch("/api/auth/settings", json={"tabletSensitive": "none"})).status, 200)
        # The wall page (its token) is the wall tablet.
        s = await self.state(self.client, headers=wall)
        self.assertEqual((s["user"]["name"], s["session"]["kind"]), ("Kitchen", "tablet"))
        self.assertEqual((await self.client.patch("/api/auth/settings", json={}, headers=wall)).status, 403)
        # A real wall tablet has only the wall cookie: its pictures and video (no token) are the tablet's.
        b = self.browser()
        await b.post("/api/auth/login", json={"username": "kitchen", "password": "wall tablet pass", "kind": "tablet"})
        s = await self.state(b)
        self.assertEqual((s["user"]["name"], s["session"]["kind"]), ("Kitchen", "tablet"))
        self.assertEqual((await b.get("/api/entities")).status, 200)

    async def test_signing_out_clears_only_its_own_cookie(self):
        r = await self.client.post("/api/auth/login", json={"username": "kitchen", "password": "wall tablet pass",
                                                            "kind": "tablet"})
        wall = {"Authorization": "Bearer " + (await r.json())["token"]}
        # The wall page signs out: the dashboard stays signed in.
        self.assertEqual((await self.client.post("/api/auth/logout", headers=wall)).status, 200)
        self.assertEqual(self.cookies(self.client.session.cookie_jar), {"newman_session"})
        self.assertEqual((await self.state(self.client))["user"]["name"], "Chris")
        self.assertEqual((await self.client.get("/api/entities", headers=wall)).status, 401)
        # Signed in on the wall again, then out of the dashboard: the wall stays signed in.
        r = await self.client.post("/api/auth/login", json={"username": "kitchen", "password": "wall tablet pass",
                                                            "kind": "tablet"})
        wall = {"Authorization": "Bearer " + (await r.json())["token"]}
        self.assertEqual((await self.client.post("/api/auth/logout")).status, 200)
        self.assertEqual(self.cookies(self.client.session.cookie_jar), {"newman_wall"})
        self.assertEqual((await self.state(self.client, headers=wall))["user"]["name"], "Kitchen")

    async def test_a_wall_tablet_signed_in_before_the_wall_cookie_keeps_working(self):
        # Its sign-in lives in the dashboard's cookie (as the hub set it then).
        r = await self.browser().post("/api/auth/login", json={"username": "kitchen", "password": "wall tablet pass",
                                                               "kind": "tablet"})
        token = (await r.json())["token"]
        old = self.browser()
        old._s.cookie_jar.update_cookies({"newman_session": token}, self.client.make_url("/"))
        s = await self.state(old)
        self.assertEqual((s["signedIn"], s["user"]["name"], s["session"]["kind"]), (True, "Kitchen", "tablet"))
        self.assertEqual((await old.post("/api/tablet/battery", json={"level": 80, "charging": True,
                                                                      "plugged": "ac"})).status, 204)
        # Signing it out clears that cookie.
        self.assertEqual((await old.post("/api/auth/logout")).status, 200)
        self.assertEqual(self.cookies(old._s.cookie_jar), set())
        self.assertFalse((await self.state(old))["signedIn"])

    async def test_signing_the_wall_in_again_moves_an_old_wall_sign_in_out_of_the_dashboards_cookie(self):
        r = await self.browser().post("/api/auth/login", json={"username": "kitchen", "password": "wall tablet pass",
                                                               "kind": "tablet"})
        old = self.browser()
        old._s.cookie_jar.update_cookies({"newman_session": (await r.json())["token"]}, self.client.make_url("/"))
        r = await old.post("/api/auth/login", json={"username": "kitchen", "password": "wall tablet pass", "kind": "tablet"})
        self.assertEqual(r.status, 200)
        self.assertEqual(self.cookies(old._s.cookie_jar), {"newman_wall"})
        # A dashboard sign-in in that cookie stays where it is.
        r = await self.client.post("/api/auth/login", json={"username": "kitchen", "password": "wall tablet pass",
                                                            "kind": "tablet"})
        self.assertEqual(self.cookies(self.client.session.cookie_jar), {"newman_session", "newman_wall"})
        self.assertEqual((await self.state(self.client))["user"]["name"], "Chris")


if __name__ == "__main__":
    unittest.main()
