"""What each kind of device is, can report and can do.

This table is the single source of truth the automation engine, the plain-
English describer and every client UI render from, so adding a new kind of
device (a light, a garage door) starts here - no screen needs hand-building.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

CATEGORIES = [
    ("security", "Security"),
    ("cameras", "Cameras"),
    ("garage", "Garage"),
    ("lighting", "Lighting"),
    ("climate", "Climate"),
    ("cleaning", "Cleaning"),
    ("appliances", "Appliances"),
    ("servers", "Servers & network"),
    ("other", "Other"),
]


# Objects a camera system can detect (Frigate's common labels). A camera/zone entity's attrs["events"]
# lists the ones it actually tracks.
DETECTABLE = ["person", "car", "dog", "cat", "animal", "bird", "horse", "bicycle", "motorcycle", "package", "face",
              "license_plate", "motion"]


@dataclass(frozen=True)
class Command:
    id: str
    label: str        # "Lock"
    past: str         # "locked" - used in follow-ups ("Chris locked Side Door")
    icon: str
    confirms_to: str  # state that proves the command worked
    sensitive: bool = False  # needs Face ID / fingerprint on a phone
    takes_value: bool = False  # e.g. brightness 0-100; not offered as a notification button
    # What the value is, so clients pick the right control without knowing the command:
    # "percent" (0-100 slider) | "number" (stepper, min/max/step/unit) | "state" (one of the kind's
    # states, e.g. a thermostat mode) | "choices" (pick any of the device's attrs[attr] list of
    # {"id", "name"} - e.g. a vacuum's rooms; the value is the list of chosen ids) | "text".
    value_type: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str = ""
    default: float | str | None = None
    attr: str = ""  # the device attribute holding the current value (e.g. "brightness")

    def to_json(self) -> dict:
        return {"id": self.id, "label": self.label, "past": self.past, "icon": self.icon,
                "confirmsTo": self.confirms_to, "sensitive": self.sensitive, "takesValue": self.takes_value,
                "valueType": self.value_type or ("text" if self.takes_value else ""), "min": self.min,
                "max": self.max, "step": self.step, "unit": self.unit, "default": self.default, "attr": self.attr}


@dataclass(frozen=True)
class Event:
    """Something a device reports that can start an automation. The wording lives here, with the
    kind, so the describer and every client stay generic. Templates may use {device} (its name),
    {device_noun} ("Front Door doorbell" - the name plus the kind's noun unless it already ends
    with it), {what} ("Person") and {a_what} ("a person")."""
    id: str
    label: str        # in pickers: "Person detected"
    phrase: str = ""  # in the sentence, after "When": "{a_what} is detected on {device}"
    title: str = ""   # notification title (empty = the automation's title)
    body: str = ""    # notification body
    sound: str = ""   # "doorbell" = the distinct doorbell alert
    what: str = ""    # the object for {what}/{a_what} (defaults to the id)

    def to_json(self) -> dict:
        return {"id": self.id, "label": self.label, "phrase": self.phrase, "title": self.title,
                "body": self.body, "sound": self.sound}


def detection_events(verb: str, phrase: str, title: str, body: str) -> list[Event]:
    """One event per object a camera system can detect."""
    out = []
    for obj in DETECTABLE:
        what = obj.replace("_", " ")
        if obj == "motion":
            out.append(Event(obj, "Motion detected", "{device} detects motion", "Motion at {device}", "Motion detected"))
        else:
            out.append(Event(obj, f"{what.capitalize()} {verb}", phrase, title, body, what=what))
    return out


# Descriptive details integrations report about a device. They never change in a way worth
# automating on, so they aren't offered as properties.
INFO_ATTRS = {"model", "firmware", "host", "channel", "source", "area", "manufacturer", "serial", "dimmable",
              "camera", "events", "zones", "streams", "lastPress", "commandFailures", "name", "unit",
              "isDay", "sunrise", "sunset", "updated", "source", "colour", "now", "localConnection", "protocol",
              "unitOfMeasurement", "deviceClass", "units", "applianceType", "programs", "image", "startedAt", "containerId",
              "ownerId", "askWho", "eveningBefore", "lead", "icon", "history", "snoozedUntil", "runtimeLimit",
              "headsUp", "needs", "changedBy", "changedAt", "whoLookup", "resumesAt",
              "changedHow", "changedHowAt", "changedByNoOne", "kernel", "mount", "fsType", "device", "diskType", "vmType", "vmid", "node", "lastParityCheck",
              "containersTotal", "cover", "batteryType", "backgroundReports", "batteryStale",
              "nextReportIn", "nextReportAt", "dozeExempt"}


@dataclass(frozen=True)
class Property:
    """A value a device reports (temperature, humidity, battery, "heating"…) that automations can
    use: "When Main Floor's humidity goes above 60%". A kind declares the ones it knows so they get a
    nice name and unit; every other simple value a device reports is offered too (see
    properties_for). `attr` "" = the device's state itself; "counts.*" = one per key of a dict."""
    id: str
    label: str        # "Humidity"; for "x.*" attrs, "{sub}" is the key ("{sub} count")
    unit: str = ""    # "%", "°C"
    attr: str = ""
    type: str = ""    # "number" | "boolean" | "text"; "" = work it out from the value
    options: tuple = ()  # known values of a text property: (("heating", "Heating"), ...)

    def to_json(self) -> dict:
        return {"id": self.id, "label": self.label, "unit": self.unit, "attr": self.attr, "type": self.type,
                "options": [{"value": v, "label": lab} for v, lab in self.options]}


BATTERY = Property("battery", "Battery", "%", "batteryLevel", "number")

