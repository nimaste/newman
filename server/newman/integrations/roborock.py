"""Roborock robot vacuums.

Sign-in happens once, by email code (no password): Newman keeps the session Roborock issues, the
way the Roborock app does, then talks to each vacuum directly on the home network whenever it can
(python-roborock picks local first, Roborock's cloud as the fallback). Works across Roborock's
protocol generations: "V1" (most vacuums) and "B01" (newer Q-series).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from ..store import slug
from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

# Roborock's own state names (V1 protocol) -> the vacuum kind's states.
V1_STATES = {
    "charging": "charging", "charging_complete": "docked", "idle": "idle", "starting": "cleaning",
    "cleaning": "cleaning", "spot_cleaning": "cleaning", "zoned_cleaning": "cleaning", "segment_cleaning": "cleaning",
    "going_to_target": "cleaning", "manual_mode": "cleaning", "remote_control_active": "cleaning",
    "robot_status_mopping": "cleaning", "clean_mop_mopping": "cleaning", "segment_mopping": "cleaning",
    "segment_clean_mop_mopping": "cleaning", "zoned_mopping": "cleaning", "patrol": "cleaning",
    "returning_home": "returning", "docking": "returning", "paused": "paused",
    "error": "error", "charging_problem": "error", "locked": "error", "charger_disconnected": "error",
    "emptying_the_bin": "emptying", "washing_the_mop": "washing", "washing_the_mop_2": "washing",
    "going_to_wash_the_mop": "washing", "clean_mop_cleaning": "washing", "segment_clean_mop_cleaning": "washing",
    "air_drying_stopping": "drying", "mapping": "mapping", "updating": "updating",
}
# Newer Q-series (B01) work status -> states.
Q7_STATES = {
    "sleeping": "idle", "waiting_for_orders": "idle", "paused": "paused", "docking": "returning",
    "charging": "charging", "sweep_moping": "cleaning", "sweep_moping_2": "cleaning", "moping": "cleaning",
    "updating": "updating", "mop_cleaning": "washing", "mop_airdrying": "drying", "working_sleep": "paused",
}
BUSY = {"cleaning", "returning", "washing", "emptying", "paused", "mapping"}
# Rated life of V1 consumables in hours (what the Roborock app's percentages are based on).
LIFE_H = {"filter": 150, "main_brush": 300, "side_brush": 200, "sensor": 30}


def v1_state(name: Any) -> str:
    return V1_STATES.get(str(name or "").lower(), "idle")


def q7_state(status: Any) -> str:
    raw = getattr(status, "value", status)
    return Q7_STATES.get(str(raw or "").lower(), "idle")


def pct_left(seconds_left: Any, hours: float) -> int | None:
    try:
        return max(0, min(100, round(float(seconds_left) / (hours * 3600) * 100)))
    except (TypeError, ValueError):
        return None


class RoborockIntegration(Integration):
    type = "roborock"
    name = "Roborock"
    category = "cleaning"
    provides = ("vacuum",)  # kinds of device it can bring in (shown on its catalog card)
    description = ("Roborock robot vacuums: start, pause, send home, find, clean chosen rooms; status, battery, "
                   "errors and consumables. Sign in once with an email code - Newman keeps Roborock's session, "
                   "never a password - then talks to the vacuum on your network whenever it can.")
    fields = [
        Field("email", "Roborock account email", required=True, placeholder="you@example.com"),
        Field("code", "Code from Roborock's email",
              help="Connect with just your email first: Roborock emails you a code. Paste it here and save."),
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._dir = Path(self.ctx.data_dir) / "integrations" / self.id
        self._devices: dict[str, Any] = {}   # entity id -> python-roborock device
        self._last: dict[str, str] = {}       # entity id -> last state (for finished / error events)
        self._slow_at: dict[str, float] = {}  # when consumables / rooms were last read

    @property
    def _session_file(self) -> Path:
        return self._dir / "session.json"

    async def _sign_in(self) -> Any:
        from roborock.data.containers import UserData
        from roborock.web_api import RoborockApiClient
        if self._session_file.exists():
            return UserData.from_dict(json.loads(self._session_file.read_text()))
        api = RoborockApiClient(username=self.config["email"])
        # Roborock ties the emailed code to the client id that asked for it, and the library makes a
        # random one per client. Asking and signing in happen in two runs (saving the code restarts
        # us), so keep the id from the request and sign in with the same one.
        client_id_file = self._dir / "client_id"
        self._dir.mkdir(parents=True, exist_ok=True)
        code = str(self.config.get("code") or "").strip()
        if code and client_id_file.exists():
            api._device_identifier = client_id_file.read_text().strip()
        if not code:
            client_id_file.write_text(api._device_identifier)
            try:
                await api.request_code_v4()
            except Exception:
                await api.request_code()
            self.set_status("pairing", f"Roborock emailed a code to {self.config['email']}. Open this connection's "
                                       "settings, paste the code and save.")
            await asyncio.Event().wait()  # the save restarts us with the code
        try:
            user = await api.code_login_v4(code)
        except Exception:
            user = await api.code_login(code)
        self._session_file.write_text(json.dumps(user.as_dict()))
        self._session_file.chmod(0o600)
        client_id_file.unlink(missing_ok=True)
        cfg = {k: v for k, v in self.config.items() if k != "code"}  # the code is single-use: don't keep it
        self.config = cfg
        if self.save_config:
            self.save_config(cfg)
        return user

    async def run(self) -> None:
        from roborock.devices.device_manager import UserParams, create_device_manager
        from roborock.devices.file_cache import FileCache
        try:
            user = await self._sign_in()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._session_file.unlink(missing_ok=True)
            self.set_status("error", f"Roborock sign-in failed ({e}). Clear the code, save to get a new one.")
            return
        self.set_status("connecting", "Finding your vacuums")
        self._dir.mkdir(parents=True, exist_ok=True)
        manager = await create_device_manager(UserParams(username=self.config["email"], user_data=user),
                                              cache=FileCache(self._dir / "cache.bin"))
        try:
            for d in await manager.get_devices():
                if not (d.v1_properties or d.b01_q7_properties or getattr(d, "b01_q10_properties", None)):
                    continue  # washers, dryers, mowers: not vacuums
                eid = self.entity_id(slug(d.name or d.duid))
                self._devices[eid] = d
                self.hub.upsert(eid, kind="vacuum", name=d.name or "Vacuum", integration=self.id)
            if not self._devices:
                self.set_status("error", "No Roborock vacuums on this account")
                return
            while True:
                for eid, d in self._devices.items():
                    try:
                        await self._refresh(eid, d)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        log.warning("Roborock %s: %s", eid, e)
                        await self.hub.update(eid, available=False)
                local = sum(1 for d in self._devices.values() if getattr(d, "is_local_connected", False))
                n = len(self._devices)
                self.set_status("connected", f"{n} vacuum{'s' if n != 1 else ''} · "
                                + ("on your network" if local == n else f"{local} on your network, the rest via Roborock"))
                busy = any(self._last.get(e) in BUSY for e in self._devices)
                await asyncio.sleep(10 if busy else 60)
        finally:
            await manager.close()

    async def _refresh(self, eid: str, d: Any) -> None:
        now = time.time()
        slow = now - self._slow_at.get(eid, 0) > 600
        attrs: dict[str, Any] = {"model": getattr(getattr(d, "product", None), "model", ""),
                                 "localConnection": bool(getattr(d, "is_local_connected", False))}
        if d.v1_properties:
            p = d.v1_properties
            await p.status.refresh()
            st = p.status
            state = v1_state(getattr(st, "state_name", None))
            attrs.update(battery=st.battery, fanSpeed=getattr(st, "fan_speed_name", None),
                         cleanArea=round(getattr(st, "square_meter_clean_area", 0) or 0, 1),
                         cleanTime=round((st.clean_time or 0) / 60),
                         problem=(getattr(st, "error_code_name", None) or "").replace("_", " ") if st.error_code else "")
            if st.error_code and state not in ("error",):
                state = "error"
            if slow:
                await p.consumables.refresh()
                c = p.consumables
                attrs.update(filterLeft=pct_left(c.filter_time_left, LIFE_H["filter"]),
                             mainBrushLeft=pct_left(c.main_brush_time_left, LIFE_H["main_brush"]),
                             sideBrushLeft=pct_left(c.side_brush_time_left, LIFE_H["side_brush"]),
                             sensorsLeft=pct_left(c.sensor_time_left, LIFE_H["sensor"]))
                try:
                    await p.rooms.refresh()
                    attrs["rooms"] = [{"id": r.segment_id, "name": getattr(r, "name", None) or f"Room {r.segment_id}"}
                                      for r in p.rooms.rooms or []]
                except Exception as e:
                    log.info("Roborock rooms unavailable: %s", e)
                self._slow_at[eid] = now
        else:
            q = d.b01_q7_properties or d.b01_q10_properties
            from roborock.roborock_message import RoborockB01Props as P
            names = ["STATUS", "FAULT", "QUANTITY", "WIND", "CLEANING_AREA", "CLEANING_TIME", "HYPA",
                     "MAIN_BRUSH", "SIDE_BRUSH", "MAIN_SENSOR", "CHARGE_STATE"]
            props = await q.query_values([getattr(P, n) for n in names if hasattr(P, n)])
            state = q7_state(getattr(props, "status", None))
            fault = getattr(props, "fault", None)
            fault_name = getattr(fault, "name", "") if fault else ""
            if fault and fault_name.lower() not in ("", "none", "f_0", "unknown"):
                state = "error"
            attrs.update(battery=getattr(props, "quantity", None), cleanArea=getattr(props, "cleaning_area", None),
                         cleanTime=getattr(props, "cleaning_time", None),
                         fanSpeed=str(getattr(getattr(props, "wind", None), "name", "") or "").replace("_", " ").lower() or None,
                         problem=fault_name.replace("_", " ").lower() if state == "error" else "",
                         filterLeft=getattr(props, "hypa", None), mainBrushLeft=getattr(props, "main_brush", None),
                         sideBrushLeft=getattr(props, "side_brush", None), sensorsLeft=getattr(props, "main_sensor", None))
        prev = self._last.get(eid)
        self._last[eid] = state
        await self.hub.update(eid, available=True, state=state, attrs=attrs)
        if prev in ("cleaning", "returning", "washing") and state in ("docked", "charging", "idle", "emptying", "drying"):
            await self.hub.emit(eid, "finished", {"area": attrs.get("cleanArea"), "minutes": attrs.get("cleanTime")})
        if state == "error" and prev not in (None, "error"):
            await self.hub.emit(eid, "error", {"problem": attrs.get("problem")})

    async def command(self, entity_id: str, command: str, value: Any = None) -> None:
        d = self._devices.get(entity_id)
        if not d:
            raise CommandError("That vacuum isn't connected")
        rooms = [int(x) for x in (value or [])] if command == "clean_rooms" else []
        if command == "clean_rooms" and not rooms:
            raise CommandError("Pick at least one room")
        try:
            if d.v1_properties:
                send = d.v1_properties.command.send
                if command == "clean_rooms":
                    await send("app_segment_clean", [{"segments": rooms, "repeat": 1}])
                else:
                    await send({"start": "app_start", "pause": "app_pause", "dock": "app_charge",
                                "locate": "find_me"}[command])
            else:
                q = d.b01_q7_properties or d.b01_q10_properties
                if command == "clean_rooms":
                    await q.clean_segments(rooms)
                else:
                    await getattr(q, {"start": "start_clean", "pause": "pause_clean", "dock": "return_to_dock",
                                      "locate": "find_me"}[command])()
        except KeyError:
            raise CommandError(f"A vacuum can't {command.replace('_', ' ')}")
        except AttributeError:
            raise CommandError("This vacuum model doesn't support that yet")
        await asyncio.sleep(2)
        await self._refresh(entity_id, d)
