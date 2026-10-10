"""Live statuses: an iPhone Live Activity / an Android ongoing notification showing devices while they
matter ("Garage Door - Open - 12 min", with a Close button).

Two things start one:
  - a device's own setting "Live status while…" (any device whose kind has states): while it is in one
    of the chosen states, its live status shows to the chosen people (everyone by default); when it
    leaves them, it ends. Kept in live_settings; edited on the device's page (PUT /api/entities/{id}/live-status).
  - an automation's "Show a live status" (describe.clean_live): its devices, shown until they leave a
    state, for a while, or until something else happens.

What each person sees is worked out here (`view`): their live statuses newest first; more than
MAX_SHOWN and the newest MAX_SHOWN - 1 stay their own, the rest share one "N more" status. Every
change re-works everyone's view and sends only what changed:
  - iPhones: ActivityKit pushes (push/live.py) - start with the push-to-start token, update / end with
    each activity's own token. Apple ends an activity after 8 hours: one still needed after
    RESTART_AFTER_S is ended and started afresh (its key carries a generation). Ended ones stay on the
    Lock Screen for END_SHOWN_S with their final state ("Garage Door closed"); ones that only moved (into
    "N more", or restarted) go at once.
  - Android and the web: the person's open live sockets get {"type": "live", "activities", "ended"};
    Android phones also an FCM data message per change when FCM is set up; test phones' copies go to
    the notifier's outbox.
  - GET /api/live/current: the same list, for an app that just opened.

Nothing here knows a kind of device by its id: states, labels and buttons come from kinds.py.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from aiohttp import web

from . import widgets as W
from .automations import describe as d
from .kinds import KINDS, Kind, join_names
from .push import live as P
from .push.apns import PushError
from .state import Entity

if TYPE_CHECKING:
    from .app import Newman

log = logging.getLogger(__name__)

MAX_SHOWN = 5                    # Apple: about five at once per app
RESTART_AFTER_S = 7.5 * 3600     # Apple ends an activity after 8 hours
END_SHOWN_S = 120                # an ended one stays this long with its final state
TRIGGER_CAP_S = 12 * 3600        # "until something happens" that never does
TICK_S = 30.0
STARTUP_GRACE_S = 20.0           # after a restart, devices report in before anything is ended
MAX_STATE_BYTES = 3500           # content-state stays well under APNs' 4 KB
ACTIONS_PER_DEVICE = 2
MORE_ROWS = 6
RETRY_AFTER_S = 300.0            # a push that failed (not a dead token) isn't retried before this
MORE = "more"

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_settings (entity_id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS live_statuses (key TEXT PRIMARY KEY, body TEXT NOT NULL);
"""

SendToPerson = Callable[[str, dict[str, Any]], Awaitable[None]]


# -- what a kind offers ---------------------------------------------------------------------------------

def state_choices(k: Kind) -> list[dict[str, str]]:
    """The states "Live status while…" can pick (moments in between - a motor running - aren't)."""
    return [{"id": s, "label": label} for s, label in k.states if s not in k.transitional]


def default_states(k: Kind) -> list[str]:
    """Its active state (unlocked, open, on…), else every state but its "all good" one."""
    ids = [c["id"] for c in state_choices(k)]
    if k.active_state in ids:
        return [k.active_state]
    return [s for s in ids if s != k.secure_state] if k.secure_state in ids else []


def offers(k: Kind | None) -> bool:
    return k is not None and len(state_choices(k)) > 1


