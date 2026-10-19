"""Light and switch groups, and rooms.

A group ("Kitchen Under Cabinet": three drivers on one switch) is a device of its own - id
group.<slug>, connection "groups" - that switches, dims and ramps all its members together, so it works
wherever a single light does (screens, automations, a remote's Controls). It's a light when every member
is a dimmable light, else a switch. On when any member is on; its brightness is the average of the
members that are on; reachable while any member is. Each member points back at its group ("group").

Rooms: every device has one - set in Newman (settings "rooms": {entity id: room}), else what its
connection says (attrs["area"]), else none. A group's room is part of the group.

Settings "groups": {group id: {"name", "members": [ids], "room": str | None}}.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from .integrations.base import CommandError, Integration
from .categories import screen_of
from .kinds import KINDS
from .state import Entity

log = logging.getLogger(__name__)

GROUPS, ROOMS, INTEGRATION_ID = "groups", "rooms", "groups"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "group"


def groupable(e: Entity | None) -> bool:
    """A light or switch: something in a category whose screen is the lights screen (after any move) that
    turns on and off (kinds metadata)."""
    if e is None or e.integration == INTEGRATION_ID:
        return False
    return screen_of(e.category) == "lights" and {"turn_on", "turn_off"} <= {c.id for c in KINDS[e.kind].commands}


def dims(e: Entity) -> bool:
    return any(c.id == "set_brightness" for c in KINDS[e.kind].commands) and e.attrs.get("dimmable") is not False


class GroupsIntegration(Integration):
    """The groups' commands: each one sent to every member together."""
    type = "groups"
    name = "Groups"
    category = "lighting"
    available = False  # never offered in the connections catalog: groups are made on the Devices page

    def __init__(self, ctx: Any, manager: Any, groups: "Groups") -> None:
        super().__init__(INTEGRATION_ID, "Groups", {}, ctx)
        self.manager, self.groups = manager, groups
        self.status, self.detail = "connected", ""

    async def run(self) -> None:  # nothing to connect to
        return None

    def _members(self, eid: str, reachable: bool = False) -> list[str]:
        out = []
        for m in self.groups.members(eid):
            e = self.hub.get(m)
            if e and (not reachable or e.available):
                out.append(m)
        return out

    async def _each(self, eid: str, call: Any) -> None:
        ms = self._members(eid, reachable=True)
        if not ms:
            raise CommandError(f"None of {self.hub.name(eid)}'s lights can be reached")
        res = await asyncio.gather(*(call(m) for m in ms), return_exceptions=True)
        fails = [r for r in res if isinstance(r, Exception)]
        for f in fails:
            log.warning("Group %s: a member failed: %s", eid, f)
        if len(fails) == len(ms):
            raise CommandError(str(fails[0]) or f"{self.hub.name(eid)} didn't respond")

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        await self._each(entity_id, lambda m: self.manager.command(m, command, value))

    def can_move(self, entity_id: str, attr: str) -> bool:
        ms = self._members(entity_id, reachable=True)
        return bool(ms) and all(self.manager.can_move(m, attr) for m in ms)

    async def move(self, entity_id: str, attr: str, direction: int, full_s: float) -> None:
        await self._each(entity_id, lambda m: self.manager.move(m, attr, direction, full_s))

    async def command_fading(self, entity_id: str, command: str, value: Any, seconds: float) -> None:
        await self._each(entity_id, lambda m: self.manager.command_fading(m, command, value, seconds))

    # "For N minutes, then put it back": each member goes back to how it was.
    def snapshot(self, entity_id: str) -> dict[str, Any] | None:
        return {"members": {m: {"state": e.state, "brightness": e.attrs.get("brightness")}
                            for m in self._members(entity_id) if (e := self.hub.get(m))}}

    def marker(self, entity_id: str) -> dict[str, Any] | None:
        return self.snapshot(entity_id)

    async def restore(self, entity_id: str, snap: dict[str, Any], marker: dict[str, Any] | None,
                      during: str) -> str:
        put, left = 0, 0
        for m, was in (snap.get("members") or {}).items():
            e = self.hub.get(m)
            ours = ((marker or {}).get("members") or {}).get(m)
            if not e:
                continue
            if ours and (e.state != ours.get("state") or e.attrs.get("brightness") != ours.get("brightness")):
                left += 1  # changed by hand since: leave it
                continue
            try:
                if was.get("state") == "on" and isinstance(was.get("brightness"), (int, float)) and dims(e):
                    await self.manager.command(m, "set_brightness", was["brightness"])
                elif was.get("state") in ("on", "off"):
                    await self.manager.command(m, "turn_on" if was["state"] == "on" else "turn_off")
                put += 1
            except Exception as ex:  # noqa: BLE001 - the others still go back
                log.warning("Group %s: couldn't put %s back: %s", entity_id, m, ex)
        name = self.hub.name(entity_id)
        if left and not put:
            return f"Left {name} as it is: changed by hand during {during}"
        return f"Put {name} back after {during}" + (f" ({left} changed by hand, left alone)" if left else "")


