"""Webhooks, offline: secret addresses that fire events, and sending to a (local test) server."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp import web  # noqa: E402

from newman.integrations.base import CommandError, Context, mask_config, merge_config  # noqa: E402
from newman.integrations.webhooks import WebhooksIntegration, parse_body  # noqa: E402
from newman.kinds import KINDS, meta_json, properties_for  # noqa: E402
from newman.state import Hub  # noqa: E402


class WebhookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        self.events = []

        async def on_event(ent, event, data):
            self.events.append((ent.id, event, data))
        self.hub.subscribe_events(on_event)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def start(self, hooks):
        w = WebhooksIntegration("webhooks", "Webhooks", {"hooks": hooks},
                                Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        self.saved = []
        w.save_config = self.saved.append
        self.task = asyncio.create_task(w.run())
        await asyncio.sleep(0.02)
        return w

    async def stop(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    def test_kind_and_body_parsing(self):
        k = KINDS["webhook"]
        self.assertEqual(([e.id for e in k.events], [c.id for c in k.commands]), (["received"], ["send"]))
        self.assertIn("webhook", [x["id"] for x in meta_json("t")["kinds"]])
        self.assertEqual(parse_body(b'{"text": "hi", "n": 2, "deep": {"x": 1}}', "application/json"),
                         ("hi", {"text": "hi", "n": 2}))
        self.assertEqual(parse_body(b"message=Gate+open&who=Sam", "application/x-www-form-urlencoded"),
                         ("Gate open", {"message": "Gate open", "who": "Sam"}))
        self.assertEqual(parse_body(b"plain words", "text/plain"), ("plain words", {}))

    async def test_incoming_secret_events_and_masking(self):
        w = await self.start([{"name": "Garden gate"}, {"name": "Alarm", "secret": "s3cret-value-123"}])
        gate, alarm = "webhooks.garden_gate", "webhooks.alarm"
        # A secret is made for the hook that had none, and saved.
        made = self.saved[-1]["hooks"][0]["secret"]
        self.assertGreaterEqual(len(made), 24)
        self.assertEqual(self.saved[-1]["hooks"][1]["secret"], "s3cret-value-123")
        self.assertEqual(w.status, "connected")
        # The status (seen by every account) names the hooks; the addresses are admin-only details.
        self.assertNotIn(made, w.detail)
        self.assertIn(f"/api/public/webhooks/{gate}/{made}", [a["path"] for a in w.admin_details()["addresses"]])
        masked = mask_config(WebhooksIntegration, w.config)
        self.assertEqual(masked["hooks"][0]["secret"], "••••")
        # Saving the masked form back keeps the real secret.
        back = merge_config(WebhooksIntegration, w.config, masked)
        self.assertEqual(back["hooks"][0]["secret"], made)
        self.assertEqual((self.hub.get(gate).kind, self.hub.get(gate).state), ("webhook", "idle"))
        # Wrong secret / unknown hook: nothing happens.
        self.assertFalse(await w.receive_webhook(gate, "guess", b"", ""))
        self.assertFalse(await w.receive_webhook("webhooks.nope", made, b"", ""))
        self.assertTrue(await w.receive_webhook(alarm, "s3cret-value-123", b'{"text":"Motion in garage"}',
                                                "application/json"))
        self.assertTrue(await w.receive_webhook(alarm, "s3cret-value-123", b'{"text":"again"}', "application/json"))
        await asyncio.sleep(0.01)
        self.assertEqual(self.events, [(alarm, "received", {"text": "Motion in garage"})])  # the quick repeat is dropped
        props = {p["id"]: p["value"] for p in properties_for(self.hub.get(alarm))}
        self.assertEqual(props["lastMessage"], "Motion in garage")
        with self.assertRaises(CommandError):
            await w.command(gate, "send", "hi")  # no Send to address
        await self.stop()

    async def test_outgoing_send_json_and_text(self):
        got = []

        async def handler(req):
            got.append((req.content_type, await req.read()))
            return web.Response(status=500 if req.path == "/fail" else 200)
        app = web.Application()
        app.router.add_post("/{p}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            w = await self.start([{"name": "Phone", "url": f"http://127.0.0.1:{port}/hook"},
                                  {"name": "Ntfy", "url": f"http://127.0.0.1:{port}/topic", "format": "text"},
                                  {"name": "Broken", "url": f"http://127.0.0.1:{port}/fail"}])
            await w.command("webhooks.phone", "send", "Door left open")
            await w.command("webhooks.ntfy", "send", "Plain hello")
            await w.command("webhooks.phone", "send")
            with self.assertRaises(CommandError):
                await w.command("webhooks.broken", "send", "x")
            self.assertEqual(got[0][0], "application/json")
            self.assertEqual(json.loads(got[0][1]), {"text": "Door left open", "from": "Newman", "device": "Phone"})
            self.assertEqual(got[1], ("text/plain", b"Plain hello"))
            self.assertEqual(json.loads(got[2][1])["text"], "")
            self.assertEqual(self.hub.get("webhooks.phone").attrs["lastSendStatus"], 200)
            await self.stop()
        finally:
            await runner.cleanup()
        self.assertEqual(WebhooksIntegration.validate({"hooks": [{"name": "x", "url": "ftp://nope"}]}),
                         ["x: the Send to address must start with http:// or https://"])
