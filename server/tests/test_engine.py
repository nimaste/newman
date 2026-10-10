"""Offline tests: MQTT discovery parsing, the automation engine end to end
(runs, buttons, follow-ups, snooze), the plain-English describer and tokens.

    docker run --rm --network none -v "$PWD/tests:/app/tests" newman:dev python -m unittest discover -s tests -v

No broker, push credentials or network: MQTT is faked and phones are
platform "test", whose notifications land in the notifier's outbox.
"""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman import tokens  # noqa: E402
from newman.automations import engine as engine_mod  # noqa: E402
from newman.automations.engine import ActionError, Engine  # noqa: E402
from newman.integrations.base import Context  # noqa: E402
from newman.integrations.manager import IntegrationManager  # noqa: E402
from newman.integrations.mqtt import MqttIntegration  # noqa: E402
from newman.push.notifier import Notifier  # noqa: E402
from newman.state import Hub  # noqa: E402
from newman.store import Phone, Store  # noqa: E402

LOC = "b4d84394-4cbc-4af5-7d82-4a4515b430dd"
SIDE = "f0b7f035-ac74-40bc-8850-6397452affa8"
LAUNDRY = "6c765b1f-a573-4618-ab90-faf1f3087526"
SIDE_ID, LAUNDRY_ID = f"mqtt.{SIDE}_lock", f"mqtt.{LAUNDRY}_lock"


def discovery(device_id: str, name: str) -> tuple[str, str]:
    base = f"ring/{LOC}/alarm/{device_id}"
    return (f"homeassistant/lock/{device_id}/lock/config", json.dumps({
        "name": "Lock", "unique_id": f"{device_id}_lock",
        "state_topic": f"{base}/lock/state", "command_topic": f"{base}/lock/command",
        "availability_topic": f"{base}/status", "device": {"name": name, "mdl": "Lock"},
    }))


class FakeClient:
    def __init__(self):
        self.published = []

    async def publish(self, topic, payload):
        self.published.append((topic, payload))


def nightly(**over):
    auto = {
        "name": "Nightly lock check",
        "trigger": {"type": "time", "time": "21:00", "days": [0, 1, 2, 3, 4, 5, 6]},
        "condition": {"mode": "any", "entities": [SIDE_ID, LAUNDRY_ID]},
        "notify": {"enabled": True, "timeSensitive": True, "onlyPending": True,
                   "buttons": [f"lock:{SIDE_ID}", f"lock:{LAUNDRY_ID}", "lock_all", "snooze:60", "dismiss"]},
        "recipients": ["p_chris", "p_marlina"],
        "followUp": {"enabled": True},
    }
    auto.update(over)
    return auto


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.store = Store(data)
        self.hub = Hub()
        self.notifier = Notifier(self.store)
        ctx = Context(self.hub, data, data / "snapshots")
        self.integrations = IntegrationManager(self.store, ctx)
        self.mqtt = MqttIntegration("mqtt", "MQTT devices", {"host": "fake"}, ctx)
        self.mqtt._client = FakeClient()
        self.integrations.instances["mqtt"] = self.mqtt
        self.engine = Engine(self.hub, self.store, self.notifier, self.integrations)
        for name in ("Chris", "Marlina"):
            p = self.store.add_person(name)
            self.store.register_phone(Phone(device_id=f"test-{p['id']}", token=f"t-{p['id']}", platform="test",
                                            person_id=p["id"], friendly_name=f"{name}'s phone"))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def msg(self, topic, payload):
        await self.mqtt._on_message(topic, payload.encode())
        await asyncio.sleep(0.02)

    async def boot(self, side="LOCKED", laundry="LOCKED"):
        await self.msg(f"ring/{LOC}/alarm/{SIDE}/lock/state", side)  # state before discovery
        for dev, name in ((SIDE, "Side Door"), (LAUNDRY, "Laundry")):
            await self.msg(*discovery(dev, name))
        await self.msg(f"ring/{LOC}/alarm/{LAUNDRY}/lock/state", laundry)

    def outbox(self, device="test-p_chris"):
        return [m for m in self.notifier.outbox if m["deviceId"] == device]

    async def tap(self, note, button, device="test-p_chris"):
        assert tokens.verify("action", note["action_subject"], device, note["action_exp"], note["action_token"])
        return await self.engine.press(note["action_subject"], button, device)


class DiscoveryTests(Base):
    async def test_discovery_and_states(self):
        await self.boot(side="UNLOCKED")
        locks = self.hub.all("lock")
        self.assertEqual([e.name for e in locks], ["Laundry", "Side Door"])
        self.assertEqual(self.hub.get(SIDE_ID).state, "UNLOCKED")

    async def test_command_publishes_payload(self):
        await self.boot(side="UNLOCKED")
        await self.engine.command(SIDE_ID, "lock", "Dashboard")
        self.assertEqual(self.mqtt._client.published[-1], (f"ring/{LOC}/alarm/{SIDE}/lock/command", "LOCK"))


