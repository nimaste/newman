"""The phone's Home screen (GET /api/home): what needs you, six category cards, favourites, the big camera
and the Climate screen's rooms - worked out here so both phone apps say exactly the same thing.

Settings "home": {"accounts": {account id: {"cards": [{"id", "on"}…], "favourites": [ids]}},
"climateRooms": [{"entity", "name"}]} - each person's Edit Home, and the household's Climate rooms.
Everything about devices comes from kinds metadata (category, secure_state, urgent, is_mode, has_camera,
properties), never lists of kinds.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable

from .kinds import KINDS
from .state import Entity

SETTING = "home"
CARDS = ["cameras", "climate", "lights", "security", "today", "people", "appliances", "cleaning"]
CARD_TITLES = {"cameras": "Cameras", "climate": "Climate", "lights": "Lights", "security": "Security",
               "today": "Today", "people": "People", "appliances": "Appliances", "cleaning": "Cleaning"}
OFF_BY_DEFAULT = {"appliances", "cleaning"}
MAX_FAVOURITES = 4
# A door, lock or garage door left away from its secure state this long counts as needing you now.


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%-I:%M %p")


def _prop_attr(e: Entity, pid: str) -> str | None:
    return next((p.attr for p in KINDS[e.kind].properties if p.id == pid), None)


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _deg(v: float) -> str:
    return f"{v:.1f}°"


def is_camera(e: Entity) -> bool:
    """A camera itself: its kind has a camera and it isn't an area of one (a zone names its camera)."""
    return KINDS[e.kind].has_camera and "camera" not in e.attrs


def is_light_row(e: Entity, groups: Any) -> bool:
    """Counted as a light on Home: a group, or a device whose kind reports a brightness - never a group's
    member (the group counts once)."""
    if e.integration == "groups":
        return True
    if groups.member_group(e.id):
        return False
    return KINDS[e.kind].category == "lighting" and any(p.attr == "brightness" for p in KINDS[e.kind].properties)


