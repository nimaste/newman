"""Dashboards: each person's pages of tiles, for the wall tablet and the browser. (The phone apps
use the same tiles on their own 4-column layouts - see phone_layouts.py.)

A dashboard is {"name", "theme", "christmas", "tabNames", "grid", "pages": [{"id", "name", "icon", "tiles": [tile]}]}
and a tile is {"id", "type", "x", "y", "w", "h", "config"} on a GRID_COLS x GRID_ROWS grid. Tile types are
listed in TILE_TYPES with the settings each takes, so the editor builds its forms from this list
and every client draws a tile by its type - nothing here or there knows about device kinds; tiles
pick devices by what they can do (a camera picture, a number you can set, a secure state…).
"""
from __future__ import annotations

import re
import secrets
from datetime import date
from typing import Any

from .automations.describe import clean_dates, dates_errors, dates_ok
from .categories import screen_of
from .kinds import KINDS, properties_for

GRID_COLS, GRID_ROWS = 24, 16
GRID = [GRID_COLS, GRID_ROWS]
# Dashboards saved before the grid was made finer (12 x 8 - they have no "grid") are drawn at twice the
# size each way: the same layout, now with half-steps to size and place tiles by (upgrade()).
OLD_SCALE = 2
# The wall's look (web/src/wall/theme.css): "midnight" (deep blue, the original look - dashboards saved
# as "night-glass" are this one), "glass" (light frosted panels) or "christmas" (winter blue, snow).
THEMES = ("midnight", "glass", "christmas")
OLD_THEMES = {"night-glass": "midnight"}
# Christmas: what it shows, and (season) the dates every year it replaces the chosen theme by itself.
# characters: "builtin" (Newman's own drawings) | "pictures" (up to MAX_PICTURES the household uploads)
# | "off"; place: "clock" (in the top bar, or on the dimmed screen when the bar is full) | "background"
# (big and soft behind the tiles) | "off".
CHRISTMAS = {"snow": True, "greeting": "Merry Christmas", "characters": "builtin", "place": "clock",
             "pictures": [], "season": None}
SEASON = {"from": "12-01", "to": "01-06"}
CHARACTERS = ("builtin", "pictures", "off")
PLACES = ("clock", "background", "off")
MAX_PICTURES = 3
PICTURE_ID = re.compile(r"^[0-9a-f]{16}$")
# The wall top bar's page tabs: "current" = icons, only the current tab named (the default) ·
# "fit" = every name when they all fit, otherwise the same as "current" (web/src/wall/tabfit.js).
TAB_NAMES = ("current", "fit")

