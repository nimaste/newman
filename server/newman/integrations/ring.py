"""Ring: the Ring alarm, live - its mode, locks, door / window and motion sensors, smoke and CO alarms,
leak sensors, sirens and keypads - and who did it (the person or keypad code the Ring app shows).

Newman talks to Ring the way the Ring app does, so no ring-mqtt bridge is needed: for each location it
asks Ring for a ticket, opens Ring's live connection (a websocket to the alarm's base station via
Ring's servers), asks for the device list and then follows every change as it happens. Commands
(lock, arm, sound the siren…) go back over the same connection, and the new state is confirmed by
Ring's own update, like every other connection.

"Who did it" rides along with each change: Ring says who started it (a person in the Ring app, a
keypad code…), and Newman puts the name on the device in the same update as the new state, so
automations never have to wait for it. While ring-mqtt still runs alongside, the same name also goes
on its matching device, so automations that still point at those get names too.

Sign-in happens once (email, password and the code Ring texts you). Newman keeps only Ring's refresh
token (which Ring replaces each time it's used - the new one is saved straight away) and forgets the
password.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import aiohttp
import httpx

from ..kinds import KINDS
from ..store import slug
from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

# Ring's endpoints (module constants so the tests can point them at a fake Ring).
OAUTH = "https://oauth.ring.com/oauth/token"
CLIENT_API = "https://api.ring.com/clients_api/"
DEVICE_API = "https://api.ring.com/devices/v1/"
APP_API = "https://prd-api-us.prd.rings.solutions/api/v1/"
USERS_API = "https://app.ring.com/api/v1/"   # where ring-mqtt looks people up (rs/users/summaries)
WS_URL = "wss://{host}/ws?authcode={ticket}&ack=false"
USER_AGENT = "android:com.ringapp:3.98.0(70492092)"

# Ring alarm states that mean the alarm is going off (or about to: the entry delay).
ALARM_STATES = ("burglar-alarm", "entry-delay", "fire-alarm", "co-alarm", "panic", "user-verified-burglar-alarm",
                "user-verified-co-or-fire-alarm", "burglar-accelerated-alarm", "fire-accelerated-alarm")
MODES = {"none": "disarmed", "some": "armed_home", "all": "armed_away"}
ARM_MODES = {"disarm": "none", "arm_home": "some", "arm_away": "all"}
# Sensors that are "faulted" while open, and so can be bypassed when arming (ring-mqtt's "Faulted" bypass).
BYPASSABLE = ("sensor.contact", "sensor.zone", "sensor.tilt")
# Ring device types that are never devices of their own (adapters inside the base station, codes…).
IGNORED_PREFIXES = ("adapter.", "access-code")

ARM_CHECK_S = 6.0         # how long an arm / disarm waits for the alarm's answer
LATE_WHO_S = 15.0         # Ring's "who" may come a moment after the change itself
KEYPAD_MOTION_S = 20.0    # a keypad's proximity sensor only says "someone came close"; clear it after this
OFFLINE_AFTER_S = 300.0   # without Ring's live connection this long, devices show as offline
TWIN_CHECK_S = 60.0       # how often to look for ring-mqtt's copies of the devices (side-by-side period)
RECONNECT_MIN_S = 1.0     # first reconnect delay; doubles each time, up to two minutes
SEND_TIMEOUT_S = 10.0     # a message (a command) not written to Ring's live connection by then: the socket is stuck
USER_CACHE_S = 6 * 3600   # how long a person's name is remembered
WHO_KEYS = ("initiatingEntityName", "accessCodeName", "userName", "user_name")
# A change that arrives without a "who" (seen live: a lock unlocked with its keypad code): look in Ring's
# activity history - what the Ring app shows - this many seconds after it, for the entry about it.
HISTORY_LOOKUPS_AT = (2.0, 4.0, 7.0, 12.0)
HISTORY_MATCH_S = 60.0    # how long after the change its "by …" entry may be dated
HISTORY_BEFORE_S = 3.0    # Ring dates an entry a moment before Newman hears of it
WHO_SAMPLES = 30          # raw updates kept (who-samples.json) for changes that need a name - to see Ring's fields


class SignedOut(Exception):
    """Ring no longer accepts Newman's token: the password is needed again."""


def flatten(item: dict[str, Any]) -> dict[str, Any]:
    """One device in Ring's list / updates: general.v2 + device.v1 as one dict (like ring-client-api)."""
    out: dict[str, Any] = {}
    for part in ((item.get("general") or {}).get("v2"), (item.get("device") or {}).get("v1")):
        if isinstance(part, dict):
            out.update(part)
    return out


def alarm_state(d: dict[str, Any], now: float | None = None) -> str | None:
    """The alarm's Newman state from its security panel's data (ring-mqtt's ringModeToMqttState)."""
    mode = d.get("mode")
    ends = d.get("transitionDelayEndTimestamp") or 0
    if mode in ("some", "all") and isinstance(ends, (int, float)) and ends / 1000 > (now or time.time()):
        return "arming"
    info = d.get("alarmInfo") or {}
    if info.get("state") in ALARM_STATES:
        return "pending" if info["state"] == "entry-delay" else "triggered"
    return MODES.get(mode)