class NightlyTests(Base):
    async def test_nothing_sent_when_all_locked(self):
        await self.boot()
        auto = self.engine.save(nightly())
        run = await self.engine.trigger(auto, {"type": "time"})
        self.assertEqual(run["status"], "skipped")
        self.assertEqual(list(self.notifier.outbox), [])

    async def test_full_flow_lock_all_then_follow_up_names_who(self):
        await self.boot(side="UNLOCKED", laundry="UNLOCKED")
        auto = self.engine.save(nightly())
        run = await self.engine.trigger(auto, {"type": "time"})
        self.assertEqual(run["status"], "sent")
        note = self.outbox()[0]
        self.assertEqual(note["body"], "Side Door and Laundry are still unlocked.")  # in the order they were picked
        labels = [b["label"] for b in json.loads(note["buttons"])]
        self.assertEqual(labels, ["Lock Side Door", "Lock Laundry", "Lock all", "Snooze 1 hour", "Dismiss"])
        self.assertEqual(len(self.outbox("test-p_marlina")), 1)  # both people got it

        await self.tap(note, "lock_all")
        self.assertEqual(sorted(p for _, p in self.mqtt._client.published), ["LOCK", "LOCK"])
        await self.msg(f"ring/{LOC}/alarm/{SIDE}/lock/state", "LOCKED")
        await self.msg(f"ring/{LOC}/alarm/{LAUNDRY}/lock/state", "LOCKED")
        await asyncio.sleep(0.05)
        follow = [m for m in self.notifier.outbox if m["kind"] == "followup"]
        self.assertEqual({m["deviceId"] for m in follow}, {"test-p_chris", "test-p_marlina"})
        self.assertEqual(follow[0]["body"], "Chris locked Side Door and Laundry.")
        self.assertEqual(self.store.runs(auto["id"])[0]["status"], "resolved")

    async def test_only_pending_buttons_and_manual_lock_follow_up(self):
        await self.boot(side="UNLOCKED")
        auto = self.engine.save(nightly())
        await self.engine.trigger(auto, {"type": "time"})
        labels = [b["label"] for b in json.loads(self.outbox()[0]["buttons"])]
        self.assertEqual(labels, ["Lock Side Door", "Snooze 1 hour", "Dismiss"])  # no Laundry, no "all"
        await self.msg(f"ring/{LOC}/alarm/{SIDE}/lock/state", "LOCKED")  # someone turned the thumb-turn
        await asyncio.sleep(0.05)
        follow = [m for m in self.notifier.outbox if m["kind"] == "followup"]
        self.assertEqual(follow[0]["body"], "Someone at the door locked Side Door.")

    async def test_rejects_button_the_run_did_not_offer(self):
        await self.boot(side="UNLOCKED")
        auto = self.engine.save(nightly())
        await self.engine.trigger(auto, {"type": "time"})
        with self.assertRaises(ActionError):
            await self.tap(self.outbox()[0], f"unlock:{SIDE_ID}")
        with self.assertRaises(ActionError):
            await self.tap(self.outbox()[0], f"lock:{LAUNDRY_ID}")  # Laundry wasn't pending

    async def test_snooze_resends_and_lock_still_works_meanwhile(self):
        await self.boot(side="UNLOCKED")
        auto = self.engine.save(nightly(notify={**nightly()["notify"], "buttons": [f"lock:{SIDE_ID}", "snooze:1"]}))
        await self.engine.trigger(auto, {"type": "time"})
        note = self.outbox()[0]
        orig_sleep = asyncio.sleep
        engine_mod.asyncio.sleep = lambda s, *a, **k: orig_sleep(0.05 if s >= 1 else s)
        try:
            await self.tap(note, "snooze:1")
            await orig_sleep(0.2)
            self.assertEqual(self.outbox()[0]["title"], "Reminder · Nightly lock check")
            await self.tap(note, f"lock:{SIDE_ID}")  # the original notification's button still works
            self.assertEqual(self.mqtt._client.published[-1][1], "LOCK")
        finally:
            engine_mod.asyncio.sleep = orig_sleep

    async def test_always_check_describes_actual_state(self):
        # Seen live: an "Always" check with both doors locked said "still unlocked".
        await self.boot()
        auto = self.engine.save(nightly(condition={"mode": "always", "entities": [SIDE_ID, LAUNDRY_ID]}))
        await self.engine.trigger(auto, {"type": "time"})
        self.assertEqual(self.outbox()[0]["body"], "Side Door and Laundry are locked.")

    async def test_save_ignores_read_only_fields(self):
        await self.boot()
        auto = self.engine.save({**nightly(), "summary": "x", "lastRun": {"id": "r"}, "nextRun": 1.0})
        self.assertNotIn("summary", self.store.automations()[0])

    async def test_dismiss_closes_run(self):
        await self.boot(side="UNLOCKED")
        auto = self.engine.save(nightly())
        run = await self.engine.trigger(auto, {"type": "time"})
        await self.tap(self.outbox()[0], "dismiss")
        self.assertNotIn(run["id"], self.engine.open_runs)
        self.assertEqual(self.store.runs(auto["id"])[0]["status"], "dismissed")


class TriggerTests(Base):
    async def test_state_trigger_lock_activity(self):
        await self.boot()
        self.engine.save({"name": "Lock activity", "trigger": {"type": "state", "entities": [SIDE_ID, LAUNDRY_ID], "to": "any"},
                          "condition": {"mode": "always"}, "notify": {"enabled": True}, "recipients": ["p_chris"]})
        await self.msg(f"ring/{LOC}/alarm/{SIDE}/lock/state", "UNLOCKED")
        await asyncio.sleep(0.05)
        self.assertEqual(self.outbox()[0]["title"], "Side Door unlocked")

    async def test_refused_unlock_is_not_a_change(self):
        # Seen live: ring-mqtt reports UNLOCKING the moment the command is sent,
        # then LOCKED when Ring refuses it. That's no change at all.
        await self.boot()
        self.engine.save({"name": "Lock activity", "trigger": {"type": "state", "entities": [SIDE_ID], "to": "any"},
                          "condition": {"mode": "always"}, "notify": {"enabled": True}, "recipients": ["p_chris"]})
        await self.msg(f"ring/{LOC}/alarm/{SIDE}/lock/state", "UNLOCKING")
        await self.msg(f"ring/{LOC}/alarm/{SIDE}/lock/state", "LOCKED")
        await asyncio.sleep(0.05)
        self.assertEqual(self.outbox(), [])
        await self.msg(f"ring/{LOC}/alarm/{SIDE}/lock/state", "UNLOCKING")
        await self.msg(f"ring/{LOC}/alarm/{SIDE}/lock/state", "UNLOCKED")
        await asyncio.sleep(0.05)
        self.assertEqual([m["title"] for m in self.outbox()], ["Side Door unlocked"])

    async def test_stale_report_mid_motion_is_not_a_refusal(self):
        # Seen live: LOCKING -> UNLOCKED (stale) -> LOCKED within ~5 s on a lock that worked.
        engine_mod.REFUSAL_SETTLE_S = 0.1
        await self.boot(side="UNLOCKED")
        await self.engine.command(SIDE_ID, "lock", "Dashboard")
        await self.msg(f"ring/{LOC}/alarm/{SIDE}/lock/state", "LOCKING")
        await self.msg(f"ring/{LOC}/alarm/{SIDE}/lock/state", "UNLOCKED")
        await self.msg(f"ring/{LOC}/alarm/{SIDE}/lock/state", "LOCKED")
        await asyncio.sleep(0.2)
        self.assertEqual(self.hub.get(SIDE_ID).health_issues, [])
        self.assertEqual(self.store.events(kind="problem"), [])

    async def test_refused_command_reported_quickly(self):
        engine_mod.REFUSAL_SETTLE_S = 0.1
        await self.boot()
        await self.engine.command(SIDE_ID, "unlock", "Dashboard")
        await self.msg(f"ring/{LOC}/alarm/{SIDE}/lock/state", "UNLOCKING")
        await self.msg(f"ring/{LOC}/alarm/{SIDE}/lock/state", "LOCKED")
        await asyncio.sleep(0.2)
        self.assertEqual(self.hub.get(SIDE_ID).attrs.get("commandFailures"), 1)
        self.assertIn("Ignored 1 remote command", self.hub.get(SIDE_ID).health_issues)
        self.assertEqual(self.store.events(kind="problem")[0]["title"], "Couldn't unlock Side Door")
        # A later command that works clears the warning.
        await self.engine.command(SIDE_ID, "unlock", "Dashboard")
        await self.msg(f"ring/{LOC}/alarm/{SIDE}/lock/state", "UNLOCKED")
        await asyncio.sleep(0.05)
        self.assertEqual(self.hub.get(SIDE_ID).health_issues, [])

    async def test_change_while_offline_still_triggers(self):
        await self.boot(side="UNLOCKED")
        self.engine.save({"name": "Lock activity", "trigger": {"type": "state", "entities": [SIDE_ID], "to": "LOCKED"},
                          "condition": {"mode": "always"}, "notify": {"enabled": True}, "recipients": ["p_chris"]})
        await self.msg(f"ring/{LOC}/alarm/{SIDE}/status", "offline")
        await self.msg(f"ring/{LOC}/alarm/{SIDE}/lock/state", "LOCKED")
        await asyncio.sleep(0.05)
        self.assertEqual(self.outbox()[0]["title"], "Side Door locked")

    async def test_doorbell_event_with_snapshot(self):
        self.hub.upsert("reolink.doorbell", kind="doorbell", name="Front Door", integration="reolink")
        self.engine.save({"name": "Doorbell alert", "trigger": {"type": "event", "entity": "reolink.doorbell", "event": "pressed"},
                          "notify": {"enabled": True, "timeSensitive": True}, "recipients": ["p_chris", "p_marlina"]})
        await self.hub.emit("reolink.doorbell", "pressed", {"snapshot_id": "20260929-2100-abcd"})
        await asyncio.sleep(0.05)
        note = self.outbox()[0]
        self.assertEqual(note["title"], "Doorbell")
        self.assertIn("/api/public/snapshots/20260929-2100-abcd?", note["snapshot_url"])
        self.assertEqual(note["time_sensitive"], "true")


