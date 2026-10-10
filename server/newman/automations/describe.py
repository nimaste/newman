"""Automation documents: defaults, validation, buttons, and the plain-English
sentence + notification text the dashboard and apps show. Clients never
re-implement this - they POST the in-progress automation to /describe."""
from __future__ import annotations

import copy
import re
import time
from datetime import date, datetime
from typing import Any

from .. import sun
from ..kinds import KINDS, OPS, find_property, format_value, humanize, join_names, property_matches, render
from ..kinds import event_phrase as kinds_event_phrase
from ..state import Entity, Hub

DEFAULT_FOLLOWUP = "{person} {action} {devices}."
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MATCHES = ("any", "all", "none", "always")

DEFAULTS: dict[str, Any] = {
    "name": "",
    "enabled": True,
    "trigger": {"type": "time", "time": "21:00", "days": [0, 1, 2, 3, 4, 5, 6]},
    # `between` (optional): {"after": "19:00", "before": "06:00"} - only run inside that window (may wrap midnight).
    # `days` (optional): [0..6], Monday = 0 - only run on those days.
    # `sun` (optional): {"only": "dark" | "light", "sunset": minutes, "sunrise": minutes} - only when it's
    # dark (from sunset + its offset until sunrise + its offset, across midnight) or during daylight.
    # `dates` (optional): {"from": "12-01", "to": "01-06", "year": None | 2027} - only between those dates
    # (inclusive). No year = every year (may wrap over New Year); a year = just that once (`to` before
    # `from` = in the next year).
    "condition": {"mode": "any", "entities": [], "between": None, "days": None, "sun": None, "dates": None},
    # Commands on any devices, run when the automation fires:
    # [{"entities": [...], "command", "value"?, "for"?}] (older ones name a single "entity").
    "actions": [],
    "notify": {"enabled": True, "title": "", "buttons": [], "onlyPending": True, "timeSensitive": False,
               "snapshot": True},
    # Show it on wall tablets: `tablets` = tablet accounts' ids ([] = every wall tablet); `style`
    # "card" (slides in, goes after a while) | "full" (full screen, with the camera if there is one) |
    # "alarm" (full screen until someone taps OK); `sound` plays a chime; `wake` lights up a dimmed screen.
    "wall": {"enabled": False, "tablets": [], "style": "card", "sound": True, "wake": True},
    "autoCommand": {"enabled": False, "command": "lock"},
    # Show a live status (iPhone Live Activity / Android ongoing notification) of `entities` to `recipients`
    # (None = everyone; else person ids) until `until` says it's over (see clean_live). `title` "" = the
    # devices' names.
    "live": {"enabled": False, "entities": [], "title": "", "recipients": None,
             "until": {"type": "leaves", "entities": [], "state": "active"}},
    "recipients": [],
    "followUp": {"enabled": False, "message": DEFAULT_FOLLOWUP, "remindMinutes": None},
}


def normalize(auto: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(DEFAULTS)
    for key, val in (auto or {}).items():
        if isinstance(out.get(key), dict) and isinstance(val, dict):
            out[key].update(val)
        else:
            out[key] = val
    out["trigger"] = normalize_trigger(out["trigger"])
    c = out["condition"]
    # `devices`: the "Only if … is …" rows - [{"entities": [...], "state": id|"active", "match": any|all|none|always}],
    # all of which must hold. "active" = each device's own active state (unlocked, on, open…).
    # Older automations have one flat `entities` + `mode`.
    rows = c.get("devices")
    if rows is None:
        rows = [{"entities": list(c.get("entities") or []), "state": "active", "match": c.get("mode") or "any"}] \
            if c.get("entities") else []
    c["devices"] = [dict(r, entities=list(r.get("entities") or []), state=r.get("state") or "active",
                         match=r.get("match") if r.get("match") in MATCHES else "any") for r in rows]
    c["entities"] = list(dict.fromkeys(e for r in c["devices"] for e in r["entities"]))
    if c["devices"]:
        c["mode"] = c["devices"][0]["match"]
    c["sun"] = clean_sun(c.get("sun"))
    c["dates"] = clean_dates(c.get("dates"))
    w = out["wall"]
    w["tablets"] = [str(t) for t in (w.get("tablets") or []) if t]
    out["live"] = clean_live(out.get("live"))
    if not out["followUp"].get("message"):
        out["followUp"]["message"] = DEFAULT_FOLLOWUP
    rm = out["followUp"].get("remindMinutes")
    out["followUp"]["remindMinutes"] = int(rm) if rm not in (None, "", "off", 0, "0") else None
    return out


ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
DEFAULT_WITHIN = {"amount": 2, "unit": "minutes"}
SUN_EVENTS = sun.EVENTS  # what a time part can follow instead of a clock time
MAX_SUN_OFFSET = 240     # minutes either side


def _minutes(v: Any) -> int:
    try:
        return int(round(float(v or 0)))
    except (TypeError, ValueError):
        return 0


def clean_sun(s: Any) -> dict[str, Any] | None:
    if not isinstance(s, dict) or s.get("only") not in ("dark", "light"):
        return None
    return {"only": s["only"], "sunset": _minutes(s.get("sunset")), "sunrise": _minutes(s.get("sunrise"))}


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
LEAP_DAYS = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]  # Feb 29 is fine for "every year"


def parse_mmdd(v: Any) -> tuple[int, int] | None:
    """"12-01" -> (12, 1); None when it isn't a real month and day."""
    m = re.fullmatch(r"\s*(\d{1,2})-(\d{1,2})\s*", str(v or ""))
    if not m:
        return None
    mo, dy = int(m[1]), int(m[2])
    return (mo, dy) if 1 <= mo <= 12 and 1 <= dy <= LEAP_DAYS[mo - 1] else None


def clean_dates(x: Any) -> dict[str, Any] | None:
    """The "only between these dates" limit, or None. Unreadable dates are kept as they are so errors() can say so."""
    if not isinstance(x, dict) or not (x.get("from") or x.get("to")):
        return None
    out = {}
    for k in ("from", "to"):
        p = parse_mmdd(x.get(k))
        out[k] = f"{p[0]:02d}-{p[1]:02d}" if p else str(x.get(k) or "")
    y = x.get("year")
    try:  # a blank year box is an error ("pick a year"), not "every year"
        out["year"] = None if y is None else int(y)
    except (TypeError, ValueError):
        out["year"] = str(y)
    return out


def _is_leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def _on(md: tuple[int, int], year: int) -> date:
    """That month/day in a year - Feb 29 counts as Feb 28 when the year has none."""
    mo, dy = md
    return date(year, mo, 28 if (mo, dy) == (2, 29) and not _is_leap(year) else dy)


