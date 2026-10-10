"""APNs payloads for notifications with buttons: no mutable-content for buttons alone (only snapshots
run the Notification Service Extension, which never touches categories), the category id, and the
buttons JSON (left out of snapshot pushes) - for automations, charging questions, and anything else carrying buttons. A push
without buttons or a snapshot stays plain."""
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.automations.engine import category_for as engine_category_for  # noqa: E402
from newman.push.notifier import Notification, Notifier, category_for  # noqa: E402
from newman.store import Phone, Store  # noqa: E402
from test_locations import FakeApns  # noqa: E402

BUTTONS = [{"id": "lock:lock.side", "label": "Lock Side Door", "icon": "lock", "sensitive": False},
           {"id": "snooze:60", "label": "Snooze 1 hour", "icon": "clock", "sensitive": False},
           {"id": "dismiss", "label": "Dismiss", "icon": "x", "sensitive": False}]


class PushButtonsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))
        self.notifier = Notifier(self.store)
        self.apns = FakeApns()
        self.notifier._apns = self.apns
        self.store.register_phone(Phone(device_id="iphone", token="tok-ios", platform="ios", person_id="p1"))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def sent(self, n):
        self.apns.sent.clear()
        await self.notifier.send(n, device_ids=["iphone"])
        self.assertEqual(len(self.apns.sent), 1)
        return self.apns.sent[0][1]

    async def test_buttons_without_snapshot_skip_the_extension(self):
        p = await self.sent(Notification(kind="automation", title="Nightly lock check", body="Side Door is unlocked.",
                                         buttons=BUTTONS, category=category_for(BUTTONS), action_subject="run_1"))
        self.assertNotIn("mutable-content", p["aps"])
        self.assertEqual(p["aps"]["category"], category_for(BUTTONS))
        self.assertEqual(json.loads(p["buttons"]), BUTTONS)
        self.assertTrue(p["action_token"])

    async def test_category_defaults_from_buttons(self):
        p = await self.sent(Notification(kind="followup", title="Smart charging", body="Which plug?", buttons=BUTTONS))
        self.assertEqual(p["aps"]["category"], category_for(BUTTONS))
        self.assertNotIn("mutable-content", p["aps"])

    async def test_plain_push_stays_plain(self):
        p = await self.sent(Notification(kind="followup", title="Done", body="All locked."))
        self.assertNotIn("mutable-content", p["aps"])
        self.assertNotIn("category", p["aps"])
        self.assertNotIn("buttons", p)

    async def test_snapshot_still_mutable(self):
        p = await self.sent(Notification(kind="automation", title="Doorbell", body="Someone's at the door.",
                                         snapshot_id="snap1"))
        self.assertEqual(p["aps"]["mutable-content"], 1)
        self.assertIn("snapshot_url", p)

    async def test_snapshot_with_buttons_keeps_buttons_from_the_extension(self):
        p = await self.sent(Notification(kind="automation", title="Garage", body="Person at the garage.",
                                         snapshot_id="snap1", buttons=BUTTONS, action_subject="run_1"))
        self.assertEqual(p["aps"]["mutable-content"], 1)
        self.assertEqual(p["aps"]["category"], category_for(BUTTONS))
        self.assertNotIn("buttons", p)

    def test_engine_and_notifier_agree(self):
        self.assertIs(engine_category_for, category_for)
        self.assertIsNone(category_for([]))
        self.assertTrue(category_for(BUTTONS).startswith("c_"))
        self.assertNotEqual(category_for(BUTTONS), category_for(BUTTONS[1:]))


if __name__ == "__main__":
    unittest.main()