"""Rachio sprinkler controllers, through Rachio's public API with the account's API key.

Rachio allows 1,700 requests a day per account, so Newman polls with that in mind: what's watering
every few minutes (spread over all controllers so the day's total stays under ~1,000), names / rain
delay / standby every half hour, the next scheduled run hourly - and once more right after each command
and when a run is due to end.

Instant updates (optional): Rachio can call Newman when a zone starts or stops, but only if Rachio can
reach Newman's /api/public/webhooks address from the internet (behind a client-certificate proxy it
can't, unless that path is let through). Give the public address in "Webhook address" and Newman
registers itself with Rachio and polls only every half hour.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any

import httpx

from ._common import TaskKeeper, plural
from ._sprinklers import command_number, controller_state, iso, set_zone
from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

API = "https://api.rach.io/1/public"
DAILY_BUDGET = 1000          # of Rachio's 1,700 - room for commands and the Rachio app's own use
MIN_POLL_S = 180
WEBHOOK_POLL_S = 1800
INFO_EVERY_S = 1800
NEXT_RUN_EVERY_S = 3600
MAX_DELAY_DAYS = 7           # Rachio's limit for a rain delay
HOOK_EVENTS = {"DEVICE_STATUS_EVENT", "ZONE_STATUS_EVENT", "RAIN_DELAY_EVENT", "RAIN_SENSOR_DETECTION_EVENT",
               "SCHEDULE_STATUS_EVENT"}
ZONE_ON = {"ZONE_STARTED"}
ZONE_OFF = {"ZONE_STOPPED", "ZONE_COMPLETED", "ZONE_CYCLING_COMPLETED"}


class BadKey(Exception):
    pass


def _ms(v: Any) -> float | None:
    return float(v) / 1000 if isinstance(v, (int, float)) and v > 0 else None


class RachioIntegration(Integration):
    type = "rachio"
    name = "Rachio"
    category = "other"
    provides = ("sprinkler", "sprinkler_zone")
    description = ("Rachio sprinkler controllers: what's watering and for how long, last and next run; water a zone "
                   "for N minutes, stop, rain delay (up to 7 days) and standby. Uses the Rachio account's API key - "
                   "no password. Polls within Rachio's daily limit; instant updates if Rachio can reach Newman.")
    fields = [
        Field("api_key", "API key", "password", required=True,
              help="In app.rach.io: your account (top right) → Account settings → Get API key. Copy it here."),
        Field("webhook_base", "Webhook address", advanced=True, placeholder="https://newman.example.com",
              help="Only if Rachio can reach Newman from the internet without a client certificate (the "
                   "/api/public/webhooks path let through your proxy). Leave empty to poll."),
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._dir = Path(self.ctx.data_dir) / "integrations" / self.id
        self._http = httpx.AsyncClient(timeout=30)
        self._devices: dict[str, str] = {}                 # device id -> controller entity id
        self._zones: dict[str, tuple[str, str]] = {}       # zone id -> (entity id, device id)
        self._ends: dict[str, float] = {}                  # device id -> when the current run ends
        self._person = ""
        self._remaining_calls: int | None = None
        self._hook_secret = ""
        self._webhooks = False
        self._wake = asyncio.Event()
        self._tasks = TaskKeeper()

    async def _api(self, method: str, path: str, body: Any = None) -> Any:
        try:
            r = await self._http.request(method, f"{API}{path}", json=body,
                                         headers={"Authorization": f"Bearer {self.config.get('api_key', '')}"})
        except httpx.HTTPError as e:
            raise CommandError(f"couldn't reach Rachio ({type(e).__name__})") from None
        left = r.headers.get("X-RateLimit-Remaining")
        if left and left.isdigit():
            self._remaining_calls = int(left)
        if r.status_code == 401:
            raise BadKey("Rachio refused the API key - copy it again from app.rach.io")
        if r.status_code == 429:
            raise CommandError("Rachio's daily limit is used up; it resets at midnight UTC")
        if r.status_code >= 400:
            try:
                msg = (r.json() or {}).get("message") or ""
            except ValueError:
                msg = ""
            raise CommandError(msg or f"Rachio said no (HTTP {r.status_code})")
        return r.json() if r.content else None

    # -- running --------------------------------------------------------------------------

    def poll_interval(self) -> float:
        """Seconds between "what's watering" checks, keeping the day's requests within budget."""
        if self._remaining_calls is not None and self._remaining_calls < 150:
            return 3600
        if self._webhooks:
            return WEBHOOK_POLL_S
        return max(MIN_POLL_S, len(self._devices) * 86400 / DAILY_BUDGET)

    async def run(self) -> None:
        attempt, info_at, next_at = 0, 0.0, 0.0
        while True:
            try:
                if not self._person:
                    self._person = str((await self._api("GET", "/person/info") or {}).get("id") or "")
                    if not self._person:
                        raise CommandError("Rachio didn't say whose key this is")
                if time.monotonic() - info_at > INFO_EVERY_S:
                    await self._load()
                    info_at = time.monotonic()
                    if self.config.get("webhook_base") and not self._webhooks:
                        await self._register_webhooks()
                if time.monotonic() - next_at > NEXT_RUN_EVERY_S:
                    await self._next_runs()
                    next_at = time.monotonic()
                for dev in list(self._devices):
                    await self.refresh(dev)
                attempt = 0
                self.set_status("connected", f"{plural(len(self._devices), 'controller')}, "
                                             f"{plural(len(self._zones), 'zone')}"
                                + (" · instant updates" if self._webhooks else ""))
                await self._sleep()
            except asyncio.CancelledError:
                raise
            except BadKey as e:
                self.set_status("error", str(e))
                self._person = ""
                await asyncio.sleep(1800)
            except Exception as e:
                log.warning("Rachio: %s", e)
                self.set_status("error", f"Rachio: {e}")
                await self.backoff_sleep(attempt + 6)
                attempt += 1

    async def _sleep(self) -> None:
        wait = self.poll_interval()
        ends = [e - time.time() + 20 for e in self._ends.values() if e > time.time()]
        if ends:
            wait = min(wait, max(30.0, min(ends)))  # look again just after the run should end
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), wait)
        except asyncio.TimeoutError:
            pass

    async def _load(self) -> None:
        person = await self._api("GET", f"/person/{self._person}") or {}
        for d in person.get("devices") or []:
            await self._apply_device(d)
        if not self._devices:
            self.set_status("error", "No controllers on this Rachio account")

    async def _apply_device(self, d: dict[str, Any]) -> None:
        dev = str(d.get("id"))
        eid = self.entity_id(f"controller_{dev[:8]}")
        self._devices[dev] = eid
        self.hub.upsert(eid, kind="sprinkler", name=d.get("name") or "Rachio", integration=self.id)
        for z in d.get("zones") or []:
            if not z.get("enabled", True):
                continue
            zeid = self.entity_id(f"zone_{str(z.get('id'))[:8]}")
            self._zones[str(z.get("id"))] = (zeid, dev)
            self.hub.upsert(zeid, kind="sprinkler_zone", name=z.get("name") or f"Zone {z.get('zoneNumber')}",
                            integration=self.id)
            last = _ms(z.get("lastWateredDate"))
            await self.hub.update(zeid, attrs={"zoneNumber": z.get("zoneNumber"),
                                              **({"lastRun": iso(last)} if last else {})})
        ent = self.hub.get(eid)
        delay_until = _ms(d.get("rainDelayExpirationDate"))
        delayed = bool(delay_until and delay_until > time.time())
        standby = d.get("on") is False
        sensor = d.get("rainSensorTripped")
        await self.hub.update(eid, available=str(d.get("status") or "ONLINE").upper() == "ONLINE",
                              state=controller_state(ent.state == "watering" if ent else False, standby, delayed),
                              attrs={"manufacturer": "Rachio", "model": d.get("model") or "",
                                     "serial": d.get("serialNumber") or "", "standby": standby,
                                     "rainDelayUntil": iso(delay_until) if delayed else None,
                                     **({"rainSensor": "wet" if sensor else "dry"} if isinstance(sensor, bool) else {})})

    async def refresh(self, dev: str) -> None:
        """What's watering on one controller (1 request)."""
        cur = await self._api("GET", f"/device/{dev}/current_schedule") or {}
        zone_id = str(cur.get("zoneId") or "")
        start, dur = _ms(cur.get("zoneStartDate")), cur.get("zoneDuration")
        end = start + float(dur) if start and isinstance(dur, (int, float)) else None
        await self._show(dev, zone_id if zone_id in self._zones else "", end)

    async def _show(self, dev: str, zone_id: str, end: float | None) -> None:
        remaining = max(0, round((end - time.time()) / 60)) if end else None
        if zone_id and end:
            self._ends[dev] = end
        else:
            self._ends.pop(dev, None)
        for zid, (zeid, d) in self._zones.items():
            if d != dev:
                continue
            if zid == zone_id:
                await set_zone(self.hub, zeid, True, remaining=remaining)
            else:
                await set_zone(self.hub, zeid, False)
        eid = self._devices.get(dev)
        ent = self.hub.get(eid) if eid else None
        if not ent:
            return
        standby = ent.attrs.get("standby") is True
        delayed = bool(ent.attrs.get("rainDelayUntil"))
        await self.hub.update(eid, state=controller_state(bool(zone_id), standby, delayed),
                              attrs={"currentZone": self.hub.name(self._zones[zone_id][0]) if zone_id else None,
                                     "remaining": remaining if zone_id else None})

    async def _next_runs(self) -> None:
        """The next scheduled run per controller (1 request each); best effort."""
        for dev, eid in list(self._devices.items()):
            try:
                items = await self._api("GET", f"/device/{dev}/scheduleitem") or []
            except CommandError as e:
                log.info("Rachio schedule for %s: %s", dev, e)
                continue
            starts = sorted(s for s in (_ms((i or {}).get("startDate") or (i or {}).get("absoluteStartDate"))
                                        for i in items if isinstance(i, dict)) if s and s > time.time())
            await self.hub.update(eid, attrs={"nextRun": iso(starts[0]) if starts else None})

    # -- webhooks (optional) --------------------------------------------------------------

    def _hook_path(self, dev: str) -> str:
        return f"/api/public/webhooks/{self._devices[dev]}/{self._hook_secret}"

    async def _register_webhooks(self) -> None:
        f = self._dir / "webhook.json"
        if f.exists():
            self._hook_secret = json.loads(f.read_text()).get("secret", "")
        if not self._hook_secret:
            self._hook_secret = secrets.token_urlsafe(24)
            self._dir.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({"secret": self._hook_secret}))
            f.chmod(0o600)
        base = str(self.config["webhook_base"]).rstrip("/")
        tag = f"newman:{self.id}"
        types = await self._api("GET", "/notification/webhook_event_type") or []
        wanted = [{"id": t["id"]} for t in types if isinstance(t, dict) and t.get("name") in HOOK_EVENTS]
        for dev in list(self._devices):
            for h in await self._api("GET", f"/notification/{dev}/webhook") or []:
                if str((h or {}).get("externalId") or "").startswith(tag):
                    await self._api("DELETE", f"/notification/webhook/{h['id']}")
            await self._api("POST", "/notification/webhook", {
                "device": {"id": dev}, "externalId": tag, "url": base + self._hook_path(dev), "eventTypes": wanted})
        self._webhooks = True

    def admin_details(self) -> dict[str, Any]:
        if not self._webhooks:
            return {}
        return {"addresses": [{"name": self.hub.name(eid), "entityId": eid, "path": self._hook_path(dev)}
                              for dev, eid in self._devices.items()]}

    async def receive_webhook(self, entity_id: str, secret: str, body: bytes, content_type: str) -> bool:
        dev = next((d for d, e in self._devices.items() if e == entity_id), None)
        if not dev or not self._hook_secret or not secrets.compare_digest(self._hook_secret.encode(), secret.encode()):
            return False
        try:
            msg = json.loads(body or b"{}")
        except ValueError:
            msg = {}
        sub = str(msg.get("subType") or "")
        zone_id = str(msg.get("zoneId") or "")
        if sub in ZONE_ON and zone_id in self._zones and isinstance(msg.get("duration"), (int, float)):
            await self._show(dev, zone_id, time.time() + float(msg["duration"]))  # no request needed
        elif sub in ZONE_OFF and zone_id in self._zones:
            await self._show(dev, "", None)
        else:  # rain delay, standby, schedule… read the controller again
            self._tasks.spawn(self._reload_device(dev))
        return True

    async def _reload_device(self, dev: str) -> None:
        try:
            d = await self._api("GET", f"/device/{dev}")
            if d:
                await self._apply_device(d)
            await self.refresh(dev)
        except Exception as e:
            log.info("Rachio: %s", e)

    # -- commands -------------------------------------------------------------------------

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        zone = next(((zid, d) for zid, (e, d) in self._zones.items() if e == entity_id), None)
        if zone:
            zid, dev = zone
            if command == "run":
                minutes = command_number("sprinkler_zone", "run", value)
                await self._api("PUT", "/zone/start", {"id": zid, "duration": minutes * 60})
                await self._show(dev, zid, time.time() + minutes * 60)
            elif command == "stop":  # Rachio stops whatever is watering on the controller
                await self._api("PUT", "/device/stop_water", {"id": dev})
                await self._show(dev, "", None)
            else:
                raise CommandError(f"A sprinkler zone can't {command.replace('_', ' ')}")
            return
        dev = next((d for d, e in self._devices.items() if e == entity_id), None)
        if not dev:
            raise CommandError("That Rachio controller isn't connected")
        if command == "stop":
            await self._api("PUT", "/device/stop_water", {"id": dev})
            await self._show(dev, "", None)
            return
        if command == "rain_delay":
            days = command_number("sprinkler", "rain_delay", value)
            if days > MAX_DELAY_DAYS:
                raise CommandError(f"Rachio allows a rain delay of up to {MAX_DELAY_DAYS} days")
            await self._api("PUT", "/device/rain_delay", {"id": dev, "duration": days * 86400})
        elif command in ("turn_off", "turn_on"):
            await self._api("PUT", f"/device/{'off' if command == 'turn_off' else 'on'}", {"id": dev})
        else:
            raise CommandError(f"A sprinkler controller can't {command.replace('_', ' ')}")
        await self._reload_device(dev)