# `ownPage`: the tile needs more than half of a short screen (the wall's compact layout, web/src/wall/
# compact.js) - it gets a swipe page to itself there instead of sharing one.
# Settings field types: "entities"/"entity" (devices matching `filter`), "command" (a command of the
# chosen device, matching `filter`), "property" (one of its reported values), "number", "text", "select",
# "people" (person ids from the household).
TILE_TYPES: list[dict[str, Any]] = [
    {"type": "camera", "name": "Camera", "description": "Live video from your favourite cameras; tap for full screen.",
     "min": [3, 3], "default": [5, 4], "ownPage": True,
     "fields": [{"key": "entities", "label": "Cameras", "type": "entities", "filter": "picture", "required": True}]},
    {"type": "alerts", "name": "Recent alerts", "description": "The latest camera alerts; tap one to play it.",
     "min": [3, 2], "default": [5, 3],
     "fields": [{"key": "entities", "label": "Only these cameras (empty = all)", "type": "entities", "filter": "alerts"},
                {"key": "limit", "label": "How many", "type": "number", "default": 4, "min": 1, "max": 12}]},
    {"type": "dial", "name": "Dial", "description": "A round dial for anything you set to a number - like a thermostat.",
     "min": [3, 3], "default": [4, 4], "ownPage": True,
     "fields": [{"key": "entity", "label": "Device", "type": "entity", "filter": "number-command", "required": True},
                {"key": "command", "label": "What the dial sets", "type": "command", "filter": "number", "required": True},
                {"key": "modeCommand", "label": "Mode buttons", "type": "command", "filter": "state"}]},
    {"type": "devices", "name": "Devices", "description": "Buttons for devices: tap to switch, lock, open…",
     "min": [2, 2], "default": [4, 3], "lists": True,
     "fields": [{"key": "title", "label": "Title", "type": "text"},
                {"key": "entities", "label": "Devices", "type": "entities", "filter": "any", "required": True},
                {"key": "groupBy", "label": "Group by", "type": "select", "default": "none",
                 "options": [{"value": "none", "label": "Nothing"}, {"value": "area", "label": "Room / area"}]}]},
    {"type": "mode", "name": "House mode", "description": "An alarm or other house-mode device with its buttons.",
     "min": [4, 1], "default": [8, 1],
     "fields": [{"key": "entity", "label": "Device", "type": "entity", "filter": "mode", "required": True}]},
    {"type": "values", "name": "Readings", "description": "Numbers and states devices report (temperature, humidity…).",
     "min": [2, 1], "default": [4, 3], "lists": True,
     "fields": [{"key": "title", "label": "Title", "type": "text"},
                {"key": "items", "label": "Readings", "type": "readings", "required": True}]},
    {"type": "attention", "name": "Needs attention", "description": "Devices that are offline, low on battery or tampered.",
     "min": [3, 2], "default": [4, 2], "fields": []},
    {"type": "weather", "name": "Weather", "description": "Conditions now, the next hours and the week ahead, plus alerts.",
     "min": [3, 2], "default": [4, 3],
     "fields": [{"key": "entity", "label": "Place", "type": "entity", "filter": "forecast", "required": True},
                {"key": "show", "label": "Show", "type": "select", "default": "days",
                 "options": [{"value": "days", "label": "The week ahead"}, {"value": "hours", "label": "The next hours"},
                             {"value": "line", "label": "One line (now, high/low, rain)"}]}]},
    {"type": "today", "name": "Today", "description": "What needs doing today (tasks from GCloud Calendar, devices, appliances), today's "
     "events and heads-ups - one list, most urgent first. Tap for the full To do list.",
     "min": [3, 4], "default": [4, 6],
     "fields": [{"key": "limit", "label": "At most this many To do items", "type": "number", "default": 4, "min": 1,
                 "max": 12}]},
    {"type": "greeting", "name": "Greeting", "description": "“Good morning” and today in one line: things to do, "
     "events, rain or a weather warning.", "min": [4, 1], "default": [12, 1], "fields": []},
    {"type": "todo", "name": "To do", "description": "What needs doing - overdue, today, coming up - with Done right "
     "there. Shows as many as fit; See all opens the full To do list.", "min": [3, 3], "default": [5, 7], "fields": []},
    {"type": "calendar", "name": "Calendar", "description": "Upcoming events from your calendars, in their colours.",
     "min": [3, 2], "default": [4, 4],
     "fields": [{"key": "entities", "label": "Calendars (empty = all)", "type": "entities", "filter": "agenda"},
                {"key": "days", "label": "Days ahead", "type": "number", "default": 31, "min": 1, "max": 31}]},
    {"type": "people", "name": "Family map", "description": "Where everyone is: name chips over a map of the home "
     "area; tap someone to refresh where they are and fly to them.", "min": [3, 2], "default": [6, 5],
     "fields": [{"key": "people", "label": "Only these people (empty = everyone)", "type": "people"},
                {"key": "mapLook", "label": "Map look", "type": "select", "default": "light",
                 "options": [{"value": "light", "label": "Light"}, {"value": "dark", "label": "Dark"}]}]},
    {"type": "music", "name": "Music", "description": "Music from Navidrome on this tablet's speaker: what's playing, "
     "a search box and a few recent albums and playlists; More opens the full picker. (Needs the Navidrome "
     "connection.)", "min": [3, 3], "default": [4, 4], "wallOnly": True,  # plays on the tablet's own speaker
     "fields": [{"key": "show", "label": "Show", "type": "select", "default": "albums",
                 "options": [{"value": "albums", "label": "Recent albums"}, {"value": "playlists", "label": "Playlists"}]}]},
    {"type": "recipes", "name": "Recipes", "description": "Your Forkast recipes with a search box; tap one to cook "
     "from it full screen (ingredients, steps, servings). (Needs the Forkast connection.)", "min": [3, 3],
     "default": [4, 4], "wallOnly": True, "fields": []},  # the phone apps don't draw it
    {"type": "climate", "name": "Climate glance", "description": "The thermostat and every room's temperature at a "
     "glance, with a cool-to-warm mark per room. Look only: a tap opens the thermostat's dial, unless When tapped "
     "says otherwise.",
     "min": [3, 4], "default": [5, 8], "wallOnly": True, "lists": True,  # the phone apps don't draw it (yet)
     "fields": [{"key": "entity", "label": "Thermostat", "type": "entity", "filter": "temperature-setting"},
                {"key": "rooms", "label": "Rooms", "type": "entities", "filter": "temperature"},
                {"key": "outside", "label": "Outside", "type": "entity", "filter": "forecast"}]},
]
# Every tile can scale its text (names, values) as a group - so one long name can fit without that
# single line looking different from its neighbours.
TEXT_SIZE = {"key": "textSize", "label": "Text size", "type": "number", "slider": True, "min": 70, "max": 140,
             "step": 5, "unit": "%", "default": 100}
