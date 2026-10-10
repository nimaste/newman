"""Every connection in the catalog renders as a form, masks its secrets and is found by discovery."""
import json
import os
import unittest

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.discovery import Discovery  # noqa: E402
from newman.integrations import REGISTRY  # noqa: E402
from newman.integrations._common import device_summary, plural  # noqa: E402


class CatalogTests(unittest.TestCase):
    def test_catalog_serialises(self):
        for cls in REGISTRY.values():
            out = json.loads(json.dumps(cls.catalog_json()))
            self.assertTrue(out["name"] and out["description"], cls.type)
        kasa = REGISTRY["kasa"].catalog_json()
        devices = next(f for f in kasa["fields"] if f["key"] == "devices")
        self.assertEqual([f["key"] for f in devices["itemFields"]], ["host", "name"])
        self.assertTrue(devices["dynamic"])
        hooks = next(f for f in REGISTRY["webhooks"].catalog_json()["fields"] if f["key"] == "hooks")
        self.assertEqual({f["key"]: f["type"] for f in hooks["itemFields"]}["secret"], "password")

    def test_discovery_listens_for_the_new_devices(self):
        types = Discovery()._types
        for t, integ in (("_shelly._tcp.local.", "shelly"), ("_esphomelib._tcp.local.", "esphome"),
                         ("_hue._tcp.local.", "hue")):
            self.assertIn(integ, [c.type for c in types[t]])

    def test_plain_english_counts(self):
        self.assertEqual(plural(3, "switch"), "3 switches")
        self.assertEqual(plural(2, "leak sensor"), "2 leak sensors")
        self.assertEqual(device_summary(["switch", "switch", "light", "temperature", "sensor"]),
                         "2 switches, 2 sensors and 1 light")
        self.assertEqual(device_summary([]), "no devices yet")
