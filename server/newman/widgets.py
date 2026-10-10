"""Phone home-screen widgets and quick tiles (the iPhone's WidgetKit widgets, Android's Glance widgets
and Quick Settings tiles): a small, fixed view of any device, worked out from kinds.py alone.

GET  /api/widgets/devices    - every device a widget can show, for the widget's device picker
GET  /api/widgets/state?ids= - those devices now: state, a reading, and what a tap does
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


def state_json(e: Entity) -> dict[str, Any]:
    """One entry of GET /api/widgets/state."""
    k = KINDS[e.kind]
    return {"id": e.id, "name": e.name, "kind": e.kind, "state": e.state, "stateLabel": k.label_for(e.state, e.attrs),
            "available": e.available, "active": k.active_state is not None and e.state == k.active_state,
            "icon": icon_of(e), "area": area_of(e),
            "actions": actions_json(e, k), "reading": reading(e, k)}


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
