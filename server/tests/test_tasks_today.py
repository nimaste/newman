"""GCloud Calendar tasks (VTODO) on Today: reading GCloud's format, the Done / Undo PUT bodies, a new task,
Today's To do built from tasks and devices (who sees what, the wording, evening heads-ups, Tomorrow), the
Calendar server connection reading and writing tasks against a fake GCloud (which rolls repeating tasks
forward the way GCloud does), the API (who did it from the wall and from a phone, + Add) and the old Upkeep
connection being dropped on upgrade."""
import asyncio
import os
import re
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")
os.environ["TZ"] = "America/Toronto"
import time  # noqa: E402
time.tzset()

import httpx  # noqa: E402
import icalendar  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman import tasks as TK  # noqa: E402
from newman import today as T  # noqa: E402
from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.integrations import caldav as CD  # noqa: E402
from newman.integrations.base import Context  # noqa: E402
from newman.state import Hub  # noqa: E402
from test_auth import Browser, H  # noqa: E402
from test_caldav import FAM, FakeServer, ics  # noqa: E402

HOME, KIDS = "33333333-cccc", "44444444-dddd"
NOW = datetime(2026, 10, 27, 18, 30)  # a Tuesday evening


def vtodo(uid, summary, due, *extra, status="NEEDS-ACTION"):
    """A task in GCloud's format; `due` "20261024" (a date) or "20261024T090000" (Toronto time)."""
    when = f";VALUE=DATE:{due}" if len(due) == 8 else f";TZID=America/Toronto:{due}"
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//GCloud//EN\r\nBEGIN:VTODO\r\n"
            f"UID:{uid}\r\nDTSTAMP:20261001T000000Z\r\nSUMMARY:{summary}\r\nDUE{when}\r\nDTSTART{when}\r\n"
            f"STATUS:{status}\r\n" + "".join(x + "\r\n" for x in extra) + "END:VTODO\r\nEND:VCALENDAR\r\n")


DRYER = vtodo("dryer", "Clean the dryer vent", "20261024", "RRULE:FREQ=MONTHLY;INTERVAL=6", "X-GCLOUD-REPEAT-FROM:DONE",
              "X-GCLOUD-ASK-WHO:TRUE", "X-GCLOUD-DONE:20260424T140000Z;Chris;20260420",
              "X-GCLOUD-DONE:20251020T140000Z;Marlina;20251018",
              "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:x", "TRIGGER:-PT1H", "END:VALARM")