def dates_span(dates: dict[str, Any] | None) -> tuple[date, date] | None:
    """A one-off range's first and last day (None for "every year" or unreadable)."""
    a, b = parse_mmdd((dates or {}).get("from")), parse_mmdd((dates or {}).get("to"))
    y = (dates or {}).get("year")
    if not a or not b or not isinstance(y, int):
        return None
    return _on(a, y), _on(b, y if b >= a else y + 1)


def dates_ok(dates: dict[str, Any] | None, today: date | None = None) -> bool:
    """The "only between these dates" check (inclusive; True when there's none)."""
    dates = clean_dates(dates)
    if not dates:
        return True
    a, b = parse_mmdd(dates["from"]), parse_mmdd(dates["to"])
    if not a or not b:
        return True  # half-filled: errors() says so; never block on it
    today = today or date.today()
    if dates["year"] is not None:
        span = dates_span(dates)
        return span is None or span[0] <= today <= span[1]
    start, end = _on(a, today.year), _on(b, today.year)
    return start <= today <= end if start <= end else (today >= start or today <= end)


def dates_allow(auto: dict[str, Any], today: date | None = None) -> bool:
    return dates_ok(auto["condition"].get("dates"), today)


def _md_text(md: tuple[int, int]) -> str:
    return f"{MONTHS[md[0] - 1]} {md[1]}"


def dates_text(dates: dict[str, Any] | None) -> str:
    """"between December 1 and January 6 (every year)" / "from July 1 to July 15, 2027"."""
    dates = clean_dates(dates)
    a, b = (parse_mmdd(dates["from"]), parse_mmdd(dates["to"])) if dates else (None, None)
    if not a or not b:
        return ""
    if dates["year"] is None:
        return f"between {_md_text(a)} and {_md_text(b)} (every year)"
    if not isinstance(dates["year"], int):
        return ""
    y = dates["year"]
    if b >= a:
        return f"from {_md_text(a)} to {_md_text(b)}, {y}"
    return f"from {_md_text(a)}, {y} to {_md_text(b)}, {y + 1}"


def dates_errors(dates: dict[str, Any] | None) -> list[str]:
    dates = clean_dates(dates)
    if not dates:
        return []
    out = []
    if not dates["from"] or not dates["to"]:
        return ["Pick both dates for “only between dates”"]
    for k, word in (("from", "first"), ("to", "last")):
        if not parse_mmdd(dates[k]):
            out.append(f"The {word} date isn't a real date - pick a month and a day that exist")
    y = dates["year"]
    if y is not None and not (isinstance(y, int) and 2000 <= y <= 2100):
        out.append("Pick a year from 2000 to 2100, or choose “every year”")
    elif isinstance(y, int) and not out:
        a, b = parse_mmdd(dates["from"]), parse_mmdd(dates["to"])
        for md, yr in ((a, y), (b, y if b >= a else y + 1)):
            if md == (2, 29) and not _is_leap(yr):
                out.append(f"There's no February 29 in {yr}")
    return out


def _item(i: dict[str, Any]) -> dict[str, Any]:
    kind = i.get("type") or ("event" if "event" in i else "time" if "time" in i else "state")
    if kind == "time":
        out = {"type": "time", "time": i.get("time") or "21:00",
               "days": sorted({int(d) for d in i.get("days", ALL_DAYS) if 0 <= int(d) <= 6})}
        # "At sunset" / "30 min before sunrise": follows the sun instead of the clock (`time` is ignored).
        if i.get("sun") in SUN_EVENTS:
            out.update(sun=i["sun"], offset=_minutes(i.get("offset")))
        return out
    if kind == "event":
        return {"type": "event", "entity": i.get("entity") or "", "event": i.get("event") or ""}
    # "for": {"amount", "unit"} on a state or value part = it has to stay that way that long first.
    # An amount of 0 is kept so errors() can say so, rather than quietly meaning "no wait".
    held = {"for": i["for"]} if isinstance(i.get("for"), dict) and i["for"].get("amount") not in (None, "") else {}
    if kind == "property":
        return {"type": "property", "entities": list(i.get("entities") or []), "property": i.get("property") or "",
                "op": i.get("op") or "changes", "value": i.get("value"), **held}
    return {"type": "state", "entities": list(i.get("entities") or []), "to": i.get("to") or "any", **held}


def hold_for(item: dict[str, Any]) -> float:
    """Seconds a state / value part must keep holding before it counts (0 = straight away)."""
    return hold_seconds({"for": item.get("for")}) if item.get("for") else 0.0


def item_holds(item: dict[str, Any], ent: Any) -> bool:
    """Is this device still in the state / value the part waits on?"""
    if item.get("type") == "property":
        return prop_ok(ent, item)
    return state_ok(ent, item.get("to", "any"))


def normalize_trigger(t: dict[str, Any]) -> dict[str, Any]:
    """"When" is a list of `items` - each a time, a device state or a device event - of which `match`
    "any" starts it, or "all" must be true together. An event or a time is a moment: under "all" it
    counts for `within` (default 2 minutes); a state counts for as long as it lasts.

    item: {"type": "time", "time": "21:00", "days": [0..6]}
        | {"type": "state", "entities": [...], "to": state|"any"}
        | {"type": "event", "entity": id, "event": "person"}
    `type` is derived: the items' type when they're all alike, else "mixed". The first item of each
    type is also mirrored into the older flat fields (time/days, entities/to, entity/event)."""
    items = [_item(i) for i in t.get("items") or []]
    if not items:
        items = [_item({**t, "type": t.get("type") or "time"})]
    types = {i["type"] for i in items}
    out: dict[str, Any] = {"type": types.pop() if len(types) == 1 else "mixed", "items": items,
                           "match": t.get("match") if t.get("match") in ("any", "all") else "any",
                           "within": t.get("within") or dict(DEFAULT_WITHIN)}
    states = [i for i in items if i["type"] == "state"]
    out["entities"] = list(dict.fromkeys(e for i in states for e in i["entities"]))
    if states:
        out["to"] = states[0]["to"]
    if times := [i for i in items if i["type"] == "time"]:
        out["time"], out["days"] = times[0]["time"], times[0]["days"]
    if events := [i for i in items if i["type"] == "event"]:
        out["entity"], out["event"] = events[0]["entity"], events[0]["event"]
    return out


def within_seconds(t: dict[str, Any]) -> float:
    return hold_seconds({"for": t.get("within")}) or 120


# -- helpers ---------------------------------------------------------------------------

def fmt_time(hhmm: str) -> str:
    try:
        return datetime.strptime(hhmm, "%H:%M").strftime("%-I:%M %p")
    except ValueError:
        return hhmm


