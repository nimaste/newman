"""One spoken request, end to end: audio -> speech-to-text -> intent -> action -> reply -> speech.

    result = await pipeline.handle(pcm=..., viewer=VoiceViewer(...))   # or text="…" (no speech-to-text)
    -> {"heard", "reply", "action", "audio": {"type": "audio/wav", "data": base64} | None, "timings"}

The speech services are injected (Wyoming clients in production, fakes in tests). Nothing about the
audio is kept: the activity log gets the words heard and what happened.

A name the parser couldn't find as heard ("run good nite", "where is ma seemo") gets a second look against
every name the hub knows of the kind the request needs (names.py): sure -> done as corrected; fairly sure ->
"Did you mean Massimo?", and a "yes" within OFFER_S does it.

Other questions back wait FOLLOW_S for their answer ("Which one: pasta or eggs?", "Just tomorrow, or every
time?", "6:30 in the morning or the evening?"); anything else lets the question go. A reply that asks has
action.listen = True: the wall listens for the answer without the wake word.
"""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable, Protocol

from ..automations.engine import ActionError
from . import intents as I
from . import names as N
from .alarms import SNOOZE_S, Alarms, js_day, say_clock
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
FOLLOW_S = 60  # how long "Which one: …?" and the other questions back wait for their answer
JUST_WORDS = {"just", "only", "once", "tomorrow", "today", "tonight", "next", "this", "that", "skip"} | set(I.DAY_WORDS)
EVERY_WORDS = {"every", "all", "always", "good", "each", "forever", "completely", "everytime", "both", "whole"}


