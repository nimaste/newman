"""The phone's Home screen (GET /api/home): what needs you, a card per category in use (plus Today and
People), favourites, the big camera and the Climate screen's rooms - worked out here so both phone apps say
exactly the same thing.

Settings "home": {"household": {"cards": [{"id", "on"}…], "favourites": [ids]}, "accounts": {account id:
{"cards", "favourites"}}, "canEdit": {account id: false}, "climateRooms": [{"entity", "name"}]} - the
household layout the admin sets, each person's own (Edit Home, when they may), and the Climate rooms.
Cards are every category with a device in it (categories.py: built-in and custom), so a new category or
kind gets one with no change here; what a card says comes from its category's screen role and kinds
metadata (secure_state, urgent, is_mode, has_camera, active_state, properties), never lists of kinds.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable

from .categories import screen_of
from .kinds import CATEGORY, KINDS
from .state import Entity

SETTING = "home"
MAX_FAVOURITES = 4
# The two cards that aren't categories: their place on a new Home (categories have theirs in kinds.CATEGORIES).
EXTRA_CARDS = {"today": ("Today", 5), "people": ("People", 6)}
OLD_IDS = {"lights": "lighting"}  # card ids an earlier Newman saved


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
    return any(p.attr == "brightness" for p in KINDS[e.kind].properties)


def screen(e: Entity) -> str | None:
    """The purpose-built screen of the category it's in (after any move): "climate", "lights"…"""
    return screen_of(e.category)


def _norm(cards: Any) -> list[dict[str, Any]]:
    out, seen = [], set()
    for c in cards if isinstance(cards, list) else []:
        if isinstance(c, dict) and isinstance(c.get("id"), str) and isinstance(c.get("on"), bool):
            cid = OLD_IDS.get(c["id"], c["id"])
            if cid not in seen:
                seen.add(cid)
                out.append({"id": cid, "on": c["on"]})
    return out