class FormatTests(unittest.TestCase):
    def test_reads_gclouds_format(self):
        [t] = TK.parse_tasks(DRYER, "/cal/dryer.ics", '"e1"', today=date(2026, 10, 27))
        self.assertEqual((t["uid"], t["title"], t["due"], t["dueAt"], t["status"]),
                         ("dryer", "Clean the dryer vent", "2026-10-24", None, "NEEDS-ACTION"))
        self.assertEqual((t["every"], t["repeatFrom"], t["owner"], t["lead"], t["askWho"], t["eveningBefore"]),
                         ("6 months", "done", "", 7, True, False))
        self.assertEqual([(d["who"], d["prevDue"]) for d in t["history"]], [("Chris", "20260420"), ("Marlina", "20251018")])
        self.assertEqual(t["history"][0]["at"], "2026-04-24T14:00:00+00:00")
        timed = vtodo("pills", "Pills", "20261027T090000", "X-GCLOUD-OWNER:marlina", "X-GCLOUD-LEAD-DAYS:2",
                      "X-GCLOUD-EVENING-BEFORE:TRUE", "X-GCLOUD-ASK-WHO:TRUE")
        [p] = TK.parse_tasks(timed, "/x", today=date(2026, 10, 27))
        self.assertEqual((p["due"], p["dueAt"], p["owner"], p["lead"], p["eveningBefore"]),
                         ("2026-10-27", "2026-10-27T09:00:00", "marlina", 2, True))
        self.assertFalse(p["askWho"])  # it belongs to someone: nobody is asked
        self.assertEqual(TK.every_text("FREQ=WEEKLY;INTERVAL=1"), "1 week")
        self.assertEqual(TK.every_text("FREQ=DAILY"), "1 day")

    def test_left_out(self):
        old = vtodo("old", "Old", "20260801", "COMPLETED:20260802T120000Z", status="COMPLETED")
        recent = vtodo("new", "Recent", "20261020", "COMPLETED:20261021T120000Z", status="COMPLETED")
        self.assertEqual(TK.parse_tasks(old, "/x", today=date(2026, 10, 27)), [])
        self.assertEqual(len(TK.parse_tasks(recent, "/x", today=date(2026, 10, 27))), 1)
        self.assertEqual(TK.parse_tasks(vtodo("c", "C", "20261020", status="CANCELLED"), "/x"), [])
        # Apple Reminders' copy of one time sits beside the task: only the task counts.
        both = DRYER.replace("END:VCALENDAR", "BEGIN:VTODO\r\nUID:dryer\r\nRECURRENCE-ID;VALUE=DATE:20261024\r\n"
                                               "SUMMARY:copy\r\nDUE;VALUE=DATE:20261024\r\nEND:VTODO\r\nEND:VCALENDAR")
        self.assertEqual([t["title"] for t in TK.parse_tasks(both, "/x")], ["Clean the dryer vent"])

    def test_done_body(self):
        out = TK.mark_done(DRYER, "Marlina", now=datetime(2026, 10, 27, 22, 0, tzinfo=timezone.utc))
        self.assertIn("STATUS:COMPLETED\r\n", out)
        self.assertNotIn("NEEDS-ACTION", out)
        self.assertIn("COMPLETED:20261027T220000Z\r\n", out)
        self.assertIn("PERCENT-COMPLETE:100\r\n", out)
        self.assertIn("X-GCLOUD-DONE-BY:Marlina\r\n", out)
        # Everything else exactly as GCloud wrote it: history lines unescaped, the alarm, the due date.
        self.assertIn("X-GCLOUD-DONE:20260424T140000Z;Chris;20260420\r\n", out)
        self.assertIn("DUE;VALUE=DATE:20261024\r\n", out)
        self.assertIn("BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:x\r\nTRIGGER:-PT1H\r\nEND:VALARM\r\n", out)
        self.assertEqual(out.count("BEGIN:VTODO"), 1)
        # The alarm stays inside the task, after the new lines' place.
        self.assertLess(out.index("X-GCLOUD-DONE-BY"), out.index("END:VTODO"))
        self.assertEqual(TK.parse_tasks(out, "/x")[0]["status"], "COMPLETED")
        self.assertIn("X-GCLOUD-DONE-BY:A\\; B\r\n", TK.mark_done(DRYER, "A; B"))

    def test_undo_body(self):
        # GCloud rolled it forward on Done: DUE moved on, a new history line on top.
        rolled = vtodo("dryer", "Clean the dryer vent", "20270427", "RRULE:FREQ=MONTHLY;INTERVAL=6",
                       "X-GCLOUD-DONE:20261027T220000Z;Marlina;20261024", "X-GCLOUD-DONE:20260424T140000Z;Chris;20260420")
        out = TK.undo_done(rolled, now=datetime(2026, 10, 27, 22, 1, tzinfo=timezone.utc))
        self.assertIn("DUE;VALUE=DATE:20261024\r\n", out)
        self.assertIn("DTSTART;VALUE=DATE:20261024\r\n", out)
        self.assertNotIn("20270427", out)
        self.assertNotIn("Marlina", out)
        self.assertIn("X-GCLOUD-DONE:20260424T140000Z;Chris;20260420\r\n", out)
        self.assertIn("STATUS:NEEDS-ACTION\r\n", out)
        # A time of day keeps its time zone.
        timed = vtodo("pills", "Pills", "20261028T090000", "RRULE:FREQ=DAILY",
                      "X-GCLOUD-DONE:20261027T130000Z;Chris;20261027T090000")
        out = TK.undo_done(timed, now=datetime(2026, 10, 27, 13, 5, tzinfo=timezone.utc))
        self.assertIn("DUE;TZID=America/Toronto:20261027T090000\r\n", out)
        # A server that isn't GCloud added no line for this Done: only open again, older history kept.
        plain = vtodo("x", "X", "20261024", "COMPLETED:20261027T220000Z", "X-GCLOUD-DONE:20260424T140000Z;Chris;20260420",
                      status="COMPLETED")
        out = TK.undo_done(plain, now=datetime(2026, 10, 27, 22, 1, tzinfo=timezone.utc))
        self.assertIn("DUE;VALUE=DATE:20261024\r\n", out)
        self.assertIn("X-GCLOUD-DONE:20260424T140000Z;Chris;20260420\r\n", out)
        self.assertIn("STATUS:NEEDS-ACTION\r\n", out)
        # A one-off task done: open again, its line gone.
        one = vtodo("salt", "Softener salt", "20261027", "COMPLETED:20261027T220000Z", "PERCENT-COMPLETE:100",
                    "X-GCLOUD-DONE:20261027T220000Z;Chris;20261027", status="COMPLETED")
        out = TK.undo_done(one, now=datetime(2026, 10, 27, 22, 5, tzinfo=timezone.utc))
        self.assertIn("STATUS:NEEDS-ACTION\r\n", out)
        for gone in ("COMPLETED:", "PERCENT-COMPLETE", "X-GCLOUD-DONE"):
            self.assertNotIn(gone, out.replace("STATUS:NEEDS-ACTION", ""))
        self.assertIn("DUE;VALUE=DATE:20261027\r\n", out)

    def test_long_lines_folded(self):
        long = vtodo("n", "N", "20261027", "DESCRIPTION:" + "é" * 120)
        out = TK.mark_done(long, "Chris")
        self.assertTrue(all(len(line.encode()) <= 75 for line in out.split("\r\n")))
        self.assertEqual(TK.parse_tasks(out, "/x")[0]["notes"], "é" * 120)

    def test_new_task(self):
        with self.assertRaisesRegex(ValueError, "what needs doing"):
            TK.task_fields({"title": " ", "due": "2026-10-27"})
        with self.assertRaisesRegex(ValueError, "due"):
            TK.task_fields({"title": "x", "due": ""})
        with self.assertRaisesRegex(ValueError, "days, weeks or months"):
            TK.task_fields({"title": "x", "due": "2026-10-27", "every": 2, "unit": "years"})
        f = TK.task_fields({"title": "Change the furnace filter", "due": "2026-10-30", "every": 3, "unit": "months", "owner": "chris"})
        uid, text = TK.new_task(f, now=datetime(2026, 10, 27, 12, tzinfo=timezone.utc), uid="u1@newman")
        todo = icalendar.Calendar.from_ical(text).walk("VTODO")[0]
        self.assertEqual(str(todo["SUMMARY"]), "Change the furnace filter")
        self.assertEqual(todo["DUE"].dt, date(2026, 10, 30))
        self.assertEqual(todo["DTSTART"].dt, date(2026, 10, 30))
        self.assertEqual(todo["RRULE"].to_ical().decode(), "FREQ=MONTHLY;INTERVAL=3")
        self.assertEqual(str(todo["X-GCLOUD-OWNER"]), "chris")
        self.assertEqual(str(todo["STATUS"]), "NEEDS-ACTION")
        _, once = TK.new_task(TK.task_fields({"title": "Once", "due": "2026-10-30"}))
        self.assertNotIn("RRULE", once)
        self.assertNotIn("X-GCLOUD-OWNER", once)

    def test_form_fields(self):
        self.assertEqual(TK.clean_rule("RRULE:freq=weekly;interval=1;byday=MO"), "FREQ=WEEKLY")
        self.assertEqual(TK.clean_rule("FREQ=YEARLY;INTERVAL=2;UNTIL=20300101T000000Z"), "FREQ=YEARLY;INTERVAL=2;UNTIL=20300101")
        self.assertEqual(TK.clean_rule("FREQ=DAILY;COUNT=5"), "FREQ=DAILY;COUNT=5")
        self.assertEqual(TK.clean_rule(""), "")
        with self.assertRaisesRegex(ValueError, "repeat"):
            TK.clean_rule("FREQ=HOURLY")
        f = TK.task_fields({"title": "Pills", "due": "2026-10-28", "time": "21:00", "rrule": "FREQ=DAILY", "repeatFrom": "done",
                            "owner": "marlina", "lead": 0, "askWho": True})
        self.assertEqual((f["rrule"], f["repeatFrom"], f["lead"], f["askWho"]), ("FREQ=DAILY", "done", 0, False))  # owned: nobody asked
        once = TK.task_fields({"title": "x", "due": "2026-10-28", "rrule": "", "repeatFrom": "done"})
        self.assertEqual(once["repeatFrom"], "due")  # only a repeat is counted from anything
        with self.assertRaisesRegex(ValueError, "time"):
            TK.task_fields({"title": "x", "due": "2026-10-28", "time": "25:00"})
        with self.assertRaisesRegex(ValueError, "Coming up"):
            TK.task_fields({"title": "x", "due": "2026-10-28", "lead": 400})
        # A phone's every + unit still works.
        self.assertEqual(TK.task_fields({"title": "x", "due": "2026-10-28", "every": 2, "unit": "weeks"})["rrule"], "FREQ=WEEKLY;INTERVAL=2")

    def test_edit_keeps_what_the_form_doesnt_show(self):
        f = TK.task_fields({"title": "Clean the dryer vent", "due": "2026-11-01", "rrule": "FREQ=YEARLY", "owner": ""})
        out = TK.edit_task(DRYER, f, now=datetime(2026, 10, 27, 12, tzinfo=timezone.utc))
        self.assertIn("RRULE:FREQ=YEARLY\r\n", out)
        self.assertIn("DUE;VALUE=DATE:20261101\r\n", out)
        self.assertNotIn("X-GCLOUD-REPEAT-FROM", out)
        self.assertNotIn("X-GCLOUD-ASK-WHO", out)
        self.assertIn("X-GCLOUD-DONE:20260424T140000Z;Chris;20260420\r\n", out)  # history kept, as written
        self.assertIn("BEGIN:VALARM\r\n", out)
        self.assertIn("UID:dryer\r\n", out)
        self.assertEqual(out.count("SUMMARY:"), 1)
        [t] = TK.parse_tasks(out, "/x")
        self.assertEqual((t["due"], t["every"], t["repeatFrom"], t["askWho"]), ("2026-11-01", "1 year", "due", False))

    def test_who_is_who(self):
        people = [{"id": "p1", "name": "Chris", "usernames": ["cgarito"]}, {"id": "p2", "name": "Marlina", "usernames": []}]
        self.assertEqual(TK.owner_person("CGarito", people)["id"], "p1")
        self.assertEqual(TK.owner_person("marlina", people)["id"], "p2")
        self.assertIsNone(TK.owner_person("massimo", people))
        self.assertEqual(TK.owner_username("p1", people), "cgarito")
        self.assertEqual(TK.owner_username("p2", people), "marlina")
        self.assertEqual(TK.split_id("caldav.home:task:a:b@x"), ("caldav.home", "a:b@x"))


