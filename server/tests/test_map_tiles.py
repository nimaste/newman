"""Map data through the hub: OpenFreeMap's styles, tiles, fonts and sprites fetched once from that one
server (named as its policy asks), kept, links rewritten back to the hub, served to signed-in maps only."""
import asyncio
import gzip
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman import api as A  # noqa: E402
from newman.config import config  # noqa: E402
from test_auth import Browser, H  # noqa: E402


class MapDataTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.hits, self.agents = [], []
        self.down = False
        self.slow = 0

        async def any_file(req):  # a fake OpenFreeMap
            self.hits.append(req.path)
            self.agents.append(req.headers.get("User-Agent", ""))
            if self.slow:
                await asyncio.sleep(self.slow)
            if self.down:
                return web.Response(status=500)
            up = self.upstream
            p = req.path
            if p == "/styles/positron":
                body = json.dumps({"version": 8, "sprite": up + "sprites/ofm_f384/ofm",
                                   "glyphs": up + "fonts/{fontstack}/{range}.pbf",
                                   "sources": {"openmaptiles": {"type": "vector", "url": up + "planet"},
                                               "ne2": {"type": "raster", "tiles": [up + "natural_earth/ne2sr/{z}/{x}/{y}.png"]}},
                                   "layers": [{"id": "bg", "type": "background"}],
                                   "other": "https://example.com/not-ours"}).encode()
                return web.Response(body=gzip.compress(body), headers={"Content-Encoding": "gzip", "Content-Type": "application/json"})
            if p == "/planet":
                body = json.dumps({"tilejson": "3.0.0", "tiles": [up + "planet/20260927_080001_pt/{z}/{x}/{y}.pbf"]}).encode()
                return web.Response(body=body, content_type="application/json")
            if p.endswith(".pbf"):
                return web.Response(body=gzip.compress(b"PBF " + p.encode()), headers={"Content-Encoding": "gzip"})
            if p.endswith(".png"):
                return web.Response(body=b"\x89PNG fake", content_type="image/png")
            return web.Response(status=404)
        fake = web.Application()
        fake.router.add_get("/{tail:.*}", any_file)
        self.fake = TestServer(fake)
        await self.fake.start_server()
        self.upstream = f"http://127.0.0.1:{self.fake.port}/"
        self.old = A.MAP_UPSTREAM
        A.MAP_UPSTREAM = self.upstream
        self.client = TestClient(TestServer(A.build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})

    async def asyncTearDown(self):
        A.MAP_UPSTREAM = self.old
        await self.client.close()
        await self.fake.close()
        self.tmp.cleanup()

    async def test_signed_in_only(self):
        stranger = Browser(self.client)
        try:
            r = await stranger.get("/api/map/ofm/planet/20260927_080001_pt/3/2/1.pbf")
            self.assertEqual(r.status, 401)
        finally:
            await stranger.close()
        self.assertEqual(self.hits, [])

    async def test_tile_fetched_once_kept_and_served_as_protobuf(self):
        path = "/api/map/ofm/planet/20260927_080001_pt/3/2/1.pbf"
        for _ in range(2):
            r = await self.client.get(path)
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers["Content-Type"], "application/x-protobuf")
            self.assertEqual(await r.read(), b"PBF /planet/20260927_080001_pt/3/2/1.pbf")  # valid, decompressed
        self.assertEqual(self.hits, ["/planet/20260927_080001_pt/3/2/1.pbf"])  # the second was the hub's copy
        self.assertTrue(self.agents[0].startswith("Newman-home-hub/"))
        # Sent compressed to a browser that takes gzip, plain to one that doesn't.
        r = await self.client.get(path, headers={"Accept-Encoding": "gzip"}, auto_decompress=False)
        self.assertEqual(r.headers.get("Content-Encoding"), "gzip")
        self.assertEqual(gzip.decompress(await r.read()), b"PBF /planet/20260927_080001_pt/3/2/1.pbf")
        r = await self.client.get(path, headers={"Accept-Encoding": "identity"}, auto_decompress=False)
        self.assertIsNone(r.headers.get("Content-Encoding"))
        self.assertEqual(await r.read(), b"PBF /planet/20260927_080001_pt/3/2/1.pbf")
        # Pictures come as they are.
        r = await self.client.get("/api/map/ofm/natural_earth/ne2sr/2/1/1.png")
        self.assertEqual((r.status, r.headers["Content-Type"], await r.read()), (200, "image/png", b"\x89PNG fake"))

    async def test_fonts_with_spaces_and_commas(self):
        r = await self.client.get("/api/map/ofm/fonts/Noto%20Sans%20Regular,Noto%20Sans%20Bold/0-255.pbf")
        self.assertEqual(r.status, 200)
        self.assertEqual(self.hits, ["/fonts/Noto Sans Regular,Noto Sans Bold/0-255.pbf"])

    async def test_style_and_tilejson_links_point_back_at_the_hub(self):
        r = await self.client.get("/api/map/ofm/styles/positron")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.headers["Content-Type"], "application/json")
        text = await r.text()
        self.assertNotIn(self.upstream, text)
        style = json.loads(text)
        self.assertEqual(style["sprite"], "/api/map/ofm/sprites/ofm_f384/ofm")
        self.assertEqual(style["glyphs"], "/api/map/ofm/fonts/{fontstack}/{range}.pbf")
        self.assertEqual(style["sources"]["openmaptiles"]["url"], "/api/map/ofm/planet")
        self.assertEqual(style["sources"]["ne2"]["tiles"], ["/api/map/ofm/natural_earth/ne2sr/{z}/{x}/{y}.png"])
        self.assertEqual(style["other"], "https://example.com/not-ours")  # only OpenFreeMap's links change
        r = await self.client.get("/api/map/ofm/planet")
        self.assertEqual((await r.json())["tiles"], ["/api/map/ofm/planet/20260927_080001_pt/{z}/{x}/{y}.pbf"])

    async def test_only_that_server_and_no_climbing_out(self):
        for path in ("/api/map/ofm/../../etc/passwd", "/api/map/ofm/fonts/%2e%2e/%2e%2e/x.pbf",
                     "/api/map/ofm/fonts/..%2F..%2Fsecret.pbf", "/api/map/ofm/%2Fetc/passwd.json",
                     "/api/map/ofm/http:%2F%2Fevil.example/x.pbf", "/api/map/ofm/@evil.example/x.pbf",
                     "/api/map/ofm/styles/positron.exe", "/api/map/ofm/a/b/c/d/e/f/g/h/i.pbf",
                     "/api/map/ofm/.hidden/x.pbf", "/api/map/ofm/fonts/a%00b/0-255.pbf"):
            r = await self.client.get(path)
            self.assertIn(r.status, (403, 404), path)
        self.assertEqual(self.hits, [])
        # And whatever the path, the fetch goes to MAP_UPSTREAM's host and nowhere else.
        r = await self.client.get("/api/map/ofm/styles/positron")
        self.assertEqual(r.status, 200)
        self.assertEqual(self.hits, ["/styles/positron"])

    async def test_one_fetch_however_many_ask_at_once(self):
        self.slow = 0.2
        rs = await asyncio.gather(*(self.client.get("/api/map/ofm/planet/v/5/5/5.pbf") for _ in range(6)))
        self.assertEqual([r.status for r in rs], [200] * 6)
        self.assertEqual(self.hits, ["/planet/v/5/5/5.pbf"])

    def age_everything(self):
        for f in (config.data_dir / "map-tiles" / "ofm").rglob("*"):
            if f.is_file():
                os.utime(f, (time.time() - 86400, time.time() - 86400))  # the style is due a refresh

    async def test_old_copy_served_at_once_and_refreshed_behind(self):
        r = await self.client.get("/api/map/ofm/styles/positron")
        self.assertEqual(r.status, 200)
        self.age_everything()
        self.slow = 0.5  # a slow upstream doesn't hold the map up: the kept copy comes straight away
        t = time.monotonic()
        r = await self.client.get("/api/map/ofm/styles/positron")
        self.assertEqual(r.status, 200)
        self.assertLess(time.monotonic() - t, 0.4)
        self.assertIn("/api/map/ofm/planet", await r.text())
        await asyncio.sleep(0.8)
        self.assertEqual(self.hits, ["/styles/positron", "/styles/positron"])  # refreshed behind
        f = next(x for x in (config.data_dir / "map-tiles" / "ofm").rglob("*") if x.is_file())
        self.assertLess(time.time() - f.stat().st_mtime, 60)

    async def test_old_copy_beats_nothing_and_unknown_is_404(self):
        r = await self.client.get("/api/map/ofm/styles/positron")
        self.assertEqual(r.status, 200)
        self.age_everything()
        self.down = True
        r = await self.client.get("/api/map/ofm/styles/positron")
        self.assertEqual(r.status, 200)  # upstream failing: the kept copy is still served
        await asyncio.sleep(0.2)
        self.assertEqual(len(self.hits), 2)
        self.down = False
        r = await self.client.get("/api/map/ofm/styles/nonesuch")
        self.assertEqual(r.status, 404)