class DescribeTests(Base):
    async def test_summary_and_preview(self):
        await self.boot()
        out = self.engine.describe(nightly(followUp={"enabled": True, "remindMinutes": 30}))
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["summary"],
                         "At 9:00 PM every day, if any of Side Door or Laundry is unlocked, notify Chris and Marlina "
                         "with Lock Side Door, Lock Laundry, Lock all, Snooze 1 hour and Dismiss buttons. "
                         "Once they're locked, tell everyone who did it. If not, remind again every 30 minutes.")
        self.assertEqual(out["preview"]["followUp"], "Chris locked Side Door and Laundry.")

    async def test_errors(self):
        out = self.engine.describe({"trigger": {"type": "time", "days": []}, "condition": {"mode": "any", "entities": []}})
        self.assertIn("Pick at least one day", out["errors"])
        self.assertIn("Pick who gets the notification", out["errors"])

    async def test_time_automation_without_devices_just_runs(self):
        await self.boot(side="UNLOCKED")
        auto = {"name": "9 PM lock", "trigger": {"type": "time", "time": "21:00", "days": [0, 1, 2, 3, 4, 5, 6]},
                "condition": {"mode": "any", "entities": []}, "notify": {"enabled": False},
                "actions": [{"entity": SIDE_ID, "command": "lock"}]}
        out = self.engine.describe(auto)
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["summary"], "At 9:00 PM every day, lock Side Door.")
        run = await self.engine.trigger(self.engine.save(auto), {"type": "time"})
        self.assertNotEqual(run["status"], "skipped")
        self.assertEqual(self.mqtt._client.published[-1][1], "LOCK")

    async def test_categories_cover_every_pending_subset(self):
        await self.boot()
        self.engine.save(nightly())
        cats = self.engine.categories()
        self.assertEqual(len(cats), 3)  # {Side}, {Laundry}, {Side, Laundry}


class LightTests(Base):
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

    async def test_brightness_needs_value_and_passes_it(self):
        with self.assertRaises(ActionError):
            await self.engine.command("lutron.device_5", "set_brightness", "Dashboard")
        await self.engine.command("lutron.device_5", "set_brightness", "Dashboard", 40)
        self.assertEqual(self.calls[-1], ("lutron.device_5", "set_brightness", 40))

    async def test_brightness_is_never_a_notification_button(self):
        out = self.engine.describe({"trigger": {"type": "state", "entities": ["lutron.device_5"], "to": "on"},
                                    "notify": {"enabled": True, "buttons": ["set_brightness:lutron.device_5",
                                                                            "turn_off:lutron.device_5"]},
                                    "recipients": ["p_chris"]})
        self.assertEqual([b["label"] for b in out["preview"]["buttons"]], ["Turn off Porch Light"])

    async def test_pico_button_starts_automation(self):
        auto = {"name": "Pico test", "trigger": {"type": "event", "entity": "lutron.remote_9", "event": "off"},
                "notify": {"enabled": True}, "recipients": ["p_chris"]}
        self.assertTrue(self.engine.describe(auto)["summary"].startswith(
            "When the Off button on Kitchen Pico Remote is pressed"))
        self.engine.save(auto)
        await self.hub.emit("lutron.remote_9", "on", {})   # a different button: nothing
        await asyncio.sleep(0.05)
        self.assertEqual(self.outbox(), [])
        await self.hub.emit("lutron.remote_9", "off", {})
        await asyncio.sleep(0.05)
        self.assertEqual(self.outbox()[0]["body"].split(" · ")[0], "Off pressed on Kitchen Pico Remote")


