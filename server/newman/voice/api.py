"""Voice routes (see API.md → Voice). Registered by api.build_app.

    GET    /api/voice                     what this sign-in gets: speech services up?, its settings, wake word
    POST   /api/voice/utterance           audio (PCM16 16 kHz mono, or WAV) or {"text"} -> {heard, reply, action, audio}
    GET    /api/voice/wakeword            this tablet's wake-word model files' addresses + threshold (the wall app)
    GET    /api/voice/wakewords           the wake words on the hub to choose from
    GET    /api/voice/wakeword/{file}     one of those .onnx files
    GET    /api/voice/timers              this account's timers
    DELETE /api/voice/timers/{id}         cancel one / stop it ringing
    GET    /api/voice/timers/{id}/done    {"text", "audio"}: "Your pasta timer is done." spoken
    GET    /api/voice/voices              the reply voices to choose from (Piper's, or the HTTP service's)
    GET    /api/voice/settings/{uid}      a wall tablet's voice settings (admin)
    PATCH  /api/voice/settings/{uid}      change them (admin)
    GET    /api/voice/hint                the Whisper hint (the home's names speech-to-text expects) (admin)
    /api/voice/teach…, /api/voice/train…  teaching and training "Hey Newman" (voice/teach.py)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from aiohttp import web

from .. import today as T
from ..automations import describe as AD
from ..automations.engine import ActionError
from ..config import config
from ..integrations.base import CommandError
from ..kinds import KINDS
from ..music import music_of
from . import intents as I
from .hint import HintFile
from .names import Name, NameIndex
from .openai_tts import FallbackTTS, OpenAITTS
from .pipeline import Pipeline, Sources, VoiceUnavailable, VoiceViewer
from .teach import add_teach_routes
from .timers import Timers
from .whisper_restart import WhisperRestarter
from .wyoming import WyomingClient, WyomingError, parse_address, pcm_from_wav

if TYPE_CHECKING:
    from ..app import Newman

log = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 30 * 16000 * 2  # 30 s of 16 kHz 16-bit mono
SENSITIVITY = {"low": 0.15, "normal": 0.0, "high": -0.15}  # added to the model's threshold
DEFAULTS = {"enabled": True, "sensitivity": "normal", "volume": 80, "voice": "", "allowSensitive": False,
            "wakeWord": ""}  # "" = the hub's default wake word
# Clear English Piper voices worth offering (the hub's default voice is VOICE_NAME). With an HTTP
# text-to-speech service (VOICE_TTS_KIND=openai) its own English voices are offered instead.
VOICES = [{"name": "en_US-lessac-medium", "label": "Lessac (American, woman)"},
          {"name": "en_US-amy-medium", "label": "Amy (American, woman)"},
          {"name": "en_US-hfc_female-medium", "label": "Hannah (American, woman)"},
          {"name": "en_US-ryan-medium", "label": "Ryan (American, man)"},
          {"name": "en_US-hfc_male-medium", "label": "Harry (American, man)"},
          {"name": "en_GB-alba-medium", "label": "Alba (Scottish, woman)"},
          {"name": "en_GB-northern_english_male-medium", "label": "Northern English (man)"}]
WAKE_FILES = ("melspectrogram", "embedding_model")
# openWakeWord's pre-trained English wake words, fetched into DATA_DIR/voice/ at startup when missing
# (not "alexa": it would wake the house's real Alexa devices too).
PRETRAINED_URL = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/{}_v0.1.onnx"
PRETRAINED = {"hey_jarvis": "Hey Jarvis", "hey_mycroft": "Hey Mycroft", "hey_rhasspy": "Hey Rhasspy"}


def wake_words(voice_dir: Path) -> list[dict[str, Any]]:
    """Every <name>.onnx in voice_dir except the two feature models, with its <name>.json
    ({"phrase", "threshold"}, both optional)."""
    out = []
    for f in sorted(voice_dir.glob("*.onnx")) if voice_dir.is_dir() else []:
        name = f.stem
        if name in WAKE_FILES or not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
            continue
        meta_path = voice_dir / f"{name}.json"
        try:
            meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
        except (ValueError, OSError):
            meta = {}
        meta = meta if isinstance(meta, dict) else {}
        try:
            threshold = float(meta.get("threshold") or 0.5)
        except (TypeError, ValueError):
            threshold = 0.5
        phrase = str(meta.get("phrase") or "") or " ".join(w.capitalize() for w in re.split(r"[_\-]+", name) if w)
        out.append({"name": name, "phrase": phrase, "threshold": threshold, "path": f})
    return out


def default_wake_word(words: list[dict[str, Any]]) -> str:
    names = [w["name"] for w in words]
    return config.voice_wakeword if config.voice_wakeword in names else (names[0] if names else "")


async def fetch_url(url: str) -> bytes:
    from aiohttp import ClientSession, ClientTimeout
    async with ClientSession(timeout=ClientTimeout(total=120)) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.read()


async def ensure_pretrained(voice_dir: Path, fetch: Callable[[str], Awaitable[bytes]] = fetch_url) -> list[str]:
    """Downloads the pre-trained wake words that aren't in voice_dir yet; returns the names added."""
    added = []
    for name, phrase in PRETRAINED.items():
        model, meta = voice_dir / f"{name}.onnx", voice_dir / f"{name}.json"
        if not model.is_file():
            try:
                data = await fetch(PRETRAINED_URL.format(name))
            except Exception as e:  # noqa: BLE001 - offline hub: just not offered
                log.warning("Voice: couldn't download the %s wake word: %s", phrase, e)
                continue
            if len(data) < 1024:
                log.warning("Voice: the %s wake word download looks wrong (%d bytes)", phrase, len(data))
                continue
            voice_dir.mkdir(parents=True, exist_ok=True)
            tmp = model.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(model)
            added.append(name)
        if not meta.is_file():
            meta.write_text(json.dumps({"phrase": phrase, "threshold": 0.5}))
    if added:
        log.info("Voice: added the wake words %s", ", ".join(added))
    return added