# Tiles that show a list of things (rooms, devices, readings): as small cards or as rows, or "auto" -
# cards when there's room, rows when the tile is narrow (the wall decides by the tile's width).
LOOKS = ("auto", "tiles", "list")
LOOK = {"key": "look", "label": "Look", "type": "look", "default": "auto",
        "options": [{"value": "auto", "label": "Auto", "help": "Tiles when there's room, a list when it's narrow."},
                    {"value": "tiles", "label": "Tiles", "help": "Each one as a small card."},
                    {"value": "list", "label": "List", "help": "One row each, with its full name."}]}
# What a tap or a hold on a tile does - the same choices on every tile (the tile's own buttons, dial
# and the like keep working): its own thing (taps only: a camera goes full screen, the climate glance
# opens the thermostat…), a page of this dashboard, one of the wall's screens (WALL_SCREENS), an
# automation (run as if by hand - its Only if still applies), a device's command, or nothing.
# Saved as {"do": …} plus "page" | "screen" | "automation" | "entity" and "command".
ACTIONS = [{"value": "own", "label": "Do what this tile does"}, {"value": "page", "label": "Open a page"},
           {"value": "screen", "label": "Open a screen"}, {"value": "automation", "label": "Run an automation"},
           {"value": "device", "label": "Control a device"}, {"value": "none", "label": "Nothing"}]
# The wall's own full-screen views a tile can open (the wall shows them at #/wall/<id>).
WALL_SCREENS = [{"id": "cameras", "name": "Cameras"}, {"id": "calendar", "name": "Calendar"},
                {"id": "weather", "name": "Weather"}, {"id": "todo", "name": "To do"}]
TAP = {"key": "tap", "label": "When tapped", "type": "action", "default": {"do": "own"}, "options": ACTIONS}
HOLD = {"key": "hold", "label": "When held", "type": "action", "default": {"do": "none"},
        "options": [a for a in ACTIONS if a["value"] != "own"]}
for _t in TILE_TYPES:
    # Sizes above are in the old grid's cells (12 x 8); the grid is twice as fine now.
    _t["min"] = [v * OLD_SCALE for v in _t["min"]]
    _t["default"] = [v * OLD_SCALE for v in _t["default"]]
    _t["fields"] = [*_t["fields"], *([LOOK] if _t.get("lists") else []), TAP, HOLD, TEXT_SIZE]