@dataclass(frozen=True)
class Kind:
    id: str
    name: str
    plural: str
    category: str
    states: list[tuple[str, str]]
    # The state a condition means by "is on" - unlocked, open, motion, disarmed.
    active_state: str | None = None
    commands: list[Command] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    # Has a camera: its events can carry a snapshot.
    has_camera: bool = False
    # Word used in sentences: "if any of X is <active label>".
    noun: str = "device"
    # Momentary states (a lock motor running) that never count as a change:
    # ring-mqtt reports UNLOCKING the instant a command is sent, even if the
    # lock then refuses and stays LOCKED.
    transitional: tuple[str, ...] = ()
    unit: str = ""  # measurement kinds: the state is a number in this unit
    # The state that means "all good" (locked, closed): anything else is worth a look and counts
    # against "All secure". None = this kind has no secure/insecure notion (lights, motion…).
    secure_state: str | None = None
    # A house-mode device (an alarm): shown as the home's mode rather than as one more sensor.
    is_mode: bool = False
    # Away from its secure state means trouble right now (a leak, smoke) - not just "open", like a door.
    urgent: bool = False
    properties: list[Property] = field(default_factory=list)
    # Voice: other words people use for one of these ("outside" for the weather), and the property
    # ids read out when someone asks about one ("what's the weather"); default: its first two.
    spoken: tuple[str, ...] = ()
    summary: tuple[str, ...] = ()
    # Shown instead of the state's label while an attr has a value: (attr, value, label) - a wall
    # tablet with its screen off shows "Screen off" (its state stays charging / on battery).
    notes: tuple[tuple[str, Any, str], ...] = ()

    def label_for(self, state: str | None, attrs: dict | None) -> str:
        """The device's label: a note that applies (in order), else its state's label."""
        for attr, value, label in self.notes:
            if (attrs or {}).get(attr) == value:
                return label
        return self.state_label(state)

    def state_label(self, state: str | None) -> str:
        if state is None:
            return "Unknown"
        if self.unit:
            return f"{state} {self.unit}"
        return dict(self.states).get(state, state.replace("_", " ").capitalize())

    def command(self, command_id: str) -> Command | None:
        return next((c for c in self.commands if c.id == command_id), None)



    def event(self, event_id: str) -> Event:
        return next((e for e in self.events if e.id == event_id), None) or \
            Event(event_id, event_id.replace("_", " ").capitalize())

    def to_json(self) -> dict:
        return {
            "id": self.id, "name": self.name, "plural": self.plural, "category": self.category, "noun": self.noun,
            "states": [{"id": s, "label": label} for s, label in self.states],
            "activeState": self.active_state,
            "commands": [c.to_json() for c in self.commands],
            "events": [e.to_json() for e in self.events], "hasCamera": self.has_camera,
            "transitional": list(self.transitional), "unit": self.unit,
            "secureState": self.secure_state, "isMode": self.is_mode, "urgent": self.urgent,
            "properties": [p.to_json() for p in self.properties],
            "spoken": list(self.spoken), "summary": list(self.summary),
            "notes": [{"attr": a, "value": v, "label": lab} for a, v, lab in self.notes],
        }


