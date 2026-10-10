"""One spoken request, end to end: audio -> speech-to-text -> intent -> action -> reply -> speech.

    result = await pipeline.handle(pcm=..., viewer=VoiceViewer(...))   # or text="…" (no speech-to-text)
    -> {"heard", "reply", "action", "audio": {"type": "audio/wav", "data": base64} | None, "timings"}

The speech services are injected (Wyoming clients in production, fakes in tests). Nothing about the
audio is kept: the activity log gets the words heard and what happened.

A name the parser couldn't find as heard ("run good nite", "where is ma seemo") gets a second look against
every name the hub knows of the kind the request needs (names.py): sure -> done as corrected; fairly sure ->
"Did you mean Massimo?", and a "yes" within OFFER_S does it.
"""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Protocol

from ..automations.engine import ActionError
from . import intents as I
from . import names as N
from .timers import Timers

log = logging.getLogger(__name__)


class SpeechToText(Protocol):
    async def transcribe(self, pcm: bytes, rate: int = 16000, width: int = 2, channels: int = 1,
                         language: str | None = "en") -> str: ...


class TextToSpeech(Protocol):
    async def synthesize(self, text: str, voice: str | None = None, speaker: str | None = None) -> Any: ...


class VoiceUnavailable(Exception):
    pass


class SpeechRestarting(Exception):
    """Speech-to-text is restarting (to read a new Whisper hint, whisper_restart.py) and isn't back yet: the
    request gets this as its reply, not an error."""

    def __init__(self, reply: str = "One moment, I'm just getting ready. Please ask me again in a few seconds.") -> None:
        super().__init__(reply)


@dataclass
class VoiceViewer:
    """Who is talking: the signed-in account and session (a wall tablet's, usually)."""
    user: dict[str, Any]
    session: dict[str, Any]
    settings: dict[str, Any] = field(default_factory=dict)  # this account's voice settings


