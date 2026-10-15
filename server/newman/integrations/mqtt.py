"""Anything that announces itself with Home Assistant's MQTT discovery protocol: ring-mqtt,
Zigbee2MQTT, Z-Wave JS UI, Tasmota, ESPHome (MQTT mode), Shelly (MQTT mode), ratgdo…

Discovery configs give each entity's name, kind and its state / command / availability topics,
so there's nothing to configure per device. Components understood: lock, alarm_control_panel,
binary_sensor, sensor, switch, light (default and JSON schema), cover (garage doors become Newman
garage doors), fan, siren, button.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any

import aiomqtt

from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

COMPONENTS = ("lock", "alarm_control_panel", "binary_sensor", "sensor", "switch", "light", "cover", "fan",
              "siren", "button")
CONTACT_CLASSES = {"door", "window", "opening", "garage_door"}
MOTION_CLASSES = {"motion", "occupancy", "presence"}
LEAK_CLASSES = {"moisture"}
SMOKE_CLASSES = {"smoke", "gas", "carbon_monoxide"}

# Default HA MQTT command payloads per component, overridable per entity by the discovery config.
DEFAULT_PAYLOADS = {
    "lock": {"lock": ("payload_lock", "LOCK"), "unlock": ("payload_unlock", "UNLOCK")},
    "alarm": {"arm_home": ("payload_arm_home", "ARM_HOME"), "arm_away": ("payload_arm_away", "ARM_AWAY"),
              "disarm": ("payload_disarm", "DISARM")},
    "on_off": {"turn_on": ("payload_on", "ON"), "turn_off": ("payload_off", "OFF")},
    "cover": {"open": ("payload_open", "OPEN"), "close": ("payload_close", "CLOSE"), "stop": ("payload_stop", "STOP")},
    "button": {"press": ("payload_press", "PRESS")},
}
# Short keys some devices (Tasmota, ESPHome) use in discovery configs.
ABBREVIATIONS = {"stat_t": "state_topic", "cmd_t": "command_topic", "avty_t": "availability_topic", "avty": "availability",
                 "dev_cla": "device_class", "uniq_id": "unique_id", "dev": "device", "val_tpl": "value_template",
                 "unit_of_meas": "unit_of_measurement", "json_attr_t": "json_attributes_topic", "pl_on": "payload_on",
                 "pl_off": "payload_off", "stat_on": "state_on", "stat_off": "state_off", "pl_avail": "payload_available",
                 "pl_not_avail": "payload_not_available", "bri_cmd_t": "brightness_command_topic",
                 "bri_stat_t": "brightness_state_topic", "bri_scl": "brightness_scale", "pos_t": "position_topic",
                 "set_pos_t": "set_position_topic", "pct_cmd_t": "percentage_command_topic",
                 "pct_stat_t": "percentage_state_topic", "pl_open": "payload_open", "pl_cls": "payload_close",
                 "pl_stop": "payload_stop", "pl_prs": "payload_press", "avty_tpl": "availability_template",
                 "bri_val_tpl": "brightness_value_template", "pct_val_tpl": "percentage_value_template",
                 "pos_tpl": "position_template", "pos_open": "position_open", "pos_clsd": "position_closed",
                 "ent_cat": "entity_category", "o": "origin"}
ATTR_KEYS = ("batteryLevel", "batteryStatus", "tamperStatus", "acStatus", "commStatus", "linkQuality", "battery",
             "linkquality", "power", "energy", "voltage", "current")
# Who-did-it fields a device may report (ring-mqtt's alarm panel), each with its time.
WHO_KEYS = (("lastDisarmedBy", "lastDisarmedTime"), ("lastArmedBy", "lastArmedTime"),
            ("alarmClearedBy", "alarmClearedTime"))
HOLD_FOR_WHO_S = 1.5
ZIGBEE_LEVEL_MAX = 254  # a Zigbee light's Level Control range (brightness_move rates are in these units)

_TEMPLATE = re.compile(r"\{\{\s*(value_json|value)((?:\.\w+|\[\s*['\"][^'\"]+['\"]\s*\]|\[\d+\])*)\s*((?:\|\s*\w+(?:\([^)]*\))?\s*)*)\}\}")


def render(template: str | None, payload: str) -> Any:
    """The common shapes of HA value templates ({{ value }}, {{ value_json.x.y }}, ['x'], | int / float /
    round(n) / lower / upper). Anything fancier: the raw payload (or a JSON "state" field)."""
    if not template:
        return payload
    m = _TEMPLATE.fullmatch(template.strip())
    if not m:
        try:
            data = json.loads(payload)
            return data.get("state", payload) if isinstance(data, dict) else payload
        except ValueError:
            return payload
    root, path, filters = m.groups()
    if root == "value":
        v: Any = payload
    else:
        try:
            v = json.loads(payload)
        except ValueError:
            return None
        for part in re.findall(r"\.(\w+)|\[\s*['\"]([^'\"]+)['\"]\s*\]|\[(\d+)\]", path):
            key = part[0] or part[1]
            try:
                v = v[int(part[2])] if part[2] else v.get(key) if isinstance(v, dict) else None
            except (IndexError, TypeError):
                v = None
            if v is None:
                return None
    for f in re.findall(r"\|\s*(\w+)(?:\(([^)]*)\))?", filters or ""):
        name, arg = f
        try:
            if name == "int":
                v = int(float(v))
            elif name == "float":
                v = float(v)
            elif name == "round":
                v = round(float(v), int(arg or 0))
            elif name == "lower":
                v = str(v).lower()
            elif name == "upper":
                v = str(v).upper()
        except (TypeError, ValueError):
            pass
    return v


def _same(a: Any, b: Any) -> bool:
    return str(a).strip().lower() == str(b).strip().lower()


class MqttIntegration(Integration):
    type = "mqtt"
    name = "MQTT devices"
    category = "other"
    provides = ("lock", "alarm", "contact", "motion", "light", "switch", "cover", "garage", "fan", "sensor", "temperature", "leak", "smoke", "siren", "button")  # kinds of device it can bring in (shown on its catalog card)
    description = ("Anything that announces itself the Home Assistant MQTT way shows up automatically: ring-mqtt, "
                   "Zigbee2MQTT, Z-Wave JS UI, Tasmota, ESPHome, Shelly, ratgdo - plugs, lights, blinds, fans, "
                   "sensors, leak and smoke alarms, locks, alarms, garage doors and buttons.")
    fields = [
        Field("host", "Broker address", required=True, placeholder="10.1.1.2"),
        Field("port", "Port", "number", default=1883),
        Field("username", "Username"),
        Field("password", "Password", "password"),
        Field("birth", "Ask devices to re-send their state on connect", "boolean", default=True,
              help='Publishes "online" to homeassistant/status like Home Assistant does - '
                   "ring-mqtt and others only send current states when they see this."),
        Field("discovery_prefix", "Discovery prefix", default="homeassistant"),
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._client: aiomqtt.Client | None = None
        self._specs: dict[str, dict[str, Any]] = {}          # entity id -> what we know from its discovery config
        self._topics: dict[str, set[tuple[str, str]]] = {}   # topic -> {(entity id, role)}
        self._device_entities: dict[str, set[str]] = {}      # ring device id -> entity ids
        self._last: dict[str, str] = {}                      # topic -> last payload (states can arrive before discovery)
        self._subscribed: set[str] = set()

    @property
    def prefix(self) -> str:
        return self.config.get("discovery_prefix") or "homeassistant"

    async def run(self) -> None:
        attempt = 0
        while True:
            try:
                async with aiomqtt.Client(
                    self.config["host"], int(self.config.get("port") or 1883),
                    username=self.config.get("username") or None, password=self.config.get("password") or None,
                    identifier=f"newman-{self.ctx.install_id}-{self.id}", keepalive=30,
                ) as client:
                    self._client, attempt = client, 0
                    self._subscribed = set()
                    for comp in COMPONENTS:
                        await client.subscribe(f"{self.prefix}/{comp}/#")
                    await client.subscribe("ring/#")
                    for topic in list(self._topics):
                        await self._subscribe(topic)
                    if self.config.get("birth", True):
                        await client.publish(f"{self.prefix}/status", "online")
                    self._update_status()
                    async for msg in client.messages:
                        await self._on_message(msg.topic.value, msg.payload)
            except asyncio.CancelledError:
                raise
            except aiomqtt.MqttError as e:
                self.set_status("error", f"Broker unreachable: {e}")
            except Exception as e:
                log.exception("MQTT loop crashed")
                self.set_status("error", str(e))
            finally:
                self._client = None
            attempt += 1
            await self.backoff_sleep(attempt)

    async def _subscribe(self, topic: str) -> None:
        if self._client and topic not in self._subscribed and not topic.startswith("ring/"):
            self._subscribed.add(topic)
            await self._client.subscribe(topic)

    def _update_status(self) -> None:
        n = len(self._specs)
        self.set_status("connected", f"{self.config['host']} · {n} device{'s' if n != 1 else ''}")

    # -- commands -------------------------------------------------------------------

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        spec = self._spec(entity_id)
        await self._send(entity_id, *self._payload_for(spec, command, value))

    def _spec(self, entity_id: str) -> dict[str, Any]:
        spec = self._specs.get(entity_id)
        if not spec:
            raise CommandError(f"{self.hub.name(entity_id)} isn't an MQTT device Newman knows")
        return spec

    async def _send(self, entity_id: str, topic: str, payload: str) -> None:
        if not self._client:
            raise CommandError("MQTT broker is not connected")
        await self._client.publish(topic, payload)
        log.info("Sent %s to %s", payload, self.hub.name(entity_id))

    # A JSON-schema light always takes a "transition" (seconds), as in Home Assistant.
    @staticmethod
    def _fades(spec: dict[str, Any]) -> bool:
        return spec["component"] == "light" and spec["cfg"].get("schema") == "json"

    @staticmethod
    def _moves(spec: dict[str, Any]) -> bool:
        """A dimmable Zigbee2MQTT light (its discovery's origin says so): Zigbee2MQTT starts the light's own
        Level Control move with "brightness_move" / "brightness_move_onoff" (a rate) and stops it with
        "brightness_move": "stop", then reads the light back and publishes its state."""
        cfg = spec["cfg"]
        origin = cfg.get("origin") if isinstance(cfg.get("origin"), dict) else {}
        return MqttIntegration._fades(spec) and bool(cfg.get("brightness")) and bool(cfg.get("command_topic")) \
            and _same(origin.get("name", ""), "Zigbee2MQTT")

    def can_move(self, entity_id: str, attr: str) -> bool:
        spec = self._specs.get(entity_id)
        return bool(spec) and attr == "brightness" and self._moves(spec)

    async def move(self, entity_id: str, attr: str, direction: int, full_s: float) -> None:
        spec = self._spec(entity_id)
        if not self.can_move(entity_id, attr):
            await super().move(entity_id, attr, direction, full_s)
        # Rate: Zigbee level units (0-254) per second. Up turns an off light on (moveWithOnOff); down is a
        # plain move, which stops at the light's lowest level rather than turning it off.
        rate = max(1, round(ZIGBEE_LEVEL_MAX / max(full_s, 0.1)))
        body = {"brightness_move": "stop"} if direction == 0 else \
            {"brightness_move_onoff": rate} if direction > 0 else {"brightness_move": -rate}
        await self._send(entity_id, spec["cfg"]["command_topic"], json.dumps(body))

    async def command_fading(self, entity_id: str, command: str, value: Any, seconds: float) -> None:
        spec = self._spec(entity_id)
        fade = seconds if self._fades(spec) and command == "set_brightness" else None
        await self._send(entity_id, *self._payload_for(spec, command, value, fade))

    def _payload_for(self, spec: dict[str, Any], command: str, value: Any,
                     transition: float | None = None) -> tuple[str, str]:
        cfg, comp, cmd_t = spec["cfg"], spec["component"], spec["cfg"].get("command_topic")
        if comp == "light" and cfg.get("schema") == "json":
            scale = float(cfg.get("brightness_scale") or 255)
            body: dict[str, Any] = {"state": "OFF" if command == "turn_off" else "ON"}
            if command == "set_brightness":
                body["brightness"] = round(max(1, min(100, float(value))) / 100 * scale)
            if transition:
                body["transition"] = transition
            return cmd_t, json.dumps(body)
        if command == "set_brightness":
            topic = cfg.get("brightness_command_topic") or cmd_t
            scale = float(cfg.get("brightness_scale") or 255)
            return topic, str(round(max(1, min(100, float(value))) / 100 * scale))
        if command == "set_position":
            lo, hi = float(cfg.get("position_closed", 0)), float(cfg.get("position_open", 100))
            return cfg.get("set_position_topic") or cmd_t, str(round(lo + (hi - lo) * float(value) / 100))
        if command == "set_speed":
            lo, hi = float(cfg.get("speed_range_min", 1)), float(cfg.get("speed_range_max", 100))
            return cfg.get("percentage_command_topic") or cmd_t, str(round(lo + (hi - lo) * (float(value) - 1) / 99))
        table = DEFAULT_PAYLOADS.get(spec["payloads"], {})
        if command not in table or not cmd_t:
            raise CommandError(f"{self.hub.name(spec['eid'])} doesn't support {command.replace('_', ' ')}")
        key, default = table[command]
        return cmd_t, str(cfg.get(key, default))

    # -- messages -------------------------------------------------------------------

    async def _on_message(self, topic: str, raw: Any) -> None:
        payload = raw.decode(errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
        if topic.startswith(self.prefix + "/") and topic.endswith("/config"):
            await self._on_discovery(topic, payload)
            return
        self._last[topic] = payload
        await self._apply(topic, payload)

    async def _apply(self, topic: str, payload: str) -> None:
        for eid, role in list(self._topics.get(topic, ())):
            spec, ent = self._specs.get(eid), self.hub.get(eid)
            if not spec or not ent:
                continue
            if role == "state":
                state, attrs = self._parse_state(spec, payload)
                if state is not None and spec["kind"] == "alarm" and spec["cfg"].get("json_attributes_topic") \
                        and state != ent.state:
                    # ring-mqtt sends the new alarm mode a moment before who changed it ("lastDisarmedBy");
                    # hold the change briefly so the automation sees both together.
                    self._hold_state(eid, state, attrs)
                elif state is not None or attrs:
                    await self.hub.update(eid, **({"state": state} if state is not None else {}), attrs=attrs or None)
            elif role == "availability":
                await self.hub.update(eid, available=self._parse_available(spec, payload))
            elif role == "attributes":
                await self._apply_attrs({eid}, payload)
                await self._release_state(eid)
            elif role == "brightness":
                v = render(spec["cfg"].get("brightness_value_template"), payload)
                await self._set_pct(eid, "brightness", v, float(spec["cfg"].get("brightness_scale") or 255))
            elif role == "position":
                v = render(spec["cfg"].get("position_template"), payload)
                await self._set_pct(eid, "position", v, float(spec["cfg"].get("position_open", 100)))
            elif role == "percentage":
                v = render(spec["cfg"].get("percentage_value_template"), payload)
                await self._set_pct(eid, "percentage", v, float(spec["cfg"].get("speed_range_max", 100)))
        parts = topic.split("/")
        # ring-mqtt: ring/<location>/<category>/<device>/info/state carries battery etc.
        if topic.startswith("ring/") and topic.endswith("/info/state") and len(parts) >= 4:
            await self._apply_attrs(self._device_entities.get(parts[3], set()), payload)

    def _hold_state(self, eid: str, state: str, attrs: dict[str, Any] | None) -> None:
        held = getattr(self, "_held", None)
        if held is None:
            held = self._held = {}
        if eid in held:
            held[eid][2].cancel()

        async def later() -> None:
            await asyncio.sleep(HOLD_FOR_WHO_S)
            await self._release_state(eid)
        held[eid] = (state, attrs, asyncio.get_running_loop().create_task(later()))

    async def _release_state(self, eid: str) -> None:
        item = getattr(self, "_held", {}).pop(eid, None)
        if not item:
            return
        state, attrs, task = item
        if task is not asyncio.current_task():
            task.cancel()
        await self.hub.update(eid, state=state, attrs=attrs or None)

    async def _set_pct(self, eid: str, attr: str, v: Any, scale: float) -> None:
        try:
            await self.hub.update(eid, attrs={attr: round(float(v) / scale * 100)})
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    async def _apply_attrs(self, eids: set[str], payload: str) -> None:
        try:
            info = json.loads(payload)
        except ValueError:
            return
        if not isinstance(info, dict):
            return
        attrs = {k: info[k] for k in ATTR_KEYS if k in info and not isinstance(info[k], (dict, list))}
        if "battery" in attrs and "batteryLevel" not in attrs:
            attrs["batteryLevel"] = attrs.pop("battery")
        if "linkquality" in attrs and "linkQuality" not in attrs:
            attrs["linkQuality"] = attrs.pop("linkquality")
        # Who last changed it (ring-mqtt's alarm panel: lastArmedBy / lastDisarmedBy / alarmClearedBy, with
        # times): the most recent one becomes changedBy / changedAt, which notifications use for "by …".
        latest = None
        for who_key, at_key in WHO_KEYS:
            who, at = info.get(who_key), info.get(at_key)
            if not who or str(who).lower() in ("unknown", "none") or not at:
                continue
            try:
                ts = datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            if latest is None or ts > latest[1]:
                latest = (str(who), ts)
        if latest:
            attrs.update(changedBy=latest[0], changedAt=latest[1])
        for eid in eids:
            if attrs:
                await self.hub.update(eid, attrs=attrs)

    def _parse_available(self, spec: dict[str, Any], payload: str) -> bool:
        cfg = spec["cfg"]
        v = render(cfg.get("availability_template"), payload)
        if isinstance(v, str) and v.strip().startswith("{"):
            try:
                v = json.loads(v).get("state", v)
            except ValueError:
                pass
        if _same(v, cfg.get("payload_not_available", "offline")):
            return False
        return _same(v, cfg.get("payload_available", "online")) or _same(v, "true")

    def _parse_state(self, spec: dict[str, Any], payload: str) -> tuple[str | None, dict[str, Any]]:
        cfg, kind, comp = spec["cfg"], spec["kind"], spec["component"]
        attrs: dict[str, Any] = {}
        if comp == "light" and cfg.get("schema") == "json":
            try:
                data = json.loads(payload)
            except ValueError:
                return None, attrs
            if "brightness" in data:
                try:
                    attrs["brightness"] = round(float(data["brightness"]) / float(cfg.get("brightness_scale") or 255) * 100)
                except (TypeError, ValueError):
                    pass
            st = data.get("state")
            return (("on" if _same(st, "ON") else "off") if st is not None else None), attrs
        v = render(cfg.get("value_template"), payload)
        if v is None:
            return None, attrs
        if kind == "lock":
            return str(v).strip().upper(), attrs
        if kind in ("alarm",):
            return str(v).strip().lower(), attrs
        if comp == "sensor":
            return (str(round(v, 2)) if isinstance(v, float) else str(v)), attrs
        if comp == "cover":
            for s, key in (("open", "state_open"), ("closed", "state_closed"), ("opening", "state_opening"),
                           ("closing", "state_closing"), ("stopped", "state_stopped")):
                if _same(v, cfg.get(key, s)):
                    return s, attrs
            return None, attrs
        # Everything else is on/off-ish: binary sensors, switches, lights, fans, sirens.
        on_value = cfg.get("state_on", cfg.get("payload_on", "ON"))
        off_value = cfg.get("state_off", cfg.get("payload_off", "OFF"))
        if _same(v, on_value):
            on = True
        elif _same(v, off_value):
            on = False
        else:
            on = str(v).strip().upper() in ("ON", "OPEN", "TRUE", "1", "DETECTED", "WET")
        return {"contact": ("open", "closed"), "motion": ("detected", "clear"), "leak": ("wet", "dry"),
                "smoke": ("detected", "clear")}.get(kind, ("on", "off"))[0 if on else 1], attrs

    def _kind_for(self, comp: str, cfg: dict[str, Any]) -> tuple[str, str] | None:
        """(Newman kind, payload table) for a discovered entity, or None to ignore it."""
        dc = cfg.get("device_class")
        if comp == "lock":
            return "lock", "lock"
        if comp == "alarm_control_panel":
            return "alarm", "alarm"
        if comp == "binary_sensor":
            if dc in CONTACT_CLASSES:
                return "contact", ""
            if dc in MOTION_CLASSES:
                return "motion", ""
            if dc in LEAK_CLASSES:
                return "leak", ""
            if dc in SMOKE_CLASSES:
                return "smoke", ""
            return "binary", ""
        if comp == "sensor":
            if dc == "temperature" and str(cfg.get("unit_of_measurement", "")).endswith("C"):
                return "temperature", ""
            return "sensor", ""
        if comp == "switch":
            # Some bridges (ring-mqtt's alarm siren) announce a siren as a plain switch.
            looks_siren = "siren" in f"{cfg.get('name', '')} {cfg.get('unique_id', '')}".lower()
            return ("siren" if looks_siren else "switch"), "on_off"
        if comp == "light":
            return "light", "on_off"
        if comp == "cover":
            return ("garage" if dc in ("garage", "gate") else "cover"), "cover"
        if comp == "fan":
            return "fan", "on_off"
        if comp == "siren":
            return "siren", "on_off"
        if comp == "button":
            return "button", "button"
        return None

    async def _on_discovery(self, topic: str, payload: str) -> None:
        parts = topic.split("/")
        if not payload or len(parts) < 3:
            return
        try:
            raw = json.loads(payload)
        except ValueError:
            return
        cfg = {ABBREVIATIONS.get(k, k): v for k, v in raw.items()}
        base = cfg.get("~")
        if base:  # "~" abbreviates a topic prefix
            cfg = {k: (v.replace("~", base) if isinstance(v, str) and k.endswith("topic") else v) for k, v in cfg.items()}
        comp = parts[1]
        if cfg.get("entity_category") in ("diagnostic", "config"):
            return  # signal strength, firmware, settings… - kept as details on the real device, not devices of their own
        found = self._kind_for(comp, cfg)
        state_topic = cfg.get("state_topic")
        if not found or (not state_topic and comp not in ("button",)):
            return
        kind, payloads = found
        dev = cfg.get("device") or {}
        dev_name = dev.get("name") if isinstance(dev, dict) else None
        own = cfg.get("name")
        # One physical device often has several entities ("Kitchen plug" power, energy…): name them apart.
        # The device's main entity keeps the device's name (ring-mqtt's "Lock" on "Side Door" = "Side Door");
        # extra parts of a device are named apart ("Woodbridge Alarm Siren", "Coffee Plug Power").
        extra = kind in ("sensor", "switch", "siren", "button", "binary", "fan")
        name = (f"{dev_name} {own}" if dev_name and own and extra and own.lower() not in dev_name.lower()
                else dev_name or own or "Device")
        uid = cfg.get("unique_id") or state_topic or cfg.get("command_topic")
        eid = self.entity_id(re.sub(r"[^A-Za-z0-9_.-]", "_", str(uid)))
        self.hub.upsert(eid, kind=kind, name=name, integration=self.id)
        spec = {"eid": eid, "component": comp, "kind": kind, "payloads": payloads, "cfg": cfg}
        self._specs[eid] = spec
        static: dict[str, Any] = {}
        if cfg.get("unit_of_measurement") and kind == "sensor":
            static["unitOfMeasurement"] = cfg["unit_of_measurement"]
        if dc := cfg.get("device_class"):
            static["deviceClass"] = dc
        if comp == "light":
            static["dimmable"] = bool(cfg.get("brightness") or cfg.get("brightness_command_topic"))
        if static:
            await self.hub.update(eid, attrs=static)

        roles = [("state", state_topic), ("attributes", cfg.get("json_attributes_topic")),
                 ("brightness", cfg.get("brightness_state_topic")), ("position", cfg.get("position_topic")),
                 ("percentage", cfg.get("percentage_state_topic"))]
        avail = cfg.get("availability_topic")
        avail_topics = [avail] if avail else [a.get("topic") for a in cfg.get("availability") or [] if isinstance(a, dict)]
        roles += [("availability", t) for t in avail_topics]
        for role, t in roles:
            if t:
                self._topics.setdefault(t, set()).add((eid, role))
                await self._subscribe(t)
        if state_topic and state_topic.startswith("ring/") and state_topic.count("/") >= 4:
            self._device_entities.setdefault(state_topic.split("/")[3], set()).add(eid)
        self._update_status()
        for _, t in roles:
            if t and t in self._last:
                await self._apply(t, self._last[t])
