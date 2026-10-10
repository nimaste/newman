"""A dashboard's tab names on the wall top bar: icons with the current tab named, or names when they fit."""
import os
import unittest

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman import dashboards as D  # noqa: E402

PAGES = [{"id": "a", "name": "A", "icon": "home", "tiles": []}]


class TabNamesTest(unittest.TestCase):
    def test_default_and_choices(self):
        self.assertEqual(D.clean({"pages": PAGES})[0]["tabNames"], "current")
        self.assertEqual(D.clean({"pages": PAGES, "tabNames": "fit"})[0]["tabNames"], "fit")
        self.assertEqual(D.clean({"pages": PAGES, "tabNames": "loud"})[0]["tabNames"], "current")
        # Saved before there was a choice: clients still get the default.
        self.assertEqual(D.with_home({"pages": PAGES})["tabNames"], "current")
        self.assertEqual(D.with_home({"pages": PAGES, "tabNames": "fit"})["tabNames"], "fit")


if __name__ == "__main__":
    unittest.main()