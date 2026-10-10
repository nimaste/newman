"""Restarting the speech-to-text container when the Whisper hint changes, so it hears the home's current names.

wyoming-faster-whisper reads its initial prompt (the hint file, voice/hint.py) only when it starts, and its
`transcribe` request has no prompt field. Its other way in - asking a Home Assistant install for names on
every utterance (--hass-token / --hass-api) - would mean Newman pretending to be Home Assistant's websocket
API (its login handshake and six registry commands) and squeezing the household, automations and artists into
Home Assistant's entity tiers. So Newman restarts the container instead, through the Docker connection, when
nobody is talking:

  * pending  - the hint file is newer than the container's last start (Docker's State.StartedAt), so it also
               holds across Newman restarts and after someone restarts Whisper by hand;
  * settled  - the file hasn't changed for SETTLE_S (a burst of renames restarts once);
  * idle     - no speech-to-text request running and none in the last IDLE_S;
  * quiet    - preferably none for QUIET_S either; after QUIET_WAIT_S pending, idle is enough;
  * spaced   - never within MIN_GAP_S of the container's last start (by Newman or anyone);
  * running  - a container that's stopped stays stopped.

Then it waits for Whisper to answer a Wyoming `describe` again and logs how long that took. A request that
arrives meanwhile waits for it (up to RETRY_WAIT_S) and is transcribed then; if Whisper still isn't back it
gets "One moment…" as its reply instead of an error (pipeline.SpeechRestarting).

    WhisperRestarter(hint_path, container, docker=lambda: conn, ready=async () -> bool)
      .guard(stt) -> an STT that counts requests and rides out a restart
      .check() -> what it decided ("current", "settling", "busy", …, "restarted")   (one look)
      .run()                                                                         (looks every CHECK_S)
      .info() -> {"container", "auto", "pending", "state", "lastRestart", …}       (GET /api/voice/hint)

Off with VOICE_STT_RESTART=0; the container is VOICE_STT_CONTAINER (default newman-whisper). Needs a Docker
connection whose socket proxy allows restarts (ALLOW_RESTARTS=1).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from .pipeline import SpeechRestarting
from .wyoming import WyomingError

log = logging.getLogger(__name__)

SETTLE_S = 300.0        # the hint unchanged this long before restarting
IDLE_S = 60.0           # no speech-to-text request for at least this long
QUIET_S = 600.0         # preferably this long
QUIET_WAIT_S = 7200.0   # pending this long: idle is enough
MIN_GAP_S = 1800.0      # never within this long of the container's last start
CHECK_S = 30.0          # how often to look
READY_TIMEOUT_S = 180.0  # Whisper loading its model after the restart
READY_POLL_S = 2.0
RETRY_WAIT_S = 20.0     # a request during the restart waits this long for Whisper


def docker_time(value: Any) -> float | None:
    """Docker's "2026-10-09T05:12:33.123456789Z" -> epoch seconds; None for the zero time / junk."""
    s = str(value or "").strip()
    if not s or s.startswith("0001-"):
        return None
    s = re.sub(r"\.(\d{1,6})\d*", r".\1", s.replace("Z", "+00:00"))  # Python takes at most microseconds
    s = re.sub(r"\.(\d{1,5})(?=\D|$)", lambda m: "." + m.group(1).ljust(6, "0"), s)  # 3.8 wants 6 digits
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


