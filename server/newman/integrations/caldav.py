"""Calendar servers (CalDAV): GCloud, Nextcloud, Radicale, iCloud, Fastmail… - read AND write.

The connection signs in with a server address, a user name and a password (an app password where the
server has them) and finds every calendar that account can see the standard way: the server's
/.well-known/caldav (or the address as given) → the account (current-user-principal) → where its
calendars are (calendar-home-set) → each calendar in there (name, colour, whether this account may
change it). One calendar device per calendar, shaped exactly like the Calendars (iCal link) connection's
(attrs.upcoming/colour/now, state busy/free, a "start" event) so the calendar tile, Today and automations
work the same - plus, per event, what the wall needs to show and edit it (uid, its address on the server,
which occurrence of a repeating one, notes, link, reminders, its repeat rule).

Events come from a calendar-query for the window shown (yesterday … `days` ahead); a calendar whose
getctag hasn't changed isn't fetched again (except once a few hours, as the window moves on). The wall's
month screen asks for other days too (events_between: a calendar-query for those days, a past month too),
kept RANGE_KEEP_S unless the calendar is fetched again meanwhile. The server
can say "this calendar changed" by POSTing {"calendar": <its id>} to /api/public/calendars/changed (GCloud
does when its Admin › Calendars › Newman address is the hub's address). The message is only a nudge - the
calendar is fetched with the connection's own sign-in - so it needs no key; nudges closer together than
NUDGE_GAP_S are put together into one fetch.

Writing (the wall's add / edit / delete): an event is one VCALENDAR resource; new ones are PUT with
If-None-Match: *, changes with If-Match on the etag fetched with it (someone else changed it meanwhile:
refused, fetched again). Times are in the hub's time zone (TZ) with its VTIMEZONE; all-day events are
DATE values (DTEND the day after the last day). Changing one occurrence of a repeating event writes an
override (RECURRENCE-ID); deleting one adds an EXDATE. Everything not shown on the wall - reminders
(VALARM), attendees, other properties - is kept as it was.

Reminders on the wall: a calendar ticked in "Remind on the wall tablets" has each event's reminders
(VALARM times) shown as a card on every wall tablet ({"type": "calendarReminder"}); OK on any one
dismisses it on all ({"type": "calendarReminderDone"}).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote, urljoin, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import icalendar
import recurring_ical_events

from ..store import slug
from .base import CommandError, Field, Integration, check_every_field
from .calendar_ics import _local, window_attrs

log = logging.getLogger(__name__)

D, C, CS, IC = "DAV:", "urn:ietf:params:xml:ns:caldav", "http://calendarserver.org/ns/", "http://apple.com/ns/ical/"
DEFAULT_COLOUR = "#3CCFB4"
REFETCH_S = 6 * 3600       # an unchanged calendar is still fetched this often (the window moves on)
TICK_S = 30.0
NUDGE_GAP_S = 5.0          # "a calendar changed" nudges closer together than this become one fetch
REMINDER_KEEP_S = 2 * 3600  # a reminder nobody dismissed goes this long after its event starts
MAX_NOTES = 2000
RANGE_KEEP_S = 300         # other days asked for (the month screen) are asked of the server again after this
MAX_SEEN = 2000            # events from other days kept for opening / editing them, per calendar

HOME_PROPS = f"""<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="{D}" xmlns:c="{C}" xmlns:cs="{CS}" xmlns:ic="{IC}"><d:prop>
<d:resourcetype/><d:displayname/><ic:calendar-color/><c:supported-calendar-component-set/>
<cs:getctag/><d:sync-token/><d:current-user-privilege-set/>
</d:prop></d:propfind>"""


class SignInRefused(Exception):
    pass


def _q(ns: str, name: str) -> str:
    return "{%s}%s" % (ns, name)


def multistatus(body: bytes) -> list[tuple[str, dict[str, ET.Element]]]:
    """A 207 reply → [(href, {"{ns}name": element})] with only the properties the server had (200)."""
    out = []
    root = ET.fromstring(body)
    for resp in root.findall(_q(D, "response")):
        href = (resp.findtext(_q(D, "href")) or "").strip()
        props: dict[str, ET.Element] = {}
        for ps in resp.findall(_q(D, "propstat")):
            status = ps.findtext(_q(D, "status")) or ""
            if " 200" not in status:
                continue
            prop = ps.find(_q(D, "prop"))
            if prop is not None:
                for p in prop:
                    props[p.tag] = p
        out.append((href, props))
    return out


def _href_in(el: ET.Element | None) -> str | None:
    h = el.find(_q(D, "href")) if el is not None else None
    return (h.text or "").strip() if h is not None and h.text else None


def colour_of(text: str | None) -> str:
    """#RRGGBB from a server's colour (#RRGGBB or Apple's #RRGGBBAA); the default when there's none."""
    t = (text or "").strip()
    if len(t) in (7, 9) and t.startswith("#"):
        try:
            int(t[1:], 16)
            return t[:7].upper()
        except ValueError:
            pass
    return DEFAULT_COLOUR


