"""Virtual devices for trying automations out without touching real ones.
Commands take effect after a short delay (like a real lock motor), and the
test API can change their state "by hand" or press a virtual doorbell."""
from __future__ import annotations

import asyncio
from typing import Any

from ..store import slug
from .base import CommandError, Field, Integration

DELAY_S = 1.5


class VirtualIntegration(Integration):
    type = "virtual"
    name = "Virtual test devices"
    category = "other"
    provides = ("lock", "contact")  # kinds of device it can bring in (shown on its catalog card)
    description = "Pretend locks, doors and a doorbell for testing automations safely."
    fields = [
        Field("locks", "Locks (one per line)", "textarea", default="Test Lock A\nTest Lock B"),
        Field("contacts", "Door sensors (one per line)", "textarea", default=""),
        Field("doorbell", "Doorbell name", default="Test Doorbell", help="Leave empty for no doorbell."),
    ]

    STARTING = {"lock": "LOCKED", "contact": "closed", "doorbell": "idle"}
    CONFIRMS = {"lock": "LOCKED", "unlock": "UNLOCKED"}

    async def run(self) -> None:
        count = 0
        for key, kind in (("locks", "lock"), ("contacts", "contact")):
            for name in [n.strip() for n in str(self.config.get(key) or "").splitlines() if n.strip()]:
                await self._add(f"{kind}_{slug(name)}", kind, name)
                count += 1
        if bell := str(self.config.get("doorbell") or "").strip():
            await self._add("doorbell", "doorbell", bell)
            count += 1
        self.set_status("connected", f"{count} virtual device{'s' if count != 1 else ''}")
        await asyncio.Event().wait()

    async def _add(self, local: str, kind: str, name: str) -> None:
        eid = self.entity_id(local)
        ent = self.hub.upsert(eid, kind=kind, name=name, integration=self.id)
        if ent.state is None:
            await self.hub.update(eid, state=self.STARTING[kind], available=True)

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        if command not in self.CONFIRMS:
            raise CommandError(f"Virtual devices can't {command}")

        async def later() -> None:
            await asyncio.sleep(DELAY_S)
            await self.hub.update(entity_id, state=self.CONFIRMS[command])
        asyncio.create_task(later())

    async def set_state(self, entity_id: str, state: str) -> None:
        await self.hub.update(entity_id, state=state)

    async def press(self, entity_id: str, event: str) -> dict[str, Any]:
        await self.hub.emit(entity_id, event, {})
        return {"ok": True}
