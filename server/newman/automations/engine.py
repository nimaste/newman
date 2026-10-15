"""The automation engine.

An automation fires (time / device change / device event) and produces a
*run*. A run remembers which devices were pending (e.g. unlocked), which
buttons it offered and who it notified, and stays open until those devices
are resolved, it's dismissed, or it expires. That's what makes follow-ups
("Chris locked Side Door and Laundry"), snooze and reminders work - and what
lets the action endpoint refuse any button the run didn't offer.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime, timedelta
from itertools import combinations
from typing import Any

from .. import sun
from ..integrations.base import CommandError
from ..integrations.manager import IntegrationManager
from ..kinds import KINDS, Command, find_property, format_value, join_names
from ..push.notifier import Notification, Notifier, category_for  # noqa: F401 (charging imports it from here)
from ..state import Entity, Hub
from ..store import Store, new_id
from . import describe as d

log = logging.getLogger(__name__)

RUN_TTL_S = 12 * 3600
ATTRIBUTION_WINDOW_S = 180   # a state change this soon after a button press is credited to the presser
WHO_WAIT_S = 8.0             # how long a notification waits for a device's "who" to be looked up
COMMAND_CONFIRM_S = 60
REFUSAL_SETTLE_S = 8  # how long a "reverted" state must hold before a command counts as refused
MAX_REMINDERS = d.DEFAULT_REMIND_TIMES  # reminders per run when an automation's followUp.remindTimes isn't set
# How long a pressed button's command may take to be handed to the device's connection (the
# connection's own limits come first, e.g. Ring's 10 s); then it counts as failed and the presser is told.
PRESS_COMMAND_S = 20.0
# Follow-ups go out per device as each pending one is confirmed ("Chris locked Laundry"); devices
# confirmed this close together (Lock all, or two by hand) share one ("… locked Side Door and Laundry").
# A device confirmed while others are still pending waits at most this long for company.
FOLLOW_UP_COMBINE_S = 10.0
# One notification per person per change: a device change a follow-up reports (sent, or queued and
# certain to go out) isn't pushed again by another automation's notification about the same change
# (same device, same new state, this close in time) to the people the follow-up is for. A notification
# that gets there before the run has seen the change is held this long, then sent late to anyone no
# follow-up told after all.
ANSWERED_WINDOW_S = 15.0
# "Brighten / Dim while held": one step this often (each step waits for the one before to be sent, so a
# light or its connection never gets more than this), and never for longer than RAMP_MAX_S - in case the
# button's "let go" never arrives.
RAMP_EVERY_S = 0.4
RAMP_MAX_S = 10.0
# A light that ramps by itself (Integration.can_move) is started once and stopped on "let go" instead, at a
# speed that takes it from lowest to brightest in RAMP_FULL_S (about a dimmer remote's own hold); where it
# stopped is waited for up to RAMP_SETTLE_S afterwards, for the run's log.
RAMP_FULL_S = 3.5
RAMP_SETTLE_S = 2.0


class ActionError(Exception):
    pass


def _remind_times(auto: dict[str, Any]) -> int:
    """How many reminders a run sends (followUp.remindTimes; older automations: MAX_REMINDERS)."""
    n = auto["followUp"].get("remindTimes")
    return n if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= d.MAX_REMIND_TIMES else MAX_REMINDERS


class Engine:
    def __init__(self, hub: Hub, store: Store, notifier: Notifier, integrations: IntegrationManager) -> None:
        self.hub, self.store, self.notifier, self.integrations = hub, store, notifier, integrations
        self.video: Any = None  # video.Video, set by the API: pictures from a device's "Video from" camera
        self.live: Any = None  # live.LiveStatuses, set by the API: "Show a live status"
        sun.use_store(store)  # "at sunset" / "only when it's dark" use the home's spot
        self.autos: dict[str, dict[str, Any]] = {a["id"]: d.normalize(a) for a in store.automations()}
        self.open_runs: dict[str, dict[str, Any]] = {}
        self._timers: dict[str, list[asyncio.Task]] = {}
        self._attribution: dict[str, tuple[str, float]] = {}  # entity -> (who, when)
        self._stable: dict[str, str] = {}  # entity -> last settled (non-transitional) state
        self._moments: dict[str, dict[int, float]] = {}  # automation -> {trigger item: when it last happened}
        # "…for 3 minutes" parts counting down: (automation, item, device) -> timer
        self._waiting: dict[tuple[str, int, str], asyncio.Task] = {}
        self._expect: dict[str, dict[str, Any]] = {}  # entity -> command awaiting confirmation
        self._holds: dict[str, dict[str, Any]] = {}   # "auto|entity" -> timed action waiting to be undone
        self._fired: set[str] = set()
        self._presses: set[asyncio.Task] = set()  # button presses whose commands are still being sent
        self._ramps: dict[str, dict[str, Any]] = {}  # entity -> its "…while held" action: {until, stop, task}
        self._heard: dict[tuple[str, str], float] = {}  # (entity, event) -> when it last happened (monotonic)
        # Device changes a follow-up reports (sent or queued): {entity, state, at, people, automationId, name}
        self._answered: list[dict[str, Any]] = []
        # Confirmed devices waiting to be reported together: run id -> {auto, devices, task}
        self._batches: dict[str, dict[str, Any]] = {}
        self._held: set[asyncio.Task] = set()  # notifications held for a follow-up that should cover them
        # Called with each alert an automation shows on wall tablets (the API sends it to them live).
        self.wall_listeners: list[Any] = []
        # Called (no arguments) after an automation is saved or deleted - the voice name index.
        self.listeners: list[Any] = []
        hub.subscribe(self._on_change)
        hub.subscribe_events(self._on_event)
        hub.subscribe_changes(self._on_values)

    # -- CRUD ------------------------------------------------------------------------

    def tablet_names(self) -> dict[str, str]:
        return {u["id"]: u["name"] for u in self.store.users() if u["role"] == "tablet"}

    def list(self) -> list[dict[str, Any]]:
        people, tablets = self.store.people(), self.tablet_names()
        out = []
        for a in self.autos.values():
            last = self.store.runs(a["id"], limit=1)
            nxt = self.next_run(a)
            out.append({**a, "summary": d.summary(a, self.hub, people, tablets),
                        "lastRun": last[0] if last else None, "nextRun": nxt.timestamp() if nxt else None})
        return out

    def save(self, auto: dict[str, Any]) -> dict[str, Any]:
        # summary/lastRun/nextRun are what list() adds; "_" keys are a client's own bookkeeping.
        auto = d.normalize({k: v for k, v in auto.items()
                            if k not in ("summary", "lastRun", "nextRun") and not k.startswith("_")})
        if errs := d.errors(auto, self.hub):
            raise ValueError("; ".join(errs))
        auto["id"] = auto.get("id") or new_id("auto")
        if not auto.get("name"):
            auto["name"] = "New automation"
        self.autos[auto["id"]] = auto
        self.store.save_automation(auto)
        self._changed()
        return auto

    def delete(self, auto_id: str) -> bool:
        self.autos.pop(auto_id, None)
        gone = self.store.remove_automation(auto_id)
        self._changed()
        return gone

    def _changed(self) -> None:
        for listener in list(self.listeners):
            try:
                listener()
            except Exception:  # a listener must never break saving
                log.exception("Automation listener failed")

    def describe(self, auto: dict[str, Any]) -> dict[str, Any]:
        people = self.store.people()
        return {"summary": d.summary(auto, self.hub, people, self.tablet_names()), "preview": d.preview(auto, self.hub, people),
                "errors": d.errors(auto, self.hub)}

    def used_by(self, entity_id: str) -> list[str]:
        out = []
        for a in self.autos.values():
            t = a["trigger"]
            if (entity_id in d.watched_entities(a) or entity_id in a["condition"].get("entities", [])
                    or any(i.get("entity") == entity_id or entity_id in i.get("entities", []) for i in t.get("items", []))
                    or any(b.endswith(":" + entity_id) for b in a["notify"].get("buttons", []))
                    or any(entity_id in d.action_targets(x) for x in a.get("actions", []))
                    or entity_id in d.live_entities(a)):
                out.append(a["id"])
        return out

    def categories(self) -> list[dict[str, Any]]:
        """Every button combination a run could show - the iOS app registers these."""
        seen: dict[str, list] = {}
        for a in self.autos.values():
            if not a["notify"].get("enabled") or not a["notify"].get("buttons"):
                continue
            watched = d.watched_entities(a)
            subsets: list[list[str] | None] = [None]
            if a["notify"].get("onlyPending", True) and 0 < len(watched) <= 4:
                subsets = [list(c) for n in range(1, len(watched) + 1) for c in combinations(watched, n)]
            for sub in subsets:
                buttons = d.buttons_for(a, self.hub, sub)
                if cat := category_for(buttons):
                    seen[cat] = buttons
        return [{"id": k, "buttons": v} for k, v in seen.items()]

    # -- scheduling --------------------------------------------------------------------

    def next_run(self, auto: dict[str, Any], after: datetime | None = None) -> datetime | None:
        t = d.normalize_trigger(auto["trigger"])
        # Only when a time on its own starts it (under "all" it also needs the other things to be true).
        if not auto.get("enabled") or (t["type"] != "time" and t["match"] != "any"):
            return None
        now = after or datetime.now()
        best = None
        dates = d.clean_dates((auto.get("condition") or {}).get("dates"))
        for item in t["items"]:
            if item["type"] != "time" or not item["days"]:
                continue
            # "Only between dates" can push the next run up to a year away.
            for offset in range(374 if dates else 8):
                day = now.date() + timedelta(days=offset)
                if dates and not d.dates_ok(dates, day):
                    continue
                try:  # a sun part's time moves day by day
                    hh, mm = (int(x) for x in (d.item_hhmm(item, day) or "").split(":")[:2])
                except ValueError:
                    break
                cand = datetime.combine(day, datetime.min.time()).replace(hour=hh, minute=mm)
                if cand > now and cand.weekday() in item["days"]:
                    best = min(best, cand) if best else cand
                    break
        return best

    def next_runs(self) -> list[dict[str, Any]]:
        out = [{"automationId": a["id"], "name": a["name"], "at": n.timestamp()}
               for a in self.autos.values() if (n := self.next_run(a))]
        return sorted(out, key=lambda x: x["at"])

    async def run_scheduler(self) -> None:
        await asyncio.sleep(30)  # give integrations a moment to report current states first
        self.resume_holds()
        while True:
            now = datetime.now()
            await asyncio.sleep(60 - now.second - now.microsecond / 1e6 + 0.2)
            now = datetime.now()
            hhmm, key_day = now.strftime("%H:%M"), now.strftime("%Y-%m-%d")
            for a in list(self.autos.values()):
                if a.get("enabled") and (hits := d.time_item_hit(a, hhmm, now.weekday(), now.date())):
                    key = f"{a['id']}@{key_day}T{hhmm}"
                    if key not in self._fired:
                        self._fired.add(key)
                        asyncio.create_task(self._item_fired(a, hits, {"type": "time"}))
            await self._expire_runs()
            if len(self._fired) > 5000:
                self._fired.clear()

    async def _safe_trigger(self, auto: dict[str, Any], trig: dict[str, Any]) -> None:
        try:
            await self.trigger(auto, trig)
        except Exception:
            log.exception("Automation %s failed", auto.get("name"))

    # -- triggers ------------------------------------------------------------------------

    async def _on_change(self, old: Entity | None, new: Entity) -> None:
        if old is None:
            return
        k = KINDS[new.kind]
        if new.state in k.transitional:
            if exp := self._expect.get(new.id):
                exp["moving"] = True
            return  # a lock motor running - wait for where it settles
        if new.state not in dict(k.states):
            return
        if exp := self._expect.get(new.id):
            if new.state == exp["target"]:
                self._expect.pop(new.id, None)
                if new.attrs.get("commandFailures"):
                    await self.hub.update(new.id, attrs={"commandFailures": 0})
            elif exp.get("moving") and not exp.get("checking"):
                # Went LOCKED -> UNLOCKING -> LOCKED: probably refused. Ring sometimes slips a stale
                # report in mid-motion (seen live: LOCKING -> UNLOCKED -> LOCKED within 5 s), so only
                # call it refused if it's still not there after a short settle.
                exp["checking"] = True
                asyncio.create_task(self._check_refused(new.id, exp))
        # Compare against the last *settled* state, so LOCKED -> UNLOCKING ->
        # LOCKED (a refused unlock) is correctly seen as no change at all.
        prev = self._stable.get(new.id, None if old.state in k.transitional else old.state)
        self._stable[new.id] = new.state
        self._resolve_pending(new)
        if prev is None or prev == new.state:
            return  # first state after startup, or nothing really changed
        wants = [a for a in list(self.autos.values()) if a.get("enabled") and d.state_item_hit(a, new)]
        changed_at = time.time()
        by = self._recent_attribution(new.id) if wants else None
        if wants and by is None and (new.attrs or {}).get("whoLookup"):
            by = await self._wait_for_who(new.id, changed_at)  # e.g. Ring saying who unlocked the door
        how = self._recent_how(new.id, changed_at) if wants and by is None else None
        for a in wants:
            if hits := d.state_item_hit(a, new):
                await self._hit(a, hits, new, {"type": "state", "entity": new.id, "from": prev,
                                               "to": new.state, "by": by, "how": how, "changedAt": changed_at})

    async def _on_values(self, old: Entity | None, new: Entity) -> None:
        """A device reported something new (temperature, humidity…): check "value" parts of triggers."""
        if old is None:
            return
        self._cancel_broken(new)
        for a in list(self.autos.values()):
            if a.get("enabled") and (hits := d.property_item_hit(a, old, new)):
                i = a["trigger"]["items"][hits[0]]
                p = find_property(new, i["property"])
                await self._hit(a, hits, new, {"type": "property", "entity": new.id, "property": i["property"],
                                               "value": p["value"] if p else None})

    # -- "…for 3 minutes": a part only counts once it has held that long -----------------------

    async def _hit(self, auto: dict[str, Any], hits: list[int], ent: Entity, trig: dict[str, Any]) -> None:
        now = []
        for n in hits:
            seconds = d.hold_for(auto["trigger"]["items"][n])
            if seconds <= 0:
                now.append(n)
            elif (auto["id"], n, ent.id) not in self._waiting:  # already counting: keep the first start
                self._waiting[(auto["id"], n, ent.id)] = asyncio.create_task(
                    self._fire_after(auto["id"], n, ent.id, seconds, trig))
        if now:
            await self._item_fired(auto, now, trig)

    def _cancel_broken(self, ent: Entity) -> None:
        """The device changed: any countdown it no longer satisfies starts over (but a lock motor
        running - UNLOCKING, opening… - is only in between, not a change)."""
        if ent.state in KINDS[ent.kind].transitional:
            return
        for key in [k for k in self._waiting if k[2] == ent.id]:
            auto = self.autos.get(key[0])
            items = auto["trigger"]["items"] if auto else []
            if key[1] >= len(items) or not d.item_holds(items[key[1]], ent):
                self._waiting.pop(key).cancel()

    async def _fire_after(self, aid: str, n: int, eid: str, seconds: float, trig: dict[str, Any]) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        self._waiting.pop((aid, n, eid), None)
        auto, ent = self.autos.get(aid), self.hub.get(eid)
        items = auto["trigger"]["items"] if auto else []
        if auto and auto.get("enabled") and ent and n < len(items) and d.item_holds(items[n], ent):
            await self._item_fired(auto, [n], dict(trig, held=seconds))

    async def _on_event(self, ent: Entity, event: str, data: dict[str, Any]) -> None:
        # Noted before anything waits: a "let go" can be handled alongside the "held" that started a ramp.
        self._heard[(ent.id, event)] = time.monotonic()
        for r in self._ramps.values():
            if r["until"] == (ent.id, event):
                r["stop"].set()
        for a in list(self.autos.values()):
            if a.get("enabled") and (hits := d.event_item_hit(a, ent.id, event)):
                await self._item_fired(a, hits, {"type": "event", "entity": ent.id, "event": event, **data})

    async def _item_fired(self, auto: dict[str, Any], hits: list[int], trig: dict[str, Any]) -> None:
        """One part of an automation's "When" just happened. Under "any" that starts it; under "all"
        it only starts once every part is true together (states now, events/times within the window)."""
        t = auto["trigger"]
        if t.get("match") != "all" or len(t["items"]) < 2:
            await self._safe_trigger(auto, trig)
            return
        now = time.time()
        moments = self._moments.setdefault(auto["id"], {})
        if trig["type"] != "state":
            for n in hits:
                moments[n] = now
        if d.all_true(auto, self.hub, moments, now):
            moments.clear()  # the events have to happen again before it fires again
            await self._safe_trigger(auto, trig)

    async def _wait_for_who(self, entity_id: str, changed_at: float) -> str | None:
        """A device whose "who" arrives a little later (looked up from its maker): wait up to
        WHO_WAIT_S for it, so the notification can say who. Without it, carry on regardless - and
        straight away once the device says how it changed with no one behind it ("manually")."""
        until = time.time() + WHO_WAIT_S
        while time.time() < until:
            await asyncio.sleep(0.5)
            if who := self._recent_attribution(entity_id):
                return who
            ent = self.hub.get(entity_id)
            if ent and ent.attrs.get("changedByNoOne") and self._recent_how(entity_id, changed_at):
                return None
        return None

    def _recent_how(self, entity_id: str, changed_at: float) -> str | None:
        """How the device says this change happened ("manually", "with a keypad code"), if it said so
        for this change."""
        ent = self.hub.get(entity_id)
        attrs = (ent.attrs or {}) if ent else {}
        at = attrs.get("changedHowAt")
        if attrs.get("changedHow") and at and float(at) >= changed_at - 2:
            return str(attrs["changedHow"])
        return None

    def _recent_attribution(self, entity_id: str) -> str | None:
        """Who just changed a device: someone using Newman (we sent the command), else whoever the
        device itself reports (e.g. the alarm's keypad code or Ring app user), if that's fresh."""
        who = self._attribution.get(entity_id)
        if who and time.time() - who[1] < ATTRIBUTION_WINDOW_S:
            return who[0]
        ent = self.hub.get(entity_id)
        at = (ent.attrs or {}).get("changedAt") if ent else None
        if at and ent.attrs.get("changedBy") and abs(time.time() - float(at)) < ATTRIBUTION_WINDOW_S:
            return str(ent.attrs["changedBy"])
        return None

    # -- runs -------------------------------------------------------------------------------

    async def trigger(self, auto: dict[str, Any], trig: dict[str, Any], force: bool = False) -> dict[str, Any]:
        auto = d.normalize(auto)
        watched = d.watched_entities(auto)
        run: dict[str, Any] = {
            "id": new_id("run"), "automationId": auto["id"], "name": auto["name"], "startedAt": time.time(),
            "status": "sent", "trigger": trig, "pending": [], "resolved": {}, "buttons": [],
            "recipients": auto["recipients"], "resolvedBy": None, "log": [], "forced": force,
        }

        def note(text: str) -> None:
            run["log"].append({"ts": time.time(), "text": text})

        t = dict(auto["trigger"])
        # A mixed "When" runs like whichever of its parts started it.
        if trig.get("type") in ("time", "state", "event", "property"):
            t["type"] = trig["type"]
        if not force and not d.dates_allow(auto):
            run["status"] = "skipped"
            note(f"Outside its dates - only {d.dates_text(auto['condition'].get('dates'))}")
            self.store.save_run(run)
            return run
        if not force and not (d.in_window(auto["condition"].get("between")) and d.on_day(d.limited_days(auto))
                              and d.sun_allows(auto)):
            run["status"] = "skipped"
            note(f"Outside its hours ({d.window_text(auto).replace('only ', '')})")
            self.store.save_run(run)
            return run
        if t["type"] != "time" and not force and not d.conditions_met(auto, self.hub):
            run["status"] = "skipped"
            note("Condition not met - " + (d.condition_text(auto, self.hub) or "devices not in the right state"))
            self.store.save_run(run)
            return run
        if t["type"] == "time":
            ents = [e for e in (self.hub.get(i) for i in watched) if e]
            active = [e.id for e in ents if e.is_active]
            unknown = [e.name for e in ents if e.state is None or not e.available]
            rows = d.guard_rows(auto)
            ok = d.conditions_met(auto, self.hub)
            # Pending = the devices in the state the check looks for ("only if none…" rows add nothing).
            active = d.pending_from(auto, self.hub)
            if unknown:
                note(f"Couldn't read {join_names(unknown)}")
            if not ok and not force:
                run["status"] = "skipped"
                if len(rows) == 1 and rows[0]["match"] == "any":
                    note(f"Nothing to do - none {d.state_word(d._ents(self.hub, rows[0]['entities']), rows[0]['state'])}")
                else:
                    note("Condition not met - " + (d.condition_text(auto, self.hub) or "devices not in the right state"))
                self.store.save_run(run)
                return run
            run["pending"] = active
            note(f"Fired at {time.strftime('%-I:%M %p')}" + (" (test run)" if force else ""))
        elif t["type"] == "state":
            ent = self.hub.get(trig.get("entity", ""))
            run["pending"] = [ent.id] if ent and ent.is_active else []
            note(f"{ent.name if ent else 'Device'} changed to {trig.get('to')}" + (f" by {trig['by']}" if trig.get("by")
                                                                                   else f" {trig['how']}" if trig.get("how") else ""))
        elif t["type"] == "property":
            ent = self.hub.get(trig.get("entity", ""))
            p = find_property(ent, trig.get("property", "")) if ent else None
            note(f"{ent.name if ent else 'Device'}: {p['label'].lower()} is {format_value(p, trig.get('value'))}" if p
                 else "Test run" if trig.get("manual") else f"{self.hub.name(trig.get('entity', ''))} reported a new value")
        else:
            note(f"{self.hub.name(trig.get('entity', ''))} {trig.get('event', 'event')}")

        # Automatic command first, so the notification can say it's handled.
        ac = auto["autoCommand"]
        if ac.get("enabled") and run["pending"]:
            for eid in run["pending"]:
                await self._command(run, eid, ac.get("command", "lock"), "Newman")
        for action in d.expand_actions(auto.get("actions", [])):
            try:
                await self._run_action(auto, run, action)
            except (ActionError, KeyError) as e:
                note(f"Couldn't {d.action_text(action, self.hub)}: {e}")
        if auto["live"].get("enabled") and self.live is not None:
            try:
                note(await self.live.start_for_automation(auto, run))
            except Exception:
                log.exception("Live status for %s failed", auto.get("name"))

        if auto["notify"].get("enabled"):
            shown_for = run["pending"] if run["pending"] else (watched if force else [])
            run["buttons"] = d.buttons_for(auto, self.hub, shown_for)
            title, body = d.notification_text(auto, self.hub, pending=run["pending"], trigger=trig)
            snapshot = trig.get("snapshot_id") if auto["notify"].get("snapshot", True) else None
            if auto["notify"].get("snapshot", True) and trig.get("type") == "event" and self.video:
                # A device showing another camera's video: the picture comes from that camera.
                snapshot = await self.video.snapshot(trig.get("entity") or "") or snapshot
            people = self.store.people()
            # Someone already in a conversation this change answers hears it from that conversation's
            # follow-up (sent or queued). If the run hasn't seen the change yet, hold it for them.
            covered, expected = self._covered_by_follow_up(auto, trig)
            to = [p for p in auto["recipients"] if p not in covered and p not in expected]
            sent = await self._notify(auto, run, title, body, snapshot, people=to) if to else 0
            for by in dict.fromkeys(covered.values()):
                who = [p for p, n in covered.items() if n == by]
                note(f"Not sent to {join_names(d.people_names(people, who)) or 'someone'}: covered by {by}'s follow-up")
            for by in dict.fromkeys(expected.values()):
                who = [p for p, n in expected.items() if n == by]
                note(f"Held for {join_names(d.people_names(people, who)) or 'someone'}: {by}'s follow-up should cover it")
            if expected:
                t = asyncio.create_task(self._send_if_uncovered(auto, run, trig, list(expected), title, body, snapshot))
                self._held.add(t)
                t.add_done_callback(self._held.discard)
            if to or not (covered or expected):
                note(f"Notified {join_names(d.people_names(people, to)) or 'nobody'}"
                     f" ({sent} phone{'s' if sent != 1 else ''})")
            self.store.log_event("automation", title, body, trig.get("entity"),
                                 {"runId": run["id"], "automationId": auto["id"],
                                  **({"snapshotId": snapshot} if snapshot else {}),
                                  **({"clip": trig["clip"]} if trig.get("clip") else {})})

        if auto["wall"].get("enabled"):
            await self._show_on_wall(auto, run, trig)

        stays_open = run["pending"] and (auto["followUp"].get("enabled") or any(
            ":" in b["id"] or b["id"].endswith("_all") for b in run["buttons"]))
        if stays_open:
            self.open_runs[run["id"]] = run
            if (mins := auto["followUp"].get("remindMinutes")) and auto["followUp"].get("enabled"):
                self._schedule(run["id"], self._remind_loop(run["id"], mins, _remind_times(auto)))
        else:
            run["status"] = "done"
        self.store.save_run(run)
        return run

    # -- actions with a duration ("for 5 minutes, then put it back") ------------------------------

    async def _run_action(self, auto: dict[str, Any], run: dict[str, Any], action: dict[str, Any]) -> None:
        eid, seconds = action["entity"], d.hold_seconds(action)
        key = f"{auto['id']}|{eid}"
        held = self._holds.get(key)
        ent = self.hub.get(eid)
        cmd = KINDS[ent.kind].action(action["command"]) if ent else None
        if cmd and cmd.ramp:
            self._start_ramp(run, ent, cmd)
            return
        # Remember how the device was *before* this automation first touched it; a re-trigger while
        # the timer runs keeps that original state and just restarts the clock.
        before = held["before"] if held else (
            {"state": ent.state, "brightness": ent.attrs.get("brightness")} if ent else None)
        # A device that keeps more than its state (a music player's queue and spot) snapshots itself.
        owner = self._owner(eid) if seconds > 0 else None
        if not hasattr(owner, "snapshot"):
            owner = None
        snap = held.get("snap") if held else (owner.snapshot(eid) if owner else None)
        await self._command(run, eid, action["command"], "Newman", action.get("value"))
        if held and held.get("task"):
            held["task"].cancel()
        if seconds > 0 and before is not None:
            hold = {"entity": eid, "before": before, "command": action["command"], "value": action.get("value"),
                    "due": time.time() + seconds, "run": run["id"], "during": d.duration_text(action)}
            if snap is not None and owner:
                hold["snap"], hold["marker"] = snap, owner.marker(eid)
            self.store.save_hold(key, hold)  # survives restarts - holds can last hours or days
            self._holds[key] = {**hold, "task": asyncio.create_task(self._restore_later(key, seconds))}
            run["log"].append({"ts": time.time(), "text": f"Will put {self.hub.name(eid)} back in {d.duration_text(action)}"})
        else:
            self._holds.pop(key, None)
            self.store.remove_hold(key)

    # -- "Brighten / Dim while held" -----------------------------------------------------------------

    def _start_ramp(self, run: dict[str, Any], ent: Entity, cmd: Command) -> None:
        """Ramp the device's level (cmd.attr) - by itself where it can, else in steps of cmd.step - until the
        button whose event started this run is let go (that event's `ends`) or RAMP_MAX_S. One ramp per
        device: a new one takes over."""
        trig = run["trigger"]
        src = self.hub.get(trig.get("entity") or "") if trig.get("type") == "event" else None
        ends = KINDS[src.kind].event(str(trig.get("event") or "")).ends if src else ""
        if old := self._ramps.get(ent.id):
            old["stop"].set()
        r: dict[str, Any] = {"until": (src.id, ends) if ends else None, "stop": asyncio.Event(),
                             "since": self._heard.get((src.id, str(trig.get("event")))) if src else None,
                             "done": asyncio.Event(), "after": old}  # done: it sent its last command
        r["task"] = asyncio.create_task(self._ramp(run, ent.id, cmd, r))
        self._ramps[ent.id] = r

    def _let_go(self, r: dict[str, Any]) -> bool:
        if r["stop"].is_set():
            return True
        # Let go before this ramp even started (both were handled at once).
        at = self._heard.get(r["until"]) if r["until"] else None
        return at is not None and r["since"] is not None and at >= r["since"]

    async def _ramp(self, run: dict[str, Any], eid: str, cmd: Command, r: dict[str, Any]) -> None:
        try:
            if (old := r.pop("after", None)) and not old["done"].is_set():
                # The ramp this one takes over stops first (its "stop" never lands after this one's start).
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(old["done"].wait(), RAMP_SETTLE_S)
            await self._ramp_steps(run, eid, cmd, r)
        except Exception:  # noqa: BLE001 - a ramp must never take the engine down
            log.exception("%s on %s failed", cmd.label, eid)
        finally:
            r["done"].set()
            if self._ramps.get(eid) is r:
                self._ramps.pop(eid)

    async def _held_down(self, r: dict[str, Any], seconds: float) -> bool:
        """Wait up to `seconds` for the button to be let go; True if it's still held after that."""
        if self._let_go(r):
            return False
        try:
            await asyncio.wait_for(r["stop"].wait(), seconds)
            return False
        except asyncio.TimeoutError:
            return not self._let_go(r)

    async def _ramp_steps(self, run: dict[str, Any], eid: str, cmd: Command, r: dict[str, Any]) -> None:
        def note(text: str) -> None:
            run["log"].append({"ts": time.time(), "text": text})
            self.store.save_run(run)
        ent = self.hub.get(eid)
        if not ent:
            return
        k = KINDS[ent.kind]
        name = ent.name
        setter = next((c for c in k.commands if c.attr == cmd.attr and c.takes_value), None)
        if not setter or ent.attrs.get("dimmable") is False:
            note(f"Couldn't {cmd.label.lower()}: {name} can't be dimmed")
            return
        lo, hi, step = cmd.min or 1, cmd.max or 100, cmd.step or 10
        lit = ent.state == k.active_state
        level = ent.attrs.get(cmd.attr) if lit else None
        if lit and not isinstance(level, (int, float)):
            level = hi  # on, but it doesn't say how bright
        if level is None and cmd.ramp < 0:
            note(f"{name} is off - left it off")  # dimming never turns a light on (or off)
            return
        if self.integrations.can_move(eid, cmd.attr):
            await self._ramp_moving(eid, cmd, r, level, note)
            return
        self._attribution[eid] = ("Newman", time.time())
        until, sent = time.monotonic() + RAMP_MAX_S, None
        while True:
            # Off and brightening: on at the first step up, then on from there.
            nxt = max(lo, min(hi, step if level is None else level + cmd.ramp * step))
            if nxt == level:
                break  # as bright (or as dim) as it goes
            try:
                # Each step fades into the next where the light can fade (it then never jumps).
                await self.integrations.command_fading(eid, setter.id, nxt, RAMP_EVERY_S)
            except CommandError as e:
                note(f"Couldn't {cmd.label.lower()} {name}: {e}")
                break
            level = sent = nxt
            if time.monotonic() >= until or not await self._held_down(r, RAMP_EVERY_S):
                break
        r["done"].set()
        if sent is not None:
            text = f"{cmd.past.capitalize()} {name} to {sent:g}{cmd.unit}"
            self.store.log_event("command", text, "by Newman", eid)
            note(text)

    async def _ramp_moving(self, eid: str, cmd: Command, r: dict[str, Any], level: float | None, note: Any) -> None:
        """A light that ramps by itself: one "start moving" when the button goes down and "stop" when it's
        let go (or after RAMP_MAX_S) - smooth, like a dimmer remote's own hold. Up turns an off light on,
        low; down stops at the light's lowest level and never turns it off."""
        ent = self.hub.get(eid)
        name = ent.name
        if level is not None and (level >= (cmd.max or 100) if cmd.ramp > 0 else level <= (cmd.min or 1)):
            return  # already as bright (or as dim) as it goes
        before = (ent.state, ent.attrs.get(cmd.attr))
        self._attribution[eid] = ("Newman", time.time())
        try:
            await self.integrations.move(eid, cmd.attr, cmd.ramp, RAMP_FULL_S)
        except CommandError as e:
            note(f"Couldn't {cmd.label.lower()} {name}: {e}")
            return
        await self._held_down(r, RAMP_MAX_S)
        try:
            await self.integrations.move(eid, cmd.attr, 0, RAMP_FULL_S)
        except CommandError as e:
            note(f"Couldn't stop {name} where it was: {e}")
            return
        finally:
            r["done"].set()
        # Where it stopped comes back from the light itself: wait a moment for it, for the log.
        until = time.monotonic() + RAMP_SETTLE_S
        while (ent := self.hub.get(eid)) and (ent.state, ent.attrs.get(cmd.attr)) == before \
                and time.monotonic() < until:
            await asyncio.sleep(0.05)
        now = ent.attrs.get(cmd.attr) if ent and ent.state == KINDS[ent.kind].active_state else None
        text = f"{cmd.past.capitalize()} {name}" + (f" to {now:g}{cmd.unit}" if isinstance(now, (int, float)) else "")
        self.store.log_event("command", text, "by Newman", eid)
        note(text)

    def resume_holds(self) -> None:
        """After a restart: re-arm pending "put it back" timers (overdue ones run right away)."""
        for key, hold in self.store.holds().items():
            self._holds[key] = {**hold, "task": asyncio.create_task(
                self._restore_later(key, max(0.0, hold["due"] - time.time())))}
        if self._holds:
            log.info("Resumed %d timed action(s)", len(self._holds))

    def _owner(self, entity_id: str) -> Any:
        try:
            return self.integrations.owner(entity_id)
        except (CommandError, AttributeError):
            return None

    def _run_note(self, run_id: str | None, text: str) -> None:
        """A line in a run's log after the run itself has finished (e.g. a put-back)."""
        run = (self.open_runs.get(run_id) or self.store.run(run_id)) if run_id else None
        if run:
            run["log"].append({"ts": time.time(), "text": text})
            self.store.save_run(run)

    async def _restore_snapshot(self, hold: dict[str, Any]) -> None:
        eid = hold["entity"]
        owner = self._owner(eid)
        for _ in range(60):  # just restarted: its connection may still be starting
            if owner:
                break
            await asyncio.sleep(2)
            owner = self._owner(eid)
        name = self.hub.name(eid)
        if not owner:
            self._run_note(hold.get("run"), f"Couldn't put {name} back: its connection isn't running")
            return
        try:
            text = await owner.restore(eid, hold["snap"], hold.get("marker"), f"the {hold.get('during') or 'time'}")
        except CommandError as e:
            text = f"Couldn't put {name} back: {e}"
        self.store.log_event("automation", text, "", eid)
        self._run_note(hold.get("run"), text)

    async def _restore_later(self, key: str, seconds: float) -> None:
        await asyncio.sleep(seconds)
        hold = self._holds.pop(key, None)
        self.store.remove_hold(key)
        if hold and hold.get("snap") is not None:
            await self._restore_snapshot(hold)
            return
        eid = hold["entity"] if hold else ""
        ent = self.hub.get(eid)
        if not hold or not ent:
            return
        k, before = KINDS[ent.kind], hold["before"]
        # Someone changed it by hand in the meantime? Then leave it alone.
        if hold["command"] == "set_brightness":
            changed = ent.state == "off" or (ent.attrs.get("brightness") not in (None, hold["value"])
                                             and abs(int(ent.attrs["brightness"]) - int(hold["value"])) > 2)
        else:
            cmd = k.command(hold["command"])
            changed = bool(cmd and cmd.confirms_to and ent.state != cmd.confirms_to)
        if changed:
            self.store.log_event("automation", f"Left {ent.name} as it is", "It was changed by hand", eid)
            return
        try:
            if ent.kind == "light":
                if before["state"] != "on":
                    await self._command(None, eid, "turn_off", "Newman")
                elif before.get("brightness") is not None and ent.attrs.get("dimmable"):
                    await self._command(None, eid, "set_brightness", "Newman", before["brightness"])
                else:
                    await self._command(None, eid, "turn_on", "Newman")
            else:
                back = next((c for c in k.commands if c.confirms_to == before["state"] and not c.takes_value), None)
                if back and before["state"] != ent.state:
                    await self._command(None, eid, back.id, "Newman")
        except ActionError as e:
            log.warning("Couldn't put %s back: %s", ent.name, e)

    def _covered_by_follow_up(self, auto: dict[str, Any], trig: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
        """Which of this automation's recipients a follow-up tells about this device change, as
        ({person: that automation's name} already covered, {person: name} expected).

        Covered = the change is in a follow-up that has been sent or is queued (queued ones always
        go out - when the combine window ends, when the rest are confirmed, or right away if the run
        is dismissed or expires), so leaving these people out can never lose the change. Expected =
        an open run with a follow-up is still waiting for this device to reach this state (this
        notification got there first): those people are held, and told late if no follow-up ends up
        reporting the change (see `_send_if_uncovered`)."""
        if trig.get("type") != "state" or trig.get("manual") or not trig.get("entity") or trig.get("to") is None:
            return {}, {}
        eid, state = trig["entity"], trig["to"]
        ent = self.hub.get(eid)
        if ent and ent.state == state:
            self._resolve_pending(ent)  # normally already done by the change itself; never misses one
        covered = self._reported_to(auto, eid, state, float(trig.get("changedAt") or time.time()))
        expected: dict[str, str] = {}
        if ent and state != KINDS[ent.kind].active_state:  # the state that answers a pending device
            for run in self.open_runs.values():
                other = self.autos.get(run["automationId"])
                if (other and other["id"] != auto["id"] and other["followUp"].get("enabled")
                        and eid in run["pending"] and eid not in run["resolved"]):
                    for p in run["recipients"]:
                        if p not in covered and p in auto["recipients"]:
                            expected.setdefault(p, other["name"])
        return covered, expected

    def _reported_to(self, auto: dict[str, Any], eid: str, state: str, at: float) -> dict[str, str]:
        """{person: automation name} for this automation's recipients whom another automation's
        follow-up (sent or queued) tells about `eid` reaching `state` around `at`."""
        cutoff = time.time() - 4 * ANSWERED_WINDOW_S
        self._answered = [r for r in self._answered if r["at"] >= cutoff]
        covered: dict[str, str] = {}
        for r in self._answered:
            if (r["entity"] == eid and r["state"] == state and r["automationId"] != auto["id"]
                    and abs(r["at"] - at) <= ANSWERED_WINDOW_S):
                for p in r["people"]:
                    if p in auto["recipients"]:
                        covered.setdefault(p, r["name"])
        return covered

    async def _send_if_uncovered(self, auto: dict[str, Any], run: dict[str, Any], trig: dict[str, Any],
                                 held: list[str], title: str, body: str, snapshot: str | None) -> None:
        """A notification held for people an open run's follow-up should tell: once the window has
        passed, send it to any of them no follow-up told after all (the run was dismissed or expired
        first, or the device never got there) - a change is never left unreported."""
        await asyncio.sleep(ANSWERED_WINDOW_S)
        at = float(trig.get("changedAt") or time.time())
        covered = self._reported_to(auto, trig["entity"], trig["to"], at)
        late = [p for p in held if p not in covered]
        names = join_names(d.people_names(self.store.people(), late)) or "someone"
        if late:
            sent = await self._notify(auto, run, title, body, snapshot, people=late)
            text = f"Sent late to {names} ({sent} phone{'s' if sent != 1 else ''}): no follow-up covered it"
        else:
            text = "Held notification covered by the follow-up"
        run["log"].append({"ts": time.time(), "text": text})
        self.store.save_run(run)

    async def _notify(self, auto: dict[str, Any], run: dict[str, Any], title: str, body: str,
                      snapshot: str | None = None, people: list[str] | None = None) -> int:
        fired = self.hub.get(run["trigger"].get("entity") or "") if run["trigger"].get("type") == "event" else None
        # The event decides its alert sound (e.g. a doorbell press gets the distinct doorbell chime).
        sound = KINDS[fired.kind].event(run["trigger"].get("event") or "").sound if fired else ""
        n = Notification(
            kind="automation", title=title, body=body, buttons=run["buttons"], category=category_for(run["buttons"]),
            thread=auto["id"], sound=f"{sound}.caf" if sound else "default",
            time_sensitive=bool(auto["notify"].get("timeSensitive")), collapse_id=auto["id"],
            snapshot_id=snapshot, action_subject=run["id"],
            data={"run_id": run["id"], "automation_id": auto["id"],
                  # Camera detections: lets the app open that exact event's clip in Optiq.
                  **{k: str(v) for k, v in run["trigger"].items() if k in ("frigate_event_id", "camera", "clip") and v}},
        )
        return await self.notifier.send(n, people=auto["recipients"] if people is None else people)

    async def _show_on_wall(self, auto: dict[str, Any], run: dict[str, Any], trig: dict[str, Any]) -> None:
        """Send the alert to wall tablets: the same words and buttons as the notification, plus the
        camera to show (the device that fired, or the one its snapshot comes from)."""
        w = auto["wall"]
        if not run["buttons"] and auto["notify"].get("buttons"):
            run["buttons"] = d.buttons_for(auto, self.hub, run["pending"] or [])
        title, body = d.notification_text(auto, self.hub, pending=run["pending"], trigger=trig)
        fired = self.hub.get(trig.get("entity") or "")
        if fired and self.hub.has_picture(fired):
            camera = fired.id
        else:
            src = d.snapshot_source(auto, self.hub)
            camera = src.id if src else None
        event_sound = KINDS[fired.kind].event(trig.get("event") or "").sound \
            if fired and trig.get("type") == "event" else ""
        style = w.get("style") if w.get("style") in d.WALL_STYLES else "card"
        alert = {
            "id": run["id"], "automationId": auto["id"], "style": style,
            "title": title, "body": body, "camera": camera, "entity": fired.id if fired else None,
            "sound": (event_sound or ("alarm" if style == "alarm" else "chime")) if w.get("sound") else None,
            "wake": bool(w.get("wake")), "tablets": list(w["tablets"]),
            # Device buttons ("lock:<id>") are pressed like the device itself; the rest via the run.
            "buttons": [dict(b, **({"entity": b["id"].split(":", 1)[1], "command": b["id"].split(":", 1)[0]}
                                   if ":" in b["id"] and not b["id"].startswith("snooze:") else {}))
                        for b in run["buttons"] if b["id"] != "open"],
            "at": time.time(),
        }
        for listener in list(self.wall_listeners):
            try:
                await listener(alert)
            except Exception:
                log.exception("Showing an alert on the wall tablets failed")
        names = [self.tablet_names().get(t, "a removed tablet") for t in w["tablets"]]
        run["log"].append({"ts": time.time(),
                           "text": "Shown on " + (join_names(names) if names else "every wall tablet")})

    def _still_pending(self, run: dict[str, Any]) -> list[str]:
        return [e for e in run["pending"] if e not in run["resolved"]
                and (ent := self.hub.get(e)) is not None and ent.is_active]

    def _schedule(self, run_id: str, coro: Any) -> None:
        self._timers.setdefault(run_id, []).append(asyncio.create_task(coro))

    def _cancel_timers(self, run_id: str) -> None:
        for t in self._timers.pop(run_id, []):
            if t is not asyncio.current_task():
                t.cancel()

    async def _remind(self, run_id: str) -> bool:
        """Re-send if still pending. Returns False once the run is over."""
        run = self.open_runs.get(run_id)
        auto = self.autos.get(run["automationId"]) if run else None
        if not run or not auto:
            return False
        still = self._still_pending(run)
        if not still:
            await self._finish(run)
            return False
        run["buttons"] = d.buttons_for(auto, self.hub, still)
        title, body = d.notification_text(auto, self.hub, pending=still, trigger=run["trigger"], follow_up=True)
        await self._notify(auto, run, title, body)
        run["log"].append({"ts": time.time(), "text": f"Reminded - {join_names([self.hub.name(e) for e in still])} still pending"})
        self.store.save_run(run)
        return True

    async def _remind_loop(self, run_id: str, minutes: int, times: int = MAX_REMINDERS) -> None:
        for _ in range(times):
            await asyncio.sleep(minutes * 60)
            if not await self._remind(run_id):
                return

    async def _snoozed(self, run_id: str, minutes: int) -> None:
        # A snooze replaces the reminders still to come: the snoozed reminder goes out when it ends
        # (it isn't one of the `remindTimes`), then the automation's reminders start over - every
        # `remindMinutes`, up to `remindTimes` more. Each snooze is someone asking to be reminded,
        # so it may repeat; the run still expires after RUN_TTL_S.
        await asyncio.sleep(minutes * 60)
        if await self._remind(run_id):
            run = self.open_runs.get(run_id)
            auto = self.autos.get(run["automationId"]) if run else None
            if auto and (mins := auto["followUp"].get("remindMinutes")) and auto["followUp"].get("enabled"):
                await self._remind_loop(run_id, mins, _remind_times(auto))

    def _resolve_pending(self, ent: Entity) -> None:
        for run in list(self.open_runs.values()):
            if ent.id in run["pending"] and ent.id not in run["resolved"] and ent.state is not None and not ent.is_active:
                who = self._recent_attribution(ent.id)
                run["resolved"][ent.id] = who
                auto = self.autos.get(run["automationId"])
                if auto and auto["followUp"].get("enabled"):
                    # Its follow-up tells this change: queued now, and certain to go out (see _report).
                    self._answered.append({"entity": ent.id, "state": ent.state, "at": time.time(),
                                           "people": list(run["recipients"]), "automationId": auto["id"],
                                           "name": auto["name"]})
                    batch = self._batches.setdefault(run["id"], {"auto": auto, "devices": [], "task": None})
                    batch["devices"].append(ent.id)
                run["log"].append({"ts": time.time(), "text": f"{ent.name} {KINDS[ent.kind].state_label(ent.state).lower()}"
                                   + (f" by {who}" if who else " at the door")})
                if all(e in run["resolved"] for e in run["pending"]):
                    asyncio.create_task(self._finish(run))
                else:
                    # Others still pending: report this one on its own unless more follow shortly.
                    if (batch := self._batches.get(run["id"])) and batch["task"] is None:
                        batch["task"] = asyncio.create_task(self._report_later(run))
                    self.store.save_run(run)

    async def _report_later(self, run: dict[str, Any]) -> None:
        await asyncio.sleep(FOLLOW_UP_COMBINE_S)
        await self._report(run)

    async def _report(self, run: dict[str, Any], final: bool = False) -> None:
        """Send the follow-up for the run's confirmed devices not reported yet ("Chris locked
        Laundry"; together: "Chris locked Side Door and Laundry"). Called when the combine window
        ends, when the run resolves (`final`), and when it's dismissed or expires - so a change the
        follow-up was counted on to tell is always told."""
        batch = self._batches.pop(run["id"], None)
        if not batch or not batch["devices"]:
            return
        if batch["task"] is not None and batch["task"] is not asyncio.current_task():
            batch["task"].cancel()
        auto = batch["auto"]
        devices = sorted(batch["devices"], key=run["pending"].index)  # in the order the alert named them
        resolvers = [run["resolved"].get(e) for e in devices]
        msg = d.follow_up_message(auto, self.hub, pending=devices, resolvers=resolvers)
        # The last one replaces the alert on the phone; an earlier one leaves it (other devices still pending).
        await self.notifier.send(Notification(
            kind="followup", title=auto["name"], body=msg, thread=auto["id"],
            collapse_id=auto["id"] if final else None,
            data={"run_id": run["id"], "automation_id": auto["id"]},
        ), people=run["recipients"])
        run["log"].append({"ts": time.time(), "text": f"Follow-up sent: {msg}"})
        self.store.log_event("followup", auto["name"], msg, None, {"runId": run["id"]})
        self.store.save_run(run)

    async def _finish(self, run: dict[str, Any]) -> None:
        if run["id"] not in self.open_runs:
            return
        self.open_runs.pop(run["id"], None)
        self._cancel_timers(run["id"])
        run["status"] = "resolved"
        resolvers = [run["resolved"].get(e) for e in run["pending"]]
        run["resolvedBy"] = join_names([r for r in dict.fromkeys(resolvers) if r]) or None
        await self._report(run, final=True)
        self.store.save_run(run)

    async def _expire_runs(self) -> None:
        for run in list(self.open_runs.values()):
            if time.time() - run["startedAt"] > RUN_TTL_S:
                self.open_runs.pop(run["id"], None)
                self._cancel_timers(run["id"])
                run["status"] = "expired"
                await self._report(run)  # anything confirmed but not yet told
                self.store.save_run(run)

    # -- buttons & commands ------------------------------------------------------------

    async def _command(self, run: dict[str, Any] | None, entity_id: str, command: str, who: str,
                       value: Any = None) -> None:
        ent = self.hub.get(entity_id)
        if not ent:
            raise ActionError("That device no longer exists")
        cmd = KINDS[ent.kind].command(command)
        if not cmd:
            raise ActionError(f"{ent.name} can't {command}")
        if cmd.takes_value and value is None:
            raise ActionError(f"{cmd.label} needs a value")
        if ent.state == cmd.confirms_to and not cmd.takes_value:
            if run:
                run["log"].append({"ts": time.time(), "text": f"{ent.name} was already {cmd.past}"})
            return
        self._attribution[entity_id] = (who, time.time())
        try:
            await self.integrations.command(entity_id, command, value)
        except CommandError as e:
            raise ActionError(str(e)) from e
        if run:
            run["log"].append({"ts": time.time(), "text": f"{who}: {cmd.label} {ent.name}"})
        self.store.log_event("command", f"{cmd.label} {ent.name}" + (f" to {value}" if cmd.takes_value else ""),
                             f"by {who}", entity_id)
        if cmd.takes_value or not cmd.confirms_to:
            return  # no single state proves a brightness change or a key press; don't track it as pending
        exp = {"target": cmd.confirms_to, "label": cmd.label, "who": who, "run": run, "at": time.time()}
        self._expect[entity_id] = exp
        asyncio.create_task(self._confirm_timeout(entity_id, exp))

    async def _check_refused(self, entity_id: str, exp: dict[str, Any]) -> None:
        await asyncio.sleep(REFUSAL_SETTLE_S)
        ent = self.hub.get(entity_id)
        if self._expect.get(entity_id) is exp and ent and ent.state != exp["target"]:
            self._expect.pop(entity_id, None)
            await self._command_failed(entity_id, exp)
        else:
            exp["checking"] = False

    async def _confirm_timeout(self, entity_id: str, exp: dict[str, Any]) -> None:
        await asyncio.sleep(COMMAND_CONFIRM_S)
        if self._expect.get(entity_id) is exp:
            self._expect.pop(entity_id, None)
            await self._command_failed(entity_id, exp, timed_out=True)

    async def _command_failed(self, entity_id: str, exp: dict[str, Any], timed_out: bool = False) -> None:
        ent = self.hub.get(entity_id)
        if not ent:
            return
        label, run = exp["label"], exp["run"]
        state = KINDS[ent.kind].state_label(ent.state).lower()
        msg = (f"{ent.name} didn't respond within {COMMAND_CONFIRM_S}s - it's still {state}." if timed_out else
               f"{ent.name} didn't {label.lower()} - it reported back as {state} "
               f"after {time.time() - exp['at']:.0f}s. The command reached the hub but not the lock.")
        await self.hub.update(entity_id, attrs={"commandFailures": int(ent.attrs.get("commandFailures") or 0) + 1})
        if run:
            run["log"].append({"ts": time.time(), "text": msg})
            self.store.save_run(run)
        self.store.log_event("problem", f"Couldn't {label.lower()} {ent.name}", msg, entity_id)
        people = run["recipients"] if run else []
        if people:
            await self.notifier.send(Notification(kind="followup", title=f"Couldn't {label.lower()} {ent.name}",
                                                  body=msg, time_sensitive=True), people=people)

    async def command(self, entity_id: str, command: str, who: str, value: Any = None) -> None:
        """A command from the dashboard / app (not a notification button)."""
        await self._command(None, entity_id, command, who, value)

    async def press(self, run_id: str, button_id: str, device_id: str, who: str | None = None,
                    person_id: str | None = None, background: bool = False) -> dict[str, Any]:
        """A button on a run's alert. From a notification: `device_id` is the phone (its token was
        checked). From a signed-in app or wall tablet: `who` is the account's name (`person_id` its
        person, if any).

        The run, the button and the presser are checked here (ActionError when refused). Device
        buttons (lock, lock all…) then send their commands; with `background` the reply doesn't wait
        for them ({"accepted": true}) - a stalled connection must never keep a phone waiting (iOS
        gives a notification button only seconds). A command that fails is told to the presser."""
        run = self.open_runs.get(run_id)
        closed = run is None
        if closed:
            run = next((r for r in self.store.runs(limit=500) if r["id"] == run_id), None)
            if not run:
                raise ActionError("That notification has expired")
        button = next((b for b in run["buttons"] if b["id"] == button_id), None)
        if not button:
            raise ActionError("That button isn't part of this notification")
        if who is None:
            phone = self.store.phone(device_id)
            if not phone:
                raise ActionError("This phone isn't registered")
            person = self.store.person(phone.person_id) if phone.person_id else None
            who = person["name"] if person else phone.display_name
            person_id = None  # the failure notice goes to this phone itself
        elif button.get("sensitive"):
            raise ActionError("Open the device to do that - it needs Face ID or a PIN")

        if button_id == "open":
            return {"ok": True}
        if button_id == "dismiss":
            if not closed:
                self.open_runs.pop(run_id, None)
                self._cancel_timers(run_id)
                run["status"] = "dismissed"
                # A device already confirmed is still told (not awaited: the phone isn't kept waiting).
                t = asyncio.create_task(self._report(run))
                self._presses.add(t)
                t.add_done_callback(self._presses.discard)
            run["log"].append({"ts": time.time(), "text": f"Dismissed by {who}"})
            self.store.save_run(run)
            return {"ok": True}
        if button_id.startswith("snooze:"):
            mins = int(button_id.split(":", 1)[1] or 60)
            if closed:
                return {"ok": True, "note": "Already resolved"}
            self._cancel_timers(run_id)
            self._schedule(run_id, self._snoozed(run_id, mins))
            run["log"].append({"ts": time.time(), "text": f"Snoozed {mins} min by {who}"})
            self.store.save_run(run)
            return {"ok": True, "snoozedMinutes": mins}

        if ":" in button_id:
            cmd_id, eid = button_id.split(":", 1)
            targets = [eid]
        else:
            cmd_id = button_id[:-4]
            targets = [e for e in run["pending"] if e not in run.get("resolved", {})] or run["pending"]
        targets = [e for e in targets if (ent := self.hub.get(e)) and KINDS[ent.kind].command(cmd_id)]
        if not targets:
            self.store.save_run(run)
            return {"ok": True, "commanded": []}
        presser = {"who": who, "device": device_id or None, "person": person_id}
        job = self._press_commands(run, closed, cmd_id, targets, presser)
        if background:
            t = asyncio.create_task(job)
            self._presses.add(t)
            t.add_done_callback(self._presses.discard)
            return {"ok": True, "accepted": True, "commanding": targets}
        done, failed = await job
        return {"ok": True, "commanded": done, **({"failed": failed} if failed else {})}

    async def _press_commands(self, run: dict[str, Any], closed: bool, cmd_id: str, targets: list[str],
                              presser: dict[str, Any]) -> tuple[list[str], list[str]]:
        """Send a pressed button's command to each device (together); tell the presser about any that fail."""
        async def one(eid: str) -> str | None:
            try:
                await asyncio.wait_for(self._command(None if closed else run, eid, cmd_id, presser["who"]),
                                       PRESS_COMMAND_S)
                return None
            except ActionError as e:
                return str(e)
            except asyncio.TimeoutError:
                return f"its connection didn't take the command within {PRESS_COMMAND_S:g}s"
            except Exception as e:  # never lose a failure silently
                log.exception("Pressed %s on %s crashed", cmd_id, eid)
                return f"something went wrong ({type(e).__name__})"

        reasons = await asyncio.gather(*(one(e) for e in targets))
        done = [e for e, why in zip(targets, reasons) if why is None]
        failed = [e for e, why in zip(targets, reasons) if why is not None]
        for eid, why in zip(targets, reasons):
            if why is not None:
                await self._press_failed(run, eid, cmd_id, why, presser)
        self.store.save_run(run)
        if done:
            log.info("Button %s by %s: sent to %s", cmd_id, presser["who"], join_names([self.hub.name(e) for e in done]))
        return done, failed

    async def _press_failed(self, run: dict[str, Any], eid: str, cmd_id: str, reason: str,
                            presser: dict[str, Any]) -> None:
        ent = self.hub.get(eid)
        cmd = KINDS[ent.kind].command(cmd_id) if ent else None
        verb = cmd.label.lower() if cmd else cmd_id.replace("_", " ")
        name = self.hub.name(eid)
        text = f"{name} didn't {verb}: {reason.rstrip('.')}"
        log.warning("Button %s by %s: %s", cmd_id, presser["who"], text)
        run["log"].append({"ts": time.time(), "text": f"{presser['who']} pressed {verb}, but {text}"})
        self.store.log_event("problem", f"Couldn't {verb} {name}", f"{reason} (pressed by {presser['who']})", eid)
        n = Notification(kind="followup", title=f"Couldn't {verb} {name}", body=text, time_sensitive=True,
                         thread=run.get("automationId"), data={"run_id": run["id"]})
        if presser["device"]:
            await self.notifier.send(n, device_ids=[presser["device"]])
        elif presser["person"]:
            await self.notifier.send(n, people=[presser["person"]])
        else:  # pressed on a wall tablet (no phone, no person): the run's people hear about it instead
            await self.notifier.send(n, people=run.get("recipients") or [])