def writable(props: dict[str, ET.Element]) -> bool:
    """May this account change the calendar's events? (current-user-privilege-set; a server that doesn't
    say is taken at its word when it accepts a write.)"""
    ps = props.get(_q(D, "current-user-privilege-set"))
    if ps is None:
        return True
    names = {child.tag for priv in ps.findall(_q(D, "privilege")) for child in priv}
    return bool(names & {_q(D, "all"), _q(D, "write"), _q(D, "write-content"), _q(D, "bind")})


def is_event_calendar(props: dict[str, ET.Element]) -> bool:
    rt = props.get(_q(D, "resourcetype"))
    if rt is None or rt.find(_q(C, "calendar")) is None:
        return False
    comps = props.get(_q(C, "supported-calendar-component-set"))
    if comps is None:
        return True
    return any((c.get("name") or "").upper() == "VEVENT" for c in comps.findall(_q(C, "comp")))


def hub_zone() -> ZoneInfo | None:
    try:
        return ZoneInfo(os.environ.get("TZ") or "")
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _minutes_before(alarm: Any, start: datetime | date, end: datetime | date) -> int | None:
    trig = alarm.get("TRIGGER")
    if trig is None:
        return None
    v = trig.dt
    if isinstance(v, timedelta):
        related_end = str(trig.params.get("RELATED", "")).upper() == "END"
        if related_end:
            base_s, base_e = _local(start)[0], _local(end)[0]
            return int(round(-(v + (base_e - base_s)).total_seconds() / 60))
        return int(round(-v.total_seconds() / 60))
    if isinstance(v, datetime):
        s = _local(start)[0]
        at = _local(v)[0]
        return int(round((s - at).total_seconds() / 60))
    return None


def occurrence_key(ev: Any) -> str:
    """Which occurrence this is: its RECURRENCE-ID (else DTSTART) as ISO - a date, or an aware/floating datetime."""
    rid = ev.get("RECURRENCE-ID") or ev.get("DTSTART")
    v = rid.dt
    return v.isoformat()


