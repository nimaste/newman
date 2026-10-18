"""A remote's buttons are worded by the names it gives them (attrs.buttons): "Top", not "Button 1"."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from newman.kinds import KINDS, event_phrase  # noqa: E402

PADDLE = {"events": ["button_1", "button_1_long", "button_1_released", "button_3", "button_3_long", "button_3_released"],
          "buttons": [{"id": "button_1", "label": "Top", "role": "on"}, {"id": "button_3", "label": "Bottom", "role": "off"}]}


class ButtonNameTests(unittest.TestCase):
    def test_named_buttons(self):
        k = KINDS["remote"]
        self.assertEqual(k.event("button_1", PADDLE).label, "“Top” is pressed")
        self.assertEqual(k.event("button_3_long", PADDLE).label, "“Bottom” is long-pressed")
        self.assertEqual(k.event("button_3_released", PADDLE).ends, "")
        self.assertEqual(k.event("button_1_long", PADDLE).ends, "button_1_released")
        self.assertEqual(event_phrase(k, "button_1", "Kitchen Under Cabinet", PADDLE),
                         "the Top button on Kitchen Under Cabinet is pressed")

    def test_without_names_nothing_changes(self):
        k = KINDS["remote"]
        self.assertEqual(k.event("button_1").label, "“Button 1” is pressed")
        self.assertEqual(k.event("on", {"buttons": []}).label, "“On” is pressed")
        self.assertEqual(event_phrase(k, "button_1", "Pico"), "the Button 1 button on Pico is pressed")


if __name__ == "__main__":
    unittest.main()
