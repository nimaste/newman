"""Navidrome music on the wall tablets: token sign-in against a fake Subsonic server (only u/t/s kept),
search and matching, the stream / cover proxy, each tablet's queue (ended -> next), commands routed to
the right tablet's socket, and the voice intents ("play Jump by Van Halen")."""
import asyncio
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import build_app  # noqa: E402
from newman.automations.engine import Engine  # noqa: E402
from newman.config import config  # noqa: E402
from newman.integrations import music_match as M  # noqa: E402
from newman.integrations import navidrome as N  # noqa: E402
from newman.voice import intents as I  # noqa: E402
from test_auth import Browser, H  # noqa: E402

PASSWORD = "music pass 1"
AUDIO = bytes(range(256)) * 400  # 100 KiB of "audio"
COVER = b"\xff\xd8\xff\xe0fakejpeg"


def song(sid, title, artist, album, album_id, n=1, duration=200):
    return {"id": sid, "title": title, "artist": artist, "album": album, "albumId": album_id, "coverArt": album_id,
            "duration": duration, "track": n, "contentType": "audio/mpeg", "suffix": "mp3"}


SONGS = [
    song("s1", "Jump", "Van Halen", "1984", "al1", 1, 241),
    song("s2", "Panama", "Van Halen", "1984", "al1", 2),
    song("s3", "Hot for Teacher", "Van Halen", "1984", "al1", 3),
    song("s4", "Jump (Live)", "Van Halen", "Live: Right Here, Right Now", "al2", 1),
    song("s5", "Jump", "Kris Kross", "Totally Krossed Out", "al3", 1),
    song("s6", "Stand by Me", "Ben E. King", "Don't Play That Song", "al4", 1),
    song("s7", "Bohemian Rhapsody", "Queen", "A Night at the Opera", "al5", 1),
]
ALBUMS = [{"id": "al1", "name": "1984", "artist": "Van Halen", "artistId": "ar1", "coverArt": "al1", "songCount": 3},
          {"id": "al2", "name": "Live: Right Here, Right Now", "artist": "Van Halen", "artistId": "ar1", "coverArt": "al2"},
          {"id": "al3", "name": "Totally Krossed Out", "artist": "Kris Kross", "artistId": "ar2", "coverArt": "al3"},
          {"id": "al4", "name": "Don't Play That Song", "artist": "Ben E. King", "artistId": "ar3", "coverArt": "al4"},
          {"id": "al5", "name": "A Night at the Opera", "artist": "Queen", "artistId": "ar4", "coverArt": "al5"}]
ARTISTS = [{"id": "ar1", "name": "Van Halen", "albumCount": 2}, {"id": "ar2", "name": "Kris Kross", "albumCount": 1},
           {"id": "ar3", "name": "Ben E. King", "albumCount": 1}, {"id": "ar4", "name": "Queen", "albumCount": 1}]
PLAYLISTS = [{"id": "pl1", "name": "Road Trip", "songCount": 2, "coverArt": "pl-pl1"},
             {"id": "pl2", "name": "Dinner", "songCount": 1}]
PLAYLIST_SONGS = {"pl1": ["s2", "s7"], "pl2": ["s6"]}


class FakeSubsonic:
    """Just enough of the Subsonic API: token auth (t = md5(password + s)), search3, albums, artists,
    playlists, random songs, stream (with Range) and cover art."""

    def __init__(self, password=PASSWORD, library=None):
        self.password, self.calls, self.token_seen, self.runner = password, [], [], None
        lib = library or {}
        self.songs, self.albums, self.artists = lib.get("songs", SONGS), lib.get("albums", ALBUMS), lib.get("artists", ARTISTS)
        self.playlists, self.playlist_songs = lib.get("playlists", PLAYLISTS), lib.get("playlist_songs", PLAYLIST_SONGS)

    def ok(self, **body):
        return web.json_response({"subsonic-response": {"status": "ok", "version": "1.16.1", **body}})

    def fail(self, code, message):
        return web.json_response({"subsonic-response": {"status": "failed", "version": "1.16.1",
                                                        "error": {"code": code, "message": message}}})

    async def handle(self, req):
        name = req.match_info["name"]
        q = req.query
        self.calls.append((name, dict(q)))
        if "p" in q:
            return self.fail(42, "password auth not allowed in this test")
        if q.get("u") != "chris" or q.get("t") != hashlib.md5((self.password + q.get("s", "")).encode()).hexdigest():
            return self.fail(40, "Wrong username or password")
        self.token_seen.append((q["t"], q["s"]))
        if name == "ping":
            return self.ok()
        if name == "search3":
            words = q.get("query", "").lower().split()
            hit = lambda *texts: all(any(w in t.lower() for t in texts) for w in words)  # noqa: E731
            return self.ok(searchResult3={
                "song": [s for s in self.songs if hit(s["title"], s["artist"], s["album"])][:int(q.get("songCount", 20))],
                "album": [a for a in self.albums if hit(a["name"], a["artist"])][:int(q.get("albumCount", 20))],
                "artist": [a for a in self.artists if hit(a["name"])][:int(q.get("artistCount", 20))]})
        if name == "getAlbum":
            al = next((a for a in self.albums if a["id"] == q.get("id")), None)
            if not al:
                return self.fail(70, "not found")
            return self.ok(album={**al, "song": [s for s in self.songs if s["albumId"] == al["id"]]})
        if name == "getArtists":
            # As Navidrome groups them: by first letter, "The" ignored; made-up names besides the self.songs' self.artists.
            extra = [{"id": "ar8", "name": "The Paper Lanterns", "albumCount": 0},
                     {"id": "ar9", "name": "47 Copper Kites", "albumCount": 0}]
            index = {}
            for a in sorted(self.artists + extra, key=lambda a: a["name"].removeprefix("The ").lower()):
                first = a["name"].removeprefix("The ")[0].upper()
                index.setdefault(first if first.isalpha() else "#", []).append(a)
            return self.ok(artists={"ignoredArticles": "The", "index": [{"name": k, "artist": v} for k, v in index.items()]})
        if name == "getArtist":
            ar = next((a for a in self.artists if a["id"] == q.get("id")), None)
            if not ar:
                return self.fail(70, "Artist not found")
            return self.ok(artist={**ar, "album": [a for a in self.albums if a["artistId"] == ar["id"]]})
        if name == "getPlaylists":
            return self.ok(playlists={"playlist": self.playlists})
        if name == "getPlaylist":
            ids = self.playlist_songs.get(q.get("id"), [])
            pl = next((p for p in self.playlists if p["id"] == q.get("id")), None)
            if not pl:
                return self.fail(70, "not found")
            return self.ok(playlist={**pl, "entry": [s for i in ids for s in self.songs if s["id"] == i]})
        if name == "getRandomSongs":
            return self.ok(randomSongs={"song": self.songs[:5]})
        if name == "getAlbumList2":
            return self.ok(albumList2={"album": [] if q.get("type") == "recent" else self.albums})
        if name == "getSong":
            return self.ok(song=next(s for s in self.songs if s["id"] == q.get("id")))
        if name == "stream":
            if q.get("id") not in {s["id"] for s in self.songs}:
                return self.fail(70, "not found")
            rng = req.headers.get("Range")
            if rng and rng.startswith("bytes="):
                start = int(rng[6:].split("-")[0])
                part = AUDIO[start:]
                return web.Response(body=part, status=206, content_type="audio/mpeg", headers={
                    "Content-Range": f"bytes {start}-{len(AUDIO) - 1}/{len(AUDIO)}", "Accept-Ranges": "bytes"})
            return web.Response(body=AUDIO, content_type="audio/mpeg", headers={"Accept-Ranges": "bytes"})
        if name == "getCoverArt":
            return web.Response(body=COVER, content_type="image/jpeg")
        return self.fail(0, f"unknown {name}")

    async def start(self):
        app = web.Application()
        app.router.add_get("/rest/{name}.view", self.handle)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        return f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

    async def stop(self):
        await self.runner.cleanup()


