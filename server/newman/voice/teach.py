"""Teaching and training "Hey Newman" with the household's own voices (README → Voice → Teaching and
training "Hey Newman"; API.md → Voice → Teaching and training). Registered by voice/api.py.

Teach: an admin starts teaching on a wall tablet (a {"type": "voice-teach"} push); the tablet records
"Hey Newman" ~30 times and half a minute of ordinary talking with its own microphone and posts each
clip here. Clips are kept as 16 kHz WAVs in DATA_DIR/voice/teach/<person>/<positive|negative>/ and
never leave the hub.

Train: the newman-wakeword-trainer companion container watches DATA_DIR/voice/train/requests/ (a
request file written here), keeps DATA_DIR/voice/train/status.json current and leaves its result in
DATA_DIR/voice/train/candidates/<name>.onnx + <name>.metrics.json. "Use it" installs a candidate as
hey_newman.onnx (the old one kept as hey_newman.previous.*, for Undo); its new version makes every
tablet download it again.

    POST   /api/voice/teach/start               {"tablet", "person"} start teaching on a tablet (admin)
    POST   /api/voice/teach/stop                end it (the tablet itself, or an admin with {"tablet"})
    POST   /api/voice/teach/clips?person=&kind= one recording (PCM16 16 kHz / WAV) - the teaching tablet or an admin
    GET    /api/voice/teach                     the recordings, per person (admin)
    GET    /api/voice/teach/{person}/{kind}/{clip}.wav   one recording (admin)
    DELETE /api/voice/teach/{person}            all of someone's recordings (admin)
    DELETE /api/voice/teach/{person}/{kind}/{clip}       one (admin, or the teaching tablet: "Redo last")
    GET    /api/voice/train                     trainer status, candidates, the model in use (admin)
    POST   /api/voice/train                     ask the trainer for a run (admin)
    POST   /api/voice/train/use/{candidate}     install a candidate as hey_newman (admin)
    POST   /api/voice/train/undo                put the previous hey_newman back (admin)
    DELETE /api/voice/train/candidates/{candidate}   keep the current one; drop the candidate (admin)
"""
from __future__ import annotations

import array
import json
import math
import re
import secrets
import shutil
import sys
import time
import wave
from io import BytesIO
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import web

WORD = "hey_newman"  # the wake word the trainer makes
RATE = 16000
KINDS = ("positive", "negative")
LIMITS_MS = {"positive": (400, 6000), "negative": (5000, 35000)}  # how long a clip may be
MIN_VOICED_MS = {"positive": 200, "negative": 2000}  # how much of it must be someone speaking
MIN_PEAK_RMS = 250.0  # ≈ -42 dBFS on the loudest 20 ms: quieter than that is silence / a dead mic
MIN_TRAIN_CLIPS = 20
TEACH_SESSION_S = 3 * 3600
TARGET_CLIPS = 30
# The first "Hey Newman" (computer voices only), measured on the NAS: recall on held-out synthetic
# clips 0.43, 2.5 false wake-ups per hour of everyday audio, threshold 0.5.
FIRST_MODEL_METRICS = {"name": "hey_newman (first version)", "threshold": 0.5, "syntheticRecall": 0.43,
                       "falseWakesPerHour": 2.5, "realRecall": None, "byPerson": [],
                       "note": "Trained on computer voices only"}
CLIP_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9]{4,8}$")
CANDIDATE = re.compile(r"^hey_newman-[0-9]{8}-[0-9]{6}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9\-]{0,39}$")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")[:40].strip("-")
    return s or "someone"


def samples_of(pcm: bytes) -> array.array:
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        a.byteswap()
    return a


