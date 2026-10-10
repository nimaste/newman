"""Connecting apps before sign-in: /api/hello (no sign-in), the outside address and home name,
/api/connect-info (admins), the connect link and the mDNS TXT record."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman import connect as CN  # noqa: E402
from newman.api import build_app, web_build_id  # noqa: E402
from newman.config import config  # noqa: E402

from test_auth import Browser, H  # noqa: E402


def adapter(name, *ips):
    return SimpleNamespace(nice_name=name, ips=[SimpleNamespace(ip=ip) for ip in ips])


class PureTests(unittest.TestCase):
    def test_outside_address_is_normalised_and_checked(self):
        self.assertEqual(CN.normalize_outside("home.example.com"), "https://home.example.com/")
        self.assertEqual(CN.normalize_outside(" https://Home.Example.com:8443/newman "), "https://home.example.com:8443/newman/")
        self.assertEqual(CN.normalize_outside("http://10.1.1.2:8093"), "http://10.1.1.2:8093/")
        self.assertEqual(CN.normalize_outside(""), "")
        for bad in ("http://home.example.com", "ftp://x.example.com", "https://x.example.com/?a=1", "https://a b.com",
                    "https://user:pw@x.example.com", "https://localhostonly", "https://x.example.com:99999"):
            with self.assertRaises(ValueError, msg=bad):
                CN.normalize_outside(bad)

    def test_home_name(self):
        self.assertEqual(CN.home_name({}), "Newman")
        self.assertEqual(CN.home_name({"homeName": "The Garitos"}), "The Garitos")
        self.assertEqual(CN.clean_name("  Our   house \n"), "Our house")
        with self.assertRaises(ValueError):
            CN.clean_name("x" * 49)
        self.assertEqual(CN.instance_name("St. John's"), "St John's")

    def test_outside_falls_back_to_public_url(self):
        self.assertEqual(CN.outside_url({}, "https://home.example.com"), "https://home.example.com/")
        self.assertIsNone(CN.outside_url({}, ""))
        self.assertIsNone(CN.outside_url({"outsideUrl": ""}, "https://home.example.com"))  # cleared on purpose
        self.assertEqual(CN.outside_url({"outsideUrl": "https://a.example.com/"}, ""), "https://a.example.com/")

    def test_connect_link_has_every_part_encoded_and_nothing_secret(self):
        link = CN.connect_link("abc123", "Our home & co", "http://10.1.1.2:8093/", "https://home.example.com/")
        self.assertEqual(link, "newman://connect?v=1&id=abc123&name=Our%20home%20%26%20co"
                               "&local=http%3A%2F%2F10.1.1.2%3A8093%2F&outside=https%3A%2F%2Fhome.example.com%2F")
        q = parse_qs(urlsplit(link).query, keep_blank_values=True)
        self.assertEqual(q["name"], ["Our home & co"])
        self.assertEqual(set(q), {"v", "id", "name", "local", "outside"})
        self.assertTrue(CN.connect_link("abc", "N", "http://10.0.0.5:8093/", None).endswith("&outside="))

    def test_mdns_txt(self):
        self.assertEqual(CN.mdns_txt("abc", "Home", "https://h.example.com/"),
                         {"id": "abc", "name": "Home", "v": "1", "ext": "https://h.example.com/", "path": "/"})
        self.assertEqual(CN.mdns_txt("abc", "Home", None)["ext"], "")

    def test_lan_addresses_skip_docker_and_vpns(self):
        adapters = [adapter("lo", "127.0.0.1"), adapter("docker0", "172.17.0.1"), adapter("br-a86e", "172.18.0.1"),
                    adapter("tailscale1", "100.127.49.119"), adapter("br0", "10.1.1.2", ("fe80::1", 0, 2)),
                    adapter("eth1", "8.8.8.8")]
        self.assertEqual(CN.lan_ipv4s(adapters), ["10.1.1.2"])
        self.assertEqual(CN.lan_ipv4s(adapters, only="docker0"), ["172.17.0.1"])
        self.assertEqual(CN.local_urls(["10.1.1.2"], 8093), ["http://10.1.1.2:8093/"])


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        object.__setattr__(config, "public_url", "")
        # A built web page (what Vite writes): the hub tells pages which build it serves.
        self.web_dir_was = config.web_dir
        (data / "web").mkdir()
        (data / "web" / "index.html").write_text(
            '<!doctype html><head><link rel="icon" href="/favicon.ico" />'
            '<script type="module" crossorigin src="./assets/index-Cg0z-opQ.js"></script>\n'
            '<link rel="modulepreload" crossorigin href="./assets/vendor-AbC1.js">'
            '<link rel="stylesheet" crossorigin href="./assets/index-D4f2a.css"></head><body><div id="root"></div></body>')
        object.__setattr__(config, "web_dir", str(data / "web"))
        self.h = H(data)
        self.announced = 0
        self.h.reannounce = lambda: setattr(self, "announced", self.announced + 1)
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        object.__setattr__(config, "web_dir", self.web_dir_was)
        self.tmp.cleanup()

    async def test_hello_needs_no_sign_in_and_tells_nothing_secret(self):
        r = await self.client.get("/api/hello")
        self.assertEqual(r.status, 200)
        body = await r.json()
        self.assertEqual(set(body), {"app", "id", "name", "version", "outsideUrl", "localUrls", "webBuild"})
        self.assertEqual(r.headers.get("Cache-Control"), "no-store")
        self.assertEqual((body["app"], body["name"], body["outsideUrl"]), ("newman", "Newman", None))
        self.assertTrue(body["id"])
        self.assertEqual((await (await self.client.get("/api/hello")).json())["id"], body["id"])  # stable
        self.assertTrue(all(u.startswith("http://") and u.endswith(f":{config.port}/") for u in body["localUrls"]))

    async def test_outside_address_and_name_are_admin_settings(self):
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        r = await self.client.patch("/api/auth/settings", json={"outsideUrl": "http://home.example.com"})
        self.assertEqual(r.status, 400)
        r = await self.client.patch("/api/auth/settings", json={"outsideUrl": "home.example.com", "homeName": " The Garitos "})
        self.assertEqual(r.status, 200)
        saved = await r.json()
        self.assertEqual((saved["outsideUrl"], saved["homeName"]), ("https://home.example.com/", "The Garitos"))
        self.assertEqual(self.announced, 1)
        hello = await (await self.client.get("/api/hello")).json()
        self.assertEqual((hello["name"], hello["outsideUrl"]), ("The Garitos", "https://home.example.com/"))
        info = await (await self.client.get("/api/connect-info")).json()
        self.assertEqual(info["id"], hello["id"])
        self.assertIn("&outside=https%3A%2F%2Fhome.example.com%2F", info["link"])
        self.assertTrue(info["link"].startswith(f"newman://connect?v=1&id={hello['id']}&name=The%20Garitos&local="))
        # Family can't see or change them.
        await self.client.post("/api/users", json={"username": "kid", "name": "Kid", "role": "family", "password": "another pass"})
        c = Browser(self.client)
        await c.post("/api/auth/login", json={"username": "kid", "password": "another pass"})
        self.assertEqual((await c.get("/api/connect-info")).status, 403)
        self.assertEqual((await c.patch("/api/auth/settings", json={"outsideUrl": ""})).status, 403)
        await c.close()

    async def test_connect_info_needs_sign_in(self):
        self.assertEqual((await self.client.get("/api/connect-info")).status, 401)

    async def test_web_build_in_hello_and_on_the_live_socket(self):
        build = "index-Cg0z-opQ.js index-D4f2a.css vendor-AbC1.js"
        self.assertEqual((await (await self.client.get("/api/hello")).json())["webBuild"], build)
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        ws = await self.client.ws_connect("/api/ws")
        first = await ws.receive_json(timeout=5)
        await ws.close()
        self.assertEqual((first["type"], first["webBuild"]), ("hello", build))
        # The page itself is never kept stale by caches (only the hashed assets are).
        r = await self.client.get("/")
        self.assertEqual(r.headers.get("Cache-Control"), "no-cache")


class WebBuildTests(unittest.TestCase):
    def test_build_id_from_index_html(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            self.assertIsNone(web_build_id(d))  # no build
            (d / "index.html").write_text('<script type="module" src="/src/main.jsx"></script>')
            self.assertIsNone(web_build_id(d))  # the dev page: nothing hashed
            (d / "index.html").write_text("<link rel='stylesheet' href='assets/b-2.css'><script src='/wall/assets/a-1.js'></script>"
                                          '<link rel="icon" href="/icon-192.png"><script src="./assets/a-1.js"></script>')
            self.assertEqual(web_build_id(d), "a-1.js b-2.css")


if __name__ == "__main__":
    unittest.main()
