"""What every TV connection shares: the remote's keys, Wake-on-LAN, one "tv" device per connection,
turning a "tv" command into the brand's own call, and pairing sessions that outlive a settings save.

Each brand module (roku, samsung_tv, webos_tv, android_tv, apple_tv, vidaa_tv) subclasses TvIntegration
and fills in the few calls its protocol has (power, keys, volume, sources, apps). One connection = one TV,
so a household with three TVs has three connections - each found on its own by discovery.
"""
from __future__ import annotations

import asyncio
import logging
import re
import socket
import time
from pathlib import Path
from typing import Any, ClassVar

from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

# The remote's keys, in the order a remote pad shows them. Each brand maps the ones it has (KEYMAP), and the
# device lists those in attrs["keys"] - the "Send key" command's choices.
KEYS: list[tuple[str, str]] = [
    ("power", "Power"), ("home", "Home"), ("back", "Back"), ("up", "Up"), ("down", "Down"), ("left", "Left"),
    ("right", "Right"), ("ok", "OK"), ("menu", "Menu"), ("info", "Info"), ("guide", "Guide"),
    ("play_pause", "Play / pause"), ("play", "Play"), ("pause", "Pause"), ("stop", "Stop"), ("rewind", "Rewind"),
    ("fast_forward", "Fast forward"), ("next", "Next"), ("previous", "Previous"), ("volume_up", "Volume up"),
    ("volume_down", "Volume down"), ("mute", "Mute"), ("channel_up", "Channel up"),
    ("channel_down", "Channel down"), ("input", "Input"), ("search", "Search"),
]
KEY_NAMES = dict(KEYS)

# Settings every TV connection has.
HOST = Field("host", "TV address", required=True, placeholder="10.1.1.50",
             help="Filled in automatically when Newman finds the TV on your network. Give the TV a fixed address "
                  "in your router so it keeps it.")
MAC = Field("mac", "TV's network (MAC) address", placeholder="a0:62:fb:66:e5:23",
            help="Used to wake the TV with Wake-on-LAN. Filled in automatically once Newman has reached the TV.")
CODE = Field("code", "Code shown on the TV", placeholder="1234",
             help="Only while pairing: the code the TV shows. Newman keeps the key it unlocks, not the code.")
POLL = Field("poll_seconds", "Check every (seconds)", "number", default=5, advanced=True)


class NeedsPairing(Exception):
    """The TV wants pairing first (shown as the connection's "pairing" status)."""


def normalise_mac(mac: Any) -> str:
    hexes = re.sub(r"[^0-9a-f]", "", str(mac or "").lower())
    return ":".join(hexes[i:i + 2] for i in range(0, 12, 2)) if len(hexes) == 12 else ""


def magic_packet(mac: str) -> bytes:
    raw = bytes.fromhex(normalise_mac(mac).replace(":", ""))
    if len(raw) != 6:
        raise ValueError(f"Not a MAC address: {mac!r}")
    return b"\xff" * 6 + raw * 16


def _send_wol(packet: bytes, targets: list[tuple[str, int]]) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for t in targets:
            try:
                s.sendto(packet, t)
            except OSError as e:
                log.debug("Wake-on-LAN to %s: %s", t, e)


async def wake_on_lan(mac: str, host: str = "") -> None:
    """Magic packet to the whole network (and straight to the TV's last address, which works on networks
    that drop broadcasts but keep the TV's address in their tables). Needs host networking."""
    packet = magic_packet(mac)
    targets = [("255.255.255.255", 9), ("255.255.255.255", 7)]
    if host:
        targets.append((host, 9))
    await asyncio.to_thread(_send_wol, packet, targets)


def mac_from_arp(host: str, arp: Path = Path("/proc/net/arp")) -> str:
    """The MAC the system last saw at `host` - so any TV we've reached gets a MAC for waking it later."""
    try:
        for line in arp.read_text().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 4 and parts[0] == host and parts[3] != "00:00:00:00:00:00":
                return normalise_mac(parts[3])
    except OSError:
        pass
    return ""