async def wait_for(cond, timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return cond()


class MatchingTests(unittest.TestCase):
    def test_title_and_artist(self):
        self.assertEqual(N.best_song(SONGS, "jump", "van halen")["id"], "s1")
        self.assertEqual(N.best_song(SONGS, "jump", "kris kross")["id"], "s5")
        self.assertIsNone(N.best_song(SONGS, "jump", "queen"))  # the artist must match too
        self.assertEqual(N.best_song(SONGS, "bohemian rhapsody")["id"], "s7")
        self.assertIsNone(N.best_song(SONGS, "yellow submarine"))

    def test_names_compare_without_the_noise(self):
        self.assertEqual(N.norm("Jump (2015 Remaster)"), "jump")
        self.assertEqual(N.norm("The Beatles"), "beatles")
        self.assertGreaterEqual(N.similarity("beatles", "The Beatles"), 0.99)
        self.assertGreaterEqual(N.similarity("night at the opera", "A Night at the Opera"), 0.85)
        self.assertLess(N.similarity("queen", "Van Halen"), 0.6)

    def test_typed_requests(self):
        self.assertEqual(N.request_from_text("Jump by Van Halen"), {"title": "Jump", "artist": "Van Halen", "shuffle": False})
        self.assertEqual(N.request_from_text("the album 1984 by Van Halen")["album"], "1984")
        self.assertEqual(N.request_from_text("my Road Trip playlist", True), {"playlist": "Road Trip", "shuffle": True})
        self.assertEqual(N.request_from_text("Queen"), {"query": "Queen", "shuffle": False})

    def test_token(self):
        self.assertEqual(N.make_token("sesame", "c19b2d"), hashlib.md5(b"sesamec19b2d").hexdigest())
        self.assertNotEqual(N.new_salt(), N.new_salt())


# -- voice: parsing --------------------------------------------------------------------------------

def music_world(state="playing", title="Jump", others=()):
    player = I.Device("navidrome.t1", "Kitchen tablet music", "music_player", state,
                      {"title": title, "artist": "Van Halen", "volume": 50} if title else {"volume": 50})
    tv = I.Device("tv.lr", "Living Room TV", "tv", "on", {})
    vac = I.Device("vac.1", "Robot", "vacuum", "cleaning", {})
    return I.World(devices=[player, tv, vac, *others], music=player)


class MusicIntentTests(unittest.TestCase):
    def p(self, text, **kw):
        return I.parse(text, music_world(**kw))

    def test_play_requests(self):
        cases = {
            "Hey Newman, play Jump by Van Halen": {"title": "jump", "artist": "van halen", "shuffle": False},
            "play Van Halen": {"query": "van halen", "shuffle": False},
            "play the album 1984": {"album": "1984", "artist": "", "shuffle": False},
            "play the album 1984 by Van Halen": {"album": "1984", "artist": "van halen", "shuffle": False},
            "play my road trip playlist": {"playlist": "road trip", "shuffle": False},
            "shuffle my dinner playlist": {"playlist": "dinner", "shuffle": True},
            "shuffle Queen": {"query": "queen", "shuffle": True},
            "play some music by Queen": {"artist": "queen", "shuffle": False},
            "play songs by Kris Kross": {"artist": "kris kross", "shuffle": False},
            "put on Bohemian Rhapsody": {"query": "bohemian rhapsody", "shuffle": False},
            "play Queen on shuffle": {"query": "queen", "shuffle": True},
            "play some music": {"random": True, "shuffle": True},
        }
        for text, music in cases.items():
            it = self.p(text, state="idle", title="")
            self.assertEqual((it.type, it.music), ("music_play", music), text)

    def test_controls(self):
        cases = {"pause the music": "pause", "pause": "pause", "resume the music": "play", "resume": "play",
                 "next song": "next", "skip": "next", "skip this song": "next", "previous song": "previous",
                 "go back": "previous", "stop the music": "stop", "stop playing": "stop",
                 "volume up": "volume_up", "turn it down": "volume_down", "louder": "volume_up",
                 "turn the music down": "volume_down", "play": "play"}
        for text, cmd in cases.items():
            it = self.p(text)
            self.assertEqual((it.type, it.command.id if it.command else None), ("music_control", cmd), text)
        it = self.p("set the volume to 40")
        self.assertEqual((it.type, it.command.id, it.value), ("music_control", "set_volume", 40.0))
        it = self.p("set volume to forty")
        self.assertEqual((it.command.id, it.value), ("set_volume", 40.0))
        self.assertEqual(self.p("set the volume to 140").reply, "The volume goes from 0 to 100.")

    def test_whats_playing(self):
        for text in ("what's playing", "what song is this", "who sings this", "what is this song", "who is this"):
            self.assertEqual(self.p(text).type, "music_what", text)
        self.assertEqual(I.say_music_what(music_world().music), "This is Jump by Van Halen.")
        self.assertEqual(I.say_music_what(music_world(state="idle", title="").music), "Nothing is playing.")

    def test_other_things_stay_what_they_were(self):
        # "stop" alone stops timers / speech; the TV is still the TV; bare "pause" with no music playing
        # is about something else.
        self.assertEqual(self.p("stop").type, "stop")
        it = self.p("set the TV volume to 20")
        self.assertEqual((it.type, it.devices[0].id), ("command", "tv.lr"))
        it = self.p("play the living room tv")
        self.assertEqual((it.type, it.devices[0].id), ("command", "tv.lr"))
        self.assertNotEqual(self.p("pause", state="idle", title="").type, "music_control")
        self.assertEqual(self.p("what's the temperature").type != "music_what", True)
        # No music player on this tablet: "play" is left to the devices.
        no_music = I.World(devices=[I.Device("tv.lr", "Living Room TV", "tv", "on", {})])
        self.assertNotEqual(I.parse("play jump by van halen", no_music).type, "music_play")

    def test_what_speech_to_text_makes_of_play(self):
        # Seen live: "Played Bed of Roses". Other ways of asking, and the words around the name.
        cases = {
            "Played Bed of Roses": {"query": "bed of roses", "shuffle": False},
            "plays bed of roses": {"query": "bed of roses", "shuffle": False},
            "playing bed of roses": {"query": "bed of roses", "shuffle": False},
            "can you play bed of roses": {"query": "bed of roses", "shuffle": False},
            "let's hear bed of roses": {"query": "bed of roses", "shuffle": False},
            "I want to hear bed of roses": {"query": "bed of roses", "shuffle": False},
            "put on some bed of roses": {"query": "bed of roses", "shuffle": False},
            "play the song bed of roses": {"title": "bed of roses", "artist": "", "kind": "song", "shuffle": False},
            "play a song called bed of roses": {"title": "bed of roses", "artist": "", "kind": "song", "shuffle": False},
            "play the song bed of roses by bon jovi": {"title": "bed of roses", "artist": "bon jovi", "shuffle": False},
            "played bed of roses by bon jovi": {"title": "bed of roses", "artist": "bon jovi", "shuffle": False},
            "play something from the album glass harbor": {"album": "glass harbor", "artist": "", "shuffle": False},
            "play from the album glass harbor by velvet engines": {"album": "glass harbor", "artist": "velvet engines",
                                                                   "shuffle": False},
            "play the band velvet engines": {"artist": "velvet engines", "shuffle": False},
            "play by bon jovi": {"artist": "bon jovi", "shuffle": False},
        }
        for text, music in cases.items():
            it = self.p(text, state="idle", title="")
            self.assertEqual((it.type, it.music), ("music_play", music), text)
        # Not requests: "playing" alone, "what's playing".
        self.assertNotEqual(self.p("playing").type, "music_play")
        self.assertEqual(self.p("what is playing").type, "music_what")
        # Unchanged: "play the doorbell camera" is still a music request (nothing else plays a camera) - the
        # library match must then come up empty rather than play something (see the hub test below).
        cam = I.Device("camera.door", "Doorbell Camera", "camera", "idle", {})
        it = self.p("play the doorbell camera", others=[cam])
        self.assertEqual((it.type, it.music), ("music_play", {"query": "the doorbell camera", "shuffle": False}))
        self.assertEqual(self.p("show the doorbell camera", others=[cam]).show, "camera")

    def test_yes_to_did_you_mean(self):
        w = music_world(state="idle", title="")
        w.music_offer = True
        for text, yes in (("yes", True), ("yes please", True), ("yeah play it", True), ("sure", True), ("okay", True),
                          ("no", False), ("no thanks", False)):
            it = I.parse(text, w)
            self.assertEqual((it.type, it.value), ("music_answer", yes), text)
        # Without a question waiting, "yes" means nothing and "okay" stops (as before).
        self.assertEqual(self.p("yes", state="idle", title="").type, "unknown")
        self.assertEqual(self.p("okay", state="idle", title="").type, "stop")


# -- matching misheard names (music_match.py) -------------------------------------------------------

def cands(*names, kind="song", artist=""):
    return [{"kind": kind, "name": n, "artist": artist, "item": {"id": kind + n + artist}} for n in names]


class MisheardTests(unittest.TestCase):
    LIB = cands("Bed of Roses", "Better Days Ahead", "Bitter Harbor", "Glass Harbor", "Paper Moons", "Saffron Parade",
                "Lanterns at Dusk", "Doorway Camera Blues")

    def best(self, said, **kw):
        r = M.rank(said, self.LIB, **kw)
        return M.band(r), (r[0][1]["name"] if r else None)

    def test_sounds_alike(self):
        self.assertEqual(M.sound("better roses"), M.sound("bed of roses"))
        self.assertEqual(M.sound_word("phone"), "FN")
        self.assertEqual(self.best("better roses"), ("high", "Bed of Roses"))
        self.assertEqual(self.best("bad of roses"), ("high", "Bed of Roses"))
        self.assertEqual(self.best("glass harbour"), ("high", "Glass Harbor"))
        self.assertEqual(self.best("red roses"), ("medium", "Bed of Roses"))   # close: ask first

    def test_unrelated_words_find_nothing(self):
        for said in ("the doorbell camera", "yellow submarine", "turn on the lights", "something else entirely"):
            self.assertEqual(self.best(said)[0], "low", said)

    def test_by_an_artist(self):
        lib = cands("Glass Harbor", artist="Velvet Engines") + cands("Glass Harbour", artist="Amber Owls")
        r = M.rank("glass harbor", lib, artist="amber owls")
        self.assertEqual([c["artist"] for _, c in r], ["Amber Owls"])  # the other artist's is left out

    def test_a_tie_goes_to_the_bigger_thing(self):
        lib = cands("Glass Harbor") + cands("Glass Harbor", kind="album")
        r = M.rank("glass harbor", lib)
        self.assertEqual((r[0][1]["kind"], M.band(r)), ("album", "high"))  # an album and its title song agree


# -- the hub: sign-in, players, proxy, routing, voice ----------------------------------------------

class NavidromeHubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", self.data)
        self.fake = FakeSubsonic(library=getattr(self, "LIBRARY", None))
        self.url = await self.fake.start()
        self.h = H(self.data)
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        self.tabs = {}
        for name in ("kitchen", "hallway"):
            r = await self.client.post("/api/users", json={"username": name, "name": name.capitalize() + " tablet",
                                                           "role": "tablet", "password": "wall tablet pass"})
            uid = (await r.json())["id"]
            b = Browser(self.client)
            r = await b.post("/api/auth/login", json={"username": name, "password": "wall tablet pass", "kind": "tablet"})
            self.tabs[name] = {"id": uid, "b": b, "auth": {"Authorization": "Bearer " + (await r.json())["token"]}}

    async def asyncTearDown(self):
        for t in self.tabs.values():
            await t["b"].close()
        await self.h.integrations.stop_all()
        await self.client.close()
        await self.fake.stop()
        self.tmp.cleanup()

    async def connect(self, password=PASSWORD):
        r = await self.client.post("/api/integrations", json={"type": "navidrome", "name": "Navidrome", "config": {
            "url": self.url, "username": "chris", "password": password}})
        self.assertEqual(r.status, 200, await r.text())
        rec = await r.json()
        self.inst = self.h.integrations.instances[rec["id"]]
        await wait_for(lambda: self.inst.status in ("connected", "pairing", "error"))
        return rec

    def stored(self, iid):
        return next(r for r in self.h.store.integrations() if r["id"] == iid)["config"]

    async def tget(self, name, path, **kw):
        return await self.tabs[name]["b"].get(path, headers=self.tabs[name]["auth"], **kw)

    async def tpost(self, name, path, body):
        return await self.tabs[name]["b"].post(path, json=body, headers=self.tabs[name]["auth"])

    async def test_sign_in_keeps_only_the_token(self):
        rec = await self.connect()
        self.assertEqual(self.inst.status, "connected", self.inst.detail)
        cfg = self.stored(rec["id"])
        self.assertNotIn("password", cfg)                     # the password is goneÃ¢â‚¬Â¦
        self.assertEqual(cfg, {"url": self.url, "username": "chris", "lrclib": True})  # + the lyrics setting's default
        tok = json.loads((self.data / "integrations" / rec["id"] / "token.json").read_text())
        self.assertEqual(set(tok), {"u", "t", "s"})           # Ã¢â‚¬Â¦only the username, token and salt are kept
        self.assertEqual(tok["t"], hashlib.md5((PASSWORD + tok["s"]).encode()).hexdigest())
        self.assertNotIn(PASSWORD, json.dumps(tok))
        self.assertTrue(all("p" not in q for _, q in self.fake.calls))  # never sent as a password either
        # A restart signs in with the kept token alone.
        await self.h.integrations.update(rec["id"], name="Music")
        self.inst = self.h.integrations.instances[rec["id"]]
        self.assertTrue(await wait_for(lambda: self.inst.status == "connected"))
        # The password changed on Navidrome: the token stops working and the password is asked for again.
        self.fake.password = "new pass"
        await self.h.integrations.update(rec["id"], name="Music")
        self.inst = self.h.integrations.instances[rec["id"]]
        self.assertTrue(await wait_for(lambda: self.inst.status == "pairing"))
        self.assertIn("password again", self.inst.detail)
        await self.h.integrations.update(rec["id"], cfg={"password": "new pass"})
        self.inst = self.h.integrations.instances[rec["id"]]
        self.assertTrue(await wait_for(lambda: self.inst.status == "connected"))
        self.assertNotIn("password", self.stored(rec["id"]))

    async def test_wrong_password_asks_again(self):
        await self.connect("nope")
        self.assertEqual(self.inst.status, "pairing")
        self.assertIn("didn't accept", self.inst.detail)

    async def test_each_tablet_gets_a_player_and_plays_through_the_hub(self):
        await self.connect()
        r = await self.tget("kitchen", "/api/music")
        info = await r.json()
        self.assertTrue(info["available"])
        eid = info["player"]["entityId"]
        e = self.h.hub.get(eid)
        self.assertEqual((e.kind, e.name, e.state, e.available), ("music_player", "Kitchen tablet music", "idle", True))
        # Never the token in anything a browser gets.
        tok = json.loads((self.data / "integrations" / self.inst.id / "token.json").read_text())["t"]
        self.assertNotIn(tok, json.dumps(info))

        # Each tablet hears only about its own player.
        ws_k = await self.tabs["kitchen"]["b"]._s.ws_connect(self.client.make_url("/api/ws"), headers=self.tabs["kitchen"]["auth"])
        ws_h = await self.tabs["hallway"]["b"]._s.ws_connect(self.client.make_url("/api/ws"), headers=self.tabs["hallway"]["auth"])
        await self.tget("hallway", "/api/music")
        got = {"k": [], "h": []}

        async def listen(ws, key):
            async for msg in ws:
                data = json.loads(msg.data)
                if data.get("type") == "music":
                    got[key].append(data)
        tasks = [asyncio.create_task(listen(ws_k, "k")), asyncio.create_task(listen(ws_h, "h"))]
        r = await self.client.post(f"/api/entities/{eid}/command", json={"command": "play_query", "value": "Jump by Van Halen"})
        self.assertEqual(r.status, 200, await r.text())
        self.assertTrue(await wait_for(lambda: got["k"]))
        await asyncio.sleep(0.1)
        self.assertEqual(got["h"], [])
        p = got["k"][-1]["player"]
        self.assertEqual((p["state"], p["track"]["title"], p["track"]["artist"]), ("playing", "Jump", "Van Halen"))
        self.assertEqual(p["track"]["stream"], "api/music/stream/s1")
        self.assertEqual(p["track"]["cover"], "api/music/cover/al1")
        self.assertGreater(p["queueLength"], 1)  # more Van Halen after it
        e = self.h.hub.get(eid)
        self.assertEqual((e.state, e.attrs["title"], e.attrs["artist"], e.attrs["duration"]), ("playing", "Jump", "Van Halen", 241))

        # Pause / volume from the device (screen, automations).
        await self.client.post(f"/api/entities/{eid}/command", json={"command": "pause"})
        await self.client.post(f"/api/entities/{eid}/command", json={"command": "set_volume", "value": 35})
        self.assertTrue(await wait_for(lambda: got["k"][-1]["player"]["volume"] == 35))
        self.assertEqual(got["k"][-1]["player"]["state"], "paused")
        self.assertEqual(self.h.hub.get(eid).attrs["volume"], 35)
        for t in tasks:
            t.cancel()
        await ws_k.close()
        await ws_h.close()

    async def test_queue_moves_on_when_a_song_ends(self):
        await self.connect()
        uid = self.tabs["kitchen"]["id"]
        await self.tget("kitchen", "/api/music")
        r = await self.tpost("kitchen", "/api/music/play", {"albumId": "al1"})
        self.assertEqual(r.status, 200, await r.text())
        p = (await r.json())["player"]
        self.assertEqual((p["track"]["id"], p["queueLength"], p["state"]), ("s1", 3, "playing"))
        eid = p["entityId"]
        # Position reports keep the device true; "ended" moves on.
        r = await self.tpost("kitchen", "/api/music/report", {"state": "playing", "position": 120, "trackId": "s1"})
        self.assertEqual(self.h.hub.get(eid).attrs["position"], 120)
        r = await self.tpost("kitchen", "/api/music/report", {"state": "ended", "position": 241, "trackId": "s1"})
        p = await r.json()
        self.assertEqual((p["track"]["id"], p["index"], p["state"]), ("s2", 1, "playing"))
        # A late "ended" for the old song changes nothing.
        p = await (await self.tpost("kitchen", "/api/music/report", {"state": "ended", "trackId": "s1"})).json()
        self.assertEqual(p["track"]["id"], "s2")
        # previous early in a song goes back; late in a song restarts it.
        await self.client.post(f"/api/entities/{eid}/command", json={"command": "previous"})
        self.assertEqual(self.inst.player_json(uid)["track"]["id"], "s1")
        await self.client.post(f"/api/entities/{eid}/command", json={"command": "next"})
        await self.client.post(f"/api/entities/{eid}/command", json={"command": "next"})
        self.assertEqual(self.inst.player_json(uid)["track"]["id"], "s3")
        p = await (await self.tpost("kitchen", "/api/music/report", {"state": "ended", "trackId": "s3"})).json()
        self.assertEqual((p["state"], self.h.hub.get(eid).state), ("idle", "idle"))  # the end of the album
        # Nothing found: a clear answer.
        r = await self.client.post(f"/api/entities/{eid}/command", json={"command": "play_query", "value": "Yellow Submarine"})
        self.assertEqual(r.status, 409)
        self.assertIn("couldn't find", (await r.json())["error"])
        # Playlists and shuffling.
        r = await self.client.post(f"/api/entities/{eid}/command", json={"command": "shuffle", "value": "Road Trip playlist"})
        self.assertEqual(r.status, 200)
        self.assertEqual(self.inst.player_json(uid)["queueLength"], 2)
        # The queue survives a restart (players.json).
        self.assertTrue((self.data / "integrations" / self.inst.id / "players.json").is_file())

    async def test_stream_and_cover_proxy(self):
        await self.connect()
        r = await self.tget("kitchen", "/api/music/stream/s1")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.headers["Content-Type"], "audio/mpeg")
        self.assertEqual(await r.read(), AUDIO)
        r = await self.tabs["kitchen"]["b"].get("/api/music/stream/s1",
                                                headers={**self.tabs["kitchen"]["auth"], "Range": "bytes=1000-"})
        self.assertEqual(r.status, 206)
        self.assertEqual(r.headers["Content-Range"], f"bytes 1000-{len(AUDIO) - 1}/{len(AUDIO)}")
        self.assertEqual(await r.read(), AUDIO[1000:])
        r = await self.tget("kitchen", "/api/music/cover/al1?size=200")
        self.assertEqual((r.status, r.headers["Content-Type"], await r.read()), (200, "image/jpeg", COVER))
        self.assertEqual(self.fake.calls[-1][1]["size"], "200")
        # The page's <audio> can authenticate with ?token= when its cookie is missing.
        tok = self.tabs["kitchen"]["auth"]["Authorization"][7:]
        anon = Browser(self.client)
        r = await anon.get("/api/music/stream/s1")
        self.assertEqual(r.status, 401)
        await r.read()
        r = await anon.get(f"/api/music/stream/s1?token={tok}")
        self.assertEqual((r.status, await r.read()), (200, AUDIO))
        await anon.close()
        r = await self.tget("kitchen", "/api/music/stream/nope")
        self.assertEqual(r.status, 404)

    async def test_browse_and_search(self):
        await self.connect()
        b = await (await self.tget("kitchen", "/api/music/browse")).json()
        self.assertEqual([a["id"] for a in b["albums"]][:2], ["al1", "al2"])  # "recent" was empty: newest
        self.assertEqual([p["name"] for p in b["playlists"]], ["Road Trip", "Dinner"])
        a = await (await self.tget("kitchen", "/api/music/albums")).json()
        self.assertEqual((len(a["albums"]), a["more"]), (5, False))
        s = await (await self.tget("kitchen", "/api/music/search?q=van halen")).json()
        self.assertEqual(s["artists"][0]["name"], "Van Halen")
        self.assertIn("s1", [x["id"] for x in s["songs"]])
        s = await (await self.tget("kitchen", "/api/music/search?q=road")).json()
        self.assertEqual([p["id"] for p in s["playlists"]], ["pl1"])
        # A song tapped in the results plays, then the rest of its album.
        p = (await (await self.tpost("kitchen", "/api/music/play", {"songId": "s2"})).json())["player"]
        self.assertEqual((p["track"]["id"], p["queueLength"]), ("s2", 2))

    async def test_artists_a_to_z(self):
        await self.connect()
        a = await (await self.tget("kitchen", "/api/music/artists")).json()
        self.assertEqual([g["letter"] for g in a["index"]], ["#", "B", "K", "P", "Q", "V"])
        self.assertEqual([x["name"] for x in a["index"][3]["artists"]], ["The Paper Lanterns"])
        self.assertEqual(a["index"][-1]["artists"][0], {"id": "ar1", "name": "Van Halen", "albums": 2, "cover": None})
        await self.tget("kitchen", "/api/music/artists")
        self.assertEqual([c[0] for c in self.fake.calls].count("getArtists"), 1)  # kept, not asked again
        r = await (await self.tget("kitchen", "/api/music/artists/ar1")).json()
        self.assertEqual((r["artist"]["name"], [x["id"] for x in r["albums"]]), ("Van Halen", ["al1", "al2"]))
        self.assertEqual(r["albums"][0]["cover"], "api/music/cover/al1")
        self.assertEqual((await self.tget("kitchen", "/api/music/artists/ar404")).status, 404)
        self.assertEqual((await self.tget("kitchen", "/api/music/artists/..%2Fx")).status, 404)
        # The artist page's Shuffle: every song of theirs.
        p = (await (await self.tpost("kitchen", "/api/music/play", {"artistId": "ar1"})).json())["player"]
        self.assertEqual((p["queueLength"], p["track"]["artist"]), (4, "Van Halen"))

    async def test_renaming_the_tablet_renames_its_player(self):
        await self.connect()
        await self.tget("kitchen", "/api/music")
        uid = self.tabs["kitchen"]["id"]
        eid = self.inst.player_id(uid)
        self.assertEqual(self.h.hub.get(eid).name, "Kitchen tablet music")
        r = await self.client.patch(f"/api/users/{uid}", json={"name": "Pantry wall"})
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual(self.h.hub.get(eid).name, "Pantry wall music")
        self.assertEqual(self.inst.players[uid]["name"], "Pantry wall")  # kept for after a restart
        await self.inst._publish(uid)
        self.assertEqual(self.h.hub.get(eid).name, "Pantry wall music")

    async def player_label(self, eid):
        ents = await (await self.client.get("/api/entities")).json()
        return next(x for x in ents if x["id"] == eid)["stateLabel"]

    async def test_a_sleeping_tablets_player_is_screen_off_not_offline(self):
        await self.connect()
        await self.tget("kitchen", "/api/music")
        uid = self.tabs["kitchen"]["id"]
        eid = self.inst.player_id(uid)
        self.assertEqual(await self.player_label(eid), "Stopped")
        battery = {"level": 60, "charging": False, "plugged": "none"}

        # The page goes quiet and the tablet says nothing about sleeping: offline, as before.
        self.inst.players[uid]["at"] -= 4 * 60
        await self.inst.check_offline()
        self.assertFalse(self.h.hub.get(eid).available)

        # The tablet's screen went off and it still checks in: "Screen off", available.
        r = await self.tpost("kitchen", "/api/tablet/battery",
                             {**battery, "screen": "off", "background": True, "nextReportIn": 900})
        self.assertEqual(r.status, 204)
        await self.inst.check_offline()
        e = self.h.hub.get(eid)
        self.assertEqual((e.available, e.state, e.attrs["screen"]), (True, "idle", "off"))
        self.assertEqual(await self.player_label(eid), "Screen off")
        # Nothing to play it on until it wakes (pausing / stopping / volume still go through).
        r = await self.client.post(f"/api/entities/{eid}/command", json={"command": "play"})
        self.assertNotEqual(r.status, 200)
        self.assertIn("screen is off", await r.text())
        r = await self.client.post(f"/api/entities/{eid}/command", json={"command": "set_volume", "value": 20})
        self.assertEqual(r.status, 200, await r.text())

        # The tablet itself goes offline (no check-in for too long - on battery, deep sleep may hold its
        # check-ins back for up to 8 hours): so does its player.
        tablets = next(i for i in self.h.integrations.instances.values() if i.type == "tablets")
        await tablets.check_offline(now=time.time() + 40 * 60)
        await self.inst.check_offline()
        self.assertTrue(self.h.hub.get(eid).available)
        await tablets.check_offline(now=time.time() + 8.5 * 3600)
        await self.inst.check_offline()
        self.assertFalse(self.h.hub.get(eid).available)

        # Woken: the page reports again - on, its usual label.
        await self.tpost("kitchen", "/api/tablet/battery", {**battery, "screen": "on", "background": True})
        await self.tpost("kitchen", "/api/music/report", {})
        e = self.h.hub.get(eid)
        self.assertEqual((e.available, e.attrs["screen"]), (True, "on"))
        self.assertEqual(await self.player_label(eid), "Stopped")

    async def test_voice_plays_on_this_tablet(self):
        await self.connect()
        await self.tget("kitchen", "/api/music")

        async def say(text):
            r = await self.tpost("kitchen", "/api/voice/utterance?speak=0", {"text": text})
            self.assertEqual(r.status, 200, await r.text())
            return await r.json()
        out = await say("Hey Newman, play Jump by Van Halen")
        self.assertEqual(out["reply"], "Playing Jump by Van Halen.")
        eid = self.inst.player_id(self.tabs["kitchen"]["id"])
        self.assertEqual((self.h.hub.get(eid).state, self.h.hub.get(eid).attrs["title"]), ("playing", "Jump"))
        self.assertEqual((await say("what's playing"))["reply"], "This is Jump by Van Halen.")
        out = await say("next song")
        self.assertNotEqual(self.h.hub.get(eid).attrs["title"], "Jump")
        self.assertTrue(out["reply"].endswith("by Van Halen."))
        self.assertEqual((await say("set the volume to 30"))["reply"], "Volume 30 percent.")
        self.assertEqual((await say("pause the music"))["reply"], "Paused.")
        self.assertEqual(self.h.hub.get(eid).state, "paused")
        self.assertEqual((await say("play the album 1984"))["reply"], "Playing 1984 by Van Halen.")
        self.assertEqual((await say("shuffle my road trip playlist"))["reply"], "Shuffling your Road Trip playlist.")
        self.assertEqual((await say("play Stand by Me"))["reply"], "Playing Stand by Me by Ben E. King.")
        self.assertEqual((await say("play Yellow Submarine by the Beatles"))["reply"],
                         "I couldn't find yellow submarine by the beatles.")
        # The hallway tablet's player was never touched.
        self.assertNotIn(self.tabs["hallway"]["id"], self.inst.players)

    async def test_album_and_playlist_pages(self):
        await self.connect()
        r = await (await self.tget("kitchen", "/api/music/albums/al1")).json()
        self.assertEqual((r["album"]["name"], r["album"]["artist"], r["album"]["songs"], r["album"]["duration"]),
                         ("1984", "Van Halen", 3, 641))
        self.assertEqual([(s["id"], s["track"], s["duration"]) for s in r["songs"]], [("s1", 1, 241), ("s2", 2, 200), ("s3", 3, 200)])
        self.assertEqual((await self.tget("kitchen", "/api/music/albums/nope")).status, 404)
        self.assertEqual((await self.tget("kitchen", "/api/music/albums/..%2Fx")).status, 404)
        r = await (await self.tget("kitchen", "/api/music/playlists/pl1")).json()
        self.assertEqual((r["playlist"]["name"], [s["id"] for s in r["songs"]]), ("Road Trip", ["s2", "s7"]))
        self.assertEqual((await self.tget("kitchen", "/api/music/playlists/nope")).status, 404)
        # A song tapped on the album's page: the whole album queued, playing from that song.
        p = (await (await self.tpost("kitchen", "/api/music/play", {"albumId": "al1", "startSongId": "s2"})).json())["player"]
        self.assertEqual((p["track"]["id"], p["index"], p["queueLength"]), ("s2", 1, 3))
        p = (await (await self.tpost("kitchen", "/api/music/play", {"albumId": "al1", "index": 2})).json())["player"]
        self.assertEqual((p["track"]["id"], p["index"]), ("s3", 2))
        p = (await (await self.tpost("kitchen", "/api/music/play", {"playlistId": "pl1", "startSongId": "s7"})).json())["player"]
        self.assertEqual((p["track"]["id"], p["queueLength"]), ("s7", 2))
        r = await self.tpost("kitchen", "/api/music/play", {"albumId": "al1", "startSongId": "s7"})
        self.assertEqual(r.status, 404)  # not on that album
        r = await self.tpost("kitchen", "/api/music/play", {"albumId": "al1", "index": "two"})
        self.assertEqual(r.status, 400)
        # The full results (the keyboard's Search key) ask Navidrome for more of everything.
        await self.tget("kitchen", "/api/music/search?q=van&full=1")
        self.assertEqual(self.fake.calls[-2][1]["songCount"], "100")