def parse_resource(text: str, href: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """One calendar object (a VCALENDAR) → its occurrences in [start, end), each the wall's event shape."""
    cal = icalendar.Calendar.from_ical(text)
    master = next((v for v in cal.walk("VEVENT") if v.get("RECURRENCE-ID") is None), None)
    rrule = master.get("RRULE").to_ical().decode() if master is not None and master.get("RRULE") is not None else ""
    repeats = bool(rrule) or (master is not None and master.get("RDATE") is not None)
    out = []
    for ev in recurring_ical_events.of(cal).between(start, end):
        try:
            s_raw = ev.get("DTSTART").dt
            s, all_day = _local(s_raw)
            e_raw = ev.get("DTEND")
            if e_raw is not None:
                e_val = e_raw.dt
            elif ev.get("DURATION") is not None:
                e_val = s_raw + ev.get("DURATION").dt
            else:
                e_val = s_raw + (timedelta(days=1) if all_day else timedelta(hours=1))
            e = _local(e_val)[0]
        except (AttributeError, ValueError, TypeError):
            continue
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        alarms = sorted({m for a in ev.walk("VALARM") if (m := _minutes_before(a, s_raw, e_val)) is not None}, reverse=True)
        out.append({
            "title": str(ev.get("SUMMARY") or "Busy"), "start": s.isoformat(), "end": e.isoformat(),
            "allDay": all_day, "location": str(ev.get("LOCATION") or ""),
            "uid": str(ev.get("UID") or ""), "href": href, "occurrence": occurrence_key(ev),
            "recurring": repeats,
            "rrule": rrule, "url": str(ev.get("URL") or ""),
            "notes": str(ev.get("DESCRIPTION") or "")[:MAX_NOTES], "alarms": alarms,
        })
    return out


# --- writing ----------------------------------------------------------------------------------------

def _parse_when(value: Any, all_day: bool, zone: ZoneInfo | None) -> date | datetime:
    s = str(value or "").strip()
    try:
        if all_day:
            return date.fromisoformat(s[:10])
        d = datetime.fromisoformat(s[:16])
    except ValueError as e:
        raise ValueError("A start or end isn't a date") from e
    return d.replace(tzinfo=zone) if zone else d


def event_fields(data: dict[str, Any], zone: ZoneInfo | None) -> dict[str, Any]:
    """What the wall sends → checked values: {title, allDay, start, end (DTEND), rrule, location, url, notes}.
    All day: start/end are the first and last day (inclusive). Timed: local "YYYY-MM-DDTHH:MM"."""
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("Give it a name first.")
    all_day = bool(data.get("allDay"))
    start = _parse_when(data.get("start"), all_day, zone)
    end = _parse_when(data.get("end") or data.get("start"), all_day, zone)
    if all_day:
        if end < start:
            raise ValueError("It ends before it starts — pick a later end.")
        end = end + timedelta(days=1)
    elif end <= start:
        raise ValueError("It ends before it starts — pick a later end.")
    rrule = str(data.get("rrule") or "").strip().removeprefix("RRULE:")
    if rrule:
        try:
            parsed = icalendar.vRecur.from_ical(rrule)
        except (ValueError, TypeError) as e:
            raise ValueError("That repeat isn't one Newman understands") from e
        if not parsed.get("FREQ"):
            raise ValueError("That repeat isn't one Newman understands")
    url = str(data.get("url") or "").strip()
    if url and urlparse(url).scheme not in ("http", "https"):
        raise ValueError("The link must start with http:// or https://")
    return {"title": title[:500], "allDay": all_day, "start": start, "end": end, "rrule": rrule,
            "location": str(data.get("location") or "").strip()[:500], "url": url[:2000],
            "notes": str(data.get("notes") or "").strip()[:MAX_NOTES]}


def _stamp(ev: Any) -> None:
    now = datetime.now(timezone.utc)
    for k in ("DTSTAMP", "LAST-MODIFIED"):
        ev.pop(k, None)
        ev.add(k, now)


def _apply(ev: Any, f: dict[str, Any], with_rule: bool) -> None:
    for k in ("SUMMARY", "DTSTART", "DTEND", "DURATION", "LOCATION", "URL", "DESCRIPTION"):
        ev.pop(k, None)
    ev.add("SUMMARY", f["title"])
    ev.add("DTSTART", f["start"])
    ev.add("DTEND", f["end"])
    for key, prop in (("location", "LOCATION"), ("url", "URL"), ("notes", "DESCRIPTION")):
        if f.get(key):
            ev.add(prop, f[key])
    if with_rule:
        ev.pop("RRULE", None)
        if f["rrule"]:
            ev.add("RRULE", icalendar.vRecur.from_ical(f["rrule"]))
    ev.pop("SEQUENCE", None)
    _stamp(ev)


def _finish(cal: Any) -> str:
    for tz in list(cal.walk("VTIMEZONE")):
        cal.subcomponents.remove(tz)
    cal.add_missing_timezones()
    return cal.to_ical().decode()


def new_object(f: dict[str, Any], uid: str | None = None) -> tuple[str, str]:
    """(uid, VCALENDAR text) for a new event."""
    uid = uid or f"{uuid.uuid4()}@newman"
    cal = icalendar.Calendar()
    cal.add("PRODID", "-//Newman//Calendar//EN")
    cal.add("VERSION", "2.0")
    ev = icalendar.Event()
    ev.add("UID", uid)
    _apply(ev, f, with_rule=True)
    cal.add_component(ev)
    return uid, _finish(cal)


def _master(cal: Any) -> Any:
    m = next((v for v in cal.walk("VEVENT") if v.get("RECURRENCE-ID") is None), None)
    if m is None:
        raise ValueError("That event isn't on the calendar any more")
    return m


def _same_moment(a: date | datetime, b: date | datetime) -> bool:
    if isinstance(a, datetime) and isinstance(b, datetime):
        if (a.tzinfo is None) != (b.tzinfo is None):
            a, b = a.replace(tzinfo=None), b.replace(tzinfo=None)
        return a == b
    if isinstance(a, datetime) or isinstance(b, datetime):
        return False
    return a == b


def _occurrence_value(master: Any, occurrence: str) -> date | datetime:
    """The occurrence's start as the master's DTSTART would write it (a date, or a datetime in its zone)."""
    ref = master.get("DTSTART").dt
    try:
        if not isinstance(ref, datetime):
            return date.fromisoformat(occurrence[:10])
        v = datetime.fromisoformat(occurrence)
    except ValueError as e:
        raise ValueError("Which time of the repeating event is that?") from e
    if ref.tzinfo is not None:
        return (v if v.tzinfo else v.replace(tzinfo=ref.tzinfo)).astimezone(ref.tzinfo)
    return v.replace(tzinfo=None)


def edit_object(text: str, f: dict[str, Any], scope: str, occurrence: str | None) -> str:
    """The changed VCALENDAR: scope "all" changes the event (the master of a repeating one), "this" one
    occurrence (an override with RECURRENCE-ID). Reminders and anything else in it stay."""
    cal = icalendar.Calendar.from_ical(text)
    master = _master(cal)
    if scope == "this" and occurrence:
        rid = _occurrence_value(master, occurrence)
        target = next((v for v in cal.walk("VEVENT") if v.get("RECURRENCE-ID") is not None
                       and _same_moment(v.get("RECURRENCE-ID").dt, rid)), None)
        if target is None:
            target = icalendar.Event()
            for k, v in master.property_items(recursive=False):
                if k in ("BEGIN", "END", "RRULE", "RDATE", "EXDATE"):
                    continue
                target.add(k, v)
            for alarm in master.walk("VALARM"):
                target.add_component(alarm)
            target.add("RECURRENCE-ID", rid)
            cal.add_component(target)
        _apply(target, {**f, "rrule": ""}, with_rule=False)
        return _finish(cal)
    old_start = master.get("DTSTART").dt
    _apply(master, f, with_rule=True)
    if not _same_moment(old_start, f["start"]) or not f["rrule"]:
        # Its times moved (or it no longer repeats): changes made to single times of the old pattern go.
        for v in [v for v in cal.walk("VEVENT") if v.get("RECURRENCE-ID") is not None]:
            cal.subcomponents.remove(v)
        master.pop("EXDATE", None)
    return _finish(cal)


def drop_occurrence(text: str, occurrence: str) -> str:
    """The VCALENDAR without one occurrence of a repeating event (an EXDATE; its override, if any, goes)."""
    cal = icalendar.Calendar.from_ical(text)
    master = _master(cal)
    rid = _occurrence_value(master, occurrence)
    for v in [v for v in cal.walk("VEVENT") if v.get("RECURRENCE-ID") is not None and _same_moment(v.get("RECURRENCE-ID").dt, rid)]:
        cal.subcomponents.remove(v)
    master.add("EXDATE", rid)
    _stamp(master)
    return _finish(cal)


def form_of(text: str, occurrence: str | None, scope: str) -> dict[str, Any]:
    """An event as the wall's form fills in (the occurrence's own values for "this", the master's for "all")."""
    cal = icalendar.Calendar.from_ical(text)
    master = _master(cal)
    ev = master
    if scope == "this" and occurrence:
        rid = _occurrence_value(master, occurrence)
        ev = next((v for v in cal.walk("VEVENT") if v.get("RECURRENCE-ID") is not None
                   and _same_moment(v.get("RECURRENCE-ID").dt, rid)), None)
        if ev is None:  # an occurrence of the pattern: the master's values moved to this time
            length = _end_of(master) - master.get("DTSTART").dt
            return _form(master, rid, rid + length)
    return _form(ev, ev.get("DTSTART").dt, _end_of(ev))


def _end_of(ev: Any) -> date | datetime:
    if ev.get("DTEND") is not None:
        return ev.get("DTEND").dt
    s = ev.get("DTSTART").dt
    if ev.get("DURATION") is not None:
        return s + ev.get("DURATION").dt
    return s + (timedelta(hours=1) if isinstance(s, datetime) else timedelta(days=1))


def _form(ev: Any, s: date | datetime, e: date | datetime) -> dict[str, Any]:
    all_day = not isinstance(s, datetime)
    if all_day:
        start, end = s.isoformat(), (e - timedelta(days=1)).isoformat()
    else:
        start, end = _local(s)[0].isoformat()[:16], _local(e)[0].isoformat()[:16]
    rule = ev.get("RRULE")
    return {"title": str(ev.get("SUMMARY") or ""), "allDay": all_day, "start": start, "end": end,
            "rrule": rule.to_ical().decode() if rule is not None else "",
            "location": str(ev.get("LOCATION") or ""), "url": str(ev.get("URL") or ""),
            "notes": str(ev.get("DESCRIPTION") or "")}


class CalDavIntegration(Integration):
    type = "caldav"
    name = "Calendar server (CalDAV)"
    category = "other"
    provides = ("calendar",)
    transport: Any = None  # tests: an httpx transport standing in for the server
    description = ("Your own calendar server - GCloud, Nextcloud, Radicale, iCloud… Every calendar the account can "
                   "see shows on Newman, and the wall tablets can add and change events.")
    fields = [
        Field("url", "Server address", required=True, placeholder="https://share.example.com/caldav/"),
        Field("username", "User name", required=True),
        Field("password", "Password", "password", required=True,
              help="An app password for this account, where the server has them."),
        Field("remind", "Remind on the wall tablets", "multiselect", dynamic=True, default=[],
              help="Each event's reminders show as a card on every wall tablet."),
        Field("days", "Show events this many days ahead", "number", default=31, advanced=True, minimum=1),
        check_every_field(15, 5, help="The server can also tell Newman about a change as it happens; "
                                      "this checks every calendar again now and then."),
    ]

    @classmethod
    def validate(cls, config: dict[str, Any]) -> list[str]:
        errors = super().validate(config)
        url = str(config.get("url") or "").strip()
        if url and urlparse(url).scheme not in ("http", "https"):
            errors.append("The server address must start with https:// or http://")
        return errors

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._http: httpx.AsyncClient | None = None
        self._cals: dict[str, dict[str, Any]] = {}       # entity id -> {id, href, name, colour, writable, ctag}
        self._objects: dict[str, dict[str, dict[str, str]]] = {}  # entity id -> href -> {"etag", "ics"}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._fetched: dict[str, float] = {}
        self._until: dict[str, datetime] = {}           # entity id -> the end of the window fetched
        self._ranges: dict[tuple[str, date, date], tuple[float, list[dict[str, Any]]]] = {}  # other days asked for
        self._seen: dict[str, dict[str, dict[str, str]]] = {}  # entity id -> href -> {"etag", "ics"} from other days
        self._errors: dict[str, str] = {}
        self._wake = asyncio.Event()
        self._wanted: set[str] = set()   # calendar ids the server said changed ("*" = all / unknown)
        self._nudged_at = 0.0
        self._nudge_later: asyncio.TimerHandle | None = None
        self._last_tick = time.time()
        self._reminders: dict[str, dict[str, Any]] = {}  # id -> the card, while it's up
        self._done: set[str] = set()
        self._checked_at: float | None = None
        self.zone = hub_zone()

    # -- the connection ------------------------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=30, follow_redirects=True, headers={"User-Agent": "Newman home hub"}, transport=self.transport,
                auth=httpx.BasicAuth(str(self.config.get("username") or ""), str(self.config.get("password") or "")))
        return self._http

    async def _dav(self, method: str, url: str, body: str | bytes | None = None, depth: str | None = None,
                   headers: dict[str, str] | None = None) -> httpx.Response:
        h = dict(headers or {})
        if depth is not None:
            h["Depth"] = depth
        if body is not None and "Content-Type" not in h:
            h["Content-Type"] = "application/xml; charset=utf-8"
        r = await self._client().request(method, url, content=body, headers=h)
        if r.status_code == 401:
            raise SignInRefused()
        return r

    async def _propfind(self, url: str, props: str, depth: str = "0") -> tuple[list[tuple[str, dict[str, ET.Element]]], str]:
        body = f'<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="{D}" xmlns:c="{C}"><d:prop>{props}</d:prop></d:propfind>'
        r = await self._dav("PROPFIND", url, body, depth)
        if r.status_code != 207:
            raise ConnectionError(f"{urlparse(url).path or '/'} answered {r.status_code}")
        return multistatus(r.content), str(r.url)

    async def discover(self) -> None:
        """The account's calendars, the standard way. Keeps devices for calendars still there."""
        given = str(self.config.get("url") or "").strip()
        p = urlparse(given)
        start = given if p.path not in ("", "/") else f"{p.scheme}://{p.netloc}/.well-known/caldav"
        found, base = await self._propfind(start, "<d:current-user-principal/>")
        principal = next((_href_in(props.get(_q(D, "current-user-principal"))) for _h, props in found
                          if props.get(_q(D, "current-user-principal")) is not None), None)
        principal_url = urljoin(base, principal) if principal else base
        found, base = await self._propfind(principal_url, "<c:calendar-home-set/>")
        home = next((_href_in(props.get(_q(C, "calendar-home-set"))) for _h, props in found
                     if props.get(_q(C, "calendar-home-set")) is not None), None)
        home_url = urljoin(base, home) if home else principal_url
        r = await self._dav("PROPFIND", home_url, HOME_PROPS, "1")
        if r.status_code != 207:
            raise ConnectionError(f"the calendar list answered {r.status_code}")
        cals: dict[str, dict[str, Any]] = {}
        for href, props in multistatus(r.content):
            if not is_event_calendar(props):
                continue
            url = urljoin(str(r.url), href)
            cid = unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
            eid = self.entity_id(slug(cid) or slug(url))
            name = (props.get(_q(D, "displayname")).text or "").strip() if props.get(_q(D, "displayname")) is not None else ""
            ctag_el = props.get(_q(CS, "getctag")) if props.get(_q(CS, "getctag")) is not None else props.get(_q(D, "sync-token"))
            cals[eid] = {"id": cid, "href": url if url.endswith("/") else url + "/", "name": name or cid,
                         "colour": colour_of(props.get(_q(IC, "calendar-color")).text if props.get(_q(IC, "calendar-color")) is not None else None),
                         "writable": writable(props), "ctag": (ctag_el.text or "") if ctag_el is not None else ""}
        for eid, c in cals.items():
            self.hub.upsert(eid, kind="calendar", name=c["name"], integration=self.id)
            old = self._cals.get(eid)
            if old and old.get("ctag") and old["ctag"] == c["ctag"] and eid in self._events:
                c["unchanged"] = True
        for gone in set(self._cals) - set(cals):
            self._events.pop(gone, None)
            self._objects.pop(gone, None)
            self._seen.pop(gone, None)
            await self.hub.update(gone, available=False)
        self._cals = cals

    async def fetch(self, eid: str, force: bool = False) -> None:
        cal = self._cals.get(eid)
        if not cal:
            return
        if not force and cal.get("unchanged") and time.time() - self._fetched.get(eid, 0) < REFETCH_S:
            return
        days = max(1, int(self.config.get("days") or 31))
        now = datetime.now()
        lo, hi = now - timedelta(days=1), now + timedelta(days=days)
        try:
            objects, events = await self._query(cal, lo, hi)
            self._objects[eid] = objects
            self._events[eid] = events
            self._until[eid] = hi
            self._fetched[eid] = time.time()
            self._errors.pop(eid, None)
        except SignInRefused:
            raise
        except Exception as e:
            self._errors[eid] = f"{cal['name']}: couldn't be read"
            log.warning("Calendar %s: %s", cal["name"], e)

    async def _query(self, cal: dict[str, Any], lo: datetime, hi: datetime) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
        """A calendar-query for [lo, hi) → ({href: {"etag", "ics"}}, its occurrences in it, by start)."""
        z = lambda d: d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: E731
        body = (f'<?xml version="1.0" encoding="utf-8"?><c:calendar-query xmlns:d="{D}" xmlns:c="{C}">'
                f"<d:prop><d:getetag/><c:calendar-data/></d:prop><c:filter><c:comp-filter name=\"VCALENDAR\">"
                f'<c:comp-filter name="VEVENT"><c:time-range start="{z(lo)}" end="{z(hi)}"/></c:comp-filter>'
                f"</c:comp-filter></c:filter></c:calendar-query>")
        r = await self._dav("REPORT", cal["href"], body, "1")
        if r.status_code != 207:
            raise ConnectionError(f"{cal['name']} answered {r.status_code}")
        objects: dict[str, dict[str, str]] = {}
        for href, props in multistatus(r.content):
            data = props.get(_q(C, "calendar-data"))
            if data is None or not (data.text or "").strip():
                continue
            etag = props.get(_q(D, "getetag"))
            objects[urljoin(cal["href"], href)] = {"etag": (etag.text or "").strip() if etag is not None else "",
                                                   "ics": data.text}
        events: list[dict[str, Any]] = []

        def parse_all() -> None:
            for href, o in objects.items():
                try:
                    events.extend(parse_resource(o["ics"], href, lo, hi))
                except Exception as e:  # one bad event mustn't hide the rest
                    log.warning("Calendar %s: an event couldn't be read (%s)", cal["name"], e)

        await asyncio.to_thread(parse_all)
        events.sort(key=lambda x: x["start"])
        return objects, events

    async def events_between(self, eid: str, start: date, end: date) -> list[dict[str, Any]]:
        """The calendar's events from `start` up to `end` (days; a past month too) in the attrs.upcoming
        shape, for the wall's month screen. Asked of the server again after RANGE_KEEP_S, or sooner when
        the calendar was fetched since (a change). Its events can be opened and edited like the window's."""
        cal = self._calendar(eid, change=False)
        key = (eid, start, end)
        hit = self._ranges.get(key)
        if hit and hit[0] >= self._fetched.get(eid, 0) and time.time() - hit[0] < RANGE_KEEP_S:
            return hit[1]
        lo, hi = datetime(start.year, start.month, start.day), datetime(end.year, end.month, end.day)
        try:
            objects, events = await self._query(cal, lo, hi)
        except SignInRefused as e:
            raise CommandError("The calendar server didn't accept Newman's password") from e
        except (httpx.HTTPError, ConnectionError) as e:
            raise CommandError(f"Couldn't read {cal['name']} from the calendar server") from e
        now = time.time()
        self._ranges = {k: v for k, v in self._ranges.items() if now - v[0] < RANGE_KEEP_S}
        self._ranges[key] = (now, events)
        seen = self._seen.setdefault(eid, {})
        if len(seen) + len(objects) > MAX_SEEN:
            seen.clear()
        seen.update(objects)
        return events

    async def refresh(self, which: set[str] | None = None) -> None:
        """Look again: the calendar list, then the events of `which` calendar ids (all when None)."""
        await self.discover()
        targets = [eid for eid, c in self._cals.items() if which is None or c["id"] in which]
        await asyncio.gather(*(self.fetch(eid, force=which is not None) for eid in targets))
        self._checked_at = time.time()
        n = len(self._cals)
        if self._errors:
            self.set_status("connected" if len(self._errors) < n else "error",
                            f"{n - len(self._errors)} of {n} calendars · " + "; ".join(self._errors.values()))
        else:
            self.set_status("connected", f"{n} calendar{'s' if n != 1 else ''}" if n else "No calendars shared with this account")
        await self._publish()

    async def run(self) -> None:
        attempt = 0
        every = self.seconds_every("check_minutes")
        while True:
            try:
                await self.refresh()
                attempt = 0
                next_full = time.time() + every
                while True:
                    try:
                        await asyncio.wait_for(self._wake.wait(), TICK_S)
                    except asyncio.TimeoutError:
                        pass
                    if self._wake.is_set():
                        self._wake.clear()
                        wanted, self._wanted = self._wanted, set()
                        await self.refresh(None if "*" in wanted or not wanted else wanted)
                    if time.time() >= next_full:
                        await self.refresh()
                        next_full = time.time() + every
                    await self._tick()
            except SignInRefused:
                self.set_status("error", "The server didn't accept the user name or password")
                await asyncio.sleep(600)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                attempt += 1
                log.warning("CalDAV %s: %s", self.display_name, e)
                self.set_status("error", f"Can't reach the calendar server ({e.__class__.__name__})")
                await self.backoff_sleep(attempt)

    def _remind_ids(self) -> set[str]:
        return {str(x) for x in (self.config.get("remind") or [])}

    async def _publish(self) -> None:
        await self._tick(publish_only=True)

    async def _tick(self, publish_only: bool = False) -> None:
        """What's on now, the next events, "event starts" for automations, and wall reminders due."""
        now, since = datetime.now(), datetime.fromtimestamp(self._last_tick)
        if not publish_only:
            self._last_tick = time.time()
        remind = self._remind_ids()
        for eid, c in self._cals.items():
            events = self._events.get(eid)
            if events is None:
                continue
            current = [e for e in events if not e["allDay"]
                       and datetime.fromisoformat(e["start"]) <= now < datetime.fromisoformat(e["end"])]
            later = [e for e in events if datetime.fromisoformat(e["end"]) > now]
            upcoming = later[:300]
            await self.hub.update(eid, available=eid not in self._errors, state="busy" if current else "free",
                                  attrs={"upcoming": upcoming, "colour": c["colour"], "headsUp": "",
                                         "now": current[0]["title"] if current else None,
                                         "readOnly": not c["writable"], "calendarId": c["id"],
                                         "remindOnWall": c["id"] in remind,
                                         **window_attrs(self._until.get(eid), later, upcoming, self._fetched.get(eid))})
            if publish_only:
                continue
            for e in events:
                s = datetime.fromisoformat(e["start"])
                if not e["allDay"] and since < s <= now:
                    await self.hub.emit(eid, "start", {"title": e["title"], "location": e["location"]})
                if c["id"] in remind:
                    for m in e.get("alarms") or []:
                        at = s - timedelta(minutes=m)
                        if since < at <= now:
                            await self._remind(eid, c, e, m)
        if not publish_only:
            cutoff = time.time() - REMINDER_KEEP_S
            for rid in [rid for rid, r in self._reminders.items() if r["startsAt"] < cutoff]:
                self._reminders.pop(rid, None)

    async def _remind(self, eid: str, c: dict[str, Any], e: dict[str, Any], minutes: int) -> None:
        rid = f"{eid}|{e['uid']}|{e['occurrence']}|{minutes}"
        if rid in self._reminders or rid in self._done:
            return
        card = {"id": rid, "title": e["title"], "start": e["start"], "end": e["end"], "allDay": e["allDay"],
                "location": e["location"], "calendar": c["name"], "colour": c["colour"], "minutes": minutes,
                "startsAt": datetime.fromisoformat(e["start"]).timestamp()}
        self._reminders[rid] = card
        if self.ctx.broadcast:
            await self.ctx.broadcast({"type": "calendarReminder", "reminder": card})

    def reminders(self) -> list[dict[str, Any]]:
        return list(self._reminders.values())

    async def dismiss_reminder(self, rid: str) -> bool:
        if self._reminders.pop(rid, None) is None:
            return False
        self._done.add(rid)
        if self.ctx.broadcast:
            await self.ctx.broadcast({"type": "calendarReminderDone", "id": rid})
        return True

    # -- the server telling us about a change --------------------------------------------------------

    def receive_change(self, body: dict[str, Any]) -> None:
        """A nudge: fetch that calendar (all of them when it's not one of ours) - at most once per NUDGE_GAP_S."""
        cid = str((body or {}).get("calendar") or "").strip()
        self._wanted.add(cid if cid and any(c["id"] == cid for c in self._cals.values()) else "*")
        wait = NUDGE_GAP_S - (time.time() - self._nudged_at)
        if wait <= 0:
            self._wake_now()
        elif self._nudge_later is None:
            self._nudge_later = asyncio.get_running_loop().call_later(wait, self._wake_now)

    def _wake_now(self) -> None:
        self._nudge_later = None
        self._nudged_at = time.time()
        self._wake.set()

    async def dynamic_options(self) -> dict[str, list[dict[str, Any]]]:
        return {"remind": [{"value": c["id"], "label": c["name"]} for c in self._cals.values()]}

    # -- events from the wall ------------------------------------------------------------------------

    def _calendar(self, eid: str, change: bool) -> dict[str, Any]:
        c = self._cals.get(eid)
        if not c:
            raise CommandError("That calendar isn't on the server any more")
        if change and not c["writable"]:
            raise CommandError(f"{c['name']} can only be viewed")
        return c

    def _object(self, eid: str, href: str) -> dict[str, str]:
        o = (self._objects.get(eid) or {}).get(href) or (self._seen.get(eid) or {}).get(href)
        if not o:
            raise CommandError("That event isn't on the calendar any more")
        return o

    async def _write(self, method: str, url: str, text: str | None, headers: dict[str, str]) -> None:
        h = {**headers}
        if text is not None:
            h["Content-Type"] = "text/calendar; charset=utf-8"
        try:
            r = await self._dav(method, url, text.encode() if text is not None else None, headers=h)
        except SignInRefused as e:
            raise CommandError("The calendar server didn't accept Newman's password") from e
        except httpx.HTTPError as e:
            raise CommandError(f"Couldn't reach the calendar server ({e.__class__.__name__})") from e
        if r.status_code == 412:
            raise CommandError("Someone changed it at the same time - look again and try once more")
        if r.status_code == 403:
            raise CommandError("The calendar server didn't allow that change")
        if r.status_code >= 300:
            raise CommandError(f"The calendar server answered {r.status_code}")

    async def event_form(self, eid: str, href: str, occurrence: str | None, scope: str) -> dict[str, Any]:
        self._calendar(eid, change=False)
        o = self._object(eid, href)
        return await asyncio.to_thread(form_of, o["ics"], occurrence, scope)

    async def add_event(self, eid: str, data: dict[str, Any]) -> dict[str, Any]:
        c = self._calendar(eid, change=True)
        f = event_fields(data, self.zone)
        uid, text = new_object(f)
        url = c["href"] + uid.replace("@", "-") + ".ics"
        await self._write("PUT", url, text, {"If-None-Match": "*"})
        await self.fetch(eid, force=True)
        await self._publish()
        return {"calendar": c["name"], "uid": uid}

    async def change_event(self, eid: str, href: str, data: dict[str, Any], scope: str, occurrence: str | None,
                           move_to: str | None = None) -> dict[str, Any]:
        c = self._calendar(eid, change=True)
        o = self._object(eid, href)
        f = event_fields(data, self.zone)
        if move_to and move_to != eid and scope != "this":
            target = self._calendar(move_to, change=True)
            text = await asyncio.to_thread(edit_object, o["ics"], f, "all", None)
            uid = str(_master(icalendar.Calendar.from_ical(text)).get("UID"))
            await self._write("PUT", target["href"] + uid.replace("@", "-") + ".ics", text, {"If-None-Match": "*"})
            await self._write("DELETE", href, None, {"If-Match": o["etag"]} if o["etag"] else {})
            await asyncio.gather(self.fetch(eid, force=True), self.fetch(move_to, force=True))
            await self._publish()
            return {"calendar": target["name"]}
        text = await asyncio.to_thread(edit_object, o["ics"], f, scope, occurrence)
        await self._write("PUT", href, text, {"If-Match": o["etag"]} if o["etag"] else {})
        await self.fetch(eid, force=True)
        await self._publish()
        return {"calendar": c["name"]}

    async def delete_event(self, eid: str, href: str, scope: str, occurrence: str | None) -> dict[str, Any]:
        c = self._calendar(eid, change=True)
        o = self._object(eid, href)
        cond = {"If-Match": o["etag"]} if o["etag"] else {}
        if scope == "this" and occurrence:
            text = await asyncio.to_thread(drop_occurrence, o["ics"], occurrence)
            await self._write("PUT", href, text, cond)
        else:
            await self._write("DELETE", href, None, cond)
        await self.fetch(eid, force=True)
        await self._publish()
        return {"calendar": c["name"]}