def pick(value: Any) -> str:
    """A choices command sends a list of ids; a TV acts on one (the first)."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value if value is not None else "").strip()


def items(pairs: Any) -> list[dict[str, str]]:
    """[(id, name)] or {id: name} -> the [{"id", "name"}] list a choices command picks from."""
    if isinstance(pairs, dict):
        pairs = pairs.items()
    return [{"id": str(i), "name": str(n or i)} for i, n in pairs if i not in (None, "")]


# -- pairing sessions -----------------------------------------------------------------------------------
# A TV shows its pairing code for one open connection. Saving the code restarts the integration, so the
# open session is parked here (by connection id) and picked up again by the restarted run().

_PENDING: dict[str, tuple[float, Any]] = {}
PENDING_SECONDS = 300


def park(instance_id: str, session: Any) -> None:
    _PENDING[instance_id] = (time.monotonic(), session)


def unpark(instance_id: str) -> Any:
    """The parked pairing session, or None if there is none (or it went stale)."""
    at, session = _PENDING.pop(instance_id, (0.0, None))
    if session is not None and time.monotonic() - at > PENDING_SECONDS:
        return None
    return session


class TvIntegration(Integration):
    """One TV. Subclasses implement run() (connect + keep state) and the do_* calls their protocol has."""
    category = "other"
    provides = ("tv",)
    KEYMAP: ClassVar[dict[str, Any]] = {}
    # Can the protocol itself switch the TV on? If not (or it fails), turn_on falls back to Wake-on-LAN.
    wakes_itself: ClassVar[bool] = False

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.eid = self.entity_id("tv")
        self._created: str | bool = False  # the name the device was last given
        self.host = str(self.config.get("host") or "").strip()

    # -- the device ---------------------------------------------------------------------------------

    def ensure_device(self, name: str = "") -> None:
        """The TV's device: named after the connection, or - while that still has its default name - after
        the name the TV reports (renaming the device by hand always wins; the hub keeps that)."""
        if self._created and not name:
            return
        if self.display_name and self.display_name != self.name:
            name = self.display_name
        name = name or self.display_name or self.name
        if not self._created or name != self._created:
            self.hub.upsert(self.eid, kind="tv", name=name, integration=self.id)
            self._created = name

    async def publish(self, state: str | None = None, available: bool = True, name: str = "", **attrs: Any) -> None:
        self.ensure_device(name)
        changes: dict[str, Any] = {"available": available}
        if state is not None:
            changes["state"] = state
        attrs.setdefault("host", self.host)
        if self.KEYMAP:
            attrs.setdefault("keys", items((k, KEY_NAMES.get(k, k)) for k in self.KEYMAP))
        await self.hub.update(self.eid, attrs=attrs, **changes)

    async def mark_off(self) -> None:
        """Not answering: most TVs drop off the network when switched off. Still "available", so it can be
        woken (when there's a MAC) rather than shown as broken."""
        await self.publish("off", available=bool(self.config.get("mac")) or self.wakes_itself)

    def remember(self, **values: Any) -> None:
        """Save something learned from the TV (its MAC, a pairing key) without restarting."""
        changed = {k: v for k, v in values.items() if v and self.config.get(k) != v}
        if not changed:
            return
        self.config = {**self.config, **changed}
        if self.save_config:
            self.save_config(self.config)

    def learn_mac(self, mac: str = "") -> None:
        if not self.config.get("mac"):
            mac = normalise_mac(mac) or mac_from_arp(self.host)
            if mac:
                self.remember(mac=mac)

    def forget(self, *keys: str) -> None:
        """Drop saved values (a used pairing code, a key the TV no longer accepts)."""
        if any(self.config.get(k) for k in keys):
            self.config = {k: v for k, v in self.config.items() if k not in keys}
            if self.save_config:
                self.save_config(self.config)

    @property
    def poll_seconds(self) -> float:
        return max(1.0, float(self.config.get("poll_seconds") or 5))

    # -- commands -----------------------------------------------------------------------------------

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        if entity_id != self.eid:
            raise CommandError("That TV isn't part of this connection")
        try:
            await self._dispatch(command, value)
        except CommandError:
            raise
        except NotImplementedError:
            raise CommandError(f"{self.name} can't {command.replace('_', ' ')}") from None
        except Exception as e:
            log.info("[%s] %s failed: %s: %s", self.display_name, command, e.__class__.__name__, e)
            raise CommandError(f"The TV didn't accept that ({e.__class__.__name__})") from e

    async def _dispatch(self, command: str, value: Any) -> None:
        if command == "turn_on":
            await self.turn_on()
        elif command == "turn_off":
            await self.do_power_off()
            # "off" confirms the command; a TV that only naps reports "standby" on its next update.
            await self.publish("off")
        elif command == "set_volume":
            level = max(0, min(100, int(float(value if value not in (None, "") else 20))))
            await self.do_set_volume(level)
            await self.publish(volume=level)
        elif command in ("volume_up", "volume_down", "mute"):
            await self.do_key(command)
        elif command in ("play", "pause", "next", "previous"):
            await self.do_media(command)
        elif command == "select_source":
            await self.do_select_source(self._need(value, "source"))
        elif command == "launch_app":
            await self.do_launch_app(self._need(value, "app"))
        elif command == "send_key":
            key = self._need(value, "key")
            if key not in self.KEYMAP:
                raise CommandError(f"This TV has no “{KEY_NAMES.get(key, key)}” key")
            await self.do_key(key)
        else:
            raise CommandError(f"A TV can't {command.replace('_', ' ')}")

    @staticmethod
    def _need(value: Any, what: str) -> str:
        v = pick(value)
        if not v:
            raise CommandError(f"Choose a {what} first")
        return v

    async def turn_on(self) -> None:
        mac = self.config.get("mac")
        if self.wakes_itself:
            try:
                await self.do_power_on()
                await self.publish("on")
                return
            except Exception:
                if not mac:
                    raise
        if not mac:
            raise CommandError("This TV can only be woken with its network (MAC) address - add it in the "
                               "connection's settings")
        await wake_on_lan(mac, self.host)

    # Brand calls (NotImplementedError = "this TV can't").
    async def do_power_on(self) -> None:
        raise NotImplementedError

    async def do_power_off(self) -> None:
        await self.do_key("power")

    async def do_set_volume(self, level: int) -> None:
        raise NotImplementedError

    async def do_media(self, action: str) -> None:
        key = action if action in self.KEYMAP else "play_pause" if action in ("play", "pause") else action
        if key not in self.KEYMAP:
            raise NotImplementedError
        await self.do_key(key)

    async def do_select_source(self, source_id: str) -> None:
        raise NotImplementedError

    async def do_launch_app(self, app_id: str) -> None:
        raise NotImplementedError

    async def do_key(self, key: str) -> None:
        raise NotImplementedError