KINDS: dict[str, Kind] = {k.id: k for k in [
    Kind("lock", "Lock", "Locks", "security",
         [("LOCKED", "Locked"), ("UNLOCKED", "Unlocked"), ("JAMMED", "Jammed"),
          ("LOCKING", "Locking"), ("UNLOCKING", "Unlocking")],
         active_state="UNLOCKED", noun="lock", transitional=("LOCKING", "UNLOCKING"), secure_state="LOCKED", properties=[BATTERY],
         commands=[Command("lock", "Lock", "locked", "lock", "LOCKED"),
                   Command("unlock", "Unlock", "unlocked", "unlock", "UNLOCKED", sensitive=True)]),
    Kind("alarm", "Alarm", "Alarms", "security",
         [("disarmed", "Disarmed"), ("armed_home", "Armed (home)"), ("armed_away", "Armed (away)"),
          ("arming", "Arming"), ("pending", "Entry delay"), ("triggered", "Triggered")],
         active_state="disarmed", noun="alarm", transitional=("arming",), is_mode=True,
         commands=[Command("arm_home", "Arm (home)", "armed", "shield", "armed_home"),
                   Command("arm_away", "Arm (away)", "armed", "shield", "armed_away"),
                   Command("disarm", "Disarm", "disarmed", "unlock", "disarmed", sensitive=True)]),
    Kind("contact", "Door sensor", "Doors & windows", "security",
         [("open", "Open"), ("closed", "Closed")], active_state="open", noun="door", secure_state="closed", properties=[BATTERY]),
    Kind("motion", "Motion sensor", "Motion", "security",
         [("detected", "Motion"), ("clear", "Clear")], active_state="detected", noun="sensor", properties=[BATTERY]),
    # Doorbells: the button press, plus whatever the doorbell's camera detects (attrs["events"] lists them).
    Kind("doorbell", "Doorbell", "Doorbells", "security",
         [("idle", "Idle"), ("pressed", "Pressed")], noun="doorbell", has_camera=True,
         events=[Event("pressed", "Rings (button pressed)", "someone rings the {device_noun}", "Doorbell",
                       "Someone is at the {device}", sound="doorbell")]
         + detection_events("detected", "{a_what} is detected on {device}", "{what} at {device}", "{what} detected")),
    Kind("light", "Light", "Lights", "lighting",
         [("on", "On"), ("off", "Off")], active_state="on", noun="light",
         properties=[Property("brightness", "Brightness", "%", "brightness", "number")],
         commands=[Command("turn_on", "Turn on", "turned on", "bulb", "on"),
                   Command("turn_off", "Turn off", "turned off", "bulb-off", "off"),
                   Command("set_brightness", "Set brightness", "dimmed", "sun", "on", takes_value=True,
                           value_type="percent", min=1, max=100, step=1, unit="%", default=50, attr="brightness")]),
    Kind("garage", "Garage door", "Garage doors", "garage",
         [("open", "Open"), ("closed", "Closed"), ("opening", "Opening"), ("closing", "Closing"),
          ("stopped", "Stopped")],
         active_state="open", noun="garage door", transitional=("opening", "closing"), secure_state="closed",
         commands=[Command("close", "Close", "closed", "garage", "closed"),
                   Command("open", "Open", "opened", "garage", "open", sensitive=True)]),
    Kind("thermostat", "Thermostat", "Thermostats", "climate",
         [("off", "Off"), ("heat", "Heat"), ("cool", "Cool"), ("auto", "Auto")], noun="thermostat",
         properties=[Property("temperature", "Temperature", "°C", "currentTemp", "number"),
                     Property("humidity", "Humidity", "%", "humidity", "number"),
                     Property("target", "Set temperature", "°C", "targetTemp", "number"),
                     Property("coolTo", "Cool to", "°C", "coolTo", "number"),
                     Property("heatTo", "Heat to", "°C", "heatTo", "number"),
                     Property("action", "Activity", "", "action", "text",
                              (("idle", "Idle"), ("heating", "Heating"), ("cooling", "Cooling")))],
         commands=[Command("set_mode", "Set mode", "switched", "thermometer", "", takes_value=True,
                           value_type="state", default="heat"),
                   Command("set_temperature", "Set temperature", "set", "thermometer", "", takes_value=True,
                           value_type="number", min=5, max=35, step=0.5, unit="°C", default=21, attr="targetTemp")],
         summary=("temperature", "target", "action")),
    # State is the reading itself (e.g. "21.5"); see Kind.unit. A sensor that measures more reports the rest
    # as attrs (humidity; others with their unit in attrs.units) and its battery as attrs.batteryLevel.
    Kind("temperature", "Temperature sensor", "Temperature", "climate", [], noun="sensor", unit="°C",
         properties=[Property("temperature", "Temperature", "°C", "", "number"),
                     Property("humidity", "Humidity", "%", "humidity", "number"), BATTERY],
         summary=("humidity",)),
    # Cameras (Frigate): events are object detections; attrs carry live counts, zones and stream names.
    Kind("camera", "Camera", "Cameras", "cameras",
         [("online", "Online"), ("offline", "Offline")], noun="camera", has_camera=True,
         properties=[Property("count", "{sub} count", "", "counts.*", "number")],
         events=detection_events("detected", "{a_what} is detected on {device}", "{what} at {device}", "{what} detected")),
    # A camera zone: occupied while any tracked object is in it; attrs.counts = {"person": 2, ...}.
    Kind("zone", "Camera zone", "Camera zones", "cameras",
         [("occupied", "Occupied"), ("clear", "Clear")], active_state="occupied", noun="zone", has_camera=True,
         properties=[Property("count", "{sub} count", "", "counts.*", "number")],
         events=detection_events("comes in", "{a_what} enters {device}", "{what} in {device}", "{what} entered {device}")),
    # A remote (e.g. a Lutron Pico): one device, one event per button. An entity's
    # attrs["events"] lists which of these its model actually has.
    Kind("remote", "Remote", "Remotes", "lighting",
         [("idle", "Idle")], noun="remote",
         events=[Event(b, f"“{name}” is pressed", f"the {name} button on {{device}} is pressed", "",
                       f"{name} pressed on {{device}}")
                 for b, name in (("on", "On"), ("favorite", "Favorite"), ("off", "Off"), ("raise", "Raise"),
                                 ("lower", "Lower"), ("button_1", "Button 1"), ("button_2", "Button 2"),
                                 ("button_3", "Button 3"), ("button_4", "Button 4"))]),
    # Weather for a place: the state is the condition now; readings are properties; attrs.forecast
    # (daily), attrs.hourly and attrs.alerts carry the rest for the weather tile.
    Kind("weather", "Weather", "Weather", "climate",
         [("clear", "Clear"), ("mostly-clear", "Mostly clear"), ("partly-cloudy", "Partly cloudy"),
          ("cloudy", "Cloudy"), ("fog", "Fog"), ("drizzle", "Drizzle"), ("rain", "Rain"),
          ("freezing-rain", "Freezing rain"), ("snow", "Snow"), ("showers", "Showers"),
          ("snow-showers", "Snow showers"), ("thunderstorm", "Thunderstorm")], noun="weather",
         events=[Event("alert", "A weather alert is issued", "a weather alert is issued for {device}", "Weather alert",
                       "New weather alert for {device}"),
                 Event("warning", "A weather warning is issued", "a weather warning is issued for {device}",
                       "Weather warning", "A weather warning was issued for {device}")],
         properties=[Property("temperature", "Temperature", "°C", "temperature", "number"),
                     Property("feelsLike", "Feels like", "°C", "feelsLike", "number"),
                     Property("humidity", "Humidity", "%", "humidity", "number"),
                     Property("wind", "Wind", "km/h", "wind", "number"),
                     Property("high", "High today", "°C", "high", "number"),
                     Property("low", "Low today", "°C", "low", "number"),
                     Property("precipChance", "Chance of rain or snow today", "%", "precipChance", "number"),
                     Property("rainToday", "Rain today", "mm", "rainToday", "number"),
                     Property("snowToday", "Snow today", "cm", "snowToday", "number"),
                     Property("gusts", "Wind gusts", "km/h", "gusts", "number"),
                     Property("uv", "UV index today", "", "uv", "number")],
         spoken=("outside", "outdoors", "weather", "forecast"), summary=("temperature", "high", "low", "precipChance")),
    # Plugs and relays.
    Kind("switch", "Switch", "Switches & plugs", "lighting", [("on", "On"), ("off", "Off")], active_state="on",
         noun="switch", commands=[Command("turn_on", "Turn on", "turned on", "power", "on"),
                                  Command("turn_off", "Turn off", "turned off", "power", "off")],
         properties=[Property("power", "Power", "W", "power", "number"), Property("energy", "Energy", "kWh", "energy", "number")]),
    Kind("fan", "Fan", "Fans", "climate", [("on", "On"), ("off", "Off")], active_state="on", noun="fan",
         commands=[Command("turn_on", "Turn on", "turned on", "fan", "on"),
                   Command("turn_off", "Turn off", "turned off", "fan", "off"),
                   Command("set_speed", "Set speed", "set", "fan", "on", takes_value=True, value_type="percent",
                           min=1, max=100, step=1, unit="%", default=50, attr="percentage")]),
    # Blinds, shades, curtains, gates.
    Kind("cover", "Blind or shade", "Blinds & shades", "other",
         [("open", "Open"), ("closed", "Closed"), ("opening", "Opening"), ("closing", "Closing"), ("stopped", "Stopped")],
         active_state="open", noun="cover", transitional=("opening", "closing"),
         commands=[Command("open", "Open", "opened", "up", "open"), Command("close", "Close", "closed", "down", "closed"),
                   Command("stop", "Stop", "stopped", "pause", ""),
                   Command("set_position", "Set position", "moved", "sliders", "", takes_value=True, value_type="percent",
                           min=0, max=100, step=1, unit="%", default=50, attr="position")]),
    # A reading on its own (humidity, power, CO2…): the state is the number; attrs.unitOfMeasurement its unit
    # (any further readings: attrs with their units in attrs.units).
    Kind("sensor", "Sensor", "Sensors", "other", [], noun="sensor",
         properties=[Property("value", "Reading", "", "", "number"), BATTERY], summary=("value",)),
    Kind("leak", "Leak sensor", "Leak sensors", "security", [("dry", "Dry"), ("wet", "Leak!")], active_state="wet",
         noun="leak sensor", secure_state="dry", urgent=True, properties=[BATTERY]),
    Kind("smoke", "Smoke / CO alarm", "Smoke & CO alarms", "security", [("clear", "Clear"), ("detected", "Alarm!")],
         active_state="detected", noun="alarm", secure_state="clear", urgent=True, properties=[BATTERY]),
    # Any other on/off sensor (vibration, presence of power, problem…).
    Kind("binary", "On/off sensor", "On/off sensors", "other", [("on", "On"), ("off", "Off")], active_state="on",
         noun="sensor", properties=[BATTERY]),
    Kind("siren", "Siren", "Sirens", "security", [("on", "Sounding"), ("off", "Off")], active_state="on", noun="siren",
         commands=[Command("turn_on", "Sound", "sounded", "bell", "on", sensitive=True),
                   Command("turn_off", "Silence", "silenced", "bell-off", "off")]),
    # Something you press (a doorbell chime, a "restart" button on a device…).
    Kind("button", "Button", "Buttons", "other", [], noun="button",
         commands=[Command("press", "Press", "pressed", "pointer", "")]),
    # A robot vacuum. attrs.rooms = [{"id", "name"}] for "Clean rooms".
    Kind("vacuum", "Vacuum", "Vacuums", "cleaning",
         [("docked", "Docked"), ("charging", "Charging"), ("cleaning", "Cleaning"), ("returning", "Going home"),
          ("paused", "Paused"), ("idle", "Idle"), ("error", "Needs help"), ("emptying", "Emptying the bin"),
          ("washing", "Washing the mop"), ("drying", "Drying the mop"), ("mapping", "Mapping"),
          ("updating", "Updating")],
         active_state="cleaning", noun="vacuum", transitional=("returning",),
         commands=[Command("start", "Start cleaning", "started", "play", "cleaning"),
                   Command("pause", "Pause", "paused", "pause", "paused"),
                   Command("dock", "Send home", "sent home", "home", ""),
                   Command("locate", "Find", "found", "bell", ""),
                   Command("clean_rooms", "Clean rooms", "started", "play", "cleaning", takes_value=True,
                           value_type="choices", attr="rooms")],
         events=[Event("finished", "Finishes cleaning", "{device} finishes cleaning", "{device}", "Finished cleaning"),
                 Event("error", "Has a problem", "{device} has a problem", "{device} needs help",
                       "{device} stopped and needs help")],
         properties=[Property("battery", "Battery", "%", "battery", "number"),
                     Property("cleanArea", "Area cleaned", "m²", "cleanArea", "number"),
                     Property("cleanTime", "Cleaning time", "min", "cleanTime", "number"),
                     Property("problem", "Problem", "", "problem", "text"),
                     Property("fanSpeed", "Suction", "", "fanSpeed", "text"),
                     Property("filterLeft", "Filter life left", "%", "filterLeft", "number"),
                     Property("mainBrushLeft", "Main brush life left", "%", "mainBrushLeft", "number"),
                     Property("sideBrushLeft", "Side brush life left", "%", "sideBrushLeft", "number"),
                     Property("sensorsLeft", "Sensors until cleaning", "%", "sensorsLeft", "number")]),
    # Something that plays audio (a Sonos speaker…). attrs: volume (%), track, artist, source.
    Kind("speaker", "Speaker", "Speakers", "other",
         [("playing", "Playing"), ("paused", "Paused"), ("idle", "Stopped")], active_state="playing", noun="speaker",
         properties=[Property("volume", "Volume", "%", "volume", "number")],
         commands=[Command("play", "Play", "played", "play", "playing"),
                   Command("pause", "Pause", "paused", "pause", "paused"),
                   Command("set_volume", "Set volume", "set", "volume", "", takes_value=True, value_type="percent",
                           min=0, max=100, step=1, unit="%", default=30, attr="volume")]),
    # Music played by a wall tablet itself (the Navidrome connection): one per tablet. The hub keeps the queue;
    # attrs: volume (%), title, artist, album, cover (picture address), position / duration (s), queueLength,
    # screen ("off" while its tablet is asleep but still checking in: shown as "Screen off", not offline).
    # "Play something" / "Shuffle" take words: a song ("Jump by Van Halen"), an artist, an album or a playlist.
    Kind("music_player", "Music player", "Music players", "other",
         [("playing", "Playing"), ("paused", "Paused"), ("idle", "Stopped")], active_state="playing",
         noun="music player", notes=(("screen", "off", "Screen off"),),
         properties=[Property("volume", "Volume", "%", "volume", "number"),
                     Property("screen", "Screen", "", "screen", "text", (("on", "On"), ("off", "Off"))),
                     Property("title", "Song", "", "title", "text"),
                     Property("artist", "Artist", "", "artist", "text"),
                     Property("album", "Album", "", "album", "text"),
                     Property("position", "Position", "s", "position", "number"),
                     Property("duration", "Song length", "s", "duration", "number"),
                     Property("queueLength", "Songs in the queue", "", "queueLength", "number")],
         commands=[Command("play", "Play", "played", "play", "playing"),
                   Command("pause", "Pause", "paused", "pause", "paused"),
                   Command("next", "Next song", "skipped", "play", ""),
                   Command("previous", "Previous song", "went back on", "play", ""),
                   Command("stop", "Stop", "stopped", "stop", "idle"),
                   Command("set_volume", "Set volume", "set", "volume", "", takes_value=True, value_type="percent",
                           min=0, max=100, step=1, unit="%", default=40, attr="volume"),
                   Command("volume_up", "Volume up", "turned up", "volume", ""),
                   Command("volume_down", "Volume down", "turned down", "volume", ""),
                   Command("play_query", "Play something", "started playing on", "play", "", takes_value=True,
                           value_type="text", default=""),
                   Command("shuffle", "Shuffle", "shuffled", "play", "", takes_value=True, value_type="text",
                           default="")],
         spoken=("music", "song", "songs", "tunes"), summary=("title", "artist", "volume")),
    # A TV, any brand (Samsung, LG, Roku, Android / Google TV, Apple TV, Hisense VIDAA). "standby" = asleep but
    # still answering (it can be woken over the network). attrs: volume (%), muted, source / app (what's on),
    # mediaState, mediaTitle; sources / apps / keys are the [{"id", "name"}] lists the choose-input, open-app and
    # press-key commands pick from - each TV lists only what it has.
    Kind("tv", "TV", "TVs", "other",
         [("on", "On"), ("standby", "Standby"), ("off", "Off")], active_state="on", noun="TV",
         properties=[Property("volume", "Volume", "%", "volume", "number"),
                     Property("muted", "Muted", "", "muted", "boolean"),
                     Property("source", "Input", "", "source", "text"),
                     Property("app", "App", "", "app", "text"),
                     Property("mediaState", "Playback", "", "mediaState", "text",
                              (("playing", "Playing"), ("paused", "Paused"), ("idle", "Stopped"))),
                     Property("mediaTitle", "Now playing", "", "mediaTitle", "text")],
         commands=[Command("turn_on", "Turn on", "turned on", "power", "on"),
                   Command("turn_off", "Turn off", "turned off", "power", "off"),
                   Command("set_volume", "Set volume", "set", "volume", "", takes_value=True, value_type="percent",
                           min=0, max=100, step=1, unit="%", default=20, attr="volume"),
                   Command("volume_up", "Volume up", "turned up", "volume", ""),
                   Command("volume_down", "Volume down", "turned down", "volume", ""),
                   Command("mute", "Mute", "muted", "volume", ""),
                   Command("play", "Play", "played", "play", ""),
                   Command("pause", "Pause", "paused", "pause", ""),
                   Command("next", "Next", "skipped", "play", ""),
                   Command("previous", "Previous", "went back on", "play", ""),
                   Command("select_source", "Choose input", "switched", "list", "", takes_value=True,
                           value_type="choices", attr="sources"),
                   Command("launch_app", "Open app", "opened an app on", "grid", "", takes_value=True,
                           value_type="choices", attr="apps"),
                   Command("send_key", "Press key", "pressed a key on", "pointer", "", takes_value=True,
                           value_type="choices", attr="keys")],
         spoken=("television", "telly"), summary=("app", "volume")),
    # A web address: other services call it (an iPhone Shortcut, IFTTT, a script) to start automations,
    # and/or Newman sends to one. Neither a physical button nor a remote, hence its own kind.
    Kind("webhook", "Webhook", "Webhooks", "other", [("idle", "Ready")], noun="webhook",
         events=[Event("received", "Is called", "{device} is called", "{device}", "{device} was called")],
         commands=[Command("send", "Send", "sent", "send", "", takes_value=True, value_type="text", default="")]),
    # A household appliance (dishwasher, washer, dryer, oven, fridge, coffee maker…) from its maker's
    # service. attrs.applianceType says which; whatever else it reports is offered as-is (with
    # attrs.units giving units). "on" is for ones that are simply running all the time (a fridge).
    Kind("appliance", "Appliance", "Appliances", "appliances",
         [("off", "Off"), ("ready", "Ready"), ("on", "On"), ("delayed", "Starts later"), ("running", "Running"),
          ("paused", "Paused"), ("attention", "Needs you"), ("finished", "Finished"), ("error", "Problem")],
         active_state="running", noun="appliance",
         commands=[Command("start", "Start", "started", "play", "running"),
                   Command("pause", "Pause", "paused", "pause", "paused"),
                   Command("resume", "Resume", "resumed", "play", "running"),
                   Command("stop", "Stop", "stopped", "stop", ""),
                   Command("turn_off", "Turn off", "turned off", "power", "off")],
         events=[Event("finished", "Finishes", "{device} finishes", "{device}", "{device} is done"),
                 Event("alert", "Needs something", "{device} needs something (rinse aid, salt, a door left open…)",
                       "{device}", "{device}: {detail}"),
                 Event("error", "Has a problem", "{device} has a problem", "{device} needs attention",
                       "{device}: {detail}")],
         properties=[Property("remaining", "Time left", "min", "remaining", "number"),
                     Property("progress", "Progress", "%", "progress", "number"),
                     Property("program", "Program", "", "program", "text"),
                     Property("door", "Door", "", "door", "text", (("open", "Open"), ("closed", "Closed"), ("locked", "Locked"))),
                     Property("remoteStart", "Can be started remotely", "", "remoteStart", "boolean"),
                     Property("problem", "Problem", "", "problem", "text")]),
    # A Docker container. attrs: health, cpu (%), memory (MB), memoryPercent, image.
    Kind("container", "Container", "Containers", "servers",
         [("running", "Running"), ("stopped", "Stopped"), ("paused", "Paused"), ("restarting", "Restarting")],
         active_state="running", noun="container", transitional=("restarting",),
         commands=[Command("start", "Start", "started", "play", "running"),
                   Command("stop", "Stop", "stopped", "stop", "stopped"),
                   Command("restart", "Restart", "restarted", "refresh", "running")],
         events=[Event("crashed", "Crashes", "{device} crashes", "{device} crashed",
                       "{device} stopped unexpectedly ({detail})"),
                 Event("unhealthy", "Becomes unhealthy", "{device} becomes unhealthy", "{device}",
                       "{device} is failing its health check")],
         properties=[Property("health", "Health", "", "health", "text",
                              (("healthy", "Healthy"), ("unhealthy", "Unhealthy"), ("starting", "Starting"))),
                     Property("cpu", "CPU", "%", "cpu", "number"),
                     Property("memory", "Memory", "MB", "memory", "number"),
                     Property("memoryPercent", "Memory use", "%", "memoryPercent", "number")],
         # Deleted from Docker but still used by an automation, dashboard or widget: kept, shown as Removed.
         notes=(("removed", True, "Removed"),)),
    # A computer that runs things - an Unraid box, a Linux machine, a Proxmox node (the server-health
    # connections). attrs: cpu (%), load (1 min), memoryPercent, temperature (hottest CPU sensor), temps
    # {sensor: °C}, uptimeDays, os, version, updateAvailable / updates, problem (what "warning" is about).
    # Unraid adds its array: arrayState, arrayUsedPercent, parityCheck, parityProgress, parityErrors,
    # parityResult, lastParityCheck, containersRunning / containersTotal.
    Kind("server", "Server", "Servers", "servers",
         [("online", "Online"), ("warning", "Needs a look"), ("offline", "Offline")], active_state="online",
         noun="server",
         events=[Event("offline", "Goes offline", "{device} goes offline", "{device} is offline",
                       "{device} stopped responding"),
                 Event("online", "Comes back online", "{device} comes back online", "{device}", "{device} is back online"),
                 Event("too_hot", "Gets too hot", "{device} gets too hot", "{device} is hot",
                       "{device} is running hot ({detail})"),
                 Event("high_cpu", "CPU stays high", "{device}'s CPU stays high", "{device}",
                       "{device}'s CPU has been busy for a while ({detail})"),
                 Event("high_memory", "Memory stays high", "{device}'s memory stays high", "{device}",
                       "{device} has been low on memory for a while ({detail} used)"),
                 Event("parity_started", "A parity check starts", "a parity check starts on {device}", "{device}",
                       "A parity check started on {device}"),
                 Event("parity_finished", "A parity check finishes", "a parity check finishes on {device}",
                       "{device}", "Parity check on {device} finished: {detail}"),
                 Event("array_stopped", "The array stops", "the array on {device} stops", "{device}",
                       "The array on {device} stopped ({detail})")],
         properties=[Property("cpu", "CPU", "%", "cpu", "number"),
                     Property("load", "Load (1 min)", "", "load", "number"),
                     Property("memoryPercent", "Memory use", "%", "memoryPercent", "number"),
                     Property("temperature", "CPU temperature", "°C", "temperature", "number"),
                     Property("temps", "{sub} temperature", "°C", "temps.*", "number"),
                     Property("uptimeDays", "Up for", "days", "uptimeDays", "number"),
                     Property("os", "System", "", "os", "text"),
                     Property("version", "Version", "", "version", "text"),
                     Property("updateAvailable", "Updates available", "", "updateAvailable", "boolean"),
                     Property("updates", "Pending updates", "", "updates", "number"),
                     Property("problem", "Problem", "", "problem", "text"),
                     Property("arrayState", "Array", "", "arrayState", "text",
                              (("started", "Started"), ("stopped", "Stopped"))),
                     Property("arrayUsedPercent", "Array used", "%", "arrayUsedPercent", "number"),
                     Property("parityCheck", "Parity check", "", "parityCheck", "text",
                              (("running", "Running"), ("paused", "Paused"), ("idle", "Not running"))),
                     Property("parityProgress", "Parity check progress", "%", "parityProgress", "number"),
                     Property("parityErrors", "Last parity check errors", "", "parityErrors", "number"),
                     Property("parityResult", "Last parity check", "", "parityResult", "text",
                              (("completed", "Completed"), ("cancelled", "Cancelled"), ("failed", "Failed"),
                               ("never_run", "Never run"), ("running", "Running"), ("paused", "Paused"))),
                     Property("containersRunning", "Containers running", "", "containersRunning", "number")],
         summary=("cpu", "memoryPercent", "temperature")),
    # A disk, filesystem or storage pool on a server. "standby" = spun down. attrs: usedPercent, free / size
    # (GB), temperature, smart (passed / warning / failed), errors, powerOnHours, lifeLeft (SSD wear), problem.
    Kind("disk", "Disk", "Disks & storage", "servers",
         [("ok", "OK"), ("warning", "Needs a look"), ("failed", "Failed"), ("standby", "Asleep")], noun="disk",
         events=[Event("failed", "Fails", "{device} fails", "{device} failed", "{device} failed ({detail})"),
                 Event("smart_warning", "Shows a SMART warning", "{device} shows a SMART warning", "{device}",
                       "{device} reports a SMART warning - it may be failing"),
                 Event("almost_full", "Is almost full", "{device} is almost full", "{device}", "{device} is {detail}"),
                 Event("too_hot", "Gets too hot", "{device} gets too hot", "{device} is hot",
                       "{device} is running hot ({detail})")],
         properties=[Property("usedPercent", "Used", "%", "usedPercent", "number"),
                     Property("free", "Free", "GB", "free", "number"),
                     Property("size", "Size", "GB", "size", "number"),
                     Property("temperature", "Temperature", "°C", "temperature", "number"),
                     Property("smart", "SMART", "", "smart", "text",
                              (("passed", "Passed"), ("warning", "Warning"), ("failed", "Failed"))),
                     Property("errors", "Errors", "", "errors", "number"),
                     Property("powerOnHours", "Power-on hours", "h", "powerOnHours", "number"),
                     Property("lifeLeft", "Life left", "%", "lifeLeft", "number"),
                     Property("problem", "Problem", "", "problem", "text")],
         summary=("usedPercent", "temperature")),
    # A virtual machine or LXC container (Proxmox). attrs: cpu (%), memory (MB), memoryPercent, uptimeDays,
    # vmType ("VM" / "LXC container"), node.
    Kind("vm", "Virtual machine", "Virtual machines", "servers",
         [("running", "Running"), ("stopped", "Stopped"), ("paused", "Paused")], active_state="running",
         noun="virtual machine",
         commands=[Command("start", "Start", "started", "play", "running"),
                   Command("shutdown", "Shut down", "shut down", "power", "stopped", sensitive=True),
                   Command("stop", "Force stop", "stopped", "stop", "stopped", sensitive=True),
                   Command("reboot", "Reboot", "rebooted", "refresh", "running")],
         properties=[Property("cpu", "CPU", "%", "cpu", "number"),
                     Property("memory", "Memory", "MB", "memory", "number"),
                     Property("memoryPercent", "Memory use", "%", "memoryPercent", "number"),
                     Property("uptimeDays", "Up for", "days", "uptimeDays", "number")]),
    # A network printer (IPP / AirPrint). attrs.levels = {"black": 60, "cyan": 55, …} (% left per supply).
    Kind("printer", "Printer", "Printers", "other",
         [("idle", "Ready"), ("printing", "Printing"), ("stopped", "Stopped")], active_state="printing", noun="printer",
         events=[Event("attention", "Needs attention", "{device} needs attention (paper, a jam, toner, a cover…)",
                       "{device}", "{device}: {detail}")],
         properties=[Property("level", "{sub} level", "%", "levels.*", "number"),
                     Property("problem", "Problem", "", "problem", "text"),
                     Property("message", "Printer's message", "", "message", "text")]),
    # Something around the house that comes due again and again (the Upkeep connection). attrs: due
    # (date), daysLeft (negative = late), every, owner, lastDone / lastDoneBy, runtimeHours, history.
    Kind("upkeep", "Upkeep item", "Upkeep", "other",
         [("ok", "Up to date"), ("soon", "Coming up"), ("due", "Due today"), ("overdue", "Overdue")],
         active_state="due", noun="item",
         commands=[Command("done", "Mark done", "marked done", "check", "ok"),
                   Command("snooze", "Remind tomorrow", "snoozed", "clock", "")],
         events=[Event("due", "Comes due", "{device} comes due", "{device}", "{device} is {detail}"),
                 Event("soon", "Is coming up", "{device} is coming up", "{device}", "{device} is coming up ({detail})")],
         properties=[Property("daysLeft", "Days left", "days", "daysLeft", "number"),
                     Property("runtimeHours", "Running hours since last done", "h", "runtimeHours", "number")]),
    # A wall tablet's own battery, as the wall app reports it (POST /api/tablet/battery): one device per
    # tablet account. With a smart plug and two automations (on below 20 %, off above 80 %) a tablet that
    # lives on the wall isn't kept at 100 % all day - what swells its battery. Temperature warns first.
    Kind("tablet", "Wall tablet", "Wall tablets", "other",
         [("charging", "Charging"), ("battery", "On battery")], active_state="charging", noun="tablet",
         properties=[BATTERY,
                     Property("temperature", "Battery temperature", "°C", "temperature", "number"),
                     Property("voltage", "Battery voltage", "V", "voltage", "number"),
                     Property("plugged", "Plugged into", "", "plugged", "text",
                              (("ac", "A charger"), ("usb", "USB"), ("wireless", "A wireless charger"),
                               ("none", "Nothing"))),
                     Property("screen", "Screen", "", "screen", "text", (("on", "On"), ("off", "Off")))],
         notes=(("screen", "off", "Screen off"),)),
    # A network check (the Network checks connection): a ping, a TCP port or a web address that answers or
    # doesn't. attrs: latency (ms), uptime (% of the last 24 h), statusCode (web checks), lastChange,
    # failures (in a row), problem; host / protocol say what it checks.
    Kind("netcheck", "Network check", "Network checks", "servers",
         [("online", "Online"), ("offline", "Offline")], active_state="offline", noun="check",
         events=[Event("offline", "Goes offline", "{device} goes offline", "{device} is offline",
                       "{device} stopped answering ({detail})"),
                 Event("online", "Comes back online", "{device} comes back online", "{device} is back",
                       "{device} is answering again")],
         properties=[Property("latency", "Response time", "ms", "latency", "number"),
                     Property("uptime", "Uptime (last 24 h)", "%", "uptime", "number"),
                     Property("statusCode", "Status code", "", "statusCode", "number"),
                     Property("lastChange", "Last change", "", "lastChange", "text"),
                     Property("failures", "Failures in a row", "", "failures", "number"),
                     Property("problem", "Problem", "", "problem", "text")],
         summary=("latency", "uptime")),
    # The sun over the home (the Sun connection), worked out locally. attrs: elevation / azimuth (degrees),
    # nextSunrise / nextSunset / nextDawn / nextDusk (local "YYYY-MM-DDTHH:MM"). Dawn and dusk = civil twilight.
    Kind("sun", "Sun", "Sun", "climate", [("above_horizon", "Up"), ("below_horizon", "Down")], noun="sun",
         events=[Event("dawn", "Dawn (first light)", "it's dawn", "Dawn", "First light"),
                 Event("sunrise", "Sunrise", "the sun rises", "Sunrise", "The sun is up"),
                 Event("sunset", "Sunset", "the sun sets", "Sunset", "The sun has set"),
                 Event("dusk", "Dusk (last light)", "it's dusk", "Dusk", "Last light")],
         properties=[Property("elevation", "Height in the sky", "°", "elevation", "number"),
                     Property("azimuth", "Direction (compass)", "°", "azimuth", "number"),
                     Property("nextSunrise", "Next sunrise", "", "nextSunrise", "text"),
                     Property("nextSunset", "Next sunset", "", "nextSunset", "text"),
                     Property("nextDawn", "Next dawn", "", "nextDawn", "text"),
                     Property("nextDusk", "Next dusk", "", "nextDusk", "text")],
         spoken=("sunrise", "sunset", "sun"), summary=("nextSunrise", "nextSunset")),
    # A recipe collection (the Forkast connection); attrs.recipes = how many. The recipes themselves are shown
    # and read out by cook mode on a wall tablet (recipes.py), not as devices.
    Kind("recipe_book", "Recipe book", "Recipe books", "other", [("ready", "Ready")], noun="recipe book",
         properties=[Property("recipes", "Recipes", "", "recipes", "number")]),
    # A calendar: "busy" while an event is on; attrs.upcoming lists the next events for the tile.
    Kind("calendar", "Calendar", "Calendars", "other",
         [("free", "Nothing on"), ("busy", "Event now")], active_state="busy", noun="calendar",
         events=[Event("start", "An event starts", "an event starts on {device}", "{device}",
                       "An event is starting on {device}")]),
    # A network ad blocker (AdGuard Home, Pi-hole, Technitium): "on" while it's filtering; "paused" turns itself
    # back on at attrs.resumesAt (epoch seconds). Numbers cover roughly the last day, as the server counts them.
    Kind("dns_filter", "DNS filter", "DNS filters", "servers",
         [("on", "Protecting"), ("off", "Off"), ("paused", "Paused")], active_state="on", noun="DNS filter",
         commands=[Command("turn_on", "Turn on", "turned on", "shield", "on"),
                   Command("turn_off", "Turn off", "turned off", "shield-off", "off"),
                   Command("pause", "Pause", "paused", "pause", "paused", takes_value=True, value_type="number",
                           min=1, max=1440, step=1, unit="min", default=5)],
         events=[Event("protection_off", "Protection is turned off", "{device}'s protection is turned off", "{device}",
                       "Ad blocking is off ({detail})"),
                 Event("protection_on", "Protection comes back on", "{device}'s protection comes back on", "{device}",
                       "Ad blocking is back on")],
         properties=[Property("queriesToday", "Queries today", "", "queriesToday", "number"),
                     Property("blockedToday", "Blocked today", "", "blockedToday", "number"),
                     Property("blockedPercent", "Blocked", "%", "blockedPercent", "number"),
                     Property("clients", "Clients", "", "clients", "number"),
                     Property("rules", "Blocking rules", "", "rules", "number"),
                     Property("version", "Version", "", "version", "text"),
                     Property("updateAvailable", "Update available", "", "updateAvailable", "boolean")],
         summary=("blockedPercent", "queriesToday")),
    # A sprinkler controller (Hydrawise, Rain Bird, Rachio…). Its zones are sprinkler_zone devices.
    # "off" = standby (the schedule won't run until switched back on); "rain_delay" = paused for a few days.
    Kind("sprinkler", "Sprinkler controller", "Sprinklers", "other",
         [("idle", "Idle"), ("watering", "Watering"), ("rain_delay", "Rain delay"), ("off", "Standby")],
         active_state="watering", noun="sprinkler controller",
         commands=[Command("stop", "Stop watering", "stopped", "stop", "idle"),
                   Command("rain_delay", "Rain delay", "delayed", "cloud", "rain_delay", takes_value=True,
                           value_type="number", min=0, max=14, step=1, unit="days", default=1),
                   Command("turn_off", "Standby", "put on standby", "power", "off"),
                   Command("turn_on", "Resume schedule", "resumed", "play", "idle")],
         properties=[Property("currentZone", "Watering now", "", "currentZone", "text"),
                     Property("remaining", "Time left", "min", "remaining", "number"),
                     Property("nextRun", "Next run", "", "nextRun", "text"),
                     Property("rainSensor", "Rain sensor", "", "rainSensor", "text", (("wet", "Wet"), ("dry", "Dry"))),
                     Property("rainDelayUntil", "Rain delay until", "", "rainDelayUntil", "text")],
         spoken=("sprinklers", "irrigation", "watering"), summary=("currentZone", "nextRun")),
    # One watering zone (station / valve) of a sprinkler controller.
    Kind("sprinkler_zone", "Sprinkler zone", "Sprinkler zones", "other",
         [("idle", "Idle"), ("watering", "Watering")], active_state="watering", noun="zone",
         commands=[Command("run", "Water for", "started", "droplet", "watering", takes_value=True,
                           value_type="number", min=1, max=180, step=1, unit="min", default=10),
                   Command("stop", "Stop", "stopped", "stop", "idle")],
         events=[Event("started", "Starts watering", "{device} starts watering", "{device}", "{device} started watering"),
                 Event("finished", "Finishes watering", "{device} finishes watering", "{device}",
                       "{device} finished watering")],
         properties=[Property("remaining", "Time left", "min", "remaining", "number"),
                     Property("lastRun", "Last watered", "", "lastRun", "text"),
                     Property("nextRun", "Next run", "", "nextRun", "text")],
         summary=("remaining", "nextRun")),
]}