def days_text(days: list[int]) -> str:
    d = sorted(set(days))
    if len(d) == 7:
        return "every day"
    if d == [0, 1, 2, 3, 4]:
        return "on weekdays"
    if d == [5, 6]:
        return "on weekends"
    if not d:
        return "on no days"
    return "on " + join_names([DAY_NAMES[i] + "s" for i in d])


def watched_entities(auto: dict[str, Any]) -> list[str]:
    """The devices a run is about (what buttons and follow-ups refer to)."""
    t = auto["trigger"]
    if t["type"] in ("state", "mixed"):
        return list(t.get("entities", []))
    if t["type"] == "time":
        return list(auto["condition"].get("entities", []))
    return []


# -- device states in triggers and "Only if" rows ---------------------------------------------

def state_ok(ent: Entity, state: str) -> bool:
    if state == "any":
        return True
    return ent.is_active if state == "active" else ent.state == state


def state_word(ents: list[Entity], state: str) -> str:
    """"unlocked", "on", "open"… for a row's state."""
    if state == "active" or not ents:
        return _active_label(ents)
    return KINDS[ents[0].kind].state_label(state).lower()


def row_met(row: dict[str, Any], hub: Hub) -> bool:
    ents = _ents(hub, row.get("entities", []))
    match = row.get("match", "any")
    if match == "always" or not ents:
        return True
    hits = [e for e in ents if row_hit(row, e)]
    if match == "none":
        return not hits
    if match == "all":
        return len(hits) == len(ents)
    return bool(hits)


def prop_ok(ent: Entity, spec: dict[str, Any]) -> bool:
    """A device's reported value compared the way an item or row asks ("humidity above 60")."""
    return property_matches(find_property(ent, spec.get("property", "")), spec.get("op", ""), spec.get("value"))


def item_true(item: dict[str, Any], hub: Hub) -> bool:
    ents = _ents(hub, item.get("entities", []))
    if item.get("type") == "property":
        return bool(ents) and all(prop_ok(e, item) for e in ents)
    return bool(ents) and all(state_ok(e, item.get("to", "any")) for e in ents)


def row_hit(row: dict[str, Any], ent: Entity) -> bool:
    return prop_ok(ent, row) if row.get("property") else state_ok(ent, row.get("state", "active"))


def property_item_hit(auto: dict[str, Any], old: Entity, new: Entity) -> list[int]:
    """Indexes of the value items a device's new report just made true (crossing, not staying)."""
    hits = []
    for n, i in enumerate(auto["trigger"]["items"]):
        if i["type"] != "property" or new.id not in i["entities"]:
            continue
        before, after = find_property(old, i["property"]), find_property(new, i["property"])
        if not before or not after or before["value"] is None or after["value"] is None:
            continue  # a first report (e.g. after a restart) isn't a change
        if i["op"] == "changes":
            if before["value"] != after["value"]:
                hits.append(n)
        elif property_matches(after, i["op"], i["value"]) and not property_matches(before, i["op"], i["value"]):
            hits.append(n)
    return hits


def _prop_info(ents: list[Entity], prop_id: str) -> dict[str, Any]:
    return (find_property(ents[0], prop_id) if ents else None) or \
        {"label": humanize(prop_id or "value"), "type": "text", "unit": "", "options": []}


def prop_text(spec: dict[str, Any], hub: Hub, *, joiner: str, moment: bool, negate: bool = False,
              held: str = "") -> str:
    """"Main Floor's humidity goes above 60%" (moment) / "… is above 60%"."""
    ents = _ents(hub, spec.get("entities", []))
    if not ents:
        return ""
    p = _prop_info(ents, spec.get("property", ""))
    label = p["label"][:1].lower() + p["label"][1:]
    subj = f"{ents[0].name}'s {label}" if len(ents) == 1 else f"the {label} at {join_names([e.name for e in ents], joiner)}"
    op, val = spec.get("op", "changes"), format_value(p, spec.get("value"))
    if op == "changes":
        return f"{subj} changes"
    if held:
        return {"above": f"{subj} stays above {val}", "below": f"{subj} stays below {val}",
                "is": f"{subj} stays {val}", "is_not": f"{subj} stays other than {val}"}.get(op, subj) + f" for {held}"
    if moment:
        return {"above": f"{subj} goes above {val}", "below": f"{subj} goes below {val}",
                "is": f"{subj} becomes {val}", "is_not": f"{subj} is no longer {val}"}.get(op, f"{subj} changes")
    if negate:
        return {"above": f"{subj} isn't above {val}", "below": f"{subj} isn't below {val}",
                "is": f"{subj} isn't {val}", "is_not": f"{subj} is {val}"}.get(op, "")
    return {"above": f"{subj} is above {val}", "below": f"{subj} is below {val}",
            "is": f"{subj} is {val}", "is_not": f"{subj} isn't {val}"}.get(op, "")


def value_errors(spec: dict[str, Any], hub: Hub, *, in_when: bool) -> list[str]:
    ents = _ents(hub, spec.get("entities", []))
    if not ents:
        return ["Pick a device for each value you compare"]
    if not spec.get("property"):
        return [f"Pick which of {ents[0].name}'s values to use"]
    out = []
    for e in ents:
        if not find_property(e, spec["property"]):
            out.append(f"{e.name} doesn't report “{humanize(spec['property']).lower()}”")
    p = _prop_info(ents, spec["property"])
    op = spec.get("op", "changes")
    if op not in OPS.get(p["type"], ()):
        out.append(f"Pick how to compare {p['label'].lower()}")
    elif op == "changes" and not in_when:
        out.append("“Changes” only works under When - pick a comparison")
    elif op != "changes" and spec.get("value") in (None, ""):
        out.append(f"Pick the {p['label'].lower()} to compare with")
    return out


def state_item_hit(auto: dict[str, Any], ent: Entity) -> list[int]:
    """Indexes of the state items a device just reached (it settled on a new state)."""
    return [n for n, i in enumerate(auto["trigger"]["items"])
            if i["type"] == "state" and ent.id in i["entities"] and state_ok(ent, i["to"])]


def event_item_hit(auto: dict[str, Any], entity_id: str, event: str) -> list[int]:
    return [n for n, i in enumerate(auto["trigger"]["items"])
            if i["type"] == "event" and i["entity"] == entity_id and i["event"] == event]


def item_hhmm(item: dict[str, Any], day: date | None = None) -> str | None:
    """The clock time a time part fires at on a day - for a sun part, that day's sunrise/sunset ±
    offset (None when the home's location isn't known)."""
    if item.get("sun"):
        return sun.item_hhmm(item["sun"], item.get("offset") or 0, day or date.today())
    return item["time"]