class Pipeline:
    def __init__(self, sources: Sources, timers: Timers, stt: SpeechToText | None, tts: TextToSpeech | None,
                 voice: str = "", language: str = "en", clock: Callable[[], float] = time.time,
                 alarms: Alarms | None = None, snooze_for: Callable[[str], float] | None = None) -> None:
        """`snooze_for`: a tablet's "Snooze for" (seconds), from its own settings."""
        self.src, self.timers, self.stt, self.tts = sources, timers, stt, tts
        self.voice, self.language, self.clock = voice, language, clock
        self.alarms = alarms
        self.snooze_for = snooze_for or (lambda _uid: SNOOZE_S)
        self.offers: dict[str, dict[str, Any]] = {}  # uid -> {"text": the corrected request, "name", "at"}
        # uid -> {"kind": which_timer | which_alarm | repeat | ampm, "intent", "ids", "at"}: a question back
        self.pending: dict[str, dict[str, Any]] = {}

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
        # "Which one: pasta or eggs?" -> "pasta"; "Just tomorrow, or every time?" -> "every time"; …
        pend = self.pending.pop(uid, None)
        if pend and said is None and self.clock() - pend["at"] <= FOLLOW_S:
            got = await self._follow_up(pend, I.clean(heard), uid, world)
            if got is not None:
                reply, action = got
                self.src.log_event("voice", f"{who}: “{heard}”", reply, None, {"intent": action["type"], "via": "voice"})
                return {"heard": heard, "reply": reply, "action": action}
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
                        "action": {"type": "confirm", "name": name.text, "slot": name.slot, "listen": True}}
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
        elif it.type.startswith("alarm_") or it.type in ("snooze", "another_minute"):
            reply, extra = await self._alarm(it, uid, world)
            action.update(extra)
        elif it.type == "stop":
            stopped = await self.timers.stop_ringing(uid)
            if self.alarms is not None:
                stopped += await self.alarms.stop_ringing(uid)
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


    # -- timers ------------------------------------------------------------------------------------------

    async def _timer(self, it: I.Intent, uid: str, world: I.World) -> tuple[str, dict[str, Any]]:
        now = self.clock()
        if it.type == "timer_set":
            try:
                t = await self.timers.add(uid, it.seconds or 0, it.label)
            except ValueError as e:
                return str(e), {}
            what = f"{it.label} timer for {I.say_duration(t['seconds'], exact=True)}" if it.label else t["name"]
            return f"Okay, {what}, starting now.", {"timerId": t["id"]}
        if it.type == "timer_list":
            return I.say_timers(world.timers, now, count=True), {}
        if not world.timers:
            return "You don't have any timers.", {}
        found = I.find_timers(world.timers, it.label, it.ref)
        if it.type == "timer_query":
            if not found:
                return f"You don't have a {it.label} timer.", {}
            return I.say_timers(found, now), {}
        if not found:
            return (f"You don't have a {it.label} timer." if it.label else "I couldn't find that timer."), {}
        if not it.label and not it.ref and len(found) > 1 and not it.all:
            # Only the ones it can mean: pausing a running one, resuming a paused one.
            pool = {"timer_pause": "running", "timer_resume": "paused"}.get(it.type)
            fits = [t for t in found if t["state"] == pool] if pool else found
            found = fits or found
        if len(found) > 1 and not it.all:
            self.pending[uid] = {"kind": "which_timer", "intent": it, "ids": [t["id"] for t in found], "at": now}
            return I.which_timer(found), {"listen": True}
        return await self._timer_do(it, uid, found)

    async def _timer_do(self, it: I.Intent, uid: str, found: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        n = len(found)
        both = "both timers" if n == 2 else f"all {n} timers"
        if it.type == "timer_cancel":
            await self.timers.remove(uid, [t["id"] for t in found])
            ids = {"cancelled": [t["id"] for t in found]}
            if n == 1:
                return f"Okay, I cancelled your {found[0]['name']}.", ids
            return f"Okay, {both} are cancelled.", ids
        do = {"timer_add": "add", "timer_take": "take", "timer_pause": "pause", "timer_resume": "resume",
              "timer_restart": "restart", "timer_change": "set"}[it.type]
        if n == 1:
            t = found[0]
            if do == "pause" and t["state"] != "running":
                return f"The {t['name']} isn't running.", {}
            if do == "resume" and t["state"] != "paused":
                return f"The {t['name']} isn't paused.", {}
        done = []
        try:
            for t in found:
                got = await self.timers.change(uid, t["id"], do, it.seconds or 0)
                if got:
                    done.append(got)
        except ValueError as e:
            return str(e), {}
        if not done:
            return "That timer isn't there any more.", {}
        extra = {"timerId": done[0]["id"]}
        if n > 1:
            amount = I.say_duration(it.seconds or 0, exact=True)
            return {"add": f"Okay, I added {amount} to {both}.", "take": f"Okay, I took {amount} off {both}.",
                    "pause": f"Okay, {both} are paused.", "resume": f"Okay, {both} are running again.",
                    "restart": f"Okay, {both} are back to their full time.",
                    "set": f"Okay, {both} are now {amount}."}[do], extra
        t, old = done[0], found[0]
        the = f"the {old['name']}"
        left = self.timers.left(t)
        if do in ("add", "take"):
            if t["state"] != "paused" and t["endsAt"] <= self.clock():
                return f"Okay, {the} is done.", extra
            return f"Okay, {the} has {I.say_duration(left)} left.", extra
        if do == "pause":
            return f"Okay, paused with {I.say_duration(left)} left.", extra
        if do == "resume":
            return f"Okay, {the} is running again.", extra
        if do == "restart":
            return f"Okay, {the} is back to {I.say_duration(t['seconds'], exact=True)}.", extra
        the = f"the {old['label']} timer" if old.get("label") else "the timer"
        return f"Okay, {the} is now {I.say_duration(t['seconds'], exact=True)}.", extra

    # -- alarms ------------------------------------------------------------------------------------------

    async def _alarm(self, it: I.Intent, uid: str, world: I.World) -> tuple[str, dict[str, Any]]:
        if it.type in ("snooze", "another_minute"):
            return await self._snooze(it, uid, world)
        if self.alarms is None:
            return "Alarms aren't set up here.", {}
        al = self.alarms
        v = dict(it.value or {})
        if it.type == "alarm_set":
            if not v.get("ampm") and 1 <= v["hour"] <= 12 and (v.get("days") or v.get("once")):
                self.pending[uid] = {"kind": "ampm", "intent": it, "at": self.clock()}
                said = f"{v['hour']}" if not v["minute"] else f"{v['hour']}:{v['minute']:02d}"
                return f"{said} in the morning or the evening?", {"listen": True}
            return await self._alarm_set(v, uid)
        if it.type == "alarm_list":
            on = [a for a in world.alarms if a["enabled"] or a["state"] != "set"]
            if not on:
                return "You don't have any alarms on.", {"show": "alarms"}
            parts = [(f"the {a['label']} alarm at {a['time']} " if a["label"] else f"{a['time']} ") + al.say_when(a)
                     for a in on]
            s = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + ", and " + parts[-1]
            head = "You have 1 alarm" if len(on) == 1 else f"You have {len(on)} alarms"
            return f"{head}: {s}.", {"show": "alarms"}
        if not world.alarms:
            return "You don't have any alarms.", {}
        target = v.get("target", v) if it.type == "alarm_change" else v
        found = I.find_alarms(world.alarms, target)
        if not found:
            return (f"You don't have a {target['label']} alarm." if target.get("label") else
                    "I couldn't find that alarm."), {}
        if len(found) > 1 and not (it.all and it.type in ("alarm_cancel", "alarm_off", "alarm_on")):
            self.pending[uid] = {"kind": "which_alarm", "intent": it, "ids": [a["id"] for a in found], "at": self.clock()}
            return I.which_alarm(found), {"listen": True}
        return await self._alarm_do(it, uid, found)

    async def _alarm_set(self, v: dict[str, Any], uid: str) -> tuple[str, dict[str, Any]]:
        al = self.alarms
        assert al is not None
        h, m, days = v["hour"], v["minute"], set(v.get("days") or [])
        if not v.get("ampm") and 1 <= h <= 12:
            # No AM / PM, no day: the next one coming.
            pairs = [(al.coming(x % 24, m) or float("inf"), x % 24) for x in (h % 12, h % 12 + 12)]
            h = min(pairs)[1]
        on_date: date | None = None
        once = v.get("once") or ""
        today = al.today()
        if once == "today":
            on_date = today
        elif once == "tomorrow":
            on_date = today + timedelta(days=1)
        elif once.startswith("d"):
            want = int(once[1:])
            for i in range(0, 8):
                d = today + timedelta(days=i)
                if js_day(d) == want and al.at(d, h, m) > self.clock():
                    on_date = d
                    break
        try:
            a = await al.add(uid, h, m, sorted(days), v.get("label") or "", on_date)
        except ValueError as e:
            return str(e), {}
        what = f"{a['label']} alarm" if a["label"] else "alarm"
        return f"Okay, {what} set for {a['time']} {al.say_when(a)}.", {"alarmId": a["id"]}

    async def _alarm_do(self, it: I.Intent, uid: str, found: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        al = self.alarms
        assert al is not None
        if len(found) > 1:  # "cancel all my alarms"
            n = len(found)
            for a in found:
                if it.type == "alarm_cancel":
                    await al.remove(uid, a["id"])
                else:
                    await al.update(uid, a["id"], enabled=it.type == "alarm_on")
            what = "both your alarms" if n == 2 else f"all {n} of your alarms"
            return f"Okay, {what} {'are cancelled' if it.type == 'alarm_cancel' else 'are ' + ('on' if it.type == 'alarm_on' else 'off')}.", {}
        a = found[0]
        extra = {"alarmId": a["id"]}
        if it.type in ("alarm_cancel", "alarm_off") and a["days"] and a.get("nextAt"):
            self.pending[uid] = {"kind": "repeat", "intent": it, "ids": [a["id"]], "at": self.clock()}
            day = al.day_word(a["nextAt"]).removeprefix("on ")
            return f"Just {day}, or every time?", {**extra, "listen": True}
        if it.type == "alarm_cancel":
            await al.remove(uid, a["id"])
            return f"Okay, your {a['name']} is cancelled.", extra
        if it.type == "alarm_off":
            await al.update(uid, a["id"], enabled=False)
            return f"Okay, your {a['name']} is off.", extra
        if it.type == "alarm_on":
            b = await al.update(uid, a["id"], enabled=True)
            return f"Okay, your {a['name']} is on, {al.say_when(b) if b else ''}.".replace(", .", "."), extra
        if it.type == "alarm_skip":
            if not a["days"]:
                await al.update(uid, a["id"], enabled=False)
                return f"Okay, your {a['name']} is off.", extra
            once = (it.value or {}).get("once") or ""
            nxt = a.get("nextAt")
            if once and nxt:
                want = {"today": al.today(), "tomorrow": al.today() + timedelta(days=1)}.get(once)
                got = al.local(nxt).date()
                if (want is not None and got != want) or (once.startswith("d") and js_day(got) != int(once[1:])):
                    word = {"today": "today", "tomorrow": "tomorrow"}.get(once) or "on " + \
                        ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][int(once[1:])]
                    return f"Your {a['name']} doesn't go off {word}.", extra
            await al.skip_next(uid, a["id"])
            return f"Okay, your {a['name']} won't go off {al.day_word(nxt) if nxt else 'next time'}.", extra
        if it.type == "alarm_change":
            v = it.value or {}
            h, m = v["hour"], v["minute"]
            if not v.get("ampm") and 1 <= h <= 12:
                h = h % 12 + (12 if a["hour"] >= 12 else 0)  # the same half of the day as before
            b = await al.update(uid, a["id"], hour=h, minute=m)
            the = f"the {a['label']} alarm" if a["label"] else "your alarm"
            return f"Okay, {the} is now {say_clock(h, m)}{', ' + al.say_when(b) if b and not b['days'] else ''}.", extra
        return "Okay.", extra

    async def _snooze(self, it: I.Intent, uid: str, world: I.World) -> tuple[str, dict[str, Any]]:
        """"Snooze" / "another minute": a ringing alarm snoozes, a ringing timer gets the time more."""
        ring_alarms = [a for a in world.alarms if a["state"] == "ringing"]
        ring_timers = [t for t in world.timers if t["state"] == "ringing"]
        if not ring_alarms and not ring_timers:
            return "Nothing is ringing.", {}
        if it.type == "snooze":
            secs = it.seconds or (self.snooze_for(uid) if ring_alarms else 60)
        else:
            secs = it.seconds or 60
        if ring_alarms and self.alarms is not None:
            for a in ring_alarms:
                await self.alarms.snooze(uid, a["id"], secs)
        for t in ring_timers:
            await self.timers.change(uid, t["id"], "add", secs)
        if ring_alarms and it.type == "snooze":
            return f"Okay, snoozing for {I.say_duration(secs, exact=True)}.", {"snoozed": True}
        return ("Okay, one more minute." if secs == 60 else f"Okay, {I.say_duration(secs, exact=True)} more."), {}

    # -- answers to the questions back ----------------------------------------------------------------

    async def _follow_up(self, pend: dict[str, Any], words: list[str], uid: str,
                         world: I.World) -> tuple[str, dict[str, Any]] | None:
        """The answer to a question Newman asked (pending), done; None when what was said isn't an answer."""
        if not words:
            return None
        it: I.Intent = pend["intent"]
        kind = pend["kind"]
        if kind == "which_timer":
            cands = [t for t in world.timers if t["id"] in pend["ids"]]
            picked = I.pick_answer(words, cands)
            if not picked:
                return None
            reply, extra = await self._timer_do(it, uid, picked)
            return reply, {"type": it.type, **extra}
        if kind == "which_alarm":
            cands = [a for a in world.alarms if a["id"] in pend["ids"]]
            picked = I.pick_alarm(words, cands)
            if not picked:
                return None
            reply, extra = await self._alarm_do(it, uid, picked[:1] if len(picked) > 1 and not it.all else picked)
            return reply, {"type": it.type, **extra}
        if kind == "repeat" and self.alarms is not None:
            a = next((x for x in world.alarms if x["id"] in pend["ids"]), None)
            if a is None:
                return None
            ws = set(words)
            if ws & EVERY_WORDS:
                if it.type == "alarm_cancel":
                    await self.alarms.remove(uid, a["id"])
                    return f"Okay, your {a['name']} is cancelled.", {"type": it.type, "alarmId": a["id"]}
                await self.alarms.update(uid, a["id"], enabled=False)
                return f"Okay, your {a['name']} is off.", {"type": it.type, "alarmId": a["id"]}
            if ws & JUST_WORDS:
                nxt = a.get("nextAt")
                await self.alarms.skip_next(uid, a["id"])
                return (f"Okay, your {a['name']} won't go off {self.alarms.day_word(nxt) if nxt else 'next time'}.",
                        {"type": "alarm_skip", "alarmId": a["id"]})
            return None
        if kind == "ampm":
            which = I.ampm_answer(words)
            if not which:
                return None
            v = dict(it.value or {})
            h = v["hour"] % 12
            v["hour"], v["ampm"] = (h + 12 if which == "pm" else h), which
            reply, extra = await self._alarm_set(v, uid)
            return reply, {"type": "alarm_set", **extra}
        return None