class ActionTests(LightTests):
    async def test_pico_button_turns_on_light_without_notifying(self):
        auto = {"name": "Kitchen Pico – On", "trigger": {"type": "event", "entity": "lutron.remote_9", "event": "on"},
                "notify": {"enabled": False}, "actions": [{"entity": "lutron.device_5", "command": "set_brightness", "value": 80}]}
        out = self.engine.describe(auto)
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["summary"], "When the On button on Kitchen Pico Remote is pressed, set Porch Light to 80%.")
        self.engine.save(auto)
        await self.hub.emit("lutron.remote_9", "on", {})
        await asyncio.sleep(0.05)
        self.assertEqual(self.calls[-1], ("lutron.device_5", "set_brightness", 80))
        self.assertEqual(self.outbox(), [])
        self.assertEqual(len(self.engine.used_by("lutron.device_5")), 1)  # the light knows it's used

    async def test_snapshot_only_mentioned_for_cameras(self):
        remote = {"trigger": {"type": "event", "entity": "lutron.remote_9", "event": "lower"},
                  "notify": {"enabled": True}, "recipients": ["p_chris"]}
        out = self.engine.describe(remote)
        self.assertNotIn("snapshot", out["summary"])
        self.assertFalse(out["preview"]["snapshot"])
        self.assertIsNone(out["preview"]["snapshotSource"])
        self.hub.upsert("reolink.doorbell", kind="doorbell", name="Front Door", integration="reolink")
        bell = dict(remote, trigger={"type": "event", "entity": "reolink.doorbell", "event": "pressed"})
        out = self.engine.describe(bell)
        self.assertIn("with a snapshot from Front Door", out["summary"])
        self.assertEqual(out["preview"]["snapshotSource"], "Front Door")
        out = self.engine.describe(dict(bell, notify={"enabled": True, "snapshot": False}))
        self.assertNotIn("snapshot", out["summary"])

    async def _mixed_devices(self):
        self.hub.upsert("lutron.device_6", kind="light", name="Dining Room", integration="lutron")
        await self.hub.update("lutron.device_6", state="off", attrs={"dimmable": True, "brightness": 0})
        self.hub.upsert("lutron.lock", kind="lock", name="Side Door", integration="lutron")
        await self.hub.update("lutron.lock", state="LOCKED")
        self.hub.upsert("lutron.garage", kind="garage", name="Garage", integration="lutron")
        await self.hub.update("lutron.garage", state="closed")

    async def test_when_all_of_these_are_true(self):
        await self._mixed_devices()
        auto = {"name": "Silly", "notify": {"enabled": False},
                "trigger": {"type": "state", "match": "all", "items": [
                    {"entities": ["lutron.device_5", "lutron.device_6"], "to": "on"},
                    {"entities": ["lutron.lock"], "to": "UNLOCKED"}]},
                "actions": [{"entities": ["lutron.garage"], "command": "open"}]}
        out = self.engine.describe(auto)
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["summary"], "When Porch Light and Dining Room are on and Side Door is unlocked, open Garage.")
        self.engine.save(auto)
        await self.hub.update("lutron.device_5", state="on")
        await self.hub.update("lutron.lock", state="UNLOCKED")
        await asyncio.sleep(0.05)
        self.assertEqual(self.calls, [])  # Dining Room is still off
        await self.hub.update("lutron.device_6", state="on")  # the last piece falls into place
        await asyncio.sleep(0.05)
        self.assertEqual(self.calls, [("lutron.garage", "open", None)])
        out = self.engine.describe(dict(auto, trigger=dict(auto["trigger"], match="any")))
        self.assertTrue(out["summary"].startswith("When Porch Light or Dining Room becomes on or Side Door becomes unlocked"))
        bad = dict(auto, trigger={"type": "state", "match": "all", "items": [{"entities": ["lutron.lock"], "to": "any"}, {"entities": ["lutron.device_6"], "to": "on"}]})
        self.assertIn("When all of them must be true, pick the state for each device", self.engine.describe(bad)["errors"])

    async def test_mixed_when_events_and_states(self):
        await self._mixed_devices()
        self.hub.upsert("frigate.garage", kind="camera", name="Garage", integration="frigate")
        self.hub.upsert("frigate.backyard", kind="camera", name="Backyard", integration="frigate")
        await self.hub.update("lutron.device_6", state="on")
        auto = {"name": "Cat and person", "notify": {"enabled": False},
                "trigger": {"match": "all", "items": [
                    {"type": "event", "entity": "frigate.garage", "event": "cat"},
                    {"type": "event", "entity": "frigate.backyard", "event": "person"},
                    {"type": "state", "entities": ["lutron.device_6"], "to": "on"}]},
                "actions": [{"entities": ["lutron.device_5"], "command": "turn_on"}]}
        out = self.engine.describe(auto)
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["summary"], "When a cat is detected on Garage, a person is detected on Backyard and "
                                         "Dining Room is on (within 2 minutes of each other), turn on Porch Light.")
        self.engine.save(auto)
        await self.hub.emit("frigate.garage", "cat", {})
        await asyncio.sleep(0.05)
        self.assertEqual(self.calls, [])  # no person yet
        await self.hub.emit("frigate.backyard", "person", {})
        await asyncio.sleep(0.05)
        self.assertEqual(self.calls, [("lutron.device_5", "turn_on", None)])
        await self.hub.emit("frigate.backyard", "person", {})  # the cat has to be seen again first
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.calls), 1)
        # "any": a time, a state and an event side by side
        anyof = {"name": "Any", "notify": {"enabled": False}, "trigger": {"match": "any", "items": [
                    {"type": "time", "time": "22:00", "days": [0, 1, 2, 3, 4, 5, 6]},
                    {"type": "event", "entity": "frigate.garage", "event": "cat"},
                    {"type": "state", "entities": ["lutron.lock"], "to": "UNLOCKED"}]},
                 "actions": [{"entities": ["lutron.device_5"], "command": "turn_off"}]}
        self.assertEqual(self.engine.describe(anyof)["summary"],
                         "When it's 10:00 PM, a cat is detected on Garage or Side Door becomes unlocked, turn off Porch Light.")
        saved = self.engine.save(anyof)
        self.assertIsNotNone(self.engine.next_run(saved))

    async def test_any_reported_value_can_start_or_gate_an_automation(self):
        from newman.kinds import properties_for
        self.hub.upsert("hk.main", kind="thermostat", name="Main Floor", integration="lutron")
        await self.hub.update("hk.main", state="cool", attrs={"currentTemp": 22.5, "targetTemp": 23, "humidity": 55,
                                                              "action": "idle", "model": "ecobee3", "fanSpeed": 2})
        props = {p["id"]: p for p in properties_for(self.hub.get("hk.main"))}
        self.assertEqual(props["humidity"]["unit"], "%")
        self.assertEqual(props["fanSpeed"]["label"], "Fan speed")  # not declared anywhere: still offered
        self.assertNotIn("model", props)  # descriptive details aren't
        auto = {"name": "Humid", "notify": {"enabled": False},
                "trigger": {"items": [{"type": "property", "entities": ["hk.main"], "property": "humidity",
                                       "op": "above", "value": 60}]},
                "condition": {"devices": [{"entities": ["hk.main"], "property": "action", "op": "is_not",
                                           "value": "cooling", "match": "any"}]},
                "actions": [{"entities": ["lutron.device_5"], "command": "turn_on"}]}
        out = self.engine.describe(auto)
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["summary"], "When Main Floor's humidity goes above 60%, if Main Floor's activity isn't "
                                         "cooling, turn on Porch Light.")
        self.engine.save(auto)
        await self.hub.update("hk.main", attrs={"humidity": 58})
        await asyncio.sleep(0.05)
        self.assertEqual(self.calls, [])
        await self.hub.update("hk.main", attrs={"humidity": 61})  # crosses 60
        await asyncio.sleep(0.05)
        self.assertEqual(self.calls, [("lutron.device_5", "turn_on", None)])
        await self.hub.update("hk.main", attrs={"humidity": 63})  # still above: no repeat
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.calls), 1)
        bad = dict(auto, trigger={"items": [{"type": "property", "entities": ["hk.main"], "property": "humidity",
                                             "op": "is_not"}]})
        self.assertIn("Pick how to compare humidity", self.engine.describe(bad)["errors"])

    async def test_appliance_done_when_power_stays_low_for_a_while(self):
        self.hub.upsert("plug.dish", kind="switch", name="Dishwasher", integration="lutron")
        await self.hub.update("plug.dish", state="on", attrs={"power": 1200})
        auto = {"name": "Dishwasher done", "notify": {"enabled": False},
                "trigger": {"items": [{"type": "property", "entities": ["plug.dish"], "property": "power",
                                       "op": "below", "value": 5, "for": {"amount": 0.3, "unit": "seconds"}}]},
                "actions": [{"entities": ["lutron.device_5"], "command": "turn_on"}]}
        out = self.engine.describe(auto)
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["summary"], "When Dishwasher's power stays below 5 W for 0.3 seconds, turn on Porch Light.")
        self.engine.save(auto)
        await self.hub.update("plug.dish", attrs={"power": 3})     # drops below 5 W…
        await asyncio.sleep(0.1)
        await self.hub.update("plug.dish", attrs={"power": 800})   # …but the drying cycle kicks in: start over
        await asyncio.sleep(0.4)
        self.assertEqual(self.calls, [])
        await self.hub.update("plug.dish", attrs={"power": 2})     # really done this time
        await asyncio.sleep(0.15)
        await self.hub.update("plug.dish", attrs={"power": 1})     # still low: keeps counting, not restarted
        await asyncio.sleep(0.4)
        self.assertEqual(self.calls, [("lutron.device_5", "turn_on", None)])
        bad = dict(auto, trigger={"items": [{"type": "property", "entities": ["plug.dish"], "property": "power",
                                             "op": "changes", "for": {"amount": 3, "unit": "minutes"}}]})
        self.assertTrue(any("needs a comparison" in e for e in self.engine.describe(bad)["errors"]))

    async def test_door_left_unlocked_for_a_while(self):
        await self._mixed_devices()
        auto = {"name": "Left unlocked", "notify": {"enabled": False},
                "trigger": {"items": [{"type": "state", "entities": ["lutron.lock"], "to": "UNLOCKED",
                                       "for": {"amount": 0.3, "unit": "seconds"}}]},
                "actions": [{"entities": ["lutron.lock"], "command": "lock"}]}
        self.assertEqual(self.engine.describe(auto)["summary"],
                         "When Side Door stays unlocked for 0.3 seconds, lock Side Door.")
        self.engine.save(auto)
        await self.hub.update("lutron.lock", state="UNLOCKED")
        await asyncio.sleep(0.1)
        await self.hub.update("lutron.lock", state="LOCKED")       # locked again in time: nothing
        await asyncio.sleep(0.4)
        self.assertEqual(self.calls, [])
        await self.hub.update("lutron.lock", state="UNLOCKED")
        await asyncio.sleep(0.5)
        self.assertEqual(self.calls[-1][:2], ("lutron.lock", "lock"))

    async def test_scheduled_battery_check_lists_low_devices(self):
        await self._mixed_devices()
        for eid, name, level in (("r.motion", "Basement Motion Sensor", 15), ("r.keypad", "Hallway Keypad", 0),
                                 ("r.back", "Back Door", 100)):
            self.hub.upsert(eid, kind="motion" if eid != "r.back" else "contact", name=name, integration="lutron")
            await self.hub.update(eid, state="clear" if eid != "r.back" else "closed", attrs={"batteryLevel": level})
        auto = self.engine.save({
            "name": "Low batteries", "trigger": {"items": [{"type": "time", "time": "09:00"}]},
            "condition": {"devices": [{"entities": ["r.motion", "r.keypad", "r.back"], "property": "battery",
                                       "op": "below", "value": 20, "match": "any"}]},
            "notify": {"enabled": True}, "recipients": ["p_chris"]})
        self.assertEqual(self.engine.describe(auto)["summary"],
                         "At 9:00 AM every day, if the battery at Basement Motion Sensor, Hallway Keypad or Back Door "
                         "is below 20%, notify Chris.")
        run = await self.engine.trigger(auto, {"type": "time"})
        self.assertEqual(sorted(run["pending"]), ["r.keypad", "r.motion"])
        self.assertEqual(self.outbox()[-1]["body"],
                         "Battery is below 20%: Basement Motion Sensor (15%) and Hallway Keypad (0%).")

    async def test_a_new_kind_needs_no_describer_changes(self):
        from newman.kinds import KINDS, Event, Kind
        KINDS["test_leak"] = Kind("test_leak", "Leak sensor", "Leak sensors", "other", [("dry", "Dry"), ("wet", "Wet")],
                             active_state="wet", noun="sensor",
                             events=[Event("leak", "Detects a leak", "{device} detects a leak", "Leak!",
                                           "Water under {device}"), Event("battery_low", "Battery low")])
        try:
            self.hub.upsert("x.sink", kind="test_leak", name="Sink", integration="lutron")
            auto = {"trigger": {"items": [{"type": "event", "entity": "x.sink", "event": "leak"}]},
                    "notify": {"enabled": True}, "recipients": ["p_chris"]}
            out = self.engine.describe(auto)
            self.assertEqual(out["summary"], "When Sink detects a leak, notify Chris.")
            self.assertEqual(out["preview"]["title"], "Leak!")
            self.assertTrue(out["preview"]["body"].startswith("Water under Sink"))
            self.assertIsNone(out["preview"]["snapshotSource"])  # no camera on this kind
            plain = dict(auto, trigger={"items": [{"type": "event", "entity": "x.sink", "event": "battery_low"}]})
            self.assertEqual(self.engine.describe(plain)["summary"], "When Sink reports “battery low”, notify Chris.")
        finally:
            KINDS.pop("test_leak")

    async def test_several_only_if_rows_each_with_its_own_state(self):
        await self._mixed_devices()
        auto = self.engine.save({
            "name": "Pico", "notify": {"enabled": False},
            "trigger": {"type": "event", "entity": "lutron.remote_9", "event": "on"},
            "condition": {"devices": [{"entities": ["lutron.device_6"], "state": "on", "match": "any"},
                                      {"entities": ["lutron.lock"], "state": "LOCKED", "match": "any"}]},
            "actions": [{"entities": ["lutron.device_5"], "command": "turn_on"}]})
        self.assertEqual(self.engine.describe(auto)["summary"],
                         "When the On button on Kitchen Pico Remote is pressed, if Dining Room is on and Side Door is locked, "
                         "turn on Porch Light.")
        run = await self.engine.trigger(auto, {"type": "event", "entity": "lutron.remote_9", "event": "on"})
        self.assertEqual(run["status"], "skipped")  # Dining Room is off
        await self.hub.update("lutron.device_6", state="on")
        run = await self.engine.trigger(auto, {"type": "event", "entity": "lutron.remote_9", "event": "on"})
        self.assertNotEqual(run["status"], "skipped")
        self.assertIn(("lutron.device_5", "turn_on", None), self.calls)

    async def test_one_action_on_several_devices(self):
        self.hub.upsert("lutron.device_6", kind="light", name="Dining Room", integration="lutron")
        await self.hub.update("lutron.device_6", state="off", attrs={"dimmable": True, "brightness": 0})
        auto = {"name": "Pico on", "trigger": {"type": "event", "entity": "lutron.remote_9", "event": "on"},
                "notify": {"enabled": False},
                "actions": [{"entities": ["lutron.device_5", "lutron.device_6"], "command": "turn_on"}]}
        out = self.engine.describe(auto)
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["summary"],
                         "When the On button on Kitchen Pico Remote is pressed, turn on Porch Light and Dining Room.")
        self.engine.save(auto)
        await self.hub.emit("lutron.remote_9", "on", {})
        await asyncio.sleep(0.05)
        self.assertEqual(sorted(c[0] for c in self.calls), ["lutron.device_5", "lutron.device_6"])
        self.assertEqual(len(self.engine.used_by("lutron.device_6")), 1)
        bad = dict(auto, actions=[{"entities": [], "command": "turn_on"}])
        self.assertIn("Pick a device for each action", self.engine.describe(bad)["errors"])

    async def test_only_on_certain_days(self):
        from datetime import datetime
        today = datetime.now().weekday()
        other = [d for d in range(7) if d != today]
        auto = {"name": "Weekday light", "trigger": {"type": "event", "entity": "lutron.remote_9", "event": "on"},
                "condition": {"days": [0, 1, 2, 3, 4]}, "notify": {"enabled": False},
                "actions": [{"entity": "lutron.device_5", "command": "turn_on"}]}
        self.assertEqual(self.engine.describe(auto)["summary"],
                         "When the On button on Kitchen Pico Remote is pressed, only on weekdays, turn on Porch Light.")
        saved = self.engine.save(dict(auto, condition={"days": other}))
        run = await self.engine.trigger(saved, {"type": "event", "entity": "lutron.remote_9", "event": "on"})
        self.assertEqual(run["status"], "skipped")
        self.assertEqual(self.calls, [])
        saved = self.engine.save(dict(auto, id=saved["id"], condition={"days": [today]}))
        run = await self.engine.trigger(saved, {"type": "event", "entity": "lutron.remote_9", "event": "on"})
        self.assertNotEqual(run["status"], "skipped")
        self.assertEqual(self.calls[-1][:2], ("lutron.device_5", "turn_on"))

    async def test_timed_action_restores_previous_state_and_retrigger_extends(self):
        orig_sleep = asyncio.sleep
        engine_mod.asyncio.sleep = lambda s, *a, **k: orig_sleep(0.15 if s >= 60 else s)

        async def fake_cmd(eid, command, value=None):
            self.calls.append((eid, command, value))
            if command == "set_brightness":
                await self.hub.update(eid, state="on", attrs={"brightness": value})
            elif command == "turn_off":
                await self.hub.update(eid, state="off", attrs={"brightness": 0})
        self.integrations.instances["lutron"].command = fake_cmd
        try:
            auto = self.engine.save({"name": "Porch light", "trigger": {"type": "event", "entity": "lutron.remote_9", "event": "on"},
                                     "notify": {"enabled": False},
                                     "actions": [{"entity": "lutron.device_5", "command": "set_brightness", "value": 45,
                                                  "for": {"amount": 5, "unit": "minutes"}}]})
            self.assertIn("for 5 minutes (then put it back)", self.engine.describe(auto)["summary"])
            await self.hub.emit("lutron.remote_9", "on", {})
            await orig_sleep(0.08)
            self.assertEqual(len(self.store.holds()), 1)  # persisted, so it survives a restart
            await self.hub.emit("lutron.remote_9", "on", {})   # re-trigger restarts the clock
            await orig_sleep(0.1)
            self.assertNotIn(("lutron.device_5", "turn_off", None), self.calls)  # not yet: timer was restarted
            await orig_sleep(0.15)
            self.assertEqual(self.calls[-1], ("lutron.device_5", "turn_off", None))  # it was off before -> off again
            self.assertEqual(self.store.holds(), {})
        finally:
            engine_mod.asyncio.sleep = orig_sleep

    async def test_duration_units_and_resume_after_restart(self):
        from newman.automations import describe as d
        self.assertEqual(d.duration_text({"for": {"amount": 2, "unit": "hours"}}), "2 hours")
        self.assertEqual(d.duration_text({"for": {"amount": 1, "unit": "days"}}), "1 day")
        self.assertEqual(d.hold_seconds({"for": {"amount": 3, "unit": "days"}}), 3 * 86400)
        # A hold that came due while Newman was down is carried out on start-up.
        await self.hub.update("lutron.device_5", state="on", attrs={"brightness": 100})
        self.store.save_hold("auto_x|lutron.device_5", {"entity": "lutron.device_5", "before": {"state": "off"},
                                                        "command": "turn_on", "value": None, "due": 0})
        self.engine.resume_holds()
        await asyncio.sleep(0.05)
        self.assertEqual(self.calls[-1], ("lutron.device_5", "turn_off", None))

    async def test_timed_action_leaves_manual_changes_alone(self):
        orig_sleep = asyncio.sleep
        engine_mod.asyncio.sleep = lambda s, *a, **k: orig_sleep(0.1 if s >= 60 else s)
        self.integrations.instances["lutron"].command = lambda eid, command, value=None: _noop()
        try:
            self.engine.save({"name": "t", "trigger": {"type": "event", "entity": "lutron.remote_9", "event": "on"},
                              "notify": {"enabled": False},
                              "actions": [{"entity": "lutron.device_5", "command": "turn_on", "forMinutes": 1}]})
            await self.hub.emit("lutron.remote_9", "on", {})
            await orig_sleep(0.03)
            await self.hub.update("lutron.device_5", state="off")  # someone switched it off by hand... then
            await orig_sleep(0.15)
            self.assertIn("Left Porch Light as it is", [e["title"] for e in self.store.events()])
        finally:
            engine_mod.asyncio.sleep = orig_sleep

    async def test_doorbell_after_dark_only_inside_window(self):
        from datetime import datetime
        from newman.automations import describe as d
        self.hub.upsert("reolink.doorbell", kind="doorbell", name="Front Door", integration="reolink")
        now = datetime.now().strftime("%H:%M")
        self.assertTrue(d.in_window({"after": "19:00", "before": "06:00"}, datetime(2026, 1, 1, 23, 30)))
        self.assertTrue(d.in_window({"after": "19:00", "before": "06:00"}, datetime(2026, 1, 1, 5, 59)))
        self.assertFalse(d.in_window({"after": "19:00", "before": "06:00"}, datetime(2026, 1, 1, 12, 0)))
        closed = {"after": "00:00", "before": "00:01"} if now != "00:00" else {"after": "12:00", "before": "12:01"}
        self.engine.save({"name": "Porch light", "trigger": {"type": "event", "entity": "reolink.doorbell", "event": "pressed"},
                          "condition": {"between": closed}, "notify": {"enabled": False},
                          "actions": [{"entity": "lutron.device_5", "command": "turn_on"}]})
        await self.hub.emit("reolink.doorbell", "pressed", {})
        await asyncio.sleep(0.05)
        self.assertEqual(self.calls, [])  # outside the window: nothing happens
        self.assertEqual(self.store.runs()[0]["status"], "skipped")