def clean_settings(raw: Any, base: dict[str, Any] | None = None, wake_names: list[str] | None = None,
                   voice_names: set[str] | None = None) -> tuple[dict[str, Any], list[str]]:
    """voice_names: the voices offered (None = Piper's VOICES)."""
    s = {**DEFAULTS, **(base or {})}
    errors: list[str] = []
    raw = raw if isinstance(raw, dict) else {}
    for key in ("enabled", "allowSensitive"):
        if key in raw:
            if isinstance(raw[key], bool):
                s[key] = raw[key]
            else:
                errors.append(f"{key} is true or false")
    if "sensitivity" in raw:
        if raw["sensitivity"] in SENSITIVITY:
            s["sensitivity"] = raw["sensitivity"]
        else:
            errors.append("Wake sensitivity is low, normal or high")
    if "volume" in raw:
        try:
            s["volume"] = max(0, min(100, int(raw["volume"])))
        except (TypeError, ValueError):
            errors.append("Reply volume is 0-100")
    if "voice" in raw:
        v = str(raw["voice"] or "")
        if v and v not in (voice_names if voice_names is not None else {x["name"] for x in VOICES}):
            errors.append("Pick one of the offered voices")
        else:
            s["voice"] = v
    if "wakeWord" in raw:
        w = str(raw["wakeWord"] or "")
        if w and wake_names is not None and w not in wake_names:
            errors.append("Pick one of the wake words on the hub")
        else:
            s["wakeWord"] = w
    return s, errors


USE_DAYS = 60  # a device's use (for the Whisper hint's order) counts this far back


