"""The token secret: TOKEN_SECRET wins; unset, the data folder's .token_secret (made once, 0600, the
same across restarts); and it travels with backups, so a restore onto a fresh install keeps links working."""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman import backup as B  # noqa: E402
from newman import tokens  # noqa: E402
from newman.config import config  # noqa: E402
from newman.store import Store  # noqa: E402


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class LoadSecret(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.file = self.data / ".token_secret"

    def tearDown(self):
        self.tmp.cleanup()

    def test_unset_reads_the_file(self):
        self.file.write_text("abc123fromfile\n")  # as written by `openssl rand -hex 32 > .token_secret`
        self.assertEqual(tokens.load_secret(self.data, ""), "abc123fromfile")

    def test_missing_file_is_made_once_private_and_stable(self):
        with self.assertLogs("newman.tokens", "INFO") as logs:
            first = tokens.load_secret(self.data, "")
        self.assertEqual(len(logs.records), 1)
        self.assertIn("Made a token secret", logs.output[0])
        self.assertEqual(len(first), 64)
        self.assertEqual(self.file.read_text().strip(), first)
        self.assertEqual(mode(self.file), 0o600)
        self.assertFalse((self.data / ".token_secret.part").exists())
        # A restart reads the same one, and says nothing.
        with self.assertNoLogs("newman.tokens", "INFO"):
            self.assertEqual(tokens.load_secret(self.data, ""), first)

    def test_empty_file_is_replaced(self):
        self.file.write_text("\n")
        secret = tokens.load_secret(self.data, "")
        self.assertEqual(len(secret), 64)
        self.assertEqual(self.file.read_text().strip(), secret)

    def test_missing_data_folder_is_made(self):
        secret = tokens.load_secret(self.data / "new", "")
        self.assertEqual((self.data / "new" / ".token_secret").read_text().strip(), secret)

    def test_env_overrides_and_leaves_the_file_alone(self):
        self.file.write_text("fromfile")
        self.assertEqual(tokens.load_secret(self.data, "fromenv"), "fromenv")
        self.assertEqual(self.file.read_text(), "fromfile")
        self.assertEqual(tokens.load_secret(self.data / "empty", "fromenv"), "fromenv")
        self.assertFalse((self.data / "empty").exists())

    def test_signing_uses_the_file_when_env_unset(self):
        self.file.write_text("one")
        saved = (config.data_dir, config.token_secret)
        try:
            object.__setattr__(config, "data_dir", self.data)
            object.__setattr__(config, "token_secret", "")
            tokens._reset()
            exp, tok = tokens.sign("action", "r1", "phone1", 60)
            self.assertTrue(tokens.verify("action", "r1", "phone1", exp, tok))
            tokens._reset()
            self.file.write_text("two")  # another secret: earlier links stop working
            self.assertFalse(tokens.verify("action", "r1", "phone1", exp, tok))
        finally:
            object.__setattr__(config, "data_dir", saved[0])
            object.__setattr__(config, "token_secret", saved[1])
            tokens._reset()


class SecretInBackups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old, self.new = root / "old", root / "new"
        self.old.mkdir()
        self.store = Store(self.old)
        self.store.add_person("Chris")
        self.secret = tokens.load_secret(self.old, "")
        self.blob = B.create(self.store._db, self.old, "test")

    def tearDown(self):
        self.store._db.close()
        self.tmp.cleanup()

    def test_backup_holds_the_secret(self):
        self.assertIn(".token_secret", B.inspect(self.blob)["files"])

    def test_restore_onto_a_fresh_install_brings_it_back(self):
        self.new.mkdir()
        fresh = tokens.load_secret(self.new, "")  # a fresh install makes its own on first start
        self.assertNotEqual(fresh, self.secret)
        B.stage(self.blob, self.new)
        self.assertTrue(B.apply_pending(self.new, env_secret=""))
        self.assertEqual(tokens.load_secret(self.new, ""), self.secret)
        self.assertEqual(mode(self.new / ".token_secret"), 0o600)
        kept = next(self.new.glob(".pre-restore-*"))
        self.assertEqual((kept / ".token_secret").read_text().strip(), fresh)

    def test_locked_download_round_trip(self):
        locked = B.lock(self.blob, "correct horse")
        self.assertNotIn(self.secret.encode(), locked)
        self.new.mkdir()
        B.stage(B.unlock(locked, "correct horse"), self.new)
        B.apply_pending(self.new, env_secret="")
        self.assertEqual(tokens.load_secret(self.new, ""), self.secret)

    def test_env_secret_wins_over_the_backups(self):
        self.new.mkdir()
        (self.new / ".token_secret").write_text("mine")
        B.stage(self.blob, self.new)
        B.apply_pending(self.new, env_secret="pinned")
        self.assertEqual((self.new / ".token_secret").read_text(), "mine")  # not written back
        self.assertEqual(tokens.load_secret(self.new, "pinned"), "pinned")

    def test_older_backup_without_one_keeps_the_current_secret(self):
        (self.old / ".token_secret").unlink()
        blob = B.create(self.store._db, self.old, "test")
        self.assertNotIn(".token_secret", B.inspect(blob)["files"])
        self.new.mkdir()
        current = tokens.load_secret(self.new, "")
        B.stage(blob, self.new)
        B.apply_pending(self.new, env_secret="")
        self.assertEqual(tokens.load_secret(self.new, ""), current)


if __name__ == "__main__":
    unittest.main()
