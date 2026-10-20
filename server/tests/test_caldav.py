"""Calendar servers (CalDAV), offline against a fake server: finding the calendars (names, colours,
view-only), events read with their repeats, adding / changing / deleting (one time of a repeating event
too), reminders kept on edit, the end-before-start and no-name checks, a view-only calendar refused, the
server's "this calendar changed" call, and reminder cards on the wall."""
import asyncio
import os
import re
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

os.environ.setdefault("TOKEN_SECRET", "test-secret")
os.environ["TZ"] = "America/Toronto"
import time  # noqa: E402
time.tzset()

import httpx  # noqa: E402
import icalendar  # noqa: E402
import recurring_ical_events  # noqa: E402

from newman.integrations import REGISTRY  # noqa: E402
from newman.integrations import caldav as CD  # noqa: E402
from newman.integrations.base import CommandError, Context  # noqa: E402
from newman.state import Hub  # noqa: E402

D, C = "DAV:", "urn:ietf:params:xml:ns:caldav"
FAM, CHORES = "11111111-aaaa", "22222222-bbbb"


def ics(uid, summary, start, end, rrule="", alarm=""):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:test\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:20260101T000000Z\r\nSUMMARY:{summary}\r\n"
            f"DTSTART;TZID=America/Toronto:{start}\r\nDTEND;TZID=America/Toronto:{end}\r\n"
            + (f"RRULE:{rrule}\r\n" if rrule else "")
            + (f"BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:x\r\nTRIGGER:{alarm}\r\nEND:VALARM\r\n" if alarm else "")
            + "END:VEVENT\r\nEND:VCALENDAR\r\n")


class FakeServer:
    """GCloud's shape: /.well-known/caldav → /caldav/, principal /caldav/newman/, calendars under it."""

    def __init__(self):
        self.objects = {FAM: {}, CHORES: {}}  # cal id -> name -> (etag, text)
        self.n = 0
        self.calls = []
        self.ranges = []  # each REPORT's time-range (UTC)
        self.password = "app-pw"

    def put(self, cal, name, text):
        self.n += 1
        self.objects[cal][name] = (f'"e{self.n}"', text)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.calls.append((req.method, path))
        if req.headers.get("authorization") != httpx.BasicAuth("newman", self.password)._auth_header:
            return httpx.Response(401)
        ms = lambda inner: httpx.Response(207, content=(f'<d:multistatus xmlns:d="{D}" xmlns:c="{C}" '  # noqa: E731
                                                         f'xmlns:cs="http://calendarserver.org/ns/" xmlns:ic="http://apple.com/ns/ical/">{inner}</d:multistatus>').encode())
        ok = lambda props: f"<d:propstat><d:prop>{props}</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>"  # noqa: E731
        if req.method == "PROPFIND" and path == "/.well-known/caldav":
            return httpx.Response(301, headers={"Location": "/caldav/"})
        if req.method == "PROPFIND" and path == "/caldav/":
            return ms(f"<d:response><d:href>/caldav/</d:href>{ok('<d:current-user-principal><d:href>/caldav/newman/</d:href></d:current-user-principal>')}</d:response>")
        if req.method == "PROPFIND" and path == "/caldav/newman/" and req.headers.get("depth") == "0":
            return ms(f"<d:response><d:href>/caldav/newman/</d:href>{ok('<c:calendar-home-set><d:href>/caldav/newman/</d:href></c:calendar-home-set>')}</d:response>")
        if req.method == "PROPFIND" and path == "/caldav/newman/":
            def cal(cid, name, colour, write):
                privs = "<d:privilege><d:read/></d:privilege>" + ("<d:privilege><d:write/></d:privilege>" if write else "")
                tag = "".join(e for e, _ in self.objects[cid].values())
                return (f"<d:response><d:href>/caldav/newman/{cid}/</d:href>"
                        + ok(f"<d:resourcetype><d:collection/><c:calendar/></d:resourcetype><d:displayname>{name}</d:displayname>"
                             f"<ic:calendar-color>{colour}</ic:calendar-color><c:supported-calendar-component-set><c:comp name=\"VEVENT\"/></c:supported-calendar-component-set>"
                             f"<cs:getctag>{tag}</cs:getctag><d:current-user-privilege-set>{privs}</d:current-user-privilege-set>")
                        + "</d:response>")
            home = f"<d:response><d:href>/caldav/newman/</d:href>{ok('<d:resourcetype><d:collection/></d:resourcetype>')}</d:response>"
            return ms(home + cal(FAM, "Family", "#34D399FF", True) + cal(CHORES, "Chores", "#F59E0BFF", False))
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "caldav" and parts[2] in self.objects:
            cid = parts[2]
            if req.method == "REPORT":
                m = re.search(r'time-range start="(\w+)" end="(\w+)"', req.content.decode())
                self.ranges.append(m.groups())
                lo, hi = (datetime.strptime(x, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc) for x in m.groups())
                inner = "".join(f"<d:response><d:href>/caldav/newman/{cid}/{n}</d:href>"
                                + ok(f"<d:getetag>{e}</d:getetag><c:calendar-data>{t.replace('&', '&amp;').replace('<', '&lt;')}</c:calendar-data>")
                                + "</d:response>" for n, (e, t) in self.objects[cid].items()
                                if recurring_ical_events.of(icalendar.Calendar.from_ical(t)).between(lo, hi))
                return ms(inner)
            name = parts[3] if len(parts) > 3 else ""
            if cid == CHORES and req.method in ("PUT", "DELETE"):
                return httpx.Response(403)
            if req.method == "PUT":
                have = self.objects[cid].get(name)
                if req.headers.get("if-none-match") == "*" and have:
                    return httpx.Response(412)
                if req.headers.get("if-match") and (not have or have[0] != req.headers["if-match"]):
                    return httpx.Response(412)
                self.put(cid, name, req.content.decode())
                return httpx.Response(201 if not have else 204)
            if req.method == "DELETE":
                have = self.objects[cid].get(name)
                if not have:
                    return httpx.Response(404)
                if req.headers.get("if-match") and have[0] != req.headers["if-match"]:
                    return httpx.Response(412)
                del self.objects[cid][name]
                return httpx.Response(204)
        return httpx.Response(404)


class CalDavTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = FakeServer()
        CD.CalDavIntegration.transport = httpx.MockTransport(self.server)
        self.sent = []
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
        self.day = tomorrow
        self.server.put(FAM, "dinner.ics", ics("dinner", "Dinner at Nonna's", f"{tomorrow}T180000", f"{tomorrow}T190000", alarm="-PT30M"))
        self.server.put(FAM, "piano.ics", ics("piano", "Piano", f"{tomorrow}T163000", f"{tomorrow}T171500", rrule="FREQ=DAILY;COUNT=5"))
        self.server.put(CHORES, "bins.ics", ics("bins", "Bins out", f"{tomorrow}T190000", f"{tomorrow}T191500"))

    def tearDown(self):
        CD.CalDavIntegration.transport = None
        self.tmp.cleanup()

    def make(self, config=None):
        async def broadcast(msg):
            self.sent.append(msg)
        hub = Hub()
        ctx = Context(hub=hub, data_dir=Path(self.tmp.name), snapshot_dir=Path(self.tmp.name))
        ctx.broadcast = broadcast
        cfg = {"url": "https://cal.example.com", "username": "newman", "password": "app-pw"}
        inst = CD.CalDavIntegration("caldav", "GCloud", {**cfg, **(config or {})}, ctx)
        inst.save_config = lambda c: None
        return hub, inst

    def run_(self, coro):
        return asyncio.run(coro)

    def eid(self, inst, cid):
        return next(eid for eid, c in inst._cals.items() if c["id"] == cid)

    def events(self, hub, eid):
        return hub.get(eid).attrs["upcoming"]

    def test_catalog(self):
        j = REGISTRY["caldav"].catalog_json()
        keys = [f["key"] for f in j["fields"]]
        self.assertEqual(keys[:3], ["url", "username", "password"])
        self.assertIn("calendar", j["provides"])

    def test_finds_calendars_and_events(self):
        hub, inst = self.make()
        self.run_(inst.refresh())
        fam, chores = self.eid(inst, FAM), self.eid(inst, CHORES)
        self.assertEqual(hub.get(fam).name, "Family")
        self.assertEqual(hub.get(fam).attrs["colour"], "#34D399")
        self.assertFalse(hub.get(fam).attrs["readOnly"])
        self.assertTrue(hub.get(chores).attrs["readOnly"])
        evs = self.events(hub, fam)
        self.assertEqual([e["title"] for e in evs].count("Piano"), 5)
        dinner = next(e for e in evs if e["title"] == "Dinner at Nonna's")
        self.assertEqual(dinner["alarms"], [30])
        self.assertTrue(dinner["href"].endswith(f"/{FAM}/dinner.ics"))
        self.assertFalse(dinner["recurring"])
        piano = next(e for e in evs if e["title"] == "Piano")
        self.assertTrue(piano["recurring"])
        self.assertEqual(piano["rrule"], "FREQ=DAILY;COUNT=5")
        self.assertIn(("PROPFIND", "/.well-known/caldav"), self.server.calls)

    def test_wrong_password(self):
        hub, inst = self.make({"password": "nope"})
        with self.assertRaises(CD.SignInRefused):
            self.run_(inst.refresh())

    def test_add_timed_and_all_day(self):
        hub, inst = self.make()
        async def go():
            await inst.refresh()
            fam = self.eid(inst, FAM)
            out = await inst.add_event(fam, {"title": "Soccer", "start": f"{self.iso()}T17:00", "end": f"{self.iso()}T18:30",
                                             "location": "Field 3", "url": "https://club.example.com/game", "notes": "Bring water",
                                             "rrule": "FREQ=WEEKLY;BYDAY=TU,TH"})
            self.assertEqual(out["calendar"], "Family")
            later = (date.today() + timedelta(days=5))
            await inst.add_event(fam, {"title": "Vacation", "allDay": True, "start": later.isoformat(),
                                       "end": (later + timedelta(days=20)).isoformat()})
            return fam
        fam = self.run_(go())
        texts = [t for _e, t in self.server.objects[FAM].values()]
        soccer = next(t for t in texts if "Soccer" in t)
        ev = icalendar.Calendar.from_ical(soccer).walk("VEVENT")[0]
        self.assertEqual(str(ev.get("DTSTART").params.get("TZID")), "America/Toronto")
        self.assertEqual(ev.get("RRULE").to_ical().decode(), "FREQ=WEEKLY;BYDAY=TU,TH")
        self.assertEqual(str(ev.get("URL")), "https://club.example.com/game")
        self.assertIn("BEGIN:VTIMEZONE", soccer)
        vac = icalendar.Calendar.from_ical(next(t for t in texts if "Vacation" in t)).walk("VEVENT")[0]
        self.assertEqual((vac.get("DTEND").dt - vac.get("DTSTART").dt).days, 21)  # 21 days, DTEND the day after
        hub = inst.hub
        titles = [e["title"] for e in self.events(hub, fam)]
        self.assertIn("Vacation", titles)

    def iso(self, days=1):
        return (date.today() + timedelta(days=days)).isoformat()

    def test_checks(self):
        z = CD.hub_zone()
        with self.assertRaisesRegex(ValueError, "Give it a name first"):
            CD.event_fields({"title": " ", "start": "2026-10-23T18:00", "end": "2026-10-23T19:00"}, z)
        with self.assertRaisesRegex(ValueError, "ends before it starts"):
            CD.event_fields({"title": "x", "start": "2026-10-23T20:03", "end": "2026-10-23T19:00"}, z)
        with self.assertRaisesRegex(ValueError, "ends before it starts"):
            CD.event_fields({"title": "x", "allDay": True, "start": "2026-11-22", "end": "2026-11-02"}, z)
        f = CD.event_fields({"title": "x", "allDay": True, "start": "2026-11-02", "end": "2026-11-02"}, z)
        self.assertEqual(f["end"], date(2026, 11, 3))

    def test_view_only_refused(self):
        hub, inst = self.make()
        self.run_(inst.refresh())
        with self.assertRaisesRegex(CommandError, "can only be viewed"):
            self.run_(inst.add_event(self.eid(inst, CHORES), {"title": "x", "start": f"{self.iso()}T10:00", "end": f"{self.iso()}T11:00"}))

    def test_edit_keeps_reminders_and_delete(self):
        hub, inst = self.make()
        async def go():
            await inst.refresh()
            fam = self.eid(inst, FAM)
            dinner = next(e for e in self.events(hub, fam) if e["uid"] == "dinner")
            form = await inst.event_form(fam, dinner["href"], None, "all")
            self.assertEqual(form["title"], "Dinner at Nonna's")
            self.assertEqual(form["start"], f"{self.iso()}T18:00")
            await inst.change_event(fam, dinner["href"], {**form, "title": "Dinner at Nonna's (late)",
                                                          "start": f"{self.iso()}T19:00", "end": f"{self.iso()}T20:00"}, "all", None)
            ev = icalendar.Calendar.from_ical(self.server.objects[FAM]["dinner.ics"][1]).walk("VEVENT")[0]
            self.assertEqual(str(ev.get("SUMMARY")), "Dinner at Nonna's (late)")
            self.assertEqual(len(ev.walk("VALARM")), 1)  # the reminder stays
            again = next(e for e in self.events(hub, fam) if e["uid"] == "dinner")
            await inst.delete_event(fam, again["href"], "all", None)
            self.assertNotIn("dinner.ics", self.server.objects[FAM])
        self.run_(go())

    def test_one_time_of_a_repeating_event(self):
        hub, inst = self.make()
        async def go():
            await inst.refresh()
            fam = self.eid(inst, FAM)
            pianos = [e for e in self.events(hub, fam) if e["uid"] == "piano"]
            second, third = pianos[1], pianos[2]
            form = await inst.event_form(fam, second["href"], second["occurrence"], "this")
            self.assertEqual(form["start"][11:], "16:30")
            await inst.change_event(fam, second["href"], {**form, "title": "Piano (moved)",
                                                          "start": form["start"][:11] + "18:00", "end": form["start"][:11] + "18:45"},
                                    "this", second["occurrence"])
            evs = [e for e in self.events(hub, fam) if e["uid"] == "piano"]
            self.assertEqual(len(evs), 5)
            moved = next(e for e in evs if e["title"] == "Piano (moved)")
            self.assertTrue(moved["start"].endswith("18:00:00"))
            await inst.delete_event(fam, third["href"], "this", third["occurrence"])
            self.assertEqual(len([e for e in self.events(hub, fam) if e["uid"] == "piano"]), 4)
            text = self.server.objects[FAM]["piano.ics"][1]
            self.assertIn("EXDATE", text)
            self.assertIn("RECURRENCE-ID", text)
        self.run_(go())

    def test_someone_else_changed_it(self):
        hub, inst = self.make()
        async def go():
            await inst.refresh()
            fam = self.eid(inst, FAM)
            dinner = next(e for e in self.events(hub, fam) if e["uid"] == "dinner")
            self.server.put(FAM, "dinner.ics", self.server.objects[FAM]["dinner.ics"][1])  # new etag behind Newman's back
            with self.assertRaisesRegex(CommandError, "changed it at the same time"):
                await inst.change_event(fam, dinner["href"], {"title": "x", "start": f"{self.iso()}T18:00",
                                                              "end": f"{self.iso()}T19:00"}, "all", None)
        self.run_(go())

    def test_server_says_a_calendar_changed(self):
        hub, inst = self.make()
        async def go():
            await inst.refresh()
            inst.receive_change({"calendar": FAM})
            self.assertEqual(inst._wanted, {FAM})
            self.assertTrue(inst._wake.is_set())
            inst._wake.clear()
            inst.receive_change({"calendar": "someone-new"})   # within NUDGE_GAP_S: put off, not dropped
            self.assertIn("*", inst._wanted)
            self.assertFalse(inst._wake.is_set())
            self.assertIsNotNone(inst._nudge_later)
            inst.receive_change({"calendar": FAM})              # still just the one wake waiting
            inst._nudge_later.cancel()
            inst._wake_now()
            self.assertTrue(inst._wake.is_set())
            self.assertIsNone(inst._nudge_later)
        self.run_(go())

    def test_reminder_cards(self):
        hub, inst = self.make({"remind": [FAM]})
        async def go():
            await inst.refresh()
            fam = self.eid(inst, FAM)
            dinner = next(e for e in self.events(hub, fam) if e["uid"] == "dinner")
            due = datetime.fromisoformat(dinner["start"]) - timedelta(minutes=30)
            inst._last_tick = (due - timedelta(seconds=5)).timestamp()
            real_now = CD.datetime
            class Frozen(real_now):
                @classmethod
                def now(cls, tz=None):
                    return due + timedelta(seconds=1)
            CD.datetime = Frozen
            try:
                await inst._tick()
            finally:
                CD.datetime = real_now
            cards = [m for m in self.sent if m["type"] == "calendarReminder"]
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0]["reminder"]["title"], "Dinner at Nonna's")
            self.assertEqual(cards[0]["reminder"]["minutes"], 30)
            self.assertEqual(len(inst.reminders()), 1)
            self.assertTrue(await inst.dismiss_reminder(cards[0]["reminder"]["id"]))
            self.assertEqual(inst.reminders(), [])
            self.assertEqual(self.sent[-1], {"type": "calendarReminderDone", "id": cards[0]["reminder"]["id"]})
        self.run_(go())

    def test_no_reminders_unless_ticked(self):
        hub, inst = self.make()
        self.run_(inst.refresh())
        self.assertFalse(hub.get(self.eid(inst, FAM)).attrs["remindOnWall"])

    def test_other_days_a_past_month_with_repeats(self):
        hub, inst = self.make()
        first = date.today().replace(day=1)
        past = ((first - timedelta(days=1)).replace(day=1) - timedelta(days=1)).replace(day=1)  # two months back
        after = (past + timedelta(days=32)).replace(day=1)
        began = past - timedelta(days=14)  # weekly from two weeks before that month: 4 times in it
        self.server.put(FAM, "swim.ics", ics("swim", "Swimming", f"{began:%Y%m%d}T100000", f"{began:%Y%m%d}T110000",
                                             rrule="FREQ=WEEKLY;COUNT=6"))
        trip = past + timedelta(days=9)
        self.server.put(FAM, "trip.ics", ics("trip", "School trip", f"{trip:%Y%m%d}T090000", f"{trip:%Y%m%d}T150000"))
        async def go():
            await inst.refresh()
            fam = self.eid(inst, FAM)
            self.assertNotIn("School trip", [e["title"] for e in self.events(hub, fam)])  # not in the window
            attrs = hub.get(fam).attrs
            self.assertGreater(attrs["upcomingUntil"], (date.today() + timedelta(days=30)).isoformat())
            self.assertIsNotNone(attrs["fetchedAt"])
            evs = await inst.events_between(fam, past, after)
            z = lambda d: datetime(d.year, d.month, d.day).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: E731
            self.assertEqual(self.server.ranges[-1], (z(past), z(after)))
            swims = [e for e in evs if e["title"] == "Swimming"]
            self.assertEqual([e["start"][:10] for e in swims], [(past + timedelta(days=7 * i)).isoformat() for i in range(4)])
            self.assertTrue(all(e["recurring"] and e["uid"] == "swim" and e["href"].endswith("/swim.ics") for e in swims))
            self.assertEqual(len({e["occurrence"] for e in swims}), 4)
            titles = [e["title"] for e in evs]
            self.assertIn("School trip", titles)
            self.assertNotIn("Dinner at Nonna's", titles)
            # Asked again: kept, not asked of the server again.
            reports = sum(1 for m, _p in self.server.calls if m == "REPORT")
            self.assertIs(await inst.events_between(fam, past, after), evs)
            self.assertEqual(sum(1 for m, _p in self.server.calls if m == "REPORT"), reports)
            # One from that month opens like the window's, and can be changed.
            ev = next(e for e in evs if e["title"] == "School trip")
            form = await inst.event_form(fam, ev["href"], None, "all")
            self.assertEqual((form["title"], form["start"]), ("School trip", f"{trip.isoformat()}T09:00"))
            await inst.change_event(fam, ev["href"], {**form, "title": "School trip (zoo)"}, "all", None)
            self.assertIn("School trip (zoo)", self.server.objects[FAM]["trip.ics"][1])
            # The calendar was fetched since: the month is asked for again.
            again = await inst.events_between(fam, past, after)
            self.assertIn("School trip (zoo)", [e["title"] for e in again])
            self.assertEqual(sum(1 for m, _p in self.server.calls if m == "REPORT"), reports + 2)
        self.run_(go())

    def test_other_days_of_a_calendar_gone(self):
        hub, inst = self.make()
        self.run_(inst.refresh())
        with self.assertRaisesRegex(CommandError, "isn't on the server any more"):
            self.run_(inst.events_between("caldav.nope", date.today(), date.today() + timedelta(days=7)))


