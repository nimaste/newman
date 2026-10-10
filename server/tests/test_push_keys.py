"""Optional push key files: a missing or broken FCM service account / APNs key turns that push type
off with one warning (like leaving the variable unset) instead of crashing the hub at startup. Also the
config defaults: no connections come from the environment."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman import config as config_mod  # noqa: E402
from newman.config import Config, config  # noqa: E402
from newman.push.apns import ApnsSender, PushError  # noqa: E402
from newman.push.fcm import FcmSender  # noqa: E402

KEYS = ("fcm_service_account_path", "apns_key_path", "apns_key_id", "apns_team_id")


class PushKeyFiles(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {k: getattr(config, k) for k in KEYS}
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            object.__setattr__(config, k, v)
        self._tmp.cleanup()

    def set(self, **kw: str) -> None:
        for k, v in kw.items():
            object.__setattr__(config, k, v)

    # -- FCM -----------------------------------------------------------------------------

    def test_fcm_unset_is_off(self) -> None:
        self.set(fcm_service_account_path="")
        self.assertFalse(FcmSender().enabled)

    def test_fcm_missing_file_is_off_with_one_warning(self) -> None:
        path = str(self.dir / "firebase.json")
        self.set(fcm_service_account_path=path)
        with self.assertLogs("newman.push.fcm", "WARNING") as logs:
            sender = FcmSender()
        self.assertFalse(sender.enabled)
        self.assertEqual(logs.output, [f"WARNING:newman.push.fcm:FCM disabled - {path} not found"])
        with self.assertRaises(PushError):
            asyncio.run(sender.send("tok", {"type": "x"}))
        asyncio.run(sender.close())  # nothing to close, must not raise

    def test_fcm_broken_file_is_off(self) -> None:
        for body in ("not json", json.dumps({"client_email": "x"})):  # bad JSON, no project_id
            path = self.dir / "firebase.json"
            path.write_text(body)
            self.set(fcm_service_account_path=str(path))
            with self.assertLogs("newman.push.fcm", "WARNING") as logs:
                sender = FcmSender()
            self.assertFalse(sender.enabled)
            self.assertIn("FCM disabled - can't read", logs.output[0])

    def test_fcm_present_file_is_on(self) -> None:
        path = self.dir / "firebase.json"
        path.write_text(json.dumps({"project_id": "p", "client_email": "a@b", "private_key": "k",
                                    "token_uri": "https://oauth2.googleapis.com/token"}))
        self.set(fcm_service_account_path=str(path))
        sender = FcmSender()
        self.assertTrue(sender.enabled)
        asyncio.run(sender.close())

    # -- APNs ----------------------------------------------------------------------------

    def test_apns_missing_key_is_off_with_one_warning(self) -> None:
        path = str(self.dir / "apns.p8")
        self.set(apns_key_path=path, apns_key_id="ABC123DEFG", apns_team_id="TEAM123456")
        with self.assertLogs("newman.push.apns", "WARNING") as logs:
            sender = ApnsSender()
        self.assertFalse(sender.enabled)
        self.assertEqual(logs.output, [f"WARNING:newman.push.apns:APNs disabled - {path} not found"])
        with self.assertRaises(PushError):
            asyncio.run(sender.send("tok", {}, env="production"))
        asyncio.run(sender.close())

    def test_apns_unreadable_key_is_off(self) -> None:
        self.set(apns_key_path=str(self.dir), apns_key_id="ABC123DEFG", apns_team_id="TEAM123456")  # a directory
        with self.assertLogs("newman.push.apns", "WARNING") as logs:
            sender = ApnsSender()
        self.assertFalse(sender.enabled)
        self.assertIn("APNs disabled - can't read", logs.output[0])

    def test_apns_present_key_is_on(self) -> None:
        path = self.dir / "apns.p8"
        path.write_text("-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n")
        self.set(apns_key_path=str(path), apns_key_id="ABC123DEFG", apns_team_id="TEAM123456")
        sender = ApnsSender()
        self.assertTrue(sender.enabled)
        asyncio.run(sender.close())


class ConfigDefaults(unittest.TestCase):
    def test_no_connections_from_the_environment(self) -> None:
        """MQTT and Reolink are added in the dashboard (Connections), never seeded from MQTT_* / REOLINK_*."""
        from newman.integrations.manager import IntegrationManager
        with mock.patch.dict(os.environ, {"MQTT_HOST": "broker.lan", "REOLINK_HOST": "cam.lan",
                                          "REOLINK_PASSWORD": "x"}):
            fresh = Config()
        for name in vars(fresh):
            self.assertFalse(name.startswith(("mqtt_", "reolink_", "doorbell_")), name)
        self.assertFalse(hasattr(IntegrationManager, "seed_from_env"))

    def test_unused_settings_are_gone(self) -> None:
        self.assertFalse(hasattr(config, "apns_production"))
        self.assertFalse(hasattr(config, "mqtt_birth_topic"))

    def test_no_lan_defaults(self) -> None:
        """Nothing defaults to a specific home network's addresses."""
        with mock.patch.dict(os.environ, {}, clear=True):
            fresh = config_mod.Config()
        for name, value in vars(fresh).items():
            self.assertNotRegex(str(value), r"\b(10|192\.168|172\.(1[6-9]|2\d|3[01]))\.\d+\.\d+", name)


if __name__ == "__main__":
    unittest.main()
