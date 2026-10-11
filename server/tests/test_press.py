"""Notification and alert buttons through the API: the reply comes at once (token, run and button
checked first), the device commands are sent after it, and a command that fails or stalls is told
to whoever pressed it. Every press is logged on arrival with its outcome; the log also goes to a
file in the data folder."""
import asyncio
import logging
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman import app as app_mod  # noqa: E402
from newman import tokens  # noqa: E402
from newman.api import build_app  # noqa: E402
from newman.automations import engine as engine_mod  # noqa: E402
from newman.config import config  # noqa: E402
from newman.integrations.base import CommandError, Integration  # noqa: E402
from newman.store import Phone  # noqa: E402
from test_auth import H  # noqa: E402


class Locks(Integration):
    """Locks that can be told to refuse (CommandError) or to never answer (a stalled connection)."""
    type = "fake"
    name = "Fake locks"

    def __init__(self, ctx):
        super().__init__("fake", "Fake locks", {}, ctx)
        self.calls, self.fail, self.hang = [], {}, set()

    async def command(self, eid, command, value=None):
        if eid in self.fail:
            raise CommandError(self.fail[eid])
        if eid in self.hang:
            await asyncio.Event().wait()
        self.calls.append((eid, command))


class PressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.locks = Locks(self.h.integrations._ctx)
        self.h.integrations.instances["fake"] = self.locks
        for eid, name in (("fake.side", "Side Door"), ("fake.back", "Back Door")):
            self.h.hub.upsert(eid, kind="lock", name=name, integration="fake")
            await self.h.hub.update(eid, state="UNLOCKED")
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        self.chris = self.h.store.users()[0]["person_id"]
        self.marlina = self.h.store.add_person("Marlina")["id"]
        for pid, did in ((self.chris, "test-chris"), (self.marlina, "test-marlina")):
            self.h.store.register_phone(Phone(device_id=did, token=did, platform="test", person_id=pid,
                                              friendly_name=f"{did} phone"))
        self.auto = self.h.engine.save({
            "name": "Nightly lock check", "recipients": [self.chris, self.marlina],
            "trigger": {"type": "time", "time": "21:00", "days": [0, 1, 2, 3, 4, 5, 6]},
            "condition": {"mode": "any", "entities": ["fake.side", "fake.back"]},
            "notify": {"enabled": True, "onlyPending": True,
                       "buttons": ["lock:fake.side", "lock:fake.back", "lock_all", "dismiss"]},
            "followUp": {"enabled": True}})
        self.run = await self.h.engine.trigger(self.auto, {"type": "time"})
        self.note = self.pushes("test-chris")[0]
        p = mock.patch.object(engine_mod, "PRESS_COMMAND_S", 0.3)
        p.start()
        self.addCleanup(p.stop)

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    def pushes(self, device, title=None):
        return [m for m in self.h.notifier.outbox if m["deviceId"] == device and (title is None or m["title"] == title)]

    async def until(self, cond, what, timeout=3.0):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return
            await asyncio.sleep(0.02)
        self.fail(f"timed out waiting for {what}")

    async def tap(self, button, note=None, **over):
        n = note or self.note
        body = {"deviceId": n["deviceId"], "subject": n["action_subject"], "exp": n["action_exp"],
                "token": n["action_token"], "button": button, **over}
        return await self.client.post("/api/public/actions", json=body)

    def run_log(self):
        run = self.h.engine.open_runs.get(self.run["id"]) or self.h.store.run(self.run["id"])
        return [line["text"] for line in run["log"]]

    async def test_lock_all_replies_at_once_then_locks_and_tells_the_presser_what_stalled(self):
        self.locks.hang.add("fake.back")  # Back Door's connection never answers
        t0 = time.time()
        r = await self.tap("lock_all")
        self.assertLess(time.time() - t0, 0.25)  # not waiting for the stalled command
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual(await r.json(), {"ok": True, "accepted": True, "commanding": ["fake.side", "fake.back"]})
        await self.until(lambda: ("fake.side", "lock") in self.locks.calls, "Side Door locked")
        await self.until(lambda: self.pushes("test-chris", "Couldn't lock Back Door"), "the failure push")
        push = self.pushes("test-chris", "Couldn't lock Back Door")[0]
        self.assertEqual(push["body"], "Back Door didn't lock: its connection didn't take the command within 0.3s")
        self.assertEqual(push.get("buttons"), None)  # plain text
        self.assertEqual(self.pushes("test-marlina", "Couldn't lock Back Door"), [])  # only the presser
        self.assertTrue(any("Back Door didn't lock" in t for t in self.run_log()), self.run_log())
        self.assertTrue(any("Chris: Lock Side Door" in t for t in self.run_log()), self.run_log())
        problems = self.h.store.events(limit=50, kind="problem")
        self.assertIn("Couldn't lock Back Door", [e["title"] for e in problems])
        # Side Door's success still ends in the usual follow-up once both are locked.
        await self.h.hub.update("fake.side", state="LOCKED")
        await self.h.hub.update("fake.back", state="LOCKED")
        await self.until(lambda: [m for m in self.pushes("test-marlina") if m["kind"] == "followup"], "the follow-up")

    async def test_a_refused_command_comes_back_with_the_connections_words(self):
        self.locks.fail["fake.side"] = "Ring didn't take the command - its live connection stalled."
        r = await self.tap("lock:fake.side")
        self.assertEqual((r.status, (await r.json())["accepted"]), (200, True))
        await self.until(lambda: self.pushes("test-chris", "Couldn't lock Side Door"), "the failure push")
        self.assertEqual(self.pushes("test-chris", "Couldn't lock Side Door")[0]["body"],
                         "Side Door didn't lock: Ring didn't take the command - its live connection stalled")

    async def test_bad_presses_are_refused_at_once_and_every_press_is_logged(self):
        with self.assertLogs("newman.api", level="INFO") as logs:
            r = await self.tap("lock_all", token="forged")
            self.assertEqual(r.status, 401)
            r = await self.tap("unlock:fake.side")
            self.assertEqual(r.status, 409)
            self.assertIn("isn't part of this notification", (await r.json())["error"])
            r = await self.tap("lock_all", subject="run_gone")
            self.assertEqual(r.status, 401)  # the token is for the real run
            r = await self.tap("dismiss")
            self.assertEqual((r.status, await r.json()), (200, {"ok": True}))
            await asyncio.sleep(0.1)
            self.assertEqual(self.locks.calls, [])  # nothing was commanded by the refused presses
            r = await self.tap("lock_all")  # the run is dismissed but its buttons still work
            self.assertEqual(r.status, 200)
        text = "\n".join(logs.output)
        rid = self.run["id"]
        self.assertIn(f"Button 'lock_all' on {rid} from test-chris (test-chris phone): received", text)
        self.assertIn(f"Button 'lock_all' on {rid} from test-chris (test-chris phone): refused (401 invalid or expired token)", text)
        self.assertIn(f"Button 'unlock:fake.side' on {rid} from test-chris (test-chris phone): refused (409 That button "
                      "isn't part of this notification)", text)
        self.assertIn(f"Button 'dismiss' on {rid} from test-chris (test-chris phone): done", text)
        self.assertIn(f"Button 'lock_all' on {rid} from test-chris (test-chris phone): accepted", text)
        await self.until(lambda: len(self.locks.calls) == 2, "the accepted press's locks")

    async def test_signed_in_press_replies_at_once_and_tells_that_person(self):
        self.locks.fail["fake.back"] = "Newman isn't connected to Ring right now - try again in a moment"
        with self.assertLogs("newman.api", level="INFO") as logs:
            r = await self.client.post(f"/api/runs/{self.run['id']}/press", json={"button": "lock:fake.back"})
            self.assertEqual(r.status, 200, await r.text())
            self.assertTrue((await r.json())["accepted"])
            r = await self.client.post(f"/api/runs/{self.run['id']}/press", json={"button": "open_sesame"})
            self.assertEqual(r.status, 409)
        text = "\n".join(logs.output)
        self.assertIn(f"Button 'lock:fake.back' on {self.run['id']} from Chris: accepted", text)
        self.assertIn(f"Button 'open_sesame' on {self.run['id']} from Chris: refused (409", text)
        await self.until(lambda: self.pushes("test-chris", "Couldn't lock Back Door"), "the failure push")
        self.assertEqual(self.pushes("test-chris", "Couldn't lock Back Door")[0]["body"],
                         "Back Door didn't lock: Newman isn't connected to Ring right now - try again in a moment")
        self.assertEqual(self.pushes("test-marlina", "Couldn't lock Back Door"), [])


    # -- press reports (POST /api/public/press-report) ----------------------------------------------

    def report(self, note=None, **over):
        """A failed press as the iPhone reports it: home address timed out, then the outside one."""
        n = note or self.note
        attempt = {
            "at": 1790000000.5, "url": "http://10.1.1.2:8093/api/public/actions", "appState": "background",
            "backgroundLeft": 29.4, "timeoutSeconds": 20, "ms": 20004, "status": None, "certChallenges": 0,
            "error": {"domain": "NSURLErrorDomain", "code": -1001, "description": "The request timed out.",
                      "underlying": "kCFErrorDomainCFNetwork -1001 timed out", "streamCode": -2102, "streamDomain": 4,
                      "failingHost": "10.1.1.2", "path": "satisfied (Path is satisfied), interface: en0[802.11]"},
            "metrics": {"transactions": 1, "dnsStart": None, "connectStart": 1, "connectEnd": None,
                        "remoteAddress": "10.1.1.2", "remotePort": 8093, "networkProtocol": "", "reusedConnection": False}}
        second = {**attempt, "url": "https://home.garito.ca/api/public/actions", "timeoutSeconds": 4, "ms": 4001,
                  "certChallenges": 1, "certAvailable": True,
                  "metrics": {"transactions": 1, "dnsStart": 0, "dnsEnd": 3, "connectStart": 3, "connectEnd": 9,
                              "secureStart": 4, "secureEnd": 9, "requestStart": 9, "requestEnd": 9,
                              "remoteAddress": "10.1.1.2", "remotePort": 443, "networkProtocol": "http/1.1",
                              "reusedConnection": False}}
        return {"id": "rep-1", "deviceId": n["deviceId"], "subject": n["action_subject"], "exp": n["action_exp"],
                "token": n["action_token"], "button": "lock:fake.side", "ok": False,
                "outcome": "failed: The Newman hub took too long to answer.",
                "summary": "failed after 2 of 3 tries", "appVersion": "1.4 (77)", "launchState": "background",
                "sinceLaunchSeconds": 0.8, "backgroundExpired": False,
                "actionURL": "https://home.garito.ca/api/public/actions", "at": 1790000000.4,
                "attempts": [attempt, second], **over}

    async def test_a_failed_press_report_is_logged_per_try_and_kept_for_admins(self):
        with self.assertLogs("newman.api", level="INFO") as logs:
            r = await self.client.post("/api/public/press-report", json=self.report())
            self.assertEqual((r.status, await r.json()), (200, {"ok": True}))
            r = await self.client.post("/api/public/press-report", json=self.report())  # the other address, too
            self.assertEqual((await r.json()).get("duplicate"), True)
        warnings = [o for o in logs.output if o.startswith("WARNING:newman.api:Press report")]
        self.assertEqual(len(warnings), 2, logs.output)  # one per try, the duplicate not again
        first, second = warnings
        self.assertIn("Press report from test-chris (test-chris phone): button 'lock:fake.side' on "
                      f"{self.run['id']} failed: The Newman hub took too long to answer. - try 1/2 "
                      "http://10.1.1.2:8093/api/public/actions at ", first)
        self.assertIn("(background, 29.4 s background left, 20 s allowed): 20004 ms, NSURLErrorDomain -1001 The request "
                      "timed out. [underlying kCFErrorDomainCFNetwork -1001 timed out; stream 4/-2102; host 10.1.1.2; "
                      "path satisfied (Path is satisfied), interface: en0[802.11]]; cert challenges 0; "
                      "dns - connect 1-? tls - request - response - | 10.1.1.2:8093 new "
                      "(app 1.4 (77), background when the button arrived, running 0.8 s)", first)
        self.assertIn("try 2/2 https://home.garito.ca/api/public/actions", second)
        self.assertIn("cert challenges 1 (certificate sent); dns 0-3 connect 3-9 tls 4-9 request 9-9 response - | "
                      "10.1.1.2:443 http/1.1 new", second)
        self.assertNotIn(self.note["action_token"], "\n".join(logs.output))
        r = await self.client.get("/api/diagnostics/press")  # the test client is signed in as the admin
        self.assertEqual(r.status, 200)
        kept = await r.json()
        self.assertEqual([k["id"] for k in kept], ["rep-1"])
        self.assertNotIn("token", kept[0])
        self.assertEqual(kept[0]["attempts"][0]["error"]["code"], -1001)
        self.assertEqual(kept[0]["phone"], "test-chris phone")

    async def test_a_press_that_worked_is_one_info_line(self):
        rep = self.report(id="rep-ok", ok=True, outcome="pressed", attempts=[],
                          summary="pressed at http://10.1.1.2:8093/api/public/actions on try 1 of 2 in 85 ms (background)")
        with self.assertLogs("newman.api", level="INFO") as logs:
            r = await self.client.post("/api/public/press-report", json=rep)
            self.assertEqual(r.status, 200)
        lines = [o for o in logs.output if "Press report" in o]
        self.assertEqual(len(lines), 1, logs.output)
        self.assertTrue(lines[0].startswith("INFO:newman.api:Press report from test-chris"), lines[0])
        self.assertIn(": pressed at http://10.1.1.2:8093/api/public/actions on try 1 of 2 in 85 ms (background) (app 1.4",
                      lines[0])

    async def test_press_reports_need_the_pushs_token_or_a_session(self):
        self.client.session.cookie_jar.clear()  # not signed in
        r = await self.client.post("/api/public/press-report", json=self.report(token="forged"))
        self.assertEqual(r.status, 401)
        r = await self.client.get("/api/diagnostics/press")
        self.assertEqual(r.status, 401)
        # A queued report may arrive after its push's token expired: accepted for a week.
        did, subject = self.note["deviceId"], self.note["action_subject"]
        old = int(time.time()) - 3 * 86400
        tok = tokens._mac("action", subject, did, old)
        r = await self.client.post("/api/public/press-report", json=self.report(id="late", exp=str(old), token=tok))
        self.assertEqual(r.status, 200)
        older = int(time.time()) - 8 * 86400
        r = await self.client.post("/api/public/press-report",
                                   json=self.report(id="too-late", exp=str(older), token=tokens._mac("action", subject, did, older)))
        self.assertEqual(r.status, 401)
        # The press itself still refuses an expired token.
        r = await self.tap("lock_all", exp=str(old), token=tok)
        self.assertEqual(r.status, 401)


class LogFileTests(unittest.TestCase):
    def test_the_log_also_goes_to_a_rotating_file_in_the_data_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = logging.getLogger()
            before, level = list(root.handlers), root.level
            try:
                path = app_mod.setup_logging(Path(tmp))
                self.assertEqual(path, Path(tmp) / "logs" / "newman.log")
                self.assertEqual(app_mod.setup_logging(Path(tmp)), path)  # not added twice
                added = [hd for hd in root.handlers if hd not in before
                         and isinstance(hd, app_mod.RotatingFileHandler)]
                self.assertEqual(len(added), 1)
                self.assertEqual((added[0].maxBytes, added[0].backupCount), (5 * 1024 * 1024, 5))
                logging.getLogger("newman.api").info("Button 'lock_all' on run_1 from phone: received")
                added[0].flush()
                self.assertIn("INFO    newman.api: Button 'lock_all' on run_1 from phone: received", path.read_text())
            finally:
                for hd in root.handlers[:]:
                    if hd not in before:
                        root.removeHandler(hd)
                        hd.close()
                root.setLevel(level)


if __name__ == "__main__":
    unittest.main()