if __name__ == "__main__":
    unittest.main()


class ChangedAddressTests(unittest.IsolatedAsyncioTestCase):
    """POST /api/public/calendars/changed: no sign-in, no key - only a nudge to fetch again."""

    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        from newman.api import build_app
        from newman.config import config
        from test_auth import H
        self.tmp = tempfile.TemporaryDirectory()
        object.__setattr__(config, "data_dir", Path(self.tmp.name))
        self.h = H(Path(self.tmp.name))
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def test_nudge(self):
        r = await self.client.post("/api/public/calendars/changed", json={"calendar": FAM})
        self.assertEqual(r.status, 404)
        self.assertEqual((await r.json())["error"], "Newman has no calendar server connection yet")
        ctx = Context(hub=Hub(), data_dir=Path(self.tmp.name), snapshot_dir=Path(self.tmp.name))
        inst = CD.CalDavIntegration("caldav", "GCloud", {"url": "https://cal.example.com", "username": "n", "password": "p"}, ctx)
        self.h.integrations.instances["caldav"] = inst
        try:
            r = await self.client.post("/api/public/calendars/changed", json={"calendar": FAM})
            self.assertEqual(r.status, 200, await r.text())
            self.assertTrue(inst._wake.is_set())
            self.assertEqual(inst._wanted, {"*"})   # not fetched yet, so not one it knows: all of them
            r = await self.client.post("/api/public/calendars/changed", data=b"not json")
            self.assertEqual(r.status, 200)
        finally:
            del self.h.integrations.instances["caldav"]
            if inst._nudge_later:
                inst._nudge_later.cancel()