# Each tile's starting size on a phone layout's 4-column grid of square cells ([w, h]): what the
# phone layout editor uses when a tile is added. `wallOnly` tiles have none: the phone apps don't
# draw them, so phone layouts never hold them (phone_layouts.py drops any an older layout has).
PHONE_SIZES = {"camera": [4, 3], "alerts": [4, 3], "dial": [2, 2], "devices": [4, 2], "mode": [4, 2],
               "values": [4, 3], "attention": [4, 2], "weather": [2, 2], "today": [4, 4], "greeting": [4, 1],
               "todo": [4, 4], "calendar": [4, 4], "people": [4, 3]}
for _t in TILE_TYPES:
    if not _t.get("wallOnly"):
        _t["phone"] = PHONE_SIZES.get(_t["type"], [4, 2])
TYPES = {t["type"]: t for t in TILE_TYPES}


def new_tile_id() -> str:
    return "t_" + secrets.token_hex(4)


def tile_config(t: dict[str, Any]) -> dict[str, Any]:
    """A tile's settings as sent (wall dashboards and phone layouts alike)."""
    c = t.get("config")
    return dict(c) if isinstance(c, dict) else {}


# -- what a device can do (the tile filters) -----------------------------------------------------

def matches(ent: Any, flt: str) -> bool:
    k = KINDS[ent.kind]
    if flt == "picture":
        return k.has_camera
    if flt == "alerts":
        return k.has_camera
    if flt == "number-command":
        return any(c.value_type in ("number", "percent") for c in k.commands)
    if flt == "mode":
        return k.is_mode
    if flt == "forecast":
        return "forecast" in ent.attrs
    if flt == "agenda":
        return "upcoming" in ent.attrs
    if flt == "temperature-setting":  # something you set to a temperature (a thermostat)
        return any(c.value_type == "number" and is_temperature_unit(c.unit) for c in k.commands)
    if flt == "temperature":
        return reports_temperature(ent)
    return True


def is_temperature_unit(unit: Any) -> bool:
    return isinstance(unit, str) and re.fullmatch(r"°[CF]", unit.strip()) is not None


def reports_temperature(ent: Any) -> bool:
    """A room's temperature: a reading in °C / °F that is the device's own state (a temperature sensor, any
    sensor measuring one), or any temperature a climate device reports (a thermostat, the weather) - not a
    server's CPU or a disk."""
    climate = screen_of(getattr(ent, "category", None) or KINDS[ent.kind].category) == "climate"
    return any(p["type"] == "number" and is_temperature_unit(p["unit"]) and (not p["attr"] or climate)
               for p in properties_for(ent))


def number_command(ent: Any) -> str | None:
    cmds = [c for c in KINDS[ent.kind].commands if c.value_type in ("number", "percent")]
    return next((c.id for c in cmds if c.value_type == "number"), cmds[0].id if cmds else None)


def state_command(ent: Any) -> str | None:
    return next((c.id for c in KINDS[ent.kind].commands if c.value_type == "state"), None)


# -- the home page: which page a wall tablet goes back to (and a different one in the mornings) --

MORNING = {"weekdays": {"from": "06:00", "to": "10:00"}, "weekends": {"from": "07:00", "to": "12:00"}}
IDLE_MINUTES = 2


def _hhmm(v: Any) -> str | None:
    try:
        hh, mm = (int(x) for x in str(v).split(":"))
    except (TypeError, ValueError):
        return None
    return f"{hh:02d}:{mm:02d}" if 0 <= hh <= 23 and 0 <= mm <= 59 else None


