"""Today: what needs someone today, what's on, and what's coming - worked out once on the hub so the
wall tile, the phone and (later) voice all say the same thing.

    to do     tasks from GCloud Calendar due or overdue (not put off till tomorrow), devices needing a hand
              (offline, low battery, no power, jammed…), and appliance needs ("rinse aid low") not dismissed
    soon      tasks coming up (within their lead days)
    events    today's calendar events
    heads-up  weather warnings, rain above a threshold, and - from the evening on - tomorrow's events
              from calendars marked "the evening before" and tasks due tomorrow marked so

Tasks (tasks.py) come from the Calendar server connections; build_for() gathers them. One that belongs to
someone (X-GCLOUD-OWNER, a GCloud user name matched to a Newman person) shows on that person's phone only;
everyone's show on the wall and on every phone.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from . import tasks as TK
from .kinds import KINDS
from .muting import switched_off, unmuted_issues

DEFAULTS = {"todayRainThreshold": 50, "todayEveningFrom": "17:00"}
LEVEL_ORDER = {"overdue": 0, "device": 1, "due": 2, "appliance": 3}


def _time_text(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p").replace(":00 ", " ")


def _days(n: int) -> str:
    return f"{n} day{'s' if n != 1 else ''}"


def collect_tasks(instances: Any) -> list[dict[str, Any]]:
    """Every task of every Calendar server connection."""
    return [t for i in list(instances.values()) if hasattr(i, "tasks") for t in i.tasks()]


def build_for(h: Any, person_id: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Today for the hub `h` (its store, devices and calendar servers' tasks); `person_id`: a phone's."""
    st = h.store.settings()
    return build(h.hub, st, person_id=person_id, dismissed=st.get("todayDismissed") or {}, now=now,
                 tasks=collect_tasks(h.integrations.instances), people=TK.household(h.store),
                 snoozed=st.get("todaySnoozed") or {})


def _task_item(t: dict[str, Any], today: Any, tomorrow: Any, now: datetime, s: dict[str, Any],
               person_id: str | None, people: list[dict[str, Any]], snoozed: dict[str, float],
               todo: list[dict[str, Any]], soon: list[dict[str, Any]], heads: list[dict[str, Any]]) -> None:
    if t["status"] == "COMPLETED" or not t.get("due"):
        return
    person = TK.owner_person(t.get("owner") or "", people)
    if person_id and t.get("owner") and (not person or person["id"] != person_id):
        return  # someone else's (on a phone: mine and everyone's)
    due = datetime.fromisoformat(t["due"]).date()
    days = (due - today).days
    tid = TK.task_id(t["calendarKey"], t["uid"])
    last = t["history"][0] if t.get("history") else None
    base = {"id": tid, "source": "task", "entity": t.get("calendarEntity"), "title": t["title"],
            "owner": person["name"] if person else t.get("owner") or "", "ownerId": person["id"] if person else "",
            "askWho": bool(t.get("askWho")), "every": t.get("every") or "", "repeatFrom": t.get("repeatFrom") or "due",
            "due": t["due"], "calendar": t["calendar"], "from": f"{t.get('connection') or 'GCloud'} · {t['calendar']}",
            "lastDone": last["at"] if last else "", "lastDoneBy": last["who"] if last else "",
            "history": [{"at": d["at"], "who": d["who"]} for d in (t.get("history") or [])[:4]],
            "notes": (t.get("notes") or "")[:500], "icon": ""}
    snoozed_now = (snoozed.get(tid) or 0) > now.timestamp()
    if days < 0 and not snoozed_now:
        todo.append({**base, "level": "overdue", "status": f"{_days(-days)} late", "sort": days, "action": "done"})
    elif days == 0 and not snoozed_now:
        todo.append({**base, "level": "due", "status": "Due today", "sort": 0, "action": "done"})
    elif days <= int(t.get("lead") if t.get("lead") is not None else TK.DEFAULT_LEAD) or snoozed_now:
        status = "Tomorrow" if snoozed_now or days == 1 else f"In {_days(days)}"
        soon.append({**base, "level": "soon", "status": status, "sort": 1 if snoozed_now else days, "action": "done"})
    if t.get("eveningBefore") and due == tomorrow and _evening(now, s):
        heads.append({"id": f"{tid}:tomorrow", "source": "task", "icon": "clock", "entity": t.get("calendarEntity"),
                      "text": f"{t['title']} is due tomorrow"})


def build(hub: Any, settings: dict[str, Any], *, person_id: str | None = None,
          dismissed: dict[str, float] | None = None, now: datetime | None = None,
          tasks: list[dict[str, Any]] | None = None, people: list[dict[str, Any]] | None = None,
          snoozed: dict[str, float] | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    today, tomorrow = now.date(), now.date() + timedelta(days=1)
    s = {**DEFAULTS, **{k: v for k, v in settings.items() if k in DEFAULTS}}
    dismissed = dismissed or {}
    ents = hub.all()
    todo: list[dict[str, Any]] = []
    soon: list[dict[str, Any]] = []
    heads: list[dict[str, Any]] = []

    for t in tasks or []:
        _task_item(t, today, tomorrow, now, s, person_id, people or [], snoozed or {}, todo, soon, heads)

    for e in ents:
        a = e.attrs or {}
        # Muted problems and those switched off on its Warnings tab: not to do.
        issues = unmuted_issues(e, settings.get("mutedIssues") or {}, now.timestamp(),
                                switched_off(settings.get("deviceWarnings") or {}, e.id))
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
    for c in (x for x in ents if KINDS[x.kind].today == "events"):
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
    for w in (x for x in ents if KINDS[x.kind].today == "weather"):
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
