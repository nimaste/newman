"""The Sun: one device saying whether the sun is up, when it next rises and sets (plus dawn and
dusk - civil twilight), and where it is in the sky. Worked out on the hub from the home's spot (the
Home place, else the Weather connection's location) - nothing to sign in to, nothing to fill in.

Automations don't need this device for "at sunset" or "only when it's dark" (those read the same
maths directly); it's here for the dashboard, for "When the Sun sets" event triggers and for anyone
who wants the elevation or azimuth (blinds that follow the sun)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from .. import sun as sunmath
from .base import Integration

EVERY_S = 60


class SunIntegration(Integration):
    type = "sun"
    name = "Sun"
    category = "climate"
    provides = ("sun",)
    description = ("Sunrise, sunset, dawn and dusk for your home, worked out on the hub - no account, nothing to "
                   "fill in. Gives you a Sun device with sunrise / sunset events.")
    fields = []

    @property
    def eid(self) -> str:
        return self.entity_id("sun")

    def snapshot(self, now: datetime | None = None) -> dict[str, Any] | None:
        """The device's state and readings right now (None = the home's location isn't known)."""
        now = now or datetime.now(timezone.utc)
        pos = sunmath.position(now)
        if pos is None:
            return None
        nxt = {e: sunmath.next_event(e, now) for e in sunmath.EVENTS}
        fmt = lambda t: t.strftime("%Y-%m-%dT%H:%M") if t else None  # noqa: E731
        return {"state": "above_horizon" if pos[0] > -0.833 else "below_horizon",
                "attrs": {"elevation": pos[0], "azimuth": pos[1], "nextSunrise": fmt(nxt["sunrise"]),
                          "nextSunset": fmt(nxt["sunset"]), "nextDawn": fmt(nxt["dawn"]), "nextDusk": fmt(nxt["dusk"])},
                "next": nxt}

    async def tick(self, now: datetime | None = None) -> None:
        snap = self.snapshot(now)
        if snap is None:
            self.set_status("error", "Newman doesn't know where home is yet - set the Home place's location "
                                     "or add the Weather connection")
            await self.hub.update(self.eid, available=False)
            self._next = None
            return
        # An event fires once its time (as last predicted) has come.
        prev = getattr(self, "_next", None) or {}
        moment = now or datetime.now(timezone.utc)
        for e in sunmath.EVENTS:
            if prev.get(e) and prev[e] <= moment:
                await self.hub.emit(self.eid, e, {})
        self._next = snap["next"]
        await self.hub.update(self.eid, available=True, state=snap["state"], attrs=snap["attrs"])
        lat, lon = sunmath.location() or (0, 0)
        self.set_status("connected", f"{lat:.3f}, {lon:.3f} · sunrise {snap['attrs']['nextSunrise'][11:]}, "
                                     f"sunset {snap['attrs']['nextSunset'][11:]}"
                        if snap["attrs"]["nextSunrise"] and snap["attrs"]["nextSunset"] else f"{lat:.3f}, {lon:.3f}")

    async def run(self) -> None:
        self.hub.upsert(self.eid, kind="sun", name=self.display_name or "Sun", integration=self.id)
        while True:
            await self.tick()
            await asyncio.sleep(EVERY_S)
