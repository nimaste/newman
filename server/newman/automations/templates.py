"""Starter recipes, filled in with the devices and people that exist now."""
from __future__ import annotations

from typing import Any

from ..state import Hub


def templates(hub: Hub, people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    everyone = [p["id"] for p in people]
    locks = [e.id for e in hub.all("lock")]
    alarms = [e.id for e in hub.all("alarm")]
    bells = [e.id for e in hub.all("doorbell")]
    out = []
    if locks:
        out.append({"id": "nightly-lock-check", "name": "Nightly lock check", "category": "security",
                    "hint": "Remind everyone at bedtime if a door is still unlocked, with Lock buttons.",
                    "automation": {
                        "name": "Nightly lock check",
                        "trigger": {"type": "time", "time": "21:00", "days": [0, 1, 2, 3, 4, 5, 6]},
                        "condition": {"mode": "any", "entities": locks},
                        "notify": {"enabled": True, "timeSensitive": True, "onlyPending": True,
                                   "buttons": [f"lock:{e}" for e in locks] + (["lock_all"] if len(locks) > 1 else [])
                                   + ["snooze:60", "dismiss"]},
                        "recipients": everyone,
                        "followUp": {"enabled": True, "remindMinutes": 30},
                    }})
        out.append({"id": "lock-activity", "name": "Lock activity", "category": "security",
                    "hint": "A notification every time a lock is locked or unlocked.",
                    "automation": {
                        "name": "Lock activity",
                        "trigger": {"type": "state", "entities": locks, "to": "any"},
                        "condition": {"mode": "always", "entities": []},
                        "notify": {"enabled": True, "buttons": []},
                        "recipients": everyone,
                    }})
    if bells:
        out.append({"id": "doorbell-alert", "name": "Doorbell alert", "category": "security",
                    "hint": "Time-sensitive notification with a snapshot every time the doorbell is pressed.",
                    "automation": {
                        "name": "Doorbell alert",
                        "trigger": {"type": "event", "entity": bells[0], "event": "pressed"},
                        "notify": {"enabled": True, "timeSensitive": True, "snapshot": True, "buttons": []},
                        "recipients": everyone,
                    }})
    if alarms:
        out.append({"id": "alarm-bedtime", "name": "Arm the alarm at bedtime", "category": "security",
                    "hint": "At 11 PM, if the alarm is still disarmed, offer to arm it.",
                    "automation": {
                        "name": "Arm the alarm at bedtime",
                        "trigger": {"type": "time", "time": "23:00", "days": [0, 1, 2, 3, 4, 5, 6]},
                        "condition": {"mode": "any", "entities": alarms[:1]},
                        "notify": {"enabled": True, "buttons": [f"arm_home:{alarms[0]}", "dismiss"]},
                        "recipients": everyone,
                        "followUp": {"enabled": True},
                    }})
    return out