# -- voice with a misheard name: the library is matched by sound -----------------------------------

FUZZY_SONGS = [
    {**song("f1", "Bed of Roses", "Bon Jovi", "Harbor Lights", "fa1", 1), "artistId": "fr1"},
    {**song("f2", "Lanterns at Dusk", "Bon Jovi", "Harbor Lights", "fa1", 2), "artistId": "fr1"},
    {**song("f3", "Better Days Ahead", "The Copper Kites", "Paper Weather", "fa2", 1), "artistId": "fr2"},
    {**song("f4", "Bitter Harbor", "The Copper Kites", "Paper Weather", "fa2", 2), "artistId": "fr2"},
    {**song("f5", "Glass Harbor", "Velvet Engines", "Glass Harbor", "fa3", 1), "artistId": "fr3"},
    {**song("f6", "Paper Moons", "Velvet Engines", "Glass Harbor", "fa3", 2), "artistId": "fr3"},
    {**song("f7", "Saffron Parade", "Amber Owls", "Night Amber", "fa4", 1), "artistId": "fr4"},
]
FUZZY_LIBRARY = {
    "songs": FUZZY_SONGS,
    "albums": [{"id": "fa1", "name": "Harbor Lights", "artist": "Bon Jovi", "artistId": "fr1", "coverArt": "fa1"},
               {"id": "fa2", "name": "Paper Weather", "artist": "The Copper Kites", "artistId": "fr2", "coverArt": "fa2"},
               {"id": "fa3", "name": "Glass Harbor", "artist": "Velvet Engines", "artistId": "fr3", "coverArt": "fa3"},
               {"id": "fa4", "name": "Night Amber", "artist": "Amber Owls", "artistId": "fr4", "coverArt": "fa4"}],
    "artists": [{"id": "fr1", "name": "Bon Jovi", "albumCount": 1}, {"id": "fr2", "name": "The Copper Kites", "albumCount": 1},
                {"id": "fr3", "name": "Velvet Engines", "albumCount": 1}, {"id": "fr4", "name": "Amber Owls", "albumCount": 1}],
    "playlists": [], "playlist_songs": {},
}


