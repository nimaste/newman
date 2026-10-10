"""The optional "only between dates" condition: yearly ranges (wrapping over New Year), one-off
ranges in a given year, leap days, validation, wording, and how it combines with other limits."""
import asyncio
import os
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman import sun  # noqa: E402
from newman.automations import describe as d  # noqa: E402
import test_engine  # noqa: E402

XMAS = {"from": "12-01", "to": "01-06", "year": None}


def auto(**cond):
    return d.normalize({"name": "Tree lights", "trigger": {"type": "event", "entity": "lutron.remote_9", "event": "on"},
                        "condition": cond, "notify": {"enabled": False},
                        "actions": [{"entity": "lutron.device_5", "command": "turn_on"}]})


class DateMathTests(unittest.TestCase):
    def test_yearly_wraps_over_new_year_inclusive(self):
        ok = lambda day: d.dates_ok(XMAS, day)  # noqa: E731
        self.assertTrue(ok(date(2026, 12, 1)))    # first day counts
        self.assertTrue(ok(date(2026, 12, 25)))
        self.assertTrue(ok(date(2027, 1, 1)))
        self.assertTrue(ok(date(2027, 1, 6)))     # last day counts
        self.assertFalse(ok(date(2027, 1, 7)))
        self.assertFalse(ok(date(2026, 11, 30)))
        self.assertFalse(ok(date(2026, 7, 4)))

    def test_yearly_plain_range(self):
        r = {"from": "07-01", "to": "07-15"}
        self.assertTrue(d.dates_ok(r, date(2030, 7, 1)))
        self.assertTrue(d.dates_ok(r, date(2030, 7, 15)))
        self.assertFalse(d.dates_ok(r, date(2030, 7, 16)))
        self.assertFalse(d.dates_ok(r, date(2030, 6, 30)))
        self.assertTrue(d.dates_ok({"from": "03-03", "to": "03-03"}, date(2031, 3, 3)))  # a single day

    def test_one_off_year(self):
        r = {"from": "07-01", "to": "07-15", "year": 2027}
        self.assertTrue(d.dates_ok(r, date(2027, 7, 1)))
        self.assertTrue(d.dates_ok(r, date(2027, 7, 15)))
        self.assertFalse(d.dates_ok(r, date(2026, 7, 10)))  # not the year before...
        self.assertFalse(d.dates_ok(r, date(2028, 7, 10)))  # ...nor after

    def test_one_off_across_new_year(self):
        r = {"from": "12-20", "to": "01-03", "year": 2026}
        self.assertTrue(d.dates_ok(r, date(2026, 12, 20)))
        self.assertTrue(d.dates_ok(r, date(2027, 1, 3)))
        self.assertFalse(d.dates_ok(r, date(2027, 1, 4)))
        self.assertFalse(d.dates_ok(r, date(2026, 1, 2)))   # the January before it started
        self.assertFalse(d.dates_ok(r, date(2027, 12, 25)))  # the next Christmas

    def test_leap_day(self):
        r = {"from": "02-29", "to": "03-02"}
        self.assertTrue(d.dates_ok(r, date(2028, 2, 29)))
        self.assertFalse(d.dates_ok(r, date(2028, 2, 28)))
        self.assertTrue(d.dates_ok(r, date(2027, 2, 28)))   # no Feb 29 this year: counts as Feb 28
        self.assertTrue(d.dates_ok(r, date(2027, 3, 2)))
        self.assertFalse(d.dates_ok(r, date(2027, 2, 27)))
        upto = {"from": "02-01", "to": "02-29"}
        self.assertTrue(d.dates_ok(upto, date(2027, 2, 28)))
        self.assertFalse(d.dates_ok(upto, date(2027, 3, 1)))

    def test_no_dates_means_no_limit(self):
        for x in (None, {}, {"from": "", "to": ""}):
            self.assertTrue(d.dates_ok(x, date(2026, 5, 5)))
        a = d.normalize({"trigger": {"type": "time", "time": "21:00"}, "condition": {"between": None}})
        self.assertIsNone(a["condition"]["dates"])
        self.assertTrue(d.dates_allow(a))

    def test_normalize_pads(self):
        self.assertEqual(d.clean_dates({"from": "7-1", "to": "7-15", "year": "2027"}),
                         {"from": "07-01", "to": "07-15", "year": 2027})

    def test_wording(self):
        self.assertEqual(d.dates_text(XMAS), "between December 1 and January 6 (every year)")
        self.assertEqual(d.dates_text({"from": "07-01", "to": "07-15", "year": 2027}), "from July 1 to July 15, 2027")
        self.assertEqual(d.dates_text({"from": "12-20", "to": "01-03", "year": 2026}),
                         "from December 20, 2026 to January 3, 2027")
        self.assertEqual(d.window_text(auto(dates=XMAS)), "only between December 1 and January 6 (every year)")
        both = auto(dates=XMAS, between={"after": "17:00", "before": "23:00"}, sun={"only": "dark"})
        self.assertEqual(d.window_text(both), "only between 5:00 PM and 11:00 PM and between sunset and sunrise "
                                              "and between December 1 and January 6 (every year)")


