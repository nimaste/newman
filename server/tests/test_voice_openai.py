"""Voice: text to speech over an OpenAI-compatible HTTP service (Kokoro-FastAPI), its voices, and the
fallback to Piper over Wyoming."""
import base64
import io
import os
import struct
import unittest
import wave

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestServer  # noqa: E402

import test_voice  # noqa: E402
from newman.config import config  # noqa: E402
from newman.voice.openai_tts import FallbackTTS, OpenAITTS, audio_from_wav, voice_label, wav_bytes  # noqa: E402

KOKORO_VOICES = ["af_heart", "af_bella", "am_adam", "bf_emma", "bm_george", "jf_alpha", "zf_xiaobei", "ef_dora"]


class FakeKokoro:
    """POST /v1/audio/speech -> a WAV (0.1 s of silence at 24 kHz); GET /v1/audio/voices."""

    def __init__(self):
        self.said, self.failing, self.voice_calls = [], False, 0
        app = web.Application()
        app.router.add_post("/v1/audio/speech", self.speech)
        app.router.add_get("/v1/audio/voices", self.voices)
        self.server = TestServer(app)

    async def speech(self, req):
        if self.failing:
            return web.Response(status=500, text="model crashed")
        body = await req.json()
        self.said.append(body)
        return web.Response(body=wav_bytes(b"\1\0" * 2400, 24000), content_type="audio/wav")

    async def voices(self, _):
        self.voice_calls += 1
        return web.json_response({"voices": KOKORO_VOICES})

    @property
    def url(self):
        return str(self.server.make_url("")).rstrip("/")


