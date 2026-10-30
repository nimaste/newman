"""Wires the pieces together and runs them."""
from __future__ import annotations

import asyncio
import logging
import secrets
import signal
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiohttp import web
from zeroconf import IPVersion
from zeroconf.asyncio import AsyncZeroconf

from . import backup, connect, tokens
from .api import build_app
from .automations.engine import Engine
from .config import config
from .discovery import Discovery
from .integrations.base import Context
from .integrations.manager import IntegrationManager
from .push.notifier import Notifier
from .state import Hub
from .store import Store

log = logging.getLogger("newman")

TABLET_SIGN_INS_EVERY_S = 3600  # how often expired wall-tablet sign-ins are looked for


class Newman:
    def __init__(self) -> None:
        self.snapshot_dir = config.data_dir / "snapshots"
        self.clip_dir = config.data_dir / "clips"
        # A restore uploaded before the last restart is swapped in before the database opens.
        restored = backup.apply_pending(config.data_dir)
        tokens.init()  # after the restore: it may have brought back .token_secret
        self.store = Store(config.data_dir)
        if restored:
            self.store.log_event("system", "Restored from a backup", "Everything was replaced by the backup's contents.")
        self.backups = backup.Saved(config.data_dir)
        self._stop: asyncio.Event | None = None
        self.hub = Hub()
        self.hub.set_name_overrides(self.store.entity_names())
        self.notifier = Notifier(self.store)
        install = config.data_dir / ".install_id"
        if not install.exists():
            install.write_text(secrets.token_hex(3))
        self.integrations = IntegrationManager(self.store, Context(self.hub, config.data_dir, self.snapshot_dir,
                                                                   hub_log=self.store.log_event,
                                                                   install_id=install.read_text().strip()))
        self.engine = Engine(self.hub, self.store, self.notifier, self.integrations)
        self.discovery = Discovery()
        self.announcer = connect.Announcer(config.port, enabled=config.mdns, interface=config.mdns_interface)

    async def run(self) -> None:
        runner = web.AppRunner(build_app(self), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", config.port).start()
        log.info("Newman %s listening on :%s", config.app_version, config.port)

        self.integrations.start_all()
        # Before the connections bring back their saved devices: a wall tablet's battery and music player
        # whose account is no longer signed in on a wall tablet go now (api.forget_signed_out_tablets).
        self._check_tablet_sign_ins()
        scheduler = asyncio.create_task(self.engine.run_scheduler(), name="scheduler")
        discovery = asyncio.create_task(self._discover(), name="discovery")
        backups = asyncio.create_task(self._auto_backups(), name="backups")
        tablets = asyncio.create_task(self._tablet_sign_ins(), name="tablet-sign-ins")
        # Button automations that only switched and dimmed a remote's lights become its Controls (once).
        controls = asyncio.create_task(self.engine.controls.migrate_soon(), name="controls-migration")

        stop = self._stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()

        log.info("Shutting down")
        scheduler.cancel()
        discovery.cancel()
        backups.cancel()
        tablets.cancel()
        controls.cancel()
        await self.integrations.stop_all()
        await self.notifier.close()
        await runner.cleanup()

    def request_restart(self) -> None:
        """Stop cleanly; Docker's restart policy starts us again (used after staging a restore)."""
        if self._stop:
            self._stop.set()

    def backup_now(self) -> bytes:
        return backup.create(self.store._db, config.data_dir, config.app_version)

    async def _auto_backups(self) -> None:
        """A backup every night (settings: backupAuto, backupTime "HH:MM", backupKeep)."""
        while True:
            s = self.store.settings()
            hh, mm = (str(s.get("backupTime") or "03:30").split(":") + ["0"])[:2]
            now = datetime.now()
            at = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            if at <= now:
                at += timedelta(days=1)
            await asyncio.sleep(max(1.0, (at - now).total_seconds()))
            s = self.store.settings()
            if s.get("backupAuto", True):
                try:
                    saved = self.backups.save(self.backup_now(), int(s.get("backupKeep") or 7))
                    log.info("Nightly backup %s (%d KB)", saved["name"], saved["size"] // 1024)
                    repo, token, key = s.get("backupGithubRepo"), s.get("backupGithubToken"), s.get("backupEncryptionKey")
                    if repo and token and key:
                        try:
                            path = self.backups.path(saved["name"])
                            await backup.github_sync(path.read_bytes(), key, token, repo,
                                                      s.get("backupGithubBranch") or "main",
                                                      s.get("backupGithubSubdir") or "")
                            log.info("Nightly backup synced to GitHub")
                        except Exception:
                            log.exception("GitHub backup sync failed")
                except Exception:
                    log.exception("Nightly backup failed")
                    self.store.log_event("system", "Nightly backup failed", "See the hub's log for why.")
            await asyncio.sleep(61)

    async def _tablet_sign_ins(self) -> None:
        """Every hour: wall tablets' devices whose account's last wall-tablet sign-in has expired go."""
        while True:
            await asyncio.sleep(TABLET_SIGN_INS_EVERY_S)
            self._check_tablet_sign_ins()

    def _check_tablet_sign_ins(self) -> None:
        try:
            self.forget_signed_out_tablets()
        except Exception:  # never stop the hub (or the hourly check) over it
            log.exception("Checking wall tablets' sign-ins failed")

    def hub_identity(self) -> tuple[str, str, str | None]:
        """(id, home name, outside address) - what /api/hello, mDNS and the connect link say."""
        s = self.store.settings()
        return connect.hub_id(self.store), connect.home_name(s), connect.outside_url(s, config.public_url)

    def reannounce(self) -> None:
        """The home name or outside address changed: announce the new ones."""
        try:
            asyncio.get_running_loop().create_task(self.announcer.update(*self.hub_identity()))
        except RuntimeError:
            pass  # no loop (tests)

    async def _discover(self) -> None:
        """One zeroconf instance: listening for devices (discovery.py) and announcing this hub."""
        azc = None
        try:
            azc = AsyncZeroconf(ip_version=IPVersion.V4Only)
            await self.announcer.start(azc, *self.hub_identity())
            await self.discovery.run(azc)
            await asyncio.Event().wait()  # (no device types to listen for: keep announcing)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Network discovery stopped - devices can still be added by address")
        finally:
            await self.announcer.stop()
            if azc is not None:
                await azc.async_close()


LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
LOG_FILE_BYTES = 5 * 1024 * 1024
LOG_FILE_COUNT = 5


def setup_logging(data_dir: Path | None = None) -> Path | None:
    """The log goes to stdout (docker logs) and to <data>/logs/newman.log (5 MB × 5 old files), so
    it outlives the container being replaced on a deploy. Returns the file (None if it can't be written)."""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    root = logging.getLogger()
    path = (data_dir or config.data_dir) / "logs" / "newman.log"
    if any(isinstance(hd, RotatingFileHandler) and Path(hd.baseFilename) == path.absolute() for hd in root.handlers):
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(path, maxBytes=LOG_FILE_BYTES, backupCount=LOG_FILE_COUNT, encoding="utf-8")
    except OSError as e:
        log.warning("No log file (%s): stdout only", e)
        return None
    fh.setFormatter(logging.Formatter(LOG_FORMAT))
    fh.setLevel(logging.INFO)
    root.addHandler(fh)
    return path


def main() -> None:
    setup_logging()
    # reolink_aio is chatty at INFO (every keep-alive); keep its warnings only.
    logging.getLogger("reolink_aio").setLevel(logging.WARNING)
    asyncio.run(Newman().run())
