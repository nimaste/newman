"""Smart charging: the hub keeps each wall tablet's battery between two levels by switching the plug it
charges from - on below "start" (20 %), off above "stop" (80 %) - so a tablet on a wall mount isn't
held at 100 % all day (what makes its battery swell).

Settings are per wall tablet account (settings key "smartCharging": {account id: {...}}), off until an
admin turns them on. It acts only when the battery crosses a level (and once when it's turned on, or
after a restart, if the battery is already outside the band), so someone switching the plug by hand is
left alone until the next crossing.

The charger is learned by watching, never by trying plugs: when exactly one switchable device goes
on (off) and, within LEARN_WINDOW_S, exactly one tablet starts (stops) charging, that device is the
tablet's charger. "Switchable" comes from the kind's metadata (turn_on / turn_off commands), not a
list of kinds.

Admins get one push - at most once an hour per tablet, again once it has charged - when a tablet is
below CRITICAL_LEVEL and not charging, or its charger isn't responding when it should turn on. And one
(when it goes silent - so again only after it reported in between - and at most once an hour) when a tablet with smart charging on stops reporting
its battery: when the tablets integration marks it offline - 15 minutes without a report with the screen on,
longer with it off (see integrations/tablets.py); a tablet with its screen off within those is fine. A
tablet whose battery is unknown (attr batteryStale: screen off, no background reports, quiet for a while -
an iPad) is left alone: no switching, no alerts, until it reports again.

The wrong plug: when Newman turns the charger on and the tablet, still reporting, hasn't started charging
within START_WAIT_S, the plug is probably not the one it's on. The admins get one question per attempt
("… which plug is it on?") with up to MAX_GUESSES other switchable devices as buttons (same area as the
tablet first, then ones that measure power, then ones that are off) and "None of these"; the same question
shows on the tablet's Smart charging settings. No other plug is switched to find out. Picking one makes it
the charger (chosenBy "user") and turns it on; the usual learning then confirms it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .automations.engine import PRESS_COMMAND_S, ActionError, category_for
from .kinds import KINDS
from .push.notifier import Notification
from .state import Entity

log = logging.getLogger(__name__)

SETTING = "smartCharging"
WHO = "Smart charging"
LEARN_WINDOW_S = 10.0     # a plug switching and a tablet starting / stopping charging this close = cause and effect
PLUG_GRACE_S = 90.0       # a charger not responding gets this long (e.g. a restart, still connecting) before the alert
ALERT_EVERY_S = 3600.0    # at most one alert an hour per tablet
CRITICAL_LEVEL = 10       # below this and not charging: tell the admins
START_WAIT_S = 60.0       # after turning the charger on: this long without charging = probably the wrong plug
MAX_GUESSES = 3           # plugs offered when asking which one the tablet is on
NONE_OF_THESE = "charger_none"
DEFAULTS: dict[str, Any] = {"enabled": False, "startBelow": 20, "stopAbove": 80, "charger": None,
                            "learned": False, "learnedAt": None, "chosenBy": None}  # chosenBy: "user" | "learned"


def _on_off(kind: str) -> tuple[str, str] | None:
    """The (on, off) states of a kind that can simply be switched on and off, by its metadata."""
    k = KINDS.get(kind)
    if not k:
        return None
    on, off = k.command("turn_on"), k.command("turn_off")
    if not on or not off or on.takes_value or off.takes_value or on.sensitive or off.sensitive:
        return None
    if not on.confirms_to or on.confirms_to != k.active_state or not off.confirms_to or off.confirms_to == on.confirms_to:
        return None
    return on.confirms_to, off.confirms_to


def switchable(kind: str) -> bool:
    return _on_off(kind) is not None


def is_on(ent: Entity | None) -> bool | None:
    """True on / False off / None unknown (or not a switchable device)."""
    states = _on_off(ent.kind) if ent else None
    if not states or not ent:
        return None
    return True if ent.state == states[0] else False if ent.state == states[1] else None


def powered(attrs: dict[str, Any]) -> bool | None:
    """The tablet is on its charger: charging, or plugged in (full)."""
    if not isinstance(attrs.get("charging"), bool):
        return None
    return attrs["charging"] or attrs.get("plugged") not in (None, "none")


def _level(v: Any) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 5 <= v <= 95 or v != int(v):
        return None
    return int(v)


def clean_settings(data: Any, current: dict[str, Any], hub: Any) -> tuple[dict[str, Any], list[str]]:
    """A PATCH body -> the new settings, or the problems with it."""
    if not isinstance(data, dict):
        return current, ["Send a JSON object"]
    s, errors = dict(current), []
    if "enabled" in data:
        if not isinstance(data["enabled"], bool):
            errors.append("enabled must be true or false")
        else:
            s["enabled"] = data["enabled"]
    for key, label in (("startBelow", "Start below"), ("stopAbove", "Stop above")):
        if key in data:
            v = _level(data[key])
            if v is None:
                errors.append(f"{label} must be a whole percent from 5 to 95")
            else:
                s[key] = v
    if not errors and s["startBelow"] >= s["stopAbove"]:
        errors.append("Start below must be lower than stop above")
    if "charger" in data:
        c = data["charger"]
        if c in (None, ""):
            s.update(charger=None, learned=False, learnedAt=None, chosenBy=None)
        elif not isinstance(c, str) or not (ent := hub.get(c)) or not switchable(ent.kind):
            errors.append("The charger must be a device that switches on and off")
        elif c != current.get("charger"):
            s.update(charger=c, learned=False, learnedAt=None, chosenBy="user")  # picked by hand
    return s, errors


class SmartCharging:
    def __init__(self, hub: Any, store: Any, engine: Any, notifier: Any) -> None:
        self.hub, self.store, self.engine, self.notifier = hub, store, engine, notifier
        self.clock = time.time
        self.learn_window = LEARN_WINDOW_S
        self.plug_grace = PLUG_GRACE_S
        self.start_wait = START_WAIT_S
        self._watch: dict[str, dict[str, Any]] = {}     # account id -> {"plug", "at"}: just turned on, should charge
        self._question: dict[str, dict[str, Any]] = {}  # account id -> the open "which plug is it on?"
        self._zone: dict[str, str] = {}             # account id -> "low" | "mid" | "high" (last seen)
        self._pending: dict[str, dict[str, Any]] = {}  # account id -> {"command", "at"}: the charger wasn't responding
        self._alerted: dict[str, float] = {}         # account id -> when the admins were last told
        self._silent: dict[str, float] = {}          # account id -> when the admins were told it stopped reporting
        self._switch_flips: list[dict[str, Any]] = []   # {"at", "id", "on"}
        self._tablet_flips: list[dict[str, Any]] = []   # {"at", "uid", "on", "done"}
        self._tasks: set[asyncio.Task] = set()
        hub.subscribe_changes(self._changed)

    # -- settings ------------------------------------------------------------------------------------

    def settings(self, uid: str) -> dict[str, Any]:
        return {**DEFAULTS, **((self.store.settings().get(SETTING) or {}).get(uid) or {})}

    def save(self, uid: str, s: dict[str, Any]) -> None:
        all_ = dict(self.store.settings().get(SETTING) or {})
        all_[uid] = {k: s.get(k, v) for k, v in DEFAULTS.items()}
        self.store.set_setting(SETTING, all_)

    async def update(self, uid: str, s: dict[str, Any]) -> None:
        """New settings from an admin: look again now, as if it had just been turned on - with the new
        charger, even when the battery's zone didn't change, and from the last level it reported even when
        the tablet is offline right now."""
        before = self.settings(uid)
        self.save(uid, s)
        self._zone.pop(uid, None)
        self._pending.pop(uid, None)
        if s.get("charger") != before.get("charger") or not s.get("enabled"):
            self._question.pop(uid, None)  # answered by picking the plug here (or no longer wanted)
            self._watch.pop(uid, None)
        await self.evaluate(uid, last_known=True)

    def tablet(self, uid: str) -> Entity | None:
        return next((e for e in self.hub.all("tablet") if e.id.split(".", 1)[-1] == uid), None)

    # -- watching --------------------------------------------------------------------------------------

    def _spawn(self, coro: Any) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _changed(self, old: Entity | None, new: Entity) -> None:
        if new.kind == "tablet":
            await self._tablet_changed(old, new)
        elif switchable(new.kind):
            await self._switch_changed(old, new)

    async def _tablet_changed(self, old: Entity | None, new: Entity) -> None:
        uid = new.id.split(".", 1)[-1]
        was, now = powered(old.attrs) if old else None, powered(new.attrs)
        if was is not None and now is not None and was != now:
            self._tablet_flips.append({"at": self.clock(), "uid": uid, "on": now, "done": False})
            self._spawn(self._learn_later())
        if new.attrs.get("charging") is True:
            self._alerted.pop(uid, None)  # charging again: the next problem is worth a new alert
        if now is True:
            self._watch.pop(uid, None)    # it did start: the plug was right (or someone plugged it in)
            self._question.pop(uid, None)
        if not new.available and old is not None and old.available:  # once per silence (and at most hourly)
            await self._stopped_reporting(uid, new)
        await self.evaluate(uid, new)

    async def _stopped_reporting(self, uid: str, tablet: Entity) -> None:
        if not self.settings(uid)["enabled"]:
            return
        now = self.clock()
        if now - self._silent.get(uid, -1e18) < ALERT_EVERY_S:
            return
        self._silent[uid] = now
        await self._tell_admins(uid, tablet, f"{tablet.name} stopped reporting its battery", "stopped")

    async def _switch_changed(self, old: Entity | None, new: Entity) -> None:
        if old is None or (old.state is None and new.state is not None):
            # Just registered / its first state (the hub starting, its connection coming up after the
            # tablet's last report was restored): a charger waited for is here now.
            if new.available:
                await self._retry_pending(new.id)
            return
        was, now = is_on(old), is_on(new)
        if was is not None and now is not None and was != now and old.available and new.available:
            self._switch_flips.append({"at": self.clock(), "id": new.id, "on": now})
            self._spawn(self._learn_later())
        if new.available and not old.available:
            await self._retry_pending(new.id)  # a charger that wasn't responding is back

    async def _retry_pending(self, plug_id: str) -> None:
        """Do what a charger couldn't do while it wasn't there (if still wanted)."""
        for uid, p in list(self._pending.items()):
            s = self.settings(uid)
            if s["enabled"] and s["charger"] == plug_id and (t := self.tablet(uid)):
                await self._act(uid, s, t, p["command"])

    # -- keeping the battery in its band ---------------------------------------------------------------

    async def evaluate(self, uid: str, ent: Entity | None = None, last_known: bool = False) -> None:
        s = self.settings(uid)
        if not s["enabled"]:
            self._zone.pop(uid, None)
            self._pending.pop(uid, None)
            return
        ent = ent or self.tablet(uid)
        level = ent.attrs.get("batteryLevel") if ent else None
        if not ent or not (ent.available or last_known) or isinstance(level, bool) or not isinstance(level, (int, float)):
            return
        if ent.attrs.get("batteryStale"):
            return  # screen off and silent: its level is unknown now - nothing to act on or alert about
        zone = "low" if level < s["startBelow"] else "high" if level > s["stopAbove"] else "mid"
        if self._zone.get(uid) != zone:  # a crossing (or the first look): act; otherwise leave the plug be
            self._zone[uid] = zone
            self._pending.pop(uid, None)
            if zone != "mid":
                await self._act(uid, s, ent, "turn_on" if zone == "low" else "turn_off")
        if ent.available and level < CRITICAL_LEVEL and ent.attrs.get("charging") is not True:
            await self._alert(uid, ent, f"{ent.name} is at {level:g}% and not charging")

    async def _act(self, uid: str, s: dict[str, Any], tablet: Entity, command: str) -> None:
        cid = s.get("charger")
        if not cid:
            return
        plug = self.hub.get(cid)
        level = tablet.attrs.get("batteryLevel")
        want_on = command == "turn_on"
        if not plug or not plug.available:
            self._unresponsive(uid, command)
            return
        if is_on(plug) is want_on:
            self._pending.pop(uid, None)
            return  # already as it should be
        try:
            await asyncio.wait_for(self.engine.command(cid, command, WHO), PRESS_COMMAND_S)
        except (ActionError, asyncio.TimeoutError) as e:
            log.warning("Smart charging %s: %s", tablet.name, str(e) or "the plug's connection didn't take the command")
            self._unresponsive(uid, command)
            return
        self._pending.pop(uid, None)
        self.store.log_event("automation", f"Turned {plug.name} {'on' if want_on else 'off'}: battery {level:g}%",
                             f"Smart charging for {tablet.name}", tablet.id)
        if want_on:
            self._expect_charging(uid, cid)
        else:
            self._watch.pop(uid, None)

    # -- the wrong plug ----------------------------------------------------------------------------------

    def _expect_charging(self, uid: str, plug_id: str) -> None:
        """The charger was just turned on: a new attempt (an older question gives way to it)."""
        self._question.pop(uid, None)
        w = self._watch[uid] = {"plug": plug_id, "at": self.clock()}
        self._spawn(self._check_started(uid, w))

    async def _check_started(self, uid: str, w: dict[str, Any]) -> None:
        await asyncio.sleep(self.start_wait)
        if self._watch.get(uid) is not w:
            return  # it started charging, or a newer attempt took over
        self._watch.pop(uid, None)
        s, t = self.settings(uid), self.tablet(uid)
        if not s["enabled"] or s["charger"] != w["plug"] or not t or not t.available or t.attrs.get("batteryStale"):
            return  # off, changed, or not reporting: nothing to conclude
        if powered(t.attrs) is not False:
            return  # charging after all (or it doesn't say)
        plug = self.hub.get(w["plug"])
        if not plug or not plug.available or is_on(plug) is not True:
            return  # the plug itself is the problem (that has its own alert) or someone turned it off
        await self._ask(uid, t, plug)

    def guesses(self, tablet: Entity, charger_id: str | None) -> list[Entity]:
        """The plugs the tablet is most likely on: other switchable devices that respond, in the tablet's
        area first, then ones that measure power (a smart plug), then ones that are off (the real charger
        would have charged it if it were on)."""
        area = str(tablet.attrs.get("area") or "").strip().lower()
        cands = [e for e in self.hub.all() if e.id != charger_id and e.available and switchable(e.kind)]
        cands.sort(key=lambda e: (not (area and str(e.attrs.get("area") or "").strip().lower() == area),
                                  not isinstance(e.attrs.get("power"), (int, float)) or isinstance(e.attrs.get("power"), bool),
                                  is_on(e) is True, e.name.lower()))
        return cands[:MAX_GUESSES]

    async def _ask(self, uid: str, tablet: Entity, plug: Entity) -> None:
        guesses = self.guesses(tablet, plug.id)
        qid = f"q{int(self.clock() * 1000):x}"
        text = f"{tablet.name} didn't start charging from {plug.name}. Which plug is it on?"
        q = self._question[uid] = {"id": qid, "text": text, "at": self.clock(), "charger": plug.id,
                                   "choices": [{"id": e.id, "name": e.name, "icon": KINDS[e.kind].command("turn_on").icon}
                                               for e in guesses]}
        log.info("Smart charging: %s", text)
        self.store.log_event("problem", text, "Smart charging", tablet.id)
        buttons = self.buttons(q)
        await self.notifier.send(Notification(kind="followup", title="Smart charging", body=text, time_sensitive=True,
                                              buttons=buttons, category=category_for(buttons),
                                              action_subject=f"charging:{uid}:{qid}", collapse_id=f"charging-{uid}-plug"),
                                 people=self._admins())

    @staticmethod
    def buttons(q: dict[str, Any]) -> list[dict[str, Any]]:
        return ([{"id": f"charger:{c['id']}", "label": c["name"], "icon": c.get("icon") or "power",
                  "sensitive": False}
                 for c in q["choices"]] + [{"id": NONE_OF_THESE, "label": "None of these", "icon": "x", "sensitive": False}])

    def categories(self) -> list[dict[str, Any]]:
        """The button sets of the questions open now (the iOS app registers these with automations')."""
        out = []
        for q in self._question.values():
            b = self.buttons(q)
            out.append({"id": category_for(b), "buttons": b})
        return out

    async def press(self, subject: str, button: str, who: str, background: bool = False) -> dict[str, Any]:
        """A notification button: subject "charging:<account id>:<question id>"."""
        _, uid, qid = (subject.split(":", 2) + ["", ""])[:3]
        if button == NONE_OF_THESE:
            return await self.answer(uid, qid, None, who, background)
        if button.startswith("charger:"):
            return await self.answer(uid, qid, button.split(":", 1)[1], who, background)
        raise ActionError("That button isn't one of the question's")

    async def answer(self, uid: str, qid: str, plug_id: str | None, who: str,
                     background: bool = False) -> dict[str, Any]:
        """Which plug the tablet is on (one of the question's choices), or None: none of these.
        With `background` (a notification button) the reply doesn't wait for the plug to be switched
        on; a plug that doesn't answer is reported to the admins the usual way (_unresponsive)."""
        q = self._question.get(uid)
        if not q or q["id"] != qid:
            raise ActionError("That question has already been answered")
        if plug_id is not None and plug_id not in {c["id"] for c in q["choices"]}:
            raise ActionError("That plug isn't one of the choices")
        self._question.pop(uid, None)
        t = self.tablet(uid)
        tname = t.name if t else "the wall tablet"
        if plug_id is None:
            self.store.log_event("automation", "Not on any of the plugs offered", f"Smart charging for {tname}, "
                                 f"answered by {who}", t.id if t else None)
            return {"ok": True, "charger": self.settings(uid)["charger"]}
        s = self.settings(uid)
        s.update(charger=plug_id, learned=False, learnedAt=None, chosenBy="user")
        self.save(uid, s)
        plug = self.hub.get(plug_id)
        name = plug.name if plug else plug_id
        self.store.log_event("automation", f"Charges from {name}", f"Smart charging for {tname}, picked by {who}",
                             t.id if t else None)
        self._zone.pop(uid, None)
        self._pending.pop(uid, None)
        if t and s["enabled"]:
            if background:
                self._spawn(self._act(uid, s, t, "turn_on"))
                return {"ok": True, "accepted": True, "charger": plug_id}
            await self._act(uid, s, t, "turn_on")
        return {"ok": True, "charger": plug_id}

    def _admins(self) -> list[str]:
        return [u["person_id"] for u in self.store.users()
                if u["role"] == "admin" and not u["disabled"] and u.get("person_id")]

    def _unresponsive(self, uid: str, command: str) -> None:
        """The charger can't be switched now: try again when it's back; tell the admins (if it should
        be charging) when it still isn't after a grace period."""
        p = self._pending.get(uid)
        if p and p["command"] == command:
            return
        p = self._pending[uid] = {"command": command, "at": self.clock()}
        self._spawn(self._still_unresponsive(uid, p))

    async def _still_unresponsive(self, uid: str, p: dict[str, Any]) -> None:
        await asyncio.sleep(self.plug_grace)
        s, t = self.settings(uid), self.tablet(uid)
        if self._pending.get(uid) is not p or not s["enabled"] or not t:
            return
        plug = self.hub.get(s["charger"] or "")
        if plug and plug.available and is_on(plug) is (p["command"] == "turn_on"):
            self._pending.pop(uid, None)  # it's answering and already as wanted: nothing went wrong
            return
        name = plug.name if plug else "its charger"
        if p["command"] == "turn_on":
            await self._alert(uid, t, f"{t.name} couldn't turn on {name}: it's not responding")
        else:
            self.store.log_event("problem", f"Couldn't turn off {name}", f"Smart charging for {t.name}: "
                                 "it's not responding", t.id)

    async def _alert(self, uid: str, tablet: Entity, text: str) -> None:
        now = self.clock()
        if now - self._alerted.get(uid, -1e18) < ALERT_EVERY_S:
            return
        self._alerted[uid] = now
        await self._tell_admins(uid, tablet, text)

    async def _tell_admins(self, uid: str, tablet: Entity, text: str, topic: str = "battery") -> None:
        self.store.log_event("problem", text, "Smart charging", tablet.id)
        admins = self._admins()
        cid = f"charging-{uid}" if topic == "battery" else f"charging-{uid}-{topic}"
        await self.notifier.send(Notification(kind="followup", title="Smart charging", body=text, time_sensitive=True,
                                              collapse_id=cid), people=admins)

    # -- learning which plug a tablet charges from -----------------------------------------------------

    async def _learn_later(self) -> None:
        # Wait until every flip that could make this one ambiguous would have been seen.
        await asyncio.sleep(2 * self.learn_window + 0.05)
        await self.learn()

    async def learn(self) -> None:
        now, w = self.clock(), self.learn_window
        for tf in self._tablet_flips:
            if tf["done"] or now - tf["at"] < 2 * w:
                continue
            tf["done"] = True
            switches = [x for x in self._switch_flips if abs(x["at"] - tf["at"]) <= w]
            if len(switches) != 1 or switches[0]["on"] != tf["on"]:
                continue  # none, several, or the wrong way round
            sw = switches[0]
            if len({x["uid"] for x in self._tablet_flips if abs(x["at"] - sw["at"]) <= w}) != 1:
                continue  # several tablets started / stopped charging then
            await self._learned(tf["uid"], sw["id"], now)
        keep = now - 4 * w
        self._tablet_flips = [x for x in self._tablet_flips if x["at"] >= keep or not x["done"]]
        self._switch_flips = [x for x in self._switch_flips if x["at"] >= keep]

    async def _learned(self, uid: str, plug_id: str, now: float) -> None:
        s = self.settings(uid)
        if not s["enabled"] or (s["charger"] == plug_id and s["learned"]):
            return
        confirmed = s["charger"] == plug_id  # picked by someone, now seen to work
        s.update(charger=plug_id, learned=True, learnedAt=now)
        if not confirmed:
            s["chosenBy"] = "learned"
        self.save(uid, s)
        t, plug = self.tablet(uid), self.hub.get(plug_id)
        name = plug.name if plug else plug_id
        log.info("Smart charging: %s charges from %s", t.name if t else uid, name)
        self.store.log_event("automation", f"{'Confirmed' if confirmed else 'Learned'}: charges from {name}",
                             f"Smart charging for {t.name if t else 'a wall tablet'}", t.id if t else None)

    # -- for the API -------------------------------------------------------------------------------------

    def to_json(self, uid: str) -> dict[str, Any]:
        s, t = self.settings(uid), self.tablet(uid)
        plug = self.hub.get(s["charger"]) if s["charger"] else None
        choices = sorted(({"id": e.id, "name": e.name, "group": KINDS[e.kind].plural, "available": e.available}
                          for e in self.hub.all() if switchable(e.kind)),
                         key=lambda c: (c["group"].lower(), c["name"].lower()))
        return {**s, "chargerName": plug.name if plug else None, "device": t.id if t else None,
                "criticalLevel": CRITICAL_LEVEL, "choices": choices,
                "question": self._question.get(uid)}