def time_item_hit(auto: dict[str, Any], hhmm: str, weekday: int, day: date | None = None) -> list[int]:
    return [n for n, i in enumerate(auto["trigger"]["items"])
            if i["type"] == "time" and weekday in i["days"] and item_hhmm(i, day) == hhmm]


def all_true(auto: dict[str, Any], hub: Hub, moments: dict[int, float], now: float) -> bool:
    """Under "all": states must hold right now; events/times must have happened within the window."""
    t = auto["trigger"]
    window = within_seconds(t)
    for n, i in enumerate(t["items"]):
        if i["type"] in ("state", "property"):
            if not item_true(i, hub):
                return False
        elif now - moments.get(n, 0) > window:
            return False
    return True


def trigger_fires(auto: dict[str, Any], hub: Hub, ent: Entity) -> bool:
    """A device settled on a new state: does a trigger made only of device states fire?"""
    if not state_item_hit(auto, ent):
        return False
    return auto["trigger"].get("match") != "all" or all_true(auto, hub, {}, time.time())


def pending_from(auto: dict[str, Any], hub: Hub) -> list[str]:
    """For a scheduled check: the devices that are in the state its rows look for (what buttons act on)."""
    out = []
    for row in auto["condition"]["devices"]:
        if row.get("match") == "none":
            continue
        out += [e.id for e in _ents(hub, row["entities"]) if row_hit(row, e)]
    return list(dict.fromkeys(out))


def _ents(hub: Hub, ids: list[str]) -> list[Entity]:
    return [e for e in (hub.get(i) for i in ids) if e]


def _active_label(ents: list[Entity]) -> str:
    kinds = {e.kind for e in ents}
    if len(kinds) == 1:
        k = KINDS[kinds.pop()]
        return k.state_label(k.active_state).lower() if k.active_state else "on"
    return "on"


def _is_are(n: int) -> str:
    return "is" if n == 1 else "are"


def people_names(people: list[dict[str, Any]], ids: list[str]) -> list[str]:
    by_id = {p["id"]: p["name"] for p in people}
    return [by_id[i] for i in ids if i in by_id]


# -- buttons ---------------------------------------------------------------------------

def button_spec(hub: Hub, button_id: str, pending: list[str]) -> dict[str, Any] | None:
    if button_id == "dismiss":
        return {"id": "dismiss", "label": "Dismiss", "icon": "x", "sensitive": False}
    if button_id == "open":
        return {"id": "open", "label": "Open Newman", "icon": "home", "sensitive": False}
    if button_id.startswith("snooze:"):
        mins = int(button_id.split(":", 1)[1] or 60)
        label = "Snooze 1 hour" if mins == 60 else f"Snooze {mins} min" if mins < 60 else f"Snooze {mins // 60} hours"
        return {"id": button_id, "label": label, "icon": "clock", "sensitive": False}
    if ":" in button_id:
        cmd_id, eid = button_id.split(":", 1)
        ent = hub.get(eid)
        cmd = KINDS[ent.kind].command(cmd_id) if ent else None
        if not cmd or cmd.takes_value:
            return None
        return {"id": button_id, "label": f"{cmd.label} {ent.name}", "icon": cmd.icon, "sensitive": cmd.sensitive}
    if button_id.endswith("_all"):
        cmd_id = button_id[:-4]
        for ent in _ents(hub, pending):
            if (cmd := KINDS[ent.kind].command(cmd_id)) and not cmd.takes_value:
                return {"id": button_id, "label": f"{cmd.label} all", "icon": cmd.icon, "sensitive": cmd.sensitive}
    return None


def buttons_for(auto: dict[str, Any], hub: Hub, pending: list[str] | None) -> list[dict[str, Any]]:
    """Buttons a run shows. `pending` None = assume every watched device is pending (preview)."""
    notify = auto["notify"]
    watched = watched_entities(auto)
    shown_for = watched if pending is None or not notify.get("onlyPending", True) else pending
    out = []
    for bid in notify.get("buttons", []):
        if ":" in bid and not bid.startswith("snooze:"):
            if bid.split(":", 1)[1] not in shown_for:
                continue
        elif bid.endswith("_all") and len(shown_for) < 2:
            continue
        if spec := button_spec(hub, bid, shown_for):
            out.append(spec)
    return out


# -- text ------------------------------------------------------------------------------

def snapshot_source(auto: dict[str, Any], hub: Hub) -> Entity | None:
    """The camera a notification's snapshot would come from - the first event part on a device
    whose kind has a camera (or that shows another device's video)."""
    for i in auto["trigger"]["items"]:
        ent = hub.get(i["entity"]) if i["type"] == "event" else None
        if ent and hub.has_picture(ent):
            return ent
    return None


def wants_snapshot(auto: dict[str, Any], hub: Hub) -> bool:
    return bool(auto["notify"].get("snapshot", True)) and snapshot_source(auto, hub) is not None


def event_phrase(item: dict[str, Any], hub: Hub) -> str:
    ent = hub.get(item.get("entity", ""))
    if not ent:
        return "something happens"
    if not item.get("event"):
        return f"{ent.name} does something"
    return kinds_event_phrase(KINDS[ent.kind], item.get("event", ""), ent.name)


SUN_WORDS = {"sunrise": "sunrise", "sunset": "sunset", "dawn": "dawn (first light)", "dusk": "dusk (last light)"}


def offset_text(minutes: int) -> str:
    """45 -> "45 min", 60 -> "1 hour", 90 -> "1 h 30 min"."""
    m = abs(int(minutes))
    if m < 60:
        return f"{m} min"
    h, rest = divmod(m, 60)
    return f"{h} hour{'s' if h > 1 else ''}" if not rest else f"{h} h {rest} min"


def sun_moment(event: str, offset: int) -> str:
    """"sunset", "30 min before sunrise", "1 hour after sunset"."""
    word = SUN_WORDS.get(event, event)
    if not offset:
        return word
    return f"{offset_text(offset)} {'before' if offset < 0 else 'after'} {word}"


def time_words(item: dict[str, Any]) -> str:
    return sun_moment(item["sun"], item.get("offset") or 0) if item.get("sun") else fmt_time(item["time"])


def sun_condition_text(s: dict[str, Any] | None) -> str:
    """"between sunset and sunrise", "during daylight", "between 30 min after sunset and sunrise"."""
    s = clean_sun(s)
    if not s:
        return ""
    if s["only"] == "light":
        if not s["sunrise"] and not s["sunset"]:
            return "during daylight"
        return f"between {sun_moment('sunrise', s['sunrise'])} and {sun_moment('sunset', s['sunset'])}"
    return f"between {sun_moment('sunset', s['sunset'])} and {sun_moment('sunrise', s['sunrise'])}"