class Groups:
    def __init__(self, store: Any, hub: Any, integrations: Any) -> None:
        self.store, self.hub = store, hub
        saved = store.settings().get(GROUPS)
        self._g: dict[str, dict[str, Any]] = saved if isinstance(saved, dict) else {}
        rooms = store.settings().get(ROOMS)
        self._rooms: dict[str, str] = rooms if isinstance(rooms, dict) else {}
        self.integration = GroupsIntegration(integrations._ctx, integrations, self)
        integrations.instances[INTEGRATION_ID] = self.integration
        self.listeners: list[Any] = []  # called (no args) when groups or rooms change
        # async (entity ids): devices whose "group" or "room" changed - the API sends them to the screens.
        self.touched: list[Any] = []

    # -- reading -----------------------------------------------------------------------------------

    def all(self) -> dict[str, dict[str, Any]]:
        return self._g

    def members(self, gid: str) -> list[str]:
        return list((self._g.get(gid) or {}).get("members") or [])

    def member_group(self, eid: str) -> str | None:
        return next((gid for gid, g in self._g.items() if eid in (g.get("members") or [])), None)

    def room_of(self, e: Entity) -> str | None:
        if e.id in self._g:
            return self._g[e.id].get("room") or None
        own = self._rooms.get(e.id)
        if own:
            return own
        area = e.attrs.get("area")
        return str(area).strip() or None if isinstance(area, str) else None

    def rooms(self) -> list[str]:
        return sorted({r for e in self.hub.all() if (r := self.room_of(e))}, key=str.lower)

    def json(self, gid: str) -> dict[str, Any]:
        g, e = self._g[gid], self.hub.get(gid)
        return {"id": gid, "name": g["name"], "members": list(g["members"]), "room": g.get("room"),
                "kind": e.kind if e else None}

    # -- the group devices ---------------------------------------------------------------------------

    def _kind(self, members: list[str]) -> str:
        ents = [self.hub.get(m) for m in members]
        return "light" if ents and all(e and dims(e) for e in ents) else "switch"

    async def start(self) -> None:
        for gid in list(self._g):
            await self.refresh(gid)
        self.hub.subscribe_changes(self._on_change)

    async def _on_change(self, old: Entity | None, new: Entity) -> None:
        if new.integration == INTEGRATION_ID:
            return
        for gid, g in list(self._g.items()):
            if new.id in (g.get("members") or []):
                await self.refresh(gid)

    async def refresh(self, gid: str) -> None:
        g = self._g.get(gid)
        if not g:
            return
        kind = self._kind(g["members"])
        cur = self.hub.get(gid)
        if cur and cur.kind != kind:
            self.hub.remove(gid)  # a member swapped for one that doesn't dim: registered again as a switch
        self.hub.upsert(gid, kind=kind, name=g["name"], integration=INTEGRATION_ID)
        ents = [e for m in g["members"] if (e := self.hub.get(m))]
        on = [e for e in ents if e.state == "on"]
        levels = [e.attrs["brightness"] for e in on if isinstance(e.attrs.get("brightness"), (int, float))]
        attrs: dict[str, Any] = {"members": list(g["members"]), "dimmable": kind == "light"}
        if kind == "light":
            attrs["brightness"] = round(sum(levels) / len(levels)) if levels else 0
        await self.hub.update(gid, state="on" if on else "off", available=any(e.available for e in ents),
                              attrs=attrs)

    # -- changing ------------------------------------------------------------------------------------

    def _clean(self, data: Any, gid: str | None) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("Send the group: a name, its lights and a room")
        name = str(data.get("name") or (self._g.get(gid) or {}).get("name") or "").strip()[:64]
        if not name:
            raise ValueError("Give the group a name")
        members = data.get("members", (self._g.get(gid) or {}).get("members"))
        if not isinstance(members, list) or not members or not all(isinstance(m, str) for m in members):
            raise ValueError("Pick the lights or switches in the group")
        members = list(dict.fromkeys(members))
        bad = [m for m in members if not groupable(self.hub.get(m))]
        if bad:
            raise ValueError("Only lights and switches can be in a group: " + ", ".join(self.hub.name(m) for m in bad))
        taken = {m: g2 for m in members if (g2 := self.member_group(m)) and g2 != gid}
        if taken:
            m, g2 = next(iter(taken.items()))
            raise ValueError(f"{self.hub.name(m)} is already in {self._g[g2]['name']}")
        room = data.get("room", (self._g.get(gid) or {}).get("room"))
        room = str(room).strip()[:48] if isinstance(room, str) and room.strip() else None
        return {"name": name, "members": members, "room": room}

    def _save(self) -> None:
        self.store.set_setting(GROUPS, self._g)
        for fn in list(self.listeners):
            fn()

    async def create(self, data: Any) -> str:
        g = self._clean(data, None)
        base = "group." + slug(g["name"])
        gid, n = base, 2
        while gid in self._g or self.hub.get(gid):
            gid, n = f"{base}_{n}", n + 1
        self._g[gid] = g
        self._save()
        await self.refresh(gid)
        await self._touch(g["members"])
        return gid

    async def update(self, gid: str, data: Any) -> None:
        if gid not in self._g:
            raise KeyError(gid)
        g = self._clean(data, gid)
        old = self._g[gid]["members"]
        self._g[gid] = g
        self._save()
        if self.hub.get(gid) and self.hub.get(gid).name != g["name"]:
            self.store.rename_entity(gid, g["name"])
            self.hub.set_name_overrides(self.store.entity_names())
        await self.refresh(gid)
        await self._touch(sorted(set(old) | set(g["members"])))  # members' "group" changed

    async def delete(self, gid: str) -> bool:
        g = self._g.pop(gid, None)
        if g is None:
            return False
        self._save()
        self.hub.remove(gid)
        await self._touch(g["members"])
        return True

    async def _touch(self, ids: list[str]) -> None:
        ids = [i for i in ids if self.hub.get(i)]
        for fn in list(self.touched):
            try:
                await fn(ids)
            except Exception:  # noqa: BLE001 - a screen update must never break the change itself
                log.exception("Telling the screens about %s failed", ids)

    async def set_room(self, eid: str, room: Any) -> None:
        e = self.hub.get(eid)
        if not e:
            raise KeyError(eid)
        room = str(room).strip()[:48] if isinstance(room, str) and room.strip() else None
        if eid in self._g:
            self._g[eid]["room"] = room
            self.store.set_setting(GROUPS, self._g)
        elif room:
            self._rooms[eid] = room
        else:
            self._rooms.pop(eid, None)
        self.store.set_setting(ROOMS, self._rooms)
        for fn in list(self.listeners):
            fn()
        await self._touch([eid])

    def replace_ids(self, mapping: dict[str, str]) -> int:
        """A device swapped for another: groups and its room follow it."""
        n = 0
        for g in self._g.values():
            new = [mapping.get(m, m) for m in g["members"]]
            if new != g["members"]:
                g["members"] = list(dict.fromkeys(new))
                n += 1
        for old, new in mapping.items():
            if old in self._rooms:
                self._rooms.setdefault(new, self._rooms.pop(old))
                n += 1
        if n:
            self.store.set_setting(GROUPS, self._g)
            self.store.set_setting(ROOMS, self._rooms)
        return n

    def forget(self, eid: str) -> None:
        """A device removed: out of its group (an emptied group stays, unreachable, until deleted) and its room."""
        changed = False
        for g in self._g.values():
            if eid in g["members"]:
                g["members"].remove(eid)
                changed = True
        if changed:
            self.store.set_setting(GROUPS, self._g)
        if self._rooms.pop(eid, None) is not None:
            self.store.set_setting(ROOMS, self._rooms)
