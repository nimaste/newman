"""Phone home-screen widgets and quick tiles (the iPhone's WidgetKit widgets, Android's Glance widgets
and Quick Settings tiles): a small, fixed view of any device, worked out from kinds.py alone.

GET  /api/widgets/devices    - every device a widget can show, for the widget's device picker
GET  /api/widgets/state?ids= - those devices now: state, a reading, its colour (tone), and what a tap does
GET  /api/widgets/home       - the "Home status" widget: all secure or not, what isn't and what fixes it
POST /api/widgets/push-token - this phone's widget push token (push/widgets.py sends the reloads)

A device is widget-showable when its kind can do something in one tap (a command that needs no value),
reports a reading (its state is a number, or a summary property is), or has more than one state to show
- cameras excepted (a picture, not a state). What a tap does is the same as on the app's own tiles
(HubModel.tapAction / web model.js): the on/off pair when there is one, else the command that isn't
the current state. Nothing here knows a kind of device by its id.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Callable

from aiohttp import web

from .kinds import KINDS, Command, Kind, properties_for
from .state import Entity

if TYPE_CHECKING:
    from .app import Newman
    from .push.widgets import WidgetPush

MAX_IDS = 64  # devices one state request may ask for


def plain_commands(k: Kind) -> list[Command]:
    """Commands usable in one tap (no value needed)."""
    return [c for c in k.commands if not c.takes_value]


def tap_action(e: Entity, k: Kind) -> Command | None:
    """What tapping the device does right now: its on/off pair (two plain, non-sensitive commands, one
    making it active) toggles; otherwise the first command that isn't the current state; a single
    command (a button's Press) always; else nothing."""
    plain = plain_commands(k)
    if k.active_state is not None and len(plain) == 2 and not any(c.sensitive for c in plain):
        on = next((c for c in plain if c.confirms_to == k.active_state), None)
        off = next((c for c in plain if c is not on and c.confirms_to), None)
        if on and off:
            return off if e.state == k.active_state else on
    nxt = next((c for c in plain if c.confirms_to and c.confirms_to != e.state), None)
    return nxt or (plain[0] if len(plain) == 1 else None)


def actions_json(e: Entity, k: Kind) -> list[dict[str, Any]]:
    """The device's one-tap commands, what a tap does now first ("primary": true)."""
    tap = tap_action(e, k)
    ordered = ([tap] if tap else []) + [c for c in plain_commands(k) if c is not tap]
    return [{"command": c.id, "label": c.label, "sensitive": c.sensitive, "primary": c is tap} for c in ordered]


def _number(v: Any) -> int | float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    try:
        f = float(v) if isinstance(v, float) else float(str(v).strip())
    except ValueError:
        return None
    if not math.isfinite(f):
        return None
    if isinstance(v, float):
        return v
    return int(f) if f.is_integer() and "." not in str(v) and "e" not in str(v).lower() else f  # never rounded


def reading(e: Entity, k: Kind) -> dict[str, Any] | None:
    """{"value", "unit"}: the state itself for kinds whose state is a number, else the first of the
    kind's summary properties that is a number right now; None when there's nothing to read."""
    props = properties_for(e)
    if not k.states:
        v = _number(e.state)
        if v is not None:
            own = next((p for p in props if p["attr"] == ""), None)
            unit = (own or {}).get("unit") or k.unit or str((e.attrs or {}).get("unitOfMeasurement") or "")
            return {"value": v, "unit": unit}
    by_id = {p["id"]: p for p in props}
    for pid in k.summary:
        p = by_id.get(pid)
        if p and p["type"] == "number" and (v := _number(p["value"])) is not None:
            return {"value": v, "unit": p["unit"]}
    return None


def kind_reads(k: Kind) -> bool:
    """The kind reports a number worth showing (its state, or a numeric summary property)."""
    return not k.states or any(p.id in k.summary and p.type == "number" for p in k.properties)


def showable(e: Entity) -> bool:
    k = KINDS.get(e.kind)
    if k is None:
        return False
    return bool(plain_commands(k)) or kind_reads(k) or (len(k.states) > 1 and not k.has_camera)


def icon_of(e: Entity) -> str:
    """The device's own icon when it names one, else its kind (clients map kinds to their icons)."""
    own = (e.attrs or {}).get("icon")
    return own if isinstance(own, str) and own.strip() else e.kind


def area_of(e: Entity) -> str | None:
    a = (e.attrs or {}).get("area")
    return a.strip() if isinstance(a, str) and a.strip() else None


def device_json(e: Entity) -> dict[str, Any]:
    """One entry of GET /api/widgets/devices."""
    k = KINDS[e.kind]
    return {"id": e.id, "name": e.name, "kind": e.kind, "kindName": k.plural, "area": area_of(e),
            "icon": icon_of(e), "controllable": bool(plain_commands(k))}


def secure_of(e: Entity, k: Kind) -> bool | None:
    """True at the kind's secure state (locked, closed, dry), False settled away from it; None when the
    kind has no secure state, the device is offline or has no state, or it's between states (locking)."""
    if k.secure_state is None or e.offline or e.state in (None, "") or e.state in k.transitional:
        return None
    return e.state == k.secure_state


def tone(e: Entity, k: Kind) -> str:
    """The colour a widget gives the device's state: "good" (at its secure state), "alert" (an urgent kind
    away from it - a leak), "active" (away from its secure state, or in the kind's active state - on),
    "offline", else "" (neutral)."""
    if e.offline:
        return "offline"
    s = secure_of(e, k)
    if s is True:
        return "good"
    if s is False:
        return "alert" if k.urgent else "active"
    if k.secure_state is None and k.active_state is not None and e.state == k.active_state:
        return "active"
    return ""


def state_json(e: Entity) -> dict[str, Any]:
    """One entry of GET /api/widgets/state."""
    k = KINDS[e.kind]
    return {"id": e.id, "name": e.name, "kind": e.kind, "state": e.state, "stateLabel": k.label_for(e.state, e.attrs),
            "available": e.available, "active": k.active_state is not None and e.state == k.active_state,
            "tone": tone(e, k), "icon": icon_of(e), "area": area_of(e),
            "actions": actions_json(e, k), "reading": reading(e, k)}


# -- the "Home status" widget (GET /api/widgets/home) -----------------------------------------------------

def fix_action(k: Kind) -> Command | None:
    """The one-tap command that brings a device back to its kind's secure state (Lock, Close)."""
    if k.secure_state is None:
        return None
    return next((c for c in plain_commands(k) if c.confirms_to == k.secure_state), None)


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:]