class WhisperRestarter:
    def __init__(self, hint_path: Path, container: str, docker: Callable[[], Any],
                 ready: Callable[[], Awaitable[bool]], enabled: bool = True,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep) -> None:
        """`docker()`: the Docker connection to use now, or None (it has container_info / restart_container);
        `ready()`: whether Whisper answers a Wyoming describe."""
        self.hint_path, self.container, self.docker, self.ready = hint_path, container, docker, ready
        self.enabled, self.clock, self.sleep = enabled, clock, sleep
        self.active = 0                    # speech-to-text requests running
        self.last_used = 0.0               # when the last one started or ended
        self.restarting = False
        self.started_at: float | None = None   # the container's last start, as Docker last said
        self.last_restart: float | None = None  # when Newman last restarted it
        self.last_result = ""              # "ready in 12 s" / an error
        self.state = "off" if not enabled else "starting"
        self._back: asyncio.Event | None = None
        self._said = ""                    # the last problem logged (each one once)

    # -- requests ---------------------------------------------------------------------------------------

    def guard(self, stt: Any) -> "GuardedSTT":
        return GuardedSTT(stt, self)

    async def wait_ready(self, timeout: float) -> bool:
        """Whether Whisper is back (not restarting) within `timeout` seconds."""
        if not self.restarting:
            return True
        if self._back is None:
            self._back = asyncio.Event()
        try:
            await asyncio.wait_for(self._back.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        return not self.restarting

    # -- deciding -----------------------------------------------------------------------------------------

    def _hint_time(self) -> float | None:
        try:
            return self.hint_path.stat().st_mtime
        except OSError:
            return None

    def _problem(self, state: str, msg: str) -> str:
        if msg != self._said:
            log.warning("Voice: can't restart %s for the new hint: %s", self.container, msg)
            self._said = msg
        self.state = state
        return state

    async def check(self) -> str:
        """One look: restarts the container when everything in the module docstring holds; returns why not
        (or "restarted" / "failed")."""
        if not self.enabled:
            self.state = "off"
            return "off"
        written = self._hint_time()
        if written is None:
            self.state = "no-hint"
            return "no-hint"
        if self.started_at is not None and written <= self.started_at:
            self.state = "current"
            return "current"
        docker = self.docker()
        if docker is None:
            return self._problem("no-docker", "no Docker connection (Connections -> Add -> Docker)")
        try:
            info = await docker.container_info(self.container)
        except Exception as e:  # noqa: BLE001 - Docker away: look again later
            return self._problem("docker-error", f"Docker: {e}")
        if info is None:
            return self._problem("missing", f"Docker has no container called {self.container}")
        st = info.get("State") or {}
        if not st.get("Running"):
            self.state = "stopped"
            return "stopped"
        self.started_at = docker_time(st.get("StartedAt")) or self.started_at
        if self.started_at is not None and written <= self.started_at:
            self.state = "current"
            return "current"
        now = self.clock()
        self.state = "pending"
        if now - written < SETTLE_S:
            return "settling"
        if self.active or now - self.last_used < IDLE_S:
            return "busy"
        if now - self.last_used < QUIET_S and now - written < QUIET_WAIT_S:
            return "waiting-quiet"
        last = max(self.started_at or 0.0, self.last_restart or 0.0)
        if now - last < MIN_GAP_S:
            return "too-soon"
        return await self.restart(docker)

    async def restart(self, docker: Any) -> str:
        t0 = self.clock()
        log.info("Voice: restarting %s so Whisper reads the new hint", self.container)
        self.restarting, self.state = True, "restarting"
        self._back = self._back or asyncio.Event()
        self._back.clear()
        self.last_restart, self.last_result = t0, ""
        ok = False
        try:
            await docker.restart_container(self.container)
            deadline = t0 + READY_TIMEOUT_S
            while self.clock() < deadline:
                try:
                    if await self.ready():
                        ok = True
                        break
                except (WyomingError, OSError, asyncio.TimeoutError):
                    pass
                await self.sleep(READY_POLL_S)
        except Exception as e:  # noqa: BLE001 - the proxy refusing, Docker away
            self.last_result = f"restart failed: {e}"
            log.warning("Voice: couldn't restart %s: %s", self.container, e)
        finally:
            self.restarting = False
            self._back.set()
        if ok:
            self.last_result = f"ready in {round(self.clock() - t0)} s"
            self.started_at = max(self.started_at or 0.0, t0)
            try:
                info = await docker.container_info(self.container)
                self.started_at = docker_time(((info or {}).get("State") or {}).get("StartedAt")) or self.started_at
            except Exception:  # noqa: BLE001 - t0 will do
                pass
            log.info("Voice: %s restarted with the new hint, %s", self.container, self.last_result)
            self.state = "current"
            return "restarted"
        if not self.last_result.startswith("restart failed"):
            self.last_result = f"not answering {round(READY_TIMEOUT_S)} s after the restart"
            log.warning("Voice: %s is %s", self.container, self.last_result)
        self.state = "failed"
        return "failed"

    async def run(self) -> None:
        if not self.enabled:
            return
        while True:
            await self.sleep(CHECK_S)
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - never let this stop looking
                log.warning("Voice: Whisper restart check: %s", e)

    def info(self) -> dict[str, Any]:
        written = self._hint_time()
        return {"container": self.container, "auto": self.enabled, "state": self.state,
                "pending": bool(written and (self.started_at is None or written > self.started_at)),
                "startedAt": self.started_at, "lastRestart": self.last_restart, "lastResult": self.last_result or None}


class GuardedSTT:
    """The speech-to-text service, with each request counted (so a restart waits for quiet) and a restart
    ridden out: wait for Whisper, then transcribe; still not back -> SpeechRestarting."""

    def __init__(self, stt: Any, restarter: WhisperRestarter) -> None:
        self.stt, self.restarter = stt, restarter

    def __getattr__(self, name: str) -> Any:
        return getattr(self.stt, name)

    async def transcribe(self, pcm: bytes, *a: Any, **kw: Any) -> str:
        r = self.restarter
        r.active += 1
        r.last_used = r.clock()
        try:
            if r.restarting and not await r.wait_ready(RETRY_WAIT_S):
                raise SpeechRestarting()
            try:
                return await self.stt.transcribe(pcm, *a, **kw)
            except WyomingError:
                if not r.restarting:
                    raise
                if not await r.wait_ready(RETRY_WAIT_S):
                    raise SpeechRestarting() from None
                return await self.stt.transcribe(pcm, *a, **kw)
        finally:
            r.active -= 1
            r.last_used = r.clock()
