"""Today: what needs someone today, what's on, and what's coming - worked out once on the hub so the
wall tile, the phone and (later) voice all say the same thing.

    to do     upkeep items due or overdue (not snoozed), devices needing a hand (offline, low battery,
              no power, jammed…), and appliance needs ("rinse aid low") not dismissed
    soon      upkeep items coming up
    events    today's calendar events
    heads-up  weather warnings, rain above a threshold, and - from the evening on - tomorrow's events
              from calendars marked "the evening before" and upkeep items due tomorrow so marked
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

DEFAULTS = {"todayRainThreshold": 50, "todayEveningFrom": "17:00"}
LEVEL_ORDER = {"overdue": 0, "device": 1, "due": 2, "appliance": 3}


def _time_text(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p").replace(":00 ", " ")


def _days(n: int) -> str:
    return f"{n} day{'s' if n != 1 else ''}"


def build(hub: Any, settings: dict[str, Any], *, person_id: str | None = None,
          dismissed: dict[str, float] | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    today, tomorrow = now.date(), now.date() + timedelta(days=1)
    s = {**DEFAULTS, **{k: v for k, v in settings.items() if k in DEFAULTS}}
    dismissed = dismissed or {}
    ents = hub.all()
    todo: list[dict[str, Any]] = []
    soon: list[dict[str, Any]] = []
    heads: list[dict[str, Any]] = []

    for e in ents:
        a = e.attrs or {}
        if e.kind == "upkeep":
            if not e.available:
                continue
            if person_id and a.get("ownerId") and a["ownerId"] != person_id:
                continue  # someone else's (on a phone: mine and everyone's)
            owner = a.get("owner") if a.get("ownerId") else ""
            base = {"id": e.id, "source": "upkeep", "entity": e.id, "title": e.name, "owner": owner,
                    "ownerId": a.get("ownerId") or "", "askWho": bool(a.get("askWho", True)) and not a.get("ownerId"),
                    "every": a.get("every") or "", "due": a.get("due"), "icon": a.get("icon") or ""}
            snoozed = a.get("snoozedUntil") and a["snoozedUntil"] > now.timestamp()
            if e.state == "overdue" and not snoozed:
                late = -int(a.get("daysLeft") or -1)
                todo.append({**base, "level": "overdue", "status": f"{_days(late)} late", "sort": -late,
                             "action": "done"})
            elif e.state == "due" and not snoozed:
                todo.append({**base, "level": "due", "status": "Due today", "sort": 0, "action": "done"})
            elif e.state == "soon" or snoozed:
                left = a.get("daysLeft")
                status = "Tomorrow" if snoozed else ("Running hours almost up" if left is None or left > 999
                                                     else f"In {_days(left)}" if left > 1 else "Tomorrow")
                soon.append({**base, "level": "soon", "status": status, "sort": left if left is not None else 999,
                             "action": "done"})
            if a.get("eveningBefore") and a.get("due") == tomorrow.isoformat() and _evening(now, s):
                heads.append({"id": f"{e.id}:tomorrow", "source": "upkeep", "icon": "clock", "entity": e.id,
                              "text": f"{e.name} is due tomorrow"})
            continue
        issues = e.health_issues
        if issues and e.kind not in ("weather", "calendar"):
            todo.append({"id": f"{e.id}:health", "source": "device", "entity": e.id, "level": "device",
                         "title": f"{e.name}: {issues[0].lower()}", "status": ", ".join(issues[1:]) or issues[0],
                         "sort": 0, "action": "open", "askWho": False})
        for need in a.get("needs") or []:
            key = f"{e.id}:need:{need}"
            if key in dismissed:
                continue
            todo.append({"id": key, "source": "appliance", "entity": e.id, "level": "appliance",
                         "title": f"{e.name}: {need}", "status": f"Reported by the {e.name.lower()}",
                         "sort": 0, "action": "dismiss", "askWho": False})

    todo.sort(key=lambda t: (LEVEL_ORDER[t["level"]], t["sort"], t["title"]))
    soon.sort(key=lambda t: (t["sort"], t["title"]))

    # Today's events, from every calendar.
    events = []
    for c in (x for x in ents if x.kind == "calendar"):
        a = c.attrs or {}
        for ev in a.get("upcoming") or []:
            try:
                start, end = datetime.fromisoformat(ev["start"]), datetime.fromisoformat(ev.get("end") or ev["start"])
            except (KeyError, ValueError):
                continue
            if ev.get("allDay"):
                on_today = start.date() <= today < end.date() or start.date() == today
            else:
                on_today = start.date() <= today <= end.date() and end > now - timedelta(hours=1)
            if on_today:
                events.append({"title": ev.get("title") or "Busy", "allDay": bool(ev.get("allDay")),
                               "time": "All day" if ev.get("allDay") else _time_text(start),
                               "start": ev["start"], "end": ev.get("end"), "location": ev.get("location") or "",
                               "calendar": c.name, "colour": a.get("colour") or "#3CCFB4",
                               "now": not ev.get("allDay") and start <= now < end})
            if a.get("headsUp") == "evening" and start.date() == tomorrow and _evening(now, s):
                heads.append({"id": f"{c.id}:{ev.get('title')}:{ev['start']}", "source": "calendar", "icon": "calendar",
                              "text": f"{ev.get('title') or 'An event'} tomorrow", "calendar": c.name})
    events.sort(key=lambda ev: (not ev["allDay"], ev["start"]))

    # Weather: official warnings, then rain worth an umbrella.
    for w in (x for x in ents if x.kind == "weather"):
        a = w.attrs or {}
        for al in a.get("alerts") or []:
            heads.insert(0, {"id": f"{w.id}:{al.get('id')}", "source": "weather", "icon": "warning", "entity": w.id,
                             "text": al.get("name") or "Weather warning", "short": (al.get("name") or "Weather warning").lower(),
                             "level": al.get("type") or "",
                             "colour": al.get("colour") or ""})
        thr = int(s["todayRainThreshold"] or 0)
        if thr > 0:
            for h in a.get("hourly") or []:
                try:
                    t = datetime.fromisoformat(h["time"])
                except (KeyError, ValueError):
                    continue
                if t.date() != today or t < now.replace(minute=0, second=0, microsecond=0):
                    continue
                if (h.get("precipChance") or 0) >= thr:
                    what = "Snow" if (h.get("snow") or 0) > (h.get("rain") or 0) else "Rain"
                    when = "now" if t <= now else f"from {_time_text(t)}"
                    # "short": for a one-line summary ("3 things to do · rain from 3 PM").
                    heads.append({"id": f"{w.id}:rain", "source": "weather", "icon": "rain", "entity": w.id,
                                  "text": f"{what} likely {when} ({h['precipChance']}%)", "short": f"{what.lower()} {when}"})
                    break

    return {"date": today.isoformat(), "todo": todo, "soon": soon, "events": events, "headsUp": heads,
            "counts": {"todo": len(todo), "soon": len(soon), "events": len(events)}}


def _evening(now: datetime, s: dict[str, Any]) -> bool:
    try:
        hh, mm = (int(x) for x in str(s["todayEveningFrom"]).split(":")[:2])
    except ValueError:
        hh, mm = 17, 0
    return (now.hour, now.minute) >= (hh, mm)
