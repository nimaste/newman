"""The integration plug-in contract.

An integration is one Python class. It declares what it is (name, category,
the setup fields the dashboard renders as a form) and, once running, registers
entities on the hub, keeps their state current, emits device events and
executes commands. Adding e.g. Lutron = one new module + one line in
integrations/__init__.py - no client screens to build.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from ..state import Hub

log = logging.getLogger(__name__)

SECRET_MASK = "••••"


def address_of(text: Any) -> str | None:
    """The host (name or IP, lower case) in an address: "10.1.1.56", "10.1.1.56:80", "rtsp://user:pass@10.1.1.56:554/x",
    "ffmpeg:http://10.1.1.54/flv?…" - never the credentials. None when there's none."""
    s = str(text or "").strip()
    m = re.search(r"[A-Za-z][A-Za-z0-9+.-]*://", s)
    s = s[m.start():] if m else "//" + s
    try:
        host = urlsplit(s.split("#", 1)[0].split(" ", 1)[0]).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def is_local_address(host: str | None) -> bool:
    """This machine itself (a restream on the same box, not where a camera is)."""
    return not host or host == "localhost" or host.startswith("127.") or host in ("::1", "0.0.0.0")


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    type: str = "text"  # text | password | number | boolean | textarea | select | multiselect
    required: bool = False
    placeholder: str = ""
    help: str = ""
    default: Any = None
    # select/multiselect: fixed choices [{"value", "label"}]. Leave None for choices that depend on the
    # device (e.g. Frigate's cameras) - those come from GET /api/integrations/{id}/options.
    options: tuple = ()
    dynamic: bool = False
    # Rarely changed - the dashboard tucks these under "More settings".
    advanced: bool = False
    # type "devices" (a list of small forms, e.g. Reolink's cameras or a list of calendars): the
    # sub-fields of each entry. Password sub-fields are masked like top-level ones.
    item_fields: tuple = ()
    # type "number": the smallest value saving accepts (also what a running connection holds itself to).
    minimum: float | None = None

    def to_json(self) -> dict[str, Any]:
        out = {"key": self.key, "label": self.label, "type": self.type, "required": self.required,
               "placeholder": self.placeholder, "help": self.help, "default": self.default,
               "options": [dict(o) for o in self.options], "dynamic": self.dynamic, "advanced": self.advanced}
        if self.item_fields:
            out["itemFields"] = [f.to_json() for f in self.item_fields]
        if self.minimum is not None:
            out["min"] = self.minimum
        return out


def check_every_field(default: int, minimum: int, help: str = "") -> Field:
    """The "Check everything every (minutes)" setting of a connection that is told about changes as they
    happen: how often it also asks for every device's status again, in case a change was missed."""
    return Field("check_minutes", "Check everything every (minutes)", "number", default=default, advanced=True,
                 minimum=minimum, help=help or "Changes arrive as they happen; this also asks for everything "
                                               "again now and then, in case one was missed.")


@dataclass
class Context:
    """What the hub hands every integration."""
    hub: "Hub"
    data_dir: Path
    snapshot_dir: Path
    # Write to the activity feed: (kind, title, body, entity_id, data)
    hub_log: Any = lambda *a, **k: None
    # Unique per Newman install (kept in the data folder): part of MQTT client ids, so a second
    # Newman - a test copy, say - never knocks this one off the broker by using the same id.
    install_id: str = "newman"
    # The running integrations by id (so e.g. Reolink can read the camera addresses Frigate knows).
    instances: dict = field(default_factory=dict)
    # A message for one account's open sockets (e.g. telling a wall tablet what music to play):
    # async (account id, message dict). Set by the API once it's running.
    push_to_user: Any = None
    # A message for every open socket (e.g. the wall tablets' Alexa timers): async (message dict). Set by
    # the API once it's running.
    broadcast: Any = None
    # Swapping device ids everywhere they're used (replace.py), for a connection whose devices change shape
    # (two old devices that are one now): async ([{"from", "to", "properties"?}]) -> counts. Set by the API.
    replace_entities: Any = None
    # What still uses a device (replace.uses): (entity id) -> ["automation", "dashboard", …], empty when
    # nothing does - for a connection deciding whether a device it no longer has can go. Set by the API.
    in_use: Any = None


class CommandError(Exception):
    pass