def _label(e: Entity, k: Kind) -> str:
    return k.label_for(e.state, e.attrs)


def _group(k: Kind, ents: list[Entity]) -> dict[str, Any]:
    """One line of the status list: "Locks: All locked", "Garage door: Closed", "Alarm: Disarmed"."""
    label = k.plural if len(ents) > 1 else k.name
    online = [e for e in ents if not e.offline]
    offline = len(ents) - len(online)
    secure: bool | None = None
    if not online:
        text = "Offline" if len(ents) == 1 else f"{len(ents)} offline"
        offline = 0
    elif k.secure_state is None:  # a house mode (an alarm): its state
        text = ", ".join(dict.fromkeys(_label(e, k) for e in online))
    else:
        bad = [e for e in online if secure_of(e, k) is False]
        secure = not bad
        if not bad:
            ok = k.state_label(k.secure_state)
            text = ok if len(online) == 1 else f"All {_lower_first(ok)}"
        elif len(bad) == 1 and len(online) > 1:
            text = f"{bad[0].name} {_lower_first(_label(bad[0], k))}"
        else:
            labels = list(dict.fromkeys(_lower_first(_label(e, k)) for e in bad))
            if len(online) == 1:
                text = _label(bad[0], k)
            else:
                text = f"{len(bad)} {labels[0]}" if len(labels) == 1 else f"{len(bad)} not secure"
    if offline:
        text += f" · {offline} offline"
    return {"kind": k.id, "label": label, "text": text, "secure": secure, "icon": k.id}