def health_attrs(d: dict[str, Any]) -> dict[str, Any]:
    """Battery, tamper, power and signal, in the names the hub's "needs attention" understands."""
    out: dict[str, Any] = {}
    level = d.get("batteryLevel")
    if isinstance(level, (int, float)) and not isinstance(level, bool):
        out["batteryLevel"] = 100 if level == 99 else level  # Ring reports a full battery as 99
    if d.get("batteryStatus") not in (None, "none"):
        out["batteryStatus"] = d["batteryStatus"]
    for key in ("tamperStatus", "acStatus", "commStatus"):
        if isinstance(d.get(key), str):
            out[key] = d[key]
    if d.get("manufacturerName"):
        out["manufacturer"] = d["manufacturerName"]
    if d.get("serialNumber"):
        out["serial"] = d["serialNumber"]
    return out


# How a lock moved, from the second half of Ring's impulse ("locked.by-manual"), as the Ring app words it.
HOW = {"by-manual": "manually", "by-one-touch": "by one-touch locking", "by-keypad": "with a keypad code",
       "by-keypad.access-code": "with a keypad code", "by-auto-relock": "by auto-lock", "by-auto-lock": "by auto-lock"}


def how_from(impulses: list[str]) -> str | None:
    """'locked.by-manual' -> 'manually'; None when Ring doesn't say how."""
    for imp in impulses:
        head, _, rest = str(imp).partition(".")
        if head in ("locked", "unlocked") and rest.startswith("by-"):
            return HOW.get(rest) or rest.replace("-", " ").replace(".", " ")
    return None


def _on(v: bool) -> str:
    return "on" if v else "off"


@dataclass
class Part:
    """One Newman device made from (part of) one Ring device - the alarm and its siren are two parts
    of Ring's security panel, a flood & freeze sensor is a leak sensor plus a freeze sensor."""
    eid: str
    zid: str
    kind: str
    name: str
    state: Callable[[dict[str, Any]], str | None]
    # The unique id ring-mqtt gives the same thing - so its copy can be found exactly.
    mqtt_uid: str
    model: str = ""
    main: bool = True         # carries the device's battery / tamper / power details
    who: bool = False         # Ring says who changed it (locks, the alarm, sirens)
    commands: dict[str, tuple] = field(default_factory=dict)  # Newman command -> what to send


@dataclass
class Location:
    id: str
    name: str
    ws: Any = None
    assets: list[dict[str, Any]] = field(default_factory=list)
    panel: str | None = None          # the security panel's zid
    has_hub: bool = True
    down_since: float | None = None
    offline_assets: set[str] = field(default_factory=set)