def name_index(h: "Newman", kitchen: Any = None) -> NameIndex:
    """Every name on the hub (voice/names.py), kept current: devices and rooms on the hub's add / remove /
    rename events, automations when one is saved or deleted; people, dashboards, recipes, the music
    library's names and the connections when their cheap fingerprint changes. Nothing here goes to the
    network - the music names are those the music connection already has."""
    idx = NameIndex()
    uses: dict[str, Any] = {"at": 0.0, "counts": {}}

    def use_counts() -> dict[str, int]:
        if time.time() - uses["at"] > 600:
            uses["at"], uses["counts"] = time.time(), h.store.entity_use_counts(time.time() - USE_DAYS * 86400)
        return uses["counts"]

    def devices() -> list[Name]:
        counts = use_counts()
        # Most used first in the hint; among the unused, the ones that take commands (kinds.py) first.
        return [Name(e.name, "device", e.id, counts.get(e.id, 0) * 10 + (1 if KINDS[e.kind].commands else 0))
                for e in h.hub.all()]

    def rooms() -> list[Name]:
        out: dict[str, Name] = {}
        for e in h.hub.all():
            area = str((e.attrs or {}).get("area") or "").strip()
            if area and area.lower() not in out:
                out[area.lower()] = Name(area, "room", area.lower())
        return list(out.values())

    def people() -> list[Name]:
        return [Name(str(p["name"]), "person", str(p["id"])) for p in h.store.people()]

    def dashboards() -> list[Name]:
        """Pages ("show the climate page") and the dashboards' "show as" names for devices."""
        out = []
        for d in h.store.dashboards():
            out += [Name(str(p.get("name") or ""), "page", str(p.get("id"))) for p in d.get("pages") or []]
            out += [Name(str(n), "device", eid) for eid, n in (d.get("names") or {}).items() if n and h.hub.get(eid)]
        return out

    def automations() -> list[Name]:
        return [Name(str(a.get("name") or ""), "automation", aid) for aid, a in h.engine.autos.items()]

    def book() -> Any:
        return kitchen.source() if kitchen else None

    def recipes() -> list[Name]:
        src = book()
        return [Name(str(x["title"]), "recipe", str(x["id"])) for x in src.summaries()] if src else []

    def music() -> list[Name]:
        m = music_of(h)
        if not m:
            return []
        return [Name(n, slot, f"{slot}:{n.lower()}", plays) for slot, items in m.cached_names().items()
                for n, plays in items if n]

    def connections() -> list[Name]:
        return [Name(str(getattr(i, "name", "") or ""), "connection", iid) for iid, i in h.integrations.instances.items()]

    idx.source("devices", devices)
    idx.source("rooms", rooms)
    idx.source("automations", automations)
    idx.source("people", people, lambda: tuple((p["id"], p["name"]) for p in h.store.people()))
    idx.source("dashboards", dashboards, h.store.dashboard_times)
    idx.source("recipes", recipes, lambda: (id(book()), tuple(x["id"] for x in book().summaries()) if book() else ()))
    idx.source("music", music, lambda: (music_of(h).names_key() if music_of(h) else None))
    idx.source("connections", connections,
               lambda: tuple((iid, getattr(i, "name", "")) for iid, i in h.integrations.instances.items()))

    async def added(_: Any) -> None:
        idx.mark("devices")
        idx.mark("rooms")

    async def changed(old: Any, new: Any) -> None:
        if old is None or old.name != new.name or (old.attrs or {}).get("area") != (new.attrs or {}).get("area"):
            idx.mark("devices")
            idx.mark("rooms")
    h.hub.subscribe_registry(added, added)
    h.hub.subscribe_changes(changed)
    h.engine.listeners.append(lambda: idx.mark("automations"))
    return idx


def automations_for_voice(h: "Newman") -> list[dict[str, Any]]:
    """intents.World.automations: each automation, and whether running it does something kinds.py marks
    `sensitive` (unlock, open, disarm…) - refused by voice unless the tablet allows those."""
    out = []
    for aid, a in h.engine.autos.items():
        sensitive = False
        for act in AD.expand_actions(a.get("actions") or []):
            e = h.hub.get(str(act.get("entity") or ""))
            c = KINDS[e.kind].command(str(act.get("command") or "")) if e else None
            if c is not None and c.sensitive:
                sensitive = True
                break
        out.append({"id": aid, "name": str(a.get("name") or ""), "sensitive": sensitive})
    return out