class Home:
    def __init__(self, hub: Any, store: Any, groups: Any, house: Any, muting: Any,
                 today: Callable[[dict[str, Any]], dict[str, Any]], people: Callable[[], list[dict[str, Any]]],
                 cats: Any = None) -> None:
        self.hub, self.store, self.groups, self.house, self.muting = hub, store, groups, house, muting
        self.today, self.people = today, people
        if cats is None:
            from .categories import Categories
            cats = Categories(store, hub)
        self.cats = cats
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

    def _card_id_ok(self, cid: Any) -> bool:
        return cid in EXTRA_CARDS or self.cats.known(OLD_IDS.get(cid, cid))

    def _layout(self, data: Any) -> dict[str, Any]:
        """A layout from what a client sent: {"cards": [{"id", "on"}…in order], "favourites": [≤4 ids]}."""
        if not isinstance(data, dict):
            raise ValueError("Send the cards and favourites")
        mine: dict[str, Any] = {}
        if "cards" in data:
            cards = data["cards"]
            if not isinstance(cards, list) or not all(isinstance(c, dict) and self._card_id_ok(c.get("id"))
                                                      and isinstance(c.get("on"), bool) for c in cards):
                raise ValueError("Cards: each {\"id\", \"on\"} - a category, \"today\" or \"people\"")
            mine["cards"] = _norm(cards)
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
        return mine

    def set_mine(self, account_id: str, data: Any) -> None:
        mine = self._layout(data)
        v = self._saved()
        accounts = v.setdefault("accounts", {})
        accounts[account_id] = {**(accounts.get(account_id) or {}), **mine}
        self._mark_seen(v, mine)
        self._save(v)

    def set_household(self, data: Any) -> None:
        lay = self._layout(data)
        v = self._saved()
        v["household"] = {**(v.get("household") or {}), **lay}
        self._mark_seen(v, lay)
        self._save(v)

    def reset(self, account_id: str) -> None:
        v = self._saved()
        (v.get("accounts") or {}).pop(account_id, None)
        self._save(v)

    def has_own(self, account_id: str) -> bool:
        return account_id in (self._saved().get("accounts") or {})

    def can_edit(self, user: dict[str, Any]) -> bool:
        """May change their own Home: admins always, wall tablets never, anyone else unless switched off."""
        if user.get("role") == "admin":
            return True
        if user.get("role") == "tablet":
            return False
        return (self._saved().get("canEdit") or {}).get(user["id"], True) is not False

    def set_can_edit(self, account_id: str, on: bool) -> None:
        v = self._saved()
        ce = v.setdefault("canEdit", {})
        if on:
            ce.pop(account_id, None)
        else:
            ce[account_id] = False
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
        return screen(e) == "climate" and bool(KINDS[e.kind].unit) and _num(e.state) is not None

    def _thermostat(self, ents: list[Entity] | None = None) -> Entity | None:
        return next((e for e in (ents if ents is not None else self.hub.all())
                     if screen(e) == "climate" and _prop_attr(e, "target")), None)

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

    def _camera_card(self, members: list[Entity]) -> dict[str, Any] | None:
        cams = [e for e in members if is_camera(e)]
        if not cams:
            return None
        s = self.house.get()["cameras"]
        top = self._latest_alert(s["featured"] if s["mode"] == "featured" else None)
        camera = s["featured"] if s["mode"] == "featured" and s["featured"] else (top or {}).get("camera") or cams[0].id
        if top:
            value, detail = f"{top['label']} · {_clock(top['at'])}", self.hub.name(top["camera"])
        else:
            value, detail = f"{len(cams)} camera{'s' if len(cams) != 1 else ''}", "Nothing seen lately"
        return {"value": value, "detail": detail, "tone": "normal", "camera": camera}

    def _climate_card(self, members: list[Entity]) -> dict[str, Any] | None:
        t = self._thermostat(members)
        if t:
            now = _num(t.attrs.get(_prop_attr(t, "temperature") or ""))
            target = _num(t.attrs.get(_prop_attr(t, "target") or ""))
            mode = KINDS[t.kind].state_label(t.state) if t.state else "Off"
            detail = mode + (f" · set to {target:g}°" if target is not None and t.state not in (None, "off") else "")
            return {"value": _deg(now) if now is not None else mode, "detail": detail,
                    "tone": "warm" if t.state == "heat" else "normal"}
        temps = [e for e in members if self._is_temperature(e)]
        if temps:
            return {"value": _deg(_num(temps[0].state) or 0), "detail": temps[0].name, "tone": "normal"}
        return None

    def _lights_card(self, members: list[Entity]) -> dict[str, Any] | None:
        rows = [e for e in members if is_light_row(e, self.groups)]
        if not rows:
            return None
        on = [e for e in rows if e.state == "on"]
        return {"value": f"{len(on)} on" if on else "All off",
                "detail": ", ".join(e.name for e in on[:2]) + (f" +{len(on) - 2}" if len(on) > 2 else "")
                if on else f"{len(rows)} light{'s' if len(rows) != 1 else ''}",
                "tone": "warm" if on else "normal"}

    def _security_card(self, members: list[Entity]) -> dict[str, Any] | None:
        sec = [e for e in members if KINDS[e.kind].secure_state or KINDS[e.kind].is_mode]
        if not sec:
            return None
        insecure = [e for e in sec if KINDS[e.kind].secure_state and e.available and e.state
                    and e.state != KINDS[e.kind].secure_state]
        alarm = next((e for e in sec if KINDS[e.kind].is_mode), None)
        alarm_text = f"Alarm {KINDS[alarm.kind].state_label(alarm.state).lower()}" if alarm and alarm.state else ""
        if insecure:
            first = insecure[0]
            urgent = any(KINDS[e.kind].urgent for e in insecure)
            return {"value": f"{first.name} {KINDS[first.kind].state_label(first.state).lower()}",
                    "detail": " · ".join(x for x in (f"+{len(insecure) - 1} more" if len(insecure) > 1 else "",
                                                     alarm_text) if x),
                    "tone": "danger" if urgent else "warm"}
        return {"value": "All secure", "detail": alarm_text or "Nothing open or unlocked", "tone": "good"}

    def _generic_card(self, members: list[Entity]) -> dict[str, Any]:
        """Any category: how many devices, and how many are active or need a look - from kinds metadata."""
        n = len(members)
        need = [e for e in members if not self.muting.health(e)["ok"]]
        active = [e for e in members if e.is_active]
        if n == 1:
            e = members[0]
            value, detail = e.name, KINDS[e.kind].label_for(e.state, e.attrs) if e.available else "Offline"
        else:
            kinds = {e.kind for e in members}
            word = KINDS[members[0].kind].plural.lower() if len(kinds) == 1 else "devices"
            value = f"{n} {word}"
            if need:
                detail = f"{len(need)} need{'s' if len(need) == 1 else ''} a look"
            elif active:
                k = KINDS[active[0].kind]
                detail = f"{len(active)} {k.state_label(k.active_state).lower()}"
            else:
                detail = "All quiet"
        return {"value": value, "detail": detail, "tone": "warm" if need else "normal"}

    def _cards(self, ents: list[Entity], today: dict[str, Any]) -> dict[str, dict[str, Any]]:
        by_cat: dict[str, list[Entity]] = {}
        for e in ents:
            by_cat.setdefault(e.category, []).append(e)
        builders = {"cameras": self._camera_card, "climate": self._climate_card, "lights": self._lights_card,
                    "security": self._security_card}
        out: dict[str, dict[str, Any]] = {}
        for cid in self.cats.order():
            members = by_cat.get(cid)
            if not members:
                continue
            info = self.cats.info(cid)
            build = builders.get(info["screen"] or "")
            card = (build(members) if build else None) or self._generic_card(members)
            out[cid] = {"title": info["name"], "category": cid, "screen": info["screen"], **card}
        events, todo = today.get("events") or [], today.get("todo") or []
        if events:
            ev = events[0]
            day = {"value": ev.get("title") or "Busy",
                   "detail": " · ".join(x for x in (ev.get("time") or "", f"{len(todo)} to do" if todo else "") if x),
                   "tone": "normal"}
        else:
            day = {"value": "Nothing today", "detail": f"{len(todo)} to do" if todo else "No events or to-dos",
                   "tone": "normal"}
        out["today"] = {"title": "Today", "category": None, "screen": "today", **day}
        people = self.people()
        if people:
            home = [p for p in people if p.get("home")]
            out["people"] = {"title": "People", "category": None, "screen": "people",
                             "value": people[0]["name"] if len(people) == 1 else
                             ("Everyone's home" if len(home) == len(people) else f"{len(home)} home"),
                             "detail": ("Home" if home else "Away") if len(people) == 1 else
                             (", ".join(p["name"] for p in home[:3]) if home else "Nobody's home"),
                             "tone": "good" if home else "normal"}
        return out

    def _default_order(self, ids: list[str]) -> list[str]:
        """Where each card sits on a new Home: the categories' and Today's/People's places, then the rest."""
        def place(cid: str) -> tuple[int, int]:
            if cid in EXTRA_CARDS:
                return (0, EXTRA_CARDS[cid][1])
            c = CATEGORY.get(cid)
            return (0, c.home) if c and c.home else (1, self.cats.order().index(cid) if cid in self.cats.order() else 999)
        return sorted(ids, key=place)

    def _on_by_default(self, cid: str) -> bool:
        c = CATEGORY.get(cid)
        return cid in EXTRA_CARDS or bool(c and c.home)

    def _seen(self, current: list[str]) -> set[str]:
        """Cards Newman has shown before. The first time (this version), every card there is now - so only a
        category that turns up later is "new" (off and badged until a saved layout includes it)."""
        v = self._saved()
        if not isinstance(v.get("seen"), list):
            v["seen"] = list(current)
            self._save(v)
        return set(v["seen"])

    def _mark_seen(self, v: dict[str, Any], lay: dict[str, Any]) -> None:
        if "cards" in lay:
            v["seen"] = list(dict.fromkeys([*(v.get("seen") or []), *(c["id"] for c in lay["cards"])]))

    def arrange(self, cards: dict[str, dict[str, Any]], layout: dict[str, Any] | None) -> list[dict[str, Any]]:
        """The cards in a layout's order. No layout saved anywhere yet: the defaults. A card the layout
        doesn't mention goes last, off - marked new when Newman hasn't shown that card before."""
        seen = self._seen(list(cards))
        saved = _norm((layout or {}).get("cards")) if layout and "cards" in layout else None
        if saved is None:
            # Nothing saved yet: the cards a new Home starts with (Today, People and the categories that have
            # their own screen) are simply on - never "new"; any other category that turns up later is new.
            return [{"id": cid, "on": self._on_by_default(cid),
                     "new": cid not in seen and not self._on_by_default(cid)}
                    for cid in self._default_order(list(cards))]
        order = [{"id": c["id"], "on": c["on"], "new": False} for c in saved if c["id"] in cards]
        placed = {c["id"] for c in order}
        order += [{"id": cid, "on": False, "new": cid not in seen and cid not in EXTRA_CARDS}
                  for cid in self._default_order(list(cards)) if cid not in placed]
        return order

    def layout_for(self, user: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """The layout this person's Home uses: their own (when they may edit and have one), else the
        household's - and which it is."""
        v = self._saved()
        household = v.get("household") or {}
        own = (v.get("accounts") or {}).get(user["id"])
        if own is not None and self.can_edit(user):
            return {"cards": own["cards"] if "cards" in own else household.get("cards"),
                    "favourites": own["favourites"] if "favourites" in own else household.get("favourites") or [],
                    **({} if "cards" in own or "cards" in household else {"_nocards": True})}, "own"
        return household, "household"

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

    def json(self, user: dict[str, Any], can_automate: bool, today: dict[str, Any] | None = None) -> dict[str, Any]:
        ents = self.hub.all()
        today = today if today is not None else self.today({})
        cards = self._cards(ents, today)
        layout, which = self.layout_for(user)
        order = self.arrange(cards, None if layout.get("_nocards") else layout)
        house = self.house.get()
        cam = house["cameras"]
        top = self._latest_alert(cam["featured"] if cam["mode"] == "featured" else None)
        if cam["mode"] == "featured" and cam["featured"] and not top:
            top = {"camera": cam["featured"], "label": None, "zone": None, "at": None, "snapshot": None, "clip": None}
        t = self._thermostat(ents)
        saved_rooms = self._saved().get("climateRooms")
        if isinstance(saved_rooms, list):
            rooms = [{"entity": r["entity"], "name": r.get("name") or self.hub.name(r["entity"]),
                      "value": _num(e.state)} for r in saved_rooms if (e := self.hub.get(r["entity"]))]
        else:
            rooms = [{"entity": e.id, "name": e.name, "value": _num(e.state)} for e in ents if self._is_temperature(e)]
        return {
            "cards": [{**c, **cards[c["id"]]} for c in order],
            "favourites": [f for f in layout.get("favourites") or [] if self.hub.get(f)],
            "status": self._status(ents),
            "cameras": {"mode": cam["mode"], "featured": cam["featured"], "top": top},
            "climate": {"thermostat": t.id if t else None, "rooms": rooms,
                        "range": house["climate"]["range"], "bands": house["climate"]["bands"]},
            "canAutomate": can_automate,
            "canEditHome": self.can_edit(user),
            "layout": which,
        }

    def layouts(self, users: list[dict[str, Any]], today: dict[str, Any]) -> dict[str, Any]:
        """The admin's view (Dashboards > Phone Home): the household layout over every card there could be,
        the categories, and who has their own layout / may edit it."""
        cards = self._cards(self.hub.all(), today)
        household = self._saved().get("household") or {}
        order = self.arrange(cards, household if "cards" in household else None)
        return {"household": {"cards": [{**c, "title": cards[c["id"]]["title"], "category": cards[c["id"]]["category"],
                                         "screen": cards[c["id"]]["screen"]} for c in order],
                              "favourites": [f for f in household.get("favourites") or [] if self.hub.get(f)]},
                "categories": self.cats.list(),
                "people": [{"userId": u["id"], "name": u["name"], "layout": "own" if self.has_own(u["id"]) else "household",
                            "canEditHome": self.can_edit(u)} for u in users if u.get("role") != "tablet"]}
