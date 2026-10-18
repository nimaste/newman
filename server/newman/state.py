"""In-memory entity registry + change/event bus.

Integrations write entity state and emit device events (a doorbell press)
here; the automation engine and the API read it and subscribe.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable

from .kinds import KINDS

log = logging.getLogger(__name__)

# Lock states (ring-mqtt publishes these verbatim); kept as names for readability.
LOCKED, UNLOCKED, JAMMED = "LOCKED", "UNLOCKED", "JAMMED"


@dataclass(frozen=True)
class Entity:
    id: str
    kind: str
    name: str
    integration: str  # integration instance id that owns it
    # Last *known* state. Deliberately kept when the device goes unavailable,
    # so "unlocked -> offline -> locked" is still seen as a change (Home
    # Assistant's from/to triggers miss exactly that transition).
    state: str | None = None
    available: bool = True
    attrs: dict[str, Any] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)

    @property
    def category(self) -> str:
        return KINDS[self.kind].category

    @property
    def is_active(self) -> bool:
        return self.state is not None and self.state == KINDS[self.kind].active_state

    @property
    def offline(self) -> bool:
        """Unavailable, or reporting itself offline (as the screens show it: "Offline")."""
        return not self.available or self.state == "offline"

    @property
    def health_items(self) -> list[tuple[str, str]]:
        """Its problems as (id, words): the id stays the same while the words may change ("Battery 15%" ->
        "Battery 9%"), so a problem can be muted (muting.py)."""
        a, items = self.attrs, []
        if not self.available:
            items.append(("offline", "Offline"))
        level = a.get("batteryLevel")
        if a.get("batteryStatus") == "failed":
            items.append(("battery_failed", "Battery failed"))
        elif isinstance(level, (int, float)) and level <= 20 and a.get("charging") is not True:  # charging: in hand
            items.append(("battery_low", f"Battery {level:g}%"))
        elif a.get("batteryLow") is True:  # the device itself says it's low / needs replacing (Matter)
            items.append(("battery_low", f"Battery low (takes {a['batteryType']})" if a.get("batteryType")
                          else "Battery low"))
        if a.get("tamperStatus") not in (None, "ok"):
            items.append(("tamper", "Tamper reported"))
        if a.get("acStatus") == "error":
            items.append(("no_ac", "No AC power"))
        if a.get("commStatus") not in (None, "ok"):
            items.append(("not_responding", "Not responding to the hub"))
        if a.get("linkQuality") not in (None, "ok"):
            items.append(("weak_signal", "Weak signal"))
        if a.get("commandFailures"):
            n = a["commandFailures"]
            items.append(("ignored_commands", f"Ignored {n} remote command{'s' if n != 1 else ''}"))
        if self.state == JAMMED:
            items.append(("jammed", "Jammed"))
        if a.get("canComeBack") is False:  # a wall tablet that can't bring itself back with its screen on
            items.append(("overlay_permission",
                          "Allow 'Display over other apps' for Newman Wall so it can keep its screen on"))
        return items

    @property
    def health_issues(self) -> list[str]:
        """Every problem it has, in words (muted ones too - muting.py leaves those out where people see them)."""
        return [text for _, text in self.health_items]


StateListener = Callable[[Entity | None, Entity], Awaitable[None]]
EventListener = Callable[[Entity, str, dict[str, Any]], Awaitable[None]]
AddedListener = Callable[[Entity], Awaitable[None]]
RemovedListener = Callable[[list[str]], Awaitable[None]]

# A connection restarting (its settings saved) drops its devices and registers them again moments
# later: a device only counts as gone (and the screens are told) if it isn't back by then.
RESTART_GRACE_S = 90.0


class Hub:
    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}
        self._names: dict[str, str] = {}  # user renames
        self._state_listeners: list[StateListener] = []
        self._event_listeners: list[EventListener] = []
        self._change_listeners: list[StateListener] = []  # any change at all, incl. attributes
        self._added_listeners: list[AddedListener] = []
        self._removed_listeners: list[RemovedListener] = []
        self._dropped: dict[str, float] = {}  # removed during a restart -> when (not "new" if they come back)
        self._held: set[str] = set()  # kept through a reconnect, not registered again yet (see hold)
        self._tasks: set[asyncio.Task] = set()
        # Set by the API (video.py): the device whose video and pictures a device shows instead of its
        # own ("Video from"), or None.
        self.video_source: Callable[[str], str | None] = lambda _eid: None

    def has_picture(self, ent: Entity) -> bool:
        """Its kind has a camera, or it shows another device's video."""
        return KINDS[ent.kind].has_camera or self.video_source(ent.id) is not None

    # -- subscriptions -----------------------------------------------------------

    def subscribe(self, listener: StateListener) -> None:
        self._state_listeners.append(listener)

    def subscribe_events(self, listener: EventListener) -> None:
        self._event_listeners.append(listener)

    def subscribe_changes(self, listener: StateListener) -> None:
        """Called for every change, including a reported value (temperature, humidity…) alone."""
        self._change_listeners.append(listener)

    def subscribe_registry(self, added: AddedListener, removed: RemovedListener) -> None:
        """A device appears (registered for the first time) / devices are gone for good."""
        self._added_listeners.append(added)
        self._removed_listeners.append(removed)

    def _spawn(self, coro: Awaitable[None]) -> None:
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:  # no loop (a script or a sync test): nobody to tell
            if hasattr(coro, "close"):
                coro.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- reads -------------------------------------------------------------------

    def get(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    def all(self, kind: str | None = None) -> list[Entity]:
        items = (e for e in self._entities.values() if kind is None or e.kind == kind)
        return sorted(items, key=lambda e: (e.kind, e.name.lower()))

    def name(self, entity_id: str) -> str:
        e = self._entities.get(entity_id)
        return e.name if e else entity_id

    # -- writes ------------------------------------------------------------------

    def set_name_overrides(self, names: dict[str, str]) -> None:
        self._names = dict(names)
        for eid, n in names.items():
            if eid in self._entities:
                self._entities[eid] = replace(self._entities[eid], name=n)

    def upsert(self, entity_id: str, *, kind: str, name: str, integration: str) -> Entity:
        name = self._names.get(entity_id, name)
        existing = self._entities.get(entity_id)
        if existing:
            self._held.discard(entity_id)
            if existing.name != name:  # renamed at its source: the screens hear it like any change
                old, existing = existing, replace(existing, name=name, updated_at=time.time())
                self._entities[entity_id] = existing
                for listener in list(self._change_listeners):
                    self._spawn(_safe(listener, old, existing))
            return existing
        ent = Entity(id=entity_id, kind=kind, name=name, integration=integration)
        self._entities[entity_id] = ent
        log.info("Registered %s %r (%s)", kind, name, entity_id)
        # Back after a restart: its next update reaches the screens as usual. Truly new: tell them now.
        if self._dropped.pop(entity_id, None) is None:
            for listener in list(self._added_listeners):
                self._spawn(_safe(listener, ent))
        return ent

    def remove_integration(self, integration_id: str, grace: float | None = 0.0) -> list[str]:
        """Drops a connection's devices. `grace`: seconds to wait before telling the screens (those that
        are registered again by then were never gone - a restart); None: don't tell (shutting down)."""
        gone = [e.id for e in self._entities.values() if e.integration == integration_id]
        for eid in gone:
            del self._entities[eid]
        if gone and grace is not None:
            self._announce_removed(gone, grace)
        return gone

    def hold(self, integration_id: str) -> list[str]:
        """A connection reconnecting: its devices stay as they are (nothing is dropped or announced) while
        it registers them again. Returns their ids, for release()."""
        ids = [e.id for e in self._entities.values() if e.integration == integration_id]
        self._held.update(ids)
        return ids

    async def release(self, ids: list[str]) -> list[str]:
        """After a reconnect: those of `ids` the connection didn't register again are no longer provided.
        They're kept (automations refer to them) and shown unavailable, like any device a connection
        stops seeing. Returns their ids."""
        gone = [eid for eid in ids if eid in self._held]
        for eid in gone:
            self._held.discard(eid)
            await self.update(eid, available=False)
        return gone

    def remove(self, entity_id: str) -> bool:
        """One device gone for good (its connection no longer has it, or an admin removed it). If its
        connection reports it again later, it's registered as a new device."""
        if self._entities.pop(entity_id, None) is None:
            return False
        self._held.discard(entity_id)
        self._announce_removed([entity_id], 0.0)
        return True

    def _announce_removed(self, ids: list[str], grace: float) -> None:
        now = time.time()
        if grace > 0:
            for eid in ids:
                self._dropped[eid] = now
        for eid in [k for k, t in self._dropped.items() if now - t > max(grace, RESTART_GRACE_S) * 2]:
            self._dropped.pop(eid, None)  # stale marks

        async def later() -> None:
            if grace > 0:
                await asyncio.sleep(grace)
            still = [eid for eid in ids if eid not in self._entities]
            for eid in still:
                self._dropped.pop(eid, None)
            if still:
                log.info("Removed %d device(s): %s", len(still), ", ".join(still[:10]))
                for listener in list(self._removed_listeners):
                    await _safe(listener, still)
        self._spawn(later())

    async def update(self, entity_id: str, **changes: Any) -> Entity | None:
        old = self._entities.get(entity_id)
        if old is None:
            return None
        attrs = changes.pop("attrs", None)
        new = replace(old, **changes, attrs={**old.attrs, **attrs} if attrs else old.attrs)
        if new == old:
            return old
        new = replace(new, updated_at=time.time())
        self._entities[entity_id] = new
        for listener in list(self._change_listeners):  # any change, readings included
            asyncio.create_task(_safe(listener, old, new))
        if new.state != old.state or new.available != old.available:
            log.info("%s: %s/%s -> %s/%s", new.name, old.state, old.available, new.state, new.available)
            for listener in list(self._state_listeners):
                asyncio.create_task(_safe(listener, old, new))
        return new

    async def emit(self, entity_id: str, event: str, data: dict[str, Any] | None = None) -> None:
        ent = self._entities.get(entity_id)
        if not ent:
            return
        log.info("%s: event %s", ent.name, event)
        for listener in list(self._event_listeners):
            asyncio.create_task(_safe(listener, ent, event, data or {}))


async def _safe(listener: Callable[..., Awaitable[None]], *args: Any) -> None:
    try:
        await listener(*args)
    except Exception:  # a broken automation must never take the hub down
        log.exception("Listener %s failed", getattr(listener, "__qualname__", listener))