def _a(word: str) -> str:
    return f"{'an' if word[:1] in 'aeiou' else 'a'} {word}"


def render(template: str, device: str, kind_: Kind, event: Event, detail: str = "") -> str:
    """Fill an event template for one device. {detail} = what the device said (e.g. "rinse aid low")."""
    noun = kind_.noun
    device_noun = device if device.lower().endswith(noun.lower()) else f"{device} {noun}"
    what = event.what or event.id.replace("_", " ")
    template = template.replace("{detail}", detail or event.label.lower())
    return (template.replace("{device_noun}", device_noun).replace("{device}", device)
            .replace("{a_what}", _a(what)).replace("{what}", what.capitalize()))


def event_phrase(kind_: Kind, event_id: str, device: str) -> str:
    """"someone rings the Front Door doorbell" - generic fallback for kinds without wording."""
    ev = kind_.event(event_id)
    return render(ev.phrase or "{device} reports “" + ev.label.lower() + "”", device, kind_, ev)


# -- properties: every value a device reports ---------------------------------------------------

def humanize(key: str) -> str:
    """"linkQuality" / "link_quality" -> "Link quality"."""
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key).replace("_", " ").replace(".", " ").split()
    return " ".join(words).capitalize()


def value_type(v: Any) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str) and 0 < len(v) <= 40:
        try:
            float(v)
            return "number"
        except ValueError:
            return "text"
    return ""


