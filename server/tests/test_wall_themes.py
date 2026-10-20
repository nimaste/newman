"""A wall dashboard's look: the theme (Midnight / Glass / Christmas, old "night-glass" = Midnight), the
Christmas settings and season (every year, over New Year too), the Climate glance tile's device filters,
and the Christmas pictures a household uploads (PNG / WebP, limits, who may, served to the wall)."""
import io
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("TOKEN_SECRET", "test-secret")

import aiohttp  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from PIL import Image  # noqa: E402

from newman import backup as B  # noqa: E402
from newman import dashboards as D  # noqa: E402
from newman import wall_pictures as WP  # noqa: E402
from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from test_locations import H  # noqa: E402

PAGES = [{"id": "a", "name": "A", "icon": "home", "tiles": []}]


def ent(kind, state=None, **attrs):
    return SimpleNamespace(id="x." + kind, kind=kind, state=state, attrs=attrs, name=kind)


def picture(fmt="PNG", size=(900, 300), alpha=True):
    img = Image.new("RGBA" if alpha else "RGB", size, (200, 30, 40, 0) if alpha else (200, 30, 40))
    img.paste((255, 255, 255, 255) if alpha else (255, 255, 255), (10, 10, 60, 60))
    out = io.BytesIO()
    img.save(out, fmt)
    return out.getvalue()


class ThemeSettingsTest(unittest.TestCase):
    def test_theme(self):
        self.assertEqual(D.clean({"pages": PAGES})[0]["theme"], "midnight")
        for t in ("midnight", "glass", "christmas"):
            self.assertEqual(D.clean({"pages": PAGES, "theme": t})[0]["theme"], t)
        self.assertEqual(D.clean({"pages": PAGES, "theme": "night-glass"}), (D.clean({"pages": PAGES})[0], []))
        clean, errors = D.clean({"pages": PAGES, "theme": "disco"})
        self.assertEqual((clean["theme"], errors), ("midnight", ["Unknown theme “disco”"]))
        # Saved before themes: clients get Midnight and the Christmas defaults.
        old = D.with_home({"pages": PAGES, "theme": "night-glass"})
        self.assertEqual(old["theme"], "midnight")
        self.assertEqual(old["christmas"], D.CHRISTMAS)
        self.assertEqual(D.starter(SimpleNamespace(all=lambda: []))["theme"], "midnight")

    def test_christmas_settings(self):
        c = D.clean({"pages": PAGES, "christmas": {"snow": False, "greeting": "  Happy   Holidays ", "characters": "pictures",
                                                   "place": "background", "season": {"from": "12-10", "to": "1-2"}}})[0]["christmas"]
        self.assertEqual(c, {"snow": False, "greeting": "Happy Holidays", "characters": "pictures", "place": "background",
                             "pictures": [], "season": {"from": "12-10", "to": "01-02"}})
        # An empty greeting stays empty (characters only); a long one is cut.
        self.assertEqual(D.clean_christmas({"greeting": ""})["greeting"], "")
        self.assertEqual(len(D.clean_christmas({"greeting": "x" * 90})["greeting"]), 40)
        # Season on with no dates: the default (December 1 to January 6).
        self.assertEqual(D.clean_christmas({"season": {}})["season"], {"from": "12-01", "to": "01-06"})
        self.assertIsNone(D.clean_christmas({"season": None})["season"])
        # Bad values: errors, defaults kept.
        clean, errors = D.clean({"pages": PAGES, "christmas": {"characters": "elves", "place": "roof",
                                                               "season": {"from": "02-30", "to": "01-06"}}})
        self.assertEqual(clean["christmas"]["characters"], "builtin")
        self.assertEqual(clean["christmas"]["place"], "clock")
        self.assertEqual(clean["christmas"]["season"], D.SEASON)
        self.assertEqual(len(errors), 3, errors)
        self.assertTrue(errors[2].startswith("Christmas season: "), errors)
        # Pictures: ids only, no repeats, at most three.
        pics = ["0123456789abcdef", "0123456789abcdef", "../../etc/passwd", "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb",
                "cccccccccccccccc"]
        self.assertEqual(D.clean_christmas({"pictures": pics})["pictures"], ["0123456789abcdef", "aaaaaaaaaaaaaaaa",
                                                                          "bbbbbbbbbbbbbbbb"])

    def test_season_dates(self):
        s = {"from": "12-01", "to": "01-06"}
        for day, on in ((date(2026, 11, 30), False), (date(2026, 12, 1), True), (date(2026, 12, 25), True),
                        (date(2026, 12, 31), True), (date(2027, 1, 1), True), (date(2027, 1, 6), True),
                        (date(2027, 1, 7), False), (date(2027, 7, 1), False)):
            self.assertEqual(D.season_active(s, day), on, day)
        self.assertFalse(D.season_active(None, date(2026, 12, 25)))
        self.assertTrue(D.season_active({"from": "12-20", "to": "12-26"}, date(2026, 12, 26)))
        self.assertFalse(D.season_active({"from": "12-20", "to": "12-26"}, date(2026, 12, 27)))
        # Christmas replaces the chosen theme in its season only.
        dash = {"theme": "glass", "christmas": {"season": s}}
        self.assertEqual(D.theme_now(dash, date(2026, 12, 24)), "christmas")
        self.assertEqual(D.theme_now(dash, date(2026, 10, 7)), "glass")
        self.assertEqual(D.theme_now({"theme": "glass"}, date(2026, 12, 24)), "glass")

    def test_climate_filters(self):
        self.assertTrue(D.matches(ent("thermostat", "heat", currentTemp=21.5, targetTemp=21), "temperature-setting"))
        self.assertFalse(D.matches(ent("light", "on"), "temperature-setting"))
        self.assertFalse(D.matches(ent("dns_filter", "on"), "temperature-setting"))  # a number, but minutes
        self.assertTrue(D.matches(ent("temperature", "21.4", humidity=46), "temperature"))
        self.assertTrue(D.matches(ent("thermostat", "heat", currentTemp=21.5), "temperature"))
        self.assertTrue(D.matches(ent("sensor", "19.5", unitOfMeasurement="°C"), "temperature"))
        self.assertFalse(D.matches(ent("sensor", "48", unitOfMeasurement="%"), "temperature"))
        self.assertFalse(D.matches(ent("server", "online", temperature=55), "temperature"))  # a CPU, not a room
        self.assertFalse(D.matches(ent("disk", "ok", temperature=41), "temperature"))
        self.assertFalse(D.matches(ent("sun", "above_horizon", elevation=30), "temperature"))  # degrees of angle
        spec = D.TYPES["climate"]
        self.assertEqual([f["key"] for f in spec["fields"]], ["entity", "rooms", "outside", "look", "tap", "hold", "textSize"])
        clean, errors = D.clean({"pages": [{"id": "a", "name": "A", "tiles": [
            {"type": "climate", "x": 7, "y": 0, "w": 5, "h": 8, "config": {"entity": "x", "rooms": ["y"], "page": "b"}}]}]})
        self.assertEqual(errors, [])
        self.assertEqual(clean["pages"][0]["tiles"][0]["config"]["rooms"], ["y"])


class PicturesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", self.data)
        self.h = H(self.data)
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        ids = {}
        for name, role in (("Marlina", "family"), ("Hallway", "tablet")):
            r = await self.client.post("/api/users", json={"username": name.lower(), "name": name, "role": role,
                                                          "password": "another pass"})
            ids[name] = (await r.json())["id"]
        self.chris = await self.token("chris", "correct horse")
        self.marlina = await self.token("marlina")
        self.tablet = await self.token("hallway", kind="tablet")
        st, self.dash = await self.call("POST", "/api/dashboards", self.chris,
                                        json={"name": "Hall", "pages": PAGES, "userId": ids["Hallway"]})
        self.assertEqual(st, 200)

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def token(self, username, password="another pass", kind="app"):
        r = await self.client.post("/api/auth/login", json={"username": username, "password": password, "kind": kind})
        return {"Authorization": "Bearer " + (await r.json())["token"]}

    async def call(self, method, path, who, **kw):
        r = await self.client.request(method, path, headers=who, **kw)
        try:
            body = await r.json()
        except Exception:
            body = None
        return r.status, body

    async def upload(self, who, raw, ctype="image/png"):
        return await self.call("POST", f"/api/dashboards/{self.dash['id']}/pictures", {**who, "Content-Type": ctype},
                               data=raw)

    async def test_upload_limits_serve_remove(self):
        did = self.dash["id"]
        st, body = await self.upload(self.chris, picture())
        self.assertEqual(st, 200, body)
        pid = body["pictures"][0]
        self.assertRegex(pid, r"^[0-9a-f]{16}$")
        st, d = await self.call("GET", f"/api/dashboards/{did}", self.chris)
        self.assertEqual(d["christmas"]["pictures"], [pid])
        # Served to the wall tablet (its dashboard) - also with ?token= as an <img> asks - shrunk, see-through.
        tok = self.tablet["Authorization"][7:]
        s = aiohttp.ClientSession()
        r = await s.get(self.client.make_url(WP.url(did, pid) + "?token=" + tok))
        self.assertEqual((r.status, r.headers["Content-Type"]), (200, "image/png"))
        img = Image.open(io.BytesIO(await r.read()))
        self.assertEqual((img.size, img.mode), ((600, 200), "RGBA"))
        self.assertEqual(img.getpixel((300, 150))[3], 0)
        self.assertEqual((await s.get(self.client.make_url(WP.url(did, pid)))).status, 401)
        await s.close()
        # Someone else's dashboard: not theirs to see or change.
        self.assertEqual((await self.call("GET", WP.url(did, pid), self.marlina))[0], 404)
        self.assertEqual((await self.upload(self.marlina, picture()))[0], 404)
        # A wall tablet can't change settings.
        self.assertEqual((await self.upload(self.tablet, picture()))[0], 403)
        # Bad uploads.
        self.assertEqual((await self.upload(self.chris, b"not a picture"))[0], 400)
        self.assertEqual((await self.upload(self.chris, picture("JPEG", alpha=False), "image/jpeg"))[0], 415)
        self.assertEqual((await self.upload(self.chris, picture("JPEG", alpha=False)))[0], 400)  # says PNG, isn't
        self.assertEqual((await self.upload(self.chris, b"\0" * (2 * 1024 * 1024 + 10)))[0], 413)
        # WebP works; the same picture twice is kept once; at most three.
        self.assertEqual((await self.upload(self.chris, picture("WEBP", (120, 120)), "image/webp"))[0], 200)
        st, body = await self.upload(self.chris, picture())
        self.assertEqual(len(body["pictures"]), 2)
        await self.upload(self.chris, picture(size=(50, 80)))
        self.assertEqual((await self.upload(self.chris, picture(size=(70, 70))))[0], 409)
        # Saving the dashboard from an editor never changes its pictures.
        st, d = await self.call("GET", f"/api/dashboards/{did}", self.chris)
        pics = d["christmas"]["pictures"]
        st, saved = await self.call("PUT", f"/api/dashboards/{did}", self.chris,
                                    json={**d, "christmas": {**d["christmas"], "pictures": []}})
        self.assertEqual((st, saved["christmas"]["pictures"]), (200, pics))
        # Kept in backups.
        names = __import__("zipfile").ZipFile(io.BytesIO(B.create(self.h.store._db, self.data, "t"))).namelist()
        self.assertIn(WP.path(self.data, did, pid).relative_to(self.data).as_posix(), names)
        # Removing one; then the whole dashboard takes the rest with it.
        st, body = await self.call("DELETE", WP.url(did, pid), self.chris)
        self.assertEqual((st, pid in body["pictures"]), (200, False))
        self.assertFalse(WP.path(self.data, did, pid).exists())
        self.assertEqual((await self.call("GET", WP.url(did, pid), self.chris))[0], 404)
        self.assertEqual((await self.call("DELETE", WP.url(did, pid), self.chris))[0], 404)
        self.assertTrue(WP.folder(self.data, did).exists())
        await self.call("DELETE", f"/api/dashboards/{did}", self.chris)
        self.assertFalse(WP.folder(self.data, did).exists())

    async def test_duplicate_copies_everything(self):
        did = self.dash["id"]
        st, d = await self.call("GET", f"/api/dashboards/{did}", self.chris)
        xmas = {"snow": False, "greeting": "Happy Holidays", "characters": "pictures", "place": "background",
                "season": {"from": "12-10", "to": "01-02"}}
        st, _ = await self.call("PUT", f"/api/dashboards/{did}", self.chris,
                                json={**d, "tabNames": "fit", "christmas": xmas, "names": {"x.y": "Garage"}})
        self.assertEqual(st, 200)
        await self.upload(self.chris, picture())
        st, body = await self.upload(self.chris, picture(size=(50, 80)))
        pics = body["pictures"]
        st, copy_ = await self.call("POST", "/api/dashboards", self.chris,
                                    json={"copyFrom": did, "name": "Copy of Hall", "userId": d["userId"]})
        self.assertEqual(st, 200, copy_)
        self.assertNotEqual(copy_["id"], did)
        self.assertEqual((copy_["name"], copy_["userId"], copy_["tabNames"], copy_["names"], copy_["isDefault"]),
                         ("Copy of Hall", d["userId"], "fit", {"x.y": "Garage"}, False))
        self.assertEqual(copy_["christmas"], {**xmas, "pictures": pics})
        self.assertEqual(copy_["pages"], d["pages"])
        # Its own files: they stay when the original goes.
        for pid in pics:
            self.assertEqual(WP.path(self.data, copy_["id"], pid).read_bytes(), WP.path(self.data, did, pid).read_bytes())
        await self.call("DELETE", f"/api/dashboards/{did}", self.chris)
        self.assertEqual((await self.call("GET", WP.url(copy_["id"], pics[0]), self.chris))[0], 200)
        # Only your own (an admin: anyone's), and a plain create can't claim pictures it doesn't have.
        self.assertEqual((await self.call("POST", "/api/dashboards", self.marlina, json={"copyFrom": copy_["id"]}))[0], 404)
        st, plain = await self.call("POST", "/api/dashboards", self.chris,
                                    json={"name": "X", "pages": PAGES, "christmas": {"pictures": pics}})
        self.assertEqual((st, plain["christmas"]["pictures"]), (200, []))


if __name__ == "__main__":
    unittest.main()