class MapTokenTests(unittest.IsolatedAsyncioTestCase):
    """Apple Maps: /api/map/token hands signed-in maps a MapKit JS token when a key is set up."""

    KEYS = ("mapkit_key_id", "mapkit_team_id", "mapkit_key_path", "apns_team_id", "apns_key_id", "apns_key_path")

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.saved = {k: getattr(config, k) for k in self.KEYS}
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.key_file = data / "mapkit.p8"
        self.key_file.write_bytes(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                         serialization.NoEncryption()))
        self.set(mapkit_key_id="ABC123DEFG", mapkit_team_id="", mapkit_key_path=str(self.key_file),
                 apns_team_id="T342YL2VCE")
        self.h = H(data)
        self.client = TestClient(TestServer(A.build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})

    async def asyncTearDown(self):
        self.set(**self.saved)
        await self.client.close()
        self.tmp.cleanup()

    def set(self, **kw):
        for k, v in kw.items():
            object.__setattr__(config, k, v)

    async def token(self):
        r = await self.client.get("/api/map/token")
        self.assertEqual(r.status, 200)
        return await r.json()

    async def test_configured_gives_a_verifiable_es256_token(self):
        t0 = int(time.time())
        body = await self.token()
        self.assertEqual(body["provider"], "apple")
        hdr = jwt.get_unverified_header(body["token"])
        self.assertEqual(hdr, {"alg": "ES256", "kid": "ABC123DEFG", "typ": "JWT"})
        claims = jwt.decode(body["token"], self.key.public_key(), algorithms=["ES256"])
        self.assertEqual(claims["iss"], "T342YL2VCE")  # the APNs team id unless MAPKIT_TEAM_ID says otherwise
        self.assertLessEqual(abs(claims["iat"] - t0), 2)
        self.assertEqual(claims["exp"] - claims["iat"], 30 * 60)
        self.assertEqual(body["expires"], claims["exp"])
        self.assertNotIn("origin", claims)  # reached at several addresses: not tied to one
        self.assertNotIn("PRIVATE", json.dumps(body))
        # Reused while it has more than 5 minutes left...
        self.assertEqual((await self.token())["token"], body["token"])
        # ...and a fresh one after that.
        with mock.patch("newman.mapkit.time.time", return_value=claims["exp"] - 4 * 60):
            later = await self.token()
        self.assertNotEqual(later["token"], body["token"])
        self.assertEqual(jwt.decode(later["token"], self.key.public_key(), algorithms=["ES256"],
                                    options={"verify_exp": False, "verify_iat": False})["iat"], claims["exp"] - 4 * 60)

    async def test_own_team_id(self):
        self.set(mapkit_team_id="OTHERTEAM1")
        body = await self.token()
        self.assertEqual(jwt.decode(body["token"], self.key.public_key(), algorithms=["ES256"])["iss"], "OTHERTEAM1")

    async def test_same_key_as_apns_by_default(self):
        # One Apple key with both services: MAPKIT_KEY_ID = the APNs key id, no MAPKIT_KEY_PATH -> the APNs key file.
        self.set(mapkit_key_path="", apns_key_id="ABC123DEFG", apns_key_path=str(self.key_file))
        body = await self.token()
        self.assertEqual(body["provider"], "apple")
        jwt.decode(body["token"], self.key.public_key(), algorithms=["ES256"])
        # A different key id, no path: /app/keys/mapkit.p8 (not there in tests -> OpenFreeMap).
        self.set(apns_key_id="OTHERKEY99")
        from newman.mapkit import MapKitTokens
        self.assertEqual(MapKitTokens.settings()[2], "/app/keys/mapkit.p8")

    async def test_not_configured_means_openfreemap(self):
        self.set(mapkit_key_id="")
        self.assertEqual(await self.token(), {"provider": "openfreemap"})

    async def test_signed_out_401(self):
        stranger = Browser(self.client)
        try:
            r = await stranger.get("/api/map/token")
            self.assertEqual(r.status, 401)
        finally:
            await stranger.close()

    async def test_unreadable_key_means_openfreemap_logged_once(self):
        self.set(mapkit_key_path=str(self.key_file.with_name("missing.p8")))
        with self.assertLogs("newman.mapkit", level="WARNING") as logs:
            for _ in range(3):
                self.assertEqual(await self.token(), {"provider": "openfreemap"})
        self.assertEqual(len(logs.records), 1)
        self.assertIn("missing.p8", logs.output[0])
        # A key that isn't a key: the same.
        bad = self.key_file.with_name("bad.p8")
        bad.write_text("not a key")
        self.set(mapkit_key_path=str(bad))
        with self.assertLogs("newman.mapkit", level="WARNING") as logs:
            self.assertEqual(await self.token(), {"provider": "openfreemap"})
        self.assertNotIn("not a key", logs.output[0])