def add_voice_routes(r: web.RouteTableDef, h: "Newman", *, loc: Any, body: Callable[..., Awaitable[dict[str, Any]]],
                     require_admin: Callable[[web.Request], None],
                     send_to_user: Callable[[str, dict[str, Any]], Awaitable[None]],
                     stt: Any = None, tts: Any = None,
                     fetch: Callable[[str], Awaitable[bytes]] | None = None,
                     kitchen: Any = None) -> tuple[Callable, Callable]:
    """Adds the routes; returns (on_startup, on_cleanup) for the timers' clock. `kitchen`: cook mode
    (recipes.Kitchen), when the hub has it."""
    stt_addr = parse_address(config.voice_stt, 10300)
    tts_addr = parse_address(config.voice_tts, 10200)
    stt = stt or (WyomingClient(*stt_addr) if stt_addr else None)
    voice_dir = Path(config.data_dir) / "voice"

    def docker_connection() -> Any:
        """The Docker connection that has the speech-to-text container (the first one, before it's listed)."""
        found = [i for i in h.integrations.instances.values() if getattr(i, "type", "") == "docker"]
        return next((i for i in found if i.knows(config.voice_stt_container)), None)

    async def stt_ready() -> bool:
        info = await WyomingClient(*stt_addr, timeout=5.0).describe()  # type: ignore[misc]
        return bool(info.get("asr"))

    # Whisper reads the hint only when it starts: restarted when it changes and nobody is talking.
    restarter = WhisperRestarter(voice_dir / "whisper_prompt.txt", config.voice_stt_container, docker_connection,
                                 stt_ready, enabled=bool(config.voice_stt_restart and stt_addr and stt is not None))
    stt = restarter.guard(stt) if stt is not None else None
    if tts is None and config.voice_tts_kind == "openai" and config.voice_tts_url:
        tts = FallbackTTS(OpenAITTS(config.voice_tts_url), WyomingClient(*tts_addr) if tts_addr else None)
    tts = tts or (WyomingClient(*tts_addr) if tts_addr else None)

    async def voice_list() -> list[dict[str, str]]:
        """The voices a tablet can pick: the HTTP service's when it speaks, else Piper's."""
        if hasattr(tts, "voices"):
            got = await tts.voices()
            return got or [{"name": config.voice_name, "label": config.voice_name}]
        return VOICES

    def known_voices() -> set[str]:
        if hasattr(tts, "known_voices"):
            return tts.known_voices() or {config.voice_name}
        return {x["name"] for x in VOICES}
    timers = Timers(h.store._db)
    spoken: dict[str, dict[str, Any]] = {}  # done-announcement text -> audio (a few, kept)

    def saved_settings(uid: str) -> dict[str, Any]:
        s = clean_settings((h.store.settings().get("voiceTablets") or {}).get(uid) or {}, voice_names=set())[0]
        saved = str(((h.store.settings().get("voiceTablets") or {}).get(uid) or {}).get("voice") or "")
        # A voice the current text-to-speech doesn't have (Piper's after switching to Kokoro) = the default.
        s["voice"] = saved if saved in known_voices() else ""
        return s

    def settings_of(uid: str) -> dict[str, Any]:
        """The tablet's settings; wakeWord is the one it listens for (the default when its own choice
        is unset or no longer on the hub - only a choice made is saved)."""
        s = saved_settings(uid)
        words = wake_words(voice_dir)
        if s["wakeWord"] not in {w["name"] for w in words}:
            s["wakeWord"] = default_wake_word(words)
        return s

    def dashboard_of(user: dict[str, Any], session: dict[str, Any]) -> dict[str, Any] | None:
        d = h.store.dashboard(session["dashboard_id"]) if session.get("dashboard_id") else None
        return d or next((x for x in h.store.dashboards(user["id"]) if x["isDefault"]), None)

    def world(v: VoiceViewer) -> I.World:
        d = dashboard_of(v.user, v.session) or {}
        names = d.get("names") or {}
        devices = [I.Device(e.id, e.name, e.kind, e.state, dict(e.attrs or {}), e.available, names.get(e.id, ""))
                   for e in h.hub.all()]
        people = [] if v.user.get("role") == "guest" else \
            [loc.person_json(p, v.user, v.session) for p in h.store.people()]
        m = music_of(h)
        mine = m.player_id(v.user["id"]) if m and v.user["id"] in m.players else None
        book = kitchen.source() if kitchen else None
        return I.World(devices=devices, people=people,
                       pages=[{"id": p["id"], "name": p["name"]} for p in d.get("pages") or []],
                       timers=timers.list(v.user["id"]), allow_sensitive=bool(v.settings.get("allowSensitive")),
                       music=next((x for x in devices if x.id == mine), None),
                       music_offer=bool(mine and m.offer(v.user["id"])),
                       recipes=[{"id": x["id"], "title": x["title"]} for x in book.summaries()] if book else None,
                       cook=kitchen.voice_json(v.user["id"]) if kitchen else None,
                       automations=None if v.user.get("role") == "guest" else automations_for_voice(h))

    def device(eid: str) -> I.Device | None:
        e = h.hub.get(eid)
        return I.Device(e.id, e.name, e.kind, e.state, dict(e.attrs or {}), e.available) if e else None

    async def play_music(v: VoiceViewer, request: dict[str, Any]) -> str:
        """Voice: find and play on this tablet's player (the music connection)."""
        m = music_of(h)
        if not m or not m.client:
            raise ActionError("Music isn't connected right now.")
        try:
            m.ensure_player(v.user["id"], v.user.get("name") or "")
            say = await m.play_request(v.user["id"], request, ask=True)
        except CommandError as e:
            raise ActionError(str(e)) from e
        except Exception as e:  # noqa: BLE001 - Navidrome unreachable, a bad answer…
            log.warning("Voice music: %s", e)
            raise ActionError("Sorry, I couldn't reach the music server.") from e
        h.store.log_event("command", f"Play music on {h.hub.name(m.player_id(v.user['id']))}", say,
                          m.player_id(v.user["id"]))
        return say

    async def music_answer(v: VoiceViewer, yes: bool) -> str:
        """Voice: "yes" / "no" to "Did you mean …?" (the music connection asked)."""
        m = music_of(h)
        if not m or not m.client:
            raise ActionError("Music isn't connected right now.")
        try:
            say = await m.answer_offer(v.user["id"], yes)
        except CommandError as e:
            raise ActionError(str(e)) from e
        except Exception as e:  # noqa: BLE001 - Navidrome unreachable, a bad answer…
            log.warning("Voice music: %s", e)
            raise ActionError("Sorry, I couldn't reach the music server.") from e
        if yes:
            h.store.log_event("command", f"Play music on {h.hub.name(m.player_id(v.user['id']))}", say,
                              m.player_id(v.user["id"]))
        return say

    def today(v: VoiceViewer) -> dict[str, Any]:
        st = h.store.settings()
        mine = v.session.get("kind") == "app"
        return T.build(h.hub, st, person_id=v.user.get("person_id") if mine else None,
                       dismissed=st.get("todayDismissed") or {})

    async def cook(v: VoiceViewer, request: dict[str, Any]) -> dict[str, Any]:
        """Voice: cook mode on this tablet (recipes.py)."""
        return await kitchen.voice(v.user["id"], request)

    async def run_automation(v: VoiceViewer, aid: str) -> str:
        """Voice: "run Good Night" - like the automation's Run button (now, whatever its conditions say)."""
        auto = h.engine.autos.get(aid)
        if not auto:
            raise ActionError("That automation isn't there any more.")
        trig: dict[str, Any] = {"type": auto["trigger"]["type"], "manual": True}
        if auto["trigger"]["type"] == "event":
            trig["entity"] = auto["trigger"].get("entity")
        await h.engine.trigger(auto, trig, force=True)
        return f"Okay, running {auto['name']}."

    names = name_index(h, kitchen)
    hint = HintFile(names, voice_dir / "whisper_prompt.txt")
    pipeline = Pipeline(Sources(world=world, command=h.engine.command, today=today, log_event=h.store.log_event,
                                music=play_music, music_answer=music_answer, device=device, cook=cook if kitchen else None,
                                names=names, run_automation=run_automation),
                        timers, stt, tts, voice=config.voice_name, language=config.voice_language)

    async def timers_changed(uid: str) -> None:
        await send_to_user(uid, {"type": "timers", "now": time.time(), "timers": timers.list(uid)})
    timers.listeners.append(timers_changed)

    def viewer(req: web.Request) -> VoiceViewer:
        u = req["user"]
        return VoiceViewer(u, req["session"], settings_of(u["id"]))

    # -- wake word ---------------------------------------------------------------------------------

    hashes: dict[tuple[str, float, int], str] = {}  # (path, mtime, size) -> sha256, so files aren't re-read

    def file_hash(f: Path) -> str:
        st = f.stat()
        key = (str(f), st.st_mtime, st.st_size)
        if key not in hashes:
            hashes[key] = hashlib.sha256(f.read_bytes()).hexdigest()
        return hashes[key]

    def wake_json(settings: dict[str, Any]) -> dict[str, Any] | None:
        """The wake word this tablet listens for (settings_of already picked it), or None."""
        features = {n: voice_dir / f"{n}.onnx" for n in WAKE_FILES}
        word = next((w for w in wake_words(voice_dir) if w["name"] == settings.get("wakeWord")), None)
        if not word or not all(f.is_file() for f in features.values()):
            return None
        # A new version (another wake word, a retrained model, a new threshold) makes the wall app
        # download the files again.
        sha = hashlib.sha256()
        for f in (*features.values(), word["path"]):
            sha.update(file_hash(f).encode())
        sha.update(f"{word['name']}|{word['threshold']}".encode())
        return {"name": word["name"], "phrase": word["phrase"], "version": sha.hexdigest()[:16],
                "threshold": round(max(0.1, min(0.95, word["threshold"] + SENSITIVITY[settings["sensitivity"]])), 3),
                "files": {**{n: f"api/voice/wakeword/{n}.onnx" for n in WAKE_FILES},
                          "wakeword": f"api/voice/wakeword/{word['name']}.onnx"}}

    @r.get("/api/voice")
    async def voice_info(req: web.Request) -> web.Response:
        v = viewer(req)
        return web.json_response({"available": stt is not None, "speaks": tts is not None, "settings": v.settings,
                                  "wakeword": wake_json(v.settings)})

    @r.get("/api/voice/wakeword")
    async def wakeword(req: web.Request) -> web.Response:
        v = viewer(req)
        w = wake_json(v.settings)
        if not w:
            return web.json_response({"error": "No wake-word model on the hub (see README → Voice)"}, status=404)
        return web.json_response({**w, "enabled": bool(v.settings["enabled"] and stt is not None)})

    @r.get("/api/voice/wakewords")
    async def wakewords(_: web.Request) -> web.Response:
        words = wake_words(voice_dir)
        return web.json_response({"default": default_wake_word(words),
                                  "wakeWords": [{"name": w["name"], "phrase": w["phrase"]} for w in words]})

    @r.get("/api/voice/wakeword/{name}")
    async def wakeword_file(req: web.Request) -> web.StreamResponse:
        files = {**{n: voice_dir / f"{n}.onnx" for n in WAKE_FILES},
                 **{w["name"]: w["path"] for w in wake_words(voice_dir)}}
        name = req.match_info["name"].removesuffix(".onnx")
        if name not in files or not files[name].is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(files[name], headers={"Content-Type": "application/octet-stream",
                                                      "Cache-Control": "private, max-age=3600"})

    # -- talking -----------------------------------------------------------------------------------

    async def read_audio(req: web.Request, max_bytes: int = MAX_AUDIO_BYTES) -> tuple[bytes, int]:
        chunks, size = [], 0
        async for chunk in req.content.iter_chunked(64 * 1024):
            size += len(chunk)
            if size > max_bytes + 44:
                raise web.HTTPRequestEntityTooLarge(max_size=max_bytes, actual_size=size)
            chunks.append(chunk)
        data = b"".join(chunks)
        if data[:4] == b"RIFF":
            pcm, rate, width, channels = pcm_from_wav(data)
            if width != 2 or channels != 1:
                raise web.HTTPBadRequest(text='{"error": "Send 16-bit mono audio"}', content_type="application/json")
            return pcm, rate
        # ?rate=, or "audio/L16; rate=16000"
        m = re.search(r"rate=(\d+)", req.headers.get("Content-Type", ""))
        try:
            rate = int(req.query.get("rate") or (m.group(1) if m else "16000"))
        except ValueError:
            rate = 16000
        return data, max(8000, min(48000, rate))

    @r.post("/api/voice/utterance")
    async def utterance(req: web.Request) -> web.Response:
        """Something said to Newman. Body: raw PCM16 (16 kHz mono; ?rate= otherwise), a WAV file, or
        JSON {"text"} (typed - no speech-to-text). ?speak=0 skips the spoken reply."""
        v = viewer(req)
        speak = req.query.get("speak") != "0"
        try:
            if req.content_type == "application/json":
                text = str((await body(req)).get("text") or "")[:500]
                out = await pipeline.handle(v, text=text, speak=speak)
            else:
                pcm, rate = await read_audio(req)
                if len(pcm) < 3200:
                    return web.json_response({"heard": "", "reply": "", "action": {"type": "none"}, "audio": None})
                out = await pipeline.handle(v, pcm=pcm, rate=rate, speak=speak)
        except VoiceUnavailable as e:
            return web.json_response({"error": str(e)}, status=503)
        except WyomingError as e:
            log.warning("Voice: %s", e)
            return web.json_response({"error": f"Couldn't reach the speech service: {e}"}, status=502)
        log.info("Voice (%s): %r -> %r in %s", v.user.get("name"), out["heard"], out["reply"], out.get("timings"))
        return web.json_response(out)

    # -- timers ------------------------------------------------------------------------------------

    @r.get("/api/voice/timers")
    async def timers_list(req: web.Request) -> web.Response:
        return web.json_response({"now": time.time(), "timers": timers.list(req["user"]["id"])})

    @r.delete("/api/voice/timers/{tid}")
    async def timers_remove(req: web.Request) -> web.Response:
        n = await timers.remove(req["user"]["id"], [req.match_info["tid"]])
        return web.json_response({"ok": bool(n)}, status=200 if n else 404)

    @r.get("/api/voice/timers/{tid}/done")
    async def timer_done(req: web.Request) -> web.Response:
        t = timers.get(req["user"]["id"], req.match_info["tid"])
        if not t:
            return web.json_response({"error": "No such timer"}, status=404)
        text = t["doneText"]
        voice = settings_of(req["user"]["id"]).get("voice") or config.voice_name
        audio = None
        if tts is not None:
            key = f"{voice}|{text}"
            audio = spoken.get(key)
            if audio is None:
                try:
                    audio = await pipeline.speech(text, voice)
                    if len(spoken) > 20:
                        spoken.pop(next(iter(spoken)))
                    spoken[key] = audio
                except (WyomingError, VoiceUnavailable, OSError) as e:
                    log.warning("Timer announcement: %s", e)
        return web.json_response({"text": text, "audio": audio})

    # -- settings (per wall tablet account) ----------------------------------------------------------

    @r.get("/api/voice/voices")
    async def voices(_: web.Request) -> web.Response:
        return web.json_response({"default": config.voice_name, "voices": await voice_list()})

    def tablet_or_404(uid: str) -> dict[str, Any]:
        u = h.store.user(uid)
        if not u:
            raise web.HTTPNotFound(text='{"error": "Unknown account"}', content_type="application/json")
        return u

    @r.get("/api/voice/settings/{uid}")
    async def settings_get(req: web.Request) -> web.Response:
        require_admin(req)
        uid = req.match_info["uid"]
        tablet_or_404(uid)
        return web.json_response(settings_of(uid))

    @r.patch("/api/voice/settings/{uid}")
    async def settings_set(req: web.Request) -> web.Response:
        require_admin(req)
        uid = req.match_info["uid"]
        tablet_or_404(uid)
        s, errors = clean_settings(await body(req), saved_settings(uid), [w["name"] for w in wake_words(voice_dir)],
                                   {v["name"] for v in await voice_list()})
        if errors:
            return web.json_response({"error": "; ".join(errors)}, status=400)
        all_ = dict(h.store.settings().get("voiceTablets") or {})
        all_[uid] = s
        h.store.set_setting("voiceTablets", all_)
        await send_to_user(uid, {"type": "voice-settings"})
        return web.json_response(settings_of(uid))

    # -- the Whisper hint (voice/hint.py) ---------------------------------------------------------------

    @r.get("/api/voice/hint")
    async def hint_get(req: web.Request) -> web.Response:
        """The names Whisper is told to expect, and whether the file it reads has them yet."""
        require_admin(req)
        return web.json_response({**hint.info(), "whisper": restarter.info()})

    async def warm_music() -> None:
        """The music library's artist and album names (kept by the music connection) for the hint."""
        m = music_of(h)
        if m and m.client:
            await m.artist_index()
            await m.album_index()

    # -- teaching and training "Hey Newman" (voice/teach.py) -----------------------------------------

    add_teach_routes(r, h, voice_dir, require_admin=require_admin, send_to_user=send_to_user,
                     read_audio=read_audio, body=body)

    task: dict[str, asyncio.Task] = {}

    async def startup(_: web.Application) -> None:
        loop = asyncio.get_running_loop()
        task["t"] = loop.create_task(timers.run(), name="voice-timers")
        task["w"] = loop.create_task(ensure_pretrained(voice_dir, fetch or fetch_url), name="voice-wake-words")
        if hasattr(tts, "voices"):
            task["v"] = loop.create_task(tts.voices(), name="voice-voices")
        task["h"] = loop.create_task(hint.run(warm=warm_music), name="voice-hint-file")
        if restarter.enabled:
            task["r"] = loop.create_task(restarter.run(), name="voice-whisper-restart")

    async def cleanup(_: web.Application) -> None:
        hint.stop()
        for t in (task.pop("t", None), task.pop("w", None), task.pop("v", None), task.pop("h", None),
                  task.pop("r", None)):
            if t:
                t.cancel()

    return startup, cleanup
