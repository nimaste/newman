"""Sonos, offline: fake SoCo speakers, one grouped under the other."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.integrations.base import Context  # noqa: E402
from newman.integrations.sonos import SonosIntegration  # noqa: E402
from newman.kinds import KINDS, properties_for  # noqa: E402
from newman.state import Hub  # noqa: E402


class FakeSpeaker:
    def __init__(self, uid, name, volume):
        self.uid, self.player_name, self.volume, self.mute = uid, name, volume, False
        self.transport, self.calls, self.group = "PLAYING", [], None

    def get_current_transport_info(self):
        return {"current_transport_state": self.transport}

    def get_current_track_info(self):
        return {"title": "So What", "artist": "Miles Davis", "album": "Kind of Blue"}

    def get_speaker_info(self):
        return {"model_name": "Sonos One"}

    def play(self):
        self.calls.append("play")
        self.transport = "PLAYING"

    def pause(self):
        self.calls.append("pause")
        self.transport = "PAUSED_PLAYBACK"


class SonosTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_speakers_state_volume_and_group_commands(self):
        kitchen = FakeSpeaker("RINCON_000E58AAAA01400", "Kitchen", 25)
        patio = FakeSpeaker("RINCON_000E58BBBB01400", "Patio", 40)
        group = NS(coordinator=kitchen, members={kitchen, patio})
        kitchen.group = patio.group = group
        s = SonosIntegration("sonos", "Sonos", {"speakers": [{"host": "10.0.0.50"}, {"host": "10.0.0.51"}],
                                                 "poll_seconds": 0.02},
                             Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        s._soco = lambda host: {"10.0.0.50": kitchen, "10.0.0.51": patio}[host]
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.1)
        e = {x.name: x for x in self.hub.all()}
        k, p = e["Kitchen"], e["Patio"]
        self.assertEqual(k.id, "sonos.rincon_000e58aaaa01400")
        self.assertEqual((k.kind, k.state, k.attrs["volume"], k.attrs["track"], k.attrs["grouped"]),
                         ("speaker", "playing", 25, "So What", True))
        self.assertEqual({q["id"]: q["unit"] for q in properties_for(k)}["volume"], "%")
        self.assertEqual((s.status, s.detail), ("connected", "2 speakers · on your network"))
        await s.command(p.id, "pause")      # a grouped speaker pauses its group (via the coordinator)
        await s.command(p.id, "set_volume", 15)
        self.assertEqual((kitchen.calls, patio.volume, self.hub.get(p.id).state), (["pause"], 15, "paused"))
        self.assertEqual([c.id for c in KINDS["speaker"].commands], ["play", "pause", "set_volume"])
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