def task(uid, title, due, **kw):
    base = {"uid": uid, "href": f"/{uid}.ics", "etag": "", "title": title, "notes": "", "due": due, "dueAt": None,
            "status": "NEEDS-ACTION", "completed": None, "rrule": "", "every": "", "repeatFrom": "due", "owner": "",
            "lead": 7, "eveningBefore": False, "askWho": False, "history": [], "calendarKey": "caldav.home",
            "calendar": "Home", "colour": "#3CCFB4", "calendarEntity": "caldav.home", "connection": "GCloud"}
    return {**base, **kw}


class TodayTests(unittest.TestCase):
    PEOPLE = [{"id": "p_chris", "name": "Chris", "usernames": ["chris"]},
              {"id": "p_marlina", "name": "Marlina", "usernames": ["marlina"]}]

    def setUp(self):
        self.hub = Hub()
        asyncio.run(self._devices())
        self.tasks = [
            task("dryer", "Clean the dryer vent", "2026-10-24", every="6 months", repeatFrom="done", askWho=True,
                 history=[{"at": "2026-04-24T14:00:00+00:00", "who": "Chris", "prevDue": "20260420"}]),
            task("oil", "Change oil · BMW X5", "2026-10-25", owner="chris"),
            task("salt", "Softener salt", "2026-10-27"),
            task("filter", "Change the furnace filter", "2026-10-30"),
            task("recycle", "Recycling bins", "2026-10-28", eveningBefore=True),
            task("far", "Smoke alarms", "2026-12-01"),
            task("done", "Done already", "2026-10-26", status="COMPLETED"),
            task("pills", "Marlina's pills", "2026-10-27", owner="MARLINA"),
            task("stranger", "Someone else's", "2026-10-27", owner="massimo"),
        ]

    async def _devices(self):
        h = self.hub
        h.upsert("ring.keypad", kind="binary", name="Hallway Keypad", integration="ring")
        await h.update("ring.keypad", state="off", attrs={"acStatus": "error"})
        h.upsert("hc.dish", kind="appliance", name="Dishwasher", integration="hc")
        await h.update("hc.dish", state="off", attrs={"needs": ["detergent empty"]})

    def build(self, **kw):
        return T.build(self.hub, {}, now=kw.pop("now", NOW), tasks=self.tasks, people=self.PEOPLE, **kw)

    def test_wall(self):
        b = self.build()
        self.assertEqual([t["title"] for t in b["todo"]],
                         ["Clean the dryer vent", "Change oil · BMW X5", "Hallway Keypad: no ac power",
                          "Marlina's pills", "Softener salt", "Someone else's", "Dishwasher: detergent empty"])
        dryer = b["todo"][0]
        self.assertEqual((dryer["status"], dryer["level"], dryer["source"], dryer["from"], dryer["owner"], dryer["askWho"]),
                         ("3 days late", "overdue", "task", "GCloud · Home", "", True))
        self.assertEqual((dryer["every"], dryer["repeatFrom"], dryer["lastDoneBy"], dryer["due"]),
                         ("6 months", "done", "Chris", "2026-10-24"))
        self.assertEqual(dryer["id"], "caldav.home:task:dryer")
        oil = b["todo"][1]
        self.assertEqual((oil["status"], oil["owner"], oil["ownerId"]), ("2 days late", "Chris", "p_chris"))
        self.assertEqual(b["todo"][4]["status"], "Due today")
        self.assertEqual([(s["title"], s["status"]) for s in b["soon"]],
                         [("Recycling bins", "Tomorrow"), ("Change the furnace filter", "In 3 days")])
        self.assertIn("Recycling bins is due tomorrow", [x["text"] for x in b["headsUp"]])
        morning = self.build(now=datetime(2026, 10, 27, 8, 0))
        self.assertNotIn("Recycling bins is due tomorrow", [x["text"] for x in morning["headsUp"]])

    def test_phones(self):
        chris = self.build(person_id="p_chris")
        titles = [t["title"] for t in chris["todo"]]
        self.assertIn("Change oil · BMW X5", titles)
        self.assertNotIn("Marlina's pills", titles)
        self.assertNotIn("Someone else's", titles)  # an owner who isn't anyone here: on no phone
        self.assertIn("Clean the dryer vent", titles)  # everyone's
        marlina = [t["title"] for t in self.build(person_id="p_marlina")["todo"]]
        self.assertIn("Marlina's pills", marlina)
        self.assertNotIn("Change oil · BMW X5", marlina)

    def test_tomorrow(self):
        b = self.build(snoozed={"caldav.home:task:dryer": NOW.timestamp() + 3600})
        self.assertNotIn("Clean the dryer vent", [t["title"] for t in b["todo"]])
        self.assertIn(("Clean the dryer vent", "Tomorrow"), [(s["title"], s["status"]) for s in b["soon"]])
        b = self.build(snoozed={"caldav.home:task:dryer": NOW.timestamp() - 1})
        self.assertIn("Clean the dryer vent", [t["title"] for t in b["todo"]])


