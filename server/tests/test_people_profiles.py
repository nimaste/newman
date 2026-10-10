"""How people look on the family map: everyone has a colour (distinct palette defaults, kept), a photo
they upload (centre-cropped, 256 px JPEG, served to those who see names, cached by version), admins
can change anyone's, and open sockets hear about it."""
import asyncio
import io
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

import aiohttp  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from PIL import Image  # noqa: E402

from newman import profiles as PF  # noqa: E402
from newman import backup as B  # noqa: E402
from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from test_locations import HOME, H  # noqa: E402


def picture(w, h, fmt="PNG"):
    """Left half red, right half blue - so the crop is visible."""
    img = Image.new("RGB", (w, h), (0, 0, 255))
    img.paste((255, 0, 0), (0, 0, w // 2, h))
    out = io.BytesIO()
    img.save(out, fmt)
    return out.getvalue()


class ProfileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", self.data)
        self.h = H(self.data)
        self.h.store.save_integration({"id": "weather", "type": "weather", "name": "Weather",
                                       "config": {"place": "Toronto", "latitude": HOME[0], "longitude": HOME[1]}})
        self.app = build_app(self.h)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        for name, role in (("Marlina", "family"), ("Dante", "family"), ("Visitor", "guest"), ("Hallway", "tablet")):
            await self.client.post("/api/users", json={"username": name.lower(), "name": name, "role": role,
                                                       "password": "another pass"})
        self.chris = await self.token("chris", "correct horse")
        self.marlina, self.dante = await self.token("marlina"), await self.token("dante")
        self.guest = await self.token("visitor")
        self.tablet = await self.token("hallway", kind="tablet")
        self.pid = {p["name"]: p["id"] for p in self.h.store.people()}

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def token(self, username, password="another pass", kind="app"):
        s = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        r = await s.post(self.client.make_url("/api/auth/login"),
                         json={"username": username, "password": password, "kind": kind})
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

    async def people(self, who):
        st, people = await self.call("GET", "/api/people", who)
        self.assertEqual(st, 200)
        return {p["name"]: p for p in people}

    async def upload(self, who, raw, ctype="image/png", path="/api/me/photo"):
        r = await self.client.put(path, data=raw, headers={**who, "Content-Type": ctype})
        try:
            body = await r.json()
        except Exception:
            body = None
        return r.status, body

    # -- colours ------------------------------------------------------------------------------------

    async def test_default_colours_are_distinct_and_stable(self):
        first = {n: p["color"] for n, p in (await self.people(self.chris)).items()}
        self.assertEqual(set(first), {"Chris", "Marlina", "Dante"})
        self.assertEqual(len(set(first.values())), 3)
        self.assertTrue(all(c in PF.PALETTE for c in first.values()))
        # The same for every viewer and every time - and a newcomer doesn't shift anyone.
        self.assertEqual({n: p["color"] for n, p in (await self.people(self.tablet)).items()}, first)
        await self.call("POST", "/api/people", self.chris, json={"name": "Nonna"})
        again = {n: p["color"] for n, p in (await self.people(self.marlina)).items()}
        self.assertEqual({n: again[n] for n in first}, first)
        self.assertNotIn(again["Nonna"], first.values())
        # Picking a colour someone else had by default doesn't change theirs.
        st, me = await self.call("PUT", "/api/me/profile", self.dante, json={"color": first["Chris"].lower()})
        self.assertEqual((st, me["color"]), (200, first["Chris"]))
        self.assertEqual((await self.people(self.chris))["Chris"]["color"], first["Chris"])
        # Palette exhausted: least-used colours, never missing.
        for i in range(12):
            self.h.store.add_person(f"Guest {i}")
        everyone = await self.people(self.chris)
        self.assertTrue(all(PF.COLOR.match(p["color"]) for p in everyone.values()))
        self.assertEqual(PF.next_color(list(PF.PALETTE) + [PF.PALETTE[0]]), PF.PALETTE[1])

    async def test_profile_colour_own_and_admin(self):
        st, me = await self.call("PUT", "/api/me/profile", self.marlina, json={"color": "#12ab9F"})
        self.assertEqual((st, me["color"], me["name"]), (200, "#12AB9F", "Marlina"))
        self.assertEqual((await self.people(self.dante))["Marlina"]["color"], "#12AB9F")
        for bad in ("red", "#12ab9", "#12ab9fz", 12, None):
            self.assertEqual((await self.call("PUT", "/api/me/profile", self.marlina, json={"color": bad}))[0], 400, bad)
        # An admin changes anyone's; others can't; a wall tablet and guests have no profile.
        mid = self.pid["Marlina"]
        st, p = await self.call("PUT", f"/api/people/{mid}/profile", self.chris, json={"color": "#000000"})
        self.assertEqual((st, p["color"]), (200, "#000000"))
        self.assertEqual((await self.call("PUT", f"/api/people/{mid}/profile", self.dante, json={"color": "#111111"}))[0], 403)
        self.assertEqual((await self.call("PUT", "/api/me/profile", self.tablet, json={"color": "#111111"}))[0], 403)
        self.assertEqual((await self.call("PUT", "/api/me/profile", self.guest, json={"color": "#111111"}))[0], 404)
        self.assertEqual((await self.call("PUT", "/api/people/p_nobody/profile", self.chris, json={"color": "#111111"}))[0], 404)

    # -- photos --------------------------------------------------------------------------------------

    async def test_photo_upload_crop_resize_serve_and_remove(self):
        st, body = await self.upload(self.dante, picture(600, 300))
        self.assertEqual(st, 200, body)
        url = body["photo"]
        self.assertRegex(url, r"^/api/people/[^/]+/photo\?v=[0-9a-f]{12}$")
        self.assertEqual((await self.people(self.chris))["Dante"]["photo"], url)
        self.assertIsNone((await self.people(self.chris))["Chris"]["photo"])
        r = await self.client.get(url, headers=self.marlina)
        self.assertEqual((r.status, r.headers["Content-Type"]), (200, "image/jpeg"))
        self.assertIn("private", r.headers["Cache-Control"])
        self.assertIn("max-age=31536000", r.headers["Cache-Control"])
        img = Image.open(io.BytesIO(await r.read()))
        self.assertEqual((img.format, img.size), ("JPEG", (256, 256)))
        # Centre square of a red|blue picture: the left half red, the right half blue.
        left, right = img.getpixel((40, 128)), img.getpixel((215, 128))
        self.assertGreater(left[0], 200)
        self.assertGreater(right[2], 200)
        # A tablet sees it too; a guest doesn't; signed out doesn't.
        self.assertEqual((await self.client.get(url, headers=self.tablet)).status, 200)
        self.assertEqual((await self.client.get(url, headers=self.guest)).status, 404)
        s = aiohttp.ClientSession()
        self.assertEqual((await s.get(self.client.make_url(url))).status, 401)
        await s.close()
        # Replacing it changes the version; JPEG and WebP work too.
        st, body2 = await self.upload(self.dante, picture(300, 500, "JPEG"), "image/jpeg")
        self.assertEqual(st, 200)
        self.assertNotEqual(body2["photo"], url)
        self.assertEqual((await self.upload(self.dante, picture(64, 64, "WEBP"), "image/webp"))[0], 200)
        # Bad uploads.
        self.assertEqual((await self.upload(self.dante, b"not a picture"))[0], 400)
        self.assertEqual((await self.upload(self.dante, picture(10, 10), "text/plain"))[0], 415)
        self.assertEqual((await self.upload(self.dante, b"\0" * (3 * 1024 * 1024 + 10), "image/jpeg"))[0], 413)
        # Only an admin sets someone else's.
        did = self.pid["Dante"]
        self.assertEqual((await self.upload(self.marlina, picture(50, 50), path=f"/api/people/{did}/photo"))[0], 403)
        self.assertEqual((await self.upload(self.chris, picture(50, 50), path=f"/api/people/{did}/photo"))[0], 200)
        self.assertEqual((await self.upload(self.tablet, picture(50, 50)))[0], 403)
        # Kept under the data folder, and in backups.
        path = PF.photo_path(self.data, did)
        self.assertTrue(path.is_file())
        names = __import__("zipfile").ZipFile(io.BytesIO(B.create(self.h.store._db, self.data, "t"))).namelist()
        self.assertIn(path.relative_to(self.data).as_posix(), names)
        # Removing it: their own, or an admin.
        self.assertEqual((await self.call("DELETE", f"/api/people/{did}/photo", self.marlina))[0], 403)
        st, body = await self.call("DELETE", "/api/me/photo", self.dante)
        self.assertEqual((st, body), (200, {"photo": None}))
        self.assertFalse(path.is_file())
        self.assertIsNone((await self.people(self.chris))["Dante"]["photo"])
        self.assertEqual((await self.client.get(url, headers=self.chris)).status, 404)
        await self.upload(self.chris, picture(50, 50), path=f"/api/people/{did}/photo")
        self.assertEqual((await self.call("DELETE", f"/api/people/{did}/photo", self.chris))[0], 200)
        self.assertFalse(path.is_file())
        # Removing the person removes their photo.
        await self.upload(self.dante, picture(50, 50))
        self.assertTrue(path.is_file())
        await self.call("DELETE", f"/api/people/{did}", self.chris)
        self.assertFalse(path.is_file())

    # -- live updates ----------------------------------------------------------------------------------

    async def test_sockets_hear_about_colour_and_photo_changes(self):
        async def socket(who):
            ws = await self.client.ws_connect("/api/ws?token=" + who["Authorization"][7:])
            self.assertEqual((await ws.receive_json(timeout=2))["type"], "hello")
            return ws

        chris, tablet, guest = await socket(self.chris), await socket(self.tablet), await socket(self.guest)
        await self.call("PUT", "/api/me/profile", self.dante, json={"color": "#224466"})
        for ws in (chris, tablet):
            msg = await ws.receive_json(timeout=2)
            self.assertEqual((msg["type"], msg["person"]["name"], msg["person"]["color"]), ("person", "Dante", "#224466"))
            self.assertFalse(msg["person"]["sharing"])  # not sharing: still no location
        await self.upload(self.dante, picture(80, 80))
        for ws in (chris, tablet):
            msg = await ws.receive_json(timeout=2)
            self.assertTrue(msg["person"]["photo"].startswith("/api/people/"))
        await self.call("DELETE", "/api/me/photo", self.dante)
        self.assertIsNone((await chris.receive_json(timeout=2))["person"]["photo"])
        with self.assertRaises(asyncio.TimeoutError):
            await guest.receive_json(timeout=0.4)
        for ws in (chris, tablet, guest):
            await ws.close()


if __name__ == "__main__":
    unittest.main()
