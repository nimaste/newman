"""Lyrics for the wall's player (lyrics.py, GET /api/music/lyrics/<id>): LRC parsing, Navidrome first
(OpenSubsonic, then the classic call), LRCLIB as the backup (only song details sent; off with the
connection's setting), the cache (none-found included), who may ask; and devices that go away reaching
open screens straight away. Every lyric line here is made up."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman import lyrics as L  # noqa: E402
from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.state import Hub  # noqa: E402
from test_auth import Browser, H  # noqa: E402
from test_navidrome import PASSWORD, FakeSubsonic, song, wait_for  # noqa: E402

SONGS = {
    "s1": song("s1", "Fixture Song One", "Test Artist", "Test Album", "al1", 1, 180),
    "s2": song("s2", "Fixture Song Two", "Test Artist", "Test Album", "al1", 2, 200),
    "s3": song("s3", "Fixture Song Three", "Other Artist", "Other Album", "al2", 1, 210),
    "s4": song("s4", "Fixture Song Four", "Other Artist", "Other Album", "al2", 2, 150),
    "s5": song("s5", "Fixture Song Five", "Nobody", "Nothing", "al3", 1, 100),
}


class LyricsSubsonic(FakeSubsonic):
    """s1: OpenSubsonic synced lyrics; s2: only the classic plain call; others: nothing."""

    def __init__(self):
        super().__init__()
        self.opensubsonic = True

    async def handle(self, req):
        name, q = req.match_info["name"], req.query
        if name == "getSong" and q.get("id") in SONGS:
            self.calls.append((name, dict(q)))
            return self.ok(song=SONGS[q["id"]])
        if name == "getLyricsBySongId":
            self.calls.append((name, dict(q)))
            if not self.opensubsonic:
                return self.fail(0, "unknown getLyricsBySongId")
            if q.get("id") == "s1":
                return self.ok(lyricsList={"structuredLyrics": [
                    {"lang": "xxx", "synced": False, "line": [{"value": "Unsynced copy of the fixture"}]},
                    {"lang": "xxx", "synced": True, "offset": 0, "line": [
                        {"start": 5000, "value": "First test line"}, {"start": 1000, "value": "Opening test line"},
                        {"start": 9500, "value": "Second test line"}]}]})
            return self.ok(lyricsList={})
        if name == "getLyrics":
            self.calls.append((name, dict(q)))
            if q.get("title") == "Fixture Song Two":
                return self.ok(lyrics={"artist": "Test Artist", "title": "Fixture Song Two",
                                       "value": "Plain test line one\nPlain test line two"})
            return self.ok(lyrics={})
        return await super().handle(req)


class FakeLrclib:
    """/api/get knows "Fixture Song Three" (synced LRC); /api/search finds "Fixture Song Four" (plain)."""

    def __init__(self):
        self.calls, self.runner = [], None

    async def get(self, req):
        self.calls.append(("get", dict(req.query), req.headers.get("User-Agent")))
        if req.query.get("track_name") == "Fixture Song Three":
            return web.json_response({"trackName": "Fixture Song Three", "duration": 210, "syncedLyrics":
                                      "[ar:Other Artist]\n[00:02.50]Made-up line A\n[00:04.00][00:12.00]Made-up chorus\n"
                                      "[00:08.00]\n[00:10.25]Made-up line B", "plainLyrics": "unused"})
        return web.json_response({"code": 404, "name": "TrackNotFound"}, status=404)

    async def search(self, req):
        self.calls.append(("search", dict(req.query), req.headers.get("User-Agent")))
        return web.json_response([
            {"trackName": "Something Else", "duration": 150, "plainLyrics": "Wrong fixture line"},
            {"trackName": "Fixture Song Four", "duration": 151, "syncedLyrics": None,
             "plainLyrics": "Searched test line one\nSearched test line two"}])

    async def start(self):
        app = web.Application()
        app.router.add_get("/api/get", self.get)
        app.router.add_get("/api/search", self.search)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        return f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

    async def stop(self):
        await self.runner.cleanup()


class ParseTests(unittest.TestCase):
    def test_lrc(self):
        lines, plain = L.parse_lrc(
            "[ti:Fixture]\n[offset:+500]\n[00:01.00]First test line\n[00:03.50][00:20.00]Repeated test line\n"
            "\n[00:10.00]\n[01:02.345]Line three of the fixture\nno stamp here")
        self.assertEqual(lines, [{"t": 0.5, "text": "First test line"}, {"t": 3.0, "text": "Repeated test line"},
                                 {"t": 9.5, "text": ""}, {"t": 19.5, "text": "Repeated test line"},
                                 {"t": 61.845, "text": "Line three of the fixture"}])
        self.assertEqual(plain, "First test line\nRepeated test line\n\nLine three of the fixture\nno stamp here")

    def test_negative_offset_and_short_stamps(self):
        lines, _ = L.parse_lrc("[offset:-250]\n[00:01.1]Fixture a\n[1:02]Fixture b")
        self.assertEqual(lines, [{"t": 1.35, "text": "Fixture a"}, {"t": 62.25, "text": "Fixture b"}])

    def test_plain_text_is_not_synced(self):
        got = L.from_lrc("Plain test line one\n\nPlain test line two", "lrclib")
        self.assertEqual((got["synced"], got["lines"], got["text"]),
                         (False, [], "Plain test line one\n\nPlain test line two"))
        self.assertIsNone(L.from_lrc("[ar:Nobody]\n", "lrclib"))

    def test_structured(self):
        got = L.from_structured([{"synced": True, "offset": 100, "line": [
            {"start": 2100, "value": "Second test line"}, {"start": 1100, "value": "First test line"}]}])
        self.assertEqual(got["lines"], [{"t": 1.0, "text": "First test line"}, {"t": 2.0, "text": "Second test line"}])
        self.assertTrue(got["synced"])
        got = L.from_structured([{"synced": False, "line": [{"value": "[00:05.00]Line three of the fixture"}]}])
        self.assertEqual((got["synced"], got["lines"]), (True, [{"t": 5.0, "text": "Line three of the fixture"}]))


class LyricsHubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", self.data)
        self.fake = LyricsSubsonic()
        self.url = await self.fake.start()
        self.lrclib = FakeLrclib()
        self.old_lrclib, L.LRCLIB_URL = L.LRCLIB_URL, await self.lrclib.start()
        self.h = H(self.data)
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        r = await self.client.post("/api/users", json={"username": "kitchen", "name": "Kitchen tablet",
                                                       "role": "tablet", "password": "wall tablet pass"})
        self.assertEqual(r.status, 200, await r.text())
        self.tab = Browser(self.client)
        r = await self.tab.post("/api/auth/login", json={"username": "kitchen", "password": "wall tablet pass", "kind": "tablet"})
        self.auth = {"Authorization": "Bearer " + (await r.json())["token"]}
        r = await self.client.post("/api/integrations", json={"type": "navidrome", "name": "Navidrome", "config": {
            "url": self.url, "username": "chris", "password": PASSWORD}})
        self.assertEqual(r.status, 200, await r.text())
        self.iid = (await r.json())["id"]
        self.assertTrue(await wait_for(lambda: self.inst().status == "connected"))

    async def asyncTearDown(self):
        await self.tab.close()
        await self.h.integrations.stop_all()
        await self.client.close()
        await self.fake.stop()
        await self.lrclib.stop()
        L.LRCLIB_URL = self.old_lrclib
        self.tmp.cleanup()

    def inst(self):
        return self.h.integrations.instances[self.iid]

    async def lyrics(self, sid):
        r = await self.tab.get(f"/api/music/lyrics/{sid}", headers=self.auth)
        self.assertEqual(r.status, 200, await r.text())
        return await r.json()

    def asked(self, name):
        return [q for n, q in self.fake.calls if n == name]

    async def test_navidrome_first_then_lrclib(self):
        got = await self.lyrics("s1")  # OpenSubsonic synced, sorted, in seconds
        self.assertEqual((got["source"], got["synced"]), ("navidrome", True))
        self.assertEqual([x["text"] for x in got["lines"]], ["Opening test line", "First test line", "Second test line"])
        self.assertEqual(got["lines"][0]["t"], 1.0)
        self.assertEqual(self.asked("getLyrics"), [])
        self.assertEqual(self.lrclib.calls, [])

        got = await self.lyrics("s2")  # the classic call
        self.assertEqual((got["source"], got["synced"], got["text"]),
                         ("navidrome", False, "Plain test line one\nPlain test line two"))
        self.assertEqual(self.asked("getLyrics")[-1]["title"], "Fixture Song Two")
        self.assertEqual(self.lrclib.calls, [])

        got = await self.lyrics("s3")  # LRCLIB's exact match, synced
        self.assertEqual((got["source"], got["synced"]), ("lrclib", True))
        self.assertEqual(got["lines"], [{"t": 2.5, "text": "Made-up line A"}, {"t": 4.0, "text": "Made-up chorus"},
                                        {"t": 8.0, "text": ""}, {"t": 10.25, "text": "Made-up line B"},
                                        {"t": 12.0, "text": "Made-up chorus"}])
        kind, params, ua = self.lrclib.calls[-1]
        self.assertEqual(kind, "get")
        self.assertEqual(params, {"artist_name": "Other Artist", "track_name": "Fixture Song Three",
                                  "album_name": "Other Album", "duration": "210"})  # song details only
        self.assertTrue(ua.startswith("Newman-home-hub/1.0"))

        got = await self.lyrics("s4")  # not found exactly: the search, matched by title and length
        self.assertEqual((got["source"], got["synced"], got["text"]),
                         ("lrclib", False, "Searched test line one\nSearched test line two"))
        self.assertEqual([c[0] for c in self.lrclib.calls[-2:]], ["get", "search"])
        self.assertEqual(self.lrclib.calls[-1][1], {"q": "Other Artist Fixture Song Four"})

    async def test_older_navidrome_without_opensubsonic(self):
        self.fake.opensubsonic = False
        got = await self.lyrics("s2")
        self.assertEqual((got["source"], got["text"]), ("navidrome", "Plain test line one\nPlain test line two"))

    async def test_none_found_is_remembered(self):
        got = await self.lyrics("s5")
        self.assertEqual(got, {"source": None, "synced": False, "lines": [], "text": ""})
        n_nav, n_lrc = len(self.fake.calls), len(self.lrclib.calls)
        self.assertEqual(n_lrc, 2)  # get, then search
        self.assertEqual((await self.lyrics("s5"))["source"], None)
        self.assertEqual((len(self.fake.calls), len(self.lrclib.calls)), (n_nav, n_lrc))  # from memory
        await self.lyrics("s3")
        cache = self.data / "integrations" / self.iid / "lyrics"
        self.assertTrue((cache / "s5.json").exists() and (cache / "s3.json").exists())
        # A restart (settings saved) keeps them: the disk copy answers, nobody is asked.
        await self.h.integrations.update(self.iid, name="Music")
        self.assertTrue(await wait_for(lambda: self.inst().status == "connected"))
        n_nav, n_lrc = len(self.fake.calls), len(self.lrclib.calls)
        self.assertEqual((await self.lyrics("s3"))["lines"][0]["text"], "Made-up line A")
        self.assertIsNone((await self.lyrics("s5"))["source"])
        self.assertEqual(len(self.lrclib.calls), n_lrc)
        self.assertEqual([c for c in self.fake.calls[n_nav:] if c[0].startswith("getLyrics")], [])
        # An expired answer is looked up again.
        d = json.loads((cache / "s5.json").read_text())
        d["expires"] = 1
        (cache / "s5.json").write_text(json.dumps(d))
        self.inst().lyrics.mem.clear()
        await self.lyrics("s5")
        self.assertGreater(len(self.lrclib.calls), n_lrc)

    async def test_lrclib_off_means_navidrome_only(self):
        self.assertEqual((await self.lyrics("s3"))["source"], "lrclib")
        await self.h.integrations.update(self.iid, cfg={"lrclib": False})
        self.assertTrue(await wait_for(lambda: self.inst().status == "connected"))
        n = len(self.lrclib.calls)
        got = await self.lyrics("s3")  # the cached LRCLIB answer isn't shown either
        self.assertIsNone(got["source"])
        self.assertIsNone((await self.lyrics("s4"))["source"])
        self.assertEqual((await self.lyrics("s1"))["source"], "navidrome")
        self.assertEqual(len(self.lrclib.calls), n)
        # On again: a "none" from while it was off is looked up online.
        await self.h.integrations.update(self.iid, cfg={"lrclib": True})
        self.assertTrue(await wait_for(lambda: self.inst().status == "connected"))
        self.assertEqual((await self.lyrics("s4"))["source"], "lrclib")

    async def test_lrclib_down_is_not_remembered_for_long(self):
        await self.lrclib.stop()
        got = await self.lyrics("s3")
        self.assertIsNone(got["source"])
        self.assertFalse((self.data / "integrations" / self.iid / "lyrics" / "s3.json").exists())

    async def test_who_may_ask(self):
        anon = Browser(self.client)
        try:
            r = await anon.get("/api/music/lyrics/s1")
            self.assertEqual(r.status, 401)
        finally:
            await anon.close()
        r = await self.client.get("/api/music/lyrics/s1")  # the signed-in admin
        self.assertEqual(r.status, 200)
        r = await self.tab.get("/api/music/lyrics/bad%20id!", headers=self.auth)
        self.assertEqual(r.status, 404)
        self.assertEqual(self.lrclib.calls, [])


class LyricsVoiceTests(unittest.TestCase):
    def test_show_the_lyrics(self):
        from newman.voice import intents as I
        from test_navidrome import music_world
        for text in ("show the lyrics", "Hey Newman, show me the lyrics", "lyrics"):
            it = I.parse(text, music_world())
            self.assertEqual((it.type, it.show, it.reply), ("show", "lyrics", ""), text)
        self.assertEqual(I.parse("show the lyrics", music_world(state="idle", title="")).reply, "Nothing is playing.")


class RemovalPushTests(unittest.IsolatedAsyncioTestCase):
    async def test_hub_tells_listeners(self):
        hub = Hub()
        added, removed = [], []

        async def on_added(e):
            added.append(e.id)

        async def on_removed(ids):
            removed.extend(ids)
        hub.subscribe_registry(on_added, on_removed)
        hub.upsert("x.a", kind="light", name="A", integration="x")
        hub.upsert("x.b", kind="light", name="B", integration="x")
        hub.upsert("x.a", kind="light", name="A again", integration="x")  # not new
        self.assertTrue(await wait_for(lambda: len(added) == 2))
        self.assertEqual(added, ["x.a", "x.b"])
        # A restart: back within the grace time -> never gone, not "new" either.
        hub.remove_integration("x", grace=0.2)
        hub.upsert("x.a", kind="light", name="A", integration="x")
        await asyncio.sleep(0.4)
        self.assertEqual(removed, ["x.b"])
        self.assertEqual(added, ["x.a", "x.b"])
        self.assertTrue(hub.remove("x.a"))
        self.assertTrue(await wait_for(lambda: removed == ["x.b", "x.a"]))
        self.assertFalse(hub.remove("x.a"))
        hub.upsert("y.c", kind="light", name="C", integration="y")
        hub.remove_integration("y", grace=None)  # shutting down: nobody told
        await asyncio.sleep(0.1)
        self.assertNotIn("y.c", removed)

    async def test_screens_hear_a_removed_connection(self):
        tmp = tempfile.TemporaryDirectory()
        data = Path(tmp.name)
        object.__setattr__(config, "data_dir", data)
        fake = FakeSubsonic()
        url = await fake.start()
        h = H(data)
        client = TestClient(TestServer(build_app(h)))
        await client.start_server()
        tab = Browser(client)
        try:
            await client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
            await client.post("/api/users", json={"username": "kitchen", "name": "Kitchen tablet", "role": "tablet",
                                                  "password": "wall tablet pass"})
            r = await tab.post("/api/auth/login", json={"username": "kitchen", "password": "wall tablet pass", "kind": "tablet"})
            auth = {"Authorization": "Bearer " + (await r.json())["token"]}
            ws = await tab._s.ws_connect(client.make_url("/api/ws"), headers=auth)
            got = []

            async def listen():
                async for msg in ws:
                    got.append(json.loads(msg.data))
            task = asyncio.create_task(listen())
            r = await client.post("/api/integrations", json={"type": "navidrome", "name": "Navidrome", "config": {
                "url": url, "username": "chris", "password": PASSWORD}})
            iid = (await r.json())["id"]
            await wait_for(lambda: h.integrations.instances[iid].status == "connected")
            eid = (await (await tab.get("/api/music", headers=auth)).json())["player"]["entityId"]
            self.assertTrue(await wait_for(lambda: any(m.get("type") == "entity" and m["entity"]["id"] == eid for m in got)))
            r = await client.delete(f"/api/integrations/{iid}")
            self.assertEqual(r.status, 200, await r.text())
            self.assertTrue(await wait_for(lambda: {"type": "entity-removed", "id": eid} in got))
            self.assertIsNone(h.hub.get(eid))
            task.cancel()
            await ws.close()
        finally:
            await tab.close()
            await h.integrations.stop_all()
            await client.close()
            await fake.stop()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