def clean_home(raw: Any, pages: list[dict[str, Any]], errors: list[str] | None = None) -> dict[str, Any]:
    """{"page", "morning": null | {"page", "weekdays": {"from", "to"}, "weekends": {...}}, "idleMinutes"}.
    Defaults filled in; a page that's gone falls back (home: the first page; mornings: off)."""
    raw = raw if isinstance(raw, dict) else {}
    ids = [p["id"] for p in pages]
    page = raw.get("page") if raw.get("page") in ids else (ids[0] if ids else "")
    morning = None
    m = raw.get("morning")
    if isinstance(m, dict) and m.get("page") in ids:
        morning = {"page": m["page"]}
        for part, label in (("weekdays", "Monday to Friday"), ("weekends", "Saturday and Sunday")):
            w = m.get(part) if isinstance(m.get(part), dict) else {}
            start, end = (MORNING[part][k] if w.get(k) in (None, "") else _hhmm(w.get(k)) for k in ("from", "to"))
            if not start or not end:
                if errors is not None:
                    errors.append(f"Mornings {label}: times look like 06:00")
                start, end = start or MORNING[part]["from"], end or MORNING[part]["to"]
            elif start >= end and errors is not None:
                errors.append(f"Mornings {label}: the end must be after the start")
            morning[part] = {"from": start, "to": end}
    try:
        idle = max(0, min(240, int(raw.get("idleMinutes", IDLE_MINUTES))))
    except (TypeError, ValueError):
        idle = IDLE_MINUTES
    return {"page": page, "morning": morning, "idleMinutes": idle}


def with_home(body: dict[str, Any]) -> dict[str, Any]:
    """A saved dashboard as clients get it: on today's grid, with `home`, `tabNames`, `theme` and `christmas`
    always there (older ones saved before them get the defaults)."""
    body = upgrade(body)
    tab_names = body.get("tabNames") if body.get("tabNames") in TAB_NAMES else TAB_NAMES[0]
    return {**body, "home": clean_home(body.get("home"), body.get("pages") or []), "tabNames": tab_names,
            "theme": clean_theme(body.get("theme")), "christmas": clean_christmas(body.get("christmas"))}


# -- the wall's look ------------------------------------------------------------------------------

def clean_theme(v: Any) -> str:
    v = OLD_THEMES.get(v, v)
    return v if v in THEMES else THEMES[0]


def clean_season(v: Any, errors: list[str] | None = None) -> dict[str, str] | None:
    """The Christmas season: None (off) or {"from": "MM-DD", "to": "MM-DD"} - every year (the automations'
    date ranges, without a year). Unreadable dates are an error and keep the default dates."""
    if not isinstance(v, dict):
        return None
    d = clean_dates({"from": v.get("from") or SEASON["from"], "to": v.get("to") or SEASON["to"], "year": None})
    problems = dates_errors(d)
    if problems:
        if errors is not None:
            errors.extend("Christmas season: " + p[0].lower() + p[1:] for p in problems)
        return dict(SEASON)
    return {"from": d["from"], "to": d["to"]}


def season_active(season: Any, today: date | None = None) -> bool:
    """Is it the Christmas season (inclusive; a season over New Year wraps, Dec 1 - Jan 6)? Off: never."""
    s = clean_season(season)
    return bool(s) and dates_ok({**s, "year": None}, today)


def clean_christmas(raw: Any, errors: list[str] | None = None) -> dict[str, Any]:
    """Christmas settings with the defaults filled in. An empty greeting stays empty (no greeting)."""
    raw = raw if isinstance(raw, dict) else {}
    out = dict(CHRISTMAS)
    out["snow"] = raw["snow"] if isinstance(raw.get("snow"), bool) else CHRISTMAS["snow"]
    if isinstance(raw.get("greeting"), str):
        out["greeting"] = " ".join(raw["greeting"].split())[:40]
    for key, allowed in (("characters", CHARACTERS), ("place", PLACES)):
        v = raw.get(key, CHRISTMAS[key])
        if v not in allowed:
            if errors is not None:
                errors.append(f"Christmas: unknown {key} “{v}”")
            v = CHRISTMAS[key]
        out[key] = v
    pics = raw.get("pictures") if isinstance(raw.get("pictures"), list) else []
    out["pictures"] = [p for p in dict.fromkeys(str(x) for x in pics) if PICTURE_ID.match(p)][:MAX_PICTURES]
    out["season"] = clean_season(raw.get("season"), errors)
    return out


def theme_now(dash: dict[str, Any], today: date | None = None) -> str:
    """The look a wall shows today: Christmas in its season, otherwise the chosen theme (the wall works
    this out itself too, at local midnight - web/src/wall/theme.js)."""
    xmas = clean_christmas(dash.get("christmas"))
    return "christmas" if season_active(xmas["season"], today) else clean_theme(dash.get("theme"))