def home_json(ents: list[Entity]) -> dict[str, Any]:
    """Everything with a secure state (locks, doors, garage doors, leak and smoke sensors - from the kind
    metadata alone) and the house modes (an alarm), summed up for the "Home status" widget:

    secure    True when every one of them that's online is at its secure state, False when one isn't,
              None when the house has none
    headline  "All secure" / "Side Door unlocked" / "2 unlocked" / "2 not secure"
    detail    "2 locks · garage door closed" / the other ones that aren't secure
    problems  the devices that aren't secure (urgent ones first), each with the command that fixes it
    groups    one line per kind: {"label": "Locks", "text": "All locked", "secure": True}
    ids       every device it covers (what the widget's reload pushes follow)
    """
    watched = [e for e in ents if (k := KINDS.get(e.kind)) is not None and (k.secure_state is not None or k.is_mode)]
    by_kind: dict[str, list[Entity]] = {}
    for e in sorted(watched, key=lambda e: e.name.lower()):
        by_kind.setdefault(e.kind, []).append(e)
    order = list(KINDS)
    kinds = sorted(by_kind, key=lambda kid: (KINDS[kid].is_mode, order.index(kid)))
    groups = [_group(KINDS[kid], by_kind[kid]) for kid in kinds]
    groups.sort(key=lambda g: g["secure"] is not False)  # what's wrong first (stable: kind order otherwise)

    bad = [e for e in watched if secure_of(e, KINDS[e.kind]) is False]
    bad.sort(key=lambda e: (not KINDS[e.kind].urgent, fix_action(KINDS[e.kind]) is None, e.name.lower()))
    problems = []
    for e in bad:
        k = KINDS[e.kind]
        fix = fix_action(k)
        problems.append({"id": e.id, "name": e.name, "kind": e.kind, "icon": icon_of(e), "state": e.state,
                         "stateLabel": _label(e, k), "urgent": k.urgent,
                         "fix": {"command": fix.id, "label": fix.label, "sensitive": fix.sensitive} if fix else None})

    secured = [e for e in watched if KINDS[e.kind].secure_state is not None]
    offline = sum(1 for e in secured if e.offline)

    def all_good(skip: set[str]) -> list[str]:
        """"2 locks", "garage door closed": the kinds whose devices are all secure."""
        parts = []
        for kid in kinds:
            k, online = KINDS[kid], [e for e in by_kind[kid] if not e.offline]
            if k.secure_state is None or not online or kid in skip:
                continue
            ok = _lower_first(k.state_label(k.secure_state))
            parts.append(f"{len(online)} {_lower_first(k.plural)}" if len(online) > 1 else f"{k.noun} {ok}")
        return parts

    if not secured:
        secure, headline, detail = None, "Nothing to keep secure", ""
    elif not problems:
        parts = all_good(set()) + ([f"{offline} offline"] if offline else [])
        secure, headline, detail = True, "All secure", " · ".join(parts)
    else:
        labels = list(dict.fromkeys(_lower_first(p["stateLabel"]) for p in problems))
        if len(problems) == 1:
            headline = f"{problems[0]['name']} {labels[0]}"
            detail = " · ".join(all_good({problems[0]["kind"]}))
        else:
            headline = f"{len(problems)} {labels[0]}" if len(labels) == 1 else f"{len(problems)} not secure"
            detail = " · ".join(f"{p['name']} {_lower_first(p['stateLabel'])}" for p in problems)
        secure = False
    return {"secure": secure, "headline": headline, "detail": detail, "problems": problems, "groups": groups,
            "ids": [e.id for e in watched]}


def parse_ids(raw: str | None) -> list[str]:
    seen: list[str] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part and part not in seen:
            seen.append(part)
    return seen[:MAX_IDS]


def add_widget_routes(r: web.RouteTableDef, h: "Newman", *, push: "WidgetPush",
                      body: Callable[[web.Request], Any]) -> None:
    @r.get("/api/widgets/devices")
    async def widget_devices(_: web.Request) -> web.Response:
        devices = [device_json(e) for e in h.hub.all() if showable(e)]
        devices.sort(key=lambda d: ((d["area"] or "￿").lower(), d["name"].lower()))
        return web.json_response({"devices": devices})

    @r.get("/api/widgets/state")
    async def widget_state(req: web.Request) -> web.Response:
        """?ids=a,b,c (in that order; unknown or no-longer-showable ones are left out). Without ids:
        every widget-showable device."""
        ids = parse_ids(req.query.get("ids"))
        if ids:
            ents = [e for e in (h.hub.get(i) for i in ids) if e is not None and showable(e)]
        else:
            ents = [e for e in h.hub.all() if showable(e)]
        if ids and req["user"].get("id"):
            push.saw(req["user"]["id"], [e.id for e in ents])  # reload pushes only for what they show
        return web.json_response({"devices": [state_json(e) for e in ents]})

    @r.get("/api/widgets/home")
    async def widget_home(req: web.Request) -> web.Response:
        """The "Home status" widget: is everything secure, what isn't and how to fix it (home_json)."""
        body = home_json(h.hub.all())
        if body["ids"] and req["user"].get("id"):
            push.saw(req["user"]["id"], body["ids"])  # reloads when any of them changes
        return web.json_response(body)

    @r.post("/api/widgets/push-token")
    async def widget_push_token(req: web.Request) -> web.Response:
        """{token, kind: "ios-widget" | "android", deviceId, apnsEnvironment?}: replaces this phone's
        token of that kind; a null / empty token forgets it."""
        data = await body(req)
        try:
            kept = push.tokens.register(req["user"], req["session"], data)
        except ValueError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        return web.json_response({"ok": True, "kept": kept})