class OtherDaysApiTests(unittest.IsolatedAsyncioTestCase):
    """GET /api/calendars/{eid}/events?from=&to=: signed in, from any connection that has events_between."""

    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        from newman.api import build_app
        from newman.config import config
        from newman.integrations.base import Integration
        from test_auth import H
        self.tmp = tempfile.TemporaryDirectory()
        object.__setattr__(config, "data_dir", Path(self.tmp.name))
        self.h = H(Path(self.tmp.name))
        ctx = self.h.integrations._ctx

        class Ranged(Integration):
            type = "ranged"
            asked = []

            async def events_between(self, eid, start, end):
                if eid == "ranged.broken":
                    raise CommandError("Couldn't read Broken from the calendar server")
                self.asked.append((eid, start, end))
                return [{"title": "Swimming", "start": f"{start.isoformat()}T10:00:00", "end": f"{start.isoformat()}T11:00:00",
                         "allDay": False, "location": ""}]

        class Plain(Integration):
            type = "plain"

        self.ranged = Ranged("ranged", "Ranged", {}, ctx)
        self.h.integrations.instances["ranged"] = self.ranged
        self.h.integrations.instances["plain"] = Plain("plain", "Plain", {}, ctx)
        for eid, iid in (("ranged.family", "ranged"), ("ranged.broken", "ranged"), ("plain.work", "plain")):
            self.h.hub.upsert(eid, kind="calendar", name=eid, integration=iid)
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def test_range(self):
        r = await self.client.get("/api/calendars/ranged.family/events?from=2026-08-01&to=2026-09-01")
        self.assertEqual(r.status, 401)  # signed in only
        r = await self.client.post("/api/auth/setup", json={"username": "Chris", "name": "Chris", "password": "correct horse"})
        self.assertEqual(r.status, 200)
        r = await self.client.get("/api/calendars/ranged.family/events?from=2026-08-01&to=2026-09-01")
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual((await r.json())["events"][0]["title"], "Swimming")
        self.assertEqual(self.ranged.asked, [("ranged.family", date(2026, 8, 1), date(2026, 9, 1))])
        for q in ("from=2026-08-01&to=2026-09-16", "from=2026-08-01&to=2026-08-01", "from=2026-08-01", "from=Aug&to=Sep"):
            r = await self.client.get(f"/api/calendars/ranged.family/events?{q}")
            self.assertEqual(r.status, 400, q)
        r = await self.client.get("/api/calendars/ranged.family/events?from=2026-08-01&to=2026-09-15")
        self.assertEqual(r.status, 200)  # 45 days
        r = await self.client.get("/api/calendars/plain.work/events?from=2026-08-01&to=2026-09-01")
        self.assertEqual(r.status, 404)
        r = await self.client.get("/api/calendars/nope.none/events?from=2026-08-01&to=2026-09-01")
        self.assertEqual(r.status, 404)
        r = await self.client.get("/api/calendars/ranged.broken/events?from=2026-08-01&to=2026-09-01")
        self.assertEqual((r.status, (await r.json())["error"]), (400, "Couldn't read Broken from the calendar server"))
