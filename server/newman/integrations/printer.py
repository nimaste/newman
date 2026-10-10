"""Network printers over IPP - the standard AirPrint and Mopria printers speak (Brother, HP, Canon,
Epson…). Read-only: ready / printing / stopped, toner and ink left, and what it needs (paper, a jam,
a cover open). No account, no driver; Newman finds printers on the network by itself.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..kinds import KINDS, humanize
from .base import Field, Integration

log = logging.getLogger(__name__)

# Short supply names printers use -> words.
SUPPLY_NAMES = {"bk": "black", "k": "black", "c": "cyan", "m": "magenta", "y": "yellow",
                "lc": "light cyan", "lm": "light magenta", "pbk": "photo black", "mbk": "matte black"}
# printer-state-reasons (without -error/-warning/-report) -> plain words.
REASONS = {"media-empty": "out of paper", "media-needed": "needs paper", "media-jam": "paper jam",
           "door-open": "a door is open", "cover-open": "a cover is open", "toner-low": "toner low",
           "toner-empty": "toner empty", "marker-supply-low": "ink or toner low",
           "marker-supply-empty": "ink or toner empty", "marker-waste-almost-full": "waste toner box almost full",
           "marker-waste-full": "waste toner box full", "input-tray-missing": "a paper tray is missing",
           "output-area-full": "output tray full", "output-area-almost-full": "output tray almost full",
           "paused": "paused", "offline": "offline", "spool-area-full": "print queue full",
           "developer-low": "drum low", "opc-near-eol": "drum near end of life", "opc-life-over": "drum needs replacing"}
# Reasons that need someone now (the "needs attention" event); "low" and "almost" ones are for levels/automations.
URGENT = {"media-empty", "media-needed", "media-jam", "door-open", "cover-open", "toner-empty", "marker-supply-empty",
          "marker-waste-full", "input-tray-missing", "output-area-full", "opc-life-over"}


def supply_name(name: str) -> str:
    n = (name or "").strip()
    words = SUPPLY_NAMES.get(n.lower()) or n.lower().replace(" toner", "").replace(" ink", "").replace(" cartridge", "")
    return words.strip().replace(" ", "_") or "supply"


def reasons_of(raw: Any) -> list[str]:
    """printer-state-reasons (a string or a list) -> base reasons, e.g. ["media-empty"]."""
    items = raw if isinstance(raw, list) else str(raw or "").split(",")
    out = []
    for r in items:
        r = str(r).strip().lower()
        for suffix in ("-error", "-warning", "-report"):
            r = r.removesuffix(suffix)
        if r and r != "none" and r not in out:
            out.append(r)
    return out


def reason_text(r: str) -> str:
    return REASONS.get(r) or humanize(r.replace("-", " ")).lower()


class PrinterIntegration(Integration):
    type = "printer"
    name = "Printer"
    category = "other"
    provides = ("printer",)
    description = ("A network printer (anything that does AirPrint or Mopria - Brother, HP, Canon, Epson…): ready or "
                   "printing, toner and ink left, and alerts when it's out of paper, jammed or needs toner. Found on "
                   "your network automatically.")
    fields = [
        Field("host", "Printer address", required=True, placeholder="192.168.1.30"),
        Field("path", "IPP path", default="ipp/print", advanced=True,
              help="Almost always ipp/print. Some printers use ipp, printers/<name> (CUPS) or ipp/printer."),
        Field("port", "Port", "number", default=631, advanced=True),
        Field("tls", "Use IPPS (encrypted)", "boolean", default=False, advanced=True),
    ]
    zeroconf = ["_ipp._tcp.local."]

    @classmethod
    def from_discovery(cls, name: str, host: str, props: dict[str, str]) -> dict[str, Any] | None:
        title = props.get("ty") or name.split("._")[0]
        return {"title": f"{title} ({host})", "config": {"host": host, "path": props.get("rp") or "ipp/print"}}

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._ipp: Any = None
        self._eid = self.entity_id("printer")

    def _client(self) -> Any:
        from pyipp import IPP
        if self._ipp is None:
            self._ipp = IPP(host=str(self.config["host"]).strip(), port=int(self.config.get("port") or 631),
                            base_path="/" + str(self.config.get("path") or "ipp/print").strip("/"),
                            tls=bool(self.config.get("tls")), request_timeout=10)
        return self._ipp

    async def run(self) -> None:
        attempt = 0
        try:
            while True:
                try:
                    printer = await self._client().printer()
                    await self.apply(printer)
                    attempt = 0
                    ent = self.hub.get(self._eid)
                    await asyncio.sleep(15 if ent and ent.state == "printing" else 60)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.info("Printer %s: %s", self.config.get("host"), e)
                    self.set_status("error", f"Can't reach the printer at {self.config.get('host')}")
                    if self.hub.get(self._eid):
                        await self.hub.update(self._eid, available=False)
                    await self.backoff_sleep(min(attempt + 3, 6))
                    attempt += 1
        finally:
            if self._ipp is not None:
                await self._ipp.close()

    async def apply(self, printer: Any) -> None:
        info, state = printer.info, printer.state
        name = info.printer_info or info.name or "Printer"
        ent = self.hub.upsert(self._eid, kind="printer", name=name, integration=self.id)
        levels = {}
        for m in printer.markers or []:
            if m.level is not None and 0 <= m.level <= 100:  # -1/-2/-3 = unknown / unlimited
                levels[supply_name(m.name)] = m.level
        reasons = reasons_of(state.reasons)
        problems = [r for r in reasons if r not in ("none",)]
        prev = set(reasons_of(ent.attrs.get("reasons")))
        await self.hub.update(self._eid, available=True, state=state.printer_state or "idle",
                              attrs={"levels": levels, "problem": ", ".join(reason_text(r) for r in problems) or None,
                                     "message": state.message or None, "reasons": reasons,
                                     "manufacturer": info.manufacturer or "", "model": info.model or ""})
        for r in reasons:
            if r in URGENT and r not in prev and ent.state is not None:
                await self.hub.emit(self._eid, "attention", {"detail": reason_text(r), "reason": r})
        low = [k.replace("_", " ") for k, v in levels.items() if v <= 10]
        self.set_status("connected", KINDS["printer"].state_label(state.printer_state or "idle")
                        + (f" · low: {', '.join(low)}" if low else "") + (f" · {reason_text(problems[0])}" if problems else ""))
