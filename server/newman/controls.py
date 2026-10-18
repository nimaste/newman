"""A remote's Controls: which lights it controls and what each of its buttons does - a setting of the
remote, not a pile of automations.

Stored under the setting "remoteControls": {remote id: {"lights": [ids], "onLevel": 1-100 | None (None =
the brightness they had last), "hold": bool, "buttons": {press: override}}}. A press is the event the
remote fires for it: "<button id>" for a tap, "<button id>_long" for a hold (its "_released" only ends a
ramp). An override is {"do": "device", "entity", "command", "value"?} | {"do": "automation", "automation"}
| {"do": "nothing"}; a press without one does the standard thing for its button's role (attrs.buttons:
"on" | "off" | "raise" | "lower" | None) to the lights. The engine runs them (Engine._controls_press).

Nothing here knows any kind of device by name: what can be switched, dimmed or ramped comes from KINDS.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

from .kinds import KINDS, Command

if TYPE_CHECKING:
    from .automations.engine import Engine
    from .state import Entity

log = logging.getLogger(__name__)

SETTING = "remoteControls"
MIGRATION = "remoteControlsMigration"
STEP = 10  # a tap of "raise" / "lower": this many points brighter or dimmer
POSITIONS = {"top", "bottom", "left", "right", "middle"}  # labels written lower case after "Tap" / "Hold"


def _switchable(ent: Entity | None) -> bool:
    k = KINDS.get(ent.kind) if ent else None
    return bool(k and k.command("turn_on") and k.command("turn_off"))


def _setter(ent: Entity) -> Command | None:
    """The command that sets its brightness (a "percent" command on the attr the ramps move), if it dims."""
    k = KINDS[ent.kind]
    ramp = next((c for c in k.automation_commands if c.ramp), None)
    if not ramp or ent.attrs.get("dimmable") is False:
        return None
    return next((c for c in k.commands if c.attr == ramp.attr and c.takes_value), None)


def _ramp(ent: Entity, up: bool) -> Command | None:
    if ent.attrs.get("dimmable") is False:
        return None
    return next((c for c in KINDS[ent.kind].automation_commands if c.ramp == (1 if up else -1)), None)


def buttons(ent: Entity) -> list[dict[str, Any]]:
    """The remote's buttons: its integration's attrs.buttons, else one per button event it fires."""
    given = ent.attrs.get("buttons")
    if isinstance(given, list) and given:
        return [b for b in given if isinstance(b, dict) and b.get("id")]
    out = []
    for ev in ent.attrs.get("events") or []:
        if not isinstance(ev, str) or ev.endswith(("_long", "_released", "_double")):
            continue
        m = re.fullmatch(r"button_(\d+)", ev)
        out.append({"id": ev, "label": f"Button {m.group(1)}" if m else ev.replace("_", " ").capitalize(), "role": None})
    return out


def presses(ent: Entity) -> list[dict[str, Any]]:
    """Every press it has, in button order: a tap, then a hold where the button can be held."""
    events = set(ent.attrs.get("events") or [])
    out = []
    for b in buttons(ent):
        label = b.get("label") or b["id"]
        name = label.lower() if label.lower() in POSITIONS else label
        out.append({"key": b["id"], "button": b, "hold": False, "label": f"Tap {name}"})
        if f"{b['id']}_long" in events:
            out.append({"key": f"{b['id']}_long", "button": b, "hold": True, "label": f"Hold {name}"})
    return out


class RemoteControls:
    def __init__(self, engine: Engine) -> None:
        self.engine, self.hub, self.store = engine, engine.hub, engine.store
        self.all: dict[str, dict[str, Any]] = dict(self.store.settings().get(SETTING) or {})

    # -- stored -----------------------------------------------------------------------------------

    def get(self, remote_id: str) -> dict[str, Any] | None:
        return self.all.get(remote_id)

    def _save(self) -> None:
        self.store.set_setting(SETTING, self.all)

    def set(self, remote_id: str, body: Any) -> dict[str, Any]:
        """Check and keep a remote's Controls; ValueError (in plain words) when something's wrong."""
        self.all[remote_id] = self.clean(remote_id, body)
        self._save()
        return self.all[remote_id]

    def remove(self, remote_id: str) -> bool:
        gone = self.all.pop(remote_id, None) is not None
        if gone:
            self._save()
        return gone

    # -- checking ---------------------------------------------------------------------------------

    def clean(self, remote_id: str, body: Any) -> dict[str, Any]:
        remote = self.hub.get(remote_id)
        if not remote or not presses(remote):
            raise ValueError("That device has no buttons to set up")
        if not isinstance(body, dict):
            raise ValueError("Send the remote's controls")
        lights = body.get("lights") or []
        if not isinstance(lights, list) or not all(isinstance(x, str) for x in lights):
            raise ValueError("lights: a list of device ids")
        for lid in lights:
            ent = self.hub.get(lid)
            if not ent:
                raise ValueError(f"There's no device {lid}")
            if not _switchable(ent):
                raise ValueError(f"{ent.name} can't be switched on and off")
        level = body.get("onLevel")
        if level is not None and (isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 100):
            raise ValueError("The brightness to turn on at is 1 to 100%")
        hold = body.get("hold", True)
        if not isinstance(hold, bool):
            raise ValueError("hold: true or false")
        keys = {p["key"]: p for p in presses(remote)}
        out_buttons: dict[str, dict[str, Any]] = {}
        given = body.get("buttons") or {}
        if not isinstance(given, dict):
            raise ValueError("buttons: one entry per press")
        for key, o in given.items():
            if key not in keys:
                raise ValueError(f"{remote.name} has no press called {key}")
            out_buttons[key] = self._clean_override(keys[key], o)
        return {"lights": list(dict.fromkeys(lights)), "onLevel": level, "hold": hold, "buttons": out_buttons}

    def _clean_override(self, press: dict[str, Any], o: Any) -> dict[str, Any]:
        what = o.get("do") if isinstance(o, dict) else None
        if what == "nothing":
            return {"do": "nothing"}
        if what == "automation":
            aid = o.get("automation")
            if aid not in self.engine.autos:
                raise ValueError(f"{press['label']}: pick an automation that exists")
            return {"do": "automation", "automation": aid}
        if what == "device":
            ent = self.hub.get(str(o.get("entity") or ""))
            if not ent:
                raise ValueError(f"{press['label']}: pick a device")
            cmd = KINDS[ent.kind].action(str(o.get("command") or ""))
            if not cmd:
                raise ValueError(f"{press['label']}: {ent.name} can't do that")
            if cmd.ramp and not press["hold"]:
                raise ValueError(f"{press['label']}: “{cmd.label}” only works on a press that's held")
            out = {"do": "device", "entity": ent.id, "command": cmd.id}
            if cmd.takes_value:
                v = o.get("value")
                if v is None or v == "":
                    raise ValueError(f"{press['label']}: {cmd.label.lower()} needs a value")
                if cmd.value_type in ("percent", "number"):
                    if isinstance(v, bool) or not isinstance(v, (int, float)):
                        raise ValueError(f"{press['label']}: {cmd.label.lower()} needs a number")
                    lo = cmd.min if cmd.min is not None else (0 if cmd.value_type == "percent" else None)
                    hi = cmd.max if cmd.max is not None else (100 if cmd.value_type == "percent" else None)
                    if (lo is not None and v < lo) or (hi is not None and v > hi):
                        raise ValueError(f"{press['label']}: {cmd.label.lower()} is {lo:g} to {hi:g}{cmd.unit}")
                out["value"] = v
            return out
        raise ValueError("Each press does the standard thing, a device, an automation or nothing")

    # -- what a press does ------------------------------------------------------------------------

    def plan(self, remote: Entity, key: str) -> list[tuple]:
        """What pressing `key` on the remote does now: ("command", eid, command, value) /
        ("ramp", eid, command) / ("automation", id) steps; [] when nothing."""
        c = self.all.get(remote.id)
        press = next((p for p in presses(remote) if p["key"] == key), None)
        if not c or not press:
            return []
        o = c["buttons"].get(key)
        if o:
            if o["do"] == "automation":
                return [("automation", o["automation"])]
            if o["do"] == "device":
                ent = self.hub.get(o["entity"])
                cmd = KINDS[ent.kind].action(o["command"]) if ent else None
                if not cmd:
                    return []
                return [("ramp", ent.id, cmd.id)] if cmd.ramp else [("command", ent.id, cmd.id, o.get("value"))]
            return []
        role, steps = press["button"].get("role"), []
        for lid in c["lights"]:
            ent = self.hub.get(lid)
            if not ent:
                continue
            setter, lit = _setter(ent), ent.state == KINDS[ent.kind].active_state
            level = ent.attrs.get(setter.attr) if setter and lit else None
            if press["hold"]:
                up = role in ("on", "raise")
                if role in ("on", "off") and not c.get("hold", True) or role not in ("on", "off", "raise", "lower"):
                    continue
                if r := _ramp(ent, up):
                    steps.append(("ramp", ent.id, r.id))
            elif role == "on":
                if c.get("onLevel") and setter:
                    steps.append(("command", ent.id, setter.id, c["onLevel"]))
                else:
                    steps.append(("command", ent.id, "turn_on", None))
            elif role == "off":
                steps.append(("command", ent.id, "turn_off", None))
            elif role in ("raise", "lower") and setter:
                if role == "raise":
                    now = level if isinstance(level, (int, float)) else 0
                    steps.append(("command", ent.id, setter.id, min(setter.max or 100, now + STEP)))
                elif isinstance(level, (int, float)):
                    steps.append(("command", ent.id, setter.id, max(setter.min or 1, level - STEP)))
        return steps

    def does(self, remote: Entity, press: dict[str, Any], c: dict[str, Any]) -> tuple[str, bool]:
        """A press in words ("On at 60%", "Turn off Kitchen") and whether it's not the standard thing."""
        o = (c.get("buttons") or {}).get(press["key"])
        if o:
            if o["do"] == "nothing":
                return "Nothing", True
            if o["do"] == "automation":
                a = self.engine.autos.get(o["automation"])
                return f"Run {a['name'] if a else 'an automation that was deleted'}", True
            ent = self.hub.get(o["entity"])
            cmd = KINDS[ent.kind].action(o["command"]) if ent else None
            if not cmd:
                return "Nothing (its device is gone)", True
            text = f"{cmd.label} {ent.name}"
            if cmd.takes_value and o.get("value") is not None:
                v = o["value"]
                text += f" to {v:g}{cmd.unit}" if isinstance(v, (int, float)) else f" to {v}"
            return text, True
        return self.standard(press, c), False

    @staticmethod
    def standard(press: dict[str, Any], c: dict[str, Any]) -> str:
        role = press["button"].get("role")
        if not c.get("lights") or role not in ("on", "off", "raise", "lower"):
            return "Nothing"
        if press["hold"]:
            if role in ("on", "off") and not c.get("hold", True):
                return "Nothing"
            return "Brighten until you let go" if role in ("on", "raise") else "Dim until you let go"
        if role == "on":
            return f"On at {c['onLevel']}%" if c.get("onLevel") else "On"
        if role == "off":
            return "Off"
        return "A bit brighter" if role == "raise" else "A bit dimmer"

    def rows(self, remote: Entity, c: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        c = c if c is not None else (self.all.get(remote.id) or {"lights": [], "onLevel": None, "hold": True, "buttons": {}})
        out = []
        for p in presses(remote):
            text, custom = self.does(remote, p, c)
            out.append({"key": p["key"], "label": p["label"], "does": text, "custom": custom})
        return out

    def json(self, remote: Entity) -> dict[str, Any] | None:
        """The remote's `controls` in its entity JSON (None when it has none)."""
        c = self.all.get(remote.id)
        if not c:
            return None
        return {"lights": c["lights"], "onLevel": c.get("onLevel"), "hold": c.get("hold", True),
                "rows": self.rows(remote, c)}

    def choices(self, remote: Entity) -> dict[str, Any]:
        devices = []
        for e in sorted(self.hub.all(), key=lambda x: x.name.lower()):
            k = KINDS.get(e.kind)
            if not k or e.id == remote.id or not (k.commands or k.automation_commands):
                continue
            cmds = [c.to_json() for c in k.commands]
            if e.attrs.get("dimmable") is not False:
                cmds += [{**c.to_json(), "holdOnly": True} for c in k.automation_commands if c.ramp]
            devices.append({"id": e.id, "name": e.name, "kind": e.kind, "switchable": _switchable(e),
                            "area": e.attrs.get("area") or "", "commands": cmds})
        autos = sorted(({"id": a["id"], "name": a.get("name") or a["id"]} for a in self.engine.autos.values()),
                       key=lambda a: a["name"].lower())
        return {"devices": devices, "automations": autos}

    # -- what uses what ---------------------------------------------------------------------------

    def remotes_using(self, entity_id: str) -> list[str]:
        """Remotes whose Controls name this device (as a light or in a press) - or are that remote."""
        out = []
        for rid, c in self.all.items():
            if rid == entity_id or entity_id in c["lights"] or any(
                    o.get("entity") == entity_id for o in c["buttons"].values()):
                out.append(rid)
        return out

    def remotes_running(self, automation_id: str) -> list[str]:
        return [rid for rid, c in self.all.items()
                if any(o.get("automation") == automation_id for o in c["buttons"].values())]

    def replace_ids(self, mapping: dict[str, str]) -> int:
        n, out = 0, {}
        for rid, c in self.all.items():
            if rid in mapping:
                rid, n = mapping[rid], n + 1
            lights = []
            for lid in c["lights"]:
                if lid in mapping:
                    lid, n = mapping[lid], n + 1
                lights.append(lid)
            buttons = {}
            for k, o in c["buttons"].items():
                if o.get("entity") in mapping:
                    o, n = {**o, "entity": mapping[o["entity"]]}, n + 1
                buttons[k] = o
            out[rid] = {**c, "lights": list(dict.fromkeys(lights)), "buttons": buttons}
        if n:
            self.all = out
            self._save()
        return n

    def forget_automation(self, automation_id: str) -> list[str]:
        """An automation was deleted: presses that ran it do nothing now (said so on the remote)."""
        changed = []
        for rid, c in self.all.items():
            for k, o in c["buttons"].items():
                if o.get("automation") == automation_id:
                    c["buttons"][k] = {"do": "nothing"}
                    changed.append(rid)
        if changed:
            self._save()
            for rid in dict.fromkeys(changed):
                self.store.log_event("system", f"{self.hub.name(rid)}: a press now does nothing",
                                     "The automation it ran was deleted.", rid)
        return list(dict.fromkeys(changed))

    # -- moving button automations over (once) ----------------------------------------------------

    def pending_migration(self) -> list[str] | None:
        """Remotes still to look at, or None once that's done. The first time: every remote an enabled
        automation is started by."""
        m = self.store.settings().get(MIGRATION)
        if isinstance(m, dict) and m.get("done"):
            return None
        if isinstance(m, dict) and isinstance(m.get("pending"), list):
            return m["pending"]
        pending = sorted({i["entity"] for a in self.engine.autos.values() if a.get("enabled")
                          for i in a["trigger"].get("items", []) if i.get("type") == "event" and i.get("entity")})
        self.store.set_setting(MIGRATION, {"pending": pending})
        return pending

    def migrate(self) -> list[str]:
        """Turn each remote's set of standard button automations into its Controls, for the remotes that
        are here now with their buttons; the ones that aren't yet stay pending. Returns what it said."""
        pending = self.pending_migration()
        if pending is None:
            return []
        said, left = [], []
        for rid in pending:
            ent = self.hub.get(rid)
            if ent and ent.kind != "remote":
                continue  # a doorbell, a container…: not a remote, nothing to move
            if not ent or not ent.attrs.get("events"):
                left.append(rid)  # its connection hasn't reported it (or its buttons) yet
                continue
            text = self._migrate_one(ent)
            if text:
                said.append(text)
        if left:
            self.store.set_setting(MIGRATION, {"pending": left})
        else:
            self.store.set_setting(MIGRATION, {"done": True})
        return said

    async def migrate_soon(self, every: float = 10.0, tries: int = 60) -> None:
        """At startup: move button automations over as the remotes they're on come in (up to ~10 minutes;
        any still missing then are looked at on the next start)."""
        for _ in range(tries):
            try:
                self.migrate()
            except Exception:  # noqa: BLE001 - never stops Newman starting
                log.exception("Moving button automations to remotes' Controls failed")
                return
            if self.pending_migration() is None:
                return
            await asyncio.sleep(every)

    def _migrate_one(self, remote: Entity) -> str | None:
        if remote.id in self.all:
            return None
        roles = {b["id"]: b.get("role") for b in buttons(remote)}
        on = next((b for b, r in roles.items() if r == "on"), None)
        off = next((b for b, r in roles.items() if r == "off"), None)
        mine = [a for a in self.engine.autos.values() if a.get("enabled") and any(
            i.get("entity") == remote.id for i in a["trigger"].get("items", []))]
        if not mine or not on or not off:
            return None
        found: dict[str, tuple[str, Any]] = {}
        lights: set[str] | None = None
        for a in mine:
            items = a["trigger"].get("items", [])
            c = a["condition"]
            if (len(items) != 1 or items[0].get("type") != "event" or a["trigger"].get("type") != "event"
                    or a["notify"].get("enabled") or a["wall"].get("enabled") or a["autoCommand"].get("enabled")
                    or a["live"].get("enabled") or c.get("entities") or c.get("devices") or c.get("between")
                    or c.get("days") or c.get("sun") or c.get("dates") or len(a.get("actions") or []) != 1):
                return None
            act = a["actions"][0]
            if act.get("for") or act.get("forMinutes") or not act.get("entities"):
                return None
            these = set(act["entities"])
            if lights is not None and these != lights:
                return None
            lights = these
            ev, cmd = items[0].get("event"), act.get("command")
            if ev in found:
                return None
            found[ev] = (cmd, act.get("value"))
        assert lights is not None
        want_taps = {on: {"turn_on", "set_brightness"}, off: {"turn_off"}}
        want_holds = {f"{on}_long": {"brighten_while_held"}, f"{off}_long": {"dim_while_held"}}
        if set(found) not in (set(want_taps), set(want_taps) | set(want_holds)):
            return None
        for ev, (cmd, _) in found.items():
            if cmd not in {**want_taps, **want_holds}[ev]:
                return None
        cmd, value = found[on]
        level = value if cmd == "set_brightness" and isinstance(value, int) and 1 <= value <= 100 else None
        if cmd == "set_brightness" and level is None:
            return None
        if any(not _switchable(self.hub.get(x)) for x in lights):
            return None
        order = list(mine[0]["actions"][0]["entities"])
        self.all[remote.id] = {"lights": order, "onLevel": level, "hold": f"{on}_long" in found, "buttons": {}}
        self._save()
        for a in mine:
            self.engine.delete(a["id"])
        text = f"{remote.name}'s {len(mine)} button automations became its Controls"
        log.info("%s (%s)", text, ", ".join(a["name"] for a in mine))
        self.store.log_event("system", text, ", ".join(a["name"] for a in mine), remote.id)
        return text
