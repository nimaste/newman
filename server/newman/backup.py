"""Backups: everything needed to bring the hub back - accounts, connections (with their keys and
sessions), automations, dashboards, activity, the trained wake word and its recordings, the Matter
fabric and the Zigbee network with their paired devices - in one file.

A backup is a zip: manifest.json, a consistent copy of newman.db (SQLite's own backup, safe while
running), each connection's data folder, people's photos, wall pictures, voice/ (wake-word models,
Teach recordings, training results) and the companion containers' matter/ and zigbee/ folders (copied
read-only; a folder that changes while it's being read is read again, so the copy is never half old,
half new). Not in it: things that come back by themselves or aren't settings - snapshots, clips, logs,
map tiles, the speech models (whisper/, piper/), the training setup (training/), push keys (keys/),
earlier backups. Downloads can be locked with a passphrase (scrypt + AES-GCM). Restoring checks the
file, stages it, and the hub restarts into it; what was there before is kept in data/.pre-restore-<time>/.

Restored folders get back the owner recorded in the backup (else the one there now): the Matter
server runs as uid 1000. matter/ and zigbee/ are swapped as whole folders, so their running
containers keep writing to the set-aside copy and load the restored one when restarted (README → Backups);
voice/ is swapped file by file, so the Whisper and trainer containers that mount it see it straight away.
A backup without one of these three (made before they were included) leaves it as it is.

The token secret (.token_secret, which signs notification links) is in the backup too, so a restore -
even onto a fresh install - keeps every phone and tablet working. Restoring writes it back (0600)
unless TOKEN_SECRET is set in the environment, which always wins. A backup without one (made before
it was included) leaves the current secret alone.
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
TOKEN_SECRET_FILE = ".token_secret"
EXTRA_FILES = (".install_id", TOKEN_SECRET_FILE)  # data-folder files that belong with the database
# data folders that do too: connections' data, people's photos, wall dashboards' Christmas pictures,
# the wake words (trained models, Teach recordings), and the Matter and Zigbee2MQTT containers' data
FOLDERS = ("integrations", "people", "wall-pictures", "voice", "matter", "zigbee")
# Shared with companion containers: a backup without one leaves the folder here as it is.
SHARED = ("voice", "matter", "zigbee")
IN_PLACE = ("voice",)           # swapped file by file (the folder stays: other containers mount it)
KEEP_PREVIOUS = 2               # .pre-restore-* folders kept
READ_TRIES = 5                  # reading a folder that keeps changing


def skipped(arc: str) -> bool:
    """Files inside FOLDERS that stay out of backups."""
    p = PurePosixPath(arc)
    top = p.parts[0]
    if top == "voice":    # a waiting training request would start a run after a restore
        return p.parts[1:3] == ("train", "requests")
    if top == "zigbee":   # Zigbee2MQTT's logs
        return len(p.parts) > 1 and p.parts[1] == "log"
    if top == "matter":   # the running server's lock and process id
        return p.suffix in (".lock", ".pid")
    return False


def _tree(folder: Path, data_dir: Path) -> dict[str, tuple[int, int]]:
    """A folder's files (as backup names) -> (size, modified), skipped ones left out."""
    out = {}
    for f in folder.rglob("*"):
        try:
            if f.is_file() and not skipped(arc := f.relative_to(data_dir).as_posix()):
                st = f.stat()
                out[arc] = (st.st_size, st.st_mtime_ns)
        except FileNotFoundError:
            pass
    return out


def _read_folder(folder: Path, data_dir: Path) -> list[tuple[str, bytes]]:
    """Every file in a folder, read only; read again if anything changed meanwhile (the Matter and
    Zigbee containers write theirs while Newman runs)."""
    for attempt in range(READ_TRIES):
        before = _tree(folder, data_dir)
        files: list[tuple[str, bytes]] = []
        try:
            for arc in sorted(before):
                files.append((arc, (data_dir / arc).read_bytes()))
        except FileNotFoundError:
            pass  # gone meanwhile: the trees below differ
        if len(files) == len(before) and _tree(folder, data_dir) == before:
            return files
        time.sleep(0.2)
    log.warning("%s kept changing while it was backed up; the copy may be mid-change", folder.name)
    return files