@dataclass
class Sources:
    """What the pipeline reads and does, so tests can hand in fakes."""
    world: Callable[[VoiceViewer], I.World]
    command: Callable[[str, str, str, Any], Awaitable[None]]  # (entity id, command, who, value)
    today: Callable[[VoiceViewer], dict[str, Any]]
    log_event: Callable[..., None]
    # Find and play music on this tablet's player: (viewer, {title, artist, album, playlist, query,
    # shuffle, random}) -> what to say ("Playing Jump by Van Halen."); raises ActionError when nothing fits.
    # Not sure of the name heard, it asks instead ("Did you mean Bed of Roses by Bon Jovi?" - also an ActionError).
    music: Callable[[VoiceViewer, dict[str, Any]], Awaitable[str]] | None = None
    # The answer to that question: (viewer, yes) -> what to say ("Playing Bed of Roses by Bon Jovi.").
    music_answer: Callable[[VoiceViewer, bool], Awaitable[str]] | None = None
    # A device as it is now (after a command): entity id -> intents.Device or None.
    device: Callable[[str], I.Device | None] | None = None
    # Cook mode on this tablet (recipes.Kitchen.voice): (viewer, intent value) -> {"reply", "timer"?: {"seconds",
    # "label"}} - a timer is started here, with the other timers, only after a "yes".
    cook: Callable[[VoiceViewer, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None
    # Every name the hub knows (names.NameIndex): a name heard a little wrong is matched against these.
    names: N.NameIndex | None = None
    # Run an automation now: (viewer, automation id) -> what to say ("Okay, running Good Night.").
    run_automation: Callable[[VoiceViewer, str], Awaitable[str]] | None = None


OFFER_S = 60  # how long "Did you mean …?" waits for a yes


class Pipeline:
    def __init__(self, sources: Sources, timers: Timers, stt: SpeechToText | None, tts: TextToSpeech | None,
                 voice: str = "", language: str = "en", clock: Callable[[], float] = time.time) -> None:
        self.src, self.timers, self.stt, self.tts = sources, timers, stt, tts
        self.voice, self.language, self.clock = voice, language, clock
        self.offers: dict[str, dict[str, Any]] = {}  # uid -> {"text": the corrected request, "name", "at"}

    async def handle(self, viewer: VoiceViewer, pcm: bytes | None = None, rate: int = 16000,
                     text: str | None = None, speak: bool = True) -> dict[str, Any]:
        t0 = time.perf_counter()
        timings: dict[str, int] = {}
        busy = ""
        if text is None:
            if self.stt is None:
                raise VoiceUnavailable("Speech-to-text isn't set up on the hub (VOICE_STT).")
            try:
                text = await self.stt.transcribe(pcm or b"", rate=rate, language=self.language)
            except SpeechRestarting as e:
                busy, text = str(e), ""
            timings["stt"] = round((time.perf_counter() - t0) * 1000)
        heard = (text or "").strip()
        if I.is_noise(heard):
            heard = ""  # a cough, the chime, the room: nothing was said
        t1 = time.perf_counter()
        if busy:
            out = {"heard": "", "reply": busy, "action": {"type": "none", "retry": True}}
        elif not heard or not I.clean(heard):
            out = {"heard": heard, "reply": "", "action": {"type": "none"}}
        else:
            out = await self.answer(heard, viewer)
        timings["intent"] = round((time.perf_counter() - t1) * 1000)
        out["audio"] = None
        if speak and out["reply"] and self.tts is not None:
            t2 = time.perf_counter()
            try:
                out["audio"] = await self.speech(out["reply"], viewer.settings.get("voice") or self.voice)
            except Exception as e:  # the words still show on the wall
                log.warning("Text-to-speech failed: %s", e)
            timings["tts"] = round((time.perf_counter() - t2) * 1000)
        timings["total"] = round((time.perf_counter() - t0) * 1000)
        out["timings"] = timings
        return out

    async def speech(self, text: str, voice: str | None = None) -> dict[str, Any]:
        if self.tts is None:
            raise VoiceUnavailable("Text-to-speech isn't set up on the hub (VOICE_TTS).")
        audio = await self.tts.synthesize(text, voice or self.voice or None)
        return {"type": "audio/wav", "data": base64.b64encode(audio.wav()).decode()}

    # -- understanding and doing ------------------------------------------------------------------

    async def answer(self, heard: str, viewer: VoiceViewer, said: str | None = None) -> dict[str, Any]:
        """`said`: what to act on when it isn't `heard` - the request with a misheard name put right, or the
        one a "yes" to "Did you mean …?" agreed to."""
        world = self.src.world(viewer)
        who = viewer.user.get("name") or "Someone"
        uid = viewer.user["id"]
        # "Did you mean Massimo?" -> "yes" / "no". Anything else lets the question go.
        offer = self.offers.pop(uid, None)
        if offer and said is None and self.clock() - offer["at"] <= OFFER_S:
            words = I.clean(heard)
            yn = I._yes_no(words, world) if words else None
            if yn == "yes":
                return await self.answer(heard, viewer, said=offer["text"])
            if yn == "no":
                reply = "Okay, never mind."
                self.src.log_event("voice", f"{who}: “{heard}”", reply, None, {"intent": "confirm", "via": "voice"})
                return {"heard": heard, "reply": reply, "action": {"type": "confirm", "yes": False}}
        it = I.parse(said or heard, world)
        if it.missing and said is None:
            fix = self._correct(heard, it, world)
            if fix is not None:
                how, text, name = fix
                if how == "high":
                    out = await self.answer(heard, viewer, said=text)
                    out["action"]["corrected"] = name.text
                    return out
                self.offers[uid] = {"text": text, "name": name.text, "at": self.clock()}
                reply = f"Did you mean {name.text}?"
                self.src.log_event("voice", f"{who}: “{heard}”", reply, None, {"intent": "confirm", "via": "voice"})
                return {"heard": heard, "reply": reply,
                        "action": {"type": "confirm", "name": name.text, "slot": name.slot}}
        action: dict[str, Any] = {"type": it.type}
        reply = it.reply
        entity = it.devices[0].id if it.devices else None
        if it.type == "command" and not reply:
            failed = []
            for d in it.devices:
                try:
                    await self.src.command(d.id, it.command.id, f"{who} (voice)", it.value)
                except ActionError as e:
                    failed.append((d, str(e)))
            if failed and len(failed) == len(it.devices):
                reply = f"Sorry, that didn't work: {failed[0][1]}"
            else:
                reply = I.say_done(I.Intent("command", devices=[d for d in it.devices if d not in [f[0] for f in failed]],
                                            command=it.command, value=it.value))
            action.update(command=it.command.id, entities=[d.id for d in it.devices], value=it.value)
        elif it.type == "refuse":
            action.update(command=it.command.id if it.command else None, entities=[d.id for d in it.devices])
            if it.command is None and it.value:
                action.update(automation=it.value)
        elif it.type == "automation_run":
            if self.src.run_automation is None:
                reply = "Automations can't be run by voice here."
            else:
                try:
                    reply = await self.src.run_automation(viewer, str(it.value))
                except ActionError as e:
                    reply = str(e)
            action.update(automation=it.value)
        elif it.type == "ask_state":
            reply = I.say_state_answer(it)
        elif it.type == "ask_value":
            reply = I.say_value_answer(it)
        elif it.type == "describe":
            reply = I.say_describe(it)
        elif it.type == "where":
            reply = I.say_where(it)
        elif it.type == "today":
            reply = I.say_today(self.src.today(viewer))
        elif it.type in ("time", "date"):
            # The hub's local time (its TZ), like the scheduler and the Today page.
            now = datetime.fromtimestamp(self.clock())
            reply = I.say_time(now) if it.type == "time" else I.say_date(now)
        elif it.type == "show" and not reply:
            action.update(self._show(it))
            reply = action.pop("say")
        elif it.type == "show":
            action = {"type": "none"}
        elif it.type.startswith("timer_") and not reply:
            reply, extra = await self._timer(it, uid, world)
            action.update(extra)
        elif it.type == "stop":
            stopped = await self.timers.stop_ringing(uid)
            if not stopped and it.label == "timer":
                running = [t for t in world.timers if t["state"] == "running"]
                if len(running) == 1:
                    await self.timers.remove(uid, [running[0]["id"]])
                    reply = f"Okay, I cancelled your {running[0]['name']}."
            elif not stopped and world.music is not None and world.music.state == "playing":
                # "Hey Newman, stop" with nothing ringing: the music.
                try:
                    await self.src.command(world.music.id, "pause", f"{who} (voice)", None)
                    entity = world.music.id
                except ActionError:
                    pass
            action.update(stopped=stopped)
        elif it.type == "music_play":
            if self.src.music is None:
                reply = "Music isn't set up on this tablet."
            else:
                try:
                    reply = await self.src.music(viewer, it.music)
                except ActionError as e:
                    reply = str(e)
            action.update(music=it.music)
        elif it.type == "music_answer":
            if self.src.music_answer is None:
                reply = "Music isn't set up on this tablet."
            else:
                try:
                    reply = await self.src.music_answer(viewer, bool(it.value))
                except ActionError as e:
                    reply = str(e)
            action.update(yes=bool(it.value))
        elif it.type == "music_control" and not reply:
            reply = await self._music_control(it, who)
            action.update(command=it.command.id, entities=[it.devices[0].id], value=it.value)
        elif it.type == "music_what":
            reply = I.say_music_what(it.devices[0])
        elif it.type == "cook":
            reply, extra = await self._cook(it, viewer, uid)
            action.update(extra)
        self.src.log_event("voice", f"{who}: “{heard}”", reply or {"stop": "Stopped"}.get(it.type, ""), entity,
                           {"intent": it.type, "via": "voice"})
        return {"heard": heard, "reply": reply, "action": action}

    # -- a name heard a little wrong ------------------------------------------------------------------

    def _correct(self, heard: str, it: I.Intent, world: I.World) -> tuple[str, str, N.Name] | None:
        """The request with the misheard name put right -> (band "high" | "medium", the corrected words, the
        name), or None. Only names of the slot the request needs (and that it can use: `missing["pool"]`) are
        considered, and the corrected request must then make sense - otherwise nothing is corrected."""
        if self.src.names is None or not it.missing:
            return None
        m = it.missing
        everything = self.src.names.names()
        pool = m.get("pool")
        allowed = [n for n in everything if n.slot in m["slots"] and (pool is None or (n.slot, n.ref) in pool)]
        kinds = {d.kind: d.k for d in world.devices}
        skip = set(I.STOPWORDS) | I.ALL_WORDS | {w for k in kinds.values() for w in I.kind_words(k)}
        fix = N.correct(list(m["words"]), allowed, everything, skip)
        if fix is None:
            return None
        text = self._replace(heard, list(m["words"][fix.start:fix.end]), fix.name.text)
        if text is None:
            return None
        again = I.parse(text, world)
        if again.missing or again.type == "unknown":
            return None
        return fix.band, text, fix.name

    @staticmethod
    def _replace(heard: str, span: list[str], name: str) -> str | None:
        """The words heard (cleaned) with the stretch `span` replaced by `name`."""
        words = I.clean(heard)
        try:
            a = words.index(span[0])
            b = a + words[a:].index(span[-1])
        except ValueError:
            return None
        return " ".join(words[:a] + I.normalize(name) + words[b + 1:])

    async def _cook(self, it: I.Intent, viewer: VoiceViewer, uid: str) -> tuple[str, dict[str, Any]]:
        if self.src.cook is None:
            return "Recipes aren't set up.", {}
        out = await self.src.cook(viewer, dict(it.value or {}))
        extra: dict[str, Any] = {"cook": (it.value or {}).get("do")}
        t = out.get("timer")
        if t:
            try:
                made = await self.timers.add(uid, int(t["seconds"]), str(t.get("label") or ""))
            except ValueError as e:
                return str(e), extra
            extra["timerId"] = made["id"]
            what = f"{made['label']} timer for {I.say_duration(made['seconds'], exact=True)}" if made["label"] else made["name"]
            return f"Okay, {what}, starting now.", extra
        return str(out.get("reply") or ""), extra

    async def _music_control(self, it: I.Intent, who: str) -> str:
        d, c = it.devices[0], it.command
        try:
            await self.src.command(d.id, c.id, f"{who} (voice)", it.value)
        except ActionError as e:
            return str(e)
        now = (self.src.device(d.id) if self.src.device else None) or d
        if c.id in ("next", "previous") and now.state != "idle" and now.attrs.get("title"):
            by = f" by {now.attrs['artist']}" if now.attrs.get("artist") else ""
            return f"{now.attrs['title']}{by}."
        if c.id in ("set_volume", "volume_up", "volume_down"):
            v = now.attrs.get("volume", it.value)
            return f"Volume {I.say_number(v)} percent." if v is not None else "Okay."
        return {"pause": "Paused.", "play": "Okay.", "stop": "Okay, the music is off.",
                "next": "That was the last song.", "previous": "Okay."}.get(c.id, "Okay.")

    @staticmethod
    def _show(it: I.Intent) -> dict[str, Any]:
        if it.show == "camera":
            d = it.devices[0]
            return {"show": "camera", "entityId": d.id, "say": f"Here's {I.the(d)}."}
        if it.show == "person":
            p = it.people[0]
            return {"show": "person", "personId": p["id"], "say": f"Here's {p['name']}."}
        if it.show == "page":
            return {"show": "page", "pageId": it.page["id"], "say": f"Here's {it.page['name']}."}
        say = {"cameras": "Here are the cameras.", "home": "Okay.", "weather": "Here's the weather.",
               "calendar": "Here's the calendar.", "todo": "Here's the to do list.",
               "lyrics": "Here are the lyrics."}.get(it.show or "", "Okay.")
        return {"show": it.show, "say": say}

    async def _timer(self, it: I.Intent, uid: str, world: I.World) -> tuple[str, dict[str, Any]]:
        now = self.clock()
        if it.type == "timer_set":
            try:
                t = await self.timers.add(uid, it.seconds or 0, it.label)
            except ValueError as e:
                return str(e), {}
            what = f"{it.label} timer for {I.say_duration(t['seconds'], exact=True)}" if it.label else t["name"]
            return f"Okay, {what}, starting now.", {"timerId": t["id"]}
        found = I._find_timers(world, it.label)
        if it.type == "timer_list":
            return I.say_timers(world.timers, now, count=True), {}
        if it.type == "timer_query":
            if not world.timers:
                return "You don't have any timers.", {}
            if not found:
                return f"You don't have a {it.label} timer.", {}
            return I.say_timers(found, now), {}
        # cancel
        if not world.timers:
            return "You don't have any timers.", {}
        if not found:
            return f"You don't have a {it.label} timer.", {}
        if len(found) > 1 and not it.all and not it.label:
            names = [t["name"] for t in found[:3]]
            return f"Which timer: {I.join_names(names, 'or')}? Or say “cancel all timers”.", {}
        await self.timers.remove(uid, [t["id"] for t in found])
        if len(found) == 1:
            return f"Okay, I cancelled your {found[0]['name']}.", {"cancelled": [found[0]["id"]]}
        return f"Okay, I cancelled {len(found)} timers.", {"cancelled": [t["id"] for t in found]}
