"""Backups: everything needed to bring the hub back - accounts, connections (with their keys and
sessions), automations, dashboards, activity - in one file.

A backup is a zip: manifest.json, a consistent copy of newman.db (SQLite's own backup, safe while
running), each connection's data folder, people's photos and wall pictures. Downloads can be locked with a passphrase (scrypt +
AES-GCM). Restoring checks the file, stages it, and the hub restarts into it; what was there before
is kept in data/.pre-restore-<time>/.

TOKEN_SECRET isn't in a backup - it lives in the environment (.env), so keep that too.
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

log = logging.getLogger(__name__)

FORMAT = 1
MAGIC = b"NEWMANBK1"  # a passphrase-locked backup starts with this
SUFFIX = ".newmanbackup"
EXTRA_FILES = (".install_id",)  # data-folder files that belong with the database
# data folders that do too (connections' data, people's photos, wall dashboards' Christmas pictures)
FOLDERS = ("integrations", "people", "wall-pictures")
KEEP_PREVIOUS = 2               # .pre-restore-* folders kept


class BackupError(Exception):
    pass


def _key(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode())


def lock(blob: bytes, passphrase: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(_key(passphrase, salt)).encrypt(nonce, blob, MAGIC)


def unlock(raw: bytes, passphrase: str | None) -> bytes:
    """A backup file -> its zip bytes (unlocking it if it's passphrase-locked)."""
    if not raw.startswith(MAGIC):
        return raw
    if not passphrase:
        raise BackupError("This backup is locked - enter its passphrase")
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    head = len(MAGIC)
    salt, nonce, body = raw[head:head + 16], raw[head + 16:head + 28], raw[head + 28:]
    try:
        return AESGCM(_key(passphrase, salt)).decrypt(nonce, body, MAGIC)
    except InvalidTag:
        raise BackupError("That passphrase doesn't open this backup")


def create(db: sqlite3.Connection, data_dir: Path, version: str) -> bytes:
    """A backup of the running hub, as zip bytes."""
    data_dir = Path(data_dir)
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "newman.db"
        dst = sqlite3.connect(copy)
        try:
            db.backup(dst)  # consistent even while the hub keeps writing
        finally:
            dst.close()
        files = ["newman.db"]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(copy, "newman.db")
            for name in EXTRA_FILES:
                if (data_dir / name).is_file():
                    z.write(data_dir / name, name)
                    files.append(name)
            for folder in FOLDERS:
                if (data_dir / folder).is_dir():
                    for f in sorted((data_dir / folder).rglob("*")):
                        if f.is_file():
                            arc = f.relative_to(data_dir).as_posix()
                            z.write(f, arc)
                            files.append(arc)
            z.writestr("manifest.json", json.dumps({"format": FORMAT, "app": "newman", "version": version,
                                                    "created": time.time(), "files": files}))
        return buf.getvalue()


def _safe(name: str) -> bool:
    p = PurePosixPath(name)
    return not p.is_absolute() and ".." not in p.parts and (name in ("manifest.json", "newman.db")
                                                            or name in EXTRA_FILES
                                                            or p.parts[0] in FOLDERS)


def inspect(blob: bytes) -> dict[str, Any]:
    """Check a backup's zip bytes; returns its manifest plus what's in it."""
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        raise BackupError("That isn't a backup file")
    with z:
        names = z.namelist()
        if "manifest.json" not in names or "newman.db" not in names:
            raise BackupError("That isn't a backup file")
        bad = [n for n in names if not _safe(n)]
        if bad:
            raise BackupError("This backup contains files it shouldn't - not restoring it")
        manifest = json.loads(z.read("manifest.json"))
        if manifest.get("format", 0) > FORMAT:
            raise BackupError("This backup was made by a newer version - update first")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "newman.db"
            path.write_bytes(z.read("newman.db"))
            con = sqlite3.connect(path)
            try:
                if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BackupError("The database in this backup is damaged")
                counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                          for t in ("users", "automations", "integrations", "dashboards")
                          if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()}
            except sqlite3.DatabaseError:
                raise BackupError("The database in this backup is damaged")
            finally:
                con.close()
        if "users" not in counts:
            raise BackupError("This backup has no accounts in it")
    return {**manifest, "counts": counts}


def stage(blob: bytes, data_dir: Path) -> dict[str, Any]:
    """Check a backup and lay it out to be swapped in at the next start."""
    info = inspect(blob)
    pending = Path(data_dir) / ".restore"
    shutil.rmtree(pending, ignore_errors=True)
    pending.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        z.extractall(pending)  # names were checked by inspect()
    (pending / "READY").write_text(json.dumps({"staged": time.time()}))
    return info


def apply_pending(data_dir: Path) -> bool:
    """At start-up, before the database opens: swap in a staged restore. True if one was applied."""
    data_dir = Path(data_dir)
    pending = data_dir / ".restore"
    if not (pending / "READY").is_file():
        return False
    keep = data_dir / f".pre-restore-{time.strftime('%Y%m%d-%H%M%S')}"
    keep.mkdir(parents=True)
    for name in ("newman.db", "newman.db-wal", "newman.db-shm", *FOLDERS, *EXTRA_FILES):
        if (data_dir / name).exists():
            shutil.move(str(data_dir / name), str(keep / name))
    for name in ("newman.db", *FOLDERS, *EXTRA_FILES):
        if (pending / name).exists():
            shutil.move(str(pending / name), str(data_dir / name))
    shutil.rmtree(pending, ignore_errors=True)
    for old in sorted(data_dir.glob(".pre-restore-*"))[:-KEEP_PREVIOUS]:
        shutil.rmtree(old, ignore_errors=True)
    log.warning("Restored from a backup; what was here before is in %s", keep.name)
    return True


class Saved:
    """Automatic backups kept on the server (data/backups), newest first."""

    def __init__(self, data_dir: Path) -> None:
        self.dir = Path(data_dir) / "backups"

    def list(self) -> list[dict[str, Any]]:
        if not self.dir.is_dir():
            return []
        out = [{"name": f.name, "size": f.stat().st_size, "created": f.stat().st_mtime}
               for f in self.dir.glob(f"*{SUFFIX}")]
        return sorted(out, key=lambda b: b["created"], reverse=True)

    def path(self, name: str) -> Path | None:
        p = self.dir / name
        return p if name.endswith(SUFFIX) and "/" not in name and "\\" not in name and p.is_file() else None

    def save(self, blob: bytes, keep: int) -> dict[str, Any]:
        self.dir.mkdir(parents=True, exist_ok=True)
        stem, n = f"newman-{time.strftime('%Y-%m-%d-%H%M%S')}", 1
        name = stem + SUFFIX
        while (self.dir / name).exists():  # two in the same second
            n += 1
            name = f"{stem}-{n}{SUFFIX}"
        tmp = self.dir / (name + ".part")
        tmp.write_bytes(blob)
        tmp.chmod(0o600)
        tmp.rename(self.dir / name)
        for old in self.list()[max(1, keep):]:
            (self.dir / old["name"]).unlink(missing_ok=True)
        return next(b for b in self.list() if b["name"] == name)
