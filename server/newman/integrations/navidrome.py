"""Navidrome (or any Subsonic-compatible music server): your own music, played through the wall tablets.

Sign-in happens once: the password is used to make a Subsonic token - t = md5(password + salt), with
a random salt - which is checked with ping.view; then only the username, token and salt are kept
(integrations/<id>/token.json) and the password is dropped from the settings. If Navidrome stops
accepting the token (the password changed, or the account signs in through LDAP, which can't use
tokens), the connection asks for the password once more.

Each signed-in wall tablet gets one "music player" device (kinds.py). The music itself plays IN the
wall page: an <audio> element fed by the hub (GET /api/music/stream/<id> proxies stream.view with the
stored token, so the token never reaches a browser). The hub keeps each tablet's queue and state
(players.json), tells the tablet what to play over its websocket ({"type": "music"}), and the tablet
reports back position / paused / ended so "next" works and the device stays true. Commands (play,
pause, next, volume, "play something" by search, shuffle an artist or playlist) work from the
screen, from voice and as automation actions like any device's. Lyrics for the wall's player: lyrics.py
(Navidrome's own, then LRCLIB when the "Look up missing lyrics online" setting is on).

A player is available while its wall page reports (at least every minute while open). Once the page
goes quiet it is offline - unless its tablet is asleep (screen off, still checking in its battery on
time: the tablets connection's tablet_asleep()); then it stays available with "screen": "off", which
the kind shows as "Screen off", and only goes offline when the tablet does.

A player lasts only while its account is signed in on a wall tablet (the same rule as the tablet's
battery device, see tablets.py): once it isn't, the player's device and its queue go (forget_account,
called by the API). Signing in again starts a fresh, empty player.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import secrets
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

import aiohttp

from ..lyrics import LyricsFinder
from .base import CommandError, Field, Integration
from .music_match import band, content, norm, rank

log = logging.getLogger(__name__)

API_VERSION = "1.16.1"
CLIENT = "newman"
OFFLINE_AFTER_S = 3 * 60      # a wall page reports at least every minute while it's open
MAX_QUEUE = 500
RESTART_AFTER_S = 5           # "previous" this far into a song starts it again instead
ARTISTS_KEEP_S = 10 * 60      # the A-Z artist list (and the album names) are asked for again after this long
ALBUM_NAMES_MAX = 2000        # album names kept for matching what's said (the whole library, up to this)
OFFER_S = 2 * 60              # "Did you mean …?" - a yes counts for this long


class SubsonicError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class NotFound(CommandError):
    """Nothing in the library fits what was asked for ("I couldn't find …")."""


class MusicQuestion(CommandError):
    """Not sure: the message asks ("Did you mean Bed of Roses by Bon Jovi?") and a yes plays it."""


def make_token(password: str, salt: str) -> str:
    return hashlib.md5((password + salt).encode()).hexdigest()


def new_salt() -> str:
    return secrets.token_hex(8)


def clean_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and not re.match(r"^https?://", url):
        url = "http://" + url
    return url


class SubsonicClient:
    """The Subsonic REST API with token auth (u, t, s) - JSON answers."""

    def __init__(self, url: str, user: str, token: str, salt: str, timeout: float = 15) -> None:
        self.url, self.user, self.token, self.salt = clean_url(url), user, token, salt
        self.timeout = timeout
        self._session: aiohttp.ClientSession | None = None

    def params(self, **extra: Any) -> dict[str, str]:
        p = {"u": self.user, "t": self.token, "s": self.salt, "v": API_VERSION, "c": CLIENT}
        p.update({k: str(v) for k, v in extra.items() if v is not None})
        return p

    def endpoint(self, name: str) -> str:
        return f"{self.url}/rest/{name}.view"

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=self.timeout))
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def call(self, name: str, **params: Any) -> dict[str, Any]:
        s = await self.session()
        async with s.get(self.endpoint(name), params=self.params(f="json", **params),
                         timeout=aiohttp.ClientTimeout(total=self.timeout)) as r:
            if r.status >= 400:
                raise SubsonicError(r.status, f"HTTP {r.status}")
            try:
                data = await r.json(content_type=None)
            except ValueError as e:
                raise SubsonicError(0, "That doesn't look like a Navidrome / Subsonic server") from e
        body = (data or {}).get("subsonic-response") if isinstance(data, dict) else None
        if not isinstance(body, dict):
            raise SubsonicError(0, "That doesn't look like a Navidrome / Subsonic server")
        if body.get("status") != "ok":
            err = body.get("error") or {}
            raise SubsonicError(int(err.get("code") or 0), str(err.get("message") or "Navidrome refused"))
        return body

    async def open(self, name: str, headers: dict[str, str] | None = None, **params: Any) -> aiohttp.ClientResponse:
        """A raw response (stream / cover art) - the caller reads and releases it."""
        s = await self.session()
        return await s.get(self.endpoint(name), params=self.params(**params), headers=headers or {})


# -- matching what someone asked for --------------------------------------------------------------

def similarity(said: str, name: str) -> float:
    a, b = norm(said), norm(name)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    r = SequenceMatcher(None, a, b).ratio()
    # every spoken word in the name ("jump" for "Jump (Live)", "dark side" for "Dark Side of the Moon")
    aw, bw = a.split(), b.split()
    if all(any(x == y for y in bw) for x in aw):
        r = max(r, 0.75 + 0.2 * len(aw) / len(bw))
    return r


def best(items: list[dict[str, Any]], said: str, key: str, at_least: float) -> dict[str, Any] | None:
    scored = [(similarity(said, str(i.get(key) or "")), n, i) for n, i in enumerate(items)]
    scored = [s for s in scored if s[0] >= at_least]
    if not scored:
        return None
    return max(scored, key=lambda s: (s[0], -s[1]))[2]


def best_song(songs: list[dict[str, Any]], title: str, artist: str = "") -> dict[str, Any] | None:
    """The song meant by "Jump" (by "Van Halen"): the title must match well; with an artist named, the
    artist must too. Ties go to the server's order (its relevance)."""
    out = None
    top = 0.0
    for n, s in enumerate(songs):
        t = similarity(title, str(s.get("title") or ""))
        if t < 0.72:
            continue
        score = t
        if artist:
            a = max(similarity(artist, str(s.get("artist") or "")),
                    similarity(artist, str(s.get("albumArtist") or s.get("displayArtist") or "")))
            if a < 0.6:
                continue
            score += a
        score -= n * 0.001
        if out is None or score > top:
            out, top = s, score
    return out