class MisheardVoiceTests(unittest.IsolatedAsyncioTestCase):
    LIBRARY = FUZZY_LIBRARY
    asyncTearDown = NavidromeHubTests.asyncTearDown
    connect = NavidromeHubTests.connect
    tget = NavidromeHubTests.tget
    tpost = NavidromeHubTests.tpost

    async def say(self, text):
        r = await self.tpost("kitchen", "/api/voice/utterance?speak=0", {"text": text})
        self.assertEqual(r.status, 200, await r.text())
        return (await r.json())["reply"]

    def now(self):
        p = self.inst.player_json(self.tabs["kitchen"]["id"])
        return (p["state"], p["track"]["title"] if p["track"] else None)

    async def asyncSetUp(self):  # the hub tests' set-up, then the kitchen tablet's player
        await NavidromeHubTests.asyncSetUp(self)
        await self.connect()
        await self.tget("kitchen", "/api/music")

    async def test_misheard_names_still_play(self):
        self.assertEqual(await self.say("Played Bed of Roses"), "Playing Bed of Roses by Bon Jovi.")
        self.assertEqual(await self.say("pause the music"), "Paused.")
        self.assertEqual(await self.say("play Better Roses"), "Playing Bed of Roses by Bon Jovi.")
        self.assertEqual(self.now(), ("playing", "Bed of Roses"))
        q = self.inst.player_json(self.tabs["kitchen"]["id"])
        self.assertEqual(q["upNext"][0]["title"], "Lanterns at Dusk")  # then more by them
        self.assertEqual(await self.say("can you play better roses by bon jovi"), "Playing Bed of Roses by Bon Jovi.")
        self.assertEqual(await self.say("play bon jovee"), "Playing Bon Jovi.")

    async def test_album_or_song_of_the_same_name(self):
        self.assertEqual(await self.say("play the album glass harbour"), "Playing Glass Harbor by Velvet Engines.")
        self.assertEqual(self.inst.player_json(self.tabs["kitchen"]["id"])["queueLength"], 2)
        self.assertEqual(await self.say("play the song glass harbour"), "Playing Glass Harbor by Velvet Engines.")
        self.assertEqual(self.now(), ("playing", "Glass Harbor"))

    async def test_not_sure_asks_first(self):
        self.assertEqual(await self.say("play red roses"), "Did you mean Bed of Roses by Bon Jovi?")
        self.assertEqual(self.now(), ("idle", None))  # nothing played yet
        self.assertEqual(await self.say("yes please"), "Playing Bed of Roses by Bon Jovi.")
        self.assertEqual(self.now(), ("playing", "Bed of Roses"))
        self.assertEqual(await self.say("yes"), "Sorry, I didn't understand that.")  # answered already
        await self.say("pause")
        self.assertEqual(await self.say("play red roses"), "Did you mean Bed of Roses by Bon Jovi?")
        self.assertEqual(await self.say("no"), "Okay, never mind.")
        self.assertEqual(self.now(), ("paused", "Bed of Roses"))

    async def test_unrelated_words_find_nothing(self):
        self.assertEqual(await self.say("play the doorbell camera"), "I couldn't find the doorbell camera.")
        self.assertEqual(await self.say("play yellow submarine by the beatles"),
                         "I couldn't find yellow submarine by the beatles.")
        self.assertEqual(self.now(), ("idle", None))


