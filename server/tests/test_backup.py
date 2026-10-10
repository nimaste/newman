"""Backups: what goes in, passphrase locking, checks before restoring, the swap at start-up, the API."""
import io
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

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

    def _companions(self, tag):
        """voice/, matter/ and zigbee/ the way the hub and its companion containers leave them."""
        files = {
            "voice/hey_newman.onnx": f"model {tag}", "voice/hey_newman.json": "{}",
            "voice/teach/chris/positive/1.wav": f"wav {tag}",
            "voice/train/candidates/hey_newman-1.onnx": f"candidate {tag}",
            "voice/train/status.json": '{"state": "done"}',
            "voice/train/requests/20261010.json": "{}",            # out: would start a run
            "matter/server-1-fff1/snapshot.json.gz": f"fabric {tag}",
            "matter/server-1-fff1/wal/0001": f"wal {tag}",
            "matter/server-1-fff1/matter.lock": "", "matter/config/matter.pid": "42",   # out
            "zigbee/configuration.yaml": f"network_key: {tag}", "zigbee/database.db": f"devices {tag}",
            "zigbee/coordinator_backup.json": f"coordinator {tag}",
            "zigbee/log/2026-10-10/log.log": "noise",               # out
        }
        for name, text in files.items():
            (self.data / name).parent.mkdir(parents=True, exist_ok=True)
            (self.data / name).write_text(text)
        for top in ("matter", "zigbee", "whisper", "piper", "training", "clips", "snapshots", "logs", "map-tiles"):
            (self.data / top).mkdir(exist_ok=True)
        for name in ("whisper/model.bin", "piper/voice.onnx", "training/venv.txt", "clips/1.mp4",
                     "snapshots/1.jpg", "logs/newman.log", "map-tiles/1.pbf"):
            (self.data / name).write_text("not in backups")
        return files

    def test_companion_folders_in_and_out(self):
        self._companions("a")
        names = set(zipfile.ZipFile(io.BytesIO(B.create(self.store._db, self.data, "test"))).namelist())
        for name in ("voice/hey_newman.onnx", "voice/teach/chris/positive/1.wav",
                     "voice/train/candidates/hey_newman-1.onnx", "matter/server-1-fff1/snapshot.json.gz",
                     "matter/server-1-fff1/wal/0001", "zigbee/configuration.yaml", "zigbee/database.db",
                     "zigbee/coordinator_backup.json"):
            self.assertIn(name, names)
        for name in names:
            self.assertFalse(name.startswith(("voice/train/requests/", "zigbee/log/", "whisper/", "piper/",
                                              "training/", "clips/", "snapshots/", "logs/", "map-tiles/")), name)
            self.assertFalse(name.endswith((".lock", ".pid")), name)

    def test_companion_folders_round_trip(self):
        files = self._companions("backed-up")
        if os.geteuid() == 0:  # the Matter server's folder belongs to uid 1000
            for p in (self.data / "matter", *(self.data / "matter").rglob("*")):
                os.chown(p, 1000, 1000)
        blob = B.create(self.store._db, self.data, "test")
        # Afterwards everything is gone (a fresh install) or redone - and then the restore.
        for top in ("voice", "matter", "zigbee"):
            shutil.rmtree(self.data / top)
            (self.data / top).mkdir()
        (self.data / "voice" / "new.onnx").write_text("made after")
        (self.data / "zigbee" / "configuration.yaml").write_text("network_key: different")
        voice_inode = (self.data / "voice").stat().st_ino
        self.store._db.close()
        B.stage(blob, self.data)
        self.assertTrue(B.apply_pending(self.data))
        for name, text in files.items():
            if B.skipped(name):
                continue
            self.assertEqual((self.data / name).read_text(), text, name)
        self.assertFalse((self.data / "voice" / "new.onnx").exists())
        self.assertEqual((self.data / "voice").stat().st_ino, voice_inode)   # same folder: mounts keep working
        kept = next(self.data.glob(".pre-restore-*"))
        self.assertEqual((kept / "zigbee" / "configuration.yaml").read_text(), "network_key: different")
        self.assertEqual((kept / "voice" / "new.onnx").read_text(), "made after")
        if os.geteuid() == 0:
            for p in (self.data / "matter", *(self.data / "matter").rglob("*")):
                self.assertEqual((p.stat().st_uid, p.stat().st_gid), (1000, 1000), p)

    def test_older_backup_leaves_companion_folders_alone(self):
        blob = B.create(self.store._db, self.data, "test")   # no voice/, matter/ or zigbee/ yet
        self._companions("paired later")
        self.store._db.close()
        B.stage(blob, self.data)
        self.assertTrue(B.apply_pending(self.data))
        self.assertEqual((self.data / "matter" / "server-1-fff1" / "snapshot.json.gz").read_text(), "fabric paired later")
        self.assertEqual((self.data / "zigbee" / "database.db").read_text(), "devices paired later")
        self.assertEqual((self.data / "voice" / "hey_newman.onnx").read_text(), "model paired later")

    def test_folder_changing_while_read_is_read_again(self):
        self._companions("a")
        real = B._tree
        looks = []

        def changing(folder, data_dir):
            if folder.name == "zigbee":
                looks.append(1)
                if len(looks) == 2:   # Zigbee2MQTT writes between the first read's two looks
                    (self.data / "zigbee" / "database.db").write_text("devices changed")
            return real(folder, data_dir)

        with mock.patch.object(B, "_tree", changing), mock.patch.object(B.time, "sleep"):
            blob = B.create(self.store._db, self.data, "test")
        z = zipfile.ZipFile(io.BytesIO(blob))
        self.assertEqual(z.read("zigbee/database.db").decode(), "devices changed")
        self.assertEqual(len(looks), 4)   # read twice

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
