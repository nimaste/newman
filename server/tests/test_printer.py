"""Printers, offline: levels, problems in plain words, "needs attention" once per new problem, discovery."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.integrations.base import Context  # noqa: E402
from newman.integrations.printer import PrinterIntegration, reasons_of, supply_name  # noqa: E402
from newman.kinds import properties_for  # noqa: E402
from newman.state import Hub  # noqa: E402


def brother(state="idle", reasons=None, yellow=50):
    markers = [NS(name="BK", level=60), NS(name="C", level=60), NS(name="M", level=8), NS(name="Y", level=yellow),
               NS(name="Waste Toner Box", level=-3)]
    return NS(info=NS(printer_info="Brother MFC-L3770CDW series", name="Brother", manufacturer="Brother",
                      model="MFC-L3770CDW series"),
              state=NS(printer_state=state, reasons=reasons, message=None), markers=markers)


class PrinterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        self.p = PrinterIntegration("printer", "Printer", {"host": "10.1.1.21"},
                                    Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        self.events = []

        async def on_event(ent, event, data):
            self.events.append((event, data.get("detail")))
        self.hub.subscribe_events(on_event)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def test_words(self):
        self.assertEqual([supply_name(n) for n in ("BK", "Y", "Black Toner", "Photo Black ink")],
                         ["black", "yellow", "black", "photo_black"])
        self.assertEqual(reasons_of(["media-empty-error", "toner-low-warning", "none"]), ["media-empty", "toner-low"])
        self.assertEqual(reasons_of("none"), [])

    async def test_levels_and_problems(self):
        await self.p.apply(brother())
        ent = self.hub.get("printer.printer")
        self.assertEqual((ent.kind, ent.name, ent.state), ("printer", "Brother MFC-L3770CDW series", "idle"))
        p = {x["id"]: x for x in properties_for(ent)}
        self.assertEqual((p["levels.black"]["label"], p["levels.black"]["value"], p["levels.black"]["unit"]),
                         ("Black level", 60, "%"))
        self.assertNotIn("levels.waste_toner_box", p)  # level unknown: not offered
        self.assertEqual(self.p.detail, "Ready · low: magenta")
        await self.p.apply(brother("stopped", ["media-empty-error", "toner-low-warning"]))
        await self.p.apply(brother("stopped", ["media-empty-error", "toner-low-warning"]))  # same problem: once
        await asyncio.sleep(0)
        self.assertEqual(self.events, [("attention", "out of paper")])  # "toner low" is a level thing, not an alarm
        self.assertEqual(self.hub.get("printer.printer").attrs["problem"], "out of paper, toner low")
        await self.p.apply(brother("idle", None))
        self.assertIsNone(self.hub.get("printer.printer").attrs["problem"])

    def test_discovery(self):
        offer = PrinterIntegration.from_discovery("Brother MFC-L3770CDW series._ipp._tcp.local.", "10.1.1.21",
                                                  {"ty": "Brother MFC-L3770CDW series", "rp": "ipp/print"})
        self.assertEqual(offer, {"title": "Brother MFC-L3770CDW series (10.1.1.21)",
                                 "config": {"host": "10.1.1.21", "path": "ipp/print"}})
