"""Alert videos (GET /api/clips/...): a finished clip asked for by several players at once is copied from
Frigate once - the others wait for that copy - and a copy that breaks off leaves no file behind."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.integrations.frigate import FrigateIntegration  # noqa: E402
from test_auth import H  # noqa: E402

VIDEO = [bytes([i]) * 1000 for i in range(5)]


class Upstream:
    """Frigate's clip.mp4, a chunk at a time (slowly, so the requests overlap)."""

    def __init__(self, status=200, break_after=None):
        self.status_code, self.break_after = status, break_after

    async def aiter_bytes(self, size):
        for i, chunk in enumerate(VIDEO):
            await asyncio.sleep(0.02)
            if self.break_after is not None and i >= self.break_after:
                raise ConnectionError("Frigate went away")
            yield chunk

    async def aclose(self):
        pass


class FakeFrigate(FrigateIntegration):
    def __init__(self, ctx):
        super().__init__("frig", "Frigate", {"url": "http://frigate.invalid"}, ctx)
        self.opened = 0
        self.finished = True
        self.break_after = None

    async def event_finished(self, clip_id):
        return self.finished

    async def open_clip(self, clip_id, range_header):
        self.opened += 1
        return Upstream(break_after=self.break_after)


class ClipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.frigate = FakeFrigate(self.h.integrations._ctx)
        self.h.integrations.instances["frig"] = self.frigate
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        r = await self.client.post("/api/auth/setup", json={"username": "Chris", "name": "Chris", "password": "correct horse"})
        self.assertEqual(r.status, 200)

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def get(self, path):
        r = await self.client.get(path)
        return r.status, await r.read()

    async def test_one_copy_for_players_asking_at_once(self):
        got = await asyncio.gather(*(self.get("/api/clips/frig/r:abc") for _ in range(4)))
        self.assertEqual([s for s, _b in got], [200] * 4)
        self.assertTrue(all(b == b"".join(VIDEO) for _s, b in got))
        self.assertEqual(self.frigate.opened, 1)
        self.assertEqual(sorted(p.name for p in self.h.clip_dir.iterdir()), ["frig_r_abc.mp4"])
        status, _b = await self.get("/api/clips/frig/r:abc")  # kept: not fetched again
        self.assertEqual((status, self.frigate.opened), (200, 1))

    async def test_a_broken_copy_leaves_nothing(self):
        self.frigate.break_after = 2
        got = await asyncio.gather(*(self.get("/api/clips/frig/r:def") for _ in range(2)))
        self.assertEqual([s for s, _b in got], [502, 502])
        self.assertEqual(self.frigate.opened, 1)
        self.assertEqual(list(self.h.clip_dir.iterdir()), [])
        self.frigate.break_after = None  # trying again fetches it again
        status, body = await self.get("/api/clips/frig/r:def")
        self.assertEqual((status, body, self.frigate.opened), (200, b"".join(VIDEO), 2))

    async def test_still_recording_is_streamed_each_time(self):
        self.frigate.finished = False
        got = await asyncio.gather(*(self.get("/api/clips/frig/r:ghi") for _ in range(2)))
        self.assertEqual([s for s, _b in got], [200, 200])
        self.assertEqual(self.frigate.opened, 2)
        self.assertFalse(self.h.clip_dir.exists() and any(self.h.clip_dir.iterdir()))


if __name__ == "__main__":
    unittest.main()