def request_from_text(text: str, shuffle: bool = False) -> dict[str, Any]:
    """Typed words (an automation's "Play something") -> a request: "the album X (by Y)", "my X playlist",
    "X by Y", or anything else as a search."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    low = t.lower()
    m = re.match(r"^(?:the )?album (.+?)(?: by (.+))?$", low)
    if m:
        return {"album": t[m.start(1):m.end(1)], "artist": t[m.start(2):m.end(2)] if m.group(2) else "",
                "shuffle": shuffle}
    m = re.match(r"^(?:my |the |our )?(.+?) playlist$", low) or re.match(r"^playlist (.+)$", low)
    if m:
        return {"playlist": t[m.start(1):m.end(1)], "shuffle": shuffle}
    i = low.rfind(" by ")
    if i > 0:
        return {"title": t[:i], "artist": t[i + 4:], "shuffle": shuffle}
    return {"query": t, "shuffle": shuffle}


def track_json(s: dict[str, Any]) -> dict[str, Any]:
    """What the hub keeps of a song."""
    return {"id": str(s.get("id")), "title": str(s.get("title") or "Unknown"), "artist": str(s.get("artist") or ""),
            "album": str(s.get("album") or ""), "coverArt": str(s.get("coverArt") or s.get("albumId") or ""),
            "duration": int(s.get("duration") or 0)}


def say_track(t: dict[str, Any]) -> str:
    return f"{t['title']} by {t['artist']}" if t.get("artist") else t["title"]


def candidates(kind: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Library items as music_match candidates."""
    key = "title" if kind == "song" else "name"
    return [{"kind": kind, "name": str(i.get(key) or ""), "artist": str(i.get("artist") or ""), "item": i}
            for i in items if i.get("id")]


def say_pick(c: dict[str, Any]) -> str:
    """How to name a match: "Bed of Roses by Bon Jovi", "the album Night Amber by …", "your Road Trip playlist"."""
    by = f" by {c['artist']}" if c.get("artist") and c["kind"] in ("song", "album") else ""
    if c["kind"] == "album":
        return f"the album {c['name']}{by}"
    if c["kind"] == "playlist":
        return f"your {c['name']} playlist"
    return f"{c['name']}{by}"


# -- the integration ---------------------------------------------------------------------------------

