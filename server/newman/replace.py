"""Swapping devices: point everything that uses one device at another instead.

For moving to a new way of reaching the same things (ring-mqtt's devices -> the Ring connection's,
say) or replacing a broken sensor: automations (their "When" parts, "Only if" rows, actions and
notification buttons), dashboards (tiles, readings and "show as" names), phone layouts, names given to devices, and
whatever a connection keeps itself (an upkeep item counting a device's running hours) all move to the
new device ids in one go. Nothing here knows about any particular connection or kind of device.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .automations import describe as d

if TYPE_CHECKING:
    from .app import Newman

DASHBOARD_META = ("id", "userId", "isDefault", "updatedAt")


def swap(value: Any, mapping: dict[str, str]) -> tuple[Any, int]:
    """`value` with every device id in it swapped, and how many were. Ids show up as plain strings
    (lists of entities, "entity": …), as keys ({entity id: "Garage"}) and after a prefix in
    notification buttons ("lock:<id>")."""
    if isinstance(value, str):
        if value in mapping:
            return mapping[value], 1
        head, sep, tail = value.partition(":")
        if sep and tail in mapping:
            return f"{head}:{mapping[tail]}", 1
        return value, 0
    if isinstance(value, list):
        out, n = [], 0
        for v in value:
            v2, k = swap(v, mapping)
            out.append(v2)
            n += k
        return out, n
    if isinstance(value, dict):
        res: dict[Any, Any] = {}
        n = 0
        for key, v in value.items():
            if isinstance(key, str) and key in mapping:
                key, n = mapping[key], n + 1
            v2, k = swap(v, mapping)
            res[key] = v2
            n += k
        return res, n
    return value, 0


swap_ids = swap


def rename_properties(value: Any, props: dict[str, dict[str, str]]) -> tuple[Any, int]:
    """`value` with the values it reads from the old devices called what the new device calls them
    ({old id: {"value": "humidity"}}): an automation row or a readings tile on an old humidity sensor
    reads "humidity" from the device it's part of now. Only where every device the row names moves the
    same way; how many changed."""
    if isinstance(value, list):
        out, n = [], 0
        for v in value:
            v2, k = rename_properties(v, props)
            out.append(v2)
            n += k
        return out, n
    if not isinstance(value, dict):
        return value, 0
    res: dict[Any, Any] = {}
    n = 0
    for key, v in value.items():
        res[key], k = rename_properties(v, props)
        n += k
    ids = res.get("entities") if isinstance(res.get("entities"), list) else [res["entity"]] if "entity" in res else []
    prop = res.get("property")
    if isinstance(prop, str) and ids and all(isinstance(i, str) and prop in props.get(i, {}) for i in ids):
        to = {props[i][prop] for i in ids}
        if len(to) == 1:
            res["property"] = to.pop()
            n += 1
    return res, n


def check_pairs(pairs: Any, hub: Any) -> dict[str, str]:
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("Say which devices to swap")
    mapping: dict[str, str] = {}
    for p in pairs:
        old, new = (str(p.get("from") or "").strip(), str(p.get("to") or "").strip()) if isinstance(p, dict) else ("", "")
        if not old or not new:
            raise ValueError("Each swap needs the old device and the new one")
        if old == new:
            raise ValueError(f"{hub.name(old)} can't replace itself")
        if not hub.get(new):
            raise ValueError(f"The new device {new} isn't there (yet) - is its connection running?")
        if old in mapping and mapping[old] != new:
            raise ValueError(f"{hub.name(old)} can only be replaced by one device")
        mapping[old] = new
    return mapping


async def replace_entities(h: "Newman", pairs: Any) -> dict[str, Any]:
    """Swap device ids everywhere; returns how many of each thing changed (and which dashboards' owners
    should be told)."""
    mapping = check_pairs(pairs, h.hub)
    counts: dict[str, Any] = {"automations": 0, "dashboards": 0, "names": 0, "references": 0}
    # A pair may say what the old device's values are called on the new one: {"properties": {old: new}}.
    props = {str(p["from"]).strip(): {str(a): str(b) for a, b in p["properties"].items()}
             for p in pairs if isinstance(p.get("properties"), dict) and p["properties"]}

    def swap(value: Any, mapping: dict[str, str]) -> tuple[Any, int]:
        renamed, n = rename_properties(value, props) if props else (value, 0)
        out, k = swap_ids(renamed, mapping)
        return out, n + k

    for aid, auto in list(h.engine.autos.items()):
        new, n = swap(auto, mapping)
        if n:
            new = d.normalize(new)
            h.engine.autos[aid] = new
            h.store.save_automation(new)
            counts["automations"] += 1
            counts["references"] += n

    owners = set()
    for dash in h.store.dashboards():
        body = {k: v for k, v in dash.items() if k not in DASHBOARD_META}
        new, n = swap(body, mapping)
        if n:
            h.store.save_dashboard(dash["userId"], new, dash["id"])
            owners.add(dash["userId"])
            counts["dashboards"] += 1
            counts["references"] += n

    # Phone layouts: the household default (everyone using it is told) and people's own.
    own = h.store.phone_layout_times()
    counts["phoneLayouts"] = 0
    for uid in [h.store.PHONE_DEFAULT, *own]:
        saved = h.store.phone_layout(uid)
        new, n = swap(saved["layout"], mapping) if saved else (None, 0)
        if n:
            h.store.save_phone_layout(uid, new)
            counts["phoneLayouts"] += 1
            counts["references"] += n
            owners |= {u["id"] for u in h.store.users() if u["role"] != "tablet" and u["id"] not in own} \
                if uid == h.store.PHONE_DEFAULT else {uid}

    names = h.store.entity_names()
    for old, new_id in mapping.items():
        # A name given to the old device goes to the new one - unless that already has its own.
        if old in names and new_id not in names:
            h.store.rename_entity(new_id, names[old])
            counts["names"] += 1
    if counts["names"]:
        h.hub.set_name_overrides(h.store.entity_names())
    if getattr(h, "video", None):  # video settings and "Video from" links (video.py)
        if n := h.video.replace_ids(mapping):
            counts["video"] = n
            counts["references"] += n

    if getattr(h, "live", None):  # "Live status while…" settings (live.py)
        if n := h.live.replace_ids(mapping):
            counts["live"] = n
            counts["references"] += n

    for inst in list(h.integrations.instances.values()):
        if n := await inst.replace_entities(mapping):  # counted under the connection's type ("upkeep": 1)
            counts[inst.type] = counts.get(inst.type, 0) + n
            counts["references"] += n
    counts["dashboardOwners"] = sorted(owners)
    return counts


def uses(h: "Newman", entity_id: str) -> list[str]:
    """What still uses a device - the same places a swap moves (automations, dashboards, phone layouts)
    plus phone widgets showing it - as "automation" / "dashboard" / "phone layout" / "widget", each once.
    Empty: nothing does, so it can go without breaking anything."""
    def found(value: Any) -> bool:
        return swap(value, {entity_id: entity_id})[1] > 0

    out = []
    if any(found(a) for a in h.engine.autos.values()):
        out.append("automation")
    if any(found({k: v for k, v in dash.items() if k not in DASHBOARD_META}) for dash in h.store.dashboards()):
        out.append("dashboard")
    for uid in [h.store.PHONE_DEFAULT, *h.store.phone_layout_times()]:
        saved = h.store.phone_layout(uid)
        if saved and found(saved["layout"]):
            out.append("phone layout")
            break
    push = getattr(h, "widget_push", None)
    if push and push.showing(entity_id):
        out.append("widget")
    return out


def references(h: "Newman", entity_id: str) -> list[dict[str, Any]]:
    """Each thing that still uses a device, by name - the places uses() looks at:
    [{"type": "automation" | "dashboard" | "phoneLayout" | "widget", "id", "name"}]."""
    def found(value: Any) -> bool:
        return swap(value, {entity_id: entity_id})[1] > 0

    out: list[dict[str, Any]] = []
    for aid, auto in h.engine.autos.items():
        if found(auto):
            out.append({"type": "automation", "id": aid, "name": auto.get("name") or aid})
    for dash in h.store.dashboards():
        if found({k: v for k, v in dash.items() if k not in DASHBOARD_META}):
            out.append({"type": "dashboard", "id": dash["id"], "name": dash.get("name") or "A dashboard"})
    users = {u["id"]: u for u in h.store.users()}
    for uid in [h.store.PHONE_DEFAULT, *h.store.phone_layout_times()]:
        saved = h.store.phone_layout(uid)
        if saved and found(saved["layout"]):
            who = "Household default" if uid == h.store.PHONE_DEFAULT else \
                f"{users[uid]['name']}'s" if uid in users else "A"
            out.append({"type": "phoneLayout", "id": uid, "name": f"{who} phone layout"})
    push = getattr(h, "widget_push", None)
    if push and push.showing(entity_id):
        out.append({"type": "widget", "id": None, "name": "A phone widget"})
    return out