class RingIntegration(Integration):
    type = "ring"
    name = "Ring"
    category = "security"
    provides = ("alarm", "lock", "contact", "motion", "smoke", "leak", "siren", "temperature", "binary")
    description = ("The Ring alarm, live: arm and disarm, locks, door and window sensors, motion, smoke / CO and leak "
                   "sensors, sirens and keypads, with who did it (the person or keypad code the Ring app shows). "
                   "Talks to Ring directly - no ring-mqtt needed. Sign in once with your Ring email, password and "
                   "the code Ring texts you; Newman keeps Ring's token and forgets the password.")
    fields = [
        Field("email", "Ring email", required=True, placeholder="you@example.com"),
        Field("password", "Ring password", "password",
              help="Only for signing in once. Newman swaps it for Ring's token and forgets it."),
        Field("code", "Code from Ring",
              help="After saving the email and password, Ring texts or emails a code. Paste it here and save."),
        Field("bypass_open", "Bypass open doors and windows when arming", "boolean", default=False, advanced=True,
              help="Without this, Ring won't arm while a door or window is open (the same as the Ring app asking)."),
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._dir = Path(self.ctx.data_dir) / "integrations" / self.id
        self._http = httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT})
        self._hwid = ""
        self._token: dict[str, Any] = {}
        self._token_lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None
        self._locations: dict[str, Location] = {}
        self._devices: dict[str, dict[str, Any]] = {}    # zid -> merged Ring data (+ "_loc", "_asset")
        self._parts: dict[str, Part] = {}                 # entity id -> part
        self._codes: dict[str, str] = {}                  # keypad code (access-code zid) -> its name
        self._users: dict[str, tuple[str, float]] = {}    # Ring user id -> (first name, when looked up)
        self._unattributed: dict[str, float] = {}         # entity id -> when it changed without a "who" yet
        self._history_tasks: dict[str, asyncio.Task] = {}  # entity id -> its history lookup
        self._samples: list[dict[str, Any]] = []
        self._panel_waiters: dict[str, list[asyncio.Future]] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._seq = 1
        self._drops: set[asyncio.Task] = set()  # stalled sockets being closed

    # -- sign-in --------------------------------------------------------------------------

    def _hardware_id(self) -> str:
        """Ring ties the texted code (and every token after it) to this id, so it's kept for good."""
        if not self._hwid:
            f = self._dir / "hardware_id"
            self._dir.mkdir(parents=True, exist_ok=True)
            if not f.exists():
                f.write_text(str(uuid.uuid4()))
            self._hwid = f.read_text().strip()
        return self._hwid

    def _save_token(self, j: dict[str, Any]) -> None:
        self._token = {"access": j["access_token"], "refresh": j.get("refresh_token") or self._token.get("refresh"),
                       "expires": time.time() + float(j.get("expires_in") or 3600) - 120}
        f = self._dir / "token.json"
        tmp = f.with_suffix(".part")
        tmp.write_text(json.dumps(self._token))
        tmp.chmod(0o600)
        tmp.replace(f)  # never a half-written token: Ring has already retired the old one

    async def _oauth(self, grant: dict[str, Any], code: str = "") -> httpx.Response:
        return await self._http.post(OAUTH, json={"client_id": "ring_official_android", "scope": "client", **grant},
                                     headers={"2fa-support": "true", "2fa-code": code, "hardware_id": self._hardware_id()})

    async def _sign_in(self) -> bool:
        """True when signed in. Otherwise sets the status saying what's needed."""
        f = self._dir / "token.json"
        if f.exists():
            self._token = json.loads(f.read_text())
            return True
        email, password = str(self.config.get("email") or ""), str(self.config.get("password") or "")
        code = str(self.config.get("code") or "").strip()
        if not password:
            self.set_status("pairing", "Enter your Ring password (used once) and save; Ring will send a code.")
            return False
        r = await self._oauth({"grant_type": "password", "username": email, "password": password}, code)
        if r.status_code == 412 or (r.status_code == 400 and "Verification Code" in r.text):
            body = r.json() if r.content else {}
            where = f" to {body['phone']}" if body.get("phone") else ""
            self.set_status("pairing", ("That code didn't work - " if code else "") +
                            f"Ring sent a code{where}. Paste it under “Code from Ring” and save.")
            return False
        if r.status_code >= 400:
            self.set_status("error", "Ring didn't accept that email and password" if r.status_code in (400, 401)
                            else f"Ring sign-in failed (HTTP {r.status_code})")
            return False
        self._save_token(r.json())
        # Signed in: keep only the email (and settings); the password and code are never needed again.
        cfg = {k: v for k, v in self.config.items() if k not in ("password", "code")}
        self.config = cfg
        if self.save_config:
            self.save_config(cfg)
        return True

    async def _access(self, refused: str | None = None) -> str:
        """A current access token. `refused`: the one Ring just turned down, so get a new one - unless
        another request already did."""
        # One refresh at a time: Ring retires a refresh token once it's used, so two at once would
        # sign Newman out.
        async with self._token_lock:
            stale = refused is not None and refused == self._token.get("access")
            if stale or time.time() >= self._token.get("expires", 0):
                r = await self._oauth({"grant_type": "refresh_token", "refresh_token": self._token.get("refresh")})
                if r.status_code in (400, 401, 403):
                    (self._dir / "token.json").unlink(missing_ok=True)
                    raise SignedOut("Ring signed Newman out - enter your password again to sign back in")
                r.raise_for_status()
                self._save_token(r.json())
            return self._token["access"]

    async def _rest(self, method: str, url: str, **kw: Any) -> Any:
        """A call to Ring's API as the signed-in user; a refused token is renewed once and retried."""
        refused = None
        for attempt in (0, 1):
            token = await self._access(refused)
            r = await self._http.request(method, url, headers={"Authorization": f"Bearer {token}",
                                                               "hardware_id": self._hardware_id()}, **kw)
            if r.status_code == 401 and attempt == 0:
                refused = token
                continue
            r.raise_for_status()
            return r.json() if r.content else None
        return None

    # -- running ------------------------------------------------------------------------

    async def run(self) -> None:
        if not await self._sign_in():
            await asyncio.Event().wait()  # saving the settings restarts us
        try:
            try:
                await self._rest("POST", CLIENT_API + "session", json={"device": {
                    "hardware_id": self._hardware_id(), "os": "android",
                    "metadata": {"api_version": 11, "device_model": "Newman"}}})
            except httpx.HTTPError as e:
                log.info("Ring session: %s", e)
            data = await self._rest("GET", DEVICE_API + "locations")
            for loc in (data.get("user_locations") if isinstance(data, dict) else data) or []:
                if loc.get("location_id"):
                    self._locations[loc["location_id"]] = Location(loc["location_id"], loc.get("name") or "Home")
            if not self._locations:
                self.set_status("error", "No Ring locations on this account")
                await asyncio.Event().wait()
            self._session = aiohttp.ClientSession()
            await asyncio.gather(*(self._follow(loc) for loc in self._locations.values()), self._twin_loop())
        except SignedOut as e:
            self.set_status("error", str(e))
            await asyncio.Event().wait()
        finally:
            for t in self._timers.values():
                t.cancel()
            if self._session:
                await self._session.close()

    async def _follow(self, loc: Location) -> None:
        """Keep one location's live connection up: a fresh ticket for every (re)connect."""
        attempt = 0
        while True:
            try:
                q = f"clap/tickets?locationID={loc.id}&enableExtendedEmergencyCellUsage=true&requestedTransport=ws"
                t = await self._rest("GET", APP_API + q) or {}
                loc.assets = [a for a in t.get("assets") or []
                              if str(a.get("kind", "")).startswith(("base_station", "beams_bridge"))]
                if not loc.assets:
                    loc.has_hub = False  # cameras only: nothing live to follow here
                    self._update_status()
                    return
                url = WS_URL.format(host=t["host"], ticket=t["ticket"])
                async with self._session.ws_connect(url, heartbeat=30) as ws:  # type: ignore[union-attr]
                    loc.ws, attempt, loc.down_since, loc.offline_assets = ws, 0, None, set()
                    for a in loc.assets:
                        await self._send(loc, {"msg": "DeviceInfoDocGetList", "dst": a["uuid"]})
                    self._update_status()
                    async for m in ws:
                        if m.type == aiohttp.WSMsgType.TEXT:
                            try:
                                outer = json.loads(m.data)
                            except ValueError:
                                continue
                            if await self._on_message(loc, outer) == "reconnect":
                                log.info("Ring asked Newman to reconnect (%s)", loc.name)
                                break
                        elif m.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except (SignedOut, asyncio.CancelledError):
                raise
            except Exception as e:
                log.warning("Ring live connection (%s): %s", loc.name, e)
            loc.ws = None
            loc.down_since = loc.down_since or time.time()
            attempt += 1
            self.set_status("connecting", f"Lost Ring's live connection for {loc.name} - reconnecting")
            await asyncio.sleep(min(RECONNECT_MIN_S * 2 ** (attempt - 1), 120))
            if time.time() - loc.down_since > OFFLINE_AFTER_S:
                for p in self._parts.values():
                    if self._devices.get(p.zid, {}).get("_loc") == loc.id:
                        await self.hub.update(p.eid, available=False)

    def _update_status(self) -> None:
        live = [l for l in self._locations.values() if l.ws is not None]
        if not live and all(not l.has_hub for l in self._locations.values()):
            self.set_status("error", "No Ring alarm or bridge at this account's locations")
            return
        if not live:
            return
        n = len(self._parts)
        self.set_status("connected", f"{', '.join(l.name for l in live)} · {n} device{'s' if n != 1 else ''}")

    async def _send(self, loc: Location, message: dict[str, Any]) -> None:
        ws = loc.ws
        if ws is None or ws.closed:
            raise CommandError("Newman isn't connected to Ring right now - try again in a moment")
        message["seq"] = self._seq
        self._seq += 1
        try:
            await asyncio.wait_for(ws.send_str(json.dumps({"channel": "message", "msg": message})), SEND_TIMEOUT_S)
        except asyncio.TimeoutError:
            # A write cut off half-way may leave a partial frame on the socket: never write to it
            # again. Drop it; _follow sees it close and reconnects with a fresh ticket.
            log.warning("Ring live connection (%s): a send took over %gs - reconnecting", loc.name, SEND_TIMEOUT_S)
            if loc.ws is ws:
                loc.ws = None
            self._spawn_drop(ws)
            raise CommandError("Ring didn't take the command - its live connection stalled. "
                               "Newman is reconnecting; try again in a moment") from None
        except (ConnectionError, RuntimeError, aiohttp.ClientError) as e:
            raise CommandError(f"Ring's live connection failed ({e or type(e).__name__}) - try again in a moment") from e

    def _spawn_drop(self, ws: Any) -> None:
        async def drop() -> None:
            try:
                await asyncio.wait_for(ws.close(), 5)
            except Exception as e:  # already broken: closing is best-effort
                log.info("Ring live connection: closing the stalled socket: %s", e or type(e).__name__)
        t = asyncio.get_running_loop().create_task(drop())
        self._drops.add(t)
        t.add_done_callback(self._drops.discard)

    # -- messages from Ring ---------------------------------------------------------------

    async def _on_message(self, loc: Location, outer: dict[str, Any]) -> str | None:
        if not isinstance(outer, dict):
            return None
        msg, channel = outer.get("msg"), outer.get("channel")
        if not isinstance(msg, dict):
            return None
        if msg.get("datatype") == "HubDisconnectionEventType":
            return "reconnect"
        if msg.get("msg") == "DeviceInfoDocGetList":
            await self._on_list(loc, msg)
        elif channel == "DataUpdate" and msg.get("msg") == "SessionInfo":
            await self._on_sessions(loc, msg.get("body") or [])
        elif channel == "DataUpdate" and msg.get("datatype") == "DeviceInfoDocType":
            await self._on_update(loc, msg)
        return None

    async def _on_list(self, loc: Location, msg: dict[str, Any]) -> None:
        src = msg.get("src") or (loc.assets[0]["uuid"] if loc.assets else "")
        zids = []
        for item in msg.get("body") or []:
            flat = flatten(item)
            if not flat.get("zid"):
                continue
            self._devices[flat["zid"]] = {**self._devices.get(flat["zid"], {}), **flat, "_loc": loc.id, "_asset": src}
            zids.append(flat["zid"])
        for zid in zids:
            await self._publish(zid)
        self._update_status()
        await self._mark_twins()

    async def _on_sessions(self, loc: Location, sessions: list[dict[str, Any]]) -> None:
        """An alarm base station dropping off (or onto its cellular backup) and coming back."""
        for s in sessions:
            uid = s.get("assetUuid")
            if uid not in {a["uuid"] for a in loc.assets}:
                continue
            if s.get("connectionStatus") == "online":
                if uid in loc.offline_assets:
                    loc.offline_assets.discard(uid)
                    log.info("Ring %s is back online", loc.name)
                    await self._send(loc, {"msg": "DeviceInfoDocGetList", "dst": uid})
            elif uid not in loc.offline_assets:
                log.warning("Ring %s is offline or on its cellular backup", loc.name)
                loc.offline_assets.add(uid)

    async def _on_update(self, loc: Location, msg: dict[str, Any]) -> None:
        for item in msg.get("body") or []:
            flat = flatten(item)
            zid = flat.get("zid")
            if not zid:
                continue
            if zid not in self._devices and not flat.get("deviceType"):
                continue  # an update for something we were never told about
            dev = self._devices.setdefault(zid, {"_loc": loc.id, "_asset": msg.get("src") or ""})
            dev.update(flat)
            impulses = [i.get("impulseType", "") for i in ((item.get("impulse") or {}).get("v1") or [])
                        if isinstance(i, dict)]
            if "keypad.motion" in impulses:
                self._keypad_motion(zid)
            if any(p.who for p in self._make_parts(zid, dev)):
                self._sample({"at": time.time(), "context": msg.get("context"), "item": item})
            # "Who" is in the message's context. A device's own "context" (seen live on locks) is its Z-Wave
            # adapter details, not who - only use it if it actually names someone.
            ctx = next((c for c in (item.get("context"), msg.get("context")) if isinstance(c, dict)
                        and (c.get("initiatingEntityId") or any(c.get(k) for k in WHO_KEYS))), msg.get("context"))
            await self._publish(zid, ctx if isinstance(ctx, dict) else None, how_from(impulses))

    def _keypad_motion(self, zid: str) -> None:
        dev = self._devices[zid]
        dev["_motion"] = True
        if old := self._timers.pop(zid, None):
            old.cancel()

        def clear() -> None:
            dev["_motion"] = False
            asyncio.get_running_loop().create_task(self._publish(zid))
        self._timers[zid] = asyncio.get_running_loop().call_later(KEYPAD_MOTION_S, clear)

    # -- Ring devices -> Newman devices ---------------------------------------------------

    def _make_parts(self, zid: str, dev: dict[str, Any]) -> list[Part]:
        """ring-mqtt's mappings, as Newman devices."""
        t, name = str(dev.get("deviceType") or ""), str(dev.get("name") or "Ring device")
        loc = self._locations.get(dev.get("_loc", ""))
        place = loc.name if loc else "Home"
        eid = self.entity_id(slug(zid))
        # Not the "hidden" tag: Ring puts it on the alarm panel itself (seen live). ring-mqtt only uses it to
        # drop phantom "ringnet" bridges, which Newman doesn't bring in anyway.
        if t.startswith(IGNORED_PREFIXES):
            return []
        if t == "security-panel":
            if loc:
                loc.panel = zid
            alarm = {c: ("mode", m) for c, m in ARM_MODES.items()}
            siren = {"turn_on": ("panel", "security-panel.sound-siren"),
                     "turn_off": ("panel", "security-panel.silence-siren")}
            return [Part(eid, zid, "alarm", f"{place} Alarm", alarm_state, zid, "Alarm control panel", who=True,
                         commands=alarm),
                    Part(eid + "_siren", zid, "siren", f"{place} Alarm Siren",
                         lambda d: _on((d.get("siren") or {}).get("state") == "on"), f"{zid}_siren", "Alarm siren",
                         main=False, who=True, commands=siren)]
        if t == "lock" or t.startswith("lock."):
            return [Part(eid, zid, "lock", name, lambda d: {"locked": "LOCKED", "unlocked": "UNLOCKED",
                                                              "jammed": "JAMMED"}.get(str(d.get("locked"))),
                         zid, "Lock", who=True,
                         commands={"lock": ("command", "lock.lock"), "unlock": ("command", "lock.unlock")})]
        if t in ("sensor.contact", "sensor.tilt"):
            return [Part(eid, zid, "contact", name, lambda d: "open" if d.get("faulted") else "closed", zid,
                         "Tilt sensor" if t == "sensor.tilt" else "Contact sensor")]
        if t == "sensor.motion" or (t == "sensor" and "motion" in name.lower()):
            return [Part(eid, zid, "motion", name, lambda d: "detected" if d.get("faulted") else "clear", zid,
                         "Motion sensor")]
        if t in ("sensor.zone", "sensor.glassbreak", "sensor"):
            return [Part(eid, zid, "binary", name, lambda d: _on(bool(d.get("faulted"))), zid,
                         {"sensor.zone": "Retrofit zone", "sensor.glassbreak": "Glass break sensor"}.get(t, "Sensor"))]
        if t in ("alarm.smoke", "alarm.co"):
            return [Part(eid, zid, "smoke", name, lambda d: "detected" if d.get("alarmStatus") == "active" else "clear",
                         zid, "Smoke alarm" if t == "alarm.smoke" else "CO alarm")]
        if t == "listener.smoke-co":
            return [Part(eid, zid, "smoke", name,
                         lambda d: "detected" if (d.get("smoke") or {}).get("alarmStatus") == "active" else "clear",
                         f"{zid}_smoke", "Smoke & CO listener"),
                    Part(eid + "_co", zid, "smoke", f"{name} CO",
                         lambda d: "detected" if (d.get("co") or {}).get("alarmStatus") == "active" else "clear",
                         f"{zid}_gas", "Smoke & CO listener", main=False)]
        if t in ("sensor.flood-freeze", "sensor.water"):
            out = [Part(eid, zid, "leak", name,
                        lambda d: "wet" if (d.get("flood") or {}).get("faulted") or d.get("faulted") else "dry",
                        f"{zid}_moisture", "Flood & freeze sensor")]
            if t == "sensor.flood-freeze":
                out.append(Part(eid + "_freeze", zid, "binary", f"{name} Freeze",
                                lambda d: _on(bool((d.get("freeze") or {}).get("faulted"))), f"{zid}_cold",
                                "Flood & freeze sensor", main=False))
            return out
        if t == "sensor.freeze":
            return [Part(eid, zid, "binary", name, lambda d: _on(bool(d.get("faulted"))), zid, "Freeze sensor")]
        if t == "sensor.temperature":
            parent = self._devices.get(str(dev.get("parentZid") or ""), {})
            if str(parent.get("deviceType", "")).startswith("temperature-control"):
                return []  # a thermostat's own sensor
            return [Part(eid, zid, "temperature", name,
                         lambda d: str(round(float(d["celsius"]), 1)) if isinstance(d.get("celsius"), (int, float))
                         else None, zid, "Temperature sensor")]
        if t in ("siren", "siren.outdoor-strobe"):
            outdoor = t == "siren.outdoor-strobe"
            cmds = ({"turn_on": ("command", "siren-test.start"), "turn_off": ("command", "siren-test.stop")} if outdoor
                    else {"turn_on": ("set", {"on": True}), "turn_off": ("set", {"on": False})})
            return [Part(eid, zid, "siren", name, lambda d: _on(d.get("sirenStatus") == "active"), zid,
                         "Outdoor siren" if outdoor else "Siren", who=True, commands=cmds)]
        if t == "security-keypad":
            # Nothing on a keypad is worth automating except its proximity sensor ("someone came close"),
            # which is also what ring-mqtt shows - and it's where the keypad's battery and power show up.
            return [Part(eid, zid, "motion", name, lambda d: "detected" if d.get("_motion") else "clear",
                         f"{zid}_motion", "Security keypad")]
        if t in ("hub.redsky", "hub.kili", "range-extender.zwave") and "acStatus" in dev:
            # Health only (power, backup battery). On = mains power, so "when it turns off" = a power cut.
            what = "Range extender" if t.startswith("range") else "Base station"
            return [Part(eid, zid, "binary", name if t.startswith("range") else f"{place} Base Station",
                         lambda d: _on(d.get("acStatus") != "error"), zid, what)]
        return []

    async def _publish(self, zid: str, ctx: dict[str, Any] | None = None, how: str | None = None) -> None:
        dev = self._devices.get(zid)
        if not dev:
            return
        if str(dev.get("deviceType", "")).startswith("access-code") and dev.get("name"):
            self._codes[zid] = str(dev["name"])  # a keypad code: who it belongs to
        now = time.time()
        has_ctx = bool(ctx and (ctx.get("initiatingEntityId") or any(ctx.get(k) for k in WHO_KEYS)))
        who: str | None = None
        at = self._event_time(ctx) if has_ctx else now
        resolved = False
        for p in self._make_parts(zid, dev):
            self._parts[p.eid] = p
            old = self.hub.upsert(p.eid, kind=p.kind, name=p.name, integration=self.id)
            state = p.state(dev)
            attrs: dict[str, Any] = health_attrs(dev) if p.main else {}
            if p.model:
                attrs["model"] = p.model
            if p.who:
                attrs["whoLookup"] = True  # the name may come a moment after the change: automations wait for it
            changed = state is not None and old.state is not None and state != old.state
            told = None
            if p.who and changed:
                if has_ctx:
                    if not resolved:
                        who, resolved = await self._who(ctx or {}, dev.get("_loc", "")), True
                    attrs.update(changedBy=who, changedAt=at)
                    told = who
                    self._unattributed.pop(p.eid, None)
                else:
                    # No "who" with this change (yet - Ring may send it a moment later). Coming out of an
                    # in-between state (arming -> armed) is still the same person's doing; otherwise
                    # forget the last name, so it isn't credited with this change.
                    if old.state not in KINDS[p.kind].transitional:
                        attrs.update(changedBy=None, changedAt=None, changedHow=None, changedHowAt=None,
                                     changedByNoOne=None)
                        self._look_in_history(p, dev.get("_loc", ""), now)
                    self._unattributed[p.eid] = now
            elif p.who and has_ctx and now - self._unattributed.get(p.eid, 0) < LATE_WHO_S:
                # The name for a change that came just before.
                if not resolved:
                    who, resolved = await self._who(ctx or {}, dev.get("_loc", "")), True
                if who:
                    attrs.update(changedBy=who, changedAt=at)
                    told = who
                    self._unattributed.pop(p.eid, None)
            if p.who and how:
                # Ring's second message about a lock change says how ("locked.by-manual"), with the person
                # if there was one. By hand, one-touch or auto-lock: no one to name - stop looking.
                no_one = not who and how != HOW["by-keypad"]
                attrs.update(changedHow=how, changedHowAt=now, changedByNoOne=no_one)
                if no_one:
                    self._unattributed.pop(p.eid, None)
                    if task := self._history_tasks.pop(p.eid, None):
                        task.cancel()
            await self.hub.update(p.eid, **({"state": state} if state is not None else {}), available=True, attrs=attrs)
            if told or (p.who and how):
                await self._tell_twin(p, told, at, how if p.who else None)
        if zid in self._panel_waiters:
            for fut in self._panel_waiters.pop(zid):
                if not fut.done():
                    fut.set_result(dict(dev))
        # Arming: Ring's exit delay ends on its own; look again then, in case no update comes.
        ends = dev.get("transitionDelayEndTimestamp")
        if dev.get("deviceType") == "security-panel" and isinstance(ends, (int, float)) and ends / 1000 > now:
            if old_t := self._timers.pop(zid + ":arm", None):
                old_t.cancel()
            loop = asyncio.get_running_loop()
            self._timers[zid + ":arm"] = loop.call_later(ends / 1000 - now + 0.5,
                                                         lambda: loop.create_task(self._publish(zid)))

    @staticmethod
    def _event_time(ctx: dict[str, Any] | None) -> float:
        ts = (ctx or {}).get("eventOccurredTsMs")
        now = time.time()
        if isinstance(ts, (int, float)) and abs(ts / 1000 - now) < 600:
            return ts / 1000
        return now  # missing, or a clock that's off: now is closer to the truth

    # -- who did it -----------------------------------------------------------------------

    async def _who(self, ctx: dict[str, Any], loc_id: str) -> str | None:
        """The name the Ring app would show for whoever started a change."""
        for key in WHO_KEYS:
            v = ctx.get(key)
            if isinstance(v, str) and v.strip() and v.strip().lower() not in ("unknown", "none", "user"):
                return v.strip()
        ent_id, ent_type = ctx.get("initiatingEntityId"), str(ctx.get("initiatingEntityType") or "")
        # A keypad code: its name is the person's, as the Ring app shows it. The code may be the
        # initiator itself or mentioned alongside (a keypad as initiator, with the code it was given).
        for v in [ent_id, *(x for x in ctx.values() if isinstance(x, str))]:
            if v in self._codes:
                return self._codes[v]
        if not isinstance(ent_id, str) or not ent_id or ent_id in self._devices:
            return None  # a device (a keypad, the lock's own button…), not a person
        if ent_type and ent_type not in ("user", "account"):
            return None
        return await self._user_name(ent_id, loc_id)

    async def _user_name(self, user_id: str, loc_id: str) -> str | None:
        hit = self._users.get(user_id)
        if hit and time.time() - hit[1] < USER_CACHE_S:
            return hit[0] or None
        name = ""
        try:
            got = await asyncio.wait_for(self._rest("POST", USERS_API + f"rs/users/summaries?locationId={loc_id}",
                                                    json=[user_id]), 5)
            u = got[0] if isinstance(got, list) and got and isinstance(got[0], dict) else {}
            name = str(u.get("firstName") or u.get("first_name") or u.get("name") or "").strip()
        except SignedOut:
            raise
        except Exception as e:
            log.info("Ring: couldn't look up who %s is: %s", user_id, e)
            return None  # not cached: try again next time
        self._users[user_id] = (name, time.time())
        return name or None

    def _sample(self, entry: dict[str, Any]) -> None:
        """Keep the last few raw updates for locks / the alarm / sirens, to see where Ring puts "who"."""
        self._samples = (self._samples + [entry])[-WHO_SAMPLES:]
        try:
            f = self._dir / "who-samples.json"
            self._dir.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(self._samples, indent=1, default=str))
            f.chmod(0o600)
        except OSError:
            pass

    def _look_in_history(self, p: Part, loc_id: str, changed_at: float) -> None:
        if not loc_id:
            return
        if old := self._history_tasks.pop(p.eid, None):
            old.cancel()
        self._history_tasks[p.eid] = asyncio.get_running_loop().create_task(
            self._history_who(p, loc_id, changed_at))

    async def _history_who(self, p: Part, loc_id: str, changed_at: float) -> None:
        """Find this change in Ring's activity history and put its person's name on the device."""
        try:
            for wait in HISTORY_LOOKUPS_AT:
                await asyncio.sleep(max(0.0, changed_at + wait - time.time()))
                if p.eid not in self._unattributed:
                    return  # the live update brought the name after all
                got = await self._rest("GET", APP_API + f"rs/history?accountId={loc_id}&limit=30&maxLevel=50")
                entries = got if isinstance(got, list) else []
                try:
                    f = self._dir / "history-sample.json"
                    f.write_text(json.dumps(entries, indent=1, default=str)[:300_000])
                    f.chmod(0o600)
                except OSError:
                    pass
                state = (self.hub.get(p.eid).state or "").lower() if self.hub.get(p.eid) else ""
                for entry in entries:
                    ctx = entry.get("context") if isinstance(entry, dict) else None
                    # This change's own entries come right with it (Ring's "by …" entry ~0.2 s later) - an
                    # older one is an earlier change (seen live: a hand-lock credited to the unlock before it).
                    if not isinstance(ctx, dict) or not (changed_at - HISTORY_BEFORE_S <= self._history_time(ctx)
                                                         <= changed_at + HISTORY_MATCH_S):
                        continue
                    body = [i for i in (entry.get("body") or []) if isinstance(i, dict)]
                    if p.zid not in {flatten(i).get("zid") for i in body}:
                        continue
                    impulses = [str(i.get("impulseType", "")).lower() for b in body
                                for i in ((b.get("impulse") or {}).get("v1") or []) if isinstance(i, dict)]
                    if p.kind == "lock" and state and impulses and not any(i.split(".")[0] == state for i in impulses):
                        continue  # "locked.by-manual" isn't about an unlock
                    # The Ring app lists a change twice - "Unlocked", then "Unlocked by Christopher Garito"
                    # (seen live) - so a nameless entry isn't the answer; keep looking for the named one.
                    who = await self._who(ctx, loc_id)
                    if not who:
                        continue
                    log.info("Ring history: %s changed by %s", p.name, who)
                    await self.hub.update(p.eid, attrs={"changedBy": who, "changedAt": changed_at})
                    await self._tell_twin(p, who, changed_at)
                    self._unattributed.pop(p.eid, None)
                    return
            log.info("Ring history: nothing about %s's change", p.name)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.info("Ring history lookup for %s: %s", p.name, e)
        finally:
            if self._history_tasks.get(p.eid) is asyncio.current_task():
                self._history_tasks.pop(p.eid, None)

    @staticmethod
    def _history_time(ctx: dict[str, Any]) -> float:
        ts = ctx.get("eventOccurredTsMs")
        return ts / 1000 if isinstance(ts, (int, float)) else 0.0

    # -- ring-mqtt's copies, while both run ------------------------------------------------

    def _twin(self, p: Part) -> str | None:
        """ring-mqtt's copy of a device (through the MQTT connection): by the unique id ring-mqtt gives it,
        else the only one of the same kind with the same name."""
        for inst in list(self.ctx.instances.values()):
            if getattr(inst, "type", "") != "mqtt":
                continue
            e = self.hub.get(f"{inst.id}.{p.mqtt_uid}")
            if e and e.kind == p.kind:
                return e.id
            same = [e for e in self.hub.all(p.kind) if e.integration == inst.id
                    and e.name.strip().casefold() == p.name.strip().casefold()]
            if len(same) == 1:
                return same[0].id
        return None

    async def _tell_twin(self, p: Part, who: str | None, at: float, how: str | None = None) -> None:
        if twin := self._twin(p):
            attrs: dict[str, Any] = {"changedBy": who, "changedAt": at} if who else {}
            if how:
                attrs.update(changedHow=how, changedHowAt=time.time())
            await self.hub.update(twin, attrs=attrs)

    async def _mark_twins(self) -> None:
        """ring-mqtt's state can arrive before Ring's "who" reaches Newman: tell automations on its copies
        that a name is on the way, so they wait a moment for it."""
        for p in list(self._parts.values()):
            if p.who and (twin := self._twin(p)):
                e = self.hub.get(twin)
                if e and not e.attrs.get("whoLookup"):
                    await self.hub.update(twin, attrs={"whoLookup": True})

    async def _twin_loop(self) -> None:
        while True:
            await asyncio.sleep(TWIN_CHECK_S)
            await self._mark_twins()

    def replacements(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self._parts.values(), key=lambda p: (p.kind, p.name)):
            if self.hub.get(p.eid) and (twin := self._twin(p)):
                out.append({"from": twin, "to": p.eid, "name": self.hub.name(p.eid), "kind": p.kind})
        return out

    # -- commands -------------------------------------------------------------------------

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        p = self._parts.get(entity_id)
        if not p:
            raise CommandError(f"{self.hub.name(entity_id)} isn't a Ring device Newman knows")
        what = p.commands.get(command)
        if not what:
            raise CommandError(f"{p.name} can't {command.replace('_', ' ')}")
        dev = self._devices[p.zid]
        loc = self._locations.get(dev.get("_loc", ""))
        if not loc or loc.ws is None:
            raise CommandError("Newman isn't connected to Ring right now - try again in a moment")
        how, arg = what
        if how == "command":
            await self._device_command(loc, p.zid, arg)
        elif how == "set":
            await self._set_info(loc, p.zid, {"device": {"v1": arg}})
        elif how == "panel":
            await self._device_command(loc, loc.panel or p.zid, arg)
        elif how == "mode":
            await self._set_mode(loc, p, arg)
        log.info("Sent %s to %s", command, p.name)

    async def _set_info(self, loc: Location, zid: str, body: dict[str, Any]) -> None:
        """ring-client-api's RingDevice.setInfo."""
        dev = self._devices.get(zid, {})
        await self._send(loc, {"msg": "DeviceInfoSet", "datatype": "DeviceInfoSetType",
                               "dst": dev.get("_asset") or (loc.assets[0]["uuid"] if loc.assets else ""),
                               "body": [{"zid": zid, **body}]})

    async def _device_command(self, loc: Location, zid: str, command_type: str, data: dict | None = None) -> None:
        """ring-client-api's RingDevice.sendCommand."""
        await self._set_info(loc, zid, {"command": {"v1": [{"commandType": command_type, "data": data or {}}]}})

    async def _set_mode(self, loc: Location, p: Part, mode: str) -> None:
        """ring-client-api's setAlarmMode: switch, then check the alarm's next answer, because Ring refuses
        to arm (without saying why) while a sensor that would need bypassing is open."""
        bypass: list[str] = []
        if mode != "none" and self.config.get("bypass_open"):
            bypass = [z for z, d in self._devices.items() if d.get("_loc") == loc.id
                      and d.get("deviceType") in BYPASSABLE and d.get("faulted")]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._panel_waiters.setdefault(p.zid, []).append(fut)
        await self._device_command(loc, p.zid, "security-panel.switch-mode", {"mode": mode, "bypass": bypass})
        try:
            answer = await asyncio.wait_for(fut, ARM_CHECK_S)
        except asyncio.TimeoutError:
            return  # no answer yet: the state still confirms (or not) the usual way
        finally:
            waiters = self._panel_waiters.get(p.zid, [])
            if fut in waiters:
                waiters.remove(fut)
        if answer.get("mode") != mode:
            if mode == "none":
                raise CommandError("Ring didn't disarm the alarm - try again, or use the Ring app")
            raise CommandError("Ring didn't arm the alarm - a door or window is probably open. Close it, or turn on "
                               "“Bypass open doors and windows when arming” in the Ring connection's settings."
                               if not self.config.get("bypass_open") else
                               "Ring didn't arm the alarm - a sensor needs attention. Check the Ring app.")