class UnitTests(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(voice_label("af_heart"), "Heart (American, woman)")
        self.assertEqual(voice_label("bm_george"), "George (British, man)")
        self.assertEqual(voice_label("am_santa_claus"), "Santa Claus (American, man)")
        self.assertIsNone(voice_label("jf_alpha"))
        self.assertIsNone(voice_label("en_US-lessac-medium"))

    def test_streamed_wav_without_sizes(self):
        good = wav_bytes(b"\2\0" * 100, 24000)
        a = audio_from_wav(good)
        self.assertEqual((a.rate, a.width, a.channels, len(a.pcm)), (24000, 2, 1, 200))
        # A streamed WAV: data chunk size 0 - the samples after it still count.
        i = good.find(b"data")
        streamed = good[:i + 4] + struct.pack("<I", 0) + good[i + 8:]
        self.assertEqual(len(audio_from_wav(streamed).pcm), 200)


class KokoroApiTests(unittest.IsolatedAsyncioTestCase):
    """The whole API with the HTTP voice and a fake Piper behind it."""

    async def asyncSetUp(self):
        self.kokoro = FakeKokoro()
        await self.kokoro.server.start_server()
        self.piper = test_voice.FakeTTS()
        self.voice_name = config.voice_name
        object.__setattr__(config, "voice_name", "af_heart")
        self.http = FallbackTTS(OpenAITTS(self.kokoro.url), self.piper)
        real = test_voice.FakeTTS
        test_voice.FakeTTS = lambda: self.http
        try:
            await test_voice.PipelineApiTests.asyncSetUp(self)
        finally:
            test_voice.FakeTTS = real

    async def asyncTearDown(self):
        await test_voice.PipelineApiTests.asyncTearDown(self)
        await self.kokoro.server.close()
        object.__setattr__(config, "voice_name", self.voice_name)

    say = test_voice.PipelineApiTests.say

    async def test_replies_are_spoken_by_kokoro(self):
        out = await self.say("Turn off the kitchen lights.", audio=False)
        self.assertEqual(out["reply"], "Okay, the Kitchen Lights are off.")
        wav = base64.b64decode(out["audio"]["data"])
        with wave.open(io.BytesIO(wav)) as w:
            self.assertEqual((w.getframerate(), w.getnframes()), (24000, 2400))
        self.assertEqual(self.kokoro.said[-1], {"model": "kokoro", "input": "Okay, the Kitchen Lights are off.",
                                                "voice": "af_heart", "response_format": "wav", "stream": False})
        self.assertEqual(self.piper.said, [])
        info = await (await self.tab.get("/api/voice", headers=self.auth)).json()
        self.assertTrue(info["speaks"])

    async def test_kokoros_english_voices_are_offered(self):
        v = await (await self.client.get("/api/voice/voices")).json()
        self.assertEqual(v["default"], "af_heart")
        self.assertEqual(v["voices"], [
            {"name": "af_bella", "label": "Bella (American, woman)"},
            {"name": "af_heart", "label": "Heart (American, woman)"},
            {"name": "am_adam", "label": "Adam (American, man)"},
            {"name": "bf_emma", "label": "Emma (British, woman)"},
            {"name": "bm_george", "label": "George (British, man)"}])
        await self.client.get("/api/voice/voices")
        self.assertEqual(self.kokoro.voice_calls, 1)  # cached (the startup listing)
        uid = self.tablet["id"]
        r = await self.client.patch(f"/api/voice/settings/{uid}", json={"voice": "en_US-ryan-medium"})
        self.assertEqual(r.status, 400)
        r = await self.client.patch(f"/api/voice/settings/{uid}", json={"voice": "jf_alpha"})
        self.assertEqual(r.status, 400)
        r = await self.client.patch(f"/api/voice/settings/{uid}", json={"voice": "bm_george"})
        self.assertEqual((await r.json())["voice"], "bm_george")
        await self.say("what's the temperature on the main floor", audio=False)
        self.assertEqual(self.kokoro.said[-1]["voice"], "bm_george")

    async def test_a_piper_voice_saved_before_the_switch_means_the_default(self):
        await self.client.get("/api/voice/voices")
        uid = self.tablet["id"]
        self.h.store.set_setting("voiceTablets", {uid: {"voice": "en_US-ryan-medium", "volume": 40}})
        s = await (await self.client.get(f"/api/voice/settings/{uid}")).json()
        self.assertEqual((s["voice"], s["volume"]), ("", 40))
        await self.say("turn off the kitchen lights", audio=False)
        self.assertEqual(self.kokoro.said[-1]["voice"], "af_heart")

    async def test_piper_speaks_when_kokoro_fails(self):
        self.kokoro.failing = True
        with self.assertLogs("newman.voice.openai_tts", "WARNING") as logs:
            out = await self.say("Turn off the kitchen lights.", audio=False)
            await self.say("Turn on the kitchen lights.", audio=False)
        self.assertEqual(len([m for m in logs.output if "using Piper" in m]), 1)  # said once
        self.assertEqual(base64.b64decode(out["audio"]["data"])[:4], b"RIFF")
        self.assertEqual(self.piper.said, [("Okay, the Kitchen Lights are off.", None),
                                           ("Okay, the Kitchen Lights are on.", None)])
        self.kokoro.failing = False
        await self.say("Turn off the kitchen lights.", audio=False)
        self.assertEqual(len(self.piper.said), 2)

    async def test_no_piper_means_text_only(self):
        self.http.fallback = None
        self.kokoro.failing = True
        with self.assertLogs("newman.voice.pipeline", "WARNING"):
            out = await self.say("Turn off the kitchen lights.", audio=False)
        self.assertEqual((out["reply"], out["audio"]), ("Okay, the Kitchen Lights are off.", None))


class UnreachableTests(unittest.IsolatedAsyncioTestCase):
    async def test_unreachable_service(self):
        tts = OpenAITTS("http://127.0.0.1:9", timeout=2)
        from newman.voice.wyoming import WyomingError
        with self.assertRaises(WyomingError):
            await tts.synthesize("hello")
        self.assertEqual(await tts.voices(), [])
        self.assertIsNone(tts.known_voices())
