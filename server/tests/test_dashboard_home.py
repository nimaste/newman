"""A dashboard's home page: the page a wall tablet goes back to, and a different one in the mornings."""
import os
import unittest

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman import dashboards as D  # noqa: E402
from newman.state import Hub  # noqa: E402

PAGES = [{"id": "today", "name": "Today", "tiles": []}, {"id": "overview", "name": "Overview", "tiles": []}]


class DashboardHomeTests(unittest.TestCase):
    def test_defaults_are_filled_in(self):
        clean, errors = D.clean({"pages": PAGES})
        self.assertEqual(errors, [])
        self.assertEqual(clean["home"], {"page": "today", "morning": None, "idleMinutes": 2})
        clean, errors = D.clean({"pages": PAGES, "home": {"page": "overview", "morning": {"page": "today"}}})
        self.assertEqual(clean["home"], {"page": "overview", "idleMinutes": 2, "morning": {
            "page": "today", "weekdays": {"from": "06:00", "to": "10:00"}, "weekends": {"from": "07:00", "to": "12:00"}}})
        # Saved before there was a home: clients still get one.
        self.assertEqual(D.with_home({"pages": PAGES})["home"]["page"], "today")

    def test_kept_as_set(self):
        home = {"page": "overview", "idleMinutes": 0, "morning": {
            "page": "today", "weekdays": {"from": "6:30", "to": "09:15"}, "weekends": {"from": "08:00", "to": "11:00"}}}
        clean, errors = D.clean({"pages": PAGES, "home": home})
        self.assertEqual(errors, [])
        self.assertEqual(clean["home"]["morning"]["weekdays"], {"from": "06:30", "to": "09:15"})
        self.assertEqual(clean["home"]["idleMinutes"], 0)

    def test_bad_times_and_missing_pages(self):
        _, errors = D.clean({"pages": PAGES, "home": {"morning": {"page": "today", "weekdays": {"from": "10:00", "to": "06:00"}}}})
        self.assertEqual(errors, ["Mornings Monday to Friday: the end must be after the start"])
        _, errors = D.clean({"pages": PAGES, "home": {"morning": {"page": "today", "weekends": {"from": "7am", "to": "12:00"}}}})
        self.assertEqual(errors, ["Mornings Saturday and Sunday: times look like 06:00"])
        # A page that's gone: home falls back to the first page, mornings turn off.
        clean, errors = D.clean({"pages": PAGES[1:], "home": {"page": "today", "morning": {"page": "today"}}})
        self.assertEqual((errors, clean["home"]["page"], clean["home"]["morning"]), ([], "overview", None))

    def test_today_page_fits_the_grid(self):
        page = D.today_page(Hub())
        self.assertEqual([t["type"] for t in page["tiles"]], ["greeting", "todo", "calendar", "weather"])
        clean, errors = D.clean({"pages": [page], "grid": D.GRID})
        self.assertEqual(errors, [])