def _prop(p_id: str, label: str, unit: str, attr: str, type_: str, options: tuple, value: Any) -> dict:
    return {"id": p_id, "label": label, "unit": unit, "attr": attr, "type": type_,
            "options": [{"value": v, "label": lab} for v, lab in options], "value": value}


def properties_for(ent: Any) -> list[dict]:
    """Everything one device reports that automations can use, with its current value: the
    kind's declared properties (nice names, units) plus every other simple value it reports."""
    k = KINDS[ent.kind]
    attrs = ent.attrs or {}
    out: dict[str, dict] = {}
    covered = set()
    units = attrs.get("units") if isinstance(attrs.get("units"), dict) else {}  # {attr: unit} for undeclared readings
    for p in k.properties:
        if p.attr.endswith(".*"):
            base = p.attr[:-2]
            covered.add(base)
            sub_values = attrs.get(base) if isinstance(attrs.get(base), dict) else {}
            for sub, v in sub_values.items():
                if value_type(v):
                    out[f"{base}.{sub}"] = _prop(f"{base}.{sub}", p.label.replace("{sub}", humanize(sub)), p.unit,
                                                 f"{base}.{sub}", p.type or value_type(v), p.options, v)
            continue
        covered.add(p.attr)
        if p.attr and p.attr not in attrs:
            continue  # this device doesn't report it
        v = ent.state if not p.attr else attrs.get(p.attr)
        unit = p.unit or ((k.unit or attrs.get("unitOfMeasurement") or "") if not p.attr else "")
        label = p.label if not (p.id == "value" and attrs.get("deviceClass")) else humanize(str(attrs["deviceClass"]))
        out[p.id] = _prop(p.id, label, unit, p.attr, p.type or value_type(v) or "text", p.options, v)
    for key, v in attrs.items():
        if key in INFO_ATTRS or key in covered or key in out:
            continue
        if isinstance(v, dict):
            for sub, sv in v.items():
                if value_type(sv) == "number":
                    pid = f"{key}.{sub}"
                    out[pid] = _prop(pid, f"{humanize(sub)} ({humanize(key).lower()})", "", pid, "number", (), sv)
        elif t := value_type(v):
            out[key] = _prop(key, humanize(key), units.get(key, ""), key, t, (), v)
    return list(out.values())


