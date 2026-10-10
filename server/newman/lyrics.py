"""Song lyrics for the wall tablets' player (GET /api/music/lyrics/<songId>, see music.py).

Where they come from, in order:
  1. Navidrome itself: OpenSubsonic getLyricsBySongId (structured, often synced - from the files'
     tags or .lrc files next to them), then the classic getLyrics?artist=&title= (plain text).
  2. LRCLIB (lrclib.net, a free public lyrics database) - only when the Navidrome connection's
     "Look up missing lyrics online" setting is on (the default). Only the song's artist, title,
     album and length are sent.

LRC text ("[01:02.34]words", several stamps on one line, an [offset:+250] tag) is turned into
[{t: seconds, text}] here, so the wall page only has to follow the clock. Answers are kept per song,
in memory and on disk (integrations/<id>/lyrics/), "none found" included, for a while.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiohttp

log = logging.getLogger(__name__)

LRCLIB_URL = "https://lrclib.net"
USER_AGENT = "Newman-home-hub/1.0"
FOUND_KEEP_S = 30 * 86400      # lyrics don't change
NONE_KEEP_S = 6 * 3600         # "none found": ask again after a few hours
FAILED_KEEP_S = 10 * 60        # LRCLIB unreachable: not remembered on disk, retried after this
DISK_MAX = 3000                # songs kept on disk (oldest dropped)

STAMP = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
TAG = re.compile(r"^\[([a-zA-Z#]+):([^\]]*)\]\s*$")
WORD_STAMP = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")  # "enhanced" LRC: per-word times


def none() -> dict[str, Any]:
    return {"source": None, "synced": False, "lines": [], "text": ""}


def _seconds(m: re.Match) -> float:
    frac = m.group(3) or "0"
    return int(m.group(1)) * 60 + int(m.group(2)) + int(frac) / (10 ** len(frac))


def parse_lrc(text: str) -> tuple[list[dict[str, Any]], str]:
    """LRC text -> (lines [{t, text}] sorted by time, plain text). Lines with no time stamp are only
    in the plain text; a stamped blank line stays (a pause in the singing). [offset:+ms] makes the
    lyrics come that much sooner (the LRC convention)."""
    offset = 0.0
    lines: list[tuple[float, int, str]] = []
    plain: list[str] = []
    for n, raw in enumerate((text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")):
        raw = raw.strip()
        tag = TAG.match(raw)
        if tag and not STAMP.match(raw):
            if tag.group(1).lower() == "offset":
                try:
                    offset = float(tag.group(2).strip()) / 1000
                except ValueError:
                    pass
            continue
        stamps = []
        rest = raw
        while True:
            m = STAMP.match(rest)
            if not m:
                break
            stamps.append(_seconds(m))
            rest = rest[m.end():].lstrip()
        words = re.sub(r"\s{2,}", " ", WORD_STAMP.sub("", rest)).strip()
        for t in stamps:
            lines.append((t, n, words))
        if words or (plain and plain[-1]):
            plain.append(words)
    out = [{"t": round(max(0.0, t - offset), 3), "text": w} for t, _, w in sorted(lines)]
    return out, "\n".join(plain).strip()


def from_lrc(text: str, source: str) -> dict[str, Any] | None:
    lines, plain = parse_lrc(text)
    if lines and any(x["text"] for x in lines):
        return {"source": source, "synced": True, "lines": lines, "text": plain}
    if plain:
        return {"source": source, "synced": False, "lines": [], "text": plain}
    return None


def from_structured(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """OpenSubsonic structuredLyrics -> ours (synced ones first)."""
    items = [x for x in items or [] if isinstance(x, dict) and x.get("line")]
    for want_synced in (True, False):
        for x in items:
            if bool(x.get("synced")) != want_synced:
                continue
            rows = [r for r in x["line"] if isinstance(r, dict)]
            text = "\n".join(str(r.get("value") or "").strip() for r in rows).strip()
            if want_synced:
                off = float(x.get("offset") or 0) / 1000
                lines = sorted(({"t": round(max(0.0, float(r.get("start") or 0) / 1000 - off), 3),
                                 "text": str(r.get("value") or "").strip()} for r in rows if r.get("start") is not None),
                               key=lambda r: r["t"])
                if any(r["text"] for r in lines):
                    return {"source": "navidrome", "synced": True, "lines": lines, "text": text}
            elif text:
                # Some servers hand back raw LRC as "unsynced" lines.
                if STAMP.search(text):
                    got = from_lrc(text, "navidrome")
                    if got:
                        return got
                return {"source": "navidrome", "synced": False, "lines": [], "text": text}
    return None


class LyricsFinder:
    """Per Navidrome connection: finds a song's lyrics and remembers the answer.

    `call(name, **params)` is the Subsonic client's call; `online()` says whether LRCLIB may be asked."""

    def __init__(self, cache_dir: Path, call: Callable[..., Awaitable[dict[str, Any]]],
                 online: Callable[[], bool]) -> None:
        self.dir = cache_dir
        self.call = call
        self.online = online
        self.mem: dict[str, tuple[float, dict[str, Any], bool]] = {}  # song id -> (expires, answer, lrclib on)
        self._busy: dict[str, asyncio.Future] = {}

    # -- the cache ------------------------------------------------------------------------------

    def _file(self, sid: str) -> Path:
        return self.dir / (re.sub(r"[^A-Za-z0-9_.\-]", "_", sid)[:100] + ".json")

    def _usable(self, answer: dict[str, Any], asked_online: bool) -> bool:
        if self.online():
            return answer.get("source") is not None or asked_online  # a "none" from before LRCLIB was on: ask again
        return answer.get("source") != "lrclib"                     # LRCLIB turned off: don't show its lyrics

    def cached(self, sid: str) -> dict[str, Any] | None:
        now = time.time()
        hit = self.mem.get(sid)
        if hit and hit[0] > now and self._usable(hit[1], hit[2]):
            return hit[1]
        try:
            d = json.loads(self._file(sid).read_text())
            if d["expires"] > now and self._usable(d["answer"], d.get("online", False)):
                self.mem[sid] = (d["expires"], d["answer"], d.get("online", False))
                return d["answer"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def _keep(self, sid: str, answer: dict[str, Any], keep_s: float, asked_online: bool, disk: bool = True) -> None:
        expires = time.time() + keep_s
        self.mem[sid] = (expires, answer, asked_online)
        if len(self.mem) > 500:
            for k in sorted(self.mem, key=lambda k: self.mem[k][0])[:100]:
                self.mem.pop(k, None)
        if not disk:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            f = self._file(sid)
            tmp = f.with_suffix(".part")
            tmp.write_text(json.dumps({"expires": expires, "online": asked_online, "answer": answer}))
            tmp.replace(f)
            files = list(self.dir.glob("*.json"))
            if len(files) > DISK_MAX:
                for old in sorted(files, key=lambda p: p.stat().st_mtime)[:len(files) - DISK_MAX]:
                    old.unlink(missing_ok=True)
        except OSError:
            log.debug("Couldn't keep lyrics on disk", exc_info=True)

    # -- finding them ---------------------------------------------------------------------------

    async def get(self, sid: str) -> dict[str, Any]:
        hit = self.cached(sid)
        if hit is not None:
            return hit
        if sid in self._busy:  # the same song asked twice at once (two tablets): one lookup
            return await asyncio.shield(self._busy[sid])
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._busy[sid] = fut
        try:
            answer = await self._find(sid)
            fut.set_result(answer)
            return answer
        except BaseException as e:
            fut.set_exception(e)
            fut.exception()  # retrieved: nobody else may be waiting
            raise
        finally:
            self._busy.pop(sid, None)

    async def _find(self, sid: str) -> dict[str, Any]:
        song = ((await self.call("getSong", id=sid)).get("song") or {})
        artist, title = str(song.get("artist") or "").strip(), str(song.get("title") or "").strip()
        album, duration = str(song.get("album") or "").strip(), song.get("duration")
        answer = await self._navidrome(sid, artist, title)
        online = self.online()
        if answer is None and online and title:
            try:
                answer = await lrclib(artist, title, album, duration)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as e:
                log.info("LRCLIB didn't answer for %s - %s (%s)", artist, title, e.__class__.__name__)
                self._keep(sid, none(), FAILED_KEEP_S, online, disk=False)
                return none()
        if answer is None:
            self._keep(sid, none(), NONE_KEEP_S, online)
            return none()
        self._keep(sid, answer, FOUND_KEEP_S, online)
        return answer

    async def _navidrome(self, sid: str, artist: str, title: str) -> dict[str, Any] | None:
        try:
            got = await self.call("getLyricsBySongId", id=sid)
            answer = from_structured((got.get("lyricsList") or {}).get("structuredLyrics") or [])
            if answer:
                return answer
        except Exception as e:  # older servers: no OpenSubsonic - the classic call below
            log.debug("getLyricsBySongId: %s", e)
        if not title:
            return None
        try:
            got = await self.call("getLyrics", artist=artist, title=title)
        except Exception as e:
            log.debug("getLyrics: %s", e)
            return None
        text = str((got.get("lyrics") or {}).get("value") or "").strip()
        return from_lrc(text, "navidrome") if text else None


async def lrclib(artist: str, title: str, album: str, duration: Any) -> dict[str, Any] | None:
    """LRCLIB's best match (exact first, then a search) - only these four facts are sent."""
    headers = {"User-Agent": USER_AGENT}
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as s:
        params = {"artist_name": artist, "track_name": title}
        if album:
            params["album_name"] = album
        if isinstance(duration, (int, float)) and duration > 0:
            params["duration"] = str(int(round(duration)))
        # The exact lookup with album and length first, then with just artist and title (LRCLIB answers
        # "busy" (503) to the detailed one at times while the simple one works), then a search. Only when
        # every one of them failed without a plain "not found" is LRCLIB counted as unreachable.
        hit, failed = None, None
        tries = [params] + ([{"artist_name": artist, "track_name": title}] if len(params) > 2 else [])
        for p in tries:
            async with s.get(LRCLIB_URL + "/api/get", params=p) as r:
                if r.status == 200:
                    hit = await r.json(content_type=None)
                    break
                if r.status == 404:
                    break  # a real "not found": straight to the search
                failed = aiohttp.ClientResponseError(r.request_info, r.history, status=r.status)
        if not isinstance(hit, dict):
            async with s.get(LRCLIB_URL + "/api/search", params={"q": f"{artist} {title}".strip()}) as r:
                if r.status != 200 and failed:
                    raise failed
                found = await r.json(content_type=None) if r.status == 200 else []
            hit = _best(found if isinstance(found, list) else [], title, duration)
    if not hit:
        return None
    return from_lrc(str(hit.get("syncedLyrics") or ""), "lrclib") or from_lrc(str(hit.get("plainLyrics") or ""), "lrclib")


def _best(found: list[Any], title: str, duration: Any) -> dict[str, Any] | None:
    want = re.sub(r"[^a-z0-9]", "", title.lower())
    for x in found:
        if not isinstance(x, dict) or x.get("instrumental"):
            continue
        if want and re.sub(r"[^a-z0-9]", "", str(x.get("trackName") or "").lower()) != want:
            continue
        if isinstance(duration, (int, float)) and isinstance(x.get("duration"), (int, float)) and \
                abs(x["duration"] - duration) > 4:
            continue
        if x.get("syncedLyrics") or x.get("plainLyrics"):
            return x
    return None
