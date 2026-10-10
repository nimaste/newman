"""Music routes for the wall tablets (see API.md → Music). Registered by api.build_app.

    GET  /api/music               is music set up? + this wall tablet's player (made on first visit)
    POST /api/music/report        the wall page: what its <audio> is doing (position, paused, ended)
    POST /api/music/play          play an album / playlist / song / artist the Music tile shows (an album or
                                  playlist from one of its songs: startSongId), or {query}
    GET  /api/music/browse        a few recent albums and the playlists, for the Music tile
    GET  /api/music/albums        every album A-Z, 60 at a time (?offset=), for the full-screen picker
    GET  /api/music/albums/{id}   one album and its songs (the picker's album page)
    GET  /api/music/playlists/{id} one playlist and its songs (the same page)
    GET  /api/music/artists       every artist A-Z in Navidrome's letter groups, for the picker's Artists tab
    GET  /api/music/artists/{id}  one artist and their albums
    GET  /api/music/search?q=     artists, albums, songs and playlists matching (&full=1: many more of each)
    GET  /api/music/stream/{id}   the song's audio, from Navidrome (the hub adds its token; Range works)
    GET  /api/music/cover/{id}    its cover art (?size=)
    GET  /api/music/lyrics/{id}   the song's lyrics: {source, synced, lines: [{t, text}], text} (lyrics.py)

The music connection (integrations/navidrome.py) keeps each tablet's queue; commands go through the
player device like any other device's (POST /api/entities/{id}/command).
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import aiohttp
from aiohttp import web

from .integrations.base import CommandError
from .integrations.navidrome import NavidromeIntegration, SubsonicError

if TYPE_CHECKING:
    from .app import Newman

log = logging.getLogger(__name__)

ITEM_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,100}$")
PASS_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified", "ETag")


def music_of(h: "Newman") -> NavidromeIntegration | None:
    return next((i for i in h.integrations.instances.values() if isinstance(i, NavidromeIntegration)), None)


def _err(status: int, msg: str) -> web.Response:
    return web.json_response({"error": msg}, status=status)


def cover_url(item: dict[str, Any]) -> str | None:
    c = item.get("coverArt")
    return f"api/music/cover/{c}" if c else None


def album_json(a: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(a.get("id")), "name": str(a.get("name") or a.get("title") or ""), "artist": a.get("artist") or "",
            "year": a.get("year"), "songs": a.get("songCount"), "cover": cover_url(a)}


def playlist_json(p: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(p.get("id")), "name": str(p.get("name") or ""), "songs": p.get("songCount"), "cover": cover_url(p)}


def song_json(s: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(s.get("id")), "title": str(s.get("title") or ""), "artist": s.get("artist") or "",
            "album": s.get("album") or "", "duration": s.get("duration"), "cover": cover_url(s)}


def artist_json(a: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(a.get("id")), "name": str(a.get("name") or ""), "albums": a.get("albumCount"), "cover": cover_url(a)}


def add_music_routes(r: web.RouteTableDef, h: "Newman", *, body: Callable[..., Awaitable[dict[str, Any]]]) -> None:

    def music() -> NavidromeIntegration:
        m = music_of(h)
        if not m:
            raise web.HTTPNotFound(text='{"error": "Music isn\'t set up - add the Navidrome connection"}',
                                   content_type="application/json")
        return m

    def connected() -> NavidromeIntegration:
        m = music()
        if not m.client:
            raise web.HTTPServiceUnavailable(text='{"error": "Navidrome isn\'t connected"}', content_type="application/json")
        return m

    def tablet_uid(req: web.Request) -> str:
        if req["session"].get("kind") != "tablet":
            raise web.HTTPForbidden(text='{"error": "Only a wall tablet plays music"}', content_type="application/json")
        return req["user"]["id"]

    @r.get("/api/music")
    async def info(req: web.Request) -> web.Response:
        m = music_of(h)
        if not m:
            return web.json_response({"available": False, "player": None})
        player = None
        if req["session"].get("kind") == "tablet":
            player = await m.report(req["user"]["id"], req["user"]["name"], {})
        return web.json_response({"available": m.client is not None, "status": m.status, "detail": m.detail,
                                  "player": player})

    @r.post("/api/music/report")
    async def report(req: web.Request) -> web.Response:
        uid = tablet_uid(req)
        data = await body(req)
        if data.get("state") not in (None, "playing", "paused", "idle", "ended", "error"):
            return _err(400, "state is playing, paused, idle, ended or error")
        return web.json_response(await music().report(uid, req["user"]["name"], data))

    @r.post("/api/music/play")
    async def play(req: web.Request) -> web.Response:
        uid = tablet_uid(req)
        m = connected()
        data = await body(req)
        m.ensure_player(uid, req["user"]["name"])
        try:
            if data.get("query"):
                say = await m.play_request(uid, {"query": str(data["query"])[:200], "shuffle": bool(data.get("shuffle"))})
            else:
                ids = {k: str(data.get(k + "Id") or "") for k in ("album", "playlist", "song", "artist")}
                if not any(ids.values()) or not all(ITEM_ID.match(v) for v in ids.values() if v):
                    return _err(400, "Send albumId, playlistId, songId, artistId or query")
                # An album / playlist from a song on its page: startSongId (or index, from 0).
                start_song = str(data.get("startSongId") or "")
                start = data.get("index")
                if start_song and not ITEM_ID.match(start_song) or \
                        start is not None and (not isinstance(start, int) or isinstance(start, bool) or start < 0):
                    return _err(400, "startSongId is a song id; index is a number from 0")
                say = await m.play_ids(uid, shuffle=bool(data.get("shuffle")), start_song=start_song, start=start, **ids)
        except CommandError as e:
            return _err(404, str(e))
        except (SubsonicError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            return _err(502, f"Navidrome didn't answer: {e}")
        return web.json_response({"ok": True, "say": say, "player": m.player_json(uid)})

    @r.get("/api/music/browse")
    async def browse(_: web.Request) -> web.Response:
        m = connected()
        try:
            albums = await m.recent_albums(18)
            playlists = await m.playlists()
        except (SubsonicError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            return _err(502, f"Navidrome didn't answer: {e}")
        return web.json_response({"albums": [album_json(a) for a in albums],
                                  "playlists": [playlist_json(p) for p in playlists[:60]]})

    @r.get("/api/music/albums")
    async def albums(req: web.Request) -> web.Response:
        """Every album, A to Z, 60 at a time (?offset=) - the full-screen picker."""
        m = connected()
        try:
            offset = max(0, int(req.query.get("offset") or 0))
        except ValueError:
            offset = 0
        try:
            got = ((await m.need_client().call("getAlbumList2", type="alphabeticalByName", size=60, offset=offset))
                   .get("albumList2") or {}).get("album") or []
        except (SubsonicError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            return _err(502, f"Navidrome didn't answer: {e}")
        return web.json_response({"albums": [album_json(a) for a in got], "more": len(got) == 60})

    async def with_songs(kind: str, iid: str) -> web.Response:
        """An album's or playlist's page: about it, then its songs in order."""
        if not ITEM_ID.match(iid):
            raise web.HTTPNotFound()
        m = connected()
        try:
            got = await (m.album(iid) if kind == "album" else m.playlist(iid))
        except SubsonicError as e:
            return _err(404 if e.code == 70 else 502, f"Navidrome didn't answer: {e}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return _err(502, f"Navidrome didn't answer: {e}")
        if not got:
            return _err(404, f"Navidrome doesn't have that {kind}")
        songs = list(got.get("song" if kind == "album" else "entry") or [])
        about = album_json(got) if kind == "album" else playlist_json(got)
        about["songs"] = len(songs)
        about["duration"] = int(got.get("duration") or sum(int(s.get("duration") or 0) for s in songs))
        if kind == "album":
            about["artistId"] = str(got["artistId"]) if got.get("artistId") else None
        return web.json_response({kind: about, "songs": [{**song_json(s), "track": s.get("track"), "disc": s.get("discNumber")}
                                                         for s in songs]})

    @r.get("/api/music/albums/{alid}")
    async def album(req: web.Request) -> web.Response:
        """One album: cover, name, artist, year, length, and its songs - play from one with
        POST /api/music/play {albumId, startSongId}."""
        return await with_songs("album", req.match_info["alid"])

    @r.get("/api/music/playlists/{plid}")
    async def playlist(req: web.Request) -> web.Response:
        """One playlist and its songs (the same page as an album's)."""
        return await with_songs("playlist", req.match_info["plid"])

    @r.get("/api/music/artists")
    async def artists(_: web.Request) -> web.Response:
        """Every artist A to Z, grouped by letter as Navidrome groups them - the picker's Artists tab."""
        m = connected()
        try:
            index = await m.artist_index()
        except (SubsonicError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            return _err(502, f"Navidrome didn't answer: {e}")
        return web.json_response({"index": [
            {"letter": str(i.get("name") or ""), "artists": [artist_json(a) for a in i.get("artist") or []]}
            for i in index if i.get("artist")]})

    @r.get("/api/music/artists/{aid}")
    async def artist(req: web.Request) -> web.Response:
        """One artist: their albums (cover grid) - shuffle them all with POST /api/music/play {artistId}."""
        aid = req.match_info["aid"]
        if not ITEM_ID.match(aid):
            raise web.HTTPNotFound()
        m = connected()
        try:
            a = await m.artist(aid)
        except SubsonicError as e:
            return _err(404 if e.code == 70 else 502, f"Navidrome didn't answer: {e}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return _err(502, f"Navidrome didn't answer: {e}")
        if not a:
            return _err(404, "Navidrome doesn't have that artist")
        return web.json_response({"artist": artist_json(a), "albums": [album_json(x) for x in a.get("album") or []]})

    @r.get("/api/music/search")
    async def search(req: web.Request) -> web.Response:
        q = (req.query.get("q") or "").strip()[:100]
        if not q:
            return web.json_response({"artists": [], "albums": [], "songs": [], "playlists": []})
        full = req.query.get("full") in ("1", "true")  # the picker's full results (Search on the keyboard)
        m = connected()
        try:
            found = await (m.search(q, songs=100, albums=50, artists=30) if full else
                           m.search(q, songs=20, albums=12, artists=6))
            lists = [p for p in await m.playlists() if q.lower() in str(p.get("name") or "").lower()]
        except (SubsonicError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            return _err(502, f"Navidrome didn't answer: {e}")
        return web.json_response({"artists": [artist_json(a) for a in found["artist"]],
                                  "albums": [album_json(a) for a in found["album"]],
                                  "songs": [song_json(s) for s in found["song"]],
                                  "playlists": [playlist_json(p) for p in lists[:60 if full else 6]]})

    async def proxy(req: web.Request, name: str, cache: str, **params: Any) -> web.StreamResponse:
        """Navidrome's answer passed through as it comes (bytes, type, length, ranges) - the token stays here."""
        m = connected()
        headers = {k: req.headers[k] for k in ("Range", "If-Range") if k in req.headers}
        try:
            up = await m.client.open(name, headers=headers, **params)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            return _err(502, f"Can't reach Navidrome ({e.__class__.__name__})")
        try:
            ctype = up.headers.get("Content-Type", "")
            if up.status >= 400 or ctype.startswith(("application/json", "text/xml", "application/xml")):
                return _err(404 if up.status < 500 else 502, "Navidrome doesn't have that")
            resp = web.StreamResponse(status=up.status)
            for k in PASS_HEADERS:
                if k in up.headers:
                    resp.headers[k] = up.headers[k]
            resp.headers["Cache-Control"] = cache
            await resp.prepare(req)
            try:
                async for chunk in up.content.iter_chunked(64 * 1024):
                    await resp.write(chunk)
                await resp.write_eof()
            except (ConnectionResetError, aiohttp.ClientError, asyncio.CancelledError):
                pass  # the tablet skipped ahead or closed the page
            return resp
        finally:
            up.release()

    @r.get("/api/music/lyrics/{sid}")
    async def lyrics(req: web.Request) -> web.Response:
        """Navidrome's lyrics, else LRCLIB's (when allowed) - synced lines in seconds, or plain text."""
        sid = req.match_info["sid"]
        if not ITEM_ID.match(sid):
            raise web.HTTPNotFound()
        m = connected()
        try:
            return web.json_response(await m.lyrics.get(sid))
        except CommandError as e:
            return _err(503, str(e))
        except (SubsonicError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            return _err(502, f"Navidrome didn't answer: {e}")

    @r.get("/api/music/stream/{sid}")
    async def stream(req: web.Request) -> web.StreamResponse:
        sid = req.match_info["sid"]
        if not ITEM_ID.match(sid):
            raise web.HTTPNotFound()
        return await proxy(req, "stream", "private, max-age=3600", id=sid)

    @r.get("/api/music/cover/{cid}")
    async def cover(req: web.Request) -> web.StreamResponse:
        cid = req.match_info["cid"]
        if not ITEM_ID.match(cid):
            raise web.HTTPNotFound()
        try:
            size = max(32, min(1200, int(req.query.get("size") or 300)))
        except ValueError:
            size = 300
        return await proxy(req, "getCoverArt", "private, max-age=86400", id=cid, size=size)