class MusicPutBackTests(unittest.IsolatedAsyncioTestCase):
    """An automation plays music for a while, then puts the player back as it was."""
    asyncSetUp = NavidromeHubTests.asyncSetUp
    asyncTearDown = NavidromeHubTests.asyncTearDown
    connect = NavidromeHubTests.connect
    tget = NavidromeHubTests.tget
    tpost = NavidromeHubTests.tpost

    async def ready(self):
        await self.connect()
        self.uid = self.tabs["kitchen"]["id"]
        await self.tget("kitchen", "/api/music")
        self.eid = self.inst.player_id(self.uid)

    def p(self):
        return self.inst.players[self.uid]

    async def cmd(self, command, value=None):
        r = await self.client.post(f"/api/entities/{self.eid}/command", json={"command": command, "value": value})
        self.assertEqual(r.status, 200, await r.text())

    async def album_at_panama(self, state="playing", volume=40):
        """Before: the 1984 album, 1:23 into Panama (song 2)."""
        await self.tpost("kitchen", "/api/music/play", {"albumId": "al1"})
        await self.cmd("next")
        await self.tpost("kitchen", "/api/music/report", {"state": "playing", "position": 83, "trackId": "s2"})
        await self.cmd("set_volume", volume)
        if state == "paused":
            await self.cmd("pause")
        self.p()["position"] = 83  # (exactly - not the few ms since the report)

    async def run_auto(self, extra=()):
        auto = self.h.engine.save({
            "name": "Music for a while", "notify": {"enabled": False},
            "trigger": {"type": "event", "entity": self.eid, "event": "pressed"},
            "actions": [{"entity": self.eid, "command": "play_query", "value": "Bohemian Rhapsody",
                         "for": {"amount": 5, "unit": "minutes"}}, *extra]})
        run = await self.h.engine.trigger(auto, {"type": "event", "entity": self.eid, "event": "pressed"}, force=True)
        self.assertEqual(self.inst.current(self.p())["title"], "Bohemian Rhapsody")
        self.assertIn(f"Will put {self.h.hub.name(self.eid)} back in 5 minutes", [x["text"] for x in run["log"]])
        return run

    async def time_up(self, engine=None):
        engine = engine or self.h.engine
        for key, hold in list(engine._holds.items()):
            hold["task"].cancel()
            await engine._restore_later(key, 0)

    def last_log(self, run):
        return self.h.store.run(run["id"])["log"][-1]["text"]

    def queue_ids(self):
        return [t["id"] for t in self.p()["queue"]]

    async def test_nothing_playing_before_stops_after(self):
        await self.ready()
        run = await self.run_auto()
        await self.time_up()
        self.assertEqual((self.p()["state"], self.p()["queue"]), ("idle", []))
        self.assertEqual(self.h.hub.get(self.eid).state, "idle")
        self.assertEqual(self.last_log(run), "Put back: nothing playing (volume 60)")
        self.assertEqual(self.h.store.holds(), {})

    async def test_paused_before_comes_back_paused_at_the_same_spot(self):
        await self.ready()
        await self.album_at_panama("paused")
        run = await self.run_auto()
        self.assertEqual(self.p()["state"], "playing")
        await self.time_up()
        p = self.p()
        self.assertEqual((self.queue_ids(), p["index"], p["position"], p["state"]), (["s1", "s2", "s3"], 1, 83, "paused"))
        self.assertEqual(self.last_log(run), "Put back: Van Halen – Panama at 1:23, paused (volume 40)")

    async def test_playing_before_resumes_at_the_right_spot_and_volume(self):
        await self.ready()
        await self.album_at_panama("playing", volume=40)
        run = await self.run_auto([{"entity": self.eid, "command": "set_volume", "value": 80,
                                    "for": {"amount": 5, "unit": "minutes"}}])
        self.assertEqual((self.p()["volume"], len(self.h.engine._holds)), (80, 1))
        sent = []
        self.inst.ctx.push_to_user = lambda uid, msg: _append(sent, msg)
        await self.time_up()
        p = self.p()
        self.assertEqual((self.queue_ids(), p["index"], p["position"], p["state"], p["volume"]),
                         (["s1", "s2", "s3"], 1, 83, "playing", 40))
        self.assertEqual(sent[-1]["seek"], 83)  # the tablet jumps back to that spot
        self.assertEqual(sent[-1]["player"]["track"]["title"], "Panama")
        self.assertEqual(self.last_log(run), "Put back: Van Halen – Panama at 1:23, playing (volume 40)")

    async def test_music_changed_meanwhile_is_left_alone(self):
        await self.ready()
        await self.album_at_panama("playing")
        run = await self.run_auto()
        await self.tpost("kitchen", "/api/music/play", {"albumId": "al3"})  # someone put on other music
        await self.time_up()
        self.assertEqual((self.queue_ids(), self.p()["state"]), (["s5"], "playing"))
        self.assertEqual(self.last_log(run), "Left the music alone: it was changed during the 5 minutes")
        # Paused by someone counts as changed too; skipping within the automation's music doesn't.
        run = await self.run_auto()
        await self.cmd("pause")
        await self.time_up()
        self.assertEqual((self.inst.current(self.p())["title"], self.p()["state"]), ("Bohemian Rhapsody", "paused"))
        self.assertIn("Left the music alone", self.last_log(run))

    async def test_restart_during_the_minutes_finishes_the_put_back(self):
        await self.ready()
        await self.album_at_panama("paused")
        await self.run_auto()
        held = list(self.h.store.holds().values())
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["snap"]["position"], 83)  # kept with the timer
        for hold in self.h.engine._holds.values():
            hold["task"].cancel()
        # Newman restarts (the connection too: the player comes back from players.json)...
        await self.h.integrations.update(self.inst.id, name="Music")
        self.inst = self.h.integrations.instances[self.inst.id]
        self.assertEqual(self.inst.current(self.p())["title"], "Bohemian Rhapsody")
        key = next(iter(self.h.store.holds()))
        self.h.store.save_hold(key, {**self.h.store.holds()[key], "due": 0})  # ...after the time was up
        engine = Engine(self.h.hub, self.h.store, self.h.notifier, self.h.integrations)
        engine.resume_holds()
        self.assertTrue(await wait_for(lambda: self.queue_ids() == ["s1", "s2", "s3"]))
        p = self.p()
        self.assertEqual((p["index"], p["position"], p["state"]), (1, 83, "paused"))
        self.assertEqual(self.h.store.holds(), {})


async def _append(lst, item):
    lst.append(item)