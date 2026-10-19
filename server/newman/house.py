"""House settings - the web dashboard's Settings page: how the whole house behaves.

Settings "house" (only what was changed is saved; everything else is its default):
  devices  {"batteryLow": 20, "leftOpenMinutes": 15}            warn below this battery %; Home's "needs you"
                                                                once a door/lock is left open this long (each
                                                                device can set its own: muting.DeviceWarnings)
  lights   {"rampSeconds": 3.5, "step": 10, "holdSeconds": 0.5} hold-to-dim speed, a raise/lower tap, how
                                                                long a Lutron Pico press must last to be a hold
  climate  {"range": [18, 26], "bands": [Cool, Comfortable, Warm]}  the phone's room bars
  cameras  {"mode": "latest"|"featured", "featured": id|null, "cooldownMinutes": 1, "clipDays": 3}
  history  {"activityDays": 90, "runDays": 90}

apply() pushes the values into the places that use them (the battery check, the hold-to-dim ramp, a remote's
raise/lower, the Pico hold time, how long activity and runs are kept); the clip copies are pruned with
clip_days().
"""

from __future__ import annotations

import copy
import re
from typing import Any, Callable

SETTING = "house"

DEFAULTS: dict[str, Any] = {
    "devices": {"batteryLow": 20, "leftOpenMinutes": 15},
    "lights": {"rampSeconds": 3.5, "step": 10, "holdSeconds": 0.5},
    "climate": {"range": [18, 26],
                "bands": [{"name": "Cool", "below": 21.5, "color": "#9db8ff"},
                          {"name": "Comfortable", "color": "#f5b14c"},
                          {"name": "Warm", "above": 22.0, "color": "#ff9a4d"}]},
    "cameras": {"mode": "latest", "featured": None, "cooldownMinutes": 1, "clipDays": 3},
    "history": {"activityDays": 90, "runDays": 90},
}
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _between(v: Any, lo: float, hi: float, what: str, whole: bool = False) -> str | None:
    if not _num(v) or v < lo or v > hi or (whole and v != int(v)):
        return f"{what}: {lo:g} to {hi:g}" + (" (whole numbers)" if whole else "")
    return None


def merged(saved: Any) -> dict[str, Any]:
    out = copy.deepcopy(DEFAULTS)
    if isinstance(saved, dict):
        for sec, vals in saved.items():
            if sec in out and isinstance(vals, dict):
                out[sec].update({k: copy.deepcopy(v) for k, v in vals.items() if k in out[sec]})
    return out


def validate(s: dict[str, Any], camera_ok: Callable[[str], bool]) -> list[str]:
    """Everything wrong with a whole settings dict, in words (empty when it's fine)."""
    errs = [e for e in (
        _between(s["devices"]["batteryLow"], 5, 50, "Low battery warning", True),
        _between(s["devices"]["leftOpenMinutes"], 1, 240, "Left open warning (minutes)", True),
        _between(s["lights"]["rampSeconds"], 3, 6, "Hold-to-dim speed (seconds)"),
        None if s["lights"]["step"] in (5, 10, 20) else "Raise / lower step: 5, 10 or 20",
        _between(s["lights"]["holdSeconds"], 0.3, 1.5, "Hold time (seconds)"),
        _between(s["cameras"]["cooldownMinutes"], 0, 1440, "Wait before alerting again (minutes)", True),
        _between(s["cameras"]["clipDays"], 1, 7, "Keep copies of watched clips (days)", True),
        _between(s["history"]["activityDays"], 1, 90, "Keep activity (days)", True),
        _between(s["history"]["runDays"], 1, 90, "Keep automation history (days)", True),
    ) if e]
    cam = s["cameras"]
    if cam["mode"] not in ("latest", "featured"):
        errs.append('Big camera: "latest" or "featured"')
    elif cam["mode"] == "featured" and not (isinstance(cam["featured"], str) and camera_ok(cam["featured"])):
        errs.append("Pick the featured camera")
    if cam["featured"] is not None and not (isinstance(cam["featured"], str) and camera_ok(cam["featured"])):
        errs.append("That featured camera isn't a camera")
    rng = s["climate"]["range"]
    if not (isinstance(rng, list) and len(rng) == 2 and all(_num(x) for x in rng) and rng[0] < rng[1]):
        errs.append("Room bar range: a lowest and a higher highest temperature")
    bands = s["climate"]["bands"]
    if not (isinstance(bands, list) and len(bands) == 3 and all(isinstance(b, dict) for b in bands)):
        errs.append("Colours: Cool, Comfortable and Warm")
    else:
        for b in bands:
            if not COLOR.match(str(b.get("color") or "")):
                errs.append(f"{b.get('name') or 'A band'}'s colour: like #ff9a4d")
            if not str(b.get("name") or "").strip():
                errs.append("Each colour band needs a name")
        lo, hi = bands[0].get("below"), bands[2].get("above")
        if not (_num(lo) and _num(hi) and lo <= hi):
            errs.append("Colours: Cool starts below a temperature no higher than where Warm starts")
    return errs


class House:
    def __init__(self, store: Any, camera_ok: Callable[[str], bool] = lambda _id: True) -> None:
        self.store = store
        self.camera_ok = camera_ok  # an entity id that is a camera (set by the API, which knows the hub)
        self.listeners: list[Callable[[dict[str, Any]], None]] = []
        self.apply()

    def get(self) -> dict[str, Any]:
        return merged(self.store.settings().get(SETTING))

    def patch(self, data: Any) -> dict[str, Any]:
        """Partial update ({"lights": {"step": 5}}). ValueError: refused, in words."""
        if not isinstance(data, dict):
            raise ValueError("Send the settings to change")
        cur = self.get()
        for sec, vals in data.items():
            if sec not in DEFAULTS or not isinstance(vals, dict):
                raise ValueError(f"Unknown settings section: {sec}")
            for k, v in vals.items():
                if k not in DEFAULTS[sec]:
                    raise ValueError(f"Unknown setting: {sec}.{k}")
                cur[sec][k] = v
        if cur["cameras"]["mode"] == "latest" and "featured" not in (data.get("cameras") or {}):
            pass  # the chosen camera is kept for when "featured" is picked again
        if errs := validate(cur, self.camera_ok):
            raise ValueError("; ".join(errs))
        # Only what differs from the defaults is saved, so a later default change reaches everyone else.
        saved = {sec: {k: v for k, v in vals.items() if v != DEFAULTS[sec][k]} for sec, vals in cur.items()}
        self.store.set_setting(SETTING, {k: v for k, v in saved.items() if v})
        self.apply()
        for fn in list(self.listeners):
            fn(cur)
        return cur

    def apply(self) -> None:
        """Hands the values to the code that uses them."""
        from . import state
        from .automations import engine
        from . import controls
        from .integrations import lutron
        s = self.get()
        state.BATTERY_LOW["default"] = s["devices"]["batteryLow"]
        engine.RAMP_FULL_S = float(s["lights"]["rampSeconds"])
        controls.STEP = int(s["lights"]["step"])
        lutron.HOLD_S = float(s["lights"]["holdSeconds"])
        self.store.event_days = int(s["history"]["activityDays"])
        self.store.run_days = int(s["history"]["runDays"])

    def clip_days(self) -> int:
        return int(self.get()["cameras"]["clipDays"])

    def camera_cooldown_minutes(self) -> int:
        return int(self.get()["cameras"]["cooldownMinutes"])
