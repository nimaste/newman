"""Backups: what goes in, passphrase locking, checks before restoring, the swap at start-up, the API."""
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

import aiohttp

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman import backup as B  # noqa: E402
from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.store import Store  # noqa: E402
from test_auth import H  # noqa: E402


class BackupFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.store = Store(self.data)
        self.store.add_person("Chris")
        self.store.set_setting("awayPolicy", "cert_or_2fa")
        (self.data / "integrations" / "roborock").mkdir(parents=True)
        (self.data / "integrations" / "roborock" / "session.json").write_text('{"token": "abc"}')
        (self.data / ".install_id").write_text("a1b2c3")
        (self.data / ".local_token").write_text("not in backups")

    def tearDown(self):
        self.tmp.cleanup()

    def test_contents_and_checks(self):
        blob = B.create(self.store._db, self.data, "test")
        names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
        self.assertIn("integrations/roborock/session.json", names)
        self.assertIn(".install_id", names)
        self.assertNotIn(".local_token", names)
        info = B.inspect(blob)
        self.assertEqual(info["app"], "newman")
        self.assertIn("users", info["counts"])
        with self.assertRaisesRegex(B.BackupError, "isn't a backup"):
            B.inspect(b"hello")
        evil = io.BytesIO()
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("manifest.json", "{}")
            z.writestr("newman.db", b"")
            z.writestr("../../etc/passwd", "x")
        with self.assertRaisesRegex(B.BackupError, "shouldn't"):
            B.inspect(evil.getvalue())

    def test_passphrase(self):
        blob = B.create(self.store._db, self.data, "test")
        locked = B.lock(blob, "correct horse")
        self.assertTrue(locked.startswith(B.MAGIC))
        self.assertNotIn(b"session.json", locked)
        self.assertEqual(B.unlock(locked, "correct horse"), blob)
        with self.assertRaisesRegex(B.BackupError, "enter its passphrase"):
            B.unlock(locked, "")
        with self.assertRaisesRegex(B.BackupError, "doesn't open"):
            B.unlock(locked, "wrong horse")
        self.assertEqual(B.unlock(blob, None), blob)  # an unlocked file needs nothing

    def test_restore_swaps_in_at_start_and_keeps_the_old(self):
        blob = B.create(self.store._db, self.data, "test")
        self.store.add_person("Someone added after the backup")
        (self.data / "integrations" / "roborock" / "session.json").write_text('{"token": "newer"}')
        self.store._db.close()
        B.stage(blob, self.data)
        self.assertTrue(B.apply_pending(self.data))
        self.assertFalse(B.apply_pending(self.data))  # once
        restored = Store(self.data)
        self.assertEqual([p["name"] for p in restored.people()], ["Chris"])
        self.assertEqual((self.data / "integrations" / "roborock" / "session.json").read_text(), '{"token": "abc"}')
        kept = list(self.data.glob(".pre-restore-*"))
        self.assertEqual(len(kept), 1)
        self.assertEqual((kept[0] / "integrations" / "roborock" / "session.json").read_text(), '{"token": "newer"}')
        self.assertEqual((self.data / ".local_token").read_text(), "not in backups")  # untouched

    def test_saved_keeps_the_newest(self):
        saved = B.Saved(self.data)
        for i in range(4):
            entry = saved.save(f"blob {i}".encode(), keep=2)
            os.utime(saved.dir / entry["name"], (1000 + i, 1000 + i))
        self.assertEqual(len(saved.list()), 2)
        self.assertIsNone(saved.path("../newman.db"))


class BackupApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.data = data
        self.h = H(data)
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "Chris", "name": "Chris", "password": "correct horse"})

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    def _form(self, blob, passphrase="", check=True):
        f = aiohttp.FormData()
        f.add_field("file", blob, filename="b.newmanbackup", content_type="application/octet-stream")
        f.add_field("passphrase", passphrase)
        f.add_field("check", "true" if check else "false")
        return f

    async def test_backup_download_check_restore(self):
        r = await self.client.get("/api/backups")
        self.assertEqual(await r.json(), {"backups": [], "auto": True, "keep": 7, "time": "03:30"})
        entry = await (await self.client.post("/api/backups")).json()
        self.assertTrue(entry["name"].endswith(".newmanbackup"))
        r = await self.client.post("/api/backups/download", json={"passphrase": "correct horse"})
        self.assertEqual(r.status, 200)
        self.assertIn("attachment", r.headers["Content-Disposition"])
        locked = await r.read()
        self.assertTrue(locked.startswith(B.MAGIC))
        r = await self.client.post("/api/backups/restore", data=self._form(locked))
        self.assertEqual((r.status, (await r.json())["error"]), (400, "This backup is locked - enter its passphrase"))
        r = await self.client.post("/api/backups/restore", data=self._form(locked, "correct horse"))
        body = await r.json()
        self.assertEqual(body["backup"]["counts"]["users"], 1)
        self.assertFalse((self.data / ".restore").exists())  # a check stages nothing
        r = await self.client.post("/api/backups/restore", json={"name": entry["name"]})
        self.assertEqual((await r.json())["restarting"], False)  # no restart hook in tests
        self.assertTrue((self.data / ".restore" / "READY").is_file())
        r = await self.client.patch("/api/backups/settings", json={"keep": 0})
        self.assertEqual(r.status, 400)
        r = await self.client.patch("/api/backups/settings", json={"auto": False, "keep": 14, "time": "02:15"})
        self.assertEqual(await r.json(), {"auto": False, "keep": 14, "time": "02:15"})

    async def test_family_cant(self):
        await self.client.post("/api/users", json={"username": "dante", "name": "Dante", "role": "family",
                                                   "password": "another horse"})
        s = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        try:
            await s.post(self.client.make_url("/api/auth/login"), json={"username": "dante", "password": "another horse"})
            for method, path in (("GET", "/api/backups"), ("POST", "/api/backups/download"), ("POST", "/api/backups")):
                r = await s.request(method, self.client.make_url(path), json={})
                self.assertEqual(r.status, 403, path)
        finally:
            await s.close()
