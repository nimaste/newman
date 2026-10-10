"""Calendars by link: the private "iCal" address Google Calendar, iCloud, Outlook and most others
give you for a calendar. Read-only, no sign-in to any account: Newman fetches each link every few
minutes, expands repeating events, and keeps one calendar device per link - "busy" while an event is
on, with the next events for the calendar tile, and an event when one starts (for automations)."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import icalendar
import recurring_ical_events

from ..store import slug
from .base import Field, Integration

log = logging.getLogger(__name__)

COLOURS = ({"value": "#3CCFB4", "label": "Teal"}, {"value": "#7FB8FF", "label": "Blue"},
           {"value": "#F5B942", "label": "Amber"}, {"value": "#C58BFF", "label": "Purple"},
           {"value": "#FF8A6B", "label": "Coral"}, {"value": "#8BD46B", "label": "Green"})


def _local(v: Any) -> tuple[datetime, bool]:
    """(local naive datetime, all-day?) for an iCal DTSTART/DTEND value."""
    if isinstance(v, datetime):
        return (v.astimezone().replace(tzinfo=None) if v.tzinfo else v), False
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day), True
    raise ValueError("no date")


def parse(text: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    cal = icalendar.Calendar.from_ical(text)
    out = []
    for ev in recurring_ical_events.of(cal).between(start, end):
        try:
            s, all_day = _local(ev.get("DTSTART").dt)
            e_raw = ev.get("DTEND")
            e = _local(e_raw.dt)[0] if e_raw else (s + (timedelta(days=1) if all_day else timedelta(hours=1)))
        except (AttributeError, ValueError):
            continue
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        out.append({"title": str(ev.get("SUMMARY") or "Busy"), "start": s.isoformat(), "end": e.isoformat(),
                    "allDay": all_day, "location": str(ev.get("LOCATION") or "")})
    out.sort(key=lambda x: x["start"])
    return out


class CalendarIntegration(Integration):
    type = "calendar"
    name = "Calendars"
    category = "other"
    provides = ("calendar",)  # kinds of device it can bring in (shown on its catalog card)
    description = ("Upcoming events from calendar links - the private iCal address Google Calendar, iCloud or "
                   "Outlook gives you. Read-only, no account sign-in; one link per calendar, each with a colour.")
    fields = [
        Field("calendars", "Calendars", "devices", required=True,
              help="Google Calendar: Settings → your calendar → “Secret address in iCal format”. "
                   "iCloud: share the calendar as a Public Calendar and copy the link.",
              item_fields=(Field("name", "Name", required=True, placeholder="Family"),
                           Field("url", "Calendar link (iCal)", "password", required=True,
                                 placeholder="https://calendar.google.com/calendar/ical/…/basic.ics"),
                           Field("colour", "Colour", "select", default="#3CCFB4", options=COLOURS),
                           Field("headsUp", "Heads-up", "select", default="", options=(
                               {"value": "", "label": "No"},
                               {"value": "evening", "label": "Evening before"})))),
        Field("days", "Show events this many days ahead", "number", default=31, advanced=True),
        Field("refresh", "Check for changes every (minutes)", "number", default=15, advanced=True),
    ]

    @classmethod
    def validate(cls, config: dict[str, Any]) -> list[str]:
        errors = []
        cals = [c for c in config.get("calendars") or [] if c]
        if not cals:
            errors.append("Add at least one calendar link")
        for c in cals:
            if not str(c.get("name") or "").strip():
                errors.append("Give each calendar a name")
            url = str(c.get("url") or "")
            if not (url.startswith("https://") or url.startswith("http://") or url.startswith("webcal://")
                    or url == "••••"):
                errors.append(f"“{c.get('name') or 'A calendar'}” needs its https:// or webcal:// link")
        return errors

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._http = httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "Newman home hub"})
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._errors: dict[str, str] = {}
        self._last_tick = time.time()

    def _eid(self, cal: dict[str, Any]) -> str:
        return self.entity_id(slug(cal["name"]))

    async def run(self) -> None:
        cals = [c for c in self.config.get("calendars") or [] if c and c.get("url")]
        for c in cals:
            self.hub.upsert(self._eid(c), kind="calendar", name=c["name"], integration=self.id)
        refresh = max(5, int(self.config.get("refresh") or 15)) * 60
        next_fetch = 0.0
        while True:
            if time.time() >= next_fetch:
                await asyncio.gather(*(self._fetch(c) for c in cals))
                next_fetch = time.time() + refresh
                ok = len(cals) - len(self._errors)
                if self._errors:
                    self.set_status("error" if not ok else "connected",
                                    f"{ok} of {len(cals)} calendars · " + "; ".join(self._errors.values()))
                else:
                    self.set_status("connected", f"{len(cals)} calendar{'s' if len(cals) != 1 else ''}")
            await self._tick(cals)
            await asyncio.sleep(30)

    async def _fetch(self, cal: dict[str, Any]) -> None:
        url = str(cal["url"]).replace("webcal://", "https://", 1)
        days = max(31, int(self.config.get("days") or 31))
        now = datetime.now()
        try:
            r = await self._http.get(url)
            r.raise_for_status()
            events = await asyncio.to_thread(parse, r.text, now - timedelta(days=1), now + timedelta(days=days))
            self._events[cal["name"]] = events
            self._errors.pop(cal["name"], None)
        except Exception as e:
            self._errors[cal["name"]] = f"{cal['name']}: {'link not found' if '404' in str(e) else 'unreachable'}"
            log.warning("Calendar %s: %s", cal["name"], e)
            await self.hub.update(self._eid(cal), available=False)

    async def _tick(self, cals: list[dict[str, Any]]) -> None:
        """Every 30 s: what's on now, what's next, and "event starts" for automations."""
        now, since = datetime.now(), datetime.fromtimestamp(self._last_tick)
        self._last_tick = time.time()
        for c in cals:
            events = self._events.get(c["name"])
            if events is None:
                continue
            eid = self._eid(c)
            current = [e for e in events if not e["allDay"]
                       and datetime.fromisoformat(e["start"]) <= now < datetime.fromisoformat(e["end"])]
            upcoming = [e for e in events if datetime.fromisoformat(e["end"]) > now][:300]  # a month of busy calendars
            await self.hub.update(eid, available=c["name"] not in self._errors, state="busy" if current else "free",
                                  attrs={"upcoming": upcoming, "colour": c.get("colour") or "#3CCFB4", "headsUp": c.get("headsUp") or "",
                                         "now": current[0]["title"] if current else None})
            for e in events:
                s = datetime.fromisoformat(e["start"])
                if not e["allDay"] and since < s <= now:
                    await self.hub.emit(eid, "start", {"title": e["title"], "location": e["location"]})