def item_phrase(item: dict[str, Any], hub: Hub, all_of: bool) -> str:
    if item["type"] == "time":
        days = "" if len(item["days"]) == 7 else " " + days_text(item["days"])
        return f"it's {time_words(item)}{days}"
    if item["type"] == "event":
        return event_phrase(item, hub)
    held = duration_text({"for": item["for"]}) if item.get("for") else ""
    if item["type"] == "property":
        return prop_text(item, hub, joiner="and" if all_of else "or", moment=not all_of, held=held)
    ents = _ents(hub, item["entities"])
    if not ents:
        return ""
    names = join_names([e.name for e in ents], "and" if all_of else "or")
    if item["to"] == "any":
        return f"{names} changes"
    if held:
        return f"{names} {'stay' if len(ents) > 1 else 'stays'} {state_word(ents, item['to'])} for {held}"
    if all_of:
        return f"{names} {_is_are(len(ents))} {state_word(ents, item['to'])}"
    return f"{names} becomes {state_word(ents, item['to'])}"


def trigger_text(auto: dict[str, Any], hub: Hub) -> str:
    t = auto["trigger"]
    items = t["items"]
    if len(items) == 1 and items[0]["type"] == "time":
        return f"At {time_words(items[0])} {days_text(items[0]['days'])}"
    all_of = t.get("match") == "all"
    parts = [p for p in (item_phrase(i, hub, all_of) for i in items) if p]
    if not parts:
        return "When a device changes" if t["type"] == "state" else "When something happens"
    text = "When " + (join_names(parts) if all_of else join_names(parts, "or"))
    if all_of and sum(1 for i in items if i["type"] in ("event", "time")) >= 2:
        text += f" (within {duration_text({'for': t.get('within')})} of each other)"
    return text


def in_window(between: dict[str, str] | None, now: datetime | None = None) -> bool:
    if not between or not between.get("after") or not between.get("before"):
        return True
    t = (now or datetime.now()).strftime("%H:%M")
    after, before = between["after"], between["before"]
    return after <= t < before if after <= before else (t >= after or t < before)


def on_day(days: list[int] | None, now: datetime | None = None) -> bool:
    return days is None or (now or datetime.now()).weekday() in days


def limited_days(auto: dict[str, Any]) -> list[int] | None:
    """The "Only on these days" check - None when every day is fine (or the time trigger already picks days)."""
    days = auto["condition"].get("days")
    if days is None or auto["trigger"]["type"] == "time" or len(set(days)) == 7:
        return None
    return sorted({int(x) for x in days})


def window_text(auto: dict[str, Any]) -> str:
    parts = []
    if (days := limited_days(auto)) is not None:
        parts.append(days_text(days))
    b = auto["condition"].get("between")
    if b and b.get("after") and b.get("before"):
        parts.append(f"between {fmt_time(b['after'])} and {fmt_time(b['before'])}")
    text = "only " + " ".join(parts) if parts else ""
    if s := sun_condition_text(auto["condition"].get("sun")):
        text = f"{text} and {s}" if text else f"only {s}"
    if dt := dates_text(auto["condition"].get("dates")):
        text = f"{text} and {dt}" if text else f"only {dt}"
    return text


def sun_allows(auto: dict[str, Any], now: datetime | None = None) -> bool:
    """The "only when it's dark / during daylight" check."""
    return sun.sun_ok(auto["condition"].get("sun"), now)


def action_targets(a: dict[str, Any]) -> list[str]:
    return list(a.get("entities") or ([a["entity"]] if a.get("entity") else []))