class MerossSetupTests(Base):
    async def test_sign_in_key_replaces_password(self):
        from newman.integrations.meross import MerossIntegration
        self.assertTrue(MerossIntegration.validate({"host": "10.1.1.35"}))  # needs a login or a key
        self.assertEqual(MerossIntegration.validate({"host": "10.1.1.35", "email": "a@b.c", "password": "x"}), [])
        rec = {"id": "meross", "type": "meross", "name": "Garage",
               "config": {"host": "10.1.1.35", "email": "a@b.c", "password": "secret", "mfa": ""}}
        self.store.save_integration(rec)
        m = MerossIntegration("meross", "Garage", dict(rec["config"]), self.integrations._ctx)
        m.save_config = lambda cfg: self.integrations._persist("meross", cfg)

        async def fake_key():
            return "the-device-key"
        m._fetch_key = fake_key
        m._request = None  # stop right after the key step
        task = asyncio.create_task(m.run())
        await asyncio.sleep(0.1)
        task.cancel()
        saved = self.store.integrations()[0]["config"]
        self.assertEqual(saved.get("key"), "the-device-key")
        self.assertNotIn("password", saved)
        self.assertNotIn("email", saved)


class FrigateTests(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        from newman.integrations.frigate import FrigateIntegration
        self.fr = FrigateIntegration("frigate", "Frigate", {"url": "http://x"}, self.integrations._ctx)
        self.fr.ctx.hub_log = self.store.log_event
        self.fr._cams = {"garage": {"eid": "frigate.camera_garage", "track": ["person", "car"], "zones": ["driveway"]}}
        self.fr._zones = {"driveway": {"eid": "frigate.zone_driveway", "camera": "garage", "objects": ["person", "car"]}}
        self.hub.upsert("frigate.camera_garage", kind="camera", name="Garage", integration="frigate")
        self.hub.upsert("frigate.zone_driveway", kind="zone", name="Driveway", integration="frigate")
        await self.hub.update("frigate.zone_driveway", state="clear", attrs={"counts": {"person": 0, "car": 0}})

        async def no_snapshot(rid, cam, detection_id):
            return "snap-1"
        self.fr._snapshot = no_snapshot

    def review(self, type_, severity="alert", objects=("car",), zones=(), rid="r1"):
        return {"type": type_, "after": {"id": rid, "camera": "garage", "severity": severity,
                                         "data": {"objects": list(objects), "zones": list(zones), "detections": ["d1"]}}}

    async def test_zone_counts_and_state(self):
        await self.fr._on_message("frigate", "frigate/driveway/person", b"2")
        z = self.hub.get("frigate.zone_driveway")
        self.assertEqual((z.state, z.attrs["counts"]["person"]), ("occupied", 2))
        await self.fr._on_message("frigate", "frigate/driveway/person", b"0")
        self.assertEqual(self.hub.get("frigate.zone_driveway").state, "clear")

    async def test_alert_fires_once_per_object_and_zone_and_logs_once(self):
        events = []
        self.hub.subscribe_events(lambda ent, e, data: _collect(events, ent.id, e, data))
        await self.fr._on_review(self.review("new"))
        await self.fr._on_review(self.review("update", zones=["driveway"]))
        await self.fr._on_review(self.review("update", zones=["driveway"]))  # still there: nothing new
        await self.fr._on_review(self.review("update", objects=("car", "person"), zones=["driveway"]))
        await asyncio.sleep(0.05)
        self.assertEqual([(e, x) for e, x, _ in events],
                         [("frigate.camera_garage", "car"), ("frigate.zone_driveway", "car"),
                          ("frigate.camera_garage", "person"), ("frigate.zone_driveway", "person")])
        self.assertEqual(events[0][2]["clip"], "frigate:r:r1")
        alerts = self.store.events(kind="alert")
        self.assertEqual(len(alerts), 1)  # one Activity entry per alert, not per update/object
        self.assertEqual(alerts[0]["data"]["snapshotId"], "snap-1")

    async def test_severity_choice(self):
        events = []
        self.hub.subscribe_events(lambda ent, e, data: _collect(events, ent.id, e, data))
        self.fr.config["severity"] = "detections"
        await self.fr._on_review(self.review("new", severity="alert", rid="a1"))
        await self.fr._on_review(self.review("new", severity="detection", rid="d1"))
        self.fr.config["severity"] = "both"
        await self.fr._on_review(self.review("new", severity="alert", rid="a2", objects=("person",)))
        await asyncio.sleep(0.05)
        self.assertEqual([d["clip"] for _, _, d in events][::1][:1], ["frigate:r:d1"])
        self.assertIn("frigate:r:a2", [d["clip"] for _, _, d in events])
        self.assertNotIn("frigate:r:a1", [d["clip"] for _, _, d in events])

    async def test_detections_are_ignored(self):
        events = []
        self.hub.subscribe_events(lambda ent, e, data: _collect(events, ent.id, e, data))
        await self.fr._on_review(self.review("new", severity="detection", rid="r2"))
        await asyncio.sleep(0.05)
        self.assertEqual(events, [])
        self.assertEqual(self.store.events(kind="alert"), [])

    async def test_only_if_nobody_in_zone(self):
        auto = {"name": "Garage person, backyard empty",
                "trigger": {"type": "event", "entity": "frigate.camera_garage", "event": "person"},
                "condition": {"mode": "none", "entities": ["frigate.zone_driveway"]},
                "notify": {"enabled": True}, "recipients": ["p_chris"]}
        self.assertEqual(self.engine.describe(auto)["summary"][:74],
                         "When a person is detected on Garage, if Driveway isn't occupied, notify Ch")
        self.engine.save(auto)
        await self.fr._on_message("frigate", "frigate/driveway/car", b"1")  # driveway occupied
        await self.hub.emit("frigate.camera_garage", "person", {})
        await asyncio.sleep(0.05)
        self.assertEqual(self.outbox(), [])
        self.assertEqual(self.store.runs()[0]["status"], "skipped")
        await self.fr._on_message("frigate", "frigate/driveway/car", b"0")  # now clear
        await self.hub.emit("frigate.camera_garage", "person", {})
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.outbox()), 1)

    async def test_describe_and_notification(self):
        auto = {"name": "Car arrives", "trigger": {"type": "event", "entity": "frigate.zone_driveway", "event": "car"},
                "notify": {"enabled": True}, "recipients": ["p_chris"]}
        self.assertTrue(self.engine.describe(auto)["summary"].startswith("When a car enters Driveway"))
        self.engine.save(auto)
        await self.hub.emit("frigate.zone_driveway", "car", {"snapshot_id": "snap-1", "frigate_event_id": "e9", "camera": "garage"})
        await asyncio.sleep(0.05)
        note = self.outbox()[0]
        self.assertEqual(note["title"], "Car in Driveway")
        self.assertEqual((note["frigate_event_id"], note["camera"]), ("e9", "garage"))