class Integration:
    type: ClassVar[str]
    name: ClassVar[str]
    category: ClassVar[str] = "other"
    # The kinds of device this connection can bring in. The catalog shows these instead of a single
    # category, because many connections (MQTT, Matter, ESPHome, HomeKit…) bring in many kinds.
    provides: ClassVar[tuple[str, ...]] = ()
    description: ClassVar[str] = ""
    fields: ClassVar[list[Field]] = []
    available: ClassVar[bool] = True  # False = listed in the catalog as "coming later"
    # Settings an older Newman used that are still accepted (and kept until converted) though no longer
    # shown in the form - see translate_config / migrate_config.
    extra_config_keys: ClassVar[tuple[str, ...]] = ()
    # Works through a service's unofficial connection, so it may stop working when that service changes
    # things: the dashboard marks it "Experimental".
    experimental: ClassVar[bool] = False
    # Brings in no devices at all (Amazon Alexa: its timers go straight to the wall tablets).
    no_devices: ClassVar[bool] = False

    def __init__(self, instance_id: str, name: str, config: dict[str, Any], ctx: Context) -> None:
        self.id = instance_id
        self.display_name = name
        self.config = config
        self.ctx = ctx
        self.hub = ctx.hub
        self.status = "connecting"  # connecting | pairing | connected | error | disabled
        self.detail = ""
        # Set by the manager: persist changed settings without restarting (e.g. swapping a one-time
        # account password for the device key it unlocked).
        self.save_config: Any = None

    @classmethod
    def catalog_json(cls) -> dict[str, Any]:
        return {"type": cls.type, "name": cls.name, "category": cls.category, "description": cls.description,
                "available": cls.available, "providesAlerts": cls.provides_alerts, "provides": list(cls.provides),
                "fields": [f.to_json() for f in cls.fields], "addByCode": cls.add_by_code,
                "experimental": cls.experimental, "noDevices": cls.no_devices}

    # Connections that add devices one at a time by a setup code (Matter): what to ask for,
    # {"label", "placeholder", "help"}; None = devices come by themselves. The code is used once, never kept.
    add_by_code: ClassVar[dict[str, str] | None] = None

    async def add_device(self, code: str) -> dict[str, Any]:
        """Start adding a device with its setup code; returns progress {"state": "working"|"done"|"error"|
        "cancelled", "step", "message", "elapsed", "seen"} (also in admin_details()["adding"] as it goes on).
        ValueError: refused, in words."""
        raise ValueError(f"{self.name} can't add devices by code")

    async def cancel_add(self) -> dict[str, Any]:
        """Stop the "add a device" that's running; returns the progress afterwards. ValueError: nothing to stop."""
        raise ValueError("Nothing is being added right now.")

    def add_seen(self) -> None:
        """The person has seen how the last "add a device" ended (its "seen" becomes true)."""

    async def sign_in(self, answer: str) -> None:
        """Finish signing in to the service with what its own sign-in page gave the admin (admin_details()
        says what to open). Only what the service hands back is kept. ValueError: refused, in words."""
        raise ValueError(f"{self.name} doesn't sign in this way")

    async def sign_out(self) -> None:
        """Forget the service's sign-in (the admin signs in again to use it)."""
        raise ValueError(f"{self.name} doesn't sign in this way")

    async def remove_device(self, device_id: str) -> None:
        """Take one of admin_details()["pairedDevices"] off this connection for good (KeyError: unknown)."""
        raise ValueError(f"{self.name} can't remove devices")

    @classmethod
    def migrate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        """Upgrade settings saved by an older Newman. Return them unchanged when nothing to do."""
        return config

    def translate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Settings sent by a client (before they're merged into the saved ones): turn keys an older
        client still uses into today's. The running instance does it, so it can use what it knows of the
        device. Return them unchanged when nothing to do."""
        return config

    @classmethod
    def validate(cls, config: dict[str, Any]) -> list[str]:
        errors = [f"{f.label} is required" for f in cls.fields
                  if f.required and config.get(f.key) in (None, "")]
        for f in cls.fields:
            v = config.get(f.key)
            if f.type == "number" and f.minimum is not None and v not in (None, ""):
                if isinstance(v, bool) or not isinstance(v, (int, float)) or v < f.minimum:
                    errors.append(f"{f.label}: {f.minimum:g} or more")
        return errors

    def seconds_every(self, key: str, unit: float = 60.0) -> float:
        """A "Check every …" number setting (in `unit` seconds, minutes by default) as seconds: its default
        when unset or not a number, and never under the field's minimum."""
        f = next(f for f in self.fields if f.key == key)
        v = self.config.get(key)
        n = v if isinstance(v, (int, float)) and not isinstance(v, bool) else f.default
        if f.minimum is not None:
            n = max(f.minimum, n)
        return float(n) * unit

    def entity_id(self, local: str) -> str:
        return f"{self.id}.{local}"

    async def run(self) -> None:
        """Long-running: connect, keep entities current, reconnect on failure."""
        raise NotImplementedError

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        raise CommandError(f"{self.name} can't {command}")

    # Changing a level gradually ("Brighten / Dim while held"). A device that can ramp a level by itself,
    # smoothly (a Zigbee or Matter light's Level Control "move"), says so in can_move(); move() starts it
    # going (+1 up - turning it on, low, if it's off - or -1 down, never turning it off) at a speed that
    # takes `full_s` seconds from bottom to top, and move(…, 0) stops it where it is. Its new level comes
    # back as an ordinary state update. Anything else is stepped, each step with command_fading().
    def can_move(self, entity_id: str, attr: str) -> bool:
        return False

    async def move(self, entity_id: str, attr: str, direction: int, full_s: float) -> None:
        raise CommandError(f"{self.hub.name(entity_id)} can't change its {attr} by itself")

    async def command_fading(self, entity_id: str, command: str, value: Any, seconds: float) -> None:
        """command(), fading to the new value over `seconds` where the device can (by default it can't:
        the value changes at once)."""
        await self.command(entity_id, command, value)

    # "Do this for N minutes, then put it back" (automations). A device whose whole setup can't be
    # read back from its state (a music player's queue and spot in a song) keeps its own snapshot:
    # snapshot() before the command, marker() right after it (what "still ours" looks like), and
    # restore() when the time is up. None from snapshot() = the engine's generic put-back by state.
    # Both must be plain JSON - they're kept with the timer, so they survive a restart.
    def snapshot(self, entity_id: str) -> dict[str, Any] | None:
        return None

    def marker(self, entity_id: str) -> dict[str, Any] | None:
        return None

    async def restore(self, entity_id: str, snap: dict[str, Any], marker: dict[str, Any] | None,
                      during: str) -> str:
        """Put the device back as `snap` was - only if it is still as `marker` left it. Returns the
        run-log line: what was put back, or why it was left alone (`during` = "the 5 minutes")."""
        raise CommandError(f"{self.name} can't put that back")

    # Pictures and alerts, for any integration that has them (cameras, doorbells…). Clients
    # never need to know which integration a device came from: they use the "still"/"alerts"
    # URLs the hub adds to each device.
    provides_alerts: ClassVar[bool] = False  # its connection page gets an "Alerts" tab

    def has_still(self, entity_id: str) -> bool:
        return False

    async def still(self, entity_id: str, height: int) -> bytes | None:
        """The device's current picture (JPEG)."""
        return None

    def admin_details(self) -> dict[str, Any]:
        """Extra details only admins may see (e.g. webhook addresses, which contain their secret)."""
        return {}

    def has_live(self, entity_id: str) -> bool:
        return False

    def live_url(self, entity_id: str, quality: str, stream: str | None = None) -> str | None:
        """Upstream websocket for real-time video, speaking go2rtc's MSE protocol ("sub" = the
        low-res stream for tiles, "main" = full quality). The hub relays it to clients. `stream`:
        one of live_streams()'s ids someone picked for this quality (None = the connection's own choice)."""
        return None

    def live_streams(self, entity_id: str) -> list[dict[str, Any]]:
        """The streams a device's live video can be played from, [{"id", "label"}] - offered as the
        device's "Tile stream" / "Full-screen stream" choices. Empty (the default): no choice."""
        return []

    def video_derived_from(self, entity_id: str) -> str | None:
        """Another of this connection's devices whose video this device's simply is (e.g. an area watched
        by one camera shows that camera). Such a device is never offered as a "Video from" source and has
        no stream choice of its own: it follows that device's. None (the default): its video is its own."""
        return None

    def video_address(self, entity_id: str) -> str | None:
        """The network host (name or IP, see address_of) a device's video comes from - never credentials.
        Lets "Video from" match a device to the same camera in another connection. None (the default):
        unknown (cloud devices, devices without video)."""
        return None

    def has_alerts(self, entity_id: str | None) -> bool:
        return False

    async def alerts(self, entity_id: str | None, limit: int, before: float | None) -> list[dict[str, Any]]:
        """Recent alerts for one device (None = all of this connection's), newest first:
        [{"id", "start", "end", "severity", "objects", "zones", "thumbUrl", "snapshotUrl", "clipUrl"?, …}]"""
        return []

    async def receive_webhook(self, entity_id: str, secret: str, body: bytes, content_type: str) -> bool:
        """Something POSTed to /api/public/webhooks/<entity id>/<secret>. Return False when the
        secret doesn't match (the API then answers "not found", revealing nothing)."""
        return False

    async def dynamic_options(self) -> dict[str, list[dict[str, Any]]]:
        """Choices for `dynamic` fields, read from the device: {field key: [{"value", "label", "group"?}]}."""
        return {}

    # Swapping devices (e.g. moving from a bridge to a direct connection): which of this connection's
    # devices can take over from another connection's, and pointing what this connection keeps at the
    # new ones.
    def replacements(self) -> list[dict[str, Any]]:
        """Suggested swaps: [{"from": the other connection's device id, "to": this one's, "name", "kind"}]."""
        return []

    async def replace_entities(self, mapping: dict[str, str]) -> int:
        """Point anything this connection keeps (outside the hub) at new device ids {old: new}. Returns
        how many things changed."""
        return 0

    # Network discovery: service types this integration's devices announce over
    # mDNS/zeroconf, and how to turn one into a pre-filled setup.
    zeroconf: ClassVar[list[str]] = []

    @classmethod
    def from_discovery(cls, name: str, host: str, props: dict[str, str]) -> dict[str, Any] | None:
        """Return {"title", "config"} to offer this device for setup, or None to ignore it."""
        return None

    def set_status(self, status: str, detail: str = "") -> None:
        if status != self.status:
            log.info("[%s] %s%s", self.display_name, status, f" - {detail}" if detail else "")
        self.status, self.detail = status, detail

    @staticmethod
    async def backoff_sleep(attempt: int) -> None:
        await asyncio.sleep(min(2 ** attempt, 120))


def _item_secrets(cls: type[Integration]) -> dict[str, set[str]]:
    return {f.key: {s.key for s in f.item_fields if s.type == "password"} for f in cls.fields if f.item_fields}


def mask_config(cls: type[Integration], config: dict[str, Any]) -> dict[str, Any]:
    secret_keys = {f.key for f in cls.fields if f.type == "password"}
    lists = _item_secrets(cls)
    out = {}
    for k, v in config.items():
        if k in secret_keys and v:
            out[k] = SECRET_MASK
        elif k in lists and lists[k] and isinstance(v, list):
            out[k] = [{sk: (SECRET_MASK if sk in lists[k] and sv else sv) for sk, sv in (item or {}).items()}
                      for item in v]
        else:
            out[k] = v
    return out


def merge_config(cls: type[Integration], old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """A masked secret sent back unchanged keeps the stored value - in list entries too, matched to
    the old entry with the same name (else the same position), so reordering keeps each one's own."""
    merged = dict(old)
    lists = _item_secrets(cls)

    def old_entry(before: list, item: dict, i: int) -> dict:
        named = next((b for b in before if item.get("name") and b.get("name") == item.get("name")), None)
        return named or (before[i] if i < len(before) else {})

    for k, v in new.items():
        if v == SECRET_MASK:
            continue
        if k in lists and lists[k] and isinstance(v, list):
            before = old.get(k) or []
            v = [{sk: (old_entry(before, item, i).get(sk) if sv == SECRET_MASK else sv)
                  for sk, sv in (item or {}).items()} for i, item in enumerate(v)]
        merged[k] = v
    known = {f.key for f in cls.fields} | set(cls.extra_config_keys)
    return {k: v for k, v in merged.items() if k in known}


class PlannedIntegration(Integration):
    """Catalog entry for an integration that isn't built yet."""
    available = False

    async def run(self) -> None:
        self.set_status("disabled", "Not available yet")