def expand_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One action per device ("turn on Kitchen and Dining Room" = two)."""
    out = []
    for a in actions:
        for eid in action_targets(a):
            one = {k: v for k, v in a.items() if k != "entities"}
            one["entity"] = eid
            out.append(one)
    return out


def action_text(a: dict[str, Any], hub: Hub) -> str:
    ents = _ents(hub, action_targets(a))
    cmd = KINDS[ents[0].kind].command(a.get("command", "")) if ents else None
    if not ents or not cmd:
        return "do something to a missing device"
    names = join_names([e.name for e in ents])
    if cmd.id == "set_brightness":
        text = f"set {names} to {a.get('value')}%"
    elif cmd.value_type == "choices":
        # Picked from the device's own list (e.g. a vacuum's rooms): name them, not their ids.
        by_id = {str(o.get("id")): o.get("name") for e in ents for o in (e.attrs.get(cmd.attr) or [])}
        picked = [by_id.get(str(v)) or str(v) for v in (a.get("value") or [])]
        text = f"{cmd.label.lower()} {join_names(picked)} on {names}" if picked else f"{cmd.label.lower()} on {names}"
    elif cmd.takes_value:
        text = f"{cmd.label.lower()} {names} to {a.get('value')}"
    else:
        text = f"{cmd.label.lower()} {names}"
    if hold_seconds(a):
        text += f" for {duration_text(a)} (then put {'it' if len(ents) == 1 else 'them'} back)"
    return text


UNITS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}


def hold_seconds(a: dict[str, Any]) -> float:
    """How long an action's effect should last before Newman puts the device back (0 = forever).
    Actions carry {"for": {"amount": 2, "unit": "hours"}}; older ones used "forMinutes"."""
    f = a.get("for") or {}
    if f.get("amount"):
        return float(f["amount"]) * UNITS.get(f.get("unit", "minutes"), 60)
    return float(a.get("forMinutes") or 0) * 60


def duration_text(a: dict[str, Any]) -> str:
    f = a.get("for") or {}
    amount, unit = (float(f["amount"]), f.get("unit", "minutes")) if f.get("amount") else \
        (float(a.get("forMinutes") or 0), "minutes")
    word = unit[:-1] if amount == 1 else unit
    return f"{amount:g} {word}"


def guard_rows(auto: dict[str, Any]) -> list[dict[str, Any]]:
    """The "Only if … is …" rows that actually restrict a run. Older device-change automations
    repeated the trigger's own devices here with "active"; those rows aren't a real check."""
    trig = set(auto["trigger"].get("entities", [])) if auto["trigger"]["type"] == "state" else set()
    return [r for r in auto["condition"]["devices"] if r["match"] != "always" and r["entities"]
            and not (trig and r["state"] == "active" and set(r["entities"]) <= trig)]


def guard_entities(auto: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(e for r in guard_rows(auto) for e in r["entities"]))


def row_text(row: dict[str, Any], hub: Hub) -> str:
    ents = _ents(hub, row["entities"])
    if not ents:
        return ""
    if row.get("property"):
        match = row["match"]
        if match == "none" and len(ents) > 1:
            return "none of " + prop_text(row, hub, joiner="or", moment=False)[len("the "):]
        return prop_text(row, hub, joiner="and" if match == "all" else "or", moment=False, negate=match == "none")
    label, names, match = state_word(ents, row["state"]), [e.name for e in ents], row["match"]
    if match == "none":
        return f"{names[0]} isn't {label}" if len(ents) == 1 else f"none of {join_names(names, 'or')} is {label}"
    if len(ents) == 1:
        return f"{names[0]} is {label}"
    if match == "all":
        return f"{join_names(names)} are all {label}"
    return f"any of {join_names(names, 'or')} is {label}"


def condition_text(auto: dict[str, Any], hub: Hub) -> str:
    window = window_text(auto)
    texts = [x for x in (row_text(r, hub) for r in guard_rows(auto)) if x]
    text = "if " + join_names(texts) if texts else ""
    return f"{text}, {window}" if text and window else text or window


def conditions_met(auto: dict[str, Any], hub: Hub) -> bool:
    return all(row_met(r, hub) for r in guard_rows(auto))

def _command_past(auto: dict[str, Any]) -> str:
    for bid in auto["notify"].get("buttons", []):
        cmd_id = bid.split(":", 1)[0] if ":" in bid else bid[:-4] if bid.endswith("_all") else None
        for k in KINDS.values():
            if cmd_id and (cmd := k.command(cmd_id)):
                return cmd.past
    if auto["autoCommand"].get("enabled"):
        for k in KINDS.values():
            if cmd := k.command(auto["autoCommand"].get("command", "")):
                return cmd.past
    return "handled"


WALL_STYLES = {"card": "", "full": " full screen", "alarm": " as an alarm"}


def wall_text(auto: dict[str, Any], tablets: dict[str, str] | None) -> str:
    """"show it on Hallway tablet full screen" ("" when the automation doesn't use wall tablets)."""
    w = auto["wall"]
    if not w.get("enabled"):
        return ""
    names = [(tablets or {}).get(t, "a removed tablet") for t in w["tablets"]]
    where = join_names(names) if names else "every wall tablet"
    return f"show it on {where}{WALL_STYLES.get(w.get('style'), '')}"


# -- "Show a live status" ---------------------------------------------------------------------------

LIVE_UNTIL = ("leaves", "duration", "trigger")


def clean_live(lv: Any) -> dict[str, Any]:
    """The live-status block, every field there. `until` is one of
      {"type": "leaves", "entities": [...], "state": id | "active"} - until none of those devices (none
          listed = the live status's own) is in that state any more ("active" = each one's active state);
      {"type": "duration", "amount": 30, "unit": "minutes"} - for that long;
      {"type": "trigger", "item": <a When part: time / state / event / value>} - until that happens."""
    lv = lv if isinstance(lv, dict) else {}
    r = lv.get("recipients")
    out: dict[str, Any] = {
        "enabled": bool(lv.get("enabled")),
        "entities": list(dict.fromkeys(str(e) for e in (lv.get("entities") or []) if e)),
        "title": str(lv.get("title") or "").strip()[:80],
        "recipients": [str(x) for x in r if x] if isinstance(r, list) else None,
    }
    u = lv.get("until") if isinstance(lv.get("until"), dict) else {}
    kind = u.get("type") if u.get("type") in LIVE_UNTIL else "leaves"
    if kind == "duration":
        out["until"] = {"type": kind, "amount": u.get("amount"), "unit": u.get("unit") or "minutes"}
    elif kind == "trigger":
        item = u.get("item") if isinstance(u.get("item"), dict) else {"type": "state"}
        out["until"] = {"type": kind, "item": _item(item)}
    else:
        out["until"] = {"type": kind, "entities": list(dict.fromkeys(str(e) for e in (u.get("entities") or []) if e)),
                        "state": str(u.get("state") or "active")}
    return out


def live_watch(lv: dict[str, Any]) -> list[str]:
    """The devices an "until they're no longer …" waits on (its own, or the live status's devices)."""
    u = lv["until"]
    return list(u.get("entities") or lv["entities"]) if u["type"] == "leaves" else []


def live_entities(auto: dict[str, Any]) -> list[str]:
    """Every device an enabled live status names (shown, waited on, or ending it)."""
    lv = auto.get("live") or {}
    if not lv.get("enabled"):
        return []
    lv = clean_live(lv)
    u = lv["until"]
    ids = list(lv["entities"]) + live_watch(lv)
    if u["type"] == "trigger":
        i = u["item"]
        ids += list(i.get("entities") or []) + ([i["entity"]] if i.get("entity") else [])
    return list(dict.fromkeys(ids))


def live_until_text(lv: dict[str, Any], hub: Hub) -> str:
    u = lv["until"]
    if u["type"] == "duration":
        try:
            return f"for {duration_text({'for': {'amount': float(u['amount']), 'unit': u['unit']}})}"
        except (TypeError, ValueError):
            return "for a while"
    if u["type"] == "trigger":
        phrase = item_phrase(u["item"], hub, False)
        return f"until {phrase}" if phrase else "until something happens"
    ents = _ents(hub, live_watch(lv))
    if not ents:
        return "until it's over"
    word = state_word(ents, u["state"])
    if len(ents) == 1:
        return f"until {ents[0].name} is no longer {word}"
    return f"until none of {join_names([e.name for e in ents], 'or')} is {word}"


def live_text(auto: dict[str, Any], hub: Hub, people: list[dict[str, Any]]) -> str:
    """"show a live status of Garage Door to everyone until Garage Door is no longer open"."""
    lv = clean_live(auto.get("live"))
    names = join_names([e.name for e in _ents(hub, lv["entities"])]) or "no devices yet"
    who = "everyone" if lv["recipients"] is None else join_names(people_names(people, lv["recipients"])) or "nobody yet"
    title = f" called “{lv['title']}”" if lv["title"] else ""
    return f"show a live status{title} of {names} to {who} {live_until_text(lv, hub)}"


def live_errors(auto: dict[str, Any], hub: Hub) -> list[str]:
    lv = clean_live(auto.get("live"))
    if not lv["enabled"]:
        return []
    out = []
    if not lv["entities"]:
        out.append("Pick the devices the live status shows")
    if any(not hub.get(e) for e in lv["entities"]):
        out.append("The live status shows a device that no longer exists")
    if lv["recipients"] is not None and not lv["recipients"]:
        out.append("Pick who sees the live status")
    u = lv["until"]
    if u["type"] == "duration":
        try:
            ok = float(u["amount"]) > 0 and u["unit"] in UNITS
        except (TypeError, ValueError):
            ok = False
        if not ok:
            out.append("“For how long” needs a positive amount in seconds, minutes, hours or days")
    elif u["type"] == "trigger":
        i = u["item"]
        if i["type"] == "time" and not i["days"]:
            out.append("Pick at least one day for the time that ends the live status")
        elif i["type"] == "state" and not _ents(hub, i["entities"]):
            out.append("Pick the device whose change ends the live status")
        elif i["type"] == "event" and (not hub.get(i["entity"]) or not i["event"]):
            out.append("Pick the device and what it does to end the live status")
        elif i["type"] == "property":
            out += value_errors(i, hub, in_when=True)
    else:
        if any(not hub.get(e) for e in u["entities"]):
            out.append("The live status waits on a device that no longer exists")
        if u["state"] != "active":
            for e in _ents(hub, live_watch(lv)):
                if u["state"] not in dict(KINDS[e.kind].states):
                    out.append(f"{e.name} is never “{u['state']}”")
    return out


def summary(auto: dict[str, Any], hub: Hub, people: list[dict[str, Any]],
            tablets: dict[str, str] | None = None) -> str:
    auto = normalize(auto)
    parts = [trigger_text(auto, hub)]
    if cond := condition_text(auto, hub):
        parts.append(cond)
    actions = [action_text(a, hub) for a in auto.get("actions", [])]
    ac = auto["autoCommand"]
    if ac.get("enabled"):
        actions.append(f"{ac.get('command', 'lock').replace('_', ' ')} them automatically")
    n = auto["notify"]
    who = people_names(people, auto["recipients"])
    if n.get("enabled"):
        text = f"notify {join_names(who) or 'nobody yet'}"
        if auto["trigger"]["type"] == "time" and watched_entities(auto) and not guard_rows(auto):
            # An "Always" check: the notification just says how those devices are.
            text += f" how {join_names([e.name for e in _ents(hub, watched_entities(auto))])} {_is_are(len(watched_entities(auto)))}"
        if wants_snapshot(auto, hub):
            text += f" with a snapshot from {snapshot_source(auto, hub).name}"
        labels = [b["label"] for b in buttons_for(auto, hub, None)]
        if labels:
            text += f" with {join_names(labels)} buttons"
        actions.append(text)
    if shown := wall_text(auto, tablets):
        actions.append(shown)
    if auto["live"].get("enabled"):
        actions.append(live_text(auto, hub, people))
    parts.append(join_names(actions) if actions else "do nothing yet")
    sentence = ", ".join(parts) + "."
    f = auto["followUp"]
    if f.get("enabled") and watched_entities(auto):
        past = _command_past(auto)
        sentence += f" Once they're {past}, tell {'everyone' if len(who) > 1 else join_names(who) or 'them'} who did it."
        if f.get("remindMinutes"):
            sentence += f" If not, remind again every {f['remindMinutes']} minutes."
    return sentence[0].upper() + sentence[1:]


def errors(auto: dict[str, Any], hub: Hub) -> list[str]:
    auto = normalize(auto)
    out = []
    if auto["wall"].get("enabled") and auto["wall"].get("style") not in WALL_STYLES:
        out.append("Choose how it shows on the wall tablet: a card, full screen or an alarm")
    t = auto["trigger"]
    # No devices under "Only if" simply means it runs every time (e.g. "At 9 PM, lock Laundry").
    uses_sun = bool(auto["condition"].get("sun")) or any(i.get("sun") for i in t["items"])
    if uses_sun and not sun.location():
        out.append("Sunrise and sunset need your home's location")
    if any(abs(i.get("offset") or 0) > MAX_SUN_OFFSET for i in t["items"]) or (
            (s := auto["condition"].get("sun")) and max(abs(s["sunset"]), abs(s["sunrise"])) > MAX_SUN_OFFSET):
        out.append("Keep it within 4 hours of sunrise or sunset")
    for i in t["items"]:
        if i["type"] == "time" and not i["days"]:
            out.append("Pick at least one day")
        elif i["type"] == "state" and not i["entities"]:
            out.append("Pick at least one device to watch")
        elif i["type"] == "state" and t["match"] == "all" and i["to"] == "any" and len(t["items"]) > 1:
            out.append("When all of them must be true, pick the state for each device")
        elif i["type"] == "event" and not hub.get(i["entity"]):
            out.append("Pick the device whose event starts it")
        elif i["type"] == "event" and not i["event"]:
            out.append(f"Pick what {hub.get(i['entity']).name} has to do")
        elif i["type"] == "property":
            out += value_errors(i, hub, in_when=True)
            if i.get("for") and i["op"] == "changes":
                out.append("“For how long” needs a comparison (like “below 5 W”), not “changes”")
        if i["type"] in ("state", "property") and i.get("for"):
            f = i["for"]
            if f.get("unit", "minutes") not in UNITS or float(f.get("amount") or 0) <= 0:
                out.append("“For how long” needs a positive amount in seconds, minutes, hours or days")
            if i["type"] == "state" and i["to"] == "any":
                out.append("“For how long” needs a state to wait for (like “unlocked”), not “changes”")
            if t["match"] == "all" and len(t["items"]) > 1 and i["op"] == "changes":
                out.append("When all of them must be true, pick a comparison instead of “changes”")
    w = t.get("within") or {}
    if t["match"] == "all" and len(t["items"]) > 1 and \
            (w.get("unit", "minutes") not in UNITS or float(w.get("amount") or 0) <= 0):
        out.append("“Within” needs a positive amount of time")
    if any(not r["entities"] for r in auto["condition"]["devices"]):
        out.append("Pick a device for each “Only if” row")
    for r in auto["condition"]["devices"]:
        if r.get("property") and r["entities"]:
            out += value_errors(r, hub, in_when=False)
    if not auto["notify"].get("enabled") and not auto["autoCommand"].get("enabled") and not auto.get("actions") \
            and not auto["wall"].get("enabled") and not auto["live"].get("enabled"):
        out.append("Choose what should happen")
    out += live_errors(auto, hub)
    for a in auto.get("actions", []):
        if not action_targets(a):
            out.append("Pick a device for each action")
        for eid in action_targets(a):
            ent = hub.get(eid)
            cmd = KINDS[ent.kind].command(a.get("command", "")) if ent else None
            if not ent:
                out.append("An action refers to a device that no longer exists")
            elif not cmd:
                out.append(f"{ent.name} can't {str(a.get('command', '')).replace('_', ' ')}")
            elif cmd.takes_value and a.get("value") in (None, ""):
                out.append(f"Choose a value for “{cmd.label}” on {ent.name}")
        f = a.get("for") or {}
        if f and (f.get("unit", "minutes") not in UNITS or float(f.get("amount") or 0) < 0):
            out.append("“For how long” needs a positive amount in seconds, minutes, hours or days")
    b = auto["condition"].get("between")
    if b and bool(b.get("after")) != bool(b.get("before")):
        out.append("Set both times for “only between”")
    out += dates_errors(auto["condition"].get("dates"))
    days = auto["condition"].get("days")
    if days is not None and auto["trigger"]["type"] != "time" and not days:
        out.append("Pick at least one day it may run on")
    if auto["notify"].get("enabled") and not auto["recipients"]:
        out.append("Pick who gets the notification")
    for bid in auto["notify"].get("buttons", []):
        if ":" in bid and not bid.startswith("snooze:") and not hub.get(bid.split(":", 1)[1]):
            out.append(f"A button refers to a device that no longer exists ({bid.split(':', 1)[1]})")
    return out


def notification_text(auto: dict[str, Any], hub: Hub, *, pending: list[str], trigger: dict[str, Any] | None,
                      follow_up: bool = False) -> tuple[str, str]:
    """Title + body for a run's notification."""
    t = auto["trigger"]
    title = auto["notify"].get("title") or auto.get("name") or "Newman"
    clock = time.strftime("%-I:%M %p")
    fired = trigger or {}
    # What actually started this run (a mixed "When" can be started by any of its kinds).
    kind = fired.get("type") if fired.get("type") in ("time", "state", "event", "property") else t["type"]
    if t["match"] == "all" and len(t["items"]) > 1 and not follow_up:
        text = trigger_text(auto, hub)[len("When "):]
        return title, f"{text[0].upper()}{text[1:]} · {clock}"
    if kind == "event":
        ent = hub.get(fired.get("entity") or t.get("entity", ""))
        ev = fired.get("event") or t.get("event", "")
        if not ent:
            return title, f"Something happened · {clock}"
        k = KINDS[ent.kind]
        e = k.event(ev)
        # The event's own wording (from its kind); the automation's title always wins.
        ev_title = render(e.title, ent.name, k, e) if e.title else ""
        body = render(e.body or "{device}: " + e.label.lower(), ent.name, k, e, str(fired.get("detail") or ""))
        return auto["notify"].get("title") or ev_title or title, f"{body} · {clock}"
    if kind == "property" and fired.get("entity"):
        ent = hub.get(fired["entity"])
        p = find_property(ent, fired.get("property", "")) if ent else None
        if ent and p:
            return (auto["notify"].get("title") or f"{ent.name}: {p['label'].lower()}",
                    f"{p['label']} is {format_value(p, p['value'])} · {clock}")
    if kind == "state" and fired.get("entity"):
        ent = hub.get(trigger["entity"])
        if ent:
            label = KINDS[ent.kind].state_label(trigger.get("to")).lower()
            # "Unlocked by Marlina", else how the device says it happened: "Locked manually".
            by = f" by {trigger['by']}" if trigger.get("by") else (f" {trigger['how']}" if trigger.get("how") else "")
            return auto["notify"].get("title") or f"{ent.name} {label}", f"{label.capitalize()}{by} at {clock}"
    if not pending:
        # Nothing is pending (an "Always" check, or a test run): say how things actually are.
        watched = _ents(hub, watched_entities(auto))
        if not watched:
            return title, f"Scheduled check · {clock}"
        groups: dict[str, list[str]] = {}
        for e in watched:
            groups.setdefault(KINDS[e.kind].state_label(e.state).lower(), []).append(e.name)
        return title, " · ".join(f"{join_names(n)} {_is_are(len(n))} {label}" for label, n in groups.items()) + "."
    ents = _ents(hub, pending)
    names = join_names([e.name for e in ents])
    ids = {e.id for e in ents}
    row = next((r for r in auto["condition"]["devices"] if ids & set(r["entities"]) and r["match"] != "none"), None)
    if row and row.get("property"):
        # A value check ("battery below 20%"): say what each device actually reads.
        p = _prop_info(ents, row["property"])
        cmp = prop_text({**row, "entities": [ents[0].id]}, hub, joiner="and", moment=False)
        cmp = cmp[len(f"{ents[0].name}'s "):] if cmp.startswith(f"{ents[0].name}'s ") else cmp
        readings = join_names([f"{e.name} ({format_value(p, (find_property(e, row['property']) or {}).get('value'))})"
                               for e in ents])
        return (f"Reminder · {title}" if follow_up else title, f"{cmp[:1].upper()}{cmp[1:]}: {readings}.")
    return (f"Reminder · {title}" if follow_up else title,
            f"{names} {_is_are(len(ents))} still {pending_word(auto, hub, ents)}.")


def pending_word(auto: dict[str, Any], hub: Hub, ents: list[Entity]) -> str:
    ids = {e.id for e in ents}
    for row in auto["condition"]["devices"]:
        if ids & set(row["entities"]) and row["match"] != "none":
            return state_word(ents, row["state"])
    return _active_label(ents)


def follow_up_message(auto: dict[str, Any], hub: Hub, *, pending: list[str], resolvers: list[str | None]) -> str:
    names = [r for r in dict.fromkeys(resolvers) if r]
    if not names:
        person = "Someone at the door"
    elif None in resolvers:
        person = join_names(names + ["someone at the door"])
    else:
        person = join_names(names)
    ents = _ents(hub, pending)
    action = _command_past(auto)
    if ents and action == "handled":
        k = KINDS[ents[0].kind]
        action = k.state_label(ents[0].state).lower()
    msg = auto["followUp"].get("message") or DEFAULT_FOLLOWUP
    text = (msg.replace("{person}", person).replace("{devices}", join_names([e.name for e in ents]))
            .replace("{action}", action))
    return text[0].upper() + text[1:] if text else text


def preview(auto: dict[str, Any], hub: Hub, people: list[dict[str, Any]]) -> dict[str, Any]:
    auto = normalize(auto)
    watched = watched_entities(auto)
    trig = None
    if auto["trigger"]["type"] == "state" and watched:
        ent = hub.get(watched[0])
        to = auto["trigger"].get("to", "any")
        if ent:
            k = KINDS[ent.kind]
            trig = {"entity": ent.id, "to": k.active_state if to == "any" else to}
    title, body = notification_text(auto, hub, pending=watched, trigger=trig)
    who = people_names(people, auto["recipients"])
    fu = None
    if auto["followUp"].get("enabled") and watched:
        fu = follow_up_message(auto, hub, pending=watched, resolvers=[who[0] if who else "Chris"])
    return {"title": title, "body": body, "timeSensitive": bool(auto["notify"].get("timeSensitive")),
            "snapshot": wants_snapshot(auto, hub),
            # Which camera a snapshot can come from (null = this trigger has none, so hide the option).
            "snapshotSource": (src.name if (src := snapshot_source(auto, hub)) else None),
            "buttons": buttons_for(auto, hub, None) if auto["notify"].get("enabled") else [],
            "followUp": fu}