async def _collect(events, eid, e, data):
    events.append((eid, e, data))


async def _noop():
    return None


class ReolinkTests(Base):
    async def test_doorbell_press_and_detections_are_separate_triggers(self):
        from newman.integrations.reolink import ReolinkIntegration

        class FakeHost:
            visitor = motion = person = False
            def visitor_detected(self, ch): return self.visitor
            def motion_detected(self, ch): return self.motion
            def ai_supported(self, ch, t): return t == "people"
            def ai_detected(self, ch, t): return self.person

        from newman.integrations.reolink import ReolinkDevice
        r = ReolinkIntegration("reolink", "Reolink", {"devices": [{"host": "x", "use": "everything"}], "password": "p"},
                               self.integrations._ctx)
        d = ReolinkDevice(r, r.config["devices"][0], first=True)
        d.host = FakeHost()
        self.hub.upsert("reolink.doorbell", kind="doorbell", name="Front Door", integration="reolink")
        d.chans = {0: {"eid": "reolink.doorbell", "doorbell": True, "flags": {}, "last": {}}}

        async def no_snap(ch=None):
            return None
        d.snapshot = no_snap
        seen = []
        self.hub.subscribe_events(lambda ent, e, data: _collect(seen, ent.id, e, data))
        d.host.person = True
        d.on_event()
        d.host.visitor = True
        d.on_event()
        d.on_event()  # still pressed: no repeat
        await asyncio.sleep(0.05)
        self.assertEqual(sorted(e for _, e, _ in seen), ["person", "pressed"])
        out = self.engine.describe({"trigger": {"type": "event", "entity": "reolink.doorbell", "event": "person"},
                                    "notify": {"enabled": True}, "recipients": ["p_chris"]})
        self.assertTrue(out["summary"].startswith("When a person is detected on Front Door"))
        # "Button presses & controls only": detections switched off
        d.spec["use"] = "buttons"
        d.chans[0]["flags"] = {}
        seen.clear()
        d.host.visitor = False
        d.on_event()
        await asyncio.sleep(0.05)
        self.assertEqual(seen, [])

    def test_single_address_setup_moves_into_device_list(self):
        from newman.integrations.reolink import ReolinkIntegration
        old = {"host": "10.1.1.56", "name": "Front Door", "use": "buttons", "username": "admin", "password": "p"}
        new = ReolinkIntegration.migrate_config(old)
        self.assertEqual(new["devices"], [{"host": "10.1.1.56", "name": "Front Door", "use": "buttons"}])
        self.assertNotIn("host", new)
        self.assertEqual(ReolinkIntegration.migrate_config(new), new)
        self.assertEqual(ReolinkIntegration.validate({"username": "a", "password": "p", "devices": []}),
                         ["Add at least one Reolink device by address"])


class TokenTests(unittest.TestCase):
    def test_tokens_are_bound(self):
        exp, tok = tokens.sign("action", "r1", "phone1", 60)
        self.assertTrue(tokens.verify("action", "r1", "phone1", exp, tok))
        self.assertFalse(tokens.verify("snapshot", "r1", "phone1", exp, tok))
        self.assertFalse(tokens.verify("action", "r1", "phone2", exp, tok))
        self.assertFalse(tokens.verify("action", "r1", "phone1", exp - 3600, tok))


if __name__ == "__main__":
    unittest.main()
