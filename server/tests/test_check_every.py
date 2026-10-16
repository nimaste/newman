"""The "Check everything every (minutes)" setting: its minimum is enforced when saving and when running."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from newman.integrations.base import Field, Integration, check_every_field  # noqa: E402


class Fake(Integration):
    type = "fake_check"
    name = "Fake"
    fields = [Field("host", "Host", required=True), check_every_field(default=15, minimum=5)]


def fake(config):
    i = Fake.__new__(Fake)
    i.config = config
    return i


class CheckEveryTests(unittest.TestCase):
    def test_saving_refuses_less_than_the_minimum(self):
        self.assertEqual(Fake.validate({"host": "h"}), [])
        self.assertEqual(Fake.validate({"host": "h", "check_minutes": 5}), [])
        self.assertEqual(Fake.validate({"host": "h", "check_minutes": 4}), ["Check everything every (minutes): 5 or more"])
        self.assertEqual(Fake.validate({"host": "h", "check_minutes": "often"}), ["Check everything every (minutes): 5 or more"])
        self.assertEqual(Fake.validate({"host": "h", "check_minutes": None}), [])

    def test_running_uses_the_setting_never_under_the_minimum(self):
        self.assertEqual(fake({}).seconds_every("check_minutes"), 15 * 60)
        self.assertEqual(fake({"check_minutes": 30}).seconds_every("check_minutes"), 30 * 60)
        self.assertEqual(fake({"check_minutes": 1}).seconds_every("check_minutes"), 5 * 60)
        self.assertEqual(fake({"check_minutes": True}).seconds_every("check_minutes"), 15 * 60)

    def test_the_form_gets_the_minimum(self):
        f = check_every_field(default=30, minimum=5).to_json()
        self.assertEqual((f["key"], f["type"], f["default"], f["min"], f["advanced"]), ("check_minutes", "number", 30, 5, True))


if __name__ == "__main__":
    unittest.main()
