"""Teaching and training "Hey Newman": clip checks and storage, the recordings list and deleting,
and the trainer hand-off (request / status / candidates / use / undo) against fake trainer files."""
import json
import math
import os
import struct
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.voice import teach as T  # noqa: E402
from test_auth import Browser, H  # noqa: E402


def tone(seconds, amp=4000, quiet_before=0.2, quiet_after=0.2, hz=220):
    """PCM16 16 kHz: quiet, a voice-like tone, quiet."""
    out = []
    for i in range(int(quiet_before * 16000)):
        out.append(20 if i % 2 else -20)
    for i in range(int(seconds * 16000)):
        out.append(int(amp * math.sin(2 * math.pi * hz * i / 16000)))
    for i in range(int(quiet_after * 16000)):
        out.append(20 if i % 2 else -20)
    return struct.pack(f"<{len(out)}h", *out)


class ClipCheckTests(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(T.check_clip(tone(0.6), "positive"), (1000, None))
        ms, why = T.check_clip(tone(0.1, quiet_before=0, quiet_after=0.1), "positive")
        self.assertIn("too short", why)
        self.assertIn("too long", T.check_clip(tone(6.5), "positive")[1])
        self.assertIn("too quiet", T.check_clip(tone(0.8, amp=100), "positive")[1])
        self.assertIn("16 kHz", T.check_clip(tone(0.8), "positive", rate=22050)[1])
        # Talking: at least 5 s, up to 35 s, with a voice in it.
        self.assertIn("too short", T.check_clip(tone(3), "negative")[1])
        self.assertIsNone(T.check_clip(tone(8), "negative")[1])
        self.assertIn("too long", T.check_clip(tone(36), "negative")[1])
        # A click in silence isn't a voice.
        click = tone(0.04, amp=9000, quiet_before=1.0, quiet_after=1.0)
        self.assertIn("voice", T.check_clip(click, "positive")[1])

    def test_slugs(self):
        self.assertEqual(T.slugify("Chris G."), "chris-g")
        self.assertEqual(T.slugify("  "), "someone")
        self.assertEqual(T.slugify("../../etc"), "etc")


class TeachApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.vdir = data / "voice"
        self.h = H(data)

        async def fetch(url):
            return b"model" + b"\0" * 2048
        self.h.voice_fetch = fetch
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        r = await self.client.post("/api/users", json={"username": "hall", "name": "Hallway tablet", "role": "tablet",
                                                       "password": "wall tablet pass"})
        self.tablet = await r.json()
        await self.client.post("/api/users", json={"username": "dana", "name": "Dana", "role": "family",
                                                   "password": "family pass 1"})
        self.tab = Browser(self.client)
        r = await self.tab.post("/api/auth/login", json={"username": "hall", "password": "wall tablet pass", "kind": "tablet"})
        self.auth = {"Authorization": "Bearer " + (await r.json())["token"]}
        self.fam = Browser(self.client)
        r = await self.fam.post("/api/auth/login", json={"username": "dana", "password": "family pass 1", "kind": "app"})
        self.fauth = {"Authorization": "Bearer " + (await r.json())["token"]}

    async def asyncTearDown(self):
        await self.tab.close()
        await self.fam.close()
        await self.client.close()
        self.tmp.cleanup()

    async def post_clip(self, who, person, kind="positive", pcm=None, headers=None):
        return await who.post("/api/voice/teach/clips", params={"person": person, "kind": kind}, data=pcm or tone(0.7),
                              headers={**(headers or {}), "Content-Type": "audio/L16; rate=16000"})

    async def test_teaching_on_a_tablet(self):
        uid = self.tablet["id"]
        # Not before an admin starts it (and only an admin can).
        r = await self.post_clip(self.tab, "Chris", headers=self.auth)
        self.assertEqual(r.status, 403)
        r = await self.tab.post("/api/voice/teach/start", json={"tablet": uid, "person": "Chris"}, headers=self.auth)
        self.assertEqual(r.status, 403)
        r = await self.client.post("/api/voice/teach/start", json={"tablet": "nope", "person": "Chris"})
        self.assertEqual(r.status, 400)
        r = await self.client.post("/api/voice/teach/start", json={"tablet": uid, "person": "Chris"})
        self.assertEqual((await r.json())["slug"], "chris")

        r = await self.post_clip(self.tab, "Chris", headers=self.auth)
        self.assertEqual(r.status, 200, await r.text())
        first = (await r.json())["clip"]
        self.assertEqual((first["kind"], first["person"], first["ms"]), ("positive", "chris", 1100))
        self.assertEqual((await (await self.post_clip(self.tab, "Chris", headers=self.auth)).json())["positive"], 2)
        # Someone else's name isn't this teaching session; a family member can't add clips.
        self.assertEqual((await self.post_clip(self.tab, "Dana", headers=self.auth)).status, 403)
        self.assertEqual((await self.post_clip(self.fam, "Dana", headers=self.fauth)).status, 403)
        # Silence, and a too-short clip, are turned away with a reason.
        r = await self.post_clip(self.tab, "Chris", pcm=b"\0\0" * 16000, headers=self.auth)
        self.assertEqual(r.status, 422)
        self.assertIn("quiet", (await r.json())["error"])
        # The talking step.
        r = await self.post_clip(self.tab, "Chris", kind="negative", pcm=tone(10), headers=self.auth)
        self.assertEqual(r.status, 200)

        f = self.vdir / "teach" / "chris" / "positive" / f"{first['id']}.wav"
        self.assertTrue(f.is_file())
        self.assertEqual(f.read_bytes()[:4], b"RIFF")
        self.assertEqual(json.loads((self.vdir / "teach" / "chris" / "person.json").read_text()), {"name": "Chris"})

        # "Redo last" from the tablet removes its own clip.
        r = await self.tab.delete(f"/api/voice/teach/chris/positive/{first['id']}", headers=self.auth)
        self.assertEqual(r.status, 200)
        self.assertFalse(f.exists())

        lst = await (await self.client.get("/api/voice/teach")).json()
        self.assertEqual(lst["totals"]["positive"], 1)
        self.assertEqual(lst["totals"]["negative"], 1)
        p = lst["people"][0]
        self.assertEqual((p["name"], p["positive"], p["negative"]), ("Chris", 1, 1))
        self.assertEqual(lst["teaching"], [{"tablet": uid, "person": "Chris"}])
        self.assertEqual((await self.tab.get("/api/voice/teach", headers=self.auth)).status, 403)
        clip = p["clips"][0]
        r = await self.client.get(f"/api/voice/teach/chris/{clip['kind']}/{clip['id']}.wav")
        self.assertEqual((r.status, (await r.read())[:4]), (200, b"RIFF"))

        # The tablet ends it; then it can't add any more.
        await self.tab.post("/api/voice/teach/stop", headers=self.auth)
        self.assertEqual((await self.post_clip(self.tab, "Chris", headers=self.auth)).status, 403)
        # An admin can add clips directly, and delete someone's recordings.
        self.assertEqual((await self.post_clip(self.client, "Someone Else")).status, 200)
        self.assertEqual((await self.client.delete("/api/voice/teach/nobody")).status, 404)
        self.assertEqual((await self.client.delete("/api/voice/teach/chris/positive/..%2F..%2Fx")).status, 404)
        self.assertEqual((await self.client.delete("/api/voice/teach/chris")).status, 200)
        lst = await (await self.client.get("/api/voice/teach")).json()
        self.assertEqual([p["name"] for p in lst["people"]], ["Someone Else"])

    async def test_training_hand_off(self):
        tdir = self.vdir / "train"
        r = await self.client.get("/api/voice/train")
        st = await r.json()
        self.assertEqual((st["status"]["state"], st["status"]["trainerOnline"], st["candidates"]), ("idle", False, []))
        self.assertIsNone(st["current"])
        self.assertEqual((await self.tab.get("/api/voice/train", headers=self.auth)).status, 403)

        # Too few recordings.
        r = await self.client.post("/api/voice/train", json={})
        self.assertEqual(r.status, 400)
        self.assertIn("at least 20", (await r.json())["error"])
        for _ in range(20):
            await self.post_clip(self.client, "Chris")
        r = await self.client.post("/api/voice/train", json={"steps": 2000})
        self.assertEqual(r.status, 200, await r.text())
        reqs = list((tdir / "requests").glob("*.json"))
        self.assertEqual(len(reqs), 1)
        self.assertEqual(json.loads(reqs[0].read_text())["steps"], 2000)
        self.assertTrue((await r.json())["pending"])
        self.assertEqual((await self.client.post("/api/voice/train", json={})).status, 409)  # one at a time

        # The trainer picks it up and reports progress.
        reqs[0].unlink()
        (tdir / "status.json").write_text(json.dumps({"state": "running", "stage": "Training", "percent": 40,
                                                      "updatedAt": time.time(), "startedAt": time.time() - 60}))
        st = await (await self.client.get("/api/voice/train")).json()
        self.assertEqual((st["status"]["state"], st["status"]["percent"], st["status"]["trainerOnline"]), ("running", 40, True))
        self.assertEqual((await self.client.post("/api/voice/train", json={})).status, 409)

        # It finishes: a candidate with its numbers. The model in use is the first one (built-in numbers).
        self.vdir.mkdir(exist_ok=True)
        for n in ("melspectrogram", "embedding_model"):
            (self.vdir / f"{n}.onnx").write_bytes(n.encode())
        (self.vdir / "hey_newman.onnx").write_bytes(b"first model")
        (tdir / "candidates").mkdir(parents=True)
        name = "hey_newman-20261003-201500"
        (tdir / "candidates" / f"{name}.onnx").write_bytes(b"new model")
        (tdir / "candidates" / f"{name}.metrics.json").write_text(json.dumps(
            {"threshold": 0.45, "realRecall": 0.9, "falseWakesPerHour": 0.6, "syntheticRecall": 0.5,
             "current": {"realRecall": 0.3, "falseWakesPerHour": 2.5}}))
        (tdir / "status.json").write_text(json.dumps({"state": "done", "percent": 100, "candidate": name,
                                                      "updatedAt": time.time()}))
        st = await (await self.client.get("/api/voice/train")).json()
        self.assertEqual(st["current"]["syntheticRecall"], 0.43)
        self.assertEqual(st["current"]["falseWakesPerHour"], 2.5)
        self.assertEqual([c["name"] for c in st["candidates"]], [name])
        self.assertEqual(st["candidates"][0]["metrics"]["realRecall"], 0.9)

        before = await (await self.tab.get("/api/voice/wakeword", headers=self.auth)).json()
        self.assertEqual((await self.client.post("/api/voice/train/use/hey_newman-nope")).status, 404)
        r = await self.client.post(f"/api/voice/train/use/{name}")
        st = await r.json()
        self.assertEqual(r.status, 200)
        self.assertEqual((self.vdir / "hey_newman.onnx").read_bytes(), b"new model")
        self.assertEqual((self.vdir / "hey_newman.previous.onnx").read_bytes(), b"first model")
        self.assertEqual(json.loads((self.vdir / "hey_newman.json").read_text())["threshold"], 0.45)
        self.assertEqual((st["current"]["realRecall"], st["current"]["name"]), (0.9, name))
        self.assertEqual(st["previous"]["syntheticRecall"], 0.43)
        self.assertEqual(st["candidates"], [])
        after = await (await self.tab.get("/api/voice/wakeword", headers=self.auth)).json()
        self.assertNotEqual(before["version"], after["version"])  # the tablets download it again
        self.assertEqual(after["threshold"], 0.45)
        # The previous model isn't offered as a wake word of its own.
        words = await (await self.client.get("/api/voice/wakewords")).json()
        self.assertNotIn("hey_newman.previous", [w["name"] for w in words["wakeWords"]])

        # Undo puts the first one back (and Undo again the new one).
        st = await (await self.client.post("/api/voice/train/undo")).json()
        self.assertEqual((self.vdir / "hey_newman.onnx").read_bytes(), b"first model")
        self.assertEqual(st["current"]["syntheticRecall"], 0.43)
        self.assertEqual(st["previous"]["realRecall"], 0.9)
        undone = await (await self.tab.get("/api/voice/wakeword", headers=self.auth)).json()
        self.assertEqual(undone["threshold"], 0.5)
        await self.client.post("/api/voice/train/undo")
        self.assertEqual((self.vdir / "hey_newman.onnx").read_bytes(), b"new model")

        # Keep current: a candidate is dropped.
        other = "hey_newman-20261004-090000"
        (tdir / "candidates" / f"{other}.onnx").write_bytes(b"x")
        self.assertEqual((await self.client.delete(f"/api/voice/train/candidates/{other}")).status, 200)
        self.assertFalse((tdir / "candidates" / f"{other}.onnx").exists())
        # A trainer that stopped reporting is shown as not running.
        (tdir / "status.json").write_text(json.dumps({"state": "idle", "updatedAt": time.time() - 600}))
        st = await (await self.client.get("/api/voice/train")).json()
        self.assertFalse(st["status"]["trainerOnline"])


if __name__ == "__main__":
    unittest.main()