class DateValidationTests(test_engine.Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        calls = self.calls = []

        class FakeLutron:
            async def command(self, eid, command, value=None):
                calls.append((eid, command, value))
        self.integrations.instances["lutron"] = FakeLutron()
        self.hub.upsert("lutron.device_5", kind="light", name="Porch Light", integration="lutron")
        await self.hub.update("lutron.device_5", state="off", attrs={"dimmable": True, "brightness": 0})
        self.hub.upsert("lutron.remote_9", kind="remote", name="Kitchen Pico Remote", integration="lutron")

    def errs(self, dates):
        return d.errors(auto(dates=dates), self.hub)

    def test_errors(self):
        self.assertEqual(self.errs(XMAS), [])
        self.assertEqual(self.errs({"from": "02-29", "to": "03-01"}), [])  # fine every year
        self.assertEqual(self.errs({"from": "12-01", "to": ""}), ["Pick both dates for “only between dates”"])
        self.assertEqual(self.errs({"from": "04-31", "to": "05-02"}),
                         ["The first date isn't a real date - pick a month and a day that exist"])
        self.assertEqual(self.errs({"from": "01-01", "to": "13-01"}),
                         ["The last date isn't a real date - pick a month and a day that exist"])
        self.assertEqual(self.errs({"from": "02-29", "to": "03-01", "year": 2027}), ["There's no February 29 in 2027"])
        self.assertEqual(self.errs({"from": "12-01", "to": "02-29", "year": 2027}), [])  # 2028 is a leap year
        self.assertEqual(self.errs({"from": "01-01", "to": "01-02", "year": "soon"}),
                         ["Pick a year from 2000 to 2100, or choose “every year”"])
        self.assertEqual(self.errs({"from": "01-01", "to": "01-02", "year": ""}),  # a cleared year box
                         ["Pick a year from 2000 to 2100, or choose “every year”"])

    def test_save_refuses_bad_dates(self):
        with self.assertRaises(Exception) as cm:
            self.engine.save(auto(dates={"from": "02-30", "to": "03-01"}))
        self.assertIn("real date", str(cm.exception))

    def test_summary_mentions_dates(self):
        s = self.engine.describe(auto(dates=XMAS))["summary"]
        self.assertEqual(s, "When the On button on Kitchen Pico Remote is pressed, only between December 1 and "
                            "January 6 (every year), turn on Porch Light.")
        s = self.engine.describe(auto(dates={"from": "07-01", "to": "07-15", "year": 2027}))["summary"]
        self.assertIn("only from July 1 to July 15, 2027", s)

    async def test_runs_only_inside_dates(self):
        today, tomorrow = date.today(), date.today() + timedelta(days=1)
        md = lambda x: x.strftime("%m-%d")  # noqa: E731
        trig = {"type": "event", "entity": "lutron.remote_9", "event": "on"}
        out = self.engine.save(auto(dates={"from": md(tomorrow), "to": md(tomorrow)}))
        run = await self.engine.trigger(out, trig)
        self.assertEqual(run["status"], "skipped")
        self.assertIn("Outside its dates", run["log"][-1]["text"])
        self.assertEqual(self.calls, [])
        out = self.engine.save(dict(out, condition={"dates": {"from": md(today), "to": md(today)}}))
        run = await self.engine.trigger(out, trig)
        self.assertNotEqual(run["status"], "skipped")
        self.assertEqual(self.calls[-1][:2], ("lutron.device_5", "turn_on"))
        # A one-off range in last year never runs again; "Run now" (force) still does.
        self.calls.clear()
        out = self.engine.save(dict(out, condition={"dates": {"from": md(today), "to": md(today), "year": today.year - 1}}))
        self.assertEqual((await self.engine.trigger(out, trig))["status"], "skipped")
        self.assertEqual(self.calls, [])
        await self.engine.trigger(out, trig, force=True)
        self.assertEqual(self.calls[-1][:2], ("lutron.device_5", "turn_on"))

    async def test_dates_and_time_window_both_needed(self):
        today = date.today().strftime("%m-%d")
        now = datetime.now().strftime("%H:%M")
        closed = {"after": "00:00", "before": "00:01"} if now != "00:00" else {"after": "12:00", "before": "12:01"}
        out = self.engine.save(auto(dates={"from": today, "to": today}, between=closed))
        run = await self.engine.trigger(out, {"type": "event", "entity": "lutron.remote_9", "event": "on"})
        self.assertEqual(run["status"], "skipped")  # right date, wrong time
        self.assertIn("Outside its hours", run["log"][-1]["text"])

    async def test_state_trigger_respects_dates(self):
        tomorrow = (date.today() + timedelta(days=1)).strftime("%m-%d")
        self.engine.save(d.normalize({"name": "t", "trigger": {"type": "state", "entities": ["lutron.device_5"], "to": "on"},
                                      "condition": {"dates": {"from": tomorrow, "to": tomorrow}},
                                      "notify": {"enabled": False},
                                      "actions": [{"entity": "lutron.device_5", "command": "set_brightness", "value": 50}]}))
        await self.hub.update("lutron.device_5", state="on", attrs={"brightness": 100})
        await asyncio.sleep(0.05)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.runs()[0]["status"], "skipped")

    def test_next_run_skips_to_the_range(self):
        a = d.normalize({"enabled": True, "trigger": {"type": "time", "time": "21:00", "days": [0, 1, 2, 3, 4, 5, 6]},
                         "condition": {"dates": XMAS}})
        a["enabled"] = True
        nxt = self.engine.next_run(a, after=datetime(2026, 10, 4, 12, 0))
        self.assertEqual(nxt.strftime("%Y-%m-%d %H:%M"), "2026-12-01 21:00")
        nxt = self.engine.next_run(a, after=datetime(2027, 1, 6, 22, 0))  # past the last evening
        self.assertEqual(nxt.strftime("%Y-%m-%d"), "2027-12-01")
        once = dict(a, condition={"dates": {"from": "07-01", "to": "07-15", "year": 2025}})
        self.assertIsNone(self.engine.next_run(once, after=datetime(2026, 10, 4, 12, 0)))  # all over

    def test_old_automations_unchanged(self):
        a = d.normalize({"trigger": {"type": "time", "time": "21:00", "days": [0, 1, 2, 3, 4, 5, 6]},
                         "condition": {"between": {"after": "19:00", "before": "06:00"}}})
        self.assertEqual(d.window_text(a), "only between 7:00 PM and 6:00 AM")
        nxt = self.engine.next_run(dict(a, enabled=True), after=datetime(2026, 10, 4, 12, 0))
        self.assertEqual(nxt.strftime("%Y-%m-%d %H:%M"), "2026-10-04 21:00")


class DateWithSunTests(unittest.TestCase):
    def test_sun_and_dates_combine(self):
        from datetime import timezone
        sun.set_location(43.65, -79.38, tz=timezone(timedelta(hours=-5)))
        try:
            a = auto(dates=XMAS, sun={"only": "dark"})
            dark_xmas = datetime(2026, 12, 24, 23, 0, tzinfo=timezone(timedelta(hours=-5)))
            dark_july = datetime(2026, 7, 4, 23, 0, tzinfo=timezone(timedelta(hours=-5)))
            self.assertTrue(d.sun_allows(a, dark_xmas) and d.dates_allow(a, dark_xmas.date()))
            self.assertTrue(d.sun_allows(a, dark_july))
            self.assertFalse(d.dates_allow(a, dark_july.date()))  # dark, but not in the date range
        finally:
            sun.set_location(None, None)


if __name__ == "__main__":
    unittest.main()
