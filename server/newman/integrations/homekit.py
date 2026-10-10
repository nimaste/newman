"""HomeKit accessories, controlled locally (aiohomekit - the library behind
Home Assistant's "HomeKit Device" integration).

A HomeKit accessory pairs with ONE hub at a time. To move one to Newman,
remove it from wherever it's paired (Home Assistant, Apple Home...) - its
network announcement then says it's free - and enter its 8-digit setup code.

Covers thermostats (Ecobee), temperature sensors and occupancy/motion sensors
(e.g. Ecobee room sensors). More service types slot into SERVICES below.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiohomekit import Controller
from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf

from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

# HomeKit short type codes (the first 8 hex digits of Apple's UUIDs).
S_INFO, S_THERMOSTAT, S_TEMPERATURE, S_OCCUPANCY, S_MOTION = 0x3E, 0x4A, 0x8A, 0x86, 0x85
C_NAME, C_TEMP, C_TARGET_TEMP, C_MODE_NOW, C_MODE_TARGET, C_HUMIDITY = 0x23, 0x11, 0x35, 0x0F, 0x33, 0x10
C_OCCUPANCY, C_MOTION, C_COOL_TO, C_HEAT_TO = 0x71, 0x22, 0x0D, 0x12

# The accessory category a HomeKit device announces ("ci" in its network announcement) -> the kinds of
# device Newman can bring in from it. Categories not listed (TVs, speakers, cameras, locks…) have no
# services Newman reads yet, so they aren't offered at all rather than shown as a thermostat.
CATEGORY_PROVIDES = {
    2: ("thermostat", "temperature", "motion"),  # bridge - could hold any of these
    9: ("thermostat", "temperature", "motion"),  # thermostat (Ecobee: it carries its room sensors)
    10: ("temperature", "motion"),               # sensor
}

MODES = {0: "off", 1: "heat", 2: "cool", 3: "auto"}
ACTIONS = {0: "idle", 1: "heating", 2: "cooling"}


def _ignore(**_: Any) -> None:
    pass


def _short(t: Any) -> int:
    s = str(t).upper()
    return int(s.split("-")[0], 16) if s else -1


class HomeKitIntegration(Integration):
    type = "homekit"
    name = "HomeKit device"
    category = "climate"
    provides = ("thermostat", "temperature", "motion")  # kinds of device it can bring in (shown on its catalog card)
    description = ("Controls a HomeKit accessory - e.g. an Ecobee thermostat and its room sensors - directly on your "
                   "network. It must first be removed from any other hub (Home Assistant, Apple Home), then paired "
                   "here with its 8-digit HomeKit code.")
    fields = [
        Field("device_id", "Accessory ID", required=True, help="Filled in automatically when found on your network."),
        Field("name", "Name"),
        Field("code", "HomeKit setup code", "password", required=True, placeholder="123-45-678",
              help="The 8-digit code on the device's label or screen (on an Ecobee: Settings > HomeKit / Reconnect)."),
    ]
    zeroconf = ["_hap._tcp.local."]

    @classmethod
    def from_discovery(cls, name: str, host: str, props: dict[str, str]) -> dict[str, Any] | None:
        model = props.get("md", "")
        if model.upper().startswith(("MSG1", "MSG2")) or "Smart Bridge" in model:
            return None  # has its own native integration (Meross / Lutron)
        try:
            category = int(props.get("ci") or 0)
        except ValueError:
            category = 0
        if category and category not in CATEGORY_PROVIDES:
            return None  # a TV, speaker, camera… - nothing Newman can use through HomeKit
        title = name.split("._hap.")[0]
        free = props.get("sf") == "1"
        return {"title": f"{title} ({model})" if model and model not in title else title,
                "provides": list(CATEGORY_PROVIDES.get(category, cls.provides)),
                "config": {"device_id": props.get("id", ""), "name": title},
                "note": None if free else "Paired to another hub - remove it there first, then set it up here"}

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._dir = self.ctx.data_dir / "integrations" / self.id
        self._azc: AsyncZeroconf | None = None
        self._pairing: Any = None
        self._chars: dict[tuple[int, int], tuple[str, str]] = {}   # (aid, iid) -> (entity id, field)
        self._writable: dict[tuple[str, str], tuple[int, int]] = {}  # (entity id, field) -> (aid, iid)
        self._values: dict[str, dict[str, Any]] = {}

    # -- pairing --------------------------------------------------------------------------

    @property
    def _file(self):
        return self._dir / "pairing.json"

    async def _controller(self) -> Controller:
        if not self._azc:
            self._azc = AsyncZeroconf(ip_version=IPVersion.V4Only)
            # aiohomekit finds accessories through a HomeKit browser that must already be running on the
            # zeroconf instance it's given (Home Assistant sets one up the same way). Without it pairing fails
            # with "There is no zeroconf browser for _hap._tcp.local." - seen live.
            self._browser = AsyncServiceBrowser(self._azc.zeroconf, ["_hap._tcp.local.", "_hap._udp.local."],
                                                handlers=[_ignore])
        controller = Controller(async_zeroconf_instance=self._azc)
        await controller.async_start()
        return controller

    async def _pair(self, controller: Controller) -> dict[str, Any]:
        code = str(self.config.get("code", "")).strip()
        if len(code.replace("-", "")) == 8 and "-" not in code:
            code = f"{code[:3]}-{code[3:5]}-{code[5:]}"
        self.set_status("pairing", "Pairing with the HomeKit code…")
        discovery = await controller.async_find(self.config["device_id"], timeout=30)
        finish = await discovery.async_start_pairing(self.id)
        pairing = await finish(code)
        data = dict(pairing.pairing_data)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(data))
        self._file.chmod(0o600)
        log.info("Paired with HomeKit accessory %s", self.config["device_id"])
        return data

    # -- running ---------------------------------------------------------------------------

    async def run(self) -> None:
        controller = await self._controller()
        try:
            if self._file.is_file():
                data = json.loads(self._file.read_text())
            else:
                try:
                    data = await self._pair(controller)
                except Exception as e:
                    msg = str(e) or e.__class__.__name__
                    if "paired" in msg.lower() or "Unavailable" in e.__class__.__name__:
                        msg = "It's still paired to another hub - remove it there first, then save again"
                    elif "auth" in msg.lower() or "Authentication" in e.__class__.__name__:
                        msg = "Wrong HomeKit code - check it and save again"
                    self.set_status("error", msg)
                    await asyncio.Event().wait()  # wait for new settings (saving restarts the integration)
            self._pairing = controller.load_pairing(self.id, data)
            attempt = 0
            while True:
                try:
                    await self._connect()
                    attempt = 0
                    while True:
                        await asyncio.sleep(60)
                        await self._refresh()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.set_status("error", f"{e.__class__.__name__}: {e}")
                    for eid in {e for e, _ in self._chars.values()}:
                        await self.hub.update(eid, available=False)
                attempt += 1
                await self.backoff_sleep(attempt + 1)
        finally:
            try:
                await controller.async_stop()
            finally:
                if getattr(self, "_browser", None):
                    await self._browser.async_cancel()
                if self._azc:
                    await self._azc.async_close()

    async def _connect(self) -> None:
        self.set_status("connecting", self.config.get("name") or self.config["device_id"])
        accessories = await self._pairing.list_accessories_and_characteristics()
        self._chars.clear()
        self._writable.clear()
        created = 0
        for acc in accessories:
            aid = acc["aid"]
            services = {(_short(s["type"]), s["iid"]): s for s in acc.get("services", [])}
            acc_name = next((c.get("value") for (t, _), s in services.items() if t == S_INFO
                             for c in s.get("characteristics", []) if _short(c["type"]) == C_NAME), None)
            acc_name = acc_name or self.config.get("name") or "HomeKit"
            # Everything the accessory offers, used or not - so we can see what else it could report.
            try:
                log.info("HomeKit %s (aid %s) offers: %s", acc_name, aid, "; ".join(
                    f"{stype}[{','.join(sorted(str(_short(c['type'])) for c in svc.get('characteristics', [])))}]"
                    for (stype, _), svc in sorted(services.items(), key=lambda kv: str(kv[0]))))
            except Exception:  # only a diagnostic: never let it stop the connection
                log.debug("Couldn't list HomeKit services", exc_info=True)
            for (stype, siid), svc in services.items():
                chars = {_short(c["type"]): c for c in svc.get("characteristics", [])}
                if stype == S_THERMOSTAT:
                    eid, kind, fields = self.entity_id(f"thermostat_{aid}"), "thermostat", {
                        C_TEMP: "currentTemp", C_TARGET_TEMP: "targetTemp", C_MODE_NOW: "action",
                        C_MODE_TARGET: "mode", C_HUMIDITY: "humidity", C_COOL_TO: "coolTo", C_HEAT_TO: "heatTo"}
                    name = acc_name
                elif stype == S_TEMPERATURE:
                    eid, kind, fields = self.entity_id(f"temp_{aid}_{siid}"), "temperature", {C_TEMP: "temperature"}
                    name = acc_name if not any(t == S_THERMOSTAT for t, _ in services) else f"{acc_name} temperature"
                elif stype == S_MOTION and any(t == S_OCCUPANCY for t, _ in services):
                    continue  # ecobee sensors expose both; occupancy (it holds while the room is in use) is the useful one
                elif stype in (S_OCCUPANCY, S_MOTION):
                    eid, kind, fields = self.entity_id(f"occupancy_{aid}_{siid}"), "motion", {
                        C_OCCUPANCY: "detected", C_MOTION: "detected"}
                    name = f"{acc_name} occupancy"
                else:
                    continue
                self.hub.upsert(eid, kind=kind, name=name, integration=self.id)
                created += 1
                vals = self._values.setdefault(eid, {})
                for ctype, field in fields.items():
                    if c := chars.get(ctype):
                        self._chars[(aid, c["iid"])] = (eid, field)
                        vals[field] = c.get("value")
                        if "pw" in c.get("perms", []):
                            self._writable[(eid, field)] = (aid, c["iid"])
                await self._publish(eid, kind)
        await self._pairing.subscribe(list(self._chars))
        self._pairing.dispatcher_connect(self._on_event)
        self.set_status("connected", f"{self.config.get('name') or 'HomeKit'} · {created} device{'s' if created != 1 else ''}")

    async def _refresh(self) -> None:
        if not self._chars:
            return
        result = await self._pairing.get_characteristics(list(self._chars))
        self._on_event(result)

    def _on_event(self, data: dict[tuple[int, int], dict[str, Any]]) -> None:
        touched = set()
        for key, payload in data.items():
            if key in self._chars and "value" in payload:
                eid, field = self._chars[key]
                self._values.setdefault(eid, {})[field] = payload["value"]
                touched.add(eid)
        for eid in touched:
            ent = self.hub.get(eid)
            if ent:
                asyncio.get_running_loop().create_task(self._publish(eid, ent.kind))

    async def _publish(self, eid: str, kind: str) -> None:
        v = self._values.get(eid, {})
        if kind == "thermostat":
            attrs = {"currentTemp": v.get("currentTemp"), "targetTemp": v.get("targetTemp"),
                     "humidity": v.get("humidity"), "action": ACTIONS.get(v.get("action")),
                     "coolTo": v.get("coolTo"), "heatTo": v.get("heatTo"), "unit": "°C"}
            await self.hub.update(eid, available=True, state=MODES.get(v.get("mode")), attrs=attrs)
        elif kind == "temperature":
            t = v.get("temperature")
            await self.hub.update(eid, available=True, state=f"{t:.1f}" if isinstance(t, (int, float)) else None)
        elif kind == "motion":
            d = v.get("detected")
            await self.hub.update(eid, available=True, state=None if d is None else ("detected" if d else "clear"))

    # -- commands ---------------------------------------------------------------------------

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        if not self._pairing:
            raise CommandError("The HomeKit device isn't connected")
        if command == "set_mode":
            modes = {v: k for k, v in MODES.items()}
            if value not in modes:
                raise CommandError(f"Mode must be one of {', '.join(modes)}")
            target, raw = (entity_id, "mode"), modes[value]
        elif command == "set_temperature":
            target, raw = (entity_id, "targetTemp"), round(float(value) * 2) / 2
        else:
            raise CommandError(f"This device can't {command.replace('_', ' ')}")
        if target not in self._writable:
            raise CommandError("That setting isn't writable on this device")
        aid, iid = self._writable[target]
        await self._pairing.put_characteristics([(aid, iid, raw)])
        self._values.setdefault(entity_id, {})[target[1]] = raw
        ent = self.hub.get(entity_id)
        await self._publish(entity_id, ent.kind if ent else "thermostat")