# -- older dashboards -----------------------------------------------------------------------------

def upgrade(body: dict[str, Any]) -> dict[str, Any]:
    """A dashboard as an older Newman saved it, brought up to date (nothing changes on one that already is):
    on the old 12 x 8 grid (no "grid"), every tile's place and size doubles; tile settings that moved
    (upgrade_config)."""
    old = body.get("grid") != GRID
    ids = {p.get("id") for p in body.get("pages") or [] if isinstance(p, dict)}
    pages = []
    for p in body.get("pages") or []:
        if not isinstance(p, dict):
            continue
        tiles = []
        for t in p.get("tiles") or []:
            if not isinstance(t, dict):
                continue
            t = dict(t)
            if old:
                for k in ("x", "y", "w", "h"):
                    try:
                        t[k] = int(t.get(k, 0)) * OLD_SCALE
                    except (TypeError, ValueError):
                        pass  # clean() says it's a bad position
            t["config"] = upgrade_config(t.get("type"), tile_config(t), ids)
            tiles.append(t)
        pages.append({**p, "tiles": tiles})
    return {**body, "pages": pages, "grid": list(GRID)}


def upgrade_config(typ: Any, c: dict[str, Any], pages: set[Any]) -> dict[str, Any]:
    """A tile's settings from before Look and When tapped: the climate glance's "Opens page" becomes When
    tapped: open that page (one of this dashboard's `pages` - a page since removed is dropped, as before);
    a devices tile's "style" (big tiles / list) becomes its Look."""
    c = dict(c)
    if typ == "climate" and "page" in c:
        page = c.pop("page")
        if page in pages and not isinstance(c.get("tap"), dict):
            c["tap"] = {"do": "page", "page": page}
    if typ == "devices" and c.get("look") not in LOOKS:
        c["look"] = "list" if c.get("style") == "list" else "tiles"
    return c


def clean_action(raw: Any, pages: set[str], tap: bool) -> tuple[dict[str, Any] | None, str]:
    """A tile's When tapped (`tap`) or When held: (the action to keep - None for the default: its own thing
    / nothing -, what's missing or "")."""
    if not isinstance(raw, dict):
        return None, ""
    do = raw.get("do")
    if do in (None, "", "own" if tap else "none") or (do == "own" and not tap):
        return None, ""
    if do == "none":
        return {"do": "none"}, ""
    if do == "page":
        page = raw.get("page")
        return {"do": "page", "page": page if page in pages else None}, "" if page in pages else "choose a page"
    if do == "screen":
        screen = raw.get("screen")
        ok = screen in {s["id"] for s in WALL_SCREENS}
        return {"do": "screen", "screen": screen if ok else None}, "" if ok else "choose a screen"
    if do == "automation":
        aid = raw.get("automation")
        ok = isinstance(aid, str) and bool(aid)
        return {"do": "automation", "automation": aid if ok else None}, "" if ok else "choose an automation"
    if do == "device":
        eid, cmd = raw.get("entity"), raw.get("command")
        ok = isinstance(eid, str) and bool(eid) and isinstance(cmd, str) and bool(cmd)
        return ({"do": "device", "entity": eid if isinstance(eid, str) else None, "command": cmd if isinstance(cmd, str) else None},
                "" if ok else "choose a device and what to do")
    return None, f"unknown choice “{do}”"


def tile_action(dash: dict[str, Any], tile_id: str, which: str) -> dict[str, Any] | None:
    """The When tapped / When held action of one of this dashboard's tiles, or None."""
    for p in dash.get("pages") or []:
        for t in p.get("tiles") or []:
            if t.get("id") == tile_id:
                act = tile_config(t).get(which)
                return act if isinstance(act, dict) else None
    return None


# -- validation -------------------------------------------------------------------------------