class FakeGCloud(FakeServer):
    """The CalDAV fake with GCloud's task calendars: Home (events and tasks, writable), Kids (tasks only).
    A PUT that completes a repeating task is rolled forward the way GCloud does (from the old DUE, or
    from the day it was done for REPEAT-FROM:DONE), with X-GCLOUD-DONE-BY turned into a history line."""

    def __init__(self):
        super().__init__()
        self.objects.update({HOME: {}, KIDS: {}})
        self.puts = []

    def __call__(self, req):
        path, parts = req.url.path, req.url.path.strip("/").split("/")
        if req.method == "PROPFIND" and path == "/caldav/newman/" and req.headers.get("depth") == "1":
            resp = super().__call__(req)
            body = resp.content.decode().replace("</d:multistatus>", self._cal(HOME, "Home", "VEVENT", "VTODO")
                                                 + self._cal(KIDS, "Kids", "VTODO") + "</d:multistatus>")
            return httpx.Response(207, content=body.encode())
        if req.method == "REPORT" and len(parts) >= 3 and parts[2] in self.objects and 'name="VTODO"' in req.content.decode():
            self.calls.append((req.method, path))
            cid = parts[2]
            inner = "".join(f"<d:response><d:href>/caldav/newman/{cid}/{n}</d:href><d:propstat><d:prop>"
                            f"<d:getetag>{e}</d:getetag><c:calendar-data>{t.replace('&', '&amp;').replace('<', '&lt;')}</c:calendar-data>"
                            "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
                            for n, (e, t) in self.objects[cid].items() if "BEGIN:VTODO" in t)
            return httpx.Response(207, content=f'<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">{inner}</d:multistatus>'.encode())
        if req.method == "REPORT" and len(parts) >= 3 and parts[2] in (HOME, KIDS):
            return httpx.Response(207, content=b'<d:multistatus xmlns:d="DAV:"></d:multistatus>')
        if req.method == "PUT" and len(parts) >= 4 and parts[2] in (HOME, KIDS):
            self.puts.append(req.content.decode())
            req = httpx.Request("PUT", req.url, headers=req.headers, content=self.roll(req.content.decode()).encode())
        return super().__call__(req)

    def _cal(self, cid, name, *comps):
        tag = "".join(e for e, _ in self.objects[cid].values()) or "empty"
        c = "".join(f'<c:comp name="{x}"/>' for x in comps)
        return (f"<d:response><d:href>/caldav/newman/{cid}/</d:href><d:propstat><d:prop>"
                f"<d:resourcetype><d:collection/><c:calendar/></d:resourcetype><d:displayname>{name}</d:displayname>"
                f"<c:supported-calendar-component-set>{c}</c:supported-calendar-component-set><cs:getctag>{tag}</cs:getctag>"
                "<d:current-user-privilege-set><d:privilege><d:write/></d:privilege></d:current-user-privilege-set>"
                "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>")

    @staticmethod
    def roll(text):
        if "STATUS:COMPLETED" not in text:
            return text
        cal = icalendar.Calendar.from_ical(text)
        t = cal.walk("VTODO")[0]
        who = str(t.get("X-GCLOUD-DONE-BY") or "Newman")
        old_due = t["DUE"].dt
        prev = old_due.strftime("%Y%m%d")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        if t.get("RRULE") is None:
            line = f"X-GCLOUD-DONE:{stamp};{who};{prev}"
            return re.sub(r"X-GCLOUD-DONE-BY:[^\r\n]*\r\n", line + "\r\n", text)
        months = int((t["RRULE"].get("INTERVAL") or [1])[0])
        base = date(2026, 10, 27) if str(t.get("X-GCLOUD-REPEAT-FROM") or "") == "DONE" else old_due
        m = base.month - 1 + months
        new = date(base.year + m // 12, m % 12 + 1, base.day).strftime("%Y%m%d")
        out = re.sub(r"(DUE|DTSTART);VALUE=DATE:\d{8}", lambda mo: f"{mo.group(1)};VALUE=DATE:{new}", text)
        out = re.sub(r"STATUS:COMPLETED\r\n", "STATUS:NEEDS-ACTION\r\n", out)
        out = re.sub(r"(COMPLETED|PERCENT-COMPLETE):[^\r\n]*\r\n", "", out)
        out = re.sub(r"X-GCLOUD-DONE-BY:[^\r\n]*\r\n", "", out)
        line = f"X-GCLOUD-DONE:{stamp};{who};{prev}\r\n"
        at = out.find("X-GCLOUD-DONE:")
        return out[:at] + line + out[at:] if at >= 0 else out.replace("END:VTODO", line + "END:VTODO", 1)


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = FakeGCloud()
        CD.CalDavIntegration.transport = httpx.MockTransport(self.server)
        self.server.put(HOME, "dryer.ics", DRYER)
        self.server.put(KIDS, "homework.ics", vtodo("hw", "Homework folder", "20261027", "X-GCLOUD-OWNER:massimo"))
        self.sent = []

    def tearDown(self):
        CD.CalDavIntegration.transport = None
        self.tmp.cleanup()

    def make(self):
        async def broadcast(msg):
            self.sent.append(msg)
        hub = Hub()
        ctx = Context(hub=hub, data_dir=Path(self.tmp.name), snapshot_dir=Path(self.tmp.name))
        ctx.broadcast = broadcast
        inst = CD.CalDavIntegration("caldav", "Calendar server (CalDAV)",
                                    {"url": "https://cal.example.com", "username": "newman", "password": "app-pw"}, ctx)
        inst.save_config = lambda c: None
        return hub, inst

    def test_read_done_undo_add(self):
        hub, inst = self.make()

        async def go():
            await inst.refresh()
            key = {c["id"]: k for k, c in inst._cals.items()}
            home, kids = key[HOME], key[KIDS]
            self.assertIsNotNone(hub.get(home))   # events and tasks: a calendar device
            self.assertIsNone(hub.get(kids))       # tasks only: no device
            self.assertIsNotNone(hub.get(key[FAM]))
            self.assertEqual(sorted(c["name"] for c in inst.task_calendars()), ["Home", "Kids"])
            ts = {t["uid"]: t for t in inst.tasks()}
            self.assertEqual(set(ts), {"dryer", "hw"})
            self.assertEqual((ts["dryer"]["calendar"], ts["dryer"]["connection"], ts["hw"]["calendarEntity"]),
                             ("Home", "GCloud", None))
            self.assertEqual(self.sent, [])  # the first look isn't news

            await inst.task_done(home, "dryer", "Marlina")
            self.assertIn("X-GCLOUD-DONE-BY:Marlina", self.server.puts[-1])
            [t] = [t for t in inst.tasks() if t["uid"] == "dryer"]
            self.assertEqual((t["due"], t["status"], t["history"][0]["who"]), ("2027-04-27", "NEEDS-ACTION", "Marlina"))
            self.assertIn({"type": "today"}, self.sent)

            await inst.task_undo(home, "dryer")
            [t] = [t for t in inst.tasks() if t["uid"] == "dryer"]
            self.assertEqual((t["due"], [h["who"] for h in t["history"]]), ("2026-10-24", ["Chris", "Marlina"]))
            self.assertIn("BEGIN:VALARM", self.server.objects[HOME]["dryer.ics"][1])

            out = await inst.add_task(kids, TK.task_fields({"title": "Pack lunches", "due": "2026-10-28", "every": 1,
                                                            "unit": "weeks"}))
            self.assertEqual(out["calendar"], "Kids")
            self.assertIn("Pack lunches", [t["title"] for t in inst.tasks()])
            with self.assertRaises(CD.CommandError):
                await inst.add_task(key[FAM], TK.task_fields({"title": "x", "due": "2026-10-28"}))  # events only
        asyncio.run(go())


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.server = FakeGCloud()
        CD.CalDavIntegration.transport = httpx.MockTransport(self.server)
        today = date.today()
        self.server.put(HOME, "salt.ics", vtodo("salt", "Softener salt", (today - timedelta(days=2)).strftime("%Y%m%d"),
                                                 "RRULE:FREQ=MONTHLY;INTERVAL=1", "X-GCLOUD-ASK-WHO:TRUE"))
        self.server.put(HOME, "oil.ics", vtodo("oil", "Change oil", today.strftime("%Y%m%d"), "X-GCLOUD-OWNER:chris"))
        self.h = H(data)
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        await self.client.post("/api/users", json={"username": "marlina", "name": "Marlina", "role": "family", "password": "another pass"})
        await self.client.post("/api/users", json={"username": "hallway", "name": "Hallway tablet", "role": "tablet", "password": "wall tablet pass"})
        r = await self.client.post("/api/integrations", json={"type": "caldav", "name": "GCloud", "config": {
            "url": "https://cal.example.com", "username": "newman", "password": "app-pw"}})
        self.assertEqual(r.status, 200, await r.text())
        self.inst = next(i for i in self.h.integrations.instances.values() if i.type == "caldav")
        await self.inst.refresh()
        self.home = next(k for k, c in self.inst._cals.items() if c["id"] == HOME)

    async def asyncTearDown(self):
        await self.h.integrations.stop_all()
        await self.client.close()
        CD.CalDavIntegration.transport = None
        self.tmp.cleanup()

    async def sign_in(self, username, password, kind):
        c = Browser(self.client)
        r = await c.post("/api/auth/login", json={"username": username, "password": password, "kind": kind})
        return c, {"Authorization": "Bearer " + (await r.json())["token"]}

    async def test_wall_and_phones(self):
        salt = f"{self.home}:task:salt"
        wall, hdr = await self.sign_in("hallway", "wall tablet pass", "tablet")
        today = await (await wall.get("/api/today", headers=hdr)).json()
        self.assertEqual([(t["title"], t["status"]) for t in today["todo"]], [("Softener salt", "2 days late"), ("Change oil", "Due today")])
        self.assertTrue(today["todo"][0]["askWho"])
        self.assertEqual(today["todo"][0]["from"], "GCloud · Home")
        people = (await (await wall.get("/api/tasks/calendars", headers=hdr)).json())["people"]
        self.assertIn("Marlina", [p["name"] for p in people])
        r = await wall.post("/api/tasks/done", json={"id": salt, "who": "Marlina"}, headers=hdr)
        self.assertEqual((await r.json())["who"], "Marlina", await r.text())
        self.assertIn("X-GCLOUD-DONE-BY:Marlina", self.server.puts[-1])
        today = await (await wall.get("/api/today", headers=hdr)).json()
        self.assertNotIn("Softener salt", [t["title"] for t in today["todo"]])  # rolled forward a month
        r = await wall.post("/api/tasks/undo", json={"id": salt}, headers=hdr)
        self.assertEqual(r.status, 200, await r.text())
        today = await (await wall.get("/api/today", headers=hdr)).json()
        self.assertEqual(today["todo"][0]["title"], "Softener salt")
        # Skipped "who?": the owner for a task that has one, else the tablet.
        r = await wall.post("/api/tasks/done", json={"id": f"{self.home}:task:oil"}, headers=hdr)
        self.assertEqual((await r.json())["who"], "Chris")
        r = await wall.post("/api/tasks/done", json={"id": salt, "who": "Nobody"}, headers=hdr)
        self.assertEqual((await r.json())["who"], "Hallway tablet")
        await wall.post("/api/tasks/undo", json={"id": salt}, headers=hdr)
        # The wall's New task: every field of GCloud's format.
        r = await wall.post("/api/tasks", json={"calendar": self.home, "title": "Change the furnace filter", "due": "2026-10-30",
                                                "rrule": "FREQ=MONTHLY;INTERVAL=3", "repeatFrom": "done", "owner": "",
                                                "lead": 3, "eveningBefore": True, "askWho": True}, headers=hdr)
        self.assertEqual(r.status, 200, await r.text())
        put = self.server.puts[-1]
        for line in ("SUMMARY:Change the furnace filter", "DUE;VALUE=DATE:20261030", "DTSTART;VALUE=DATE:20261030",
                     "RRULE:FREQ=MONTHLY;INTERVAL=3", "X-GCLOUD-REPEAT-FROM:DONE", "X-GCLOUD-LEAD-DAYS:3",
                     "X-GCLOUD-EVENING-BEFORE:TRUE", "X-GCLOUD-ASK-WHO:TRUE"):
            self.assertIn(line + "\r\n", put)
        listing = await (await wall.get("/api/tasks", headers=hdr)).json()
        furnace = next(t for t in listing["tasks"] if t["title"] == "Change the furnace filter")
        self.assertEqual((furnace["due"], furnace["every"], furnace["repeatFrom"], furnace["lead"], furnace["eveningBefore"],
                          furnace["askWho"], furnace["writable"], furnace["calendar"]),
                         ("2026-10-30", "3 months", "done", 3, True, True, True, "Home"))
        self.assertEqual(sorted(c["name"] for c in listing["calendars"]), ["Home", "Kids"])
        # Edit: at a set time, belongs to Chris now, no repeat; its history stays.
        chris = next(p for p in listing["people"] if p["name"] == "Chris")
        r = await wall.post("/api/tasks/change", json={"id": furnace["id"], "task": {
            "title": "Change the furnace filter (MERV 11)", "due": "2026-11-02", "time": "09:30", "rrule": "",
            "owner": chris["id"], "lead": 7, "askWho": True}}, headers=hdr)
        self.assertEqual(r.status, 200, await r.text())
        put = self.server.puts[-1]
        self.assertIn("SUMMARY:Change the furnace filter (MERV 11)\r\n", put)
        self.assertIn("DUE:20261102T143000Z\r\n", put)  # 9:30 in Toronto
        self.assertIn("X-GCLOUD-OWNER:chris\r\n", put)
        for gone in ("RRULE", "X-GCLOUD-REPEAT-FROM", "X-GCLOUD-LEAD-DAYS", "X-GCLOUD-ASK-WHO", "X-GCLOUD-EVENING-BEFORE"):
            self.assertNotIn(gone, put)
        listing = await (await wall.get("/api/tasks", headers=hdr)).json()
        changed = next(t for t in listing["tasks"] if t["id"] == furnace["id"])
        self.assertEqual((changed["dueAt"], changed["owner"], changed["ownerUser"]), ("2026-11-02T09:30:00", "Chris", "chris"))
        # Keeping an owner who isn't a Newman person: "=".
        r = await wall.post("/api/tasks/change", json={"id": furnace["id"], "task": {"title": "x", "due": "2026-11-02", "owner": "="}}, headers=hdr)
        self.assertIn("X-GCLOUD-OWNER:chris\r\n", self.server.puts[-1])
        r = await wall.post("/api/tasks/delete", json={"id": furnace["id"]}, headers=hdr)
        self.assertEqual(r.status, 200, await r.text())
        listing = await (await wall.get("/api/tasks", headers=hdr)).json()
        self.assertNotIn(furnace["id"], [t["id"] for t in listing["tasks"]])
        r = await wall.post("/api/tasks/change", json={"id": furnace["id"], "task": {"title": "x", "due": "2026-11-02"}}, headers=hdr)
        self.assertEqual(r.status, 400)
        r = await wall.post("/api/tasks/snooze", json={"id": salt}, headers=hdr)
        self.assertEqual(r.status, 200)
        today = await (await wall.get("/api/today", headers=hdr)).json()
        self.assertEqual([(s["title"], s["status"]) for s in today["soon"]], [("Softener salt", "Tomorrow")])
        self.assertEqual((await wall.post("/api/tasks/done", json={"id": "nope"}, headers=hdr)).status, 404)
        await wall.close()

        # Marlina's phone: she is who did it, whatever it says; Chris's oil change isn't on it. She can add.
        phone, hdr = await self.sign_in("marlina", "another pass", "app")
        today = await (await phone.get("/api/today", headers=hdr)).json()
        self.assertNotIn("Change oil", [t["title"] for t in today["todo"] + today["soon"]])
        r = await phone.post("/api/tasks/done", json={"id": salt, "who": "Chris"}, headers=hdr)
        self.assertEqual((await r.json())["who"], "Marlina")
        cals = (await (await phone.get("/api/tasks/calendars", headers=hdr)).json())["calendars"]
        self.assertEqual(sorted(c["name"] for c in cals), ["Home", "Kids"])
        marlina = next(p for p in people if p["name"] == "Marlina")
        r = await phone.post("/api/tasks", json={"calendar": self.home, "title": "Water the plants", "due": "2026-10-30",
                                                 "every": 2, "unit": "weeks", "owner": marlina["id"]}, headers=hdr)
        self.assertEqual(r.status, 200, await r.text())
        self.assertIn("X-GCLOUD-OWNER:marlina", self.server.puts[-1])
        self.assertIn("RRULE:FREQ=WEEKLY;INTERVAL=2", self.server.puts[-1])
        r = await phone.post("/api/tasks", json={"calendar": "nope", "title": "x", "due": "2026-10-30"}, headers=hdr)
        self.assertEqual(r.status, 400)
        r = await phone.post("/api/tasks", json={"calendar": self.home, "title": "", "due": "2026-10-30"}, headers=hdr)
        self.assertEqual((await r.json())["error"], "Say what needs doing first.")
        await phone.close()

        s = await (await self.client.get("/api/today/settings")).json()
        self.assertEqual(sorted(s["taskCalendars"]), ["Home", "Kids"])
        self.assertEqual((s["todayRainThreshold"], s["todayEveningFrom"]), (50, "17:00"))
        r = await self.client.patch("/api/today/settings", json={"todayRainThreshold": 40, "todayEveningFrom": "18:30"})
        self.assertEqual(((await r.json())["todayRainThreshold"], (await r.json())["todayEveningFrom"]), (40, "18:30"))


class UpgradeTests(unittest.TestCase):
    def test_upkeep_connection_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            h = H(data)
            h.store.save_integration({"id": "upkeep", "type": "upkeep", "name": "Upkeep", "config": {}, "enabled": True})
            (data / "integrations" / "upkeep").mkdir(parents=True)
            (data / "integrations" / "upkeep" / "items.json").write_text("[]")
            h.integrations.migrate_types()
            self.assertEqual([r for r in h.store.integrations() if r["type"] == "upkeep"], [])
            self.assertFalse((data / "integrations" / "upkeep").exists())


if __name__ == "__main__":
    unittest.main()