class Home:
    def __init__(self, hub: Any, store: Any, groups: Any, house: Any, muting: Any,
                 today: Callable[[dict[str, Any]], dict[str, Any]], people: Callable[[], list[dict[str, Any]]]) -> None:
        self.hub, self.store, self.groups, self.house, self.muting = hub, store, groups, house, muting
        self.today, self.people = today, people
        self._since: dict[str, float] = {}  # entity id -> when its state last changed (seen here)

    async def on_state(self, old: Entity | None, new: Entity) -> None:
        if old is None or old.state != new.state:
            self._since[new.id] = time.time()

    def _changed_at(self, e: Entity) -> float:
        return self._since.get(e.id, e.updated_at)

    # -- saved choices --------------------------------------------------------------------------------

    def _saved(self) -> dict[str, Any]:
        v = self.store.settings().get(SETTING)
        return v if isinstance(v, dict) else {}

    def _save(self, v: dict[str, Any]) -> None:
        self.store.set_setting(SETTING, v)

    def set_mine(self, account_id: str, data: Any) -> None:
        if not isinstance(data, dict):
            raise ValueError("Send the cards and favourites")
        mine: dict[str, Any] = {}
        if "cards" in data:
            cards = data["cards"]
            if not isinstance(cards, list) or not all(isinstance(c, dict) and c.get("id") in CARDS
                                                      and isinstance(c.get("on"), bool) for c in cards):
                raise ValueError("Cards: each {\"id\", \"on\"} - " + ", ".join(CARDS))
            mine["cards"] = [{"id": c["id"], "on": c["on"]} for c in {c["id"]: c for c in cards}.values()]
        if "favourites" in data:
            fav = data["favourites"]
            if not isinstance(fav, list) or not all(isinstance(f, str) for f in fav):
                raise ValueError("Favourites: a list of devices")
            fav = list(dict.fromkeys(fav))
            if len(fav) > MAX_FAVOURITES:
                raise ValueError(f"Up to {MAX_FAVOURITES} favourites")
            if bad := [f for f in fav if not self.hub.get(f)]:
                raise ValueError("Unknown device: " + ", ".join(bad))
            mine["favourites"] = fav
        v = self._saved()
        accounts = v.setdefault("accounts", {})
        accounts[account_id] = {**(accounts.get(account_id) or {}), **mine}
        self._save(v)

    def set_climate(self, data: Any) -> None:
        rooms = data.get("rooms") if isinstance(data, dict) else None
        if not isinstance(rooms, list) or not all(isinstance(r, dict) and isinstance(r.get("entity"), str) for r in rooms):
            raise ValueError("Rooms: a list of {\"entity\", \"name\"}")
        out = []
        for r in rooms:
            e = self.hub.get(r["entity"])
            if not e or not self._is_temperature(e):
                raise ValueError(f"{r['entity']} isn't a temperature sensor")
            name = str(r.get("name") or "").strip()[:48] or e.name
            out.append({"entity": e.id, "name": name})
        v = self._saved()
        v["climateRooms"] = out
        self._save(v)

    # -- the cards -------------------------------------------------------------------------------------

    def _is_temperature(self, e: Entity) -> bool:
        k = KINDS[e.kind]
        return k.category == "climate" and bool(k.unit) and _num(e.state) is not None

    def _thermostat(self) -> Entity | None:
        return next((e for e in self.hub.all() if KINDS[e.kind].category == "climate"
                     and _prop_attr(e, "target")), None)

    def _latest_alert(self, camera: str | None) -> dict[str, Any] | None:
        for ev in self.store.events(100, kind="alert"):
            eid = ev.get("entityId")
            e = self.hub.get(eid or "")
            if not e or not is_camera(e) or (camera and eid != camera):
                continue
            data = ev.get("data") or {}
            clip = data.get("clip")
            iid, _, rest = str(clip or "").partition(":")
            return {"camera": eid, "label": str(ev.get("title") or "").split(" at ")[0] or "Activity",
                    "zone": ev.get("body") or None, "at": ev["ts"],
                    "snapshot": data.get("snapshotUrl") or (f"api/snapshots/{data['snapshotId']}"
                                                            if data.get("snapshotId") else None),
                    "clip": f"api/clips/{iid}/{rest}" if clip and rest else None}
        return None

    def _cards(self, ents: list[Entity], today: dict[str, Any]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        cams = [e for e in ents if is_camera(e)]
        if cams:
            s = self.house.get()["cameras"]
            top = self._latest_alert(s["featured"] if s["mode"] == "featured" else None)
            camera = s["featured"] if s["mode"] == "featured" and s["featured"] else (top or {}).get("camera") or cams[0].id
            if top:
                value, detail = f"{top['label']} · {_clock(top['at'])}", self.hub.name(top["camera"])
            else:
                value, detail = f"{len(cams)} camera{'s' if len(cams) != 1 else ''}", "Nothing seen lately"
            out["cameras"] = {"value": value, "detail": detail, "tone": "normal", "camera": camera}
        t = self._thermostat()
        temps = [e for e in ents if self._is_temperature(e)]
        if t:
            now = _num(t.attrs.get(_prop_attr(t, "temperature") or ""))
            target = _num(t.attrs.get(_prop_attr(t, "target") or ""))
            mode = KINDS[t.kind].state_label(t.state) if t.state else "Off"
            detail = mode + (f" · set to {target:g}°" if target is not None and t.state not in (None, "off") else "")
            out["climate"] = {"value": _deg(now) if now is not None else mode, "detail": detail,
                              "tone": "warm" if t.state == "heat" else "normal"}
        elif temps:
            out["climate"] = {"value": _deg(_num(temps[0].state) or 0), "detail": temps[0].name, "tone": "normal"}
        rows = [e for e in ents if is_light_row(e, self.groups)]
        if rows:
            on = [e for e in rows if e.state == "on"]
            out["lights"] = {"value": f"{len(on)} on" if on else "All off",
                             "detail": ", ".join(e.name for e in on[:2]) + (f" +{len(on) - 2}" if len(on) > 2 else "")
                             if on else f"{len(rows)} light{'s' if len(rows) != 1 else ''}",
                             "tone": "warm" if on else "normal"}
        sec = [e for e in ents if KINDS[e.kind].secure_state or KINDS[e.kind].is_mode]
        if sec:
            insecure = [e for e in sec if KINDS[e.kind].secure_state and e.available and e.state
                        and e.state != KINDS[e.kind].secure_state]
            alarm = next((e for e in sec if KINDS[e.kind].is_mode), None)
            alarm_text = f"Alarm {KINDS[alarm.kind].state_label(alarm.state).lower()}" if alarm and alarm.state else ""
            if insecure:
                first = insecure[0]
                urgent = any(KINDS[e.kind].urgent for e in insecure)
                out["security"] = {"value": f"{first.name} {KINDS[first.kind].state_label(first.state).lower()}",
                                   "detail": " · ".join(x for x in (f"+{len(insecure) - 1} more" if len(insecure) > 1 else "",
                                                                    alarm_text) if x),
                                   "tone": "danger" if urgent else "warm"}
            else:
                out["security"] = {"value": "All secure", "detail": alarm_text or "Nothing open or unlocked",
                                   "tone": "good"}
        events, todo = today.get("events") or [], today.get("todo") or []
        if events:
            ev = events[0]
            out["today"] = {"value": ev.get("title") or "Busy",
                            "detail": " · ".join(x for x in (ev.get("time") or "",
                                                             f"{len(todo)} to do" if todo else "") if x), "tone": "normal"}
        else:
            out["today"] = {"value": "Nothing today",
                            "detail": f"{len(todo)} to do" if todo else "No events or to-dos", "tone": "normal"}
        people = self.people()
        if people:
            home = [p for p in people if p.get("home")]
            out["people"] = {"value": people[0]["name"] if len(people) == 1 else
                             ("Everyone's home" if len(home) == len(people) else f"{len(home)} home"),
                             "detail": ("Home" if home else "Away") if len(people) == 1 else
                             (", ".join(p["name"] for p in home[:3]) if home else "Nobody's home"),
                             "tone": "good" if home else "normal"}
        for cat in ("appliances", "cleaning"):
            mine = [e for e in ents if KINDS[e.kind].category == cat]
            if mine:
                busy = next((e for e in mine if e.state and e.state not in ("off", "idle", "docked")), mine[0])
                label = KINDS[busy.kind].label_for(busy.state, busy.attrs)
                out[cat] = {"value": f"{len(mine)} {CARD_TITLES[cat].lower() if len(mine) != 1 else busy.name}",
                            "detail": f"{busy.name} {label.lower()}" if len(mine) != 1 else label, "tone": "normal"}
        return out

    # -- what needs you ----------------------------------------------------------------------------------

    def _status(self, ents: list[Entity]) -> dict[str, Any]:
        now = time.time()
        house_open = self.house.get()["devices"]["leftOpenMinutes"]
        danger, warnings = [], []
        secure_kinds: dict[str, list[Entity]] = {}
        for e in ents:
            k = KINDS[e.kind]
            if k.is_mode and e.state == "triggered":
                danger.append({"id": f"{e.id}:triggered", "title": f"{e.name} {k.state_label(e.state).lower()}",
                               "detail": f"Since {_clock(self._changed_at(e))}", "entity": e.id, "action": None})
            if not k.secure_state:
                continue
            secure_kinds.setdefault(e.kind, []).append(e)
            if not e.available or not e.state or e.state in (k.secure_state, *k.transitional):
                continue
            since = self._changed_at(e)
            after = self.muting.warnings.left_open(e.id) or house_open  # minutes; urgent kinds at once
            if k.urgent or now - since >= after * 60:
                mins = max(1, int((now - since) // 60))
                fix = next((c for c in k.commands if c.confirms_to == k.secure_state), None)
                danger.append({"id": f"{e.id}:{e.state}", "title": f"{e.name} {k.state_label(e.state).lower()}",
                               "detail": f"For {mins} minute{'s' if mins != 1 else ''} · since {_clock(since)}",
                               "entity": e.id, "action": {"command": fix.id, "label": fix.label} if fix else None})
        for e in ents:
            for item in self.muting.health(e)["items"]:
                if item["muted"] is None:
                    warnings.append({"entity": e.id, "issue": item["id"], "text": f"{e.name} · {item['text']}"})
        insecure = [e for es in secure_kinds.values() for e in es
                    if e.available and e.state and e.state != KINDS[e.kind].secure_state]
        if insecure:
            summary = " · ".join(f"{e.name} {KINDS[e.kind].state_label(e.state).lower()}" for e in insecure[:3])
        else:
            parts = [f"{KINDS[kid].plural.lower()} {KINDS[kid].state_label(KINDS[kid].secure_state).lower()}"
                     for kid in secure_kinds]
            summary = " · ".join(parts[:3])
            summary = summary[:1].upper() + summary[1:] if summary else "All good at home"
        return {"level": "danger" if danger else "warn" if warnings else "ok", "summary": summary,
                "danger": danger, "warnings": warnings}

    # -- all of it ------------------------------------------------------------------------------------------

    def json(self, account_id: str, can_automate: bool, today: dict[str, Any] | None = None) -> dict[str, Any]:
        ents = self.hub.all()
        today = today if today is not None else self.today({})
        cards = self._cards(ents, today)
        mine = (self._saved().get("accounts") or {}).get(account_id) or {}
        order = [c for c in (mine.get("cards") or []) if c["id"] in cards]
        seen = {c["id"] for c in order}
        order += [{"id": cid, "on": cid not in OFF_BY_DEFAULT} for cid in CARDS if cid in cards and cid not in seen]
        house = self.house.get()
        cam = house["cameras"]
        top = self._latest_alert(cam["featured"] if cam["mode"] == "featured" else None)
        if cam["mode"] == "featured" and cam["featured"] and not top:
            top = {"camera": cam["featured"], "label": None, "zone": None, "at": None, "snapshot": None, "clip": None}
        t = self._thermostat()
        saved_rooms = self._saved().get("climateRooms")
        if isinstance(saved_rooms, list):
            rooms = [{"entity": r["entity"], "name": r.get("name") or self.hub.name(r["entity"]),
                      "value": _num(e.state)} for r in saved_rooms if (e := self.hub.get(r["entity"]))]
        else:
            rooms = [{"entity": e.id, "name": e.name, "value": _num(e.state)} for e in ents if self._is_temperature(e)]
        return {
            "cards": [{"id": c["id"], "on": c["on"], "title": CARD_TITLES[c["id"]], **cards[c["id"]]} for c in order],
            "favourites": [f for f in mine.get("favourites") or [] if self.hub.get(f)],
            "status": self._status(ents),
            "cameras": {"mode": cam["mode"], "featured": cam["featured"], "top": top},
            "climate": {"thermostat": t.id if t else None, "rooms": rooms,
                        "range": house["climate"]["range"], "bands": house["climate"]["bands"]},
            "canAutomate": can_automate,
        }