def find_property(ent: Any, prop_id: str) -> dict | None:
    return next((p for p in properties_for(ent) if p["id"] == prop_id), None)


def _truthy(v: Any) -> bool:
    return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "1", "yes", "on")


# What each type of value can be compared with ("changes" only starts automations).
OPS = {"number": ("above", "below", "is", "changes"), "text": ("is", "is_not", "changes"),
       "boolean": ("is", "changes")}


def property_matches(p: dict | None, op: str, target: Any) -> bool:
    if not p or p["value"] is None:
        return False
    v = p["value"]
    if p["type"] == "number":
        try:
            v, t = float(v), float(target)
        except (TypeError, ValueError):
            return False
        return {"above": v > t, "below": v < t, "is": v == t, "is_not": v != t}.get(op, False)
    if p["type"] == "boolean":
        if op == "is":
            return _truthy(v) == _truthy(target)
        return _truthy(v) != _truthy(target) if op == "is_not" else False
    if op == "is":
        return str(v) == str(target)
    return str(v) != str(target) if op == "is_not" else False


def format_value(p: dict, v: Any) -> str:
    if p["type"] == "number":
        try:
            num = f"{float(v):g}"
        except (TypeError, ValueError):
            return str(v)
        unit = p.get("unit") or ""
        return num + unit if unit == "%" else f"{num} {unit}" if unit else num
    if p["type"] == "boolean":
        return "yes" if _truthy(v) else "no"
    label = next((o["label"] for o in p.get("options") or [] if o["value"] == v), None)
    return (label or humanize(str(v))).lower()


def meta_json(version: str) -> dict:
    return {
        "version": version,
        "categories": [{"id": c, "name": n} for c, n in CATEGORIES],
        "kinds": [k.to_json() for k in KINDS.values()],
    }


def kind(kind_id: str) -> Kind:
    return KINDS[kind_id]


def join_names(names: list[str], word: str = "and") -> str:
    names = [n for n in names if n]
    if len(names) <= 1:
        return names[0] if names else ""
    return f"{', '.join(names[:-1])} {word} {names[-1]}"


__all__ = ["CATEGORIES", "Command", "Event", "Property", "Kind", "properties_for", "humanize", "find_property", "property_matches", "format_value", "OPS", "KINDS", "render", "event_phrase", "meta_json", "kind", "join_names", "asdict"]