def _owner(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
        return st.st_uid, st.st_gid
    except OSError:
        return None


def _chown_tree(path: Path, owner: tuple[int, int] | None) -> None:
    """Gives a restored folder back its owner (only possible as root, which the image runs as)."""
    if not owner or not hasattr(os, "chown") or os.geteuid() != 0:
        return
    for p in (path, *path.rglob("*")):
        try:
            os.chown(p, *owner, follow_symlinks=False)
        except OSError as e:
            log.warning("Couldn't give %s back its owner: %s", p, e)


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
            owners = {}
            for folder in FOLDERS:
                if (data_dir / folder).is_dir():
                    owners[folder] = _owner(data_dir / folder)
                    for arc, data in _read_folder(data_dir / folder, data_dir):
                        z.writestr(arc, data)
                        files.append(arc)
            z.writestr("manifest.json", json.dumps({"format": FORMAT, "app": "newman", "version": version,
                                                    "created": time.time(), "files": files,
                                                    "owners": owners}))
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
    if (pending / TOKEN_SECRET_FILE).is_file():
        (pending / TOKEN_SECRET_FILE).chmod(0o600)
    (pending / "READY").write_text(json.dumps({"staged": time.time()}))
    return info


def apply_pending(data_dir: Path, env_secret: str | None = None) -> bool:
    """At start-up, before the database opens: swap in a staged restore. True if one was applied.
    env_secret: TOKEN_SECRET (default: from the environment) - when set, the backup's .token_secret
    isn't written back, since the environment's wins anyway."""
    data_dir = Path(data_dir)
    pending = data_dir / ".restore"
    if not (pending / "READY").is_file():
        return False
    if env_secret is None:
        from .config import config
        env_secret = config.token_secret
    if env_secret:
        (pending / TOKEN_SECRET_FILE).unlink(missing_ok=True)
    # Files the backup doesn't have (an older backup's missing .token_secret) stay as they are.
    extras = [n for n in EXTRA_FILES if (pending / n).is_file()]
    # Folders shared with companion containers stay as they are when the backup doesn't have them.
    folders = [f for f in FOLDERS if f not in SHARED or (pending / f).is_dir()]
    try:
        recorded = json.loads((pending / "manifest.json").read_text()).get("owners") or {}
    except (OSError, ValueError):
        recorded = {}
    owners = {f: (tuple(recorded[f]) if recorded.get(f) else None) or _owner(data_dir / f) for f in folders}
    keep = data_dir / f".pre-restore-{time.strftime('%Y%m%d-%H%M%S')}"
    keep.mkdir(parents=True)
    for name in ("newman.db", "newman.db-wal", "newman.db-shm", *folders, *extras):
        if name in IN_PLACE:
            if (data_dir / name).is_dir():
                (keep / name).mkdir()
                for f in list((data_dir / name).iterdir()):
                    shutil.move(str(f), str(keep / name / f.name))
        elif (data_dir / name).exists():
            shutil.move(str(data_dir / name), str(keep / name))
    for name in ("newman.db", *folders, *extras):
        if not (pending / name).exists():
            continue
        if name in IN_PLACE and (data_dir / name).is_dir():
            for f in list((pending / name).iterdir()):
                shutil.move(str(f), str(data_dir / name / f.name))
        else:
            shutil.move(str(pending / name), str(data_dir / name))
        if name in owners:
            _chown_tree(data_dir / name, owners[name])
    if TOKEN_SECRET_FILE in extras:
        (data_dir / TOKEN_SECRET_FILE).chmod(0o600)
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


# -- GitHub sync --------------------------------------------------------------
#
# A fixed remote filename, overwritten every sync, so git's own commit
# history is the version history instead of an ever-growing pile of
# once-off timestamped files (the mistake another fleet app made and had to
# be fixed after the fact -- every other app here already does it this way).
# Always locked with the configured passphrase via the same lock() above
# used for passphrase-protected downloads: a Newman backup has account
# credentials, connection keys, session tokens and the token secret, so it
# must never reach a repo unencrypted.
#
# httpx, not requests -- this whole server is async (aiohttp), and requests
# has no async client; a sync HTTP call here would block the event loop for
# every other request while it ran.

GITHUB_REMOTE_NAME = f"newman-backup{SUFFIX}"


def _github_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


async def _github_file_sha(client: Any, repo: str, token: str, branch: str, path: str) -> str | None:
    res = await client.get(
        f"https://api.github.com/repos/{repo}/contents/{path}",
        params={"ref": branch}, headers=_github_headers(token), timeout=15,
    )
    if res.status_code == 404:
        return None
    if res.status_code >= 400:
        raise BackupError(f"GitHub returned {res.status_code}: {res.text}")
    return res.json().get("sha")


async def github_test(token: str, repo: str) -> None:
    import httpx
    async with httpx.AsyncClient() as client:
        res = await client.get(f"https://api.github.com/repos/{repo}", headers=_github_headers(token), timeout=15)
        if res.status_code >= 400:
            raise BackupError(f"GitHub connection failed: {res.status_code} {res.text}")


async def github_sync(blob: bytes, passphrase: str, token: str, repo: str, branch: str, subdir: str = "") -> None:
    """Locks blob with passphrase and pushes it to repo at a fixed path,
    overwriting whatever was there before."""
    import base64
    import httpx
    subdir = subdir.strip().strip("/")
    remote_path = f"{subdir + '/' if subdir else ''}backups/{GITHUB_REMOTE_NAME}"
    locked = lock(blob, passphrase)
    async with httpx.AsyncClient() as client:
        sha = await _github_file_sha(client, repo, token, branch, remote_path)
        payload = {
            "message": f"backup @ {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
            "content": base64.b64encode(locked).decode("ascii"),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        res = await client.put(
            f"https://api.github.com/repos/{repo}/contents/{remote_path}",
            json=payload, headers=_github_headers(token), timeout=30,
        )
        if res.status_code >= 400:
            raise BackupError(f"GitHub returned {res.status_code}: {res.text}")