def clean(body: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Normalise a dashboard sent by a client; return it plus problems (empty = fine to save)."""
    errors: list[str] = []
    body = upgrade(body)  # sent by a page from before the finer grid: its tiles are on the old one
    pages = []
    for p in (body.get("pages") or [])[:12]:
        tiles = []
        for t in (p.get("tiles") or [])[:40]:
            spec = TYPES.get(t.get("type"))
            if not spec:
                errors.append(f"Unknown tile type “{t.get('type')}”")
                continue
            try:
                x, y, w, h = (int(t.get(k, 0)) for k in ("x", "y", "w", "h"))
            except (TypeError, ValueError):
                errors.append(f"A {spec['name']} tile has a bad position")
                continue
            if w < 1 or h < 1 or x < 0 or y < 0 or x + w > GRID_COLS or y + h > GRID_ROWS:
                errors.append(f"A {spec['name']} tile on “{p.get('name', 'a page')}” is off the grid")
            tiles.append({"id": str(t.get("id") or new_tile_id())[:24], "type": spec["type"], "x": x, "y": y,
                          "w": w, "h": h, "config": tile_config(t)})
        for i, a in enumerate(tiles):  # tiles may not overlap
            for b in tiles[i + 1:]:
                if a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]:
                    errors.append(f"Two tiles overlap on “{p.get('name', 'a page')}”")
        pages.append({"id": str(p.get("id") or "p_" + secrets.token_hex(3))[:24],
                      "name": str(p.get("name") or "Page")[:24], "icon": str(p.get("icon") or "home")[:24],
                      "tiles": tiles})
    if not pages:
        errors.append("A dashboard needs at least one page")
    page_ids = {p["id"] for p in pages}
    for p in pages:
        for t in p["tiles"]:
            spec, c = TYPES[t["type"]], t["config"]
            if not spec.get("lists") or c.get("look") not in LOOKS:
                c.pop("look", None)
            if t["type"] == "devices":
                c["style"] = "list" if c.get("look") == "list" else "tiles"  # what the phone apps go by
            for f in (TAP, HOLD):
                act, missing = clean_action(c.get(f["key"]), page_ids, f is TAP)
                if missing:
                    errors.append(f"{spec['name']} tile on “{p['name']}”: {f['label']} - {missing}")
                if act is None:
                    c.pop(f["key"], None)
                else:
                    c[f["key"]] = act
    name = str(body.get("name") or "Dashboard").strip()[:40]
    theme = OLD_THEMES.get(body.get("theme"), body.get("theme"))
    if theme not in THEMES:
        if theme not in (None, ""):
            errors.append(f"Unknown theme “{theme}”")
        theme = THEMES[0]
    christmas = clean_christmas(body.get("christmas"), errors)
    # "Show as" names for this dashboard only ({entity id: "Garage"}): the device keeps its real name
    # everywhere else, and automations still point at the device itself.
    names = {str(k)[:120]: str(v).strip()[:40] for k, v in (body.get("names") or {}).items()
             if isinstance(v, str) and v.strip()} if isinstance(body.get("names"), dict) else {}
    home = clean_home(body.get("home"), pages, errors)
    tab_names = body.get("tabNames") if body.get("tabNames") in TAB_NAMES else TAB_NAMES[0]
    return {"name": name, "theme": theme, "christmas": christmas, "pages": pages, "names": names, "home": home,
            "tabNames": tab_names, "grid": list(GRID)}, errors


# -- a first dashboard, laid out from the house's own devices ------------------------------------

def _tile(t: str, x: int, y: int, w: int, h: int, **config: Any) -> dict[str, Any]:
    """A tile placed in the old grid's cells (12 x 8, easier to read), on today's grid."""
    x, y, w, h = (v * OLD_SCALE for v in (x, y, w, h))
    return {"id": new_tile_id(), "type": t, "x": x, "y": y, "w": w, "h": h, "config": config}


def today_page(hub: Any, page_id: str = "today") -> dict[str, Any]:
    """The Today page (the mornings' home): a greeting, To do, the agenda and the weather."""
    weather = next((e.id for e in hub.all() if matches(e, "forecast")), None)
    return {"id": page_id, "name": "Today", "icon": "sun", "tiles": [
        _tile("greeting", 0, 0, 12, 1),
        _tile("todo", 0, 1, 5, 7),
        _tile("calendar", 5, 1, 4, 7, entities=[], days=31),
        _tile("weather", 9, 1, 3, 7, **({"entity": weather, "show": "days"} if weather else {"show": "days"})),
    ]}


def starter(hub: Any, name: str = "Wall tablet") -> dict[str, Any]:
    ents = hub.all()
    kinds = {e.id: KINDS[e.kind] for e in ents}
    cameras = [e.id for e in ents if kinds[e.id].has_camera and "camera" not in e.attrs]  # zones point at a camera
    # The dial goes to something you set to a number (a thermostat) before a brightness (percent).
    dials = sorted((e for e in ents if number_command(e)),
                   key=lambda e: (KINDS[e.kind].command(number_command(e)).value_type != "number", not state_command(e)))
    secure_cmd = [e.id for e in ents if kinds[e.id].secure_state and kinds[e.id].commands]
    secure_ro = [e.id for e in ents if kinds[e.id].secure_state and not kinds[e.id].commands]
    modes = [e.id for e in ents if kinds[e.id].is_mode]
    lights = [e.id for e in ents if screen_of(e.category) == "lights" and kinds[e.id].commands]
    readings = [{"entity": e.id, "property": p["id"]} for e in ents if screen_of(e.category) == "climate"
                for p in properties_for(e)[:1] if p["type"] == "number"]

    tile = _tile
    dial = dials[0] if dials else None
    dial_cfg = {"entity": dial.id, "command": number_command(dial), "modeCommand": state_command(dial)} if dial else None
    weather = next((e.id for e in ents if matches(e, "forecast")), None)
    overview = [tile("weather", 0, 0, 4, 3, **({"entity": weather, "show": "days"} if weather else {})),
                tile("calendar", 0, 3, 4, 5, entities=[], days=31)]
    if cameras:
        overview += [tile("camera", 4, 0, 5, 5, entities=cameras[:4]), tile("alerts", 4, 5, 5, 3, limit=4)]
    if dial_cfg:
        overview.append(tile("dial", 9, 0, 3, 4, **dial_cfg))
    overview.append(tile("devices", 9, 4, 3, 4, title="Shortcuts", entities=(secure_cmd + lights)[:4], style="tiles",
                         look="auto"))
    security = []
    if modes:
        security.append(tile("mode", 0, 0, 7, 1, entity=modes[0]))
    security.append(tile("devices", 0, 1, 7, 3, title="Locks & doors", entities=secure_cmd, style="list", look="list"))
    security += [tile("devices", 0, 4, 4, 4, title="Doors & windows", entities=secure_ro, style="list", look="list"),
                 tile("attention", 4, 4, 3, 4)]
    if cameras:
        security += [tile("camera", 7, 0, 5, 5, entities=cameras), tile("alerts", 7, 5, 5, 3, limit=4)]
    pages = [today_page(hub), {"id": "overview", "name": "Overview", "icon": "home", "tiles": overview},
             {"id": "security", "name": "Security", "icon": "shield", "tiles": security},
             {"id": "lights", "name": "Lights", "icon": "bulb",
              "tiles": [tile("devices", 0, 0, 12, 8, title="Lights", entities=lights, style="tiles", look="tiles",
                                 groupBy="area")]}]
    climate = []
    if dial_cfg:
        climate.append(tile("dial", 0, 0, 8, 8, **dial_cfg))
    if readings:
        climate.append(tile("values", 8 if dial_cfg else 0, 0, 4, 8, title="Around the house", items=readings))
    if climate:
        pages.append({"id": "climate", "name": "Climate", "icon": "thermometer", "tiles": climate})
    # Overview is home; mornings start on Today.
    home = clean_home({"page": "overview", "morning": {"page": "today"}}, pages)
    return {"name": name, "theme": THEMES[0], "christmas": clean_christmas(None), "pages": pages, "home": home,
            "tabNames": TAB_NAMES[0], "grid": list(GRID)}
