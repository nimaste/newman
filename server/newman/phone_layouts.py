"""Phone layouts: how the phone apps arrange their pages - a household default, and a layout of
their own for anyone who wants one (everyone else uses the default; a reset goes back to it).

A layout is {"today": bool, "pages": [{"id", "name", "icon", "tab", "tiles": [tile]}]}. A tile is a
dashboard tile (the same types and settings as the wall, from dashboards.TILE_TYPES) placed on a
4-column grid of square cells: x 0-3, w 1-4, h 1-8, y from 0 down (a page scrolls). Pages with
`tab` are the phone's first bottom tabs (at most 3, in order; People and Alerts are the last two);
the others open from links at the bottom of the first tab. `today` puts the Today card at the top of
the first tab.

The first household default is laid out from the first admin's default wall dashboard by the rules
the phone apps used before layouts existed (see `from_dashboard`), so phones look the same until
someone arranges them.
"""
from __future__ import annotations

import math
import secrets
from typing import Any

from . import dashboards as D

COLS = 4
MAX_H = 8
MAX_Y = 400  # a long page, but not an endless one
MAX_PAGES = 8
MAX_TABS = 3
MAX_TILES = 48  # per page

# The phone's Today card stands in for the wall's Today page: a page of only these tiles (with at
# least one marker) isn't a phone page.
TODAY_TYPES = {"greeting", "todo", "today"}
TODAY_MARKERS = {"greeting", "todo"}
INFO_TYPES = {"calendar", "weather"}


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# -- validation -------------------------------------------------------------------------------

def clean(body: Any) -> tuple[dict[str, Any], list[str]]:
    """Normalise a layout sent by a client; return it plus problems (empty = fine to save)."""
    body = body if isinstance(body, dict) else {}
    errors: list[str] = []
    raw_pages = body.get("pages") if isinstance(body.get("pages"), list) else []
    if len(raw_pages) > MAX_PAGES:
        errors.append(f"A phone has room for {MAX_PAGES} pages at most")
    pages, seen = [], set()
    for p in raw_pages[:MAX_PAGES]:
        p = p if isinstance(p, dict) else {}
        name = str(p.get("name") or "Page").strip()[:24] or "Page"
        raw_tiles = p.get("tiles") if isinstance(p.get("tiles"), list) else []
        if len(raw_tiles) > MAX_TILES:
            errors.append(f"“{name}” has more than {MAX_TILES} tiles")
        tiles, ids = [], set()
        for t in raw_tiles[:MAX_TILES]:
            t = t if isinstance(t, dict) else {}
            spec = D.TYPES.get(t.get("type"))
            if not spec:
                errors.append(f"Unknown tile type “{t.get('type')}”")
                continue
            if spec.get("wallOnly"):
                continue  # one the phones don't draw (an older layout's): left out, not an error
            try:
                x, y, w, h = (int(t.get(k, 0)) for k in ("x", "y", "w", "h"))
            except (TypeError, ValueError):
                errors.append(f"A {spec['name']} tile has a bad position")
                continue
            if w < 1 or h < 1 or w > COLS or h > MAX_H or x < 0 or y < 0 or x + w > COLS or y + h > MAX_Y:
                errors.append(f"A {spec['name']} tile on “{name}” is off the phone's 4-column grid")
            config = D.tile_config(t)
            # Text size: in the tile's settings like the wall (config.textSize), and on the tile itself.
            size = _num(t.get("textSize")) if t.get("textSize") is not None else _num(config.get("textSize"))
            if size is not None and 50 <= size <= 200:
                config["textSize"] = size if size != int(size) else int(size)
            else:
                config.pop("textSize", None)
            tid = str(t.get("id") or D.new_tile_id())[:24]
            if tid in ids:
                tid = D.new_tile_id()
            ids.add(tid)
            tile = {"id": tid, "type": spec["type"], "x": x, "y": y, "w": w, "h": h, "config": config}
            if "textSize" in config:
                tile["textSize"] = config["textSize"]
            tiles.append(tile)
        if len(tiles) < len(raw_tiles[:MAX_TILES]):
            tiles = _close_gaps(tiles)
        for i, a in enumerate(tiles):  # tiles may not overlap
            if any(_overlap(a, b) for b in tiles[i + 1:]):
                errors.append(f"Two tiles overlap on “{name}”")
                break
        pid = str(p.get("id") or "p_" + secrets.token_hex(3))[:24]
        if pid in seen:
            pid = "p_" + secrets.token_hex(3)
        seen.add(pid)
        pages.append({"id": pid, "name": name, "icon": str(p.get("icon") or "home")[:24],
                      "tab": p.get("tab") is True, "tiles": tiles})
    tabs = sum(1 for p in pages if p["tab"])
    if not pages:
        errors.append("A phone layout needs at least one page")
    elif not tabs:
        errors.append("At least one page needs to be a tab")
    if tabs > MAX_TABS:
        errors.append(f"At most {MAX_TABS} pages can be tabs (People and Alerts are the other two)")
    return {"today": body.get("today") is True, "pages": pages}, errors