class NavidromeIntegration(Integration):
    type = "navidrome"
    name = "Navidrome"
    category = "other"
    provides = ("music_player",)
    description = ("Your own music from Navidrome (or another Subsonic server), played through the wall tablets' "
                   "speakers - on screen, by voice (“play Jump by Van Halen”) and from automations. The password "
                   "is used once to make a token; Newman keeps only the username, token and salt.")
    fields = [
        Field("url", "Server address", required=True, placeholder="http://10.1.1.2:4533",
              help="Where Newman reaches Navidrome - its address on your network is best."),
        Field("username", "Username", required=True),
        Field("password", "Password", "password",
              help="Used once to make a token, then forgotten. Only needed again if Navidrome stops accepting it."),
        Field("lrclib", "Look up missing lyrics online (LRCLIB)", "boolean", default=True,
              help="When Navidrome has no lyrics for a song, the wall's player asks lrclib.net (a free public lyrics "
                   "database). Only the song's artist, title, album and length are sent - nothing about you or your "
                   "home. Off: only the lyrics Navidrome has."),
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._dir = Path(self.ctx.data_dir) / "integrations" / self.id
        self.client: SubsonicClient | None = None
        self.players: dict[str, dict[str, Any]] = self._load_players()
        self._artists: tuple[float, list[dict[str, Any]]] | None = None  # (when, getArtists' index)
        self._albums: tuple[float, list[dict[str, Any]]] | None = None   # (when, every album) - for matching
        self._playlist_names: tuple[str, ...] = ()  # as last seen (the hub's name index)
        self.offers: dict[str, dict[str, Any]] = {}  # uid -> "Did you mean …?" waiting for a yes
        self._lock = asyncio.Lock()
        self.shuffle_rng: Callable[[list], None] = random.shuffle
        self.lyrics = LyricsFinder(self._dir / "lyrics", self._call, lambda: self.config.get("lrclib", True) is not False)

    async def _call(self, name: str, **params: Any) -> dict[str, Any]:
        return await self.need_client().call(name, **params)

    # -- sign-in ------------------------------------------------------------------------------

    def _token_file(self) -> Path:
        return self._dir / "token.json"

    def _save_token(self, data: dict[str, str]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        f = self._token_file()
        tmp = f.with_suffix(".part")
        tmp.write_text(json.dumps(data))
        tmp.chmod(0o600)
        tmp.replace(f)

    def saved_token(self) -> dict[str, str]:
        try:
            d = json.loads(self._token_file().read_text())
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    async def sign_in(self) -> bool:
        """True when Navidrome accepts us. Otherwise the status says what's needed."""
        url, user = clean_url(str(self.config.get("url") or "")), str(self.config.get("username") or "").strip()
        password = str(self.config.get("password") or "")
        saved = self.saved_token()
        if password:
            salt = new_salt()
            client = SubsonicClient(url, user, make_token(password, salt), salt)
        elif saved.get("t") and saved.get("u") == user:
            client = SubsonicClient(url, user, saved["t"], saved.get("s", ""))
        else:
            self.set_status("pairing", "Enter your Navidrome password (used once to make a token) and save.")
            return False
        try:
            await client.call("ping")
        except SubsonicError as e:
            await client.close()
            if e.code == 41:
                msg = "This Navidrome account can't use token sign-in (an LDAP account?) - Newman needs one that can"
            elif e.code in (40, 401):
                msg = ("Navidrome didn't accept that username and password" if password else
                       "Navidrome no longer accepts Newman's token - enter the password again (used once)")
                if not password:
                    self._token_file().unlink(missing_ok=True)
            else:
                msg = f"Navidrome said: {e}"
            self.set_status("pairing" if e.code in (40, 41, 401) else "error", msg)
            return False
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            await client.close()
            self.set_status("error", f"Can't reach Navidrome at {url} ({e.__class__.__name__})")
            return False
        if password:
            # Signed in: keep the username, token and salt; the password is never needed again.
            self._save_token({"u": user, "t": client.token, "s": client.salt})
            cfg = {k: v for k, v in self.config.items() if k != "password"}
            self.config = cfg
            if self.save_config:
                self.save_config(cfg)
        self.client = client
        return True

    async def run(self) -> None:
        # The wall tablets' players are back straight away (offline until their page reports, or
        # "Screen off" while their tablet is asleep).
        for uid in list(self.players):
            if uid in self.players:  # (unless its account signed out meanwhile: forget_account)
                await self._publish(uid, page=False)
        attempt = 0
        while True:
            ok = await self.sign_in()
            if ok:
                break
            if self.status == "pairing":
                await asyncio.Event().wait()  # until the settings change (the manager restarts us)
            await self.backoff_sleep(attempt)
            attempt += 1
        self.set_status("connected", self._detail())
        try:
            while True:
                await asyncio.sleep(30)
                await self.check_offline()
                try:
                    await self.client.call("ping")
                    if self.status != "connected":
                        self.set_status("connected", self._detail())
                except SubsonicError as e:
                    if e.code in (40, 41):
                        self._token_file().unlink(missing_ok=True)
                        self.set_status("pairing", "Navidrome no longer accepts Newman's token - enter the password "
                                                   "again (used once)")
                        await asyncio.Event().wait()
                    self.set_status("error", f"Navidrome said: {e}")
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                    self.set_status("error", f"Can't reach Navidrome ({e.__class__.__name__})")
        finally:
            if self.client:
                await self.client.close()

    def _detail(self) -> str:
        n = len(self.players)
        return f"{n} wall tablet{'s' if n != 1 else ''} can play music" if n else \
            "Connected - each wall tablet gets a music player when it's next opened"

    def connected(self) -> bool:
        return self.client is not None and self.status == "connected"

    def need_client(self) -> SubsonicClient:
        if not self.client:
            raise CommandError("Navidrome isn't connected")
        return self.client

    # -- the players (one per wall tablet) ----------------------------------------------------------

    def _players_file(self) -> Path:
        return self._dir / "players.json"

    def _load_players(self) -> dict[str, dict[str, Any]]:
        try:
            d = json.loads(self._players_file().read_text())
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_players(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._players_file().with_suffix(".part")
            tmp.write_text(json.dumps(self.players))
            tmp.replace(self._players_file())
        except OSError:
            log.warning("Couldn't keep the music players' queues", exc_info=True)

    def player_id(self, uid: str) -> str:
        return self.entity_id(uid)

    def uid_of(self, entity_id: str) -> str:
        return entity_id.split(".", 1)[1] if "." in entity_id else entity_id

    def player(self, uid: str) -> dict[str, Any] | None:
        return self.players.get(uid)

    def rename_account(self, uid: str, name: str) -> None:
        """The tablet's account was renamed: its player is called after the new name from now on."""
        p = self.players.get(uid)
        if p is not None and name and p.get("name") != name:
            p["name"] = name
            self._save_players()

    def tablet_accounts(self) -> set[str]:
        """The accounts this connection keeps a wall tablet's player for."""
        return set(self.players) | {self.uid_of(e.id) for e in self.hub.all() if e.integration == self.id}

    def forget_account(self, uid: str) -> tuple[str, list[str]]:
        """The account isn't signed in on any wall tablet now: its player device, queue and an unanswered
        "Did you mean …?" go (the API decides, see api.forget_signed_out_tablets). Signing in again starts
        a fresh player. Returns (the account's name, the removed device ids)."""
        p = self.players.pop(uid, None)
        self.offers.pop(uid, None)
        eid = self.player_id(uid)
        name = (p or {}).get("name") or ""
        on_hub = self.hub.remove(eid)  # (not yet, at start: then only its saved queue goes)
        gone = [eid] if on_hub or p is not None else []
        if p is not None:
            self._save_players()
        if self.status == "connected":
            self.set_status("connected", self._detail())
        return name, gone

    def ensure_player(self, uid: str, name: str) -> dict[str, Any]:
        p = self.players.get(uid)
        if p is None:
            p = self.players[uid] = {"name": name, "queue": [], "index": 0, "state": "idle", "position": 0,
                                     "volume": 60, "at": time.time(), "seq": 0}
            self._save_players()
        elif name and p.get("name") != name:
            p["name"] = name
        return p

    @staticmethod
    def current(p: dict[str, Any]) -> dict[str, Any] | None:
        q = p.get("queue") or []
        i = int(p.get("index") or 0)
        return q[i] if 0 <= i < len(q) else None

    def player_json(self, uid: str) -> dict[str, Any] | None:
        """What the wall page needs to play (and the hub's view of it)."""
        p = self.players.get(uid)
        if not p:
            return None
        t = self.current(p)
        track = None
        if t:
            track = {**t, "stream": f"api/music/stream/{t['id']}",
                     "cover": f"api/music/cover/{t['coverArt']}" if t.get("coverArt") else None}
        nxt = (p.get("queue") or [])[int(p.get("index") or 0) + 1:int(p.get("index") or 0) + 4]
        # Since when it has sat paused (epoch seconds), so every screen - even one opened days later - can
        # tell a moment's pause from music left paused; kept with the player, dropped once it isn't paused.
        if p["state"] == "paused":
            p.setdefault("pausedAt", time.time())
        else:
            p.pop("pausedAt", None)
        return {"entityId": self.player_id(uid), "state": p["state"], "volume": p["volume"],
                "pausedAt": p.get("pausedAt"),
                "position": p.get("position", 0), "track": track, "index": p.get("index", 0),
                "queueLength": len(p.get("queue") or []), "seq": p.get("seq", 0),
                "upNext": [{"title": x["title"], "artist": x["artist"]} for x in nxt]}

    def _tablet_asleep(self, uid: str) -> bool:
        """The account's wall tablet has its screen off but still checks in (the tablets connection
        says so; whichever running connection answers tablet_asleep)."""
        for inst in list((getattr(self.ctx, "instances", None) or {}).values()):
            asleep = getattr(inst, "tablet_asleep", None)
            if callable(asleep):
                try:
                    if asleep(uid):
                        return True
                except Exception:  # noqa: BLE001 - never let a look at another connection break the player
                    log.debug("tablet_asleep failed", exc_info=True)
        return False

    def _presence(self, uid: str, page: bool | None = None) -> tuple[bool, str]:
        """(available, screen): the page reporting = on; quiet but its tablet asleep = "Screen off";
        otherwise offline. [page]: whether the page counts as reporting (None = by its last report)."""
        if page is None:
            page = time.time() - float(self.players[uid].get("at") or 0) <= OFFLINE_AFTER_S
        if page:
            return True, "on"
        if self._tablet_asleep(uid):
            return True, "off"
        return False, "on"

    async def _publish(self, uid: str, page: bool | None = None) -> None:
        """The player's device, from its state."""
        p = self.players[uid]
        eid = self.player_id(uid)
        self.hub.upsert(eid, kind="music_player", name=f"{p.get('name') or 'Wall tablet'} music", integration=self.id)
        t = self.current(p) or {}
        available, screen = self._presence(uid, page)
        attrs = {"volume": p["volume"], "title": t.get("title"), "artist": t.get("artist"), "album": t.get("album"),
                 "cover": f"api/music/cover/{t['coverArt']}" if t.get("coverArt") else None,
                 "position": int(p.get("position") or 0), "duration": t.get("duration"),
                 "queueLength": len(p.get("queue") or []), "screen": screen}
        await self.hub.update(eid, state=p["state"], attrs=attrs, available=available)

    async def _push(self, uid: str, seek: float | None = None) -> None:
        """Tell the tablet what to play now (it follows the hub)."""
        p = self.players[uid]
        p["seq"] = int(p.get("seq") or 0) + 1
        if seek is not None:  # the spot is known as of now
            self._mark_position(p)
        self._save_players()
        await self._publish(uid)
        msg = {"type": "music", "player": self.player_json(uid)}
        if seek is not None:
            msg["seek"] = seek
        push = getattr(self.ctx, "push_to_user", None)
        if push:
            await push(uid, msg)

    async def report(self, uid: str, name: str, body: dict[str, Any]) -> dict[str, Any]:
        """The wall page: {"state": playing|paused|idle|ended|error, "position", "trackId"} (all optional; an
        empty report = "I'm here"). Returns the player as the page should be."""
        async with self._lock:
            p = self.ensure_player(uid, name)
            p["at"] = time.time()
            cur = self.current(p)
            same = cur is not None and str(body.get("trackId") or "") == cur["id"]
            state = body.get("state")
            pos = body.get("position")
            if same and isinstance(pos, (int, float)) and not isinstance(pos, bool):
                p["position"] = max(0, int(pos))
                self._mark_position(p)
            if same and state == "ended":
                await self._advance(uid, 1, auto=True)
                return self.player_json(uid)
            if same and state == "error":
                log.info("Music: %s couldn't play %s", p.get("name"), cur["id"])
                await self._advance(uid, 1, auto=True)
                return self.player_json(uid)
            if same and state in ("playing", "paused") and p["state"] != state and body.get("byUser"):
                p["state"] = state  # paused by the page itself (e.g. the system's media controls)
            await self._publish(uid, page=True)
            self._save_players()
            return self.player_json(uid)

    async def check_offline(self) -> None:
        """Every 30 s: a page gone quiet is "Screen off" while its tablet sleeps, offline otherwise."""
        for uid in list(self.players):
            ent = self.hub.get(self.player_id(uid))
            if not ent:
                continue
            available, screen = self._presence(uid)
            if ent.available != available or (ent.attrs or {}).get("screen", "on") != screen:
                await self.hub.update(ent.id, attrs={"screen": screen}, available=available)

    async def _advance(self, uid: str, step: int, auto: bool = False) -> None:
        p = self.players[uid]
        i = int(p.get("index") or 0) + step
        if i >= len(p.get("queue") or []):
            p["state"], p["position"], p["index"] = "idle", 0, 0  # the end: "play" starts over
            p["ranOut"] = p.get("queueId")  # (played to the end, not stopped by someone)
        else:
            p["index"], p["position"] = max(0, i), 0
            if auto or p["state"] == "idle":
                p["state"] = "playing"  # (skipping while paused stays paused)
        await self._push(uid, seek=0)

    async def set_queue(self, uid: str, songs: list[dict[str, Any]], start: int = 0) -> dict[str, Any]:
        p = self.players[uid]
        p["queue"] = [track_json(s) for s in songs if s.get("id")][:MAX_QUEUE]
        p["index"], p["position"], p["state"] = start, 0, "playing" if p["queue"] else "idle"
        p["queueId"] = secrets.token_hex(6)  # which music this is ("still the automation's?")
        p.pop("ranOut", None)
        await self._push(uid, seek=0)
        return p

    # -- "play for N minutes, then put it back" (automations; see Integration.snapshot) ---------------

    @staticmethod
    def _mark_position(p: dict[str, Any]) -> None:
        cur = NavidromeIntegration.current(p)
        p["posAt"], p["posTrack"] = time.time(), cur["id"] if cur else None

    def position_now(self, p: dict[str, Any]) -> int:
        """Seconds into the song: the last report (every ~15 s while playing), plus the time since if it plays."""
        pos = float(p.get("position") or 0)
        cur = self.current(p)
        if p.get("state") == "playing" and cur and p.get("posTrack") == cur["id"] and p.get("posAt"):
            pos += max(0.0, time.time() - float(p["posAt"]))
            if cur.get("duration"):
                pos = min(pos, max(0, int(cur["duration"]) - 1))
        return int(pos)

    def snapshot(self, entity_id: str) -> dict[str, Any] | None:
        p = self.players.get(self.uid_of(entity_id))
        if p is None:
            return None
        return {"queue": [dict(t) for t in p.get("queue") or []], "index": int(p.get("index") or 0),
                "position": self.position_now(p), "state": p.get("state") or "idle",
                "volume": int(p.get("volume") or 0), "queueId": p.get("queueId")}

    def marker(self, entity_id: str) -> dict[str, Any] | None:
        p = self.players.get(self.uid_of(entity_id))
        return {"queueId": p.get("queueId"), "state": p.get("state")} if p is not None else None

    @staticmethod
    def still_as_left(p: dict[str, Any], marker: dict[str, Any] | None) -> bool:
        """Still the music the automation started (skipping within it is fine; it running out is too)?"""
        if not marker:
            return True
        if p.get("queueId") != marker.get("queueId"):
            return False  # other music was put on
        if p.get("state") == marker.get("state"):
            return True
        return p.get("state") == "idle" and p.get("ranOut") == p.get("queueId")

    @staticmethod
    def describe_snapshot(snap: dict[str, Any]) -> str:
        q, i = snap.get("queue") or [], int(snap.get("index") or 0)
        t = q[i] if 0 <= i < len(q) else None
        if not t or snap.get("state") not in ("playing", "paused"):
            return "nothing playing"
        pos = int(snap.get("position") or 0)
        name = f"{t['artist']} – {t['title']}" if t.get("artist") else t.get("title") or "Unknown"
        return f"{name} at {pos // 60}:{pos % 60:02d}, {snap['state']}"

    async def restore(self, entity_id: str, snap: dict[str, Any], marker: dict[str, Any] | None,
                      during: str) -> str:
        uid = self.uid_of(entity_id)
        async with self._lock:
            p = self.players.get(uid)
            if p is None:
                raise CommandError("That music player is gone")
            if not self.still_as_left(p, marker):
                return f"Left the music alone: it was changed during {during}"
            p["queue"] = [t for t in snap.get("queue") or [] if isinstance(t, dict) and t.get("id")][:MAX_QUEUE]
            i = int(snap.get("index") or 0)
            p["index"] = i if 0 <= i < len(p["queue"]) else 0
            state = snap.get("state") if snap.get("state") in ("playing", "paused") and p["queue"] else "idle"
            p["state"], p["position"] = state, int(snap.get("position") or 0) if state != "idle" else 0
            if isinstance(snap.get("volume"), int):
                p["volume"] = max(0, min(100, snap["volume"]))
            p["queueId"] = snap.get("queueId") or secrets.token_hex(6)
            p.pop("ranOut", None)
            await self._push(uid, seek=p["position"])
        return "Put back: " + self.describe_snapshot(snap) + f" (volume {p['volume']})"

    # -- finding music ------------------------------------------------------------------------------

    async def search(self, query: str, songs: int = 20, albums: int = 8, artists: int = 5) -> dict[str, list]:
        r = (await self.need_client().call("search3", query=query, songCount=songs, albumCount=albums,
                                           artistCount=artists)).get("searchResult3") or {}
        return {"song": list(r.get("song") or []), "album": list(r.get("album") or []),
                "artist": list(r.get("artist") or [])}

    async def album(self, album_id: str) -> dict[str, Any]:
        """One album with its songs (getAlbum: {..., "song": [...]})."""
        return (await self.need_client().call("getAlbum", id=album_id)).get("album") or {}

    async def album_songs(self, album_id: str) -> list[dict[str, Any]]:
        return list((await self.album(album_id)).get("song") or [])

    async def playlist(self, pid: str) -> dict[str, Any]:
        """One playlist with its songs (getPlaylist: {..., "entry": [...]})."""
        return (await self.need_client().call("getPlaylist", id=pid)).get("playlist") or {}

    async def album_index(self) -> list[dict[str, Any]]:
        """Every album's name (up to ALBUM_NAMES_MAX), kept ARTISTS_KEEP_S - matching what's said."""
        now = time.monotonic()
        if self._albums and now - self._albums[0] < ARTISTS_KEEP_S:
            return self._albums[1]
        out: list[dict[str, Any]] = []
        while len(out) < ALBUM_NAMES_MAX:
            got = list(((await self.need_client().call("getAlbumList2", type="alphabeticalByName", size=500,
                                                       offset=len(out))).get("albumList2") or {}).get("album") or [])
            out += got
            if len(got) < 500:
                break
        self._albums = (now, out)
        return out

    async def playlists(self) -> list[dict[str, Any]]:
        got = list(((await self.need_client().call("getPlaylists")).get("playlists") or {}).get("playlist") or [])
        self._playlist_names = tuple(str(p.get("name") or "") for p in got)
        return got

    def names_key(self) -> tuple[Any, ...]:
        """Changes when the names on hand do (cached_names)."""
        return (self._artists[0] if self._artists else None, self._albums[0] if self._albums else None,
                self._playlist_names)

    def cached_names(self) -> dict[str, list[tuple[str, int]]]:
        """The library's names already on hand - no network: {"artist" | "album" | "playlist": [(name, plays)]}.
        Plays add up the albums' play counts (when Navidrome gives them; 0 otherwise)."""
        albums = self._albums[1] if self._albums else []
        plays: dict[str, int] = {}
        for al in albums:
            a = str(al.get("artist") or "")
            plays[a] = plays.get(a, 0) + int(al.get("playCount") or 0)
        artists = [str(a.get("name") or "") for i in (self._artists[1] if self._artists else []) for a in i.get("artist") or []]
        return {"artist": [(n, plays.get(n, 0)) for n in artists] or [(n, c) for n, c in plays.items() if n],
                "album": [(str(al.get("name") or ""), int(al.get("playCount") or 0)) for al in albums],
                "playlist": [(n, 0) for n in self._playlist_names]}

    async def playlist_songs(self, pid: str) -> list[dict[str, Any]]:
        return list((await self.playlist(pid)).get("entry") or [])

    async def recent_albums(self, size: int = 12) -> list[dict[str, Any]]:
        c = self.need_client()
        for kind in ("recent", "newest"):
            got = list(((await c.call("getAlbumList2", type=kind, size=size)).get("albumList2") or {}).get("album") or [])
            if got:
                return got
        return []

    async def artist_index(self) -> list[dict[str, Any]]:
        """Every artist, in Navidrome's own letter groups (getArtists: [{name: "A", artist: [...]}], "The"
        and the like ignored as Navidrome is set to) - kept ARTISTS_KEEP_S, it's the whole library."""
        now = time.monotonic()
        if self._artists and now - self._artists[0] < ARTISTS_KEEP_S:
            return self._artists[1]
        got = list(((await self.need_client().call("getArtists")).get("artists") or {}).get("index") or [])
        self._artists = (now, got)
        return got

    async def artist(self, artist_id: str) -> dict[str, Any]:
        """One artist and their albums (getArtist)."""
        return (await self.need_client().call("getArtist", id=artist_id)).get("artist") or {}

    async def artist_songs(self, artist: dict[str, Any]) -> list[dict[str, Any]]:
        """An artist's songs: every album of theirs (most artists have a handful)."""
        a = await self.artist(str(artist["id"]))
        out: list[dict[str, Any]] = []
        for al in (a.get("album") or [])[:30]:
            out += await self.album_songs(str(al["id"]))
            if len(out) >= MAX_QUEUE:
                break
        return out

    async def find_exact(self, request: dict[str, Any]) -> tuple[list[dict[str, Any]], int, str]:
        """What someone asked for, by Navidrome's own search -> (songs, start index, what to say). Raises
        NotFound("I couldn't find …") when nothing fits. request: {title, artist, album, playlist, query,
        shuffle}."""
        title, artist = str(request.get("title") or "").strip(), str(request.get("artist") or "").strip()
        album, playlist = str(request.get("album") or "").strip(), str(request.get("playlist") or "").strip()
        query = str(request.get("query") or "").strip()
        shuffle = bool(request.get("shuffle"))

        def mix(songs: list[dict[str, Any]]) -> list[dict[str, Any]]:
            songs = list(songs)
            self.shuffle_rng(songs)
            return songs

        if request.get("random"):
            songs = list(((await self.need_client().call("getRandomSongs", size=100)).get("randomSongs") or {})
                         .get("song") or [])
            if not songs:
                raise CommandError("I couldn't find any music on Navidrome.")
            return songs, 0, "Shuffling your music."
        if playlist:
            pl = best(await self.playlists(), playlist, "name", 0.6)
            if not pl:
                raise NotFound(f"I couldn't find a playlist called {playlist}.")
            songs = await self.playlist_songs(str(pl["id"]))
            if not songs:
                raise CommandError(f"The {pl['name']} playlist is empty.")
            return (mix(songs) if shuffle else songs), 0, \
                f"{'Shuffling' if shuffle else 'Playing'} your {pl['name']} playlist."
        if album:
            r = await self.search(f"{album} {artist}".strip() if artist else album, songs=0, albums=20, artists=0)
            cands = r["album"]
            if artist:
                cands = [a for a in cands if similarity(artist, str(a.get("artist") or "")) >= 0.6] or []
            al = best(cands, album, "name", 0.7)
            if not al:
                raise NotFound(f"I couldn't find the album {album}" + (f" by {artist}." if artist else "."))
            songs = await self.album_songs(str(al["id"]))
            by = f" by {al['artist']}" if al.get("artist") else ""
            return (mix(songs) if shuffle else songs), 0, f"{'Shuffling' if shuffle else 'Playing'} {al['name']}{by}."
        if title:
            r = await self.search(f"{title} {artist}".strip(), songs=40, albums=0, artists=0)
            song = best_song(r["song"], title, artist)
            if not song and artist:  # some servers only find the title alone
                r = await self.search(title, songs=60, albums=0, artists=0)
                song = best_song(r["song"], title, artist)
            if not song and artist:  # "Stand by Me" is a title, not "Stand" by "Me"
                whole = f"{title} by {artist}"
                song = best_song((await self.search(whole, songs=40, albums=0, artists=0))["song"], whole)
            if not song:
                raise NotFound(f"I couldn't find {title}" + (f" by {artist}." if artist else "."))
            return await self._song_then_more(song, artist), 0, f"Playing {say_track(track_json(song))}."
        said = artist or query
        if not said:
            raise CommandError("Say what to play - a song, an artist, an album or a playlist.")
        r = await self.search(said, songs=40, albums=10, artists=5)
        art = best(r["artist"], said, "name", 0.85)
        if art:
            songs = await self.artist_songs(art)
            if songs:
                return mix(songs), 0, f"{'Shuffling' if shuffle else 'Playing'} {art['name']}."
        if not artist:
            # A playlist by that name ("play road trip")
            try:
                pl = best(await self.playlists(), said, "name", 0.85)
            except SubsonicError:
                pl = None
            if pl:
                songs = await self.playlist_songs(str(pl["id"]))
                if songs:
                    return (mix(songs) if shuffle else songs), 0, \
                        f"{'Shuffling' if shuffle else 'Playing'} your {pl['name']} playlist."
            al = best(r["album"], said, "name", 0.85)
            if al:
                songs = await self.album_songs(str(al["id"]))
                if songs:
                    by = f" by {al['artist']}" if al.get("artist") else ""
                    return (mix(songs) if shuffle else songs), 0, f"Playing {al['name']}{by}."
            song = best_song(r["song"], said)
            if song:
                return [song], 0, f"Playing {say_track(track_json(song))}."
        raise NotFound(f"I couldn't find {said}.")

    async def _song_then_more(self, song: dict[str, Any], artist: str = "") -> list[dict[str, Any]]:
        """A song, then more by the same artist (shuffled), so the music keeps going."""
        more: list[dict[str, Any]] = []
        try:
            art = {"id": song["artistId"]} if song.get("artistId") else \
                best((await self.search(song.get("artist") or artist, songs=0, albums=0, artists=5))["artist"],
                     str(song.get("artist") or artist), "name", 0.8)
            if art:
                more = [s for s in await self.artist_songs(art) if s.get("id") != song.get("id")]
                self.shuffle_rng(more)
        except (SubsonicError, aiohttp.ClientError, asyncio.TimeoutError):
            more = []
        return [song] + more

    async def find(self, request: dict[str, Any], ask: bool = False,
                   uid: str = "") -> tuple[list[dict[str, Any]], int, str]:
        """What someone asked for -> (songs, start index, what to say). Navidrome's own search first; when
        that finds nothing (speech-to-text mishears names: "Better Roses"), the library is matched by how
        the words look and sound (music_match.py): sure -> play it; fairly sure and `ask` (voice) -> raise
        MusicQuestion("Did you mean …?"), kept for a yes (answer_offer); otherwise NotFound as before.
        request: {title, artist, album, playlist, query, kind, shuffle, random}."""
        if uid:
            self.offers.pop(uid, None)  # a new request replaces an unanswered "Did you mean …?"
        try:
            return await self.find_exact(request)
        except NotFound as missed:
            title, artist = str(request.get("title") or "").strip(), str(request.get("artist") or "").strip()
            album, query = str(request.get("album") or "").strip(), str(request.get("query") or "").strip()
            kind = str(request.get("kind") or "")
            if album:
                said, kind = album, "album"
            elif title:
                said, kind = title, kind or "song"
            elif query:
                said = query
            elif artist:
                said, artist, kind = artist, "", "artist"
            else:
                raise
            ranked = await self.match(said, artist=artist, kind=kind)
            how = band(ranked)
            if how == "low":
                raise missed from None
            pick = ranked[0][1]
            if how == "medium" and ask:
                if uid:
                    self.offers[uid] = {"pick": pick, "shuffle": bool(request.get("shuffle")), "at": time.time()}
                raise MusicQuestion(f"Did you mean {say_pick(pick)}?") from None
            songs, say = await self.play_pick(pick, bool(request.get("shuffle")))
            return songs, 0, say

    async def match(self, said: str, artist: str = "", kind: str = "") -> list[tuple[float, dict[str, Any]]]:
        """The library's best fits for words heard (music_match.rank), best first. Candidates: Navidrome's
        search for the whole phrase, then for each longer word on its own ("roses"), and for the artist
        named; plus every artist and album name (kept a while - whole-library lists)."""
        kinds = {kind} if kind else {"song", "album", "artist", "playlist"}
        queries = [said] + [w for w in content(norm(said).split()) if len(w) >= 3] + ([artist] if artist else [])
        cands: list[dict[str, Any]] = []
        asked: set[str] = set()
        for q in queries:
            if q.lower() in asked or len(asked) >= 6:
                continue
            asked.add(q.lower())
            r = await self.search(q, songs=40 if "song" in kinds else 0, albums=20 if "album" in kinds else 0,
                                  artists=10 if "artist" in kinds else 0)
            cands += candidates("song", r["song"]) + candidates("album", r["album"]) + candidates("artist", r["artist"])
        try:
            if "artist" in kinds and not artist:
                cands += candidates("artist", [a for i in await self.artist_index() for a in i.get("artist") or []])
            if "album" in kinds:
                cands += candidates("album", await self.album_index())
            if "playlist" in kinds and not artist:
                cands += candidates("playlist", await self.playlists())
        except SubsonicError:
            pass
        return rank(said, cands, artist)

    async def play_pick(self, pick: dict[str, Any], shuffle: bool = False) -> tuple[list[dict[str, Any]], str]:
        """A match (music_match candidate) -> (songs to queue, what to say)."""
        item, kind = pick["item"], pick["kind"]
        verb = "Shuffling" if shuffle else "Playing"
        if kind == "song":
            return await self._song_then_more(item), f"Playing {say_track(track_json(item))}."
        if kind == "artist":
            songs = await self.artist_songs(item)
            self.shuffle_rng(songs)
        elif kind == "album":
            songs = await self.album_songs(str(item["id"]))
        else:
            songs = await self.playlist_songs(str(item["id"]))
        if not songs:
            raise NotFound(f"There's nothing to play in {say_pick(pick)}.")
        if shuffle and kind != "artist":
            songs = list(songs)
            self.shuffle_rng(songs)
        what = say_pick(pick)
        what = what[len("the album "):] if kind == "album" else what
        return songs, f"{verb} {what}."

    def offer(self, uid: str) -> dict[str, Any] | None:
        """The "Did you mean …?" waiting for this tablet's yes, if it's still fresh."""
        o = self.offers.get(uid)
        if o and time.time() - o["at"] > OFFER_S:
            self.offers.pop(uid, None)
            o = None
        return o

    async def answer_offer(self, uid: str, yes: bool) -> str:
        """The answer to "Did you mean …?": yes plays it; no lets it go."""
        o = self.offer(uid)
        self.offers.pop(uid, None)
        if not o:
            return "Okay."
        if not yes:
            return "Okay, never mind."
        songs, say = await self.play_pick(o["pick"], o["shuffle"])
        async with self._lock:
            await self.set_queue(uid, songs)
        return say

    async def play_request(self, uid: str, request: dict[str, Any], ask: bool = False) -> str:
        """Find and play; returns what to say ("Playing Jump by Van Halen."). `ask` (voice): a fair guess
        is asked about first (MusicQuestion) instead of played."""
        songs, start, say = await self.find(request, ask=ask, uid=uid)
        async with self._lock:
            await self.set_queue(uid, songs, start)
        return say

    async def play_ids(self, uid: str, *, album: str = "", playlist: str = "", song: str = "", artist: str = "",
                       shuffle: bool = False, start_song: str = "", start: int | None = None) -> str:
        """The wall's Music tile: play what was tapped. An album or playlist can start part way through
        (`start_song` id or `start` index - a song tapped on its page); the whole of it is queued."""
        at = 0
        if album or playlist:
            songs = await self.album_songs(album) if album else await self.playlist_songs(playlist)
            if start_song:
                at = next((n for n, x in enumerate(songs) if str(x.get("id")) == start_song), -1)
                if at < 0:
                    raise CommandError("That song isn't there any more.")
            elif start is not None:
                if not 0 <= start < len(songs):
                    raise CommandError("That song isn't there any more.")
                at = start
            if at and shuffle:
                shuffle = False  # a song picked to start from: in order from there
        elif artist:
            songs = await self.artist_songs({"id": artist})
            shuffle = True
        elif song:
            # A song tapped in the search results: it, then the rest of its album.
            s = (await self.need_client().call("getSong", id=song)).get("song")
            songs = [s] if s else []
            if s and s.get("albumId"):
                album_songs = await self.album_songs(str(s["albumId"]))
                here = next((n for n, x in enumerate(album_songs) if x.get("id") == s.get("id")), None)
                if here is not None:
                    songs = album_songs[here:]
        else:
            songs = []
        if not songs:
            raise CommandError("Nothing to play there.")
        if shuffle:
            songs = list(songs)
            self.shuffle_rng(songs)
        async with self._lock:
            await self.set_queue(uid, songs, at)
        return say_track(track_json(songs[at]))

    # -- commands (screen, voice, automations) ------------------------------------------------------

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        uid = self.uid_of(entity_id)
        p = self.players.get(uid)
        if p is None:
            raise CommandError("That music player is gone")
        ent = self.hub.get(entity_id)
        if ent is not None and command not in ("pause", "stop", "set_volume"):
            if not ent.available:
                raise CommandError("That wall tablet isn't open right now")
            if (ent.attrs or {}).get("screen") == "off":  # asleep: no page to play it
                raise CommandError("That wall tablet's screen is off right now")
        if command in ("play_query", "shuffle"):
            text = str(value or "").strip()
            if not text:
                raise CommandError("Say what to play")
            try:
                await self.play_request(uid, request_from_text(text, command == "shuffle"))
            except (SubsonicError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                raise CommandError(f"Navidrome didn't answer ({e})") from e
            return
        async with self._lock:
            if command == "play":
                if not self.current(p):
                    raise CommandError("Nothing to play yet - pick some music first")
                if p["state"] != "playing":
                    self._mark_position(p)  # it carries on from here
                p["state"] = "playing"
                await self._push(uid)
            elif command == "pause":
                if p["state"] == "playing":
                    p["position"] = self.position_now(p)  # (the page's last report may be a few seconds old)
                    p["state"] = "paused"
                await self._push(uid)
            elif command == "stop":
                p["state"], p["position"] = "idle", 0
                await self._push(uid, seek=0)
            elif command == "next":
                if not self.current(p):
                    raise CommandError("Nothing is playing")
                await self._advance(uid, 1)
            elif command == "previous":
                if not self.current(p):
                    raise CommandError("Nothing is playing")
                if int(p.get("position") or 0) > RESTART_AFTER_S or int(p.get("index") or 0) == 0:
                    p["position"] = 0
                    await self._push(uid, seek=0)
                else:
                    await self._advance(uid, -1)
            elif command in ("set_volume", "volume_up", "volume_down"):
                if command == "set_volume":
                    try:
                        v = int(round(float(value)))
                    except (TypeError, ValueError):
                        raise CommandError("Volume is 0-100") from None
                else:
                    v = int(p["volume"]) + (10 if command == "volume_up" else -10)
                p["volume"] = max(0, min(100, v))
                await self._push(uid)
            else:
                raise CommandError(f"A music player can't {command.replace('_', ' ')}")
