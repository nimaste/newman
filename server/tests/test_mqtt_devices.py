"""MQTT discovery as real devices announce themselves: Zigbee2MQTT, Tasmota (short keys), ratgdo."""
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.integrations.base import Context  # noqa: E402
from newman.integrations.mqtt import MqttIntegration, render  # noqa: E402
from newman.kinds import properties_for  # noqa: E402
from newman.state import Hub  # noqa: E402

Z2M_AVAIL = [{"topic": "zigbee2mqtt/bridge/state", "value_template": "{{ value_json.state }}"}]


def z2m(component, object_id, device, **cfg):
    body = {"state_topic": f"zigbee2mqtt/{device}", "json_attributes_topic": f"zigbee2mqtt/{device}",
            "unique_id": f"0x00158d0001_{object_id}_zigbee2mqtt", "availability": Z2M_AVAIL,
            "device": {"name": device, "identifiers": [f"zigbee2mqtt_{device}"]}, **cfg}
    return f"homeassistant/{component}/0x00158d0001/{object_id}/config", json.dumps(body)


class FakeClient:
    def __init__(self):
        self.published, self.subscribed = [], []

    async def publish(self, topic, payload):
        self.published.append((topic, payload))

    async def subscribe(self, topic):
        self.subscribed.append(topic)


class MqttDeviceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        self.m = MqttIntegration("mqtt", "MQTT devices", {"host": "fake"},
                                 Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        self.m._client = FakeClient()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def msg(self, topic, payload):
        await self.m._on_message(topic, payload.encode() if isinstance(payload, str) else payload)

    def test_templates(self):
        self.assertEqual(render("{{ value_json.state }}", '{"state":"ON"}'), "ON")
        self.assertEqual(render("{{ value_json['sensor']['temp'] | round(1) }}", '{"sensor":{"temp":21.46}}'), 21.5)
        self.assertEqual(render("{{value_json.POWER}}", '{"POWER":"OFF"}'), "OFF")
        self.assertEqual(render("{{ value | int }}", "42.7"), 42)

    async def test_zigbee2mqtt_plug_light_and_sensors(self):
        await self.msg(*z2m("switch", "switch", "Coffee Plug", value_template="{{ value_json.state }}",
                            payload_on="ON", payload_off="OFF", command_topic="zigbee2mqtt/Coffee Plug/set"))
        await self.msg(*z2m("sensor", "power", "Coffee Plug", value_template="{{ value_json.power }}",
                            unit_of_measurement="W", device_class="power", name="Power"))
        await self.msg(*z2m("sensor", "linkquality", "Coffee Plug", value_template="{{ value_json.linkquality }}",
                            entity_category="diagnostic"))
        await self.msg(*z2m("light", "light", "Hall Bulb", schema="json", brightness=True, brightness_scale=254,
                            command_topic="zigbee2mqtt/Hall Bulb/set"))
        await self.msg(*z2m("binary_sensor", "contact", "Back Gate", value_template="{{ value_json.contact }}",
                            payload_on=False, payload_off=True, device_class="door"))
        await self.msg(*z2m("binary_sensor", "water_leak", "Under Sink", value_template="{{ value_json.water_leak }}",
                            payload_on=True, payload_off=False, device_class="moisture"))
        await self.msg(*z2m("sensor", "temperature", "Garage Climate", value_template="{{ value_json.temperature }}",
                            unit_of_measurement="°C", device_class="temperature"))
        await self.msg(*z2m("sensor", "humidity", "Garage Climate", value_template="{{ value_json.humidity }}",
                            unit_of_measurement="%", device_class="humidity", name="Humidity"))
        # States arrive on each device's own topic.
        await self.msg("zigbee2mqtt/bridge/state", '{"state":"online"}')
        await self.msg("zigbee2mqtt/Coffee Plug", '{"state":"ON","power":1180.5,"linkquality":120}')
        await self.msg("zigbee2mqtt/Hall Bulb", '{"state":"ON","brightness":127}')
        await self.msg("zigbee2mqtt/Back Gate", '{"contact":false,"battery":91}')
        await self.msg("zigbee2mqtt/Under Sink", '{"water_leak":true,"battery":77}')
        await self.msg("zigbee2mqtt/Garage Climate", '{"temperature":11.3,"humidity":64}')
        by_name = {e.name: e for e in self.hub.all()}
        self.assertNotIn("Coffee Plug linkquality", by_name)  # diagnostic: not a device of its own
        plug, bulb = by_name["Coffee Plug"], by_name["Hall Bulb"]
        self.assertEqual((plug.kind, plug.state, plug.available), ("switch", "on", True))
        self.assertEqual((bulb.kind, bulb.state, bulb.attrs["brightness"], bulb.attrs["dimmable"]), ("light", "on", 50, True))
        self.assertEqual(by_name["Back Gate"].state, "open")  # Zigbee2MQTT: contact=false means open
        self.assertEqual((by_name["Under Sink"].kind, by_name["Under Sink"].state), ("leak", "wet"))
        self.assertEqual(by_name["Under Sink"].attrs["batteryLevel"], 77)
        self.assertEqual((by_name["Garage Climate"].kind, by_name["Garage Climate"].state), ("temperature", "11.3"))
        hum = by_name["Garage Climate Humidity"]
        props = {p["id"]: p for p in properties_for(hum)}
        self.assertEqual((hum.kind, props["value"]["value"], props["value"]["unit"], props["value"]["label"]),
                         ("sensor", "64", "%", "Humidity"))
        self.assertEqual(by_name["Coffee Plug Power"].state, "1180.5")
        self.assertIn("zigbee2mqtt/Coffee Plug", self.m._client.subscribed)
        # Commands in each device's own format.
        await self.m.command(plug.id, "turn_off")
        await self.m.command(bulb.id, "set_brightness", 50)
        self.assertEqual(self.m._client.published[-2:], [("zigbee2mqtt/Coffee Plug/set", "OFF"),
                                                         ("zigbee2mqtt/Hall Bulb/set", '{"state": "ON", "brightness": 126}')])
        await self.msg("zigbee2mqtt/bridge/state", '{"state":"offline"}')
        self.assertFalse(self.hub.get(plug.id).available)

    async def test_tasmota_short_keys_and_ratgdo_garage(self):
        await self.msg("homeassistant/switch/A1B2C3_RL_1/config", json.dumps({
            "name": "Porch Relay", "stat_t": "tele/porch/STATE", "val_tpl": "{{value_json.POWER}}", "pl_on": "ON",
            "pl_off": "OFF", "cmd_t": "cmnd/porch/POWER", "avty_t": "tele/porch/LWT", "pl_avail": "Online",
            "pl_not_avail": "Offline", "uniq_id": "A1B2C3_RL_1", "dev": {"name": "Porch Relay"}}))
        await self.msg("tele/porch/LWT", "Online")
        await self.msg("tele/porch/STATE", '{"POWER":"ON"}')
        relay = next(e for e in self.hub.all() if e.name == "Porch Relay")
        self.assertEqual((relay.state, relay.available), ("on", True))
        await self.msg("homeassistant/cover/ratgdo/door/config", json.dumps({
            "name": "Garage Door", "device_class": "garage", "state_topic": "ratgdo/status/door",
            "command_topic": "ratgdo/command/door", "payload_open": "open", "payload_close": "close",
            "payload_stop": "stop", "unique_id": "ratgdo_door", "device": {"name": "Garage Door"}}))
        await self.msg("ratgdo/status/door", "closed")
        door = next(e for e in self.hub.all() if e.name == "Garage Door")
        self.assertEqual((door.kind, door.state), ("garage", "closed"))
        await self.m.command(door.id, "open")
        self.assertEqual(self.m._client.published[-1], ("ratgdo/command/door", "open"))