class LiveStatuses:
    def __init__(self, hub: Any, store: Any, notifier: Any, *, send_to_person: SendToPerson | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.hub, self.store, self.notifier, self.clock = hub, store, notifier, clock
        self.send_to_person = send_to_person
        self.tokens = P.LiveTokens(store)
        self._db = store._db
        self._db.executescript(SCHEMA)
        self._db.commit()
        self.settings: dict[str, dict[str, Any]] = {
            r[0]: json.loads(r[1]) for r in self._db.execute("SELECT entity_id, body FROM live_settings")}
        self.statuses: dict[str, dict[str, Any]] = {
            r[0]: json.loads(r[1]) for r in self._db.execute("SELECT key, body FROM live_statuses")}
        self._last: dict[str, dict[str, dict[str, Any]]] = {}   # person -> {activity key: entry} last told
        self._ended_final: dict[str, dict[str, Any]] = {}        # slot -> its final content (just ended)
        self._more_since: dict[str, float] = {}
        self._failed: dict[tuple[str, str], float] = {}
        self._was_on: dict[str, bool] = {}                       # device -> last settled "in its states"
        self._minute = ""
        sent = self.tokens.all_sent()
        for did, key, slot in sent:
            if slot == MORE and (pid := self._person_of_device(did)):
                try:
                    self._more_since[pid] = float(key.split(".")[1])
                except (IndexError, ValueError):
                    pass
        # Just restarted with things showing: let devices report in before anything is ended.
        self.ready_at = clock() + (STARTUP_GRACE_S if (self.statuses or sent) else 0.0)
        self._lock = asyncio.Lock()
        self._soon: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task] = set()
        self._ticker: asyncio.Task | None = None
        self.pushes: list[tuple[str, str, str]] = []  # (device id, event, activity key) sent, newest last (tests)
        hub.subscribe_changes(self._on_change)
        hub.subscribe_events(self._on_event)
        hub.subscribe_registry(self._on_added, self._on_removed)

    # -- lifecycle ---------------------------------------------------------------------------------------

    async def start(self, _app: Any = None) -> None:
        if self._ticker is None:
            self._ticker = asyncio.get_running_loop().create_task(self._tick_loop())
        self.schedule()

    async def close(self, _app: Any = None) -> None:
        if self._soon:
            self._soon.cancel()
        for t in [self._ticker, *self._tasks]:
            if t:
                t.cancel()

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                self.tick()
            except Exception:
                log.exception("Live status tick failed")

    def tick(self, now: float | None = None) -> None:
        """Ends what ran out (a duration, a time that ends one, the cap) and keeps restarts on time."""
        now = self.clock() if now is None else now
        minute = datetime.fromtimestamp(now)
        hhmm, stamp = minute.strftime("%H:%M"), minute.strftime("%Y-%m-%dT%H:%M")
        new_minute = stamp != self._minute
        self._minute = stamp
        for key, s in list(self.statuses.items()):
            if s.get("endsAt") and now >= s["endsAt"]:
                self._end(key)
            elif new_minute and s.get("until", {}).get("type") == "trigger":
                item = s["until"]["item"]
                if item["type"] == "time" and d.time_item_hit({"trigger": {"items": [item]}}, hhmm, minute.weekday(),
                                                              minute.date()):
                    self._end(key)
        if self.statuses or any(self._last.values()):
            self.schedule()

    def schedule(self, delay: float = 0.05) -> None:
        """Re-work everyone's view soon (changes in a burst go together)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._soon is not None:
            return
        delay = max(delay, self.ready_at - self.clock())
        self._soon = loop.call_later(delay, self._fire)

    def _fire(self) -> None:
        self._soon = None
        task = asyncio.get_running_loop().create_task(self._safe_reconcile())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _safe_reconcile(self) -> None:
        try:
            await self.reconcile()
        except Exception:
            log.exception("Live status update failed")

    # -- per-device settings -----------------------------------------------------------------------------

    def setting_json(self, e: Entity) -> dict[str, Any] | None:
        """The device's "Live status while…" (None: its kind has nothing to pick)."""
        k = KINDS.get(e.kind)
        if not offers(k):
            return None
        s = self.settings.get(e.id) or {}
        return {"enabled": bool(s.get("enabled")), "states": list(s.get("states") or default_states(k)),
                "recipients": s.get("recipients"), "choices": state_choices(k),
                "showing": f"device:{e.id}" in self.statuses}

    def save_setting(self, eid: str, data: dict[str, Any]) -> dict[str, Any]:
        """Change a device's setting with any of {enabled, states, recipients}; ValueError says why not."""
        e = self.hub.get(eid)
        if not e:
            raise ValueError("Unknown device")
        k = KINDS[e.kind]
        if not offers(k):
            raise ValueError(f"{e.name} has no states to show a live status for")
        cur = self.setting_json(e) or {}
        enabled = data.get("enabled", cur["enabled"])
        if not isinstance(enabled, bool):
            raise ValueError("enabled is true or false")
        states = data.get("states", cur["states"])
        ids = [c["id"] for c in state_choices(k)]
        if not isinstance(states, list) or any(s not in ids for s in states):
            raise ValueError(f"Pick states {e.name} can be in: " + ", ".join(c["label"] for c in state_choices(k)))
        states = [s for s in ids if s in states]  # the kind's order
        if enabled and not states:
            raise ValueError("Pick at least one state")
        if enabled and len(states) == len(ids):
            raise ValueError("Leave out at least one state - otherwise it never ends")
        recipients = data.get("recipients", cur["recipients"])
        if recipients is not None:
            known = {p["id"] for p in self.store.people()}
            if not isinstance(recipients, list) or any(r not in known for r in recipients):
                raise ValueError("Pick people from the household")
            if not recipients:
                raise ValueError("Pick who sees it, or everyone")
            recipients = list(dict.fromkeys(recipients))
        body = {"enabled": enabled, "states": states, "recipients": recipients}
        if not enabled and states == default_states(k) and recipients is None:
            self.settings.pop(eid, None)
            self._exec("DELETE FROM live_settings WHERE entity_id = ?", (eid,))
        else:
            self.settings[eid] = body
            self._exec("INSERT INTO live_settings (entity_id, body) VALUES (?, ?) ON CONFLICT(entity_id) "
                       "DO UPDATE SET body=excluded.body", (eid, json.dumps(body)))
        self._was_on.pop(eid, None)
        self._evaluate_device(e)
        key = f"device:{eid}"
        if key in self.statuses:  # who sees it / which states may have changed
            self.statuses[key].update(recipients=recipients, states=states)
            self._persist(key)
        self.schedule()
        return self.setting_json(e) or {}

    def _evaluate_device(self, e: Entity) -> None:
        key, s = f"device:{e.id}", self.settings.get(e.id)
        if not s or not s.get("enabled"):
            if key in self.statuses:
                self._end(key)
            return
        k = KINDS[e.kind]
        if e.state is None or e.state in k.transitional:
            return  # in between (a door moving): whatever it was still holds
        on = e.state in s["states"]
        if on and key not in self.statuses:
            self._begin(key, {"key": key, "source": "device", "entities": [e.id], "title": "",
                              "recipients": s.get("recipients"), "states": list(s["states"]), "since": self.clock()})
        elif not on and key in self.statuses:
            self._end(key)
        self._was_on[e.id] = on

    # -- automations: "Show a live status" ----------------------------------------------------------------

    async def start_for_automation(self, auto: dict[str, Any], run: dict[str, Any] | None = None) -> str:
        """Start (or keep going, with its clock reset) the automation's live status. Returns a line for the run's log."""
        lv = d.clean_live(auto.get("live"))
        ents = [e for e in (self.hub.get(i) for i in lv["entities"]) if e]
        if not ents:
            return "No live status - its devices are gone"
        u, now, key = lv["until"], self.clock(), f"automation:{auto['id']}"
        ends_at = None
        if u["type"] == "leaves":
            watch = [e for e in (self.hub.get(i) for i in d.live_watch(lv)) if e]
            if not any(d.state_ok(e, u["state"]) for e in watch):
                return f"No live status - it's already over ({d.live_until_text(lv, self.hub)})"
        elif u["type"] == "duration":
            ends_at = now + d.hold_seconds({"for": {"amount": float(u["amount"]), "unit": u["unit"]}})
        else:
            ends_at = now + TRIGGER_CAP_S
        old = self.statuses.get(key)
        self._begin(key, {"key": key, "source": "automation", "automationId": auto["id"], "runId": (run or {}).get("id"),
                          "entities": [e.id for e in ents], "title": lv["title"] or "", "recipients": lv["recipients"],
                          "until": u, "watch": d.live_watch(lv), "endsAt": ends_at,
                          "since": old["since"] if old else now})
        names = join_names(d.people_names(self.store.people(), lv["recipients"])) if lv["recipients"] is not None \
            else "everyone"
        return f"Showing a live status to {names or 'nobody'} {d.live_until_text(lv, self.hub)}"

    def automations_changed(self, autos: dict[str, dict[str, Any]]) -> None:
        """After an automation is saved or deleted: one that no longer shows a live status ends it; one
        still showing it takes its new title and people."""
        for key, s in list(self.statuses.items()):
            if s.get("source") != "automation":
                continue
            a = autos.get(s.get("automationId"))
            lv = d.clean_live(a.get("live")) if a else None
            if not a or not a.get("enabled") or not lv["enabled"]:
                self._end(key)
            elif (s.get("title"), s.get("recipients")) != (lv["title"], lv["recipients"]):
                s.update(title=lv["title"], recipients=lv["recipients"])
                self._persist(key)
                self.schedule()

    # -- device changes ------------------------------------------------------------------------------------

    async def _on_change(self, old: Entity | None, new: Entity) -> None:
        if new.id in self.settings:
            self._evaluate_device(new)
        moved = old is None or old.state != new.state
        settled = new.state is not None and new.state not in KINDS[new.kind].transitional
        for key, s in list(self.statuses.items()):
            u = s.get("until") or {}
            if u.get("type") == "leaves" and new.id in s.get("watch", []) and settled:
                watch = [e for e in (self.hub.get(i) for i in s["watch"]) if e]
                if not any(d.state_ok(e, u["state"]) for e in watch):
                    self._end(key)
            elif u.get("type") == "trigger":
                item = u["item"]
                if item["type"] == "state" and new.id in item["entities"] and moved and settled \
                        and d.state_ok(new, item["to"]):
                    self._end(key)
                elif item["type"] == "property" and old is not None and \
                        d.property_item_hit({"trigger": {"items": [item]}}, old, new):
                    self._end(key)
        if any(new.id in s["entities"] for s in self.statuses.values()):
            self.schedule()

    async def _on_event(self, ent: Entity, event: str, data: dict[str, Any]) -> None:
        for key, s in list(self.statuses.items()):
            item = (s.get("until") or {}).get("item") or {}
            if item.get("type") == "event" and item.get("entity") == ent.id and item.get("event") == event:
                self._end(key)

    async def _on_added(self, ent: Entity) -> None:
        if ent.id in self.settings:
            self._evaluate_device(ent)

    async def _on_removed(self, ids: list[str]) -> None:
        gone = set(ids)
        for eid in gone & set(self.settings):
            self.settings.pop(eid, None)
            self._exec("DELETE FROM live_settings WHERE entity_id = ?", (eid,))
        for key, s in list(self.statuses.items()):
            left = [e for e in s["entities"] if e not in gone]
            if not left:
                self._end(key)
            elif left != s["entities"]:
                s["entities"] = left
                self._persist(key)
        self.schedule()

    def replace_ids(self, mapping: dict[str, str]) -> int:
        """Swapping devices (replace.py): a setting moves to the new device (unless it has its own)."""
        n = 0
        for old, new in mapping.items():
            if old in self.settings and new not in self.settings:
                self.settings[new] = self.settings.pop(old)
                self._exec("UPDATE live_settings SET entity_id = ? WHERE entity_id = ?", (new, old))
                n += 1
                if f"device:{old}" in self.statuses:
                    self._end(f"device:{old}")
                if ent := self.hub.get(new):
                    self._evaluate_device(ent)
        for key, s in list(self.statuses.items()):
            if s.get("source") == "automation" and any(e in mapping for e in s["entities"] + s.get("watch", [])):
                s["entities"] = [mapping.get(e, e) for e in s["entities"]]
                s["watch"] = [mapping.get(e, e) for e in s.get("watch", [])]
                self._persist(key)
        self.schedule()
        return n

    def forget_person(self, pid: str) -> None:
        for eid, s in list(self.settings.items()):
            if isinstance(s.get("recipients"), list) and pid in s["recipients"]:
                s["recipients"] = [x for x in s["recipients"] if x != pid]
                self._exec("UPDATE live_settings SET body = ? WHERE entity_id = ?", (json.dumps(s), eid))
        for key, s in self.statuses.items():
            if isinstance(s.get("recipients"), list) and pid in s["recipients"]:
                s["recipients"] = [x for x in s["recipients"] if x != pid]
                self._persist(key)
        self._last.pop(pid, None)
        self.schedule()

    # -- statuses ------------------------------------------------------------------------------------------

    def _exec(self, sql: str, args: tuple = ()) -> None:
        self._db.execute(sql, args)
        self._db.commit()

    def _persist(self, key: str) -> None:
        self._exec("INSERT INTO live_statuses (key, body) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET body=excluded.body",
                   (key, json.dumps(self.statuses[key])))

    def _begin(self, key: str, body: dict[str, Any]) -> None:
        self.statuses[key] = body
        self._ended_final.pop(key, None)
        self._persist(key)
        self.schedule()

    def _end(self, key: str) -> None:
        if self.statuses.pop(key, None) is not None:
            self._exec("DELETE FROM live_statuses WHERE key = ?", (key,))
            self.schedule()

    # -- what each person sees -------------------------------------------------------------------------------

    def _device_active(self, e: Entity, s: dict[str, Any]) -> bool:
        if s.get("source") == "device":
            k = KINDS[e.kind]
            return e.state in s.get("states", []) or (e.state in k.transitional and self._was_on.get(e.id, True))
        u = s.get("until") or {}
        if u.get("type") == "leaves" and e.id in s.get("watch", []):
            return d.state_ok(e, u["state"])
        return e.is_active

    @staticmethod
    def _actions(e: Entity) -> list[dict[str, Any]]:
        """What would change it from here (its kind's one-tap commands), what a tap does first."""
        if not e.available:
            return []
        k = KINDS[e.kind]
        out = [a for a in W.actions_json(e, k)
               if (c := k.command(a["command"])) and c.confirms_to and c.confirms_to != e.state]
        return [{"command": a["command"], "label": a["label"], "sensitive": a["sensitive"]}
                for a in out[:ACTIONS_PER_DEVICE]]

    def _device_json(self, e: Entity, s: dict[str, Any], *, final: bool = False) -> dict[str, Any]:
        k = KINDS[e.kind]
        label = "Offline" if not e.available else k.label_for(e.state, e.attrs)
        return {"id": e.id, "name": e.name[:48], "icon": W.icon_of(e), "stateLabel": label,
                "active": False if final else self._device_active(e, s),
                "actions": [] if final else self._actions(e)}

    @staticmethod
    def _subtitle(devs: list[dict[str, Any]]) -> str:
        labels = list(dict.fromkeys(x["stateLabel"] for x in devs if x["active"])) or \
            list(dict.fromkeys(x["stateLabel"] for x in devs))
        return ", ".join(labels)

    def _title(self, s: dict[str, Any], ents: list[Entity]) -> str:
        return (s.get("title") or join_names([e.name for e in ents]) or "Newman")[:64]

    def content(self, s: dict[str, Any]) -> dict[str, Any] | None:
        """One live status's content-state (None while none of its devices is known)."""
        ents = [e for e in (self.hub.get(i) for i in s["entities"]) if e]
        if not ents:
            return None
        devs = [self._device_json(e, s) for e in ents]
        return fit({"title": self._title(s, ents), "subtitle": self._subtitle(devs), "since": int(s["since"]),
                    "active": True, "devices": devs, "more": 0})

    def _more_content(self, rest: list[tuple[dict[str, Any], dict[str, Any]]], since: float) -> dict[str, Any]:
        rows = []
        for s, c in rest[:MORE_ROWS]:
            first = c["devices"][0] if c["devices"] else {"id": s["key"], "icon": "", "stateLabel": "", "active": True}
            rows.append({"id": first["id"], "name": c["title"][:48], "icon": first["icon"],
                         "stateLabel": c["subtitle"] or first["stateLabel"], "active": c["active"], "actions": []})
        return fit({"title": f"{len(rest)} more", "subtitle": join_names([c["title"] for _, c in rest])[:120],
                    "since": int(since), "active": True, "devices": rows,
                    # "more" counts only what isn't listed: devices + more = every grouped status.
                    "more": len(rest) - len(rows)})

    def _activity_key(self, slot: str, since: float, now: float) -> str:
        gen = int(max(0.0, now - since) // RESTART_AFTER_S)
        return f"{slot}.{int(since)}" + (f".{gen}" if gen else "")

    def _for_person(self, pid: str) -> list[dict[str, Any]]:
        mine = [s for s in self.statuses.values() if s.get("recipients") is None or pid in s["recipients"]]
        return sorted(mine, key=lambda s: (-s["since"], s["key"]))

    def view(self, pid: str, now: float | None = None) -> list[dict[str, Any]]:
        """What this person's phones show: [{"key", "slot", "title", "contentState"}], newest first, at
        most MAX_SHOWN (the oldest grouped into one "N more" when there are more)."""
        now = self.clock() if now is None else now
        shown = [(s, c) for s in self._for_person(pid) if (c := self.content(s)) is not None]
        out = []
        if len(shown) > MAX_SHOWN:
            keep, rest = shown[:MAX_SHOWN - 1], shown[MAX_SHOWN - 1:]
            since = self._more_since.setdefault(pid, now)
        else:
            keep, rest = shown, []
            self._more_since.pop(pid, None)
        for s, c in keep:
            out.append({"key": self._activity_key(s["key"], s["since"], now), "slot": s["key"], "title": c["title"],
                        "contentState": c})
        if rest:
            c = self._more_content(rest, since)
            out.append({"key": self._activity_key(MORE, since, now), "slot": MORE, "title": c["title"], "contentState": c})
        return out

    def _final(self, slot: str, last: dict[str, Any] | None, reason: str) -> dict[str, Any]:
        """An ended activity's last content: its devices as they are now, nothing to press. Really over
        (not just moved) and about one device: "Garage Door closed"."""
        c = copy.deepcopy(last) if last else {"title": "Newman", "subtitle": "", "since": int(self.clock()),
                                               "active": False, "devices": [], "more": 0}
        c["active"] = False
        devs = []
        for x in c.get("devices") or []:
            e = self.hub.get(x.get("id", ""))
            if e and slot != MORE:
                x = self._device_json(e, {}, final=True) | {"name": x.get("name", e.name)}
            devs.append({**x, "active": False, "actions": []})
        c["devices"] = devs
        if reason == "ended" and slot != MORE and devs:
            c["subtitle"] = ", ".join(dict.fromkeys(x["stateLabel"] for x in devs))
            if len(devs) == 1 and devs[0]["stateLabel"]:
                c["title"] = f"{c['title']} {devs[0]['stateLabel'].lower()}"[:64]
        return fit(c)

    def _reason(self, slot: str, now_slots: set[str]) -> str:
        """Why an activity went: "moved" (restarted, into "N more" or out of it - shown on) or "ended"."""
        if slot == MORE or slot in now_slots or slot in self.statuses:
            return "moved"
        return "ended"

    # -- telling the phones --------------------------------------------------------------------------------

    def _person_of_device(self, device_id: str, user_id: str | None = None) -> str | None:
        ph = self.store.phone(device_id)
        if ph and ph.person_id:
            return ph.person_id
        u = self.store.user(user_id) if user_id else None
        return (u or {}).get("person_id")

    async def reconcile(self) -> None:
        async with self._lock:
            now = self.clock()
            if now < self.ready_at:
                self.schedule()
                return
            views = {p["id"]: {e["key"]: e for e in self.view(p["id"], now)} for p in self.store.people()}
            await self._push_iphones(views, now)
            await self._push_people(views, now)

    async def _push_people(self, views: dict[str, dict[str, dict[str, Any]]], now: float) -> None:
        for pid, cur in views.items():
            prev = self._last.get(pid, {})
            if {k: e["contentState"] for k, e in prev.items()} == {k: e["contentState"] for k, e in cur.items()}:
                continue
            slots = {e["slot"] for e in cur.values()}
            ended = []
            for k, e in prev.items():
                if k in cur:
                    continue
                reason = self._reason(e["slot"], slots)
                ended.append({"key": k, "slot": e["slot"], "title": e["title"], "reason": reason,
                              "contentState": self._final(e["slot"], e["contentState"], reason),
                              "dismissAt": int(now + (END_SHOWN_S if reason == "ended" else 0))})
            changes = [("start", e) for k, e in cur.items() if k not in prev] + \
                [("update", e) for k, e in cur.items() if k in prev and prev[k]["contentState"] != e["contentState"]] + \
                [("end", e) for e in ended]
            self._last[pid] = cur
            if self.send_to_person:
                try:
                    await self.send_to_person(pid, {"type": "live", "activities": list(cur.values()), "ended": ended})
                except Exception:
                    log.exception("Live status to sockets failed")
            for ph in self.store.phones(pid):
                if ph.platform in ("android", "test"):
                    for event, e in changes:
                        await self._to_android(ph, event, e)

    async def _to_android(self, ph: Any, event: str, e: dict[str, Any]) -> None:
        data = {"type": "live", "event": event, "key": e["key"], "slot": e["slot"], "title": e["title"],
                "state": json.dumps(e["contentState"], separators=(",", ":"))}
        if "dismissAt" in e:
            data["dismissAt"] = str(e["dismissAt"])
        if ph.platform == "test":
            self.notifier.outbox.appendleft({"ts": time.time(), "deviceId": ph.device_id, "personId": ph.person_id,
                                             "kind": "live", **data})
            self.pushes.append((ph.device_id, event, e["key"]))
            return
        fcm = self.notifier._fcm
        if not fcm.enabled:
            return  # no FCM on this hub: the app follows its live connection and GET /api/live/current
        try:
            await fcm.send(ph.token, data, collapse_key=None, ttl_s=3600)
            self.pushes.append((ph.device_id, event, e["key"]))
        except PushError as err:
            log.warning("Live status push -> %s failed: %s", ph.display_name, err)
            self.store.record_push_failure(ph.device_id, err.dead_token)
        except Exception:
            log.exception("Live status push -> %s crashed", ph.display_name)

    async def _apns(self, device_id: str, t: P.LiveToken, event: str, key: str, payload: dict[str, Any]) -> bool:
        if self._failed.get((device_id, key), 0) > self.clock():
            return False
        try:
            await P.send(self.notifier._apns, t, payload)
            self.pushes.append((device_id, event, key))
            self._failed.pop((device_id, key), None)
            return True
        except PushError as err:
            log.warning("Live Activity %s -> %s failed: %s", event, device_id, err)
            if err.dead_token:
                self.tokens.forget(device_id, t.kind, t.activity_key)
            else:
                self._failed[(device_id, key)] = self.clock() + RETRY_AFTER_S
        except Exception:
            log.exception("Live Activity %s -> %s crashed", event, device_id)
        return False

    async def _push_iphones(self, views: dict[str, dict[str, dict[str, Any]]], now: float) -> None:
        apns = self.notifier._apns
        if not getattr(apns, "enabled", False):
            return
        phones: dict[str, dict[str, Any]] = {}
        for t in self.tokens.live():
            p = phones.setdefault(t.device_id, {"start": None, "update": {}, "user": t.user_id})
            if t.kind == "push-to-start":
                p["start"] = t
            else:
                p["update"][t.activity_key] = t
        for did, p in phones.items():
            pid = self._person_of_device(did, p["user"])
            desired = views.get(pid, {}) if pid else {}
            slots = {e["slot"] for e in desired.values()}
            sent = self.tokens.sent(did)
            # Ends first: they make room on the phone for what starts.
            for key, row in sent.items():
                if key in desired:
                    continue
                reason = self._reason(row["slot"], slots)
                if t := p["update"].get(key):
                    final = self._final(row["slot"], row["content"], reason)
                    dismiss = now + (END_SHOWN_S if reason == "ended" else 0)
                    await self._apns(did, t, "end", key, P.end_payload(final, now, dismiss))
                    self.tokens.forget(did, "update", key)
                self.tokens.drop_sent(did, key)
            for key, t in p["update"].items():
                if key not in desired and key not in sent:  # running on the phone, not wanted (any more)
                    last = next((e for e in self._last.get(pid or "", {}).values() if e["key"] == key), None)
                    slot = key.rsplit(".", 1)[0]
                    await self._apns(did, t, "end", key,
                                     P.end_payload(self._final(slot, last and last["contentState"], "moved"), now, now))
                    self.tokens.forget(did, "update", key)
            for key, e in desired.items():
                row = sent.get(key)
                c = e["contentState"]
                if row is None:
                    if key in p["update"]:  # the app started it itself (GET /api/live/current)
                        self.tokens.save_sent(did, key, e["slot"], None)
                        row = {"content": None}
                    elif p["start"] is not None:
                        alert = {"title": e["title"], "body": c.get("subtitle") or ""}
                        if await self._apns(did, p["start"], "start", key, P.start_payload(key, e["title"], c, alert, now)):
                            self.tokens.save_sent(did, key, e["slot"], c)
                        continue
                    else:
                        continue  # never opened since: it asks GET /api/live/current when it does
                if (t := p["update"].get(key)) and row["content"] != c:
                    if await self._apns(did, t, "update", key, P.update_payload(c, now)):
                        self.tokens.save_sent(did, key, e["slot"], c)

    # -- the API -------------------------------------------------------------------------------------------

    def current(self, pid: str | None) -> dict[str, Any]:
        acts = [{**e, "attributes": {"key": e["key"], "title": e["title"]}} for e in self.view(pid)] if pid else []
        return {"attributesType": P.ATTRIBUTES_TYPE, "max": MAX_SHOWN, "activities": acts}


def fit(c: dict[str, Any]) -> dict[str, Any]:
    """Keep a content-state small: rows beyond what fits are counted in "more" instead."""
    while len(json.dumps(c, separators=(",", ":")).encode()) > MAX_STATE_BYTES and len(c["devices"]) > 1:
        c["devices"].pop()
        c["more"] = c.get("more", 0) + 1
    return c


def add_live_routes(r: web.RouteTableDef, h: "Newman", *, live: LiveStatuses, body: Callable[[web.Request], Any]) -> None:
    @r.get("/api/live/current")
    async def live_current(req: web.Request) -> web.Response:
        """The signed-in person's live statuses now (?deviceId= names this phone: its person, if the phone
        is someone else's)."""
        did = req.query.get("deviceId") or ""
        ph = h.store.phone(did) if did else None
        pid = (ph.person_id if ph and ph.person_id else None) or req["user"].get("person_id")
        return web.json_response(live.current(pid))

    @r.post("/api/live/tokens")
    async def live_token(req: web.Request) -> web.Response:
        """{kind: "push-to-start", token, deviceId, apnsEnvironment?} or {kind: "update", activityKey, token,
        deviceId}: an iPhone's ActivityKit tokens."""
        try:
            live.tokens.register(req["user"], req["session"], await body(req))
        except ValueError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        live.schedule()
        return web.json_response({"ok": True})

    @r.delete("/api/live/tokens")
    async def live_token_forget(req: web.Request) -> web.Response:
        """{kind, deviceId, activityKey?} (or the same as query parameters): forget that token. An
        activity's token goes when it ends on the phone (dismissed there: not started again)."""
        data = dict(req.query)
        data.update(await body(req))
        kind, did = data.get("kind"), str(data.get("deviceId") or "")
        if kind not in P.KINDS or not did:
            return web.json_response({"ok": False, "error": 'kind ("push-to-start" | "update") and deviceId'}, status=400)
        key = str(data.get("activityKey") or "") if kind == "update" else ""
        mine = {t.device_id for t in live.tokens.live() if t.user_id == req["user"]["id"]}
        if did not in mine and req["user"]["role"] != "admin":
            return web.json_response({"ok": False, "error": "That isn't your phone"}, status=403)
        n = live.tokens.forget(did, kind, key)
        return web.json_response({"ok": True, "forgotten": n})
