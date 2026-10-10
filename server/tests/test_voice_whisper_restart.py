"""Restarting Whisper for a new hint (voice/whisper_restart.py), offline: a fake Docker connection, a fake
clock, a fake Wyoming service; and the Docker connection's by-name inspect / restart."""
import asyncio
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import httpx

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.integrations.base import CommandError, Context  # noqa: E402
from newman.integrations.docker import DockerIntegration  # noqa: E402
from newman.state import Hub  # noqa: E402
from newman.voice import whisper_restart as WR  # noqa: E402
from newman.voice.pipeline import Pipeline, Sources, SpeechRestarting, VoiceViewer  # noqa: E402
from newman.voice.timers import Timers  # noqa: E402
from newman.voice.wyoming import WyomingError  # noqa: E402

T = 1_800_000_000.0  # when the hint was written


def iso(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "123Z"


class FakeConn:
    """What the restarter uses of the Docker connection."""

    def __init__(self, started, running=True):
        self.started, self.running = started, running
        self.restarts, self.inspects = [], 0
        self.fail = None
        self.missing = False

    async def container_info(self, name):
        self.inspects += 1
        if self.missing:
            return None
        return {"Name": "/" + name, "State": {"Running": self.running, "StartedAt": iso(self.started)}}

    async def restart_container(self, name):
        if self.fail:
            raise self.fail
        self.restarts.append(name)
        self.started = self.now()


class RestarterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hint = Path(self.tmp.name) / "whisper_prompt.txt"
        self.hint.write_text("Newman. Chris, Massimo.\n")
        os.utime(self.hint, (T, T))
        self.now = T + 10
        self.conn = FakeConn(started=T - 7200)
        self.conn.now = lambda: self.now
        self.readies = 0
        self.up_after = 3  # describes before Whisper answers

        async def ready():
            self.readies += 1
            if self.readies < self.up_after:
                raise WyomingError("can't reach 127.0.0.1:10300")
            return True

        async def sleep(s):
            self.now += s
            await asyncio.sleep(0)
        self.r = WR.WhisperRestarter(self.hint, "newman-whisper", lambda: self.conn, ready,
                                     clock=lambda: self.now, sleep=sleep)

    def tearDown(self):
        self.tmp.cleanup()

    def test_docker_times(self):
        from datetime import datetime, timezone
        at = datetime(2027, 1, 15, 8, tzinfo=timezone.utc).timestamp()
        self.assertAlmostEqual(WR.docker_time("2027-01-15T08:00:00.123456789Z"), at + 0.123456, places=5)
        self.assertEqual(WR.docker_time("2027-01-15T08:00:00Z"), at)
        self.assertEqual(WR.docker_time("2027-01-15T03:00:00.5-05:00"), at + 0.5)
        self.assertIsNone(WR.docker_time("0001-01-01T00:00:00Z"))
        self.assertIsNone(WR.docker_time("soon"))

    async def test_waits_for_the_hint_to_settle_and_for_quiet_then_restarts_once(self):
        self.assertEqual(await self.r.check(), "settling")       # written 10 s ago
        self.now = T + WR.SETTLE_S + 1
        self.r.active, self.r.last_used = 1, self.now - 500       # someone is talking
        self.assertEqual(await self.r.check(), "busy")
        self.r.active, self.r.last_used = 0, self.now - 30        # just finished
        self.assertEqual(await self.r.check(), "busy")
        self.r.last_used = self.now - 120                         # idle, but not quiet yet
        self.assertEqual(await self.r.check(), "waiting-quiet")
        self.assertTrue(self.r.info()["pending"])
        self.assertEqual(self.conn.restarts, [])
        self.r.last_used = self.now - WR.QUIET_S - 1
        self.assertEqual(await self.r.check(), "restarted")
        self.assertEqual(self.conn.restarts, ["newman-whisper"])
        self.assertEqual(self.readies, 3)                         # waited for Whisper to answer
        info = self.r.info()
        self.assertFalse(info["pending"])
        self.assertEqual((info["state"], info["auto"]), ("current", True))
        self.assertRegex(info["lastResult"], r"^ready in \d+ s$")
        inspects = self.conn.inspects
        self.assertEqual(await self.r.check(), "current")         # nothing new: Docker isn't even asked
        self.assertEqual(self.conn.inspects, inspects)

    async def test_long_pending_settles_for_idle_and_never_twice_in_half_an_hour(self):
        self.now = T + WR.QUIET_WAIT_S + 1
        self.r.last_used = self.now - WR.IDLE_S - 1               # busy household: idle is enough by now
        self.assertEqual(await self.r.check(), "restarted")
        os.utime(self.hint, (self.now + 5, self.now + 5))         # renamed again right after
        self.now += WR.SETTLE_S + 10
        self.r.last_used = 0
        self.assertEqual(await self.r.check(), "too-soon")
        self.now = self.r.last_restart + WR.MIN_GAP_S + 1
        self.assertEqual(await self.r.check(), "restarted")
        self.assertEqual(len(self.conn.restarts), 2)

    async def test_a_recent_start_by_anyone_counts(self):
        self.conn.started = T + 100                              # restarted by hand after the hint: current
        self.now = T + 4000
        self.assertEqual(await self.r.check(), "current")
        os.utime(self.hint, (T + 200, T + 200))                   # changed again, 100 s after that start
        self.now = T + 1000
        self.assertEqual(await self.r.check(), "too-soon")

    async def test_leaves_alone_what_it_should(self):
        self.now = T + 4000
        self.conn.running = False
        self.assertEqual(await self.r.check(), "stopped")         # stopped on purpose: stays stopped
        self.conn.running, self.conn.missing = True, True
        self.assertEqual(await self.r.check(), "missing")
        self.r.docker = lambda: None
        self.assertEqual(await self.r.check(), "no-docker")
        self.r.enabled = False
        self.assertEqual(await self.r.check(), "off")
        self.hint.unlink()
        self.r.enabled = True
        self.assertEqual(await self.r.check(), "no-hint")
        self.assertEqual(self.conn.restarts, [])

    async def test_a_refused_or_silent_restart_is_logged_not_retried_at_once(self):
        self.now = T + 4000
        self.conn.fail = CommandError("Newman's Docker connection is read-only")
        with self.assertLogs("newman.voice.whisper_restart", "WARNING"):
            self.assertEqual(await self.r.check(), "failed")
        self.assertIn("read-only", self.r.info()["lastResult"])
        self.assertFalse(self.r.restarting)
        self.assertEqual(await self.r.check(), "too-soon")
        self.conn.fail, self.up_after = None, 10**9               # restarts but never answers
        self.now += WR.MIN_GAP_S + 1
        with self.assertLogs("newman.voice.whisper_restart", "WARNING"):
            self.assertEqual(await self.r.check(), "failed")
        self.assertIn("not answering", self.r.info()["lastResult"])

    async def test_run_keeps_looking(self):
        calls = []

        async def check():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            if len(calls) == 3:
                raise asyncio.CancelledError
            return "current"
        self.r.check = check
        with self.assertLogs("newman.voice.whisper_restart", "WARNING"), self.assertRaises(asyncio.CancelledError):
            await self.r.run()
        self.assertEqual(len(calls), 3)


class GuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        async def ready():
            return True
        self.r = WR.WhisperRestarter(Path("/nonexistent"), "newman-whisper", lambda: None, ready)

    async def transcribe(self, pcm, rate=16000, width=2, channels=1, language="en"):
        self.seen = self.r.active
        if self.r.restarting and not getattr(self, "fixed", False):
            raise WyomingError("can't reach 127.0.0.1:10300")
        return "turn on the lights"

    async def test_counts_requests_and_rides_out_a_restart(self):
        g = self.r.guard(self)
        self.assertEqual(await g.transcribe(b"\0" * 10), "turn on the lights")
        self.assertEqual((self.seen, self.r.active), (1, 0))
        self.assertGreater(self.r.last_used, 0)
        # Whisper restarting: the request waits for it, then is transcribed.
        self.r.restarting = True
        self.r._back = asyncio.Event()
        task = asyncio.create_task(g.transcribe(b"\0" * 10))
        await asyncio.sleep(0.05)
        self.assertFalse(task.done())
        self.r.restarting = False
        self.r._back.set()
        self.assertEqual(await task, "turn on the lights")

    async def test_still_restarting_is_a_one_moment_reply_not_an_error(self):
        old = WR.RETRY_WAIT_S
        WR.RETRY_WAIT_S = 0.05
        try:
            self.r.restarting = True
            g = self.r.guard(self)
            with self.assertRaises(SpeechRestarting):
                await g.transcribe(b"\0" * 10)
            self.assertEqual(self.r.active, 0)
            p = Pipeline(Sources(world=lambda v: None, command=None, today=lambda v: {}, log_event=lambda *a, **k: None),
                         Timers(sqlite3.connect(":memory:")), g, None)
            out = await p.handle(VoiceViewer({"id": "u1", "name": "Hall"}, {}), pcm=b"\0" * 6400)
            self.assertEqual(out["heard"], "")
            self.assertTrue(out["reply"].startswith("One moment"))
            self.assertEqual(out["action"], {"type": "none", "retry": True})
        finally:
            WR.RETRY_WAIT_S = old

    async def test_other_failures_are_still_errors(self):
        async def broken(*a, **k):
            raise WyomingError("can't reach 127.0.0.1:10300")
        self.transcribe = broken
        with self.assertRaises(WyomingError):
            await self.r.guard(self).transcribe(b"")


class DockerByNameTests(unittest.IsolatedAsyncioTestCase):
    async def test_inspect_and_restart_by_name(self):
        seen = []

        def handler(req):
            seen.append((req.method, req.url.path, dict(req.url.params)))
            if req.url.path == "/containers/newman-whisper/json":
                return httpx.Response(200, json={"State": {"Running": True, "StartedAt": "2026-10-09T05:00:00Z"}})
            if req.url.path == "/containers/newman-whisper/restart":
                return httpx.Response(403 if deny else 204)
            return httpx.Response(404, json={"message": "no such container"})
        deny = False
        with tempfile.TemporaryDirectory() as tmp:
            d = DockerIntegration("docker", "Docker", {"address": "http://proxy:2375"},
                                  Context(Hub(), Path(tmp), Path(tmp)))
            d._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://proxy:2375")
            self.assertTrue(d.knows("newman-whisper"))  # not listed yet
            info = await d.container_info("newman-whisper")
            self.assertTrue(info["State"]["Running"])
            self.assertIsNone(await d.container_info("gone"))
            await d.restart_container("newman-whisper")
            self.assertEqual(seen[-1], ("POST", "/containers/newman-whisper/restart", {"t": "10"}))
            self.assertIn("newman-whisper", d._stopping)  # its "die" isn't a crash
            deny = True
            with self.assertRaisesRegex(CommandError, "read-only"):
                await d.restart_container("newman-whisper")
            d._names = ["plex"]
            self.assertFalse(d.knows("newman-whisper"))


if __name__ == "__main__":
    unittest.main()