def check_clip(pcm: bytes, kind: str, rate: int = RATE) -> tuple[int, str | None]:
    """(length in ms, None) for a usable recording, else (ms, why not) in plain words."""
    if rate != RATE:
        return 0, "Send 16 kHz audio"
    s = samples_of(pcm)
    ms = len(s) * 1000 // RATE
    lo, hi = LIMITS_MS[kind]
    if ms < lo:
        return ms, "That was too short - say it again" if kind == "positive" else "That was too short - keep talking a bit longer"
    if ms > hi:
        return ms, "That was too long"
    f = RATE // 50  # 20 ms frames
    rms = [math.sqrt(sum(x * x for x in s[i:i + f]) / f) for i in range(0, len(s) - f + 1, f)]
    peak = max(rms) if rms else 0.0
    if peak < MIN_PEAK_RMS:
        return ms, "That was too quiet - move closer or speak up"
    floor = sorted(rms)[len(rms) // 10]
    # Louder than the room (but a clip that's all talking has no quiet "room" to measure).
    voiced = sum(1 for r in rms if r > max(peak * 0.1, min(floor * 2.5, peak * 0.5))) * 20
    if voiced < MIN_VOICED_MS[kind]:
        return ms, "Didn't hear a voice in that - try again"
    return ms, None


def wav_bytes(pcm: bytes, rate: int = RATE) -> bytes:
    out = BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


def wav_ms(path: Path) -> int:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() * 1000 // max(1, w.getframerate())
    except (OSError, wave.Error, EOFError):
        return 0


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


class Recordings:
    """DATA_DIR/voice/teach/<slug>/{person.json, positive/*.wav, negative/*.wav}."""

    def __init__(self, voice_dir: Path):
        self.root = voice_dir / "teach"

    def person_dir(self, name: str) -> Path:
        d = self.root / slugify(name)
        if not (d / "person.json").is_file():
            d.mkdir(parents=True, exist_ok=True)
            write_json(d / "person.json", {"name": str(name).strip()[:60] or "Someone"})
        return d

    def add(self, name: str, kind: str, pcm: bytes) -> dict[str, Any]:
        d = self.person_dir(name) / kind
        d.mkdir(parents=True, exist_ok=True)
        cid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        (d / f"{cid}.wav").write_bytes(wav_bytes(pcm))
        return {"id": cid, "kind": kind, "person": slugify(name), "ms": len(pcm) * 1000 // (2 * RATE)}

    def clip_path(self, slug: str, kind: str, cid: str) -> Path | None:
        if not SLUG.match(slug) or kind not in KINDS or not CLIP_ID.match(cid):
            return None
        p = self.root / slug / kind / f"{cid}.wav"
        return p if p.is_file() else None

    def people(self, clips: bool = True) -> list[dict[str, Any]]:
        out = []
        for d in sorted(p for p in self.root.glob("*") if p.is_dir()) if self.root.is_dir() else []:
            meta = read_json(d / "person.json", {}) or {}
            entry: dict[str, Any] = {"slug": d.name, "name": meta.get("name") or d.name, "positive": 0, "negative": 0,
                                     "seconds": 0.0}
            items = []
            for kind in KINDS:
                for f in sorted((d / kind).glob("*.wav")):
                    ms = wav_ms(f)
                    entry[kind] += 1
                    entry["seconds"] += ms / 1000
                    items.append({"id": f.stem, "kind": kind, "ms": ms, "at": f.stat().st_mtime})
            entry["seconds"] = round(entry["seconds"], 1)
            if clips:
                entry["clips"] = items
            out.append(entry)
        return out

    def summary(self) -> dict[str, Any]:
        people = self.people()
        return {"people": people, "minClips": MIN_TRAIN_CLIPS, "target": TARGET_CLIPS,
                "totals": {"positive": sum(p["positive"] for p in people), "negative": sum(p["negative"] for p in people),
                           "minutes": round(sum(p["seconds"] for p in people) / 60, 1)}}


class Training:
    """The files shared with the newman-wakeword-trainer container (DATA_DIR/voice/train/) and the
    installed model (DATA_DIR/voice/hey_newman.*)."""

    def __init__(self, voice_dir: Path):
        self.voice = voice_dir
        self.root = voice_dir / "train"
        self.requests = self.root / "requests"
        self.candidates = self.root / "candidates"

    def status(self) -> dict[str, Any]:
        s = read_json(self.root / "status.json", {}) or {}
        s = s if isinstance(s, dict) else {}
        now = time.time()
        age = now - float(s.get("updatedAt") or 0)
        running = s.get("state") == "running"
        out = {"state": s.get("state") or "idle", "stage": s.get("stage") or "", "percent": s.get("percent") or 0,
               "startedAt": s.get("startedAt"), "finishedAt": s.get("finishedAt"), "eta": s.get("eta"),
               "message": s.get("message") or "", "candidate": s.get("candidate"),
               # A heartbeat every 30 s while idle; progress at least every few minutes while training.
               "trainerOnline": bool(s) and age < (1800 if running else 120)}
        return out

    def pending(self) -> bool:
        return self.requests.is_dir() and any(self.requests.glob("*.json"))

    def request(self, by: str, steps: int | None) -> dict[str, Any]:
        self.requests.mkdir(parents=True, exist_ok=True)
        rid = time.strftime("%Y%m%d-%H%M%S")
        req: dict[str, Any] = {"id": rid, "at": time.time(), "by": by}
        if steps:
            req["steps"] = steps
        write_json(self.requests / f"{rid}.json", req)
        return req

    def candidate_list(self) -> list[dict[str, Any]]:
        out = []
        for f in sorted(self.candidates.glob("hey_newman-*.onnx"), reverse=True) if self.candidates.is_dir() else []:
            if not CANDIDATE.match(f.stem):
                continue
            m = read_json(self.candidates / f"{f.stem}.metrics.json", {}) or {}
            out.append({"name": f.stem, "createdAt": m.get("createdAt") or f.stat().st_mtime, "metrics": m})
        return out

    def current(self) -> dict[str, Any] | None:
        if not (self.voice / f"{WORD}.onnx").is_file():
            return None
        m = read_json(self.voice / f"{WORD}.metrics.json")
        return m if isinstance(m, dict) else dict(FIRST_MODEL_METRICS)

    def previous(self) -> dict[str, Any] | None:
        if not (self.voice / f"{WORD}.previous.onnx").is_file():
            return None
        m = read_json(self.voice / f"{WORD}.previous.metrics.json")
        return m if isinstance(m, dict) else dict(FIRST_MODEL_METRICS)

    def _files(self, tag: str = "") -> dict[str, Path]:
        base = WORD + (f".{tag}" if tag else "")
        return {ext: self.voice / f"{base}.{ext}" for ext in ("onnx", "json", "metrics.json")}

    def use(self, name: str) -> dict[str, Any]:
        model = self.candidates / f"{name}.onnx"
        metrics = read_json(self.candidates / f"{name}.metrics.json", {}) or {}
        cur, prev = self._files(), self._files("previous")
        if cur["onnx"].is_file():
            shutil.copyfile(cur["onnx"], prev["onnx"])
            if cur["json"].is_file():
                shutil.copyfile(cur["json"], prev["json"])
            else:
                write_json(prev["json"], {"phrase": "Hey Newman", "threshold": 0.5})
            write_json(prev["metrics.json"], read_json(cur["metrics.json"]) or FIRST_MODEL_METRICS)
        tmp = cur["onnx"].with_name(cur["onnx"].name + ".part")
        shutil.copyfile(model, tmp)
        tmp.replace(cur["onnx"])
        try:
            threshold = float(metrics.get("threshold") or 0.5)
        except (TypeError, ValueError):
            threshold = 0.5
        write_json(cur["json"], {"phrase": "Hey Newman", "threshold": round(threshold, 3)})
        write_json(cur["metrics.json"], {**metrics, "name": name, "installedAt": time.time()})
        for f in (model, self.candidates / f"{name}.metrics.json"):
            f.unlink(missing_ok=True)
        return self.current() or {}

    def undo(self) -> dict[str, Any]:
        """Swaps the installed model with the previous one (so Undo twice is back where it was)."""
        cur, prev = self._files(), self._files("previous")
        tmp = self._files("swap")
        for ext in cur:
            if cur[ext].is_file():
                cur[ext].replace(tmp[ext])
            if prev[ext].is_file():
                prev[ext].replace(cur[ext])
            if tmp[ext].is_file():
                tmp[ext].replace(prev[ext])
        return self.current() or {}


def add_teach_routes(r: web.RouteTableDef, h: Any, voice_dir: Path, *, require_admin: Callable[[web.Request], None],
                     send_to_user: Callable[[str, dict[str, Any]], Awaitable[None]],
                     read_audio: Callable[..., Awaitable[tuple[bytes, int]]],
                     body: Callable[..., Awaitable[dict[str, Any]]]) -> None:
    rec = Recordings(voice_dir)
    tr = Training(voice_dir)
    teaching: dict[str, dict[str, Any]] = {}  # tablet account id -> {"person", "slug", "until"}

    def err(status: int, msg: str) -> web.Response:
        return web.json_response({"error": msg}, status=status)

    def is_tablet(req: web.Request) -> bool:
        return req["session"].get("kind") == "tablet"

    def session_of(req: web.Request) -> dict[str, Any] | None:
        t = teaching.get(req["user"]["id"])
        if t and t["until"] < time.time():
            teaching.pop(req["user"]["id"], None)
            return None
        return t

    async def tell_tablets(msg: dict[str, Any]) -> None:
        for u in h.store.users():
            if u.get("role") == "tablet":
                await send_to_user(u["id"], msg)

    # -- teaching ------------------------------------------------------------------------------------

    @r.post("/api/voice/teach/start")
    async def teach_start(req: web.Request) -> web.Response:
        require_admin(req)
        data = await body(req)
        uid, person = str(data.get("tablet") or ""), str(data.get("person") or "").strip()[:60]
        u = h.store.user(uid)
        if not u or u.get("role") != "tablet":
            return err(400, "Pick a wall tablet")
        if not person:
            return err(400, "Who's recording?")
        teaching[uid] = {"person": person, "slug": slugify(person), "until": time.time() + TEACH_SESSION_S}
        done = next((p["positive"] for p in rec.people(clips=False) if p["slug"] == slugify(person)), 0)
        await send_to_user(uid, {"type": "voice-teach", "person": person, "slug": slugify(person), "target": TARGET_CLIPS,
                                "have": done})
        return web.json_response({"ok": True, "person": person, "slug": slugify(person), "tablet": uid})

    @r.post("/api/voice/teach/stop")
    async def teach_stop(req: web.Request) -> web.Response:
        if is_tablet(req):
            teaching.pop(req["user"]["id"], None)
            return web.json_response({"ok": True})
        require_admin(req)
        uid = str((await body(req)).get("tablet") or "")
        teaching.pop(uid, None)
        if uid:
            await send_to_user(uid, {"type": "voice-teach-stop"})
        return web.json_response({"ok": True})

    @r.post("/api/voice/teach/clips")
    async def teach_clip(req: web.Request) -> web.Response:
        person, kind = str(req.query.get("person") or "").strip()[:60], req.query.get("kind") or "positive"
        if kind not in KINDS:
            return err(400, "kind is positive or negative")
        if is_tablet(req):
            t = session_of(req)
            if not t or t["slug"] != slugify(person or t["person"]):
                return err(403, "Start teaching from the Newman dashboard first")
            person = person or t["person"]
        else:
            require_admin(req)
        if not person:
            return err(400, "Who's recording?")
        pcm, rate = await read_audio(req, max_bytes=LIMITS_MS["negative"][1] * RATE * 2 // 1000)
        ms, why = check_clip(pcm, kind, rate)
        if why:
            return web.json_response({"ok": False, "error": why, "ms": ms}, status=422)
        clip = rec.add(person, kind, pcm)
        p = next((x for x in rec.people(clips=False) if x["slug"] == clip["person"]), {})
        return web.json_response({"ok": True, "clip": clip, "positive": p.get("positive", 0), "negative": p.get("negative", 0)})

    @r.get("/api/voice/teach")
    async def teach_list(req: web.Request) -> web.Response:
        require_admin(req)
        now = time.time()
        out = rec.summary()
        out["teaching"] = [{"tablet": uid, "person": t["person"]} for uid, t in teaching.items() if t["until"] > now]
        return web.json_response(out)

    @r.get("/api/voice/teach/{slug}/{kind}/{cid}.wav")
    async def teach_audio(req: web.Request) -> web.StreamResponse:
        require_admin(req)
        p = rec.clip_path(req.match_info["slug"], req.match_info["kind"], req.match_info["cid"])
        if not p:
            raise web.HTTPNotFound()
        return web.FileResponse(p, headers={"Content-Type": "audio/wav", "Cache-Control": "private, no-store"})

    @r.delete("/api/voice/teach/{slug}")
    async def teach_forget(req: web.Request) -> web.Response:
        require_admin(req)
        slug = req.match_info["slug"]
        d = rec.root / slug
        if not SLUG.match(slug) or not d.is_dir():
            return err(404, "No recordings for them")
        shutil.rmtree(d)
        return web.json_response({"ok": True})

    @r.delete("/api/voice/teach/{slug}/{kind}/{cid}")
    async def teach_remove(req: web.Request) -> web.Response:
        slug = req.match_info["slug"]
        if is_tablet(req):
            t = session_of(req)
            if not t or t["slug"] != slug:
                return err(403, "Start teaching from the Newman dashboard first")
        else:
            require_admin(req)
        p = rec.clip_path(slug, req.match_info["kind"], req.match_info["cid"])
        if not p:
            return err(404, "No such recording")
        p.unlink()
        return web.json_response({"ok": True})

    # -- training ------------------------------------------------------------------------------------

    def train_json() -> dict[str, Any]:
        people = rec.people(clips=False)
        return {"status": tr.status(), "pending": tr.pending(), "minClips": MIN_TRAIN_CLIPS,
                "recordings": {"positive": sum(p["positive"] for p in people),
                               "negative": sum(p["negative"] for p in people), "people": len(people)},
                "current": tr.current(), "previous": tr.previous(), "candidates": tr.candidate_list()}

    @r.get("/api/voice/train")
    async def train_get(req: web.Request) -> web.Response:
        require_admin(req)
        return web.json_response(train_json())

    @r.post("/api/voice/train")
    async def train_start(req: web.Request) -> web.Response:
        require_admin(req)
        data = await body(req)
        if tr.status()["state"] == "running" or tr.pending():
            return err(409, "Training is already under way")
        n = sum(p["positive"] for p in rec.people(clips=False))
        if n < MIN_TRAIN_CLIPS:
            return err(400, f"Record at least {MIN_TRAIN_CLIPS} “Hey Newman” clips first ({n} so far)")
        steps = None
        if data.get("steps") is not None:  # a shorter run (for trying the pipeline out)
            try:
                steps = max(500, min(200000, int(data["steps"])))
            except (TypeError, ValueError):
                return err(400, "steps is a number")
        tr.request(req["user"].get("name") or "", steps)
        return web.json_response(train_json())

    @r.post("/api/voice/train/use/{name}")
    async def train_use(req: web.Request) -> web.Response:
        require_admin(req)
        name = req.match_info["name"]
        if not CANDIDATE.match(name) or not (tr.candidates / f"{name}.onnx").is_file():
            return err(404, "No such new version")
        tr.use(name)
        await tell_tablets({"type": "voice-settings"})
        return web.json_response(train_json())

    @r.post("/api/voice/train/undo")
    async def train_undo(req: web.Request) -> web.Response:
        require_admin(req)
        if not tr.previous():
            return err(404, "There's no earlier version to go back to")
        tr.undo()
        await tell_tablets({"type": "voice-settings"})
        return web.json_response(train_json())

    @r.delete("/api/voice/train/candidates/{name}")
    async def train_drop(req: web.Request) -> web.Response:
        require_admin(req)
        name = req.match_info["name"]
        if not CANDIDATE.match(name) or not (tr.candidates / f"{name}.onnx").is_file():
            return err(404, "No such new version")
        for f in (tr.candidates / f"{name}.onnx", tr.candidates / f"{name}.metrics.json"):
            f.unlink(missing_ok=True)
        return web.json_response(train_json())