def _overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


def phone_tile(t: Any) -> bool:
    """A tile the phones draw: a known type that isn't `wallOnly`."""
    spec = D.TYPES.get(t.get("type")) if isinstance(t, dict) else None
    return bool(spec) and not spec.get("wallOnly")


def _close_gaps(tiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """After tiles were left out: rows nothing covers any more close up (everything below moves up
    by that many rows), so no hole is left where they were. Nothing else moves, nothing overlaps."""
    used = {r for t in tiles for r in range(t["y"], t["y"] + t["h"])}
    for t in tiles:
        t["y"] -= sum(1 for r in range(t["y"]) if r not in used)
    return tiles


def with_text_size(layout: dict[str, Any]) -> dict[str, Any]:
    """A saved layout as clients get it: tiles carry `textSize` beside their config when it's set.
    Tiles the phones don't draw (`wallOnly` ones a layout saved before they were marked so) are left
    out, and the rows they took close up."""
    for p in layout.get("pages") or []:
        raw = p.get("tiles") or []
        keep = [t for t in raw if phone_tile(t)]
        if len(keep) < len(raw):
            p["tiles"] = _close_gaps(keep)
        for t in p.get("tiles") or []:
            v = (t.get("config") or {}).get("textSize")
            if v is not None:
                t["textSize"] = v
            else:
                t.pop("textSize", None)
    return layout


# -- the first household default, from a wall dashboard ------------------------------------------

def is_today_page(page: dict[str, Any]) -> bool:
    types = [t.get("type") for t in page.get("tiles") or []]
    return bool(types) and any(t in TODAY_MARKERS for t in types) and all(t in TODAY_TYPES | INFO_TYPES for t in types)


def phone_height(tile: dict[str, Any], wide: bool) -> int:
    """How many square rows a whole wall tile gets on the phone (4 columns, each about 85 pt)."""
    t, c = tile.get("type"), tile.get("config") or {}
    if not wide:
        return 2  # the half-width (compact) dial and weather: a square
    if t == "dial":
        return 4  # a square, full-width dial
    if t == "weather":
        return 1 if c.get("show") == "line" else 3
    if t in ("camera", "alerts", "people"):
        return 3  # a 4:3 picture; four alerts; a few people
    if t in ("calendar", "today", "todo"):
        return 4
    if t in ("values", "devices"):  # rows of about half a cell, plus the title
        n = len(c.get("items") or []) if t == "values" else len(c.get("entities") or [])
        return max(2, min(6, math.ceil(n * 0.6) + 1))
    if t in ("attention", "mode"):
        return 2
    return 3


def _span(t: dict[str, Any]) -> tuple[float, float]:
    """A wall tile's width and height in the old wall grid's cells (12 x 8): the rules below were written
    for it, before the wall's grid was made twice as fine."""
    def cells(k: str) -> float:
        try:
            return int(t.get(k) or 0) / D.OLD_SCALE
        except (TypeError, ValueError):
            return 0
    return cells("w"), cells("h")


def flow(page: dict[str, Any]) -> list[dict[str, Any]]:
    """A wall page's tiles as phone cells, in dashboard order: {"tile", "span", "h", "header"}.
    Wide tiles span 4 columns, small ones 2; a devices tile in the big-tiles look becomes one card
    (a one-device devices tile, 1 row high) per device - full width with a brightness bar when the
    wall tile is wide; its title is a section break for packing. The greeting isn't drawn (the
    phone's header greets)."""
    tiles = page.get("tiles") or []
    several = len(tiles) > 1
    out: list[dict[str, Any]] = []
    for t in tiles:
        c = t.get("config") or {}
        typ = t.get("type")
        if typ == "devices" and c.get("style") != "list":
            title = str(c.get("title") or "").strip()
            if several and title and title.lower() != str(page.get("name") or "").lower():
                out.append({"header": True, "span": COLS, "h": 0, "tile": None})
            wide = _span(t)[0] >= 6
            keep = {k: c[k] for k in ("textSize",) if k in c}
            for i, eid in enumerate(c.get("entities") or []):
                card = {"id": (str(t.get("id") or "t") + "_" + str(i))[:24], "type": "devices",
                        "config": {"entities": [eid], "style": "tiles", **keep}}
                out.append({"tile": card, "span": COLS if wide else 2, "h": 1})
        elif typ == "dial":
            big = _span(t)[0] >= 5 and _span(t)[1] >= 5
            out.append({"tile": t, "span": COLS if big else 2, "h": phone_height(t, big)})
        elif typ == "weather":
            big = _span(t)[0] >= 6 or c.get("show") == "hours"
            out.append({"tile": t, "span": COLS if big else 2, "h": phone_height(t, big)})
        elif typ == "greeting" or not phone_tile(t):
            continue
        else:
            out.append({"tile": t, "span": COLS, "h": phone_height(t, True)})
    return pack(out)


def pack(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reorder so half-width cells pair up; never moves a cell across a section header."""
    queue, out, used = list(cells), [], 0
    while queue:
        nxt = queue[0]
        if used > 0 and used + nxt["span"] > COLS:
            fill = next((i for i, c in enumerate(queue) if c["span"] <= COLS - used), -1)
            header = next((i for i, c in enumerate(queue) if c.get("header")), -1)
            if fill > 0 and (header < 0 or fill < header):
                nxt = queue[fill]
        queue.remove(nxt)
        out.append(nxt)
        used = nxt["span"] if used + nxt["span"] > COLS else used + nxt["span"]
        if used >= COLS:
            used = 0
    return out


def place(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cells onto the grid in order: a wide one starts below everything so far; a half one goes in
    the half (left or right) that ends higher, so a 1-row card tucks in beside a 2-row dial."""
    col, tiles = [0] * COLS, []
    for c in cells:
        if c.get("header"):
            col = [max(col)] * COLS  # a section starts on a fresh row
            continue
        h, span = max(1, min(MAX_H, c["h"])), c["span"]
        if span >= COLS:
            x, y = 0, max(col)
        else:
            y, x = min((max(col[i:i + span]), i) for i in range(0, COLS - span + 1, span))
        for i in range(x, x + span):
            col[i] = y + h
        t = c["tile"]
        tile = {"id": str(t.get("id") or D.new_tile_id())[:24], "type": t["type"], "x": x, "y": y, "w": span, "h": h,
                "config": dict(t.get("config") or {})}
        tiles.append(tile)
    return tiles[:MAX_TILES]


def from_dashboard(dash: dict[str, Any]) -> dict[str, Any]:
    """The phone layout the apps drew from this dashboard automatically: the Today page (if any)
    becomes the Today card, the first three other pages are tabs, the rest open from links."""
    pages = D.upgrade(dash).get("pages") or []  # on today's wall grid, whenever it was saved
    today = next((p for p in pages if is_today_page(p)), None)
    rest = [p for p in pages if p is not today][:MAX_PAGES]
    out = [{"id": str(p.get("id") or "p_" + secrets.token_hex(3))[:24], "name": str(p.get("name") or "Page")[:24],
            "icon": str(p.get("icon") or "home")[:24], "tab": i < MAX_TABS, "tiles": place(flow(p))}
           for i, p in enumerate(rest)]
    if not out:
        out = [{"id": "home", "name": "Home", "icon": "home", "tab": True, "tiles": []}]
    return with_text_size({"today": today is not None, "pages": out})
